"""P3 80 y P3 81, decisión del propietario del 29-10-2026 (`0047`). Datos SINTETICOS / PRUEBA /
NO REAL.

- P3 80: la cantidad de aprobaciones solo se carga con un acta de asamblea o del Consejo.
- P3 81: `egresos.aprobar_orden` solo en el paquete de `miembro_consejo` y solo en mandatos de
  ese tipo.
"""

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.test_aprobaciones_orden_pago_0046 import cargas, fijar
from tests.test_modo_operacion import consorcio_con_persona, decidir_equipo
from tests.test_orden_pago_neutra import e  # noqa: F401  (`e` es un fixture)
from tests.test_vinculo_0014 import (  # noqa: F401  (`base_aparte` es un fixture)
    _consorcio_y_unidad,
    base_aparte,
)

pytestmark = pytest.mark.db

FACULTAD = "egresos.aprobar_orden"


# P3 80 ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("tipo", ["contrato_administracion", "inscripcion_rpa"])
def test_la_cantidad_no_se_carga_con_algo_que_no_es_un_acta(motor_migrate, motor_app, tipo):
    cons, _, _ = consorcio_con_persona(motor_migrate)
    with pytest.raises(DBAPIError) as error:
        fijar(motor_app, cons, decision=decidir_equipo(motor_app, cons, tipo))
    assert error.value.orig.diag.message_hint == "decision_no_es_acta"
    assert cargas(motor_migrate, cons) == []


@pytest.mark.parametrize("tipo", ["asamblea", "consejo"])
def test_la_cantidad_se_carga_con_un_acta_de_asamblea_o_del_consejo(motor_migrate, motor_app, tipo):
    cons, _, _ = consorcio_con_persona(motor_migrate)
    fijar(motor_app, cons, decision=decidir_equipo(motor_app, cons, tipo))
    assert cargas(motor_migrate, cons) == [(2, 0)]


def test_los_tipos_de_decision_son_los_que_conoce_la_prueba(motor_migrate):
    """Si se suma un tipo de decisión, esta prueba obliga a decidir si respalda la cantidad."""
    with motor_migrate.connect() as c:
        tipos = c.execute(
            text("select enum_range(null::nucleo.tipo_decision)::text[]")
        ).scalar_one()
    assert sorted(tipos) == ["asamblea", "consejo", "contrato_administracion", "inscripcion_rpa"]


# P3 81 ---------------------------------------------------------------------------------------


def _intenta(motor, sentencia, params=None) -> str | None:
    """Corre la sentencia y deshace siempre. Devuelve el motivo del rechazo (o su sqlstate, si no
    tiene motivo: un choque de unicidad no pasa por None), o None si se ejecutó."""
    with motor.connect() as c:
        tx = c.begin()
        try:
            c.execute(text(sentencia), params or {})
        except DBAPIError as error:
            return error.orig.diag.message_hint or error.orig.diag.sqlstate
        finally:
            tx.rollback()
    return None


def test_ningun_otro_paquete_recibe_aprobar_ordenes(motor_migrate):
    with motor_migrate.connect() as c:
        tipos = c.execute(text("select enum_range(null::nucleo.tipo_mandato)::text[]")).scalar_one()
    otros = [t for t in tipos if t != "miembro_consejo"]
    assert "administrador" in otros and "administrador_externo" in otros  # control de la lista
    for tipo in otros:
        motivo = _intenta(
            motor_migrate,
            "insert into nucleo.paquete_facultad (tipo, facultad) values (:t, :f)",
            {"t": tipo, "f": FACULTAD},
        )
        assert motivo == "facultad_solo_consejo", tipo
    # Control: la guarda mira solo esa facultad.
    assert _intenta(
        motor_migrate,
        "insert into nucleo.paquete_facultad (tipo, facultad)"
        " values ('administrador', 'fondo_reserva.autorizar')",
    ) is None  # fmt: skip


def _mandato_de(motor, cons, persona) -> tuple:
    with motor.connect() as c:
        return c.execute(
            text("select id, decision_id, tipo::text from nucleo.mandato"
                 " where consorcio_id = :c and persona_id = :p"),
            {"c": cons, "p": persona},
        ).one()  # fmt: skip


