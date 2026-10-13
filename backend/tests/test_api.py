from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.config import Config
from app.contexto import Identidad, obtener_identidad
from app.main import crear_app
from tests.conftest import url_rol


def cliente(database_url: str = "", persona=None) -> TestClient:
    app = crear_app(Config(database_url, "0.1.0", "sha-imagen", "sha-contratos"))
    if persona is not None:
        app.dependency_overrides[obtener_identidad] = lambda: Identidad(persona)
    return TestClient(app)


def test_health():
    r = cliente().get("/health")
    assert r.status_code == 200
    assert r.json()["estado"] == "ok" and r.json()["sha_contratos"] == "sha-contratos"


def test_sin_sesion_responde_401_con_cuerpo_uniforme():
    r = cliente().get(f"/api/v1/consorcios/{uuid4()}/unidades")
    assert r.status_code == 401
    cuerpo = r.json()
    assert cuerpo["codigo"] == "no_autenticado"
    assert set(cuerpo) == {"codigo", "mensaje", "detalle", "id_correlacion"}


def test_ruta_inexistente_usa_el_cuerpo_uniforme():
    r = cliente().get("/api/v1/no-existe")
    assert r.status_code == 404 and r.json()["codigo"] == "no_encontrado"


@pytest.mark.db
def test_mis_consorcios(datos):
    r = cliente(url_rol("app"), datos.persona_ab).get("/api/v1/me/consorcios")
    assert r.status_code == 200
    assert [(c["nombre"], c["vinculos"]) for c in r.json()] == [
        ("Edificio Alvear 100", ["propietario"]),
        ("Edificio Belgrano 200", ["inquilino"]),
    ]


@pytest.mark.db
def test_vinculo_vencido_no_da_acceso(datos):
    c = cliente(url_rol("app"), datos.persona_vencida)
    assert c.get("/api/v1/me/consorcios").json() == []
    assert c.get(f"/api/v1/consorcios/{datos.consorcio_a}/unidades").status_code == 404


@pytest.mark.db
def test_unidades_del_propio_consorcio(datos):
    r = cliente(url_rol("app"), datos.persona_a).get(
        f"/api/v1/consorcios/{datos.consorcio_a}/unidades"
    )
    assert r.status_code == 200
    assert [u["designacion"] for u in r.json()["elementos"]] == ["1° A", "1° B"]


@pytest.mark.db
def test_otro_consorcio_y_uno_inexistente_responden_igual(datos):
    c = cliente(url_rol("app"), datos.persona_a)
    ajeno = c.get(f"/api/v1/consorcios/{datos.consorcio_b}/unidades")
    inexistente = c.get(f"/api/v1/consorcios/{uuid4()}/unidades")
    assert ajeno.status_code == inexistente.status_code == 404
    assert ajeno.json()["codigo"] == inexistente.json()["codigo"] == "no_encontrado"
    assert ajeno.json()["mensaje"] == inexistente.json()["mensaje"]
