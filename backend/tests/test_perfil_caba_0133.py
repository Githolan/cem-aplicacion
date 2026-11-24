"""`0133`: la versión 1 del perfil de CABA rige desde el 1-8-2024 (P0-13).

Datos de prueba: SINTETICOS / NO REAL.
"""

import importlib.util
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.test_vinculo_0014 import base_aparte

pytestmark = [pytest.mark.db]
# ruff: noqa: F811

__all__ = ["base_aparte"]

_versiones = Path(__file__).parents[1] / "migrations" / "versions"


def _cargar(nombre: str):
    spec = importlib.util.spec_from_file_location(nombre.removesuffix(".py"), _versiones / nombre)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


migracion = _cargar("0133_perfil_caba_desde_agosto.py")
migracion_0125 = _cargar("0125_perfil_normativo.py")

DESDE = text(
    "select vigente_desde::text from nucleo.perfil_normativo"
    " where jurisdiccion = 'caba' and version = 1"
)


def test_la_fecha_vieja_es_la_que_cargo_0125():
    assert migracion.VIGENTE_DESDE_0125 == migracion_0125.CABA_1["vigente_desde"]
    assert (migracion.JURISDICCION, migracion.VERSION) == ("caba", 1)
    assert migracion.VIGENTE_DESDE == "2024-08-01"


def test_en_la_base_rige_desde_el_1_de_agosto(motor_migrate):
    with motor_migrate.connect() as c:
        assert c.execute(DESDE).scalar_one() == "2024-08-01"


def test_0133_ida_y_vuelta_y_la_historia_sigue_cerrada(base_aparte):
    cfg, motor = base_aparte

    def desde() -> str:
        with motor.connect() as c:
            return c.execute(DESDE).scalar_one()

    def cambiar_a_mano() -> str:
        with pytest.raises(DBAPIError) as error:
            with motor.begin() as c:
                c.execute(text(
                    "update nucleo.perfil_normativo set vigente_desde = '2024-09-01'"
                    " where jurisdiccion = 'caba' and version = 1"))  # fmt: skip
        return error.value.orig.diag.message_hint

    command.upgrade(cfg, "0132")
    assert desde() == "2024-07-31"
    command.upgrade(cfg, "0133")
    assert desde() == "2024-08-01"
    assert cambiar_a_mano() == "historia_inmutable"  # el disparador volvió a quedar prendido
    command.downgrade(cfg, "0132")
    assert desde() == "2024-07-31"
    assert cambiar_a_mano() == "historia_inmutable"
    command.upgrade(cfg, "0133")  # control: vuelve a subir limpio
    assert desde() == "2024-08-01"


def test_0133_no_toca_nada_si_la_fila_no_es_la_esperada(base_aparte):
    cfg, motor = base_aparte
    command.upgrade(cfg, "0132")
    with motor.begin() as c:
        c.execute(
            text("alter table nucleo.perfil_normativo disable trigger historia_perfil_normativo")
        )
        c.execute(text(
            "update nucleo.perfil_normativo set vigente_desde = '2024-07-15'"
            " where jurisdiccion = 'caba' and version = 1"))  # fmt: skip
        c.execute(
            text("alter table nucleo.perfil_normativo enable trigger historia_perfil_normativo")
        )
    with pytest.raises(DBAPIError, match="se esperaba una fila"):
        command.upgrade(cfg, "0133")
    with motor.connect() as c:
        assert c.execute(DESDE).scalar_one() == "2024-07-15"
