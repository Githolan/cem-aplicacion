"""H3, decisión 1 del propietario (30-10-2026, «1A»): tres franjas con un umbral que fija cada
consorcio por acta, contratos y abonos que respaldan lo rutinario, vigencia de 7 días de una
aprobación y la emisión (Aprobada → EnEjecucion).

Diseño: `doc/diseno/H3_Proveedor_Beneficiario_Cuenta.md` del repositorio CEM, «Decisión 1A», con
el mensaje del propietario del 30-10-2026 (puntos 1 a 36).

**El umbral** (`nucleo.umbral_orden_pago`): una fila por umbral y moneda, con el importe en
centavos, la moneda, desde cuándo rige y el acta (de asamblea o del Consejo) que lo respalda. Solo
se agrega: un umbral nuevo es otra fila con una fecha posterior y nunca cambia lo ya preparado. No
hay umbral de la plataforma: sin umbral registrado, todo lo no rutinario pide aprobación. Lo carga
soporte con `nucleo.fijar_umbral_orden_pago` (`scripts/umbral_orden_pago.py`), con el importe y el
acta que decidió el consorcio, desde hoy en adelante; como el resto de la clase B, sin evento de
soporte.

**Contratos y abonos** (`nucleo.contrato_proveedor`): el registro estructurado que respalda lo
rutinario. Proveedor, tipo (`contrato` o `abono`), alcance, importe máximo por orden y moneda,
vigencia (desde y hasta), el documento de respaldo (uno vigente de la categoría
`contratos_proveedores`, el archivo que ya guarda H2c; no hay otro almacén) y, si corresponde, la
decisión que lo autoriza. Lo registra quien prepara (`egresos.preparar_orden`) y **solo rige
después de que lo confirme un consejero** (`egresos.aprobar_orden`) que no es quien lo registró,
sobre la huella que vio (`contrato_proveedor_confirmacion`): si el administrador solo pudiera
habilitar lo rutinario, escribir «rutinario» le alcanzaría. Se da de baja con
`contrato_proveedor_baja`. Todo solo se agrega.

**El contrato se declara.** Lo rutinario no se deduce: quien prepara declara en la orden el
contrato que la respalda (`contrato_id`; `preparar_orden_pago` y `modificar_orden_pago` suman el
parámetro). Si no la cubre (otro proveedor, otra moneda, un importe mayor al máximo, sin confirmar,
de baja o fuera de su vigencia) se niega (`contrato_no_aplica`). Sin declararlo, una orden nunca es
rutinaria aunque el proveedor tenga un contrato (revisión de app #169).

**La franja**, calculada en cada versión de la orden al prepararla y guardada con el umbral
vigente ese día en la moneda de la orden (`umbral_id`) y el contrato declarado. Gana la primera
que corresponde, de la más estricta a la más permisiva (punto 35):
1. `fondo_reserva`: usa el fondo de reserva. Aprueba el Consejo.
2. `beneficiario_nuevo`: el proveedor no tiene una orden aprobada antes en el consorcio.
3. `cuenta_cambiada`: la tuvo, pero a otra cuenta.
4. `rutinaria`: la orden declara un contrato o abono que la cubre. Sin aprobación previa.
5. `sin_umbral`: no hay umbral vigente en la moneda de la orden. Aprobación.
6. `sobre_umbral`: el importe supera el umbral. Aprobación.
7. `bajo_umbral`: el importe no lo supera. Sin aprobación previa.
La franja, el umbral y el contrato entran en la huella: una aprobación queda atada a la regla con
que se evaluó. Una franja sin aprobación pasa la orden de EnRevision a Aprobada al enviarla.

**Fraccionamiento** (punto 9): una orden `bajo_umbral` lleva `alerta_fraccionamiento` si, sumada a
las demás órdenes no rechazadas del mismo proveedor y moneda preparadas en el mismo mes del
consorcio, supera el umbral; una `rutinaria`, si sumada a las demás del mismo contrato en el mes
supera su importe máximo (revisión de app #169). Es una alerta que se ve en la revisión, no un
bloqueo.

**Vigencia de una aprobación** (punto 10): `orden_pago.aprobada_en` es cuándo la orden llegó a
Aprobada. A los 7 días corridos (`VIGENCIA_APROBACION_DIAS`) ya no se emite
(`aprobacion_vencida`) y quien prepara la renueva (`renovar_orden_vencida`): una versión nueva con
los mismos datos, evaluada con las reglas de ese día, que vuelve a revisión. Nada se renueva solo.

**Emitir** (`emitir_orden_pago`, que existe desde `0045`) agrega: la aprobación no venció, las
aprobaciones de la versión alcanzan si la franja las pide, el contrato de una rutinaria sigue
vigente y el proveedor tiene cuenta destino. `cem_app` recibe EXECUTE: la ruta existe. No mueve
dinero: deja la orden en EnEjecucion.

**Upgrade** se niega (`upgrade_con_datos`) si hay órdenes de pago: no se sabe con qué franja se
evaluó cada versión. **Downgrade** se niega (`downgrade_con_datos`) si hay órdenes, umbrales o
contratos.

Revisión: 0050
Anterior: 0049
"""

from alembic import op
from sqlalchemy import text

revision = "0050"
down_revision = "0049"
branch_labels = None
depends_on = None

# Decisión del propietario (30-10-2026, punto 10): 7 días corridos desde que la orden llega a
# Aprobada. `cem_contracts.egresos.VIGENCIA_APROBACION` dice lo mismo (una prueba los compara).
VIGENCIA_APROBACION_DIAS = 7
TIPOS_ACTA = ("asamblea", "consejo")
FRANJAS = (
    "fondo_reserva",
    "beneficiario_nuevo",
    "cuenta_cambiada",
    "rutinaria",
    "sin_umbral",
    "sobre_umbral",
    "bajo_umbral",
)
SIN_APROBACION = ("rutinaria", "bajo_umbral")
CATEGORIA_CONTRATOS = "contratos_proveedores"

