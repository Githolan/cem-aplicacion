"""Rutas de autenticación: enlace mágico, canje, sesión y salida (documento 13, §10.3 y §10.4).

Todas viven bajo `/api/v1/auth/`, la familia de autenticación del límite de tasa (§10.6): 20
pedidos por minuto por IP (middleware) y, al pedir un enlace, 5 por minuto por correo.
"""

import asyncio
import logging
import re
import time
from datetime import timedelta
from uuid import UUID

from cem_contracts.errores import CodigoError
from cem_contracts.identidad import CanjeEnlace, EnlacePedido, PedidoEnlace, SesionActual
from fastapi import APIRouter, Request, Response
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import Connection, Engine, text

from app import limite
from app.contexto import IdentidadDep, MotorDep
from app.db import fijar_ambito, transaccion
from app.errores import ErrorApi
from app.identidad import eventos, sesiones

router = APIRouter(prefix="/api/v1/auth", tags=["identidad"])
log = logging.getLogger("cem.identidad")

DURACION_MINIMA_PEDIDO = timedelta(milliseconds=400)
"""Ningún pedido de enlace responde antes: con cuenta o sin ella tarda lo mismo (§10.3). Debe
superar holgadamente lo que tarda el camino con cuenta; si no, se registra una advertencia."""
FORMATO_ORIGEN = re.compile(r"^https://[a-z0-9.-]+(:[0-9]{1,5})?$")
RUTA_ENTRAR = "/entrar"
"""Página del frontend que abre el enlace del correo. El token va en el fragmento (`#token=`):
el navegador nunca lo manda al servidor, así que no queda en el registro de acceso de nginx ni
en `Referer`. La página lo lee, lo borra de la URL y lo manda en el cuerpo de `POST /canje`."""
MENSAJE_PEDIDO = (
    "Si el correo corresponde a una cuenta, en unos minutos te llega un enlace para entrar."
    " Abrilo en este mismo navegador."
)
SIN_CACHE = {"Cache-Control": "no-store"}
ENCABEZADO_SITIO = "Sec-Fetch-Site"
MISMO_ORIGEN = "same-origin"


def validar_origen(origen: str) -> None:
    """`CEM_ORIGEN_PUBLICO` mal escrito impide el arranque: el enlace saldría roto."""
    if not FORMATO_ORIGEN.fullmatch(origen):
        raise RuntimeError("CEM_ORIGEN_PUBLICO debe ser https://<dominio> sin barra final ni ruta")


