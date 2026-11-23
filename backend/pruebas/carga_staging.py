"""Humo de carga de staging con el perfil del pico mensual (H6; RNF01; documento 13, §19).

H6 pide «prueba de humo en staging con el perfil de pico mensual» (documento 14, §H6). Este
script la hace corta y repetible, por HTTP a través del frontend de staging (Traefik, nginx,
cookies y CSRF reales), con un consorcio sintético nuevo y nunca en producción.

**El perfil** (documento 13, §19.1): en el piloto, hasta 120.000 peticiones por día, con el pico
en los cinco días posteriores a la publicación de expensas a **10 veces el promedio horario**.
Eso da 120.000 / 86.400 × 10 ≈ **13,9 peticiones por segundo** (`TASA_PICO`), la tasa por
defecto. La mezcla es la de una persona que abre la aplicación en esos días: casi todo lecturas
autenticadas (inicio, Mis expensas, comunicados, reparaciones; y, para el Consejo, el panel, la
liquidación oficial y los reclamos) y unas pocas escrituras: pedidos de reparación de los
propietarios.

**Umbrales que dicen los documentos** (y ningún otro): RNF01 (documento 08 y documento 13, §20)
pide que el 95 % de las consultas habituales responda en menos de 1 s «bajo 500 sesiones»; la
señal de §19.3 es la misma (p95 de más de 1 s durante el pico mensual). Para p50, p99 y la tasa
de errores los documentos no fijan un umbral: se informan sin veredicto.

**Sesiones.** Cada persona entra por su invitación (como el smoke): una sesión real por persona,
desde su propia IP de prueba (RFC 5737, `CF-Connecting-IP`), así ni el cupo general de 300 por
minuto por cliente ni el de autenticación de 20 por minuto por IP se comparten entre personas,
igual que con vecinos reales. Cada invitación encola un correo a `.invalid` que `cem-trabajos`
cierra sin enviar (`correo.DOMINIO_RESERVADO`): por eso las personas son `--personas` (100 por
defecto, un edificio grande del piloto) y no las 500 sesiones de RNF01, que dejarían 500 trabajos
en la cola. La tasa de pedidos es la del pico; la cantidad de sesiones de RNF01 queda SIN VERIFICAR
y lo dice el resumen.

1. **Guarda dura**, antes de escribir nada (la misma que el smoke y los ensayos): el
   `CEM_ORIGEN_PUBLICO` del contenedor es de staging (primer nivel terminado en `-staging`), el
   host de `CEM_DATABASE_URL` es `--base`, `--host` es el del origen público, y el consorcio
   `--consorcio` no existe: corre una sola vez por consorcio (código 4 si ya existe).
2. **Escena**: el consorcio por `scripts.alta_consorcio` (CABA) con `--personas` unidades; una
   invitación de soporte por unidad (a `.invalid`) aceptada por HTTP; Modo Consejo por una
   decisión de soporte; `--consejo` mandatos de Consejo; y el Consejo confirma por HTTP los
   vínculos de los demás (como `ensayo_modo_consejo_staging.py`).
3. **Comprobación previa**: cada ruta de la mezcla, una vez por rol, tiene que dar 200. Así una
   ruta que el rol no puede leer no se mide como error y no deja un 403 auditado por segundo.
4. **Carga** de lazo abierto durante `--duracion` segundos: cada `1 / --tasa` segundos sale un
   pedido de una persona al azar con una ruta al azar según los pesos de `MEZCLA_*`, en un hilo de
   `--hilos` con su conexión HTTP persistente (como un navegador). Las `--escrituras` se reparten
   en el tiempo. Se cuenta cada pedido: latencia, código y si salió tarde (el cliente no daba
   abasto: entonces la medición no vale y lo dice).
5. **Resultado**: p50, p95 y p99 por ruta y en total (lecturas y escrituras por separado), tasa
   lograda, errores (todo lo que no sea el código esperado, también 429 y 5xx), y el veredicto
   contra RNF01.
6. **Cierre**, siempre: baja de los mandatos, revocación de todas las sesiones, y la cuenta de
   sesiones abiertas (tiene que dar 0) y de navegadores que siguen entrando (ninguno).

**Cómo se corre** (bash o Git Bash; en PowerShell, `Get-Content -Raw <script> | ssh oci '...'`),
mirando la VM con `ssh oci "docker stats --no-stream"` antes, durante y después:

    ssh oci 'docker exec -i -w /app -e PYTHONPATH=/app <cem-backend-staging> python - \\
        --consorcio <uuid nuevo> --base <host de cem-postgres-staging> \\
        --url https://coolify-proxy --host cem-staging.holancloud.com \\
        --sin-verificar-certificado --simular' < backend/pruebas/carga_staging.py

Después lo mismo sin `--simular` (por defecto: 100 personas, 3 del Consejo, 13,9 pedidos por
segundo durante 180 s, 10 escrituras). No va en la imagen (emite sesiones de prueba).

**Qué deja en staging.** Un consorcio `NOMBRE_CONSORCIO` en Modo Consejo con `--personas`
unidades y personas `@cem-prueba.invalid`, una decisión, mandatos dados de baja, vínculos
confirmados, `--escrituras` reparaciones pedidas, los correos en la cola (a `.invalid`, no salen)
y los eventos de auditoría, todo marcado SINTETICO. Ninguna llamada al modelo. Sale con 0 solo si
la carga corrió entera, el cierre dejó 0 sesiones y nadie entra; el veredicto de RNF01 va en el
resumen (`rnf01`), no en el código de salida.
"""

