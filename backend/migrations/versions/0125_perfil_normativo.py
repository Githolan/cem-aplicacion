"""Perfil normativo mínimo (CABA) y la señal de la liquidación sin el enlace a la documentación
(RF01, RF47; diseño P0 S5; decisiones del propietario del 25-10-2026, §1.2).

**El perfil** es de una jurisdicción y tiene versiones; lo mantiene el equipo CEM con fuentes
oficiales (§1.2), por migración: cada versión registra su vigencia (`vigente_desde`), la fecha en
que se verificó la fuente y, por requisito, si lo exige, la norma y su publicación oficial.

- **`nucleo.perfil_normativo`**: una fila por versión. La versión `n + 1` de una jurisdicción
  empieza después que la `n`; su vigencia corre hasta que empieza la siguiente. Nada se cambia ni
  se borra: cambiar el perfil es publicar otra versión (`negar_cambio_perfil_normativo`).
- **`nucleo.perfil_normativo_requisito`**: lo que cada versión dice de cada requisito
  (`RequisitoNormativo`). Un requisito no se agrega a una versión que algún consorcio ya usa.
- **`nucleo.consorcio_perfil_normativo`**: el consorcio queda vinculado a una versión; una fila
  por vínculo (`orden`), el último es el vigente. Vincular otra versión es otra fila. RLS forzada,
  sin acceso directo de `cem_app`. Lo vincula la plataforma, sin persona: `scripts.alta_consorcio`
  con `vincular_perfil_normativo` (la versión vigente hoy de la jurisdicción del archivo).

La versión 1 de CABA (`caba`): la Disposición 1146/DGDYPC/24 (documento 08, §3 y F4; documento 01,
§12) pide en cada liquidación un código QR o un hipervínculo a la documentación del período. Rige
desde el 31-7-2024 (prórroga de la disposición); fuente consultada el 11-10-2026. Los consorcios que
ya existen son de la Ciudad (piloto): quedan vinculados a esa versión.

**La búsqueda de enlaces** (`0118` los anota sin seguirlos, pero sin enlaces no dejaba rastro):
**`nucleo.busqueda_enlaces_liquidacion`** registra que la lectura buscó y si pudo buscar en todo el
documento (`completa`: texto, anotaciones y QR de cada página). La escribe `cem-trabajos` con
`registrar_busqueda_enlaces` en cada lectura, haya enlaces o no.

**La señal** (`enlace_documentacion_liquidaciones`, barrera `exigir_ver_panel`): por cada
liquidación oficial **confirmada** (la vigente de su período, `0119`) desde el comienzo de la
versión vinculada, si esa versión exige el enlace: `encontrado` (hay al menos uno; CEM no comprueba
a dónde lleva), `no_encontrado` (se buscó en todo el documento) o `sin_buscar` (no se pudo buscar
en todo, o se leyó antes de esta migración). En Modo Consejo CEM no publica: es una señal, no un
bloqueo.

**Exportación** (`0069`): la parte nueva `perfil_normativo`, con cada vínculo (la versión y sus
requisitos) y las búsquedas de enlaces.

Downgrade: se niega (`downgrade_con_datos`) si hay algún consorcio vinculado o alguna búsqueda.

Datos de prueba: SINTETICOS / NO REAL.

Revisión: 0125
Anterior: 0124
"""

import importlib.util
from pathlib import Path

from alembic import op

revision = "0125"
down_revision = "0124"
branch_labels = None
depends_on = None

JURISDICCIONES = ("caba",)
REQUISITOS = ("liquidacion_enlace_documentacion",)
ESTADOS = ("encontrado", "no_encontrado", "sin_buscar")
LARGO_NORMA = 200
"""Los de `cem_contracts.perfil_normativo`; una prueba compara."""
ENLACE = "liquidacion_enlace_documentacion"
CABA_1 = {
    "jurisdiccion": "caba",
    "vigente_desde": "2024-07-31",
    "verificado_el": "2026-10-11",
    "norma": "Disposición 1146/DGDYPC/24",
    "fuente": "https://boletinoficial.buenosaires.gob.ar/normativaba/norma/714586",
}
"""La versión 1 de CABA: documento 01, §12 (fecha y prórroga), y documento 08, F4 (fuente y
consulta)."""
EXPORTAR = "exportar_perfil_normativo"


def _lista(valores) -> str:
    return ", ".join(f"'{v}'" for v in valores)


