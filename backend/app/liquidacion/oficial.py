"""La liquidación oficial del período y quién la lee (diseño H5 §6, D3-A; migración `0119`).

La oficial de un período es la importación confirmada (una sola, `0119`); las reemplazadas quedan
como historia. Cada lectura evalúa su política y la base repite la barrera en su función
`security definer`; si la base niega lo que el evaluador concedió, es una discrepancia (500).

- **`GET …/liquidacion-oficial`** y **`GET …/liquidacion-oficial/{id}`**
  (`liquidacion.ver_importada`, el Consejo y la administración, con la facultad del panel): las
  oficiales de cada período, la vigente y las reemplazadas, y una con sus datos oficiales y sus
  diferencias. Nunca una importación sin confirmar (RN30).
- **`GET …/resumen`** (`fiscalizacion.ver_resumen`, cada propietario con el vínculo confirmado):
  de la liquidación vigente de un período, el gasto por rubro, los totales por grupo, el total y
  los vencimientos; y el total del gasto de cada uno de los últimos `PERIODOS_DE_GASTO` períodos
  con liquidación oficial (`0120`). Sin gastos, diferencias ni unidades.
- **`GET …/mi-cuenta`** (`cuenta.ver_propia`): lo que la liquidación vigente dice de cada unidad
  donde la persona es propietaria con el vínculo confirmado. La política pide la misma unidad
  (`misma_unidad`): la ruta la evalúa por cada unidad de sus vínculos y exige que la base devuelva
  exactamente esas, nunca otra (RN15). Una unidad propia que no figura en la liquidación vuelve con
  `figura` falso.

Las lecturas van en una transacción `REPEATABLE READ`: una confirmación en el medio no deja la
lista de períodos de un momento y la liquidación de otro.
"""

from collections.abc import Callable
from datetime import date
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

from cem_contracts.errores import CodigoError
from cem_contracts.estados import TipoVinculo
from cem_contracts.importacion_liquidacion import (
    PERIODOS_DE_GASTO,
    CampoOficial,
    CuentaDelPeriodo,
    DiferenciaLeida,
    GrupoDeLaUnidad,
    LiquidacionOficial,
    LiquidacionOficialResumen,
    MiCuenta,
    PeriodoOficial,
    ResumenDelPeriodo,
    ResumenLiquidacion,
    RubroDelPeriodo,
    TotalDelGrupo,
    TotalDelPeriodo,
    UnidadEnLaLiquidacion,
)
from fastapi import APIRouter, Depends, Query
from psycopg.errors import InsufficientPrivilege
from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError

from app.autorizacion.evaluador import Entidad
from app.autorizacion.requiere import NO_ENCONTRADO, Permiso, requiere
from app.consorcios.padron import Discrepancia, sobre_unidad
from app.contexto import MotorDep
from app.db import transaccion
from app.errores import ErrorApi, sin_cache

router = APIRouter(prefix="/api/v1", tags=["liquidaciones"], dependencies=[Depends(sin_cache)])

VER_IMPORTADA = "liquidacion.ver_importada"
VER_RESUMEN = "fiscalizacion.ver_resumen"
VER_PROPIA = "cuenta.ver_propia"
CLAVE_TOTAL = "total:gastos"
"""El dato con los totales del gasto y los vencimientos (`importacion.campos`)."""
PERIODO_INVALIDO = "El período es el primer día de un mes (AAAA-MM-01)."

PeriodoConsulta = Annotated[
    date | None,
    Query(description="El primer día del mes (`AAAA-MM-01`); sin él, el último con liquidación."),
]


def _leer[T](permiso: Permiso, motor, leer: Callable[[Connection], T]) -> T:
    ctx = permiso.ctx
    foto = motor.execution_options(isolation_level="REPEATABLE READ")
    try:
        with transaccion(foto, persona_id=ctx.persona_id, consorcio_id=ctx.consorcio_id) as c:
            return leer(c)
    except DBAPIError as e:
        if isinstance(e.orig, InsufficientPrivilege):
            raise Discrepancia(f"la base niega {permiso.accion} y el evaluador lo concede") from e
        raise


def _importe(valor: Any) -> Decimal | None:
    return None if valor is None else Decimal(valor)


def _periodo(periodo: date | None) -> None:
    if periodo is not None and periodo.day != 1:
        raise ErrorApi(422, CodigoError.PETICION_INVALIDA, PERIODO_INVALIDO)


