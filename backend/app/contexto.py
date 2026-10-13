"""Identidad y ámbito por consorcio de cada petición (documento 13, §7.2).

La autenticación (módulo `identidad`: sesiones, passkeys, Access) todavía no existe.
`obtener_identidad` rechaza toda petición hasta entonces: no hay ningún atajo de
desarrollo que permita entrar sin credenciales. Las pruebas la reemplazan con
`app.dependency_overrides`.
"""

from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from cem_contracts.errores import CodigoError
from fastapi import Depends, Request
from sqlalchemy import Engine, text

from app.db import transaccion
from app.errores import ErrorApi


@dataclass(frozen=True)
class Identidad:
    persona_id: UUID


@dataclass(frozen=True)
class Contexto:
    persona_id: UUID
    consorcio_id: UUID
    vinculos: tuple[str, ...]


def obtener_identidad() -> Identidad:
    raise ErrorApi(401, CodigoError.NO_AUTENTICADO, "Iniciá sesión para continuar.")


def obtener_motor(request: Request) -> Engine:
    return request.app.state.motor


IdentidadDep = Annotated[Identidad, Depends(obtener_identidad)]
MotorDep = Annotated[Engine, Depends(obtener_motor)]


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
