"""Cuerpo uniforme de error (documento 13, §11.1)."""

from enum import StrEnum

from pydantic import BaseModel, Field


class CodigoError(StrEnum):
    """Identificadores estables que el frontend traduce. Nunca cambian de significado."""

    NO_AUTENTICADO = "no_autenticado"
    NO_ENCONTRADO = "no_encontrado"
    """También se usa para recursos de otro consorcio: 403 y 404 no se distinguen hacia afuera."""
    PETICION_INVALIDA = "peticion_invalida"
    DEMASIADAS_PETICIONES = "demasiadas_peticiones"
    """Límite de tasa superado (documento 13, §10.6); la respuesta lleva `Retry-After`."""
    ERROR_INTERNO = "error_interno"
    SERVICIO_NO_DISPONIBLE = "servicio_no_disponible"
    """Falta una dependencia sin la cual no se atiende (p. ej. Redis para el límite de tasa)."""


class Error(BaseModel):
    codigo: CodigoError
    mensaje: str = Field(description="Texto genérico para la persona usuaria; sin trazas.")
    detalle: dict[str, object] | None = None
    id_correlacion: str = Field(description="Identificador para cruzar con los registros.")
