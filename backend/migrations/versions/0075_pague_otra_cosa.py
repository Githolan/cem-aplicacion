"""«Pagué otra cosa» y «Volver a pagar» (`Rediseno_Circuito_Pagos_2026-11-06.md`, §7 y §9,
repositorio CEM; decisión §12 del propietario del 05-11-2026).

**`nucleo.diferencia_pago_manual`.** Un pago de otro importe o a otra cuenta no se podía registrar
(`no_coincide_con_la_orden`), y la única salida era declarar que no se pagó, que es falso. Ahora
quien transfirió registra lo real. La orden pasa a En verificación y **no** queda pagada: solo la
resolución del Consejo la mueve. Una por ejecución. `tipo` lo deduce la base comparando con la
ejecución, nunca lo dice la persona: una moneda distinta cuenta como otro importe. `importe_real`
es `NUMERIC(18,2)`, en pesos, y se pasa a centavos para comparar con `ejecucion_orden_pago`.

**La fila no se borra y solo cambia una vez.** `negar_cambio_diferencia_pago` deja completar la
resolución y después la recuperación, cada una una sola vez: el resto sigue inmutable, como la
historia de la orden.

**Quien resuelve** (`resolver_diferencia_pago_manual`, `recuperar_diferencia_pago_manual`):
`egresos.aprobar_orden`, gesto y la separación de `conciliar_pago_manual`. `exigir_no_ejecuto` ya
cubre a quien registró la diferencia: registrarla exige haber abierto la cuenta.

**Otra cuenta, solo reclamar:** lo niega la función (`solo_reclamo`) y, además, un `check` de la
tabla. Otra moneda tampoco se da por pagada: no hay cómo decir cuánto falta, y la liquidación no
mezcla monedas. Eso lo niega solo la función.

**Mientras haya diferencia nada más entra.** `exigir_sin_pago_vigente` la cuenta como un pago
vigente: niega abrir la cuenta, registrar, corregir, declarar y confirmar, y con eso los `admite_*`
de `0072`. Una declaración anterior queda superada, como con un registro.

**Liquidación (§17).** Antes de esta migración una orden pagada se liquidaba siempre por
`ejecucion_orden_pago.importe_centavos`: el importe aprobado, que era el único que se podía pagar.
`agregar_gasto_liquidacion` y `ordenes_liquidables_lectura` pasan a `importe_pagado_ejecucion`: el
real si el Consejo lo dio por pagado; si no, el mismo de siempre. `fecha_pago_ejecucion` buscaba
solo un registro conforme: sin esto, una orden pagada por lo real no tenía fecha y nunca entraba
en un período (`pagada_despues_del_periodo`). Las órdenes sin diferencia no cambian.

**Lo que falta.** Pagada de menos, quien prepara ve «Preparar la orden por lo que falta» (hecho
`pagada_de_menos`). No se crea sola, y por eso `acciones_de_ordenes_lectura` lee también esas
órdenes pagadas.

**Lectura** (`diferencias_pago_manual_lectura`): con la barrera de egresos, y la cuenta solo con sus
últimos dígitos: `cuenta_real` no sale nunca de la base.

**Exportación económica (`0069`):** `exportar_pagos` suma `diferencia_pago`, con los últimos dígitos
de la cuenta y la explicación sin cuentas.

**Cotejo (`0074`):** `cotejo_pago` cita `pago_manual`, y una diferencia no es un registro: su
transferencia todavía no se coteja. `pagos_del_cotejo` deja de ofrecer el registro observado que la
diferencia reemplazó, porque no es lo que salió del banco.

**Volver a pagar (§9).** `modificar_orden_pago` acepta desde `fallida` una versión sin cambios, que
pasa otra vez por aprobación. Desde cualquier otro estado sigue dando `sin_cambios`.

Downgrade: se niega (`downgrade_con_datos`) si hay alguna diferencia. Sin eso, vuelve a las
definiciones anteriores.

Revisión: 0075
Anterior: 0074
"""

from alembic import op
from sqlalchemy import text

revision = "0075"
down_revision = "0074"
branch_labels = None
depends_on = None

VIGENCIA_APROBACION_DIAS = 7
"""La de `0050`, `0055` y `0067`."""
CATEGORIA = "facturas_y_comprobantes"
DIGITOS_VISIBLES = 4
"""Los de `DIGITOS_VISIBLES_CUENTA`, en `contratos/`."""

SIN_PAGO = "nucleo.exigir_sin_pago_vigente(uuid)"
HECHOS = "nucleo.hechos_de_pago_manual(uuid, nucleo.orden_pago)"
ACCIONES = "nucleo.acciones_de_ordenes_lectura(uuid, uuid, text, uuid)"
CONSULTA = "nucleo.pagos_consulta_lectura(text, text[], integer, integer, text, uuid, text, uuid)"
DECLARACIONES = "nucleo.declaraciones_pago_no_realizado_lectura(uuid, text, uuid, text, uuid)"
POR_ACLARAR = "nucleo.pagos_por_aclarar(uuid, text, uuid)"
FECHA = "nucleo.fecha_pago_ejecucion(uuid, uuid)"
AGREGAR = "nucleo.agregar_gasto_liquidacion(uuid, uuid, text, uuid, text, uuid)"
LIQUIDABLES = "nucleo.ordenes_liquidables_lectura(uuid, text, uuid)"
MODIFICAR = (
    "nucleo.modificar_orden_pago(uuid, integer, bigint, text, text, uuid, text, boolean, uuid,"
    " uuid, text, uuid)"
)
EXPORTAR = "nucleo.exportar_pagos(uuid, uuid)"
COTEJO = "nucleo.pagos_del_cotejo(uuid)"

