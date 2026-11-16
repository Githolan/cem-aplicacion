"""El aviso diario también cuenta lo que el extracto no explica (comentario 29 del propietario,
09-11-2026: lo que el extracto no explica va a la bandeja de la administración como lista de cosas
para hacer, con un correo sin datos de dinero; confirmado el 16-11-2026: «si»).

- **Una sola regla para la bandeja y el aviso.** El cuerpo de
  `nucleo.movimientos_sin_cotejo_lectura` y el de `nucleo.pagos_para_explicar_lectura` (`0098`)
  pasan a las funciones internas
  `nucleo.movimientos_sin_cotejo_de` y `nucleo.pagos_para_explicar_de`, que reciben el consorcio
  en vez de leerlo de la sesión. Las funciones de la API las llaman después de sus controles
  (`exigir_lectura_extracto`), como `acciones_de_ordenes_de` en `0098`. Las internas no se
  conceden a `cem_app`. Los cuerpos se mueven tal cual están en la base (`pg_get_functiondef`).
- **`nucleo.extracto_sin_explicar_para_avisar`**: para el trabajo diario de avisos (`cem-trabajos`,
  sin persona ni consorcio en el ámbito, como `pendientes_para_avisar`). En cada edificio cuyo modo
  admite egresos y que tiene débitos sin cotejo, devuelve esos débitos, los pagos con los que se
  comparan y quiénes leen lo que el extracto no explica: quien concilia (`egresos.aprobar_orden`)
  y la administración (`egresos.preparar_orden`), con su identidad humana verificada, la regla de
  `exigir_lectura_extracto` (pregunta 8, opción c). La comparación la hace el trabajo con la misma
  función que la bandeja (`egresos.movimientos_sin_pago`): fechas e importes llegan al trabajo,
  nunca al correo, que solo lleva cuántos son. Con persona o consorcio en el ámbito, 42501.

No escribe eventos de auditoría: lee, como `pendientes_para_avisar`. No toca la separación de
funciones ni la regla de la cuenta: nada de esto devuelve una cuenta, un proveedor ni un concepto.

Downgrade: los cuerpos vuelven adentro de las funciones de la API y se borra lo demás. No hay datos
que perder.

Revisión: 0107
Anterior: 0106
"""

import re

from alembic import op
from sqlalchemy import text

revision = "0107"
down_revision = "0106"
branch_labels = None
depends_on = None

INICIO_CUERPO = "    return query\n"
FIN_CUERPO = "  end\n"
DEVUELVE = re.compile(r"RETURNS TABLE\((?P<columnas>[^)]*)\)\n")

MOVIMIENTOS = "nucleo.movimientos_sin_cotejo_lectura(uuid, text, uuid)"
PAGOS = "nucleo.pagos_para_explicar_lectura(text[], date, date, uuid, text, uuid)"
INTERNAS = (
    # (función de la API, interna, parámetros de la interna, firma de la interna, llamada)
    (
        MOVIMIENTOS,
        "nucleo.movimientos_sin_cotejo_de",
        "c uuid",
        "nucleo.movimientos_sin_cotejo_de(uuid)",
        "    return query select * from nucleo.movimientos_sin_cotejo_de(c);\n",
    ),
    (
        PAGOS,
        "nucleo.pagos_para_explicar_de",
        "c uuid, numeros text[], desde date, hasta date",
        "nucleo.pagos_para_explicar_de(uuid, text[], date, date)",
        "    return query select * from nucleo.pagos_para_explicar_de(c, numeros, desde, hasta);\n",
    ),
)
AVISO = "nucleo.extracto_sin_explicar_para_avisar()"

