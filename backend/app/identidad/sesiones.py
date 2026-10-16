"""Tokens, cookies y validación de sesiones (documento 13, §10.3, §10.4 y §15.3).

Cinco cookies, todas con prefijo `__Host-` (solo `Secure`, `Path=/` y sin `Domain`: ningún
subdominio las puede fijar ni pisar):

| Cookie | Qué guarda | `HttpOnly` | Vida |
|---|---|---|---|
| `__Host-cem_sesion` | Token opaco de la sesión | sí | 30 días, renovada con el uso |
| `__Host-cem_csrf` | Token CSRF, derivado de la sesión | no: el frontend lo lee | la de la sesión |
| `__Host-cem_nonce` | Nonce que liga el enlace al navegador que lo pidió | sí | 15 minutos |
| `__Host-cem_dispositivo` | Identificador del navegador (no es secreto) | sí | 400 días |
| `__Host-cem_desafio` | Paso del segundo factor al entrar con contraseña | sí | 5 minutos |

En la base solo queda el SHA-256 de cada token y de cada nonce, nunca el valor (§15.2).

**Código por correo (§10.3).** El correo del enlace lleva también un código de seis dígitos
para entrar desde otro dispositivo. Seis dígitos son 10⁶ valores: un SHA-256 sin clave se
invierte probándolos todos. En la base queda `HMAC-SHA256(CEM_CODIGO_CLAVE, correo || código)`
(`hash_codigo`); la clave vive solo en el entorno de la API, así que un volcado de la base no
alcanza para probar códigos. No hace falta un hash lento: sin la clave no hay nada que probar, y
con la clave ningún costo por intento salva a un espacio de 10⁶.

**CSRF de token doble (§10.4).** El token CSRF es `HMAC-SHA256(token de sesión, "cem-csrf")`:
ligado a la sesión, se verifica sin guardarlo y una cookie CSRF puesta por otro (o de otra
sesión) no sirve. Toda petición que modifica estado con sesión debe traerlo en `X-CSRF-Token`.
"""

import base64
import hashlib
import hmac
import ipaddress
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from cem_contracts.errores import CodigoError
from fastapi import Request, Response
from sqlalchemy import Connection, text

from app.errores import ErrorApi

VIGENCIA_ENLACE = timedelta(minutes=15)
"""Enlace de un solo uso (§10.3). La migración 0004 impone el mismo tope."""
VIGENCIA_CODIGO = timedelta(minutes=10)
"""Código por correo (§10.3). La migración 0006 impone el mismo tope."""
INTENTOS_CODIGO = 5
"""Intentos fallidos que agotan un código (§10.3). La migración 0006 impone el mismo tope."""
DIGITOS_CODIGO = 6
FORMATO_CODIGO = re.compile(rf"^[0-9]{{{DIGITOS_CODIGO}}}$")
VIGENCIA_SESION = timedelta(days=30)
"""Vencimiento deslizante para vecinos (§10.4). Las 12 h de los roles con facultades de dinero
llegan con los roles (H2 2b, pendiente): la vigencia pasará a depender de la persona y
`nucleo.validar_sesion` ya la recibe como parámetro."""
RETENCION_ACCESO = timedelta(days=90)
"""Cuánto se conservan una sesión y un enlace después de dejar de servir (vencer, revocarse o
usarse): son registros de acceso técnico, 90 días (§15.6). Los borra la purga diaria."""
RENOVAR_CADA = timedelta(minutes=5)
"""Cada cuánto, como mucho, una petición corre el vencimiento: evita escribir en cada una."""
VIGENCIA_DISPOSITIVO = timedelta(days=400)
"""El tope de vida de una cookie en Chrome."""
BYTES_TOKEN = 32
"""256 bits para el token del enlace, el nonce y el token de sesión."""
FORMATO_TOKEN = re.compile(r"^[A-Za-z0-9_-]{43}$")
"""`secrets.token_urlsafe(32)`: 43 caracteres base64url sin relleno."""
LARGO_MAXIMO_AGENTE = 512
"""El mismo tope que la restricción de `nucleo.sesion.agente`."""

COOKIE_SESION = "__Host-cem_sesion"
COOKIE_CSRF = "__Host-cem_csrf"
COOKIE_NONCE = "__Host-cem_nonce"
COOKIE_DISPOSITIVO = "__Host-cem_dispositivo"
COOKIE_DESAFIO = "__Host-cem_desafio"
ENCABEZADO_CSRF = "X-CSRF-Token"
METODOS_SEGUROS = frozenset({"GET", "HEAD", "OPTIONS"})
"""Los que no modifican estado: no piden token CSRF."""
_ETIQUETA_CSRF = b"cem-csrf"
_ETIQUETA_CODIGO = b"cem-codigo-acceso"


def nuevo_token() -> str:
    return secrets.token_urlsafe(BYTES_TOKEN)


def hash_token(token: str) -> bytes:
    return hashlib.sha256(token.encode("ascii")).digest()


def token_valido(valor: str | None) -> bool:
    return valor is not None and FORMATO_TOKEN.fullmatch(valor) is not None


def nuevo_codigo() -> str:
    """Seis dígitos uniformes, con ceros a la izquierda."""
    return f"{secrets.randbelow(10**DIGITOS_CODIGO):0{DIGITOS_CODIGO}d}"