IMPORTE_PAGADO = "nucleo.importe_pagado_ejecucion(c, e.id)"

TABLA = """
create table nucleo.diferencia_pago_manual (
  consorcio_id          uuid not null,
  id                    uuid not null default plataforma.uuid_v7(),
  orden_id              uuid not null,
  ejecucion_id          uuid not null unique,
  tipo                  text not null check (tipo in ('importe', 'cuenta', 'ambos')),
  importe_real          numeric(18, 2) not null check (importe_real > 0),
  moneda                text not null check (moneda ~ '^[A-Z]{3}$'),
  cuenta_real           text not null check (cuenta_real ~ '^[0-9]{22}$'),
  numero_operacion      text not null,
  fecha_operacion       date not null,
  documento_id          uuid not null,
  version_documento_id  uuid not null references nucleo.version_documento (id),
  -- Texto libre: no va a la auditoría.
  explicacion           text not null check (length(btrim(explicacion)) between 1 and 300),
  fuera_de_termino      boolean not null,
  registrada_por        uuid not null references nucleo.persona (id),
  identidad_registro    uuid not null,
  registrada_en         timestamptz not null default clock_timestamp(),
  paso                  bigint not null default nextval('nucleo.paso_pago_manual'),
  resolucion            text check (resolucion in ('pagada_por_lo_real', 'reclamo')),
  resuelta_por          uuid references nucleo.persona (id),
  identidad_resolvio    uuid,
  resuelta_en           timestamptz,
  recuperada_por        uuid references nucleo.persona (id),
  identidad_recupero    uuid,
  recuperada_en         timestamptz,
  primary key (id),
  unique (consorcio_id, id),
  foreign key (consorcio_id, orden_id) references nucleo.orden_pago (consorcio_id, id),
  foreign key (consorcio_id, ejecucion_id)
    references nucleo.ejecucion_orden_pago (consorcio_id, id),
  foreign key (consorcio_id, numero_operacion, ejecucion_id)
    references nucleo.operacion_pago_manual (consorcio_id, numero_operacion, ejecucion_id),
  foreign key (consorcio_id, documento_id, ejecucion_id)
    references nucleo.comprobante_pago_manual (consorcio_id, documento_id, ejecucion_id),
  foreign key (consorcio_id, identidad_registro)
    references nucleo.identidad_humana (consorcio_id, id),
  foreign key (consorcio_id, identidad_resolvio)
    references nucleo.identidad_humana (consorcio_id, id),
  foreign key (consorcio_id, identidad_recupero)
    references nucleo.identidad_humana (consorcio_id, id),
  -- La plata que fue a otra cuenta no se da por pagada: ni aunque una función se equivoque.
  constraint diferencia_otra_cuenta_solo_reclamo
    check (resolucion is distinct from 'pagada_por_lo_real' or tipo = 'importe'),
  constraint diferencia_resolucion_completa check (
    (resolucion is null and resuelta_por is null and identidad_resolvio is null
     and resuelta_en is null)
    or (resolucion is not null and resuelta_por is not null and identidad_resolvio is not null
        and resuelta_en is not null)),
  constraint diferencia_recuperacion_completa check (
    (recuperada_en is null and recuperada_por is null and identidad_recupero is null)
    or (resolucion = 'reclamo' and recuperada_en is not null and recuperada_por is not null
        and identidad_recupero is not null))
);
alter table nucleo.diferencia_pago_manual enable row level security;
alter table nucleo.diferencia_pago_manual force row level security;
create policy por_consorcio on nucleo.diferencia_pago_manual for select
  using (consorcio_id = plataforma.consorcio_actual());
revoke all on nucleo.diferencia_pago_manual from public, cem_app;

-- Solo se agrega la resolución y después la recuperación, cada una una vez.
create function nucleo.negar_cambio_diferencia_pago() returns trigger
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    resolucion_c constant text[] :=
      array['resolucion', 'resuelta_por', 'identidad_resolvio', 'resuelta_en'];
    recuperacion_c constant text[] :=
      array['recuperada_por', 'identidad_recupero', 'recuperada_en'];
  begin
    if (to_jsonb(old) - resolucion_c - recuperacion_c)
       is distinct from (to_jsonb(new) - resolucion_c - recuperacion_c)
       or (old.resolucion is not null
           and (old.resolucion, old.resuelta_por, old.identidad_resolvio, old.resuelta_en)
               is distinct from
               (new.resolucion, new.resuelta_por, new.identidad_resolvio, new.resuelta_en))
       or (old.recuperada_en is not null
           and (old.recuperada_por, old.identidad_recupero, old.recuperada_en)
               is distinct from
               (new.recuperada_por, new.identidad_recupero, new.recuperada_en)) then
      raise exception 'la historia de una orden de pago solo se agrega'
        using errcode = 'P0001', hint = 'historia_inmutable';
    end if;
    return new;
  end
  $$;
revoke all on function nucleo.negar_cambio_diferencia_pago() from public;

create trigger historia_diferencia_pago_manual before update on nucleo.diferencia_pago_manual
  for each row execute function nucleo.negar_cambio_diferencia_pago();
create trigger historia_diferencia_pago_manual_borrar before delete
  on nucleo.diferencia_pago_manual
  for each row execute function nucleo.negar_cambio_historia_orden_pago();
create trigger historia_diferencia_pago_manual_truncate before truncate
  on nucleo.diferencia_pago_manual
  for each statement execute function nucleo.negar_cambio_historia_orden_pago();
create trigger explicacion_sin_cuenta before insert on nucleo.diferencia_pago_manual
  for each row execute function nucleo.negar_cuenta_en_motivo('explicacion');
"""