import argparse
import http.client
import ipaddress
import json
import random
import ssl
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from http.cookies import SimpleCookie
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.engine import make_url

NOMBRE_CONSORCIO = "CEM LAB — Carga de pico mensual (SINTETICO, PRUEBA, NO REAL)"
DOMINIO = "cem-prueba.invalid"
SUFIJO_STAGING = "-staging"
ZONA = "America/Argentina/Buenos_Aires"
JURISDICCION = "caba"
REFERENCIA = "ACTA SINTETICA DE PRUEBA — SIN VALIDEZ JURIDICA — humo de carga en staging"
PETICIONES_DIA_PILOTO = 120_000
"""Documento 13, §19.1: el piloto, de 40.000 a 120.000 peticiones por día (el tope)."""
FACTOR_PICO = 10
"""Documento 13, §19.1: el pico mensual, 10 veces el promedio horario."""
TASA_PICO = round(PETICIONES_DIA_PILOTO / 86_400 * FACTOR_PICO, 1)
"""13,9 pedidos por segundo."""
UMBRAL_P95_S = 1.0
"""RNF01 y §19.3: p95 de menos de 1 s. El único umbral de latencia que fijan los documentos."""
SESIONES_RNF01 = 500
REDES_DE_PRUEBA = ("203.0.113.0/24", "198.51.100.0/24")
"""RFC 5737. De la segunda se saltean las del smoke y los ensayos (…77, …78, …79)."""
IP_RESERVADAS = frozenset({"198.51.100.77", "198.51.100.78", "198.51.100.79"})
MEZCLA_PROPIETARIO = {
    "/api/v1/auth/sesion": 3,
    "/api/v1/me/consorcios": 3,
    "/api/v1/me/invitaciones": 1,
    "{c}/resumen": 3,
    "{c}/mi-cuenta": 4,
    "{c}/comunicados": 2,
    "{c}/reparaciones": 1,
}
"""Pesos de lo que abre un propietario: el inicio (sesión, sus edificios, sus invitaciones y el
resumen), Mis expensas y los comunicados; de vez en cuando, sus reparaciones."""
MEZCLA_CONSEJO = MEZCLA_PROPIETARIO | {
    "{c}/panel": 3,
    "{c}/liquidacion-oficial": 2,
    "{c}/reclamos-liquidacion": 1,
    "{c}/liquidaciones-importadas": 1,
}
"""El Consejo, además: el panel, la liquidación oficial, los reclamos y las importadas."""
ESPERA_MAXIMA_CUPO_S = 60
TOPE_PEDIDO_S = 30
TARDE_S = 0.5
"""Un pedido que sale más de esto después de su turno salió tarde: el cliente no daba abasto."""
PROPORCION_TARDE_MAXIMA = 0.01
"""Si más del 1 % salió tarde, la tasa no fue la pedida y la medición no vale."""


