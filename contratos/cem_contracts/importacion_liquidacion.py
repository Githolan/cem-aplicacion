"""Importación de la liquidación del administrador (H5-1; migración `0113`; RF42, RN30).

En Modo Consejo, quien tiene `liquidacion.importar` sube el PDF que manda la administración como
documento de la categoría `liquidaciones_del_administrador` y, cuando pasó el antivirus, pide
importarlo. `cem-trabajos` lo lee sin IA (formato del Programa Mis Expensas) y deja cada dato como
campo preliminar, con el renglón de la página de donde salió. Si el formato no se reconoce, lo lee
el modelo (H5-2, `0114`), dentro del tope mensual de gasto: sus campos llevan origen `ia` y la
confianza que informó. Nada leído es oficial hasta que una persona lo confirma (H5-3, `0115`):
campo por campo (confirmar, corregir o descartar) y, cuando no queda ninguno pendiente, la
importación entera. Una foto o un PDF sin texto los lee el modelo como imágenes (H5-5, `0117`).

Los importes van como texto decimal con dos decimales (`"1234.56"`), tal como los informa la
liquidación: son lo leído, no un cálculo.
"""

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from cem_contracts.perfil_normativo import EnlaceDocumentacion


class ImportacionNueva(BaseModel):
    """La versión del documento que se importa: un PDF, JPEG o PNG `disponible` de la categoría
    `liquidaciones_del_administrador` (la foto o el PDF escaneado, desde H5-5, `0117`)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    documento_id: UUID
    version_documento_id: UUID


class ImportacionCreada(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID


class EstadoImportacion(StrEnum):
    LEYENDO = "leyendo"
    PARA_CONFIRMAR = "para_confirmar"
    NO_RECONOCIDA = "no_reconocida"
    CONFIRMADA = "confirmada"
    DESCARTADA = "descartada"
    REEMPLAZADA = "reemplazada"
    """Fue la liquidación oficial de su período hasta que otra confirmada la reemplazó con motivo
    (`0119`): queda como historia."""
    FALLA_DE_LECTURA = "falla_de_lectura"
    """No se guarda: la lectura agotó sus intentos. Se reintenta subiendo una versión nueva del
    documento."""


class TipoCampo(StrEnum):
    TOTAL = "total"
    RUBRO = "rubro"
    GASTO = "gasto"
    UNIDAD = "unidad"
    IMPORTE_UNIDAD = "importe_unidad"
    SALDO_UNIDAD = "saldo_unidad"


class EstadoCampo(StrEnum):
    PENDIENTE = "pendiente"
    CONFIRMADO = "confirmado"
    CORREGIDO = "corregido"
    DESCARTADO = "descartado"


class OrigenCampo(StrEnum):
    DETERMINISTA = "determinista"
    IA = "ia"


class CampoPreliminar(BaseModel):
    """Un dato leído. `clave` es estable dentro de la importación (`gasto:3:2`, `unidad:001`)."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    orden: int
    tipo: TipoCampo
    clave: str
    valor: dict[str, Any]
    confianza: Decimal = Field(ge=0, le=1)
    origen: OrigenCampo
    pagina: int = Field(ge=1)
    renglon: Decimal = Field(description="`top` del renglón en la página, en puntos.")
    unidad_id: UUID | None = Field(
        description="La unidad del padrón, en los campos de una unidad que coincide."
    )
    estado: EstadoCampo
    valor_corregido: dict[str, Any] | None
    fragmento: str | None = Field(
        description="El texto del renglón de donde salió; vacío en lecturas anteriores a `0115`."
    )
    confirmado_por: UUID | None = Field(description="Quién tomó la última decisión.")
    confirmado_en: datetime | None


class DiferenciaLeida(BaseModel):
    """Lo que no cierra al cruzar lo leído (D5: más de un centavo) o una unidad que no está en el
    padrón. Dice la diferencia y su base; no califica (RN33)."""

    model_config = ConfigDict(frozen=True, extra="allow")

    regla: str
    detalle: str
    pagina: int
    renglon: Decimal


class OrigenEnlace(StrEnum):
    ANOTACION = "anotacion"
    TEXTO = "texto"
    QR = "qr"


class EnlaceLeido(BaseModel):
    """Un enlace que trae la liquidación (Disposición 1146/24: comprobantes y estado de pagos),
    anotado al leerla y sin seguirlo (H5-5, `0118`)."""

    model_config = ConfigDict(frozen=True)

    orden: int = Field(description="Su número en la importación, desde 1.")
    url: str
    origen: OrigenEnlace = Field(
        description="`anotacion` (enlace del PDF), `texto` (escrito en la página) o `qr`."
    )
    pagina: int