FUNCIONES = f"""
-- En centavos, para comparar con la ejecución: `numeric(18,2)` por 100 es un entero exacto.
create function nucleo.centavos_de(importe numeric)
  returns bigint
  language sql immutable strict
  set search_path = pg_catalog, pg_temp
  as $$ select (importe * 100)::bigint $$;

-- Lo que realmente costó la ejecución pagada: lo real, si el Consejo lo dio por pagado.
create function nucleo.importe_pagado_ejecucion(c uuid, ejecucion uuid)
  returns bigint
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select coalesce(
      (select nucleo.centavos_de(f.importe_real) from nucleo.diferencia_pago_manual f
        where f.consorcio_id = c and f.ejecucion_id = ejecucion
          and f.resolucion = 'pagada_por_lo_real'),
      (select e.importe_centavos from nucleo.ejecucion_orden_pago e
        where e.consorcio_id = c and e.id = ejecucion))
  $$;

create function nucleo.registrar_diferencia_pago_manual(orden uuid, version_vista integer,
                                                        huella_vista bytea, numero text,
                                                        fecha date, importe numeric,
                                                        moneda_pago text, cuenta text,
                                                        documento uuid, explicacion_nueva text,
                                                        actor uuid, origen_pedido text,
                                                        sesion uuid)
  returns uuid
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    identidad uuid;
    e nucleo.ejecucion_orden_pago;
    o nucleo.orden_pago;
    anterior nucleo.pago_manual;
    n text := upper(regexp_replace(coalesce(numero, ''), '[^A-Za-z0-9]', '', 'g'));
    cuenta_escrita text := regexp_replace(coalesce(cuenta, ''), '[^0-9]', '', 'g');
    otro_importe boolean;
    otra_cuenta boolean;
    tz text;
    archivo uuid;
    atada uuid;
    tarde boolean;
    nueva uuid;
  begin
    identidad := nucleo.exigir_acto_economico(c, actor, sesion, 'egresos.ejecutar_pago',
                                              origen_pedido);
    e := nucleo.orden_para_pago_manual(c, orden, version_vista, huella_vista);
    select * into o from nucleo.orden_pago x where x.consorcio_id = c and x.id = orden;
    perform nucleo.exigir_no_aprobo(c, orden, e.version, actor, identidad);
    anterior := nucleo.exigir_sin_pago_vigente(e.id);
    -- Antes de comparar la cuenta escrita, como al registrar: no sirve para probar cuentas.
    if not exists (select 1 from nucleo.apertura_cuenta_pago_manual a
                    where a.ejecucion_id = e.id and a.identidad_abrio = identidad) then
      raise exception 'registra el pago quien vio la cuenta en el acto de ejecución'
        using errcode = 'P0001', hint = 'no_es_quien_ejecuto';
    end if;
    if n !~ '^[A-Z0-9]{{4,40}}$' or nucleo.contiene_cbu(n) then
      raise exception 'el número de operación no es válido' using errcode = 'P0001',
        hint = 'numero_operacion_invalido';
    end if;
    otro_importe := nucleo.centavos_de(importe) is distinct from e.importe_centavos
                    or moneda_pago is distinct from e.moneda;
    otra_cuenta := cuenta_escrita <> e.cuenta_destino;
    if not otro_importe and not otra_cuenta then
      raise exception 'lo pagado es lo que se aprobó' using errcode = 'P0001',
        hint = 'sin_diferencia';
    end if;
    select k.zona_horaria into tz from nucleo.consorcio k where k.id = c;
    if fecha is null or fecha < (e.creada_en at time zone tz)::date
       or fecha > nucleo.hoy_del_consorcio(c) then
      raise exception 'la fecha de la operación no es válida' using errcode = 'P0001',
        hint = 'fecha_operacion_invalida';
    end if;
    tarde := fecha > ((o.aprobada_en + make_interval(days => {VIGENCIA_APROBACION_DIAS}))
                      at time zone tz)::date;
    -- La misma evidencia que `registrar_pago_manual`: el último archivo que pasó el antivirus, de
    -- un comprobante cargado desde el pago de esta orden después del acto de ejecución.
    select v.id into archivo
      from nucleo.documento d
      join nucleo.version_documento v on v.consorcio_id = d.consorcio_id and v.documento_id = d.id
     where d.consorcio_id = c and d.id = documento and d.estado = 'vigente'
       and d.categoria = '{CATEGORIA}' and v.estado = 'disponible'
     order by v.analizada_en desc, v.id desc limit 1;
    if archivo is null then
      raise exception 'el comprobante no existe o no pasó el antivirus' using errcode = 'P0001',
        hint = 'comprobante_invalido';
    end if;
    if not exists (select 1 from nucleo.documento d
                    where d.consorcio_id = c and d.id = documento
                      and d.orden_comprobante = orden) then
      raise exception 'el comprobante no se cargó desde el pago de esta orden'
        using errcode = 'P0001', hint = 'comprobante_invalido';
    end if;
    insert into nucleo.comprobante_pago_manual (consorcio_id, documento_id, ejecucion_id)
    values (c, documento, e.id)
    on conflict (consorcio_id, documento_id) do nothing;
    select x.ejecucion_id into atada from nucleo.comprobante_pago_manual x
     where x.consorcio_id = c and x.documento_id = documento;
    if atada is distinct from e.id then
      raise exception 'ese comprobante ya respalda otro pago' using errcode = 'P0001',
        hint = 'comprobante_de_otra_orden';
    end if;
    if not exists (
         select 1 from nucleo.documento d
          where d.consorcio_id = c and d.id = documento
            and d.cargado_en >= (select min(a.abierta_en)
                                   from nucleo.apertura_cuenta_pago_manual a
                                  where a.consorcio_id = c and a.ejecucion_id = e.id)) then
      raise exception 'el comprobante es anterior al acto de ejecución'
        using errcode = 'P0001', hint = 'comprobante_invalido';
    end if;
    insert into nucleo.operacion_pago_manual (consorcio_id, numero_operacion, ejecucion_id)
    values (c, n, e.id)
    on conflict (consorcio_id, numero_operacion) do nothing;
    select x.ejecucion_id into atada from nucleo.operacion_pago_manual x
     where x.consorcio_id = c and x.numero_operacion = n;
    if atada is distinct from e.id then
      raise exception 'ese número de operación ya respalda otra orden' using errcode = 'P0001',
        hint = 'operacion_de_otra_orden';
    end if;
    insert into nucleo.diferencia_pago_manual
      (consorcio_id, orden_id, ejecucion_id, tipo, importe_real, moneda, cuenta_real,
       numero_operacion, fecha_operacion, documento_id, version_documento_id, explicacion,
       fuera_de_termino, registrada_por, identidad_registro)
    values (c, orden, e.id,
            case when otro_importe and otra_cuenta then 'ambos'
                 when otra_cuenta then 'cuenta' else 'importe' end,
            importe, moneda_pago, cuenta_escrita, n, fecha, documento, archivo,
            btrim(explicacion_nueva), tarde, actor, identidad)
    returning id into nueva;
    if o.estado = 'en_ejecucion' then
      update nucleo.orden_pago set estado = 'en_verificacion', actualizada_en = now()
       where id = orden;
    end if;
    perform nucleo.auditar_orden_pago(c, 'consorcio.orden_pago.registrar_diferencia_pago_manual',
      orden, actor, jsonb_build_object('estado', o.estado),
      jsonb_build_object('estado', 'en_verificacion', 'version', e.version, 'ejecucion', e.id,
                         'diferencia', nueva, 'corrige_a', anterior.id, 'numero_operacion', n,
                         'fecha_operacion', fecha, 'importe_real', importe,
                         'moneda', moneda_pago,
                         'cuenta_ultimos_digitos', right(cuenta_escrita, {DIGITOS_VISIBLES}),
                         'comprobante', documento, 'archivo_comprobante', archivo,
                         'fuera_de_termino', tarde));
    return nueva;
  end
  $$;

-- La diferencia de la ejecución abierta y la separación de `conciliar_pago_manual`.
create function nucleo.diferencia_para_resolver(c uuid, o nucleo.orden_pago, diferencia uuid,
                                                actor uuid, identidad uuid)
  returns nucleo.diferencia_pago_manual
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    e nucleo.ejecucion_orden_pago := nucleo.ejecucion_manual_abierta(c, o);
    f nucleo.diferencia_pago_manual;
  begin
    select * into f from nucleo.diferencia_pago_manual x
     where x.consorcio_id = c and x.id = diferencia and x.ejecucion_id = e.id;
    if not found then
      raise exception 'esa diferencia no es de esta orden' using errcode = 'P0001',
        hint = 'no_encontrado';
    end if;
    perform nucleo.exigir_no_ejecuto(c, e.id, actor, identidad);
    perform nucleo.exigir_no_preparo(c, o.id, actor, identidad);
    return f;
  end
  $$;

create function nucleo.resolver_diferencia_pago_manual(orden uuid, diferencia uuid,
                                                       resolucion_nueva text, actor uuid,
                                                       origen_pedido text, sesion uuid)
  returns text
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    identidad uuid;
    o nucleo.orden_pago;
    e nucleo.ejecucion_orden_pago;
    f nucleo.diferencia_pago_manual;
    estado_nuevo text;
  begin
    identidad := nucleo.exigir_acto_economico(c, actor, sesion, 'egresos.aprobar_orden',
                                              origen_pedido);
    o := nucleo.orden_pago_bloqueada(c, orden);
    f := nucleo.diferencia_para_resolver(c, o, diferencia, actor, identidad);
    e := nucleo.ejecucion_manual_abierta(c, o);
    if f.resolucion is not null then
      raise exception 'esa diferencia ya está resuelta' using errcode = 'P0001',
        hint = 'diferencia_ya_resuelta';
    end if;
    if resolucion_nueva = 'pagada_por_lo_real'
       and (f.tipo <> 'importe' or f.moneda <> e.moneda) then
      raise exception 'un pago a otra cuenta o en otra moneda solo se reclama'
        using errcode = 'P0001', hint = 'solo_reclamo';
    end if;
    update nucleo.diferencia_pago_manual
       set resolucion = resolucion_nueva, resuelta_por = actor, identidad_resolvio = identidad,
           resuelta_en = clock_timestamp()
     where id = f.id;
    estado_nuevo := o.estado::text;
    if resolucion_nueva = 'pagada_por_lo_real' then
      insert into nucleo.resultado_ejecucion_orden_pago
        (consorcio_id, ejecucion_id, resultado, origen, referencia)
      values (c, e.id, 'pagada', 'manual', f.numero_operacion);
      update nucleo.orden_pago set estado = 'pagada', actualizada_en = now() where id = orden;
      estado_nuevo := 'pagada';
    else
      update nucleo.orden_pago set actualizada_en = now() where id = orden;
    end if;
    perform nucleo.auditar_orden_pago(c, 'consorcio.orden_pago.resolver_diferencia_pago_manual',
      orden, actor, jsonb_build_object('estado', o.estado),
      jsonb_build_object('estado', estado_nuevo, 'version', e.version, 'ejecucion', e.id,
                         'diferencia', f.id, 'resolucion', resolucion_nueva,
                         'importe_real', f.importe_real, 'moneda', f.moneda));
    return estado_nuevo;
  end
  $$;

create function nucleo.recuperar_diferencia_pago_manual(orden uuid, diferencia uuid, actor uuid,
                                                        origen_pedido text, sesion uuid)
  returns void
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    identidad uuid;
    o nucleo.orden_pago;
    e nucleo.ejecucion_orden_pago;
    f nucleo.diferencia_pago_manual;
  begin
    identidad := nucleo.exigir_acto_economico(c, actor, sesion, 'egresos.aprobar_orden',
                                              origen_pedido);
    o := nucleo.orden_pago_bloqueada(c, orden);
    f := nucleo.diferencia_para_resolver(c, o, diferencia, actor, identidad);
    e := nucleo.ejecucion_manual_abierta(c, o);
    if f.resolucion is distinct from 'reclamo' then
      raise exception 'esa diferencia no tiene un reclamo abierto' using errcode = 'P0001',
        hint = 'estado_invalido';
    end if;
    update nucleo.diferencia_pago_manual
       set recuperada_por = actor, identidad_recupero = identidad,
           recuperada_en = clock_timestamp()
     where id = f.id;
    insert into nucleo.resultado_ejecucion_orden_pago
      (consorcio_id, ejecucion_id, resultado, origen, motivo)
    values (c, e.id, 'fallida', 'manual', 'Se recuperó lo que se había pagado de más o mal.');
    update nucleo.orden_pago set estado = 'fallida', actualizada_en = now() where id = orden;
    perform nucleo.auditar_orden_pago(c, 'consorcio.orden_pago.recuperar_diferencia_pago_manual',
      orden, actor, jsonb_build_object('estado', o.estado),
      jsonb_build_object('estado', 'fallida', 'version', e.version, 'ejecucion', e.id,
                         'diferencia', f.id));
  end
  $$;

-- Sin la cuenta completa: solo sus últimos dígitos.
create function nucleo.diferencias_pago_manual_lectura(orden uuid, facultad text, actor uuid,
                                                       origen_pedido text, sesion uuid)
  returns table (id uuid, ejecucion_id uuid, orden_id uuid, tipo text, importe_real numeric,
                 moneda text, cuenta_ultimos_digitos text, importe_orden_centavos bigint,
                 moneda_orden text, numero_operacion text, fecha_operacion date,
                 documento_id uuid, version_documento_id uuid, explicacion text,
                 fuera_de_termino boolean, registrada_por uuid, registrada_por_nombre text,
                 registrada_en timestamptz, resolucion text, resuelta_por uuid,
                 resuelta_en timestamptz, recuperada_en timestamptz)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
  begin
    perform nucleo.exigir_lectura_egresos(c, facultad, actor, origen_pedido, sesion);
    return query
      select f.id, f.ejecucion_id, f.orden_id, f.tipo, f.importe_real, f.moneda,
             right(f.cuenta_real, {DIGITOS_VISIBLES}), e.importe_centavos, e.moneda,
             f.numero_operacion, f.fecha_operacion, f.documento_id, f.version_documento_id,
             f.explicacion, f.fuera_de_termino, f.registrada_por, r.nombre, f.registrada_en,
             f.resolucion, f.resuelta_por, f.resuelta_en, f.recuperada_en
        from nucleo.diferencia_pago_manual f
        join nucleo.ejecucion_orden_pago e on e.consorcio_id = c and e.id = f.ejecucion_id
        join nucleo.persona r on r.id = f.registrada_por
       where f.consorcio_id = c and (orden is null or f.orden_id = orden)
       order by f.paso;
  end
  $$;

-- Sí o no: lo que `diferencia_para_resolver` exige, sobre la diferencia que espera al Consejo.
create function nucleo.admite_resolver_diferencia(c uuid, o nucleo.orden_pago, actor uuid,
                                                  identidad uuid)
  returns boolean
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    e nucleo.ejecucion_orden_pago;
  begin
    e := nucleo.ejecucion_manual_abierta(c, o);
    perform nucleo.exigir_no_ejecuto(c, e.id, actor, identidad);
    perform nucleo.exigir_no_preparo(c, o.id, actor, identidad);
    return true;
  exception when raise_exception then
    return false;
  end
  $$;
"""

