"""Contraseña y entrada con correo y contraseña (documento 13, §10.3 y §15.2).

La contraseña es el método de administradores, consejeros y operadores. Quien tiene el segundo
factor activo entra en dos pasos: la contraseña y después el código de su aplicación de
autenticación (o uno de recuperación).
"""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from cem_contracts.identidad import FORMATO_CORREO, LARGO_MAXIMO_CORREO, SesionActual
from cem_contracts.segundo_factor import FORMATO_CODIGO_RECUPERACION, FORMATO_CODIGO_TOTP

LARGO_MINIMO_CONTRASENA = 12
"""Sin reglas de composición (NIST SP 800-63B, §3.1.1): el largo es lo que cuenta. Se cuenta
después de normalizar (NFKC), en caracteres."""
LARGO_MAXIMO_CONTRASENA = 128
"""Admite frases largas y acota el trabajo de cada pedido."""
_CONTRASENA = {"format": "password", "writeOnly": True}


class EntradaContrasena(BaseModel):
    """`POST /api/v1/auth/contrasena/entrar`."""

    model_config = ConfigDict(frozen=True)

    correo: str = Field(
        max_length=LARGO_MAXIMO_CORREO, pattern=FORMATO_CORREO, examples=["ana@ejemplo.com"]
    )
    contrasena: str = Field(
        min_length=1, max_length=LARGO_MAXIMO_CONTRASENA, json_schema_extra=_CONTRASENA
    )


class SesionAbierta(BaseModel):
    """La contraseña sirvió y la persona no tiene segundo factor: la sesión ya está abierta."""

    model_config = ConfigDict(frozen=True)

    estado: Literal["sesion_abierta"]
    sesion: SesionActual


class FaltaSegundoFactor(BaseModel):
    """La contraseña sirvió y la persona tiene segundo factor: falta el código
    (`POST /api/v1/auth/contrasena/segundo-factor`) desde el mismo navegador, antes de
    `vence_en`."""

    model_config = ConfigDict(frozen=True)

    estado: Literal["falta_segundo_factor"]
    vence_en: datetime


ResultadoEntrada = Annotated[SesionAbierta | FaltaSegundoFactor, Field(discriminator="estado")]


class CodigoEntrada(BaseModel):
    """`POST /api/v1/auth/contrasena/segundo-factor`: el código de la aplicación o uno de
    recuperación (que queda gastado)."""

    model_config = ConfigDict(frozen=True)

    codigo: str = Field(
        pattern=f"{FORMATO_CODIGO_TOTP}|{FORMATO_CODIGO_RECUPERACION}",
        examples=["042917", "k7m2q-x9p4t"],
    )


class NuevaContrasena(BaseModel):
    """`PUT /api/v1/auth/contrasena`: definir o cambiar la contraseña.

    Con el segundo factor activo, la sesión tiene que haberlo verificado en los últimos 10
    minutos (TB-CEM-3). Sin segundo factor, cambiar una contraseña existente pide la actual."""

    model_config = ConfigDict(frozen=True)

    contrasena_nueva: str = Field(
        min_length=LARGO_MINIMO_CONTRASENA,
        max_length=LARGO_MAXIMO_CONTRASENA,
        json_schema_extra=_CONTRASENA,
    )
    contrasena_actual: str | None = Field(
        default=None, max_length=LARGO_MAXIMO_CONTRASENA, json_schema_extra=_CONTRASENA
    )


class EstadoContrasena(BaseModel):
    """`GET /api/v1/auth/contrasena`."""

    model_config = ConfigDict(frozen=True)

    definida: bool
    cambiada_en: datetime | None = None


class ContrasenaGuardada(BaseModel):
    """Respuesta de `PUT /api/v1/auth/contrasena`."""

    model_config = ConfigDict(frozen=True)

    cambiada_en: datetime
    sesiones_cerradas: int = Field(
        ge=0,
        description="Al cambiar una contraseña existente se cierran las sesiones de los otros"
        " dispositivos; al definirla por primera vez, ninguna.",
    )
