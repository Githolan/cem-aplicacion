"""Padrón mínimo y confirmación de vínculos (H2b 2b-4; D6, criterio 6; P4 y P5 del propietario).

El padrón muestra lo mínimo de D6 para quien tiene `padron.ver`: unidad, nombre, carácter,
vigencia y si el vínculo está confirmado. Nunca correo ni teléfono. Solo vínculos vigentes o
futuros: los terminados no salen.

Confirmar un vínculo acredita el carácter de la persona sobre la unidad según el título que se
refiere (escritura, contrato de locación u otro), no su identidad civil. Si el título no lo
acredita, el vínculo queda sin confirmar. Nadie confirma su propio vínculo ni el que registró.
"""

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from cem_contracts.estados import TipoVinculo

LARGO_MAXIMO_REFERENCIA = 300
"""La migración 0014 impone el mismo tope."""
FORMATO_REFERENCIA = r"^[^\x00-\x1f\x7f]*\S[^\x00-\x1f\x7f]*$"
"""Al menos un carácter visible y ningún control."""


class FilaPadron(BaseModel):
    """Un vínculo del padrón, con los datos mínimos de D6."""

    model_config = ConfigDict(frozen=True)

    vinculo_id: UUID
    unidad_id: UUID
    designacion: str = Field(description="Designación de la unidad.", examples=["3° B"])
    nombre: str = Field(description="Nombre de la persona del vínculo.")
    tipo: TipoVinculo
    vigente_desde: date
    vigente_hasta: date | None = Field(description="Exclusivo: ese día ya no vale.")
    confirmado: bool


class Padron(BaseModel):
    """`GET /api/v1/consorcios/{consorcio_id}/padron`: vigentes y futuros, por unidad."""

    model_config = ConfigDict(frozen=True)

    elementos: list[FilaPadron]


class ConfirmacionVinculo(BaseModel):
    """`POST /api/v1/consorcios/{consorcio_id}/vinculos/{vinculo_id}/confirmar`."""

    model_config = ConfigDict(frozen=True)

    referencia: str = Field(
        max_length=LARGO_MAXIMO_REFERENCIA,
        pattern=FORMATO_REFERENCIA,
        description="Referencia del título que acredita el carácter (escritura, contrato de"
        " locación u otro). Queda en el vínculo, no en la auditoría.",
        examples=["Escritura 1234, registro notarial 56"],
    )


class VinculoConfirmado(BaseModel):
    """Respuesta de la confirmación."""

    model_config = ConfigDict(frozen=True)

    vinculo_id: UUID
    unidad_id: UUID
    tipo: TipoVinculo
    confirmado: bool = Field(description="Siempre verdadero en esta respuesta.")
    confirmado_en: datetime
