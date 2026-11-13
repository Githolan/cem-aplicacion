"""Cotejar con el banco (RF12; `0074`): a mano o con el extracto en CSV.

Diseño: `doc/diseno/Rediseno_Circuito_Pagos_2026-11-06.md` del repositorio CEM, §10. Cotejar dice
que un pago ya conciliado salió del banco: no cambia la orden y no mueve dinero. De un extracto solo
se guardan la fecha, el importe y el número de cada débito.
"""

from datetime import date, datetime
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

LIMITE_COTEJO_POR_DEFECTO = 50
LIMITE_COTEJO_MAXIMO = 100
MAXIMO_PAGOS_POR_COTEJO = LIMITE_COTEJO_MAXIMO
"""Lo que entra en una pantalla de tildes."""
MAXIMO_FILAS_RESULTADO = 200
"""Lo que se lista de cada lado del resultado: el total va aparte."""
MAXIMO_FILAS_NO_LEIDAS = 20
MONEDA_SUGERIDA = "ARS"
"""La de un extracto mientras nadie elija otra: el archivo no la dice."""
FORMATO_MONEDA = r"^[A-Z]{3}$"
LARGO_MAXIMO_COLUMNA = 80
LARGO_MAXIMO_MOTIVO = 300
FORMATO_COLUMNA = r"^[^\x00-\x1f\x7f=+@-][^\x00-\x1f\x7f]{0,79}$"
"""El nombre de una columna, como viene en el archivo. Empieza sin los caracteres con que una
planilla arma una fórmula: un encabezado así no se guarda."""

NombreColumna = Annotated[str, Field(pattern=FORMATO_COLUMNA, max_length=LARGO_MAXIMO_COLUMNA)]
MonedaExtracto = Annotated[str, Field(pattern=FORMATO_MONEDA, examples=[MONEDA_SUGERIDA])]


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
    moneda: MonedaExtracto = Field(
        default=MONEDA_SUGERIDA,
        description="El archivo no la dice: un débito solo coincide con un pago en su moneda.",
    )


class PagoPorCotejar(BaseModel):
    """Lo que salió del banco y falta cotejar: un pago conciliado conforme o una diferencia ya
    resuelta, por su importe real. Sin la cuenta."""

    model_config = ConfigDict(frozen=True)

    pago_id: UUID = Field(description="El id del registro o de la diferencia.")
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
    moneda_extracto: str = Field(description="La guardada del edificio, o la sugerida si no hay.")


class PagoCotejado(PagoPorCotejar):
    """Un cotejo vigente. `puede_cotejar` dice también si quien pide puede anularlo: las reglas son
    las mismas."""

    origen: str = Field(description="`manual` o `extracto`.")
    cotejado_en: datetime


class ListaCotejados(BaseModel):
    """`GET …/pagos/cotejo/cotejados`."""

    model_config = ConfigDict(frozen=True)

    elementos: list[PagoCotejado]
    siguiente: UUID | None = Field(
        description="El `despues` de la página siguiente; sin él, no hay más."
    )


class AnulacionCotejo(BaseModel):
    """`POST …/pagos/cotejo/{pago_id}/anulacion`: el cotejo no se borra, queda con su motivo."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    motivo: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=LARGO_MAXIMO_MOTIVO)
    ]


class CotejoAnulado(BaseModel):
    model_config = ConfigDict(frozen=True)

    pago_id: UUID


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
    columnas_reconocidas: ColumnasExtracto | None = Field(
        description="Las que se reconocen solas por su nombre (P-23): una sola columna de fecha,"
        " una sola de importe o de débitos y, si hay, una sola de número de operación. Nula si"
        " falta alguna o hay más de una que puede ser: entonces se pregunta."
    )


class MovimientoSinRegistrar(BaseModel):
    """Un débito del banco sin pago en Consorcio en Mano, en la moneda elegida para el extracto."""

    model_config = ConfigDict(frozen=True)

    fecha: date
    importe_centavos: int
    moneda: str
    numero_operacion: str | None


class MovimientoSinExplicar(BaseModel):
    """Un débito de un extracto ya subido que ningún pago de Consorcio en Mano explica (pregunta 8
    del propietario, 13-11-2026). Lo ven quien concilia y, solo para leer, la administración."""

    model_config = ConfigDict(frozen=True)

    movimiento_id: UUID
    fecha: date
    importe_centavos: int
    moneda: str
    numero_operacion: str | None
    visto_en: datetime = Field(description="Cuándo se subió el extracto que lo trajo.")


class ListaSinExplicar(BaseModel):
    """`GET …/pagos/extracto/sin-explicar`: de lo más viejo a lo más nuevo, hasta
    `MAXIMO_FILAS_RESULTADO`. Sin la cuenta ni la descripción del banco: no se guardan."""

    model_config = ConfigDict(frozen=True)

    elementos: list[MovimientoSinExplicar] = Field(max_length=MAXIMO_FILAS_RESULTADO)
    total: int = Field(ge=0)


class NoEsPago(BaseModel):
    """`POST …/pagos/extracto/sin-explicar/{movimiento_id}/no-es-pago`: quien concilia dice que ese
    débito no es el pago de una orden (una comisión, un impuesto del banco). No se borra: queda
    quién, cuándo y el motivo, y sale de lo que el extracto no explica."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    motivo: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=LARGO_MAXIMO_MOTIVO)
    ]


class MovimientoMarcado(BaseModel):
    model_config = ConfigDict(frozen=True)

    movimiento_id: UUID


class MarcaNoEsPago(BaseModel):
    """Un débito con la marca vigente «No es el pago de una orden» (respuesta del propietario,
    13-11-2026). Quien concilia puede retirarla, con motivo: `POST …/no-es-pago/{id}/retiro`, con
    el mismo cuerpo que marcar (`NoEsPago`)."""

    model_config = ConfigDict(frozen=True)

    movimiento_id: UUID
    fecha: date
    importe_centavos: int
    moneda: str
    numero_operacion: str | None
    motivo: str
    marcado_en: datetime


class ListaMarcasNoEsPago(BaseModel):
    """`GET …/pagos/extracto/no-es-pago`: las marcas vigentes, de lo más viejo a lo más nuevo."""

    model_config = ConfigDict(frozen=True)

    elementos: list[MarcaNoEsPago] = Field(max_length=MAXIMO_FILAS_RESULTADO)
    total: int = Field(ge=0)


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
    anulados: int = Field(
        description="Coinciden con un pago cuyo cotejo se anuló: no vuelven solos,"
        " se tildan a mano."
    )
    lineas_no_leidas: int
    filas_no_leidas: list[int] = Field(
        description=f"Las primeras {MAXIMO_FILAS_NO_LEIDAS}, por su número de línea en el archivo."
    )
    aca_no_banco: list[PagoPorCotejar]
    total_aca_no_banco: int
    banco_no_aca: list[MovimientoSinRegistrar]
    total_banco_no_aca: int
