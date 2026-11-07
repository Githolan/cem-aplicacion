"""El extracto tiene moneda (decisión 9 del propietario, 07-11-2026).

Un CSV de banco no dice la moneda: un pago de USD 1.000 podía coincidir con un débito de ARS 1.000 y
quedar cotejado. La moneda se pide al elegir las columnas, queda en `columnas_extracto` (ARS
sugerida, se puede cambiar al subir) y en cada línea de `movimiento_extracto`, y se compara.
`cotejar_por_extracto` no le cree a la API: vuelve a mirarla (`movimiento_no_coincide`).

Lo que ya estaba guardado es ARS, que es lo único que había: `default 'ARS'` lo dice una sola vez y
la función siempre la manda. El hash de línea no cambia para ARS, así que reimportar un extracto
viejo no duplica nada (`app/consorcios/extracto.py`).

**Exportación económica (`0069`):** `movimiento_extracto` y `columnas_extracto` suman `moneda`.

Downgrade: se niega (`downgrade_con_datos`) si hay una moneda que no sea ARS.

Revisión: 0085
Anterior: 0084
"""

from alembic import op
from sqlalchemy import text

revision = "0085"
down_revision = "0084"
branch_labels = None
depends_on = None

COLUMNAS = "nucleo.columnas_extracto_lectura(uuid, text, uuid)"
FIRMA = "text, date, date, jsonb, text, text, text, text, uuid, text, uuid"
IMPORTAR = f"nucleo.importar_extracto({FIRMA})"
IMPORTAR_NUEVA = f"nucleo.importar_extracto({FIRMA}, text)"
POR_EXTRACTO = "nucleo.cotejar_por_extracto(jsonb, uuid, text, uuid)"
EXPORTAR = "nucleo.exportar_pagos(uuid, uuid)"

TABLA = """
alter table nucleo.columnas_extracto
  add column moneda text not null default 'ARS' check (moneda ~ '^[A-Z]{3}$');
alter table nucleo.movimiento_extracto
  add column moneda text not null default 'ARS' check (moneda ~ '^[A-Z]{3}$');
"""

COLUMNAS_CAMBIOS = (
    ("columna_numero text, signo text)\n", "columna_numero text, signo text, moneda text)\n"),
    ("x.columna_numero, x.signo\n", "x.columna_numero, x.signo, x.moneda\n"),
)
IMPORTAR_CAMBIOS = (
    (
        "actor uuid, origen_pedido text, sesion uuid)\n",
        "actor uuid, origen_pedido text, sesion uuid, moneda_extracto text)\n",
    ),
    (
        "numero_operacion text, nuevo boolean, pago_id uuid)\n",
        "numero_operacion text, nuevo boolean, pago_id uuid, moneda text)\n",
    ),
    (
        "        (consorcio_id, importacion_id, hash_linea, fecha, importe_centavos,"
        " numero_operacion)\n",
        "        (consorcio_id, importacion_id, hash_linea, fecha, importe_centavos,"
        " numero_operacion,\n         moneda)\n",
    ),
    (
        "      select c, importacion, m.hash_linea, m.fecha, m.importe_centavos,"
        " m.numero_operacion\n",
        "      select c, importacion, m.hash_linea, m.fecha, m.importe_centavos,"
        " m.numero_operacion,\n             moneda_extracto\n",
    ),
    (
        "      (consorcio_id, columna_fecha, columna_importe, columna_numero, signo,"
        " guardadas_por)\n",
        "      (consorcio_id, columna_fecha, columna_importe, columna_numero, signo, moneda,\n"
        "       guardadas_por)\n",
    ),
    (
        "    values (c, col_fecha, col_importe, col_numero, signo_importe, actor)\n",
        "    values (c, col_fecha, col_importe, col_numero, signo_importe, moneda_extracto,\n"
        "            actor)\n",
    ),
    (
        "          columna_numero = excluded.columna_numero, signo = excluded.signo,\n",
        "          columna_numero = excluded.columna_numero, signo = excluded.signo,\n"
        "          moneda = excluded.moneda,\n",
    ),
    (
        "      where (x.columna_fecha, x.columna_importe, x.columna_numero, x.signo)\n",
        "      where (x.columna_fecha, x.columna_importe, x.columna_numero, x.signo, x.moneda)\n",
    ),
    (
        "                              excluded.columna_numero, excluded.signo);\n",
        "                              excluded.columna_numero, excluded.signo,\n"
        "                              excluded.moneda);\n",
    ),
    (
        "             m.hash_linea = any(nuevos), coalesce(t.pago_id, t.diferencia_id)\n",
        "             m.hash_linea = any(nuevos), coalesce(t.pago_id, t.diferencia_id), m.moneda\n",
    ),
)
POR_EXTRACTO_CAMBIOS = (
    (
        "      if m.importe_centavos <> f.importe_centavos\n",
        "      if m.importe_centavos <> f.importe_centavos or m.moneda <> f.moneda\n",
    ),
)
EXPORTAR_CAMBIOS = (
    (
        "'importe_centavos', m.importe_centavos,",
        "'importe_centavos', m.importe_centavos, 'moneda', m.moneda,",
    ),
    ("'signo', x.signo", "'signo', x.signo, 'moneda', x.moneda"),
)

GUARDA_DOWNGRADE = """
lock table nucleo.movimiento_extracto, nucleo.columnas_extracto in access exclusive mode;
do $$
begin
  if exists (select 1 from nucleo.movimiento_extracto where moneda <> 'ARS')
     or exists (select 1 from nucleo.columnas_extracto where moneda <> 'ARS') then
    raise exception '0085 no baja con extractos en otra moneda: se perdería cuál era'
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
            raise RuntimeError(f"0085: {funcion} no tiene exactamente una vez {antes!r}")
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
    _otra(COLUMNAS, COLUMNAS, COLUMNAS_CAMBIOS)
    _otra(IMPORTAR, IMPORTAR_NUEVA, IMPORTAR_CAMBIOS)
    for funcion, cambios in ((POR_EXTRACTO, POR_EXTRACTO_CAMBIOS), (EXPORTAR, EXPORTAR_CAMBIOS)):
        op.execute(_definicion(funcion, cambios, al_reves=False))


def downgrade() -> None:
    op.execute(GUARDA_DOWNGRADE)
    for funcion, cambios in ((EXPORTAR, EXPORTAR_CAMBIOS), (POR_EXTRACTO, POR_EXTRACTO_CAMBIOS)):
        op.execute(_definicion(funcion, cambios, al_reves=True))
    _otra(IMPORTAR_NUEVA, IMPORTAR, IMPORTAR_CAMBIOS, al_reves=True)
    _otra(COLUMNAS, COLUMNAS, COLUMNAS_CAMBIOS, al_reves=True)
    op.execute(
        "alter table nucleo.columnas_extracto drop column moneda;"
        " alter table nucleo.movimiento_extracto drop column moneda"
    )
