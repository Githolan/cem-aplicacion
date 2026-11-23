"""Ayuda por una persona autorizada (RF53; decisiones del propietario del 25-10-2026, §1.7; H2b,
criterio 5 y tabla `autorizacion_ayuda`; H2, §2.7).

Principio: quien ayuda mira y prepara, y nunca sustituye la voluntad de la persona asistida en un
acto decisorio. Usa su propia cuenta y su propia sesión; la auditoría registra quién actuó, en
representación de quién y con qué autorización.

**Los datos.** Dos tablas, una fila por hecho, sin cambios ni borrado (`negar_cambio_ayuda`), con
RLS forzada por consorcio y sin acceso directo de `cem_app`:

- `nucleo.autorizacion_ayuda`: quién autoriza (`otorgante_id`), a quién (`autorizado_id`, otra
  cuenta), en qué consorcio y con qué alcance (`alcances`, lista cerrada de
  `cem_contracts.ayuda.ALCANCES_OTORGABLES`; `preparar_reclamos` pide `consultar_expensas`).
- `nucleo.autorizacion_ayuda_revocacion`: la revocación, una sola por autorización, de quien la dio.
  Cambiar el alcance es revocar y dar otra: nada se edita en el lugar.

Una autorización **cuenta** mientras no está revocada y la persona asistida sigue siendo propietaria
con el vínculo confirmado y vigente en el consorcio (`propietario_confirmado_hoy`).

**La auditoría** (`auditoria.eventos`): dos columnas nuevas, `en_representacion_de` y
`autorizacion_id`, que llena solo la base. `auditoria.encadenar` mira `app.ayuda_id` (lo fija
`app/db.py` en cada transacción de la persona asistida durante una petición de ayuda): todo evento
de esa transacción tiene que ser de la persona asistida en su consorcio, y queda con
`actor_persona_id` = quien ayuda, `en_representacion_de` = la persona asistida y la autorización.
Quien ayuda nunca queda oculto detrás de la asistida. Las dos columnas van al final del hash de la
cadena; `concat_ws` saltea los nulos, así que el hash de cada evento anterior no cambia (lo
comprueban `copia_continua` y los smoke de staging, que suman las dos columnas).

**La sesión de quien ayuda** (`sesion_de_quien_actua`): sin ayuda en curso, la sesión tiene que ser
de quien actúa, como siempre. Con ayuda, tiene que ser de quien ayuda, con la autorización de la
transacción vigente, de la persona asistida (`actor`), en este consorcio y con el alcance que pide
la función. Solo lo piden con un alcance las funciones de los reclamos que admiten ayuda
(`exigir_reclamo_propio`, presentar y retirar, con `preparar_reclamos`; `exigir_lectura_reclamos`,
con `consultar_expensas` y sin la facultad de atender de la asistida). Toda otra función que exige
la sesión de quien actúa la niega a quien ayuda: ninguna cambia.

**Los actos** (`security definer`, solo `cem_app`): `otorgar_ayuda` (el propietario confirmado, por
la dirección de una cuenta, con un modo registrado), `revocar_ayuda` (solo quien la dio) y las
lecturas `ayudas_otorgadas_lectura`, `ayudas_recibidas_lectura` (las de la persona de la sesión, sin
consorcio), `ayuda_vigente` (la que valida `require_scope`) y `ayuda_terminada` (la que distingue
una revocada, para que la pantalla salga sola de la ayuda). Ninguno corre con una ayuda en curso.

**Exportación** (`0069`): la parte nueva `ayudas`, con los nombres y nunca los identificadores.

Downgrade: se niega (`downgrade_con_datos`) si hay autorizaciones o eventos hechos ayudando.

Datos de prueba: SINTETICOS / NO REAL.

Revisión: 0127
Anterior: 0126
"""

import importlib.util
import re
from pathlib import Path

from alembic import op

revision = "0127"
down_revision = "0126"
branch_labels = None
depends_on = None

ALCANCES = ("consultar_expensas", "preparar_reclamos")
"""`cem_contracts.ayuda.ALCANCES_OTORGABLES`; una prueba compara."""
REQUIERE = (("preparar_reclamos", "consultar_expensas"),)
ACCION_OTORGAR = "consorcio.ayuda.otorgar"
ACCION_REVOCAR = "consorcio.ayuda.revocar"
TABLAS = ("nucleo.autorizacion_ayuda", "nucleo.autorizacion_ayuda_revocacion")
EXPORTAR = "exportar_ayudas"


