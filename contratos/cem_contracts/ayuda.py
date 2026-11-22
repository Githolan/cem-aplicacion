"""Ayuda por una persona autorizada (RF53; `0127`).

Principio aprobado por el propietario (decisiones del 25-10-2026, §1.7): quien ayuda mira y
prepara, y nunca sustituye la voluntad de la persona asistida en un acto decisorio. Usa su propia
cuenta (H2b, criterio 5) y la auditoría registra quién actuó y en representación de quién.

- **Quién autoriza** (`ayuda.otorgar`): un propietario con el vínculo confirmado, en un edificio,
  a otra persona con cuenta, con un alcance explícito. Revoca solo quien la dio (`ayuda.revocar`).
  Nada se cambia en el lugar: para cambiar el alcance se revoca y se da otra.
- **El alcance** es una lista cerrada (`AlcanceAyuda`), sacada de lo que §1.7 dice que se puede:
  consultar lo propio y preparar reclamos. Cada política declara si admite ayuda y con qué
  alcance (`Politica.ayuda`); el evaluador niega toda otra acción a quien ayuda, aunque la persona
  asistida la tenga (votar, firmar, aprobar, pagar, facultades económicas, permisos, modo,
  mandatos, verificar identidades, privacidad).
- **Cómo se ayuda:** la persona elige «Ayudar a …» y cada pedido al edificio lleva el encabezado
  `ENCABEZADO_AYUDA` con la autorización. Sin él, actúa como ella misma.
"""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from cem_contracts.identidad import FORMATO_CORREO, LARGO_MAXIMO_CORREO

ENCABEZADO_AYUDA = "X-CEM-Ayuda"
"""El encabezado con el id de la autorización que se está usando."""


class AlcanceAyuda(StrEnum):
    """Lo que una autorización deja hacer en nombre de la persona asistida (§1.7, «puede»)."""

    ORIENTARSE = "orientarse"
    """Saber qué se puede hacer en el edificio (`consorcios.mis_acciones`). Va con toda
    autorización vigente y no se otorga: no da ningún dato de la persona."""
    CONSULTAR_EXPENSAS = "consultar_expensas"
    """Ver «Mis expensas» (lo que la liquidación oficial dice de sus unidades y el resumen del
    edificio) y sus reclamos."""
    PREPARAR_RECLAMOS = "preparar_reclamos"
    """Preparar y presentar un reclamo sobre la liquidación de una unidad suya, y retirarlo
    mientras está presentado. Pide `consultar_expensas`: se reclama sobre lo que se ve."""


ALCANCES_OTORGABLES: tuple[AlcanceAyuda, ...] = (
    AlcanceAyuda.CONSULTAR_EXPENSAS,
    AlcanceAyuda.PREPARAR_RECLAMOS,
)
"""Los que se eligen al autorizar; la base tiene la misma lista (`0127`)."""
REQUIERE: dict[AlcanceAyuda, AlcanceAyuda] = {
    AlcanceAyuda.PREPARAR_RECLAMOS: AlcanceAyuda.CONSULTAR_EXPENSAS,
}


class AyudaNueva(BaseModel):
    """`POST …/ayudas`: a quién y con qué alcance."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    correo: str = Field(
        max_length=LARGO_MAXIMO_CORREO,
        pattern=FORMATO_CORREO,
        description="La dirección de la cuenta de la persona que va a ayudar.",
        examples=["beto@ejemplo.com"],
    )
    alcances: list[AlcanceAyuda] = Field(
        min_length=1,
        description="Lo que puede hacer en nombre de quien autoriza. `preparar_reclamos` pide"
        " también `consultar_expensas`.",
    )

    @model_validator(mode="after")
    def _alcances(self) -> "AyudaNueva":
        if len(set(self.alcances)) != len(self.alcances):
            raise ValueError("alcances repetidos")
        if not set(self.alcances) <= set(ALCANCES_OTORGABLES):
            raise ValueError("ese alcance no se otorga")
        for alcance, pide in REQUIERE.items():
            if alcance in self.alcances and pide not in self.alcances:
                raise ValueError(f"{alcance} pide también {pide}")
        return self


class AyudaOtorgada(BaseModel):
    """Una autorización que dio la persona, vigente o revocada (la historia no se borra)."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    autorizado_nombre: str
    alcances: list[AlcanceAyuda]
    otorgada_en: datetime
    revocada_en: datetime | None


class ListaAyudasOtorgadas(BaseModel):
    model_config = ConfigDict(frozen=True)

    elementos: list[AyudaOtorgada] = Field(description="La más nueva primero.")
    alcances_otorgables: list[AlcanceAyuda]


class AyudaRecibida(BaseModel):
    """Una autorización vigente que la persona de la sesión puede usar para ayudar."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    consorcio_id: UUID
    consorcio_nombre: str
    otorgante_nombre: str
    alcances: list[AlcanceAyuda]
    otorgada_en: datetime


class ListaAyudasRecibidas(BaseModel):
    model_config = ConfigDict(frozen=True)

    elementos: list[AyudaRecibida]
