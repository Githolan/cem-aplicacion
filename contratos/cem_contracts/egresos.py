"""Órdenes de pago y proveedores (H3; documento 08 §6; migraciones `0045` a `0049`).

Diseño: `doc/diseno/H3_Proveedor_Beneficiario_Cuenta.md` del repositorio CEM, con las decisiones
del propietario: aprobar es del Consejo (decisión 2, 29-10-2026), preparar sigue el paquete
vigente, y la decisión 1A (30-10-2026): tres franjas con el umbral que fija cada consorcio por
acta, contratos y abonos que respaldan lo rutinario, y una aprobación que vence a los 7 días.

- **Preparación** (`egresos.preparar_orden`): proveedores, órdenes y sus versiones, borradores
  incluidos.
- **Revisión** (`egresos.revisar_orden`, con la facultad `egresos.aprobar_orden`): las órdenes ya
  enviadas, cada versión con el proveedor exacto que fija, para aprobar o rechazar esa versión.
- **La cuenta destino completa** solo sale en la revisión. Preparación recibe los últimos
  4 dígitos (`cuenta_ultimos_digitos`): la cuenta llega ya recortada desde el backend, no
  se oculta en la pantalla (decisión del propietario, 30-10-2026).
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
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from cem_contracts.documentos import EstadoVersion, Titulo

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
llegó a Aprobada. Son 168 horas exactas desde el instante en que reunió las aprobaciones que pide,
no hasta el final del séptimo día calendario (confirmado por el propietario el 30-10-2026). Es
`VIGENCIA_APROBACION_DIAS` de `0050` (una prueba los compara)."""
DIGITOS_VISIBLES_CUENTA = 4
"""Lo que ve de la cuenta destino quien no revisa: los últimos dígitos."""
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


def ultimos_digitos(cuenta: str | None) -> str | None:
    """La cuenta como la recibe quien no revisa."""
    return None if cuenta is None else cuenta[-DIGITOS_VISIBLES_CUENTA:]


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
Telefono = _texto(1, 50)
Domicilio = _texto(1, 200)
CorreoProveedor = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=3, max_length=254, pattern=r"^[^@\s]+@[^@\s]+$"
    ),
]
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
    telefono: Telefono | None = None
    whatsapp: Telefono | None = None
    correo: CorreoProveedor | None = None
    domicilio: Domicilio | None = None
    atiende_emergencias: bool = False
    documento_id: UUID | None = Field(
        default=None, description="Un documento vigente del consorcio que respalda los datos."
    )
    cuenta_destino: Cuenta | None = None


class ProveedorNuevo(DatosProveedor):
    """`POST …/proveedores`. Queda `activo` en su versión 1."""

    cuit: str = Field(pattern=FORMATO_CUIT, description="Obligatorio y único en el consorcio.")


class ProveedorVersionNueva(DatosProveedor):
    """`POST …/proveedores/{id}/versiones`. Cambiar la cuenta es una versión nueva: las órdenes ya
    preparadas siguen apuntando a la anterior. El CUIT no cambia.

    Quien prepara nunca recibe la cuenta completa, así que no puede reenviarla:
    `conservar_cuenta` deja la de la versión esperada, y entonces `cuenta_destino` no viene.
    Sin `conservar_cuenta`, `cuenta_destino` es la cuenta nueva o ninguna."""

    version_esperada: Version
    estado: EstadoProveedor
    conservar_cuenta: bool = Field(
        description="Obligatorio: así un cliente que no manda la cuenta nunca la borra sin querer."
    )

    @model_validator(mode="after")
    def _conservar_sin_cuenta(self) -> "ProveedorVersionNueva":
        if self.conservar_cuenta and self.cuenta_destino is not None:
            raise ValueError("Con conservar_cuenta no se manda cuenta_destino.")
        return self


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
    telefono: str | None
    whatsapp: str | None
    correo: str | None
    domicilio: str | None
    atiende_emergencias: bool
    estado: EstadoProveedor
    documento_id: UUID | None
    cuenta_ultimos_digitos: str | None = Field(
        description="Los últimos 4 dígitos de la cuenta destino: la completa solo la ve quien"
        " revisa (decisión del 30-10-2026)."
    )
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
    telefono: str | None
    whatsapp: str | None
    correo: str | None
    domicilio: str | None
    atiende_emergencias: bool
    estado: EstadoProveedor
    documento_id: UUID | None
    cuenta_ultimos_digitos: str | None = Field(
        description="Los últimos 4 dígitos de la cuenta destino: la completa solo la ve quien"
        " revisa (decisión del 30-10-2026)."
    )
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
    aprobada_por_vos: bool = Field(
        description="Si quien lee, con esta cuenta u otra del mismo ser humano, aprobó la versión"
        " vigente: quien aprobó no la emite ni la paga. Solo en una orden en curso (en revisión,"
        " aprobada, en ejecución o en verificación); en las demás es falso. Solo para mostrar: la"
        " ruta y la base vuelven a decidir."
    )