def _lista(valores) -> str:
    return ", ".join(f"'{v}'" for v in valores)


def _requiere() -> str:
    return "".join(f"\n       and (not '{a}' = any(a) or '{b}' = any(a))" for a, b in REQUIERE)


TABLAS_SQL = f"""
create function nucleo.negar_cambio_ayuda()
  returns trigger
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  begin
    raise exception 'una autorización de ayuda no se cambia ni se borra: se revoca'
      using errcode = 'P0001', hint = 'historia_inmutable';
  end
  $$;
revoke all on function nucleo.negar_cambio_ayuda() from public;

-- La lista cerrada de alcances (§1.7, «puede»), sin repetidos.
create function nucleo.alcances_ayuda_validos(a text[])
  returns boolean
  language sql immutable
  set search_path = pg_catalog, pg_temp
  as $$
    select a is not null and array_ndims(a) = 1 and cardinality(a) >= 1
       and array_position(a, null) is null
       and a <@ array[{_lista(ALCANCES)}]::text[]
       and cardinality(a) = (select count(distinct x) from unnest(a) x){_requiere()}
  $$;
revoke all on function nucleo.alcances_ayuda_validos(text[]) from public;

create table nucleo.autorizacion_ayuda (
  consorcio_id   uuid not null references nucleo.consorcio (id),
  id             uuid primary key default plataforma.uuid_v7(),
  otorgante_id   uuid not null references nucleo.persona (id),
  autorizado_id  uuid not null references nucleo.persona (id),
  alcances       text[] not null check (nucleo.alcances_ayuda_validos(alcances)),
  otorgada_en    timestamptz not null default clock_timestamp(),
  check (otorgante_id <> autorizado_id),
  unique (consorcio_id, id)
);
create index autorizacion_ayuda_otorgante on nucleo.autorizacion_ayuda (consorcio_id, otorgante_id);
create index autorizacion_ayuda_autorizado on nucleo.autorizacion_ayuda (autorizado_id);

create table nucleo.autorizacion_ayuda_revocacion (
  consorcio_id     uuid not null,
  autorizacion_id  uuid primary key,
  revocada_por     uuid not null references nucleo.persona (id),
  revocada_en      timestamptz not null default clock_timestamp(),
  foreign key (consorcio_id, autorizacion_id)
    references nucleo.autorizacion_ayuda (consorcio_id, id)
);

alter table auditoria.eventos
  add column en_representacion_de uuid,
  add column autorizacion_id uuid,
  add constraint eventos_ayuda_completa
    check ((en_representacion_de is null) = (autorizacion_id is null));
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
  for each row execute function nucleo.negar_cambio_ayuda();
create trigger historia_{corto}_truncate before truncate on {tabla}
  for each statement execute function nucleo.negar_cambio_ayuda();
"""


AYUDA_GUC = "nullif(current_setting('app.ayuda_id', true), '')"

