"""Lectura con IA de la liquidación que el formato conocido no resuelve, H5-2 (diseño H5 §5; PA25).

Cuando la lectura determinista (`0113`) no reconoce el formato, `cem-trabajos` le pasa el texto del
PDF al modelo (`app/liquidacion/lectura_ia.py`) y guarda lo leído como campos preliminares con
origen `ia`. Esta migración pone lo que la base tiene que saber para eso:

**Formato `lectura_ia`** en `nucleo.importacion_liquidacion`: la importación leída por el modelo.

**Tope de gasto** (D4, 19-11-2026: 20 dólares por mes en total para la importación).

- `plataforma.tope_gasto_ia`: el tope por uso, en dólares por mes, y la zona horaria del mes.
- `plataforma.precio_modelo_ia`: el precio por millón de fichas de cada modelo, con su fuente.
- `plataforma.gasto_ia`: una fila por llamada. **Antes** de llamar, `reservar_gasto_ia` anota el
  costo máximo de la llamada (las fichas de entrada acotadas por el tamaño del texto y las de
  salida por `max_tokens`, al precio del modelo que se va a pedir, `CEM_LLM_MODEL`) y la niega si
  con ella se pasa el tope. Un modelo pedido sin precio no se llama: sin precio no hay cota.
  **Después**, `registrar_gasto_ia` anota el modelo, las fichas y el costo real. Una reserva sin
  registrar (la llamada se cortó) o un modelo informado sin precio (el proveedor puede informar un
  nombre distinto del pedido) cuentan por su costo máximo: el tope nunca queda por debajo de lo
  gastado.
- Una sola reserva por importación: una lectura repetida (un reintento del trabajo) no vuelve a
  llamar al modelo.
- Las dos funciones toman el mismo candado por uso, así que dos lecturas a la vez no pasan el tope
  entre las dos.

El gasto es de la plataforma, no de un consorcio: la tabla va sin RLS, como `plataforma.jobs`, y
solo la tocan sus funciones.

Downgrade: se niega (`downgrade_con_datos`) si hay gasto registrado o importaciones leídas con IA.

Revisión: 0114
Anterior: 0113
"""

from alembic import op

revision = "0114"
down_revision = "0113"
branch_labels = None
depends_on = None

USO = "liquidacion.leer"
"""El trabajo de `TRABAJO_LEER`, en `app/liquidacion/importacion.py`."""
TOPE_USD_POR_MES = "20.00"
"""D4 del propietario (19-11-2026)."""
ZONA_DEL_MES = "America/Argentina/Buenos_Aires"
"""La de `nucleo.consorcio.zona_horaria` por defecto (`0001`): el mes del tope, el de la Ciudad."""
PRECIOS = (
    (
        "claude-sonnet-5-5",
        "2.0000",
        "10.0000",
        "Precios publicados de Anthropic para Claude Sonnet 5.5 al 20-11-2026: 2 dólares por millón"
        " de fichas de entrada y 10 por millón de salida. Es el modelo que informa el proveedor en"
        " las lecturas de staging.",
    ),
)

