"""Vínculo con procedencia y confirmación, padrón mínimo y eventos mínimos (2b-4, PR 1).

Diseño: `doc/diseno/H2b_4_Diseno.md` del repositorio CEM (§6, §11.1), con las decisiones del
propietario del 19-10-2026 (P4, P5) y E31.

- `nucleo.vinculo` suma `fuente`, `origen`, `registrado_por` y la confirmación
  (`confirmado_por`, `confirmado_en`, `confirmacion_referencia`). La procedencia de lo existente
  sale del primer evento `identidad.invitacion.aceptar` que nombra cada vínculo; si alguno no
  tiene, el upgrade se niega (`vinculo_sin_procedencia`, criterio 9).
- Restricciones: origen `api`/`soporte`, `registrado_por` según el origen, confirmación completa
  con referencia de 1 a 300, nadie confirma el propio y quien registró no confirma (P4-c).
- `cem_app` pierde `INSERT`, `UPDATE` y `DELETE` sobre `vinculo` (escribe por funciones) y no
  lee `confirmacion_referencia`.
- `aceptar_invitacion` carga la procedencia; nada más cambia.
- `nucleo.confirmar_vinculo` y `nucleo.padron_del_consorcio` (vigentes y futuros, sin correo).
- E31: `alta_mandato`, `baja_mandato` y `registrar_decision` dejan de escribir en la auditoría el
  nombre y el CUIT del mandatario, la descripción, la referencia del instrumento y la del acta.
  Quedan en sus filas. Hay 0 eventos de mandatos y de decisiones en los dos entornos.

Downgrade: se niega si hay algún vínculo confirmado (`downgrade_con_datos`); si no, restaura las
definiciones anteriores (`aceptar_invitacion` de 0007, `alta_mandato` de 0012, `baja_mandato` y
`registrar_decision` de 0013), los permisos y la tabla.

Revisión: 0014
Anterior: 0013
"""

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

