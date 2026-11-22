"""Perfil normativo del consorcio, mínimo para el piloto (RF01, RF47; `0125`; diseño P0 S5).

El perfil es de una jurisdicción y tiene versiones con su vigencia (decisiones del propietario del
25-10-2026, §1.2: lo mantiene el equipo CEM con fuentes oficiales y registra norma, vigencia,
jurisdicción, versión, fuente y fecha de verificación). Cada versión dice qué exige cada requisito;
cambiar el perfil es publicar otra versión, nunca cambiar una. El consorcio queda vinculado a una
versión.

En Modo Consejo CEM no publica la liquidación: el requisito del enlace a la documentación
(Disposición 1146/DGDYPC/24) es una **señal de fiscalización** sobre la liquidación oficial
confirmada, no un bloqueo (PA37 es de la publicación, en Administrador Aumentado y Autogestión).
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Jurisdiccion(StrEnum):
    CABA = "caba"
    """Ciudad Autónoma de Buenos Aires."""


class RequisitoNormativo(StrEnum):
    LIQUIDACION_ENLACE_DOCUMENTACION = "liquidacion_enlace_documentacion"
    """La liquidación lleva un código QR o un hipervínculo a la documentación del período."""


class EstadoEnlaceDocumentacion(StrEnum):
    ENCONTRADO = "encontrado"
    """La lectura encontró al menos un enlace o un QR. CEM no los sigue: no comprueba a dónde
    llevan."""
    NO_ENCONTRADO = "no_encontrado"
    """La lectura buscó en todo el documento (texto, anotaciones y QR de cada página) y no encontró
    ninguno."""
    SIN_BUSCAR = "sin_buscar"
    """La lectura no pudo buscar en todo el documento y no encontró ninguno: una foto (sin texto
    que leer), un PDF con alguna página sin texto (una escaneada) o con más páginas de las que se
    dibujan (sin QR), o una liquidación leída antes de `0125`."""


LARGO_NORMA = 200
"""El de la base (`0125`); una prueba compara."""


class EnlaceDocumentacion(BaseModel):
    """Lo que el perfil normativo del consorcio exige de la liquidación oficial de un período sobre
    el enlace a la documentación, y lo que encontró la lectura. Solo para la vigente de su
    período, y solo si la versión del perfil lo exige desde antes de ese período."""

    model_config = ConfigDict(frozen=True)

    estado: EstadoEnlaceDocumentacion
    jurisdiccion: Jurisdiccion
    version: int = Field(ge=1, description="La versión del perfil a la que está vinculado.")
    norma: str = Field(max_length=LARGO_NORMA, description="La norma que lo exige.")
    fuente: str = Field(description="La publicación oficial de la norma.")
