"""Cola de trabajos, outbox de eventos y ejecuciones del planificador.

Documento 13: §12.1 (cola en PostgreSQL con `SKIP LOCKED`), §12.2 (outbox en la misma
transacción), §12.3 (planificador con `UNIQUE(tipo, ventana)`) y §16.2 (`/health/cola`).

Seguridad por fila: `jobs` y `outbox` tienen `consorcio_id`, así que llevan RLS forzada.
La API, `cem-trabajos` y `cem-planificador` usan el mismo rol `cem_app` (§8.6), que solo
puede encolar y leer en el consorcio de su ámbito. Tomar, terminar y fallar trabajos cruza
consorcios: eso lo hacen funciones `security definer` de `cem_migrate` que no devuelven
más que lo que el trabajador necesita y no aceptan SQL del llamador.

Revisión: 0002
Anterior: 0001
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

UPGRADE = r"""
create type plataforma.estado_trabajo as enum ('pendiente', 'en_curso', 'hecho', 'muerto');

-- Cola (§12.1) ------------------------------------------------------------------------

create table plataforma.jobs (
  id                uuid primary key default plataforma.uuid_v7(),
  tipo              text not null check (tipo ~ '^[a-z_]+(\.[a-z_]+)+$'),
  -- Nulo para los trabajos de plataforma (purga, copias): no pertenecen a un consorcio.
  consorcio_id      uuid references nucleo.consorcio (id),
  carga             jsonb not null default '{}',
  estado            plataforma.estado_trabajo not null default 'pendiente',
  intentos          integer not null default 0 check (intentos >= 0),
  maximo_intentos   integer not null default 3 check (maximo_intentos between 1 and 20),
  disponible_desde  timestamptz not null default now(),
  bloqueado_por     text,
  bloqueado_hasta   timestamptz,
  resultado         jsonb,
  error             text,
  -- Un mismo efecto no se encola dos veces (evento del outbox, ventana del planificador).
  clave_unica       text unique,
  creado_en         timestamptz not null default now(),
  terminado_en      timestamptz,
  check ((estado = 'en_curso') = (bloqueado_por is not null and bloqueado_hasta is not null))
);
create index jobs_listos on plataforma.jobs (disponible_desde) where estado = 'pendiente';
create index jobs_en_curso on plataforma.jobs (bloqueado_hasta) where estado = 'en_curso';
create index jobs_muertos on plataforma.jobs (terminado_en) where estado = 'muerto';
create index jobs_consorcio on plataforma.jobs (consorcio_id, creado_en);

-- Outbox (§12.2) ----------------------------------------------------------------------

create table plataforma.outbox (
  id             uuid primary key default plataforma.uuid_v7(),
  consorcio_id   uuid references nucleo.consorcio (id),
  evento         text not null check (evento ~ '^[a-z_]+(\.[a-z_]+)+$'),
  carga          jsonb not null default '{}',
  creado_en      timestamptz not null default clock_timestamp(),
  despachado_en  timestamptz
);
create index outbox_pendientes on plataforma.outbox (id) where despachado_en is null;
create index outbox_consorcio on plataforma.outbox (consorcio_id, creado_en);

-- Ejecuciones del planificador (§12.3) ------------------------------------------------
-- Sin consorcio: una fila por tarea periódica y ventana. La clave primaria impide que un
-- reinicio o un segundo planificador vuelvan a encolar la misma ventana.

create table plataforma.ejecuciones_programadas (
  tipo        text not null,
  ventana     timestamptz not null,
  trabajo_id  uuid,
  creado_en   timestamptz not null default now(),
  primary key (tipo, ventana)
);

-- Seguridad por fila (§8.5) -----------------------------------------------------------
-- Encolar, publicar y leer solo en el consorcio del ámbito; sin consorcio en el ámbito, solo
-- los trabajos y eventos de plataforma (sin consorcio). La lectura hace falta además para
-- `insert ... returning`, que PostgreSQL somete a la política de SELECT.

alter table plataforma.jobs enable row level security;
alter table plataforma.jobs force row level security;
create policy encolar on plataforma.jobs for insert
  with check (consorcio_id is not distinct from plataforma.consorcio_actual());
create policy por_consorcio on plataforma.jobs for select
  using (consorcio_id is not distinct from plataforma.consorcio_actual());

alter table plataforma.outbox enable row level security;
alter table plataforma.outbox force row level security;
create policy publicar on plataforma.outbox for insert
  with check (consorcio_id is not distinct from plataforma.consorcio_actual());
create policy por_consorcio on plataforma.outbox for select
  using (consorcio_id is not distinct from plataforma.consorcio_actual());

-- Funciones del trabajador y del planificador -----------------------------------------
-- security definer: corren como cem_migrate (BYPASSRLS). search_path fijo para que nadie
-- las desvíe con un objeto propio.

-- Toma hasta `cantidad` trabajos listos y los reserva por `plazo`. Un trabajo cuyo plazo
-- venció (el trabajador murió) vuelve a tomarse; si ya agotó sus intentos pasa a muerto.
create function plataforma.tomar_trabajos(trabajador text, cantidad integer, plazo interval)
  returns setof plataforma.jobs
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  begin
    with vencidos as (
      select j.id from plataforma.jobs j
       where j.estado = 'en_curso' and j.bloqueado_hasta < now()
         and j.intentos >= j.maximo_intentos
       for update skip locked
    )
    update plataforma.jobs j
       set estado = 'muerto', bloqueado_por = null, bloqueado_hasta = null,
           terminado_en = now(),
           error = 'El trabajador no terminó dentro del plazo en el último intento.'
      from vencidos where j.id = vencidos.id;

    return query
    with elegidos as (
      select j.id from plataforma.jobs j
       where (j.estado = 'pendiente' and j.disponible_desde <= now())
          or (j.estado = 'en_curso' and j.bloqueado_hasta < now()
              and j.intentos < j.maximo_intentos)
       order by j.disponible_desde, j.id
       limit cantidad
       for update skip locked
    )
    update plataforma.jobs j
       set estado = 'en_curso', intentos = j.intentos + 1,
           bloqueado_por = trabajador, bloqueado_hasta = now() + plazo
      from elegidos where j.id = elegidos.id
    returning j.*;
  end
  $$;

