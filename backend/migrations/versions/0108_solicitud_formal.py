"""Solicitud formal de documentación al administrador (RF49, CU12, PA36, PG08; documento 14, §6.7,
H4a). Diseño: `doc/diseno/RF49_Solicitud_Formal.md` del repositorio CEM, con los valores por
defecto de P1 a P5 y el envío como constancia (observación de Codex en CEM #194).

**Los datos.** Siete tablas, una fila por hecho, sin cambios ni borrado
(`negar_cambio_historia_orden_pago`), con RLS forzada por consorcio y sin acceso directo de
`cem_app`:

- `nucleo.solicitud_formal`: número correlativo por consorcio, objeto con su referencia en texto,
  documentación pedida, plazo en días corridos (entre `PLAZO_MINIMO_DIAS` y `PLAZO_MAXIMO_DIAS`),
  quién la preparó (persona e identidad humana) y cuándo. Preparar no cuenta como firma (A5).
- `nucleo.solicitud_formal_firma`: una por firma, única por persona y por identidad humana.
- `nucleo.solicitud_formal_emision`: la segunda firma emite. Fija el destinatario (el único mandato
  vigente de administración, `nucleo.mandato_administracion_vigente`, A14), el día del consorcio,
  el texto de la plantilla PG08 y su huella SHA-256.
- `nucleo.solicitud_formal_envio`: la constancia del envío, desde la que corre el plazo
  (`vence_el` = fecha del envío + plazo). Si el destinatario tiene cuenta, se escribe en el mismo
  acto de emitir (medio `aplicacion`); si no, la registra el Consejo con el medio, la fecha (entre
  la emisión y hoy) y el comprobante adjunto. Mientras falta, la solicitud no vence.
- `nucleo.solicitud_formal_respuesta`: el texto, el medio (`aplicacion` si responde el
  destinatario; `medio_formal` si el Consejo registra lo que llegó de un destinatario sin cuenta),
  el día en que llegó y un adjunto opcional.
- `nucleo.solicitud_formal_retiro`: solo de un borrador, con motivo (P4).
- `nucleo.solicitud_formal_adjunto`: un archivo subido para el envío o para la respuesta de una
  solicitud, por quien puede hacer ese acto, antes de hacerlo. El acto lo cita; uno que no se
  usó queda como constancia de lo que se subió.

**El estado** (`nucleo.estado_solicitud_formal`, con el día del consorcio): `retirada`,
`respondida`, `borrador`, `envio_pendiente`, `vencida` (hoy pasó `vence_el`) o `enviada`. No hace
falta una tarea programada. Una respuesta después del vencimiento se acepta y queda «fuera de
plazo» (P5).

**Los actos** (`security definer`, solo `cem_app`): cada uno vuelve a exigir lo de la política. Los
del Consejo (`nucleo.exigir_acto_solicitud_formal`): la facultad vigente, un modo que lo admite
(`consejo` o `administrador_aumentado`, A3; `exigir_acto_sin_gesto` no sirve: es de egresos y
admite Autogestión), la sesión válida y la identidad humana verificada. Firmar bloquea la
solicitud: una segunda firma de la misma persona o de la misma identidad humana se niega
(`firma_repetida`, PA36, A13-C) y, sin administrador vigente, la segunda firma se niega antes de
escribir nada (`sin_administrador_vigente`). Responder lo puede solo la persona del mandato
destinatario: para cualquier otra, la solicitud no existe (`no_encontrado`).

**Adjuntos.** Comprobante del envío y adjunto de la respuesta: se suben antes del acto
(`nucleo.subir_adjunto_solicitud_formal`), porque el texto de la respuesta (hasta
`LARGO_RESPUESTA` caracteres) no entra en la dirección de una subida y la API no recibe
formularios de varias partes. Es un documento de «Otros» (categoría restringida, la ve quien tiene
`documentos.ver`) con su versión en cuarentena; la ruta encola el antivirus en la misma
transacción. Se escribe adentro de la función, no con `nueva_version`: quien lo sube no necesita la
facultad de carga, sino poder hacer el acto. Por lo mismo `fijar_version_objeto` acepta, sin la
facultad de carga, la versión que la misma persona subió como adjunto de una solicitud. El acto
cita un adjunto de esa solicitud, para ese acto, subido por quien lo hace, que el antivirus no
rechazó y que nadie usó (`adjunto_invalido`). El adjunto cita su documento
(`documento_citado_vigente`, `documento_referenciado`).

**Lectura** (`nucleo.solicitudes_formales_lectura`): quien tiene `fiscalizacion.ver_panel` ve
todas; cualquier otra persona, solo las dirigidas a su cuenta. El panel cuenta las abiertas
(`nucleo.panel_solicitudes_abiertas`).

**Exportación (`0069`):** `exportar_mandatos` suma las siete entidades, con el nombre de cada
persona y nunca su identificador.

Downgrade: se niega (`downgrade_con_datos`) si hay alguna solicitud. Sin eso, vuelve a las
definiciones anteriores.

Revisión: 0108
Anterior: 0107
"""

from alembic import op
from sqlalchemy import text

revision = "0108"
down_revision = "0107"
branch_labels = None
depends_on = None

PLAZO_MINIMO_DIAS = 5
PLAZO_MAXIMO_DIAS = 60
"""Los de `cem_contracts.fiscalizacion` (pregunta P1 del diseño). El valor por defecto (15) lo
pone el contrato: la base exige siempre el plazo."""
LARGO_REFERENCIA = 200
LARGO_DOCUMENTACION_PEDIDA = 2000
LARGO_RESPUESTA = 4000
LARGO_MOTIVO_RETIRO = 500
LARGO_DETALLE_ENVIO = 300
"""Los de `cem_contracts.fiscalizacion`: una migración no cambia después de aplicada."""
OBJETOS = {
    "periodo": "Período",
    "orden_pago": "Orden de pago",
    "contrato": "Contrato",
    "movimiento": "Movimiento",
    "otro": "Otro",
}
"""`ObjetoSolicitud` del contrato, con la palabra que lleva el texto emitido."""
MEDIOS_EXTERNOS = ("carta_documento", "correo_electronico", "entrega_en_mano", "otro")
MEDIOS_ENVIO = ("aplicacion", *MEDIOS_EXTERNOS)
"""`MedioEnvio` del contrato."""
MEDIOS_RESPUESTA = ("aplicacion", "medio_formal")
PARA = ("envio", "respuesta")
"""`ParaAdjunto` del contrato: el acto que va a citar el adjunto."""
ABIERTAS = ("borrador", "envio_pendiente", "enviada", "vencida")
"""`ABIERTAS` del contrato: lo que cuenta el panel."""
MODOS = ("consejo", "administrador_aumentado")
"""Los de las políticas `solicitud_formal.*` del Consejo (A3)."""
CATEGORIA_ADJUNTO = "otros"
"""Restringida: la ve solo quien tiene la facultad `documentos.ver`."""
FUNDAMENTO = (
    "artículo 2064, inciso b, del Código Civil y Comercial de la Nación y reglamento de"
    " propiedad horizontal"
)

