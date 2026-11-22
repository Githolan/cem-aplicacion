"""Reclamos sobre la liquidación oficial (RF34, Modo Consejo; `0123`), con el borrador sin conexión
de la pantalla (RF38, PA16).

Diseño P0 S7 del repositorio CEM y decisiones del 22-11-2026.

- **Quién presenta** (`reclamos_liquidacion.presentar`): el propietario con el vínculo confirmado,
  sobre una unidad suya (`misma_unidad`). Inquilinos y ocupantes, no en este corte.
- **Quién atiende** (`reclamos_liquidacion.atender`): quien tiene `reclamos.atender`, que entra en
  los paquetes `miembro_consejo` y `administrador` y no se da a los mandatos ya registrados.
- **Qué cita:** una liquidación oficial, una unidad propia y, si quiere, un dato oficial de esa
  unidad (`saldo_unidad` o `importe_unidad`). Nunca cambia un importe ni la liquidación (RF34).
- **Estados:** `presentado` y después uno solo de `respondido`, `derivado_a_solicitud` o
  `retirado`. Nada se cambia: cada paso es una fila de la historia.
- **El borrador (RF38, PA16)** vive en el teléfono; el servidor revalida todo al recibirlo. Si la
  liquidación citada fue reemplazada desde el borrador, se niega (`liquidacion_reemplazada`) salvo
  que el pedido diga que reclama sobre la reemplazada.
"""

from datetime import date, datetime
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

LARGO_TEXTO = 2000
LARGO_MOTIVO = 500
"""La base tiene el mismo control (`0123`)."""
CON_RENGLONES = r"^[^\x00-\x09\x0b-\x1f\x7f]*$"
"""Ningún carácter de control salvo el salto de renglón."""
UN_RENGLON = r"^[^\x00-\x1f\x7f]*$"

TextoReclamo = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=LARGO_TEXTO,
                      pattern=CON_RENGLONES),
]  # fmt: skip
MotivoRetiro = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=LARGO_MOTIVO,
                      pattern=UN_RENGLON),
]  # fmt: skip


class CampoCitado(StrEnum):
    """El dato oficial de la unidad que el reclamo cita (`importacion.campos`)."""

    SALDO_UNIDAD = "saldo_unidad"
    """El saldo anterior, los pagos, la deuda, el interés y el disponible."""
    IMPORTE_UNIDAD = "importe_unidad"
    """El importe del período: los grupos, otros gastos, el redondeo y el total."""


class EstadoReclamo(StrEnum):
    PRESENTADO = "presentado"
    RESPONDIDO = "respondido"
    DERIVADO_A_SOLICITUD = "derivado_a_solicitud"
    """Lo atiende una solicitud formal (RF49) que preparó quien atiende."""
    RETIRADO = "retirado"


class ReclamoNuevo(BaseModel):
    """`POST …/reclamos-liquidacion`: también lo que manda el borrador al volver la conexión."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    liquidacion_id: UUID = Field(description="La liquidación oficial que se cita.")
    unidad_id: UUID = Field(description="Una unidad donde la persona es propietaria confirmada.")
    campo: CampoCitado | None = Field(
        default=None, description="El dato oficial de la unidad que se cita, si cita uno."
    )
    texto: TextoReclamo
    acepta_reemplazada: bool = Field(
        default=False,
        description="Reclamar sobre la liquidación citada aunque haya sido reemplazada. Sin esto,"
        " una reemplazada se niega con `liquidacion_reemplazada`.",
    )


class RespuestaReclamo(BaseModel):
    """`POST …/reclamos-liquidacion/{r}/respuesta`: cierra el reclamo."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    texto: TextoReclamo


class RetiroReclamo(BaseModel):
    """`POST …/reclamos-liquidacion/{r}/retiro`: solo quien lo presentó, mientras está
    presentado."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    motivo: MotivoRetiro


class PasoReclamo(BaseModel):
    """Un paso de la historia del reclamo."""

    model_config = ConfigDict(frozen=True)

    estado: EstadoReclamo
    texto: str | None = Field(description="La respuesta o el motivo del retiro.")
    solicitud_id: UUID | None = Field(description="La solicitud formal que lo atiende.")
    solicitud_numero: int | None
    registrado_por_nombre: str
    registrado_en: datetime


class AccionReclamo(StrEnum):
    """Lo que la persona puede hacer con el reclamo ahora (la pantalla no ofrece otra cosa)."""

    RESPONDER = "responder"
    PASAR_A_SOLICITUD = "pasar_a_solicitud"
    RETIRAR = "retirar"


class ReclamoResumen(BaseModel):
    """Una fila de la lista."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    liquidacion_id: UUID
    periodo: date
    liquidacion_vigente: bool = Field(
        description="Si la liquidación citada sigue siendo la oficial de su período."
    )
    unidad_id: UUID
    designacion: str
    campo: CampoCitado | None
    presentado_por_nombre: str
    presentado_en: datetime
    estado: EstadoReclamo
    propio: bool = Field(description="Si lo presentó la persona que lee.")


class Reclamo(ReclamoResumen):
    """`GET …/reclamos-liquidacion/{r}` y la respuesta de cada acto."""

    texto: str
    historia: list[PasoReclamo] = Field(description="Del primer paso al último.")
    acciones: list[AccionReclamo]


class ListaReclamos(BaseModel):
    """`GET …/reclamos-liquidacion`: el más nuevo primero. Quien atiende ve todos; el propietario,
    los que presentó."""

    model_config = ConfigDict(frozen=True)

    elementos: list[ReclamoResumen]
    atiende: bool
    largo_texto: int
    largo_motivo: int