def _registrar_pedido(
    motor: Engine, correo: str, nonce: str, dispositivo: UUID, ip: str, origen: str
) -> bool:
    """Registra el enlace y publica el correo en una transacción. Devuelve si hay cuenta."""
    token = sesiones.nuevo_token()
    with transaccion(motor, persona_id=None) as conn:
        hay_cuenta = conn.execute(
            text("select nucleo.pedir_enlace(:c, :t, :n, :d, cast(:ip as inet), :v)"),
            {
                "c": correo,
                "t": sesiones.hash_token(token),
                "n": sesiones.hash_token(nonce),
                "d": dispositivo,
                "ip": ip,
                "v": sesiones.VIGENCIA_ENLACE,
            },
        ).scalar_one()
        if hay_cuenta:
            eventos.solicitar_enlace(
                conn,
                para=correo,
                enlace=f"{origen}{RUTA_ENTRAR}#token={token}",
                minutos=int(sesiones.VIGENCIA_ENLACE.total_seconds() // 60),
            )
    return hay_cuenta


@router.post(
    "/enlace",
    status_code=202,
    response_model=EnlacePedido,
    summary="Pedir un enlace de acceso por correo",
)
async def pedir_enlace(
    pedido: PedidoEnlace, request: Request, response: Response, motor: MotorDep
) -> EnlacePedido:
    """Responde siempre 202 con el mismo cuerpo y en el mismo tiempo, exista o no la cuenta.
    Deja en el navegador la cookie `nonce` a la que queda ligado el enlace."""
    inicio = time.monotonic()
    origen = request.app.state.config.origen_publico
    if not origen:
        log.error("CEM_ORIGEN_PUBLICO sin configurar: no se puede armar el enlace de acceso")
        raise ErrorApi(503, CodigoError.SERVICIO_NO_DISPONIBLE, "El servicio no está disponible.")
    correo = pedido.correo.lower()
    await limite.consumir(
        request.app.state.limitador,
        limite.LIMITE_AUTENTICACION_CORREO,
        "auth-correo",
        limite.huella(correo),
    )
    nonce, dispositivo = sesiones.fijar_nonce_y_dispositivo(request, response)
    hay_cuenta = await run_in_threadpool(
        _registrar_pedido, motor, correo, nonce, dispositivo, sesiones.ip_cliente(request), origen
    )
    log.info("enlace de acceso pedido", extra={"campos": {"cuenta_existe": hay_cuenta}})
    restante = DURACION_MINIMA_PEDIDO.total_seconds() - (time.monotonic() - inicio)
    if restante > 0:
        await asyncio.sleep(restante)
    else:
        log.warning(
            "el pedido de enlace superó la duración mínima: el tiempo puede delatar la cuenta",
            extra={"campos": {"duracion_ms": round((time.monotonic() - inicio) * 1000, 1)}},
        )
    response.headers.update(SIN_CACHE)
    return EnlacePedido(estado="pedido", mensaje=MENSAJE_PEDIDO)


def _persona(conn: Connection, persona_id: UUID) -> tuple[str, str]:
    nombre, correo = conn.execute(
        text("select nombre, correo from nucleo.persona where id = :p"), {"p": persona_id}
    ).one()
    return nombre, correo


@router.post("/canje", response_model=SesionActual, summary="Canjear el enlace de acceso")
def canjear(
    canje: CanjeEnlace, request: Request, response: Response, motor: MotorDep
) -> SesionActual:
    """Valida el token (existe, no venció, no se usó) y que el navegador tenga la cookie
    `nonce` con la que se pidió; crea la sesión y deja sus cookies. Todo rechazo responde
    igual: 401 `enlace_invalido`.

    Sin token CSRF, porque todavía no hay sesión. La protección equivalente es el nonce: la
    cookie es `SameSite=Lax`, así que un `POST` desde otro sitio llega sin ella y se rechaza; y
    aunque llegara, el token canjeado tendría que ser uno pedido desde este mismo navegador. Una
    falsificación de inicio de sesión (meter a la víctima en la cuenta del atacante) necesita el
    nonce de la víctima ligado al token del atacante, y no lo tiene. Además, si el navegador
    declara `Sec-Fetch-Site` y no es `same-origin`, se rechaza."""
    response.headers.update(SIN_CACHE)
    sitio = request.headers.get(ENCABEZADO_SITIO)
    if sitio is not None and sitio != MISMO_ORIGEN:
        log.info("canje rechazado: otro_sitio")
        raise ErrorApi(
            403, CodigoError.CSRF_INVALIDO, "No pudimos verificar el pedido. Recargá la página."
        )
    token = canje.token
    nonce = request.cookies.get(sesiones.COOKIE_NONCE)
    if not sesiones.token_valido(nonce):
        log.info("canje rechazado: sin_nonce")
        raise _enlace_invalido()
    token_sesion = sesiones.nuevo_token()
    with transaccion(motor, persona_id=None) as conn:
        r = conn.execute(
            text(
                "select resultado, persona, sesion, vence"
                " from nucleo.canjear_enlace(:t, :n, :s, :a, cast(:ip as inet), :v)"
            ),
            {
                "t": sesiones.hash_token(token),
                "n": sesiones.hash_token(nonce),
                "s": sesiones.hash_token(token_sesion),
                "a": sesiones.agente(request),
                "ip": sesiones.ip_cliente(request),
                "v": sesiones.VIGENCIA_SESION,
            },
        ).one()
        if r.resultado != "ok":
            log.info("canje rechazado: %s", r.resultado)
            raise _enlace_invalido()
        fijar_ambito(conn, persona_id=r.persona)
        nombre, correo = _persona(conn, r.persona)
    log.info("sesión creada", extra={"campos": {"persona_id": str(r.persona)}})
    sesiones.fijar_sesion(response, token_sesion)
    sesiones.borrar_nonce(response)
    return SesionActual(
        persona_id=r.persona, nombre=nombre, correo=correo, sesion_id=r.sesion, vence_en=r.vence
    )


def _enlace_invalido() -> ErrorApi:
    return ErrorApi(
        401,
        CodigoError.ENLACE_INVALIDO,
        "El enlace no sirve: venció, ya se usó o se abrió en otro navegador. Pedí uno nuevo.",
    )


@router.get("/sesion", response_model=SesionActual, summary="Quién soy")
def sesion_actual(identidad: IdentidadDep, motor: MotorDep, response: Response) -> SesionActual:
    response.headers.update(SIN_CACHE)
    with transaccion(motor, persona_id=identidad.persona_id) as conn:
        vence_en = conn.execute(
            text("select vence_en from nucleo.sesion where id = :s"),
            {"s": identidad.sesion_id},
        ).scalar_one()
        nombre, correo = _persona(conn, identidad.persona_id)
    return SesionActual(
        persona_id=identidad.persona_id,
        nombre=nombre,
        correo=correo,
        sesion_id=identidad.sesion_id,
        vence_en=vence_en,
    )


@router.post("/salir", status_code=204, response_class=Response, summary="Cerrar la sesión")
def salir(identidad: IdentidadDep, motor: MotorDep) -> Response:
    """Revoca la sesión actual y borra sus cookies. Pide `X-CSRF-Token`."""
    with transaccion(motor, persona_id=identidad.persona_id) as conn:
        conn.execute(
            text(
                "update nucleo.sesion set revocada_en = now() where id = :s and revocada_en is null"
            ),
            {"s": identidad.sesion_id},
        )
    log.info("sesión revocada", extra={"campos": {"persona_id": str(identidad.persona_id)}})
    respuesta = Response(status_code=204)
    sesiones.borrar_sesion(respuesta)
    return respuesta
