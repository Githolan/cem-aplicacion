"""Ayuda por una persona autorizada (RF53; `0127`).

Principio aprobado (decisiones del propietario del 25-10-2026, §1.7): quien ayuda mira y prepara,
y nunca sustituye la voluntad de la persona asistida en un acto decisorio. Usa su propia cuenta
(H2b, criterio 5).

- **`GET|POST /consorcios/{c}/ayudas`** (`ayuda.ver_otorgadas`, `ayuda.otorgar`): el propietario
  confirmado ve las autorizaciones que dio (también las revocadas: la historia no se borra) y da
  una nueva, por la dirección de la cuenta de quien va a ayudar y con un alcance explícito
  (`cem_contracts.ayuda`). Le llega un correo a quien ayuda, con el edificio, el nombre de quien
  autorizó y el enlace; nunca el alcance ni datos de la persona asistida.
- **`POST /consorcios/{c}/ayudas/{a}/revocacion`** (`ayuda.revocar`): solo quien la dio. La
  autorización no se cambia ni se borra: queda con su revocación.
- **`GET /me/ayudas`**: las autorizaciones vigentes que la persona de la sesión puede usar para
  ayudar. Es de la persona, como `/me/consorcios` (alcance `propia`, fuera de la matriz).

Mientras ayuda, quien ayuda no puede autorizar, revocar ni ver autorizaciones en nombre de la
asistida: ninguna de estas políticas admite ayuda (`ayuda_no_admitida`) y la base exige, además,
la sesión de la persona misma y ninguna ayuda en curso.

Cupo de autorizaciones nuevas: el de las invitaciones (20 por hora y 50 por día por persona); cada
una manda un correo a una dirección que eligió quien autoriza.
"""

import logging
from typing import Annotated
from uuid import UUID

from cem_contracts.ayuda import (
    ALCANCES_OTORGABLES,
    AlcanceAyuda,
    AyudaNueva,
    AyudaOtorgada,
    AyudaRecibida,
    ListaAyudasOtorgadas,
    ListaAyudasRecibidas,
)
from cem_contracts.errores import CodigoError
from fastapi import APIRouter, Depends, Request, Response
from psycopg.errors import InsufficientPrivilege
from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError

from app import limite
from app.autorizacion.requiere import NO_ENCONTRADO, VOLVER_A_ENTRAR, Permiso, requiere
from app.consorcios.padron import Discrepancia
from app.contexto import IdentidadDep, MotorDep
from app.db import transaccion
from app.errores import ErrorApi, detalle_de_campo, sin_cache
from app.identidad.invitaciones import origen_publico
from app.plataforma import correo
from app.plataforma.idempotencia import (
    IdempotenciaDep,
    responder_una_vez,
    responder_una_vez_con_cupo,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["consorcios"], dependencies=[Depends(sin_cache)])

VER = "ayuda.ver_otorgadas"
OTORGAR = "ayuda.otorgar"
REVOCAR = "ayuda.revocar"
LIMITE_OTORGAR = "ayuda-otorgar"
RUTA_AYUDA = "/ayuda"
"""La página de la ayuda (`frontend/src/ayuda/Ayuda.tsx`): ahí se elige «Ayudar a …»."""

ERRORES = {
    "sin_cuenta": (
        422, CodigoError.AYUDA_SIN_CUENTA,
        "Esa dirección no tiene una cuenta en Consorcio en Mano. La persona que va a ayudar entra"
        " con su propia cuenta: primero necesita tenerla.",
    ),
    "a_si_misma": (
        422, CodigoError.AYUDA_A_SI_MISMA,
        "Esa dirección es la de esta misma cuenta: hay que escribir la de la persona que va a"
        " ayudar.",
    ),
    "ya_vigente": (
        409, CodigoError.AYUDA_YA_VIGENTE,
        "Esa persona ya tiene una autorización vigente en este edificio. Para cambiar lo que puede"
        " hacer, hay que revocarla y dar otra.",
    ),
    "ya_revocada": (409, CodigoError.AYUDA_YA_REVOCADA, "Esa autorización ya estaba revocada."),
    "no_encontrado": (404, CodigoError.NO_ENCONTRADO, NO_ENCONTRADO),
    "sesion_invalida": (403, *VOLVER_A_ENTRAR),
}  # fmt: skip
"""Motivos de las funciones de `0127` que la persona puede provocar."""
CAMPO_DEL_MOTIVO = {"sin_cuenta": "correo", "a_si_misma": "correo", "ya_vigente": "correo"}

