"""La versión 1 del perfil normativo de CABA rige desde el 1-8-2024 (decisión del propietario
P0-13, 23-11-2026; registro `doc/diseno/DECISIONES_POR_DEFECTO.md` del repositorio CEM).

`0125` la cargó desde el 31-7-2024, con la fecha **SIN VERIFICAR** («prórroga al 31-7-2024» del
documento 01, §12). El propietario decidió el 1-8-2024. No es otra versión de las reglas (lo que
exige es lo mismo): es la fecha de la misma versión mal cargada, y se corrige en su fila.

El perfil es historia que no se cambia (`negar_cambio_perfil_normativo`, `0125`): la migración
apaga el disparador de `nucleo.perfil_normativo` solo para esta fila y lo vuelve a prender en la
misma transacción. Las liquidaciones son mensuales (`periodo`, el primer día del mes): agosto de
2024 se mide igual con las dos fechas y julio con ninguna, así que nada cambia en lo que se ve.

Downgrade: vuelve a la fecha de `0125`.

Datos de prueba: SINTETICOS / NO REAL.

Revisión: 0133
Anterior: 0132
"""

from alembic import op

revision = "0133"
down_revision = "0132"
branch_labels = None
depends_on = None

JURISDICCION = "caba"
VERSION = 1
VIGENTE_DESDE = "2024-08-01"
"""La que decidió el propietario (P0-13)."""
VIGENTE_DESDE_0125 = "2024-07-31"
"""La que cargó `0125`; una prueba la compara con su `CABA_1`."""


def _fijar(desde: str, antes: str) -> None:
    op.execute(f"""
alter table nucleo.perfil_normativo disable trigger historia_perfil_normativo;
do $$
declare
  n integer;
begin
  update nucleo.perfil_normativo set vigente_desde = '{desde}'
   where jurisdiccion = '{JURISDICCION}' and version = {VERSION} and vigente_desde = '{antes}';
  get diagnostics n = row_count;
  if n <> 1 then
    raise exception 'se esperaba una fila de % versión % desde %, hay %',
      '{JURISDICCION}', {VERSION}, '{antes}', n;
  end if;
end
$$;
alter table nucleo.perfil_normativo enable trigger historia_perfil_normativo;
""")


def upgrade() -> None:
    _fijar(VIGENTE_DESDE, VIGENTE_DESDE_0125)


def downgrade() -> None:
    _fijar(VIGENTE_DESDE_0125, VIGENTE_DESDE)
