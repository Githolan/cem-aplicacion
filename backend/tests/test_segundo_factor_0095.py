"""`0095`: la marca del segundo factor la pone solo la base, gastando la prueba que la justifica.

Revisión de seguridad transversal de H3, P3: R10 confiaba en `nucleo.sesion.segundo_factor_en`,
que `cem_app` podía escribir con un UPDATE, y en el booleano de `resolver_desafio_contrasena`.
Contra PostgreSQL 18 y Redis 7 reales; las sesiones y los factores se crean por la API.
"""

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, ProgrammingError

from app.db import fijar_ambito
from app.identidad import segundo_factor as sf
from app.identidad import sesiones
from tests.test_contrasena import CLAVE, definir, entrar, persona_de_correo
from tests.test_identidad import CLAVE_CODIGO, app, csrf  # noqa: F401  (`app` es un fixture)
from tests.test_llave_acceso import con_llave
from tests.test_segundo_factor import BASE, con_factor, reloj  # noqa: F401  (`reloj` es fixture)
from tests.test_vinculo_0014 import base_aparte  # noqa: F401  (`base_aparte` es un fixture)

pytestmark = [pytest.mark.db, pytest.mark.redis]

RESOLVER = (
    "select resultado, persona, sesion from nucleo.resolver_desafio_contrasena("
    ":t, :paso, :rec, :s, gen_random_uuid(), null, '198.51.100.7', interval '1 day', 5)"
)
RESOLVER_0009 = (
    "nucleo.resolver_desafio_contrasena(bytea, boolean, bytea, uuid, text, inet, interval, integer)"
)


def sesion_y_persona(motor_migrate, c, correo: str):
    return (
        sesion_del_navegador(motor_migrate, c),
        persona_de_correo(motor_migrate, correo),
    )


def sesion_del_navegador(motor_migrate, c):
    with motor_migrate.connect() as conn:
        return conn.execute(
            text("select id from nucleo.sesion where token_hash = :t"),
            {"t": sesiones.hash_token(c.cookies[sesiones.COOKIE_SESION])},
        ).scalar_one()


def marca(motor_migrate, sesion):
    with motor_migrate.connect() as conn:
        return conn.execute(
            text("select segundo_factor_en from nucleo.sesion where id = :s"), {"s": sesion}
        ).scalar_one()


def borrar_marca(motor_migrate, sesion) -> None:
    with motor_migrate.begin() as conn:
        conn.execute(
            text("update nucleo.sesion set segundo_factor_en = null where id = :s"), {"s": sesion}
        )


def ultimo_paso(motor_migrate, persona) -> int:
    with motor_migrate.connect() as conn:
        return conn.execute(
            text("select ultimo_paso from nucleo.factor_totp where persona_id = :p"),
            {"p": persona},
        ).scalar_one()


def como_app(motor_app, persona, sql: str, **parametros):
    with motor_app.begin() as conn:
        fijar_ambito(conn, persona_id=persona)
        return conn.execute(text(sql), parametros).scalar_one()


# Escrituras directas de `cem_app` ----------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "update nucleo.sesion set segundo_factor_en = now() where id = :s",
        "update nucleo.sesion set segundo_factor_en = null where id = :s",
        "update nucleo.factor_totp set ultimo_paso = 0 where persona_id = :p",
        "update nucleo.factor_totp set confirmado_en = now() where persona_id = :p",
        "update nucleo.codigo_recuperacion set usado_en = null where persona_id = :p",
        "select codigo_hash from nucleo.codigo_recuperacion where persona_id = :p",
        "update nucleo.llave_acceso set contador = 0 where persona_id = :p",
        "update nucleo.llave_acceso set ultimo_uso_en = now() where persona_id = :p",
        "select nucleo.marcar_segundo_factor(:s, :p)",
    ],
)
def test_cem_app_no_escribe_la_marca_ni_las_pruebas(app, motor_app, motor_migrate, reloj, sql):  # noqa: F811
    c, _, _, correo = con_factor(app, motor_migrate, reloj)
    s, p = sesion_y_persona(motor_migrate, c, correo)
    borrar_marca(motor_migrate, s)
    with motor_app.begin() as conn, pytest.raises(ProgrammingError, match="permission denied"):
        fijar_ambito(conn, persona_id=p)
        conn.execute(text(sql), {"s": s, "p": p})
    assert marca(motor_migrate, s) is None


