"""Selector de edificio y padrón de unidades."""

from typing import Annotated

from cem_contracts.consorcios import ConsorcioResumen, ListaUnidades, Unidad
from fastapi import APIRouter, Depends
from sqlalchemy import text

from app.autorizacion.requiere import Permiso, requiere
from app.contexto import IdentidadDep, MotorDep
from app.db import transaccion

router = APIRouter(prefix="/api/v1", tags=["consorcios"])


@router.get("/me/consorcios", response_model=list[ConsorcioResumen])
def mis_consorcios(identidad: IdentidadDep, motor: MotorDep) -> list[ConsorcioResumen]:
    """Consorcios donde la persona tiene un vínculo vigente o un mandato vigente, con la fecha
    de cada consorcio. Un vínculo importado pendiente no cuenta (RF02, `0024`): sin otro
    vínculo ni mandato, la lista queda vacía y el consorcio no entra en `consorcios_persona`."""
    # Ruta de la persona (alcance `propia`, fuera de la matriz; C4). "Vigente" es con la fecha de
    # **cada** consorcio (`nucleo.hoy_del_consorcio`), que solo se puede leer con el consorcio en
    # el ámbito: la primera consulta junta candidatos (vínculos propios de cualquier fecha y los
    # consorcios de `nucleo.consorcios_con_mandato_vigente()`, que ya aplica la fecha de cada
    # uno y devuelve solo ids) y la segunda decide con la fecha de cada consorcio.
    p = identidad.persona_id
    with transaccion(motor, persona_id=p) as conn:
        por_vinculo = set(
            conn.execute(
                text(
                    "select distinct consorcio_id from nucleo.vinculo where persona_id = :p"
                    " and id not in (select nucleo.vinculos_importados_pendientes())"
                ),
                {"p": p},
            ).scalars()
        )
        por_mandato = set(
            conn.execute(text("select nucleo.consorcios_con_mandato_vigente()")).scalars()
        )
    ids = sorted(por_vinculo | por_mandato)
    if not ids:
        return []
    # Ruta de persona: la política alternativa filtra por la lista de consorcios propios (§8.5).
    with transaccion(motor, persona_id=p, consorcios_persona=ids) as conn:
        filas = conn.execute(
            text(
                "select c.id, c.nombre,"
                " coalesce(array_agg(v.tipo::text order by v.tipo)"
                "          filter (where v.tipo is not null), '{}') as vinculos"
                " from nucleo.consorcio c"
                " left join nucleo.vinculo v on v.consorcio_id = c.id and v.persona_id = :p"
                "  and daterange(v.vigente_desde, v.vigente_hasta)"
                "      @> nucleo.hoy_del_consorcio(c.id)"
                "  and v.id not in (select nucleo.vinculos_importados_pendientes())"
                " where c.id = any(:ids) and c.eliminado_en is null"
                " group by c.id, c.nombre"
                " having count(v.tipo) > 0 or c.id = any(:mandatos)"
                " order by c.nombre"
            ),
            {"p": p, "ids": ids, "mandatos": sorted(por_mandato)},
        ).mappings()
        return [ConsorcioResumen(**f) for f in filas]


@router.get("/consorcios/{consorcio_id}/unidades", response_model=ListaUnidades)
def unidades(
    permiso: Annotated[Permiso, Depends(requiere("unidades.listar", al_entrar=True))],
    motor: MotorDep,
) -> ListaUnidades:
    """Id y designación de las unidades, sin personas (D6). Concede por ámbito: cualquier
    vínculo vigente o mandato vigente con cuenta (tabla de verdad, §2.1)."""
    ctx = permiso.ctx
    with transaccion(motor, persona_id=ctx.persona_id, consorcio_id=ctx.consorcio_id) as conn:
        filas = conn.execute(
            text(
                "select id, designacion from nucleo.unidad"
                " where eliminado_en is null order by designacion"
            )
        ).mappings()
        return ListaUnidades(elementos=[Unidad(**f) for f in filas])
