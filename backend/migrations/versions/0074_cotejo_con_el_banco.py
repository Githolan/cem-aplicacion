"""Cotejar con el banco (RF12): a mano o con el extracto en CSV.

Diseño: `doc/diseno/Rediseno_Circuito_Pagos_2026-11-06.md` del repositorio CEM, §10 y §13 punto 6.

**Cotejar no cambia nada de la orden.** Dice «esto salió del banco», sobre un pago ya conciliado
conforme. No mueve dinero, no toca `orden_pago` ni su ejecución.

**Quién.** El diseño no lo dice y el catálogo lo pone en el Consejo: va con las reglas de conciliar,
que son las más estrictas que hay sobre un pago (`egresos.conciliar_pago`). La facultad
`egresos.aprobar_orden`, el gesto en lo que escribe, y quien preparó, vio la cuenta o registró no
coteja ese pago (`exigir_no_preparo`, `exigir_no_ejecuto`), con cualquiera de sus cuentas. Del
extracto, un pago que coincide y quien subió no puede cotejar queda sin cotejar para otro miembro.

**Tablas.** `nucleo.cotejo_pago` (uno por pago), `nucleo.importacion_extracto` (el archivo, por su
hash: documento 13, RF12), `nucleo.movimiento_extracto` (solo fecha, importe y número de cada
débito: ni la descripción ni el saldo ni la cuenta) y `nucleo.columnas_extracto` (qué columna es
cada dato, una fila por edificio). Las cuatro con RLS habilitada y forzada; `cem_app` no las
toca: pasa por las funciones. No hay cuenta bancaria: el proyecto no tiene esa tabla, así que la
clave del documento 13 cuando no hay identificador, el hash de línea, queda por consorcio.

**Reimportar no duplica.** `(consorcio_id, hash_linea)` y `(consorcio_id, hash_archivo)` son únicas
y un pago tiene un solo cotejo. El hash lo calcula el backend (`app/consorcios/extracto.py`).

**`cotejar_por_extracto` no le cree a la API:** vuelve a mirar que el importe sea el mismo y que el
número, o a falta de número la fecha, coincidan.

**Exportación.** Lo cotejado y los movimientos son datos económicos del consorcio: son la evidencia
de que el pago salió del banco, y el paquete de soporte es íntegro (RF36, `0069`). `exportar_pagos`
suma `cotejo`, `importacion_extracto`, `movimiento_extracto` y `columnas_extracto`, con las reglas
de `0069`: el nombre de la persona y nunca sus identificadores.

Downgrade: se niega si hay datos: se perdería lo cotejado. `exportar_pagos` vuelve a lo de `0069`.

Revisión: 0074
Anterior: 0073
"""

from alembic import op
from sqlalchemy import text

revision = "0074"
down_revision = "0073"
branch_labels = None
depends_on = None

ORIGENES = ("manual", "extracto")
SIGNOS = ("con_signo", "sin_signo")
FACULTAD = "egresos.aprobar_orden"
"""La de `egresos.conciliar_pago` en las políticas: la base no conoce las acciones."""

_lista = ", ".join
_origenes = _lista(f"'{o}'" for o in ORIGENES)
_signos = _lista(f"'{s}'" for s in SIGNOS)

HISTORIA = (
    "nucleo.importacion_extracto",
    "nucleo.movimiento_extracto",
    "nucleo.cotejo_pago",
)
TABLAS = (*HISTORIA, "nucleo.columnas_extracto")

