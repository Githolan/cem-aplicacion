"""Contactos de emergencia del consorcio (H2b 2b-7; RF06, primera parte; D5).

Diseño: `doc/diseno/H2b_7_Diseno.md` del repositorio CEM, con la decisión del propietario del
25-10-2026 (`Decisiones_Propietario_2026-10-25.md`, §1.1).

- **`GET …/contactos`** (`contactos.ver`, por ámbito): los vigentes hoy, sin quién cambió qué,
  la observación ni la constancia del consentimiento. Es la respuesta que el dispositivo guarda
  sin conexión.
- **`GET …/contactos/gestion`** (`contactos.gestionar`, MC y AD): suma los futuros y esos datos.
  Es una ruta aparte para que cada ruta declare una sola acción y la lista de gestión nunca
  comparta URL con la que se guarda.
- **Escrituras** (`contactos.gestionar`): agregar, corregir, retirar, verificar y señalar. La
  política decide antes de leer nada, así que un contacto inexistente da 404 solo a quien tiene
  la facultad.

Las funciones de `0031` vuelven a exigir el ámbito o la facultad. Si la base niega lo que el
evaluador concedió, es una discrepancia (500), nunca una lista vacía ni un 403.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated
from uuid import UUID

from cem_contracts.contactos import (
    Contacto,
    ContactoGestion,
    ContactoId,
    ContactoNuevo,
    DatosContacto,
    ListaContactos,
    ListaContactosGestion,
    Senalamiento,
)
from cem_contracts.errores import CodigoError
from fastapi import APIRouter, Depends, Response
from psycopg.errors import InsufficientPrivilege
from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError

from app.autorizacion.evaluador import Entidad
from app.autorizacion.requiere import NO_ENCONTRADO, Permiso, requiere
from app.consorcios.padron import Discrepancia
from app.contexto import MotorDep
from app.db import transaccion
from app.errores import ErrorApi, sin_cache
from app.plataforma.idempotencia import IdempotenciaDep, responder_una_vez

router = APIRouter(prefix="/api/v1", tags=["contactos"], dependencies=[Depends(sin_cache)])

ERRORES = {
    "no_encontrado": (404, CodigoError.NO_ENCONTRADO, NO_ENCONTRADO),
    "contacto_terminado": (
        409, CodigoError.CONTACTO_TERMINADO,
        "Ese contacto ya no está vigente: para cambiarlo, agregá uno nuevo.",
    ),
}  # fmt: skip
"""Motivos de las funciones de `0031`. `origen_invalido` lo fija esta ruta: si llega, es un
defecto (500)."""

COLUMNAS = (
    "id, funcion, naturaleza, nombre, telefono, indicaciones, vigente_desde, vigente_hasta,"
    " actualizado_en, verificado_en, observado"
)
COLUMNAS_GESTION = (
    COLUMNAS + ", consentimiento_referencia, observacion, actualizado_por, verificado_por,"
    " observado_por"
)


@contextmanager
def _lectura(permiso: Permiso, motor) -> Iterator[Connection]:
    ctx = permiso.ctx
    permiso.verificar(Entidad())
    try:
        with transaccion(motor, persona_id=ctx.persona_id, consorcio_id=ctx.consorcio_id) as c:
            yield c
    except DBAPIError as e:
        if isinstance(e.orig, InsufficientPrivilege):
            raise Discrepancia(f"la base niega {permiso.accion} y el evaluador lo concede") from e
        raise


def _contactos(c: Connection, columnas: str, gestion: bool) -> list:
    sql = f"select {columnas} from nucleo.contactos_del_consorcio(:g)"
    return list(c.execute(text(sql), {"g": gestion}).mappings().all())


def _escribir(conn: Connection, sql: str, parametros: dict):
    """Ejecuta una función de `0031` y traduce sus motivos a errores de la API."""
    try:
        return conn.execute(text(sql), parametros)
    except DBAPIError as e:
        if isinstance(e.orig, InsufficientPrivilege):
            raise Discrepancia("la base niega contactos.gestionar y el evaluador lo concede") from e
        motivo = e.orig.diag.message_hint
        if motivo in ERRORES:
            raise ErrorApi(*ERRORES[motivo]) from e
        raise


@router.get(
    "/consorcios/{consorcio_id}/contactos",
    response_model=ListaContactos,
    summary="Contactos de emergencia vigentes, solo los que no son personales",
)
def listar_contactos(
    permiso: Annotated[Permiso, Depends(requiere("contactos.ver"))], motor: MotorDep
) -> ListaContactos:
    with _lectura(permiso, motor) as c:
        filas = _contactos(c, COLUMNAS, gestion=False)
        estado = c.execute(text("select * from nucleo.estado_contactos()")).mappings().one()
    return ListaContactos(elementos=[Contacto(**k) for k in filas], **estado)


@router.get(
    "/consorcios/{consorcio_id}/contactos/gestion",
    response_model=ListaContactosGestion,
    summary="Contactos vigentes y futuros, con quién cambió qué (gestión)",
    responses={403: {"description": "Sin `contactos.gestionar` en el consorcio."}},
)
def listar_contactos_gestion(
    permiso: Annotated[Permiso, Depends(requiere("contactos.gestionar"))], motor: MotorDep
) -> ListaContactosGestion:
    with _lectura(permiso, motor) as c:
        filas = _contactos(c, COLUMNAS_GESTION, gestion=True)
    return ListaContactosGestion(elementos=[ContactoGestion(**c) for c in filas])


def _datos(d: DatosContacto) -> dict:
    return {
        "f": d.funcion.value,
        "n": d.naturaleza.value,
        "nom": d.nombre,
        "tel": d.telefono,
        "con": d.consentimiento_referencia,
        "ind": d.indicaciones,
    }


@router.post(
    "/consorcios/{consorcio_id}/contactos",
    status_code=201,
    response_model=ContactoId,
    summary="Agregar un contacto de emergencia",
    responses={403: {"description": "Sin `contactos.gestionar` en el consorcio."}},
)
def crear_contacto(
    nuevo: ContactoNuevo,
    permiso: Annotated[Permiso, Depends(requiere("contactos.gestionar"))],
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    ctx = permiso.ctx
    permiso.verificar(Entidad())

    def efecto(conn: Connection) -> tuple[int, ContactoId]:
        nuevo_id = _escribir(
            conn,
            "select nucleo.crear_contacto(:f, :n, :nom, :tel, :con, :ind, :desde, :a, 'api')",
            {**_datos(nuevo), "desde": nuevo.vigente_desde, "a": ctx.persona_id},
        ).scalar_one()
        return 201, ContactoId(id=nuevo_id)

    return responder_una_vez(motor, ctx, idempotencia, efecto)


RESPUESTAS_CAMBIO = {
    403: {"description": "Sin `contactos.gestionar` en el consorcio."},
    404: {"description": "El contacto no existe en este consorcio."},
    409: {"description": "El contacto ya no está vigente (`contacto_terminado`)."},
}


def _cambiar(permiso: Permiso, idempotencia, motor, contacto_id: UUID, sql: str, extra: dict):
    ctx = permiso.ctx
    permiso.verificar(Entidad())

    def efecto(conn: Connection) -> tuple[int, ContactoId]:
        _escribir(conn, sql, {"k": contacto_id, "a": ctx.persona_id, **extra})
        return 200, ContactoId(id=contacto_id)

    return responder_una_vez(motor, ctx, idempotencia, efecto)


@router.put(
    "/consorcios/{consorcio_id}/contactos/{contacto_id}",
    response_model=ContactoId,
    summary="Corregir un contacto vigente; resuelve la observación si había",
    responses=RESPUESTAS_CAMBIO,
)
def corregir_contacto(
    contacto_id: UUID,
    datos: DatosContacto,
    permiso: Annotated[Permiso, Depends(requiere("contactos.gestionar"))],
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    return _cambiar(
        permiso, idempotencia, motor, contacto_id,
        "select nucleo.corregir_contacto(:k, :f, :n, :nom, :tel, :con, :ind, :a, 'api')",
        _datos(datos),
    )  # fmt: skip


@router.post(
    "/consorcios/{consorcio_id}/contactos/{contacto_id}/retirar",
    response_model=ContactoId,
    summary="Retirar un contacto: desde hoy deja de figurar (la fila no se borra)",
    responses=RESPUESTAS_CAMBIO,
)
def retirar_contacto(
    contacto_id: UUID,
    permiso: Annotated[Permiso, Depends(requiere("contactos.gestionar"))],
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    return _cambiar(
        permiso, idempotencia, motor, contacto_id,
        "select nucleo.retirar_contacto(:k, :a, 'api')", {},
    )  # fmt: skip


@router.post(
    "/consorcios/{consorcio_id}/contactos/{contacto_id}/verificar",
    response_model=ContactoId,
    summary="Dejar constancia de que el contacto se comprobó; resuelve la observación",
    responses=RESPUESTAS_CAMBIO,
)
def verificar_contacto(
    contacto_id: UUID,
    permiso: Annotated[Permiso, Depends(requiere("contactos.gestionar"))],
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    return _cambiar(
        permiso, idempotencia, motor, contacto_id,
        "select nucleo.verificar_contacto(:k, :a, 'api')", {},
    )  # fmt: skip


@router.post(
    "/consorcios/{consorcio_id}/contactos/{contacto_id}/senalar",
    response_model=ContactoId,
    summary="Señalar un dato incorrecto, con el motivo (sin teléfonos ni correos)",
    responses=RESPUESTAS_CAMBIO,
)
def senalar_contacto(
    contacto_id: UUID,
    senalamiento: Senalamiento,
    permiso: Annotated[Permiso, Depends(requiere("contactos.gestionar"))],
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    return _cambiar(
        permiso, idempotencia, motor, contacto_id,
        "select nucleo.senalar_contacto(:k, :m, :a, 'api')", {"m": senalamiento.motivo},
    )  # fmt: skip
