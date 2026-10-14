"""Enlace mágico, sesiones y CSRF contra PostgreSQL 18 y Redis 7 reales (documento 13, §10.3,
§10.4 y §10.6; plan H2 2a).

Cada cliente es un navegador: su propio frasco de cookies y su propia IP. La URL base es
`https://`, porque las cookies `__Host-` son `Secure` y un cliente no las devuelve por `http`.
El enlace se saca del evento que quedó en el outbox, como lo leería el trabajo de correo.
"""

import hashlib
import json
import re
import secrets
import statistics
import time
from collections.abc import Iterator
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

import app.plataforma.tareas  # noqa: F401  (ruta correo.solicitado → notificar.correo)
from app.config import Config
from app.identidad import eventos, router, sesiones
from app.limite import LIMITE_AUTENTICACION_CORREO, LIMITE_AUTENTICACION_IP
from app.main import crear_app
from app.plataforma import correo, outbox
from app.registros import redactar
from tests.conftest import REDIS_URL, url_rol
from tests.test_registros import salida_json  # noqa: F401  (fixture)

pytestmark = [pytest.mark.db, pytest.mark.redis]

ORIGEN = "https://cem.ejemplo.test"
ENLACE = "/api/v1/auth/enlace"
CANJE = "/api/v1/auth/canje"
SESION = "/api/v1/auth/sesion"
SALIR = "/api/v1/auth/salir"
ANA = "ana@ejemplo.com"
NADIE = "nadie@ejemplo.com"


def app_identidad(origen: str = ORIGEN):
    return crear_app(
        Config(url_rol("app"), "0.1.0", "s", "s", redis_url=REDIS_URL, origen_publico=origen)
    )


class Navegadores:
    """Abre navegadores: cada uno con su app, su bucle de eventos, sus cookies y su IP.

    Una app por navegador, como un proceso de uvicorn por cliente: el almacén asíncrono del
    límite de tasa queda atado al bucle de eventos del primer pedido y cada `TestClient` tiene
    el suyo. Todas comparten la base (el motor de `cem_app`) y el Redis reales.
    """

    def __init__(self, motor, origen: str = ORIGEN) -> None:
        self.motor, self.origen, self.abiertos = motor, origen, []
        self.ips = iter(range(1, 255))

    def abrir(self, ip: str | None = None, **kw) -> TestClient:
        app = app_identidad(self.origen)
        app.state.motor = self.motor
        c = TestClient(
            app,
            base_url="https://testserver",
            client=(ip or f"198.51.100.{next(self.ips)}", 40000),
            **kw,
        )
        c.__enter__()
        self.abiertos.append(c)
        return c

    def cerrar(self) -> None:
        for c in self.abiertos:
            c.__exit__(None, None, None)


@pytest.fixture
def app(motor_app, datos, redis_limpio) -> Iterator[Navegadores]:
    navegadores = Navegadores(motor_app)
    yield navegadores
    navegadores.cerrar()


def navegador(app: Navegadores, ip: str | None = None, **kw) -> TestClient:
    return app.abrir(ip, **kw)


def pedir(c: TestClient, direccion: str):
    return c.post(ENLACE, json={"correo": direccion})


def eventos_de(motor_migrate, direccion: str) -> list[dict]:
    with motor_migrate.connect() as conn:
        return list(
            conn.execute(
                text(
                    "select carga from plataforma.outbox where evento = :e"
                    " and carga->>'para' = :c order by id"
                ),
                {"e": correo.EVENTO, "c": direccion},
            ).scalars()
        )


def ultimo_token(motor_migrate, direccion: str) -> str:
    texto = eventos_de(motor_migrate, direccion)[-1]["texto"]
    [token] = re.findall(rf"{re.escape(ORIGEN + CANJE)}\?token=([A-Za-z0-9_-]{{43}})", texto)
    return token


def h(valor: str) -> bytes:
    return hashlib.sha256(valor.encode()).digest()


