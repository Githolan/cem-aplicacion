"""`GET /api/v1/health/deps` contra PostgreSQL y Redis reales (documento 13, §16.2)."""

import pytest
from fastapi.testclient import TestClient

from app.config import Config
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
