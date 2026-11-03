"""La evidencia y la conciliación se miden por ejecución, no por orden (revisión final de H3).

La revisión transversal final de H3 (03-11-2026) encontró un P2: `0063`/`0064` dan por «vio la
cuenta» haberla visto en **cualquier** ejecución manual de la orden, y `exigir_no_ejecuto` (`0055`)
mira solo la ejecución que se concilia. Quien vio la cuenta en una ejecución anterior (declarada
«no se pagó» y vuelta a emitir con otra versión) podía subir una versión al comprobante de la
ejecución siguiente; el registro toma la última versión que pasó el antivirus, sin mirar quién la
subió, y quien registra no la ve (`0061`/`0062`). Después podía conciliar ese pago. Toca la
integridad de la evidencia y la separación de quien ejecuta y quien concilia: se reclasificó y se
corrige. Cierra también P3 164 y 167.

- **`nucleo.abrio_la_cuenta_de_la_orden`** cuenta solo la ejecución manual **abierta**: la de la
  versión vigente, con la orden en ejecución o en verificación y sin resultado definitivo (la misma
  regla que `nucleo.ejecucion_manual_abierta`). Con eso `crear_comprobante_de_pago` y
  `nueva_version` (para un comprobante) quedan para quien vio la cuenta de esa ejecución; con la
  orden pagada o fallida, nadie cambia la evidencia.
- **`nucleo.exigir_no_ejecuto`** mira todas las ejecuciones de la orden de la que se concilia o se
  confirma: quien vio la cuenta o registró un pago en cualquiera de ellas no concilia ni confirma
  que no se pagó.
- **El número de operación no lleva una cuenta adentro** (`nucleo.contiene_cbu`): se niega si
  cualquier tramo de 22 dígitos seguidos es una cuenta válida, también con texto pegado
  («CBU0170…») o con más dígitos alrededor. Antes se negaba solo el número que era exactamente una
  cuenta. Lo lee quien prepara (`pagos_manuales_lectura`, la referencia y la auditoría).

Downgrade: vuelve a las definiciones de `0063` y `0055` y quita `nucleo.contiene_cbu`. No hay
datos que dependan de esta.

Revisión: 0065
Anterior: 0064
"""

from alembic import op
from sqlalchemy import text

revision = "0065"
down_revision = "0064"
branch_labels = None
depends_on = None

ABRIO = "nucleo.abrio_la_cuenta_de_la_orden"
REGISTRO = (
    "nucleo.registrar_pago_manual(uuid, integer, bytea, text, date, bigint, text, text, uuid,"
    " uuid, text, uuid)"
)

ABRIO_0063 = f"""
create or replace function {ABRIO}(c uuid, orden uuid, quien uuid)
  returns boolean
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select exists (select 1 from nucleo.ejecucion_orden_pago e
                     join nucleo.apertura_cuenta_pago_manual a
                       on a.consorcio_id = e.consorcio_id and a.ejecucion_id = e.id
                    where e.consorcio_id = c and e.orden_id = orden and e.modalidad = 'manual'
                      and a.identidad_abrio = nucleo.identidad_humana_de(c, quien))
  $$;
"""
ABRIO_ABIERTA = f"""
-- Si quien pide recibió la cuenta en el acto de la ejecución manual abierta de la orden (la de la
-- versión vigente, sin resultado definitivo), por su identidad humana. Sin identidad, no (0065).
create or replace function {ABRIO}(c uuid, orden uuid, quien uuid)
  returns boolean
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select exists (select 1 from nucleo.orden_pago o
                     join nucleo.ejecucion_orden_pago e
                       on e.consorcio_id = o.consorcio_id and e.orden_id = o.id
                      and e.version = o.version_vigente
                     join nucleo.apertura_cuenta_pago_manual a
                       on a.consorcio_id = e.consorcio_id and a.ejecucion_id = e.id
                    where o.consorcio_id = c and o.id = orden
                      and o.estado in ('en_ejecucion', 'en_verificacion')
                      and e.modalidad = 'manual'
                      and not exists (select 1 from nucleo.resultado_ejecucion_orden_pago r
                                       where r.ejecucion_id = e.id and r.resultado <> 'incierta')
                      and a.identidad_abrio = nucleo.identidad_humana_de(c, quien))
  $$;
"""

