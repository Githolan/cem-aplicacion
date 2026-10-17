"""Errores de las rutas de la contraseña contra PostgreSQL 18 y Redis 7 reales: sin base
responden 503 como las demás rutas de identidad, y todos llevan `Cache-Control: no-store`.
"""

import pytest
from limits import parse
from sqlalchemy import text

from app import limite
from app.db import crear_motor
from app.identidad import contrasena as ct
from app.identidad import sesiones
from tests.test_contrasena import (
    BASE,
    CLAVE,
    ENTRAR,
    OTRA,
    PASO2,
    con_contrasena,
    definir,
    entrar,
    factor_y_contrasena,
    persona_de_correo,
)
from tests.test_identidad import (  # noqa: F401  (`app` es un fixture)
    Navegadores,
    app,
    csrf,
    navegador,
)
from tests.test_identidad_cierre_y_purga import entrar_como
from tests.test_identidad_tabla import nueva_persona
from tests.test_registros import salida_json  # noqa: F401  (fixture)
from tests.test_salud_dependencias import CAIDO_PG
from tests.test_segundo_factor import reloj  # noqa: F401  (fixture)

pytestmark = [pytest.mark.db, pytest.mark.redis]

CANJE = "/api/v1/auth/canje"


def sin_cache(r) -> bool:
    return r.headers.get("cache-control") == "no-store"


def es(r, estado: int, codigo: str) -> bool:
    return r.status_code == estado and r.json()["codigo"] == codigo


@pytest.fixture
def sin_base(redis_limpio):
    """Navegadores de un proceso sin `CEM_DATABASE_URL`, como el backend de la e2e."""
    navegadores = Navegadores(None)
    yield navegadores
    navegadores.cerrar()


@pytest.fixture
def base_caida(redis_limpio):
    """Navegadores de un proceso con la base configurada pero sin responder."""
    navegadores = Navegadores(crear_motor(CAIDO_PG))
    yield navegadores
    navegadores.cerrar()


# Sin base: 503, no 500 -------------------------------------------------------------------------


def test_sin_base_la_entrada_y_el_paso_responden_503(sin_base, salida_json):  # noqa: F811
    c = navegador(sin_base)
    r = c.post(ENTRAR, json={"correo": "ana@ejemplo.com", "contrasena": CLAVE})
    assert es(r, 503, "servicio_no_disponible"), r.text
    assert sin_cache(r)
    c.cookies.set(sesiones.COOKIE_DESAFIO, sesiones.nuevo_token())
    r = c.post(PASO2, json={"codigo": "000000"})
    assert es(r, 503, "servicio_no_disponible"), r.text
    assert sin_cache(r)
    # El motivo va a los registros, no a la respuesta; y no es un "error no controlado".
    mensajes = [x["mensaje"] for x in salida_json()]
    assert "base de datos no disponible: SinBase: CEM_DATABASE_URL sin configurar" in mensajes
    assert "error no controlado" not in mensajes


def test_sin_base_lo_que_se_rechaza_antes_de_consultarla_no_cambia(sin_base):
    """Control del orden: lo que la e2e afirma contra el backend sin base sigue igual."""
    c = navegador(sin_base)
    assert es(c.get(BASE), 401, "no_autenticado")
    assert es(c.put(BASE, json={"contrasena_nueva": CLAVE}), 401, "no_autenticado")
    assert es(c.post(CANJE, json={"token": "Z" * 43}), 401, "enlace_invalido")
    assert es(c.post(PASO2, json={"codigo": "000000"}), 401, "entrada_vencida")


def test_con_la_base_caida_la_entrada_responde_503(base_caida, salida_json):  # noqa: F811
    r = navegador(base_caida).post(ENTRAR, json={"correo": "ana@ejemplo.com", "contrasena": CLAVE})
    assert es(r, 503, "servicio_no_disponible"), r.text
    assert sin_cache(r)
    mensajes = [x["mensaje"] for x in salida_json()]
    assert any(m.startswith("base de datos no disponible: OperationalError") for m in mensajes)
    assert "error no controlado" not in mensajes


# Todos los errores llevan no-store -----------------------------------------------------------


def test_cada_error_de_la_entrada_lleva_no_store(app, motor_migrate, monkeypatch):  # noqa: F811
    _, correo, _ = con_contrasena(app, motor_migrate)
    errores = {
        "credenciales_invalidas": entrar(app, correo, OTRA)[1],
        "peticion_invalida": navegador(app).post(ENTRAR, json={"correo": correo}),
        "csrf_invalido": navegador(app, headers={"sec-fetch-site": "cross-site"}).post(
            ENTRAR, json={"correo": correo, "contrasena": CLAVE}
        ),
    }
    for _ in range(limite.LIMITES_CONTRASENA_CORREO[0].amount - 1):
        entrar(app, correo, OTRA)
    errores["demasiadas_peticiones"] = entrar(app, correo)[1]
    _, otro = nueva_persona(motor_migrate)

    def roto(*_):
        raise RuntimeError("prueba: falla inesperada")

    monkeypatch.setattr(ct, "_cuenta", roto)
    errores["error_interno"] = entrar(app, otro, raise_server_exceptions=False)[1]
    estados = {"credenciales_invalidas": 401, "peticion_invalida": 422, "csrf_invalido": 403,
               "demasiadas_peticiones": 429, "error_interno": 500}  # fmt: skip
    for codigo, r in errores.items():
        assert es(r, estados[codigo], codigo), (codigo, r.status_code, r.text)
        assert sin_cache(r), (codigo, dict(r.headers))