TABLAS = (
    "nucleo.solicitud_formal",
    "nucleo.solicitud_formal_firma",
    "nucleo.solicitud_formal_emision",
    "nucleo.solicitud_formal_adjunto",
    "nucleo.solicitud_formal_envio",
    "nucleo.solicitud_formal_respuesta",
    "nucleo.solicitud_formal_retiro",
)
"""En el orden en que se crean: cada una después de las que cita."""
CITAN_DOCUMENTO = ("nucleo.solicitud_formal_adjunto",)
REFERENCIAS_NUEVAS = (("nucleo.solicitud_formal_adjunto", "documento_id"),)
"""Lo que esta migración suma a `REFERENCIAS` de `0089`: tablas que citan un documento."""


def _lista(valores) -> str:
    return ", ".join(f"'{v}'" for v in valores)


def _largo(columna: str, maximo: int) -> str:
    return f"length(btrim({columna})) between 1 and {maximo} and {columna} = btrim({columna})"


TABLAS_SQL = f"""
create table nucleo.solicitud_formal (
  consorcio_id        uuid not null references nucleo.consorcio (id),
  id                  uuid primary key default plataforma.uuid_v7(),
  numero              integer not null check (numero >= 1),
  objeto              text not null check (objeto in ({_lista(OBJETOS)})),
  referencia          text not null check ({_largo("referencia", LARGO_REFERENCIA)}),
  documentacion       text not null
    check ({_largo("documentacion", LARGO_DOCUMENTACION_PEDIDA)}),
  plazo_dias          smallint not null
    check (plazo_dias between {PLAZO_MINIMO_DIAS} and {PLAZO_MAXIMO_DIAS}),
  preparada_por       uuid not null references nucleo.persona (id),
  identidad_preparo   uuid not null,
  preparada_en        timestamptz not null default now(),
  unique (consorcio_id, numero),
  unique (consorcio_id, id),
  foreign key (consorcio_id, identidad_preparo)
    references nucleo.identidad_humana (consorcio_id, id)
);

create table nucleo.solicitud_formal_firma (
  consorcio_id   uuid not null,
  id             uuid primary key default plataforma.uuid_v7(),
  solicitud_id   uuid not null,
  persona_id     uuid not null references nucleo.persona (id),
  identidad_id   uuid not null,
  firmada_en     timestamptz not null default now(),
  -- PA36, A13-C: dos firmas de personas y de seres humanos distintos.
  unique (solicitud_id, persona_id),
  unique (solicitud_id, identidad_id),
  foreign key (consorcio_id, solicitud_id) references nucleo.solicitud_formal (consorcio_id, id),
  foreign key (consorcio_id, identidad_id) references nucleo.identidad_humana (consorcio_id, id)
);
create index solicitud_formal_firma_solicitud
  on nucleo.solicitud_formal_firma (consorcio_id, solicitud_id);

create table nucleo.solicitud_formal_emision (
  consorcio_id   uuid not null,
  solicitud_id   uuid primary key,
  mandato_id     uuid not null,
  emitida_en     timestamptz not null default now(),
  emitida_el     date not null,
  texto          text not null check (length(texto) > 0),
  huella         text not null check (huella ~ '^[0-9a-f]{{64}}$'),
  unique (consorcio_id, solicitud_id),
  foreign key (consorcio_id, solicitud_id) references nucleo.solicitud_formal (consorcio_id, id),
  foreign key (consorcio_id, mandato_id) references nucleo.mandato (consorcio_id, id)
);
create index solicitud_formal_emision_mandato
  on nucleo.solicitud_formal_emision (consorcio_id, mandato_id);

create table nucleo.solicitud_formal_adjunto (
  consorcio_id          uuid not null,
  id                    uuid primary key default plataforma.uuid_v7(),
  solicitud_id          uuid not null,
  para                  text not null check (para in ({_lista(PARA)})),
  documento_id          uuid not null,
  version_documento_id  uuid not null unique references nucleo.version_documento (id),
  subido_por            uuid not null references nucleo.persona (id),
  subido_en             timestamptz not null default now(),
  unique (consorcio_id, id),
  foreign key (consorcio_id, solicitud_id) references nucleo.solicitud_formal (consorcio_id, id),
  foreign key (consorcio_id, documento_id) references nucleo.documento (consorcio_id, id)
);
create index solicitud_formal_adjunto_solicitud
  on nucleo.solicitud_formal_adjunto (consorcio_id, solicitud_id);
create index solicitud_formal_adjunto_documento
  on nucleo.solicitud_formal_adjunto (consorcio_id, documento_id);

create table nucleo.solicitud_formal_envio (
  consorcio_id          uuid not null,
  solicitud_id          uuid primary key,
  medio                 text not null check (medio in ({_lista(MEDIOS_ENVIO)})),
  detalle               text check (detalle is null or ({_largo("detalle", LARGO_DETALLE_ENVIO)})),
  fecha_envio           date not null,
  vence_el              date not null check (vence_el > fecha_envio),
  registrado_por        uuid not null references nucleo.persona (id),
  identidad_registro    uuid not null,
  registrado_en         timestamptz not null default now(),
  adjunto_id            uuid unique,
  -- El comprobante es obligatorio en el medio externo y no existe en la aplicación.
  check ((medio = 'aplicacion') = (adjunto_id is null)),
  check (medio <> 'aplicacion' or detalle is null),
  unique (consorcio_id, solicitud_id),
  foreign key (consorcio_id, solicitud_id)
    references nucleo.solicitud_formal_emision (consorcio_id, solicitud_id),
  foreign key (consorcio_id, adjunto_id)
    references nucleo.solicitud_formal_adjunto (consorcio_id, id),
  foreign key (consorcio_id, identidad_registro)
    references nucleo.identidad_humana (consorcio_id, id)
);

create table nucleo.solicitud_formal_respuesta (
  consorcio_id          uuid not null,
  solicitud_id          uuid primary key,
  texto                 text not null check ({_largo("texto", LARGO_RESPUESTA)}),
  medio                 text not null check (medio in ({_lista(MEDIOS_RESPUESTA)})),
  respondida_el         date not null,
  registrada_por        uuid not null references nucleo.persona (id),
  -- El destinatario puede no tener la identidad verificada; el Consejo, siempre.
  identidad_registro    uuid,
  registrada_en         timestamptz not null default now(),
  adjunto_id            uuid unique,
  check (medio = 'aplicacion' or identidad_registro is not null),
  foreign key (consorcio_id, solicitud_id)
    references nucleo.solicitud_formal_envio (consorcio_id, solicitud_id),
  foreign key (consorcio_id, adjunto_id)
    references nucleo.solicitud_formal_adjunto (consorcio_id, id),
  foreign key (consorcio_id, identidad_registro)
    references nucleo.identidad_humana (consorcio_id, id)
);

create table nucleo.solicitud_formal_retiro (
  consorcio_id      uuid not null,
  solicitud_id      uuid primary key,
  motivo            text not null check ({_largo("motivo", LARGO_MOTIVO_RETIRO)}),
  retirada_por      uuid not null references nucleo.persona (id),
  identidad_retiro  uuid not null,
  retirada_en       timestamptz not null default now(),
  foreign key (consorcio_id, solicitud_id) references nucleo.solicitud_formal (consorcio_id, id),
  foreign key (consorcio_id, identidad_retiro)
    references nucleo.identidad_humana (consorcio_id, id)
);
"""


