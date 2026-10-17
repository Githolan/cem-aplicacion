"""Modo de operación del consorcio y su decisión formal de respaldo (H2b, corte 2b-1).

Documento 08: §5 (modos y transición), RF41, RN26 y PA31. Documento 13: §8.2 y §8.3
(vigencias con `EXCLUDE`, claves compuestas). Diseño: `doc/diseno/H2b_Mandatos_Padron_y_Permisos.md`
del repositorio CEM, §4 y §7 (2b-1).

- `nucleo.decision_formal`: registro **mínimo** de que existe una decisión con su acta (tipo,
  fecha, descripción y referencia del acta como texto). No es el circuito de asambleas
  (H4b/H9): solo deja el respaldo que exigen RN03 y RN26. No se edita ni se borra.
- `nucleo.modo_operacion`: historial del modo con vigencia; uno solo vigente por consorcio
  (`EXCLUDE`) y cada tramo apunta a la decisión que lo respalda (clave compuesta: no puede
  apuntar a una decisión de otro consorcio).

`cem_app` solo **lee** las dos tablas. Las escriben `nucleo.registrar_decision` y
`nucleo.cambiar_modo`, `security definer`, que toman el consorcio del ámbito (nunca de un
parámetro), validan, escriben la auditoría encadenada y, en el caso del modo, cierran el tramo
anterior en la misma transacción. `cambiar_modo` exige una decisión de tipo `asamblea` del
mismo consorcio (RN26): sin ella el modo no cambia y el anterior sigue vigente (PA31). Quién
puede llamarlas lo deciden las políticas del corte 2b-3; ninguna ruta las usa todavía.

Las fechas de vigencia son las del consorcio (`nucleo.hoy_del_consorcio`), no las de la base,
que corre en GMT.

Revisión: 0010
Anterior: 0009
"""

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