FUNCIONES = f"""
create function nucleo.ayuda_revocada(autorizacion uuid)
  returns boolean
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select exists (select 1 from nucleo.autorizacion_ayuda_revocacion r
                    where r.autorizacion_id = autorizacion)
  $$;

-- Propietaria con el vínculo confirmado y vigente hoy (un confirmado nunca es importado
-- pendiente: esa regla de `0024` pide `confirmado_en` nulo).
create function nucleo.propietario_confirmado_hoy(c uuid, quien uuid)
  returns boolean
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select exists (
      select 1 from nucleo.vinculo v
       where v.consorcio_id = c and v.persona_id = quien and v.tipo = 'propietario'
         and v.confirmado_en is not null
         and daterange(v.vigente_desde, v.vigente_hasta) @> nucleo.hoy_del_consorcio(c))
  $$;

-- La autorización de la ayuda en curso (`app.ayuda_id`), o ninguna fila. Una que no existe es un
-- defecto: falla. La usa `auditoria.encadenar`, que corre con el rol de quien inserta.
create function nucleo.ayuda_de_la_transaccion()
  returns table (id uuid, consorcio_id uuid, otorgante_id uuid, autorizado_id uuid)
  language plpgsql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    t text := {AYUDA_GUC};
  begin
    if t is null then
      return;
    end if;
    return query
      select a.id, a.consorcio_id, a.otorgante_id, a.autorizado_id
        from nucleo.autorizacion_ayuda a where a.id = t::uuid;
    if not found then
      raise exception 'la ayuda de la transacción no existe' using errcode = '42501';
    end if;
  end
  $$;

-- La sesión de la petición sirve para que `actor` haga esto. Sin ayuda en curso: la sesión es de
-- `actor`. Con ayuda: es de quien ayuda, la autorización es de `actor` en `c`, cuenta hoy y tiene
-- `alcance` (nulo: la función no admite ayuda). Traba la autorización contra una revocación.
create function nucleo.sesion_de_quien_actua(c uuid, actor uuid, sesion uuid, alcance text)
  returns boolean
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    t text := {AYUDA_GUC};
    a nucleo.autorizacion_ayuda;
  begin
    if t is null then
      return exists (select 1 from nucleo.sesion x
                      where x.id = sesion and x.persona_id = actor and x.revocada_en is null
                        and x.vence_en > now());
    end if;
    if alcance is null then
      return false;
    end if;
    select * into a from nucleo.autorizacion_ayuda y
     where y.id = t::uuid and y.consorcio_id = c
       for share;
    if not found or a.otorgante_id is distinct from actor or not alcance = any(a.alcances)
       or nucleo.ayuda_revocada(a.id) or not nucleo.propietario_confirmado_hoy(c, actor) then
      return false;
    end if;
    return exists (select 1 from nucleo.sesion x
                    where x.id = sesion and x.persona_id = a.autorizado_id
                      and x.revocada_en is null and x.vence_en > now());
  end
  $$;

-- La autorización del encabezado para `require_scope`: de la persona de la sesión, en este
-- consorcio, vigente. Sin filas si no (la ruta responde 404).
create function nucleo.ayuda_vigente(autorizacion uuid, sesion uuid)
  returns table (otorgante_id uuid, alcances text[])
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    yo uuid := plataforma.persona_actual();
  begin
    if c is null or yo is null or {AYUDA_GUC} is not null then
      raise exception 'sin ámbito para validar la ayuda' using errcode = '42501';
    end if;
    if not nucleo.sesion_de_quien_actua(c, yo, sesion, null) then
      return;
    end if;
    return query
      select a.otorgante_id, a.alcances from nucleo.autorizacion_ayuda a
        join nucleo.persona o on o.id = a.otorgante_id and o.eliminado_en is null
       where a.id = autorizacion and a.consorcio_id = c and a.autorizado_id = yo
         and not nucleo.ayuda_revocada(a.id)
         and nucleo.propietario_confirmado_hoy(c, a.otorgante_id);
  end
  $$;

-- Las autorizaciones que la persona de la sesión puede usar para ayudar, en cualquier consorcio.
create function nucleo.ayudas_recibidas_lectura(sesion uuid)
  returns table (id uuid, consorcio_id uuid, consorcio_nombre text, otorgante_id uuid,
                 otorgante_nombre text, alcances text[], otorgada_en timestamptz)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    yo uuid := plataforma.persona_actual();
  begin
    if yo is null or {AYUDA_GUC} is not null then
      raise exception 'sin persona en el ámbito' using errcode = '42501';
    end if;
    if not exists (select 1 from nucleo.sesion x
                    where x.id = sesion and x.persona_id = yo and x.revocada_en is null
                      and x.vence_en > now()) then
      raise exception 'la sesión no es válida' using errcode = 'P0001', hint = 'sesion_invalida';
    end if;
    return query
      select a.id, a.consorcio_id, co.nombre, a.otorgante_id, o.nombre, a.alcances, a.otorgada_en
        from nucleo.autorizacion_ayuda a
        join nucleo.consorcio co on co.id = a.consorcio_id and co.eliminado_en is null
        join nucleo.persona o on o.id = a.otorgante_id and o.eliminado_en is null
       where a.autorizado_id = yo and not nucleo.ayuda_revocada(a.id)
         and nucleo.propietario_confirmado_hoy(a.consorcio_id, a.otorgante_id)
       order by co.nombre, o.nombre, a.otorgada_en;
  end
  $$;

-- La autorización del encabezado ya no vale porque la persona asistida la revocó o dejó de ser
-- propietaria confirmada: true solo para quien la recibió, con su sesión. A cualquier otra persona
-- no le dice nada (false, como una que no existe). La API responde `ayuda_revocada` y la pantalla
-- sale sola de la ayuda (RF53).
create function nucleo.ayuda_terminada(autorizacion uuid, sesion uuid)
  returns boolean
  language plpgsql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    yo uuid := plataforma.persona_actual();
  begin
    if yo is null or {AYUDA_GUC} is not null then
      raise exception 'sin persona en el ámbito' using errcode = '42501';
    end if;
    return exists (
      select 1 from nucleo.autorizacion_ayuda a
        join nucleo.sesion x on x.id = sesion and x.persona_id = yo and x.revocada_en is null
                            and x.vence_en > now()
       where a.id = autorizacion and a.autorizado_id = yo
         and (nucleo.ayuda_revocada(a.id)
              or not nucleo.propietario_confirmado_hoy(a.consorcio_id, a.otorgante_id)));
  end
  $$;

-- Quien autoriza, revoca o lee las suyas: la persona de la sesión, con su propia sesión, por la
-- API y sin ninguna ayuda en curso (quien ayuda nunca da ni quita permisos). Con `con_modo`, un
-- modo registrado. Devuelve el día del consorcio.
create function nucleo.exigir_sesion_ayuda(c uuid, actor uuid, sesion uuid, origen_pedido text,
                                           con_modo boolean)
  returns date
  language plpgsql volatile
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    hoy date;
  begin
    if c is null or {AYUDA_GUC} is not null then
      raise exception 'sin ámbito para la ayuda' using errcode = '42501';
    end if;
    if origen_pedido is distinct from 'api' then
      raise exception 'origen inválido' using errcode = 'P0001', hint = 'origen_invalido';
    end if;
    if actor is null or actor is distinct from plataforma.persona_actual() then
      raise exception 'la persona no es la de la petición' using errcode = '42501';
    end if;
    hoy := nucleo.hoy_del_consorcio(c);
    if con_modo then
      perform pg_advisory_xact_lock_shared(hashtextextended('modo:' || c::text, 0));
      if not exists (
           select 1 from nucleo.modo_operacion m
            where m.consorcio_id = c and m.vigente_desde <= hoy
              and (m.vigente_hasta is null or m.vigente_hasta > hoy)) then
        raise exception 'sin modo registrado' using errcode = 'P0001', hint = 'modo_no_admitido';
      end if;
    end if;
    if not nucleo.sesion_de_quien_actua(c, actor, sesion, null) then
      raise exception 'la sesión no es válida' using errcode = 'P0001', hint = 'sesion_invalida';
    end if;
    return hoy;
  end
  $$;

create function nucleo.auditar_ayuda(c uuid, actor uuid, origen_pedido text, accion text,
                                     autorizacion uuid, despues jsonb)
  returns void
  language sql volatile
  set search_path = pg_catalog, pg_temp
  as $$
    insert into auditoria.eventos
      (consorcio_id, actor_persona_id, origen, accion, entidad, entidad_id, despues,
       hash_previo, hash)
    values (c, actor, origen_pedido, accion, 'autorizacion_ayuda', autorizacion, despues, '', '')
  $$;

-- Autorizar: el propietario confirmado, a la cuenta de esa dirección, con ese alcance. Devuelve a
-- quién avisar; la ruta publica el correo en la misma transacción.
create function nucleo.otorgar_ayuda(correo_autorizado text, alcances_pedidos text[],
                                     actor uuid, origen_pedido text, sesion uuid)
  returns table (autorizacion_id uuid, correo text, otorgante_nombre text,
                 consorcio_nombre text)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    autorizado uuid;
    i uuid;
  begin
    perform nucleo.exigir_sesion_ayuda(c, actor, sesion, origen_pedido, true);
    perform nucleo.exigir_propietario_confirmado(c, actor);
    if not nucleo.alcances_ayuda_validos(alcances_pedidos) then
      raise exception 'alcances inválidos' using errcode = 'P0001', hint = 'alcances_invalidos';
    end if;
    select p.id into autorizado from nucleo.persona p
     where p.correo = lower(btrim(correo_autorizado)) and p.eliminado_en is null;
    if autorizado is null then
      raise exception 'esa dirección no tiene cuenta' using errcode = 'P0001',
        hint = 'sin_cuenta';
    end if;
    if autorizado = actor then
      raise exception 'no se autoriza a sí misma' using errcode = 'P0001', hint = 'a_si_misma';
    end if;
    -- Dos autorizaciones a la vez a la misma persona: gana una.
    perform pg_advisory_xact_lock(hashtextextended(
      'ayuda:' || c::text || ':' || actor::text || ':' || autorizado::text, 0));
    if exists (select 1 from nucleo.autorizacion_ayuda a
                where a.consorcio_id = c and a.otorgante_id = actor
                  and a.autorizado_id = autorizado and not nucleo.ayuda_revocada(a.id)) then
      raise exception 'ya tiene una vigente' using errcode = 'P0001', hint = 'ya_vigente';
    end if;
    insert into nucleo.autorizacion_ayuda (consorcio_id, otorgante_id, autorizado_id, alcances)
    values (c, actor, autorizado, alcances_pedidos)
    returning id into i;
    perform nucleo.auditar_ayuda(c, actor, origen_pedido, '{ACCION_OTORGAR}', i,
      jsonb_build_object('autorizado_id', autorizado, 'alcances', to_jsonb(alcances_pedidos)));
    return query
      select i, p.correo, o.nombre, co.nombre
        from nucleo.persona p, nucleo.persona o, nucleo.consorcio co
       where p.id = autorizado and o.id = actor and co.id = c;
  end
  $$;

-- Revocar: solo quien la dio. La autorización queda; se agrega su revocación.
create function nucleo.revocar_ayuda(autorizacion uuid, actor uuid, origen_pedido text,
                                     sesion uuid)
  returns void
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    a nucleo.autorizacion_ayuda;
  begin
    perform nucleo.exigir_sesion_ayuda(c, actor, sesion, origen_pedido, false);
    select * into a from nucleo.autorizacion_ayuda y
     where y.consorcio_id = c and y.id = autorizacion
       for update;
    if not found or a.otorgante_id <> actor then
      raise exception 'la autorización no existe o no es de esta persona' using errcode = 'P0001',
        hint = 'no_encontrado';
    end if;
    if nucleo.ayuda_revocada(a.id) then
      raise exception 'ya estaba revocada' using errcode = 'P0001', hint = 'ya_revocada';
    end if;
    insert into nucleo.autorizacion_ayuda_revocacion (consorcio_id, autorizacion_id, revocada_por)
    values (c, a.id, actor);
    perform nucleo.auditar_ayuda(c, actor, origen_pedido, '{ACCION_REVOCAR}', a.id,
      jsonb_build_object('autorizado_id', a.autorizado_id));
  end
  $$;

-- Las que dio la persona en el consorcio, vigentes y revocadas, la más nueva primero.
create function nucleo.ayudas_otorgadas_lectura(actor uuid, origen_pedido text, sesion uuid)
  returns table (id uuid, autorizado_nombre text, alcances text[], otorgada_en timestamptz,
                 revocada_en timestamptz)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
  begin
    perform nucleo.exigir_sesion_ayuda(c, actor, sesion, origen_pedido, false);
    return query
      select a.id, p.nombre, a.alcances, a.otorgada_en, r.revocada_en
        from nucleo.autorizacion_ayuda a
        join nucleo.persona p on p.id = a.autorizado_id
        left join nucleo.autorizacion_ayuda_revocacion r on r.autorizacion_id = a.id
       where a.consorcio_id = c and a.otorgante_id = actor
       order by a.otorgada_en desc, a.id desc;
  end
  $$;
"""

