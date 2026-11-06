"""Configuración por variables de entorno.

Ningún secreto vive en el repositorio (documento 13, §15.4).
"""

import os
from dataclasses import dataclass

from cem_contracts.documentos import TOPE_BYTES

PAGOS_DIAS_BANDEJA = 7
"""Decisión por defecto del rediseño del circuito de pagos (§15). `CEM_PAGOS_DIAS_BANDEJA` la
cambia."""
COTEJO_MAXIMO_LINEAS = 50_000
"""Más que el extracto de varios años de un edificio."""


@dataclass(frozen=True)
class Config:
    database_url: str
    """Conexión con el rol `cem_app` (sin BYPASSRLS)."""
    version: str
    sha_imagen: str
    sha_contratos: str
    redis_url: str
    """Almacén del límite de tasa (§10.6): `redis://…` de `cem-redis`. Obligatoria."""
    access_equipo: str = ""
    """Equipo de Access que firma los JWT de soporte: `https://<equipo>.cloudflareaccess.com`."""
    access_aud: str = ""
    """Audiencia (AUD) de la aplicación de Access que cubre las rutas de soporte."""
    origen_publico: str = ""
    """Origen de la app para armar el enlace de acceso: `https://cem.holancloud.com`. Nunca se
    toma del encabezado `Host`, que el cliente puede inventar. Sin él, pedir un enlace da 503."""
    codigo_clave: str = ""
    """Clave del HMAC de los códigos por correo (§10.3): `secrets.token_urlsafe(32)`, distinta en
    cada entorno, solo en `cem-backend`. Nunca en la base: con ella sola un código no se puede
    probar. Mal escrita impide el arranque; sin ella, pedir la entrada y canjear un código dan
    503."""
    pagos_dias_bandeja: int = PAGOS_DIAS_BANDEJA
    """Cuántos días queda lo rechazado en la bandeja de pagos antes de pasar a la consulta."""
    cotejo_tope_bytes: int = TOPE_BYTES
    """Tamaño máximo de un extracto. nginx deja pasar hasta 21 MB en esas rutas
    (`cem-frontend.conf.template`): más que eso pide cambiarlo también ahí."""
    cotejo_maximo_lineas: int = COTEJO_MAXIMO_LINEAS
    """Líneas máximas de un extracto."""


def _dias(variable: str, por_defecto: int) -> int:
    """Un valor mal escrito impide el arranque: no se reemplaza en silencio por el de omisión."""
    valor = os.environ.get(variable)
    if valor is None:
        return por_defecto
    dias = int(valor)
    if dias < 0:
        raise ValueError(f"{variable} no puede ser negativa")
    return dias


def _positivo(variable: str, por_defecto: int) -> int:
    """Un valor mal escrito impide el arranque, como en `_dias`."""
    valor = int(os.environ.get(variable, por_defecto))
    if valor <= 0:
        raise ValueError(f"{variable} tiene que ser positiva")
    return valor


def cargar() -> Config:
    return Config(
        database_url=os.environ.get("CEM_DATABASE_URL", ""),
        version=os.environ.get("CEM_VERSION", "0.1.0"),
        sha_imagen=os.environ.get("CEM_SHA_IMAGEN", "desconocida"),
        sha_contratos=os.environ.get("CEM_SHA_CONTRATOS", "desconocida"),
        redis_url=os.environ.get("CEM_REDIS_URL", ""),
        access_equipo=os.environ.get("CEM_ACCESS_EQUIPO", ""),
        access_aud=os.environ.get("CEM_ACCESS_AUD", ""),
        origen_publico=os.environ.get("CEM_ORIGEN_PUBLICO", ""),
        codigo_clave=os.environ.get("CEM_CODIGO_CLAVE", ""),
        pagos_dias_bandeja=_dias("CEM_PAGOS_DIAS_BANDEJA", PAGOS_DIAS_BANDEJA),
        cotejo_tope_bytes=_positivo("CEM_COTEJO_TOPE_BYTES", TOPE_BYTES),
        cotejo_maximo_lineas=_positivo("CEM_COTEJO_MAXIMO_LINEAS", COTEJO_MAXIMO_LINEAS),
    )