OTORGAR = (
    "insert into nucleo.facultad_otorgada (consorcio_id, mandato_id, facultad, decision_id)"
    " values (:c, :m, :f, :d)"
)


def test_solo_un_mandato_del_consejo_recibe_aprobar_ordenes(motor_migrate, e):  # noqa: F811
    m_adm, acta, tipo = _mandato_de(motor_migrate, e.cons, e.adm)
    assert tipo != "miembro_consejo"
    assert _intenta(
        motor_migrate, OTORGAR, {"c": e.cons, "m": m_adm, "f": FACULTAD, "d": acta}
    ) == "facultad_solo_consejo"  # fmt: skip
    # Control: otra facultad que el mandato no tiene pasa (la guarda mira solo aprobar órdenes).
    assert _intenta(
        motor_migrate, OTORGAR,
        {"c": e.cons, "m": m_adm, "f": "liquidacion.revisar", "d": acta},
    ) is None  # fmt: skip


def test_un_mandato_que_aprueba_no_cambia_de_tipo(motor_migrate, e):  # noqa: F811
    m_mc, _, tipo = _mandato_de(motor_migrate, e.cons, e.mc1)
    assert tipo == "miembro_consejo"
    with motor_migrate.connect() as c:
        tiene = c.execute(
            text("select exists (select 1 from nucleo.facultad_otorgada"
                 " where mandato_id = :m and facultad = :f)"), {"m": m_mc, "f": FACULTAD},
        ).scalar_one()  # fmt: skip
    assert tiene  # el alta copió el paquete del Consejo
    cambiar = "update nucleo.mandato set tipo = :t where id = :m"
    assert _intenta(motor_migrate, cambiar, {"t": "administrador", "m": m_mc}) == (
        "facultad_solo_consejo"
    )
    # Control: un mandato sin la facultad no lo frena esta guarda.
    m_adm, _, tipo_adm = _mandato_de(motor_migrate, e.cons, e.adm)
    assert _intenta(motor_migrate, cambiar, {"t": tipo_adm, "m": m_adm}) is None


def test_tampoco_se_saca_del_consejo_con_un_update(motor_migrate, e):  # noqa: F811
    """Revisión de app #164: el trigger mira todas las columnas, no solo `facultad`."""
    m_adm, _, _ = _mandato_de(motor_migrate, e.cons, e.adm)
    m_mc, _, _ = _mandato_de(motor_migrate, e.cons, e.mc1)
    with motor_migrate.connect() as c:
        filas = c.execute(
            text("select count(*) from nucleo.facultad_otorgada where (mandato_id = :adm"
                 " and facultad = 'egresos.preparar_orden') or (mandato_id = :mc"
                 " and facultad = :f)"), {"adm": m_adm, "mc": m_mc, "f": FACULTAD},
        ).scalar_one()  # fmt: skip
    assert filas == 2  # control: los UPDATE de abajo tocan una fila cada uno (índice único)
    casos = {
        "facultad nueva en un mandato del administrador": (
            "update nucleo.facultad_otorgada set facultad = :f"
            " where mandato_id = :adm and facultad = 'egresos.preparar_orden'"
        ),
        "la del Consejo movida a otro mandato": (
            "update nucleo.facultad_otorgada set mandato_id = :adm"
            " where mandato_id = :mc and facultad = :f"
        ),
        "la fila del paquete pasada a otro tipo": (
            "update nucleo.paquete_facultad set tipo = 'administrador'"
            " where tipo = 'miembro_consejo' and facultad = :f"
        ),
    }
    for caso, sentencia in casos.items():
        motivo = _intenta(motor_migrate, sentencia, {"f": FACULTAD, "adm": m_adm, "mc": m_mc})
        assert motivo == "facultad_solo_consejo", caso
    # Control: el mismo UPDATE con una facultad que el mandato no tiene pasa.
    assert _intenta(
        motor_migrate,
        "update nucleo.facultad_otorgada set facultad = 'liquidacion.revisar'"
        " where mandato_id = :adm and facultad = 'egresos.preparar_orden'",
        {"adm": m_adm},
    ) is None  # fmt: skip


# Migración -----------------------------------------------------------------------------------

FIJAR = "nucleo.fijar_aprobaciones_orden_pago(smallint, date, uuid, uuid, text)"


