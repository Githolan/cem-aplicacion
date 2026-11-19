"""Tanda 10 de la revisión por flujos (18-11-2026), en pagos:

- **FL-ORD-08:** con el proveedor suspendido o de baja, la ficha de una orden «No se pagó» no
  ofrece «Enviar a aprobar de nuevo» (la versión nueva se negaría), y sí lo que lo resuelve.
- **FL-ACL-01:** quien vio la cuenta y ya no ejecuta recibe, en los pagos para aclarar, lo que la
  IA leyó de su comprobante (sin la cuenta: solo los últimos dígitos y si coincide), registra sin
  escribir la cuenta si la lectura la acredita y puede decir «Se leyó mal».

La escena es la de `test_pago_manual_0055`. Contra PostgreSQL real. Datos SINTETICOS / NO REAL.
"""

from decimal import Decimal

import pytest
from sqlalchemy import text

from tests.test_circuito_pagos_0072 import ficha
from tests.test_egresos_1a import _hoy
from tests.test_orden_pago_neutra import RAZON_SOCIAL
from tests.test_ordenes_trabadas_0067 import lista_por_aclarar, trabada
from tests.test_pago_manual_0055 import CUENTA, IMPORTE
from tests.test_pago_manual_0055 import e as e  # noqa: F401  (es un fixture)
from tests.test_pague_otra_cosa_0075 import fallida
from tests.test_proveedor_0048 import _nueva_cuenta
from tests.test_registro_sin_facultad_0068 import comprobante_sin_facultad
from tests.test_rutas_egresos import post

pytestmark = [pytest.mark.db, pytest.mark.redis]
# ruff: noqa: F811

NUMERO = "0001-234567"


def codigos(x: dict) -> set[str]:
    return {a["codigo"] for a in x["acciones"]}


# FL-ORD-08 -------------------------------------------------------------------------------------


@pytest.mark.parametrize("estado", ["suspendido", "baja"])
def test_con_el_proveedor_inactivo_no_se_ofrece_volver_a_pagar(motor_migrate, e, estado):
    o = fallida(motor_migrate, e)
    x = ficha(e, e.adm, o)
    # Control: con el proveedor activo se ofrece, junto con modificar y cerrar sin pagar.
    assert {"volver_a_pagar", "modificar", "cerrar_sin_pagar"} <= codigos(x)
    assert x["motivo_sin_acto"] is None
    _nueva_cuenta(motor_migrate, e, e.proveedor, 1, estado=estado)
    x = ficha(e, e.adm, o)
    assert "volver_a_pagar" not in codigos(x)
    # Lo que lo resuelve sigue: elegir otro proveedor al modificar, o cerrarla sin pagar.
    assert {"modificar", "cerrar_sin_pagar"} <= codigos(x)
    assert x["motivo_sin_acto"] == "proveedor_inactivo"


# FL-ACL-01 -------------------------------------------------------------------------------------


def leida(motor_migrate, e, o, version, **cambios) -> None:
    """Datos sintéticos: lo que `cem-trabajos` guarda al leer el comprobante (sin la cuenta)."""
    fila = {
        "importe": Decimal(IMPORTE) / 100, "moneda": "ARS", "ultimos": CUENTA[-4:],
        "coincide": True, "titular": RAZON_SOCIAL, "numero": NUMERO,
        "fecha": _hoy(motor_migrate, e),
    } | cambios  # fmt: skip
    with motor_migrate.begin() as c:
        n = c.execute(
            text(
                "insert into nucleo.lectura_comprobante (consorcio_id, version_documento_id,"
                " documento_id, ejecucion_id, modelo, version_prompt, legible, importe, moneda,"
                " cuenta_ultimos_digitos, cuenta_coincide, titular_destino, numero_operacion,"
                " fecha)"
                " select v.consorcio_id, v.id, v.documento_id, x.id, 'modelo SINTETICO', 'p1',"
                " true, :importe, :moneda, :ultimos, :coincide, :titular, :numero, :fecha"
                " from nucleo.version_documento v"
                " join nucleo.ejecucion_orden_pago x on x.orden_id = :o"
                " where v.id = :v"
            ),
            fila | {"o": o, "v": version},
        ).rowcount
    assert n == 1


