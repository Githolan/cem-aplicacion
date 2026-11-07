"""Corregir una diferencia mal cargada, contra PostgreSQL real (`0082`). Datos SINTETICOS / PRUEBA /
NO REAL: ninguna cuenta es de un banco (prefijo 999) y no se mueve dinero. Decisión 6 del
propietario, 07-11-2026."""

from decimal import Decimal

import pytest
from sqlalchemy import text

from tests.test_circuito_pagos_0072 import ficha, segundo_ejecutor
from tests.test_cotejo_0074 import pendientes
from tests.test_orden_pago_neutra import huella
from tests.test_pago_manual_0055 import CUENTA, IMPORTE, emitida
from tests.test_pago_manual_0055 import e as e  # noqa: F401  (es un fixture)
from tests.test_pague_otra_cosa_0075 import (
    REAL_DE_MENOS,
    con_diferencia,
    fila_de,
    orden,
    resuelta,
)
from tests.test_rutas_egresos import codigo, post

pytestmark = [pytest.mark.db, pytest.mark.redis]
# ruff: noqa: F811

OTRO_REAL = Decimal(IMPORTE - 900) / 100


def correccion(motor_migrate, e, o, d, **cambios) -> dict:
    f = fila_de(motor_migrate, d)
    return {
        "version": 1, "huella": huella(motor_migrate, o, 1).hex(),
        "numero_operacion": f["numero_operacion"], "fecha_operacion": str(f["fecha_operacion"]),
        "importe_real": str(OTRO_REAL), "moneda": "ARS", "cuenta_real": CUENTA,
        "documento_id": str(f["documento_id"]), "explicacion": "SINTETICO: lo escribí mal",
    } | cambios  # fmt: skip


def corregir(motor_migrate, e, quien, o, d, **cambios):
    return post(e, quien, f"ordenes-pago/{o}/pago-manual/diferencia/{d}/correccion",
                correccion(motor_migrate, e, o, d, **cambios))  # fmt: skip


def filas(motor_migrate, o) -> int:
    with motor_migrate.connect() as c:
        return c.execute(text("select count(*) from nucleo.diferencia_pago_manual"
                              " where orden_id = :o"), {"o": o}).scalar_one()  # fmt: skip


def test_corrige_quien_la_registro_con_una_fila_nueva(motor_migrate, e):
    o = emitida(motor_migrate, e)
    d = con_diferencia(motor_migrate, e, o)
    assert ficha(e, e.adm, o)["puede_corregir_diferencia"] is True
    r = corregir(motor_migrate, e, e.adm, o, d)
    assert r.status_code == 201, r.text
    nueva = r.json()["id"]
    assert fila_de(motor_migrate, nueva)["corrige_a"] == d
    assert fila_de(motor_migrate, d)["importe_real"] == REAL_DE_MENOS  # la anterior no cambió
    f = ficha(e, e.mc2, o)
    assert f["diferencia_vigente"]["id"] == nueva
    assert [x["id"] for x in f["diferencias_anteriores"]] == [str(d)]
    assert f["situacion"] == "con_diferencia"


def test_otra_persona_no_corrige(motor_migrate, motor_app, e):
    o = emitida(motor_migrate, e)
    d = con_diferencia(motor_migrate, e, o)
    x = segundo_ejecutor(motor_migrate, motor_app, e)
    r = corregir(motor_migrate, e, x, o, d)
    # Antes de quién registró, la base pide haber visto la cuenta, como al registrar.
    assert r.status_code == 403, r.text
    assert filas(motor_migrate, o) == 1
    assert ficha(e, x, o)["puede_corregir_diferencia"] is False
    assert ficha(e, e.mc2, o)["puede_corregir_diferencia"] is False


def test_quien_vio_la_cuenta_y_no_registro_tampoco(motor_migrate, motor_app, e):
    from tests.test_circuito_pagos_0072 import segunda_apertura

    o = emitida(motor_migrate, e)
    d = con_diferencia(motor_migrate, e, o)
    x = segundo_ejecutor(motor_migrate, motor_app, e)
    segunda_apertura(motor_migrate, e, x, o)
    assert codigo(corregir(motor_migrate, e, x, o, d)) == (403, "no_es_quien_registro")
    assert filas(motor_migrate, o) == 1


