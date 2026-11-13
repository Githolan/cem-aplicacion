"""Un solo pagador y «Tomar el pago» contra PostgreSQL real (`0078`). Datos SINTETICOS / PRUEBA / NO
REAL: ninguna cuenta es de un banco (prefijo 999) y no se mueve dinero.

Decisión 1 del propietario, 07-11-2026. La escena es la de `test_pago_manual_0055`.
"""

from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.config import cargar
from app.db import transaccion
from tests.test_circuito_pagos_0072 import (
    declarada,
    e,  # noqa: F401  (es un fixture)
    ficha,
    fila,
    paso,
    segundo_ejecutor,
)
from tests.test_cotejo_0074 import tildar
from tests.test_egresos_1a import envejecer
from tests.test_orden_pago_neutra import huella
from tests.test_pago_manual_0055 import (
    CONCILIAR,
    REGISTRAR,
    acta,
    emitida,
    evidencia,
    llamar,
    niega,
    otorgar,
    otra_cuenta_en_el_consejo,
    ver,
)
from tests.test_pague_otra_cosa_0075 import DIFERENCIA
from tests.test_pague_otra_cosa_0075 import datos as datos_diferencia
from tests.test_rutas_egresos import cliente, codigo, get, post, url

pytestmark = pytest.mark.db
# ruff: noqa: F811


def viejo(motor_migrate, o, horas=25) -> None:
    """Corre para atrás la apertura. Solo se puede en esta base: la historia no cambia."""
    with motor_migrate.begin() as c:
        c.execute(text("alter table nucleo.apertura_cuenta_pago_manual disable trigger user"))
        c.execute(text(
            "update nucleo.apertura_cuenta_pago_manual a"
            "   set abierta_en = abierta_en - make_interval(hours => :h)"
            "  from nucleo.ejecucion_orden_pago x where x.id = a.ejecucion_id and x.orden_id = :o"
        ), {"h": horas, "o": o})  # fmt: skip
        c.execute(text("alter table nucleo.apertura_cuenta_pago_manual enable trigger user"))


def cuerpo(motor_migrate, o) -> dict:
    return {"version": 1, "huella": huella(motor_migrate, o, 1).hex(), "no_se_transfirio": True}


def tomar(motor_migrate, e, quien, o):
    return post(e, quien, f"ordenes-pago/{o}/pago-manual/tomar", cuerpo(motor_migrate, o))


def cuenta_por_ruta(motor_migrate, e, quien, o):
    datos = cuerpo(motor_migrate, o)
    del datos["no_se_transfirio"]
    return post(e, quien, f"ordenes-pago/{o}/pago-manual/cuenta", datos)


def aperturas(motor_migrate, o) -> int:
    with motor_migrate.connect() as c:
        return c.execute(text(
            "select count(*) from nucleo.apertura_cuenta_pago_manual a"
            "  join nucleo.ejecucion_orden_pago x on x.id = a.ejecucion_id where x.orden_id = :o"
        ), {"o": o}).scalar_one()  # fmt: skip


def test_la_base_no_le_da_la_cuenta_a_un_segundo_ejecutor(motor_migrate, motor_app, e):
    x = segundo_ejecutor(motor_migrate, motor_app, e)
    o = emitida(motor_migrate, e)
    ver(motor_migrate, e, e.adm, o)
    r = cuenta_por_ruta(motor_migrate, e, x, o)
    assert codigo(r) == (409, "pago_en_curso"), r.text
    assert "cuenta_destino" not in r.text
    assert aperturas(motor_migrate, o) == 1
    # Control: quien la vio la vuelve a pedir.
    assert cuenta_por_ruta(motor_migrate, e, e.adm, o).status_code == 200


def test_si_quien_vio_la_cuenta_declaro_otro_puede_retomar(motor_migrate, motor_app, e):
    x = segundo_ejecutor(motor_migrate, motor_app, e)
    o = emitida(motor_migrate, e)
    ver(motor_migrate, e, e.adm, o)
    declarada(motor_migrate, e, e.adm, o)
    assert cuenta_por_ruta(motor_migrate, e, x, o).status_code == 200