_lista = ", ".join
_franjas = _lista(f"'{f}'" for f in FRANJAS)
_sin_aprobacion = _lista(f"'{f}'" for f in SIN_APROBACION)
_actas = _lista(f"'{t}'" for t in TIPOS_ACTA)

# Las que cambian de firma (suman el contrato declarado) llevan `{}`: sin nada antes de 0050,
# `, uuid` después. Se crean de nuevo y se borra la anterior, como en 0048.
NUEVA_VERSION = (
    "nucleo.nueva_version_orden_pago(uuid, uuid, integer, bigint, text, text, uuid, text,"
    " boolean{}, uuid, uuid)"
)
PREPARAR = "nucleo.preparar_orden_pago(bigint, text, text, uuid, text, boolean{}, uuid, text, uuid)"
MODIFICAR = (
    "nucleo.modificar_orden_pago(uuid, integer, bigint, text, text, uuid, text, boolean{}, uuid,"
    " text, uuid)"
)
ANTES, DESPUES = "", ", uuid"
ENVIAR = "nucleo.enviar_orden_a_revision(uuid, integer, uuid, text, uuid)"
APROBAR = "nucleo.registrar_aprobacion_orden_pago(uuid, integer, bytea, uuid, text, uuid)"
EMITIR = "nucleo.emitir_orden_pago(uuid, integer, uuid, text, uuid)"
FIRMA = ("usa_reserva boolean, actor uuid", "usa_reserva boolean, contrato_nuevo uuid, actor uuid")

# Sobre el texto exacto de cada función (0045 con lo que sumaron 0046 y 0048): (antes, después).
CAMBIOS = {
    NUEVA_VERSION: [
        FIRMA,
        ("    h bytea;\n  begin", "    h bytea;\n    ev record;\n  begin"),
        (
            """    h := nucleo.huella_orden_pago(orden, numero, importe_nuevo, moneda_nueva,
           btrim(concepto_nuevo), encode(pv.huella, 'hex'), btrim(comprobante_nuevo),
           usa_reserva);""",
            """    -- 0050: la franja, con el umbral y el contrato declarado, entra en la huella.
    select * into ev from nucleo.evaluar_orden_pago(c, orden, pv, importe_nuevo, moneda_nueva,
                                                    usa_reserva, contrato_nuevo);
    h := nucleo.huella_orden_pago_evaluada(
           nucleo.huella_orden_pago(orden, numero, importe_nuevo, moneda_nueva,
             btrim(concepto_nuevo), encode(pv.huella, 'hex'), btrim(comprobante_nuevo),
             usa_reserva),
           ev.franja, ev.umbral_id, ev.contrato_id);""",
        ),
        (
            "       preparada_por, identidad_preparo, aprobaciones_requeridas)",
            "       preparada_por, identidad_preparo, aprobaciones_requeridas, franja, umbral_id,\n"
            "       contrato_id, alerta_fraccionamiento)",
        ),
        (
            "            pv.proveedor_id, pv.numero, nucleo.condicion_beneficiario(c, orden, pv),",
            "            pv.proveedor_id, pv.numero, ev.condicion,",
        ),
        (
            "            nucleo.aprobaciones_requeridas_orden_pago(c));",
            "            nucleo.aprobaciones_requeridas_orden_pago(c), ev.franja, ev.umbral_id,\n"
            "            ev.contrato_id, ev.alerta);",
        ),
    ],
    PREPARAR: [
        FIRMA,
        (
            "comprobante_nuevo, usa_reserva, actor, identidad);",
            "comprobante_nuevo, usa_reserva, contrato_nuevo,\n           actor, identidad);",
        ),
    ],
    MODIFICAR: [
        FIRMA,
        (
            "comprobante_nuevo, usa_reserva,\n           actor, identidad);",
            "comprobante_nuevo, usa_reserva,\n           contrato_nuevo, actor, identidad);",
        ),
        # Declarar otro contrato (o ninguno) también es un cambio.
        (
            "        v.comprobante, v.usa_fondo_reserva)",
            "        v.comprobante, v.usa_fondo_reserva, v.contrato_id)",
        ),
        (
            "btrim(comprobante_nuevo), usa_reserva)\n    then",
            "btrim(comprobante_nuevo), usa_reserva, contrato_nuevo)\n    then",
        ),
        (
            "       set version_vigente = o.version_vigente + 1, estado = estado_nuevo,"
            " actualizada_en = now()",
            "       set version_vigente = o.version_vigente + 1, estado = estado_nuevo,"
            " aprobada_en = null,\n           actualizada_en = now()",
        ),
        (
            "    return o.version_vigente + 1;",
            "    if estado_nuevo = 'en_revision' then\n"
            "      perform nucleo.aprobar_por_franja(c, orden, o.version_vigente + 1, actor);\n"
            "    end if;\n"
            "    return o.version_vigente + 1;",
        ),
    ],
    ENVIAR: [
        (
            "'version', o.version_vigente));\n",
            "'version', o.version_vigente));\n"
            "    perform nucleo.aprobar_por_franja(c, orden, o.version_vigente, actor);\n",
        ),
    ],
    APROBAR: [
        (
            "update nucleo.orden_pago set estado = 'aprobada', actualizada_en = now() where id ="
            " orden;",
            "update nucleo.orden_pago set estado = 'aprobada', aprobada_en = now(),\n"
            "             actualizada_en = now() where id = orden;",
        ),
    ],
    EMITIR: [
        (
            "    perform nucleo.exigir_proveedor_sin_cambios(c, orden, version_vista);\n",
            "    perform nucleo.exigir_proveedor_sin_cambios(c, orden, version_vista);\n"
            "    perform nucleo.exigir_orden_emitible(c, o, version_vista);\n",
        ),
    ],
}