def _protecciones(tabla: str) -> str:
    corto = tabla.removeprefix("nucleo.")
    sql = f"""
alter table {tabla} enable row level security;
alter table {tabla} force row level security;
create policy por_consorcio on {tabla} for select
  using (consorcio_id = plataforma.consorcio_actual());
revoke all on {tabla} from public, cem_app;
create trigger historia_{corto} before update or delete on {tabla}
  for each row execute function nucleo.negar_cambio_historia_orden_pago();
create trigger historia_{corto}_truncate before truncate on {tabla}
  for each statement execute function nucleo.negar_cambio_historia_orden_pago();
"""
    if tabla in CITAN_DOCUMENTO:
        sql += f"""
create trigger documento_citado_vigente before insert or update of documento_id on {tabla}
  for each row execute function nucleo.documento_citado_vigente('documento_id');
"""
    return sql


_objetos = "\n".join(f"             when '{k}' then '{v}'" for k, v in OBJETOS.items())

FUNCIONES = f"""
-- A14: el único mandato vigente de administración del consorcio ese día (la exclusión
-- `mandato_administracion_unica` impide que haya dos); nulo si no hay.
create function nucleo.mandato_administracion_vigente(c uuid, dia date)
  returns uuid
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select m.id from nucleo.mandato m
     where m.consorcio_id = c and m.tipo in ('administrador', 'administrador_externo')
       and nucleo.mandato_vigente(m.id, dia)
     order by m.vigente_desde desc, m.id
     limit 1
  $$;

create function nucleo.auditar_solicitud_formal(c uuid, accion text, solicitud uuid, actor uuid,
                                                despues jsonb)
  returns void
  language sql volatile
  set search_path = pg_catalog, pg_temp
  as $$
    insert into auditoria.eventos
      (consorcio_id, actor_persona_id, origen, accion, entidad, entidad_id, despues,
       hash_previo, hash)
    values (c, actor, 'api', accion, 'solicitud_formal', solicitud, despues, '', '')
  $$;

-- Quien pide es la persona de la sesión, con una sesión válida y por la API. Sin facultad ni modo:
-- la usan la lectura y la respuesta del destinatario.
create function nucleo.exigir_sesion_solicitud_formal(c uuid, actor uuid, sesion uuid,
                                                      origen_pedido text)
  returns void
  language plpgsql stable
  set search_path = pg_catalog, pg_temp
  as $$
  begin
    if c is null then
      raise exception 'sin consorcio en el ámbito' using errcode = '42501';
    end if;
    if origen_pedido is distinct from 'api' then
      raise exception 'origen inválido' using errcode = 'P0001', hint = 'origen_invalido';
    end if;
    if actor is null or actor is distinct from plataforma.persona_actual() then
      raise exception 'la persona no es la de la petición' using errcode = '42501';
    end if;
    if not exists (select 1 from nucleo.sesion x
                    where x.id = sesion and x.persona_id = actor and x.revocada_en is null
                      and x.vence_en > now()) then
      raise exception 'la sesión no es válida' using errcode = 'P0001', hint = 'sesion_invalida';
    end if;
  end
  $$;

-- Los actos del Consejo: la facultad vigente, un modo que los admite (A3), la sesión y la
-- identidad humana verificada, que devuelve.
create function nucleo.exigir_acto_solicitud_formal(c uuid, actor uuid, sesion uuid, codigo text,
                                                    origen_pedido text)
  returns uuid
  language plpgsql volatile
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    hoy date;
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
    -- El candado del modo de `0090`: un cambio de modo en curso termina antes de leerlo.
    perform pg_advisory_xact_lock_shared(hashtextextended('modo:' || c::text, 0));
    if not exists (
         select 1 from nucleo.modo_operacion m
          where m.consorcio_id = c and m.vigente_desde <= hoy
            and (m.vigente_hasta is null or m.vigente_hasta > hoy)
            and m.modo in ({_lista(MODOS)})) then
      raise exception 'el modo vigente no admite solicitudes formales' using errcode = 'P0001',
        hint = 'modo_no_admitido';
    end if;
    perform nucleo.exigir_sesion_solicitud_formal(c, actor, sesion, origen_pedido);
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

create function nucleo.estado_solicitud_formal(c uuid, solicitud uuid)
  returns text
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select case
             when exists (select 1 from nucleo.solicitud_formal_retiro r
                           where r.consorcio_id = c and r.solicitud_id = s.id) then 'retirada'
             when exists (select 1 from nucleo.solicitud_formal_respuesta r
                           where r.consorcio_id = c and r.solicitud_id = s.id) then 'respondida'
             when not exists (select 1 from nucleo.solicitud_formal_emision e
                               where e.consorcio_id = c and e.solicitud_id = s.id)
               then 'borrador'
             when n.solicitud_id is null then 'envio_pendiente'
             when n.vence_el < nucleo.hoy_del_consorcio(c) then 'vencida'
             else 'enviada'
           end
      from nucleo.solicitud_formal s
      left join nucleo.solicitud_formal_envio n
        on n.consorcio_id = s.consorcio_id and n.solicitud_id = s.id
     where s.consorcio_id = c and s.id = solicitud
  $$;

-- Bloqueada hasta el fin de la transacción: dos firmas, o una firma y un retiro, a la vez se ven
-- una a la otra.
create function nucleo.solicitud_formal_bloqueada(c uuid, solicitud uuid)
  returns nucleo.solicitud_formal
  language plpgsql volatile
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    s nucleo.solicitud_formal;
  begin
    select * into s from nucleo.solicitud_formal x
     where x.consorcio_id = c and x.id = solicitud
       for update;
    if not found then
      raise exception 'la solicitud no existe' using errcode = 'P0001', hint = 'no_encontrado';
    end if;
    return s;
  end
  $$;

-- El nombre del mandatario como lo muestra el mandato (`exportar_mandatos`).
create function nucleo.nombre_del_mandato(m nucleo.mandato)
  returns text
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select case when m.tipo = 'administrador_externo'
                then coalesce(m.mandatario_nombre, pe.nombre)
                else coalesce(pe.nombre, m.mandatario_nombre) end
      from (select 1) uno
      left join nucleo.persona pe on pe.id = m.persona_id
  $$;

-- La plantilla PG08: consorcio, firmantes, destinatario, objeto, fundamento, documentación
-- pedida, plazo y medio de respuesta. Tono informativo, sin calificaciones (RN33).
create function nucleo.texto_solicitud_formal(c uuid, s nucleo.solicitud_formal,
                                              m nucleo.mandato, dia date)
  returns text
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select format(
      concat_ws(E'\\n',
        'Solicitud formal de documentación N.º %s',
        'Consorcio: %s',
        'Destinatario: %s (administración del consorcio)',
        'Fecha de emisión: %s',
        '',
        'El Consejo de Propietarios solicita la documentación o aclaración que se detalla, con'
        ' fundamento en el {FUNDAMENTO}.',
        '',
        'Objeto: %s. %s',
        '',
        'Documentación solicitada:',
        '%s',
        '',
        'Plazo: %s días corridos desde el envío de esta solicitud.',
        'Medio de respuesta: %s',
        '',
        'Firmantes: %s'),
      s.numero, co.nombre, nucleo.nombre_del_mandato(m), to_char(dia, 'DD/MM/YYYY'),
      case s.objeto
{_objetos}
      end,
      s.referencia, s.documentacion, s.plazo_dias,
      case when m.persona_id is not null
           then 'en la aplicación Consorcio en Mano, donde la solicitud queda disponible.'
           else 'por escrito, dirigida al Consejo de Propietarios.' end,
      (select string_agg(p.nombre, ', ' order by f.firmada_en, f.id)
         from nucleo.solicitud_formal_firma f
         join nucleo.persona p on p.id = f.persona_id
        where f.consorcio_id = c and f.solicitud_id = s.id))
      from nucleo.consorcio co
     where co.id = c
  $$;

-- Quien puede hacer ahora el acto que va a citar el adjunto: el envío (el Consejo, con la
-- solicitud con el envío pendiente) o la respuesta (el destinatario con cuenta, o el Consejo si
-- no la tiene, con la solicitud enviada o vencida). Devuelve la identidad humana de quien actúa
-- (nula solo para el destinatario sin la identidad verificada).
create function nucleo.exigir_acto_con_adjunto(c uuid, s nucleo.solicitud_formal, para_nuevo text,
                                               actor uuid, sesion uuid, origen_pedido text)
  returns uuid
  language plpgsql volatile
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    destinatario uuid;
    estado text := nucleo.estado_solicitud_formal(c, s.id);
  begin
    select m.persona_id into destinatario
      from nucleo.solicitud_formal_emision e
      join nucleo.mandato m on m.consorcio_id = e.consorcio_id and m.id = e.mandato_id
     where e.consorcio_id = c and e.solicitud_id = s.id;
    if para_nuevo = 'respuesta' and destinatario is not null then
      perform nucleo.exigir_sesion_solicitud_formal(c, actor, sesion, origen_pedido);
      -- La concede ser la persona del mandato destinatario: para cualquier otra, no existe.
      if destinatario is distinct from actor then
        raise exception 'la solicitud no existe' using errcode = 'P0001', hint = 'no_encontrado';
      end if;
      if estado not in ('enviada', 'vencida') then
        raise exception 'la solicitud no espera respuesta' using errcode = 'P0001',
          hint = 'solicitud_no_admite';
      end if;
      return nucleo.identidad_humana_de(c, actor);
    end if;
    if para_nuevo = 'envio' then
      if estado <> 'envio_pendiente' then
        raise exception 'el envío no está pendiente' using errcode = 'P0001',
          hint = 'solicitud_no_admite';
      end if;
    elsif para_nuevo = 'respuesta' then
      if estado not in ('enviada', 'vencida') then
        raise exception 'la solicitud no espera respuesta' using errcode = 'P0001',
          hint = 'solicitud_no_admite';
      end if;
    else
      raise exception 'el adjunto es para el envío o para la respuesta' using errcode = 'P0001',
        hint = 'adjunto_invalido';
    end if;
    return nucleo.exigir_acto_solicitud_formal(c, actor, sesion, 'solicitud_formal.firmar',
                                               origen_pedido);
  end
  $$;

-- Un documento de «Otros» con su versión en cuarentena, subida por quien va a hacer el acto. La
-- ruta guarda el objeto y encola el antivirus en la misma transacción.
create function nucleo.subir_adjunto_solicitud_formal(solicitud uuid, para_nuevo text,
                                                      titulo_nuevo text, tipo_archivo text,
                                                      tamano_nuevo integer, sha256_nuevo text,
                                                      actor uuid, origen_pedido text,
                                                      sesion uuid)
  returns table (nueva_version_id uuid, nueva_clave_objeto text, nuevo_documento_id uuid,
                 nuevo_adjunto_id uuid)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    s nucleo.solicitud_formal;
    d uuid;
    v uuid := plataforma.uuid_v7();
    k text;
    a uuid;
    hoy date;
  begin
    if c is null then
      raise exception 'sin consorcio en el ámbito' using errcode = '42501';
    end if;
    s := nucleo.solicitud_formal_bloqueada(c, solicitud);
    perform nucleo.exigir_acto_con_adjunto(c, s, para_nuevo, actor, sesion, origen_pedido);
    hoy := nucleo.hoy_del_consorcio(c);
    insert into nucleo.documento (consorcio_id, categoria, titulo, fecha, cargado_por)
    values (c, '{CATEGORIA_ADJUNTO}', btrim(titulo_nuevo), hoy, actor)
    returning id into d;
    -- El título no va a la auditoría: es texto libre.
    perform nucleo.auditar_documento(c, 'consorcio.documento.crear', 'documento', d, actor,
      origen_pedido, jsonb_build_object('categoria', '{CATEGORIA_ADJUNTO}', 'fecha', hoy));
    k := 'cem/' || c || '/' || d || '/' || v;
    insert into nucleo.version_documento
      (consorcio_id, documento_id, id, tipo, tamano, sha256, clave_objeto, subida_por)
    values (c, d, v, tipo_archivo, tamano_nuevo, sha256_nuevo, k, actor);
    perform nucleo.auditar_documento(c, 'consorcio.documento.cargar_version', 'version_documento',
      v, actor, origen_pedido, jsonb_build_object('tipo', tipo_archivo, 'tamano', tamano_nuevo));
    insert into nucleo.solicitud_formal_adjunto
      (consorcio_id, solicitud_id, para, documento_id, version_documento_id, subido_por)
    values (c, s.id, para_nuevo, d, v, actor)
    returning id into a;
    perform nucleo.auditar_solicitud_formal(c, 'consorcio.solicitud_formal.adjuntar', s.id, actor,
      jsonb_build_object('numero', s.numero, 'para', para_nuevo, 'adjunto_id', a,
                         'documento_id', d));
    return query select v, k, d, a;
  end
  $$;

-- El adjunto que cita un acto: de esa solicitud, para ese acto, subido por quien lo hace, de un
-- documento vigente, que el antivirus no rechazó y que ningún acto usó. Nulo, sin adjunto.
create function nucleo.exigir_adjunto_solicitud_formal(c uuid, solicitud uuid, adjunto uuid,
                                                       para_nuevo text, actor uuid)
  returns void
  language plpgsql stable
  set search_path = pg_catalog, pg_temp
  as $$
  begin
    if adjunto is null then
      return;
    end if;
    if not exists (
         select 1 from nucleo.solicitud_formal_adjunto a
           join nucleo.version_documento x on x.id = a.version_documento_id
           join nucleo.documento d on d.consorcio_id = a.consorcio_id and d.id = a.documento_id
          where a.consorcio_id = c and a.id = adjunto and a.solicitud_id = solicitud
            and a.para = para_nuevo and a.subido_por = actor
            and x.estado <> 'rechazado' and d.estado = 'vigente')
       or exists (select 1 from nucleo.solicitud_formal_envio n
                   where n.consorcio_id = c and n.adjunto_id = adjunto)
       or exists (select 1 from nucleo.solicitud_formal_respuesta r
                   where r.consorcio_id = c and r.adjunto_id = adjunto) then
      raise exception 'el adjunto no es uno de esta solicitud para este acto, sin usar'
        using errcode = 'P0001', hint = 'adjunto_invalido';
    end if;
  end
  $$;

-- Si esa versión la subió esa persona como adjunto de una solicitud (para `fijar_version_objeto`).
create function nucleo.adjunto_de_solicitud_formal(c uuid, version uuid, actor uuid)
  returns boolean
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select exists (select 1 from nucleo.solicitud_formal_adjunto a
                    where a.consorcio_id = c and a.version_documento_id = version
                      and a.subido_por = actor)
  $$;

create function nucleo.preparar_solicitud_formal(objeto_nuevo text, referencia_nueva text,
                                                 documentacion_nueva text, plazo integer,
                                                 actor uuid, origen_pedido text, sesion uuid)
  returns uuid
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    identidad uuid;
    n integer;
    s uuid;
  begin
    identidad := nucleo.exigir_acto_solicitud_formal(c, actor, sesion,
                                                     'solicitud_formal.preparar', origen_pedido);
    -- El número es correlativo por consorcio: dos preparaciones a la vez no toman el mismo.
    perform pg_advisory_xact_lock(hashtextextended('solicitud_formal:' || c::text, 0));
    select coalesce(max(x.numero), 0) + 1 into n from nucleo.solicitud_formal x
     where x.consorcio_id = c;
    insert into nucleo.solicitud_formal
      (consorcio_id, numero, objeto, referencia, documentacion, plazo_dias, preparada_por,
       identidad_preparo)
    values (c, n, objeto_nuevo, btrim(referencia_nueva), btrim(documentacion_nueva), plazo, actor,
            identidad)
    returning id into s;
    -- Sin la referencia ni la documentación: son texto libre.
    perform nucleo.auditar_solicitud_formal(c, 'consorcio.solicitud_formal.preparar', s, actor,
      jsonb_build_object('numero', n, 'objeto', objeto_nuevo, 'plazo_dias', plazo));
    return s;
  end
  $$;

create function nucleo.firmar_solicitud_formal(solicitud uuid, actor uuid, origen_pedido text,
                                               sesion uuid)
  returns table (emitida boolean, correo_destinatario text, consorcio_nombre text,
                 vence date)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    identidad uuid;
    s nucleo.solicitud_formal;
    m nucleo.mandato;
    p uuid;
    previas integer;
    hoy date;
    t text;
    h text;
  begin
    identidad := nucleo.exigir_acto_solicitud_formal(c, actor, sesion, 'solicitud_formal.firmar',
                                                     origen_pedido);
    s := nucleo.solicitud_formal_bloqueada(c, solicitud);
    if nucleo.estado_solicitud_formal(c, s.id) <> 'borrador' then
      raise exception 'solo se firma un borrador' using errcode = 'P0001',
        hint = 'solicitud_no_admite';
    end if;
    -- PA36, A13-C: ni la misma cuenta ni otra cuenta del mismo ser humano.
    if exists (select 1 from nucleo.solicitud_formal_firma f
                where f.consorcio_id = c and f.solicitud_id = s.id
                  and (f.persona_id = actor or f.identidad_id = identidad)) then
      raise exception 'esa persona ya firmó la solicitud' using errcode = 'P0001',
        hint = 'firma_repetida';
    end if;
    for p in select f.persona_id from nucleo.solicitud_formal_firma f
              where f.consorcio_id = c and f.solicitud_id = s.id loop
      perform nucleo.exigir_separacion_humana(c, actor, p, 'firma_repetida');
    end loop;
    select count(*) into previas from nucleo.solicitud_formal_firma f
     where f.consorcio_id = c and f.solicitud_id = s.id;
    hoy := nucleo.hoy_del_consorcio(c);
    if previas = 1 then
      -- A14: la segunda firma emite y necesita el destinatario; sin él no se escribe nada.
      select * into m from nucleo.mandato x
       where x.consorcio_id = c and x.id = nucleo.mandato_administracion_vigente(c, hoy);
      if m.id is null then
        raise exception 'no hay un administrador vigente' using errcode = 'P0001',
          hint = 'sin_administrador_vigente';
      end if;
    end if;
    insert into nucleo.solicitud_formal_firma (consorcio_id, solicitud_id, persona_id, identidad_id)
    values (c, s.id, actor, identidad);
    perform nucleo.auditar_solicitud_formal(c, 'consorcio.solicitud_formal.firmar', s.id, actor,
      jsonb_build_object('numero', s.numero, 'firma', previas + 1));
    if previas = 0 then
      return query select false, null::text, null::text, null::date;
      return;
    end if;
    t := nucleo.texto_solicitud_formal(c, s, m, hoy);
    h := encode(sha256(convert_to(t, 'UTF8')), 'hex');
    insert into nucleo.solicitud_formal_emision
      (consorcio_id, solicitud_id, mandato_id, emitida_el, texto, huella)
    values (c, s.id, m.id, hoy, t, h);
    perform nucleo.auditar_solicitud_formal(c, 'consorcio.solicitud_formal.emitir', s.id, actor,
      jsonb_build_object('numero', s.numero, 'mandato_id', m.id, 'huella', h,
                         'destinatario_con_cuenta', m.persona_id is not null));
    if m.persona_id is null then
      -- Sin cuenta: el Consejo registra el envío por el medio externo; mientras tanto no vence.
      return query select true, null::text, null::text, null::date;
      return;
    end if;
    -- Con cuenta: la solicitud le aparece en la aplicación ahora, y desde hoy corre el plazo.
    insert into nucleo.solicitud_formal_envio
      (consorcio_id, solicitud_id, medio, fecha_envio, vence_el, registrado_por,
       identidad_registro)
    values (c, s.id, 'aplicacion', hoy, hoy + s.plazo_dias, actor, identidad);
    perform nucleo.auditar_solicitud_formal(c, 'consorcio.solicitud_formal.enviar', s.id, actor,
      jsonb_build_object('numero', s.numero, 'medio', 'aplicacion', 'fecha_envio', hoy,
                         'vence_el', hoy + s.plazo_dias));
    return query
      select true, pe.correo, co.nombre, hoy + s.plazo_dias
        from nucleo.persona pe, nucleo.consorcio co
       where pe.id = m.persona_id and co.id = c;
  end
  $$;

create function nucleo.retirar_solicitud_formal(solicitud uuid, motivo_nuevo text, actor uuid,
                                                origen_pedido text, sesion uuid)
  returns void
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    identidad uuid;
    s nucleo.solicitud_formal;
  begin
    identidad := nucleo.exigir_acto_solicitud_formal(c, actor, sesion, 'solicitud_formal.firmar',
                                                     origen_pedido);
    s := nucleo.solicitud_formal_bloqueada(c, solicitud);
    -- P4: una emitida ya no se retira.
    if nucleo.estado_solicitud_formal(c, s.id) <> 'borrador' then
      raise exception 'solo se retira un borrador' using errcode = 'P0001',
        hint = 'solicitud_no_admite';
    end if;
    insert into nucleo.solicitud_formal_retiro
      (consorcio_id, solicitud_id, motivo, retirada_por, identidad_retiro)
    values (c, s.id, btrim(motivo_nuevo), actor, identidad);
    -- Sin el motivo: es texto libre.
    perform nucleo.auditar_solicitud_formal(c, 'consorcio.solicitud_formal.retirar', s.id, actor,
      jsonb_build_object('numero', s.numero));
  end
  $$;

create function nucleo.registrar_envio_solicitud_formal(solicitud uuid, medio_nuevo text,
                                                        detalle_nuevo text, fecha date,
                                                        adjunto uuid, actor uuid,
                                                        origen_pedido text, sesion uuid)
  returns void
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    identidad uuid;
    s nucleo.solicitud_formal;
    emitida date;
  begin
    identidad := nucleo.exigir_acto_solicitud_formal(c, actor, sesion, 'solicitud_formal.firmar',
                                                     origen_pedido);
    s := nucleo.solicitud_formal_bloqueada(c, solicitud);
    -- Una sola vez, y solo la de un destinatario sin cuenta: la otra se envía al emitir.
    if nucleo.estado_solicitud_formal(c, s.id) <> 'envio_pendiente' then
      raise exception 'el envío no está pendiente' using errcode = 'P0001',
        hint = 'solicitud_no_admite';
    end if;
    if medio_nuevo is null or medio_nuevo not in ({_lista(MEDIOS_EXTERNOS)}) then
      raise exception 'el medio del envío no es uno externo' using errcode = 'P0001',
        hint = 'medio_invalido';
    end if;
    select e.emitida_el into emitida from nucleo.solicitud_formal_emision e
     where e.consorcio_id = c and e.solicitud_id = s.id;
    if fecha is null or fecha < emitida or fecha > nucleo.hoy_del_consorcio(c) then
      raise exception 'la fecha del envío va entre la emisión y hoy' using errcode = 'P0001',
        hint = 'fecha_envio_invalida';
    end if;
    -- El comprobante es obligatorio en el medio externo.
    if adjunto is null then
      raise exception 'falta el comprobante del envío' using errcode = 'P0001',
        hint = 'adjunto_invalido';
    end if;
    perform nucleo.exigir_adjunto_solicitud_formal(c, s.id, adjunto, 'envio', actor);
    insert into nucleo.solicitud_formal_envio
      (consorcio_id, solicitud_id, medio, detalle, fecha_envio, vence_el, registrado_por,
       identidad_registro, adjunto_id)
    values (c, s.id, medio_nuevo, nullif(btrim(detalle_nuevo), ''), fecha, fecha + s.plazo_dias,
            actor, identidad, adjunto);
    -- Sin el detalle: es texto libre.
    perform nucleo.auditar_solicitud_formal(c, 'consorcio.solicitud_formal.enviar', s.id, actor,
      jsonb_build_object('numero', s.numero, 'medio', medio_nuevo, 'fecha_envio', fecha,
                         'vence_el', fecha + s.plazo_dias, 'adjunto_id', adjunto));
  end
  $$;

-- Lo común de las dos respuestas, ya autorizadas y con la solicitud bloqueada.
create function nucleo.guardar_respuesta_solicitud_formal(c uuid, s nucleo.solicitud_formal,
                                                          texto_nuevo text, medio_nuevo text,
                                                          dia date, identidad uuid,
                                                          adjunto uuid, actor uuid)
  returns void
  language plpgsql volatile
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    vence date;
  begin
    perform nucleo.exigir_adjunto_solicitud_formal(c, s.id, adjunto, 'respuesta', actor);
    select n.vence_el into vence from nucleo.solicitud_formal_envio n
     where n.consorcio_id = c and n.solicitud_id = s.id;
    insert into nucleo.solicitud_formal_respuesta
      (consorcio_id, solicitud_id, texto, medio, respondida_el, registrada_por,
       identidad_registro, adjunto_id)
    values (c, s.id, btrim(texto_nuevo), medio_nuevo, dia, actor, identidad, adjunto);
    -- Sin el texto: es libre. P5: fuera de plazo se acepta y queda marcada.
    perform nucleo.auditar_solicitud_formal(c, 'consorcio.solicitud_formal.responder', s.id,
      actor, jsonb_build_object('numero', s.numero, 'medio', medio_nuevo, 'respondida_el', dia,
                                'fuera_de_plazo', dia > vence, 'adjunto_id', adjunto));
  end
  $$;

create function nucleo.responder_solicitud_formal(solicitud uuid, texto_nuevo text, adjunto uuid,
                                                  actor uuid, origen_pedido text, sesion uuid)
  returns void
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    s nucleo.solicitud_formal;
  begin
    perform nucleo.exigir_sesion_solicitud_formal(c, actor, sesion, origen_pedido);
    s := nucleo.solicitud_formal_bloqueada(c, solicitud);
    -- La concede ser la persona del mandato destinatario: para cualquier otra, no existe.
    if not exists (select 1 from nucleo.solicitud_formal_emision e
                     join nucleo.mandato m
                       on m.consorcio_id = e.consorcio_id and m.id = e.mandato_id
                    where e.consorcio_id = c and e.solicitud_id = s.id and m.persona_id = actor)
    then
      raise exception 'la solicitud no existe' using errcode = 'P0001', hint = 'no_encontrado';
    end if;
    if nucleo.estado_solicitud_formal(c, s.id) not in ('enviada', 'vencida') then
      raise exception 'la solicitud no espera respuesta' using errcode = 'P0001',
        hint = 'solicitud_no_admite';
    end if;
    perform nucleo.guardar_respuesta_solicitud_formal(c, s, texto_nuevo, 'aplicacion',
      nucleo.hoy_del_consorcio(c), nucleo.identidad_humana_de(c, actor), adjunto, actor);
  end
  $$;

create function nucleo.registrar_respuesta_solicitud_formal(solicitud uuid, texto_nuevo text,
                                                            recibida date, adjunto uuid,
                                                            actor uuid, origen_pedido text,
                                                            sesion uuid)
  returns void
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    identidad uuid;
    s nucleo.solicitud_formal;
    enviada date;
  begin
    identidad := nucleo.exigir_acto_solicitud_formal(c, actor, sesion, 'solicitud_formal.firmar',
                                                     origen_pedido);
    s := nucleo.solicitud_formal_bloqueada(c, solicitud);
    if nucleo.estado_solicitud_formal(c, s.id) not in ('enviada', 'vencida') then
      raise exception 'la solicitud no espera respuesta' using errcode = 'P0001',
        hint = 'solicitud_no_admite';
    end if;
    -- P2: la de un destinatario con cuenta la da él en la aplicación.
    if exists (select 1 from nucleo.solicitud_formal_emision e
                 join nucleo.mandato m on m.consorcio_id = e.consorcio_id and m.id = e.mandato_id
                where e.consorcio_id = c and e.solicitud_id = s.id and m.persona_id is not null)
    then
      raise exception 'el destinatario tiene cuenta: responde en la aplicación'
        using errcode = 'P0001', hint = 'destinatario_con_cuenta';
    end if;
    select n.fecha_envio into enviada from nucleo.solicitud_formal_envio n
     where n.consorcio_id = c and n.solicitud_id = s.id;
    if recibida is null or recibida < enviada or recibida > nucleo.hoy_del_consorcio(c) then
      raise exception 'la respuesta llega entre el envío y hoy' using errcode = 'P0001',
        hint = 'fecha_respuesta_invalida';
    end if;
    perform nucleo.guardar_respuesta_solicitud_formal(c, s, texto_nuevo, 'medio_formal', recibida,
      identidad, adjunto, actor);
  end
  $$;

-- Quien tiene `fiscalizacion.ver_panel` ve todas; cualquier otra persona, las dirigidas a su
-- cuenta. Las personas de las firmas van para decidir qué se le ofrece: la API no las publica.
create function nucleo.solicitudes_formales_lectura(actor uuid, origen_pedido text, sesion uuid)
  returns table (id uuid, numero integer, objeto text, referencia text, documentacion text,
                 plazo_dias integer, estado text, preparada_por_nombre text,
                 preparada_en timestamptz, firmas jsonb, destinatario_nombre text,
                 destinatario_con_cuenta boolean, destinatario_es_actor boolean,
                 emitida_en timestamptz, texto_emitido text, huella_texto text, envio jsonb,
                 respuesta jsonb, retiro jsonb)
  language plpgsql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    todas boolean;
  begin
    perform nucleo.exigir_sesion_solicitud_formal(c, actor, sesion, origen_pedido);
    todas := nucleo.tiene_facultad_vigente(c, actor, 'fiscalizacion.ver_panel',
                                           nucleo.hoy_del_consorcio(c));
    return query
      select s.id, s.numero, s.objeto, s.referencia, s.documentacion, s.plazo_dias::integer,
             nucleo.estado_solicitud_formal(c, s.id), pp.nombre, s.preparada_en,
             coalesce((select jsonb_agg(jsonb_build_object('persona_id', f.persona_id,
                                                           'nombre', pf.nombre,
                                                           'firmada_en', f.firmada_en)
                                        order by f.firmada_en, f.id)
                         from nucleo.solicitud_formal_firma f
                         join nucleo.persona pf on pf.id = f.persona_id
                        where f.consorcio_id = c and f.solicitud_id = s.id), '[]'::jsonb),
             case when m.id is not null then nucleo.nombre_del_mandato(m) end,
             case when m.id is not null then m.persona_id is not null end,
             m.persona_id is not distinct from actor and m.id is not null,
             e.emitida_en, e.texto, e.huella,
             case when n.solicitud_id is not null then jsonb_build_object(
               'medio', n.medio, 'detalle', n.detalle, 'fecha_envio', n.fecha_envio,
               'vence_el', n.vence_el, 'registrado_por_nombre', pn.nombre,
               'registrado_en', n.registrado_en, 'documento_id', an.documento_id,
               'version_documento_id', an.version_documento_id) end,
             case when r.solicitud_id is not null then jsonb_build_object(
               'texto', r.texto, 'medio', r.medio, 'respondida_el', r.respondida_el,
               'fuera_de_plazo', r.respondida_el > n.vence_el,
               'registrada_por_nombre', pr.nombre, 'registrada_en', r.registrada_en,
               'documento_id', ar.documento_id,
               'version_documento_id', ar.version_documento_id) end,
             case when t.solicitud_id is not null then jsonb_build_object(
               'motivo', t.motivo, 'retirada_por_nombre', pt.nombre,
               'retirada_en', t.retirada_en) end
        from nucleo.solicitud_formal s
        join nucleo.persona pp on pp.id = s.preparada_por
        left join nucleo.solicitud_formal_emision e
          on e.consorcio_id = s.consorcio_id and e.solicitud_id = s.id
        left join nucleo.mandato m on m.consorcio_id = e.consorcio_id and m.id = e.mandato_id
        left join nucleo.solicitud_formal_envio n
          on n.consorcio_id = s.consorcio_id and n.solicitud_id = s.id
        left join nucleo.persona pn on pn.id = n.registrado_por
        left join nucleo.solicitud_formal_adjunto an
          on an.consorcio_id = n.consorcio_id and an.id = n.adjunto_id
        left join nucleo.solicitud_formal_respuesta r
          on r.consorcio_id = s.consorcio_id and r.solicitud_id = s.id
        left join nucleo.persona pr on pr.id = r.registrada_por
        left join nucleo.solicitud_formal_adjunto ar
          on ar.consorcio_id = r.consorcio_id and ar.id = r.adjunto_id
        left join nucleo.solicitud_formal_retiro t
          on t.consorcio_id = s.consorcio_id and t.solicitud_id = s.id
        left join nucleo.persona pt on pt.id = t.retirada_por
       where s.consorcio_id = c and (todas or (m.id is not null and m.persona_id = actor))
       order by s.numero desc;
  end
  $$;

create function nucleo.panel_solicitudes_abiertas()
  returns integer
  language plpgsql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
  begin
    perform nucleo.exigir_ver_panel(c, plataforma.persona_actual());
    return (select count(*)::integer from nucleo.solicitud_formal s
             where s.consorcio_id = c
               and nucleo.estado_solicitud_formal(c, s.id) in ({_lista(ABIERTAS)}));
  end
  $$;
"""

