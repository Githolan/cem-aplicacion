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
    """Falta una dependencia sin la cual no se atiende (Redis del límite, claves de Access)."""
    IDEMPOTENCIA_REUTILIZADA = "idempotencia_reutilizada"
    """La `Idempotency-Key` ya se usó en esta ruta con otro pedido (documento 13, §7.4)."""
    IDEMPOTENCIA_EN_CURSO = "idempotencia_en_curso"
    """Otro pedido con la misma `Idempotency-Key` sigue en curso; se reintenta más tarde."""
    ENLACE_INVALIDO = "enlace_invalido"
    """El enlace de acceso no existe, venció, ya se usó o se abrió en otro navegador (§10.3).
    Las cuatro causas responden igual: la diferencia solo va a los registros."""
    CODIGO_INVALIDO = "codigo_invalido"
    """El código por correo no sirve: no hay cuenta, no es el del último pedido, venció, ya se
    usó, se agotaron sus cinco intentos o es incorrecto (§10.3). Todas las causas responden
    igual: la diferencia solo va a los registros."""
    CSRF_INVALIDO = "csrf_invalido"
    """Falta el encabezado `X-CSRF-Token` o no corresponde a la sesión (documento 13, §10.4)."""
    SIN_PERMISO = "sin_permiso"
    """La persona está en el consorcio pero su vínculo no le da esa acción (documento 08, §4)."""
    INVITACION_INVALIDA = "invitacion_invalida"
    """La invitación no existe, venció, ya se aceptó o la retiraron (RF03). Todas las causas
    responden igual: la diferencia solo va a los registros."""
    INVITACION_REQUIERE_SESION = "invitacion_requiere_sesion"
    """El correo invitado ya tiene cuenta: hay que entrar con él y volver a abrir la invitación."""
    INVITACION_DE_OTRA_PERSONA = "invitacion_de_otra_persona"
    """La sesión abierta es de otra persona que la del correo invitado."""
    INVITACION_NO_PENDIENTE = "invitacion_no_pendiente"
    """Se pidió retirar una invitación que ya se aceptó: para quitar el acceso se termina el
    vínculo."""


class Error(BaseModel):
    codigo: CodigoError
    mensaje: str = Field(description="Texto genérico para la persona usuaria; sin trazas.")
    detalle: dict[str, object] | None = None
    id_correlacion: str = Field(description="Identificador para cruzar con los registros.")
