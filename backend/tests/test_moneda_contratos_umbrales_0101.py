"""Las monedas del edificio también en contratos, umbrales y exportación (`0101`; cierre de P-14, sí
del propietario del 14-11-2026): un contrato o un umbral en una moneda en que el edificio no opera
se niega (`moneda_no_habilitada`), en la base y en la API; la exportación lleva las monedas
habilitadas.

Cada negación lleva su control: con la moneda habilitada, lo mismo pasa. Datos SINTETICOS / PRUEBA /
NO REAL.
"""

from datetime import date, timedelta

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from scripts import umbral_orden_pago
from tests.conftest import url_rol
from tests.test_egresos_1a import IMPORTE, _hoy, contrato, documento_de_contrato, umbral
from tests.test_modo_operacion import decidir_equipo
from tests.test_orden_pago_neutra import escena_economica, habilitar_moneda
from tests.test_rutas_egresos import codigo, post
from tests.test_rutas_egresos import e as e  # noqa: F401  (es un fixture)
from tests.test_vinculo_0014 import (  # noqa: F401  (`base_aparte` es un fixture)
    _consorcio_y_unidad,
    base_aparte,
)

pytestmark = pytest.mark.db
# Los fixtures importados no se redefinen con el parámetro de su nombre.
# ruff: noqa: F811

SOLO_PESOS = "Este edificio opera solo en pesos."


def _motivo(error) -> str:
    return error.value.orig.diag.message_hint


# Umbrales ----------------------------------------------------------------------------------------


def test_un_umbral_en_dolares_en_un_edificio_solo_en_pesos_se_niega(motor_migrate, motor_app):
    s = escena_economica(motor_migrate, motor_app)
    with pytest.raises(DBAPIError) as error:
        umbral(motor_app, s.cons, IMPORTE, moneda="USD")
    assert _motivo(error) == "moneda_no_habilitada"
    umbral(motor_app, s.cons, IMPORTE)  # control: en pesos, sí
    habilitar_moneda(motor_app, s.cons)
    umbral(motor_app, s.cons, IMPORTE, moneda="USD")  # control: habilitado el dólar, también
    with motor_migrate.connect() as c:
        monedas = c.execute(
            text("select moneda from nucleo.umbral_orden_pago where consorcio_id = :c"
                 " order by moneda"), {"c": s.cons},
        ).scalars().all()  # fmt: skip
    assert monedas == ["ARS", "USD"]


def test_el_script_del_umbral_lo_dice(motor_migrate, motor_app, monkeypatch, capsys):
    monkeypatch.setenv("CEM_DATABASE_URL", url_rol("app"))
    s = escena_economica(motor_migrate, motor_app)
    acta = decidir_equipo(motor_app, s.cons)
    hoy = _hoy(motor_migrate, s)

    def correr(moneda: str) -> tuple[int, str, str]:
        resultado = umbral_orden_pago.main([
            "--consorcio", str(s.cons), "--importe-centavos", str(IMPORTE), "--moneda", moneda,
            "--desde", hoy.isoformat(), "--decision", str(acta),
        ])  # fmt: skip
        salida = capsys.readouterr()
        return resultado, salida.out, salida.err

    resultado, salida, errores = correr("USD")
    assert (resultado, salida) == (1, "")
    assert "moneda_no_habilitada" in errores
    habilitar_moneda(motor_app, s.cons)
    resultado, salida, errores = correr("USD")  # control
    assert (resultado, errores) == (0, "")
    assert '"moneda": "USD"' in salida


# Contratos ---------------------------------------------------------------------------------------


def test_un_contrato_en_dolares_se_niega_en_la_base(motor_migrate, motor_app):
    s = escena_economica(motor_migrate, motor_app)
    with pytest.raises(DBAPIError) as error:
        contrato(motor_migrate, s, moneda="USD")
    assert _motivo(error) == "moneda_no_habilitada"
    assert contrato(motor_migrate, s)  # control: en pesos, sí
    habilitar_moneda(motor_app, s.cons)
    assert contrato(motor_migrate, s, moneda="USD")  # control: habilitado el dólar, también


