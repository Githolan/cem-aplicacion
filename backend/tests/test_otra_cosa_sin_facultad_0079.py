"""«Se transfirió otro importe o a otra cuenta» para quien perdió la facultad, contra PostgreSQL
real (`0079`). Datos SINTETICOS / PRUEBA / NO REAL: ninguna cuenta es de un banco (prefijo 999)
y no se mueve dinero.

Decisión 3 del propietario, 07-11-2026.
"""

import pytest
from sqlalchemy import text

from tests.test_mandatos import otra_persona
from tests.test_orden_pago_neutra import huella, nueva_sesion
from tests.test_pago_manual_0055 import CUENTA, niega
from tests.test_pago_manual_0055 import e as e  # noqa: F401  (es un fixture)
from tests.test_pague_otra_cosa_0075 import (
    OTRA_CUENTA,
    REAL_DE_MENOS,
    RESOLVER,
    con_diferencia,
    fila_de,
    resuelta,
)
from tests.test_registro_sin_facultad_0068 import comprobante_sin_facultad, trabada
from tests.test_rutas_egresos import codigo, post

pytestmark = [pytest.mark.db, pytest.mark.redis]
# ruff: noqa: F811


def cuerpo(motor_migrate, e, o, doc) -> dict:
    with motor_migrate.connect() as c:
        hoy = c.execute(text("select nucleo.hoy_del_consorcio(:c)"), {"c": e.cons}).scalar_one()
    return {
        "version": 1, "huella": huella(motor_migrate, o, 1).hex(),
        "numero_operacion": "OP-SINTETICA-79", "fecha_operacion": str(hoy),
        "importe_real": str(REAL_DE_MENOS), "moneda": "ARS", "cuenta_real": OTRA_CUENTA,
        "documento_id": str(doc), "explicacion": "SINTETICO: transferí otra cosa",
    }  # fmt: skip


def test_quien_perdio_la_facultad_registra_lo_que_paso_marcado(motor_migrate, motor_app, e):
    o = trabada(motor_migrate, motor_app, e)
    doc = comprobante_sin_facultad(motor_migrate, e, o)["documento"]
    r = post(e, e.adm, f"pagos-por-aclarar/{o}/diferencia", cuerpo(motor_migrate, e, o, doc))
    assert r.status_code == 201, r.text
    d = r.json()["id"]
    f = fila_de(motor_migrate, d)
    assert (f["sin_facultad"], f["tipo"], f["registrada_por"]) == (True, "ambos", e.adm)
    # Lo resuelve el Consejo igual que siempre; la cuenta es otra: solo reclamo.
    assert resuelta(motor_migrate, e, o, d, "reclamo") == "en_verificacion"


def test_lo_que_salio_en_otra_moneda_queda_como_diferencia_de_moneda(motor_migrate, motor_app, e):
    o = trabada(motor_migrate, motor_app, e)
    doc = comprobante_sin_facultad(motor_migrate, e, o)["documento"]
    datos = cuerpo(motor_migrate, e, o, doc) | {"moneda": "USD", "cuenta_real": CUENTA}
    r = post(e, e.adm, f"pagos-por-aclarar/{o}/diferencia", datos)
    assert r.status_code == 201, r.text
    d = r.json()["id"]
    f = fila_de(motor_migrate, d)
    assert (f["tipo"], f["moneda"], f["sin_facultad"]) == ("importe", "USD", True)
    # Otra moneda: no se da por pagada, solo se reclama.
    pedido = {"orden": o, "diferencia": d, "resolucion": "pagada_por_lo_real"}
    niega(motor_migrate, e, e.mc2, RESOLVER, "solo_reclamo", **pedido)
    assert resuelta(motor_migrate, e, o, d, "reclamo") == "en_verificacion"


def test_en_la_moneda_de_la_orden_se_puede_dar_por_pagada(motor_migrate, motor_app, e):
    """Control de la anterior: el mismo registro en ARS no queda solo para reclamar."""
    o = trabada(motor_migrate, motor_app, e)
    doc = comprobante_sin_facultad(motor_migrate, e, o)["documento"]
    datos = cuerpo(motor_migrate, e, o, doc) | {"cuenta_real": CUENTA}
    r = post(e, e.adm, f"pagos-por-aclarar/{o}/diferencia", datos)
    assert r.status_code == 201, r.text
    f = fila_de(motor_migrate, r.json()["id"])
    assert (f["tipo"], f["moneda"]) == ("importe", "ARS")
    assert resuelta(motor_migrate, e, o, r.json()["id"], "pagada_por_lo_real") == "pagada"


def test_a_quien_no_vio_la_cuenta_le_responde_404(motor_migrate, motor_app, e):
    o = trabada(motor_migrate, motor_app, e)
    doc = comprobante_sin_facultad(motor_migrate, e, o)["documento"]
    x = otra_persona(motor_migrate, e.cons)
    e.sesion[x] = nueva_sesion(motor_migrate, x)
    datos = cuerpo(motor_migrate, e, o, doc)
    for quien in (e.mc2, x):
        r = post(e, quien, f"pagos-por-aclarar/{o}/diferencia", datos)
        assert codigo(r) == (404, "no_encontrado"), r.text
    with motor_migrate.connect() as c:
        hechas = c.execute(text("select count(*) from nucleo.diferencia_pago_manual"
                                " where orden_id = :o"), {"o": o}).scalar_one()  # fmt: skip
    assert hechas == 0
    r = post(e, e.adm, f"pagos-por-aclarar/{o}/diferencia", datos)  # control
    assert r.status_code == 201, r.text


def test_sin_diferencia_tampoco_acá(motor_migrate, motor_app, e):
    from tests.test_pago_manual_0055 import CUENTA, IMPORTE

    o = trabada(motor_migrate, motor_app, e)
    doc = comprobante_sin_facultad(motor_migrate, e, o)["documento"]
    datos = cuerpo(motor_migrate, e, o, doc) | {
        "importe_real": f"{IMPORTE / 100:.2f}", "cuenta_real": CUENTA,
    }  # fmt: skip
    r = post(e, e.adm, f"pagos-por-aclarar/{o}/diferencia", datos)
    assert codigo(r) == (422, "sin_diferencia"), r.text


def test_con_la_facultad_no_queda_marcada(motor_migrate, e):
    from tests.test_pago_manual_0055 import emitida

    o = emitida(motor_migrate, e)
    d = con_diferencia(motor_migrate, e, o)
    assert fila_de(motor_migrate, d)["sin_facultad"] is False
