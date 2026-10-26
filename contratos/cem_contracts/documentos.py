"""Documentos del consorcio: carga a cuarentena (H2c 2c-1; RF24, primera parte).

Diseño: `doc/diseno/H2c_Diseno.md` del repositorio CEM, §2 y §3, con las decisiones del
propietario del 25-10-2026 (`Decisiones_Propietario_2026-10-25.md`, §1.3).

- **En dos pasos:** se crea el documento (categoría, título y fecha) y después se sube cada
  archivo crudo en el cuerpo, sin multipart, con su `Content-Type`.
- **Solo PDF, JPEG y PNG, hasta 20 MB.** El tipo real sale de los primeros bytes, no de la
  extensión ni del tipo declarado.
- **Cada archivo entra en cuarentena** y recién se sirve cuando el análisis lo aprueba (2c-2
  trae la lista y la descarga).
- El título no admite teléfonos ni correos, como el texto libre de los contactos (2b-7); la base
  repite la regla (`nucleo.sin_contacto_libre`).
"""

from datetime import date
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from cem_contracts.contactos import _sin_contacto_libre

LARGO_MAXIMO_TITULO = 200
TOPE_BYTES = 20 * 1024 * 1024
"""20 MB (decisión del 25-10, §1.3). Un byte más es 413."""


class CategoriaDocumento(StrEnum):
    """Las 8 categorías de §4. Quién ve y quién carga cada una llega en 2c-2."""

    INSTITUCIONAL = "institucional"
    ACTAS_Y_DECISIONES = "actas_y_decisiones"
    SEGUROS = "seguros"
    CONTRATOS_PROVEEDORES = "contratos_proveedores"
    FACTURAS_Y_COMPROBANTES = "facturas_y_comprobantes"
    PRESUPUESTOS = "presupuestos"
    MANTENIMIENTO_Y_CERTIFICADOS = "mantenimiento_y_certificados"
    OTROS = "otros"


class TipoArchivo(StrEnum):
    PDF = "application/pdf"
    JPEG = "image/jpeg"
    PNG = "image/png"


Titulo = Annotated[str, Field(max_length=LARGO_MAXIMO_TITULO), AfterValidator(_sin_contacto_libre)]


class DocumentoNuevo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    categoria: CategoriaDocumento
    titulo: Titulo
    fecha: date = Field(description="Fecha del documento (no la de la carga).")


class DocumentoId(BaseModel):
    id: UUID


class VersionCargada(BaseModel):
    id: UUID
    estado: Literal["en_cuarentena"] = Field(
        description="Toda versión nueva entra en cuarentena hasta que el análisis la resuelve."
    )
    tipo: TipoArchivo = Field(description="El tipo real, leído de los primeros bytes.")
    tamano: int
    sha256: str
