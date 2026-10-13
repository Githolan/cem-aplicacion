"""Selector de edificio y padrón de unidades."""

from cem_contracts.consorcios import ConsorcioResumen, ListaUnidades, Unidad
from fastapi import APIRouter
from sqlalchemy import text

from app.contexto import ContextoDep, IdentidadDep, MotorDep
from app.db import transaccion

router = APIRouter(prefix="/api/v1", tags=["consorcios"])


@router.get("/me/consorcios", response_model=list[ConsorcioResumen])
def mis_consorcios(identidad: IdentidadDep, motor: MotorDep) -> list[ConsorcioResumen]:
    """Consorcios donde la persona tiene al menos un vínculo vigente."""
    with transaccion(motor, persona_id=identidad.persona_id) as conn:
        ids = list(
            conn.execute(
                text(
                    "select distinct consorcio_id from nucleo.vinculo"
                    " where persona_id = :p"
                    " and daterange(vigente_desde, vigente_hasta) @> current_date"
                ),
                {"p": identidad.persona_id},
            ).scalars()
        )
    if not ids:
        return []
    # Ruta de persona: la política alternativa filtra por la lista de consorcios propios (§8.5).
    with transaccion(motor, persona_id=identidad.persona_id, consorcios_persona=ids) as conn:
        filas = conn.execute(
            text(
                "select c.id, c.nombre,"
                " array_agg(v.tipo::text order by v.tipo) as vinculos"
                " from nucleo.consorcio c"
                " join nucleo.vinculo v on v.consorcio_id = c.id"
                " where v.persona_id = :p"
                " and daterange(v.vigente_desde, v.vigente_hasta) @> current_date"
                " and c.eliminado_en is null"
                " group by c.id, c.nombre order by c.nombre"
            ),
            {"p": identidad.persona_id},
        ).mappings()
        return [ConsorcioResumen(**f) for f in filas]


@router.get("/consorcios/{consorcio_id}/unidades", response_model=ListaUnidades)
def unidades(ctx: ContextoDep, motor: MotorDep) -> ListaUnidades:
    with transaccion(motor, persona_id=ctx.persona_id, consorcio_id=ctx.consorcio_id) as conn:
        filas = conn.execute(
            text(
                "select id, designacion from nucleo.unidad"
                " where eliminado_en is null order by designacion"
            )
        ).mappings()
        return ListaUnidades(elementos=[Unidad(**f) for f in filas])
