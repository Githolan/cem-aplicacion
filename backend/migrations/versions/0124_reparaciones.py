"""Reparaciones a cargo del consorcio (RF19, con la regla de cierre de RF20; Modo Consejo, diseño P0
S10 a S12 y plan §5.2 y §6.8 del repositorio CEM).

**Quién pide** (decisión del 22-11-2026): una persona con un vínculo de propietario, inquilino u
ocupante confirmado, vigente y que no sea un importado pendiente (el documento 08, §4, dice «Sí»
para los tres), o quien tiene la facultad nueva `reparaciones.gestionar`. El pedido lleva una
descripción, el lugar (una unidad de quien pide, o un espacio común con un texto corto; quien
gestiona puede pedir por cualquier unidad) y hasta `FOTOS_MAXIMO` fotos, que entran por el mismo
camino de los documentos con antivirus (2c): un documento de «Otros», restringido, con su versión en
cuarentena. El número es correlativo por consorcio (`pg_advisory_xact_lock`, como la solicitud
formal de `0108`).

**Quién gestiona:** la facultad nueva `reparaciones.gestionar` (no económica), en los paquetes
`miembro_consejo` y `administrador`; como en `0122` y `0123`, los mandatos ya registrados no la
reciben. Registra el responsable (una persona con la facultad), la prioridad y la fecha comprometida
(`reparacion_gestion`, una fila por cambio), el presupuesto (documento de «Presupuestos») con el
proveedor (nombre y CUIT optativo, validado con `cuit_valido`; en Modo Consejo no hace falta una
ficha de proveedor: lo confirma el gasto enlazado al cerrar), la constancia de aprobación (quién
aprobó, con qué facultad se registró y qué la respalda: una decisión formal del consorcio o un
documento firmado en «Actas y decisiones»; en Modo Consejo la administración ejecuta fuera de la
plataforma, así que es una constancia y no una aprobación de pago), la terminación con la factura
(documento de «Facturas y comprobantes») y el cierre.

**Estados** (`nucleo.reparacion_estado`, una fila por paso, sin cambios ni borrado): `pedida` →
`en_gestion` → `presupuestada` → `aprobada` → `terminada_gasto_pendiente` → `cerrada`; desde
cualquier estado abierto, `no_realizada` (con el motivo de la imposibilidad, RF20); desde `pedida`,
`retirada` (por quien la pidió, con motivo). La base impone las transiciones sola: cada fila cita
el estado anterior (clave foránea a la misma tabla), el par anterior → nuevo tiene que estar en
`TRANSICIONES` (`reparacion_transicion_valida`) y un estado tiene a lo sumo un sucesor
(`reparacion_un_sucesor`): dos pasos a la vez desde el mismo estado, gana uno.

**El cierre (RF19 y RF20):** sin la factura no se termina. En Modo Consejo el gasto llega con la
liquidación importada (H5), así que la reparación terminada espera en `terminada_gasto_pendiente`
hasta que se enlaza un gasto (`gasto:<rubro>:<n>`) de una liquidación oficial **confirmada** del
mismo consorcio (`campos_oficiales_de`); ahí pasa a `cerrada`. Un gasto cierra a lo sumo una
reparación (`reparacion_un_gasto`, como PA11). Todo documento que un acto cita tiene que estar
disponible (el antivirus lo aceptó), ser de esa reparación, del tipo del acto, subido por quien hace
el acto y no usado por otro acto. En Administrador Aumentado y Autogestión el cierre con la orden de
pago pagada no está en este corte: las políticas de los actos admiten solo el Modo Consejo.

**Lectura** (`reparaciones_lectura`): quien gestiona ve todas; quien pidió, las suyas. Los demás
propietarios no ven nada en este corte (el seguimiento público queda pendiente).

**Correos:** la ruta publica, en la misma transacción, uno a cada persona con la facultad al
pedirse, y uno a quien la pidió en cada cambio de estado que hace otra persona. Solo el título con
el número y el enlace; nunca la descripción ni las fotos.

**Exportación** (`0069`): la parte nueva `reparaciones`, con los nombres y nunca los identificadores
de las personas, y los textos por `texto_sin_cuenta`.

Downgrade: se niega (`downgrade_con_datos`) si hay reparaciones o algún mandato con la facultad.

Datos de prueba: SINTETICOS / NO REAL.

Revisión: 0124
Anterior: 0123
"""

import importlib.util
from pathlib import Path

from alembic import op
from sqlalchemy import text

revision = "0124"
down_revision = "0123"
branch_labels = None
depends_on = None

FACULTAD = "reparaciones.gestionar"
LARGO_DESCRIPCION = 2000
LARGO_LUGAR = 120
LARGO_MOTIVO = 500
LARGO_PROVEEDOR = 120
LARGO_APROBADO_POR = 120
FOTOS_MAXIMO = 4
"""Los de `cem_contracts.reparaciones`; una prueba compara."""
PRIORIDADES = ("emergencia", "urgente", "normal")
TIPOS_ADJUNTO = ("foto", "presupuesto", "aprobacion", "factura")
ABIERTOS = ("pedida", "en_gestion", "presupuestada", "aprobada", "terminada_gasto_pendiente")
ESTADOS = (*ABIERTOS, "cerrada", "no_realizada", "retirada")
TRANSICIONES = {
    "pedida": (None,),
    "en_gestion": ("pedida",),
    "presupuestada": ("en_gestion",),
    "aprobada": ("presupuestada",),
    "terminada_gasto_pendiente": ("aprobada",),
    "cerrada": ("terminada_gasto_pendiente",),
    "no_realizada": ABIERTOS,
    "retirada": ("pedida",),
}
"""Estado nuevo → los estados desde los que se llega (None: el primero)."""
CATEGORIAS = {
    "foto": "otros",
    "presupuesto": "presupuestos",
    "aprobacion": "actas_y_decisiones",
    "factura": "facturas_y_comprobantes",
}
"""La categoría del documento de cada adjunto. Las fotos (pueden mostrar el interior de una unidad)
van a «Otros», que ve solo quien tiene la facultad `documentos.ver`."""
TITULOS = {
    "foto": "foto",
    "presupuesto": "presupuesto",
    "aprobacion": "constancia de aprobación",
    "factura": "factura",
}
ESTADO_DEL_ADJUNTO = {
    "presupuesto": "en_gestion",
    "aprobacion": "presupuestada",
    "factura": "aprobada",
}
"""El estado en que se sube cada documento de quien gestiona: el del acto que lo va a citar."""
ACCION_PEDIR = "consorcio.reparacion.pedir"
ACCION_ADJUNTAR = "consorcio.reparacion.adjuntar"
ACCION_GESTIONAR = "consorcio.reparacion.gestionar"
ACCION_ESTADO = "consorcio.reparacion.cambiar_estado"
TABLAS = (
    "nucleo.reparacion",
    "nucleo.reparacion_adjunto",
    "nucleo.reparacion_gestion",
    "nucleo.reparacion_estado",
)
"""En el orden en que se crean: cada una después de las que cita."""
EXPORTAR = "exportar_reparaciones"


def _lista(valores) -> str:
    return ", ".join(f"'{v}'" for v in valores)


def _largo(columna: str, maximo: int) -> str:
    return f"length(btrim({columna})) between 1 and {maximo} and {columna} = btrim({columna})"


