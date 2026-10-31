"""Liquidación neutral: borrador por período, sus gastos y la base de la simulación (H3).

Mandato del propietario del 31-10-2026: la liquidación que no depende de las decisiones 4.2 y 5
(quién revisa, quién aprueba). Este corte prepara y simula; no revisa, no aprueba ni publica.

- `nucleo.liquidacion`: una por período (`periodo`, el primer día del mes) y versión. Nace en
  `borrador`; `publicada` queda en el modelo con su restricción de una sola versión efectiva por
  período (documento 13, PA04), pero ninguna función publica todavía. Un solo borrador por
  período a la vez.
- `nucleo.liquidacion_gasto`: cada gasto es una orden **pagada** (conciliada, si fue manual),
  con el importe congelado de su ejecución y el esquema de coeficientes de su grupo vigente el
  último día del período (RF05: el cierre referencia una versión del esquema). Una orden está en
  una sola liquidación a la vez.
- `nucleo.liquidacion_gasto_quitado`: quitar un gasto del borrador agrega una fila; nada se
  modifica ni se borra.

Las tres tablas solo se agregan y `cem_app` no las lee: todo pasa por funciones `security
definer` que exigen `liquidacion.preparar` con R10 (`exigir_acto_economico`), porque leen
importes y proveedores de órdenes pagadas, como cualquier lectura de egresos. La simulación del
reparto la hace la API con el motor de `app/liquidacion` sobre estos datos; no se guarda.

Downgrade: se niega con alguna liquidación (`downgrade_con_datos`); si no, quita todo.

Revisión: 0057
Anterior: 0056
"""

from alembic import op

revision = "0057"
down_revision = "0056"
branch_labels = None
depends_on = None

TABLAS = (
    "nucleo.liquidacion",
    "nucleo.liquidacion_gasto",
    "nucleo.liquidacion_gasto_quitado",
)
RUTAS = (
    "nucleo.preparar_liquidacion(date, uuid, text, uuid)",
    "nucleo.agregar_gasto_liquidacion(uuid, uuid, text, uuid, text, uuid)",
    "nucleo.quitar_gasto_liquidacion(uuid, uuid, uuid, text, uuid)",
    "nucleo.liquidaciones_lectura(uuid, uuid, text, uuid)",
    "nucleo.liquidacion_gastos_lectura(uuid, uuid, text, uuid)",
    "nucleo.ordenes_liquidables_lectura(uuid, text, uuid)",
    "nucleo.grupos_liquidables_lectura(uuid, uuid, text, uuid)",
)
INTERNAS = (
    "nucleo.auditar_liquidacion(uuid, text, uuid, uuid, jsonb)",
    "nucleo.negar_cambio_historia_liquidacion()",
)

