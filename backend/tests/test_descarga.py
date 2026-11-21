"""La descarga acotada de un enlace de la liquidación (H5-5, `app/liquidacion/descarga.py`; SSRF).

El archivo lo sirve la prueba desde la misma máquina, por HTTPS con un certificado propio para un
nombre inventado (`liquidaciones.prueba`). Como esa dirección es local, cada prueba que tiene que
llegar al servidor pasa un `publica` que admite 127.0.0.1; las que prueban el control usan el de la
aplicación. Datos SINTETICOS / NO REAL.
"""

import ipaddress
import ssl
import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from cem_contracts.documentos import TOPE_BYTES, TipoArchivo
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from app.consorcios.documentos import tipo_real
from app.liquidacion import descarga

NOMBRE = "liquidaciones.prueba"
INTERNO = "interno.prueba"
PDF = b"%PDF-1.4\n% liquidacion SINTETICA\n"
LOCAL = "127.0.0.1"
SIN_LARGO = "X-Prueba-Sin-Largo"
"""Encabezado de la prueba (no se manda): el servidor responde sin `Content-Length`."""
GOTEO = "X-Prueba-Goteo"
"""Encabezado de la prueba (no se manda): segundos entre byte y byte del cuerpo."""


def _certificado(carpeta: Path) -> tuple[Path, Path]:
    clave = ec.generate_private_key(ec.SECP256R1())
    nombre = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, NOMBRE)])
    ahora = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder().subject_name(nombre).issuer_name(nombre)
        .public_key(clave.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(ahora - timedelta(minutes=1)).not_valid_after(ahora + timedelta(hours=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(NOMBRE),
                                                    x509.DNSName(INTERNO)]), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(clave, hashes.SHA256())
    )  # fmt: skip
    archivo_cert, archivo_clave = carpeta / "cert.pem", carpeta / "clave.pem"
    archivo_cert.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    archivo_clave.write_bytes(clave.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ))  # fmt: skip
    return archivo_cert, archivo_clave


class Servidor(ThreadingHTTPServer):
    respuestas: dict[str, tuple[int, dict[str, str], bytes]]
    pedidos: list[tuple[str, str]]


class Atencion(BaseHTTPRequestHandler):
    server: Servidor

    def do_GET(self) -> None:
        self.server.pedidos.append((self.path, self.headers.get("Host", "")))
        estado, encabezados, cuerpo = self.server.respuestas.get(self.path, (404, {}, b""))
        self.send_response(estado)
        sin_largo = encabezados.get(SIN_LARGO) is not None
        for k, v in encabezados.items():
            if k not in (SIN_LARGO, GOTEO):
                self.send_header(k, v)
        # Sin largo, HTTP/1.0 termina el cuerpo cerrando la conexión.
        if "Content-Length" not in encabezados and not sin_largo:
            self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        if GOTEO not in encabezados:
            self.wfile.write(cuerpo)
            return
        pausa = float(encabezados[GOTEO])
        try:
            for k in range(len(cuerpo)):
                self.wfile.write(cuerpo[k : k + 1])
                self.wfile.flush()
                time.sleep(pausa)
        except (BrokenPipeError, ConnectionResetError, ssl.SSLError):
            return  # el cliente cortó: es lo que prueba el plazo

    def log_message(self, *_: object) -> None:
        pass


@pytest.fixture(scope="module")
def tls(tmp_path_factory) -> tuple[ssl.SSLContext, Path, Path]:
    cert, clave = _certificado(tmp_path_factory.mktemp("tls"))
    cliente = ssl.create_default_context(cafile=str(cert))
    return cliente, cert, clave


@pytest.fixture
def servidor(tls) -> Iterator[Servidor]:
    _, cert, clave = tls
    s = Servidor((LOCAL, 0), Atencion)
    contexto = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    contexto.load_cert_chain(str(cert), str(clave))
    s.socket = contexto.wrap_socket(s.socket, server_side=True)
    s.respuestas, s.pedidos = {}, []
    hilo = threading.Thread(target=s.serve_forever, daemon=True)
    hilo.start()
    yield s
    s.shutdown()
    s.server_close()