RUTAS = (
    "nucleo.ayuda_vigente(uuid, uuid)",
    "nucleo.ayudas_recibidas_lectura(uuid)",
    "nucleo.ayuda_terminada(uuid, uuid)",
    "nucleo.otorgar_ayuda(text, text[], uuid, text, uuid)",
    "nucleo.revocar_ayuda(uuid, uuid, text, uuid)",
    "nucleo.ayudas_otorgadas_lectura(uuid, text, uuid)",
    "nucleo.ayuda_de_la_transaccion()",
)
"""`ayuda_de_la_transaccion` es interna, pero la llama `auditoria.encadenar` con el rol que
inserta, que puede ser `cem_app`: por eso tiene `execute`."""
INTERNAS = (
    "nucleo.ayuda_revocada(uuid)",
    "nucleo.propietario_confirmado_hoy(uuid, uuid)",
    "nucleo.sesion_de_quien_actua(uuid, uuid, uuid, text)",
    "nucleo.exigir_sesion_ayuda(uuid, uuid, uuid, text, boolean)",
    "nucleo.auditar_ayuda(uuid, uuid, text, text, uuid, jsonb)",
)


# --- La cadena de la auditoría ----------------------------------------------------------------


def _modulo(nombre: str):
    ruta = Path(__file__).with_name(nombre)
    spec = importlib.util.spec_from_file_location(ruta.stem, ruta)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def _definicion(sql: str, cabecera: str) -> str:
    """La definición de una función tal como la dejó su migración (ya aplicada, no cambia), para
    volver a ella al bajar."""
    inicio = sql.index(cabecera)
    fin = sql.index("\n  $$;", inicio) + len("\n  $$;")
    return sql[inicio:fin].replace("create function", "create or replace function", 1) + "\n"


