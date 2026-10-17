"""Contraseña con Argon2id y entrada con correo y contraseña (documento 13, §10.3 y §15.2;
migración 0009).

**Para quién.** §10.3: "Contraseña + TOTP" es el método de administradores, consejeros y
operadores. Cualquier persona con sesión puede definir una; que sea obligatoria con el segundo
factor para los roles con facultades de dinero llega con los mandatos (H2 2b), que son los que
dicen quién tiene esos roles.

**Entrada.** `POST /entrar` con correo y contraseña. Si la persona no tiene segundo factor,
abre la sesión. Si lo tiene, no la abre: deja la cookie `__Host-cem_desafio` (5 minutos, cinco
intentos) y pide el código de la aplicación o uno de recuperación en `POST /segundo-factor`,
que abre la sesión ya verificada (TB-CEM-3). La base se niega a abrir una sesión sin el
segundo factor a quien lo tiene activo, aunque la API se equivoque.

**Misma respuesta, mismo tiempo.** Sin cuenta, cuenta sin contraseña y contraseña incorrecta
responden igual (401 `credenciales_invalidas`), después de la misma comprobación de Argon2id
(contra un hash ficticio cuando no hay cuenta) y no antes de `DURACION_MINIMA_PEDIDO`.

**Cambio.** `PUT ""` define o cambia la contraseña. Con el segundo factor activo, la sesión
tiene que haberlo verificado en los últimos 10 minutos; sin él, cambiar una existente pide la
actual. Siempre avisa por correo, y al cambiar una existente cierra las sesiones de los otros
dispositivos (quien la conocía y entró con ella queda afuera).

Toda respuesta de estas rutas lleva `Cache-Control: no-store` (`errores.sin_cache`), también
los errores que salen de las dependencias: límite de tasa, sesión, CSRF, claves y base.

Cupos: además de los 20 por minuto por IP de la familia `/api/v1/auth/`, la entrada gasta
`LIMITES_CONTRASENA_CORREO` por correo pedido y `LIMITES_CONTRASENA_IP` por IP; el paso del
segundo factor, el mismo cupo de intentos por persona que `/totp/verificar`.
"""

import logging
import secrets
import threading
import time
import unicodedata
from dataclasses import dataclass
from datetime import timedelta
from functools import cache
from uuid import UUID

from argon2 import PasswordHasher, Type
from argon2.exceptions import VerifyMismatchError
from cem_contracts.contrasena import (
    LARGO_MINIMO_CONTRASENA,
    CodigoEntrada,
    ContrasenaGuardada,
    EntradaContrasena,
    EstadoContrasena,
    FaltaSegundoFactor,
    NuevaContrasena,
    ResultadoEntrada,
    SesionAbierta,
)
from cem_contracts.errores import CodigoError, Error
from cem_contracts.identidad import SesionActual
from fastapi import APIRouter, Depends, Request, Response
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import Connection, Engine, text

from app import limite
from app.contexto import Identidad, IdentidadDep, MotorDep
from app.db import fijar_ambito, transaccion
from app.errores import ErrorApi, sin_cache
from app.identidad import router as rutas_auth
from app.identidad import segundo_factor as sf
from app.identidad import sesiones
from app.plataforma import correo

router = APIRouter(
    prefix="/api/v1/auth/contrasena", tags=["identidad"], dependencies=[Depends(sin_cache)]
)
log = logging.getLogger("cem.identidad")

