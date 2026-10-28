"""H3, infraestructura común: la orden de pago versionada, sus aprobaciones por versión y las
barreras de la base, sin decidir quién aprueba ni cuántos.

Diseño: `doc/diseno/H3_Paquete_Decisiones_Economicas.md` del repositorio CEM, «Infraestructura
común a todas las opciones»; estados y reglas del documento 08 §6 («Política de egresos» y
diagrama 4) y RN12 (un cambio esencial invalida la aprobación).

**Qué existe después de esta migración.**
- `nucleo.orden_pago`: una fila por orden, con su estado (los del diagrama 4 del documento 08), la
  versión vigente, quién la preparó y el motivo si se rechazó.
- `nucleo.orden_pago_version`: cada versión de los datos esenciales (importe en centavos, moneda,
  concepto, beneficiario, comprobante y si usa el fondo de reserva) con su huella SHA-256. Solo se
  agrega: un trigger niega cualquier UPDATE o DELETE, también para el propietario de la tabla. Así
  se puede reconstruir qué se aprobó exactamente.
- `nucleo.orden_pago_aprobacion`: una aprobación es de una versión y de su huella, nunca de la
  orden en general. Una por identidad humana y por versión (unicidad `(orden, versión,
  identidad)`): la misma persona, con una o con dos cuentas, nunca suma dos sobre la misma versión,
  y sí puede aprobar la versión nueva después de un cambio. Solo se agrega.
- `nucleo.transicion_orden_pago`: las transiciones del diagrama 4. Un trigger niega cualquier
  cambio de estado que no esté en la tabla.

**Funciones** (`security definer`, solo por la API, con la persona de la sesión):
- `preparar_orden_pago`, `modificar_orden_pago` y `enviar_orden_a_revision`, con
  `egresos.preparar_orden`. Modificar crea una versión nueva con control optimista
  (`version_esperada`); si la orden estaba aprobada (o fallida), vuelve a revisión, y las
  aprobaciones de la versión anterior quedan como historia sin contar.
- `registrar_aprobacion_orden_pago` y `rechazar_orden_pago`, con `egresos.aprobar_orden`. La
  aprobación exige la versión vigente y la huella que vio quien aprueba. **No cambia el estado**:
  cuántas aprobaciones hacen falta es la decisión 2 del paquete H3, así que EnRevision → Aprobada
  no tiene camino todavía.
- `emitir_orden_pago` (Aprobada → EnEjecucion), con `egresos.ejecutar_pago`: quien emite no es
  ninguno de los que aprobaron la versión vigente (A5).

**Barreras comunes** (`exigir_acto_economico`), las de las políticas de egresos y R10:
- la facultad exacta y vigente (`tiene_facultad_vigente`);
- un modo que admite egresos: Administrador Aumentado o Autogestión (en Modo Consejo la
  plataforma no tiene egresos; el fondo de reserva tiene su propia política);
- la sesión de la persona, vigente, de menos de 12 horas y con el segundo factor verificado hace
  menos de 10 minutos (`VIGENCIA_SESION_ECONOMICA` y `VIGENCIA_SEGUNDO_FACTOR_RECIENTE`; una prueba
  compara los valores);
- la identidad humana verificada en el consorcio (A13-C).
La separación (quien aprueba o rechaza no preparó ninguna versión; quien emite no aprobó) usa
`exigir_separacion_humana`: compara seres humanos, no cuentas.

**Lo que no hace.** No asigna `egresos.aprobar_orden` a ningún paquete (decisión 7 del 21-10), no
fija umbral, franjas ni cantidad de aprobaciones, no conoce proveedores (el beneficiario es un
texto hasta el modelo de proveedor) y no mueve dinero. `cem_app` no recibe ningún permiso: no hay
rutas todavía. Cada acto escribe su evento en `auditoria.eventos` con el actor real; concepto,
beneficiario y motivo, que son texto libre, no van al evento.

Revisión: 0045
Anterior: 0044
"""

from alembic import op

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None

ESTADOS = (
    "borrador",
    "en_revision",
    "rechazada",
    "aprobada",
    "en_ejecucion",
    "en_verificacion",
    "pagada",
    "fallida",
    "conciliada",
)