ENCADENAR_0001 = _definicion(
    _modulo("0001_esquemas_nucleo_rls.py").UPGRADE, "create function auditoria.encadenar()"
)

ENCADENAR = """
create or replace function auditoria.encadenar() returns trigger
  language plpgsql
  as $$
  declare
    a_id uuid;
    a_consorcio uuid;
    a_otorgante uuid;
    a_autorizado uuid;
  begin
    perform pg_advisory_xact_lock(hashtextextended('auditoria:' || new.consorcio_id::text, 0));
    -- RF53: las columnas de la ayuda las llena solo la base.
    if new.en_representacion_de is not null or new.autorizacion_id is not null then
      raise exception 'la ayuda de un evento la registra la base' using errcode = '42501';
    end if;
    select x.id, x.consorcio_id, x.otorgante_id, x.autorizado_id
      into a_id, a_consorcio, a_otorgante, a_autorizado
      from nucleo.ayuda_de_la_transaccion() x;
    if a_id is not null then
      if a_consorcio is distinct from new.consorcio_id
         or new.actor_persona_id is distinct from a_otorgante then
        raise exception 'durante la ayuda solo hay eventos de la persona asistida en su consorcio'
          using errcode = '42501';
      end if;
      new.actor_persona_id := a_autorizado;
      new.en_representacion_de := a_otorgante;
      new.autorizacion_id := a_id;
    end if;
    select e.hash into new.hash_previo
      from auditoria.eventos e
     where e.consorcio_id = new.consorcio_id
     order by e.secuencia desc
     limit 1;
    new.hash_previo := coalesce(new.hash_previo, '');
    -- Las dos de la ayuda al final: `concat_ws` saltea los nulos y el hash de los demás no cambia.
    new.hash := encode(sha256(convert_to(concat_ws('|',
      new.hash_previo, new.consorcio_id, new.id, new.secuencia,
      to_char(new.creado_en at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US'),
      new.actor_persona_id, new.origen, new.accion, new.entidad, new.entidad_id,
      new.antes, new.despues, new.motivo, new.ip,
      new.en_representacion_de, new.autorizacion_id), 'UTF8')), 'hex');
    return new;
  end
  $$;
"""