ESQUEMA = f"""
create table nucleo.importacion_extracto (
  consorcio_id       uuid not null references nucleo.consorcio (id),
  id                 uuid not null default plataforma.uuid_v7(),
  hash_archivo       text not null check (hash_archivo ~ '^[0-9a-f]{{64}}$'),
  desde              date not null,
  hasta              date not null,
  importado_por      uuid not null references nucleo.persona (id),
  identidad_importo  uuid not null,
  importado_en       timestamptz not null default clock_timestamp(),
  primary key (id),
  unique (consorcio_id, id),
  unique (consorcio_id, hash_archivo),
  check (desde <= hasta),
  foreign key (consorcio_id, identidad_importo)
    references nucleo.identidad_humana (consorcio_id, id)
);

create table nucleo.movimiento_extracto (
  consorcio_id      uuid not null,
  id                uuid not null default plataforma.uuid_v7(),
  importacion_id    uuid not null,
  hash_linea        text not null check (hash_linea ~ '^[0-9a-f]{{64}}$'),
  fecha             date not null,
  importe_centavos  bigint not null check (importe_centavos > 0),
  numero_operacion  text check (numero_operacion ~ '^[A-Z0-9]{{4,40}}$'),
  primary key (id),
  unique (consorcio_id, id),
  unique (consorcio_id, hash_linea),
  foreign key (consorcio_id, importacion_id)
    references nucleo.importacion_extracto (consorcio_id, id)
);

create table nucleo.cotejo_pago (
  consorcio_id      uuid not null,
  id                uuid not null default plataforma.uuid_v7(),
  pago_id           uuid not null,
  origen            text not null check (origen in ({_origenes})),
  movimiento_id     uuid unique,
  cotejado_por      uuid not null references nucleo.persona (id),
  identidad_cotejo  uuid not null,
  cotejado_en       timestamptz not null default clock_timestamp(),
  primary key (id),
  unique (consorcio_id, pago_id),
  check ((origen = 'extracto') = (movimiento_id is not null)),
  foreign key (consorcio_id, pago_id) references nucleo.pago_manual (consorcio_id, id),
  foreign key (consorcio_id, movimiento_id)
    references nucleo.movimiento_extracto (consorcio_id, id),
  foreign key (consorcio_id, identidad_cotejo)
    references nucleo.identidad_humana (consorcio_id, id)
);

create table nucleo.columnas_extracto (
  consorcio_id     uuid primary key references nucleo.consorcio (id),
  columna_fecha    text not null,
  columna_importe  text not null,
  columna_numero   text,
  signo            text not null check (signo in ({_signos})),
  guardadas_por    uuid not null references nucleo.persona (id),
  guardadas_en     timestamptz not null default clock_timestamp()
);
"""

SEGURIDAD = "".join(
    f"""
alter table {t} enable row level security;
alter table {t} force row level security;
create policy por_consorcio on {t} for select
  using (consorcio_id = plataforma.consorcio_actual());
revoke all on {t} from public, cem_app;
"""
    for t in TABLAS
) + "".join(
    f"""
create trigger historia_{t.split(".")[1]} before update or delete on {t}
  for each row execute function nucleo.negar_cambio_historia_orden_pago();
create trigger historia_{t.split(".")[1]}_truncate before truncate on {t}
  for each statement execute function nucleo.negar_cambio_historia_orden_pago();
"""
    for t in HISTORIA
)

INTERNAS_SQL = """
-- El último registro de cada ejecución que no terminó en «no se pagó», sea cual sea su
-- conciliación: lo que el banco tendría que mostrar. Un registro corregido ya no cuenta.
create function nucleo.pagos_del_cotejo(c uuid)
  returns table (pago_id uuid, orden_id uuid, ejecucion_id uuid, proveedor text, concepto text,
                 numero_operacion text, fecha_operacion date, importe_centavos bigint,
                 moneda text, conforme boolean, cotejado boolean)
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select p.id, e.orden_id, e.id, pv.razon_social, v.concepto, p.numero_operacion,
           p.fecha_operacion, p.importe_centavos, p.moneda,
           exists (select 1 from nucleo.conciliacion_pago_manual k
                    where k.pago_id = p.id and k.resultado = 'conforme'),
           exists (select 1 from nucleo.cotejo_pago t
                    where t.consorcio_id = c and t.pago_id = p.id)
      from nucleo.pago_manual p
      join nucleo.ejecucion_orden_pago e
        on e.consorcio_id = p.consorcio_id and e.id = p.ejecucion_id
      join nucleo.orden_pago_version v
        on v.consorcio_id = e.consorcio_id and v.orden_id = e.orden_id and v.numero = e.version
      join nucleo.proveedor_version pv
        on pv.consorcio_id = v.consorcio_id and pv.proveedor_id = v.proveedor_id
       and pv.numero = v.proveedor_version
     where p.consorcio_id = c
       and not exists (select 1 from nucleo.pago_manual q where q.corrige_a = p.id)
       and not exists (select 1 from nucleo.resultado_ejecucion_orden_pago r
                        where r.ejecucion_id = e.id and r.resultado = 'fallida')
  $$;

-- Las mismas separaciones que conciliar, como sí o no: solo para decidir qué se ofrece.
create function nucleo.admite_cotejar(c uuid, orden uuid, ejecucion uuid, actor uuid,
                                      identidad uuid)
  returns boolean
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  begin
    perform nucleo.exigir_no_ejecuto(c, ejecucion, actor, identidad);
    perform nucleo.exigir_no_preparo(c, orden, actor, identidad);
    return true;
  exception when raise_exception then
    return false;
  end
  $$;

-- Un acto por vez en cada consorcio: dos extractos a la vez no se disputan los mismos pagos.
create function nucleo.bloquear_cotejo(c uuid)
  returns void
  language sql volatile
  set search_path = pg_catalog, pg_temp
  as $$
    select pg_advisory_xact_lock(hashtextextended('cotejo:' || c::text, 0))
  $$;

create function nucleo.anotar_cotejo(c uuid, orden uuid, pago uuid, origen_cotejo text,
                                     movimiento uuid, actor uuid, identidad uuid)
  returns void
  language plpgsql volatile
  set search_path = pg_catalog, pg_temp
  as $$
  begin
    insert into nucleo.cotejo_pago
      (consorcio_id, pago_id, origen, movimiento_id, cotejado_por, identidad_cotejo)
    values (c, pago, origen_cotejo, movimiento, actor, identidad);
    perform nucleo.auditar_orden_pago(c, 'consorcio.orden_pago.cotejar_pago', orden, actor, null,
      jsonb_build_object('pago', pago, 'origen', origen_cotejo, 'movimiento', movimiento));
  end
  $$;
"""

