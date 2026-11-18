"""Operación de mandatos y decisiones por la aplicación (H2b 2b-4b, PR 2).

Diseño: `doc/diseno/H2b_4b_Diseno.md` del repositorio CEM, §5. Registrar una decisión es cargar
un acto ya tomado (la referencia del acta es el respaldo, criterio 9); dar de alta y de baja un
mandato usa las funciones de la base tal cual, más A15: quien registró la decisión no la usa
para dar de alta ni para remover.

Las respuestas de escritura devuelven identificadores y fechas: ningún nombre, CUIT ni
referencia de terceros. Los topes son los de la base (`0010`, `0011`, `0012`); la base vuelve a
validarlos (E26, `0016`).

**Lectura (2b-4c, `doc/diseno/H2b_4c_Diseno.md`).** Con `mandatos.ver` y `decisiones.ver`, de MC
y AD. Las listas son mínimas: sin referencias, instrumentos ni hashes, que van solo en el
detalle; el CUIT, solo del administrador externo. Nunca correo, teléfono ni el identificador de
la persona del mandato. Los candidatos al alta (con `mandatos.registrar`) son las personas con
un vínculo confirmado, vigente o futuro, en el consorcio.
"""

from datetime import date, datetime
from enum import StrEnum
from typing import Annotated, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from cem_contracts.estados import ModoOperacion
from cem_contracts.mandatos_invitaciones import (
    FORMATO_REFERENCIA,
    LARGO_MAXIMO_REFERENCIA,
    EstadoInvitacionMandato,
    InvitacionMandatoNueva,
)

LARGO_MAXIMO_DESCRIPCION = 500
LARGO_MAXIMO_NOMBRE_MANDATARIO = 200
LARGO_MAXIMO_MATRICULA = 100
FORMATO_CUIT = r"^[0-9]{11}$"
"""Once dígitos, sin guiones; el dígito verificador lo comprueba la base (`cuit_valido`)."""
FORMATO_SHA256 = r"^[0-9a-fA-F]{64}$"
FORMATO_FACULTAD = r"^[a-z][a-z_]*(\.[a-z_]+)+$"
MAXIMO_FACULTADES = 50

Facultad = Annotated[str, Field(max_length=100, pattern=FORMATO_FACULTAD)]


class TipoDecision(StrEnum):
    ASAMBLEA = "asamblea"
    CONSEJO = "consejo"
    CONTRATO_ADMINISTRACION = "contrato_administracion"
    INSCRIPCION_RPA = "inscripcion_rpa"


class TipoMandato(StrEnum):
    ADMINISTRADOR = "administrador"
    MIEMBRO_CONSEJO = "miembro_consejo"
    ADMINISTRADOR_EXTERNO = "administrador_externo"


class CausaBaja(StrEnum):
    REMOCION = "remocion"
    """Solo con una decisión de asamblea del mismo consorcio (C6)."""
    RENUNCIA = "renuncia"
    FALLECIMIENTO_INCAPACIDAD = "fallecimiento_incapacidad"
    ERROR_CARGA = "error_carga"
    OTRO = "otro"


def _texto(maximo: int, descripcion: str, ejemplo: str):
    return Field(
        min_length=1, max_length=maximo, pattern=FORMATO_REFERENCIA, description=descripcion,
        examples=[ejemplo],
    )  # fmt: skip


class DecisionNueva(BaseModel):
    """`POST /api/v1/consorcios/{consorcio_id}/decisiones`."""

    model_config = ConfigDict(frozen=True)

    tipo: TipoDecision
    fecha: date = Field(
        description="Fecha del acto (asamblea, reunión, contrato o inscripción). Hasta hoy en el"
        " consorcio (su día local): una fecha posterior responde 422 `fecha_futura`."
    )
    descripcion: str = _texto(LARGO_MAXIMO_DESCRIPCION, "Qué se decidió.", "Designa al Consejo")
    referencia: str = _texto(
        LARGO_MAXIMO_REFERENCIA,
        "Dónde consta: libro, acta y folio. Nunca se inventa (criterio 9).",
        "Acta 12, libro 3, folio 40",
    )
    sha256: str | None = Field(
        default=None, pattern=FORMATO_SHA256, description="Huella del acta escaneada, si existe."
    )


