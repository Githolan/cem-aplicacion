"""Cliente mínimo de `clamd` por TCP (H2c §3, paso 5, repositorio CEM).

`cem-antivirus` (o `cem-antivirus-staging`) corre ClamAV en la red interna; `cem-trabajos` le
manda cada archivo por `INSTREAM` y le pregunta la fecha de sus firmas con `VERSION`. Se usa el
formato `z…\\0` del protocolo de `clamd` (comandos y respuestas terminados en un byte nulo).

Variables: `CEM_ANTIVIRUS_SERVIDOR` (el alias de red) y `CEM_ANTIVIRUS_PUERTO`.
"""

import os
import socket
import struct
from dataclasses import dataclass
from datetime import UTC, datetime

TRAMO = 64 * 1024
TIEMPO_DE_ESPERA = 120
"""Segundos por operación: un archivo de 20 MB se analiza en pocos segundos; un `clamd` colgado
no debe retener al trabajador hasta el final de su reserva (5 minutos)."""


class AntivirusNoDisponible(RuntimeError):
    """`clamd` no respondió o respondió algo que no se entiende: la versión sigue en cuarentena y
    el trabajo se reintenta (§3, paso 6)."""


@dataclass(frozen=True)
class Firmas:
    antivirus: str
    """Lo que informa `VERSION`, p. ej. `ClamAV 1.4.3/27800`."""
    fecha: datetime


def _destino() -> tuple[str, int]:
    servidor, puerto = (
        os.environ.get("CEM_ANTIVIRUS_SERVIDOR"),
        os.environ.get("CEM_ANTIVIRUS_PUERTO"),
    )
    if not servidor or not puerto:
        raise AntivirusNoDisponible(
            "faltan variables de entorno: CEM_ANTIVIRUS_SERVIDOR, CEM_ANTIVIRUS_PUERTO"
        )
    return servidor, int(puerto)


def _conversar(enviar) -> str:
    try:
        with socket.create_connection(_destino(), timeout=TIEMPO_DE_ESPERA) as s:
            enviar(s)
            respuesta = b""
            while not respuesta.endswith(b"\0"):
                parte = s.recv(4096)
                if not parte:
                    break
                respuesta += parte
    except OSError as e:
        raise AntivirusNoDisponible(f"clamd no responde: {e}") from e
    if not respuesta.endswith(b"\0"):
        raise AntivirusNoDisponible("clamd cortó la respuesta")
    return respuesta[:-1].decode("utf-8", "replace")


def firmas() -> Firmas:
    """`ClamAV 1.4.3/27800/Sun Oct 26 08:21:03 2026`: la fecha es la de la base de firmas."""
    texto = _conversar(lambda s: s.sendall(b"zVERSION\0"))
    partes = texto.split("/")
    if len(partes) != 3:
        raise AntivirusNoDisponible(f"VERSION sin fecha de firmas: {texto!r}")
    try:
        fecha = datetime.strptime(partes[2].strip(), "%a %b %d %H:%M:%S %Y").replace(tzinfo=UTC)
    except ValueError as e:
        raise AntivirusNoDisponible(f"fecha de firmas ilegible: {texto!r}") from e
    return Firmas("/".join(partes[:2]), fecha)


def analizar(contenido: bytes) -> str | None:
    """None si está limpio; el nombre de lo detectado si no. Cualquier otra respuesta (un error
    de `clamd`, por ejemplo por pasar su `StreamMaxLength`) es `AntivirusNoDisponible`."""

    def enviar(s: socket.socket) -> None:
        s.sendall(b"zINSTREAM\0")
        for i in range(0, len(contenido), TRAMO):
            tramo = contenido[i : i + TRAMO]
            s.sendall(struct.pack("!I", len(tramo)) + tramo)
        s.sendall(struct.pack("!I", 0))

    texto = _conversar(enviar)
    if texto == "stream: OK":
        return None
    if texto.startswith("stream: ") and texto.endswith(" FOUND"):
        return texto[len("stream: ") : -len(" FOUND")]
    raise AntivirusNoDisponible(f"respuesta de clamd inesperada: {texto!r}")