# Diagrama 4 del documento 08 §6.
TRANSICIONES = (
    ("borrador", "en_revision"),
    ("en_revision", "rechazada"),
    ("en_revision", "aprobada"),
    ("aprobada", "en_ejecucion"),
    ("aprobada", "en_revision"),
    ("en_ejecucion", "en_verificacion"),
    ("en_verificacion", "pagada"),
    ("en_verificacion", "fallida"),
    ("en_ejecucion", "pagada"),
    ("en_ejecucion", "fallida"),
    ("fallida", "en_revision"),
    ("pagada", "conciliada"),
)

ESENCIALES = (
    "importe_nuevo bigint, moneda_nueva text, concepto_nuevo text, beneficiario_nuevo text,"
    " comprobante_nuevo text, usa_reserva boolean"
)
TIPOS_ESENCIALES = "bigint, text, text, text, text, boolean"

FUNCIONES = (
    f"nucleo.preparar_orden_pago({TIPOS_ESENCIALES}, uuid, text, uuid)",
    f"nucleo.modificar_orden_pago(uuid, integer, {TIPOS_ESENCIALES}, uuid, text, uuid)",
    "nucleo.enviar_orden_a_revision(uuid, integer, uuid, text, uuid)",
    "nucleo.registrar_aprobacion_orden_pago(uuid, integer, bytea, uuid, text, uuid)",
    "nucleo.rechazar_orden_pago(uuid, integer, text, uuid, text, uuid)",
    "nucleo.emitir_orden_pago(uuid, integer, uuid, text, uuid)",
)

INTERNAS = (
    "nucleo.exigir_acto_economico(uuid, uuid, uuid, text, text)",
    "nucleo.huella_orden_pago(uuid, integer, bigint, text, text, text, text, boolean)",
    "nucleo.orden_pago_bloqueada(uuid, uuid)",
    "nucleo.nueva_version_orden_pago(uuid, uuid, integer, bigint, text, text, text, text, boolean,"
    " uuid, uuid)",
    "nucleo.auditar_orden_pago(uuid, text, uuid, uuid, jsonb, jsonb)",
    "nucleo.exigir_transicion_orden_pago()",
    "nucleo.negar_cambio_historia_orden_pago()",
)

_estados = ", ".join(f"'{e}'" for e in ESTADOS)
_transiciones = ",\n  ".join(f"('{a}', '{b}')" for a, b in TRANSICIONES)