# --- Los reclamos que admiten ayuda (`0123`) --------------------------------------------------

_SQL_0123 = _modulo("0123_reclamos_liquidacion.py").FUNCIONES
RECLAMOS_0123 = "".join(
    _definicion(_SQL_0123, f"create function nucleo.{nombre}(")
    for nombre in (
        "exigir_sesion_reclamos",
        "exigir_reclamo_propio",
        "exigir_lectura_reclamos",
        "retirar_reclamo_liquidacion",
    )
)
_RETIRAR_SESION = "    perform nucleo.exigir_sesion_reclamos(c, actor, sesion, origen_pedido);\n"


def _retirar_con_ayuda() -> str:
    """`retirar_reclamo_liquidacion` de `0123`: retirar lo preparado admite `preparar_reclamos`."""
    original = _definicion(_SQL_0123, "create function nucleo.retirar_reclamo_liquidacion(")
    if original.count(_RETIRAR_SESION) != 1:
        raise RuntimeError("0123 cambió: retirar no exige la sesión una sola vez")
    return original.replace(
        _RETIRAR_SESION,
        "    perform nucleo.exigir_sesion_reclamos_con_ayuda(c, actor, sesion, origen_pedido,\n"
        "                                                     'preparar_reclamos');\n",
    )


_SESION_0123 = """    if not exists (select 1 from nucleo.sesion x
                    where x.id = sesion and x.persona_id = actor and x.revocada_en is null
                      and x.vence_en > now()) then
      raise exception 'la sesión no es válida' using errcode = 'P0001', hint = 'sesion_invalida';
    end if;"""
