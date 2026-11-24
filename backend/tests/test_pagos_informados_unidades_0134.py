"""`0134`: las unidades para las que la persona puede informar un pago (PI-S6).

Datos de prueba: SINTETICOS / NO REAL.
"""

import importlib.util
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import text

from tests.test_liquidacion_oficial_0119 import (  # noqa: F401  (los fixtures se importan)
    bucket,
    firmas_de_la_imagen,
)
from tests.test_pagos_informados_0129 import armar, listar
from tests.test_vinculo_0014 import base_aparte

pytestmark = [pytest.mark.db, pytest.mark.redis]
# ruff: noqa: F811

__all__ = ["base_aparte", "bucket", "firmas_de_la_imagen"]

_ruta = Path(__file__).parents[1] / "migrations" / "versions" / "0134_pagos_informados_unidades.py"
_spec = importlib.util.spec_from_file_location("migracion_0134", _ruta)
migracion = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migracion)


@pytest.fixture
def e(motor_migrate, motor_app, bucket) -> dict:
    return armar(motor_migrate, motor_app)


def designaciones(motor_migrate, unidades) -> list[str]:
    with motor_migrate.connect() as c:
        return list(c.execute(text(
            "select designacion from nucleo.unidad where id = any(cast(:u as uuid[]))"
            " order by designacion, id"),
            {"u": [str(u) for u in unidades]}).scalars())  # fmt: skip


def unidades(e, quien) -> list[dict]:
    r = listar(e, quien)
    assert r.status_code == 200, r.text
    return r.json()["unidades"]


def test_cada_uno_ve_solo_las_suyas_por_designacion(e, motor_migrate):
    de_prop = unidades(e, "prop")
    esperadas = designaciones(motor_migrate, [e["u"]["1-A"], e["otra_de_prop"]])
    assert [u["designacion"] for u in de_prop] == esperadas
    assert {u["id"] for u in de_prop} == {str(e["u"]["1-A"]), str(e["otra_de_prop"])}
    for quien in ("inq", "oc"):  # Q1: el inquilino y el ocupante también informan
        assert unidades(e, quien) == [{"id": str(e["u"]["PB-A"]), "designacion": "PB-A"}]
    assert unidades(e, "ad") == []  # la administración lee, pero no tiene unidades


def test_una_unidad_borrada_no_se_ofrece(e, motor_migrate):
    assert len(unidades(e, "prop")) == 2  # control: antes de borrarla, las dos
    with motor_migrate.begin() as c:
        c.execute(text("update nucleo.unidad set eliminado_en = now() where id = :u"),
                  {"u": e["otra_de_prop"]})  # fmt: skip
    assert [u["id"] for u in unidades(e, "prop")] == [str(e["u"]["1-A"])]


def test_la_ruta_es_solo_para_cem_app(motor_migrate):
    puede = text("select has_function_privilege(:r, :f, 'EXECUTE')")
    with motor_migrate.connect() as c:
        for f in migracion.RUTAS:
            assert c.execute(puede, {"r": "cem_app", "f": f}).scalar_one() is True, f
            assert c.execute(puede, {"r": "cem_ro", "f": f}).scalar_one() is False, f


def test_0134_ida_y_vuelta(base_aparte):
    cfg, motor = base_aparte

    def existe() -> bool:
        with motor.connect() as c:
            return c.execute(text("select to_regprocedure(:f) is not null"),
                             {"f": migracion.RUTAS[0]}).scalar_one()  # fmt: skip

    command.upgrade(cfg, "0134")
    assert existe()
    command.downgrade(cfg, "0133")
    assert not existe()
    command.upgrade(cfg, "0134")  # control: vuelve a subir limpio
    assert existe()
