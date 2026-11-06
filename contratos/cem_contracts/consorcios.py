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
    invitable: bool = Field(
        description="Si la persona puede invitar a esta unidad. Solo para mostrar."
    )


class ListaUnidades(BaseModel):
    elementos: list[Unidad]


class MisAcciones(BaseModel):
    """Lo que la interfaz le muestra a la persona en el consorcio (documento 13, §10.5).

    Solo sirve para mostrar: no concede nada. Cada ruta sigue decidiendo por su cuenta, así que
    una acción de la lista todavía puede negarse por la entidad concreta o pedir el gesto.
    """

    model_config = ConfigDict(frozen=True)

    acciones: list[str] = Field(
        description="Códigos de acción que la persona alcanza en el consorcio, ordenados.",
        examples=[["consorcios.mis_acciones", "unidades.listar"]],
    )
