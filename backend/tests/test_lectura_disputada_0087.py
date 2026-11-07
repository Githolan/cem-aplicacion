"""«Se leyó mal» queda guardado, contra PostgreSQL real (`0087`). Datos SINTETICOS / PRUEBA / NO
REAL. Decisión 14 del propietario, 07-11-2026."""

import pytest

from tests.test_circuito_pagos_0072 import ficha, observada
from tests.test_circuito_pagos_0072 import fila as fila_de_bandeja
from tests.test_documentos_2c1 import (  # noqa: F401  (`bucket` y `firmas_de_la_imagen` son fixtures)
    analizar,
    bucket,
    firmas_de_la_imagen,
)
from tests.test_lectura_comprobante_0077 import (
    leida,
    leido,
    modelo,  # noqa: F401  (es un fixture)
    pagando,
    proveedor,  # noqa: F401  (es un fixture)
    registro,
    responder,
    subido,
)
from tests.test_pago_manual_0055 import (
    CONCILIAR,
    llamar,
)
from tests.test_pago_manual_0055 import (
    e as e,  # noqa: F401  (es un fixture)
)
from tests.test_rutas_egresos import codigo, post

pytestmark = [pytest.mark.db, pytest.mark.redis]
# ruff: noqa: F811


def con_lectura(motor_migrate, motor_app, e, modelo):
    o = pagando(motor_migrate, e)
    responder(modelo, leido(motor_migrate, e, importe="1.00"))
    doc, _ = leida(motor_migrate, motor_app, e, o)
    return o, doc


def registrado(motor_migrate, e, o, doc, **cambios):
    r = post(e, e.adm, f"ordenes-pago/{o}/pago-manual",
             registro(motor_migrate, e, o, doc, **cambios))  # fmt: skip
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_el_consejo_lo_ve_en_la_ficha_y_en_la_bandeja(motor_migrate, motor_app, e, bucket, modelo):
    o, doc = con_lectura(motor_migrate, motor_app, e, modelo)
    registrado(motor_migrate, e, o, doc, lectura_mal=True)
    f = ficha(e, e.mc2, o)
    assert f["ejecucion"]["pagos"][-1]["lectura_disputada"] is True
    x = fila_de_bandeja(e, e.mc2, o)
    assert (x["paso"], x["lectura_disputada"]) == ("conciliar", True)
    # Quien no concilia no recibe la nota en la fila.
    assert fila_de_bandeja(e, e.adm, o)["lectura_disputada"] is False


def test_control_sin_elegirlo_no_dice_nada(motor_migrate, motor_app, e, bucket, modelo):
    o, doc = con_lectura(motor_migrate, motor_app, e, modelo)
    registrado(motor_migrate, e, o, doc)
    assert ficha(e, e.mc2, o)["ejecucion"]["pagos"][-1]["lectura_disputada"] is False
    assert fila_de_bandeja(e, e.mc2, o)["lectura_disputada"] is False


def test_sin_lectura_no_hay_que_discutir(motor_migrate, motor_app, e, bucket):
    o = pagando(motor_migrate, e)
    doc, _ = subido(motor_app, e, o)
    assert analizar(motor_app) == 1
    r = post(e, e.adm, f"ordenes-pago/{o}/pago-manual",
             registro(motor_migrate, e, o, doc, lectura_mal=True))  # fmt: skip
    assert codigo(r) == (422, "peticion_invalida"), r.text
    assert ficha(e, e.mc2, o)["ejecucion"]["pagos"] == []


def test_no_cambia_ninguna_regla(motor_migrate, motor_app, e, bucket, modelo):
    """Decir que se leyó mal no acepta lo que no coincide con la orden."""
    o, doc = con_lectura(motor_migrate, motor_app, e, modelo)
    r = post(e, e.adm, f"ordenes-pago/{o}/pago-manual",
             registro(motor_migrate, e, o, doc, lectura_mal=True,
                      importe_centavos=100))  # fmt: skip
    assert codigo(r) == (422, "no_coincide_con_la_orden"), r.text


def test_conciliado_la_ficha_lo_sigue_diciendo(motor_migrate, motor_app, e, bucket, modelo):
    o, doc = con_lectura(motor_migrate, motor_app, e, modelo)
    p = registrado(motor_migrate, e, o, doc, lectura_mal=True)
    llamar(motor_migrate, e, e.mc2, CONCILIAR, orden=o, pago=p, resultado="conforme", motivo=None)
    assert ficha(e, e.mc2, o)["ejecucion"]["pagos"][-1]["lectura_disputada"] is True


def test_observado_ya_no_espera_conciliar(motor_migrate, motor_app, e, bucket, modelo):
    o, doc = con_lectura(motor_migrate, motor_app, e, modelo)
    p = registrado(motor_migrate, e, o, doc, lectura_mal=True)
    observada(motor_migrate, e, o, p)
    assert fila_de_bandeja(e, e.mc2, o)["lectura_disputada"] is False
