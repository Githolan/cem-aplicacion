"""2b-7: contactos de emergencia del consorcio (RF06, primera parte; D5).

Diseño: `doc/diseno/H2b_7_Diseno.md` del repositorio CEM, con la decisión del propietario del
25-10-2026 (`Decisiones_Propietario_2026-10-25.md`, §1.1).

- **`nucleo.contacto_emergencia`:** solo datos de contacto que no son personales. No existe la
  naturaleza «persona particular»; un `profesional` figura solo con su teléfono y la constancia
  del consentimiento. Ningún número ni correo fuera de `telefono`
  (`nucleo.sin_contacto_libre`, §2.1 del diseño). Retirar pone `vigente_hasta`: la fila no se borra.
- **Facultad `contactos.gestionar`** en los paquetes de `miembro_consejo` y `administrador`, no
  `administrador_externo` (documento 08 §4: solo carga). Los mandatos que ya existen no la reciben
  (el criterio de `0028`).
- **Funciones `security definer`**, con la segunda barrera: leer exige ámbito (vínculo vigente no
  pendiente o mandato vigente), y la lista de gestión y las escrituras por la API exigen la
  facultad vigente de quien está en la sesión. Soporte escribe sin persona en la sesión.
- **Auditoría** de cada escritura, sin datos de contacto ni texto libre (`app/auditoria.py`): la
  función, la naturaleza, si hay teléfono y la vigencia. Las cinco acciones entran en la
  proyección funcional de RF35-B (`0028`).

`cem_app` no lee ni escribe la tabla directo.

Downgrade: se niega si hay algún contacto o alguna facultad `contactos.gestionar` otorgada
(`downgrade_con_datos`); si no, quita todo.

Revisión: 0031
Anterior: 0030
"""

from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None

FACULTAD = "contactos.gestionar"
TIPOS_CON_CONTACTOS = ("miembro_consejo", "administrador")
FUNCIONES = (
    "administracion",
    "encargado",
    "emergencias",
    "aseguradora",
    "proveedor_critico",
    "servicio_publico",
    "otro",
)
NATURALEZAS = ("empresa", "servicio", "organismo", "profesional")
CAMPOS = ("funcion", "naturaleza", "tiene_telefono", "vigente_desde", "vigente_hasta")
PROYECCION = (
    ("consorcio.contacto.crear", "Agregó un contacto de emergencia"),
    ("consorcio.contacto.corregir", "Corrigió un contacto de emergencia"),
    ("consorcio.contacto.retirar", "Retiró un contacto de emergencia"),
    ("consorcio.contacto.verificar", "Verificó un contacto de emergencia"),
    ("consorcio.contacto.senalar", "Señaló un dato incorrecto de un contacto de emergencia"),
)


def _lista(valores: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in valores)


