"""H3: el proveedor versionado, su cuenta destino y el beneficiario de la orden de pago.

Diseño: `doc/diseno/H3_Proveedor_Beneficiario_Cuenta.md` del repositorio CEM, pedido por el
propietario el 29-10-2026 (mensaje del 29-10, puntos 10 a 12): un modelo de proveedor neutral
respecto de las decisiones 1 y 6. Reglas fijas (punto 7): un beneficiario nuevo o un cambio de
cuenta necesitan control fuerte, una aprobación es de una versión, un cambio material invalida la
aprobación anterior y, si falta algo necesario, se cierra.

- **`nucleo.proveedor`**: la identidad estable del proveedor en el consorcio. El CUIT es opcional
  (`nucleo.cuit_valido`, de `0012`) y, si está, único en el consorcio; no cambia (otro CUIT es otro
  proveedor).
- **`nucleo.proveedor_version`**: razón social, rubro, contacto, estado (`activo`, `suspendido`,
  `baja`), documento de respaldo (un `nucleo.documento` del consorcio) y cuenta destino (CBU o CVU
  de 22 dígitos con sus dos verificadores, `nucleo.cbu_valido`; el alias no se admite). Cada
  versión lleva la huella SHA-256 de lo crítico: CUIT, razón social y cuenta. Solo se agrega: la
  vigente es la de número más alto.
- **La orden apunta a una versión del proveedor.** `orden_pago_version.beneficiario` (un texto
  desde `0045`) se reemplaza por `proveedor_id` y `proveedor_version`. En la huella de la orden,
  el lugar del beneficiario lo ocupa la huella de esa versión del proveedor: cambiar la cuenta
  cambia la huella de la orden. `preparar_orden_pago` y `modificar_orden_pago` reciben el
  proveedor (uuid) en lugar del texto.
- **Condición del beneficiario**, guardada en cada versión de la orden al prepararla:
  - `nuevo`: el proveedor no tiene otra orden aprobada (o más adelante en el circuito) en el
    consorcio;
  - `cuenta_cambiada`: la tiene, pero con otra cuenta;
  - `conocido`: la tiene, con esta misma cuenta.
  Qué exige cada condición es la decisión 1; hoy toda orden pasa por revisión.
- **Emitir exige que el proveedor no haya cambiado** (`proveedor_cambiado`): si después de aprobar
  se carga otra versión del proveedor (otra cuenta, otra razón social o la baja), la orden se
  modifica, lo que crea una versión nueva y la vuelve a revisión. Un proveedor que no está
  `activo` no recibe órdenes nuevas ni pagos (`proveedor_inactivo`).
- **Quien firmó la cuenta no aprueba el pago** (`no_es_quien_cargo_la_cuenta`, revisión de app
  #165): la separación de la orden suma a quien firmó cualquier versión del proveedor, hasta la
  que fija la orden, que lleve la cuenta que la orden paga, aunque en esa versión solo haya
  cambiado otro dato (por la cuenta, la identidad guardada y la actual). Solo aprobar: rechazar
  sigue abierto, es la acción segura. No decide la 6: alcanza al ser humano que tiene las dos
  facultades, por el camino que sea.
- **Quién:** el alta y las versiones del proveedor, con `egresos.preparar_orden` y las mismas
  barreras de la orden (`exigir_acto_economico`: modo, sesión reciente con segundo factor e
  identidad verificada). Es el «facultad de preparar = según paquete vigente» del punto 8; una
  facultad propia de proveedores sería la decisión 7 y reemplazaría a esta en las dos funciones.
- **Auditoría:** cada alta y versión escribe su evento con el actor real, sin la cuenta ni el
  texto libre (la razón social y el contacto tampoco van: son datos de un tercero); van la
  versión, el estado, la huella y si cambió la cuenta.

**Lo que no hace.** No valida la cuenta contra el banco ni su titularidad, no mueve dinero, no
fija umbral, franjas ni plazos (decisión 1) y no decide la 6. `cem_app` no recibe permisos: las
rutas van aparte.

**Upgrade** se niega (`upgrade_con_datos`) si hay órdenes de pago: el beneficiario de texto no se
puede convertir en un proveedor. **Downgrade** se niega (`downgrade_con_datos`) si hay órdenes o
proveedores.

Revisión: 0048
Anterior: 0047
"""

