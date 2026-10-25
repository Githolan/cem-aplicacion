"""Lectura de la auditoría con exposición mínima (RF35-B, 2b-6c).

Diseño: `doc/diseno/H2b_6_Diseno.md` del repositorio CEM, §6.

- `GET …/auditoria/mis-eventos` (`auditoria.mis_eventos`, por ámbito): los eventos que hizo quien
  lee en el consorcio.
- `GET …/auditoria` (`auditoria.ver`, MC y AD): los del consorcio.

La política decide antes de leer; las funciones de `0028` vuelven a exigir el ámbito y la
facultad y arman la proyección (lista blanca de acciones y de campos). Si la base niega lo que el
evaluador concedió, es una discrepancia (500), nunca una lista vacía. Sin exportación (E0).

**Cursor.** Opaco para el cliente: la fecha del último evento de la página y cuántos eventos de
esa misma fecha ya salieron, en base64url. Ningún identificador ni la secuencia. Con la página
llena hay `siguiente`; la última página puede venir vacía.
"""

import base64
import binascii
from datetime import datetime
from typing import Annotated

from cem_contracts.auditoria import (
    LARGO_MAXIMO_CURSOR,
    LIMITE_MAXIMO,
    LIMITE_POR_DEFECTO,
    ActorEvento,
    CambioEvento,
    EventoConsorcio,
    EventoPropio,
    ListaEventosConsorcio,
    ListaEventosPropios,
)
from cem_contracts.errores import CodigoError
from fastapi import APIRouter, Depends, Query

from app.autorizacion.requiere import Permiso, requiere
from app.consorcios.mandatos import _leer
from app.contexto import MotorDep
from app.errores import ErrorApi, sin_cache

router = APIRouter(prefix="/api/v1", tags=["auditoria"], dependencies=[Depends(sin_cache)])

CursorConsulta = Annotated[str | None, Query(max_length=LARGO_MAXIMO_CURSOR)]
LimiteConsulta = Annotated[int, Query(ge=1, le=LIMITE_MAXIMO)]


def _leer_cursor(cursor: str | None) -> tuple[datetime | None, int]:
    if cursor is None:
        return None, 0
    try:
        texto = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("ascii")
        fecha, saltar = texto.split("|")
        antes_de, n = datetime.fromisoformat(fecha), int(saltar)
    except (ValueError, UnicodeError, binascii.Error) as e:
        raise ErrorApi(422, CodigoError.PETICION_INVALIDA, "El cursor no es válido.") from e
    if antes_de.tzinfo is None or n < 1:
        raise ErrorApi(422, CodigoError.PETICION_INVALIDA, "El cursor no es válido.")
    return antes_de, n


def _siguiente(filas: list, limite: int, antes_de: datetime | None, saltar: int) -> str | None:
    if len(filas) < limite:
        return None
    ultima = filas[-1]["fecha"]
    n = sum(f["fecha"] == ultima for f in filas) + (saltar if ultima == antes_de else 0)
    return base64.urlsafe_b64encode(f"{ultima.isoformat()}|{n}".encode("ascii")).decode("ascii")


def _cambios(fila) -> list[CambioEvento]:
    return [CambioEvento(**c) for c in fila["cambios"]]


@router.get(
    "/consorcios/{consorcio_id}/auditoria/mis-eventos",
    response_model=ListaEventosPropios,
    summary="Los eventos propios en el consorcio, con lo mínimo",
    responses={422: {"description": "Cursor o límite inválidos."}},
)
def mis_eventos(
    permiso: Annotated[Permiso, Depends(requiere("auditoria.mis_eventos"))],
    motor: MotorDep,
    cursor: CursorConsulta = None,
    limite: LimiteConsulta = LIMITE_POR_DEFECTO,
) -> ListaEventosPropios:
    antes_de, saltar = _leer_cursor(cursor)
    filas = _leer(
        permiso,
        motor,
        "select fecha, accion, recurso, origen, cambios"
        " from auditoria.eventos_propios(:antes_de, :saltar, :limite)",
        {"antes_de": antes_de, "saltar": saltar, "limite": limite},
    )
    return ListaEventosPropios(
        elementos=[
            EventoPropio(
                fecha=f["fecha"],
                accion=f["accion"],
                recurso=f["recurso"],
                origen=f["origen"],
                cambios=_cambios(f),
            )  # fmt: skip
            for f in filas
        ],
        siguiente=_siguiente(filas, limite, antes_de, saltar),
    )


@router.get(
    "/consorcios/{consorcio_id}/auditoria",
    response_model=ListaEventosConsorcio,
    summary="Los eventos del consorcio, con lo mínimo",
    responses={
        403: {"description": "Sin `auditoria.ver` en el consorcio."},
        422: {"description": "Cursor o límite inválidos."},
    },
)
def eventos_del_consorcio(
    permiso: Annotated[Permiso, Depends(requiere("auditoria.ver"))],
    motor: MotorDep,
    cursor: CursorConsulta = None,
    limite: LimiteConsulta = LIMITE_POR_DEFECTO,
) -> ListaEventosConsorcio:
    antes_de, saltar = _leer_cursor(cursor)
    filas = _leer(
        permiso,
        motor,
        "select fecha, accion, recurso, origen, actor, actor_nombre, cambios"
        " from auditoria.eventos_del_consorcio(:antes_de, :saltar, :limite)",
        {"antes_de": antes_de, "saltar": saltar, "limite": limite},
    )
    return ListaEventosConsorcio(
        elementos=[
            EventoConsorcio(
                fecha=f["fecha"],
                accion=f["accion"],
                recurso=f["recurso"],
                origen=f["origen"],
                actor=ActorEvento(tipo=f["actor"], nombre=f["actor_nombre"]),
                cambios=_cambios(f),
            )  # fmt: skip
            for f in filas
        ],
        siguiente=_siguiente(filas, limite, antes_de, saltar),
    )
