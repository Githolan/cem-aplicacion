"""Smoke autenticado de staging (H2b 2b-4, decisión P3 del propietario; diseño §12.4).

Recorre por HTTP, contra el frontend de staging (nginx, cookies y CSRF reales), lo que las
pruebas de la PC cubren por equivalencia y no por ejecución: un vínculo que entra, el padrón
con su 403 auditado, confirmar un vínculo, crear, listar y revocar una invitación, un mandato
que da ámbito, la invitación de mandato con cuatro ojos (A7) y desasociar. Al final comprueba la
cadena de auditoría del consorcio y revoca todas las sesiones de prueba.

**No va en la imagen de la aplicación** (emite sesiones de prueba: en producción sería una
herramienta para entrar como cualquiera). Se copia a `cem-backend-staging` solo para correr:

    docker cp backend/pruebas/smoke_staging.py <contenedor>:/tmp/smoke_staging.py
    docker exec -w /app -e PYTHONPATH=/app <contenedor> python /tmp/smoke_staging.py \\
        --consorcio <uuid> --base <host de cem-postgres-staging> \\
        --url https://coolify-proxy --host cem-staging.holancloud.com --sin-verificar-certificado

**Guarda dura**, antes de escribir nada:
- el `CEM_ORIGEN_PUBLICO` del contenedor tiene que ser el de staging (su primer nivel termina en
  `-staging`, como todo nombre de staging);
- el host de `CEM_DATABASE_URL` tiene que ser `--base` (el de `cem-postgres-staging` en
  `doc/infra/REGISTRO_RECURSOS.md`);
- el consorcio `--consorcio`, si existe, tiene que llamarse exactamente `NOMBRE_CONSORCIO`; si no
  existe, se crea con ese nombre. Es el único consorcio que el script toca: todo corre con él
  en el ámbito de la RLS.

**Datos:** cada corrida crea cuatro unidades y sus personas con correos `@cem-prueba.invalid`
(RFC 6761: nunca resuelven; `cem-trabajos` no intenta enviarles, `correo.DOMINIO_RESERVADO`),
mandatos de prueba por la vía de soporte que termina al final (con baja del mismo día, rango
vacío: no ocupa la administración de corridas siguientes) y deja eventos de auditoría, que son
de solo agregar. La primera invitación de mandato deja staging sin poder bajar de `0015`
(barrera aceptada en P3): se registra en `REGISTRO_RECURSOS`. Nunca en producción.

`--sin-verificar-certificado` existe porque dentro de la red de Docker el certificado de
Traefik no es el del dominio (el TLS público lo termina Cloudflare); el tráfico no sale de la
VM. Sale con código 0 solo si todo dio lo esperado; ante el primer desvío falla con el detalle,
y aun así revoca las sesiones y da de baja los mandatos que haya creado.
"""

import argparse
import json
import re
import ssl
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from http.cookies import SimpleCookie
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from cem_contracts.estados import TipoVinculo
from cem_contracts.invitaciones import InvitacionNueva
from sqlalchemy import Connection, Engine, text
from sqlalchemy.engine import make_url

from app import auditoria
from app.config import cargar
from app.db import crear_motor, transaccion
from app.identidad import invitaciones, sesiones
from app.plataforma.idempotencia import ENCABEZADO, ENCABEZADO_REPETIDA

NOMBRE_CONSORCIO = "CEM — prueba automatizada (staging)"
DOMINIO = "cem-prueba.invalid"
SUFIJO_STAGING = "-staging"
ZONA = "America/Argentina/Buenos_Aires"
SOPORTE = "soporte"
REFERENCIA = "Prueba automatizada de staging (P3), sin valor documental"
CUIT_SINTETICO = "30000000007"
"""Válido (dígito verificador) y sin dueño: solo para el mandato externo de la prueba."""
IP_DE_PRUEBA = "198.51.100.77"
"""RFC 5737 (documentación): el límite de tasa de la prueba no se mezcla con el de nadie."""
TOKEN_MANDATO = re.compile(r"/invitacion-mandato#token=([A-Za-z0-9_-]{43})")


class Negado(Exception):
    """La guarda dura: el entorno no es staging o el consorcio no es el de la prueba."""


class Fallo(Exception):
    """Un paso no dio lo esperado."""


