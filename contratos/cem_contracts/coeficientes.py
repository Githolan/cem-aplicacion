"""Lectura de los esquemas de coeficientes y sus reglas de reparto (RF05; D4).

Diseño: `doc/diseno/H2b_5_Diseno.md` del repositorio CEM, §3: por la API, solo lectura, para
quien tiene `padron.ver` o `coeficientes.proponer`. Proponer y aprobar siguen fuera de la API.

Cada coeficiente sale si quien lee tiene `padron.ver` sobre su unidad
(`coeficientes.ver_por_unidad`), `coeficientes.proponer` sobre su grupo
(`coeficientes.ver_por_grupo`) o un vínculo confirmado con esa unidad
(`coeficientes.ver_propia_unidad`, decisión del propietario del 25-10-2026). Un esquema sale si
quien lee ve alguno de sus coeficientes o lo lee por su grupo. Los porcentajes salen **tal como los guarda la base** (D4): si la fuente suma
99,96 %, se lee 99,96 %. La referencia de la fuente, la decisión que respalda cada versión y quién
la registró no salen.
"""

from datetime import date
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class MetodoReparto(StrEnum):
    """`nucleo.metodo_reparto` (0021)."""

    PROPORCIONAL_A_COEFICIENTES = "proporcional_a_coeficientes"


class TratamientoCentavos(StrEnum):
    """`nucleo.tratamiento_centavos` (0021): restos mayores, con desempate por unidad."""

    RESTOS_MAYORES = "restos_mayores"


class CoeficienteUnidad(BaseModel):
    model_config = ConfigDict(frozen=True)

    unidad_id: UUID
    designacion: str = Field(description="Designación de la unidad.", examples=["3° B"])
    porcentaje: str = Field(
        description="Tal como lo guarda la base, como texto decimal con 6 decimales (D4).",
        examples=["33.320000"],
    )


class ReglaReparto(BaseModel):
    """Una versión de la regla de un esquema. Ninguna versión cambia: se agrega otra."""

    model_config = ConfigDict(frozen=True)

    version: int
    metodo: MetodoReparto
    centavos: TratamientoCentavos


class EsquemaCoeficientes(BaseModel):
    """Una versión del esquema de un grupo de gasto, inalterable."""

    model_config = ConfigDict(frozen=True)

    grupo: str = Field(examples=["expensas_comunes"])
    version: int
    vigente_desde: date
    vigente_hasta: date | None = Field(
        description="Exclusivo: el día en que empieza la versión siguiente del grupo. Sin fecha,"
        " es la versión vigente o una futura."
    )
    reglas: list[ReglaReparto] = Field(description="De la más vieja a la más nueva.")
    coeficientes: list[CoeficienteUnidad] = Field(
        description="Solo los que quien lee puede ver, por designación de la unidad. Una versión"
        " muestra lo que se guardó: también una unidad borrada después."
    )


class ListaEsquemasCoeficientes(BaseModel):
    """`GET /api/v1/consorcios/{consorcio_id}/coeficientes`: por grupo y versión. Vacía: no hay
    esquemas o quien lee no ve ninguno."""

    model_config = ConfigDict(frozen=True)

    elementos: list[EsquemaCoeficientes]
