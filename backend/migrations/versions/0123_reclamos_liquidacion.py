"""Reclamos sobre la liquidación oficial (RF34, Modo Consejo; diseño P0 S7), con el borrador sin
conexión de la pantalla (RF38, PA16), que el servidor revalida entero al recibirlo.

**Quién presenta** (decisión del 22-11-2026): el propietario con el vínculo confirmado, vigente y
no importado pendiente (`exigir_propietario_confirmado`, `0119`), y solo sobre una unidad suya.
Inquilinos y ocupantes, no en este corte. **Quién atiende:** la facultad nueva `reclamos.atender`
(no económica), en los paquetes `miembro_consejo` y `administrador`; como en `0036`, `0113` y
`0122`, los mandatos ya registrados no la reciben.

**Qué cita un reclamo:** una liquidación oficial (la vigente o una reemplazada de un período), una
unidad propia y, si quiere, uno de los datos oficiales de esa unidad (`saldo_unidad` o
`importe_unidad`, que tiene que figurar en esa liquidación para esa unidad), y un texto. Un texto
con el número de una cuenta (definición de `0060`) se **rechaza** (`texto_con_cuenta`). Un reclamo
**nunca cambia un importe** (RF34: «sin alterar el saldo por sí solo»): ninguna función de acá
escribe en las tablas de la importación.

**El borrador (RF38, PA16).** La pantalla lo guarda en el teléfono y lo manda al volver la
conexión. La función revalida todo: el vínculo, la unidad, la liquidación y el dato citado. Si la
liquidación citada fue reemplazada después, se niega (`liquidacion_reemplazada`) salvo que el
pedido diga que reclama sobre la reemplazada (`acepta_reemplazada`).

**Estados** (`nucleo.reclamo_liquidacion_estado`, una fila por paso, sin cambios ni borrado):
`presentado`, y después uno solo de `respondido` (con la respuesta), `derivado_a_solicitud` (con
la solicitud formal de `0108` que lo atiende) o `retirado` (por quien lo presentó, con el motivo).
El índice único parcial `reclamo_un_cierre` es la última barrera frente a dos cierres a la vez.

**Lectura** (`reclamos_liquidacion_lectura`): quien atiende ve todos los del consorcio; el
propietario, los que presentó.

**Correos:** la ruta publica, en la misma transacción, uno a cada persona con `reclamos.atender` al
presentarse, y uno a quien lo presentó al responderse. Solo el título y el enlace.

**Exportación** (`0069`): la parte nueva `reclamos_liquidacion`, con los nombres y nunca los
identificadores de las personas, y los textos por `texto_sin_cuenta`.

Downgrade: se niega (`downgrade_con_datos`) si hay reclamos o algún mandato con la facultad.

Datos de prueba: SINTETICOS / NO REAL.

Revisión: 0123
Anterior: 0122
"""

import importlib.util
from pathlib import Path

from alembic import op

revision = "0123"
down_revision = "0122"
branch_labels = None
depends_on = None

FACULTAD = "reclamos.atender"
ACCION_PRESENTAR = "consorcio.reclamo_liquidacion.presentar"
ACCION_CERRAR = "consorcio.reclamo_liquidacion.cerrar"
LARGO_TEXTO = 2000
LARGO_MOTIVO = 500
"""Los de `cem_contracts.reclamos`; una prueba compara."""
CAMPOS_CITABLES = ("saldo_unidad", "importe_unidad")
ESTADOS = ("presentado", "respondido", "derivado_a_solicitud", "retirado")
TABLAS = ("nucleo.reclamo_liquidacion", "nucleo.reclamo_liquidacion_estado")
EXPORTAR = "exportar_reclamos_liquidacion"


def _lista(valores) -> str:
    return ", ".join(f"'{v}'" for v in valores)


def _largo(columna: str, maximo: int) -> str:
    return f"length(btrim({columna})) between 1 and {maximo} and {columna} = btrim({columna})"