def _upgrade() -> str:
    paquetes = ", ".join(f"('{t}', '{FACULTAD}')" for t in TIPOS_CON_CONTACTOS)
    campos = "array[" + _lista(CAMPOS) + "]"
    proyeccion = ",\n  ".join(f"('{a}', '{t}', 'Contacto', {campos})" for a, t in PROYECCION)
    return f"""
-- §2.1: en el texto libre no va un teléfono (8 o más dígitos, con o sin separadores) ni un correo.
-- Se quita todo lo que no sea letra, dígito, dos puntos o coma, y se buscan 8 dígitos seguidos:
-- así ningún separador ('11 - 2345 - 6789') lo esquiva, y un horario ('9:00/13:00') pasa.
create function nucleo.sin_contacto_libre(t text)
  returns boolean
  language sql immutable
  set search_path = pg_catalog, pg_temp
  as $$
    select t is null
        or (regexp_replace(t, '[^[:alnum:]:,]', '', 'g') !~ '[0-9]{{8}}'
            and t !~ '[^[:space:]@]+@[^[:space:]@]+')
  $$;

insert into nucleo.facultad (codigo, area, economica) values ('{FACULTAD}', 'contactos', false);
insert into nucleo.paquete_facultad (tipo, facultad) values {paquetes};
insert into auditoria.proyeccion (accion, texto, recurso, campos) values
  {proyeccion};

create table nucleo.contacto_emergencia (
  consorcio_id              uuid not null references nucleo.consorcio (id),
  id                        uuid primary key default plataforma.uuid_v7(),
  funcion                   text not null check (funcion in ({_lista(FUNCIONES)})),
  naturaleza                text not null check (naturaleza in ({_lista(NATURALEZAS)})),
  nombre                    text not null
    check (length(btrim(nombre)) between 1 and 120 and nucleo.sin_contacto_libre(nombre)),
  telefono                  text check (telefono ~ '^[+0-9][0-9 ()-]{{2,29}}$'),
  consentimiento_referencia text
    check (length(btrim(consentimiento_referencia)) between 1 and 300),
  indicaciones              text check (length(btrim(indicaciones)) between 1 and 300
                                        and nucleo.sin_contacto_libre(indicaciones)),
  vigente_desde             date not null,
  vigente_hasta             date,
  actualizado_por           uuid references nucleo.persona (id),
  actualizado_en            timestamptz not null default now(),
  verificado_por            uuid references nucleo.persona (id),
  verificado_en             timestamptz,
  observacion               text check (length(btrim(observacion)) between 1 and 300
                                        and nucleo.sin_contacto_libre(observacion)),
  observado_por             uuid references nucleo.persona (id),
  observado_en              timestamptz,
  unique (consorcio_id, id),
  -- Un rango vacío (`hasta = desde`) es un contacto futuro que se retiró: nunca figura.
  check (vigente_hasta is null or vigente_hasta >= vigente_desde),
  -- §1.1: un profesional figura solo con su número profesional y la constancia de que se
  -- consintió publicarlo; las demás naturalezas no llevan constancia.
  check ((naturaleza = 'profesional') = (consentimiento_referencia is not null)),
  check (naturaleza <> 'profesional' or telefono is not null),
  check ((observacion is null) = (observado_en is null)),
  check (observacion is not null or observado_por is null),
  check (verificado_en is not null or verificado_por is null)
);
alter table nucleo.contacto_emergencia enable row level security;
alter table nucleo.contacto_emergencia force row level security;
create policy por_consorcio on nucleo.contacto_emergencia for select
  using (consorcio_id = plataforma.consorcio_actual());
revoke all on nucleo.contacto_emergencia from public, cem_app;

-- Ámbito: un vínculo vigente que no sea un importado pendiente (0024) o un mandato vigente.
-- Recibe `quien` para no repetir la lectura, pero solo la llaman las funciones de este archivo,
-- siempre con `plataforma.persona_actual()`; no se concede a `cem_app`.
create function nucleo.en_ambito(c uuid, quien uuid)
  returns boolean
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select exists (
             select 1 from nucleo.vinculo v
              where v.consorcio_id = c and v.persona_id = quien
                and daterange(v.vigente_desde, v.vigente_hasta) @> nucleo.hoy_del_consorcio(c)
                and v.id not in (select nucleo.vinculos_importados_pendientes()))
        or exists (
             select 1 from nucleo.mandato m
              where m.consorcio_id = c and m.persona_id = quien
                and nucleo.mandato_vigente(m.id, nucleo.hoy_del_consorcio(c)))
  $$;
revoke all on function nucleo.en_ambito(uuid, uuid) from public;

-- Cuándo cambió por última vez la lista general, y la zona del consorcio con la que el
-- dispositivo vuelve a aplicar la vigencia a su copia. Cuenta todos los contactos, también los
-- retirados y los que empezaron o terminaron por fecha: un retiro o un vencimiento también la
-- cambian.
create function nucleo.estado_contactos()
  returns table (actualizada_en timestamptz, zona_horaria text)
  language plpgsql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    quien uuid := plataforma.persona_actual();
    hoy date;
    zona text;
  begin
    if c is null or quien is null or not nucleo.en_ambito(c, quien) then
      raise exception 'sin ámbito en este consorcio' using errcode = '42501';
    end if;
    hoy := nucleo.hoy_del_consorcio(c);
    select k.zona_horaria into strict zona from nucleo.consorcio k where k.id = c;
    return query
      select max(greatest(
               k.actualizado_en, k.verificado_en, k.observado_en,
               case when k.vigente_desde <= hoy
                    then k.vigente_desde::timestamp at time zone zona end,
               case when k.vigente_hasta <= hoy
                    then k.vigente_hasta::timestamp at time zone zona end)),
             zona
        from nucleo.contacto_emergencia k
       where k.consorcio_id = c;
  end
  $$;

-- La lista general son los vigentes hoy; la de gestión suma los futuros y lo que no se publica.
create function nucleo.contactos_del_consorcio(gestion boolean)
  returns table (id uuid, funcion text, naturaleza text, nombre text, telefono text,
                 indicaciones text, vigente_desde date, vigente_hasta date,
                 actualizado_en timestamptz, verificado_en timestamptz, observado boolean,
                 consentimiento_referencia text, observacion text, actualizado_por uuid,
                 verificado_por uuid, observado_por uuid)
  language plpgsql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    quien uuid := plataforma.persona_actual();
    hoy date;
  begin
    if c is null or quien is null then
      raise exception 'sin ámbito para leer los contactos' using errcode = '42501';
    end if;
    hoy := nucleo.hoy_del_consorcio(c);
    if gestion and not nucleo.tiene_facultad_vigente(c, quien, '{FACULTAD}', hoy) then
      raise exception 'sin {FACULTAD} en este consorcio' using errcode = '42501';
    end if;
    if not gestion and not nucleo.en_ambito(c, quien) then
      raise exception 'sin ámbito en este consorcio' using errcode = '42501';
    end if;
    return query
      select k.id, k.funcion, k.naturaleza, k.nombre, k.telefono, k.indicaciones,
             k.vigente_desde, k.vigente_hasta, k.actualizado_en, k.verificado_en,
             k.observacion is not null,
             case when gestion then k.consentimiento_referencia end,
             case when gestion then k.observacion end,
             case when gestion then k.actualizado_por end,
             case when gestion then k.verificado_por end,
             case when gestion then k.observado_por end
        from nucleo.contacto_emergencia k
       where k.consorcio_id = c
         and (k.vigente_hasta is null or (k.vigente_hasta > hoy
                                          and k.vigente_hasta > k.vigente_desde))
         and (gestion or k.vigente_desde <= hoy)
       order by k.funcion, k.nombre, k.id;
  end
  $$;

-- Quién escribe: por la API, la persona de la sesión con la facultad vigente; soporte, sin
-- persona en la sesión ni actor.
create function nucleo.exigir_gestion_contactos(c uuid, actor uuid, origen_pedido text)
  returns void
  language plpgsql stable
  set search_path = pg_catalog, pg_temp
  as $$
  begin
    if c is null then
      raise exception 'sin consorcio en el ámbito' using errcode = '42501';
    end if;
    if origen_pedido = 'soporte' then
      if actor is not null or plataforma.persona_actual() is not null then
        raise exception 'soporte escribe sin persona en la sesión'
          using errcode = 'P0001', hint = 'origen_invalido';
      end if;
    elsif origen_pedido = 'api' then
      if actor is null or actor is distinct from plataforma.persona_actual()
         or not nucleo.tiene_facultad_vigente(c, actor, '{FACULTAD}',
                                              nucleo.hoy_del_consorcio(c)) then
        raise exception 'sin {FACULTAD} en este consorcio' using errcode = '42501';
      end if;
    else
      raise exception 'origen inválido' using errcode = 'P0001', hint = 'origen_invalido';
    end if;
  end
  $$;
revoke all on function nucleo.exigir_gestion_contactos(uuid, uuid, text) from public;

create function nucleo.auditar_contacto(c uuid, k nucleo.contacto_emergencia, accion text,
                                        actor uuid, origen_pedido text,
                                        antes nucleo.contacto_emergencia default null)
  returns void
  language sql volatile
  set search_path = pg_catalog, pg_temp
  as $$
    insert into auditoria.eventos
      (consorcio_id, actor_persona_id, origen, accion, entidad, entidad_id, antes, despues,
       hash_previo, hash)
    select c, actor, origen_pedido, accion, 'contacto_emergencia', k.id,
           case when antes.id is not null then jsonb_build_object(
             'funcion', antes.funcion, 'naturaleza', antes.naturaleza,
             'tiene_telefono', antes.telefono is not null,
             'vigente_desde', antes.vigente_desde, 'vigente_hasta', antes.vigente_hasta) end,
           jsonb_build_object(
             'funcion', k.funcion, 'naturaleza', k.naturaleza,
             'tiene_telefono', k.telefono is not null,
             'vigente_desde', k.vigente_desde, 'vigente_hasta', k.vigente_hasta),
           '', ''
  $$;
revoke all on function nucleo.auditar_contacto(uuid, nucleo.contacto_emergencia, text, uuid, text,
  nucleo.contacto_emergencia) from public;

create function nucleo.crear_contacto(
  funcion_nueva text, naturaleza_nueva text, nombre_nuevo text, telefono_nuevo text,
  consentimiento text, indicaciones_nuevas text, desde date, actor uuid, origen_pedido text)
  returns uuid
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    k nucleo.contacto_emergencia;
  begin
    perform nucleo.exigir_gestion_contactos(c, actor, origen_pedido);
    insert into nucleo.contacto_emergencia
      (consorcio_id, funcion, naturaleza, nombre, telefono, consentimiento_referencia,
       indicaciones, vigente_desde, actualizado_por)
    values (c, funcion_nueva, naturaleza_nueva, btrim(nombre_nuevo), btrim(telefono_nuevo),
            btrim(consentimiento), btrim(indicaciones_nuevas),
            coalesce(desde, nucleo.hoy_del_consorcio(c)), actor)
    returning * into k;
    perform nucleo.auditar_contacto(c, k, 'consorcio.contacto.crear', actor, origen_pedido);
    return k.id;
  end
  $$;

-- Un contacto que ya terminó no se toca: se agrega otro.
create function nucleo.contacto_para_cambiar(c uuid, contacto uuid)
  returns nucleo.contacto_emergencia
  language plpgsql volatile
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    k nucleo.contacto_emergencia;
  begin
    select * into k from nucleo.contacto_emergencia x
     where x.consorcio_id = c and x.id = contacto for update;
    if not found then
      raise exception 'el contacto no existe' using errcode = 'P0001', hint = 'no_encontrado';
    end if;
    if k.vigente_hasta is not null and (k.vigente_hasta <= nucleo.hoy_del_consorcio(c)
                                        or k.vigente_hasta <= k.vigente_desde) then
      raise exception 'el contacto ya no está vigente'
        using errcode = 'P0001', hint = 'contacto_terminado';
    end if;
    return k;
  end
  $$;
revoke all on function nucleo.contacto_para_cambiar(uuid, uuid) from public;

create function nucleo.corregir_contacto(
  contacto uuid, funcion_nueva text, naturaleza_nueva text, nombre_nuevo text,
  telefono_nuevo text, consentimiento text, indicaciones_nuevas text, actor uuid,
  origen_pedido text)
  returns void
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    antes nucleo.contacto_emergencia;
    k nucleo.contacto_emergencia;
  begin
    perform nucleo.exigir_gestion_contactos(c, actor, origen_pedido);
    antes := nucleo.contacto_para_cambiar(c, contacto);
    -- Corregir resuelve la observación: el dato señalado ya se revisó.
    update nucleo.contacto_emergencia x
       set funcion = funcion_nueva, naturaleza = naturaleza_nueva, nombre = btrim(nombre_nuevo),
           telefono = btrim(telefono_nuevo), consentimiento_referencia = btrim(consentimiento),
           indicaciones = btrim(indicaciones_nuevas), actualizado_por = actor,
           actualizado_en = now(), observacion = null, observado_por = null,
           observado_en = null
     where x.consorcio_id = c and x.id = contacto
    returning * into k;
    perform nucleo.auditar_contacto(c, k, 'consorcio.contacto.corregir', actor, origen_pedido,
                                    antes);
  end
  $$;

create function nucleo.retirar_contacto(contacto uuid, actor uuid, origen_pedido text)
  returns void
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    antes nucleo.contacto_emergencia;
    k nucleo.contacto_emergencia;
    hoy date;
  begin
    perform nucleo.exigir_gestion_contactos(c, actor, origen_pedido);
    antes := nucleo.contacto_para_cambiar(c, contacto);
    hoy := nucleo.hoy_del_consorcio(c);
    -- Desde hoy deja de figurar. Uno que todavía no empezó queda con el rango vacío: no llega
    -- a figurar nunca.
    update nucleo.contacto_emergencia x
       set vigente_hasta = greatest(hoy, x.vigente_desde), actualizado_por = actor,
           actualizado_en = now()
     where x.consorcio_id = c and x.id = contacto
    returning * into k;
    perform nucleo.auditar_contacto(c, k, 'consorcio.contacto.retirar', actor, origen_pedido,
                                    antes);
  end
  $$;

create function nucleo.verificar_contacto(contacto uuid, actor uuid, origen_pedido text)
  returns void
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    k nucleo.contacto_emergencia;
  begin
    perform nucleo.exigir_gestion_contactos(c, actor, origen_pedido);
    perform nucleo.contacto_para_cambiar(c, contacto);
    update nucleo.contacto_emergencia x
       set verificado_por = actor, verificado_en = now(), observacion = null,
           observado_por = null, observado_en = null
     where x.consorcio_id = c and x.id = contacto
    returning * into k;
    perform nucleo.auditar_contacto(c, k, 'consorcio.contacto.verificar', actor, origen_pedido);
  end
  $$;

create function nucleo.senalar_contacto(contacto uuid, motivo text, actor uuid,
                                        origen_pedido text)
  returns void
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    k nucleo.contacto_emergencia;
  begin
    perform nucleo.exigir_gestion_contactos(c, actor, origen_pedido);
    perform nucleo.contacto_para_cambiar(c, contacto);
    -- El motivo queda en la fila, no en la auditoría (texto libre).
    update nucleo.contacto_emergencia x
       set observacion = btrim(motivo), observado_por = actor, observado_en = now()
     where x.consorcio_id = c and x.id = contacto
    returning * into k;
    perform nucleo.auditar_contacto(c, k, 'consorcio.contacto.senalar', actor, origen_pedido);
  end
  $$;

revoke all on function
  nucleo.contactos_del_consorcio(boolean),
  nucleo.estado_contactos(),
  nucleo.crear_contacto(text, text, text, text, text, text, date, uuid, text),
  nucleo.corregir_contacto(uuid, text, text, text, text, text, text, uuid, text),
  nucleo.retirar_contacto(uuid, uuid, text),
  nucleo.verificar_contacto(uuid, uuid, text),
  nucleo.senalar_contacto(uuid, text, uuid, text)
  from public;
grant execute on function
  nucleo.contactos_del_consorcio(boolean),
  nucleo.estado_contactos(),
  nucleo.crear_contacto(text, text, text, text, text, text, date, uuid, text),
  nucleo.corregir_contacto(uuid, text, text, text, text, text, text, uuid, text),
  nucleo.retirar_contacto(uuid, uuid, text),
  nucleo.verificar_contacto(uuid, uuid, text),
  nucleo.senalar_contacto(uuid, text, uuid, text)
  to cem_app;
"""


