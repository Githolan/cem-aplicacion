"""El presupuesto del consorcio y los desvíos por rubro (RF15 en su forma de Modo Consejo, RF48;
diseño P0 S9; `0128`).

- **Quién registra** (`presupuesto.registrar`): quien tiene la facultad, que entra en los paquetes
  `miembro_consejo` y `administrador` y no se da a los mandatos ya registrados. Solo Modo Consejo
  en este corte. La ayuda por una persona autorizada no la admite (RF53): es decisorio.
- **Qué se registra**: el presupuesto tal como lo aprobó la asamblea, para un rango de períodos,
  con el importe **mensual** de cada rubro. El rubro se identifica por su número, la misma clave
  de los rubros de la liquidación importada (`rubro:<número>`).
- **Nada se edita.** Una corrección es otra versión que reemplaza a una vigente, con su motivo; la
  reemplazada queda como historia. Dos vigentes no cubren el mismo período.
- **Quién lee** (`fiscalizacion.ver_panel`): el Consejo y la administración, en el panel. El
  resumen del propietario no lleva el presupuesto (documento 08, §4: gasto por rubro, por período y
  fondo de reserva).
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

LARGO_APROBACION = 200
LARGO_MOTIVO = 500
LARGO_NOMBRE_RUBRO = 120
MAXIMO_RUBROS = 60
"""La base tiene el mismo control (`0128`)."""
UN_RENGLON = r"^[^\x00-\x1f\x7f]*$"
CON_RENGLONES = r"^[^\x00-\x09\x0b-\x1f\x7f]*$"

Aprobacion = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=LARGO_APROBACION,
                      pattern=UN_RENGLON),
]  # fmt: skip
Motivo = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=LARGO_MOTIVO,
                      pattern=CON_RENGLONES),
]  # fmt: skip
NombreRubro = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=LARGO_NOMBRE_RUBRO,
                      pattern=UN_RENGLON),
]  # fmt: skip


def _primer_dia(fecha: date, campo: str) -> None:
    if fecha.day != 1:
        raise ValueError(f"`{campo}` es el primer día de un mes (AAAA-MM-01)")


class RubroPresupuestado(BaseModel):
    """El importe mensual aprobado para un rubro, por su número en la liquidación."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    numero: int = Field(ge=1, description="El número del rubro en la liquidación importada.")
    nombre: NombreRubro = Field(description="El nombre con que se aprobó.")
    importe: Decimal = Field(
        ge=0, max_digits=18, decimal_places=2, description="Por mes, en pesos."
    )


class PresupuestoNuevo(BaseModel):
    """`POST …/presupuestos`: un presupuesto nuevo, o la versión que reemplaza a uno vigente."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    desde: date = Field(description="El primer período que cubre (`AAAA-MM-01`).")
    hasta: date = Field(description="El último período que cubre, incluido (`AAAA-MM-01`).")
    aprobacion: Aprobacion = Field(
        description="De dónde sale, por ejemplo «Asamblea ordinaria del 12-03-2026»."
    )
    rubros: list[RubroPresupuestado] = Field(min_length=1, max_length=MAXIMO_RUBROS)
    reemplaza_a: UUID | None = Field(
        default=None, description="El presupuesto vigente que esta versión reemplaza."
    )
    motivo: Motivo | None = Field(
        default=None, description="Por qué se reemplaza; obligatorio con `reemplaza_a`."
    )

    @model_validator(mode="after")
    def _coherente(self) -> "PresupuestoNuevo":
        _primer_dia(self.desde, "desde")
        _primer_dia(self.hasta, "hasta")
        if self.hasta < self.desde:
            raise ValueError("`hasta` no puede ser anterior a `desde`")
        if (self.reemplaza_a is None) != (self.motivo is None):
            raise ValueError("el reemplazo lleva su motivo, y el motivo solo va con un reemplazo")
        numeros = [r.numero for r in self.rubros]
        if len(set(numeros)) != len(numeros):
            raise ValueError("cada rubro va una sola vez")
        return self


class Presupuesto(BaseModel):
    """Una versión registrada: la vigente o una reemplazada."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    desde: date
    hasta: date
    aprobacion: str
    rubros: list[RubroPresupuestado] = Field(description="Por número de rubro.")
    reemplaza_a: UUID | None
    motivo: str | None
    reemplazado_por: UUID | None = Field(description="Nula mientras está vigente.")
    registrado_por_nombre: str
    registrado_en: datetime


class ListaPresupuestos(BaseModel):
    """`GET …/presupuestos`: todas las versiones, la más nueva primero."""

    model_config = ConfigDict(frozen=True)

    elementos: list[Presupuesto]
    puede_registrar: bool
    largo_aprobacion: int
    largo_motivo: int
    largo_nombre_rubro: int
    maximo_rubros: int


class DesvioPresupuesto(BaseModel):
    """Un rubro de un período del panel: lo presupuestado por el vigente que cubre el período, lo
    liquidado por la liquidación oficial y el desvío (`0128`). Nada se estima: sin presupuesto, o
    sin el rubro en la liquidación, el importe que falta y el desvío van nulos."""

    model_config = ConfigDict(frozen=True)

    periodo: date
    numero: int | None
    nombre: str | None = Field(
        description="El de la liquidación; si no lo trae, el del presupuesto."
    )
    presupuesto_id: UUID | None = Field(
        description="El presupuesto vigente que cubre el período; nulo si no hay ninguno."
    )
    presupuestado: Decimal | None = Field(description="Nulo: «sin presupuesto» para ese rubro.")
    liquidado: Decimal | None = Field(description="Nulo: la liquidación no trae ese rubro.")
    desvio: Decimal | None = Field(description="Liquidado − presupuestado, con los dos importes.")
    porcentaje: Decimal | None = Field(
        description="Desvío × 100 / presupuestado, a dos decimales; nulo si falta un importe o lo"
        " presupuestado es cero."
    )
