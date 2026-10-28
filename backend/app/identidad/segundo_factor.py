"""Segundo factor TOTP, códigos de recuperación y verificación reciente (documento 13, §10.3 y
TB-CEM-3; migración 0008).

**TOTP (RFC 6238).** HMAC-SHA1, seis dígitos, pasos de 30 segundos: lo que entienden todas las
aplicaciones de autenticación. Se acepta el paso actual y uno a cada lado (relojes de teléfono
corridos), y nunca uno igual o anterior al último aceptado: un código visto por encima del
hombro no se puede volver a usar.

**Claves.** El secreto se guarda cifrado con AES-256-GCM (el nonce va adelante; la persona es
dato asociado, así que un secreto copiado a otra fila no descifra). Los códigos de recuperación,
como HMAC-SHA256. Las dos claves se derivan con HKDF de `CEM_CODIGO_CLAVE`, la que ya existe
solo en `cem-backend`: no hay un secreto más que administrar por entorno. Cambiar esa clave
invalida los segundos factores dados de alta (hay que volver a darlos de alta).

**Verificación reciente (TB-CEM-3).** `SegundoFactorRecienteDep` exige que la sesión de la
petición haya verificado el segundo factor en los últimos 10 minutos. Hoy la usa la baja; la
usarán las acciones privilegiadas (aprobar egresos, cambiar beneficiarios o el modo, exportar)
y, con los mandatos de H2 2b, el segundo factor se vuelve obligatorio para los roles de dinero.

Cupo de intentos: 5 por minuto y 20 por día por persona, acierten o no.
"""

import base64
import hashlib
import hmac
import logging
import secrets
import struct
import time
from datetime import datetime
from typing import Annotated
from urllib.parse import quote, urlencode
from uuid import UUID

from cem_contracts.errores import CodigoError, Error
from cem_contracts.segundo_factor import (
    AltaTotp,
    CodigosRecuperacion,
    CodigoTotp,
    EstadoSegundoFactor,
    SegundoFactorVerificado,
    VerificacionSegundoFactor,
)
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from fastapi import APIRouter, Depends, Request, Response
from limits import RateLimitItem, parse
from sqlalchemy import Connection, text

from app import limite
from app.contexto import Identidad, IdentidadDep, MotorDep
from app.db import transaccion
from app.errores import ErrorApi
from app.identidad import sesiones
from app.plataforma import correo

router = APIRouter(prefix="/api/v1/auth/totp", tags=["identidad"])
log = logging.getLogger("cem.identidad")

PASO_S = 30
DIGITOS = 6
TOLERANCIA_PASOS = 1
"""Pasos aceptados a cada lado del actual: ±30 segundos de reloj corrido."""
BYTES_SECRETO = 20
"""160 bits, lo que recomienda el RFC 4226 para HMAC-SHA1."""
EMISOR = "Consorcio en Mano"
CANTIDAD_RECUPERACION = 10
ALFABETO_RECUPERACION = "abcdefghijkmnpqrstuvwxyz23456789"
"""32 símbolos sin 0, 1, o ni l: 50 bits por código de diez."""
VIGENCIA_VERIFICACION = sesiones.VIGENCIA_SEGUNDO_FACTOR_RECIENTE
"""TB-CEM-3: cuánto cuenta como reciente una verificación (la misma que mira R10)."""
LIMITES_INTENTOS: tuple[RateLimitItem, ...] = (parse("5/minute"), parse("20/day"))
"""Intentos de código (alta, verificación o baja) por persona. Con ±1 paso hay 3 códigos
válidos en 10⁶: 20 por día dan 0,006 % de acertar al azar en un día."""
LIMITE_NOMBRE = "auth-totp"
SIN_CACHE = {"Cache-Control": "no-store"}

ASUNTO_ALTA = "Activaste la verificación en dos pasos"
ASUNTO_BAJA = "Desactivaste la verificación en dos pasos"
TEXTO_AVISO = """Hola:

{que} la verificación en dos pasos de tu cuenta de Consorcio en Mano.

Si no fuiste vos, entrá, cerrá las sesiones de los otros dispositivos y avisá a quien administra
tu consorcio.
"""