UPGRADE = """
create table nucleo.liquidacion (
  consorcio_id       uuid not null references nucleo.consorcio (id),
  id                 uuid not null default plataforma.uuid_v7(),
  periodo            date not null check (periodo = date_trunc('month', periodo)::date),
  version            integer not null check (version >= 1),
  estado             text not null default 'borrador' check (estado in ('borrador', 'publicada')),
  preparada_por      uuid not null references nucleo.persona (id),
  identidad_preparo  uuid not null,
  creada_en          timestamptz not null default now(),
  primary key (id),
  unique (consorcio_id, id),
  unique (consorcio_id, periodo, version),
  foreign key (consorcio_id, identidad_preparo)
    references nucleo.identidad_humana (consorcio_id, id)
);
-- PA04: una sola versión efectiva por período. Y un solo borrador a la vez.
create unique index liquidacion_publicada_unica on nucleo.liquidacion (consorcio_id, periodo)
  where estado = 'publicada';
create unique index liquidacion_borrador_unico on nucleo.liquidacion (consorcio_id, periodo)
  where estado = 'borrador';

create table nucleo.liquidacion_gasto (
  consorcio_id      uuid not null,
  id                uuid not null default plataforma.uuid_v7(),
  liquidacion_id    uuid not null,
  orden_id          uuid not null,
  ejecucion_id      uuid not null,
  importe_centavos  bigint not null check (importe_centavos > 0),
  moneda            text not null check (moneda ~ '^[A-Z]{3}$'),
  esquema_id        uuid not null,
  agregado_por      uuid not null references nucleo.persona (id),
  identidad_agrego  uuid not null,
  agregado_en       timestamptz not null default now(),
  primary key (id),
  unique (consorcio_id, id),
  foreign key (consorcio_id, liquidacion_id) references nucleo.liquidacion (consorcio_id, id),
  foreign key (consorcio_id, orden_id) references nucleo.orden_pago (consorcio_id, id),
  foreign key (consorcio_id, ejecucion_id)
    references nucleo.ejecucion_orden_pago (consorcio_id, id),
  foreign key (consorcio_id, esquema_id)
    references nucleo.esquema_coeficientes (consorcio_id, id),
  foreign key (consorcio_id, identidad_agrego)
    references nucleo.identidad_humana (consorcio_id, id)
);
create index liquidacion_gasto_orden on nucleo.liquidacion_gasto (consorcio_id, orden_id);

create table nucleo.liquidacion_gasto_quitado (
  consorcio_id      uuid not null,
  gasto_id          uuid not null,
  quitado_por       uuid not null references nucleo.persona (id),
  identidad_quito   uuid not null,
  quitado_en        timestamptz not null default now(),
  primary key (gasto_id),
  foreign key (consorcio_id, gasto_id) references nucleo.liquidacion_gasto (consorcio_id, id),
  foreign key (consorcio_id, identidad_quito)
    references nucleo.identidad_humana (consorcio_id, id)
);

create function nucleo.negar_cambio_historia_liquidacion()
  returns trigger
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  begin
    raise exception 'la historia de una liquidación solo se agrega'
      using errcode = 'P0001', hint = 'historia_inmutable';
  end
  $$;
"""

for _t in TABLAS:
    _n = _t.split(".")[1]
    UPGRADE += f"""
alter table {_t} enable row level security;
alter table {_t} force row level security;
create policy por_consorcio on {_t} for select
  using (consorcio_id = plataforma.consorcio_actual());
revoke all on {_t} from public, cem_app;
create trigger historia_{_n} before update or delete on {_t}
  for each row execute function nucleo.negar_cambio_historia_liquidacion();
create trigger historia_{_n}_truncate before truncate on {_t}
  for each statement execute function nucleo.negar_cambio_historia_liquidacion();
"""