def registro(e, fila: dict, doc, **cambios) -> dict:
    return {
        "version": fila["version"], "huella": fila["huella"], "numero_operacion": NUMERO,
        "fecha_operacion": fila["lectura_comprobante"]["fecha"], "importe_centavos": IMPORTE,
        "moneda": "ARS", "documento_id": str(doc),
    } | cambios  # fmt: skip


def lectura_disputada(motor_migrate, o) -> list[bool]:
    with motor_migrate.connect() as c:
        return list(c.execute(
            text("select p.lectura_disputada from nucleo.pago_manual p"
                 " join nucleo.ejecucion_orden_pago x on x.id = p.ejecucion_id"
                 " where x.orden_id = :o order by p.paso"), {"o": o},
        ).scalars())  # fmt: skip


def test_por_aclarar_trae_la_lectura_y_registra_sin_escribir_la_cuenta(motor_migrate, motor_app, e):
    o = trabada(motor_migrate, motor_app, e)
    datos = comprobante_sin_facultad(motor_migrate, e, o)
    # Control: sin lectura, nada.
    [fila] = lista_por_aclarar(e, e.adm)
    assert (fila["comprobante_estado"], fila["lectura_comprobante"]) == ("disponible", None)
    leida(motor_migrate, e, o, datos["version_archivo"])
    [fila] = lista_por_aclarar(e, e.adm)
    lectura = fila["lectura_comprobante"]
    assert lectura["version_documento_id"] == str(datos["version_archivo"])
    assert (lectura["estado"], lectura["importe_centavos"], lectura["numero_operacion"]) == (
        "leida", IMPORTE, NUMERO)  # fmt: skip
    assert (lectura["cuenta_ultimos_digitos"], lectura["cuenta_coincide"]) == (CUENTA[-4:], True)
    assert lectura["distinto_de_la_orden"] == [] and lectura["titular_distinto"] is False
    # La cuenta completa no viaja: ni la de la orden ni la leída (que no se guarda).
    assert CUENTA not in str(fila)
    r = post(e, e.adm, f"pagos-por-aclarar/{o}/pago", registro(e, fila, datos["documento"]))
    assert r.status_code == 201, r.text
    assert lectura_disputada(motor_migrate, o) == [False]
    assert lista_por_aclarar(e, e.adm) == []


def test_por_aclarar_lo_distinto_se_marca_y_se_leyo_mal_queda(motor_migrate, motor_app, e):
    o = trabada(motor_migrate, motor_app, e)
    datos = comprobante_sin_facultad(motor_migrate, e, o)
    leida(motor_migrate, e, o, datos["version_archivo"], importe=Decimal(IMPORTE + 100) / 100)
    [fila] = lista_por_aclarar(e, e.adm)
    assert fila["lectura_comprobante"]["distinto_de_la_orden"] == ["importe"]
    # «Se leyó mal»: escribe la cuenta y lo dice; antes de 0111 (tanda 10) se perdía en esta ruta.
    cuerpo = registro(e, fila, datos["documento"], cuenta_destino=CUENTA, lectura_mal=True)
    r = post(e, e.adm, f"pagos-por-aclarar/{o}/pago", cuerpo)
    assert r.status_code == 201, r.text
    assert lectura_disputada(motor_migrate, o) == [True]


def test_la_lectura_es_solo_de_quien_vio_la_cuenta(motor_migrate, motor_app, e):
    o = trabada(motor_migrate, motor_app, e)
    datos = comprobante_sin_facultad(motor_migrate, e, o)
    leida(motor_migrate, e, o, datos["version_archivo"])
    assert lista_por_aclarar(e, e.mc2) == []
    assert lista_por_aclarar(e, e.adm)[0]["lectura_comprobante"] is not None  # control
