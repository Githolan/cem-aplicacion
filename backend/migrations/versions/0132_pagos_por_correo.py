"""Pagos informados, cuarto corte: la entrada por correo (PI-S4 del diseño «Pagos informados» del
repositorio CEM, `doc/diseno/Pagos_Informados.md`, reglas PC-1 a PC-5; decisiones del propietario
del 23-11-2026, Q1 y Q3, y la revisión de PC-4, PI-D22).

Quien pagó manda el comprobante a la dirección de pagos del entorno. Cloudflare Email Routing lo
pasa al Worker, que lo entrega entero a `POST /api/v1/entrada/correo-pagos` con la clave
compartida. La ruta identifica a quien envía (DMARC alineado, PC-3) y llama a estas funciones sin
sesión: no hay una persona que haya entrado, sino un correo que pasó la autenticación.

**Quién manda** (`remitente_de_pagos_por_correo`): la persona con esa dirección y un vínculo de
propietario, inquilino u ocupante confirmado y vigente (PI-1, Q1), en un solo consorcio. Con
unidades en más de uno, la ruta rebota el correo (PI-D22): no hay forma de saber de cuál es el
pago. Sin cuenta o sin vínculo, también rebota (Q3). Quien ayuda no entra por correo.

**Cada adjunto** (`recibir_pago_por_correo`) entra como un documento de «Otros», restringido, con
su versión en cuarentena, como el comprobante que se sube en la app (`0129`), y una fila en
`nucleo.pago_por_correo` en `por_completar`. Un solo registro por pago también acá (PA07, PI-3),
bajo el mismo candado que `informar_pago`: el mismo archivo con el mismo identificador de mensaje
(un reintento de entrega) devuelve la fila existente; el mismo archivo ya en un pago vigente del
consorcio no guarda nada (`ya_informado`); el mismo archivo de la misma persona todavía por
completar, tampoco (`ya_recibido`).

**La lectura** (RN21: la IA propone, no decide). Cuando el antivirus acepta la versión, el
análisis encola `pagos_informados.leer_correo` (con `CEM_LECTURA_COMPROBANTES` encendida), que
guarda importe, moneda, número de operación y fecha **una sola vez** en la misma fila
(`guardar_lectura_pago_por_correo`). De la cuenta no se guarda nada; una lectura cuyo número de
operación es una cuenta se descarta entera. La persona ve lo leído como propuesta.

**Completar** (`informar_pago_por_correo`): la persona elige la unidad, revisa los datos y lo
informa. Se crea el `comprobante_pago_subido` de esa unidad y se llama a `informar_pago`, que hace
todo lo de siempre (barrera, PA07, auditoría); el pago queda con `origen = 'correo'` porque su
comprobante vino de un correo (`origen_del_comprobante`). La fila pasa a `informado` con el pago
(el nuevo o el que ya estaba). **Descartar** (`descartar_pago_por_correo`) la deja `descartada`:
un adjunto que no era un comprobante (un logo, otro archivo). Nada se borra.

**La fila cambia poco:** el disparador `cambio_pago_por_correo` deja escribir la lectura una vez
y pasar de `por_completar` a `informado` o `descartado`; nada más. No se borra.

**Lectura** (`pagos_por_correo_lectura`, `comprobante_pago_por_correo_para_ver`): solo la persona
que mandó el correo, por la API y sin ayuda; lo que está por completar.

Downgrade: se niega (`downgrade_con_datos`) si hay pagos recibidos por correo.

Datos de prueba: SINTETICOS / NO REAL.

Revisión: 0132
Anterior: 0131
"""

import importlib.util
from pathlib import Path

from alembic import op
from sqlalchemy import text

revision = "0132"
down_revision = "0131"
branch_labels = None
depends_on = None