TABLAS_SQL = f"""
create function nucleo.negar_cambio_reclamo()
  returns trigger
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  begin
    raise exception 'un reclamo no se cambia ni se borra: cada paso es una fila nueva'
      using errcode = 'P0001', hint = 'historia_inmutable';
  end
  $$;
revoke all on function nucleo.negar_cambio_reclamo() from public;

-- Ni el texto del reclamo ni la respuesta ni el motivo llevan una cuenta (`0060`): se rechaza.
create function nucleo.negar_cuenta_en_reclamo()
  returns trigger
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  begin
    if nucleo.texto_con_cuenta(new.texto) then
      raise exception 'un reclamo no puede llevar una cuenta' using errcode = 'P0001',
        hint = 'texto_con_cuenta';
    end if;
    return new;
  end
  $$;
revoke all on function nucleo.negar_cuenta_en_reclamo() from public;

create table nucleo.reclamo_liquidacion (
  consorcio_id    uuid not null references nucleo.consorcio (id),
  id              uuid primary key default plataforma.uuid_v7(),
  importacion_id  uuid not null,
  unidad_id       uuid not null references nucleo.unidad (id),
  campo           text check (campo in ({_lista(CAMPOS_CITABLES)})),
  texto           text not null check ({_largo("texto", LARGO_TEXTO)}),
  presentado_por  uuid not null references nucleo.persona (id),
  presentado_en   timestamptz not null default clock_timestamp(),
  unique (consorcio_id, id),
  foreign key (consorcio_id, importacion_id)
    references nucleo.importacion_liquidacion (consorcio_id, id)
);
create index reclamo_liquidacion_presentado_por
  on nucleo.reclamo_liquidacion (consorcio_id, presentado_por);
create trigger reclamo_liquidacion_sin_cuenta before insert on nucleo.reclamo_liquidacion
  for each row execute function nucleo.negar_cuenta_en_reclamo();

create table nucleo.reclamo_liquidacion_estado (
  consorcio_id    uuid not null,
  reclamo_id      uuid not null,
  estado          text not null check (estado in ({_lista(ESTADOS)})),
  texto           text check ({_largo("texto", LARGO_TEXTO)}),
  solicitud_id    uuid,
  registrado_por  uuid not null references nucleo.persona (id),
  registrado_en   timestamptz not null default clock_timestamp(),
  primary key (reclamo_id, estado),
  foreign key (consorcio_id, reclamo_id)
    references nucleo.reclamo_liquidacion (consorcio_id, id),
  foreign key (consorcio_id, solicitud_id) references nucleo.solicitud_formal (consorcio_id, id),
  constraint reclamo_estado_con_texto
    check ((estado in ('respondido', 'retirado')) = (texto is not null)),
  constraint reclamo_motivo_corto
    check (estado <> 'retirado' or length(texto) <= {LARGO_MOTIVO}),
  constraint reclamo_estado_con_solicitud
    check ((estado = 'derivado_a_solicitud') = (solicitud_id is not null))
);
-- La última barrera: un solo cierre por reclamo, y una solicitud atiende a un solo reclamo.
create unique index reclamo_un_cierre
  on nucleo.reclamo_liquidacion_estado (reclamo_id) where estado <> 'presentado';
create unique index reclamo_una_solicitud
  on nucleo.reclamo_liquidacion_estado (solicitud_id) where solicitud_id is not null;
create trigger reclamo_liquidacion_estado_sin_cuenta
  before insert on nucleo.reclamo_liquidacion_estado
  for each row execute function nucleo.negar_cuenta_en_reclamo();
"""


def _protecciones(tabla: str) -> str:
    corto = tabla.removeprefix("nucleo.")
    return f"""
alter table {tabla} enable row level security;
alter table {tabla} force row level security;
create policy por_consorcio on {tabla} for select
  using (consorcio_id = plataforma.consorcio_actual());
revoke all on {tabla} from public, cem_app;
create trigger historia_{corto} before update or delete on {tabla}
  for each row execute function nucleo.negar_cambio_reclamo();
create trigger historia_{corto}_truncate before truncate on {tabla}
  for each statement execute function nucleo.negar_cambio_reclamo();
"""


