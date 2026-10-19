"""A7: la cuenta del mandatario externo, con cuatro ojos, y su corrección (H2b 2b-4, PR 4).

Diseño: `doc/diseno/H2b_4_Diseno.md` de CEM, §7 (decisión P1 del propietario, 19-10-2026).

- `nucleo.invitacion_mandato`: una persona con `mandatos.registrar` emite la invitación al correo
  del mandatario externo, con la referencia de dónde sale ese correo (P1-b); otra persona la
  aprueba (cuatro ojos), y recién entonces se fijan el token y el vencimiento y sale el correo.
  Solo si los cuatro ojos no son viables, el equipo la emite y aprueba en un paso por script
  (`soporte`), con un motivo obligatorio (P1-e). La asociación la hace la persona invitada al
  aceptar, desde ese mismo correo.
- `nucleo.desasociacion_mandato`: corregir una cuenta asociada por error (P1-c) deja
  `persona_id` nulo, conserva el CUIT, el mandato y su vigencia, revoca las sesiones de la
  persona y guarda la referencia en esta fila, nunca en el evento.
- Correo, referencia y motivo no los lee `cem_app` (permiso por columna): solo
  `invitaciones_de_mandato`, y solo de la invitación por aprobar, para quien la emitió o tiene
  hoy `mandatos.registrar` en el consorcio (P1 sobre P6). La auditoría nunca los lleva.
- Al aceptar: invitación aprobada y vigente; mandato AE, sin persona, vigente o futuro; el
  correo de la cuenta es el invitado; quien acepta no es quien emitió ni quien aprobó; sin un
  mandato de Consejo vigente o futuro superpuesto (P1-d); quien emitió y quien aprobó siguen
  habilitados hoy. Orden de bloqueo único: el mandato AE, la invitación, `for share` los
  mandatos de emisor y aprobador, y el bloqueo consultivo por correo al final.
- El downgrade se niega si hay cualquier fila en las dos tablas: cada una guarda la única copia
  de una referencia documental obligatoria que sus eventos nombran.

Revisión: 0015
Anterior: 0014
"""

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

FIRMAS = """
  nucleo.invitar_a_mandato(uuid, text, text, text, bytea, timestamptz, uuid, text),
  nucleo.aprobar_invitacion_mandato(uuid, bytea, timestamptz, uuid, text),
  nucleo.revocar_invitacion_mandato(uuid, uuid, text),
  nucleo.invitaciones_de_mandato(uuid),
  nucleo.consultar_invitacion_mandato(bytea),
  nucleo.aceptar_invitacion_mandato(bytea, uuid, text, bytea, uuid, text, inet, interval),
  nucleo.desasociar_persona_de_mandato(uuid, text, text, uuid, text)"""

FIRMAS_INTERNAS = """
  nucleo.exigir_actor_y_origen(uuid, text),
  nucleo.habilitado_para_mandatos(uuid, uuid, date),
  nucleo.mandato_asociable(nucleo.mandato, date)"""