# Argon2id (RFC 9106) ------------------------------------------------------------------------
ARGON2_TIEMPO = 3
ARGON2_MEMORIA_KIB = 64 * 1024
ARGON2_PARALELISMO = 4
ARGON2_LARGO_HASH = 32
ARGON2_LARGO_SAL = 16
"""Parámetros de Argon2id: 3 pasadas sobre 64 MiB con 4 carriles, hash de 32 bytes y sal de 16.

Es la segunda opción recomendada del RFC 9106 (§4), la pensada para cuando no se pueden gastar
2 GiB por hash, y queda por encima del mínimo de OWASP (19 MiB, 2 pasadas, 1 carril). La
primera opción del RFC (2 GiB) no entra en la VM: 2 OCPU y 12 GB compartidos con
AnalytikCapital (documento 13, §2.1). Se fijan acá y no se toman de los valores por defecto de
argon2-cffi para que una actualización de la biblioteca no los cambie sin que se vea en el PR.

Cada hash queda con sus parámetros (formato PHC): si cambian, los viejos se siguen verificando
y se rehacen con los nuevos la próxima vez que la persona entra (`check_needs_rehash`)."""
ARGON2_SIMULTANEOS = 2
"""Cálculos de Argon2id a la vez por proceso: 2 × 64 MiB como mucho, uno por OCPU. Sin tope,
una ráfaga de pedidos (hasta 40 hilos del grupo de FastAPI) pediría 2,5 GiB."""

HASHER = PasswordHasher(
    time_cost=ARGON2_TIEMPO,
    memory_cost=ARGON2_MEMORIA_KIB,
    parallelism=ARGON2_PARALELISMO,
    hash_len=ARGON2_LARGO_HASH,
    salt_len=ARGON2_LARGO_SAL,
    type=Type.ID,
)
_turnos = threading.BoundedSemaphore(ARGON2_SIMULTANEOS)

VIGENCIA_DESAFIO = timedelta(minutes=5)
"""Para escribir el código después de la contraseña. La migración 0009 impone el mismo tope."""
INTENTOS_DESAFIO = 5
"""Códigos fallidos que agotan el desafío. La migración 0009 impone el mismo tope."""
LIMITE_CORREO = "auth-contrasena"
LIMITE_IP = "auth-contrasena-ip"
LIMITE_CAMBIO = "auth-contrasena-cambio"

ASUNTO_DEFINIDA = "Definiste una contraseña"
ASUNTO_CAMBIADA = "Cambiaste tu contraseña"
TEXTO_AVISO = """Hola:

{que} de tu cuenta de Consorcio en Mano.{cerradas}

Si no fuiste vos, entrá con el enlace por correo, cambiá la contraseña, cerrá las sesiones de
los otros dispositivos y avisá a quien administra tu consorcio.
"""


def normalizar(contrasena: str) -> str:
    """NFKC (NIST SP 800-63B, §5.1.1.2): la misma contraseña escrita en otro teclado o sistema
    (tildes compuestas o separadas, anchos distintos) da el mismo hash."""
    return unicodedata.normalize("NFKC", contrasena)


def hashear(contrasena: str) -> str:
    with _turnos:
        return HASHER.hash(normalizar(contrasena))


@cache
def _hash_ficticio() -> str:
    """Un hash con los mismos parámetros y una contraseña que nadie conoce: sin cuenta se
    comprueba contra este, así tarda lo mismo que una contraseña incorrecta."""
    return hashear(secrets.token_urlsafe(32))


def verificar(hash_: str | None, contrasena: str) -> bool:
    """Siempre hace una comprobación completa de Argon2id. Un hash ilegible en la base no es
    una contraseña incorrecta: la excepción sigue de largo."""
    contra = hash_ or _hash_ficticio()
    with _turnos:
        try:
            return HASHER.verify(contra, normalizar(contrasena)) and hash_ is not None
        except VerifyMismatchError:
            return False


# Entrada -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Cuenta:
    persona_id: UUID
    hash: str
    con_segundo_factor: bool


def _cuenta(motor: Engine, correo_pedido: str) -> Cuenta | None:
    with transaccion(motor, persona_id=None) as conn:
        fila = conn.execute(
            text("select persona, hash, con_segundo_factor from nucleo.contrasena_de(:c)"),
            {"c": correo_pedido},
        ).one_or_none()
    return Cuenta(*fila) if fila else None


