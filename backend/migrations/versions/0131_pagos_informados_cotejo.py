"""Pagos informados, tercer corte: el cotejo con la liquidación oficial (PI-S3 del diseño «Pagos
informados» del repositorio CEM, `doc/diseno/Pagos_Informados.md`, reglas PI-7 y PI-8), con su
número en la bandeja y el aviso diario del Consejo.

**Se calcula al leer, nunca se guarda** (PI-7; corrección de Codex en #223: un resultado guardado
queda viejo si después se confirma o se anula un pago, o si la liquidación se reemplaza). Este
corte no crea tablas.

**La regla** (`cotejo_de`, interna). Para una liquidación oficial (la confirmada de su período,
`0119`) de período P (`importacion_liquidacion.periodo`, el primer día del mes, `0113`) y cada
unidad:

- `informado`: la suma del importe de los pagos informados de la unidad en estado `confirmado`
  con la fecha del pago dentro de `[P − MESES_ANTES meses, P)`. Decisión del propietario del
  23-11-2026 (Q2): la columna «Pagos» de la liquidación de un mes suma lo pagado **el mes
  anterior** (la de noviembre, lo de octubre). **SIN VERIFICAR** contra una liquidación real que
  `periodo` guarde el mes con que se nombra la liquidación: se comprueba con la primera real. Un
  pago `informado` (sin decidir), `rechazado` o `anulado` no cuenta; como solo se corrige un
  rechazado o un anulado (`0130`), de una cadena de correcciones cuenta a lo sumo la última.
- `confirmados`: cuántos pagos suma `informado` (para que la pantalla diga «sin pagos informados»
  cuando la liquidación trae pagos y nadie informó ninguno, PI-8).
- `liquidacion`: el valor `pagos` del dato oficial `saldo_unidad` de la unidad (por su unidad
  oficial, `campos_oficiales_de`). Si la unidad no tiene ese dato, si tiene más de uno (no se sabe
  cuál vale) o si `pagos` falta o no es un importe (`PATRON_IMPORTE`), es nulo.
- `resultado`: `sin_datos` sin `liquidacion`; `coincide` si es igual a `informado`; si no,
  `difiere`. Una unidad sin pagos confirmados y con `pagos` = 0 coincide.

Las unidades son las del consorcio sin borrar, más las que tienen el dato o pagos que cuentan.

**La lectura** (`cotejo_pagos_informados_lectura`, ruta): la liquidación vigente del período
pedido (sin período, la del último que la tiene, `liquidacion_vigente`), con la ventana de los
pagos (`pagos_desde` y `pagos_hasta`, ambos incluidos) y una fila por unidad. La barrera es la de
leer pagos informados (`exigir_lectura_pagos_informados`, `0129`): quien tiene
`fiscalizacion.ver_panel` ve todas las unidades; quien tiene el vínculo confirmado de
**propietario**, solo las suyas (`unidades_propias_para_cotejo`). El inquilino y el ocupante pasan
la barrera (ven sus pagos informados), pero no la columna «Pagos» de la liquidación: como «Mis
expensas» (`cuenta.ver_propia`, P0-3), que es solo del propietario. Sin liquidación vigente,
ninguna fila; con liquidación pero sin unidades que ver, una fila sin unidad.

**Bandeja y aviso diario** (`pendientes_consejo_para_avisar`, reemplazada otra vez, con las
columnas de `0130`): suma `diferencias_cotejo`, cuántas unidades `difiere` **con al menos un pago
confirmado** hay en la liquidación vigente más nueva del edificio, para quien tiene
`fiscalizacion.ver_panel` en un modo de la acción `pagos_informados.ver`. Solo el número: ni
unidad, ni importe, ni persona. Una unidad sin pagos confirmados cuya liquidación trae pagos difiere
en el cotejo, pero no se avisa (decisión por defecto de PI-S3, para revisión del propietario):
mientras los vecinos no informan, casi todas las unidades estarían así y el aviso diario saldría
todos los días sin nada que revisar.

Downgrade: vuelven las de `0130`; sin datos que perder.

Datos de prueba: SINTETICOS / NO REAL.

Revisión: 0131
Anterior: 0130
"""

import importlib.util
from pathlib import Path

from alembic import op

revision = "0131"
down_revision = "0130"
branch_labels = None
depends_on = None