def _transiciones() -> str:
    casos = []
    for nuevo, desde in TRANSICIONES.items():
        if desde == (None,):
            casos.append(f"when '{nuevo}' then anterior is null")
        else:
            casos.append(f"when '{nuevo}' then anterior in ({_lista(desde)})")
    return "case estado " + " ".join(casos) + " else false end"


def _categoria_de(columna: str) -> str:
    casos = " ".join(f"when '{t}' then '{c}'" for t, c in CATEGORIAS.items())
    return f"case {columna} {casos} end"


def _titulo_de(columna: str) -> str:
    casos = " ".join(f"when '{t}' then '{c}'" for t, c in TITULOS.items())
    return f"case {columna} {casos} end"


def _estado_para(columna: str) -> str:
    casos = " ".join(f"when '{t}' then '{e}'" for t, e in ESTADO_DEL_ADJUNTO.items())
    return f"case {columna} {casos} end"


TABLAS_SQL = f"""
create function nucleo.negar_cambio_reparacion()
  returns trigger
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  begin
    raise exception 'una reparación no se cambia ni se borra: cada paso es una fila nueva'
      using errcode = 'P0001', hint = 'historia_inmutable';
  end
  $$;
revoke all on function nucleo.negar_cambio_reparacion() from public;

-- Ningún texto libre de una reparación lleva una cuenta (`0060`): se rechaza. Las columnas que se
-- miran van como argumentos del disparador.
create function nucleo.negar_cuenta_en_reparacion()
  returns trigger
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    columna text;
  begin
    foreach columna in array tg_argv loop
      if nucleo.texto_con_cuenta(to_jsonb(new) ->> columna) then
        raise exception 'una reparación no puede llevar una cuenta' using errcode = 'P0001',
          hint = 'texto_con_cuenta';
      end if;
    end loop;
    return new;
  end
  $$;
revoke all on function nucleo.negar_cuenta_en_reparacion() from public;

create table nucleo.reparacion (
  consorcio_id  uuid not null references nucleo.consorcio (id),
  id            uuid primary key default plataforma.uuid_v7(),
  numero        integer not null check (numero > 0),
  descripcion   text not null check ({_largo("descripcion", LARGO_DESCRIPCION)}),
  unidad_id     uuid,
  lugar         text check ({_largo("lugar", LARGO_LUGAR)}),
  pedida_por    uuid not null references nucleo.persona (id),
  pedida_en     timestamptz not null default clock_timestamp(),
  unique (consorcio_id, id),
  unique (consorcio_id, numero),
  foreign key (consorcio_id, unidad_id) references nucleo.unidad (consorcio_id, id),
  -- Una unidad, o un espacio común con su texto.
  constraint reparacion_con_lugar check (unidad_id is not null or lugar is not null)
);
create index reparacion_pedida_por on nucleo.reparacion (consorcio_id, pedida_por);
create trigger reparacion_sin_cuenta before insert on nucleo.reparacion
  for each row execute function nucleo.negar_cuenta_en_reparacion('descripcion', 'lugar');

create table nucleo.reparacion_adjunto (
  consorcio_id          uuid not null,
  id                    uuid primary key default plataforma.uuid_v7(),
  reparacion_id         uuid not null,
  tipo                  text not null check (tipo in ({_lista(TIPOS_ADJUNTO)})),
  documento_id          uuid not null,
  version_documento_id  uuid not null unique references nucleo.version_documento (id),
  subido_por            uuid not null references nucleo.persona (id),
  subido_en             timestamptz not null default clock_timestamp(),
  unique (consorcio_id, id),
  unique (consorcio_id, reparacion_id, id),
  foreign key (consorcio_id, reparacion_id) references nucleo.reparacion (consorcio_id, id),
  foreign key (consorcio_id, documento_id) references nucleo.documento (consorcio_id, id)
);
create index reparacion_adjunto_reparacion
  on nucleo.reparacion_adjunto (consorcio_id, reparacion_id);
create index reparacion_adjunto_documento
  on nucleo.reparacion_adjunto (consorcio_id, documento_id);
create trigger documento_citado_vigente before insert or update of documento_id
  on nucleo.reparacion_adjunto
  for each row execute function nucleo.documento_citado_vigente('documento_id');

create table nucleo.reparacion_gestion (
  consorcio_id        uuid not null,
  id                  uuid primary key default plataforma.uuid_v7(),
  reparacion_id       uuid not null,
  responsable_id      uuid not null references nucleo.persona (id),
  prioridad           text not null check (prioridad in ({_lista(PRIORIDADES)})),
  fecha_comprometida  date,
  registrado_por      uuid not null references nucleo.persona (id),
  registrado_en       timestamptz not null default clock_timestamp(),
  foreign key (consorcio_id, reparacion_id) references nucleo.reparacion (consorcio_id, id)
);
create index reparacion_gestion_reparacion
  on nucleo.reparacion_gestion (consorcio_id, reparacion_id, registrado_en);

create table nucleo.reparacion_estado (
  consorcio_id      uuid not null,
  reparacion_id     uuid not null,
  estado            text not null check (estado in ({_lista(ESTADOS)})),
  anterior          text,
  motivo            text check ({_largo("motivo", LARGO_MOTIVO)}),
  adjunto_id        uuid unique,
  proveedor_nombre  text check ({_largo("proveedor_nombre", LARGO_PROVEEDOR)}),
  proveedor_cuit    text check (nucleo.cuit_valido(proveedor_cuit)),
  aprobado_por      text check ({_largo("aprobado_por", LARGO_APROBADO_POR)}),
  facultad          text,
  decision_id       uuid,
  importacion_id    uuid,
  gasto_clave       text check (gasto_clave ~ '^gasto:[^:]+:[0-9]+$'),
  registrado_por    uuid not null references nucleo.persona (id),
  registrado_en     timestamptz not null default clock_timestamp(),
  primary key (reparacion_id, estado),
  foreign key (consorcio_id, reparacion_id) references nucleo.reparacion (consorcio_id, id),
  -- El estado anterior existe en la historia de la misma reparación, y tiene un solo sucesor.
  foreign key (reparacion_id, anterior) references nucleo.reparacion_estado (reparacion_id, estado),
  constraint reparacion_un_sucesor unique (reparacion_id, anterior),
  constraint reparacion_transicion_valida check ({_transiciones()}),
  foreign key (consorcio_id, reparacion_id, adjunto_id)
    references nucleo.reparacion_adjunto (consorcio_id, reparacion_id, id),
  foreign key (consorcio_id, decision_id) references nucleo.decision_formal (consorcio_id, id),
  foreign key (consorcio_id, importacion_id)
    references nucleo.importacion_liquidacion (consorcio_id, id),
  constraint reparacion_motivo_del_estado
    check ((motivo is not null) = (estado in ('no_realizada', 'retirada'))),
  constraint reparacion_presupuesto_con_proveedor
    check ((proveedor_nombre is not null) = (estado = 'presupuestada')
           and (proveedor_cuit is null or estado = 'presupuestada')),
  constraint reparacion_aprobacion_con_constancia
    check ((aprobado_por is not null) = (estado = 'aprobada')
           and (facultad is not null) = (estado = 'aprobada')
           and (decision_id is null or estado = 'aprobada')
           and (estado <> 'aprobada' or (decision_id is null) <> (adjunto_id is null))),
  constraint reparacion_documento_del_estado
    check ((adjunto_id is not null) = (estado in ('presupuestada', 'terminada_gasto_pendiente')
                                       or estado = 'aprobada' and decision_id is null)),
  constraint reparacion_cierre_con_gasto
    check ((importacion_id is not null) = (estado = 'cerrada')
           and (gasto_clave is not null) = (estado = 'cerrada'))
);
-- PA11: un gasto cierra a lo sumo una reparación.
create unique index reparacion_un_gasto
  on nucleo.reparacion_estado (consorcio_id, importacion_id, gasto_clave)
  where gasto_clave is not null;
-- La última barrera frente a dos finales a la vez.
create unique index reparacion_un_final
  on nucleo.reparacion_estado (reparacion_id)
  where estado in ('cerrada', 'no_realizada', 'retirada');
create trigger reparacion_estado_sin_cuenta before insert on nucleo.reparacion_estado
  for each row execute function nucleo.negar_cuenta_en_reparacion('motivo', 'proveedor_nombre',
                                                                  'aprobado_por');
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
  for each row execute function nucleo.negar_cambio_reparacion();
create trigger historia_{corto}_truncate before truncate on {tabla}
  for each statement execute function nucleo.negar_cambio_reparacion();
"""