ASUNTO = "Autorización para ayudar en {consorcio}"
TEXTO = (
    "Hola:\n\n"
    "{otorgante} dio autorización a esta cuenta para ayudar en {consorcio} en Consorcio en"
    " Mano: consultar o preparar, nunca votar, firmar, aprobar ni pagar.\n\n"
    "Para empezar a ayudar: {enlace}\n"
)
"""Solo quién autorizó y el edificio: ni el alcance ni ningún dato de sus unidades."""

RESPUESTAS = {
    403: {"description": "Sin un vínculo de propietario confirmado, o mientras se ayuda a otra"
          " persona."},
}  # fmt: skip


def _una_linea(texto: str) -> str:
    return " ".join(texto.split())


def _ejecutar(conn: Connection, permiso: Permiso, sql: str, parametros: dict):
    """Llama a una función de `0127` con la persona y la sesión, y traduce sus motivos."""
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


LEER = "select * from nucleo.ayudas_otorgadas_lectura(:actor, 'api', :sesion)"


def _otorgada(f) -> AyudaOtorgada:
    return AyudaOtorgada(
        id=f["id"],
        autorizado_nombre=f["autorizado_nombre"],
        alcances=[AlcanceAyuda(a) for a in f["alcances"]],
        otorgada_en=f["otorgada_en"],
        revocada_en=f["revocada_en"],
    )


def _una(conn: Connection, permiso: Permiso, autorizacion: UUID) -> AyudaOtorgada:
    filas = _ejecutar(conn, permiso, LEER + " where id = :a", {"a": autorizacion}).mappings().all()
    if not filas:
        raise ErrorApi(404, CodigoError.NO_ENCONTRADO, NO_ENCONTRADO)
    return _otorgada(filas[0])


@router.get(
    "/consorcios/{consorcio_id}/ayudas",
    response_model=ListaAyudasOtorgadas,
    summary="Las autorizaciones de ayuda que dio la persona en el edificio, vigentes y revocadas",
    responses=RESPUESTAS,
)
def listar_ayudas(
    permiso: Annotated[Permiso, Depends(requiere(VER, al_entrar=True))], motor: MotorDep
) -> ListaAyudasOtorgadas:
    ctx = permiso.ctx
    with transaccion(motor, persona_id=ctx.persona_id, consorcio_id=ctx.consorcio_id) as c:
        filas = _ejecutar(c, permiso, LEER, {}).mappings().all()
    return ListaAyudasOtorgadas(
        elementos=[_otorgada(f) for f in filas], alcances_otorgables=list(ALCANCES_OTORGABLES)
    )