ESTADOS = ("por_completar", "informado", "descartado")
ESTADO_INICIAL = "por_completar"
LARGO_ASUNTO = 200
LARGO_MENSAJE = 998
"""El largo máximo de una línea de encabezado (RFC 5322): el identificador del mensaje entra."""
LARGO_NUMERO_LEIDO = 60
"""El de `nucleo.lectura_comprobante` (`0077`) y `LARGO_NUMERO_OPERACION` del contrato."""
ERRORES_LECTURA = ("falla_del_modelo", "respuesta_invalida")
"""Los de `lectura_comprobantes.Lectura`."""
TIPOS_VINCULO = ("propietario", "inquilino", "ocupante")
"""PI-1 y Q1: los de `unidades_para_pagos_informados` (`0129`)."""
CATEGORIA = "otros"
TITULO = "Comprobante de pago recibido por correo"
ALCANCE_INFORMAR = "preparar_reclamos"
"""El de `0129`: `exigir_sesion_pagos_informados` lo pide; la ayuda se niega después."""
ESTADOS_QUE_NO_CUENTAN = ("rechazado", "anulado")
MAXIMO_POR_DIA = 30
"""`MAXIMO_COMPROBANTES_POR_DIA` del contrato: adjuntos nuevos por persona en 24 horas; una prueba
compara."""
ACCION_RECIBIR = "consorcio.pago_por_correo.recibir"
ACCION_DESCARTAR = "consorcio.pago_por_correo.descartar"
EXPORTAR = "exportar_pagos_informados"
AYUDA_GUC = "nullif(current_setting('app.ayuda_id', true), '')"
TABLA = "nucleo.pago_por_correo"


def _lista(valores) -> str:
    return ", ".join(f"'{v}'" for v in valores)


def _modulo(nombre: str):
    """Una migración anterior, ya aplicada: sus textos no cambian."""
    ruta = Path(__file__).with_name(nombre)
    spec = importlib.util.spec_from_file_location(ruta.stem, ruta)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


REFERENCIAS_NUEVAS = ((TABLA, "documento_id"),)
"""Lo que esta migración suma a `REFERENCIAS` de `0089`: el documento del adjunto queda citado
(no se quita ni se retira) mientras exista la fila, como el comprobante subido en la app."""
LECTURA = (
    "lectura_en", "lectura_modelo", "lectura_legible", "lectura_error", "lectura_importe",
    "lectura_moneda", "lectura_numero", "lectura_fecha",
)  # fmt: skip
FIJAS = (
    "consorcio_id", "id", "persona_id", "mensaje_id", "asunto", "recibido_en", "documento_id",
    "version_documento_id", "sha256",
)  # fmt: skip

