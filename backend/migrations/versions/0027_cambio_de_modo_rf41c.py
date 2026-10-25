"""2b-6b: el cambio de modo, solo por soporte y solo por el diagrama 12 (RF41-C, cierra E29).

Diseño: decisiones del propietario del 24-10-2026 sobre 2b-6 (repositorio CEM,
`doc/diseno/H2b_6_Diseno.md`, §5). Sin ruta pública de escritura: el modo lo registra el equipo
con una decisión de asamblea (C7). `nucleo.cambiar_modo` se reescribe entera:

- **Solo soporte** (C7, que la base no reflejaba: E29). Ningún pedido `api`, ni con persona en la
  sesión ni con actor.
- **Una decisión, un cambio** (E29): una decisión de asamblea del consorcio que no haya respaldado
  otro modo (`decision_ya_usada`), y el modo no empieza antes de la fecha de la decisión.
- **Sin cambio retroactivo** (E29): un cambio entre modos rige desde hoy, en la fecha del
  consorcio. Tampoco anticipado: un modo futuro dejaría mandatos y permisos en un estado que
  nadie verificó al registrarlo (estado ambiguo, que se niega).
- **Solo las transiciones del diagrama 12** (documento 08 §5): Consejo → AA, Consejo → AG,
  AA ⇄ AG y AA → Consejo. No AG → Consejo (`transicion_no_admitida`).
- **Mandatos al salir de un modo.** Cada transición del diagrama reemplaza a quien administra
  (sus rótulos: «el administrador acepta operar», «designa administrador propietario», «no
  renueva o remueve el mandato», «contrata un administrador profesional», «deja de operar»).
  Si al salir queda vigente el mandato de administración del modo que se deja (`administrador`
  en AA y AG, `administrador_externo` en Consejo), se niega
  (`transicion_requiere_fin_de_mandato`). Nunca se infiere ni se ejecuta una baja.
  - Una baja que la misma decisión diga de forma expresa y verificable revoca accesos.
  - Por la regla 4 del documento 08 §5, esa salida necesita el paquete de transferencia (RF52),
    que no existe: queda bloqueada sin excepciones.
- **Historia completa:** solo se cierra el período vigente; nada se borra. Un historial sin
  período abierto es un estado ambiguo y se niega (`modo_ambiguo`).
- **Frenado y presentado al propietario:** el primer modo de un consorcio sigue como antes
  (cualquiera de los tres, con cualquier fecha desde la decisión). El diagrama solo dibuja la
  entrada a Consejo, pero el alta de consorcios y el smoke de staging registran AA. La decisión 11
  (Autogestión real) sigue siendo del propietario: no se activa nada.

`nucleo.modos_del_consorcio()` es la segunda barrera de `GET …/modos`: exige `decisiones.ver`
vigente, como las demás lecturas de gobierno (0018), y devuelve solo modo, vigencia y decisión.

Downgrade: restaura la definición de 0013 y quita la lectura. No hay datos nuevos que proteger.

Revisión: 0027
Anterior: 0026
"""

from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None