class EnlaceTraido(BaseModel):
    """El archivo traído de un enlace: un documento nuevo de la categoría, en cuarentena hasta que
    pasa el antivirus; después se importa como cualquier archivo subido."""

    model_config = ConfigDict(frozen=True)

    documento_id: UUID
    version_documento_id: UUID
    tipo: str
    tamano: int


class ImportacionLiquidacion(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    documento_id: UUID
    version_documento_id: UUID
    subida_por: UUID
    creada_en: datetime
    estado: EstadoImportacion
    formato: str | None = Field(
        description=(
            "`mis_expensas` (formato conocido, sin IA), `lectura_ia` (leída con IA) o"
            " `lectura_ia_imagen` (leída con IA de una foto o un PDF sin texto)."
        )
    )
    periodo: date | None
    motivo: str | None = Field(
        description="Por qué no se leyó, con `no_reconocida`: sin el formato y, si se intentó con"
        " IA, por qué tampoco así (el tope del mes, una falla del modelo); se carga a mano."
    )
    diferencias: list[DiferenciaLeida]
    leida_en: datetime | None
    decidida_por: UUID | None = Field(description="Quién la confirmó o la descartó.")
    decidida_en: datetime | None
    motivo_descarte: str | None
    reemplaza_a: UUID | None = Field(
        description="La liquidación oficial del período que esta reemplazó al confirmarse (`0119`)."
    )
    motivo_reemplazo: str | None
    campos: list[CampoPreliminar]
    enlaces: list[EnlaceLeido] = Field(
        description="Los enlaces del documento, sin seguir (H5-5); se trae uno a pedido."
    )


class ImportacionResumen(BaseModel):
    """Una importación en la lista del consorcio, con cuántos datos tiene y cuántos faltan
    decidir."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    documento_id: UUID
    version_documento_id: UUID
    subida_por: UUID
    creada_en: datetime
    estado: EstadoImportacion
    formato: str | None
    periodo: date | None
    motivo: str | None
    diferencias: int = Field(ge=0)
    campos: int = Field(ge=0)
    pendientes: int = Field(ge=0)
    decidida_por: UUID | None
    decidida_en: datetime | None
    motivo_descarte: str | None
    reemplaza_a: UUID | None
    motivo_reemplazo: str | None


class DecisionCampo(StrEnum):
    CONFIRMADO = "confirmado"
    CORREGIDO = "corregido"
    DESCARTADO = "descartado"


class DecisionSobreCampo(BaseModel):
    """Confirmar, corregir o descartar un dato leído. `valor_corregido` va solo al corregir: las
    mismas claves que el valor leído, y los importes como texto decimal con dos decimales."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision: DecisionCampo
    valor_corregido: dict[str, Any] | None = None


class ConfirmacionImportacion(BaseModel):
    """Confirmar la importación (`0119`): una sola liquidación oficial por período. Sin cuerpo (o
    vacío), no reemplaza nada y se niega si el período ya tiene su liquidación oficial
    (`periodo_ya_confirmado`). Con `reemplaza_a` (la importación confirmada que hoy es la oficial)
    y `motivo`, la reemplaza: la anterior pasa a `reemplazada` y queda como historia. Van los dos o
    ninguno."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reemplaza_a: UUID | None = None
    motivo: str | None = Field(
        default=None, min_length=1, max_length=500, description="Por qué se la reemplaza."
    )

    @model_validator(mode="after")
    def _juntos(self) -> "ConfirmacionImportacion":
        if (self.reemplaza_a is None) != (self.motivo is None):
            raise ValueError("el reemplazo va con su motivo")
        return self


class DescarteImportacion(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    motivo: str = Field(min_length=1, max_length=500, description="Por qué no sirve.")


# --- Recálculo con los coeficientes del edificio (H5-4, `0116`) --------------------------------


class CorrespondenciaNueva(BaseModel):
    """Qué esquema de coeficientes del edificio (`grupo` de RF05, p. ej. `expensas_comunes`)
    corresponde a un grupo de la liquidación, con su nombre tal como figura (p. ej. «Gastos
    Comunes»). El edificio la recuerda para los meses siguientes."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    nombre: str = Field(min_length=1, max_length=80)
    grupo: str = Field(pattern=r"^[a-z][a-z0-9_]{1,39}$")


class EstadoGrupoRecalculado(StrEnum):
    RECALCULADO = "recalculado"
    SIN_CORRESPONDENCIA = "sin_correspondencia"
    """Falta decir qué esquema de coeficientes le corresponde."""
    SIN_ESQUEMA = "sin_esquema"
    """El esquema que le corresponde no estaba vigente en el período."""
    SIN_TOTAL = "sin_total"
    """El total del grupo no está entre los datos confirmados de la importación."""
    COEFICIENTES_INCOMPLETOS = "coeficientes_incompletos"
    """Alguna unidad activa no tiene coeficiente en el esquema, o suman cero."""


class UnidadRecalculada(BaseModel):
    """Una unidad en un grupo: lo que informó la administración y lo que da el reparto con el
    coeficiente cargado. `unidad_id` nulo: la fila de la liquidación no coincide con una unidad del
    padrón. `informado` nulo: la unidad del padrón no figura en la liquidación (se compara como
    cero) o figura con el monto vacío (`sin_monto`: no se compara)."""

    model_config = ConfigDict(frozen=True)

    unidad_id: UUID | None
    designacion: str
    coeficiente: Decimal | None = Field(description="El porcentaje cargado en el esquema (RF05).")
    porcentaje_informado: Decimal | None
    esperado: Decimal | None = Field(description="El reparto del total del grupo, en pesos.")
    informado: Decimal | None = Field(description="El monto que figura en la liquidación.")
    diferencia: Decimal | None = Field(description="`informado - esperado`.")
    senalada: bool = Field(description="La diferencia pasa de un centavo (D5-A).")
    sin_monto: bool = Field(
        description="Figura en la liquidación con el monto vacío: no se toma como cero ni se "
        "compara."
    )


class GrupoRecalculado(BaseModel):
    model_config = ConfigDict(frozen=True)

    nombre: str = Field(description="El grupo como figura en la liquidación.")
    estado: EstadoGrupoRecalculado
    grupo: str | None = Field(description="El esquema de coeficientes que le corresponde.")
    esquema_version: int | None
    vigente_desde: date | None
    total: Decimal | None = Field(description="El gasto del grupo según la liquidación.")
    unidades: list[UnidadRecalculada]
    senaladas: int = Field(ge=0)
    sin_monto: int = Field(ge=0, description="Unidades con el monto vacío, que no se comparan.")


class EsquemaDelPeriodo(BaseModel):
    model_config = ConfigDict(frozen=True)

    grupo: str
    esquema_version: int
    vigente_desde: date


class RecalculoLiquidacion(BaseModel):
    """El reparto de cada grupo de una importación confirmada con los coeficientes del edificio
    vigentes en el período, frente a lo que informó la administración. Usa solo los datos
    confirmados o corregidos (RN30). Dice la diferencia y su base; no la califica (RN33).
    `sin_grupos`: la liquidación no informa el reparto por grupo (p. ej. leída con IA)."""

    model_config = ConfigDict(frozen=True)

    importacion_id: UUID
    periodo: date
    sin_grupos: bool
    grupos: list[GrupoRecalculado]
    esquemas: list[EsquemaDelPeriodo] = Field(
        description="Los esquemas de coeficientes vigentes en el período, para elegir."
    )


# --- La liquidación oficial y quién la lee (`0119`; diseño H5 §6, D3-A) -----------------------

PERIODOS_DE_GASTO = 12
"""Cuántos períodos con liquidación oficial muestran el gasto por período del panel y del resumen
(`0120`): los últimos que la tienen."""


class LiquidacionOficialResumen(BaseModel):
    """Una liquidación oficial en la lista del Consejo y la administración
    (`liquidacion.ver_importada`): la vigente de su período o una reemplazada (historia)."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    periodo: date
    formato: str | None
    decidida_por: UUID = Field(description="Quién la confirmó.")
    decidida_en: datetime = Field(description="Cuándo se confirmó.")
    vigente: bool = Field(description="Es la oficial de su período; si no, fue reemplazada.")
    reemplaza_a: UUID | None = Field(description="La oficial anterior del período, si reemplazó.")
    motivo_reemplazo: str | None
    reemplazada_por: UUID | None = Field(description="La que la reemplazó, si ya no es vigente.")
    diferencias: int = Field(ge=0)
    enlace_documentacion: EnlaceDocumentacion | None = Field(
        description="Lo que pide el perfil normativo del consorcio sobre el enlace a la"
        " documentación y lo que encontró la lectura (RF47, `0125`). Nulo si no es la vigente de"
        " su período o si la versión del perfil no lo exige para ese período."
    )


class CampoOficial(BaseModel):
    """Un dato oficial: confirmado o corregido (con el valor corregido), RN30. `unidad_id` es la
    unidad oficial (la del valor corregido, si se corrigió) y `designacion`, la del padrón."""

    model_config = ConfigDict(frozen=True)

    orden: int
    tipo: TipoCampo
    clave: str
    valor: dict[str, Any]
    unidad_id: UUID | None
    designacion: str | None


class LiquidacionOficial(BaseModel):
    """Una liquidación oficial con sus datos oficiales y sus diferencias (las diferencias, solo
    para quien ve el panel completo, RN33)."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    periodo: date
    formato: str | None
    decidida_por: UUID
    decidida_en: datetime
    vigente: bool
    reemplaza_a: UUID | None
    motivo_reemplazo: str | None
    reemplazada_por: UUID | None
    diferencias: list[DiferenciaLeida]
    campos: list[CampoOficial]
    enlace_documentacion: EnlaceDocumentacion | None = Field(
        description="Lo que pide el perfil normativo del consorcio sobre el enlace a la"
        " documentación y lo que encontró la lectura (RF47, `0125`). Nulo si no es la vigente de"
        " su período o si la versión del perfil no lo exige para ese período."
    )


class PeriodoOficial(BaseModel):
    """Un período con liquidación oficial vigente, y cuándo se confirmó."""

    model_config = ConfigDict(frozen=True)

    periodo: date
    confirmada_en: datetime


class RubroDelPeriodo(BaseModel):
    """El gasto de un rubro según la liquidación de la administración. Vacío lo que no informa."""

    model_config = ConfigDict(frozen=True)

    numero: int | None
    nombre: str | None
    total: Decimal | None


class TotalDelGrupo(BaseModel):
    """El gasto de un grupo («Gastos Comunes», «AySA»…) según la liquidación."""

    model_config = ConfigDict(frozen=True)

    nombre: str
    total: Decimal | None


class ResumenDelPeriodo(BaseModel):
    """El resumen de la liquidación vigente de un período para el propietario
    (`fiscalizacion.ver_resumen`): el gasto por rubro y los totales. Sin el detalle de los gastos,
    sin diferencias y sin unidades."""

    model_config = ConfigDict(frozen=True)

    importacion_id: UUID = Field(
        description="La importación oficial de la que sale: con la de `mi-cuenta`, dice si las dos"
        " respuestas describen la misma liquidación."
    )
    periodo: date
    confirmada_en: datetime
    rubros: list[RubroDelPeriodo]
    grupos: list[TotalDelGrupo]
    total: Decimal | None
    vencimientos: list[str]


class TotalDelPeriodo(BaseModel):
    """El total del gasto de un período según su liquidación oficial (`0120`). Vacío si la
    liquidación no tiene ese dato oficial."""

    model_config = ConfigDict(frozen=True)

    periodo: date
    total: Decimal | None


class ResumenLiquidacion(BaseModel):
    """Los períodos con liquidación vigente y el resumen del pedido (sin período, el último).
    `liquidacion` vacío: todavía no hay ninguna confirmada."""

    model_config = ConfigDict(frozen=True)

    periodos: list[PeriodoOficial]
    liquidacion: ResumenDelPeriodo | None
    gasto_por_periodo: list[TotalDelPeriodo] = Field(
        description="El total del gasto de cada uno de los últimos `PERIODOS_DE_GASTO` períodos"
        " con liquidación oficial, del más viejo al más nuevo (`0120`)."
    )


class GrupoDeLaUnidad(BaseModel):
    model_config = ConfigDict(frozen=True)

    nombre: str
    porcentaje: Decimal | None
    monto: Decimal | None


class UnidadEnLaLiquidacion(BaseModel):
    """Lo que la liquidación vigente dice de una unidad donde la persona es propietaria
    (`cuenta.ver_propia`). `figura` falso: la unidad no está en la liquidación (o no se pudo
    asociar al padrón) y los importes van vacíos."""

    model_config = ConfigDict(frozen=True)

    unidad_id: UUID
    designacion: str
    figura: bool
    saldo_anterior: Decimal | None
    pagos: Decimal | None
    deuda: Decimal | None
    interes: Decimal | None
    disponible: Decimal | None
    grupos: list[GrupoDeLaUnidad]
    otros_gastos: Decimal | None
    redondeo: Decimal | None
    total: Decimal | None = Field(description="Lo que la unidad paga en el período.")


class CuentaDelPeriodo(BaseModel):
    model_config = ConfigDict(frozen=True)

    importacion_id: UUID = Field(
        description="La importación oficial de la que sale: con la del `resumen`, dice si las dos"
        " respuestas describen la misma liquidación."
    )
    periodo: date
    confirmada_en: datetime
    vencimientos: list[str]
    unidades: list[UnidadEnLaLiquidacion]


class MiCuenta(BaseModel):
    """Las unidades propias en la liquidación vigente de un período (sin período, el último).
    `liquidacion` vacío: todavía no hay ninguna confirmada."""

    model_config = ConfigDict(frozen=True)

    periodos: list[PeriodoOficial]
    liquidacion: CuentaDelPeriodo | None