def entrar(app, motor_migrate, direccion: str = ANA) -> TestClient:
    c = navegador(app)
    assert pedir(c, direccion).status_code == 202
    r = c.get(CANJE, params={"token": ultimo_token(motor_migrate, direccion)})
    assert r.status_code == 200, r.text
    return c


def csrf(c: TestClient) -> dict[str, str]:
    return {sesiones.ENCABEZADO_CSRF: c.cookies[sesiones.COOKIE_CSRF]}


def set_cookies(r) -> dict[str, str]:
    """Nombre → atributos (sin el valor) de cada Set-Cookie de la respuesta."""
    salida = {}
    for linea in r.headers.get_list("set-cookie"):
        nombre_valor, _, atributos = linea.partition(";")
        salida[nombre_valor.split("=", 1)[0]] = atributos.strip()
    return salida


def es_401(r, codigo: str) -> bool:
    return r.status_code == 401 and r.json()["codigo"] == codigo


# Pedido del enlace ------------------------------------------------------------------------


REPETICIONES_TIEMPO = 3
"""Pedidos por rama, intercalados (con cuenta, sin cuenta, con cuenta…): la carga de la máquina
pega por igual en las dos ramas. No más de 5 por correo: es el cupo por minuto."""


def test_misma_respuesta_y_mismo_tiempo_exista_o_no_la_cuenta(
    app,
    motor_migrate,
    salida_json,  # noqa: F811
):
    """El tiempo no delata la cuenta. Lo que importa, y lo que se comprueba:

    1. Ninguna respuesta sale antes de `DURACION_MINIMA_PEDIDO`, en ninguna rama (sin la espera,
       la rama sin cuenta responde en milisegundos).
    2. Las dos ramas terminan su trabajo dentro de esa espera: el servidor registra una
       advertencia si una la supera. Es una medición del propio servidor, sin la latencia del
       cliente de pruebas, así que no depende de la carga de la máquina.
    3. Las medianas de las dos ramas no difieren en más de la mitad de la espera mínima. Una
       rama que se saltara la espera difiere en la espera entera; el ruido de la carga afecta a
       las dos por igual y la mediana descarta el pico de una repetición suelta.
    """
    antes = len(eventos_de(motor_migrate, ANA))
    respuestas, duraciones = [], {ANA: [], NADIE: []}
    for direccion in (ANA, NADIE) * REPETICIONES_TIEMPO:
        inicio = time.monotonic()
        respuestas.append(pedir(navegador(app), direccion))
        duraciones[direccion].append(time.monotonic() - inicio)
    assert {r.status_code for r in respuestas} == {202}
    assert len({r.content for r in respuestas}) == 1
    # Mismas cookies con los mismos atributos; solo cambian los valores aleatorios.
    assert len({json.dumps(set_cookies(r), sort_keys=True) for r in respuestas}) == 1
    assert len({tuple(sorted(r.headers.keys())) for r in respuestas}) == 1
    minimo = router.DURACION_MINIMA_PEDIDO.total_seconds()
    assert min(min(d) for d in duraciones.values()) >= minimo, duraciones
    lineas = salida_json()
    pedidos = [x for x in lineas if x["mensaje"] == "enlace de acceso pedido"]
    # Control: el servidor registró los pedidos de las dos ramas.
    assert sorted(x["cuenta_existe"] for x in pedidos) == sorted(
        [True, False] * REPETICIONES_TIEMPO
    )
    assert not [x for x in lineas if "superó la duración mínima" in x["mensaje"]]
    medianas = {d: statistics.median(t) for d, t in duraciones.items()}
    assert abs(medianas[ANA] - medianas[NADIE]) < minimo / 2, duraciones
    # Solo la cuenta que existe recibe correo.
    assert len(eventos_de(motor_migrate, ANA)) == antes + REPETICIONES_TIEMPO
    assert eventos_de(motor_migrate, NADIE) == []


def test_las_cookies_del_pedido_son_host_secure_httponly_lax(app):
    cookies = set_cookies(pedir(navegador(app), ANA))
    assert set(cookies) == {sesiones.COOKIE_NONCE, sesiones.COOKIE_DISPOSITIVO}
    for atributos in cookies.values():
        partes = {p.strip().lower() for p in atributos.split(";")}
        assert {"httponly", "secure", "samesite=lax", "path=/"} <= partes
        assert not any(p.startswith("domain") for p in partes)