def test_antes_del_plazo_no_se_toma(motor_migrate, motor_app, e):
    x = segundo_ejecutor(motor_migrate, motor_app, e)
    o = emitida(motor_migrate, e)
    ver(motor_migrate, e, e.adm, o)
    assert paso(e, x, o) == "espera_comprobante"
    f = ficha(e, x, o)
    assert (f["puede_tomar"], f["toma_vigente"]) == (False, None)
    assert f["tomar_desde"] is not None
    assert codigo(tomar(motor_migrate, e, x, o)) == (409, "toma_antes_de_tiempo")
    # Control: una hora antes del plazo sigue sin poder; pasado el plazo, sí.
    viejo(motor_migrate, o, horas=23)
    assert codigo(tomar(motor_migrate, e, x, o)) == (409, "toma_antes_de_tiempo")
    viejo(motor_migrate, o, horas=2)
    assert paso(e, x, o) == "tomar_pago"
    assert tomar(motor_migrate, e, x, o).status_code == 201


def test_el_plazo_sale_de_la_configuracion(motor_migrate, motor_app, e):
    x = segundo_ejecutor(motor_migrate, motor_app, e)
    o = emitida(motor_migrate, e)
    ver(motor_migrate, e, e.adm, o)
    viejo(motor_migrate, o, horas=2)
    assert paso(e, x, o) == "espera_comprobante"  # control: con las 24 horas
    with cliente(x, e.sesion[x]) as c:
        c.app.state.config = replace(c.app.state.config, pagos_horas_tomar=1)
        r = c.get(url(e, "pagos/bandeja"))
    assert [f["paso"] for f in r.json()["elementos"]] == ["tomar_pago"]


def test_un_plazo_mal_escrito_impide_el_arranque(monkeypatch):
    monkeypatch.setenv("CEM_PAGOS_HORAS_TOMAR", "0")
    with pytest.raises(ValueError, match="CEM_PAGOS_HORAS_TOMAR"):
        cargar()
    monkeypatch.setenv("CEM_PAGOS_HORAS_TOMAR", "6")
    assert cargar().pagos_horas_tomar == 6  # control


def test_tomar_deja_el_evento_y_suelta_a_quien_vio_la_cuenta(motor_migrate, motor_app, e):
    x = segundo_ejecutor(motor_migrate, motor_app, e)
    o = emitida(motor_migrate, e)
    ver(motor_migrate, e, e.adm, o)
    viejo(motor_migrate, o)
    assert tomar(motor_migrate, e, x, o).status_code == 201
    with motor_migrate.connect() as c:
        hechos = c.execute(text(
            "select count(*) from auditoria.eventos"
            " where accion = 'consorcio.orden_pago.tomar_pago_manual' and entidad_id = :o"
        ), {"o": o}).scalar_one()  # fmt: skip
    assert hechos == 1
    assert aperturas(motor_migrate, o) == 1  # tomar no da la cuenta
    assert paso(e, x, o) == "pagar"
    assert ficha(e, x, o)["toma_vigente"]["tomado_por_nombre"]
    assert cuenta_por_ruta(motor_migrate, e, x, o).status_code == 200
    # Control: ahora el que espera es quien la vio antes.
    assert codigo(cuenta_por_ruta(motor_migrate, e, e.adm, o)) == (409, "pago_en_curso")


def test_quien_vuelve_con_el_comprobante_todavia_registra(motor_migrate, motor_app, e):
    x = segundo_ejecutor(motor_migrate, motor_app, e)
    o = emitida(motor_migrate, e)
    ver(motor_migrate, e, e.adm, o)
    viejo(motor_migrate, o)
    assert tomar(motor_migrate, e, x, o).status_code == 201
    assert ficha(e, e.adm, o)["puede_registrar"] is True


def test_con_la_aprobacion_vencida_quien_tomo_declara_y_el_consejo_confirma(
    motor_migrate, motor_app, e
):
    x = segundo_ejecutor(motor_migrate, motor_app, e)
    o = emitida(motor_migrate, e)
    ver(motor_migrate, e, e.adm, o)
    viejo(motor_migrate, o)
    envejecer(motor_migrate, o, timedelta(days=8))
    assert paso(e, x, o) == "tomar_pago"
    # Control: antes de tomarlo, declarar se le niega.
    r = post(e, x, f"ordenes-pago/{o}/pago-manual/no-realizado",
             {"version": 1, "motivo": "SINTETICO: no salió"})  # fmt: skip
    assert codigo(r) == (403, "no_es_quien_ejecuto"), r.text
    assert tomar(motor_migrate, e, x, o).status_code == 201
    assert paso(e, x, o) == "declarar_no_pago"
    d = declarada(motor_migrate, e, x, o)
    r = post(e, e.mc2, f"ordenes-pago/{o}/pago-manual/no-realizado/{d}/confirmacion", {})
    assert r.status_code == 200, r.text