TABLAS_SQL = f"""
create table {TABLA} (
  consorcio_id          uuid not null references nucleo.consorcio (id),
  id                    uuid primary key default plataforma.uuid_v7(),
  persona_id            uuid not null references nucleo.persona (id),
  mensaje_id            text not null
    check (length(mensaje_id) between 1 and {LARGO_MENSAJE} and mensaje_id !~ '[[:cntrl:]]'),
  asunto                text
    check (asunto is null
           or (length(asunto) between 1 and {LARGO_ASUNTO} and asunto !~ '[[:cntrl:]]')),
  recibido_en           timestamptz not null default clock_timestamp(),
  documento_id          uuid not null,
  version_documento_id  uuid not null unique references nucleo.version_documento (id),
  sha256                text not null check (sha256 ~ '^[0-9a-f]{{64}}$'),
  estado                text not null default '{ESTADO_INICIAL}'
    check (estado in ({_lista(ESTADOS)})),
  pago_id               uuid,
  lectura_en            timestamptz,
  lectura_modelo        text check (length(lectura_modelo) between 1 and 200),
  lectura_legible       boolean,
  lectura_error         text check (lectura_error in ({_lista(ERRORES_LECTURA)})),
  lectura_importe       numeric(18, 2) check (lectura_importe > 0),
  lectura_moneda        text check (lectura_moneda ~ '^[A-Z]{{3}}$'),
  lectura_numero        text
    check (length(lectura_numero) between 1 and {LARGO_NUMERO_LEIDO}
           and lectura_numero !~ '[[:cntrl:]]'),
  lectura_fecha         date,
  unique (consorcio_id, id),
  -- Un reintento de entrega del mismo correo no duplica el adjunto.
  unique (consorcio_id, mensaje_id, sha256),
  foreign key (consorcio_id, documento_id) references nucleo.documento (consorcio_id, id),
  foreign key (consorcio_id, pago_id) references nucleo.pago_informado (consorcio_id, id),
  check ((estado = 'informado') = (pago_id is not null)),
  -- Sin lectura, ningún dato leído; leído sin legible, ningún dato tampoco.
  check (lectura_en is not null
         or (lectura_modelo is null and lectura_legible is null and lectura_error is null
             and lectura_importe is null and lectura_moneda is null and lectura_numero is null
             and lectura_fecha is null)),
  check (lectura_en is null or lectura_legible is not null),
  check (lectura_legible is not false
         or (lectura_importe is null and lectura_moneda is null and lectura_numero is null
             and lectura_fecha is null))
);
create index pago_por_correo_persona on {TABLA} (consorcio_id, persona_id, estado);
create index pago_por_correo_sha256 on {TABLA} (consorcio_id, sha256);
create trigger documento_citado_vigente before insert or update of documento_id on {TABLA}
  for each row execute function nucleo.documento_citado_vigente('documento_id');

-- La lectura se escribe una vez; el estado pasa de por completar a informado o descartado.
create function nucleo.cambio_pago_por_correo()
  returns trigger
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    antes jsonb := to_jsonb(old);
    despues jsonb := to_jsonb(new);
    lectura text[] := array[{_lista(LECTURA)}];
    fijas text[] := array[{_lista(FIJAS)}];
  begin
    if (antes - lectura - 'estado' - 'pago_id') is distinct from
       (despues - lectura - 'estado' - 'pago_id') then
      raise exception 'lo recibido por correo no cambia' using errcode = 'P0001',
        hint = 'historia_inmutable';
    end if;
    if old.lectura_en is not null
       and (antes - fijas - 'estado' - 'pago_id') is distinct from
           (despues - fijas - 'estado' - 'pago_id') then
      raise exception 'la lectura se guarda una sola vez' using errcode = 'P0001',
        hint = 'historia_inmutable';
    end if;
    if (old.estado, old.pago_id) is distinct from (new.estado, new.pago_id)
       and old.estado <> '{ESTADO_INICIAL}' then
      raise exception 'lo recibido por correo ya se informó o se descartó'
        using errcode = 'P0001', hint = 'historia_inmutable';
    end if;
    return new;
  end
  $$;
revoke all on function nucleo.cambio_pago_por_correo() from public;

alter table {TABLA} enable row level security;
alter table {TABLA} force row level security;
create policy por_consorcio on {TABLA} for select
  using (consorcio_id = plataforma.consorcio_actual());
revoke all on {TABLA} from public, cem_app;
create trigger cambio_pago_por_correo before update on {TABLA}
  for each row execute function nucleo.cambio_pago_por_correo();
create trigger historia_pago_por_correo before delete on {TABLA}
  for each row execute function nucleo.negar_cambio_pago_informado();
create trigger historia_pago_por_correo_truncate before truncate on {TABLA}
  for each statement execute function nucleo.negar_cambio_pago_informado();
"""

