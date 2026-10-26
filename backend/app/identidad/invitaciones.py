"""Invitaciones y alta de personas (documento 08, RF03; plan H2 2a; migración 0007).

**Quién invita (H2b 2b-3).** Lo decide la política `invitaciones.crear` (`requiere`): el
propietario vigente de esa misma unidad (puede sumar a su inquilino, al ocupante o a otro
titular), o quien tiene la facultad `invitaciones.crear` en un mandato vigente de este consorcio
con un recurso que alcanza la unidad (sin recurso, todo el consorcio). Nadie que solo sea
inquilino u ocupante. Listar y retirar siguen la misma regla. El equipo de la plataforma da de
alta a las primeras personas de un consorcio con `scripts/invitar.py`.

**Recorrido.** Crear la invitación registra el hash del token y, en la misma transacción,
publica el correo con el enlace `/invitacion#token=…` (el token va en el fragmento, como en
`/entrar`). La página consulta la invitación (`POST /api/v1/auth/invitacion/consultar`) para
mostrar edificio, unidad y vínculo, y la acepta (`POST /api/v1/auth/invitacion/aceptar`):

- correo sin cuenta: se crea la persona, el vínculo y una sesión, sin pasar por el enlace de
  acceso (el token le llegó a ese correo y sirve una sola vez);
- correo con cuenta: hay que aceptar con la sesión de esa persona (409
  `invitacion_requiere_sesion` si no hay sesión, 409 `invitacion_de_otra_persona` si es de otra).

Invitar de nuevo al mismo correo, unidad y vínculo retira la pendiente anterior (reenviar).
Crear, retirar y aceptar quedan en la auditoría encadenada, sin el correo.
"""

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated
from uuid import UUID
from zoneinfo import ZoneInfo

from cem_contracts.errores import CodigoError, Error
from cem_contracts.estados import TipoVinculo
from cem_contracts.invitaciones import (
    AceptacionInvitacion,
    Invitacion,
    InvitacionAceptada,
    InvitacionNueva,
    InvitacionPropia,
    InvitacionPropiaAceptada,
    InvitacionRecibida,
    ListaInvitaciones,
    ListaInvitacionesPropias,
    TokenInvitacion,
)
from cem_contracts.politicas import TipoRecurso
from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import Connection, text

from app import auditoria, limite
from app.autorizacion.evaluador import Entidad
from app.autorizacion.requiere import Permiso, requiere
from app.contexto import IdentidadDep, MotorDep
from app.db import transaccion
from app.errores import ErrorApi
from app.identidad import eventos, sesiones
from app.plataforma.idempotencia import IdempotenciaDep, responder_una_vez_con_cupo

router = APIRouter(prefix="/api/v1", tags=["identidad"])
log = logging.getLogger("cem.identidad")

VIGENCIA_INVITACION = timedelta(days=7)
"""La migración 0007 impone el mismo tope."""
RUTA_INVITACION = "/invitacion"
"""Página del frontend que abre el enlace del correo; lee el token del fragmento."""
LIMITE_INVITAR = "invitar"
SIN_CACHE = {"Cache-Control": "no-store"}
MOTIVOS_INVALIDA = frozenset(
    {"inexistente", "aceptada", "revocada", "vencida", "consorcio_eliminado", "unidad_eliminada"}
)
"""Los de `motivo_invitacion_invalida` (0007): todos responden igual (404)."""
ESTADO_SQL = """case when i.aceptada_en is not null then 'aceptada'
  when i.revocada_en is not null then 'revocada'
  when i.vence_en <= now() then 'vencida' else 'pendiente' end"""
COLUMNAS = f"""i.id, i.unidad_id, u.designacion as unidad, i.tipo::text as tipo, i.correo,
  i.nombre, {ESTADO_SQL} as estado, i.creada_en, i.vence_en, i.aceptada_en, i.revocada_en"""