def resolver(nombre: str) -> list[str]:
    return {NOMBRE: [LOCAL], INTERNO: ["10.0.0.5"]}[nombre]


def solo_local(direccion) -> bool:
    return str(direccion) == LOCAL


def traer(servidor: Servidor, tls, camino: str, **cambios) -> descarga.Traido:
    puerto = servidor.server_address[1]
    opciones = {"resolver": resolver, "publica": solo_local, "contexto_tls": tls[0],
                "puerto": puerto, "tipo_real": tipo_real} | cambios  # fmt: skip
    return descarga.traer(f"https://{NOMBRE}:{puerto}{camino}", **opciones)


def rechazo(funcion, *args, **kwargs) -> str:
    with pytest.raises(descarga.DescargaRechazada) as e:
        funcion(*args, **kwargs)
    return e.value.motivo


def test_trae_un_pdf_con_el_nombre_en_el_certificado_y_en_el_host(servidor, tls):
    servidor.respuestas["/liq.pdf"] = (200, {"Content-Type": "application/pdf"}, PDF)
    t = traer(servidor, tls, "/liq.pdf")
    assert (t.contenido, t.tipo) == (PDF, TipoArchivo.PDF)
    assert servidor.pedidos == [("/liq.pdf", f"{NOMBRE}")]


def test_con_el_control_de_la_aplicacion_una_direccion_local_no_se_pide(servidor, tls):
    """Control: el mismo pedido, con `es_publica`, se corta antes de conectar."""
    servidor.respuestas["/liq.pdf"] = (200, {}, PDF)
    assert rechazo(traer, servidor, tls, "/liq.pdf", publica=descarga.es_publica) == (
        "destino_no_admitido")  # fmt: skip
    assert servidor.pedidos == []


@pytest.mark.parametrize("direccion", [
    "127.0.0.1", "10.0.0.5", "172.17.0.1", "192.168.1.10", "169.254.169.254", "100.64.0.1",
    "0.0.0.0", "224.0.0.1", "::1", "fe80::1", "fc00::1", "::ffff:10.0.0.1", "::ffff:127.0.0.1",
])  # fmt: skip
def test_las_direcciones_que_no_son_publicas_no_se_admiten(direccion):
    assert not descarga.es_publica(ipaddress.ip_address(direccion))


@pytest.mark.parametrize("direccion", ["8.8.8.8", "200.45.191.35", "2001:4860:4860::8888"])
def test_las_publicas_si(direccion):
    assert descarga.es_publica(ipaddress.ip_address(direccion))


@pytest.mark.parametrize(("url", "motivo"), [
    ("http://liquidaciones.prueba/liq.pdf", "esquema_no_admitido"),
    ("file:///etc/passwd", "esquema_no_admitido"),
    ("https://10.0.0.5/liq.pdf", "destino_no_admitido"),
    ("https://[::1]/liq.pdf", "destino_no_admitido"),
    ("https://liquidaciones.prueba:8443/liq.pdf", "puerto_no_admitido"),
    ("https://usuario:clave@liquidaciones.prueba/liq.pdf", "enlace_invalido"),
    ("https:///sin-nombre", "enlace_invalido"),
    ("https://liquidaciones.prueba/" + "a" * descarga.LARGO_MAXIMO_URL, "enlace_invalido"),
])  # fmt: skip
def test_un_enlace_fuera_de_lo_admitido_no_se_pide(url, motivo):
    assert rechazo(descarga.traer, url, resolver=resolver, tipo_real=tipo_real) == motivo


def test_un_nombre_con_una_direccion_interna_entre_las_publicas_no_se_admite():
    mezclado = {"mixto.prueba": ["8.8.8.8", "10.0.0.5"]}
    assert rechazo(descarga.traer, "https://mixto.prueba/x.pdf", resolver=mezclado.__getitem__,
                   tipo_real=tipo_real) == "destino_no_admitido"  # fmt: skip


