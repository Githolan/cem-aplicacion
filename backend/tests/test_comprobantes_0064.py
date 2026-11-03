"""Una versión de un comprobante atado antes de `0063` la sube solo quien vio la cuenta (`0064`).

`0063` limita las versiones a quien vio la cuenta de la orden del documento, pero solo mira
`orden_comprobante`; un comprobante atado antes de `0063` no la tiene (P3 158). La escena es la de
`test_pago_manual_0055`. Datos SINTETICOS / NO REAL.
"""

import importlib.util
from pathlib import Path
from uuid import UUID

import pytest
from alembic import command
from sqlalchemy import text

from tests.test_comprobantes_0063 import VERSION
from tests.test_orden_pago_neutra import llamar, niega
from tests.test_pago_manual_0055 import REGISTRAR, emitida, evidencia, otro_ejecutor, ver
from tests.test_pago_manual_0055 import e as e  # noqa: F401  (es un fixture)
from tests.test_vinculo_0014 import base_aparte  # noqa: F401  (es un fixture)

pytestmark = pytest.mark.db


def atado_antes_de_0063(motor_migrate, e, quien, o) -> UUID:
    """Registrado por `quien`, que vio la cuenta; después se le borra la orden de la carga, como
    quedó todo comprobante atado antes de `0063` (el disparador se apaga solo para eso)."""
    ver(motor_migrate, e, quien, o)
    datos = evidencia(motor_migrate, e, o)
    llamar(motor_migrate, e, quien, REGISTRAR, **datos)
    with motor_migrate.begin() as c:
        c.execute(text("alter table nucleo.documento disable trigger orden_comprobante_fija"))
        c.execute(text("update nucleo.documento set orden_comprobante = null where id = :d"),
                  {"d": datos["documento"]})  # fmt: skip
        c.execute(text("alter table nucleo.documento enable trigger orden_comprobante_fija"))
    return datos["documento"]


def test_version_de_un_atado_antes_de_0063_la_sube_solo_quien_vio_la_cuenta(
    motor_migrate, motor_app, e
):
    """x transfirió y registró; la administración tiene `documentos.cargar` y no vio la cuenta de
    esa orden. Para ella el documento no existe. El de una orden que ella misma pagó, sí."""
    o, propia = emitida(motor_migrate, e), emitida(motor_migrate, e)
    x = otro_ejecutor(motor_migrate, motor_app, e)
    doc = atado_antes_de_0063(motor_migrate, e, x, o)
    control = atado_antes_de_0063(motor_migrate, e, e.adm, propia)
    with motor_migrate.connect() as c:
        sql = ("select count(*) from nucleo.documento where id in (:d, :k)"
               " and orden_comprobante is null")  # fmt: skip
        assert c.execute(text(sql), {"d": doc, "k": control}).scalar_one() == 2
    niega(motor_migrate, e, e.adm, VERSION, "no_encontrado", documento=doc)
    assert llamar(motor_migrate, e, e.adm, VERSION, documento=control) is not None


def test_0064_ida_y_vuelta(base_aparte):  # noqa: F811
    cfg, motor = base_aparte
    m = migracion_0064()

    def definicion() -> str:
        with motor.connect() as c:
            sql = "select pg_get_functiondef(cast(:v as regprocedure))"
            return c.execute(text(sql), {"v": m.VERSION}).scalar_one()

    command.upgrade(cfg, "0063")
    antes = definicion()
    assert "comprobante_pago_manual" not in antes
    command.upgrade(cfg, "0064")
    assert "comprobante_pago_manual" in definicion()
    command.downgrade(cfg, "0063")
    assert definicion() == antes
    command.upgrade(cfg, "0064")  # control
    assert "comprobante_pago_manual" in definicion()


def migracion_0064():
    ruta = Path(__file__).resolve().parents[1] / "migrations" / "versions"
    archivo = ruta / "0064_version_de_comprobante_atado.py"
    spec = importlib.util.spec_from_file_location("migracion_0064", archivo)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo
