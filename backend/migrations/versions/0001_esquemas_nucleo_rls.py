"""Esquemas, núcleo (consorcio, persona, unidad, vínculo), auditoría encadenada y RLS.

Documento 13: §8.1 (esquemas), §8.3 (diseño físico), §8.4 (auditoría inmutable),
§8.5 (seguridad por fila) y §8.6 (roles). Corre con `cem_migrate`; los roles los crea
antes `scripts/crear_roles.sql`.

Revisión: 0001
Anterior: ninguna
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

ESQUEMAS = (
    "nucleo",
    "economico",
    "operacion",
    "comunidad",
    "documentos",
    "mensajeria",
    "auditoria",
    "plataforma",
    "comparador",
)

UPGRADE = r"""
-- Esquemas (§8.1). plataforma ya existe: lo crea env.py para la tabla de versiones.
{esquemas}

create extension if not exists btree_gist;

-- Ámbito de la petición, fijado por la API con set_config(..., true) (§7.2).
-- Si falta, devuelven NULL o un arreglo vacío y las políticas no dejan pasar ninguna fila.
create function plataforma.persona_actual() returns uuid
  language sql stable
  as $$ select nullif(current_setting('app.persona_id', true), '')::uuid $$;

create function plataforma.consorcio_actual() returns uuid
  language sql stable
  as $$ select nullif(current_setting('app.consorcio_id', true), '')::uuid $$;

create function plataforma.consorcios_persona() returns uuid[]
  language sql stable
  as $$
    select coalesce(nullif(current_setting('app.consorcios_persona', true), ''), '{{}}')::uuid[]
  $$;

-- uuid v7 ordenable por tiempo (§8.3). PostgreSQL 18 trae uuidv7(); esta versión sirve
-- también en 16 y 17 y produce el mismo formato (RFC 9562).
create function plataforma.uuid_v7() returns uuid
  language sql volatile
  as $$
    select encode(
      set_bit(set_bit(
        overlay(uuid_send(gen_random_uuid())
                placing substring(
                  int8send(floor(extract(epoch from clock_timestamp()) * 1000)::bigint) from 3)
                from 1 for 6),
        52, 1), 53, 1),
      'hex')::uuid
  $$;

-- Núcleo ---------------------------------------------------------------------------

create type nucleo.tipo_vinculo as enum ('propietario', 'inquilino', 'ocupante');

create table nucleo.consorcio (
  id            uuid primary key default plataforma.uuid_v7(),
  nombre        text not null check (length(btrim(nombre)) > 0),
  cuit          text check (cuit ~ '^[0-9]{{11}}$'),
  zona_horaria  text not null default 'America/Argentina/Buenos_Aires',
  creado_en     timestamptz not null default now(),
  eliminado_en  timestamptz
);

create table nucleo.persona (
  id            uuid primary key default plataforma.uuid_v7(),
  correo        text not null check (correo = lower(btrim(correo)) and position('@' in correo) > 1),
  nombre        text not null check (length(btrim(nombre)) > 0),
  creado_en     timestamptz not null default now(),
  eliminado_en  timestamptz
);
create unique index persona_correo_unico on nucleo.persona (correo) where eliminado_en is null;

create table nucleo.unidad (
  id            uuid primary key default plataforma.uuid_v7(),
  consorcio_id  uuid not null references nucleo.consorcio (id),
  designacion   text not null check (length(btrim(designacion)) > 0),
  creado_en     timestamptz not null default now(),
  eliminado_en  timestamptz,
  -- Destino de las claves foráneas compuestas: una referencia cruzada entre consorcios
  -- es imposible por construcción (§8.3).
  unique (consorcio_id, id)
);
create unique index unidad_designacion_unica
  on nucleo.unidad (consorcio_id, designacion) where eliminado_en is null;

create table nucleo.vinculo (
  id             uuid primary key default plataforma.uuid_v7(),
  consorcio_id   uuid not null,
  unidad_id      uuid not null,
  persona_id     uuid not null references nucleo.persona (id),
  tipo           nucleo.tipo_vinculo not null,
  vigente_desde  date not null,
  vigente_hasta  date,
  creado_en      timestamptz not null default now(),
  foreign key (consorcio_id, unidad_id) references nucleo.unidad (consorcio_id, id),
  check (vigente_hasta is null or vigente_hasta > vigente_desde),
  -- Sin vigencias solapadas del mismo vínculo (§8.3).
  exclude using gist (
    consorcio_id with =, unidad_id with =, persona_id with =, tipo with =,
    daterange(vigente_desde, vigente_hasta) with &&
  )
);
create index vinculo_persona on nucleo.vinculo (persona_id);

-- Auditoría inmutable y encadenada (§8.4) -------------------------------------------

create sequence auditoria.eventos_secuencia;

