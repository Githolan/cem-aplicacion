"""Las monedas en que opera cada edificio (`0100`; informe de fricción, P-14; sí del propietario del
14-11-2026): todo edificio opera en pesos, el equipo habilita el dólar con
`scripts/moneda_consorcio.py`, y una orden en una moneda que el edificio no tiene habilitada se
niega en la base y en la API (`moneda_no_habilitada`).

Cada negación lleva su control: el mismo pedido, con la moneda habilitada, pasa. Datos SINTETICOS /
PRUEBA / NO REAL.
"""

import importlib.util
import json
from datetime import date
from uuid import UUID

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db import transaccion
from scripts import moneda_consorcio
from tests.conftest import RAIZ, url_rol
from tests.test_orden_pago_neutra import (
    MODIFICAR,
    PREPARAR,
    escena_economica,
    habilitar_moneda,
    llamar,
    niega,
    versiones,
)
from tests.test_rutas_egresos import ORDEN, cliente, codigo, post
from tests.test_rutas_egresos import e as e  # noqa: F401  (es un fixture)
from tests.test_vinculo_0014 import (  # noqa: F401  (`base_aparte` es un fixture)
    _consorcio_y_unidad,
    base_aparte,
)

pytestmark = pytest.mark.db
# Los fixtures importados no se redefinen con el parámetro de su nombre.
# ruff: noqa: F811

SOLO_PESOS = "Este edificio opera solo en pesos."


def _migracion():
    ruta = RAIZ / "migrations" / "versions" / "0100_monedas_del_consorcio.py"
    spec = importlib.util.spec_from_file_location("migracion_0100", ruta)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def monedas(motor_migrate, cons) -> list[str]:
    with motor_migrate.connect() as c:
        return c.execute(text("select nucleo.monedas_consorcio(:c)"), {"c": cons}).scalar_one()


def filas(motor, cons) -> list[tuple[str, str]]:
    with motor.connect() as c:
        return [tuple(f) for f in c.execute(
            text("select moneda, origen from nucleo.moneda_consorcio where consorcio_id = :c"
                 " order by moneda"), {"c": cons},
        ).all()]  # fmt: skip


def eventos_del_consorcio(motor_migrate, cons) -> int:
    with motor_migrate.connect() as c:
        return c.execute(
            text("select count(*) from auditoria.eventos where consorcio_id = :c"), {"c": cons}
        ).scalar_one()


# La barrera de la base ---------------------------------------------------------------------------


def test_un_edificio_nuevo_opera_solo_en_pesos(motor_migrate, motor_app):
    e = escena_economica(motor_migrate, motor_app)
    assert monedas(motor_migrate, e.cons) == ["ARS"]
    niega(motor_migrate, e, e.adm, PREPARAR, "moneda_no_habilitada", moneda="USD")
    niega(motor_migrate, e, e.adm, PREPARAR, "moneda_no_habilitada", moneda="EUR")
    # Control: en pesos, la misma orden se prepara.
    o = llamar(motor_migrate, e, e.adm, PREPARAR)
    assert versiones(motor_migrate, o)[0]["moneda"] == "ARS"


def test_con_el_dolar_habilitado_la_orden_en_dolares_pasa(motor_migrate, motor_app):
    e = escena_economica(motor_migrate, motor_app)
    o = llamar(motor_migrate, e, e.adm, PREPARAR)
    niega(motor_migrate, e, e.adm, MODIFICAR, "moneda_no_habilitada", orden=o, version=1,
          moneda="USD")  # fmt: skip
    habilitar_moneda(motor_app, e.cons)
    assert monedas(motor_migrate, e.cons) == ["ARS", "USD"]
    # Control: la versión nueva en dólares y una orden nueva en dólares pasan.
    assert llamar(motor_migrate, e, e.adm, MODIFICAR, orden=o, version=1, moneda="USD") == 2
    otra = llamar(motor_migrate, e, e.adm, PREPARAR, moneda="USD")
    assert versiones(motor_migrate, otra)[0]["moneda"] == "USD"
    # Otra moneda que no se habilitó sigue negada.
    niega(motor_migrate, e, e.adm, PREPARAR, "moneda_no_habilitada", moneda="EUR")


