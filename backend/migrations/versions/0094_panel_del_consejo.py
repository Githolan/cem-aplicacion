"""H4a, corte 1: lecturas del panel del Consejo (RF48), en solo lectura.

Diseño: `doc/diseno/H4a_1_Panel_del_Consejo.md` del repositorio CEM.

**Qué agrega.** Tres lecturas `security definer` para `GET …/panel`, con la segunda barrera de
`0018`: un mandato vigente hoy de quien pide, con `fiscalizacion.ver_panel` general, en el
consorcio del ámbito (`exigir_ver_panel`, interna). Sin eso, 42501.

- `panel_ordenes_por_estado`: cantidad e importe de la versión vigente por estado y moneda, sin
  borradores (como la lectura de quien aprueba, `0049`).
- `panel_gasto_pagado`: lo pagado por mes, rubro del proveedor de la versión pagada, fondo de
  reserva y moneda. El importe y la fecha salen de `importe_pagado_ejecucion` (`0075`) y
  `fecha_pago_ejecucion` (`0059`), las mismas que usa la liquidación; las mismas órdenes que
  `ordenes_liquidables_lectura` (pagadas, la ejecución de la versión vigente con resultado
  pagada), más las conciliadas. Sin fecha todavía, no entra.
- `panel_comprobantes_faltantes`: órdenes aprobadas o más adelante cuya versión vigente no tiene
  archivo de factura (`factura_version_id`, `0088`).

**Lo que no hace.** No crea facultades ni tablas, no cambia ninguna regla y no da acceso a ninguna
tabla: `cem_app` recibe solo `EXECUTE` sobre las tres lecturas. No devuelve cuentas, CUIT,
contactos, personas ni motivos.

**Downgrade.** Quita las funciones; no hay datos propios.

Revisión: 0094
Anterior: 0093
"""

from alembic import op

revision = "0094"
down_revision = "0093"
branch_labels = None
depends_on = None

FACULTAD = "fiscalizacion.ver_panel"
LECTURAS = (
    "nucleo.panel_ordenes_por_estado()",
    "nucleo.panel_gasto_pagado()",
    "nucleo.panel_comprobantes_faltantes()",
)
INTERNAS = ("nucleo.exigir_ver_panel(uuid, uuid)",)
PAGADAS = "('pagada', 'conciliada')"
"""Los estados de una orden ya pagada (`EstadoOrdenPago`)."""
CON_DINERO_COMPROMETIDO = "('aprobada', 'en_ejecucion', 'en_verificacion', 'pagada', 'conciliada')"
"""Desde la aprobación: la orden ya puede pagarse o se pagó, y la factura tendría que estar."""

UPGRADE = f"""
-- Segunda barrera del panel. Interna: la llaman las lecturas, no `cem_app`.
create function nucleo.exigir_ver_panel(c uuid, quien uuid)
  returns void
  language plpgsql stable
  set search_path = pg_catalog, pg_temp
  as $$
  begin
    if c is null or quien is null then
      raise exception 'sin ámbito para leer el panel' using errcode = '42501';
    end if;
    if not nucleo.tiene_facultad_vigente(c, quien, '{FACULTAD}', nucleo.hoy_del_consorcio(c)) then
      raise exception 'sin {FACULTAD} en este consorcio' using errcode = '42501';
    end if;
  end
  $$;

create function nucleo.panel_ordenes_por_estado()
  returns table (estado text, moneda text, cantidad integer, importe_centavos bigint)
  language plpgsql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
  begin
    perform nucleo.exigir_ver_panel(c, plataforma.persona_actual());
    return query
      select o.estado::text, v.moneda, count(*)::integer, sum(v.importe_centavos)::bigint
        from nucleo.orden_pago o
        join nucleo.orden_pago_version v
          on v.consorcio_id = o.consorcio_id and v.orden_id = o.id
         and v.numero = o.version_vigente
       where o.consorcio_id = c and o.estado <> 'borrador'
       group by o.estado, v.moneda
       order by o.estado, v.moneda;
  end
  $$;

create function nucleo.panel_gasto_pagado()
  returns table (periodo date, rubro text, fondo_reserva boolean, moneda text, cantidad integer,
                 importe_centavos bigint)
  language plpgsql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
  begin
    perform nucleo.exigir_ver_panel(c, plataforma.persona_actual());
    return query
      select date_trunc('month', p.pagada_el)::date, p.rubro, p.fondo_reserva, p.moneda,
             count(*)::integer, sum(p.importe)::bigint
        from (select nucleo.fecha_pago_ejecucion(c, e.id) as pagada_el,
                     nullif(btrim(pv.rubro), '') as rubro, v.usa_fondo_reserva as fondo_reserva,
                     e.moneda, nucleo.importe_pagado_ejecucion(c, e.id) as importe
                from nucleo.orden_pago o
                join nucleo.ejecucion_orden_pago e
                  on e.consorcio_id = c and e.orden_id = o.id and e.version = o.version_vigente
                join nucleo.resultado_ejecucion_orden_pago r
                  on r.ejecucion_id = e.id and r.resultado = 'pagada'
                join nucleo.orden_pago_version v on v.orden_id = e.orden_id and v.numero = e.version
                join nucleo.proveedor_version pv
                  on pv.proveedor_id = e.proveedor_id and pv.numero = e.proveedor_version
               where o.consorcio_id = c and o.estado in {PAGADAS}) p
       where p.pagada_el is not null
       group by 1, 2, 3, 4
       order by 1, 2 nulls last, 3, 4;
  end
  $$;

create function nucleo.panel_comprobantes_faltantes()
  returns table (orden_id uuid, estado text, proveedor text, concepto text,
                 importe_centavos bigint, moneda text, actualizada_en timestamptz)
  language plpgsql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
  begin
    perform nucleo.exigir_ver_panel(c, plataforma.persona_actual());
    return query
      select o.id, o.estado::text, pv.razon_social, v.concepto, v.importe_centavos, v.moneda,
             o.actualizada_en
        from nucleo.orden_pago o
        join nucleo.orden_pago_version v
          on v.consorcio_id = o.consorcio_id and v.orden_id = o.id
         and v.numero = o.version_vigente
        join nucleo.proveedor_version pv
          on pv.consorcio_id = v.consorcio_id and pv.proveedor_id = v.proveedor_id
         and pv.numero = v.proveedor_version
       where o.consorcio_id = c and o.estado in {CON_DINERO_COMPROMETIDO}
         and v.factura_version_id is null
       order by o.actualizada_en desc, o.id;
  end
  $$;
"""


def _privilegios() -> str:
    lineas = [f"revoke all on function {f} from public;" for f in LECTURAS + INTERNAS]
    lineas += [f"grant execute on function {f} to cem_app;" for f in LECTURAS]
    return "\n".join(lineas) + "\n"


def upgrade() -> None:
    op.execute(UPGRADE)
    op.execute(_privilegios())


def downgrade() -> None:
    op.execute("\n".join(f"drop function {f};" for f in LECTURAS + INTERNAS))