RUTAS = (
    "nucleo.preparar_solicitud_formal(text, text, text, integer, uuid, text, uuid)",
    "nucleo.firmar_solicitud_formal(uuid, uuid, text, uuid)",
    "nucleo.retirar_solicitud_formal(uuid, text, uuid, text, uuid)",
    "nucleo.subir_adjunto_solicitud_formal(uuid, text, text, text, integer, text, uuid, text,"
    " uuid)",
    "nucleo.registrar_envio_solicitud_formal(uuid, text, text, date, uuid, uuid, text, uuid)",
    "nucleo.responder_solicitud_formal(uuid, text, uuid, uuid, text, uuid)",
    "nucleo.registrar_respuesta_solicitud_formal(uuid, text, date, uuid, uuid, text, uuid)",
    "nucleo.solicitudes_formales_lectura(uuid, text, uuid)",
    "nucleo.panel_solicitudes_abiertas()",
    # Sin `security definer`: la lee `app.contexto` con la RLS de `cem_app` (A14).
    "nucleo.mandato_administracion_vigente(uuid, date)",
)
INTERNAS = (
    "nucleo.guardar_respuesta_solicitud_formal(uuid, nucleo.solicitud_formal, text, text, date,"
    " uuid, uuid, uuid)",
    "nucleo.adjunto_de_solicitud_formal(uuid, uuid, uuid)",
    "nucleo.exigir_adjunto_solicitud_formal(uuid, uuid, uuid, text, uuid)",
    "nucleo.exigir_acto_con_adjunto(uuid, nucleo.solicitud_formal, text, uuid, uuid, text)",
    "nucleo.texto_solicitud_formal(uuid, nucleo.solicitud_formal, nucleo.mandato, date)",
    "nucleo.nombre_del_mandato(nucleo.mandato)",
    "nucleo.solicitud_formal_bloqueada(uuid, uuid)",
    "nucleo.estado_solicitud_formal(uuid, uuid)",
    "nucleo.exigir_acto_solicitud_formal(uuid, uuid, uuid, text, text)",
    "nucleo.exigir_sesion_solicitud_formal(uuid, uuid, uuid, text)",
    "nucleo.auditar_solicitud_formal(uuid, text, uuid, uuid, jsonb)",
)
"""En el orden en que se pueden borrar: cada una antes de las que llama."""