RUTAS = (
    "nucleo.registrar_diferencia_pago_manual(uuid, integer, bytea, text, date, numeric, text, text,"
    " uuid, text, uuid, text, uuid)",
    "nucleo.resolver_diferencia_pago_manual(uuid, uuid, text, uuid, text, uuid)",
    "nucleo.recuperar_diferencia_pago_manual(uuid, uuid, uuid, text, uuid)",
    "nucleo.diferencias_pago_manual_lectura(uuid, text, uuid, text, uuid)",
)
INTERNAS = (
    "nucleo.admite_resolver_diferencia(uuid, nucleo.orden_pago, uuid, uuid)",
    "nucleo.diferencia_para_resolver(uuid, nucleo.orden_pago, uuid, uuid, uuid)",
    "nucleo.importe_pagado_ejecucion(uuid, uuid)",
    "nucleo.centavos_de(numeric)",
)

SIN_PAGO_CAMBIOS = (
    (
        "    return p;\n",
        "    -- Lo que pasó de verdad ya está registrado: espera al Consejo (0075).\n"
        "    if exists (select 1 from nucleo.diferencia_pago_manual x"
        " where x.ejecucion_id = e) then\n"
        "      raise exception 'el pago de esta orden ya está registrado'"
        " using errcode = 'P0001',\n"
        "        hint = 'pago_ya_registrado';\n"
        "    end if;\n"
        "    return p;\n",
    ),
)

