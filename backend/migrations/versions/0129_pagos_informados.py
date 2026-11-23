"""Pagos informados por los vecinos, primer corte (PI-S1 del diseño «Pagos informados» del
repositorio CEM, `doc/diseno/Pagos_Informados.md`, reglas PI-1, PI-2, PI-3, PI-6 y PI-9).

Quien pagó las expensas de una unidad lo informa en CEM con el comprobante. CEM no mueve dinero ni
imputa cobros: deja la constancia que el Consejo confirma después (PI-S2) y coteja con la columna
«Pagos» de la liquidación oficial (PI-S3). La entrada por correo (PI-S4) usa la misma tabla con
`origen = 'correo'`.

**Quién informa** (PI-1, decisión del propietario del 23-11-2026): una persona con un vínculo de
propietario, inquilino u ocupante confirmado, vigente y que no sea un importado pendiente con esa
unidad, en los tres modos; o quien la ayuda con una autorización de `0127` con el alcance
`preparar_reclamos` (informar es preparar una constancia en su nombre, no un acto decisorio). Nadie
más: la facultad de un mandato no alcanza.

**El comprobante** es un documento con cuarentena y antivirus (2c). Lo sube quien informa por
`subir_comprobante_pago` (para una unidad suya): un documento de «Otros», restringido (lo ve en
Documentos solo quien tiene la facultad `documentos.ver`, como las fotos de una reparación, `0124`),
con su versión en cuarentena y una fila en `nucleo.comprobante_pago_subido`. El pago informado puede
citar una versión todavía en cuarentena; si el antivirus la rechaza, ya no se cita. El circuito del
antivirus no cambia. La identidad del archivo es el SHA-256 que guarda `version_documento`, copiado
en el pago (`comprobante_sha256`) para que la base lo haga único.

**Los datos** (`nucleo.pago_informado`): unidad, importe (`NUMERIC(18,2)`, mayor que cero), fecha
del pago (no posterior a hoy en el consorcio), medio (`MEDIOS`), número de operación (obligatorio
salvo en efectivo, PI-2) con su forma normalizada (sin espacios, guiones ni puntos, en minúsculas),
el comprobante (obligatorio salvo en efectivo), el origen, quién informó y cuándo, y el estado.

**Un solo registro por pago (PA07, RN08, PI-3):** dos índices únicos parciales, uno por
consorcio, unidad y número normalizado, y otro por consorcio y SHA-256 del comprobante. Cuentan
solo los registros cuyo estado no está en `ESTADOS_QUE_NO_CUENTAN` (rechazado o anulado, que suma
PI-S2): un pago rechazado o anulado no impide volver a informarlo corregido. `informar_pago` toma el
candado de los pagos informados del consorcio, busca el existente y, si lo hay, lo devuelve con
`ya_informado` sin escribir nada; los índices son la última barrera. Vale entre la app y el correo:
el origen no entra en la clave. El mismo archivo informado para otra unidad se niega
(`comprobante_de_otra_unidad`): devolver ese pago mostraría una unidad ajena.

**`corrige_a`** (nula): el pago rechazado o anulado que este corrige, del mismo consorcio, a lo sumo
una corrección por pago. En este corte no hay rechazo, así que `informar_pago` no la llena; PI-S2
la usa sin cambiar la tabla.

**El estado.** Hoy solo `informado`. PI-S2 suma `confirmado`, `rechazado` y `anulado` ampliando el
check de la columna; el predicado de los índices ya excluye `rechazado` y `anulado`, así que no se
tocan. **Inmutable:** `negar_cambio_pago_informado` niega todo cambio y borrado (también
`truncate`). PI-S2 cambiará el estado solo por sus funciones `security definer`: reemplazará el
disparador por uno que admita únicamente el cambio de `estado` con un indicador de transacción que
fijan esas funciones, y la historia de cada cambio irá a una tabla aparte, como los pasos de una
reparación (`0124`). Nada se borra (PI-9).

**Lectura** (PI-6, `pagos_informados_lectura`): quien tiene `fiscalizacion.ver_panel` ve todos los
del consorcio; los demás, los de las unidades donde tienen el vínculo que cuenta y los que
informaron. Otras unidades, nunca. Quien ayuda, con `consultar_expensas` y solo por el vínculo de la
persona asistida: la facultad no se presta. `comprobante_pago_para_ver` sirve el archivo a quien ve
el pago (disponible; en cuarentena o rechazado, solo a quien lo subió).

**Exportación** (`0069`): la parte nueva `pagos_informados`, con los nombres y nunca los
identificadores de las personas, y el número de operación por `texto_sin_cuenta`.

Downgrade: se niega (`downgrade_con_datos`) si hay pagos informados o comprobantes subidos.

Datos de prueba: SINTETICOS / NO REAL.

Revisión: 0129
Anterior: 0128
"""

