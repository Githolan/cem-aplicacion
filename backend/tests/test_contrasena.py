"""Contraseña con Argon2id y entrada con correo y contraseña (documento 13, §10.3 y §15.2;
migración 0009) contra PostgreSQL 18 y Redis 7 reales.

Cada prueba usa personas nuevas. Las que tienen segundo factor lo dan de alta por el camino
real (`con_factor`), con el reloj del TOTP fijo (`reloj`).
"""

import json
import statistics
import time

import pytest
from alembic import command
from argon2 import PasswordHasher
from limits import parse
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from app import limite
from app.db import transaccion
from app.identidad import contrasena as ct
from app.identidad import router, sesiones
from app.identidad import segundo_factor as sf
from tests.conftest import alembic_config, vaciar_mandatos, volver_a_subir
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
from tests.test_registros import salida_json  # noqa: F401  (fixture)
from tests.test_segundo_factor import (  # noqa: F401  (`reloj` es un fixture)
    avisos,
    con_factor,
    envejecer_sesion,
    reloj,
)

pytestmark = [pytest.mark.db, pytest.mark.redis]

BASE = "/api/v1/auth/contrasena"
ENTRAR = f"{BASE}/entrar"
PASO2 = f"{BASE}/segundo-factor"
CLAVE = "una frase larga de prueba"
OTRA = "otra frase distinta y larga"
NADIE = "nadie-contrasena@ejemplo.com"
FUNCIONES = (
    "nucleo.contrasena_de(text)",
    "nucleo.contrasena_vigente(uuid, text)",
    "nucleo.tiene_segundo_factor(uuid)",
    "nucleo.entrar_con_contrasena(uuid, text, bytea, uuid, text, inet, interval)",
    "nucleo.pedir_desafio_contrasena(uuid, text, bytea, interval)",
    "nucleo.desafio_contrasena_de(bytea, integer)",
    "nucleo.resolver_desafio_contrasena(bytea, boolean, bytea, uuid, text, inet, interval,"
    " integer)",
)


def definir(c, nueva: str = CLAVE, actual: str | None = None):
    cuerpo = {"contrasena_nueva": nueva}
    if actual is not None:
        cuerpo["contrasena_actual"] = actual
    return c.put(BASE, json=cuerpo, headers=csrf(c))


def con_contrasena(app, motor_migrate, clave: str = CLAVE):  # noqa: F811
    """Una persona nueva, con sesión por enlace y contraseña definida, sin segundo factor."""
    persona, correo = nueva_persona(motor_migrate)
    c = entrar_como(app, motor_migrate, correo)
    r = definir(c, clave)
    assert r.status_code == 200, r.text
    return persona, correo, c


def entrar(app, correo: str, clave: str = CLAVE, **kw):  # noqa: F811
    c = navegador(app, **kw)
    return c, c.post(ENTRAR, json={"correo": correo, "contrasena": clave})


def hash_de(motor_migrate, persona) -> str | None:
    with motor_migrate.connect() as conn:
        return conn.execute(
            text("select hash from nucleo.contrasena where persona_id = :p"), {"p": persona}
        ).scalar_one_or_none()


def desafio_de(motor_migrate, persona):
    with motor_migrate.connect() as conn:
        return conn.execute(
            text("select * from nucleo.desafio_contrasena where persona_id = :p"), {"p": persona}
        ).one_or_none()


def persona_de_correo(motor_migrate, correo: str):
    with motor_migrate.connect() as conn:
        return conn.execute(
            text("select id from nucleo.persona where correo = :c"), {"c": correo}
        ).scalar_one()


# Argon2id -----------------------------------------------------------------------------------


def test_el_hash_es_argon2id_con_los_parametros_fijados(app, motor_migrate):  # noqa: F811
    persona, _, _ = con_contrasena(app, motor_migrate)
    guardado = hash_de(motor_migrate, persona)
    assert guardado.startswith("$argon2id$v=19$m=65536,t=3,p=4$"), guardado
    assert CLAVE not in guardado
    assert PasswordHasher().verify(guardado, CLAVE)  # control: lo entiende cualquier Argon2id
    assert (ct.ARGON2_TIEMPO, ct.ARGON2_MEMORIA_KIB, ct.ARGON2_PARALELISMO) == (3, 65536, 4)


