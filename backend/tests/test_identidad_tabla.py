"""Tablas y funciones de identidad (migración 0004) contra PostgreSQL real, sin la API.

Documento 13, §10.3 (enlace de un solo uso, 15 minutos, ligado a un `nonce`), §10.4 (sesiones)
y §8.5 (las tablas sin consorcio se filtran por persona). Todo corre como `cem_app`, el rol de
la API, sin `BYPASSRLS`; `cem_migrate` solo prepara casos (envejecer filas, dar de baja).
"""

import hashlib
import secrets
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from alembic import command
from sqlalchemy import Connection, text
from sqlalchemy.exc import IntegrityError, ProgrammingError

from app.db import transaccion
from tests.conftest import alembic_config, vaciar_mandatos, volver_a_subir

pytestmark = pytest.mark.db

VIGENCIA_ENLACE = timedelta(minutes=15)
"""La del diseño (§10.3); la migración la impone como tope."""
VIGENCIA_SESION = timedelta(days=30)
"""La del diseño (§10.4); la migración la impone como tope."""
RENOVAR_CADA = timedelta(minutes=5)
IP = "198.51.100.7"
FUNCIONES = (
    "nucleo.pedir_enlace(text, bytea, bytea, uuid, inet, interval)",
    "nucleo.canjear_enlace(bytea, bytea, bytea, text, inet, interval)",
    "nucleo.validar_sesion(bytea, interval, interval)",
)


def h(valor: str) -> bytes:
    return hashlib.sha256(valor.encode()).digest()


def token() -> str:
    return secrets.token_urlsafe(32)


def pedir(conn: Connection, correo: str, tok: str, nonce: str, vigencia=VIGENCIA_ENLACE) -> bool:
    return conn.execute(
        text("select nucleo.pedir_enlace(:c, :t, :n, :d, cast(:ip as inet), :v)"),
        {"c": correo, "t": h(tok), "n": h(nonce), "d": uuid4(), "ip": IP, "v": vigencia},
    ).scalar_one()


def canjear(conn: Connection, tok: str, nonce: str, sesion: str, vigencia=VIGENCIA_SESION):
    return conn.execute(
        text(
            "select resultado, persona, sesion, vence"
            " from nucleo.canjear_enlace(:t, :n, :s, 'navegador de prueba', cast(:ip as inet), :v)"
        ),
        {"t": h(tok), "n": h(nonce), "s": h(sesion), "ip": IP, "v": vigencia},
    ).one()


def validar(conn: Connection, sesion: str):
    return conn.execute(
        text("select persona, sesion, vence, renovada from nucleo.validar_sesion(:t, :v, :r)"),
        {"t": h(sesion), "v": VIGENCIA_SESION, "r": RENOVAR_CADA},
    ).all()


def nueva_persona(motor_migrate) -> tuple[UUID, str]:
    correo = f"p{uuid4().hex[:12]}@ejemplo.com"
    with motor_migrate.begin() as conn:
        id_ = conn.execute(
            text("insert into nucleo.persona (correo, nombre) values (:c, 'Prueba') returning id"),
            {"c": correo},
        ).scalar_one()
    return id_, correo


def sesion_de(motor_app, motor_migrate) -> tuple[UUID, str]:
    """Una persona nueva con una sesión válida, creada por el camino real (pedir y canjear)."""
    persona, correo = nueva_persona(motor_migrate)
    tok, nonce, sesion = token(), token(), token()
    with motor_app.begin() as conn:
        assert pedir(conn, correo, tok, nonce)
        assert canjear(conn, tok, nonce, sesion).resultado == "ok"
    return persona, sesion


def envejecer(motor_migrate, tabla: str, token_hash: bytes, columnas: tuple[str, ...], cuanto):
    asignaciones = ", ".join(f"{c} = {c} - :d" for c in columnas)
    with motor_migrate.begin() as conn:
        conn.execute(
            text(f"update {tabla} set {asignaciones} where token_hash = :t"),
            {"d": cuanto, "t": token_hash},
        )


# Pedir ----------------------------------------------------------------------------------


def test_pedir_registra_solo_si_hay_una_persona_activa(motor_app, motor_migrate, datos):
    persona, correo = nueva_persona(motor_migrate)
    con_cuenta, sin_cuenta, escrito_distinto = token(), token(), token()
    with motor_app.begin() as conn:
        assert pedir(conn, correo, con_cuenta, token()) is True
        assert pedir(conn, "nadie@ejemplo.com", sin_cuenta, token()) is False
        assert pedir(conn, f"  {correo.upper()} ", escrito_distinto, token()) is True
    with motor_migrate.begin() as conn:
        conn.execute(text("update nucleo.persona set eliminado_en = now() where id = :p"),
                     {"p": persona})  # fmt: skip
    with motor_app.begin() as conn:
        assert pedir(conn, correo, token(), token()) is False
    with motor_migrate.connect() as conn:
        filas = conn.execute(
            text("select token_hash from nucleo.enlace_acceso where persona_id = :p"),
            {"p": persona},
        ).scalars()
        assert set(filas) == {h(con_cuenta), h(escrito_distinto)}