_SESION_CON_AYUDA = f"""    if not nucleo.sesion_de_quien_actua(c, actor, sesion, alcance) then
      if {AYUDA_GUC} is not null then
        raise exception 'la ayuda no alcanza a esto' using errcode = '42501';
      end if;
      raise exception 'la sesión no es válida' using errcode = 'P0001', hint = 'sesion_invalida';
    end if;"""


def _con_ayuda() -> str:
    """`exigir_sesion_reclamos` de `0123` con el alcance que admite la función que llama."""
    original = _definicion(_SQL_0123, "create function nucleo.exigir_sesion_reclamos(")
    if original.count(_SESION_0123) != 1:
        raise RuntimeError("0123 cambió: la comprobación de la sesión no está una sola vez")
    cabecera = re.compile(r"create or replace function nucleo\.exigir_sesion_reclamos\([^)]*\)")
    nueva = cabecera.sub(
        "create function nucleo.exigir_sesion_reclamos_con_ayuda(c uuid, actor uuid,"
        " sesion uuid, origen_pedido text, alcance text)",
        original,
        count=1,
    )
    return nueva.replace(_SESION_0123, _SESION_CON_AYUDA)


RECLAMOS = f"""
{_con_ayuda()}
revoke all on function nucleo.exigir_sesion_reclamos_con_ayuda(uuid, uuid, uuid, text, text)
  from public;

-- Sin alcance: ninguna función de `0123` que no lo pida lo admite.
create or replace function nucleo.exigir_sesion_reclamos(c uuid, actor uuid, sesion uuid,
                                                         origen_pedido text)
  returns date
  language plpgsql volatile
  set search_path = pg_catalog, pg_temp
  as $$
  begin
    return nucleo.exigir_sesion_reclamos_con_ayuda(c, actor, sesion, origen_pedido, null);
  end
  $$;

-- Presentar y retirar: lo de `0123`, y quien ayuda con `preparar_reclamos`.
create or replace function nucleo.exigir_reclamo_propio(c uuid, actor uuid, sesion uuid,
                                                        origen_pedido text, unidad uuid)
  returns void
  language plpgsql volatile
  set search_path = pg_catalog, pg_temp
  as $$
  begin
    perform nucleo.exigir_sesion_reclamos_con_ayuda(c, actor, sesion, origen_pedido,
                                                     'preparar_reclamos');
    if unidad is null or not unidad = any(nucleo.exigir_propietario_confirmado(c, actor)) then
      raise exception 'sin un vínculo de propietario confirmado sobre esa unidad'
        using errcode = '42501';
    end if;
  end
  $$;

-- Leer: lo de `0123`, y quien ayuda con `consultar_expensas`, solo como propietaria: la facultad de
-- atender de la persona asistida no se presta.
create or replace function nucleo.exigir_lectura_reclamos(c uuid, actor uuid, sesion uuid,
                                                          origen_pedido text)
  returns boolean
  language plpgsql volatile
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    hoy date;
  begin
    hoy := nucleo.exigir_sesion_reclamos_con_ayuda(c, actor, sesion, origen_pedido,
                                                    'consultar_expensas');
    if {AYUDA_GUC} is null
       and nucleo.tiene_facultad_vigente(c, actor, 'reclamos.atender', hoy) then
      return true;
    end if;
    perform nucleo.exigir_propietario_confirmado(c, actor);
    return false;
  end
  $$;

-- Retirar: lo de `0123`, y quien ayuda con `preparar_reclamos` (deshace lo que preparó).
{_retirar_con_ayuda()}
"""


