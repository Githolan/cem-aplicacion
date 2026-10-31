"""Ensayo sintético de egresos en staging, de punta a punta (CEM LAB: SINTETICO, PRUEBA, NO REAL).

Mandato del propietario del 30-10-2026, puntos 17 a 20: recorrer con datos sintéticos, en
staging y nunca en producción, el flujo de una orden de pago completo por HTTP (frontend de
staging: nginx, cookies, CSRF, segundo factor y claves de reintento reales), con el riel de pago
**simulado**: el ensayo no conecta ningún banco ni proveedor de pagos, no usa ninguna cuenta real
y no mueve dinero.

Recorre:
1. personas sintéticas que entran por invitación de soporte, con su identidad verificada (A13-C),
   mandatos de administración y de Consejo, y TOTP dado de alta y confirmado por HTTP;
2. un proveedor nuevo con una cuenta sintética válida (prefijo 999, de ningún banco), y que quien
   prepara nunca reciba la cuenta completa (punto 6);
3. un documento de respaldo subido por HTTP y analizado por el antivirus real de staging, un
   abono registrado por la administración y confirmado por un consejero;
4. órdenes: proveedor nuevo con aprobación del Consejo; rutinaria por el abono; bajo el umbral;
   modificación después de aprobar (409 con la huella vieja, sin trasladar la aprobación); la
   aprobación que pide el segundo factor y se reintenta con la misma clave; separación humana
   (quien aprueba no emite);
5. la ejecución con el riel simulado, corriendo los manejadores reales de `cem-trabajos` en este
   proceso: pagada; incierta que la consulta encuentra pagada (un solo envío); incierta que el
   riel nunca recibió (Fallida, sin reenvío, y emitir de nuevo la misma versión se niega);
6. concurrencia: dos emisiones a la vez con claves distintas (una sola pasa) y con la misma clave
   (un solo efecto).

Al final comprueba que ningún evento de auditoría del consorcio lleva una cuenta, que la cola del
consorcio no tiene trabajos muertos, la cadena de auditoría, y revoca sesiones y da de baja los
mandatos de la corrida.

**El riel y la cola de staging.** `cem-trabajos-staging` no tiene riel (`sin_riel`): los trabajos
que encola cada emisión los termina sin hacer nada. El ensayo corre los mismos manejadores
(`ordenar_pago`, `verificar_pago`) en este proceso, con el ámbito de un trabajo (consorcio, sin
persona) y un riel simulado que vive solo acá. Lo que prueba es el código real contra la base
real; lo que no prueba es un riel real (SIN VERIFICAR).

**Datos.** El consorcio `--consorcio` se crea la primera vez con `NOMBRE_CONSORCIO`; si existe con
otro nombre, se niega. Cada corrida agrega unidades, personas `@cem-prueba.invalid`, una decisión
`SINTETICA`, mandatos que terminan al final, un proveedor, un documento, un abono y órdenes: todo
de solo agregar, marcado SINTETICO, y queda en staging como historia. Nunca en producción.

Corre como el smoke, leído de la entrada estándar dentro de `cem-backend-staging`:

    ssh oci 'docker exec -i -w /app -e PYTHONPATH=/app <contenedor> python - \\
        --consorcio <uuid> --base <host de cem-postgres-staging> \\
        --url https://coolify-proxy --host cem-staging.holancloud.com \\
        --sin-verificar-certificado' < backend/pruebas/ensayo_egresos_staging.py

Sale con 0 solo si todo dio lo esperado; ante el primer desvío falla con el detalle y aun así
cierra (sesiones y mandatos).
"""

import argparse
import base64
import io
import json
import os
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from http.cookies import SimpleCookie
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from cem_contracts.estados import TipoVinculo
from cem_contracts.invitaciones import InvitacionNueva
from pypdf import PdfWriter
from sqlalchemy import Engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from app.config import cargar
from app.consorcios import pagos
from app.consorcios.pagos import EstadoRiel, PedidoPago, RespuestaRiel
from app.db import crear_motor, transaccion
from app.identidad import invitaciones, sesiones
from app.identidad import segundo_factor as segundo_factor_app
from app.plataforma import cola
from app.plataforma.idempotencia import ENCABEZADO, ENCABEZADO_REPETIDA

NOMBRE_CONSORCIO = "CEM LAB — egresos sintéticos (SINTETICO, PRUEBA, NO REAL)"
DOMINIO = "cem-prueba.invalid"
SUFIJO_STAGING = "-staging"
ZONA = "America/Argentina/Buenos_Aires"
REFERENCIA = "ACTA SINTETICA DE PRUEBA — SIN VALIDEZ JURIDICA — ensayo de egresos en staging"
IP_DE_PRUEBA = "198.51.100.78"
"""RFC 5737: otra que la del smoke (…77), así sus cupos de autenticación no se mezclan."""
UMBRAL_CENTAVOS = 10_000_000
"""El umbral sintético del consorcio LAB: 100.000,00 ARS."""
ESPERA_ANTIVIRUS_S = 120
PESOS_1 = (7, 1, 3, 9, 7, 1, 3)
PESOS_2 = (3, 9, 7, 1, 3, 9, 7, 1, 3, 9, 7, 1, 3)


class Negado(Exception):
    """La guarda dura: el entorno no es staging o el consorcio no es el del ensayo."""