def test_la_misma_contrasena_da_hashes_distintos():
    assert ct.hashear(CLAVE) != ct.hashear(CLAVE)  # sal propia por hash


def test_la_normalizacion_iguala_la_misma_contrasena_escrita_distinto():
    compuesta, separada = "café con medialunas", "café con medialunas"
    assert compuesta != separada
    assert ct.verificar(ct.hashear(compuesta), separada)
    assert not ct.verificar(ct.hashear(compuesta), "cafe con medialunas")


def test_sin_hash_nunca_acierta():
    assert ct.verificar(None, CLAVE) is False


# Definir y entrar sin segundo factor --------------------------------------------------------


def test_definir_y_entrar_sin_segundo_factor(app, motor_migrate):  # noqa: F811
    persona, correo, c = con_contrasena(app, motor_migrate)
    estado = c.get(BASE).json()
    assert estado["definida"] is True and estado["cambiada_en"]
    assert avisos(motor_migrate, correo)[-1] == ct.ASUNTO_DEFINIDA
    nuevo, r = entrar(app, correo.upper())  # el correo, escrito distinto
    assert r.status_code == 200, r.text
    cuerpo = r.json()
    assert cuerpo["estado"] == "sesion_abierta"
    assert cuerpo["sesion"]["persona_id"] == str(persona)
    assert r.headers["cache-control"] == "no-store"
    assert {sesiones.COOKIE_SESION, sesiones.COOKIE_CSRF} <= set(set_cookies(r))
    assert sesiones.COOKIE_DESAFIO not in set_cookies(r)
    assert nuevo.get(SESION).json()["persona_id"] == str(persona)
    # Sin segundo factor, la sesión no queda verificada.
    assert nuevo.get("/api/v1/auth/totp").json()["verificado_en"] is None


def test_la_primera_contrasena_pide_una_entrada_reciente(app, motor_migrate):  # noqa: F811
    """Revisión H3: una sesión vieja que quedó abierta no define la primera contraseña."""
    _, correo = nueva_persona(motor_migrate)
    c = entrar_como(app, motor_migrate, correo)
    envejecer_sesion(motor_migrate, c)
    r = definir(c)
    assert (r.status_code, r.json()["codigo"], r.json()["detalle"]) == (
        403,
        "reautenticacion_requerida",
        {"falta": "entrar_de_nuevo"},
    )
    assert c.get(BASE).json()["definida"] is False
    reciente = entrar_como(app, motor_migrate, correo)
    assert definir(reciente).status_code == 200  # control: recién entró


def test_estado_sin_contrasena(app, motor_migrate):  # noqa: F811
    _, correo = nueva_persona(motor_migrate)
    c = entrar_como(app, motor_migrate, correo)
    assert c.get(BASE).json() == {"definida": False, "cambiada_en": None}


def test_definir_pide_csrf_y_sesion(app, motor_migrate):  # noqa: F811
    _, correo = nueva_persona(motor_migrate)
    c = entrar_como(app, motor_migrate, correo)
    r = c.put(BASE, json={"contrasena_nueva": CLAVE})
    assert r.status_code == 403 and r.json()["codigo"] == "csrf_invalido"
    assert es_401(navegador(app).put(BASE, json={"contrasena_nueva": CLAVE}), "no_autenticado")


def test_contrasena_debil(app, motor_migrate):  # noqa: F811
    _, correo = nueva_persona(motor_migrate)
    c = entrar_como(app, motor_migrate, correo)
    r = definir(c, "a" * 11)
    assert r.status_code == 422 and r.json()["codigo"] == "peticion_invalida"
    assert "a" * 11 not in r.text  # el 422 no devuelve lo enviado
    r = definir(c, correo.upper())
    assert r.status_code == 422 and r.json()["codigo"] == "contrasena_debil"
    assert definir(c, "a" * 12).status_code == 200  # control: el largo mínimo sirve


# Misma respuesta, mismo tiempo ----------------------------------------------------------------


REPETICIONES_TIEMPO = 3