GUARDA = """
-- Nada se escribe en las órdenes entre la guarda y el cambio de las tablas (como en 0048).
lock table nucleo.orden_pago in share row exclusive mode;

do $$
begin
  if exists (select 1 from nucleo.orden_pago) then
    raise exception '0050 no sube con órdenes de pago: no se sabe con qué franja se evaluó cada'
      ' versión' using errcode = 'P0001', hint = 'upgrade_con_datos';
  end if;
end
$$;
"""

UPGRADE = f"""
create table nucleo.umbral_orden_pago (
  consorcio_id      uuid not null references nucleo.consorcio (id),
  id                uuid not null default plataforma.uuid_v7(),
  importe_centavos  bigint not null check (importe_centavos > 0),
  moneda            text not null check (moneda ~ '^[A-Z]{{3}}$'),
  vigente_desde     date not null,
  decision_id       uuid not null,
  creada_en         timestamptz not null default now(),
  primary key (id),
  unique (consorcio_id, id),
  -- Un umbral por moneda: el de otra moneda no apaga este.
  unique (consorcio_id, moneda, vigente_desde),
  foreign key (consorcio_id, decision_id) references nucleo.decision_formal (consorcio_id, id)
);

create table nucleo.contrato_proveedor (
  consorcio_id             uuid not null,
  id                       uuid not null default plataforma.uuid_v7(),
  proveedor_id             uuid not null,
  tipo                     text not null check (tipo in ('contrato', 'abono')),
  alcance                  text not null check (length(btrim(alcance)) between 1 and 300),
  importe_maximo_centavos  bigint not null check (importe_maximo_centavos > 0),
  moneda                   text not null check (moneda ~ '^[A-Z]{{3}}$'),
  vigente_desde            date not null,
  vigente_hasta            date not null check (vigente_hasta >= vigente_desde),
  documento_id             uuid not null,
  decision_id              uuid,
  huella                   bytea not null check (length(huella) = 32),
  registrado_por           uuid not null references nucleo.persona (id),
  identidad_registro       uuid not null,
  creado_en                timestamptz not null default now(),
  primary key (id),
  unique (consorcio_id, id),
  unique (id, huella),
  foreign key (consorcio_id, proveedor_id) references nucleo.proveedor (consorcio_id, id),
  foreign key (consorcio_id, documento_id) references nucleo.documento (consorcio_id, id),
  foreign key (consorcio_id, decision_id) references nucleo.decision_formal (consorcio_id, id),
  foreign key (consorcio_id, identidad_registro)
    references nucleo.identidad_humana (consorcio_id, id)
);

create table nucleo.contrato_proveedor_confirmacion (
  consorcio_id  uuid not null,
  contrato_id   uuid not null,
  huella        bytea not null,
  persona_id    uuid not null references nucleo.persona (id),
  identidad_id  uuid not null,
  creada_en     timestamptz not null default now(),
  primary key (contrato_id),
  -- La confirmación es de lo que se vio: no se puede colgar de otro contrato.
  foreign key (contrato_id, huella) references nucleo.contrato_proveedor (id, huella),
  foreign key (consorcio_id, contrato_id) references nucleo.contrato_proveedor (consorcio_id, id),
  foreign key (consorcio_id, identidad_id) references nucleo.identidad_humana (consorcio_id, id)
);

create table nucleo.contrato_proveedor_baja (
  consorcio_id  uuid not null,
  contrato_id   uuid not null,
  persona_id    uuid not null references nucleo.persona (id),
  creada_en     timestamptz not null default now(),
  primary key (contrato_id),
  foreign key (consorcio_id, contrato_id) references nucleo.contrato_proveedor (consorcio_id, id)
);

alter table nucleo.umbral_orden_pago enable row level security;
alter table nucleo.umbral_orden_pago force row level security;
alter table nucleo.contrato_proveedor enable row level security;
alter table nucleo.contrato_proveedor force row level security;
alter table nucleo.contrato_proveedor_confirmacion enable row level security;
alter table nucleo.contrato_proveedor_confirmacion force row level security;
alter table nucleo.contrato_proveedor_baja enable row level security;
alter table nucleo.contrato_proveedor_baja force row level security;
create policy por_consorcio on nucleo.umbral_orden_pago for select
  using (consorcio_id = plataforma.consorcio_actual());
create policy por_consorcio on nucleo.contrato_proveedor for select
  using (consorcio_id = plataforma.consorcio_actual());
create policy por_consorcio on nucleo.contrato_proveedor_confirmacion for select
  using (consorcio_id = plataforma.consorcio_actual());
create policy por_consorcio on nucleo.contrato_proveedor_baja for select
  using (consorcio_id = plataforma.consorcio_actual());

-- Solo se agregan filas, como la historia de la orden.
create trigger historia_umbral_orden_pago before update or delete on nucleo.umbral_orden_pago
  for each row execute function nucleo.negar_cambio_historia_orden_pago();
create trigger historia_umbral_orden_pago_truncate before truncate on nucleo.umbral_orden_pago
  for each statement execute function nucleo.negar_cambio_historia_orden_pago();
create trigger historia_contrato_proveedor before update or delete on nucleo.contrato_proveedor
  for each row execute function nucleo.negar_cambio_historia_orden_pago();
create trigger historia_contrato_proveedor_truncate before truncate on nucleo.contrato_proveedor
  for each statement execute function nucleo.negar_cambio_historia_orden_pago();
create trigger historia_contrato_proveedor_confirmacion before update or delete
  on nucleo.contrato_proveedor_confirmacion
  for each row execute function nucleo.negar_cambio_historia_orden_pago();
create trigger historia_contrato_proveedor_confirmacion_truncate before truncate
  on nucleo.contrato_proveedor_confirmacion
  for each statement execute function nucleo.negar_cambio_historia_orden_pago();
create trigger historia_contrato_proveedor_baja before update or delete
  on nucleo.contrato_proveedor_baja
  for each row execute function nucleo.negar_cambio_historia_orden_pago();
create trigger historia_contrato_proveedor_baja_truncate before truncate
  on nucleo.contrato_proveedor_baja
  for each statement execute function nucleo.negar_cambio_historia_orden_pago();

alter table nucleo.orden_pago_version
  add column franja text not null check (franja in ({_franjas})),
  add column umbral_id uuid,
  add column contrato_id uuid,
  add column alerta_fraccionamiento boolean not null,
  add foreign key (consorcio_id, umbral_id) references nucleo.umbral_orden_pago (consorcio_id, id),
  add foreign key (consorcio_id, contrato_id)
    references nucleo.contrato_proveedor (consorcio_id, id),
  -- `contrato_id` es el contrato que declaró quien preparó; solo hace rutinaria la orden si
  -- ninguna franja más estricta corresponde.
  add check (franja <> 'rutinaria' or contrato_id is not null),
  add check (alerta_fraccionamiento is false or franja in ('bajo_umbral', 'rutinaria'));

-- Cuándo llegó a Aprobada: la vigencia de la aprobación se cuenta desde ahí.
alter table nucleo.orden_pago
  add column aprobada_en timestamptz,
  add check ((estado in ('borrador', 'en_revision', 'rechazada')) = (aprobada_en is null));

create function nucleo.franja_requiere_aprobacion(franja text)
  returns boolean
  language sql immutable strict
  set search_path = pg_catalog, pg_temp
  as $$
    select franja not in ({_sin_aprobacion})
  $$;

create function nucleo.huella_contrato_proveedor(contrato uuid, proveedor uuid, tipo text,
                                                 alcance text, importe_maximo bigint,
                                                 moneda text, desde date, hasta date,
                                                 documento uuid, decision uuid)
  returns bytea
  language sql immutable
  set search_path = pg_catalog, pg_temp
  as $$
    select sha256(convert_to(jsonb_build_object(
      'contrato', contrato, 'proveedor', proveedor, 'tipo', tipo, 'alcance', alcance,
      'importe_maximo_centavos', importe_maximo, 'moneda', moneda, 'vigente_desde', desde,
      'vigente_hasta', hasta, 'documento', documento, 'decision', decision)::text, 'UTF8'))
  $$;

create function nucleo.huella_orden_pago_evaluada(base bytea, franja text, umbral uuid,
                                                  contrato uuid)
  returns bytea
  language sql immutable
  set search_path = pg_catalog, pg_temp
  as $$
    select sha256(base || convert_to(jsonb_build_object(
      'franja', franja, 'umbral', umbral, 'contrato', contrato)::text, 'UTF8'))
  $$;

-- Rige si un consejero lo confirmó, no se dio de baja, el día está dentro de su vigencia y el
-- documento de respaldo sigue vigente.
create function nucleo.contrato_vigente(c uuid, contrato uuid, dia date)
  returns boolean
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select exists (
      select 1 from nucleo.contrato_proveedor k
        join nucleo.contrato_proveedor_confirmacion f on f.contrato_id = k.id
        join nucleo.documento d on d.consorcio_id = k.consorcio_id and d.id = k.documento_id
       where k.consorcio_id = c and k.id = contrato
         and dia between k.vigente_desde and k.vigente_hasta
         and d.estado = 'vigente'
         and not exists (select 1 from nucleo.contrato_proveedor_baja b
                          where b.contrato_id = k.id))
  $$;

-- La franja de una versión de la orden, con la condición del beneficiario, el umbral vigente hoy
-- en la moneda de la orden (aunque no decida) y la alerta de fraccionamiento. Lo rutinario no se
-- deduce: quien prepara declara el contrato que respalda la orden, y si ese contrato no la cubre
-- (otro proveedor, otra moneda, un importe mayor al máximo, sin confirmar, de baja o fuera de su
-- vigencia) se niega, en vez de evaluarla callada como otra cosa.
create function nucleo.evaluar_orden_pago(c uuid, orden uuid, pv nucleo.proveedor_version,
                                          importe_orden bigint, moneda_orden text,
                                          reserva boolean, contrato_declarado uuid)
  returns table (condicion text, franja text, umbral_id uuid, contrato_id uuid, alerta boolean)
  language plpgsql stable
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    hoy date := nucleo.hoy_del_consorcio(c);
    cond text := nucleo.condicion_beneficiario(c, orden, pv);
    u nucleo.umbral_orden_pago;
    tope bigint;
    elegida text;
    previas numeric;
  begin
    select * into u from nucleo.umbral_orden_pago x
     where x.consorcio_id = c and x.moneda = moneda_orden and x.vigente_desde <= hoy
     order by x.vigente_desde desc limit 1;
    if contrato_declarado is not null then
      select k.importe_maximo_centavos into tope from nucleo.contrato_proveedor k
       where k.consorcio_id = c and k.id = contrato_declarado
         and k.proveedor_id = pv.proveedor_id and k.moneda = moneda_orden
         and importe_orden <= k.importe_maximo_centavos
         and nucleo.contrato_vigente(c, k.id, hoy);
      if not found then
        raise exception 'el contrato declarado no respalda esta orden' using errcode = 'P0001',
          hint = 'contrato_no_aplica';
      end if;
    end if;
    -- De la más estricta a la más permisiva: ninguna permisiva anula una estricta.
    elegida := case
      when reserva then 'fondo_reserva'
      when cond = 'nuevo' then 'beneficiario_nuevo'
      when cond = 'cuenta_cambiada' then 'cuenta_cambiada'
      when contrato_declarado is not null then 'rutinaria'
      when u.id is null then 'sin_umbral'
      when importe_orden > u.importe_centavos then 'sobre_umbral'
      else 'bajo_umbral' end;
    -- Fraccionamiento: las demás órdenes no rechazadas del mismo proveedor y moneda (o, si es
    -- rutinaria, del mismo contrato) preparadas este mes del consorcio. Alerta, no bloqueo.
    if elegida in ('bajo_umbral', 'rutinaria') then
      select coalesce(sum(v2.importe_centavos), 0) into previas
        from nucleo.orden_pago o2
        join nucleo.orden_pago_version v2
          on v2.orden_id = o2.id and v2.numero = o2.version_vigente
        join nucleo.consorcio k on k.id = o2.consorcio_id
       where o2.consorcio_id = c and o2.id <> orden and o2.estado <> 'rechazada'
         and v2.proveedor_id = pv.proveedor_id and v2.moneda = moneda_orden
         and (elegida = 'bajo_umbral' or v2.contrato_id = contrato_declarado)
         and date_trunc('month', v2.creada_en at time zone k.zona_horaria)
             = date_trunc('month', hoy::timestamp);
    end if;
    return query select cond, elegida, u.id, contrato_declarado,
      coalesce(case elegida
                 when 'bajo_umbral' then previas + importe_orden > u.importe_centavos
                 when 'rutinaria' then previas + importe_orden > tope
               end, false);
  end
  $$;

-- Una franja que no pide aprobación pasa la orden de EnRevision a Aprobada.
create function nucleo.aprobar_por_franja(c uuid, orden uuid, numero integer, actor uuid)
  returns void
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    f text;
  begin
    select v.franja into f from nucleo.orden_pago_version v
     where v.orden_id = orden and v.numero = aprobar_por_franja.numero;
    if not nucleo.franja_requiere_aprobacion(f) then
      update nucleo.orden_pago set estado = 'aprobada', aprobada_en = now(),
             actualizada_en = now()
       where id = orden;
      perform nucleo.auditar_orden_pago(c, 'consorcio.orden_pago.aprobada_por_franja', orden,
        actor, jsonb_build_object('estado', 'en_revision'),
        jsonb_build_object('estado', 'aprobada', 'version', numero, 'franja', f));
    end if;
  end
  $$;

-- Lo que emitir agrega en 0050 a lo de 0045 y 0048.
create function nucleo.exigir_orden_emitible(c uuid, o nucleo.orden_pago, numero integer)
  returns void
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    v nucleo.orden_pago_version;
  begin
    select * into v from nucleo.orden_pago_version x
     where x.orden_id = o.id and x.numero = exigir_orden_emitible.numero;
    if o.aprobada_en <= now() - make_interval(days => {VIGENCIA_APROBACION_DIAS}) then
      raise exception 'la aprobación venció: la orden se renueva y vuelve a revisión'
        using errcode = 'P0001', hint = 'aprobacion_vencida';
    end if;
    if nucleo.franja_requiere_aprobacion(v.franja)
       and (select count(distinct a.identidad_id) from nucleo.orden_pago_aprobacion a
             where a.orden_id = o.id and a.version = v.numero) < v.aprobaciones_requeridas then
      raise exception 'la versión no tiene las aprobaciones que pide' using errcode = 'P0001',
        hint = 'aprobaciones_insuficientes';
    end if;
    -- La baja del contrato bloquea la misma fila (FOR UPDATE): no se cruza con esta emisión.
    perform 1 from nucleo.contrato_proveedor k where k.id = v.contrato_id for share;
    if v.franja = 'rutinaria'
       and not nucleo.contrato_vigente(c, v.contrato_id, nucleo.hoy_del_consorcio(c)) then
      raise exception 'el contrato que hacía rutinaria la orden ya no rige'
        using errcode = 'P0001', hint = 'contrato_no_vigente';
    end if;
    if (select pv.cuenta_destino from nucleo.proveedor_version pv
         where pv.proveedor_id = v.proveedor_id and pv.numero = v.proveedor_version) is null then
      raise exception 'el proveedor no tiene cuenta destino' using errcode = 'P0001',
        hint = 'proveedor_sin_cuenta';
    end if;
  end
  $$;

create function nucleo.renovar_orden_vencida(orden uuid, version_esperada integer, actor uuid,
                                             origen_pedido text, sesion uuid)
  returns integer
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    o nucleo.orden_pago;
    v nucleo.orden_pago_version;
    h bytea;
    identidad uuid;
  begin
    identidad := nucleo.exigir_acto_economico(c, actor, sesion, 'egresos.preparar_orden',
                                              origen_pedido);
    o := nucleo.orden_pago_bloqueada(c, orden);
    if o.estado <> 'aprobada' then
      raise exception 'solo se renueva una orden aprobada' using errcode = 'P0001',
        hint = 'estado_invalido';
    end if;
    if version_esperada is distinct from o.version_vigente then
      raise exception 'la orden cambió mientras tanto' using errcode = 'P0001',
        hint = 'version_desactualizada';
    end if;
    if o.aprobada_en > now() - make_interval(days => {VIGENCIA_APROBACION_DIAS}) then
      raise exception 'la aprobación todavía rige' using errcode = 'P0001',
        hint = 'aprobacion_vigente';
    end if;
    select * into v from nucleo.orden_pago_version x
     where x.orden_id = orden and x.numero = o.version_vigente;
    h := nucleo.nueva_version_orden_pago(c, orden, o.version_vigente + 1, v.importe_centavos,
           v.moneda, v.concepto, v.proveedor_id, v.comprobante, v.usa_fondo_reserva,
           v.contrato_id, actor, identidad);
    update nucleo.orden_pago
       set version_vigente = o.version_vigente + 1, estado = 'en_revision', aprobada_en = null,
           actualizada_en = now()
     where id = orden;
    perform nucleo.auditar_orden_pago(c, 'consorcio.orden_pago.renovar', orden, actor,
      jsonb_build_object('estado', o.estado, 'version', o.version_vigente),
      jsonb_build_object('estado', 'en_revision', 'version', o.version_vigente + 1,
                         'huella', encode(h, 'hex')));
    perform nucleo.aprobar_por_franja(c, orden, o.version_vigente + 1, actor);
    return o.version_vigente + 1;
  end
  $$;

create function nucleo.fijar_umbral_orden_pago(importe bigint, moneda_umbral text, desde date,
                                               decision uuid, actor uuid, origen_pedido text)
  returns uuid
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    nuevo uuid;
  begin
    if c is null then
      raise exception 'sin consorcio en el ámbito' using errcode = '42501';
    end if;
    -- Lo carga solo el equipo, con lo que decidió el consorcio (punto 12): no elige el importe.
    if origen_pedido is distinct from 'soporte' then
      raise exception 'el umbral lo carga solo el equipo (soporte)' using errcode = 'P0001',
        hint = 'origen_invalido';
    end if;
    if plataforma.persona_actual() is not null or actor is not null then
      raise exception 'el origen soporte no admite persona en la sesión ni actor'
        using errcode = 'P0001', hint = 'origen_invalido';
    end if;
    if importe is null or importe <= 0 then
      raise exception 'el umbral es un importe positivo en centavos' using errcode = 'P0001',
        hint = 'importe_invalido';
    end if;
    if moneda_umbral is null or moneda_umbral !~ '^[A-Z]{{3}}$' then
      raise exception 'la moneda es un código de tres letras' using errcode = 'P0001',
        hint = 'moneda_invalida';
    end if;
    if desde is null or desde < nucleo.hoy_del_consorcio(c) then
      raise exception 'el umbral rige desde hoy o más adelante, nunca hacia atrás'
        using errcode = 'P0001', hint = 'fecha_invalida';
    end if;
    if decision is null or not exists (
         select 1 from nucleo.decision_formal d where d.consorcio_id = c and d.id = decision) then
      raise exception 'el umbral necesita una decisión de respaldo de este consorcio'
        using errcode = 'P0001', hint = 'decision_invalida';
    end if;
    if not exists (select 1 from nucleo.decision_formal d
                    where d.consorcio_id = c and d.id = decision and d.tipo in ({_actas})) then
      raise exception 'el umbral solo se carga con un acta de asamblea o del Consejo'
        using errcode = 'P0001', hint = 'decision_no_es_acta';
    end if;
    if exists (select 1 from nucleo.umbral_orden_pago u
                where u.consorcio_id = c and u.moneda = moneda_umbral and u.vigente_desde = desde)
    then
      raise exception 'ya hay un umbral en % que rige desde %', moneda_umbral, desde
        using errcode = 'P0001', hint = 'vigencia_repetida';
    end if;
    insert into nucleo.umbral_orden_pago
      (consorcio_id, importe_centavos, moneda, vigente_desde, decision_id)
    values (c, importe, moneda_umbral, desde, decision)
    returning id into nuevo;
    return nuevo;
  end
  $$;

create function nucleo.auditar_contrato_proveedor(c uuid, accion text, contrato uuid,
                                                  actor uuid, despues jsonb)
  returns void
  language sql volatile
  set search_path = pg_catalog, pg_temp
  as $$
    insert into auditoria.eventos
      (consorcio_id, actor_persona_id, origen, accion, entidad, entidad_id, antes, despues,
       hash_previo, hash)
    values (c, actor, 'api', accion, 'contrato_proveedor', contrato, null, despues, '', '')
  $$;

create function nucleo.registrar_contrato_proveedor(proveedor uuid, tipo_nuevo text,
                                                    alcance_nuevo text, importe_maximo bigint,
                                                    moneda_nueva text, desde date, hasta date,
                                                    documento uuid, decision uuid, actor uuid,
                                                    origen_pedido text, sesion uuid)
  returns uuid
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    k uuid := plataforma.uuid_v7();
    identidad uuid;
    pv nucleo.proveedor_version;
    h bytea;
  begin
    identidad := nucleo.exigir_acto_economico(c, actor, sesion, 'egresos.preparar_orden',
                                              origen_pedido);
    pv := nucleo.proveedor_vigente(c, proveedor);
    if pv.estado is distinct from 'activo' then
      raise exception 'el proveedor no está activo' using errcode = 'P0001',
        hint = 'proveedor_inactivo';
    end if;
    if desde is null or hasta is null or hasta < desde
       or hasta < nucleo.hoy_del_consorcio(c) then
      raise exception 'la vigencia no es válida (hasta no es anterior a desde ni a hoy)'
        using errcode = 'P0001', hint = 'fecha_invalida';
    end if;
    if documento is null or not exists (
         select 1 from nucleo.documento d
          where d.consorcio_id = c and d.id = documento and d.estado = 'vigente'
            and d.categoria = '{CATEGORIA_CONTRATOS}') then
      raise exception 'el respaldo es un documento vigente de contratos de proveedores'
        using errcode = 'P0001', hint = 'documento_invalido';
    end if;
    if decision is not null and not exists (
         select 1 from nucleo.decision_formal d where d.consorcio_id = c and d.id = decision) then
      raise exception 'la decisión no es de este consorcio' using errcode = 'P0001',
        hint = 'decision_invalida';
    end if;
    h := nucleo.huella_contrato_proveedor(k, proveedor, tipo_nuevo, btrim(alcance_nuevo),
                                          importe_maximo, moneda_nueva, desde, hasta, documento,
                                          decision);
    insert into nucleo.contrato_proveedor
      (consorcio_id, id, proveedor_id, tipo, alcance, importe_maximo_centavos, moneda,
       vigente_desde, vigente_hasta, documento_id, decision_id, huella, registrado_por,
       identidad_registro)
    values (c, k, proveedor, tipo_nuevo, btrim(alcance_nuevo), importe_maximo, moneda_nueva,
            desde, hasta, documento, decision, h, actor, identidad);
    perform nucleo.auditar_contrato_proveedor(c, 'consorcio.contrato_proveedor.registrar', k,
      actor, jsonb_build_object('proveedor', proveedor, 'tipo', tipo_nuevo,
                                'importe_maximo_centavos', importe_maximo,
                                'moneda', moneda_nueva, 'vigente_desde', desde,
                                'vigente_hasta', hasta, 'huella', encode(h, 'hex')));
    return k;
  end
  $$;

create function nucleo.confirmar_contrato_proveedor(contrato uuid, huella_vista bytea,
                                                    actor uuid, origen_pedido text,
                                                    sesion uuid)
  returns void
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    identidad uuid;
    k nucleo.contrato_proveedor;
  begin
    identidad := nucleo.exigir_acto_economico(c, actor, sesion, 'egresos.aprobar_orden',
                                              origen_pedido);
    select * into k from nucleo.contrato_proveedor x
     where x.consorcio_id = c and x.id = contrato for update;
    if not found then
      raise exception 'el contrato no existe' using errcode = 'P0001', hint = 'no_encontrado';
    end if;
    if exists (select 1 from nucleo.contrato_proveedor_baja b where b.contrato_id = contrato)
    then
      raise exception 'el contrato está dado de baja' using errcode = 'P0001',
        hint = 'contrato_dado_de_baja';
    end if;
    if exists (select 1 from nucleo.contrato_proveedor_confirmacion f
                where f.contrato_id = contrato) then
      raise exception 'el contrato ya está confirmado' using errcode = 'P0001',
        hint = 'contrato_ya_confirmado';
    end if;
    if huella_vista is distinct from k.huella then
      raise exception 'lo que se confirmó no es el contrato registrado' using errcode = 'P0001',
        hint = 'huella_distinta';
    end if;
    if k.identidad_registro = identidad then
      raise exception 'quien registró el contrato no lo confirma' using errcode = 'P0001',
        hint = 'no_es_quien_registro';
    end if;
    perform nucleo.exigir_separacion_humana(c, actor, k.registrado_por, 'no_es_quien_registro');
    insert into nucleo.contrato_proveedor_confirmacion
      (consorcio_id, contrato_id, huella, persona_id, identidad_id)
    values (c, contrato, huella_vista, actor, identidad);
    perform nucleo.auditar_contrato_proveedor(c, 'consorcio.contrato_proveedor.confirmar',
      contrato, actor, jsonb_build_object('huella', encode(huella_vista, 'hex')));
  end
  $$;

create function nucleo.baja_contrato_proveedor(contrato uuid, actor uuid, origen_pedido text,
                                               sesion uuid)
  returns void
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
  begin
    perform nucleo.exigir_acto_economico(c, actor, sesion, 'egresos.preparar_orden',
                                         origen_pedido);
    perform 1 from nucleo.contrato_proveedor x where x.consorcio_id = c and x.id = contrato
       for update;
    if not found then
      raise exception 'el contrato no existe' using errcode = 'P0001', hint = 'no_encontrado';
    end if;
    if exists (select 1 from nucleo.contrato_proveedor_baja b where b.contrato_id = contrato)
    then
      raise exception 'el contrato ya está dado de baja' using errcode = 'P0001',
        hint = 'contrato_dado_de_baja';
    end if;
    insert into nucleo.contrato_proveedor_baja (consorcio_id, contrato_id, persona_id)
    values (c, contrato, actor);
    perform nucleo.auditar_contrato_proveedor(c, 'consorcio.contrato_proveedor.baja', contrato,
      actor, jsonb_build_object('estado', 'dado_de_baja'));
  end
  $$;

-- Lecturas, con la barrera de 0049 (`exigir_lectura_egresos`).
create function nucleo.orden_pago_evaluaciones_lectura(orden uuid, facultad text, actor uuid,
                                                       origen_pedido text, sesion uuid)
  returns table (orden_id uuid, numero integer, franja text, alerta_fraccionamiento boolean,
                 umbral_importe_centavos bigint, umbral_moneda text, contrato_id uuid,
                 aprobada_en timestamptz)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
  begin
    perform nucleo.exigir_lectura_egresos(c, facultad, actor, origen_pedido, sesion);
    return query
      select v.orden_id, v.numero, v.franja, v.alerta_fraccionamiento, u.importe_centavos,
             u.moneda, v.contrato_id, o.aprobada_en
        from nucleo.orden_pago o
        join nucleo.orden_pago_version v on v.consorcio_id = o.consorcio_id and v.orden_id = o.id
        left join nucleo.umbral_orden_pago u
          on u.consorcio_id = v.consorcio_id and u.id = v.umbral_id
       where o.consorcio_id = c and (orden is null or o.id = orden)
         and (facultad = 'egresos.preparar_orden' or o.estado <> 'borrador')
       order by v.orden_id, v.numero;
  end
  $$;

create function nucleo.contratos_proveedor_lectura(contrato uuid, facultad text, actor uuid,
                                                   origen_pedido text, sesion uuid)
  returns table (id uuid, proveedor_id uuid, razon_social text, tipo text, alcance text,
                 importe_maximo_centavos bigint, moneda text, vigente_desde date,
                 vigente_hasta date, documento_id uuid, decision_id uuid, huella text,
                 registrado_por uuid, creado_en timestamptz, confirmado_por uuid,
                 confirmado_en timestamptz, baja_en timestamptz, estado text)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    hoy date;
  begin
    perform nucleo.exigir_lectura_egresos(c, facultad, actor, origen_pedido, sesion);
    hoy := nucleo.hoy_del_consorcio(c);
    return query
      select k.id, k.proveedor_id, pv.razon_social, k.tipo, k.alcance,
             k.importe_maximo_centavos, k.moneda, k.vigente_desde, k.vigente_hasta,
             k.documento_id, k.decision_id, encode(k.huella, 'hex'), k.registrado_por,
             k.creado_en, f.persona_id, f.creada_en, b.creada_en,
             case when b.contrato_id is not null then 'dado_de_baja'
                  when f.contrato_id is null then 'pendiente_confirmacion'
                  when k.vigente_hasta < hoy then 'vencido'
                  when k.vigente_desde > hoy then 'no_iniciado'
                  else 'vigente' end
        from nucleo.contrato_proveedor k
        join nucleo.proveedor_version pv
          on pv.consorcio_id = k.consorcio_id and pv.proveedor_id = k.proveedor_id
         and pv.numero = (select max(x.numero) from nucleo.proveedor_version x
                           where x.consorcio_id = k.consorcio_id
                             and x.proveedor_id = k.proveedor_id)
        left join nucleo.contrato_proveedor_confirmacion f on f.contrato_id = k.id
        left join nucleo.contrato_proveedor_baja b on b.contrato_id = k.id
       where k.consorcio_id = c and (contrato is null or k.id = contrato)
       order by k.creado_en desc, k.id;
  end
  $$;
"""