TABLAS = """
alter table nucleo.importacion_liquidacion
  drop constraint importacion_liquidacion_formato_check,
  add constraint importacion_liquidacion_formato_check
    check (formato in ('mis_expensas', 'lectura_ia'));

create table plataforma.tope_gasto_ia (
  uso           text primary key check (length(uso) between 1 and 100),
  usd_por_mes   numeric(10, 2) not null check (usd_por_mes >= 0),
  zona_horaria  text not null check (length(zona_horaria) between 1 and 100)
);
revoke all on plataforma.tope_gasto_ia from public, cem_app;

create table plataforma.precio_modelo_ia (
  modelo                 text primary key check (length(modelo) between 1 and 200),
  usd_por_millon_entrada numeric(10, 4) not null check (usd_por_millon_entrada >= 0),
  usd_por_millon_salida  numeric(10, 4) not null check (usd_por_millon_salida >= 0),
  fuente                 text not null check (length(fuente) between 1 and 500)
);
revoke all on plataforma.precio_modelo_ia from public, cem_app;

create table plataforma.gasto_ia (
  id              uuid primary key default plataforma.uuid_v7(),
  uso             text not null references plataforma.tope_gasto_ia (uso),
  consorcio_id    uuid not null references nucleo.consorcio (id),
  importacion_id  uuid not null unique,
  mes             date not null check (mes = date_trunc('month', mes)::date),
  reservado_en    timestamptz not null default clock_timestamp(),
  costo_maximo    numeric(12, 6) not null check (costo_maximo >= 0),
  modelo          text check (length(modelo) between 1 and 200),
  fichas_entrada  integer check (fichas_entrada >= 0),
  fichas_salida   integer check (fichas_salida >= 0),
  costo           numeric(12, 6) check (costo >= 0),
  registrado_en   timestamptz,
  foreign key (consorcio_id, importacion_id)
    references nucleo.importacion_liquidacion (consorcio_id, id),
  constraint gasto_registrado_con_costo check ((registrado_en is null) = (costo is null))
);
create index gasto_ia_por_mes on plataforma.gasto_ia (uso, mes);
revoke all on plataforma.gasto_ia from public, cem_app;

-- Solo el registro de la llamada, una vez: lo demás no cambia y nada se borra.
create function plataforma.negar_cambio_gasto_ia()
  returns trigger
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  begin
    if tg_op <> 'UPDATE' then
      raise exception 'el gasto en IA no se borra' using errcode = 'P0001',
        hint = 'historia_inmutable';
    end if;
    if old.registrado_en is not null or new.registrado_en is null
       or (new.id, new.uso, new.consorcio_id, new.importacion_id, new.mes, new.reservado_en,
           new.costo_maximo)
          is distinct from
          (old.id, old.uso, old.consorcio_id, old.importacion_id, old.mes, old.reservado_en,
           old.costo_maximo) then
      raise exception 'el gasto en IA solo se registra una vez' using errcode = 'P0001',
        hint = 'historia_inmutable';
    end if;
    return new;
  end
  $$;
revoke all on function plataforma.negar_cambio_gasto_ia() from public;
create trigger historia_gasto_ia before update or delete on plataforma.gasto_ia
  for each row execute function plataforma.negar_cambio_gasto_ia();
create trigger historia_gasto_ia_truncate before truncate on plataforma.gasto_ia
  for each statement execute function plataforma.negar_cambio_gasto_ia();
"""

