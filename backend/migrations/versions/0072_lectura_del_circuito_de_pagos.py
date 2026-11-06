"""Lectura del circuito de pagos: qué le toca a cada persona, según las reglas de la base.

Diseño: `doc/diseno/Rediseno_Circuito_Pagos_2026-11-06.md` del repositorio CEM, §2, §3, §11 y la
fila «Hueco de `puede_aprobar` y `puede_conciliar`» de §12. Fase 1: solo lectura, sin tablas nuevas.

**El hueco.** `cem_app` no lee las identidades guardadas en cada acto ni las aperturas de la cuenta:
quien vio la cuenta sin registrar recibía el botón de conciliar y después un 403. Lo mismo pasaba
con quien firmó la cuenta del proveedor y ya no prepara, al aprobar.

**`nucleo.acciones_de_ordenes_lectura`** responde solo sí o no, por orden en curso, sobre quien
pide: no dice quién vio la cuenta ni quién aprobó, ni devuelve dato alguno de otra persona. Cada
respuesta sale de las mismas `exigir_*` que usa el acto (`admite_*`): si una de ellas niega con
`P0001`, la respuesta es no. Cualquier otro error sigue siendo un error. Las condiciones que el acto
tiene escritas adentro y no en una `exigir_*` se repiten acá; las pruebas comparan cada sí con lo
que hace el acto.

No cuenta el gesto: una falta transitoria no oculta nada (decisión del propietario del 05-11-2026,
§1), y el acto lo sigue pidiendo. Sí pide la sesión de 12 horas y la identidad humana verificada.

**`nucleo.hechos_de_pago_manual`** (interna) dice en qué anda la ejecución manual de la versión
vigente. La situación que ve la persona se deriva de eso en la API.

**`nucleo.pagos_consulta_lectura`** es la consulta paginada de `/pagos/consulta`, con la barrera de
lectura de egresos y sin la cuenta. La página se elige antes de calcular los hechos.

**`nucleo.comprobante_subido_lectura`**: el último archivo que pasó el antivirus y que quien pide
subió para la ejecución vigente. Un documento atado a otra ejecución no cuenta.

Downgrade: borra las funciones. No hay datos que dependan de ellas.

Revisión: 0072
Anterior: 0071
"""

from alembic import op

revision = "0072"
down_revision = "0071"
branch_labels = None
depends_on = None

VIGENCIA_APROBACION_DIAS = 7
"""La de `0050`, `0055` y `0067`."""
CATEGORIA = "facturas_y_comprobantes"

HECHOS = """
-- En qué anda la ejecución manual de la versión vigente. Sin ejecución manual, todo es no.
-- `declaracion_confirmable` son las condiciones de `confirmar_pago_no_realizado` sobre el orden de
-- los hechos, para la última declaración: una anterior no puede serlo si esta no lo es.
create function nucleo.hechos_de_pago_manual(c uuid, o nucleo.orden_pago,
                                             out manual boolean, out pago_sin_conciliar boolean,
                                             out pago_observado boolean,
                                             out declaracion_vigente boolean,
                                             out declaracion_confirmable boolean,
                                             out cuenta_vista boolean)
  language plpgsql stable
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    e nucleo.ejecucion_orden_pago;
    p nucleo.pago_manual;
    d nucleo.declaracion_pago_no_realizado;
  begin
    manual := false;
    pago_sin_conciliar := false;
    pago_observado := false;
    declaracion_vigente := false;
    declaracion_confirmable := false;
    cuenta_vista := false;
    select * into e from nucleo.ejecucion_orden_pago x
     where x.consorcio_id = c and x.orden_id = o.id and x.version = o.version_vigente;
    if not found or e.modalidad <> 'manual' then
      return;
    end if;
    manual := true;
    cuenta_vista := exists (select 1 from nucleo.apertura_cuenta_pago_manual a
                             where a.ejecucion_id = e.id);
    p := nucleo.ultimo_pago_manual(e.id);
    if p.id is not null then
      pago_observado := nucleo.pago_manual_observado(p.id);
      pago_sin_conciliar := not exists (select 1 from nucleo.conciliacion_pago_manual k
                                         where k.pago_id = p.id);
    end if;
    select * into d from nucleo.declaracion_pago_no_realizado x
     where x.ejecucion_id = e.id order by x.paso desc limit 1;
    if not found then
      return;
    end if;
    declaracion_vigente :=
      not exists (select 1 from nucleo.confirmacion_pago_no_realizado f
                   where f.declaracion_id = d.id)
      and not exists (select 1 from nucleo.apertura_cuenta_pago_manual a
                       where a.ejecucion_id = e.id and a.paso > d.paso)
      and not exists (select 1 from nucleo.pago_manual x
                       where x.ejecucion_id = e.id and x.paso > d.paso);
    declaracion_confirmable := declaracion_vigente
      and o.estado in ('en_ejecucion', 'en_verificacion')
      and not exists (select 1 from nucleo.resultado_ejecucion_orden_pago r
                       where r.ejecucion_id = e.id and r.resultado <> 'incierta')
      and not exists (select 1 from nucleo.apertura_cuenta_pago_manual a
                       where a.ejecucion_id = e.id
                         and not exists (select 1 from nucleo.declaracion_pago_no_realizado y
                                          where y.ejecucion_id = e.id
                                            and y.identidad_declaro = a.identidad_abrio
                                            and y.paso > a.paso));
  end
  $$;
"""

