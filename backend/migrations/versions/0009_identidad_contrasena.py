"""Identidad: contraseña con Argon2id y entrada en dos pasos cuando hay segundo factor.

Documento 13: §10.3 (contraseña + TOTP para administradores, consejeros y operadores;
Argon2id) y §15.2 (contraseñas con Argon2id). Plan, documento 14, §6.4 (2a).

- `nucleo.contrasena`: una fila por persona con el hash Argon2id en el formato PHC
  (`$argon2id$v=19$m=…,t=…,p=…$sal$hash`): lleva sus parámetros y su sal, así que un cambio de
  parámetros convive con los hashes viejos y se rehace al entrar. Nunca la contraseña.
- `nucleo.desafio_contrasena`: el paso intermedio de quien acertó la contraseña y tiene el
  segundo factor activo. Uno por persona (uno nuevo reemplaza al anterior), 5 minutos, cinco
  intentos; en la base queda el SHA-256 del token que va en la cookie `__Host-cem_desafio`.
  Se borra al usarlo, así que no se acumula: no necesita purga.

Seguridad por fila: `nucleo.contrasena` es de la persona (RLS forzada por `persona_actual()`,
como la 0008); con sesión, `cem_app` lee y escribe solo la suya. `nucleo.desafio_contrasena`
no tiene políticas ni privilegios para `cem_app`.

La entrada ocurre antes de saber quién es la persona, así que cruza personas con funciones
`security definer` acotadas (como las de la 0004 y la 0006). La comprobación de Argon2id
corre en la API (PostgreSQL no la tiene), por eso `nucleo.contrasena_de` le devuelve el hash
del correo pedido: es lo mínimo para verificar, y el mismo alcance que ya tiene `cem_app`
sobre la persona de su ámbito. Las funciones que crean la sesión reciben el hash que la API
verificó y lo comparan con el vigente (una contraseña cambiada en el medio no abre sesión), y
la base se niega a abrir una sesión sin el segundo factor a quien lo tiene activo.

Revisión: 0009
Anterior: 0008
"""

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