UPGRADE = f"""
create type nucleo.estado_orden_pago as enum ({_estados});

create table nucleo.transicion_orden_pago (
  desde  nucleo.estado_orden_pago not null,
  hacia  nucleo.estado_orden_pago not null,
  primary key (desde, hacia)
);
insert into nucleo.transicion_orden_pago (desde, hacia) values
  {_transiciones};

create table nucleo.orden_pago (
  consorcio_id     uuid not null references nucleo.consorcio (id),
  id               uuid not null default plataforma.uuid_v7(),
  estado           nucleo.estado_orden_pago not null default 'borrador',
  version_vigente  integer not null check (version_vigente > 0),
  preparada_por    uuid not null references nucleo.persona (id),
  creada_en        timestamptz not null default now(),
  actualizada_en   timestamptz not null default now(),
  -- Texto libre: no va a la auditoría.
  motivo_rechazo   text check (length(btrim(motivo_rechazo)) between 1 and 500),
  primary key (id),
  unique (consorcio_id, id),
  check ((estado = 'rechazada') = (motivo_rechazo is not null))
);

create table nucleo.orden_pago_version (
  consorcio_id       uuid not null,
  orden_id           uuid not null,
  numero             integer not null check (numero > 0),
  importe_centavos   bigint not null check (importe_centavos > 0),
  moneda             text not null check (moneda ~ '^[A-Z]{{3}}$'),
  concepto           text not null check (length(btrim(concepto)) between 1 and 300),
  -- Referencia del beneficiario hasta el modelo de proveedor (decisión 7 del paquete H3).
  beneficiario       text not null check (length(btrim(beneficiario)) between 1 and 200),
  comprobante        text check (length(btrim(comprobante)) between 1 and 200),
  usa_fondo_reserva  boolean not null,
  huella             bytea not null check (length(huella) = 32),
  preparada_por      uuid not null references nucleo.persona (id),
  -- La identidad humana de quien preparó, al preparar: la separación la compara aunque después
  -- se revincule la cuenta.
  identidad_preparo  uuid not null,
  creada_en          timestamptz not null default now(),
  primary key (orden_id, numero),
  foreign key (consorcio_id, identidad_preparo)
    references nucleo.identidad_humana (consorcio_id, id),
  unique (orden_id, numero, huella),
  foreign key (consorcio_id, orden_id) references nucleo.orden_pago (consorcio_id, id)
);

create table nucleo.orden_pago_aprobacion (
  consorcio_id  uuid not null,
  id            uuid not null default plataforma.uuid_v7(),
  orden_id      uuid not null,
  version       integer not null,
  huella        bytea not null,
  persona_id    uuid not null references nucleo.persona (id),
  identidad_id  uuid not null,
  creada_en     timestamptz not null default now(),
  primary key (id),
  -- La aprobación es de esa versión con esa huella: no se puede colgar de otra.
  foreign key (orden_id, version, huella)
    references nucleo.orden_pago_version (orden_id, numero, huella),
  foreign key (consorcio_id, orden_id) references nucleo.orden_pago (consorcio_id, id),
  foreign key (consorcio_id, identidad_id) references nucleo.identidad_humana (consorcio_id, id),
  unique (orden_id, version, identidad_id),
  unique (orden_id, version, persona_id)
);

alter table nucleo.orden_pago enable row level security;
alter table nucleo.orden_pago force row level security;
alter table nucleo.orden_pago_version enable row level security;
alter table nucleo.orden_pago_version force row level security;
alter table nucleo.orden_pago_aprobacion enable row level security;
alter table nucleo.orden_pago_aprobacion force row level security;
create policy por_consorcio on nucleo.orden_pago
  using (consorcio_id = plataforma.consorcio_actual())
  with check (consorcio_id = plataforma.consorcio_actual());
create policy por_consorcio on nucleo.orden_pago_version
  using (consorcio_id = plataforma.consorcio_actual())
  with check (consorcio_id = plataforma.consorcio_actual());
create policy por_consorcio on nucleo.orden_pago_aprobacion
  using (consorcio_id = plataforma.consorcio_actual())
  with check (consorcio_id = plataforma.consorcio_actual());

-- Solo transiciones del diagrama 4, y nada de lo que identifica a la orden cambia.
create function nucleo.exigir_transicion_orden_pago()
  returns trigger
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  begin
    if new.consorcio_id <> old.consorcio_id or new.id <> old.id
       or new.preparada_por <> old.preparada_por or new.creada_en <> old.creada_en
       or new.version_vigente < old.version_vigente then
      raise exception 'la identidad de la orden no cambia' using errcode = 'P0001',
        hint = 'orden_inmutable';
    end if;
    if new.estado <> old.estado and not exists (
         select 1 from nucleo.transicion_orden_pago t
          where t.desde = old.estado and t.hacia = new.estado) then
      raise exception 'la orden no pasa de % a %', old.estado, new.estado
        using errcode = 'P0001', hint = 'transicion_invalida';
    end if;
    return new;
  end
  $$;
create trigger transicion_orden_pago before update on nucleo.orden_pago
  for each row execute function nucleo.exigir_transicion_orden_pago();

create function nucleo.negar_cambio_historia_orden_pago()
  returns trigger
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  begin
    raise exception 'la historia de una orden de pago solo se agrega'
      using errcode = 'P0001', hint = 'historia_inmutable';
  end
  $$;
create trigger historia_orden_pago_version before update or delete
  on nucleo.orden_pago_version
  for each row execute function nucleo.negar_cambio_historia_orden_pago();
create trigger historia_orden_pago_aprobacion before update or delete
  on nucleo.orden_pago_aprobacion
  for each row execute function nucleo.negar_cambio_historia_orden_pago();
-- TRUNCATE no dispara los triggers por fila: también se niega.
create trigger historia_orden_pago_version_truncate before truncate
  on nucleo.orden_pago_version
  for each statement execute function nucleo.negar_cambio_historia_orden_pago();
create trigger historia_orden_pago_aprobacion_truncate before truncate
  on nucleo.orden_pago_aprobacion
  for each statement execute function nucleo.negar_cambio_historia_orden_pago();

-- R10 y las políticas de egresos. Devuelve la identidad humana de quien actúa.
create function nucleo.exigir_acto_economico(c uuid, actor uuid, sesion uuid, codigo text,
                                             origen_pedido text)
  returns uuid
  language plpgsql volatile
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    hoy date;
    s nucleo.sesion;
    identidad uuid;
  begin
    if c is null then
      raise exception 'sin consorcio en el ámbito' using errcode = '42501';
    end if;
    if origen_pedido is distinct from 'api' then
      raise exception 'origen inválido' using errcode = 'P0001', hint = 'origen_invalido';
    end if;
    hoy := nucleo.hoy_del_consorcio(c);
    if actor is null or actor is distinct from plataforma.persona_actual()
       or not nucleo.tiene_facultad_vigente(c, actor, codigo, hoy) then
      raise exception 'sin % en este consorcio', codigo using errcode = '42501';
    end if;
    if not exists (
         select 1 from nucleo.modo_operacion m
          where m.consorcio_id = c and m.vigente_desde <= hoy
            and (m.vigente_hasta is null or m.vigente_hasta > hoy)
            and m.modo in ('administrador_aumentado', 'autogestion')) then
      raise exception 'el modo vigente no admite egresos' using errcode = 'P0001',
        hint = 'modo_no_admitido';
    end if;
    select * into s from nucleo.sesion x
     where x.id = sesion and x.persona_id = actor and x.revocada_en is null
       and x.vence_en > now();
    if not found then
      raise exception 'la sesión no es válida' using errcode = 'P0001', hint = 'sesion_invalida';
    end if;
    if s.creada_en <= now() - interval '12 hours' then
      raise exception 'hace falta una sesión reciente' using errcode = 'P0001',
        hint = 'requiere_sesion_reciente';
    end if;
    if s.segundo_factor_en is null or s.segundo_factor_en <= now() - interval '10 minutes' then
      raise exception 'hace falta el segundo factor reciente' using errcode = 'P0001',
        hint = 'requiere_segundo_factor_reciente';
    end if;
    -- El vínculo queda bloqueado hasta el fin de la transacción: la identidad que se registra es
    -- la que se comprobó.
    perform 1 from nucleo.identidad_persona ip
     where ip.consorcio_id = c and ip.persona_id = actor and ip.desvinculada_en is null
       for share;
    identidad := nucleo.identidad_humana_de(c, actor);
    if identidad is null then
      raise exception 'hace falta la identidad humana verificada' using errcode = 'P0001',
        hint = 'identidad_no_verificada';
    end if;
    return identidad;
  end
  $$;

create function nucleo.huella_orden_pago(orden uuid, numero integer, importe bigint, moneda text,
                                         concepto text, beneficiario text, comprobante text,
                                         usa_reserva boolean)
  returns bytea
  language sql immutable
  set search_path = pg_catalog, pg_temp
  as $$
    select sha256(convert_to(jsonb_build_object(
      'orden', orden, 'version', numero, 'importe_centavos', importe, 'moneda', moneda,
      'concepto', concepto, 'beneficiario', beneficiario, 'comprobante', comprobante,
      'usa_fondo_reserva', usa_reserva)::text, 'UTF8'))
  $$;

-- La orden del consorcio, bloqueada para el resto de la transacción.
create function nucleo.orden_pago_bloqueada(c uuid, orden uuid)
  returns nucleo.orden_pago
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    o nucleo.orden_pago;
  begin
    select * into o from nucleo.orden_pago x where x.consorcio_id = c and x.id = orden
       for update;
    if not found then
      raise exception 'la orden no existe' using errcode = 'P0001', hint = 'no_encontrado';
    end if;
    return o;
  end
  $$;

create function nucleo.nueva_version_orden_pago(c uuid, orden uuid, numero integer,
                                                {ESENCIALES}, actor uuid, identidad uuid)
  returns bytea
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    h bytea := nucleo.huella_orden_pago(orden, numero, importe_nuevo, moneda_nueva,
                 btrim(concepto_nuevo), btrim(beneficiario_nuevo), btrim(comprobante_nuevo),
                 usa_reserva);
  begin
    insert into nucleo.orden_pago_version
      (consorcio_id, orden_id, numero, importe_centavos, moneda, concepto, beneficiario,
       comprobante, usa_fondo_reserva, huella, preparada_por, identidad_preparo)
    values (c, orden, numero, importe_nuevo, moneda_nueva, btrim(concepto_nuevo),
            btrim(beneficiario_nuevo), btrim(comprobante_nuevo), usa_reserva, h, actor,
            identidad);
    return h;
  end
  $$;

create function nucleo.auditar_orden_pago(c uuid, accion text, orden uuid, actor uuid,
                                          antes jsonb, despues jsonb)
  returns void
  language sql volatile
  set search_path = pg_catalog, pg_temp
  as $$
    insert into auditoria.eventos
      (consorcio_id, actor_persona_id, origen, accion, entidad, entidad_id, antes, despues,
       hash_previo, hash)
    values (c, actor, 'api', accion, 'orden_pago', orden, antes, despues, '', '')
  $$;

create function nucleo.preparar_orden_pago({ESENCIALES}, actor uuid, origen_pedido text,
                                           sesion uuid)
  returns uuid
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    o uuid := plataforma.uuid_v7();
    h bytea;
    identidad uuid;
  begin
    identidad := nucleo.exigir_acto_economico(c, actor, sesion, 'egresos.preparar_orden',
                                              origen_pedido);
    insert into nucleo.orden_pago (consorcio_id, id, version_vigente, preparada_por)
    values (c, o, 1, actor);
    h := nucleo.nueva_version_orden_pago(c, o, 1, importe_nuevo, moneda_nueva, concepto_nuevo,
           beneficiario_nuevo, comprobante_nuevo, usa_reserva, actor, identidad);
    perform nucleo.auditar_orden_pago(c, 'consorcio.orden_pago.preparar', o, actor, null,
      jsonb_build_object('estado', 'borrador', 'version', 1, 'huella', encode(h, 'hex'),
                         'importe_centavos', importe_nuevo, 'moneda', moneda_nueva,
                         'usa_fondo_reserva', usa_reserva));
    return o;
  end
  $$;

create function nucleo.modificar_orden_pago(orden uuid, version_esperada integer, {ESENCIALES},
                                            actor uuid, origen_pedido text, sesion uuid)
  returns integer
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    o nucleo.orden_pago;
    v nucleo.orden_pago_version;
    h bytea;
    estado_nuevo nucleo.estado_orden_pago;
    identidad uuid;
  begin
    identidad := nucleo.exigir_acto_economico(c, actor, sesion, 'egresos.preparar_orden',
                                              origen_pedido);
    o := nucleo.orden_pago_bloqueada(c, orden);
    if o.estado not in ('borrador', 'en_revision', 'aprobada', 'fallida') then
      raise exception 'la orden ya no se puede modificar' using errcode = 'P0001',
        hint = 'estado_invalido';
    end if;
    if version_esperada is distinct from o.version_vigente then
      raise exception 'la orden cambió mientras tanto' using errcode = 'P0001',
        hint = 'version_desactualizada';
    end if;
    select * into v from nucleo.orden_pago_version x
     where x.orden_id = orden and x.numero = o.version_vigente;
    if (v.importe_centavos, v.moneda, v.concepto, v.beneficiario, v.comprobante,
        v.usa_fondo_reserva)
       is not distinct from (importe_nuevo, moneda_nueva, btrim(concepto_nuevo),
                             btrim(beneficiario_nuevo), btrim(comprobante_nuevo), usa_reserva)
    then
      raise exception 'los datos esenciales no cambiaron' using errcode = 'P0001',
        hint = 'sin_cambios';
    end if;
    h := nucleo.nueva_version_orden_pago(c, orden, o.version_vigente + 1, importe_nuevo,
           moneda_nueva, concepto_nuevo, beneficiario_nuevo, comprobante_nuevo, usa_reserva,
           actor, identidad);
    estado_nuevo := case when o.estado = 'borrador' then 'borrador'::nucleo.estado_orden_pago
                         else 'en_revision'::nucleo.estado_orden_pago end;
    update nucleo.orden_pago
       set version_vigente = o.version_vigente + 1, estado = estado_nuevo, actualizada_en = now()
     where id = orden;
    perform nucleo.auditar_orden_pago(c, 'consorcio.orden_pago.modificar', orden, actor,
      jsonb_build_object('estado', o.estado, 'version', o.version_vigente),
      jsonb_build_object('estado', estado_nuevo, 'version', o.version_vigente + 1,
                         'huella', encode(h, 'hex'), 'importe_centavos', importe_nuevo,
                         'moneda', moneda_nueva, 'usa_fondo_reserva', usa_reserva));
    return o.version_vigente + 1;
  end
  $$;

create function nucleo.enviar_orden_a_revision(orden uuid, version_esperada integer, actor uuid,
                                               origen_pedido text, sesion uuid)
  returns void
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    o nucleo.orden_pago;
  begin
    perform nucleo.exigir_acto_economico(c, actor, sesion, 'egresos.preparar_orden',
                                         origen_pedido);
    o := nucleo.orden_pago_bloqueada(c, orden);
    if o.estado <> 'borrador' then
      raise exception 'solo un borrador va a revisión' using errcode = 'P0001',
        hint = 'estado_invalido';
    end if;
    if version_esperada is distinct from o.version_vigente then
      raise exception 'la orden cambió mientras tanto' using errcode = 'P0001',
        hint = 'version_desactualizada';
    end if;
    update nucleo.orden_pago set estado = 'en_revision', actualizada_en = now() where id = orden;
    perform nucleo.auditar_orden_pago(c, 'consorcio.orden_pago.enviar_a_revision', orden, actor,
      jsonb_build_object('estado', o.estado), jsonb_build_object('estado', 'en_revision',
                                                                 'version', o.version_vigente));
  end
  $$;

-- Quien decide sobre una orden en revisión: la versión vigente, nadie que haya preparado alguna
-- versión (comparando seres humanos: la identidad guardada al preparar y la actual).
create function nucleo.exigir_decision_sobre_orden(c uuid, o nucleo.orden_pago,
                                                   version_vista integer, actor uuid,
                                                   identidad uuid)
  returns void
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    p uuid;
  begin
    if o.estado <> 'en_revision' then
      raise exception 'la orden no está en revisión' using errcode = 'P0001',
        hint = 'estado_invalido';
    end if;
    if version_vista is distinct from o.version_vigente then
      raise exception 'esa versión ya no es la vigente' using errcode = 'P0001',
        hint = 'version_desactualizada';
    end if;
    if exists (select 1 from nucleo.orden_pago_version x
                where x.orden_id = o.id and x.identidad_preparo = identidad) then
      raise exception 'la misma persona no puede ocupar los dos lugares de esta separación'
        using errcode = 'P0001', hint = 'no_es_quien_preparo';
    end if;
    for p in select distinct x.preparada_por from nucleo.orden_pago_version x
              where x.orden_id = o.id loop
      perform nucleo.exigir_separacion_humana(c, actor, p, 'no_es_quien_preparo');
    end loop;
  end
  $$;

create function nucleo.registrar_aprobacion_orden_pago(orden uuid, version_vista integer,
                                                       huella_vista bytea, actor uuid,
                                                       origen_pedido text, sesion uuid)
  returns uuid
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    identidad uuid;
    o nucleo.orden_pago;
    a uuid;
  begin
    identidad := nucleo.exigir_acto_economico(c, actor, sesion, 'egresos.aprobar_orden',
                                              origen_pedido);
    o := nucleo.orden_pago_bloqueada(c, orden);
    perform nucleo.exigir_decision_sobre_orden(c, o, version_vista, actor, identidad);
    if not exists (select 1 from nucleo.orden_pago_version x
                    where x.orden_id = orden and x.numero = version_vista
                      and x.huella = huella_vista)
    then
      raise exception 'lo que se aprobó no es la versión vigente' using errcode = 'P0001',
        hint = 'huella_distinta';
    end if;
    if exists (select 1 from nucleo.orden_pago_aprobacion x
                where x.orden_id = orden and x.version = version_vista
                  and (x.identidad_id = identidad or x.persona_id = actor)) then
      raise exception 'esa persona ya aprobó esta versión' using errcode = 'P0001',
        hint = 'aprobacion_repetida';
    end if;
    insert into nucleo.orden_pago_aprobacion
      (consorcio_id, orden_id, version, huella, persona_id, identidad_id)
    values (c, orden, version_vista, huella_vista, actor, identidad)
    returning id into a;
    perform nucleo.auditar_orden_pago(c, 'consorcio.orden_pago.aprobar', orden, actor, null,
      jsonb_build_object('version', version_vista, 'huella', encode(huella_vista, 'hex')));
    return a;
  end
  $$;

create function nucleo.rechazar_orden_pago(orden uuid, version_vista integer, motivo text,
                                           actor uuid, origen_pedido text, sesion uuid)
  returns void
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    o nucleo.orden_pago;
    identidad uuid;
  begin
    identidad := nucleo.exigir_acto_economico(c, actor, sesion, 'egresos.aprobar_orden',
                                              origen_pedido);
    o := nucleo.orden_pago_bloqueada(c, orden);
    perform nucleo.exigir_decision_sobre_orden(c, o, version_vista, actor, identidad);
    update nucleo.orden_pago
       set estado = 'rechazada', motivo_rechazo = btrim(motivo), actualizada_en = now()
     where id = orden;
    perform nucleo.auditar_orden_pago(c, 'consorcio.orden_pago.rechazar', orden, actor,
      jsonb_build_object('estado', o.estado),
      jsonb_build_object('estado', 'rechazada', 'version', version_vista));
  end
  $$;

create function nucleo.emitir_orden_pago(orden uuid, version_vista integer, actor uuid,
                                         origen_pedido text, sesion uuid)
  returns void
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    o nucleo.orden_pago;
    p uuid;
    identidad uuid;
  begin
    identidad := nucleo.exigir_acto_economico(c, actor, sesion, 'egresos.ejecutar_pago',
                                              origen_pedido);
    o := nucleo.orden_pago_bloqueada(c, orden);
    if o.estado <> 'aprobada' then
      raise exception 'solo se emite una orden aprobada' using errcode = 'P0001',
        hint = 'estado_invalido';
    end if;
    if version_vista is distinct from o.version_vigente then
      raise exception 'esa versión ya no es la vigente' using errcode = 'P0001',
        hint = 'version_desactualizada';
    end if;
    -- A5: quien emite no es ninguno de los que aprobaron la versión vigente, ni por la identidad
    -- guardada al aprobar ni por la actual.
    if exists (select 1 from nucleo.orden_pago_aprobacion x
                where x.orden_id = orden and x.version = version_vista
                  and x.identidad_id = identidad) then
      raise exception 'la misma persona no puede ocupar los dos lugares de esta separación'
        using errcode = 'P0001', hint = 'no_es_quien_aprobo';
    end if;
    for p in select x.persona_id from nucleo.orden_pago_aprobacion x
              where x.orden_id = orden and x.version = version_vista loop
      perform nucleo.exigir_separacion_humana(c, actor, p, 'no_es_quien_aprobo');
    end loop;
    update nucleo.orden_pago set estado = 'en_ejecucion', actualizada_en = now()
     where id = orden;
    perform nucleo.auditar_orden_pago(c, 'consorcio.orden_pago.emitir', orden, actor,
      jsonb_build_object('estado', o.estado),
      jsonb_build_object('estado', 'en_ejecucion', 'version', version_vista));
  end
  $$;
"""