def comprobar(condicion: bool, que: str) -> None:
    if not condicion:
        raise Fallo(que)


# Guarda --------------------------------------------------------------------------------------


def exigir_staging(origen_publico: str, database_url: str, base: str) -> None:
    host_publico = urlsplit(origen_publico).hostname or ""
    if not host_publico.split(".")[0].endswith(SUFIJO_STAGING):
        raise Negado(f"CEM_ORIGEN_PUBLICO no es de staging: {host_publico!r}")
    host_base = make_url(database_url).host
    if host_base != base:
        raise Negado(f"CEM_DATABASE_URL apunta a {host_base!r}, no a --base {base!r}")


def asegurar_consorcio(conn: Connection, consorcio: UUID) -> bool:
    """True si lo creó. Si existe con otro nombre, se niega: no es el de la prueba."""
    nombre = conn.execute(
        text("select nombre from nucleo.consorcio where id = :c"), {"c": consorcio}
    ).scalar_one_or_none()
    if nombre is not None:
        if nombre != NOMBRE_CONSORCIO:
            raise Negado(f"el consorcio {consorcio} se llama {nombre!r}: no es el de la prueba")
        return False
    conn.execute(
        text("insert into nucleo.consorcio (id, nombre, zona_horaria) values (:c, :n, :z)"),
        {"c": consorcio, "n": NOMBRE_CONSORCIO, "z": ZONA},
    )
    auditoria.registrar(conn, consorcio_id=consorcio, actor=None, origen=SOPORTE,
                        accion="consorcios.consorcio.crear", entidad="consorcio",
                        entidad_id=consorcio,
                        despues={"nombre": NOMBRE_CONSORCIO, "zona_horaria": ZONA})  # fmt: skip
    return True


# HTTP ----------------------------------------------------------------------------------------


@dataclass
class Respuesta:
    estado: int
    encabezados: dict[str, str]
    cuerpo: object
    texto: str


