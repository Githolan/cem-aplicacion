"""Padrón mínimo y confirmación de vínculos (H2b 2b-4; D6, criterio 6; P4 y P5 del propietario).

**Padrón.** `nucleo.padron_del_consorcio()` (migración 0014) lee los nombres con `security
definer` y exige, como segunda barrera, un mandato vigente con `padron.ver` en el consorcio. La
aplicación decide la concesión fila por fila con la política `padron.ver` y el recurso `unidad`,
como `invitaciones.listar`. Si la base niega y el evaluador permite, las dos no coinciden: la
ruta falla fuerte (500), nunca devuelve una lista vacía.

**Confirmación.** La política `padron.confirmar_vinculo` decide antes de cualquier efecto: la
facultad, el recurso de la unidad, que no sea el propio vínculo y que no sea quien lo registró
(P4-c). `nucleo.confirmar_vinculo` vuelve a garantizar la integridad en la base. Un motivo de la
base que el evaluador ya tenía que haber negado es una discrepancia y responde 500.
"""

import logging
from typing import Annotated
from uuid import UUID

from cem_contracts.errores import CodigoError
from cem_contracts.padron import ConfirmacionVinculo, FilaPadron, Padron, VinculoConfirmado
from cem_contracts.politicas import TipoRecurso
from fastapi import APIRouter, Depends
from fastapi.responses import Response
from psycopg.errors import InsufficientPrivilege
from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError

from app.autorizacion.evaluador import Entidad
from app.autorizacion.requiere import NO_ENCONTRADO, Permiso, requiere
from app.contexto import MotorDep
from app.db import transaccion
from app.errores import ErrorApi, sin_cache
from app.plataforma.idempotencia import IdempotenciaDep, responder_una_vez

router = APIRouter(prefix="/api/v1", tags=["consorcios"], dependencies=[Depends(sin_cache)])
log = logging.getLogger("cem.padron")

ORIGEN_SOPORTE = "soporte"
CONFLICTOS = {
    "vinculo_finalizado": (CodigoError.VINCULO_FINALIZADO, "El vínculo ya terminó."),
    "vinculo_ya_confirmado": (CodigoError.VINCULO_YA_CONFIRMADO, "El vínculo ya está confirmado."),
}
"""Motivos de `nucleo.confirmar_vinculo` que dependen del estado del vínculo (409). Los demás
los niega antes el evaluador o el contrato: si llegan a la base, es una discrepancia."""


def sobre_unidad(unidad_id: UUID) -> Entidad:
    return Entidad(recursos={TipoRecurso.UNIDAD: str(unidad_id)})


class Discrepancia(RuntimeError):
    """La base y el evaluador no coinciden: un defecto, no un caso de uso."""


@router.get(
    "/consorcios/{consorcio_id}/padron",
    response_model=Padron,
    summary="Padrón de vínculos vigentes y futuros",
    responses={403: {"description": "La persona no tiene `padron.ver` en este consorcio."}},
)
def padron(permiso: Annotated[Permiso, Depends(requiere("padron.ver"))], motor: MotorDep) -> Padron:
    """Unidad, nombre, carácter, vigencia y si el vínculo está confirmado (D6). Sin correo ni
    teléfono, sin vínculos terminados. Una facultad acotada a unidades ve solo esas."""
    ctx = permiso.ctx
    try:
        with transaccion(motor, persona_id=ctx.persona_id, consorcio_id=ctx.consorcio_id) as conn:
            filas = (
                conn.execute(
                    text(
                        "select vinculo_id, unidad_id, designacion, nombre, tipo::text as tipo,"
                        " vigente_desde, vigente_hasta, confirmado"
                        " from nucleo.padron_del_consorcio()"
                    )
                )
                .mappings()
                .all()
            )
    except DBAPIError as e:
        if not isinstance(e.orig, InsufficientPrivilege):
            raise
        permiso.verificar(Entidad())  # niega, audita y responde 403
        raise Discrepancia("padron_del_consorcio niega padron.ver y el evaluador lo concede") from e
    return Padron(
        elementos=[
            FilaPadron(**f)
            for f in filas
            if permiso.decidir(sobre_unidad(f["unidad_id"])).permitido
        ]
    )


def _confirmar(conn: Connection, ctx, vinculo_id: UUID, referencia: str) -> VinculoConfirmado:
    try:
        conn.execute(
            text("select nucleo.confirmar_vinculo(:v, :r, :a, 'api')"),
            {"v": vinculo_id, "r": referencia, "a": ctx.persona_id},
        )
    except DBAPIError as e:
        motivo = e.orig.diag.message_hint
        if motivo in CONFLICTOS:
            codigo, mensaje = CONFLICTOS[motivo]
            raise ErrorApi(409, codigo, mensaje) from e
        raise
    fila = (
        conn.execute(
            text(
                "select id as vinculo_id, unidad_id, tipo::text as tipo, confirmado_en"
                " from nucleo.vinculo where id = :v"
            ),
            {"v": vinculo_id},
        )
        .mappings()
        .one()
    )
    return VinculoConfirmado(**fila, confirmado=True)


@router.post(
    "/consorcios/{consorcio_id}/vinculos/{vinculo_id}/confirmar",
    response_model=VinculoConfirmado,
    summary="Confirmar un vínculo con el título que lo acredita",
    responses={
        403: {
            "description": "Sin `padron.confirmar_vinculo` sobre esa unidad, el vínculo es de"
            " la propia persona o lo registró ella (P4)."
        },
        404: {"description": "El vínculo no existe en este consorcio."},
        409: {"description": "El vínculo ya terminó o ya está confirmado."},
    },
)
def confirmar(
    vinculo_id: UUID,
    pedido: ConfirmacionVinculo,
    permiso: Annotated[Permiso, Depends(requiere("padron.confirmar_vinculo"))],
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    """Acredita el carácter de la persona sobre la unidad según el título que se refiere, no su
    identidad civil. Si el título no lo acredita, no se confirma: no hay estado "rechazado". La
    referencia queda en el vínculo y no sale en ninguna respuesta ni en la auditoría."""
    ctx = permiso.ctx
    with transaccion(motor, persona_id=ctx.persona_id, consorcio_id=ctx.consorcio_id) as conn:
        # Filtrada por el consorcio de la ruta: la RLS deja leer vínculos de otros consorcios
        # de la persona, y uno de otro consorcio responde igual que uno inexistente.
        v = conn.execute(
            text(
                "select unidad_id, persona_id, registrado_por, origen from nucleo.vinculo"
                " where id = :v and consorcio_id = :c"
            ),
            {"v": vinculo_id, "c": ctx.consorcio_id},
        ).one_or_none()
    if v is None:
        raise ErrorApi(404, CodigoError.NO_ENCONTRADO, NO_ENCONTRADO)
    permiso.verificar(
        Entidad(
            recursos={TipoRecurso.UNIDAD: str(v.unidad_id)},
            persona_objetivo=v.persona_id,
            registrado_por=v.registrado_por,
            registrado_por_soporte=v.registrado_por is None and v.origen == ORIGEN_SOPORTE,
            respaldo_presente=True,  # el contrato exige la referencia
        )
    )
    respuesta = responder_una_vez(
        motor,
        ctx,
        idempotencia,
        lambda conn: (200, _confirmar(conn, ctx, vinculo_id, pedido.referencia)),
    )
    log.info(
        "confirmación de vínculo respondida",
        extra={
            "campos": {
                "consorcio_id": str(ctx.consorcio_id),
                "persona_id": str(ctx.persona_id),
                "vinculo_id": str(vinculo_id),
            }
        },
    )
    return respuesta
