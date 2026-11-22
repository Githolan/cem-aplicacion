"""Comunicados oficiales del consorcio (RF28, mínimo para el piloto en Modo Consejo; diseño P0 S8,
plan §6.7: «RF28 sin estados de lectura»).

**`comunicados.emitir`** (decisión del 22-11-2026): emite un comunicado a todo el edificio. No es
económica. Entra en los paquetes `miembro_consejo` y `administrador`; como en `0036` y `0113`, un
acta reduce un paquete y nunca lo amplía: los mandatos ya registrados no la reciben. Consejo,
Administrador Aumentado y Autogestión (el documento 08, §5, la pone solo en los dos últimos; el plan
la pone en H4a para el piloto: rige también en Modo Consejo).

**Los datos.** Tres tablas, una fila por hecho, sin cambios ni borrado
(`negar_cambio_comunicado`), con RLS forzada por consorcio y sin acceso directo de `cem_app`:

- `nucleo.comunicado`: título (una línea, hasta `LARGO_TITULO`), texto (hasta `LARGO_TEXTO`), quién
  lo emitió y cuándo, y `corrige_a`: una corrección es otro comunicado que cita al que corrige,
  nunca un cambio. Un título o un texto con el número de una cuenta se **rechaza**
  (`texto_con_cuenta`), con la definición de `0060` (`nucleo.texto_con_cuenta`).
- `nucleo.comunicado_destinatario`: cada persona a la que se dirigió, una vez aunque tenga varias
  unidades. Son las que en ese momento tienen un vínculo de propietario, inquilino u ocupante
  confirmado, vigente y no importado pendiente (`nucleo.destinatarios_comunicado`, el criterio de
  `exigir_propietario_confirmado` de `0119` con los tres tipos).
- `nucleo.comunicado_envio`: el correo de cada destinatario, con el evento del outbox
  (`correo.solicitado`) que lo pidió. Es el estado **enviado**. La plataforma no registra la
  entrega: el trabajo `notificar.correo` dice que el servidor SMTP aceptó el correo y se purga a
  los 30 días (`plataforma.purgar_cola`), así que «entregado» no se guarda en este corte. Tampoco
  «visto» ni «formalizado».

**Los actos** (`security definer`, solo `cem_app`): `emitir_comunicado` exige la facultad, un modo
registrado y una sesión válida; con nadie para recibirlo se niega (`sin_destinatarios`). La ruta
publica un correo por destinatario en la misma transacción y `registrar_envios_comunicado` los
anota: cada evento tiene que ser un `correo.solicitado` de este consorcio dirigido a esa persona, y
todos los destinatarios tienen que tener el suyo, o no queda nada.

**Lectura** (`comunicados_lectura`): quien emite y quien tiene un vínculo confirmado ven todos los
comunicados del consorcio; las cuentas de destinatarios y envíos, solo quien emite. Nunca la lista
de personas ni sus correos.

**Exportación** (`0069`): la parte nueva `comunicados`, con los nombres y nunca los
identificadores de las personas, y los textos por `texto_sin_cuenta`.

Downgrade: se niega (`downgrade_con_datos`) si hay comunicados o algún mandato con la facultad.

Datos de prueba: SINTETICOS / NO REAL.

Revisión: 0122
Anterior: 0121
"""

import importlib.util
from pathlib import Path

from alembic import op

revision = "0122"
down_revision = "0121"
branch_labels = None
depends_on = None

FACULTAD = "comunicados.emitir"
ACCION = "consorcio.comunicado.emitir"
LARGO_TITULO = 120
LARGO_TEXTO = 4000
"""Los de `cem_contracts.comunicados`; una prueba compara."""
TIPOS_LECTORES = ("propietario", "inquilino", "ocupante")
TABLAS = (
    "nucleo.comunicado",
    "nucleo.comunicado_destinatario",
    "nucleo.comunicado_envio",
)
EXPORTAR = "exportar_comunicados"


def _lista(valores) -> str:
    return ", ".join(f"'{v}'" for v in valores)