class ListaOrdenesPago(BaseModel):
    model_config = ConfigDict(frozen=True)

    elementos: list[OrdenPago]


class VersionOrdenPago(BaseModel):
    """Cada versión con el proveedor exacto que fija: razón social, CUIT y cuenta de esa versión
    del proveedor, no de la vigente. En preparación, la cuenta va recortada."""

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
    cuenta_ultimos_digitos: str | None = Field(
        description="Los últimos 4 dígitos de la cuenta destino: la completa solo la ve quien"
        " revisa (decisión del 30-10-2026)."
    )
    aprobada_por: list[UUID]
    franja: Franja
    alerta_fraccionamiento: bool
    umbral_importe_centavos: int | None = Field(
        description="El umbral vigente al preparar la versión, aunque no haya decidido."
    )
    umbral_moneda: str | None
    contrato_id: UUID | None = Field(description="El contrato que la hace rutinaria.")


class OrdenPagoConVersiones(BaseModel):
    """Preparación: sin la cuenta completa."""

    model_config = ConfigDict(frozen=True)

    orden: OrdenPago
    versiones: list[VersionOrdenPago]


class VersionOrdenPagoRevision(VersionOrdenPago):
    """Revisión: el Consejo verifica la cuenta completa antes de aprobar."""

    cuenta_destino: str | None = Field(
        description="La cuenta completa. Nula si la versión no tiene cuenta o si quien lee preparó"
        " la orden o alguna de sus versiones (no la aprueba): entonces vale"
        " `cuenta_ultimos_digitos`."
    )


class OrdenPagoConVersionesRevision(BaseModel):
    model_config = ConfigDict(frozen=True)

    orden: OrdenPago
    versiones: list[VersionOrdenPagoRevision]
    puede_aprobar: bool = Field(
        description="Si quien lee puede aprobar la versión vigente: la orden en revisión, sin haber"
        " preparado ninguna versión ni haberla aprobado ya, y sin haber firmado la cuenta que paga."
        " Lo de la cuenta solo se sabe de quien prepara: a quien ya no prepara se lo niega la base"
        " al aprobar. Solo para mostrar."
    )
    puede_rechazar: bool = Field(
        description="Si quien lee puede rechazar la versión vigente: la orden en revisión y sin"
        " haber preparado ninguna versión. No mira la cuenta del proveedor: esa separación es solo"
        " de aprobar. Solo para mostrar."
    )


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
    PENDIENTE_RECONFIRMACION = "pendiente_reconfirmacion"
    """Estaba confirmado, pero la cuenta del proveedor cambió después: no respalda nada hasta que
    un consejero lo confirme de nuevo sobre la versión con la cuenta nueva (`0053`, decisión del
    propietario del 31-10-2026)."""


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
    """`POST …/contratos/{id}/confirmacion`: la huella del contrato y la versión del proveedor que
    se vieron. Si el proveedor tiene otra versión vigente, 409 `proveedor_cambiado`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    huella: str = Field(pattern=FORMATO_HUELLA)
    proveedor_version: Version = Field(
        description="La versión vigente del proveedor que se vio (`Contrato.proveedor_version`)."
    )


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
    proveedor_version: int = Field(description="La versión vigente del proveedor.")
    cuenta_ultimos_digitos: str | None = Field(
        description="Los últimos dígitos de la cuenta de esa versión."
    )


class ListaContratos(BaseModel):
    model_config = ConfigDict(frozen=True)

    elementos: list[Contrato]


class ContratoRevision(Contrato):
    """Revisión del Consejo: quien confirma (o reconfirma, `0053`) ve la cuenta completa de la
    versión vigente del proveedor, que es lo que confirma."""

    cuenta_destino: str | None = Field(
        description="La cuenta completa de esa versión. Nula si quien lee registró el contrato"
        " (también con otra cuenta suya: por identidad humana); entonces vale"
        " `cuenta_ultimos_digitos`."
    )


class ListaContratosRevision(BaseModel):
    model_config = ConfigDict(frozen=True)

    elementos: list[ContratoRevision]


# --- Pago manual asistido (piloto sin riel, `0055`) -------------------------------------------

FORMATO_NUMERO_OPERACION = r"^[A-Za-z0-9 ./-]{4,60}$"
"""Como lo escribe la persona; la base guarda solo letras y dígitos en mayúsculas (de 4 a 40) y
compara así: «0001-23» y «000123» son el mismo número."""


class ModalidadPago(StrEnum):
    """Cómo se paga una ejecución, fijada al emitir (`0055`): nunca las dos."""

    RIEL = "riel"
    MANUAL = "manual"
    """La paga una persona autorizada por el canal que el consorcio ya tiene (home banking)."""


class ResultadoConciliacion(StrEnum):
    CONFORME = "conforme"
    """El comprobante coincide con la orden: la orden queda Pagada."""
    OBSERVADO = "observado"
    """No coincide: el registro queda sin efecto (y en la historia) y se registra otro."""


class PedidoCuentaPagoManual(BaseModel):
    """`POST …/ordenes-pago/{id}/pago-manual/cuenta`: el acto de ejecución manual, sobre la versión
    y la huella que se vieron. Es el único lugar donde sale la cuenta completa para transferir."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Version
    huella: str = Field(pattern=FORMATO_HUELLA)