FUNCIONES = f"""
-- Quién manda el correo: la persona con esa dirección y los consorcios donde tiene un vínculo que
-- cuenta (PI-1), cada uno con su fecha. Ninguna fila: no hay cuenta con esa dirección.
create function nucleo.remitente_de_pagos_por_correo(correo_remitente text)
  returns table (persona_id uuid, consorcios uuid[])
  language sql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
    select p.id,
           coalesce((select array_agg(distinct v.consorcio_id order by v.consorcio_id)
                       from nucleo.vinculo v
                      where v.persona_id = p.id
                        and v.tipo in ({_lista(TIPOS_VINCULO)})
                        and v.confirmado_en is not null
                        and daterange(v.vigente_desde, v.vigente_hasta)
                            @> nucleo.hoy_del_consorcio(v.consorcio_id)), '{{}}')
      from nucleo.persona p
     where p.correo = lower(btrim(correo_remitente)) and p.eliminado_en is null
  $$;

-- La entrada por correo: el ámbito es el de quien manda (la persona que identificó el DMARC, sin
-- sesión ni ayuda), con el consorcio fijado, un modo registrado y un vínculo que cuenta. Nunca un
-- ámbito sin persona: la API no abre ámbitos de trabajo. Devuelve el día del consorcio.
create function nucleo.exigir_entrada_por_correo(c uuid, remitente uuid)
  returns date
  language plpgsql volatile
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    hoy date;
  begin
    if c is null or remitente is null or plataforma.persona_actual() is distinct from remitente
       or {AYUDA_GUC} is not null then
      raise exception 'solo la entrada por correo' using errcode = '42501';
    end if;
    hoy := nucleo.hoy_del_consorcio(c);
    perform pg_advisory_xact_lock_shared(hashtextextended('modo:' || c::text, 0));
    if not exists (
         select 1 from nucleo.modo_operacion m
          where m.consorcio_id = c and m.vigente_desde <= hoy
            and (m.vigente_hasta is null or m.vigente_hasta > hoy)) then
      raise exception 'sin un modo registrado' using errcode = 'P0001',
        hint = 'modo_no_admitido';
    end if;
    if cardinality(nucleo.unidades_para_pagos_informados(c, remitente)) = 0 then
      raise exception 'sin un vínculo que permita informar pagos' using errcode = 'P0001',
        hint = 'sin_vinculo';
    end if;
    return hoy;
  end
  $$;

create function nucleo.auditar_pago_por_correo(c uuid, actor uuid, accion text, recibido uuid,
                                               despues jsonb)
  returns void
  language sql volatile
  set search_path = pg_catalog, pg_temp
  as $$
    insert into auditoria.eventos
      (consorcio_id, actor_persona_id, origen, accion, entidad, entidad_id, despues,
       hash_previo, hash)
    values (c, actor, 'api', accion, 'pago_por_correo', recibido, despues, '', '')
  $$;

-- Un adjunto del correo. `resultado`: `nuevo` (la ruta guarda el objeto y encola el antivirus),
-- `ya_recibido` (un reintento, o el mismo archivo todavía por completar) o `ya_informado` (el
-- mismo archivo ya está en un pago vigente: no se guarda nada; PA07).
create function nucleo.recibir_pago_por_correo(remitente uuid, mensaje text, asunto_correo text,
                                               tipo_archivo text, tamano_nuevo integer,
                                               sha256_nuevo text)
  returns table (recibido_id uuid, nueva_version_id uuid, nueva_clave_objeto text,
                 resultado text)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    hoy date;
    previo {TABLA};
    d uuid;
    v uuid := plataforma.uuid_v7();
    k text;
    r uuid;
  begin
    hoy := nucleo.exigir_entrada_por_correo(c, remitente);
    -- El candado de `informar_pago`: la búsqueda del pago existente y el alta no se cruzan.
    perform pg_advisory_xact_lock(hashtextextended('pago_informado:' || c::text, 0));
    select * into previo from {TABLA} x
     where x.consorcio_id = c and x.mensaje_id = mensaje and x.sha256 = sha256_nuevo;
    if previo.id is not null then
      return query select previo.id, previo.version_documento_id, null::text, 'ya_recibido';
      return;
    end if;
    if exists (select 1 from nucleo.pago_informado p
                where p.consorcio_id = c and p.comprobante_sha256 = sha256_nuevo
                  and p.estado not in ({_lista(ESTADOS_QUE_NO_CUENTAN)})) then
      return query select null::uuid, null::uuid, null::text, 'ya_informado';
      return;
    end if;
    select * into previo from {TABLA} x
     where x.consorcio_id = c and x.persona_id = remitente and x.sha256 = sha256_nuevo
       and x.estado = '{ESTADO_INICIAL}'
     order by x.recibido_en limit 1;
    if previo.id is not null then
      return query select previo.id, previo.version_documento_id, null::text, 'ya_recibido';
      return;
    end if;
    if (select count(*) from {TABLA} x
         where x.consorcio_id = c and x.persona_id = remitente
           and x.recibido_en > clock_timestamp() - interval '1 day') >= {MAXIMO_POR_DIA} then
      raise exception 'demasiados comprobantes en un día' using errcode = 'P0001',
        hint = 'demasiados_comprobantes';
    end if;
    insert into nucleo.documento (consorcio_id, categoria, titulo, fecha, cargado_por)
    values (c, '{CATEGORIA}', '{TITULO}', hoy, remitente)
    returning id into d;
    perform nucleo.auditar_documento(c, 'consorcio.documento.crear', 'documento', d, remitente,
      'api', jsonb_build_object('categoria', '{CATEGORIA}', 'fecha', hoy));
    k := 'cem/' || c || '/' || d || '/' || v;
    insert into nucleo.version_documento
      (consorcio_id, documento_id, id, tipo, tamano, sha256, clave_objeto, subida_por)
    values (c, d, v, tipo_archivo, tamano_nuevo, sha256_nuevo, k, remitente);
    perform nucleo.auditar_documento(c, 'consorcio.documento.cargar_version', 'version_documento',
      v, remitente, 'api', jsonb_build_object('tipo', tipo_archivo, 'tamano', tamano_nuevo));
    insert into {TABLA}
      (consorcio_id, persona_id, mensaje_id, asunto, documento_id, version_documento_id, sha256)
    values (c, remitente, mensaje, asunto_correo, d, v, sha256_nuevo)
    returning id into r;
    perform nucleo.auditar_pago_por_correo(c, remitente, '{ACCION_RECIBIR}', r,
      jsonb_build_object('documento_id', d, 'version_id', v));
    return query select r, v, k, 'nuevo';
  end
  $$;

-- El `VersionId` del almacén para la versión de un adjunto recién guardado (como
-- `fijar_version_objeto`, en el ámbito de quien manda).
create function nucleo.fijar_version_pago_por_correo(version uuid, version_almacen text,
                                                     remitente uuid)
  returns void
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
  begin
    perform nucleo.exigir_entrada_por_correo(c, remitente);
    update nucleo.version_documento x set version_objeto = version_almacen
     where x.consorcio_id = c and x.id = version and x.subida_por = remitente
       and x.estado = 'en_cuarentena' and x.version_objeto is null
       and exists (select 1 from {TABLA} r
                    where r.consorcio_id = c and r.version_documento_id = version);
    if not found then
      raise exception 'la versión no existe' using errcode = 'P0001', hint = 'no_encontrado';
    end if;
  end
  $$;

-- Para el análisis (solo un trabajo): si la versión es un adjunto recibido por correo.
create function nucleo.version_es_pago_por_correo(version uuid)
  returns boolean
  language plpgsql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
  begin
    perform nucleo.exigir_trabajo(c);
    return exists (select 1 from {TABLA} r
                    where r.consorcio_id = c and r.version_documento_id = version);
  end
  $$;

-- Lo que leyó la IA, una sola vez y solo mientras está por completar. De la cuenta, nada; un
-- número de operación que es una cuenta, o demasiado largo, no se guarda.
create function nucleo.guardar_lectura_pago_por_correo(version uuid, modelo text, legible boolean,
                                                       error text, importe numeric, moneda text,
                                                       numero text, fecha date)
  returns boolean
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    n text := nullif(btrim(numero), '');
  begin
    perform nucleo.exigir_trabajo(c);
    if n is not null and (nucleo.texto_con_cuenta(n) or length(n) > {LARGO_NUMERO_LEIDO}
                          or n ~ '[[:cntrl:]]') then
      n := null;
    end if;
    update {TABLA} r
       set lectura_en = clock_timestamp(), lectura_modelo = modelo, lectura_legible = legible,
           lectura_error = error,
           lectura_importe = case when legible then importe end,
           lectura_moneda = case when legible then moneda end,
           lectura_numero = case when legible then n end,
           lectura_fecha = case when legible then fecha end
     where r.consorcio_id = c and r.version_documento_id = version
       and r.lectura_en is null and r.estado = '{ESTADO_INICIAL}';
    return found;
  end
  $$;

-- La persona que mandó el correo, con su sesión y sin ayuda. Devuelve el día del consorcio.
create function nucleo.exigir_remitente_pagos_por_correo(c uuid, actor uuid, sesion uuid,
                                                         origen_pedido text)
  returns date
  language plpgsql volatile
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    hoy date;
  begin
    hoy := nucleo.exigir_sesion_pagos_informados(c, actor, sesion, origen_pedido,
                                                 '{ALCANCE_INFORMAR}');
    if {AYUDA_GUC} is not null then
      raise exception 'lo recibido por correo lo completa quien lo mandó' using errcode = '42501';
    end if;
    return hoy;
  end
  $$;

-- Lo que la persona mandó por correo y le falta completar, del más nuevo al más viejo, con lo que
-- propone la lectura.
create function nucleo.pagos_por_correo_lectura(actor uuid, origen_pedido text, sesion uuid)
  returns table (id uuid, recibido_en timestamptz, asunto text, comprobante_version_id uuid,
                 comprobante_tipo text, comprobante_estado text, leido boolean,
                 legible boolean, importe numeric(18, 2), moneda text, numero_operacion text,
                 fecha_pago date)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
  begin
    perform nucleo.exigir_remitente_pagos_por_correo(c, actor, sesion, origen_pedido);
    return query
      select r.id, r.recibido_en, r.asunto, r.version_documento_id, v.tipo, v.estado,
             r.lectura_en is not null, r.lectura_legible, r.lectura_importe, r.lectura_moneda,
             r.lectura_numero, r.lectura_fecha
        from {TABLA} r
        join nucleo.version_documento v
          on v.consorcio_id = c and v.id = r.version_documento_id
       where r.consorcio_id = c and r.persona_id = actor and r.estado = '{ESTADO_INICIAL}'
       order by r.recibido_en desc, r.id desc;
  end
  $$;

-- El archivo de lo recibido, para quien lo mandó (también en cuarentena: la ruta decide).
create function nucleo.comprobante_pago_por_correo_para_ver(recibido uuid, actor uuid,
                                                            origen_pedido text, sesion uuid)
  returns table (estado text, tipo text, clave_objeto text, version_objeto text)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
  begin
    perform nucleo.exigir_remitente_pagos_por_correo(c, actor, sesion, origen_pedido);
    return query
      select v.estado, v.tipo, v.clave_objeto, v.version_objeto
        from {TABLA} r
        join nucleo.version_documento v
          on v.consorcio_id = c and v.id = r.version_documento_id
        join nucleo.documento d on d.consorcio_id = c and d.id = r.documento_id
       where r.consorcio_id = c and r.id = recibido and r.persona_id = actor
         and r.estado = '{ESTADO_INICIAL}' and d.estado = 'vigente';
  end
  $$;

-- El origen de un pago: `correo` si su comprobante llegó por correo; si no, `app`.
create function nucleo.origen_del_comprobante(c uuid, version uuid)
  returns text
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select case when exists (select 1 from {TABLA} r
                              where r.consorcio_id = c and r.version_documento_id = version)
                then 'correo' else 'app' end
  $$;

-- Completar lo recibido: la persona elige la unidad y los datos. El comprobante queda como subido
-- para esa unidad y `informar_pago` hace el resto (barrera, PA07, auditoría). Si ya se había
-- informado, responde ese pago.
create function nucleo.informar_pago_por_correo(recibido uuid, unidad uuid, importe_nuevo numeric,
                                                fecha date, medio_nuevo text, numero text,
                                                actor uuid, origen_pedido text, sesion uuid)
  returns table (pago_id uuid, ya_informado boolean)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    r {TABLA};
    i uuid;
    ya boolean;
  begin
    perform nucleo.exigir_remitente_pagos_por_correo(c, actor, sesion, origen_pedido);
    perform nucleo.exigir_informar_pago(c, actor, sesion, origen_pedido, unidad);
    select * into r from {TABLA} x
     where x.consorcio_id = c and x.id = recibido and x.persona_id = actor
     for update;
    if r.id is null then
      raise exception 'no existe' using errcode = 'P0001', hint = 'no_encontrado';
    end if;
    if r.estado = 'informado' then
      return query select r.pago_id, true;
      return;
    end if;
    if r.estado <> '{ESTADO_INICIAL}' then
      raise exception 'lo recibido se descartó' using errcode = 'P0001',
        hint = 'pago_por_correo_descartado';
    end if;
    insert into nucleo.comprobante_pago_subido
      (consorcio_id, version_documento_id, documento_id, unidad_id, subido_por)
    values (c, r.version_documento_id, r.documento_id, unidad, actor);
    select p.pago_id, p.ya_informado into i, ya
      from nucleo.informar_pago(unidad, importe_nuevo, fecha, medio_nuevo, numero,
                                r.version_documento_id, null, actor, origen_pedido, sesion) p;
    update {TABLA} x set estado = 'informado', pago_id = i
     where x.consorcio_id = c and x.id = recibido;
    return query select i, ya;
  end
  $$;

-- Descartar lo recibido que no era un comprobante. Dos veces da lo mismo.
create function nucleo.descartar_pago_por_correo(recibido uuid, actor uuid, origen_pedido text,
                                                 sesion uuid)
  returns void
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    r {TABLA};
  begin
    perform nucleo.exigir_remitente_pagos_por_correo(c, actor, sesion, origen_pedido);
    select * into r from {TABLA} x
     where x.consorcio_id = c and x.id = recibido and x.persona_id = actor
     for update;
    if r.id is null then
      raise exception 'no existe' using errcode = 'P0001', hint = 'no_encontrado';
    end if;
    if r.estado = 'descartado' then
      return;
    end if;
    if r.estado <> '{ESTADO_INICIAL}' then
      raise exception 'lo recibido ya se informó' using errcode = 'P0001',
        hint = 'pago_por_correo_informado';
    end if;
    update {TABLA} x set estado = 'descartado' where x.consorcio_id = c and x.id = recibido;
    perform nucleo.auditar_pago_por_correo(c, actor, '{ACCION_DESCARTAR}', recibido,
      jsonb_build_object('version_id', r.version_documento_id));
  end
  $$;
"""

