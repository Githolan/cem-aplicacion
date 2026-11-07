"""Deshacer el cotejo, contra PostgreSQL real (`0083`). Datos SINTETICOS / PRUEBA / NO REAL: ninguna
cuenta es de un banco y no se mueve dinero. Decisión 7 del propietario, 07-11-2026."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db import transaccion
from tests.test_cotejo_0074 import (
    e,  # noqa: F401  (es un fixture)
    extracto,
    linea_del_pago,
    pagado,
    pendientes,
    subio,
    tildar,
)
from tests.test_orden_pago_neutra import cbu_sintetico
from tests.test_pago_manual_0055 import CONCILIAR, con_ejecutar, emitida, llamar, orden, registrada
from tests.test_pago_manual_0055 import segunda_apertura as apertura
from tests.test_rutas_egresos import codigo, get, post

pytestmark = pytest.mark.db
# ruff: noqa: F811

MOTIVO = {"motivo": "SINTETICO: era otro débito"}


def anular(e, quien, p, cuerpo=MOTIVO):
    return post(e, quien, f"pagos/cotejo/{p}/anulacion", cuerpo)


def filas(motor_migrate, p) -> list[dict]:
    with motor_migrate.connect() as c:
        return [dict(f) for f in c.execute(text(
            "select * from nucleo.cotejo_pago where coalesce(pago_id, diferencia_id) = :p"
            " order by cotejado_en"
        ), {"p": p}).mappings()]  # fmt: skip


def cotejados(e, quien) -> list[str]:
    return [x["pago_id"] for x in get(e, quien, "pagos/cotejo/cotejados").json()["elementos"]]


def test_anular_deja_la_fila_con_motivo_y_vuelve_a_lo_que_falta(motor_migrate, e):
    o, p = pagado(motor_migrate, e)
    assert tildar(e, e.mc1, p).status_code == 200
    assert cotejados(e, e.mc1) == [str(p)]  # control
    r = anular(e, e.mc1, p)
    assert r.status_code == 200, r.text
    [f] = filas(motor_migrate, p)
    assert (f["motivo_anulacion"], f["anulado_por"]) == (MOTIVO["motivo"], e.mc1)
    assert f["cotejado_por"] == e.mc1  # no se borró quién cotejó
    assert cotejados(e, e.mc1) == []
    assert [x["pago_id"] for x in pendientes(e, e.mc1)["elementos"]] == [str(p)]
    assert orden(motor_migrate, o)["estado"] == "pagada"  # no toca la orden


def test_sin_motivo_no_se_anula(motor_migrate, e):
    _, p = pagado(motor_migrate, e)
    tildar(e, e.mc1, p)
    assert anular(e, e.mc1, p, {"motivo": "  "}).status_code == 422
    assert filas(motor_migrate, p)[0]["anulado_en"] is None


def test_el_motivo_no_lleva_una_cuenta(motor_migrate, e):
    _, p = pagado(motor_migrate, e)
    tildar(e, e.mc1, p)
    r = anular(e, e.mc1, p, {"motivo": f"SINTETICO: {cbu_sintetico(3)}"})
    assert codigo(r) == (422, "motivo_con_cuenta"), r.text
    assert filas(motor_migrate, p)[0]["anulado_en"] is None


def test_las_mismas_separaciones_que_cotejar(motor_migrate, e):
    o = emitida(motor_migrate, e)
    con_ejecutar(motor_migrate, e, e.mc2)
    p = registrada(motor_migrate, e, o)
    apertura(motor_migrate, e, e.mc2, o)
    llamar(motor_migrate, e, e.mc1, CONCILIAR, orden=o, pago=p, resultado="conforme", motivo=None)
    assert tildar(e, e.mc1, p).status_code == 200
    assert codigo(anular(e, e.mc2, p)) == (403, "no_es_quien_ejecuto")  # vio la cuenta
    assert anular(e, e.adm, p).status_code == 403  # preparó y no aprueba
    [elemento] = get(e, e.mc2, "pagos/cotejo/cotejados").json()["elementos"]
    assert elemento["puede_cotejar"] is False  # la pantalla no lo ofrece
    assert filas(motor_migrate, p)[0]["anulado_en"] is None
    assert anular(e, e.mc1, p).status_code == 200  # control


def test_lo_que_no_esta_cotejado_no_se_anula(motor_migrate, e):
    _, p = pagado(motor_migrate, e)
    assert codigo(anular(e, e.mc1, p)) == (409, "orden_no_admite")
    assert filas(motor_migrate, p) == []


def test_un_pago_inventado_no_se_anula(motor_migrate, e):
    r = anular(e, e.mc1, "00000000-0000-7000-8000-000000000000")
    assert r.status_code == 404, r.text


def test_dos_veces_no_se_anula(motor_migrate, e):
    _, p = pagado(motor_migrate, e)
    tildar(e, e.mc1, p)
    assert anular(e, e.mc1, p).status_code == 200
    r = anular(e, e.mc1, p, {"motivo": "SINTETICO: otro motivo"})
    assert codigo(r) == (409, "orden_no_admite"), r.text
    assert filas(motor_migrate, p)[0]["motivo_anulacion"] == MOTIVO["motivo"]


def test_reimportar_no_lo_vuelve_a_tildar_solo(motor_migrate, e):
    _, p = pagado(motor_migrate, e)
    cuerpo = extracto(linea_del_pago(motor_migrate, p))
    assert subio(e, e.mc1, cuerpo)["cotejados"] == 1  # control: el extracto lo coteja
    assert anular(e, e.mc1, p).status_code == 200
    resultado = subio(e, e.mc1, cuerpo)
    assert (resultado["cotejados"], resultado["anulados"]) == (0, 1)
    assert resultado["ya_cotejados"] == 0
    assert len(filas(motor_migrate, p)) == 1
    assert cotejado_vigente(motor_migrate, p) is False


def cotejado_vigente(motor_migrate, p) -> bool:
    return any(f["anulado_en"] is None for f in filas(motor_migrate, p))


def test_la_base_tampoco_lo_vuelve_a_tildar_aunque_se_lo_pida(motor_migrate, motor_app, e):
    _, p = pagado(motor_migrate, e)
    cuerpo = extracto(linea_del_pago(motor_migrate, p))
    subio(e, e.mc1, cuerpo)
    assert anular(e, e.mc1, p).status_code == 200
    movimiento = filas(motor_migrate, p)[0]["movimiento_id"]
    with transaccion(motor_app, persona_id=e.mc1, consorcio_id=e.cons) as c:
        hechos = c.execute(
            text("select nucleo.cotejar_por_extracto(cast(:p as jsonb), :a, 'api', :s)"),
            {"p": f'[{{"movimiento": "{movimiento}", "pago": "{p}"}}]', "a": e.mc1,
             "s": e.sesion[e.mc1]},
        ).scalar_one()  # fmt: skip
    assert hechos == 0
    assert cotejado_vigente(motor_migrate, p) is False


def test_a_mano_se_vuelve_a_cotejar_con_una_fila_nueva(motor_migrate, e):
    _, p = pagado(motor_migrate, e)
    tildar(e, e.mc1, p)
    anular(e, e.mc1, p)
    assert tildar(e, e.mc1, p).status_code == 200
    vieja, nueva = filas(motor_migrate, p)
    assert vieja["anulado_en"] is not None and nueva["anulado_en"] is None
    # Con una vigente, no hay dos: tildar otra vez no agrega nada.
    assert tildar(e, e.mc1, p).json()["cotejados"] == 0
    assert len(filas(motor_migrate, p)) == 2


def test_la_anulacion_no_cambia_una_vez_hecha(motor_migrate, e):
    _, p = pagado(motor_migrate, e)
    tildar(e, e.mc1, p)
    anular(e, e.mc1, p)
    for sql in (
        "update nucleo.cotejo_pago set motivo_anulacion = 'otra' where pago_id = :p",
        "delete from nucleo.cotejo_pago where pago_id = :p",
    ):
        with pytest.raises(DBAPIError, match="solo se agrega"), motor_migrate.begin() as c:
            c.execute(text(sql), {"p": p})


def test_una_fila_vigente_solo_acepta_la_anulacion(motor_migrate, e):
    _, p = pagado(motor_migrate, e)
    tildar(e, e.mc1, p)
    for sql in (
        "update nucleo.cotejo_pago set origen = 'extracto' where pago_id = :p",
        "delete from nucleo.cotejo_pago where pago_id = :p",
    ):
        with pytest.raises(DBAPIError, match="solo se agrega"), motor_migrate.begin() as c:
            c.execute(text(sql), {"p": p})


def test_dos_cotejos_vigentes_de_un_pago_los_niega_la_base(motor_migrate, e):
    _, p = pagado(motor_migrate, e)
    tildar(e, e.mc1, p)
    with pytest.raises(DBAPIError, match="cotejo_vigente_de_pago"), motor_migrate.begin() as c:
        c.execute(text(
            "insert into nucleo.cotejo_pago"
            " (consorcio_id, pago_id, origen, cotejado_por, identidad_cotejo)"
            " select consorcio_id, pago_id, origen, cotejado_por, identidad_cotejo"
            " from nucleo.cotejo_pago where pago_id = :p"
        ), {"p": p})  # fmt: skip


def test_quien_no_concilia_no_lee_lo_cotejado(motor_migrate, e):
    _, p = pagado(motor_migrate, e)
    tildar(e, e.mc1, p)
    assert get(e, e.adm, "pagos/cotejo/cotejados").status_code == 403