INTERNAS = (
    "nucleo.franja_requiere_aprobacion(text)",
    "nucleo.huella_contrato_proveedor(uuid, uuid, text, text, bigint, text, date, date, uuid,"
    " uuid)",
    "nucleo.huella_orden_pago_evaluada(bytea, text, uuid, uuid)",
    "nucleo.contrato_vigente(uuid, uuid, date)",
    "nucleo.evaluar_orden_pago(uuid, uuid, nucleo.proveedor_version, bigint, text, boolean, uuid)",
    "nucleo.aprobar_por_franja(uuid, uuid, integer, uuid)",
    "nucleo.exigir_orden_emitible(uuid, nucleo.orden_pago, integer)",
    "nucleo.auditar_contrato_proveedor(uuid, text, uuid, uuid, jsonb)",
)
RUTAS = (
    "nucleo.renovar_orden_vencida(uuid, integer, uuid, text, uuid)",
    "nucleo.registrar_contrato_proveedor(uuid, text, text, bigint, text, date, date, uuid, uuid,"
    " uuid, text, uuid)",
    "nucleo.confirmar_contrato_proveedor(uuid, bytea, uuid, text, uuid)",
    "nucleo.baja_contrato_proveedor(uuid, uuid, text, uuid)",
    "nucleo.orden_pago_evaluaciones_lectura(uuid, text, uuid, text, uuid)",
    "nucleo.contratos_proveedor_lectura(uuid, text, uuid, text, uuid)",
)
# El umbral lo carga soporte con `scripts/umbral_orden_pago.py`, que corre como `cem_app` sin
# persona (como `fijar_aprobaciones_orden_pago`).
FIJAR = "nucleo.fijar_umbral_orden_pago(bigint, text, date, uuid, uuid, text)"