RUTAS = (
    "nucleo.remitente_de_pagos_por_correo(text)",
    "nucleo.recibir_pago_por_correo(uuid, text, text, text, integer, text)",
    "nucleo.fijar_version_pago_por_correo(uuid, text, uuid)",
    "nucleo.version_es_pago_por_correo(uuid)",
    "nucleo.guardar_lectura_pago_por_correo(uuid, text, boolean, text, numeric, text, text, date)",
    "nucleo.pagos_por_correo_lectura(uuid, text, uuid)",
    "nucleo.comprobante_pago_por_correo_para_ver(uuid, uuid, text, uuid)",
    "nucleo.informar_pago_por_correo(uuid, uuid, numeric, date, text, text, uuid, text, uuid)",
    "nucleo.descartar_pago_por_correo(uuid, uuid, text, uuid)",
)
INTERNAS = (
    "nucleo.origen_del_comprobante(uuid, uuid)",
    "nucleo.exigir_remitente_pagos_por_correo(uuid, uuid, uuid, text)",
    "nucleo.auditar_pago_por_correo(uuid, uuid, text, uuid, jsonb)",
    "nucleo.exigir_entrada_por_correo(uuid, uuid)",
)
"""En el orden en que se pueden borrar: cada una antes de las que llama."""

INFORMAR = "nucleo.informar_pago(uuid, numeric, date, text, text, uuid, uuid, uuid, text, uuid)"
INFORMAR_CAMBIOS = (
    (
        "            s.version_documento_id, suma, 'app', actor, corrige)\n",
        "            s.version_documento_id, suma,\n"
        "            nucleo.origen_del_comprobante(c, s.version_documento_id), actor, corrige)\n",
    ),
)
"""El origen del pago sale de su comprobante (`origen_del_comprobante`) en vez de ser siempre
`app`; misma firma, `create or replace`."""
REFERENCIADO = "nucleo.documento_referenciado(uuid, uuid)"
REFERENCIADO_CAMBIOS = (
    (
        "        or exists (select 1 from nucleo.comprobante_pago_subido x\n",
        f"        or exists (select 1 from {TABLA} x\n"
        "                    where x.consorcio_id = c and x.documento_id = documento)\n"
        "        or exists (select 1 from nucleo.comprobante_pago_subido x\n",
    ),
)
"""`REFERENCIAS_NUEVAS` en `documento_referenciado`."""
EN_SU_LUGAR = ((INFORMAR, INFORMAR_CAMBIOS), (REFERENCIADO, REFERENCIADO_CAMBIOS))

