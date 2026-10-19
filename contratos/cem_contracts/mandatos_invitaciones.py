"""A7: la cuenta del mandatario externo (H2b 2b-4, PR 4; decisión P1 del propietario).

Un administrador externo cargado sin cuenta tiene su identidad jurídica en el CUIT; el sistema
solo puede comprobar que alguien controla un correo. Por eso la asociación es una invitación al
correo del mandatario con cuatro ojos: una persona con `mandatos.registrar` la emite con la
referencia de dónde sale ese correo, otra distinta la aprueba (recién ahí sale el correo con el
enlace `/invitacion-mandato#token=…`), y la acepta la persona de ese mismo correo. Corregir una
cuenta asociada por error es desasociar, no dar de baja el mandato.

El CUIT no viaja en ninguna respuesta. El correo y la referencia solo los ve, mientras la
invitación está por aprobar, quien la emitió o quien tiene `mandatos.registrar` en el consorcio.
"""

from datetime import date, datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from cem_contracts.identidad import FORMATO_CORREO, FORMATO_TOKEN, LARGO_MAXIMO_CORREO
from cem_contracts.invitaciones import FORMATO_NOMBRE, LARGO_MAXIMO_NOMBRE

LARGO_MAXIMO_REFERENCIA = 300
"""La migración 0015 impone el mismo tope."""
FORMATO_REFERENCIA = r"^[^\x00-\x1f\x7f]*\S[^\x00-\x1f\x7f]*$"
"""Al menos un carácter visible y ningún control."""


class EstadoInvitacionMandato(StrEnum):
    POR_APROBAR = "por_aprobar"
    """La emitió una persona y falta que la apruebe otra; todavía no salió ningún correo."""
    PENDIENTE = "pendiente"
    """Aprobada: el enlace salió y no venció."""
    VENCIDA = "vencida"
    ACEPTADA = "aceptada"
    REVOCADA = "revocada"
    """La retiraron, o la reemplazó una invitación nueva al mismo mandato."""


class InvitacionMandatoNueva(BaseModel):
    """`POST /api/v1/consorcios/{consorcio_id}/mandatos/{mandato_id}/invitaciones`."""

    model_config = ConfigDict(frozen=True)

    correo: str = Field(
        max_length=LARGO_MAXIMO_CORREO,
        pattern=FORMATO_CORREO,
        description="El correo del mandatario que figura en el respaldo.",
        examples=["administracion@estudio.com"],
    )
    referencia: str = Field(
        max_length=LARGO_MAXIMO_REFERENCIA,
        pattern=FORMATO_REFERENCIA,
        description="De dónde sale ese correo: contrato de administración, nota del"
        " administrador u otro instrumento (P1-b). Queda en la invitación, no en la auditoría.",
        examples=["Contrato de administración del 01-10-2026, cláusula 12"],
    )


class InvitacionMandato(BaseModel):
    """Una invitación de mandato, como la ve quien tiene `mandatos.registrar`."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    estado: EstadoInvitacionMandato
    correo: str | None = Field(
        description="Solo mientras está por aprobar, para quien la emitió o tiene"
        " `mandatos.registrar`; nulo en cualquier otro estado."
    )
    referencia: str | None = Field(description="Con la misma regla que el correo.")
    origen: str = Field(description="`api`, o `soporte` si la emitió el equipo en un paso.")
    emitida_por_mi: bool
    creada_en: datetime
    aprobada_en: datetime | None = None
    vence_en: datetime | None = None
    aceptada_en: datetime | None = None
    revocada_en: datetime | None = None


class InvitacionMandatoEmitida(BaseModel):
    """Respuesta de emitir: queda por aprobar, sin enlace ni correo enviado."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    mandato_id: UUID
    estado: EstadoInvitacionMandato = Field(description="Siempre `por_aprobar`.")


class InvitacionMandatoAprobada(BaseModel):
    """Respuesta de aprobar: el correo con el enlace salió por la cola."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    estado: EstadoInvitacionMandato = Field(description="Siempre `pendiente`.")
    vence_en: datetime


class ListaInvitacionesMandato(BaseModel):
    """`GET /api/v1/consorcios/{consorcio_id}/mandatos/{mandato_id}/invitaciones`."""

    model_config = ConfigDict(frozen=True)

    elementos: list[InvitacionMandato]


class Desasociacion(BaseModel):
    """`POST /api/v1/consorcios/{consorcio_id}/mandatos/{mandato_id}/desasociar-persona`."""

    model_config = ConfigDict(frozen=True)

    referencia: str = Field(
        max_length=LARGO_MAXIMO_REFERENCIA,
        pattern=FORMATO_REFERENCIA,
        description="Por qué la cuenta asociada no es la del mandatario (P1-c). Queda en la"
        " fila de la desasociación, no en la auditoría.",
    )


class DesasociacionHecha(BaseModel):
    """Respuesta de desasociar: el mandato, su CUIT y su vigencia quedan como estaban."""

    model_config = ConfigDict(frozen=True)

    mandato_id: UUID
    sesiones_revocadas: int = Field(description="Sesiones de la persona que se cerraron (RN22).")


class TokenInvitacionMandato(BaseModel):
    """`POST /api/v1/auth/invitacion-mandato/consultar`: el token del fragmento del enlace."""

    model_config = ConfigDict(frozen=True)

    token: str = Field(pattern=FORMATO_TOKEN)


class InvitacionMandatoRecibida(BaseModel):
    """Lo que ve quien abre el enlace, antes de aceptar. Sin CUIT."""

    model_config = ConfigDict(frozen=True)

    consorcio: str
    mandatario: str | None = Field(description="Nombre del mandatario que figura en el mandato.")
    invitada_por: str | None = Field(
        description="Nombre de quien emitió; nulo si la emitió el equipo de la plataforma."
    )
    vence_en: datetime


class AceptacionInvitacionMandato(BaseModel):
    """`POST /api/v1/auth/invitacion-mandato/aceptar`."""

    model_config = ConfigDict(frozen=True)

    token: str = Field(pattern=FORMATO_TOKEN)
    nombre: str | None = Field(
        default=None,
        max_length=LARGO_MAXIMO_NOMBRE,
        pattern=FORMATO_NOMBRE,
        description="Solo para una cuenta nueva: el nombre con que queda. Sin él, el del"
        " mandatario. Una cuenta que ya existe conserva el suyo.",
    )


class InvitacionMandatoAceptada(BaseModel):
    """Respuesta de aceptar: el mandato quedó asociado a la persona."""

    model_config = ConfigDict(frozen=True)

    consorcio_id: UUID
    persona_id: UUID
    mandato_id: UUID
    cuenta_nueva: bool
    vigente_desde: date = Field(
        description="Desde cuándo vale el mandato: si es futuro, el consorcio aparece en"
        " `/me/consorcios` recién desde esa fecha."
    )
