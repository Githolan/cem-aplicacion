"""Esquemas de coeficientes, coeficientes jurídicos y reglas de reparto (H2b 2b-5, PR 2; D4, RF05).

Diseño: `doc/diseno/H2b_5_Diseno.md` del repositorio CEM, §3.

- `nucleo.esquema_coeficientes`: un esquema por grupo de gasto (`expensas_comunes`, `agua`,
  `cocheras`…), versionado. La versión `n + 1` rige desde una fecha posterior a la de `n`: el
  esquema de un día es la última versión con `vigente_desde <= día`. Guarda la referencia de la
  fuente (reglamento, escritura o acta) y la decisión que lo respalda.
- `nucleo.coeficiente_juridico`: el porcentaje de cada unidad **tal como dice la fuente**
  (`NUMERIC(9,6)`, entre 0 y 100). Nunca se ajusta para que la suma cierre: si la fuente suma
  99,96 %, se guarda 99,96 %. Toda unidad activa del consorcio figura en cada esquema; el 0 % es
  explícito (una unidad sin cochera).
- `nucleo.regla_reparto`: cómo se calcula con ese esquema. Hoy, proporcional a los coeficientes
  con los centavos residuales por restos mayores (el motor de `app/liquidacion/reparto.py`).
  Cada cierre de H3 referenciará una versión (RF05).
- Las tres tablas son inalterables para `cem_app` (solo lectura, RLS por consorcio). Se escriben
  con `nucleo.registrar_esquema_coeficientes`, que en este corte acepta solo el origen
  `soporte`: proponer y aprobar por la API (`coeficientes.proponer` y `coeficientes.aprobar`,
  económica) esperan las decisiones de 2b-6.
- Los porcentajes llegan como texto (`"12.345678"`), nunca como número JSON: un `float` no
  garantiza la exactitud que pide D4.

Downgrade: se niega (`downgrade_con_datos`) si hay algún esquema; si no, quita todo.

Revisión: 0021
Anterior: 0020
"""

from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None

