"""Contactos de emergencia del consorcio (H2b 2b-7; RF06, primera parte; D5).

Diseño: `doc/diseno/H2b_7_Diseno.md` del repositorio CEM, con la decisión del propietario del
25-10-2026 (`Decisiones_Propietario_2026-10-25.md`, §1.1).

- **Solo datos de contacto que no son personales.** No existe la naturaleza «persona particular».
  Un `profesional` figura solo con su número profesional y la constancia de que se consintió
  publicarlo.
- **Ningún número ni correo fuera de `telefono`** (§2.1): `nombre`, `indicaciones` y
  `observacion` rechazan una secuencia de 8 o más dígitos, aunque tenga separadores, y algo con
  forma de correo. La base repite la regla (`nucleo.sin_contacto_libre`, `0031`).
- **La lista general** (`contactos.ver`, por ámbito) es la que el dispositivo guarda sin
  conexión: sin quién actualizó, verificó u observó, la observación ni la constancia. **La de
  gestión** (`contactos.gestionar`) las agrega, y nunca se guarda.
"""

import re
from datetime import date, datetime
from enum import StrEnum
from typing import Annotated, Self
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

LARGO_MAXIMO_NOMBRE = 120
LARGO_MAXIMO_TEXTO = 300
FORMATO_TELEFONO = r"^[+0-9][0-9 ()-]{2,29}$"
TELEFONO_EN_TEXTO = re.compile(r"([0-9][^0-9a-zA-Z]{0,2}){7}[0-9]")
"""8 o más dígitos con hasta dos separadores entre cada uno: el mismo patrón que la base."""
CORREO_EN_TEXTO = re.compile(r"[^\s@]+@[^\s@]+")


def _sin_contacto_libre(texto: str) -> str:
    """Recorta, exige algo y rechaza un teléfono o un correo dentro del texto libre (§2.1)."""
    texto = texto.strip()
    if not texto:
        raise ValueError("El campo no puede quedar vacío.")
    if TELEFONO_EN_TEXTO.search(texto) or CORREO_EN_TEXTO.search(texto):
        raise ValueError("Sin teléfonos ni correos acá: el número va solo en el teléfono.")
    return texto


def _texto(largo: int):
    return Annotated[str, Field(max_length=largo), AfterValidator(_sin_contacto_libre)]


Nombre = _texto(LARGO_MAXIMO_NOMBRE)
TextoLibre = _texto(LARGO_MAXIMO_TEXTO)
Telefono = Annotated[str, Field(pattern=FORMATO_TELEFONO, examples=["0800-555-1234"])]
Referencia = Annotated[str, Field(min_length=1, max_length=LARGO_MAXIMO_TEXTO)]


class FuncionContacto(StrEnum):
    """D5: administración, encargado, emergencias, aseguradora y proveedores críticos, más dos."""

    ADMINISTRACION = "administracion"
    ENCARGADO = "encargado"
    EMERGENCIAS = "emergencias"
    ASEGURADORA = "aseguradora"
    PROVEEDOR_CRITICO = "proveedor_critico"
    SERVICIO_PUBLICO = "servicio_publico"
    OTRO = "otro"


class NaturalezaContacto(StrEnum):
    EMPRESA = "empresa"
    SERVICIO = "servicio"
    ORGANISMO = "organismo"
    PROFESIONAL = "profesional"
    """Solo con su número profesional y la constancia del consentimiento (§1.1)."""


class DatosContacto(BaseModel):
    """Lo que se carga o se corrige."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    funcion: FuncionContacto
    naturaleza: NaturalezaContacto
    nombre: Nombre
    telefono: Telefono | None = None
    consentimiento_referencia: Referencia | None = Field(
        default=None,
        description="Dónde consta que el número es profesional y que se consintió publicarlo."
        " Obligatoria para un profesional; nula para las demás naturalezas.",
    )
    indicaciones: TextoLibre | None = None

    @model_validator(mode="after")
    def _profesional(self) -> Self:
        profesional = self.naturaleza == NaturalezaContacto.PROFESIONAL
        if profesional != (self.consentimiento_referencia is not None):
            raise ValueError(
                "La constancia del consentimiento va solo, y siempre, con un profesional."
            )
        if profesional and self.telefono is None:
            raise ValueError("Un profesional figura solo con su número profesional.")
        return self


class ContactoNuevo(DatosContacto):
    vigente_desde: date | None = Field(
        default=None, description="Por omisión, hoy en el consorcio."
    )


class Senalamiento(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    motivo: TextoLibre


class ContactoId(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID


class Contacto(BaseModel):
    """Un contacto de la lista general: lo que se publica y se guarda sin conexión."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    funcion: FuncionContacto
    naturaleza: NaturalezaContacto
    nombre: str
    telefono: str | None
    indicaciones: str | None
    vigente_desde: date
    vigente_hasta: date | None
    actualizado_en: datetime
    verificado_en: datetime | None
    observado: bool = Field(description="Hay un dato señalado como incorrecto sin resolver.")


class ListaContactos(BaseModel):
    model_config = ConfigDict(frozen=True)

    elementos: list[Contacto]
    actualizada_en: datetime | None = Field(
        description="El último cambio o verificación de cualquier contacto vigente."
    )


class ContactoGestion(Contacto):
    """La lista de gestión: con quién cambió qué. Nunca se guarda sin conexión."""

    consentimiento_referencia: str | None
    observacion: str | None
    actualizado_por: UUID | None
    verificado_por: UUID | None
    observado_por: UUID | None


class ListaContactosGestion(BaseModel):
    model_config = ConfigDict(frozen=True)

    elementos: list[ContactoGestion]