import importlib.util
from pathlib import Path

from alembic import op
from sqlalchemy import text

revision = "0129"
down_revision = "0128"
branch_labels = None
depends_on = None

MEDIOS = ("transferencia", "deposito", "efectivo", "mercado_pago", "otro")
MEDIO_SIN_COMPROBANTE = "efectivo"
"""El único medio que admite informar sin comprobante y sin número de operación (PI-2)."""
ORIGENES = ("app", "correo")
ESTADOS = ("informado",)
"""Los estados de este corte; PI-S2 suma `confirmado`, `rechazado` y `anulado`."""
ESTADOS_QUE_NO_CUENTAN = ("rechazado", "anulado")
"""Los que no cuentan para «un solo registro por pago»: el predicado de los índices únicos."""
LARGO_NUMERO_OPERACION = 60
"""Los de `cem_contracts.pagos_informados`; una prueba compara."""
CATEGORIA = "otros"
"""La categoría del documento del comprobante: restringida, como las fotos de una reparación."""
ALCANCE_INFORMAR = "preparar_reclamos"
ALCANCE_LEER = "consultar_expensas"
"""Los alcances de `0127` con que quien ayuda informa y lee (los de las políticas)."""
FACULTAD_VER_TODOS = "fiscalizacion.ver_panel"
ACCION_INFORMAR = "consorcio.pago_informado.informar"
ACCION_COMPROBANTE = "consorcio.pago_informado.subir_comprobante"
TABLAS = ("nucleo.comprobante_pago_subido", "nucleo.pago_informado")
"""En el orden en que se crean: cada una después de las que cita."""
EXPORTAR = "exportar_pagos_informados"
AYUDA_GUC = "nullif(current_setting('app.ayuda_id', true), '')"
"""El de `0127`: la autorización de ayuda de la transacción, si hay."""


def _lista(valores) -> str:
    return ", ".join(f"'{v}'" for v in valores)


NORMALIZADO = "lower(regexp_replace(numero_operacion, '[[:space:].-]', '', 'g'))"
"""El número sin espacios, puntos ni guiones, en minúsculas (PI-3)."""
CUENTA = f"estado not in ({_lista(ESTADOS_QUE_NO_CUENTAN)})"
"""El predicado de los índices únicos: los registros que cuentan como «el pago»."""

REFERENCIAS_NUEVAS = (
    ("nucleo.comprobante_pago_subido", "documento_id"),
    ("nucleo.pago_informado", "comprobante_documento_id"),
)
"""Lo que esta migración suma a `REFERENCIAS` de `0089`: las tablas que citan un documento, con su
columna. Llevan `documento_citado_vigente` (no citan uno quitado o retirado) y entran en
`documento_referenciado` (lo que citan no se quita ni se retira): el comprobante subido queda como
constancia aunque todavía no se haya informado el pago, como el adjunto de una reparación."""
CITAN_DOCUMENTO = "".join(
    f"create trigger documento_citado_vigente before insert or update of {columna} on {tabla}\n"
    f"  for each row execute function nucleo.documento_citado_vigente('{columna}');\n"
    for tabla, columna in REFERENCIAS_NUEVAS
)