class ActaDecision(BaseModel):
    """`PUT /api/v1/consorcios/{consorcio_id}/decisiones/{decision_id}/acta` (`0110`,
    FL-DEC-01): ata el acta a una decisión que quedó sin ella."""

    model_config = ConfigDict(frozen=True)

    sha256: str = Field(
        pattern=FORMATO_SHA256,
        description="Huella de una versión disponible (ya analizada) de un documento vigente del"
        " consorcio: si no lo es, 422 `acta_no_disponible`. Una decisión con acta responde 409"
        " `acta_ya_registrada`.",
    )


class DecisionRegistrada(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID


class MandatoNuevo(BaseModel):
    """`POST /api/v1/consorcios/{consorcio_id}/mandatos`."""

    model_config = ConfigDict(frozen=True)

    tipo: TipoMandato
    persona_id: UUID | None = Field(
        default=None,
        description="La cuenta del mandatario, con un vínculo confirmado (vigente o futuro) en el"
        " consorcio. Obligatoria para el Consejo y el administrador. El administrador externo"
        " se carga sin cuenta: la cuenta se asocia por invitación (A7).",
    )
    nombre: str | None = Field(
        default=None,
        min_length=1,
        max_length=LARGO_MAXIMO_NOMBRE_MANDATARIO,
        pattern=FORMATO_REFERENCIA,
        description="Obligatorio si no hay cuenta: razón social o nombre del mandatario. Del"
        " Consejo y del administrador se muestra siempre el nombre de la cuenta.",
    )
    cuit: str | None = Field(
        default=None, pattern=FORMATO_CUIT, description="Obligatorio para el administrador externo."
    )
    matricula: str | None = Field(
        default=None, min_length=1, max_length=LARGO_MAXIMO_MATRICULA, pattern=FORMATO_REFERENCIA
    )
    decision_id: UUID = Field(
        description="La decisión de respaldo, del mismo consorcio, que no registró quien da el"
        " alta (A15)."
    )
    desde: date
    hasta: date | None = Field(
        default=None, description="Exclusivo (A1): el último día es el anterior."
    )
    facultades: list[Facultad] | None = Field(
        default=None,
        max_length=MAXIMO_FACULTADES,
        description="Un subconjunto del paquete del tipo; sin lista, el paquete completo.",
    )
    invitacion: InvitacionMandatoNueva | None = Field(
        default=None,
        description="Solo para el administrador externo: el correo del mandatario y de dónde sale."
        " Con él, el alta emite también la invitación (A7), que queda por aprobar por otra"
        " persona, como si se emitiera después (pregunta 17 del propietario, 18-11-2026;"
        " FL-MAN-06). Pide `mandatos.asociar_persona`.",
    )

    @model_validator(mode="after")
    def _coherente(self) -> Self:
        if self.hasta is not None and self.hasta < self.desde:
            raise ValueError("hasta no puede ser anterior a desde")
        if self.persona_id is None and self.nombre is None:
            raise ValueError("sin cuenta, el mandato necesita el nombre del mandatario")
        if self.invitacion is not None and self.tipo != TipoMandato.ADMINISTRADOR_EXTERNO:
            raise ValueError("la invitación con el alta es solo del administrador externo")
        return self


class MandatoRegistrado(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    invitacion_id: UUID | None = Field(
        default=None,
        description="La invitación emitida con el alta, por aprobar; nula si no se pidió.",
    )


class BajaMandato(BaseModel):
    """`POST /api/v1/consorcios/{consorcio_id}/mandatos/{mandato_id}/baja`."""

    model_config = ConfigDict(frozen=True)

    causa: CausaBaja
    decision_id: UUID | None = Field(
        default=None,
        description="Solo para la remoción: una asamblea del mismo consorcio que no registró"
        " quien da la baja (C6, A15).",
    )
    instrumento: str | None = Field(
        default=None,
        min_length=1,
        max_length=LARGO_MAXIMO_REFERENCIA,
        pattern=FORMATO_REFERENCIA,
        description="Para toda causa que no sea la remoción: la nota, el certificado u otro"
        " instrumento.",
    )
    descripcion: str = _texto(LARGO_MAXIMO_DESCRIPCION, "Por qué termina.", "Renuncia presentada")

    @model_validator(mode="after")
    def _respaldo_segun_causa(self) -> Self:
        if self.causa is CausaBaja.REMOCION:
            if self.decision_id is None or self.instrumento is not None:
                raise ValueError("la remoción se respalda solo con una decisión de asamblea")
        elif self.decision_id is not None or self.instrumento is None:
            raise ValueError("esta causa se respalda solo con la referencia del instrumento")
        return self


class MandatoDadoDeBaja(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    causa: CausaBaja
    vigente_hasta: date | None = Field(description="Exclusivo (A1).")


class RevisionMandato(BaseModel):
    """`POST /api/v1/consorcios/{consorcio_id}/mandatos/{mandato_id}/revision` (E62, alternativa 1
    del propietario): le da eficacia hacia adelante a un mandato del Consejo pendiente de
    revisión. No toca el mandato ni cubre el período en que estuvo sin efecto."""

    model_config = ConfigDict(frozen=True)

    decision_id: UUID = Field(
        description="Una asamblea del mismo consorcio, con fecha hasta hoy, que no registró quien"
        " revisa (A15)."
    )
    eficaz_desde: date = Field(
        description="Desde qué día vuelve a operar: no antes de la decisión ni de hoy en el"
        " consorcio, y dentro del mandato. De hoy a ese día la persona tiene que cumplir, sin"
        " interrupción, una condición habilitante."
    )


class MandatoRevisado(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID = Field(description="La revisión.")
    mandato_id: UUID
    eficaz_desde: date


class EstadoMandato(StrEnum):
    """Para leer, contra el hoy del consorcio (A1: `vigente_hasta` exclusivo)."""

    VIGENTE = "vigente"
    PENDIENTE_REVISION = "pendiente_revision"
    """Vigente por sus fechas, pero un mandato de Consejo cuya persona dejó de cumplir toda
    condición habilitante para integrarlo (E62, migración 0023): conserva su historia, no
    autoriza actos nuevos y espera la decisión competente. Volver a cumplirla no lo reactiva."""
    FUTURO = "futuro"
    TERMINADO = "terminado"
    """Terminó por su fecha, sin baja."""
    DADO_DE_BAJA = "dado_de_baja"
    ERROR_CARGA = "error_carga"
    """Dado de baja por error de carga: nunca valió."""


class MandatoResumen(BaseModel):
    """Una fila de la lista de mandatos (2b-4c): lo mínimo."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    tipo: TipoMandato
    nombre: str = Field(
        description="El del mandatario sin cuenta (razón social) o el de la persona del mandato."
    )
    cuit: str | None = Field(description="Solo del administrador externo.")
    vigente_desde: date
    vigente_hasta: date | None = Field(description="Exclusivo (A1).")
    estado: EstadoMandato
    con_cuenta: bool = Field(description="Si el mandato tiene una cuenta asociada.")
    invitacion: EstadoInvitacionMandato | None = Field(
        description="El estado de la última invitación del mandato, para quien ve las"
        " asociaciones (`mandatos.ver_asociaciones`); nulo si no tiene o si quien lee no las ve"
        " (FL-MAN-04)."
    )
    invitacion_emitida_por_mi: bool = Field(
        description="Si esa invitación la emitió quien lee: no la aprueba (cuatro ojos)."
    )


class ListaMandatos(BaseModel):
    model_config = ConfigDict(frozen=True)

    elementos: list[MandatoResumen]


class MandatoDetalle(MandatoResumen):
    """El detalle autorizado: suma la matrícula, la decisión, las facultades y la baja."""

    matricula: str | None
    decision_id: UUID
    facultades: list[str]
    causa_baja: CausaBaja | None
    baja_decision_id: UUID | None
    instrumento_referencia: str | None
    motivo_fin: str | None
    finalizado_en: datetime | None
    es_propio: bool = Field(
        description="Si el mandato es de la cuenta de quien lee: nadie registra su propia baja (A4,"
        " por cuenta). Solo para mostrar: la ruta vuelve a decidir."
    )
    condicion_cumplida_hoy: bool | None = Field(
        description="En un mandato pendiente de revisión: si la persona cumple hoy una condición"
        " para integrar el Consejo, medida del padrón como al mantenerlo (`0109`). Sin ella, la"
        " revisión que lo mantiene se niega (`sin_condicion_habilitante`). Nulo en otro estado."
    )


class DecisionResumen(BaseModel):
    """Una fila de la lista de decisiones (2b-4c): sin la referencia del acta ni su huella."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    tipo: TipoDecision
    fecha: date
    descripcion: str
    registrada_por_vos: bool = Field(
        description="Si la registró quien lee, con esta cuenta u otra del mismo ser humano: no la"
        " usa para dar de alta ni para remover (A15). Solo para mostrar: la ruta vuelve a decidir."
    )
    del_equipo: bool = Field(description="Si la cargó el equipo: no activa A15.")
    tiene_acta: bool = Field(
        description="Si ya tiene la huella del acta. Sin ella, quien registra decisiones la puede"
        " adjuntar (`PUT …/decisiones/{id}/acta`, `0110`, FL-DEC-01)."
    )


class ListaDecisiones(BaseModel):
    model_config = ConfigDict(frozen=True)

    elementos: list[DecisionResumen]


class PeriodoModo(BaseModel):
    """Un período de la historia de modos (RF41-C, 2b-6b): lo mínimo. Quién lo registró no sale
    (lo registra siempre el equipo) y la decisión se lee con `decisiones.ver`."""

    model_config = ConfigDict(frozen=True)

    modo: ModoOperacion
    vigente_desde: date
    vigente_hasta: date | None = Field(description="Sin fecha: es el modo vigente.")
    decision_id: UUID


class ListaModos(BaseModel):
    """La historia completa, del más reciente al más antiguo. Vacía: el consorcio todavía no
    tiene modo registrado."""

    model_config = ConfigDict(frozen=True)

    elementos: list[PeriodoModo]


class DecisionDetalle(DecisionResumen):
    """El detalle autorizado. Quién la registró no sale: solo si fue quien lee (A15: no puede
    usarla para dar de alta ni para remover) o el equipo."""

    referencia: str
    sha256: str | None


class CandidatoMandato(BaseModel):
    """Una persona a la que se le puede dar un mandato de Consejo o de administración."""

    model_config = ConfigDict(frozen=True)

    persona_id: UUID
    nombre: str
    unidades: list[str] = Field(description="Designaciones de sus unidades con vínculo confirmado.")
    habilita_consejo: bool = Field(
        description="Si hoy cumple la condición para integrar el Consejo con un vínculo"
        " confirmado de un carácter del catálogo (`0023`; hoy, propietario). El alta de un"
        " «Consejo de propietarios» se ofrece solo a quien la cumple (pregunta 8 del"
        " propietario, 18-11-2026; FL-MAN-02)."
    )
    mandatos: list[TipoMandato] = Field(
        description="Los tipos de sus mandatos vigentes o futuros, sin baja: el alta de uno"
        " superpuesto del mismo tipo, o de un Consejo con administración, se niega (FL-MAN-03)."
    )


class ListaCandidatos(BaseModel):
    model_config = ConfigDict(frozen=True)

    elementos: list[CandidatoMandato]
