"""Importación de la liquidación del administrador (H5-1; migración `0113`; RF42, RN30).

En Modo Consejo, quien tiene `liquidacion.importar` sube el PDF que manda la administración como
documento de la categoría `liquidaciones_del_administrador` y, cuando pasó el antivirus, pide
importarlo. `cem-trabajos` lo lee sin IA (formato del Programa Mis Expensas) y deja cada dato como
campo preliminar, con el renglón de la página de donde salió. Si el formato no se reconoce, lo lee
el modelo (H5-2, `0114`), dentro del tope mensual de gasto: sus campos llevan origen `ia` y la
confianza que informó. Nada leído es oficial hasta que una persona lo confirma (H5-3, `0115`):
campo por campo (confirmar, corregir o descartar) y, cuando no queda ninguno pendiente, la
importación entera.

Los importes van como texto decimal con dos decimales (`"1234.56"`), tal como los informa la
liquidación: son lo leído, no un cálculo.
"""

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ImportacionNueva(BaseModel):
    """La versión del documento que se importa: un PDF `disponible` de la categoría
    `liquidaciones_del_administrador`."""

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


class ImportacionLiquidacion(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    documento_id: UUID
    version_documento_id: UUID
    subida_por: UUID
    creada_en: datetime
    estado: EstadoImportacion
    formato: str | None = Field(
        description="`mis_expensas` (formato conocido, sin IA) o `lectura_ia` (leída con IA)."
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
    campos: list[CampoPreliminar]


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


class DescarteImportacion(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    motivo: str = Field(min_length=1, max_length=500, description="Por qué no sirve.")