RUTAS_SQL = f"""
-- Lo que falta cotejar: conciliado conforme y sin cotejo. Por `despues`, para que un pago cotejado
-- mientras tanto no corra la página.
create function nucleo.cotejo_lectura(despues uuid, limite integer, actor uuid,
                                      origen_pedido text, sesion uuid)
  returns table (pago_id uuid, orden_id uuid, proveedor text, concepto text,
                 numero_operacion text, fecha_operacion date, importe_centavos bigint,
                 moneda text, puede_cotejar boolean)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    identidad uuid;
  begin
    identidad := nucleo.exigir_acto_sin_gesto(c, actor, sesion, '{FACULTAD}', origen_pedido);
    return query
      select f.pago_id, f.orden_id, f.proveedor, f.concepto, f.numero_operacion,
             f.fecha_operacion, f.importe_centavos, f.moneda,
             nucleo.admite_cotejar(c, f.orden_id, f.ejecucion_id, actor, identidad)
        from nucleo.pagos_del_cotejo(c) f
       where f.conforme and not f.cotejado and (despues is null or f.pago_id > despues)
       order by f.pago_id
       limit limite;
  end
  $$;

-- Qué columna es cada dato en este edificio.
create function nucleo.columnas_extracto_lectura(actor uuid, origen_pedido text, sesion uuid)
  returns table (columna_fecha text, columna_importe text, columna_numero text, signo text)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
  begin
    perform nucleo.exigir_acto_sin_gesto(c, actor, sesion, '{FACULTAD}', origen_pedido);
    return query
      select x.columna_fecha, x.columna_importe, x.columna_numero, x.signo
        from nucleo.columnas_extracto x where x.consorcio_id = c;
  end
  $$;

-- Los pagos contra los que se compara un extracto: los del período o con alguno de sus números.
create function nucleo.pagos_para_comparar_lectura(numeros text[], desde date, hasta date,
                                                   actor uuid, origen_pedido text, sesion uuid)
  returns table (pago_id uuid, orden_id uuid, proveedor text, concepto text,
                 numero_operacion text, fecha_operacion date, importe_centavos bigint,
                 moneda text, conforme boolean, cotejado boolean, puede_cotejar boolean)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    identidad uuid;
  begin
    identidad := nucleo.exigir_acto_sin_gesto(c, actor, sesion, '{FACULTAD}', origen_pedido);
    return query
      select f.pago_id, f.orden_id, f.proveedor, f.concepto, f.numero_operacion,
             f.fecha_operacion, f.importe_centavos, f.moneda, f.conforme, f.cotejado,
             nucleo.admite_cotejar(c, f.orden_id, f.ejecucion_id, actor, identidad)
        from nucleo.pagos_del_cotejo(c) f
       where f.fecha_operacion between desde and hasta or f.numero_operacion = any(numeros)
       order by f.pago_id;
  end
  $$;

-- A mano. Lo que otra persona cotejó mientras tanto no es un error: ya está.
create function nucleo.cotejar_pagos(pagos uuid[], actor uuid, origen_pedido text, sesion uuid)
  returns integer
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    identidad uuid;
    f record;
    vistos integer := 0;
    hechos integer := 0;
  begin
    identidad := nucleo.exigir_acto_economico(c, actor, sesion, '{FACULTAD}', origen_pedido);
    perform nucleo.bloquear_cotejo(c);
    for f in select * from nucleo.pagos_del_cotejo(c) x where x.pago_id = any(pagos) loop
      vistos := vistos + 1;
      if not f.conforme then
        raise exception 'ese pago no está conciliado' using errcode = 'P0001',
          hint = 'pago_sin_conciliar';
      end if;
      perform nucleo.exigir_no_ejecuto(c, f.ejecucion_id, actor, identidad);
      perform nucleo.exigir_no_preparo(c, f.orden_id, actor, identidad);
      if not f.cotejado then
        perform nucleo.anotar_cotejo(c, f.orden_id, f.pago_id, 'manual', null, actor, identidad);
        hechos := hechos + 1;
      end if;
    end loop;
    if vistos <> (select count(distinct x) from unnest(pagos) x) then
      raise exception 'ese pago no existe en este consorcio' using errcode = 'P0001',
        hint = 'no_encontrado';
    end if;
    return hechos;
  end
  $$;

-- Guarda el archivo, sus líneas nuevas y qué columna es cada dato. Devuelve todas las líneas del
-- archivo, nuevas o no, con el pago que ya tienen.
create function nucleo.importar_extracto(archivo text, desde_fecha date, hasta_fecha date,
                                         movimientos jsonb, col_fecha text, col_importe text,
                                         col_numero text, signo_importe text, actor uuid,
                                         origen_pedido text, sesion uuid)
  returns table (movimiento_id uuid, fecha date, importe_centavos bigint,
                 numero_operacion text, nuevo boolean, pago_id uuid)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    identidad uuid;
    importacion uuid;
    nuevos text[];
  begin
    identidad := nucleo.exigir_acto_economico(c, actor, sesion, '{FACULTAD}', origen_pedido);
    perform nucleo.bloquear_cotejo(c);
    insert into nucleo.importacion_extracto
      (consorcio_id, hash_archivo, desde, hasta, importado_por, identidad_importo)
    values (c, archivo, desde_fecha, hasta_fecha, actor, identidad)
    on conflict (consorcio_id, hash_archivo) do nothing
    returning id into importacion;
    if importacion is null then
      select x.id into strict importacion from nucleo.importacion_extracto x
       where x.consorcio_id = c and x.hash_archivo = archivo;
    end if;
    with insertados as (
      insert into nucleo.movimiento_extracto as t
        (consorcio_id, importacion_id, hash_linea, fecha, importe_centavos, numero_operacion)
      select c, importacion, m.hash_linea, m.fecha, m.importe_centavos, m.numero_operacion
        from jsonb_to_recordset(movimientos)
          as m(hash_linea text, fecha date, importe_centavos bigint, numero_operacion text)
      on conflict on constraint movimiento_extracto_consorcio_id_hash_linea_key do nothing
      returning t.hash_linea
    )
    select coalesce(array_agg(i.hash_linea), '{{}}') into nuevos from insertados i;
    insert into nucleo.columnas_extracto as x
      (consorcio_id, columna_fecha, columna_importe, columna_numero, signo, guardadas_por)
    values (c, col_fecha, col_importe, col_numero, signo_importe, actor)
    on conflict on constraint columnas_extracto_pkey do update
      set columna_fecha = excluded.columna_fecha, columna_importe = excluded.columna_importe,
          columna_numero = excluded.columna_numero, signo = excluded.signo,
          guardadas_por = excluded.guardadas_por, guardadas_en = clock_timestamp()
      where (x.columna_fecha, x.columna_importe, x.columna_numero, x.signo)
            is distinct from (excluded.columna_fecha, excluded.columna_importe,
                              excluded.columna_numero, excluded.signo);
    insert into auditoria.eventos
      (consorcio_id, actor_persona_id, origen, accion, entidad, entidad_id, antes, despues,
       hash_previo, hash)
    values (c, actor, 'api', 'consorcio.extracto.importar', 'importacion_extracto', importacion,
            null, jsonb_build_object('desde', desde_fecha, 'hasta', hasta_fecha,
                                     'movimientos_nuevos', cardinality(nuevos)), '', '');
    return query
      select m.id, m.fecha, m.importe_centavos, m.numero_operacion,
             m.hash_linea = any(nuevos), t.pago_id
        from nucleo.movimiento_extracto m
        left join nucleo.cotejo_pago t
          on t.consorcio_id = m.consorcio_id and t.movimiento_id = m.id
       where m.consorcio_id = c
         and m.hash_linea in (select y.hash_linea from jsonb_to_recordset(movimientos)
                                as y(hash_linea text))
       order by m.id;
  end
  $$;

-- Lo que coincidió. `pares`: `movimiento` y `pago`. Un pago ya cotejado es una carrera perdida.
create function nucleo.cotejar_por_extracto(pares jsonb, actor uuid, origen_pedido text,
                                            sesion uuid)
  returns integer
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    identidad uuid;
    par record;
    f record;
    m nucleo.movimiento_extracto;
    hechos integer := 0;
  begin
    identidad := nucleo.exigir_acto_economico(c, actor, sesion, '{FACULTAD}', origen_pedido);
    perform nucleo.bloquear_cotejo(c);
    for par in select * from jsonb_to_recordset(pares) as x(movimiento uuid, pago uuid) loop
      select * into m from nucleo.movimiento_extracto y
       where y.consorcio_id = c and y.id = par.movimiento;
      select * into f from nucleo.pagos_del_cotejo(c) x where x.pago_id = par.pago;
      if m.id is null or f.pago_id is null then
        raise exception 'ese movimiento o ese pago no existe en este consorcio'
          using errcode = 'P0001', hint = 'no_encontrado';
      end if;
      if not f.conforme then
        raise exception 'ese pago no está conciliado' using errcode = 'P0001',
          hint = 'pago_sin_conciliar';
      end if;
      if m.importe_centavos <> f.importe_centavos
         or (m.numero_operacion is not null and m.numero_operacion <> f.numero_operacion)
         or (m.numero_operacion is null and m.fecha <> f.fecha_operacion) then
        raise exception 'el movimiento no coincide con el pago' using errcode = 'P0001',
          hint = 'movimiento_no_coincide';
      end if;
      perform nucleo.exigir_no_ejecuto(c, f.ejecucion_id, actor, identidad);
      perform nucleo.exigir_no_preparo(c, f.orden_id, actor, identidad);
      if not f.cotejado then
        perform nucleo.anotar_cotejo(c, f.orden_id, f.pago_id, 'extracto', m.id, actor, identidad);
        hechos := hechos + 1;
      end if;
    end loop;
    return hechos;
  end
  $$;
"""

