"""Errores con cuerpo uniforme (documento 13, §11.1).

Forma: `{codigo, mensaje, detalle, id_correlacion}`. El id es el de la petición
(`registros.RegistroDePeticiones`): el mismo que llevan sus líneas de registro.

Sin base (`CEM_DATABASE_URL` vacía, o PostgreSQL que no responde) la API contesta 503
`servicio_no_disponible`, como cuando falta cualquier otra dependencia (Redis, las claves, el
origen público); nunca 500.

Un router con la dependencia `sin_cache` lleva `Cache-Control: no-store` en todas sus
respuestas, también en los errores que salen de sus dependencias (límite de tasa, sesión,
CSRF, claves, base), de la validación y del 500.
"""

import logging

from cem_contracts.errores import CodigoError, Error
from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.db import SinBase
from app.registros import id_correlacion_actual

log = logging.getLogger("cem")

SIN_CACHE = {"Cache-Control": "no-store"}


class ErrorApi(Exception):
    def __init__(
        self,
        estado: int,
        codigo: CodigoError,
        mensaje: str,
        encabezados: dict[str, str] | None = None,
        detalle: dict[str, object] | None = None,
    ) -> None:
        self.estado, self.codigo, self.mensaje = estado, codigo, mensaje
        self.encabezados = encabezados or {}
        self.detalle = detalle

    def respuesta(self) -> JSONResponse:
        respuesta = respuesta_error(self.estado, self.codigo, self.mensaje, self.detalle)
        respuesta.headers.update(self.encabezados)
        return respuesta


def respuesta_error(estado: int, codigo: CodigoError, mensaje: str, detalle=None) -> JSONResponse:
    cuerpo = Error(
        codigo=codigo, mensaje=mensaje, detalle=detalle, id_correlacion=id_correlacion_actual()
    )
    return JSONResponse(cuerpo.model_dump(mode="json"), status_code=estado)


FORMATO_POR_CAMPO = {
    "correo": "Escribir un correo válido, por ejemplo nombre@dominio.com.",
    "cuenta_destino": "Escribir los 22 números del CBU o CVU, sin espacios ni guiones.",
    "cuit": "Escribir los 11 números del CUIT, sin guiones.",
    "telefono": "Escribir el teléfono solo con números, espacios, paréntesis o guiones.",
    "whatsapp": "Escribir el número solo con números, espacios, paréntesis o guiones.",
}
"""El formato que se espera, por el nombre del campo, cuando no cumple su patrón."""

FALTA_COMPLETAR = "Falta completar este dato."
REVISAR_DATO = "Revisar este dato."


def _con_punto(texto: str) -> str:
    texto = texto.strip()
    return texto[:1].upper() + texto[1:] + ("" if texto.endswith((".", "?", "!")) else ".")


def _mensaje_de_campo(err: dict, campo: str) -> str:
    """Qué hacer con un dato rechazado por la validación. Nunca repite lo que se escribió: un
    número de cuenta o un dato de otra persona no vuelven en la respuesta."""
    tipo, ctx = err["type"], err.get("ctx") or {}
    if tipo == "missing":
        return FALTA_COMPLETAR
    if tipo == "string_too_short":
        minimo = ctx.get("min_length", 1)
        return FALTA_COMPLETAR if minimo <= 1 else f"Escribir al menos {minimo} caracteres."
    if tipo == "string_too_long":
        return f"Usar hasta {ctx['max_length']} caracteres."
    if tipo == "string_pattern_mismatch":
        return FORMATO_POR_CAMPO.get(campo, "Revisar cómo está escrito: el formato no corresponde.")
    if tipo == "value_error" and isinstance(ctx.get("error"), ValueError):
        # Los motivos propios de los contratos, escritos para la persona.
        return _con_punto(str(ctx["error"]))
    if tipo.startswith("date"):
        return "Escribir una fecha válida." if not tipo.startswith("datetime") else REVISAR_DATO
    if tipo in ("enum", "literal_error", "uuid_parsing", "uuid_type"):
        return "Elegir una de las opciones."
    if tipo.startswith(("int_", "decimal_", "float_")):
        return "Escribir un número válido."
    if tipo == "greater_than":
        return f"Tiene que ser mayor que {ctx['gt']}."
    if tipo == "greater_than_equal":
        return f"Tiene que ser {ctx['ge']} o más."
    if tipo == "less_than":
        return f"Tiene que ser menor que {ctx['lt']}."
    if tipo == "less_than_equal":
        return f"Tiene que ser {ctx['le']} o menos."
    return REVISAR_DATO


