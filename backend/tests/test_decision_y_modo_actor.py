"""Issue #66: el actor y el origen se atan a la sesión (0013).

`registrar_decision` y `cambiar_modo` reciben el mismo control que `alta_mandato` y
`baja_mandato` en `0012`, y `baja_mandato` deja de aceptar un actor con origen `soporte`.

Cada caso negativo **pasaba** antes de `0013` (o fallaba por otro motivo, como un `check` de la
tabla), así que se exige el motivo del control nuevo. Los positivos son el control: `api` con la
persona de la sesión y `soporte` sin sesión ni actor siguen funcionando.
"""

from datetime import date
from uuid import UUID

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db import transaccion
from tests.conftest import (
    alembic_config,
    vaciar_mandatos,
    verificar_en_el_consorcio,
    volver_a_subir,
)
from tests.test_mandatos_0012 import alta

pytestmark = pytest.mark.db

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
    for persona in (p, otra):
        verificar_en_el_consorcio(motor_migrate, cons, persona)
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


def contar(motor_migrate, tabla: str, cons) -> int:
    with motor_migrate.connect() as c:
        return c.execute(
            text(f"select count(*) from {tabla} where consorcio_id = :c"), {"c": cons}
        ).scalar_one()


CASOS = [
    ("api con otra persona como actor", "p", "otra", "api", "actor_no_coincide"),
    ("api sin sesión con actor", None, "p", "api", "actor_no_coincide"),
    ("api sin actor", "p", None, "api", "actor_requerido"),
    ("api sin sesión ni actor", None, None, "api", "actor_requerido"),
    ("trabajos", None, None, "trabajos", "origen_invalido"),
    ("planificador", None, None, "planificador", "origen_invalido"),
    ("origen nulo", None, None, None, "origen_invalido"),
    ("soporte con sesión abierta", "p", None, "soporte", "origen_invalido"),
    ("soporte con actor", None, "p", "soporte", "origen_invalido"),
]


@pytest.mark.parametrize(("caso", "sesion", "actor", "origen", "motivo"), CASOS)
def test_registrar_decision_rechaza_actor_u_origen_ajeno(
    motor_migrate, motor_app, caso, sesion, actor, origen, motivo
):
    cons, p, otra = consorcio_con_dos_personas(motor_migrate)
    quien = {"p": p, "otra": otra, None: None}
    with pytest.raises(DBAPIError, match=motivo):
        registrar(motor_app, cons, quien[sesion], quien[actor], origen)
    assert contar(motor_migrate, "nucleo.decision_formal", cons) == 0, caso


@pytest.mark.parametrize(("caso", "sesion", "actor", "origen", "motivo"), CASOS)
def test_cambiar_modo_rechaza_actor_u_origen_ajeno(
    motor_migrate, motor_app, caso, sesion, actor, origen, motivo
):
    """Desde 0027 (RF41-C, C7) el modo lo registra solo el equipo: todo lo que no es soporte
    sin sesión ni actor es `origen_invalido`, también un pedido `api` bien formado."""
    cons, p, otra = consorcio_con_dos_personas(motor_migrate)
    decision = registrar(motor_app, cons, None, None, "soporte")
    quien = {"p": p, "otra": otra, None: None}
    with pytest.raises(DBAPIError, match="origen_invalido"):
        cambiar(motor_app, cons, quien[sesion], decision, quien[actor], origen)
    assert contar(motor_migrate, "nucleo.modo_operacion", cons) == 0, caso


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
    # El modo, en cambio, ya no por la API (0027): ni con la persona de la sesión.
    with pytest.raises(DBAPIError, match="origen_invalido"):
        cambiar(motor_app, cons, p, decision, p, "api")
    # Control: el equipo lo registra con esa decisión.
    cambiar(motor_app, cons, None, decision, None, "soporte")
    # S2b (0043): el modo lo cambió soporte y no deja evento; la decisión por la API, sí.
    assert eventos(motor_migrate, cons) == [("consorcio.decision.registrar", "api", p)]


def test_soporte_sin_sesion_ni_actor_sigue_funcionando(motor_migrate, motor_app):
    cons, _, _ = consorcio_con_dos_personas(motor_migrate)
    decision = registrar(motor_app, cons, None, None, "soporte")
    cambiar(motor_app, cons, None, decision, None, "soporte")
    assert eventos(motor_migrate, cons) == []  # S2b (0043)


