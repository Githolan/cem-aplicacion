"""Límite de tasa (documento 13, §10.6; cierra `TB-8` de AnalytikCapital).

En AnalytikCapital el límite existía en el código pero el middleware nunca se agregó y la clave
era la IP del proxy, así que todos los llamadores compartían un solo cupo. Aquí hay un único
middleware (una prueba lo exige y otra comprueba el 429) con almacenamiento en Redis (claves
`cem:rl:*`) y la IP real del cliente como clave:

- el nginx de `cem-frontend` escribe `X-Forwarded-For` con `CF-Connecting-IP`, porque Traefik
  pone ahí la IP de cloudflared (medido el 14-10-2026);
- uvicorn solo acepta ese encabezado de las direcciones de `FORWARDED_ALLOW_IPS` (`arranque.sh`)
  y deja la IP real en `scope["client"]`.

No se usa slowapi: su versión 0.1.10 busca el manejador solo entre las rutas de primer nivel y
FastAPI 0.141 agrega las de `include_router` anidadas, así que eximía toda la API sin avisar
(comprobado el 14-10-2026). El motor es el mismo, `limits`.

Además del cupo general, cada familia de rutas puede tener el suyo (§10.6): autenticación, 20
por minuto por IP en `/api/v1/auth/*` (lo aplica este middleware) y 5 por minuto por correo
(lo aplica la ruta que pide el enlace con `consumir()`, porque el correo viene en el cuerpo).

Pendiente (H2a): con sesión, la clave del cupo general pasa a ser la persona. Si Redis no
responde, la API contesta 503: sin límite no se atiende (falla cerrada).
"""

import hashlib
import logging
import math
import time
from collections.abc import Iterable
from dataclasses import dataclass

from cem_contracts.errores import CodigoError
from limits import RateLimitItem, parse
from limits.aio.strategies import FixedWindowRateLimiter
from limits.errors import StorageError
from limits.storage import storage_from_string
from starlette.types import ASGIApp, Receive, Scope, Send

from app.errores import ErrorApi

LIMITE_API: RateLimitItem = parse("300/minute")
"""Toda la API por cliente (§10.6). Webhooks y descargas tendrán límites propios."""
LIMITE_AUTENTICACION_IP: RateLimitItem = parse("20/minute")
"""Familia autenticación (§10.6), por IP: se suma al cupo general."""
LIMITE_AUTENTICACION_CORREO: RateLimitItem = parse("5/minute")
"""Familia autenticación (§10.6), por correo pedido: lo consume `POST /api/v1/auth/enlace`."""
LIMITES_CODIGO_CORREO: tuple[RateLimitItem, ...] = (parse("10/hour"), parse("20/day"))
"""Intentos de canjear un código por correo (`POST /api/v1/auth/codigo`), acierten o no.
Por correo y no por IP: quien rota direcciones no gana intentos. Con 10⁶ códigos posibles,
20 por día dan 0,002 % de acertar en un día y 0,7 % en un año de intentos sin pausa, cada uno
con un correo que le llega a la persona; una persona real usa uno o dos."""
LIMITES_INVITACIONES_PERSONA: tuple[RateLimitItem, ...] = (parse("20/hour"), parse("50/day"))
"""Invitaciones que una persona puede mandar (`POST /api/v1/consorcios/{id}/invitaciones`): cada
una es un correo a una dirección que eligió ella, así que no puede servir para mandar correo en
cantidad."""
LIMITES_CONTRASENA_CORREO: tuple[RateLimitItem, ...] = (
    parse("5/minute"),
    parse("20/hour"),
    parse("100/day"),
)
"""Intentos de entrar con contraseña (`POST /api/v1/auth/contrasena/entrar`) por correo pedido,
acierten o no y aunque cambie la IP: 5 por minuto como toda la familia (§10.6) y un tope de 100
por día (NIST SP 800-63B, §5.2.2: no más de 100 fallos seguidos por cuenta). Exista o no la
cuenta, el cupo es el mismo. Quien lo agota a propósito le impide a la persona entrar con
contraseña ese día, pero no con el enlace o el código por correo."""
LIMITES_CONTRASENA_IP: tuple[RateLimitItem, ...] = (parse("60/hour"),)
"""Intentos de entrar con contraseña por IP, sumados a los 20 por minuto de la familia: frena
probar una contraseña común contra muchos correos desde una dirección."""
LIMITES_CAMBIO_CONTRASENA: tuple[RateLimitItem, ...] = (parse("5/minute"), parse("20/day"))
"""Pedidos de definir o cambiar la contraseña por persona (`PUT /api/v1/auth/contrasena`):
cada uno puede comprobar la contraseña actual, así que no puede servir para probarla."""
PREFIJO = "cem:rl"
ESPERA_REDIS_S = 1.0
"""Tope para conectar y para cada orden a Redis: una caída no deja colgada la petición."""