ADMITE = f"""
-- Sí o no por acto, con las barreras de ese acto. `identidad` es la de quien pide.
create function nucleo.admite_rechazar(c uuid, o nucleo.orden_pago, actor uuid, identidad uuid)
  returns boolean
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  begin
    perform nucleo.exigir_decision_sobre_orden(c, o, o.version_vigente, actor, identidad);
    return true;
  exception when raise_exception then
    return false;
  end
  $$;

create function nucleo.admite_aprobar(c uuid, o nucleo.orden_pago, actor uuid, identidad uuid)
  returns boolean
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  begin
    perform nucleo.exigir_decision_sobre_orden(c, o, o.version_vigente, actor, identidad);
    perform nucleo.exigir_no_cargo_la_cuenta(c, o.id, o.version_vigente, actor, identidad);
    return not exists (
      select 1 from nucleo.orden_pago_aprobacion x
       where x.orden_id = o.id and x.version = o.version_vigente
         and (x.identidad_id = identidad or x.persona_id = actor
              or nucleo.identidad_humana_de(c, x.persona_id) = identidad));
  exception when raise_exception then
    return false;
  end
  $$;

-- Aprobada, lo que pide emitir. Ya emitida, lo que pide `cuenta_para_pago_manual`.
create function nucleo.admite_pagar(c uuid, o nucleo.orden_pago, actor uuid, identidad uuid)
  returns boolean
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    e nucleo.ejecucion_orden_pago;
  begin
    if o.estado <> 'aprobada' then
      e := nucleo.ejecucion_manual_abierta(c, o);
      perform nucleo.exigir_sin_pago_vigente(e.id);
    end if;
    perform nucleo.exigir_proveedor_sin_cambios(c, o.id, o.version_vigente);
    perform nucleo.exigir_orden_emitible(c, o, o.version_vigente);
    perform nucleo.exigir_no_aprobo(c, o.id, o.version_vigente, actor, identidad);
    return true;
  exception when raise_exception then
    return false;
  end
  $$;

-- Lo que `renovar_orden_vencida` admite: la aprobación venció o el contrato dejó de regir.
create function nucleo.admite_renovar(c uuid, o nucleo.orden_pago)
  returns boolean
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select o.estado = 'aprobada'
       and (o.aprobada_en <= now() - make_interval(days => {VIGENCIA_APROBACION_DIAS})
            or exists (
              select 1 from nucleo.orden_pago_version v
               where v.orden_id = o.id and v.numero = o.version_vigente
                 and v.contrato_id is not null
                 and not nucleo.contrato_vigente(c, v.contrato_id, nucleo.hoy_del_consorcio(c))))
  $$;

-- Lo que `registrar_pago_manual` exige de la persona: vio la cuenta de esta ejecución, no aprobó y
-- no hay un registro vigente.
create function nucleo.admite_registrar(c uuid, o nucleo.orden_pago, actor uuid, identidad uuid)
  returns boolean
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    e nucleo.ejecucion_orden_pago;
  begin
    e := nucleo.ejecucion_manual_abierta(c, o);
    perform nucleo.exigir_no_aprobo(c, o.id, e.version, actor, identidad);
    perform nucleo.exigir_sin_pago_vigente(e.id);
    return exists (select 1 from nucleo.apertura_cuenta_pago_manual a
                    where a.ejecucion_id = e.id and a.identidad_abrio = identidad);
  exception when raise_exception then
    return false;
  end
  $$;

-- Lo que `declarar_pago_no_realizado` exige de quien tiene la facultad: si alguien vio la cuenta,
-- declara alguien que la vio.
create function nucleo.admite_declarar(c uuid, o nucleo.orden_pago, actor uuid, identidad uuid)
  returns boolean
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    e nucleo.ejecucion_orden_pago;
  begin
    e := nucleo.ejecucion_manual_abierta(c, o);
    perform nucleo.exigir_no_aprobo(c, o.id, e.version, actor, identidad);
    perform nucleo.exigir_sin_pago_vigente(e.id);
    return not exists (select 1 from nucleo.apertura_cuenta_pago_manual a
                        where a.ejecucion_id = e.id)
        or exists (select 1 from nucleo.apertura_cuenta_pago_manual a
                    where a.ejecucion_id = e.id and a.identidad_abrio = identidad);
  exception when raise_exception then
    return false;
  end
  $$;

-- La ejecución abierta que nadie vio y que ya nadie puede abrir (la aprobación venció, el proveedor
-- cambió): no se renueva ni se modifica, y queda solo declarar que no se pagó.
create function nucleo.pago_sin_salida(c uuid, o nucleo.orden_pago)
  returns boolean
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    e nucleo.ejecucion_orden_pago;
  begin
    begin
      e := nucleo.ejecucion_manual_abierta(c, o);
    exception when raise_exception then
      return false;
    end;
    if exists (select 1 from nucleo.apertura_cuenta_pago_manual a where a.ejecucion_id = e.id)
       or exists (select 1 from nucleo.pago_manual x where x.ejecucion_id = e.id) then
      return false;
    end if;
    begin
      perform nucleo.exigir_proveedor_sin_cambios(c, o.id, e.version);
      perform nucleo.exigir_orden_emitible(c, o, e.version);
      return false;
    exception when raise_exception then
      return true;
    end;
  end
  $$;

-- Corrige quien registró el pago observado (decisión del propietario del 05-11-2026, §9).
create function nucleo.registro_el_pago_observado(c uuid, o nucleo.orden_pago, identidad uuid)
  returns boolean
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select exists (
      select 1 from nucleo.ejecucion_orden_pago e
       cross join lateral nucleo.ultimo_pago_manual(e.id) p
       where e.consorcio_id = c and e.orden_id = o.id and e.version = o.version_vigente
         and p.identidad_registro = identidad and nucleo.pago_manual_observado(p.id))
  $$;

create function nucleo.admite_conciliar(c uuid, o nucleo.orden_pago, actor uuid, identidad uuid)
  returns boolean
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    e nucleo.ejecucion_orden_pago;
    p nucleo.pago_manual;
  begin
    e := nucleo.ejecucion_manual_abierta(c, o);
    p := nucleo.ultimo_pago_manual(e.id);
    if p.id is null or exists (select 1 from nucleo.conciliacion_pago_manual k
                                where k.pago_id = p.id) then
      return false;
    end if;
    perform nucleo.exigir_no_ejecuto(c, e.id, actor, identidad);
    perform nucleo.exigir_no_preparo(c, o.id, actor, identidad);
    return true;
  exception when raise_exception then
    return false;
  end
  $$;

-- Sobre la última declaración; que sea confirmable lo dice `hechos_de_pago_manual`.
create function nucleo.admite_confirmar(c uuid, o nucleo.orden_pago, actor uuid, identidad uuid)
  returns boolean
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    e nucleo.ejecucion_orden_pago;
    d nucleo.declaracion_pago_no_realizado;
  begin
    e := nucleo.ejecucion_manual_abierta(c, o);
    select * into d from nucleo.declaracion_pago_no_realizado x
     where x.ejecucion_id = e.id order by x.paso desc limit 1;
    if not found or d.identidad_declaro = identidad then
      return false;
    end if;
    perform nucleo.exigir_sin_pago_vigente(e.id);
    perform nucleo.exigir_no_ejecuto(c, e.id, actor, identidad);
    perform nucleo.exigir_separacion_humana(c, actor, d.declarado_por, 'no_es_quien_ejecuto');
    perform nucleo.exigir_no_preparo(c, o.id, actor, identidad);
    return true;
  exception when raise_exception then
    return false;
  end
  $$;

-- Si quien pide vio la cuenta de esta ejecución y no declaró después de su última apertura.
create function nucleo.falta_que_declare(c uuid, o nucleo.orden_pago, identidad uuid)
  returns boolean
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select exists (
      select 1 from nucleo.ejecucion_orden_pago e
        join nucleo.apertura_cuenta_pago_manual a
          on a.consorcio_id = e.consorcio_id and a.ejecucion_id = e.id
       where e.consorcio_id = c and e.orden_id = o.id and e.version = o.version_vigente
         and a.identidad_abrio = identidad
         and not exists (select 1 from nucleo.declaracion_pago_no_realizado y
                          where y.ejecucion_id = e.id and y.identidad_declaro = identidad
                            and y.paso > a.paso))
  $$;
"""