UPGRADE = r"""
create type nucleo.tipo_modo as enum ('consejo', 'administrador_aumentado', 'autogestion');
create type nucleo.tipo_decision as enum
  ('asamblea', 'consejo', 'contrato_administracion', 'inscripcion_rpa');

-- La fecha de hoy en la zona horaria del consorcio. La base corre en GMT: entre las 21 y las
-- 24 de Buenos Aires `current_date` ya es mañana.
create function nucleo.hoy_del_consorcio(consorcio uuid) returns date
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select (now() at time zone c.zona_horaria)::date from nucleo.consorcio c where c.id = consorcio
  $$;

create table nucleo.decision_formal (
  consorcio_id     uuid not null references nucleo.consorcio (id),
  id               uuid not null default plataforma.uuid_v7(),
  tipo             nucleo.tipo_decision not null,
  fecha            date not null,
  descripcion      text not null check (length(btrim(descripcion)) between 1 and 500),
  -- Número de acta, libro y folio, o la referencia que permita encontrarla. El documento
  -- llega con el almacenamiento (2c).
  referencia_acta  text not null check (length(btrim(referencia_acta)) between 1 and 300),
  -- Nula cuando la registra el equipo de la plataforma (origen `soporte`).
  registrada_por   uuid references nucleo.persona (id),
  origen           text not null check (origen in ('api', 'soporte')),
  creada_en        timestamptz not null default now(),
  primary key (id),
  unique (consorcio_id, id),
  check ((origen = 'api') = (registrada_por is not null))
);

create table nucleo.modo_operacion (
  consorcio_id   uuid not null references nucleo.consorcio (id),
  id             uuid primary key default plataforma.uuid_v7(),
  modo           nucleo.tipo_modo not null,
  vigente_desde  date not null,
  vigente_hasta  date,
  decision_id    uuid not null,
  creado_en      timestamptz not null default now(),
  foreign key (consorcio_id, decision_id) references nucleo.decision_formal (consorcio_id, id),
  check (vigente_hasta is null or vigente_hasta > vigente_desde),
  exclude using gist (consorcio_id with =, daterange(vigente_desde, vigente_hasta) with &&)
);
create index modo_operacion_consorcio on nucleo.modo_operacion (consorcio_id, vigente_desde desc);

alter table nucleo.decision_formal enable row level security;
alter table nucleo.decision_formal force row level security;
alter table nucleo.modo_operacion enable row level security;
alter table nucleo.modo_operacion force row level security;

create policy por_consorcio on nucleo.decision_formal for select
  using (consorcio_id = plataforma.consorcio_actual());
create policy por_consorcio on nucleo.modo_operacion for select
  using (consorcio_id = plataforma.consorcio_actual());
-- El selector de edificios muestra el modo de cada consorcio de la persona.
create policy por_consorcios_persona on nucleo.modo_operacion for select
  using (consorcio_id = any (plataforma.consorcios_persona()));

create function nucleo.registrar_decision(
  tipo_decision nucleo.tipo_decision, fecha_decision date, descripcion_decision text,
  referencia text, actor uuid, origen_pedido text)
  returns uuid
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    d uuid;
  begin
    if c is null then
      raise exception 'sin consorcio en el ámbito' using errcode = '42501';
    end if;
    insert into nucleo.decision_formal
      (consorcio_id, tipo, fecha, descripcion, referencia_acta, registrada_por, origen)
    values (c, tipo_decision, fecha_decision, btrim(descripcion_decision), btrim(referencia),
            actor, origen_pedido)
    returning id into d;
    insert into auditoria.eventos
      (consorcio_id, actor_persona_id, origen, accion, entidad, entidad_id, despues,
       hash_previo, hash)
    values (c, actor, origen_pedido, 'consorcio.decision.registrar', 'decision_formal', d,
            jsonb_build_object('tipo', tipo_decision, 'fecha', fecha_decision,
                               'referencia_acta', btrim(referencia)),
            '', '');
    return d;
  end
  $$;

create function nucleo.cambiar_modo(
  modo_nuevo nucleo.tipo_modo, decision uuid, desde date, actor uuid, origen_pedido text)
  returns uuid
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    tipo_respaldo nucleo.tipo_decision;
    anterior nucleo.modo_operacion;
    nuevo uuid;
  begin
    if c is null then
      raise exception 'sin consorcio en el ámbito' using errcode = '42501';
    end if;
    -- Serializa los cambios de modo del consorcio.
    perform 1 from nucleo.consorcio x where x.id = c for update;

    select d.tipo into tipo_respaldo
      from nucleo.decision_formal d where d.consorcio_id = c and d.id = decision;
    -- RN26: solo una decisión de asamblea formalizada y registrada de este consorcio.
    if tipo_respaldo is null or tipo_respaldo <> 'asamblea' then
      raise exception 'el modo solo cambia con una decisión de asamblea registrada'
        using errcode = 'P0001', hint = 'decision_invalida';
    end if;

    select * into anterior from nucleo.modo_operacion m
     where m.consorcio_id = c and m.vigente_hasta is null;
    if anterior.id is not null then
      if anterior.modo = modo_nuevo then
        raise exception 'el consorcio ya está en ese modo'
          using errcode = 'P0001', hint = 'mismo_modo';
      end if;
      if desde <= anterior.vigente_desde then
        raise exception 'el modo nuevo tiene que empezar después del vigente'
          using errcode = 'P0001', hint = 'fecha_invalida';
      end if;
      update nucleo.modo_operacion set vigente_hasta = desde where id = anterior.id;
    end if;

    insert into nucleo.modo_operacion (consorcio_id, modo, vigente_desde, decision_id)
    values (c, modo_nuevo, desde, decision)
    returning id into nuevo;

    insert into auditoria.eventos
      (consorcio_id, actor_persona_id, origen, accion, entidad, entidad_id, antes, despues,
       hash_previo, hash)
    values (c, actor, origen_pedido, 'consorcio.modo.cambiar', 'modo_operacion', nuevo,
            case when anterior.id is null then null
                 else jsonb_build_object('modo', anterior.modo, 'id', anterior.id) end,
            jsonb_build_object('modo', modo_nuevo, 'desde', desde, 'decision_id', decision),
            '', '');
    return nuevo;
  end
  $$;

-- Privilegios (§8.6) ------------------------------------------------------------------

grant select on nucleo.decision_formal, nucleo.modo_operacion to cem_app;
revoke all on function
  nucleo.hoy_del_consorcio(uuid),
  nucleo.registrar_decision(nucleo.tipo_decision, date, text, text, uuid, text),
  nucleo.cambiar_modo(nucleo.tipo_modo, uuid, date, uuid, text)
  from public;
grant execute on function
  nucleo.hoy_del_consorcio(uuid),
  nucleo.registrar_decision(nucleo.tipo_decision, date, text, text, uuid, text),
  nucleo.cambiar_modo(nucleo.tipo_modo, uuid, date, uuid, text)
  to cem_app;
"""

DOWNGRADE = r"""
drop function nucleo.cambiar_modo(nucleo.tipo_modo, uuid, date, uuid, text);
drop function nucleo.registrar_decision(nucleo.tipo_decision, date, text, text, uuid, text);
drop table nucleo.modo_operacion;
drop table nucleo.decision_formal;
drop function nucleo.hoy_del_consorcio(uuid);
drop type nucleo.tipo_decision;
drop type nucleo.tipo_modo;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