FUNCIONES = f"""
-- Quien pide es la persona de la sesión, con una sesión válida, por la API y con un modo
-- registrado (los tres admiten los reclamos). Devuelve el día del consorcio.
create function nucleo.exigir_sesion_reclamos(c uuid, actor uuid, sesion uuid, origen_pedido text)
  returns date
  language plpgsql volatile
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    hoy date;
  begin
    if c is null then
      raise exception 'sin consorcio en el ámbito' using errcode = '42501';
    end if;
    if origen_pedido is distinct from 'api' then
      raise exception 'origen inválido' using errcode = 'P0001', hint = 'origen_invalido';
    end if;
    if actor is null or actor is distinct from plataforma.persona_actual() then
      raise exception 'la persona no es la de la petición' using errcode = '42501';
    end if;
    hoy := nucleo.hoy_del_consorcio(c);
    -- El candado del modo de `0090`: un cambio de modo en curso termina antes de leerlo.
    perform pg_advisory_xact_lock_shared(hashtextextended('modo:' || c::text, 0));
    if not exists (
         select 1 from nucleo.modo_operacion m
          where m.consorcio_id = c and m.vigente_desde <= hoy
            and (m.vigente_hasta is null or m.vigente_hasta > hoy)) then
      raise exception 'sin modo registrado' using errcode = 'P0001', hint = 'modo_no_admitido';
    end if;
    if not exists (select 1 from nucleo.sesion x
                    where x.id = sesion and x.persona_id = actor and x.revocada_en is null
                      and x.vence_en > now()) then
      raise exception 'la sesión no es válida' using errcode = 'P0001', hint = 'sesion_invalida';
    end if;
    return hoy;
  end
  $$;

-- Quien presenta o retira: lo anterior y un vínculo de propietario confirmado, vigente y no
-- importado pendiente sobre esa unidad (`exigir_propietario_confirmado`, `0119`).
create function nucleo.exigir_reclamo_propio(c uuid, actor uuid, sesion uuid, origen_pedido text,
                                             unidad uuid)
  returns void
  language plpgsql volatile
  set search_path = pg_catalog, pg_temp
  as $$
  begin
    perform nucleo.exigir_sesion_reclamos(c, actor, sesion, origen_pedido);
    if unidad is null or not unidad = any(nucleo.exigir_propietario_confirmado(c, actor)) then
      raise exception 'sin un vínculo de propietario confirmado sobre esa unidad'
        using errcode = '42501';
    end if;
  end
  $$;

-- Quien atiende: lo anterior y `{FACULTAD}` vigente.
create function nucleo.exigir_atender_reclamos(c uuid, actor uuid, sesion uuid,
                                               origen_pedido text)
  returns void
  language plpgsql volatile
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    hoy date;
  begin
    hoy := nucleo.exigir_sesion_reclamos(c, actor, sesion, origen_pedido);
    if not nucleo.tiene_facultad_vigente(c, actor, '{FACULTAD}', hoy) then
      raise exception 'sin {FACULTAD} en este consorcio' using errcode = '42501';
    end if;
  end
  $$;

-- El reclamo del consorcio, trabado hasta el final de la transacción, y si sigue `presentado`.
create function nucleo.reclamo_abierto(c uuid, reclamo uuid)
  returns nucleo.reclamo_liquidacion
  language plpgsql volatile
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    r nucleo.reclamo_liquidacion;
  begin
    select * into r from nucleo.reclamo_liquidacion x
     where x.consorcio_id = c and x.id = reclamo
       for update;
    if not found then
      raise exception 'el reclamo no existe en este consorcio' using errcode = 'P0001',
        hint = 'no_encontrado';
    end if;
    if exists (select 1 from nucleo.reclamo_liquidacion_estado e
                where e.consorcio_id = c and e.reclamo_id = reclamo
                  and e.estado <> 'presentado') then
      raise exception 'el reclamo ya se cerró' using errcode = 'P0001', hint = 'reclamo_cerrado';
    end if;
    return r;
  end
  $$;

create function nucleo.auditar_reclamo(c uuid, actor uuid, origen_pedido text, accion text,
                                       reclamo uuid, despues jsonb)
  returns void
  language sql volatile
  set search_path = pg_catalog, pg_temp
  as $$
    insert into auditoria.eventos
      (consorcio_id, actor_persona_id, origen, accion, entidad, entidad_id, despues,
       hash_previo, hash)
    values (c, actor, origen_pedido, accion, 'reclamo_liquidacion', reclamo, despues, '', '')
  $$;

-- Presentar: revalida todo (RF38, PA16) y deja el reclamo con su primer paso. Devuelve a quiénes
-- avisar (las personas con `{FACULTAD}` vigente); la ruta les publica el correo en la misma
-- transacción. Una fila con `persona_id` nulo si no hay nadie a quien avisar.
create function nucleo.presentar_reclamo_liquidacion(importacion uuid, unidad uuid,
                                                     campo_citado text, texto_nuevo text,
                                                     acepta_reemplazada boolean, actor uuid,
                                                     origen_pedido text, sesion uuid)
  returns table (reclamo_id uuid, periodo date, persona_id uuid, correo text,
                 consorcio_nombre text)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    x nucleo.importacion_liquidacion;
    i uuid;
    hoy date;
  begin
    perform nucleo.exigir_reclamo_propio(c, actor, sesion, origen_pedido, unidad);
    hoy := nucleo.hoy_del_consorcio(c);
    -- Un reemplazo en curso termina antes: lo que se lee es lo que queda.
    select * into x from nucleo.importacion_liquidacion l
     where l.consorcio_id = c and l.id = importacion
       and l.estado in ('confirmada', 'reemplazada')
       for share;
    if not found then
      raise exception 'la liquidación citada no es una oficial de este consorcio'
        using errcode = 'P0001', hint = 'liquidacion_no_oficial';
    end if;
    if x.estado = 'reemplazada' and not coalesce(acepta_reemplazada, false) then
      raise exception 'la liquidación citada fue reemplazada' using errcode = 'P0001',
        hint = 'liquidacion_reemplazada';
    end if;
    if campo_citado is not null and not exists (
         select 1 from nucleo.campos_oficiales_de(c, importacion) f
          where f.tipo = campo_citado and f.unidad_id = unidad) then
      raise exception 'ese dato no figura para esa unidad en la liquidación citada'
        using errcode = 'P0001', hint = 'campo_no_figura';
    end if;
    insert into nucleo.reclamo_liquidacion
      (consorcio_id, importacion_id, unidad_id, campo, texto, presentado_por)
    values (c, importacion, unidad, campo_citado, texto_nuevo, actor)
    returning id into i;
    insert into nucleo.reclamo_liquidacion_estado (consorcio_id, reclamo_id, estado, registrado_por)
    values (c, i, 'presentado', actor);
    perform nucleo.auditar_reclamo(c, actor, origen_pedido, '{ACCION_PRESENTAR}', i,
      jsonb_build_object('importacion_id', importacion, 'unidad_id', unidad,
                         'campo', campo_citado));
    return query
      select i, x.periodo, p.persona_id, p.correo, co.nombre
        from nucleo.consorcio co
        left join lateral (
          select distinct pe.id as persona_id, pe.correo
            from nucleo.mandato m
            join nucleo.persona pe on pe.id = m.persona_id and pe.eliminado_en is null
           where m.consorcio_id = c
             and nucleo.tiene_facultad_vigente(c, m.persona_id, '{FACULTAD}', hoy)) p on true
       where co.id = c
       order by p.persona_id;
  end
  $$;

-- Responder: cierra el reclamo con la respuesta. Devuelve a quién avisar (quien lo presentó).
create function nucleo.responder_reclamo_liquidacion(reclamo uuid, texto_nuevo text, actor uuid,
                                                     origen_pedido text, sesion uuid)
  returns table (periodo date, persona_id uuid, correo text, consorcio_nombre text)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    r nucleo.reclamo_liquidacion;
  begin
    perform nucleo.exigir_atender_reclamos(c, actor, sesion, origen_pedido);
    r := nucleo.reclamo_abierto(c, reclamo);
    insert into nucleo.reclamo_liquidacion_estado
      (consorcio_id, reclamo_id, estado, texto, registrado_por)
    values (c, reclamo, 'respondido', texto_nuevo, actor);
    perform nucleo.auditar_reclamo(c, actor, origen_pedido, '{ACCION_CERRAR}', reclamo,
                                   jsonb_build_object('estado', 'respondido'));
    return query
      select x.periodo, pe.id, pe.correo, co.nombre
        from nucleo.importacion_liquidacion x
        join nucleo.persona pe on pe.id = r.presentado_por
        join nucleo.consorcio co on co.id = c
       where x.consorcio_id = c and x.id = r.importacion_id;
  end
  $$;

-- Derivar: lo atiende una solicitud formal (`0108`) que quien atiende acaba de preparar, en
-- borrador, en la misma transacción. Una solicitud atiende a un solo reclamo.
create function nucleo.derivar_reclamo_liquidacion(reclamo uuid, solicitud uuid, actor uuid,
                                                   origen_pedido text, sesion uuid)
  returns void
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
  begin
    perform nucleo.exigir_atender_reclamos(c, actor, sesion, origen_pedido);
    perform nucleo.reclamo_abierto(c, reclamo);
    if not exists (
         select 1 from nucleo.solicitud_formal s
          where s.consorcio_id = c and s.id = solicitud and s.preparada_por = actor
            and not exists (select 1 from nucleo.solicitud_formal_firma f
                             where f.consorcio_id = c and f.solicitud_id = s.id)) then
      raise exception 'la solicitud no es un borrador sin firmas de esta persona'
        using errcode = 'P0001', hint = 'solicitud_invalida';
    end if;
    insert into nucleo.reclamo_liquidacion_estado
      (consorcio_id, reclamo_id, estado, solicitud_id, registrado_por)
    values (c, reclamo, 'derivado_a_solicitud', solicitud, actor);
    perform nucleo.auditar_reclamo(c, actor, origen_pedido, '{ACCION_CERRAR}', reclamo,
      jsonb_build_object('estado', 'derivado_a_solicitud', 'solicitud_id', solicitud));
  end
  $$;

-- Retirar: solo quien lo presentó, con el vínculo todavía vigente sobre la unidad, y con motivo.
create function nucleo.retirar_reclamo_liquidacion(reclamo uuid, motivo text, actor uuid,
                                                   origen_pedido text, sesion uuid)
  returns void
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    r nucleo.reclamo_liquidacion;
  begin
    perform nucleo.exigir_sesion_reclamos(c, actor, sesion, origen_pedido);
    r := nucleo.reclamo_abierto(c, reclamo);
    if r.presentado_por is distinct from actor then
      raise exception 'el reclamo no es de esta persona' using errcode = 'P0001',
        hint = 'no_encontrado';
    end if;
    perform nucleo.exigir_reclamo_propio(c, actor, sesion, origen_pedido, r.unidad_id);
    insert into nucleo.reclamo_liquidacion_estado
      (consorcio_id, reclamo_id, estado, texto, registrado_por)
    values (c, reclamo, 'retirado', motivo, actor);
    perform nucleo.auditar_reclamo(c, actor, origen_pedido, '{ACCION_CERRAR}', reclamo,
                                   jsonb_build_object('estado', 'retirado'));
  end
  $$;

-- Quien lee: quien atiende ve todos; el propietario confirmado, los que presentó. Devuelve si
-- atiende.
create function nucleo.exigir_lectura_reclamos(c uuid, actor uuid, sesion uuid,
                                               origen_pedido text)
  returns boolean
  language plpgsql volatile
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    hoy date;
  begin
    hoy := nucleo.exigir_sesion_reclamos(c, actor, sesion, origen_pedido);
    if nucleo.tiene_facultad_vigente(c, actor, '{FACULTAD}', hoy) then
      return true;
    end if;
    perform nucleo.exigir_propietario_confirmado(c, actor);
    return false;
  end
  $$;

-- Los reclamos, el más nuevo primero, cada uno con su historia (del primer paso al último).
create function nucleo.reclamos_liquidacion_lectura(actor uuid, origen_pedido text, sesion uuid)
  returns table (id uuid, importacion_id uuid, periodo date, liquidacion_vigente boolean,
                 unidad_id uuid, designacion text, campo text, texto text,
                 presentado_por_nombre text, presentado_en timestamptz, propio boolean,
                 estado text, historia jsonb)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    atiende boolean;
  begin
    atiende := nucleo.exigir_lectura_reclamos(c, actor, sesion, origen_pedido);
    return query
      select r.id, r.importacion_id, x.periodo, x.estado = 'confirmada', r.unidad_id,
             u.designacion, r.campo, r.texto, pe.nombre, r.presentado_en,
             r.presentado_por = actor,
             (select e.estado from nucleo.reclamo_liquidacion_estado e
               where e.consorcio_id = c and e.reclamo_id = r.id
               order by e.estado = 'presentado', e.registrado_en desc limit 1),
             (select jsonb_agg(jsonb_build_object(
                       'estado', e.estado, 'texto', e.texto, 'solicitud_id', e.solicitud_id,
                       'solicitud_numero', s.numero, 'registrado_por_nombre', q.nombre,
                       'registrado_en', e.registrado_en)
                     order by e.estado <> 'presentado', e.registrado_en)
                from nucleo.reclamo_liquidacion_estado e
                join nucleo.persona q on q.id = e.registrado_por
                left join nucleo.solicitud_formal s
                  on s.consorcio_id = c and s.id = e.solicitud_id
               where e.consorcio_id = c and e.reclamo_id = r.id)
        from nucleo.reclamo_liquidacion r
        join nucleo.importacion_liquidacion x
          on x.consorcio_id = c and x.id = r.importacion_id
        join nucleo.unidad u on u.consorcio_id = c and u.id = r.unidad_id
        join nucleo.persona pe on pe.id = r.presentado_por
       where r.consorcio_id = c and (atiende or r.presentado_por = actor)
       order by r.presentado_en desc, r.id desc;
  end
  $$;
"""