HECHOS_CAMBIOS = (
    (
        " OUT cuenta_vista boolean)\n",
        " OUT cuenta_vista boolean, OUT diferencia_sin_resolver boolean,"
        " OUT reclamo_abierto boolean, OUT pagada_de_menos boolean)\n",
    ),
    (
        "    d nucleo.declaracion_pago_no_realizado;\n  begin\n",
        "    d nucleo.declaracion_pago_no_realizado;\n"
        "    dif nucleo.diferencia_pago_manual;\n  begin\n",
    ),
    (
        "    cuenta_vista := false;\n    select * into e",
        "    cuenta_vista := false;\n"
        "    diferencia_sin_resolver := false;\n"
        "    reclamo_abierto := false;\n"
        "    pagada_de_menos := false;\n"
        "    select * into e",
    ),
    (
        "    select * into d from nucleo.declaracion_pago_no_realizado x\n",
        "    select * into dif from nucleo.diferencia_pago_manual x where x.ejecucion_id = e.id;\n"
        "    if found then\n"
        "      diferencia_sin_resolver := dif.resolucion is null;\n"
        "      reclamo_abierto := dif.resolucion is not distinct from 'reclamo'\n"
        "        and dif.recuperada_en is null;\n"
        "      pagada_de_menos := dif.resolucion is not distinct from 'pagada_por_lo_real'\n"
        "        and nucleo.centavos_de(dif.importe_real) < e.importe_centavos;\n"
        "    end if;\n"
        "    select * into d from nucleo.declaracion_pago_no_realizado x\n",
    ),
    (
        "      and not exists (select 1 from nucleo.pago_manual x\n"
        "                       where x.ejecucion_id = e.id and x.paso > d.paso);\n",
        "      and not exists (select 1 from nucleo.pago_manual x\n"
        "                       where x.ejecucion_id = e.id and x.paso > d.paso)\n"
        "      and dif.id is null;\n",
    ),
)