from alembic import op
from sqlalchemy import text

revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None

ESTADOS_PROVEEDOR = ("activo", "suspendido", "baja")
# Órdenes que ya pasaron una aprobación completa (diagrama 4 del documento 08). `fallida` no
# cuenta: un pago que falló no prueba que la cuenta sea la correcta.
APROBADAS = ("aprobada", "en_ejecucion", "en_verificacion", "pagada", "conciliada")

_estados = ", ".join(f"'{e}'" for e in ESTADOS_PROVEEDOR)
_aprobadas = ", ".join(f"'{e}'" for e in APROBADAS)

ESENCIALES_0045 = "bigint, text, text, text, text, boolean"
ESENCIALES_0048 = "bigint, text, text, uuid, text, boolean"
NUEVA_VERSION = "nucleo.nueva_version_orden_pago(uuid, uuid, integer, {}, uuid, uuid)"
PREPARAR = "nucleo.preparar_orden_pago({}, uuid, text, uuid)"
MODIFICAR = "nucleo.modificar_orden_pago(uuid, integer, {}, uuid, text, uuid)"
EMITIR = "nucleo.emitir_orden_pago(uuid, integer, uuid, text, uuid)"
APROBAR = "nucleo.registrar_aprobacion_orden_pago(uuid, integer, bytea, uuid, text, uuid)"

FIRMA = ("beneficiario_nuevo text", "proveedor_nuevo uuid")

# Sobre el texto exacto de cada función (0045, con lo que sumó 0046): (antes, después). Las que
# cambian de firma se crean de nuevo y se borra la anterior.
CAMBIOS = {
    NUEVA_VERSION: [
        FIRMA,
        (
            """  declare
    h bytea := nucleo.huella_orden_pago(orden, numero, importe_nuevo, moneda_nueva,
                 btrim(concepto_nuevo), btrim(beneficiario_nuevo), btrim(comprobante_nuevo),
                 usa_reserva);
  begin
    insert into nucleo.orden_pago_version
      (consorcio_id, orden_id, numero, importe_centavos, moneda, concepto, beneficiario,
       comprobante, usa_fondo_reserva, huella, preparada_por, identidad_preparo,
       aprobaciones_requeridas)
    values (c, orden, numero, importe_nuevo, moneda_nueva, btrim(concepto_nuevo),
            btrim(beneficiario_nuevo), btrim(comprobante_nuevo), usa_reserva, h, actor,
            identidad,
            nucleo.aprobaciones_requeridas_orden_pago(c));""",
            """  declare
    pv nucleo.proveedor_version := nucleo.proveedor_vigente(c, proveedor_nuevo);
    h bytea;
  begin
    if pv.estado is distinct from 'activo' then
      raise exception 'el proveedor no está activo' using errcode = 'P0001',
        hint = 'proveedor_inactivo';
    end if;
    -- 0048: el lugar del beneficiario en la huella lo ocupa la huella de la versión del
    -- proveedor; otra cuenta es otra huella.
    h := nucleo.huella_orden_pago(orden, numero, importe_nuevo, moneda_nueva,
           btrim(concepto_nuevo), encode(pv.huella, 'hex'), btrim(comprobante_nuevo),
           usa_reserva);
    insert into nucleo.orden_pago_version
      (consorcio_id, orden_id, numero, importe_centavos, moneda, concepto, proveedor_id,
       proveedor_version, condicion_beneficiario, comprobante, usa_fondo_reserva, huella,
       preparada_por, identidad_preparo, aprobaciones_requeridas)
    values (c, orden, numero, importe_nuevo, moneda_nueva, btrim(concepto_nuevo),
            pv.proveedor_id, pv.numero, nucleo.condicion_beneficiario(c, orden, pv),
            btrim(comprobante_nuevo), usa_reserva, h, actor, identidad,
            nucleo.aprobaciones_requeridas_orden_pago(c));""",
        ),
    ],
    PREPARAR: [
        FIRMA,
        ("beneficiario_nuevo, comprobante_nuevo", "proveedor_nuevo, comprobante_nuevo"),
    ],
    MODIFICAR: [
        FIRMA,
        ("beneficiario_nuevo, comprobante_nuevo", "proveedor_nuevo, comprobante_nuevo"),
        (
            """    if (v.importe_centavos, v.moneda, v.concepto, v.beneficiario, v.comprobante,
        v.usa_fondo_reserva)
       is not distinct from (importe_nuevo, moneda_nueva, btrim(concepto_nuevo),
                             btrim(beneficiario_nuevo), btrim(comprobante_nuevo), usa_reserva)""",
            """    -- 0048: el mismo proveedor con otra versión (otra cuenta) también es un cambio.
    if (v.importe_centavos, v.moneda, v.concepto, v.proveedor_id, v.proveedor_version,
        v.comprobante, v.usa_fondo_reserva)
       is not distinct from (importe_nuevo, moneda_nueva, btrim(concepto_nuevo),
                             proveedor_nuevo, (nucleo.proveedor_vigente(c, proveedor_nuevo)).numero,
                             btrim(comprobante_nuevo), usa_reserva)""",
        ),
    ],
    APROBAR: [
        (
            "    perform nucleo.exigir_decision_sobre_orden(c, o, version_vista, actor,"
            " identidad);\n    if not exists (select 1 from nucleo.orden_pago_version x",
            "    perform nucleo.exigir_decision_sobre_orden(c, o, version_vista, actor,"
            " identidad);\n"
            "    perform nucleo.exigir_no_cargo_la_cuenta(c, orden, version_vista, actor,"
            " identidad);\n    if not exists (select 1 from nucleo.orden_pago_version x",
        ),
    ],
    EMITIR: [
        (
            "    -- A5: quien emite no es ninguno",
            "    perform nucleo.exigir_proveedor_sin_cambios(c, orden, version_vista);\n"
            "    -- A5: quien emite no es ninguno",
        ),
    ],
}