def test_el_evento_queda_en_el_outbox_y_se_despacha_como_notificar_correo(
    app, motor_migrate, motor_app
):
    assert pedir(navegador(app), ANA).status_code == 202
    carga = eventos_de(motor_migrate, ANA)[-1]
    assert set(carga) == {"para", "asunto", "texto"}
    assert carga["asunto"] == eventos.ASUNTO_ENLACE
    token = ultimo_token(motor_migrate, ANA)
    with motor_migrate.connect() as conn:
        # Mismo token en el correo y en el enlace registrado: misma transacción.
        assert (
            conn.execute(
                text("select count(*) from nucleo.enlace_acceso where token_hash = :t"),
                {"t": h(token)},
            ).scalar_one()
            == 1
        )
        # El valor no está en la tabla del enlace (solo su hash).
        fila = conn.execute(
            text("select e::text from nucleo.enlace_acceso e where token_hash = :t"),
            {"t": h(token)},
        ).scalar_one()
        assert token not in fila
    outbox.despachar(motor_app)
    with motor_migrate.connect() as conn:
        tipos = conn.execute(
            text(
                "select tipo from plataforma.jobs where carga->>'evento' = :e"
                " and carga->>'texto' like :t"
            ),
            {"e": correo.EVENTO, "t": f"%{token}%"},
        ).scalars()
        assert list(tipos) == [correo.TIPO]


def test_si_falla_la_publicacion_no_queda_ni_el_enlace_ni_el_evento(
    app, motor_migrate, monkeypatch
):
    def contar() -> tuple[int, int]:
        with motor_migrate.connect() as conn:
            return (
                conn.execute(text("select count(*) from nucleo.enlace_acceso")).scalar_one(),
                conn.execute(text("select count(*) from plataforma.outbox")).scalar_one(),
            )

    publicar = outbox.publicar

    def publicar_y_fallar(conn, *a, **kw):
        publicar(conn, *a, **kw)  # el insert real ocurre
        raise RuntimeError("falla después de publicar")

    antes = contar()
    monkeypatch.setattr(outbox, "publicar", publicar_y_fallar)
    r = pedir(navegador(app, raise_server_exceptions=False), ANA)
    assert r.status_code == 500
    assert contar() == antes
    monkeypatch.undo()
    assert pedir(navegador(app), ANA).status_code == 202
    assert contar() == (antes[0] + 1, antes[1] + 1)  # control: sin la falla, quedan los dos


def test_sin_origen_publico_el_pedido_responde_503(motor_app, datos, redis_limpio):
    sin_origen = Navegadores(motor_app, origen="")
    r = pedir(sin_origen.abrir(), ANA)
    sin_origen.cerrar()
    assert r.status_code == 503
    assert r.json()["codigo"] == "servicio_no_disponible"


@pytest.mark.parametrize("origen", ["http://cem.ejemplo.test", "https://cem.ejemplo.test/"])
def test_un_origen_mal_escrito_impide_el_arranque(origen):
    with pytest.raises(RuntimeError, match="CEM_ORIGEN_PUBLICO"):
        app_identidad(origen)


# Canje ------------------------------------------------------------------------------------


