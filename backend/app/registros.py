"""Registros en JSON a la salida estándar, con redacción (documento 13, §16.1).

Un objeto por línea con `momento`, `nivel`, `proceso` (`CEM_PROCESO`: api, trabajos o
planificador), `modulo` (el nombre del registrador, p. ej. `cem.trabajos`) y `mensaje`, más
los campos del contexto cuando existen: `id_correlacion`, `consorcio_id`, `persona_id`
(identificador opaco, nunca el correo ni el nombre) y `duracion_ms`. Dozzle los muestra en vivo.

Antes de escribir, el mensaje, la excepción y todo campo de texto pasan por `redactar()`:
correos, tokens, claves (también las de una URL como `redis://usuario:clave@`), teléfonos e
importes. La redacción es la última barrera; el código no debe registrar esos datos a propósito.

Los tres procesos usan la misma configuración, `registros.json`: `trabajos` y `planificador`
con `configurar()` y la API con `uvicorn --log-config app/registros.json --no-access-log`. El
registro de acceso de uvicorn queda apagado y lo reemplaza `RegistroDePeticiones`, una línea
por petición con método, ruta, estado, tamaño y duración (§16.1).
"""

import json
import logging
import logging.config
import os
import re
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

CONFIGURACION = Path(__file__).with_name("registros.json")
"""dictConfig de `logging` compartido por los tres procesos (uvicorn lo lee con `--log-config`)."""

REDACTADO = "[redactado]"

# El orden importa: la clave de una URL antes que el correo (en `usuario:clave@host.com` el
# correo se llevaría el host) y los JWT y `Bearer` antes que el par clave=valor.
_PATRONES: tuple[tuple[re.Pattern[str], str], ...] = (
    # Clave dentro de una URL: redis://default:clave@cem-redis:6379/0 → redis://default:[…]@…
    (re.compile(r"(\b[a-zA-Z][\w+.-]*://[^\s:/@]*:)[^\s@/]+@"), rf"\1{REDACTADO}@"),
    # JWT (tres partes en base64url; la cabecera empieza siempre con `eyJ`).
    (re.compile(r"\beyJ[\w-]*\.[\w-]*\.[\w-]*"), REDACTADO),
    (re.compile(r"(?i)\b(bearer|basic)\s+[\w~+/.=-]+"), rf"\1 {REDACTADO}"),
    # Pares nombre=valor o nombre: valor con nombre de secreto (token, clave, password,
    # api_key, authorization, cookie...), también como parte de otro nombre: access_token=…
    (
        re.compile(
            r"(?i)((?<![\w-])[\w-]*(?:token|clave|password|passwd|contrase[ñn]a|secret|secreto"
            r"|api[_-]?key|authorization|cookie)[\"']?\s*[:=]\s*[\"']?)[^\s\"',;&]+"
        ),
        rf"\1{REDACTADO}",
    ),
    (re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), "[correo]"),
    # Importes: con moneda ($ 1.234,56 · ARS 100 · U$S 20) o como Decimal('1234.56').
    (re.compile(r"(?:\bARS|\bUSD|U\$S|\$)\s?-?\d+(?:[.,]\d+)*"), "[importe]"),
    (re.compile(r"Decimal\('-?[\d.]+'\)"), "[importe]"),
    # Teléfonos: internacional con `+` (+54 9 11 4567-8901) o local con separadores
    # (11 4567-8901, 15-4567-8901, 4567-8901). Sin separadores no se tocan: un número suelto
    # puede ser un contador o una marca de tiempo. Nunca dentro de un UUID ni de una fecha.
    (re.compile(r"(?<![\w+-])\+\d(?:[\s-]?\d){7,14}(?![\w-])"), "[telefono]"),
    (
        re.compile(r"(?<![\w-])(?:\(?\d{2,4}\)?[\s-])?(?:15[\s-])?\d{3,4}[\s-]\d{4}(?![\w-])"),
        "[telefono]",
    ),
)