ACCIONES = """
-- Solo sí o no, sobre quien pide, en las órdenes en curso (o en una). Las rutas de pagar y de
-- cargar el comprobante leen la orden como quien prepara: por eso piden las dos facultades.
create function nucleo.acciones_de_ordenes_lectura(orden uuid, actor uuid, origen_pedido text,
                                                   sesion uuid)
  returns table (orden_id uuid, manual boolean, pago_sin_conciliar boolean,
                 pago_observado boolean, declaracion_vigente boolean,
                 declaracion_confirmable boolean, cuenta_vista boolean, puede_aprobar boolean,
                 puede_rechazar boolean, puede_pagar boolean, puede_renovar boolean,
                 puede_registrar boolean, puede_declarar boolean,
                 puede_subir_comprobante boolean, puede_corregir boolean, puede_conciliar boolean,
                 puede_confirmar boolean, falta_que_declares boolean, pago_sin_salida boolean)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    hoy date;
    prepara boolean;
    aprueba boolean;
    ejecuta boolean;
    carga boolean;
    identidad uuid;
  begin
    if c is null or actor is null or actor is distinct from plataforma.persona_actual() then
      raise exception 'sin ámbito para leer las órdenes' using errcode = '42501';
    end if;
    hoy := nucleo.hoy_del_consorcio(c);
    prepara := nucleo.tiene_facultad_vigente(c, actor, 'egresos.preparar_orden', hoy);
    aprueba := nucleo.tiene_facultad_vigente(c, actor, 'egresos.aprobar_orden', hoy);
    ejecuta := prepara
      and nucleo.tiene_facultad_vigente(c, actor, 'egresos.ejecutar_pago', hoy);
    carga := nucleo.tiene_facultad_vigente(c, actor, 'documentos.cargar', hoy);
    if not (prepara or aprueba) then
      raise exception 'sin facultad para leer egresos en este consorcio' using errcode = '42501';
    end if;
    identidad := nucleo.exigir_sesion_operativa(c, actor, sesion, origen_pedido, false);
    return query
      select o.id, h.manual, h.pago_sin_conciliar, h.pago_observado, h.declaracion_vigente,
             h.declaracion_confirmable, h.cuenta_vista,
             aprueba and nucleo.admite_aprobar(c, o, actor, identidad),
             aprueba and nucleo.admite_rechazar(c, o, actor, identidad),
             ejecuta and nucleo.admite_pagar(c, o, actor, identidad),
             prepara and nucleo.admite_renovar(c, o),
             ejecuta and nucleo.admite_registrar(c, o, actor, identidad),
             ejecuta and nucleo.admite_declarar(c, o, actor, identidad),
             ejecuta and carga and nucleo.admite_registrar(c, o, actor, identidad),
             ejecuta and nucleo.admite_registrar(c, o, actor, identidad)
               and nucleo.registro_el_pago_observado(c, o, identidad),
             aprueba and nucleo.admite_conciliar(c, o, actor, identidad),
             aprueba and h.declaracion_confirmable
               and nucleo.admite_confirmar(c, o, actor, identidad),
             nucleo.falta_que_declare(c, o, identidad),
             nucleo.pago_sin_salida(c, o)
        from nucleo.orden_pago o
       cross join lateral nucleo.hechos_de_pago_manual(c, o) h
       where o.consorcio_id = c and (orden is null or o.id = orden)
         and o.estado in ('en_revision', 'aprobada', 'en_ejecucion', 'en_verificacion');
  end
  $$;
"""

