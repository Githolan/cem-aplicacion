"""Pagos informados por los vecinos, primer corte (PI-S1; `0129`).

Diseño: `doc/diseno/Pagos_Informados.md` del repositorio CEM (reglas PI-1, PI-2, PI-3 y PI-6).
CEM no mueve dinero: quien pagó las expensas de una unidad deja la constancia con el comprobante,
para que el Consejo la confirme (PI-S2) y se coteje con la liquidación oficial (PI-S3).

- **Quién informa** (`pagos_informados.informar`): quien tiene un vínculo confirmado de
  propietario, inquilino u ocupante con la unidad, en los tres modos, o quien lo ayuda con el
  alcance `preparar_reclamos` (RF53). Ninguna facultad alcanza.
- **En dos pasos:** primero se sube el comprobante (`POST …/pagos-informados/comprobantes`, el
  archivo crudo en el cuerpo, como en Documentos), que entra en cuarentena hasta que el antivirus
  lo acepta; después se informa el pago citando esa versión. En efectivo se puede informar sin
  comprobante y sin número de operación.
- **Un solo registro por pago (PA07, RN08, PI-3):** si ya hay uno con la misma unidad y el mismo
  número de operación (sin espacios, puntos ni guiones, sin distinguir mayúsculas) o con el mismo
  archivo, no se crea otro: se responde 200 con el existente y `ya_informado`. La primera vez,
  201. Un pago rechazado o anulado (PI-S2) no cuenta.
- **Quién lee** (`pagos_informados.ver`): quien lo informó y quien tiene el vínculo que cuenta con
  esa unidad; quien tiene la facultad `fiscalizacion.ver_panel`, todos los del consorcio.
"""

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from cem_contracts.documentos import EstadoVersion, VersionCargada

LARGO_NUMERO_OPERACION = 60
"""La base tiene el mismo control (`0129`)."""
UN_RENGLON_CON_ALGO = r"^[^\x00-\x1f\x7f]*[0-9A-Za-z][^\x00-\x1f\x7f]*$"
"""Un renglón con al menos una letra o un dígito: hecho solo de separadores no es un número."""


class MedioPago(StrEnum):
    TRANSFERENCIA = "transferencia"
    DEPOSITO = "deposito"
    EFECTIVO = "efectivo"
    MERCADO_PAGO = "mercado_pago"
    OTRO = "otro"


MEDIO_SIN_COMPROBANTE = MedioPago.EFECTIVO
"""El único medio que admite informar sin comprobante y sin número de operación (PI-2)."""


class EstadoPagoInformado(StrEnum):
    """Hoy solo `informado`; PI-S2 suma `confirmado`, `rechazado` y `anulado`."""

    INFORMADO = "informado"


ESTADOS_QUE_NO_CUENTAN = ("rechazado", "anulado")
"""Los estados de PI-S2 que no cuentan para «un solo registro por pago»: la base los excluye de
sus índices únicos (`0129`)."""


class OrigenPagoInformado(StrEnum):
    APP = "app"
    CORREO = "correo"
    """La entrada por correo llega en PI-S4."""


NumeroOperacion = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=LARGO_NUMERO_OPERACION,
                      pattern=UN_RENGLON_CON_ALGO),
]  # fmt: skip


class PagoInformadoNuevo(BaseModel):
    """`POST …/pagos-informados`: un pago que la persona hizo por una unidad."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    unidad_id: UUID
    importe: Decimal = Field(gt=0, max_digits=18, decimal_places=2, description="En pesos.")
    fecha_pago: date = Field(description="El día en que se pagó; no posterior a hoy.")
    medio: MedioPago
    numero_operacion: NumeroOperacion | None = Field(
        default=None,
        description="El de la transferencia, el depósito o el pago; obligatorio salvo en efectivo.",
    )
    comprobante_version_id: UUID | None = Field(
        default=None,
        description="La versión que devolvió `POST …/pagos-informados/comprobantes` para esta"
        " unidad; obligatoria salvo en efectivo. Puede estar todavía en cuarentena.",
    )

    @model_validator(mode="after")
    def _del_medio(self) -> "PagoInformadoNuevo":
        if self.medio is not MEDIO_SIN_COMPROBANTE:
            if self.comprobante_version_id is None:
                raise ValueError("sin comprobante solo se informa un pago en efectivo")
            if self.numero_operacion is None:
                raise ValueError("sin número de operación solo se informa un pago en efectivo")
        return self


class ComprobantePagoSubido(BaseModel):
    """`POST …/pagos-informados/comprobantes`: el documento y su versión en cuarentena."""

    model_config = ConfigDict(frozen=True)

    documento_id: UUID
    archivo: VersionCargada


class PagoInformado(BaseModel):
    """Un pago informado, como lo ve quien puede verlo."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    unidad_id: UUID
    unidad_designacion: str
    importe: Decimal
    fecha_pago: date
    medio: MedioPago
    numero_operacion: str | None
    comprobante_version_id: UUID | None
    comprobante_estado: EstadoVersion | None = Field(
        description="Cómo va el análisis del comprobante; nulo sin comprobante."
    )
    origen: OrigenPagoInformado
    estado: EstadoPagoInformado
    corrige_a: UUID | None = Field(
        description="El pago rechazado o anulado que este corrige (PI-S2); nulo en este corte."
    )
    informado_por_nombre: str
    informado_en: datetime
    propio: bool = Field(description="Si lo informó quien pregunta.")


class PagoInformadoRespuesta(PagoInformado):
    """La respuesta de `POST …/pagos-informados`: 201 con `ya_informado` falso si es nuevo; 200 con
    el existente y `ya_informado` verdadero si ese pago ya estaba informado (PA07)."""

    ya_informado: bool


class ListaPagosInformados(BaseModel):
    """`GET …/pagos-informados`: los que la persona puede ver, del más nuevo al más viejo."""

    model_config = ConfigDict(frozen=True)

    elementos: list[PagoInformado]
    largo_numero_operacion: int
