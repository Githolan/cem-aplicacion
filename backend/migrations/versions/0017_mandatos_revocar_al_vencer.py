"""E15: revocar las sesiones del mandatario cuando su mandato vence por fecha (2b-4b, PR 3).

Diseño: `doc/diseno/H2b_4b_Diseno.md` del repositorio CEM, §6; criterio 3 del diseño H2b (RN22:
"un cambio o vencimiento de mandato revoca las sesiones afectadas").

Desde 2b-3, un mandato vencido deja de conceder en el pedido siguiente, pero las sesiones del
mandatario siguen abiertas. Esta migración agrega:

- `nucleo.mandato.vencimiento_procesado_en`: la marca de que ya se revocó por el vencimiento.
  Hace al trabajo idempotente: una persona que vuelve a entrar después no pierde su sesión nueva
  en la corrida siguiente.
- `nucleo.revocar_sesiones_por_vencimiento()`, `security definer` de `cem_migrate`. Recorre los
  mandatos de consorcios no eliminados con `vigente_hasta <= hoy` del consorcio (A1: exclusivo),
  sin baja, con persona y sin la marca. Por cada uno, con `for update skip locked`, revoca las
  sesiones de la persona (`revocar_sesiones_de`, como la baja), pone la marca y escribe el
  evento `consorcio.mandato.vencimiento`, con origen `planificador` y sin actor. Devuelve
  cuántos mandatos procesó. Solo `cem_app` la ejecuta: los trabajos corren con ese rol, sin
  persona ni consorcio, y la RLS de `nucleo.mandato` no les deja ver nada sin la función.

Downgrade: se niega si algún mandato tiene la marca (`downgrade_con_datos`): es la constancia de
que se revocaron sesiones, como las guardas de 0012, 0014 y 0015. Si no, quita la función y la
columna.

Revisión: 0017
Anterior: 0016
"""

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None

FIRMA = "nucleo.revocar_sesiones_por_vencimiento()"

UPGRADE = rf"""
alter table nucleo.mandato add column vencimiento_procesado_en timestamptz;

create function {FIRMA}
  returns integer
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    m record;
    revocadas bigint;
    procesados integer := 0;
  begin
    for m in
      select x.id, x.consorcio_id, x.persona_id, x.vigente_hasta
        from nucleo.mandato x
        join nucleo.consorcio co on co.id = x.consorcio_id and co.eliminado_en is null
       where x.causa_baja is null
         and x.persona_id is not null
         and x.vencimiento_procesado_en is null
         and x.vigente_hasta is not null
         and x.vigente_hasta <= nucleo.hoy_del_consorcio(x.consorcio_id)
       order by x.id
         for update of x skip locked
    loop
      revocadas := nucleo.revocar_sesiones_de(m.persona_id);
      update nucleo.mandato set vencimiento_procesado_en = now() where id = m.id;
      insert into auditoria.eventos
        (consorcio_id, actor_persona_id, origen, accion, entidad, entidad_id, despues,
         hash_previo, hash)
      values (m.consorcio_id, null, 'planificador', 'consorcio.mandato.vencimiento', 'mandato',
              m.id, jsonb_build_object('persona_id', m.persona_id,
                                       'vigente_hasta', m.vigente_hasta,
                                       'sesiones_revocadas', revocadas),
              '', '');
      procesados := procesados + 1;
    end loop;
    return procesados;
  end
  $$;

revoke all on function {FIRMA} from public;
grant execute on function {FIRMA} to cem_app;
"""

DOWNGRADE = rf"""
do $$
begin
  if exists (select 1 from nucleo.mandato where vencimiento_procesado_en is not null) then
    raise exception 'el downgrade de 0017 se niega: hay vencimientos procesados'
      using errcode = 'P0001', hint = 'downgrade_con_datos',
            detail = 'la marca es la constancia de que se revocaron sesiones por el vencimiento';
  end if;
end
$$;
drop function {FIRMA};
alter table nucleo.mandato drop column vencimiento_procesado_en;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
