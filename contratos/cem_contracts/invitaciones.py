"""Invitaciones y alta de personas (documento 08, RF03; documento 13, §10.3; plan H2 2a).

Una invitación es para un correo, una unidad y un tipo de vínculo. Llega por correo con un
enlace de un solo uso a la página `/invitacion#token=…`; quien la acepta queda con ese vínculo
y, si el correo no tenía cuenta, con una persona nueva y una sesión. Si el correo ya tenía
cuenta, hay que aceptarla con la sesión de esa misma persona: la invitación nunca abre la
cuenta de otro.
"""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from cem_contracts.estados import TipoVinculo
from cem_contracts.identidad import FORMATO_CORREO, FORMATO_TOKEN, LARGO_MAXIMO_CORREO

LARGO_MAXIMO_NOMBRE = 100
"""El nombre viaja en el correo de la invitación: corto, para que no sirva de mensaje libre."""
FORMATO_NOMBRE = r"^[^\x00-\x1f\x7f]*\S[^\x00-\x1f\x7f]*$"
"""Al menos un carácter visible y ningún control (sin saltos de línea dentro del correo)."""


class EstadoInvitacion(StrEnum):
    PENDIENTE = "pendiente"
    ACEPTADA = "aceptada"
    VENCIDA = "vencida"
    REVOCADA = "revocada"
    """La retiró quien invitó, o la reemplazó una invitación nueva al mismo correo, unidad y
    vínculo."""


class PasoInvitacion(StrEnum):
    """Lo que pasaría al aceptar con la sesión del pedido (`0109`, el mismo orden que
    `aceptar_invitacion`). No revela nada que la aceptación no diga ya a quien tiene el enlace."""

    CREAR_CUENTA = "crear_cuenta"
    """El correo no tiene cuenta y no hay sesión: aceptar crea la cuenta, con el nombre."""
    ACEPTAR = "aceptar"
    """La sesión es la de la cuenta del correo: aceptar no pide nombre."""
    ENTRAR = "entrar"
    """El correo ya tiene cuenta y no hay sesión: hay que entrar con ella."""
    OTRA_CUENTA = "otra_cuenta"
    """La sesión abierta es de otra cuenta."""
    PROPIA = "propia"
    """La sesión es la de quien la creó (o de su misma identidad humana): no la acepta (F9)."""


class InvitacionNueva(BaseModel):
    """`POST /api/v1/consorcios/{consorcio_id}/invitaciones`."""

    model_config = ConfigDict(frozen=True)

    unidad_id: UUID
    tipo: TipoVinculo
    correo: str = Field(
        max_length=LARGO_MAXIMO_CORREO, pattern=FORMATO_CORREO, examples=["beto@ejemplo.com"]
    )
    nombre: str = Field(
        max_length=LARGO_MAXIMO_NOMBRE,
        pattern=FORMATO_NOMBRE,
        description="Cómo se llama la persona invitada; ella lo puede corregir al aceptar.",
        examples=["Beto"],
    )


