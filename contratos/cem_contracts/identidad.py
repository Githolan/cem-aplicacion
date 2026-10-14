"""Identidad: enlace mágico y sesiones (documento 13, §10.3 y §10.4)."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

LARGO_MAXIMO_CORREO = 254
"""RFC 5321: el tope de una dirección que se puede entregar."""
FORMATO_CORREO = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
"""Solo la forma; si la dirección existe lo dice el envío, nunca la respuesta (§10.3)."""


class PedidoEnlace(BaseModel):
    """`POST /api/v1/auth/enlace`: pide un enlace de acceso para ese correo."""

    model_config = ConfigDict(frozen=True)

    correo: str = Field(
        max_length=LARGO_MAXIMO_CORREO, pattern=FORMATO_CORREO, examples=["ana@ejemplo.com"]
    )


FORMATO_TOKEN = r"^[A-Za-z0-9_-]{43}$"
"""Token del enlace: 256 bits en base64url sin relleno."""


class CanjeEnlace(BaseModel):
    """`POST /api/v1/auth/canje`: el token que la página `/entrar` leyó del fragmento del enlace.

    Va en el cuerpo y no en la URL: así no queda en ningún registro de acceso ni en `Referer`."""

    model_config = ConfigDict(frozen=True)

    token: str = Field(pattern=FORMATO_TOKEN)


class EnlacePedido(BaseModel):
    """Respuesta 202 del pedido de enlace: la misma exista o no la cuenta (§10.3)."""

    model_config = ConfigDict(frozen=True)

    estado: Literal["pedido"]
    mensaje: str = Field(description="Texto genérico para la persona usuaria.")


class SesionActual(BaseModel):
    """`GET /api/v1/auth/sesion` y respuesta del canje: quién es la persona de la sesión."""

    model_config = ConfigDict(frozen=True)

    persona_id: UUID
    nombre: str
    correo: str
    sesion_id: UUID
    vence_en: datetime = Field(
        description="Vencimiento deslizante: cada uso de la sesión lo vuelve a correr."
    )
