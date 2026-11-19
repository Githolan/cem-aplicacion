"""Panel del Consejo (H4a, corte 1; RF48), en solo lectura, y solicitud formal de documentación
(RF49, CU12, PA36; `0108`).

Diseño: `doc/diseno/H4a_1_Panel_del_Consejo.md` y `doc/diseno/RF49_Solicitud_Formal.md` del
repositorio CEM. La política (`fiscalizacion.ver_panel`) decide al entrar; la base repite la
barrera en cada lectura (`0094`). Las lecturas del panel van en una sola transacción `REPEATABLE
READ`: un pago confirmado en el medio no puede dejar el gráfico de un momento y la tabla de otro.

**Solicitudes formales.** Cada ruta evalúa su política con la solicitud a la vista (las firmas
que ya tiene, para PA36 y A14) y la base vuelve a exigir todo: la facultad, el modo, la sesión, la
identidad humana y el estado. Si la base niega lo que el evaluador concedió, es una discrepancia
(500). Toda escritura lleva `Idempotency-Key`. La emisión a un destinatario con cuenta encola en
la misma transacción un correo sin el contenido de la solicitud (P3).
"""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

import anyio
from cem_contracts.documentos import EstadoVersion, Titulo, VersionCargada
from cem_contracts.errores import CodigoError
from cem_contracts.fiscalizacion import (
    PLAZO_MAXIMO_DIAS,
    PLAZO_MINIMO_DIAS,
    PLAZO_POR_DEFECTO_DIAS,
    AccionSolicitud,
    AdjuntoSubido,
    ComprobanteFaltante,
    ComprobanteNuevo,
    EnvioNuevo,
    EnvioSolicitud,
    EstadoSolicitudFormal,
    FirmaSolicitud,
    GastoPagado,
    ListaSolicitudesFormales,
    OrdenesPorEstado,
    PanelFiscalizacion,
    ParaAdjunto,
    RespuestaNueva,
    RespuestaRecibida,
    RespuestaSolicitud,
    RetiroRegistrado,
    RetiroSolicitud,
    SolicitudFormal,
    SolicitudFormalNueva,
)
from fastapi import APIRouter, Depends, Query, Request, Response
from psycopg.errors import InsufficientPrivilege
from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError

from app.autorizacion.evaluador import Entidad, Motivo
from app.autorizacion.requiere import (
    NO_ENCONTRADO,
    SIN_ADMINISTRADOR,
    VOLVER_A_ENTRAR,
    YA_PARTICIPO,
    Permiso,
    requiere,
)
from app.consorcios.documentos import RESPUESTAS_SUBIDA, SUBIDA_OPENAPI, subir_archivo
from app.consorcios.padron import Discrepancia
from app.contexto import MotorDep
from app.db import transaccion
from app.errores import ErrorApi, detalle_de_campo, sin_cache
from app.identidad.invitaciones import origen_publico
from app.plataforma import correo
from app.plataforma.idempotencia import (
    ClaveIdempotenciaDep,
    IdempotenciaDep,
    responder_una_vez,
)

router = APIRouter(prefix="/api/v1", tags=["consorcios"], dependencies=[Depends(sin_cache)])

VER_PANEL = "fiscalizacion.ver_panel"
PREPARAR = "solicitud_formal.preparar"
FIRMAR = "solicitud_formal.firmar"
RETIRAR = "solicitud_formal.retirar"
REGISTRAR_ENVIO = "solicitud_formal.registrar_envio"
RESPONDER = "solicitud_formal.responder"
REGISTRAR_RESPUESTA = "solicitud_formal.registrar_respuesta"
RUTA_SOLICITUDES = "/solicitudes"
"""La pantalla de la aplicación (`frontend/src/fiscalizacion/Solicitudes.tsx`)."""


