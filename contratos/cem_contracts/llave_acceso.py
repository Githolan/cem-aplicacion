"""Llave de acceso (WebAuthn): el PIN, la huella o la cara del dispositivo como verificación.

Es otro medio de la verificación reciente de `segundo_factor`: al confirmarla, la sesión cuenta
como verificada durante 10 minutos. Las opciones y las respuestas son las de la especificación
WebAuthn en JSON: las arma el backend y las entiende el navegador, así que van sin modelar.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class OpcionesLlave(BaseModel):
    """Respuesta de `POST …/alta/opciones` y `POST …/verificar/opciones`."""

    model_config = ConfigDict(frozen=True)

    opciones: dict[str, Any] = Field(description="Opciones WebAuthn, con binarios en base64url.")


class LlaveNueva(BaseModel):
    """`POST /api/v1/auth/llave/alta`."""

    model_config = ConfigDict(frozen=True)

    credencial: dict[str, Any] = Field(description="La respuesta de `navigator.credentials`.")
    nombre: str = Field(min_length=1, max_length=60, examples=["Mi computadora"])


class RespuestaLlave(BaseModel):
    """`POST /api/v1/auth/llave/verificar`."""

    model_config = ConfigDict(frozen=True)

    credencial: dict[str, Any] = Field(description="La respuesta de `navigator.credentials`.")


class LlaveRegistrada(BaseModel):
    """Respuesta de `POST /api/v1/auth/llave/alta`."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    codigos_recuperacion: list[str] | None = Field(
        description="Diez códigos que se muestran una sola vez. Nulo si ya tenía códigos sin usar."
    )


class LlaveResumen(BaseModel):
    """Una fila de `GET /api/v1/auth/llave`."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    credencial_id: str = Field(
        description="Identificador público de la credencial, en base64url: el que da el navegador."
    )
    nombre: str
    creada_en: datetime
    ultimo_uso_en: datetime | None