CUERPO_EXPORTAR_CORREO = f"""    return query
      select 'pago_por_correo'::text,
             jsonb_build_object('id', r.id,
                                'persona_nombre',
                                nucleo.nombre_para_exportar(c, r.persona_id, hoy),
                                'recibido_en', r.recibido_en, 'asunto', r.asunto,
                                'documento_id', r.documento_id,
                                'version_documento_id', r.version_documento_id,
                                'sha256', r.sha256, 'estado', r.estado, 'pago_id', r.pago_id,
                                'lectura_en', r.lectura_en, 'lectura_legible', r.lectura_legible,
                                'lectura_importe', r.lectura_importe,
                                'lectura_moneda', r.lectura_moneda,
                                'lectura_fecha', r.lectura_fecha)
        from {TABLA} r
       where r.consorcio_id = c
       order by r.recibido_en, r.id;
"""
"""Sin el identificador del mensaje (puede llevar la dirección de quien manda) ni el número leído
(se exporta el del pago, cuando se informa)."""

GUARDA = f"""
do $guarda$
begin
  if exists (select 1 from {TABLA}) then
    raise exception '0132 no baja: hay pagos recibidos por correo'
      using errcode = 'P0001', hint = 'downgrade_con_datos';
  end if;
end
$guarda$;
"""