UPGRADE = rf"""
create table nucleo.invitacion_mandato (
  id              uuid primary key default plataforma.uuid_v7(),
  consorcio_id    uuid not null,
  mandato_id      uuid not null,
  correo          text not null,
  referencia      text not null,
  origen          text not null,
  emitida_por     uuid references nucleo.persona (id),
  motivo_soporte  text,
  creada_en       timestamptz not null default now(),
  aprobada_por    uuid references nucleo.persona (id),
  aprobada_en     timestamptz,
  -- SHA-256 del token del enlace: se fija recién al aprobar.
  token_hash      bytea unique,
  vence_en        timestamptz,
  aceptada_en     timestamptz,
  persona_id      uuid references nucleo.persona (id),
  revocada_en     timestamptz,
  foreign key (consorcio_id, mandato_id) references nucleo.mandato (consorcio_id, id),
  constraint invitacion_mandato_correo
    check (correo = lower(btrim(correo)) and position('@' in correo) > 1
           and length(correo) <= 254),
  constraint invitacion_mandato_referencia check (length(btrim(referencia)) between 1 and 300),
  constraint invitacion_mandato_origen check (origen in ('api', 'soporte')),
  constraint invitacion_mandato_emisor_segun_origen
    check ((origen = 'api') = (emitida_por is not null)),
  constraint invitacion_mandato_motivo_segun_origen
    check ((origen = 'soporte') = (motivo_soporte is not null)),
  constraint invitacion_mandato_motivo
    check (motivo_soporte is null or length(btrim(motivo_soporte)) between 1 and 300),
  -- Con api, aprobar deja quién y cuándo; con soporte, emitir y aprobar es un solo paso del
  -- script: queda cuándo, sin persona.
  constraint invitacion_mandato_aprobacion
    check (case when origen = 'api' then (aprobada_por is null) = (aprobada_en is null)
                else aprobada_por is null and aprobada_en is not null end),
  constraint invitacion_mandato_aprobador_no_emisor
    check (aprobada_por is null or emitida_por is null or aprobada_por <> emitida_por),
  constraint invitacion_mandato_token_32 check (octet_length(token_hash) = 32),
  constraint invitacion_mandato_token_al_aprobar
    check ((token_hash is null) = (aprobada_en is null)
           and (vence_en is null) = (aprobada_en is null)),
  -- 7 días como máximo desde la aprobación: la base lo impone aunque la API se equivoque.
  constraint invitacion_mandato_vigencia
    check (vence_en is null
           or (vence_en > aprobada_en and vence_en - aprobada_en <= interval '7 days')),
  constraint invitacion_mandato_aceptada_con_persona
    check ((aceptada_en is null) = (persona_id is null)),
  constraint invitacion_mandato_aceptada_o_revocada
    check (aceptada_en is null or revocada_en is null),
  constraint invitacion_mandato_aceptada_aprobada
    check (aceptada_en is null or aprobada_en is not null),
  -- Con <> y no con `is distinct from`: con columnas nulas (por aprobar, o de soporte) la fila
  -- tiene que poder existir.
  constraint invitacion_mandato_no_de_si_mismo
    check (persona_id is null
           or ((emitida_por is null or persona_id <> emitida_por)
               and (aprobada_por is null or persona_id <> aprobada_por)))
);
-- Una sola invitación sin aceptar ni retirar por mandato: emitir de nuevo retira la anterior.
create unique index invitacion_mandato_pendiente_unica on nucleo.invitacion_mandato (mandato_id)
  where aceptada_en is null and revocada_en is null;
create index invitacion_mandato_por_mandato
  on nucleo.invitacion_mandato (consorcio_id, mandato_id, creada_en desc);

create table nucleo.desasociacion_mandato (
  id              uuid primary key default plataforma.uuid_v7(),
  consorcio_id    uuid not null,
  mandato_id      uuid not null,
  -- La persona que tenía el mandato.
  persona_id      uuid not null references nucleo.persona (id),
  referencia      text not null,
  origen          text not null,
  actor           uuid references nucleo.persona (id),
  motivo_soporte  text,
  creada_en       timestamptz not null default now(),
  foreign key (consorcio_id, mandato_id) references nucleo.mandato (consorcio_id, id),
  constraint desasociacion_referencia check (length(btrim(referencia)) between 1 and 300),
  constraint desasociacion_origen check (origen in ('api', 'soporte')),
  constraint desasociacion_actor_segun_origen check ((origen = 'api') = (actor is not null)),
  constraint desasociacion_motivo_segun_origen
    check ((origen = 'soporte') = (motivo_soporte is not null)),
  constraint desasociacion_motivo
    check (motivo_soporte is null or length(btrim(motivo_soporte)) between 1 and 300)
);
create index desasociacion_por_mandato on nucleo.desasociacion_mandato (consorcio_id, mandato_id);

alter table nucleo.invitacion_mandato enable row level security;
alter table nucleo.invitacion_mandato force row level security;
alter table nucleo.desasociacion_mandato enable row level security;
alter table nucleo.desasociacion_mandato force row level security;
create policy por_consorcio on nucleo.invitacion_mandato for select
  using (consorcio_id = plataforma.consorcio_actual());
create policy por_consorcio on nucleo.desasociacion_mandato for select
  using (consorcio_id = plataforma.consorcio_actual());
-- Solo lectura y solo de las columnas sin datos personales ni documentales: las escrituras
-- van por las funciones, y correo, referencia y motivo los lee `invitaciones_de_mandato`.
grant select (id, consorcio_id, mandato_id, origen, emitida_por, creada_en, aprobada_por,
              aprobada_en, vence_en, aceptada_en, persona_id, revocada_en)
  on nucleo.invitacion_mandato to cem_app;
grant select (id, consorcio_id, mandato_id, persona_id, origen, actor, creada_en)
  on nucleo.desasociacion_mandato to cem_app;

-- El actor es la persona de la sesión (la misma que usa la RLS): con 'api' tiene que serlo, y
-- con 'soporte' no hay persona en la sesión ni actor (scripts del equipo; #66).
create function nucleo.exigir_actor_y_origen(actor uuid, origen_pedido text) returns void
  language plpgsql stable
  set search_path = pg_catalog, pg_temp
  as $$
  begin
    if origen_pedido is null or origen_pedido not in ('api', 'soporte') then
      raise exception 'origen no admitido: solo api o soporte'
        using errcode = 'P0001', hint = 'origen_invalido';
    end if;
    if origen_pedido = 'api' and actor is null then
      raise exception 'un pedido por la API necesita quién lo hace'
        using errcode = 'P0001', hint = 'actor_requerido';
    end if;
    if origen_pedido = 'api' and actor is distinct from plataforma.persona_actual() then
      raise exception 'el actor no es la persona de la sesión'
        using errcode = 'P0001', hint = 'actor_no_coincide';
    end if;
    if origen_pedido = 'soporte'
       and (plataforma.persona_actual() is not null or actor is not null) then
      raise exception 'el origen soporte no admite persona en la sesión ni actor'
        using errcode = 'P0001', hint = 'origen_invalido';
    end if;
  end
  $$;

-- ¿Tiene hoy `quien` un mandato válido con `mandatos.registrar` en el consorcio? Comprobación
-- mínima, como la barrera del padrón (0014): no es una copia del evaluador.
create function nucleo.habilitado_para_mandatos(quien uuid, consorcio uuid, hoy date)
  returns boolean
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select exists (
      select 1 from nucleo.mandato m
        join nucleo.facultad_otorgada f on f.mandato_id = m.id
       where m.consorcio_id = consorcio and m.persona_id = quien
         and f.facultad = 'mandatos.registrar'
         and nucleo.mandato_vigente(m.id, hoy))
  $$;

-- Nulo si el mandato admite una cuenta; si no, por qué: un administrador externo, sin persona,
-- sin baja y vigente o futuro.
create function nucleo.mandato_asociable(m nucleo.mandato, hoy date) returns text
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select case
      when m.id is null then 'mandato_invalido'
      when m.tipo <> 'administrador_externo' then 'mandato_no_asociable'
      when m.causa_baja is not null
           or (m.vigente_hasta is not null and m.vigente_hasta <= hoy) then 'mandato_no_vigente'
      when m.persona_id is not null then 'mandato_ya_asociado'
    end
  $$;

create function nucleo.invitar_a_mandato(
  mandato uuid, correo_invitado text, referencia text, motivo text, token bytea,
  vence timestamptz, actor uuid, origen_pedido text)
  returns uuid
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    m nucleo.mandato;
    motivo_rechazo text;
    anterior uuid;
    nueva uuid;
    correo_normal text := lower(btrim(correo_invitado));
  begin
    if c is null then
      raise exception 'sin consorcio en el ámbito' using errcode = '42501';
    end if;
    perform nucleo.exigir_actor_y_origen(actor, origen_pedido);
    -- Con api queda por aprobar: sin motivo, token ni vencimiento. Con soporte, el script
    -- emite y aprueba en un paso, con el motivo de por qué no hubo cuatro ojos (P1-e).
    if origen_pedido = 'api' and (motivo is not null or token is not null or vence is not null) then
      raise exception 'por la API queda por aprobar: sin motivo, token ni vencimiento'
        using errcode = 'P0001', hint = 'origen_invalido';
    end if;
    if origen_pedido = 'soporte' and (motivo is null or btrim(motivo) = '') then
      raise exception 'por soporte hace falta el motivo de por qué no hubo cuatro ojos'
        using errcode = 'P0001', hint = 'motivo_requerido';
    end if;
    if origen_pedido = 'soporte' and (token is null or vence is null) then
      raise exception 'por soporte se aprueba en el mismo paso: hacen falta token y vencimiento'
        using errcode = 'P0001', hint = 'origen_invalido';
    end if;
    if referencia is null or length(btrim(referencia)) not between 1 and 300 then
      raise exception 'la invitación necesita la referencia de dónde sale el correo'
        using errcode = 'P0001', hint = 'respaldo_requerido';
    end if;
    if correo_normal is null or position('@' in correo_normal) <= 1
       or length(correo_normal) > 254 then
      raise exception 'el correo no es válido' using errcode = 'P0001', hint = 'correo_invalido';
    end if;
    select * into m from nucleo.mandato x where x.consorcio_id = c and x.id = mandato for update;
    motivo_rechazo := nucleo.mandato_asociable(m, nucleo.hoy_del_consorcio(c));
    if motivo_rechazo is not null then
      raise exception 'el mandato no admite una invitación: %', motivo_rechazo
        using errcode = 'P0001', hint = motivo_rechazo;
    end if;

    for anterior in
      update nucleo.invitacion_mandato set revocada_en = now()
       where mandato_id = m.id and aceptada_en is null and revocada_en is null
      returning id
    loop
      insert into auditoria.eventos
        (consorcio_id, actor_persona_id, origen, accion, entidad, entidad_id, despues,
         hash_previo, hash)
      values (c, actor, origen_pedido, 'consorcio.mandato.reemplazar_invitacion',
              'invitacion_mandato', anterior, jsonb_build_object('mandato_id', m.id), '', '');
    end loop;

    insert into nucleo.invitacion_mandato
      (consorcio_id, mandato_id, correo, referencia, origen, emitida_por, motivo_soporte,
       aprobada_en, token_hash, vence_en)
    values (c, m.id, correo_normal, btrim(referencia), origen_pedido, actor, btrim(motivo),
            case when origen_pedido = 'soporte' then now() end, token, vence)
    returning id into nueva;

    insert into auditoria.eventos
      (consorcio_id, actor_persona_id, origen, accion, entidad, entidad_id, despues,
       hash_previo, hash)
    values (c, actor, origen_pedido, 'consorcio.mandato.invitar_persona', 'invitacion_mandato',
            nueva, jsonb_build_object('mandato_id', m.id, 'un_paso', origen_pedido = 'soporte'),
            '', '');
    return nueva;
  end
  $$;

-- Aprueba ESA invitación (la que vio quien aprueba), que tiene que seguir siendo la que está
-- por aprobar del mandato, siempre con otra persona que la emitió. Devuelve lo que necesita el
-- correo: el correo invitado, el consorcio y el mandatario.
create function nucleo.aprobar_invitacion_mandato(
  invitacion uuid, token bytea, vence timestamptz, actor uuid, origen_pedido text)
  returns table (correo text, consorcio text, mandatario text, emisor text)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    i nucleo.invitacion_mandato;
    m nucleo.mandato;
    motivo_rechazo text;
  begin
    if c is null then
      raise exception 'sin consorcio en el ámbito' using errcode = '42501';
    end if;
    perform nucleo.exigir_actor_y_origen(actor, origen_pedido);
    if origen_pedido <> 'api' then
      raise exception 'aprobar es solo por la API: soporte aprueba al emitir'
        using errcode = 'P0001', hint = 'origen_invalido';
    end if;
    if token is null or vence is null then
      raise exception 'aprobar fija el token y el vencimiento'
        using errcode = 'P0001', hint = 'token_requerido';
    end if;
    -- Orden de bloqueo único: primero el mandato, después la invitación.
    select x.mandato_id into i.mandato_id from nucleo.invitacion_mandato x
     where x.consorcio_id = c and x.id = invitacion;
    if i.mandato_id is null then
      raise exception 'la invitación no existe en este consorcio'
        using errcode = 'P0001', hint = 'invitacion_invalida';
    end if;
    select * into m from nucleo.mandato x
     where x.consorcio_id = c and x.id = i.mandato_id for update;
    select * into i from nucleo.invitacion_mandato x where x.id = invitacion for update;
    -- Reemplazada o retirada: lo que vio el aprobador ya no vale (404). Ya aceptada o ya
    -- aprobada: 409, el estado es otro (diseño §12.1).
    if i.revocada_en is not null then
      raise exception 'la invitación ya no es la que está por aprobar'
        using errcode = 'P0001', hint = 'invitacion_invalida';
    end if;
    if i.aceptada_en is not null then
      raise exception 'la invitación ya se aceptó'
        using errcode = 'P0001', hint = 'invitacion_aceptada';
    end if;
    if i.aprobada_en is not null then
      raise exception 'la invitación ya está aprobada'
        using errcode = 'P0001', hint = 'invitacion_ya_aprobada';
    end if;
    if i.emitida_por = actor then
      raise exception 'quien emitió la invitación no la aprueba'
        using errcode = 'P0001', hint = 'aprobacion_de_si_mismo';
    end if;
    motivo_rechazo := nucleo.mandato_asociable(m, nucleo.hoy_del_consorcio(c));
    if motivo_rechazo is not null then
      raise exception 'el mandato no admite una invitación: %', motivo_rechazo
        using errcode = 'P0001', hint = motivo_rechazo;
    end if;
    update nucleo.invitacion_mandato
       set aprobada_por = actor, aprobada_en = now(), token_hash = token, vence_en = vence
     where id = i.id;
    insert into auditoria.eventos
      (consorcio_id, actor_persona_id, origen, accion, entidad, entidad_id, despues,
       hash_previo, hash)
    values (c, actor, 'api', 'consorcio.mandato.aprobar_invitacion', 'invitacion_mandato', i.id,
            jsonb_build_object('mandato_id', m.id), '', '');
    return query
    select i.correo, co.nombre, m.mandatario_nombre, p.nombre
      from nucleo.consorcio co left join nucleo.persona p on p.id = i.emitida_por
     where co.id = c;
  end
  $$;

-- Retira una invitación no aceptada. Sobre una ya retirada no falla: devuelve false (reintento).
create function nucleo.revocar_invitacion_mandato(
  invitacion uuid, actor uuid, origen_pedido text)
  returns boolean
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    i nucleo.invitacion_mandato;
  begin
    if c is null then
      raise exception 'sin consorcio en el ámbito' using errcode = '42501';
    end if;
    perform nucleo.exigir_actor_y_origen(actor, origen_pedido);
    select x.mandato_id into i.mandato_id from nucleo.invitacion_mandato x
     where x.consorcio_id = c and x.id = invitacion;
    if i.mandato_id is null then
      raise exception 'la invitación no existe en este consorcio'
        using errcode = 'P0001', hint = 'invitacion_invalida';
    end if;
    perform 1 from nucleo.mandato x where x.consorcio_id = c and x.id = i.mandato_id for update;
    select * into i from nucleo.invitacion_mandato x where x.id = invitacion for update;
    if i.aceptada_en is not null then
      raise exception 'la invitación ya se aceptó: para corregir la cuenta se desasocia'
        using errcode = 'P0001', hint = 'invitacion_aceptada';
    end if;
    if i.revocada_en is not null then
      return false;
    end if;
    update nucleo.invitacion_mandato set revocada_en = now() where id = i.id;
    insert into auditoria.eventos
      (consorcio_id, actor_persona_id, origen, accion, entidad, entidad_id, despues,
       hash_previo, hash)
    values (c, actor, origen_pedido, 'consorcio.mandato.revocar_invitacion',
            'invitacion_mandato', i.id, jsonb_build_object('mandato_id', i.mandato_id), '', '');
    return true;
  end
  $$;

-- Las invitaciones de un mandato del consorcio del ámbito. Correo y referencia salen solo de
-- la invitación por aprobar, y solo para quien la emitió o tiene hoy `mandatos.registrar` en
-- el consorcio; en cualquier otro estado, nulos para todos (P1 sobre P6).
create function nucleo.invitaciones_de_mandato(mandato uuid)
  returns table (id uuid, estado text, correo text, referencia text, origen text,
                 emitida_por_mi boolean, creada_en timestamptz, aprobada_en timestamptz,
                 vence_en timestamptz, aceptada_en timestamptz, revocada_en timestamptz)
  language plpgsql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    yo uuid := plataforma.persona_actual();
    habilitado boolean;
  begin
    if c is null or yo is null then
      raise exception 'sin ámbito para leer las invitaciones' using errcode = '42501';
    end if;
    habilitado := nucleo.habilitado_para_mandatos(yo, c, nucleo.hoy_del_consorcio(c));
    return query
    select i.id, e.estado,
           case when e.estado = 'por_aprobar' and (i.emitida_por = yo or habilitado)
                then i.correo end,
           case when e.estado = 'por_aprobar' and (i.emitida_por = yo or habilitado)
                then i.referencia end,
           i.origen, coalesce(i.emitida_por = yo, false), i.creada_en, i.aprobada_en,
           i.vence_en, i.aceptada_en, i.revocada_en
      from nucleo.invitacion_mandato i
      cross join lateral (select case
        when i.aceptada_en is not null then 'aceptada'
        when i.revocada_en is not null then 'revocada'
        when i.aprobada_en is null then 'por_aprobar'
        when i.vence_en <= now() then 'vencida'
        else 'pendiente' end as estado) e
     where i.consorcio_id = c and i.mandato_id = mandato
     order by i.creada_en desc, i.id desc;
  end
  $$;

-- Lo que ve quien abre el enlace, antes de aceptar: consorcio, mandatario y quién emitió, sin
-- CUIT. `resultado` es 'ok' o el motivo (para los registros; la API responde igual a todos).
create function nucleo.consultar_invitacion_mandato(token bytea)
  returns table (resultado text, consorcio text, mandatario text, invitada_por text,
                 vence timestamptz)
  language plpgsql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    i nucleo.invitacion_mandato;
    m nucleo.mandato;
    motivo text;
  begin
    select * into i from nucleo.invitacion_mandato x where x.token_hash = token;
    select * into m from nucleo.mandato x where x.id = i.mandato_id;
    motivo := case
      when i.id is null then 'inexistente'
      when i.aceptada_en is not null then 'aceptada'
      when i.revocada_en is not null then 'revocada'
      when i.vence_en <= now() then 'vencida'
      when not exists (select 1 from nucleo.consorcio co
                        where co.id = i.consorcio_id and co.eliminado_en is null)
        then 'consorcio_eliminado'
      else nucleo.mandato_asociable(m, nucleo.hoy_del_consorcio(i.consorcio_id))
    end;
    if motivo is not null then
      return query select motivo, null::text, null::text, null::text, null::timestamptz;
      return;
    end if;
    return query
    select 'ok'::text, co.nombre, m.mandatario_nombre, p.nombre, i.vence_en
      from nucleo.consorcio co left join nucleo.persona p on p.id = i.emitida_por
     where co.id = i.consorcio_id;
  end
  $$;

-- Acepta la invitación y asocia la persona al mandato. `persona_sesion` es la persona de la
-- sesión de la petición, si hay.
--   'requiere_sesion'  el correo ya tiene cuenta y la petición no trae sesión.
--   'otra_persona'     la sesión es de otra persona que la del correo invitado.
--   'ok'               mandato asociado; si el correo no tenía cuenta, `cuenta_nueva` y la
--                      sesión creada con `sesion_token`.
-- Cualquier otro valor es el motivo del rechazo; los rechazos no consumen la invitación. Sin
-- sesión no hay reintento: un token ya aceptado responde 'aceptada'. Con la sesión de quien
-- aceptó, 'ya_asociado' solo si el mandato sigue siendo hoy de esa persona.
create function nucleo.aceptar_invitacion_mandato(
  invitacion_token bytea, persona_sesion uuid, nombre_elegido text, sesion_token bytea,
  dispositivo_pedido uuid, agente_pedido text, ip_pedido inet, vigencia interval)
  returns table (resultado text, persona uuid, consorcio uuid, mandato uuid,
                 cuenta_nueva boolean, sesion uuid, vence timestamptz, vigente_desde date)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    i nucleo.invitacion_mandato;
    m nucleo.mandato;
    motivo text;
    hoy date;
    p uuid;
    nueva boolean := false;
    s uuid;
    v timestamptz;
  begin
    -- Sin bloquear, solo para saber el mandato; después se bloquea en el orden único.
    select x.mandato_id into i.mandato_id from nucleo.invitacion_mandato x
     where x.token_hash = invitacion_token;
    if i.mandato_id is null then
      return query select 'inexistente'::text, null::uuid, null::uuid, null::uuid,
                          null::boolean, null::uuid, null::timestamptz, null::date;
      return;
    end if;
    select * into m from nucleo.mandato x where x.id = i.mandato_id for update;
    select * into i from nucleo.invitacion_mandato x
     where x.token_hash = invitacion_token for update;
    hoy := nucleo.hoy_del_consorcio(m.consorcio_id);

    if i.aceptada_en is not null and persona_sesion is not null
       and i.persona_id = persona_sesion and m.persona_id = persona_sesion then
      return query select 'ya_asociado'::text, persona_sesion, m.consorcio_id, m.id, false,
                          null::uuid, null::timestamptz, m.vigente_desde;
      return;
    end if;
    motivo := case
      when i.aceptada_en is not null then 'aceptada'
      when i.revocada_en is not null then 'revocada'
      when i.aprobada_en is null then 'por_aprobar'
      when i.vence_en <= now() then 'vencida'
      when not exists (select 1 from nucleo.consorcio co
                        where co.id = m.consorcio_id and co.eliminado_en is null)
        then 'consorcio_eliminado'
      else nucleo.mandato_asociable(m, hoy)
    end;
    if motivo is null then
      -- Quien emitió y quien aprobó (si no fue soporte) siguen habilitados hoy. Sus mandatos
      -- se leen `for share`: una baja concurrente espera.
      perform 1 from nucleo.mandato x
       where x.consorcio_id = m.consorcio_id
         and x.persona_id in (i.emitida_por, i.aprobada_por)
       order by x.id
         for share;
      if (i.emitida_por is not null
          and not nucleo.habilitado_para_mandatos(i.emitida_por, m.consorcio_id, hoy))
         or (i.aprobada_por is not null
             and not nucleo.habilitado_para_mandatos(i.aprobada_por, m.consorcio_id, hoy)) then
        motivo := 'emisor_no_habilitado';
      end if;
    end if;
    if motivo is not null then
      return query select motivo, null::uuid, null::uuid, null::uuid, null::boolean,
                          null::uuid, null::timestamptz, null::date;
      return;
    end if;

    -- El bloqueo consultivo por correo, último: dos aceptaciones al mismo correo nuevo no
    -- crean dos personas.
    perform pg_advisory_xact_lock(hashtextextended('persona:' || i.correo, 0));
    select x.id into p from nucleo.persona x
     where x.correo = i.correo and x.eliminado_en is null;
    if p is null then
      if persona_sesion is not null then
        return query select 'otra_persona'::text, null::uuid, null::uuid, null::uuid,
                            null::boolean, null::uuid, null::timestamptz, null::date;
        return;
      end if;
    elsif persona_sesion is null then
      return query select 'requiere_sesion'::text, null::uuid, null::uuid, null::uuid,
                          null::boolean, null::uuid, null::timestamptz, null::date;
      return;
    elsif persona_sesion <> p then
      return query select 'otra_persona'::text, null::uuid, null::uuid, null::uuid,
                          null::boolean, null::uuid, null::timestamptz, null::date;
      return;
    end if;
    if p is not null and (p = i.emitida_por or p = i.aprobada_por) then
      return query select 'asociacion_de_si_mismo'::text, null::uuid, null::uuid, null::uuid,
                          null::boolean, null::uuid, null::timestamptz, null::date;
      return;
    end if;
    -- P1-d: el Consejo fiscaliza y no co-administra. Un mandato de Consejo vigente o futuro
    -- que se superponga con la vigencia del AE impide la asociación.
    if p is not null and exists (
         select 1 from nucleo.mandato x
          where x.consorcio_id = m.consorcio_id and x.persona_id = p
            and x.tipo = 'miembro_consejo'
            and x.causa_baja is distinct from 'error_carga'
            and (x.vigente_hasta is null or x.vigente_hasta > hoy)
            and daterange(x.vigente_desde, x.vigente_hasta)
                && daterange(m.vigente_desde, m.vigente_hasta)) then
      return query select 'asociacion_incompatible_con_consejo'::text, null::uuid, null::uuid,
                          null::uuid, null::boolean, null::uuid, null::timestamptz, null::date;
      return;
    end if;
    if p is null then
      insert into nucleo.persona (correo, nombre)
      values (i.correo, btrim(coalesce(nombre_elegido, m.mandatario_nombre)))
      returning id into p;
      nueva := true;
    end if;

    update nucleo.mandato set persona_id = p where id = m.id;
    update nucleo.invitacion_mandato set aceptada_en = now(), persona_id = p where id = i.id;
    insert into auditoria.eventos
      (consorcio_id, actor_persona_id, origen, accion, entidad, entidad_id, antes, despues, ip,
       hash_previo, hash)
    values (m.consorcio_id, p, 'api', 'consorcio.mandato.asociar_persona', 'mandato', m.id,
            jsonb_build_object('persona_id', null),
            jsonb_build_object('persona_id', p, 'invitacion_id', i.id, 'cuenta_nueva', nueva),
            ip_pedido, '', '');
    if nueva then
      insert into nucleo.sesion (persona_id, token_hash, dispositivo, agente, ip, vence_en)
      values (p, sesion_token, dispositivo_pedido, agente_pedido, ip_pedido, now() + vigencia)
      returning id, vence_en into s, v;
    end if;
    return query select 'ok'::text, p, m.consorcio_id, m.id, nueva, s, v, m.vigente_desde;
  end
  $$;

-- Corrige una cuenta asociada por error (P1-c): deja `persona_id` nulo, conserva el CUIT, el
-- mandato y su vigencia, revoca las sesiones de esa persona (RN22) y guarda la referencia en
-- su fila. Devuelve cuántas sesiones revocó.
create function nucleo.desasociar_persona_de_mandato(
  mandato uuid, referencia text, motivo text, actor uuid, origen_pedido text)
  returns bigint
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    m nucleo.mandato;
    fila uuid;
    revocadas bigint;
  begin
    if c is null then
      raise exception 'sin consorcio en el ámbito' using errcode = '42501';
    end if;
    perform nucleo.exigir_actor_y_origen(actor, origen_pedido);
    if origen_pedido = 'api' and motivo is not null then
      raise exception 'el motivo de soporte no va por la API'
        using errcode = 'P0001', hint = 'origen_invalido';
    end if;
    if origen_pedido = 'soporte' and (motivo is null or btrim(motivo) = '') then
      raise exception 'por soporte hace falta el motivo de por qué no hubo cuatro ojos'
        using errcode = 'P0001', hint = 'motivo_requerido';
    end if;
    if referencia is null or length(btrim(referencia)) not between 1 and 300 then
      raise exception 'desasociar necesita la referencia del respaldo'
        using errcode = 'P0001', hint = 'respaldo_requerido';
    end if;
    select * into m from nucleo.mandato x where x.consorcio_id = c and x.id = mandato for update;
    if m.id is null then
      raise exception 'el mandato no existe en este consorcio'
        using errcode = 'P0001', hint = 'mandato_invalido';
    end if;
    if m.tipo <> 'administrador_externo' then
      raise exception 'solo se desasocia la cuenta de un administrador externo'
        using errcode = 'P0001', hint = 'mandato_no_asociable';
    end if;
    if m.persona_id is null then
      raise exception 'el mandato no tiene una cuenta asociada'
        using errcode = 'P0001', hint = 'mandato_sin_persona';
    end if;
    -- A4: nadie se desasocia a sí mismo por la API.
    if origen_pedido = 'api' and actor = m.persona_id then
      raise exception 'nadie se desasocia a sí mismo'
        using errcode = 'P0001', hint = 'desasociacion_de_si_mismo';
    end if;
    insert into nucleo.desasociacion_mandato
      (consorcio_id, mandato_id, persona_id, referencia, origen, actor, motivo_soporte)
    values (c, m.id, m.persona_id, btrim(referencia), origen_pedido, actor, btrim(motivo))
    returning id into fila;
    update nucleo.mandato set persona_id = null where id = m.id;
    revocadas := nucleo.revocar_sesiones_de(m.persona_id);
    insert into auditoria.eventos
      (consorcio_id, actor_persona_id, origen, accion, entidad, entidad_id, antes, despues,
       hash_previo, hash)
    values (c, actor, origen_pedido, 'consorcio.mandato.desasociar_persona', 'mandato', m.id,
            jsonb_build_object('persona_id', m.persona_id),
            jsonb_build_object('persona_id', null, 'desasociacion_id', fila,
                               'sesiones_revocadas', revocadas),
            '', '');
    return revocadas;
  end
  $$;

revoke all on function {FIRMAS}, {FIRMAS_INTERNAS} from public;
grant execute on function {FIRMAS} to cem_app;
"""

DOWNGRADE = rf"""
do $$
begin
  if exists (select 1 from nucleo.invitacion_mandato)
     or exists (select 1 from nucleo.desasociacion_mandato) then
    raise exception 'el downgrade de 0015 se niega: hay invitaciones o desasociaciones de mandato'
      using errcode = 'P0001', hint = 'downgrade_con_datos',
            detail = 'cada fila guarda la única copia de una referencia documental obligatoria';
  end if;
end
$$;
drop function {FIRMAS}, {FIRMAS_INTERNAS};
drop table nucleo.desasociacion_mandato;
drop table nucleo.invitacion_mandato;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
