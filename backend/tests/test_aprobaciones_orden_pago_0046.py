"""H3, decisión 2 del propietario (29-10-2026, `0046`): cuántas aprobaciones necesita una orden de
pago se configura por consorcio; la carga solo soporte, con su acta, desde hoy en adelante. Datos
SINTETICOS / PRUEBA / NO REAL.

Lo que cambia en la aprobación (la orden pasa a «aprobada», la cantidad por versión, P3 69) se
prueba en `test_orden_pago_neutra.py`; acá, la configuración, el script y la migración.
"""

import json
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db import transaccion
from scripts import aprobaciones_orden_pago
from tests.conftest import url_rol
from tests.test_modo_operacion import consorcio_con_persona, decidir_equipo
from tests.test_orden_pago_neutra import (  # noqa: F401  (`e` es un fixture)
    PREPARAR,
    e,
    exigir,
    llamar,
    versiones,
)
from tests.test_vinculo_0014 import (  # noqa: F401  (`base_aparte` es un fixture)
    _consorcio_y_unidad,
    _persona,
    base_aparte,
)

pytestmark = pytest.mark.db

FIJAR = (
    "select nucleo.fijar_aprobaciones_orden_pago(cast(:n as smallint),"
    " nucleo.hoy_del_consorcio(:c) + :dias, :d, :actor, :origen)"
)


def fijar(motor_app, cons, *, n=2, dias=0, decision=None, actor=None, origen="soporte",
          persona=None) -> UUID:  # fmt: skip
    with transaccion(motor_app, persona_id=persona, consorcio_id=cons) as c:
        return c.execute(
            text(FIJAR),
            {"n": n, "c": cons, "dias": dias, "actor": actor, "origen": origen,
             "d": decision or decidir_equipo(motor_app, cons)},
        ).scalar_one()  # fmt: skip


def cargas(motor_migrate, cons) -> list[tuple[int, int]]:
    """(cantidad, días desde hoy) de cada carga del consorcio."""
    with motor_migrate.connect() as c:
        return [
            tuple(f)
            for f in c.execute(
                text(
                    "select cantidad, vigente_desde - nucleo.hoy_del_consorcio(:c)"
                    " from nucleo.aprobaciones_orden_pago where consorcio_id = :c"
                    " order by vigente_desde"
                ),
                {"c": cons},
            ).all()
        ]


@pytest.mark.parametrize(
    ("cambio", "motivo"),
    [
        ({"origen": "api"}, "origen_invalido"),
        ({"n": 0}, "cantidad_invalida"),
        ({"n": 3}, "cantidad_invalida"),
        ({"dias": -1}, "fecha_invalida"),  # nunca hacia atrás
        ({"decision": uuid4()}, "decision_invalida"),
    ],
)
def test_la_carga_se_niega_sin_lo_que_exige(motor_migrate, motor_app, cambio, motivo):
    cons, _, _ = consorcio_con_persona(motor_migrate)
    with pytest.raises(DBAPIError) as error:
        fijar(motor_app, cons, **cambio)
    assert error.value.orig.diag.message_hint == motivo
    assert cargas(motor_migrate, cons) == []
    fijar(motor_app, cons)  # control: con todo en orden, carga
    assert cargas(motor_migrate, cons) == [(2, 0)]


def test_ninguna_persona_la_carga(motor_migrate, motor_app):
    """Solo el equipo: ni con una persona en la sesión ni nombrando un actor."""
    cons, _, prop = consorcio_con_persona(motor_migrate)
    for extra in ({"persona": prop}, {"actor": prop}, {"persona": prop, "actor": prop}):
        with pytest.raises(DBAPIError) as error:
            fijar(motor_app, cons, **extra)
        assert error.value.orig.diag.message_hint == "origen_invalido", extra
    assert cargas(motor_migrate, cons) == []


def test_la_decision_tiene_que_ser_del_mismo_consorcio(motor_migrate, motor_app):
    cons, _, _ = consorcio_con_persona(motor_migrate)
    otro, _, _ = consorcio_con_persona(motor_migrate)
    with pytest.raises(DBAPIError) as error:
        fijar(motor_app, cons, decision=decidir_equipo(motor_app, otro))
    assert error.value.orig.diag.message_hint == "decision_invalida"


def test_un_cambio_es_otra_carga_y_ninguna_se_modifica(motor_migrate, motor_app):
    cons, _, _ = consorcio_con_persona(motor_migrate)
    fijar(motor_app, cons, n=2)
    with pytest.raises(DBAPIError) as error:
        fijar(motor_app, cons, n=1)
    assert error.value.orig.diag.message_hint == "vigencia_repetida"
    fijar(motor_app, cons, n=1, dias=30)
    assert cargas(motor_migrate, cons) == [(2, 0), (1, 30)]
    for sentencia in (
        "update nucleo.aprobaciones_orden_pago set cantidad = 1 where consorcio_id = :c",
        "delete from nucleo.aprobaciones_orden_pago where consorcio_id = :c",
        "truncate nucleo.aprobaciones_orden_pago",
    ):
        with pytest.raises(DBAPIError) as error:
            with motor_migrate.begin() as c:
                c.execute(text(sentencia), {"c": cons})
        assert error.value.orig.diag.message_hint == "historia_inmutable", sentencia
    assert cargas(motor_migrate, cons) == [(2, 0), (1, 30)]


