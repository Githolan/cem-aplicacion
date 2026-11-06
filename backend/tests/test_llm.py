"""Capa `llm`: variables obligatorias, elección del SDK y aislamiento de los proveedores.

Las llamadas van por HTTP real a un servidor local que responde como cada proveedor: se prueba lo
que sale por la red, incluida la dirección y la clave.
"""

import ast
import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from app import llm
from app.main import crear_app

RAIZ = Path(__file__).resolve().parent.parent
CLAVE = "clave-de-prueba-que-no-debe-aparecer"
OPCIONES = llm.Opciones(max_tokens=8, espera_s=5.0)
RESPUESTAS = {
    "/v1/messages": {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "modelo-informado",
        "content": [{"type": "text", "text": "hola"}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 1, "output_tokens": 1},
    },
    "/chat/completions": {
        "id": "c1",
        "object": "chat.completion",
        "created": 1,
        "model": "modelo-informado",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": "hola"},
            }
        ],
    },
}


class Proveedor(ThreadingHTTPServer):
    pedidos: list[tuple[str, dict[str, str], dict]]
    sin_texto = False


class Atencion(BaseHTTPRequestHandler):
    server: Proveedor

    def do_POST(self) -> None:
        cuerpo = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        encabezados = {k.lower(): v for k, v in self.headers.items()}
        self.server.pedidos.append((self.path, encabezados, cuerpo))
        respuesta = dict(RESPUESTAS[self.path])
        if self.server.sin_texto:
            respuesta["content"] = []
        datos = json.dumps(respuesta).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(datos)))
        self.end_headers()
        self.wfile.write(datos)

    def log_message(self, *_: object) -> None:
        pass


@pytest.fixture
def proveedor() -> Iterator[Proveedor]:
    servidor = Proveedor(("127.0.0.1", 0), Atencion)
    servidor.pedidos = []
    hilo = threading.Thread(target=servidor.serve_forever, daemon=True)
    hilo.start()
    yield servidor
    servidor.shutdown()
    servidor.server_close()


def configurar(monkeypatch: pytest.MonkeyPatch, nombre: str, servidor: Proveedor) -> None:
    monkeypatch.setenv("CEM_LLM_PROVIDER", nombre)
    monkeypatch.setenv("CEM_LLM_API_KEY", CLAVE)
    monkeypatch.setenv("CEM_LLM_BASE_URL", f"http://127.0.0.1:{servidor.server_address[1]}")
    monkeypatch.setenv("CEM_LLM_MODEL", "modelo-pedido")


@pytest.mark.parametrize("nombre", ["anthropic", "zai"])
def test_anthropic_y_zai_usan_su_protocolo_y_separan_el_sistema(
    monkeypatch: pytest.MonkeyPatch, proveedor: Proveedor, nombre: str
) -> None:
    configurar(monkeypatch, nombre, proveedor)
    respuesta = llm.complete(
        [{"role": "system", "content": "regla"}, {"role": "user", "content": "hola"}], OPCIONES
    )
    assert respuesta == llm.Respuesta(texto="hola", modelo="modelo-informado")
    ((ruta, encabezados, cuerpo),) = proveedor.pedidos
    assert ruta == "/v1/messages"
    assert encabezados["authorization"] == f"Bearer {CLAVE}"
    assert cuerpo["model"] == "modelo-pedido"
    assert cuerpo["max_tokens"] == 8
    assert cuerpo["system"] == "regla"
    assert cuerpo["messages"] == [{"role": "user", "content": "hola"}]


@pytest.mark.parametrize("nombre", ["openrouter", "otro-compatible"])
def test_los_demas_proveedores_usan_el_protocolo_de_openai(
    monkeypatch: pytest.MonkeyPatch, proveedor: Proveedor, nombre: str
) -> None:
    configurar(monkeypatch, nombre, proveedor)
    mensajes: list[llm.Mensaje] = [
        {"role": "system", "content": "regla"},
        {"role": "user", "content": "hola"},
    ]
    assert llm.complete(mensajes, OPCIONES).texto == "hola"
    ((ruta, encabezados, cuerpo),) = proveedor.pedidos
    assert ruta == "/chat/completions"
    assert encabezados["authorization"] == f"Bearer {CLAVE}"
    assert cuerpo["messages"] == mensajes