RUTAS = (
    "nucleo.presentar_reclamo_liquidacion(uuid, uuid, text, text, boolean, uuid, text, uuid)",
    "nucleo.responder_reclamo_liquidacion(uuid, text, uuid, text, uuid)",
    "nucleo.derivar_reclamo_liquidacion(uuid, uuid, uuid, text, uuid)",
    "nucleo.retirar_reclamo_liquidacion(uuid, text, uuid, text, uuid)",
    "nucleo.reclamos_liquidacion_lectura(uuid, text, uuid)",
)
INTERNAS = (
    "nucleo.exigir_sesion_reclamos(uuid, uuid, uuid, text)",
    "nucleo.exigir_reclamo_propio(uuid, uuid, uuid, text, uuid)",
    "nucleo.exigir_atender_reclamos(uuid, uuid, uuid, text)",
    "nucleo.reclamo_abierto(uuid, uuid)",
    "nucleo.auditar_reclamo(uuid, uuid, text, text, uuid, jsonb)",
    "nucleo.exigir_lectura_reclamos(uuid, uuid, uuid, text)",
)


def _de_0069():
    """`_funcion` de `0069`: el mismo encabezado, las mismas exigencias y los mismos permisos.
    `0069` ya está aplicada y no cambia."""
    ruta = Path(__file__).with_name("0069_exportacion_economica.py")
    spec = importlib.util.spec_from_file_location("migracion_0069", ruta)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def _nombre(columna: str) -> str:
    return f"nucleo.nombre_para_exportar(c, {columna}, hoy)"


