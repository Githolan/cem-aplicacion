"""Baja de mandatos con respaldo, `error_carga` y administrador único (H2b, corte 2b-3).

Diseño: `doc/diseno/H2b_3_Tabla_de_verdad.md` del repositorio CEM, §5.1, §5.2 y §5.3 (forma SQL
probada), decisiones C2, C4, C6, A2, A4, A9, A10, A11, A12 y A14.

- **Paquetes (C2, A2):** `mandatos.registrar` y `decisiones.registrar` para `miembro_consejo` y
  `administrador`; `fiscalizacion.ver_panel` (solo lectura) para `administrador`. Valen para los
  mandatos nuevos: `alta_mandato` copia el paquete al momento del alta.
- **Baja con respaldo (C6):** `causa_baja` tipificada y exactamente un respaldo: `remocion` solo
  con una decisión de asamblea del mismo consorcio; las demás causas solo con
  `instrumento_referencia`. La descripción sigue siendo `motivo_fin`, obligatoria.
- **`error_carga` (A9, A10):** no toca ninguna fecha; el mandato no vale para ninguna fecha y no
  participa de las exclusiones. Es irreversible: un mandato con causa no admite otra baja.
- **Identidad (A11, A12):** `administrador_externo` exige un CUIT normalizado y válido;
  `miembro_consejo` y `administrador` exigen cuenta (`persona_id`).
- **Superposición:** un miembro del Consejo no tiene dos mandatos de consejo superpuestos en el
  consorcio, y hay **a lo sumo un** mandato de administración (`administrador` y
  `administrador_externo` juntos) por consorcio y fecha (A14). Esta última contiene a la
  exclusión de AD por persona y a la de AE por CUIT.
- **A4:** con origen `api`, nadie se da un mandato ni se da de baja a sí mismo.
- **Validez material en un solo lugar:** `nucleo.mandato_vigente(mandato, fecha)`. Ninguna
  consulta que conceda puede decidir por fechas solas.
- **C4:** `nucleo.consorcios_con_mandato_vigente()` devuelve solo los ids de los consorcios donde
  la persona actual tiene un mandato vigente. **No** se agrega una política RLS `propios` a
  `nucleo.mandato`: se combinaría con OR con `por_consorcio` y mostraría, dentro del ámbito de un
  consorcio, los mandatos propios de otro.

El downgrade se niega si existe **cualquier** mandato (`downgrade_con_datos`): perdería causas y
respaldos, y dejaría facultades otorgadas fuera del paquete de `0011`.

Revisión: 0012
Anterior: 0011
"""

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

PAQUETES_NUEVOS = [
    ("miembro_consejo", "mandatos.registrar"),
    ("miembro_consejo", "decisiones.registrar"),
    ("administrador", "mandatos.registrar"),
    ("administrador", "decisiones.registrar"),
    ("administrador", "fiscalizacion.ver_panel"),  # A2, solo lectura
]

FIRMA_ALTA = (
    "nucleo.alta_mandato(nucleo.tipo_mandato, uuid, text, text, text, uuid, date, date,"
    " text[], uuid, text)"
)
FIRMA_BAJA_0011 = "nucleo.baja_mandato(uuid, text, uuid, text)"
FIRMA_BAJA = "nucleo.baja_mandato(uuid, nucleo.causa_baja, uuid, text, text, uuid, text)"
FIRMA_CUIT = "nucleo.cuit_valido(text)"
FIRMA_VIGENTE = "nucleo.mandato_vigente(uuid, date)"
FIRMA_CONSORCIOS = "nucleo.consorcios_con_mandato_vigente()"

