"""Órdenes de pago y proveedores (H3; documento 08 §6; migraciones `0045` a `0049`).

Diseño: `doc/diseno/H3_Proveedor_Beneficiario_Cuenta.md` del repositorio CEM, con las decisiones
del propietario: aprobar es del Consejo (decisión 2, 29-10-2026), preparar sigue el paquete
vigente, y la decisión 1A (30-10-2026): tres franjas con el umbral que fija cada consorcio por
acta, contratos y abonos que respaldan lo rutinario, y una aprobación que vence a los 7 días.

- **Preparación** (`egresos.preparar_orden`): proveedores, órdenes y sus versiones, borradores
  incluidos.
- **Revisión** (`egresos.revisar_orden`, con la facultad `egresos.aprobar_orden`): las órdenes ya
  enviadas, cada versión con el proveedor exacto que fija, para aprobar o rechazar esa versión.
- **La franja** de cada versión (`Franja`) dice si pide aprobación. Lo rutinario y lo que no
  supera el umbral no la piden; el fondo de reserva, el beneficiario nuevo, la cuenta cambiada,
  lo que supera el umbral y todo lo no rutinario sin umbral, sí.
- **Una aprobación es de una versión y de su huella**: el pedido lleva las dos, tal como se
  vieron. Si la orden cambió en el medio, se niega (`huella_distinta` o
  `version_desactualizada`).

Cada campo es igual o más estricto que la base: lo que la base rechazaría con un CHECK es un 422
acá, nunca un 500. Los importes van en centavos, enteros.
"""

from datetime import date, datetime, timedelta
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
VIGENCIA_APROBACION = timedelta(days=7)
"""Decisión 1A (30-10-2026): una orden aprobada se emite dentro de los 7 días corridos desde que
llegó a Aprobada. Es `VIGENCIA_APROBACION_DIAS` de `0050` (una prueba los compara)."""
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
Alcance = _texto(1, 300)
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


class Franja(StrEnum):
    """La franja de una versión de la orden (decisión 1A, `0050`). Gana la primera que
    corresponde, de la más estricta a la más permisiva."""

    FONDO_RESERVA = "fondo_reserva"
    """Usa el fondo de reserva: aprueba el Consejo."""
    BENEFICIARIO_NUEVO = "beneficiario_nuevo"
    """El proveedor no tiene una orden aprobada antes: aprobación, aunque el importe sea chico."""
    CUENTA_CAMBIADA = "cuenta_cambiada"
    """Tuvo órdenes aprobadas, pero a otra cuenta: aprobación."""
    RUTINARIA = "rutinaria"
    """Un contrato o abono confirmado y vigente la respalda: sin aprobación previa."""
    SIN_UMBRAL = "sin_umbral"
    """El consorcio no tiene umbral en esa moneda: aprobación."""
    SOBRE_UMBRAL = "sobre_umbral"
    """Supera el umbral: aprobación."""
    BAJO_UMBRAL = "bajo_umbral"
    """No supera el umbral: sin aprobación previa."""

    @property
    def requiere_aprobacion(self) -> bool:
        return self not in (Franja.RUTINARIA, Franja.BAJO_UMBRAL)


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
    contrato_id: UUID | None = Field(
        default=None,
        description="El contrato o abono confirmado que respalda la orden: solo así es rutinaria.",
    )


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
    aprobaciones_requeridas: int = Field(
        description="Las que pide la versión vigente si su franja pide aprobación."
    )
    aprobaciones: int = Field(description="Las que ya tiene la versión vigente.")
    franja: Franja = Field(description="La de la versión vigente.")
    alerta_fraccionamiento: bool = Field(
        description="Sumada a las demás del mismo proveedor en el mes, supera el umbral."
    )
    aprobada_en: datetime | None = Field(description="Cuándo llegó a Aprobada.")
    aprobacion_vence_en: datetime | None = Field(
        description="Hasta cuándo se emite; después se renueva. Solo en una orden aprobada."
    )


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
    franja: Franja
    alerta_fraccionamiento: bool
    umbral_importe_centavos: int | None = Field(
        description="El umbral vigente al preparar la versión, aunque no haya decidido."
    )
    umbral_moneda: str | None
    contrato_id: UUID | None = Field(description="El contrato que la hace rutinaria.")


class OrdenPagoConVersiones(BaseModel):
    model_config = ConfigDict(frozen=True)

    orden: OrdenPago
    versiones: list[VersionOrdenPago]


class Emision(BaseModel):
    """`POST …/ordenes-pago/{id}/emitir`: la versión aprobada que se emite (Aprobada →
    EnEjecucion). No mueve dinero."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Version
    huella: str = Field(pattern=FORMATO_HUELLA, description="La de la versión que se emite.")


class Renovacion(BaseModel):
    """`POST …/ordenes-pago/{id}/renovar`: una orden aprobada hace más de 7 días vuelve a
    revisión con una versión nueva, evaluada con las reglas de hoy."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version_esperada: Version


# --- Contratos y abonos ------------------------------------------------------------------------


class TipoContrato(StrEnum):
    CONTRATO = "contrato"
    ABONO = "abono"


class EstadoContrato(StrEnum):
    PENDIENTE_CONFIRMACION = "pendiente_confirmacion"
    """Registrado; no rige hasta que lo confirme un consejero que no lo registró."""
    NO_INICIADO = "no_iniciado"
    VIGENTE = "vigente"
    VENCIDO = "vencido"
    DADO_DE_BAJA = "dado_de_baja"
    DOCUMENTO_NO_VIGENTE = "documento_no_vigente"
    """El documento de respaldo se borró: el contrato no respalda nada (`contrato_vigente`)."""


class ContratoNuevo(BaseModel):
    """`POST …/contratos`: el registro que respalda lo rutinario. El documento es uno vigente de
    la categoría `contratos_proveedores`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    proveedor_id: UUID
    tipo: TipoContrato
    alcance: Alcance = Field(description="Qué cubre: el servicio, el rubro, la frecuencia.")
    importe_maximo_centavos: Importe = Field(description="El tope de cada orden que respalda.")
    moneda: Moneda
    vigente_desde: date
    vigente_hasta: date
    documento_id: UUID
    decision_id: UUID | None = Field(
        default=None, description="La decisión del consorcio que lo autoriza, si corresponde."
    )


class Confirmacion(BaseModel):
    """`POST …/contratos/{id}/confirmacion`: la huella del contrato que se vio."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    huella: str = Field(pattern=FORMATO_HUELLA)


class ContratoCambiado(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID


class Contrato(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    proveedor_id: UUID
    razon_social: str = Field(description="La de la versión vigente del proveedor.")
    tipo: TipoContrato
    alcance: str
    importe_maximo_centavos: int
    moneda: str
    vigente_desde: date
    vigente_hasta: date
    documento_id: UUID
    decision_id: UUID | None
    huella: str
    registrado_por: UUID
    creado_en: datetime
    confirmado_por: UUID | None
    confirmado_en: datetime | None
    baja_en: datetime | None
    estado: EstadoContrato


class ListaContratos(BaseModel):
    model_config = ConfigDict(frozen=True)

    elementos: list[Contrato]
