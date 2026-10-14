"""JWT de Cloudflare Access en las rutas de soporte (documento 13, §10.2 `TB-CEM-5`; primer paso
del cierre de `TB-1`).

En AnalytikCapital el origen confiaba en la red: quien llegaba al backend estaba "adentro". Aquí
las rutas de `/api/v1/soporte/` exigen el encabezado `Cf-Access-Jwt-Assertion` firmado por el
equipo de Access (RS256, claves de `<equipo>/cdn-cgi/access/certs`), con la audiencia de la
aplicación de Access y sin vencer. Un encabezado inventado no pasa aunque el pedido llegue por
un camino sin Access. La identidad propia de vecinos y administradores llega en H2a.

Sin `CEM_ACCESS_EQUIPO` y `CEM_ACCESS_AUD` las rutas de soporte rechazan todo (falla cerrada).
"""

import logging
from dataclasses import dataclass
from typing import Annotated, Literal

import jwt
from cem_contracts.errores import CodigoError
from fastapi import Depends, Request

from app.errores import ErrorApi

ENCABEZADO = "Cf-Access-Jwt-Assertion"
ESPERA_CLAVES_S = 5
"""Tope para bajar las claves públicas del equipo (se guardan y se renuevan cada 5 minutos)."""

log = logging.getLogger("cem.acceso")


@dataclass(frozen=True)
class Operador:
    identidad: str
    """Correo de la persona o, para un token de servicio de Access, su `common_name`."""
    tipo: Literal["persona", "servicio"]


class VerificadorAccess:
    def __init__(self, equipo: str, audiencia: str) -> None:
        self.emisor = equipo.rstrip("/")
        self.audiencia = audiencia
        self.claves = jwt.PyJWKClient(
            f"{self.emisor}/cdn-cgi/access/certs",
            headers={"User-Agent": "cem-backend"},
            timeout=ESPERA_CLAVES_S,
        )

    def verificar(self, token: str) -> Operador:
        clave = self.claves.get_signing_key_from_jwt(token)
        datos = jwt.decode(
            token,
            clave.key,
            algorithms=["RS256"],
            audience=self.audiencia,
            issuer=self.emisor,
            options={"require": ["exp", "iat", "iss", "aud"]},
        )
        if datos.get("email"):
            return Operador(datos["email"], "persona")
        if datos.get("common_name"):
            return Operador(datos["common_name"], "servicio")
        raise jwt.InvalidTokenError("el token no dice quién es")


def crear_verificador(equipo: str, audiencia: str) -> VerificadorAccess | None:
    if bool(equipo) != bool(audiencia):
        raise RuntimeError("CEM_ACCESS_EQUIPO y CEM_ACCESS_AUD van juntas: falta una")
    if not equipo:
        log.warning("Access sin configurar: las rutas de soporte rechazan todo")
        return None
    return VerificadorAccess(equipo, audiencia)


def require_operador(request: Request) -> Operador:
    verificador: VerificadorAccess | None = request.app.state.access
    token = request.headers.get(ENCABEZADO)
    if verificador is not None and token:
        try:
            return verificador.verificar(token)
        except jwt.PyJWKClientConnectionError as e:
            log.error("no se pudieron bajar las claves de Access: %s", e)
            raise ErrorApi(
                503, CodigoError.SERVICIO_NO_DISPONIBLE, "El servicio no está disponible."
            ) from e
        except jwt.PyJWTError as e:
            log.warning("JWT de Access rechazado: %s: %s", type(e).__name__, e)
    raise ErrorApi(401, CodigoError.NO_AUTENTICADO, "Iniciá sesión para continuar.")


OperadorDep = Annotated[Operador, Depends(require_operador)]
