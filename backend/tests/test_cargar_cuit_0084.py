"""Cargar el CUIT de un proveedor viejo, contra PostgreSQL real (`0084`). Datos SINTETICOS / PRUEBA
/ NO REAL. Decisión 8 del propietario, 07-11-2026."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.test_circuito_pagos_0072 import (
    e,  # noqa: F401  (es un fixture)
    ficha,
    paso,
)
from tests.test_egresos_1a import aprobada
from tests.test_orden_pago_neutra import cuit_sintetico
from tests.test_proveedor_0048 import alta
from tests.test_rutas_egresos import codigo, get, post

pytestmark = [pytest.mark.db, pytest.mark.redis]
# ruff: noqa: F811


def sin_cuit(motor_migrate, p) -> None:
    """Un proveedor de antes de `0076`. Hoy no se puede crear: la base niega el alta sin CUIT."""
    with motor_migrate.begin() as c:
        c.execute(text("alter table nucleo.proveedor disable trigger user"))
        c.execute(text("alter table nucleo.proveedor drop constraint proveedor_cuit_obligatorio"))
        c.execute(text("update nucleo.proveedor set cuit = null where id = :p"), {"p": p})
        c.execute(text("alter table nucleo.proveedor add constraint proveedor_cuit_obligatorio"
                       " check (cuit is not null) not valid"))  # fmt: skip
        c.execute(text("alter table nucleo.proveedor enable trigger user"))


def viejo(motor_migrate, e):
    p = alta(motor_migrate, e, cuit=cuit_sintetico())
    sin_cuit(motor_migrate, p)
    return p


def cargar(e, quien, p, cuit, version=1):
    return post(e, quien, f"proveedores/{p}/cuit", {"cuit": cuit, "version_esperada": version})


def proveedor(e, p) -> dict:
    return get(e, e.adm, f"proveedores/{p}").json()


def test_sin_cuit_se_carga_y_deja_una_version_nueva(motor_migrate, e):
    p = viejo(motor_migrate, e)
    assert proveedor(e, p)["proveedor"]["cuit"] is None  # control: la escena es la de antes
    cuit = cuit_sintetico()
    r = cargar(e, e.adm, p, cuit)
    assert r.status_code == 200, r.text
    assert r.json()["version"] == 2
    f = proveedor(e, p)
    assert (f["proveedor"]["cuit"], f["proveedor"]["version"]) == (cuit, 2)
    anterior, nueva = sorted(f["versiones"], key=lambda v: v["version"])
    assert anterior["huella"] != nueva["huella"]  # la huella incluye el CUIT
    assert anterior["razon_social"] == nueva["razon_social"]
    assert anterior["cuenta_ultimos_digitos"] == nueva["cuenta_ultimos_digitos"]


def test_una_sola_vez(motor_migrate, e):
    p = viejo(motor_migrate, e)
    primero = cuit_sintetico()
    assert cargar(e, e.adm, p, primero).status_code == 200
    r = cargar(e, e.adm, p, cuit_sintetico(), version=2)
    assert codigo(r) == (409, "cuit_ya_cargado"), r.text
    assert proveedor(e, p)["proveedor"]["cuit"] == primero


def test_un_proveedor_con_cuit_no_lo_cambia(motor_migrate, e):
    p = alta(motor_migrate, e, cuit=cuit_sintetico())
    assert codigo(cargar(e, e.adm, p, cuit_sintetico())) == (409, "cuit_ya_cargado")


def test_invalido_o_repetido_no_se_carga(motor_migrate, e):
    p = viejo(motor_migrate, e)
    otro = cuit_sintetico()
    alta(motor_migrate, e, cuit=otro)
    r = cargar(e, e.adm, p, "20000000000")
    assert codigo(r) == (422, "cuit_invalido"), r.text
    assert codigo(cargar(e, e.adm, p, otro)) == (409, "cuit_repetido")
    assert proveedor(e, p)["proveedor"]["cuit"] is None
    assert proveedor(e, p)["proveedor"]["version"] == 1


def test_sobre_una_version_vieja_no_se_carga(motor_migrate, e):
    p = viejo(motor_migrate, e)
    r = cargar(e, e.adm, p, cuit_sintetico(), version=7)
    assert r.status_code == 409, r.text
    assert proveedor(e, p)["proveedor"]["cuit"] is None


def test_quien_no_prepara_no_carga(motor_migrate, e):
    p = viejo(motor_migrate, e)
    assert cargar(e, e.mc1, p, cuit_sintetico()).status_code == 403
    assert proveedor(e, p)["proveedor"]["cuit"] is None


def test_un_proveedor_que_no_esta_en_el_consorcio_no_se_carga(motor_migrate, e):
    r = post(e, e.adm, "proveedores/00000000-0000-7000-8000-000000000000/cuit",
             {"cuit": cuit_sintetico(), "version_esperada": 1})  # fmt: skip
    assert codigo(r) == (422, "proveedor_inexistente"), r.text


def test_la_tabla_no_deja_borrar_ni_cambiar_el_cuit(motor_migrate, e):
    p = alta(motor_migrate, e, cuit=cuit_sintetico())
    for sql in (
        "update nucleo.proveedor set cuit = '20000000001' where id = :p",
        "update nucleo.proveedor set cuit = null where id = :p",
        "delete from nucleo.proveedor where id = :p",
    ):
        with pytest.raises(DBAPIError, match="solo se agrega"), motor_migrate.begin() as c:
            c.execute(text(sql), {"p": p})


def test_con_una_nulo_solo_cambia_el_cuit(motor_migrate, e):
    p = viejo(motor_migrate, e)
    with pytest.raises(DBAPIError, match="solo se agrega"), motor_migrate.begin() as c:
        c.execute(text("update nucleo.proveedor set cuit = :q, alta_por = :a where id = :p"),
                  {"q": cuit_sintetico(), "a": e.mc1, "p": p})  # fmt: skip


def test_una_orden_aprobada_pasa_por_modificar(motor_migrate, e):
    o = aprobada(motor_migrate, e)
    sin_cuit(motor_migrate, e.proveedor)
    assert paso(e, e.adm, o) == "pagar"  # control
    assert cargar(e, e.adm, e.proveedor, cuit_sintetico()).status_code == 200
    assert paso(e, e.adm, o) == "modificar"
    assert ficha(e, e.adm, o)["puede_pagar"] is False
