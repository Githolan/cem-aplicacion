"""Los motivos escritos a mano no llevan una cuenta (`0060`): quien prepara los lee.

La escena y los ayudantes son los de `test_pago_manual_0055`.
"""

import pytest
from alembic import command
from sqlalchemy import text

from tests.test_orden_pago_neutra import RECHAZAR, cbu_sintetico, llamar, niega, orden, preparada
from tests.test_pago_manual_0055 import CONCILIAR, CUENTA, DECLARAR, emitida, registrada
from tests.test_pago_manual_0055 import e as e  # noqa: F401  (es un fixture)
from tests.test_rutas_egresos import get, post
from tests.test_vinculo_0014 import base_aparte  # noqa: F401  (es un fixture)

pytestmark = pytest.mark.db


def partida(cuenta: str, sep: str) -> str:
    """La cuenta como la escribiría una persona: en bloques de 8 y 14 con un separador."""
    return f"{cuenta[:8]}{sep}{cuenta[8:]}"


CON_CUENTA = [
    f"SINTETICO: transferiste a {CUENTA}",
    f"SINTETICO: fue a {partida(CUENTA, ' ')}, no a la de la orden",
    f"SINTETICO: cuenta {partida(CUENTA, ' - ')}",
    f"SINTETICO: {partida(CUENTA, '.')}",
    f"SINTETICO: {partida(CUENTA, '/')}",
    # Con los verificadores mal también es la cuenta (o casi toda).
    f"SINTETICO: a {CUENTA[:21]}{(int(CUENTA[21]) + 1) % 10}",
]
SIN_CUENTA = [
    "SINTETICO: el comprobante no se lee",
    f"SINTETICO: la operación {CUENTA[:21]} no aparece",  # 21 dígitos: no alcanza
    "SINTETICO: 1.234.567,89 del 01-10-2026, factura 0001-00012345",
]


@pytest.mark.parametrize("motivo", CON_CUENTA)
def test_la_observacion_no_lleva_la_cuenta(motor_migrate, e, motivo):
    o = emitida(motor_migrate, e)
    p = registrada(motor_migrate, e, o)
    niega(motor_migrate, e, e.mc2, CONCILIAR, "motivo_con_cuenta", orden=o, pago=p,
          resultado="observado", motivo=motivo)  # fmt: skip
    assert orden(motor_migrate, o)["estado"] == "en_verificacion"


@pytest.mark.parametrize("motivo", SIN_CUENTA)
def test_la_observacion_sin_cuenta_pasa(motor_migrate, e, motivo):
    """Control: los motivos de siempre, con números que no son una cuenta, siguen pasando."""
    o = emitida(motor_migrate, e)
    p = registrada(motor_migrate, e, o)
    llamar(motor_migrate, e, e.mc2, CONCILIAR, orden=o, pago=p, resultado="observado",
           motivo=motivo)  # fmt: skip


def test_no_se_pago_no_lleva_la_cuenta(motor_migrate, e):
    o = emitida(motor_migrate, e)
    niega(motor_migrate, e, e.adm, DECLARAR, "motivo_con_cuenta", orden=o, version=1,
          motivo=CON_CUENTA[1])  # fmt: skip
    llamar(motor_migrate, e, e.adm, DECLARAR, orden=o, version=1, motivo=SIN_CUENTA[0])


def test_el_rechazo_no_lleva_la_cuenta(motor_migrate, e):
    o = preparada(motor_migrate, e, en_revision=True)
    niega(motor_migrate, e, e.mc1, RECHAZAR, "motivo_con_cuenta", orden=o, version=1,
          motivo=f"SINTETICO: la cuenta tendría que ser {cbu_sintetico(7)}")  # fmt: skip
    assert orden(motor_migrate, o)["estado"] == "en_revision"
    llamar(motor_migrate, e, e.mc1, RECHAZAR, orden=o, version=1, motivo=SIN_CUENTA[0])
    assert orden(motor_migrate, o)["estado"] == "rechazada"


def test_por_http_es_422_y_quien_prepara_no_la_lee(motor_migrate, e):
    o = emitida(motor_migrate, e)
    p = registrada(motor_migrate, e, o)
    ruta = f"ordenes-pago/{o}/pago-manual/{p}/conciliacion"
    r = post(e, e.mc2, ruta, {"resultado": "observado", "motivo": CON_CUENTA[0]})
    assert (r.status_code, r.json()["codigo"]) == (422, "motivo_con_cuenta"), r.text
    assert CUENTA not in r.text
    # Lo que lee quien prepara no trae la cuenta en ningún lado.
    lectura = get(e, e.adm, "pagos-manuales")
    assert lectura.status_code == 200, lectura.text
    assert CUENTA not in lectura.text
    r = post(e, e.mc2, ruta, {"resultado": "observado", "motivo": SIN_CUENTA[0]})
    assert r.status_code == 200, r.text  # control: el mismo pedido sin la cuenta pasa


def test_0060_ida_y_vuelta(base_aparte):  # noqa: F811
    cfg, motor = base_aparte

    def disparadores() -> list[tuple[str, str]]:
        with motor.connect() as c:
            sql = ("select tgrelid::regclass::text, tgname from pg_trigger"
                   " where tgname = 'motivo_sin_cuenta' order by 1")  # fmt: skip
            return c.execute(text(sql)).all()

    command.upgrade(cfg, "0059")
    assert disparadores() == []
    command.upgrade(cfg, "0060")
    assert [t for t, _ in disparadores()] == [
        "nucleo.conciliacion_pago_manual", "nucleo.declaracion_pago_no_realizado",
        "nucleo.orden_pago",
    ]  # fmt: skip
    command.downgrade(cfg, "0059")
    assert disparadores() == []
    with motor.connect() as c:
        assert (
            c.execute(text("select to_regprocedure('nucleo.texto_con_cuenta(text)')")).scalar()
            is None
        )
    command.upgrade(cfg, "0060")  # control
    assert len(disparadores()) == 3
