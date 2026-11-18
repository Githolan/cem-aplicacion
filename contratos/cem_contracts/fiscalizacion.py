"""Panel del Consejo (H4a, corte 1; RF48), en solo lectura.

Diseño: `doc/diseno/H4a_1_Panel_del_Consejo.md` del repositorio CEM.

- **Una acción, una vista** (tabla de verdad, §1.2): `GET …/panel` es `fiscalizacion.ver_panel`,
  el panel completo del Consejo y la administración. El resumen del propietario
  (`fiscalizacion.ver_resumen`) no está en este corte.
- **Señales con su base de cálculo, no conclusiones** (RN33): cada fila dice de dónde sale.
- Todo sale de las órdenes de pago. Sin cuenta, CUIT, contacto, personas ni motivos.

**Solicitud formal de documentación (RF49, CU12, PA36; `0108`).** Diseño:
`doc/diseno/RF49_Solicitud_Formal.md` del repositorio CEM. El Consejo prepara una solicitud, dos
personas distintas la firman y la segunda firma la emite dirigida al único administrador vigente
(A14). El plazo corre desde el envío: si el destinatario tiene cuenta, el envío ocurre al emitir;
si no, lo registra el Consejo con el comprobante del medio externo. Tono informativo (RN33).
"""

from datetime import date, datetime
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from cem_contracts.documentos import EstadoVersion, VersionCargada
from cem_contracts.egresos import EstadoOrdenPago, Version


class OrdenesPorEstado(BaseModel):
    """Órdenes de un estado y una moneda, por el importe de su versión vigente. Sin borradores."""

    model_config = ConfigDict(frozen=True)

    estado: EstadoOrdenPago
    moneda: str
    cantidad: int = Field(ge=1)
    importe_centavos: int = Field(ge=1)


class GastoPagado(BaseModel):
    """Lo pagado en un mes, con un rubro, con o sin el fondo de reserva, en una moneda.

    Importe y fecha son los que usa la liquidación: lo real si el Consejo lo dio por pagado, y la
    fecha de la operación conciliada o el día del consorcio en que se pagó."""

    model_config = ConfigDict(frozen=True)

    periodo: date = Field(
        description="El primer día del mes del pago, en el calendario del consorcio."
    )
    rubro: str | None = Field(
        description="El rubro del proveedor en la versión con que se pagó; null si no tenía."
    )
    fondo_reserva: bool = Field(description="Si la versión pagada usaba el fondo de reserva.")
    moneda: str
    cantidad: int = Field(ge=1)
    importe_centavos: int = Field(ge=1)


class ComprobanteFaltante(BaseModel):
    """Una orden aprobada o más adelante cuya versión vigente no tiene archivo de factura, ni uno
    subido después que haya pasado el antivirus (`0099`)."""

    model_config = ConfigDict(frozen=True)

    orden_id: UUID
    version: Version = Field(
        description="La versión vigente: la que se manda al subir la factura (`0099`)."
    )
    estado: EstadoOrdenPago
    proveedor: str = Field(description="La razón social, como figura en la versión vigente.")
    concepto: str
    importe_centavos: int
    moneda: str
    actualizada_en: datetime
    factura_subida: EstadoVersion | None = Field(
        description="Cómo está el último archivo subido como factura después de aprobada"
        " (`0099`): `en_cuarentena` mientras el antivirus lo revisa, `rechazado` si no lo pasó."
        " Nulo si no se subió ninguno."
    )


class PanelFiscalizacion(BaseModel):
    """`GET …/panel`: las tres lecturas, de una misma foto de la base."""

    model_config = ConfigDict(frozen=True)

    ordenes_por_estado: list[OrdenesPorEstado]
    gasto_pagado: list[GastoPagado] = Field(
        description="Por período, de lo más viejo a lo más nuevo."
    )
    comprobantes_faltantes: list[ComprobanteFaltante] = Field(
        description="De lo último que cambió a lo más viejo."
    )
    solicitudes_abiertas: int = Field(
        ge=0,
        description="Solicitudes formales en borrador, con el envío pendiente, enviadas o"
        " vencidas: las que no se respondieron ni se retiraron (RF49).",
    )


# --- Solicitud formal de documentación (RF49; `0108`) -------------------------------------------

PLAZO_POR_DEFECTO_DIAS = 15
"""Días corridos desde el envío (pregunta P1 del diseño, por defecto)."""
PLAZO_MINIMO_DIAS = 5
PLAZO_MAXIMO_DIAS = 60
"""El rango que se puede elegir (P1). La base tiene el mismo control (`0108`)."""
LARGO_REFERENCIA = 200
LARGO_DOCUMENTACION_PEDIDA = 2000
LARGO_RESPUESTA = 4000
LARGO_MOTIVO_RETIRO = 500
LARGO_DETALLE_ENVIO = 300
UN_RENGLON = r"^[^\x00-\x1f\x7f]*$"
"""Ningún carácter de control."""
CON_RENGLONES = r"^[^\x00-\x09\x0b-\x1f\x7f]*$"
"""Ningún carácter de control salvo el salto de renglón."""


