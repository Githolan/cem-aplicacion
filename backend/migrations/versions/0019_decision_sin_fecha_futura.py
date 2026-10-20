"""Una decisión formal no puede tener fecha futura (punto 4 de §7 de 2b-4b).

Decisión del propietario del 19-10-2026: `decision_formal` registra un acto ya tomado. Con el día
local del consorcio (`nucleo.hoy_del_consorcio`, no la fecha UTC de la base):
- `fecha < hoy` y `fecha = hoy`: se admiten;
- `fecha > hoy`: se rechaza con `fecha_futura`.

Vale para la API y para soporte o el script: el SSOT no tiene una excepción para la fecha de una
decisión. Una decisión futura no se convierte en "programada": planificar una decisión sería otro
concepto, con su propia decisión de producto.

`registrar_decision` es la única vía de escritura (`cem_app` solo lee `decision_formal`), así que
la regla vive en ella, como segunda barrera detrás de la ruta. Va después de los controles del
origen y del actor, y del de tipo y fecha (`decision_incompleta`), como los demás motivos de E26.

El upgrade se niega (`decisiones_futuras_previas`) si ya hay una decisión con fecha posterior al
día del consorcio: la corrige el equipo, con su respaldo. El downgrade vuelve a la función de
`0016` (el mismo texto, leído de esa migración); no hay datos que proteger.

Revisión: 0019
Anterior: 0018
"""

import importlib.util
from pathlib import Path

from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None

INICIO_REGISTRAR = "CREATE OR REPLACE FUNCTION nucleo.registrar_decision("
FIN_FUNCION = "\n  $function$;\n"


def _registrar_de_0016() -> str:
    """El texto de `registrar_decision` que dejó `0016`: es la base del cambio y lo que restaura
    el downgrade, sin copiarlo a mano."""
    ruta = Path(__file__).with_name("0016_mandatos_a15_e26_r10.py")
    spec = importlib.util.spec_from_file_location(ruta.stem, ruta)
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    desde = modulo.UPGRADE.index(INICIO_REGISTRAR)
    fin = modulo.UPGRADE.index(FIN_FUNCION, desde) + len(FIN_FUNCION)
    return modulo.UPGRADE[desde:fin]


DECISION_COMPLETA = """\
    if tipo_decision is null or fecha_decision is null then
      raise exception 'la decisión necesita tipo y fecha'
        using errcode = 'P0001', hint = 'decision_incompleta';
    end if;
"""
SIN_FECHA_FUTURA = """\
    -- 0019 (2b-4b §7.4): la decisión registra un acto ya tomado. Se compara con el día local
    -- del consorcio; hoy se admite, mañana no. Vale también para soporte.
    if fecha_decision > nucleo.hoy_del_consorcio(c) then
      raise exception 'la fecha de la decisión es posterior a hoy en el consorcio'
        using errcode = 'P0001', hint = 'fecha_futura';
    end if;
"""


def _registrar_0019() -> str:
    texto = _registrar_de_0016()
    assert texto.count(DECISION_COMPLETA) == 1
    return texto.replace(DECISION_COMPLETA, DECISION_COMPLETA + SIN_FECHA_FUTURA)


GUARDA = """
do $guarda$
begin
  if exists (
       select 1 from nucleo.decision_formal d
        where d.fecha > nucleo.hoy_del_consorcio(d.consorcio_id)) then
    raise exception 'hay decisiones con fecha posterior al día del consorcio'
      using errcode = 'P0001', hint = 'decisiones_futuras_previas',
            detail = 'las corrige el equipo, con su respaldo, antes de aplicar 0019';
  end if;
end
$guarda$;
"""


def upgrade() -> None:
    op.execute(GUARDA)
    op.execute(_registrar_0019())


def downgrade() -> None:
    op.execute(_registrar_de_0016())