def _abrir_sesion(
    motor: Engine, cuenta: Cuenta, token_sesion: str, dispositivo: UUID, agente: str | None, ip: str
) -> tuple[str, SesionActual | None]:
    with transaccion(motor, persona_id=None) as conn:
        r = conn.execute(
            text(
                "select resultado, persona, sesion, vence from nucleo.entrar_con_contrasena("
                ":p, :h, :s, :d, :a, cast(:ip as inet), :v)"
            ),
            {
                "p": cuenta.persona_id,
                "h": cuenta.hash,
                "s": sesiones.hash_token(token_sesion),
                "d": dispositivo,
                "a": agente,
                "ip": ip,
                "v": sesiones.VIGENCIA_SESION,
            },
        ).one()
        if r.resultado != "ok":
            return r.resultado, None
        fijar_ambito(conn, persona_id=r.persona)
        nombre, correo_persona = rutas_auth.persona_de(conn, r.persona)
    return "ok", SesionActual(
        persona_id=r.persona, nombre=nombre, correo=correo_persona, sesion_id=r.sesion,
        vence_en=r.vence,
    )  # fmt: skip


def _pedir_desafio(
    motor: Engine, cuenta: Cuenta, token: str
) -> tuple[str, FaltaSegundoFactor | None]:
    with transaccion(motor, persona_id=None) as conn:
        r = conn.execute(
            text("select resultado, vence from nucleo.pedir_desafio_contrasena(:p, :h, :t, :v)"),
            {
                "p": cuenta.persona_id,
                "h": cuenta.hash,
                "t": sesiones.hash_token(token),
                "v": VIGENCIA_DESAFIO,
            },
        ).one()
    if r.resultado != "ok":
        return r.resultado, None
    return "ok", FaltaSegundoFactor(estado="falta_segundo_factor", vence_en=r.vence)


def _rehacer_si_hace_falta(motor: Engine, cuenta: Cuenta, contrasena: str) -> None:
    """Si el hash se hizo con otros parámetros, lo rehace con los vigentes. Solo si nadie lo
    cambió mientras tanto."""
    if not HASHER.check_needs_rehash(cuenta.hash):
        return
    nuevo = hashear(contrasena)
    with transaccion(motor, persona_id=cuenta.persona_id) as conn:
        conn.execute(
            text("update nucleo.contrasena set hash = :n where hash = :v"),
            {"n": nuevo, "v": cuenta.hash},
        )
    log.info("hash de contraseña rehecho", extra={"campos": {"persona_id": str(cuenta.persona_id)}})


def _exigir_mismo_origen(request: Request, que: str) -> None:
    """Contra la falsificación de inicio de sesión desde otro sitio, como `POST /auth/codigo`:
    `Sec-Fetch-Site` debe ser `same-origin` si el navegador lo declara, y el cuerpo es JSON
    (un formulario de otro sitio no lo puede mandar; `fetch` pediría una consulta CORS que la
    API no autoriza)."""
    sitio = request.headers.get(rutas_auth.ENCABEZADO_SITIO)
    if sitio is not None and sitio != rutas_auth.MISMO_ORIGEN:
        log.info("%s rechazada: otro_sitio", que)
        raise ErrorApi(
            403, CodigoError.CSRF_INVALIDO, "No pudimos verificar el pedido. Recargá la página."
        )


def _credenciales_invalidas() -> ErrorApi:
    return ErrorApi(
        401,
        CodigoError.CREDENCIALES_INVALIDAS,
        "El correo o la contraseña no son correctos. Si no tenés contraseña, entrá con el"
        " enlace por correo.",
    )