GUARDA = """
do $$
begin
  if exists (select 1 from nucleo.orden_pago) then
    raise exception '0048 no sube con órdenes de pago: el beneficiario de texto no se convierte'
      ' en un proveedor' using errcode = 'P0001', hint = 'upgrade_con_datos';
  end if;
end
$$;
"""

UPGRADE = f"""
-- CBU o CVU: 22 dígitos; el 8 verifica el primer bloque y el 22 el segundo (BCRA). Entidad y
-- sucursal en cero no existen (un CVU lleva 000 y el código del proveedor de pagos).
create function nucleo.cbu_valido(c text) returns boolean
  language sql immutable strict parallel safe
  set search_path = pg_catalog, pg_temp
  as $$
    select case when c ~ '^[0-9]{{22}}$' and substr(c, 1, 7) <> '0000000' then
        (10 - (select sum(substr(c, i::int, 1)::int * w)
                 from unnest(array[7,1,3,9,7,1,3]) with ordinality as t(w, i)) % 10) % 10
          = substr(c, 8, 1)::int
        and (10 - (select sum(substr(c, 8 + i::int, 1)::int * w)
                     from unnest(array[3,9,7,1,3,9,7,1,3,9,7,1,3]) with ordinality as t(w, i))
                  % 10) % 10
          = substr(c, 22, 1)::int
      else false end
  $$;

create type nucleo.estado_proveedor as enum ({_estados});

create table nucleo.proveedor (
  consorcio_id    uuid not null references nucleo.consorcio (id),
  id              uuid not null default plataforma.uuid_v7(),
  cuit            text check (nucleo.cuit_valido(cuit)),
  alta_por        uuid not null references nucleo.persona (id),
  identidad_alta  uuid not null,
  creado_en       timestamptz not null default now(),
  primary key (id),
  unique (consorcio_id, id),
  foreign key (consorcio_id, identidad_alta)
    references nucleo.identidad_humana (consorcio_id, id)
);
create unique index proveedor_cuit_unico on nucleo.proveedor (consorcio_id, cuit)
  where cuit is not null;

create table nucleo.proveedor_version (
  consorcio_id       uuid not null,
  proveedor_id       uuid not null,
  numero             integer not null check (numero > 0),
  razon_social       text not null check (length(btrim(razon_social)) between 1 and 200),
  rubro              text check (length(btrim(rubro)) between 1 and 100),
  contacto           text check (length(btrim(contacto)) between 1 and 200),
  estado             nucleo.estado_proveedor not null,
  documento_id       uuid,
  cuenta_destino     text check (nucleo.cbu_valido(cuenta_destino)),
  huella             bytea not null check (length(huella) = 32),
  preparada_por      uuid not null references nucleo.persona (id),
  identidad_preparo  uuid not null,
  creada_en          timestamptz not null default now(),
  primary key (proveedor_id, numero),
  unique (proveedor_id, numero, huella),
  foreign key (consorcio_id, proveedor_id) references nucleo.proveedor (consorcio_id, id),
  foreign key (consorcio_id, documento_id) references nucleo.documento (consorcio_id, id),
  foreign key (consorcio_id, identidad_preparo)
    references nucleo.identidad_humana (consorcio_id, id)
);

alter table nucleo.proveedor enable row level security;
alter table nucleo.proveedor force row level security;
alter table nucleo.proveedor_version enable row level security;
alter table nucleo.proveedor_version force row level security;
create policy por_consorcio on nucleo.proveedor for select
  using (consorcio_id = plataforma.consorcio_actual());
create policy por_consorcio on nucleo.proveedor_version for select
  using (consorcio_id = plataforma.consorcio_actual());

-- Solo se agregan filas, como la historia de la orden.
create trigger historia_proveedor before update or delete on nucleo.proveedor
  for each row execute function nucleo.negar_cambio_historia_orden_pago();
create trigger historia_proveedor_version before update or delete on nucleo.proveedor_version
  for each row execute function nucleo.negar_cambio_historia_orden_pago();
create trigger historia_proveedor_truncate before truncate on nucleo.proveedor
  for each statement execute function nucleo.negar_cambio_historia_orden_pago();
create trigger historia_proveedor_version_truncate before truncate on nucleo.proveedor_version
  for each statement execute function nucleo.negar_cambio_historia_orden_pago();

alter table nucleo.orden_pago_version
  drop column beneficiario,
  add column proveedor_id uuid not null,
  add column proveedor_version integer not null,
  add column condicion_beneficiario text not null
    check (condicion_beneficiario in ('nuevo', 'cuenta_cambiada', 'conocido')),
  add foreign key (consorcio_id, proveedor_id) references nucleo.proveedor (consorcio_id, id),
  add foreign key (proveedor_id, proveedor_version)
    references nucleo.proveedor_version (proveedor_id, numero);

create function nucleo.huella_proveedor(proveedor uuid, numero integer, cuit text,
                                        razon_social text, cuenta text)
  returns bytea
  language sql immutable
  set search_path = pg_catalog, pg_temp
  as $$
    select sha256(convert_to(jsonb_build_object(
      'proveedor', proveedor, 'version', numero, 'cuit', cuit, 'razon_social', razon_social,
      'cuenta_destino', cuenta)::text, 'UTF8'))
  $$;

-- La versión vigente del proveedor del consorcio. Bloquea la fila del proveedor (FOR SHARE)
-- hasta el fin de la transacción: una versión nueva concurrente espera.
create function nucleo.proveedor_vigente(c uuid, proveedor uuid)
  returns nucleo.proveedor_version
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    v nucleo.proveedor_version;
  begin
    perform 1 from nucleo.proveedor p where p.consorcio_id = c and p.id = proveedor for share;
    if not found then
      raise exception 'el proveedor no existe' using errcode = 'P0001',
        hint = 'proveedor_inexistente';
    end if;
    select * into v from nucleo.proveedor_version x
     where x.proveedor_id = proveedor order by x.numero desc limit 1;
    return v;
  end
  $$;

create function nucleo.condicion_beneficiario(c uuid, orden uuid, pv nucleo.proveedor_version)
  returns text
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    with previas as (
      select pv2.cuenta_destino
        from nucleo.orden_pago o2
        join nucleo.orden_pago_version v2
          on v2.orden_id = o2.id and v2.numero = o2.version_vigente
        join nucleo.proveedor_version pv2
          on pv2.proveedor_id = v2.proveedor_id and pv2.numero = v2.proveedor_version
       where o2.consorcio_id = c and o2.id <> orden and v2.proveedor_id = pv.proveedor_id
         and o2.estado in ({_aprobadas}))
    select case
      when not exists (select 1 from previas) then 'nuevo'
      when exists (select 1 from previas p where p.cuenta_destino is not distinct from
                                                 pv.cuenta_destino) then 'conocido'
      else 'cuenta_cambiada' end
  $$;

create function nucleo.exigir_proveedor_sin_cambios(c uuid, orden uuid, numero integer)
  returns void
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    v nucleo.orden_pago_version;
    pv nucleo.proveedor_version;
  begin
    select * into v from nucleo.orden_pago_version x
     where x.orden_id = orden and x.numero = exigir_proveedor_sin_cambios.numero;
    pv := nucleo.proveedor_vigente(c, v.proveedor_id);
    if pv.numero is distinct from v.proveedor_version then
      raise exception 'el proveedor cambió después de preparar la orden' using errcode = 'P0001',
        hint = 'proveedor_cambiado';
    end if;
    if pv.estado is distinct from 'activo' then
      raise exception 'el proveedor no está activo' using errcode = 'P0001',
        hint = 'proveedor_inactivo';
    end if;
  end
  $$;

-- Revisión de app #165: quien cargó la cuenta que paga la orden (en cualquier versión del
-- proveedor hasta la que fija la orden) no la aprueba. Compara la cuenta, la identidad guardada
-- y la actual, como la separación de la orden.
create function nucleo.exigir_no_cargo_la_cuenta(c uuid, orden uuid, numero integer, actor uuid,
                                                 identidad uuid)
  returns void
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  begin
    if exists (
         select 1
           from nucleo.orden_pago_version v
           join nucleo.proveedor_version fija
             on fija.proveedor_id = v.proveedor_id and fija.numero = v.proveedor_version
           join nucleo.proveedor_version x
             on x.proveedor_id = v.proveedor_id and x.numero <= fija.numero
            and x.cuenta_destino is not distinct from fija.cuenta_destino
          where v.orden_id = orden and v.numero = exigir_no_cargo_la_cuenta.numero
            and (x.preparada_por = actor or x.identidad_preparo = identidad
                 or nucleo.identidad_humana_de(c, x.preparada_por) = identidad)) then
      raise exception 'quien cargó la cuenta del proveedor no aprueba este pago'
        using errcode = 'P0001', hint = 'no_es_quien_cargo_la_cuenta';
    end if;
  end
  $$;

create function nucleo.auditar_proveedor(c uuid, accion text, proveedor uuid, actor uuid,
                                         antes jsonb, despues jsonb)
  returns void
  language sql volatile
  set search_path = pg_catalog, pg_temp
  as $$
    insert into auditoria.eventos
      (consorcio_id, actor_persona_id, origen, accion, entidad, entidad_id, antes, despues,
       hash_previo, hash)
    values (c, actor, 'api', accion, 'proveedor', proveedor, antes, despues, '', '')
  $$;

create function nucleo.insertar_version_proveedor(
  c uuid, proveedor uuid, numero integer, cuit text, razon_social_nueva text, rubro_nuevo text,
  contacto_nuevo text, estado_nuevo nucleo.estado_proveedor, documento uuid, cuenta text,
  actor uuid, identidad uuid)
  returns bytea
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    h bytea;
  begin
    if razon_social_nueva is null or length(btrim(razon_social_nueva)) not between 1 and 200 then
      raise exception 'la razón social es obligatoria (hasta 200 caracteres)'
        using errcode = 'P0001', hint = 'razon_social_invalida';
    end if;
    if cuenta is not null and not nucleo.cbu_valido(cuenta) then
      raise exception 'la cuenta destino no es un CBU o CVU válido' using errcode = 'P0001',
        hint = 'cuenta_invalida';
    end if;
    if documento is not null and not exists (
         select 1 from nucleo.documento d
          where d.consorcio_id = c and d.id = documento and d.estado = 'vigente') then
      raise exception 'el documento no existe en este consorcio' using errcode = 'P0001',
        hint = 'documento_invalido';
    end if;
    h := nucleo.huella_proveedor(proveedor, numero, cuit, btrim(razon_social_nueva), cuenta);
    insert into nucleo.proveedor_version
      (consorcio_id, proveedor_id, numero, razon_social, rubro, contacto, estado, documento_id,
       cuenta_destino, huella, preparada_por, identidad_preparo)
    values (c, proveedor, numero, btrim(razon_social_nueva), btrim(rubro_nuevo),
            btrim(contacto_nuevo), estado_nuevo, documento, cuenta, h, actor, identidad);
    return h;
  end
  $$;

create function nucleo.alta_proveedor(cuit_nuevo text, razon_social_nueva text,
                                      rubro_nuevo text, contacto_nuevo text, documento uuid,
                                      cuenta text, actor uuid, origen_pedido text, sesion uuid)
  returns uuid
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    p uuid := plataforma.uuid_v7();
    identidad uuid;
    h bytea;
  begin
    identidad := nucleo.exigir_acto_economico(c, actor, sesion, 'egresos.preparar_orden',
                                              origen_pedido);
    if cuit_nuevo is not null and not nucleo.cuit_valido(cuit_nuevo) then
      raise exception 'el CUIT no es válido' using errcode = 'P0001', hint = 'cuit_invalido';
    end if;
    begin
      insert into nucleo.proveedor (consorcio_id, id, cuit, alta_por, identidad_alta)
      values (c, p, cuit_nuevo, actor, identidad);
    exception when unique_violation then
      raise exception 'ya hay un proveedor con ese CUIT en este consorcio'
        using errcode = 'P0001', hint = 'cuit_repetido';
    end;
    h := nucleo.insertar_version_proveedor(c, p, 1, cuit_nuevo, razon_social_nueva, rubro_nuevo,
           contacto_nuevo, 'activo', documento, cuenta, actor, identidad);
    perform nucleo.auditar_proveedor(c, 'consorcio.proveedor.alta', p, actor, null,
      jsonb_build_object('version', 1, 'estado', 'activo', 'huella', encode(h, 'hex'),
                         'con_cuit', cuit_nuevo is not null, 'con_cuenta', cuenta is not null));
    return p;
  end
  $$;

create function nucleo.nueva_version_proveedor(proveedor uuid, version_esperada integer,
                                               razon_social_nueva text, rubro_nuevo text,
                                               contacto_nuevo text,
                                               estado_nuevo nucleo.estado_proveedor,
                                               documento uuid, cuenta text, actor uuid,
                                               origen_pedido text, sesion uuid)
  returns integer
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    identidad uuid;
    cuit_proveedor text;
    v nucleo.proveedor_version;
    h bytea;
  begin
    identidad := nucleo.exigir_acto_economico(c, actor, sesion, 'egresos.preparar_orden',
                                              origen_pedido);
    select p.cuit into cuit_proveedor from nucleo.proveedor p
     where p.consorcio_id = c and p.id = proveedor for update;
    if not found then
      raise exception 'el proveedor no existe' using errcode = 'P0001',
        hint = 'proveedor_inexistente';
    end if;
    v := nucleo.proveedor_vigente(c, proveedor);
    if version_esperada is distinct from v.numero then
      raise exception 'el proveedor cambió mientras tanto' using errcode = 'P0001',
        hint = 'version_desactualizada';
    end if;
    if (v.razon_social, v.rubro, v.contacto, v.estado, v.documento_id, v.cuenta_destino)
       is not distinct from (btrim(razon_social_nueva), btrim(rubro_nuevo),
                             btrim(contacto_nuevo), estado_nuevo, documento, cuenta) then
      raise exception 'los datos del proveedor no cambiaron' using errcode = 'P0001',
        hint = 'sin_cambios';
    end if;
    h := nucleo.insertar_version_proveedor(c, proveedor, v.numero + 1, cuit_proveedor,
           razon_social_nueva, rubro_nuevo, contacto_nuevo, estado_nuevo, documento, cuenta,
           actor, identidad);
    perform nucleo.auditar_proveedor(c, 'consorcio.proveedor.modificar', proveedor, actor,
      jsonb_build_object('version', v.numero, 'estado', v.estado,
                         'huella', encode(v.huella, 'hex')),
      jsonb_build_object('version', v.numero + 1, 'estado', estado_nuevo,
                         'huella', encode(h, 'hex'),
                         'cambio_cuenta', v.cuenta_destino is distinct from cuenta));
    return v.numero + 1;
  end
  $$;
"""

