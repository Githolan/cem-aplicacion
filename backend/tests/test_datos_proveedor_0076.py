"""Los datos del proveedor (`0076`): CUIT obligatorio y contacto. Datos SINTETICOS / PRUEBA / NO
REAL.

Diseño: `Rediseno_Circuito_Pagos_2026-11-06.md`, §12, del repositorio CEM.
"""

import importlib.util
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.test_orden_pago_neutra import (  # noqa: F401  (`e` es un fixture)
    cuit_sintetico,
    e,
    llamar,
    niega,
)
from tests.test_proveedor_0048 import ALTA, DATOS, VERSION, alta, proveedor_versiones
from tests.test_rutas_egresos import codigo, con_cuit, get, post
from tests.test_vinculo_0014 import base_aparte  # noqa: F401  (es un fixture)

pytestmark = pytest.mark.db

CONTACTO = {
    "telefono": "11 5555 0142",
    "whatsapp": "11 5555 0143",
    "correo": "guardia@cem-prueba.invalid",
    "domicilio": "Calle SINTETICA 1234",
    "atiende_emergencias": True,
}
ALTA_CON_CONTACTO = ALTA.replace(
    ":sesion)", ":sesion, :telefono, :whatsapp, :correo, :domicilio, :atiende_emergencias)"
)
VERSION_CON_CONTACTO = VERSION.replace(
    ":sesion)", ":sesion, :telefono, :whatsapp, :correo, :domicilio, :atiende_emergencias)"
)


def _migracion_0076():
    ruta = Path(__file__).parent.parent / "migrations/versions/0076_datos_del_proveedor.py"
    especificacion = importlib.util.spec_from_file_location("migracion_0076", ruta)
    modulo = importlib.util.module_from_spec(especificacion)
    especificacion.loader.exec_module(modulo)
    return modulo


def test_contacto_ya_quedaba_guardado_en_la_version(motor_migrate, e):  # noqa: F811
    """Lo que había que comprobar antes de migrar: no hizo falta una columna para `contacto`."""
    p = alta(motor_migrate, e, cuit=cuit_sintetico())
    [v] = proveedor_versiones(motor_migrate, p)
    assert v["contacto"] == DATOS["contacto"]


def test_el_alta_sin_cuit_se_niega_en_la_funcion(motor_migrate, e):  # noqa: F811
    niega(motor_migrate, e, e.adm, ALTA, "cuit_invalido", **{**DATOS, "cuit": None})
    assert alta(motor_migrate, e, cuit=cuit_sintetico()) is not None  # control: con CUIT pasa


def test_el_alta_sin_cuit_se_niega_en_la_tabla(motor_migrate, e):  # noqa: F811
    sentencia = text(
        "insert into nucleo.proveedor (consorcio_id, cuit, alta_por, identidad_alta)"
        " values (:c, :cuit, :q, nucleo.identidad_humana_de(:c, :q))"
    )
    with pytest.raises(DBAPIError) as error, motor_migrate.begin() as c:
        c.execute(sentencia, {"c": e.cons, "q": e.adm, "cuit": None})
    assert error.value.orig.diag.constraint_name == "proveedor_cuit_obligatorio"
    with motor_migrate.begin() as c:  # control: la misma fila con CUIT entra
        c.execute(sentencia, {"c": e.cons, "q": e.adm, "cuit": cuit_sintetico()})


def test_el_alta_guarda_el_contacto(motor_migrate, e):  # noqa: F811
    p = llamar(
        motor_migrate, e, e.adm, ALTA_CON_CONTACTO,
        **{**DATOS, **CONTACTO, "cuit": cuit_sintetico()},
    )  # fmt: skip
    [v] = proveedor_versiones(motor_migrate, p)
    assert {k: v[k] for k in CONTACTO} == CONTACTO


def test_sin_contacto_no_atiende_emergencias(motor_migrate, e):  # noqa: F811
    [v] = proveedor_versiones(motor_migrate, alta(motor_migrate, e, cuit=cuit_sintetico()))
    assert v["telefono"] is None and v["atiende_emergencias"] is False