def test_misma_respuesta_y_mismo_tiempo_sin_cuenta_sin_contrasena_o_incorrecta(
    app,  # noqa: F811
    motor_migrate,
    salida_json,  # noqa: F811
):
    _, con_clave, _ = con_contrasena(app, motor_migrate)
    _, sin_clave = nueva_persona(motor_migrate)
    casos = {
        "sin_cuenta": (NADIE, CLAVE),
        "cuenta_sin_contrasena": (sin_clave, CLAVE),
        "incorrecta": (con_clave, OTRA),
    }
    respuestas, duraciones = [], {n: [] for n in casos}
    for _ in range(REPETICIONES_TIEMPO):
        for nombre, (correo, clave) in casos.items():
            inicio = time.monotonic()
            respuestas.append(entrar(app, correo, clave)[1])
            duraciones[nombre].append(time.monotonic() - inicio)
    assert {r.status_code for r in respuestas} == {401}
    cuerpos = {json.dumps({**r.json(), "id_correlacion": None}) for r in respuestas}
    assert len(cuerpos) == 1 and "credenciales_invalidas" in cuerpos.pop()
    assert len({tuple(sorted(r.headers.keys())) for r in respuestas}) == 1
    assert {json.dumps(set_cookies(r)) for r in respuestas} == {"{}"}
    minimo = router.DURACION_MINIMA_PEDIDO.total_seconds()
    assert min(min(d) for d in duraciones.values()) >= minimo, duraciones
    lineas = salida_json()
    motivos = {
        x["mensaje"].rsplit(": ", 1)[1]
        for x in lineas
        if x["mensaje"].startswith("entrada con contraseña rechazada")
    }
    # Control: el servidor sí distinguió las causas (solo en los registros). Sin cuenta y
    # cuenta sin contraseña son la misma consulta vacía.
    assert motivos == {"sin_cuenta", "incorrecta"}
    assert not [x for x in lineas if "superó la duración mínima" in x["mensaje"]]
    medianas = [statistics.median(t) for t in duraciones.values()]
    assert max(medianas) - min(medianas) < minimo / 2, duraciones


def test_los_registros_nunca_llevan_la_contrasena(app, motor_migrate, salida_json):  # noqa: F811
    _, correo, _ = con_contrasena(app, motor_migrate)
    entrar(app, correo, OTRA)
    assert entrar(app, correo)[1].status_code == 200
    lineas = salida_json()
    mensajes = {x["mensaje"] for x in lineas}
    assert {"contraseña definida", "entrada con contraseña rechazada: incorrecta"} <= mensajes
    assert "contraseña aceptada" in mensajes  # control: se registró lo que pasó
    volcado = json.dumps(lineas, ensure_ascii=False)
    assert CLAVE not in volcado and OTRA not in volcado and "$argon2id$" not in volcado


def test_desde_otro_sitio_se_rechaza(app, motor_migrate):  # noqa: F811
    _, correo, _ = con_contrasena(app, motor_migrate)
    c = navegador(app, headers={"sec-fetch-site": "cross-site"})
    r = c.post(ENTRAR, json={"correo": correo, "contrasena": CLAVE})
    assert r.status_code == 403 and r.json()["codigo"] == "csrf_invalido"
    r = c.post(PASO2, json={"codigo": "000000"})
    assert r.status_code == 403 and r.json()["codigo"] == "csrf_invalido"


# Con segundo factor -------------------------------------------------------------------------


def factor_y_contrasena(app, motor_migrate, reloj):  # noqa: F811
    c, secreto, codigos, correo = con_factor(app, motor_migrate, reloj)
    # Recién confirmado el alta, la sesión está verificada: puede definir la contraseña.
    assert definir(c).status_code == 200
    return c, secreto, codigos, correo