FUNCIONES = f"""
-- Quien pide es la persona de la sesión, con una sesión válida, por la API y con un modo
-- registrado; para los actos, además, el Modo Consejo (el único con el cierre por el gasto
-- importado en este corte). Devuelve el día del consorcio.
create function nucleo.exigir_sesion_reparaciones(c uuid, actor uuid, sesion uuid,
                                                  origen_pedido text, acto boolean)
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
            and (m.vigente_hasta is null or m.vigente_hasta > hoy)
            and (not acto or m.modo = 'consejo')) then
      raise exception 'sin un modo que lo admita' using errcode = 'P0001',
        hint = 'modo_no_admitido';
    end if;
    if not exists (select 1 from nucleo.sesion x
                    where x.id = sesion and x.persona_id = actor and x.revocada_en is null
                      and x.vence_en > now()) then
      raise exception 'la sesión no es válida' using errcode = 'P0001', hint = 'sesion_invalida';
    end if;
    return hoy;
  end
  $$;

-- Las unidades donde la persona tiene un vínculo de propietario, inquilino u ocupante vigente,
-- confirmado y que no es un importado pendiente (como `exigir_propietario_confirmado`, `0119`).
create function nucleo.unidades_para_reparaciones(c uuid, quien uuid)
  returns uuid[]
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select coalesce(array_agg(distinct v.unidad_id), '{{}}')
      from nucleo.vinculo v
     where v.consorcio_id = c and v.persona_id = quien
       and v.tipo in ('propietario', 'inquilino', 'ocupante')
       and v.confirmado_en is not null
       and daterange(v.vigente_desde, v.vigente_hasta) @> nucleo.hoy_del_consorcio(c)
       and v.id not in (select nucleo.vinculos_importados_pendientes())
  $$;

-- Quien gestiona: `{FACULTAD}` vigente.
create function nucleo.gestiona_reparaciones(c uuid, quien uuid, hoy date)
  returns boolean
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select nucleo.tiene_facultad_vigente(c, quien, '{FACULTAD}', hoy)
  $$;

create function nucleo.exigir_gestionar_reparaciones(c uuid, actor uuid, sesion uuid,
                                                     origen_pedido text)
  returns void
  language plpgsql volatile
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    hoy date;
  begin
    hoy := nucleo.exigir_sesion_reparaciones(c, actor, sesion, origen_pedido, true);
    if not nucleo.gestiona_reparaciones(c, actor, hoy) then
      raise exception 'sin {FACULTAD} en este consorcio' using errcode = '42501';
    end if;
  end
  $$;

-- Quien pide (o sigue actuando sobre lo que pidió): un vínculo que cuenta o la facultad. Con una
-- unidad, quien no gestiona solo pide por una suya.
create function nucleo.exigir_pedir_reparacion(c uuid, actor uuid, sesion uuid,
                                               origen_pedido text, unidad uuid)
  returns void
  language plpgsql volatile
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    hoy date;
    propias uuid[];
  begin
    hoy := nucleo.exigir_sesion_reparaciones(c, actor, sesion, origen_pedido, true);
    if nucleo.gestiona_reparaciones(c, actor, hoy) then
      return;
    end if;
    propias := nucleo.unidades_para_reparaciones(c, actor);
    if cardinality(propias) = 0 or (unidad is not null and not unidad = any(propias)) then
      raise exception 'sin un vínculo confirmado que permita pedir esa reparación'
        using errcode = '42501';
    end if;
  end
  $$;

-- La reparación del consorcio, trabada hasta el final de la transacción, con su estado actual.
create function nucleo.reparacion_bloqueada(c uuid, reparacion uuid,
                                            out r nucleo.reparacion, out estado_actual text)
  language plpgsql volatile
  set search_path = pg_catalog, pg_temp
  as $$
  begin
    select * into r from nucleo.reparacion x
     where x.consorcio_id = c and x.id = reparacion
       for update;
    if not found then
      raise exception 'la reparación no existe en este consorcio' using errcode = 'P0001',
        hint = 'no_encontrado';
    end if;
    estado_actual := nucleo.estado_de_reparacion(c, reparacion);
  end
  $$;

-- El último paso: el único estado sin sucesor.
create function nucleo.estado_de_reparacion(c uuid, reparacion uuid)
  returns text
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select e.estado from nucleo.reparacion_estado e
     where e.consorcio_id = c and e.reparacion_id = reparacion
       and not exists (select 1 from nucleo.reparacion_estado s
                        where s.reparacion_id = e.reparacion_id and s.anterior = e.estado)
  $$;

create function nucleo.exigir_estado_reparacion(actual text, admitidos text[])
  returns void
  language plpgsql immutable
  set search_path = pg_catalog, pg_temp
  as $$
  begin
    if not actual = any(admitidos) then
      raise exception 'la reparación no admite ese paso en su estado' using errcode = 'P0001',
        hint = 'reparacion_no_admite';
    end if;
  end
  $$;

create function nucleo.auditar_reparacion(c uuid, actor uuid, origen_pedido text, accion text,
                                          reparacion uuid, despues jsonb)
  returns void
  language sql volatile
  set search_path = pg_catalog, pg_temp
  as $$
    insert into auditoria.eventos
      (consorcio_id, actor_persona_id, origen, accion, entidad, entidad_id, despues,
       hash_previo, hash)
    values (c, actor, origen_pedido, accion, 'reparacion', reparacion, despues, '', '')
  $$;

-- A quién avisar de un cambio de estado: quien la pidió, si el paso lo dio otra persona. Una fila
-- con `persona_id` nulo si no hay a quién.
create function nucleo.aviso_a_quien_pidio(c uuid, r nucleo.reparacion, actor uuid)
  returns table (numero integer, persona_id uuid, correo text, consorcio_nombre text)
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select r.numero, pe.id, pe.correo, co.nombre
      from nucleo.consorcio co
      left join nucleo.persona pe
        on pe.id = r.pedida_por and pe.eliminado_en is null and r.pedida_por <> actor
     where co.id = c
  $$;

-- Un paso nuevo de la historia, desde el estado actual.
create function nucleo.paso_de_reparacion(c uuid, reparacion uuid, actual text, nuevo text,
                                          actor uuid, origen_pedido text, datos jsonb)
  returns void
  language plpgsql volatile
  set search_path = pg_catalog, pg_temp
  as $$
  begin
    insert into nucleo.reparacion_estado
      (consorcio_id, reparacion_id, estado, anterior, motivo, adjunto_id, proveedor_nombre,
       proveedor_cuit, aprobado_por, facultad, decision_id, importacion_id, gasto_clave,
       registrado_por)
    values (c, reparacion, nuevo, actual, datos ->> 'motivo', (datos ->> 'adjunto_id')::uuid,
            datos ->> 'proveedor_nombre', datos ->> 'proveedor_cuit', datos ->> 'aprobado_por',
            datos ->> 'facultad', (datos ->> 'decision_id')::uuid,
            (datos ->> 'importacion_id')::uuid, datos ->> 'gasto_clave', actor);
    perform nucleo.auditar_reparacion(c, actor, origen_pedido, '{ACCION_ESTADO}', reparacion,
      jsonb_build_object('estado', nuevo, 'anterior', actual)
        || (datos - 'motivo' - 'proveedor_nombre' - 'aprobado_por'));
  end
  $$;

-- El documento que cita un acto: de esa reparación, del tipo del acto, subido por quien lo hace,
-- de un documento vigente, que el antivirus aceptó y que ningún acto usó.
create function nucleo.exigir_adjunto_reparacion(c uuid, reparacion uuid, adjunto uuid,
                                                 tipo_pedido text, actor uuid)
  returns void
  language plpgsql stable
  set search_path = pg_catalog, pg_temp
  as $$
  begin
    if adjunto is null or not exists (
         select 1 from nucleo.reparacion_adjunto a
           join nucleo.version_documento x on x.id = a.version_documento_id
           join nucleo.documento d on d.consorcio_id = a.consorcio_id and d.id = a.documento_id
          where a.consorcio_id = c and a.id = adjunto and a.reparacion_id = reparacion
            and a.tipo = tipo_pedido and a.subido_por = actor
            and x.estado = 'disponible' and d.estado = 'vigente')
       or exists (select 1 from nucleo.reparacion_estado e
                   where e.consorcio_id = c and e.adjunto_id = adjunto) then
      raise exception 'el documento no es uno disponible de esta reparación para este paso'
        using errcode = 'P0001', hint = 'adjunto_invalido';
    end if;
  end
  $$;

-- Si esa versión la subió esa persona para una reparación (para `fijar_version_objeto`).
create function nucleo.adjunto_de_reparacion(c uuid, version uuid, actor uuid)
  returns boolean
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select exists (select 1 from nucleo.reparacion_adjunto a
                    where a.consorcio_id = c and a.version_documento_id = version
                      and a.subido_por = actor)
  $$;

-- Pedir: el número correlativo y el primer paso. Devuelve a quiénes avisar (las personas con
-- `{FACULTAD}` vigente, salvo quien pide); la ruta les publica el correo en la misma transacción.
create function nucleo.pedir_reparacion(descripcion_nueva text, unidad uuid, lugar_nuevo text,
                                        actor uuid, origen_pedido text, sesion uuid)
  returns table (reparacion_id uuid, numero integer, persona_id uuid, correo text,
                 consorcio_nombre text)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    n integer;
    i uuid;
    hoy date;
  begin
    perform nucleo.exigir_pedir_reparacion(c, actor, sesion, origen_pedido, unidad);
    hoy := nucleo.hoy_del_consorcio(c);
    -- El número es correlativo por consorcio: dos pedidos a la vez no toman el mismo.
    perform pg_advisory_xact_lock(hashtextextended('reparacion:' || c::text, 0));
    select coalesce(max(x.numero), 0) + 1 into n from nucleo.reparacion x
     where x.consorcio_id = c;
    insert into nucleo.reparacion (consorcio_id, numero, descripcion, unidad_id, lugar, pedida_por)
    values (c, n, descripcion_nueva, unidad, lugar_nuevo, actor)
    returning id into i;
    insert into nucleo.reparacion_estado (consorcio_id, reparacion_id, estado, registrado_por)
    values (c, i, 'pedida', actor);
    perform nucleo.auditar_reparacion(c, actor, origen_pedido, '{ACCION_PEDIR}', i,
      jsonb_build_object('numero', n, 'unidad_id', unidad));
    return query
      select i, n, p.persona_id, p.correo, co.nombre
        from nucleo.consorcio co
        left join lateral (
          select distinct pe.id as persona_id, pe.correo
            from nucleo.mandato m
            join nucleo.persona pe on pe.id = m.persona_id and pe.eliminado_en is null
           where m.consorcio_id = c and m.persona_id <> actor
             and nucleo.gestiona_reparaciones(c, m.persona_id, hoy)) p on true
       where co.id = c
       order by p.persona_id;
  end
  $$;

-- Un archivo de la reparación: un documento con su versión en cuarentena, subido por quien va a
-- usarlo. Las fotos, quien la pidió mientras está abierta (hasta `FOTOS_MAXIMO` que el antivirus no
-- rechazó); los demás, quien gestiona en el estado del acto que lo va a citar. La ruta guarda el
-- objeto y encola el antivirus en la misma transacción.
create function nucleo.subir_adjunto_reparacion(reparacion uuid, tipo_nuevo text,
                                                tipo_archivo text, tamano_nuevo integer,
                                                sha256_nuevo text, actor uuid,
                                                origen_pedido text, sesion uuid)
  returns table (nueva_version_id uuid, nueva_clave_objeto text, nuevo_documento_id uuid,
                 nuevo_adjunto_id uuid)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    b record;
    d uuid;
    v uuid := plataforma.uuid_v7();
    k text;
    a uuid;
    cat text := {_categoria_de("tipo_nuevo")};
    hoy date;
  begin
    if cat is null then
      raise exception 'tipo de adjunto inválido' using errcode = 'P0001',
        hint = 'adjunto_invalido';
    end if;
    select * into b from nucleo.reparacion_bloqueada(c, reparacion);
    if tipo_nuevo = 'foto' then
      if (b.r).pedida_por is distinct from actor then
        raise exception 'la reparación no es de esta persona' using errcode = 'P0001',
          hint = 'no_encontrado';
      end if;
      perform nucleo.exigir_pedir_reparacion(c, actor, sesion, origen_pedido, (b.r).unidad_id);
      perform nucleo.exigir_estado_reparacion(b.estado_actual, array[{_lista(ABIERTOS)}]);
      if (select count(*) from nucleo.reparacion_adjunto f
            join nucleo.version_documento x on x.id = f.version_documento_id
           where f.consorcio_id = c and f.reparacion_id = reparacion and f.tipo = 'foto'
             and x.estado <> 'rechazado') >= {FOTOS_MAXIMO} then
        raise exception 'la reparación ya tiene todas las fotos' using errcode = 'P0001',
          hint = 'fotos_completas';
      end if;
    else
      perform nucleo.exigir_gestionar_reparaciones(c, actor, sesion, origen_pedido);
      perform nucleo.exigir_estado_reparacion(b.estado_actual,
                                              array[{_estado_para("tipo_nuevo")}]);
    end if;
    hoy := nucleo.hoy_del_consorcio(c);
    insert into nucleo.documento (consorcio_id, categoria, titulo, fecha, cargado_por)
    values (c, cat, 'Reparación N.º ' || (b.r).numero || ': ' || {_titulo_de("tipo_nuevo")}, hoy,
            actor)
    returning id into d;
    perform nucleo.auditar_documento(c, 'consorcio.documento.crear', 'documento', d, actor,
      origen_pedido, jsonb_build_object('categoria', cat, 'fecha', hoy));
    k := 'cem/' || c || '/' || d || '/' || v;
    insert into nucleo.version_documento
      (consorcio_id, documento_id, id, tipo, tamano, sha256, clave_objeto, subida_por)
    values (c, d, v, tipo_archivo, tamano_nuevo, sha256_nuevo, k, actor);
    perform nucleo.auditar_documento(c, 'consorcio.documento.cargar_version', 'version_documento',
      v, actor, origen_pedido, jsonb_build_object('tipo', tipo_archivo, 'tamano', tamano_nuevo));
    insert into nucleo.reparacion_adjunto
      (consorcio_id, reparacion_id, tipo, documento_id, version_documento_id, subido_por)
    values (c, reparacion, tipo_nuevo, d, v, actor)
    returning id into a;
    perform nucleo.auditar_reparacion(c, actor, origen_pedido, '{ACCION_ADJUNTAR}', reparacion,
      jsonb_build_object('tipo', tipo_nuevo, 'adjunto_id', a, 'documento_id', d));
    return query select v, k, d, a;
  end
  $$;

-- Responsable, prioridad y fecha comprometida: la primera vez la reparación pasa a `en_gestion`;
-- después, cada cambio es otra fila. Devuelve a quién avisar si cambió el estado.
create function nucleo.gestionar_reparacion(reparacion uuid, responsable uuid,
                                            prioridad_nueva text, fecha date, actor uuid,
                                            origen_pedido text, sesion uuid)
  returns table (numero integer, persona_id uuid, correo text, consorcio_nombre text)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    b record;
  begin
    perform nucleo.exigir_gestionar_reparaciones(c, actor, sesion, origen_pedido);
    select * into b from nucleo.reparacion_bloqueada(c, reparacion);
    perform nucleo.exigir_estado_reparacion(b.estado_actual, array[{_lista(ABIERTOS)}]);
    if responsable is null or not exists (
         select 1 from nucleo.persona p where p.id = responsable and p.eliminado_en is null)
       or not nucleo.gestiona_reparaciones(c, responsable, nucleo.hoy_del_consorcio(c)) then
      raise exception 'el responsable no tiene {FACULTAD} en este consorcio'
        using errcode = 'P0001', hint = 'responsable_invalido';
    end if;
    insert into nucleo.reparacion_gestion
      (consorcio_id, reparacion_id, responsable_id, prioridad, fecha_comprometida, registrado_por)
    values (c, reparacion, responsable, prioridad_nueva, fecha, actor);
    perform nucleo.auditar_reparacion(c, actor, origen_pedido, '{ACCION_GESTIONAR}', reparacion,
      jsonb_build_object('responsable_id', responsable, 'prioridad', prioridad_nueva,
                         'fecha_comprometida', fecha));
    if b.estado_actual = 'pedida' then
      perform nucleo.paso_de_reparacion(c, reparacion, 'pedida', 'en_gestion', actor,
                                        origen_pedido, '{{}}'::jsonb);
      return query select * from nucleo.aviso_a_quien_pidio(c, b.r, actor);
    end if;
  end
  $$;

-- Un paso de quien gestiona con un documento o un dato: presupuesto, aprobación y terminación.
create function nucleo.presupuestar_reparacion(reparacion uuid, adjunto uuid, proveedor text,
                                               cuit text, actor uuid, origen_pedido text,
                                               sesion uuid)
  returns table (numero integer, persona_id uuid, correo text, consorcio_nombre text)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    b record;
  begin
    perform nucleo.exigir_gestionar_reparaciones(c, actor, sesion, origen_pedido);
    select * into b from nucleo.reparacion_bloqueada(c, reparacion);
    perform nucleo.exigir_estado_reparacion(b.estado_actual, array['en_gestion']);
    perform nucleo.exigir_adjunto_reparacion(c, reparacion, adjunto, 'presupuesto', actor);
    if cuit is not null and not nucleo.cuit_valido(cuit) then
      raise exception 'el CUIT no es válido' using errcode = 'P0001', hint = 'cuit_invalido';
    end if;
    perform nucleo.paso_de_reparacion(c, reparacion, 'en_gestion', 'presupuestada', actor,
      origen_pedido, jsonb_build_object('adjunto_id', adjunto, 'proveedor_nombre', proveedor,
                                        'proveedor_cuit', cuit));
    return query select * from nucleo.aviso_a_quien_pidio(c, b.r, actor);
  end
  $$;

-- La constancia de aprobación: quién aprobó, con qué facultad se registra y qué la respalda (una
-- decisión formal del consorcio o un documento firmado). No es una aprobación de pago.
create function nucleo.aprobar_reparacion(reparacion uuid, aprobado text, decision uuid,
                                          adjunto uuid, actor uuid, origen_pedido text,
                                          sesion uuid)
  returns table (numero integer, persona_id uuid, correo text, consorcio_nombre text)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    b record;
  begin
    perform nucleo.exigir_gestionar_reparaciones(c, actor, sesion, origen_pedido);
    select * into b from nucleo.reparacion_bloqueada(c, reparacion);
    perform nucleo.exigir_estado_reparacion(b.estado_actual, array['presupuestada']);
    if (decision is null) = (adjunto is null) then
      raise exception 'la constancia cita una decisión formal o un documento, uno de los dos'
        using errcode = 'P0001', hint = 'constancia_invalida';
    end if;
    if decision is not null and not exists (
         select 1 from nucleo.decision_formal f where f.consorcio_id = c and f.id = decision) then
      raise exception 'la decisión no es de este consorcio' using errcode = 'P0001',
        hint = 'constancia_invalida';
    end if;
    if adjunto is not null then
      perform nucleo.exigir_adjunto_reparacion(c, reparacion, adjunto, 'aprobacion', actor);
    end if;
    perform nucleo.paso_de_reparacion(c, reparacion, 'presupuestada', 'aprobada', actor,
      origen_pedido, jsonb_build_object('aprobado_por', aprobado, 'facultad', '{FACULTAD}',
                                        'decision_id', decision, 'adjunto_id', adjunto));
    return query select * from nucleo.aviso_a_quien_pidio(c, b.r, actor);
  end
  $$;

-- Terminada, con la factura: espera el gasto (en Modo Consejo llega con la liquidación importada).
create function nucleo.terminar_reparacion(reparacion uuid, adjunto uuid, actor uuid,
                                           origen_pedido text, sesion uuid)
  returns table (numero integer, persona_id uuid, correo text, consorcio_nombre text)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    b record;
  begin
    perform nucleo.exigir_gestionar_reparaciones(c, actor, sesion, origen_pedido);
    select * into b from nucleo.reparacion_bloqueada(c, reparacion);
    perform nucleo.exigir_estado_reparacion(b.estado_actual, array['aprobada']);
    perform nucleo.exigir_adjunto_reparacion(c, reparacion, adjunto, 'factura', actor);
    perform nucleo.paso_de_reparacion(c, reparacion, 'aprobada', 'terminada_gasto_pendiente',
      actor, origen_pedido, jsonb_build_object('adjunto_id', adjunto));
    return query select * from nucleo.aviso_a_quien_pidio(c, b.r, actor);
  end
  $$;

-- El cierre (RF19): enlaza un gasto de una liquidación oficial confirmada del consorcio. Un gasto
-- cierra a lo sumo una reparación: el candado del gasto ordena dos enlaces a la vez y el índice
-- `reparacion_un_gasto` es la última barrera.
create function nucleo.enlazar_gasto_reparacion(reparacion uuid, importacion uuid, clave text,
                                                actor uuid, origen_pedido text, sesion uuid)
  returns table (numero integer, persona_id uuid, correo text, consorcio_nombre text)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    b record;
  begin
    perform nucleo.exigir_gestionar_reparaciones(c, actor, sesion, origen_pedido);
    select * into b from nucleo.reparacion_bloqueada(c, reparacion);
    perform nucleo.exigir_estado_reparacion(b.estado_actual,
                                            array['terminada_gasto_pendiente']);
    -- Un reemplazo en curso termina antes: lo que se lee es lo que queda.
    perform 1 from nucleo.importacion_liquidacion x
     where x.consorcio_id = c and x.id = importacion and x.estado = 'confirmada'
       for share;
    if not found or not exists (
         select 1 from nucleo.campos_oficiales_de(c, importacion) f
          where f.tipo = 'gasto' and f.clave = enlazar_gasto_reparacion.clave) then
      raise exception 'no es un gasto de una liquidación oficial vigente de este consorcio'
        using errcode = 'P0001', hint = 'gasto_no_oficial';
    end if;
    perform pg_advisory_xact_lock(
      hashtextextended('reparacion_gasto:' || importacion::text || ':' || clave, 0));
    if exists (select 1 from nucleo.reparacion_estado e
                where e.consorcio_id = c and e.importacion_id = importacion
                  and e.gasto_clave = enlazar_gasto_reparacion.clave) then
      raise exception 'ese gasto ya cierra otra reparación' using errcode = 'P0001',
        hint = 'gasto_ya_enlazado';
    end if;
    perform nucleo.paso_de_reparacion(c, reparacion, 'terminada_gasto_pendiente', 'cerrada',
      actor, origen_pedido,
      jsonb_build_object('importacion_id', importacion, 'gasto_clave', clave));
    return query select * from nucleo.aviso_a_quien_pidio(c, b.r, actor);
  end
  $$;

-- RF20: sin evidencia, el final lleva el motivo de la imposibilidad.
create function nucleo.no_realizar_reparacion(reparacion uuid, motivo_nuevo text, actor uuid,
                                              origen_pedido text, sesion uuid)
  returns table (numero integer, persona_id uuid, correo text, consorcio_nombre text)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    b record;
  begin
    perform nucleo.exigir_gestionar_reparaciones(c, actor, sesion, origen_pedido);
    select * into b from nucleo.reparacion_bloqueada(c, reparacion);
    perform nucleo.exigir_estado_reparacion(b.estado_actual, array[{_lista(ABIERTOS)}]);
    perform nucleo.paso_de_reparacion(c, reparacion, b.estado_actual, 'no_realizada', actor,
      origen_pedido, jsonb_build_object('motivo', motivo_nuevo));
    return query select * from nucleo.aviso_a_quien_pidio(c, b.r, actor);
  end
  $$;

-- Retirar: solo quien la pidió, mientras está pedida, con el permiso de pedir todavía vigente.
create function nucleo.retirar_reparacion(reparacion uuid, motivo_nuevo text, actor uuid,
                                          origen_pedido text, sesion uuid)
  returns void
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    b record;
  begin
    perform nucleo.exigir_sesion_reparaciones(c, actor, sesion, origen_pedido, true);
    select * into b from nucleo.reparacion_bloqueada(c, reparacion);
    if (b.r).pedida_por is distinct from actor then
      raise exception 'la reparación no es de esta persona' using errcode = 'P0001',
        hint = 'no_encontrado';
    end if;
    perform nucleo.exigir_pedir_reparacion(c, actor, sesion, origen_pedido, (b.r).unidad_id);
    perform nucleo.exigir_estado_reparacion(b.estado_actual, array['pedida']);
    perform nucleo.paso_de_reparacion(c, reparacion, 'pedida', 'retirada', actor, origen_pedido,
                                      jsonb_build_object('motivo', motivo_nuevo));
  end
  $$;

-- Quien lee: quien gestiona ve todas; quien tiene un vínculo que cuenta, las que pidió. En
-- cualquier modo registrado (la historia no se pierde con un cambio de modo). Devuelve si gestiona.
create function nucleo.exigir_lectura_reparaciones(c uuid, actor uuid, sesion uuid,
                                                   origen_pedido text)
  returns boolean
  language plpgsql volatile
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    hoy date;
  begin
    hoy := nucleo.exigir_sesion_reparaciones(c, actor, sesion, origen_pedido, false);
    if nucleo.gestiona_reparaciones(c, actor, hoy) then
      return true;
    end if;
    if cardinality(nucleo.unidades_para_reparaciones(c, actor)) = 0 then
      raise exception 'sin un vínculo confirmado ni la facultad en este consorcio'
        using errcode = '42501';
    end if;
    return false;
  end
  $$;

-- Las reparaciones, la más nueva primero, cada una con su gestión vigente, su historia (del primer
-- paso al último) y sus documentos: los disponibles y, de los otros, los que subió quien lee.
create function nucleo.reparaciones_lectura(actor uuid, origen_pedido text, sesion uuid)
  returns table (id uuid, numero integer, descripcion text, unidad_id uuid, designacion text,
                 lugar text, pedida_por_nombre text, pedida_en timestamptz, propia boolean,
                 estado text, gestion jsonb, historia jsonb, adjuntos jsonb)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    gestiona boolean;
  begin
    gestiona := nucleo.exigir_lectura_reparaciones(c, actor, sesion, origen_pedido);
    return query
      select r.id, r.numero, r.descripcion, r.unidad_id, u.designacion, r.lugar, pe.nombre,
             r.pedida_en, r.pedida_por = actor, nucleo.estado_de_reparacion(c, r.id),
             (select jsonb_build_object('responsable_id', g.responsable_id,
                                        'responsable_nombre', q.nombre,
                                        'prioridad', g.prioridad,
                                        'fecha_comprometida', g.fecha_comprometida)
                from nucleo.reparacion_gestion g
                join nucleo.persona q on q.id = g.responsable_id
               where g.consorcio_id = c and g.reparacion_id = r.id
               order by g.registrado_en desc, g.id desc limit 1),
             (select jsonb_agg(jsonb_build_object(
                       'estado', e.estado, 'motivo', e.motivo, 'adjunto_id', e.adjunto_id,
                       'proveedor_nombre', e.proveedor_nombre,
                       'proveedor_cuit', e.proveedor_cuit, 'aprobado_por', e.aprobado_por,
                       'facultad', e.facultad, 'decision_id', e.decision_id,
                       'decision_descripcion', f.descripcion, 'decision_fecha', f.fecha,
                       'liquidacion_id', e.importacion_id, 'periodo', x.periodo,
                       'gasto_clave', e.gasto_clave, 'gasto', o.valor,
                       'registrado_por_nombre', q.nombre, 'registrado_en', e.registrado_en)
                     order by e.registrado_en, e.estado = 'pedida' desc)
                from nucleo.reparacion_estado e
                join nucleo.persona q on q.id = e.registrado_por
                left join nucleo.decision_formal f
                  on f.consorcio_id = c and f.id = e.decision_id
                left join nucleo.importacion_liquidacion x
                  on x.consorcio_id = c and x.id = e.importacion_id
                left join lateral (
                  select o.valor from nucleo.campos_oficiales_de(c, e.importacion_id) o
                   where o.tipo = 'gasto' and o.clave = e.gasto_clave) o on true
               where e.consorcio_id = c and e.reparacion_id = r.id),
             (select coalesce(jsonb_agg(jsonb_build_object(
                       'id', a.id, 'tipo', a.tipo, 'estado', v.estado,
                       'tipo_archivo', v.tipo, 'subido_en', a.subido_en,
                       'propio', a.subido_por = actor,
                       'usado', exists (select 1 from nucleo.reparacion_estado u
                                         where u.consorcio_id = c and u.adjunto_id = a.id))
                     order by a.subido_en, a.id), '[]'::jsonb)
                from nucleo.reparacion_adjunto a
                join nucleo.version_documento v on v.id = a.version_documento_id
                join nucleo.documento d on d.consorcio_id = c and d.id = a.documento_id
               where a.consorcio_id = c and a.reparacion_id = r.id and d.estado = 'vigente'
                 and (v.estado = 'disponible' or a.subido_por = actor))
        from nucleo.reparacion r
        left join nucleo.unidad u on u.consorcio_id = c and u.id = r.unidad_id
        join nucleo.persona pe on pe.id = r.pedida_por
       where r.consorcio_id = c and (gestiona or r.pedida_por = actor)
       order by r.numero desc;
  end
  $$;

-- Lo que la pantalla de quien gestiona ofrece elegir: los responsables posibles, las decisiones
-- formales que respaldan una aprobación y los gastos de las liquidaciones oficiales confirmadas
-- que todavía no cierran ninguna reparación. Para quien pide: las unidades por las que puede pedir.
create function nucleo.opciones_reparaciones(actor uuid, origen_pedido text, sesion uuid)
  returns table (clase text, fila jsonb)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    gestiona boolean;
    hoy date;
  begin
    gestiona := nucleo.exigir_lectura_reparaciones(c, actor, sesion, origen_pedido);
    hoy := nucleo.hoy_del_consorcio(c);
    return query
      select 'unidad'::text, jsonb_build_object('id', u.id, 'designacion', u.designacion)
        from nucleo.unidad u
       where u.consorcio_id = c and u.eliminado_en is null
         and (gestiona or u.id = any(nucleo.unidades_para_reparaciones(c, actor)))
       order by u.designacion;
    if not gestiona then
      return;
    end if;
    return query
      select 'responsable'::text, jsonb_build_object('id', p.id, 'nombre', p.nombre)
        from nucleo.persona p
       where p.eliminado_en is null
         and p.id in (select m.persona_id from nucleo.mandato m where m.consorcio_id = c)
         and nucleo.gestiona_reparaciones(c, p.id, hoy)
       order by p.nombre, p.id;
    return query
      select 'decision'::text, jsonb_build_object('id', f.id, 'tipo', f.tipo::text,
                                                  'fecha', f.fecha,
                                                  'descripcion', f.descripcion)
        from nucleo.decision_formal f
       where f.consorcio_id = c
       order by f.fecha desc, f.id desc;
    return query
      select 'gasto'::text, jsonb_build_object('liquidacion_id', x.id, 'periodo', x.periodo,
                                               'clave', o.clave, 'valor', o.valor)
        from nucleo.importacion_liquidacion x
        cross join lateral nucleo.campos_oficiales_de(c, x.id) o
       where x.consorcio_id = c and x.estado = 'confirmada' and o.tipo = 'gasto'
         and not exists (select 1 from nucleo.reparacion_estado e
                          where e.consorcio_id = c and e.importacion_id = x.id
                            and e.gasto_clave = o.clave)
       order by x.periodo desc, o.orden;
  end
  $$;

-- Un documento de la reparación para verlo: quien la lee lo ve si está disponible; quien lo subió,
-- también en cuarentena o rechazado (la ruta decide qué responde). Ninguna fila: no existe o no se
-- puede leer.
create function nucleo.adjunto_reparacion_para_ver(reparacion uuid, adjunto uuid, actor uuid,
                                                   origen_pedido text, sesion uuid)
  returns table (estado text, tipo text, clave_objeto text, version_objeto text, propia boolean)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    gestiona boolean;
  begin
    gestiona := nucleo.exigir_lectura_reparaciones(c, actor, sesion, origen_pedido);
    return query
      select v.estado, v.tipo, v.clave_objeto, v.version_objeto, a.subido_por = actor
        from nucleo.reparacion_adjunto a
        join nucleo.reparacion r on r.consorcio_id = c and r.id = a.reparacion_id
        join nucleo.version_documento v on v.id = a.version_documento_id
        join nucleo.documento d on d.consorcio_id = c and d.id = a.documento_id
       where a.consorcio_id = c and a.reparacion_id = reparacion and a.id = adjunto
         and d.estado = 'vigente' and (gestiona or r.pedida_por = actor)
         and (v.estado = 'disponible' or a.subido_por = actor);
  end
  $$;
"""

