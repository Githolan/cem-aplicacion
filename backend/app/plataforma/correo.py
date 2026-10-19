"""Correo transaccional por SMTP desde `cem-trabajos` (documento 13, §12.4 y §13.1).

Cómo se pide un correo
----------------------
Un módulo de negocio no envía: publica el evento `correo.solicitado` en el outbox, en la
misma transacción que su cambio, con `solicitar()`. El despachador de `cem-planificador` lo
convierte en un trabajo `notificar.correo` (clave única `notificar.correo:<id del evento>`) y
`cem-trabajos` lo envía. La carga del evento (y del trabajo) es:

    {
      "para":   "vecina@ejemplo.com",   # una sola dirección ASCII, sin nombre (máx. 254)
      "asunto": "Tu enlace para entrar", # una línea, no vacía
      "texto":  "Entrá con ...",         # cuerpo en texto plano, no vacío
      "html":   "<p>Entrá con ...</p>"   # opcional: alternativa HTML del mismo contenido
    }

Ninguna otra clave se acepta (un error de tipeo como `destinatario` deja el trabajo muerto en
vez de mandar un correo sin destinatario). El despachador agrega `evento_id` y `evento`.
`solicitar()` valida la carga antes de publicar, así un error de programación falla en la
transacción de negocio y no horas después en la cola.

Envío y errores
---------------
Conexión a `CEM_SMTP_SERVIDOR`:`CEM_SMTP_PUERTO` (OCI Email Delivery: puerto 587), STARTTLS
obligatorio con el certificado verificado, autenticación con `CEM_SMTP_USUARIO` y
`CEM_SMTP_CLAVE`, remitente `CEM_CORREO_REMITENTE` (el aprobado en OCI). Solo `cem-trabajos`
lee esas variables, y solo al enviar: la API no las tiene ni las necesita (TB-CEM-6, §10.2).

- Carga inválida → `ErrorDefinitivo`: muerto al primer intento.
- Respuesta 5xx del servidor (destinatario inexistente, remitente no aprobado, credencial
  rechazada) → `ErrorDefinitivo`: según RFC 5321 un 5xx no se arregla repitiendo el pedido.
- Respuesta 4xx, conexión caída, tiempo agotado, certificado inválido o falta de
  configuración → error común: se reintenta con retroceso (5 intentos; la última espera llega
  al tope de 1 hora, §12.4) y al agotarlos queda muerto y `/api/v1/health/cola` alerta.

Al menos una vez
----------------
SMTP no tiene idempotencia del lado del receptor. Si el servidor aceptó el correo y el
trabajador muere (o pierde la reserva) antes de confirmar el trabajo, el correo sale otra vez.
Cada envío lleva un `Message-ID` fijo derivado de la clave del envío (el id del evento, o el del
trabajo si se encoló sin outbox) y los encabezados `X-CEM-Clave-Envio` y `X-CEM-Intento`: un
reenvío por reintento tiene el mismo `Message-ID` y se distingue en los registros de OCI y en la
casilla. Algunos clientes agrupan o descartan duplicados por `Message-ID`; no es una garantía.

Registros: nunca la dirección ni el asunto ni el cuerpo. El error que queda en
`plataforma.jobs` y en los registros pasa por `redactar()` (§16.1).
"""

import logging
import os
import re
import smtplib
import ssl
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from email.message import EmailMessage
from email.utils import formataddr, formatdate
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator
from sqlalchemy import Connection

from app.plataforma import cola, outbox

log = logging.getLogger("cem.correo")

EVENTO = "correo.solicitado"
"""Evento del outbox que pide un correo. Su carga es `CargaCorreo`."""
TIPO = "notificar.correo"
"""Tipo de trabajo que lo envía."""

# Reintentos de las notificaciones (§12.4): 5 intentos con esperas de 7,5, 15, 30 y 60 minutos;
# la última llega al tope de 1 hora. Cubren una caída del proveedor de casi dos horas.
MAXIMO_INTENTOS = 5
RETROCESO_BASE = timedelta(minutes=7, seconds=30)
RETROCESO_TOPE = timedelta(hours=1)

TIEMPO_DE_ESPERA_SMTP = 30
"""Segundos por operación de red con el servidor SMTP: muy por debajo del plazo de reserva de
un trabajo (5 minutos en `cem-trabajos`), así un servidor colgado no hace vencer la reserva."""

NOMBRE_REMITENTE = "Consorcio en Mano"

