"""Rutas de autenticación: enlace mágico, código por correo, canje, sesión, salida y sesiones de
la persona (documento 13, §10.3 y §10.4; RF04).

Todas viven bajo `/api/v1/auth/`, la familia de autenticación del límite de tasa (§10.6): 20
pedidos por minuto por IP (middleware); al pedir la entrada, 5 por minuto por correo; al
canjear un código, 10 por hora y 20 por día por correo.
"""

import asyncio
import logging
import re
import time
from datetime import timedelta
from uuid import UUID

from cem_contracts.errores import CodigoError
from cem_contracts.identidad import (
    CanjeCodigo,
    CanjeEnlace,
    EnlacePedido,
    ListaSesiones,
    PedidoEnlace,
    SesionActual,
    SesionDispositivo,
    SesionesRevocadas,
)
from fastapi import APIRouter, Request, Response
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import Connection, Engine, text

from app import limite
from app.contexto import Identidad, IdentidadDep, MotorDep
from app.db import fijar_ambito, transaccion
from app.errores import ErrorApi
from app.identidad import dispositivos, eventos, sesiones

router = APIRouter(prefix="/api/v1/auth", tags=["identidad"])
log = logging.getLogger("cem.identidad")

DURACION_MINIMA_PEDIDO = timedelta(milliseconds=400)
"""Ningún pedido de entrada ni canje de código responde antes: con cuenta o sin ella tarda lo
mismo (§10.3). Debe superar holgadamente lo que tarda el camino con cuenta; si no, se registra
una advertencia."""
FORMATO_ORIGEN = re.compile(r"^https://[a-z0-9.-]+(:[0-9]{1,5})?$")
RUTA_ENTRAR = "/entrar"
"""Página del frontend que abre el enlace del correo. El token va en el fragmento (`#token=`):
el navegador nunca lo manda al servidor, así que no queda en el registro de acceso de nginx ni
en `Referer`. La página lo lee, lo borra de la URL y lo manda en el cuerpo de `POST /canje`.
La misma página tiene el formulario del código por correo."""
MENSAJE_PEDIDO = (
    "Si el correo corresponde a una cuenta, en unos minutos te llega un enlace para entrar."
    " Abrilo en este mismo navegador o, desde otro dispositivo, usá el código del mismo correo."
)
SIN_CACHE = {"Cache-Control": "no-store"}
ENCABEZADO_SITIO = "Sec-Fetch-Site"
MISMO_ORIGEN = "same-origin"
LIMITE_CODIGO = "auth-codigo"
"""Nombre del cupo de intentos de código por correo en Redis."""


def validar_origen(origen: str) -> None:
    """`CEM_ORIGEN_PUBLICO` mal escrito impide el arranque: el enlace saldría roto."""
    if not FORMATO_ORIGEN.fullmatch(origen):
        raise RuntimeError("CEM_ORIGEN_PUBLICO debe ser https://<dominio> sin barra final ni ruta")


def _clave_codigo(request: Request) -> bytes:
    """La clave del HMAC de los códigos. Sin ella no se puede pedir la entrada ni canjear un
    código: el correo llevaría un código que nadie podría comprobar."""
    valor = request.app.state.config.codigo_clave
    if not valor:
        log.error("CEM_CODIGO_CLAVE sin configurar: no se pueden emitir ni comprobar códigos")
        raise ErrorApi(503, CodigoError.SERVICIO_NO_DISPONIBLE, "El servicio no está disponible.")
    return sesiones.clave_codigo(valor)


async def _esperar_minimo(inicio: float, que: str) -> None:
    """Completa `DURACION_MINIMA_PEDIDO` desde `inicio`, o avisa si ya se pasó."""
    restante = DURACION_MINIMA_PEDIDO.total_seconds() - (time.monotonic() - inicio)
    if restante > 0:
        await asyncio.sleep(restante)
    else:
        log.warning(
            "el %s superó la duración mínima: el tiempo puede delatar la cuenta",
            que,
            extra={"campos": {"duracion_ms": round((time.monotonic() - inicio) * 1000, 1)}},
        )