ACCIONES_CAMBIOS = (
    (
        " falta_que_declares boolean, pago_sin_salida boolean)\n",
        " falta_que_declares boolean, pago_sin_salida boolean, diferencia_sin_resolver boolean,"
        " reclamo_abierto boolean, pagada_de_menos boolean, puede_resolver_diferencia boolean)\n",
    ),
    (
        "             nucleo.pago_sin_salida(c, o)\n",
        "             nucleo.pago_sin_salida(c, o),\n"
        "             h.diferencia_sin_resolver, h.reclamo_abierto, h.pagada_de_menos,\n"
        "             aprueba and (h.diferencia_sin_resolver or h.reclamo_abierto)\n"
        "               and nucleo.admite_resolver_diferencia(c, o, actor, identidad)\n",
    ),
    (
        "         and o.estado in ('en_revision', 'aprobada', 'en_ejecucion',"
        " 'en_verificacion');\n",
        "         -- Pagada de menos: quien prepara ve lo que falta (0075).\n"
        "         and (o.estado in ('en_revision', 'aprobada', 'en_ejecucion', 'en_verificacion')\n"
        "              or h.pagada_de_menos);\n",
    ),
)

CONSULTA_CAMBIOS = (
    (
        " declaracion_vigente boolean, declaracion_confirmable boolean)\n",
        " declaracion_vigente boolean, declaracion_confirmable boolean,"
        " diferencia_sin_resolver boolean, reclamo_abierto boolean,"
        " importe_pagado_centavos bigint)\n",
    ),
    (
        "                         and position(buscado_operacion in p.numero_operacion) > 0)))\n",
        "                         and position(buscado_operacion in p.numero_operacion) > 0)\n"
        "                     or exists (\n"
        "                       select 1 from nucleo.diferencia_pago_manual f\n"
        "                        where f.consorcio_id = c and f.orden_id = o.id\n"
        "                          and position(buscado_operacion in f.numero_operacion) > 0))))\n",
    ),
    (
        "                or (buscado_operacion is not null and exists (\n",
        "                or (buscado_operacion is not null and (exists (\n",
    ),
    (
        "             u.numero_operacion, u.fecha_operacion, o.actualizada_en, h.manual,\n",
        "             coalesce(f.numero_operacion, u.numero_operacion),\n"
        "             coalesce(f.fecha_operacion, u.fecha_operacion), o.actualizada_en,"
        " h.manual,\n",
    ),
    (
        "             h.declaracion_confirmable\n        from pagina g\n",
        "             h.declaracion_confirmable, h.diferencia_sin_resolver, h.reclamo_abierto,\n"
        "             case when f.resolucion = 'pagada_por_lo_real'\n"
        "                  then nucleo.centavos_de(f.importe_real) end\n"
        "        from pagina g\n",
    ),
    (
        "           order by p.paso desc limit 1) u on true\n",
        "           order by p.paso desc limit 1) u on true\n"
        "        left join lateral (\n"
        "          select x.numero_operacion, x.fecha_operacion, x.importe_real, x.resolucion\n"
        "            from nucleo.ejecucion_orden_pago e\n"
        "            join nucleo.diferencia_pago_manual x\n"
        "              on x.consorcio_id = e.consorcio_id and x.ejecucion_id = e.id\n"
        "           where e.consorcio_id = c and e.orden_id = o.id\n"
        "             and e.version = o.version_vigente) f on true\n",
    ),
)

