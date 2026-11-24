"""Pagos informados por los vecinos: cortes PI-S1 (`0129`), PI-S2 (`0130`), PI-S3 (`0131`) y
PI-S4 (`0132`, la entrada por correo).

Diseño: `doc/diseno/Pagos_Informados.md` del repositorio CEM (reglas PI-1 a PI-9).
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
  201. Un pago rechazado o anulado no cuenta.
- **Confirmar, rechazar y anular** (`pagos_informados.confirmar`, PI-4, PI-5 y PI-9): quien tiene la
  facultad `pagos.confirmar_informado` (paquetes de miembro del Consejo y de administrador, los tres
  modos). Nadie decide sobre un pago que informó él mismo, ni ayudando a otra persona. `informado`
  pasa a `confirmado` (con el comprobante ya aceptado por el antivirus, o sin comprobante en
  efectivo) o a `rechazado` (con motivo); `confirmado` pasa a `anulado` (con motivo). Nada se borra:
  cada paso queda en `historia`. Cada pago dice en `acciones` lo que quien lee puede hacer ahora.
- **Corregir** (PI-3): un pago rechazado o anulado se informa de nuevo con `corrige_a`, aunque tenga
  el mismo número de operación o el mismo archivo; cada pago se corrige una sola vez y solo cuenta
  la última versión vigente.
- **Quién lee** (`pagos_informados.ver`): quien lo informó y quien tiene el vínculo que cuenta con
  esa unidad; quien tiene la facultad `fiscalizacion.ver_panel`, todos los del consorcio.
- **El cotejo** (`GET …/pagos-informados/cotejo`, PI-7 y PI-8): por cada unidad de la liquidación
  oficial vigente, lo informado y confirmado del mes anterior al período contra la columna «Pagos»
  de la liquidación. Se calcula cada vez que se lee: nunca queda un resultado viejo. Nunca dice que
  la administración se equivocó: dice «lo informado y confirmado suma X; la liquidación dice Y».
"""

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from cem_contracts.documentos import EstadoVersion, TipoArchivo, VersionCargada

LARGO_NUMERO_OPERACION = 60
"""La base tiene el mismo control (`0129`)."""
LARGO_MOTIVO = 500
"""El del motivo de un rechazo o una anulación; la base tiene el mismo control (`0130`)."""
UN_RENGLON = r"^[^\x00-\x1f\x7f]*$"
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
    """PI-4: `informado` → `confirmado` o `rechazado`; `confirmado` → `anulado`."""

    INFORMADO = "informado"
    CONFIRMADO = "confirmado"
    RECHAZADO = "rechazado"
    ANULADO = "anulado"


ESTADOS_QUE_NO_CUENTAN = (EstadoPagoInformado.RECHAZADO, EstadoPagoInformado.ANULADO)
"""Los que no cuentan para «un solo registro por pago»: la base los excluye de sus índices únicos
(`0129`) y solo ellos se corrigen (`0130`)."""
TRANSICIONES = (
    (EstadoPagoInformado.INFORMADO, EstadoPagoInformado.CONFIRMADO),
    (EstadoPagoInformado.INFORMADO, EstadoPagoInformado.RECHAZADO),
    (EstadoPagoInformado.CONFIRMADO, EstadoPagoInformado.ANULADO),
)
"""Los pasos que admite la base (`0130`); ningún otro."""
ESTADOS_CON_MOTIVO = (EstadoPagoInformado.RECHAZADO, EstadoPagoInformado.ANULADO)
"""Llegar a estos estados pide el motivo; a `confirmado`, no."""


class OrigenPagoInformado(StrEnum):
    APP = "app"
    CORREO = "correo"
    """La entrada por correo llega en PI-S4."""


