"""A15, E26 y R10 en las funciones de mandatos y decisiones (2b-4b, PR 1).

Diseño: `doc/diseno/H2b_4b_Diseno.md` del repositorio CEM (§2 a §4).

- A15 (P2, opción A): `alta_mandato` y la remoción de `baja_mandato`, con origen `api`, rechazan
  con `decision_registrada_por_el_actor` si el actor registró la decisión que usa. Las
  decisiones del equipo (`registrada_por` nulo) no la activan.
- E26: nombre, matrícula, vigencia, facultad nula, descripción de la baja, instrumento largo, y
  tipo, fecha, descripción, referencia y sha256 de la decisión salen con su motivo en vez de un
  `check_violation` o `not_null_violation` crudo. Los `check` siguen como última barrera.
- R10 y P1-d: el alta de un miembro del Consejo o de un administrador externo con cuenta toma
  el mismo bloqueo consultivo (`persona:<correo>`) que la aceptación de A7 y, bajo él, rechaza
  con `consejo_incompatible_con_administracion` si la persona tiene el otro mandato superpuesto
  (el predicado de A7, en los dos sentidos).
- `alta_mandato` suma `actor_requerido` y el soporte sin actor (#66), como las otras dos.

Solo cambian las tres funciones: no hay datos que migrar ni barrera de downgrade. El downgrade
restaura las definiciones de 0014 tal cual.

Revisión: 0016
Anterior: 0015
"""

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None

