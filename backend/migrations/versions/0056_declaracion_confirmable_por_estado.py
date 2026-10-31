"""`confirmable` de «no se pagó» mira también el estado de la orden (revisión de #182).

`declaraciones_pago_no_realizado_lectura` (`0055`) calcula `confirmable` con las reglas del orden de
los hechos de `confirmar_pago_no_realizado`, pero no con las de `ejecucion_manual_abierta`: la
orden esperando su pago (en ejecución o en verificación), la ejecución de la versión vigente y sin
un resultado definitivo. La pantalla del Consejo ya filtraba por estado; otro cliente no. Ahora la
lectura dice «se puede confirmar hoy» con las mismas condiciones que la confirmación, salvo las de
quien mira (facultad, separación), que dependen de cada persona.
"""

from alembic import op
from sqlalchemy import text

revision = "0056"
down_revision = "0055"
branch_labels = None
depends_on = None

LECTURA = "nucleo.declaraciones_pago_no_realizado_lectura(uuid, text, uuid, text, uuid)"

ANTES = "             f.id is null\n"
DESPUES = (
    "             f.id is null\n"
    "             and exists (select 1 from nucleo.orden_pago o\n"
    "                          where o.id = e.orden_id and o.version_vigente = e.version\n"
    "                            and o.estado in ('en_ejecucion', 'en_verificacion'))\n"
    "             and not exists (select 1 from nucleo.resultado_ejecucion_orden_pago r\n"
    "                              where r.ejecucion_id = e.id and r.resultado <> 'incierta')\n"
)


def _reemplazar(antes: str, despues: str) -> None:
    definicion = (
        op.get_bind()
        .execute(text("select pg_get_functiondef(cast(:f as regprocedure))"), {"f": LECTURA})
        .scalar_one()
    )
    if definicion.count(antes) != 1:
        raise RuntimeError(f"0056: {LECTURA} no tiene exactamente una vez {antes!r}")
    op.execute(definicion.replace(antes, despues))


def upgrade() -> None:
    _reemplazar(ANTES, DESPUES)


def downgrade() -> None:
    _reemplazar(DESPUES, ANTES)
