"""Documentos del consorcio: carga a cuarentena (H2c 2c-1; RF24, primera parte).

Diseño: `doc/diseno/H2c_Diseno.md` del repositorio CEM, §2 y §3.

- **`POST …/documentos`** (`documentos.cargar`): crea el documento, sin versiones.
- **`POST …/documentos/{id}/versiones`** (`documentos.cargar`): el archivo crudo en el cuerpo,
  sin multipart (Starlette guarda en disco los multipart de más de 1 MB).
  - Se lee **en tramos con un contador**, a memoria, y corta con 413 al pasar los 20 MB, antes
    de escribir nada.
  - El tipo real sale de los primeros bytes; otro tipo, o uno que no coincide con el declarado,
    da 415 sin escribir nada.
  - **A lo sumo `CARGAS_POR_PROCESO` a la vez** por proceso (`cem-backend` corre 2). Cada carga
    de 20 MB llega a ~40 MB en el pico (el búfer y su copia), así que 4 son ~160 MB de los 512 del
    contenedor. La siguiente espera su turno o recibe 503 a los `ESPERA_TURNO` segundos, y el
    cuerpo entero tiene que llegar en `LECTURA_MAXIMA` segundos (408).
  - El objeto se sube con la clave de documentos (§3.1) en un `put_object` único: la clave no
    tiene `OBJECT_OVERWRITE`, que la carga multiparte de OCI pide. La versión queda
    `en_cuarentena` y se encola `documentos.verificar`, en la misma transacción: si la subida
    falla, no queda la fila.

En este corte no hay lectura ni descarga (2c-2): ninguna versión se sirve todavía.
"""

import hashlib
from typing import Annotated
from uuid import UUID

import anyio
from cem_contracts.documentos import (
    TOPE_BYTES,
    DocumentoId,
    DocumentoNuevo,
    TipoArchivo,
    VersionCargada,
)
from cem_contracts.errores import CodigoError
from fastapi import APIRouter, Depends, Request, Response
from psycopg.errors import InsufficientPrivilege
from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError

from app.autorizacion.evaluador import Entidad
from app.autorizacion.requiere import NO_ENCONTRADO, Permiso, requiere
from app.consorcios.padron import Discrepancia
from app.contexto import MotorDep
from app.errores import ErrorApi, sin_cache
from app.plataforma import almacen, cola
from app.plataforma.idempotencia import (
    ClaveIdempotenciaDep,
    IdempotenciaDep,
    pedido_con_cuerpo,
    responder_una_vez,
)

router = APIRouter(prefix="/api/v1", tags=["documentos"], dependencies=[Depends(sin_cache)])

CARGAS_POR_PROCESO = 2
"""Con `--workers 2` en `backend/arranque.sh`, 4 cargas por contenedor. Si cambia la cantidad de
procesos, cambia esto (§3)."""
ESPERA_TURNO = 30
"""Segundos que una carga espera un turno antes del 503."""
LECTURA_MAXIMA = 120
"""Segundos para recibir el cuerpo entero: un cliente que manda de a un byte no retiene un
turno más que esto (nginx solo corta a los 60 s sin recibir nada)."""
TRABAJO_VERIFICAR = "documentos.verificar"

FIRMAS = (
    (b"%PDF-", TipoArchivo.PDF),
    (b"\xff\xd8\xff", TipoArchivo.JPEG),
    (b"\x89PNG\r\n\x1a\n", TipoArchivo.PNG),
)
"""Los primeros bytes de cada tipo admitido (§3, paso 3)."""

ERRORES = {"no_encontrado": (404, CodigoError.NO_ENCONTRADO, NO_ENCONTRADO)}
"""Motivos de las funciones de `0035`. `origen_invalido` lo fija esta ruta: si llega, es un
defecto (500)."""


def _demasiado_grande() -> ErrorApi:
    return ErrorApi(
        413, CodigoError.ARCHIVO_DEMASIADO_GRANDE, "El archivo pasa de 20 MB: no se guardó."
    )


def _no_admitido() -> ErrorApi:
    return ErrorApi(
        415, CodigoError.TIPO_NO_ADMITIDO, "Solo se aceptan archivos PDF, JPEG o PNG: no se guardó."
    )


_turnos: anyio.Semaphore | None = None


def _semaforo() -> anyio.Semaphore:
    """Uno por proceso, creado dentro de su bucle de eventos."""
    global _turnos
    if _turnos is None:
        _turnos = anyio.Semaphore(CARGAS_POR_PROCESO)
    return _turnos


def tipo_real(cuerpo: bytes) -> TipoArchivo | None:
    return next((t for firma, t in FIRMAS if cuerpo.startswith(firma)), None)


def _escribir(conn: Connection, sql: str, parametros: dict):
    """Ejecuta una función de `0035` y traduce sus motivos a errores de la API."""
    try:
        return conn.execute(text(sql), parametros)
    except DBAPIError as e:
        if isinstance(e.orig, InsufficientPrivilege):
            raise Discrepancia("la base niega documentos.cargar y el evaluador lo concede") from e
        motivo = e.orig.diag.message_hint
        if motivo in ERRORES:
            raise ErrorApi(*ERRORES[motivo]) from e
        raise