def redactar(texto: str) -> str:
    for patron, reemplazo in _PATRONES:
        texto = patron.sub(reemplazo, texto)
    return texto


# Contexto ------------------------------------------------------------------------------

CAMPOS_BASE = ("momento", "nivel", "proceso", "modulo", "mensaje", "excepcion")

_contexto: ContextVar[dict[str, Any] | None] = ContextVar("cem_registro", default=None)


def _campos() -> dict[str, Any]:
    return _contexto.get() or {}


@contextmanager
def contexto(**campos: Any) -> Iterator[None]:
    """Agrega campos a todo lo que se registre dentro del bloque (y de lo que llame)."""
    token = _contexto.set({**_campos(), **campos})
    try:
        yield
    finally:
        _contexto.reset(token)


def id_correlacion_actual() -> str:
    """El de la petición en curso, que también va en el cuerpo de un error (§11.1)."""
    id_ = _campos().get("id_correlacion")
    if id_ is None:
        raise RuntimeError("fuera de una petición: no hay id de correlación")
    return id_


class FormateadorJson(logging.Formatter):
    """Una línea JSON por registro. Campos propios: `extra={"campos": {...}}`."""

    def __init__(self) -> None:
        super().__init__()
        self.proceso = os.environ.get("CEM_PROCESO", "api")

    def format(self, record: logging.LogRecord) -> str:
        objeto: dict[str, Any] = {
            "momento": datetime.fromtimestamp(record.created, UTC).isoformat(
                timespec="milliseconds"
            ),
            "nivel": record.levelname,
            "proceso": self.proceso,
            "modulo": record.name,
            "mensaje": redactar(record.getMessage()),
        }
        if record.exc_info:
            objeto["excepcion"] = redactar(self.formatException(record.exc_info))
        campos = {**_campos(), **getattr(record, "campos", {})}
        repetidos = set(campos) & set(CAMPOS_BASE)
        if repetidos:
            raise ValueError(f"campos reservados del registro: {sorted(repetidos)}")
        for nombre, valor in campos.items():
            objeto[nombre] = redactar(valor) if isinstance(valor, str) else valor
        return json.dumps(objeto, ensure_ascii=False, default=lambda v: redactar(str(v)))


def configurar() -> None:
    """Registros JSON para `trabajos` y `planificador`; la API los recibe de uvicorn."""
    logging.config.dictConfig(json.loads(CONFIGURACION.read_text(encoding="utf-8")))


# Petición --------------------------------------------------------------------------------

log_peticiones = logging.getLogger("cem.peticiones")


class RegistroDePeticiones:
    """Middleware ASGI: fija el id de correlación y registra una línea por petición.

    Va por fuera de los demás middlewares para registrar también los 429 y 503 del límite de
    tasa. El id no se quita del contexto al salir: el manejador del 500 de Starlette corre por
    fuera de este middleware y lo necesita para el cuerpo del error; cada petición es su propia
    tarea de asyncio con su propia copia del contexto, así que no pasa a la siguiente.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        _contexto.set({**_campos(), "id_correlacion": uuid.uuid4().hex})
        inicio = time.perf_counter()
        estado, tamano = 500, 0

        async def enviar(mensaje: Message) -> None:
            nonlocal estado, tamano
            if mensaje["type"] == "http.response.start":
                estado = mensaje["status"]
            elif mensaje["type"] == "http.response.body":
                tamano += len(mensaje.get("body", b""))
            await send(mensaje)

        try:
            await self.app(scope, receive, enviar)
        finally:
            # Solo la ruta: la consulta (`?…`) puede traer datos personales.
            log_peticiones.info(
                "%s %s %s",
                scope["method"],
                scope["path"],
                estado,
                extra={
                    "campos": {
                        "metodo": scope["method"],
                        "ruta": scope["path"],
                        "estado": estado,
                        "tamano_bytes": tamano,
                        "duracion_ms": round((time.perf_counter() - inicio) * 1000, 1),
                    }
                },
            )