CAMBIAR_MODO = """
create or replace function nucleo.cambiar_modo(
  modo_nuevo nucleo.tipo_modo, decision uuid, desde date, actor uuid, origen_pedido text)
  returns uuid
  language plpgsql
  security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    dec nucleo.decision_formal;
    anterior nucleo.modo_operacion;
    hoy date;
    nuevo uuid;
  begin
    if c is null then
      raise exception 'sin consorcio en el ámbito' using errcode = '42501';
    end if;
    -- #66 y C7 (E29, 0027): el modo lo registra solo el equipo, sin sesión ni actor.
    if origen_pedido is distinct from 'soporte' then
      raise exception 'el modo lo registra solo el equipo, con origen soporte'
        using errcode = 'P0001', hint = 'origen_invalido';
    end if;
    if plataforma.persona_actual() is not null then
      raise exception 'el origen soporte no admite una persona en la sesión'
        using errcode = 'P0001', hint = 'origen_invalido';
    end if;
    if actor is not null then
      raise exception 'el origen soporte no lleva actor: lo registra el equipo'
        using errcode = 'P0001', hint = 'origen_invalido';
    end if;
    -- Serializa los cambios de modo del consorcio.
    perform 1 from nucleo.consorcio x where x.id = c for update;

    select * into dec from nucleo.decision_formal d where d.consorcio_id = c and d.id = decision;
    -- RN26: solo una decisión de asamblea registrada de este consorcio.
    if dec.id is null or dec.tipo <> 'asamblea' then
      raise exception 'el modo solo cambia con una decisión de asamblea registrada'
        using errcode = 'P0001', hint = 'decision_invalida';
    end if;
    -- E29 (0027): una decisión respalda un solo modo.
    if exists (select 1 from nucleo.modo_operacion m
                where m.consorcio_id = c and m.decision_id = decision) then
      raise exception 'esa decisión ya respaldó un modo de este consorcio'
        using errcode = 'P0001', hint = 'decision_ya_usada';
    end if;
    if desde is null or desde < dec.fecha then
      raise exception 'el modo no puede empezar antes de la decisión que lo respalda'
        using errcode = 'P0001', hint = 'fecha_invalida';
    end if;

    select * into anterior from nucleo.modo_operacion m
     where m.consorcio_id = c and m.vigente_hasta is null;
    if anterior.id is null
       and exists (select 1 from nucleo.modo_operacion m where m.consorcio_id = c) then
      raise exception 'el historial de modos no tiene un período abierto'
        using errcode = 'P0001', hint = 'modo_ambiguo';
    end if;

    if anterior.id is not null then
      if anterior.modo = modo_nuevo then
        raise exception 'el consorcio ya está en ese modo'
          using errcode = 'P0001', hint = 'mismo_modo';
      end if;
      -- E29 (0027): ni retroactivo ni anticipado; rige desde hoy, en la fecha del consorcio.
      hoy := nucleo.hoy_del_consorcio(c);
      if desde <> hoy or anterior.vigente_desde >= hoy then
        raise exception 'un cambio de modo rige desde hoy y después del modo vigente'
          using errcode = 'P0001', hint = 'fecha_invalida';
      end if;
      -- Diagrama 12 (documento 08 §5): no hay AG → Consejo.
      if (anterior.modo::text, modo_nuevo::text) not in (
           ('consejo', 'administrador_aumentado'), ('consejo', 'autogestion'),
           ('administrador_aumentado', 'autogestion'),
           ('autogestion', 'administrador_aumentado'),
           ('administrador_aumentado', 'consejo')) then
        raise exception 'esa transición no está en el diagrama 12'
          using errcode = 'P0001', hint = 'transicion_no_admitida';
      end if;
      -- Quien administra en el modo que se deja tiene que haber terminado: la baja no se
      -- infiere, y una baja con la transición necesita el paquete de transferencia (RF52).
      if exists (select 1 from nucleo.mandato m
                  where m.consorcio_id = c
                    and m.tipo = case anterior.modo
                                   when 'consejo' then 'administrador_externo'::nucleo.tipo_mandato
                                   else 'administrador'::nucleo.tipo_mandato end
                    and m.causa_baja is distinct from 'error_carga'
                    and (m.vigente_hasta is null or m.vigente_hasta > desde)) then
        raise exception 'la transición necesita que termine el mandato de administración'
          using errcode = 'P0001', hint = 'transicion_requiere_fin_de_mandato';
      end if;
      update nucleo.modo_operacion set vigente_hasta = desde where id = anterior.id;
    end if;

    insert into nucleo.modo_operacion (consorcio_id, modo, vigente_desde, decision_id)
    values (c, modo_nuevo, desde, decision)
    returning id into nuevo;

    insert into auditoria.eventos
      (consorcio_id, actor_persona_id, origen, accion, entidad, entidad_id, antes, despues,
       hash_previo, hash)
    values (c, null, origen_pedido, 'consorcio.modo.cambiar', 'modo_operacion', nuevo,
            case when anterior.id is null then null
                 else jsonb_build_object('modo', anterior.modo, 'id', anterior.id) end,
            jsonb_build_object('modo', modo_nuevo, 'desde', desde, 'decision_id', decision),
            '', '');
    return nuevo;
  end
  $$;
"""

MODOS_DEL_CONSORCIO = """
create function nucleo.modos_del_consorcio()
  returns table (modo nucleo.tipo_modo, vigente_desde date, vigente_hasta date, decision_id uuid)
  language plpgsql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    quien uuid := plataforma.persona_actual();
  begin
    if c is null or quien is null then
      raise exception 'sin ámbito para leer los modos' using errcode = '42501';
    end if;
    if not nucleo.tiene_facultad_vigente(c, quien, 'decisiones.ver',
                                         nucleo.hoy_del_consorcio(c)) then
      raise exception 'sin decisiones.ver en este consorcio' using errcode = '42501';
    end if;
    return query
      select m.modo, m.vigente_desde, m.vigente_hasta, m.decision_id
        from nucleo.modo_operacion m
       where m.consorcio_id = c
       order by m.vigente_desde desc;
  end
  $$;
revoke all on function nucleo.modos_del_consorcio() from public;
grant execute on function nucleo.modos_del_consorcio() to cem_app;
"""

# La definición de 0013, tal como estaba vigente (`pg_get_functiondef` sobre 0026).
CAMBIAR_MODO_0013 = """
create or replace function nucleo.cambiar_modo(
  modo_nuevo nucleo.tipo_modo, decision uuid, desde date, actor uuid, origen_pedido text)
  returns uuid
  language plpgsql
  security definer
  set search_path = pg_catalog, pg_temp
  as $$
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
  $$;
"""


def upgrade() -> None:
    op.execute(CAMBIAR_MODO)
    op.execute(MODOS_DEL_CONSORCIO)


def downgrade() -> None:
    op.execute("drop function nucleo.modos_del_consorcio();")
    op.execute(CAMBIAR_MODO_0013)