RUTAS = (
    "nucleo.pedir_reparacion(text, uuid, text, uuid, text, uuid)",
    "nucleo.subir_adjunto_reparacion(uuid, text, text, integer, text, uuid, text, uuid)",
    "nucleo.gestionar_reparacion(uuid, uuid, text, date, uuid, text, uuid)",
    "nucleo.presupuestar_reparacion(uuid, uuid, text, text, uuid, text, uuid)",
    "nucleo.aprobar_reparacion(uuid, text, uuid, uuid, uuid, text, uuid)",
    "nucleo.terminar_reparacion(uuid, uuid, uuid, text, uuid)",
    "nucleo.enlazar_gasto_reparacion(uuid, uuid, text, uuid, text, uuid)",
    "nucleo.no_realizar_reparacion(uuid, text, uuid, text, uuid)",
    "nucleo.retirar_reparacion(uuid, text, uuid, text, uuid)",
    "nucleo.reparaciones_lectura(uuid, text, uuid)",
    "nucleo.opciones_reparaciones(uuid, text, uuid)",
    "nucleo.adjunto_reparacion_para_ver(uuid, uuid, uuid, text, uuid)",
)
INTERNAS = (
    "nucleo.exigir_lectura_reparaciones(uuid, uuid, uuid, text)",
    "nucleo.adjunto_de_reparacion(uuid, uuid, uuid)",
    "nucleo.exigir_adjunto_reparacion(uuid, uuid, uuid, text, uuid)",
    "nucleo.paso_de_reparacion(uuid, uuid, text, text, uuid, text, jsonb)",
    "nucleo.aviso_a_quien_pidio(uuid, nucleo.reparacion, uuid)",
    "nucleo.auditar_reparacion(uuid, uuid, text, text, uuid, jsonb)",
    "nucleo.exigir_estado_reparacion(text, text[])",
    "nucleo.reparacion_bloqueada(uuid, uuid)",
    "nucleo.estado_de_reparacion(uuid, uuid)",
    "nucleo.exigir_pedir_reparacion(uuid, uuid, uuid, text, uuid)",
    "nucleo.exigir_gestionar_reparaciones(uuid, uuid, uuid, text)",
    "nucleo.gestiona_reparaciones(uuid, uuid, date)",
    "nucleo.unidades_para_reparaciones(uuid, uuid)",
    "nucleo.exigir_sesion_reparaciones(uuid, uuid, uuid, text, boolean)",
)
"""En el orden en que se pueden borrar: cada una antes de las que llama."""

