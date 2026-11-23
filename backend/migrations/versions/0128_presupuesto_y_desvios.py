"""El presupuesto del consorcio, los desvíos por rubro y los vencimientos en el panel (RF15 en su
forma de Modo Consejo, RF48; diseño P0 S9).

En Modo Consejo el presupuesto no sale de la importación: lo aprueba la asamblea y el Consejo lo
registra tal como se aprobó. Lo liquidado sale de las liquidaciones oficiales (la confirmada de cada
período, `0119`), en la misma ventana del panel (`ultimas_liquidaciones_oficiales`, `0120`). Ninguna
cifra se estima: un rubro sin presupuesto se dice «sin presupuesto», y uno presupuestado que la
liquidación no trae no se cuenta como gasto cero.

**`presupuesto.registrar`** (decisión por recomendación del 22-11-2026): registra el presupuesto
aprobado. No es económica: no mueve dinero ni autoriza gastos, deja constancia de lo que decidió la
asamblea. Entra en los paquetes `miembro_consejo` y `administrador`; como en `0036`, `0113` y
`0122`, un acta reduce un paquete y nunca lo amplía: los mandatos ya registrados no la reciben. Solo
Modo Consejo en este corte (lo liquidado sale de las liquidaciones importadas). La ayuda por una
persona autorizada no la admite (RF53, `0127`): registrar es decisorio.

**Los datos.** Dos tablas, una fila por hecho, sin cambios ni borrado
(`negar_cambio_presupuesto`), con RLS forzada por consorcio y sin acceso directo de `cem_app`:

- `nucleo.presupuesto`: el rango de períodos (`desde` y `hasta`, primeros días de mes, ambos
  incluidos), de dónde sale (`aprobacion`, p. ej. «Asamblea ordinaria del 12-03-2026»), quién lo
  registró y cuándo, y `reemplaza_a` con su `motivo`: una corrección es otra versión que reemplaza a
  una vigente, nunca un cambio. La reemplazada queda como historia. Ni la aprobación ni el motivo
  llevan una cuenta (`texto_con_cuenta`, `0060`).
- `nucleo.presupuesto_rubro`: el importe **mensual** de cada rubro, por su número (la clave de los
  rubros de la importación, `rubro:<número>`) y con el nombre con que se aprobó.

Un presupuesto es **vigente** mientras ninguno lo reemplaza. Dos vigentes no se superponen en
ningún período (`presupuesto_superpuesto`); lo controla el acto bajo el candado del presupuesto del
consorcio.

**Los desvíos** (`panel_desvios_presupuesto`, barrera `exigir_ver_panel` de `0094`): por cada
período de la ventana y cada rubro (por número), lo presupuestado por el vigente que cubre el
período y lo liquidado por la oficial. `desvio` = liquidado − presupuestado y `porcentaje` =
desvio × 100 / presupuestado, redondeado a dos decimales; solo si están los dos importes (y el
porcentaje, con lo presupuestado mayor que cero). Sin presupuesto, o sin el rubro en la liquidación,
no hay desvío.

**Los vencimientos** (`panel_vencimientos_liquidacion_vigente`): los que trae el dato oficial
`total:gastos` de la liquidación vigente (la lectura extrae «1er vencimiento» y «2do vencimiento»,
H5-1). Nada que no esté ahí.

**Exportación** (`0069`): la parte nueva `presupuestos`, con los nombres y nunca los identificadores
de las personas, y los textos por `texto_sin_cuenta`.

Downgrade: se niega (`downgrade_con_datos`) si hay presupuestos o algún mandato con la facultad.

Datos de prueba: SINTETICOS / NO REAL.

Revisión: 0128
Anterior: 0127
"""

import importlib.util
from pathlib import Path

from alembic import op

revision = "0128"
down_revision = "0127"
branch_labels = None
depends_on = None

FACULTAD = "presupuesto.registrar"
ACCION = "consorcio.presupuesto.registrar"
LARGO_APROBACION = 200
LARGO_MOTIVO = 500
LARGO_NOMBRE_RUBRO = 200
MAXIMO_RUBROS = 60
"""Los de `cem_contracts.presupuesto`; una prueba compara. El del nombre del rubro es el de la
importación (`cem_contracts.importacion_liquidacion.LARGO_NOMBRE_RUBRO`), sin recortarlo."""
CLAVE_TOTAL = "total:gastos"
"""El dato con los totales del gasto y los vencimientos (`importacion.campos`)."""
TABLAS = ("nucleo.presupuesto", "nucleo.presupuesto_rubro")
EXPORTAR = "exportar_presupuestos"