RUTAS = (
    "nucleo.cotejo_lectura(uuid, integer, uuid, text, uuid)",
    "nucleo.columnas_extracto_lectura(uuid, text, uuid)",
    "nucleo.pagos_para_comparar_lectura(text[], date, date, uuid, text, uuid)",
    "nucleo.cotejar_pagos(uuid[], uuid, text, uuid)",
    "nucleo.importar_extracto(text, date, date, jsonb, text, text, text, text, uuid, text, uuid)",
    "nucleo.cotejar_por_extracto(jsonb, uuid, text, uuid)",
)
INTERNAS = (
    "nucleo.anotar_cotejo(uuid, uuid, uuid, text, uuid, uuid, uuid)",
    "nucleo.bloquear_cotejo(uuid)",
    "nucleo.admite_cotejar(uuid, uuid, uuid, uuid, uuid)",
    "nucleo.pagos_del_cotejo(uuid)",
)

EXPORTAR_PAGOS = "nucleo.exportar_pagos(uuid, uuid)"
FIN_DE_EXPORTAR_PAGOS = """\
       order by f.confirmado_en, f.id;
"""
"""El final de `exportar_pagos` (`0069`): lo nuevo va después."""
EXPORTAR_COTEJO = """\
    return query
      select 'cotejo'::text,
             jsonb_build_object('id', t.id, 'pago_id', t.pago_id, 'origen', t.origen,
                                'movimiento_id', t.movimiento_id,
                                'cotejado_por_nombre',
                                nucleo.nombre_para_exportar(c, t.cotejado_por, hoy),
                                'cotejado_en', t.cotejado_en)
        from nucleo.cotejo_pago t
       where t.consorcio_id = c
       order by t.cotejado_en, t.id;
    return query
      select 'importacion_extracto'::text,
             jsonb_build_object('id', i.id, 'hash_archivo', i.hash_archivo, 'desde', i.desde,
                                'hasta', i.hasta,
                                'importado_por_nombre',
                                nucleo.nombre_para_exportar(c, i.importado_por, hoy),
                                'importado_en', i.importado_en)
        from nucleo.importacion_extracto i
       where i.consorcio_id = c
       order by i.importado_en, i.id;
    return query
      select 'movimiento_extracto'::text,
             jsonb_build_object('id', m.id, 'importacion_id', m.importacion_id,
                                'hash_linea', m.hash_linea, 'fecha', m.fecha,
                                'importe_centavos', m.importe_centavos,
                                'numero_operacion', m.numero_operacion)
        from nucleo.movimiento_extracto m
       where m.consorcio_id = c
       order by m.fecha, m.id;
    return query
      select 'columnas_extracto'::text,
             jsonb_build_object('columna_fecha', nucleo.texto_sin_cuenta(x.columna_fecha),
                                'columna_importe', nucleo.texto_sin_cuenta(x.columna_importe),
                                'columna_numero', nucleo.texto_sin_cuenta(x.columna_numero),
                                'signo', x.signo,
                                'guardadas_por_nombre',
                                nucleo.nombre_para_exportar(c, x.guardadas_por, hoy),
                                'guardadas_en', x.guardadas_en)
        from nucleo.columnas_extracto x
       where x.consorcio_id = c;
"""