DECLARACIONES_CAMBIOS = (
    (
        "             and not exists (select 1 from nucleo.pago_manual x\n"
        "                              where x.ejecucion_id = d.ejecucion_id"
        " and x.paso > d.paso)\n",
        "             and not exists (select 1 from nucleo.pago_manual x\n"
        "                              where x.ejecucion_id = d.ejecucion_id"
        " and x.paso > d.paso)\n"
        "             and not exists (select 1 from nucleo.diferencia_pago_manual x\n"
        "                              where x.ejecucion_id = d.ejecucion_id)\n",
    ),
)

POR_ACLARAR_CAMBIOS = (
    (
        "                from nucleo.ultimo_pago_manual(e.id) x)\n",
        "                from nucleo.ultimo_pago_manual(e.id) x)\n"
        "         and not exists (select 1 from nucleo.diferencia_pago_manual x\n"
        "                          where x.ejecucion_id = e.id)\n",
    ),
)

FECHA_CAMBIOS = (
    (
        "    select case when r.origen = 'manual' then\n"
        "                  (select p.fecha_operacion from nucleo.pago_manual p\n",
        "    select case when r.origen = 'manual' then\n"
        "                  coalesce((select f.fecha_operacion\n"
        "                              from nucleo.diferencia_pago_manual f\n"
        "                             where f.consorcio_id = c and f.ejecucion_id = ejecucion\n"
        "                               and f.resolucion = 'pagada_por_lo_real'),\n"
        "                  (select p.fecha_operacion from nucleo.pago_manual p\n",
    ),
    (
        "                    where p.consorcio_id = c and p.ejecucion_id = ejecucion)\n",
        "                    where p.consorcio_id = c and p.ejecucion_id = ejecucion))\n",
    ),
)

AGREGAR_CAMBIOS = (
    (
        "    values (c, l.id, o.id, e.id, e.importe_centavos, e.moneda, esquema, actor,"
        " identidad)\n",
        f"    values (c, l.id, o.id, e.id, {IMPORTE_PAGADO}, e.moneda, esquema, actor,\n"
        "            identidad)\n",
    ),
    (
        "                         'importe_centavos', e.importe_centavos, 'moneda', e.moneda));\n",
        f"                         'importe_centavos', {IMPORTE_PAGADO},\n"
        "                         'moneda', e.moneda));\n",
    ),
)

LIQUIDABLES_CAMBIOS = (
    (
        "      select o.id, v.concepto, pv.razon_social, e.importe_centavos, e.moneda,\n",
        f"      select o.id, v.concepto, pv.razon_social, {IMPORTE_PAGADO}, e.moneda,\n",
    ),
)

MODIFICAR_CAMBIOS = (
    (
        "    if (v.importe_centavos, v.moneda, v.concepto, v.proveedor_id, v.proveedor_version,\n",
        "    -- Volver a pagar (0075): desde fallida los datos pueden ser los mismos.\n"
        "    if o.estado <> 'fallida' and\n"
        "       (v.importe_centavos, v.moneda, v.concepto, v.proveedor_id, v.proveedor_version,\n",
    ),
)