@router.get(
    "/consorcios/{consorcio_id}/panel",
    response_model=PanelFiscalizacion,
    summary="Panel del Consejo: órdenes por estado, gasto pagado, comprobantes faltantes y"
    " solicitudes formales abiertas",
    responses={403: {"description": "Sin `fiscalizacion.ver_panel` en el consorcio."}},
)
def ver_panel(
    permiso: Annotated[Permiso, Depends(requiere(VER_PANEL, al_entrar=True))], motor: MotorDep
) -> PanelFiscalizacion:
    ctx = permiso.ctx
    foto = motor.execution_options(isolation_level="REPEATABLE READ")
    try:
        with transaccion(foto, persona_id=ctx.persona_id, consorcio_id=ctx.consorcio_id) as c:

            def leer(funcion: str) -> list:
                return list(c.execute(text(f"select * from nucleo.{funcion}()")).mappings())

            estados = leer("panel_ordenes_por_estado")
            gasto = leer("panel_gasto_pagado")
            faltantes = leer("panel_comprobantes_faltantes")
            abiertas = c.execute(text("select nucleo.panel_solicitudes_abiertas()")).scalar_one()
    except DBAPIError as e:
        if isinstance(e.orig, InsufficientPrivilege):
            raise Discrepancia(f"la base niega {VER_PANEL} y el evaluador lo concede") from e
        raise
    return PanelFiscalizacion(
        ordenes_por_estado=[OrdenesPorEstado(**f) for f in estados],
        gasto_pagado=[GastoPagado(**f) for f in gasto],
        comprobantes_faltantes=[ComprobanteFaltante(**f) for f in faltantes],
        solicitudes_abiertas=abiertas,
    )


# --- Solicitud formal de documentación (RF49; `0108`) -------------------------------------------

NO_ADMITE = "La solicitud ya no admite eso en su estado: hay que volver a abrirla para ver cómo"
ERRORES = {
    "no_encontrado": (404, CodigoError.NO_ENCONTRADO, NO_ENCONTRADO),
    "solicitud_no_admite": (409, CodigoError.SOLICITUD_NO_ADMITE, NO_ADMITE + " quedó."),
    "firma_repetida": (
        403, CodigoError.YA_PARTICIPO, YA_PARTICIPO[(FIRMAR, "firma_repetida")],
    ),
    "sin_administrador_vigente": (409, *SIN_ADMINISTRADOR),
    "fecha_envio_invalida": (
        422, CodigoError.FECHA_ENVIO_INVALIDA,
        "La fecha del envío va entre el día en que se emitió la solicitud y hoy.",
    ),
    "fecha_respuesta_invalida": (
        422, CodigoError.FECHA_RESPUESTA_INVALIDA,
        "La fecha en que llegó la respuesta va entre el día del envío y hoy.",
    ),
    "destinatario_con_cuenta": (
        409, CodigoError.DESTINATARIO_CON_CUENTA,
        "El destinatario tiene cuenta: la respuesta la da en la aplicación.",
    ),
    "adjunto_invalido": (
        422, CodigoError.ADJUNTO_INVALIDO,
        "Hace falta un archivo subido para este paso de esta solicitud, que no se haya usado y"
        " que el antivirus no haya rechazado.",
    ),
    "medio_invalido": (
        422, CodigoError.PETICION_INVALIDA,
        "El envío que se registra es por un medio externo: carta documento, correo electrónico,"
        " entrega en mano u otro.",
    ),
    "identidad_no_verificada": (
        403, CodigoError.IDENTIDAD_NO_VERIFICADA,
        "Falta verificar una identidad en el consorcio. Soporte puede ayudar.",
    ),
    "sesion_invalida": (403, *VOLVER_A_ENTRAR),
}  # fmt: skip
"""Motivos de las funciones de `0108`. Los que el contrato ya impide (largos, plazo) y los que
fija esta ruta (origen) no están: si llegan, son un defecto (500)."""

CAMPO_DEL_MOTIVO = {
    "fecha_envio_invalida": "fecha_envio",
    "fecha_respuesta_invalida": "recibida_el",
    "adjunto_invalido": "adjunto_id",
    "medio_invalido": "medio",
}
"""El campo del cuerpo que motiva cada error que tiene uno: la pantalla lo marca junto al campo."""

RESPUESTAS = {
    403: {"description": "Sin la facultad o sin el modo; o la misma persona ya firmó."},
    404: {"description": "La solicitud no existe en este consorcio, o no es para esta cuenta."},
    409: {"description": "La solicitud no admite eso en su estado, o falta el administrador."},
}
ASUNTO_AVISO = "Solicitud formal del Consejo"
TEXTO_AVISO = (
    "El Consejo de Propietarios de {consorcio} emitió una solicitud formal de documentación"
    " dirigida a la administración.\n\n"
    "Plazo para responder: hasta el {vence:%d/%m/%Y}.\n\n"
    "El detalle está en la aplicación: {enlace}\n"
)
"""P3: el aviso lleva a la aplicación, sin el contenido de la solicitud."""