def _largo(columna: str, maximo: int) -> str:
    return f"length(btrim({columna})) between 1 and {maximo} and {columna} = btrim({columna})"


TABLAS_SQL = f"""
create function nucleo.negar_cambio_comunicado()
  returns trigger
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  begin
    raise exception 'un comunicado no se cambia ni se borra: se corrige con otro'
      using errcode = 'P0001', hint = 'historia_inmutable';
  end
  $$;
revoke all on function nucleo.negar_cambio_comunicado() from public;

-- Ni el título ni el texto llevan una cuenta (`0060`): se rechaza, no se tapa.
create function nucleo.negar_cuenta_en_comunicado()
  returns trigger
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  begin
    if nucleo.texto_con_cuenta(new.titulo) or nucleo.texto_con_cuenta(new.texto) then
      raise exception 'un comunicado no puede llevar una cuenta' using errcode = 'P0001',
        hint = 'texto_con_cuenta';
    end if;
    return new;
  end
  $$;
revoke all on function nucleo.negar_cuenta_en_comunicado() from public;

create table nucleo.comunicado (
  consorcio_id  uuid not null references nucleo.consorcio (id),
  id            uuid primary key default plataforma.uuid_v7(),
  titulo        text not null
    check ({_largo("titulo", LARGO_TITULO)} and titulo !~ '[[:cntrl:]]'),
  texto         text not null check ({_largo("texto", LARGO_TEXTO)}),
  corrige_a     uuid,
  emitido_por   uuid not null references nucleo.persona (id),
  emitido_en    timestamptz not null default clock_timestamp(),
  unique (consorcio_id, id),
  foreign key (consorcio_id, corrige_a) references nucleo.comunicado (consorcio_id, id)
);
create index comunicado_corrige_a on nucleo.comunicado (consorcio_id, corrige_a)
  where corrige_a is not null;
create trigger comunicado_sin_cuenta before insert on nucleo.comunicado
  for each row execute function nucleo.negar_cuenta_en_comunicado();

create table nucleo.comunicado_destinatario (
  consorcio_id   uuid not null,
  comunicado_id  uuid not null,
  persona_id     uuid not null references nucleo.persona (id),
  primary key (comunicado_id, persona_id),
  unique (consorcio_id, comunicado_id, persona_id),
  foreign key (consorcio_id, comunicado_id) references nucleo.comunicado (consorcio_id, id)
);

-- Sin clave foránea al outbox: sus filas se purgan a los 30 días y esta queda.
create table nucleo.comunicado_envio (
  consorcio_id   uuid not null,
  comunicado_id  uuid not null,
  persona_id     uuid not null,
  evento_id      uuid not null unique,
  enviado_en     timestamptz not null default clock_timestamp(),
  primary key (comunicado_id, persona_id),
  foreign key (consorcio_id, comunicado_id, persona_id)
    references nucleo.comunicado_destinatario (consorcio_id, comunicado_id, persona_id)
);
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
  for each row execute function nucleo.negar_cambio_comunicado();
create trigger historia_{corto}_truncate before truncate on {tabla}
  for each statement execute function nucleo.negar_cambio_comunicado();
"""