UPGRADE += "".join(f"revoke all on function {f} from public;\n" for f in INTERNAS + RUTAS)
UPGRADE += f"revoke all on function {FIJAR} from public;\n"
UPGRADE += "".join(f"grant execute on function {f} to cem_app;\n" for f in (*RUTAS, FIJAR, EMITIR))

DOWNGRADE_GUARDA = """
lock table nucleo.orden_pago, nucleo.umbral_orden_pago, nucleo.contrato_proveedor
  in share row exclusive mode;

do $$
begin
  if exists (select 1 from nucleo.orden_pago) or exists (select 1 from nucleo.umbral_orden_pago)
     or exists (select 1 from nucleo.contrato_proveedor) then
    raise exception '0050 no baja con órdenes, umbrales o contratos: se perdería su historia'
      using errcode = 'P0001', hint = 'downgrade_con_datos';
  end if;
end
$$;
"""

DOWNGRADE_FINAL = (
    f"revoke execute on function {EMITIR} from cem_app;\n"
    + "".join(f"drop function {f};\n" for f in (*RUTAS, FIJAR))
    + """
alter table nucleo.orden_pago drop column aprobada_en;
alter table nucleo.orden_pago_version
  drop column alerta_fraccionamiento,
  drop column contrato_id,
  drop column umbral_id,
  drop column franja;
"""
    + "".join(f"drop function {f};\n" for f in INTERNAS)
    + """
drop table nucleo.contrato_proveedor_baja;
drop table nucleo.contrato_proveedor_confirmacion;
drop table nucleo.contrato_proveedor;
drop table nucleo.umbral_orden_pago;
"""
)