def test_con_segundo_factor_la_contrasena_sola_no_abre_sesion(app, motor_migrate, reloj):  # noqa: F811
    _, secreto, _, correo = factor_y_contrasena(app, motor_migrate, reloj)
    c, r = entrar(app, correo)
    assert r.status_code == 200, r.text
    assert r.json()["estado"] == "falta_segundo_factor"
    cookies = set_cookies(r)
    assert sesiones.COOKIE_SESION not in cookies and sesiones.COOKIE_CSRF not in cookies
    assert "HttpOnly" in cookies[sesiones.COOKIE_DESAFIO]
    assert "Max-Age=300" in cookies[sesiones.COOKIE_DESAFIO]
    assert es_401(c.get(SESION), "no_autenticado")
    # Un código incorrecto no abre sesión.
    malo = c.post(PASO2, json={"codigo": sf.codigo_en(secreto, reloj.paso + 5)})
    assert malo.status_code == 400 and malo.json()["codigo"] == "segundo_factor_invalido"
    assert es_401(c.get(SESION), "no_autenticado")
    # El correcto (otro paso que el del alta: el del alta ya se usó) la abre, verificada.
    reloj.paso += 1
    bueno = c.post(PASO2, json={"codigo": sf.codigo_en(secreto, reloj.paso)})
    assert bueno.status_code == 200, bueno.text
    assert sesiones.COOKIE_SESION in set_cookies(bueno)
    assert c.get(SESION).status_code == 200
    assert c.get("/api/v1/auth/totp").json()["verificado_en"] is not None
    assert desafio_de(motor_migrate, persona_de_correo(motor_migrate, correo)) is None
    # El mismo código no sirve dos veces, ni con otra entrada.
    c2, r2 = entrar(app, correo)
    assert r2.json()["estado"] == "falta_segundo_factor"
    otra = c2.post(PASO2, json={"codigo": sf.codigo_en(secreto, reloj.paso)})
    assert otra.status_code == 400


def test_un_codigo_de_recuperacion_sirve_y_queda_gastado(app, motor_migrate, reloj):  # noqa: F811
    _, _, codigos, correo = factor_y_contrasena(app, motor_migrate, reloj)
    c, r = entrar(app, correo)
    assert r.json()["estado"] == "falta_segundo_factor"
    assert c.post(PASO2, json={"codigo": codigos[0]}).status_code == 200
    c2, r2 = entrar(app, correo)
    assert r2.json()["estado"] == "falta_segundo_factor"
    assert c2.post(PASO2, json={"codigo": codigos[0]}).status_code == 400
    assert c2.post(PASO2, json={"codigo": codigos[1]}).status_code == 200


def test_el_paso_sin_la_cookie_del_navegador_no_sirve(app, motor_migrate, reloj):  # noqa: F811
    _, secreto, _, correo = factor_y_contrasena(app, motor_migrate, reloj)
    assert entrar(app, correo)[1].json()["estado"] == "falta_segundo_factor"
    otro = navegador(app)
    reloj.paso += 1
    assert es_401(otro.post(PASO2, json={"codigo": sf.codigo_en(secreto, reloj.paso)}),
                  "entrada_vencida")  # fmt: skip


def test_un_codigo_incorrecto_descuenta_y_al_quinto_se_agota(app, motor_migrate, reloj):  # noqa: F811
    _, secreto, _, correo = factor_y_contrasena(app, motor_migrate, reloj)
    persona = persona_de_correo(motor_migrate, correo)
    c, r = entrar(app, correo)
    assert r.json()["estado"] == "falta_segundo_factor"
    assert c.post(PASO2, json={"codigo": "000000"}).status_code == 400
    assert desafio_de(motor_migrate, persona).intentos == 1
    # Los otros cuatro se cargan directo: el cupo por persona (5 por minuto) cortaría antes.
    with motor_migrate.begin() as conn:
        conn.execute(
            text("update nucleo.desafio_contrasena set intentos = :m where persona_id = :p"),
            {"m": ct.INTENTOS_DESAFIO, "p": persona},
        )
    reloj.paso += 1
    assert es_401(c.post(PASO2, json={"codigo": sf.codigo_en(secreto, reloj.paso)}),
                  "entrada_vencida")  # fmt: skip
    # Volver a escribir la contraseña da un desafío nuevo, con los intentos en cero.
    c2, r2 = entrar(app, correo)
    assert r2.json()["estado"] == "falta_segundo_factor"
    assert desafio_de(motor_migrate, persona).intentos == 0
    assert c2.post(PASO2, json={"codigo": sf.codigo_en(secreto, reloj.paso)}).status_code == 200