class CuentaParaPagoManual(BaseModel):
    """Lo que necesita quien transfiere, de la ejecución de esa versión: la cuenta completa."""

    model_config = ConfigDict(frozen=True)

    ejecucion_id: UUID
    version: Version
    huella: str
    proveedor_id: UUID
    proveedor_version: Version
    razon_social: str
    cuit: str | None
    cuenta_destino: str = Field(description="CBU o CVU completo, el de la versión aprobada.")
    importe_centavos: int
    moneda: str
    concepto: str
    comprobante: str | None
    aprobada_en: datetime
    aprobacion_vence_en: datetime = Field(
        description="El pago tiene que hacerse antes: después no se registra con esa fecha."
    )


class ComprobantePagoNuevo(BaseModel):
    """`POST …/ordenes-pago/{id}/pago-manual/comprobante`: el documento del comprobante, cargado
    desde el pago (`0063`). Lo crea quien vio la cuenta en el acto de ejecución de esa orden. Nace
    en «Facturas y comprobantes» y restringido: lo ven quien sube cada versión y el Consejo que no
    preparó la orden. El archivo se sube después a `…/documentos/{id}/versiones`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    titulo: Titulo
    fecha: date = Field(description="Fecha del comprobante (no la de la carga).")


class RegistroPagoManual(BaseModel):
    """`POST …/ordenes-pago/{id}/pago-manual`: la evidencia de una transferencia ya hecha. No da la
    orden por pagada: queda pendiente de conciliación (En verificación)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Version
    huella: str = Field(pattern=FORMATO_HUELLA)
    numero_operacion: str = Field(
        pattern=FORMATO_NUMERO_OPERACION, description="El número de operación del comprobante."
    )
    fecha_operacion: date
    importe_centavos: Importe
    moneda: Moneda
    cuenta_destino: Cuenta | None = Field(
        default=None,
        description="Como figura en el comprobante. Nula al corregir un registro observado, porque"
        " sigue la que ese registro ya acreditó, o si la lectura del archivo dio que coincide con"
        " la orden. En cualquier otro registro nuevo la base la niega"
        " (`no_coincide_con_la_orden`).",
    )
    documento_id: UUID = Field(
        description="El comprobante: cargado desde el pago de esta orden"
        " (`…/pago-manual/comprobante`), con un archivo que pasó el antivirus."
    )


class TomaPagoManual(BaseModel):
    """`POST …/ordenes-pago/{id}/pago-manual/tomar`: quien vio la cuenta desapareció y pasó el plazo.
    Suelta a esa persona; abrir la cuenta sigue siendo otro acto."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Version
    huella: str = Field(pattern=FORMATO_HUELLA)
    no_se_transfirio: Literal[True] = Field(
        description="Quien toma el pago confirmó antes que la plata no salió."
    )


class TomaRegistrada(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    orden_id: UUID


class TomaVigente(BaseModel):
    """La última vez que alguien tomó el pago de esta ejecución."""

    model_config = ConfigDict(frozen=True)

    tomado_por_nombre: str
    tomada_en: datetime


class TipoDiferencia(StrEnum):
    """Qué fue distinto de la orden. Lo deduce la base: otra moneda cuenta como otro importe."""

    IMPORTE = "importe"
    CUENTA = "cuenta"
    AMBOS = "ambos"


class ResolucionDiferencia(StrEnum):
    PAGADA_POR_LO_REAL = "pagada_por_lo_real"
    RECLAMO = "reclamo"


class RegistroDiferenciaPago(BaseModel):
    """`POST …/ordenes-pago/{id}/pago-manual/diferencia`: lo que salió del banco, cuando no es lo
    que se aprobó. La orden **no** queda pagada: lo resuelve el Consejo. Si todo coincide con la
    orden, `sin_diferencia`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Version
    huella: str = Field(pattern=FORMATO_HUELLA)
    numero_operacion: str = Field(
        pattern=FORMATO_NUMERO_OPERACION, description="El número de operación del comprobante."
    )
    fecha_operacion: date
    importe_real: Decimal = Field(
        gt=0, max_digits=18, decimal_places=2, description="Lo que salió del banco, en pesos."
    )
    moneda: Moneda
    cuenta_real: Cuenta = Field(description="Como figura en el comprobante.")
    documento_id: UUID = Field(
        description="El comprobante: cargado desde el pago de esta orden, con un archivo que pasó"
        " el antivirus."
    )
    explicacion: _texto(1, 300) = Field(
        description="Qué pasó, sin el número de una cuenta (`motivo_con_cuenta`)."
    )


