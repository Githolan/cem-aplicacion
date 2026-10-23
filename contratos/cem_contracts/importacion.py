"""Importación de unidades y relaciones con informe (RF02; H2b 2b-5, corte 3).

Dos pasos sobre la misma planilla CSV:

- **Informe** (`POST …/padron/importacion/informe`): valida la planilla, la compara con lo que ya
  existe en el consorcio y dice qué haría cada fila. No escribe nada.
- **Aplicación** (`POST …/padron/importacion`): vuelve a calcular el informe y, si no tiene
  problemas, escribe todo en una transacción; si tiene uno solo, no escribe nada.

**La planilla.** CSV con encabezado. Columnas:

- `unidad`: la designación, obligatoria en cada fila;
- `nombre`, `correo`, `caracter`, `vigente_desde` y `ultimo_dia`: la relación. Vacías todas, la
  fila solo carga la unidad. `ultimo_dia` es el último día con vigencia, **inclusivo**, o vacío
  si no tiene fin; la base guarda `vigente_hasta = ultimo_dia + 1`, exclusivo. Las fechas van
  como `DD-MM-AAAA`. Una columna `vigente_hasta` no se acepta: nadie carga la fecha exclusiva
  creyendo que es la inclusiva;
- `coeficiente_<grupo>`, opcionales: el porcentaje de la unidad en ese grupo de gasto, con punto
  decimal y hasta 6 decimales, tal como dice la fuente. Se validan e informan, **no se
  escriben**: los carga el equipo hasta que 2b-6 resuelva la aprobación (D3).

El informe no devuelve correos ni nombres (D6): nombra cada fila por su línea y su unidad.
"""

from datetime import date
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from cem_contracts.estados import TipoVinculo
from cem_contracts.padron import FORMATO_REFERENCIA, LARGO_MAXIMO_REFERENCIA

LARGO_MAXIMO_ARCHIVO = 1_000_000
"""Caracteres de la planilla: alcanza para varios miles de filas."""


class CodigoProblema(StrEnum):
    """Por qué una fila (o el archivo) no se puede aplicar. Uno solo bloquea todo el archivo."""

    ARCHIVO_VACIO = "archivo_vacio"
    """La planilla no tiene encabezado o no tiene filas."""
    ENCABEZADO_INVALIDO = "encabezado_invalido"
    """Falta una columna obligatoria, hay una desconocida o una repetida."""
    COLUMNA_VIGENTE_HASTA = "columna_vigente_hasta"
    """La planilla trae `vigente_hasta`: se carga `ultimo_dia`, inclusivo."""
    FILA_MAL_FORMADA = "fila_mal_formada"
    """La fila no tiene la cantidad de columnas del encabezado."""
    UNIDAD_VACIA = "unidad_vacia"
    """La fila no dice la unidad."""
    DESIGNACION_AMBIGUA = "designacion_ambigua"
    """La designación se escribe distinto que otra de la planilla o del consorcio, pero es la
    misma si se ignoran mayúsculas y espacios: sería una unidad duplicada (PA01)."""
    RELACION_INCOMPLETA = "relacion_incompleta"
    """La fila trae parte de la relación: faltan el nombre, el carácter o `vigente_desde`."""
    RELACION_SIN_CORREO = "relacion_sin_correo"
    """Una relación sin correo no identifica a nadie: no se inventa un destinatario."""
    CORREO_INVALIDO = "correo_invalido"
    NOMBRE_INVALIDO = "nombre_invalido"
    """El nombre tiene que tener entre 1 y 100 caracteres."""
    CARACTER_INVALIDO = "caracter_invalido"
    """El carácter es `propietario`, `inquilino` u `ocupante`."""
    FECHA_INVALIDA = "fecha_invalida"
    """Una fecha no es `DD-MM-AAAA` o no existe, o `ultimo_dia` es anterior a `vigente_desde`."""
    RELACION_DUPLICADA = "relacion_duplicada"
    """La misma relación (unidad, correo, carácter y fechas) está dos veces en la planilla."""
    VINCULO_SOLAPADO = "vinculo_solapado"
    """Dos filas de la planilla dan a la misma persona, unidad y carácter fechas superpuestas."""
    CORRIGE_EXISTENTE = "corrige_existente"
    """La fila se superpone con un vínculo que ya existe y no es igual: pretende corregirlo. En
    este corte no se corrige por importación (D1): se finaliza o se anula y se vuelve a cargar."""
    INVITACIONES_SIMULTANEAS = "invitaciones_simultaneas"
    """Dos vínculos vigentes o futuros nuevos de la misma persona, unidad y carácter: solo puede
    haber una invitación pendiente para ellos."""
    INVITACION_PENDIENTE = "invitacion_pendiente"
    """Ya hay una invitación vigente para esa persona, unidad y carácter que no nombra este
    vínculo (manual o de otro): no se retira (D4.2)."""
    COEFICIENTE_INVALIDO = "coeficiente_invalido"
    """El porcentaje no es un número entre 0 y 100 con punto decimal y hasta 6 decimales, o el
    grupo no es válido."""
    COEFICIENTE_CONTRADICTORIO = "coeficiente_contradictorio"
    """Dos filas de la misma unidad dan porcentajes distintos para el mismo grupo."""
    FUERA_DE_ALCANCE = "fuera_de_alcance"
    """Sin `padron.gestionar` sobre esa unidad, o sin el alcance general para crearla."""
    SIN_FACULTAD_INVITAR = "sin_facultad_invitar"
    """La fila crea una invitación y falta `invitaciones.crear` sobre esa unidad (general, si la
    unidad es nueva) (D4.1)."""