UPGRADE = r"""
create type nucleo.fuente_vinculo as enum ('invitacion', 'importacion', 'declaracion', 'documento');

alter table nucleo.vinculo
  add column fuente                  nucleo.fuente_vinculo,
  add column origen                  text,
  add column registrado_por          uuid references nucleo.persona (id),
  add column confirmado_por          uuid references nucleo.persona (id),
  add column confirmado_en           timestamptz,
  add column confirmacion_referencia text;

-- Procedencia de lo existente: hoy todo vínculo nace de `aceptar_invitacion`, que audita el
-- `vinculo_id`. El primer evento que lo nombra es el que lo creó (las aceptaciones que
-- reutilizan un vínculo también lo nombran, después). No se une por la tupla del vínculo.
with primera as (
  select distinct on ((e.despues ->> 'vinculo_id')::uuid)
         (e.despues ->> 'vinculo_id')::uuid as vinculo_id, e.consorcio_id, e.entidad_id
    from auditoria.eventos e
   where e.accion = 'identidad.invitacion.aceptar' and e.entidad = 'invitacion'
     and (e.despues ->> 'vinculo_id') is not null
   order by (e.despues ->> 'vinculo_id')::uuid, e.secuencia
)
update nucleo.vinculo v
   set fuente = 'invitacion',
       origen = case when i.invitada_por is null then 'soporte' else 'api' end,
       registrado_por = i.invitada_por
  from primera p
  join nucleo.invitacion i on i.id = p.entidad_id and i.consorcio_id = p.consorcio_id
 where v.id = p.vinculo_id and v.consorcio_id = p.consorcio_id;

-- Criterio 9: nunca se inventa una procedencia.
do $guarda$
begin
  if exists (select 1 from nucleo.vinculo where fuente is null) then
    raise exception 'hay vínculos sin evento de aceptación: no se inventa su procedencia'
      using errcode = 'P0001', hint = 'vinculo_sin_procedencia';
  end if;
end
$guarda$;

alter table nucleo.vinculo
  alter column fuente set not null,
  alter column origen set not null,
  add constraint vinculo_origen_valido check (origen in ('api', 'soporte')),
  add constraint vinculo_registrado_segun_origen
    check ((origen = 'api') = (registrado_por is not null)),
  add constraint vinculo_confirmacion_completa
    check ((confirmado_por is null) = (confirmado_en is null)
           and (confirmado_en is null) = (confirmacion_referencia is null)),
  add constraint vinculo_confirmacion_referencia
    check (confirmacion_referencia is null
           or length(btrim(confirmacion_referencia)) between 1 and 300),
  -- Nadie confirma su propio vínculo, aunque escriba el dueño de la tabla.
  add constraint vinculo_no_confirma_el_propio
    check (confirmado_por is null or confirmado_por <> persona_id),
  -- P4-c: quien registró el vínculo no lo confirma. Sin quien registró (soporte), no restringe.
  add constraint vinculo_no_confirma_quien_registro
    check (confirmado_por is null or registrado_por is null or confirmado_por <> registrado_por);

-- Las escrituras sobre el vínculo van solo por funciones (como `mandato` desde 0011). La
-- referencia del título tampoco se lee por la RLS: solo la ve quien lee la fila como dueño
-- (ninguna ruta de 2b-4 la devuelve). `cem_app` lee el resto de las columnas.
revoke select, insert, update, delete on nucleo.vinculo from cem_app;
grant select (id, consorcio_id, unidad_id, persona_id, tipo, vigente_desde, vigente_hasta,
  creado_en, fuente, origen, registrado_por, confirmado_por, confirmado_en)
  on nucleo.vinculo to cem_app;

create or replace function nucleo.aceptar_invitacion(
  token bytea, persona_sesion uuid, nombre_elegido text, sesion_token bytea,
  dispositivo_pedido uuid, agente_pedido text, ip_pedido inet, vigencia interval)
  returns table (resultado text, persona uuid, consorcio uuid, cuenta_nueva boolean,
                 sesion uuid, vence timestamptz)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    i nucleo.invitacion;
    motivo text;
    p uuid;
    nueva boolean := false;
    s uuid;
    v timestamptz;
    hoy date;
    vinculo uuid;
  begin
    select * into i from nucleo.invitacion x where x.token_hash = token for update;
    motivo := nucleo.motivo_invitacion_invalida(i);
    if motivo is not null then
      return query select motivo, null::uuid, null::uuid, null::boolean, null::uuid,
                          null::timestamptz;
      return;
    end if;
    -- Serializa el alta por correo: dos invitaciones al mismo correo nuevo aceptadas a la vez
    -- no crean dos personas.
    perform pg_advisory_xact_lock(hashtextextended('persona:' || i.correo, 0));
    select x.id into p from nucleo.persona x
     where x.correo = i.correo and x.eliminado_en is null;
    if p is null then
      if persona_sesion is not null then
        return query select 'otra_persona'::text, null::uuid, null::uuid, null::boolean,
                            null::uuid, null::timestamptz;
        return;
      end if;
      insert into nucleo.persona (correo, nombre)
      values (i.correo, btrim(coalesce(nombre_elegido, i.nombre)))
      returning id into p;
      nueva := true;
    elsif persona_sesion is null then
      return query select 'requiere_sesion'::text, null::uuid, null::uuid, null::boolean,
                          null::uuid, null::timestamptz;
      return;
    elsif persona_sesion <> p then
      return query select 'otra_persona'::text, null::uuid, null::uuid, null::boolean,
                          null::uuid, null::timestamptz;
      return;
    end if;

    select (now() at time zone c.zona_horaria)::date into hoy
      from nucleo.consorcio c where c.id = i.consorcio_id;
    select v2.id into vinculo from nucleo.vinculo v2
     where v2.consorcio_id = i.consorcio_id and v2.unidad_id = i.unidad_id
       and v2.persona_id = p and v2.tipo = i.tipo
       and daterange(v2.vigente_desde, v2.vigente_hasta) && daterange(hoy, null)
     limit 1;
    if vinculo is null then
      -- 0014: procedencia. Quien invitó es quien declaró el carácter (criterio 6); una
      -- invitación cargada por el equipo no tiene `invitada_por` y queda como soporte.
      insert into nucleo.vinculo (consorcio_id, unidad_id, persona_id, tipo, vigente_desde,
                                  fuente, origen, registrado_por)
      values (i.consorcio_id, i.unidad_id, p, i.tipo, hoy, 'invitacion',
              case when i.invitada_por is null then 'soporte' else 'api' end, i.invitada_por)
      returning id into vinculo;
    end if;
    update nucleo.invitacion set aceptada_en = now(), persona_id = p where id = i.id;

    insert into auditoria.eventos
      (consorcio_id, actor_persona_id, origen, accion, entidad, entidad_id, despues, ip,
       hash_previo, hash)
    values (i.consorcio_id, p, 'api', 'identidad.invitacion.aceptar', 'invitacion', i.id,
            jsonb_build_object('unidad_id', i.unidad_id, 'tipo', i.tipo,
                               'vinculo_id', vinculo, 'cuenta_nueva', nueva),
            ip_pedido, '', '');

    if nueva then
      insert into nucleo.sesion (persona_id, token_hash, dispositivo, agente, ip, vence_en)
      values (p, sesion_token, dispositivo_pedido, agente_pedido, ip_pedido, now() + vigencia)
      returning id, vence_en into s, v;
    end if;
    return query select 'ok'::text, p, i.consorcio_id, nueva, s, v;
  end
  $$;

-- Confirmar el carácter de un vínculo con la referencia de un título (P4). La facultad la
-- decide la aplicación (`padron.confirmar_vinculo`); la base garantiza ámbito, actor, estado,
-- que nadie confirme el propio y que quien lo registró no lo confirme (P4-c).
create function nucleo.confirmar_vinculo(
  vinculo_a_confirmar uuid, referencia text, actor uuid, origen_pedido text)
  returns void
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    v nucleo.vinculo;
    hoy date;
  begin
    if c is null then
      raise exception 'sin consorcio en el ámbito' using errcode = '42501';
    end if;
    -- Soporte no confirma (RN23): solo la API, con la persona de la sesión.
    if origen_pedido is distinct from 'api' then
      raise exception 'origen no admitido: solo api'
        using errcode = 'P0001', hint = 'origen_invalido';
    end if;
    if actor is null then
      raise exception 'una confirmación necesita quién la hace'
        using errcode = 'P0001', hint = 'actor_requerido';
    end if;
    if actor is distinct from plataforma.persona_actual() then
      raise exception 'el actor no es la persona de la sesión'
        using errcode = 'P0001', hint = 'actor_no_coincide';
    end if;
    if referencia is null or length(btrim(referencia)) not between 1 and 300 then
      raise exception 'la confirmación necesita la referencia del título'
        using errcode = 'P0001', hint = 'respaldo_requerido';
    end if;
    select * into v from nucleo.vinculo x
     where x.consorcio_id = c and x.id = vinculo_a_confirmar for update;
    if v.id is null then
      raise exception 'el vínculo no existe en este consorcio'
        using errcode = 'P0001', hint = 'vinculo_invalido';
    end if;
    if v.persona_id = actor then
      raise exception 'nadie confirma su propio vínculo'
        using errcode = 'P0001', hint = 'confirmar_propio_vinculo';
    end if;
    if v.registrado_por = actor then
      raise exception 'quien registró el vínculo no lo confirma'
        using errcode = 'P0001', hint = 'confirmar_lo_que_registro';
    end if;
    hoy := nucleo.hoy_del_consorcio(c);
    if v.vigente_hasta is not null and v.vigente_hasta <= hoy then
      raise exception 'el vínculo ya terminó'
        using errcode = 'P0001', hint = 'vinculo_finalizado';
    end if;
    if v.confirmado_en is not null then
      raise exception 'el vínculo ya está confirmado'
        using errcode = 'P0001', hint = 'vinculo_ya_confirmado';
    end if;
    update nucleo.vinculo
       set confirmado_por = actor, confirmado_en = now(),
           confirmacion_referencia = btrim(referencia)
     where id = v.id;
    -- La referencia queda en la fila: la auditoría no se puede borrar y no lleva nombres.
    insert into auditoria.eventos
      (consorcio_id, actor_persona_id, origen, accion, entidad, entidad_id, antes, despues,
       hash_previo, hash)
    values (c, actor, 'api', 'consorcio.vinculo.confirmar', 'vinculo', v.id,
            jsonb_build_object('confirmado', false),
            jsonb_build_object('confirmado', true, 'unidad_id', v.unidad_id, 'tipo', v.tipo),
            '', '');
  end
  $$;

-- Padrón mínimo (D6, P5): vínculos vigentes y futuros, sin correo ni teléfono. `security
-- definer` porque la RLS `propia` de persona no deja leer nombres ajenos; por eso exige, como
-- segunda barrera, un mandato vigente hoy con `padron.ver` en el consorcio del ámbito. El
-- filtro por recurso lo hace la aplicación, fila por fila.
create function nucleo.padron_del_consorcio()
  returns table (vinculo_id uuid, unidad_id uuid, designacion text, nombre text,
                 tipo nucleo.tipo_vinculo, vigente_desde date, vigente_hasta date,
                 confirmado boolean)
  language plpgsql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    quien uuid := plataforma.persona_actual();
    hoy date;
  begin
    if c is null or quien is null then
      raise exception 'sin ámbito para leer el padrón' using errcode = '42501';
    end if;
    hoy := nucleo.hoy_del_consorcio(c);
    if not exists (
      select 1 from nucleo.mandato m
        join nucleo.facultad_otorgada f
          on f.consorcio_id = m.consorcio_id and f.mandato_id = m.id
       where m.consorcio_id = c and m.persona_id = quien and f.facultad = 'padron.ver'
         and nucleo.mandato_vigente(m.id, hoy)) then
      raise exception 'sin padron.ver en este consorcio' using errcode = '42501';
    end if;
    return query
      select v.id, v.unidad_id, u.designacion, pe.nombre, v.tipo, v.vigente_desde,
             v.vigente_hasta, v.confirmado_en is not null
        from nucleo.vinculo v
        join nucleo.unidad u on u.consorcio_id = v.consorcio_id and u.id = v.unidad_id
        join nucleo.persona pe on pe.id = v.persona_id
       where v.consorcio_id = c
         and (v.vigente_hasta is null or v.vigente_hasta > hoy)
         and u.eliminado_en is null and pe.eliminado_en is null
       order by u.designacion, v.tipo, pe.nombre, v.id;
  end
  $$;

revoke all on function
  nucleo.confirmar_vinculo(uuid, text, uuid, text),
  nucleo.padron_del_consorcio()
  from public;
grant execute on function
  nucleo.confirmar_vinculo(uuid, text, uuid, text),
  nucleo.padron_del_consorcio()
  to cem_app;

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

DOWNGRADE = r"""
-- Las confirmaciones no se pueden derivar de nada: con alguna, no se baja (como 0012 con los
-- mandatos). La procedencia sí se puede volver a derivar de los eventos de aceptación.
do $guarda$
begin
  if exists (select 1 from nucleo.vinculo where confirmado_en is not null) then
    raise exception 'hay vínculos confirmados: bajar a 0013 perdería las confirmaciones'
      using errcode = 'P0001', hint = 'downgrade_con_datos';
  end if;