def _definicion(firma: str) -> str:
    return (
        op.get_bind()
        .execute(text("select pg_get_functiondef(cast(:f as regprocedure))"), {"f": firma})
        .scalar_one()
    )


def _aplicar(inverso: bool) -> None:
    """Reescribe cada función; las que cambian de firma se crean de nuevo y se borra la
    anterior. Las nuevas quedan sin EXECUTE para public y, preparar y modificar, con el de
    `cem_app` que tenían desde 0049."""
    de, a = (DESPUES, ANTES) if inverso else (ANTES, DESPUES)
    for plantilla, pares in CAMBIOS.items():
        vieja, nueva = plantilla.format(de), plantilla.format(a)
        if inverso:
            pares = [(despues, antes) for antes, despues in reversed(pares)]
        definicion = _definicion(vieja)
        for antes, despues in pares:
            if definicion.count(antes) != 1:
                raise RuntimeError(f"0050: {vieja} no tiene exactamente una vez {antes!r}")
            definicion = definicion.replace(antes, despues)
        op.execute(definicion)
        if vieja != nueva:
            op.execute(f"drop function {vieja}")
            op.execute(f"revoke all on function {nueva} from public")
            if plantilla in (PREPARAR, MODIFICAR):
                op.execute(f"grant execute on function {nueva} to cem_app")


def upgrade() -> None:
    op.execute(GUARDA)
    op.execute(UPGRADE)
    _aplicar(inverso=False)


def downgrade() -> None:
    op.execute(DOWNGRADE_GUARDA)
    _aplicar(inverso=True)
    op.execute(DOWNGRADE_FINAL)
