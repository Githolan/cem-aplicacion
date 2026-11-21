"""Traer un archivo de un enlace de la liquidación, con la descarga acotada (H5-5; diseño H5 §5).

La liquidación trae enlaces (y su QR) a los comprobantes del período (Disposición 1146/24 de la
Ciudad). Quien importa puede pedir que la plataforma traiga el archivo de uno: así no lo baja a su
teléfono para volver a subirlo. El servidor hace un pedido a una dirección que escribió un tercero,
así que la descarga está acotada en **destino, tamaño y tipo** (SSRF):

- **Destino.** Solo `https`, al puerto 443, a un nombre (no a una IP escrita en el enlace). El
  nombre se resuelve una vez y **todas** sus direcciones tienen que ser públicas
  (`ipaddress.is_global`): ni la red local, ni la de Docker, ni la de metadatos de la nube
  (169.254.169.254), ni direcciones reservadas. La conexión va a la dirección ya revisada, con el
  nombre en el SNI y en el certificado, así que una segunda resolución (DNS rebinding) no la cambia.
  Las redirecciones se siguen a mano, hasta `REDIRECCIONES`, y cada destino pasa por el
  mismo control.
- **Tamaño.** Se lee en tramos con un contador y se corta al pasar `TOPE_BYTES` (el de los
  documentos), aunque el servidor no diga el largo o diga uno falso. El pedido entero tiene
  un plazo.
- **Tipo.** Lo que llega tiene que ser PDF, JPEG o PNG por sus primeros bytes, como una carga
  (`documentos.tipo_real`); lo que diga el servidor no alcanza.

Lo traído no se usa directamente: entra como una versión nueva de un documento, en cuarentena, y
pasa por el antivirus como cualquier carga (`documentos.guardar_archivo`).
"""

from __future__ import annotations

import ipaddress
import socket
import ssl
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import urllib3
from cem_contracts.documentos import TOPE_BYTES, TipoArchivo

ESQUEMA = "https"
PUERTO = 443
REDIRECCIONES = 3
PLAZO_CONEXION_S = 5.0
PLAZO_LECTURA_S = 20.0
"""Por tramo: el pedido entero lo acota además `PLAZO_TOTAL_S`."""
PLAZO_TOTAL_S = 45.0
"""Por debajo del plazo de un pedido a la API detrás de nginx."""
TRAMO = 64 * 1024
LARGO_MAXIMO_URL = 2000
CODIGOS_REDIRECCION = frozenset({301, 302, 303, 307, 308})

Resolver = Callable[[str], list[str]]
EsPublica = Callable[[ipaddress.IPv4Address | ipaddress.IPv6Address], bool]


class DescargaRechazada(ValueError):
    """La descarga no se hizo. `motivo` es una clave estable; el mensaje no lleva lo descargado."""

    def __init__(self, motivo: str) -> None:
        super().__init__(motivo)
        self.motivo = motivo


@dataclass(frozen=True)
class Traido:
    contenido: bytes
    tipo: TipoArchivo
    url: str
    """La dirección final, después de las redirecciones."""


def resolver_con_dns(nombre: str) -> list[str]:
    try:
        return sorted(
            {
                str(info[4][0])
                for info in socket.getaddrinfo(nombre, PUERTO, type=socket.SOCK_STREAM)
            }
        )
    except socket.gaierror:
        raise DescargaRechazada("nombre_inexistente") from None


