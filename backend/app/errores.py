"""Errores con cuerpo uniforme (documento 13, §11.1).

Forma: `{codigo, mensaje, detalle, id_correlacion}`. El id es el de la petición
(`registros.RegistroDePeticiones`): el mismo que llevan sus líneas de registro.
"""

import logging

from cem_contracts.errores import CodigoError, Error
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.registros import id_correlacion_actual

log = logging.getLogger("cem")


class ErrorApi(Exception):
    def __init__(self, estado: int, codigo: CodigoError, mensaje: str) -> None:
        self.estado, self.codigo, self.mensaje = estado, codigo, mensaje


def respuesta_error(estado: int, codigo: CodigoError, mensaje: str, detalle=None) -> JSONResponse:
    cuerpo = Error(
        codigo=codigo, mensaje=mensaje, detalle=detalle, id_correlacion=id_correlacion_actual()
    )
    return JSONResponse(cuerpo.model_dump(mode="json"), status_code=estado)


def instalar(app: FastAPI) -> None:
    @app.exception_handler(ErrorApi)
    async def _api(_: Request, e: ErrorApi) -> JSONResponse:
        return respuesta_error(e.estado, e.codigo, e.mensaje)

    @app.exception_handler(RequestValidationError)
    async def _validacion(_: Request, e: RequestValidationError) -> JSONResponse:
        campos = [".".join(str(p) for p in err["loc"]) for err in e.errors()]
        return respuesta_error(
            422, CodigoError.PETICION_INVALIDA, "Revisá los datos enviados.", {"campos": campos}
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, e: StarletteHTTPException) -> JSONResponse:
        if e.status_code == 404:
            return respuesta_error(404, CodigoError.NO_ENCONTRADO, "No encontramos lo que buscás.")
        return respuesta_error(e.status_code, CodigoError.PETICION_INVALIDA, "Petición no válida.")

    @app.exception_handler(Exception)
    async def _interno(_: Request, e: Exception) -> JSONResponse:
        log.exception("error no controlado")
        return respuesta_error(500, CodigoError.ERROR_INTERNO, "Ocurrió un error. Probá de nuevo.")