end
$guarda$;

drop function nucleo.padron_del_consorcio();
drop function nucleo.confirmar_vinculo(uuid, text, uuid, text);

create or replace function nucleo.aceptar_invitacion(
  token bytea, persona_sesion uuid, nombre_elegido text, sesion_token bytea,
  dispositivo_pedido uuid, agente_pedido text, ip_pedido inet, vigencia interval)
  returns table (resultado text, persona uuid, consorcio uuid, cuenta_nueva boolean,
                 sesion uuid, vence timestamptz)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    i nucleo.invitacion;
    motivo text;
    p uuid;
    nueva boolean := false;
    s uuid;
    v timestamptz;
    hoy date;
    vinculo uuid;
  begin
    select * into i from nucleo.invitacion x where x.token_hash = token for update;
    motivo := nucleo.motivo_invitacion_invalida(i);
    if motivo is not null then
      return query select motivo, null::uuid, null::uuid, null::boolean, null::uuid,
                          null::timestamptz;
      return;
    end if;
    -- Serializa el alta por correo: dos invitaciones al mismo correo nuevo aceptadas a la vez
    -- no crean dos personas.
    perform pg_advisory_xact_lock(hashtextextended('persona:' || i.correo, 0));
    select x.id into p from nucleo.persona x
     where x.correo = i.correo and x.eliminado_en is null;
    if p is null then
      if persona_sesion is not null then
        return query select 'otra_persona'::text, null::uuid, null::uuid, null::boolean,
                            null::uuid, null::timestamptz;
        return;
      end if;
      insert into nucleo.persona (correo, nombre)
      values (i.correo, btrim(coalesce(nombre_elegido, i.nombre)))
      returning id into p;
      nueva := true;
    elsif persona_sesion is null then
      return query select 'requiere_sesion'::text, null::uuid, null::uuid, null::boolean,
                          null::uuid, null::timestamptz;
      return;
    elsif persona_sesion <> p then
      return query select 'otra_persona'::text, null::uuid, null::uuid, null::boolean,
                          null::uuid, null::timestamptz;
      return;
    end if;

    select (now() at time zone c.zona_horaria)::date into hoy
      from nucleo.consorcio c where c.id = i.consorcio_id;
    select v2.id into vinculo from nucleo.vinculo v2
     where v2.consorcio_id = i.consorcio_id and v2.unidad_id = i.unidad_id
       and v2.persona_id = p and v2.tipo = i.tipo
       and daterange(v2.vigente_desde, v2.vigente_hasta) && daterange(hoy, null)
     limit 1;
    if vinculo is null then
      insert into nucleo.vinculo (consorcio_id, unidad_id, persona_id, tipo, vigente_desde)
      values (i.consorcio_id, i.unidad_id, p, i.tipo, hoy)
      returning id into vinculo;
    end if;
    update nucleo.invitacion set aceptada_en = now(), persona_id = p where id = i.id;

    insert into auditoria.eventos
      (consorcio_id, actor_persona_id, origen, accion, entidad, entidad_id, despues, ip,
       hash_previo, hash)
    values (i.consorcio_id, p, 'api', 'identidad.invitacion.aceptar', 'invitacion', i.id,
            jsonb_build_object('unidad_id', i.unidad_id, 'tipo', i.tipo,
                               'vinculo_id', vinculo, 'cuenta_nueva', nueva),
            ip_pedido, '', '');

    if nueva then
      insert into nucleo.sesion (persona_id, token_hash, dispositivo, agente, ip, vence_en)
      values (p, sesion_token, dispositivo_pedido, agente_pedido, ip_pedido, now() + vigencia)
      returning id, vence_en into s, v;
    end if;
    return query select 'ok'::text, p, i.consorcio_id, nueva, s, v;
  end
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
              'vigente_hasta', hasta_nuevo, 'causa', causa, 'decision_id', decision,
              'instrumento_referencia', btrim(instrumento), 'motivo', btrim(descripcion),
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
              'tipo', tipo_decision, 'fecha', fecha_decision,
              'referencia_acta', btrim(referencia), 'sha256_acta', lower(sha256))),
            '', '');
    return d;
  end
  $function$;

revoke select (id, consorcio_id, unidad_id, persona_id, tipo, vigente_desde, vigente_hasta,
  creado_en, fuente, origen, registrado_por, confirmado_por, confirmado_en)
  on nucleo.vinculo from cem_app;
grant select, insert, update, delete on nucleo.vinculo to cem_app;

alter table nucleo.vinculo
  drop constraint vinculo_no_confirma_quien_registro,
  drop constraint vinculo_no_confirma_el_propio,
  drop constraint vinculo_confirmacion_referencia,
  drop constraint vinculo_confirmacion_completa,
  drop constraint vinculo_registrado_segun_origen,
  drop constraint vinculo_origen_valido,
  drop column confirmacion_referencia,
  drop column confirmado_en,
  drop column confirmado_por,
  drop column registrado_por,
  drop column origen,
  drop column fuente;

drop type nucleo.fuente_vinculo;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
