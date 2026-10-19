"""A7: la cuenta del mandatario externo, con cuatro ojos, y su corrección (H2b 2b-4, PR 4).

Diseño: `doc/diseno/H2b_4_Diseno.md` de CEM, §7 y §10 (decisión P1 del propietario).

**Quién.** Las cinco acciones usan la facultad `mandatos.registrar` (MC o AD, C2). Emitir
(`mandatos.asociar_persona`) y desasociar (`mandatos.desasociar_persona`) piden la referencia;
aprobar (`mandatos.aprobar_asociacion`) exige otra persona que la que emitió; leer
(`mandatos.ver_asociaciones`) y retirar (`mandatos.revocar_asociacion`) no traen entidad. La
política decide antes de cualquier efecto; las funciones de `0015` vuelven a garantizar la
integridad en la base. Un motivo de la base que el evaluador ya tenía que haber negado es una
discrepancia y responde 500.

**Recorrido.** Emitir deja la invitación por aprobar, sin enlace ni correo. Aprobar fija el
token y el vencimiento (7 días) y, en la misma transacción, publica el correo con el enlace
`/invitacion-mandato#token=…`. La página consulta (`POST /api/v1/auth/invitacion-mandato/
consultar`) y acepta (`…/aceptar`) con el mismo modelo que las invitaciones de vínculo: correo
sin cuenta, se crea; con cuenta, hace falta su sesión. Sin sesión no hay reintento idempotente
de aceptar (el token no prueba quién reintenta).

El CUIT no sale en ninguna respuesta; el correo y la referencia, solo de la invitación por
aprobar y para quien la emitió o tiene `mandatos.registrar` (`invitaciones_de_mandato`).
"""

import logging
from datetime import timedelta
from typing import Annotated
from uuid import UUID
from zoneinfo import ZoneInfo

from cem_contracts.errores import CodigoError, Error
from cem_contracts.mandatos_invitaciones import (
    AceptacionInvitacionMandato,
    Desasociacion,
    DesasociacionHecha,
    EstadoInvitacionMandato,
    InvitacionMandato,
    InvitacionMandatoAceptada,
    InvitacionMandatoAprobada,
    InvitacionMandatoEmitida,
    InvitacionMandatoNueva,
    InvitacionMandatoRecibida,
    ListaInvitacionesMandato,
    TokenInvitacionMandato,
)
from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError

from app.autorizacion.evaluador import Entidad
from app.autorizacion.requiere import NO_ENCONTRADO, Permiso, requiere
from app.contexto import MotorDep
from app.db import transaccion
from app.errores import ErrorApi, sin_cache
from app.identidad import eventos, sesiones
from app.identidad.invitaciones import origen_publico, persona_de_la_sesion
from app.plataforma.idempotencia import IdempotenciaDep, responder_una_vez

router = APIRouter(prefix="/api/v1", tags=["mandatos"], dependencies=[Depends(sin_cache)])
log = logging.getLogger("cem.mandatos")

VIGENCIA_INVITACION_MANDATO = timedelta(days=7)
"""La migración 0015 impone el mismo tope."""
RUTA_INVITACION_MANDATO = "/invitacion-mandato"
"""Página del frontend que abre el enlace del correo; lee el token del fragmento."""

ERRORES = {
    "mandato_invalido": (404, CodigoError.NO_ENCONTRADO, NO_ENCONTRADO),
    "mandato_ya_asociado": (
        409,
        CodigoError.MANDATO_YA_ASOCIADO,
        "El mandato ya tiene una cuenta. Para cambiarla, primero hay que desasociarla.",
    ),
    "mandato_no_vigente": (
        409,
        CodigoError.MANDATO_NO_VIGENTE,
        "El mandato terminó o tiene una baja: no admite una cuenta.",
    ),
    "mandato_no_asociable": (
        409,
        CodigoError.MANDATO_NO_ASOCIABLE,
        "Solo la cuenta de un administrador externo se asocia o se desasocia así.",
    ),
    "mandato_sin_persona": (
        409,
        CodigoError.MANDATO_SIN_PERSONA,
        "El mandato no tiene una cuenta asociada.",
    ),
    "invitacion_invalida": (
        404,
        CodigoError.INVITACION_INVALIDA,
        "La invitación ya no es la que está por aprobar, o no existe.",
    ),
    "invitacion_aceptada": (
        409,
        CodigoError.INVITACION_NO_PENDIENTE,
        "La invitación ya se aceptó. Para corregir la cuenta hay que desasociarla.",
    ),
    "invitacion_ya_aprobada": (
        409,
        CodigoError.INVITACION_NO_PENDIENTE,
        "La invitación ya está aprobada: el correo con el enlace ya salió.",
    ),
}
"""Motivos de las funciones de `0015` que dependen del estado del mandato o de la invitación.
Los de separación (`aprobacion_de_si_mismo`, `desasociacion_de_si_mismo`) los niega antes el
evaluador: si llegan a la base, es una discrepancia (500)."""


