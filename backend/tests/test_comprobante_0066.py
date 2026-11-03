"""La carga del comprobante de un pago manual exige R10 en la base (`0066`, P3 170).

Antes la sesión reciente y el segundo factor los exigía solo la ruta HTTP. Datos SINTETICOS / NO
REAL.
"""

import importlib.util
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import text

from tests.test_comprobantes_0063 import CREAR, TITULO
from tests.test_comprobantes_0065 import ver_version
from tests.test_orden_pago_neutra import llamar, niega, nueva_sesion
from tests.test_pago_manual_0055 import e as e  # noqa: F401  (es un fixture)
from tests.test_pago_manual_0055 import emitida
from tests.test_vinculo_0014 import base_aparte  # noqa: F401  (es un fixture)

pytestmark = pytest.mark.db


@pytest.mark.parametrize(
    ("horas_atras", "totp_min_atras", "motivo"),
    [
        (1, 11, "requiere_segundo_factor_reciente"),
        (1, None, "requiere_segundo_factor_reciente"),
        (13, 0, "requiere_sesion_reciente"),
    ],
)
def test_sin_sesion_reciente_con_segundo_factor_no_se_carga_el_comprobante(
    motor_migrate, e, horas_atras, totp_min_atras, motivo
):
    o = emitida(motor_migrate, e)
    ver_version(motor_migrate, e, e.adm, o, 1)  # vio la cuenta de la ejecución abierta
    vieja = nueva_sesion(motor_migrate, e.adm, horas_atras=horas_atras,
                         totp_min_atras=totp_min_atras)  # fmt: skip
    niega(motor_migrate, e, e.adm, CREAR, motivo, sesion=vieja, orden=o, titulo=TITULO)
    # Control: la misma persona, con su sesión reciente, sí lo carga.
    assert llamar(motor_migrate, e, e.adm, CREAR, orden=o, titulo=TITULO) is not None


def test_con_la_sesion_de_otra_persona_no_se_carga(motor_migrate, e):
    o = emitida(motor_migrate, e)
    ver_version(motor_migrate, e, e.adm, o, 1)
    niega(motor_migrate, e, e.adm, CREAR, "sesion_invalida", sesion=e.sesion[e.mc2], orden=o,
          titulo=TITULO)  # fmt: skip


def test_0066_ida_y_vuelta(base_aparte):  # noqa: F811
    cfg, motor = base_aparte
    m = migracion_0066()

    def firmas() -> tuple:
        with motor.connect() as c:
            return (
                c.execute(
                    text("select to_regprocedure(:f) is not null"), {"f": m.CREAR_0063}
                ).scalar_one(),
                c.execute(
                    text("select to_regprocedure(:f) is not null"), {"f": m.CREAR_R10}
                ).scalar_one(),
                c.execute(
                    text("select pg_get_functiondef(to_regprocedure(:f))"), {"f": m.CREAR_0063}
                ).scalar_one(),
            )

    command.upgrade(cfg, "0065")
    antes = firmas()
    assert antes[:2] == (True, False)
    command.upgrade(cfg, "0066")
    assert firmas()[:2] == (False, True)
    command.downgrade(cfg, "0065")
    assert firmas() == antes
    command.upgrade(cfg, "0066")  # control
    assert firmas()[:2] == (False, True)


def migracion_0066():
    ruta = Path(__file__).resolve().parents[1] / "migrations" / "versions"
    archivo = ruta / "0066_comprobante_con_r10.py"
    spec = importlib.util.spec_from_file_location("migracion_0066", archivo)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo
