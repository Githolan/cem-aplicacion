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

from cem_contracts.mandatos_invitaciones import FORMATO_REFERENCIA, LARGO_MAXIMO_REFERENCIA

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

    @model_validator(mode="after")
    def _coherente(self) -> Self:
        if self.hasta is not None and self.hasta < self.desde:
            raise ValueError("hasta no puede ser anterior a desde")
        if self.persona_id is None and self.nombre is None:
            raise ValueError("sin cuenta, el mandato necesita el nombre del mandatario")
        return self


class MandatoRegistrado(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID


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


class EstadoMandato(StrEnum):
    """Para leer, contra el hoy del consorcio (A1: `vigente_hasta` exclusivo)."""

    VIGENTE = "vigente"
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


class DecisionResumen(BaseModel):
    """Una fila de la lista de decisiones (2b-4c): sin la referencia del acta ni su huella."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    tipo: TipoDecision
    fecha: date
    descripcion: str


class ListaDecisiones(BaseModel):
    model_config = ConfigDict(frozen=True)

    elementos: list[DecisionResumen]


class DecisionDetalle(DecisionResumen):
    """El detalle autorizado. Quién la registró no sale: solo si fue quien lee (A15: no puede
    usarla para dar de alta ni para remover) o el equipo."""

    referencia: str
    sha256: str | None
    registrada_por_vos: bool
    del_equipo: bool


class CandidatoMandato(BaseModel):
    """Una persona a la que se le puede dar un mandato de Consejo o de administración."""

    model_config = ConfigDict(frozen=True)

    persona_id: UUID
    nombre: str
    unidades: list[str] = Field(description="Designaciones de sus unidades con vínculo confirmado.")


class ListaCandidatos(BaseModel):
    model_config = ConfigDict(frozen=True)

    elementos: list[CandidatoMandato]