INTERNAS = (
    "nucleo.cbu_valido(text)",
    "nucleo.huella_proveedor(uuid, integer, text, text, text)",
    "nucleo.proveedor_vigente(uuid, uuid)",
    "nucleo.condicion_beneficiario(uuid, uuid, nucleo.proveedor_version)",
    "nucleo.exigir_proveedor_sin_cambios(uuid, uuid, integer)",
    "nucleo.exigir_no_cargo_la_cuenta(uuid, uuid, integer, uuid, uuid)",
    "nucleo.auditar_proveedor(uuid, text, uuid, uuid, jsonb, jsonb)",
    "nucleo.insertar_version_proveedor(uuid, uuid, integer, text, text, text, text,"
    " nucleo.estado_proveedor, uuid, text, uuid, uuid)",
    "nucleo.alta_proveedor(text, text, text, text, uuid, text, uuid, text, uuid)",
    "nucleo.nueva_version_proveedor(uuid, integer, text, text, text, nucleo.estado_proveedor,"
    " uuid, text, uuid, text, uuid)",
)

UPGRADE += "".join(f"revoke all on function {f} from public;\n" for f in INTERNAS)

DOWNGRADE_GUARDA = """
do $$
begin
  if exists (select 1 from nucleo.orden_pago) or exists (select 1 from nucleo.proveedor) then
    raise exception '0048 no baja con órdenes o proveedores: se perdería su historia'
      using errcode = 'P0001', hint = 'downgrade_con_datos';
  end if;
end
$$;
"""