LECTURA_DEL_AVISO = """
-- Solo el trabajo de avisos: sin persona y sin consorcio en el ámbito. Recorre todos los
-- edificios, como `pendientes_para_avisar`. Quien lee: la regla de `exigir_lectura_extracto`.
create function nucleo.extracto_sin_explicar_para_avisar()
  returns table (consorcio_id uuid, consorcio text, lectores jsonb, movimientos jsonb,
                 pagos jsonb)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    x record;
    debitos jsonb;
    quienes jsonb;
    contra jsonb;
    desde_ date;
    hasta_ date;
    numeros_ text[];
  begin
    if plataforma.persona_actual() is not null or plataforma.consorcio_actual() is not null then
      raise exception 'solo el trabajo de avisos lee lo que el extracto no explica en todos'
        using errcode = '42501';
    end if;
    for x in
      select co.id as c, co.nombre, nucleo.hoy_del_consorcio(co.id) as hoy
        from nucleo.consorcio co
       where co.eliminado_en is null
         and exists (
           select 1 from nucleo.modo_operacion mo
            where mo.consorcio_id = co.id
              and mo.vigente_desde <= nucleo.hoy_del_consorcio(co.id)
              and (mo.vigente_hasta is null
                   or mo.vigente_hasta > nucleo.hoy_del_consorcio(co.id))
              and mo.modo in ('administrador_aumentado', 'autogestion'))
       order by co.id
    loop
      select jsonb_agg(jsonb_build_object('movimiento_id', s.movimiento_id, 'fecha', s.fecha,
                                          'importe_centavos', s.importe_centavos,
                                          'moneda', s.moneda,
                                          'numero_operacion', s.numero_operacion)
                       order by s.fecha, s.movimiento_id),
             min(s.fecha), max(s.fecha),
             coalesce(array_agg(distinct s.numero_operacion)
                        filter (where s.numero_operacion is not null), '{}')
        into debitos, desde_, hasta_, numeros_
        from nucleo.movimientos_sin_cotejo_de(x.c) s;
      continue when debitos is null;
      select jsonb_agg(jsonb_build_object('persona_id', p.id, 'correo', p.correo)
                       order by p.id)
        into quienes
        from nucleo.persona p
       where p.eliminado_en is null
         and exists (select 1 from nucleo.mandato m
                      where m.consorcio_id = x.c and m.persona_id = p.id)
         and (nucleo.tiene_facultad_vigente(x.c, p.id, 'egresos.aprobar_orden', x.hoy)
              or nucleo.tiene_facultad_vigente(x.c, p.id, 'egresos.preparar_orden', x.hoy))
         and nucleo.identidad_humana_de(x.c, p.id) is not null;
      continue when quienes is null;
      select coalesce(jsonb_agg(to_jsonb(f) order by f.pago_id), '[]'::jsonb)
        into contra
        from nucleo.pagos_para_explicar_de(x.c, numeros_, desde_, hasta_) f;
      return query select x.c, x.nombre, quienes, debitos, contra;
    end loop;
  end
  $$;
"""


def _definicion(funcion: str) -> str:
    return (
        op.get_bind()
        .execute(text("select pg_get_functiondef(cast(:f as regprocedure))"), {"f": funcion})
        .scalar_one()
    )


def _una_vez(definicion: str, parte: str, funcion: str) -> None:
    if definicion.count(parte) != 1:
        raise RuntimeError(f"0107: {funcion} no tiene exactamente una vez {parte!r}")


def _cuerpo(definicion: str, funcion: str) -> str:
    """De `return query` hasta el `end` de la función, sin incluirlo."""
    _una_vez(definicion, INICIO_CUERPO, funcion)
    inicio = definicion.index(INICIO_CUERPO)
    fin = definicion.rindex(FIN_CUERPO)
    if fin < inicio:
        raise RuntimeError(f"0107: {funcion} no termina después de su return query")
    return definicion[inicio:fin]


def _columnas(definicion: str, funcion: str) -> str:
    encontradas = DEVUELVE.findall(definicion)
    if len(encontradas) != 1:
        raise RuntimeError(f"0107: no se encontró una sola lista de columnas en {funcion}")
    return encontradas[0]


def upgrade() -> None:
    for api, nombre, parametros, interna, llamada in INTERNAS:
        definicion = _definicion(api)
        cuerpo = _cuerpo(definicion, api)
        op.execute(
            f"create function {nombre}({parametros})\n"
            f"  returns table ({_columnas(definicion, api)})\n"
            "  language plpgsql volatile\n"
            "  set search_path = pg_catalog, pg_temp\n"
            "  as $cuerpo$\n"
            "  begin\n"
            f"{cuerpo}"
            "  end\n"
            "  $cuerpo$"
        )
        op.execute(f"revoke all on function {interna} from public")
        op.execute(definicion.replace(cuerpo, llamada))
    op.execute(LECTURA_DEL_AVISO)
    op.execute(f"revoke all on function {AVISO} from public")
    op.execute(f"grant execute on function {AVISO} to cem_app")


def downgrade() -> None:
    op.execute(f"drop function {AVISO}")
    for api, _, _, interna, llamada in INTERNAS:
        cuerpo = _cuerpo(_definicion(interna), interna)
        definicion = _definicion(api)
        _una_vez(definicion, llamada, api)
        op.execute(definicion.replace(llamada, cuerpo))
        op.execute(f"drop function {interna}")