def _largo(columna: str, maximo: int) -> str:
    return f"length(btrim({columna})) between 1 and {maximo} and {columna} = btrim({columna})"


def _primer_dia(columna: str) -> str:
    return f"{columna} = date_trunc('month', {columna})::date"


TABLAS_SQL = f"""
create function nucleo.negar_cambio_presupuesto()
  returns trigger
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  begin
    raise exception 'un presupuesto no se cambia ni se borra: se reemplaza con otra versión'
      using errcode = 'P0001', hint = 'historia_inmutable';
  end
  $$;
revoke all on function nucleo.negar_cambio_presupuesto() from public;

create table nucleo.presupuesto (
  consorcio_id    uuid not null references nucleo.consorcio (id),
  id              uuid primary key default plataforma.uuid_v7(),
  desde           date not null check ({_primer_dia("desde")}),
  hasta           date not null check ({_primer_dia("hasta")}),
  aprobacion      text not null
    check ({_largo("aprobacion", LARGO_APROBACION)} and aprobacion !~ '[[:cntrl:]]'),
  reemplaza_a     uuid unique,
  motivo          text check (motivo is null or {_largo("motivo", LARGO_MOTIVO)}),
  registrado_por  uuid not null references nucleo.persona (id),
  registrado_en   timestamptz not null default clock_timestamp(),
  check (hasta >= desde),
  check ((reemplaza_a is null) = (motivo is null)),
  unique (consorcio_id, id),
  foreign key (consorcio_id, reemplaza_a) references nucleo.presupuesto (consorcio_id, id)
);

create table nucleo.presupuesto_rubro (
  consorcio_id    uuid not null,
  presupuesto_id  uuid not null,
  numero          integer not null check (numero >= 1),
  nombre          text not null
    check ({_largo("nombre", LARGO_NOMBRE_RUBRO)} and nombre !~ '[[:cntrl:]]'),
  importe         numeric(18, 2) not null check (importe >= 0),
  primary key (presupuesto_id, numero),
  foreign key (consorcio_id, presupuesto_id) references nucleo.presupuesto (consorcio_id, id)
);
"""


def _protecciones(tabla: str) -> str:
    corto = tabla.removeprefix("nucleo.")
    return f"""
alter table {tabla} enable row level security;
alter table {tabla} force row level security;
create policy por_consorcio on {tabla} for select
  using (consorcio_id = plataforma.consorcio_actual());
revoke all on {tabla} from public, cem_app;
create trigger historia_{corto} before update or delete on {tabla}
  for each row execute function nucleo.negar_cambio_presupuesto();
create trigger historia_{corto}_truncate before truncate on {tabla}
  for each statement execute function nucleo.negar_cambio_presupuesto();
"""