def test_un_desafio_vencido_no_sirve(app, motor_migrate, reloj):  # noqa: F811
    _, secreto, _, correo = factor_y_contrasena(app, motor_migrate, reloj)
    c, r = entrar(app, correo)
    assert r.json()["estado"] == "falta_segundo_factor"
    with motor_migrate.begin() as conn:
        conn.execute(
            text(
                "update nucleo.desafio_contrasena set creado_en = now() - interval '10 minutes',"
                " vence_en = now() - interval '6 minutes' where persona_id = :p"
            ),
            {"p": persona_de_correo(motor_migrate, correo)},
        )
    reloj.paso += 1
    assert es_401(c.post(PASO2, json={"codigo": sf.codigo_en(secreto, reloj.paso)}),
                  "entrada_vencida")  # fmt: skip


def test_el_paso_gasta_el_cupo_de_intentos_del_segundo_factor(app, motor_migrate, reloj):  # noqa: F811
    _, _, _, correo = factor_y_contrasena(app, motor_migrate, reloj)
    c, r = entrar(app, correo)
    assert r.json()["estado"] == "falta_segundo_factor"
    # El alta ya gastó uno del cupo de la persona (5 por minuto): quedan cuatro.
    for _ in range(4):
        assert c.post(PASO2, json={"codigo": "000000"}).status_code == 400
    r = c.post(PASO2, json={"codigo": "000000"})
    assert r.status_code == 429 and "Retry-After" in r.headers


def test_la_base_no_abre_sesion_sin_segundo_factor_a_quien_lo_tiene(
    app,  # noqa: F811
    motor_migrate,
    motor_app,
    reloj,  # noqa: F811
):
    _, _, _, correo = factor_y_contrasena(app, motor_migrate, reloj)
    persona = persona_de_correo(motor_migrate, correo)
    hash_ = hash_de(motor_migrate, persona)
    llamada = (
        "select resultado from nucleo.entrar_con_contrasena(:p, :h, :s, gen_random_uuid(),"
        " null, '198.51.100.1', interval '1 day')"
    )
    with transaccion(motor_app, persona_id=None) as conn:
        assert conn.execute(
            text(llamada), {"p": persona, "h": hash_, "s": b"\1" * 32}
        ).scalar() == ("falta_segundo_factor")
        assert conn.execute(text(llamada), {"p": persona, "h": "x", "s": b"\2" * 32}).scalar() == (
            "cambiada"
        )
    with motor_migrate.connect() as conn:
        assert (
            conn.execute(
                text("select count(*) from nucleo.sesion where token_hash in (:a, :b)"),
                {"a": b"\1" * 32, "b": b"\2" * 32},
            ).scalar_one()
            == 0
        )


# Cambiar --------------------------------------------------------------------------------------


def test_cambiar_sin_segundo_factor_pide_la_actual_y_cierra_las_otras(app, motor_migrate):  # noqa: F811
    _, correo, c = con_contrasena(app, motor_migrate)
    otra_sesion, r = entrar(app, correo)
    assert r.status_code == 200
    assert definir(c, OTRA).json()["codigo"] == "contrasena_actual_invalida"
    assert definir(c, OTRA, actual="no es esta para nada").status_code == 403
    r = definir(c, OTRA, actual=CLAVE)
    assert r.status_code == 200, r.text
    assert r.json()["sesiones_cerradas"] == 1
    assert es_401(otra_sesion.get(SESION), "no_autenticado")
    assert c.get(SESION).status_code == 200  # la del cambio sigue
    assert avisos(motor_migrate, correo)[-1] == ct.ASUNTO_CAMBIADA
    assert es_401(entrar(app, correo, CLAVE)[1], "credenciales_invalidas")
    assert entrar(app, correo, OTRA)[1].status_code == 200