REFERENCIADO = "nucleo.documento_referenciado(uuid, uuid)"
FIJAR_OBJETO = "nucleo.fijar_version_objeto(uuid, text, uuid, text)"
EXPORTAR_MANDATOS = "nucleo.exportar_mandatos(uuid, uuid)"

REFERENCIADO_CAMBIOS = (
    (
        "        or exists (select 1 from nucleo.documentacion_proveedor x\n",
        "        or exists (select 1 from nucleo.solicitud_formal_adjunto x\n"
        "                    where x.consorcio_id = c and x.documento_id = documento)\n"
        "        or exists (select 1 from nucleo.documentacion_proveedor x\n",
    ),
)
FIJAR_CAMBIOS = (
    (
        "    if actor is distinct from plataforma.persona_actual() or not exists (\n",
        "    -- 0108: tampoco la pide el adjunto que esa persona subió para una solicitud formal.\n"
        "    if actor is distinct from plataforma.persona_actual()\n"
        "       or not nucleo.adjunto_de_solicitud_formal(c, version, actor) and not exists (\n",
    ),
)


def _exportar(entidad: str, columnas: str, desde: str, orden: str) -> str:
    return (
        "    return query\n"
        f"      select '{entidad}'::text,\n"
        f"             jsonb_build_object({columnas})\n"
        f"        from {desde}\n"
        f"       where x.consorcio_id = c\n"
        f"       order by {orden};\n"
    )