FUNCIONES = f"""
-- Quien pide es la persona de la sesión, con una sesión válida, por la API y con un modo
-- registrado (los tres admiten los comunicados). Devuelve el día del consorcio.
create function nucleo.exigir_sesion_comunicados(c uuid, actor uuid, sesion uuid,
                                                 origen_pedido text)
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

-- Quien emite: lo anterior y `{FACULTAD}` vigente.
create function nucleo.exigir_emitir_comunicado(c uuid, actor uuid, sesion uuid,
                                                origen_pedido text)
  returns void
  language plpgsql volatile
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    hoy date;
  begin
    hoy := nucleo.exigir_sesion_comunicados(c, actor, sesion, origen_pedido);
    if not nucleo.tiene_facultad_vigente(c, actor, '{FACULTAD}', hoy) then
      raise exception 'sin {FACULTAD} en este consorcio' using errcode = '42501';
    end if;
  end
  $$;

-- Las personas con un vínculo de propietario, inquilino u ocupante confirmado, vigente hoy y no
-- importado pendiente (el criterio de `exigir_propietario_confirmado`, `0119`), una vez cada una.
create function nucleo.destinatarios_comunicado(c uuid)
  returns table (persona_id uuid)
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select distinct v.persona_id
      from nucleo.vinculo v
      join nucleo.persona pe on pe.id = v.persona_id and pe.eliminado_en is null
     where v.consorcio_id = c and v.tipo::text in ({_lista(TIPOS_LECTORES)})
       and v.confirmado_en is not null
       and daterange(v.vigente_desde, v.vigente_hasta) @> nucleo.hoy_del_consorcio(c)
       and v.id not in (select nucleo.vinculos_importados_pendientes())
  $$;

-- Quien lee: quien emite, o una persona entre los destinatarios de hoy. Devuelve si emite.
create function nucleo.exigir_lectura_comunicados(c uuid, actor uuid, sesion uuid,
                                                  origen_pedido text)
  returns boolean
  language plpgsql volatile
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    hoy date;
  begin
    hoy := nucleo.exigir_sesion_comunicados(c, actor, sesion, origen_pedido);
    if nucleo.tiene_facultad_vigente(c, actor, '{FACULTAD}', hoy) then
      return true;
    end if;
    if not exists (select 1 from nucleo.destinatarios_comunicado(c) d where d.persona_id = actor)
    then
      raise exception 'sin un vínculo confirmado en este consorcio' using errcode = '42501';
    end if;
    return false;
  end
  $$;

-- Emitir: el comunicado y sus destinatarios. Devuelve a quiénes mandarles el correo; la ruta lo
-- publica en la misma transacción y lo anota con `registrar_envios_comunicado`.
create function nucleo.emitir_comunicado(titulo_nuevo text, texto_nuevo text, corrige uuid,
                                         actor uuid, origen_pedido text, sesion uuid)
  returns table (comunicado_id uuid, persona_id uuid, correo text, consorcio_nombre text)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    i uuid;
  begin
    perform nucleo.exigir_emitir_comunicado(c, actor, sesion, origen_pedido);
    if corrige is not null and not exists (
         select 1 from nucleo.comunicado x where x.consorcio_id = c and x.id = corrige) then
      raise exception 'el comunicado que se corrige no existe en este consorcio'
        using errcode = 'P0001', hint = 'corrige_a_invalido';
    end if;
    if not exists (select 1 from nucleo.destinatarios_comunicado(c)) then
      raise exception 'nadie tiene un vínculo confirmado en este consorcio'
        using errcode = 'P0001', hint = 'sin_destinatarios';
    end if;
    insert into nucleo.comunicado (consorcio_id, titulo, texto, corrige_a, emitido_por)
    values (c, titulo_nuevo, texto_nuevo, corrige, actor)
    returning id into i;
    insert into nucleo.comunicado_destinatario (consorcio_id, comunicado_id, persona_id)
    select c, i, d.persona_id from nucleo.destinatarios_comunicado(c) d;
    insert into auditoria.eventos
      (consorcio_id, actor_persona_id, origen, accion, entidad, entidad_id, despues,
       hash_previo, hash)
    values (c, actor, origen_pedido, '{ACCION}', 'comunicado', i,
            jsonb_build_object('titulo', titulo_nuevo, 'corrige_a', corrige,
                               'destinatarios', (select count(*) from
                                 nucleo.comunicado_destinatario d
                                 where d.consorcio_id = c and d.comunicado_id = i)),
            '', '');
    return query
      select i, d.persona_id, pe.correo, co.nombre
        from nucleo.comunicado_destinatario d
        join nucleo.persona pe on pe.id = d.persona_id
        join nucleo.consorcio co on co.id = c
       where d.consorcio_id = c and d.comunicado_id = i
       order by d.persona_id;
  end
  $$;

-- Los correos publicados: `envios` = [{{persona_id, evento_id}}, …]. Cada evento es un
-- `correo.solicitado` de este consorcio dirigido a esa persona, y cada destinatario tiene el suyo.
create function nucleo.registrar_envios_comunicado(comunicado uuid, envios jsonb, actor uuid,
                                                   origen_pedido text, sesion uuid)
  returns integer
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    cuantos integer;
  begin
    perform nucleo.exigir_emitir_comunicado(c, actor, sesion, origen_pedido);
    if not exists (select 1 from nucleo.comunicado x
                    where x.consorcio_id = c and x.id = comunicado and x.emitido_por = actor) then
      raise exception 'el comunicado no es de esta persona en este consorcio'
        using errcode = 'P0001', hint = 'no_encontrado';
    end if;
    if exists (select 1 from nucleo.comunicado_envio n
                where n.consorcio_id = c and n.comunicado_id = comunicado) then
      raise exception 'los envíos ya se registraron' using errcode = 'P0001',
        hint = 'envios_ya_registrados';
    end if;
    insert into nucleo.comunicado_envio (consorcio_id, comunicado_id, persona_id, evento_id)
    select c, comunicado, (e.envio->>'persona_id')::uuid, (e.envio->>'evento_id')::uuid
      from jsonb_array_elements(envios) as e (envio);
    if exists (
         select 1 from nucleo.comunicado_envio n
           join nucleo.persona pe on pe.id = n.persona_id
          where n.consorcio_id = c and n.comunicado_id = comunicado
            and not exists (select 1 from plataforma.outbox o
                             where o.id = n.evento_id and o.consorcio_id = c
                               and o.evento = 'correo.solicitado'
                               and o.carga->>'para' = pe.correo)) then
      raise exception 'un envío no es el correo de su destinatario' using errcode = 'P0001',
        hint = 'envio_sin_correo';
    end if;
    select count(*) into cuantos from nucleo.comunicado_envio x
     where x.consorcio_id = c and x.comunicado_id = comunicado;
    if cuantos <> (select count(*) from nucleo.comunicado_destinatario d
              where d.consorcio_id = c and d.comunicado_id = comunicado) then
      raise exception 'falta el correo de algún destinatario' using errcode = 'P0001',
        hint = 'envios_incompletos';
    end if;
    return cuantos;
  end
  $$;

-- Cuántas personas recibirían un comunicado emitido ahora: el paso de confirmar.
create function nucleo.comunicados_destinatarios_posibles(actor uuid, origen_pedido text,
                                                          sesion uuid)
  returns integer
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
  begin
    perform nucleo.exigir_emitir_comunicado(c, actor, sesion, origen_pedido);
    return (select count(*)::integer from nucleo.destinatarios_comunicado(c));
  end
  $$;

-- Los comunicados del consorcio, del más nuevo al más viejo. Las cuentas, solo para quien emite.
create function nucleo.comunicados_lectura(actor uuid, origen_pedido text, sesion uuid)
  returns table (id uuid, titulo text, texto text, emitido_por_nombre text,
                 emitido_en timestamptz, corrige_a jsonb, corregido_por jsonb,
                 destinatarios integer, enviados integer)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    emisor boolean;
  begin
    emisor := nucleo.exigir_lectura_comunicados(c, actor, sesion, origen_pedido);
    return query
      select x.id, x.titulo, x.texto, pe.nombre, x.emitido_en,
             (select jsonb_build_object('id', y.id, 'titulo', y.titulo,
                                        'emitido_en', y.emitido_en)
                from nucleo.comunicado y
               where y.consorcio_id = c and y.id = x.corrige_a),
             coalesce((select jsonb_agg(jsonb_build_object('id', z.id, 'titulo', z.titulo,
                                                           'emitido_en', z.emitido_en)
                                        order by z.emitido_en, z.id)
                         from nucleo.comunicado z
                        where z.consorcio_id = c and z.corrige_a = x.id), '[]'::jsonb),
             case when emisor then
               (select count(*)::integer from nucleo.comunicado_destinatario d
                 where d.consorcio_id = c and d.comunicado_id = x.id) end,
             case when emisor then
               (select count(*)::integer from nucleo.comunicado_envio n
                 where n.consorcio_id = c and n.comunicado_id = x.id) end
        from nucleo.comunicado x
        join nucleo.persona pe on pe.id = x.emitido_por
       where x.consorcio_id = c
       order by x.emitido_en desc, x.id desc;
  end
  $$;
"""