PROYECCIONES = (
    (ACCION_RECIBIR, "Mandó un comprobante de pago por correo", ["documento_id"]),
    (ACCION_DESCARTAR, "Descartó un comprobante recibido por correo", ["version_id"]),
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
            raise RuntimeError(f"0132: {funcion} no tiene exactamente una vez {antes!r}")
        definicion = definicion.replace(antes, despues)
    return definicion


def _exportar(cuerpo_extra: str) -> str:
    m0129 = _modulo("0129_pagos_informados.py")
    m0130 = _modulo("0130_pagos_informados_confirmar.py")
    cuerpo = m0129.CUERPO_EXPORTAR + m0130.CUERPO_EXPORTAR_ESTADOS + cuerpo_extra
    return _modulo("0069_exportacion_economica.py")._funcion(EXPORTAR, cuerpo)


def upgrade() -> None:
    op.execute(TABLAS_SQL)
    op.execute(FUNCIONES)
    for funcion in INTERNAS + RUTAS:
        op.execute(f"revoke all on function {funcion} from public")
    for funcion in RUTAS:
        op.execute(f"grant execute on function {funcion} to cem_app")
    for funcion, cambios in EN_SU_LUGAR:
        op.execute(_definicion(funcion, cambios, al_reves=False))
    op.execute(f"drop function nucleo.{EXPORTAR}(uuid, uuid)")
    op.execute(_exportar(CUERPO_EXPORTAR_CORREO))
    filas = ", ".join(
        f"('{accion}', '{texto_}', 'Pago por correo', array[{_lista(campos)}])"
        for accion, texto_, campos in PROYECCIONES
    )
    op.execute(f"insert into auditoria.proyeccion (accion, texto, recurso, campos) values {filas}")


def downgrade() -> None:
    op.execute(GUARDA)
    acciones = _lista(accion for accion, _, _ in PROYECCIONES)
    op.execute(f"delete from auditoria.proyeccion where accion in ({acciones})")
    op.execute(f"drop function nucleo.{EXPORTAR}(uuid, uuid)")
    op.execute(_exportar(""))
    for funcion, cambios in reversed(EN_SU_LUGAR):
        op.execute(_definicion(funcion, cambios, al_reves=True))
    for funcion in RUTAS + INTERNAS:
        op.execute(f"drop function {funcion}")
    op.execute(f"drop table {TABLA}")
    op.execute("drop function nucleo.cambio_pago_por_correo()")