@dataclass
class Navegador:
    """Un navegador con sus cookies, contra el frontend (el `Host` lo elige nginx/Traefik)."""

    url: str
    host: str
    contexto: ssl.SSLContext | None
    cookies: dict[str, str] = field(default_factory=dict)

    def pedir(self, metodo: str, ruta: str, cuerpo: object = None, *, csrf: bool = False,
              clave: str | None = None) -> Respuesta:  # fmt: skip
        encabezados = {"Host": self.host, "Accept": "application/json",
                       "CF-Connecting-IP": IP_DE_PRUEBA}  # fmt: skip
        datos = None
        if cuerpo is not None:
            datos = json.dumps(cuerpo).encode()
            encabezados["Content-Type"] = "application/json"
        if self.cookies:
            encabezados["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        if csrf:
            encabezados[sesiones.ENCABEZADO_CSRF] = self.cookies[sesiones.COOKIE_CSRF]
        if clave is not None:
            encabezados[ENCABEZADO] = clave
        pedido = urllib.request.Request(self.url + ruta, data=datos, headers=encabezados,
                                        method=metodo)  # fmt: skip
        try:
            r = urllib.request.urlopen(pedido, context=self.contexto, timeout=30)
        except urllib.error.HTTPError as e:
            r = e
        for linea in r.headers.get_all("Set-Cookie") or []:
            galleta = SimpleCookie()
            galleta.load(linea)
            for nombre, morsel in galleta.items():
                if morsel["max-age"] == "0":
                    self.cookies.pop(nombre, None)
                else:
                    self.cookies[nombre] = morsel.value
        texto = r.read().decode()
        cuerpo_json = json.loads(texto) if texto and r.headers.get_content_type() == (
            "application/json") else None  # fmt: skip
        return Respuesta(r.status, {k.lower(): v for k, v in r.headers.items()}, cuerpo_json,
                         texto)  # fmt: skip


# Recorrido -----------------------------------------------------------------------------------


@dataclass
class Corrida:
    motor: Engine
    consorcio: UUID
    origen_publico: str
    url: str
    host: str
    contexto: ssl.SSLContext | None
    id: str = field(default_factory=lambda: uuid4().hex[:8])
    personas: dict[str, UUID] = field(default_factory=dict)
    mandatos: dict[str, UUID] = field(default_factory=dict)
    terminados: set[str] = field(default_factory=set)
    controles: list[str] = field(default_factory=list)
    navegadores: dict[str, Navegador] = field(default_factory=dict)

    def ok(self, que: str) -> None:
        self.controles.append(que)
        print(f"ok  {que}", flush=True)

    def navegador(self) -> Navegador:
        return Navegador(self.url, self.host, self.contexto)

    def tx(self):
        return transaccion(self.motor, persona_id=None, consorcio_id=self.consorcio)

    def correo(self, rol: str) -> str:
        return f"smoke-{self.id}-{rol}@{DOMINIO}"

    def c(self, resto: str = "") -> str:
        return f"/api/v1/consorcios/{self.consorcio}{resto}"


def unidades(corrida: Corrida) -> dict[str, UUID]:
    salida = {}
    with corrida.tx() as conn:
        for letra in "ABCD":
            designacion = f"Prueba {corrida.id} {letra}"
            u = conn.execute(
                text("insert into nucleo.unidad (consorcio_id, designacion) values (:c, :d)"
                     " returning id"), {"c": corrida.consorcio, "d": designacion},
            ).scalar_one()  # fmt: skip
            auditoria.registrar(conn, consorcio_id=corrida.consorcio, actor=None, origen=SOPORTE,
                                accion="consorcios.unidad.crear", entidad="unidad",
                                entidad_id=u, despues={"designacion": designacion})  # fmt: skip
            salida[letra] = u
    return salida


def entra_por_invitacion(corrida: Corrida, rol: str, unidad: UUID) -> Navegador:
    """Invitación de soporte (sin correo: `.invalid`) y aceptación por HTTP: cuenta nueva."""
    with corrida.tx() as conn:
        creada = invitaciones.crear(
            conn, consorcio_id=corrida.consorcio,
            nueva=InvitacionNueva(unidad_id=unidad, tipo=TipoVinculo.PROPIETARIO,
                                  correo=corrida.correo(rol), nombre=f"Prueba {rol}"),
            invitada_por=None, origen_publico=corrida.origen_publico, origen=SOPORTE,
        )  # fmt: skip
    comprobar(creada is not None, f"invitación de soporte para {rol}")
    n = corrida.navegador()
    r = n.pedir("POST", "/api/v1/auth/invitacion/aceptar", {"token": creada.token})
    comprobar(r.estado == 200 and r.cuerpo["cuenta_nueva"] is True, f"aceptar {rol}: {r.texto}")
    comprobar(sesiones.COOKIE_SESION in n.cookies and sesiones.COOKIE_CSRF in n.cookies,
              f"aceptar {rol} deja la sesión y el CSRF")  # fmt: skip
    corrida.personas[rol] = UUID(r.cuerpo["persona_id"])
    corrida.navegadores[rol] = n
    return n


def alta(corrida: Corrida, rol: str, tipo: str, decision: UUID, hoy, persona=None,
         nombre=None, cuit=None) -> None:  # fmt: skip
    with corrida.tx() as conn:
        corrida.mandatos[rol] = conn.execute(
            text("select nucleo.alta_mandato(:t, :p, :n, :cuit, null, :d, :h, null, null, null,"
                 " 'soporte')"),
            {"t": tipo, "p": persona, "n": nombre, "cuit": cuit, "d": decision, "h": hoy},
        ).scalar_one()  # fmt: skip


def baja(corrida: Corrida, rol: str) -> None:
    with corrida.tx() as conn:
        conn.execute(
            text("select nucleo.baja_mandato(:m, 'renuncia', null, :r, 'Fin de la prueba"
                 " automatizada', null, 'soporte')"),
            {"m": corrida.mandatos[rol], "r": REFERENCIA},
        )  # fmt: skip
    corrida.terminados.add(rol)


def denegado(corrida: Corrida, persona: UUID, accion: str) -> bool:
    with corrida.tx() as conn:
        return conn.execute(
            text("select exists (select 1 from auditoria.eventos where consorcio_id = :c"
                 " and actor_persona_id = :p and accion = 'autorizacion.denegada'"
                 " and despues->>'accion' = :a)"),
            {"c": corrida.consorcio, "p": persona, "a": accion},
        ).scalar_one()  # fmt: skip


def recorrer(corrida: Corrida) -> None:
    u = unidades(corrida)
    prop = entra_por_invitacion(corrida, "propietaria", u["A"])
    mc1 = entra_por_invitacion(corrida, "consejo1", u["B"])
    mc2 = entra_por_invitacion(corrida, "consejo2", u["C"])
    adm = entra_por_invitacion(corrida, "administracion", u["D"])
    corrida.ok("cuatro vínculos entran por invitación, con cuenta nueva y sesión")

    with corrida.tx() as conn:
        hoy = conn.execute(
            text("select nucleo.hoy_del_consorcio(:c)"), {"c": corrida.consorcio}
        ).scalar_one()
        decision = conn.execute(
            text("select nucleo.registrar_decision('asamblea', :h, :d, :r, null, 'soporte')"),
            {"h": hoy, "d": "Mandatos de la prueba automatizada", "r": REFERENCIA},
        ).scalar_one()
        # Confirmar exige un modo de operación; el consorcio de prueba queda en
        # `administrador_aumentado` desde la primera corrida.
        con_modo = conn.execute(
            text("select exists (select 1 from nucleo.modo_operacion where vigente_desde <= :h"
                 " and (vigente_hasta is null or vigente_hasta > :h))"), {"h": hoy},
        ).scalar_one()  # fmt: skip
        if not con_modo:
            conn.execute(
                text("select nucleo.cambiar_modo('administrador_aumentado', :d, :h, null,"
                     " 'soporte')"), {"d": decision, "h": hoy},
            )  # fmt: skip
    for rol in ("consejo1", "consejo2"):
        alta(corrida, rol, "miembro_consejo", decision, hoy, persona=corrida.personas[rol])
    alta(corrida, "administracion", "administrador", decision, hoy,
         persona=corrida.personas["administracion"])  # fmt: skip

    r = prop.pedir("GET", "/api/v1/me/consorcios")
    [fila] = [x for x in r.cuerpo if x["id"] == str(corrida.consorcio)]
    comprobar(fila["vinculos"] == ["propietario"], f"C4 de la propietaria: {r.texto}")
    corrida.ok("el vínculo da ámbito (/me/consorcios)")

    r = prop.pedir("GET", corrida.c("/padron"))
    comprobar(r.estado == 403 and r.cuerpo["codigo"] == "sin_permiso", f"padrón: {r.texto}")
    comprobar(denegado(corrida, corrida.personas["propietaria"], "padron.ver"),
              "el 403 del padrón quedó auditado")  # fmt: skip
    corrida.ok("padrón sin padron.ver: 403 auditado")

    r = adm.pedir("GET", corrida.c("/padron"))
    comprobar(r.estado == 200, f"padrón de la administración: {r.texto}")
    comprobar(
        "correo" not in r.texto and "telefono" not in r.texto, "padrón sin correo ni teléfono"
    )
    [vp] = [x for x in r.cuerpo["elementos"] if x["unidad_id"] == str(u["A"])]
    comprobar(vp["confirmado"] is False, "el vínculo de la propietaria entra sin confirmar")
    corrida.ok("padrón mínimo, sin correo ni teléfono")

    clave = str(uuid4())
    ruta = corrida.c(f"/vinculos/{vp['vinculo_id']}/confirmar")
    r = adm.pedir("POST", ruta, {"referencia": REFERENCIA}, csrf=True, clave=clave)
    comprobar(r.estado == 200 and r.cuerpo["confirmado"] is True, f"confirmar: {r.texto}")
    otra = adm.pedir("POST", ruta, {"referencia": REFERENCIA}, csrf=True, clave=clave)
    comprobar(otra.estado == 200 and otra.encabezados.get(ENCABEZADO_REPETIDA.lower()) == "true",
              "el reintento con la misma clave repite la respuesta")  # fmt: skip
    sin_csrf = adm.pedir("POST", ruta, {"referencia": REFERENCIA}, clave=str(uuid4()))
    comprobar(sin_csrf.estado == 403, f"control: sin CSRF se rechaza: {sin_csrf.texto}")
    r = adm.pedir("GET", corrida.c("/padron"))
    [vp] = [x for x in r.cuerpo["elementos"] if x["unidad_id"] == str(u["A"])]
    comprobar(vp["confirmado"] is True, "el padrón muestra el vínculo confirmado")
    corrida.ok("confirmar un vínculo con CSRF y clave de reintento (control: sin CSRF, 403)")

    inquilino = corrida.correo("inquilino")
    r = mc1.pedir("POST", corrida.c("/invitaciones"),
                  {"unidad_id": str(u["A"]), "tipo": "inquilino", "correo": inquilino,
                   "nombre": "Prueba inquilino"}, csrf=True, clave=str(uuid4()))  # fmt: skip
    comprobar(r.estado == 201, f"crear invitación: {r.texto}")
    invitacion = r.cuerpo["id"]

    def de_la_lista(n: Navegador) -> dict:
        r = n.pedir("GET", corrida.c("/invitaciones"))
        comprobar(r.estado == 200, f"listar invitaciones: {r.texto}")
        [x] = [x for x in r.cuerpo["elementos"] if x["id"] == invitacion]
        return x

    comprobar(de_la_lista(mc1)["correo"] == inquilino, "P6: quien la creó ve el correo")
    comprobar(de_la_lista(mc2)["correo"] is None, "P6: otro autorizado no lo ve")
    r = mc1.pedir("POST", corrida.c(f"/invitaciones/{invitacion}/revocar"), csrf=True)
    comprobar(r.estado == 204, f"revocar: {r.texto}")
    comprobar(de_la_lista(mc1)["estado"] == "revocada", "la lista la muestra revocada")
    corrida.ok("crear, listar (P6) y revocar una invitación")

    # 2b-4b: decisión, alta y baja por HTTP. A15: quien registró la decisión no la usa.
    r = mc1.pedir("POST", corrida.c("/decisiones"),
                  {"tipo": "asamblea", "fecha": str(hoy), "referencia": REFERENCIA,
                   "descripcion": "Prueba automatizada: designa y remueve"},
                  csrf=True, clave=str(uuid4()))  # fmt: skip
    comprobar(r.estado == 201, f"registrar una decisión: {r.texto}")
    acta = r.cuerpo["id"]
    nuevo = {"tipo": "miembro_consejo", "persona_id": str(corrida.personas["propietaria"]),
             "decision_id": acta, "desde": str(hoy)}  # fmt: skip
    r = mc1.pedir("POST", corrida.c("/mandatos"), nuevo, csrf=True, clave=str(uuid4()))
    comprobar(r.estado == 403, f"A15: quien registró la decisión no da el alta: {r.texto}")
    comprobar(denegado(corrida, corrida.personas["consejo1"], "mandatos.alta"),
              "el 403 de A15 en el alta quedó auditado")  # fmt: skip
    r = mc2.pedir("POST", corrida.c("/mandatos"), nuevo, csrf=True, clave=str(uuid4()))
    comprobar(r.estado == 201, f"alta por HTTP con la decisión de otra persona: {r.texto}")
    corrida.mandatos["consejo_http"] = UUID(r.cuerpo["id"])
    remocion = {"causa": "remocion", "decision_id": acta,
                "descripcion": "Fin de la prueba automatizada"}  # fmt: skip
    ruta = corrida.c(f"/mandatos/{corrida.mandatos['consejo_http']}/baja")
    r = mc1.pedir("POST", ruta, remocion, csrf=True, clave=str(uuid4()))
    comprobar(r.estado == 403, f"A15: quien registró la asamblea no remueve: {r.texto}")
    comprobar(denegado(corrida, corrida.personas["consejo1"], "mandatos.baja"),
              "el 403 de A15 en la baja quedó auditado")  # fmt: skip
    r = mc2.pedir("POST", ruta, remocion, csrf=True, clave=str(uuid4()))
    comprobar(r.estado == 200 and r.cuerpo["causa"] == "remocion", f"remoción: {r.texto}")
    corrida.terminados.add("consejo_http")
    corrida.ok("decisión, alta y remoción por HTTP, con A15 (403 auditado al que registró)")

    baja(corrida, "administracion")
    alta(corrida, "externo", "administrador_externo", decision, hoy,
         nombre="Estudio de la prueba automatizada", cuit=CUIT_SINTETICO)  # fmt: skip
    ae = corrida.mandatos["externo"]
    correo_ae = corrida.correo("externo")
    r = mc1.pedir("POST", corrida.c(f"/mandatos/{ae}/invitaciones"),
                  {"correo": correo_ae, "referencia": REFERENCIA}, csrf=True,
                  clave=str(uuid4()))  # fmt: skip
    comprobar(r.estado == 201 and r.cuerpo["estado"] == "por_aprobar", f"emitir A7: {r.texto}")
    emitida = r.cuerpo["id"]
    aprobar = corrida.c(f"/invitaciones-mandato/{emitida}/aprobar")
    r = mc1.pedir("POST", aprobar, csrf=True, clave=str(uuid4()))
    comprobar(r.estado == 403, f"cuatro ojos: quien emitió no aprueba: {r.texto}")
    comprobar(denegado(corrida, corrida.personas["consejo1"], "mandatos.aprobar_asociacion"),
              "el 403 de cuatro ojos quedó auditado")  # fmt: skip
    r = mc2.pedir("POST", aprobar, csrf=True, clave=str(uuid4()))
    comprobar(r.estado == 200 and r.cuerpo["estado"] == "pendiente", f"aprobar A7: {r.texto}")
    with corrida.tx() as conn:
        textos = conn.execute(
            text("select carga->>'texto' from plataforma.outbox where consorcio_id = :c"
                 " and carga->>'para' = :p"), {"c": corrida.consorcio, "p": correo_ae},
        ).scalars().all()  # fmt: skip
    comprobar(len(textos) == 1 and CUIT_SINTETICO not in textos[0], "un correo de A7, sin CUIT")
    [token] = TOKEN_MANDATO.findall(textos[0])
    externo = corrida.navegador()
    r = externo.pedir("POST", "/api/v1/auth/invitacion-mandato/consultar", {"token": token})
    comprobar(r.estado == 200 and CUIT_SINTETICO not in r.texto, f"consultar A7: {r.texto}")
    r = externo.pedir("POST", "/api/v1/auth/invitacion-mandato/aceptar", {"token": token})
    comprobar(r.estado == 200 and r.cuerpo["cuenta_nueva"] is True
              and r.cuerpo["mandato_id"] == str(ae), f"aceptar A7: {r.texto}")  # fmt: skip
    corrida.personas["externo"] = UUID(r.cuerpo["persona_id"])
    corrida.navegadores["externo"] = externo
    r = externo.pedir("GET", "/api/v1/me/consorcios")
    [fila] = [x for x in r.cuerpo if x["id"] == str(corrida.consorcio)]
    comprobar(fila["vinculos"] == [], "el externo entra por el mandato, sin vínculo")
    corrida.ok("A7 con cuatro ojos (403 auditado al emisor) y un mandato que da ámbito")

    r = mc2.pedir("POST", corrida.c(f"/mandatos/{ae}/desasociar-persona"),
                  {"referencia": REFERENCIA}, csrf=True, clave=str(uuid4()))  # fmt: skip
    comprobar(r.estado == 200 and r.cuerpo["sesiones_revocadas"] >= 1, f"desasociar: {r.texto}")
    r = externo.pedir("GET", "/api/v1/me/consorcios")
    comprobar(r.estado == 401, f"después de desasociar, la sesión del externo no vale: {r.texto}")
    corrida.ok("desasociar revoca la sesión y conserva el mandato")


def cadena(corrida: Corrida) -> tuple[int, int, int, int]:
    """Total, enlaces rotos y hashes distintos del consorcio; y, de control, cuántos hashes
    distintos da la misma cuenta con la acción alterada (tienen que ser todos)."""
    formula = (
        "encode(sha256(convert_to(concat_ws('|', hash_previo, consorcio_id, id, secuencia,"
        " to_char(creado_en at time zone 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS.US'),"
        " actor_persona_id, origen, {accion}, entidad, entidad_id, antes, despues, motivo, ip),"
        " 'UTF8')), 'hex')"
    )
    with corrida.tx() as conn:
        return tuple(
            conn.execute(
                text(
                    "with e as (select *, lag(hash) over (order by secuencia) as anterior"
                    " from auditoria.eventos where consorcio_id = :c)"
                    " select count(*),"
                    " count(*) filter (where hash_previo <> coalesce(anterior, '')),"
                    f" count(*) filter (where hash <> {formula.format(accion='accion')}),"
                    f" count(*) filter (where hash <> {formula.format(accion="accion || 'x'")})"
                    " from e"
                ),
                {"c": corrida.consorcio},
            ).one()
        )


def cerrar(corrida: Corrida) -> tuple[int, list[str]]:
    """Da de baja los mandatos que siguen vigentes y revoca las sesiones de las personas de la
    corrida. Devuelve cuántas sesiones quedan abiertas en la base y qué navegadores de la
    corrida siguen entrando por HTTP (las dos cosas tienen que dar cero).

    La RLS de `nucleo.sesion` (`propias`) solo deja ver las sesiones de la persona del ámbito:
    sin persona, la cuenta daría 0 siempre. Por eso se cuenta persona por persona."""
    for rol in corrida.mandatos.keys() - corrida.terminados:
        baja(corrida, rol)
    with corrida.tx() as conn:
        for persona in corrida.personas.values():
            conn.execute(text("select nucleo.revocar_sesiones_de(:p)"), {"p": persona})
    abiertas = 0
    for persona in corrida.personas.values():
        with transaccion(corrida.motor, persona_id=persona) as conn:
            abiertas += conn.execute(
                text("select count(*) from nucleo.sesion where persona_id = :p"
                     " and revocada_en is null and vence_en > now()"), {"p": persona},
            ).scalar_one()  # fmt: skip
    entran = [rol for rol, n in corrida.navegadores.items()
              if n.pedir("GET", "/api/v1/me/consorcios").estado != 401]  # fmt: skip
    return abiertas, entran


def main(argumentos: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--consorcio", type=UUID, required=True)
    p.add_argument("--base", required=True, help="host de cem-postgres-staging")
    p.add_argument("--url", required=True, help="adónde mandar los pedidos HTTP")
    p.add_argument("--host", required=True, help="encabezado Host del frontend de staging")
    p.add_argument("--sin-verificar-certificado", action="store_true")
    a = p.parse_args(argumentos)
    config = cargar()
    try:
        exigir_staging(config.origen_publico, config.database_url, a.base)
    except Negado as e:
        print(f"negado: {e}", file=sys.stderr)
        return 3
    contexto = None
    if a.sin_verificar_certificado:
        contexto = ssl.create_default_context()
        contexto.check_hostname = False
        contexto.verify_mode = ssl.CERT_NONE
    motor = crear_motor(config.database_url)
    corrida = Corrida(motor, a.consorcio, config.origen_publico, a.url.rstrip("/"), a.host,
                      contexto)  # fmt: skip
    try:
        with corrida.tx() as conn:
            creado = asegurar_consorcio(conn, a.consorcio)
        print(
            f"corrida {corrida.id}; consorcio {a.consorcio} ({'creado' if creado else 'existente'})"
        )
        error = None
        try:
            recorrer(corrida)
            total, rotos, distintos, control = cadena(corrida)
            comprobar(total > 0 and rotos == 0 and distintos == 0,
                      f"cadena: {total} eventos, {rotos} rotos, {distintos} distintos")  # fmt: skip
            comprobar(control == total, f"control de la cadena: {control} de {total}")
            corrida.ok(f"cadena de auditoría: {total} eventos, 0 rotos, 0 distintos (control:"
                       f" {control} de {total} detectados con la acción alterada)")  # fmt: skip
        except Fallo as e:
            error = e
            # Antes de cerrar: si el cierre también falla, el desvío original ya quedó dicho.
            print(f"FALLÓ: {type(error).__name__}: {error}", file=sys.stderr, flush=True)
        finally:
            # Siempre, también ante una excepción inesperada (que después sigue su curso y
            # termina el proceso con su traza): revocar sesiones y dar de baja los mandatos.
            abiertas, entran = cerrar(corrida)
            resumen = {
                "corrida": corrida.id,
                "consorcio": str(a.consorcio),
                "personas": {k: str(v) for k, v in corrida.personas.items()},
                "mandatos": {k: str(v) for k, v in corrida.mandatos.items()},
                "sesiones_abiertas_al_cerrar": abiertas,
                "navegadores_que_siguen_entrando": entran,
                "controles": corrida.controles,
            }
            print(json.dumps(resumen, ensure_ascii=False, indent=2))
        if error is not None:
            return 1
        if abiertas != 0 or entran:
            print(
                f"FALLÓ: quedaron {abiertas} sesiones abiertas; siguen entrando: {entran}",
                file=sys.stderr,
            )
            return 1
        return 0
    except Negado as e:
        print(f"negado: {e}", file=sys.stderr)
        return 3
    finally:
        motor.dispose()


if __name__ == "__main__":
    sys.exit(main())