_todas = (
    FUNCIONES
    + INTERNAS
    + ("nucleo.exigir_decision_sobre_orden(uuid, nucleo.orden_pago, integer, uuid, uuid)",)
)
UPGRADE += "".join(f"revoke all on function {f} from public;\n" for f in _todas)

DOWNGRADE = (
    """
do $guarda$
begin
  if exists (select 1 from nucleo.orden_pago) then
    raise exception 'el downgrade de 0045 se niega: hay órdenes de pago'
      using errcode = 'P0001', hint = 'downgrade_con_datos',
            detail = 'bajar borraría las versiones y las aprobaciones, que la auditoría no guarda';
  end if;
end
$guarda$;
"""
    + "".join(f"drop function {f};\n" for f in FUNCIONES)
    + """
drop function nucleo.exigir_decision_sobre_orden(uuid, nucleo.orden_pago, integer, uuid,
                                                 uuid);
drop function nucleo.auditar_orden_pago(uuid, text, uuid, uuid, jsonb, jsonb);
drop function nucleo.nueva_version_orden_pago(uuid, uuid, integer, bigint, text, text, text, text,
                                              boolean, uuid, uuid);
drop function nucleo.orden_pago_bloqueada(uuid, uuid);
drop function nucleo.huella_orden_pago(uuid, integer, bigint, text, text, text, text, boolean);
drop function nucleo.exigir_acto_economico(uuid, uuid, uuid, text, text);
drop table nucleo.orden_pago_aprobacion;
drop table nucleo.orden_pago_version;
drop table nucleo.orden_pago;
drop function nucleo.negar_cambio_historia_orden_pago();
drop function nucleo.exigir_transicion_orden_pago();
drop table nucleo.transicion_orden_pago;
drop type nucleo.estado_orden_pago;
"""
)


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
