"""Límite de tasa con Redis real (documento 13, §10.6; cierre de TB-8)."""

import os
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Config
from app.limite import LIMITE_API, LimiteDeTasa
from app.main import crear_app
from tests.conftest import REDIS_URL

RAIZ = Path(__file__).resolve().parent.parent
RUTA = "/api/v1/me/consorcios"
"""Responde 401 sin sesión: el límite corre antes que la autenticación, así que también cuenta."""
CUPO = LIMITE_API.amount


def app_con(redis_url: str):
    return crear_app(Config("", "0.1.0", "s", "s", redis_url=redis_url))


def test_el_limite_es_un_middleware_de_toda_la_app():
    # La ausencia del middleware es exactamente el defecto que se midió en AnalytikCapital.
    app = app_con("memory://")
    assert [m.cls for m in app.user_middleware] == [LimiteDeTasa]
    assert str(LIMITE_API) == "300 per 1 minute"


def test_sin_redis_configurado_la_api_no_arranca():
    with pytest.raises(RuntimeError, match="CEM_REDIS_URL"):
        app_con("")


@pytest.mark.redis
def test_cupo_por_cliente_para_toda_la_api(redis_limpio):
    # Una app por cliente, como dos procesos de uvicorn: el contador compartido está en Redis.
    # Cada TestClient abierto con `with` usa un solo bucle de eventos, como uvicorn.
    with (
        TestClient(app_con(REDIS_URL), client=("203.0.113.10", 40000)) as a,
        TestClient(app_con(REDIS_URL), client=("203.0.113.10", 40001)) as a_otro_proceso,
        TestClient(app_con(REDIS_URL), client=("203.0.113.20", 40000)) as b,
    ):
        assert {a.get(RUTA).status_code for _ in range(CUPO - 1)} == {401}
        assert a_otro_proceso.get(RUTA).status_code == 401  # agota el cupo de la misma IP
        r = a.get(RUTA)
        assert r.status_code == 429
        assert r.json()["codigo"] == "demasiadas_peticiones"
        assert set(r.json()) == {"codigo", "mensaje", "detalle", "id_correlacion"}
        assert 1 <= int(r.headers["Retry-After"]) <= 60
        # El cupo es de toda la API, no de cada ruta.
        assert a.get(f"/api/v1/consorcios/{uuid4()}/unidades").status_code == 429
        # Otro cliente tiene su propio cupo; las sondas no se limitan.
        assert b.get(RUTA).status_code == 401
        assert a.get("/health").status_code == 200
    # Los contadores viven en Redis, con el prefijo del proyecto y la IP en la clave.
    claves = [k.decode() for k in redis_limpio.scan_iter("cem:rl:*")]
    assert any("203.0.113.10" in k for k in claves) and any("203.0.113.20" in k for k in claves)


@pytest.mark.redis
def test_sin_redis_la_api_responde_503_y_la_salud_sigue(redis_limpio):
    with TestClient(app_con("redis://127.0.0.1:1/0")) as c:
        r = c.get(RUTA)
        assert r.status_code == 503 and r.json()["codigo"] == "servicio_no_disponible"
        assert c.get("/health").status_code == 200
    # Control: el mismo pedido con Redis llega a la ruta (401 sin sesión).
    with TestClient(app_con(REDIS_URL)) as c:
        assert c.get(RUTA).status_code == 401


def arrancar(confiables: str | None) -> subprocess.Popen:
    """Corre `arranque.sh` como en la imagen, sin base de datos."""
    entorno = {k: v for k, v in os.environ.items() if not k.startswith(("CEM_", "FORWARDED_"))}
    entorno["PATH"] = f"{Path(sys.executable).parent}{os.pathsep}{entorno['PATH']}"
    entorno["CEM_REDIS_URL"] = REDIS_URL
    if confiables is not None:
        entorno["FORWARDED_ALLOW_IPS"] = confiables
    return subprocess.Popen(
        ["sh", "arranque.sh"], cwd=RAIZ, env=entorno, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True,
    )  # fmt: skip


def test_arranque_exige_forwarded_allow_ips():
    proceso = arrancar(None)
    salida, _ = proceso.communicate(timeout=10)
    assert proceso.returncode != 0 and "FORWARDED_ALLOW_IPS" in salida


@pytest.mark.redis
@pytest.mark.parametrize(
    ("confiables", "ip_contada", "ip_ignorada"),
    [
        ("127.0.0.1/32", "198.51.100.7", "127.0.0.1"),
        ("10.255.255.0/24", "127.0.0.1", "198.51.100.7"),  # control: el proxy no es confiable
    ],
)
def test_uvicorn_usa_x_forwarded_for_solo_desde_un_proxy_confiable(
    redis_limpio, confiables, ip_contada, ip_ignorada
):
    proceso = arrancar(confiables)
    try:
        for _ in range(100):
            try:
                if httpx.get("http://127.0.0.1:8000/health").status_code == 200:
                    break
            except httpx.TransportError:
                time.sleep(0.1)
        else:
            pytest.fail("uvicorn no arrancó")
        r = httpx.get(f"http://127.0.0.1:8000{RUTA}", headers={"X-Forwarded-For": "198.51.100.7"})
        assert r.status_code == 401
        claves = [k.decode() for k in redis_limpio.scan_iter("cem:rl:*")]
        assert any(ip_contada in k for k in claves), claves
        assert not any(ip_ignorada in k for k in claves), claves
    finally:
        proceso.terminate()
        proceso.wait(timeout=10)
