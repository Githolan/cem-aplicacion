"""Identidad: invitaciones de un solo uso y alta de personas (RF03).

Documento 08: RF03 ("invitación de un solo uso y aceptación de identidad y ámbito") y §13.1
(la invitación lleva edificio, unidad, vínculo y datos mínimos). Documento 13: §10.3 (tokens de
un solo uso guardados como hash) y §8.5 (RLS por consorcio).

- `nucleo.invitacion` tiene `consorcio_id`: RLS forzada por consorcio. Quien invita la crea con
  su persona en `invitada_por` (una política restrictiva de `insert` lo exige) o, sin persona,
  el equipo de la plataforma con `scripts/invitar.py`. `cem_app` lee, inserta y solo puede
  cambiar `revocada_en`, también en la pendiente que creó otra propietaria de la unidad.
- Del token queda solo el SHA-256; 7 días como máximo, impuesto por la base.
- Abrir y aceptar una invitación ocurre antes de saber en qué consorcio está ni, a veces,
  quién es la persona: lo hacen dos funciones `security definer` que reciben el hash del token
  y devuelven lo justo.
- Aceptar es atómico: crea la persona si el correo no tenía cuenta (y su sesión), crea el
  vínculo con vigencia desde hoy en la zona horaria del consorcio, marca la invitación y
  escribe el evento de auditoría. Si el correo ya tenía cuenta, solo la acepta una sesión de
  esa misma persona: una invitación nunca abre la cuenta de otro.
- Las invitaciones no se purgan: son la constancia de quién dio acceso a quién.

Revisión: 0007
Anterior: 0006
"""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