class Problema(BaseModel):
    model_config = ConfigDict(frozen=True)

    codigo: CodigoProblema
    mensaje: str


class AccionUnidad(StrEnum):
    CREAR = "crear"
    EXISTENTE = "existente"


class AccionVinculo(StrEnum):
    CREAR = "crear"
    EXISTENTE = "existente"
    """Igual a uno que ya existe: no se escribe nada (reimportar es idempotente)."""


class AccionInvitacion(StrEnum):
    CREAR = "crear"
    """Se crea, sin enviarla. La persona la ve y la acepta con su sesión."""
    REEMPLAZAR_VENCIDA = "reemplazar_vencida"
    """Había una pendiente vencida para esa persona, unidad y carácter: se retira y se crea la
    nueva, al aplicar y en la misma transacción."""
    NINGUNA = "ninguna"
    """El vínculo ya terminó, o ya existía."""


class FilaInforme(BaseModel):
    """Lo que haría una fila. Sin correo ni nombre."""

    model_config = ConfigDict(frozen=True)

    linea: int = Field(description="Línea de la planilla (el encabezado es la 1).")
    designacion: str
    unidad: AccionUnidad | None = Field(description="Nulo si la fila tiene un problema de unidad.")
    tipo: TipoVinculo | None = None
    vigente_desde: date | None = None
    ultimo_dia: date | None = Field(
        default=None, description="Inclusivo, como en la planilla. Nulo: sin fin."
    )
    vigente_hasta: date | None = Field(
        default=None, description="Exclusivo, como lo guarda la base: `ultimo_dia + 1`."
    )
    vinculo: AccionVinculo | None = Field(
        default=None, description="Nulo si la fila no trae relación o la relación tiene problemas."
    )
    invitacion: AccionInvitacion | None = None
    problemas: list[Problema]


class CoeficientesGrupo(BaseModel):
    """Lo que dice la planilla de un grupo de gasto. Se informa, no se escribe ni se corrige."""

    model_config = ConfigDict(frozen=True)

    grupo: str
    suma: str = Field(description="Suma tal cual, como texto decimal (D4).", examples=["99.96"])
    sin_coeficiente: list[str] = Field(
        description="Unidades (de la planilla o del consorcio) sin porcentaje en este grupo."
    )
    distintos_del_vigente: list[str] = Field(
        description="Unidades cuyo porcentaje difiere del esquema vigente de ese grupo."
    )
    hay_esquema_vigente: bool


class InformeImportacion(BaseModel):
    """`POST /api/v1/consorcios/{consorcio_id}/padron/importacion/informe`. No escribe nada."""

    model_config = ConfigDict(frozen=True)

    archivo_sha256: str
    aplicable: bool = Field(description="Sin ningún problema: se puede aplicar entera.")
    sin_cambios: bool = Field(description="Todo ya existe: aplicarla no escribiría nada.")
    problemas_archivo: list[Problema]
    filas: list[FilaInforme]
    coeficientes: list[CoeficientesGrupo]
    esquemas_a_revisar: list[str] = Field(
        description="Grupos con esquema vigente que no incluirán las unidades nuevas: el reparto"
        " se bloquea hasta cargar una versión nueva (PA02)."
    )


class PlanillaPadron(BaseModel):
    """El cuerpo de los dos pasos: la planilla CSV como texto UTF-8."""

    model_config = ConfigDict(frozen=True)

    archivo: str = Field(min_length=1, max_length=LARGO_MAXIMO_ARCHIVO)


class ImportacionAplicada(BaseModel):
    """`POST /api/v1/consorcios/{consorcio_id}/padron/importacion`."""

    model_config = ConfigDict(frozen=True)

    importacion_id: UUID | None = Field(description="Nulo si no había nada que escribir.")
    archivo_sha256: str
    unidades_creadas: int
    vinculos_creados: int
    invitaciones_creadas: int
    invitaciones_reemplazadas: int


class AnulacionVinculo(BaseModel):
    """`POST /api/v1/consorcios/{consorcio_id}/vinculos/{vinculo_id}/anular`."""

    model_config = ConfigDict(frozen=True)

    motivo: str = Field(
        max_length=LARGO_MAXIMO_REFERENCIA,
        pattern=FORMATO_REFERENCIA,
        description="Por qué se anula (carga errónea). No queda en la auditoría (E31).",
        examples=["Fecha de inicio cargada por error"],
    )


class VinculoAnulado(BaseModel):
    """Respuesta de la anulación: el vínculo ya no existe."""

    model_config = ConfigDict(frozen=True)

    vinculo_id: UUID
    unidad_id: UUID
    tipo: TipoVinculo
