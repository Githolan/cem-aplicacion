"""Quien aprueba la orden no concilia su pago; quien la preparó sí puede (decisión del propietario
del 15-11-2026: «Que quien aprueba no concilie, quien prepara sí puede conciliar»).

Hasta acá la conciliación separaba de quien preparó la orden (`nucleo.exigir_no_preparo`, `0055`)
y dejaba conciliar a quien la aprobó: lo contrario de lo que quiere el propietario.

**La barrera nueva** (`nucleo.exigir_no_aprobo_la_orden`): quien concilia, confirma que no se pagó,
resuelve una diferencia o coteja con el banco no aprobó **ninguna** versión de la orden, ni con
esta cuenta ni con otra de la misma persona (por la identidad guardada en cada aprobación y por
la de hoy). Niega con `no_es_quien_aprobo`. Reemplaza a `exigir_no_preparo` en las diez funciones
que la llamaban (conciliar, confirmar, resolver la diferencia, cotejar, anular un cotejo y las
lecturas de qué puede hacer quien lee); la lista se comprueba: si la base tiene otra cosa, la
migración falla.

Lo demás no cambia: quien vio la cuenta, registró o tomó el pago sigue sin conciliar
(`exigir_no_ejecuto`); quien preparó sigue sin aprobar y quien aprobó sigue sin pagar.

**La lectura** `nucleo.lee_quien_aprobo_la_orden(orden, actor)`, como `lee_quien_preparo_la_orden`
(`0054`): para que la bandeja no ofrezca conciliar a quien aprobó. Falla cerrada: sin identidad
vigente de quien lee, verdadero.

Downgrade: vuelve `exigir_no_preparo` (la de `0055`) a las mismas funciones y borra las dos
nuevas. No toca datos.

Revisión: 0102
Anterior: 0101
"""

from alembic import op
from sqlalchemy import text

revision = "0102"
down_revision = "0101"
branch_labels = None
depends_on = None

VIEJA = "nucleo.exigir_no_preparo("
NUEVA = "nucleo.exigir_no_aprobo_la_orden("
FIRMA_VIEJA = "nucleo.exigir_no_preparo(uuid, uuid, uuid, uuid)"
FIRMA_NUEVA = "nucleo.exigir_no_aprobo_la_orden(uuid, uuid, uuid, uuid)"
FIRMA_LECTURA = "nucleo.lee_quien_aprobo_la_orden(uuid, uuid)"

QUIENES_LLAMAN = frozenset({
    "admite_conciliar",
    "admite_confirmar",
    "admite_cotejar",
    "admite_resolver_diferencia",
    "anular_cotejo",
    "conciliar_pago_manual",
    "confirmar_pago_no_realizado",
    "cotejar_pagos",
    "cotejar_por_extracto",
    "diferencia_para_resolver",
})  # fmt: skip
"""Las funciones de `nucleo` que llamaban a `exigir_no_preparo` en `0101` (leídas de la base)."""

FUNCIONES = """
-- Quien concilia, confirma, resuelve o coteja no aprobó ninguna versión de la orden (0102).
create function nucleo.exigir_no_aprobo_la_orden(c uuid, orden uuid, actor uuid, identidad uuid)
  returns void
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    p uuid;
  begin
    if exists (select 1 from nucleo.orden_pago_aprobacion x
                where x.orden_id = orden and x.identidad_id = identidad) then
      raise exception 'la misma persona no puede ocupar los dos lugares de esta separación'
        using errcode = 'P0001', hint = 'no_es_quien_aprobo';
    end if;
    for p in select distinct x.persona_id from nucleo.orden_pago_aprobacion x
              where x.orden_id = orden loop
      perform nucleo.exigir_separacion_humana(c, actor, p, 'no_es_quien_aprobo');
    end loop;
  end
  $$;

create function nucleo.lee_quien_aprobo_la_orden(orden uuid, actor uuid)
  returns boolean
  language plpgsql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    yo uuid;
  begin
    if c is null or actor is null or actor is distinct from plataforma.persona_actual() then
      raise exception 'sin ámbito para leer la revisión' using errcode = '42501';
    end if;
    yo := nucleo.identidad_humana_de(c, actor);
    return yo is null
        or not exists (select 1 from nucleo.orden_pago o where o.consorcio_id = c and o.id = orden)
        or exists (select 1 from nucleo.orden_pago_aprobacion x
                    where x.consorcio_id = c and x.orden_id = orden
                      and (x.identidad_id = yo
                           or nucleo.misma_persona_humana(c, actor, x.persona_id)));
  end
  $$;
"""

NO_PREPARO = """
-- Quien concilia o confirma no preparó ninguna versión de la orden.
create function nucleo.exigir_no_preparo(c uuid, orden uuid, actor uuid, identidad uuid)
  returns void
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    p uuid;
  begin
    if exists (select 1 from nucleo.orden_pago_version x
                where x.orden_id = orden and x.identidad_preparo = identidad) then
      raise exception 'la misma persona no puede ocupar los dos lugares de esta separación'
        using errcode = 'P0001', hint = 'no_es_quien_preparo';
    end if;
    for p in select distinct x.preparada_por from nucleo.orden_pago_version x
              where x.orden_id = orden loop
      perform nucleo.exigir_separacion_humana(c, actor, p, 'no_es_quien_preparo');
    end loop;
  end
  $$;
"""
"""La de `0055`, tal cual, para el downgrade."""


def _llaman(llamada: str) -> dict[str, str]:
    """Las funciones de `nucleo` cuyo cuerpo contiene `llamada`, por firma; falla si no son
    exactamente `QUIENES_LLAMAN`."""
    filas = (
        op.get_bind()
        .execute(
            text(
                "select p.proname, cast(cast(p.oid as regprocedure) as text) from pg_proc p"
                " join pg_namespace n on n.oid = p.pronamespace"
                " where n.nspname = 'nucleo' and strpos(p.prosrc, :llamada) > 0"
            ),
            {"llamada": llamada},
        )
        .all()
    )
    nombres = frozenset(nombre for nombre, _ in filas)
    if nombres != QUIENES_LLAMAN or len(filas) != len(QUIENES_LLAMAN):
        raise RuntimeError(
            f"0102: llaman a {llamada!r} {sorted(nombres)}, se esperaba {sorted(QUIENES_LLAMAN)}"
        )
    return {nombre: firma for nombre, firma in filas}


def _cambiar(antes: str, despues: str) -> None:
    for firma in _llaman(antes).values():
        definicion = (
            op.get_bind()
            .execute(text("select pg_get_functiondef(cast(:f as regprocedure))"), {"f": firma})
            .scalar_one()
        )
        if definicion.count(antes) != 1:
            raise RuntimeError(f"0102: {firma} no tiene exactamente una vez {antes!r}")
        op.execute(definicion.replace(antes, despues))


def upgrade() -> None:
    op.execute(FUNCIONES)
    op.execute(f"revoke all on function {FIRMA_NUEVA} from public")
    op.execute(f"revoke all on function {FIRMA_LECTURA} from public")
    op.execute(f"grant execute on function {FIRMA_LECTURA} to cem_app")
    _cambiar(VIEJA, NUEVA)
    op.execute(f"drop function {FIRMA_VIEJA}")


def downgrade() -> None:
    op.execute(NO_PREPARO)
    op.execute(f"revoke all on function {FIRMA_VIEJA} from public")
    _cambiar(NUEVA, VIEJA)
    op.execute(f"drop function {FIRMA_LECTURA}")
    op.execute(f"drop function {FIRMA_NUEVA}")