def clave_codigo(valor: str) -> bytes:
    """`CEM_CODIGO_CLAVE`: 256 bits en base64url sin relleno (`secrets.token_urlsafe(32)`).

    Una clave corta o mal copiada impide el arranque: haría los hashes adivinables.
    """
    if not token_valido(valor):
        raise RuntimeError(
            "CEM_CODIGO_CLAVE debe ser 32 bytes en base64url sin relleno (43 caracteres):"
            " python -c 'import secrets; print(secrets.token_urlsafe(32))'"
        )
    return base64.urlsafe_b64decode(valor + "=")


def hash_codigo(clave: bytes, correo: str, codigo: str) -> bytes:
    """HMAC-SHA256 del correo normalizado y el código, con la clave de la API.

    El correo separa a las personas: el mismo código de dos personas da hashes distintos."""
    mensaje = b"\0".join((_ETIQUETA_CODIGO, correo.encode(), codigo.encode("ascii")))
    return hmac.new(clave, mensaje, hashlib.sha256).digest()


def token_csrf(token_sesion: str) -> str:
    return hmac.new(token_sesion.encode("ascii"), _ETIQUETA_CSRF, hashlib.sha256).hexdigest()


def ip_cliente(request: Request) -> str:
    """La IP real (uvicorn la toma de `X-Forwarded-For` solo desde el proxy, `arranque.sh`).

    Si no es una IP, falla: es un error de configuración, no un dato del cliente.
    """
    if request.client is None:
        raise RuntimeError("petición sin dirección de cliente")
    return str(ipaddress.ip_address(request.client.host))


def agente(request: Request) -> str | None:
    valor = request.headers.get("user-agent")
    return valor[:LARGO_MAXIMO_AGENTE] if valor else None


def _cookie(response: Response, nombre: str, valor: str, vida: timedelta, *, legible=False):
    response.set_cookie(
        nombre,
        valor,
        max_age=int(vida.total_seconds()),
        path="/",
        secure=True,
        httponly=not legible,
        samesite="lax",
    )


def fijar_dispositivo(request: Request, response: Response) -> UUID:
    """El identificador del navegador, reusando el que ya tenga."""
    try:
        dispositivo = UUID(request.cookies.get(COOKIE_DISPOSITIVO, ""))
    except ValueError:
        dispositivo = UUID(bytes=secrets.token_bytes(16), version=4)
    _cookie(response, COOKIE_DISPOSITIVO, str(dispositivo), VIGENCIA_DISPOSITIVO)
    return dispositivo


def fijar_nonce_y_dispositivo(request: Request, response: Response) -> tuple[str, UUID]:
    """El nonce y el dispositivo del navegador, reusando los que ya tenga.

    Reusar el nonce deja que dos enlaces pedidos desde el mismo navegador sirvan los dos.
    """
    nonce = request.cookies.get(COOKIE_NONCE)
    if not token_valido(nonce):
        nonce = nuevo_token()
    _cookie(response, COOKIE_NONCE, nonce, VIGENCIA_ENLACE)
    return nonce, fijar_dispositivo(request, response)


def fijar_sesion(response: Response, token_sesion: str) -> None:
    _cookie(response, COOKIE_SESION, token_sesion, VIGENCIA_SESION)
    _cookie(response, COOKIE_CSRF, token_csrf(token_sesion), VIGENCIA_SESION, legible=True)


def borrar_sesion(response: Response) -> None:
    for nombre, legible in ((COOKIE_SESION, False), (COOKIE_CSRF, True)):
        response.delete_cookie(nombre, path="/", secure=True, httponly=not legible, samesite="lax")


def borrar_nonce(response: Response) -> None:
    response.delete_cookie(COOKIE_NONCE, path="/", secure=True, httponly=True, samesite="lax")


def fijar_desafio(response: Response, token: str, vida: timedelta) -> None:
    _cookie(response, COOKIE_DESAFIO, token, vida)


def borrar_desafio(response: Response) -> None:
    response.delete_cookie(COOKIE_DESAFIO, path="/", secure=True, httponly=True, samesite="lax")


@dataclass(frozen=True)
class SesionValida:
    persona_id: UUID
    sesion_id: UUID
    vence_en: datetime
    renovada: bool


def validar(conn: Connection, token_sesion: str) -> SesionValida | None:
    fila = conn.execute(
        text("select persona, sesion, vence, renovada from nucleo.validar_sesion(:t, :v, :r)"),
        {"t": hash_token(token_sesion), "v": VIGENCIA_SESION, "r": RENOVAR_CADA},
    ).one_or_none()
    return SesionValida(*fila) if fila else None


def exigir_csrf(request: Request, token_sesion: str) -> None:
    """Token doble en toda petición que modifica estado (§10.4)."""
    if request.method in METODOS_SEGUROS:
        return
    enviado = request.headers.get(ENCABEZADO_CSRF, "")
    if not hmac.compare_digest(enviado.encode(), token_csrf(token_sesion).encode()):
        raise ErrorApi(
            403, CodigoError.CSRF_INVALIDO, "No pudimos verificar el pedido. Recargá la página."
        )
