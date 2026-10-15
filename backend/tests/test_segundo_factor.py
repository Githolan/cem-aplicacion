"""Segundo factor TOTP, códigos de recuperación y verificación reciente (TB-CEM-3; migración
0008) contra PostgreSQL 18 y Redis 7 reales.

Cada prueba usa personas nuevas. El reloj del TOTP se fija con `paso_actual` para poder
generar el código "del teléfono" y probar la repetición sin esperar 30 segundos.
"""

import base64
from datetime import timedelta
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import pytest
import sqlalchemy.exc
from cryptography.exceptions import InvalidTag
from sqlalchemy import text

from app.identidad import segundo_factor as sf
from app.identidad import sesiones
from tests.test_identidad import (  # noqa: F401  (`app` es un fixture)
    CLAVE_CODIGO,
    Navegadores,
    app,
    csrf,
    navegador,
)
from tests.test_identidad_cierre_y_purga import entrar_como
from tests.test_identidad_tabla import nueva_persona

pytestmark = [pytest.mark.db, pytest.mark.redis]

BASE = "/api/v1/auth/totp"
PASO = 60_000_000
"""Un paso fijo cualquiera (año 2027)."""


@pytest.fixture
def reloj(monkeypatch):
    """Fija el paso actual; `reloj.paso` se puede mover dentro de la prueba."""

    class Reloj:
        paso = PASO

    monkeypatch.setattr(sf, "paso_actual", lambda ahora=None: Reloj.paso)
    return Reloj


def secreto_de(alta: dict) -> bytes:
    s = alta["secreto"]
    return base64.b32decode(s + "=" * (-len(s) % 8))


def con_factor(app, motor_migrate, reloj):  # noqa: F811
    """Una persona nueva con sesión y el segundo factor activo. Devuelve el navegador, el
    secreto, los códigos de recuperación y el correo."""
    _, correo = nueva_persona(motor_migrate)
    c = entrar_como(app, motor_migrate, correo)
    alta = c.post(f"{BASE}/alta", headers=csrf(c))
    assert alta.status_code == 200, alta.text
    secreto = secreto_de(alta.json())
    r = c.post(
        f"{BASE}/confirmar", json={"codigo": sf.codigo_en(secreto, reloj.paso)}, headers=csrf(c)
    )
    assert r.status_code == 200, r.text
    return c, secreto, r.json()["codigos"], correo


def avisos(motor_migrate, correo: str) -> list[str]:
    with motor_migrate.connect() as conn:
        return list(
            conn.execute(
                text(
                    "select carga->>'asunto' from plataforma.outbox where carga->>'para' = :c"
                    " order by id"
                ),
                {"c": correo},
            ).scalars()
        )


