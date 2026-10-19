"""Actor y origen atados a la sesión en `registrar_decision` y `cambiar_modo` (issue #66).

`nucleo.registrar_decision` (0011) y `nucleo.cambiar_modo` (0010) recibían `actor` y
`origen_pedido` sin compararlos con la sesión. Se les aplica el mismo control que `0012` puso en
`alta_mandato` y `baja_mandato` (hallazgo de Codex en app #64):

- `api`: `actor` no nulo e igual a `plataforma.persona_actual()` (`actor_no_coincide`);
- `soporte`: sin persona en la sesión y sin `actor` (`origen_invalido`);
- cualquier otro origen (`trabajos`, `planificador`) se rechaza (`origen_invalido`).

En `registrar_decision` los `check` de `decision_formal` ya limitaban el origen a `api` y
`soporte`; el agujero era `api` con otro actor y `soporte` con sesión. En `cambiar_modo`, que no
guarda el origen en la tabla, valía además cualquier origen de la auditoría.

No cambia ninguna semántica de permisos: ninguna ruta llama a estas funciones y el único
llamador, `scripts.alta_mandato`, usa `soporte` sin actor. `create or replace` conserva los
permisos (`cem_app` ejecuta, `PUBLIC` no). El downgrade restaura las definiciones anteriores,
tomadas del catálogo de una base en `0012`, sin tocar datos.

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
    -- #66: actor y origen atados como en `alta_mandato` y `baja_mandato` (0012).
    if origen_pedido is null or origen_pedido not in ('api', 'soporte') then
      raise exception 'origen no admitido: %', origen_pedido
        using errcode = 'P0001', hint = 'origen_invalido';
    end if;
    if origen_pedido = 'api'
       and (actor is null or actor is distinct from plataforma.persona_actual()) then
      raise exception 'el actor no es la persona de la sesión'
        using errcode = 'P0001', hint = 'actor_no_coincide';
    end if;
    if origen_pedido = 'soporte'
       and (plataforma.persona_actual() is not null or actor is not null) then
      raise exception 'soporte actúa sin sesión ni actor'
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
    -- #66: actor y origen atados como en `alta_mandato` y `baja_mandato` (0012).
    if origen_pedido is null or origen_pedido not in ('api', 'soporte') then
      raise exception 'origen no admitido: %', origen_pedido
        using errcode = 'P0001', hint = 'origen_invalido';
    end if;
    if origen_pedido = 'api'
       and (actor is null or actor is distinct from plataforma.persona_actual()) then
      raise exception 'el actor no es la persona de la sesión'
        using errcode = 'P0001', hint = 'actor_no_coincide';
    end if;
    if origen_pedido = 'soporte'
       and (plataforma.persona_actual() is not null or actor is not null) then
      raise exception 'soporte actúa sin sesión ni actor'
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
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
