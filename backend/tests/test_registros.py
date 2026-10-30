"""Registros JSON y redacción (documento 13, §16.1)."""

import io
import json
import logging
import socket
import sys
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app import registros
from app.registros import FormateadorJson, redactar
from tests.test_api import cliente
from tests.test_limite_de_tasa import arrancar

UUID = "0199e3a2-1234-7abc-8def-0123456789ab"


@pytest.fixture
def salida_json():
    """Lo que escribiría el proceso: las líneas del manejador JSON, ya interpretadas."""
    flujo = io.StringIO()
    manejador = logging.StreamHandler(flujo)
    manejador.setFormatter(FormateadorJson())
    raiz = logging.getLogger()
    nivel = raiz.level
    raiz.addHandler(manejador)
    raiz.setLevel(logging.INFO)
    yield lambda: [json.loads(linea) for linea in flujo.getvalue().splitlines()]
    raiz.removeHandler(manejador)
    raiz.setLevel(nivel)


# Redacción -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("texto", "secreto", "marca"),
    [
        ("aviso a ana.perez+cem@ejemplo.com.ar", "ana.perez", "[correo]"),
        (
            "redis://default:S3cr3t0-Redis@cem-redis:6379/0",
            "S3cr3t0",
            "redis://default:[redactado]@cem-redis:6379/0",
        ),
        (
            "postgresql+psycopg://cem_app:otra%40clave@cem-postgres:5432/cem",
            "otra%40clave",
            "cem_app:[redactado]@",
        ),
        ("redis://:sinusuario@cem-redis.interno:6379", "sinusuario", ":[redactado]@cem-redis."),
        ("Authorization: Bearer abc.def-123_xyz", "abc.def-123_xyz", "[redactado]"),
        (
            "jwt eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ4In0.c2lnbmF0dXJh rechazado",
            "eyJzdWIiOiJ4In0",
            "jwt [redactado] rechazado",
        ),
        ("token=tk_vivo_123&otro=1", "tk_vivo_123", "token=[redactado]&otro=1"),
        ('{"access_token": "abc999"}', "abc999", '"access_token": "[redactado]"'),
        ("clave: hunter2", "hunter2", "clave: [redactado]"),
        ("CEM_REDIS_CLAVE=Zq9-xx", "Zq9-xx", "CEM_REDIS_CLAVE=[redactado]"),
        ("password=p4ss", "p4ss", "password=[redactado]"),
        ("al +54 9 11 4567-8901 por WhatsApp", "4567-8901", "al [telefono] por"),
        ("llamar al 11 4567-8901", "4567-8901", "[telefono]"),
        ("celular 15-4567-8901", "4567-8901", "[telefono]"),
        ("tel (011) 4567 8901.", "4567 8901", "[telefono]."),
        ("expensa de $ 12.345,67 impaga", "12.345,67", "de [importe] impaga"),
        ("saldo ARS -1500.50", "1500.50", "[importe]"),
        ("cuota U$S 20", "U$S 20", "[importe]"),
        ("importe Decimal('98765.43') en la unidad", "98765.43", "[importe] en la unidad"),
        ("cuenta 2850590940090418135201 rechazada", "2850590940090418135201",
         "cuenta [cuenta] rechazada"),
    ],
)  # fmt: skip
def test_log_redaction(texto, secreto, marca):
    """La prueba que pide §16.1 (análoga a la de AnalytikCapital): cada dato sale tapado."""
    redactado = redactar(texto)
    assert secreto not in redactado
    assert marca in redactado


@pytest.mark.parametrize(
    "texto",
    [
        f"trabajo {UUID} hecho",
        "/api/v1/consorcios/12345678-1234-1234-1234-123456789012/unidades",
        "momento 2026-10-14T12:00:00.123+00:00 y fecha 14-10-2026",
        "desde 198.51.100.7 y 10.0.2.0/24",
        "trabajo fallido (pendiente): prueba.correo intento 3 de 5",
        "redis://cem-redis:6379/0 sin credenciales",
        "postgresql+psycopg://cem_app@cem-postgres:5432/cem",
        "clave_unica prueba.correo:abc y la clave única no cambia",
        "1000 trabajos en 1234.5 ms, latencia 60 s",
        "unidad 1° A del Edificio Alvear 100",
        "GET /health 200",
        "marca 1730400000123456789 ns y pedido 12345678901234567890123",
        "tipo de trabajo desconocido: plataforma.purgar_cola",
    ],
)
def test_la_redaccion_no_toca_lo_que_no_es_dato_personal(texto):
    """Control: sin esto, un patrón que tapara todo pasaría la prueba anterior."""
    assert redactar(texto) == texto


# Formato -------------------------------------------------------------------------------