def _texto(maximo: int, patron: str = UN_RENGLON):
    """Se recorta antes de validar: la base guarda `btrim` y lo controla con su largo."""
    return Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=maximo, pattern=patron),
    ]


Referencia = _texto(LARGO_REFERENCIA)
DocumentacionPedida = _texto(LARGO_DOCUMENTACION_PEDIDA, CON_RENGLONES)
TextoRespuesta = _texto(LARGO_RESPUESTA, CON_RENGLONES)
MotivoRetiro = _texto(LARGO_MOTIVO_RETIRO)
DetalleEnvio = _texto(LARGO_DETALLE_ENVIO)
PlazoDias = Annotated[int, Field(ge=PLAZO_MINIMO_DIAS, le=PLAZO_MAXIMO_DIAS)]


class ObjetoSolicitud(StrEnum):
    """Sobre qué se pide documentación o una aclaración."""

    PERIODO = "periodo"
    ORDEN_PAGO = "orden_pago"
    CONTRATO = "contrato"
    MOVIMIENTO = "movimiento"
    OTRO = "otro"


class EstadoSolicitudFormal(StrEnum):
    """Lo calcula la base con el día del consorcio; `vencida` no es una transición guardada."""

    BORRADOR = "borrador"
    ENVIO_PENDIENTE = "envio_pendiente"
    """Emitida a un destinatario sin cuenta: falta registrar el envío. Mientras tanto no vence."""
    ENVIADA = "enviada"
    VENCIDA = "vencida"
    RESPONDIDA = "respondida"
    RETIRADA = "retirada"


ABIERTAS = frozenset({
    EstadoSolicitudFormal.BORRADOR, EstadoSolicitudFormal.ENVIO_PENDIENTE,
    EstadoSolicitudFormal.ENVIADA, EstadoSolicitudFormal.VENCIDA,
})  # fmt: skip
"""Las que cuenta el panel: ni respondidas ni retiradas."""


class MedioEnvio(StrEnum):
    APLICACION = "aplicacion"
    """El destinatario tiene cuenta: el envío ocurre al emitir."""
    CARTA_DOCUMENTO = "carta_documento"
    CORREO_ELECTRONICO = "correo_electronico"
    ENTREGA_EN_MANO = "entrega_en_mano"
    OTRO = "otro"


class MedioRespuesta(StrEnum):
    APLICACION = "aplicacion"
    """La respondió el destinatario en la aplicación."""
    MEDIO_FORMAL = "medio_formal"
    """Llegó por otro medio y la registró el Consejo (destinatario sin cuenta, P2)."""


class AccionSolicitud(StrEnum):
    """Lo que la cuenta que pide puede hacer con la solicitud ahora. Solo para mostrar: cada acto
    lo vuelven a decidir la ruta y la base."""

    FIRMAR = "firmar"
    RETIRAR = "retirar"
    REGISTRAR_ENVIO = "registrar_envio"
    RESPONDER = "responder"
    REGISTRAR_RESPUESTA = "registrar_respuesta"


class SolicitudFormalNueva(BaseModel):
    """`POST …/solicitudes-formales`: preparar no cuenta como firma (A5)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    objeto: ObjetoSolicitud
    referencia: Referencia = Field(
        description="Qué período, orden, contrato o movimiento, en palabras."
    )
    documentacion: DocumentacionPedida = Field(
        description="La documentación o la aclaración que se pide."
    )
    plazo_dias: PlazoDias = Field(
        default=PLAZO_POR_DEFECTO_DIAS, description="Días corridos desde el envío."
    )


class ParaAdjunto(StrEnum):
    """El acto que va a citar el archivo que se sube antes de hacerlo."""

    ENVIO = "envio"
    RESPUESTA = "respuesta"


class EnvioNuevo(BaseModel):
    """`POST …/solicitudes-formales/{s}/envio`: el destinatario no tiene cuenta y el Consejo
    registra el envío por el medio externo, con el comprobante ya subido (`adjunto_id`)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    medio: MedioEnvio = Field(description="Uno externo: `aplicacion` se registra al emitir.")
    detalle: DetalleEnvio | None = Field(
        default=None, description="P. ej. el número de la carta documento."
    )
    fecha_envio: date = Field(description="Entre el día de la emisión y hoy.")
    adjunto_id: UUID = Field(description="El comprobante, subido para el envío.")


