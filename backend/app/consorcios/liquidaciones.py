"""Liquidación neutral: borrador por período, gastos y simulación del reparto (H3; `0057`).

Mandato del propietario del 31-10-2026: lo que no depende de las decisiones 4.2 y 5. Todo con
`liquidacion.preparar`: abrir el borrador de un período, agregarle órdenes pagadas con su grupo
de gasto, quitarlas y ver la simulación. No hay revisión, aprobación ni publicación todavía.

Las funciones de la base exigen R10 (como toda lectura de egresos: leen importes y proveedores
de órdenes pagadas), aunque la política de `liquidacion.preparar` no sea económica: si el
segundo factor no es reciente, la base lo dice con el mismo código que el evaluador
(`segundo_factor_requerido`) y la pantalla lo pide. Si la base niega lo que el evaluador
concedió por facultad, es una discrepancia (500). Toda escritura lleva `Idempotency-Key`.

La simulación reparte cada grupo con `repartir_por_esquema` (`app/liquidacion`): el total del
grupo se distribuye exacto entre las unidades del esquema, sin ajustar coeficientes; si el
esquema no coincide con las unidades activas, la simulación sale con el bloqueo y sin unidades.
"""

from collections import defaultdict
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from cem_contracts.errores import CodigoError
from cem_contracts.liquidaciones import (
    GastoAgregado,
    GastoLiquidacion,
    GastoNuevo,
    GrupoLiquidable,
    GrupoSimulado,
    Liquidacion,
    LiquidacionCreada,
    LiquidacionDetalle,
    LiquidacionNueva,
    ListaLiquidaciones,
    ListaOrdenesLiquidables,
    OrdenLiquidable,
    SimulacionLiquidacion,
    UnidadSimulada,
)
from fastapi import APIRouter, Depends, Response
from sqlalchemy import Connection, text

from app.autorizacion.requiere import NO_ENCONTRADO, Permiso, requiere
from app.consorcios.egresos import RESPUESTAS_ESCRITURA, RESPUESTAS_LECTURA, _ejecutar, _lectura
from app.contexto import MotorDep
from app.errores import ErrorApi, sin_cache
from app.liquidacion.esquemas import repartir_por_esquema
from app.liquidacion.reparto import RepartoBloqueado
from app.plataforma.idempotencia import IdempotenciaDep, responder_una_vez

router = APIRouter(prefix="/api/v1", tags=["liquidaciones"], dependencies=[Depends(sin_cache)])

PREPARAR = "liquidacion.preparar"

NO_LIQUIDABLE = (409, CodigoError.ORDEN_NO_LIQUIDABLE)
ERRORES = {
    "no_encontrado": (404, CodigoError.NO_ENCONTRADO, NO_ENCONTRADO),
    "liquidacion_en_borrador": (
        409, CodigoError.LIQUIDACION_NO_ADMITE,
        "Ya hay un borrador de ese período: seguí trabajando en ese.",
    ),
    "liquidacion_no_admite": (
        409, CodigoError.LIQUIDACION_NO_ADMITE,
        "La liquidación no admite eso: ya no es un borrador o el gasto ya se quitó.",
    ),
    "periodo_invalido": (
        422, CodigoError.PETICION_INVALIDA, "El período es el primer día de un mes."
    ),
    "orden_no_pagada": (*NO_LIQUIDABLE, "Solo se liquida una orden pagada."),
    "gasto_ya_liquidado": (*NO_LIQUIDABLE, "La orden ya está en una liquidación."),
    "moneda_distinta": (
        *NO_LIQUIDABLE, "La orden es de otra moneda que los gastos de esta liquidación."
    ),
    "sin_esquema": (
        422, CodigoError.SIN_ESQUEMA,
        "Ese grupo no tiene un esquema de coeficientes vigente en el período.",
    ),
}  # fmt: skip

RESPUESTAS_GASTO = RESPUESTAS_ESCRITURA | {
    422: {"description": "El grupo no tiene un esquema vigente en el período."}
}


def _liquidaciones(c: Connection, permiso: Permiso, liquidacion: UUID | None) -> list[Liquidacion]:
    filas = _ejecutar(
        c, permiso, "select * from nucleo.liquidaciones_lectura(:l, :actor, 'api', :sesion)",
        {"l": liquidacion}, ERRORES,
    ).mappings()  # fmt: skip
    return [Liquidacion(**f) for f in filas]


