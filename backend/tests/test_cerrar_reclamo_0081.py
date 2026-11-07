"""«Cerrar sin recuperar», contra PostgreSQL real (`0081`). Datos SINTETICOS / PRUEBA / NO REAL:
ninguna cuenta es de un banco (prefijo 999) y no se mueve dinero. Decisión 5 del propietario,
07-11-2026."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.test_circuito_pagos_0072 import ficha, segundo_ejecutor
from tests.test_orden_pago_neutra import cbu_sintetico
from tests.test_pago_manual_0055 import e as e  # noqa: F401  (es un fixture)
from tests.test_pago_manual_0055 import emitida, ver
from tests.test_pague_otra_cosa_0075 import (
    DIFERENCIA,
    con_diferencia,
    datos,
    fila_de,
    llamar,
    orden,
    resuelta,
)
from tests.test_rutas_egresos import codigo, get, post

pytestmark = [pytest.mark.db, pytest.mark.redis]
# ruff: noqa: F811

MOTIVO = {"motivo": "SINTETICO: el banco no devuelve la plata"}


def reclamada(motor_migrate, e):
    o = emitida(motor_migrate, e)
    d = con_diferencia(motor_migrate, e, o)
    resuelta(motor_migrate, e, o, d, "reclamo")
    return o, d


def cierre(e, quien, o, d, cuerpo=MOTIVO):
    return post(e, quien, f"ordenes-pago/{o}/pago-manual/diferencia/{d}/cierre", cuerpo)


def test_cerrar_deja_la_orden_fallida_y_la_diferencia_en_la_historia(motor_migrate, e):
    o, d = reclamada(motor_migrate, e)
    r = cierre(e, e.mc2, o, d)
    assert r.status_code == 200, r.text
    assert r.json()["estado"] == "fallida"
    assert orden(motor_migrate, o)["estado"] == "fallida"
    f = fila_de(motor_migrate, d)
    assert (f["motivo_cierre"], f["cerrada_por"], f["recuperada_en"]) == (
        MOTIVO["motivo"], e.mc2, None,
    )  # fmt: skip
    vista = ficha(e, e.mc1, o)
    assert vista["situacion"] == "no_se_pago"
    assert vista["diferencia_vigente"]["motivo_cierre"] == MOTIVO["motivo"]


def test_no_se_imputa_en_la_liquidacion(motor_migrate, e):
    o, d = reclamada(motor_migrate, e)
    assert cierre(e, e.mc2, o, d).status_code == 200
    with motor_migrate.connect() as c:
        pagado = c.execute(text(
            "select nucleo.importe_pagado_ejecucion(:c, f.ejecucion_id), e.importe_centavos,"
            " nucleo.centavos_de(f.importe_real)"
            " from nucleo.diferencia_pago_manual f"
            " join nucleo.ejecucion_orden_pago e on e.id = f.ejecucion_id where f.id = :d"
        ), {"c": e.cons, "d": d}).one()  # fmt: skip
    assert pagado[0] == pagado[1] != pagado[2]  # no toma lo real
    assert orden(motor_migrate, o)["estado"] == "fallida"  # y una fallida no es liquidable


def test_sin_motivo_no_se_cierra(motor_migrate, e):
    o, d = reclamada(motor_migrate, e)
    assert cierre(e, e.mc2, o, d, {"motivo": "  "}).status_code == 422
    assert fila_de(motor_migrate, d)["cerrada_en"] is None


def test_el_motivo_no_lleva_una_cuenta(motor_migrate, e):
    o, d = reclamada(motor_migrate, e)
    r = cierre(e, e.mc2, o, d, {"motivo": f"SINTETICO: {cbu_sintetico(3)}"})
    assert codigo(r) == (422, "motivo_con_cuenta"), r.text
    assert orden(motor_migrate, o)["estado"] == "en_verificacion"


def test_la_separacion_de_conciliar(motor_migrate, motor_app, e):
    o = emitida(motor_migrate, e)
    x = segundo_ejecutor(motor_migrate, motor_app, e)
    ver(motor_migrate, e, x, o)
    d = llamar(motor_migrate, e, x, DIFERENCIA, **datos(motor_migrate, e, o))
    resuelta(motor_migrate, e, o, d, "reclamo")
    assert cierre(e, e.adm, o, d).status_code == 403  # preparó y no aprueba
    assert cierre(e, x, o, d).status_code == 403  # vio la cuenta
    assert orden(motor_migrate, o)["estado"] == "en_verificacion"
    assert cierre(e, e.mc2, o, d).status_code == 200  # control


def test_sin_reclamo_no_se_cierra(motor_migrate, e):
    o = emitida(motor_migrate, e)
    d = con_diferencia(motor_migrate, e, o)
    assert codigo(cierre(e, e.mc2, o, d)) == (409, "orden_no_admite")
    assert fila_de(motor_migrate, d)["cerrada_en"] is None


def test_cerrado_no_se_recupera_ni_se_cierra_de_nuevo(motor_migrate, e):
    o, d = reclamada(motor_migrate, e)
    assert cierre(e, e.mc2, o, d).status_code == 200
    base = f"ordenes-pago/{o}/pago-manual/diferencia/{d}"
    assert post(e, e.mc2, f"{base}/recuperada", {}).status_code == 409
    assert cierre(e, e.mc1, o, d).status_code == 409
    assert fila_de(motor_migrate, d)["cerrada_por"] == e.mc2


def test_el_cierre_no_cambia_una_vez_hecho(motor_migrate, e):
    o, d = reclamada(motor_migrate, e)
    assert cierre(e, e.mc2, o, d).status_code == 200
    with pytest.raises(DBAPIError, match="historia"), motor_migrate.begin() as c:
        c.execute(text("update nucleo.diferencia_pago_manual set motivo_cierre = 'otra'"
                       " where id = :d"), {"d": d})  # fmt: skip


def test_recuperada_no_se_puede_marcar_cerrada_sin_pasar_por_la_funcion(motor_migrate, e):
    o, d = reclamada(motor_migrate, e)
    r = post(e, e.mc2, f"ordenes-pago/{o}/pago-manual/diferencia/{d}/recuperada", {})
    assert r.status_code == 200, r.text
    with pytest.raises(DBAPIError, match="diferencia_cierre_completo"), motor_migrate.begin() as c:
        c.execute(text(
            "update nucleo.diferencia_pago_manual set cerrada_por = :q,"
            " identidad_cerro = identidad_resolvio, cerrada_en = now(), motivo_cierre = 'x'"
            " where id = :d"
        ), {"q": e.mc2, "d": d})  # fmt: skip


def test_quien_prepara_lee_la_ficha_sin_la_cuenta(motor_migrate, e):
    o, d = reclamada(motor_migrate, e)
    assert cierre(e, e.mc2, o, d).status_code == 200
    assert get(e, e.adm, f"pagos/{o}").status_code == 200