create table auditoria.eventos (
  consorcio_id      uuid not null references nucleo.consorcio (id),
  id                uuid not null default plataforma.uuid_v7(),
  secuencia         bigint not null default nextval('auditoria.eventos_secuencia'),
  creado_en         timestamptz not null default clock_timestamp(),
  actor_persona_id  uuid,
  origen            text not null check (origen in ('api', 'trabajos', 'planificador', 'soporte')),
  accion            text not null,
  entidad           text not null,
  entidad_id        uuid,
  antes             jsonb,
  despues           jsonb,
  motivo            text,
  ip                inet,
  hash_previo       text not null,
  hash              text not null,
  primary key (consorcio_id, id, creado_en)
) partition by range (creado_en);
-- Las particiones mensuales las crea el planificador; la predeterminada recibe el resto.
create table auditoria.eventos_predeterminada partition of auditoria.eventos default;
create index eventos_cadena on auditoria.eventos (consorcio_id, secuencia desc);

-- Cada fila guarda el SHA-256 de la anterior del mismo consorcio; el valor que mande
-- la aplicación se ignora. El bloqueo consultivo serializa la cadena por consorcio.
create function auditoria.encadenar() returns trigger
  language plpgsql
  as $$
  begin
    perform pg_advisory_xact_lock(hashtextextended('auditoria:' || new.consorcio_id::text, 0));
    select e.hash into new.hash_previo
      from auditoria.eventos e
     where e.consorcio_id = new.consorcio_id
     order by e.secuencia desc
     limit 1;
    new.hash_previo := coalesce(new.hash_previo, '');
    new.hash := encode(sha256(convert_to(concat_ws('|',
      new.hash_previo, new.consorcio_id, new.id, new.secuencia,
      to_char(new.creado_en at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US'),
      new.actor_persona_id, new.origen, new.accion, new.entidad, new.entidad_id,
      new.antes, new.despues, new.motivo, new.ip), 'UTF8')), 'hex');
    return new;
  end
  $$;
create trigger eventos_encadenar before insert on auditoria.eventos
  for each row execute function auditoria.encadenar();

-- Seguridad por fila (§8.5) -----------------------------------------------------------
-- FORCE alcanza también al propietario; cem_migrate la saltea solo porque tiene BYPASSRLS.

alter table nucleo.consorcio enable row level security;
alter table nucleo.consorcio force row level security;
create policy por_consorcio on nucleo.consorcio
  using (id = plataforma.consorcio_actual())
  with check (id = plataforma.consorcio_actual());
create policy por_consorcios_persona on nucleo.consorcio for select
  using (id = any (plataforma.consorcios_persona()));

alter table nucleo.persona enable row level security;
alter table nucleo.persona force row level security;
create policy propia on nucleo.persona
  using (id = plataforma.persona_actual())
  with check (id = plataforma.persona_actual());

alter table nucleo.unidad enable row level security;
alter table nucleo.unidad force row level security;
create policy por_consorcio on nucleo.unidad
  using (consorcio_id = plataforma.consorcio_actual())
  with check (consorcio_id = plataforma.consorcio_actual());
create policy por_consorcios_persona on nucleo.unidad for select
  using (consorcio_id = any (plataforma.consorcios_persona()));

alter table nucleo.vinculo enable row level security;
alter table nucleo.vinculo force row level security;
create policy por_consorcio on nucleo.vinculo
  using (consorcio_id = plataforma.consorcio_actual())
  with check (consorcio_id = plataforma.consorcio_actual());
create policy por_consorcios_persona on nucleo.vinculo for select
  using (consorcio_id = any (plataforma.consorcios_persona()));
-- La persona ve sus propios vínculos en cualquier consorcio: así require_scope arma el ámbito.
create policy propios on nucleo.vinculo for select
  using (persona_id = plataforma.persona_actual());

alter table auditoria.eventos enable row level security;
alter table auditoria.eventos force row level security;
create policy por_consorcio on auditoria.eventos
  using (consorcio_id = plataforma.consorcio_actual())
  with check (consorcio_id = plataforma.consorcio_actual());

-- Privilegios (§8.1, §8.6) -------------------------------------------------------------

revoke all on schema {lista} from public;
grant usage on schema nucleo, auditoria, plataforma to cem_app;
grant select, insert, update, delete
  on nucleo.consorcio, nucleo.persona, nucleo.unidad, nucleo.vinculo to cem_app;
-- Auditoría: solo agregar y leer; sin UPDATE ni DELETE.
grant select, insert on auditoria.eventos to cem_app;
grant usage on sequence auditoria.eventos_secuencia to cem_app;
revoke all on function plataforma.persona_actual(), plataforma.consorcio_actual(),
  plataforma.consorcios_persona(), plataforma.uuid_v7(), auditoria.encadenar() from public;
grant execute on function plataforma.persona_actual(), plataforma.consorcio_actual(),
  plataforma.consorcios_persona(), plataforma.uuid_v7(), auditoria.encadenar() to cem_app;
"""


def upgrade() -> None:
    crear = "\n".join(f"create schema if not exists {e};" for e in ESQUEMAS)
    op.execute(UPGRADE.format(esquemas=crear, lista=", ".join(ESQUEMAS)))


def downgrade() -> None:
    # La tabla de versiones de Alembic vive en plataforma: se vacía el esquema sin borrarlo.
    for esquema in ESQUEMAS:
        if esquema != "plataforma":
            op.execute(f"drop schema if exists {esquema} cascade")
    op.execute(
        "drop function if exists plataforma.persona_actual(), plataforma.consorcio_actual(),"
        " plataforma.consorcios_persona(), plataforma.uuid_v7()"
    )