REFERENCIADO = "nucleo.documento_referenciado(uuid, uuid)"
REFERENCIADO_CAMBIOS = (
    (
        "        or exists (select 1 from nucleo.importacion_liquidacion x\n",
        "        or exists (select 1 from nucleo.reparacion_adjunto x\n"
        "                    where x.consorcio_id = c and x.documento_id = documento)\n"
        "        or exists (select 1 from nucleo.importacion_liquidacion x\n",
    ),
)
"""Lo que esta migración suma a `REFERENCIAS` de `0089`: el adjunto de una reparación cita su
documento, que ya no se quita ni se retira."""
FIJAR_OBJETO = "nucleo.fijar_version_objeto(uuid, text, uuid, text)"
FIJAR_CAMBIOS = (
    (
        "       or not nucleo.adjunto_de_solicitud_formal(c, version, actor) and not exists (\n",
        "       or not nucleo.adjunto_de_solicitud_formal(c, version, actor)\n"
        "          -- 0124: tampoco la pide el archivo que esa persona subió para una reparación.\n"
        "          and not nucleo.adjunto_de_reparacion(c, version, actor) and not exists (\n",
    ),
)
EN_SU_LUGAR = ((REFERENCIADO, REFERENCIADO_CAMBIOS), (FIJAR_OBJETO, FIJAR_CAMBIOS))
"""Misma firma y lo mismo que devuelven: `create or replace` con la definición cambiada."""


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
      select 'reparacion'::text,
             jsonb_build_object('id', r.id, 'numero', r.numero,
                                'descripcion', {_texto("r.descripcion")},
                                'unidad_designacion', u.designacion,
                                'lugar', {_texto("r.lugar")},
                                'pedida_por_nombre', {_nombre("r.pedida_por")},
                                'pedida_en', r.pedida_en)
        from nucleo.reparacion r
        left join nucleo.unidad u on u.consorcio_id = r.consorcio_id and u.id = r.unidad_id
       where r.consorcio_id = c
       order by r.numero;
    return query
      select 'reparacion_gestion'::text,
             jsonb_build_object('reparacion_id', g.reparacion_id,
                                'responsable_nombre', {_nombre("g.responsable_id")},
                                'prioridad', g.prioridad,
                                'fecha_comprometida', g.fecha_comprometida,
                                'registrado_por_nombre', {_nombre("g.registrado_por")},
                                'registrado_en', g.registrado_en)
        from nucleo.reparacion_gestion g
       where g.consorcio_id = c
       order by g.registrado_en, g.id;
    return query
      select 'reparacion_adjunto'::text,
             jsonb_build_object('id', a.id, 'reparacion_id', a.reparacion_id, 'tipo', a.tipo,
                                'documento_id', a.documento_id,
                                'version_documento_id', a.version_documento_id,
                                'subido_por_nombre', {_nombre("a.subido_por")},
                                'subido_en', a.subido_en)
        from nucleo.reparacion_adjunto a
       where a.consorcio_id = c
       order by a.subido_en, a.id;
    return query
      select 'reparacion_estado'::text,
             jsonb_build_object('reparacion_id', e.reparacion_id, 'estado', e.estado,
                                'anterior', e.anterior, 'motivo', {_texto("e.motivo")},
                                'adjunto_id', e.adjunto_id,
                                'proveedor_nombre', {_texto("e.proveedor_nombre")},
                                'proveedor_cuit', e.proveedor_cuit,
                                'aprobado_por', {_texto("e.aprobado_por")},
                                'facultad', e.facultad, 'decision_id', e.decision_id,
                                'importacion_id', e.importacion_id,
                                'gasto_clave', e.gasto_clave,
                                'registrado_por_nombre', {_nombre("e.registrado_por")},
                                'registrado_en', e.registrado_en)
        from nucleo.reparacion_estado e
        join nucleo.reparacion r on r.consorcio_id = e.consorcio_id and r.id = e.reparacion_id
       where e.consorcio_id = c
       order by r.numero, e.registrado_en, e.estado = 'pedida' desc;
