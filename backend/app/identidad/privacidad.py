"""Casos de privacidad del titular dentro de la aplicación (RF37, corte 2d-2; migración 0042).

Diseño: `doc/diseno/H2d_RF37_Diseno.md` del repositorio CEM, §5 y §9.1. Decisión del propietario
del 28-10-2026, opción (a): la confirmación de quien tiene contraseña o TOTP y la copia de los
datos propios van **solo dentro de la aplicación, al titular autenticado**. No hay ruta para
soporte: abre, corrobora y resuelve los demás casos con `scripts.privacidad`.

- **`GET /api/v1/privacidad/casos`:** los casos de la persona de la sesión.
- **`POST …/{caso_id}/confirmar`:** el código que llegó al correo de la cuenta, con
  reautenticación reciente.
- **`POST …/{caso_id}/copia`:** con el caso de acceso confirmado y reautenticación reciente,
  devuelve la copia y el caso queda resuelto. Una sola entrega: otra copia es otro caso.

**Reautenticación reciente.** Si la persona tiene contraseña, la escribe en el pedido y se
compara acá con su hash (la base no puede comparar Argon2id: recibe solo si coincidió). La base
controla el resto con la sesión de la petición: que sea de la persona y esté vigente, que haya
empezado hace menos de 10 minutos si no hay contraseña (entró recién con el enlace), y el TOTP
verificado hace menos de 10 minutos si está activo. Los intentos tienen el cupo del segundo factor
(5 por minuto y 20 por día por persona), en un contador propio.

El titular sale siempre de la sesión: las funciones de `0042` leen `persona_actual()`. Un caso de
otra persona responde 404, igual que uno que no existe. Nada de esto va a `auditoria.eventos`: la
constancia queda en el caso.
"""

import hashlib
import logging
import re
from uuid import UUID

from cem_contracts.errores import CodigoError, Error
from cem_contracts.privacidad import (
    CasoPrivacidad,
    ConfirmacionCaso,
    CopiaDatosPropios,
    ListaCasosPrivacidad,
    PedidoCopia,
    Reautenticacion,
)
from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError

from app import limite
from app.autorizacion.requiere import NO_ENCONTRADO
from app.contexto import Identidad, IdentidadDep, MotorDep
from app.db import transaccion
from app.errores import ErrorApi, sin_cache
from app.identidad import contrasena, segundo_factor

router = APIRouter(
    prefix="/api/v1/privacidad", tags=["privacidad"], dependencies=[Depends(sin_cache)]
)
log = logging.getLogger("cem.privacidad")

LIMITE_NOMBRE = "privacidad-reautenticacion"
LIMITES_INTENTOS = segundo_factor.LIMITES_INTENTOS

FALTA = {
    "requiere_contrasena": "contrasena",
    "requiere_sesion_reciente": "entrar_de_nuevo",
    "requiere_segundo_factor": "segundo_factor",
}
"""Motivos de `privacidad_reautenticacion` (0042) y lo que la persona tiene que hacer."""
MENSAJE_REAUTENTICACION = (
    "Para esto tenés que volver a confirmar que sos vos: escribí tu contraseña, entrá de nuevo"
    " con el enlace del correo si no tenés contraseña, y confirmá con tu aplicación de"
    " autenticación si la activaste."
)
CODIGO_INCORRECTO = {"sin_codigo", "codigo_usado", "codigo_vencido", "codigo_incorrecto"}
CASO_NO_DISPONIBLE = {
    "caso_no_abierto": "El caso ya no espera confirmación.",
    "caso_no_confirmado": "El caso no está confirmado o ya se entregó la copia.",
    "derecho_no_acceso": "Ese caso no es un pedido de copia de tus datos.",
    "requiere_corroboracion": (
        "Falta que la administración o el Consejo corroboren el pedido: el equipo te va a escribir."
    ),
}


def hash_codigo(codigo: str) -> bytes:
    """El código se compara sin guiones, espacios ni mayúsculas: como lo escriba la persona."""
    normal = re.sub(r"[^A-Z2-7]", "", codigo.upper())
    return hashlib.sha256(normal.encode("ascii")).digest()


async def _consumir_intento(request: Request, identidad: Identidad) -> None:
    for cupo in LIMITES_INTENTOS:
        await limite.consumir(
            request.app.state.limitador, cupo, LIMITE_NOMBRE, str(identidad.persona_id)
        )


def _contrasena_ok(conn: Connection, identidad: Identidad, pedido: Reautenticacion) -> bool:
    """Si la persona tiene contraseña, la del pedido tiene que coincidir; si no la escribió, la
    base responde `requiere_contrasena`. Sin contraseña guardada, lo escrito no cuenta."""
    guardada = conn.execute(
        text("select hash from nucleo.contrasena where persona_id = :p"),
        {"p": identidad.persona_id},
    ).scalar_one_or_none()
    if guardada is None or pedido.contrasena is None:
        return False
    if not contrasena.verificar(guardada, pedido.contrasena):
        log.info(
            "caso de privacidad rechazado: contrasena_incorrecta",
            extra={"campos": {"persona_id": str(identidad.persona_id)}},
        )
        raise ErrorApi(
            403,
            CodigoError.REAUTENTICACION_REQUERIDA,
            "La contraseña no es correcta.",
            detalle={"falta": "contrasena"},
        )
    return True


