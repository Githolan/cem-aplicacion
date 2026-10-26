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

**Fin de vínculo (PA17, 2b-5).** La política `padron.gestionar` decide sobre el recurso de la
unidad. `nucleo.finalizar_vinculo` (migración 0020) cierra la vigencia hoy, conserva la fila y
revoca las sesiones de la persona solo si no le queda otro acceso al consorcio.

**Anulación (2b-5, corte 3).** Un vínculo que todavía no empezó no se finaliza: si se cargó por
error, se anula. `nucleo.anular_vinculo` (0025) lo borra; por la API deja un evento con sus
datos, sin el motivo (E31). Por la API, con `padron.gestionar` sobre su unidad y solo sin
confirmar; uno confirmado lo anula el equipo por soporte, con la referencia del respaldo
(`scripts.anular_vinculo`), y desde `0034` sin evento: la constancia es la salida del script.
"""

import logging
from typing import Annotated
from uuid import UUID

from cem_contracts.errores import CodigoError
from cem_contracts.importacion import AnulacionVinculo, VinculoAnulado
from cem_contracts.padron import (
    ConfirmacionVinculo,
    FilaPadron,
    FinVinculo,
    Padron,
    VinculoConfirmado,
    VinculoFinalizado,
)
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
CONFLICTOS_FIN = {
    "vinculo_finalizado": (CodigoError.VINCULO_FINALIZADO, "El vínculo ya terminó."),
    "vinculo_no_iniciado": (
        CodigoError.VINCULO_NO_INICIADO,
        "El vínculo todavía no empezó: no se puede finalizar.",
    ),
}
"""Lo mismo para `nucleo.finalizar_vinculo`."""
CONFLICTOS_ANULAR = {
    "vinculo_iniciado": (
        CodigoError.VINCULO_INICIADO,
        "El vínculo ya empezó: no se anula, se finaliza.",
    ),
    "vinculo_confirmado": (
        CodigoError.VINCULO_CONFIRMADO,
        "El vínculo está confirmado: lo anula el equipo, con el respaldo.",
    ),
}
"""Lo mismo para `nucleo.anular_vinculo`."""


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
                        " vigente_desde, vigente_hasta, confirmado,"
                        # La misma fecha y la misma regla que nucleo.finalizar_vinculo (0020):
                        # now() es el de la transacción, el mismo que usa padron_del_consorcio.
                        " vigente_desde <= nucleo.hoy_del_consorcio(:c) as iniciado"
                        " from nucleo.padron_del_consorcio()"
                    ),
                    {"c": ctx.consorcio_id},
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


def _ejecutar(conn: Connection, sql: str, parametros: dict, conflictos: dict) -> None:
    """Corre la función de escritura y traduce a 409 los motivos que dependen del estado."""
    try:
        conn.execute(text(sql), parametros)
    except DBAPIError as e:
        motivo = e.orig.diag.message_hint
        if motivo in conflictos:
            codigo, mensaje = conflictos[motivo]
            raise ErrorApi(409, codigo, mensaje) from e
        raise


def _vinculo_del_consorcio(motor, ctx, vinculo_id: UUID):
    """El vínculo, filtrado por el consorcio de la ruta: la RLS deja leer vínculos de otros
    consorcios de la persona, y uno de otro consorcio responde igual que uno inexistente."""
    with transaccion(motor, persona_id=ctx.persona_id, consorcio_id=ctx.consorcio_id) as conn:
        v = conn.execute(
            text(
                "select unidad_id, persona_id, registrado_por, origen from nucleo.vinculo"
                " where id = :v and consorcio_id = :c"
            ),
            {"v": vinculo_id, "c": ctx.consorcio_id},
        ).one_or_none()
    if v is None:
        raise ErrorApi(404, CodigoError.NO_ENCONTRADO, NO_ENCONTRADO)
    return v


def _confirmar(conn: Connection, ctx, vinculo_id: UUID, referencia: str) -> VinculoConfirmado:
    _ejecutar(
        conn,
        "select nucleo.confirmar_vinculo(:v, :r, :a, 'api')",
        {"v": vinculo_id, "r": referencia, "a": ctx.persona_id},
        CONFLICTOS,
    )
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
    v = _vinculo_del_consorcio(motor, ctx, vinculo_id)
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


def _finalizar(conn: Connection, ctx, vinculo_id: UUID, motivo: str) -> VinculoFinalizado:
    _ejecutar(
        conn,
        "select nucleo.finalizar_vinculo(:v, :m, :a, 'api')",
        {"v": vinculo_id, "m": motivo, "a": ctx.persona_id},
        CONFLICTOS_FIN,
    )
    fila = (
        conn.execute(
            text(
                "select id as vinculo_id, unidad_id, tipo::text as tipo, vigente_hasta,"
                " finalizado_en from nucleo.vinculo where id = :v"
            ),
            {"v": vinculo_id},
        )
        .mappings()
        .one()
    )
    return VinculoFinalizado(**fila)


@router.post(
    "/consorcios/{consorcio_id}/vinculos/{vinculo_id}/finalizar",
    response_model=VinculoFinalizado,
    summary="Finalizar un vínculo (PA17)",
    responses={
        403: {"description": "Sin `padron.gestionar` sobre esa unidad."},
        404: {"description": "El vínculo no existe en este consorcio."},
        409: {"description": "El vínculo ya terminó o todavía no empezó."},
    },
)
def finalizar(
    vinculo_id: UUID,
    pedido: FinVinculo,
    permiso: Annotated[Permiso, Depends(requiere("padron.gestionar"))],
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    """Cierra la vigencia hoy (día del consorcio) y conserva el vínculo con quién, cuándo y por
    qué. La persona pierde el acceso que le daba; si no le queda otro vínculo ni mandato vigente
    en el consorcio, se cierran sus sesiones. El motivo no sale en la respuesta ni en la
    auditoría."""
    ctx = permiso.ctx
    v = _vinculo_del_consorcio(motor, ctx, vinculo_id)
    permiso.verificar(sobre_unidad(v.unidad_id))
    respuesta = responder_una_vez(
        motor,
        ctx,
        idempotencia,
        lambda conn: (200, _finalizar(conn, ctx, vinculo_id, pedido.motivo)),
    )
    log.info(
        "fin de vínculo respondido",
        extra={
            "campos": {
                "consorcio_id": str(ctx.consorcio_id),
                "persona_id": str(ctx.persona_id),
                "vinculo_id": str(vinculo_id),
            }
        },
    )
    return respuesta


@router.post(
    "/consorcios/{consorcio_id}/vinculos/{vinculo_id}/anular",
    response_model=VinculoAnulado,
    summary="Anular un vínculo que todavía no empezó (carga errónea)",
    responses={
        403: {"description": "Sin `padron.gestionar` sobre esa unidad."},
        404: {"description": "El vínculo no existe en este consorcio."},
        409: {"description": "El vínculo ya empezó o está confirmado."},
    },
)
def anular(
    vinculo_id: UUID,
    pedido: AnulacionVinculo,
    permiso: Annotated[Permiso, Depends(requiere("padron.gestionar"))],
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    """Borra un vínculo sin confirmar que todavía no empezó y deja un evento con su unidad, su
    carácter y sus fechas. El motivo no sale en la respuesta ni en la auditoría. Uno que ya
    empezó se finaliza; uno confirmado lo anula el equipo."""
    ctx = permiso.ctx

    # La anulación borra el vínculo: buscarlo antes de `responder_una_vez` haría que un reintento
    # con la misma clave respondiera 404 en vez de repetir el 200 (Codex en app #105). Por eso la
    # búsqueda y la facultad van dentro del efecto, que un reintento no vuelve a correr.
    def efecto(conn: Connection) -> tuple[int, VinculoAnulado]:
        fila = (
            conn.execute(
                text(
                    "select unidad_id, tipo::text as tipo from nucleo.vinculo"
                    " where id = :v and consorcio_id = :c"
                ),
                {"v": vinculo_id, "c": ctx.consorcio_id},
            )
            .mappings()
            .one_or_none()
        )
        if fila is None:  # no existe, es de otro consorcio u otra anulación lo borró
            raise ErrorApi(404, CodigoError.NO_ENCONTRADO, NO_ENCONTRADO)
        permiso.verificar(sobre_unidad(fila["unidad_id"]))
        _ejecutar(
            conn,
            "select nucleo.anular_vinculo(:v, :m, null, :a, 'api')",
            {"v": vinculo_id, "m": pedido.motivo, "a": ctx.persona_id},
            CONFLICTOS_ANULAR,
        )
        return 200, VinculoAnulado(vinculo_id=vinculo_id, **fila)

    respuesta = responder_una_vez(motor, ctx, idempotencia, efecto)
    log.info(
        "anulación de vínculo respondida",
        extra={
            "campos": {
                "consorcio_id": str(ctx.consorcio_id),
                "persona_id": str(ctx.persona_id),
                "vinculo_id": str(vinculo_id),
            }
        },
    )
    return respuesta