NO_ENCONTRADA = "No encontramos lo que buscás."
# El correo en el listado (P6 del propietario, 19-10-2026): solo el de una invitación
# **pendiente** y solo para quien la creó. Para cualquier otro autorizado, y para toda aceptada,
# vencida o revocada, es null: una aceptada es el correo con el que la persona entra (D6; E30).
# Las cargadas por el equipo (`invitada_por` nulo) no lo muestran a nadie. Es un filtro de la
# consulta, no del frontend.
COLUMNAS_LISTA = f"""i.id, i.unidad_id, u.designacion as unidad, i.tipo::text as tipo,
  case when i.aceptada_en is null and i.revocada_en is null and i.vence_en > now()
    and i.invitada_por = :yo then i.correo end as correo,
  i.nombre, {ESTADO_SQL} as estado, i.creada_en, i.vence_en, i.aceptada_en, i.revocada_en"""


@dataclass(frozen=True)
class Creada:
    invitacion: Invitacion
    token: str


def sobre_unidad(unidad_id: UUID) -> Entidad:
    """La entidad de las acciones de invitaciones: su unidad (recurso `unidad`, A8)."""
    return Entidad(recursos={TipoRecurso.UNIDAD: str(unidad_id)})


def crear(
    conn: Connection,
    *,
    consorcio_id: UUID,
    nueva: InvitacionNueva,
    invitada_por: UUID | None,
    origen_publico: str,
    origen: str = "api",
    ip: str | None = None,
) -> Creada | None:
    """Registra la invitación, retira la pendiente igual y publica el correo, todo en la
    transacción de `conn` (con el consorcio en el ámbito). None si la unidad no está en el
    consorcio o está dada de baja. 409 si la pendiente igual es de la importación (nombra su
    vínculo): esa no se retira."""
    unidad = conn.execute(
        text("select designacion from nucleo.unidad where id = :u and eliminado_en is null"),
        {"u": nueva.unidad_id},
    ).scalar_one_or_none()
    if unidad is None:
        return None
    correo = nueva.correo.lower()
    # Orden de bloqueos de RF02 (invitación, persona, vínculo, auditoría; ver 0025): primero las
    # pendientes de esta clave y después `persona:<correo>`, antes de retirar, insertar o
    # auditar. Sin la persona, una importación del mismo correo, unidad y carácter sin pendiente
    # previa se trababa con esta ruta: cada una esperaba la invitación nueva o la auditoría de
    # la otra (Codex en app #105).
    clave = {"u": nueva.unidad_id, "t": nueva.tipo.value, "c": correo}
    de_la_clave = (
        "select id, vinculo_id from nucleo.invitacion"
        " where unidad_id = :u and tipo = cast(:t as nucleo.tipo_vinculo) and correo = :c"
        " and aceptada_en is null and revocada_en is null order by id"
    )
    bloqueadas = {f.id for f in conn.execute(text(de_la_clave + " for update"), clave)}
    conn.execute(
        text("select pg_advisory_xact_lock(hashtextextended('persona:' || lower(btrim(:c)), 0))"),
        {"c": correo},
    )
    # Se vuelve a leer con la persona tomada, **sin bloquear**: mientras se esperaba, otra ruta
    # pudo crear (y confirmar) una invitación de esta clave. Bloquearla ahora invertiría el orden
    # (una aceptación de esa invitación la tiene y espera la persona: tres transacciones
    # trabadas, Codex en app #105). Lo que apareció se responde sin tocarlo.
    pendientes = conn.execute(text(de_la_clave), clave).all()
    # La invitación que creó la importación nombra su vínculo (0025): una manual no la reemplaza,
    # ni vencida, porque la nueva no tendría ese vínculo (decisión del propietario, 23-10-2026).
    if any(f.vinculo_id is not None for f in pendientes):
        raise ErrorApi(
            409,
            CodigoError.INVITACION_IMPORTADA_PENDIENTE,
            "Esa persona ya tiene una invitación del padrón importado para esta unidad y carácter."
            " No se reemplaza con una invitación manual.",
        )
    if any(f.id not in bloqueadas for f in pendientes):
        # Otra invitación manual de esta clave se registró mientras se esperaba: retirarla
        # obligaría a bloquearla fuera de orden. No se escribe nada; el pedido se repite.
        raise ErrorApi(
            409,
            CodigoError.IDEMPOTENCIA_EN_CURSO,
            "Otra invitación para esta persona, unidad y carácter se registró recién. Probá de"
            " nuevo en unos segundos.",
        )
    # Se retiran solo las que se bloquearon al principio (las mismas, por lo de arriba).
    for (anterior,) in conn.execute(
        text(
            "update nucleo.invitacion set revocada_en = now()"
            " where id = any(:ids) and aceptada_en is null and revocada_en is null returning id"
        ),
        {"ids": list(bloqueadas)},
    ).all():
        if origen != "soporte":
            auditoria.registrar(
                conn,
                consorcio_id=consorcio_id,
                actor=invitada_por,
                origen=origen,
                accion="identidad.invitacion.reemplazar",
                entidad="invitacion",
                entidad_id=anterior,
                ip=ip,
            )
    token = sesiones.nuevo_token()
    fila = (
        conn.execute(
            text(
                "with i as (insert into nucleo.invitacion"
                " (consorcio_id, unidad_id, tipo, correo, nombre, token_hash, invitada_por,"
                " vence_en) values (:co, :u, cast(:t as nucleo.tipo_vinculo), :c, :n, :h, :p,"
                " now() + :v) returning *)"
                f" select {COLUMNAS} from i join nucleo.unidad u on u.id = i.unidad_id"
            ),
            {
                "co": consorcio_id,
                "u": nueva.unidad_id,
                "t": nueva.tipo.value,
                "c": correo,
                "n": nueva.nombre.strip(),
                "h": sesiones.hash_token(token),
                "p": invitada_por,
                "v": VIGENCIA_INVITACION,
            },
        )
        .mappings()
        .one()
    )
    invitacion = Invitacion(**fila)
    # Soporte de plataforma no escribe eventos de auditoría funcional (decisión del propietario
    # del 26-10-2026, corte S2a): la invitación del equipo queda con `invitada_por` nulo.
    if origen != "soporte":
        auditoria.registrar(
            conn,
            consorcio_id=consorcio_id,
            actor=invitada_por,
            origen=origen,
            accion="identidad.invitacion.crear",
            entidad="invitacion",
            entidad_id=invitacion.id,
            despues={"unidad_id": invitacion.unidad_id, "tipo": invitacion.tipo.value},
            ip=ip,
        )
    consorcio, zona = conn.execute(
        text("select nombre, zona_horaria from nucleo.consorcio where id = :c"),
        {"c": consorcio_id},
    ).one()
    quien = (
        conn.execute(
            text("select nombre from nucleo.persona where id = :p"), {"p": invitada_por}
        ).scalar_one()
        if invitada_por
        else None
    )
    eventos.solicitar_invitacion(
        conn,
        consorcio_id=consorcio_id,
        para=correo,
        nombre=invitacion.nombre,
        quien=quien,
        consorcio=consorcio,
        unidad=unidad,
        tipo=invitacion.tipo.value,
        enlace=f"{origen_publico}{RUTA_INVITACION}#token={token}",
        vence=invitacion.vence_en.astimezone(ZoneInfo(zona)).strftime("%d/%m/%Y a las %H:%M"),
    )
    return Creada(invitacion, token)