# --- Exportación ----------------------------------------------------------------------------


def _nombre(columna: str) -> str:
    return f"nucleo.nombre_para_exportar(c, {columna}, hoy)"


_AUTORIZADO = (
    "(select pe.nombre from nucleo.persona pe where pe.id = a.autorizado_id"
    " and pe.eliminado_en is null)"
)
"""Quien ayuda puede no tener vínculo ni mandato en el edificio, y `nombre_para_exportar` solo
nombra a quien lo tiene: su relación con el edificio es la autorización misma, así que se nombra
igual (salvo una cuenta suprimida, RF37). Quien ayuda nunca queda oculto (§1.7)."""

CUERPO_EXPORTAR = f"""
    hoy := nucleo.hoy_del_consorcio(c);
    return query
      select 'autorizacion_ayuda'::text,
             jsonb_build_object('id', a.id, 'otorgante_nombre', {_nombre("a.otorgante_id")},
                                'autorizado_nombre', {_AUTORIZADO},
                                'alcances', to_jsonb(a.alcances), 'otorgada_en', a.otorgada_en)
        from nucleo.autorizacion_ayuda a
       where a.consorcio_id = c
       order by a.otorgada_en, a.id;
    return query
      select 'autorizacion_ayuda_revocacion'::text,
             jsonb_build_object('autorizacion_id', r.autorizacion_id,
                                'revocada_por_nombre', {_nombre("r.revocada_por")},
                                'revocada_en', r.revocada_en)
        from nucleo.autorizacion_ayuda_revocacion r
       where r.consorcio_id = c
       order by r.revocada_en, r.autorizacion_id;
"""

GUARDA = """
do $guarda$
begin
  if exists (select 1 from nucleo.autorizacion_ayuda)
     or exists (select 1 from auditoria.eventos where autorizacion_id is not null) then
    raise exception '0127 no baja: hay autorizaciones de ayuda o eventos hechos ayudando'
      using errcode = 'P0001', hint = 'downgrade_con_datos';
  end if;
end
$guarda$;
"""


def upgrade() -> None:
    op.execute(TABLAS_SQL)
    for tabla in TABLAS:
        op.execute(_protecciones(tabla))
    op.execute(FUNCIONES)
    for funcion in INTERNAS + RUTAS:
        op.execute(f"revoke all on function {funcion} from public")
    for funcion in RUTAS:
        op.execute(f"grant execute on function {funcion} to cem_app")
    op.execute(ENCADENAR)
    op.execute(RECLAMOS)
    op.execute(_modulo("0069_exportacion_economica.py")._funcion(EXPORTAR, CUERPO_EXPORTAR))
    op.execute(
        "insert into auditoria.proyeccion (accion, texto, recurso, campos) values"
        f" ('{ACCION_OTORGAR}', 'Autorizó a una persona a ayudar', 'Autorización de ayuda',"
        " array['alcances']),"
        f" ('{ACCION_REVOCAR}', 'Revocó una autorización de ayuda', 'Autorización de ayuda',"
        " array[]::text[])"
    )


def downgrade() -> None:
    op.execute(GUARDA)
    op.execute(
        f"delete from auditoria.proyeccion where accion in ('{ACCION_OTORGAR}', '{ACCION_REVOCAR}')"
    )
    op.execute(f"drop function nucleo.{EXPORTAR}(uuid, uuid)")
    op.execute(RECLAMOS_0123)
    op.execute(
        "drop function nucleo.exigir_sesion_reclamos_con_ayuda(uuid, uuid, uuid, text, text)"
    )
    op.execute(ENCADENAR_0001)
    for funcion in reversed(INTERNAS + RUTAS):
        op.execute(f"drop function {funcion}")
    op.execute(
        "alter table auditoria.eventos drop constraint eventos_ayuda_completa,"
        " drop column autorizacion_id, drop column en_representacion_de"
    )
    for tabla in reversed(TABLAS):
        op.execute(f"drop table {tabla}")
    op.execute("drop function nucleo.alcances_ayuda_validos(text[])")
    op.execute("drop function nucleo.negar_cambio_ayuda()")