def _traducir(e: DBAPIError, persona_id: UUID, caso_id: UUID) -> ErrorApi | None:
    motivo = e.orig.diag.message_hint
    campos = {"persona_id": str(persona_id), "caso_id": str(caso_id), "motivo": motivo}
    if motivo == "caso_inexistente":
        return ErrorApi(404, CodigoError.NO_ENCONTRADO, NO_ENCONTRADO)
    if motivo in FALTA:
        log.info("caso de privacidad rechazado", extra={"campos": campos})
        return ErrorApi(
            403,
            CodigoError.REAUTENTICACION_REQUERIDA,
            MENSAJE_REAUTENTICACION,
            detalle={"falta": FALTA[motivo]},
        )
    if motivo in CODIGO_INCORRECTO:
        log.info("caso de privacidad rechazado", extra={"campos": campos})
        return ErrorApi(
            400,
            CodigoError.CODIGO_CASO_INVALIDO,
            "El código no sirve: revisá que sea el último que te llegó y que no haya vencido.",
        )
    if motivo in CASO_NO_DISPONIBLE:
        log.info("caso de privacidad rechazado", extra={"campos": campos})
        return ErrorApi(409, CodigoError.CASO_NO_DISPONIBLE, CASO_NO_DISPONIBLE[motivo])
    return None


@router.get("/casos", response_model=ListaCasosPrivacidad, summary="Mis casos de privacidad")
def listar(identidad: IdentidadDep, motor: MotorDep) -> ListaCasosPrivacidad:
    with transaccion(motor, persona_id=identidad.persona_id) as conn:
        filas = conn.execute(text("select * from plataforma.privacidad_mis_casos()")).mappings()
        return ListaCasosPrivacidad(elementos=[CasoPrivacidad(**f) for f in filas])


@router.post(
    "/casos/{caso_id}/confirmar",
    status_code=204,
    response_class=Response,
    summary="Confirmar mi caso de privacidad con el código del correo",
    responses={
        400: {"model": Error, "description": "El código no sirve."},
        403: {"model": Error, "description": "Falta la reautenticación reciente."},
        404: {"model": Error, "description": "No hay un caso así de esta persona."},
        409: {"model": Error, "description": "El caso no admite confirmarse."},
    },
)
async def confirmar(
    caso_id: UUID,
    pedido: ConfirmacionCaso,
    request: Request,
    identidad: IdentidadDep,
    motor: MotorDep,
) -> Response:
    await _consumir_intento(request, identidad)
    with transaccion(motor, persona_id=identidad.persona_id) as conn:
        ok = _contrasena_ok(conn, identidad, pedido)
        try:
            conn.execute(
                text("select plataforma.privacidad_confirmar_en_app(:k, :h, :s, :c)"),
                {"k": caso_id, "h": hash_codigo(pedido.codigo), "s": identidad.sesion_id, "c": ok},
            )
        except DBAPIError as e:
            error = _traducir(e, identidad.persona_id, caso_id)
            if error is None:
                raise
            raise error from e
    log.info(
        "caso de privacidad confirmado en la aplicación",
        extra={"campos": {"persona_id": str(identidad.persona_id), "caso_id": str(caso_id)}},
    )
    return Response(status_code=204)


@router.post(
    "/casos/{caso_id}/copia",
    response_model=CopiaDatosPropios,
    summary="Recibir la copia de mis datos (una vez por caso)",
    responses={
        403: {"model": Error, "description": "Falta la reautenticación reciente."},
        404: {"model": Error, "description": "No hay un caso así de esta persona."},
        409: {"model": Error, "description": "El caso no es de copia o no está confirmado."},
    },
)
async def copia(
    caso_id: UUID,
    pedido: PedidoCopia,
    request: Request,
    identidad: IdentidadDep,
    motor: MotorDep,
) -> CopiaDatosPropios:
    await _consumir_intento(request, identidad)
    with transaccion(motor, persona_id=identidad.persona_id) as conn:
        ok = _contrasena_ok(conn, identidad, pedido)
        try:
            datos = conn.execute(
                text("select plataforma.privacidad_entregar_copia(:k, :s, :c)"),
                {"k": caso_id, "s": identidad.sesion_id, "c": ok},
            ).scalar_one()
        except DBAPIError as e:
            error = _traducir(e, identidad.persona_id, caso_id)
            if error is None:
                raise
            raise error from e
    log.info(
        "copia de datos propios entregada",
        extra={"campos": {"persona_id": str(identidad.persona_id), "caso_id": str(caso_id)}},
    )
    caso = datos.pop("caso_id")
    generada = datos.pop("generada_en")
    return CopiaDatosPropios(
        caso_id=caso,
        generada_en=generada,
        alcance_consorcio_id=datos.get("consorcio_id"),
        datos=datos,
    )
