"""Issue #66: `registrar_decision` y `cambiar_modo` atan el actor y el origen a la sesión (0013).

Cada caso negativo **pasaba** antes de `0013` (o fallaba por otro motivo), así que el motivo que
se espera es el del control nuevo y no un `check` de la tabla. Los positivos son el control:
`api` con la persona de la sesión y `soporte` sin sesión ni actor siguen funcionando.
"""

from datetime import date
from uuid import UUID

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db import transaccion
from tests.conftest import alembic_config

DESDE = date(2026, 3, 1)


def consorcio_con_dos_personas(motor_migrate) -> tuple[UUID, UUID, UUID]:
    with motor_migrate.begin() as c:
        cons = c.execute(
            text("insert into nucleo.consorcio (nombre) values ('Actor 66') returning id")
        ).scalar_one()
        p, otra = (
            c.execute(
                text("insert into nucleo.persona (correo, nombre) values (:c, 'P') returning id"),
                {"c": f"actor66-{n}-{cons}@ejemplo.com"},
            ).scalar_one()
            for n in ("a", "b")
        )
    return cons, p, otra


def registrar(motor_app, cons, sesion, actor, origen):
    with transaccion(motor_app, persona_id=sesion, consorcio_id=cons) as c:
        return c.execute(
            text(
                "select nucleo.registrar_decision('asamblea', '2026-01-01', 'Prueba #66',"
                " 'Acta de prueba', :a, :o)"
            ),
            {"a": actor, "o": origen},
        ).scalar_one()


def cambiar(motor_app, cons, sesion, decision, actor, origen):
    with transaccion(motor_app, persona_id=sesion, consorcio_id=cons) as c:
        return c.execute(
            text("select nucleo.cambiar_modo('consejo', :d, :desde, :a, :o)"),
            {"d": decision, "desde": DESDE, "a": actor, "o": origen},
        ).scalar_one()


@pytest.mark.parametrize(
    ("caso", "sesion", "actor", "origen", "motivo"),
    [
        ("api con otra persona como actor", "p", "otra", "api", "actor_no_coincide"),
        ("api sin actor", "p", None, "api", "actor_no_coincide"),
        ("soporte con sesión abierta", "p", None, "soporte", "origen_invalido"),
        ("soporte con actor", None, "p", "soporte", "origen_invalido"),
    ],
)
def test_registrar_decision_rechaza_actor_u_origen_ajeno(
    motor_migrate, motor_app, caso, sesion, actor, origen, motivo
):
    cons, p, otra = consorcio_con_dos_personas(motor_migrate)
    quien = {"p": p, "otra": otra, None: None}
    with pytest.raises(DBAPIError, match=motivo):
        registrar(motor_app, cons, quien[sesion], quien[actor], origen)
    with motor_migrate.connect() as c:
        assert (
            c.execute(
                text("select count(*) from nucleo.decision_formal where consorcio_id = :c"),
                {"c": cons},
            ).scalar_one()
            == 0
        ), caso


@pytest.mark.parametrize(
    ("caso", "sesion", "actor", "origen", "motivo"),
    [
        ("api con otra persona como actor", "p", "otra", "api", "actor_no_coincide"),
        ("api sin actor", "p", None, "api", "actor_no_coincide"),
        ("trabajos", None, None, "trabajos", "origen_invalido"),
        ("planificador", None, None, "planificador", "origen_invalido"),
        ("soporte con sesión abierta", "p", None, "soporte", "origen_invalido"),
        ("soporte con actor", None, "p", "soporte", "origen_invalido"),
    ],
)
def test_cambiar_modo_rechaza_actor_u_origen_ajeno(
    motor_migrate, motor_app, caso, sesion, actor, origen, motivo
):
    cons, p, otra = consorcio_con_dos_personas(motor_migrate)
    decision = registrar(motor_app, cons, None, None, "soporte")
    quien = {"p": p, "otra": otra, None: None}
    with pytest.raises(DBAPIError, match=motivo):
        cambiar(motor_app, cons, quien[sesion], decision, quien[actor], origen)
    with motor_migrate.connect() as c:
        assert (
            c.execute(
                text("select count(*) from nucleo.modo_operacion where consorcio_id = :c"),
                {"c": cons},
            ).scalar_one()
            == 0
        ), caso


def eventos(motor_migrate, cons) -> list[tuple[str, str, UUID | None]]:
    with motor_migrate.connect() as c:
        return [
            tuple(f)
            for f in c.execute(
                text(
                    "select accion, origen, actor_persona_id from auditoria.eventos"
                    " where consorcio_id = :c order by secuencia"
                ),
                {"c": cons},
            ).all()
        ]


def test_api_con_la_persona_de_la_sesion_sigue_funcionando(motor_migrate, motor_app):
    cons, p, _ = consorcio_con_dos_personas(motor_migrate)
    decision = registrar(motor_app, cons, p, p, "api")
    cambiar(motor_app, cons, p, decision, p, "api")
    assert eventos(motor_migrate, cons) == [
        ("consorcio.decision.registrar", "api", p),
        ("consorcio.modo.cambiar", "api", p),
    ]


def test_soporte_sin_sesion_ni_actor_sigue_funcionando(motor_migrate, motor_app):
    cons, _, _ = consorcio_con_dos_personas(motor_migrate)
    decision = registrar(motor_app, cons, None, None, "soporte")
    cambiar(motor_app, cons, None, decision, None, "soporte")
    assert eventos(motor_migrate, cons) == [
        ("consorcio.decision.registrar", "soporte", None),
        ("consorcio.modo.cambiar", "soporte", None),
    ]


def test_los_permisos_de_las_funciones_no_cambian(motor_migrate):
    with motor_migrate.connect() as c:
        filas = c.execute(
            text(
                "select p.proname, p.prosecdef,"
                " has_function_privilege('cem_app', p.oid, 'execute'),"
                " has_function_privilege('public', p.oid, 'execute')"
                " from pg_proc p join pg_namespace n on n.oid = p.pronamespace"
                " where n.nspname = 'nucleo'"
                " and p.proname in ('registrar_decision', 'cambiar_modo') order by 1"
            )
        ).all()
    assert [tuple(f) for f in filas] == [
        ("cambiar_modo", True, True, False),
        ("registrar_decision", True, True, False),
    ]


def test_el_downgrade_restaura_las_funciones_anteriores_y_el_upgrade_las_vuelve_a_atar(
    motor_migrate, motor_app
):
    cons, p, otra = consorcio_con_dos_personas(motor_migrate)
    cfg = alembic_config()
    command.downgrade(cfg, "0012")
    try:
        # Antes de 0013 el actor ajeno pasaba: es exactamente el defecto del issue.
        registrar(motor_app, cons, p, otra, "api")
    finally:
        command.upgrade(cfg, "head")
    with pytest.raises(DBAPIError, match="actor_no_coincide"):
        registrar(motor_app, cons, p, otra, "api")
