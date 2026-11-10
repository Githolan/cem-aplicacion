"""Panel del Consejo (H4a, corte 1; RF48), en solo lectura.

Diseño: `doc/diseno/H4a_1_Panel_del_Consejo.md` del repositorio CEM.

- **Una acción, una vista** (tabla de verdad, §1.2): `GET …/panel` es `fiscalizacion.ver_panel`,
  el panel completo del Consejo y la administración. El resumen del propietario
  (`fiscalizacion.ver_resumen`) no está en este corte.
- **Señales con su base de cálculo, no conclusiones** (RN33): cada fila dice de dónde sale.
- Todo sale de las órdenes de pago. Sin cuenta, CUIT, contacto, personas ni motivos.
"""

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from cem_contracts.egresos import EstadoOrdenPago


class OrdenesPorEstado(BaseModel):
    """Órdenes de un estado y una moneda, por el importe de su versión vigente. Sin borradores."""

    model_config = ConfigDict(frozen=True)

    estado: EstadoOrdenPago
    moneda: str
    cantidad: int = Field(ge=1)
    importe_centavos: int = Field(ge=1)


class GastoPagado(BaseModel):
    """Lo pagado en un mes, con un rubro, con o sin el fondo de reserva, en una moneda.

    Importe y fecha son los que usa la liquidación: lo real si el Consejo lo dio por pagado, y la
    fecha de la operación conciliada o el día del consorcio en que se pagó."""

    model_config = ConfigDict(frozen=True)

    periodo: date = Field(
        description="El primer día del mes del pago, en el calendario del consorcio."
    )
    rubro: str | None = Field(
        description="El rubro del proveedor en la versión con que se pagó; null si no tenía."
    )
    fondo_reserva: bool = Field(description="Si la versión pagada usaba el fondo de reserva.")
    moneda: str
    cantidad: int = Field(ge=1)
    importe_centavos: int = Field(ge=1)


class ComprobanteFaltante(BaseModel):
    """Una orden aprobada o más adelante cuya versión vigente no tiene archivo de factura."""

    model_config = ConfigDict(frozen=True)

    orden_id: UUID
    estado: EstadoOrdenPago
    proveedor: str = Field(description="La razón social, como figura en la versión vigente.")
    concepto: str
    importe_centavos: int
    moneda: str
    actualizada_en: datetime


class PanelFiscalizacion(BaseModel):
    """`GET …/panel`: las tres lecturas, de una misma foto de la base."""

    model_config = ConfigDict(frozen=True)

    ordenes_por_estado: list[OrdenesPorEstado]
    gasto_pagado: list[GastoPagado] = Field(
        description="Por período, de lo más viejo a lo más nuevo."
    )
    comprobantes_faltantes: list[ComprobanteFaltante] = Field(
        description="De lo último que cambió a lo más viejo."
    )