TABLAS_SQL = f"""
create function nucleo.negar_cambio_pago_informado()
  returns trigger
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  begin
    raise exception 'un pago informado no se cambia ni se borra' using errcode = 'P0001',
      hint = 'historia_inmutable';
  end
  $$;
revoke all on function nucleo.negar_cambio_pago_informado() from public;

create table nucleo.comprobante_pago_subido (
  consorcio_id          uuid not null references nucleo.consorcio (id),
  version_documento_id  uuid primary key references nucleo.version_documento (id),
  documento_id          uuid not null,
  unidad_id             uuid not null,
  subido_por            uuid not null references nucleo.persona (id),
  subido_en             timestamptz not null default clock_timestamp(),
  unique (consorcio_id, version_documento_id),
  foreign key (consorcio_id, documento_id) references nucleo.documento (consorcio_id, id),
  foreign key (consorcio_id, unidad_id) references nucleo.unidad (consorcio_id, id)
);
create index comprobante_pago_subido_documento
  on nucleo.comprobante_pago_subido (consorcio_id, documento_id);

create table nucleo.pago_informado (
  consorcio_id                  uuid not null references nucleo.consorcio (id),
  id                            uuid primary key default plataforma.uuid_v7(),
  unidad_id                     uuid not null,
  importe                       numeric(18, 2) not null check (importe > 0),
  fecha_pago                    date not null,
  medio                         text not null check (medio in ({_lista(MEDIOS)})),
  numero_operacion              text
    check (numero_operacion is null
           or (length(numero_operacion) between 1 and {LARGO_NUMERO_OPERACION}
               and numero_operacion = btrim(numero_operacion)
               and numero_operacion !~ '[[:cntrl:]]')),
  numero_operacion_normalizado  text generated always as ({NORMALIZADO}) stored,
  comprobante_documento_id      uuid,
  comprobante_version_id        uuid unique,
  comprobante_sha256            text check (comprobante_sha256 ~ '^[0-9a-f]{{64}}$'),
  origen                        text not null check (origen in ({_lista(ORIGENES)})),
  informado_por                 uuid not null references nucleo.persona (id),
  informado_en                  timestamptz not null default clock_timestamp(),
  estado                        text not null default 'informado'
    check (estado in ({_lista(ESTADOS)})),
  corrige_a                     uuid unique,
  unique (consorcio_id, id),
  foreign key (consorcio_id, unidad_id) references nucleo.unidad (consorcio_id, id),
  foreign key (consorcio_id, comprobante_version_id)
    references nucleo.comprobante_pago_subido (consorcio_id, version_documento_id),
  foreign key (consorcio_id, comprobante_documento_id)
    references nucleo.documento (consorcio_id, id),
  foreign key (consorcio_id, corrige_a) references nucleo.pago_informado (consorcio_id, id),
  check (corrige_a is distinct from id),
  -- El número normalizado no queda vacío (un número hecho solo de separadores no es un número).
  check (numero_operacion is null or numero_operacion_normalizado <> ''),
  -- El comprobante va entero (documento, versión y SHA-256) o no va.
  check ((comprobante_version_id is null) = (comprobante_documento_id is null)
         and (comprobante_version_id is null) = (comprobante_sha256 is null)),
  -- PI-2: sin comprobante ni número de operación, solo en efectivo.
  constraint pago_informado_comprobante_del_medio
    check (medio = '{MEDIO_SIN_COMPROBANTE}' or comprobante_version_id is not null),
  constraint pago_informado_numero_del_medio
    check (medio = '{MEDIO_SIN_COMPROBANTE}' or numero_operacion is not null)
);
-- PA07 (PI-3): un solo registro que cuenta por unidad y número de operación, y por archivo.
create unique index pago_informado_un_numero
  on nucleo.pago_informado (consorcio_id, unidad_id, numero_operacion_normalizado)
  where numero_operacion_normalizado is not null and {CUENTA};
create unique index pago_informado_un_archivo
  on nucleo.pago_informado (consorcio_id, comprobante_sha256)
  where comprobante_sha256 is not null and {CUENTA};
create index pago_informado_unidad on nucleo.pago_informado (consorcio_id, unidad_id);
{CITAN_DOCUMENTO}"""


def _protecciones(tabla: str) -> str:
    corto = tabla.removeprefix("nucleo.")
    return f"""
alter table {tabla} enable row level security;
alter table {tabla} force row level security;
create policy por_consorcio on {tabla} for select
  using (consorcio_id = plataforma.consorcio_actual());
revoke all on {tabla} from public, cem_app;
create trigger historia_{corto} before update or delete on {tabla}
  for each row execute function nucleo.negar_cambio_pago_informado();
create trigger historia_{corto}_truncate before truncate on {tabla}
  for each statement execute function nucleo.negar_cambio_pago_informado();
"""