def mensajes_por_campo(errores) -> dict[str, str]:
    """Un mensaje por cada campo del cuerpo (`body.<campo>`); el primero si hay varios. Lo que
    rechaza el modelo entero (`body` solo) no nombra un campo y no lleva mensaje acá."""
    mensajes: dict[str, str] = {}
    for err in errores:
        loc = [str(p) for p in err["loc"]]
        if len(loc) < 2 or loc[0] != "body":
            continue
        clave = ".".join(loc)
        mensajes.setdefault(clave, _mensaje_de_campo(err, loc[-1]))
    return mensajes


def detalle_de_campo(campo: str | None) -> dict[str, object] | None:
    """El campo del cuerpo que motivó un error de negocio, con la misma forma que el 422
    (`detalle.campos`), para que la pantalla lo marque. Sin campo, sin detalle."""
    return None if campo is None else {"campos": [f"body.{campo}"]}


def sin_cache(request: Request, response: Response) -> None:
    """Dependencia de router. Va primera (FastAPI pone las del router antes que las de la
    ruta), así que el error de cualquier dependencia posterior ya la encuentra marcada."""
    request.state.sin_cache = True
    response.headers.update(SIN_CACHE)


def _terminar(request: Request, respuesta: JSONResponse) -> JSONResponse:
    if getattr(request.state, "sin_cache", False):
        respuesta.headers.update(SIN_CACHE)
    return respuesta


def instalar(app: FastAPI) -> None:
    @app.exception_handler(ErrorApi)
    async def _api(request: Request, e: ErrorApi) -> JSONResponse:
        return _terminar(request, e.respuesta())

    @app.exception_handler(RequestValidationError)
    async def _validacion(request: Request, e: RequestValidationError) -> JSONResponse:
        errores = e.errors()
        campos = [".".join(str(p) for p in err["loc"]) for err in errores]
        return _terminar(
            request,
            respuesta_error(
                422,
                CodigoError.PETICION_INVALIDA,
                "Hay que revisar los datos enviados.",
                {"campos": campos, "mensajes": mensajes_por_campo(errores)},
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, e: StarletteHTTPException) -> JSONResponse:
        if e.status_code == 404:
            respuesta = respuesta_error(404, CodigoError.NO_ENCONTRADO, "No se encontró lo pedido.")
        else:
            respuesta = respuesta_error(
                e.status_code, CodigoError.PETICION_INVALIDA, "Petición no válida."
            )
        return _terminar(request, respuesta)

    @app.exception_handler(SinBase)
    @app.exception_handler(OperationalError)
    async def _base(request: Request, e: SinBase | OperationalError) -> JSONResponse:
        """Sin motor, o la conexión a PostgreSQL falló o la base cortó la operación (caída,
        reinicio, tiempo agotado): es la base la que no está, no un error del pedido."""
        causa = e.orig if isinstance(e, OperationalError) else e
        log.error("base de datos no disponible: %s: %s", type(causa).__name__, causa)
        return _terminar(
            request,
            respuesta_error(
                503,
                CodigoError.SERVICIO_NO_DISPONIBLE,
                "El servicio no está disponible. Reintentar en un rato.",
            ),
        )

    @app.exception_handler(Exception)
    async def _interno(request: Request, e: Exception) -> JSONResponse:
        log.exception("error no controlado")
        return _terminar(
            request,
            respuesta_error(500, CodigoError.ERROR_INTERNO, "Ocurrió un error. Reintentar."),
        )