# Primitivas --------------------------------------------------------------------------------


def _derivar(clave_codigo: bytes, etiqueta: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=etiqueta).derive(clave_codigo)


class Claves:
    """Las dos claves del segundo factor, derivadas de `CEM_CODIGO_CLAVE`."""

    def __init__(self, clave_codigo: bytes) -> None:
        self.cifrado = AESGCM(_derivar(clave_codigo, b"cem-totp-cifrado"))
        self.recuperacion = _derivar(clave_codigo, b"cem-totp-recuperacion")

    def cifrar(self, persona_id: UUID, secreto: bytes) -> bytes:
        nonce = secrets.token_bytes(12)
        return nonce + self.cifrado.encrypt(nonce, secreto, persona_id.bytes)

    def descifrar(self, persona_id: UUID, cifrado: bytes) -> bytes:
        return self.cifrado.decrypt(cifrado[:12], cifrado[12:], persona_id.bytes)

    def hash_recuperacion(self, persona_id: UUID, codigo: str) -> bytes:
        return hmac.new(
            self.recuperacion, persona_id.bytes + codigo.encode("ascii"), hashlib.sha256
        ).digest()


def codigo_en(secreto: bytes, paso: int) -> str:
    """HOTP (RFC 4226) del paso: el TOTP de ese intervalo de 30 segundos."""
    digest = hmac.new(secreto, struct.pack(">Q", paso), hashlib.sha1).digest()
    corrimiento = digest[-1] & 0x0F
    valor = struct.unpack(">I", digest[corrimiento : corrimiento + 4])[0] & 0x7FFFFFFF
    return f"{valor % 10**DIGITOS:0{DIGITOS}d}"


