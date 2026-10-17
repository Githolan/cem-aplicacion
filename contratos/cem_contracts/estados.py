"""Enumeraciones de estados compartidas por backend y frontend."""

from enum import StrEnum


class ModoOperacion(StrEnum):
    """Modo de operación del consorcio (documento 08, §5; `nucleo.tipo_modo`, 0010)."""

    CONSEJO = "consejo"
    ADMINISTRADOR_AUMENTADO = "administrador_aumentado"
    AUTOGESTION = "autogestion"


class TipoVinculo(StrEnum):
    """Relación de una persona con una unidad funcional."""

    PROPIETARIO = "propietario"
    INQUILINO = "inquilino"
    OCUPANTE = "ocupante"