def _nombre(columna: str) -> str:
    return f"nucleo.nombre_para_exportar(c, {columna}, hoy)"


EXPORTAR_SOLICITUDES = (
    "    -- 0108: las solicitudes formales, con el nombre de cada persona y no su identificador.\n"
    + _exportar(
        "solicitud_formal",
        "'id', x.id, 'numero', x.numero, 'objeto', x.objeto, 'referencia', x.referencia,"
        " 'documentacion', x.documentacion, 'plazo_dias', x.plazo_dias,"
        f" 'preparada_por_nombre', {_nombre('x.preparada_por')}, 'preparada_en', x.preparada_en",
        "nucleo.solicitud_formal x",
        "x.numero",
    )
    + _exportar(
        "solicitud_formal_firma",
        f"'solicitud_id', x.solicitud_id, 'firmada_por_nombre', {_nombre('x.persona_id')},"
        " 'firmada_en', x.firmada_en",
        "nucleo.solicitud_formal_firma x",
        "x.solicitud_id, x.firmada_en, x.id",
    )
    + _exportar(
        "solicitud_formal_emision",
        "'solicitud_id', x.solicitud_id, 'mandato_id', x.mandato_id, 'emitida_en', x.emitida_en,"
        " 'emitida_el', x.emitida_el, 'texto', x.texto, 'huella', x.huella",
        "nucleo.solicitud_formal_emision x",
        "x.emitida_en, x.solicitud_id",
    )
    + _exportar(
        "solicitud_formal_adjunto",
        "'id', x.id, 'solicitud_id', x.solicitud_id, 'para', x.para,"
        " 'documento_id', x.documento_id, 'version_documento_id', x.version_documento_id,"
        f" 'subido_por_nombre', {_nombre('x.subido_por')}, 'subido_en', x.subido_en",
        "nucleo.solicitud_formal_adjunto x",
        "x.subido_en, x.id",
    )
    + _exportar(
        "solicitud_formal_envio",
        "'solicitud_id', x.solicitud_id, 'medio', x.medio, 'detalle', x.detalle,"
        " 'fecha_envio', x.fecha_envio, 'vence_el', x.vence_el,"
        f" 'registrado_por_nombre', {_nombre('x.registrado_por')},"
        " 'registrado_en', x.registrado_en, 'adjunto_id', x.adjunto_id",
        "nucleo.solicitud_formal_envio x",
        "x.registrado_en, x.solicitud_id",
    )
    + _exportar(
        "solicitud_formal_respuesta",
        "'solicitud_id', x.solicitud_id, 'texto', x.texto, 'medio', x.medio,"
        " 'respondida_el', x.respondida_el,"
        f" 'registrada_por_nombre', {_nombre('x.registrada_por')},"
        " 'registrada_en', x.registrada_en, 'adjunto_id', x.adjunto_id",
        "nucleo.solicitud_formal_respuesta x",
        "x.registrada_en, x.solicitud_id",
    )
    + _exportar(
        "solicitud_formal_retiro",
        "'solicitud_id', x.solicitud_id, 'motivo', x.motivo,"
        f" 'retirada_por_nombre', {_nombre('x.retirada_por')}, 'retirada_en', x.retirada_en",
        "nucleo.solicitud_formal_retiro x",
        "x.retirada_en, x.solicitud_id",
    )
)
EXPORTAR_CAMBIOS = (
    (
        "       order by r.eficaz_desde, r.id;\n",
        "       order by r.eficaz_desde, r.id;\n" + EXPORTAR_SOLICITUDES,
    ),
)