CONSULTA = """
-- `busqueda`: en el proveedor y el concepto de la versión vigente, y en el número de operación de
-- cualquier registro de la orden, que se compara como se guarda. `estados`: nulo, todos. Como las
-- demás lecturas, con `egresos.aprobar_orden` no se ven los borradores.
create function nucleo.pagos_consulta_lectura(busqueda text, estados text[], limite integer,
                                              saltar integer, facultad text, actor uuid,
                                              origen_pedido text, sesion uuid)
  returns table (orden_id uuid, razon_social text, concepto text, importe_centavos bigint,
                 moneda text, estado text, numero_operacion text, fecha_operacion date,
                 actualizada_en timestamptz, manual boolean, pago_sin_conciliar boolean,
                 pago_observado boolean, declaracion_vigente boolean,
                 declaracion_confirmable boolean)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    buscado text := nullif(lower(btrim(busqueda)), '');
    buscado_operacion text :=
      nullif(upper(regexp_replace(coalesce(busqueda, ''), '[^A-Za-z0-9]', '', 'g')), '');
  begin
    perform nucleo.exigir_lectura_egresos(c, facultad, actor, origen_pedido, sesion);
    return query
      with pagina as (
        select o.id
          from nucleo.orden_pago o
          join nucleo.orden_pago_version v
            on v.consorcio_id = o.consorcio_id and v.orden_id = o.id
           and v.numero = o.version_vigente
          join nucleo.proveedor_version pv
            on pv.consorcio_id = v.consorcio_id and pv.proveedor_id = v.proveedor_id
           and pv.numero = v.proveedor_version
         where o.consorcio_id = c
           and (facultad = 'egresos.preparar_orden' or o.estado <> 'borrador')
           and (estados is null or o.estado::text = any(estados))
           and (buscado is null
                or position(buscado in lower(pv.razon_social)) > 0
                or position(buscado in lower(v.concepto)) > 0
                or (buscado_operacion is not null and exists (
                      select 1 from nucleo.ejecucion_orden_pago e
                        join nucleo.pago_manual p
                          on p.consorcio_id = e.consorcio_id and p.ejecucion_id = e.id
                       where e.consorcio_id = c and e.orden_id = o.id
                         and position(buscado_operacion in p.numero_operacion) > 0)))
         order by o.actualizada_en desc, o.id
         limit limite offset saltar
      )
      select o.id, pv.razon_social, v.concepto, v.importe_centavos, v.moneda, o.estado::text,
             u.numero_operacion, u.fecha_operacion, o.actualizada_en, h.manual,
             h.pago_sin_conciliar, h.pago_observado, h.declaracion_vigente,
             h.declaracion_confirmable
        from pagina g
        join nucleo.orden_pago o on o.consorcio_id = c and o.id = g.id
        join nucleo.orden_pago_version v
          on v.consorcio_id = o.consorcio_id and v.orden_id = o.id
         and v.numero = o.version_vigente
        join nucleo.proveedor_version pv
          on pv.consorcio_id = v.consorcio_id and pv.proveedor_id = v.proveedor_id
         and pv.numero = v.proveedor_version
       cross join lateral nucleo.hechos_de_pago_manual(c, o) h
        left join lateral (
          select p.numero_operacion, p.fecha_operacion
            from nucleo.ejecucion_orden_pago e
            join nucleo.pago_manual p
              on p.consorcio_id = e.consorcio_id and p.ejecucion_id = e.id
           where e.consorcio_id = c and e.orden_id = o.id and e.version = o.version_vigente
           order by p.paso desc limit 1) u on true
       order by o.actualizada_en desc, o.id;
  end
  $$;
"""

