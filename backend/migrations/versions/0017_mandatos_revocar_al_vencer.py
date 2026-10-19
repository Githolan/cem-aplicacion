"""E15: revocar las sesiones del mandatario cuando su mandato vence por fecha (2b-4b, PR 3).

Diseño: `doc/diseno/H2b_4b_Diseno.md` del repositorio CEM, §6; criterio 3 del diseño H2b (RN22:
"un cambio o vencimiento de mandato revoca las sesiones afectadas").

Desde 2b-3, un mandato vencido deja de conceder en el pedido siguiente, pero las sesiones del
mandatario siguen abiertas. Esta migración agrega:

- `nucleo.mandato.vencimiento_procesado_en`: la marca de que ya se revocó por el vencimiento.
  Hace al trabajo idempotente: una persona que vuelve a entrar después no pierde su sesión nueva
  en la corrida siguiente.
- `nucleo.revocar_sesiones_por_vencimiento(tope)`, `security definer` de `cem_migrate`. Recorre los
  mandatos de consorcios no eliminados con `vigente_hasta <= hoy` del consorcio (A1: exclusivo),
  sin baja, con persona, sin la marca y que estuvieron vigentes después de registrarse
  (`vigente_hasta` posterior al día de `creado_en` en la zona del consorcio): un mandato cargado
  ya vencido nunca concedió nada a ninguna sesión, y revocar por él le cortaría la sesión a una
  persona activa (revisión independiente). Por cada uno, con `for no key update skip locked`
  (no choca con los bloqueos de clave foránea; si A7 tiene el mandato con `for share`, la
  corrida lo saltea y lo toma la siguiente), revoca las
  sesiones de la persona (`revocar_sesiones_de`, como la baja), pone la marca y escribe el
  evento `consorcio.mandato.vencimiento`, con origen `planificador` y sin actor. Devuelve
  cuántos mandatos procesó, nunca más que `tope`: el trabajo tiene que terminar dentro del plazo
  del trabajador (Codex); lo que quede, lo toma la corrida siguiente. Solo `cem_app` la ejecuta:
  los trabajos corren con ese rol, sin persona ni consorcio, y la RLS de `nucleo.mandato` no les
  deja ver nada sin la función.

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

FIRMA = "nucleo.revocar_sesiones_por_vencimiento(integer)"

UPGRADE = rf"""
alter table nucleo.mandato add column vencimiento_procesado_en timestamptz;

create function nucleo.revocar_sesiones_por_vencimiento(tope integer)
  returns integer
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    m record;
    revocadas bigint;
    procesados integer := 0;
  begin
    if tope is null or tope < 1 then
      raise exception 'el tope del lote tiene que ser positivo'
        using errcode = 'P0001', hint = 'tope_invalido';
    end if;
    for m in
      select x.id, x.consorcio_id, x.persona_id, x.vigente_hasta
        from nucleo.mandato x
        join nucleo.consorcio co on co.id = x.consorcio_id and co.eliminado_en is null
       where x.causa_baja is null
         and x.persona_id is not null
         and x.vencimiento_procesado_en is null
         and x.vigente_hasta is not null
         and x.vigente_hasta <= nucleo.hoy_del_consorcio(x.consorcio_id)
         and x.vigente_hasta > (x.creado_en at time zone co.zona_horaria)::date
       order by x.id
       limit tope
         for no key update of x skip locked
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
