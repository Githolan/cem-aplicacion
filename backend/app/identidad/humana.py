"""Huella del documento para la identidad humana por consorcio (A13-C, migración 0026).

Una cuenta controla un correo; la identidad humana dice qué ser humano es, para que las
separaciones distingan personas y no solo cuentas. Para detectar dos cuentas del mismo ser
humano se usa el tipo y el número del documento que soporte vio, en forma canónica, y se guarda
solo su huella: `HMAC-SHA256(clave, etiqueta || consorcio || documento canónico)`.

- **Acotada al consorcio:** el mismo documento en dos consorcios da dos huellas sin relación, así
  que la base no permite correlacionar a nadie entre consorcios (decisión del propietario).
- **Sin el número en claro:** existe solo en memoria, en el script de soporte, mientras se
  verifica. Nunca va a la base, a los registros, a los errores ni a la auditoría.
- **Claves versionadas:** `CEM_IDENTIDAD_CLAVES` = `versión:clave[,versión:clave…]`, cada clave
  como `CEM_CODIGO_CLAVE` (`secrets.token_urlsafe(32)`), distinta en cada entorno y solo en
  `cem-backend`. La de versión más alta firma lo nuevo; las anteriores siguen reconociendo los
  documentos verificados con ellas. Retirar una versión exige volver a ver esos documentos
  (`corregir`): sin el número, una huella no se recalcula. Mientras quede una identidad firmada
  con una versión que el llavero no tiene, la base niega verificar, vincular y corregir. Es una
  variable de entorno más, igual que las demás claves; no hay otro mecanismo de secretos.

El número en claro de un DNI es chico (unos 10⁸ valores): sin la clave la huella no se puede
probar, pero con la clave sí. Por eso la clave nunca está en la base ni en sus copias.
"""

import hashlib
import hmac
import re
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from app.identidad.sesiones import clave_codigo

_ETIQUETA = b"cem-identidad-humana"
_DIGITOS = re.compile(r"^[0-9]{1,9}$")
_PAIS = re.compile(r"^[A-Z]{3}$")
_PASAPORTE = re.compile(r"^[A-Z0-9]{5,20}$")
_SEPARADORES = re.compile(r"[\s.\-]")


class TipoDocumento(StrEnum):
    DNI = "dni"
    """Documento Nacional de Identidad argentino. La LE y la LC llevan el mismo número que el DNI
    que las reemplazó: se cargan como DNI."""
    PASAPORTE = "pasaporte"
    """Solo para quien no tiene DNI. Lleva el país emisor (ISO 3166-1 alfa-3)."""


class DocumentoInvalido(ValueError):
    """El documento no tiene la forma esperada. El mensaje nunca repite el número."""


@dataclass(frozen=True)
class Llavero:
    """Las claves de `CEM_IDENTIDAD_CLAVES`, de la versión más alta a la más baja."""

    claves: tuple[tuple[int, bytes], ...]

    @property
    def vigente(self) -> int:
        return self.claves[0][0]


def cargar_llavero(valor: str) -> Llavero:
    """Lee `versión:clave[,versión:clave…]`. Mal escrita, falla: nunca se firma con una clave
    corta ni se sigue sin ella."""
    claves: dict[int, bytes] = {}
    for parte in (p.strip() for p in valor.split(",")):
        version, separador, clave = parte.partition(":")
        if not separador or not version.isdigit() or int(version) <= 0:
            raise RuntimeError("CEM_IDENTIDAD_CLAVES: cada entrada es versión:clave, versión > 0")
        if int(version) in claves:
            raise RuntimeError("CEM_IDENTIDAD_CLAVES: una versión repetida")
        try:
            claves[int(version)] = clave_codigo(clave)
        except RuntimeError as e:
            raise RuntimeError(
                "CEM_IDENTIDAD_CLAVES: cada clave, 32 bytes en base64url sin relleno"
            ) from e
    if not claves:
        raise RuntimeError("CEM_IDENTIDAD_CLAVES sin configurar")
    return Llavero(tuple(sorted(claves.items(), reverse=True)))


def canonico(tipo: TipoDocumento, numero: str, pais: str | None = None) -> str:
    """La forma única de un documento: sin puntos, espacios ni guiones; el DNI sin ceros a la
    izquierda y el pasaporte en mayúsculas con su país."""
    limpio = _SEPARADORES.sub("", numero).upper()
    if tipo is TipoDocumento.DNI:
        if pais is not None:
            raise DocumentoInvalido("el DNI no lleva país")
        sin_ceros = limpio.lstrip("0")
        if not _DIGITOS.fullmatch(sin_ceros):
            raise DocumentoInvalido("el DNI tiene que ser un número de hasta 9 dígitos")
        return f"dni:{sin_ceros}"
    if pais is None or not _PAIS.fullmatch(pais.strip().upper()):
        raise DocumentoInvalido("el pasaporte lleva el país emisor en ISO 3166-1 alfa-3 (ARG)")
    if not _PASAPORTE.fullmatch(limpio):
        raise DocumentoInvalido("el pasaporte tiene que tener de 5 a 20 letras y números")
    return f"pasaporte:{pais.strip().upper()}:{limpio}"


def huellas(llavero: Llavero, consorcio_id: UUID, documento: str) -> tuple[list[int], list[bytes]]:
    """La huella del documento canónico con cada clave del llavero, la vigente primero."""
    mensaje = b"\0".join((_ETIQUETA, consorcio_id.bytes, documento.encode("ascii")))
    versiones = [v for v, _ in llavero.claves]
    return versiones, [hmac.new(k, mensaje, hashlib.sha256).digest() for _, k in llavero.claves]


def contiene_documento(texto: str, documento: str) -> bool:
    """Si un texto libre (la referencia o el motivo) trae el número: se rechaza antes de
    guardarlo, para que el número no quede en la base por ese camino."""
    return documento.rsplit(":", 1)[1] in _SEPARADORES.sub("", texto).upper()