log = logging.getLogger("cem.limite")


@dataclass(frozen=True)
class Familia:
    """Cupo propio de las rutas que empiezan con `prefijo`, por IP, además del general."""

    nombre: str
    prefijo: str
    limite: RateLimitItem


FAMILIAS: tuple[Familia, ...] = (Familia("auth-ip", "/api/v1/auth/", LIMITE_AUTENTICACION_IP),)


def crear_limitador(almacen: str) -> FixedWindowRateLimiter:
    """Un limitador por app: lo comparten el middleware y las rutas (`app.state.limitador`)."""
    return FixedWindowRateLimiter(
        storage_from_string(
            f"async+{almacen}",
            implementation="redispy",
            key_prefix=PREFIJO,
            wrap_exceptions=True,
            socket_connect_timeout=ESPERA_REDIS_S,
            socket_timeout=ESPERA_REDIS_S,
        )
    )


def huella(valor: str) -> str:
    """Clave de cupo para un dato personal (un correo): en Redis no queda el dato."""
    return hashlib.sha256(valor.encode()).hexdigest()


async def consumir(
    limitador: FixedWindowRateLimiter, limite: RateLimitItem, nombre: str, clave: str
) -> None:
    """Gasta una unidad del cupo. Agotado: 429 con `Retry-After`; sin Redis: 503."""
    try:
        if await limitador.hit(limite, nombre, clave):
            return
        ventana = await limitador.get_window_stats(limite, nombre, clave)
    except StorageError as e:
        log.error("límite de tasa sin almacén: %s: %s", type(e.storage_error).__name__, e)
        raise ErrorApi(
            503,
            CodigoError.SERVICIO_NO_DISPONIBLE,
            "El servicio no está disponible. Probá en un rato.",
        ) from e
    espera = max(1, math.ceil(ventana.reset_time - time.time()))
    raise ErrorApi(
        429,
        CodigoError.DEMASIADAS_PETICIONES,
        "Hiciste demasiados pedidos seguidos. Esperá un momento y probá de nuevo.",
        encabezados={"Retry-After": str(espera)},
    )


class LimiteDeTasa:
    """Middleware ASGI: un cupo por cliente para toda la app, salvo las rutas `exentas`, más
    el de la familia de la ruta, si tiene."""

    def __init__(
        self,
        app: ASGIApp,
        limitador: FixedWindowRateLimiter,
        exentas: Iterable[str],
        familias: Iterable[Familia] = FAMILIAS,
    ) -> None:
        self.app = app
        self.limitador = limitador
        self.exentas = frozenset(exentas)
        self.familias = tuple(familias)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] in self.exentas:
            await self.app(scope, receive, send)
            return
        if scope.get("client") is None:
            raise RuntimeError("petición sin dirección de cliente: no se puede aplicar el límite")
        ip = scope["client"][0]
        cupos = [(LIMITE_API, "api")] + [
            (f.limite, f.nombre) for f in self.familias if scope["path"].startswith(f.prefijo)
        ]
        try:
            for limite, nombre in cupos:
                await consumir(self.limitador, limite, nombre, ip)
        except ErrorApi as e:
            await e.respuesta()(scope, receive, send)
            return
        await self.app(scope, receive, send)