def test_una_redireccion_a_una_direccion_interna_se_corta(servidor, tls):
    puerto = servidor.server_address[1]
    servidor.respuestas["/r"] = (302, {"Location": f"https://{INTERNO}:{puerto}/x.pdf"}, b"")
    assert rechazo(traer, servidor, tls, "/r") == "destino_no_admitido"
    assert [p for p, _ in servidor.pedidos] == ["/r"]


def test_una_redireccion_al_mismo_sitio_se_sigue(servidor, tls):
    servidor.respuestas["/r"] = (302, {"Location": "/liq.pdf"}, b"")
    servidor.respuestas["/liq.pdf"] = (200, {}, PDF)
    assert traer(servidor, tls, "/r").contenido == PDF


def test_demasiadas_redirecciones_se_cortan(servidor, tls):
    servidor.respuestas["/r"] = (302, {"Location": "/r"}, b"")
    assert rechazo(traer, servidor, tls, "/r") == "demasiadas_redirecciones"
    assert len(servidor.pedidos) == descarga.REDIRECCIONES + 1


def test_una_pagina_web_no_es_un_archivo_admitido(servidor, tls):
    servidor.respuestas["/x"] = (
        200,
        {"Content-Type": "application/pdf"},
        b"<html>comprobantes</html>",
    )
    assert rechazo(traer, servidor, tls, "/x") == "tipo_no_admitido"


def test_un_largo_declarado_sobre_el_tope_se_corta_sin_leer(servidor, tls):
    servidor.respuestas["/x"] = (200, {"Content-Length": str(TOPE_BYTES + 1)}, b"")
    assert rechazo(traer, servidor, tls, "/x") == "demasiado_grande"


def test_un_cuerpo_sobre_el_tope_se_corta_aunque_no_lo_declare(servidor, tls):
    servidor.respuestas["/x"] = (200, {SIN_LARGO: "1"}, PDF + b"0" * TOPE_BYTES)
    assert rechazo(traer, servidor, tls, "/x") == "demasiado_grande"
    # Control: el mismo cuerpo, sin largo y dentro del tope, se trae.
    servidor.respuestas["/chico"] = (200, {SIN_LARGO: "1"}, PDF)
    assert traer(servidor, tls, "/chico").contenido == PDF


def test_un_error_del_servidor_no_trae_nada(servidor, tls):
    assert rechazo(traer, servidor, tls, "/no-existe") == "respuesta_no_valida"


def test_un_certificado_de_otro_nombre_no_se_acepta(servidor, tls):
    """El certificado de la prueba no es de `otro.prueba`: la conexión no se acepta."""
    puerto = servidor.server_address[1]
    servidor.respuestas["/liq.pdf"] = (200, {}, PDF)
    assert rechazo(descarga.traer, f"https://otro.prueba:{puerto}/liq.pdf",
                   resolver=lambda _: [LOCAL], publica=solo_local, contexto_tls=tls[0],
                   puerto=puerto, tipo_real=tipo_real) == "sin_respuesta"  # fmt: skip


def test_un_servidor_que_gotea_no_pasa_del_plazo_de_reloj(servidor, tls):
    """Un byte cada 0,2 s nunca vence el plazo de lectura (20 s), pero sí el de reloj (Codex)."""
    servidor.respuestas["/gotea"] = (200, {GOTEO: "0.2"}, PDF + b"0" * 200)
    inicio = time.monotonic()
    assert rechazo(traer, servidor, tls, "/gotea", plazo_total=1.0) == "plazo_vencido"
    assert time.monotonic() - inicio < 3.0
    # Control: el mismo goteo, con un cuerpo que entra en el plazo, se trae entero.
    servidor.respuestas["/corto"] = (200, {GOTEO: "0.001"}, PDF)
    assert traer(servidor, tls, "/corto", plazo_total=5.0).contenido == PDF