@dataclass(frozen=True)
class Permisos:
    """Lo que la persona puede hacer con las solicitudes; la de `responder`, que concede el ámbito,
    da el contexto. Para decidir qué ofrecer: cada ruta vuelve a verificar su acción."""

    responder: Permiso
    preparar: Permiso
    firmar: Permiso
    retirar: Permiso
    registrar_envio: Permiso
    registrar_respuesta: Permiso
    ver_documentos: Permiso


def _permisos(
    responder: Annotated[Permiso, Depends(requiere(RESPONDER, al_entrar=True))],
    preparar: Annotated[Permiso, Depends(requiere(PREPARAR))],
    firmar: Annotated[Permiso, Depends(requiere(FIRMAR))],
    retirar: Annotated[Permiso, Depends(requiere(RETIRAR))],
    registrar_envio: Annotated[Permiso, Depends(requiere(REGISTRAR_ENVIO))],
    registrar_respuesta: Annotated[Permiso, Depends(requiere(REGISTRAR_RESPUESTA))],
    ver_documentos: Annotated[Permiso, Depends(requiere("documentos.ver"))],
) -> Permisos:
    return Permisos(
        responder, preparar, firmar, retirar, registrar_envio, registrar_respuesta, ver_documentos
    )


PermisosDep = Annotated[Permisos, Depends(_permisos)]


def _ejecutar(conn: Connection, permiso: Permiso, sql: str, parametros: dict):
    """Llama a una función de `0108` con la persona y la sesión de la petición, y traduce sus
    motivos a errores de la API."""
    ctx = permiso.ctx
    try:
        return conn.execute(
            text(sql), parametros | {"actor": ctx.persona_id, "sesion": ctx.sesion_id}
        )
    except DBAPIError as e:
        if isinstance(e.orig, InsufficientPrivilege):
            raise Discrepancia(f"la base niega {permiso.accion} y el evaluador lo concede") from e
        motivo = e.orig.diag.message_hint
        if motivo in ERRORES:
            raise ErrorApi(
                *ERRORES[motivo], detalle=detalle_de_campo(CAMPO_DEL_MOTIVO.get(motivo))
            ) from e
        raise


@contextmanager
def _lectura(permiso: Permiso, motor) -> Iterator[Connection]:
    ctx = permiso.ctx
    with transaccion(motor, persona_id=ctx.persona_id, consorcio_id=ctx.consorcio_id) as c:
        yield c


LEER = "select * from nucleo.solicitudes_formales_lectura(:actor, 'api', :sesion)"


def _filas(conn: Connection, permisos: Permisos, solicitud: UUID | None) -> list:
    sql = LEER if solicitud is None else LEER + " where id = :s"
    return _ejecutar(conn, permisos.responder, sql, {"s": solicitud}).mappings().all()


def _firmantes(fila) -> tuple[UUID, ...]:
    return tuple(UUID(f["persona_id"]) for f in fila["firmas"])


def _acciones(fila, firmar_decision, puede: dict[str, bool]) -> list[AccionSolicitud]:
    """Lo que se le ofrece a la persona: lo que el evaluador concedería con la solicitud a la
    vista. La segunda firma sin administrador vigente no se ofrece (informe de fricción C8P-08,
    17-11-2026): la solicitud lo dice con `falta_administrador_vigente`, para que la pantalla
    explique qué falta en vez de llevar al 409."""
    estado = EstadoSolicitudFormal(fila["estado"])
    acciones = []
    if estado is EstadoSolicitudFormal.BORRADOR:
        if firmar_decision.permitido:
            acciones.append(AccionSolicitud.FIRMAR)
        if puede[RETIRAR]:
            acciones.append(AccionSolicitud.RETIRAR)
    elif estado is EstadoSolicitudFormal.ENVIO_PENDIENTE and puede[REGISTRAR_ENVIO]:
        acciones.append(AccionSolicitud.REGISTRAR_ENVIO)
    elif estado in {EstadoSolicitudFormal.ENVIADA, EstadoSolicitudFormal.VENCIDA}:
        # Pregunta 15 del propietario (18-11-2026): vencida, el Consejo registra la respuesta
        # que llegó por otro medio también cuando el destinatario tiene cuenta (`0110`).
        por_otro_medio = (
            fila["destinatario_con_cuenta"] is False or estado is EstadoSolicitudFormal.VENCIDA
        )
        if fila["destinatario_es_actor"]:
            acciones.append(AccionSolicitud.RESPONDER)
        elif por_otro_medio and puede[REGISTRAR_RESPUESTA]:
            acciones.append(AccionSolicitud.REGISTRAR_RESPUESTA)
    # Pregunta 17 (FL-SOL-04, `0112`): el antivirus rechazó el comprobante del envío ya
    # registrado; quien registra envíos sube otro, en cualquier estado posterior al envío.
    envio = fila["envio"]
    if envio is not None and envio.get("estado_archivo") == "rechazado" and puede[REGISTRAR_ENVIO]:
        acciones.append(AccionSolicitud.REEMPLAZAR_COMPROBANTE)
    return acciones


