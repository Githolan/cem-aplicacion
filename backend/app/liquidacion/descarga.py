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
  un plazo de reloj (`PLAZO_TOTAL_S`) que cubre la resolución, las redirecciones y la lectura: un
  servidor que manda de a poco no retiene el pedido a la API más allá de ese plazo.
- **Tipo.** Lo que llega tiene que ser PDF, JPEG o PNG por sus primeros bytes, como una carga
  (`documentos.tipo_real`); lo que diga el servidor no alcanza.

Lo traído no se usa directamente: entra como una versión nueva de un documento, en cuarentena, y
pasa por el antivirus como cualquier carga (`documentos.guardar_archivo`).
"""

from __future__ import annotations

import errno
import http.client
import ipaddress
import socket
import ssl
import threading
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

from cem_contracts.documentos import TOPE_BYTES, TipoArchivo

ESQUEMA = "https"
PUERTO = 443
REDIRECCIONES = 3
PLAZO_CONEXION_S = 5.0
PLAZO_LECTURA_S = 20.0
"""Por lectura del socket. No acota el pedido: un servidor que manda un byte cada 19 segundos nunca
lo vence. Eso lo acota `PLAZO_TOTAL_S`."""
PLAZO_TOTAL_S = 45.0
"""De reloj, para toda la descarga; por debajo del plazo de un pedido a la API detrás de nginx."""
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
             puerto_admitido: int) -> tuple[str, list[str], str]:  # fmt: skip
    """(nombre, direcciones revisadas, camino con la consulta) o `DescargaRechazada`. Las IPv4 van
    primero: un servidor sin ruta IPv6 igual llega por la IPv4 del mismo nombre (Codex)."""
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
    return nombre, sorted(direcciones, key=lambda d: ipaddress.ip_address(d).version), camino


class _Corrida:
    """El socket de la respuesta en curso, para cortarlo desde afuera cuando vence el plazo."""

    def __init__(self) -> None:
        self._cerrojo = threading.Lock()
        self._socket: socket.socket | None = None
        self.cortada = False

    def usar(self, sock: socket.socket | None) -> None:
        with self._cerrojo:
            self._socket = sock
            cortada = self.cortada
        if cortada:
            raise DescargaRechazada("plazo_vencido")

    def usar_sin_controlar(self, sock: socket.socket | None) -> None:
        with self._cerrojo:
            self._socket = sock

    def cortar(self) -> None:
        with self._cerrojo:
            self.cortada = True
            sock = self._socket
        if sock is not None:
            # `shutdown` despierta una lectura bloqueada en otro hilo; `close` solo, no.
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError as e:
                if e.errno not in (errno.ENOTCONN, errno.EBADF):
                    raise
                # Ya cerrado: no hay lectura que despertar.


def traer(
    url: str,
    *,
    resolver: Resolver = resolver_con_dns,
    publica: EsPublica = es_publica,
    contexto_tls: ssl.SSLContext | None = None,
    puerto: int = PUERTO,
    plazo_total: float = PLAZO_TOTAL_S,
    tipo_real: Callable[[bytes], TipoArchivo | None],
) -> Traido:
    """El archivo del enlace, dentro de los topes, o `DescargaRechazada` con el motivo.

    La descarga corre en un hilo aparte y este la espera `plazo_total` segundos de reloj. Si vence,
    corta el socket en curso (el hilo termina solo con el error de lectura) y responde
    `plazo_vencido`. Una resolución de DNS en curso no se puede interrumpir: su hilo sigue hasta que
    el resolutor del sistema se rinde, pero el pedido a la API ya respondió.

    `resolver`, `publica`, `contexto_tls`, `puerto` y `plazo_total` existen para las pruebas, que
    sirven el archivo desde la misma máquina, en otro puerto, con un certificado propio y con plazos
    cortos; en la aplicación van los de omisión."""
    corrida = _Corrida()
    resultado: dict[str, Traido | BaseException] = {}

    def trabajo() -> None:
        try:
            resultado["traido"] = _traer(url, corrida, resolver, publica,
                                         contexto_tls or ssl.create_default_context(), puerto,
                                         tipo_real)  # fmt: skip
        except BaseException as e:  # se relanza en el hilo del pedido
            resultado["error"] = e

    hilo = threading.Thread(target=trabajo, name="descarga-enlace", daemon=True)
    hilo.start()
    hilo.join(plazo_total)
    if hilo.is_alive():
        corrida.cortar()
        raise DescargaRechazada("plazo_vencido")
    if "error" in resultado:
        raise resultado["error"]
    traido = resultado["traido"]
    assert isinstance(traido, Traido)
    return traido


def _motivo(corrida: _Corrida) -> str:
    return "plazo_vencido" if corrida.cortada else "sin_respuesta"


def _conectar(direcciones: list[str], nombre: str, puerto: int, contexto: ssl.SSLContext,
              corrida: _Corrida) -> ssl.SSLSocket:  # fmt: skip
    """La conexión TLS a una de las direcciones ya revisadas, en orden, con el nombre en el SNI y
    en el certificado. Sin volver a resolver: solo se prueban las revisadas. El socket queda en
    `corrida` antes del saludo TLS y antes de leer nada: el plazo de reloj lo corta aunque el
    servidor gotee el saludo o los encabezados (Codex)."""
    crudo: socket.socket | None = None
    falla: OSError | None = None
    for direccion in direcciones:
        corrida.usar(None)  # vencido entre intentos: no se prueba otra
        try:
            crudo = socket.create_connection((direccion, puerto), timeout=PLAZO_CONEXION_S)
            break
        except OSError as e:
            falla = e
    if crudo is None:
        raise DescargaRechazada(_motivo(corrida)) from falla
    try:
        corrida.usar(crudo)
        crudo.settimeout(PLAZO_LECTURA_S)
        cifrado = contexto.wrap_socket(crudo, server_hostname=nombre)
    except (OSError, DescargaRechazada) as e:
        crudo.close()
        if isinstance(e, DescargaRechazada):
            raise
        raise DescargaRechazada(_motivo(corrida)) from e
    try:
        corrida.usar(cifrado)  # `wrap_socket` desprendió el socket crudo: se corta este
    except DescargaRechazada:
        cifrado.close()
        raise
    return cifrado


def _traer(url: str, corrida: _Corrida, resolver: Resolver, publica: EsPublica,
           contexto: ssl.SSLContext, puerto: int,
           tipo_real: Callable[[bytes], TipoArchivo | None]) -> Traido:  # fmt: skip
    actual = url
    for _ in range(REDIRECCIONES + 1):
        nombre, direcciones, camino = _destino(actual, resolver, publica, puerto)
        corrida.usar(None)  # vencido mientras se resolvía: no se conecta
        cifrado = _conectar(direcciones, nombre, puerto, contexto, corrida)
        conexion = http.client.HTTPConnection(nombre, puerto, timeout=PLAZO_LECTURA_S)
        conexion.sock = cifrado
        try:
            try:
                conexion.request(
                    "GET", camino, headers={"Host": nombre, "Accept": "application/pdf, image/*"}
                )
                respuesta = conexion.getresponse()
            except (OSError, http.client.HTTPException) as e:
                raise DescargaRechazada(_motivo(corrida)) from e
            if respuesta.status in CODIGOS_REDIRECCION:
                siguiente = respuesta.getheader("Location")
                if not siguiente:
                    raise DescargaRechazada("redireccion_invalida")
                actual = urljoin(actual, siguiente)
                continue
            if respuesta.status != 200:
                raise DescargaRechazada("respuesta_no_valida")
            declarado = respuesta.getheader("Content-Length")
            if declarado is not None and declarado.isdigit() and int(declarado) > TOPE_BYTES:
                raise DescargaRechazada("demasiado_grande")
            cuerpo = bytearray()
            try:
                while tramo := respuesta.read(TRAMO):
                    cuerpo += tramo
                    if len(cuerpo) > TOPE_BYTES:
                        raise DescargaRechazada("demasiado_grande")
            except (OSError, http.client.HTTPException) as e:
                raise DescargaRechazada(_motivo(corrida)) from e
            tipo = tipo_real(bytes(cuerpo))
            if tipo is None:
                raise DescargaRechazada("tipo_no_admitido")
            return Traido(bytes(cuerpo), tipo, actual)
        finally:
            corrida.usar_sin_controlar(None)
            conexion.close()
    raise DescargaRechazada("demasiadas_redirecciones")