UPGRADE = r"""
create table nucleo.invitacion (
  id            uuid primary key default plataforma.uuid_v7(),
  consorcio_id  uuid not null references nucleo.consorcio (id),
  unidad_id     uuid not null,
  tipo          nucleo.tipo_vinculo not null,
  correo        text not null check (correo = lower(btrim(correo))
                                     and position('@' in correo) > 1 and length(correo) <= 254),
  nombre        text not null check (length(btrim(nombre)) between 1 and 100),
  -- SHA-256 del token que viaja en el enlace del correo.
  token_hash    bytea not null unique check (length(token_hash) = 32),
  -- Quién invitó; nulo solo si la cargó el equipo de la plataforma (scripts/invitar.py).
  invitada_por  uuid references nucleo.persona (id),
  creada_en     timestamptz not null default now(),
  vence_en      timestamptz not null,
  aceptada_en   timestamptz,
  -- La persona que la aceptó (nueva o ya existente).
  persona_id    uuid references nucleo.persona (id),
  revocada_en   timestamptz,
  foreign key (consorcio_id, unidad_id) references nucleo.unidad (consorcio_id, id),
  check (vence_en > creada_en),
  -- 7 días como máximo (VIGENCIA_INVITACION): la base lo impone aunque la API se equivoque.
  check (vence_en - creada_en <= interval '7 days'),
  check ((aceptada_en is null) = (persona_id is null)),
  check (aceptada_en is null or revocada_en is null)
);
create index invitacion_unidad on nucleo.invitacion (consorcio_id, unidad_id, creada_en desc);
-- Una sola pendiente por correo, unidad y vínculo: invitar de nuevo retira la anterior.
create unique index invitacion_pendiente_unica on nucleo.invitacion
  (consorcio_id, unidad_id, tipo, correo) where aceptada_en is null and revocada_en is null;

alter table nucleo.invitacion enable row level security;
alter table nucleo.invitacion force row level security;
create policy por_consorcio on nucleo.invitacion
  using (consorcio_id = plataforma.consorcio_actual())
  with check (consorcio_id = plataforma.consorcio_actual());
-- Al crear, quien invita es la persona del ámbito (o nadie, si la carga el equipo con el
-- script). Solo al crear: retirar o reemplazar una pendiente de otra propietaria de la
-- unidad, o del equipo, lo decide `puede_invitar`, y `cem_app` solo cambia `revocada_en`.
create policy invitada_por_quien_crea on nucleo.invitacion as restrictive for insert
  with check (invitada_por is not distinct from plataforma.persona_actual());

-- Lo que ve quien abre el enlace, antes de aceptar. `resultado` es 'ok' o el motivo del
-- rechazo (para los registros; la API responde igual a todos).
create function nucleo.consultar_invitacion(token bytea)
  returns table (resultado text, consorcio text, unidad text, tipo text, correo text,
                 nombre text, invitada_por text, vence timestamptz)
  language plpgsql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    i nucleo.invitacion;
    motivo text;
  begin
    select * into i from nucleo.invitacion x where x.token_hash = token;
    motivo := nucleo.motivo_invitacion_invalida(i);
    if motivo is not null then
      return query select motivo, null::text, null::text, null::text, null::text, null::text,
                          null::text, null::timestamptz;
      return;
    end if;
    return query
    select 'ok'::text, c.nombre, u.designacion, i.tipo::text, i.correo, i.nombre, p.nombre,
           i.vence_en
      from nucleo.consorcio c
      join nucleo.unidad u on u.consorcio_id = c.id and u.id = i.unidad_id
      left join nucleo.persona p on p.id = i.invitada_por
     where c.id = i.consorcio_id;
  end
  $$;

-- Nulo si la invitación sirve; si no, por qué. Una fila nula es 'inexistente'.
create function nucleo.motivo_invitacion_invalida(i nucleo.invitacion)
  returns text
  language sql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
    select case
      when i.id is null then 'inexistente'
      when i.aceptada_en is not null then 'aceptada'
      when i.revocada_en is not null then 'revocada'
      when i.vence_en <= now() then 'vencida'
      when not exists (select 1 from nucleo.consorcio c
                        where c.id = i.consorcio_id and c.eliminado_en is null)
        then 'consorcio_eliminado'
      when not exists (select 1 from nucleo.unidad u
                        where u.id = i.unidad_id and u.eliminado_en is null)
        then 'unidad_eliminada'
    end
  $$;

-- Acepta la invitación. `persona_sesion` es la persona de la sesión de la petición, si hay.
--   'requiere_sesion'  el correo ya tiene cuenta y la petición no trae sesión.
--   'otra_persona'     la sesión es de otra persona que la del correo invitado.
--   'ok'               vínculo creado (o ya existía) e invitación aceptada; si el correo no
--                      tenía cuenta, `cuenta_nueva` y la sesión creada con `sesion_token`.
-- Los rechazos no consumen la invitación.
create function nucleo.aceptar_invitacion(
  token bytea, persona_sesion uuid, nombre_elegido text, sesion_token bytea,
  dispositivo_pedido uuid, agente_pedido text, ip_pedido inet, vigencia interval)
  returns table (resultado text, persona uuid, consorcio uuid, cuenta_nueva boolean,
                 sesion uuid, vence timestamptz)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    i nucleo.invitacion;
    motivo text;
    p uuid;
    nueva boolean := false;
    s uuid;
    v timestamptz;
    hoy date;
    vinculo uuid;
  begin
    select * into i from nucleo.invitacion x where x.token_hash = token for update;
    motivo := nucleo.motivo_invitacion_invalida(i);
    if motivo is not null then
      return query select motivo, null::uuid, null::uuid, null::boolean, null::uuid,
                          null::timestamptz;
      return;
    end if;
    -- Serializa el alta por correo: dos invitaciones al mismo correo nuevo aceptadas a la vez
    -- no crean dos personas.
    perform pg_advisory_xact_lock(hashtextextended('persona:' || i.correo, 0));
    select x.id into p from nucleo.persona x
     where x.correo = i.correo and x.eliminado_en is null;
    if p is null then
      if persona_sesion is not null then
        return query select 'otra_persona'::text, null::uuid, null::uuid, null::boolean,
                            null::uuid, null::timestamptz;
        return;
      end if;
      insert into nucleo.persona (correo, nombre)
      values (i.correo, btrim(coalesce(nombre_elegido, i.nombre)))
      returning id into p;
      nueva := true;
    elsif persona_sesion is null then
      return query select 'requiere_sesion'::text, null::uuid, null::uuid, null::boolean,
                          null::uuid, null::timestamptz;
      return;
    elsif persona_sesion <> p then
      return query select 'otra_persona'::text, null::uuid, null::uuid, null::boolean,
                          null::uuid, null::timestamptz;
      return;
    end if;

    select (now() at time zone c.zona_horaria)::date into hoy
      from nucleo.consorcio c where c.id = i.consorcio_id;
    select v2.id into vinculo from nucleo.vinculo v2
     where v2.consorcio_id = i.consorcio_id and v2.unidad_id = i.unidad_id
       and v2.persona_id = p and v2.tipo = i.tipo
       and daterange(v2.vigente_desde, v2.vigente_hasta) && daterange(hoy, null)
     limit 1;
    if vinculo is null then
      insert into nucleo.vinculo (consorcio_id, unidad_id, persona_id, tipo, vigente_desde)
      values (i.consorcio_id, i.unidad_id, p, i.tipo, hoy)
      returning id into vinculo;
    end if;
    update nucleo.invitacion set aceptada_en = now(), persona_id = p where id = i.id;

    insert into auditoria.eventos
      (consorcio_id, actor_persona_id, origen, accion, entidad, entidad_id, despues, ip,
       hash_previo, hash)
    values (i.consorcio_id, p, 'api', 'identidad.invitacion.aceptar', 'invitacion', i.id,
            jsonb_build_object('unidad_id', i.unidad_id, 'tipo', i.tipo,
                               'vinculo_id', vinculo, 'cuenta_nueva', nueva),
            ip_pedido, '', '');

    if nueva then
      insert into nucleo.sesion (persona_id, token_hash, dispositivo, agente, ip, vence_en)
      values (p, sesion_token, dispositivo_pedido, agente_pedido, ip_pedido, now() + vigencia)
      returning id, vence_en into s, v;
    end if;
    return query select 'ok'::text, p, i.consorcio_id, nueva, s, v;
  end
  $$;

-- Privilegios (§8.6) ------------------------------------------------------------------

grant select, insert, update (revocada_en) on nucleo.invitacion to cem_app;
revoke all on function
  nucleo.consultar_invitacion(bytea),
  nucleo.motivo_invitacion_invalida(nucleo.invitacion),
  nucleo.aceptar_invitacion(bytea, uuid, text, bytea, uuid, text, inet, interval)
  from public;
grant execute on function
  nucleo.consultar_invitacion(bytea),
  nucleo.aceptar_invitacion(bytea, uuid, text, bytea, uuid, text, inet, interval)
  to cem_app;
"""

DOWNGRADE = """
drop function if exists
  nucleo.consultar_invitacion(bytea),
  nucleo.aceptar_invitacion(bytea, uuid, text, bytea, uuid, text, inet, interval);
drop function if exists nucleo.motivo_invitacion_invalida(nucleo.invitacion);
drop table if exists nucleo.invitacion;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