def _falta_administrador(fila, firmar_decision) -> bool:
    """Si quien lee firmaría este borrador y solo lo impide que la firma emite sin un
    administrador vigente (A14): el mismo motivo con que la ruta de firmar responde 409."""
    return (
        EstadoSolicitudFormal(fila["estado"]) is EstadoSolicitudFormal.BORRADOR
        and firmar_decision.motivo_interno == Motivo.SIN_ADMINISTRADOR_VIGENTE
    )


def _solicitudes(
    conn: Connection, permisos: Permisos, solicitud: UUID | None = None
) -> list[SolicitudFormal]:
    filas = _filas(conn, permisos, solicitud)
    firmar = permisos.firmar.decidir_en(conn, [Entidad(firmas=_firmantes(f)) for f in filas])
    puede = {
        p.accion: p.decidir().permitido
        for p in (permisos.retirar, permisos.registrar_envio, permisos.registrar_respuesta)
    }
    yo = permisos.responder.ctx.persona_id
    lee = permisos.ver_documentos.decidir(Entidad()).permitido
    return [
        SolicitudFormal(
            id=f["id"], numero=f["numero"], objeto=f["objeto"], referencia=f["referencia"],
            documentacion=f["documentacion"], plazo_dias=f["plazo_dias"], estado=f["estado"],
            preparada_por_nombre=f["preparada_por_nombre"], preparada_en=f["preparada_en"],
            firmas=[
                FirmaSolicitud(nombre=x["nombre"], firmada_en=x["firmada_en"],
                               propia=UUID(x["persona_id"]) == yo)
                for x in f["firmas"]
            ],
            destinatario_nombre=f["destinatario_nombre"],
            destinatario_con_cuenta=f["destinatario_con_cuenta"],
            emitida_en=f["emitida_en"], texto_emitido=f["texto_emitido"],
            huella_texto=f["huella_texto"],
            envio=EnvioSolicitud(**_con_archivo(f["envio"], lee)) if f["envio"] else None,
            respuesta=(
                RespuestaSolicitud(**_con_archivo(f["respuesta"], lee)) if f["respuesta"] else None
            ),
            retiro=RetiroRegistrado(**f["retiro"]) if f["retiro"] else None,
            acciones=_acciones(f, decision, puede),
            falta_administrador_vigente=_falta_administrador(f, decision),
        )
        for f, decision in zip(filas, firmar, strict=True)
    ]  # fmt: skip


def _con_archivo(datos: dict, lee: bool) -> dict:
    """FL-SOL-04 (18-11-2026): el adjunto se descarga si el antivirus lo aprobó y quien lee tiene
    `documentos.ver`. Solo para mostrar: la descarga vuelve a decidir."""
    return datos | {"descargable": lee and datos.get("estado_archivo") == EstadoVersion.DISPONIBLE}


def _una(conn: Connection, permisos: Permisos, solicitud: UUID) -> SolicitudFormal:
    encontradas = _solicitudes(conn, permisos, solicitud)
    if not encontradas:
        raise ErrorApi(404, CodigoError.NO_ENCONTRADO, NO_ENCONTRADO)
    return encontradas[0]


def _vista(permisos: Permisos, motor, solicitud: UUID) -> SolicitudFormal:
    """La solicitud como la ve la persona, antes de decidir: 404 si no la ve."""
    return _vista_y_firmas(permisos, motor, solicitud)[0]


def _vista_y_firmas(
    permisos: Permisos, motor, solicitud: UUID
) -> tuple[SolicitudFormal, tuple[UUID, ...]]:
    """`_vista` y las personas que ya firmaron, de la misma lectura."""
    with _lectura(permisos.responder, motor) as c:
        filas = _filas(c, permisos, solicitud)
        if not filas:
            raise ErrorApi(404, CodigoError.NO_ENCONTRADO, NO_ENCONTRADO)
        return _una(c, permisos, solicitud), _firmantes(filas[0])


