"""Una decisión formal no puede tener fecha futura (migración 0019, punto 4 de §7 de 2b-4b).

Decisión del propietario del 19-10-2026: con el día local del consorcio, hasta hoy se admite y
desde mañana se rechaza con `fecha_futura` (422), por la API y por soporte. La ruta lo niega
antes de la base; la base lo repite como segunda barrera. Cada rechazo tiene su control (la
misma decisión con fecha de hoy pasa).
"""

from datetime import date, timedelta
from uuid import UUID

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.consorcios import mandatos as rutas
from app.db import transaccion
from app.errores import ErrorApi
from tests.conftest import clave_idempotencia
from tests.test_contexto_mandatos import cliente
from tests.test_mandatos import otra_persona
from tests.test_mandatos_0012 import alta
from tests.test_modo_operacion import consorcio_con_persona, decidir_equipo, zona_con_otra_fecha
from tests.test_vinculo_0014 import base_aparte  # noqa: F401  (fixture)

pytestmark = pytest.mark.db

DIA = timedelta(days=1)
REGISTRAR = (
    "select nucleo.registrar_decision('asamblea', :f, 'Designa al Consejo',"
    " 'Acta 14, libro 3, folio 52', :a, :o)"
)


def hoy_en(motor, cons: UUID) -> date:
    with motor.connect() as c:
        return c.execute(text("select nucleo.hoy_del_consorcio(:c)"), {"c": cons}).scalar_one()


def hoy_utc(motor) -> date:
    with motor.connect() as c:
        return c.execute(text("select (now() at time zone 'UTC')::date")).scalar_one()


def decisiones(motor, cons: UUID) -> int:
    with motor.connect() as c:
        return c.execute(
            text("select count(*) from nucleo.decision_formal where consorcio_id = :c"),
            {"c": cons},
        ).scalar_one()


def registrar(motor_app, cons: UUID, actor: UUID | None, fecha: date) -> UUID:
    origen = "soporte" if actor is None else "api"
    with transaccion(motor_app, persona_id=actor, consorcio_id=cons) as c:
        return c.execute(text(REGISTRAR), {"f": fecha, "a": actor, "o": origen}).scalar_one()


def motivo_de(motor_app, cons: UUID, actor: UUID | None, fecha: date) -> str:
    with pytest.raises(DBAPIError) as error:
        registrar(motor_app, cons, actor, fecha)
    return error.value.orig.diag.message_hint


@pytest.fixture
def escena(motor_migrate, motor_app):
    """Un consorcio con un miembro del Consejo, que tiene `decisiones.registrar` (C2)."""
    cons, _, _ = consorcio_con_persona(motor_migrate)
    acta = decidir_equipo(motor_app, cons)
    mc = otra_persona(motor_migrate, cons)
    alta(motor_app, cons, None, acta, persona=mc, tipo="miembro_consejo", origen="soporte")
    return {"cons": cons, "mc": mc}


# Base ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("por_soporte", [False, True], ids=["api", "soporte"])
def test_hasta_hoy_se_registra_y_desde_manana_no(escena, motor_migrate, motor_app, por_soporte):
    cons = escena["cons"]
    actor = None if por_soporte else escena["mc"]
    hoy = hoy_en(motor_migrate, cons)
    registrar(motor_app, cons, actor, hoy - DIA)  # control: un acto de ayer
    registrar(motor_app, cons, actor, hoy)  # control: el de hoy, el borde
    antes = decisiones(motor_migrate, cons)
    assert motivo_de(motor_app, cons, actor, hoy + DIA) == "fecha_futura"
    assert motivo_de(motor_app, cons, actor, hoy + 400 * DIA) == "fecha_futura"
    assert decisiones(motor_migrate, cons) == antes  # el rechazo no deja nada


def test_la_base_compara_con_el_dia_del_consorcio_y_no_con_el_utc(motor_migrate, motor_app):
    """En una zona cuyo día difiere del de GMT ahora mismo: su hoy pasa y su mañana no. Con el
    día GMT, uno de los dos casos daría lo contrario (hoy GMT + 1 en Kiritimati es su hoy; hoy
    GMT en Pago Pago es su mañana)."""
    zona, desfase = zona_con_otra_fecha(motor_app)
    cons, _, _ = consorcio_con_persona(motor_migrate, zona)
    hoy = hoy_en(motor_migrate, cons)
    assert hoy == hoy_utc(motor_migrate) + desfase * DIA  # control: la zona sí difiere
    registrar(motor_app, cons, None, hoy)
    assert motivo_de(motor_app, cons, None, hoy + DIA) == "fecha_futura"


