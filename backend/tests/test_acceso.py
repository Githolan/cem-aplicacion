"""JWT de Cloudflare Access en las rutas de soporte (documento 13, §10.2, `TB-CEM-5`).

Las claves públicas las sirve un servidor HTTP real con el mismo formato que
`<equipo>.cloudflareaccess.com/cdn-cgi/access/certs`; los tokens se firman acá con RS256.
"""

import base64
import hashlib
import hmac
import json
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from app.acceso import ENCABEZADO
from app.config import Config
from app.main import crear_app

RUTA = "/api/v1/soporte/sesion"
AUD = "aud-de-la-aplicacion-cem-soporte"
KID = "clave-del-equipo"
CLAVE = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTRA_CLAVE = rsa.generate_private_key(public_exponent=65537, key_size=2048)


@dataclass
class Emisor:
    url: str
    pedidos: list[str]


@pytest.fixture(scope="module")
def emisor() -> Iterator[Emisor]:
    publica = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(CLAVE.public_key()))
    cuerpo = json.dumps({"keys": [{**publica, "kid": KID, "alg": "RS256", "use": "sig"}]})
    pedidos: list[str] = []

    class Claves(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            pedidos.append(self.path)
            self.send_response(200 if self.path == "/cdn-cgi/access/certs" else 404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(cuerpo.encode())

        def log_message(self, *_) -> None:
            pass

    servidor = HTTPServer(("127.0.0.1", 0), Claves)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    yield Emisor(f"http://127.0.0.1:{servidor.server_port}", pedidos)
    servidor.shutdown()
    servidor.server_close()


def cliente(equipo: str = "", aud: str = "") -> TestClient:
    config = Config("", "0.1.0", "s", "s", access_equipo=equipo, access_aud=aud)
    return TestClient(crear_app(config))


def reclamos(emisor: Emisor, **cambios) -> dict:
    ahora = int(time.time())
    base = {"aud": [AUD], "email": "operador@ejemplo.com", "iss": emisor.url, "iat": ahora,
            "exp": ahora + 600, "sub": "id-de-access", "type": "app"}  # fmt: skip
    return {**base, **cambios}


def firmar(datos: dict, clave=CLAVE, kid: str = KID) -> str:
    return jwt.encode(datos, clave, algorithm="RS256", headers={"kid": kid})


def armar(cabecera: dict, datos: dict, firma: bytes) -> str:
    def b64(b: bytes) -> str:
        return base64.urlsafe_b64encode(b).rstrip(b"=").decode()

    return ".".join([b64(json.dumps(cabecera).encode()), b64(json.dumps(datos).encode()),
                     b64(firma)])  # fmt: skip


def pedir(c: TestClient, token: str | None):
    return c.get(RUTA, headers={ENCABEZADO: token} if token is not None else {})


def test_un_operador_de_access_entra_y_las_claves_se_bajan_una_vez(emisor):
    c = cliente(emisor.url, AUD)
    antes = len(emisor.pedidos)
    for _ in range(3):
        r = pedir(c, firmar(reclamos(emisor)))
        assert r.status_code == 200
        assert r.json() == {"identidad": "operador@ejemplo.com", "tipo": "persona"}
    assert emisor.pedidos[antes:] == ["/cdn-cgi/access/certs"]


def test_un_token_de_servicio_entra_con_su_nombre(emisor):
    datos = reclamos(emisor, common_name="kuma.access", sub="")
    del datos["email"]
    r = pedir(cliente(emisor.url, AUD), firmar(datos))
    assert r.status_code == 200 and r.json() == {"identidad": "kuma.access", "tipo": "servicio"}


def rechazados(emisor: Emisor) -> dict[str, str | None]:
    ahora = int(time.time())
    publica_pem = CLAVE.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    datos = reclamos(emisor)
    sin_identidad = {k: v for k, v in datos.items() if k != "email"}
    return {
        "sin encabezado": None,
        "vencido": firmar(reclamos(emisor, iat=ahora - 900, exp=ahora - 300)),
        "otra audiencia": firmar(reclamos(emisor, aud=["otra-aplicacion"])),
        "otro emisor": firmar(reclamos(emisor, iss="https://otro.cloudflareaccess.com")),
        "firmado con otra clave": firmar(datos, clave=OTRA_CLAVE),
        "clave desconocida": firmar(datos, kid="otra"),
        "sin vencimiento": firmar({k: v for k, v in datos.items() if k != "exp"}),
        "sin identidad": firmar(sin_identidad),
        "alg none": armar({"alg": "none", "typ": "JWT", "kid": KID}, datos, b""),
        # Confusión de algoritmos: HMAC con la clave pública como secreto.
        "HS256 con la clave pública": armar(
            {"alg": "HS256", "typ": "JWT", "kid": KID},
            datos,
            hmac.new(
                publica_pem,
                armar({"alg": "HS256", "typ": "JWT", "kid": KID}, datos, b"")
                .rsplit(".", 1)[0]
                .encode(),
                hashlib.sha256,
            ).digest(),
        ),
        "basura": "esto.no.es-un-jwt",
    }


@pytest.mark.parametrize(
    "caso",
    [
        "sin encabezado", "vencido", "otra audiencia", "otro emisor", "firmado con otra clave",
        "clave desconocida", "sin vencimiento", "sin identidad", "alg none",
        "HS256 con la clave pública", "basura",
    ],
)  # fmt: skip
def test_token_no_valido_da_401_sin_detalles(emisor, caso):
    r = pedir(cliente(emisor.url, AUD), rechazados(emisor)[caso])
    assert r.status_code == 401
    assert r.json()["codigo"] == "no_autenticado"
    assert r.json()["mensaje"] == "Iniciá sesión para continuar."


def test_sin_configurar_rechaza_incluso_un_token_valido(emisor):
    assert pedir(cliente(), firmar(reclamos(emisor))).status_code == 401
    # Control: el mismo token, con Access configurado, entra.
    assert pedir(cliente(emisor.url, AUD), firmar(reclamos(emisor))).status_code == 200


def test_configuracion_a_medias_no_arranca(emisor):
    with pytest.raises(RuntimeError, match="CEM_ACCESS"):
        cliente(emisor.url, "")


def test_sin_las_claves_del_equipo_responde_503():
    r = pedir(cliente("http://127.0.0.1:1", AUD), firmar({"iss": "x"}))
    assert r.status_code == 503 and r.json()["codigo"] == "servicio_no_disponible"


def test_toda_ruta_de_soporte_exige_access():
    c = cliente()
    rutas = [r for r in c.app.openapi()["paths"] if r.startswith("/api/v1/soporte/")]
    assert rutas == [RUTA]
    for ruta in rutas:
        assert c.get(ruta).status_code == 401
