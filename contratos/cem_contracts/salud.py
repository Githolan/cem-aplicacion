"""Respuesta de `GET /health` (documento 13, §16.2)."""

from typing import Literal

from pydantic import BaseModel


class Salud(BaseModel):
    estado: Literal["ok"]
    version: str
    sha_imagen: str
    sha_contratos: str
    version_contrato: str