def test_cambiar_el_contacto_es_una_version_nueva(motor_migrate, e):  # noqa: F811
    p = alta(motor_migrate, e, cuit=cuit_sintetico())
    datos = {**DATOS, **CONTACTO, "proveedor": p, "version": 1}
    assert llamar(motor_migrate, e, e.adm, VERSION_CON_CONTACTO, **datos) == 2
    versiones = sorted(proveedor_versiones(motor_migrate, p), key=lambda v: v["numero"])
    assert versiones[0]["telefono"] is None
    assert versiones[1]["telefono"] == CONTACTO["telefono"]
    assert versiones[1]["cuenta_destino"] == versiones[0]["cuenta_destino"]
    # Sin cambios sigue siendo sin cambios, también con los datos nuevos (control).
    niega(motor_migrate, e, e.adm, VERSION_CON_CONTACTO, "sin_cambios", **{**datos, "version": 2})
    otro = {**datos, "version": 2, "atiende_emergencias": False}
    assert llamar(motor_migrate, e, e.adm, VERSION_CON_CONTACTO, **otro) == 3


def test_la_api_exige_el_cuit_y_devuelve_el_contacto(e):  # noqa: F811
    sin_cuit = {k: v for k, v in con_cuit().items() if k != "cuit"}
    assert codigo(post(e, e.adm, "proveedores", sin_cuit)) == (422, "peticion_invalida")

    r = post(e, e.adm, "proveedores", con_cuit() | CONTACTO)
    assert r.status_code == 201, r.json()
    p = r.json()["id"]
    detalle = get(e, e.adm, f"proveedores/{p}").json()
    assert {k: detalle["proveedor"][k] for k in CONTACTO} == CONTACTO
    assert {k: detalle["versiones"][0][k] for k in CONTACTO} == CONTACTO
    [lista] = [x for x in get(e, e.adm, "proveedores").json()["elementos"] if x["id"] == p]
    assert lista["correo"] == CONTACTO["correo"]

    cambio = {
        "razon_social": detalle["proveedor"]["razon_social"],
        "rubro": detalle["proveedor"]["rubro"],
        "estado": "activo",
        "version_esperada": 1,
        "conservar_cuenta": True,
        "telefono": "11 5555 0199",
    }
    r = post(e, e.adm, f"proveedores/{p}/versiones", cambio)
    assert r.status_code == 200, r.json()
    vigente = get(e, e.adm, f"proveedores/{p}").json()["proveedor"]
    assert vigente["telefono"] == "11 5555 0199"
    assert vigente["correo"] is None and vigente["atiende_emergencias"] is False


def test_la_api_rechaza_un_correo_que_no_lo_es(e):  # noqa: F811
    r = post(e, e.adm, "proveedores", con_cuit() | {"correo": "sin-arroba"})
    assert codigo(r) == (422, "peticion_invalida")


def test_la_guarda_del_downgrade_se_niega_con_datos(motor_migrate, e):  # noqa: F811
    guarda = _migracion_0076().GUARDA_DOWNGRADE
    llamar(
        motor_migrate, e, e.adm, ALTA_CON_CONTACTO,
        **{**DATOS, **CONTACTO, "cuit": cuit_sintetico()},
    )  # fmt: skip
    with motor_migrate.connect() as c, c.begin() as tx:
        with pytest.raises(DBAPIError) as error, c.begin_nested():
            c.execute(text(guarda))
        assert error.value.orig.diag.message_hint == "downgrade_con_datos"
        tx.rollback()


def test_0076_ida_y_vuelta(base_aparte):  # noqa: F811
    cfg, motor = base_aparte
    anterior = _migracion_0076().down_revision
    columnas = text(
        "select count(*) from information_schema.columns where table_schema = 'nucleo'"
        " and table_name = 'proveedor_version' and column_name = 'telefono'"
    )
    command.upgrade(cfg, "0076")
    command.downgrade(cfg, anterior)
    with motor.connect() as c:
        assert c.execute(columnas).scalar_one() == 0
        c.execute(text("select 'nucleo.alta_proveedor(text, text, text, text, uuid, text, uuid,"
                       " text, uuid)'::regprocedure"))  # fmt: skip
    command.upgrade(cfg, "0076")
    with motor.connect() as c:
        assert c.execute(columnas).scalar_one() == 1
