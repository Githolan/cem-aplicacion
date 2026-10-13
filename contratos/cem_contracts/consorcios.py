"""Consorcios y unidades funcionales visibles para una persona."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from cem_contracts.estados import TipoVinculo


class ConsorcioResumen(BaseModel):
    """Un consorcio donde la persona tiene vínculo vigente (selector de edificio)."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    nombre: str
    vinculos: list[TipoVinculo] = Field(description="Vínculos vigentes de la persona en él.")


class Unidad(BaseModel):
    """Unidad funcional del padrón."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    designacion: str = Field(examples=["3° B"])


class ListaUnidades(BaseModel):
    elementos: list[Unidad]
