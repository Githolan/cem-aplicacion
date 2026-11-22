"""Reparaciones a cargo del consorcio (RF19, con la regla de cierre de RF20; Modo Consejo, `0124`).

Diseño P0 S10 a S12 y plan §5.2 y §6.8 del repositorio CEM; decisiones del 22-11-2026.

- **Quién pide** (`reparaciones.pedir`): un propietario, inquilino u ocupante con el vínculo
  confirmado, por una unidad suya o por un espacio común; también quien gestiona.
- **Quién gestiona** (`reparaciones.gestionar`): quien tiene la facultad del mismo nombre, que
  entra en los paquetes `miembro_consejo` y `administrador` y no se da a los mandatos ya
  registrados.
- **Estados:** `pedida` → `en_gestion` → `presupuestada` → `aprobada` →
  `terminada_gasto_pendiente` → `cerrada`; desde cualquier estado abierto, `no_realizada` con el
  motivo (RF20); desde `pedida`, `retirada` por quien la pidió. Nada se cambia: cada paso es una
  fila de la historia.
- **El cierre:** con la factura y un gasto de una liquidación oficial confirmada
  (`gasto:<rubro>:<n>`); un gasto cierra a lo sumo una reparación (PA11). La reparación no guarda
  importes.
"""

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from cem_contracts.documentos import EstadoVersion, TipoArchivo, VersionCargada
from cem_contracts.egresos import FORMATO_CUIT

LARGO_DESCRIPCION = 2000
LARGO_LUGAR = 120
LARGO_MOTIVO = 500
LARGO_PROVEEDOR = 120
LARGO_APROBADO_POR = 120
FOTOS_MAXIMO = 4
"""Las fotos de un pedido que el antivirus no rechazó. La base tiene los mismos controles
(`0124`)."""
CON_RENGLONES = r"^[^\x00-\x09\x0b-\x1f\x7f]*$"
"""Ningún carácter de control salvo el salto de renglón."""
UN_RENGLON = r"^[^\x00-\x1f\x7f]*$"
FORMATO_GASTO = r"^gasto:[^:]+:[0-9]+$"
"""La clave de un gasto en la liquidación importada (`gasto:<rubro>:<n>`)."""


def _texto(maximo: int, patron: str = UN_RENGLON) -> Any:
    return Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=maximo, pattern=patron),
    ]


Descripcion = _texto(LARGO_DESCRIPCION, CON_RENGLONES)
Lugar = _texto(LARGO_LUGAR)
Motivo = _texto(LARGO_MOTIVO)
Proveedor = _texto(LARGO_PROVEEDOR)
AprobadoPor = _texto(LARGO_APROBADO_POR)


class EstadoReparacion(StrEnum):
    PEDIDA = "pedida"
    EN_GESTION = "en_gestion"
    """Tiene responsable."""
    PRESUPUESTADA = "presupuestada"
    APROBADA = "aprobada"
    """Con la constancia de quién aprobó el presupuesto."""
    TERMINADA_GASTO_PENDIENTE = "terminada_gasto_pendiente"
    """Hecha, con la factura; espera el gasto de la próxima liquidación oficial."""
    CERRADA = "cerrada"
    NO_REALIZADA = "no_realizada"
    """Terminó sin evidencia: lleva el motivo de la imposibilidad (RF20)."""
    RETIRADA = "retirada"


ABIERTOS = frozenset(
    {
        EstadoReparacion.PEDIDA,
        EstadoReparacion.EN_GESTION,
        EstadoReparacion.PRESUPUESTADA,
        EstadoReparacion.APROBADA,
        EstadoReparacion.TERMINADA_GASTO_PENDIENTE,
    }
)


class Prioridad(StrEnum):
    """Los niveles de atención del documento 08, §10, que aplican a una reparación («Consulta» no
    es una reparación). «Emergencia» es solo la clasificación: el flujo de emergencias (RF26)
    sigue en H4b."""

    EMERGENCIA = "emergencia"
    URGENTE = "urgente"
    NORMAL = "normal"


