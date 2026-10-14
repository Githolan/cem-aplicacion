"""Rutas de soporte, detrás de Cloudflare Access (documento 13, §10.2, `TB-CEM-5`)."""

from typing import Literal

from pydantic import BaseModel


class SesionSoporte(BaseModel):
    """`GET /api/v1/soporte/sesion`: el operador según el JWT de Access verificado."""

    identidad: str
    tipo: Literal["persona", "servicio"]