@router.post(
    "/entrar",
    response_model=ResultadoEntrada,
    summary="Entrar con correo y contraseña",
    responses={
        401: {"model": Error, "description": "No sirven (todas las causas responden igual)."}
    },
)
async def entrar(
    pedido: EntradaContrasena, request: Request, response: Response, motor: MotorDep
) -> SesionAbierta | FaltaSegundoFactor:
    """Sin segundo factor, abre la sesión y deja sus cookies (`sesion_abierta`). Con segundo
    factor, deja la cookie del desafío y pide el código en `POST /segundo-factor`
    (`falta_segundo_factor`). Todo rechazo responde 401 `credenciales_invalidas`, en el mismo
    tiempo, exista o no la cuenta."""
    inicio = time.monotonic()
    _exigir_mismo_origen(request, "entrada con contraseña")
    correo_pedido = pedido.correo.lower()
    ip = sesiones.ip_cliente(request)
    limitador = request.app.state.limitador
    for cupo in limite.LIMITES_CONTRASENA_CORREO:
        await limite.consumir(limitador, cupo, LIMITE_CORREO, limite.huella(correo_pedido))
    for cupo in limite.LIMITES_CONTRASENA_IP:
        await limite.consumir(limitador, cupo, LIMITE_IP, ip)
    cuenta = await run_in_threadpool(_cuenta, motor, correo_pedido)
    acierto = await run_in_threadpool(verificar, cuenta.hash if cuenta else None, pedido.contrasena)
    resultado: SesionAbierta | FaltaSegundoFactor | None = None
    if cuenta is None:
        motivo = "sin_cuenta"
    elif not acierto:
        motivo = "incorrecta"
    elif cuenta.con_segundo_factor:
        token = sesiones.nuevo_token()
        motivo, resultado = await run_in_threadpool(_pedir_desafio, motor, cuenta, token)
        if resultado is not None:
            sesiones.fijar_desafio(response, token, VIGENCIA_DESAFIO)
    else:
        token_sesion = sesiones.nuevo_token()
        dispositivo = sesiones.fijar_dispositivo(request, response)
        motivo, sesion = await run_in_threadpool(
            _abrir_sesion, motor, cuenta, token_sesion, dispositivo, sesiones.agente(request), ip
        )
        if sesion is not None:
            sesiones.fijar_sesion(response, token_sesion)
            resultado = SesionAbierta(estado="sesion_abierta", sesion=sesion)
    if resultado is not None:
        await run_in_threadpool(_rehacer_si_hace_falta, motor, cuenta, pedido.contrasena)
    await rutas_auth.esperar_minimo(inicio, "entrada con contraseña")
    if resultado is None:
        log.info("entrada con contraseña rechazada: %s", motivo)
        raise _credenciales_invalidas()
    log.info(
        "contraseña aceptada",
        extra={"campos": {"persona_id": str(cuenta.persona_id), "estado": resultado.estado}},
    )
    return resultado


# Paso del segundo factor -----------------------------------------------------------------


def _entrada_vencida() -> ErrorApi:
    return ErrorApi(
        401,
        CodigoError.ENTRADA_VENCIDA,
        "Pasó demasiado tiempo o hubo demasiados intentos. Volvé a escribir la contraseña.",
    )


def _persona_del_desafio(motor: Engine, token: str) -> UUID | None:
    with transaccion(motor, persona_id=None) as conn:
        return conn.execute(
            text("select nucleo.desafio_contrasena_de(:t, :m)"),
            {"t": sesiones.hash_token(token), "m": INTENTOS_DESAFIO},
        ).scalar_one()


def _resolver_desafio(
    motor: Engine,
    claves: sf.Claves,
    token: str,
    codigo: str,
    token_sesion: str,
    dispositivo: UUID,
    agente: str | None,
    ip: str,
) -> tuple[str, SesionActual | None]:
    """Comprueba el código y resuelve el desafío en una transacción que se confirma también
    cuando el código no sirve: el intento fallido queda descontado."""
    with transaccion(motor, persona_id=None) as conn:
        persona = conn.execute(
            text("select nucleo.desafio_contrasena_de(:t, :m)"),
            {"t": sesiones.hash_token(token), "m": INTENTOS_DESAFIO},
        ).scalar_one()
        if persona is None:
            return "vencido", None
        fijar_ambito(conn, persona_id=persona)
        medio = sf.comprobar_codigo(conn, claves, persona, codigo)
        r = conn.execute(
            text(
                "select resultado, persona, sesion, vence from nucleo.resolver_desafio_contrasena("
                ":t, :ok, :s, :d, :a, cast(:ip as inet), :v, :m)"
            ),
            {
                "t": sesiones.hash_token(token),
                "ok": medio is not None,
                "s": sesiones.hash_token(token_sesion),
                "d": dispositivo,
                "a": agente,
                "ip": ip,
                "v": sesiones.VIGENCIA_SESION,
                "m": INTENTOS_DESAFIO,
            },
        ).one()
        if r.resultado != "ok":
            return r.resultado, None
        nombre, correo_persona = rutas_auth.persona_de(conn, persona)
    log.info(
        "sesión creada con contraseña y segundo factor",
        extra={"campos": {"persona_id": str(persona), "medio": medio}},
    )
    return "ok", SesionActual(
        persona_id=persona, nombre=nombre, correo=correo_persona, sesion_id=r.sesion,
        vence_en=r.vence,
    )  # fmt: skip