FUNCIONES = f"""
-- Vigente: ninguna otra versión lo reemplaza.
create function nucleo.presupuesto_vigente(c uuid, presupuesto uuid)
  returns boolean
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select not exists (select 1 from nucleo.presupuesto r
                        where r.consorcio_id = c and r.reemplaza_a = presupuesto)
  $$;

-- Quien registra: la persona de la sesión (sin ayuda en curso: `sesion_de_quien_actua` sin
-- alcance la niega a quien ayuda, `0127`), por la API, en Modo Consejo y con `{FACULTAD}` vigente.
create function nucleo.exigir_registrar_presupuesto(c uuid, actor uuid, sesion uuid,
                                                    origen_pedido text)
  returns void
  language plpgsql volatile
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    hoy date;
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
    hoy := nucleo.hoy_del_consorcio(c);
    -- El candado del modo de `0090`: un cambio de modo en curso termina antes de leerlo.
    perform pg_advisory_xact_lock_shared(hashtextextended('modo:' || c::text, 0));
    if not exists (
         select 1 from nucleo.modo_operacion m
          where m.consorcio_id = c and m.vigente_desde <= hoy
            and (m.vigente_hasta is null or m.vigente_hasta > hoy) and m.modo = 'consejo') then
      raise exception 'sin Modo Consejo vigente' using errcode = 'P0001',
        hint = 'modo_no_admitido';
    end if;
    if not nucleo.sesion_de_quien_actua(c, actor, sesion, null) then
      raise exception 'la sesión no es válida' using errcode = 'P0001', hint = 'sesion_invalida';
    end if;
    if not nucleo.tiene_facultad_vigente(c, actor, '{FACULTAD}', hoy) then
      raise exception 'sin {FACULTAD} en este consorcio' using errcode = '42501';
    end if;
  end
  $$;

-- Registrar: un presupuesto nuevo o la versión que reemplaza a uno vigente, con su motivo.
-- `rubros` = [{{numero, nombre, importe}}, …], el importe mensual de cada rubro.
create function nucleo.registrar_presupuesto(desde_nuevo date, hasta_nuevo date,
                                             aprobacion_nueva text, rubros jsonb,
                                             reemplaza uuid, motivo_nuevo text, actor uuid,
                                             origen_pedido text, sesion uuid)
  returns uuid
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    i uuid;
  begin
    perform nucleo.exigir_registrar_presupuesto(c, actor, sesion, origen_pedido);
    -- Un registro a la vez por consorcio: la superposición se decide sobre lo ya registrado.
    perform pg_advisory_xact_lock(hashtextextended('presupuesto:' || c::text, 0));
    if nucleo.texto_con_cuenta(aprobacion_nueva) or nucleo.texto_con_cuenta(motivo_nuevo) then
      raise exception 'un presupuesto no puede llevar una cuenta' using errcode = 'P0001',
        hint = 'texto_con_cuenta';
    end if;
    if rubros is null or jsonb_typeof(rubros) <> 'array' or jsonb_array_length(rubros) = 0
       or jsonb_array_length(rubros) > {MAXIMO_RUBROS} then
      raise exception 'el presupuesto lleva entre 1 y {MAXIMO_RUBROS} rubros'
        using errcode = 'P0001', hint = 'rubros_invalidos';
    end if;
    if (select count(distinct r->>'numero') from jsonb_array_elements(rubros) r)
       <> jsonb_array_length(rubros) then
      raise exception 'un rubro repetido' using errcode = 'P0001', hint = 'rubros_invalidos';
    end if;
    if reemplaza is not null and not exists (
         select 1 from nucleo.presupuesto x
          where x.consorcio_id = c and x.id = reemplaza
            and nucleo.presupuesto_vigente(c, x.id)) then
      raise exception 'lo que se reemplaza no es un presupuesto vigente de este consorcio'
        using errcode = 'P0001', hint = 'reemplazo_invalido';
    end if;
    if exists (
         select 1 from nucleo.presupuesto x
          where x.consorcio_id = c and x.id is distinct from reemplaza
            and nucleo.presupuesto_vigente(c, x.id)
            and daterange(x.desde, x.hasta, '[]')
                && daterange(desde_nuevo, hasta_nuevo, '[]')) then
      raise exception 'otro presupuesto vigente cubre alguno de esos períodos'
        using errcode = 'P0001', hint = 'presupuesto_superpuesto';
    end if;
    insert into nucleo.presupuesto
      (consorcio_id, desde, hasta, aprobacion, reemplaza_a, motivo, registrado_por)
    values (c, desde_nuevo, hasta_nuevo, aprobacion_nueva, reemplaza, motivo_nuevo, actor)
    returning id into i;
    insert into nucleo.presupuesto_rubro (consorcio_id, presupuesto_id, numero, nombre, importe)
    select c, i, (r->>'numero')::integer, r->>'nombre', (r->>'importe')::numeric(18, 2)
      from jsonb_array_elements(rubros) r;
    insert into auditoria.eventos
      (consorcio_id, actor_persona_id, origen, accion, entidad, entidad_id, despues,
       hash_previo, hash)
    values (c, actor, origen_pedido, '{ACCION}', 'presupuesto', i,
            jsonb_build_object('desde', desde_nuevo, 'hasta', hasta_nuevo,
                               'reemplaza_a', reemplaza,
                               'rubros', jsonb_array_length(rubros)),
            '', '');
    return i;
  end
  $$;

-- El Consejo y la administración (`fiscalizacion.ver_panel`), y quien registra (`{FACULTAD}`, que
-- lee lo que registró aunque un acta le haya dejado solo esa): todas las versiones, la más nueva
-- primero, con sus rubros por número.
create function nucleo.presupuestos_lectura()
  returns table (id uuid, desde date, hasta date, aprobacion text, reemplaza_a uuid,
                 motivo text, reemplazado_por uuid, registrado_por_nombre text,
                 registrado_en timestamptz, rubros jsonb)
  language plpgsql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    yo uuid := plataforma.persona_actual();
  begin
    if c is null or yo is null or not nucleo.tiene_facultad_vigente(
         c, yo, '{FACULTAD}', nucleo.hoy_del_consorcio(c)) then
      perform nucleo.exigir_ver_panel(c, yo);
    end if;
    return query
      select x.id, x.desde, x.hasta, x.aprobacion, x.reemplaza_a, x.motivo,
             (select r.id from nucleo.presupuesto r
               where r.consorcio_id = c and r.reemplaza_a = x.id),
             pe.nombre, x.registrado_en,
             -- El importe como texto: el JSON con números lo leería como float.
             (select jsonb_agg(jsonb_build_object('numero', b.numero, 'nombre', b.nombre,
                                                  'importe', b.importe::text)
                               order by b.numero)
                from nucleo.presupuesto_rubro b
               where b.consorcio_id = c and b.presupuesto_id = x.id)
        from nucleo.presupuesto x
        join nucleo.persona pe on pe.id = x.registrado_por
       where x.consorcio_id = c
       order by x.registrado_en desc, x.id desc;
  end
  $$;

-- El panel: por período de la ventana y por rubro (su número), lo presupuestado por el vigente
-- que cubre el período y lo liquidado por la oficial. El desvío, solo con los dos importes.
create function nucleo.panel_desvios_presupuesto(cuantos integer)
  returns table (periodo date, numero integer, nombre text, presupuesto_id uuid,
                 presupuestado numeric(18, 2), liquidado numeric(18, 2), desvio numeric(18, 2),
                 porcentaje numeric(18, 2))
  language plpgsql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
  begin
    perform nucleo.exigir_ver_panel(c, plataforma.persona_actual());
    return query
      with u as (
        select w.id, w.periodo from nucleo.ultimas_liquidaciones_oficiales(c, cuantos) w
      ), cobertura as (
        select u.periodo,
               (select p.id from nucleo.presupuesto p
                 where p.consorcio_id = c and u.periodo between p.desde and p.hasta
                   and nucleo.presupuesto_vigente(c, p.id)) as cubre
          from u
      ), de_la_liquidacion as (
        select u.periodo, (f.valor->>'numero')::integer as numero, f.valor->>'nombre' as nombre,
               (f.valor->>'total')::numeric(18, 2) as total, f.orden
          from u cross join lateral nucleo.campos_oficiales_de(c, u.id) f
         where f.tipo = 'rubro'
      ), del_presupuesto as (
        select v.periodo, b.numero, b.nombre, b.importe
          from cobertura v
          join nucleo.presupuesto_rubro b
            on b.consorcio_id = c and b.presupuesto_id = v.cubre
      )
      select coalesce(l.periodo, b.periodo), coalesce(l.numero, b.numero),
             coalesce(l.nombre, b.nombre), v.cubre, b.importe, l.total,
             case when l.total is not null and b.importe is not null
                  then l.total - b.importe end,
             case when l.total is not null and b.importe > 0
                  then round((l.total - b.importe) * 100 / b.importe, 2) end
        from de_la_liquidacion l
        full join del_presupuesto b on b.periodo = l.periodo and b.numero = l.numero
        join cobertura v on v.periodo = coalesce(l.periodo, b.periodo)
       order by 1, l.orden nulls last, 2;
  end
  $$;

-- El panel: los vencimientos que trae el dato oficial de los totales de la liquidación vigente,
-- tal como los leyó la importación (AAAA-MM-DD). Sin filas si no hay ninguna oficial.
create function nucleo.panel_vencimientos_liquidacion_vigente()
  returns table (id uuid, vencimientos jsonb)
  language plpgsql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    vigente uuid;
  begin
    perform nucleo.exigir_ver_panel(c, plataforma.persona_actual());
    vigente := nucleo.liquidacion_vigente(c, null);
    if vigente is null then
      return;
    end if;
    return query
      select vigente, coalesce((select f.valor->'vencimientos'
                                  from nucleo.campos_oficiales_de(c, vigente) f
                                 where f.clave = '{CLAVE_TOTAL}'), '[]'::jsonb);
  end
  $$;
"""