TABLAS = f"""
create function nucleo.negar_cambio_perfil_normativo()
  returns trigger
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  begin
    raise exception 'el perfil normativo no se cambia ni se borra: se publica otra versión'
      using errcode = 'P0001', hint = 'historia_inmutable';
  end
  $$;
revoke all on function nucleo.negar_cambio_perfil_normativo() from public;

create table nucleo.perfil_normativo (
  id             uuid primary key default plataforma.uuid_v7(),
  jurisdiccion   text not null check (jurisdiccion in ({_lista(JURISDICCIONES)})),
  version        integer not null check (version >= 1),
  vigente_desde  date not null,
  verificado_el  date not null,
  registrado_en  timestamptz not null default clock_timestamp(),
  unique (jurisdiccion, version),
  unique (jurisdiccion, vigente_desde)
);

-- La versión siguiente de la jurisdicción, y después de la última: la vigencia nunca se solapa ni
-- se reescribe hacia atrás.
create function nucleo.exigir_version_siguiente()
  returns trigger
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    ultima integer;
    desde date;
  begin
    perform pg_advisory_xact_lock(hashtext('perfil_normativo:' || new.jurisdiccion));
    select p.version, p.vigente_desde into ultima, desde from nucleo.perfil_normativo p
     where p.jurisdiccion = new.jurisdiccion order by p.version desc limit 1;
    if new.version <> coalesce(ultima, 0) + 1 or new.vigente_desde <= desde then
      raise exception 'la versión del perfil sigue a la última y empieza después'
        using errcode = 'P0001', hint = 'version_de_perfil_invalida';
    end if;
    return new;
  end
  $$;
revoke all on function nucleo.exigir_version_siguiente() from public;
create trigger perfil_version_siguiente before insert on nucleo.perfil_normativo
  for each row execute function nucleo.exigir_version_siguiente();

create table nucleo.perfil_normativo_requisito (
  perfil_id  uuid not null references nucleo.perfil_normativo (id),
  codigo     text not null check (codigo in ({_lista(REQUISITOS)})),
  exige      boolean not null,
  norma      text not null check (length(norma) between 1 and {LARGO_NORMA}
                                  and norma = btrim(norma)),
  fuente     text not null check (fuente ~ '^https://[^[:space:]]+$'),
  primary key (perfil_id, codigo)
);

create table nucleo.consorcio_perfil_normativo (
  consorcio_id  uuid not null references nucleo.consorcio (id),
  orden         integer not null check (orden >= 1),
  perfil_id     uuid not null references nucleo.perfil_normativo (id),
  vinculado_en  timestamptz not null default clock_timestamp(),
  primary key (consorcio_id, orden)
);

-- Lo que una versión exige queda fijo desde que un consorcio la usa.
create function nucleo.negar_requisito_de_perfil_usado()
  returns trigger
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  begin
    if exists (select 1 from nucleo.consorcio_perfil_normativo b
                where b.perfil_id = new.perfil_id) then
      raise exception 'esa versión del perfil ya la usa un consorcio: se publica otra'
        using errcode = 'P0001', hint = 'historia_inmutable';
    end if;
    return new;
  end
  $$;
revoke all on function nucleo.negar_requisito_de_perfil_usado() from public;
create trigger requisito_de_perfil_sin_usar before insert on nucleo.perfil_normativo_requisito
  for each row execute function nucleo.negar_requisito_de_perfil_usado();

create table nucleo.busqueda_enlaces_liquidacion (
  consorcio_id    uuid not null,
  importacion_id  uuid not null,
  completa        boolean not null,
  buscada_en      timestamptz not null default clock_timestamp(),
  primary key (consorcio_id, importacion_id),
  foreign key (consorcio_id, importacion_id)
    references nucleo.importacion_liquidacion (consorcio_id, id)
);
"""


def _inmutable(tabla: str) -> str:
    corto = tabla.removeprefix("nucleo.")
    return f"""
create trigger historia_{corto} before update or delete on {tabla}
  for each row execute function nucleo.negar_cambio_perfil_normativo();
create trigger historia_{corto}_truncate before truncate on {tabla}
  for each statement execute function nucleo.negar_cambio_perfil_normativo();
"""


def _por_consorcio(tabla: str) -> str:
    return f"""
alter table {tabla} enable row level security;
alter table {tabla} force row level security;
create policy por_consorcio on {tabla} for select
  using (consorcio_id = plataforma.consorcio_actual());
revoke all on {tabla} from public, cem_app;
"""


GLOBALES = ("nucleo.perfil_normativo", "nucleo.perfil_normativo_requisito")
"""Sin `consorcio_id`: la norma es de la jurisdicción. `cem_app` no las lee directo."""
DEL_CONSORCIO = ("nucleo.consorcio_perfil_normativo", "nucleo.busqueda_enlaces_liquidacion")
TABLAS_NUEVAS = GLOBALES + DEL_CONSORCIO

