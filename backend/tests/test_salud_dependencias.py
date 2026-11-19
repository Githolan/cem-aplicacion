"""`GET /api/v1/health/deps` contra PostgreSQL y Redis reales (documento 13, §16.2)."""

import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from redis import Redis
from sqlalchemy import event

from app.config import SALUD_CACHE_SEGUNDOS, Config
from app.main import crear_app
from tests.conftest import REDIS_URL, url_rol

pytestmark = [pytest.mark.db, pytest.mark.redis]

RUTA = "/api/v1/health/deps"
CAIDO_PG = "postgresql+psycopg://cem_app:x@127.0.0.1:1/cem"
CAIDO_REDIS = "redis://127.0.0.1:1/0"


def sondear(database_url: str, redis_url: str):
    app = crear_app(Config(database_url, "0.1.0", "s", "s", redis_url=redis_url))
    return TestClient(app).get(RUTA)


def test_todo_responde(base_de_prueba, redis_limpio):
    r = sondear(url_rol("app"), REDIS_URL)
    assert r.status_code == 200
    assert r.json() == {"estado": "ok", "db": "ok", "redis": "ok"}
    assert '"db":"ok"' in r.text  # la palabra clave que busca Kuma


@pytest.mark.parametrize(
    ("db", "redis", "esperado"),
    [
        (CAIDO_PG, REDIS_URL, {"estado": "falla", "db": "falla", "redis": "ok"}),
        ("{app}", CAIDO_REDIS, {"estado": "falla", "db": "ok", "redis": "falla"}),
        ("", REDIS_URL, {"estado": "falla", "db": "falla", "redis": "ok"}),
    ],
)
def test_una_dependencia_caida_da_503_sin_detalles(base_de_prueba, db, redis, esperado):
    r = sondear(db.format(app=url_rol("app")), redis)
    assert r.status_code == 503
    assert r.json() == esperado  # el motivo va a los registros, no a la respuesta


# Caché (revisión de seguridad H3, P3) -------------------------------------------------------

LLAMADAS = 5


def app_con_contador(segundos: int = SALUD_CACHE_SEGUNDOS):
    """Una app real y la lista de `select 1` que su motor manda a PostgreSQL (un oyente del motor,
    no un sustituto: la consulta llega a la base)."""
    app = crear_app(
        Config(url_rol("app"), "0.1.0", "s", "s", redis_url=REDIS_URL,
               salud_cache_segundos=segundos)
    )  # fmt: skip
    consultas: list[str] = []

    def contar(conn, cursor, sql, parametros, contexto, varias) -> None:
        if sql == "select 1":
            consultas.append(sql)

    event.listen(app.state.motor, "before_cursor_execute", contar)
    return app, consultas


def pings() -> int:
    """`PING` que recibió el Redis real desde que arrancó (lo cuenta el propio servidor). Suma los
    de cualquier cliente: el healthcheck de `backend/pruebas/compose.yml` manda uno por segundo."""
    cliente = Redis.from_url(REDIS_URL)
    try:
        return cliente.info("commandstats").get("cmdstat_ping", {}).get("calls", 0)
    finally:
        cliente.close()


def pings_de(app) -> list[str]:
    """Los `PING` que manda el cliente de Redis de la app. Cada uno sigue de largo al Redis real
    (un oyente, como el de `app_con_contador`): solo se anotan, para no sumar los de otros
    clientes del mismo servidor."""
    cliente = app.state.redis
    original = cliente.execute_command
    anotados: list[str] = []

    def anotar(*args, **opciones):
        if args and str(args[0]).upper() == "PING":
            anotados.append("PING")
        return original(*args, **opciones)

    cliente.execute_command = anotar
    return anotados


def test_el_healthcheck_de_las_pruebas_tambien_le_manda_ping_al_servidor(redis_limpio):
    """Por qué la prueba de abajo no cuenta en el servidor: con la app quieta, el contador igual
    sube (fallaba de a ratos con 2 en vez de 1 cuando las cinco llamadas pasaban del segundo)."""
    antes = pings()
    time.sleep(2.5)
    assert pings() - antes >= 1


def test_llamadas_seguidas_hacen_una_sola_comprobacion(base_de_prueba, redis_limpio):
    app, consultas = app_con_contador()
    de_la_app = pings_de(app)
    pings_antes = pings()
    c = TestClient(app)
    respuestas = [c.get(RUTA) for _ in range(LLAMADAS)]
    assert [r.status_code for r in respuestas] == [200] * LLAMADAS
    assert all(r.text == respuestas[0].text for r in respuestas)
    assert '"db":"ok"' in respuestas[0].text  # el contrato que mira Kuma no cambia
    assert len(consultas) == 1
    assert de_la_app == ["PING"]
    assert pings() - pings_antes >= 1  # control: el PING llegó al Redis real


def test_vencido_el_intervalo_vuelve_a_comprobar(base_de_prueba, redis_limpio):
    app, consultas = app_con_contador(segundos=1)
    de_la_app = pings_de(app)
    c = TestClient(app)
    assert c.get(RUTA).status_code == c.get(RUTA).status_code == 200
    assert len(consultas) == 1
    time.sleep(1.2)
    assert c.get(RUTA).status_code == 200
    assert len(consultas) == 2
    assert de_la_app == ["PING", "PING"]  # control de `pings_de`: cuenta cada comprobación


def test_llamadas_a_la_vez_hacen_una_sola_comprobacion(base_de_prueba, redis_limpio):
    app, consultas = app_con_contador()
    with ThreadPoolExecutor(max_workers=LLAMADAS) as hilos:
        codigos = list(hilos.map(lambda _: TestClient(app).get(RUTA).status_code, range(LLAMADAS)))
    assert codigos == [200] * LLAMADAS
    assert len(consultas) == 1
