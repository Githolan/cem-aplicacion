"""Liquidación (`0058`): descartar un borrador, período acotado y pagada hasta el fin del período.

Revisión crítica de los P3 129, 130 y 132 (mandato del propietario del 31-10-2026). La escena es
la de `test_liquidacion_0057`.
"""

import importlib.util
from datetime import date
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.test_coeficientes_0021 import registrar
from tests.test_liquidacion_0057 import AGREGAR, borrador, pagada, porcentajes, unidades
from tests.test_liquidacion_0057 import esquema as esquema  # noqa: F401  (es un fixture)
from tests.test_orden_pago_neutra import llamar, niega
from tests.test_pago_manual_0055 import acta
from tests.test_pago_manual_0055 import e as e  # noqa: F401  (es un fixture)
from tests.test_rutas_egresos import codigo, get, post
from tests.test_vinculo_0014 import base_aparte  # noqa: F401  (es un fixture)

pytestmark = pytest.mark.db

DESCARTAR = "select nucleo.descartar_liquidacion(:liquidacion, :motivo, :actor, :origen, :sesion)"
MOTIVO = "SINTETICO: período equivocado"


def mes(d: date, mas: int) -> str:
    """El primer día del mes que está `mas` meses después del de `d`."""
    total = d.year * 12 + d.month - 1 + mas
    return date(total // 12, total % 12 + 1, 1).isoformat()


def hoy(motor_migrate, e) -> date:
    with motor_migrate.connect() as c:
        return c.execute(text("select nucleo.hoy_del_consorcio(:c)"), {"c": e.cons}).scalar_one()


def liquidacion(motor_migrate, liq) -> dict:
    with motor_migrate.connect() as c:
        return dict(
            c.execute(text("select * from nucleo.liquidacion where id = :l"), {"l": liq})
            .mappings()
            .one()
        )


def _migracion(nombre: str):
    ruta = Path(__file__).parents[1] / "migrations" / "versions" / nombre
    spec = importlib.util.spec_from_file_location(nombre.removesuffix(".py"), ruta)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


# P3 129: descartar ----------------------------------------------------------------------------


def test_descartar_libera_el_periodo_y_sus_ordenes_sin_borrar_historia(motor_migrate, e, esquema):
    o = pagada(motor_migrate, e)
    liq = borrador(motor_migrate, e)
    gasto = llamar(motor_migrate, e, e.adm, AGREGAR, liquidacion=liq, orden=o,
                   grupo="expensas_comunes")  # fmt: skip
    niega(motor_migrate, e, e.adm, AGREGAR, "gasto_ya_liquidado", liquidacion=liq, orden=o,
          grupo="expensas_comunes")  # control: la orden está tomada  # fmt: skip
    r = post(e, e.adm, f"liquidaciones/{liq}/descartar", {"motivo": MOTIVO})
    assert r.status_code == 200, r.json()
    fila = liquidacion(motor_migrate, liq)
    assert fila["estado"] == "descartada" and fila["motivo_descarte"] == MOTIVO
    assert fila["descartada_por"] == e.adm and fila["identidad_descarto"] is not None
    with motor_migrate.connect() as c:
        quitado = c.execute(
            text("select quitado_por from nucleo.liquidacion_gasto_quitado where gasto_id = :g"),
            {"g": gasto},
        ).scalar_one()  # fmt: skip
        acciones = c.execute(
            text("select accion from auditoria.eventos where entidad_id = :l order by secuencia"),
            {"l": liq},
        ).scalars().all()  # fmt: skip
    assert quitado == e.adm
    assert acciones[-1] == "consorcio.liquidacion.descartar"
    otra = borrador(motor_migrate, e)
    assert liquidacion(motor_migrate, otra)["version"] == 2
    llamar(motor_migrate, e, e.adm, AGREGAR, liquidacion=otra, orden=o, grupo="expensas_comunes")
    estados = {x["id"]: x["estado"] for x in get(e, e.adm, "liquidaciones").json()["elementos"]}
    assert estados[str(liq)] == "descartada" and estados[str(otra)] == "borrador"


def test_solo_se_descarta_un_borrador_con_motivo_y_una_vez(motor_migrate, e):
    liq = borrador(motor_migrate, e)
    niega(motor_migrate, e, e.adm, DESCARTAR, "motivo_invalido", liquidacion=liq, motivo="  ")
    r = post(e, e.adm, f"liquidaciones/{liq}/descartar", {"motivo": ""})
    assert r.status_code == 422
    niega(motor_migrate, e, e.mc1, DESCARTAR, "42501", liquidacion=liq, motivo=MOTIVO)
    llamar(motor_migrate, e, e.adm, DESCARTAR, liquidacion=liq, motivo=MOTIVO)  # control
    niega(motor_migrate, e, e.adm, DESCARTAR, "liquidacion_no_admite", liquidacion=liq,
          motivo=MOTIVO)  # fmt: skip
    r = post(e, e.adm, f"liquidaciones/{liq}/descartar", {"motivo": MOTIVO})
    assert codigo(r) == (409, "liquidacion_no_admite")
    niega(motor_migrate, e, e.adm, AGREGAR, "liquidacion_no_admite", liquidacion=liq,
          orden=liq, grupo="expensas_comunes")  # fmt: skip


@pytest.mark.parametrize(
    "cambio",
    [
        "periodo = periodo + interval '1 month'",
        "version = version + 1",
        "estado = 'publicada'",
        "motivo_descarte = 'otro'",
    ],
)
def test_la_tabla_solo_admite_pasar_de_borrador_a_descartada(motor_migrate, e, cambio):
    liq = borrador(motor_migrate, e)
    with pytest.raises(DBAPIError) as error, motor_migrate.begin() as c:
        c.execute(text(f"update nucleo.liquidacion set {cambio} where id = :l"), {"l": liq})
    assert error.value.orig.diag.message_hint == "historia_inmutable"
    llamar(motor_migrate, e, e.adm, DESCARTAR, liquidacion=liq, motivo=MOTIVO)
    for sentencia in ("update nucleo.liquidacion set estado = 'borrador',"
                      " descartada_en = null, descartada_por = null, identidad_descarto = null,"
                      " motivo_descarte = null where id = :l",
                      "delete from nucleo.liquidacion where id = :l"):  # fmt: skip
        with pytest.raises(DBAPIError) as error, motor_migrate.begin() as c:
            c.execute(text(sentencia), {"l": liq})
        assert error.value.orig.diag.message_hint == "historia_inmutable"
    assert liquidacion(motor_migrate, liq)["estado"] == "descartada"


def test_el_periodo_es_a_lo_sumo_el_mes_que_viene(motor_migrate, e):
    d = hoy(motor_migrate, e)
    r = post(e, e.adm, "liquidaciones", {"periodo": mes(d, 2)})
    assert codigo(r) == (422, "peticion_invalida")
    assert "mes que viene" in r.json()["mensaje"]
    assert post(e, e.adm, "liquidaciones", {"periodo": mes(d, 1)}).status_code == 201  # control


# P3 130: la orden pagada hasta el último día del período ---------------------------------------


def test_una_orden_pagada_despues_del_periodo_no_entra(motor_migrate, e, esquema):
    o = pagada(motor_migrate, e)  # pagada hoy
    d = hoy(motor_migrate, e)
    anterior = borrador(motor_migrate, e, mes(d, -1))
    niega(motor_migrate, e, e.adm, AGREGAR, "pagada_despues_del_periodo", liquidacion=anterior,
          orden=o, grupo="expensas_comunes")  # fmt: skip
    r = post(e, e.adm, f"liquidaciones/{anterior}/gastos",
             {"orden_id": str(o), "grupo": "expensas_comunes"})  # fmt: skip
    assert codigo(r) == (409, "orden_no_liquidable") and "período posterior" in r.json()["mensaje"]
    actual = borrador(motor_migrate, e, mes(d, 0))
    llamar(motor_migrate, e, e.adm, AGREGAR, liquidacion=actual, orden=o,
           grupo="expensas_comunes")  # control  # fmt: skip
    siguiente = borrador(motor_migrate, e, mes(d, 1))
    llamar(motor_migrate, e, e.adm, DESCARTAR, liquidacion=actual, motivo=MOTIVO)
    llamar(motor_migrate, e, e.adm, AGREGAR, liquidacion=siguiente, orden=o,
           grupo="expensas_comunes")  # pagada antes y olvidada: entra después  # fmt: skip


# P3 132: las guardas de los downgrades ---------------------------------------------------------


def test_las_guardas_del_downgrade_se_niegan_con_datos(motor_migrate, e):
    liq = borrador(motor_migrate, e)
    guarda_57 = _migracion("0057_liquidacion_borrador.py").DOWNGRADE
    guarda_58 = _migracion("0058_liquidacion_descarte_y_fechas.py").DOWNGRADE
    for guarda in (guarda_57, guarda_58):
        if guarda is guarda_58:
            llamar(motor_migrate, e, e.adm, DESCARTAR, liquidacion=liq, motivo=MOTIVO)
        with motor_migrate.connect() as c, c.begin() as tx:
            with pytest.raises(DBAPIError) as error, c.begin_nested():
                c.execute(text(guarda))
            assert error.value.orig.diag.message_hint == "downgrade_con_datos"
            tx.rollback()
    assert liquidacion(motor_migrate, liq)["estado"] == "descartada"


def test_0058_ida_y_vuelta(base_aparte):  # noqa: F811
    cfg, motor = base_aparte

    def columnas() -> int:
        with motor.connect() as c:
            return c.execute(text("select count(*) from information_schema.columns"
                                  " where table_schema = 'nucleo' and table_name = 'liquidacion'"
                                  " and column_name like 'descart%'")).scalar_one()  # fmt: skip

    command.upgrade(cfg, "0058")
    assert columnas() == 2
    command.downgrade(cfg, "0057")
    assert columnas() == 0
    command.upgrade(cfg, "0058")  # control
    assert columnas() == 2


def test_el_esquema_de_un_grupo_rige_desde_su_fecha(motor_migrate, motor_app, e, esquema):
    """Lo que antes probaba el período de 2023: con `0058`, una orden pagada hoy no entra en un
    período pasado, así que el esquema que todavía no rige se prueba con uno que empieza el mes
    que viene."""
    o = pagada(motor_migrate, e)
    us = unidades(motor_migrate, e)
    coeficientes = {str(u): f"{p:.6f}" for u, p in zip(us, porcentajes(len(us)), strict=True)}
    d = hoy(motor_migrate, e)
    registrar(motor_app, e.cons, coeficientes, grupo="agua", desde=mes(d, 1),
              decision=acta(motor_migrate, e))  # fmt: skip
    actual, siguiente = borrador(motor_migrate, e, mes(d, 0)), borrador(motor_migrate, e, mes(d, 1))
    niega(motor_migrate, e, e.adm, AGREGAR, "sin_esquema", liquidacion=actual, orden=o,
          grupo="agua")  # fmt: skip
    llamar(motor_migrate, e, e.adm, AGREGAR, liquidacion=siguiente, orden=o,
           grupo="agua")  # control  # fmt: skip
