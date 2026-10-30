"""Órdenes de pago y proveedores (H3; documento 08 §6; migraciones `0045` a `0049`).

Diseño: `doc/diseno/H3_Proveedor_Beneficiario_Cuenta.md` del repositorio CEM, con las decisiones
del propietario del 29-10-2026: aprobar es del Consejo (decisión 2), preparar sigue el paquete
vigente, y no se fija umbral, franjas ni plazo (decisión 1). Hoy toda orden pasa por revisión.

- **Preparación** (`egresos.preparar_orden`): proveedores, órdenes y sus versiones, borradores
  incluidos.
- **Revisión** (`egresos.revisar_orden`, con la facultad `egresos.aprobar_orden`): las órdenes ya
  enviadas, cada versión con el proveedor exacto que fija, para aprobar o rechazar esa versión.
- **Una aprobación es de una versión y de su huella**: el pedido lleva las dos, tal como se
  vieron. Si la orden cambió en el medio, se niega (`huella_distinta` o
  `version_desactualizada`).

Cada campo es igual o más estricto que la base: lo que la base rechazaría con un CHECK es un 422
acá, nunca un 500. Los importes van en centavos, enteros.
"""

from datetime import datetime
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

IMPORTE_MAXIMO_CENTAVOS = 2**63 - 1
"""El tope de `bigint` en la base."""
FORMATO_MONEDA = r"^[A-Z]{3}$"
FORMATO_CUIT = r"^[0-9]{11}$"
"""Once dígitos, sin guiones; el dígito verificador lo comprueba la base (`cuit_valido`)."""
FORMATO_CUENTA = r"^[0-9]{22}$"
VERSION_MAXIMA = 2**31 - 1
"""Las versiones son `integer` en la base: una mayor no encontraría la función (500)."""
SIN_CONTROLES = r"^[^\x00-\x1f\x7f]*$"
"""Ningún carácter de control: el nulo, por ejemplo, lo rechaza el controlador de la base."""
FORMATO_HUELLA = r"^[0-9a-f]{64}$"
"""SHA-256 en hexadecimal, en minúsculas, como lo devuelve la lectura."""
PESOS_BLOQUE_1 = (7, 1, 3, 9, 7, 1, 3)
PESOS_BLOQUE_2 = (3, 9, 7, 1, 3, 9, 7, 1, 3, 9, 7, 1, 3)


def _digito(cifras: str, pesos: tuple[int, ...]) -> int:
    return (10 - sum(int(c) * p for c, p in zip(cifras, pesos, strict=True)) % 10) % 10


def _cuenta_valida(cuenta: str) -> str:
    """CBU o CVU: los mismos controles que `nucleo.cbu_valido` (`0048`), los dos dígitos
    verificadores y ningún primer bloque en ceros."""
    if cuenta.startswith("0000000"):
        raise ValueError("La cuenta no es un CBU o CVU válido.")
    if _digito(cuenta[:7], PESOS_BLOQUE_1) != int(cuenta[7]):
        raise ValueError("La cuenta no es un CBU o CVU válido.")
    if _digito(cuenta[8:21], PESOS_BLOQUE_2) != int(cuenta[21]):
        raise ValueError("La cuenta no es un CBU o CVU válido.")
    return cuenta


def _texto(minimo: int, maximo: int):
    """Se recorta antes de validar: la base guarda `btrim` y lo controla con su largo."""
    return Annotated[
        str,
        StringConstraints(
            strip_whitespace=True, min_length=minimo, max_length=maximo, pattern=SIN_CONTROLES
        ),
    ]


Cuenta = Annotated[
    str,
    StringConstraints(pattern=FORMATO_CUENTA),
    AfterValidator(_cuenta_valida),
    Field(description="CBU o CVU de 22 dígitos. El alias no se admite."),
]
RazonSocial = _texto(1, 200)
Rubro = _texto(1, 100)
Contacto = _texto(1, 200)
Concepto = _texto(1, 300)
Comprobante = _texto(1, 200)
Motivo = _texto(1, 500)
Importe = Annotated[int, Field(gt=0, le=IMPORTE_MAXIMO_CENTAVOS, description="En centavos.")]
Moneda = Annotated[str, Field(pattern=FORMATO_MONEDA, examples=["ARS"])]
Version = Annotated[int, Field(gt=0, le=VERSION_MAXIMA)]


class EstadoProveedor(StrEnum):
    ACTIVO = "activo"
    SUSPENDIDO = "suspendido"
    """No recibe órdenes nuevas ni se emite a su nombre (`proveedor_inactivo`)."""
    BAJA = "baja"


class EstadoOrdenPago(StrEnum):
    """Los estados del diagrama 4 del documento 08 (`0045`)."""

    BORRADOR = "borrador"
    EN_REVISION = "en_revision"
    RECHAZADA = "rechazada"
    APROBADA = "aprobada"
    EN_EJECUCION = "en_ejecucion"
    EN_VERIFICACION = "en_verificacion"
    PAGADA = "pagada"
    FALLIDA = "fallida"
    CONCILIADA = "conciliada"