# Las de 0011, tal cual, para el downgrade.
ALTA_0011 = r"""
create or replace function nucleo.alta_mandato(
  tipo_nuevo nucleo.tipo_mandato, persona uuid, nombre text, cuit text, matricula_nueva text,
  decision uuid, desde date, hasta date, facultades text[], actor uuid, origen_pedido text)
  returns uuid
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    paquete text[];
    elegidas text[];
    fuera text[];
    nuevo uuid;
  begin
    if c is null then
      raise exception 'sin consorcio en el ámbito' using errcode = '42501';
    end if;
    -- Criterio 9: sin decisión de respaldo de este consorcio no hay mandato.
    if decision is null or not exists (
         select 1 from nucleo.decision_formal d where d.consorcio_id = c and d.id = decision) then
      raise exception 'el mandato necesita una decisión de respaldo de este consorcio'
        using errcode = 'P0001', hint = 'decision_invalida';
    end if;
    if persona is not null and not exists (select 1 from nucleo.persona p where p.id = persona) then
      raise exception 'la persona no existe' using errcode = 'P0001', hint = 'persona_invalida';
    end if;

    select array_agg(p.facultad order by p.facultad) into paquete
      from nucleo.paquete_facultad p where p.tipo = tipo_nuevo;
    elegidas := coalesce(facultades, paquete);
    if cardinality(elegidas) = 0 then
      raise exception 'el mandato necesita al menos una facultad'
        using errcode = 'P0001', hint = 'sin_facultades';
    end if;
    select array_agg(f order by f) into fuera
      from unnest(elegidas) f where not f = any (paquete);
    if fuera is not null then
      raise exception 'facultades fuera del paquete de %: %',
        tipo_nuevo, array_to_string(fuera, ', ')
        using errcode = 'P0001', hint = 'facultad_fuera_de_paquete';
    end if;

    begin
      insert into nucleo.mandato
        (consorcio_id, tipo, persona_id, mandatario_nombre, mandatario_cuit, matricula,
         decision_id, vigente_desde, vigente_hasta, registrado_por, origen)
      values (c, tipo_nuevo, persona, btrim(nombre), cuit, btrim(matricula_nueva), decision,
              desde, hasta, actor, origen_pedido)
      returning id into nuevo;
    exception when exclusion_violation then
      raise exception 'ya hay un mandato de % para esa persona en esas fechas', tipo_nuevo
        using errcode = 'P0001', hint = 'mandato_superpuesto';
    end;

    insert into nucleo.facultad_otorgada (consorcio_id, mandato_id, facultad, decision_id)
    select c, nuevo, f, decision from (select distinct unnest(elegidas) f) x;

    insert into auditoria.eventos
      (consorcio_id, actor_persona_id, origen, accion, entidad, entidad_id, despues,
       hash_previo, hash)
    values (c, actor, origen_pedido, 'consorcio.mandato.alta', 'mandato', nuevo,
            jsonb_strip_nulls(jsonb_build_object(
              'tipo', tipo_nuevo, 'persona_id', persona, 'mandatario_nombre', btrim(nombre),
              'decision_id', decision, 'desde', desde, 'hasta', hasta,
              'facultades', (select to_jsonb(array_agg(distinct f order by f))
                               from unnest(elegidas) f))),
            '', '');
    return nuevo;
  end
  $$;
"""

