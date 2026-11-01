"""Los motivos escritos a mano no llevan una cuenta (revisión transversal de H3, 01-11).

Quien prepara una orden nunca recibe la cuenta completa, pero lee los motivos que escriben otros:
el de la observación de un pago manual, el de «no se pagó» (que además pasa al resultado de la
ejecución) y el del rechazo de la orden. Un consejero que escribiera «transferiste a 0170…» se la
daba entera. El número de operación ya se niega si es una cuenta (`0055`); ahora también estos
motivos, en la base, para cualquier camino que los escriba.

Se niega un texto con exactamente 22 dígitos, sin otro dígito pegado antes ni después y con a lo
sumo un separador entre dos de ellos: espacio (también el duro y los finos que deja copiar de un
home banking o un PDF), punto, guion o barra. Es la definición del riel (`CUENTA_EN_TEXTO` en
`app/consorcios/pagos.py`) con más separadores; una lista de facturas o de fechas no la cumple. Con
o sin verificadores válidos: 22 dígitos de una cuenta ya son la cuenta.

Cubre el descuido de copiar la cuenta, no a quien quiera pasarla a propósito: quien aprueba ya la
recibe completa (decisión del propietario del 30-10) y podría darla por cualquier otro medio.

Los motivos de los resultados del riel no se tocan: los escribe el proveedor, no una persona, y el
riel ya los guarda tapados. Si un motivo guardado antes ya tiene una cuenta, la migración se niega:
habría que verlo antes de seguir.
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

# Espacio, punto, barra, espacio duro (U+00A0), espacio fino (U+202F y U+2009) y guion, último
# para que sea literal en el corchete.
SEPARADORES = " ./\u00a0\u202f\u2009-"

UPGRADE = f"""
-- Exactamente 22 dígitos, con a lo sumo un separador entre dos de ellos.
create function nucleo.texto_con_cuenta(t text) returns boolean
  language sql immutable strict parallel safe
  set search_path = pg_catalog, pg_temp
  as $$ select t ~ '(?<![0-9])[0-9](?:[{SEPARADORES}]?[0-9]){{21}}(?![0-9])' $$;
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

COMPROBAR = """
do $$
begin
  if exists (select 1 from nucleo.conciliacion_pago_manual where nucleo.texto_con_cuenta(motivo))
     or exists (select 1 from nucleo.declaracion_pago_no_realizado
                 where nucleo.texto_con_cuenta(motivo))
     or exists (select 1 from nucleo.orden_pago where nucleo.texto_con_cuenta(motivo_rechazo)) then
    raise exception 'hay motivos guardados con una cuenta: revisarlos antes de migrar';
  end if;
end
$$;
"""
"""Después de los disparadores: `create trigger` espera a las escrituras en curso y frena las
nuevas, así que lo que se comprueba ya no cambia antes de que la migración confirme."""


def upgrade() -> None:
    op.execute(UPGRADE)
    for tabla, cuando, columna in TABLAS:
        op.execute(
            f"create trigger motivo_sin_cuenta before {cuando} on nucleo.{tabla}"
            f" for each row execute function nucleo.negar_cuenta_en_motivo('{columna}')"
        )
    op.execute(COMPROBAR)


def downgrade() -> None:
    for tabla, _, _ in TABLAS:
        op.execute(f"drop trigger motivo_sin_cuenta on nucleo.{tabla}")
    op.execute("drop function nucleo.negar_cuenta_en_motivo()")
    op.execute("drop function nucleo.texto_con_cuenta(text)")
