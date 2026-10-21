"""Permiso `MAINTAIN` a `cem_app` sobre las tablas de `nucleo` con índices GiST (E58).

El control diario `plataforma.verificar_indices` (`app/plataforma/indices.py`) compara, para cada
índice GiST de `nucleo`, lo que ve una lectura secuencial con lo que devuelve el índice. Si
difieren, reconstruye el índice con `REINDEX` en una conexión de `cem_app`. PostgreSQL exige
`MAINTAIN` sobre la tabla para eso (17+); sin este permiso, el control detecta pero no repara.

Los GiST de hoy (cuatro, en `vinculo`, `mandato` y `modo_operacion`) son índices de
restricciones de exclusión, que PostgreSQL no reconstruye con `CONCURRENTLY`: el `REINDEX` es el
común y frena las escrituras de esa tabla mientras dura (milisegundos con estos tamaños; el
control pone un tope de espera de bloqueo).

`MAINTAIN` también permite `VACUUM`, `ANALYZE`, `CLUSTER` y `LOCK TABLE` sobre esas tablas. No
abre datos nuevos: `cem_app` ya lee y escribe en ellas bajo la RLS.

Una tabla con un GiST nuevo en una migración futura no recibe el permiso sola: el control falla
con `permission denied` al querer repararla, y el trabajo queda muerto (lo ve el monitor de la
cola). Esa migración tiene que agregar su `grant maintain`.

Revisión: 0022
Anterior: 0021
"""

from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None

TABLAS_CON_GIST = """
  select distinct format('%I.%I', n.nspname, t.relname)
    from pg_index x
    join pg_class i on i.oid = x.indexrelid
    join pg_am a on a.oid = i.relam
    join pg_class t on t.oid = x.indrelid
    join pg_namespace n on n.oid = t.relnamespace
   where n.nspname = 'nucleo' and a.amname = 'gist'
"""

UPGRADE = f"""
do $$
declare t text;
begin
  for t in {TABLAS_CON_GIST} loop
    execute format('grant maintain on table %s to cem_app', t);
  end loop;
end
$$;
"""

DOWNGRADE = f"""
do $$
declare t text;
begin
  for t in {TABLAS_CON_GIST} loop
    execute format('revoke maintain on table %s from cem_app', t);
  end loop;
end
$$;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
