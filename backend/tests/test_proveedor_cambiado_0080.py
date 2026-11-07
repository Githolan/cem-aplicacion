"""Aprobada con el proveedor cambiado, contra PostgreSQL real (`0080`). Datos SINTETICOS / PRUEBA /
NO REAL. Decisión 4 del propietario, 07-11-2026.
"""

import pytest

from tests.test_circuito_pagos_0072 import (
    e,  # noqa: F401  (es un fixture)
    ficha,
    fila,
    paso,
)
from tests.test_egresos_1a import aprobada, nueva_cuenta
from tests.test_rutas_egresos import codigo, get, post

pytestmark = pytest.mark.db
# ruff: noqa: F811


def test_quien_prepara_ve_que_hay_que_modificarla(motor_migrate, e):
    o = aprobada(motor_migrate, e)
    assert paso(e, e.adm, o) == "pagar"  # control: con el proveedor de la orden
    nueva_cuenta(motor_migrate, e, 7)
    x = fila(e, e.adm, o)
    assert (x["paso"], x["grupo"], x["le_toca_a"]) == ("modificar", "para_preparar", "vos")
    f = ficha(e, e.adm, o)
    assert (f["puede_modificar"], f["puede_pagar"]) == (True, False)
    # Quien no prepara no recibe el paso.
    assert paso(e, e.mc1, o) is None


def test_modificarla_la_devuelve_a_aprobacion_y_el_paso_se_va(motor_migrate, e):
    o = aprobada(motor_migrate, e)
    nueva_cuenta(motor_migrate, e, 7)
    v = get(e, e.adm, f"ordenes-pago/{o}").json()["versiones"][-1]
    r = post(e, e.adm, f"ordenes-pago/{o}/versiones", {
        "version_esperada": 1, "importe_centavos": v["importe_centavos"], "moneda": v["moneda"],
        "concepto": v["concepto"], "proveedor_id": v["proveedor_id"],
        "comprobante": v["comprobante"], "usa_fondo_reserva": v["usa_fondo_reserva"],
        "contrato_id": None,
    })  # fmt: skip
    assert r.status_code == 200, r.text
    assert paso(e, e.adm, o) != "modificar"


def test_la_regla_no_cambia_sin_modificar_no_se_emite(motor_migrate, e):
    o = aprobada(motor_migrate, e)
    nueva_cuenta(motor_migrate, e, 7)
    v = get(e, e.adm, f"ordenes-pago/{o}").json()["versiones"][-1]
    r = post(e, e.adm, f"ordenes-pago/{o}/emitir", {"version": 1, "huella": v["huella"]})
    assert codigo(r) == (409, "proveedor_cambiado"), r.text