FUNCIONES = f"""
-- Lo opera la plataforma sin persona (`scripts.alta_consorcio`), con el consorcio en el ámbito.
create function nucleo.exigir_plataforma(c uuid)
  returns void
  language plpgsql stable
  set search_path = pg_catalog, pg_temp
  as $$
  begin
    if c is null or plataforma.persona_actual() is not null then
      raise exception 'el perfil normativo lo vincula la plataforma' using errcode = '42501';
    end if;
  end
  $$;

-- La versión vigente hoy de la jurisdicción. Si ya es la vinculada, no escribe nada.
create function nucleo.vincular_perfil_normativo(jurisdiccion_pedida text)
  returns table (perfil_id uuid, version integer, vinculado boolean)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    nuevo uuid;
    numero integer;
    actual uuid;
    ultimo integer;
  begin
    perform nucleo.exigir_plataforma(c);
    perform 1 from nucleo.consorcio x where x.id = c for no key update;
    if not found then
      raise exception 'consorcio inexistente' using errcode = 'P0001', hint = 'no_encontrado';
    end if;
    select p.id, p.version into nuevo, numero from nucleo.perfil_normativo p
     where p.jurisdiccion = jurisdiccion_pedida
       and p.vigente_desde <= nucleo.hoy_del_consorcio(c)
     order by p.version desc limit 1;
    if nuevo is null then
      raise exception 'la jurisdicción no tiene un perfil normativo vigente'
        using errcode = 'P0001', hint = 'sin_perfil_vigente';
    end if;
    select b.perfil_id, b.orden into actual, ultimo from nucleo.consorcio_perfil_normativo b
     where b.consorcio_id = c order by b.orden desc limit 1;
    if actual = nuevo then
      return query select nuevo, numero, false;
      return;
    end if;
    insert into nucleo.consorcio_perfil_normativo (consorcio_id, orden, perfil_id)
    values (c, coalesce(ultimo, 0) + 1, nuevo);
    return query select nuevo, numero, true;
  end
  $$;

-- El perfil vinculado al consorcio del ámbito, para la plataforma. Sin filas si no tiene.
create function nucleo.perfil_normativo_del_consorcio()
  returns table (jurisdiccion text, version integer, vinculado_en timestamptz)
  language plpgsql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
  begin
    perform nucleo.exigir_plataforma(c);
    return query
      select p.jurisdiccion, p.version, b.vinculado_en
        from nucleo.consorcio_perfil_normativo b
        join nucleo.perfil_normativo p on p.id = b.perfil_id
       where b.consorcio_id = c
       order by b.orden desc limit 1;
  end
  $$;

-- Para `cem-trabajos`, en cada lectura: que se buscaron los enlaces y si fue en todo el
-- documento. Con `no key update` por la misma razón que `guardar_enlaces_liquidacion` (`0118`).
create function nucleo.registrar_busqueda_enlaces(importacion uuid, en_todo boolean)
  returns void
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    actual text;
  begin
    perform nucleo.exigir_trabajo(c);
    select x.estado into actual from nucleo.importacion_liquidacion x
     where x.consorcio_id = c and x.id = importacion for no key update;
    if actual is null then
      raise exception 'importación inexistente' using errcode = 'P0001', hint = 'no_encontrado';
    end if;
    if actual <> 'leyendo' then
      return;
    end if;
    insert into nucleo.busqueda_enlaces_liquidacion (consorcio_id, importacion_id, completa)
    values (c, importacion, en_todo)
    on conflict (consorcio_id, importacion_id) do nothing;
  end
  $$;

-- La señal de RF47 (Modo Consejo): las oficiales confirmadas desde el comienzo de la versión
-- vinculada, si esa versión exige el enlace. Quien ve el panel (`0094`).
create function nucleo.enlace_documentacion_liquidaciones()
  returns table (importacion_id uuid, estado text, jurisdiccion text, version integer,
                 norma text, fuente text)
  language plpgsql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
  begin
    perform nucleo.exigir_ver_panel(c, plataforma.persona_actual());
    return query
      with vinculado as (
        select b.perfil_id from nucleo.consorcio_perfil_normativo b
         where b.consorcio_id = c order by b.orden desc limit 1
      )
      select x.id,
             case
               when exists (select 1 from nucleo.enlace_liquidacion e
                             where e.consorcio_id = c and e.importacion_id = x.id)
                 then 'encontrado'
               when exists (select 1 from nucleo.busqueda_enlaces_liquidacion s
                             where s.consorcio_id = c and s.importacion_id = x.id
                               and s.completa)
                 then 'no_encontrado'
               else 'sin_buscar'
             end,
             p.jurisdiccion, p.version, r.norma, r.fuente
        from vinculado v
        join nucleo.perfil_normativo p on p.id = v.perfil_id
        join nucleo.perfil_normativo_requisito r
          on r.perfil_id = p.id and r.codigo = '{ENLACE}' and r.exige
        join nucleo.importacion_liquidacion x
          on x.consorcio_id = c and x.estado = 'confirmada' and x.periodo >= p.vigente_desde
       order by x.periodo desc;
  end
  $$;
"""