def test_quien_aprobo_no_toma_y_sin_el_ejecutor_no_hay_fila(motor_migrate, e):
    o = emitida(motor_migrate, e)
    ver(motor_migrate, e, e.adm, o)
    viejo(motor_migrate, o)
    assert paso(e, e.mc1, o) is None
    assert tomar(motor_migrate, e, e.mc1, o).status_code == 403


def test_sin_confirmar_que_no_se_transfirio_no_se_toma(motor_migrate, motor_app, e):
    x = segundo_ejecutor(motor_migrate, motor_app, e)
    o = emitida(motor_migrate, e)
    ver(motor_migrate, e, e.adm, o)
    viejo(motor_migrate, o)
    datos = cuerpo(motor_migrate, o) | {"no_se_transfirio": False}
    assert post(e, x, f"ordenes-pago/{o}/pago-manual/tomar", datos).status_code == 422
    assert fila(e, x, o)["paso"] == "tomar_pago"


def test_con_un_registro_observado_no_se_toma(motor_migrate, motor_app, e):
    from tests.test_circuito_pagos_0072 import observada
    from tests.test_pago_manual_0055 import registrada

    x = segundo_ejecutor(motor_migrate, motor_app, e)
    o = emitida(motor_migrate, e)
    p = registrada(motor_migrate, e, o)
    observada(motor_migrate, e, o, p)
    viejo(motor_migrate, o)
    assert codigo(tomar(motor_migrate, e, x, o)) == (409, "orden_no_admite")
    assert ficha(e, x, o)["puede_tomar"] is False


def test_la_toma_no_se_borra_ni_se_cambia(motor_migrate, motor_app, e):
    x = segundo_ejecutor(motor_migrate, motor_app, e)
    o = emitida(motor_migrate, e)
    ver(motor_migrate, e, e.adm, o)
    viejo(motor_migrate, o)
    assert tomar(motor_migrate, e, x, o).status_code == 201
    with pytest.raises(DBAPIError, match="historia"), motor_migrate.begin() as c:
        c.execute(text("delete from nucleo.toma_pago_manual"))


def test_rls_otro_consorcio_no_ve_la_toma(motor_migrate, motor_app, e):
    x = segundo_ejecutor(motor_migrate, motor_app, e)
    o = emitida(motor_migrate, e)
    ver(motor_migrate, e, e.adm, o)
    viejo(motor_migrate, o)
    assert tomar(motor_migrate, e, x, o).status_code == 201
    sql = text("select count(*) from nucleo.toma_pago_manual")
    with motor_migrate.connect() as c:
        rls = c.execute(text(
            "select relrowsecurity, relforcerowsecurity from pg_class"
            " where oid = 'nucleo.toma_pago_manual'::regclass"
        )).one()  # fmt: skip
        puede_leer = c.execute(text(
            "select has_table_privilege('cem_app', 'nucleo.toma_pago_manual', 'select')"
        )).scalar_one()  # fmt: skip
    assert tuple(rls) == (True, True)
    assert not puede_leer
    # Con el permiso dado solo para esta prueba: la política recorta por el consorcio del ámbito.
    with motor_migrate.begin() as c:
        c.execute(text("grant select on nucleo.toma_pago_manual to cem_app"))
    try:
        with transaccion(motor_app, persona_id=e.adm, consorcio_id=e.cons) as c:
            propias = c.execute(sql).scalar_one()
        with transaccion(motor_app, persona_id=e.adm, consorcio_id=uuid4()) as c:
            ajenas = c.execute(sql).scalar_one()
    finally:
        with motor_migrate.begin() as c:
            c.execute(text("revoke select on nucleo.toma_pago_manual from cem_app"))
    assert propias == 1  # control: la consulta ve la fila desde su consorcio
    assert ajenas == 0


# Quien tomó ocupa el lugar de quien ejecutó ------------------------------------------------------


def puede_tomar(motor_migrate, e, persona) -> None:
    with motor_migrate.connect() as c:
        m = c.execute(
            text("select id from nucleo.mandato where consorcio_id = :c and persona_id = :p"),
            {"c": e.cons, "p": persona},
        ).scalar_one()
    a = acta(motor_migrate, e)
    for facultad in ("egresos.ejecutar_pago", "egresos.preparar_orden"):
        otorgar(motor_migrate, e.cons, m, a, facultad)


def tomada_por_un_consejero(motor_migrate, e):
    """`mc2` toma el pago sin abrir la cuenta y vuelve quien la vio: `adm` lo registra."""
    puede_tomar(motor_migrate, e, e.mc2)
    o = emitida(motor_migrate, e)
    ver(motor_migrate, e, e.adm, o)
    viejo(motor_migrate, o)
    assert tomar(motor_migrate, e, e.mc2, o).status_code == 201
    assert aperturas(motor_migrate, o) == 1  # no abrió la cuenta
    return o