def test_canje_feliz_crea_la_sesion_y_sus_cookies(app, motor_migrate, datos):
    c = navegador(app)
    pedir(c, ANA)
    r = c.get(CANJE, params={"token": ultimo_token(motor_migrate, ANA)})
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-store"
    cuerpo = r.json()
    assert (cuerpo["persona_id"], cuerpo["correo"]) == (str(datos.persona_a), ANA)
    cookies = set_cookies(r)
    partes = {n: {p.strip().lower() for p in a.split(";")} for n, a in cookies.items()}
    assert {"httponly", "secure", "samesite=lax", "path=/"} <= partes[sesiones.COOKIE_SESION]
    assert {"secure", "samesite=lax", "path=/"} <= partes[sesiones.COOKIE_CSRF]
    assert "httponly" not in partes[sesiones.COOKIE_CSRF]  # el frontend lo tiene que leer
    assert "max-age=0" in partes[sesiones.COOKIE_NONCE]  # el nonce se borra al canjear
    # Quién soy, y una ruta protegida que antes respondía 401.
    yo = c.get(SESION)
    assert yo.status_code == 200
    assert yo.json()["sesion_id"] == cuerpo["sesion_id"]
    assert [x["id"] for x in c.get("/api/v1/me/consorcios").json()] == [str(datos.consorcio_a)]
    # En la base, la sesión con persona, dispositivo, agente e IP; el token solo como hash.
    valor = c.cookies[sesiones.COOKIE_SESION]
    with motor_migrate.connect() as conn:
        fila = conn.execute(
            text("select s.*, s::text as todo from nucleo.sesion s where token_hash = :t"),
            {"t": h(valor)},
        ).one()
    assert fila.persona_id == datos.persona_a
    assert str(fila.ip).startswith("198.51.100.")
    assert fila.agente and fila.dispositivo and fila.revocada_en is None
    assert fila.vence_en - fila.creada_en == sesiones.VIGENCIA_SESION
    assert valor not in fila.todo


def test_un_token_reusado_no_entra(app, motor_migrate):
    c = navegador(app)
    pedir(c, ANA)
    token = ultimo_token(motor_migrate, ANA)
    nonce = c.cookies[sesiones.COOKIE_NONCE]
    assert c.get(CANJE, params={"token": token}).status_code == 200
    # Doble clic o reenvío: el mismo navegador, con el mismo nonce, vuelve a abrir el enlace.
    c.cookies.set(sesiones.COOKIE_NONCE, nonce, domain="testserver.local")
    assert es_401(c.get(CANJE, params={"token": token}), "enlace_invalido")


def test_un_token_vencido_no_entra_y_no_se_consume(app, motor_migrate):
    c = navegador(app)
    pedir(c, ANA)
    token = ultimo_token(motor_migrate, ANA)
    with motor_migrate.begin() as conn:
        conn.execute(
            text(
                "update nucleo.enlace_acceso set creado_en = creado_en - interval '16 minutes',"
                " vence_en = vence_en - interval '16 minutes' where token_hash = :t"
            ),
            {"t": h(token)},
        )
    assert es_401(c.get(CANJE, params={"token": token}), "enlace_invalido")
    with motor_migrate.connect() as conn:
        usado = conn.execute(
            text("select usado_en from nucleo.enlace_acceso where token_hash = :t"),
            {"t": h(token)},
        ).scalar_one()
    assert usado is None


def test_un_enlace_abierto_en_otro_navegador_no_entra_ni_se_gasta(app, motor_migrate):
    pide, otro = navegador(app), navegador(app)
    pedir(pide, ANA)
    token = ultimo_token(motor_migrate, ANA)
    # Sin cookie nonce (un escáner de correo, otro dispositivo).
    assert es_401(otro.get(CANJE, params={"token": token}), "enlace_invalido")
    # Con un nonce propio, de otro pedido.
    pedir(otro, NADIE)
    assert sesiones.COOKIE_NONCE in otro.cookies
    assert es_401(otro.get(CANJE, params={"token": token}), "enlace_invalido")
    # Control: el navegador que lo pidió todavía puede usarlo.
    assert pide.get(CANJE, params={"token": token}).status_code == 200


def test_un_token_inventado_no_entra(app):
    c = navegador(app)
    pedir(c, ANA)
    assert es_401(c.get(CANJE, params={"token": secrets.token_urlsafe(32)}), "enlace_invalido")
    r = c.get(CANJE, params={"token": "corto"})
    assert r.status_code == 422
    assert "corto" not in r.text


def test_dos_enlaces_pedidos_en_el_mismo_navegador_sirven_los_dos(app, motor_migrate):
    c = navegador(app)
    pedir(c, ANA)
    primero = ultimo_token(motor_migrate, ANA)
    pedir(c, ANA)
    segundo = ultimo_token(motor_migrate, ANA)
    assert c.get(CANJE, params={"token": segundo}).status_code == 200
    # El nonce se borró al canjear: el primero ya no sirve en este navegador.
    assert es_401(c.get(CANJE, params={"token": primero}), "enlace_invalido")