RUTAS = (
    "nucleo.registrar_presupuesto(date, date, text, jsonb, uuid, text, uuid, text, uuid)",
    "nucleo.presupuestos_lectura()",
    "nucleo.panel_desvios_presupuesto(integer)",
    "nucleo.panel_vencimientos_liquidacion_vigente()",
)
INTERNAS = (
    "nucleo.presupuesto_vigente(uuid, uuid)",
    "nucleo.exigir_registrar_presupuesto(uuid, uuid, uuid, text)",
)


def _de_0069():
    """`_funcion` de `0069`: el mismo encabezado, las mismas exigencias y los mismos permisos.
    `0069` ya está aplicada y no cambia."""
    ruta = Path(__file__).with_name("0069_exportacion_economica.py")
    spec = importlib.util.spec_from_file_location("migracion_0069", ruta)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


CUERPO_EXPORTAR = """
    hoy := nucleo.hoy_del_consorcio(c);
    return query
      select 'presupuesto'::text,
             jsonb_build_object('id', x.id, 'desde', x.desde, 'hasta', x.hasta,
                                'aprobacion', nucleo.texto_sin_cuenta(x.aprobacion),
                                'reemplaza_a', x.reemplaza_a,
                                'motivo', nucleo.texto_sin_cuenta(x.motivo),
                                'registrado_por_nombre',
                                nucleo.nombre_para_exportar(c, x.registrado_por, hoy),
                                'registrado_en', x.registrado_en)
        from nucleo.presupuesto x
       where x.consorcio_id = c
       order by x.registrado_en, x.id;
    return query
      select 'presupuesto_rubro'::text,
             jsonb_build_object('presupuesto_id', b.presupuesto_id, 'numero', b.numero,
                                'nombre', nucleo.texto_sin_cuenta(b.nombre),
                                'importe', b.importe)
        from nucleo.presupuesto_rubro b
        join nucleo.presupuesto x on x.consorcio_id = b.consorcio_id and x.id = b.presupuesto_id
       where b.consorcio_id = c
       order by x.registrado_en, x.id, b.numero;
"""