def _fijar_def(motor) -> str:
    with motor.connect() as c:
        return c.execute(
            text("select pg_get_functiondef(cast(:f as regprocedure))"), {"f": FIJAR}
        ).scalar_one()


def _guardas(motor) -> int:
    with motor.connect() as c:
        return c.execute(
            text("select count(*) from pg_trigger where not tgisinternal"
                 " and tgname like '%aprobar_orden_solo_consejo'")
        ).scalar_one()  # fmt: skip


def test_0047_ida_y_vuelta(base_aparte):  # noqa: F811
    cfg, motor = base_aparte
    command.upgrade(cfg, "0046")
    en_0046 = _fijar_def(motor)
    assert _guardas(motor) == 0
    command.upgrade(cfg, "0047")
    assert "decision_no_es_acta" in _fijar_def(motor) and _guardas(motor) == 3
    command.downgrade(cfg, "0046")
    assert _fijar_def(motor) == en_0046 and _guardas(motor) == 0
    command.upgrade(cfg, "0047")  # control: vuelve a subir limpio
    assert _guardas(motor) == 3


def _decision(motor, cons, tipo):
    with motor.begin() as c:
        return c.execute(
            text("insert into nucleo.decision_formal (consorcio_id, tipo, fecha, descripcion,"
                 " referencia_acta, origen) values (:c, :t, current_date, 'SINTETICO',"
                 " 'Acta SINTETICA', 'soporte') returning id"), {"c": cons, "t": tipo},
        ).scalar_one()  # fmt: skip


def test_0047_no_sube_con_una_carga_sin_acta(base_aparte):  # noqa: F811
    """La carga no se puede borrar (solo se agrega): el control es la ida y vuelta, sin cargas."""
    cfg, motor = base_aparte
    command.upgrade(cfg, "0046")
    cons, _ = _consorcio_y_unidad(motor)
    contrato = _decision(motor, cons, "contrato_administracion")
    with motor.begin() as c:
        c.execute(
            text("insert into nucleo.aprobaciones_orden_pago (consorcio_id, cantidad,"
                 " vigente_desde, decision_id) values (:c, 2, current_date, :d)"),
            {"c": cons, "d": contrato},
        )  # fmt: skip
    with pytest.raises(DBAPIError) as error:
        command.upgrade(cfg, "0047")
    assert error.value.orig.diag.message_hint == "upgrade_con_datos"
    assert _guardas(motor) == 0


def test_0047_no_sube_con_aprobar_ordenes_fuera_del_consejo(base_aparte):  # noqa: F811
    cfg, motor = base_aparte
    command.upgrade(cfg, "0046")
    with motor.begin() as c:
        c.execute(text("insert into nucleo.paquete_facultad (tipo, facultad)"
                       " values ('administrador', :f)"), {"f": FACULTAD})  # fmt: skip
    with pytest.raises(DBAPIError) as error:
        command.upgrade(cfg, "0047")
    assert error.value.orig.diag.message_hint == "upgrade_con_datos"
    with motor.begin() as c:
        c.execute(text("delete from nucleo.paquete_facultad where tipo = 'administrador'"
                       " and facultad = :f"), {"f": FACULTAD})  # fmt: skip
    command.upgrade(cfg, "0047")  # control: sin esa fila, sube
    assert _guardas(motor) == 3


def test_0047_no_sube_con_aprobar_ordenes_en_un_mandato_que_no_es_del_consejo(
    base_aparte,  # noqa: F811
):
    cfg, motor = base_aparte
    command.upgrade(cfg, "0046")
    cons, _ = _consorcio_y_unidad(motor)
    acta = _decision(motor, cons, "asamblea")
    with motor.begin() as c:
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
                 " decision_id) values (:c, :m, :f, :d)"),
            {"c": cons, "m": mandato, "f": FACULTAD, "d": acta},
        )  # fmt: skip
    with pytest.raises(DBAPIError) as error:
        command.upgrade(cfg, "0047")
    assert error.value.orig.diag.message_hint == "upgrade_con_datos"
    with motor.begin() as c:
        c.execute(text("delete from nucleo.facultad_otorgada where mandato_id = :m"),
                  {"m": mandato})  # fmt: skip
    command.upgrade(cfg, "0047")  # control: sin esa facultad, sube
    assert _guardas(motor) == 3