def _registrar_pedido(
    motor: Engine, correo: str, nonce: str, dispositivo: UUID, ip: str, origen: str, clave: bytes
) -> bool:
    """Registra el enlace y su código y publica el correo en una transacción. Devuelve si hay
    cuenta. El código y su HMAC se calculan siempre, haya cuenta o no: mismo trabajo."""
    token, codigo = sesiones.nuevo_token(), sesiones.nuevo_codigo()
    codigo_hash = sesiones.hash_codigo(clave, correo, codigo)
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
            conn.execute(
                text("select nucleo.registrar_codigo(:t, :h, :v)"),
                {"t": sesiones.hash_token(token), "h": codigo_hash, "v": sesiones.VIGENCIA_CODIGO},
            )
            eventos.solicitar_enlace(
                conn,
                para=correo,
                enlace=f"{origen}{RUTA_ENTRAR}#token={token}",
                minutos=int(sesiones.VIGENCIA_ENLACE.total_seconds() // 60),
                codigo=codigo,
                minutos_codigo=int(sesiones.VIGENCIA_CODIGO.total_seconds() // 60),
            )
    return hay_cuenta


@router.post(
    "/enlace",
    status_code=202,
    response_model=EnlacePedido,
    summary="Pedir la entrada: enlace y código por correo",
)
async def pedir_enlace(
    pedido: PedidoEnlace, request: Request, response: Response, motor: MotorDep
) -> EnlacePedido:
    """Responde siempre 202 con el mismo cuerpo y en el mismo tiempo, exista o no la cuenta.
    Deja en el navegador la cookie `nonce` a la que queda ligado el enlace. El mismo correo
    lleva un código de seis dígitos para entrar desde otro dispositivo (`POST /codigo`)."""
    inicio = time.monotonic()
    origen = request.app.state.config.origen_publico
    if not origen:
        log.error("CEM_ORIGEN_PUBLICO sin configurar: no se puede armar el enlace de acceso")
        raise ErrorApi(503, CodigoError.SERVICIO_NO_DISPONIBLE, "El servicio no está disponible.")
    clave = _clave_codigo(request)
    correo = pedido.correo.lower()
    await limite.consumir(
        request.app.state.limitador,
        limite.LIMITE_AUTENTICACION_CORREO,
        "auth-correo",
        limite.huella(correo),
    )
    nonce, dispositivo = sesiones.fijar_nonce_y_dispositivo(request, response)
    hay_cuenta = await run_in_threadpool(
        _registrar_pedido,
        motor,
        correo,
        nonce,
        dispositivo,
        sesiones.ip_cliente(request),
        origen,
        clave,
    )
    log.info("enlace de acceso pedido", extra={"campos": {"cuenta_existe": hay_cuenta}})
    await _esperar_minimo(inicio, "pedido de enlace")
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


# Código por correo (§10.3) ----------------------------------------------------------------


def _canjear_codigo(
    motor: Engine,
    correo: str,
    codigo_hash: bytes,
    token_sesion: str,
    dispositivo: UUID,
    agente: str | None,
    ip: str,
) -> tuple[str, SesionActual | None]:
    """Canjea en una transacción que se confirma también cuando el código no sirve: el intento
    fallido queda descontado. Devuelve el resultado de `nucleo.canjear_codigo` y, si es 'ok', la
    sesión creada."""
    with transaccion(motor, persona_id=None) as conn:
        r = conn.execute(
            text(
                "select resultado, persona, sesion, vence from nucleo.canjear_codigo("
                ":c, :h, :s, :d, :a, cast(:ip as inet), :v, :m)"
            ),
            {
                "c": correo,
                "h": codigo_hash,
                "s": sesiones.hash_token(token_sesion),
                "d": dispositivo,
                "a": agente,
                "ip": ip,
                "v": sesiones.VIGENCIA_SESION,
                "m": sesiones.INTENTOS_CODIGO,
            },
        ).one()
        if r.resultado != "ok":
            return r.resultado, None
        fijar_ambito(conn, persona_id=r.persona)
        nombre, correo_persona = _persona(conn, r.persona)
    return r.resultado, SesionActual(
        persona_id=r.persona,
        nombre=nombre,
        correo=correo_persona,
        sesion_id=r.sesion,
        vence_en=r.vence,
    )


@router.post(
    "/codigo",
    response_model=SesionActual,
    summary="Entrar con el código del correo",
    responses={401: {"description": "El código no sirve (todas las causas responden igual)."}},
)
async def canjear_codigo(
    canje: CanjeCodigo, request: Request, response: Response, motor: MotorDep
) -> SesionActual:
    """Entra con el correo y el código de seis dígitos del último pedido, desde cualquier
    dispositivo. Crea la sesión como el canje del enlace y gasta el pedido entero (el enlace
    deja de servir). Todo rechazo responde igual, 401 `codigo_invalido`, y en el mismo tiempo,
    exista o no la cuenta.

    Vale 10 minutos y cinco intentos fallidos; al quinto queda agotado aunque después llegue el
    correcto. Además, cada correo tiene un cupo de intentos (10 por hora, 20 por día) que no
    depende de la IP.

    No pide el `nonce`: sirve justamente en otro navegador. Lo que lo reemplaza contra una
    falsificación de inicio de sesión desde otro sitio: `Sec-Fetch-Site` debe ser `same-origin`
    si el navegador lo declara, y el cuerpo debe ser `application/json` (FastAPI 0.141 no
    interpreta otro tipo ni un cuerpo sin tipo: 422), que un formulario de otro sitio no puede
    mandar y que desde `fetch` obliga a una consulta previa CORS que la API no autoriza. Lo
    demás lo cubre que el código solo llega al correo de la persona."""
    inicio = time.monotonic()
    response.headers.update(SIN_CACHE)
    sitio = request.headers.get(ENCABEZADO_SITIO)
    if sitio is not None and sitio != MISMO_ORIGEN:
        log.info("canje de código rechazado: otro_sitio")
        raise ErrorApi(
            403, CodigoError.CSRF_INVALIDO, "No pudimos verificar el pedido. Recargá la página."
        )
    clave = _clave_codigo(request)
    correo = canje.correo.lower()
    for cupo in limite.LIMITES_CODIGO_CORREO:
        await limite.consumir(
            request.app.state.limitador, cupo, LIMITE_CODIGO, limite.huella(correo)
        )
    token_sesion = sesiones.nuevo_token()
    dispositivo = sesiones.fijar_dispositivo(request, response)
    resultado, sesion = await run_in_threadpool(
        _canjear_codigo,
        motor,
        correo,
        sesiones.hash_codigo(clave, correo, canje.codigo),
        token_sesion,
        dispositivo,
        sesiones.agente(request),
        sesiones.ip_cliente(request),
    )
    await _esperar_minimo(inicio, "canje de código")
    if sesion is None:
        log.info("canje de código rechazado: %s", resultado)
        raise ErrorApi(
            401,
            CodigoError.CODIGO_INVALIDO,
            "El código no sirve: puede estar mal escrito, haber vencido o haberse usado."
            " Revisalo o pedí uno nuevo.",
            encabezados=SIN_CACHE,
        )
    log.info("sesión creada con código", extra={"campos": {"persona_id": str(sesion.persona_id)}})
    sesiones.fijar_sesion(response, token_sesion)
    return sesion


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


# Sesiones de la persona (RF04) -------------------------------------------------------------
# Las tres consultas corren como `cem_app` con la persona en el ámbito: la RLS de
# `nucleo.sesion` (migración 0004) deja ver y revocar solo las propias. Una sesión ajena no se
# distingue de una inexistente. Las dos que revocan son `POST` con sesión: `obtener_identidad`
# exige el `X-CSRF-Token`. No llevan `Idempotency-Key`: no crean dinero, cargos ni expedientes
# (§7.4) y repetirlas deja el mismo estado.


def _sesion_actual(identidad: Identidad) -> UUID:
    if identidad.sesion_id is None:
        raise RuntimeError("las rutas de sesiones necesitan la sesión de la petición")
    return identidad.sesion_id


@router.get("/sesiones", response_model=ListaSesiones, summary="Mis sesiones vigentes")
def listar_sesiones(identidad: IdentidadDep, motor: MotorDep, response: Response) -> ListaSesiones:
    """Las sesiones vigentes (ni vencidas ni revocadas) de la persona, con la actual marcada.
    El dispositivo es una descripción del navegador; ni el agente crudo ni la IP salen."""
    response.headers.update(SIN_CACHE)
    actual = _sesion_actual(identidad)
    with transaccion(motor, persona_id=identidad.persona_id) as conn:
        filas = conn.execute(
            text(
                "select id, agente, creada_en, ultimo_uso_en, vence_en from nucleo.sesion"
                " where revocada_en is null and vence_en > now()"
                " order by ultimo_uso_en desc, id desc"
            )
        ).all()
    return ListaSesiones(
        elementos=[
            SesionDispositivo(
                id=f.id,
                dispositivo=dispositivos.describir(f.agente),
                creada_en=f.creada_en,
                ultimo_uso_en=f.ultimo_uso_en,
                vence_en=f.vence_en,
                actual=f.id == actual,
            )
            for f in filas
        ]
    )


@router.post(
    "/sesiones/revocar-otras",
    response_model=SesionesRevocadas,
    summary="Cerrar las sesiones de los otros dispositivos",
)
def revocar_otras(
    identidad: IdentidadDep, motor: MotorDep, response: Response
) -> SesionesRevocadas:
    """Revoca todas las sesiones vigentes de la persona menos la de esta petición."""
    response.headers.update(SIN_CACHE)
    actual = _sesion_actual(identidad)
    with transaccion(motor, persona_id=identidad.persona_id) as conn:
        revocadas = conn.execute(
            text(
                "update nucleo.sesion set revocada_en = now()"
                " where id <> :s and revocada_en is null and vence_en > now()"
            ),
            {"s": actual},
        ).rowcount
    log.info(
        "otras sesiones revocadas",
        extra={"campos": {"persona_id": str(identidad.persona_id), "revocadas": revocadas}},
    )
    return SesionesRevocadas(revocadas=revocadas)


@router.post(
    "/sesiones/{sesion_id}/revocar",
    status_code=204,
    response_class=Response,
    summary="Cerrar una sesión",
    responses={404: {"description": "La sesión no existe o no es de la persona."}},
)
def revocar_sesion(sesion_id: UUID, identidad: IdentidadDep, motor: MotorDep) -> Response:
    """Revoca una sesión propia. Repetirlo responde lo mismo y conserva la fecha de la primera
    revocación. Si es la actual, además borra sus cookies, como `salir`."""
    actual = _sesion_actual(identidad)
    with transaccion(motor, persona_id=identidad.persona_id) as conn:
        propia = conn.execute(
            text(
                "update nucleo.sesion set revocada_en = coalesce(revocada_en, now())"
                " where id = :s returning id"
            ),
            {"s": sesion_id},
        ).one_or_none()
    if propia is None:
        raise ErrorApi(404, CodigoError.NO_ENCONTRADO, "No encontramos lo que buscás.")
    log.info("sesión revocada", extra={"campos": {"persona_id": str(identidad.persona_id)}})
    respuesta = Response(status_code=204, headers=SIN_CACHE)
    if sesion_id == actual:
        sesiones.borrar_sesion(respuesta)
    return respuesta