def paso_actual(ahora: float | None = None) -> int:
    return int((time.time() if ahora is None else ahora) // PASO_S)


def paso_valido(secreto: bytes, codigo: str, ultimo_paso: int, ahora: float | None = None):
    """El paso del código si coincide con alguno de la ventana posterior a `ultimo_paso`; si
    no, None. Compara siempre los tres en tiempo constante."""
    actual = paso_actual(ahora)
    acertado = None
    for paso in range(actual - TOLERANCIA_PASOS, actual + TOLERANCIA_PASOS + 1):
        if hmac.compare_digest(codigo_en(secreto, paso), codigo) and paso > ultimo_paso:
            acertado = paso
    return acertado


def base32(secreto: bytes) -> str:
    return base64.b32encode(secreto).decode("ascii").rstrip("=")


def uri_otpauth(secreto: bytes, cuenta: str) -> str:
    etiqueta = quote(f"{EMISOR}:{cuenta}", safe="")
    consulta = urlencode(
        {"secret": base32(secreto), "issuer": EMISOR, "algorithm": "SHA1", "digits": DIGITOS,
         "period": PASO_S},
        quote_via=quote,
    )  # fmt: skip
    return f"otpauth://totp/{etiqueta}?{consulta}"


def nuevo_codigo_recuperacion() -> str:
    letras = "".join(secrets.choice(ALFABETO_RECUPERACION) for _ in range(10))
    return f"{letras[:5]}-{letras[5:]}"


# Dependencias ------------------------------------------------------------------------------


def _claves(request: Request) -> Claves:
    valor = request.app.state.config.codigo_clave
    if not valor:
        log.error("CEM_CODIGO_CLAVE sin configurar: no se puede usar el segundo factor")
        raise ErrorApi(503, CodigoError.SERVICIO_NO_DISPONIBLE, "El servicio no está disponible.")
    return Claves(sesiones.clave_codigo(valor))


ClavesDep = Annotated[Claves, Depends(_claves)]


def _sesion(identidad: Identidad) -> UUID:
    if identidad.sesion_id is None:
        raise RuntimeError("el segundo factor necesita la sesión de la petición")
    return identidad.sesion_id


def _verificado_en(conn: Connection, sesion_id: UUID) -> datetime | None:
    return conn.execute(
        text("select segundo_factor_en from nucleo.sesion where id = :s"), {"s": sesion_id}
    ).scalar_one()


def exigir_segundo_factor_reciente(identidad: IdentidadDep, motor: MotorDep) -> Identidad:
    """TB-CEM-3: la sesión verificó el segundo factor hace menos de 10 minutos."""
    with transaccion(motor, persona_id=identidad.persona_id) as conn:
        reciente = conn.execute(
            text("select segundo_factor_en > now() - :v from nucleo.sesion where id = :s"),
            {"s": _sesion(identidad), "v": VIGENCIA_VERIFICACION},
        ).scalar_one()
    if not reciente:
        raise ErrorApi(
            403,
            CodigoError.SEGUNDO_FACTOR_REQUERIDO,
            "Para esto tenés que confirmar con el código de tu aplicación de autenticación.",
        )
    return identidad


SegundoFactorRecienteDep = Annotated[Identidad, Depends(exigir_segundo_factor_reciente)]


async def consumir_intento(request: Request, persona_id: UUID) -> None:
    for cupo in LIMITES_INTENTOS:
        await limite.consumir(request.app.state.limitador, cupo, LIMITE_NOMBRE, str(persona_id))


def codigo_invalido() -> ErrorApi:
    return ErrorApi(
        400,
        CodigoError.SEGUNDO_FACTOR_INVALIDO,
        "El código no sirve. Revisá que la hora del teléfono esté bien y usá el código nuevo.",
        encabezados=SIN_CACHE,
    )


def _avisar(conn: Connection, persona_id: UUID, asunto: str, que: str) -> None:
    para = conn.execute(
        text("select correo from nucleo.persona where id = :p"), {"p": persona_id}
    ).scalar_one()
    correo.solicitar(conn, para=para, asunto=asunto, texto=TEXTO_AVISO.format(que=que))


def _comprobar_totp(
    conn: Connection, claves: Claves, persona_id: UUID, codigo: str, *, confirmado: bool
) -> bool:
    """Comprueba el código contra el factor (confirmado o pendiente) y, si sirve, registra su
    paso para que no se repita. Bloquea la fila: dos intentos a la vez no usan el mismo código."""
    fila = conn.execute(
        text(
            "select secreto_cifrado, ultimo_paso from nucleo.factor_totp"
            " where (confirmado_en is not null) = :c for update"
        ),
        {"c": confirmado},
    ).one_or_none()
    if fila is None:
        raise ErrorApi(
            409,
            CodigoError.SEGUNDO_FACTOR_SIN_ALTA,
            "No hay una aplicación de autenticación"
            + (" activa." if confirmado else " esperando confirmación. Empezá el alta de nuevo."),
        )
    paso = paso_valido(claves.descifrar(persona_id, fila.secreto_cifrado), codigo, fila.ultimo_paso)
    if paso is None:
        return False
    conn.execute(text("update nucleo.factor_totp set ultimo_paso = :p"), {"p": paso})
    return True


def comprobar_codigo(conn: Connection, claves: Claves, persona_id: UUID, codigo: str) -> str | None:
    """Comprueba un código de la aplicación o uno de recuperación (que queda gastado) contra el
    factor confirmado de la persona del ámbito. Devuelve el medio ('totp' o 'recuperacion') si
    sirve; None si no. Sin factor confirmado responde 409."""
    if "-" not in codigo:
        return (
            "totp" if _comprobar_totp(conn, claves, persona_id, codigo, confirmado=True) else None
        )
    hay_factor = conn.execute(
        text("select exists (select 1 from nucleo.factor_totp where confirmado_en is not null)")
    ).scalar_one()
    if not hay_factor:
        raise ErrorApi(
            409,
            CodigoError.SEGUNDO_FACTOR_SIN_ALTA,
            "No hay una aplicación de autenticación activa.",
        )
    usado = conn.execute(
        text(
            "update nucleo.codigo_recuperacion set usado_en = now()"
            " where codigo_hash = :h and usado_en is null returning id"
        ),
        {"h": claves.hash_recuperacion(persona_id, codigo)},
    ).one_or_none()
    return "recuperacion" if usado is not None else None


def _restantes(conn: Connection) -> int:
    return conn.execute(
        text("select count(*) from nucleo.codigo_recuperacion where usado_en is null")
    ).scalar_one()


# Rutas -------------------------------------------------------------------------------------
# Todas con sesión; las que cambian estado piden `X-CSRF-Token` (`obtener_identidad`). Corren
# como `cem_app` con la persona en el ámbito: la RLS de la 0008 deja ver y tocar solo lo suyo.


@router.get("", response_model=EstadoSegundoFactor, summary="Estado de mi segundo factor")
def estado(identidad: IdentidadDep, motor: MotorDep, response: Response) -> EstadoSegundoFactor:
    response.headers.update(SIN_CACHE)
    with transaccion(motor, persona_id=identidad.persona_id) as conn:
        confirmado = conn.execute(
            text("select confirmado_en from nucleo.factor_totp where confirmado_en is not null")
        ).scalar_one_or_none()
        return EstadoSegundoFactor(
            activo=confirmado is not None,
            activado_en=confirmado,
            codigos_recuperacion_restantes=_restantes(conn) if confirmado else 0,
            verificado_en=_verificado_en(conn, _sesion(identidad)),
        )


@router.post(
    "/alta",
    response_model=AltaTotp,
    summary="Empezar el alta de una aplicación de autenticación",
    responses={409: {"model": Error, "description": "Ya hay una aplicación activa."}},
)
def alta(
    identidad: IdentidadDep, claves: ClavesDep, motor: MotorDep, response: Response
) -> AltaTotp:
    """Genera un secreto nuevo, pendiente hasta confirmarlo con un código. Pedir otro reemplaza
    el pendiente. Si ya hay uno confirmado, primero hay que darlo de baja."""
    response.headers.update(SIN_CACHE)
    secreto = secrets.token_bytes(BYTES_SECRETO)
    with transaccion(motor, persona_id=identidad.persona_id) as conn:
        activo = conn.execute(
            text("select confirmado_en is not null from nucleo.factor_totp for update")
        ).scalar_one_or_none()
        if activo:
            raise ErrorApi(
                409,
                CodigoError.SEGUNDO_FACTOR_YA_ACTIVO,
                "Ya tenés una aplicación de autenticación activa. Para cambiarla, primero"
                " desactivala.",
            )
        conn.execute(
            text(
                "insert into nucleo.factor_totp (persona_id, secreto_cifrado) values (:p, :s)"
                " on conflict (persona_id) do update set"
                " secreto_cifrado = excluded.secreto_cifrado, creado_en = now(), ultimo_paso = 0"
            ),
            {"p": identidad.persona_id, "s": claves.cifrar(identidad.persona_id, secreto)},
        )
        cuenta = conn.execute(
            text("select correo from nucleo.persona where id = :p"), {"p": identidad.persona_id}
        ).scalar_one()
    log.info(
        "alta de segundo factor pedida", extra={"campos": {"persona_id": str(identidad.persona_id)}}
    )
    return AltaTotp(secreto=base32(secreto), uri=uri_otpauth(secreto, cuenta))


@router.post(
    "/confirmar",
    response_model=CodigosRecuperacion,
    summary="Confirmar el alta con el primer código",
    responses={
        400: {"model": Error, "description": "El código no sirve."},
        409: {"model": Error, "description": "No hay un alta pendiente."},
    },
)
async def confirmar(
    pedido: CodigoTotp,
    request: Request,
    identidad: IdentidadDep,
    claves: ClavesDep,
    motor: MotorDep,
    response: Response,
) -> CodigosRecuperacion:
    """Con el código correcto activa el segundo factor, deja la sesión verificada y devuelve diez
    códigos de recuperación que no se vuelven a mostrar. Avisa por correo."""
    response.headers.update(SIN_CACHE)
    await consumir_intento(request, identidad.persona_id)
    codigos = [nuevo_codigo_recuperacion() for _ in range(CANTIDAD_RECUPERACION)]
    with transaccion(motor, persona_id=identidad.persona_id) as conn:
        if not _comprobar_totp(conn, claves, identidad.persona_id, pedido.codigo, confirmado=False):
            raise codigo_invalido()
        conn.execute(text("update nucleo.factor_totp set confirmado_en = now()"))
        conn.execute(text("delete from nucleo.codigo_recuperacion"))
        conn.execute(
            text(
                "insert into nucleo.codigo_recuperacion (persona_id, codigo_hash) values (:p, :h)"
            ),
            [
                {"p": identidad.persona_id, "h": claves.hash_recuperacion(identidad.persona_id, c)}
                for c in codigos
            ],
        )
        conn.execute(
            text("update nucleo.sesion set segundo_factor_en = now() where id = :s"),
            {"s": _sesion(identidad)},
        )
        _avisar(conn, identidad.persona_id, ASUNTO_ALTA, "Se activó")
    log.info("segundo factor activado", extra={"campos": {"persona_id": str(identidad.persona_id)}})
    return CodigosRecuperacion(codigos=codigos)


@router.post(
    "/verificar",
    response_model=SegundoFactorVerificado,
    summary="Verificar el segundo factor en esta sesión",
    responses={
        400: {"model": Error, "description": "El código no sirve."},
        409: {"model": Error, "description": "No hay una aplicación activa."},
    },
)
async def verificar(
    pedido: VerificacionSegundoFactor,
    request: Request,
    identidad: IdentidadDep,
    claves: ClavesDep,
    motor: MotorDep,
    response: Response,
) -> SegundoFactorVerificado:
    """Con un código de la aplicación o uno de recuperación (que queda gastado), la sesión
    cuenta como verificada durante 10 minutos (TB-CEM-3)."""
    response.headers.update(SIN_CACHE)
    await consumir_intento(request, identidad.persona_id)
    persona = identidad.persona_id
    with transaccion(motor, persona_id=persona) as conn:
        medio = comprobar_codigo(conn, claves, persona, pedido.codigo)
        if medio is None:
            log.info(
                "verificación de segundo factor rechazada",
                extra={"campos": {"persona_id": str(persona)}},
            )
            raise codigo_invalido()
        verificado_en = conn.execute(
            text(
                "update nucleo.sesion set segundo_factor_en = now() where id = :s"
                " returning segundo_factor_en"
            ),
            {"s": _sesion(identidad)},
        ).scalar_one()
        restantes = _restantes(conn)
    log.info(
        "segundo factor verificado",
        extra={"campos": {"persona_id": str(persona), "medio": medio, "restantes": restantes}},
    )
    return SegundoFactorVerificado(
        verificado_en=verificado_en,
        vale_hasta=verificado_en + VIGENCIA_VERIFICACION,
        codigos_recuperacion_restantes=restantes,
    )


@router.post(
    "/baja",
    status_code=204,
    response_class=Response,
    summary="Desactivar el segundo factor",
    responses={
        403: {"model": Error, "description": "Falta una verificación reciente (10 minutos)."},
    },
)
def baja(identidad: SegundoFactorRecienteDep, motor: MotorDep) -> Response:
    """Pide haber verificado en los últimos 10 minutos: quien solo tiene la sesión (un
    dispositivo olvidado abierto) no puede sacar el segundo factor. Borra el secreto y los
    códigos de recuperación y avisa por correo."""
    with transaccion(motor, persona_id=identidad.persona_id) as conn:
        # TOTP y después recuperación, el orden de `confirmar` y de la supresión (0040).
        conn.execute(text("delete from nucleo.factor_totp"))
        conn.execute(text("delete from nucleo.codigo_recuperacion"))
        conn.execute(
            text("update nucleo.sesion set segundo_factor_en = null where persona_id = :p"),
            {"p": identidad.persona_id},
        )
        _avisar(conn, identidad.persona_id, ASUNTO_BAJA, "Se desactivó")
    log.info(
        "segundo factor desactivado", extra={"campos": {"persona_id": str(identidad.persona_id)}}
    )
    return Response(status_code=204, headers=SIN_CACHE)