RUTAS = (
    "nucleo.vincular_perfil_normativo(text)",
    "nucleo.perfil_normativo_del_consorcio()",
    "nucleo.registrar_busqueda_enlaces(uuid, boolean)",
    "nucleo.enlace_documentacion_liquidaciones()",
)
INTERNAS = ("nucleo.exigir_plataforma(uuid)",)


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
      select 'perfil_normativo'::text,
             jsonb_build_object(
               'orden', b.orden, 'jurisdiccion', p.jurisdiccion, 'version', p.version,
               'vigente_desde', p.vigente_desde, 'verificado_el', p.verificado_el,
               'vinculado_en', b.vinculado_en,
               'requisitos', coalesce((
                 select jsonb_agg(jsonb_build_object('codigo', r.codigo, 'exige', r.exige,
                                                     'norma', r.norma, 'fuente', r.fuente)
                                  order by r.codigo)
                   from nucleo.perfil_normativo_requisito r where r.perfil_id = p.id),
                 '[]'::jsonb))
        from nucleo.consorcio_perfil_normativo b
        join nucleo.perfil_normativo p on p.id = b.perfil_id
       where b.consorcio_id = c
       order by b.orden;
    return query
      select 'busqueda_enlaces_liquidacion'::text,
             jsonb_build_object('importacion_id', s.importacion_id, 'completa', s.completa,
                                'buscada_en', s.buscada_en)
        from nucleo.busqueda_enlaces_liquidacion s
       where s.consorcio_id = c
       order by s.buscada_en, s.importacion_id;
"""

GUARDA = """
do $guarda$
begin
  if exists (select 1 from nucleo.consorcio_perfil_normativo)
     or exists (select 1 from nucleo.busqueda_enlaces_liquidacion) then
    raise exception '0125 no baja: hay consorcios con perfil normativo o búsquedas de enlaces'
      using errcode = 'P0001', hint = 'downgrade_con_datos';
  end if;
end
$guarda$;
"""


def _caba_1() -> str:
    v = CABA_1
    perfil = (
        "(select id from nucleo.perfil_normativo"
        f" where jurisdiccion = '{v['jurisdiccion']}' and version = 1)"
    )
    return f"""
insert into nucleo.perfil_normativo (jurisdiccion, version, vigente_desde, verificado_el)
values ('{v["jurisdiccion"]}', 1, '{v["vigente_desde"]}', '{v["verificado_el"]}');
insert into nucleo.perfil_normativo_requisito (perfil_id, codigo, exige, norma, fuente)
values ({perfil}, '{ENLACE}', true, '{v["norma"]}', '{v["fuente"]}');
insert into nucleo.consorcio_perfil_normativo (consorcio_id, orden, perfil_id)
select x.id, 1, {perfil} from nucleo.consorcio x;
"""


def upgrade() -> None:
    op.execute(TABLAS)
    for tabla in DEL_CONSORCIO:
        op.execute(_por_consorcio(tabla))
    for tabla in GLOBALES:
        op.execute(f"revoke all on {tabla} from public, cem_app")
    for tabla in TABLAS_NUEVAS:
        op.execute(_inmutable(tabla))
    op.execute(FUNCIONES)
    for funcion in INTERNAS + RUTAS:
        op.execute(f"revoke all on function {funcion} from public")
    for funcion in RUTAS:
        op.execute(f"grant execute on function {funcion} to cem_app")
    op.execute(_de_0069()._funcion(EXPORTAR, CUERPO_EXPORTAR))
    op.execute(_caba_1())


def downgrade() -> None:
    op.execute(GUARDA)
    op.execute(f"drop function nucleo.{EXPORTAR}(uuid, uuid)")
    for funcion in reversed(INTERNAS + RUTAS):
        op.execute(f"drop function {funcion}")
    for tabla in reversed(TABLAS_NUEVAS):
        op.execute(f"drop table {tabla}")
    op.execute("drop function nucleo.negar_requisito_de_perfil_usado()")
    op.execute("drop function nucleo.exigir_version_siguiente()")
    op.execute("drop function nucleo.negar_cambio_perfil_normativo()")