EN_SU_LUGAR = (
    (REFERENCIADO, REFERENCIADO_CAMBIOS),
    (FIJAR_OBJETO, FIJAR_CAMBIOS),
    (EXPORTAR_MANDATOS, EXPORTAR_CAMBIOS),
)
"""Misma firma y lo mismo que devuelven: `create or replace` con la definición cambiada."""

GUARDA_DOWNGRADE = """
-- Hasta el fin del downgrade nadie prepara: la guarda vale para lo que se borra.
lock table nucleo.solicitud_formal in access exclusive mode;
do $$
begin
  if exists (select 1 from nucleo.solicitud_formal) then
    raise exception '0108 no baja con solicitudes formales: se perdería la constancia'
      using errcode = 'P0001', hint = 'downgrade_con_datos';
  end if;
end
$$;
"""


def _definicion(funcion: str, cambios, *, al_reves: bool) -> str:
    definicion = (
        op.get_bind()
        .execute(text("select pg_get_functiondef(cast(:f as regprocedure))"), {"f": funcion})
        .scalar_one()
    )
    for antes, despues in reversed(cambios) if al_reves else cambios:
        if al_reves:
            antes, despues = despues, antes
        if definicion.count(antes) != 1:
            raise RuntimeError(f"0108: {funcion} no tiene exactamente una vez {antes!r}")
        definicion = definicion.replace(antes, despues)
    return definicion


def upgrade() -> None:
    op.execute(TABLAS_SQL)
    for tabla in TABLAS:
        op.execute(_protecciones(tabla))
    op.execute(FUNCIONES)
    for funcion in RUTAS + INTERNAS:
        op.execute(f"revoke all on function {funcion} from public")
    for funcion in RUTAS:
        op.execute(f"grant execute on function {funcion} to cem_app")
    for funcion, cambios in EN_SU_LUGAR:
        op.execute(_definicion(funcion, cambios, al_reves=False))


def downgrade() -> None:
    op.execute(GUARDA_DOWNGRADE)
    for funcion, cambios in reversed(EN_SU_LUGAR):
        op.execute(_definicion(funcion, cambios, al_reves=True))
    for funcion in RUTAS + INTERNAS:
        op.execute(f"drop function {funcion}")
    for tabla in reversed(TABLAS):
        op.execute(f"drop table {tabla}")