FUNCIONES = f"""
-- Las unidades donde la persona tiene un vínculo de propietario, inquilino u ocupante vigente,
-- confirmado y que no es un importado pendiente (como `unidades_para_reparaciones`, `0124`).
create function nucleo.unidades_para_pagos_informados(c uuid, quien uuid)
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

-- La persona de la sesión (o quien la ayuda, con `alcance`), por la API y con un modo registrado.
-- Devuelve el día del consorcio.
create function nucleo.exigir_sesion_pagos_informados(c uuid, actor uuid, sesion uuid,
                                                      origen_pedido text, alcance text)
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
      raise exception 'sin un modo registrado' using errcode = 'P0001',
        hint = 'modo_no_admitido';
    end if;
    if not nucleo.sesion_de_quien_actua(c, actor, sesion, alcance) then
      if {AYUDA_GUC} is not null then
        raise exception 'la ayuda no alcanza a esto' using errcode = '42501';
      end if;
      raise exception 'la sesión no es válida' using errcode = 'P0001', hint = 'sesion_invalida';
    end if;
    return hoy;
  end
  $$;

-- Quien informa por esa unidad (PI-1): el vínculo que cuenta, o quien ayuda a esa persona con
-- `{ALCANCE_INFORMAR}`. Ninguna facultad alcanza. Devuelve el día del consorcio.
create function nucleo.exigir_informar_pago(c uuid, actor uuid, sesion uuid, origen_pedido text,
                                            unidad uuid)
  returns date
  language plpgsql volatile
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    hoy date;
  begin
    hoy := nucleo.exigir_sesion_pagos_informados(c, actor, sesion, origen_pedido,
                                                 '{ALCANCE_INFORMAR}');
    if unidad is null or not unidad = any(nucleo.unidades_para_pagos_informados(c, actor)) then
      raise exception 'sin un vínculo confirmado con esa unidad' using errcode = '42501';
    end if;
    return hoy;
  end
  $$;

-- Quien lee (PI-6): con `{FACULTAD_VER_TODOS}`, todos (devuelve true); si no, con el vínculo que
-- cuenta en alguna unidad o habiendo informado alguno (false: la lectura filtra). Quien ayuda,
-- con `{ALCANCE_LEER}` y solo por la persona asistida: la facultad no se presta.
create function nucleo.exigir_lectura_pagos_informados(c uuid, actor uuid, sesion uuid,
                                                       origen_pedido text)
  returns boolean
  language plpgsql volatile
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    hoy date;
  begin
    hoy := nucleo.exigir_sesion_pagos_informados(c, actor, sesion, origen_pedido,
                                                 '{ALCANCE_LEER}');
    if {AYUDA_GUC} is null
       and nucleo.tiene_facultad_vigente(c, actor, '{FACULTAD_VER_TODOS}', hoy) then
      return true;
    end if;
    if cardinality(nucleo.unidades_para_pagos_informados(c, actor)) = 0
       and not exists (select 1 from nucleo.pago_informado p
                        where p.consorcio_id = c and p.informado_por = actor) then
      raise exception 'sin un vínculo que permita ver pagos informados' using errcode = '42501';
    end if;
    return false;
  end
  $$;

-- Si esa versión la subió esa persona como comprobante de un pago (para `fijar_version_objeto`).
create function nucleo.comprobante_pago_de(c uuid, version uuid, actor uuid)
  returns boolean
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select exists (select 1 from nucleo.comprobante_pago_subido s
                    where s.consorcio_id = c and s.version_documento_id = version
                      and s.subido_por = actor)
  $$;

create function nucleo.auditar_pago_informado(c uuid, actor uuid, origen_pedido text, accion text,
                                              pago uuid, despues jsonb)
  returns void
  language sql volatile
  set search_path = pg_catalog, pg_temp
  as $$
    insert into auditoria.eventos
      (consorcio_id, actor_persona_id, origen, accion, entidad, entidad_id, despues,
       hash_previo, hash)
    values (c, actor, origen_pedido, accion, 'pago_informado', pago, despues, '', '')
  $$;

-- El comprobante: un documento de «{CATEGORIA}» con su versión en cuarentena, subido por quien va a
-- informar el pago de esa unidad. La ruta guarda el objeto y encola el antivirus en la misma
-- transacción.
create function nucleo.subir_comprobante_pago(unidad uuid, tipo_archivo text,
                                              tamano_nuevo integer, sha256_nuevo text,
                                              actor uuid, origen_pedido text, sesion uuid)
  returns table (nueva_version_id uuid, nueva_clave_objeto text, nuevo_documento_id uuid)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    d uuid;
    v uuid := plataforma.uuid_v7();
    k text;
    hoy date;
    designacion_unidad text;
  begin
    hoy := nucleo.exigir_informar_pago(c, actor, sesion, origen_pedido, unidad);
    select u.designacion into designacion_unidad from nucleo.unidad u
     where u.consorcio_id = c and u.id = unidad;
    insert into nucleo.documento (consorcio_id, categoria, titulo, fecha, cargado_por)
    values (c, '{CATEGORIA}', 'Comprobante de pago: ' || designacion_unidad, hoy, actor)
    returning id into d;
    perform nucleo.auditar_documento(c, 'consorcio.documento.crear', 'documento', d, actor,
      origen_pedido, jsonb_build_object('categoria', '{CATEGORIA}', 'fecha', hoy));
    k := 'cem/' || c || '/' || d || '/' || v;
    insert into nucleo.version_documento
      (consorcio_id, documento_id, id, tipo, tamano, sha256, clave_objeto, subida_por)
    values (c, d, v, tipo_archivo, tamano_nuevo, sha256_nuevo, k, actor);
    perform nucleo.auditar_documento(c, 'consorcio.documento.cargar_version', 'version_documento',
      v, actor, origen_pedido, jsonb_build_object('tipo', tipo_archivo, 'tamano', tamano_nuevo));
    insert into nucleo.comprobante_pago_subido
      (consorcio_id, version_documento_id, documento_id, unidad_id, subido_por)
    values (c, v, d, unidad, actor);
    perform nucleo.auditar_pago_informado(c, actor, origen_pedido, '{ACCION_COMPROBANTE}', null,
      jsonb_build_object('unidad_id', unidad, 'documento_id', d, 'version_id', v));
    return query select v, k, d;
  end
  $$;

-- Informar (PI-1 a PI-3). Un solo registro por pago: bajo el candado de los pagos informados del
-- consorcio, si ya cuenta uno con la misma unidad y el mismo número normalizado, o con el mismo
-- archivo, lo devuelve con `ya_informado` y no escribe nada. El mismo archivo en otra unidad se
-- niega. `corrige_a` queda nula: en este corte no hay pagos rechazados que corregir.
create function nucleo.informar_pago(unidad uuid, importe_nuevo numeric, fecha date,
                                     medio_nuevo text, numero text, comprobante uuid,
                                     actor uuid, origen_pedido text, sesion uuid)
  returns table (pago_id uuid, ya_informado boolean)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    hoy date;
    s nucleo.comprobante_pago_subido;
    suma text;
    normalizado text := lower(regexp_replace(numero, '[[:space:].-]', '', 'g'));
    existente nucleo.pago_informado;
    i uuid;
  begin
    hoy := nucleo.exigir_informar_pago(c, actor, sesion, origen_pedido, unidad);
    if fecha is null or fecha > hoy then
      raise exception 'la fecha del pago es posterior a hoy' using errcode = 'P0001',
        hint = 'fecha_futura';
    end if;
    if nucleo.texto_con_cuenta(numero) then
      raise exception 'el número de operación no puede ser una cuenta' using errcode = 'P0001',
        hint = 'texto_con_cuenta';
    end if;
    if comprobante is not null then
      select * into s from nucleo.comprobante_pago_subido x
       where x.consorcio_id = c and x.version_documento_id = comprobante;
      select v.sha256 into suma from nucleo.version_documento v
        join nucleo.documento d on d.consorcio_id = v.consorcio_id and d.id = v.documento_id
       where v.consorcio_id = c and v.id = comprobante and v.estado <> 'rechazado'
         and d.estado = 'vigente';
      if s.version_documento_id is null or s.subido_por is distinct from actor
         or s.unidad_id is distinct from unidad or suma is null then
        raise exception 'el comprobante no es uno que esta persona subió para esa unidad'
          using errcode = 'P0001', hint = 'comprobante_invalido';
      end if;
    end if;
    -- Un informe a la vez por consorcio: la búsqueda del existente y el alta no se cruzan.
    perform pg_advisory_xact_lock(hashtextextended('pago_informado:' || c::text, 0));
    select * into existente from nucleo.pago_informado p
     where p.consorcio_id = c and p.{CUENTA}
       and ((normalizado is not null and p.unidad_id = unidad
             and p.numero_operacion_normalizado = normalizado)
            or (suma is not null and p.comprobante_sha256 = suma))
     order by (p.unidad_id = unidad) desc, p.informado_en, p.id
     limit 1;
    if existente.id is not null then
      if existente.unidad_id is distinct from unidad then
        raise exception 'ese comprobante ya se informó para otra unidad' using errcode = 'P0001',
          hint = 'comprobante_de_otra_unidad';
      end if;
      return query select existente.id, true;
      return;
    end if;
    insert into nucleo.pago_informado
      (consorcio_id, unidad_id, importe, fecha_pago, medio, numero_operacion,
       comprobante_documento_id, comprobante_version_id, comprobante_sha256, origen,
       informado_por)
    values (c, unidad, importe_nuevo, fecha, medio_nuevo, numero, s.documento_id,
            s.version_documento_id, suma, 'app', actor)
    returning id into i;
    perform nucleo.auditar_pago_informado(c, actor, origen_pedido, '{ACCION_INFORMAR}', i,
      jsonb_build_object('unidad_id', unidad, 'importe', importe_nuevo, 'fecha_pago', fecha,
                         'medio', medio_nuevo, 'version_id', s.version_documento_id));
    return query select i, false;
  end
  $$;

-- Lo que la persona puede ver (PI-6), del más nuevo al más viejo, con el estado del comprobante.
create function nucleo.pagos_informados_lectura(actor uuid, origen_pedido text, sesion uuid)
  returns table (id uuid, unidad_id uuid, unidad_designacion text, importe numeric(18, 2),
                 fecha_pago date, medio text, numero_operacion text, comprobante_version_id uuid,
                 comprobante_estado text, origen text, estado text, corrige_a uuid,
                 informado_por_nombre text, informado_en timestamptz, propio boolean)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    todos boolean;
    propias uuid[];
  begin
    todos := nucleo.exigir_lectura_pagos_informados(c, actor, sesion, origen_pedido);
    propias := nucleo.unidades_para_pagos_informados(c, actor);
    return query
      select p.id, p.unidad_id, u.designacion, p.importe, p.fecha_pago, p.medio,
             p.numero_operacion, p.comprobante_version_id, v.estado, p.origen, p.estado,
             p.corrige_a, pe.nombre, p.informado_en, p.informado_por = actor
        from nucleo.pago_informado p
        join nucleo.unidad u on u.consorcio_id = c and u.id = p.unidad_id
        join nucleo.persona pe on pe.id = p.informado_por
        left join nucleo.version_documento v
          on v.consorcio_id = c and v.id = p.comprobante_version_id
       where p.consorcio_id = c
         and (todos or p.unidad_id = any(propias) or p.informado_por = actor)
       order by p.informado_en desc, p.id desc;
  end
  $$;

-- El comprobante de un pago para verlo: quien ve el pago lo ve si está disponible; quien lo subió,
-- también en cuarentena o rechazado (la ruta decide qué responde). Ninguna fila: no existe o no se
-- puede leer.
create function nucleo.comprobante_pago_para_ver(pago uuid, actor uuid, origen_pedido text,
                                                 sesion uuid)
  returns table (estado text, tipo text, clave_objeto text, version_objeto text, propia boolean)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    todos boolean;
    propias uuid[];
  begin
    todos := nucleo.exigir_lectura_pagos_informados(c, actor, sesion, origen_pedido);
    propias := nucleo.unidades_para_pagos_informados(c, actor);
    return query
      select v.estado, v.tipo, v.clave_objeto, v.version_objeto, v.subida_por = actor
        from nucleo.pago_informado p
        join nucleo.version_documento v
          on v.consorcio_id = c and v.id = p.comprobante_version_id
        join nucleo.documento d on d.consorcio_id = c and d.id = p.comprobante_documento_id
       where p.consorcio_id = c and p.id = pago and d.estado = 'vigente'
         and (todos or p.unidad_id = any(propias) or p.informado_por = actor)
         and (v.estado = 'disponible' or v.subida_por = actor);
  end
  $$;
"""

