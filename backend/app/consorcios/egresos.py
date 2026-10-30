"""Órdenes de pago, proveedores y contratos (H3; documento 08 §6; migraciones `0045` a `0050`).

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

Toda acción es económica (R10): sesión de menos de 12 horas, segundo factor de menos de 10
minutos e identidad humana verificada. Las funciones de la base vuelven a exigir todo eso con la
sesión de la petición. Si la base niega lo que el evaluador concedió, es una discrepancia (500),
nunca una lista vacía ni un 403. Toda escritura lleva `Idempotency-Key`.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated
from uuid import UUID

from cem_contracts.egresos import (
    VIGENCIA_APROBACION,
    Aprobacion,
    AprobacionRegistrada,
    Confirmacion,
    Contrato,
    ContratoCambiado,
    ContratoNuevo,
    DatosOrdenPago,
    DatosProveedor,
    Emision,
    EnvioARevision,
    EstadoOrdenPago,
    ListaContratos,
    ListaOrdenesPago,
    ListaProveedores,
    OrdenPago,
    OrdenPagoCambiada,
    OrdenPagoConVersiones,
    OrdenPagoNueva,
    OrdenPagoVersionNueva,
    Proveedor,
    ProveedorConVersiones,
    ProveedorCreado,
    ProveedorNuevo,
    ProveedorVersionNueva,
    Rechazo,
    Renovacion,
    VersionOrdenPago,
    VersionProveedor,
)
from cem_contracts.errores import CodigoError
from fastapi import APIRouter, Depends, Response
from psycopg.errors import InsufficientPrivilege
from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError

from app.autorizacion.evaluador import Entidad
from app.autorizacion.requiere import NO_ENCONTRADO, RENOVAR, Permiso, requiere
from app.consorcios.padron import Discrepancia
from app.contexto import MotorDep
from app.db import transaccion
from app.errores import ErrorApi, sin_cache
from app.plataforma.idempotencia import IdempotenciaDep, responder_una_vez

router = APIRouter(prefix="/api/v1", tags=["egresos"], dependencies=[Depends(sin_cache)])

PREPARAR = "egresos.preparar_orden"
REVISAR = "egresos.revisar_orden"
APROBAR = "egresos.aprobar_orden"
EJECUTAR = "egresos.ejecutar_pago"
RESPALDADAS = {
    EstadoOrdenPago.APROBADA, EstadoOrdenPago.EN_EJECUCION, EstadoOrdenPago.EN_VERIFICACION,
    EstadoOrdenPago.PAGADA, EstadoOrdenPago.FALLIDA, EstadoOrdenPago.CONCILIADA,
}  # fmt: skip
"""Las órdenes que ya pasaron una aprobación completa y, después, su emisión (también la que
falló): el reintento de la emisión con la misma clave devuelve la respuesta guardada."""
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
    "requiere_segundo_factor_reciente": (403, *RENOVAR["requiere_segundo_factor_reciente"]),
    "requiere_sesion_reciente": (403, *RENOVAR["requiere_sesion_reciente"]),
    "sesion_invalida": (403, *RENOVAR["requiere_sesion_reciente"]),
}  # fmt: skip
"""Motivos de las funciones de `0045` a `0050`. Los que el contrato ya impide (razón social,
cuenta) y los que fija esta ruta (origen, modo) no están: si llegan, son un defecto (500). Los de
R10 pueden llegar si la sesión venció entre el evaluador y la base."""

RESPUESTAS_LECTURA = {
    403: {"description": "Sin la facultad, sin el modo, o falta renovar la sesión (R10)."}
}
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


def _evaluaciones(c: Connection, permiso: Permiso, orden: UUID | None) -> dict:
    """La franja de cada versión (`0050`), por `(orden, número)`. Se lee **después** de las
    órdenes o las versiones: las versiones solo se agregan, así que toda versión ya leída está."""
    sql = "select * from nucleo.orden_pago_evaluaciones_lectura(:o, :f, :actor, 'api', :sesion)"
    filas = _ejecutar(c, permiso, sql, {"o": orden, "f": LEE_COMO[permiso.accion]})
    return {(f["orden_id"], f["numero"]): f for f in filas.mappings()}


def _ordenes(c: Connection, permiso: Permiso, orden: UUID | None) -> list[OrdenPago]:
    sql = "select * from nucleo.ordenes_pago_lectura(:o, :f, :actor, 'api', :sesion)"
    filas = list(_ejecutar(c, permiso, sql, {"o": orden, "f": LEE_COMO[permiso.accion]}).mappings())
    evaluaciones = _evaluaciones(c, permiso, orden)
    ordenes = []
    for f in filas:
        ev = evaluaciones[(f["id"], f["version_vigente"])]
        aprobada_en = ev["aprobada_en"]
        aprobada = f["estado"] == "aprobada" and aprobada_en is not None
        ordenes.append(OrdenPago(
            **f, franja=ev["franja"], alerta_fraccionamiento=ev["alerta_fraccionamiento"],
            aprobada_en=aprobada_en,
            aprobacion_vence_en=aprobada_en + VIGENCIA_APROBACION if aprobada else None,
        ))  # fmt: skip
    return ordenes


def _orden_con_versiones(permiso: Permiso, motor, orden_id: UUID) -> OrdenPagoConVersiones:
    with _lectura(permiso, motor) as c:
        ordenes = _ordenes(c, permiso, orden_id)
        if not ordenes:
            raise ErrorApi(404, CodigoError.NO_ENCONTRADO, NO_ENCONTRADO)
        sql = "select * from nucleo.orden_pago_versiones_lectura(:o, :f, :actor, 'api', :sesion)"
        filas = list(
            _ejecutar(c, permiso, sql, {"o": orden_id, "f": LEE_COMO[permiso.accion]}).mappings()
        )
        evaluaciones = _evaluaciones(c, permiso, orden_id)
        versiones = []
        for f in filas:
            ev = evaluaciones[(orden_id, f["numero"])]
            versiones.append(VersionOrdenPago(
                **f, franja=ev["franja"], alerta_fraccionamiento=ev["alerta_fraccionamiento"],
                umbral_importe_centavos=ev["umbral_importe_centavos"],
                umbral_moneda=ev["umbral_moneda"], contrato_id=ev["contrato_id"],
            ))  # fmt: skip
    return OrdenPagoConVersiones(orden=ordenes[0], versiones=versiones)


# --- Proveedores -------------------------------------------------------------------------------


def _proveedores(c: Connection, permiso: Permiso, proveedor: UUID | None) -> list[Proveedor]:
    sql = "select * from nucleo.proveedores_lectura(:p, :actor, 'api', :sesion)"
    return [Proveedor(**f) for f in _ejecutar(c, permiso, sql, {"p": proveedor}).mappings()]


@router.get(
    "/consorcios/{consorcio_id}/proveedores",
    response_model=ListaProveedores,
    summary="Proveedores del consorcio, cada uno en su versión vigente",
    responses=RESPUESTAS_LECTURA,
)
def listar_proveedores(
    permiso: Annotated[Permiso, Depends(requiere(PREPARAR, al_entrar=True))], motor: MotorDep
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
    permiso: Annotated[Permiso, Depends(requiere(PREPARAR, al_entrar=True))],
    motor: MotorDep,
) -> ProveedorConVersiones:
    with _lectura(permiso, motor) as c:
        proveedores = _proveedores(c, permiso, proveedor_id)
        if not proveedores:
            raise ErrorApi(404, CodigoError.NO_ENCONTRADO, NO_ENCONTRADO)
        sql = "select * from nucleo.proveedor_versiones_lectura(:p, :actor, 'api', :sesion)"
        filas = _ejecutar(c, permiso, sql, {"p": proveedor_id}).mappings()
        versiones = [VersionProveedor(**f) for f in filas]
    return ProveedorConVersiones(proveedor=proveedores[0], versiones=versiones)


def _datos_proveedor(d: DatosProveedor) -> dict:
    return {
        "razon": d.razon_social,
        "rubro": d.rubro,
        "contacto": d.contacto,
        "doc": d.documento_id,
        "cuenta": d.cuenta_destino,
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
            " :actor, 'api', :sesion)",
            {"cuit": nuevo.cuit, **_datos_proveedor(nuevo)},
        ).scalar_one()  # fmt: skip
        return 201, ProveedorCreado(id=nuevo_id, version=1)

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
        version = _ejecutar(
            conn, permiso,
            "select nucleo.nueva_version_proveedor(:p, :esperada, :razon, :rubro, :contacto,"
            " cast(:estado as nucleo.estado_proveedor), :doc, :cuenta, :actor, 'api', :sesion)",
            {"p": proveedor_id, "esperada": datos.version_esperada, "estado": datos.estado.value,
             **_datos_proveedor(datos)},
            # El proveedor viene en la ruta: si no existe es 404, como al verlo.
            {"proveedor_inexistente": ERRORES["no_encontrado"]},
        ).scalar_one()  # fmt: skip
        return 200, ProveedorCreado(id=proveedor_id, version=version)

    return responder_una_vez(motor, permiso.ctx, idempotencia, efecto)


# --- Órdenes de pago: preparación --------------------------------------------------------------


@router.get(
    "/consorcios/{consorcio_id}/ordenes-pago",
    response_model=ListaOrdenesPago,
    summary="Órdenes de pago del consorcio, borradores incluidos (preparación)",
    responses=RESPUESTAS_LECTURA,
)
def listar_ordenes(
    permiso: Annotated[Permiso, Depends(requiere(PREPARAR, al_entrar=True))], motor: MotorDep
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
    permiso: Annotated[Permiso, Depends(requiere(REVISAR, al_entrar=True))], motor: MotorDep
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
    permiso: Annotated[Permiso, Depends(requiere(PREPARAR, al_entrar=True))],
    motor: MotorDep,
) -> OrdenPagoConVersiones:
    return _orden_con_versiones(permiso, motor, orden_id)


@router.get(
    "/consorcios/{consorcio_id}/ordenes-pago/{orden_id}/revision",
    response_model=OrdenPagoConVersiones,
    summary="Una orden enviada a revisión, con la huella de cada versión para aprobarla",
    responses=RESPUESTAS_LECTURA | {404: {"description": "No existe o es un borrador."}},
)
def ver_orden_revision(
    orden_id: UUID,
    permiso: Annotated[Permiso, Depends(requiere(REVISAR, al_entrar=True))],
    motor: MotorDep,
) -> OrdenPagoConVersiones:
    return _orden_con_versiones(permiso, motor, orden_id)


def _datos_orden(d: DatosOrdenPago) -> dict:
    return {
        "importe": d.importe_centavos,
        "moneda": d.moneda,
        "concepto": d.concepto,
        "proveedor": d.proveedor_id,
        "comprobante": d.comprobante,
        "reserva": d.usa_fondo_reserva,
        "contrato": d.contrato_id,
    }


@router.post(
    "/consorcios/{consorcio_id}/ordenes-pago",
    status_code=201,
    response_model=OrdenPagoCambiada,
    summary="Preparar una orden de pago (queda en borrador, versión 1)",
    responses=RESPUESTAS_ESCRITURA | {422: {"description": "El proveedor no existe."}},
)
def preparar_orden(
    nueva: OrdenPagoNueva,
    permiso: Annotated[Permiso, Depends(requiere(PREPARAR, al_entrar=True))],
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    def efecto(conn: Connection) -> tuple[int, OrdenPagoCambiada]:
        orden_id = _ejecutar(
            conn, permiso,
            "select nucleo.preparar_orden_pago(:importe, :moneda, :concepto, :proveedor,"
            " :comprobante, :reserva, :contrato, :actor, 'api', :sesion)",
            _datos_orden(nueva),
        ).scalar_one()  # fmt: skip
        return 201, OrdenPagoCambiada(id=orden_id, version=1)

    return responder_una_vez(motor, permiso.ctx, idempotencia, efecto)


@router.post(
    "/consorcios/{consorcio_id}/ordenes-pago/{orden_id}/versiones",
    response_model=OrdenPagoCambiada,
    summary="Modificar una orden: crea una versión nueva y la vuelve a revisión si hacía falta",
    responses=RESPUESTAS_ESCRITURA | {422: {"description": "El proveedor no existe."}},
)
def modificar_orden(
    orden_id: UUID,
    datos: OrdenPagoVersionNueva,
    permiso: Annotated[Permiso, Depends(requiere(PREPARAR, al_entrar=True))],
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    def efecto(conn: Connection) -> tuple[int, OrdenPagoCambiada]:
        version = _ejecutar(
            conn, permiso,
            "select nucleo.modificar_orden_pago(:o, :esperada, :importe, :moneda, :concepto,"
            " :proveedor, :comprobante, :reserva, :contrato, :actor, 'api', :sesion)",
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
    permiso: Annotated[Permiso, Depends(requiere(PREPARAR, al_entrar=True))],
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
    permiso: Annotated[Permiso, Depends(requiere(PREPARAR, al_entrar=True))],
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


def _contratos(c: Connection, permiso: Permiso, contrato: UUID | None) -> list[Contrato]:
    sql = "select * from nucleo.contratos_proveedor_lectura(:k, :f, :actor, 'api', :sesion)"
    filas = _ejecutar(c, permiso, sql, {"k": contrato, "f": LEE_COMO[permiso.accion]})
    return [Contrato(**f) for f in filas.mappings()]


@router.get(
    "/consorcios/{consorcio_id}/contratos",
    response_model=ListaContratos,
    summary="Contratos y abonos que respaldan lo rutinario (preparación)",
    responses=RESPUESTAS_LECTURA,
)
def listar_contratos(
    permiso: Annotated[Permiso, Depends(requiere(PREPARAR, al_entrar=True))], motor: MotorDep
) -> ListaContratos:
    with _lectura(permiso, motor) as c:
        return ListaContratos(elementos=_contratos(c, permiso, None))


@router.get(
    "/consorcios/{consorcio_id}/contratos/revision",
    response_model=ListaContratos,
    summary="Contratos y abonos, para confirmarlos (quien aprueba)",
    responses=RESPUESTAS_LECTURA,
)
def listar_contratos_revision(
    permiso: Annotated[Permiso, Depends(requiere(REVISAR, al_entrar=True))], motor: MotorDep
) -> ListaContratos:
    with _lectura(permiso, motor) as c:
        return ListaContratos(elementos=_contratos(c, permiso, None))


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
            "select nucleo.confirmar_contrato_proveedor(:k, :huella, :actor, 'api', :sesion)",
            {"k": contrato_id, "huella": bytes.fromhex(confirmacion.huella)},
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
