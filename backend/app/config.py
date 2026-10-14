"""Configuración por variables de entorno.

Ningún secreto vive en el repositorio (documento 13, §15.4).
"""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    database_url: str
    """Conexión con el rol `cem_app` (sin BYPASSRLS)."""
    version: str
    sha_imagen: str
    sha_contratos: str
    access_equipo: str = ""
    """Equipo de Access que firma los JWT de soporte: `https://<equipo>.cloudflareaccess.com`."""
    access_aud: str = ""
    """Audiencia (AUD) de la aplicación de Access que cubre las rutas de soporte."""


def cargar() -> Config:
    return Config(
        database_url=os.environ.get("CEM_DATABASE_URL", ""),
        version=os.environ.get("CEM_VERSION", "0.1.0"),
        sha_imagen=os.environ.get("CEM_SHA_IMAGEN", "desconocida"),
        sha_contratos=os.environ.get("CEM_SHA_CONTRATOS", "desconocida"),
        access_equipo=os.environ.get("CEM_ACCESS_EQUIPO", ""),
        access_aud=os.environ.get("CEM_ACCESS_AUD", ""),
    )