MESES_ANTES = 1
"""`cem_contracts.pagos_informados.MESES_ANTES_COLUMNA_PAGOS`; una prueba compara. SIN VERIFICAR
contra una liquidación real."""
RESULTADOS = ("coincide", "difiere", "sin_datos")
"""`cem_contracts.pagos_informados.ResultadoCotejoLiquidacion`; una prueba compara."""
ESTADO_QUE_CUENTA = "confirmado"
TIPO_SALDO = "saldo_unidad"
"""El dato oficial con la columna «Pagos» de cada unidad (`TipoCampo.SALDO_UNIDAD`)."""
CLAVE_PAGOS = "pagos"
PATRON_IMPORTE = r"^-?[0-9]{1,16}(\.[0-9]{1,2})?$"
"""Lo que entra en `NUMERIC(18,2)` sin redondear; la importación lo escribe con dos decimales."""
ACCION_LECTURA = "pagos_informados.ver"
FACULTAD_PANEL = "fiscalizacion.ver_panel"
MODOS = ("consejo", "administrador_aumentado", "autogestion")
"""Los de la política `pagos_informados.ver`; una prueba compara."""
PENDIENTES = "nucleo.pendientes_consejo_para_avisar()"
RUTAS = ("nucleo.cotejo_pagos_informados_lectura(date, uuid, text, uuid)",)
INTERNAS = ("nucleo.cotejo_de(uuid, uuid)", "nucleo.unidades_propias_para_cotejo(uuid, uuid)")
"""En el orden en que se pueden borrar: cada una antes de las que llama."""


def _modulo(nombre: str):
    """Una migración anterior, ya aplicada (no cambia), para partir de sus definiciones."""
    ruta = Path(__file__).with_name(nombre)
    spec = importlib.util.spec_from_file_location(ruta.stem, ruta)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


FUNCIONES = f"""
-- PI-7: el cotejo de una liquidación oficial, unidad por unidad, calculado en el momento. Interna:
-- la usan la lectura y la bandeja. Ninguna fila si la importación no es oficial.
create function nucleo.cotejo_de(c uuid, importacion uuid)
  returns table (unidad_id uuid, informado numeric(18, 2), confirmados integer,
                 liquidacion numeric(18, 2), resultado text)
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    with x as (
      select i.periodo,
             (i.periodo - interval '{MESES_ANTES} month')::date as desde
        from nucleo.importacion_liquidacion i
       where i.consorcio_id = c and i.id = importacion
         and i.estado in ('confirmada', 'reemplazada')
    ), saldos as (
      select o.unidad_id, count(*) as datos, min(o.valor->>'{CLAVE_PAGOS}') as pagos
        from nucleo.campos_oficiales_de(c, importacion) o
       where o.tipo = '{TIPO_SALDO}' and o.unidad_id is not null
       group by o.unidad_id
    ), pagos as (
      select p.unidad_id, sum(p.importe) as suma, count(*)::integer as cuantos
        from nucleo.pago_informado p
        cross join x
       where p.consorcio_id = c and p.estado = '{ESTADO_QUE_CUENTA}'
         and p.fecha_pago >= x.desde and p.fecha_pago < x.periodo
       group by p.unidad_id
    ), unidades as (
      select u.id from nucleo.unidad u where u.consorcio_id = c and u.eliminado_en is null
      union
      select s.unidad_id from saldos s
      union
      select q.unidad_id from pagos q
    ), leidas as (
      select u.id, coalesce(q.suma, 0)::numeric(18, 2) as informado,
             coalesce(q.cuantos, 0) as confirmados,
             case when s.datos = 1 and s.pagos ~ '{PATRON_IMPORTE}'
                  then s.pagos::numeric(18, 2) end as liquidacion
        from unidades u
        left join pagos q on q.unidad_id = u.id
        left join saldos s on s.unidad_id = u.id
    )
    select l.id, l.informado, l.confirmados, l.liquidacion,
           case when l.liquidacion is null then '{RESULTADOS[2]}'
                when l.liquidacion = l.informado then '{RESULTADOS[0]}'
                else '{RESULTADOS[1]}' end
      from leidas l
      cross join x
  $$;

-- La lectura del cotejo (PI-7, PI-8): la liquidación vigente del período pedido (sin período, la
-- del último que la tiene) y sus unidades que la persona puede ver (la barrera de leer pagos
-- informados, `0129`). Sin liquidación vigente, ninguna fila; sin unidades que ver, una fila sin
-- unidad.
-- Las unidades de las que la persona es propietaria confirmada y vigente: las del cotejo para quien
-- no ve el panel (P0-3: la columna «Pagos» de la liquidación es del propietario).
create function nucleo.unidades_propias_para_cotejo(c uuid, quien uuid)
  returns uuid[]
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select coalesce(array_agg(distinct v.unidad_id), '{{}}')
      from nucleo.vinculo v
     where v.consorcio_id = c and v.persona_id = quien
       and v.tipo = 'propietario'
       and v.confirmado_en is not null
       and daterange(v.vigente_desde, v.vigente_hasta) @> nucleo.hoy_del_consorcio(c)
       and v.id not in (select nucleo.vinculos_importados_pendientes())
  $$;

create function nucleo.cotejo_pagos_informados_lectura(periodo_pedido date, actor uuid,
                                                       origen_pedido text, sesion uuid)
  returns table (importacion_id uuid, periodo date, pagos_desde date, pagos_hasta date,
                 unidad_id uuid, unidad_designacion text, informado_confirmado numeric(18, 2),
                 pagos_confirmados integer, liquidacion numeric(18, 2), resultado text)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    todos boolean;
    propias uuid[];
    v uuid;
  begin
    todos := nucleo.exigir_lectura_pagos_informados(c, actor, sesion, origen_pedido);
    propias := nucleo.unidades_propias_para_cotejo(c, actor);
    v := nucleo.liquidacion_vigente(c, periodo_pedido);
    return query
      select x.id, x.periodo, (x.periodo - interval '{MESES_ANTES} month')::date,
             x.periodo - 1, k.unidad_id, k.designacion, k.informado, k.confirmados,
             k.liquidacion, k.resultado
        from nucleo.importacion_liquidacion x
        left join lateral (
          select d.unidad_id, u.designacion, d.informado, d.confirmados, d.liquidacion,
                 d.resultado
            from nucleo.cotejo_de(c, x.id) d
            join nucleo.unidad u on u.consorcio_id = c and u.id = d.unidad_id
           where todos or d.unidad_id = any(propias)) k on true
       where x.consorcio_id = c and x.id = v
       order by k.designacion, k.unidad_id;
  end
  $$;
"""


