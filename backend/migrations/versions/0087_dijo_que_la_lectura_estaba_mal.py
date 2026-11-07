"""«Se leyó mal» queda guardado y lo ve el Consejo (decisión 14 del propietario, 07-11-2026).

Cuando la lectura del comprobante no coincide con la orden y la persona elige «Se leyó mal», escribe
los datos a mano. Esa elección no se guardaba: el Consejo conciliaba sin saber que quien pagó
discutió lo que la IA leyó. `registrar_pago_manual` suma `lectura_mal` y el registro queda con
`lectura_disputada`. Sin una lectura de ese comprobante se niega (`sin_lectura`): no hay qué
discutir. No cambia ninguna regla: los datos escritos pasan por los mismos controles.

`pagos_manuales_lectura` lo entrega en la ficha y `acciones_de_ordenes_lectura`, en la fila de la
bandeja: solo el registro vigente sin conciliar (`registro_dijo_lectura_mal`).

**Exportación económica (`0069`):** `pago` suma `lectura_disputada`.

Downgrade: se niega (`downgrade_con_datos`) si algún registro lo dijo.

Revisión: 0087
Anterior: 0086
"""

from alembic import op
from sqlalchemy import text

revision = "0087"
down_revision = "0086"
branch_labels = None
depends_on = None

FIRMA = "uuid, integer, bytea, text, date, bigint, text, text, uuid, uuid, text, uuid"
REGISTRAR = f"nucleo.registrar_pago_manual({FIRMA})"
REGISTRAR_NUEVA = f"nucleo.registrar_pago_manual({FIRMA}, boolean)"
LECTURA = "nucleo.pagos_manuales_lectura(uuid, text, uuid, text, uuid)"
ACCIONES = "nucleo.acciones_de_ordenes_lectura(uuid, uuid, text, uuid, interval)"
EXPORTAR = "nucleo.exportar_pagos(uuid, uuid)"
DIJO = "nucleo.registro_dijo_lectura_mal(uuid, nucleo.orden_pago)"

TABLA = """
alter table nucleo.pago_manual add column lectura_disputada boolean not null default false;
"""

INTERNA = f"""
create function {DIJO.split("(")[0]}(c uuid, o nucleo.orden_pago)
  returns boolean
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select exists (
      select 1 from nucleo.ejecucion_orden_pago e
        join nucleo.pago_manual p on p.consorcio_id = e.consorcio_id and p.ejecucion_id = e.id
       where e.consorcio_id = c and e.orden_id = o.id and e.version = o.version_vigente
         and p.lectura_disputada
         and not exists (select 1 from nucleo.pago_manual q where q.corrige_a = p.id)
         and not exists (select 1 from nucleo.conciliacion_pago_manual k where k.pago_id = p.id))
  $$;
revoke all on function {DIJO} from public;
"""

REGISTRAR_CAMBIOS = (
    (
        "origen_pedido text, sesion uuid)\n RETURNS uuid",
        "origen_pedido text, sesion uuid, lectura_mal boolean DEFAULT false)\n RETURNS uuid",
    ),
    (
        "    -- El comprobante se cargó desde el pago de esta orden (0063): nació restringido.",
        "    if lectura_mal and not exists (\n"
        "         select 1 from nucleo.lectura_comprobante l\n"
        "          where l.consorcio_id = c and l.ejecucion_id = e.id\n"
        "            and l.documento_id = documento) then\n"
        "      raise exception 'ese comprobante no tiene una lectura' using errcode = 'P0001',\n"
        "        hint = 'sin_lectura';\n"
        "    end if;\n"
        "    -- El comprobante se cargó desde el pago de esta orden (0063): nació restringido.",
    ),
    (
        "       identidad_registro, fuera_de_termino, sin_facultad)\n",
        "       identidad_registro, fuera_de_termino, sin_facultad, lectura_disputada)\n",
    ),
    (
        "            identidad, tarde, sin_facultad)\n",
        "            identidad, tarde, sin_facultad, lectura_mal)\n",
    ),
)
LECTURA_CAMBIOS = (
    (" registrado_por_nombre text)\n", " registrado_por_nombre text, lectura_disputada boolean)\n"),
    ("p.fuera_de_termino, r.nombre\n", "p.fuera_de_termino, r.nombre, p.lectura_disputada\n"),
)
ACCIONES_CAMBIOS = (
    (
        " puede_corregir_diferencia boolean)\n",
        " puede_corregir_diferencia boolean, lectura_disputada boolean)\n",
    ),
    (
        "             ejecuta and nucleo.registro_la_diferencia(c, o, identidad)\n",
        "             ejecuta and nucleo.registro_la_diferencia(c, o, identidad),\n"
        "             nucleo.registro_dijo_lectura_mal(c, o)\n",
    ),
)
EXPORTAR_CAMBIOS = (
    (
        "                                'sin_facultad', p.sin_facultad)\n",
        "                                'sin_facultad', p.sin_facultad,\n"
        "                                'lectura_disputada', p.lectura_disputada)\n",
    ),
)

GUARDA_DOWNGRADE = """
lock table nucleo.pago_manual in access exclusive mode;
do $$
begin
  if exists (select 1 from nucleo.pago_manual where lectura_disputada) then
    raise exception '0087 no baja con registros que discuten la lectura: se perdería ese dato'
      using errcode = 'P0001', hint = 'downgrade_con_datos';
  end if;
end
$$;
"""


def _definicion(funcion: str, cambios, *, al_reves: bool) -> str:
    definicion = (
        op.get_bind()
        .execute(text("select pg_get_functiondef(cast(:f as regprocedure))"), {"f": funcion})
        .scalar_one()
    )
    for antes, despues in cambios:
        if al_reves:
            antes, despues = despues, antes
        if definicion.count(antes) != 1:
            raise RuntimeError(f"0087: {funcion} no tiene exactamente una vez {antes!r}")
        definicion = definicion.replace(antes, despues)
    return definicion


def _otra(desde: str, hasta: str, cambios, *, al_reves: bool = False) -> None:
    """Otra firma o lo que devuelve: se borra y se crea de nuevo, con su permiso."""
    definicion = _definicion(desde, cambios, al_reves=al_reves)
    op.execute(f"drop function {desde}")
    op.execute(definicion)
    op.execute(f"revoke all on function {hasta} from public")
    op.execute(f"grant execute on function {hasta} to cem_app")


def upgrade() -> None:
    op.execute(TABLA)
    op.execute(INTERNA)
    _otra(REGISTRAR, REGISTRAR_NUEVA, REGISTRAR_CAMBIOS)
    _otra(LECTURA, LECTURA, LECTURA_CAMBIOS)
    _otra(ACCIONES, ACCIONES, ACCIONES_CAMBIOS)
    op.execute(_definicion(EXPORTAR, EXPORTAR_CAMBIOS, al_reves=False))


def downgrade() -> None:
    op.execute(GUARDA_DOWNGRADE)
    op.execute(_definicion(EXPORTAR, EXPORTAR_CAMBIOS, al_reves=True))
    _otra(ACCIONES, ACCIONES, ACCIONES_CAMBIOS, al_reves=True)
    _otra(LECTURA, LECTURA, LECTURA_CAMBIOS, al_reves=True)
    _otra(REGISTRAR_NUEVA, REGISTRAR, REGISTRAR_CAMBIOS, al_reves=True)
    op.execute(f"drop function {DIJO}")
    op.execute("alter table nucleo.pago_manual drop column lectura_disputada")