def test_cambiar_con_segundo_factor_sin_verificacion_reciente_se_rechaza(
    app,  # noqa: F811
    motor_migrate,
    reloj,  # noqa: F811
):
    c, secreto, _, correo = factor_y_contrasena(app, motor_migrate, reloj)
    persona = persona_de_correo(motor_migrate, correo)
    # La verificación de esta sesión queda vieja (11 minutos).
    with motor_migrate.begin() as conn:
        conn.execute(
            text(
                "update nucleo.sesion set creada_en = now() - interval '1 hour',"
                " segundo_factor_en = now() - interval '11 minutes' where persona_id = :p"
            ),
            {"p": persona},
        )
    r = definir(c, OTRA)
    assert r.status_code == 403 and r.json()["codigo"] == "segundo_factor_requerido"
    # Ni siquiera con la contraseña actual: con segundo factor lo que vale es el factor.
    assert definir(c, OTRA, actual=CLAVE).status_code == 403
    # Una sesión nueva por enlace, que nunca verificó, tampoco.
    por_enlace = entrar_como(app, motor_migrate, correo)
    assert definir(por_enlace, OTRA).status_code == 403
    assert hash_de(motor_migrate, persona) and entrar(app, correo, CLAVE)[1].status_code == 200
    # Control: después de verificar, sí.
    reloj.paso += 1
    v = c.post("/api/v1/auth/totp/verificar", json={"codigo": sf.codigo_en(secreto, reloj.paso)},
               headers=csrf(c))  # fmt: skip
    assert v.status_code == 200, v.text
    assert definir(c, OTRA).status_code == 200


def test_cupo_de_cambios_por_persona(app, motor_migrate):  # noqa: F811
    _, _, c = con_contrasena(app, motor_migrate)  # gastó uno
    por_minuto, por_dia = limite.LIMITES_CAMBIO_CONTRASENA
    assert (por_minuto.amount, por_dia.amount) == (5, 20)
    for _ in range(por_minuto.amount - 1):
        assert definir(c, OTRA, actual="incorrecta incorrecta").status_code == 403
    assert definir(c, OTRA, actual=CLAVE).status_code == 429


# Límites de tasa de la entrada ----------------------------------------------------------------


def test_limite_por_correo_aunque_cambie_la_ip(app, motor_migrate):  # noqa: F811
    por_minuto, por_hora, por_dia = limite.LIMITES_CONTRASENA_CORREO
    assert (por_minuto.amount, por_hora.amount, por_dia.amount) == (5, 20, 100)
    _, correo, _ = con_contrasena(app, motor_migrate)
    for _ in range(por_minuto.amount):
        assert es_401(entrar(app, correo, OTRA)[1], "credenciales_invalidas")
    r = entrar(app, correo)[1]  # la correcta, desde otra IP
    assert r.status_code == 429 and 1 <= int(r.headers["Retry-After"]) <= 60
    # Sin cuenta, el mismo cupo y el mismo comportamiento.
    for _ in range(por_minuto.amount):
        assert es_401(entrar(app, NADIE)[1], "credenciales_invalidas")
    assert entrar(app, NADIE)[1].status_code == 429
    # Control: otro correo no está limitado.
    _, otro, _ = con_contrasena(app, motor_migrate)
    assert entrar(app, otro)[1].status_code == 200


def test_limite_por_ip_aunque_cambie_el_correo(app, monkeypatch):  # noqa: F811
    assert [(x.amount, x.get_expiry()) for x in limite.LIMITES_CONTRASENA_IP] == [(60, 3600)]
    # El de la familia (20 por minuto por IP) cortaría antes que 60 por hora: se baja a 3 para
    # medir el propio.
    monkeypatch.setattr(limite, "LIMITES_CONTRASENA_IP", (parse("3/hour"),))
    ip = "203.0.113.77"
    for i in range(3):
        assert es_401(entrar(app, f"x{i}@ejemplo.com", ip=ip)[1], "credenciales_invalidas")
    assert entrar(app, "x9@ejemplo.com", ip=ip)[1].status_code == 429
    assert entrar(app, "x9@ejemplo.com")[1].status_code == 401  # control: otra IP


# Seguridad por fila y privilegios (§8.5, §8.6) -------------------------------------------------