MotivoDecision = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=LARGO_MOTIVO,
                      pattern=UN_RENGLON),
]  # fmt: skip

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
    corrige_a: UUID | None = Field(
        default=None,
        description="El pago rechazado o anulado de la misma unidad que este corrige (PI-3). Con"
        " él, el pago nuevo se crea aunque tenga el mismo número o el mismo archivo.",
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


class AccionPagoInformado(StrEnum):
    """Lo que quien lee puede hacer con el pago ahora (la pantalla no ofrece otra cosa, C9)."""

    CONFIRMAR = "confirmar"
    RECHAZAR = "rechazar"
    ANULAR = "anular"
    CORREGIR = "corregir"
    """Informarlo de nuevo con `corrige_a`: rechazado o anulado, todavía sin corrección, y por
    quien puede informar el pago de esa unidad."""


class PasoPagoInformado(BaseModel):
    """Un cambio de estado, en el orden en que pasó."""

    model_config = ConfigDict(frozen=True)

    de: EstadoPagoInformado
    a: EstadoPagoInformado
    motivo: str | None = Field(description="El del rechazo o la anulación; nulo al confirmar.")
    registrado_por_nombre: str
    registrado_en: datetime


class MotivoPagoInformado(BaseModel):
    """`POST …/{p}/rechazo` y `POST …/{p}/anulacion`: el motivo, que lee quien informó."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    motivo: MotivoDecision


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
    corrige_a: UUID | None = Field(description="El pago rechazado o anulado que este corrige.")
    corregido_por: UUID | None = Field(
        description="El pago que corrige a este, si es rechazado o anulado y ya se corrigió."
    )
    informado_por_nombre: str
    informado_en: datetime
    propio: bool = Field(description="Si lo informó quien pregunta.")
    historia: list[PasoPagoInformado] = Field(
        description="Los cambios de estado, del primero al último; vacía mientras está informado."
    )
    acciones: list[AccionPagoInformado]


class PagoInformadoRespuesta(PagoInformado):
    """La respuesta de `POST …/pagos-informados`: 201 con `ya_informado` falso si es nuevo; 200 con
    el existente y `ya_informado` verdadero si ese pago ya estaba informado (PA07)."""

    ya_informado: bool


class ListaPagosInformados(BaseModel):
    """`GET …/pagos-informados`: los que la persona puede ver, del más nuevo al más viejo."""

    model_config = ConfigDict(frozen=True)

    elementos: list[PagoInformado]
    largo_numero_operacion: int
    largo_motivo: int
    por_confirmar: int = Field(
        description="Cuántos pagos `informado` puede confirmar o rechazar"
        " quien lee ahora: el número de la bandeja del Consejo."
    )


# --- El cotejo con la liquidación oficial (PI-S3, `0131`) -------------------------------------

MESES_ANTES_COLUMNA_PAGOS = 1
"""Decisión del propietario del 23-11-2026 (Q2): la columna «Pagos» de la liquidación de un mes
suma lo pagado el mes anterior (la de noviembre, lo de octubre). La base usa el mismo valor
(`0131`). SIN VERIFICAR contra una liquidación real: se comprueba con la primera."""


class ResultadoCotejoLiquidacion(StrEnum):
    """PI-7: cómo queda cada unidad."""

    COINCIDE = "coincide"
    """Lo informado y confirmado suma lo mismo que la columna «Pagos» (también cero y cero)."""
    DIFIERE = "difiere"
    """Suman distinto; con los dos importes."""
    SIN_DATOS = "sin_datos"
    """La liquidación no trae la columna «Pagos» de la unidad, o no se puede leer como importe."""


class UnidadCotejada(BaseModel):
    """Una unidad en el cotejo. La pantalla dice «lo informado y confirmado suma X; la liquidación
    dice Y» (PI-8), nunca que alguien se equivocó."""

    model_config = ConfigDict(frozen=True)

    unidad_id: UUID
    unidad_designacion: str
    informado_confirmado: Decimal = Field(
        description="La suma de los pagos confirmados de la unidad con la fecha del pago entre"
        " `pagos_desde` y `pagos_hasta`; cero si no hay ninguno."
    )
    pagos_confirmados: int = Field(description="Cuántos pagos confirmados suma.")
    sin_pagos_informados: bool = Field(
        description="Ningún pago confirmado en la ventana: si la liquidación trae pagos, la"
        " pantalla lo dice así y no como una diferencia de alguien."
    )
    liquidacion: Decimal | None = Field(
        description="La columna «Pagos» de la unidad en la liquidación oficial; nula con"
        " `sin_datos`."
    )
    diferencia: Decimal | None = Field(
        description="`liquidacion` − `informado_confirmado`; nula con `sin_datos`."
    )
    resultado: ResultadoCotejoLiquidacion


class CotejoDeLaLiquidacion(BaseModel):
    """El cotejo de una liquidación oficial vigente."""

    model_config = ConfigDict(frozen=True)

    importacion_id: UUID
    periodo: date = Field(description="El de la liquidación: el primer día del mes.")
    pagos_desde: date = Field(description="El primer día de los pagos que cuentan (incluido).")
    pagos_hasta: date = Field(description="El último día de los pagos que cuentan (incluido).")
    unidades: list[UnidadCotejada] = Field(
        description="Las que quien lee puede ver: todas con `fiscalizacion.ver_panel`; si no, las"
        " de sus vínculos confirmados."
    )
    para_revisar: int = Field(
        description="Cuántas de esas unidades tienen `difiere` con al menos un pago confirmado: el"
        " número de la bandeja y del aviso diario del Consejo. Las que difieren sin pagos"
        " informados no se cuentan (decisión por defecto de PI-S3)."
    )


class CotejoPagosInformados(BaseModel):
    """`GET …/pagos-informados/cotejo`: nula si todavía no hay una liquidación oficial vigente."""

    model_config = ConfigDict(frozen=True)

    liquidacion: CotejoDeLaLiquidacion | None


# --- La entrada por correo (PI-S4, `0132`) ------------------------------------------------------

ENCABEZADO_CLAVE_CORREO = "X-CEM-Clave-Correo"
"""El encabezado con la clave compartida que manda el Worker de Cloudflare (PC-2)."""
MAXIMO_COMPROBANTES_POR_CORREO = 10
"""Más adjuntos que esto en un correo se rebotan: que los mande en varios correos."""
MAXIMO_COMPROBANTES_POR_DIA = 30
"""Cuántos adjuntos nuevos acepta por persona en 24 horas; los demás se rebotan."""
TOPE_CORREO_BYTES = 26 * 1024 * 1024
"""Lo que acepta la entrada: 25 MiB es el tope de Email Routing para un correo entrante, más el
margen de la codificación del cuerpo."""


class EstadoPagoPorCorreo(StrEnum):
    POR_COMPLETAR = "por_completar"
    INFORMADO = "informado"
    DESCARTADO = "descartado"


class EntradaCorreoRecibida(BaseModel):
    """`POST /api/v1/entrada/correo-pagos`: qué pasó con cada adjunto del correo."""

    model_config = ConfigDict(frozen=True)

    nuevos: int = Field(description="Adjuntos guardados por primera vez, por completar.")
    ya_recibidos: int = Field(
        description="Adjuntos que ya estaban: un reintento del mismo correo, o el mismo archivo"
        " todavía por completar."
    )
    ya_informados: int = Field(
        description="Adjuntos que ya son el comprobante de un pago vigente: no se guardaron (PA07)."
    )


class PagoPorCorreo(BaseModel):
    """Un comprobante recibido por correo que quien lo mandó todavía tiene que completar. Lo leído
    por la IA es una propuesta (RN21): la persona lo revisa antes de informarlo."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    recibido_en: datetime
    asunto: str | None
    comprobante_version_id: UUID
    comprobante_tipo: TipoArchivo
    comprobante_estado: EstadoVersion
    leido: bool = Field(description="Si ya pasó la lectura (con la lectura apagada, nunca).")
    legible: bool | None = Field(description="Si la lectura encontró los datos; nulo sin lectura.")
    importe: Decimal | None = Field(description="Propuesto por la lectura.")
    moneda: str | None = Field(description="Propuesta por la lectura.")
    numero_operacion: str | None = Field(description="Propuesto por la lectura.")
    fecha_pago: date | None = Field(description="Propuesta por la lectura.")


class ListaPagosPorCorreo(BaseModel):
    """`GET …/pagos-informados/por-correo`: lo que la persona mandó y le falta completar."""

    model_config = ConfigDict(frozen=True)

    elementos: list[PagoPorCorreo]


class PagoPorCorreoInforme(BaseModel):
    """`POST …/pagos-informados/por-correo/{r}/informe`: los datos revisados por la persona. El
    comprobante es el del correo."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    unidad_id: UUID
    importe: Decimal = Field(gt=0, max_digits=18, decimal_places=2, description="En pesos.")
    fecha_pago: date = Field(description="El día en que se pagó; no posterior a hoy.")
    medio: MedioPago
    numero_operacion: NumeroOperacion | None = Field(
        default=None,
        description="El de la transferencia, el depósito o el pago; obligatorio salvo en efectivo.",
    )

    @model_validator(mode="after")
    def _del_medio(self) -> "PagoPorCorreoInforme":
        if self.medio is not MEDIO_SIN_COMPROBANTE and self.numero_operacion is None:
            raise ValueError("sin número de operación solo se informa un pago en efectivo")
        return self


class PagoPorCorreoDescartado(BaseModel):
    """`POST …/pagos-informados/por-correo/{r}/descarte`."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    estado: EstadoPagoPorCorreo