def test_rige_la_ultima_carga_que_ya_empezo(motor_migrate, motor_app, e):  # noqa: F811
    """Una carga futura todavía no rige; sin cargas, 1. Lo ve la versión que se prepara."""

    def requeridas_de_una_nueva() -> int:
        o = llamar(motor_migrate, e, e.adm, PREPARAR)
        return versiones(motor_migrate, o)[0]["aprobaciones_requeridas"]

    assert requeridas_de_una_nueva() == 1
    fijar(motor_app, e.cons, n=2, dias=1)
    assert requeridas_de_una_nueva() == 1
    exigir(motor_app, e.cons, 2)  # desde hoy
    assert requeridas_de_una_nueva() == 2


# Script de soporte ---------------------------------------------------------------------------


@pytest.fixture
def correr(monkeypatch, capsys):
    monkeypatch.setenv("CEM_DATABASE_URL", url_rol("app"))

    def _correr(cons, decision, cantidad, desde):
        codigo = aprobaciones_orden_pago.main(
            ["--consorcio", str(cons), "--cantidad", str(cantidad), "--desde", desde.isoformat(),
             "--decision", str(decision)]
        )  # fmt: skip
        salida = capsys.readouterr()
        return codigo, salida.out, salida.err

    return _correr


def test_el_script_carga_y_rechaza_hacia_atras(motor_migrate, motor_app, correr):
    cons, _, _ = consorcio_con_persona(motor_migrate)
    acta = decidir_equipo(motor_app, cons)
    with motor_migrate.connect() as c:
        hoy = c.execute(text("select nucleo.hoy_del_consorcio(:c)"), {"c": cons}).scalar_one()
    codigo, out, err = correr(cons, acta, 1, hoy - timedelta(days=1))
    assert (codigo, out) == (1, "") and "fecha_invalida" in err
    codigo, out, err = correr(cons, acta, 2, hoy)
    assert codigo == 0, err
    impreso = json.loads(out)
    assert (impreso["cantidad"], impreso["desde"]) == (2, hoy.isoformat())
    assert cargas(motor_migrate, cons) == [(2, 0)]


# Migración -----------------------------------------------------------------------------------


def _paquete_del_consejo(motor) -> bool:
    with motor.connect() as c:
        return c.execute(
            text("select exists (select 1 from nucleo.paquete_facultad"
                 " where tipo = 'miembro_consejo' and facultad = 'egresos.aprobar_orden')")
        ).scalar_one()  # fmt: skip


def _existe(motor) -> bool:
    with motor.connect() as c:
        return c.execute(
            text("select to_regclass('nucleo.aprobaciones_orden_pago') is not null")
        ).scalar_one()


def _guarda_niega_aprobar_ordenes(motor) -> bool:
    with motor.connect() as c:
        tx = c.begin()
        try:
            c.execute(text("insert into nucleo.paquete_facultad (tipo, facultad)"
                           " values ('administrador', 'egresos.aprobar_orden')"))  # fmt: skip
        except DBAPIError as error:
            return error.orig.diag.message_hint == "facultad_sin_decidir"
        finally:
            tx.rollback()
    return False


FUNCIONES_CAMBIADAS = (
    "nucleo.negar_facultad_sin_decidir()",
    "nucleo.nueva_version_orden_pago(uuid, uuid, integer, bigint, text, text, text, text, boolean,"
    " uuid, uuid)",
    "nucleo.registrar_aprobacion_orden_pago(uuid, integer, bytea, uuid, text, uuid)",
)


def _definiciones(motor) -> list[str]:
    with motor.connect() as c:
        return [
            c.execute(
                text("select pg_get_functiondef(cast(:f as regprocedure))"), {"f": f}
            ).scalar_one()
            for f in FUNCIONES_CAMBIADAS
        ]