@router.post(
    "/consorcios/{consorcio_id}/ayudas",
    status_code=201,
    response_model=AyudaOtorgada,
    summary="Autorizar a otra persona con cuenta a ayudar en el edificio, con un alcance; le avisa"
    " por correo",
    responses={
        **RESPUESTAS,
        409: {"description": "Esa persona ya tiene una autorización vigente (`ayuda_ya_vigente`)."},
        422: {"description": "La dirección no tiene cuenta (`ayuda_sin_cuenta`) o es la propia"
              " (`ayuda_a_si_misma`)."},
    },
)  # fmt: skip
async def otorgar_ayuda(
    request: Request,
    cuerpo: AyudaNueva,
    permiso: Annotated[Permiso, Depends(requiere(OTORGAR, al_entrar=True))],
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    ctx = permiso.ctx
    enlace = f"{origen_publico(request)}{RUTA_AYUDA}"

    async def cupo() -> None:
        for limite_persona in limite.LIMITES_INVITACIONES_PERSONA:
            await limite.consumir(
                request.app.state.limitador, limite_persona, LIMITE_OTORGAR, str(ctx.persona_id)
            )

    def efecto(conn: Connection) -> tuple[int, AyudaOtorgada]:
        [f] = _ejecutar(
            conn, permiso,
            "select * from nucleo.otorgar_ayuda(:correo, :alcances, :actor, 'api', :sesion)",
            {"correo": cuerpo.correo, "alcances": [a.value for a in cuerpo.alcances]},
        ).mappings().all()  # fmt: skip
        nombre = _una_linea(f["consorcio_nombre"])
        correo.solicitar(
            conn, para=f["correo"], asunto=ASUNTO.format(consorcio=nombre),
            texto=TEXTO.format(otorgante=_una_linea(f["otorgante_nombre"]), consorcio=nombre,
                               enlace=enlace),
            consorcio_id=ctx.consorcio_id,
        )  # fmt: skip
        log.info(
            "ayuda otorgada",
            extra={"campos": {"consorcio_id": str(ctx.consorcio_id),
                              "persona_id": str(ctx.persona_id),
                              "autorizacion_id": str(f["autorizacion_id"])}},
        )  # fmt: skip
        return 201, _una(conn, permiso, f["autorizacion_id"])

    return await responder_una_vez_con_cupo(motor, ctx, idempotencia, cupo, efecto)


@router.post(
    "/consorcios/{consorcio_id}/ayudas/{autorizacion_id}/revocacion",
    response_model=AyudaOtorgada,
    summary="Revocar una autorización de ayuda que dio la persona; queda en la historia",
    responses={
        **RESPUESTAS,
        404: {"description": "La autorización no existe en el edificio o no la dio esta persona."},
        409: {"description": "Ya estaba revocada (`ayuda_ya_revocada`)."},
    },
)
def revocar_ayuda(
    autorizacion_id: UUID,
    permiso: Annotated[Permiso, Depends(requiere(REVOCAR, al_entrar=True))],
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    def efecto(conn: Connection) -> tuple[int, AyudaOtorgada]:
        _ejecutar(
            conn, permiso,
            "select nucleo.revocar_ayuda(:a, :actor, 'api', :sesion)",
            {"a": autorizacion_id},
        )  # fmt: skip
        return 200, _una(conn, permiso, autorizacion_id)

    return responder_una_vez(motor, permiso.ctx, idempotencia, efecto)


RECIBIDAS = "select * from nucleo.ayudas_recibidas_lectura(:sesion)"


def _recibidas(conn: Connection, identidad, autorizacion: UUID | None = None) -> list:
    sql = RECIBIDAS if autorizacion is None else RECIBIDAS + " where id = :a"
    return conn.execute(text(sql), {"sesion": identidad.sesion_id, "a": autorizacion}).mappings()


@router.get(
    "/me/ayudas",
    response_model=ListaAyudasRecibidas,
    summary="Las autorizaciones vigentes con que la persona puede ayudar a otra",
)
def mis_ayudas(identidad: IdentidadDep, motor: MotorDep) -> ListaAyudasRecibidas:
    """Ruta de la persona (alcance `propia`, fuera de la matriz, como `/me/consorcios`): la base
    devuelve solo las suyas, vigentes y de una persona asistida que sigue siendo propietaria
    confirmada."""
    with transaccion(motor, persona_id=identidad.persona_id) as conn:
        filas = list(_recibidas(conn, identidad))
    return ListaAyudasRecibidas(
        elementos=[
            AyudaRecibida(
                id=f["id"],
                consorcio_id=f["consorcio_id"],
                consorcio_nombre=f["consorcio_nombre"],
                otorgante_nombre=f["otorgante_nombre"],
                alcances=[AlcanceAyuda(a) for a in f["alcances"]],
                otorgada_en=f["otorgada_en"],
            )  # fmt: skip
            for f in filas
        ]
    )


def recibida(motor, identidad, autorizacion: UUID):
    """La autorización vigente de la persona de la sesión, o None (para `/me/consorcios`)."""
    with transaccion(motor, persona_id=identidad.persona_id) as conn:
        filas = list(_recibidas(conn, identidad, autorizacion))
    return filas[0] if filas else None