def test_las_variables_de_claude_code_no_desvian_la_llamada(
    monkeypatch: pytest.MonkeyPatch, proveedor: Proveedor
) -> None:
    configurar(monkeypatch, "anthropic", proveedor)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "otra-clave")
    assert llm.complete([{"role": "user", "content": "hola"}], OPCIONES).texto == "hola"
    assert proveedor.pedidos[0][1]["authorization"] == f"Bearer {CLAVE}"


@pytest.mark.parametrize(
    ("tipo", "bloque"),
    [("application/pdf", "document"), ("image/png", "image"), ("image/jpeg", "image")],
)
def test_anthropic_recibe_el_adjunto_en_base64(
    monkeypatch: pytest.MonkeyPatch, proveedor: Proveedor, tipo: str, bloque: str
) -> None:
    configurar(monkeypatch, "anthropic", proveedor)
    adjunto = llm.Adjunto(tipo=tipo, datos=b"contenido")
    llm.complete([{"role": "user", "content": "leer", "adjuntos": [adjunto]}], OPCIONES)
    primero, texto = proveedor.pedidos[0][2]["messages"][0]["content"]
    assert primero == {
        "type": bloque,
        "source": {"type": "base64", "media_type": tipo, "data": "Y29udGVuaWRv"},
    }
    assert texto == {"type": "text", "text": "leer"}


def test_openai_recibe_imagen_y_pdf_en_su_formato(
    monkeypatch: pytest.MonkeyPatch, proveedor: Proveedor
) -> None:
    configurar(monkeypatch, "openrouter", proveedor)
    adjuntos = [
        llm.Adjunto(tipo="image/png", datos=b"contenido"),
        llm.Adjunto(tipo="application/pdf", datos=b"contenido"),
    ]
    llm.complete([{"role": "user", "content": "leer", "adjuntos": adjuntos}], OPCIONES)
    texto, imagen, pdf = proveedor.pedidos[0][2]["messages"][0]["content"]
    assert texto == {"type": "text", "text": "leer"}
    assert imagen["image_url"]["url"] == "data:image/png;base64,Y29udGVuaWRv"
    assert pdf["file"]["file_data"] == "data:application/pdf;base64,Y29udGVuaWRv"


def test_una_respuesta_sin_texto_es_un_error(
    monkeypatch: pytest.MonkeyPatch, proveedor: Proveedor
) -> None:
    configurar(monkeypatch, "anthropic", proveedor)
    proveedor.sin_texto = True
    with pytest.raises(RuntimeError, match="sin texto"):
        llm.complete([{"role": "user", "content": "hola"}], OPCIONES)


@pytest.mark.parametrize("faltante", llm.VARIABLES)
@pytest.mark.parametrize("valor", [None, ""])
def test_sin_una_variable_la_app_no_arranca_y_el_error_no_muestra_valores(
    monkeypatch: pytest.MonkeyPatch, faltante: str, valor: str | None
) -> None:
    for nombre in llm.VARIABLES:
        monkeypatch.setenv(nombre, CLAVE)
    if valor is None:
        monkeypatch.delenv(faltante)
    else:
        monkeypatch.setenv(faltante, valor)
    with pytest.raises(RuntimeError) as error:
        crear_app()
    assert faltante in str(error.value)
    assert CLAVE not in str(error.value)


def test_ningun_otro_modulo_importa_el_sdk_de_un_proveedor() -> None:
    def importados(archivo: Path) -> set[str]:
        nombres: set[str] = set()
        for nodo in ast.walk(ast.parse(archivo.read_text(encoding="utf8"))):
            if isinstance(nodo, ast.Import):
                nombres |= {alias.name.split(".")[0] for alias in nodo.names}
            elif isinstance(nodo, ast.ImportFrom) and nodo.module:
                nombres.add(nodo.module.split(".")[0])
        return nombres

    sdk = {"anthropic", "openai"}
    assert sdk <= importados(RAIZ / "app" / "llm.py"), "el control debe ver los imports de llm.py"
    archivos = [*(RAIZ / "app").rglob("*.py"), *(RAIZ / "scripts").rglob("*.py")]
    assert [a for a in archivos if a.name != "llm.py" and importados(a) & sdk] == []
