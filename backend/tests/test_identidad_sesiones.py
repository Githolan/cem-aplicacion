"""Listar y revocar las sesiones propias (RF04) contra PostgreSQL 18 y Redis 7 reales
(documento 13, §10.4; plan H2 2a).

Cada prueba usa personas nuevas, así las listas se comparan enteras. Los navegadores y el
camino de entrada (pedir y canjear el enlace) son los de `test_identidad.py`.
"""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.identidad import dispositivos, router, sesiones
from app.limite import FAMILIAS
from tests.test_identidad import (  # noqa: F401  (`app` es un fixture)
    SESION,
    app,
    csrf,
    es_401,
    navegador,
    set_cookies,
)
from tests.test_identidad_cierre_y_purga import entrar_como
from tests.test_identidad_tabla import nueva_persona

pytestmark = [pytest.mark.db, pytest.mark.redis]

SESIONES = "/api/v1/auth/sesiones"
REVOCAR_OTRAS = f"{SESIONES}/revocar-otras"
CHROME_ANDROID = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko)"
    " Chrome/131.0.0.0 Mobile Safari/537.36"
)
SAFARI_IPHONE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15"
    " (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"
)


def revocar(sesion_id) -> str:
    return f"{SESIONES}/{sesion_id}/revocar"


def sesion_id(c: TestClient) -> str:
    return c.get(SESION).json()["sesion_id"]


def revocada_en(motor_migrate, id_: str):
    with motor_migrate.connect() as conn:
        return conn.execute(
            text("select revocada_en from nucleo.sesion where id = :s"), {"s": id_}
        ).scalar_one()


# Listar -----------------------------------------------------------------------------------


def test_lista_solo_las_vigentes_propias_con_la_actual_marcada(app, motor_migrate):  # noqa: F811
    _, ana = nueva_persona(motor_migrate)
    _, beto = nueva_persona(motor_migrate)
    telefono = entrar_como(app, motor_migrate, ana, CHROME_ANDROID)
    iphone = entrar_como(app, motor_migrate, ana, SAFARI_IPHONE)
    revocada = entrar_como(app, motor_migrate, ana)
    vencida = entrar_como(app, motor_migrate, ana)
    de_beto = entrar_como(app, motor_migrate, beto, CHROME_ANDROID)
    ids = {n: sesion_id(c) for n, c in [("telefono", telefono), ("iphone", iphone),
           ("revocada", revocada), ("vencida", vencida), ("beto", de_beto)]}  # fmt: skip
    assert revocada.post(revocar(ids["revocada"]), headers=csrf(revocada)).status_code == 204
    with motor_migrate.begin() as conn:
        conn.execute(
            text(
                "update nucleo.sesion set creada_en = creada_en - interval '31 days',"
                " ultimo_uso_en = ultimo_uso_en - interval '31 days',"
                " vence_en = vence_en - interval '31 days' where id = :s"
            ),
            {"s": ids["vencida"]},
        )
    r = telefono.get(SESIONES)
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-store"
    elementos = r.json()["elementos"]
    # La consulta no filtra por persona: lo que separa a Ana de Beto es la RLS.
    assert {e["id"] for e in elementos} == {ids["telefono"], ids["iphone"]}
    por_id = {e["id"]: e for e in elementos}
    assert por_id[ids["telefono"]]["actual"] is True
    assert por_id[ids["iphone"]]["actual"] is False
    assert por_id[ids["telefono"]]["dispositivo"] == "Chrome en Android"
    assert por_id[ids["iphone"]]["dispositivo"] == "Safari en iPhone"
    assert set(elementos[0]) == {
        "id", "dispositivo", "creada_en", "ultimo_uso_en", "vence_en", "actual"
    }  # fmt: skip
    # Ni el agente crudo, ni la IP, ni el hash, ni el valor de la cookie.
    with motor_migrate.connect() as conn:
        filas = conn.execute(
            text("select agente, host(ip) as ip, encode(token_hash, 'hex') as hash"
                 " from nucleo.sesion where id = any(cast(:ids as uuid[]))"),
            {"ids": [ids["telefono"], ids["iphone"]]},
        ).all()  # fmt: skip
    prohibidos = [x for f in filas for x in f] + [telefono.cookies[sesiones.COOKIE_SESION]]
    assert len(prohibidos) == 7 and all(prohibidos)  # control: hay qué buscar
    assert not [x for x in prohibidos if x in r.text]
    # Desde el otro navegador, la actual es la otra.
    actuales = [e["id"] for e in iphone.get(SESIONES).json()["elementos"] if e["actual"]]
    assert actuales == [ids["iphone"]]
    # Beto ve solo la suya.
    assert [e["id"] for e in de_beto.get(SESIONES).json()["elementos"]] == [ids["beto"]]