@router.post(
    "/segundo-factor",
    response_model=SesionActual,
    summary="Completar la entrada con el código del segundo factor",
    responses={
        400: {"model": Error, "description": "El código no sirve."},
        401: {"model": Error, "description": "El paso venció, se agotó o falta su cookie."},
    },
)
async def completar_con_segundo_factor(
    pedido: CodigoEntrada,
    request: Request,
    response: Response,
    motor: MotorDep,
    claves: sf.ClavesDep,
) -> SesionActual:
    """Con el código de la aplicación o uno de recuperación, desde el navegador que escribió la
    contraseña (cookie `__Host-cem_desafio`), abre la sesión con el segundo factor ya
    verificado. Cinco códigos incorrectos agotan el paso; vale 5 minutos."""
    _exigir_mismo_origen(request, "segundo factor de la entrada")
    token = request.cookies.get(sesiones.COOKIE_DESAFIO)
    if not sesiones.token_valido(token):
        log.info("segundo factor de la entrada rechazado: sin_cookie")
        raise _entrada_vencida()
    persona = await run_in_threadpool(_persona_del_desafio, motor, token)
    if persona is None:
        log.info("segundo factor de la entrada rechazado: desafio_invalido")
        raise _entrada_vencida()
    await sf.consumir_intento(request, persona)
    token_sesion = sesiones.nuevo_token()
    dispositivo = sesiones.fijar_dispositivo(request, response)
    resultado, sesion = await run_in_threadpool(
        _resolver_desafio,
        motor,
        claves,
        token,
        pedido.codigo,
        token_sesion,
        dispositivo,
        sesiones.agente(request),
        sesiones.ip_cliente(request),
    )
    if sesion is None:
        log.info("segundo factor de la entrada rechazado: %s", resultado)
        raise sf.codigo_invalido() if resultado == "incorrecto" else _entrada_vencida()
    sesiones.fijar_sesion(response, token_sesion)
    sesiones.borrar_desafio(response)
    return sesion


# Definir y cambiar (con sesión) ----------------------------------------------------------
# Corren como `cem_app` con la persona en el ámbito: la RLS de la 0009 deja ver y tocar solo
# su propia contraseña. Las que cambian estado piden `X-CSRF-Token` (`obtener_identidad`).


@router.get("", response_model=EstadoContrasena, summary="Estado de mi contraseña")
def estado(identidad: IdentidadDep, motor: MotorDep) -> EstadoContrasena:
    with transaccion(motor, persona_id=identidad.persona_id) as conn:
        cambiada_en = conn.execute(
            text("select cambiada_en from nucleo.contrasena")
        ).scalar_one_or_none()
    return EstadoContrasena(definida=cambiada_en is not None, cambiada_en=cambiada_en)