BAJA_0011 = r"""
create function nucleo.baja_mandato(mandato uuid, motivo text, actor uuid, origen_pedido text)
  returns bigint
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    m nucleo.mandato;
    hoy date;
    revocadas bigint := 0;
  begin
    if c is null then
      raise exception 'sin consorcio en el ámbito' using errcode = '42501';
    end if;
    select * into m from nucleo.mandato x where x.consorcio_id = c and x.id = mandato for update;
    if m.id is null then
      raise exception 'el mandato no existe en este consorcio'
        using errcode = 'P0001', hint = 'mandato_invalido';
    end if;
    hoy := nucleo.hoy_del_consorcio(c);
    if m.finalizado_en is not null or (m.vigente_hasta is not null and m.vigente_hasta <= hoy) then
      raise exception 'el mandato ya terminó' using errcode = 'P0001', hint = 'mandato_terminado';
    end if;

    update nucleo.mandato
       set vigente_hasta = greatest(m.vigente_desde, hoy), finalizado_por = actor,
           finalizado_en = now(), motivo_fin = btrim(motivo)
     where id = m.id;
    if m.persona_id is not null then
      revocadas := nucleo.revocar_sesiones_de(m.persona_id);
    end if;

    insert into auditoria.eventos
      (consorcio_id, actor_persona_id, origen, accion, entidad, entidad_id, antes, despues,
       hash_previo, hash)
    values (c, actor, origen_pedido, 'consorcio.mandato.baja', 'mandato', m.id,
            jsonb_build_object('vigente_hasta', m.vigente_hasta),
            jsonb_build_object('vigente_hasta', greatest(m.vigente_desde, hoy),
                               'motivo', btrim(motivo), 'sesiones_revocadas', revocadas),
            '', '');
    return revocadas;
  end
  $$;
"""