class Fallo(Exception):
    """Un paso no dio lo esperado."""


def comprobar(condicion: bool, que: str) -> None:
    if not condicion:
        raise Fallo(que)


def _digito(cifras: str, pesos: tuple[int, ...]) -> str:
    return str((10 - sum(int(c) * p for c, p in zip(cifras, pesos, strict=True)) % 10) % 10)


def pdf_sintetico(corrida_id: str) -> bytes:
    """Un PDF válido de una página en blanco, sin contenido activo: el analizador de documentos
    lo lee con pypdf y lo rechaza si no puede (`pdf_mal_formado`). Único por corrida, así el
    antivirus y el almacenamiento lo ven como un archivo nuevo."""
    w = PdfWriter()
    w.add_blank_page(width=595, height=842)
    w.add_metadata({"/Title": f"CEM LAB {corrida_id} SINTETICO (NO REAL)"})
    salida = io.BytesIO()
    w.write(salida)
    return salida.getvalue()


def cbu_sintetico(n: int) -> str:
    """Una cuenta con sus dos dígitos verificadores y el prefijo 999, que no es de ningún banco."""
    bloque1 = f"9990{n % 1000:03d}"
    bloque2 = f"{n:013d}"
    return bloque1 + _digito(bloque1, PESOS_1) + bloque2 + _digito(bloque2, PESOS_2)


# Guarda --------------------------------------------------------------------------------------


def exigir_staging(origen_publico: str, database_url: str, base: str, host: str) -> None:
    host_publico = urlsplit(origen_publico).hostname or ""
    if not host_publico.split(".")[0].endswith(SUFIJO_STAGING):
        raise Negado(f"CEM_ORIGEN_PUBLICO no es de staging: {host_publico!r}")
    if make_url(database_url).host != base:
        raise Negado(f"CEM_DATABASE_URL no apunta a --base {base!r}")
    # El HTTP va al mismo staging que la base: el proxy podría servir también producción.
    if host != host_publico:
        raise Negado(f"--host {host!r} no es el de CEM_ORIGEN_PUBLICO ({host_publico!r})")


def asegurar_consorcio(motor: Engine, consorcio: UUID) -> bool:
    with transaccion(motor, persona_id=None, consorcio_id=consorcio) as conn:
        nombre = conn.execute(
            text("select nombre from nucleo.consorcio where id = :c"), {"c": consorcio}
        ).scalar_one_or_none()
        if nombre is not None:
            if nombre != NOMBRE_CONSORCIO:
                raise Negado(f"el consorcio {consorcio} se llama {nombre!r}: no es el del ensayo")
            return False
        conn.execute(
            text("insert into nucleo.consorcio (id, nombre, zona_horaria) values (:c, :n, :z)"),
            {"c": consorcio, "n": NOMBRE_CONSORCIO, "z": ZONA},
        )
    return True


# HTTP ----------------------------------------------------------------------------------------


@dataclass
class Respuesta:
    estado: int
    encabezados: dict[str, str]
    cuerpo: object
    texto: str

    def codigo(self) -> str | None:
        return self.cuerpo.get("codigo") if isinstance(self.cuerpo, dict) else None