def por_conciliar(motor_migrate, e):
    o = tomada_por_un_consejero(motor_migrate, e)
    return o, llamar(motor_migrate, e, e.adm, REGISTRAR, **evidencia(motor_migrate, e, o))


def conciliacion(e, quien, o, p):
    camino = f"ordenes-pago/{o}/pago-manual/{p}/conciliacion"
    return post(e, quien, camino, {"resultado": "conforme"})


def ofrece_conciliar(e, quien, o) -> bool:
    [elemento] = [
        x for x in get(e, quien, "pagos-manuales/revision").json()["elementos"]
        if x["orden"]["id"] == str(o)
    ]  # fmt: skip
    return elemento["puede_conciliar"]


def test_quien_tomo_no_concilia_lo_que_registro_el_pagador(motor_migrate, e):
    o, p = por_conciliar(motor_migrate, e)
    datos = {"orden": o, "pago": p, "resultado": "conforme", "motivo": None}
    niega(motor_migrate, e, e.mc2, CONCILIAR, "no_es_quien_ejecuto", **datos)  # la base
    assert codigo(conciliacion(e, e.mc2, o, p)) == (
        403,
        "ya_participo",
    )  # la API, antes, con el motivo
    assert ficha(e, e.mc2, o)["puede_conciliar"] is False
    assert ofrece_conciliar(e, e.mc2, o) is False
    # Control: otro consejero que no tomó sí.
    assert ficha(e, e.mc1, o)["puede_conciliar"] is True
    assert ofrece_conciliar(e, e.mc1, o) is True
    assert conciliacion(e, e.mc1, o, p).status_code == 200


def test_quien_tomo_tampoco_concilia_con_otra_cuenta(motor_migrate, motor_app, e):
    o, p = por_conciliar(motor_migrate, e)
    otra = otra_cuenta_en_el_consejo(motor_migrate, motor_app, e, e.mc2)
    datos = {"orden": o, "pago": p, "resultado": "conforme", "motivo": None}
    niega(motor_migrate, e, otra, CONCILIAR, "no_es_quien_ejecuto", **datos)
    assert codigo(conciliacion(e, otra, o, p)) == (403, "ya_participo")
    assert ficha(e, otra, o)["puede_conciliar"] is False


def test_quien_tomo_no_coteja_ni_deshace_el_cotejo(motor_migrate, motor_app, e):
    o, p = por_conciliar(motor_migrate, e)
    assert conciliacion(e, e.mc1, o, p).status_code == 200
    otra = otra_cuenta_en_el_consejo(motor_migrate, motor_app, e, e.mc2)
    for quien in (e.mc2, otra):
        assert codigo(tildar(e, quien, p)) == (403, "no_es_quien_ejecuto")
        [elemento] = get(e, quien, "pagos/cotejo").json()["elementos"]
        assert elemento["puede_cotejar"] is False  # la pantalla no lo ofrece
    assert tildar(e, e.mc1, p).status_code == 200  # control
    motivo = {"motivo": "SINTETICO: era otro débito"}
    r = post(e, e.mc2, f"pagos/cotejo/{p}/anulacion", motivo)
    assert codigo(r) == (403, "no_es_quien_ejecuto")
    assert post(e, e.mc1, f"pagos/cotejo/{p}/anulacion", motivo).status_code == 200  # control


def test_quien_tomo_no_confirma_que_no_se_pago(motor_migrate, e):
    o = tomada_por_un_consejero(motor_migrate, e)
    d = declarada(motor_migrate, e, e.adm, o)
    camino = f"ordenes-pago/{o}/pago-manual/no-realizado/{d}/confirmacion"
    assert codigo(post(e, e.mc2, camino, {})) == (403, "ya_participo")
    assert post(e, e.mc1, camino, {}).status_code == 200  # control


def test_quien_tomo_no_resuelve_la_diferencia(motor_migrate, e):
    o = tomada_por_un_consejero(motor_migrate, e)
    d = llamar(motor_migrate, e, e.adm, DIFERENCIA, **datos_diferencia(motor_migrate, e, o))
    camino = f"ordenes-pago/{o}/pago-manual/diferencia/{d}/resolucion"
    resolver = {"resolucion": "reclamo"}
    assert codigo(post(e, e.mc2, camino, resolver)) == (403, "ya_participo")
    assert post(e, e.mc1, camino, resolver).status_code == 200  # control