class Invitacion(BaseModel):
    """Una invitación, como la ve quien puede invitar en esa unidad."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    unidad_id: UUID
    unidad: str = Field(description="Designación de la unidad.", examples=["3° B"])
    tipo: TipoVinculo
    correo: str | None = Field(
        description="El correo invitado. Nulo en el listado cuando ya se aceptó: desde ahí es el"
        " correo con el que la persona entra, y nadie lo ve de forma automática (D6)."
    )
    nombre: str
    estado: EstadoInvitacion
    creada_en: datetime
    vence_en: datetime
    aceptada_en: datetime | None = None
    revocada_en: datetime | None = None
    importada: bool = Field(
        description="Si salió con el padrón importado (nombra su vínculo): una invitación manual"
        " no la reemplaza (`invitacion_importada_pendiente`).",
    )


class ListaInvitaciones(BaseModel):
    """`GET /api/v1/consorcios/{consorcio_id}/invitaciones`: la más reciente primero."""

    model_config = ConfigDict(frozen=True)

    elementos: list[Invitacion]


class ConsultaInvitacionImportada(BaseModel):
    """`POST …/invitaciones/importada-pendiente`: antes de invitar, si la persona de ese correo ya
    tiene una invitación del padrón importado para esa unidad y carácter. El correo va en el
    cuerpo, nunca en la URL."""

    model_config = ConfigDict(frozen=True)

    unidad_id: UUID
    tipo: TipoVinculo
    correo: str = Field(max_length=LARGO_MAXIMO_CORREO, pattern=FORMATO_CORREO)


class InvitacionImportadaConsultada(BaseModel):
    """Responde lo mismo que invitar respondería (409 `invitacion_importada_pendiente`), sin
    decir nada más de esa invitación."""

    model_config = ConfigDict(frozen=True)

    importada_pendiente: bool


class TokenInvitacion(BaseModel):
    """`POST /api/v1/auth/invitacion/consultar`: el token que la página `/invitacion` leyó del
    fragmento del enlace. Va en el cuerpo, nunca en la URL."""

    model_config = ConfigDict(frozen=True)

    token: str = Field(pattern=FORMATO_TOKEN)


class AceptacionInvitacion(BaseModel):
    """`POST /api/v1/auth/invitacion/aceptar`."""

    model_config = ConfigDict(frozen=True)

    token: str = Field(pattern=FORMATO_TOKEN)
    nombre: str | None = Field(
        default=None,
        max_length=LARGO_MAXIMO_NOMBRE,
        pattern=FORMATO_NOMBRE,
        description="Solo para una cuenta nueva: el nombre con que queda. Sin él, el de la"
        " invitación. Una cuenta que ya existe conserva el suyo.",
    )


class InvitacionRecibida(BaseModel):
    """Lo que ve quien abre una invitación pendiente, antes de aceptarla."""

    model_config = ConfigDict(frozen=True)

    consorcio: str = Field(description="Nombre del consorcio.", examples=["Edificio Alvear 100"])
    unidad: str = Field(examples=["3° B"])
    tipo: TipoVinculo
    correo: str = Field(description="El correo invitado: con él queda la cuenta.")
    nombre: str
    invitada_por: str | None = Field(
        description="Nombre de quien invitó; nulo si la cargó el equipo de la plataforma."
    )
    vence_en: datetime
    paso: PasoInvitacion = Field(
        description="Lo que pasaría al aceptar con la sesión de este pedido. Solo para mostrar:"
        " la aceptación vuelve a decidir."
    )


class InvitacionAceptada(BaseModel):
    """Respuesta de `POST /api/v1/auth/invitacion/aceptar`."""

    model_config = ConfigDict(frozen=True)

    consorcio_id: UUID
    persona_id: UUID
    cuenta_nueva: bool = Field(
        description="Se creó la persona y una sesión (llegan sus cookies). Si es falso, la"
        " persona ya tenía cuenta y aceptó con su sesión."
    )


class InvitacionPropia(BaseModel):
    """Una invitación de vínculo pendiente dirigida al correo de la persona de la sesión
    (`GET /api/v1/me/invitaciones`, RF02). Es lo único que puede hacer un importado pendiente:
    verla y aceptarla con su sesión."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    consorcio: str = Field(description="Nombre del consorcio.", examples=["Edificio Alvear 100"])
    unidad: str = Field(examples=["3° B"])
    tipo: TipoVinculo
    nombre: str
    invitada_por: str | None = Field(
        description="Nombre de quien invitó; nulo si la cargó el equipo de la plataforma."
    )
    vence_en: datetime


class ListaInvitacionesPropias(BaseModel):
    model_config = ConfigDict(frozen=True)

    elementos: list[InvitacionPropia]


class InvitacionPropiaAceptada(BaseModel):
    """Respuesta de `POST /api/v1/me/invitaciones/{id}/aceptar`: desde ahí, el vínculo sigue las
    reglas normales."""

    model_config = ConfigDict(frozen=True)

    consorcio_id: UUID
