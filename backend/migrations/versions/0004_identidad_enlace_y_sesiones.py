"""Identidad: enlaces de acceso de un solo uso y sesiones.

Documento 13: §10.3 (enlace mágico: un solo uso, 15 minutos, ligado por un `nonce` al
navegador que lo pidió), §10.4 (sesiones opacas con persona, dispositivo, agente, IP, creada,
último uso, vencimiento y revocación), §15.2 (tokens de un solo uso guardados como hash) y
§8.5 (las tablas sin consorcio se filtran por persona).

Nunca se guarda el valor de un token, de un `nonce` ni de una cookie de sesión: solo su
SHA-256. Son valores aleatorios de 256 bits, así que un hash lento no agrega nada.

Pedir y canjear un enlace, y validar una sesión, ocurren antes de saber quién es la persona:
cruzan personas. Lo hacen tres funciones `security definer` de `cem_migrate` acotadas (como
las de la cola): reciben hashes, no SQL, y devuelven solo lo que la API necesita. `cem_app` no
tiene ningún privilegio sobre `nucleo.enlace_acceso` y de `nucleo.sesion` solo lee y revoca
las de la persona de su ámbito.

Revisión: 0004
Anterior: 0003
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

UPGRADE = r"""
-- Sesiones (§10.4) ----------------------------------------------------------------------

create table nucleo.sesion (
  id             uuid primary key default plataforma.uuid_v7(),
  persona_id     uuid not null references nucleo.persona (id),
  -- SHA-256 del valor de la cookie de sesión.
  token_hash     bytea not null unique check (length(token_hash) = 32),
  -- Identificador del navegador (cookie propia, no secreta): la sesión se lista por dispositivo.
  dispositivo    uuid not null,
  agente         text check (length(agente) <= 512),
  ip             inet not null,
  creada_en      timestamptz not null default now(),
  ultimo_uso_en  timestamptz not null default now(),
  vence_en       timestamptz not null,
  revocada_en    timestamptz,
  check (vence_en > creada_en),
  -- Vencimiento deslizante de 30 días como máximo desde el último uso (VIGENCIA_SESION).
  check (vence_en - ultimo_uso_en <= interval '30 days')
);
create index sesion_persona on nucleo.sesion (persona_id, creada_en);

-- Enlaces de acceso de un solo uso (§10.3) ---------------------------------------------

create table nucleo.enlace_acceso (
  id           uuid primary key default plataforma.uuid_v7(),
  persona_id   uuid not null references nucleo.persona (id),
  -- SHA-256 del token que viaja en el enlace y del nonce de la cookie del navegador.
  token_hash   bytea not null unique check (length(token_hash) = 32),
  nonce_hash   bytea not null check (length(nonce_hash) = 32),
  dispositivo  uuid not null,
  ip           inet not null,
  creado_en    timestamptz not null default now(),
  vence_en     timestamptz not null,
  usado_en     timestamptz,
  check (vence_en > creado_en),
  -- 15 minutos como máximo (VIGENCIA_ENLACE): la base lo impone aunque la API se equivoque.
  check (vence_en - creado_en <= interval '15 minutes')
);
create index enlace_acceso_persona on nucleo.enlace_acceso (persona_id, creado_en);

-- Seguridad por fila (§8.5) -------------------------------------------------------------
-- sesion: cada persona ve y revoca solo las suyas. enlace_acceso: sin políticas ni
-- privilegios para cem_app; solo la tocan las funciones de abajo.

alter table nucleo.sesion enable row level security;
alter table nucleo.sesion force row level security;
create policy propias on nucleo.sesion
  using (persona_id = plataforma.persona_actual())
  with check (persona_id = plataforma.persona_actual());

alter table nucleo.enlace_acceso enable row level security;
alter table nucleo.enlace_acceso force row level security;

-- Funciones que cruzan personas ----------------------------------------------------------
-- security definer: corren como cem_migrate (BYPASSRLS). search_path fijo.

-- Registra un enlace si el correo es de una persona activa. Devuelve si lo registró: la API
-- publica el correo solo en ese caso, pero responde lo mismo en los dos (§10.3).
create function nucleo.pedir_enlace(
  correo_pedido text, token bytea, nonce bytea, dispositivo_pedido uuid, ip_pedido inet,
  vigencia interval)
  returns boolean
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    persona uuid;
  begin
    select p.id into persona from nucleo.persona p
     where p.correo = lower(btrim(correo_pedido)) and p.eliminado_en is null;
    if persona is null then
      return false;
    end if;
    insert into nucleo.enlace_acceso
      (persona_id, token_hash, nonce_hash, dispositivo, ip, vence_en)
    values (persona, token, nonce, dispositivo_pedido, ip_pedido, now() + vigencia);
    return true;
  end
  $$;