UPGRADE += """
create function nucleo.auditar_liquidacion(c uuid, accion text, liquidacion uuid, actor uuid,
                                           despues jsonb)
  returns void
  language sql volatile
  set search_path = pg_catalog, pg_temp
  as $$
    insert into auditoria.eventos
      (consorcio_id, actor_persona_id, origen, accion, entidad, entidad_id, antes, despues,
       hash_previo, hash)
    values (c, actor, 'api', accion, 'liquidacion', liquidacion, null, despues, '', '')
  $$;

-- El borrador de un período, versión siguiente. Uno solo a la vez por período.
create function nucleo.preparar_liquidacion(periodo_pedido date, actor uuid, origen_pedido text,
                                            sesion uuid)
  returns uuid
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    identidad uuid;
    siguiente integer;
    nueva uuid;
  begin
    identidad := nucleo.exigir_acto_economico(c, actor, sesion, 'liquidacion.preparar',
                                              origen_pedido);
    if periodo_pedido is null or periodo_pedido <> date_trunc('month', periodo_pedido)::date then
      raise exception 'el período es el primer día de un mes' using errcode = 'P0001',
        hint = 'periodo_invalido';
    end if;
    perform pg_advisory_xact_lock(
      hashtextextended('liquidacion:' || c::text || ':' || periodo_pedido::text, 0));
    if exists (select 1 from nucleo.liquidacion l
                where l.consorcio_id = c and l.periodo = periodo_pedido
                  and l.estado = 'borrador') then
      raise exception 'ya hay un borrador de ese período' using errcode = 'P0001',
        hint = 'liquidacion_en_borrador';
    end if;
    select coalesce(max(l.version), 0) + 1 into siguiente from nucleo.liquidacion l
     where l.consorcio_id = c and l.periodo = periodo_pedido;
    insert into nucleo.liquidacion (consorcio_id, periodo, version, preparada_por,
                                    identidad_preparo)
    values (c, periodo_pedido, siguiente, actor, identidad)
    returning id into nueva;
    perform nucleo.auditar_liquidacion(c, 'consorcio.liquidacion.preparar', nueva, actor,
      jsonb_build_object('periodo', periodo_pedido, 'version', siguiente));
    return nueva;
  end
  $$;

-- Una orden pagada al borrador, con el importe de su ejecución y el esquema de su grupo vigente
-- el último día del período. La orden se bloquea: dos borradores no la toman a la vez.
create function nucleo.agregar_gasto_liquidacion(liquidacion uuid, orden uuid, grupo_pedido text,
                                                 actor uuid, origen_pedido text, sesion uuid)
  returns uuid
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    identidad uuid;
    l nucleo.liquidacion;
    o nucleo.orden_pago;
    e nucleo.ejecucion_orden_pago;
    esquema uuid;
    nuevo uuid;
  begin
    identidad := nucleo.exigir_acto_economico(c, actor, sesion, 'liquidacion.preparar',
                                              origen_pedido);
    select * into l from nucleo.liquidacion x
     where x.consorcio_id = c and x.id = liquidacion for update;
    if not found then
      raise exception 'la liquidación no existe' using errcode = 'P0001', hint = 'no_encontrado';
    end if;
    if l.estado <> 'borrador' then
      raise exception 'la liquidación no es un borrador' using errcode = 'P0001',
        hint = 'liquidacion_no_admite';
    end if;
    select * into o from nucleo.orden_pago x where x.consorcio_id = c and x.id = orden for update;
    if not found then
      raise exception 'la orden no existe' using errcode = 'P0001', hint = 'no_encontrado';
    end if;
    if o.estado <> 'pagada' then
      raise exception 'solo se liquida una orden pagada' using errcode = 'P0001',
        hint = 'orden_no_pagada';
    end if;
    select * into e from nucleo.ejecucion_orden_pago x
     where x.consorcio_id = c and x.orden_id = o.id and x.version = o.version_vigente
       and exists (select 1 from nucleo.resultado_ejecucion_orden_pago r
                    where r.ejecucion_id = x.id and r.resultado = 'pagada');
    if not found then
      raise exception 'la orden pagada no tiene su ejecución pagada' using errcode = 'P0001',
        hint = 'orden_no_pagada';
    end if;
    if exists (select 1 from nucleo.liquidacion_gasto g
                where g.consorcio_id = c and g.orden_id = o.id
                  and not exists (select 1 from nucleo.liquidacion_gasto_quitado q
                                   where q.gasto_id = g.id)) then
      raise exception 'la orden ya está en una liquidación' using errcode = 'P0001',
        hint = 'gasto_ya_liquidado';
    end if;
    if exists (select 1 from nucleo.liquidacion_gasto g
                where g.consorcio_id = c and g.liquidacion_id = l.id and g.moneda <> e.moneda
                  and not exists (select 1 from nucleo.liquidacion_gasto_quitado q
                                   where q.gasto_id = g.id)) then
      raise exception 'la liquidación ya tiene gastos en otra moneda' using errcode = 'P0001',
        hint = 'moneda_distinta';
    end if;
    select s.id into esquema from nucleo.esquema_coeficientes s
     where s.consorcio_id = c and s.grupo = grupo_pedido
       and s.vigente_desde <= (l.periodo + interval '1 month' - interval '1 day')::date
     order by s.vigente_desde desc
     limit 1;
    if esquema is null then
      raise exception 'el grupo no tiene un esquema vigente en el período' using errcode = 'P0001',
        hint = 'sin_esquema';
    end if;
    insert into nucleo.liquidacion_gasto (consorcio_id, liquidacion_id, orden_id, ejecucion_id,
                                          importe_centavos, moneda, esquema_id, agregado_por,
                                          identidad_agrego)
    values (c, l.id, o.id, e.id, e.importe_centavos, e.moneda, esquema, actor, identidad)
    returning id into nuevo;
    perform nucleo.auditar_liquidacion(c, 'consorcio.liquidacion.agregar_gasto', l.id, actor,
      jsonb_build_object('gasto', nuevo, 'orden', o.id, 'esquema', esquema,
                         'importe_centavos', e.importe_centavos, 'moneda', e.moneda));
    return nuevo;
  end
  $$;

create function nucleo.quitar_gasto_liquidacion(liquidacion uuid, gasto uuid, actor uuid,
                                                origen_pedido text, sesion uuid)
  returns void
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    identidad uuid;
    g nucleo.liquidacion_gasto;
    l nucleo.liquidacion;
  begin
    identidad := nucleo.exigir_acto_economico(c, actor, sesion, 'liquidacion.preparar',
                                              origen_pedido);
    select * into g from nucleo.liquidacion_gasto x
     where x.consorcio_id = c and x.id = gasto and x.liquidacion_id = liquidacion;
    if not found then
      raise exception 'el gasto no existe' using errcode = 'P0001', hint = 'no_encontrado';
    end if;
    select * into l from nucleo.liquidacion x
     where x.consorcio_id = c and x.id = g.liquidacion_id for update;
    if l.estado <> 'borrador' then
      raise exception 'la liquidación no es un borrador' using errcode = 'P0001',
        hint = 'liquidacion_no_admite';
    end if;
    if exists (select 1 from nucleo.liquidacion_gasto_quitado q where q.gasto_id = g.id) then
      raise exception 'el gasto ya se quitó' using errcode = 'P0001',
        hint = 'liquidacion_no_admite';
    end if;
    insert into nucleo.liquidacion_gasto_quitado (consorcio_id, gasto_id, quitado_por,
                                                  identidad_quito)
    values (c, g.id, actor, identidad);
    perform nucleo.auditar_liquidacion(c, 'consorcio.liquidacion.quitar_gasto', l.id, actor,
      jsonb_build_object('gasto', g.id, 'orden', g.orden_id));
  end
  $$;

create function nucleo.liquidaciones_lectura(liquidacion uuid, actor uuid, origen_pedido text,
                                             sesion uuid)
  returns table (id uuid, periodo date, version integer, estado text, preparada_por uuid,
                 creada_en timestamptz)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
  begin
    perform nucleo.exigir_acto_economico(c, actor, sesion, 'liquidacion.preparar', origen_pedido);
    return query
      select l.id, l.periodo, l.version, l.estado, l.preparada_por, l.creada_en
        from nucleo.liquidacion l
       where l.consorcio_id = c and (liquidacion is null or l.id = liquidacion)
       order by l.periodo desc, l.version desc;
  end
  $$;

-- Los gastos vigentes del borrador (los quitados no), con lo que la pantalla muestra de cada
-- orden y el esquema exacto que se usa para repartirlo.
create function nucleo.liquidacion_gastos_lectura(liquidacion uuid, actor uuid,
                                                  origen_pedido text, sesion uuid)
  returns table (id uuid, orden_id uuid, concepto text, razon_social text,
                 importe_centavos bigint, moneda text, pagada_en timestamptz, grupo text,
                 esquema_id uuid, esquema_version integer, agregado_por uuid,
                 agregado_en timestamptz)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
  begin
    perform nucleo.exigir_acto_economico(c, actor, sesion, 'liquidacion.preparar', origen_pedido);
    return query
      select g.id, g.orden_id, v.concepto, pv.razon_social, g.importe_centavos, g.moneda,
             r.registrado_en, s.grupo, s.id, s.version, g.agregado_por, g.agregado_en
        from nucleo.liquidacion_gasto g
        join nucleo.ejecucion_orden_pago e on e.consorcio_id = c and e.id = g.ejecucion_id
        join nucleo.orden_pago_version v on v.orden_id = e.orden_id and v.numero = e.version
        join nucleo.proveedor_version pv
          on pv.proveedor_id = e.proveedor_id and pv.numero = e.proveedor_version
        join nucleo.resultado_ejecucion_orden_pago r
          on r.ejecucion_id = e.id and r.resultado = 'pagada'
        join nucleo.esquema_coeficientes s on s.consorcio_id = c and s.id = g.esquema_id
       where g.consorcio_id = c and g.liquidacion_id = liquidacion
         and not exists (select 1 from nucleo.liquidacion_gasto_quitado q where q.gasto_id = g.id)
       order by r.registrado_en, g.id;
  end
  $$;

-- Las órdenes pagadas que todavía no están en ninguna liquidación.
create function nucleo.ordenes_liquidables_lectura(actor uuid, origen_pedido text, sesion uuid)
  returns table (orden_id uuid, concepto text, razon_social text, importe_centavos bigint,
                 moneda text, pagada_en timestamptz)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
  begin
    perform nucleo.exigir_acto_economico(c, actor, sesion, 'liquidacion.preparar', origen_pedido);
    return query
      select o.id, v.concepto, pv.razon_social, e.importe_centavos, e.moneda, r.registrado_en
        from nucleo.orden_pago o
        join nucleo.ejecucion_orden_pago e
          on e.consorcio_id = c and e.orden_id = o.id and e.version = o.version_vigente
        join nucleo.resultado_ejecucion_orden_pago r
          on r.ejecucion_id = e.id and r.resultado = 'pagada'
        join nucleo.orden_pago_version v on v.orden_id = e.orden_id and v.numero = e.version
        join nucleo.proveedor_version pv
          on pv.proveedor_id = e.proveedor_id and pv.numero = e.proveedor_version
       where o.consorcio_id = c and o.estado = 'pagada'
         and not exists (select 1 from nucleo.liquidacion_gasto g
                          where g.consorcio_id = c and g.orden_id = o.id
                            and not exists (select 1 from nucleo.liquidacion_gasto_quitado q
                                             where q.gasto_id = g.id))
       order by r.registrado_en, o.id;
  end
  $$;

-- Los grupos con un esquema vigente el último día del período de la liquidación.
create function nucleo.grupos_liquidables_lectura(liquidacion uuid, actor uuid,
                                                  origen_pedido text, sesion uuid)
  returns table (grupo text, esquema_id uuid, esquema_version integer, vigente_desde date)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    hasta date;
  begin
    perform nucleo.exigir_acto_economico(c, actor, sesion, 'liquidacion.preparar', origen_pedido);
    select (l.periodo + interval '1 month' - interval '1 day')::date into hasta
      from nucleo.liquidacion l where l.consorcio_id = c and l.id = liquidacion;
    if hasta is null then
      raise exception 'la liquidación no existe' using errcode = 'P0001', hint = 'no_encontrado';
    end if;
    return query
      select distinct on (s.grupo) s.grupo, s.id, s.version, s.vigente_desde
        from nucleo.esquema_coeficientes s
       where s.consorcio_id = c and s.vigente_desde <= hasta
       order by s.grupo, s.vigente_desde desc;
  end
  $$;
"""

UPGRADE += "".join(f"revoke all on function {f} from public;\n" for f in RUTAS + INTERNAS)
UPGRADE += "".join(f"grant execute on function {f} to cem_app;\n" for f in RUTAS)

DOWNGRADE = (
    """
lock table nucleo.liquidacion in share row exclusive mode;

do $$
begin
  if exists (select 1 from nucleo.liquidacion) then
    raise exception '0057 no baja con liquidaciones: se perdería su historia'
      using errcode = 'P0001', hint = 'downgrade_con_datos';
  end if;
end
$$;
"""
    + "".join(f"drop function {f};\n" for f in RUTAS)
    + "".join(f"drop table {t};\n" for t in reversed(TABLAS))
    + "".join(f"drop function {f};\n" for f in INTERNAS)
)


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
