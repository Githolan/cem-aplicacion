"""Importación de unidades y relaciones con informe (RF02; H2b 2b-5, corte 3).

Diseño: `doc/diseno/H2b_5_Diseno.md` del repositorio CEM, §4, con las decisiones del
propietario del 22-10-2026 (`doc/diseno/Decisiones_Propietario_2026-10-21.md` §2.4, «La
invitación nombra su vínculo») y las de D1 a D4 de `doc/rf02_corte3_informe.md` de este
repositorio.

- `nucleo.importacion_padron`: una fila por importación aplicada, con el SHA-256 del archivo,
  quién la aplicó y cuándo. No guarda el archivo: trae correos y nombres. RLS habilitada y
  forzada por consorcio; `cem_app` solo la lee y la escribe `nucleo.registrar_importacion_padron`.
- `nucleo.vinculo.importacion_id`: la importación que creó el vínculo (trazabilidad archivo →
  importación → vínculo). Solo un vínculo con `fuente = 'importacion'` la tiene.
- `nucleo.invitacion.vinculo_id`, opcional: la invitación que creó el importador nombra su
  vínculo. `aceptar_invitacion` toma **exactamente** ese vínculo y, si ya no corresponde
  (anulado, de otra persona, de otra unidad o de otro carácter), responde
  `vinculo_no_corresponde` sin crear nada. Sin `vinculo_id`, todo sigue como en 0014. No hay
  clave foránea: la anulación borra el vínculo y la invitación tiene que poder decir por qué ya
  no sirve.
- `nucleo.importar_vinculo`: busca o crea la persona por su correo normalizado (con el mismo
  bloqueo `persona:<correo>` de `aceptar_invitacion`) y crea el vínculo con fuente
  `importacion`, origen `api`, sin confirmar y con quién lo cargó. Un solape responde
  `vinculo_solapado`: nunca se pisa un vínculo existente (PA01).
- `nucleo.vinculo.nombre_declarado`: el nombre de la planilla. Mientras el vínculo está
  importado pendiente, `padron_del_consorcio` muestra ese nombre y no el de la persona: si el
  correo ya tenía cuenta (en otro consorcio), el padrón no lo delata ni muestra su nombre real
  (revisión independiente del corte 3, P1). `cem_app` no lee la columna, como `motivo_fin`.
- **Orden de los bloqueos**, el mismo en todo el código: invitación, persona (`persona:<correo>`),
  vínculo y, al final, la cadena de auditoría (`auditoria:<consorcio>`). La importación toma los
  de persona de toda la planilla antes de escribir el primer evento, y la anulación bloquea las
  invitaciones antes que el vínculo (Codex en app #105 y P3 4 de la revisión sobre 69f1f77).
- `nucleo.anular_vinculo`: borra un vínculo que todavía no empezó (`vigente_desde > hoy`) y deja
  un evento con los identificadores, sin el motivo (E31). Por la API, solo sin confirmar; uno
  confirmado lo anula el equipo por soporte con la referencia del respaldo, que queda en el
  evento. Un vínculo que ya empezó no se anula: se finaliza (0020). Las invitaciones pendientes
  que lo nombran se retiran en la misma transacción, auditadas: si no, ocuparían
  `invitacion_pendiente_unica` y bloquearían volver a cargarlo corregido (revisión, P2).
- `nucleo.vinculos_de_correos`: los vínculos del consorcio de esos correos, con si siguen
  importados pendientes, para el informe; exige un mandato vigente con `padron.gestionar`, como
  `padron_del_consorcio` exige `padron.ver`.
- Renovación (decisión del propietario del 23-10-2026): reimportar la misma fila de un vínculo
  importado pendiente cuya invitación venció la renueva. Es la única escritura de una fila igual:
  `crear_invitacion_importada` retira la vencida y crea otra que nombra el mismo vínculo.

Downgrade: se niega (`downgrade_con_datos`) si hay alguna importación o alguna invitación con
vínculo; si no, restaura `aceptar_invitacion` y `padron_del_consorcio` de 0014 y quita todo lo
demás.

Revisión: 0025
Anterior: 0024
"""

import importlib.util
from pathlib import Path

from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None

