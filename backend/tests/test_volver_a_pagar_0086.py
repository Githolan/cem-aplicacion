"""Volver a pagar, contra PostgreSQL real (`0086`). Datos SINTETICOS / PRUEBA / NO REAL. Decisión 11
del propietario, 07-11-2026: es un desvío de §9 del rediseño."""

import pytest

from tests.test_circuito_pagos_0072 import declarada, ficha, paso
from tests.test_egresos_1a import conocido, umbral
from tests.test_lectura_comprobante_0077 import APROBAR, EMITIR, PREPARAR
from tests.test_orden_pago_neutra import huella, niega
from tests.test_pago_manual_0055 import CONFIRMAR, IMPORTE, ver
from tests.test_pago_manual_0055 import e as e  # noqa: F401  (es un fixture)
from tests.test_pague_otra_cosa_0075 import (
    MODIFICAR,
    RECUPERAR,
    con_diferencia,
    llamar,
    orden,
    resuelta,
)
from tests.test_rutas_egresos import post

pytestmark = [pytest.mark.db, pytest.mark.redis]
# ruff: noqa: F811


def bajo_el_umbral(motor_migrate, motor_app, e):
    """Emitida sin pasar por el Consejo: su franja no pide aprobación."""
    conocido(motor_migrate, e)
    umbral(motor_app, e.cons, IMPORTE * 3)
    o = llamar(motor_migrate, e, e.adm, PREPARAR)
    r = post(e, e.adm, f"ordenes-pago/{o}/enviar-a-revision", {"version_esperada": 1})
    assert r.status_code == 200, r.text
    assert orden(motor_migrate, o)["estado"] == "aprobada"  # control: la aprobó la franja
    llamar(motor_migrate, e, e.adm, EMITIR, orden=o, version=1)
    return o


def test_una_fallida_simple_sigue_por_su_franja(motor_migrate, motor_app, e):
    o = bajo_el_umbral(motor_migrate, motor_app, e)
    ver(motor_migrate, e, e.adm, o)
    d = declarada(motor_migrate, e, e.adm, o)
    llamar(motor_migrate, e, e.mc2, CONFIRMAR, orden=o, declaracion=d)
    assert orden(motor_migrate, o)["estado"] == "fallida"
    assert llamar(motor_migrate, e, e.adm, MODIFICAR, orden=o, version=1) == 2
    assert orden(motor_migrate, o)["estado"] == "aprobada"


def test_con_un_reclamo_recuperado_la_decide_el_consejo(motor_migrate, motor_app, e):
    o = bajo_el_umbral(motor_migrate, motor_app, e)
    d = con_diferencia(motor_migrate, e, o)
    resuelta(motor_migrate, e, o, d, "reclamo")
    llamar(motor_migrate, e, e.mc2, RECUPERAR, orden=o, diferencia=d)
    assert orden(motor_migrate, o)["estado"] == "fallida"
    assert llamar(motor_migrate, e, e.adm, MODIFICAR, orden=o, version=1) == 2
    assert orden(motor_migrate, o)["estado"] == "en_revision"
    assert paso(e, e.mc1, o) == "aprobar"
    # Sin aprobación no se ejecuta.
    niega(motor_migrate, e, e.adm, EMITIR, "estado_invalido", orden=o, version=2)
    # Control: el Consejo la aprueba y recién ahí se emite.
    llamar(motor_migrate, e, e.mc1, APROBAR, orden=o, version=2,
           huella=huella(motor_migrate, o, 2))  # fmt: skip
    assert orden(motor_migrate, o)["estado"] == "aprobada"
    llamar(motor_migrate, e, e.adm, EMITIR, orden=o, version=2)
    assert orden(motor_migrate, o)["estado"] == "en_ejecucion"


def test_con_un_reclamo_cerrado_tambien(motor_migrate, motor_app, e):
    o = bajo_el_umbral(motor_migrate, motor_app, e)
    d = con_diferencia(motor_migrate, e, o)
    resuelta(motor_migrate, e, o, d, "reclamo")
    r = post(e, e.mc2, f"ordenes-pago/{o}/pago-manual/diferencia/{d}/cierre",
             {"motivo": "SINTETICO: no vuelve"})  # fmt: skip
    assert r.status_code == 200, r.text
    assert llamar(motor_migrate, e, e.adm, MODIFICAR, orden=o, version=1) == 2
    assert orden(motor_migrate, o)["estado"] == "en_revision"
    assert ficha(e, e.mc1, o)["puede_aprobar"] is True


def test_modificar_otra_vez_no_saltea_al_consejo(motor_migrate, motor_app, e):
    """Después de la versión nueva, otra versión tampoco se aprueba sola."""
    o = bajo_el_umbral(motor_migrate, motor_app, e)
    d = con_diferencia(motor_migrate, e, o)
    resuelta(motor_migrate, e, o, d, "reclamo")
    llamar(motor_migrate, e, e.mc2, RECUPERAR, orden=o, diferencia=d)
    llamar(motor_migrate, e, e.adm, MODIFICAR, orden=o, version=1)
    otra = llamar(motor_migrate, e, e.adm, MODIFICAR, orden=o, version=2, concepto="SINTETICO: x")
    assert otra == 3
    assert orden(motor_migrate, o)["estado"] == "en_revision"