UPGRADE = r"""
create type nucleo.causa_baja as enum
  ('remocion', 'renuncia', 'fallecimiento_incapacidad', 'error_carga', 'otro');

-- A11: CUIT normalizado (11 dígitos, sin guiones ni espacios) con dígito verificador por
-- módulo 11 (pesos 5,4,3,2,7,6,5,4,3,2; resultado 11 -> 0; 10 -> inválido, con -1 y no NULL,
-- porque un CHECK con NULL pasa). Nunca devuelve NULL con entrada no nula.
create function nucleo.cuit_valido(c text) returns boolean
  language sql immutable strict parallel safe
  set search_path = pg_catalog, pg_temp
  as $$
    select c ~ '^[0-9]{11}$'
       and (with p as (
              select sum(substr(c, i::int, 1)::int * w) s
                from unnest(array[5,4,3,2,7,6,5,4,3,2]) with ordinality as t(w, i)),
            d as (select (11 - (s % 11)) r from p)
            select case when r = 11 then 0 when r = 10 then -1 else r end
              from d) = substr(c, 11, 1)::int
  $$;

alter table nucleo.mandato
  add column causa_baja             nucleo.causa_baja,
  add column baja_decision_id       uuid,
  add column instrumento_referencia text
      constraint mandato_instrumento_referencia_largo
      check (length(btrim(instrumento_referencia)) between 1 and 300),
  -- C6: la decisión es del mismo consorcio.
  add constraint mandato_baja_decision_mismo_consorcio
      foreign key (consorcio_id, baja_decision_id)
      references nucleo.decision_formal (consorcio_id, id),
  -- terminado <=> con causa
  add constraint mandato_terminado_con_causa
      check ((finalizado_en is null) = (causa_baja is null)),
  -- con causa, exactamente un respaldo (nunca vacío, nunca los dos)
  add constraint mandato_baja_un_respaldo
      check (causa_baja is null
             or ((baja_decision_id is null) <> (instrumento_referencia is null))),
  -- remocion -> solo decisión
  add constraint mandato_remocion_con_decision
      check (causa_baja is distinct from 'remocion'
             or (baja_decision_id is not null and instrumento_referencia is null)),
  -- renuncia, fallecimiento_incapacidad, error_carga, otro -> solo instrumento
  add constraint mandato_otras_causas_con_instrumento
      check (causa_baja is null or causa_baja = 'remocion'
             or (instrumento_referencia is not null and baja_decision_id is null)),
  -- A12: MC y AD exigen cuenta (su exclusión por persona_id nunca compara nulos).
  add constraint mandato_con_cuenta
      check (tipo = 'administrador_externo' or persona_id is not null),
  -- A11: la identidad de un administrador_externo es su CUIT, con o sin cuenta.
  add constraint mandato_externo_con_cuit
      check (tipo <> 'administrador_externo' or mandatario_cuit is not null),
  add constraint mandato_cuit_valido
      check (mandatario_cuit is null or nucleo.cuit_valido(mandatario_cuit)),
  -- Reemplaza el EXCLUDE de 0011 (nombre leído del catálogo de los dos entornos).
  drop constraint mandato_consorcio_id_tipo_persona_id_daterange_excl,
  -- Un miembro del Consejo no tiene dos mandatos de consejo superpuestos en el consorcio.
  add constraint mandato_consejo_sin_superposicion
      exclude using gist (consorcio_id with =, persona_id with =,
                          daterange(vigente_desde, vigente_hasta) with &&)
      where (tipo = 'miembro_consejo' and causa_baja is distinct from 'error_carga'),
  -- A14: a lo sumo UN mandato de administración (AD y AE juntos) por consorcio y fecha.
  add constraint mandato_administracion_unica
      exclude using gist (consorcio_id with =,
                          daterange(vigente_desde, vigente_hasta) with &&)
      where (tipo in ('administrador', 'administrador_externo')
             and causa_baja is distinct from 'error_carga');

-- Validez material, definida una sola vez (A9, A10). La usan el Contexto, require_scope, la
-- función de C4 y toda consulta que conceda. Prohibido autorizar por fechas solas.
create function nucleo.mandato_vigente(mandato uuid, fecha date) returns boolean
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select exists (select 1 from nucleo.mandato m
                    where m.id = mandato
                      and m.causa_baja is distinct from 'error_carga'
                      and daterange(m.vigente_desde, m.vigente_hasta) @> fecha)
  $$;

-- C4: en qué consorcios tiene la persona actual un mandato vigente, cada uno con su fecha.
-- Solo ids: no expone filas de mandatos.
create function nucleo.consorcios_con_mandato_vigente() returns setof uuid
  language sql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
    select distinct m.consorcio_id from nucleo.mandato m
     where m.persona_id = plataforma.persona_actual()
       and nucleo.mandato_vigente(m.id, nucleo.hoy_del_consorcio(m.consorcio_id))
  $$;

create or replace function nucleo.alta_mandato(
  tipo_nuevo nucleo.tipo_mandato, persona uuid, nombre text, cuit text, matricula_nueva text,
  decision uuid, desde date, hasta date, facultades text[], actor uuid, origen_pedido text)
  returns uuid
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    paquete text[];
    elegidas text[];
    fuera text[];
    nuevo uuid;
    restriccion text;
  begin
    if c is null then
      raise exception 'sin consorcio en el ámbito' using errcode = '42501';
    end if;
    -- Criterio 9: sin decisión de respaldo de este consorcio no hay mandato.
    if decision is null or not exists (
         select 1 from nucleo.decision_formal d where d.consorcio_id = c and d.id = decision) then
      raise exception 'el mandato necesita una decisión de respaldo de este consorcio'
        using errcode = 'P0001', hint = 'decision_invalida';
    end if;
    if persona is not null and not exists (select 1 from nucleo.persona p where p.id = persona) then
      raise exception 'la persona no existe' using errcode = 'P0001', hint = 'persona_invalida';
    end if;
    -- El actor es la persona de la sesión (la misma que usa la RLS): con origen 'api' no se
    -- acepta otro, y con 'soporte' no puede haber persona en la sesión (scripts del equipo).
    if origen_pedido is null or origen_pedido not in ('api', 'soporte') then
      raise exception 'origen no admitido: solo api o soporte'
        using errcode = 'P0001', hint = 'origen_invalido';
    end if;
    if origen_pedido = 'api' and actor is distinct from plataforma.persona_actual() then
      raise exception 'el actor no es la persona de la sesión'
        using errcode = 'P0001', hint = 'actor_no_coincide';
    end if;
    if origen_pedido = 'soporte' and plataforma.persona_actual() is not null then
      raise exception 'el origen soporte no admite una persona en la sesión'
        using errcode = 'P0001', hint = 'origen_invalido';
    end if;
    -- A4: nadie se da un mandato a sí mismo.
    if origen_pedido = 'api' and persona is not null and actor = persona then
      raise exception 'nadie se da un mandato a sí mismo'
        using errcode = 'P0001', hint = 'alta_de_si_mismo';
    end if;
    -- A12: miembro del Consejo y administrador, con cuenta.
    if tipo_nuevo <> 'administrador_externo' and persona is null then
      raise exception 'un mandato de % necesita la cuenta de la persona', tipo_nuevo
        using errcode = 'P0001', hint = 'mandato_sin_cuenta';
    end if;
    -- A11: administrador externo, con su CUIT normalizado y válido.
    if tipo_nuevo = 'administrador_externo' and cuit is null then
      raise exception 'un administrador externo necesita su CUIT'
        using errcode = 'P0001', hint = 'cuit_requerido';
    end if;
    if cuit is not null and not nucleo.cuit_valido(cuit) then
      raise exception 'el CUIT no es válido (11 dígitos, sin guiones, con dígito verificador)'
        using errcode = 'P0001', hint = 'cuit_invalido';
    end if;

    select array_agg(p.facultad order by p.facultad) into paquete
      from nucleo.paquete_facultad p where p.tipo = tipo_nuevo;
    elegidas := coalesce(facultades, paquete);
    if cardinality(elegidas) = 0 then
      raise exception 'el mandato necesita al menos una facultad'
        using errcode = 'P0001', hint = 'sin_facultades';
    end if;
    select array_agg(f order by f) into fuera
      from unnest(elegidas) f where not f = any (paquete);
    if fuera is not null then
      raise exception 'facultades fuera del paquete de %: %',
        tipo_nuevo, array_to_string(fuera, ', ')
        using errcode = 'P0001', hint = 'facultad_fuera_de_paquete';
    end if;

    begin
      insert into nucleo.mandato
        (consorcio_id, tipo, persona_id, mandatario_nombre, mandatario_cuit, matricula,
         decision_id, vigente_desde, vigente_hasta, registrado_por, origen)
      values (c, tipo_nuevo, persona, btrim(nombre), cuit, btrim(matricula_nueva), decision,
              desde, hasta, actor, origen_pedido)
      returning id into nuevo;
    exception when exclusion_violation then
      get stacked diagnostics restriccion = constraint_name;
      if restriccion = 'mandato_administracion_unica' then
        raise exception 'ya hay un mandato de administración en esas fechas en este consorcio'
          using errcode = 'P0001', hint = 'administracion_ocupada';
      end if;
      raise exception 'ya hay un mandato de % para esa persona en esas fechas', tipo_nuevo
        using errcode = 'P0001', hint = 'mandato_superpuesto';
    end;

    insert into nucleo.facultad_otorgada (consorcio_id, mandato_id, facultad, decision_id)
    select c, nuevo, f, decision from (select distinct unnest(elegidas) f) x;

    insert into auditoria.eventos
      (consorcio_id, actor_persona_id, origen, accion, entidad, entidad_id, despues,
       hash_previo, hash)
    values (c, actor, origen_pedido, 'consorcio.mandato.alta', 'mandato', nuevo,
            jsonb_strip_nulls(jsonb_build_object(
              'tipo', tipo_nuevo, 'persona_id', persona, 'mandatario_nombre', btrim(nombre),
              'mandatario_cuit', cuit, 'decision_id', decision, 'desde', desde,
              'hasta', hasta,
              'facultades', (select to_jsonb(array_agg(distinct f order by f))
                               from unnest(elegidas) f))),
            '', '');
    return nuevo;
  end
  $$;

drop function nucleo.baja_mandato(uuid, text, uuid, text);

-- C6, A4, A9, A10 y RN22. Toda baja tiene causa y respaldo; la ordinaria corta la vigencia hoy
-- (fecha del consorcio) y la de error_carga no toca ninguna fecha. En todas se revocan las
-- sesiones del mandatario y se audita. Nada se borra. Devuelve cuántas sesiones revocó.
create function nucleo.baja_mandato(
  mandato uuid, causa nucleo.causa_baja, decision uuid, instrumento text, descripcion text,
  actor uuid, origen_pedido text)
  returns bigint
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    m nucleo.mandato;
    hoy date;
    hasta_nuevo date;
    revocadas bigint := 0;
  begin
    if c is null then
      raise exception 'sin consorcio en el ámbito' using errcode = '42501';
    end if;
    if causa is null then
      raise exception 'la baja necesita una causa' using errcode = 'P0001', hint = 'sin_causa';
    end if;
    if origen_pedido = 'api' and actor is null then
      raise exception 'una baja por la API necesita quién la registra'
        using errcode = 'P0001', hint = 'actor_requerido';
    end if;
    -- El actor es la persona de la sesión (la misma que usa la RLS): con origen 'api' no se
    -- acepta otro, y con 'soporte' no puede haber persona en la sesión (scripts del equipo).
    if origen_pedido is null or origen_pedido not in ('api', 'soporte') then
      raise exception 'origen no admitido: solo api o soporte'
        using errcode = 'P0001', hint = 'origen_invalido';
    end if;
    if origen_pedido = 'api' and actor is distinct from plataforma.persona_actual() then
      raise exception 'el actor no es la persona de la sesión'
        using errcode = 'P0001', hint = 'actor_no_coincide';
    end if;
    if origen_pedido = 'soporte' and plataforma.persona_actual() is not null then
      raise exception 'el origen soporte no admite una persona en la sesión'
        using errcode = 'P0001', hint = 'origen_invalido';
    end if;
    select * into m from nucleo.mandato x where x.consorcio_id = c and x.id = mandato for update;
    if m.id is null then
      raise exception 'el mandato no existe en este consorcio'
        using errcode = 'P0001', hint = 'mandato_invalido';
    end if;
    -- A4: la renuncia la registra otra persona.
    if origen_pedido = 'api' and actor = m.persona_id then
      raise exception 'nadie se da de baja a sí mismo'
        using errcode = 'P0001', hint = 'baja_de_si_mismo';
    end if;
    -- A10: irreversible. Un mandato con causa no admite otra baja.
    if m.causa_baja is not null then
      raise exception 'el mandato ya tiene una baja registrada'
        using errcode = 'P0001', hint = 'mandato_ya_dado_de_baja';
    end if;
    -- C6: respaldo según la causa.
    if causa = 'remocion' then
      if decision is null or instrumento is not null then
        raise exception 'la remoción se respalda solo con una decisión de asamblea'
          using errcode = 'P0001', hint = 'respaldo_invalido';
      end if;
      if not exists (select 1 from nucleo.decision_formal d
                      where d.consorcio_id = c and d.id = decision and d.tipo = 'asamblea') then
        raise exception 'la remoción necesita una decisión de asamblea de este consorcio'
          using errcode = 'P0001', hint = 'decision_invalida';
      end if;
    elsif decision is not null or instrumento is null or btrim(instrumento) = '' then
      raise exception 'la baja por % se respalda solo con la referencia del instrumento', causa
        using errcode = 'P0001', hint = 'respaldo_invalido';
    end if;

    hoy := nucleo.hoy_del_consorcio(c);
    hasta_nuevo := m.vigente_hasta;
    if causa <> 'error_carga' then
      if m.vigente_hasta is not null and m.vigente_hasta <= hoy then
        raise exception 'el mandato ya terminó' using errcode = 'P0001', hint = 'mandato_terminado';
      end if;
      hasta_nuevo := greatest(m.vigente_desde, hoy);
    end if;

    update nucleo.mandato
       set vigente_hasta = hasta_nuevo, finalizado_por = actor, finalizado_en = now(),
           motivo_fin = btrim(descripcion), causa_baja = causa, baja_decision_id = decision,
           instrumento_referencia = btrim(instrumento)
     where id = m.id;
    if m.persona_id is not null then
      revocadas := nucleo.revocar_sesiones_de(m.persona_id);
    end if;

    insert into auditoria.eventos
      (consorcio_id, actor_persona_id, origen, accion, entidad, entidad_id, antes, despues,
       hash_previo, hash)
    values (c, actor, origen_pedido, 'consorcio.mandato.baja', 'mandato', m.id,
            jsonb_build_object('vigente_desde', m.vigente_desde,
                               'vigente_hasta', m.vigente_hasta),
            jsonb_strip_nulls(jsonb_build_object(
              'vigente_hasta', hasta_nuevo, 'causa', causa, 'decision_id', decision,
              'instrumento_referencia', btrim(instrumento), 'motivo', btrim(descripcion),
              'sesiones_revocadas', revocadas)),
            '', '');
    return revocadas;
  end
  $$;
"""