VARIABLES = {
    "servidor": "CEM_SMTP_SERVIDOR",
    "puerto": "CEM_SMTP_PUERTO",
    "usuario": "CEM_SMTP_USUARIO",
    "clave": "CEM_SMTP_CLAVE",
    "remitente": "CEM_CORREO_REMITENTE",
}
"""Campo de `ConfigCorreo` → variable de entorno. Solo en `cem-trabajos` (TB-CEM-6)."""

CAMPOS_DEL_OUTBOX = ("evento_id", "evento")
"""Claves que agrega el despachador del outbox a la carga del trabajo."""

LARGO_MAXIMO_DIRECCION = 254
"""RFC 5321, §4.5.3.1.3: el camino tiene 256 octetos con los corchetes angulares."""

DOMINIO_RESERVADO = ".invalid"
"""RFC 6761, §6.4: `.invalid` nunca resuelve. Lo usan solo las cuentas sintéticas del smoke de
staging (decisión P3 del propietario: no sale ningún correo). A esas direcciones no se intenta
enviar: el trabajo termina como hecho con `no_enviado`, sin abrir la conexión SMTP."""

# Una dirección simple `local@dominio`: sin nombre, sin comentarios, sin varias direcciones.
_DIRECCION = re.compile(
    r"[^@\s<>()\[\],;:\"\\]+"
    r"@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+"
)


class ErrorConfigCorreo(RuntimeError):
    """Falta o es inválida una variable del correo. Se reintenta: se arregla configurando."""


@dataclass(frozen=True)
class ConfigCorreo:
    servidor: str
    puerto: int
    usuario: str
    clave: str
    remitente: str

    def __repr__(self) -> str:
        # La clave nunca en un registro ni en un error.
        return f"ConfigCorreo(servidor={self.servidor!r}, puerto={self.puerto})"

    @classmethod
    def desde_entorno(cls, entorno: Mapping[str, str] = os.environ) -> "ConfigCorreo":
        faltan = [v for v in VARIABLES.values() if not entorno.get(v)]
        if faltan:
            raise ErrorConfigCorreo("faltan variables del correo: " + ", ".join(faltan))
        valores = {campo: entorno[v] for campo, v in VARIABLES.items()}
        puerto = valores.pop("puerto")
        if not puerto.isdecimal():
            raise ErrorConfigCorreo(f"{VARIABLES['puerto']} debe ser un número de puerto")
        if not _DIRECCION.fullmatch(valores["remitente"]):
            raise ErrorConfigCorreo(f"{VARIABLES['remitente']} no es una dirección simple")
        return cls(puerto=int(puerto), **valores)

    @property
    def dominio_remitente(self) -> str:
        return self.remitente.rpartition("@")[2]