NO_EJECUTO_0055 = """
create or replace function nucleo.exigir_no_ejecuto(c uuid, e uuid, actor uuid, identidad uuid)
  returns void
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    p uuid;
  begin
    if exists (select 1 from nucleo.apertura_cuenta_pago_manual a
                where a.ejecucion_id = e and a.identidad_abrio = identidad)
       or exists (select 1 from nucleo.pago_manual x
                   where x.ejecucion_id = e and x.identidad_registro = identidad) then
      raise exception 'la misma persona no puede ocupar los dos lugares de esta separación'
        using errcode = 'P0001', hint = 'no_es_quien_ejecuto';
    end if;
    for p in select a.abierta_por from nucleo.apertura_cuenta_pago_manual a
              where a.ejecucion_id = e
             union
             select x.registrado_por from nucleo.pago_manual x where x.ejecucion_id = e loop
      perform nucleo.exigir_separacion_humana(c, actor, p, 'no_es_quien_ejecuto');
    end loop;
  end
  $$;
"""
NO_EJECUTO_EN_LA_ORDEN = """
-- Quien concilia o confirma no recibió la cuenta ni registró un pago en ninguna ejecución de la
-- orden (0065): no solo en la que se concilia.
create or replace function nucleo.exigir_no_ejecuto(c uuid, e uuid, actor uuid, identidad uuid)
  returns void
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    p uuid;
    de_la_orden uuid[] := array(
      select x.id from nucleo.ejecucion_orden_pago x
       where x.consorcio_id = c
         and x.orden_id = (select y.orden_id from nucleo.ejecucion_orden_pago y
                            where y.consorcio_id = c and y.id = e));
  begin
    if exists (select 1 from nucleo.apertura_cuenta_pago_manual a
                where a.ejecucion_id = any(de_la_orden) and a.identidad_abrio = identidad)
       or exists (select 1 from nucleo.pago_manual x
                   where x.ejecucion_id = any(de_la_orden) and x.identidad_registro = identidad)
    then
      raise exception 'la misma persona no puede ocupar los dos lugares de esta separación'
        using errcode = 'P0001', hint = 'no_es_quien_ejecuto';
    end if;
    for p in select a.abierta_por from nucleo.apertura_cuenta_pago_manual a
              where a.ejecucion_id = any(de_la_orden)
             union
             select x.registrado_por from nucleo.pago_manual x
              where x.ejecucion_id = any(de_la_orden) loop
      perform nucleo.exigir_separacion_humana(c, actor, p, 'no_es_quien_ejecuto');
    end loop;
  end
  $$;
"""

CONTIENE_CBU = """
-- Si algún tramo de 22 dígitos seguidos es una cuenta válida (0065).
create function nucleo.contiene_cbu(t text) returns boolean
  language sql immutable
  set search_path = pg_catalog, pg_temp
  as $$
    select exists (select 1 from regexp_matches(coalesce(t, ''), '[0-9]{22,}', 'g') as m(r),
                                 generate_series(1, length(m.r[1]) - 21) as i
                    where nucleo.cbu_valido(substr(m.r[1], i, 22)))
  $$;
revoke all on function nucleo.contiene_cbu(text) from public;
"""
NUMERO_0055 = "    if n !~ '^[A-Z0-9]{4,40}$' or nucleo.cbu_valido(n) then\n"
NUMERO_SIN_CUENTA = "    if n !~ '^[A-Z0-9]{4,40}$' or nucleo.contiene_cbu(n) then\n"


def _reemplazar(funcion: str, antes: str, despues: str) -> None:
    definicion = (
        op.get_bind()
        .execute(text("select pg_get_functiondef(cast(:f as regprocedure))"), {"f": funcion})
        .scalar_one()
    )
    if definicion.count(antes) != 1:
        raise RuntimeError(f"0065: {funcion} no tiene exactamente una vez {antes!r}")
    op.execute(definicion.replace(antes, despues))


def upgrade() -> None:
    op.execute(ABRIO_ABIERTA)
    op.execute(NO_EJECUTO_EN_LA_ORDEN)
    op.execute(CONTIENE_CBU)
    _reemplazar(REGISTRO, NUMERO_0055, NUMERO_SIN_CUENTA)


def downgrade() -> None:
    _reemplazar(REGISTRO, NUMERO_SIN_CUENTA, NUMERO_0055)
    op.execute("drop function nucleo.contiene_cbu(text)")
    op.execute(NO_EJECUTO_0055)
    op.execute(ABRIO_0063)