def dar_de_baja_por_soporte(motor_app, cons, mandato, actor):
    with transaccion(motor_app, persona_id=None, consorcio_id=cons) as c:
        return c.execute(
            text(
                "select nucleo.baja_mandato(:m, 'renuncia', null, 'Nota de renuncia de prueba',"
                " 'Renuncia', :a, 'soporte')"
            ),
            {"m": mandato, "a": actor},
        ).scalar_one()


def finalizado(motor_migrate, mandato) -> tuple[str | None, UUID | None]:
    with motor_migrate.connect() as c:
        return tuple(
            c.execute(
                text("select causa_baja::text, finalizado_por from nucleo.mandato where id = :m"),
                {"m": mandato},
            ).one()
        )


def test_baja_mandato_por_soporte_no_acepta_un_actor(motor_migrate, motor_app):
    cons, p, otra = consorcio_con_dos_personas(motor_migrate)
    # La decisión la carga el equipo: A15 (0016) no deja que `p` use una que registró.
    decision = registrar(motor_app, cons, None, None, "soporte")
    mandato = alta(motor_app, cons, p, decision, persona=otra, tipo="miembro_consejo")
    with pytest.raises(DBAPIError, match="origen_invalido"):
        dar_de_baja_por_soporte(motor_app, cons, mandato, p)
    assert finalizado(motor_migrate, mandato) == (None, None)
    # Control: la misma baja por soporte sin actor se registra, sin nadie en finalizado_por.
    dar_de_baja_por_soporte(motor_app, cons, mandato, None)
    assert finalizado(motor_migrate, mandato) == ("renuncia", None)


def test_los_permisos_de_las_funciones_no_cambian(motor_migrate):
    with motor_migrate.connect() as c:
        filas = c.execute(
            text(
                "select p.proname, p.prosecdef, p.proconfig, pg_get_userbyid(p.proowner),"
                " has_function_privilege('cem_app', p.oid, 'execute'),"
                " has_function_privilege('public', p.oid, 'execute')"
                " from pg_proc p join pg_namespace n on n.oid = p.pronamespace"
                " where n.nspname = 'nucleo'"
                " and p.proname in ('registrar_decision', 'cambiar_modo', 'baja_mandato')"
                " order by 1"
            )
        ).all()
    camino = ["search_path=pg_catalog, pg_temp"]
    assert [tuple(f) for f in filas] == [
        ("baja_mandato", True, camino, "cem_migrate", True, False),
        ("cambiar_modo", True, camino, "cem_migrate", True, False),
        ("registrar_decision", True, camino, "cem_migrate", True, False),
    ]


def con_el_control(motor_migrate) -> dict[str, bool]:
    with motor_migrate.connect() as c:
        return dict(
            c.execute(
                text(
                    "select p.proname, p.prosrc like '%#66%'"
                    " from pg_proc p join pg_namespace n on n.oid = p.pronamespace"
                    " where n.nspname = 'nucleo'"
                    " and p.proname in ('registrar_decision', 'cambiar_modo', 'baja_mandato')"
                )
            ).all()
        )


def test_el_downgrade_restaura_las_tres_funciones_y_el_upgrade_las_vuelve_a_atar(
    motor_migrate, motor_app
):
    cons, p, otra = consorcio_con_dos_personas(motor_migrate)
    todas = {"registrar_decision": True, "cambiar_modo": True, "baja_mandato": True}
    assert con_el_control(motor_migrate) == todas
    vaciar_mandatos(motor_migrate)  # la guarda de 0012 no participa: 0013 no la toca
    cfg = alembic_config()
    command.downgrade(cfg, "0012")
    try:
        assert con_el_control(motor_migrate) == dict.fromkeys(todas, False)
        # Antes de 0013 el actor ajeno pasaba: es exactamente el defecto del issue.
        registrar(motor_app, cons, p, otra, "api")
    finally:
        volver_a_subir(cfg, motor_migrate)
    assert con_el_control(motor_migrate) == todas
    with pytest.raises(DBAPIError, match="actor_no_coincide"):
        registrar(motor_app, cons, p, otra, "api")