def origen_publico(request: Request) -> str:
    origen = request.app.state.config.origen_publico
    if not origen:
        log.error("CEM_ORIGEN_PUBLICO sin configurar: no se puede armar el enlace de invitación")
        raise ErrorApi(503, CodigoError.SERVICIO_NO_DISPONIBLE, "El servicio no está disponible.")
    return origen


# Rutas de quien invita ---------------------------------------------------------------------


@router.post(
    "/consorcios/{consorcio_id}/invitaciones",
    status_code=201,
    response_model=Invitacion,
    summary="Invitar a una persona a una unidad",
    responses={
        403: {
            "description": "La persona no es propietaria vigente de esa unidad ni tiene"
            " `invitaciones.crear` sobre ella."
        },
        404: {"description": "La unidad no existe en este consorcio."},
        409: {
            "description": "Hay una invitación de la importación pendiente para ese correo,"
            " unidad y carácter: no se reemplaza."
        },
    },
)
async def invitar(
    nueva: InvitacionNueva,
    permiso: Annotated[Permiso, Depends(requiere("invitaciones.crear"))],
    idempotencia: IdempotenciaDep,
    request: Request,
    motor: MotorDep,
) -> Response:
    """Manda por correo una invitación de un solo uso que vence en 7 días. Responde igual tenga
    o no cuenta el correo. Invitar de nuevo al mismo correo, unidad y vínculo retira la
    anterior. Cupo: 20 por hora y 50 por día por persona.

    Puede invitar quien es propietario vigente de esa unidad o tiene `invitaciones.crear` con un
    recurso que la alcanza (tabla de verdad, §2.1).

    Pide `Idempotency-Key`: un reintento con la misma clave y los mismos datos recibe la misma
    respuesta durante 24 horas, sin mandar otro correo ni gastar el cupo, también si llega
    mientras el primero sigue en curso. Esa respuesta guardada conserva el correo, solo para
    quien la creó (la clave es por persona; excepción acotada de P6)."""
    ctx = permiso.ctx
    permiso.verificar(sobre_unidad(nueva.unidad_id))
    origen = origen_publico(request)
    ip = sesiones.ip_cliente(request)

    async def cupo() -> None:
        for limite_persona in limite.LIMITES_INVITACIONES_PERSONA:
            await limite.consumir(
                request.app.state.limitador, limite_persona, LIMITE_INVITAR, str(ctx.persona_id)
            )

    def efecto(conn: Connection) -> tuple[int, Invitacion]:
        creada = crear(
            conn,
            consorcio_id=ctx.consorcio_id,
            nueva=nueva,
            invitada_por=ctx.persona_id,
            origen_publico=origen,
            ip=ip,
        )
        if creada is None:  # la unidad no existe en el consorcio del ámbito (E25)
            raise ErrorApi(404, CodigoError.NO_ENCONTRADO, NO_ENCONTRADA)
        log.info(
            "invitación creada",
            extra={
                "campos": {
                    "consorcio_id": str(ctx.consorcio_id),
                    "persona_id": str(ctx.persona_id),
                    "invitacion_id": str(creada.invitacion.id),
                }
            },
        )
        return 201, creada.invitacion

    return await responder_una_vez_con_cupo(motor, ctx, idempotencia, cupo, efecto)