class Negado(Exception):
    """La guarda dura: el entorno no es staging, o el consorcio ya existe."""


class Fallo(Exception):
    """Un paso no dio lo esperado."""


def comprobar(condicion: bool, que: str) -> None:
    if not condicion:
        raise Fallo(que)


# Funciones puras -----------------------------------------------------------------------------


def ips_de_prueba(cantidad: int) -> list[str]:
    """Una IP de documentación distinta por persona (sin las reservadas)."""
    ips = [str(ip) for red in REDES_DE_PRUEBA for ip in ipaddress.ip_network(red).hosts()
           if str(ip) not in IP_RESERVADAS]  # fmt: skip
    if cantidad > len(ips):
        raise ValueError(f"hay {len(ips)} IP de prueba y se pidieron {cantidad}")
    return ips[:cantidad]


def percentil(valores: list[float], p: float) -> float | None:
    """Percentil por el rango más cercano (el valor observado en la posición ⌈p·n⌉)."""
    if not valores:
        return None
    ordenados = sorted(valores)
    k = max(1, -(-len(ordenados) * p // 100))
    return ordenados[int(k) - 1]


def plan_de_pedidos(duracion: float, tasa: float, escrituras: int) -> list[tuple[float, bool]]:
    """(segundo desde el inicio, es_escritura): un pedido cada `1 / tasa` segundos, y las
    escrituras repartidas parejas entre ellos (reemplazan a la lectura de su turno)."""
    total = int(duracion * tasa)
    if escrituras > total:
        raise ValueError("más escrituras que pedidos")
    turnos = (
        {int((i + 0.5) * total / escrituras) for i in range(escrituras)} if escrituras else set()
    )
    return [(i / tasa, i in turnos) for i in range(total)]


def elegir(mezcla: dict[str, int], azar: random.Random) -> str:
    rutas = list(mezcla)
    return azar.choices(rutas, weights=[mezcla[r] for r in rutas])[0]


def resumir(muestras: list["Muestra"]) -> dict:
    """p50/p95/p99 en milisegundos, cantidad y errores, por ruta y en total."""

    def bloque(grupo: list[Muestra]) -> dict:
        lat = [m.segundos for m in grupo]
        return {
            "pedidos": len(grupo),
            "errores": sum(1 for m in grupo if not m.bien),
            "p50_ms": _ms(percentil(lat, 50)),
            "p95_ms": _ms(percentil(lat, 95)),
            "p99_ms": _ms(percentil(lat, 99)),
            "max_ms": _ms(max(lat, default=None)),
        }

    por_ruta = defaultdict(list)
    for m in muestras:
        por_ruta[m.ruta].append(m)
    lecturas = [m for m in muestras if not m.escritura]
    escrituras = [m for m in muestras if m.escritura]
    codigos = defaultdict(int)
    for m in muestras:
        codigos[str(m.estado)] += 1
    return {
        "total": bloque(muestras),
        "lecturas": bloque(lecturas),
        "escrituras": bloque(escrituras),
        "por_ruta": {r: bloque(g) for r, g in sorted(por_ruta.items())},
        "codigos": dict(sorted(codigos.items())),
        "tarde": sum(1 for m in muestras if m.tarde),
    }


def _ms(segundos: float | None) -> float | None:
    return None if segundos is None else round(segundos * 1000, 1)


def veredicto_rnf01(resumen: dict, personas: int) -> dict:
    p95 = resumen["lecturas"]["p95_ms"]
    return {
        "umbral": f"p95 de las consultas < {UMBRAL_P95_S * 1000:.0f} ms (RNF01, §19.3)",
        "p95_lecturas_ms": p95,
        "cumple_latencia": p95 is not None and p95 < UMBRAL_P95_S * 1000,
        "sesiones": personas,
        "sesiones_rnf01": SESIONES_RNF01,
        "sesiones_verificadas": personas >= SESIONES_RNF01,
        "sin_umbral_en_los_documentos": ["p50", "p99", "tasa de errores"],
    }


def exigir_staging(origen_publico: str, database_url: str, base: str, host: str) -> None:
    host_publico = urlsplit(origen_publico or "").hostname or ""
    if not host_publico.split(".")[0].endswith(SUFIJO_STAGING):
        raise Negado(f"CEM_ORIGEN_PUBLICO no es de staging: {host_publico!r}")
    if make_url(database_url).host != base:
        raise Negado(f"CEM_DATABASE_URL no apunta a --base {base!r}")
    if host != host_publico:
        raise Negado(f"--host {host!r} no es el de CEM_ORIGEN_PUBLICO ({host_publico!r})")


# HTTP ----------------------------------------------------------------------------------------


@dataclass
class Persona:
    rol: str
    ip: str
    correo: str
    id: UUID | None = None
    unidad: UUID | None = None
    cookies: dict[str, str] = field(default_factory=dict)


@dataclass
class Muestra:
    ruta: str
    estado: int
    segundos: float
    bien: bool
    escritura: bool
    tarde: bool


class Cliente:
    """Pedidos con las cookies y la IP de cada persona; una conexión persistente por hilo."""

    def __init__(self, url: str, host: str, contexto: ssl.SSLContext | None) -> None:
        partes = urlsplit(url)
        self.https = partes.scheme == "https"
        self.destino = partes.hostname
        self.puerto = partes.port or (443 if self.https else 80)
        self.host = host
        self.contexto = contexto
        self.local = threading.local()

    def _conexion(self) -> http.client.HTTPConnection:
        conexion = getattr(self.local, "conexion", None)
        if conexion is None:
            if self.https:
                conexion = http.client.HTTPSConnection(self.destino, self.puerto,
                                                       timeout=TOPE_PEDIDO_S,
                                                       context=self.contexto)  # fmt: skip
            else:
                conexion = http.client.HTTPConnection(self.destino, self.puerto,
                                                      timeout=TOPE_PEDIDO_S)  # fmt: skip
            self.local.conexion = conexion
        return conexion

    def pedir(self, persona: Persona, metodo: str, ruta: str, cuerpo: object = None, *,
              acto: bool = False) -> tuple[int, dict, object]:  # fmt: skip
        from app.identidad import sesiones
        from app.plataforma.idempotencia import ENCABEZADO

        encabezados = {"Host": self.host, "Accept": "application/json",
                       "CF-Connecting-IP": persona.ip, "Connection": "keep-alive"}  # fmt: skip
        datos = None
        if cuerpo is not None:
            datos = json.dumps(cuerpo).encode()
            encabezados["Content-Type"] = "application/json"
        if persona.cookies:
            encabezados["Cookie"] = "; ".join(f"{k}={v}" for k, v in persona.cookies.items())
        if acto:
            encabezados[sesiones.ENCABEZADO_CSRF] = persona.cookies[sesiones.COOKIE_CSRF]
            encabezados[ENCABEZADO] = str(uuid4())
        for intento in (1, 2):
            conexion = self._conexion()
            try:
                conexion.request(metodo, ruta, body=datos, headers=encabezados)
                r = conexion.getresponse()
                texto = r.read()
                break
            except (http.client.HTTPException, OSError):
                conexion.close()
                self.local.conexion = None
                if intento == 2:
                    raise
        for linea in r.headers.get_all("Set-Cookie") or []:
            galleta = SimpleCookie()
            galleta.load(linea)
            for nombre, morsel in galleta.items():
                if morsel["max-age"] == "0":
                    persona.cookies.pop(nombre, None)
                else:
                    persona.cookies[nombre] = morsel.value
        cuerpo_json = None
        if texto and r.headers.get_content_type() == "application/json":
            cuerpo_json = json.loads(texto)
        return r.status, {k.lower(): v for k, v in r.headers.items()}, cuerpo_json


# Escena --------------------------------------------------------------------------------------


@dataclass
class Corrida:
    motor: object
    consorcio: UUID
    origen_publico: str
    cliente: Cliente
    personas: list[Persona] = field(default_factory=list)
    mandatos: list[UUID] = field(default_factory=list)
    id: str = field(default_factory=lambda: uuid4().hex[:8])
    controles: list[str] = field(default_factory=list)
    hoy: object = None

    def ok(self, que: str) -> None:
        self.controles.append(que)
        print(f"ok  {que}", flush=True)

    def tx(self):
        from app.db import transaccion

        return transaccion(self.motor, persona_id=None, consorcio_id=self.consorcio)

    def c(self, resto: str = "") -> str:
        return f"/api/v1/consorcios/{self.consorcio}{resto}"

    def ruta(self, plantilla: str) -> str:
        return plantilla.replace("{c}", self.c())

    def consejo(self) -> list[Persona]:
        return [p for p in self.personas if p.rol == "consejo"]

    def pedir(self, persona: Persona, metodo: str, ruta: str, cuerpo: object = None, *,
              acto: bool = False):  # fmt: skip
        """Un pedido de la escena: respeta el `Retry-After` de un 429 (como el ensayo)."""
        estado, encabezados, cuerpo_json = self.cliente.pedir(persona, metodo, ruta, cuerpo,
                                                              acto=acto)  # fmt: skip
        if estado == 429:
            espera = int(encabezados.get("retry-after", "0"))
            if 0 < espera <= ESPERA_MAXIMA_CUPO_S:
                print(f"cupo agotado: espero {espera} s", flush=True)
                time.sleep(espera)
                return self.pedir(persona, metodo, ruta, cuerpo, acto=acto)
        return estado, cuerpo_json


def preparar(corrida: Corrida, cantidad: int, consejo: int) -> None:
    from cem_contracts.estados import TipoVinculo
    from cem_contracts.invitaciones import InvitacionNueva

    from app.identidad import invitaciones
    from scripts import alta_consorcio

    designaciones = [f"U-{i:03d}" for i in range(1, cantidad + 1)]
    archivo = alta_consorcio.Archivo.model_validate({
        "consorcio": {"id": str(corrida.consorcio), "nombre": NOMBRE_CONSORCIO,
                      "zona_horaria": ZONA, "jurisdiccion": JURISDICCION},
        "unidades": [{"designacion": d} for d in designaciones],
    })  # fmt: skip
    resultado = alta_consorcio.dar_de_alta(corrida.motor, archivo, simular=False)
    if resultado.estado != "creado":
        raise Negado(f"el consorcio {corrida.consorcio} ya existe: corre una sola vez")
    for i, (d, ip) in enumerate(zip(designaciones, ips_de_prueba(cantidad), strict=True)):
        p = Persona(
            "consejo" if i < consejo else "propietario",
            ip,
            f"carga-{corrida.id}-{i:03d}@{DOMINIO}",
            unidad=resultado.unidades[d],
        )
        with corrida.tx() as conn:
            creada = invitaciones.crear(
                conn, consorcio_id=corrida.consorcio,
                nueva=InvitacionNueva(unidad_id=p.unidad, tipo=TipoVinculo.PROPIETARIO,
                                      correo=p.correo, nombre=f"LAB carga {corrida.id} {i:03d}"
                                      " SINTETICO"),
                invitada_por=None, origen_publico=corrida.origen_publico, origen="soporte",
            )  # fmt: skip
        estado, cuerpo = corrida.pedir(p, "POST", "/api/v1/auth/invitacion/aceptar",
                                       {"token": creada.token})  # fmt: skip
        comprobar(estado == 200 and cuerpo["cuenta_nueva"] is True,
                  f"aceptar la invitación {i}: {estado} {cuerpo}")  # fmt: skip
        p.id = UUID(cuerpo["persona_id"])
        corrida.personas.append(p)
    with corrida.tx() as conn:
        corrida.hoy = conn.execute(text("select nucleo.hoy_del_consorcio(:c)"),
                                   {"c": corrida.consorcio}).scalar_one()  # fmt: skip
        decision = conn.execute(
            text("select nucleo.registrar_decision('asamblea', :h, :d, :r, null, 'soporte')"),
            {"h": corrida.hoy, "d": "SINTETICA: Modo Consejo para el humo de carga",
             "r": REFERENCIA},
        ).scalar_one()  # fmt: skip
        conn.execute(text("select nucleo.cambiar_modo('consejo', :d, :h, null, 'soporte')"),
                     {"d": decision, "h": corrida.hoy})  # fmt: skip
        for p in corrida.consejo():
            corrida.mandatos.append(conn.execute(
                text("select nucleo.alta_mandato('miembro_consejo', :p, null, null, null, :d,"
                     " :h, null, null, null, 'soporte')"),
                {"p": p.id, "d": decision, "h": corrida.hoy},
            ).scalar_one())  # fmt: skip
        vinculos = dict(conn.execute(
            text("select persona_id, id from nucleo.vinculo where consorcio_id = :c"),
            {"c": corrida.consorcio},
        ).all())  # fmt: skip
    primero, segundo = corrida.consejo()[0], corrida.consejo()[-1]
    for p in corrida.personas:
        quien = segundo if p is primero else primero
        estado, cuerpo = corrida.pedir(quien, "POST", corrida.c(f"/vinculos/{vinculos[p.id]}"
                                       "/confirmar"), {"referencia": "Escritura SINTETICA (NO"
                                       " REAL)"}, acto=True)  # fmt: skip
        comprobar(estado == 200 and cuerpo["confirmado"] is True,
                  f"confirmar el vínculo de {p.correo}: {estado} {cuerpo}")  # fmt: skip
    corrida.ok(f"consorcio en Modo Consejo con {cantidad} unidades; {cantidad} personas entran por"
               f" su invitación, cada una desde su IP de prueba; {consejo} del Consejo; vínculos"
               " confirmados por HTTP")  # fmt: skip


def comprobar_rutas(corrida: Corrida) -> None:
    """Cada ruta de la mezcla da 200 a su rol antes de medir: si no, la carga no empieza."""
    propietario = next(p for p in corrida.personas if p.rol == "propietario")
    for persona, mezcla in ((propietario, MEZCLA_PROPIETARIO),
                            (corrida.consejo()[0], MEZCLA_CONSEJO)):  # fmt: skip
        for plantilla in mezcla:
            estado, cuerpo = corrida.pedir(persona, "GET", corrida.ruta(plantilla))
            comprobar(estado == 200, f"{persona.rol} {plantilla}: {estado} {cuerpo}")
    corrida.ok(f"las {len(MEZCLA_PROPIETARIO)} rutas del propietario y las {len(MEZCLA_CONSEJO)}"
               " del Consejo dan 200 antes de medir")  # fmt: skip


# Carga ---------------------------------------------------------------------------------------


def cargar(corrida: Corrida, duracion: float, tasa: float, escrituras: int,
           hilos: int, semilla: int) -> tuple[list[Muestra], float]:  # fmt: skip
    azar = random.Random(semilla)
    plan = plan_de_pedidos(duracion, tasa, escrituras)
    propietarios = [p for p in corrida.personas if p.rol == "propietario"]
    muestras: list[Muestra] = []
    candado = threading.Lock()
    textos = iter(range(1, escrituras + 1))

    def uno(turno: float, inicio: float, persona: Persona, plantilla: str, escribe: bool) -> None:
        tarde = time.monotonic() - (inicio + turno) > TARDE_S
        if escribe:
            n = next(textos)
            cuerpo = {
                "descripcion": f"SINTETICO carga {corrida.id}-{n}: pierde agua la canilla"
                " del palier",
                "unidad_id": str(persona.unidad),
            }
            ruta, metodo, esperado = corrida.c("/reparaciones"), "POST", 201
        else:
            cuerpo, ruta, metodo, esperado = None, corrida.ruta(plantilla), "GET", 200
        t0 = time.perf_counter()
        try:
            estado, _, _ = corrida.cliente.pedir(persona, metodo, ruta, cuerpo, acto=escribe)
        except (http.client.HTTPException, OSError):
            estado = 0
        segundos = time.perf_counter() - t0
        with candado:
            muestras.append(Muestra("POST /reparaciones" if escribe else plantilla, estado,
                                    segundos, estado == esperado, escribe, tarde))  # fmt: skip

    with ThreadPoolExecutor(max_workers=hilos) as grupo:
        inicio = time.monotonic()
        for turno, escribe in plan:
            if escribe:
                persona, plantilla = azar.choice(propietarios), ""
            else:
                persona = azar.choice(corrida.personas)
                plantilla = elegir(MEZCLA_CONSEJO if persona.rol == "consejo"
                                   else MEZCLA_PROPIETARIO, azar)  # fmt: skip
            espera = inicio + turno - time.monotonic()
            if espera > 0:
                time.sleep(espera)
            grupo.submit(uno, turno, inicio, persona, plantilla, escribe)
    return muestras, time.monotonic() - inicio


def cerrar(corrida: Corrida) -> tuple[int, int]:
    """Baja de los mandatos y revocación de las sesiones; cuenta las abiertas persona por persona
    (la RLS de `nucleo.sesion` solo deja ver las propias) y cuántas personas siguen entrando."""
    from app.db import transaccion

    with corrida.tx() as conn:
        for m in corrida.mandatos:
            conn.execute(
                text("select nucleo.baja_mandato(:m, 'renuncia', null, :r, 'Fin del humo de"
                     " carga', null, 'soporte')"), {"m": m, "r": REFERENCIA},
            )  # fmt: skip
        for p in corrida.personas:
            conn.execute(text("select nucleo.revocar_sesiones_de(:p)"), {"p": p.id})
    abiertas = 0
    for p in corrida.personas:
        with transaccion(corrida.motor, persona_id=p.id) as conn:
            abiertas += conn.execute(
                text("select count(*) from nucleo.sesion where persona_id = :p"
                     " and revocada_en is null and vence_en > now()"), {"p": p.id},
            ).scalar_one()  # fmt: skip
    entran = sum(
        1
        for p in corrida.personas
        if corrida.cliente.pedir(p, "GET", "/api/v1/me/consorcios")[0] != 401
    )
    return abiertas, entran


def main(argumentos: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--consorcio", type=UUID, required=True, help="uuid nuevo")
    p.add_argument("--base", required=True, help="host de cem-postgres-staging")
    p.add_argument("--url", required=True, help="adónde mandar los pedidos HTTP")
    p.add_argument("--host", required=True, help="encabezado Host del frontend de staging")
    p.add_argument("--sin-verificar-certificado", action="store_true")
    p.add_argument("--personas", type=int, default=100)
    p.add_argument("--consejo", type=int, default=3)
    p.add_argument("--tasa", type=float, default=TASA_PICO, help="pedidos por segundo")
    p.add_argument("--duracion", type=float, default=180, help="segundos de carga")
    p.add_argument("--escrituras", type=int, default=10)
    p.add_argument("--hilos", type=int, default=64)
    p.add_argument("--semilla", type=int, default=20261123)
    p.add_argument("--simular", action="store_true", help="solo la guarda y el plan")
    a = p.parse_args(argumentos)
    if not 2 <= a.consejo < a.personas:
        p.error("--consejo tiene que ser al menos 2 y menos que --personas")
    from app.config import cargar as cargar_config
    from app.db import crear_motor

    config = cargar_config()
    try:
        exigir_staging(config.origen_publico, config.database_url, a.base, a.host)
        ips_de_prueba(a.personas)
        plan = plan_de_pedidos(a.duracion, a.tasa, a.escrituras)
    except (Negado, ValueError) as e:
        print(f"negado: {e}", file=sys.stderr)
        return 3
    if a.simular:
        print(json.dumps({"simulacion": True, "consorcio": str(a.consorcio),
                          "pedidos": len(plan), "escrituras": a.escrituras, "tasa": a.tasa,
                          "personas": a.personas}, indent=2))  # fmt: skip
        return 0
    contexto = None
    if a.sin_verificar_certificado:
        contexto = ssl.create_default_context()
        contexto.check_hostname = False
        contexto.verify_mode = ssl.CERT_NONE
    motor = crear_motor(config.database_url)
    corrida = Corrida(motor, a.consorcio, config.origen_publico,
                      Cliente(a.url.rstrip("/"), a.host, contexto))  # fmt: skip
    try:
        with corrida.tx() as conn:
            if conn.execute(text("select count(*) from nucleo.consorcio where id = :c"),
                            {"c": a.consorcio}).scalar_one():  # fmt: skip
                print(f"negado: el consorcio {a.consorcio} ya existe", file=sys.stderr)
                return 4
        print(f"corrida {corrida.id}; consorcio {a.consorcio}", flush=True)
        error, resultado = None, None
        try:
            t0 = time.monotonic()
            preparar(corrida, a.personas, a.consejo)
            comprobar_rutas(corrida)
            escena_s = time.monotonic() - t0
            print(f"carga: {len(plan)} pedidos en {a.duracion:.0f} s ({a.tasa}/s)", flush=True)
            inicio_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            muestras, transcurrido = cargar(corrida, a.duracion, a.tasa, a.escrituras, a.hilos,
                                            a.semilla)  # fmt: skip
            fin_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            resumen = resumir(muestras)
            valida = resumen["tarde"] <= PROPORCION_TARDE_MAXIMA * len(muestras)
            resultado = {"inicio_utc": inicio_utc, "fin_utc": fin_utc,
                         "escena_s": round(escena_s, 1), "segundos": round(transcurrido, 1),
                         "tasa_lograda": round(len(muestras) / transcurrido, 2),
                         "medicion_valida": valida, **resumen,
                         "rnf01": veredicto_rnf01(resumen, a.personas)}  # fmt: skip
            comprobar(len(muestras) == len(plan), f"{len(muestras)} de {len(plan)} pedidos")
            comprobar(valida, f"{resumen['tarde']} pedidos salieron tarde: el cliente no dio"
                      " abasto y la tasa no fue la pedida")  # fmt: skip
            corrida.ok(f"{len(muestras)} pedidos en {transcurrido:.0f} s; p95 de las lecturas"
                       f" {resumen['lecturas']['p95_ms']} ms; errores"
                       f" {resumen['total']['errores']}")  # fmt: skip
        except Fallo as e:
            error = e
            print(f"FALLÓ: {e}", file=sys.stderr, flush=True)
        finally:
            abiertas, entran = cerrar(corrida) if corrida.personas else (0, 0)
            print(json.dumps({
                "corrida": corrida.id, "consorcio": str(a.consorcio),
                "parametros": {k: v for k, v in vars(a).items() if k not in ("base",)},
                "resultado": resultado, "controles": corrida.controles,
                "sesiones_abiertas_al_cerrar": abiertas, "personas_que_siguen_entrando": entran,
            }, ensure_ascii=False, indent=2, default=str))  # fmt: skip
        if error is not None or abiertas or entran:
            if error is None:
                print(f"FALLÓ: quedaron {abiertas} sesiones abiertas; siguen entrando {entran}",
                      file=sys.stderr)  # fmt: skip
            return 1
        return 0
    finally:
        motor.dispose()


if __name__ == "__main__":
    sys.exit(main())
