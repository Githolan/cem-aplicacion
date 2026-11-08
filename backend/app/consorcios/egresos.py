"""Órdenes de pago, proveedores y contratos (H3; documento 08 §6; migraciones `0045` a `0055`).

Diseño: `doc/diseno/H3_Proveedor_Beneficiario_Cuenta.md` del repositorio CEM, «Rutas» y
«Decisión 1A», con las decisiones del propietario: aprobar es del Consejo (decisión 2), preparar
sigue el paquete vigente, y la franja de cada versión dice si pide aprobación (decisión 1A,
30-10-2026). Emitir deja la orden en ejecución; no hay ruta que mueva dinero.

- **Preparación** (`egresos.preparar_orden`): leer, dar de alta y versionar proveedores; registrar
  y dar de baja contratos; leer, preparar, modificar, enviar a revisión y renovar órdenes.
- **Revisión** (`egresos.revisar_orden`, con la facultad `egresos.aprobar_orden`): leer las
  órdenes ya enviadas, con el proveedor exacto de cada versión, y los contratos.
- **Aprobar, rechazar y confirmar contratos** (`egresos.aprobar_orden`): la ruta verifica primero
  la revisión, lee quién preparó la orden (o registró el contrato) y recién entonces evalúa la
  separación con esa persona.
- **Emitir** (`egresos.ejecutar_pago`): lee la orden como quien prepara y evalúa la separación con
  quienes aprobaron la versión vigente (A5).
- **Pago manual** (`0055`, piloto sin riel): el acto de ejecución da la cuenta completa a quien va a
  transferir (`egresos.ejecutar_pago`, con la misma separación que emitir) y registra la evidencia;
  otro miembro del Consejo concilia (`egresos.conciliar_pago`, con `egresos.aprobar_orden`): nunca
  quien registró el pago ni quien preparó la orden. «No se pagó» lo declara quien ejecuta y lo
  confirma el Consejo, con la misma separación.

Toda acción es económica (R10): sesión de menos de 12 horas, segundo factor de menos de 10
minutos e identidad humana verificada. Las funciones de la base vuelven a exigir todo eso con la
sesión de la petición. Si la base niega lo que el evaluador concedió, es una discrepancia (500),
nunca una lista vacía ni un 403. Toda escritura lleva `Idempotency-Key`.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated
from uuid import UUID

import anyio
from cem_contracts.documentos import DocumentoId, EstadoVersion, VersionCargada
from cem_contracts.egresos import (
    LARGO_MAXIMO_BUSQUEDA_PAGOS,
    LIMITE_CONSULTA_PAGOS_MAXIMO,
    LIMITE_CONSULTA_PAGOS_POR_DEFECTO,
    MAXIMO_SALTAR_CONSULTA_PAGOS,
    SIN_CONTROLES,
    VIGENCIA_APROBACION,
    Aprobacion,
    AprobacionRegistrada,
    BandejaPagos,
    CampoComprobante,
    CargaCuitProveedor,
    CierreReclamo,
    ComprobanteActual,
    ComprobantePagoNuevo,
    ComprobanteSubido,
    ConciliacionPagoManual,
    Confirmacion,
    ConsultaPagos,
    Contrato,
    ContratoCambiado,
    ContratoNuevo,
    ContratoRevision,
    CuentaParaPagoManual,
    DatosOrdenPago,
    DatosProveedor,
    DeclaracionPagoNoRealizado,
    DeclaracionRegistrada,
    DiferenciaPagoManual,
    DiferenciaRegistrada,
    DocumentoParaCarga,
    EjecucionManual,
    ElementoConsultaPagos,
    Emision,
    EnvioARevision,
    EstadoLectura,
    EstadoLecturaCarga,
    EstadoOrdenPago,
    EstadoProveedor,
    FichaPago,
    FiltroConsultaPagos,
    LecturaCarga,
    LecturaComprobante,
    ListaContratos,
    ListaContratosRevision,
    ListaOrdenesPago,
    ListaPagosManuales,
    ListaPagosPorAclarar,
    ListaProveedores,
    ObservacionVigente,
    OrdenConPagoManual,
    OrdenPago,
    OrdenPagoCambiada,
    OrdenPagoConVersiones,
    OrdenPagoConVersionesRevision,
    OrdenPagoNueva,
    OrdenPagoVersionNueva,
    PagoManual,
    PagoManualCambiado,
    PagoManualRegistrado,
    PagoNoRealizado,
    PagoPorAclarar,
    PasoPago,
    PedidoCuentaPagoManual,
    Proveedor,
    ProveedorConVersiones,
    ProveedorCreado,
    ProveedorNuevo,
    ProveedorSugerido,
    ProveedorVersionNueva,
    Rechazo,
    RegistroDiferenciaPago,
    RegistroPagoManual,
    Renovacion,
    ResolucionDiferenciaPago,
    TipoCarga,
    TipoDiferencia,
    TomaPagoManual,
    TomaRegistrada,
    TomaVigente,
    VersionOrdenPago,
    VersionOrdenPagoRevision,
    VersionProveedor,
    ultimos_digitos,
)
from cem_contracts.errores import CodigoError
from fastapi import APIRouter, Depends, Query, Request, Response
from psycopg.errors import InsufficientPrivilege
from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError

from app.autorizacion.evaluador import Entidad, alcanza
from app.autorizacion.requiere import NO_ENCONTRADO, RENOVAR, Permiso, requiere
from app.consorcios import circuito_pagos, lectura_comprobantes
from app.consorcios.documentos import (
    RESPUESTAS_SUBIDA,
    RESPUESTAS_VERSION,
    SUBIDA_OPENAPI,
    subir_archivo,
    version_visible,
)
from app.consorcios.padron import Discrepancia
from app.contexto import Contexto, IdentidadDep, MotorDep
from app.db import transaccion
from app.errores import ErrorApi, sin_cache
from app.plataforma.idempotencia import (
    ClaveIdempotenciaDep,
    IdempotenciaDep,
    responder_una_vez,
)

router = APIRouter(prefix="/api/v1", tags=["egresos"], dependencies=[Depends(sin_cache)])

PREPARAR = "egresos.preparar_orden"
REVISAR = "egresos.revisar_orden"
APROBAR = "egresos.aprobar_orden"
EJECUTAR = "egresos.ejecutar_pago"
CONCILIAR = "egresos.conciliar_pago"
RESPALDADAS = {
    EstadoOrdenPago.APROBADA, EstadoOrdenPago.EN_EJECUCION, EstadoOrdenPago.EN_VERIFICACION,
    EstadoOrdenPago.PAGADA, EstadoOrdenPago.FALLIDA, EstadoOrdenPago.CONCILIADA,
}  # fmt: skip
"""Las órdenes que ya pasaron una aprobación completa y, después, su emisión (también la que
falló): el reintento de la emisión con la misma clave devuelve la respuesta guardada."""
ESPERAN_PAGO = {EstadoOrdenPago.EN_EJECUCION, EstadoOrdenPago.EN_VERIFICACION}
"""Las órdenes con su ejecución manual abierta (`nucleo.ejecucion_manual_abierta`)."""
EN_CURSO = ESPERAN_PAGO | {EstadoOrdenPago.EN_REVISION, EstadoOrdenPago.APROBADA}
"""Las órdenes en las que haber aprobado todavía separa: quien aprobó no repite, ni emite, ni paga.
Son pocas y no crecen con la historia del consorcio."""
LEE_COMO = {PREPARAR: PREPARAR, REVISAR: APROBAR}
"""La facultad con que cada acción lee en la base (`exigir_lectura_egresos`, `0049`)."""

NO_ADMITE = "La orden no admite eso en su estado."
ERRORES = {
    "no_encontrado": (404, CodigoError.NO_ENCONTRADO, NO_ENCONTRADO),
    "version_desactualizada": (
        409, CodigoError.VERSION_DESACTUALIZADA,
        "Mientras tanto hubo otro cambio: volvé a abrirla para ver la versión vigente.",
    ),
    "sin_cambios": (409, CodigoError.SIN_CAMBIOS, "No hay ningún cambio para guardar."),
    "estado_invalido": (409, CodigoError.ORDEN_NO_ADMITE, NO_ADMITE),
    "transicion_invalida": (409, CodigoError.ORDEN_NO_ADMITE, NO_ADMITE),
    "orden_inmutable": (409, CodigoError.ORDEN_NO_ADMITE, NO_ADMITE),
    "huella_distinta": (
        409, CodigoError.HUELLA_DISTINTA,
        "La orden cambió mientras la revisabas: volvé a abrirla antes de aprobar.",
    ),
    "aprobacion_repetida": (409, CodigoError.APROBACION_REPETIDA, "Ya aprobaste esta versión."),
    "no_es_quien_preparo": (
        403, CodigoError.NO_ES_QUIEN_PREPARO,
        "Quien preparó la orden no la aprueba ni la rechaza.",
    ),
    "no_es_quien_cargo_la_cuenta": (
        403, CodigoError.NO_ES_QUIEN_CARGO_LA_CUENTA,
        "Quien cargó la cuenta del proveedor no aprueba un pago a esa cuenta.",
    ),
    "identidad_no_verificada": (
        403, CodigoError.IDENTIDAD_NO_VERIFICADA,
        "Falta verificar una identidad en el consorcio. Soporte puede ayudarte.",
    ),
    "proveedor_inexistente": (
        422, CodigoError.PROVEEDOR_INEXISTENTE, "Ese proveedor no existe en este consorcio.",
    ),
    "proveedor_inactivo": (
        409, CodigoError.PROVEEDOR_INACTIVO,
        "El proveedor está suspendido o dado de baja: no recibe órdenes nuevas.",
    ),
    "cuit_invalido": (422, CodigoError.CUIT_INVALIDO, "El CUIT no es válido."),
    "sin_lectura": (
        422, CodigoError.PETICION_INVALIDA, "Ese comprobante no tiene una lectura para discutir.",
    ),
    "cuit_ya_cargado": (
        409, CodigoError.CUIT_YA_CARGADO, "Ese proveedor ya tiene CUIT, y un CUIT no cambia.",
    ),
    "cuit_repetido": (
        409, CodigoError.CUIT_REPETIDO, "Ya hay un proveedor con ese CUIT en este consorcio.",
    ),
    "documento_invalido": (
        422, CodigoError.DOCUMENTO_INVALIDO,
        "El documento no existe en este consorcio o no está vigente.",
    ),
    "aprobacion_vencida": (
        409, CodigoError.APROBACION_VENCIDA,
        "La aprobación tiene más de 7 días: renová la orden para que vuelva a revisión.",
    ),
    "aprobacion_vigente": (
        409, CodigoError.APROBACION_VIGENTE, "La aprobación todavía rige: no hay que renovarla.",
    ),
    "aprobaciones_insuficientes": (
        409, CodigoError.APROBACIONES_INSUFICIENTES,
        "La versión no tiene las aprobaciones que pide.",
    ),
    "contrato_no_vigente": (
        409, CodigoError.CONTRATO_NO_VIGENTE,
        "El contrato que respaldaba la orden ya no rige: renovala para evaluarla de nuevo.",
    ),
    "contrato_no_aplica": (
        422, CodigoError.CONTRATO_NO_APLICA,
        "El contrato elegido no respalda esta orden: revisá el proveedor, la moneda, el importe"
        " máximo y que esté confirmado y vigente.",
    ),
    "contrato_ya_confirmado": (
        409, CodigoError.CONTRATO_YA_CONFIRMADO, "El contrato ya está confirmado.",
    ),
    "contrato_dado_de_baja": (
        409, CodigoError.CONTRATO_DADO_DE_BAJA, "El contrato está dado de baja.",
    ),
    "proveedor_cambiado": (
        409, CodigoError.PROVEEDOR_CAMBIADO,
        "El proveedor cambió después de preparar la orden: modificala para que vuelva a revisión.",
    ),
    "proveedor_sin_cuenta": (
        409, CodigoError.PROVEEDOR_SIN_CUENTA, "El proveedor no tiene cuenta destino.",
    ),
    "no_es_quien_aprobo": (
        403, CodigoError.NO_ES_QUIEN_APROBO, "Quien aprobó la orden no la emite.",
    ),
    "no_es_quien_registro": (
        403, CodigoError.NO_ES_QUIEN_REGISTRO, "Quien registró el contrato no lo confirma.",
    ),
    "fecha_invalida": (
        422, CodigoError.FECHA_INVALIDA,
        "La vigencia no es válida: termina antes de empezar o antes de hoy.",
    ),
    "decision_invalida": (
        422, CodigoError.DECISION_INVALIDA, "La decisión no existe en este consorcio.",
    ),
    "modalidad_riel": (
        409, CodigoError.MODALIDAD_RIEL, "Esta orden se paga por el riel: no se registra a mano.",
    ),
    "resultado_repetido": (409, CodigoError.ORDEN_NO_ADMITE, NO_ADMITE),
    "pago_ya_registrado": (
        409, CodigoError.PAGO_YA_REGISTRADO,
        "El pago de esta orden ya está registrado: falta que el Consejo lo concilie.",
    ),
    "pago_en_curso": (
        409, CodigoError.PAGO_EN_CURSO,
        "Otra persona ya vio la cuenta de este pago: esperá su comprobante, para no pagar dos"
        " veces.",
    ),
    "toma_antes_de_tiempo": (
        409, CodigoError.TOMA_ANTES_DE_TIEMPO,
        "Todavía no pasó el plazo para tomar el pago: quien vio la cuenta puede estar pagando.",
    ),
    "numero_operacion_invalido": (
        422, CodigoError.NUMERO_OPERACION_INVALIDO,
        "El número de operación no es válido: copialo tal como figura en el comprobante.",
    ),
    "no_coincide_con_la_orden": (
        422, CodigoError.NO_COINCIDE_CON_LA_ORDEN,
        "El importe, la moneda o la cuenta no son los de la orden aprobada: revisá el comprobante"
        " o usá «Pagué otra cosa».",
    ),
    "fecha_operacion_invalida": (
        422, CodigoError.FECHA_OPERACION_INVALIDA,
        "La fecha no puede ser anterior a la emisión ni futura.",
    ),
    "comprobante_invalido": (
        422, CodigoError.COMPROBANTE_INVALIDO,
        "El comprobante tiene que cargarse desde el pago de esta orden, después de ver la cuenta"
        " para pagar, seguir en «Facturas y comprobantes» y su archivo tiene que haber pasado el"
        " antivirus.",
    ),
    "comprobante_de_otra_orden": (
        409, CodigoError.COMPROBANTE_DE_OTRA_ORDEN, "Ese comprobante ya respalda otro pago.",
    ),
    "operacion_de_otra_orden": (
        409, CodigoError.OPERACION_DE_OTRA_ORDEN,
        "Ese número de operación ya respalda otra orden: una transferencia paga una sola orden.",
    ),
    "conciliacion_invalida": (
        422, CodigoError.PETICION_INVALIDA,
        "Un pago en término se da por conforme sin motivo; uno fuera de término, con el motivo"
        " por el que se acepta.",
    ),
    "conciliacion_repetida": (
        409, CodigoError.CONCILIACION_REPETIDA, "Ese pago ya está conciliado.",
    ),
    "motivo_con_cuenta": (
        422, CodigoError.MOTIVO_CON_CUENTA,
        "El motivo no puede llevar el número de una cuenta: describí el problema sin copiarla."
        " Si no es una cuenta, separá los números con una coma.",
    ),
    "sin_diferencia": (
        422, CodigoError.SIN_DIFERENCIA,
        "Lo que escribiste es lo mismo que la orden: registrá el pago, no una diferencia.",
    ),
    "diferencia_ya_resuelta": (
        409, CodigoError.DIFERENCIA_YA_RESUELTA, "El Consejo ya resolvió esa diferencia.",
    ),
    "solo_reclamo": (
        409, CodigoError.SOLO_RECLAMO,
        "Un pago a otra cuenta o en otra moneda no se da por pagado: solo se puede reclamar.",
    ),
    "no_es_quien_ejecuto": (
        403, CodigoError.NO_ES_QUIEN_EJECUTO,
        "Carga el comprobante y registra el pago (o declara que no se hizo, si alguien vio la"
        " cuenta) quien vio la cuenta en el acto de ejecución. Quien vio la cuenta o registró un"
        " pago no lo concilia ni confirma que no se pagó, y quien declaró no confirma su propia"
        " declaración.",
    ),
    "requiere_segundo_factor_reciente": (403, *RENOVAR["requiere_segundo_factor_reciente"]),
    "requiere_sesion_reciente": (403, *RENOVAR["requiere_sesion_reciente"]),
    "sesion_invalida": (403, *RENOVAR["requiere_sesion_reciente"]),
}  # fmt: skip
"""Motivos de las funciones de `0045` a `0055`. Los que el contrato ya impide (razón social,
cuenta) y los que fija esta ruta (origen, modo) no están: si llegan, son un defecto (500). Los de
R10 pueden llegar si la sesión venció entre el evaluador y la base."""

ERRORES_CONFIRMACION = {
    "proveedor_cambiado": (
        409, CodigoError.PROVEEDOR_CAMBIADO,
        "El proveedor cambió desde que viste el contrato: recargá y confirmá la versión nueva.",
    ),
}  # fmt: skip
"""Al confirmar un contrato, `proveedor_cambiado` es que se vio otra versión del proveedor
(`0053`), no una orden preparada."""

RESPUESTAS_LECTURA = {
    403: {"description": "Sin la facultad, sin el modo, o falta renovar la sesión (R10)."}
}
DESCRIPCION_422_ORDEN = (
    "El proveedor no existe (`proveedor_inexistente`), o `factura_version_id` no es un archivo"
    " `disponible` de un documento vigente del consorcio, para carga como factura o de «Facturas y"
    " comprobantes», y que no sea el comprobante de un pago (`documento_invalido`)."
)
RESPUESTAS_ESCRITURA = RESPUESTAS_LECTURA | {
    404: {"description": "No existe en este consorcio."},
    409: {"description": "Otra versión, sin cambios, o el estado no lo admite."},
}


def _ejecutar(conn: Connection, permiso: Permiso, sql: str, parametros: dict, errores=None):
    """Llama a una función de la base con la persona y la sesión de la petición, y traduce sus
    motivos a errores de la API (`errores` cambia la traducción de algunos, para esta ruta)."""
    errores = ERRORES | (errores or {})
    ctx = permiso.ctx
    try:
        return conn.execute(
            text(sql), parametros | {"actor": ctx.persona_id, "sesion": ctx.sesion_id}
        )
    except DBAPIError as e:
        if isinstance(e.orig, InsufficientPrivilege):
            raise Discrepancia(f"la base niega {permiso.accion} y el evaluador lo concede") from e
        motivo = e.orig.diag.message_hint
        if motivo in errores:
            raise ErrorApi(*errores[motivo]) from e
        raise


@contextmanager
def _lectura(permiso: Permiso, motor) -> Iterator[Connection]:
    ctx = permiso.ctx
    with transaccion(motor, persona_id=ctx.persona_id, consorcio_id=ctx.consorcio_id) as c:
        yield c


def _una_foto(motor):
    """Bandeja y ficha cruzan varias lecturas: con READ COMMITTED, un acto confirmado en el medio
    daría una respuesta que mezcla dos momentos."""
    return motor.execution_options(isolation_level="REPEATABLE READ") if motor else motor


def _evaluaciones(c: Connection, permiso: Permiso, orden: UUID | None) -> dict:
    """La franja de cada versión (`0050`), por `(orden, número)`. Se lee **después** de las
    órdenes o las versiones: las versiones solo se agregan, así que toda versión ya leída está."""
    sql = "select * from nucleo.orden_pago_evaluaciones_lectura(:o, :f, :actor, 'api', :sesion)"
    filas = _ejecutar(c, permiso, sql, {"o": orden, "f": LEE_COMO[permiso.accion]})
    return {(f["orden_id"], f["numero"]): f for f in filas.mappings()}


VERSIONES = "select * from nucleo.orden_pago_versiones_lectura(:o, :f, :actor, 'api', :sesion)"


def _aprobadas_por_vos(c: Connection, permiso: Permiso, filas: list) -> set[UUID]:
    """Las órdenes en curso cuya versión vigente aprobó el ser humano que lee. Quién aprobó se lee
    orden por orden (`cem_app` no tiene otra lectura): por eso solo en las que están en curso y ya
    tienen alguna aprobación, y no en toda la historia. Solo para mostrar."""
    en_curso = [
        f for f in filas if EstadoOrdenPago(f["estado"]) in EN_CURSO and f["aprobaciones"] > 0
    ]
    if not en_curso:
        return set()
    cuentas = permiso.cuentas_humanas(c, historicas=True)
    aprobadas = set()
    for f in en_curso:
        versiones = _ejecutar(
            c, permiso, VERSIONES, {"o": f["id"], "f": LEE_COMO[permiso.accion]}
        ).mappings().all()  # fmt: skip
        vigente = next(v for v in versiones if v["numero"] == f["version_vigente"])
        if not cuentas.isdisjoint(vigente["aprobada_por"]):
            aprobadas.add(f["id"])
    return aprobadas


def _ordenes(c: Connection, permiso: Permiso, orden: UUID | None) -> list[OrdenPago]:
    sql = "select * from nucleo.ordenes_pago_lectura(:o, :f, :actor, 'api', :sesion)"
    filas = list(_ejecutar(c, permiso, sql, {"o": orden, "f": LEE_COMO[permiso.accion]}).mappings())
    evaluaciones = _evaluaciones(c, permiso, orden)
    aprobadas = _aprobadas_por_vos(c, permiso, filas)
    ordenes = []
    for f in filas:
        ev = evaluaciones[(f["id"], f["version_vigente"])]
        aprobada_en = ev["aprobada_en"]
        aprobada = f["estado"] == "aprobada" and aprobada_en is not None
        ordenes.append(OrdenPago(
            **f, franja=ev["franja"], alerta_fraccionamiento=ev["alerta_fraccionamiento"],
            aprobada_en=aprobada_en,
            aprobacion_vence_en=aprobada_en + VIGENCIA_APROBACION if aprobada else None,
            aprobada_por_vos=f["id"] in aprobadas,
        ))  # fmt: skip
    return ordenes


def _recortada(fila) -> dict:
    """La fila sin la cuenta completa: quien prepara recibe solo los últimos dígitos."""
    datos = dict(fila)
    datos["cuenta_ultimos_digitos"] = ultimos_digitos(datos.pop("cuenta_destino"))
    return datos


PREPARO = "select nucleo.lee_quien_preparo_la_orden(:o, :actor)"
REGISTRO = "select nucleo.lee_quien_registro_el_contrato(:k, :actor)"
"""Si quien lee es quien preparó la orden o registró el contrato, por la identidad humana guardada
en el acto y no por los vínculos de hoy (P3 115, `0054`). Fallan cerradas: sin identidad vigente
de quien lee, verdadero."""


def _cargo_la_cuenta(c: Connection, aprobar: Permiso, orden: OrdenPago, filas: list) -> bool:
    """Si el ser humano que lee firmó la cuenta que paga la versión vigente: la lógica de
    `nucleo.exigir_no_cargo_la_cuenta`, para mostrar. La historia del proveedor la lee solo quien
    prepara: a quien la firmó y ya no prepara se lo niega la base al aprobar (403, regla 2)."""
    if not alcanza(PREPARAR, aprobar.ctx.autorizacion):
        return False
    vigente = next(f for f in filas if f["numero"] == orden.version_vigente)
    historia = _ejecutar(
        c, aprobar, "select * from nucleo.proveedor_versiones_lectura(:p, :actor, 'api', :sesion)",
        {"p": vigente["proveedor_id"]},
    ).mappings().all()  # fmt: skip
    fija = next(v for v in historia if v["version"] == vigente["proveedor_version"])
    cuentas = aprobar.cuentas_humanas(c, historicas=True)
    return any(
        v["preparada_por"] in cuentas
        for v in historia
        if v["version"] <= fija["version"] and v["cuenta_destino"] == fija["cuenta_destino"]
    )


def _puede_decidir(
    c: Connection, aprobar: Permiso, orden: OrdenPago, filas: list, preparo: bool
) -> dict[str, bool]:
    """`puede_aprobar` y `puede_rechazar`: la misma separación que `_decision`, con cada persona
    que preparó una versión, como la base (`exigir_decision_sobre_orden`). `preparo` suma la
    identidad guardada en cada acto. No audita ni concede: las rutas vuelven a decidir."""
    preparadores = {orden.preparada_por, *(f["preparada_por"] for f in filas)}
    decisiones = aprobar.decidir_en(c, [Entidad(preparado_por=p) for p in preparadores])
    rechaza = (
        orden.estado == EstadoOrdenPago.EN_REVISION
        and not preparo
        and all(d.permitido for d in decisiones)
    )
    return {
        "puede_rechazar": rechaza,
        "puede_aprobar": rechaza
        and not orden.aprobada_por_vos
        and not _cargo_la_cuenta(c, aprobar, orden, filas),
    }


def _orden_con_versiones(permiso: Permiso, motor, orden_id: UUID, aprobar: Permiso | None = None):
    """Con `aprobar`, la revisión: cada versión trae la cuenta completa para que el Consejo la
    verifique, y la respuesta dice qué puede decidir quien lee; si no, solo sus últimos dígitos.
    Quien preparó la orden o alguna de sus versiones no la aprueba (`no_es_quien_preparo`), así
    que tampoco en la revisión recibe la cuenta completa: un consejero que además prepara la ve
    como quien prepara."""
    with _lectura(permiso, motor) as c:
        return _leer_orden_con_versiones(c, permiso, orden_id, aprobar)


def _leer_orden_con_versiones(
    c: Connection, permiso: Permiso, orden_id: UUID, aprobar: Permiso | None = None
):
    revision = aprobar is not None
    ordenes = _ordenes(c, permiso, orden_id)
    if not ordenes:
        raise ErrorApi(404, CodigoError.NO_ENCONTRADO, NO_ENCONTRADO)
    filas = list(
        _ejecutar(c, permiso, VERSIONES, {"o": orden_id, "f": LEE_COMO[permiso.accion]}).mappings()
    )
    evaluaciones = _evaluaciones(c, permiso, orden_id)
    # Por la identidad humana guardada en cada versión, no por cuenta ni por los vínculos de
    # hoy: quien preparó y revisa con otra cuenta suya tampoco recibe la cuenta completa,
    # aunque la cuenta con la que preparó ya no esté vinculada (#175; P3 115).
    preparo = revision and _ejecutar(c, permiso, PREPARO, {"o": orden_id}).scalar_one()
    versiones = []
    for f in filas:
        ev = evaluaciones[(orden_id, f["numero"])]
        datos = _recortada(f)
        modelo = VersionOrdenPago
        if revision:
            modelo = VersionOrdenPagoRevision
            datos["cuenta_destino"] = None if preparo else f["cuenta_destino"]
        versiones.append(modelo(
            **datos, franja=ev["franja"], alerta_fraccionamiento=ev["alerta_fraccionamiento"],
            umbral_importe_centavos=ev["umbral_importe_centavos"],
            umbral_moneda=ev["umbral_moneda"], contrato_id=ev["contrato_id"],
        ))  # fmt: skip
    if revision:
        return OrdenPagoConVersionesRevision(
            orden=ordenes[0], versiones=versiones,
            **_puede_decidir(c, aprobar, ordenes[0], filas, preparo),
        )  # fmt: skip
    return OrdenPagoConVersiones(orden=ordenes[0], versiones=versiones)


# --- Proveedores -------------------------------------------------------------------------------


def _proveedores(c: Connection, permiso: Permiso, proveedor: UUID | None) -> list[Proveedor]:
    sql = "select * from nucleo.proveedores_lectura(:p, :actor, 'api', :sesion)"
    filas = _ejecutar(c, permiso, sql, {"p": proveedor}).mappings()
    return [Proveedor(**_recortada(f)) for f in filas]


@router.get(
    "/consorcios/{consorcio_id}/proveedores",
    response_model=ListaProveedores,
    summary="Proveedores del consorcio, cada uno en su versión vigente",
    responses=RESPUESTAS_LECTURA,
)
def listar_proveedores(
    permiso: Annotated[Permiso, Depends(requiere(PREPARAR, al_entrar=True, sin_gesto=True))],
    motor: MotorDep,
) -> ListaProveedores:
    with _lectura(permiso, motor) as c:
        return ListaProveedores(elementos=_proveedores(c, permiso, None))


@router.get(
    "/consorcios/{consorcio_id}/proveedores/{proveedor_id}",
    response_model=ProveedorConVersiones,
    summary="Un proveedor con toda su historia: cada cambio de cuenta es una versión",
    responses=RESPUESTAS_LECTURA | {404: {"description": "No existe en este consorcio."}},
)
def ver_proveedor(
    proveedor_id: UUID,
    permiso: Annotated[Permiso, Depends(requiere(PREPARAR, al_entrar=True, sin_gesto=True))],
    motor: MotorDep,
) -> ProveedorConVersiones:
    with _lectura(permiso, motor) as c:
        proveedores = _proveedores(c, permiso, proveedor_id)
        if not proveedores:
            raise ErrorApi(404, CodigoError.NO_ENCONTRADO, NO_ENCONTRADO)
        sql = "select * from nucleo.proveedor_versiones_lectura(:p, :actor, 'api', :sesion)"
        filas = _ejecutar(c, permiso, sql, {"p": proveedor_id}).mappings()
        versiones = [VersionProveedor(**_recortada(f)) for f in filas]
    return ProveedorConVersiones(proveedor=proveedores[0], versiones=versiones)


def _datos_proveedor(d: DatosProveedor) -> dict:
    return {
        "razon": d.razon_social,
        "rubro": d.rubro,
        "contacto": d.contacto,
        "doc": d.documento_id,
        "cuenta": d.cuenta_destino,
        "telefono": d.telefono,
        "whatsapp": d.whatsapp,
        "correo": d.correo,
        "domicilio": d.domicilio,
        "atiende": d.atiende_emergencias,
    }


@router.post(
    "/consorcios/{consorcio_id}/proveedores",
    status_code=201,
    response_model=ProveedorCreado,
    summary="Dar de alta un proveedor (queda activo, versión 1)",
    responses=RESPUESTAS_ESCRITURA | {422: {"description": "CUIT o documento inválido."}},
)
def crear_proveedor(
    nuevo: ProveedorNuevo,
    permiso: Annotated[Permiso, Depends(requiere(PREPARAR, al_entrar=True))],
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    def efecto(conn: Connection) -> tuple[int, ProveedorCreado]:
        nuevo_id = _ejecutar(
            conn, permiso,
            "select nucleo.alta_proveedor(:cuit, :razon, :rubro, :contacto, :doc, :cuenta,"
            " :actor, 'api', :sesion, :telefono, :whatsapp, :correo, :domicilio, :atiende)",
            {"cuit": nuevo.cuit, **_datos_proveedor(nuevo)},
        ).scalar_one()  # fmt: skip
        return 201, ProveedorCreado(id=nuevo_id, version=1)

    return responder_una_vez(motor, permiso.ctx, idempotencia, efecto)


@router.post(
    "/consorcios/{consorcio_id}/proveedores/{proveedor_id}/cuit",
    response_model=ProveedorCreado,
    summary="Cargar el CUIT de un proveedor que no lo tiene: una sola vez, con una versión nueva",
    responses=RESPUESTAS_ESCRITURA | {422: {"description": "CUIT inválido."}},
)
def cargar_cuit_proveedor(
    proveedor_id: UUID,
    carga: CargaCuitProveedor,
    permiso: Annotated[Permiso, Depends(requiere(PREPARAR, al_entrar=True))],
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    def efecto(conn: Connection) -> tuple[int, ProveedorCreado]:
        version = _ejecutar(
            conn, permiso,
            "select nucleo.cargar_cuit_proveedor(:p, :esperada, :cuit, :actor, 'api', :sesion)",
            {"p": proveedor_id, "esperada": carga.version_esperada, "cuit": carga.cuit},
        ).scalar_one()  # fmt: skip
        return 200, ProveedorCreado(id=proveedor_id, version=version)

    return responder_una_vez(motor, permiso.ctx, idempotencia, efecto)


@router.post(
    "/consorcios/{consorcio_id}/proveedores/{proveedor_id}/versiones",
    response_model=ProveedorCreado,
    summary="Cambiar los datos de un proveedor: crea una versión nueva, nunca en silencio",
    responses=RESPUESTAS_ESCRITURA | {422: {"description": "Documento inválido."}},
)
def versionar_proveedor(
    proveedor_id: UUID,
    datos: ProveedorVersionNueva,
    permiso: Annotated[Permiso, Depends(requiere(PREPARAR, al_entrar=True))],
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    def efecto(conn: Connection) -> tuple[int, ProveedorCreado]:
        datos_nuevos = _datos_proveedor(datos)
        if datos.conservar_cuenta:
            # La cuenta de la versión esperada, leída en la misma transacción: si la vigente ya
            # es otra, la función niega la versión desactualizada y esta cuenta no se usa.
            vigente = _ejecutar(
                conn, permiso,
                "select version, cuenta_destino from nucleo.proveedores_lectura(:p, :actor, 'api',"
                " :sesion)", {"p": proveedor_id},
            ).mappings().one_or_none()  # fmt: skip
            if vigente is not None and vigente["version"] == datos.version_esperada:
                datos_nuevos["cuenta"] = vigente["cuenta_destino"]
        version = _ejecutar(
            conn, permiso,
            "select nucleo.nueva_version_proveedor(:p, :esperada, :razon, :rubro, :contacto,"
            " cast(:estado as nucleo.estado_proveedor), :doc, :cuenta, :actor, 'api', :sesion,"
            " :telefono, :whatsapp, :correo, :domicilio, :atiende)",
            {"p": proveedor_id, "esperada": datos.version_esperada, "estado": datos.estado.value,
             **datos_nuevos},
            # El proveedor viene en la ruta: si no existe es 404, como al verlo.
            {"proveedor_inexistente": ERRORES["no_encontrado"]},
        ).scalar_one()  # fmt: skip
        return 200, ProveedorCreado(id=proveedor_id, version=version)

    return responder_una_vez(motor, permiso.ctx, idempotencia, efecto)


# --- Documentos para carga: la IA propone, la persona confirma (`0088`) -------------------------


@router.post(
    "/consorcios/{consorcio_id}/documentos/para-carga",
    status_code=201,
    response_model=DocumentoId,
    summary="Crear un documento para cargar datos con IA: la factura de una orden o lo que respalda"
    " los datos de un proveedor",
    responses=RESPUESTAS_LECTURA,
)
def crear_documento_para_carga(
    nuevo: DocumentoParaCarga,
    permiso: Annotated[Permiso, Depends(requiere(PREPARAR, al_entrar=True, sin_gesto=True))],
    cargar: Annotated[Permiso, Depends(requiere("documentos.cargar"))],
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    """Como `POST …/documentos`, en «Facturas y comprobantes», para quien prepara órdenes y
    proveedores y además carga documentos. El archivo se sube después a
    `…/documentos/{id}/versiones`; cuando el antivirus lo aprueba, con la lectura encendida, la IA
    lo lee (`GET …/lectura-carga`). Sin gesto: no mueve dinero, y confirmar lo leído es mandar el
    formulario de la orden o del proveedor, que pide lo suyo."""
    cargar.verificar(Entidad())

    def efecto(conn: Connection) -> tuple[int, DocumentoId]:
        doc = _ejecutar(
            conn, permiso,
            "select nucleo.crear_documento_para_carga(:tipo, :titulo, :fecha, :actor, 'api',"
            " :sesion)",
            {"tipo": nuevo.tipo.value, "titulo": nuevo.titulo, "fecha": nuevo.fecha},
        ).scalar_one()  # fmt: skip
        return 201, DocumentoId(id=doc)

    return responder_una_vez(motor, permiso.ctx, idempotencia, efecto)


def _proveedor_sugerido(c: Connection, permiso: Permiso, cuit: str | None):
    """El proveedor activo con ese CUIT. El CUIT es único en el consorcio (`0048`)."""
    if cuit is None:
        return None
    activo = next(
        (p for p in _proveedores(c, permiso, None)
         if p.cuit == cuit and p.estado == EstadoProveedor.ACTIVO),
        None,
    )  # fmt: skip
    if activo is None:
        return None
    return ProveedorSugerido(id=activo.id, razon_social=activo.razon_social)


@router.get(
    "/consorcios/{consorcio_id}/documentos/{documento_id}/versiones/{version_id}/lectura-carga",
    response_model=LecturaCarga,
    summary="Lo que la IA leyó de un documento para carga: propone, la persona confirma",
    responses=RESPUESTAS_LECTURA | {
        404: {"description": RESPUESTAS_VERSION[404]["description"] + " O no es un documento para"
              " carga."},
    },
)  # fmt: skip
def lectura_para_carga(
    documento_id: UUID,
    version_id: UUID,
    permiso: Annotated[Permiso, Depends(requiere(PREPARAR, al_entrar=True, sin_gesto=True))],
    ver: Annotated[Permiso, Depends(requiere("documentos.ver"))],
    restringidos: Annotated[Permiso, Depends(requiere("documentos.ver_restringidos"))],
    motor: MotorDep,
) -> LecturaCarga:
    """Con las reglas del archivo (quien no puede abrir la versión no sabe qué dice: 404) y solo
    para quien prepara. Mientras `estado_version` es `en_cuarentena` o `estado` es `leyendo`, se
    vuelve a preguntar."""
    f = version_visible(documento_id, version_id, ver, restringidos, motor)
    with _lectura(permiso, motor) as c:
        leido = _ejecutar(
            c, permiso,
            "select * from nucleo.lectura_carga_lectura(:d, :v, :actor, 'api', :sesion)",
            {"d": documento_id, "v": version_id},
        ).one_or_none()  # fmt: skip
        if leido is None:
            raise ErrorApi(404, CodigoError.NO_ENCONTRADO, NO_ENCONTRADO)
        sugerido = _proveedor_sugerido(c, permiso, leido.cuit)
    return LecturaCarga(
        documento_id=documento_id, version_documento_id=version_id, tipo=TipoCarga(leido.tipo),
        estado_version=EstadoVersion(f["estado"]), estado=EstadoLecturaCarga(leido.estado),
        cuit=leido.cuit, cuit_invalido=leido.cuit_invalido, razon_social=leido.razon_social,
        domicilio=leido.domicilio, telefono=leido.telefono, correo=leido.correo,
        numero_comprobante=leido.numero_comprobante, fecha=leido.fecha,
        importe_centavos=None if leido.importe is None else int(leido.importe * 100),
        moneda=leido.moneda, concepto=leido.concepto, proveedor_sugerido=sugerido,
    )  # fmt: skip


# --- Órdenes de pago: preparación --------------------------------------------------------------


@router.get(
    "/consorcios/{consorcio_id}/ordenes-pago",
    response_model=ListaOrdenesPago,
    summary="Órdenes de pago del consorcio, borradores incluidos (preparación)",
    responses=RESPUESTAS_LECTURA,
)
def listar_ordenes(
    permiso: Annotated[Permiso, Depends(requiere(PREPARAR, al_entrar=True, sin_gesto=True))],
    motor: MotorDep,
) -> ListaOrdenesPago:
    with _lectura(permiso, motor) as c:
        return ListaOrdenesPago(elementos=_ordenes(c, permiso, None))


@router.get(
    "/consorcios/{consorcio_id}/ordenes-pago/revision",
    response_model=ListaOrdenesPago,
    summary="Órdenes de pago ya enviadas a revisión (quien aprueba)",
    responses=RESPUESTAS_LECTURA,
)
def listar_ordenes_revision(
    permiso: Annotated[Permiso, Depends(requiere(REVISAR, al_entrar=True, sin_gesto=True))],
    motor: MotorDep,
) -> ListaOrdenesPago:
    with _lectura(permiso, motor) as c:
        return ListaOrdenesPago(elementos=_ordenes(c, permiso, None))


@router.get(
    "/consorcios/{consorcio_id}/ordenes-pago/{orden_id}",
    response_model=OrdenPagoConVersiones,
    summary="Una orden con todas sus versiones (preparación)",
    responses=RESPUESTAS_LECTURA | {404: {"description": "No existe en este consorcio."}},
)
def ver_orden(
    orden_id: UUID,
    permiso: Annotated[Permiso, Depends(requiere(PREPARAR, al_entrar=True, sin_gesto=True))],
    motor: MotorDep,
) -> OrdenPagoConVersiones:
    return _orden_con_versiones(permiso, motor, orden_id)


@router.get(
    "/consorcios/{consorcio_id}/ordenes-pago/{orden_id}/revision",
    response_model=OrdenPagoConVersionesRevision,
    summary="Una orden enviada a revisión, con la huella y la cuenta completa de cada versión",
    responses=RESPUESTAS_LECTURA | {404: {"description": "No existe o es un borrador."}},
)
def ver_orden_revision(
    orden_id: UUID,
    permiso: Annotated[Permiso, Depends(requiere(REVISAR, al_entrar=True, sin_gesto=True))],
    aprobar: Annotated[Permiso, Depends(requiere(APROBAR, sin_gesto=True))],
    motor: MotorDep,
) -> OrdenPagoConVersionesRevision:
    return _orden_con_versiones(permiso, motor, orden_id, aprobar)


def _datos_orden(d: DatosOrdenPago) -> dict:
    return {
        "importe": d.importe_centavos,
        "moneda": d.moneda,
        "concepto": d.concepto,
        "proveedor": d.proveedor_id,
        "comprobante": d.comprobante,
        "reserva": d.usa_fondo_reserva,
        "contrato": d.contrato_id,
        "factura": d.factura_version_id,
    }


@router.post(
    "/consorcios/{consorcio_id}/ordenes-pago",
    status_code=201,
    response_model=OrdenPagoCambiada,
    summary="Preparar una orden de pago (queda en borrador, versión 1)",
    responses=RESPUESTAS_ESCRITURA | {422: {"description": DESCRIPCION_422_ORDEN}},
)
def preparar_orden(
    nueva: OrdenPagoNueva,
    permiso: Annotated[Permiso, Depends(requiere(PREPARAR, al_entrar=True, sin_gesto=True))],
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    def efecto(conn: Connection) -> tuple[int, OrdenPagoCambiada]:
        orden_id = _ejecutar(
            conn, permiso,
            "select nucleo.preparar_orden_pago(:importe, :moneda, :concepto, :proveedor,"
            " :comprobante, :reserva, :contrato, :actor, 'api', :sesion, :factura)",
            _datos_orden(nueva),
        ).scalar_one()  # fmt: skip
        return 201, OrdenPagoCambiada(id=orden_id, version=1)

    return responder_una_vez(motor, permiso.ctx, idempotencia, efecto)


@router.post(
    "/consorcios/{consorcio_id}/ordenes-pago/{orden_id}/versiones",
    response_model=OrdenPagoCambiada,
    summary="Modificar una orden: crea una versión nueva y la vuelve a revisión si hacía falta",
    responses=RESPUESTAS_ESCRITURA | {422: {"description": DESCRIPCION_422_ORDEN}},
)
def modificar_orden(
    orden_id: UUID,
    datos: OrdenPagoVersionNueva,
    permiso: Annotated[Permiso, Depends(requiere(PREPARAR, al_entrar=True, sin_gesto=True))],
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    def efecto(conn: Connection) -> tuple[int, OrdenPagoCambiada]:
        version = _ejecutar(
            conn, permiso,
            "select nucleo.modificar_orden_pago(:o, :esperada, :importe, :moneda, :concepto,"
            " :proveedor, :comprobante, :reserva, :contrato, :actor, 'api', :sesion, :factura)",
            {"o": orden_id, "esperada": datos.version_esperada, **_datos_orden(datos)},
        ).scalar_one()  # fmt: skip
        return 200, OrdenPagoCambiada(id=orden_id, version=version)

    return responder_una_vez(motor, permiso.ctx, idempotencia, efecto)


@router.post(
    "/consorcios/{consorcio_id}/ordenes-pago/{orden_id}/enviar-a-revision",
    response_model=OrdenPagoCambiada,
    summary="Enviar un borrador a revisión",
    responses=RESPUESTAS_ESCRITURA,
)
def enviar_a_revision(
    orden_id: UUID,
    envio: EnvioARevision,
    permiso: Annotated[Permiso, Depends(requiere(PREPARAR, al_entrar=True, sin_gesto=True))],
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    def efecto(conn: Connection) -> tuple[int, OrdenPagoCambiada]:
        _ejecutar(
            conn, permiso,
            "select nucleo.enviar_orden_a_revision(:o, :esperada, :actor, 'api', :sesion)",
            {"o": orden_id, "esperada": envio.version_esperada},
        )  # fmt: skip
        return 200, OrdenPagoCambiada(id=orden_id, version=envio.version_esperada)

    return responder_una_vez(motor, permiso.ctx, idempotencia, efecto)


# --- Órdenes de pago: aprobar y rechazar -------------------------------------------------------


def _decision(revision: Permiso, permiso: Permiso, motor, orden_id: UUID) -> None:
    """Lee la orden con la revisión ya verificada (sin borradores: 404) y evalúa la separación
    de quien decide con quien la preparó. La base compara además con quien preparó cada versión
    y con quien cargó la cuenta del proveedor."""
    with _lectura(revision, motor) as c:
        ordenes = _ordenes(c, revision, orden_id)
    if not ordenes:
        raise ErrorApi(404, CodigoError.NO_ENCONTRADO, NO_ENCONTRADO)
    permiso.verificar(Entidad(preparado_por=ordenes[0].preparada_por))


@router.post(
    "/consorcios/{consorcio_id}/ordenes-pago/{orden_id}/aprobaciones",
    status_code=201,
    response_model=AprobacionRegistrada,
    summary="Aprobar una versión: con la versión y la huella que se vieron",
    responses=RESPUESTAS_ESCRITURA,
)
def aprobar_orden(
    orden_id: UUID,
    aprobacion: Aprobacion,
    revision: Annotated[Permiso, Depends(requiere(REVISAR, al_entrar=True))],
    permiso: Annotated[Permiso, Depends(requiere(APROBAR))],
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    _decision(revision, permiso, motor, orden_id)

    def efecto(conn: Connection) -> tuple[int, AprobacionRegistrada]:
        aprobacion_id = _ejecutar(
            conn, permiso,
            "select nucleo.registrar_aprobacion_orden_pago(:o, :version, :huella, :actor, 'api',"
            " :sesion)",
            {"o": orden_id, "version": aprobacion.version,
             "huella": bytes.fromhex(aprobacion.huella)},
        ).scalar_one()  # fmt: skip
        return 201, AprobacionRegistrada(
            id=aprobacion_id, orden_id=orden_id, version=aprobacion.version
        )

    return responder_una_vez(motor, permiso.ctx, idempotencia, efecto)


@router.post(
    "/consorcios/{consorcio_id}/ordenes-pago/{orden_id}/rechazar",
    response_model=OrdenPagoCambiada,
    summary="Rechazar la versión vigente, con el motivo",
    responses=RESPUESTAS_ESCRITURA,
)
def rechazar_orden(
    orden_id: UUID,
    rechazo: Rechazo,
    revision: Annotated[Permiso, Depends(requiere(REVISAR, al_entrar=True))],
    permiso: Annotated[Permiso, Depends(requiere(APROBAR))],
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    _decision(revision, permiso, motor, orden_id)

    def efecto(conn: Connection) -> tuple[int, OrdenPagoCambiada]:
        _ejecutar(
            conn, permiso,
            "select nucleo.rechazar_orden_pago(:o, :version, :motivo, :actor, 'api', :sesion)",
            {"o": orden_id, "version": rechazo.version, "motivo": rechazo.motivo},
        )  # fmt: skip
        return 200, OrdenPagoCambiada(id=orden_id, version=rechazo.version)

    return responder_una_vez(motor, permiso.ctx, idempotencia, efecto)


@router.post(
    "/consorcios/{consorcio_id}/ordenes-pago/{orden_id}/renovar",
    response_model=OrdenPagoCambiada,
    summary="Renovar una orden aprobada hace más de 7 días: versión nueva, de nuevo a revisión",
    responses=RESPUESTAS_ESCRITURA,
)
def renovar_orden(
    orden_id: UUID,
    renovacion: Renovacion,
    permiso: Annotated[Permiso, Depends(requiere(PREPARAR, al_entrar=True, sin_gesto=True))],
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    def efecto(conn: Connection) -> tuple[int, OrdenPagoCambiada]:
        version = _ejecutar(
            conn, permiso,
            "select nucleo.renovar_orden_vencida(:o, :esperada, :actor, 'api', :sesion)",
            {"o": orden_id, "esperada": renovacion.version_esperada},
        ).scalar_one()  # fmt: skip
        return 200, OrdenPagoCambiada(id=orden_id, version=version)

    return responder_una_vez(motor, permiso.ctx, idempotencia, efecto)


# --- Órdenes de pago: emitir -------------------------------------------------------------------


@router.post(
    "/consorcios/{consorcio_id}/ordenes-pago/{orden_id}/emitir",
    response_model=OrdenPagoCambiada,
    summary="Emitir una orden aprobada (queda en ejecución); no mueve dinero",
    responses=RESPUESTAS_ESCRITURA,
)
def emitir_orden(
    orden_id: UUID,
    emision: Emision,
    lectura: Annotated[Permiso, Depends(requiere(PREPARAR, al_entrar=True))],
    permiso: Annotated[Permiso, Depends(requiere(EJECUTAR))],
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    """Quien emite lee la orden como quien prepara (el paquete del administrador tiene las dos
    facultades) y la separación se evalúa con quienes aprobaron la versión vigente (A5). La base
    vuelve a exigir todo: la franja, las aprobaciones, los 7 días, el proveedor, la cuenta y la
    separación por identidad humana."""
    detalle = _orden_con_versiones(lectura, motor, orden_id)
    vigente = next(v for v in detalle.versiones if v.numero == detalle.orden.version_vigente)
    # Lo que se emite es la versión y la huella vistas. La versión de la orden no cambia nunca
    # (solo se agregan versiones), así que la base, que exige la versión vigente, exige también
    # esta huella.
    vista = next((v for v in detalle.versiones if v.numero == emision.version), None)
    if vista is not None and vista.huella != emision.huella:
        raise ErrorApi(*ERRORES["huella_distinta"])
    # El respaldo es que la orden pasó la aprobación, también si ya se emitió: así el reintento
    # con la misma clave devuelve la respuesta guardada, y otra emisión la niega la base (409).
    permiso.verificar(Entidad(
        aprobado_por=tuple(vigente.aprobada_por),
        respaldo_presente=detalle.orden.estado in RESPALDADAS,
    ))  # fmt: skip

    def efecto(conn: Connection) -> tuple[int, OrdenPagoCambiada]:
        _ejecutar(
            conn, permiso,
            "select nucleo.emitir_orden_pago(:o, :version, :actor, 'api', :sesion)",
            {"o": orden_id, "version": emision.version},
        )  # fmt: skip
        return 200, OrdenPagoCambiada(id=orden_id, version=emision.version)

    return responder_una_vez(motor, permiso.ctx, idempotencia, efecto)


# --- Contratos y abonos ------------------------------------------------------------------------


def _contratos(c: Connection, permiso: Permiso, contrato: UUID | None, revision: bool = False):
    """Quien prepara recibe los últimos dígitos de la cuenta; la revisión del Consejo, también la
    completa, que es lo que confirma, salvo para quien registró el contrato."""
    sql = "select * from nucleo.contratos_proveedor_lectura(:k, :f, :actor, 'api', :sesion)"
    filas = _ejecutar(c, permiso, sql, {"k": contrato, "f": LEE_COMO[permiso.accion]})
    contratos = []
    for f in filas.mappings():
        datos = dict(f)
        datos["cuenta_ultimos_digitos"] = ultimos_digitos(datos["cuenta_destino"])
        if revision:
            # Quien registró el contrato ocupa el lugar de quien preparó: por la identidad humana
            # guardada al registrarlo, tampoco con otra cuenta suya recibe la cuenta completa
            # (Codex en #178; P3 115).
            registro = _ejecutar(c, permiso, REGISTRO, {"k": datos["id"]}).scalar_one()
            if registro:
                datos["cuenta_destino"] = None
            contratos.append(ContratoRevision(**datos))
        else:
            del datos["cuenta_destino"]
            contratos.append(Contrato(**datos))
    return contratos


@router.get(
    "/consorcios/{consorcio_id}/contratos",
    response_model=ListaContratos,
    summary="Contratos y abonos que respaldan lo rutinario (preparación)",
    responses=RESPUESTAS_LECTURA,
)
def listar_contratos(
    permiso: Annotated[Permiso, Depends(requiere(PREPARAR, al_entrar=True, sin_gesto=True))],
    motor: MotorDep,
) -> ListaContratos:
    with _lectura(permiso, motor) as c:
        return ListaContratos(elementos=_contratos(c, permiso, None))


@router.get(
    "/consorcios/{consorcio_id}/contratos/revision",
    response_model=ListaContratosRevision,
    summary="Contratos y abonos, para confirmarlos (quien aprueba), con la cuenta completa",
    responses=RESPUESTAS_LECTURA,
)
def listar_contratos_revision(
    permiso: Annotated[Permiso, Depends(requiere(REVISAR, al_entrar=True, sin_gesto=True))],
    motor: MotorDep,
) -> ListaContratosRevision:
    with _lectura(permiso, motor) as c:
        return ListaContratosRevision(elementos=_contratos(c, permiso, None, revision=True))


@router.post(
    "/consorcios/{consorcio_id}/contratos",
    status_code=201,
    response_model=ContratoCambiado,
    summary="Registrar un contrato o abono: no rige hasta que lo confirme un consejero",
    responses=RESPUESTAS_ESCRITURA
    | {422: {"description": "Proveedor, documento, decisión o vigencia inválidos."}},
)
def registrar_contrato(
    nuevo: ContratoNuevo,
    permiso: Annotated[Permiso, Depends(requiere(PREPARAR, al_entrar=True))],
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    def efecto(conn: Connection) -> tuple[int, ContratoCambiado]:
        contrato_id = _ejecutar(
            conn, permiso,
            "select nucleo.registrar_contrato_proveedor(:p, :tipo, :alcance, :tope, :moneda,"
            " :desde, :hasta, :doc, :decision, :actor, 'api', :sesion)",
            {"p": nuevo.proveedor_id, "tipo": nuevo.tipo.value, "alcance": nuevo.alcance,
             "tope": nuevo.importe_maximo_centavos, "moneda": nuevo.moneda,
             "desde": nuevo.vigente_desde, "hasta": nuevo.vigente_hasta,
             "doc": nuevo.documento_id, "decision": nuevo.decision_id},
        ).scalar_one()  # fmt: skip
        return 201, ContratoCambiado(id=contrato_id)

    return responder_una_vez(motor, permiso.ctx, idempotencia, efecto)


@router.post(
    "/consorcios/{consorcio_id}/contratos/{contrato_id}/confirmacion",
    status_code=201,
    response_model=ContratoCambiado,
    summary="Confirmar un contrato, con la huella que se vio: desde ahí respalda lo rutinario",
    responses=RESPUESTAS_ESCRITURA,
)
def confirmar_contrato(
    contrato_id: UUID,
    confirmacion: Confirmacion,
    revision: Annotated[Permiso, Depends(requiere(REVISAR, al_entrar=True))],
    permiso: Annotated[Permiso, Depends(requiere(APROBAR))],
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    with _lectura(revision, motor) as c:
        contratos = _contratos(c, revision, contrato_id)
    if not contratos:
        raise ErrorApi(404, CodigoError.NO_ENCONTRADO, NO_ENCONTRADO)
    # Quien registró el contrato ocupa el lugar de quien preparó (`no_es_quien_preparo`).
    permiso.verificar(Entidad(preparado_por=contratos[0].registrado_por))

    def efecto(conn: Connection) -> tuple[int, ContratoCambiado]:
        _ejecutar(
            conn, permiso,
            "select nucleo.confirmar_contrato_proveedor(:k, :huella, :version, :actor, 'api',"
            " :sesion)",
            {"k": contrato_id, "huella": bytes.fromhex(confirmacion.huella),
             "version": confirmacion.proveedor_version},
            errores=ERRORES_CONFIRMACION,
        )  # fmt: skip
        return 201, ContratoCambiado(id=contrato_id)

    return responder_una_vez(motor, permiso.ctx, idempotencia, efecto)


@router.post(
    "/consorcios/{consorcio_id}/contratos/{contrato_id}/baja",
    response_model=ContratoCambiado,
    summary="Dar de baja un contrato: deja de respaldar las órdenes nuevas",
    responses=RESPUESTAS_ESCRITURA,
)
def baja_contrato(
    contrato_id: UUID,
    permiso: Annotated[Permiso, Depends(requiere(PREPARAR, al_entrar=True))],
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    def efecto(conn: Connection) -> tuple[int, ContratoCambiado]:
        _ejecutar(
            conn, permiso, "select nucleo.baja_contrato_proveedor(:k, :actor, 'api', :sesion)",
            {"k": contrato_id},
        )  # fmt: skip
        return 200, ContratoCambiado(id=contrato_id)

    return responder_una_vez(motor, permiso.ctx, idempotencia, efecto)


# --- Pago manual (`0055`) ----------------------------------------------------------------------


def _puede_resolver(
    c: Connection,
    conciliar: Permiso,
    o: OrdenPago,
    registraron: set[UUID],
    declararon: set[UUID],
    tomaron: set[UUID],
) -> tuple[bool, bool]:
    """Si quien lee queda separado de la orden para conciliar y para confirmar que no se pagó: la
    separación de esas rutas, con todos los que registraron, declararon o tomaron el pago en
    cualquier ejecución de la orden, como `nucleo.exigir_no_ejecuto`.

    Quién vio la cuenta no lo lee `cem_app`. Confirmar no lo necesita: con una declaración
    `confirmable`, todos los que la vieron declararon, y por eso declarar cuenta. Conciliar sí:
    a quien la vio y no registró se lo niega la base (403, regla 2). Declarar no cuenta para
    conciliar porque se puede declarar sin haberla visto, y no se le esconde a nadie lo que sí
    puede hacer. No audita ni concede: las rutas vuelven a decidir."""
    if _ejecutar(c, conciliar, PREPARO, {"o": o.id}).scalar_one():
        return False, False
    personas = list(registraron | declararon | tomaron)
    decisiones = conciliar.decidir_en(
        c, [Entidad(preparado_por=o.preparada_por, ejecutado_por=p) for p in personas]
    )
    negadas = {p for p, d in zip(personas, decisiones, strict=True) if not d.permitido}
    concilia = bool(registraron) and negadas.isdisjoint(registraron | tomaron)
    return concilia, bool(personas) and not negadas


def _tomas(c: Connection, lector: Permiso, orden: UUID | None):
    return _ejecutar(
        c, lector,
        "select * from nucleo.tomas_pago_manual_lectura(:o, :f, :actor, 'api', :sesion)",
        {"o": orden, "f": LEE_COMO[lector.accion]},
    ).mappings().all()  # fmt: skip


def _quienes_tomaron(c: Connection, lector: Permiso, orden: UUID | None) -> dict[UUID, set[UUID]]:
    """Quiénes tomaron el pago en cada orden: ocupan el lugar de quien ejecutó, aunque no hayan
    abierto la cuenta."""
    tomaron: dict[UUID, set[UUID]] = {}
    for fila in _tomas(c, lector, orden):
        tomaron.setdefault(fila["orden_id"], set()).add(fila["tomado_por"])
    return tomaron


def _verificar_no_tomo(
    lector: Permiso, permiso: Permiso, motor, orden_id: UUID, preparada_por: UUID
) -> None:
    """La separación con quien tomó el pago, como con quien registró."""
    with _lectura(lector, motor) as c:
        tomaron = _quienes_tomaron(c, lector, orden_id).get(orden_id, set())
    for persona in tomaron:
        permiso.verificar(Entidad(preparado_por=preparada_por, ejecutado_por=persona))


def _leer_ejecuciones(c: Connection, permiso: Permiso, orden: UUID | None):
    """Las ejecuciones manuales con sus registros y declaraciones, y quiénes registraron o
    declararon en cada orden. Sin la cuenta."""
    datos_lectura = {"o": orden, "f": LEE_COMO[permiso.accion]}
    ejecuciones = _ejecutar(
        c, permiso, "select * from nucleo.ejecuciones_manuales_lectura(:o, :f, :actor, 'api',"
        " :sesion)", datos_lectura,
    ).mappings().all()  # fmt: skip
    pagos: dict[UUID, list[PagoManual]] = {}
    registraron: dict[UUID, set[UUID]] = {}
    for fila in _ejecutar(
        c, permiso, "select * from nucleo.pagos_manuales_lectura(:o, :f, :actor, 'api', :sesion)",
        datos_lectura,
    ).mappings():  # fmt: skip
        datos = dict(fila)
        registraron.setdefault(datos.pop("orden_id"), set()).add(datos["registrado_por"])
        pagos.setdefault(datos.pop("ejecucion_id"), []).append(PagoManual(**datos))
    declaraciones: dict[UUID, list[dict]] = {}
    declararon: dict[UUID, set[UUID]] = {}
    for fila in _ejecutar(
        c, permiso, "select * from nucleo.declaraciones_pago_no_realizado_lectura(:o, :f, :actor,"
        " 'api', :sesion)", datos_lectura,
    ).mappings():  # fmt: skip
        datos = dict(fila)
        declararon.setdefault(datos.pop("orden_id"), set()).add(datos["declarado_por"])
        declaraciones.setdefault(datos.pop("ejecucion_id"), []).append(datos)
    return ejecuciones, pagos, registraron, declaraciones, declararon


def _pagos_manuales(
    c: Connection, permiso: Permiso, conciliar: Permiso, orden: UUID | None
) -> list[OrdenConPagoManual]:
    """Las órdenes con una ejecución manual de su versión vigente, con sus registros y
    declaraciones, sin la cuenta. Las ejecuciones se leen **después** de las órdenes: una que no
    sea de la versión leída (la orden cambió en el medio) no se muestra. Qué puede resolver quien
    lee se calcula solo en las órdenes que esperan el pago y tienen algo por resolver."""
    ordenes = {o.id: o for o in _ordenes(c, permiso, orden)}
    ejecuciones, pagos, registraron, declaraciones, declararon = _leer_ejecuciones(
        c, permiso, orden
    )
    tomaron = _quienes_tomaron(c, permiso, orden)
    alcanza_conciliar = alcanza(CONCILIAR, conciliar.ctx.autorizacion)
    elementos = []
    for e in ejecuciones:
        o = ordenes.get(e["orden_id"])
        if o is None or o.version_vigente != e["version"]:
            continue
        de_la_ejecucion = pagos.get(e["id"], [])
        declaradas = declaraciones.get(e["id"], [])
        por_conciliar = (
            o.estado in ESPERAN_PAGO
            and e["resultado"] is None
            and any(p.conciliacion is None for p in de_la_ejecucion)
        )
        concilia = confirma = False
        if alcanza_conciliar and (por_conciliar or any(d["confirmable"] for d in declaradas)):
            concilia, confirma = _puede_resolver(
                c,
                conciliar,
                o,
                registraron.get(o.id, set()),
                declararon.get(o.id, set()),
                tomaron.get(o.id, set()),
            )
        elementos.append(OrdenConPagoManual(
            orden=o, puede_conciliar=por_conciliar and concilia,
            ejecucion=EjecucionManual(
                id=e["id"], version=e["version"], creada_en=e["creada_en"],
                resultado=e["resultado"], referencia=e["referencia"], pagos=de_la_ejecucion,
                declaraciones=[PagoNoRealizado(**d, puede_confirmar=d["confirmable"] and confirma)
                               for d in declaradas],
            ),
        ))  # fmt: skip
    return elementos


@router.get(
    "/consorcios/{consorcio_id}/pagos-manuales",
    response_model=ListaPagosManuales,
    summary="Pagos manuales por pagar, por conciliar y resueltos (quien ejecuta), sin la cuenta",
    responses=RESPUESTAS_LECTURA,
)
def listar_pagos_manuales(
    permiso: Annotated[Permiso, Depends(requiere(PREPARAR, al_entrar=True, sin_gesto=True))],
    conciliar: Annotated[Permiso, Depends(requiere(CONCILIAR, sin_gesto=True))],
    motor: MotorDep,
) -> ListaPagosManuales:
    with _lectura(permiso, motor) as c:
        return ListaPagosManuales(elementos=_pagos_manuales(c, permiso, conciliar, None))


@router.get(
    "/consorcios/{consorcio_id}/pagos-manuales/revision",
    response_model=ListaPagosManuales,
    summary="Pagos manuales para conciliar (Consejo), sin la cuenta",
    responses=RESPUESTAS_LECTURA,
)
def listar_pagos_manuales_revision(
    permiso: Annotated[Permiso, Depends(requiere(REVISAR, al_entrar=True, sin_gesto=True))],
    conciliar: Annotated[Permiso, Depends(requiere(CONCILIAR, sin_gesto=True))],
    motor: MotorDep,
) -> ListaPagosManuales:
    with _lectura(permiso, motor) as c:
        return ListaPagosManuales(elementos=_pagos_manuales(c, permiso, conciliar, None))


def _plazo_para_tomar(request: Request) -> timedelta:
    return timedelta(hours=request.app.state.config.pagos_horas_tomar)


def _ejecutor(lectura: Permiso, permiso: Permiso, motor, orden_id: UUID) -> None:
    """Como al emitir: quien ejecuta lee la orden como quien prepara y la separación se evalúa con
    quienes aprobaron la versión vigente (A5). La base vuelve a exigir todo."""
    detalle = _orden_con_versiones(lectura, motor, orden_id)
    vigente = next(v for v in detalle.versiones if v.numero == detalle.orden.version_vigente)
    permiso.verificar(Entidad(
        aprobado_por=tuple(vigente.aprobada_por),
        respaldo_presente=detalle.orden.estado in RESPALDADAS,
    ))  # fmt: skip


@router.post(
    "/consorcios/{consorcio_id}/ordenes-pago/{orden_id}/pago-manual/cuenta",
    response_model=CuentaParaPagoManual,
    summary="El acto de ejecución manual: la cuenta completa para transferir",
    responses=RESPUESTAS_ESCRITURA,
)
def cuenta_para_pago_manual(
    orden_id: UUID,
    pedido: PedidoCuentaPagoManual,
    lectura: Annotated[Permiso, Depends(requiere(PREPARAR, al_entrar=True))],
    permiso: Annotated[Permiso, Depends(requiere(EJECUTAR))],
    motor: MotorDep,
) -> CuentaParaPagoManual:
    """La única ruta que da la cuenta completa a quien va a pagar. Se evalúa de nuevo en cada
    pedido y cada uno queda en la auditoría, con los últimos 4 dígitos. No cambia nada más: no
    lleva `Idempotency-Key`."""
    _ejecutor(lectura, permiso, motor, orden_id)
    with _lectura(permiso, motor) as c:
        fila = _ejecutar(
            c, permiso,
            "select * from nucleo.cuenta_para_pago_manual(:o, :version, :huella, :actor, 'api',"
            " :sesion)",
            {"o": orden_id, "version": pedido.version, "huella": bytes.fromhex(pedido.huella)},
        ).mappings().one()  # fmt: skip
    return CuentaParaPagoManual(**fila)


@router.post(
    "/consorcios/{consorcio_id}/ordenes-pago/{orden_id}/pago-manual/tomar",
    status_code=201,
    response_model=TomaRegistrada,
    summary="Tomar el pago de quien vio la cuenta y no dijo nada: no da la cuenta",
    responses=RESPUESTAS_ESCRITURA,
)
def tomar_pago_manual(
    request: Request,
    orden_id: UUID,
    toma: TomaPagoManual,
    lectura: Annotated[Permiso, Depends(requiere(PREPARAR, al_entrar=True))],
    permiso: Annotated[Permiso, Depends(requiere(EJECUTAR))],
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    """Con la separación de pagar. La base mira el plazo y deja el evento (`0078`)."""
    _ejecutor(lectura, permiso, motor, orden_id)
    plazo = _plazo_para_tomar(request)

    def efecto(conn: Connection) -> tuple[int, TomaRegistrada]:
        nueva = _ejecutar(
            conn, permiso,
            "select nucleo.tomar_pago_manual(:o, :version, :huella, :plazo, :actor, 'api',"
            " :sesion)",
            {"o": orden_id, "version": toma.version, "huella": bytes.fromhex(toma.huella),
             "plazo": plazo},
        ).scalar_one()  # fmt: skip
        return 201, TomaRegistrada(id=nueva, orden_id=orden_id)

    return responder_una_vez(motor, permiso.ctx, idempotencia, efecto)


@router.post(
    "/consorcios/{consorcio_id}/ordenes-pago/{orden_id}/pago-manual",
    status_code=201,
    response_model=PagoManualRegistrado,
    summary="Registrar un pago manual con su evidencia: queda pendiente de conciliación",
    responses=RESPUESTAS_ESCRITURA | {422: {"description": "Evidencia inválida."}},
)
def registrar_pago_manual(
    orden_id: UUID,
    registro: RegistroPagoManual,
    lectura: Annotated[Permiso, Depends(requiere(PREPARAR, al_entrar=True))],
    permiso: Annotated[Permiso, Depends(requiere(EJECUTAR))],
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    _ejecutor(lectura, permiso, motor, orden_id)

    def efecto(conn: Connection) -> tuple[int, PagoManualRegistrado]:
        pago = _ejecutar(
            conn, permiso,
            "select nucleo.registrar_pago_manual(:o, :version, :huella, :numero, :fecha, :importe,"
            " :moneda, :cuenta, :doc, :actor, 'api', :sesion, :mal)",
            {"o": orden_id, "version": registro.version,
             "huella": bytes.fromhex(registro.huella), "numero": registro.numero_operacion,
             "fecha": registro.fecha_operacion, "importe": registro.importe_centavos,
             "moneda": registro.moneda, "cuenta": registro.cuenta_destino,
             "doc": registro.documento_id, "mal": registro.lectura_mal},
        ).scalar_one()  # fmt: skip
        return 201, PagoManualRegistrado(id=pago, orden_id=orden_id)

    return responder_una_vez(motor, permiso.ctx, idempotencia, efecto)


@router.post(
    "/consorcios/{consorcio_id}/ordenes-pago/{orden_id}/pago-manual/comprobante",
    status_code=201,
    response_model=DocumentoId,
    summary="Cargar el comprobante de un pago manual: nace visible solo para quien lo sube y el"
    " Consejo",
    responses=RESPUESTAS_ESCRITURA,
)
def crear_comprobante_de_pago(
    orden_id: UUID,
    nuevo: ComprobantePagoNuevo,
    lectura: Annotated[Permiso, Depends(requiere(PREPARAR, al_entrar=True, sin_gesto=True))],
    permiso: Annotated[Permiso, Depends(requiere(EJECUTAR, sin_gesto=True))],
    cargar: Annotated[Permiso, Depends(requiere("documentos.cargar"))],
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    """El documento del comprobante, con la orden que respalda (`0063`); el archivo se sube
    después a `…/documentos/{id}/versiones`. Lo crea quien vio la cuenta en el acto de ejecución
    (`no_es_quien_ejecuto`); la base lo vuelve a exigir. Sin gesto (`0073`): no mueve dinero, y la
    atadura ocurre al registrar, que sí lo pide."""
    cargar.verificar(Entidad())
    _ejecutor(lectura, permiso, motor, orden_id)

    def efecto(conn: Connection) -> tuple[int, DocumentoId]:
        doc = _ejecutar(
            conn, permiso,
            "select nucleo.crear_comprobante_de_pago(:o, :titulo, :fecha, :actor, 'api', :sesion)",
            {"o": orden_id, "titulo": nuevo.titulo, "fecha": nuevo.fecha},
        ).scalar_one()  # fmt: skip
        return 201, DocumentoId(id=doc)

    return responder_una_vez(motor, permiso.ctx, idempotencia, efecto)


def _orden_con_pago(
    revision: Permiso, conciliar: Permiso, motor, orden_id: UUID
) -> OrdenConPagoManual:
    with _lectura(revision, motor) as c:
        elementos = _pagos_manuales(c, revision, conciliar, orden_id)
    if not elementos:
        raise ErrorApi(404, CodigoError.NO_ENCONTRADO, NO_ENCONTRADO)
    return elementos[0]


@router.post(
    "/consorcios/{consorcio_id}/ordenes-pago/{orden_id}/pago-manual/{pago_id}/conciliacion",
    response_model=PagoManualCambiado,
    summary="Conciliar un pago manual (otro miembro del Consejo): conforme la deja Pagada",
    responses=RESPUESTAS_ESCRITURA,
)
def conciliar_pago_manual(
    orden_id: UUID,
    pago_id: UUID,
    conciliacion: ConciliacionPagoManual,
    revision: Annotated[Permiso, Depends(requiere(REVISAR, al_entrar=True))],
    permiso: Annotated[Permiso, Depends(requiere(CONCILIAR))],
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    """Lee la orden con la revisión ya verificada y evalúa la separación con quien la preparó y
    con quien registró el pago. La base compara además con quien preparó cada versión, por la
    identidad guardada en cada acto."""
    actual = _orden_con_pago(revision, permiso, motor, orden_id)
    pago = next((p for p in actual.ejecucion.pagos if p.id == pago_id), None)
    if pago is None:
        raise ErrorApi(404, CodigoError.NO_ENCONTRADO, NO_ENCONTRADO)
    permiso.verificar(Entidad(
        preparado_por=actual.orden.preparada_por, ejecutado_por=pago.registrado_por
    ))  # fmt: skip
    _verificar_no_tomo(revision, permiso, motor, orden_id, actual.orden.preparada_por)

    def efecto(conn: Connection) -> tuple[int, PagoManualCambiado]:
        estado = _ejecutar(
            conn, permiso,
            "select nucleo.conciliar_pago_manual(:o, :p, :resultado, :motivo, :actor, 'api',"
            " :sesion)",
            {"o": orden_id, "p": pago_id, "resultado": conciliacion.resultado.value,
             "motivo": conciliacion.motivo},
        ).scalar_one()  # fmt: skip
        return 200, PagoManualCambiado(orden_id=orden_id, estado=estado)

    return responder_una_vez(motor, permiso.ctx, idempotencia, efecto)


@router.post(
    "/consorcios/{consorcio_id}/ordenes-pago/{orden_id}/pago-manual/no-realizado",
    status_code=201,
    response_model=DeclaracionRegistrada,
    summary="Declarar que el pago manual no se hizo: lo confirma otro miembro del Consejo",
    responses=RESPUESTAS_ESCRITURA,
)
def declarar_pago_no_realizado(
    orden_id: UUID,
    declaracion: DeclaracionPagoNoRealizado,
    lectura: Annotated[Permiso, Depends(requiere(PREPARAR, al_entrar=True))],
    permiso: Annotated[Permiso, Depends(requiere(EJECUTAR))],
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    _ejecutor(lectura, permiso, motor, orden_id)

    def efecto(conn: Connection) -> tuple[int, DeclaracionRegistrada]:
        d = _ejecutar(
            conn, permiso,
            "select nucleo.declarar_pago_no_realizado(:o, :version, :motivo, :actor, 'api',"
            " :sesion)",
            {"o": orden_id, "version": declaracion.version, "motivo": declaracion.motivo},
        ).scalar_one()  # fmt: skip
        return 201, DeclaracionRegistrada(id=d, orden_id=orden_id)

    return responder_una_vez(motor, permiso.ctx, idempotencia, efecto)


@router.post(
    "/consorcios/{consorcio_id}/ordenes-pago/{orden_id}/pago-manual/no-realizado/"
    "{declaracion_id}/confirmacion",
    response_model=PagoManualCambiado,
    summary="Confirmar que el pago no se hizo (otro miembro del Consejo): la orden queda Fallida",
    responses=RESPUESTAS_ESCRITURA,
)
def confirmar_pago_no_realizado(
    orden_id: UUID,
    declaracion_id: UUID,
    revision: Annotated[Permiso, Depends(requiere(REVISAR, al_entrar=True))],
    permiso: Annotated[Permiso, Depends(requiere(CONCILIAR))],
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    """Quien declaró ocupa el lugar de quien ejecutó (`no_es_quien_ejecuto`)."""
    actual = _orden_con_pago(revision, permiso, motor, orden_id)
    declarada = next((d for d in actual.ejecucion.declaraciones if d.id == declaracion_id), None)
    if declarada is None:
        raise ErrorApi(404, CodigoError.NO_ENCONTRADO, NO_ENCONTRADO)
    permiso.verificar(Entidad(
        preparado_por=actual.orden.preparada_por, ejecutado_por=declarada.declarado_por
    ))  # fmt: skip
    _verificar_no_tomo(revision, permiso, motor, orden_id, actual.orden.preparada_por)

    def efecto(conn: Connection) -> tuple[int, PagoManualCambiado]:
        _ejecutar(
            conn, permiso,
            "select nucleo.confirmar_pago_no_realizado(:o, :d, :actor, 'api', :sesion)",
            {"o": orden_id, "d": declaracion_id},
        )  # fmt: skip
        return 200, PagoManualCambiado(orden_id=orden_id, estado=EstadoOrdenPago.FALLIDA)

    return responder_una_vez(motor, permiso.ctx, idempotencia, efecto)


# --- «Pagué otra cosa» (`0075`) -----------------------------------------------------------------


def _diferencias(
    c: Connection, permiso: Permiso, orden: UUID
) -> dict[UUID, list[DiferenciaPagoManual]]:
    """Por ejecución, sin la cuenta completa: la base entrega solo sus últimos dígitos. La última de
    cada lista es la vigente; las demás, lo que corrigió (`0082`)."""
    filas = _ejecutar(
        c, permiso,
        "select * from nucleo.diferencias_pago_manual_lectura(:o, :f, :actor, 'api', :sesion)",
        {"o": orden, "f": LEE_COMO[permiso.accion]},
    ).mappings().all()  # fmt: skip
    diferencias = {}
    for fila in filas:
        datos = dict(fila)
        ejecucion = datos.pop("ejecucion_id")
        del datos["orden_id"]
        misma_moneda = datos["moneda"] == datos["moneda_orden"]
        saldo = datos["importe_orden_centavos"] - int(datos["importe_real"] * 100)
        diferencias.setdefault(ejecucion, []).append(DiferenciaPagoManual(
            **datos,
            falta_centavos=saldo if misma_moneda and saldo > 0 else None,
            sobra_centavos=-saldo if misma_moneda and saldo < 0 else None,
            se_puede_dar_por_pagada=datos["tipo"] == "importe" and misma_moneda,
        ))  # fmt: skip
    return diferencias


REGISTRAR_DIFERENCIA = (
    "select nucleo.registrar_diferencia_pago_manual(:o, :version, :huella, :numero, :fecha,"
    " :importe, :moneda, :cuenta, :doc, :explicacion, :actor, 'api', :sesion, :corrige)"
)


ERRORES_CORRECCION = {
    "no_es_quien_registro": (
        403, CodigoError.NO_ES_QUIEN_REGISTRO, "La diferencia la corrige quien la registró.",
    ),
}  # fmt: skip
"""Al corregir una diferencia, `no_es_quien_registro` no habla de un contrato."""


def _datos_diferencia(
    orden_id: UUID, registro: RegistroDiferenciaPago, corrige: UUID | None = None
) -> dict:
    return {
        "o": orden_id, "version": registro.version, "huella": bytes.fromhex(registro.huella),
        "numero": registro.numero_operacion, "fecha": registro.fecha_operacion,
        "importe": registro.importe_real, "moneda": registro.moneda,
        "cuenta": registro.cuenta_real, "doc": registro.documento_id,
        "explicacion": registro.explicacion, "corrige": corrige,
    }  # fmt: skip


@router.post(
    "/consorcios/{consorcio_id}/ordenes-pago/{orden_id}/pago-manual/diferencia",
    status_code=201,
    response_model=DiferenciaRegistrada,
    summary="Registrar lo que salió del banco cuando no es lo aprobado: lo resuelve el Consejo",
    responses=RESPUESTAS_ESCRITURA | {422: {"description": "Sin diferencia o evidencia inválida."}},
)
def registrar_diferencia_pago(
    orden_id: UUID,
    registro: RegistroDiferenciaPago,
    lectura: Annotated[Permiso, Depends(requiere(PREPARAR, al_entrar=True))],
    permiso: Annotated[Permiso, Depends(requiere(EJECUTAR))],
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    """Las mismas facultades, gesto y controles que `registrar_pago_manual`, salvo la igualdad con
    la orden. La orden no queda pagada."""
    _ejecutor(lectura, permiso, motor, orden_id)

    def efecto(conn: Connection) -> tuple[int, DiferenciaRegistrada]:
        diferencia = _ejecutar(
            conn, permiso, REGISTRAR_DIFERENCIA, _datos_diferencia(orden_id, registro)
        ).scalar_one()  # fmt: skip
        return 201, DiferenciaRegistrada(id=diferencia, orden_id=orden_id)

    return responder_una_vez(motor, permiso.ctx, idempotencia, efecto)


@router.post(
    "/consorcios/{consorcio_id}/ordenes-pago/{orden_id}/pago-manual/diferencia/{diferencia_id}/"
    "correccion",
    status_code=201,
    response_model=DiferenciaRegistrada,
    summary="Corregir una diferencia mal cargada (quien la registró, antes de la resolución): fila"
    " nueva",
    responses=RESPUESTAS_ESCRITURA | {422: {"description": "Sin diferencia o evidencia inválida."}},
)
def corregir_diferencia_pago(
    orden_id: UUID,
    diferencia_id: UUID,
    registro: RegistroDiferenciaPago,
    lectura: Annotated[Permiso, Depends(requiere(PREPARAR, al_entrar=True))],
    permiso: Annotated[Permiso, Depends(requiere(EJECUTAR))],
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    """Los mismos controles que al registrar; la base mira quién registró y si ya se resolvió."""
    _ejecutor(lectura, permiso, motor, orden_id)

    def efecto(conn: Connection) -> tuple[int, DiferenciaRegistrada]:
        nueva = _ejecutar(
            conn, permiso, REGISTRAR_DIFERENCIA,
            _datos_diferencia(orden_id, registro, diferencia_id), ERRORES_CORRECCION,
        ).scalar_one()  # fmt: skip
        return 201, DiferenciaRegistrada(id=nueva, orden_id=orden_id)

    return responder_una_vez(motor, permiso.ctx, idempotencia, efecto)


def _verificar_quien_resuelve(
    revision: Permiso, permiso: Permiso, motor, orden_id: UUID, diferencia_id: UUID
) -> None:
    """La separación de `conciliar_pago_manual`: con quien preparó la orden y con quien registró.
    La base compara además con quien vio la cuenta y con quien preparó cada versión."""
    with _lectura(revision, motor) as c:
        ordenes = _ordenes(c, revision, orden_id)
        diferencias = _diferencias(c, revision, orden_id).values()
    diferencia = next((d[-1] for d in diferencias if d[-1].id == diferencia_id), None)
    if not ordenes or diferencia is None:
        raise ErrorApi(404, CodigoError.NO_ENCONTRADO, NO_ENCONTRADO)
    permiso.verificar(Entidad(
        preparado_por=ordenes[0].preparada_por, ejecutado_por=diferencia.registrada_por
    ))  # fmt: skip
    _verificar_no_tomo(revision, permiso, motor, orden_id, ordenes[0].preparada_por)


@router.post(
    "/consorcios/{consorcio_id}/ordenes-pago/{orden_id}/pago-manual/diferencia/{diferencia_id}/"
    "resolucion",
    response_model=PagoManualCambiado,
    summary="Resolver una diferencia (otro miembro del Consejo): dar por pagado lo real o reclamar",
    responses=RESPUESTAS_ESCRITURA,
)
def resolver_diferencia_pago(
    orden_id: UUID,
    diferencia_id: UUID,
    resolucion: ResolucionDiferenciaPago,
    revision: Annotated[Permiso, Depends(requiere(REVISAR, al_entrar=True))],
    permiso: Annotated[Permiso, Depends(requiere(CONCILIAR))],
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    _verificar_quien_resuelve(revision, permiso, motor, orden_id, diferencia_id)

    def efecto(conn: Connection) -> tuple[int, PagoManualCambiado]:
        estado = _ejecutar(
            conn, permiso,
            "select nucleo.resolver_diferencia_pago_manual(:o, :d, :resolucion, :actor, 'api',"
            " :sesion)",
            {"o": orden_id, "d": diferencia_id, "resolucion": resolucion.resolucion.value},
        ).scalar_one()  # fmt: skip
        return 200, PagoManualCambiado(orden_id=orden_id, estado=estado)

    return responder_una_vez(motor, permiso.ctx, idempotencia, efecto)


@router.post(
    "/consorcios/{consorcio_id}/ordenes-pago/{orden_id}/pago-manual/diferencia/{diferencia_id}/"
    "recuperada",
    response_model=PagoManualCambiado,
    summary="Decir que se recuperó la plata reclamada (otro miembro del Consejo): queda Fallida",
    responses=RESPUESTAS_ESCRITURA,
)
def recuperar_diferencia_pago(
    orden_id: UUID,
    diferencia_id: UUID,
    revision: Annotated[Permiso, Depends(requiere(REVISAR, al_entrar=True))],
    permiso: Annotated[Permiso, Depends(requiere(CONCILIAR))],
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    _verificar_quien_resuelve(revision, permiso, motor, orden_id, diferencia_id)

    def efecto(conn: Connection) -> tuple[int, PagoManualCambiado]:
        _ejecutar(
            conn, permiso,
            "select nucleo.recuperar_diferencia_pago_manual(:o, :d, :actor, 'api', :sesion)",
            {"o": orden_id, "d": diferencia_id},
        )  # fmt: skip
        return 200, PagoManualCambiado(orden_id=orden_id, estado=EstadoOrdenPago.FALLIDA)

    return responder_una_vez(motor, permiso.ctx, idempotencia, efecto)


@router.post(
    "/consorcios/{consorcio_id}/ordenes-pago/{orden_id}/pago-manual/diferencia/{diferencia_id}/"
    "cierre",
    response_model=PagoManualCambiado,
    summary="Cerrar un reclamo sin recuperar la plata (otro miembro del Consejo): queda Fallida y"
    " no se imputa en la liquidación",
    responses=RESPUESTAS_ESCRITURA,
)
def cerrar_reclamo(
    orden_id: UUID,
    diferencia_id: UUID,
    cierre: CierreReclamo,
    revision: Annotated[Permiso, Depends(requiere(REVISAR, al_entrar=True))],
    permiso: Annotated[Permiso, Depends(requiere(CONCILIAR))],
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    _verificar_quien_resuelve(revision, permiso, motor, orden_id, diferencia_id)

    def efecto(conn: Connection) -> tuple[int, PagoManualCambiado]:
        _ejecutar(
            conn, permiso,
            "select nucleo.cerrar_reclamo_sin_recuperar(:o, :d, :motivo, :actor, 'api', :sesion)",
            {"o": orden_id, "d": diferencia_id, "motivo": cierre.motivo},
        )  # fmt: skip
        return 200, PagoManualCambiado(orden_id=orden_id, estado=EstadoOrdenPago.FALLIDA)

    return responder_una_vez(motor, permiso.ctx, idempotencia, efecto)


# --- Circuito de pagos: bandeja, ficha y consulta (`0072`) --------------------------------------
# Las lee quien prepara o quien revisa, sin el gesto. Lo que cada persona puede hacer lo decide la
# base con `nucleo.acciones_de_ordenes_lectura`. Ninguna de estas rutas da la cuenta completa.

ESTADOS_DE_FILTRO = {
    FiltroConsultaPagos.TODOS: None,
    FiltroConsultaPagos.PAGADOS: [EstadoOrdenPago.PAGADA, EstadoOrdenPago.CONCILIADA],
    FiltroConsultaPagos.NO_SE_PAGARON: [EstadoOrdenPago.FALLIDA],
    FiltroConsultaPagos.RECHAZADOS: [EstadoOrdenPago.RECHAZADA],
    FiltroConsultaPagos.EN_CURSO: [EstadoOrdenPago.BORRADOR, *EN_CURSO],
}
HECHOS = ("manual", "pago_sin_conciliar", "pago_observado", "declaracion_vigente",
          "declaracion_confirmable", "diferencia_sin_resolver", "reclamo_abierto")  # fmt: skip

Preparar = Annotated[Permiso, Depends(requiere(PREPARAR, sin_gesto=True))]
Revisar = Annotated[Permiso, Depends(requiere(REVISAR, sin_gesto=True))]


def _lector(preparar: Permiso, revisar: Permiso) -> Permiso:
    """Quien prepara lee como quien prepara (ve los borradores); si no, como quien revisa. Se elige
    por lo que alcanza, no por lo que se concede hoy: si falta renovar la sesión, la negación lo
    tiene que decir."""
    permiso = preparar if alcanza(PREPARAR, preparar.ctx.autorizacion) else revisar
    permiso.verificar()
    return permiso


def _acciones(
    c: Connection, lector: Permiso, orden: UUID | None, plazo: timedelta
) -> dict[UUID, circuito_pagos.AccionesOrden]:
    sql = "select * from nucleo.acciones_de_ordenes_lectura(:o, :actor, 'api', :sesion, :plazo)"
    acciones = {}
    for fila in _ejecutar(c, lector, sql, {"o": orden, "plazo": plazo}).mappings():
        datos = dict(fila)
        orden_id = datos.pop("orden_id")
        acciones[orden_id] = circuito_pagos.AccionesOrden(**datos)
    return acciones


def _ejecucion_vigente(
    c: Connection, lector: Permiso, orden: OrdenPago, a: circuito_pagos.AccionesOrden
) -> EjecucionManual | None:
    """La ejecución manual de la versión vigente. `puede_confirmar` es de la última declaración: es
    la que mira la base."""
    ejecuciones, pagos, _, declaraciones, _ = _leer_ejecuciones(c, lector, orden.id)
    e = next((x for x in ejecuciones if x["version"] == orden.version_vigente), None)
    if e is None:
        return None
    declaradas = declaraciones.get(e["id"], [])
    return EjecucionManual(
        id=e["id"], version=e["version"], creada_en=e["creada_en"], resultado=e["resultado"],
        referencia=e["referencia"], pagos=pagos.get(e["id"], []),
        declaraciones=[
            PagoNoRealizado(**d, puede_confirmar=a.puede_confirmar and d is declaradas[-1])
            for d in declaradas
        ],
    )  # fmt: skip


def _el_registrado(
    c: Connection, ver: Permiso, ultimo: PagoManual | DiferenciaPagoManual
) -> ComprobanteActual | None:
    """El archivo del registro, si quien mira lo puede abrir: el mismo control que `…/contenido`.
    A quien preparó la orden, por ejemplo, no se le ofrece lo que después le daría 404."""
    if not ver.decidir(Entidad()).permitido:
        return None
    ids = {"documento_id": ultimo.documento_id, "version_documento_id": ultimo.version_documento_id}
    fila = c.execute(
        text("select 1 from nucleo.version_para_descargar(:documento_id, :version_documento_id)"),
        ids,
    ).first()  # fmt: skip
    return None if fila is None else ComprobanteActual(**ids)


def _comprobantes(
    c: Connection,
    lector: Permiso,
    ver: Permiso,
    orden_id: UUID,
    ultimo: PagoManual | DiferenciaPagoManual | None,
    observado: bool,
) -> tuple[ComprobanteActual | None, ComprobanteSubido | None]:
    """El actual es el del registro vigente; sin él, lo último que subió quien mira y pasó el
    antivirus y, si no subió nada, el del registro observado. Subir de nuevo reemplaza: cuenta el
    orden en que se subieron, no en el que terminó el antivirus. Lo subido, si todavía no pasó."""
    if ultimo is not None and not observado:
        return _el_registrado(c, ver, ultimo), None
    subidas = _ejecutar(
        c, lector,
        "select * from nucleo.comprobante_subido_lectura(:o, :f, :actor, 'api', :sesion)",
        {"o": orden_id, "f": LEE_COMO[lector.accion]},
    ).mappings().all()  # fmt: skip
    disponible = next((s for s in subidas if s["estado"] == EstadoVersion.DISPONIBLE), None)
    if disponible is not None:
        actual = ComprobanteActual(
            documento_id=disponible["documento_id"],
            version_documento_id=disponible["version_documento_id"],
        )
    else:
        actual = None if ultimo is None else _el_registrado(c, ver, ultimo)
    if not subidas or subidas[0] is disponible:
        return actual, None
    return actual, ComprobanteSubido(**subidas[0])


def _misma_cuenta(leido, registrado: PagoManual | DiferenciaPagoManual) -> bool:
    """Si lo leído no contradice la cuenta del registro. Un registro normal es a la cuenta de la
    orden, y una diferencia de importe también. Si la diferencia dice otra cuenta, la base no guarda
    la leída: se comparan los últimos dígitos, y no tiene que ser la de la orden."""
    if leido.cuenta_coincide is None:
        return True
    if isinstance(registrado, PagoManual) or registrado.tipo == TipoDiferencia.IMPORTE:
        return leido.cuenta_coincide
    return (
        not leido.cuenta_coincide
        and leido.cuenta_ultimos_digitos == registrado.cuenta_ultimos_digitos
    )


def _distinto_de_lo_registrado(
    leido, registrado: PagoManual | DiferenciaPagoManual
) -> list[CampoComprobante]:
    importe = (
        registrado.importe_real
        if isinstance(registrado, DiferenciaPagoManual)
        else Decimal(registrado.importe_centavos) / 100
    )
    return lectura_comprobantes.distinto_de_lo_registrado(
        leido, importe, registrado.moneda, registrado.numero_operacion,
        registrado.fecha_operacion, _misma_cuenta(leido, registrado),
    )  # fmt: skip


def _lectura_del_comprobante(
    c: Connection,
    lector: Permiso,
    comprobante: ComprobanteActual | None,
    vigente: VersionOrdenPago,
    registrado: PagoManual | DiferenciaPagoManual | None,
) -> LecturaComprobante | None:
    """Solo del archivo que quien mira puede abrir: a quien no lo ve no se le dice qué dice."""
    if comprobante is None:
        return None
    version = comprobante.version_documento_id
    leido = _ejecutar(
        c, lector,
        "select * from nucleo.lectura_comprobante_lectura(:v, :f, :actor, 'api', :sesion)",
        {"v": version, "f": LEE_COMO[lector.accion]},
    ).one()  # fmt: skip
    if leido.estado == "sin_lectura":
        return None
    con_registro = registrado is not None and registrado.version_documento_id == version
    distinto = _distinto_de_lo_registrado(leido, registrado) if con_registro else []
    comparada = (
        leido.importe is not None and leido.cuenta_coincide is not None and leido.fecha is not None
    )
    return LecturaComprobante(
        version_documento_id=version, estado=EstadoLectura(leido.estado),
        importe_centavos=None if leido.importe is None else int(leido.importe * 100),
        moneda=leido.moneda, cuenta_ultimos_digitos=leido.cuenta_ultimos_digitos,
        cuenta_coincide=leido.cuenta_coincide, titular_destino=leido.titular_destino,
        numero_operacion=leido.numero_operacion, fecha=leido.fecha,
        distinto_de_la_orden=lectura_comprobantes.distinto_de_la_orden(
            leido, vigente.importe_centavos, vigente.moneda
        ),
        titular_distinto=lectura_comprobantes.titular_distinto(leido, vigente.razon_social),
        distinto_de_lo_registrado=distinto,
        coincide_con_lo_registrado=con_registro and comparada and not distinto,
    )  # fmt: skip


def _toma_vigente(
    c: Connection, lector: Permiso, orden: UUID, ejecucion: UUID
) -> TomaVigente | None:
    ultima = next(
        (f for f in reversed(_tomas(c, lector, orden)) if f["ejecucion_id"] == ejecucion), None
    )
    if ultima is None:
        return None
    return TomaVigente(tomado_por_nombre=ultima["tomado_por_nombre"], tomada_en=ultima["tomada_en"])


@router.get(
    "/consorcios/{consorcio_id}/pagos/bandeja",
    response_model=BandejaPagos,
    summary="Lo que quien pide tiene que hacer ahora, agrupado por el próximo paso, sin la cuenta",
    responses=RESPUESTAS_LECTURA,
)
def bandeja_pagos(
    request: Request, preparar: Preparar, revisar: Revisar, motor: MotorDep
) -> BandejaPagos:
    lector = _lector(preparar, revisar)
    dias = request.app.state.config.pagos_dias_bandeja
    with _lectura(lector, _una_foto(motor)) as c:
        ordenes = _ordenes(c, lector, None)
        acciones = _acciones(c, lector, None, _plazo_para_tomar(request))
    return BandejaPagos(elementos=circuito_pagos.bandeja(
        ordenes, acciones, lector.accion == PREPARAR, datetime.now(UTC) - timedelta(days=dias),
    ))  # fmt: skip


@router.get(
    "/consorcios/{consorcio_id}/pagos/consulta",
    response_model=ConsultaPagos,
    summary="Consultar pagos, por proveedor, concepto o número de operación, sin la cuenta",
    responses=RESPUESTAS_LECTURA,
)
def consulta_pagos(
    preparar: Preparar,
    revisar: Revisar,
    motor: MotorDep,
    busqueda: Annotated[
        str | None, Query(max_length=LARGO_MAXIMO_BUSQUEDA_PAGOS, pattern=SIN_CONTROLES)
    ] = None,
    filtro: FiltroConsultaPagos = FiltroConsultaPagos.TODOS,
    limite: Annotated[
        int, Query(ge=1, le=LIMITE_CONSULTA_PAGOS_MAXIMO)
    ] = LIMITE_CONSULTA_PAGOS_POR_DEFECTO,
    saltar: Annotated[int, Query(ge=0, le=MAXIMO_SALTAR_CONSULTA_PAGOS)] = 0,
) -> ConsultaPagos:
    lector = _lector(preparar, revisar)
    estados = ESTADOS_DE_FILTRO[filtro]
    with _lectura(lector, motor) as c:
        # Uno de más, para saber si hay otra página.
        filas = _ejecutar(
            c, lector,
            "select * from nucleo.pagos_consulta_lectura(:b, :e, :l, :s, :f, :actor, 'api',"
            " :sesion)",
            {"b": busqueda, "e": None if estados is None else [e.value for e in estados],
             "l": limite + 1, "s": saltar, "f": LEE_COMO[lector.accion]},
        ).mappings().all()  # fmt: skip
    elementos = []
    for f in filas[:limite]:
        hechos = circuito_pagos.HechosPago(**{k: f[k] for k in HECHOS})
        estado = EstadoOrdenPago(f["estado"])
        elementos.append(ElementoConsultaPagos(
            orden_id=f["orden_id"], proveedor=f["razon_social"], concepto=f["concepto"],
            importe_centavos=f["importe_centavos"], moneda=f["moneda"], estado=estado,
            importe_pagado_centavos=f["importe_pagado_centavos"],
            situacion=circuito_pagos.situacion_de(estado, hechos),
            numero_operacion=f["numero_operacion"], fecha_operacion=f["fecha_operacion"],
            actualizada_en=f["actualizada_en"],
        ))  # fmt: skip
    return ConsultaPagos(
        elementos=elementos, siguiente=saltar + limite if len(filas) > limite else None
    )


@router.get(
    "/consorcios/{consorcio_id}/pagos/{orden_id}",
    response_model=FichaPago,
    summary="La ficha de una orden en cualquier estado: en qué anda y qué puede hacer quien pide,"
    " sin la cuenta",
    responses=RESPUESTAS_LECTURA | {404: {"description": "No existe en este consorcio."}},
)
def ficha_pago(
    request: Request,
    orden_id: UUID,
    preparar: Preparar,
    revisar: Revisar,
    ver: Annotated[Permiso, Depends(requiere("documentos.ver"))],
    motor: MotorDep,
) -> FichaPago:
    lector = _lector(preparar, revisar)
    prepara = lector.accion == PREPARAR
    with _lectura(lector, _una_foto(motor)) as c:
        detalle = _leer_orden_con_versiones(c, lector, orden_id)
        orden = detalle.orden
        a = _acciones(c, lector, orden_id, _plazo_para_tomar(request)).get(
            orden_id, circuito_pagos.SIN_ACCIONES
        )
        ejecucion = _ejecucion_vigente(c, lector, orden, a)
        ultimo = ejecucion.pagos[-1] if ejecucion is not None and ejecucion.pagos else None
        de_la_ejecucion = []
        if ejecucion is not None:
            de_la_ejecucion = _diferencias(c, lector, orden_id).get(ejecucion.id, [])
        diferencia = de_la_ejecucion[-1] if de_la_ejecucion else None
        comprobante, subido = _comprobantes(
            c, lector, ver, orden_id, diferencia or ultimo, a.pago_observado and diferencia is None
        )
        vigente = next(x for x in detalle.versiones if x.numero == orden.version_vigente)
        lectura = _lectura_del_comprobante(c, lector, comprobante, vigente, diferencia or ultimo)
        toma = None if ejecucion is None else _toma_vigente(c, lector, orden_id, ejecucion.id)
    situacion = circuito_pagos.situacion_de(orden.estado, a)
    paso = circuito_pagos.paso_de(situacion, a, prepara)
    observacion = None
    if a.pago_observado and diferencia is None:
        observacion = ObservacionVigente(
            pago_id=ultimo.id, motivo=ultimo.motivo_conciliacion,
            observado_por=ultimo.conciliado_por, observado_en=ultimo.conciliado_en,
        )  # fmt: skip
    return FichaPago(
        orden=orden, versiones=detalle.versiones, situacion=situacion, paso=paso,
        le_toca_a=None if paso is None else circuito_pagos.le_toca_a(paso, a),
        puede_enviar=prepara and orden.estado == EstadoOrdenPago.BORRADOR,
        puede_aprobar=a.puede_aprobar, puede_rechazar=a.puede_rechazar,
        puede_renovar=a.puede_renovar,
        # Con una declaración vigente, un registro observado o la cuenta ya vista no se ofrece.
        puede_pagar=circuito_pagos.ofrece_pagar(situacion, a),
        puede_reabrir_cuenta=circuito_pagos.reabre_cuenta(situacion, a),
        puede_tomar=a.puede_tomar, tomar_desde=a.tomar_desde,
        puede_modificar=prepara and orden.estado in circuito_pagos.MODIFICABLES,
        puede_subir_comprobante=a.puede_subir_comprobante, puede_registrar=a.puede_registrar,
        puede_declarar=a.puede_declarar,
        puede_corregir=a.puede_corregir, puede_conciliar=a.puede_conciliar,
        puede_confirmar=a.puede_confirmar,
        puede_resolver_diferencia=a.puede_resolver_diferencia and a.diferencia_sin_resolver,
        puede_recuperar=a.puede_resolver_diferencia and a.reclamo_abierto,
        puede_corregir_diferencia=a.puede_corregir_diferencia,
        puede_preparar_faltante=paso == PasoPago.PREPARAR_FALTANTE,
        ejecucion=ejecucion, comprobante_actual=comprobante,
        comprobante_subido=subido, lectura_comprobante=lectura, observacion_vigente=observacion,
        declaracion_vigente=ejecucion.declaraciones[-1] if a.declaracion_vigente else None,
        diferencia_vigente=diferencia, diferencias_anteriores=de_la_ejecucion[:-1],
        toma_vigente=toma,
    )  # fmt: skip


# --- Quien vio la cuenta y ya no ejecuta (`0067`, P3 179) ---------------------------------------
# Sin `egresos.ejecutar_pago` ya no tiene ámbito en el consorcio (`require_scope` da 404): estas
# rutas parten solo de la sesión y la base exige todo (R10, identidad verificada, que haya visto la
# cuenta de la ejecución abierta). A cualquier otra persona le responde 404.

ERRORES_SIN_AMBITO = {
    "modo_no_admitido": (
        409, CodigoError.ORDEN_NO_ADMITE, "El modo vigente del consorcio no admite egresos.",
    ),
}  # fmt: skip


@router.get(
    "/pagos-por-aclarar",
    response_model=ListaPagosPorAclarar,
    summary="Los pagos manuales que esperan que digas si transferiste: viste la cuenta y ya no"
    " ejecutás",
)
def pagos_por_aclarar(identidad: IdentidadDep, motor: MotorDep) -> ListaPagosPorAclarar:
    with transaccion(motor, persona_id=identidad.persona_id) as c:
        filas = c.execute(
            text("select * from nucleo.pagos_por_aclarar(:actor, 'api', :sesion)"),
            {"actor": identidad.persona_id, "sesion": identidad.sesion_id},
        ).mappings()  # fmt: skip
        return ListaPagosPorAclarar(elementos=[PagoPorAclarar(**f) for f in filas])


def _sin_ambito(motor, identidad, consorcio_id: UUID) -> Contexto:
    """El contexto de quien ya no tiene ámbito en el consorcio: solo la persona, la sesión y el
    consorcio, sin vínculos ni mandatos (no decide nada con él: lo decide la base). 404 si el
    consorcio no existe, como `require_scope`."""
    with transaccion(motor, persona_id=identidad.persona_id, consorcio_id=consorcio_id) as c:
        hoy = c.execute(
            text("select nucleo.hoy_del_consorcio(id) from nucleo.consorcio"
                 " where id = :c and eliminado_en is null"),
            {"c": consorcio_id},
        ).scalar_one_or_none()  # fmt: skip
    if hoy is None:
        raise ErrorApi(404, CodigoError.NO_ENCONTRADO, NO_ENCONTRADO)
    return Contexto(persona_id=identidad.persona_id, consorcio_id=consorcio_id, hoy=hoy,
                    vinculos=(), modo=None, sesion_id=identidad.sesion_id)  # fmt: skip


def ejecutar_sin_ambito(conn: Connection, ctx: Contexto, sql: str, parametros: dict):
    """Como `_ejecutar`, para estas rutas: `42501` es 404 (a quien no vio la cuenta no se le dice
    si la orden existe)."""
    try:
        return conn.execute(
            text(sql), parametros | {"actor": ctx.persona_id, "sesion": ctx.sesion_id}
        )
    except DBAPIError as e:
        if isinstance(e.orig, InsufficientPrivilege):
            raise ErrorApi(404, CodigoError.NO_ENCONTRADO, NO_ENCONTRADO) from e
        motivo = e.orig.diag.message_hint
        errores = ERRORES | ERRORES_SIN_AMBITO
        if motivo in errores:
            raise ErrorApi(*errores[motivo]) from e
        raise


@router.post(
    "/consorcios/{consorcio_id}/pagos-por-aclarar/{orden_id}/no-realizado",
    status_code=201,
    response_model=DeclaracionRegistrada,
    summary="Declarar que no transferiste, sin ejecutar ya pagos en el consorcio: lo confirma otro"
    " miembro del Consejo",
    responses=RESPUESTAS_ESCRITURA,
)
def declarar_pago_no_realizado_sin_facultad(
    consorcio_id: UUID,
    orden_id: UUID,
    declaracion: DeclaracionPagoNoRealizado,
    identidad: IdentidadDep,
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    ctx = _sin_ambito(motor, identidad, consorcio_id)

    def efecto(conn: Connection) -> tuple[int, DeclaracionRegistrada]:
        d = ejecutar_sin_ambito(
            conn, ctx,
            "select nucleo.declarar_pago_no_realizado(:o, :version, :motivo, :actor, 'api',"
            " :sesion)",
            {"o": orden_id, "version": declaracion.version, "motivo": declaracion.motivo},
        ).scalar_one()  # fmt: skip
        return 201, DeclaracionRegistrada(id=d, orden_id=orden_id)

    return responder_una_vez(motor, ctx, idempotencia, efecto)


@router.post(
    "/consorcios/{consorcio_id}/pagos-por-aclarar/{orden_id}/comprobante",
    status_code=201,
    response_model=DocumentoId,
    summary="Cargar el comprobante de lo que transferiste, sin ejecutar ya pagos en el consorcio",
    responses=RESPUESTAS_ESCRITURA,
)
def crear_comprobante_sin_facultad(
    consorcio_id: UUID,
    orden_id: UUID,
    nuevo: ComprobantePagoNuevo,
    identidad: IdentidadDep,
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    """El documento (`0068`, P3 184); el archivo va a
    `…/pagos-por-aclarar/{orden}/comprobante/{documento}/versiones`."""
    ctx = _sin_ambito(motor, identidad, consorcio_id)

    def efecto(conn: Connection) -> tuple[int, DocumentoId]:
        doc = ejecutar_sin_ambito(
            conn, ctx,
            "select nucleo.crear_comprobante_de_pago(:o, :titulo, :fecha, :actor, 'api', :sesion)",
            {"o": orden_id, "titulo": nuevo.titulo, "fecha": nuevo.fecha},
        ).scalar_one()  # fmt: skip
        return 201, DocumentoId(id=doc)

    return responder_una_vez(motor, ctx, idempotencia, efecto)


@router.post(
    "/consorcios/{consorcio_id}/pagos-por-aclarar/{orden_id}/comprobante/{documento_id}/versiones",
    status_code=201,
    response_model=VersionCargada,
    summary="Subir el archivo del comprobante, sin ejecutar ya pagos en el consorcio: entra en"
    " cuarentena hasta que se analiza",
    openapi_extra=SUBIDA_OPENAPI,
    responses=RESPUESTAS_SUBIDA,
)
async def subir_comprobante_sin_facultad(
    consorcio_id: UUID,
    orden_id: UUID,
    documento_id: UUID,
    request: Request,
    identidad: IdentidadDep,
    clave: ClaveIdempotenciaDep,
    motor: MotorDep,
) -> Response:
    ctx = await anyio.to_thread.run_sync(lambda: _sin_ambito(motor, identidad, consorcio_id))

    def nueva(conn: Connection, tipo: str, tamano: int, suma: str):
        return ejecutar_sin_ambito(
            conn, ctx,
            "select id, clave_objeto from nucleo.nueva_version_de_comprobante(:o, :d, :t, :n,"
            " :s, :actor, 'api', :sesion)",
            {"o": orden_id, "d": documento_id, "t": tipo, "n": tamano, "s": suma},
        ).one()  # fmt: skip

    return await subir_archivo(request, ctx, clave, motor, nueva)


@router.post(
    "/consorcios/{consorcio_id}/pagos-por-aclarar/{orden_id}/diferencia",
    status_code=201,
    response_model=DiferenciaRegistrada,
    summary="Registrar lo que salió del banco, sin ejecutar ya pagos en el consorcio: lo resuelve"
    " el"
    " Consejo",
    responses=RESPUESTAS_ESCRITURA | {422: {"description": "Sin diferencia o evidencia inválida."}},
)
def registrar_diferencia_sin_facultad(
    consorcio_id: UUID,
    orden_id: UUID,
    registro: RegistroDiferenciaPago,
    identidad: IdentidadDep,
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    ctx = _sin_ambito(motor, identidad, consorcio_id)

    def efecto(conn: Connection) -> tuple[int, DiferenciaRegistrada]:
        diferencia = ejecutar_sin_ambito(
            conn, ctx, REGISTRAR_DIFERENCIA, _datos_diferencia(orden_id, registro)
        ).scalar_one()  # fmt: skip
        return 201, DiferenciaRegistrada(id=diferencia, orden_id=orden_id)

    return responder_una_vez(motor, ctx, idempotencia, efecto)


@router.post(
    "/consorcios/{consorcio_id}/pagos-por-aclarar/{orden_id}/pago",
    status_code=201,
    response_model=PagoManualRegistrado,
    summary="Registrar lo que transferiste, sin ejecutar ya pagos en el consorcio: lo concilia"
    " otro miembro del Consejo",
    responses=RESPUESTAS_ESCRITURA,
)
def registrar_pago_sin_facultad(
    consorcio_id: UUID,
    orden_id: UUID,
    registro: RegistroPagoManual,
    identidad: IdentidadDep,
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    ctx = _sin_ambito(motor, identidad, consorcio_id)

    def efecto(conn: Connection) -> tuple[int, PagoManualRegistrado]:
        pago = ejecutar_sin_ambito(
            conn, ctx,
            "select nucleo.registrar_pago_manual(:o, :version, :huella, :numero, :fecha, :importe,"
            " :moneda, :cuenta, :doc, :actor, 'api', :sesion)",
            {"o": orden_id, "version": registro.version,
             "huella": bytes.fromhex(registro.huella), "numero": registro.numero_operacion,
             "fecha": registro.fecha_operacion, "importe": registro.importe_centavos,
             "moneda": registro.moneda, "cuenta": registro.cuenta_destino,
             "doc": registro.documento_id},
        ).scalar_one()  # fmt: skip
        return 201, PagoManualRegistrado(id=pago, orden_id=orden_id)

    return responder_una_vez(motor, ctx, idempotencia, efecto)
