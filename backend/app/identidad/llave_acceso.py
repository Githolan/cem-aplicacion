"""Llave de acceso: el PIN, la huella o la cara del dispositivo (WebAuthn; migración 0070).

Es otro medio de escribir `nucleo.sesion.segundo_factor_en`, así que las reglas de dinero (R10) y
las separaciones no cambian. CEM guarda la clave pública: el gesto nunca sale del dispositivo.

**El desafío** es de un solo uso y va ligado a la sesión. Se gasta en su propia transacción, antes
de comprobar la firma: una respuesta inválida revierte lo demás, y el desafío no puede volver.

**El sitio.** La llave queda atada al nombre de `CEM_ORIGEN_PUBLICO`: la de staging no sirve en
producción. Se exige el gesto (`userVerification: required`) y no se pide `attestation`.

**Dar de alta** con un medio activo pide verificarse con él (`sf.exigir_permiso_de_alta`). Quien no
tiene códigos de recuperación sin usar los recibe con la llave: son lo que permite registrar otra
si pierde el dispositivo.
"""

import json
import logging
import secrets
from datetime import timedelta
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from cem_contracts.errores import CodigoError, Error
from cem_contracts.llave_acceso import (
    LlaveNueva,
    LlaveRegistrada,
    LlaveResumen,
    OpcionesLlave,
    RespuestaLlave,
)
from cem_contracts.segundo_factor import SegundoFactorVerificado
from fastapi import APIRouter, Request, Response
from sqlalchemy import Connection, Engine, text
from webauthn import (
    base64url_to_bytes,
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import bytes_to_base64url
from webauthn.helpers.exceptions import WebAuthnException
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from app.contexto import Identidad, IdentidadDep, MotorDep
from app.db import transaccion
from app.errores import ErrorApi
from app.identidad import segundo_factor as sf
from app.identidad.invitaciones import origen_publico

router = APIRouter(prefix="/api/v1/auth/llave", tags=["identidad"])
log = logging.getLogger("cem.identidad")

VIGENCIA_DESAFIO = timedelta(minutes=5)
BYTES_DESAFIO = 32
ASUNTO_ALTA = "Agregaste una llave de acceso"
ASUNTO_BAJA = "Quitaste una llave de acceso"


def _sitio(request: Request) -> tuple[str, str]:
    """El nombre del sitio y el origen, los dos de `CEM_ORIGEN_PUBLICO`."""
    origen = origen_publico(request)
    sitio = urlsplit(origen).hostname
    if sitio is None:
        raise RuntimeError(f"CEM_ORIGEN_PUBLICO sin nombre de sitio: {origen}")
    return sitio, origen


def _nuevo_desafio(conn: Connection, identidad: Identidad, proposito: str) -> bytes:
    desafio = secrets.token_bytes(BYTES_DESAFIO)
    sesion = sf.sesion_de(identidad)
    conn.execute(text("delete from nucleo.desafio_llave where sesion_id = :s"), {"s": sesion})
    conn.execute(
        text(
            "insert into nucleo.desafio_llave (sesion_id, persona_id, proposito, desafio, vence_en)"
            " values (:s, :p, :o, :d, now() + :v)"
        ),
        {
            "s": sesion,
            "p": identidad.persona_id,
            "o": proposito,
            "d": desafio,
            "v": VIGENCIA_DESAFIO,
        },
    )
    return desafio


def llave_invalida() -> ErrorApi:
    return ErrorApi(
        400,
        CodigoError.SEGUNDO_FACTOR_INVALIDO,
        "No pudimos confirmar tu llave. Probá de nuevo.",
        encabezados=sf.SIN_CACHE,
    )


def _gastar_desafio(motor: Engine, identidad: Identidad, proposito: str) -> bytes:
    """Borra el desafío de la sesión en su propia transacción y lo devuelve si seguía vigente."""
    with transaccion(motor, persona_id=identidad.persona_id) as conn:
        fila = conn.execute(
            text(
                "delete from nucleo.desafio_llave where sesion_id = :s and proposito = :o"
                " returning desafio, vence_en > now() as vigente"
            ),
            {"s": sf.sesion_de(identidad), "o": proposito},
        ).one_or_none()
    if fila is None or not fila.vigente:
        log.info("llave rechazada: sin_desafio_vigente")
        raise llave_invalida()
    return fila.desafio


def _opciones(generadas: Any) -> OpcionesLlave:
    return OpcionesLlave(opciones=json.loads(options_to_json(generadas)))


def _credenciales(conn: Connection) -> list[PublicKeyCredentialDescriptor]:
    filas = conn.execute(text("select credencial_id from nucleo.llave_acceso order by id"))
    return [PublicKeyCredentialDescriptor(id=f.credencial_id) for f in filas]


def _identificador(credencial: dict[str, Any]) -> bytes:
    crudo = credencial.get("rawId")
    if not isinstance(crudo, str):
        raise llave_invalida()
    try:
        return base64url_to_bytes(crudo)
    except ValueError as e:
        log.info("llave rechazada: identificador ilegible")
        raise llave_invalida() from e


# Rutas -------------------------------------------------------------------------------------
# Todas con sesión; las que cambian estado piden `X-CSRF-Token` (`obtener_identidad`). Corren
# como `cem_app` con la persona en el ámbito: la RLS de la 0070 deja ver y tocar solo lo suyo.


@router.get("", response_model=list[LlaveResumen], summary="Mis llaves de acceso")
def listar(identidad: IdentidadDep, motor: MotorDep, response: Response) -> list[LlaveResumen]:
    response.headers.update(sf.SIN_CACHE)
    with transaccion(motor, persona_id=identidad.persona_id) as conn:
        filas = conn.execute(
            text(
                "select id, credencial_id, nombre, creada_en, ultimo_uso_en"
                " from nucleo.llave_acceso order by id"
            )
        ).all()
    return [
        LlaveResumen(
            id=f.id,
            credencial_id=bytes_to_base64url(f.credencial_id),
            nombre=f.nombre,
            creada_en=f.creada_en,
            ultimo_uso_en=f.ultimo_uso_en,
        )
        for f in filas
    ]


@router.post(
    "/alta/opciones",
    response_model=OpcionesLlave,
    summary="Empezar el alta de una llave",
    responses={403: {"model": Error, "description": "Falta confirmar que sos vos."}},
)
def alta_opciones(
    request: Request, identidad: IdentidadDep, motor: MotorDep, response: Response
) -> OpcionesLlave:
    response.headers.update(sf.SIN_CACHE)
    sitio, _ = _sitio(request)
    with transaccion(motor, persona_id=identidad.persona_id) as conn:
        sf.exigir_permiso_de_alta(conn, identidad)
        cuenta = conn.execute(
            text("select correo from nucleo.persona where id = :p"), {"p": identidad.persona_id}
        ).scalar_one()
        return _opciones(
            generate_registration_options(
                rp_id=sitio,
                rp_name=sf.EMISOR,
                user_id=identidad.persona_id.bytes,
                user_name=cuenta,
                challenge=_nuevo_desafio(conn, identidad, "alta"),
                authenticator_selection=AuthenticatorSelectionCriteria(
                    resident_key=ResidentKeyRequirement.PREFERRED,
                    user_verification=UserVerificationRequirement.REQUIRED,
                ),
                exclude_credentials=_credenciales(conn),
            )
        )


@router.post(
    "/alta",
    response_model=LlaveRegistrada,
    status_code=201,
    summary="Registrar la llave",
    responses={
        400: {"model": Error, "description": "La llave no se pudo confirmar."},
        403: {"model": Error, "description": "Falta confirmar que sos vos."},
    },
)
async def alta(
    pedido: LlaveNueva,
    request: Request,
    identidad: IdentidadDep,
    claves: sf.ClavesDep,
    motor: MotorDep,
    response: Response,
) -> LlaveRegistrada:
    """Deja la sesión verificada y avisa por correo. Devuelve códigos de recuperación si la persona
    no tenía ninguno sin usar."""
    response.headers.update(sf.SIN_CACHE)
    await sf.consumir_intento(request, identidad.persona_id)
    sitio, origen = _sitio(request)
    desafio = _gastar_desafio(motor, identidad, "alta")
    try:
        verificada = verify_registration_response(
            credential=pedido.credencial,
            expected_challenge=desafio,
            expected_rp_id=sitio,
            expected_origin=origen,
            require_user_verification=True,
        )
    except WebAuthnException as e:
        log.info("alta de llave rechazada", extra={"campos": {"motivo": type(e).__name__}})
        raise llave_invalida() from e
    persona = identidad.persona_id
    with transaccion(motor, persona_id=persona) as conn:
        sf.exigir_permiso_de_alta(conn, identidad)
        id_ = conn.execute(
            text(
                "insert into nucleo.llave_acceso"
                " (persona_id, credencial_id, clave_publica, contador, nombre)"
                " values (:p, :c, :k, :n, :m) returning id"
            ),
            {
                "p": persona,
                "c": verificada.credential_id,
                "k": verificada.credential_public_key,
                "n": verificada.sign_count,
                "m": pedido.nombre.strip(),
            },
        ).scalar_one()
        codigos = None if sf.restantes(conn) else sf.emitir_codigos(conn, claves, persona)
        sf.marcar_verificada(conn, identidad)
        sf.avisar(conn, persona, ASUNTO_ALTA, "Se agregó una llave de acceso a")
    log.info("llave de acceso agregada", extra={"campos": {"persona_id": str(persona)}})
    return LlaveRegistrada(id=id_, codigos_recuperacion=codigos)


@router.post(
    "/verificar/opciones",
    response_model=OpcionesLlave,
    summary="Pedir el desafío para confirmar con la llave",
    responses={409: {"model": Error, "description": "No hay una llave activa."}},
)
def verificar_opciones(
    request: Request, identidad: IdentidadDep, motor: MotorDep, response: Response
) -> OpcionesLlave:
    response.headers.update(sf.SIN_CACHE)
    sitio, _ = _sitio(request)
    with transaccion(motor, persona_id=identidad.persona_id) as conn:
        credenciales = _credenciales(conn)
        if not credenciales:
            raise ErrorApi(
                409, CodigoError.SEGUNDO_FACTOR_SIN_ALTA, "No tenés una llave de acceso activa."
            )
        return _opciones(
            generate_authentication_options(
                rp_id=sitio,
                challenge=_nuevo_desafio(conn, identidad, "verificar"),
                allow_credentials=credenciales,
                user_verification=UserVerificationRequirement.REQUIRED,
            )
        )


@router.post(
    "/verificar",
    response_model=SegundoFactorVerificado,
    summary="Confirmar con la llave en esta sesión",
    responses={400: {"model": Error, "description": "La llave no se pudo confirmar."}},
)
async def verificar(
    pedido: RespuestaLlave,
    request: Request,
    identidad: IdentidadDep,
    motor: MotorDep,
    response: Response,
) -> SegundoFactorVerificado:
    """La sesión cuenta como verificada durante 10 minutos, como con el código."""
    response.headers.update(sf.SIN_CACHE)
    await sf.consumir_intento(request, identidad.persona_id)
    sitio, origen = _sitio(request)
    desafio = _gastar_desafio(motor, identidad, "verificar")
    persona = identidad.persona_id
    with transaccion(motor, persona_id=persona) as conn:
        fila = conn.execute(
            text(
                "select id, clave_publica, contador from nucleo.llave_acceso"
                " where credencial_id = :c for update"
            ),
            {"c": _identificador(pedido.credencial)},
        ).one_or_none()
        if fila is None:
            log.info("llave rechazada: no_es_de_la_persona")
            raise llave_invalida()
        try:
            verificada = verify_authentication_response(
                credential=pedido.credencial,
                expected_challenge=desafio,
                expected_rp_id=sitio,
                expected_origin=origen,
                credential_public_key=fila.clave_publica,
                credential_current_sign_count=fila.contador,
                require_user_verification=True,
            )
        except WebAuthnException as e:
            log.info("llave rechazada", extra={"campos": {"motivo": type(e).__name__}})
            raise llave_invalida() from e
        conn.execute(
            text(
                "update nucleo.llave_acceso set contador = :n, ultimo_uso_en = now() where id = :i"
            ),
            {"n": verificada.new_sign_count, "i": fila.id},
        )
        verificado_en = sf.marcar_verificada(conn, identidad)
        restantes = sf.restantes(conn)
    log.info(
        "segundo factor verificado",
        extra={"campos": {"persona_id": str(persona), "medio": "llave", "restantes": restantes}},
    )
    return SegundoFactorVerificado(
        verificado_en=verificado_en,
        vale_hasta=verificado_en + sf.VIGENCIA_VERIFICACION,
        codigos_recuperacion_restantes=restantes,
    )


@router.delete(
    "/{llave_id}",
    status_code=204,
    response_class=Response,
    summary="Quitar una llave",
    responses={
        403: {"model": Error, "description": "Falta una verificación reciente (10 minutos)."},
        404: {"model": Error, "description": "No hay una llave tuya con ese identificador."},
    },
)
def baja(llave_id: UUID, identidad: sf.SegundoFactorRecienteDep, motor: MotorDep) -> Response:
    """Pide haber verificado hace poco y deja todas las sesiones sin verificar. Si era el último
    medio, también borra los códigos de recuperación, que sin medio no sirven."""
    persona = identidad.persona_id
    with transaccion(motor, persona_id=persona) as conn:
        borrada = conn.execute(
            text("delete from nucleo.llave_acceso where id = :i returning id"), {"i": llave_id}
        ).one_or_none()
        if borrada is None:
            raise ErrorApi(404, CodigoError.NO_ENCONTRADO, "No encontramos esa llave.")
        if not sf.tiene_medio(conn):
            conn.execute(text("delete from nucleo.codigo_recuperacion"))
        conn.execute(
            text("update nucleo.sesion set segundo_factor_en = null where persona_id = :p"),
            {"p": persona},
        )
        sf.avisar(conn, persona, ASUNTO_BAJA, "Se quitó una llave de acceso de")
    log.info("llave de acceso quitada", extra={"campos": {"persona_id": str(persona)}})
    return Response(status_code=204, headers=sf.SIN_CACHE)
