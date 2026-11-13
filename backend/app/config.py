"""Configuración por variables de entorno.

Ningún secreto vive en el repositorio (documento 13, §15.4).
"""

import os
import re
from dataclasses import dataclass
from datetime import time

from cem_contracts.documentos import TOPE_BYTES

PAGOS_DIAS_BANDEJA = 7
"""Decisión por defecto del rediseño del circuito de pagos (§15). `CEM_PAGOS_DIAS_BANDEJA` la
cambia."""
PAGOS_DIAS_BORRADOR_VIEJO = 7
"""Decisión 4 del propietario (12-11-2026): un borrador sin movimiento pasa a «Borradores
viejos». `CEM_PAGOS_DIAS_BORRADOR_VIEJO` la cambia."""
PAGOS_DIAS_REPETIDA = 30
"""Decisión 5 del propietario (12-11-2026): ventana del aviso de orden repetida.
`CEM_PAGOS_DIAS_REPETIDA` la cambia."""
PAGOS_HORAS_TOMAR = 24
"""Decisión 1 del propietario (07-11-2026). `CEM_PAGOS_HORAS_TOMAR` la cambia."""
AVISOS_HORA_UTC = time(12, 0)
"""Pregunta 7 del propietario (13-11-2026): el aviso diario de pendientes sale a las 12:00 UTC (las
09:00 en Buenos Aires). `CEM_AVISOS_HORA_UTC` («HH:MM», en UTC) la cambia."""
AVISOS_TOPE_DIARIO = 1
"""Pregunta 7: como máximo un aviso por persona por día. `CEM_AVISOS_TOPE_DIARIO` lo cambia; con
`N` el aviso sale `N` veces por día, repartidas desde la hora; con 0 no sale ninguno."""
COTEJO_MAXIMO_LINEAS = 50_000
"""Más que el extracto de varios años de un edificio."""
SALUD_CACHE_SEGUNDOS = 10
"""Cuánto se reutiliza el resultado de `/api/v1/health/deps` (revisión de seguridad H3, P3): la
ruta es pública y sin límite de tasa, y sin caché cada llamada toma una conexión. Kuma la consulta
cada minuto, así que 10 segundos no le esconden nada. `CEM_SALUD_CACHE_SEGUNDOS` la cambia."""


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
    pagos_dias_borrador_viejo: int = PAGOS_DIAS_BORRADOR_VIEJO
    """Días sin movimiento tras los que un borrador pasa a «Borradores viejos»."""
    pagos_dias_repetida: int = PAGOS_DIAS_REPETIDA
    """Días de diferencia entre dos órdenes iguales para avisar que puede estar repetida."""
    pagos_horas_tomar: int = PAGOS_HORAS_TOMAR
    """Cuántas horas espera otro ejecutor a quien vio la cuenta antes de poder tomar el pago."""
    cotejo_tope_bytes: int = TOPE_BYTES
    """Tamaño máximo de un extracto. nginx deja pasar hasta 21 MB en esas rutas
    (`cem-frontend.conf.template`): más que eso pide cambiarlo también ahí."""
    cotejo_maximo_lineas: int = COTEJO_MAXIMO_LINEAS
    """Líneas máximas de un extracto."""
    salud_cache_segundos: int = SALUD_CACHE_SEGUNDOS
    """Segundos durante los que `/api/v1/health/deps` reutiliza su última comprobación."""
    avisos_hora_utc: time = AVISOS_HORA_UTC
    """Hora (UTC) del primer aviso del día a quien tiene algo para aprobar, pagar o conciliar."""
    avisos_tope_diario: int = AVISOS_TOPE_DIARIO
    """Avisos por persona en 24 horas, y corridas por día del aviso. 0 apaga los avisos."""


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


HORA = re.compile(r"(?P<hora>[01]\d|2[0-3]):(?P<minuto>[0-5]\d)")


def _hora(variable: str, por_defecto: time) -> time:
    """«HH:MM». Un valor mal escrito impide el arranque, como en `_dias`."""
    valor = os.environ.get(variable)
    if valor is None:
        return por_defecto
    hallada = HORA.fullmatch(valor)
    if hallada is None:
        raise ValueError(f"{variable} tiene que ser una hora HH:MM, no {valor!r}")
    return time(int(hallada["hora"]), int(hallada["minuto"]))


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
        pagos_dias_borrador_viejo=_dias("CEM_PAGOS_DIAS_BORRADOR_VIEJO", PAGOS_DIAS_BORRADOR_VIEJO),
        pagos_dias_repetida=_dias("CEM_PAGOS_DIAS_REPETIDA", PAGOS_DIAS_REPETIDA),
        pagos_horas_tomar=_positivo("CEM_PAGOS_HORAS_TOMAR", PAGOS_HORAS_TOMAR),
        cotejo_tope_bytes=_positivo("CEM_COTEJO_TOPE_BYTES", TOPE_BYTES),
        cotejo_maximo_lineas=_positivo("CEM_COTEJO_MAXIMO_LINEAS", COTEJO_MAXIMO_LINEAS),
        salud_cache_segundos=_positivo("CEM_SALUD_CACHE_SEGUNDOS", SALUD_CACHE_SEGUNDOS),
        avisos_hora_utc=_hora("CEM_AVISOS_HORA_UTC", AVISOS_HORA_UTC),
        avisos_tope_diario=_dias("CEM_AVISOS_TOPE_DIARIO", AVISOS_TOPE_DIARIO),
    )