def _texto(columna: str) -> str:
    return f"nucleo.texto_sin_cuenta({columna})"


CUERPO_EXPORTAR = f"""
    hoy := nucleo.hoy_del_consorcio(c);
    return query
      select 'reclamo_liquidacion'::text,
             jsonb_build_object('id', r.id, 'importacion_id', r.importacion_id,
                                'unidad_designacion', u.designacion, 'campo', r.campo,
                                'texto', {_texto("r.texto")},
                                'presentado_por_nombre', {_nombre("r.presentado_por")},
                                'presentado_en', r.presentado_en)
        from nucleo.reclamo_liquidacion r
        join nucleo.unidad u on u.id = r.unidad_id
       where r.consorcio_id = c
       order by r.presentado_en, r.id;
    return query
      select 'reclamo_liquidacion_estado'::text,
             jsonb_build_object('reclamo_id', e.reclamo_id, 'estado', e.estado,
                                'texto', {_texto("e.texto")}, 'solicitud_id', e.solicitud_id,
                                'registrado_por_nombre', {_nombre("e.registrado_por")},
                                'registrado_en', e.registrado_en)
        from nucleo.reclamo_liquidacion_estado e
        join nucleo.reclamo_liquidacion r on r.consorcio_id = e.consorcio_id and r.id = e.reclamo_id
       where e.consorcio_id = c
       order by r.presentado_en, r.id, e.estado <> 'presentado', e.registrado_en;
"""

