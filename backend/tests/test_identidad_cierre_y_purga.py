"""Cierre de todas las sesiones de una persona (RN22) y purga de sesiones y enlaces, contra
PostgreSQL 18 y Redis 7 reales (documento 13, §10.4 y §15.6; plan H2 2a).

Las sesiones se crean por el camino real (pedir y canjear el enlace), con los navegadores de
`test_identidad.py`; la purga prepara filas viejas con `cem_migrate`.
"""

import secrets
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db import transaccion
from app.identidad import sesiones
from app.plataforma import cola, tareas
from tests.conftest import alembic_config, vaciar_mandatos, volver_a_subir
from tests.test_identidad import (  # noqa: F401  (`app` es un fixture)
    CANJE,
    SESION,
    app,
    es_401,
    h,
    navegador,
    pedir,
    ultimo_token,
)
from tests.test_identidad_tabla import nueva_persona

pytestmark = [pytest.mark.db, pytest.mark.redis]

FUNCIONES = ("nucleo.revocar_sesiones_de(uuid)", "nucleo.purgar_identidad(interval)")


def entrar_como(app, motor_migrate, correo: str, agente: str | None = None) -> TestClient:  # noqa: F811
    c = navegador(app, headers={"user-agent": agente} if agente else None)
    assert pedir(c, correo).status_code == 202
    r = c.post(CANJE, json={"token": ultimo_token(motor_migrate, correo)})
    assert r.status_code == 200, r.text
    return c


# RN22: revocar todas las de una persona ---------------------------------------------------


def test_revocar_sesiones_de_una_persona_las_cierra_todas_y_solo_esas(app, motor_migrate,  # noqa: F811
                                                                      motor_app):  # fmt: skip
    """El enganche que llamará el módulo de mandatos: como `cem_app`, sin la persona afectada
    en el ámbito (quien cambia el mandato es otra)."""
    afectada, ana = nueva_persona(motor_migrate)
    _, beto = nueva_persona(motor_migrate)
    suyas = [entrar_como(app, motor_migrate, ana) for _ in range(2)]
    de_beto = entrar_como(app, motor_migrate, beto)
    with transaccion(motor_app, persona_id=None) as conn:
        revocadas = conn.execute(
            text("select nucleo.revocar_sesiones_de(:p)"), {"p": afectada}
        ).scalar_one()
    assert revocadas == 2
    assert all(es_401(c.get(SESION), "no_autenticado") for c in suyas)
    assert de_beto.get(SESION).status_code == 200
    with transaccion(motor_app, persona_id=None) as conn:
        assert (
            conn.execute(
                text("select nucleo.revocar_sesiones_de(:p)"), {"p": afectada}
            ).scalar_one()
            == 0
        )
    with (
        transaccion(motor_app, persona_id=None) as conn,
        pytest.raises(Exception, match="falta la persona"),
    ):
        conn.execute(text("select nucleo.revocar_sesiones_de(null)"))


# Purga ------------------------------------------------------------------------------------


def _insertar_sesion(conn, persona: UUID, creada, ultimo_uso, vence, revocada=None) -> UUID:
    return conn.execute(
        text(
            "insert into nucleo.sesion (persona_id, token_hash, dispositivo, ip, creada_en,"
            " ultimo_uso_en, vence_en, revocada_en) values (:p, :t, gen_random_uuid(),"
            " '198.51.100.9', now() - :c, now() - :u, now() - :v,"
            " now() - cast(:r as interval)) returning id"
        ),
        {"p": persona, "t": h(secrets.token_urlsafe(32)), "c": creada, "u": ultimo_uso,
         "v": vence, "r": revocada},
    ).scalar_one()  # fmt: skip


def _insertar_enlace(conn, persona: UUID, vence, usado=None) -> UUID:
    return conn.execute(
        text(
            "insert into nucleo.enlace_acceso (persona_id, token_hash, nonce_hash, dispositivo,"
            " ip, creado_en, vence_en, usado_en) values (:p, :t, :n, gen_random_uuid(),"
            " '198.51.100.9', now() - :v - interval '15 minutes', now() - :v,"
            " now() - cast(:u as interval)) returning id"
        ),
        {"p": persona, "t": h(secrets.token_urlsafe(32)), "n": h(secrets.token_urlsafe(32)),
         "v": vence, "u": usado},
    ).scalar_one()  # fmt: skip