def test_una_linea_json_con_los_campos_de_16_1(monkeypatch):
    monkeypatch.setenv("CEM_PROCESO", "trabajos")
    formateador = FormateadorJson()
    log = logging.getLogger("cem.prueba")
    try:
        raise ValueError("falló el envío a beto@ejemplo.com")
    except ValueError:
        registro = log.makeRecord(
            "cem.prueba", logging.ERROR, __file__, 1, "aviso a %s", ("ana@ejemplo.com",),
            sys.exc_info(),
            extra={"campos": {"duracion_ms": 12.5, "destino": "+54 9 11 4567-8901"}},
        )  # fmt: skip
    with registros.contexto(id_correlacion="abc", consorcio_id=UUID, persona_id=None):
        linea = formateador.format(registro)
    assert "\n" not in linea
    objeto = json.loads(linea)
    assert objeto["nivel"] == "ERROR"
    assert objeto["proceso"] == "trabajos"
    assert objeto["modulo"] == "cem.prueba"
    assert objeto["mensaje"] == "aviso a [correo]"
    assert objeto["momento"].endswith("+00:00")
    assert (objeto["id_correlacion"], objeto["consorcio_id"], objeto["persona_id"]) == (
        "abc", UUID, None,
    )  # fmt: skip
    assert (objeto["duracion_ms"], objeto["destino"]) == (12.5, "[telefono]")
    assert "ValueError" in objeto["excepcion"] and "beto@" not in objeto["excepcion"]
    # Fuera del bloque, el contexto ya no está.
    assert "id_correlacion" not in json.loads(formateador.format(registro))


def test_un_campo_no_puede_pisar_uno_base():
    registro = logging.getLogger("x").makeRecord(
        "x", logging.INFO, __file__, 1, "m", (), None, extra={"campos": {"mensaje": "otro"}}
    )
    with pytest.raises(ValueError, match="reservados"):
        FormateadorJson().format(registro)


# API -----------------------------------------------------------------------------------


def test_una_linea_por_peticion_con_el_id_del_error(salida_json):
    r = cliente().get("/api/v1/no-existe?correo=ana@ejemplo.com")
    assert r.status_code == 404
    [linea] = [x for x in salida_json() if x["modulo"] == "cem.peticiones"]
    assert linea["id_correlacion"] == r.json()["id_correlacion"]
    assert (linea["metodo"], linea["ruta"], linea["estado"]) == ("GET", "/api/v1/no-existe", 404)
    assert linea["tamano_bytes"] == len(r.content)
    assert linea["duracion_ms"] >= 0
    assert "ana@" not in json.dumps(salida_json())


def test_un_500_lleva_el_mismo_id_en_el_cuerpo_y_en_los_registros(salida_json):
    """El manejador del 500 corre por fuera del middleware: el id tiene que seguir ahí."""
    c = cliente()
    c.app.add_api_route("/falla", lambda: 1 / 0)
    r = TestClient(c.app, raise_server_exceptions=False).get("/falla")
    assert r.status_code == 500
    lineas = salida_json()
    [peticion] = [x for x in lineas if x["modulo"] == "cem.peticiones"]
    [error] = [x for x in lineas if x["mensaje"] == "error no controlado"]
    assert peticion["estado"] == 500
    assert r.json()["id_correlacion"] == peticion["id_correlacion"] == error["id_correlacion"]
    assert "ZeroDivisionError" in error["excepcion"]


def test_cada_peticion_tiene_su_propio_id(salida_json):
    c = cliente()
    ids = {c.get("/api/v1/no-existe").json()["id_correlacion"] for _ in range(3)}
    assert len(ids) == 3


@pytest.mark.redis
def test_uvicorn_escribe_solo_json_y_sin_su_registro_de_acceso(redis_limpio):
    """Como en la imagen: `sh arranque.sh` con la configuración de `app/registros.json`."""
    with socket.socket() as s:  # arranque.sh usa el 8000: si está ocupado, la prueba no vale
        assert s.connect_ex(("127.0.0.1", 8000)) != 0, "el puerto 8000 ya está en uso"
    proceso = arrancar("127.0.0.1/32")
    try:
        for _ in range(100):
            try:
                if httpx.get("http://127.0.0.1:8000/health").status_code == 200:
                    break
            except httpx.TransportError:
                time.sleep(0.1)
        else:
            pytest.fail("uvicorn no arrancó")
        r = httpx.get("http://127.0.0.1:8000/api/v1/no-existe?token=tk_vivo_123")
        assert r.status_code == 404
    finally:
        proceso.terminate()
        salida, _ = proceso.communicate(timeout=10)
    lineas = [json.loads(linea) for linea in salida.splitlines() if linea.strip()]
    assert len(lineas) == len([x for x in salida.splitlines() if x.strip()])
    assert {x["proceso"] for x in lineas} == {"api"}
    modulos = {x["modulo"] for x in lineas}
    assert "uvicorn.error" in modulos  # los mensajes de arranque de uvicorn también son JSON
    assert "uvicorn.access" not in modulos
    peticiones = [(x["ruta"], x["estado"]) for x in lineas if x["modulo"] == "cem.peticiones"]
    assert ("/health", 200) in peticiones
    assert ("/api/v1/no-existe", 404) in peticiones
    [error] = [x for x in lineas if x.get("ruta") == "/api/v1/no-existe"]
    assert error["id_correlacion"] == r.json()["id_correlacion"]
    assert "tk_vivo_123" not in salida
