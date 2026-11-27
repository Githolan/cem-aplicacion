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
- **Nada se borra** (`0089`): un archivo subido por error en un formulario lo quita quien lo subió
  (`POST …/documentos/{id}/quitar`, estado `descartado`), y un documento que dejó de valer lo retira
  quien administra los documentos, con un motivo (`POST …/documentos/{id}/retiro`, estado
  `retirado`). Los dos se niegan si algo guardado cita el documento (`documento_referenciado`). Las
  listas y la descarga muestran solo los vigentes; la exportación lleva también los retirados.
"""

from datetime import date, datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from cem_contracts.contactos import _sin_contacto_libre

LARGO_MAXIMO_TITULO = 200
LARGO_MAXIMO_MOTIVO_RETIRO = 300
TOPE_BYTES = 20 * 1024 * 1024
"""20 MB (decisión del 25-10, §1.3). Un byte más es 413."""


class CategoriaDocumento(StrEnum):
    """Las 8 categorías de §4 y la liquidación del administrador (H5-1, `0113`). Quién ve y quién
    carga cada una: `ACCION_DE_LECTURA` y `ACCIONES_DE_CARGA`."""

    INSTITUCIONAL = "institucional"
    ACTAS_Y_DECISIONES = "actas_y_decisiones"
    SEGUROS = "seguros"
    CONTRATOS_PROVEEDORES = "contratos_proveedores"
    FACTURAS_Y_COMPROBANTES = "facturas_y_comprobantes"
    PRESUPUESTOS = "presupuestos"
    MANTENIMIENTO_Y_CERTIFICADOS = "mantenimiento_y_certificados"
    OTROS = "otros"
    LIQUIDACIONES_DEL_ADMINISTRADOR = "liquidaciones_del_administrador"


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
        CategoriaDocumento.LIQUIDACIONES_DEL_ADMINISTRADOR,
    }
)
"""Las que el Consejo carga con `documentos.cargar_operativos` (inferencia de §4, va al
propietario en §8). La liquidación del administrador la sube el Consejo en Modo Consejo (H5, D2)."""

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


class DemoraAnalisis(StrEnum):
    """Por qué el análisis de una versión todavía no terminó (`0136`)."""

    FIRMAS_VIEJAS = "firmas_viejas"
    """Las firmas del antivirus pasan de `CEM_ANTIVIRUS_FIRMAS_HORAS`: no se aprueba nada."""
    ANTIVIRUS_NO_DISPONIBLE = "antivirus_no_disponible"
    OTRA_FALLA = "otra_falla"


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
    orden_pago_id: UUID | None = Field(
        default=None,
        description="Si es el comprobante de un pago manual cargado desde el pago (`0063`), la"
        " orden que respalda.",
    )
    versiones: list[VersionEnLista] = Field(description="De la más nueva a la más vieja.")
    respalda: bool | None = Field(
        default=None,
        description="Si algo guardado lo cita (una orden, un pago, un proveedor, un contrato o el"
        " acta de una decisión): entonces no se retira (`0089`). Solo para quien tiene la facultad"
        " `documentos.ver` (`documentos.ver_restringidos`), la que pide retirar; nulo para el resto"
        " (tanda 6 de fricción, G-02, `0097`).",
    )


class ListaDocumentos(BaseModel):
    documentos: list[DocumentoEnLista] = Field(
        description="Los vigentes de las categorías que la persona puede ver, del más nuevo al"
        " más viejo por su fecha."
    )


MotivoRetiro = Annotated[
    str, Field(max_length=LARGO_MAXIMO_MOTIVO_RETIRO), AfterValidator(_sin_contacto_libre)
]


class RetiroDocumento(BaseModel):
    """`POST …/documentos/{id}/retiro`: el documento sale de las listas y queda en la exportación,
    con este motivo. No se borra nada."""

    model_config = ConfigDict(extra="forbid")

    motivo: MotivoRetiro = Field(
        description="Por qué se retira (obligatorio): sin teléfonos, correos ni cuentas."
    )


class EstadoDeMiVersion(BaseModel):
    """`GET …/documentos/{id}/versiones/{v}/estado`: cómo terminó el análisis de un archivo que
    subió quien pregunta (tanda 6 de fricción, G-09). Es para quien carga sin ver la lista (el
    administrador externo, documento 08 §4): sin esto, nunca se entera si el antivirus lo rechazó.
    Solo de las versiones propias; de las ajenas, 404 (`0097`)."""

    estado: EstadoVersion
    motivo_rechazo: MotivoRechazo | None = None
    demora: DemoraAnalisis | None = Field(
        default=None,
        description="Mientras sigue en cuarentena: por qué falló el último intento del análisis"
        " (`0136`). Nula si no falló ninguno. La pantalla lo dice: nunca «se sigue esperando» a"
        " secas para algo que está fallando.",
    )
    se_abandono: bool = Field(
        default=False,
        description="En cuarentena y sin más intentos: hay que subir el archivo de nuevo.",
    )
    proximo_intento: datetime | None = Field(
        default=None, description="Cuándo vuelve a probar, después de un intento fallido."
    )


CARGAS_PROPIAS_DIAS = 30
"""FL-DOC-04 (18-11-2026): cuántos días atrás llega «Lo que cargó esta cuenta»."""
CARGAS_PROPIAS_MAXIMO = 10
"""FL-DOC-04: cuántas cargas propias recientes se listan, de la más nueva a la más vieja."""


class CargaPropia(BaseModel):
    """Un archivo que subió quien pregunta, con cómo terminó el análisis."""

    model_config = ConfigDict(frozen=True)

    documento_id: UUID
    version_id: UUID
    titulo: str
    categoria: CategoriaDocumento
    estado: EstadoVersion
    motivo_rechazo: MotivoRechazo | None = None
    subida_en: datetime


class ListaCargasPropias(BaseModel):
    """`GET …/documentos/mis-cargas`: los archivos que subió quien pregunta en los últimos
    `CARGAS_PROPIAS_DIAS` días, a lo sumo `CARGAS_PROPIAS_MAXIMO`, de documentos vigentes
    (FL-DOC-04, `0111`). Es para quien carga sin ver la lista (el administrador externo): al
    volver a Documentos sabe si el antivirus aceptó o rechazó lo que subió. Pide alguna acción de
    carga, como `…/versiones/{v}/estado`."""

    model_config = ConfigDict(frozen=True)

    elementos: list[CargaPropia]