def _periodos(c: Connection) -> list[PeriodoOficial]:
    filas = c.execute(text("select * from nucleo.periodos_liquidacion_oficial()")).mappings()
    return [PeriodoOficial(**f) for f in filas]


# --- El Consejo y la administración ------------------------------------------------------------


@router.get(
    "/consorcios/{consorcio_id}/liquidacion-oficial",
    response_model=list[LiquidacionOficialResumen],
    summary="Las liquidaciones oficiales de cada período: la vigente y las reemplazadas",
    responses={403: {"description": "Sin `fiscalizacion.ver_panel` en el consorcio."}},
)
def liquidaciones_oficiales(
    permiso: Annotated[Permiso, Depends(requiere(VER_IMPORTADA, al_entrar=True))],
    motor: MotorDep,
) -> list[LiquidacionOficialResumen]:
    consulta = text("select * from nucleo.liquidaciones_oficiales_lectura()")
    filas = _leer(permiso, motor, lambda c: c.execute(consulta).mappings().all())
    return [LiquidacionOficialResumen(**f) for f in filas]


@router.get(
    "/consorcios/{consorcio_id}/liquidacion-oficial/{importacion_id}",
    response_model=LiquidacionOficial,
    summary="Una liquidación oficial: sus datos oficiales y sus diferencias",
    responses={
        403: {"description": "Sin `fiscalizacion.ver_panel` en el consorcio."},
        404: {"description": "No es una liquidación oficial de este consorcio."},
    },
)
def liquidacion_oficial(
    importacion_id: UUID,
    permiso: Annotated[Permiso, Depends(requiere(VER_IMPORTADA, al_entrar=True))],
    motor: MotorDep,
) -> LiquidacionOficial:
    def leer(c: Connection) -> LiquidacionOficial:
        cabeza = c.execute(text("select * from nucleo.liquidacion_oficial_lectura(:i)"),
                           {"i": importacion_id}).mappings().one_or_none()  # fmt: skip
        if cabeza is None:
            raise ErrorApi(404, CodigoError.NO_ENCONTRADO, NO_ENCONTRADO)
        campos = c.execute(text("select * from nucleo.campos_liquidacion_oficial_lectura(:i)"),
                           {"i": importacion_id}).mappings()  # fmt: skip
        datos = dict(cabeza)
        datos["diferencias"] = [DiferenciaLeida(**d) for d in datos["diferencias"]]
        return LiquidacionOficial(**datos, campos=[CampoOficial(**f) for f in campos])

    return _leer(permiso, motor, leer)


# --- Cada propietario: el resumen --------------------------------------------------------------


@router.get(
    "/consorcios/{consorcio_id}/resumen",
    response_model=ResumenLiquidacion,
    summary="El gasto del período por rubro y el total de los últimos períodos según las"
    " liquidaciones oficiales, para cada propietario",
    responses={
        403: {"description": "Sin un vínculo de propietario confirmado en el consorcio."},
        404: {"description": "Ese período no tiene liquidación oficial."},
        422: {"description": "El período no es el primer día de un mes."},
    },
)
def resumen(
    permiso: Annotated[Permiso, Depends(requiere(VER_RESUMEN, al_entrar=True))],
    motor: MotorDep,
    periodo: PeriodoConsulta = None,
) -> ResumenLiquidacion:
    _periodo(periodo)

    def leer(c: Connection) -> ResumenLiquidacion:
        periodos = _periodos(c)
        filas = c.execute(text("select * from nucleo.resumen_liquidacion_oficial(:p)"),
                          {"p": periodo}).mappings().all()  # fmt: skip
        if not filas:
            if periodo is not None:
                raise ErrorApi(404, CodigoError.NO_ENCONTRADO, NO_ENCONTRADO)
            return ResumenLiquidacion(periodos=periodos, liquidacion=None, gasto_por_periodo=[])
        serie = c.execute(text("select * from nucleo.resumen_gasto_por_periodo(:n)"),
                          {"n": PERIODOS_DE_GASTO}).mappings()  # fmt: skip
        gasto_por_periodo = [TotalDelPeriodo(**f) for f in serie]
        total = next((f["valor"] for f in filas if f["tipo"] == "total"), {})
        rubros = [
            RubroDelPeriodo(numero=f["valor"]["numero"], nombre=f["valor"]["nombre"],
                            total=_importe(f["valor"]["total"]))
            for f in filas if f["tipo"] == "rubro"
        ]  # fmt: skip
        return ResumenLiquidacion(periodos=periodos, liquidacion=ResumenDelPeriodo(
            importacion_id=filas[0]["importacion_id"], periodo=filas[0]["periodo"],
            confirmada_en=filas[0]["confirmada_en"], rubros=rubros,
            grupos=[TotalDelGrupo(nombre=n, total=_importe(t))
                    for n, t in (total.get("grupos") or {}).items()],
            total=_importe(total.get("total")), vencimientos=total.get("vencimientos") or [],
        ), gasto_por_periodo=gasto_por_periodo)  # fmt: skip

    return _leer(permiso, motor, leer)