# Primitivas ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("segundos", "esperado"),
    [(59, "287082"), (1111111109, "081804"), (1234567890, "005924"), (2000000000, "279037")],
)
def test_vectores_del_rfc_6238(segundos, esperado):
    """RFC 6238, apéndice B (SHA-1), los últimos seis de los ocho dígitos."""
    assert sf.codigo_en(b"12345678901234567890", segundos // 30) == esperado


def test_ventana_de_un_paso_y_sin_repetir():
    secreto = b"12345678901234567890"
    ahora = PASO * 30
    for corrimiento in (-1, 0, 1):
        codigo = sf.codigo_en(secreto, PASO + corrimiento)
        assert sf.paso_valido(secreto, codigo, 0, ahora) == PASO + corrimiento
    assert sf.paso_valido(secreto, sf.codigo_en(secreto, PASO + 2), 0, ahora) is None
    assert sf.paso_valido(secreto, sf.codigo_en(secreto, PASO - 2), 0, ahora) is None
    # Ya aceptado ese paso (o uno posterior): no se acepta de nuevo.
    assert sf.paso_valido(secreto, sf.codigo_en(secreto, PASO), PASO, ahora) is None


def test_la_uri_la_entienden_las_aplicaciones():
    uri = urlparse(sf.uri_otpauth(b"\x01" * 20, "ana@ejemplo.com"))
    assert (uri.scheme, uri.netloc) == ("otpauth", "totp")
    assert uri.path == "/Consorcio%20en%20Mano%3Aana%40ejemplo.com"
    q = parse_qs(uri.query)
    assert q == {
        "secret": [sf.base32(b"\x01" * 20)],
        "issuer": ["Consorcio en Mano"],
        "algorithm": ["SHA1"],
        "digits": ["6"],
        "period": ["30"],
    }
    assert "=" not in q["secret"][0]


def test_el_cifrado_ata_el_secreto_a_la_persona():
    claves = sf.Claves(sesiones.clave_codigo(CLAVE_CODIGO))
    ana, beto = uuid4(), uuid4()
    cifrado = claves.cifrar(ana, b"s" * 20)
    assert len(cifrado) == 48 and b"s" * 20 not in cifrado
    assert claves.descifrar(ana, cifrado) == b"s" * 20
    with pytest.raises(InvalidTag):
        claves.descifrar(beto, cifrado)  # copiado a otra fila no sirve
    otra = sf.Claves(sesiones.clave_codigo(sesiones.nuevo_token()))
    with pytest.raises(InvalidTag):
        otra.descifrar(ana, cifrado)  # sin la clave del entorno no sirve


def test_codigos_de_recuperacion_tienen_el_formato_del_contrato():
    import re

    from cem_contracts.segundo_factor import FORMATO_CODIGO_RECUPERACION

    codigos = {sf.nuevo_codigo_recuperacion() for _ in range(200)}
    assert len(codigos) == 200
    assert all(re.fullmatch(FORMATO_CODIGO_RECUPERACION, c) for c in codigos)


# Alta y confirmación ------------------------------------------------------------------------


def test_alta_confirmacion_y_estado(app, motor_migrate, reloj):  # noqa: F811
    _, correo = nueva_persona(motor_migrate)
    c = entrar_como(app, motor_migrate, correo)
    assert c.get(BASE).json() == {
        "activo": False,
        "activado_en": None,
        "codigos_recuperacion_restantes": 0,
        "verificado_en": None,
    }
    assert c.post(f"{BASE}/alta").status_code == 403  # sin CSRF
    alta = c.post(f"{BASE}/alta", headers=csrf(c))
    assert alta.status_code == 200
    assert alta.headers["cache-control"] == "no-store"
    assert correo.replace("@", "%40") in alta.json()["uri"]
    secreto = secreto_de(alta.json())
    # Pendiente: todavía no está activo y no sirve para verificar.
    assert c.get(BASE).json()["activo"] is False
    r = c.post(f"{BASE}/verificar", json={"codigo": sf.codigo_en(secreto, reloj.paso)},
               headers=csrf(c))  # fmt: skip
    assert r.status_code == 409 and r.json()["codigo"] == "segundo_factor_sin_alta"

    malo = c.post(f"{BASE}/confirmar", json={"codigo": "000000"}, headers=csrf(c))
    assert malo.status_code == 400 and malo.json()["codigo"] == "segundo_factor_invalido"
    r = c.post(
        f"{BASE}/confirmar", json={"codigo": sf.codigo_en(secreto, reloj.paso)}, headers=csrf(c)
    )
    assert r.status_code == 200
    codigos = r.json()["codigos"]
    assert len(set(codigos)) == 10
    e = c.get(BASE).json()
    assert e["activo"] is True and e["codigos_recuperacion_restantes"] == 10
    assert e["verificado_en"] is not None  # confirmar deja la sesión verificada
    assert avisos(motor_migrate, correo)[-1] == sf.ASUNTO_ALTA
    # En la base: ni el secreto ni los códigos en claro.
    with motor_migrate.connect() as conn:
        filas = conn.execute(
            text(
                "select f::text from nucleo.factor_totp f join nucleo.persona p"
                " on p.id = f.persona_id where p.correo = :c"
                " union all select r::text from nucleo.codigo_recuperacion r"
                " join nucleo.persona p on p.id = r.persona_id where p.correo = :c"
            ),
            {"c": correo},
        ).scalars()
        volcado = " ".join(filas)
    assert secreto.hex() not in volcado and alta.json()["secreto"] not in volcado
    assert not any(x in volcado for x in codigos)


def test_pedir_otra_alta_reemplaza_la_pendiente_y_con_una_activa_no_se_puede(
    app,  # noqa: F811
    motor_migrate,
    reloj,
):
    _, correo = nueva_persona(motor_migrate)
    c = entrar_como(app, motor_migrate, correo)
    viejo = secreto_de(c.post(f"{BASE}/alta", headers=csrf(c)).json())
    nuevo = secreto_de(c.post(f"{BASE}/alta", headers=csrf(c)).json())
    r = c.post(f"{BASE}/confirmar", json={"codigo": sf.codigo_en(viejo, reloj.paso)},
               headers=csrf(c))  # fmt: skip
    assert r.status_code == 400
    r = c.post(f"{BASE}/confirmar", json={"codigo": sf.codigo_en(nuevo, reloj.paso)},
               headers=csrf(c))  # fmt: skip
    assert r.status_code == 200
    otra = c.post(f"{BASE}/alta", headers=csrf(c))
    assert otra.status_code == 409 and otra.json()["codigo"] == "segundo_factor_ya_activo"
    # Sin alta pendiente, confirmar no tiene qué confirmar.
    r = c.post(f"{BASE}/confirmar", json={"codigo": "123456"}, headers=csrf(c))
    assert r.status_code == 409


# Verificación ------------------------------------------------------------------------------


def test_verificar_es_por_sesion_y_un_codigo_no_se_repite(app, motor_migrate, reloj):  # noqa: F811
    c, secreto, _, correo = con_factor(app, motor_migrate, reloj)
    otra = entrar_como(app, motor_migrate, correo)
    assert otra.get(BASE).json()["verificado_en"] is None  # no hereda la de la primera
    # El código ya usado para confirmar, en el mismo paso: no sirve.
    usado = sf.codigo_en(secreto, reloj.paso)
    r = otra.post(f"{BASE}/verificar", json={"codigo": usado}, headers=csrf(otra))
    assert r.status_code == 400
    reloj.paso += 1
    r = otra.post(f"{BASE}/verificar", json={"codigo": sf.codigo_en(secreto, reloj.paso)},
                  headers=csrf(otra))  # fmt: skip
    assert r.status_code == 200, r.text
    cuerpo = r.json()
    assert cuerpo["codigos_recuperacion_restantes"] == 10
    assert otra.get(BASE).json()["verificado_en"] is not None
    # El mismo código otra vez, ya en otra sesión: tampoco.
    r = c.post(f"{BASE}/verificar", json={"codigo": sf.codigo_en(secreto, reloj.paso)},
               headers=csrf(c))  # fmt: skip
    assert r.status_code == 400


def test_codigo_de_recuperacion_sirve_una_vez(app, motor_migrate, reloj):  # noqa: F811
    _, _, codigos, correo = con_factor(app, motor_migrate, reloj)
    c = entrar_como(app, motor_migrate, correo)
    r = c.post(f"{BASE}/verificar", json={"codigo": codigos[3]}, headers=csrf(c))
    assert r.status_code == 200
    assert r.json()["codigos_recuperacion_restantes"] == 9
    assert c.post(f"{BASE}/verificar", json={"codigo": codigos[3]},
                  headers=csrf(c)).status_code == 400  # fmt: skip
    # Un código de otra persona no sirve.
    _, _, ajenos, _ = con_factor(app, motor_migrate, reloj)
    assert c.post(f"{BASE}/verificar", json={"codigo": ajenos[0]},
                  headers=csrf(c)).status_code == 400  # fmt: skip


def test_el_cupo_de_intentos_es_por_persona(app, motor_migrate, reloj):  # noqa: F811
    _, secreto, _, correo = con_factor(app, motor_migrate, reloj)
    # Desde dos navegadores (dos IP): el cupo es de la persona.
    a, b = entrar_como(app, motor_migrate, correo), entrar_como(app, motor_migrate, correo)
    # La confirmación de con_factor ya gastó uno de los 5 por minuto.
    for c in (a, b, a, b):
        assert c.post(f"{BASE}/verificar", json={"codigo": "000000"},
                      headers=csrf(c)).status_code == 400  # fmt: skip
    reloj.paso += 1
    r = a.post(f"{BASE}/verificar", json={"codigo": sf.codigo_en(secreto, reloj.paso)},
               headers=csrf(a))  # fmt: skip
    assert r.status_code == 429 and "retry-after" in r.headers


# Verificación reciente (TB-CEM-3) y baja --------------------------------------------------


def test_la_baja_pide_verificacion_reciente(app, motor_migrate, reloj):  # noqa: F811
    _, secreto, _, correo = con_factor(app, motor_migrate, reloj)
    c = entrar_como(app, motor_migrate, correo)
    r = c.post(f"{BASE}/baja", headers=csrf(c))
    assert r.status_code == 403 and r.json()["codigo"] == "segundo_factor_requerido"
    reloj.paso += 1
    c.post(f"{BASE}/verificar", json={"codigo": sf.codigo_en(secreto, reloj.paso)},
           headers=csrf(c))  # fmt: skip
    # Hace 11 minutos ya no cuenta (la sesión se corre atrás: la verificación no puede ser
    # anterior a su creación).
    with motor_migrate.begin() as conn:
        conn.execute(
            text(
                "update nucleo.sesion set creada_en = now() - interval '1 hour',"
                " segundo_factor_en = now() - :v where token_hash = :t"
            ),
            {
                "v": sf.VIGENCIA_VERIFICACION + timedelta(minutes=1),
                "t": sesiones.hash_token(c.cookies[sesiones.COOKIE_SESION]),
            },
        )
    assert c.post(f"{BASE}/baja", headers=csrf(c)).status_code == 403
    reloj.paso += 1
    c.post(f"{BASE}/verificar", json={"codigo": sf.codigo_en(secreto, reloj.paso)},
           headers=csrf(c))  # fmt: skip
    assert c.post(f"{BASE}/baja").status_code == 403  # sin CSRF
    assert c.post(f"{BASE}/baja", headers=csrf(c)).status_code == 204
    assert c.get(BASE).json() == {
        "activo": False,
        "activado_en": None,
        "codigos_recuperacion_restantes": 0,
        "verificado_en": None,
    }
    assert avisos(motor_migrate, correo)[-1] == sf.ASUNTO_BAJA
    # Se puede volver a dar de alta.
    assert c.post(f"{BASE}/alta", headers=csrf(c)).status_code == 200


def test_sin_sesion_todo_responde_401(app):  # noqa: F811
    c = navegador(app)
    assert c.get(BASE).status_code == 401
    for ruta in ("alta", "confirmar", "verificar", "baja"):
        assert c.post(f"{BASE}/{ruta}", json={"codigo": "123456"}).status_code == 401


def test_sin_clave_responde_503(motor_app, motor_migrate, datos, redis_limpio):
    sin_clave = Navegadores(motor_app, clave_codigo="")
    try:
        # La sesión se crea con otra app que sí tiene la clave (el enlace la necesita).
        con = Navegadores(motor_app)
        _, correo = nueva_persona(motor_migrate)
        c = entrar_como(con, motor_migrate, correo)
        s = sin_clave.abrir()
        s.cookies.update(c.cookies)
        assert s.post(f"{BASE}/alta", headers=csrf(c)).status_code == 503
        con.cerrar()
    finally:
        sin_clave.cerrar()


# Base: RLS y privilegios --------------------------------------------------------------------


def test_otra_persona_no_ve_ni_toca_el_segundo_factor(app, motor_migrate, motor_app, reloj):  # noqa: F811
    con_factor(app, motor_migrate, reloj)
    otra, _ = nueva_persona(motor_migrate)

    def contar(persona) -> tuple[int, int]:
        with motor_app.begin() as conn:
            conn.execute(
                text("select set_config('app.persona_id', :p, true)"),
                {"p": str(persona) if persona else ""},
            )
            return (
                conn.execute(text("select count(*) from nucleo.factor_totp")).scalar_one(),
                conn.execute(text("select count(*) from nucleo.codigo_recuperacion")).scalar_one(),
            )

    assert contar(otra) == (0, 0)
    assert contar(None) == (0, 0)
    with pytest.raises(sqlalchemy.exc.ProgrammingError, match="row-level security"):
        with motor_app.begin() as conn:
            conn.execute(text("select set_config('app.persona_id', :p, true)"), {"p": str(otra)})
            conn.execute(
                text(
                    "insert into nucleo.factor_totp (persona_id, secreto_cifrado) values (:p, :s)"
                ),
                {"p": uuid4(), "s": b"\0" * 48},
            )


def test_cem_app_no_cambia_la_persona_de_un_factor(motor_app):
    with pytest.raises(sqlalchemy.exc.ProgrammingError, match="permission denied"):
        with motor_app.begin() as conn:
            conn.execute(text("update nucleo.factor_totp set persona_id = persona_id"))
    with pytest.raises(sqlalchemy.exc.ProgrammingError, match="permission denied"):
        with motor_app.begin() as conn:
            conn.execute(text("update nucleo.codigo_recuperacion set codigo_hash = codigo_hash"))