GUARDA = f"""
do $guarda$
begin
  if exists (select 1 from nucleo.presupuesto)
     or exists (select 1 from nucleo.facultad_otorgada where facultad = '{FACULTAD}') then
    raise exception '0128 no baja: hay presupuestos o mandatos con la facultad'
      using errcode = 'P0001', hint = 'downgrade_con_datos';
  end if;
end
$guarda$;
"""


def upgrade() -> None:
    op.execute(
        f"insert into nucleo.facultad (codigo, area, economica)"
        f" values ('{FACULTAD}', 'presupuesto', false);"
        f" insert into nucleo.paquete_facultad (tipo, facultad)"
        f" values ('miembro_consejo', '{FACULTAD}'), ('administrador', '{FACULTAD}');"
    )
    op.execute(TABLAS_SQL)
    for tabla in TABLAS:
        op.execute(_protecciones(tabla))
    op.execute(FUNCIONES)
    for funcion in INTERNAS + RUTAS:
        op.execute(f"revoke all on function {funcion} from public")
    for funcion in RUTAS:
        op.execute(f"grant execute on function {funcion} to cem_app")
    op.execute(_de_0069()._funcion(EXPORTAR, CUERPO_EXPORTAR))
    op.execute(
        "insert into auditoria.proyeccion (accion, texto, recurso, campos) values"
        f" ('{ACCION}', 'Registró un presupuesto', 'Presupuesto',"
        " array['desde', 'hasta', 'reemplaza_a', 'rubros'])"
    )


def downgrade() -> None:
    op.execute(GUARDA)
    op.execute(f"delete from auditoria.proyeccion where accion = '{ACCION}'")
    op.execute(f"drop function nucleo.{EXPORTAR}(uuid, uuid)")
    for funcion in reversed(INTERNAS + RUTAS):
        op.execute(f"drop function {funcion}")
    for tabla in reversed(TABLAS):
        op.execute(f"drop table {tabla}")
    op.execute("drop function nucleo.negar_cambio_presupuesto()")
    op.execute(f"delete from nucleo.paquete_facultad where facultad = '{FACULTAD}'")
    op.execute(f"delete from nucleo.facultad where codigo = '{FACULTAD}'")