def _paquetes() -> str:
    filas = ",\n  ".join(f"('{t}', '{f}')" for t, f in PAQUETES_NUEVOS)
    return f"insert into nucleo.paquete_facultad (tipo, facultad) values\n  {filas};\n"


def _quitar_paquetes() -> str:
    condicion = " or ".join(f"(tipo = '{t}' and facultad = '{f}')" for t, f in PAQUETES_NUEVOS)
    return f"delete from nucleo.paquete_facultad where {condicion};\n"


PRIVILEGIOS = f"""
revoke all on function {FIRMA_CUIT}, {FIRMA_VIGENTE}, {FIRMA_CONSORCIOS}, {FIRMA_BAJA},
  {FIRMA_ALTA} from public;
grant execute on function {FIRMA_CUIT}, {FIRMA_VIGENTE}, {FIRMA_CONSORCIOS}, {FIRMA_BAJA},
  {FIRMA_ALTA} to cem_app;
"""

# Sin datos se vuelve exactamente a 0011. Con cualquier mandato, se niega y no toca nada.
GUARDA_DOWNGRADE = r"""
do $$
begin
  if exists (select 1 from nucleo.mandato) then
    raise exception 'el downgrade de 0012 se niega: hay mandatos; perdería causas y '
                    'respaldos y dejaría facultades fuera del paquete de 0011'
      using errcode = 'P0001', hint = 'downgrade_con_datos';
  end if;
end
$$;
"""