FUNCIONES = """
-- Para `cem-trabajos`, antes de llamar al modelo. Devuelve la reserva, o el motivo sin reserva:
-- `sin_precio` (el modelo que se va a pedir no tiene precio: no hay cota), `ya_intentada` (la
-- importación ya tuvo su llamada) o `tope_alcanzado`. Sin tope para el uso, falla: es
-- configuración que falta, no un «no».
create function plataforma.reservar_gasto_ia(uso_pedido text, importacion uuid,
                                             modelo_pedido text, fichas_entrada_max integer,
                                             fichas_salida_max integer)
  returns table (reserva uuid, motivo text)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    t plataforma.tope_gasto_ia;
    p plataforma.precio_modelo_ia;
    maximo numeric;
    mes_actual date;
    gastado numeric;
    r uuid;
  begin
    perform nucleo.exigir_trabajo(c);
    if fichas_entrada_max is null or fichas_entrada_max < 0
       or fichas_salida_max is null or fichas_salida_max < 0 then
      raise exception 'fichas máximas inválidas' using errcode = 'P0001';
    end if;
    perform pg_advisory_xact_lock(hashtextextended('gasto_ia:' || uso_pedido, 0));
    select x.* into t from plataforma.tope_gasto_ia x where x.uso = uso_pedido;
    if t.uso is null then
      raise exception 'sin tope de gasto en IA para %', uso_pedido using errcode = 'P0001';
    end if;
    select x.* into p from plataforma.precio_modelo_ia x where x.modelo = modelo_pedido;
    if p.modelo is null then
      return query select null::uuid, 'sin_precio'::text;
      return;
    end if;
    if exists (select 1 from plataforma.gasto_ia g where g.importacion_id = importacion) then
      return query select null::uuid, 'ya_intentada'::text;
      return;
    end if;
    maximo := (fichas_entrada_max * p.usd_por_millon_entrada
               + fichas_salida_max * p.usd_por_millon_salida) / 1000000;
    mes_actual := date_trunc('month', now() at time zone t.zona_horaria)::date;
    select coalesce(sum(coalesce(g.costo, g.costo_maximo)), 0) into gastado
      from plataforma.gasto_ia g where g.uso = uso_pedido and g.mes = mes_actual;
    if gastado + maximo > t.usd_por_mes then
      return query select null::uuid, 'tope_alcanzado'::text;
      return;
    end if;
    insert into plataforma.gasto_ia (uso, consorcio_id, importacion_id, mes, costo_maximo)
    values (uso_pedido, c, importacion, mes_actual, maximo)
    returning id into r;
    return query select r, null::text;
  end
  $$;

-- Para `cem-trabajos`, después de la llamada: lo que informó el proveedor. Con el modelo en la
-- tabla de precios y las fichas informadas, el costo real; si no, el máximo reservado. Devuelve el
-- costo anotado.
create function plataforma.registrar_gasto_ia(reserva uuid, modelo_informado text,
                                              fichas_entrada_usadas integer,
                                              fichas_salida_usadas integer)
  returns numeric
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    g plataforma.gasto_ia;
    p plataforma.precio_modelo_ia;
    costo_real numeric;
  begin
    perform nucleo.exigir_trabajo(c);
    select x.* into g from plataforma.gasto_ia x
     where x.id = reserva and x.consorcio_id = c for update;
    if g.id is null then
      raise exception 'reserva inexistente' using errcode = 'P0001', hint = 'no_encontrado';
    end if;
    perform pg_advisory_xact_lock(hashtextextended('gasto_ia:' || g.uso, 0));
    select x.* into p from plataforma.precio_modelo_ia x where x.modelo = modelo_informado;
    if p.modelo is not null and fichas_entrada_usadas is not null
       and fichas_salida_usadas is not null then
      costo_real := (fichas_entrada_usadas * p.usd_por_millon_entrada
               + fichas_salida_usadas * p.usd_por_millon_salida) / 1000000;
    else
      costo_real := g.costo_maximo;
    end if;
    update plataforma.gasto_ia
       set modelo = modelo_informado, fichas_entrada = fichas_entrada_usadas,
           fichas_salida = fichas_salida_usadas, costo = costo_real,
           registrado_en = clock_timestamp()
     where id = reserva;
    return costo_real;
  end
  $$;
"""

RUTAS = (
    "plataforma.reservar_gasto_ia(text, uuid, text, integer, integer)",
    "plataforma.registrar_gasto_ia(uuid, text, integer, integer)",
)

GUARDA = """
do $guarda$
begin
  if exists (select 1 from plataforma.gasto_ia)
     or exists (select 1 from nucleo.importacion_liquidacion where formato = 'lectura_ia') then
    raise exception '0114 no baja: hay gasto en IA registrado o importaciones leídas con IA'
      using errcode = 'P0001', hint = 'downgrade_con_datos';
  end if;
end
$guarda$;
"""


def upgrade() -> None:
    op.execute(TABLAS)
    op.execute(
        "insert into plataforma.tope_gasto_ia (uso, usd_por_mes, zona_horaria)"
        f" values ('{USO}', {TOPE_USD_POR_MES}, '{ZONA_DEL_MES}')"
    )
    for modelo, entrada, salida, fuente in PRECIOS:
        op.execute(
            "insert into plataforma.precio_modelo_ia"
            " (modelo, usd_por_millon_entrada, usd_por_millon_salida, fuente)"
            f" values ('{modelo}', {entrada}, {salida}, '{fuente}')"
        )
    op.execute(FUNCIONES)
    for funcion in RUTAS:
        op.execute(f"revoke all on function {funcion} from public")
        op.execute(f"grant execute on function {funcion} to cem_app")


def downgrade() -> None:
    op.execute(GUARDA)
    for funcion in reversed(RUTAS):
        op.execute(f"drop function {funcion}")
    op.execute("drop table plataforma.gasto_ia")
    op.execute("drop function plataforma.negar_cambio_gasto_ia()")
    op.execute("drop table plataforma.precio_modelo_ia")
    op.execute("drop table plataforma.tope_gasto_ia")
    op.execute(
        "alter table nucleo.importacion_liquidacion"
        " drop constraint importacion_liquidacion_formato_check,"
        " add constraint importacion_liquidacion_formato_check"
        " check (formato in ('mis_expensas'))"
    )
