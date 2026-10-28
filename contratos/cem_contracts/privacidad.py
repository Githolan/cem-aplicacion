"""Casos de privacidad del titular dentro de la aplicación (RF37, corte 2d-2).

Diseño: `doc/diseno/H2d_RF37_Diseno.md` del repositorio CEM, §5 y §9.1. Decisión del propietario
del 28-10-2026: la confirmación de quien tiene contraseña o TOTP y la copia de los datos propios
van **solo dentro de la aplicación, al titular autenticado**. La copia no se manda por correo ni
tiene una URL pública, y soporte no la descarga ni actúa por el titular.

Cada pedido pide reautenticación reciente: la contraseña en el mismo pedido si la persona la
tiene; si no, haber entrado con el enlace hace menos de 10 minutos; y, con TOTP activo, haberlo
verificado en esta sesión hace menos de 10 minutos.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from cem_contracts.contrasena import LARGO_MAXIMO_CONTRASENA

_CONTRASENA = {"format": "password", "writeOnly": True}
LARGO_MAXIMO_CODIGO_CASO = 64
"""El código son 26 caracteres base32 en grupos con guiones; se admite con espacios."""


class DerechoPrivacidad(StrEnum):
    ACCESO = "acceso"
    RECTIFICACION_NOMBRE = "rectificacion_nombre"
    SUPRESION = "supresion"


class EstadoCaso(StrEnum):
    ABIERTO = "abierto"
    CONFIRMADO = "confirmado"
    RESUELTO = "resuelto"
    RECHAZADO = "rechazado"


class CasoPrivacidad(BaseModel):
    """Un caso del titular, sin el código, la corroboración ni la referencia del pedido."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    alcance_consorcio_id: UUID | None = Field(
        description="Nulo: el alcance es la cuenta (los datos globales de la persona)."
    )
    derecho: DerechoPrivacidad
    estado: EstadoCaso
    abierto_en: datetime
    codigo_vence_en: datetime | None = Field(
        description="Hasta cuándo sirve el código del correo; nulo si no hay o ya se usó."
    )
    confirmado_en: datetime | None = None
    copia_entregada_en: datetime | None = Field(
        default=None, description="Primera entrega de la copia; se puede volver a pedir."
    )
    resuelto_en: datetime | None = None


class ListaCasosPrivacidad(BaseModel):
    """`GET /api/v1/privacidad/casos`: los más nuevos primero."""

    model_config = ConfigDict(frozen=True)

    elementos: list[CasoPrivacidad]


class Reautenticacion(BaseModel):
    model_config = ConfigDict(frozen=True)

    contrasena: str | None = Field(
        default=None,
        min_length=1,
        max_length=LARGO_MAXIMO_CONTRASENA,
        json_schema_extra=_CONTRASENA,
        description="Obligatoria si la persona tiene contraseña.",
    )


class ConfirmacionCaso(Reautenticacion):
    """`POST /api/v1/privacidad/casos/{caso_id}/confirmar`."""

    codigo: str = Field(
        min_length=1,
        max_length=LARGO_MAXIMO_CODIGO_CASO,
        description="El código que llegó al correo de la cuenta.",
    )


class PedidoCopia(Reautenticacion):
    """`POST /api/v1/privacidad/casos/{caso_id}/copia`."""


class CopiaDatosPropios(BaseModel):
    """La copia de los datos propios (§5). Se puede volver a pedir mientras el caso siga
    confirmado; queda resuelto cuando el titular avisa que la guardó (`…/recibida`).

    `datos` lleva, con alcance `cuenta`, `persona`, `sesiones`, `credenciales` y
    `casos_privacidad`; con un consorcio, `vinculos`, `mandatos`, `invitaciones`,
    `invitaciones_mandato`, `desasociaciones_mandato`, `eventos`, `documentos`,
    `versiones_subidas` e `identidad_humana`. Nunca datos de otras personas."""

    model_config = ConfigDict(frozen=True)

    caso_id: UUID
    generada_en: datetime
    alcance_consorcio_id: UUID | None
    datos: dict[str, Any]