GUARDA = f"""
do $guarda$
begin
  if exists (select 1 from nucleo.reclamo_liquidacion)
     or exists (select 1 from nucleo.facultad_otorgada where facultad = '{FACULTAD}') then
    raise exception '0123 no baja: hay reclamos o mandatos con la facultad'
      using errcode = 'P0001', hint = 'downgrade_con_datos';
  end if;
end
$guarda$;
"""


def upgrade() -> None:
    op.execute(
        f"insert into nucleo.facultad (codigo, area, economica)"
        f" values ('{FACULTAD}', 'fiscalizacion', false);"
        f" insert into nucleo.paquete_facultad (tipo, facultad)"
        f" values ('miembro_consejo', '{FACULTAD}'), ('administrador', '{FACULTAD}');"
    )
    op.execute(TABLAS_SQL)
    for tabla in TABLAS:
        op.execute(_protecciones(tabla))
    op.execute(FUNCIONES)
    for funcion in INTERNAS + RUTAS:
        op.execute(f"revoke all on function {funcion} from public")
    for funcion in RUTAS:
        op.execute(f"grant execute on function {funcion} to cem_app")
    op.execute(_de_0069()._funcion(EXPORTAR, CUERPO_EXPORTAR))
    op.execute(
        "insert into auditoria.proyeccion (accion, texto, recurso, campos) values"
        f" ('{ACCION_PRESENTAR}', 'Presentó un reclamo sobre la liquidación',"
        " 'Reclamo sobre la liquidación', array['importacion_id', 'unidad_id', 'campo']),"
        f" ('{ACCION_CERRAR}', 'Cerró un reclamo sobre la liquidación',"
        " 'Reclamo sobre la liquidación', array['estado', 'solicitud_id'])"
    )


def downgrade() -> None:
    op.execute(GUARDA)
    op.execute(
        "delete from auditoria.proyeccion"
        f" where accion in ('{ACCION_PRESENTAR}', '{ACCION_CERRAR}')"
    )
    op.execute(f"drop function nucleo.{EXPORTAR}(uuid, uuid)")
    for funcion in reversed(INTERNAS + RUTAS):
        op.execute(f"drop function {funcion}")
    for tabla in reversed(TABLAS):
        op.execute(f"drop table {tabla}")
    op.execute("drop function nucleo.negar_cuenta_en_reclamo()")
    op.execute("drop function nucleo.negar_cambio_reclamo()")
    op.execute(f"delete from nucleo.paquete_facultad where facultad = '{FACULTAD}'")
    op.execute(f"delete from nucleo.facultad where codigo = '{FACULTAD}'")