def _guardar(motor: Engine, identidad: Identidad, pedido: NuevaContrasena) -> ContrasenaGuardada:
    persona = identidad.persona_id
    with transaccion(motor, persona_id=persona) as conn:
        para = conn.execute(
            text("select correo from nucleo.persona where id = :p"), {"p": persona}
        ).scalar_one()
        nueva = normalizar(pedido.contrasena_nueva)
        if len(nueva) < LARGO_MINIMO_CONTRASENA or nueva.casefold() == para.casefold():
            raise ErrorApi(
                422,
                CodigoError.CONTRASENA_DEBIL,
                f"La contraseña tiene que tener al menos {LARGO_MINIMO_CONTRASENA} caracteres y"
                " no puede ser tu correo. Una frase de varias palabras sirve.",
            )
        anterior = conn.execute(
            text("select hash from nucleo.contrasena for update")
        ).scalar_one_or_none()
        if _tiene_segundo_factor(conn):
            _exigir_verificacion_reciente(conn, identidad)
        elif anterior is not None and (
            pedido.contrasena_actual is None or not verificar(anterior, pedido.contrasena_actual)
        ):
            log.info("cambio de contraseña rechazado: actual_invalida")
            raise ErrorApi(
                403,
                CodigoError.CONTRASENA_ACTUAL_INVALIDA,
                "La contraseña actual no es correcta.",
            )
        cambiada_en = conn.execute(
            text(
                "insert into nucleo.contrasena (persona_id, hash) values (:p, :h)"
                " on conflict (persona_id) do update set"
                " hash = excluded.hash, cambiada_en = now()"
                " returning cambiada_en"
            ),
            {"p": persona, "h": hashear(nueva)},
        ).scalar_one()
        cerradas = 0
        if anterior is not None:
            cerradas = conn.execute(
                text(
                    "update nucleo.sesion set revocada_en = now()"
                    " where id <> :s and revocada_en is null and vence_en > now()"
                ),
                {"s": identidad.sesion_id},
            ).rowcount
        correo.solicitar(
            conn,
            para=para,
            asunto=ASUNTO_CAMBIADA if anterior else ASUNTO_DEFINIDA,
            texto=TEXTO_AVISO.format(
                que="Se cambió la contraseña" if anterior else "Se definió una contraseña",
                cerradas=" Cerramos las sesiones de los otros dispositivos." if cerradas else "",
            ),
        )
    log.info(
        "contraseña cambiada" if anterior else "contraseña definida",
        extra={"campos": {"persona_id": str(persona), "sesiones_cerradas": cerradas}},
    )
    return ContrasenaGuardada(cambiada_en=cambiada_en, sesiones_cerradas=cerradas)


def _tiene_segundo_factor(conn: Connection) -> bool:
    return conn.execute(
        text("select exists (select 1 from nucleo.factor_totp where confirmado_en is not null)")
    ).scalar_one()


def _exigir_verificacion_reciente(conn: Connection, identidad: Identidad) -> None:
    """TB-CEM-3, como `SegundoFactorRecienteDep`, pero solo cuando hay segundo factor."""
    reciente = conn.execute(
        text("select segundo_factor_en > now() - :v from nucleo.sesion where id = :s"),
        {"s": identidad.sesion_id, "v": sf.VIGENCIA_VERIFICACION},
    ).scalar_one()
    if not reciente:
        log.info("cambio de contraseña rechazado: sin_verificacion_reciente")
        raise ErrorApi(
            403,
            CodigoError.SEGUNDO_FACTOR_REQUERIDO,
            "Para esto tenés que confirmar con el código de tu aplicación de autenticación.",
        )


@router.put(
    "",
    response_model=ContrasenaGuardada,
    summary="Definir o cambiar mi contraseña",
    responses={
        403: {
            "model": Error,
            "description": "Falta la verificación reciente del segundo factor o la contraseña"
            " actual no es correcta.",
        },
        422: {"model": Error, "description": "La contraseña nueva es demasiado débil."},
    },
)
async def guardar(
    pedido: NuevaContrasena, request: Request, identidad: IdentidadDep, motor: MotorDep
) -> ContrasenaGuardada:
    """Define la contraseña o la cambia, y avisa por correo. Con el segundo factor activo pide
    haberlo verificado en esta sesión en los últimos 10 minutos (TB-CEM-3); sin él, cambiar
    una existente pide la actual. Cambiarla cierra las sesiones de los otros dispositivos."""
    for cupo in limite.LIMITES_CAMBIO_CONTRASENA:
        await limite.consumir(
            request.app.state.limitador, cupo, LIMITE_CAMBIO, str(identidad.persona_id)
        )
    return await run_in_threadpool(_guardar, motor, identidad, pedido)