class CondicionBeneficiario(StrEnum):
    """Calculada al preparar cada versión (`0048`). Qué control pide cada una es la decisión 1:
    hoy toda orden pasa por revisión."""

    NUEVO = "nuevo"
    """El proveedor no tiene ninguna orden aprobada antes en el consorcio."""
    CUENTA_CAMBIADA = "cuenta_cambiada"
    """Tiene órdenes aprobadas antes, pero con otra cuenta."""
    CONOCIDO = "conocido"
    """Tiene órdenes aprobadas antes con esta misma cuenta."""


# --- Proveedores -------------------------------------------------------------------------------


class DatosProveedor(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    razon_social: RazonSocial
    rubro: Rubro | None = None
    contacto: Contacto | None = None
    documento_id: UUID | None = Field(
        default=None, description="Un documento vigente del consorcio que respalda los datos."
    )
    cuenta_destino: Cuenta | None = None


class ProveedorNuevo(DatosProveedor):
    """`POST …/proveedores`. Queda `activo` en su versión 1."""

    cuit: str | None = Field(
        default=None, pattern=FORMATO_CUIT, description="Si se carga, es único en el consorcio."
    )


class ProveedorVersionNueva(DatosProveedor):
    """`POST …/proveedores/{id}/versiones`. Cambiar la cuenta es una versión nueva: las órdenes ya
    preparadas siguen apuntando a la anterior. El CUIT no cambia."""

    version_esperada: Version
    estado: EstadoProveedor


class ProveedorCreado(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    version: Version


class Proveedor(BaseModel):
    """La versión vigente del proveedor."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    cuit: str | None
    version: Version
    razon_social: str
    rubro: str | None
    contacto: str | None
    estado: EstadoProveedor
    documento_id: UUID | None
    cuenta_destino: str | None
    huella: str
    alta_por: UUID
    creado_en: datetime
    actualizado_en: datetime


class ListaProveedores(BaseModel):
    model_config = ConfigDict(frozen=True)

    elementos: list[Proveedor]


class VersionProveedor(BaseModel):
    model_config = ConfigDict(frozen=True)

    version: Version
    razon_social: str
    rubro: str | None
    contacto: str | None
    estado: EstadoProveedor
    documento_id: UUID | None
    cuenta_destino: str | None
    huella: str
    preparada_por: UUID
    creada_en: datetime


class ProveedorConVersiones(BaseModel):
    model_config = ConfigDict(frozen=True)

    proveedor: Proveedor
    versiones: list[VersionProveedor]


# --- Órdenes de pago ---------------------------------------------------------------------------


class DatosOrdenPago(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    importe_centavos: Importe
    moneda: Moneda
    concepto: Concepto
    proveedor_id: UUID
    comprobante: Comprobante | None = None
    usa_fondo_reserva: bool


class OrdenPagoNueva(DatosOrdenPago):
    """`POST …/ordenes-pago`. Queda en borrador, versión 1."""


class OrdenPagoVersionNueva(DatosOrdenPago):
    """`POST …/ordenes-pago/{id}/versiones`. Si la orden estaba aprobada vuelve a revisión, y las
    aprobaciones de la versión anterior quedan como historia sin contar."""

    version_esperada: Version


class EnvioARevision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version_esperada: Version


class Aprobacion(BaseModel):
    """`POST …/ordenes-pago/{id}/aprobaciones`: la versión y la huella que se vieron."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Version
    huella: str = Field(pattern=FORMATO_HUELLA)


class Rechazo(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Version
    motivo: Motivo


class OrdenPagoCambiada(BaseModel):
    """Respuesta de toda escritura sobre una orden: su id y la versión vigente."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    version: Version


class AprobacionRegistrada(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    orden_id: UUID
    version: Version


class OrdenPago(BaseModel):
    """La orden con su versión vigente."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    estado: EstadoOrdenPago
    version_vigente: Version
    preparada_por: UUID
    creada_en: datetime
    actualizada_en: datetime
    motivo_rechazo: str | None
    importe_centavos: int
    moneda: str
    concepto: str
    proveedor_id: UUID
    razon_social: str
    condicion_beneficiario: CondicionBeneficiario
    usa_fondo_reserva: bool
    aprobaciones_requeridas: int = Field(description="Las que pide la versión vigente.")
    aprobaciones: int = Field(description="Las que ya tiene la versión vigente.")


class ListaOrdenesPago(BaseModel):
    model_config = ConfigDict(frozen=True)

    elementos: list[OrdenPago]


class VersionOrdenPago(BaseModel):
    """Cada versión con el proveedor exacto que fija: razón social, CUIT y cuenta de esa versión
    del proveedor, no de la vigente."""

    model_config = ConfigDict(frozen=True)

    numero: Version
    importe_centavos: int
    moneda: str
    concepto: str
    comprobante: str | None
    usa_fondo_reserva: bool
    huella: str
    preparada_por: UUID
    creada_en: datetime
    aprobaciones_requeridas: int
    proveedor_id: UUID
    proveedor_version: Version
    condicion_beneficiario: CondicionBeneficiario
    razon_social: str
    cuit: str | None
    cuenta_destino: str | None
    aprobada_por: list[UUID]


class OrdenPagoConVersiones(BaseModel):
    model_config = ConfigDict(frozen=True)

    orden: OrdenPago
    versiones: list[VersionOrdenPago]
