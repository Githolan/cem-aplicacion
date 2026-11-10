"""Panel del Consejo (H4a, corte 1; RF48), en solo lectura.

Diseño: `doc/diseno/H4a_1_Panel_del_Consejo.md` del repositorio CEM. La política
(`fiscalizacion.ver_panel`) decide al entrar; la base repite la barrera en cada lectura (`0094`).
Las tres lecturas van en una sola transacción `REPEATABLE READ`: un pago confirmado en el medio no
puede dejar el gráfico de un momento y la tabla de otro.
"""

from typing import Annotated

from cem_contracts.fiscalizacion import (
    ComprobanteFaltante,
    GastoPagado,
    OrdenesPorEstado,
    PanelFiscalizacion,
)
from fastapi import APIRouter, Depends
from psycopg.errors import InsufficientPrivilege
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.autorizacion.requiere import Permiso, requiere
from app.contexto import MotorDep
from app.db import transaccion
from app.errores import sin_cache

router = APIRouter(prefix="/api/v1", tags=["consorcios"], dependencies=[Depends(sin_cache)])

VER_PANEL = "fiscalizacion.ver_panel"


class Discrepancia(RuntimeError):
    """La base y el evaluador no coinciden: un defecto, no un caso de uso."""


@router.get(
    "/consorcios/{consorcio_id}/panel",
    response_model=PanelFiscalizacion,
    summary="Panel del Consejo: órdenes por estado, gasto pagado y comprobantes faltantes",
    responses={403: {"description": "Sin `fiscalizacion.ver_panel` en el consorcio."}},
)
def ver_panel(
    permiso: Annotated[Permiso, Depends(requiere(VER_PANEL, al_entrar=True))], motor: MotorDep
) -> PanelFiscalizacion:
    ctx = permiso.ctx
    foto = motor.execution_options(isolation_level="REPEATABLE READ")
    try:
        with transaccion(foto, persona_id=ctx.persona_id, consorcio_id=ctx.consorcio_id) as c:

            def leer(funcion: str) -> list:
                return list(c.execute(text(f"select * from nucleo.{funcion}()")).mappings())

            estados = leer("panel_ordenes_por_estado")
            gasto = leer("panel_gasto_pagado")
            faltantes = leer("panel_comprobantes_faltantes")
    except DBAPIError as e:
        if isinstance(e.orig, InsufficientPrivilege):
            raise Discrepancia(f"la base niega {VER_PANEL} y el evaluador lo concede") from e
        raise
    return PanelFiscalizacion(
        ordenes_por_estado=[OrdenesPorEstado(**f) for f in estados],
        gasto_pagado=[GastoPagado(**f) for f in gasto],
        comprobantes_faltantes=[ComprobanteFaltante(**f) for f in faltantes],
    )