def test_sin_sesion_las_tres_rutas_responden_401(app):  # noqa: F811
    c = navegador(app)
    assert es_401(c.get(SESIONES), "no_autenticado")
    assert es_401(c.post(REVOCAR_OTRAS), "no_autenticado")
    assert es_401(c.post(revocar(uuid4())), "no_autenticado")


# Revocar una ------------------------------------------------------------------------------


def test_revocar_una_propia_corta_el_pedido_siguiente(app, motor_migrate):  # noqa: F811
    _, ana = nueva_persona(motor_migrate)
    uno, otro = entrar_como(app, motor_migrate, ana), entrar_como(app, motor_migrate, ana)
    id_otro = sesion_id(otro)
    assert otro.get(SESION).status_code == 200  # control
    r = uno.post(revocar(id_otro), headers=csrf(uno))
    assert r.status_code == 204
    assert sesiones.COOKIE_SESION not in set_cookies(r)  # no es la actual: no borra cookies
    assert es_401(otro.get(SESION), "no_autenticado")
    assert uno.get(SESION).status_code == 200
    assert [e["id"] for e in uno.get(SESIONES).json()["elementos"]] == [sesion_id(uno)]
    # Repetirlo responde igual y conserva la fecha de la primera revocación.
    primera = revocada_en(motor_migrate, id_otro)
    assert primera is not None
    assert uno.post(revocar(id_otro), headers=csrf(uno)).status_code == 204
    assert revocada_en(motor_migrate, id_otro) == primera


def test_revocar_la_actual_borra_sus_cookies(app, motor_migrate):  # noqa: F811
    _, ana = nueva_persona(motor_migrate)
    c = entrar_como(app, motor_migrate, ana)
    r = c.post(revocar(sesion_id(c)), headers=csrf(c))
    assert r.status_code == 204
    assert "max-age=0" in set_cookies(r)[sesiones.COOKIE_SESION].lower()
    assert es_401(c.get(SESION), "no_autenticado")


def test_revocar_una_ajena_o_inexistente_da_404_indistinto_y_no_la_toca(app, motor_migrate):  # noqa: F811
    _, ana = nueva_persona(motor_migrate)
    _, beto = nueva_persona(motor_migrate)
    c = entrar_como(app, motor_migrate, ana)
    de_beto = entrar_como(app, motor_migrate, beto)
    id_beto = sesion_id(de_beto)
    ajena = c.post(revocar(id_beto), headers=csrf(c))
    inexistente = c.post(revocar(uuid4()), headers=csrf(c))
    assert ajena.status_code == inexistente.status_code == 404
    sin_correlacion = [{k: v for k, v in x.json().items() if k != "id_correlacion"}
                       for x in (ajena, inexistente)]  # fmt: skip
    assert sin_correlacion[0] == sin_correlacion[1]
    assert sin_correlacion[0]["codigo"] == "no_encontrado"
    assert revocada_en(motor_migrate, id_beto) is None
    assert de_beto.get(SESION).status_code == 200
    # Control: Beto sí puede revocarla.
    assert de_beto.post(revocar(id_beto), headers=csrf(de_beto)).status_code == 204