# Sesiones ---------------------------------------------------------------------------------


def test_sin_cookie_o_con_una_inventada_responde_401(app):
    c = navegador(app)
    assert es_401(c.get(SESION), "no_autenticado")
    c.cookies.set(sesiones.COOKIE_SESION, secrets.token_urlsafe(32), domain="testserver.local")
    assert es_401(c.get(SESION), "no_autenticado")
    c.cookies.set(sesiones.COOKIE_SESION, "no-es-un-token", domain="testserver.local")
    assert es_401(c.get(SESION), "no_autenticado")


def test_salir_revoca_la_sesion(app, motor_migrate):
    c = entrar(app, motor_migrate)
    valor = c.cookies[sesiones.COOKIE_SESION]
    # Otro navegador con una copia de la cookie (un ladrón): antes de salir, entra (control).
    copia = navegador(app)
    copia.cookies.set(sesiones.COOKIE_SESION, valor, domain="testserver.local")
    assert copia.get(SESION).status_code == 200
    r = c.post(SALIR, headers=csrf(c))
    assert r.status_code == 204
    assert "max-age=0" in set_cookies(r)[sesiones.COOKIE_SESION].lower()
    assert es_401(c.get(SESION), "no_autenticado")
    # La copia ya no sirve: la sesión se revocó en la base, no solo se borró la cookie.
    assert es_401(copia.get(SESION), "no_autenticado")
    with motor_migrate.connect() as conn:
        revocada = conn.execute(
            text("select revocada_en from nucleo.sesion where token_hash = :t"), {"t": h(valor)}
        ).scalar_one()
    assert revocada is not None


def test_una_sesion_vencida_no_entra(app, motor_migrate):
    c = entrar(app, motor_migrate)
    assert c.get(SESION).status_code == 200  # control
    with motor_migrate.begin() as conn:
        conn.execute(
            text(
                "update nucleo.sesion set creada_en = creada_en - interval '31 days',"
                " ultimo_uso_en = ultimo_uso_en - interval '31 days',"
                " vence_en = vence_en - interval '31 days' where token_hash = :t"
            ),
            {"t": h(c.cookies[sesiones.COOKIE_SESION])},
        )
    assert es_401(c.get(SESION), "no_autenticado")


def test_una_persona_dada_de_baja_pierde_la_sesion(app, motor_migrate):
    with motor_migrate.begin() as conn:
        conn.execute(
            text("insert into nucleo.persona (correo, nombre) values ('eva@ejemplo.com', 'Eva')")
        )
    c = entrar(app, motor_migrate, "eva@ejemplo.com")
    assert c.get(SESION).status_code == 200  # control
    with motor_migrate.begin() as conn:
        conn.execute(
            text("update nucleo.persona set eliminado_en = now() where correo = 'eva@ejemplo.com'")
        )
    assert es_401(c.get(SESION), "no_autenticado")


def test_el_vencimiento_se_desliza_con_el_uso(app, motor_migrate):
    c = entrar(app, motor_migrate)
    t = {"t": h(c.cookies[sesiones.COOKIE_SESION])}
    # Recién creada: no se renueva en cada petición.
    assert sesiones.COOKIE_SESION not in set_cookies(c.get(SESION))
    with motor_migrate.begin() as conn:
        conn.execute(
            text(
                "update nucleo.sesion set creada_en = creada_en - interval '10 days',"
                " ultimo_uso_en = ultimo_uso_en - interval '10 days',"
                " vence_en = vence_en - interval '10 days' where token_hash = :t"
            ),
            t,
        )
    r = c.get(SESION)
    assert r.status_code == 200
    assert sesiones.COOKIE_SESION in set_cookies(r)  # cookie reemitida con 30 días nuevos
    with motor_migrate.connect() as conn:
        restante = conn.execute(
            text("select vence_en - now() from nucleo.sesion where token_hash = :t"), t
        ).scalar_one()
    assert restante > sesiones.VIGENCIA_SESION - timedelta(minutes=1)