def _simular(
    c: Connection, gastos: list[GastoLiquidacion], grupos_vigentes: list[GrupoLiquidable]
) -> SimulacionLiquidacion:
    """Cada esquema reparte el total de sus gastos; la unidad suma lo de cada grupo.

    Un gasto reparte con el esquema que guardó al agregarse. Si después se registró otro vigente
    el último día del período, ese gasto ya no reparte como corresponde: la simulación se
    bloquea y dice qué grupo hay que quitar y volver a agregar (revisión de 0057)."""
    totales: dict[UUID, int] = defaultdict(int)
    for g in gastos:
        totales[g.esquema_id] += g.importe_centavos
    esquemas = {g.esquema_id: g for g in gastos}
    grupos = sorted(
        (
            GrupoSimulado(grupo=g.grupo, esquema_id=e, esquema_version=g.esquema_version,
                          total_centavos=totales[e])
            for e, g in esquemas.items()
        ),
        key=lambda g: (g.grupo, g.esquema_version),
    )  # fmt: skip
    simulacion = {
        "moneda": gastos[0].moneda if gastos else None,
        "total_centavos": sum(totales.values()),
        "grupos": grupos,
    }
    vigentes = {g.grupo: g.esquema_id for g in grupos_vigentes}
    viejos = sorted({g.grupo for g in gastos if vigentes.get(g.grupo) != g.esquema_id})
    if viejos:
        motivo = (
            f"el esquema de {', '.join(viejos)} cambió después de agregar sus gastos:"
            " quitalos y volvé a agregarlos"
        )
        return SimulacionLiquidacion(**simulacion, unidades=[], bloqueo=motivo)
    try:
        repartos = {
            e: repartir_por_esquema(c, e, Decimal(total).scaleb(-2)) for e, total in totales.items()
        }
    except RepartoBloqueado as error:
        return SimulacionLiquidacion(**simulacion, unidades=[], bloqueo=str(error))
    por_unidad: dict[UUID, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for e, reparto in repartos.items():
        for unidad, importe in reparto.items():
            por_unidad[unidad][esquemas[e].grupo] += int(importe.scaleb(2))
    designaciones = dict(
        c.execute(
            text("select id, designacion from nucleo.unidad where id = any(:u)"),
            {"u": list(por_unidad)},
        ).all()
    )
    unidades = sorted(
        (
            UnidadSimulada(unidad_id=u, designacion=designaciones[u],
                           total_centavos=sum(grupo.values()), por_grupo=dict(grupo))
            for u, grupo in por_unidad.items()
        ),
        key=lambda u: (u.designacion, u.unidad_id),
    )  # fmt: skip
    return SimulacionLiquidacion(**simulacion, unidades=unidades, bloqueo=None)


@router.get(
    "/consorcios/{consorcio_id}/liquidaciones",
    response_model=ListaLiquidaciones,
    summary="Liquidaciones del consorcio, por período y versión (preparación)",
    responses=RESPUESTAS_LECTURA,
)
def listar_liquidaciones(
    permiso: Annotated[Permiso, Depends(requiere(PREPARAR, al_entrar=True))], motor: MotorDep
) -> ListaLiquidaciones:
    with _lectura(permiso, motor) as c:
        return ListaLiquidaciones(elementos=_liquidaciones(c, permiso, None))


@router.post(
    "/consorcios/{consorcio_id}/liquidaciones",
    status_code=201,
    response_model=LiquidacionCreada,
    summary="Abrir el borrador de un período (versión siguiente; uno solo a la vez)",
    responses=RESPUESTAS_ESCRITURA,
)
def preparar_liquidacion(
    nueva: LiquidacionNueva,
    permiso: Annotated[Permiso, Depends(requiere(PREPARAR, al_entrar=True))],
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    def efecto(conn: Connection) -> tuple[int, LiquidacionCreada]:
        liquidacion_id = _ejecutar(
            conn, permiso, "select nucleo.preparar_liquidacion(:p, :actor, 'api', :sesion)",
            {"p": nueva.periodo}, ERRORES,
        ).scalar_one()  # fmt: skip
        return 201, LiquidacionCreada(id=liquidacion_id)

    return responder_una_vez(motor, permiso.ctx, idempotencia, efecto)


@router.get(
    "/consorcios/{consorcio_id}/liquidaciones/ordenes-liquidables",
    response_model=ListaOrdenesLiquidables,
    summary="Órdenes pagadas que todavía no están en ninguna liquidación",
    responses=RESPUESTAS_LECTURA,
)
def listar_ordenes_liquidables(
    permiso: Annotated[Permiso, Depends(requiere(PREPARAR, al_entrar=True))], motor: MotorDep
) -> ListaOrdenesLiquidables:
    with _lectura(permiso, motor) as c:
        filas = _ejecutar(
            c, permiso, "select * from nucleo.ordenes_liquidables_lectura(:actor, 'api', :sesion)",
            {}, ERRORES,
        ).mappings()  # fmt: skip
        return ListaOrdenesLiquidables(elementos=[OrdenLiquidable(**f) for f in filas])


@router.get(
    "/consorcios/{consorcio_id}/liquidaciones/{liquidacion_id}",
    response_model=LiquidacionDetalle,
    summary="Un borrador con sus gastos, los grupos que se pueden usar y la simulación",
    responses=RESPUESTAS_LECTURA | {404: {"description": "No existe en este consorcio."}},
)
def ver_liquidacion(
    liquidacion_id: UUID,
    permiso: Annotated[Permiso, Depends(requiere(PREPARAR, al_entrar=True))],
    motor: MotorDep,
) -> LiquidacionDetalle:
    with _lectura(permiso, motor) as c:
        liquidaciones = _liquidaciones(c, permiso, liquidacion_id)
        if not liquidaciones:
            raise ErrorApi(*ERRORES["no_encontrado"])
        [liquidacion] = liquidaciones
        datos = {"l": liquidacion_id}
        gastos = [
            GastoLiquidacion(**f)
            for f in _ejecutar(
                c, permiso,
                "select * from nucleo.liquidacion_gastos_lectura(:l, :actor, 'api', :sesion)",
                datos, ERRORES,
            ).mappings()
        ]  # fmt: skip
        grupos = [
            GrupoLiquidable(**f)
            for f in _ejecutar(
                c, permiso,
                "select * from nucleo.grupos_liquidables_lectura(:l, :actor, 'api', :sesion)",
                datos, ERRORES,
            ).mappings()
        ]  # fmt: skip
        return LiquidacionDetalle(
            liquidacion=liquidacion,
            gastos=gastos,
            grupos=grupos,
            simulacion=_simular(c, gastos, grupos),
        )


@router.post(
    "/consorcios/{consorcio_id}/liquidaciones/{liquidacion_id}/gastos",
    status_code=201,
    response_model=GastoAgregado,
    summary="Agregar una orden pagada al borrador, con el grupo de gasto que la reparte",
    responses=RESPUESTAS_GASTO,
)
def agregar_gasto(
    liquidacion_id: UUID,
    nuevo: GastoNuevo,
    permiso: Annotated[Permiso, Depends(requiere(PREPARAR, al_entrar=True))],
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    def efecto(conn: Connection) -> tuple[int, GastoAgregado]:
        gasto_id = _ejecutar(
            conn, permiso,
            "select nucleo.agregar_gasto_liquidacion(:l, :o, :g, :actor, 'api', :sesion)",
            {"l": liquidacion_id, "o": nuevo.orden_id, "g": nuevo.grupo}, ERRORES,
        ).scalar_one()  # fmt: skip
        return 201, GastoAgregado(id=gasto_id)

    return responder_una_vez(motor, permiso.ctx, idempotencia, efecto)


@router.post(
    "/consorcios/{consorcio_id}/liquidaciones/{liquidacion_id}/gastos/{gasto_id}/quitar",
    response_model=GastoAgregado,
    summary="Quitar un gasto del borrador (queda en la historia)",
    responses=RESPUESTAS_ESCRITURA,
)
def quitar_gasto(
    liquidacion_id: UUID,
    gasto_id: UUID,
    permiso: Annotated[Permiso, Depends(requiere(PREPARAR, al_entrar=True))],
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    def efecto(conn: Connection) -> tuple[int, GastoAgregado]:
        _ejecutar(
            conn, permiso,
            "select nucleo.quitar_gasto_liquidacion(:l, :g, :actor, 'api', :sesion)",
            {"l": liquidacion_id, "g": gasto_id}, ERRORES,
        )  # fmt: skip
        return 200, GastoAgregado(id=gasto_id)

    return responder_una_vez(motor, permiso.ctx, idempotencia, efecto)