RUTAS = (
    "nucleo.subir_comprobante_pago(uuid, text, integer, text, uuid, text, uuid)",
    "nucleo.informar_pago(uuid, numeric, date, text, text, uuid, uuid, text, uuid)",
    "nucleo.pagos_informados_lectura(uuid, text, uuid)",
    "nucleo.comprobante_pago_para_ver(uuid, uuid, text, uuid)",
)
INTERNAS = (
    "nucleo.auditar_pago_informado(uuid, uuid, text, text, uuid, jsonb)",
    "nucleo.comprobante_pago_de(uuid, uuid, uuid)",
    "nucleo.exigir_lectura_pagos_informados(uuid, uuid, uuid, text)",
    "nucleo.exigir_informar_pago(uuid, uuid, uuid, text, uuid)",
    "nucleo.exigir_sesion_pagos_informados(uuid, uuid, uuid, text, text)",
    "nucleo.unidades_para_pagos_informados(uuid, uuid)",
)
"""En el orden en que se pueden borrar: cada una antes de las que llama."""

REFERENCIADO = "nucleo.documento_referenciado(uuid, uuid)"
REFERENCIADO_CAMBIOS = (
    (
        "        or exists (select 1 from nucleo.importacion_liquidacion x\n",
        "        or exists (select 1 from nucleo.comprobante_pago_subido x\n"
        "                    where x.consorcio_id = c and x.documento_id = documento)\n"
        "        or exists (select 1 from nucleo.pago_informado x\n"
        "                    where x.consorcio_id = c and x.comprobante_documento_id = documento)\n"
        "        or exists (select 1 from nucleo.importacion_liquidacion x\n",
    ),
)
"""`REFERENCIAS_NUEVAS` en `documento_referenciado`."""
FIJAR_OBJETO = "nucleo.fijar_version_objeto(uuid, text, uuid, text)"
FIJAR_CAMBIOS = (
    (
        "          and not nucleo.adjunto_de_reparacion(c, version, actor) and not exists (\n",
        "          and not nucleo.adjunto_de_reparacion(c, version, actor)\n"
        "          -- 0129: ni el comprobante que esa persona subió para informar un pago.\n"
        "          and not nucleo.comprobante_pago_de(c, version, actor) and not exists (\n",
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


CUERPO_EXPORTAR = f"""
    hoy := nucleo.hoy_del_consorcio(c);
    return query
      select 'comprobante_pago'::text,
             jsonb_build_object('version_documento_id', s.version_documento_id,
                                'documento_id', s.documento_id,
                                'unidad_designacion', u.designacion,
                                'subido_por_nombre', {_nombre("s.subido_por")},
                                'subido_en', s.subido_en)
        from nucleo.comprobante_pago_subido s
        join nucleo.unidad u on u.consorcio_id = s.consorcio_id and u.id = s.unidad_id
       where s.consorcio_id = c
       order by s.subido_en, s.version_documento_id;
    return query
      select 'pago_informado'::text,
             jsonb_build_object('id', p.id, 'unidad_designacion', u.designacion,
                                'importe', p.importe, 'fecha_pago', p.fecha_pago,
                                'medio', p.medio,
                                'numero_operacion', nucleo.texto_sin_cuenta(p.numero_operacion),
                                'comprobante_documento_id', p.comprobante_documento_id,
                                'comprobante_version_id', p.comprobante_version_id,
                                'comprobante_sha256', p.comprobante_sha256,
                                'origen', p.origen, 'estado', p.estado,
                                'corrige_a', p.corrige_a,
                                'informado_por_nombre', {_nombre("p.informado_por")},
                                'informado_en', p.informado_en)
        from nucleo.pago_informado p
        join nucleo.unidad u on u.consorcio_id = p.consorcio_id and u.id = p.unidad_id
       where p.consorcio_id = c
       order by p.informado_en, p.id;
"""

GUARDA = """
do $guarda$
begin
  if exists (select 1 from nucleo.pago_informado)
     or exists (select 1 from nucleo.comprobante_pago_subido) then
    raise exception '0129 no baja: hay pagos informados o comprobantes subidos'
      using errcode = 'P0001', hint = 'downgrade_con_datos';
  end if;
end
$guarda$;
"""

PROYECCIONES = (
    (ACCION_INFORMAR, "Informó un pago", ["unidad_id", "importe", "fecha_pago", "medio"]),
    (ACCION_COMPROBANTE, "Subió el comprobante de un pago", ["unidad_id", "documento_id"]),
)


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
            raise RuntimeError(f"0129: {funcion} no tiene exactamente una vez {antes!r}")
        definicion = definicion.replace(antes, despues)
    return definicion


def upgrade() -> None:
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
        f"('{accion}', '{texto_}', 'Pago informado', array[{_lista(campos)}])"
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
    op.execute("drop function nucleo.negar_cambio_pago_informado()")
