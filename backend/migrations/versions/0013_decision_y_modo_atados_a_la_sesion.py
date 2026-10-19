"""Actor y origen atados a la sesión en `registrar_decision`, `cambiar_modo` y `baja_mandato` (#66).

`nucleo.registrar_decision` (0011) y `nucleo.cambiar_modo` (0010) recibían `actor` y
`origen_pedido` sin compararlos con la sesión. Se les aplica el control que `0012` puso en
`alta_mandato` y `baja_mandato` (hallazgo de Codex en app #64), con sus mismos mensajes:

- `api`: `actor` no nulo (`actor_requerido`) e igual a `plataforma.persona_actual()`
  (`actor_no_coincide`);
- `soporte`: sin persona en la sesión y sin `actor` (`origen_invalido`);
- cualquier otro origen (`trabajos`, `planificador`, nulo) se rechaza (`origen_invalido`).

En `registrar_decision` los `check` de `decision_formal` ya limitaban el origen a `api` y
`soporte`; el agujero era `api` con otro actor y `soporte` con sesión. En `cambiar_modo`, que no
guarda el origen en la tabla, valía además cualquier origen de la auditoría, y `api` sin sesión
ni actor.

`baja_mandato` (0012) tenía el mismo hueco en un caso: con `soporte` aceptaba un `actor` y lo
grababa en `finalizado_por` y en la auditoría. Ahora `soporte` no lleva actor, como el resto.
`alta_mandato` no lo necesita: lo impide `check ((origen = 'api') = (registrado_por is not null))`.

No cambia ninguna semántica de permisos: ninguna ruta llama a estas funciones y el único
llamador, `scripts.alta_mandato`, usa `soporte` sin actor. `create or replace` conserva dueño y
permisos (`cem_app` ejecuta, `PUBLIC` no). El downgrade restaura las definiciones anteriores
(las dos primeras del catálogo de una base en `0012`, la tercera del texto de `0012`), sin tocar
datos.

Diseño: `doc/diseno/H2b_4_Diseno.md` del repositorio CEM, §9.1 (PR 0).

Revisión: 0013
Anterior: 0012
"""

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

UPGRADE = r"""
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

CREATE OR REPLACE FUNCTION nucleo.cambiar_modo(
  modo_nuevo nucleo.tipo_modo,
  decision uuid,
  desde date,
  actor uuid,
  origen_pedido text)
 RETURNS uuid
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog', 'pg_temp'
AS $function$
  declare
    c uuid := plataforma.consorcio_actual();
    tipo_respaldo nucleo.tipo_decision;
    anterior nucleo.modo_operacion;
    nuevo uuid;
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
    -- Serializa los cambios de modo del consorcio.
    perform 1 from nucleo.consorcio x where x.id = c for update;

    select d.tipo into tipo_respaldo
      from nucleo.decision_formal d where d.consorcio_id = c and d.id = decision;
    -- RN26: solo una decisión de asamblea formalizada y registrada de este consorcio.
    if tipo_respaldo is null or tipo_respaldo <> 'asamblea' then
      raise exception 'el modo solo cambia con una decisión de asamblea registrada'
        using errcode = 'P0001', hint = 'decision_invalida';
    end if;

    select * into anterior from nucleo.modo_operacion m
     where m.consorcio_id = c and m.vigente_hasta is null;
    if anterior.id is not null then
      if anterior.modo = modo_nuevo then
        raise exception 'el consorcio ya está en ese modo'
          using errcode = 'P0001', hint = 'mismo_modo';
      end if;
      if desde <= anterior.vigente_desde then
        raise exception 'el modo nuevo tiene que empezar después del vigente'
          using errcode = 'P0001', hint = 'fecha_invalida';
      end if;
      update nucleo.modo_operacion set vigente_hasta = desde where id = anterior.id;
    end if;

    insert into nucleo.modo_operacion (consorcio_id, modo, vigente_desde, decision_id)
    values (c, modo_nuevo, desde, decision)
    returning id into nuevo;

    insert into auditoria.eventos
      (consorcio_id, actor_persona_id, origen, accion, entidad, entidad_id, antes, despues,
       hash_previo, hash)
    values (c, actor, origen_pedido, 'consorcio.modo.cambiar', 'modo_operacion', nuevo,
            case when anterior.id is null then null
                 else jsonb_build_object('modo', anterior.modo, 'id', anterior.id) end,
            jsonb_build_object('modo', modo_nuevo, 'desde', desde, 'decision_id', decision),
            '', '');
    return nuevo;
  end
  $function$;

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
"""

DOWNGRADE = r"""
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

CREATE OR REPLACE FUNCTION nucleo.cambiar_modo(
  modo_nuevo nucleo.tipo_modo,
  decision uuid,
  desde date,
  actor uuid,
  origen_pedido text)
 RETURNS uuid
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog', 'pg_temp'
AS $function$
  declare
    c uuid := plataforma.consorcio_actual();
    tipo_respaldo nucleo.tipo_decision;
    anterior nucleo.modo_operacion;
    nuevo uuid;
  begin
    if c is null then
      raise exception 'sin consorcio en el ámbito' using errcode = '42501';
    end if;
    -- Serializa los cambios de modo del consorcio.
    perform 1 from nucleo.consorcio x where x.id = c for update;

    select d.tipo into tipo_respaldo
      from nucleo.decision_formal d where d.consorcio_id = c and d.id = decision;
    -- RN26: solo una decisión de asamblea formalizada y registrada de este consorcio.
    if tipo_respaldo is null or tipo_respaldo <> 'asamblea' then
      raise exception 'el modo solo cambia con una decisión de asamblea registrada'
        using errcode = 'P0001', hint = 'decision_invalida';
    end if;

    select * into anterior from nucleo.modo_operacion m
     where m.consorcio_id = c and m.vigente_hasta is null;
    if anterior.id is not null then
      if anterior.modo = modo_nuevo then
        raise exception 'el consorcio ya está en ese modo'
          using errcode = 'P0001', hint = 'mismo_modo';
      end if;
      if desde <= anterior.vigente_desde then
        raise exception 'el modo nuevo tiene que empezar después del vigente'
          using errcode = 'P0001', hint = 'fecha_invalida';
      end if;
      update nucleo.modo_operacion set vigente_hasta = desde where id = anterior.id;
    end if;

    insert into nucleo.modo_operacion (consorcio_id, modo, vigente_desde, decision_id)
    values (c, modo_nuevo, desde, decision)
    returning id into nuevo;

    insert into auditoria.eventos
      (consorcio_id, actor_persona_id, origen, accion, entidad, entidad_id, antes, despues,
       hash_previo, hash)
    values (c, actor, origen_pedido, 'consorcio.modo.cambiar', 'modo_operacion', nuevo,
            case when anterior.id is null then null
                 else jsonb_build_object('modo', anterior.modo, 'id', anterior.id) end,
            jsonb_build_object('modo', modo_nuevo, 'desde', desde, 'decision_id', decision),
            '', '');
    return nuevo;
  end
  $function$;

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


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