DOWNGRADE_FINAL = """
alter table nucleo.orden_pago_version
  drop column condicion_beneficiario,
  drop column proveedor_version,
  drop column proveedor_id,
  add column beneficiario text not null check (length(btrim(beneficiario)) between 1 and 200);
drop function nucleo.nueva_version_proveedor(uuid, integer, text, text, text,
  nucleo.estado_proveedor, uuid, text, uuid, text, uuid);
drop function nucleo.alta_proveedor(text, text, text, text, uuid, text, uuid, text, uuid);
drop function nucleo.insertar_version_proveedor(uuid, uuid, integer, text, text, text, text,
  nucleo.estado_proveedor, uuid, text, uuid, uuid);
drop function nucleo.auditar_proveedor(uuid, text, uuid, uuid, jsonb, jsonb);
drop function nucleo.exigir_no_cargo_la_cuenta(uuid, uuid, integer, uuid, uuid);
drop function nucleo.exigir_proveedor_sin_cambios(uuid, uuid, integer);
drop function nucleo.condicion_beneficiario(uuid, uuid, nucleo.proveedor_version);
drop function nucleo.proveedor_vigente(uuid, uuid);
drop function nucleo.huella_proveedor(uuid, integer, text, text, text);
drop table nucleo.proveedor_version;
drop table nucleo.proveedor;
drop type nucleo.estado_proveedor;
drop function nucleo.cbu_valido(text);
"""