"""

GUARDA = f"""
do $guarda$
begin
  if exists (select 1 from nucleo.reparacion)
     or exists (select 1 from nucleo.facultad_otorgada where facultad = '{FACULTAD}') then
    raise exception '0124 no baja: hay reparaciones o mandatos con la facultad'
      using errcode = 'P0001', hint = 'downgrade_con_datos';
  end if;
end
$guarda$;
"""

PROYECCIONES = (
    (ACCION_PEDIR, "Pidió una reparación", ["numero", "unidad_id"]),
    (ACCION_ADJUNTAR, "Subió un archivo de una reparación", ["tipo", "adjunto_id", "documento_id"]),
    (ACCION_GESTIONAR, "Registró el responsable de una reparación",
     ["responsable_id", "prioridad", "fecha_comprometida"]),
    (ACCION_ESTADO, "Cambió el estado de una reparación",
     ["estado", "anterior", "adjunto_id", "decision_id", "importacion_id", "gasto_clave"]),
)  # fmt: skip


def _definicion(funcion: str, cambios, *, al_reves: bool) -> str:
    definicion = (
        op.get_bind()
        .execute(text("select pg_get_functiondef(cast(:f as regprocedure))"), {"f": funcion})
        .scalar_one()
    )
    for antes, despues in reversed(cambios) if al_reves else cambios:
        if al_reves:
            antes, despues = despues, antes
        if definicion.count(antes) != 1:
            raise RuntimeError(f"0124: {funcion} no tiene exactamente una vez {antes!r}")
        definicion = definicion.replace(antes, despues)
    return definicion


def upgrade() -> None:
    op.execute(
        f"insert into nucleo.facultad (codigo, area, economica)"
        f" values ('{FACULTAD}', 'reparaciones', false);"
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
    for funcion, cambios in EN_SU_LUGAR:
        op.execute(_definicion(funcion, cambios, al_reves=False))
    op.execute(_de_0069()._funcion(EXPORTAR, CUERPO_EXPORTAR))
    filas = ", ".join(
        f"('{accion}', '{texto_}', 'Reparación', array[{_lista(campos)}])"
        for accion, texto_, campos in PROYECCIONES
    )
    op.execute(f"insert into auditoria.proyeccion (accion, texto, recurso, campos) values {filas}")


def downgrade() -> None:
    op.execute(GUARDA)
    acciones = _lista(accion for accion, _, _ in PROYECCIONES)
    op.execute(f"delete from auditoria.proyeccion where accion in ({acciones})")
    op.execute(f"drop function nucleo.{EXPORTAR}(uuid, uuid)")
    for funcion, cambios in reversed(EN_SU_LUGAR):
        op.execute(_definicion(funcion, cambios, al_reves=True))
    for funcion in RUTAS + INTERNAS:
        op.execute(f"drop function {funcion}")
    for tabla in reversed(TABLAS):
        op.execute(f"drop table {tabla}")
    op.execute("drop function nucleo.negar_cuenta_en_reparacion()")
    op.execute("drop function nucleo.negar_cambio_reparacion()")
    op.execute(f"delete from nucleo.paquete_facultad where facultad = '{FACULTAD}'")
    op.execute(f"delete from nucleo.facultad where codigo = '{FACULTAD}'")