-- Canjea un enlace y crea la sesión en la misma transacción. `resultado` es 'ok' o el motivo
-- del rechazo (para los registros; la API responde igual a todos). Un enlace vencido, de otro
-- navegador o de una persona dada de baja no se consume: un escáner de correo que abre el
-- enlace sin la cookie no se lo gasta a su dueño.
create function nucleo.canjear_enlace(
  token bytea, nonce bytea, sesion_token bytea, agente_pedido text, ip_pedido inet,
  vigencia interval)
  returns table (resultado text, persona uuid, sesion uuid, vence timestamptz)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    e nucleo.enlace_acceso;
  begin
    select * into e from nucleo.enlace_acceso where token_hash = token for update;
    if not found then
      return query select 'inexistente'::text, null::uuid, null::uuid, null::timestamptz;
      return;
    end if;
    if e.usado_en is not null then
      return query select 'usado'::text, null::uuid, null::uuid, null::timestamptz;
      return;
    end if;
    if e.vence_en <= now() then
      return query select 'vencido'::text, null::uuid, null::uuid, null::timestamptz;
      return;
    end if;
    if e.nonce_hash <> nonce then
      return query select 'otro_navegador'::text, null::uuid, null::uuid, null::timestamptz;
      return;
    end if;
    if not exists (select 1 from nucleo.persona p
                    where p.id = e.persona_id and p.eliminado_en is null) then
      return query select 'persona_eliminada'::text, null::uuid, null::uuid, null::timestamptz;
      return;
    end if;
    update nucleo.enlace_acceso set usado_en = now() where id = e.id;
    return query
    insert into nucleo.sesion as s (persona_id, token_hash, dispositivo, agente, ip, vence_en)
    values (e.persona_id, sesion_token, e.dispositivo, agente_pedido, ip_pedido, now() + vigencia)
    returning 'ok'::text, s.persona_id, s.id, s.vence_en;
  end
  $$;

-- Valida una sesión: existe, no está revocada ni vencida y la persona sigue activa. Si el
-- último uso tiene más de `renovar_cada`, corre el vencimiento (deslizante) y lo informa en
-- `renovada`, para que la API vuelva a emitir la cookie. Sin filas: sesión inválida.
create function nucleo.validar_sesion(token bytea, vigencia interval, renovar_cada interval)
  returns table (persona uuid, sesion uuid, vence timestamptz, renovada boolean)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  begin
    return query
    update nucleo.sesion s set ultimo_uso_en = now(), vence_en = now() + vigencia
     where s.token_hash = token and s.revocada_en is null and s.vence_en > now()
       and s.ultimo_uso_en <= now() - renovar_cada
       and exists (select 1 from nucleo.persona p
                    where p.id = s.persona_id and p.eliminado_en is null)
    returning s.persona_id, s.id, s.vence_en, true;
    if found then
      return;
    end if;
    return query
    select s.persona_id, s.id, s.vence_en, false from nucleo.sesion s
     where s.token_hash = token and s.revocada_en is null and s.vence_en > now()
       and exists (select 1 from nucleo.persona p
                    where p.id = s.persona_id and p.eliminado_en is null);
  end
  $$;

-- Privilegios (§8.6) ------------------------------------------------------------------

grant select, update (revocada_en) on nucleo.sesion to cem_app;
revoke all on nucleo.enlace_acceso from cem_app;
revoke all on function
  nucleo.pedir_enlace(text, bytea, bytea, uuid, inet, interval),
  nucleo.canjear_enlace(bytea, bytea, bytea, text, inet, interval),
  nucleo.validar_sesion(bytea, interval, interval)
  from public;
grant execute on function
  nucleo.pedir_enlace(text, bytea, bytea, uuid, inet, interval),
  nucleo.canjear_enlace(bytea, bytea, bytea, text, inet, interval),
  nucleo.validar_sesion(bytea, interval, interval)
  to cem_app;
"""

DOWNGRADE = """
drop function if exists
  nucleo.pedir_enlace(text, bytea, bytea, uuid, inet, interval),
  nucleo.canjear_enlace(bytea, bytea, bytea, text, inet, interval),
  nucleo.validar_sesion(bytea, interval, interval);
drop table if exists nucleo.enlace_acceso, nucleo.sesion;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