ACEPTAR = "nucleo.aceptar_invitacion(bytea, uuid, text, bytea, uuid, text, inet, interval)"
REGISTRAR = "nucleo.registrar_importacion_padron(bytea, uuid)"
IMPORTAR = "nucleo.importar_vinculo(uuid, uuid, text, text, nucleo.tipo_vinculo, date, date, uuid)"
ANULAR = "nucleo.anular_vinculo(uuid, text, text, uuid, text)"
INVITAR = "nucleo.crear_invitacion_importada(uuid, text, bytea, interval, uuid)"
DE_CORREOS = "nucleo.vinculos_de_correos(text[])"
COLUMNAS_INVITACION_0007 = (
    "id, consorcio_id, unidad_id, tipo, correo, nombre, token_hash, invitada_por, creada_en,"
    " vence_en, aceptada_en, persona_id, revocada_en"
)


def _de_0014(inicio: str) -> str:
    """Una función tal como la dejó 0014, en `create or replace`: la base del cambio y lo que
    restaura el downgrade, sin copiarla a mano (como `_funcion_de` en 0023)."""
    ruta = Path(__file__).with_name("0014_vinculo_procedencia_confirmacion_y_padron.py")
    spec = importlib.util.spec_from_file_location(ruta.stem, ruta)
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    texto = modulo.UPGRADE
    desde = texto.index(inicio)
    fin = texto.index("\n  $$;\n", desde) + len("\n  $$;\n")
    return texto[desde:fin].replace("create function", "create or replace function", 1)


def _aceptar_de_0014() -> str:
    return _de_0014("create or replace function nucleo.aceptar_invitacion(")


def _padron_de_0014() -> str:
    return _de_0014("create function nucleo.padron_del_consorcio(")


NO_CORRESPONDE = """\
        return query select 'vinculo_no_corresponde'::text, null::uuid, null::uuid,
                            null::boolean, null::uuid, null::timestamptz;
        return;
"""

PERSONA_0014 = """\
     where x.correo = i.correo and x.eliminado_en is null;
    if p is null then
"""
PERSONA_0025 = """\
     where x.correo = i.correo and x.eliminado_en is null;
    -- 0025: la invitación que nombra su vínculo no crea a nadie. Si su persona ya no existe,
    -- el vínculo no le corresponde a quien acepta.
    if i.vinculo_id is not null and p is null then
      return query select 'vinculo_no_corresponde'::text, null::uuid, null::uuid,
                          null::boolean, null::uuid, null::timestamptz;
      return;
    end if;
    if p is null then
"""

VINCULO_0014 = """\
    select v2.id into vinculo from nucleo.vinculo v2
     where v2.consorcio_id = i.consorcio_id and v2.unidad_id = i.unidad_id
       and v2.persona_id = p and v2.tipo = i.tipo
       and daterange(v2.vigente_desde, v2.vigente_hasta) && daterange(hoy, null)
     limit 1;
"""
VINCULO_0025 = f"""\
    if i.vinculo_id is not null then
      -- 0025: exactamente el vínculo que nombra (decisión del propietario, 22-10-2026). Si fue
      -- anulado, ya terminó o es de otra persona, unidad o carácter, la invitación quedó
      -- obsoleta: no se acepta, no se reutiliza otro ni se crea uno (decisión del 23-10-2026).
      select v2.id into vinculo from nucleo.vinculo v2
       where v2.id = i.vinculo_id and v2.consorcio_id = i.consorcio_id
         and v2.unidad_id = i.unidad_id and v2.persona_id = p and v2.tipo = i.tipo
         and (v2.vigente_hasta is null or v2.vigente_hasta > hoy)
       for update;
      if vinculo is null then
{NO_CORRESPONDE}      end if;
    else
      select v2.id into vinculo from nucleo.vinculo v2
       where v2.consorcio_id = i.consorcio_id and v2.unidad_id = i.unidad_id
         and v2.persona_id = p and v2.tipo = i.tipo
         and daterange(v2.vigente_desde, v2.vigente_hasta) && daterange(hoy, null)
       limit 1;
    end if;
"""


