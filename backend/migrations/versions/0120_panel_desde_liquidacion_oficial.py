"""El panel del Consejo y el resumen del propietario leídos de las liquidaciones oficiales (Modo
Consejo; diseño P0 S3, H4a §4 y §5, plan §6.7 «Gráficos»).

En Modo Consejo no hay órdenes de pago: lo que el edificio gastó lo dice la liquidación de la
administración que el Consejo confirmó (la oficial de cada período, `0119`). Estas lecturas leen
solo los datos oficiales de la importación `confirmada` de cada período (confirmados y corregidos,
RN30); una para confirmar, descartada o reemplazada nunca cuenta.

**El panel completo** (`fiscalizacion.ver_panel`, barrera `exigir_ver_panel` de `0094`):

- `panel_gasto_liquidado_por_periodo(cuantos)`: de cada uno de los últimos `cuantos` períodos con
  liquidación oficial, el total del gasto y los totales por grupo que informa (`total:gastos`).
- `panel_gasto_liquidado_por_rubro(cuantos)`: en la misma ventana, el número, el nombre y el total
  de cada rubro.
- `panel_diferencias_liquidacion_vigente()`: de la liquidación oficial del último período que la
  tiene, cuántas diferencias dejó la lectura (lo que no cierra entre lo informado y lo que suma,
  en el documento tal como llegó, antes de las correcciones; RN33: la diferencia, sin
  calificarla).

**El resumen del propietario** (`fiscalizacion.ver_resumen`, barrera
`exigir_propietario_confirmado` de `0119`): `resumen_gasto_por_periodo(cuantos)`, el total del
gasto de cada período en la misma ventana. Sin gastos, diferencias ni unidades.

La ventana (`cuantos`) la pasa la API desde el contrato (`PERIODOS_DE_GASTO`): son los últimos
períodos que tienen liquidación oficial, del más viejo al más nuevo. El fondo de reserva no está:
la lectura todavía no extrae el estado financiero.

**Lo que no hace.** No crea tablas, facultades ni reglas. `cem_app` recibe solo `EXECUTE` sobre las
cuatro lecturas; la ventana (`ultimas_liquidaciones_oficiales`) es interna.

**Downgrade.** Quita las funciones; no hay datos propios.

Datos de prueba: SINTETICOS / NO REAL.

Revisión: 0120
Anterior: 0119
"""

from alembic import op

revision = "0120"
down_revision = "0119"
branch_labels = None
depends_on = None

CLAVE_TOTAL = "total:gastos"
"""El dato con los totales del gasto (`importacion.campos`)."""

FUNCIONES = f"""
-- La ventana: las liquidaciones oficiales vigentes de los últimos `cuantos` períodos que la tienen.
-- Interna: la usan las lecturas de abajo.
create function nucleo.ultimas_liquidaciones_oficiales(c uuid, cuantos integer)
  returns table (id uuid, periodo date, decidida_en timestamptz)
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select x.id, x.periodo, x.decidida_en from nucleo.importacion_liquidacion x
     where x.consorcio_id = c and x.estado = 'confirmada'
     order by x.periodo desc
     limit cuantos
  $$;

-- El panel: el gasto de cada período según su liquidación oficial, del más viejo al más nuevo. El
-- total y los grupos vacíos si la liquidación no tiene ese dato oficial.
create function nucleo.panel_gasto_liquidado_por_periodo(cuantos integer)
  returns table (periodo date, confirmada_en timestamptz, total numeric(18, 2), grupos jsonb)
  language plpgsql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
  begin
    perform nucleo.exigir_ver_panel(c, plataforma.persona_actual());
    return query
      select u.periodo, u.decidida_en, (o.valor->>'total')::numeric(18, 2), o.valor->'grupos'
        from nucleo.ultimas_liquidaciones_oficiales(c, cuantos) u
        left join lateral (
          select f.valor from nucleo.campos_oficiales_de(c, u.id) f
           where f.clave = '{CLAVE_TOTAL}') o on true
       order by u.periodo;
  end
  $$;

-- El panel: cada rubro de cada período según su liquidación oficial, en el orden del documento.
create function nucleo.panel_gasto_liquidado_por_rubro(cuantos integer)
  returns table (periodo date, numero integer, nombre text, total numeric(18, 2))
  language plpgsql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
  begin
    perform nucleo.exigir_ver_panel(c, plataforma.persona_actual());
    return query
      select u.periodo, (f.valor->>'numero')::integer, f.valor->>'nombre',
             (f.valor->>'total')::numeric(18, 2)
        from nucleo.ultimas_liquidaciones_oficiales(c, cuantos) u
        cross join lateral nucleo.campos_oficiales_de(c, u.id) f
       where f.tipo = 'rubro'
       order by u.periodo, f.orden;
  end
  $$;

-- El panel: la liquidación oficial del último período que la tiene y cuántas diferencias dejó su
-- lectura. Sin filas si no hay ninguna.
create function nucleo.panel_diferencias_liquidacion_vigente()
  returns table (id uuid, periodo date, diferencias integer)
  language plpgsql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
  begin
    perform nucleo.exigir_ver_panel(c, plataforma.persona_actual());
    return query
      select x.id, x.periodo, jsonb_array_length(x.diferencias)
        from nucleo.importacion_liquidacion x
       where x.consorcio_id = c and x.id = nucleo.liquidacion_vigente(c, null);
  end
  $$;

-- Cada propietario: el total del gasto de cada período según su liquidación oficial, del más viejo
-- al más nuevo. Sin grupos, gastos, diferencias ni unidades.
create function nucleo.resumen_gasto_por_periodo(cuantos integer)
  returns table (periodo date, total numeric(18, 2))
  language plpgsql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
  begin
    perform nucleo.exigir_propietario_confirmado(c, plataforma.persona_actual());
    return query
      select u.periodo, (o.valor->>'total')::numeric(18, 2)
        from nucleo.ultimas_liquidaciones_oficiales(c, cuantos) u
        left join lateral (
          select f.valor from nucleo.campos_oficiales_de(c, u.id) f
           where f.clave = '{CLAVE_TOTAL}') o on true
       order by u.periodo;
  end
  $$;
"""

RUTAS = (
    "nucleo.panel_gasto_liquidado_por_periodo(integer)",
    "nucleo.panel_gasto_liquidado_por_rubro(integer)",
    "nucleo.panel_diferencias_liquidacion_vigente()",
    "nucleo.resumen_gasto_por_periodo(integer)",
)
INTERNAS = ("nucleo.ultimas_liquidaciones_oficiales(uuid, integer)",)


def upgrade() -> None:
    op.execute(FUNCIONES)
    for funcion in INTERNAS + RUTAS:
        op.execute(f"revoke all on function {funcion} from public")
    for funcion in RUTAS:
        op.execute(f"grant execute on function {funcion} to cem_app")


def downgrade() -> None:
    for funcion in reversed(INTERNAS + RUTAS):
        op.execute(f"drop function {funcion}")