COMPROBANTE = f"""
-- El archivo que quien pide subió para la ejecución de la versión vigente y pasó el antivirus. El
-- documento atado a otra ejecución ya respalda otro pago: no sirve para esta.
create function nucleo.comprobante_subido_lectura(orden uuid, facultad text, actor uuid,
                                                  origen_pedido text, sesion uuid)
  returns table (documento_id uuid, version_documento_id uuid)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
  begin
    perform nucleo.exigir_lectura_egresos(c, facultad, actor, origen_pedido, sesion);
    return query
      select d.id, v.id
        from nucleo.orden_pago o
        join nucleo.ejecucion_orden_pago e
          on e.consorcio_id = o.consorcio_id and e.orden_id = o.id
         and e.version = o.version_vigente
        join nucleo.documento d
          on d.consorcio_id = o.consorcio_id and d.orden_comprobante = o.id
        join nucleo.version_documento v
          on v.consorcio_id = d.consorcio_id and v.documento_id = d.id
        left join nucleo.comprobante_pago_manual t
          on t.consorcio_id = d.consorcio_id and t.documento_id = d.id
       where o.consorcio_id = c and o.id = orden and d.estado = 'vigente'
         and d.categoria = '{CATEGORIA}' and v.estado = 'disponible' and v.subida_por = actor
         and v.subida_en >= e.creada_en and (t.ejecucion_id is null or t.ejecucion_id = e.id)
       order by v.analizada_en desc, v.id desc
       limit 1;
  end
  $$;
"""