NOMBRE_0014 = """\
      select v.id, v.unidad_id, u.designacion, pe.nombre, v.tipo, v.vigente_desde,
"""
NOMBRE_0025 = """\
      -- 0025: el importado pendiente se muestra con el nombre de la planilla, no con el de la
      -- persona (que puede tener cuenta en otro consorcio). El mismo criterio que
      -- `vinculos_importados_pendientes` (0024): sin confirmar y sin una aceptación que lo nombre.
      select v.id, v.unidad_id, u.designacion,
             case when v.nombre_declarado is not null and v.confirmado_en is null
                       and not exists (
                         select 1 from auditoria.eventos e
                           join nucleo.invitacion i
                             on i.id = e.entidad_id and i.consorcio_id = e.consorcio_id
                          where e.consorcio_id = v.consorcio_id
                            and e.accion = 'identidad.invitacion.aceptar'
                            and e.entidad = 'invitacion'
                            and e.despues ->> 'vinculo_id' = v.id::text
                            and i.aceptada_en is not null)
                  then v.nombre_declarado else pe.nombre end,
             v.tipo, v.vigente_desde,
"""
ORDEN_0014 = """\
       order by u.designacion, v.tipo, pe.nombre, v.id;
"""
ORDEN_0025 = """\
       order by 3, 5, 4, 1;
"""


def _padron_de_0025() -> str:
    texto = _padron_de_0014()
    for antes, despues in ((NOMBRE_0014, NOMBRE_0025), (ORDEN_0014, ORDEN_0025)):
        assert texto.count(antes) == 1, antes
        texto = texto.replace(antes, despues)
    return texto


def _aceptar_de_0025() -> str:
    texto = _aceptar_de_0014()
    for antes, despues in ((PERSONA_0014, PERSONA_0025), (VINCULO_0014, VINCULO_0025)):
        assert texto.count(antes) == 1, antes
        texto = texto.replace(antes, despues)
    return texto