def test_un_contrato_en_dolares_por_la_api_es_422(motor_migrate, motor_app, e):
    hoy = _hoy(motor_migrate, e)
    cuerpo = {"proveedor_id": str(e.proveedor), "tipo": "abono", "alcance": "SINTETICO: limpieza",
              "importe_maximo_centavos": IMPORTE, "moneda": "USD",
              "vigente_desde": hoy.isoformat(),
              "vigente_hasta": (hoy + timedelta(days=30)).isoformat(),
              "documento_id": str(documento_de_contrato(motor_migrate, e))}  # fmt: skip
    r = post(e, e.adm, "contratos", cuerpo)
    assert codigo(r) == (422, "moneda_no_habilitada")
    assert r.json()["mensaje"] == SOLO_PESOS
    r = post(e, e.adm, "contratos", cuerpo | {"moneda": "ARS"})  # control: en pesos
    assert r.status_code == 201, r.json()
    habilitar_moneda(motor_app, e.cons)
    r = post(e, e.adm, "contratos", cuerpo)  # control: habilitado el dólar
    assert r.status_code == 201, r.json()


# La migración ------------------------------------------------------------------------------------

EXPORTAR = "nucleo.exportar_ordenes_pago(uuid, uuid)"


def _estado(motor, cons) -> dict:
    with motor.connect() as c:
        return {
            "triggers": sorted(c.execute(
                text("select tgrelid::regclass::text from pg_trigger"
                     " where tgname = 'moneda_habilitada'"),
            ).scalars().all()),
            "exportar": c.execute(
                text("select pg_get_functiondef(cast(:f as regprocedure))"), {"f": EXPORTAR}
            ).scalar_one(),
            "monedas": c.execute(
                text("select nucleo.monedas_consorcio(:c)"), {"c": cons}
            ).scalar_one(),
        }  # fmt: skip


def test_0101_ida_y_vuelta_y_relleno(base_aparte):
    """Un umbral en dólares cargado entre `0100` y `0101` (sin la barrera todavía): al subir, el
    edificio queda con el dólar y nada se rompe."""
    cfg, motor = base_aparte
    command.upgrade(cfg, "0100")
    cons, _ = _consorcio_y_unidad(motor)
    with motor.begin() as c:
        decision = c.execute(
            text("insert into nucleo.decision_formal (consorcio_id, tipo, fecha, descripcion,"
                 " referencia_acta, origen) values (:c, 'asamblea', :f, 'SINTETICA', 'Acta 1',"
                 " 'soporte') returning id"), {"c": cons, "f": date(2026, 1, 1)},
        ).scalar_one()  # fmt: skip
        c.execute(
            text("insert into nucleo.umbral_orden_pago (consorcio_id, importe_centavos, moneda,"
                 " vigente_desde, decision_id) values (:c, 100000, 'USD', :f, :d)"),
            {"c": cons, "f": date(2026, 1, 1), "d": decision},
        )  # fmt: skip
    antes = _estado(motor, cons)
    assert antes["triggers"] == ["nucleo.orden_pago_version"]
    assert antes["monedas"] == ["ARS"]  # control: sin el relleno de 0101, solo pesos
    command.upgrade(cfg, "0101")
    arriba = _estado(motor, cons)
    assert arriba["triggers"] == ["nucleo.contrato_proveedor", "nucleo.orden_pago_version",
                                  "nucleo.umbral_orden_pago"]  # fmt: skip
    assert arriba["monedas"] == ["ARS", "USD"]
    assert "'moneda_habilitada'::text" in arriba["exportar"]
    assert "'moneda_habilitada'::text" not in antes["exportar"]
    command.downgrade(cfg, "0100")
    abajo = _estado(motor, cons)
    assert (abajo["triggers"], abajo["exportar"]) == (antes["triggers"], antes["exportar"])
    command.upgrade(cfg, "0101")  # control: vuelve a subir limpio
    assert _estado(motor, cons) == arriba
