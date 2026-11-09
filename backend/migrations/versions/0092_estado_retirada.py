"""El estado `retirada` de una orden de pago (paso 1 de 2; el resto, en `0093`).

Decisión del propietario del 09-11-2026, comentario 23 de la revisión de pantallas (repositorio
CEM, `doc/revision-pantallas/pendientes.md`): «Retirar la orden», solo mientras nadie la aprobó;
queda registrado quién y cuándo, y sale de la bandeja. En CEM nada se borra: la orden queda, en su
propio estado.

Va sola porque PostgreSQL no deja usar un valor nuevo de un enum en la misma transacción que lo
agrega, y cada migración corre en su transacción (`transaction_per_migration`).

Downgrade: no hace nada. PostgreSQL no quita un valor de un enum sin rehacer el tipo, y rehacerlo
tocaría la tabla de órdenes y todas las funciones que la reciben. El valor sin usar no cambia nada:
`0093` baja antes, se niega si alguna orden está retirada y saca sus transiciones, así que ninguna
fila puede tenerlo. Al volver a subir, `if not exists` lo encuentra.

Revisión: 0092
Anterior: 0091
"""

from alembic import op

revision = "0092"
down_revision = "0091"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("alter type nucleo.estado_orden_pago add value if not exists 'retirada'")


def downgrade() -> None:
    """Sin cambios a propósito: ver la nota del módulo."""
