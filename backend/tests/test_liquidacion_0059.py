"""Liquidación (`0059`): las lecturas muestran la misma fecha de pago que usa la regla (P3 139).

La escena y los ayudantes son los de `test_liquidacion_0058`.
"""

from datetime import date, timedelta

import pytest
from alembic import command
from sqlalchemy import text

from tests.test_liquidacion_0057 import AGREGAR, borrador, pagada
from tests.test_liquidacion_0058 import agua_desde, fechar_pago, hoy, mes
from tests.test_orden_pago_neutra import llamar
from tests.test_pago_manual_0055 import e as e  # noqa: F401  (es un fixture)
from tests.test_rutas_egresos import get
from tests.test_vinculo_0014 import base_aparte  # noqa: F401  (es un fixture)

pytestmark = pytest.mark.db


def liquidables(e) -> dict[str, str]:
    r = get(e, e.adm, "liquidaciones/ordenes-liquidables")
    assert r.status_code == 200, r.text
    return {x["orden_id"]: x["pagada_el"] for x in r.json()["elementos"]}


def test_con_pago_manual_las_lecturas_dan_la_fecha_de_la_operacion(motor_migrate, motor_app, e):
    """Transferida el último día del mes pasado y conciliada hoy: las dos lecturas dicen el último
    día, que es la fecha con la que entra en el período (antes decían la de la conciliación)."""
    d = hoy(motor_migrate, e)
    ultimo = date.fromisoformat(mes(d, 0)) - timedelta(days=1)
    agua_desde(motor_migrate, motor_app, e, mes(d, -1))
    anterior = borrador(motor_migrate, e, mes(d, -1))
    o = pagada(motor_migrate, e)  # conciliada hoy
    assert liquidables(e) == {str(o): d.isoformat()}  # control: sin correr, la de hoy
    fechar_pago(motor_migrate, o, operacion=ultimo)
    assert liquidables(e) == {str(o): ultimo.isoformat()}
    llamar(motor_migrate, e, e.adm, AGREGAR, liquidacion=anterior, orden=o, grupo="agua")
    r = get(e, e.adm, f"liquidaciones/{anterior}")
    assert r.status_code == 200, r.text
    assert [g["pagada_el"] for g in r.json()["gastos"]] == [ultimo.isoformat()]


def test_con_el_riel_las_lecturas_dan_la_fecha_del_consorcio(motor_migrate, e):
    """El 1 a las 02:00 UTC todavía es el último día en Buenos Aires; a las 03:30 ya no."""
    d = hoy(motor_migrate, e)
    primero = date.fromisoformat(mes(d, 0))
    o = pagada(motor_migrate, e)
    fechar_pago(motor_migrate, o, origen="ordenar", registrado=f"{primero} 02:00+00")
    otra = pagada(motor_migrate, e)
    fechar_pago(motor_migrate, otra, origen="ordenar", registrado=f"{primero} 03:30+00")
    assert liquidables(e) == {str(o): (primero - timedelta(days=1)).isoformat(),
                              str(otra): primero.isoformat()}  # fmt: skip


def test_0059_ida_y_vuelta(base_aparte):  # noqa: F811
    cfg, motor = base_aparte

    def funciones() -> list[tuple[str, str]]:
        with motor.connect() as c:
            return c.execute(text("select pg_get_functiondef(p.oid), coalesce(p.proacl::text, '')"
                                  " from pg_proc p join pg_namespace n on n.oid = p.pronamespace"
                                  " where n.nspname = 'nucleo' and p.proname in"
                                  " ('agregar_gasto_liquidacion', 'liquidacion_gastos_lectura',"
                                  "  'ordenes_liquidables_lectura', 'fecha_pago_ejecucion')"
                                  " order by p.proname")).all()  # fmt: skip

    command.upgrade(cfg, "0058")
    de_0058 = funciones()
    command.upgrade(cfg, "0059")
    de_0059 = funciones()
    assert len(de_0059) == len(de_0058) + 1 and de_0059 != de_0058
    assert all("registrado_en" not in f for f, _ in de_0059 if "lectura" in f)
    command.downgrade(cfg, "0058")
    assert funciones() == de_0058  # vuelven exactas, con sus permisos
    command.upgrade(cfg, "0059")  # control
    assert funciones() == de_0059