DOWNGRADE = f"""
drop function {FIRMA_BAJA};
drop function {FIRMA_CONSORCIOS};
drop function {FIRMA_VIGENTE};
{ALTA_0011}
{BAJA_0011}
revoke all on function {FIRMA_ALTA}, {FIRMA_BAJA_0011} from public;
grant execute on function {FIRMA_ALTA}, {FIRMA_BAJA_0011} to cem_app;
alter table nucleo.mandato
  drop constraint mandato_administracion_unica,
  drop constraint mandato_consejo_sin_superposicion,
  drop constraint mandato_cuit_valido,
  drop constraint mandato_externo_con_cuit,
  drop constraint mandato_con_cuenta,
  drop constraint mandato_otras_causas_con_instrumento,
  drop constraint mandato_remocion_con_decision,
  drop constraint mandato_baja_un_respaldo,
  drop constraint mandato_terminado_con_causa,
  drop constraint mandato_baja_decision_mismo_consorcio,
  drop column instrumento_referencia,
  drop column baja_decision_id,
  drop column causa_baja,
  add exclude using gist (consorcio_id with =, tipo with =, persona_id with =,
                          daterange(vigente_desde, vigente_hasta) with &&);
drop function {FIRMA_CUIT};
drop type nucleo.causa_baja;
{_quitar_paquetes()}
"""


def upgrade() -> None:
    op.execute(UPGRADE + _paquetes() + PRIVILEGIOS)


def downgrade() -> None:
    op.execute(GUARDA_DOWNGRADE + DOWNGRADE)
