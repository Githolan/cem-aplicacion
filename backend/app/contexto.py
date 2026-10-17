"""Identidad y ámbito por consorcio de cada petición (documento 13, §7.2 y §10.4).

`obtener_identidad` lee la cookie de sesión (`app/identidad/sesiones.py`), la valida en la
base (existe, no está revocada ni vencida, la persona sigue activa), corre el vencimiento
deslizante y, si la petición modifica estado, exige el token CSRF. Sin sesión válida responde
401: no hay ningún atajo de desarrollo que permita entrar sin credenciales. Las pruebas que no
prueban la autenticación la reemplazan con `app.dependency_overrides`.
"""

from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from cem_contracts.errores import CodigoError
from fastapi import Depends, Request, Response
from sqlalchemy import Engine, text

from app.db import transaccion
from app.errores import ErrorApi
from app.identidad import sesiones


@dataclass(frozen=True)
class Identidad:
    persona_id: UUID
    sesion_id: UUID | None = None
    """La sesión de la petición; None solo en las pruebas que reemplazan la identidad."""


@dataclass(frozen=True)
class Contexto:
    persona_id: UUID
    consorcio_id: UUID
    vinculos: tuple[str, ...]


def obtener_motor(request: Request) -> Engine:
    """None sin `CEM_DATABASE_URL`: `transaccion()` responde 503 cuando se usa (`app/db.py`)."""
    return request.app.state.motor


MotorDep = Annotated[Engine, Depends(obtener_motor)]


def obtener_identidad(request: Request, response: Response, motor: MotorDep) -> Identidad:
    token = request.cookies.get(sesiones.COOKIE_SESION)
    if not sesiones.token_valido(token):
        raise ErrorApi(401, CodigoError.NO_AUTENTICADO, "Iniciá sesión para continuar.")
    # Ninguna persona en el ámbito: la validación la hace una función `security definer`.
    with transaccion(motor, persona_id=None) as conn:
        sesion = sesiones.validar(conn, token)
    if sesion is None:
        raise ErrorApi(401, CodigoError.NO_AUTENTICADO, "Iniciá sesión para continuar.")
    sesiones.exigir_csrf(request, token)
    if sesion.renovada:
        sesiones.fijar_sesion(response, token)
    return Identidad(persona_id=sesion.persona_id, sesion_id=sesion.sesion_id)


IdentidadDep = Annotated[Identidad, Depends(obtener_identidad)]


def require_scope(consorcio_id: UUID, identidad: IdentidadDep, motor: MotorDep) -> Contexto:
    """Carga los vínculos vigentes de la persona en el consorcio de la ruta.

    Sin vínculo vigente responde 404, igual que si el consorcio no existiera.
    """
    with transaccion(motor, persona_id=identidad.persona_id) as conn:
        vinculos = conn.execute(
            text(
                "select tipo from nucleo.vinculo"
                " where consorcio_id = :c and persona_id = :p"
                " and daterange(vigente_desde, vigente_hasta) @> current_date order by tipo"
            ),
            {"c": consorcio_id, "p": identidad.persona_id},
        ).scalars()
        tipos = tuple(vinculos)
    if not tipos:
        raise ErrorApi(404, CodigoError.NO_ENCONTRADO, "No encontramos lo que buscás.")
    return Contexto(persona_id=identidad.persona_id, consorcio_id=consorcio_id, vinculos=tipos)


ContextoDep = Annotated[Contexto, Depends(require_scope)]
