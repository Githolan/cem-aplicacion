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

**Pago manual** (`--pago-manual --otro-consorcio <uuid>`, `0055`, propietario del 31-10-2026): en
otro consorcio LAB (`NOMBRE_CONSORCIO_MANUAL`), sin el riel habilitado, recorre la administración
que prepara, el Consejo que aprueba, la misma administración que emite y recibe la cuenta completa
solo en el acto de ejecución, el registro con un comprobante sintético que analiza el antivirus real
y la conciliación de otro consejero; con los negativos (evidencia inválida, registro repetido o a
la vez, conciliación por la misma persona con otra cuenta vinculada por soporte, dos conciliaciones
a la vez, número y comprobante de otra orden, orden emitida modificada, cuenta cambiada, contrato
de baja, «no se pagó» confirmado por otro ser humano) y las rutas de `--otro-consorcio`. El riel de
este modo no se usa: el consorcio no lo tiene habilitado y la emisión no encola nada.

**Liquidación neutral** (`--liquidacion --otro-consorcio <uuid> --orden-ajena <uuid>`, `0057`,
propietario del 31-10-2026): en otro consorcio LAB (`NOMBRE_CONSORCIO_LIQUIDACION`), dos órdenes
pagadas a mano y conciliadas y una sin pagar; un borrador por período; agregar, simular (suma
exacta, cada unidad a menos de un centavo de su parte, repetible) y quitar (la baja queda en la
historia); orden sin pagar, de otro consorcio e inexistente; la misma orden y dos borradores a la
vez (dos hilos de la administración); un esquema nuevo dentro del período que bloquea la
simulación; y las rutas de `--otro-consorcio`. No publica ni genera cargos.

**Recorrido completo** (`--completo --otro-consorcio <uuid>`, propietario del 03-11-2026): en otro
consorcio LAB (`NOMBRE_CONSORCIO_COMPLETO`), el pago manual entero y después, en el mismo consorcio:
quien preparó que intenta aprobar, también desde su otra cuenta del Consejo; dos emisiones a la vez;
quien aprobó que intenta ejecutar; un archivo inválido; quien ejecutó que intenta conciliar, también
desde su otra cuenta; el comprobante oculto a la otra cuenta del Consejo de quien preparó y visible
para quien lo subió y para el Consejo que no preparó (solo la administración tiene
`egresos.ejecutar_pago`, `0011`: quien prepara es también quien ejecuta y sube el comprobante); la
liquidación del período con las dos órdenes pagadas y su simulación exacta; una unidad nueva que
deja la simulación bloqueada sin repartir (falla cerrada); el propietario que no ve comprobantes;
las rutas de documentos de otro consorcio; las acciones del recorrido, quién registró y quién
concilió en la auditoría, y su cadena. Deja vigente el mandato de consejo1 y devuelve en el resumen
(`exportacion`) lo que necesita `ensayo_exportacion_staging.py`, que corre en
`cem-trabajos-staging`, arma la exportación formal, comprueba los comprobantes y da de baja ese
mandato. La aprobación vencida no entra: vence a los 7 días y el ensayo no toca el reloj (SIN
VERIFICAR en staging; la cubren las pruebas).

**Orden trabada** (`--orden-trabada`, `0067`, P3 179; opción A del propietario, 03-11-2026): en
otro consorcio LAB (`NOMBRE_CONSORCIO_TRABADA`), la administración emite, ve la cuenta y renuncia
antes de registrar (la baja revoca sus sesiones). Vuelve a entrar con su contraseña y el segundo
factor, ve la orden en «pagos para aclarar» sin la cuenta y declara que no transfirió, sin
`egresos.ejecutar_pago`; quien no vio la cuenta recibe 404; otro consejero confirma y la orden
queda Fallida. El pago fuera de término (P3 180) no entra: pide una aprobación de más de 7 días y
el ensayo no toca el reloj (SIN VERIFICAR en staging; lo cubren las pruebas).

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
import re
import secrets
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
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
from app.plataforma import cola, copia_continua
from app.plataforma.idempotencia import ENCABEZADO, ENCABEZADO_REPETIDA

NOMBRE_CONSORCIO = "CEM LAB — egresos sintéticos (SINTETICO, PRUEBA, NO REAL)"
NOMBRE_CONSORCIO_MANUAL = "CEM LAB — pago manual sintético (SINTETICO, PRUEBA, NO REAL)"
NOMBRE_CONSORCIO_LIQUIDACION = "CEM LAB — liquidación sintética (SINTETICO, PRUEBA, NO REAL)"
NOMBRE_CONSORCIO_COMPLETO = "CEM LAB — recorrido completo sintético (SINTETICO, PRUEBA, NO REAL)"
NOMBRE_CONSORCIO_TRABADA = "CEM LAB — orden trabada sintética (SINTETICO, PRUEBA, NO REAL)"
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