UPGRADE = r"""
create type nucleo.metodo_reparto as enum ('proporcional_a_coeficientes');
create type nucleo.tratamiento_centavos as enum ('restos_mayores');

create table nucleo.esquema_coeficientes (
  id                 uuid primary key default plataforma.uuid_v7(),
  consorcio_id       uuid not null references nucleo.consorcio (id),
  grupo              text not null,
  version            integer not null,
  fuente_referencia  text not null,
  vigente_desde      date not null,
  decision_id        uuid not null,
  origen             text not null,
  registrado_por     uuid references nucleo.persona (id),
  creado_en          timestamptz not null default now(),
  unique (consorcio_id, id),
  constraint esquema_version_unica unique (consorcio_id, grupo, version),
  constraint esquema_vigencia_unica unique (consorcio_id, grupo, vigente_desde),
  foreign key (consorcio_id, decision_id) references nucleo.decision_formal (consorcio_id, id),
  constraint esquema_grupo_valido check (grupo ~ '^[a-z][a-z0-9_]{1,39}$'),
  constraint esquema_version_positiva check (version >= 1),
  constraint esquema_fuente_referencia
    check (length(btrim(fuente_referencia)) between 1 and 300),
  constraint esquema_origen_valido check (origen in ('api', 'soporte')),
  constraint esquema_registrado_segun_origen
    check ((origen = 'api') = (registrado_por is not null))
);

create table nucleo.coeficiente_juridico (
  consorcio_id  uuid not null,
  esquema_id    uuid not null,
  unidad_id     uuid not null,
  porcentaje    numeric(9, 6) not null,
  primary key (esquema_id, unidad_id),
  foreign key (consorcio_id, esquema_id)
    references nucleo.esquema_coeficientes (consorcio_id, id),
  foreign key (consorcio_id, unidad_id) references nucleo.unidad (consorcio_id, id),
  constraint coeficiente_entre_0_y_100 check (porcentaje >= 0 and porcentaje <= 100)
);

create table nucleo.regla_reparto (
  id            uuid primary key default plataforma.uuid_v7(),
  consorcio_id  uuid not null,
  esquema_id    uuid not null,
  version       integer not null,
  metodo        nucleo.metodo_reparto not null,
  centavos      nucleo.tratamiento_centavos not null,
  decision_id   uuid not null,
  creado_en     timestamptz not null default now(),
  constraint regla_version_unica unique (esquema_id, version),
  constraint regla_version_positiva check (version >= 1),
  foreign key (consorcio_id, esquema_id)
    references nucleo.esquema_coeficientes (consorcio_id, id),
  foreign key (consorcio_id, decision_id) references nucleo.decision_formal (consorcio_id, id)
);

alter table nucleo.esquema_coeficientes enable row level security;
alter table nucleo.esquema_coeficientes force row level security;
alter table nucleo.coeficiente_juridico enable row level security;
alter table nucleo.coeficiente_juridico force row level security;
alter table nucleo.regla_reparto enable row level security;
alter table nucleo.regla_reparto force row level security;
create policy por_consorcio on nucleo.esquema_coeficientes for select
  using (consorcio_id = plataforma.consorcio_actual());
create policy por_consorcio on nucleo.coeficiente_juridico for select
  using (consorcio_id = plataforma.consorcio_actual());
create policy por_consorcio on nucleo.regla_reparto for select
  using (consorcio_id = plataforma.consorcio_actual());

grant select on nucleo.esquema_coeficientes, nucleo.coeficiente_juridico, nucleo.regla_reparto
  to cem_app;

-- Un esquema nuevo (o la versión siguiente de un grupo) con sus coeficientes y su regla, en una
-- sola transacción. `coeficientes` es un objeto {"<unidad_id>": "<porcentaje>"}.
create function nucleo.registrar_esquema_coeficientes(
  grupo_nuevo text, fuente text, desde date, decision uuid, coeficientes jsonb,
  metodo_nuevo nucleo.metodo_reparto, centavos_nuevo nucleo.tratamiento_centavos,
  actor uuid, origen_pedido text)
  returns uuid
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    previa nucleo.esquema_coeficientes;
    nuevo uuid;
    malas text[];
    faltan text[];
    suma numeric;
    n integer;
  begin
    if c is null then
      raise exception 'sin consorcio en el ámbito' using errcode = '42501';
    end if;
    -- Proponer y aprobar por la API esperan 2b-6 (`coeficientes.aprobar` es económica).
    if origen_pedido is distinct from 'soporte' then
      raise exception 'por ahora los coeficientes los carga solo el equipo (soporte)'
        using errcode = 'P0001', hint = 'origen_invalido';
    end if;
    if plataforma.persona_actual() is not null or actor is not null then
      raise exception 'el origen soporte no admite persona en la sesión ni actor'
        using errcode = 'P0001', hint = 'origen_invalido';
    end if;
    if grupo_nuevo is null or grupo_nuevo !~ '^[a-z][a-z0-9_]{1,39}$' then
      raise exception 'el grupo de gasto no es válido (minúsculas, dígitos y guiones bajos)'
        using errcode = 'P0001', hint = 'grupo_invalido';
    end if;
    if fuente is null or length(btrim(fuente)) not between 1 and 300 then
      raise exception 'el esquema necesita la referencia de su fuente'
        using errcode = 'P0001', hint = 'fuente_requerida';
    end if;
    if desde is null then
      raise exception 'el esquema necesita la fecha desde la que rige'
        using errcode = 'P0001', hint = 'vigencia_requerida';
    end if;
    if decision is null or not exists (
         select 1 from nucleo.decision_formal d where d.consorcio_id = c and d.id = decision) then
      raise exception 'el esquema necesita una decisión de respaldo de este consorcio'
        using errcode = 'P0001', hint = 'decision_invalida';
    end if;
    if metodo_nuevo is null or centavos_nuevo is null then
      raise exception 'la regla de reparto necesita su método y el tratamiento de los centavos'
        using errcode = 'P0001', hint = 'regla_incompleta';
    end if;
    if coeficientes is null or jsonb_typeof(coeficientes) <> 'object'
       or coeficientes = '{}'::jsonb then
      raise exception 'los coeficientes van como un objeto {unidad: porcentaje}'
        using errcode = 'P0001', hint = 'coeficientes_invalidos';
    end if;

    -- Claves que no son una unidad activa de este consorcio.
    select array_agg(k order by k) into malas
      from jsonb_object_keys(coeficientes) k
     where k !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
        or not exists (select 1 from nucleo.unidad u
                        where u.consorcio_id = c and u.id = k::uuid and u.eliminado_en is null);
    if malas is not null then
      raise exception 'unidades que no son de este consorcio: %', array_to_string(malas, ', ')
        using errcode = 'P0001', hint = 'unidad_invalida';
    end if;
    -- Porcentajes como texto, de 0 a 100 y con hasta 6 decimales: así se guardan exactos.
    select array_agg(e.key order by e.key) into malas
      from jsonb_each(coeficientes) e
     where jsonb_typeof(e.value) <> 'string'
        or (e.value #>> '{}') !~ '^[0-9]{1,3}(\.[0-9]{1,6})?$'
        or (e.value #>> '{}')::numeric > 100;
    if malas is not null then
      raise exception 'porcentajes inválidos para: %', array_to_string(malas, ', ')
        using errcode = 'P0001', hint = 'porcentaje_invalido';
    end if;
    -- Toda unidad activa figura, aunque sea con 0 %.
    select array_agg(u.id::text order by u.designacion) into faltan
      from nucleo.unidad u
     where u.consorcio_id = c and u.eliminado_en is null and not coeficientes ? u.id::text;
    if faltan is not null then
      raise exception 'faltan los coeficientes de: %', array_to_string(faltan, ', ')
        using errcode = 'P0001', hint = 'coeficientes_incompletos';
    end if;
    select sum((e.value #>> '{}')::numeric), count(*) into suma, n from jsonb_each(coeficientes) e;
    if suma <= 0 then
      raise exception 'el esquema no tiene ningún porcentaje positivo'
        using errcode = 'P0001', hint = 'esquema_sin_pesos';
    end if;

    -- Serializa las versiones de un mismo grupo.
    perform pg_advisory_xact_lock(hashtextextended('esquema:' || c || ':' || grupo_nuevo, 0));
    select * into previa from nucleo.esquema_coeficientes x
     where x.consorcio_id = c and x.grupo = grupo_nuevo order by x.version desc limit 1;
    if previa.id is not null and desde <= previa.vigente_desde then
      raise exception 'la versión nueva tiene que regir después de la anterior (%)',
        previa.vigente_desde
        using errcode = 'P0001', hint = 'vigencia_no_posterior';
    end if;

    insert into nucleo.esquema_coeficientes
      (consorcio_id, grupo, version, fuente_referencia, vigente_desde, decision_id, origen,
       registrado_por)
    values (c, grupo_nuevo, coalesce(previa.version, 0) + 1, btrim(fuente), desde, decision,
            origen_pedido, actor)
    returning id into nuevo;
    insert into nucleo.coeficiente_juridico (consorcio_id, esquema_id, unidad_id, porcentaje)
    select c, nuevo, e.key::uuid, (e.value #>> '{}')::numeric from jsonb_each(coeficientes) e;
    insert into nucleo.regla_reparto
      (consorcio_id, esquema_id, version, metodo, centavos, decision_id)
    values (c, nuevo, 1, metodo_nuevo, centavos_nuevo, decision);

    -- Sin la referencia de la fuente (E31): queda en la fila.
    insert into auditoria.eventos
      (consorcio_id, actor_persona_id, origen, accion, entidad, entidad_id, despues,
       hash_previo, hash)
    values (c, actor, origen_pedido, 'consorcio.coeficientes.registrar', 'esquema_coeficientes',
            nuevo,
            jsonb_build_object('grupo', grupo_nuevo, 'version', coalesce(previa.version, 0) + 1,
                               'vigente_desde', desde, 'decision_id', decision,
                               'unidades', n, 'suma', suma::text, 'metodo', metodo_nuevo,
                               'centavos', centavos_nuevo),
            '', '');
    return nuevo;
  end
  $$;

revoke all on function nucleo.registrar_esquema_coeficientes(
  text, text, date, uuid, jsonb, nucleo.metodo_reparto, nucleo.tratamiento_centavos, uuid, text)
  from public;
grant execute on function nucleo.registrar_esquema_coeficientes(
  text, text, date, uuid, jsonb, nucleo.metodo_reparto, nucleo.tratamiento_centavos, uuid, text)
  to cem_app;
"""

DOWNGRADE = r"""
do $guarda$
begin
  if exists (select 1 from nucleo.esquema_coeficientes) then
    raise exception 'hay esquemas de coeficientes: bajar a 0020 los perdería'
      using errcode = 'P0001', hint = 'downgrade_con_datos';
  end if;
end
$guarda$;

drop function nucleo.registrar_esquema_coeficientes(
  text, text, date, uuid, jsonb, nucleo.metodo_reparto, nucleo.tratamiento_centavos, uuid, text);
drop table nucleo.regla_reparto;
drop table nucleo.coeficiente_juridico;
drop table nucleo.esquema_coeficientes;
drop type nucleo.tratamiento_centavos;
drop type nucleo.metodo_reparto;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