class TipoAdjunto(StrEnum):
    FOTO = "foto"
    """Del pedido; la sube quien la pidió mientras está abierta."""
    PRESUPUESTO = "presupuesto"
    APROBACION = "aprobacion"
    """El documento firmado que respalda la aprobación, si no es una decisión formal."""
    FACTURA = "factura"


class AccionReparacion(StrEnum):
    """Lo que la persona puede hacer con la reparación ahora (la pantalla no ofrece otra cosa)."""

    GESTIONAR = "gestionar"
    PRESUPUESTAR = "presupuestar"
    APROBAR = "aprobar"
    TERMINAR = "terminar"
    ENLAZAR_GASTO = "enlazar_gasto"
    NO_REALIZADA = "no_realizada"
    RETIRAR = "retirar"
    AGREGAR_FOTO = "agregar_foto"


class ReparacionNueva(BaseModel):
    """`POST …/reparaciones`: la descripción y el lugar. Las fotos se suben después, una por una
    (`POST …/reparaciones/{r}/adjuntos?tipo=foto`)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    descripcion: Descripcion
    unidad_id: UUID | None = Field(
        default=None,
        description="Una unidad de quien pide (quien gestiona, cualquiera). Sin unidad, es un"
        " espacio común y `lugar` es obligatorio.",
    )
    lugar: Lugar | None = Field(
        default=None, description="Dónde, en pocas palabras (obligatorio en un espacio común)."
    )

    @model_validator(mode="after")
    def _con_lugar(self) -> "ReparacionNueva":
        if self.unidad_id is None and self.lugar is None:
            raise ValueError("un espacio común lleva el lugar")
        return self


class GestionReparacion(BaseModel):
    """`POST …/reparaciones/{r}/gestion`: la primera vez la reparación pasa a `en_gestion`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    responsable_id: UUID = Field(description="Una persona con `reparaciones.gestionar`.")
    prioridad: Prioridad
    fecha_comprometida: date | None = None


class PresupuestoReparacion(BaseModel):
    """`POST …/reparaciones/{r}/presupuesto`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    adjunto_id: UUID = Field(description="El presupuesto subido y disponible (`tipo=presupuesto`).")
    proveedor_nombre: Proveedor
    proveedor_cuit: str | None = Field(
        default=None,
        pattern=FORMATO_CUIT,
        description="Optativo; la base comprueba el dígito verificador (`cuit_invalido`).",
    )


class AprobacionReparacion(BaseModel):
    """`POST …/reparaciones/{r}/aprobacion`: la constancia de quién aprobó el presupuesto, con una
    decisión formal del edificio o un documento firmado (uno de los dos). En Modo Consejo la
    administración ejecuta fuera de la plataforma: es una constancia, no una aprobación de pago."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    aprobado_por: AprobadoPor = Field(description="Quién aprobó (p. ej. «Asamblea del 3/11»).")
    decision_id: UUID | None = None
    adjunto_id: UUID | None = Field(
        default=None, description="El documento firmado subido y disponible (`tipo=aprobacion`)."
    )

    @model_validator(mode="after")
    def _una_constancia(self) -> "AprobacionReparacion":
        if (self.decision_id is None) == (self.adjunto_id is None):
            raise ValueError("una decisión formal o un documento, uno de los dos")
        return self


class TerminacionReparacion(BaseModel):
    """`POST …/reparaciones/{r}/terminacion`: hecha, con la factura. Queda
    `terminada_gasto_pendiente` hasta que se enlaza el gasto."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    adjunto_id: UUID = Field(description="La factura subida y disponible (`tipo=factura`).")


class GastoReparacion(BaseModel):
    """`POST …/reparaciones/{r}/gasto`: el cierre."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    liquidacion_id: UUID = Field(description="Una liquidación oficial vigente (confirmada).")
    clave: str = Field(pattern=FORMATO_GASTO, description="La clave del gasto en esa liquidación.")