def test_control_el_update_directo_es_valido_y_alcanza_a_la_fila(app, motor_migrate, reloj):  # noqa: F811
    """Control de la prueba anterior: el mismo UPDATE, como dueño de la tabla, marca la sesión. El
    «permission denied» de `cem_app` es por el permiso, no por un SQL mal escrito."""
    c, _, _, correo = con_factor(app, motor_migrate, reloj)
    s, _ = sesion_y_persona(motor_migrate, c, correo)
    borrar_marca(motor_migrate, s)
    with motor_migrate.begin() as conn:
        cambiadas = conn.execute(
            text("update nucleo.sesion set segundo_factor_en = now() where id = :s"), {"s": s}
        ).rowcount
    assert cambiadas == 1 and marca(motor_migrate, s) is not None


def test_cem_app_sigue_contando_los_codigos_restantes(app, motor_app, motor_migrate, reloj):  # noqa: F811
    """`restantes` y el humo de staging cuentan sin leer el hash."""
    c, _, _, correo = con_factor(app, motor_migrate, reloj)
    _, p = sesion_y_persona(motor_migrate, c, correo)
    sql = "select count(*) from nucleo.codigo_recuperacion where usado_en is null"
    assert como_app(motor_app, p, sql) == sf.CANTIDAD_RECUPERACION
    assert como_app(motor_app, p, "select count(*) from nucleo.codigo_recuperacion") == (
        sf.CANTIDAD_RECUPERACION
    )


# TOTP: el paso se gasta en la base ---------------------------------------------------------


def test_un_codigo_no_se_acepta_dos_veces_aunque_cem_app_intente_rebobinar(
    app, motor_app, motor_migrate, reloj  # noqa: F811
):  # fmt: skip
    """El mismo código TOTP dos veces: la segunda falla. Con el código viejo `cem_app` podía volver
    `ultimo_paso` a 0 y el mismo código se aceptaba de nuevo."""
    c, secreto, _, correo = con_factor(app, motor_migrate, reloj)
    _, p = sesion_y_persona(motor_migrate, c, correo)
    reloj.paso += 1
    codigo = {"codigo": sf.codigo_en(secreto, reloj.paso)}
    assert c.post(f"{BASE}/verificar", json=codigo, headers=csrf(c)).status_code == 200
    with motor_app.begin() as conn, pytest.raises(ProgrammingError, match="permission denied"):
        fijar_ambito(conn, persona_id=p)
        conn.execute(text("update nucleo.factor_totp set ultimo_paso = 0"))
    assert ultimo_paso(motor_migrate, p) == reloj.paso
    assert c.post(f"{BASE}/verificar", json=codigo, headers=csrf(c)).status_code == 400


def test_la_base_no_marca_con_un_paso_repetido_o_anterior(app, motor_app, motor_migrate, reloj):  # noqa: F811
    c, _, _, correo = con_factor(app, motor_migrate, reloj)
    s, p = sesion_y_persona(motor_migrate, c, correo)
    assert ultimo_paso(motor_migrate, p) == reloj.paso  # el alta gastó su paso
    borrar_marca(motor_migrate, s)
    sql = "select nucleo.verificar_sesion_con_totp(:s, :paso)"
    for repetido in (reloj.paso, reloj.paso - 1):
        assert como_app(motor_app, p, sql, s=s, paso=repetido) is None
        assert marca(motor_migrate, s) is None
    # Control: el paso siguiente marca y avanza `ultimo_paso`; repetirlo ya no.
    assert como_app(motor_app, p, sql, s=s, paso=reloj.paso + 1) is not None
    assert ultimo_paso(motor_migrate, p) == reloj.paso + 1
    borrar_marca(motor_migrate, s)
    assert como_app(motor_app, p, sql, s=s, paso=reloj.paso + 1) is None
    assert marca(motor_migrate, s) is None