# Canjear --------------------------------------------------------------------------------


def test_canje_crea_la_sesion_y_el_enlace_sirve_una_sola_vez(motor_app, motor_migrate, datos):
    persona, correo = nueva_persona(motor_migrate)
    tok, nonce, sesion = token(), token(), token()
    with motor_app.begin() as conn:
        pedir(conn, correo, tok, nonce)
        r = canjear(conn, tok, nonce, sesion)
        assert (r.resultado, r.persona) == ("ok", persona)
        assert canjear(conn, tok, nonce, token()).resultado == "usado"
    with motor_migrate.connect() as conn:
        s = conn.execute(
            text("select * from nucleo.sesion where token_hash = :t"), {"t": h(sesion)}
        ).one()
    assert (s.id, s.persona_id, s.agente, str(s.ip)) == (
        r.sesion,
        persona,
        "navegador de prueba",
        IP,
    )
    assert s.vence_en - s.creada_en == VIGENCIA_SESION


def test_canje_rechaza_sin_consumir_lo_vencido_y_lo_de_otro_navegador(
    motor_app, motor_migrate, datos
):
    _, correo = nueva_persona(motor_migrate)
    tok, nonce = token(), token()
    with motor_app.begin() as conn:
        pedir(conn, correo, tok, nonce)
        assert canjear(conn, tok, token(), token()).resultado == "otro_navegador"
        assert canjear(conn, token(), nonce, token()).resultado == "inexistente"
    envejecer(motor_migrate, "nucleo.enlace_acceso", h(tok), ("creado_en", "vence_en"),
              VIGENCIA_ENLACE + timedelta(seconds=1))  # fmt: skip
    with motor_app.begin() as conn:
        assert canjear(conn, tok, nonce, token()).resultado == "vencido"
    # Control: el mismo enlace, todavía vigente, entra; los rechazos no lo gastaron.
    envejecer(motor_migrate, "nucleo.enlace_acceso", h(tok), ("creado_en", "vence_en"),
              -(VIGENCIA_ENLACE + timedelta(seconds=1)))  # fmt: skip
    with motor_app.begin() as conn:
        assert canjear(conn, tok, nonce, token()).resultado == "ok"


def test_canje_de_una_persona_dada_de_baja(motor_app, motor_migrate, datos):
    persona, correo = nueva_persona(motor_migrate)
    tok, nonce = token(), token()
    with motor_app.begin() as conn:
        pedir(conn, correo, tok, nonce)
    with motor_migrate.begin() as conn:
        conn.execute(text("update nucleo.persona set eliminado_en = now() where id = :p"),
                     {"p": persona})  # fmt: skip
    with motor_app.begin() as conn:
        assert canjear(conn, tok, nonce, token()).resultado == "persona_eliminada"


# Validar ---------------------------------------------------------------------------------


def test_validar_y_vencimiento_deslizante(motor_app, motor_migrate, datos):
    persona, sesion = sesion_de(motor_app, motor_migrate)
    with motor_app.begin() as conn:
        [fila] = validar(conn, sesion)
        assert (fila.persona, fila.renovada) == (persona, False)  # recién usada: no escribe
        assert validar(conn, token()) == []
    envejecer(motor_migrate, "nucleo.sesion", h(sesion),
              ("creada_en", "ultimo_uso_en", "vence_en"), timedelta(days=10))  # fmt: skip
    with motor_app.begin() as conn:
        [fila] = validar(conn, sesion)
        assert fila.renovada is True
        # La misma transacción que la renovación: now() es el mismo instante.
        restante = conn.execute(text("select :v - now()"), {"v": fila.vence}).scalar_one()
        assert restante == VIGENCIA_SESION