UPGRADE = r"""
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
    dec nucleo.decision_formal;
    correo_persona text;
  begin
    if c is null then
      raise exception 'sin consorcio en el ámbito' using errcode = '42501';
    end if;
    -- Criterio 9: sin decisión de respaldo de este consorcio no hay mandato.
    select * into dec from nucleo.decision_formal d where d.consorcio_id = c and d.id = decision;
    if dec.id is null then
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
    -- E26 (0016): como baja_mandato y registrar_decision (#66); sin esto, `mandato_check1`.
    if origen_pedido = 'api' and actor is null then
      raise exception 'un alta por la API necesita quién la registra'
        using errcode = 'P0001', hint = 'actor_requerido';
    end if;
    if origen_pedido = 'api' and actor is distinct from plataforma.persona_actual() then
      raise exception 'el actor no es la persona de la sesión'
        using errcode = 'P0001', hint = 'actor_no_coincide';
    end if;
    if origen_pedido = 'soporte' and plataforma.persona_actual() is not null then
      raise exception 'el origen soporte no admite una persona en la sesión'
        using errcode = 'P0001', hint = 'origen_invalido';
    end if;
    if origen_pedido = 'soporte' and actor is not null then
      raise exception 'el origen soporte no lleva actor: lo registra el equipo'
        using errcode = 'P0001', hint = 'origen_invalido';
    end if;
    -- A4: nadie se da un mandato a sí mismo.
    if origen_pedido = 'api' and persona is not null and actor = persona then
      raise exception 'nadie se da un mandato a sí mismo'
        using errcode = 'P0001', hint = 'alta_de_si_mismo';
    end if;
    -- A15 (P2, 0016): quien registró la decisión no la usa para dar de alta. Las decisiones del
    -- equipo (registrada_por nulo) no la activan.
    if origen_pedido = 'api' and dec.registrada_por = actor then
      raise exception 'quien registró la decisión no la usa para dar de alta'
        using errcode = 'P0001', hint = 'decision_registrada_por_el_actor';
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
    -- E26 (0016): lo que antes salía como check_violation crudo sale con su motivo, después
    -- de A12 y A11 para no cambiar qué motivo sale primero.
    if (nombre is null and persona is null)
       or (nombre is not null and length(btrim(nombre)) not between 1 and 200) then
      raise exception 'el nombre del mandatario falta o no tiene entre 1 y 200 caracteres'
        using errcode = 'P0001', hint = 'nombre_invalido';
    end if;
    if matricula_nueva is not null and length(btrim(matricula_nueva)) not between 1 and 100 then
      raise exception 'la matrícula no tiene entre 1 y 100 caracteres'
        using errcode = 'P0001', hint = 'matricula_invalida';
    end if;
    if desde is null or (hasta is not null and hasta < desde) then
      raise exception 'la vigencia necesita un desde, y el hasta no puede ser anterior'
        using errcode = 'P0001', hint = 'vigencia_invalida';
    end if;

    select array_agg(p.facultad order by p.facultad) into paquete
      from nucleo.paquete_facultad p where p.tipo = tipo_nuevo;
    elegidas := coalesce(facultades, paquete);
    if cardinality(elegidas) = 0 then
      raise exception 'el mandato necesita al menos una facultad'
        using errcode = 'P0001', hint = 'sin_facultades';
    end if;
    if array_position(elegidas, null) is not null then
      raise exception 'una facultad nula no está en el paquete de %', tipo_nuevo
        using errcode = 'P0001', hint = 'facultad_fuera_de_paquete';
    end if;
    select array_agg(f order by f) into fuera
      from unnest(elegidas) f where not f = any (paquete);
    if fuera is not null then
      raise exception 'facultades fuera del paquete de %: %',
        tipo_nuevo, array_to_string(fuera, ', ')
        using errcode = 'P0001', hint = 'facultad_fuera_de_paquete';
    end if;

    -- R10 y P1-d (0016): el Consejo fiscaliza y no co-administra, en los dos sentidos. El alta
    -- de un Consejo o de un AE con cuenta toma el mismo bloqueo consultivo que la aceptación de
    -- A7 y, bajo él, aplica su predicado: el otro mandato de la persona, que no sea un error de
    -- carga, vigente o futuro y superpuesto. Un alta que ya terminó (histórica) no lo activa,
    -- como en A7.
    if persona is not null and tipo_nuevo in ('miembro_consejo', 'administrador_externo')
       and (hasta is null or hasta > nucleo.hoy_del_consorcio(c)) then
      select p.correo into strict correo_persona from nucleo.persona p where p.id = persona;
      perform pg_advisory_xact_lock(hashtextextended('persona:' || correo_persona, 0));
      if exists (
           select 1 from nucleo.mandato x
            where x.consorcio_id = c and x.persona_id = persona
              and x.tipo = case tipo_nuevo when 'miembro_consejo'
                                then 'administrador_externo'::nucleo.tipo_mandato
                                else 'miembro_consejo'::nucleo.tipo_mandato end
              and x.causa_baja is distinct from 'error_carga'
              and (x.vigente_hasta is null or x.vigente_hasta > nucleo.hoy_del_consorcio(c))
              and daterange(x.vigente_desde, x.vigente_hasta) && daterange(desde, hasta)) then
        raise exception 'la persona tendría un mandato de Consejo y una administración externa'
                        ' superpuestos'
          using errcode = 'P0001', hint = 'consejo_incompatible_con_administracion';
      end if;
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
              -- E31 (0014): sin nombre ni CUIT; quedan en la fila del mandato.
              'tipo', tipo_nuevo, 'persona_id', persona, 'decision_id', decision,
              'desde', desde, 'hasta', hasta,
              'facultades', (select to_jsonb(array_agg(distinct f order by f))
                               from unnest(elegidas) f))),
            '', '');
    return nuevo;
  end
  $$;

create or replace function nucleo.baja_mandato(
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
    -- E26 (0016): la descripción es obligatoria a propósito; sin ella, un motivo y no un check.
    if descripcion is null or length(btrim(descripcion)) not between 1 and 500 then
      raise exception 'la baja necesita una descripción de 1 a 500 caracteres'
        using errcode = 'P0001', hint = 'descripcion_invalida';
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
    -- #66: con soporte no hay actor; si no, la baja quedaría a nombre de quien diga el script.
    if origen_pedido = 'soporte' and actor is not null then
      raise exception 'el origen soporte no lleva actor: lo registra el equipo'
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
      -- A15 (P2, 0016): quien registró la decisión no la usa para remover.
      if origen_pedido = 'api' and exists (
           select 1 from nucleo.decision_formal d
            where d.consorcio_id = c and d.id = decision and d.registrada_por = actor) then
        raise exception 'quien registró la decisión no la usa para remover'
          using errcode = 'P0001', hint = 'decision_registrada_por_el_actor';
      end if;
    elsif decision is not null or instrumento is null or btrim(instrumento) = ''
          or length(btrim(instrumento)) > 300 then
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
              -- E31 (0014): sin la referencia ni la descripción; quedan en la fila.
              'vigente_hasta', hasta_nuevo, 'causa', causa, 'decision_id', decision,
              'sesiones_revocadas', revocadas)),
            '', '');
    return revocadas;
  end
  $$;

CREATE OR REPLACE FUNCTION nucleo.registrar_decision(
  tipo_decision nucleo.tipo_decision,
  fecha_decision date,
  descripcion_decision text,
  referencia text,
  actor uuid,
  origen_pedido text,
  sha256 text DEFAULT NULL::text)
 RETURNS uuid
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog', 'pg_temp'
AS $function$
  declare
    c uuid := plataforma.consorcio_actual();
    d uuid;
  begin
    if c is null then
      raise exception 'sin consorcio en el ámbito' using errcode = '42501';
    end if;
    -- #66: el actor es la persona de la sesión, como en `alta_mandato` y `baja_mandato` (0012).
    if origen_pedido is null or origen_pedido not in ('api', 'soporte') then
      raise exception 'origen no admitido: solo api o soporte'
        using errcode = 'P0001', hint = 'origen_invalido';
    end if;
    if origen_pedido = 'api' and actor is null then
      raise exception 'un registro por la API necesita quién lo hace'
        using errcode = 'P0001', hint = 'actor_requerido';
    end if;
    if origen_pedido = 'api' and actor is distinct from plataforma.persona_actual() then
      raise exception 'el actor no es la persona de la sesión'
        using errcode = 'P0001', hint = 'actor_no_coincide';
    end if;
    if origen_pedido = 'soporte' and plataforma.persona_actual() is not null then
      raise exception 'el origen soporte no admite una persona en la sesión'
        using errcode = 'P0001', hint = 'origen_invalido';
    end if;
    if origen_pedido = 'soporte' and actor is not null then
      raise exception 'el origen soporte no lleva actor: lo registra el equipo'
        using errcode = 'P0001', hint = 'origen_invalido';
    end if;
    -- E26 (0016): lo que antes salía como check_violation o not_null crudo, con su motivo.
    if tipo_decision is null or fecha_decision is null then
      raise exception 'la decisión necesita tipo y fecha'
        using errcode = 'P0001', hint = 'decision_incompleta';
    end if;
    if descripcion_decision is null
       or length(btrim(descripcion_decision)) not between 1 and 500 then
      raise exception 'la descripción no tiene entre 1 y 500 caracteres'
        using errcode = 'P0001', hint = 'descripcion_invalida';
    end if;
    if referencia is null or length(btrim(referencia)) not between 1 and 300 then
      raise exception 'la referencia del acta no tiene entre 1 y 300 caracteres'
        using errcode = 'P0001', hint = 'referencia_invalida';
    end if;
    if sha256 is not null and lower(sha256) !~ '^[0-9a-f]{64}$' then
      raise exception 'el sha256 del acta no es hexadecimal de 64'
        using errcode = 'P0001', hint = 'sha256_invalido';
    end if;
    insert into nucleo.decision_formal
      (consorcio_id, tipo, fecha, descripcion, referencia_acta, registrada_por, origen,
       sha256_acta)
    values (c, tipo_decision, fecha_decision, btrim(descripcion_decision), btrim(referencia),
            actor, origen_pedido, lower(sha256))
    returning id into d;
    insert into auditoria.eventos
      (consorcio_id, actor_persona_id, origen, accion, entidad, entidad_id, despues,
       hash_previo, hash)
    values (c, actor, origen_pedido, 'consorcio.decision.registrar', 'decision_formal', d,
            jsonb_strip_nulls(jsonb_build_object(
              -- E31 (0014): sin la referencia del acta; queda en la fila de la decisión.
              'tipo', tipo_decision, 'fecha', fecha_decision, 'sha256_acta', lower(sha256))),
            '', '');
    return d;
  end
  $function$;
"""