def test_la_base_solo_marca_una_sesion_vigente_de_la_persona(app, motor_app, motor_migrate, reloj):  # noqa: F811
    c, _, _, correo = con_factor(app, motor_migrate, reloj)
    _, p = sesion_y_persona(motor_migrate, c, correo)
    otra, _, _, correo_otra = con_factor(app, motor_migrate, reloj)
    s_otra, _ = sesion_y_persona(motor_migrate, otra, correo_otra)
    borrar_marca(motor_migrate, s_otra)
    with motor_app.begin() as conn, pytest.raises(DBAPIError, match="no es vigente"):
        fijar_ambito(conn, persona_id=p)
        conn.execute(
            text("select nucleo.verificar_sesion_con_totp(:s, :paso)"),
            {"s": s_otra, "paso": reloj.paso + 1},
        )
    # La transacción se revirtió entera: ni la marca ni el paso.
    assert marca(motor_migrate, s_otra) is None
    assert ultimo_paso(motor_migrate, p) == reloj.paso


def test_un_codigo_de_recuperacion_se_gasta_en_la_base(app, motor_app, motor_migrate, reloj):  # noqa: F811
    c, _, codigos, correo = con_factor(app, motor_migrate, reloj)
    s, p = sesion_y_persona(motor_migrate, c, correo)
    borrar_marca(motor_migrate, s)
    claves = sf.Claves(sesiones.clave_codigo(CLAVE_CODIGO))
    sql = "select nucleo.verificar_sesion_con_recuperacion(:s, :h)"
    inventado = claves.hash_recuperacion(p, "aaaaa-aaaaa")
    assert como_app(motor_app, p, sql, s=s, h=inventado) is None
    assert marca(motor_migrate, s) is None
    bueno = claves.hash_recuperacion(p, codigos[0])
    assert como_app(motor_app, p, sql, s=s, h=bueno) is not None
    borrar_marca(motor_migrate, s)
    assert como_app(motor_app, p, sql, s=s, h=bueno) is None
    assert marca(motor_migrate, s) is None


# Llave: el contador no retrocede -------------------------------------------------------------


def test_la_llave_marca_solo_si_el_contador_avanza(app, motor_app, motor_migrate):  # noqa: F811
    c, _, _, correo = con_llave(app, motor_migrate)
    s, p = sesion_y_persona(motor_migrate, c, correo)
    with motor_migrate.begin() as conn:
        llave = conn.execute(
            text("update nucleo.llave_acceso set contador = 5 where persona_id = :p returning id"),
            {"p": p},
        ).scalar_one()
    borrar_marca(motor_migrate, s)
    sql = "select nucleo.verificar_sesion_con_llave(:s, :llave, :n)"
    for n in (4, 5, 0):
        assert como_app(motor_app, p, sql, s=s, llave=llave, n=n) is None
    assert marca(motor_migrate, s) is None
    assert como_app(motor_app, p, sql, s=s, llave=llave, n=6) is not None
    # Una llave de otra persona no sirve.
    _, _, _, correo_otra = con_llave(app, motor_migrate)
    p_otra = persona_de_correo(motor_migrate, correo_otra)
    borrar_marca(motor_migrate, s)
    assert como_app(motor_app, p_otra, sql, s=s, llave=llave, n=7) is None


# El desafío de la contraseña: sin booleano ---------------------------------------------------


def desafio_pendiente(app, motor_migrate, reloj):  # noqa: F811
    """Una persona con TOTP y contraseña que escribió la contraseña: el token del desafío."""
    c, secreto, codigos, correo = con_factor(app, motor_migrate, reloj)
    assert definir(c).status_code == 200
    navegador, r = entrar(app, correo, CLAVE)
    assert r.json()["estado"] == "falta_segundo_factor"
    token = sesiones.hash_token(navegador.cookies[sesiones.COOKIE_DESAFIO])
    return token, persona_de_correo(motor_migrate, correo), codigos


def resolver(motor_app, token, paso=None, rec=None):
    with motor_app.begin() as conn:
        sesion = sesiones.hash_token(sesiones.nuevo_token())
        return conn.execute(
            text(RESOLVER), {"t": token, "paso": paso, "rec": rec, "s": sesion}
        ).one()


