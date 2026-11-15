"""Las monedas del edificio (`0100`, P-14) también en los contratos, los umbrales y la exportación
(cierre de P-14; sí del propietario del 14-11-2026).

**La barrera** (`nucleo.exigir_moneda_habilitada`, la de `0100`) suma dos triggers `before insert`:
sobre `nucleo.contrato_proveedor` (registrar un contrato o abono, por la API) y sobre
`nucleo.umbral_orden_pago` (el umbral que carga el equipo con `scripts/umbral_orden_pago.py`). Un
contrato o un umbral en una moneda en que el edificio no opera se niega con
`moneda_no_habilitada`, como una orden; ningún camino lo saltea.

**Lo que ya existe no se rompe.** El relleno de `0100` ya habilitó la moneda de cada contrato y
umbral que existía. Por lo que se haya cargado entre `0100` y esta, se vuelve a correr para
contratos y umbrales, sin tocar lo que ya está (`origen = 'migracion'`).

**La exportación** (`nucleo.exportar_ordenes_pago`, `0069`, en su lugar): suma la entidad
`moneda_habilitada`, una fila por moneda que el edificio habilitó, con su origen (`soporte` o
`migracion`) y cuándo. El peso no tiene fila: rige siempre (`0100`). El paquete la escribe en
`ordenes_pago/moneda_habilitada.json` (`scripts/exportar_consorcio.py`).

Downgrade: quita los dos triggers y la entidad de la exportación. Las filas que agregó el relleno
quedan: son de la migración y `0100` las recalcula o las borra al bajar.

Revisión: 0101
Anterior: 0100
"""

from alembic import op
from sqlalchemy import text

revision = "0101"
down_revision = "0100"
branch_labels = None
depends_on = None

MONEDA_BASE = "ARS"
"""La de `0100`: rige siempre y no tiene fila."""
TABLAS = ("nucleo.contrato_proveedor", "nucleo.umbral_orden_pago")
EXPORTAR_ORDENES = "nucleo.exportar_ordenes_pago(uuid, uuid)"

TRIGGERS = "".join(
    f"create trigger moneda_habilitada before insert on {t}\n"
    "  for each row execute function nucleo.exigir_moneda_habilitada();\n"
    for t in TABLAS
)

RELLENO = f"""
-- Nadie registra contratos ni umbrales entre el relleno y la barrera.
lock table nucleo.contrato_proveedor, nucleo.umbral_orden_pago in share row exclusive mode;
insert into nucleo.moneda_consorcio (consorcio_id, moneda, origen)
select distinct consorcio_id, moneda, 'migracion'
  from (select consorcio_id, moneda from nucleo.umbral_orden_pago
        union select consorcio_id, moneda from nucleo.contrato_proveedor) usadas
 where moneda <> '{MONEDA_BASE}'
on conflict (consorcio_id, moneda) do nothing;
"""

EXPORTAR_CAMBIOS = (
    (
        "       order by x.vigente_desde, x.id;\n",
        "       order by x.vigente_desde, x.id;\n"
        "    -- 0101: las monedas que habilitó el edificio (el peso rige siempre, sin fila).\n"
        "    return query\n"
        "      select 'moneda_habilitada'::text,\n"
        "             jsonb_build_object('moneda', m.moneda, 'origen', m.origen,\n"
        "                                'habilitada_en', m.habilitada_en)\n"
        "        from nucleo.moneda_consorcio m\n"
        "       where m.consorcio_id = c\n"
        "       order by m.moneda;\n",
    ),
)


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
            raise RuntimeError(f"0101: {funcion} no tiene exactamente una vez {antes!r}")
        definicion = definicion.replace(antes, despues)
    return definicion


def upgrade() -> None:
    op.execute(RELLENO)
    op.execute(TRIGGERS)
    op.execute(_definicion(EXPORTAR_ORDENES, EXPORTAR_CAMBIOS, al_reves=False))


def downgrade() -> None:
    op.execute(_definicion(EXPORTAR_ORDENES, EXPORTAR_CAMBIOS, al_reves=True))
    op.execute("".join(f"drop trigger moneda_habilitada on {t};\n" for t in TABLAS))