# --- Cada propietario: sus unidades ------------------------------------------------------------


def _unidad(unidad_id: UUID, designacion: str, datos: dict[str, dict]) -> UnidadEnLaLiquidacion:
    saldo = datos.get("saldo_unidad", {})
    importe = datos.get("importe_unidad", {})
    return UnidadEnLaLiquidacion(
        unidad_id=unidad_id, designacion=designacion, figura=bool(datos),
        saldo_anterior=_importe(saldo.get("saldo_anterior")), pagos=_importe(saldo.get("pagos")),
        deuda=_importe(saldo.get("deuda")), interes=_importe(saldo.get("interes")),
        disponible=_importe(saldo.get("disponible")),
        grupos=[GrupoDeLaUnidad(nombre=n, porcentaje=_importe(g.get("porcentaje")),
                                monto=_importe(g.get("monto")))
                for n, g in (importe.get("grupos") or {}).items()],
        otros_gastos=_importe(importe.get("otros_gastos")),
        redondeo=_importe(importe.get("redondeo")), total=_importe(importe.get("total")),
    )  # fmt: skip


@router.get(
    "/consorcios/{consorcio_id}/mi-cuenta",
    response_model=MiCuenta,
    summary="Lo que la liquidación oficial dice de cada unidad propia: saldo, pagos y total",
    responses={
        403: {"description": "Sin un vínculo de propietario confirmado en el consorcio."},
        404: {"description": "Ese período no tiene liquidación oficial."},
        422: {"description": "El período no es el primer día de un mes."},
    },
)
def mi_cuenta(
    permiso: Annotated[Permiso, Depends(requiere(VER_PROPIA))],
    motor: MotorDep,
    periodo: PeriodoConsulta = None,
) -> MiCuenta:
    ctx = permiso.ctx
    propias = {
        v.unidad_id
        for v in ctx.vinculos
        if v.tipo == TipoVinculo.PROPIETARIO
        and permiso.decidir(sobre_unidad(v.unidad_id)).permitido
    }
    if not propias:
        permiso.verificar(Entidad())  # niega, audita y responde 403
        raise Discrepancia(f"el evaluador niega {VER_PROPIA} en cada unidad y después concede")
    _periodo(periodo)

    def leer(c: Connection) -> MiCuenta:
        periodos = _periodos(c)
        filas = c.execute(text("select * from nucleo.mi_cuenta_liquidacion_oficial(:p)"),
                          {"p": periodo}).mappings().all()  # fmt: skip
        if not filas:
            if periodo is not None:
                raise ErrorApi(404, CodigoError.NO_ENCONTRADO, NO_ENCONTRADO)
            return MiCuenta(periodos=periodos, liquidacion=None)
        # RN15: la base devuelve exactamente las unidades que el evaluador concede, ni una más.
        if {f["unidad_id"] for f in filas} != propias:
            raise Discrepancia(f"la base y el evaluador difieren en las unidades de {VER_PROPIA}")
        designaciones: dict[UUID, str] = {}
        datos: dict[UUID, dict[str, dict]] = {}
        for f in filas:
            designaciones[f["unidad_id"]] = f["designacion"]
            de_la_unidad = datos.setdefault(f["unidad_id"], {})
            if f["tipo"] is None:
                continue
            if f["tipo"] in de_la_unidad:
                raise RuntimeError(f"la liquidación oficial tiene dos {f['tipo']} de una unidad")
            de_la_unidad[f["tipo"]] = f["valor"]
        return MiCuenta(periodos=periodos, liquidacion=CuentaDelPeriodo(
            importacion_id=filas[0]["importacion_id"], periodo=filas[0]["periodo"],
            confirmada_en=filas[0]["confirmada_en"],
            vencimientos=filas[0]["vencimientos"] or [],
            unidades=[_unidad(u, designaciones[u], datos[u]) for u in designaciones],
        ))  # fmt: skip

    return _leer(permiso, motor, leer)