def intentos(motor_migrate, persona) -> int:
    with motor_migrate.connect() as conn:
        return conn.execute(
            text("select intentos from nucleo.desafio_contrasena where persona_id = :p"),
            {"p": persona},
        ).scalar_one()


def test_el_desafio_ya_no_acepta_un_booleano(motor_migrate):
    with motor_migrate.connect() as conn:
        assert conn.execute(
            text("select to_regprocedure(:f)"), {"f": RESOLVER_0009}
        ).scalar_one() is None  # fmt: skip


def test_el_desafio_solo_se_resuelve_gastando_un_paso_nuevo(app, motor_app, motor_migrate, reloj):  # noqa: F811
    token, p, _ = desafio_pendiente(app, motor_migrate, reloj)
    # Sin prueba, o con el paso que ya se usó (el del alta), es un intento fallido.
    assert resolver(motor_app, token).resultado == "incorrecto"
    assert resolver(motor_app, token, paso=reloj.paso).resultado == "incorrecto"
    assert intentos(motor_migrate, p) == 2
    assert ultimo_paso(motor_migrate, p) == reloj.paso
    with pytest.raises(DBAPIError, match="una sola prueba"):
        resolver(motor_app, token, paso=reloj.paso + 1, rec=b"\0" * 32)
    # Control: un paso nuevo lo resuelve, abre la sesión verificada y gasta el paso.
    r = resolver(motor_app, token, paso=reloj.paso + 1)
    assert (r.resultado, r.persona) == ("ok", p)
    assert ultimo_paso(motor_migrate, p) == reloj.paso + 1
    assert marca(motor_migrate, r.sesion) is not None


def test_el_desafio_con_recuperacion_gasta_el_codigo(app, motor_app, motor_migrate, reloj):  # noqa: F811
    token, p, codigos = desafio_pendiente(app, motor_migrate, reloj)
    claves = sf.Claves(sesiones.clave_codigo(CLAVE_CODIGO))
    inventado = claves.hash_recuperacion(p, "aaaaa-aaaaa")
    assert resolver(motor_app, token, rec=inventado).resultado == "incorrecto"
    r = resolver(motor_app, token, rec=claves.hash_recuperacion(p, codigos[0]))
    assert r.resultado == "ok"
    with motor_migrate.connect() as conn:
        assert conn.execute(
            text("select count(*) from nucleo.codigo_recuperacion"
                 " where persona_id = :p and usado_en is not null"), {"p": p},
        ).scalar_one() == 1  # fmt: skip


# Migración -----------------------------------------------------------------------------------


def _permisos(motor) -> tuple:
    with motor.connect() as c:
        return c.execute(
            text(
                "select has_column_privilege('cem_app', 'nucleo.sesion', 'segundo_factor_en',"
                " 'update'),"
                " has_column_privilege('cem_app', 'nucleo.factor_totp', 'ultimo_paso', 'update'),"
                " has_column_privilege('cem_app', 'nucleo.factor_totp', 'confirmado_en',"
                " 'update'),"
                " has_column_privilege('cem_app', 'nucleo.codigo_recuperacion', 'usado_en',"
                " 'update'),"
                " has_column_privilege('cem_app', 'nucleo.codigo_recuperacion', 'codigo_hash',"
                " 'select'),"
                " has_column_privilege('cem_app', 'nucleo.llave_acceso', 'contador', 'update'),"
                " to_regprocedure(:viejo) is not null,"
                " to_regprocedure('nucleo.verificar_sesion_con_totp(uuid, bigint)') is not null"
            ),
            {"viejo": RESOLVER_0009},
        ).one()


def test_0095_ida_y_vuelta(base_aparte):  # noqa: F811
    cfg, motor = base_aparte
    command.upgrade(cfg, "0094")
    antes = _permisos(motor)
    assert tuple(antes) == (True,) * 7 + (False,)
    command.upgrade(cfg, "0095")
    assert tuple(_permisos(motor)) == (False,) * 7 + (True,)
    command.downgrade(cfg, "0094")
    assert _permisos(motor) == antes