@router.get(
    "/consorcios/{consorcio_id}/solicitudes-formales",
    response_model=ListaSolicitudesFormales,
    summary="Solicitudes formales de documentación: todas, para quien ve el panel; las dirigidas a"
    " su cuenta, para el destinatario",
)
def listar_solicitudes(permisos: PermisosDep, motor: MotorDep) -> ListaSolicitudesFormales:
    with _lectura(permisos.responder, motor) as c:
        elementos = _solicitudes(c, permisos)
    return ListaSolicitudesFormales(
        elementos=elementos,
        puede_preparar=permisos.preparar.decidir().permitido,
        hay_administrador_vigente=permisos.responder.ctx.hay_administrador_vigente,
        plazo_por_defecto_dias=PLAZO_POR_DEFECTO_DIAS,
        plazo_minimo_dias=PLAZO_MINIMO_DIAS,
        plazo_maximo_dias=PLAZO_MAXIMO_DIAS,
    )


@router.post(
    "/consorcios/{consorcio_id}/solicitudes-formales",
    status_code=201,
    response_model=SolicitudFormal,
    summary="Preparar una solicitud formal: queda en borrador; preparar no cuenta como firma",
    responses=RESPUESTAS,
)
def preparar_solicitud(
    cuerpo: SolicitudFormalNueva,
    permisos: PermisosDep,
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    permisos.preparar.verificar()

    def efecto(conn: Connection) -> tuple[int, SolicitudFormal]:
        nueva = _ejecutar(
            conn, permisos.preparar,
            "select nucleo.preparar_solicitud_formal(:objeto, :referencia, :documentacion,"
            " :plazo, :actor, 'api', :sesion)",
            {"objeto": cuerpo.objeto.value, "referencia": cuerpo.referencia,
             "documentacion": cuerpo.documentacion, "plazo": cuerpo.plazo_dias},
        ).scalar_one()  # fmt: skip
        return 201, _una(conn, permisos, nueva)

    return responder_una_vez(motor, permisos.preparar.ctx, idempotencia, efecto)


@router.post(
    "/consorcios/{consorcio_id}/solicitudes-formales/{solicitud_id}/firma",
    response_model=SolicitudFormal,
    summary="Firmar una solicitud: la segunda firma, de otra persona, la emite dirigida al"
    " administrador vigente",
    responses=RESPUESTAS,
)
def firmar_solicitud(
    request: Request,
    solicitud_id: UUID,
    permisos: PermisosDep,
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    """PA36: dos firmas de personas y de seres humanos distintos (A5, A13-C). La segunda firma
    emite: fija el destinatario (A14), el texto PG08 y su huella. Si el destinatario tiene
    cuenta, el envío ocurre en el mismo acto y se le encola un aviso por correo, sin el
    contenido (P3); si no, queda con el envío pendiente."""
    enlace = f"{origen_publico(request)}{RUTA_SOLICITUDES}"

    def efecto(conn: Connection) -> tuple[int, SolicitudFormal]:
        # Las firmas se leen y se controlan dentro del turno de la clave: un reintento de una
        # firma que ya se hizo recibe la respuesta guardada en vez de `ya_participo` (Codex).
        filas = _filas(conn, permisos, solicitud_id)
        if not filas:
            raise ErrorApi(404, CodigoError.NO_ENCONTRADO, NO_ENCONTRADO)
        permisos.firmar.verificar(Entidad(firmas=_firmantes(filas[0])))
        fila = _ejecutar(
            conn, permisos.firmar,
            "select * from nucleo.firmar_solicitud_formal(:s, :actor, 'api', :sesion)",
            {"s": solicitud_id},
        ).mappings().one()  # fmt: skip
        if fila["correo_destinatario"] is not None:
            correo.solicitar(
                conn, para=fila["correo_destinatario"], asunto=ASUNTO_AVISO,
                texto=TEXTO_AVISO.format(consorcio=fila["consorcio_nombre"], vence=fila["vence"],
                                         enlace=enlace),
                consorcio_id=permisos.firmar.ctx.consorcio_id,
            )  # fmt: skip
        return 200, _una(conn, permisos, solicitud_id)

    return responder_una_vez(motor, permisos.firmar.ctx, idempotencia, efecto)


@router.post(
    "/consorcios/{consorcio_id}/solicitudes-formales/{solicitud_id}/retiro",
    response_model=SolicitudFormal,
    summary="Retirar un borrador, con el motivo: una solicitud emitida no se retira (P4)",
    responses=RESPUESTAS,
)
def retirar_solicitud(
    solicitud_id: UUID,
    cuerpo: RetiroSolicitud,
    permisos: PermisosDep,
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    vista = _vista(permisos, motor, solicitud_id)
    permisos.retirar.verificar()

    def efecto(conn: Connection) -> tuple[int, SolicitudFormal]:
        _ejecutar(
            conn, permisos.retirar,
            "select nucleo.retirar_solicitud_formal(:s, :motivo, :actor, 'api', :sesion)",
            {"s": vista.id, "motivo": cuerpo.motivo},
        )  # fmt: skip
        return 200, _una(conn, permisos, vista.id)

    return responder_una_vez(motor, permisos.retirar.ctx, idempotencia, efecto)


def _permiso_del_acto(permisos: Permisos, vista: SolicitudFormal, para: ParaAdjunto) -> Permiso:
    """El acto que va a citar el adjunto, con su política verificada: el envío lo registra el
    Consejo; la respuesta la da el destinatario con cuenta o, si no tiene, la registra el
    Consejo."""
    if para is ParaAdjunto.ENVIO:
        permiso = permisos.registrar_envio
    elif AccionSolicitud.RESPONDER in vista.acciones:
        permiso = permisos.responder
    else:
        permiso = permisos.registrar_respuesta
    permiso.verificar()
    return permiso


@router.post(
    "/consorcios/{consorcio_id}/solicitudes-formales/{solicitud_id}/adjuntos",
    status_code=201,
    response_model=AdjuntoSubido,
    summary="Subir el comprobante del envío o un archivo de la respuesta, antes de registrarlos:"
    " entra en cuarentena hasta que el antivirus lo acepta",
    openapi_extra=SUBIDA_OPENAPI,
    responses=RESPUESTAS | RESPUESTAS_SUBIDA,
)
async def subir_adjunto(
    solicitud_id: UUID,
    para: Annotated[ParaAdjunto, Query(description="El acto que lo va a citar.")],
    titulo: Annotated[Titulo, Query(min_length=1, description="El título del documento.")],
    request: Request,
    permisos: PermisosDep,
    clave: ClaveIdempotenciaDep,
    motor: MotorDep,
) -> Response:
    """Lo sube quien puede hacer ese acto ahora: el documento queda en «Otros», que ve quien
    tiene `documentos.ver`, y cuenta recién cuando el antivirus lo acepta."""

    def autorizar() -> Permiso:
        vista = _vista(permisos, motor, solicitud_id)
        return _permiso_del_acto(permisos, vista, para)

    permiso = await anyio.to_thread.run_sync(autorizar)

    def nueva(conn: Connection, tipo_archivo: str, tamano: int, suma: str):
        return _ejecutar(
            conn, permiso,
            "select nueva_version_id as id, nueva_clave_objeto as clave_objeto,"
            " nuevo_documento_id as documento_id, nuevo_adjunto_id as adjunto_id"
            " from nucleo.subir_adjunto_solicitud_formal(:s, :para, :titulo, :t, :n, :suma,"
            " :actor, 'api', :sesion)",
            {"s": solicitud_id, "para": para.value, "titulo": titulo, "t": tipo_archivo,
             "n": tamano, "suma": suma},
        ).one()  # fmt: skip

    def respuesta(fila, cargada: VersionCargada) -> AdjuntoSubido:
        return AdjuntoSubido(adjunto_id=fila.adjunto_id, documento_id=fila.documento_id,
                             archivo=cargada)  # fmt: skip

    return await subir_archivo(request, permiso.ctx, clave, motor, nueva, respuesta)


@router.post(
    "/consorcios/{consorcio_id}/solicitudes-formales/{solicitud_id}/envio",
    response_model=SolicitudFormal,
    summary="Registrar el envío por un medio externo (destinatario sin cuenta): desde esa fecha"
    " corre el plazo",
    responses=RESPUESTAS | {422: {"description": "Fecha, medio o comprobante inválidos."}},
)
def registrar_envio(
    solicitud_id: UUID,
    cuerpo: EnvioNuevo,
    permisos: PermisosDep,
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    vista = _vista(permisos, motor, solicitud_id)
    permisos.registrar_envio.verificar()

    def efecto(conn: Connection) -> tuple[int, SolicitudFormal]:
        _ejecutar(
            conn, permisos.registrar_envio,
            "select nucleo.registrar_envio_solicitud_formal(:s, :medio, :detalle, :fecha,"
            " :adjunto, :actor, 'api', :sesion)",
            {"s": vista.id, "medio": cuerpo.medio.value, "detalle": cuerpo.detalle,
             "fecha": cuerpo.fecha_envio, "adjunto": cuerpo.adjunto_id},
        )  # fmt: skip
        return 200, _una(conn, permisos, vista.id)

    return responder_una_vez(motor, permisos.registrar_envio.ctx, idempotencia, efecto)


@router.post(
    "/consorcios/{consorcio_id}/solicitudes-formales/{solicitud_id}/envio/comprobante",
    response_model=SolicitudFormal,
    summary="Otro comprobante para el envío ya registrado, si el antivirus rechazó el vigente:"
    " la fecha y el vencimiento no cambian (pregunta 17, FL-SOL-04)",
    responses=RESPUESTAS | {422: {"description": "Comprobante inválido."}},
)
def reemplazar_comprobante(
    solicitud_id: UUID,
    cuerpo: ComprobanteNuevo,
    permisos: PermisosDep,
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    vista = _vista(permisos, motor, solicitud_id)
    permisos.registrar_envio.verificar()

    def efecto(conn: Connection) -> tuple[int, SolicitudFormal]:
        _ejecutar(
            conn, permisos.registrar_envio,
            "select nucleo.reemplazar_comprobante_envio_solicitud_formal(:s, :adjunto, :actor,"
            " 'api', :sesion)",
            {"s": vista.id, "adjunto": cuerpo.adjunto_id},
        )  # fmt: skip
        return 200, _una(conn, permisos, vista.id)

    return responder_una_vez(motor, permisos.registrar_envio.ctx, idempotencia, efecto)


@router.post(
    "/consorcios/{consorcio_id}/solicitudes-formales/{solicitud_id}/respuesta",
    response_model=SolicitudFormal,
    summary="Responder la solicitud en la aplicación: solo la persona del mandato destinatario;"
    " después del vencimiento se acepta y queda fuera de plazo (P5)",
    responses=RESPUESTAS | {422: {"description": "Adjunto inválido."}},
)
def responder_solicitud(
    solicitud_id: UUID,
    cuerpo: RespuestaNueva,
    permisos: PermisosDep,
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    vista = _vista(permisos, motor, solicitud_id)
    # La política concede por ámbito; ser el destinatario lo decide la base (404 si no lo es).

    def efecto(conn: Connection) -> tuple[int, SolicitudFormal]:
        _ejecutar(
            conn, permisos.responder,
            "select nucleo.responder_solicitud_formal(:s, :texto, :adjunto, :actor, 'api',"
            " :sesion)",
            {"s": vista.id, "texto": cuerpo.texto, "adjunto": cuerpo.adjunto_id},
        )  # fmt: skip
        return 200, _una(conn, permisos, vista.id)

    return responder_una_vez(motor, permisos.responder.ctx, idempotencia, efecto)


@router.post(
    "/consorcios/{consorcio_id}/solicitudes-formales/{solicitud_id}/respuesta-recibida",
    response_model=SolicitudFormal,
    summary="Registrar la respuesta que llegó por otro medio de un destinatario sin cuenta (P2)",
    responses=RESPUESTAS | {422: {"description": "Fecha o adjunto inválidos."}},
)
def registrar_respuesta(
    solicitud_id: UUID,
    cuerpo: RespuestaRecibida,
    permisos: PermisosDep,
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    vista = _vista(permisos, motor, solicitud_id)
    permisos.registrar_respuesta.verificar()

    def efecto(conn: Connection) -> tuple[int, SolicitudFormal]:
        _ejecutar(
            conn, permisos.registrar_respuesta,
            "select nucleo.registrar_respuesta_solicitud_formal(:s, :texto, :recibida, :adjunto,"
            " :actor, 'api', :sesion)",
            {"s": vista.id, "texto": cuerpo.texto, "recibida": cuerpo.recibida_el,
             "adjunto": cuerpo.adjunto_id},
        )  # fmt: skip
        return 200, _una(conn, permisos, vista.id)

    return responder_una_vez(motor, permisos.registrar_respuesta.ctx, idempotencia, efecto)