RUTAS = (
    "nucleo.emitir_comunicado(text, text, uuid, uuid, text, uuid)",
    "nucleo.registrar_envios_comunicado(uuid, jsonb, uuid, text, uuid)",
    "nucleo.comunicados_destinatarios_posibles(uuid, text, uuid)",
    "nucleo.comunicados_lectura(uuid, text, uuid)",
)
INTERNAS = (
    "nucleo.exigir_sesion_comunicados(uuid, uuid, uuid, text)",
    "nucleo.exigir_emitir_comunicado(uuid, uuid, uuid, text)",
    "nucleo.destinatarios_comunicado(uuid)",
    "nucleo.exigir_lectura_comunicados(uuid, uuid, uuid, text)",
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
      select 'comunicado'::text,
             jsonb_build_object('id', x.id, 'titulo', {_texto("x.titulo")},
                                'texto', {_texto("x.texto")}, 'corrige_a', x.corrige_a,
                                'emitido_por_nombre', {_nombre("x.emitido_por")},
                                'emitido_en', x.emitido_en)
        from nucleo.comunicado x
       where x.consorcio_id = c
       order by x.emitido_en, x.id;
    return query
      select 'comunicado_destinatario'::text,
             jsonb_build_object('comunicado_id', d.comunicado_id,
                                'destinatario_nombre', {_nombre("d.persona_id")},
                                'estado', case when n.persona_id is null then 'sin_envio'
                                               else 'enviado' end,
                                'enviado_en', n.enviado_en)
        from nucleo.comunicado_destinatario d
        join nucleo.comunicado x on x.consorcio_id = d.consorcio_id and x.id = d.comunicado_id
        left join nucleo.comunicado_envio n
          on n.consorcio_id = d.consorcio_id and n.comunicado_id = d.comunicado_id
         and n.persona_id = d.persona_id
       where d.consorcio_id = c
       order by x.emitido_en, x.id, n.enviado_en, d.persona_id;
"""

GUARDA = f"""
do $guarda$
begin
  if exists (select 1 from nucleo.comunicado)
     or exists (select 1 from nucleo.facultad_otorgada where facultad = '{FACULTAD}') then
    raise exception '0122 no baja: hay comunicados o mandatos con la facultad'
      using errcode = 'P0001', hint = 'downgrade_con_datos';
  end if;
end
$guarda$;
"""


def upgrade() -> None:
    op.execute(
        f"insert into nucleo.facultad (codigo, area, economica)"
        f" values ('{FACULTAD}', 'comunicaciones', false);"
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
        f" ('{ACCION}', 'Emitió un comunicado al edificio', 'Comunicado',"
        " array['titulo', 'corrige_a', 'destinatarios'])"
    )


def downgrade() -> None:
    op.execute(GUARDA)
    op.execute(f"delete from auditoria.proyeccion where accion = '{ACCION}'")
    op.execute(f"drop function nucleo.{EXPORTAR}(uuid, uuid)")
    for funcion in reversed(INTERNAS + RUTAS):
        op.execute(f"drop function {funcion}")
    for tabla in reversed(TABLAS):
        op.execute(f"drop table {tabla}")
    op.execute("drop function nucleo.negar_cuenta_en_comunicado()")
    op.execute("drop function nucleo.negar_cambio_comunicado()")
    op.execute(f"delete from nucleo.paquete_facultad where facultad = '{FACULTAD}'")
    op.execute(f"delete from nucleo.facultad where codigo = '{FACULTAD}'")