@router.get(
    "/consorcios/{consorcio_id}/invitaciones",
    response_model=ListaInvitaciones,
    summary="Invitaciones de mis unidades",
)
def listar(
    permiso: Annotated[Permiso, Depends(requiere("invitaciones.listar"))],
    motor: MotorDep,
    response: Response,
) -> ListaInvitaciones:
    """Las invitaciones de las unidades que la persona alcanza (por su vínculo de propietaria o
    por su facultad y su recurso), la más reciente primero, con su estado. Cada fila pasa por la
    política: no hay una consulta aparte que decida."""
    response.headers.update(SIN_CACHE)
    ctx = permiso.ctx
    with transaccion(motor, persona_id=ctx.persona_id, consorcio_id=ctx.consorcio_id) as conn:
        filas = conn.execute(
            text(
                f"select {COLUMNAS_LISTA} from nucleo.invitacion i"
                " join nucleo.unidad u on u.id = i.unidad_id"
                " where i.consorcio_id = :c"
                " order by i.creada_en desc, i.id desc"
            ),
            {"c": ctx.consorcio_id, "yo": ctx.persona_id},
        ).mappings()
        return ListaInvitaciones(
            elementos=[
                Invitacion(**f)
                for f in filas
                if permiso.decidir(sobre_unidad(f["unidad_id"])).permitido
            ]
        )