def asegurar_consorcio(motor: Engine, consorcio: UUID, nombre_esperado: str) -> bool:
    with transaccion(motor, persona_id=None, consorcio_id=consorcio) as conn:
        nombre = conn.execute(
            text("select nombre from nucleo.consorcio where id = :c"), {"c": consorcio}
        ).scalar_one_or_none()
        if nombre is not None:
            if nombre != nombre_esperado:
                raise Negado(f"el consorcio {consorcio} se llama {nombre!r}: no es el del ensayo")
            return False
        conn.execute(
            text("insert into nucleo.consorcio (id, nombre, zona_horaria) values (:c, :n, :z)"),
            {"c": consorcio, "n": nombre_esperado, "z": ZONA},
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


def id_de_corrida() -> str:
    """Ocho hexadecimales sin cinco dígitos seguidos: en un texto libre («LAB <id>-<n>») nunca
    suman los ocho que el contrato niega como un teléfono (P3 153, corrida `43739007`)."""
    while True:
        candidato = uuid4().hex[:8]
        if re.search(r"[0-9]{5}", candidato) is None:
            return candidato


@dataclass
class Corrida:
    motor: Engine
    consorcio: UUID
    origen_publico: str
    url: str
    host: str
    contexto: ssl.SSLContext | None
    id: str = field(default_factory=lambda: id_de_corrida())
    personas: dict[str, UUID] = field(default_factory=dict)
    mandatos: dict[str, UUID] = field(default_factory=dict)
    navegadores: dict[str, Navegador] = field(default_factory=dict)
    secretos_totp: dict[str, bytes] = field(default_factory=dict)
    pasos_totp: dict[str, int] = field(default_factory=dict)
    cuentas: list[str] = field(default_factory=list)
    controles: list[str] = field(default_factory=list)
    riel: RielSimulado = field(default_factory=RielSimulado)
    huellas: dict[str, bytes] = field(default_factory=dict)
    """La huella sintética de la identidad de cada rol: con ella soporte vincula otra cuenta."""
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
    corrida.huellas[rol] = os.urandom(32)
    with corrida.tx() as conn:
        conn.execute(
            text("select nucleo.verificar_identidad_humana(:p, array[1]::smallint[],"
                 " array[:h]::bytea[], 'documento_presencial', :r, 'Ensayo de egresos')"),
            {"p": corrida.personas[rol], "h": corrida.huellas[rol],
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
    return documento_disponible(corrida, "contratos_proveedores",
                                f"Abono LAB {corrida.id} SINTETICO (NO REAL)")  # fmt: skip


def documento_disponible(corrida: Corrida, categoria: str, titulo: str) -> str:
    adm = corrida.n("administracion")
    r = adm.pedir("POST", corrida.c("/documentos"),
                  {"categoria": categoria, "titulo": titulo,
                   "fecha": time.strftime("%Y-%m-%d")}, csrf=True, clave=str(uuid4()))  # fmt: skip
    comprobar(r.estado == 201, f"crear el documento: {r.texto}")
    return con_archivo_disponible(corrida, r.cuerpo["id"], categoria)


def con_archivo_disponible(corrida: Corrida, doc: str, categoria: str,
                           rol: str = "administracion") -> str:  # fmt: skip
    adm = corrida.n(rol)
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
    corrida.ok(f"documento ({categoria}) subido por HTTP; el antivirus real lo deja disponible")
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
    de a una, así que la emisión de `o` deja exactamente un trabajo nuevo desde la corrida. Por
    eso dos corridas no pueden ir a la vez sobre el mismo consorcio: cada una vería los trabajos
    de la otra (revisión de #178). Cada corrida usa su propio `--consorcio` o espera a la otra."""
    with corrida.tx() as conn:
        filas = set(conn.execute(
            text("select carga->>'ejecucion' from plataforma.jobs where consorcio_id = :c"
                 " and tipo = :t and creado_en >= :i"),
            {"c": corrida.consorcio, "t": pagos.TRABAJO_ORDENAR, "i": corrida.inicio},
        ).scalars())  # fmt: skip
    nuevas = filas - corrida.ejecuciones
    # Sin el riel habilitado para el consorcio (`nucleo.riel_de_pago_habilitado`, `0055`), la
    # emisión crea una ejecución manual y no encola nada.
    comprobar(len(nuevas) == 1, f"una sola ejecución nueva de {o}: {len(nuevas)} (¿el consorcio"
              " tiene el riel habilitado?)")  # fmt: skip
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

    # 6. Cambio de cuenta del proveedor con el abono confirmado (0053, P3 112): el abono deja de
    # regir hasta que otro ser humano lo confirme sobre la versión nueva.
    cuenta_nueva = cbu_sintetico(int(corrida.id, 16) % 10**9 + 7)
    corrida.cuentas.append(cuenta_nueva)
    r = adm.pedir("POST", corrida.c(f"/proveedores/{p1}/versiones"),
                  {"razon_social": f"Proveedor LAB {corrida.id}-1 SINTETICO (NO REAL)",
                   "rubro": "Mantenimiento SINTETICO", "cuenta_destino": cuenta_nueva,
                   "version_esperada": 1, "estado": "activo", "conservar_cuenta": False},
                  csrf=True, clave=str(uuid4()))  # fmt: skip
    comprobar(r.estado == 200, f"cambio de cuenta del proveedor: {r.texto}")
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


# Pago manual (`0055`, modo `--pago-manual`) ---------------------------------------------------


def entra_otra_cuenta(corrida: Corrida, rol: str, de: str) -> None:
    """Otra cuenta del mismo ser humano que `de`: entra por su invitación y soporte la vincula a
    la identidad existente con la misma huella (`vincular_identidad_humana`), como haría al volver
    a ver el documento. Sintética: la huella es la que se sorteó para `de`."""
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
        mismo = conn.execute(
            text("select nucleo.vincular_identidad_humana(:p, array[1]::smallint[],"
                 " array[:h]::bytea[], 'documento_presencial', :r, 'Ensayo de pago manual')"),
            {"p": corrida.personas[rol], "h": corrida.huellas[de],
             "r": REFERENCIA + "; otra cuenta del mismo ser humano sintético"},
        ).scalar_one()  # fmt: skip
        de_quien = conn.execute(
            text("select identidad_id from nucleo.identidad_persona where consorcio_id = :c"
                 " and persona_id = :p and desvinculada_en is null"),
            {"c": corrida.consorcio, "p": corrida.personas[de]},
        ).scalar_one()  # fmt: skip
    comprobar(mismo == de_quien, f"{rol} y {de} son la misma identidad humana")


def reverificar(corrida: Corrida, rol: str) -> None:
    """El segundo factor otra vez (vale 10 minutos para lo económico), con un paso TOTP que la
    corrida no usó: si hace falta, espera al siguiente (a lo sumo 30 segundos)."""
    paso = max(segundo_factor_app.paso_actual(), corrida.pasos_totp[rol] + 1)
    while segundo_factor_app.paso_actual() < paso:
        time.sleep(1)
    codigo = segundo_factor_app.codigo_en(corrida.secretos_totp[rol], paso)
    r = corrida.n(rol).pedir("POST", "/api/v1/auth/totp/verificar", {"codigo": codigo}, csrf=True)
    comprobar(r.estado == 200, f"verificar de nuevo el TOTP de {rol}: {r.texto}")
    corrida.pasos_totp[rol] = paso


def al_dia(corrida: Corrida, *roles: str) -> None:
    for rol in roles:
        reverificar(corrida, rol)


def comprobante_disponible(corrida: Corrida, n: int, orden: str,
                           rol: str = "administracion") -> str:  # fmt: skip
    """Cargado desde el pago de `orden` por `rol`, que vio la cuenta (`0063`): nace con la orden
    y restringido, y la lista se la devuelve."""
    adm = corrida.n(rol)
    titulo = f"Transferencia LAB {corrida.id}-{n} SINTETICA (NO REAL)"
    r = adm.pedir("POST", corrida.c(f"/ordenes-pago/{orden}/pago-manual/comprobante"),
                  {"titulo": titulo, "fecha": time.strftime("%Y-%m-%d")}, csrf=True,
                  clave=str(uuid4()))  # fmt: skip
    comprobar(r.estado == 201, f"cargar el comprobante desde el pago: {r.texto}")
    doc = con_archivo_disponible(corrida, r.cuerpo["id"], "facturas_y_comprobantes", rol)
    lista = adm.pedir("GET", corrida.c("/documentos"))
    [d] = [x for x in lista.cuerpo["documentos"] if x["id"] == doc]
    comprobar(d["orden_pago_id"] == orden, f"la lista da la orden del comprobante: {d}")
    return doc


def cuenta_para_pagar(corrida: Corrida, rol: str, o: str) -> Respuesta:
    v = vigente(ficha(corrida, "administracion", o))
    return corrida.n(rol).pedir("POST", corrida.c(f"/ordenes-pago/{o}/pago-manual/cuenta"),
                                {"version": v["numero"], "huella": v["huella"]},
                                csrf=True)  # fmt: skip


def hoy_del_consorcio(corrida: Corrida) -> date:
    """La fecha en la zona del consorcio: el contenedor está en UTC, y de noche ya es mañana."""
    with corrida.tx() as conn:
        return conn.execute(
            text("select nucleo.hoy_del_consorcio(:c)"), {"c": corrida.consorcio}
        ).scalar_one()


def registrar_pago(corrida: Corrida, rol: str, o: str, **datos) -> Respuesta:
    v = vigente(ficha(corrida, "administracion", o))
    cuerpo = {"version": v["numero"], "huella": v["huella"], "moneda": "ARS",
              "fecha_operacion": hoy_del_consorcio(corrida).isoformat()} | datos  # fmt: skip
    return corrida.n(rol).pedir("POST", corrida.c(f"/ordenes-pago/{o}/pago-manual"), cuerpo,
                                csrf=True, clave=str(uuid4()))  # fmt: skip


def pagos_de(corrida: Corrida, rol: str, o: str, revision: bool = False) -> dict:
    ruta = "/pagos-manuales/revision" if revision else "/pagos-manuales"
    r = corrida.n(rol).pedir("GET", corrida.c(ruta))
    comprobar(r.estado == 200, f"lista de pagos manuales para {rol}: {r.texto}")
    comprobar(not any(c in r.texto for c in corrida.cuentas),
              f"la lista de pagos manuales no trae ninguna cuenta ({rol})")  # fmt: skip
    [elemento] = [x for x in r.cuerpo["elementos"] if x["orden"]["id"] == o]
    return elemento


def conciliar(corrida: Corrida, rol: str, o: str, pago: str, resultado: str,
              motivo: str | None = None, clave: str | None = None) -> Respuesta:  # fmt: skip
    cuerpo: dict = {"resultado": resultado}
    if motivo is not None:
        cuerpo["motivo"] = motivo
    return corrida.n(rol).pedir(
        "POST", corrida.c(f"/ordenes-pago/{o}/pago-manual/{pago}/conciliacion"), cuerpo,
        csrf=True, clave=clave or str(uuid4()),
    )  # fmt: skip


def sin_trabajos_del_riel(corrida: Corrida) -> None:
    """Una ejecución manual no encola el riel: ningún `egresos.ordenar_pago` en la corrida."""
    with corrida.tx() as conn:
        n = conn.execute(
            text("select count(*) from plataforma.jobs where consorcio_id = :c and tipo = :t"
                 " and creado_en >= :i"),
            {"c": corrida.consorcio, "t": pagos.TRABAJO_ORDENAR, "i": corrida.inicio},
        ).scalar_one()  # fmt: skip
    comprobar(n == 0, f"la emisión manual no encola el riel: {n} trabajos")


def en_paralelo(*pedidos) -> list[Respuesta]:
    resultados: list[Respuesta] = []
    hilos = [threading.Thread(target=lambda f=f: resultados.append(f())) for f in pedidos]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    return resultados


def recorrer_pago_manual(corrida: Corrida, otro_consorcio: UUID) -> str:
    """Devuelve la orden 1, pagada y conciliada (la usa `--completo`)."""
    adm = corrida.n("administracion")
    entra_otra_cuenta(corrida, "administracion_otra", "administracion")
    with corrida.tx() as conn:
        hoy = conn.execute(
            text("select nucleo.hoy_del_consorcio(:c)"), {"c": corrida.consorcio}
        ).scalar_one()
        decision = conn.execute(
            text("select nucleo.registrar_decision('asamblea', :h, :d, :r, null, 'soporte')"),
            {"h": hoy, "d": "SINTETICA: otra cuenta de la administración en el Consejo",
             "r": REFERENCIA},
        ).scalar_one()  # fmt: skip
        mandato = conn.execute(
            text("select nucleo.alta_mandato('miembro_consejo', :p, null, null, null, :d, :h,"
                 " null, null, null, 'soporte')"),
            {"p": corrida.personas["administracion_otra"], "d": decision, "h": hoy},
        ).scalar_one()  # fmt: skip
    corrida.mandatos["administracion_otra"] = mandato
    for rol in ("consejo2", "administracion_otra"):
        activar_totp(corrida, rol)
    corrida.ok("otra cuenta de la misma persona que la administración, en el Consejo, con TOTP")

    al_dia(corrida, "administracion", "consejo1")
    # 1. Proveedor nuevo y contrato: la administración prepara; el Consejo aprueba.
    p1 = proveedor(corrida, 1)
    cuenta = corrida.cuentas[0]
    o1 = preparar(corrida, p1, 2_500_000)
    r = cuenta_para_pagar(corrida, "administracion", o1)
    comprobar(r.estado == 403 and cuenta not in r.texto,
              f"en revisión no hay acto de ejecución ni cuenta: {r.texto}")  # fmt: skip
    comprobar(aprobar(corrida, "consejo1", o1).estado == 201, "el Consejo aprueba la orden 1")
    r = cuenta_para_pagar(corrida, "administracion", o1)
    comprobar(r.estado == 409 and cuenta not in r.texto,
              f"aprobada sin emitir: todavía no hay cuenta: {r.texto}")  # fmt: skip
    r = cuenta_para_pagar(corrida, "consejo1", o1)
    comprobar(r.estado == 403 and cuenta not in r.texto,
              f"quien aprobó no entra al acto de ejecución: {r.texto}")  # fmt: skip
    comprobar(emitir(corrida, "administracion", o1).estado == 200, "la administración emite")
    sin_trabajos_del_riel(corrida)
    comprobar(estado(corrida, o1) == "en_ejecucion", "emitida: lista para pagar")
    for ruta in (f"/ordenes-pago/{o1}", "/ordenes-pago", "/proveedores"):
        leido = adm.pedir("GET", corrida.c(ruta))
        comprobar(leido.estado == 200 and cuenta not in leido.texto,
                  f"la preparación sigue sin la cuenta completa en {ruta}")  # fmt: skip
    elemento = pagos_de(corrida, "administracion", o1)
    comprobar(elemento["ejecucion"]["pagos"] == [], "por pagar, sin registros")
    corrida.ok("preparar → aprobar → emitir manual: sin riel encolado y sin la cuenta completa")

    # 2. El acto de ejecución: la misma administración que preparó recibe la cuenta, solo acá.
    reverificar(corrida, "administracion")
    r = cuenta_para_pagar(corrida, "administracion", o1)
    comprobar(r.estado == 200 and r.cuerpo["cuenta_destino"] == cuenta,
              f"el acto de ejecución da la cuenta completa: {r.texto}")  # fmt: skip
    comprobar(r.cuerpo["importe_centavos"] == 2_500_000, "con el importe aprobado")
    corrida.ok("quien preparó, sin haber aprobado, recibe la cuenta completa solo en el acto")

    # 3. Evidencia inválida: nada se registra.
    doc1 = comprobante_disponible(corrida, 1, o1)
    for datos, esperado in (
        ({"importe_centavos": 2_500_001, "cuenta_destino": cuenta, "numero_operacion": "LAB0001",
          "documento_id": doc1}, (422, "no_coincide_con_la_orden")),
        ({"importe_centavos": 2_500_000, "cuenta_destino": cuenta, "numero_operacion": cuenta,
          "documento_id": doc1}, (422, "numero_operacion_invalido")),
        ({"importe_centavos": 2_500_000, "cuenta_destino": cuenta, "numero_operacion": "LAB0001",
          "documento_id": str(uuid4())}, (422, "comprobante_invalido")),
    ):  # fmt: skip
        r = registrar_pago(corrida, "administracion", o1, **datos)
        comprobar((r.estado, r.codigo()) == esperado, f"evidencia inválida {esperado}: {r.texto}")
    comprobar(estado(corrida, o1) == "en_ejecucion", "sigue lista para pagar")
    corrida.ok("importe distinto, número que es una cuenta y comprobante inexistente: rechazados")

    # 4. Registro con la transferencia sintética: queda pendiente de conciliación.
    numero = f"LAB{corrida.id}01"
    r = registrar_pago(
        corrida,
        "administracion",
        o1,
        importe_centavos=2_500_000,
        cuenta_destino=cuenta,
        numero_operacion=numero,
        documento_id=doc1,
    )
    comprobar(r.estado == 201, f"registrar el pago: {r.texto}")
    pago1 = r.cuerpo["id"]
    comprobar(estado(corrida, o1) == "en_verificacion", "registrado no es pagado")
    r = registrar_pago(
        corrida,
        "administracion",
        o1,
        importe_centavos=2_500_000,
        cuenta_destino=cuenta,
        numero_operacion=numero + "X",
        documento_id=doc1,
    )
    comprobar((r.estado, r.codigo()) == (409, "pago_ya_registrado"), f"no dos veces: {r.texto}")
    corrida.ok("pago registrado con comprobante que pasó el antivirus; pendiente de conciliación")

    # 5. Conciliación: ni quien ejecutó (sin la facultad), ni su otra cuenta del Consejo.
    r = conciliar(corrida, "administracion", o1, pago1, "conforme")
    comprobar(r.estado == 403, f"quien registró no concilia: {r.texto}")
    r = conciliar(corrida, "administracion_otra", o1, pago1, "conforme")
    comprobar(r.estado == 403, f"la misma persona con otra cuenta tampoco: {r.texto}")
    comprobar(estado(corrida, o1) == "en_verificacion", "sigue pendiente")
    corrida.ok("misma identidad con otra cuenta del Consejo: no concilia lo que registró (403)")

    # Quien prepara lee el motivo: la cuenta no va en él, ni partida en bloques (`0060`).
    if contrato_desplegado(corrida) >= (0, 36, 0):
        r = conciliar(corrida, "consejo2", o1, pago1, "observado",
                      f"SINTETICO: fue a {cuenta[:8]} {cuenta[8:]}")  # fmt: skip
        comprobar((r.estado, r.codigo()) == (422, "motivo_con_cuenta"),
                  f"motivo con la cuenta: {r.texto}")  # fmt: skip
        comprobar(estado(corrida, o1) == "en_verificacion", "sigue pendiente")
        corrida.ok("un motivo con la cuenta (en bloques) no se guarda: 422")
    else:
        corrida.ok("OMITIDO: motivo con la cuenta (el contrato desplegado es anterior a 0.36.0)")

    # 6. Observado → corrección con el mismo número → dos conciliaciones a la vez: una sola.
    r = conciliar(corrida, "consejo2", o1, pago1, "observado",
                  "SINTETICO: el comprobante es de otra fecha")  # fmt: skip
    comprobar(r.estado == 200 and r.cuerpo["estado"] == "en_verificacion",
              f"observado no paga: {r.texto}")  # fmt: skip
    reverificar(corrida, "administracion")
    r = registrar_pago(
        corrida,
        "administracion",
        o1,
        importe_centavos=2_500_000,
        cuenta_destino=cuenta,
        numero_operacion=numero,
        documento_id=doc1,
    )
    comprobar(r.estado == 201, f"la corrección usa el mismo número de operación: {r.texto}")
    pago2 = r.cuerpo["id"]
    reverificar(corrida, "consejo1")
    reverificar(corrida, "consejo2")
    resultados = en_paralelo(
        lambda: conciliar(corrida, "consejo1", o1, pago2, "conforme"),
        lambda: conciliar(corrida, "consejo2", o1, pago2, "conforme"),
    )
    estados = sorted(r.estado for r in resultados)
    comprobar(estados == [200, 409], f"dos conciliaciones a la vez: una sola gana {estados}")
    comprobar(estado(corrida, o1) == "pagada", "conciliada conforme: Pagada")
    final = pagos_de(corrida, "consejo1", o1, revision=True)
    comprobar(final["ejecucion"]["referencia"] == numero.upper(), f"referencia: {final}")
    comprobar([p["conciliacion"] for p in final["ejecucion"]["pagos"]] == ["observado", "conforme"],
              "la historia guarda el registro observado y el conforme")  # fmt: skip
    corrida.ok("observado, corregido con el mismo número y conciliado: Pagada (una conciliación)")

    al_dia(corrida, "administracion", "consejo1")
    # 7. Otra orden: el número y el comprobante de la orden 1 no la respaldan; dos registros a
    # la vez: uno solo.
    o2 = preparar(corrida, p1, 1_000_000)
    comprobar(estado(corrida, o2) == "aprobada", "bajo el umbral, conocido: aprobada por franja")
    comprobar(emitir(corrida, "administracion", o2).estado == 200, "emitir la orden 2")
    reverificar(corrida, "administracion")
    # Registra quien recibió la cuenta en el acto de esa ejecución (0055).
    r = cuenta_para_pagar(corrida, "administracion", o2)
    comprobar(r.estado == 200, f"el acto de ejecución de la orden 2: {r.texto}")
    r = registrar_pago(
        corrida,
        "administracion",
        o2,
        importe_centavos=1_000_000,
        cuenta_destino=cuenta,
        numero_operacion=numero,
        documento_id=doc1,
    )
    # Desde 0063 el de otra orden se niega antes de atarlo (el comprobante nace con su orden).
    comprobar((r.estado, r.codigo()) == (422, "comprobante_invalido"),
              f"el comprobante de la orden 1 no respalda la 2: {r.texto}")  # fmt: skip
    doc2, doc3 = comprobante_disponible(corrida, 2, o2), comprobante_disponible(corrida, 3, o2)
    r = registrar_pago(
        corrida,
        "administracion",
        o2,
        importe_centavos=1_000_000,
        cuenta_destino=cuenta,
        numero_operacion=numero,
        documento_id=doc2,
    )
    comprobar((r.estado, r.codigo()) == (409, "operacion_de_otra_orden"),
              f"el número de la orden 1 no paga la 2: {r.texto}")  # fmt: skip
    reverificar(corrida, "administracion")
    resultados = en_paralelo(
        lambda: registrar_pago(corrida, "administracion", o2, importe_centavos=1_000_000,
                               cuenta_destino=cuenta, numero_operacion=f"LAB{corrida.id}02",
                               documento_id=doc2),
        lambda: registrar_pago(corrida, "administracion", o2, importe_centavos=1_000_000,
                               cuenta_destino=cuenta, numero_operacion=f"LAB{corrida.id}03",
                               documento_id=doc3),
    )  # fmt: skip
    estados = sorted(r.estado for r in resultados)
    comprobar(estados == [201, 409], f"dos registros a la vez: uno solo {estados}")
    corrida.ok("número y comprobante atados a su orden; dos registros a la vez: uno solo")

    # 8. Orden modificada, cuenta cambiada y contrato de baja: no hay cuenta.
    al_dia(corrida, "administracion", "consejo1")
    r = adm.pedir("POST", corrida.c(f"/ordenes-pago/{o2}/versiones"),
                  {"proveedor_id": p1, "importe_centavos": 1_000_001, "moneda": "ARS",
                   "concepto": f"SINTETICO {corrida.id}: cambio tardío (NO REAL)",
                   "comprobante": None, "usa_fondo_reserva": False, "version_esperada": 1},
                  csrf=True, clave=str(uuid4()))  # fmt: skip
    comprobar(r.estado == 409, f"una orden emitida no se modifica: {r.texto}")
    p2 = proveedor(corrida, 2)
    o3 = preparar(corrida, p2, 2_000_000)
    comprobar(aprobar(corrida, "consejo1", o3).estado == 201, "el Consejo aprueba la orden 3")
    comprobar(emitir(corrida, "administracion", o3).estado == 200, "emitir la orden 3")
    cuenta_nueva = cbu_sintetico(int(corrida.id, 16) % 10**9 + 9)
    corrida.cuentas.append(cuenta_nueva)
    r = adm.pedir("POST", corrida.c(f"/proveedores/{p2}/versiones"),
                  {"razon_social": f"Proveedor LAB {corrida.id}-2 SINTETICO (NO REAL)",
                   "rubro": "Mantenimiento SINTETICO", "cuenta_destino": cuenta_nueva,
                   "version_esperada": 1, "estado": "activo", "conservar_cuenta": False},
                  csrf=True, clave=str(uuid4()))  # fmt: skip
    comprobar(r.estado == 200, f"cambio de cuenta del proveedor 2: {r.texto}")
    reverificar(corrida, "administracion")
    r = cuenta_para_pagar(corrida, "administracion", o3)
    comprobar((r.estado, r.codigo()) == (409, "proveedor_cambiado")
              and not any(c in r.texto for c in corrida.cuentas),
              f"cuenta cambiada: no hay cuenta hasta otra aprobación: {r.texto}")  # fmt: skip
    al_dia(corrida, "administracion", "consejo1")
    doc_abono = documento_disponible(corrida, "contratos_proveedores",
                                     f"Abono LAB {corrida.id} SINTETICO (NO REAL)")  # fmt: skip
    k = abono_confirmado(corrida, p1, doc_abono, tope=3_000_000)
    o4 = preparar(corrida, p1, 2_000_000, contrato=k)
    comprobar(estado(corrida, o4) == "aprobada", "rutinaria: aprobada por franja")
    comprobar(emitir(corrida, "administracion", o4).estado == 200, "emitir la rutinaria")
    r = adm.pedir("POST", corrida.c(f"/contratos/{k}/baja"), None, csrf=True, clave=str(uuid4()))
    comprobar(r.estado == 200, f"baja del abono: {r.texto}")
    reverificar(corrida, "administracion")
    r = cuenta_para_pagar(corrida, "administracion", o4)
    comprobar((r.estado, r.codigo()) == (409, "contrato_no_vigente") and cuenta not in r.texto,
              f"contrato de baja: no hay cuenta: {r.texto}")  # fmt: skip
    corrida.ok("orden emitida no se modifica; cuenta cambiada y contrato de baja: sin cuenta")

    # 9. «No se pagó»: lo declara quien ejecuta y lo confirma otro ser humano del Consejo.
    r = adm.pedir("POST", corrida.c(f"/ordenes-pago/{o4}/pago-manual/no-realizado"),
                  {"version": 1, "motivo": "SINTETICO: el abono se dio de baja antes de pagar"},
                  csrf=True, clave=str(uuid4()))  # fmt: skip
    comprobar(r.estado == 201, f"declarar que no se pagó: {r.texto}")
    declaracion = r.cuerpo["id"]
    ruta = f"/ordenes-pago/{o4}/pago-manual/no-realizado/{declaracion}/confirmacion"
    r = corrida.n("administracion_otra").pedir("POST", corrida.c(ruta), None, csrf=True,
                                               clave=str(uuid4()))  # fmt: skip
    comprobar(r.estado == 403, f"la otra cuenta de quien declaró no confirma: {r.texto}")
    reverificar(corrida, "consejo1")
    r = corrida.n("consejo1").pedir("POST", corrida.c(ruta), None, csrf=True, clave=str(uuid4()))
    comprobar(r.estado == 200 and estado(corrida, o4) == "fallida", f"confirmada: {r.texto}")
    corrida.ok("«no se pagó»: declarado por la administración, confirmado por otro ser humano")

    # 10. Otro consorcio: nada de este se lee ni se toca desde las rutas de otro.
    otro = f"/api/v1/consorcios/{otro_consorcio}"
    for rol, metodo, ruta, cuerpo in (
        ("administracion", "GET", "/pagos-manuales", None),
        ("administracion", "POST", f"/ordenes-pago/{o1}/pago-manual/cuenta",
         {"version": 1, "huella": "0" * 64}),
        ("consejo1", "GET", "/pagos-manuales/revision", None),
        ("consejo1", "POST", f"/ordenes-pago/{o1}/pago-manual/{pago2}/conciliacion",
         {"resultado": "conforme"}),
    ):  # fmt: skip
        r = corrida.n(rol).pedir(metodo, otro + ruta, cuerpo, csrf=metodo == "POST",
                                 clave=str(uuid4()) if metodo == "POST" else None)  # fmt: skip
        comprobar(r.estado == 404 and not any(c in r.texto for c in corrida.cuentas),
                  f"otro consorcio {metodo} {ruta}: {r.estado} {r.texto}")  # fmt: skip
    corrida.ok("rutas de otro consorcio: 404, sin ninguna cuenta")
    return o1


# Liquidación neutral (0057) ----------------------------------------------------------------


GRUPO = "expensas_comunes"
COEFICIENTES = ("50.000000", "33.333333", "16.666667")
"""Suman 99,999999 %, como una fuente real: el motor reparte el total exacto igual."""


def coeficientes_para(n: int) -> tuple[str, ...]:
    """Partes iguales con seis decimales; la última lleva el resto, así suman 100."""
    parte = (Decimal(100) / n).quantize(Decimal("0.000001"), rounding="ROUND_DOWN")
    return (*(f"{parte}",) * (n - 1), f"{Decimal(100) - parte * (n - 1)}")


def registrar_esquema(corrida: Corrida, desde, decision: UUID,
                      coeficientes: tuple[str, ...] = COEFICIENTES) -> UUID:  # fmt: skip
    """El esquema de `GRUPO` con las unidades activas del consorcio, cargado por soporte."""
    with corrida.tx() as conn:
        unidades = conn.execute(
            text("select id from nucleo.unidad where consorcio_id = :c and eliminado_en is null"
                 " order by designacion"), {"c": corrida.consorcio},
        ).scalars().all()  # fmt: skip
        comprobar(len(unidades) == len(coeficientes),
                  f"el consorcio de la corrida tiene {len(unidades)} unidades")  # fmt: skip
        coeficientes = dict(zip(map(str, unidades), coeficientes, strict=True))
        return conn.execute(
            text("select nucleo.registrar_esquema_coeficientes(:g, :f, :d, :dec,"
                 " cast(:co as jsonb), 'proporcional_a_coeficientes', 'restos_mayores', null,"
                 " 'soporte')"),
            {"g": GRUPO, "f": REFERENCIA + "; coeficientes sintéticos", "d": desde,
             "dec": decision, "co": json.dumps(coeficientes)},
        ).scalar_one()  # fmt: skip


def aprobada(corrida: Corrida, o: str, n: int) -> None:
    """La primera orden de un proveedor nuevo la aprueba el Consejo; las siguientes, bajo el
    umbral, quedan aprobadas por franja al enviarse a revisión."""
    if estado(corrida, o) != "aprobada":
        r = aprobar(corrida, "consejo1", o)
        comprobar(r.estado == 201, f"el Consejo aprueba la orden {n}: {r.texto}")
    comprobar(estado(corrida, o) == "aprobada", f"la orden {n} está aprobada")


def pagada_a_mano(corrida: Corrida, p: str, cuenta: str, importe: int, n: int) -> str:
    """Preparada y emitida por la administración, aprobada por consejo1, pagada a mano con su
    comprobante y conciliada por consejo2: `pagada`."""
    al_dia(corrida, "administracion", "consejo1", "consejo2")
    o = preparar(corrida, p, importe)
    aprobada(corrida, o, n)
    r = emitir(corrida, "administracion", o)
    comprobar(r.estado == 200, f"se emite la orden {n}: {r.texto}")
    r = cuenta_para_pagar(corrida, "administracion", o)
    comprobar(r.estado == 200 and r.cuerpo["cuenta_destino"] == cuenta,
              f"acto de ejecución de la orden {n}: {r.texto}")  # fmt: skip
    doc = comprobante_disponible(corrida, n, o)
    r = registrar_pago(corrida, "administracion", o, importe_centavos=importe,
                       cuenta_destino=cuenta, numero_operacion=f"LIQ{corrida.id}{n:02d}",
                       documento_id=doc)  # fmt: skip
    comprobar(r.estado == 201, f"registrar el pago de la orden {n}: {r.texto}")
    pago = r.cuerpo["id"]
    reverificar(corrida, "consejo2")
    r = conciliar(corrida, "consejo2", o, pago, "conforme")
    comprobar(r.estado == 200, f"consejo2 concilia la orden {n}: {r.texto}")
    comprobar(estado(corrida, o) == "pagada", f"la orden {n} queda Pagada al conciliar")
    return o


def detalle_liquidacion(corrida: Corrida, rol: str, liq: str) -> dict:
    r = corrida.n(rol).pedir("GET", corrida.c(f"/liquidaciones/{liq}"))
    comprobar(r.estado == 200, f"detalle de la liquidación: {r.texto}")
    comprobar(not any(c in r.texto for c in corrida.cuentas), "el detalle no trae ninguna cuenta")
    return r.cuerpo


def agregar_gasto(corrida: Corrida, rol: str, liq: str, o: str) -> Respuesta:
    return corrida.n(rol).pedir("POST", corrida.c(f"/liquidaciones/{liq}/gastos"),
                                {"orden_id": o, "grupo": GRUPO}, csrf=True,
                                clave=str(uuid4()))  # fmt: skip


def quitar_gasto(corrida: Corrida, rol: str, liq: str, gasto: str) -> Respuesta:
    return corrida.n(rol).pedir("POST", corrida.c(f"/liquidaciones/{liq}/gastos/{gasto}/quitar"),
                                {}, csrf=True, clave=str(uuid4()))  # fmt: skip


def descartar_liquidacion(corrida: Corrida, rol: str, liq: str, motivo: str) -> Respuesta:
    return corrida.n(rol).pedir("POST", corrida.c(f"/liquidaciones/{liq}/descartar"),
                                {"motivo": motivo}, csrf=True, clave=str(uuid4()))  # fmt: skip


def liquidaciones_por_id(corrida: Corrida) -> dict[str, dict]:
    r = corrida.n("administracion").pedir("GET", corrida.c("/liquidaciones"))
    comprobar(r.estado == 200, f"lista de liquidaciones: {r.texto}")
    return {x["id"]: x for x in r.cuerpo["elementos"]}


def contrato_desplegado(corrida: Corrida) -> tuple[int, ...]:
    r = corrida.n("administracion").pedir("GET", "/health")
    comprobar(r.estado == 200 and "version_contrato" in r.cuerpo, f"/health: {r.texto}")
    return tuple(int(x) for x in r.cuerpo["version_contrato"].split("."))


def preparar_liquidacion(corrida: Corrida, rol: str, periodo: str) -> Respuesta:
    return corrida.n(rol).pedir("POST", corrida.c("/liquidaciones"), {"periodo": periodo},
                                csrf=True, clave=str(uuid4()))  # fmt: skip


def reparto_exacto(simulacion: dict, coeficientes: tuple[str, ...] = COEFICIENTES) -> None:
    """La suma por unidad es el total, y cada unidad queda a menos de un centavo de su parte
    exacta (restos mayores)."""
    comprobar(simulacion["bloqueo"] is None, f"la simulación reparte: {simulacion['bloqueo']}")
    total = simulacion["total_centavos"]
    unidades = simulacion["unidades"]
    comprobar(sum(u["total_centavos"] for u in unidades) == total,
              f"la suma por unidad es el total {total}: {unidades}")  # fmt: skip
    suma = sum(Decimal(x) for x in coeficientes)
    partes = sorted(Decimal(total) * Decimal(x) / suma for x in coeficientes)
    recibidos = sorted(Decimal(u["total_centavos"]) for u in unidades)
    comprobar(all(abs(r - p) < 1 for r, p in zip(recibidos, partes, strict=True)),
              f"cada unidad a menos de un centavo: {recibidos} vs {partes}")  # fmt: skip


def recorrer_liquidacion(corrida: Corrida, otro_consorcio: UUID, orden_ajena: UUID) -> None:
    adm = corrida.n("administracion")
    with corrida.tx() as conn:
        hoy = conn.execute(
            text("select nucleo.hoy_del_consorcio(:c)"), {"c": corrida.consorcio}
        ).scalar_one()
        decision = conn.execute(
            text("select nucleo.registrar_decision('asamblea', :h, :d, :r, null, 'soporte')"),
            {"h": hoy, "d": "SINTETICA: coeficientes del ensayo de liquidación",
             "r": REFERENCIA},
        ).scalar_one()  # fmt: skip
    activar_totp(corrida, "consejo2")
    periodo = hoy.replace(day=1)
    # Desde el día anterior al período: así el esquema nuevo del paso 6 cabe aunque hoy sea el 1.
    esquema_1 = registrar_esquema(corrida, periodo - timedelta(days=1), decision)
    corrida.ok("esquema sintético vigente desde antes del período; TOTP de consejo2")

    # 1. Órdenes pagadas a mano y conciliadas, y una que no está pagada.
    p = proveedor(corrida, 1)
    cuenta = corrida.cuentas[0]
    o1 = pagada_a_mano(corrida, p, cuenta, 2_500_000, 1)
    o2 = pagada_a_mano(corrida, p, cuenta, 1_000_001, 2)
    al_dia(corrida, "administracion", "consejo1")
    o3 = preparar(corrida, p, 700_000)
    aprobada(corrida, o3, 3)
    corrida.ok("dos órdenes pagadas a mano y conciliadas; una tercera aprobada sin pagar")

    # 2. Un borrador por período; quien no tiene la facultad no prepara ni lee.
    al_dia(corrida, "administracion")
    r = preparar_liquidacion(corrida, "administracion", periodo.isoformat())
    comprobar(r.estado == 201, f"abrir el borrador del período: {r.texto}")
    liq = r.cuerpo["id"]
    r = preparar_liquidacion(corrida, "administracion", periodo.isoformat())
    comprobar((r.estado, r.codigo()) == (409, "liquidacion_no_admite"),
              f"un solo borrador por período: {r.texto}")  # fmt: skip
    r = corrida.n("consejo1").pedir("GET", corrida.c("/liquidaciones"))
    comprobar((r.estado, r.codigo()) == (403, "sin_permiso"), f"el Consejo no lee: {r.texto}")
    r = preparar_liquidacion(corrida, "consejo1", periodo.isoformat())
    comprobar((r.estado, r.codigo()) == (403, "sin_permiso"), f"el Consejo no prepara: {r.texto}")
    liquidables = adm.pedir("GET", corrida.c("/liquidaciones/ordenes-liquidables"))
    comprobar(liquidables.estado == 200, f"liquidables: {liquidables.texto}")
    ids = {x["orden_id"] for x in liquidables.cuerpo["elementos"]}
    comprobar(ids == {o1, o2}, f"liquidables: solo las dos pagadas, no la 3: {ids}")
    if contrato_desplegado(corrida) >= (0, 35, 0):
        # `0059`: la fecha del pago es la de la operación (hoy, en el calendario del consorcio).
        fechas = {x["pagada_el"] for x in liquidables.cuerpo["elementos"]}
        comprobar(fechas == {hoy_del_consorcio(corrida).isoformat()},
                  f"liquidables: pagadas el día de la operación: {fechas}")  # fmt: skip
    corrida.ok("un borrador por período; el Consejo sin liquidacion.preparar: 403;"
               " liquidables = pagadas")  # fmt: skip

    # 3. Negativos al agregar: orden sin pagar, de otro consorcio, inexistente.
    r = agregar_gasto(corrida, "administracion", liq, o3)
    comprobar((r.estado, r.codigo()) == (409, "orden_no_liquidable"), f"sin pagar: {r.texto}")
    for ajena in (orden_ajena, uuid4()):
        r = agregar_gasto(corrida, "administracion", liq, str(ajena))
        comprobar((r.estado, r.codigo()) == (404, "no_encontrado"),
                  f"orden de otro consorcio o inexistente: {r.texto}")  # fmt: skip
    corrida.ok("orden sin pagar 409; orden de otro consorcio e inexistente 404")

    # 4. Agregar y simular: suma exacta y repetible.
    for o in (o1, o2):
        r = agregar_gasto(corrida, "administracion", liq, o)
        comprobar(r.estado == 201, f"agregar la orden pagada: {r.texto}")
    detalle = detalle_liquidacion(corrida, "administracion", liq)
    comprobar({g["esquema_id"] for g in detalle["gastos"]} == {str(esquema_1)},
              "los gastos guardan el esquema vigente del período")  # fmt: skip
    simulacion = detalle["simulacion"]
    comprobar(simulacion["total_centavos"] == 3_500_001 and simulacion["moneda"] == "ARS",
              f"total y moneda: {simulacion}")  # fmt: skip
    reparto_exacto(simulacion)
    otra = detalle_liquidacion(corrida, "administracion", liq)["simulacion"]
    comprobar(otra["unidades"] == simulacion["unidades"], "el reparto se repite igual")
    corrida.ok("simulación de 35.000,01 ARS: suma exacta, a menos de un centavo, repetible")

    # 5. Quitar deja la historia; la misma orden y dos borradores a la vez.
    gasto_2 = next(g["id"] for g in detalle["gastos"] if g["orden_id"] == o2)
    comprobar(quitar_gasto(corrida, "administracion", liq, gasto_2).estado == 200, "quitar")
    r = quitar_gasto(corrida, "administracion", liq, gasto_2)
    comprobar((r.estado, r.codigo()) == (409, "liquidacion_no_admite"),
              f"no se quita dos veces: {r.texto}")  # fmt: skip
    detalle = detalle_liquidacion(corrida, "administracion", liq)
    comprobar([g["orden_id"] for g in detalle["gastos"]] == [o1], "queda solo la orden 1")
    with corrida.tx() as conn:
        acciones = conn.execute(
            text("select accion from auditoria.eventos where consorcio_id = :c"
                 " and creado_en >= :i and accion like 'consorcio.liquidacion.%'"),
            {"c": corrida.consorcio, "i": corrida.inicio},
        ).scalars().all()  # fmt: skip
    comprobar(acciones.count("consorcio.liquidacion.quitar_gasto") == 1
              and acciones.count("consorcio.liquidacion.agregar_gasto") == 2,
              f"la baja queda en la historia: {acciones}")  # fmt: skip
    # A la vez: dos hilos con la sesión de la administración, la única que tiene la facultad (un
    # solo mandato de administración por fechas). `Navegador` solo serializa las cookies.
    al_dia(corrida, "administracion")
    resultados = en_paralelo(
        lambda: agregar_gasto(corrida, "administracion", liq, o2),
        lambda: agregar_gasto(corrida, "administracion", liq, o2),
    )
    comprobar(sorted((r.estado, r.codigo() or "") for r in resultados)
              == [(201, ""), (409, "orden_no_liquidable")],
              f"la misma orden a la vez: una sola {[r.texto for r in resultados]}")  # fmt: skip
    siguiente = (periodo + timedelta(days=32)).replace(day=1).isoformat()
    resultados = en_paralelo(
        lambda: preparar_liquidacion(corrida, "administracion", siguiente),
        lambda: preparar_liquidacion(corrida, "administracion", siguiente),
    )
    comprobar(sorted(r.estado for r in resultados) == [201, 409],
              f"dos borradores a la vez: uno solo {[r.texto for r in resultados]}")  # fmt: skip
    corrida.ok("quitar deja la baja en la historia; a la vez: una sola orden y un solo borrador")

    # 6. Esquema nuevo dentro del período: la simulación se bloquea hasta quitar y volver a
    # agregar.
    esquema_2 = registrar_esquema(corrida, hoy_del_consorcio(corrida), decision)
    detalle = detalle_liquidacion(corrida, "administracion", liq)
    bloqueo = detalle["simulacion"]["bloqueo"] or ""
    comprobar(GRUPO in bloqueo and detalle["simulacion"]["unidades"] == [],
              f"esquema nuevo: la simulación se bloquea: {detalle['simulacion']}")  # fmt: skip
    al_dia(corrida, "administracion")
    for g in detalle["gastos"]:
        comprobar(quitar_gasto(corrida, "administracion", liq, g["id"]).estado == 200,
                  "quitar para volver a agregar")  # fmt: skip
        comprobar(agregar_gasto(corrida, "administracion", liq, g["orden_id"]).estado == 201,
                  "volver a agregar con el esquema nuevo")  # fmt: skip
    detalle = detalle_liquidacion(corrida, "administracion", liq)
    comprobar({g["esquema_id"] for g in detalle["gastos"]} == {str(esquema_2)},
              "los gastos vueltos a agregar llevan el esquema nuevo")  # fmt: skip
    reparto_exacto(detalle["simulacion"])
    corrida.ok("esquema nuevo en el período: simulación bloqueada; quitar y agregar destraba")

    # 7. Otro consorcio: sus liquidaciones no se ven ni se tocan.
    otra_ruta = f"/api/v1/consorcios/{otro_consorcio}/liquidaciones"
    pedidos = (("GET", otra_ruta, None), ("GET", f"{otra_ruta}/{liq}", None),
               ("POST", otra_ruta, {"periodo": periodo.isoformat()}))  # fmt: skip
    for metodo, ruta, cuerpo in pedidos:
        r = adm.pedir(metodo, ruta, cuerpo, csrf=metodo == "POST",
                      clave=str(uuid4()) if cuerpo else None)  # fmt: skip
        comprobar(r.estado == 404, f"otro consorcio {metodo} {ruta}: {r.estado} {r.texto}")
    corrida.ok("rutas de liquidación de otro consorcio: 404")

    # 8. `0058` (contrato 0.34.0): descartar libera el período y sus gastos sin borrar la
    # historia, y una orden pagada después del último día de un período no entra en él.
    if contrato_desplegado(corrida) < (0, 34, 0):
        corrida.ok("SIN VERIFICAR con esta imagen: descartar y la fecha de pago llegan con 0058")
        return
    al_dia(corrida, "administracion")
    r = descartar_liquidacion(corrida, "administracion", liq, " ")
    comprobar(r.estado == 422, f"descartar sin motivo: {r.texto}")
    r = descartar_liquidacion(corrida, "administracion", liq, "SINTETICO: ensayo de descarte")
    comprobar(r.estado == 200, f"descartar el borrador: {r.texto}")
    r = descartar_liquidacion(corrida, "administracion", liq, "SINTETICO: otra vez")
    comprobar((r.estado, r.codigo()) == (409, "liquidacion_no_admite"),
              f"se descarta una sola vez: {r.texto}")  # fmt: skip
    comprobar(liquidaciones_por_id(corrida)[liq]["estado"] == "descartada", "queda descartada")
    liquidables = adm.pedir("GET", corrida.c("/liquidaciones/ordenes-liquidables"))
    ids = {x["orden_id"] for x in liquidables.cuerpo["elementos"]}
    comprobar(ids == {o1, o2}, f"descartar devuelve sus órdenes: {ids}")
    anterior = (periodo - timedelta(days=1)).replace(day=1).isoformat()
    r = preparar_liquidacion(corrida, "administracion", anterior)
    comprobar(r.estado == 201, f"borrador del período anterior: {r.texto}")
    r = agregar_gasto(corrida, "administracion", r.cuerpo["id"], o1)
    comprobar((r.estado, r.codigo()) == (409, "orden_no_liquidable")
              and "período posterior" in r.cuerpo["mensaje"],
              f"pagada después del período: no entra {r.texto}")  # fmt: skip
    r = preparar_liquidacion(corrida, "administracion", periodo.isoformat())
    comprobar(r.estado == 201, f"otro borrador del período descartado: {r.texto}")
    nuevo = r.cuerpo["id"]
    comprobar(liquidaciones_por_id(corrida)[nuevo]["version"] == 2, "es la versión 2")
    comprobar(agregar_gasto(corrida, "administracion", nuevo, o1).estado == 201,
              "la orden liberada entra en el borrador nuevo")  # fmt: skip
    with corrida.tx() as conn:
        descartes = conn.execute(
            text("select count(*) from auditoria.eventos where consorcio_id = :c"
                 " and accion = 'consorcio.liquidacion.descartar' and entidad_id = :l"),
            {"c": corrida.consorcio, "l": liq},
        ).scalar_one()  # fmt: skip
    comprobar(descartes == 1, f"el descarte queda en la auditoría: {descartes}")
    abiertos = [i for i, x in liquidaciones_por_id(corrida).items() if x["estado"] == "borrador"]
    for i in abiertos:
        r = descartar_liquidacion(corrida, "administracion", i, "SINTETICO: fin del ensayo")
        comprobar(r.estado == 200, f"descartar los borradores del ensayo: {r.texto}")
    comprobar(len(abiertos) == 3, f"tres borradores abiertos al final: {abiertos}")
    corrida.ok("0058: descartar con motivo y una vez libera período y órdenes (versión 2);"
               " pagada después del período: 409")  # fmt: skip


# Recorrido completo (mandato del propietario del 03-11) ---------------------------------------


ACCIONES_DEL_RECORRIDO = (
    "consorcio.proveedor.alta", "consorcio.proveedor.modificar",
    "consorcio.contrato_proveedor.registrar", "consorcio.contrato_proveedor.confirmar",
    "consorcio.orden_pago.preparar", "consorcio.orden_pago.aprobar",
    "consorcio.orden_pago.emitir", "consorcio.orden_pago.ver_cuenta_para_pago",
    "consorcio.documento.crear", "consorcio.orden_pago.registrar_pago_manual",
    "consorcio.orden_pago.conciliar_pago_manual", "consorcio.liquidacion.agregar_gasto",
    "autorizacion.denegada",
)  # fmt: skip


def ultima_negacion(corrida: Corrida, accion: str) -> str | None:
    with corrida.tx() as conn:
        return conn.execute(
            text("select despues->>'motivo' from auditoria.eventos where consorcio_id = :c"
                 " and accion = 'autorizacion.denegada' and despues->>'accion' = :a"
                 " and creado_en >= :i order by secuencia desc limit 1"),
            {"c": corrida.consorcio, "a": accion, "i": corrida.inicio},
        ).scalar_one_or_none()  # fmt: skip


def ids_de_documentos(corrida: Corrida, rol: str) -> dict[str, str | None]:
    r = corrida.n(rol).pedir("GET", corrida.c("/documentos"))
    comprobar(r.estado == 200, f"documentos de {rol}: {r.texto}")
    return {d["id"]: d["orden_pago_id"] for d in r.cuerpo["documentos"]}


def recorrer_completo(corrida: Corrida, otro_consorcio: UUID) -> dict:
    """El recorrido del pago manual y, en el mismo consorcio, lo que le faltaba para ir de punta a
    punta: los negativos de separación que faltaban, la visibilidad de los comprobantes, la
    liquidación con su simulación y su falla cerrada, la auditoría y lo que necesita la
    exportación formal (`ensayo_exportacion_staging.py`, en `cem-trabajos`)."""
    o1 = recorrer_pago_manual(corrida, otro_consorcio)
    adm = corrida.n("administracion")
    al_dia(corrida, "administracion")
    control = documento_disponible(
        corrida,
        "facturas_y_comprobantes",
        f"Factura LAB {corrida.id} SINTETICA de control (NO REAL)",
    )

    # 11. Quien prepara no aprueba, tampoco con su otra cuenta del Consejo.
    al_dia(corrida, "administracion", "consejo1", "administracion_otra")
    p3 = proveedor(corrida, 3)
    cuenta_3 = corrida.cuentas[-1]
    o5 = preparar(corrida, p3, 1_200_000)
    comprobar(estado(corrida, o5) == "en_revision", "proveedor nuevo: espera al Consejo")
    r = aprobar(corrida, "administracion", o5)
    comprobar(r.estado == 403, f"quien preparó no aprueba: {r.texto}")
    r = aprobar(corrida, "administracion_otra", o5)
    comprobar(r.estado == 403, f"ni con otra cuenta del Consejo: {r.texto}")
    # El evaluador niega por la identidad humana y responde un 403 genérico: el motivo queda en
    # la auditoría.
    comprobar(ultima_negacion(corrida, "egresos.aprobar_orden") == "no_es_quien_preparo",
              "la negación es por haber preparado la orden")  # fmt: skip
    comprobar(estado(corrida, o5) == "en_revision", "sigue en revisión")
    comprobar(aprobar(corrida, "consejo1", o5).estado == 201, "consejo1 aprueba la orden 5")
    corrida.ok("quien preparó no aprueba (403), tampoco desde otra cuenta del Consejo")

    # 12. Dos emisiones a la vez: una sola.
    reverificar(corrida, "administracion")
    resultados = en_paralelo(
        lambda: emitir(corrida, "administracion", o5, str(uuid4())),
        lambda: emitir(corrida, "administracion", o5, str(uuid4())),
    )
    comprobar(sorted(r.estado for r in resultados) == [200, 409],
              f"dos emisiones a la vez: una sola {[r.texto for r in resultados]}")  # fmt: skip
    corrida.ok("dos emisiones manuales a la vez: una sola")

    # 13. Quien aprobó no entra al acto; ejecuta la administración (el único paquete con
    # `egresos.ejecutar_pago`, `0011`); quien ejecutó no concilia, tampoco con su otra cuenta.
    reverificar(corrida, "consejo1")
    r = cuenta_para_pagar(corrida, "consejo1", o5)
    comprobar(r.estado == 403 and cuenta_3 not in r.texto, f"quien aprobó no ejecuta: {r.texto}")
    reverificar(corrida, "administracion")
    r = cuenta_para_pagar(corrida, "administracion", o5)
    comprobar(r.estado == 200 and r.cuerpo["cuenta_destino"] == cuenta_3,
              f"la administración recibe la cuenta en el acto: {r.texto}")  # fmt: skip
    # Documento inválido: un tipo no admitido, y un PDF declarado que no lo es.
    for tipo in ("text/plain", "application/pdf"):
        r = adm.pedir("POST", corrida.c(f"/documentos/{control}/versiones"),
                      crudo=b"SINTETICO: no es un PDF (NO REAL)", tipo=tipo, csrf=True,
                      clave=str(uuid4()))  # fmt: skip
        comprobar((r.estado, r.codigo()) == (415, "tipo_no_admitido"),
                  f"archivo inválido declarado {tipo}: {r.texto}")  # fmt: skip
    doc5 = comprobante_disponible(corrida, 5, o5)
    r = registrar_pago(corrida, "administracion", o5, importe_centavos=1_200_000,
                       cuenta_destino=cuenta_3, numero_operacion=f"LAB{corrida.id}05",
                       documento_id=doc5)  # fmt: skip
    comprobar(r.estado == 201, f"la administración registra: {r.texto}")
    pago5 = r.cuerpo["id"]
    al_dia(corrida, "administracion", "administracion_otra")
    for rol in ("administracion", "administracion_otra"):
        r = conciliar(corrida, rol, o5, pago5, "conforme")
        comprobar(r.estado == 403, f"quien ejecutó no concilia ({rol}): {r.texto}")
    reverificar(corrida, "consejo1")
    r = conciliar(corrida, "consejo1", o5, pago5, "conforme")
    comprobar(r.estado == 200 and estado(corrida, o5) == "pagada",
              f"concilia quien aprobó, que no ejecutó: {r.texto}")  # fmt: skip
    corrida.ok("quien aprobó no ejecuta; quien ejecutó no concilia, tampoco con otra cuenta;"
               " un archivo inválido se niega (415)")  # fmt: skip

    # 14. El comprobante: lo ven quien lo subió (la administración, que también preparó: el
    # piloto no tiene otro ejecutor) y el Consejo que no preparó; no la otra cuenta del Consejo
    # de quien preparó. El control es una factura sin orden, que todos ven.
    for rol, lo_ve in (("administracion", True), ("consejo2", True),
                       ("administracion_otra", False)):  # fmt: skip
        ids = ids_de_documentos(corrida, rol)
        comprobar(control in ids, f"control: {rol} lee documentos")
        comprobar((doc5 in ids) == lo_ve, f"comprobante de la orden 5 para {rol}: {lo_ve}")
    corrida.ok("comprobante: lo ven quien lo subió y el Consejo que no preparó;"
               " no la otra cuenta del Consejo de quien preparó")  # fmt: skip

    # 15. Liquidación del período con las dos órdenes pagadas, y su simulación.
    with corrida.tx() as conn:
        hoy = conn.execute(
            text("select nucleo.hoy_del_consorcio(:c)"), {"c": corrida.consorcio}
        ).scalar_one()
        decision = conn.execute(
            text("select nucleo.registrar_decision('asamblea', :h, :d, :r, null, 'soporte')"),
            {"h": hoy, "d": "SINTETICA: coeficientes del recorrido completo", "r": REFERENCIA},
        ).scalar_one()  # fmt: skip
        n_unidades = conn.execute(
            text("select count(*) from nucleo.unidad where consorcio_id = :c"
                 " and eliminado_en is null"), {"c": corrida.consorcio},
        ).scalar_one()  # fmt: skip
    coeficientes = coeficientes_para(n_unidades)
    periodo = hoy.replace(day=1)
    registrar_esquema(corrida, periodo - timedelta(days=1), decision, coeficientes)
    al_dia(corrida, "administracion")
    r = preparar_liquidacion(corrida, "administracion", periodo.isoformat())
    comprobar(r.estado == 201, f"borrador del período: {r.texto}")
    liq = r.cuerpo["id"]
    liquidables = adm.pedir("GET", corrida.c("/liquidaciones/ordenes-liquidables"))
    ids = {x["orden_id"] for x in liquidables.cuerpo["elementos"]}
    comprobar(ids == {o1, o5}, f"liquidables: las dos pagadas: {ids}")
    for o in sorted(ids):
        comprobar(agregar_gasto(corrida, "administracion", liq, o).estado == 201, "agregar")
    simulacion = detalle_liquidacion(corrida, "administracion", liq)["simulacion"]
    comprobar(simulacion["total_centavos"] == 3_700_000, f"total: {simulacion}")
    reparto_exacto(simulacion, coeficientes)
    corrida.ok(f"liquidación con las dos órdenes pagadas: 37.000,00 ARS en {n_unidades}"
               " unidades, suma exacta")  # fmt: skip

    # 16. Una unidad nueva (entra un propietario): el esquema ya no describe el consorcio y la
    # simulación se niega en vez de repartir mal (falla cerrada, como P3 146).
    entra(corrida, "propietario")
    simulacion = detalle_liquidacion(corrida, "administracion", liq)["simulacion"]
    comprobar("no coincide con las unidades activas" in (simulacion["bloqueo"] or "")
              and simulacion["unidades"] == [],
              f"esquema que no describe el consorcio: no reparte {simulacion}")  # fmt: skip
    r = descartar_liquidacion(corrida, "administracion", liq, "SINTETICO: fin del recorrido")
    comprobar(r.estado == 200, f"descartar el borrador: {r.texto}")
    ids = ids_de_documentos(corrida, "propietario")
    comprobar(control in ids, "control: el propietario lee documentos")
    comprobar(not any(o is not None for o in ids.values()),
              f"el propietario no ve ningún comprobante: {ids}")  # fmt: skip
    corrida.ok("unidad nueva: la simulación se bloquea sin repartir; el propietario no ve"
               " comprobantes")  # fmt: skip

    # 17. Documentos de otro consorcio y auditoría del recorrido.
    otro = f"/api/v1/consorcios/{otro_consorcio}"
    for rol in ("consejo2", "administracion"):
        r = corrida.n(rol).pedir("GET", f"{otro}/documentos")
        comprobar(r.estado == 404 and doc5 not in r.texto, f"otro consorcio: {r.texto}")
    with corrida.tx() as conn:
        acciones = dict(conn.execute(
            text("select accion, count(*) from auditoria.eventos where consorcio_id = :c"
                 " and creado_en >= :i group by accion"),
            {"c": corrida.consorcio, "i": corrida.inicio},
        ).all())  # fmt: skip
        actores = dict(conn.execute(
            text("select accion, actor_persona_id from auditoria.eventos where consorcio_id = :c"
                 " and entidad_id = :o and accion in"
                 " ('consorcio.orden_pago.registrar_pago_manual',"
                 "  'consorcio.orden_pago.conciliar_pago_manual')"),
            {"c": corrida.consorcio, "o": o5},
        ).all())  # fmt: skip
        cadena = conn.execute(copia_continua.CADENA).one()
    faltan = [a for a in ACCIONES_DEL_RECORRIDO if not acciones.get(a)]
    comprobar(not faltan, f"acciones del recorrido en la auditoría: faltan {faltan}")
    esperados = {
        "consorcio.orden_pago.registrar_pago_manual": corrida.personas["administracion"],
        "consorcio.orden_pago.conciliar_pago_manual": corrida.personas["consejo1"],
    }
    comprobar(actores == esperados, f"quién registró y quién concilió: {actores}")
    total, rotos, distintos, control_cadena = cadena
    comprobar(total > 0 and rotos == 0 and distintos == 0 and control_cadena == total,
              f"cadena de auditoría: {cadena}")  # fmt: skip
    corrida.ok(f"auditoría: {len(ACCIONES_DEL_RECORRIDO)} acciones, quién registró y concilió;"
               f" cadena íntegra ({total} eventos); otro consorcio 404")  # fmt: skip

    comprobantes = sorted(d for d, o in ids_de_documentos(corrida, "consejo2").items() if o)
    return {"consorcio": str(corrida.consorcio), "solicitante": str(corrida.personas["consejo1"]),
            "mandato": str(corrida.mandatos["consejo1"]), "comprobantes": comprobantes,
            "control": control, "propietario": str(corrida.personas["propietario"]),
            "cuentas": corrida.cuentas}  # fmt: skip


# Orden trabada (`0067`, P3 179) ---------------------------------------------------------------


def recorrer_orden_trabada(corrida: Corrida) -> None:
    al_dia(corrida, "administracion", "consejo1")
    p1 = proveedor(corrida, 1)
    cuenta = corrida.cuentas[0]
    o = preparar(corrida, p1, 1_800_000)
    comprobar(aprobar(corrida, "consejo1", o).estado == 201, "el Consejo aprueba la orden")
    comprobar(emitir(corrida, "administracion", o).estado == 200, "la administración emite")
    reverificar(corrida, "administracion")
    r = cuenta_para_pagar(corrida, "administracion", o)
    comprobar(r.estado == 200 and r.cuerpo["cuenta_destino"] == cuenta,
              f"la administración ve la cuenta en el acto de ejecución: {r.texto}")  # fmt: skip
    adm = corrida.n("administracion")
    contrasena = secrets.token_urlsafe(24)  # solo en memoria, para volver a entrar
    r = adm.pedir("PUT", "/api/v1/auth/contrasena", {"contrasena_nueva": contrasena}, csrf=True)
    comprobar(r.estado == 200, f"la administración define su contraseña: {r.texto}")
    r = adm.pedir("GET", "/api/v1/pagos-por-aclarar")
    comprobar(r.estado == 200 and r.cuerpo["elementos"] == [],
              f"con la facultad, nada para aclarar: {r.texto}")  # fmt: skip
    corrida.ok("la administración vio la cuenta; con la facultad, no tiene pagos para aclarar")

    # La renuncia, antes de registrar: la baja revoca sus sesiones (RN22).
    mandato = corrida.mandatos.pop("administracion")
    with corrida.tx() as conn:
        conn.execute(
            text("select nucleo.baja_mandato(:m, 'renuncia', null, :r, 'Ensayo de orden trabada',"
                 " null, 'soporte')"), {"m": mandato, "r": REFERENCIA},
        )  # fmt: skip
    r = adm.pedir("GET", "/api/v1/pagos-por-aclarar")
    comprobar(r.estado == 401, f"la sesión de antes de la renuncia ya no sirve: {r.texto}")
    n = Navegador(corrida.url, corrida.host, corrida.contexto)
    r = n.pedir("POST", "/api/v1/auth/contrasena/entrar",
                {"correo": f"egresos-{corrida.id}-administracion@{DOMINIO}",
                 "contrasena": contrasena})  # fmt: skip
    comprobar(r.estado == 200 and r.cuerpo["estado"] == "falta_segundo_factor",
              f"vuelve a entrar con la contraseña: {r.texto}")  # fmt: skip
    paso = max(segundo_factor_app.paso_actual(), corrida.pasos_totp["administracion"] + 1)
    while segundo_factor_app.paso_actual() < paso:
        time.sleep(1)
    codigo = segundo_factor_app.codigo_en(corrida.secretos_totp["administracion"], paso)
    r = n.pedir("POST", "/api/v1/auth/contrasena/segundo-factor", {"codigo": codigo})
    comprobar(r.estado == 200, f"y con el segundo factor: {r.texto}")
    corrida.pasos_totp["administracion"] = paso
    corrida.navegadores["administracion"] = n
    corrida.ok("renunció: sus sesiones se revocaron y volvió a entrar con contraseña y TOTP")

    r = n.pedir("GET", "/api/v1/pagos-por-aclarar")
    comprobar(r.estado == 200 and cuenta not in r.texto, f"pagos para aclarar: {r.texto}")
    [fila] = [x for x in r.cuerpo["elementos"] if x["orden_id"] == o]
    comprobar(fila["consorcio_id"] == str(corrida.consorcio) and fila["version"] == 1,
              f"la orden, en su consorcio, sin la cuenta: {fila}")  # fmt: skip
    leido = n.pedir("GET", corrida.c(f"/ordenes-pago/{o}"))
    comprobar(leido.estado in (403, 404) and cuenta not in leido.texto,
              f"sin la facultad, ya no lee la orden: {leido.estado}")  # fmt: skip
    corrida.ok("sin la facultad, ve la orden en «pagos para aclarar», sin la cuenta, y nada más")

    ruta = corrida.c(f"/pagos-por-aclarar/{o}/no-realizado")
    declaracion = {"version": 1, "motivo": f"LAB {corrida.id}: renuncié antes de transferir"}
    reverificar(corrida, "consejo1")
    r = corrida.n("consejo1").pedir("POST", ruta, declaracion, csrf=True, clave=str(uuid4()))
    comprobar((r.estado, r.codigo()) == (404, "no_encontrado"),
              f"quien no vio la cuenta no declara (404): {r.texto}")  # fmt: skip
    r = n.pedir("POST", ruta, declaracion, csrf=True, clave=str(uuid4()))
    comprobar(r.estado == 201, f"quien vio la cuenta declara sin la facultad: {r.texto}")
    r = n.pedir("GET", "/api/v1/pagos-por-aclarar")
    comprobar(r.estado == 200 and all(x["orden_id"] != o for x in r.cuerpo["elementos"]),
              f"declarada, ya no está para aclarar: {r.texto}")  # fmt: skip
    corrida.ok("la declara quien vio la cuenta y renunció; quien no la vio recibe 404")

    elemento = pagos_de(corrida, "consejo1", o, revision=True)
    [d] = elemento["ejecucion"]["declaraciones"]
    comprobar(d["confirmable"], f"el Consejo puede confirmarla: {d}")
    reverificar(corrida, "consejo1")
    r = corrida.n("consejo1").pedir(
        "POST", corrida.c(f"/ordenes-pago/{o}/pago-manual/no-realizado/{d['id']}/confirmacion"),
        {}, csrf=True, clave=str(uuid4()),
    )  # fmt: skip
    comprobar(r.estado == 200 and r.cuerpo["estado"] == "fallida",
              f"otro consejero confirma: {r.texto}")  # fmt: skip
    comprobar(estado(corrida, o) == "fallida", "la orden quedó Fallida: ya no está trabada")
    corrida.ok("el Consejo confirma que no se pagó: la orden sale de la traba (Fallida)")


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


def cerrar(corrida: Corrida, conservar: str | None = None) -> int:
    """Revoca todas las sesiones y da de baja los mandatos, salvo el de `conservar`: con
    `--completo`, el del Consejo que pide la exportación, que la cierra al terminar."""
    with corrida.tx() as conn:
        for rol, m in corrida.mandatos.items():
            if rol == conservar:
                continue
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
    p.add_argument(
        "--pago-manual",
        action="store_true",
        help="recorre el pago manual (0055) en un consorcio sin el riel habilitado",
    )
    p.add_argument(
        "--liquidacion",
        action="store_true",
        help="recorre la liquidación neutral (0057) sobre órdenes pagadas a mano",
    )
    p.add_argument(
        "--completo",
        action="store_true",
        help="recorre todo en un consorcio: pago manual, liquidación, auditoría y exportación",
    )
    p.add_argument(
        "--orden-trabada",
        action="store_true",
        help="recorre la orden que quedaba trabada (0067, P3 179): declara quien vio la cuenta",
    )
    p.add_argument(
        "--otro-consorcio",
        type=UUID,
        help="con --pago-manual o --liquidacion: un consorcio ajeno para probar que no se cruza",
    )
    p.add_argument(
        "--orden-ajena",
        type=UUID,
        help="con --liquidacion: una orden pagada de --otro-consorcio, que no se puede agregar",
    )
    a = p.parse_args(argumentos)
    if sum((a.pago_manual, a.liquidacion, a.completo, a.orden_trabada)) > 1:
        p.error(
            "--pago-manual, --liquidacion, --completo y --orden-trabada van en corridas distintas"
        )
    if (a.pago_manual or a.liquidacion or a.completo) and a.otro_consorcio is None:
        p.error("--pago-manual, --liquidacion y --completo piden --otro-consorcio")
    if a.liquidacion and a.orden_ajena is None:
        p.error("--liquidacion pide --orden-ajena")
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
        nombre = NOMBRE_CONSORCIO
        if a.pago_manual:
            nombre = NOMBRE_CONSORCIO_MANUAL
        elif a.liquidacion:
            nombre = NOMBRE_CONSORCIO_LIQUIDACION
        elif a.completo:
            nombre = NOMBRE_CONSORCIO_COMPLETO
        elif a.orden_trabada:
            nombre = NOMBRE_CONSORCIO_TRABADA
        creado = asegurar_consorcio(motor, a.consorcio, nombre)
        print(
            f"corrida {corrida.id}; consorcio {a.consorcio} ({'creado' if creado else 'existente'})"
        )
        with corrida.tx() as conn:
            inicio = conn.execute(text("select clock_timestamp()")).scalar_one()
        corrida.inicio = inicio
        error = None
        exportacion = None
        try:
            preparar_escena(corrida)
            if a.completo:
                exportacion = recorrer_completo(corrida, a.otro_consorcio)
            elif a.pago_manual:
                recorrer_pago_manual(corrida, a.otro_consorcio)
            elif a.liquidacion:
                recorrer_liquidacion(corrida, a.otro_consorcio, a.orden_ajena)
            elif a.orden_trabada:
                recorrer_orden_trabada(corrida)
            else:
                recorrer_ordenes(corrida)
            comprobar_fin(corrida, inicio)
        except Fallo as e:
            error = e
            print(f"FALLÓ: {e}", file=sys.stderr, flush=True)
        finally:
            # Con `--completo` en verde, el mandato de consejo1 lo cierra la exportación.
            abiertas = cerrar(corrida, "consejo1" if exportacion and error is None else None)
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
                        "exportacion": exportacion if error is None else None,
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