def _definicion(firma: str) -> str:
    return (
        op.get_bind()
        .execute(text("select pg_get_functiondef(cast(:f as regprocedure))"), {"f": firma})
        .scalar_one()
    )


def _reemplazar(definicion: str, firma: str, pares: list[tuple[str, str]]) -> str:
    for antes, despues in pares:
        if definicion.count(antes) != 1:
            raise RuntimeError(f"0048: {firma} no tiene exactamente una vez {antes!r}")
        definicion = definicion.replace(antes, despues)
    return definicion


def _aplicar(de: str, a: str, inverso: bool) -> None:
    """Reescribe cada función de la firma `de` a la firma `a` (las que no cambian de firma, en
    el lugar). Las nuevas quedan sin EXECUTE para public, como las de 0045."""
    for plantilla, pares in CAMBIOS.items():
        vieja, nueva = plantilla.format(de), plantilla.format(a)
        if inverso:
            pares = [(despues, antes) for antes, despues in reversed(pares)]
        op.execute(_reemplazar(_definicion(vieja), vieja, pares))
        if vieja != nueva:
            op.execute(f"drop function {vieja}")
            op.execute(f"revoke all on function {nueva} from public")


def upgrade() -> None:
    op.execute(GUARDA)
    op.execute(UPGRADE)
    _aplicar(ESENCIALES_0045, ESENCIALES_0048, inverso=False)


def downgrade() -> None:
    op.execute(DOWNGRADE_GUARDA)
    _aplicar(ESENCIALES_0048, ESENCIALES_0045, inverso=True)
    op.execute(DOWNGRADE_FINAL)