def test_otra_persona_no_ve_ni_toca_el_hash(app, motor_migrate, motor_app):  # noqa: F811
    ana, _, _ = con_contrasena(app, motor_migrate)
    beto, _, _ = con_contrasena(app, motor_migrate, OTRA)
    with transaccion(motor_app, persona_id=ana) as conn:
        assert conn.execute(text("select persona_id from nucleo.contrasena")).scalars().all() == [
            ana
        ]
        assert (
            conn.execute(
                text("select count(*) from nucleo.contrasena where persona_id = :b"), {"b": beto}
            ).scalar_one()
            == 0
        )
        tocadas = conn.execute(
            text("update nucleo.contrasena set hash = '$argon2id$x' where persona_id = :b"),
            {"b": beto},
        ).rowcount
        assert tocadas == 0
    with pytest.raises(ProgrammingError, match="row-level security"):
        with transaccion(motor_app, persona_id=ana) as conn:
            conn.execute(
                text("insert into nucleo.contrasena (persona_id, hash) values (:b, '$argon2id$x')"),
                {"b": beto},
            )
    with transaccion(motor_app, persona_id=None) as conn:
        assert conn.execute(text("select count(*) from nucleo.contrasena")).scalar_one() == 0
    # Control: las dos filas existen (cem_migrate no tiene RLS) y el hash de Beto no cambió.
    assert hash_de(motor_migrate, beto).startswith("$argon2id$v=19$")
    assert hash_de(motor_migrate, ana).startswith("$argon2id$v=19$")


def test_cem_app_no_toca_los_desafios_ni_borra_contrasenas(motor_app, datos):
    for sql in (
        "select count(*) from nucleo.desafio_contrasena",
        "update nucleo.desafio_contrasena set intentos = 0",
        "delete from nucleo.desafio_contrasena",
        "delete from nucleo.contrasena",
        "update nucleo.contrasena set creada_en = now()",
    ):
        with motor_app.begin() as conn, pytest.raises(ProgrammingError, match="permission denied"):
            conn.execute(text(sql))


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


def test_la_base_impone_los_topes(motor_migrate, datos):
    persona, _ = nueva_persona(motor_migrate)
    for sql in (
        "insert into nucleo.contrasena (persona_id, hash) values (:p, 'texto plano')",
        "insert into nucleo.desafio_contrasena (persona_id, token_hash, vence_en)"
        " values (:p, '\\x00', now() + interval '1 minute')",
        "insert into nucleo.desafio_contrasena (persona_id, token_hash, vence_en)"
        " values (:p, sha256('x'), now() + interval '6 minutes')",
        "insert into nucleo.desafio_contrasena (persona_id, token_hash, vence_en, intentos)"
        " values (:p, sha256('x'), now() + interval '1 minute', 6)",
    ):
        with motor_migrate.begin() as conn, pytest.raises(Exception, match="violates check"):
            conn.execute(text(sql), {"p": persona})


def test_un_hash_con_parametros_viejos_se_rehace_al_entrar(app, motor_migrate):  # noqa: F811
    persona, correo = nueva_persona(motor_migrate)
    viejo = PasswordHasher(time_cost=1, memory_cost=8192, parallelism=1).hash(CLAVE)
    with motor_migrate.begin() as conn:
        conn.execute(
            text("insert into nucleo.contrasena (persona_id, hash) values (:p, :h)"),
            {"p": persona, "h": viejo},
        )
    assert entrar(app, correo)[1].status_code == 200
    nuevo = hash_de(motor_migrate, persona)
    assert nuevo != viejo and nuevo.startswith("$argon2id$v=19$m=65536,t=3,p=4$")
    assert entrar(app, correo)[1].status_code == 200


def test_la_migracion_0009_baja_y_sube(base_de_prueba, motor_migrate):
    def existe() -> tuple[bool, bool, bool]:
        with motor_migrate.connect() as conn:
            return tuple(
                conn.execute(
                    text(
                        "select to_regclass('nucleo.contrasena') is not null,"
                        " to_regclass('nucleo.desafio_contrasena') is not null,"
                        " to_regclass('nucleo.factor_totp') is not null"
                    )
                ).one()
            )

    assert existe() == (True, True, True)
    cfg = alembic_config()
    vaciar_mandatos(motor_migrate)
    command.downgrade(cfg, "0008")
    assert existe() == (False, False, True)  # la 0008 queda
    volver_a_subir(cfg, motor_migrate)
    assert existe() == (True, True, True)