@router.post(
    "/consorcios/{consorcio_id}/documentos",
    status_code=201,
    response_model=DocumentoId,
    summary="Crear un documento (sin archivos todavía)",
    responses={403: {"description": "Sin `documentos.cargar` en el consorcio."}},
)
def crear_documento(
    nuevo: DocumentoNuevo,
    permiso: Annotated[Permiso, Depends(requiere("documentos.cargar"))],
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    ctx = permiso.ctx
    permiso.verificar(Entidad())

    def efecto(conn: Connection) -> tuple[int, DocumentoId]:
        nuevo_id = _escribir(
            conn,
            "select nucleo.crear_documento(:cat, :tit, :fecha, :a, 'api')",
            {"cat": nuevo.categoria.value, "tit": nuevo.titulo, "fecha": nuevo.fecha,
             "a": ctx.persona_id},
        ).scalar_one()  # fmt: skip
        return 201, DocumentoId(id=nuevo_id)

    return responder_una_vez(motor, ctx, idempotencia, efecto)


async def _leer_con_tope(request: Request) -> bytes:
    """El cuerpo en tramos, con un contador: corta al pasar el tope, antes de guardar nada."""
    declarado = request.headers.get("content-length")
    if declarado is not None and declarado.isdigit() and int(declarado) > TOPE_BYTES:
        raise _demasiado_grande()
    cuerpo = bytearray()
    try:
        with anyio.fail_after(LECTURA_MAXIMA):
            async for tramo in request.stream():
                cuerpo += tramo
                if len(cuerpo) > TOPE_BYTES:
                    raise _demasiado_grande()
    except TimeoutError:
        raise ErrorApi(
            408,
            CodigoError.PETICION_INVALIDA,
            "El archivo tardó demasiado en llegar: no se guardó.",
        ) from None
    return bytes(cuerpo)


@router.post(
    "/consorcios/{consorcio_id}/documentos/{documento_id}/versiones",
    status_code=201,
    response_model=VersionCargada,
    summary="Subir un archivo de un documento: entra en cuarentena hasta que se analiza",
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                t.value: {"schema": {"type": "string", "format": "binary"}} for t in TipoArchivo
            },
        }
    },
    responses={
        403: {"description": "Sin `documentos.cargar` en el consorcio."},
        404: {"description": "El documento no existe en este consorcio."},
        408: {"description": "El cuerpo no llegó entero a tiempo: no se guardó nada."},
        413: {"description": "El archivo pasa de 20 MB (`archivo_demasiado_grande`)."},
        415: {"description": "No es PDF, JPEG ni PNG, o no coincide con el tipo declarado."},
        503: {"description": "Demasiadas cargas a la vez: probá de nuevo en un momento."},
    },
)
async def subir_version(
    documento_id: UUID,
    request: Request,
    permiso: Annotated[Permiso, Depends(requiere("documentos.cargar"))],
    clave: ClaveIdempotenciaDep,
    motor: MotorDep,
) -> Response:
    ctx = permiso.ctx
    permiso.verificar(Entidad())
    declarado = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if declarado not in {t.value for t in TipoArchivo}:
        raise _no_admitido()
    turnos = _semaforo()
    try:
        with anyio.fail_after(ESPERA_TURNO):
            await turnos.acquire()
    except TimeoutError:
        raise ErrorApi(
            503,
            CodigoError.SERVICIO_NO_DISPONIBLE,
            "Hay muchas cargas en curso. Probá de nuevo en un momento.",
            {"Retry-After": str(ESPERA_TURNO)},
        ) from None
    try:
        cuerpo = await _leer_con_tope(request)
        tipo = tipo_real(cuerpo)
        if tipo is None or tipo.value != declarado:
            raise _no_admitido()
        suma = hashlib.sha256(cuerpo).hexdigest()
        pedido = pedido_con_cuerpo(request, clave, cuerpo)
        destino = almacen.AlmacenDocumentos.desde_entorno(almacen.DOCUMENTOS)

        def efecto(conn: Connection) -> tuple[int, VersionCargada]:
            fila = _escribir(
                conn,
                "select id, clave_objeto from nucleo.nueva_version(:d, :t, :n, :s, :a, 'api')",
                {"d": documento_id, "t": tipo.value, "n": len(cuerpo), "s": suma,
                 "a": ctx.persona_id},
            ).one()  # fmt: skip
            subida = destino.cliente().put_object(
                Bucket=destino.bucket, Key=fila.clave_objeto, Body=cuerpo,
                ContentType=tipo.value, Metadata={"sha256": suma},
            )  # fmt: skip
            # Un bucket con versiones (producción) devuelve el `VersionId` con el que el análisis
            # borra un rechazado; uno sin versiones (staging) no devuelve ninguno.
            if subida.get("VersionId"):
                _escribir(
                    conn,
                    "select nucleo.fijar_version_objeto(:v, :vo, :a, 'api')",
                    {"v": fila.id, "vo": subida["VersionId"], "a": ctx.persona_id},
                )
            cola.encolar(
                conn,
                TRABAJO_VERIFICAR,
                carga={"version": str(fila.id)},
                consorcio_id=ctx.consorcio_id,
                clave_unica=f"{TRABAJO_VERIFICAR}:{fila.id}",
            )
            return 201, VersionCargada(
                id=fila.id, estado="en_cuarentena", tipo=tipo, tamano=len(cuerpo), sha256=suma
            )

        return await anyio.to_thread.run_sync(lambda: responder_una_vez(motor, ctx, pedido, efecto))
    finally:
        turnos.release()