class MotivoReparacion(BaseModel):
    """`POST …/reparaciones/{r}/no-realizada` y `…/retiro`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    motivo: Motivo


class AdjuntoReparacionSubido(BaseModel):
    """`POST …/reparaciones/{r}/adjuntos`: el archivo, en cuarentena hasta que el antivirus lo
    acepta. Un acto lo cita por `adjunto_id` recién cuando está disponible."""

    model_config = ConfigDict(frozen=True)

    adjunto_id: UUID
    documento_id: UUID
    archivo: VersionCargada


class AdjuntoReparacion(BaseModel):
    """Un archivo de la reparación: los disponibles y, de los otros, los que subió quien lee."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    tipo: TipoAdjunto
    estado: EstadoVersion
    tipo_archivo: TipoArchivo
    subido_en: datetime
    propio: bool
    usado: bool = Field(description="Si ya lo cita un paso de la historia.")


class GastoOficial(BaseModel):
    """Un gasto de una liquidación oficial, tal como figura (la reparación no guarda importes)."""

    model_config = ConfigDict(frozen=True)

    liquidacion_id: UUID
    periodo: date
    clave: str
    concepto: str | None
    total: Decimal | None


class PasoReparacion(BaseModel):
    """Un paso de la historia, con lo que registró."""

    model_config = ConfigDict(frozen=True)

    estado: EstadoReparacion
    motivo: str | None
    adjunto_id: UUID | None
    proveedor_nombre: str | None
    proveedor_cuit: str | None
    aprobado_por: str | None
    facultad: str | None = Field(description="Con qué facultad se registró la aprobación.")
    decision_id: UUID | None
    decision_descripcion: str | None
    decision_fecha: date | None
    gasto: GastoOficial | None
    registrado_por_nombre: str
    registrado_en: datetime


class GestionVigente(BaseModel):
    model_config = ConfigDict(frozen=True)

    responsable_id: UUID
    responsable_nombre: str
    prioridad: Prioridad
    fecha_comprometida: date | None


class ReparacionResumen(BaseModel):
    """Una fila de la lista."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    numero: int
    estado: EstadoReparacion
    unidad_id: UUID | None
    designacion: str | None = Field(description="La unidad; nulo en un espacio común.")
    lugar: str | None
    gestion: GestionVigente | None
    pedida_por_nombre: str
    pedida_en: datetime
    propia: bool = Field(description="Si la pidió la persona que lee.")


class Persona(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    nombre: str


class DecisionCitable(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    tipo: str
    fecha: date
    descripcion: str


class Reparacion(ReparacionResumen):
    """`GET …/reparaciones/{r}` y la respuesta de cada acto. Para quien gestiona, lo que puede
    elegir en el paso que se le ofrece."""

    descripcion: str
    historia: list[PasoReparacion] = Field(description="Del primer paso al último.")
    adjuntos: list[AdjuntoReparacion]
    acciones: list[AccionReparacion]
    responsables_posibles: list[Persona] = Field(
        description="Con `gestionar`: las personas con `reparaciones.gestionar`."
    )
    decisiones_citables: list[DecisionCitable] = Field(
        description="Con `aprobar`: las decisiones formales del edificio."
    )
    gastos_enlazables: list[GastoOficial] = Field(
        description="Con `enlazar_gasto`: los gastos de las liquidaciones oficiales vigentes que"
        " todavía no cierran ninguna reparación."
    )


class UnidadParaPedir(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    designacion: str


class ListaReparaciones(BaseModel):
    """`GET …/reparaciones`: la más nueva primero. Quien gestiona ve todas; quien pidió, las
    suyas."""

    model_config = ConfigDict(frozen=True)

    elementos: list[ReparacionResumen]
    gestiona: bool
    pide: bool = Field(description="Si la persona puede pedir una reparación ahora.")
    unidades: list[UnidadParaPedir] = Field(description="Las unidades por las que puede pedir.")
    largo_descripcion: int
    largo_lugar: int
    largo_motivo: int
    largo_proveedor: int
    largo_aprobado_por: int
    fotos_maximo: int
