"""Capa única de acceso a un modelo de lenguaje.

El resto de la aplicación llama solo a `complete()`: ningún otro módulo importa el SDK de un
proveedor. Se llama solo desde el backend; la clave nunca llega al frontend. Cambiar de proveedor
es cambiar las cuatro variables `CEM_LLM_*`, sin tocar código.

Una llamada es un pedido: la capa no reintenta. Lo que el proveedor no resuelve sale como
`ErrorDeModelo`, y quien llama decide qué hacer con eso.
"""

import base64
import os
from dataclasses import dataclass
from typing import Literal, NotRequired, TypedDict

VARIABLES = ("CEM_LLM_PROVIDER", "CEM_LLM_API_KEY", "CEM_LLM_BASE_URL", "CEM_LLM_MODEL")
PROVEEDORES_ANTHROPIC = frozenset({"anthropic", "zai"})
"""Hablan el protocolo de Anthropic. Cualquier otro se trata como compatible con OpenAI."""


TipoAdjunto = Literal["application/pdf", "image/jpeg", "image/png"]


class ErrorDeModelo(RuntimeError):
    """El proveedor no dio una respuesta usable. Lleva el tipo de la falla, nunca el pedido."""


@dataclass(frozen=True)
class Adjunto:
    """Un PDF o una imagen que acompaña al texto del mensaje."""

    tipo: TipoAdjunto
    datos: bytes


class Mensaje(TypedDict):
    role: Literal["system", "user", "assistant"]
    content: str
    adjuntos: NotRequired[list[Adjunto]]


@dataclass(frozen=True)
class Opciones:
    max_tokens: int
    espera_s: float
    """Tiempo máximo de la llamada. Lo fija quien llama: depende de lo que esté esperando."""


@dataclass(frozen=True)
class Respuesta:
    texto: str
    modelo: str
    """El modelo que informó el proveedor, no el que se pidió."""


def exigir_variables() -> dict[str, str]:
    """Devuelve las cuatro variables. Si falta alguna, el error la nombra sin mostrar valores."""
    faltan = [nombre for nombre in VARIABLES if not os.environ.get(nombre)]
    if faltan:
        raise RuntimeError(f"Faltan variables del modelo de lenguaje: {', '.join(faltan)}")
    return {nombre: os.environ[nombre] for nombre in VARIABLES}


def complete(messages: list[Mensaje], opts: Opciones) -> Respuesta:
    variables = exigir_variables()
    if variables["CEM_LLM_PROVIDER"] in PROVEEDORES_ANTHROPIC:
        respuesta = _con_anthropic(messages, opts, variables)
    else:
        respuesta = _con_openai(messages, opts, variables)
    if not respuesta.texto:
        raise ErrorDeModelo(f"El modelo {respuesta.modelo} respondió sin texto")
    return respuesta


def _base64(adjunto: Adjunto) -> str:
    return base64.b64encode(adjunto.datos).decode("ascii")


def _mensaje_anthropic(mensaje: Mensaje) -> dict:
    if not mensaje.get("adjuntos"):
        return {"role": mensaje["role"], "content": mensaje["content"]}
    bloques = [
        {
            "type": "document" if a.tipo == "application/pdf" else "image",
            "source": {"type": "base64", "media_type": a.tipo, "data": _base64(a)},
        }
        for a in mensaje["adjuntos"]
    ]
    texto = {"type": "text", "text": mensaje["content"]}
    return {"role": mensaje["role"], "content": [*bloques, texto]}


def _mensaje_openai(mensaje: Mensaje) -> dict:
    if not mensaje.get("adjuntos"):
        return {"role": mensaje["role"], "content": mensaje["content"]}
    bloques = []
    for a in mensaje["adjuntos"]:
        url = f"data:{a.tipo};base64,{_base64(a)}"
        if a.tipo == "application/pdf":
            bloques.append({"type": "file", "file": {"filename": "adjunto.pdf", "file_data": url}})
        else:
            bloques.append({"type": "image_url", "image_url": {"url": url}})
    texto = {"type": "text", "text": mensaje["content"]}
    return {"role": mensaje["role"], "content": [texto, *bloques]}


def _con_anthropic(messages: list[Mensaje], opts: Opciones, variables: dict[str, str]) -> Respuesta:
    from anthropic import Anthropic, AnthropicError

    cliente = Anthropic(
        api_key=variables["CEM_LLM_API_KEY"],
        base_url=variables["CEM_LLM_BASE_URL"],
        timeout=opts.espera_s,
        max_retries=0,
    )
    # El protocolo de Anthropic lleva las instrucciones de sistema aparte de la conversación.
    sistema = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    extra = {"system": sistema} if sistema else {}
    try:
        respuesta = cliente.messages.create(
            model=variables["CEM_LLM_MODEL"],
            max_tokens=opts.max_tokens,
            messages=[_mensaje_anthropic(m) for m in messages if m["role"] != "system"],
            **extra,
        )
    except AnthropicError as e:
        raise ErrorDeModelo(type(e).__name__) from e
    texto = "".join(bloque.text for bloque in respuesta.content if bloque.type == "text")
    return Respuesta(texto=texto, modelo=respuesta.model)


def _con_openai(messages: list[Mensaje], opts: Opciones, variables: dict[str, str]) -> Respuesta:
    from openai import OpenAI, OpenAIError

    cliente = OpenAI(
        api_key=variables["CEM_LLM_API_KEY"],
        base_url=variables["CEM_LLM_BASE_URL"],
        timeout=opts.espera_s,
        max_retries=0,
    )
    try:
        respuesta = cliente.chat.completions.create(
            model=variables["CEM_LLM_MODEL"],
            max_tokens=opts.max_tokens,
            messages=[_mensaje_openai(m) for m in messages],
        )
    except OpenAIError as e:
        raise ErrorDeModelo(type(e).__name__) from e
    return Respuesta(texto=respuesta.choices[0].message.content or "", modelo=respuesta.model)