def test_despues_de_la_resolucion_no_se_corrige(motor_migrate, e):
    o = emitida(motor_migrate, e)
    d = con_diferencia(motor_migrate, e, o)
    resuelta(motor_migrate, e, o, d, "reclamo")
    assert ficha(e, e.adm, o)["puede_corregir_diferencia"] is False
    assert codigo(corregir(motor_migrate, e, e.adm, o, d)) == (409, "diferencia_ya_resuelta")
    assert filas(motor_migrate, o) == 1


def test_la_corregida_no_se_resuelve_ni_se_vuelve_a_corregir(motor_migrate, e):
    o = emitida(motor_migrate, e)
    d = con_diferencia(motor_migrate, e, o)
    nueva = corregir(motor_migrate, e, e.adm, o, d).json()["id"]
    base = f"ordenes-pago/{o}/pago-manual/diferencia"
    r = post(e, e.mc2, f"{base}/{d}/resolucion", {"resolucion": "reclamo"})
    assert codigo(r) == (404, "no_encontrado"), r.text
    assert codigo(corregir(motor_migrate, e, e.adm, o, d)) == (404, "no_encontrado")
    assert post(e, e.mc2, f"{base}/{nueva}/resolucion",
                {"resolucion": "pagada_por_lo_real"}).status_code == 200  # control  # fmt: skip
    assert orden(motor_migrate, o)["estado"] == "pagada"


def test_sigue_pidiendo_que_haya_diferencia(motor_migrate, e):
    o = emitida(motor_migrate, e)
    d = con_diferencia(motor_migrate, e, o)
    r = corregir(motor_migrate, e, e.adm, o, d, importe_real=f"{IMPORTE / 100:.2f}")
    assert codigo(r) == (422, "sin_diferencia"), r.text
    assert filas(motor_migrate, o) == 1


def test_la_liquidacion_y_el_cotejo_toman_la_ultima(motor_migrate, e):
    o = emitida(motor_migrate, e)
    d = con_diferencia(motor_migrate, e, o)
    nueva = corregir(motor_migrate, e, e.adm, o, d).json()["id"]
    resuelta(motor_migrate, e, o, nueva, "pagada_por_lo_real")
    assert [x["pago_id"] for x in pendientes(e, e.mc1)["elementos"]] == [nueva]
    with motor_migrate.connect() as c:
        pagado = c.execute(text(
            "select nucleo.importe_pagado_ejecucion(:c, f.ejecucion_id)"
            " from nucleo.diferencia_pago_manual f where f.id = :d"
        ), {"c": e.cons, "d": d}).scalar_one()  # fmt: skip
    assert pagado == int(OTRO_REAL * 100)


def test_una_ejecucion_no_tiene_dos_diferencias_sin_corrige_a(motor_migrate, e):
    """Sin pasar por la función, con el dueño de la tabla: lo niega la base."""
    from sqlalchemy.exc import DBAPIError

    o = emitida(motor_migrate, e)
    d = con_diferencia(motor_migrate, e, o)
    with pytest.raises(DBAPIError, match="diferencia_una_por"), motor_migrate.begin() as c:
        c.execute(text(
            "insert into nucleo.diferencia_pago_manual (consorcio_id, orden_id, ejecucion_id, tipo,"
            " importe_real, moneda, cuenta_real, numero_operacion, fecha_operacion, documento_id,"
            " version_documento_id, explicacion, fuera_de_termino, registrada_por,"
            " identidad_registro)"
            " select consorcio_id, orden_id, ejecucion_id, tipo, importe_real, moneda, cuenta_real,"
            " numero_operacion, fecha_operacion, documento_id, version_documento_id, explicacion,"
            " fuera_de_termino, registrada_por, identidad_registro"
            " from nucleo.diferencia_pago_manual where id = :d"
        ), {"d": d})  # fmt: skip