GUARDA_DOWNGRADE = """
lock table nucleo.importacion_extracto, nucleo.cotejo_pago, nucleo.columnas_extracto
  in share row exclusive mode;

do $$
begin
  if exists (select 1 from nucleo.importacion_extracto)
     or exists (select 1 from nucleo.cotejo_pago)
     or exists (select 1 from nucleo.columnas_extracto) then
    raise exception '0074 no baja con cotejos ni extractos: se perdería lo cotejado'
      using errcode = 'P0001', hint = 'downgrade_con_datos';
  end if;
end
$$;
"""


def _cambiar_exportar_pagos(antes: str, despues: str) -> None:
    definicion = (
        op.get_bind()
        .execute(text("select pg_get_functiondef(cast(:f as regprocedure))"), {"f": EXPORTAR_PAGOS})
        .scalar_one()
    )
    if definicion.count(antes) != 1:
        raise RuntimeError(f"0074: exportar_pagos no tiene una sola vez {antes!r}")
    op.get_bind().exec_driver_sql(definicion.replace(antes, despues))


def upgrade() -> None:
    for sql in (ESQUEMA, SEGURIDAD, INTERNAS_SQL, RUTAS_SQL):
        op.execute(sql)
    for funcion in INTERNAS + RUTAS:
        op.execute(f"revoke all on function {funcion} from public")
    for funcion in RUTAS:
        op.execute(f"grant execute on function {funcion} to cem_app")
    _cambiar_exportar_pagos(FIN_DE_EXPORTAR_PAGOS, FIN_DE_EXPORTAR_PAGOS + EXPORTAR_COTEJO)


def downgrade() -> None:
    op.execute(GUARDA_DOWNGRADE)
    _cambiar_exportar_pagos(FIN_DE_EXPORTAR_PAGOS + EXPORTAR_COTEJO, FIN_DE_EXPORTAR_PAGOS)
    for funcion in RUTAS + INTERNAS:
        op.execute(f"drop function {funcion}")
    for tabla in ("cotejo_pago", "movimiento_extracto", "importacion_extracto",
                  "columnas_extracto"):  # fmt: skip
        op.execute(f"drop table nucleo.{tabla}")