def test_0046_ida_y_vuelta(base_aparte):  # noqa: F811
    cfg, motor = base_aparte
    command.upgrade(cfg, "0045")
    en_0045 = _definiciones(motor)
    assert (_existe(motor), _paquete_del_consejo(motor), _guarda_niega_aprobar_ordenes(motor)) == (
        False, False, True,
    )  # fmt: skip
    command.upgrade(cfg, "0046")
    assert (_existe(motor), _paquete_del_consejo(motor), _guarda_niega_aprobar_ordenes(motor)) == (
        True, True, False,
    )  # fmt: skip
    # Control: las tres cambian al subir (si no, la igualdad de abajo no probaría nada).
    assert all(a != b for a, b in zip(en_0045, _definiciones(motor), strict=True))
    command.downgrade(cfg, "0045")
    with motor.connect() as c:
        columna = c.execute(
            text("select count(*) from information_schema.columns where table_schema = 'nucleo'"
                 " and table_name = 'orden_pago_version'"
                 " and column_name = 'aprobaciones_requeridas'")
        ).scalar_one()  # fmt: skip
        funciones = c.execute(
            text("select count(*) from pg_proc where proname in"
                 " ('fijar_aprobaciones_orden_pago', 'aprobaciones_requeridas_orden_pago')")
        ).scalar_one()  # fmt: skip
    assert (_existe(motor), _paquete_del_consejo(motor), _guarda_niega_aprobar_ordenes(motor)) == (
        False, False, True,
    )  # fmt: skip
    assert (columna, funciones) == (0, 0)
    assert _definiciones(motor) == en_0045  # las tres vuelven exactamente a como estaban
    command.upgrade(cfg, "0046")  # control: vuelve a subir limpio
    assert _existe(motor) and _paquete_del_consejo(motor)


def test_0046_no_sube_con_ordenes(base_aparte):  # noqa: F811
    cfg, motor = base_aparte
    command.upgrade(cfg, "0045")
    cons, _ = _consorcio_y_unidad(motor)
    quien = _persona(motor, "Sintetica")
    with motor.begin() as c:
        orden = c.execute(
            text("insert into nucleo.orden_pago (consorcio_id, version_vigente, preparada_por)"
                 " values (:c, 1, :p) returning id"), {"c": cons, "p": quien},
        ).scalar_one()  # fmt: skip
    with pytest.raises(DBAPIError) as error:
        command.upgrade(cfg, "0046")
    assert error.value.orig.diag.message_hint == "upgrade_con_datos"
    assert not _existe(motor)
    with motor.begin() as c:
        c.execute(text("delete from nucleo.orden_pago where id = :o"), {"o": orden})
    command.upgrade(cfg, "0046")  # control: sin la orden, sube
    assert _existe(motor)


@pytest.mark.parametrize("dato", ["carga", "mandato"])
def test_0046_no_baja_con_cargas_ni_mandatos_que_aprueban(base_aparte, dato):  # noqa: F811
    """Bajar dejaría la carga sin tabla o el mandato con una facultad que la guarda niega.
    Control: sin el mandato, baja. La carga no se puede borrar (solo se agrega): su control es la
    ida y vuelta sin cargas."""
    cfg, motor = base_aparte
    command.upgrade(cfg, "0046")
    cons, _ = _consorcio_y_unidad(motor)
    with motor.begin() as c:
        acta = c.execute(
            text("insert into nucleo.decision_formal (consorcio_id, tipo, fecha, descripcion,"
                 " referencia_acta, origen) values (:c, 'asamblea', current_date, 'SINTETICO',"
                 " 'Acta SINTETICA', 'soporte') returning id"), {"c": cons},
        ).scalar_one()  # fmt: skip
        if dato == "carga":
            c.execute(
                text("insert into nucleo.aprobaciones_orden_pago (consorcio_id, cantidad,"
                     " vigente_desde, decision_id) values (:c, 2, current_date, :d)"),
                {"c": cons, "d": acta},
            )  # fmt: skip
            borrar = None
        else:
            mandato = c.execute(
                # Sin cuenta, como en las pruebas de 0044 (el Consejo exige una persona).
                text("insert into nucleo.mandato (consorcio_id, tipo, mandatario_nombre,"
                     " mandatario_cuit, decision_id, vigente_desde, origen) values (:c,"
                     " 'administrador_externo', 'SINTETICO', '30000000007', :d, current_date,"
                     " 'soporte') returning id"),
                {"c": cons, "d": acta},
            ).scalar_one()  # fmt: skip
            c.execute(
                text("insert into nucleo.facultad_otorgada (consorcio_id, mandato_id, facultad,"
                     " decision_id) values (:c, :m, 'egresos.aprobar_orden', :d)"),
                {"c": cons, "m": mandato, "d": acta},
            )  # fmt: skip
            borrar = mandato
    with pytest.raises(DBAPIError) as error:
        command.downgrade(cfg, "0045")
    assert error.value.orig.diag.message_hint == "downgrade_con_datos"
    assert _existe(motor) and _paquete_del_consejo(motor)
    if borrar is not None:
        with motor.begin() as c:
            c.execute(text("delete from nucleo.facultad_otorgada where mandato_id = :m"),
                      {"m": borrar})  # fmt: skip
            c.execute(text("delete from nucleo.mandato where id = :m"), {"m": borrar})
        command.downgrade(cfg, "0045")  # control
        assert not _existe(motor)
