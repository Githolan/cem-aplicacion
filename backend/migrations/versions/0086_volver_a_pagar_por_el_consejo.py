"""Volver a pagar: por franja si fue simple, por el Consejo si hubo diferencia (decisión 11 del
propietario, 07-11-2026).

**Desvío de §9 de `Rediseno_Circuito_Pagos_2026-11-06.md` (repositorio CEM), decidido por el
propietario.** §9 pide que la versión nueva pase otra vez por aprobación, sin distinguir. Desde
`0075` esa aprobación era la de su franja: una orden bajo el umbral se aprobaba sola aunque la
ejecución anterior hubiera sacado plata de más, a otra cuenta o con un reclamo. Ahora:

- una `fallida` simple (no se pagó, sin diferencia) sigue por su franja;
- una orden con una diferencia registrada en cualquier ejecución anterior queda `en_revision` y la
  decide el Consejo, aunque su franja no pida aprobación. Da igual cómo terminó esa diferencia:
  recuperada, cerrada sin recuperar o corregida (`0082`).

`aprobar_por_franja` la usan enviar, modificar y renovar: mirar acá cubre los tres caminos.

Downgrade: vuelve a la definición anterior. Las órdenes que quedaron en revisión siguen ahí.

Revisión: 0086
Anterior: 0085
"""

from alembic import op
from sqlalchemy import text

revision = "0086"
down_revision = "0085"
branch_labels = None
depends_on = None

FRANJA = "nucleo.aprobar_por_franja(uuid, uuid, integer, uuid)"
TUVO = "nucleo.orden_tuvo_diferencia(uuid, uuid)"

INTERNA = f"""
create function {TUVO.split("(")[0]}(c uuid, orden uuid)
  returns boolean
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select exists (select 1 from nucleo.diferencia_pago_manual f
                    where f.consorcio_id = c and f.orden_id = orden)
  $$;
revoke all on function {TUVO} from public;
"""

CAMBIOS = (
    (
        "    -- Con la alerta de fraccionamiento la decide el Consejo (decisión §12, `0077`).\n"
        "    if not nucleo.franja_requiere_aprobacion(f) and not alerta then\n",
        "    -- Con la alerta de fraccionamiento la decide el Consejo (decisión §12, `0077`), y\n"
        "    -- también si una ejecución anterior tuvo una diferencia (decisión 11, `0086`).\n"
        "    if not nucleo.franja_requiere_aprobacion(f) and not alerta\n"
        "       and not nucleo.orden_tuvo_diferencia(c, orden) then\n",
    ),
)


def _definicion(*, al_reves: bool) -> str:
    definicion = (
        op.get_bind()
        .execute(text("select pg_get_functiondef(cast(:f as regprocedure))"), {"f": FRANJA})
        .scalar_one()
    )
    for antes, despues in CAMBIOS:
        if al_reves:
            antes, despues = despues, antes
        if definicion.count(antes) != 1:
            raise RuntimeError(f"0086: {FRANJA} no tiene exactamente una vez {antes!r}")
        definicion = definicion.replace(antes, despues)
    return definicion


def upgrade() -> None:
    op.execute(INTERNA)
    op.execute(_definicion(al_reves=False))


def downgrade() -> None:
    op.execute(_definicion(al_reves=True))
    op.execute(f"drop function {TUVO}")
