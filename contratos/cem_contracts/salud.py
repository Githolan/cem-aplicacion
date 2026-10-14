"""Respuestas de las sondas de salud (documento 13, §16.2)."""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class Salud(BaseModel):
    """`GET /health`: el proceso vive. No consulta ninguna dependencia."""

    estado: Literal["ok"]
    version: str
    sha_imagen: str
    sha_contratos: str
    version_contrato: str


class EstadoCola(BaseModel):
    """Respuesta de `GET /api/v1/health/cola` (documento 13, §16.2 y §16.3).

    Solo totales: ningún dato de un consorcio. Kuma alerta cuando `estado` es `alerta`.
    """

    estado: Literal["ok", "alerta"] = Field(
        description="`alerta` si hay trabajos muertos o el pendiente más viejo espera más de "
        "15 minutos."
    )
    pendientes: int
    en_curso: int
    muertos: int
    segundos_mas_viejo: float = Field(description="Espera del pendiente listo más viejo.")


class EstadoDependencia(StrEnum):
    OK = "ok"
    FALLA = "falla"


class SaludDependencias(BaseModel):
    """`GET /api/v1/health/deps`: 200 si todas responden; 503 si alguna falla.

    Kuma busca la palabra clave `"db":"ok"`. El motivo de una falla va a los registros,
    nunca a la respuesta. El almacén de objetos y el antivirus se suman cuando existan.
    """

    estado: EstadoDependencia
    db: EstadoDependencia
    redis: EstadoDependencia