@router.post(
    "/consorcios/{consorcio_id}/invitaciones/{invitacion_id}/revocar",
    status_code=204,
    response_class=Response,
    summary="Retirar una invitación pendiente",
    responses={
        404: {"description": "La invitación no existe o no es de una unidad de la persona."},
        409: {"description": "Ya se aceptó: para quitar el acceso se termina el vínculo."},
    },
)
def revocar(
    invitacion_id: UUID,
    permiso: Annotated[Permiso, Depends(requiere("invitaciones.revocar"))],
    request: Request,
    motor: MotorDep,
) -> Response:
    """Repetirlo responde lo mismo. Una vencida también se puede retirar. Una invitación de una
    unidad que la persona no alcanza responde 404, igual que una que no existe."""
    ctx = permiso.ctx
    with transaccion(motor, persona_id=ctx.persona_id, consorcio_id=ctx.consorcio_id) as conn:
        fila = conn.execute(
            text(
                "select unidad_id, aceptada_en, revocada_en from nucleo.invitacion"
                " where id = :i and consorcio_id = :c for update"
            ),
            {"i": invitacion_id, "c": ctx.consorcio_id},
        ).one_or_none()
        if fila is None:
            raise ErrorApi(404, CodigoError.NO_ENCONTRADO, NO_ENCONTRADA)
        permiso.verificar(sobre_unidad(fila.unidad_id), estado_negado=404)
        if fila.aceptada_en is not None:
            raise ErrorApi(
                409,
                CodigoError.INVITACION_NO_PENDIENTE,
                "La invitación ya se aceptó. Para quitar el acceso hay que terminar el vínculo.",
            )
        if fila.revocada_en is None:
            conn.execute(
                text("update nucleo.invitacion set revocada_en = now() where id = :i"),
                {"i": invitacion_id},
            )
            auditoria.registrar(
                conn,
                consorcio_id=ctx.consorcio_id,
                actor=ctx.persona_id,
                origen="api",
                accion="identidad.invitacion.revocar",
                entidad="invitacion",
                entidad_id=invitacion_id,
                ip=sesiones.ip_cliente(request),
            )
    return Response(status_code=204, headers=SIN_CACHE)


# Rutas de quien recibe la invitación --------------------------------------------------------
# Bajo /api/v1/auth/: comparten el cupo por IP de la familia de autenticación (§10.6).


def _invitacion_propia() -> ErrorApi:
    """F9 (0026): quien creó la invitación no la acepta para sí, ni con otra cuenta de la misma
    identidad humana verificada en el consorcio."""
    log.info("aceptación de invitación rechazada: invitacion_propia")
    return ErrorApi(
        409,
        CodigoError.INVITACION_PROPIA,
        "Esta invitación la creaste vos: la tiene que aceptar la persona invitada.",
        encabezados=SIN_CACHE,
    )


def _vinculo_no_corresponde() -> ErrorApi:
    """La invitación que creó la importación nombra su vínculo (0025) y ese vínculo ya no le
    corresponde: no se reutiliza otro ni se crea uno."""
    log.info("aceptación de invitación rechazada: vinculo_no_corresponde")
    return ErrorApi(
        409,
        CodigoError.INVITACION_VINCULO_NO_CORRESPONDE,
        "El vínculo de esta invitación ya no está en el padrón. Consultá a quien administra el"
        " consorcio.",
        encabezados=SIN_CACHE,
    )


def _invitacion_invalida() -> ErrorApi:
    return ErrorApi(
        404,
        CodigoError.INVITACION_INVALIDA,
        "La invitación no sirve: venció, ya se usó o la retiraron. Pedí una nueva a quien te"
        " invitó.",
        encabezados=SIN_CACHE,
    )


@router.post(
    "/auth/invitacion/consultar",
    response_model=InvitacionRecibida,
    summary="Ver una invitación antes de aceptarla",
    responses={404: {"description": "La invitación no sirve (todas las causas responden igual)."}},
)
def consultar(pedido: TokenInvitacion, motor: MotorDep, response: Response) -> InvitacionRecibida:
    """No la consume: abrirla no la acepta."""
    response.headers.update(SIN_CACHE)
    with transaccion(motor, persona_id=None) as conn:
        r = conn.execute(
            text(
                "select resultado, consorcio, unidad, tipo, correo, nombre, invitada_por, vence"
                " from nucleo.consultar_invitacion(:t)"
            ),
            {"t": sesiones.hash_token(pedido.token)},
        ).one()
    if r.resultado != "ok":
        log.info("invitación consultada sin servir: %s", r.resultado)
        raise _invitacion_invalida()
    return InvitacionRecibida(
        consorcio=r.consorcio,
        unidad=r.unidad,
        tipo=TipoVinculo(r.tipo),
        correo=r.correo,
        nombre=r.nombre,
        invitada_por=r.invitada_por,
        vence_en=r.vence,
    )


