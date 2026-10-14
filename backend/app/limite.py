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

Con la identidad propia (H2a) la clave pasa a ser la persona. Si Redis no responde, la API
contesta 503: sin límite no se atiende (falla cerrada).
"""

import logging
import math
import time
from collections.abc import Iterable

from cem_contracts.errores import CodigoError
from limits import RateLimitItem, parse
from limits.aio.strategies import FixedWindowRateLimiter
from limits.errors import StorageError
from limits.storage import storage_from_string
from starlette.types import ASGIApp, Receive, Scope, Send

from app.errores import respuesta_error

LIMITE_API: RateLimitItem = parse("300/minute")
"""Toda la API por cliente (§10.6). Autenticación, webhooks y descargas tendrán límites propios."""
PREFIJO = "cem:rl"
ESPERA_REDIS_S = 1.0
"""Tope para conectar y para cada orden a Redis: una caída no deja colgada la petición."""

log = logging.getLogger("cem.limite")


class LimiteDeTasa:
    """Middleware ASGI: un cupo por cliente para toda la app, salvo las rutas `exentas`."""

    def __init__(self, app: ASGIApp, almacen: str, exentas: Iterable[str]) -> None:
        self.app = app
        self.exentas = frozenset(exentas)
        self.limitador = FixedWindowRateLimiter(
            storage_from_string(
                f"async+{almacen}",
                implementation="redispy",
                key_prefix=PREFIJO,
                wrap_exceptions=True,
                socket_connect_timeout=ESPERA_REDIS_S,
                socket_timeout=ESPERA_REDIS_S,
            )
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] in self.exentas:
            await self.app(scope, receive, send)
            return
        if scope.get("client") is None:
            raise RuntimeError("petición sin dirección de cliente: no se puede aplicar el límite")
        ip = scope["client"][0]
        try:
            if await self.limitador.hit(LIMITE_API, "api", ip):
                await self.app(scope, receive, send)
                return
            ventana = await self.limitador.get_window_stats(LIMITE_API, "api", ip)
        except StorageError as e:
            log.error("límite de tasa sin almacén: %s: %s", type(e.storage_error).__name__, e)
            respuesta = respuesta_error(
                503,
                CodigoError.SERVICIO_NO_DISPONIBLE,
                "El servicio no está disponible. Probá en un rato.",
            )
        else:
            respuesta = respuesta_error(
                429,
                CodigoError.DEMASIADAS_PETICIONES,
                "Hiciste demasiados pedidos seguidos. Esperá un momento y probá de nuevo.",
            )
            espera = max(1, math.ceil(ventana.reset_time - time.time()))
            respuesta.headers["Retry-After"] = str(espera)
        await respuesta(scope, receive, send)