DOWNGRADE = f"""
do $guarda$
begin
  if exists (select 1 from nucleo.contacto_emergencia)
     or exists (select 1 from nucleo.facultad_otorgada where facultad = '{FACULTAD}') then
    raise exception 'hay contactos o facultades {FACULTAD} otorgadas: no se bajan'
      using errcode = 'P0001', hint = 'downgrade_con_datos';
  end if;
end
$guarda$;
drop function nucleo.senalar_contacto(uuid, text, uuid, text);
drop function nucleo.verificar_contacto(uuid, uuid, text);
drop function nucleo.retirar_contacto(uuid, uuid, text);
drop function nucleo.corregir_contacto(uuid, text, text, text, text, text, text, uuid, text);
drop function nucleo.contacto_para_cambiar(uuid, uuid);
drop function nucleo.crear_contacto(text, text, text, text, text, text, date, uuid, text);
drop function nucleo.auditar_contacto(uuid, nucleo.contacto_emergencia, text, uuid, text,
  nucleo.contacto_emergencia);
drop function nucleo.exigir_gestion_contactos(uuid, uuid, text);
drop function nucleo.contactos_del_consorcio(boolean);
drop function nucleo.estado_contactos();
drop function nucleo.en_ambito(uuid, uuid);
drop table nucleo.contacto_emergencia;
drop function nucleo.sin_contacto_libre(text);
delete from auditoria.proyeccion where accion like 'consorcio.contacto.%';
delete from nucleo.paquete_facultad where facultad = '{FACULTAD}';
delete from nucleo.facultad where codigo = '{FACULTAD}';
"""


def upgrade() -> None:
    op.execute(_upgrade())


def downgrade() -> None:
    op.execute(DOWNGRADE)