EXPORTAR_CAMBIOS = (
    (
        "    return query\n      select 'declaracion_no_pagado'::text,\n",
        "    return query\n"
        "      select 'diferencia_pago'::text,\n"
        "             jsonb_build_object('id', f.id, 'orden_id', f.orden_id,\n"
        "                                'ejecucion_id', f.ejecucion_id, 'tipo', f.tipo,\n"
        "                                'importe_real', f.importe_real, 'moneda', f.moneda,\n"
        f"                                'cuenta_ultimos_digitos',"
        f" right(f.cuenta_real, {DIGITOS_VISIBLES}),\n"
        "                                'numero_operacion',"
        " nucleo.texto_sin_cuenta(f.numero_operacion),\n"
        "                                'fecha_operacion', f.fecha_operacion,\n"
        "                                'documento_id', f.documento_id,\n"
        "                                'version_documento_id', f.version_documento_id,\n"
        "                                'explicacion', nucleo.texto_sin_cuenta(f.explicacion),\n"
        "                                'fuera_de_termino', f.fuera_de_termino,\n"
        "                                'registrada_por_nombre',"
        " nucleo.nombre_para_exportar(c, f.registrada_por, hoy),\n"
        "                                'registrada_en', f.registrada_en,\n"
        "                                'resolucion', f.resolucion,\n"
        "                                'resuelta_por_nombre',"
        " nucleo.nombre_para_exportar(c, f.resuelta_por, hoy),\n"
        "                                'resuelta_en', f.resuelta_en,\n"
        "                                'recuperada_por_nombre',"
        " nucleo.nombre_para_exportar(c, f.recuperada_por, hoy),\n"
        "                                'recuperada_en', f.recuperada_en)\n"
        "        from nucleo.diferencia_pago_manual f\n"
        "       where f.consorcio_id = c\n"
        "       order by f.paso;\n"
        "    return query\n      select 'declaracion_no_pagado'::text,\n",
    ),
)

COTEJO_CAMBIOS = (
    (
        "       and not exists (select 1 from nucleo.pago_manual q where q.corrige_a = p.id)\n",
        "       and not exists (select 1 from nucleo.pago_manual q where q.corrige_a = p.id)\n"
        "       -- Con una diferencia, el registro observado ya no es lo que salió del banco.\n"
        "       and not exists (select 1 from nucleo.diferencia_pago_manual f\n"
        "                        where f.ejecucion_id = e.id)\n",
    ),
)

REEMPLAZOS = (
    (SIN_PAGO, SIN_PAGO_CAMBIOS),
    (DECLARACIONES, DECLARACIONES_CAMBIOS),
    (POR_ACLARAR, POR_ACLARAR_CAMBIOS),
    (FECHA, FECHA_CAMBIOS),
    (AGREGAR, AGREGAR_CAMBIOS),
    (LIQUIDABLES, LIQUIDABLES_CAMBIOS),
    (MODIFICAR, MODIFICAR_CAMBIOS),
    (EXPORTAR, EXPORTAR_CAMBIOS),
    (COTEJO, COTEJO_CAMBIOS),
)
OTRAS_LECTURAS = ((ACCIONES, ACCIONES_CAMBIOS), (CONSULTA, CONSULTA_CAMBIOS))

GUARDA_DOWNGRADE = """
-- Hasta el fin del downgrade nadie registra: la guarda vale para lo que se borra.
lock table nucleo.diferencia_pago_manual in access exclusive mode;
do $$
begin
  if exists (select 1 from nucleo.diferencia_pago_manual) then
    raise exception '0075 no baja con diferencias de pago: se perdería lo que pasó de verdad'
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
            raise RuntimeError(f"0075: {funcion} no tiene exactamente una vez {antes!r}")
        definicion = definicion.replace(antes, despues)
    return definicion


def _otra_lectura(funcion: str, cambios, *, al_reves: bool = False) -> None:
    """Lo que devuelve cambia: se borra y se crea de nuevo, con su permiso."""
    definicion = _definicion(funcion, cambios, al_reves=al_reves)
    op.execute(f"drop function {funcion}")
    op.execute(definicion)
    op.execute(f"revoke all on function {funcion} from public")
    op.execute(f"grant execute on function {funcion} to cem_app")


def _hechos(*, al_reves: bool = False) -> None:
    """Interna: cambia lo que devuelve y `cem_app` no la llama."""
    definicion = _definicion(HECHOS, HECHOS_CAMBIOS, al_reves=al_reves)
    op.execute(f"drop function {HECHOS}")
    op.execute(definicion)
    op.execute(f"revoke all on function {HECHOS} from public")


def upgrade() -> None:
    op.execute(TABLA)
    op.execute(FUNCIONES)
    for funcion in INTERNAS + RUTAS:
        op.execute(f"revoke all on function {funcion} from public")
    for funcion in RUTAS:
        op.execute(f"grant execute on function {funcion} to cem_app")
    for funcion, cambios in REEMPLAZOS:
        op.execute(_definicion(funcion, cambios, al_reves=False))
    _hechos()
    for funcion, cambios in OTRAS_LECTURAS:
        _otra_lectura(funcion, cambios)


def downgrade() -> None:
    op.execute(GUARDA_DOWNGRADE)
    for funcion, cambios in OTRAS_LECTURAS:
        _otra_lectura(funcion, cambios, al_reves=True)
    _hechos(al_reves=True)
    for funcion, cambios in reversed(REEMPLAZOS):
        op.execute(_definicion(funcion, cambios, al_reves=True))
    for funcion in RUTAS + INTERNAS:
        op.execute(f"drop function {funcion}")
    op.execute("drop table nucleo.diferencia_pago_manual")
    op.execute("drop function nucleo.negar_cambio_diferencia_pago()")
