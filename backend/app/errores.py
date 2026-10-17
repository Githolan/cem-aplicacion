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
    ) -> None:
        self.estado, self.codigo, self.mensaje = estado, codigo, mensaje
        self.encabezados = encabezados or {}

    def respuesta(self) -> JSONResponse:
        respuesta = respuesta_error(self.estado, self.codigo, self.mensaje)
        respuesta.headers.update(self.encabezados)
        return respuesta


def respuesta_error(estado: int, codigo: CodigoError, mensaje: str, detalle=None) -> JSONResponse:
    cuerpo = Error(
        codigo=codigo, mensaje=mensaje, detalle=detalle, id_correlacion=id_correlacion_actual()
    )
    return JSONResponse(cuerpo.model_dump(mode="json"), status_code=estado)


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
        campos = [".".join(str(p) for p in err["loc"]) for err in e.errors()]
        return _terminar(
            request,
            respuesta_error(
                422, CodigoError.PETICION_INVALIDA, "Revisá los datos enviados.", {"campos": campos}
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, e: StarletteHTTPException) -> JSONResponse:
        if e.status_code == 404:
            respuesta = respuesta_error(
                404, CodigoError.NO_ENCONTRADO, "No encontramos lo que buscás."
            )
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
                "El servicio no está disponible. Probá en un rato.",
            ),
        )

    @app.exception_handler(Exception)
    async def _interno(request: Request, e: Exception) -> JSONResponse:
        log.exception("error no controlado")
        return _terminar(
            request,
            respuesta_error(500, CodigoError.ERROR_INTERNO, "Ocurrió un error. Probá de nuevo."),
        )