class RespuestaNueva(BaseModel):
    """`POST …/solicitudes-formales/{s}/respuesta`: la del destinatario en la aplicación."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    texto: TextoRespuesta
    adjunto_id: UUID | None = Field(
        default=None, description="Un archivo subido para la respuesta, si lo hay."
    )


class RespuestaRecibida(BaseModel):
    """`POST …/solicitudes-formales/{s}/respuesta-recibida`: la que llegó por otro medio de un
    destinatario sin cuenta, que registra el Consejo (P2)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    texto: TextoRespuesta
    recibida_el: date = Field(description="Entre el día del envío y hoy.")
    adjunto_id: UUID | None = Field(
        default=None, description="Un archivo subido para la respuesta, si lo hay."
    )


class AdjuntoSubido(BaseModel):
    """`POST …/solicitudes-formales/{s}/adjuntos`: el archivo, en cuarentena hasta que el
    antivirus lo acepta. El acto lo cita por `adjunto_id`."""

    model_config = ConfigDict(frozen=True)

    adjunto_id: UUID
    documento_id: UUID
    archivo: VersionCargada


class RetiroSolicitud(BaseModel):
    """Solo un borrador se retira, con el motivo (P4)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    motivo: MotivoRetiro


class FirmaSolicitud(BaseModel):
    model_config = ConfigDict(frozen=True)

    nombre: str | None
    firmada_en: datetime
    propia: bool = Field(description="La puso la cuenta que pide.")


class EnvioSolicitud(BaseModel):
    model_config = ConfigDict(frozen=True)

    medio: MedioEnvio
    detalle: str | None
    fecha_envio: date
    vence_el: date = Field(description="La fecha del envío más el plazo, en días corridos.")
    registrado_por_nombre: str | None
    registrado_en: datetime
    documento_id: UUID | None = Field(description="El comprobante; nulo en `aplicacion`.")
    version_documento_id: UUID | None
    estado_archivo: EstadoVersion | None = Field(
        description="Cómo terminó el análisis del comprobante (FL-SOL-04): en cuarentena todavía"
        " no se descarga, y rechazado no se descarga nunca. Nulo sin comprobante."
    )
    descargable: bool = Field(
        description="Si quien lee lo puede descargar: disponible y con `documentos.ver`. Solo"
        " para mostrar: la descarga vuelve a decidir."
    )


class RespuestaSolicitud(BaseModel):
    model_config = ConfigDict(frozen=True)

    texto: str
    medio: MedioRespuesta
    respondida_el: date = Field(description="El día en que llegó la respuesta.")
    fuera_de_plazo: bool = Field(description="Llegó después del vencimiento (P5).")
    registrada_por_nombre: str | None
    registrada_en: datetime
    documento_id: UUID | None
    version_documento_id: UUID | None
    estado_archivo: EstadoVersion | None = Field(
        description="Cómo terminó el análisis del adjunto (FL-SOL-04). Nulo sin adjunto."
    )
    descargable: bool = Field(
        description="Si quien lee lo puede descargar: disponible y con `documentos.ver`."
    )


class RetiroRegistrado(BaseModel):
    model_config = ConfigDict(frozen=True)

    motivo: str
    retirada_por_nombre: str | None
    retirada_en: datetime


class SolicitudFormal(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    numero: int = Field(ge=1, description="Correlativo por consorcio.")
    objeto: ObjetoSolicitud
    referencia: str
    documentacion: str
    plazo_dias: int
    estado: EstadoSolicitudFormal
    preparada_por_nombre: str | None
    preparada_en: datetime
    firmas: list[FirmaSolicitud]
    destinatario_nombre: str | None = Field(description="Desde la emisión.")
    destinatario_con_cuenta: bool | None = Field(description="Desde la emisión.")
    emitida_en: datetime | None
    texto_emitido: str | None = Field(
        description="El texto de la solicitud (plantilla PG08), para enviarlo por el medio formal."
    )
    huella_texto: str | None = Field(description="SHA-256 del texto emitido, en hexadecimal.")
    envio: EnvioSolicitud | None
    respuesta: RespuestaSolicitud | None
    retiro: RetiroRegistrado | None
    acciones: list[AccionSolicitud]
    falta_administrador_vigente: bool = Field(
        description="Si quien lee firmaría este borrador pero la firma emite y el consorcio no"
        " tiene un administrador vigente (A14): `firmar` no se ofrece hasta que lo tenga."
    )


class ListaSolicitudesFormales(BaseModel):
    """`GET …/solicitudes-formales`: el Consejo y la administración (`fiscalizacion.ver_panel`)
    ven todas; el destinatario, las dirigidas a su cuenta."""

    model_config = ConfigDict(frozen=True)

    elementos: list[SolicitudFormal] = Field(description="De la más nueva a la más vieja.")
    puede_preparar: bool
    hay_administrador_vigente: bool = Field(
        description="Si hoy hay un mandato de administración vigente: sin él, una solicitud se"
        " prepara y se firma una vez, pero la segunda firma no la emite (A14; FL-SOL-05)."
    )
    plazo_por_defecto_dias: int
    plazo_minimo_dias: int
    plazo_maximo_dias: int