UPGRADE = r"""
create table nucleo.contrasena (
  persona_id   uuid primary key references nucleo.persona (id),
  -- Formato PHC de Argon2id; nunca la contraseña.
  hash         text not null check (hash like '$argon2id$%' and length(hash) <= 256),
  creada_en    timestamptz not null default now(),
  cambiada_en  timestamptz not null default now(),
  check (cambiada_en >= creada_en)
);

create table nucleo.desafio_contrasena (
  -- Uno por persona: pedir otro (volver a escribir la contraseña) reemplaza el anterior.
  persona_id  uuid primary key references nucleo.persona (id),
  -- SHA-256 del token de la cookie `__Host-cem_desafio`.
  token_hash  bytea not null unique check (length(token_hash) = 32),
  creado_en   timestamptz not null default now(),
  vence_en    timestamptz not null,
  -- Códigos fallidos. Al quinto (INTENTOS_DESAFIO) queda agotado.
  intentos    smallint not null default 0 check (intentos between 0 and 5),
  check (vence_en > creado_en),
  -- 5 minutos como máximo (VIGENCIA_DESAFIO): la base lo impone aunque la API se equivoque.
  check (vence_en - creado_en <= interval '5 minutes')
);

alter table nucleo.contrasena enable row level security;
alter table nucleo.contrasena force row level security;
create policy propia on nucleo.contrasena
  using (persona_id = plataforma.persona_actual())
  with check (persona_id = plataforma.persona_actual());

alter table nucleo.desafio_contrasena enable row level security;
alter table nucleo.desafio_contrasena force row level security;

-- Funciones que cruzan personas ----------------------------------------------------------
-- security definer: corren como cem_migrate (BYPASSRLS). search_path fijo.

-- El hash del correo pedido, si es de una persona activa con contraseña, y si tiene el segundo
-- factor activo. Sin filas: no hay cuenta o no tiene contraseña (la API responde igual).
create function nucleo.contrasena_de(correo_pedido text)
  returns table (persona uuid, hash text, con_segundo_factor boolean)
  language sql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
    select p.id, c.hash,
           exists (select 1 from nucleo.factor_totp f
                    where f.persona_id = p.id and f.confirmado_en is not null)
      from nucleo.persona p join nucleo.contrasena c on c.persona_id = p.id
     where p.correo = lower(btrim(correo_pedido)) and p.eliminado_en is null
  $$;

-- 'ok' si `hash_verificado` sigue siendo el vigente de una persona activa; si no, el motivo.
create function nucleo.contrasena_vigente(persona_pedida uuid, hash_verificado text)
  returns text
  language plpgsql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    vigente text;
  begin
    select c.hash into vigente from nucleo.contrasena c join nucleo.persona p on p.id = c.persona_id
     where c.persona_id = persona_pedida and p.eliminado_en is null;
    if vigente is null then
      return 'sin_contrasena';
    end if;
    if vigente <> hash_verificado then
      return 'cambiada';
    end if;
    return 'ok';
  end
  $$;

create function nucleo.tiene_segundo_factor(persona_pedida uuid)
  returns boolean
  language sql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
    select exists (select 1 from nucleo.factor_totp f
                    where f.persona_id = persona_pedida and f.confirmado_en is not null)
  $$;

-- Abre la sesión de quien acertó la contraseña y no tiene segundo factor. Con el segundo
-- factor activo se niega ('falta_segundo_factor'): ese camino pasa por el desafío.
create function nucleo.entrar_con_contrasena(
  persona_pedida uuid, hash_verificado text, sesion_token bytea, dispositivo_pedido uuid,
  agente_pedido text, ip_pedido inet, vigencia interval)
  returns table (resultado text, persona uuid, sesion uuid, vence timestamptz)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    estado text := nucleo.contrasena_vigente(persona_pedida, hash_verificado);
  begin
    if estado <> 'ok' then
      return query select estado, null::uuid, null::uuid, null::timestamptz;
      return;
    end if;
    if nucleo.tiene_segundo_factor(persona_pedida) then
      return query select 'falta_segundo_factor'::text, null::uuid, null::uuid, null::timestamptz;
      return;
    end if;
    return query
    insert into nucleo.sesion as s (persona_id, token_hash, dispositivo, agente, ip, vence_en)
    values (persona_pedida, sesion_token, dispositivo_pedido, agente_pedido, ip_pedido,
            now() + vigencia)
    returning 'ok'::text, s.persona_id, s.id, s.vence_en;
  end
  $$;

-- Registra el desafío de quien acertó la contraseña y tiene el segundo factor activo.
create function nucleo.pedir_desafio_contrasena(
  persona_pedida uuid, hash_verificado text, token bytea, vigencia interval)
  returns table (resultado text, vence timestamptz)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    estado text := nucleo.contrasena_vigente(persona_pedida, hash_verificado);
  begin
    if estado <> 'ok' then
      return query select estado, null::timestamptz;
      return;
    end if;
    if not nucleo.tiene_segundo_factor(persona_pedida) then
      return query select 'sin_segundo_factor'::text, null::timestamptz;
      return;
    end if;
    return query
    insert into nucleo.desafio_contrasena as d (persona_id, token_hash, vence_en)
    values (persona_pedida, token, now() + vigencia)
    on conflict (persona_id) do update set
      token_hash = excluded.token_hash, creado_en = now(), vence_en = excluded.vence_en,
      intentos = 0
    returning 'ok'::text, d.vence_en;
  end
  $$;

-- La persona del desafío vigente (no vencido ni agotado, persona activa), con la fila
-- bloqueada hasta el final de la transacción: dos códigos a la vez no se pisan el contador.
-- Nulo: no sirve.
create function nucleo.desafio_contrasena_de(token bytea, maximo_intentos integer)
  returns uuid
  language sql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
    select d.persona_id from nucleo.desafio_contrasena d
     where d.token_hash = token and d.vence_en > now() and d.intentos < maximo_intentos
       and exists (select 1 from nucleo.persona p
                    where p.id = d.persona_id and p.eliminado_en is null)
       for update of d
  $$;

-- Resuelve el desafío con el resultado del código, que la API comprobó en la misma
-- transacción. Incorrecto: suma un intento (la API confirma aunque responda error). Correcto:
-- borra el desafío y abre la sesión con el segundo factor ya verificado (TB-CEM-3).
create function nucleo.resolver_desafio_contrasena(
  token bytea, acierto boolean, sesion_token bytea, dispositivo_pedido uuid,
  agente_pedido text, ip_pedido inet, vigencia interval, maximo_intentos integer)
  returns table (resultado text, persona uuid, sesion uuid, vence timestamptz)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    d nucleo.desafio_contrasena;
  begin
    select * into d from nucleo.desafio_contrasena x where x.token_hash = token for update;
    if not found then
      return query select 'inexistente'::text, null::uuid, null::uuid, null::timestamptz;
      return;
    end if;
    if d.vence_en <= now() then
      return query select 'vencido'::text, null::uuid, null::uuid, null::timestamptz;
      return;
    end if;
    if d.intentos >= maximo_intentos then
      return query select 'agotado'::text, null::uuid, null::uuid, null::timestamptz;
      return;
    end if;
    if not acierto then
      update nucleo.desafio_contrasena set intentos = intentos + 1 where persona_id = d.persona_id;
      return query select 'incorrecto'::text, null::uuid, null::uuid, null::timestamptz;
      return;
    end if;
    delete from nucleo.desafio_contrasena where persona_id = d.persona_id;
    return query
    insert into nucleo.sesion as s
      (persona_id, token_hash, dispositivo, agente, ip, vence_en, segundo_factor_en)
    values (d.persona_id, sesion_token, dispositivo_pedido, agente_pedido, ip_pedido,
            now() + vigencia, now())
    returning 'ok'::text, s.persona_id, s.id, s.vence_en;
  end
  $$;

-- Privilegios (§8.6) ------------------------------------------------------------------

grant select, insert, update (hash, cambiada_en) on nucleo.contrasena to cem_app;
revoke all on nucleo.desafio_contrasena from cem_app;
revoke all on function
  nucleo.contrasena_de(text),
  nucleo.contrasena_vigente(uuid, text),
  nucleo.tiene_segundo_factor(uuid),
  nucleo.entrar_con_contrasena(uuid, text, bytea, uuid, text, inet, interval),
  nucleo.pedir_desafio_contrasena(uuid, text, bytea, interval),
  nucleo.desafio_contrasena_de(bytea, integer),
  nucleo.resolver_desafio_contrasena(bytea, boolean, bytea, uuid, text, inet, interval, integer)
  from public;
grant execute on function
  nucleo.contrasena_de(text),
  nucleo.entrar_con_contrasena(uuid, text, bytea, uuid, text, inet, interval),
  nucleo.pedir_desafio_contrasena(uuid, text, bytea, interval),
  nucleo.desafio_contrasena_de(bytea, integer),
  nucleo.resolver_desafio_contrasena(bytea, boolean, bytea, uuid, text, inet, interval, integer)
  to cem_app;
"""

DOWNGRADE = """
drop function if exists
  nucleo.resolver_desafio_contrasena(bytea, boolean, bytea, uuid, text, inet, interval, integer),
  nucleo.desafio_contrasena_de(bytea, integer),
  nucleo.pedir_desafio_contrasena(uuid, text, bytea, interval),
  nucleo.entrar_con_contrasena(uuid, text, bytea, uuid, text, inet, interval),
  nucleo.tiene_segundo_factor(uuid),
  nucleo.contrasena_vigente(uuid, text),
  nucleo.contrasena_de(text);
drop table if exists nucleo.desafio_contrasena, nucleo.contrasena;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