def persona_de_la_sesion(request: Request, motor) -> UUID | None:
    """La persona de la sesión de la petición, si trae una válida; con ella, exige CSRF. Una
    cookie vencida o inventada cuenta como sin sesión."""
    token = request.cookies.get(sesiones.COOKIE_SESION)
    if not sesiones.token_valido(token):
        return None
    with transaccion(motor, persona_id=None) as conn:
        sesion = sesiones.validar(conn, token)
    if sesion is None:
        return None
    sesiones.exigir_csrf(request, token)
    return sesion.persona_id


@router.post(
    "/auth/invitacion/aceptar",
    response_model=InvitacionAceptada,
    summary="Aceptar una invitación",
    responses={
        404: {
            "model": Error,
            "description": "La invitación no sirve (todas las causas responden igual).",
        },
        409: {
            "model": Error,
            "description": "El correo ya tiene cuenta y la sesión no es la suya, o no hay; la"
            " invitación nombra un vínculo que ya no le corresponde; o la creó quien la acepta"
            " (`invitacion_propia`, F9).",
        },
    },
)
def aceptar(
    pedido: AceptacionInvitacion, request: Request, response: Response, motor: MotorDep
) -> InvitacionAceptada:
    """Crea el vínculo con vigencia desde hoy. Si el correo no tenía cuenta crea la persona y
    una sesión (deja sus cookies). Si la tenía, pide la sesión de esa persona (con su
    `X-CSRF-Token`).

    Sin sesión no hay token CSRF; como en `POST /auth/codigo`, protegen contra que otro sitio
    haga aceptar una invitación ajena (y meta al navegador en una cuenta que no es suya):
    `Sec-Fetch-Site` debe ser `same-origin` si el navegador lo declara, y el cuerpo debe ser
    `application/json`, que un formulario de otro sitio no puede mandar."""
    response.headers.update(SIN_CACHE)
    sitio = request.headers.get("Sec-Fetch-Site")
    if sitio is not None and sitio != "same-origin":
        log.info("aceptación de invitación rechazada: otro_sitio")
        raise ErrorApi(
            403, CodigoError.CSRF_INVALIDO, "No pudimos verificar el pedido. Recargá la página."
        )
    persona_sesion = persona_de_la_sesion(request, motor)
    token_sesion = sesiones.nuevo_token()
    dispositivo = sesiones.fijar_dispositivo(request, response)
    with transaccion(motor, persona_id=None) as conn:
        r = conn.execute(
            text(
                "select resultado, persona, consorcio, cuenta_nueva, sesion, vence"
                " from nucleo.aceptar_invitacion(:t, :p, :n, :s, :d, :a, cast(:ip as inet), :v)"
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
    if r.resultado == "requiere_sesion":
        raise ErrorApi(
            409,
            CodigoError.INVITACION_REQUIERE_SESION,
            "Ya tenés una cuenta con este correo. Entrá con él y volvé a abrir la invitación.",
            encabezados=SIN_CACHE,
        )
    if r.resultado == "otra_persona":
        raise ErrorApi(
            409,
            CodigoError.INVITACION_DE_OTRA_PERSONA,
            "La invitación es para otro correo. Salí de esta cuenta y abrila de nuevo.",
            encabezados=SIN_CACHE,
        )
    if r.resultado == "vinculo_no_corresponde":
        raise _vinculo_no_corresponde()
    if r.resultado == "invitacion_propia":
        raise _invitacion_propia()
    if r.resultado != "ok":
        log.info("aceptación de invitación rechazada: %s", r.resultado)
        raise _invitacion_invalida()
    if r.cuenta_nueva:
        sesiones.fijar_sesion(response, token_sesion)
    log.info(
        "invitación aceptada",
        extra={
            "campos": {
                "consorcio_id": str(r.consorcio),
                "persona_id": str(r.persona),
                "cuenta_nueva": r.cuenta_nueva,
            }
        },
    )
    return InvitacionAceptada(
        consorcio_id=r.consorcio, persona_id=r.persona, cuenta_nueva=r.cuenta_nueva
    )


# Las invitaciones de la persona (RF02, `0024`) ------------------------------------------------


@router.get(
    "/me/invitaciones",
    response_model=ListaInvitacionesPropias,
    summary="Mis invitaciones de vínculo pendientes",
)
def mis_invitaciones(
    identidad: IdentidadDep, motor: MotorDep, response: Response
) -> ListaInvitacionesPropias:
    """Las invitaciones de vínculo pendientes dirigidas a mi correo (las de mandato tienen su
    propio flujo). Ruta de la persona, sin consorcio: es lo único que puede hacer un importado
    pendiente, que todavía no tiene ámbito (decisión del propietario del 21-10-2026)."""
    with transaccion(motor, persona_id=identidad.persona_id) as conn:
        filas = conn.execute(
            text(
                "select id, consorcio, unidad, tipo, nombre, invitada_por, vence_en"
                " from nucleo.mis_invitaciones()"
            )
        ).mappings()
        lista = ListaInvitacionesPropias(elementos=[InvitacionPropia(**f) for f in filas])
    # Datos de la persona: ni el navegador ni un intermediario los guardan (Codex en #104).
    response.headers.update(SIN_CACHE)
    return lista


@router.post(
    "/me/invitaciones/{invitacion_id}/aceptar",
    response_model=InvitacionPropiaAceptada,
    summary="Aceptar con mi sesión una invitación dirigida a mi correo",
    responses={
        404: {
            "model": Error,
            "description": "No es una invitación pendiente dirigida a mi correo (todas las causas"
            " responden igual).",
        },
        409: {
            "model": Error,
            "description": "La invitación nombra un vínculo que ya no le corresponde (anulado, o"
            " de otra persona, unidad o carácter), o la creó quien la acepta"
            " (`invitacion_propia`, F9).",
        },
    },
)
def aceptar_propia(
    invitacion_id: UUID, request: Request, response: Response, identidad: IdentidadDep,
    motor: MotorDep,
) -> InvitacionPropiaAceptada:  # fmt: skip
    """La prueba es el control del correo, la misma que el token del enlace: la base acepta con
    `aceptar_invitacion` (0014), crea o reutiliza el vínculo y lo audita igual. Desde ahí, el
    vínculo deja de estar pendiente y sigue las reglas normales."""
    response.headers.update(SIN_CACHE)
    with transaccion(motor, persona_id=identidad.persona_id) as conn:
        r = conn.execute(
            text(
                "select resultado, consorcio"
                " from nucleo.aceptar_invitacion_propia(:i, cast(:ip as inet))"
            ),
            {"i": invitacion_id, "ip": sesiones.ip_cliente(request)},
        ).one()
    if r.resultado in MOTIVOS_INVALIDA:
        log.info("invitación propia sin servir: %s", r.resultado)
        raise _invitacion_invalida()
    if r.resultado == "vinculo_no_corresponde":
        raise _vinculo_no_corresponde()
    if r.resultado == "invitacion_propia":
        raise _invitacion_propia()
    if r.resultado != "ok":
        # `otra_persona` o `requiere_sesion`: la base eligió la invitación por mi correo, así que
        # no pueden pasar. Si pasan, es un defecto (500), no una invitación que no sirve.
        raise RuntimeError(f"aceptar_invitacion_propia respondió {r.resultado}")
    log.info(
        "invitación propia aceptada",
        extra={
            "campos": {"consorcio_id": str(r.consorcio), "persona_id": str(identidad.persona_id)}
        },
    )
    return InvitacionPropiaAceptada(consorcio_id=r.consorcio)