# Las definiciones de 0014, copiadas tal cual.
DOWNGRADE = r"""
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
              -- E31 (0014): sin nombre ni CUIT; quedan en la fila del mandato.
              'tipo', tipo_nuevo, 'persona_id', persona, 'decision_id', decision,
              'desde', desde, 'hasta', hasta,
              'facultades', (select to_jsonb(array_agg(distinct f order by f))
                               from unnest(elegidas) f))),
            '', '');
    return nuevo;
  end
  $$;

create or replace function nucleo.baja_mandato(
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
    -- #66: con soporte no hay actor; si no, la baja quedaría a nombre de quien diga el script.
    if origen_pedido = 'soporte' and actor is not null then
      raise exception 'el origen soporte no lleva actor: lo registra el equipo'
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
              -- E31 (0014): sin la referencia ni la descripción; quedan en la fila.
              'vigente_hasta', hasta_nuevo, 'causa', causa, 'decision_id', decision,
              'sesiones_revocadas', revocadas)),
            '', '');
    return revocadas;
  end
  $$;

CREATE OR REPLACE FUNCTION nucleo.registrar_decision(
  tipo_decision nucleo.tipo_decision,
  fecha_decision date,
  descripcion_decision text,
  referencia text,
  actor uuid,
  origen_pedido text,
  sha256 text DEFAULT NULL::text)
 RETURNS uuid
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog', 'pg_temp'
AS $function$
  declare
    c uuid := plataforma.consorcio_actual();
    d uuid;
  begin
    if c is null then
      raise exception 'sin consorcio en el ámbito' using errcode = '42501';
    end if;
    -- #66: el actor es la persona de la sesión, como en `alta_mandato` y `baja_mandato` (0012).
    if origen_pedido is null or origen_pedido not in ('api', 'soporte') then
      raise exception 'origen no admitido: solo api o soporte'
        using errcode = 'P0001', hint = 'origen_invalido';
    end if;
    if origen_pedido = 'api' and actor is null then
      raise exception 'un registro por la API necesita quién lo hace'
        using errcode = 'P0001', hint = 'actor_requerido';
    end if;
    if origen_pedido = 'api' and actor is distinct from plataforma.persona_actual() then
      raise exception 'el actor no es la persona de la sesión'
        using errcode = 'P0001', hint = 'actor_no_coincide';
    end if;
    if origen_pedido = 'soporte' and plataforma.persona_actual() is not null then
      raise exception 'el origen soporte no admite una persona en la sesión'
        using errcode = 'P0001', hint = 'origen_invalido';
    end if;
    if origen_pedido = 'soporte' and actor is not null then
      raise exception 'el origen soporte no lleva actor: lo registra el equipo'
        using errcode = 'P0001', hint = 'origen_invalido';
    end if;
    insert into nucleo.decision_formal
      (consorcio_id, tipo, fecha, descripcion, referencia_acta, registrada_por, origen,
       sha256_acta)
    values (c, tipo_decision, fecha_decision, btrim(descripcion_decision), btrim(referencia),
            actor, origen_pedido, lower(sha256))
    returning id into d;
    insert into auditoria.eventos
      (consorcio_id, actor_persona_id, origen, accion, entidad, entidad_id, despues,
       hash_previo, hash)
    values (c, actor, origen_pedido, 'consorcio.decision.registrar', 'decision_formal', d,
            jsonb_strip_nulls(jsonb_build_object(
              -- E31 (0014): sin la referencia del acta; queda en la fila de la decisión.
              'tipo', tipo_decision, 'fecha', fecha_decision, 'sha256_acta', lower(sha256))),
            '', '');
    return d;
  end
  $function$;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