def es_publica(direccion: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    # Una IPv6 que lleva una IPv4 adentro (::ffff:10.0.0.1) se juzga por la de adentro.
    if isinstance(direccion, ipaddress.IPv6Address) and direccion.ipv4_mapped is not None:
        direccion = direccion.ipv4_mapped
    return direccion.is_global and not direccion.is_multicast


def _destino(url: str, resolver: Resolver, publica: EsPublica,
             puerto_admitido: int) -> tuple[str, str, str]:  # fmt: skip
    """(nombre, dirección revisada, camino con la consulta) o `DescargaRechazada`."""
    if len(url) > LARGO_MAXIMO_URL:
        raise DescargaRechazada("enlace_invalido")
    partes = urlsplit(url)
    if partes.scheme != ESQUEMA:
        raise DescargaRechazada("esquema_no_admitido")
    if partes.username is not None or partes.password is not None:
        raise DescargaRechazada("enlace_invalido")
    nombre = partes.hostname
    if not nombre:
        raise DescargaRechazada("enlace_invalido")
    try:
        ipaddress.ip_address(nombre)
    except ValueError:
        pass
    else:
        raise DescargaRechazada("destino_no_admitido")  # una IP escrita: no hay nombre que validar
    try:
        puerto = partes.port
    except ValueError:
        raise DescargaRechazada("enlace_invalido") from None
    if (puerto or PUERTO) != puerto_admitido:
        raise DescargaRechazada("puerto_no_admitido")
    direcciones = resolver(nombre)
    if not direcciones or not all(publica(ipaddress.ip_address(d)) for d in direcciones):
        raise DescargaRechazada("destino_no_admitido")
    camino = (partes.path or "/") + (f"?{partes.query}" if partes.query else "")
    return nombre, direcciones[0], camino


def traer(
    url: str,
    *,
    resolver: Resolver = resolver_con_dns,
    publica: EsPublica = es_publica,
    contexto_tls: ssl.SSLContext | None = None,
    puerto: int = PUERTO,
    tipo_real: Callable[[bytes], TipoArchivo | None],
) -> Traido:
    """El archivo del enlace, dentro de los topes, o `DescargaRechazada` con el motivo.

    `resolver`, `publica`, `contexto_tls` y `puerto` existen para las pruebas, que sirven el archivo
    desde la misma máquina, en otro puerto y con un certificado propio; en la aplicación van los de
    omisión."""
    contexto = contexto_tls or ssl.create_default_context()
    plazo = urllib3.Timeout(connect=PLAZO_CONEXION_S, read=PLAZO_LECTURA_S, total=PLAZO_TOTAL_S)
    actual = url
    for _ in range(REDIRECCIONES + 1):
        nombre, direccion, camino = _destino(actual, resolver, publica, puerto)
        conexion = urllib3.HTTPSConnectionPool(
            direccion, port=puerto,
            server_hostname=nombre, assert_hostname=nombre, ssl_context=contexto,
            timeout=plazo, retries=False, maxsize=1,
        )  # fmt: skip
        try:
            respuesta = conexion.urlopen(
                "GET", camino, headers={"Host": nombre, "Accept": "application/pdf, image/*"},
                redirect=False, preload_content=False, assert_same_host=False,
            )  # fmt: skip
        except urllib3.exceptions.HTTPError as e:
            raise DescargaRechazada("sin_respuesta") from e
        try:
            if respuesta.status in CODIGOS_REDIRECCION:
                siguiente = respuesta.headers.get("Location")
                if not siguiente:
                    raise DescargaRechazada("redireccion_invalida")
                actual = urljoin(actual, siguiente)
                continue
            if respuesta.status != 200:
                raise DescargaRechazada("respuesta_no_valida")
            declarado = respuesta.headers.get("Content-Length")
            if declarado is not None and declarado.isdigit() and int(declarado) > TOPE_BYTES:
                raise DescargaRechazada("demasiado_grande")
            cuerpo = bytearray()
            try:
                for tramo in respuesta.stream(TRAMO):
                    cuerpo += tramo
                    if len(cuerpo) > TOPE_BYTES:
                        raise DescargaRechazada("demasiado_grande")
            except urllib3.exceptions.HTTPError as e:
                raise DescargaRechazada("sin_respuesta") from e
            tipo = tipo_real(bytes(cuerpo))
            if tipo is None:
                raise DescargaRechazada("tipo_no_admitido")
            return Traido(bytes(cuerpo), tipo, actual)
        finally:
            respuesta.release_conn()
            conexion.close()
    raise DescargaRechazada("demasiadas_redirecciones")