def _llamar(conn: Connection, sql: str, parametros: dict):
    """Ejecuta una función de `0015` y traduce sus motivos de estado a errores de la API."""
    try:
        return conn.execute(text(sql), parametros)
    except DBAPIError as e:
        motivo = e.orig.diag.message_hint
        if motivo in ERRORES:
            estado, codigo, mensaje = ERRORES[motivo]
            raise ErrorApi(estado, codigo, mensaje) from e
        raise


def _mandato(conn: Connection, consorcio_id: UUID, mandato_id: UUID):
    fila = conn.execute(
        text("select persona_id from nucleo.mandato where id = :m and consorcio_id = :c"),
        {"m": mandato_id, "c": consorcio_id},
    ).one_or_none()
    if fila is None:
        raise ErrorApi(404, CodigoError.NO_ENCONTRADO, NO_ENCONTRADO)
    return fila


@router.post(
    "/consorcios/{consorcio_id}/mandatos/{mandato_id}/invitaciones",
    status_code=201,
    response_model=InvitacionMandatoEmitida,
    summary="Emitir la invitación de un administrador externo (queda por aprobar)",
    responses={
        403: {"description": "Sin `mandatos.registrar` en el consorcio."},
        404: {"description": "El mandato no existe en este consorcio."},
        409: {"description": "El mandato ya tiene cuenta, no está vigente o no es externo."},
    },
)
def emitir(
    mandato_id: UUID,
    nueva: InvitacionMandatoNueva,
    permiso: Annotated[Permiso, Depends(requiere("mandatos.asociar_persona"))],
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    """Al correo que figura en el respaldo, con la referencia de dónde sale (P1-b). No sale
    ningún correo hasta que otra persona con `mandatos.registrar` la apruebe. Emitir de nuevo
    retira la anterior que no se aceptó."""
    ctx = permiso.ctx
    permiso.verificar(Entidad(respaldo_presente=True))  # el contrato exige la referencia

    def efecto(conn: Connection) -> tuple[int, InvitacionMandatoEmitida]:
        nueva_id = _llamar(
            conn,
            "select nucleo.invitar_a_mandato(:m, :correo, :r, null, null, null, :a, 'api')",
            {"m": mandato_id, "correo": nueva.correo, "r": nueva.referencia, "a": ctx.persona_id},
        ).scalar_one()
        return 201, InvitacionMandatoEmitida(
            id=nueva_id, mandato_id=mandato_id, estado=EstadoInvitacionMandato.POR_APROBAR
        )

    return responder_una_vez(motor, ctx, idempotencia, efecto)


@router.get(
    "/consorcios/{consorcio_id}/mandatos/{mandato_id}/invitaciones",
    response_model=ListaInvitacionesMandato,
    summary="Invitaciones de un mandato",
    responses={
        403: {"description": "Sin `mandatos.registrar` en el consorcio."},
        404: {"description": "El mandato no existe en este consorcio."},
    },
)
def listar(
    mandato_id: UUID,
    permiso: Annotated[Permiso, Depends(requiere("mandatos.ver_asociaciones", al_entrar=True))],
    motor: MotorDep,
) -> ListaInvitacionesMandato:
    """La más reciente primero. El correo y la referencia salen solo de la que está por
    aprobar: sin verlos, aprobar sería un sello de goma (P1). En cualquier otro estado, nulos."""
    ctx = permiso.ctx
    with transaccion(motor, persona_id=ctx.persona_id, consorcio_id=ctx.consorcio_id) as conn:
        _mandato(conn, ctx.consorcio_id, mandato_id)
        filas = conn.execute(
            text("select * from nucleo.invitaciones_de_mandato(:m)"), {"m": mandato_id}
        ).mappings()
        return ListaInvitacionesMandato(elementos=[InvitacionMandato(**f) for f in filas])


@router.post(
    "/consorcios/{consorcio_id}/invitaciones-mandato/{invitacion_id}/aprobar",
    response_model=InvitacionMandatoAprobada,
    summary="Aprobar la invitación de un administrador externo (sale el correo)",
    responses={
        403: {"description": "Sin `mandatos.registrar`, o quien aprueba es quien la emitió."},
        404: {"description": "La invitación no existe aquí, o fue reemplazada o retirada."},
        409: {
            "description": "La invitación ya está aprobada o aceptada, o el mandato ya tiene"
            " cuenta, no está vigente o no es externo."
        },
    },
)
def aprobar(
    invitacion_id: UUID,
    request: Request,
    permiso: Annotated[Permiso, Depends(requiere("mandatos.aprobar_asociacion"))],
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    """Aprueba ESA invitación: si quien la emitió la reemplazó mientras tanto, responde 404 y
    nadie aprueba un correo que no vio. Fija el enlace, que vence en 7 días, y lo manda al
    correo invitado en la misma transacción."""
    ctx = permiso.ctx
    origen = origen_publico(request)
    with transaccion(motor, persona_id=ctx.persona_id, consorcio_id=ctx.consorcio_id) as conn:
        fila = conn.execute(
            text(
                "select emitida_por from nucleo.invitacion_mandato"
                " where id = :i and consorcio_id = :c"
            ),
            {"i": invitacion_id, "c": ctx.consorcio_id},
        ).one_or_none()
    # Una del equipo (sin emisor) ya se aprobó al emitirla: no hay nada que aprobar.
    if fila is None or fila.emitida_por is None:
        estado, codigo, mensaje = ERRORES["invitacion_invalida"]
        raise ErrorApi(estado, codigo, mensaje)
    permiso.verificar(Entidad(emitido_por=fila.emitida_por, respaldo_presente=True))

    def efecto(conn: Connection) -> tuple[int, InvitacionMandatoAprobada]:
        token = sesiones.nuevo_token()
        # El vencimiento sale de now() de la misma transacción que fija `aprobada_en`: así el
        # tope de 7 días de la base se cumple justo.
        datos = _llamar(
            conn,
            "select a.correo, a.consorcio, a.mandatario, a.emisor, now() + :v as vence,"
            " c.zona_horaria from nucleo.aprobar_invitacion_mandato(:i, :h, now() + :v, :a,"
            " 'api') a cross join nucleo.consorcio c where c.id = :c",
            {
                "i": invitacion_id,
                "h": sesiones.hash_token(token),
                "v": VIGENCIA_INVITACION_MANDATO,
                "a": ctx.persona_id,
                "c": ctx.consorcio_id,
            },
        ).one()
        eventos.solicitar_invitacion_mandato(
            conn,
            consorcio_id=ctx.consorcio_id,
            para=datos.correo,
            quien=datos.emisor,
            consorcio=datos.consorcio,
            mandatario=datos.mandatario,
            enlace=f"{origen}{RUTA_INVITACION_MANDATO}#token={token}",
            vence=datos.vence.astimezone(ZoneInfo(datos.zona_horaria)).strftime(
                "%d/%m/%Y a las %H:%M"
            ),
        )
        return 200, InvitacionMandatoAprobada(
            id=invitacion_id, estado=EstadoInvitacionMandato.PENDIENTE, vence_en=datos.vence
        )

    return responder_una_vez(motor, ctx, idempotencia, efecto)


@router.post(
    "/consorcios/{consorcio_id}/invitaciones-mandato/{invitacion_id}/revocar",
    status_code=204,
    response_class=Response,
    summary="Retirar una invitación de mandato no aceptada",
    responses={
        404: {"description": "La invitación no existe en este consorcio."},
        409: {"description": "Ya se aceptó: para corregir la cuenta se desasocia."},
    },
)
def revocar(
    invitacion_id: UUID,
    permiso: Annotated[Permiso, Depends(requiere("mandatos.revocar_asociacion", al_entrar=True))],
    _clave: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    """Repetirlo responde lo mismo, con un solo evento: retirar es idempotente por sí mismo, así
    que la clave se exige como en toda escritura de A7 pero no se guarda respuesta."""
    ctx = permiso.ctx
    with transaccion(motor, persona_id=ctx.persona_id, consorcio_id=ctx.consorcio_id) as conn:
        _llamar(
            conn,
            "select nucleo.revocar_invitacion_mandato(:i, :a, 'api')",
            {"i": invitacion_id, "a": ctx.persona_id},
        )
    return Response(status_code=204)


@router.post(
    "/consorcios/{consorcio_id}/mandatos/{mandato_id}/desasociar-persona",
    response_model=DesasociacionHecha,
    summary="Desasociar la cuenta de un administrador externo",
    responses={
        403: {"description": "Sin `mandatos.registrar`, o la cuenta es la de quien lo pide."},
        404: {"description": "El mandato no existe en este consorcio."},
        409: {"description": "El mandato no tiene cuenta o no es de un administrador externo."},
    },
)
def desasociar(
    mandato_id: UUID,
    pedido: Desasociacion,
    permiso: Annotated[Permiso, Depends(requiere("mandatos.desasociar_persona"))],
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    """Corrige una cuenta asociada por error (P1-c): el mandato, su CUIT y su vigencia quedan
    como estaban; la persona pierde el acceso (se cierran sus sesiones) y la referencia queda en
    la fila de la desasociación, no en la auditoría."""
    ctx = permiso.ctx
    with transaccion(motor, persona_id=ctx.persona_id, consorcio_id=ctx.consorcio_id) as conn:
        m = _mandato(conn, ctx.consorcio_id, mandato_id)
    # Sin cuenta no se corta acá: un reintento con la misma clave tiene que recibir el 200
    # guardado (la desasociación ya dejó el mandato sin persona), y una solicitud nueva recibe
    # el 409 `mandato_sin_persona` de la función. La facultad se verifica igual.
    if m.persona_id is None:
        permiso.verificar(Entidad(objetivo_sin_cuenta=True, respaldo_presente=True))
    else:
        permiso.verificar(Entidad(persona_objetivo=m.persona_id, respaldo_presente=True))

    def efecto(conn: Connection) -> tuple[int, DesasociacionHecha]:
        revocadas = _llamar(
            conn,
            "select nucleo.desasociar_persona_de_mandato(:m, :r, null, :a, 'api')",
            {"m": mandato_id, "r": pedido.referencia, "a": ctx.persona_id},
        ).scalar_one()
        return 200, DesasociacionHecha(mandato_id=mandato_id, sesiones_revocadas=revocadas)

    return responder_una_vez(motor, ctx, idempotencia, efecto)


# Rutas de quien recibe la invitación --------------------------------------------------------
# Bajo /api/v1/auth/: comparten el cupo por IP de la familia de autenticación (§10.6).

SIN_CACHE = {"Cache-Control": "no-store"}


def _invitacion_invalida() -> ErrorApi:
    return ErrorApi(
        404,
        CodigoError.INVITACION_INVALIDA,
        "La invitación no sirve: puede haber vencido, haberse usado o haber sido retirada."
        " Pedí una nueva a quien te invitó.",
        encabezados=SIN_CACHE,
    )


@router.post(
    "/auth/invitacion-mandato/consultar",
    response_model=InvitacionMandatoRecibida,
    summary="Ver una invitación de mandato antes de aceptarla",
    responses={404: {"description": "La invitación no sirve (todas las causas responden igual)."}},
)
def consultar(pedido: TokenInvitacionMandato, motor: MotorDep) -> InvitacionMandatoRecibida:
    """No la consume: abrirla no la acepta. Sin CUIT."""
    with transaccion(motor, persona_id=None) as conn:
        r = conn.execute(
            text(
                "select resultado, consorcio, mandatario, invitada_por, vence"
                " from nucleo.consultar_invitacion_mandato(:t)"
            ),
            {"t": sesiones.hash_token(pedido.token)},
        ).one()
    if r.resultado != "ok":
        log.info("invitación de mandato consultada sin servir: %s", r.resultado)
        raise _invitacion_invalida()
    return InvitacionMandatoRecibida(
        consorcio=r.consorcio, mandatario=r.mandatario, invitada_por=r.invitada_por,
        vence_en=r.vence,
    )  # fmt: skip


RECHAZOS_ACEPTAR = {
    "requiere_sesion": (
        CodigoError.INVITACION_REQUIERE_SESION,
        "Ya tenés una cuenta con este correo. Entrá con él y volvé a abrir la invitación.",
    ),
    "otra_persona": (
        CodigoError.INVITACION_DE_OTRA_PERSONA,
        "La invitación es para otro correo. Salí de esta cuenta y abrila de nuevo.",
    ),
    "asociacion_incompatible_con_consejo": (
        CodigoError.ASOCIACION_INCOMPATIBLE_CON_CONSEJO,
        "Tenés en este consorcio un mandato del Consejo en las mismas fechas: el Consejo"
        " fiscaliza y no administra.",
    ),
    "asociacion_de_si_mismo": (
        CodigoError.ASOCIACION_DE_SI_MISMO,
        "Quien emitió o aprobó la invitación no la acepta para sí.",
    ),
}
"""Rechazos de aceptar que dependen de la persona y se le explican (409). Cualquier otro motivo
responde igual que una invitación inexistente (404)."""


@router.post(
    "/auth/invitacion-mandato/aceptar",
    response_model=InvitacionMandatoAceptada,
    summary="Aceptar una invitación de mandato",
    responses={
        404: {"model": Error, "description": "La invitación no sirve (todas las causas igual)."},
        409: {
            "model": Error,
            "description": "Hace falta la sesión del correo invitado, o la persona no puede"
            " asociarse (Consejo superpuesto, o fue quien emitió o aprobó).",
        },
    },
)
def aceptar(
    pedido: AceptacionInvitacionMandato, request: Request, response: Response, motor: MotorDep
) -> InvitacionMandatoAceptada:
    """Asocia la persona al mandato. Si el correo no tenía cuenta crea la persona y una sesión
    (deja sus cookies). Si la tenía, pide la sesión de esa persona (con su `X-CSRF-Token`).
    Con la sesión de quien ya la aceptó, responde que el mandato es suyo mientras lo siga
    siendo; nunca reasocia ni abre otra sesión. Las mismas protecciones contra otro sitio que
    `POST /auth/invitacion/aceptar`."""
    response.headers.update(SIN_CACHE)
    sitio = request.headers.get("Sec-Fetch-Site")
    if sitio is not None and sitio != "same-origin":
        log.info("aceptación de invitación de mandato rechazada: otro_sitio")
        raise ErrorApi(
            403, CodigoError.CSRF_INVALIDO, "No pudimos verificar el pedido. Recargá la página."
        )
    persona_sesion = persona_de_la_sesion(request, motor)
    token_sesion = sesiones.nuevo_token()
    dispositivo = sesiones.fijar_dispositivo(request, response)
    with transaccion(motor, persona_id=None) as conn:
        r = conn.execute(
            text(
                "select resultado, persona, consorcio, mandato, cuenta_nueva, sesion, vence,"
                " vigente_desde from nucleo.aceptar_invitacion_mandato(:t, :p, :n, :s, :d, :a,"
                " cast(:ip as inet), :v)"
            ),
            {
                "t": sesiones.hash_token(pedido.token),
                "p": persona_sesion,
                "n": pedido.nombre.strip() if pedido.nombre else None,
                "s": sesiones.hash_token(token_sesion),
                "d": dispositivo,
                "a": sesiones.agente(request),
                "ip": sesiones.ip_cliente(request),
                "v": sesiones.VIGENCIA_SESION,
            },
        ).one()
    if r.resultado in RECHAZOS_ACEPTAR:
        codigo, mensaje = RECHAZOS_ACEPTAR[r.resultado]
        raise ErrorApi(409, codigo, mensaje, encabezados=SIN_CACHE)
    if r.resultado not in {"ok", "ya_asociado"}:
        log.info("aceptación de invitación de mandato rechazada: %s", r.resultado)
        raise _invitacion_invalida()
    if r.cuenta_nueva:
        sesiones.fijar_sesion(response, token_sesion)
    log.info(
        "invitación de mandato aceptada",
        extra={
            "campos": {
                "consorcio_id": str(r.consorcio),
                "persona_id": str(r.persona),
                "mandato_id": str(r.mandato),
                "resultado": r.resultado,
            }
        },
    )
    return InvitacionMandatoAceptada(
        consorcio_id=r.consorcio, persona_id=r.persona, mandato_id=r.mandato,
        cuenta_nueva=r.cuenta_nueva, vigente_desde=r.vigente_desde,
    )  # fmt: skip