def test_la_ruta_traduce_el_motivo_de_la_base(escena, motor_migrate, motor_app):
    """Si una fecha futura llegara a la base (la ruta la niega antes), el motivo de la segunda
    barrera se responde como el de la primera."""
    cons, mc = escena["cons"], escena["mc"]
    manana = hoy_en(motor_migrate, cons) + DIA
    with pytest.raises(ErrorApi) as error:
        with transaccion(motor_app, persona_id=mc, consorcio_id=cons) as c:
            rutas._llamar(c, REGISTRAR, {"f": manana, "a": mc, "o": "api"})
    assert (error.value.estado, error.value.codigo) == (422, "fecha_futura")


# Ruta ---------------------------------------------------------------------------------------


def _post(persona: UUID, cons: UUID, fecha: date):
    with cliente(persona) as c:
        return c.post(
            f"/api/v1/consorcios/{cons}/decisiones",
            json={"tipo": "asamblea", "fecha": str(fecha), "descripcion": "Designa al Consejo",
                  "referencia": "Acta 14, libro 3, folio 52"},
            headers=clave_idempotencia(),
        )  # fmt: skip


@pytest.mark.redis
def test_la_ruta_niega_manana_sin_llegar_a_la_base(escena, motor_migrate, monkeypatch):
    cons, mc = escena["cons"], escena["mc"]
    hoy = hoy_en(motor_migrate, cons)
    r = _post(mc, cons, hoy)  # control: hoy pasa por la ruta y por la base
    assert r.status_code == 201, r.text
    antes = decisiones(motor_migrate, cons)
    _sin_base(monkeypatch)
    r = _post(mc, cons, hoy + DIA)
    assert (r.status_code, r.json()["codigo"]) == (422, "fecha_futura")
    assert decisiones(motor_migrate, cons) == antes


def _sin_base(monkeypatch):
    def no_llega(*_):
        raise AssertionError("una fecha futura no llega a la base")

    monkeypatch.setattr(rutas, "_llamar", no_llega)


@pytest.mark.redis
def test_la_ruta_usa_el_dia_del_consorcio(motor_migrate, motor_app, monkeypatch):
    """Con el día GMT, la ruta negaría el hoy de Kiritimati o dejaría pasar a la base el mañana
    de Pago Pago (que es el hoy GMT)."""
    zona, _ = zona_con_otra_fecha(motor_app)
    cons, _, _ = consorcio_con_persona(motor_migrate, zona)
    acta = decidir_equipo(motor_app, cons)
    mc = otra_persona(motor_migrate, cons)
    alta(motor_app, cons, None, acta, persona=mc, tipo="miembro_consejo", origen="soporte")
    hoy = hoy_en(motor_migrate, cons)
    assert _post(mc, cons, hoy).status_code == 201
    _sin_base(monkeypatch)
    r = _post(mc, cons, hoy + DIA)
    assert (r.status_code, r.json()["codigo"]) == (422, "fecha_futura")


# Upgrade y downgrade ------------------------------------------------------------------------


def _registrar_decision(motor) -> str:
    with motor.connect() as c:
        return c.execute(
            text("select pg_get_functiondef('nucleo.registrar_decision'::regproc)")
        ).scalar_one()


def test_el_upgrade_se_niega_con_decisiones_futuras_y_el_downgrade_restaura(base_aparte):  # noqa: F811
    """En una zona cuyo día difiere del de GMT: con el día GMT, la guarda dejaría pasar el
    mañana de Pago Pago o negaría el hoy de Kiritimati (el control)."""
    cfg, motor = base_aparte
    command.upgrade(cfg, "0018")
    antes = _registrar_decision(motor)
    zona, _ = zona_con_otra_fecha(motor)
    with motor.begin() as c:
        cons = c.execute(
            text("insert into nucleo.consorcio (nombre, zona_horaria) values ('A', :z)"
                 " returning id"), {"z": zona},
        ).scalar_one()  # fmt: skip
        futura = c.execute(
            text("insert into nucleo.decision_formal (consorcio_id, tipo, fecha, descripcion,"
                 " referencia_acta, origen) values (:c, 'asamblea',"
                 " nucleo.hoy_del_consorcio(:c) + 1, 'x', 'Acta', 'soporte') returning id"),
            {"c": cons},
        ).scalar_one()  # fmt: skip
    with pytest.raises(DBAPIError) as error:
        command.upgrade(cfg, "0019")
    assert error.value.orig.diag.message_hint == "decisiones_futuras_previas"
    with motor.connect() as c:
        version = c.execute(text("select version_num from plataforma.alembic_version"))
        assert version.scalar_one() == "0018"
    with motor.begin() as c:  # control: con la decisión de hoy, el upgrade pasa
        c.execute(
            text("update nucleo.decision_formal set fecha = nucleo.hoy_del_consorcio(:c)"
                 " where id = :d"), {"c": cons, "d": futura},
        )  # fmt: skip
    command.upgrade(cfg, "0019")
    despues = _registrar_decision(motor)
    assert despues != antes and "fecha_futura" in despues
    command.downgrade(cfg, "0018")
    assert _registrar_decision(motor) == antes  # la bajada deja la función de 0016