# Revocar las otras ------------------------------------------------------------------------


def test_revocar_las_otras_deja_viva_la_actual(app, motor_migrate):  # noqa: F811
    _, ana = nueva_persona(motor_migrate)
    _, beto = nueva_persona(motor_migrate)
    actual, *otras = (entrar_como(app, motor_migrate, ana) for _ in range(3))
    de_beto = entrar_como(app, motor_migrate, beto)
    assert all(o.get(SESION).status_code == 200 for o in otras)  # control
    r = actual.post(REVOCAR_OTRAS, headers=csrf(actual))
    assert r.status_code == 200
    assert r.json() == {"revocadas": 2}
    assert actual.get(SESION).status_code == 200
    assert all(es_401(o.get(SESION), "no_autenticado") for o in otras)
    assert de_beto.get(SESION).status_code == 200  # otra persona: intacta
    assert [e["actual"] for e in actual.get(SESIONES).json()["elementos"]] == [True]
    # Repetirlo no revoca nada más.
    assert actual.post(REVOCAR_OTRAS, headers=csrf(actual)).json() == {"revocadas": 0}


# CSRF y límite de tasa --------------------------------------------------------------------


def test_revocar_sin_csrf_da_403_y_no_revoca(app, motor_migrate):  # noqa: F811
    _, ana = nueva_persona(motor_migrate)
    c, otra = entrar_como(app, motor_migrate, ana), entrar_como(app, motor_migrate, ana)
    id_otra = sesion_id(otra)
    for encabezados in ({}, {sesiones.ENCABEZADO_CSRF: "x" * 64}, csrf(otra)):
        for ruta in (revocar(id_otra), REVOCAR_OTRAS):
            r = c.post(ruta, headers=encabezados)
            assert r.status_code == 403, (ruta, encabezados)
            assert r.json()["codigo"] == "csrf_invalido"
    assert revocada_en(motor_migrate, id_otra) is None
    assert otra.get(SESION).status_code == 200
    assert c.post(revocar(id_otra), headers=csrf(c)).status_code == 204  # control


def test_las_rutas_de_sesiones_estan_en_la_familia_de_autenticacion():
    """El cupo de 20 por minuto por IP (§10.6) lo aplica el middleware por prefijo."""
    [auth] = [f for f in FAMILIAS if f.nombre == "auth-ip"]
    rutas = [r.path for r in router.router.routes if "/sesiones" in r.path]
    assert len(rutas) == 3  # control: las encontró
    assert all(r.startswith(auth.prefijo) for r in rutas)


# Descripción del dispositivo --------------------------------------------------------------


@pytest.mark.parametrize(
    ("agente", "esperado"),
    [
        (CHROME_ANDROID, "Chrome en Android"),
        (SAFARI_IPHONE, "Safari en iPhone"),
        ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)"
         " Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0", "Edge en Windows"),
        ("Mozilla/5.0 (Macintosh; Intel Mac OS X 14.5; rv:132.0) Gecko/20100101 Firefox/132.0",
         "Firefox en Mac"),
        ("Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like"
         " Gecko) CriOS/131.0 Mobile/15E148 Safari/604.1", "Chrome en iPad"),
        ("Mozilla/5.0 (Linux; Android 14; SM-S911B) AppleWebKit/537.36 (KHTML, like Gecko)"
         " SamsungBrowser/26.0 Chrome/122.0.0.0 Mobile Safari/537.36",
         "Samsung Internet en Android"),
        ("curl/8.5.0", dispositivos.DESCONOCIDO),
        ("", dispositivos.DESCONOCIDO),
        (None, dispositivos.DESCONOCIDO),
    ],
)  # fmt: skip
def test_descripcion_del_dispositivo(agente, esperado):
    assert dispositivos.describir(agente) == esperado
