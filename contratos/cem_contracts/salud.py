"""Respuesta de `GET /health` (documento 13, §16.2)."""

from typing import Literal

from pydantic import BaseModel, Field


class Salud(BaseModel):
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
