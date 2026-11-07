"""La moneda del extracto, contra PostgreSQL real (`0085`). Datos SINTETICOS / PRUEBA / NO REAL.
Decisión 9 del propietario, 07-11-2026: un pago de USD 1.000 no coincide con un débito de ARS 1.000.
"""

import pytest
from sqlalchemy import text

from app.db import transaccion
from tests.test_cotejo_0074 import (
    COLUMNAS,
    cotejo,
    e,  # noqa: F401  (es un fixture)
    extracto,
    linea_del_pago,
    pagado,
    pendientes,
    subio,
    subir,
)
from tests.test_rutas_egresos import codigo

pytestmark = pytest.mark.db
# ruff: noqa: F811

EN_DOLARES = COLUMNAS | {"moneda": "USD"}


def en_dolares(motor_migrate, e):
    """Un pago conforme en dólares. La escena solo tiene umbral en pesos: se corrige la fila."""
    o, p = pagado(motor_migrate, e)
    with motor_migrate.begin() as c:
        c.execute(text("alter table nucleo.pago_manual disable trigger user"))
        c.execute(text("update nucleo.pago_manual set moneda = 'USD' where id = :p"), {"p": p})
        c.execute(text("alter table nucleo.pago_manual enable trigger user"))
    return o, p


def columnas_guardadas(motor_migrate, cons) -> dict:
    with motor_migrate.connect() as c:
        return dict(c.execute(
            text("select * from nucleo.columnas_extracto where consorcio_id = :c"), {"c": cons}
        ).mappings().one())  # fmt: skip


def test_un_pago_en_dolares_no_coincide_con_un_debito_en_pesos(motor_migrate, e):
    _, p = en_dolares(motor_migrate, e)
    cuerpo = extracto(linea_del_pago(motor_migrate, p))
    r = subio(e, e.mc1, cuerpo)  # sin elegir: la sugerida, ARS
    assert r["cotejados"] == 0
    assert cotejo(motor_migrate, p) is None
    assert r["total_banco_no_aca"] == 1 and r["banco_no_aca"][0]["moneda"] == "ARS"
    assert r["total_aca_no_banco"] == 1


def test_control_el_mismo_debito_en_su_moneda_coincide(motor_migrate, e):
    _, p = en_dolares(motor_migrate, e)
    cuerpo = extracto(linea_del_pago(motor_migrate, p))
    assert subio(e, e.mc1, cuerpo, EN_DOLARES)["cotejados"] == 1
    assert cotejo(motor_migrate, p) is not None


def test_sin_numero_tampoco_coincide_por_importe_y_fecha(motor_migrate, e):
    _, p = en_dolares(motor_migrate, e)
    cuerpo = extracto(linea_del_pago(motor_migrate, p, referencia=""))
    assert subio(e, e.mc1, cuerpo)["cotejados"] == 0
    assert cotejo(motor_migrate, p) is None


def test_un_pago_en_pesos_no_coincide_con_un_extracto_en_dolares(motor_migrate, e):
    _, p = pagado(motor_migrate, e)
    cuerpo = extracto(linea_del_pago(motor_migrate, p))
    assert subio(e, e.mc1, cuerpo, EN_DOLARES)["cotejados"] == 0
    assert subio(e, e.mc1, cuerpo)["cotejados"] == 1  # control: en pesos sí


def test_queda_guardada_con_las_columnas_y_se_cambia_al_subir(motor_migrate, e):
    _, p = pagado(motor_migrate, e)
    cuerpo = extracto(linea_del_pago(motor_migrate, p))
    subio(e, e.mc1, cuerpo, EN_DOLARES)
    assert columnas_guardadas(motor_migrate, e.cons)["moneda"] == "USD"
    assert pendientes(e, e.mc1)["moneda_extracto"] == "USD"
    # Con las columnas guardadas, cambiar solo la moneda.
    assert subio(e, e.mc1, cuerpo, {"moneda": "ARS"})["cotejados"] == 1
    assert columnas_guardadas(motor_migrate, e.cons)["moneda"] == "ARS"


def test_sin_columnas_sugiere_pesos(motor_migrate, e):
    assert pendientes(e, e.mc1)["moneda_extracto"] == "ARS"


def test_una_moneda_mal_escrita_no_se_acepta(motor_migrate, e):
    _, p = pagado(motor_migrate, e)
    r = subir(e, e.mc1, extracto(linea_del_pago(motor_migrate, p)), COLUMNAS | {"moneda": "pesos"})
    assert codigo(r) == (422, "peticion_invalida"), r.text


def test_la_base_no_acepta_un_par_en_otra_moneda(motor_migrate, motor_app, e):
    """Aunque la API lo pida: `cotejar_por_extracto` vuelve a mirar."""
    _, p = en_dolares(motor_migrate, e)
    subio(e, e.mc1, extracto(linea_del_pago(motor_migrate, p)))
    with motor_migrate.connect() as c:
        movimiento = c.execute(
            text("select id from nucleo.movimiento_extracto where consorcio_id = :c"),
            {"c": e.cons},
        ).scalar_one()
    with (
        pytest.raises(Exception, match="no coincide"),
        transaccion(motor_app, persona_id=e.mc1, consorcio_id=e.cons) as c,
    ):
        c.execute(
            text("select nucleo.cotejar_por_extracto(cast(:p as jsonb), :a, 'api', :s)"),
            {"p": f'[{{"movimiento": "{movimiento}", "pago": "{p}"}}]', "a": e.mc1,
             "s": e.sesion[e.mc1]},
        )  # fmt: skip
    assert cotejo(motor_migrate, p) is None


def test_el_mismo_archivo_en_otra_moneda_son_otras_lineas(motor_migrate, e):
    _, p = pagado(motor_migrate, e)
    cuerpo = extracto(linea_del_pago(motor_migrate, p))
    assert subio(e, e.mc1, cuerpo, EN_DOLARES)["movimientos_nuevos"] == 1
    assert subio(e, e.mc1, cuerpo, EN_DOLARES)["movimientos_nuevos"] == 0  # control: no duplica
    assert subio(e, e.mc1, cuerpo)["movimientos_nuevos"] == 1
