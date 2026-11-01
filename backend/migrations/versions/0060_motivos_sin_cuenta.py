"""Los motivos escritos a mano no llevan una cuenta (revisión transversal de H3, 01-11).

Quien prepara una orden nunca recibe la cuenta completa, pero lee los motivos que escriben otros:
el de la observación de un pago manual, el de «no se pagó» (que además pasa al resultado de la
ejecución) y el del rechazo de la orden. Un consejero que escribiera «transferiste a 0170…» se la
daba entera. El número de operación ya se niega si es una cuenta (`0055`); ahora también estos
motivos, en la base, para cualquier camino que los escriba.

Se niega un texto con 22 dígitos seguidos, contando como seguidos los separados por espacios,
puntos, guiones o barras: con o sin dígitos verificadores válidos, 22 dígitos de una cuenta ya son
la cuenta. Los motivos de los resultados del riel no se tocan: los escribe el proveedor, no una
persona, y hoy no hay riel.
"""

from alembic import op

revision = "0060"
down_revision = "0059"
branch_labels = None
depends_on = None

TABLAS = (
    ("conciliacion_pago_manual", "insert", "motivo"),
    ("declaracion_pago_no_realizado", "insert", "motivo"),
    ("orden_pago", "insert or update of motivo_rechazo", "motivo_rechazo"),
)

UPGRADE = r"""
-- 22 dígitos seguidos, aunque estén separados por espacios, puntos, guiones o barras.
create function nucleo.texto_con_cuenta(t text) returns boolean
  language sql immutable strict parallel safe
  set search_path = pg_catalog, pg_temp
  as $$ select regexp_replace(t, '([0-9])[ ./-]+(?=[0-9])', '\1', 'g') ~ '[0-9]{22}' $$;
revoke all on function nucleo.texto_con_cuenta(text) from public;

-- tg_argv[0]: la columna del motivo.
create function nucleo.negar_cuenta_en_motivo() returns trigger
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  begin
    if nucleo.texto_con_cuenta(to_jsonb(new) ->> tg_argv[0]) then
      raise exception 'el motivo no puede llevar una cuenta' using errcode = 'P0001',
        hint = 'motivo_con_cuenta';
    end if;
    return new;
  end
  $$;
revoke all on function nucleo.negar_cuenta_en_motivo() from public;
"""


def upgrade() -> None:
    op.execute(UPGRADE)
    for tabla, cuando, columna in TABLAS:
        op.execute(
            f"create trigger motivo_sin_cuenta before {cuando} on nucleo.{tabla}"
            f" for each row execute function nucleo.negar_cuenta_en_motivo('{columna}')"
        )


def downgrade() -> None:
    for tabla, _, _ in TABLAS:
        op.execute(f"drop trigger motivo_sin_cuenta on nucleo.{tabla}")
    op.execute("drop function nucleo.negar_cuenta_en_motivo()")
    op.execute("drop function nucleo.texto_con_cuenta(text)")
