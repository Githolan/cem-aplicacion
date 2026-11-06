"""Capa única de acceso a un modelo de lenguaje.

El resto de la aplicación llama solo a `complete()`: ningún otro módulo importa el SDK de un
proveedor. Se llama solo desde el backend; la clave nunca llega al frontend. Cambiar de proveedor
es cambiar las cuatro variables `CEM_LLM_*`, sin tocar código.
"""

import os
from dataclasses import dataclass
from typing import Literal, TypedDict

VARIABLES = ("CEM_LLM_PROVIDER", "CEM_LLM_API_KEY", "CEM_LLM_BASE_URL", "CEM_LLM_MODEL")
PROVEEDORES_ANTHROPIC = frozenset({"anthropic", "zai"})
"""Hablan el protocolo de Anthropic. Cualquier otro se trata como compatible con OpenAI."""


class Mensaje(TypedDict):
    role: Literal["system", "user", "assistant"]
    content: str


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
        raise RuntimeError(f"El modelo {respuesta.modelo} respondió sin texto")
    return respuesta


def _con_anthropic(messages: list[Mensaje], opts: Opciones, variables: dict[str, str]) -> Respuesta:
    from anthropic import Anthropic

    cliente = Anthropic(
        api_key=variables["CEM_LLM_API_KEY"],
        base_url=variables["CEM_LLM_BASE_URL"],
        timeout=opts.espera_s,
    )
    # El protocolo de Anthropic lleva las instrucciones de sistema aparte de la conversación.
    sistema = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    extra = {"system": sistema} if sistema else {}
    respuesta = cliente.messages.create(
        model=variables["CEM_LLM_MODEL"],
        max_tokens=opts.max_tokens,
        messages=[m for m in messages if m["role"] != "system"],
        **extra,
    )
    texto = "".join(bloque.text for bloque in respuesta.content if bloque.type == "text")
    return Respuesta(texto=texto, modelo=respuesta.model)


def _con_openai(messages: list[Mensaje], opts: Opciones, variables: dict[str, str]) -> Respuesta:
    from openai import OpenAI

    cliente = OpenAI(
        api_key=variables["CEM_LLM_API_KEY"],
        base_url=variables["CEM_LLM_BASE_URL"],
        timeout=opts.espera_s,
    )
    respuesta = cliente.chat.completions.create(
        model=variables["CEM_LLM_MODEL"],
        max_tokens=opts.max_tokens,
        messages=messages,
    )
    return Respuesta(texto=respuesta.choices[0].message.content or "", modelo=respuesta.model)