UPGRADE = f"""
create table nucleo.importacion_padron (
  id               uuid primary key default plataforma.uuid_v7(),
  consorcio_id     uuid not null references nucleo.consorcio (id),
  archivo_sha256   bytea not null check (length(archivo_sha256) = 32),
  registrada_por   uuid not null references nucleo.persona (id),
  creada_en        timestamptz not null default now(),
  unique (consorcio_id, id)
);
create index importacion_padron_por_archivo on nucleo.importacion_padron
  (consorcio_id, archivo_sha256);
alter table nucleo.importacion_padron enable row level security;
alter table nucleo.importacion_padron force row level security;
create policy por_consorcio on nucleo.importacion_padron for select
  using (consorcio_id = plataforma.consorcio_actual());
grant select on nucleo.importacion_padron to cem_app;

alter table nucleo.vinculo
  add column importacion_id uuid,
  add constraint vinculo_importacion_del_consorcio foreign key (consorcio_id, importacion_id)
    references nucleo.importacion_padron (consorcio_id, id),
  add column nombre_declarado text,
  add constraint vinculo_importacion_solo_importado
    check (importacion_id is null or fuente = 'importacion'),
  -- El nombre de la planilla va con la importación, y solo con ella.
  add constraint vinculo_nombre_declarado
    check ((importacion_id is null) = (nombre_declarado is null)
           and (nombre_declarado is null
                or length(btrim(nombre_declarado)) between 1 and 100));
create index vinculo_por_importacion on nucleo.vinculo (importacion_id)
  where importacion_id is not null;
grant select (importacion_id) on nucleo.vinculo to cem_app;

alter table nucleo.invitacion add column vinculo_id uuid;
-- Solo el importador completa `vinculo_id`, y lo hace por `crear_invitacion_importada`:
-- `cem_app` sigue insertando invitaciones manuales, pero sin esa columna.
revoke insert on nucleo.invitacion from cem_app;
grant insert ({COLUMNAS_INVITACION_0007}) on nucleo.invitacion to cem_app;

{_aceptar_de_0025()}
{_padron_de_0025()}
-- La importación aplicada: quién y el hash del archivo. Las filas las escribe la aplicación con
-- `importar_vinculo`, en la misma transacción.
create function nucleo.registrar_importacion_padron(archivo bytea, actor uuid)
  returns uuid
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    nueva uuid;
  begin
    if c is null then
      raise exception 'sin consorcio en el ámbito' using errcode = '42501';
    end if;
    if actor is null or actor is distinct from plataforma.persona_actual() then
      raise exception 'el actor no es la persona de la sesión'
        using errcode = 'P0001', hint = 'actor_no_coincide';
    end if;
    insert into nucleo.importacion_padron (consorcio_id, archivo_sha256, registrada_por)
    values (c, archivo, actor)
    returning id into nueva;
    insert into auditoria.eventos
      (consorcio_id, actor_persona_id, origen, accion, entidad, entidad_id, despues,
       hash_previo, hash)
    values (c, actor, 'api', 'consorcio.padron.importar', 'importacion_padron', nueva,
            jsonb_build_object('archivo_sha256', encode(archivo, 'hex')), '', '');
    return nueva;
  end
  $$;

-- Un vínculo importado. La facultad (`padron.gestionar` sobre su unidad) la decide la
-- aplicación; la base garantiza ámbito, actor, importación, unidad, datos y que no se pise nada.
create function nucleo.importar_vinculo(
  importacion uuid, unidad uuid, correo_fila text, nombre_fila text,
  tipo_fila nucleo.tipo_vinculo, desde date, hasta date, actor uuid)
  returns uuid
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    correo_normal text := lower(btrim(correo_fila));
    p uuid;
    nuevo uuid;
  begin
    if c is null then
      raise exception 'sin consorcio en el ámbito' using errcode = '42501';
    end if;
    if actor is null or actor is distinct from plataforma.persona_actual() then
      raise exception 'el actor no es la persona de la sesión'
        using errcode = 'P0001', hint = 'actor_no_coincide';
    end if;
    if not exists (select 1 from nucleo.importacion_padron x
                    where x.consorcio_id = c and x.id = importacion
                      and x.registrada_por = actor) then
      raise exception 'la importación no es de este consorcio ni de este actor'
        using errcode = 'P0001', hint = 'importacion_invalida';
    end if;
    if not exists (select 1 from nucleo.unidad u
                    where u.consorcio_id = c and u.id = unidad and u.eliminado_en is null) then
      raise exception 'la unidad no existe en este consorcio'
        using errcode = 'P0001', hint = 'unidad_invalida';
    end if;
    -- El formato de `cem_contracts.identidad.FORMATO_CORREO` y el tope de la invitación.
    if correo_normal is null or correo_normal !~ '^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$'
       or length(correo_normal) > 254 then
      raise exception 'el vínculo importado necesita el correo de la persona'
        using errcode = 'P0001', hint = 'correo_invalido';
    end if;
    if nombre_fila is null or length(btrim(nombre_fila)) not between 1 and 100 then
      raise exception 'el vínculo importado necesita el nombre de la persona'
        using errcode = 'P0001', hint = 'nombre_invalido';
    end if;
    if desde is null or (hasta is not null and hasta <= desde) then
      raise exception 'la vigencia del vínculo no es válida'
        using errcode = 'P0001', hint = 'vigencia_invalida';
    end if;

    -- El mismo bloqueo que `aceptar_invitacion`: dos altas del mismo correo nuevo a la vez no
    -- crean dos personas.
    perform pg_advisory_xact_lock(hashtextextended('persona:' || correo_normal, 0));
    select x.id into p from nucleo.persona x
     where x.correo = correo_normal and x.eliminado_en is null;
    if p is null then
      insert into nucleo.persona (correo, nombre) values (correo_normal, btrim(nombre_fila))
      returning id into p;
    end if;

    begin
      insert into nucleo.vinculo (consorcio_id, unidad_id, persona_id, tipo, vigente_desde,
                                  vigente_hasta, fuente, origen, registrado_por,
                                  importacion_id, nombre_declarado)
      values (c, unidad, p, tipo_fila, desde, hasta, 'importacion', 'api', actor, importacion,
              btrim(nombre_fila))
      returning id into nuevo;
    exception when exclusion_violation then
      raise exception 'el vínculo se superpone con otro de la misma persona, unidad y carácter'
        using errcode = 'P0001', hint = 'vinculo_solapado';
    end;

    -- Sin correo ni nombre (E31), y sin decir si la persona ya existía.
    insert into auditoria.eventos
      (consorcio_id, actor_persona_id, origen, accion, entidad, entidad_id, despues,
       hash_previo, hash)
    values (c, actor, 'api', 'consorcio.vinculo.importar', 'vinculo', nuevo,
            jsonb_build_object('unidad_id', unidad, 'tipo', tipo_fila, 'vigente_desde', desde,
                               'vigente_hasta', hasta, 'importacion_id', importacion),
            '', '');
    return nuevo;
  end
  $$;

-- La corrección de una carga errónea que el fin (0020) deja afuera: un vínculo que nunca rigió.
create function nucleo.anular_vinculo(
  vinculo_a_anular uuid, motivo text, referencia text, actor uuid, origen_pedido text)
  returns void
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    v nucleo.vinculo;
    retirada uuid;
  begin
    if c is null then
      raise exception 'sin consorcio en el ámbito' using errcode = '42501';
    end if;
    -- Origen y actor como `finalizar_vinculo` (0020).
    if origen_pedido is null or origen_pedido not in ('api', 'soporte') then
      raise exception 'origen no admitido: solo api o soporte'
        using errcode = 'P0001', hint = 'origen_invalido';
    end if;
    if origen_pedido = 'api' and actor is null then
      raise exception 'una anulación por la API necesita quién la registra'
        using errcode = 'P0001', hint = 'actor_requerido';
    end if;
    if origen_pedido = 'api' and actor is distinct from plataforma.persona_actual() then
      raise exception 'el actor no es la persona de la sesión'
        using errcode = 'P0001', hint = 'actor_no_coincide';
    end if;
    if origen_pedido = 'soporte' and (plataforma.persona_actual() is not null
                                      or actor is not null) then
      raise exception 'el origen soporte no admite persona en la sesión ni actor'
        using errcode = 'P0001', hint = 'origen_invalido';
    end if;
    if motivo is null or length(btrim(motivo)) not between 1 and 300 then
      raise exception 'la anulación de un vínculo necesita su motivo'
        using errcode = 'P0001', hint = 'motivo_requerido';
    end if;
    -- La referencia del respaldo es del equipo (soporte); por la API no se pide.
    if origen_pedido = 'soporte'
       and (referencia is null or length(btrim(referencia)) not between 1 and 300) then
      raise exception 'el equipo anula con la referencia del respaldo'
        using errcode = 'P0001', hint = 'referencia_requerida';
    end if;
    if origen_pedido = 'api' and referencia is not null then
      raise exception 'la referencia del respaldo es solo del equipo'
        using errcode = 'P0001', hint = 'origen_invalido';
    end if;
    -- Orden de bloqueos: primero las invitaciones que lo nombran y después el vínculo, como
    -- `aceptar_invitacion` (invitación, persona, vínculo, auditoría). Al revés, una anulación y
    -- una aceptación del mismo vínculo podían trabarse entre sí (P3 4 de la revisión sobre
    -- 69f1f77).
    perform 1 from nucleo.invitacion i
     where i.consorcio_id = c and i.vinculo_id = vinculo_a_anular
       and i.aceptada_en is null and i.revocada_en is null
     order by i.id
       for update;
    select * into v from nucleo.vinculo x
     where x.consorcio_id = c and x.id = vinculo_a_anular for update;
    if v.id is null then
      raise exception 'el vínculo no existe en este consorcio'
        using errcode = 'P0001', hint = 'vinculo_invalido';
    end if;
    if v.vigente_desde <= nucleo.hoy_del_consorcio(c) then
      raise exception 'el vínculo ya empezó: no se anula, se finaliza'
        using errcode = 'P0001', hint = 'vinculo_iniciado';
    end if;
    if origen_pedido = 'api' and v.confirmado_en is not null then
      raise exception 'un vínculo confirmado lo anula el equipo, con el respaldo'
        using errcode = 'P0001', hint = 'vinculo_confirmado';
    end if;

    delete from nucleo.vinculo where id = v.id;

    -- Su invitación pendiente ya no sirve: se retira, auditada, para no bloquear la recarga.
    for retirada in
      update nucleo.invitacion set revocada_en = now()
       where consorcio_id = c and vinculo_id = v.id
         and aceptada_en is null and revocada_en is null
      returning id
    loop
      insert into auditoria.eventos
        (consorcio_id, actor_persona_id, origen, accion, entidad, entidad_id, hash_previo, hash)
      values (c, actor, origen_pedido, 'identidad.invitacion.revocar', 'invitacion', retirada,
              '', '');
    end loop;

    -- El motivo no va (E31); la referencia del respaldo, sí, como referencia.
    insert into auditoria.eventos
      (consorcio_id, actor_persona_id, origen, accion, entidad, entidad_id, antes, despues,
       hash_previo, hash)
    values (c, actor, origen_pedido, 'consorcio.vinculo.anular', 'vinculo', v.id,
            jsonb_build_object('unidad_id', v.unidad_id, 'tipo', v.tipo,
                               'vigente_desde', v.vigente_desde,
                               'vigente_hasta', v.vigente_hasta, 'fuente', v.fuente,
                               'confirmado', v.confirmado_en is not null,
                               'importacion_id', v.importacion_id),
            case when origen_pedido = 'soporte'
                 then jsonb_build_object('referencia', btrim(referencia)) end,
            '', '');
  end
  $$;

-- La invitación de un vínculo importado (decisiones del propietario del 22-10-2026): la crea,
-- no la envía. Reimportar la misma fila de un importado pendiente cuya invitación venció la
-- renueva con esta misma función (decisión del 23-10-2026). Nombra su vínculo; unidad, carácter
-- y correo salen del vínculo y de su persona.
-- Una pendiente igual (consorcio, unidad, carácter y correo) que ya venció se retira acá, en la
-- misma transacción y auditada; una pendiente vigente no se retira nunca: es
-- `invitacion_pendiente` (D4.2).
create function nucleo.crear_invitacion_importada(
  vinculo uuid, nombre_fila text, token bytea, vigencia interval, actor uuid)
  returns uuid
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    v nucleo.vinculo;
    correo_persona text;
    anterior record;
    nueva uuid;
  begin
    if c is null then
      raise exception 'sin consorcio en el ámbito' using errcode = '42501';
    end if;
    if actor is null or actor is distinct from plataforma.persona_actual() then
      raise exception 'el actor no es la persona de la sesión'
        using errcode = 'P0001', hint = 'actor_no_coincide';
    end if;
    select * into v from nucleo.vinculo x where x.consorcio_id = c and x.id = vinculo;
    if v.id is null or v.fuente <> 'importacion' or v.importacion_id is null then
      raise exception 'solo un vínculo importado lleva una invitación que lo nombra'
        using errcode = 'P0001', hint = 'vinculo_invalido';
    end if;
    if v.vigente_hasta is not null and v.vigente_hasta <= nucleo.hoy_del_consorcio(c) then
      raise exception 'el vínculo ya terminó: su invitación no daría nada'
        using errcode = 'P0001', hint = 'vinculo_finalizado';
    end if;
    -- Solo un importado pendiente (0024) lleva o renueva su invitación: uno confirmado o ya
    -- aceptado no la necesita (renovación por reimportación, decisión del 23-10-2026).
    if v.confirmado_en is not null or exists (
         select 1 from auditoria.eventos e
           join nucleo.invitacion i on i.id = e.entidad_id and i.consorcio_id = e.consorcio_id
          where e.consorcio_id = c and e.accion = 'identidad.invitacion.aceptar'
            and e.entidad = 'invitacion' and e.despues ->> 'vinculo_id' = v.id::text
            and i.aceptada_en is not null) then
      raise exception 'el vínculo ya no está pendiente: no lleva otra invitación'
        using errcode = 'P0001', hint = 'vinculo_no_pendiente';
    end if;
    select p.correo into correo_persona from nucleo.persona p where p.id = v.persona_id;
    for anterior in
      select i.id, i.vence_en, i.vinculo_id from nucleo.invitacion i
       where i.consorcio_id = c and i.unidad_id = v.unidad_id and i.tipo = v.tipo
         and i.correo = correo_persona and i.aceptada_en is null and i.revocada_en is null
       for update
    loop
      -- Una vigente no se retira; una que nombra otro vínculo tampoco, aunque haya vencido
      -- (D4.2). Solo se reemplaza una vencida manual o la vencida de este mismo vínculo.
      if anterior.vence_en > now() or anterior.vinculo_id is distinct from v.id
                                      and anterior.vinculo_id is not null then
        raise exception 'hay una invitación vigente para esa persona, unidad y carácter'
          using errcode = 'P0001', hint = 'invitacion_pendiente';
      end if;
      update nucleo.invitacion set revocada_en = now() where id = anterior.id;
      insert into auditoria.eventos
        (consorcio_id, actor_persona_id, origen, accion, entidad, entidad_id, hash_previo, hash)
      values (c, actor, 'api', 'identidad.invitacion.reemplazar', 'invitacion', anterior.id,
              '', '');
    end loop;
    insert into nucleo.invitacion (consorcio_id, unidad_id, tipo, correo, nombre, token_hash,
                                   invitada_por, vence_en, vinculo_id)
    values (c, v.unidad_id, v.tipo, correo_persona, btrim(nombre_fila), token, actor,
            now() + vigencia, v.id)
    returning id into nueva;
    insert into auditoria.eventos
      (consorcio_id, actor_persona_id, origen, accion, entidad, entidad_id, despues,
       hash_previo, hash)
    values (c, actor, 'api', 'identidad.invitacion.crear', 'invitacion', nueva,
            jsonb_build_object('unidad_id', v.unidad_id, 'tipo', v.tipo, 'vinculo_id', v.id),
            '', '');
    return nueva;
  end
  $$;

-- Los vínculos de este consorcio de las personas con esos correos, para que el informe compare
-- cada fila con lo que ya existe. `cem_app` no lee otras personas (política `propia`); esto no
-- dice nada de una persona sin vínculos en este consorcio, así que no permite saber si un
-- correo tiene cuenta.
create function nucleo.vinculos_de_correos(correos text[])
  returns table (correo text, vinculo_id uuid, unidad_id uuid, tipo nucleo.tipo_vinculo,
                 vigente_desde date, vigente_hasta date, importado_pendiente boolean)
  language plpgsql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    quien uuid := plataforma.persona_actual();
  begin
    if c is null or quien is null then
      raise exception 'sin ámbito para comparar el padrón' using errcode = '42501';
    end if;
    -- Segunda barrera, como `padron_del_consorcio`: la facultad la decide la aplicación.
    if not exists (
      select 1 from nucleo.mandato m
        join nucleo.facultad_otorgada f
          on f.consorcio_id = m.consorcio_id and f.mandato_id = m.id
       where m.consorcio_id = c and m.persona_id = quien and f.facultad = 'padron.gestionar'
         and nucleo.mandato_vigente(m.id, nucleo.hoy_del_consorcio(c))) then
      raise exception 'sin padron.gestionar en este consorcio' using errcode = '42501';
    end if;
    return query
      select p.correo, v.id, v.unidad_id, v.tipo, v.vigente_desde, v.vigente_hasta,
             -- El criterio de 0024: importado, sin confirmar y sin una aceptación que lo nombre.
             v.fuente = 'importacion' and v.confirmado_en is null and not exists (
               select 1 from auditoria.eventos e
                 join nucleo.invitacion i
                   on i.id = e.entidad_id and i.consorcio_id = e.consorcio_id
                where e.consorcio_id = c and e.accion = 'identidad.invitacion.aceptar'
                  and e.entidad = 'invitacion' and e.despues ->> 'vinculo_id' = v.id::text
                  and i.aceptada_en is not null)
        from nucleo.vinculo v
        join nucleo.persona p on p.id = v.persona_id
       where v.consorcio_id = c and p.correo = any (correos)
         -- Como `importar_vinculo`: una cuenta borrada no es la persona de ese correo (Codex
         -- en app #105).
         and p.eliminado_en is null;
  end
  $$;

revoke all on function {REGISTRAR}, {IMPORTAR}, {ANULAR}, {INVITAR}, {DE_CORREOS} from public;
grant execute on function {REGISTRAR}, {IMPORTAR}, {ANULAR}, {INVITAR}, {DE_CORREOS} to cem_app;
"""