def test_validar_rechaza_revocada_vencida_y_de_baja(motor_app, motor_migrate, datos):
    """Cada caso dos veces: recién usada (la función solo lee) y con más de `RENOVAR_CADA` sin
    uso (la función intenta renovarla): las dos ramas tienen que rechazar."""
    casos = {
        nombre: sesion_de(motor_app, motor_migrate)
        for nombre in ("revocada", "revocada_vieja", "vencida", "baja", "baja_vieja")
    }
    with motor_app.begin() as conn:
        assert all(len(validar(conn, s)) == 1 for _, s in casos.values())  # control
    for nombre in ("revocada", "revocada_vieja"):
        with transaccion(motor_app, persona_id=casos[nombre][0]) as conn:
            conn.execute(text("update nucleo.sesion set revocada_en = now()"))
    for nombre in ("baja", "baja_vieja"):
        with motor_migrate.begin() as conn:
            conn.execute(text("update nucleo.persona set eliminado_en = now() where id = :p"),
                         {"p": casos[nombre][0]})  # fmt: skip
    columnas = ("creada_en", "ultimo_uso_en", "vence_en")
    for nombre in ("revocada_vieja", "baja_vieja"):
        envejecer(motor_migrate, "nucleo.sesion", h(casos[nombre][1]), columnas, 2 * RENOVAR_CADA)
    envejecer(motor_migrate, "nucleo.sesion", h(casos["vencida"][1]), columnas,
              VIGENCIA_SESION + timedelta(seconds=1))  # fmt: skip
    with motor_app.begin() as conn:
        assert {nombre: validar(conn, s) for nombre, (_, s) in casos.items()} == {
            nombre: [] for nombre in casos
        }


# Seguridad por fila y privilegios (§8.5, §8.6) -------------------------------------------


def test_cem_app_no_toca_los_enlaces(motor_app, datos):
    for sql in (
        "select count(*) from nucleo.enlace_acceso",
        "update nucleo.enlace_acceso set usado_en = null",
        "delete from nucleo.enlace_acceso",
    ):
        with motor_app.begin() as conn, pytest.raises(ProgrammingError, match="permission denied"):
            conn.execute(text(sql))


def test_cada_persona_ve_y_revoca_solo_sus_sesiones(motor_app, motor_migrate, datos):
    a, sesion_a = sesion_de(motor_app, motor_migrate)
    b, sesion_b = sesion_de(motor_app, motor_migrate)
    with motor_app.connect() as conn:
        assert conn.execute(text("select count(*) from nucleo.sesion")).scalar_one() == 0
    with transaccion(motor_app, persona_id=a) as conn:
        assert set(conn.execute(text("select persona_id from nucleo.sesion")).scalars()) == {a}
        revocadas = conn.execute(
            text("update nucleo.sesion set revocada_en = now() where persona_id = :p"), {"p": b}
        ).rowcount
        assert revocadas == 0
    with motor_app.begin() as conn:
        assert len(validar(conn, sesion_b)) == 1  # la de B sigue viva
    for sql in (
        "update nucleo.sesion set vence_en = now() + interval '1 year'",
        "update nucleo.sesion set persona_id = :b",
        "delete from nucleo.sesion",
        "insert into nucleo.sesion (persona_id, token_hash, dispositivo, ip, vence_en)"
        " values (:b, :t, gen_random_uuid(), '127.0.0.1', now() + interval '1 day')",
    ):
        with (
            transaccion(motor_app, persona_id=a) as conn,
            pytest.raises(ProgrammingError, match="permission denied"),
        ):
            conn.execute(text(sql), {"b": b, "t": h(token())})
    with motor_app.begin() as conn:
        assert len(validar(conn, sesion_a)) == 1


def test_las_funciones_son_security_definer_con_search_path_fijo(motor_app, datos):
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


def test_la_base_impone_los_topes_de_vigencia(motor_app, motor_migrate, datos):
    _, correo = nueva_persona(motor_migrate)
    tok, nonce = token(), token()
    with motor_app.begin() as conn, pytest.raises(IntegrityError, match="enlace_acceso"):
        pedir(conn, correo, token(), token(), vigencia=VIGENCIA_ENLACE + timedelta(seconds=1))
    with motor_app.begin() as conn:
        pedir(conn, correo, tok, nonce)
    with motor_app.begin() as conn, pytest.raises(IntegrityError, match="sesion"):
        canjear(conn, tok, nonce, token(), vigencia=VIGENCIA_SESION + timedelta(seconds=1))
    with motor_app.begin() as conn:
        assert canjear(conn, tok, nonce, token()).resultado == "ok"  # control


# Migración -------------------------------------------------------------------------------


def test_la_migracion_0004_baja_y_sube(base_de_prueba, motor_migrate):
    def existe() -> tuple[bool, ...]:
        with motor_migrate.connect() as conn:
            return tuple(
                conn.execute(
                    text(
                        "select to_regclass('nucleo.sesion') is not null,"
                        " to_regclass('nucleo.enlace_acceso') is not null,"
                        " to_regprocedure(:f1) is not null, to_regprocedure(:f2) is not null,"
                        " to_regprocedure(:f3) is not null"
                    ),
                    dict(zip(("f1", "f2", "f3"), FUNCIONES, strict=True)),
                ).one()
            )

    assert existe() == (True,) * 5
    cfg = alembic_config()
    vaciar_mandatos(motor_migrate)
    command.downgrade(cfg, "0003")
    assert existe() == (False,) * 5
    volver_a_subir(cfg, motor_migrate)  # bajó por debajo de 0007
    assert existe() == (True,) * 5
