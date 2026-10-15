"""Segundo factor TOTP y verificación reciente (documento 13, §10.3 y TB-CEM-3).

La persona da de alta una aplicación de autenticación (TOTP, RFC 6238: seis dígitos cada 30
segundos) y recibe diez códigos de recuperación de un solo uso. Las acciones privilegiadas
(aprobar un egreso, cambiar un beneficiario o el modo, exportar, revocar) piden haber
verificado el segundo factor en los últimos 10 minutos en la misma sesión.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

FORMATO_CODIGO_TOTP = r"^[0-9]{6}$"
FORMATO_CODIGO_RECUPERACION = r"^[a-z2-9]{5}-[a-z2-9]{5}$"
"""Diez caracteres sin los que se confunden (0, 1, o, l), en dos grupos: `k7m2q-x9p4t`."""


class EstadoSegundoFactor(BaseModel):
    """`GET /api/v1/auth/totp`."""

    model_config = ConfigDict(frozen=True)

    activo: bool = Field(description="Tiene una aplicación de autenticación confirmada.")
    activado_en: datetime | None = None
    codigos_recuperacion_restantes: int = Field(ge=0)
    verificado_en: datetime | None = Field(
        description="Última verificación del segundo factor en esta sesión."
    )


class AltaTotp(BaseModel):
    """Respuesta de `POST /api/v1/auth/totp/alta`: el secreto para cargar en la aplicación.

    Se muestra una sola vez y queda pendiente hasta que la persona lo confirme con un código."""

    model_config = ConfigDict(frozen=True)

    secreto: str = Field(description="Base32 sin relleno, para escribirlo a mano.")
    uri: str = Field(
        description="`otpauth://totp/…` para abrir la aplicación o dibujar un código QR.",
        examples=["otpauth://totp/Consorcio%20en%20Mano:ana%40ejemplo.com?secret=…"],
    )


class CodigoTotp(BaseModel):
    """`POST /api/v1/auth/totp/confirmar` y `POST /api/v1/auth/totp/baja`."""

    model_config = ConfigDict(frozen=True)

    codigo: str = Field(pattern=FORMATO_CODIGO_TOTP, examples=["042917"])


class VerificacionSegundoFactor(BaseModel):
    """`POST /api/v1/auth/totp/verificar`: el código de la aplicación o uno de recuperación."""

    model_config = ConfigDict(frozen=True)

    codigo: str = Field(
        pattern=f"{FORMATO_CODIGO_TOTP}|{FORMATO_CODIGO_RECUPERACION}",
        examples=["042917", "k7m2q-x9p4t"],
    )


class CodigosRecuperacion(BaseModel):
    """Respuesta de `POST /api/v1/auth/totp/confirmar`: se muestran una sola vez."""

    model_config = ConfigDict(frozen=True)

    codigos: list[str] = Field(min_length=10, max_length=10)


class SegundoFactorVerificado(BaseModel):
    """Respuesta de `POST /api/v1/auth/totp/verificar`."""

    model_config = ConfigDict(frozen=True)

    verificado_en: datetime
    vale_hasta: datetime = Field(description="Hasta cuándo cuenta como reciente (10 minutos).")
    codigos_recuperacion_restantes: int = Field(ge=0)