def test_cada_error_del_paso_del_segundo_factor_lleva_no_store(
    app,  # noqa: F811
    motor_migrate,
    reloj,  # noqa: F811
):
    _, _, _, correo = factor_y_contrasena(app, motor_migrate, reloj)
    c, r = entrar(app, correo)
    assert r.json()["estado"] == "falta_segundo_factor"
    errores = {
        "peticion_invalida": c.post(PASO2, json={}),
        "csrf_invalido": navegador(app, headers={"sec-fetch-site": "cross-site"}).post(
            PASO2, json={"codigo": "000000"}
        ),
        "entrada_vencida": navegador(app).post(PASO2, json={"codigo": "000000"}),
        "segundo_factor_invalido": c.post(PASO2, json={"codigo": "000000"}),
    }
    # El alta y el primer código ya gastaron dos del cupo de la persona (5 por minuto).
    for _ in range(3):
        assert c.post(PASO2, json={"codigo": "000000"}).status_code == 400
    errores["demasiadas_peticiones"] = c.post(PASO2, json={"codigo": "000000"})
    sin_clave = Navegadores(app.motor, clave_codigo="")
    try:
        errores["servicio_no_disponible"] = sin_clave.abrir().post(PASO2, json={"codigo": "1"})
    finally:
        sin_clave.cerrar()
    estados = {"peticion_invalida": 422, "csrf_invalido": 403, "entrada_vencida": 401,
               "segundo_factor_invalido": 400, "demasiadas_peticiones": 429,
               "servicio_no_disponible": 503}  # fmt: skip
    for codigo, r in errores.items():
        assert es(r, estados[codigo], codigo), (codigo, r.status_code, r.text)
        assert sin_cache(r), (codigo, dict(r.headers))


def test_cada_error_del_estado_y_del_cambio_lleva_no_store(
    app,  # noqa: F811
    motor_migrate,
    reloj,  # noqa: F811
    monkeypatch,
):
    _, correo, c = con_contrasena(app, motor_migrate)
    anonimo = navegador(app)
    errores = {
        "no_autenticado": anonimo.get(BASE),
        "no_autenticado_put": anonimo.put(BASE, json={"contrasena_nueva": CLAVE}),
        "csrf_invalido": c.put(BASE, json={"contrasena_nueva": OTRA}),
        "peticion_invalida": definir(c, "corta"),
        "contrasena_debil": definir(c, correo.upper()),
        "contrasena_actual_invalida": definir(c, OTRA, actual="incorrecta incorrecta"),
    }
    # Con segundo factor y la verificación vieja.
    f, _, _, correo_f = factor_y_contrasena(app, motor_migrate, reloj)
    with motor_migrate.begin() as conn:
        conn.execute(
            text(
                "update nucleo.sesion set creada_en = now() - interval '1 hour',"
                " segundo_factor_en = now() - interval '11 minutes' where persona_id = :p"
            ),
            {"p": persona_de_correo(motor_migrate, correo_f)},
        )
    errores["segundo_factor_requerido"] = definir(f, OTRA)
    monkeypatch.setattr(limite, "LIMITES_CAMBIO_CONTRASENA", (parse("1/minute"),))
    otra = entrar_como(app, motor_migrate, nueva_persona(motor_migrate)[1])
    assert definir(otra).status_code == 200
    errores["demasiadas_peticiones"] = definir(otra, OTRA)
    estados = {"no_autenticado": 401, "no_autenticado_put": 401, "csrf_invalido": 403,
               "peticion_invalida": 422, "contrasena_debil": 422,
               "contrasena_actual_invalida": 403, "segundo_factor_requerido": 403,
               "demasiadas_peticiones": 429}  # fmt: skip
    for nombre, r in errores.items():
        codigo = nombre.removesuffix("_put")
        assert es(r, estados[nombre], codigo), (nombre, r.status_code, r.text)
        assert sin_cache(r), (nombre, dict(r.headers))


def test_las_otras_rutas_de_identidad_no_cambian(app):  # noqa: F811
    """Control del alcance: el marcador es del router de la contraseña, no de toda la API."""
    r = navegador(app).get("/api/v1/me/consorcios")
    assert r.status_code == 401 and "cache-control" not in r.headers
