"""Documentos del consorcio: carga a cuarentena (H2c 2c-1; RF24, primera parte).

Diseño: `doc/diseno/H2c_Diseno.md` del repositorio CEM, §2 y §3, con las decisiones del
propietario del 25-10-2026 (`Decisiones_Propietario_2026-10-25.md`, §1.3).

- **En dos pasos:** se crea el documento (categoría, título y fecha) y después se sube cada
  archivo crudo en el cuerpo, sin multipart, con su `Content-Type`.
- **Solo PDF, JPEG y PNG, hasta 20 MB.** El tipo real sale de los primeros bytes, no de la
  extensión ni del tipo declarado.
- **Cada archivo entra en cuarentena** y recién se sirve cuando el análisis lo aprueba.
- **Quién ve y quién carga cada categoría** (2c-2, §4): un mapa cerrado de categoría a acción.
  `contratos_proveedores` y `otros` se leen con `documentos.ver_restringidos` (la facultad
  `documentos.ver`, sin concesión por vínculo); las demás, con `documentos.ver`. Actas,
  presupuestos y mantenimiento se cargan también con `documentos.cargar_operativos`.
- **La descarga** responde 302 a una URL firmada de 5 minutos, solo para una versión
  `disponible`; nunca hay una URL permanente.
- El título no admite teléfonos ni correos, como el texto libre de los contactos (2b-7); la base
  repite la regla (`nucleo.sin_contacto_libre`).
"""

from datetime import date, datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from cem_contracts.contactos import _sin_contacto_libre

LARGO_MAXIMO_TITULO = 200
TOPE_BYTES = 20 * 1024 * 1024
"""20 MB (decisión del 25-10, §1.3). Un byte más es 413."""


class CategoriaDocumento(StrEnum):
    """Las 8 categorías de §4. Quién ve y quién carga cada una: `ACCION_DE_LECTURA` y
    `ACCIONES_DE_CARGA`."""

    INSTITUCIONAL = "institucional"
    ACTAS_Y_DECISIONES = "actas_y_decisiones"
    SEGUROS = "seguros"
    CONTRATOS_PROVEEDORES = "contratos_proveedores"
    FACTURAS_Y_COMPROBANTES = "facturas_y_comprobantes"
    PRESUPUESTOS = "presupuestos"
    MANTENIMIENTO_Y_CERTIFICADOS = "mantenimiento_y_certificados"
    OTROS = "otros"


CATEGORIAS_RESTRINGIDAS = frozenset(
    {CategoriaDocumento.CONTRATOS_PROVEEDORES, CategoriaDocumento.OTROS}
)
"""Las ve solo quien tiene la facultad `documentos.ver`, nunca un propietario por su vínculo
(Codex en CEM #86)."""
CATEGORIAS_OPERATIVAS = frozenset(
    {
        CategoriaDocumento.ACTAS_Y_DECISIONES,
        CategoriaDocumento.PRESUPUESTOS,
        CategoriaDocumento.MANTENIMIENTO_Y_CERTIFICADOS,
    }
)
"""Las que el Consejo carga con `documentos.cargar_operativos` (inferencia de §4, va al
propietario en §8)."""

ACCION_DE_LECTURA: dict[CategoriaDocumento, str] = {
    c: "documentos.ver_restringidos" if c in CATEGORIAS_RESTRINGIDAS else "documentos.ver"
    for c in CategoriaDocumento
}
ACCIONES_DE_CARGA: dict[CategoriaDocumento, tuple[str, ...]] = {
    c: ("documentos.cargar", "documentos.cargar_operativos")
    if c in CATEGORIAS_OPERATIVAS
    else ("documentos.cargar",)
    for c in CategoriaDocumento
}


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


class EstadoVersion(StrEnum):
    EN_CUARENTENA = "en_cuarentena"
    DISPONIBLE = "disponible"
    RECHAZADO = "rechazado"


class MotivoRechazo(StrEnum):
    VIRUS = "virus"
    CONTENIDO_ACTIVO = "contenido_activo"
    PDF_CIFRADO = "pdf_cifrado"
    PDF_MAL_FORMADO = "pdf_mal_formado"
    NO_COINCIDE = "no_coincide"


class VersionEnLista(BaseModel):
    id: UUID
    estado: EstadoVersion = Field(
        description="Quien lee ve solo las versiones `disponible`; las que están en cuarentena o"
        " fueron rechazadas las ve solo quien las subió."
    )
    motivo_rechazo: MotivoRechazo | None = None
    tipo: TipoArchivo
    tamano: int
    sha256: str = Field(description="Para verificar el archivo descargado (documento 13 §9.3).")
    subida_en: datetime


class DocumentoEnLista(BaseModel):
    id: UUID
    categoria: CategoriaDocumento
    titulo: str
    fecha: date
    cargado_en: datetime
    versiones: list[VersionEnLista] = Field(description="De la más nueva a la más vieja.")


class ListaDocumentos(BaseModel):
    documentos: list[DocumentoEnLista] = Field(
        description="Los vigentes de las categorías que la persona puede ver, del más nuevo al"
        " más viejo por su fecha."
    )
