"""Una versión de un comprobante atado antes de `0063` la sube solo quien vio la cuenta (P3 158).

`0063` limita las versiones nuevas de un comprobante a quien vio la cuenta de su orden, pero la
orden la toma solo de `nucleo.documento.orden_comprobante`. Un comprobante atado a un pago antes de
`0063` no la tiene: cualquiera con la facultad de carga que supiera su `documento_id` le subía una
versión. Sigue sin ver las de otros ni la cuenta, pero esa versión podía pasar a ser la evidencia
de una corrección. Se reclasificó por tocar la autorización.

La orden de un comprobante es ahora también la de la ejecución a la que está atado, la misma que
mira `nucleo.preparo_la_orden_del_comprobante` (`0063`). Si alguna de sus órdenes no es una en la
que el actor vio la cuenta, para él el documento no existe (`no_encontrado`), como en `0063`.

Downgrade: vuelve a la regla de `0063`. No hay datos que dependan de esta.

Revisión: 0064
Anterior: 0063
"""

from alembic import op
from sqlalchemy import text

revision = "0064"
down_revision = "0063"
branch_labels = None
depends_on = None

VERSION = "nucleo.nueva_version(uuid, text, integer, text, uuid, text)"
SOLO_CON_ORDEN = """\
    if exists (select 1 from nucleo.documento d
                where d.consorcio_id = c and d.id = documento and d.orden_comprobante is not null
                  and not nucleo.abrio_la_cuenta_de_la_orden(c, d.orden_comprobante, actor)) then
"""
TAMBIEN_ATADO = """\
    -- También la orden de la ejecución a la que está atado: un comprobante atado antes de 0063 no
    -- tiene orden de carga (0064, P3 158).
    if exists (select 1 from (
                 select d.orden_comprobante as respalda from nucleo.documento d
                  where d.consorcio_id = c and d.id = documento and d.orden_comprobante is not null
                 union
                 select e.orden_id from nucleo.comprobante_pago_manual p
                   join nucleo.ejecucion_orden_pago e
                     on e.consorcio_id = p.consorcio_id and e.id = p.ejecucion_id
                  where p.consorcio_id = c and p.documento_id = documento) x
                where not nucleo.abrio_la_cuenta_de_la_orden(c, x.respalda, actor)) then
"""


def _reemplazar(antes: str, despues: str) -> None:
    definicion = (
        op.get_bind()
        .execute(text("select pg_get_functiondef(cast(:f as regprocedure))"), {"f": VERSION})
        .scalar_one()
    )
    if definicion.count(antes) != 1:
        raise RuntimeError(f"0064: {VERSION} no tiene exactamente una vez {antes!r}")
    op.execute(definicion.replace(antes, despues))


def upgrade() -> None:
    _reemplazar(SOLO_CON_ORDEN, TAMBIEN_ATADO)


def downgrade() -> None:
    _reemplazar(TAMBIEN_ATADO, SOLO_CON_ORDEN)