def test_la_purga_diaria_borra_lo_viejo_y_conserva_lo_reciente_y_lo_vigente(
    motor_migrate, motor_app, datos
):
    persona, _ = nueva_persona(motor_migrate)
    ret, dia, mes = sesiones.RETENCION_ACCESO, timedelta(days=1), timedelta(days=30)
    with motor_migrate.begin() as conn:
        s = {
            "vigente": _insertar_sesion(conn, persona, dia, dia, -mes + dia),
            "vencida_hace_poco": _insertar_sesion(conn, persona, ret + mes, ret, ret - dia),
            "vencida_vieja": _insertar_sesion(conn, persona, ret + 2 * mes, ret + mes + dia,
                                              ret + dia),
            # Revocada hace más de la retención pero vencida hace menos: cuenta la revocación.
            "revocada_vieja": _insertar_sesion(conn, persona, ret + mes, ret + dia,
                                               ret - mes + 2 * dia, revocada=ret + dia),
            "revocada_hace_poco": _insertar_sesion(conn, persona, dia, dia, -mes + dia,
                                                   revocada=dia),
        }  # fmt: skip
        e = {
            "vigente": _insertar_enlace(conn, persona, -timedelta(minutes=10)),
            "vencido_hace_poco": _insertar_enlace(conn, persona, ret - dia),
            "vencido_viejo": _insertar_enlace(conn, persona, ret + dia),
            "usado_viejo": _insertar_enlace(
                conn, persona, ret + dia, usado=ret + dia + timedelta(minutes=5)
            ),
        }
    trabajo = cola.Trabajo(uuid4(), "plataforma.purgar_cola", None, {}, 1, 3)
    # Como en cem-trabajos: cem_app sin persona ni consorcio en el ámbito.
    with transaccion(motor_app, persona_id=None) as conn:
        salida = tareas.purgar_cola(conn, trabajo)
    with motor_migrate.connect() as conn:
        sesiones_quedan = set(conn.execute(
            text("select id from nucleo.sesion where persona_id = :p"), {"p": persona}
        ).scalars())  # fmt: skip
        enlaces_quedan = set(conn.execute(
            text("select id from nucleo.enlace_acceso where persona_id = :p"), {"p": persona}
        ).scalars())  # fmt: skip
    assert sesiones_quedan == {s["vigente"], s["vencida_hace_poco"], s["revocada_hace_poco"]}
    assert enlaces_quedan == {e["vigente"], e["vencido_hace_poco"]}
    assert (salida["sesiones_purgadas"], salida["enlaces_purgados"]) == (2, 2)


def test_la_purga_rechaza_una_retencion_negativa(motor_app, datos):
    with (
        transaccion(motor_app, persona_id=None) as conn,
        pytest.raises(Exception, match="retención inválida"),
    ):
        conn.execute(text("select * from nucleo.purgar_identidad(interval '-1 day')"))


# Funciones y migración --------------------------------------------------------------------


def test_las_funciones_nuevas_son_security_definer_con_search_path_fijo(motor_app, datos):
    with motor_app.connect() as conn:
        for firma in FUNCIONES:
            definer, config, publico = conn.execute(
                text(
                    "select prosecdef, proconfig, has_function_privilege('public', oid, 'execute')"
                    " from pg_proc where oid = cast(:f as regprocedure)"
                ),
                {"f": firma},
            ).one()
            assert definer and config == ["search_path=pg_catalog, pg_temp"], firma
            assert publico is False, firma


def test_la_migracion_0005_baja_y_sube(base_de_prueba, motor_migrate):
    def existe() -> tuple[bool, ...]:
        with motor_migrate.connect() as conn:
            return tuple(
                conn.execute(
                    text(
                        "select to_regprocedure(:f1) is not null, to_regprocedure(:f2) is not null,"
                        " to_regclass('nucleo.sesion') is not null"
                    ),
                    dict(zip(("f1", "f2"), FUNCIONES, strict=True)),
                ).one()
            )

    assert existe() == (True, True, True)
    cfg = alembic_config()
    vaciar_mandatos(motor_migrate)
    command.downgrade(cfg, "0004")
    assert existe() == (False, False, True)  # la 0004 queda: solo se van las funciones
    volver_a_subir(cfg, motor_migrate)  # bajó por debajo de 0007
    assert existe() == (True, True, True)
