"""Operación de mandatos y decisiones por la aplicación (H2b 2b-4b, PR 2).

Diseño: `doc/diseno/H2b_4b_Diseno.md` del repositorio CEM, §5. Registrar una decisión es cargar
un acto ya tomado (la referencia del acta es el respaldo, criterio 9); dar de alta y de baja un
mandato usa las funciones de la base tal cual, más A15: quien registró la decisión no la usa
para dar de alta ni para remover.

Las respuestas devuelven identificadores y fechas: ningún nombre, CUIT ni referencia de terceros.
Los topes son los de la base (`0010`, `0011`, `0012`); la base vuelve a validarlos (E26, `0016`).
"""

from datetime import date
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
    fecha: date = Field(description="Fecha del acto (asamblea, reunión, contrato o inscripción).")
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
        description="La cuenta del mandatario. Obligatoria salvo para el administrador externo,"
        " que puede cargarse sin cuenta y asociarla después (A7).",
    )
    nombre: str | None = Field(
        default=None,
        min_length=1,
        max_length=LARGO_MAXIMO_NOMBRE_MANDATARIO,
        pattern=FORMATO_REFERENCIA,
        description="Obligatorio si no hay cuenta: razón social o nombre del mandatario.",
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