RUTAS = (
    "nucleo.acciones_de_ordenes_lectura(uuid, uuid, text, uuid)",
    "nucleo.pagos_consulta_lectura(text, text[], integer, integer, text, uuid, text, uuid)",
    "nucleo.comprobante_subido_lectura(uuid, text, uuid, text, uuid)",
)
INTERNAS = (
    "nucleo.falta_que_declare(uuid, nucleo.orden_pago, uuid)",
    "nucleo.pago_sin_salida(uuid, nucleo.orden_pago)",
    "nucleo.admite_declarar(uuid, nucleo.orden_pago, uuid, uuid)",
    "nucleo.admite_confirmar(uuid, nucleo.orden_pago, uuid, uuid)",
    "nucleo.admite_conciliar(uuid, nucleo.orden_pago, uuid, uuid)",
    "nucleo.registro_el_pago_observado(uuid, nucleo.orden_pago, uuid)",
    "nucleo.admite_registrar(uuid, nucleo.orden_pago, uuid, uuid)",
    "nucleo.admite_renovar(uuid, nucleo.orden_pago)",
    "nucleo.admite_pagar(uuid, nucleo.orden_pago, uuid, uuid)",
    "nucleo.admite_aprobar(uuid, nucleo.orden_pago, uuid, uuid)",
    "nucleo.admite_rechazar(uuid, nucleo.orden_pago, uuid, uuid)",
    "nucleo.hechos_de_pago_manual(uuid, nucleo.orden_pago)",
)


def upgrade() -> None:
    for sql in (HECHOS, ADMITE, ACCIONES, CONSULTA, COMPROBANTE):
        op.execute(sql)
    for funcion in INTERNAS + RUTAS:
        op.execute(f"revoke all on function {funcion} from public")
    for funcion in RUTAS:
        op.execute(f"grant execute on function {funcion} to cem_app")


def downgrade() -> None:
    for funcion in RUTAS + INTERNAS:
        op.execute(f"drop function {funcion}")