@dataclass
class Navegador:
    url: str
    host: str
    contexto: ssl.SSLContext | None
    cookies: dict[str, str] = field(default_factory=dict)
    candado: threading.Lock = field(default_factory=threading.Lock)

    def pedir(self, metodo: str, ruta: str, cuerpo: object = None, *, csrf: bool = False,
              clave: str | None = None, crudo: bytes | None = None,
              tipo: str | None = None) -> Respuesta:  # fmt: skip
        encabezados = {"Host": self.host, "Accept": "application/json",
                       "CF-Connecting-IP": IP_DE_PRUEBA}  # fmt: skip
        datos = crudo
        if cuerpo is not None:
            datos = json.dumps(cuerpo).encode()
            encabezados["Content-Type"] = "application/json"
        elif tipo is not None:
            encabezados["Content-Type"] = tipo
        with self.candado:
            if self.cookies:
                encabezados["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
            if csrf:
                encabezados[sesiones.ENCABEZADO_CSRF] = self.cookies[sesiones.COOKIE_CSRF]
        if clave is not None:
            encabezados[ENCABEZADO] = clave
        pedido = urllib.request.Request(self.url + ruta, data=datos, headers=encabezados,
                                        method=metodo)  # fmt: skip
        try:
            r = urllib.request.urlopen(pedido, context=self.contexto, timeout=60)
        except urllib.error.HTTPError as e:
            r = e
        with self.candado:
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


# Riel simulado -------------------------------------------------------------------------------


@dataclass
class RielSimulado:
    """Solo de este ensayo. Idempotente por clave, como exige `RielDePago`. `modo` decide qué
    pasa con el próximo pedido: `pagar`, `pagar_sin_responder` (el riel paga y la respuesta se
    pierde) o `perder` (el pedido nunca llega al riel)."""

    modo: str = "pagar"
    pagos: dict[UUID, RespuestaRiel] = field(default_factory=dict)
    envios: int = 0
    consultas: int = 0

    def ordenar(self, pedido: PedidoPago) -> RespuestaRiel:
        self.envios += 1
        if self.modo == "perder":
            raise TimeoutError("SIMULADO: el pedido no llegó")
        if pedido.clave not in self.pagos:
            self.pagos[pedido.clave] = RespuestaRiel(
                EstadoRiel.PAGADA, referencia=f"SIM-LAB-{len(self.pagos) + 1}"
            )
        if self.modo == "pagar_sin_responder":
            raise TimeoutError("SIMULADO: el riel pagó y la respuesta se perdió")
        return self.pagos[pedido.clave]

    def consultar(self, clave: UUID) -> RespuestaRiel:
        self.consultas += 1
        return self.pagos.get(clave, RespuestaRiel(EstadoRiel.DESCONOCIDA))


# Corrida -------------------------------------------------------------------------------------


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
    navegadores: dict[str, Navegador] = field(default_factory=dict)
    secretos_totp: dict[str, bytes] = field(default_factory=dict)
    pasos_totp: dict[str, int] = field(default_factory=dict)
    cuentas: list[str] = field(default_factory=list)
    controles: list[str] = field(default_factory=list)
    riel: RielSimulado = field(default_factory=RielSimulado)
    inicio: datetime | None = None
    ejecuciones: set[str] = field(default_factory=set)

    def ok(self, que: str) -> None:
        self.controles.append(que)
        print(f"ok  {que}", flush=True)

    def tx(self):
        return transaccion(self.motor, persona_id=None, consorcio_id=self.consorcio)

    def c(self, resto: str = "") -> str:
        return f"/api/v1/consorcios/{self.consorcio}{resto}"

    def n(self, rol: str) -> Navegador:
        return self.navegadores[rol]


def entra(corrida: Corrida, rol: str) -> None:
    """Una unidad propia, la invitación de soporte (`.invalid`: no sale correo) y la aceptación
    por HTTP; después soporte verifica la identidad humana (huella al azar: no hay documento)."""
    with corrida.tx() as conn:
        unidad = conn.execute(
            text("insert into nucleo.unidad (consorcio_id, designacion) values (:c, :d)"
                 " returning id"), {"c": corrida.consorcio, "d": f"LAB {corrida.id} {rol}"},
        ).scalar_one()  # fmt: skip
        creada = invitaciones.crear(
            conn, consorcio_id=corrida.consorcio,
            nueva=InvitacionNueva(unidad_id=unidad, tipo=TipoVinculo.PROPIETARIO,
                                  correo=f"egresos-{corrida.id}-{rol}@{DOMINIO}",
                                  nombre=f"LAB {rol} SINTETICO"),
            invitada_por=None, origen_publico=corrida.origen_publico, origen="soporte",
        )  # fmt: skip
    n = Navegador(corrida.url, corrida.host, corrida.contexto)
    r = n.pedir("POST", "/api/v1/auth/invitacion/aceptar", {"token": creada.token})
    comprobar(r.estado == 200, f"aceptar la invitación de {rol}: {r.texto}")
    corrida.personas[rol] = UUID(r.cuerpo["persona_id"])
    corrida.navegadores[rol] = n
    with corrida.tx() as conn:
        conn.execute(
            text("select nucleo.verificar_identidad_humana(:p, array[1]::smallint[],"
                 " array[:h]::bytea[], 'documento_presencial', :r, 'Ensayo de egresos')"),
            {"p": corrida.personas[rol], "h": os.urandom(32),
             "r": REFERENCIA + "; cuenta sin documento, huella al azar"},
        )  # fmt: skip


def activar_totp(corrida: Corrida, rol: str) -> None:
    """Alta y confirmación por HTTP: la sesión queda con el segundo factor recién verificado."""
    n = corrida.n(rol)
    r = n.pedir("POST", "/api/v1/auth/totp/alta", csrf=True)
    comprobar(r.estado == 200, f"alta del TOTP de {rol}: {r.texto}")
    clave = r.cuerpo["secreto"]
    secreto = base64.b32decode(clave + "=" * (-len(clave) % 8))
    paso = segundo_factor_app.paso_actual()
    r = n.pedir("POST", "/api/v1/auth/totp/confirmar",
                {"codigo": segundo_factor_app.codigo_en(secreto, paso)}, csrf=True)  # fmt: skip
    comprobar(r.estado == 200, f"confirmar el TOTP de {rol}: {r.texto}")
    corrida.secretos_totp[rol] = secreto
    corrida.pasos_totp[rol] = paso


def preparar_escena(corrida: Corrida) -> None:
    for rol in ("administracion", "consejo1", "consejo2"):
        entra(corrida, rol)
    with corrida.tx() as conn:
        hoy = conn.execute(
            text("select nucleo.hoy_del_consorcio(:c)"), {"c": corrida.consorcio}
        ).scalar_one()
        decision = conn.execute(
            text("select nucleo.registrar_decision('asamblea', :h, :d, :r, null, 'soporte')"),
            {"h": hoy, "d": "SINTETICA: mandatos y umbral del ensayo de egresos",
             "r": REFERENCIA},
        ).scalar_one()  # fmt: skip
        con_modo = conn.execute(
            text("select exists (select 1 from nucleo.modo_operacion where vigente_desde <= :h"
                 " and (vigente_hasta is null or vigente_hasta > :h))"), {"h": hoy},
        ).scalar_one()  # fmt: skip
        if not con_modo:
            conn.execute(
                text("select nucleo.cambiar_modo('administrador_aumentado', :d, :h, null,"
                     " 'soporte')"), {"d": decision, "h": hoy},
            )  # fmt: skip
        mandatos = {}
        for rol, tipo in (("administracion", "administrador"), ("consejo1", "miembro_consejo"),
                          ("consejo2", "miembro_consejo")):  # fmt: skip
            mandatos[rol] = conn.execute(
                text("select nucleo.alta_mandato(:t, :p, null, null, null, :d, :h, null, null,"
                     " null, 'soporte')"),
                {"t": tipo, "p": corrida.personas[rol], "d": decision, "h": hoy},
            ).scalar_one()  # fmt: skip
        # El umbral de la corrida rige desde hoy. `cem_app` no lee la tabla de umbrales: si otra
        # corrida de hoy ya lo fijó, la función lo dice (`vigencia_repetida`) y vale ese.
        try:
            with conn.begin_nested():
                conn.execute(
                    text("select nucleo.fijar_umbral_orden_pago(:i, 'ARS', :h, :d, null,"
                         " 'soporte')"), {"i": UMBRAL_CENTAVOS, "h": hoy, "d": decision},
                )  # fmt: skip
        except DBAPIError as e:
            if e.orig.diag.message_hint != "vigencia_repetida":
                raise
    # Recién confirmados: si la transacción se deshizo, `cerrar` no tiene mandatos que dar de baja.
    corrida.mandatos.update(mandatos)
    corrida.ok("tres personas sintéticas por invitación, identidad verificada, mandatos y umbral")
    for rol in ("administracion", "consejo1"):
        activar_totp(corrida, rol)
    corrida.ok("TOTP activo y verificado por HTTP para administración y consejo1")


# Proveedor, documento y abono ----------------------------------------------------------------


def proveedor(corrida: Corrida, n: int) -> str:
    cuenta = cbu_sintetico(int(corrida.id, 16) % 10**9 + n)
    corrida.cuentas.append(cuenta)
    adm = corrida.n("administracion")
    r = adm.pedir("POST", corrida.c("/proveedores"),
                  {"razon_social": f"Proveedor LAB {corrida.id}-{n} SINTETICO (NO REAL)",
                   "rubro": "Mantenimiento SINTETICO", "cuenta_destino": cuenta},
                  csrf=True, clave=str(uuid4()))  # fmt: skip
    comprobar(r.estado == 201, f"alta del proveedor {n}: {r.texto}")
    p = r.cuerpo["id"]
    for ruta in ("/proveedores", f"/proveedores/{p}"):
        leido = adm.pedir("GET", corrida.c(ruta))
        comprobar(leido.estado == 200 and cuenta not in leido.texto,
                  f"quien prepara no recibe la cuenta completa en {ruta}")  # fmt: skip
        comprobar(cuenta[-4:] in leido.texto, f"sí los 4 últimos dígitos en {ruta}")
    return p


def documento_vigente(corrida: Corrida) -> str:
    adm = corrida.n("administracion")
    r = adm.pedir("POST", corrida.c("/documentos"),
                  {"categoria": "contratos_proveedores",
                   "titulo": f"Abono LAB {corrida.id} SINTETICO (NO REAL)",
                   "fecha": time.strftime("%Y-%m-%d")}, csrf=True, clave=str(uuid4()))  # fmt: skip
    comprobar(r.estado == 201, f"crear el documento: {r.texto}")
    doc = r.cuerpo["id"]
    r = adm.pedir("POST", corrida.c(f"/documentos/{doc}/versiones"),
                  crudo=pdf_sintetico(corrida.id),
                  tipo="application/pdf", csrf=True, clave=str(uuid4()))  # fmt: skip
    comprobar(r.estado in (201, 202), f"subir la versión: {r.texto}")
    # Desde 0052 un contrato solo se respalda en una versión que pasó el antivirus. Se la espera
    # como la ve quien la subió (la lista de documentos le muestra también la cuarentena).
    limite = time.monotonic() + ESPERA_ANTIVIRUS_S
    estado = "en_cuarentena"
    while estado == "en_cuarentena" and time.monotonic() < limite:
        time.sleep(2)
        lista = adm.pedir("GET", corrida.c("/documentos"))
        comprobar(lista.estado == 200, f"listar documentos: {lista.texto}")
        [d] = [x for x in lista.cuerpo["documentos"] if x["id"] == doc]
        estado = d["versiones"][0]["estado"]
    comprobar(
        estado == "disponible", f"el antivirus de staging deja la versión disponible: {estado}"
    )
    corrida.ok("documento de respaldo subido por HTTP; el antivirus real lo deja disponible")
    return doc


def abono_confirmado(corrida: Corrida, p: str, doc: str, tope: int) -> str:
    adm, mc1 = corrida.n("administracion"), corrida.n("consejo1")
    with corrida.tx() as conn:
        hoy = conn.execute(
            text("select nucleo.hoy_del_consorcio(:c)"), {"c": corrida.consorcio}
        ).scalar_one()
    r = adm.pedir("POST", corrida.c("/contratos"),
                  {"proveedor_id": p, "tipo": "abono",
                   "alcance": "SINTETICO: mantenimiento mensual del ascensor (NO REAL)",
                   "importe_maximo_centavos": tope, "moneda": "ARS",
                   "vigente_desde": hoy.isoformat(),
                   "vigente_hasta": (hoy + timedelta(days=90)).isoformat(),
                   "documento_id": doc}, csrf=True, clave=str(uuid4()))  # fmt: skip
    comprobar(r.estado == 201, f"registrar el abono: {r.texto}")
    k = r.cuerpo["id"]
    r = adm.pedir("POST", corrida.c(f"/contratos/{k}/confirmacion"),
                  {"huella": "0" * 64, "proveedor_version": 1}, csrf=True,
                  clave=str(uuid4()))  # fmt: skip
    comprobar(r.estado == 403, f"quien registró no confirma (sin la facultad): {r.texto}")
    revision = mc1.pedir("GET", corrida.c("/contratos/revision"))
    [visto] = [x for x in revision.cuerpo["elementos"] if x["id"] == k]
    comprobar(visto["estado"] == "pendiente_confirmacion", f"pendiente: {visto}")
    # Desde 0053 la confirmación es de la versión del proveedor que se vio (P3 112).
    r = mc1.pedir("POST", corrida.c(f"/contratos/{k}/confirmacion"),
                  {"huella": visto["huella"], "proveedor_version": visto["proveedor_version"]},
                  csrf=True, clave=str(uuid4()))  # fmt: skip
    comprobar(r.estado == 201, f"el consejero confirma el abono: {r.texto}")
    [vigente] = [x for x in adm.pedir("GET", corrida.c("/contratos")).cuerpo["elementos"]
                 if x["id"] == k]  # fmt: skip
    comprobar(vigente["estado"] == "vigente", f"el abono rige: {vigente}")
    corrida.ok("abono registrado por la administración, confirmado por otro ser humano (Consejo)")
    return k


# Órdenes -------------------------------------------------------------------------------------


def preparar(corrida: Corrida, p: str, importe: int, contrato: str | None = None) -> str:
    adm = corrida.n("administracion")
    cuerpo = {"proveedor_id": p, "importe_centavos": importe, "moneda": "ARS",
              "concepto": f"SINTETICO {corrida.id}: servicio de prueba (NO REAL)",
              "comprobante": f"Factura TEST-{corrida.id}", "usa_fondo_reserva": False}  # fmt: skip
    if contrato is not None:
        cuerpo["contrato_id"] = contrato
    r = adm.pedir("POST", corrida.c("/ordenes-pago"), cuerpo, csrf=True, clave=str(uuid4()))
    comprobar(r.estado == 201, f"preparar: {r.texto}")
    o = r.cuerpo["id"]
    r = adm.pedir("POST", corrida.c(f"/ordenes-pago/{o}/enviar-a-revision"),
                  {"version_esperada": 1}, csrf=True, clave=str(uuid4()))  # fmt: skip
    comprobar(r.estado == 200, f"enviar a revisión: {r.texto}")
    return o


def ficha(corrida: Corrida, rol: str, o: str, revision: bool = False) -> dict:
    r = corrida.n(rol).pedir("GET", corrida.c(f"/ordenes-pago/{o}" + ("/revision" if revision
                                                                     else "")))  # fmt: skip
    comprobar(r.estado == 200, f"ficha de {o} para {rol}: {r.texto}")
    return r.cuerpo


def vigente(detalle: dict) -> dict:
    return next(
        v for v in detalle["versiones"] if v["numero"] == detalle["orden"]["version_vigente"]
    )


LECTOR_REVISION = "consejo1"
"""Quien lee la versión y su huella para armar una aprobación. No es siempre quien la intenta:
la administración no lee la revisión (403), y consejo2 todavía no tiene el segundo factor."""


def aprobar(corrida: Corrida, rol: str, o: str, clave: str | None = None) -> Respuesta:
    v = vigente(ficha(corrida, LECTOR_REVISION, o, revision=True))
    return corrida.n(rol).pedir("POST", corrida.c(f"/ordenes-pago/{o}/aprobaciones"),
                                {"version": v["numero"], "huella": v["huella"]}, csrf=True,
                                clave=clave or str(uuid4()))  # fmt: skip


def emitir(corrida: Corrida, rol: str, o: str, clave: str | None = None) -> Respuesta:
    v = vigente(ficha(corrida, "administracion", o))
    return corrida.n(rol).pedir("POST", corrida.c(f"/ordenes-pago/{o}/emitir"),
                                {"version": v["numero"], "huella": v["huella"]}, csrf=True,
                                clave=clave or str(uuid4()))  # fmt: skip


def estado(corrida: Corrida, o: str) -> str:
    return ficha(corrida, "administracion", o)["orden"]["estado"]


def ejecucion(corrida: Corrida, o: str) -> UUID:
    """La ejecución que dejó la última emisión de `o`. `cem_app` no lee las ejecuciones; sí la
    cola, y cada emisión encola un `egresos.ordenar_pago` con su ejecución. Las órdenes se emiten
    de a una, así que la emisión de `o` deja exactamente un trabajo nuevo desde la corrida."""
    with corrida.tx() as conn:
        filas = set(conn.execute(
            text("select carga->>'ejecucion' from plataforma.jobs where consorcio_id = :c"
                 " and tipo = :t and creado_en >= :i"),
            {"c": corrida.consorcio, "t": pagos.TRABAJO_ORDENAR, "i": corrida.inicio},
        ).scalars())  # fmt: skip
    nuevas = filas - corrida.ejecuciones
    comprobar(len(nuevas) == 1, f"una sola ejecución nueva de {o}: {len(nuevas)}")
    corrida.ejecuciones |= nuevas
    return UUID(nuevas.pop())


def trabajo(tipo: str, consorcio: UUID, carga: dict) -> cola.Trabajo:
    return cola.Trabajo(uuid4(), tipo, consorcio, carga, 1, 1)


def ordenar(corrida: Corrida, x: UUID) -> dict:
    with corrida.tx() as conn:
        return pagos.ordenar_pago(conn, trabajo(pagos.TRABAJO_ORDENAR, corrida.consorcio,
                                                {"ejecucion": str(x)}))  # fmt: skip


def verificar(corrida: Corrida, x: UUID) -> dict:
    with corrida.tx() as conn:
        carga = {"ejecucion": str(x), "consulta": 1}
        tarea = trabajo(pagos.TRABAJO_VERIFICAR, corrida.consorcio, carga)
        return pagos.verificar_pago(conn, tarea)


def recorrer_ordenes(corrida: Corrida) -> None:
    riel = corrida.riel
    p1 = proveedor(corrida, 1)
    corrida.ok("proveedor nuevo con cuenta sintética; quien prepara ve solo los 4 últimos")

    # 1. Proveedor nuevo: pide aprobación; quien prepara no aprueba; quien aprueba no emite.
    o1 = preparar(corrida, p1, 2_500_000)
    comprobar(estado(corrida, o1) == "en_revision", "beneficiario nuevo: queda en revisión")
    r = aprobar(corrida, "administracion", o1)
    comprobar(r.estado == 403, f"quien preparó no aprueba: {r.texto}")
    revision = ficha(corrida, "consejo1", o1, revision=True)
    comprobar(vigente(revision)["cuenta_destino"] == corrida.cuentas[0],
              "el Consejo ve la cuenta completa en la revisión")  # fmt: skip
    comprobar(corrida.cuentas[0] not in json.dumps(ficha(corrida, "administracion", o1)),
              "la ficha de preparación no la trae")  # fmt: skip
    r = aprobar(corrida, "consejo1", o1)
    comprobar(r.estado == 201, f"el Consejo aprueba: {r.texto}")
    r = emitir(corrida, "consejo1", o1)
    comprobar(r.estado == 403, f"quien aprobó no emite: {r.texto}")
    r = emitir(corrida, "administracion", o1)
    comprobar(r.estado == 200, f"la administración emite: {r.texto}")
    x1 = ejecucion(corrida, o1)
    riel.modo = "pagar"
    comprobar(ordenar(corrida, x1) == {"estado": "pagada"}, "el riel simulado paga")
    comprobar(estado(corrida, o1) == "pagada" and riel.envios == 1, "orden 1 pagada, un envío")
    corrida.ok(
        "proveedor nuevo: aprobación del Consejo, separación humana, emisión y pago simulado"
    )

    # 2. Rutinaria por el abono confirmado: no pide aprobación. La respuesta del riel se pierde.
    doc = documento_vigente(corrida)
    k = abono_confirmado(corrida, p1, doc, tope=3_000_000)
    o2 = preparar(corrida, p1, 2_000_000, contrato=k)
    comprobar(estado(corrida, o2) == "aprobada", "rutinaria por el abono: aprobada por franja")
    r = emitir(corrida, "administracion", o2)
    comprobar(r.estado == 200, f"emitir la rutinaria: {r.texto}")
    x2 = ejecucion(corrida, o2)
    riel.modo = "pagar_sin_responder"
    comprobar(ordenar(corrida, x2) == {"estado": "en_verificacion"}, "sin respuesta: incierta")
    envios = riel.envios
    comprobar(verificar(corrida, x2) == {"estado": "pagada"}, "la consulta la encuentra pagada")
    comprobar(riel.envios == envios and len(riel.pagos) == 2, "ningún segundo envío")
    comprobar(estado(corrida, o2) == "pagada", "orden 2 pagada")
    corrida.ok("rutinaria con abono; respuesta perdida, la consulta encuentra el pago (un envío)")

    # 3. Bajo el umbral, proveedor ya conocido: el pedido nunca llega al riel.
    o3 = preparar(corrida, p1, 1_000_000)
    comprobar(estado(corrida, o3) == "aprobada", "bajo el umbral, conocido: aprobada por franja")
    comprobar(emitir(corrida, "administracion", o3).estado == 200, "emitir la orden 3")
    x3 = ejecucion(corrida, o3)
    riel.modo = "perder"
    comprobar(ordenar(corrida, x3) == {"estado": "en_verificacion"}, "falla la llamada: incierta")
    comprobar(verificar(corrida, x3) == {"estado": "fallida"}, "el riel nunca la recibió: Fallida")
    r = emitir(corrida, "administracion", o3)
    comprobar(r.estado == 409, f"la misma versión no se emite de nuevo: {r.texto}")
    comprobar(len(riel.pagos) == 2, "nada pagado por la orden 3")
    corrida.ok("pedido que nunca llegó: Fallida por la consulta, sin reenvío ni segunda emisión")

    # 4. Sobre el umbral, modificada después de aprobar; la aprobación nueva pide el segundo
    # factor (consejo2 todavía no lo tiene) y se reintenta con la misma clave.
    o4 = preparar(corrida, p1, UMBRAL_CENTAVOS + 100)
    vieja = vigente(ficha(corrida, "consejo1", o4, revision=True))
    comprobar(aprobar(corrida, "consejo1", o4).estado == 201, "aprobada la versión 1")
    adm = corrida.n("administracion")
    r = adm.pedir("POST", corrida.c(f"/ordenes-pago/{o4}/versiones"),
                  {"proveedor_id": p1, "importe_centavos": UMBRAL_CENTAVOS + 200, "moneda": "ARS",
                   "concepto": f"SINTETICO {corrida.id}: importe corregido (NO REAL)",
                   "comprobante": f"Factura TEST-{corrida.id}-B", "usa_fondo_reserva": False,
                   "version_esperada": 1}, csrf=True, clave=str(uuid4()))  # fmt: skip
    comprobar(r.estado == 200, f"modificar después de aprobar: {r.texto}")
    comprobar(estado(corrida, o4) == "en_revision", "la versión 2 vuelve a revisión")
    r = corrida.n("consejo1").pedir("POST", corrida.c(f"/ordenes-pago/{o4}/aprobaciones"),
                                    {"version": 1, "huella": vieja["huella"]}, csrf=True,
                                    clave=str(uuid4()))  # fmt: skip
    comprobar(r.estado == 409, f"la huella vieja se niega (409): {r.texto}")
    comprobar(vigente(ficha(corrida, "consejo1", o4, revision=True))["aprobada_por"] == [],
              "la aprobación vieja no pasó a la versión 2")  # fmt: skip
    clave = str(uuid4())
    r = aprobar(corrida, "consejo2", o4, clave)
    comprobar(r.estado == 403 and r.codigo() == "segundo_factor_requerido",
              f"sin segundo factor reciente: {r.texto}")  # fmt: skip
    activar_totp(corrida, "consejo2")
    r = aprobar(corrida, "consejo2", o4, clave)
    comprobar(r.estado == 201, f"reintento con la misma clave después del código: {r.texto}")
    comprobar(estado(corrida, o4) == "aprobada", "orden 4 aprobada en su versión 2")
    corrida.ok("modificación después de aprobar: 409 y sin traslado; segundo factor y misma clave")

    # 5. Concurrencia sobre la orden 4: dos emisiones con claves distintas y dos con la misma.
    resultados: list[Respuesta] = []

    def emitir_en_hilo(clave_hilo: str) -> None:
        resultados.append(emitir(corrida, "administracion", o4, clave_hilo))

    misma = str(uuid4())
    hilos = [threading.Thread(target=emitir_en_hilo, args=(c,))
             for c in (str(uuid4()), str(uuid4()), misma, misma)]  # fmt: skip
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    estados = sorted(r.estado for r in resultados)
    exitosas = [r for r in resultados if r.estado == 200]
    originales = [r for r in exitosas if r.encabezados.get(ENCABEZADO_REPETIDA.lower()) != "true"]
    comprobar(len(originales) == 1, f"una sola emisión tuvo efecto: {estados}")
    comprobar(
        all(r.estado in (200, 409) for r in resultados), f"el resto 409 o repetida: {estados}"
    )
    ejecucion(corrida, o4)  # exactamente una
    corrida.ok(f"cuatro emisiones concurrentes (dos claves iguales): un solo efecto {estados}")

    # 5. Cambio de cuenta del proveedor con el abono confirmado (0053, P3 112): el abono deja de
    # regir hasta que otro ser humano lo confirme sobre la versión nueva.
    cuenta_nueva = cbu_sintetico(int(corrida.id, 16) % 10**9 + 7)
    corrida.cuentas.append(cuenta_nueva)
    r = adm.pedir("POST", corrida.c(f"/proveedores/{p1}/versiones"),
                  {"razon_social": f"Proveedor LAB {corrida.id}-1 SINTETICO (NO REAL)",
                   "rubro": "Mantenimiento SINTETICO", "cuenta_destino": cuenta_nueva,
                   "version_esperada": 1, "estado": "activo", "conservar_cuenta": False},
                  csrf=True, clave=str(uuid4()))  # fmt: skip
    comprobar(r.estado == 201, f"cambio de cuenta del proveedor: {r.texto}")
    [antes] = [x for x in corrida.n(LECTOR_REVISION).pedir(
        "GET", corrida.c("/contratos/revision")).cuerpo["elementos"] if x["id"] == k]  # fmt: skip
    comprobar((antes["estado"], antes["proveedor_version"]) == ("pendiente_reconfirmacion", 2),
              f"el abono queda pendiente de reconfirmar: {antes}")  # fmt: skip
    r = adm.pedir("POST", corrida.c("/ordenes-pago"),
                  {"proveedor_id": p1, "importe_centavos": 1_000_000, "moneda": "ARS",
                   "concepto": f"SINTETICO {corrida.id}: abono sin reconfirmar (NO REAL)",
                   "comprobante": f"Factura TEST-{corrida.id}-C", "usa_fondo_reserva": False,
                   "contrato_id": k}, csrf=True, clave=str(uuid4()))  # fmt: skip
    comprobar(r.estado == 422 and r.codigo() == "contrato_no_aplica",
              f"sin reconfirmar no respalda una orden: {r.texto}")  # fmt: skip
    consejo1 = corrida.n(LECTOR_REVISION)
    r = consejo1.pedir("POST", corrida.c(f"/contratos/{k}/confirmacion"),
                       {"huella": antes["huella"], "proveedor_version": 1}, csrf=True,
                       clave=str(uuid4()))  # fmt: skip
    comprobar(r.estado == 409 and r.codigo() == "proveedor_cambiado",
              f"la versión vieja del proveedor se niega: {r.texto}")  # fmt: skip
    r = consejo1.pedir("POST", corrida.c(f"/contratos/{k}/confirmacion"),
                       {"huella": antes["huella"], "proveedor_version": 2}, csrf=True,
                       clave=str(uuid4()))  # fmt: skip
    comprobar(r.estado == 201, f"el Consejo reconfirma sobre la versión nueva: {r.texto}")
    [despues] = [x for x in adm.pedir("GET", corrida.c("/contratos")).cuerpo["elementos"]
                 if x["id"] == k]  # fmt: skip
    comprobar(despues["estado"] == "vigente", f"el abono vuelve a regir: {despues}")
    corrida.ok(
        "cambio de cuenta: abono pendiente, 422 al usarlo, 409 con la versión vieja, "
        "reconfirmado por el Consejo sobre la nueva"
    )


def comprobar_fin(corrida: Corrida, inicio) -> None:
    with corrida.tx() as conn:
        textos = conn.execute(
            text("select coalesce(antes::text, '') || coalesce(despues::text, '')"
                 " from auditoria.eventos where consorcio_id = :c and creado_en >= :i"),
            {"c": corrida.consorcio, "i": inicio},
        ).scalars().all()  # fmt: skip
        muertos = conn.execute(
            text("select count(*) from plataforma.jobs where consorcio_id = :c"
                 " and estado = 'muerto' and creado_en >= :i"),
            {"c": corrida.consorcio, "i": inicio},
        ).scalar_one()  # fmt: skip
    comprobar(len(textos) > 0, "control: hay eventos de la corrida")
    con_cuenta = [t for t in textos if any(c in t for c in corrida.cuentas)]
    comprobar(not con_cuenta, f"{len(con_cuenta)} eventos llevan una cuenta")
    comprobar(muertos == 0, f"{muertos} trabajos muertos del consorcio")
    corrida.ok(f"{len(textos)} eventos de la corrida sin ninguna cuenta; 0 trabajos muertos")


def cerrar(corrida: Corrida) -> int:
    with corrida.tx() as conn:
        for m in corrida.mandatos.values():
            conn.execute(
                text("select nucleo.baja_mandato(:m, 'renuncia', null, :r, 'Fin del ensayo de"
                     " egresos', null, 'soporte')"), {"m": m, "r": REFERENCIA},
            )  # fmt: skip
        for persona in corrida.personas.values():
            conn.execute(text("select nucleo.revocar_sesiones_de(:p)"), {"p": persona})
    abiertas = 0
    for persona in corrida.personas.values():
        with transaccion(corrida.motor, persona_id=persona) as conn:
            abiertas += conn.execute(
                text("select count(*) from nucleo.sesion where persona_id = :p"
                     " and revocada_en is null and vence_en > now()"), {"p": persona},
            ).scalar_one()  # fmt: skip
    return abiertas


def main(argumentos: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--consorcio", type=UUID, required=True)
    p.add_argument("--base", required=True, help="host de cem-postgres-staging")
    p.add_argument("--url", required=True)
    p.add_argument("--host", required=True)
    p.add_argument("--sin-verificar-certificado", action="store_true")
    a = p.parse_args(argumentos)
    config = cargar()
    try:
        exigir_staging(config.origen_publico, config.database_url, a.base, a.host)
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
    pagos.configurar_riel(corrida.riel)
    try:
        creado = asegurar_consorcio(motor, a.consorcio)
        print(
            f"corrida {corrida.id}; consorcio {a.consorcio} ({'creado' if creado else 'existente'})"
        )
        with corrida.tx() as conn:
            inicio = conn.execute(text("select clock_timestamp()")).scalar_one()
        corrida.inicio = inicio
        error = None
        try:
            preparar_escena(corrida)
            recorrer_ordenes(corrida)
            comprobar_fin(corrida, inicio)
        except Fallo as e:
            error = e
            print(f"FALLÓ: {e}", file=sys.stderr, flush=True)
        finally:
            abiertas = cerrar(corrida)
            print(
                json.dumps(
                    {
                        "corrida": corrida.id,
                        "consorcio": str(a.consorcio),
                        "riel_simulado": {
                            "envios": corrida.riel.envios,
                            "pagos": len(corrida.riel.pagos),
                            "consultas": corrida.riel.consultas,
                        },
                        "sesiones_abiertas_al_cerrar": abiertas,
                        "controles": corrida.controles,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        if error is not None or abiertas != 0:
            return 1
        return 0
    except Negado as e:
        print(f"negado: {e}", file=sys.stderr)
        return 3
    finally:
        pagos.configurar_riel(None)
        motor.dispose()


if __name__ == "__main__":
    sys.exit(main())