# CSRF -------------------------------------------------------------------------------------


def test_csrf_faltante_o_erroneo_se_rechaza(app, motor_migrate):
    c = entrar(app, motor_migrate)
    otra = entrar(app, motor_migrate, "beto@ejemplo.com")
    for encabezados in (
        {},
        {sesiones.ENCABEZADO_CSRF: ""},
        {sesiones.ENCABEZADO_CSRF: "x" * 64},
        csrf(otra),  # el token válido de otra sesión
        {sesiones.ENCABEZADO_CSRF: c.cookies[sesiones.COOKIE_SESION]},  # el de la sesión misma
    ):
        r = c.post(SALIR, headers=encabezados)
        assert r.status_code == 403
        assert r.json()["codigo"] == "csrf_invalido"
    assert c.get(SESION).status_code == 200  # la sesión sigue viva
    assert c.post(SALIR, headers=csrf(c)).status_code == 204  # control


# Límite de tasa (§10.6) -------------------------------------------------------------------


def test_cinco_pedidos_por_minuto_por_correo(app):
    cupo = LIMITE_AUTENTICACION_CORREO.amount
    assert cupo == 5
    # Cada pedido desde otra IP: el cupo es del correo, no del cliente.
    assert {pedir(navegador(app), ANA).status_code for _ in range(cupo)} == {202}
    r = pedir(navegador(app), ANA.upper())  # el mismo correo, escrito distinto
    assert r.status_code == 429
    assert r.json()["codigo"] == "demasiadas_peticiones"
    assert 1 <= int(r.headers["Retry-After"]) <= 60
    # Un correo inexistente tiene su propio cupo y se comporta igual: no delata nada.
    assert {pedir(navegador(app), NADIE).status_code for _ in range(cupo)} == {202}
    assert pedir(navegador(app), NADIE).status_code == 429


def test_veinte_pedidos_por_minuto_por_ip_en_auth(app):
    cupo = LIMITE_AUTENTICACION_IP.amount
    assert cupo == 20
    c = navegador(app, ip="203.0.113.77")
    assert {c.get(SESION).status_code for _ in range(cupo)} == {401}
    r = c.get(SESION)
    assert r.status_code == 429
    assert c.get(CANJE, params={"token": secrets.token_urlsafe(32)}).status_code == 429
    # Fuera de /api/v1/auth/ rige solo el cupo general.
    assert c.get("/api/v1/me/consorcios").status_code == 401
    assert navegador(app, ip="203.0.113.78").get(SESION).status_code == 401


# Registros ----------------------------------------------------------------------------


def test_los_registros_no_llevan_token_nonce_ni_cookies(app, motor_migrate, salida_json):  # noqa: F811
    c = navegador(app)
    pedir(c, ANA)
    nonce = c.cookies[sesiones.COOKIE_NONCE]
    token = ultimo_token(motor_migrate, ANA)
    c.get(CANJE, params={"token": token})
    secretos = [token, nonce, c.cookies[sesiones.COOKIE_SESION], c.cookies[sesiones.COOKIE_CSRF]]
    c.post(SALIR, headers=csrf(c))
    lineas = salida_json()
    assert any(x["mensaje"] == "sesión creada" for x in lineas)  # control: se registró algo
    volcado = json.dumps(lineas)
    assert not [s for s in secretos if s in volcado]
    assert ANA not in volcado


@pytest.mark.parametrize(
    "texto",
    [
        f"{ORIGEN}/api/v1/auth/canje?token=SECRETO123",
        "Cookie: __Host-cem_sesion=SECRETO123; __Host-cem_csrf=SECRETO123",
        "__Host-cem_sesion=SECRETO123",
        "__Host-cem_nonce=SECRETO123",
        "X-CSRF-Token: SECRETO123",
    ],
)
def test_la_redaccion_cubre_tokens_y_cookies_de_identidad(texto):
    assert "SECRETO123" not in redactar(texto)