class CargaCorreo(BaseModel):
    """Carga del evento `correo.solicitado` y del trabajo `notificar.correo`."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    para: str
    asunto: str
    texto: str
    html: str | None = None

    @field_validator("para")
    @classmethod
    def _para(cls, valor: str) -> str:
        # Los mensajes no repiten el valor: llegan al error del trabajo y a los registros.
        if len(valor) > LARGO_MAXIMO_DIRECCION:
            raise ValueError(f"la dirección supera {LARGO_MAXIMO_DIRECCION} caracteres")
        if not valor.isascii() or not _DIRECCION.fullmatch(valor):
            raise ValueError("no es una dirección simple local@dominio en ASCII")
        return valor

    @field_validator("asunto")
    @classmethod
    def _asunto(cls, valor: str) -> str:
        if not valor.strip():
            raise ValueError("el asunto está vacío")
        if "\r" in valor or "\n" in valor:
            raise ValueError("el asunto tiene saltos de línea")
        return valor

    @field_validator("texto", "html")
    @classmethod
    def _cuerpo(cls, valor: str | None) -> str | None:
        if valor is not None and not valor.strip():
            raise ValueError("está vacío")
        return valor


def validar(carga: Mapping[str, Any]) -> CargaCorreo:
    """La carga sin las claves del outbox, validada. Inválida → `ErrorDefinitivo`."""
    datos = {k: v for k, v in carga.items() if k not in CAMPOS_DEL_OUTBOX}
    try:
        return CargaCorreo.model_validate(datos)
    except ValidationError as e:
        # Sin `input`: el valor (la dirección, el cuerpo) no va al error.
        problemas = "; ".join(
            f"{'.'.join(map(str, x['loc'])) or 'carga'}: {x['msg']}"
            for x in e.errors(include_input=False, include_url=False)
        )
        raise cola.ErrorDefinitivo(f"carga de correo inválida: {problemas}") from None


def solicitar(
    conn: Connection,
    *,
    para: str,
    asunto: str,
    texto: str,
    html: str | None = None,
    consorcio_id: UUID | None = None,
) -> UUID:
    """Publica `correo.solicitado` en la transacción de `conn`. Devuelve el id del evento.

    Sin consorcio (p. ej. el enlace para entrar de una persona) el ámbito debe ser sin
    consorcio, como pide la RLS del outbox.
    """
    carga = validar({"para": para, "asunto": asunto, "texto": texto, "html": html})
    return outbox.publicar(
        conn, EVENTO, carga=carga.model_dump(exclude_none=True), consorcio_id=consorcio_id
    )


def clave_envio(trabajo: cola.Trabajo) -> str:
    """`evento-<id>` si vino del outbox; `trabajo-<id>` si se encoló directo."""
    if "evento_id" in trabajo.carga:
        return f"evento-{UUID(trabajo.carga['evento_id'])}"
    return f"trabajo-{trabajo.id}"


def armar(config: ConfigCorreo, carga: CargaCorreo, clave: str, intento: int) -> EmailMessage:
    mensaje = EmailMessage()
    mensaje["From"] = formataddr((NOMBRE_REMITENTE, config.remitente))
    mensaje["To"] = carga.para
    mensaje["Subject"] = carga.asunto
    mensaje["Date"] = formatdate(usegmt=True)
    mensaje["Message-ID"] = f"<cem.{clave}@{config.dominio_remitente}>"
    mensaje["X-CEM-Clave-Envio"] = clave
    mensaje["X-CEM-Intento"] = str(intento)
    mensaje.set_content(carga.texto)
    if carga.html is not None:
        mensaje.add_alternative(carga.html, subtype="html")
    return mensaje


def enviar(config: ConfigCorreo, mensaje: EmailMessage) -> None:
    """Envía por SMTP con STARTTLS obligatorio y el certificado del servidor verificado.

    El contexto TLS es el del sistema (`ssl.create_default_context`), que respeta
    `SSL_CERT_FILE`. Sin STARTTLS en el servidor, `smtplib` falla y no se envía en claro.
    """
    contexto = ssl.create_default_context()
    with smtplib.SMTP(config.servidor, config.puerto, timeout=TIEMPO_DE_ESPERA_SMTP) as smtp:
        smtp.ehlo()
        smtp.starttls(context=contexto)
        smtp.ehlo()
        smtp.login(config.usuario, config.clave)
        smtp.send_message(mensaje)


def _permanente(codigo: int) -> bool:
    return 500 <= codigo < 600


def notificar_correo(conn: Connection, trabajo: cola.Trabajo) -> dict[str, str]:
    """Manejador de `notificar.correo` (lo registra `tareas.py`)."""
    carga = validar(trabajo.carga)
    clave = clave_envio(trabajo)
    if carga.para.lower().endswith(DOMINIO_RESERVADO):
        log.info("correo no enviado: destino en un dominio reservado (%s)", clave,
                 extra={"campos": {"clave_envio": clave}})  # fmt: skip
        return {"clave_envio": clave, "no_enviado": "dominio_reservado"}
    config = ConfigCorreo.desde_entorno()
    mensaje = armar(config, carga, clave, trabajo.intentos)
    try:
        enviar(config, mensaje)
    except smtplib.SMTPRecipientsRefused as e:
        codigos = sorted({codigo for codigo, _ in e.recipients.values()})
        detalle = "; ".join(
            f"{codigo} {texto.decode(errors='replace')}" for codigo, texto in e.recipients.values()
        )
        if any(_permanente(c) for c in codigos):
            raise cola.ErrorDefinitivo(f"el servidor rechazó al destinatario: {detalle}") from e
        raise ConnectionError(f"el servidor rechazó al destinatario por ahora: {detalle}") from e
    except smtplib.SMTPResponseException as e:
        detalle = f"{e.smtp_code} {e.smtp_error.decode(errors='replace')}"
        if _permanente(e.smtp_code):
            raise cola.ErrorDefinitivo(
                f"el servidor rechazó el envío ({type(e).__name__}): {detalle}"
            ) from e
        raise
    log.info("correo enviado: %s intento %s", clave, trabajo.intentos,
             extra={"campos": {"clave_envio": clave}})  # fmt: skip
    return {"clave_envio": clave, "message_id": mensaje["Message-ID"]}
