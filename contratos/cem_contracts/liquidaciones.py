"""Liquidación neutral: borrador por período, sus gastos y la simulación del reparto (H3;
migración `0057`).

Mandato del propietario del 31-10-2026: la parte de la liquidación que no depende de las
decisiones 4.2 y 5. Quien tiene `liquidacion.preparar` abre el borrador de un período, le agrega
órdenes **pagadas** con el grupo de gasto de cada una y ve la simulación: cuánto le toca a cada
unidad según el esquema de coeficientes vigente del grupo. No se revisa, aprueba ni publica
todavía; la simulación no se guarda.

Como toda lectura de egresos, pide R10: sesión de menos de 12 horas, segundo factor de menos de
10 minutos e identidad humana verificada. Los importes van en centavos, enteros.
"""

from datetime import date, datetime
from typing import Annotated
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from cem_contracts.egresos import Motivo, Version

FORMATO_GRUPO = r"^[a-z][a-z0-9_]{1,39}$"
"""El de `nucleo.esquema_coeficientes.grupo` (`0021`)."""


def _primer_dia(periodo: date) -> date:
    if periodo.day != 1:
        raise ValueError("El período es el primer día de un mes.")
    return periodo


Periodo = Annotated[date, AfterValidator(_primer_dia)]


class LiquidacionNueva(BaseModel):
    """`POST …/liquidaciones`. Abre el borrador del período en su versión siguiente."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    periodo: Periodo = Field(examples=["2026-10-01"])


class LiquidacionDescarte(BaseModel):
    """`POST …/liquidaciones/{id}/descartar`. El borrador queda `descartada`, sus gastos activos
    se quitan (quedan en la historia) y el período admite un borrador nuevo (`0058`)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    motivo: Motivo


class LiquidacionCreada(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID


class Liquidacion(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    periodo: date
    version: Version
    estado: str = Field(
        description="`borrador`, `descartada` (`0058`) o `publicada`; este corte no publica."
    )
    preparada_por: UUID
    creada_en: datetime


class ListaLiquidaciones(BaseModel):
    model_config = ConfigDict(frozen=True)

    elementos: list[Liquidacion]


class GastoNuevo(BaseModel):
    """`POST …/liquidaciones/{id}/gastos`: una orden pagada y el grupo de gasto que la reparte."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    orden_id: UUID
    grupo: str = Field(pattern=FORMATO_GRUPO, examples=["expensas_comunes"])


class GastoAgregado(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID


class GastoLiquidacion(BaseModel):
    """Un gasto del borrador, con el importe congelado de la ejecución pagada y el esquema exacto
    (grupo y versión) con que se reparte."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    orden_id: UUID
    concepto: str
    razon_social: str
    importe_centavos: int
    moneda: str
    pagada_en: datetime
    grupo: str
    esquema_id: UUID
    esquema_version: Version
    agregado_por: UUID
    agregado_en: datetime


class OrdenLiquidable(BaseModel):
    """Una orden pagada que todavía no está en ninguna liquidación."""

    model_config = ConfigDict(frozen=True)

    orden_id: UUID
    concepto: str
    razon_social: str
    importe_centavos: int
    moneda: str
    pagada_en: datetime


class GrupoLiquidable(BaseModel):
    """Un grupo con su esquema vigente el último día del período."""

    model_config = ConfigDict(frozen=True)

    grupo: str
    esquema_id: UUID
    esquema_version: Version
    vigente_desde: date


class GrupoSimulado(BaseModel):
    model_config = ConfigDict(frozen=True)

    grupo: str
    esquema_id: UUID
    esquema_version: Version
    total_centavos: int


class UnidadSimulada(BaseModel):
    model_config = ConfigDict(frozen=True)

    unidad_id: UUID
    designacion: str
    total_centavos: int
    por_grupo: dict[str, int] = Field(description="Centavos de la unidad en cada grupo.")


class SimulacionLiquidacion(BaseModel):
    """El reparto de los gastos del borrador entre las unidades. La suma de cada grupo es
    exactamente su total (RN05). `bloqueo` dice por qué no se pudo repartir (por ejemplo, una
    unidad sin coeficiente en el esquema): entonces no hay unidades."""

    model_config = ConfigDict(frozen=True)

    moneda: str | None
    total_centavos: int
    grupos: list[GrupoSimulado]
    unidades: list[UnidadSimulada]
    bloqueo: str | None


class LiquidacionDetalle(BaseModel):
    model_config = ConfigDict(frozen=True)

    liquidacion: Liquidacion
    gastos: list[GastoLiquidacion]
    grupos: list[GrupoLiquidable]
    simulacion: SimulacionLiquidacion


class ListaOrdenesLiquidables(BaseModel):
    model_config = ConfigDict(frozen=True)

    elementos: list[OrdenLiquidable]