DOWNGRADE = f"""
do $guarda$
begin
  if exists (select 1 from nucleo.importacion_padron)
     or exists (select 1 from nucleo.invitacion where vinculo_id is not null) then
    raise exception 'hay importaciones o invitaciones con vínculo: bajar a 0024 perdería su'
                    ' trazabilidad'
      using errcode = 'P0001', hint = 'downgrade_con_datos';
  end if;
end
$guarda$;

drop function {DE_CORREOS};
drop function {INVITAR};
drop function {ANULAR};
drop function {IMPORTAR};
drop function {REGISTRAR};
{_aceptar_de_0014()}
{_padron_de_0014()}
revoke insert ({COLUMNAS_INVITACION_0007}) on nucleo.invitacion from cem_app;
grant insert on nucleo.invitacion to cem_app;
alter table nucleo.invitacion drop column vinculo_id;
revoke select (importacion_id) on nucleo.vinculo from cem_app;
drop index nucleo.vinculo_por_importacion;
alter table nucleo.vinculo
  drop constraint vinculo_nombre_declarado,
  drop constraint vinculo_importacion_solo_importado,
  drop column nombre_declarado,
  drop constraint vinculo_importacion_del_consorcio,
  drop column importacion_id;
drop table nucleo.importacion_padron;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
