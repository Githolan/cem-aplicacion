"""Cotejar con el banco (RF12; `0074`): a mano o con el extracto en CSV.

Diseño: `doc/diseno/Rediseno_Circuito_Pagos_2026-11-06.md` del repositorio CEM, §10. Cotejar dice
que un pago ya conciliado salió del banco: no cambia la orden y no mueve dinero. De un extracto solo
se guardan la fecha, el importe y el número de cada débito.
"""

from datetime import date
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

LIMITE_COTEJO_POR_DEFECTO = 50
LIMITE_COTEJO_MAXIMO = 100
MAXIMO_PAGOS_POR_COTEJO = LIMITE_COTEJO_MAXIMO
"""Lo que entra en una pantalla de tildes."""
MAXIMO_FILAS_RESULTADO = 200
"""Lo que se lista de cada lado del resultado: el total va aparte."""
MAXIMO_FILAS_NO_LEIDAS = 20
LARGO_MAXIMO_COLUMNA = 80
FORMATO_COLUMNA = r"^[^\x00-\x1f\x7f=+@-][^\x00-\x1f\x7f]{0,79}$"
"""El nombre de una columna, como viene en el archivo. Empieza sin los caracteres con que una
planilla arma una fórmula: un encabezado así no se guarda."""

NombreColumna = Annotated[str, Field(pattern=FORMATO_COLUMNA, max_length=LARGO_MAXIMO_COLUMNA)]


class SignoExtracto(StrEnum):
    """Cómo se reconoce un débito: lo positivo de una columna con signo es un crédito."""

    CON_SIGNO = "con_signo"
    """Una columna de importes: los débitos llevan signo menos."""
    SIN_SIGNO = "sin_signo"
    """Una columna solo de débitos: sin signo, y vacía en las líneas que no lo son."""


class ColumnasExtracto(BaseModel):
    """Qué columna del archivo es cada dato. Queda guardado por edificio."""

    model_config = ConfigDict(frozen=True)

    fecha: NombreColumna
    importe: NombreColumna
    numero_operacion: NombreColumna | None = Field(
        default=None, description="Sin ella se compara por importe y fecha."
    )
    signo: SignoExtracto


class PagoPorCotejar(BaseModel):
    """Un pago conciliado conforme que falta cotejar. Sin la cuenta."""

    model_config = ConfigDict(frozen=True)

    pago_id: UUID
    orden_id: UUID
    proveedor: str
    concepto: str
    numero_operacion: str
    fecha_operacion: date
    importe_centavos: int
    moneda: str
    puede_cotejar: bool = Field(
        description="No, si quien pide preparó la orden, vio la cuenta o registró el pago."
    )


class ListaCotejo(BaseModel):
    """`GET …/pagos/cotejo`: del pago registrado primero al último."""

    model_config = ConfigDict(frozen=True)

    elementos: list[PagoPorCotejar]
    siguiente: UUID | None = Field(
        description="El `despues` de la página siguiente; sin él, no hay más."
    )
    tope_bytes: int = Field(description="El tamaño máximo de un extracto.")
    maximo_pagos: int = Field(description="Cuántos pagos entran en un pedido de cotejar a mano.")


class PagosACotejar(BaseModel):
    model_config = ConfigDict(frozen=True)

    pagos: list[UUID] = Field(min_length=1, max_length=MAXIMO_PAGOS_POR_COTEJO)


class PagosCotejados(BaseModel):
    model_config = ConfigDict(frozen=True)

    cotejados: int = Field(description="Los que quedaron cotejados con este pedido.")


class VistaExtracto(BaseModel):
    """`POST …/pagos/cotejo/columnas`: lo que hace falta para elegir. No guarda nada."""

    model_config = ConfigDict(frozen=True)

    columnas: list[str]
    muestra: list[str] = Field(
        description="La primera línea de datos, columna por columna, sin números de cuenta."
    )
    signo_sugerido: SignoExtracto
    columnas_guardadas: ColumnasExtracto | None = Field(
        description="Las del edificio, si este archivo las tiene todas."
    )


class MovimientoSinRegistrar(BaseModel):
    """Un débito del banco sin pago en Consorcio en Mano. El extracto no dice la moneda."""

    model_config = ConfigDict(frozen=True)

    fecha: date
    importe_centavos: int
    numero_operacion: str | None


class ResultadoCotejo(BaseModel):
    """`POST …/pagos/cotejo/extracto`. Lo que coincide se cuenta; se lista lo que hay que mirar.

    `aca_no_banco` son los pagos conformes sin cotejar con fecha dentro de lo que cubre el extracto:
    de otro período no se puede decir nada."""

    model_config = ConfigDict(frozen=True)

    desde: date
    hasta: date
    movimientos_nuevos: int
    movimientos_repetidos: int = Field(description="Ya estaban de un extracto anterior.")
    cotejados: int = Field(description="Coincidieron y quedaron cotejados con este extracto.")
    ya_cotejados: int
    esperan_conciliar: int = Field(description="Coinciden con un pago que falta conciliar.")
    para_otra_persona: int = Field(
        description="Coinciden con un pago que quien subió el extracto no puede cotejar."
    )
    lineas_no_leidas: int
    filas_no_leidas: list[int] = Field(
        description=f"Las primeras {MAXIMO_FILAS_NO_LEIDAS}, por su número de línea en el archivo."
    )
    aca_no_banco: list[PagoPorCotejar]
    total_aca_no_banco: int
    banco_no_aca: list[MovimientoSinRegistrar]
    total_banco_no_aca: int
