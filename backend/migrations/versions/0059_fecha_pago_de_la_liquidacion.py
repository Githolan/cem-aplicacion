"""Liquidación: una sola fecha de pago para la regla del período y para la pantalla (P3 139).

`0058` decide si un gasto entra en el período con la fecha del pago: con pago manual, la de la
operación del pago conciliado `conforme`; con el riel, `registrado_en` en la zona del consorcio.
Pero las dos lecturas seguían devolviendo `registrado_en`, que con pago manual es el momento de
la conciliación: un pago del 30-10 conciliado el 02-11 entraba en octubre y la pantalla decía
«pagada el 02-11». Ahora las tres usan `nucleo.fecha_pago_ejecucion`, y las lecturas devuelven
esa fecha (`pagada_el`, una fecha sin hora) en lugar de `pagada_en`.

Las funciones de `0057` y `0058` se reescriben sobre su texto (`pg_get_functiondef`), como `0058`.
Las lecturas cambian el tipo que devuelven, así que se borran y se vuelven a crear con sus permisos.
"""

from alembic import op
from sqlalchemy import text

revision = "0059"
down_revision = "0058"
branch_labels = None
depends_on = None

FECHA = "nucleo.fecha_pago_ejecucion(uuid, uuid)"
AGREGAR = "nucleo.agregar_gasto_liquidacion(uuid, uuid, text, uuid, text, uuid)"
GASTOS = "nucleo.liquidacion_gastos_lectura(uuid, uuid, text, uuid)"
LIQUIDABLES = "nucleo.ordenes_liquidables_lectura(uuid, text, uuid)"

UPGRADE = f"""
-- La fecha en que se pagó la ejecución, en el calendario del consorcio; null si no se pagó.
create function nucleo.fecha_pago_ejecucion(c uuid, ejecucion uuid)
  returns date
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select case when r.origen = 'manual' then
                  (select p.fecha_operacion from nucleo.pago_manual p
                     join nucleo.conciliacion_pago_manual k
                       on k.pago_id = p.id and k.resultado = 'conforme'
                    where p.consorcio_id = c and p.ejecucion_id = ejecucion)
                else (r.registrado_en at time zone x.zona_horaria)::date end
      from nucleo.resultado_ejecucion_orden_pago r
      join nucleo.consorcio x on x.id = c
     where r.ejecucion_id = ejecucion and r.resultado = 'pagada'
  $$;
revoke all on function {FECHA} from public;
"""

AGREGAR_ANTES = (
    "    if (select case when r.origen = 'manual' then\n"
    "                 (select p.fecha_operacion from nucleo.pago_manual p\n"
    "                    join nucleo.conciliacion_pago_manual k\n"
    "                      on k.pago_id = p.id and k.resultado = 'conforme'\n"
    "                   where p.consorcio_id = c and p.ejecucion_id = e.id)\n"
    "               else (r.registrado_en at time zone x.zona_horaria)::date end\n"
    "          from nucleo.resultado_ejecucion_orden_pago r\n"
    "          join nucleo.consorcio x on x.id = c\n"
    "         where r.ejecucion_id = e.id and r.resultado = 'pagada')\n"
)
AGREGAR_DESPUES = "    if nucleo.fecha_pago_ejecucion(c, e.id)\n"

FECHA_DEL_PAGO = "nucleo.fecha_pago_ejecucion(c, e.id)"
GASTOS_CAMBIOS = (
    (" pagada_en timestamp with time zone,", " pagada_el date,"),
    ("             r.registrado_en, s.grupo,", f"             {FECHA_DEL_PAGO}, s.grupo,"),
    ("       order by r.registrado_en, g.id;", f"       order by {FECHA_DEL_PAGO}, g.id;"),
)
LIQUIDABLES_CAMBIOS = (
    (" pagada_en timestamp with time zone)", " pagada_el date)"),
    ("e.importe_centavos, e.moneda, r.registrado_en\n",
     f"e.importe_centavos, e.moneda,\n             {FECHA_DEL_PAGO}\n"),
    ("       order by r.registrado_en, o.id;", f"       order by {FECHA_DEL_PAGO}, o.id;"),
)  # fmt: skip


def _definicion(funcion: str) -> str:
    return (
        op.get_bind()
        .execute(text("select pg_get_functiondef(cast(:f as regprocedure))"), {"f": funcion})
        .scalar_one()
    )


def _cambiar(definicion: str, cambios: tuple[tuple[str, str], ...], funcion: str) -> str:
    for antes, despues in cambios:
        if definicion.count(antes) != 1:
            raise RuntimeError(f"0059: {funcion} no tiene exactamente una vez {antes!r}")
        definicion = definicion.replace(antes, despues)
    return definicion


def _reescribir(funcion: str, cambios: tuple[tuple[str, str], ...], recrear: bool) -> None:
    """Con `recrear`, el tipo que devuelve cambia: se borra y se crea con los mismos permisos."""
    nueva = _cambiar(_definicion(funcion), cambios, funcion)
    if recrear:
        op.execute(f"drop function {funcion}")
    op.execute(nueva)
    if recrear:
        op.execute(f"revoke all on function {funcion} from public")
        op.execute(f"grant execute on function {funcion} to cem_app")


def _al_reves(cambios: tuple[tuple[str, str], ...]) -> tuple[tuple[str, str], ...]:
    return tuple((despues, antes) for antes, despues in cambios)


def upgrade() -> None:
    op.execute(UPGRADE)
    _reescribir(AGREGAR, ((AGREGAR_ANTES, AGREGAR_DESPUES),), recrear=False)
    _reescribir(GASTOS, GASTOS_CAMBIOS, recrear=True)
    _reescribir(LIQUIDABLES, LIQUIDABLES_CAMBIOS, recrear=True)


def downgrade() -> None:
    _reescribir(LIQUIDABLES, _al_reves(LIQUIDABLES_CAMBIOS), recrear=True)
    _reescribir(GASTOS, _al_reves(GASTOS_CAMBIOS), recrear=True)
    _reescribir(AGREGAR, ((AGREGAR_DESPUES, AGREGAR_ANTES),), recrear=False)
    op.execute(f"drop function {FECHA}")