class DiferenciaRegistrada(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    orden_id: UUID


class CierreReclamo(BaseModel):
    """`POST …/pago-manual/diferencia/{d}/cierre`: la plata reclamada no vuelve. Otro ser humano del
    Consejo cierra el reclamo: la orden queda Fallida y la diferencia, en la historia."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    motivo: _texto(1, 300)


class ResolucionDiferenciaPago(BaseModel):
    """`POST …/pago-manual/diferencia/{d}/resolucion`: otro ser humano del Consejo. Dar por pagado
    lo real vale solo para otro importe en la moneda de la orden (`solo_reclamo`)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    resolucion: ResolucionDiferencia


class DiferenciaPagoManual(BaseModel):
    """Lo que se pagó de verdad, sin la cuenta completa."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    tipo: TipoDiferencia
    importe_real: Decimal
    moneda: str
    cuenta_ultimos_digitos: str
    importe_orden_centavos: int
    moneda_orden: str
    numero_operacion: str
    fecha_operacion: date
    documento_id: UUID
    version_documento_id: UUID = Field(description="El archivo exacto que quedó como evidencia.")
    explicacion: str
    fuera_de_termino: bool
    registrada_por: UUID
    registrada_por_nombre: str
    registrada_en: datetime
    resolucion: ResolucionDiferencia | None
    resuelta_por: UUID | None
    resuelta_en: datetime | None
    recuperada_en: datetime | None
    cerrada_en: datetime | None = Field(description="Si el reclamo se cerró sin recuperar la plata.")
    motivo_cierre: str | None
    falta_centavos: int | None = Field(
        description="Cuánto faltó pagar, si fue de menos en la moneda de la orden."
    )
    sobra_centavos: int | None = Field(
        description="Cuánto se pagó de más, si fue de más en la moneda de la orden."
    )
    se_puede_dar_por_pagada: bool = Field(
        description="Si admite `pagada_por_lo_real`: la cuenta y la moneda son las de la orden."
    )


class PagoManualRegistrado(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    orden_id: UUID


class ConciliacionPagoManual(BaseModel):
    """`POST …/ordenes-pago/{id}/pago-manual/{pago}/conciliacion`: otro ser humano del Consejo."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    resultado: ResultadoConciliacion
    motivo: _texto(1, 300) | None = Field(
        default=None,
        description="Obligatorio si es `observado`. Con `conforme`, va solo si el pago se hizo"
        " fuera de término (`0067`): la base lo exige y lo niega en un pago en término.",
    )

    @model_validator(mode="after")
    def _motivo_si_observado(self):
        if self.resultado == ResultadoConciliacion.OBSERVADO and self.motivo is None:
            raise ValueError("Una conciliación observada lleva el motivo.")
        return self


class DeclaracionPagoNoRealizado(BaseModel):
    """`POST …/ordenes-pago/{id}/pago-manual/no-realizado`: no se pagó. Lo confirma otro ser
    humano del Consejo; recién entonces la orden queda Fallida."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Version
    motivo: _texto(1, 200)


class PagoManualCambiado(BaseModel):
    """Respuesta de la conciliación y de la confirmación: la orden y su estado nuevo."""

    model_config = ConfigDict(frozen=True)

    orden_id: UUID
    estado: EstadoOrdenPago


class DeclaracionRegistrada(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    orden_id: UUID


class PagoManual(BaseModel):
    """Un registro de pago manual, sin la cuenta. `corrige_a`: el registro observado que
    reemplaza."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    corrige_a: UUID | None
    numero_operacion: str
    fecha_operacion: date
    importe_centavos: int
    moneda: str
    documento_id: UUID
    version_documento_id: UUID = Field(description="El archivo exacto que quedó como evidencia.")
    registrado_por: UUID
    registrado_por_nombre: str
    registrado_en: datetime
    conciliacion: ResultadoConciliacion | None
    motivo_conciliacion: str | None
    conciliado_por: UUID | None
    conciliado_en: datetime | None
    fuera_de_termino: bool = Field(
        description="Transferido después de que venció la aprobación (`0067`): el Consejo lo"
        " concilia con un motivo escrito, también para darlo por conforme."
    )


class PagoNoRealizado(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    motivo: str
    declarado_por: UUID
    declarado_en: datetime
    confirmado_por: UUID | None
    confirmado_en: datetime | None
    confirmable: bool = Field(
        description="Si el Consejo puede confirmarla hoy: nadie vio la cuenta ni registró un pago"
        " después, y cada persona que vio la cuenta declaró después de su última apertura."
    )
    puede_confirmar: bool = Field(
        description="Si quien lee puede confirmarla: es `confirmable` y no preparó la orden, ni"
        " registró un pago ni declaró en ella. Solo para mostrar: la ruta y la base vuelven a"
        " decidir."
    )


class EjecucionManual(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    version: Version
    creada_en: datetime
    resultado: str | None = Field(description="`pagada` o `fallida` cuando ya se resolvió.")
    referencia: str | None = Field(description="El número de operación conciliado.")
    pagos: list[PagoManual]
    declaraciones: list[PagoNoRealizado]


class OrdenConPagoManual(BaseModel):
    model_config = ConfigDict(frozen=True)

    orden: OrdenPago
    ejecucion: EjecucionManual
    puede_conciliar: bool = Field(
        description="Si quien lee puede conciliar el pago pendiente: la ejecución abierta, sin"
        " haber preparado la orden ni registrado un pago de ella. Quién vio la cuenta sin registrar"
        " no se sabe acá: se lo niega la base al conciliar. Solo para mostrar."
    )


class ListaPagosManuales(BaseModel):
    """Las órdenes con una ejecución manual: por pagar, por conciliar y ya resueltas."""

    model_config = ConfigDict(frozen=True)

    elementos: list[OrdenConPagoManual]


class PagoPorAclarar(BaseModel):
    """Una orden que espera que digas si transferiste: viste la cuenta en el acto de ejecución y
    ya no tenés `egresos.ejecutar_pago` en ese consorcio (`0067`, P3 179). Sin la cuenta."""

    model_config = ConfigDict(frozen=True)

    consorcio_id: UUID
    consorcio: str
    orden_id: UUID
    version: Version
    concepto: str
    importe_centavos: int
    moneda: str
    cuenta_vista_en: datetime = Field(description="La última vez que viste la cuenta.")
    huella: str = Field(
        pattern=FORMATO_HUELLA, description="La de la versión: el registro la pide (`0068`)."
    )
    comprobante_id: UUID | None = Field(
        description="El último comprobante de esta orden que cargaste, si hay (`0068`, P3 184)."
    )
    comprobante_estado: str | None = Field(
        description="El estado de su último archivo: `en_cuarentena`, `disponible` o `rechazado`."
    )


class ListaPagosPorAclarar(BaseModel):
    """`GET /pagos-por-aclarar`: en cualquier consorcio. Si no transferiste, se declara con
    `POST /consorcios/{id}/pagos-por-aclarar/{orden}/no-realizado` (`DeclaracionPagoNoRealizado`).
    Si transferiste, se carga el comprobante (`…/{orden}/comprobante` y su archivo en
    `…/{orden}/comprobante/{documento}/versiones`) y se registra en `…/{orden}/pago`
    (`RegistroPagoManual`, `0068`). Lo concilia o confirma otro miembro del Consejo."""

    model_config = ConfigDict(frozen=True)

    elementos: list[PagoPorAclarar]


# --- Circuito de pagos: bandeja, ficha y consulta (`0072`) --------------------------------------
# Diseño: `doc/diseno/Rediseno_Circuito_Pagos_2026-11-06.md` del repositorio CEM, §2, §3 y §11. El
# frontend no deduce estados: el servidor dice en qué anda la orden y qué le toca a quien mira. La
# cuenta completa no sale nunca por acá: solo en el acto de ejecución.

LIMITE_CONSULTA_PAGOS_POR_DEFECTO = 25
LIMITE_CONSULTA_PAGOS_MAXIMO = 100
LARGO_MAXIMO_BUSQUEDA_PAGOS = 100
MAXIMO_SALTAR_CONSULTA_PAGOS = VERSION_MAXIMA
"""`saltar` es `integer` en la base."""


class PasoPago(StrEnum):
    """El próximo paso de una orden para quien mira (§2). Sale solo si la base va a aceptar la
    acción; los `espera_*` son de quien la ve y no le toca."""

    PAGAR = "pagar"
    TOMAR_PAGO = "tomar_pago"
    """Otra persona vio la cuenta y pasó el plazo sin que dijera nada: se puede tomar el pago."""
    SUBIR_COMPROBANTE = "subir_comprobante"
    CORREGIR = "corregir"
    ENVIAR = "enviar"
    RENOVAR = "renovar"
    MODIFICAR = "modificar"
    """Aprobada, y el proveedor cambió: no se puede pagar hasta modificarla (`proveedor_cambiado`)."""
    VOLVER_A_PAGAR = "volver_a_pagar"
    RECHAZADA = "rechazada"
    APROBAR = "aprobar"
    CONCILIAR = "conciliar"
    CONFIRMAR_NO_PAGO = "confirmar_no_pago"
    DECLARAR_NO_PAGO = "declarar_no_pago"
    """Nadie vio la cuenta y ya no se puede abrir (la aprobación venció, por ejemplo): la orden en
    ejecución no se renueva, y la salida es declarar que no se pagó."""
    RESOLVER_DIFERENCIA = "resolver_diferencia"
    """Se pagó otra cosa, o hay un reclamo abierto: lo resuelve el Consejo (§7)."""
    PREPARAR_FALTANTE = "preparar_faltante"
    """Se dio por pagado menos de lo aprobado: quien prepara decide si hace otra orden por lo que
    falta. No se crea sola."""
    ESPERA_APROBAR = "espera_aprobar"
    ESPERA_COMPROBANTE = "espera_comprobante"
    """Otra persona ya vio la cuenta: se espera su comprobante y no se ofrece pagar, para que la
    misma orden no se transfiera dos veces."""
    ESPERA_DECLARAR = "espera_declarar"
    ESPERA_CONCILIAR = "espera_conciliar"
    ESPERA_CONFIRMAR = "espera_confirmar"
    ESPERA_RESOLVER = "espera_resolver"
    ESPERA_CORREGIR = "espera_corregir"


class GrupoPago(StrEnum):
    """Los grupos de la bandeja, en el orden en que se muestran (§2)."""

    PARA_PAGAR = "para_pagar"
    PARA_CORREGIR = "para_corregir"
    PARA_PREPARAR = "para_preparar"
    PARA_APROBAR = "para_aprobar"
    PARA_CONCILIAR = "para_conciliar"
    PARA_CONFIRMAR = "para_confirmar"
    PARA_RESOLVER = "para_resolver"
    ESPERANDO = "esperando"


GRUPO_DE_PASO = {
    PasoPago.PAGAR: GrupoPago.PARA_PAGAR,
    PasoPago.TOMAR_PAGO: GrupoPago.PARA_PAGAR,
    PasoPago.SUBIR_COMPROBANTE: GrupoPago.PARA_PAGAR,
    PasoPago.DECLARAR_NO_PAGO: GrupoPago.PARA_PAGAR,
    PasoPago.CORREGIR: GrupoPago.PARA_CORREGIR,
    PasoPago.ENVIAR: GrupoPago.PARA_PREPARAR,
    PasoPago.RENOVAR: GrupoPago.PARA_PREPARAR,
    PasoPago.MODIFICAR: GrupoPago.PARA_PREPARAR,
    PasoPago.VOLVER_A_PAGAR: GrupoPago.PARA_PREPARAR,
    PasoPago.RECHAZADA: GrupoPago.PARA_PREPARAR,
    PasoPago.PREPARAR_FALTANTE: GrupoPago.PARA_PREPARAR,
    PasoPago.APROBAR: GrupoPago.PARA_APROBAR,
    PasoPago.CONCILIAR: GrupoPago.PARA_CONCILIAR,
    PasoPago.CONFIRMAR_NO_PAGO: GrupoPago.PARA_CONFIRMAR,
    PasoPago.RESOLVER_DIFERENCIA: GrupoPago.PARA_RESOLVER,
    PasoPago.ESPERA_APROBAR: GrupoPago.ESPERANDO,
    PasoPago.ESPERA_COMPROBANTE: GrupoPago.ESPERANDO,
    PasoPago.ESPERA_DECLARAR: GrupoPago.ESPERANDO,
    PasoPago.ESPERA_CONCILIAR: GrupoPago.ESPERANDO,
    PasoPago.ESPERA_CONFIRMAR: GrupoPago.ESPERANDO,
    PasoPago.ESPERA_RESOLVER: GrupoPago.ESPERANDO,
    PasoPago.ESPERA_CORREGIR: GrupoPago.ESPERANDO,
}


class LeTocaA(StrEnum):
    """A quién le toca el próximo paso, por su lugar en el circuito: no nombra personas."""

    VOS = "vos"
    CONSEJO = "consejo"
    QUIEN_REGISTRO = "quien_registro"
    QUIEN_VIO_LA_CUENTA = "quien_vio_la_cuenta"
    QUIEN_EJECUTA = "quien_ejecuta"


class SituacionPago(StrEnum):
    """Lo que se le dice a la persona (§3), derivado en el servidor: puede diferir del estado de
    la base. Los estados que §3 no nombra conservan su nombre."""

    BORRADOR = "borrador"
    EN_REVISION = "en_revision"
    RECHAZADA = "rechazada"
    PARA_PAGAR = "para_pagar"
    PAGANDO = "pagando"
    NO_PAGO_DECLARADO = "no_pago_declarado"
    FALTA_CONCILIAR = "falta_conciliar"
    OBSERVADO = "observado"
    CON_DIFERENCIA = "con_diferencia"
    RECLAMO = "reclamo"
    PAGADA = "pagada"
    NO_SE_PAGO = "no_se_pago"


class FiltroConsultaPagos(StrEnum):
    TODOS = "todos"
    PAGADOS = "pagados"
    NO_SE_PAGARON = "no_se_pagaron"
    RECHAZADOS = "rechazados"
    EN_CURSO = "en_curso"


class ElementoBandejaPagos(BaseModel):
    """Una orden en la bandeja de quien pide, sin la cuenta."""

    model_config = ConfigDict(frozen=True)

    orden_id: UUID
    proveedor: str = Field(description="La razón social de la versión vigente de la orden.")
    concepto: str
    importe_centavos: int
    moneda: str
    paso: PasoPago
    grupo: GrupoPago
    le_toca_a: LeTocaA


class BandejaPagos(BaseModel):
    """`GET …/pagos/bandeja`: lo que quien pide tiene que hacer ahora, por grupo y de lo más viejo
    a lo más nuevo. Lo pagado no está; lo rechazado, solo `CEM_PAGOS_DIAS_BANDEJA` días."""

    model_config = ConfigDict(frozen=True)

    elementos: list[ElementoBandejaPagos]


class ComprobanteActual(BaseModel):
    """El archivo que corresponde mostrar: el del registro vigente o, sin él, el último que quien
    mira subió para esta ejecución y pasó el antivirus. Se abre con `…/contenido`."""

    model_config = ConfigDict(frozen=True)

    documento_id: UUID
    version_documento_id: UUID


class ComprobanteSubido(BaseModel):
    """Lo último que quien mira subió para esta ejecución, mientras no es el actual: lo revisa el
    antivirus o lo rechazó."""

    model_config = ConfigDict(frozen=True)

    documento_id: UUID
    version_documento_id: UUID
    estado: EstadoVersion


class EstadoLectura(StrEnum):
    """En qué quedó la lectura con IA del comprobante."""

    LEYENDO = "leyendo"
    LEIDA = "leida"
    NO_LEIDA = "no_leida"
    """No se pudo leer: se carga a mano."""


class CampoComprobante(StrEnum):
    """Un dato que se compara entre lo leído y la orden o lo registrado."""

    IMPORTE = "importe"
    MONEDA = "moneda"
    CUENTA = "cuenta"
    NUMERO_OPERACION = "numero_operacion"
    FECHA = "fecha"


class LecturaComprobante(BaseModel):
    """Lo que la IA leyó de `comprobante_actual`. Propone, no decide: llena campos y avisa. La
    comparación la hace el servidor. La cuenta leída no se guarda ni viaja: solo sus últimos dígitos
    y si coincide con la de la orden."""

    model_config = ConfigDict(frozen=True)

    version_documento_id: UUID
    estado: EstadoLectura
    importe_centavos: int | None
    moneda: str | None
    cuenta_ultimos_digitos: str | None
    cuenta_coincide: bool | None = Field(
        description="Si la cuenta leída es, dígito a dígito, la de la orden. Nula si no se leyó."
    )
    titular_destino: str | None
    numero_operacion: str | None
    fecha: date | None
    distinto_de_la_orden: list[CampoComprobante] = Field(
        description="Lo que se leyó y no es lo de la orden. Lo que no se leyó no cuenta."
    )
    titular_distinto: bool = Field(
        description="Si el titular leído no es el de la orden, una vez normalizados. Solo avisa:"
        " una razón social se escribe de varias maneras."
    )
    distinto_de_lo_registrado: list[CampoComprobante] = Field(
        description="Lo que se leyó y no es lo que se registró con este archivo. El Consejo lo ve"
        " al conciliar. Vacía si todavía no hay registro. Vacía no quiere decir que coincida: lo"
        " que no se leyó no se compara."
    )
    coincide_con_lo_registrado: bool = Field(
        description="Si importe, cuenta y fecha se leyeron, se compararon con lo registrado con"
        " este archivo y nada difiere. Con una lectura parcial o vacía es falso."
    )


class ObservacionVigente(BaseModel):
    """Por qué el Consejo observó el último registro."""

    model_config = ConfigDict(frozen=True)

    pago_id: UUID
    motivo: str
    observado_por: UUID
    observado_en: datetime


class FichaPago(BaseModel):
    """`GET …/pagos/{orden}`: una sola ficha para todos los estados, sin la cuenta completa. Los
    `puede_*` dicen lo que la base va a aceptar hoy de quien pide, sin contar el gesto (se pide al
    actuar); solo sirven para mostrar: la ruta y la base vuelven a decidir."""

    model_config = ConfigDict(frozen=True)

    orden: OrdenPago
    versiones: list[VersionOrdenPago]
    situacion: SituacionPago
    paso: PasoPago | None = Field(description="Nulo si a quien mira no le toca ni espera nada.")
    le_toca_a: LeTocaA | None
    puede_enviar: bool
    puede_aprobar: bool
    puede_rechazar: bool
    puede_renovar: bool
    puede_pagar: bool
    puede_reabrir_cuenta: bool = Field(
        description="Si puede abrir otra vez una cuenta ya vista. Con una declaración vigente es"
        " «Retomar el pago», y abrirla deja sin efecto lo declarado."
    )
    puede_tomar: bool = Field(
        description="Si puede tomar el pago de quien vio la cuenta: pasó el plazo y no dijo nada."
    )
    tomar_desde: datetime | None = Field(
        description="Desde cuándo se puede tomar el pago, si espera a otra persona."
    )
    puede_modificar: bool = Field(description="Si puede guardar una versión nueva de la orden.")
    puede_subir_comprobante: bool
    puede_registrar: bool
    puede_declarar: bool
    puede_corregir: bool
    puede_conciliar: bool
    puede_confirmar: bool
    puede_resolver_diferencia: bool = Field(
        description="Si puede resolver la diferencia sin resolver."
    )
    puede_recuperar: bool = Field(
        description="Si puede decir que se recuperó la plata reclamada o cerrar el reclamo sin"
        " recuperarla."
    )
    puede_preparar_faltante: bool = Field(
        description="Si le corresponde preparar una orden por lo que faltó pagar."
    )
    ejecucion: EjecucionManual | None = Field(
        description="La ejecución manual de la versión vigente, si tiene."
    )
    comprobante_actual: ComprobanteActual | None = Field(
        description="Nulo si no hay o si quien mira no puede ver ese archivo."
    )
    comprobante_subido: ComprobanteSubido | None = Field(
        description="Nulo si no subió nada, si lo último ya es el actual o si hay un registro"
        " vigente."
    )
    lectura_comprobante: LecturaComprobante | None = Field(
        description="La de `comprobante_actual`. Nula si la lectura no corrió para ese archivo: con"
        " la lectura apagada, siempre."
    )
    observacion_vigente: ObservacionVigente | None
    declaracion_vigente: PagoNoRealizado | None = Field(
        description="La última declaración de «no se pagó» que nadie superó ni confirmó."
    )
    diferencia_vigente: DiferenciaPagoManual | None = Field(
        description="Lo que se pagó de verdad en la ejecución de la versión vigente, resuelto o no."
    )
    toma_vigente: TomaVigente | None = Field(
        description="Quién tomó el pago de la ejecución de la versión vigente, si alguien lo tomó."
    )


class ElementoConsultaPagos(BaseModel):
    """Una orden en la consulta, sin la cuenta. Abre la misma ficha."""

    model_config = ConfigDict(frozen=True)

    orden_id: UUID
    proveedor: str
    concepto: str
    importe_centavos: int
    moneda: str
    estado: EstadoOrdenPago
    situacion: SituacionPago
    importe_pagado_centavos: int | None = Field(
        description="Lo que el Consejo dio por pagado, si no es el importe de la orden."
    )
    numero_operacion: str | None = Field(
        description="El de la diferencia o, sin ella, el del último registro de la versión vigente,"
        " si tiene."
    )
    fecha_operacion: date | None
    actualizada_en: datetime


class ConsultaPagos(BaseModel):
    """`GET …/pagos/consulta`: de lo último que cambió a lo más viejo."""

    model_config = ConfigDict(frozen=True)

    elementos: list[ElementoConsultaPagos]
    siguiente: int | None = Field(
        description="El `saltar` de la página siguiente; sin él, no hay más."
    )