-- Marca hecho un trabajo que este trabajador tiene reservado. Se llama dentro de la misma
-- transacción que el efecto del trabajo: o quedan los dos o ninguno. Devuelve falso si la
-- reserva ya no es suya (otro la tomó al vencer el plazo).
create function plataforma.terminar_trabajo(trabajo uuid, trabajador text, salida jsonb)
  returns boolean
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  begin
    update plataforma.jobs
       set estado = 'hecho', resultado = salida, error = null, terminado_en = now(),
           bloqueado_por = null, bloqueado_hasta = null
     where id = trabajo and estado = 'en_curso' and bloqueado_por = trabajador;
    return found;
  end
  $$;

-- Registra un fallo. Con `reintentar_en` nulo, o sin intentos restantes, el trabajo queda
-- muerto (alerta en /health/cola); si no, vuelve a pendiente tras el retroceso.
-- Devuelve el estado nuevo, o nulo si la reserva ya no es de este trabajador.
create function plataforma.fallar_trabajo(
  trabajo uuid, trabajador text, mensaje text, reintentar_en interval)
  returns plataforma.estado_trabajo
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    nuevo plataforma.estado_trabajo;
  begin
    update plataforma.jobs
       set estado = case when reintentar_en is null or intentos >= maximo_intentos
                         then 'muerto' else 'pendiente' end::plataforma.estado_trabajo,
           disponible_desde = case when reintentar_en is null then disponible_desde
                                   else now() + reintentar_en end,
           terminado_en = case when reintentar_en is null or intentos >= maximo_intentos
                               then now() end,
           error = left(mensaje, 4000), bloqueado_por = null, bloqueado_hasta = null
     where id = trabajo and estado = 'en_curso' and bloqueado_por = trabajador
    returning estado into nuevo;
    return nuevo;
  end
  $$;

-- Reserva eventos sin despachar. Se llama en la misma transacción que encola sus trabajos:
-- si el despacho falla, los eventos siguen pendientes.
create function plataforma.tomar_eventos(cantidad integer)
  returns setof plataforma.outbox
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  begin
    return query
    with elegidos as (
      select o.id from plataforma.outbox o
       where o.despachado_en is null
       order by o.id
       limit cantidad
       for update skip locked
    )
    update plataforma.outbox o set despachado_en = clock_timestamp()
      from elegidos where o.id = elegidos.id
    returning o.*;
  end
  $$;

-- Totales sin datos de ningún consorcio, para /health/cola y Kuma (§16.2 y §16.3).
create function plataforma.estado_cola()
  returns table (pendientes bigint, en_curso bigint, muertos bigint,
                 segundos_mas_viejo double precision)
  language sql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
    select count(*) filter (where estado = 'pendiente'),
           count(*) filter (where estado = 'en_curso'),
           count(*) filter (where estado = 'muerto'),
           coalesce(extract(epoch from now() - min(disponible_desde)
                    filter (where estado = 'pendiente' and disponible_desde <= now())), 0)::float8
      from plataforma.jobs
     where estado <> 'hecho'
  $$;

-- Borra lo terminado con más de `antiguedad`. Los muertos se conservan hasta que soporte
-- los reprocese o los descarte.
create function plataforma.purgar_cola(antiguedad interval)
  returns bigint
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    borrados bigint;
    n bigint;
  begin
    delete from plataforma.jobs where estado = 'hecho' and terminado_en < now() - antiguedad;
    get diagnostics borrados = row_count;
    delete from plataforma.outbox where despachado_en < now() - antiguedad;
    get diagnostics n = row_count;
    borrados := borrados + n;
    delete from plataforma.ejecuciones_programadas where ventana < now() - antiguedad;
    get diagnostics n = row_count;
    return borrados + n;
  end
  $$;

-- Privilegios (§8.6) ------------------------------------------------------------------

grant usage on type plataforma.estado_trabajo to cem_app;
grant select, insert on plataforma.jobs, plataforma.outbox to cem_app;
grant select, insert, update on plataforma.ejecuciones_programadas to cem_app;
revoke all on function
  plataforma.tomar_trabajos(text, integer, interval),
  plataforma.terminar_trabajo(uuid, text, jsonb),
  plataforma.fallar_trabajo(uuid, text, text, interval),
  plataforma.tomar_eventos(integer),
  plataforma.estado_cola(),
  plataforma.purgar_cola(interval)
  from public;
grant execute on function
  plataforma.tomar_trabajos(text, integer, interval),
  plataforma.terminar_trabajo(uuid, text, jsonb),
  plataforma.fallar_trabajo(uuid, text, text, interval),
  plataforma.tomar_eventos(integer),
  plataforma.estado_cola(),
  plataforma.purgar_cola(interval)
  to cem_app;
"""

DOWNGRADE = """
drop function if exists
  plataforma.tomar_trabajos(text, integer, interval),
  plataforma.terminar_trabajo(uuid, text, jsonb),
  plataforma.fallar_trabajo(uuid, text, text, interval),
  plataforma.tomar_eventos(integer),
  plataforma.estado_cola(),
  plataforma.purgar_cola(interval);
drop table if exists
  plataforma.ejecuciones_programadas, plataforma.outbox, plataforma.jobs;
drop type if exists plataforma.estado_trabajo;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