def test_la_moneda_de_un_edificio_no_habilita_la_de_otro(motor_migrate, motor_app):
    a = escena_economica(motor_migrate, motor_app)
    b = escena_economica(motor_migrate, motor_app)
    habilitar_moneda(motor_app, a.cons)
    assert (monedas(motor_migrate, a.cons), monedas(motor_migrate, b.cons)) == (
        ["ARS", "USD"], ["ARS"])  # fmt: skip
    niega(motor_migrate, b, b.adm, PREPARAR, "moneda_no_habilitada", moneda="USD")
    llamar(motor_migrate, a, a.adm, PREPARAR, moneda="USD")  # control


# Quién la habilita -------------------------------------------------------------------------------


def test_solo_el_equipo_la_habilita_y_solo_se_agrega(motor_migrate, motor_app):
    e = escena_economica(motor_migrate, motor_app)
    sql = "select nucleo.habilitar_moneda_consorcio('USD', :actor, :origen)"
    for persona, actor, origen in ((e.adm, e.adm, "api"), (None, None, "api"),
                                   (e.adm, None, "soporte")):  # fmt: skip
        with pytest.raises(DBAPIError) as error:
            with transaccion(motor_app, persona_id=persona, consorcio_id=e.cons) as c:
                c.execute(text(sql), {"actor": actor, "origen": origen})
        assert error.value.orig.diag.message_hint == "origen_invalido"
    assert filas(motor_migrate, e.cons) == []
    habilitar_moneda(motor_app, e.cons)  # control: el equipo, sin persona
    assert filas(motor_migrate, e.cons) == [("USD", "soporte")]
    # La fila no se reescribe ni se borra, ni siquiera como dueño de la tabla.
    for sentencia in ("update nucleo.moneda_consorcio set moneda = 'EUR' where consorcio_id = :c",
                      "delete from nucleo.moneda_consorcio where consorcio_id = :c"):  # fmt: skip
        with pytest.raises(DBAPIError) as error, motor_migrate.begin() as c:
            c.execute(text(sentencia), {"c": e.cons})
        assert error.value.orig.diag.message_hint == "historia_inmutable"
    with motor_migrate.connect() as c:
        privilegios = [c.execute(
            text("select has_table_privilege('cem_app', 'nucleo.moneda_consorcio', :p)"),
            {"p": p},
        ).scalar_one() for p in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE")]  # fmt: skip
    assert privilegios == [True, False, False, False, False]


@pytest.fixture
def correr(monkeypatch, capsys):
    monkeypatch.setenv("CEM_DATABASE_URL", url_rol("app"))

    def _correr(*argumentos: str) -> tuple[int, str, str]:
        resultado = moneda_consorcio.main(list(argumentos))
        salida = capsys.readouterr()
        return resultado, salida.out, salida.err

    return _correr


def test_el_script_habilita_el_dolar_con_constancia_y_sin_evento(motor_migrate, motor_app,
                                                                  correr):  # fmt: skip
    e = escena_economica(motor_migrate, motor_app)
    eventos = eventos_del_consorcio(motor_migrate, e.cons)
    resultado, salida, errores = correr("--consorcio", str(e.cons), "--habilitar", "USD")
    assert (resultado, errores) == (0, "")
    impreso = json.loads(salida)
    assert (impreso["consorcio_id"], impreso["habilitada"], impreso["monedas"]) == (
        str(e.cons), "USD", ["ARS", "USD"])  # fmt: skip
    with motor_migrate.connect() as c:
        cuando = c.execute(
            text("select habilitada_en from nucleo.moneda_consorcio where consorcio_id = :c"),
            {"c": e.cons},
        ).scalar_one()
    # La constancia: quién (el equipo) y cuándo. Soporte no escribe eventos de clase B (S2b).
    assert filas(motor_migrate, e.cons) == [("USD", "soporte")]
    assert impreso["habilitada_en"] == cuando.isoformat()
    assert eventos_del_consorcio(motor_migrate, e.cons) == eventos
    llamar(motor_migrate, e, e.adm, PREPARAR, moneda="USD")  # ya opera en dólares
    # Dos veces no: ya está habilitada.
    resultado, salida, errores = correr("--consorcio", str(e.cons), "--habilitar", "USD")
    assert (resultado, salida) == (1, "")
    assert "moneda_ya_habilitada" in errores


@pytest.mark.parametrize("moneda", ["EUR", "ARS", "usd", "DOLARES"])
def test_el_script_rechaza_una_moneda_que_no_se_habilita(motor_migrate, motor_app, correr, moneda):
    e = escena_economica(motor_migrate, motor_app)
    resultado, salida, errores = correr("--consorcio", str(e.cons), "--habilitar", moneda)
    assert (resultado, salida) == (1, "")
    assert "moneda_invalida" in errores
    assert filas(motor_migrate, e.cons) == []
    assert monedas(motor_migrate, e.cons) == ["ARS"]


# La API ------------------------------------------------------------------------------------------


def _mis_monedas(e) -> list[str]:
    with cliente(e.adm, e.sesion[e.adm]) as c:
        r = c.get("/api/v1/me/consorcios")
    assert r.status_code == 200, r.text
    [este] = [x for x in r.json() if x["id"] == str(e.cons)]
    return este["monedas"]


def test_una_orden_en_dolares_en_un_edificio_solo_en_pesos_es_422(e, motor_app):
    nueva = ORDEN | {"proveedor_id": str(e.proveedor), "moneda": "USD"}
    r = post(e, e.adm, "ordenes-pago", nueva)
    assert codigo(r) == (422, "moneda_no_habilitada")
    assert r.json()["mensaje"] == SOLO_PESOS
    # Una versión nueva en dólares de una orden en pesos, tampoco.
    r = post(e, e.adm, "ordenes-pago", ORDEN | {"proveedor_id": str(e.proveedor)})
    assert r.status_code == 201, r.text
    o = r.json()["id"]
    version = nueva | {"version_esperada": 1}
    assert codigo(post(e, e.adm, f"ordenes-pago/{o}/versiones", version)) == (
        422, "moneda_no_habilitada")  # fmt: skip
    # Control: con el dólar habilitado, las dos pasan.
    habilitar_moneda(motor_app, e.cons)
    r = post(e, e.adm, "ordenes-pago", nueva)
    assert r.status_code == 201, r.text
    r = post(e, e.adm, f"ordenes-pago/{o}/versiones", version)
    assert (r.status_code, r.json()["version"]) == (200, 2)


def test_mis_consorcios_dice_las_monedas_de_cada_edificio(e, motor_app):
    assert _mis_monedas(e) == ["ARS"]
    habilitar_moneda(motor_app, e.cons)
    assert _mis_monedas(e) == ["ARS", "USD"]


# La migración ------------------------------------------------------------------------------------


def test_el_relleno_habilita_lo_que_cada_edificio_ya_usa(motor_migrate, motor_app):
    """El `insert` de la migración, sobre órdenes reales: el edificio con una orden en dólares queda
    con el dólar, el que solo tiene órdenes en pesos queda solo en pesos. En una transacción que se
    deshace: la tabla se vacía (como dueño, sin los triggers) y se rellena de nuevo."""
    con_dolares = escena_economica(motor_migrate, motor_app)
    habilitar_moneda(motor_app, con_dolares.cons)
    llamar(motor_migrate, con_dolares, con_dolares.adm, PREPARAR, moneda="USD")
    solo_pesos = escena_economica(motor_migrate, motor_app)
    habilitar_moneda(motor_app, solo_pesos.cons)  # habilitado, pero sin nada en dólares
    llamar(motor_migrate, solo_pesos, solo_pesos.adm, PREPARAR)
    relleno = _migracion().RELLENO
    with motor_migrate.connect() as c, c.begin() as tx:
        c.execute(text("alter table nucleo.moneda_consorcio disable trigger user;"
                       " delete from nucleo.moneda_consorcio;"
                       " alter table nucleo.moneda_consorcio enable trigger user"))  # fmt: skip
        c.execute(text(relleno))
        quedan = {
            cons: [tuple(f) for f in c.execute(
                text("select moneda, origen from nucleo.moneda_consorcio where consorcio_id = :c"),
                {"c": cons},
            ).all()]
            for cons in (con_dolares.cons, solo_pesos.cons)
        }  # fmt: skip
        tx.rollback()
    assert quedan == {con_dolares.cons: [("USD", "migracion")], solo_pesos.cons: []}
    # Control: lo deshecho no tocó lo que había.
    assert filas(motor_migrate, solo_pesos.cons) == [("USD", "soporte")]


def _decision(c, cons) -> UUID:
    return c.execute(
        text("insert into nucleo.decision_formal (consorcio_id, tipo, fecha, descripcion,"
             " referencia_acta, origen) values (:c, 'asamblea', :f, 'SINTETICA', 'Acta 1',"
             " 'soporte') returning id"), {"c": cons, "f": date(2026, 1, 1)},
    ).scalar_one()  # fmt: skip


def _umbral(c, cons, moneda: str) -> None:
    c.execute(
        text("insert into nucleo.umbral_orden_pago (consorcio_id, importe_centavos, moneda,"
             " vigente_desde, decision_id) values (:c, 100000, :m, :f, :d)"),
        {"c": cons, "m": moneda, "f": date(2026, 1, 1), "d": _decision(c, cons)},
    )  # fmt: skip


ORIGENES = "select distinct origen from nucleo.moneda_consorcio"


def _estado(motor, consorcios) -> dict:
    with motor.connect() as c:
        tabla = c.execute(text("select to_regclass('nucleo.moneda_consorcio')")).scalar()
        if tabla is None:
            barrera = c.execute(
                text("select count(*) from pg_trigger where tgname = 'moneda_habilitada'")
            ).scalar_one()
            funciones = c.execute(
                text("select count(*) from pg_proc where proname in ('monedas_consorcio',"
                     " 'habilitar_moneda_consorcio', 'exigir_moneda_habilitada')")
            ).scalar_one()  # fmt: skip
            return {"tabla": False, "barrera": barrera, "funciones": funciones}
        return {
            "tabla": True,
            "monedas": [
                c.execute(text("select nucleo.monedas_consorcio(:c)"), {"c": x}).scalar_one()
                for x in consorcios
            ],
            "origenes": c.execute(text(ORIGENES)).scalars().all(),
        }


def test_0100_ida_y_vuelta_y_relleno(base_aparte):
    cfg, motor = base_aparte
    command.upgrade(cfg, "0099")
    dolares, _ = _consorcio_y_unidad(motor)
    pesos, _ = _consorcio_y_unidad(motor)
    nada, _ = _consorcio_y_unidad(motor)
    with motor.begin() as c:
        _umbral(c, dolares, "USD")
        _umbral(c, pesos, "ARS")
    consorcios = (dolares, pesos, nada)
    arriba = {"tabla": True, "monedas": [["ARS", "USD"], ["ARS"], ["ARS"]],
              "origenes": ["migracion"]}  # fmt: skip
    abajo = {"tabla": False, "barrera": 0, "funciones": 0}
    command.upgrade(cfg, "0100")
    assert _estado(motor, consorcios) == arriba
    command.downgrade(cfg, "0099")
    assert _estado(motor, consorcios) == abajo
    command.upgrade(cfg, "0100")  # las de la migración se recalculan
    assert _estado(motor, consorcios) == arriba


def test_0100_no_baja_si_el_equipo_habilito_una_moneda(base_aparte):
    cfg, motor = base_aparte
    command.upgrade(cfg, "0100")
    cons, _ = _consorcio_y_unidad(motor)
    with motor.begin() as c:
        c.execute(
            text("insert into nucleo.moneda_consorcio (consorcio_id, moneda, origen)"
                 " values (:c, 'USD', 'soporte')"), {"c": cons},
        )  # fmt: skip
    with pytest.raises(DBAPIError) as error:
        command.downgrade(cfg, "0099")
    assert error.value.orig.diag.message_hint == "downgrade_con_datos"
    assert _estado(motor, [cons])["monedas"] == [["ARS", "USD"]]  # sigue arriba, con la fila
    # Control: sin la fila del equipo, baja.
    with motor.begin() as c:
        c.execute(text("alter table nucleo.moneda_consorcio disable trigger user;"
                       " delete from nucleo.moneda_consorcio;"
                       " alter table nucleo.moneda_consorcio enable trigger user"))  # fmt: skip
    command.downgrade(cfg, "0099")
    assert _estado(motor, [cons])["tabla"] is False