def _pendientes() -> str:
    """`pendientes_consejo_para_avisar` de `0130` con `diferencias_cotejo` al final: misma
    lectura, una columna y una facultad más."""
    sql = _modulo("0130_pagos_informados_confirmar.py")._pendientes()
    modos = ", ".join(f"'{m}'" for m in MODOS)
    cambios = (
        ("                 pagos_informados integer)",
         "                 pagos_informados integer, diferencias_cotejo integer)"),
        ("    decide boolean;\n",
         "    decide boolean;\n    ve_panel boolean;\n"),
        ("      continue when not (importa or firma or atiende or gestiona or decide);\n",
         f"      ve_panel := (x.modo in ({modos})"
         f" and nucleo.tiene_facultad_vigente(x.c, x.persona, '{FACULTAD_PANEL}', x.hoy));\n"
         "      continue when not (importa or firma or atiende or gestiona or decide\n"
         "                         or ve_panel);\n"),
        ("                     and not nucleo.informo_el_pago(x.c, q.id, x.persona)) end;\n",
         "                     and not nucleo.informo_el_pago(x.c, q.id, x.persona)) end,\n"
         "               case when ve_panel then\n"
         "                 (select count(*)::integer\n"
         "                    from nucleo.cotejo_de(x.c, nucleo.liquidacion_vigente(x.c, null)) k\n"
         f"                   where k.resultado = '{RESULTADOS[1]}'\n"
         "                     and k.confirmados > 0) end;\n"),
    )  # fmt: skip
    for antes, despues in cambios:
        if sql.count(antes) != 1:
            raise RuntimeError(f"0131: 0130 no tiene exactamente una vez {antes!r}")
        sql = sql.replace(antes, despues)
    return sql


def _reemplazar_pendientes(sql: str) -> None:
    op.execute(f"drop function {PENDIENTES}")
    op.execute(sql)
    op.execute(f"revoke all on function {PENDIENTES} from public")
    op.execute(f"grant execute on function {PENDIENTES} to cem_app")


def upgrade() -> None:
    op.execute(FUNCIONES)
    for funcion in INTERNAS + RUTAS:
        op.execute(f"revoke all on function {funcion} from public")
    for funcion in RUTAS:
        op.execute(f"grant execute on function {funcion} to cem_app")
    _reemplazar_pendientes(_pendientes())


def downgrade() -> None:
    _reemplazar_pendientes(_modulo("0130_pagos_informados_confirmar.py")._pendientes())
    for funcion in RUTAS + INTERNAS:
        op.execute(f"drop function {funcion}")
