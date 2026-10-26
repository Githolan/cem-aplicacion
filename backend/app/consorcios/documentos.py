"""Documentos del consorcio: carga a cuarentena, lista y descarga (H2c 2c-1 y 2c-2; RF24).

Diseño: `doc/diseno/H2c_Diseno.md` del repositorio CEM, §2, §3 y §4. Cada categoría tiene su
acción de lectura y sus acciones de carga (`ACCION_DE_LECTURA`, `ACCIONES_DE_CARGA` del
contrato): la categoría elige la acción.

- **`POST …/documentos`** (`documentos.cargar`, o `documentos.cargar_operativos` para actas,
  presupuestos y mantenimiento): crea el documento, sin versiones.
- **`POST …/documentos/{id}/versiones`** (la carga de la categoría del documento): el archivo
  crudo en el cuerpo, sin multipart (Starlette guarda en disco los multipart de más de 1 MB).
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

- **`GET …/documentos`** (`documentos.ver`): los vigentes de las categorías que la persona puede
  ver. De las versiones, las `disponible`; las otras, solo las que subió ella.
- **`GET …/documentos/{id}/versiones/{v}/descargar`** (la lectura de la categoría): 302 a una URL
  firmada de `VIGENCIA_DESCARGA` segundos, solo si la versión está `disponible` (documento 13
  §9.2). Nunca hay una URL permanente. Si el objeto ya no está en el almacén (staging lo borra a
  los 30 días), 410.
"""

import hashlib
import re
from typing import Annotated
from urllib.parse import quote
from uuid import UUID

import anyio
from botocore.exceptions import ClientError
from cem_contracts.documentos import (
    ACCION_DE_LECTURA,
    ACCIONES_DE_CARGA,
    TOPE_BYTES,
    CategoriaDocumento,
    DocumentoEnLista,
    DocumentoId,
    DocumentoNuevo,
    EstadoVersion,
    ListaDocumentos,
    TipoArchivo,
    VersionCargada,
    VersionEnLista,
)
from cem_contracts.errores import CodigoError
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse
from psycopg.errors import InsufficientPrivilege
from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError

from app.autorizacion.evaluador import Entidad
from app.autorizacion.requiere import NO_ENCONTRADO, Permiso, requiere
from app.consorcios.padron import Discrepancia
from app.contexto import MotorDep
from app.db import transaccion
from app.errores import SIN_CACHE, ErrorApi, sin_cache
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
INTENTOS_VERIFICAR = 10
"""Se guarda con el trabajo al encolarlo: el proceso de la API no importa el registro de los
trabajos, y sin esto `encolar` pondría el valor por omisión (3)."""

FIRMAS = (
    (b"%PDF-", TipoArchivo.PDF),
    (b"\xff\xd8\xff", TipoArchivo.JPEG),
    (b"\x89PNG\r\n\x1a\n", TipoArchivo.PNG),
)
"""Los primeros bytes de cada tipo admitido (§3, paso 3)."""

VIGENCIA_DESCARGA = 300
"""Segundos que vale la URL firmada de una descarga (documento 13 §9.2: 5 minutos)."""
EXTENSIONES = {TipoArchivo.PDF: ".pdf", TipoArchivo.JPEG: ".jpg", TipoArchivo.PNG: ".png"}

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


def _verificar_carga(
    cargar: Permiso, operativos: Permiso, categoria: CategoriaDocumento | None
) -> None:
    """Concede si alguna acción de carga de la categoría lo permite; si no, niega y audita con
    `documentos.cargar`. Sin categoría (todavía no se leyó la del documento), con cualquiera de
    las dos."""
    acciones = ACCIONES_DE_CARGA[categoria] if categoria else (cargar.accion, operativos.accion)
    permisos = [p for p in (cargar, operativos) if p.accion in acciones]
    if not any(p.decidir(Entidad()).permitido for p in permisos):
        cargar.verificar(Entidad())  # niega, audita y responde 403
        raise Discrepancia("el evaluador niega y después concede la carga")


def _categoria_para_cargar(motor, ctx, documento_id: UUID) -> CategoriaDocumento:
    """La categoría del documento, para elegir la acción antes de leer el archivo; 404 si no
    existe o está borrado."""
    with transaccion(motor, persona_id=ctx.persona_id, consorcio_id=ctx.consorcio_id) as conn:
        try:
            categoria = conn.execute(
                text("select nucleo.categoria_para_cargar(:d)"), {"d": documento_id}
            ).scalar_one()
        except DBAPIError as e:
            if isinstance(e.orig, InsufficientPrivilege):
                raise Discrepancia("la base niega la carga y el evaluador la concede") from e
            raise
    if categoria is None:
        raise ErrorApi(404, CodigoError.NO_ENCONTRADO, NO_ENCONTRADO)
    return CategoriaDocumento(categoria)


def _escribir(conn: Connection, sql: str, parametros: dict):
    """Ejecuta una función de `0035`/`0036` y traduce sus motivos a errores de la API."""
    try:
        return conn.execute(text(sql), parametros)
    except DBAPIError as e:
        if isinstance(e.orig, InsufficientPrivilege):
            raise Discrepancia("la base niega la carga y el evaluador la concede") from e
        motivo = e.orig.diag.message_hint
        if motivo in ERRORES:
            raise ErrorApi(*ERRORES[motivo]) from e
        raise


@router.post(
    "/consorcios/{consorcio_id}/documentos",
    status_code=201,
    response_model=DocumentoId,
    summary="Crear un documento (sin archivos todavía)",
    responses={
        403: {
            "description": "Sin `documentos.cargar` en el consorcio, ni"
            " `documentos.cargar_operativos` para una categoría operativa."
        }
    },
)
def crear_documento(
    nuevo: DocumentoNuevo,
    cargar: Annotated[Permiso, Depends(requiere("documentos.cargar"))],
    operativos: Annotated[Permiso, Depends(requiere("documentos.cargar_operativos"))],
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    ctx = cargar.ctx
    _verificar_carga(cargar, operativos, nuevo.categoria)

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
        403: {"description": "Sin una acción de carga para la categoría del documento."},
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
    cargar: Annotated[Permiso, Depends(requiere("documentos.cargar"))],
    operativos: Annotated[Permiso, Depends(requiere("documentos.cargar_operativos"))],
    clave: ClaveIdempotenciaDep,
    motor: MotorDep,
) -> Response:
    ctx = cargar.ctx
    _verificar_carga(cargar, operativos, None)
    categoria = _categoria_para_cargar(motor, ctx, documento_id)
    _verificar_carga(cargar, operativos, categoria)
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
                maximo_intentos=INTENTOS_VERIFICAR,
            )
            return 201, VersionCargada(
                id=fila.id, estado="en_cuarentena", tipo=tipo, tamano=len(cuerpo), sha256=suma
            )

        return await anyio.to_thread.run_sync(lambda: responder_una_vez(motor, ctx, pedido, efecto))
    finally:
        turnos.release()


def _leer(conn: Connection, sql: str, parametros: dict, ver: Permiso):
    """Una lectura de `0036`. Si la base la niega y el evaluador también, 403 auditado; si el
    evaluador la concede, es una discrepancia."""
    try:
        return conn.execute(text(sql), parametros).mappings().all()
    except DBAPIError as e:
        if not isinstance(e.orig, InsufficientPrivilege):
            raise
        if ver.decidir(Entidad()).permitido:
            raise Discrepancia("la base niega leer documentos y el evaluador lo concede") from e
        ver.verificar(Entidad())  # niega, audita y responde 403
        raise Discrepancia("el evaluador niega y después concede la lectura") from e


@router.get(
    "/consorcios/{consorcio_id}/documentos",
    response_model=ListaDocumentos,
    summary="Los documentos vigentes que la persona puede ver, con sus versiones",
    responses={403: {"description": "Sin `documentos.ver` en el consorcio (A6: ni I ni O)."}},
)
def listar_documentos(
    ver: Annotated[Permiso, Depends(requiere("documentos.ver"))],
    restringidos: Annotated[Permiso, Depends(requiere("documentos.ver_restringidos"))],
    motor: MotorDep,
) -> ListaDocumentos:
    ctx = ver.ctx
    ver.verificar(Entidad())
    puede = {
        ver.accion: True,
        restringidos.accion: restringidos.decidir(Entidad()).permitido,
    }
    with transaccion(motor, persona_id=ctx.persona_id, consorcio_id=ctx.consorcio_id) as conn:
        filas = _leer(
            conn,
            "select documento_id, categoria, titulo, fecha, cargado_en, version_id, estado,"
            " motivo_rechazo, tipo, tamano, sha256, subida_en"
            " from nucleo.documentos_del_consorcio()",
            {},
            ver,
        )
    documentos: dict[UUID, DocumentoEnLista] = {}
    for f in filas:
        categoria = CategoriaDocumento(f["categoria"])
        # La base filtra por el vínculo o la facultad; el modo y el recurso, el evaluador.
        if not puede[ACCION_DE_LECTURA[categoria]]:
            continue
        d = documentos.get(f["documento_id"])
        if d is None:
            d = documentos[f["documento_id"]] = DocumentoEnLista(
                id=f["documento_id"], categoria=categoria, titulo=f["titulo"],
                fecha=f["fecha"], cargado_en=f["cargado_en"], versiones=[],
            )  # fmt: skip
        if f["version_id"] is not None:
            d.versiones.append(
                VersionEnLista(
                    id=f["version_id"], estado=f["estado"], motivo_rechazo=f["motivo_rechazo"],
                    tipo=f["tipo"], tamano=f["tamano"], sha256=f["sha256"],
                    subida_en=f["subida_en"],
                )  # fmt: skip
            )
    return ListaDocumentos(documentos=list(documentos.values()))


def _disposicion(titulo: str, tipo: TipoArchivo) -> str:
    """`attachment` con el título como nombre: una versión ASCII y la original en UTF-8
    (RFC 6266)."""
    nombre = titulo + EXTENSIONES[tipo]
    ascii_ = re.sub(r"[^A-Za-z0-9 ._-]", "_", nombre)
    return f"attachment; filename=\"{ascii_}\"; filename*=UTF-8''{quote(nombre, safe='')}"


@router.get(
    "/consorcios/{consorcio_id}/documentos/{documento_id}/versiones/{version_id}/descargar",
    status_code=302,
    response_class=RedirectResponse,
    summary="Descargar una versión disponible: 302 a una URL firmada de 5 minutos",
    responses={
        302: {"description": "`Location` es la URL firmada del almacén; vence a los 5 minutos."},
        403: {"description": "Sin `documentos.ver` en el consorcio."},
        404: {
            "description": "La versión o el documento no existen en este consorcio, o su"
            " categoría es restringida y la persona no tiene la facultad `documentos.ver`."
        },
        409: {"description": "La versión está en cuarentena o fue rechazada."},
        410: {"description": "El archivo ya no está en el almacén (staging, a los 30 días)."},
    },
)
def descargar_version(
    documento_id: UUID,
    version_id: UUID,
    ver: Annotated[Permiso, Depends(requiere("documentos.ver"))],
    restringidos: Annotated[Permiso, Depends(requiere("documentos.ver_restringidos"))],
    motor: MotorDep,
) -> RedirectResponse:
    ctx = ver.ctx
    ver.verificar(Entidad())
    with transaccion(motor, persona_id=ctx.persona_id, consorcio_id=ctx.consorcio_id) as conn:
        try:
            filas = conn.execute(
                text(
                    "select categoria, estado, tipo, clave_objeto, version_objeto, titulo"
                    " from nucleo.version_para_descargar(:d, :v)"
                ),
                {"d": documento_id, "v": version_id},
            ).mappings().all()  # fmt: skip
        except DBAPIError as e:
            if not isinstance(e.orig, InsufficientPrivilege):
                raise
            # La base niega la categoría restringida: el evaluador decide y, para no revelar
            # que el documento existe, responde 404.
            if restringidos.decidir(Entidad()).permitido:
                raise Discrepancia(
                    "la base niega una categoría restringida y el evaluador la concede"
                ) from e
            restringidos.verificar(Entidad(), estado_negado=404)
            raise Discrepancia("el evaluador niega y después concede la lectura") from e
    if not filas:
        raise ErrorApi(404, CodigoError.NO_ENCONTRADO, NO_ENCONTRADO)
    f = filas[0]
    if ACCION_DE_LECTURA[CategoriaDocumento(f["categoria"])] == restringidos.accion:
        restringidos.verificar(Entidad(), estado_negado=404)
    if f["estado"] != EstadoVersion.DISPONIBLE:
        raise ErrorApi(
            409,
            CodigoError.VERSION_NO_DISPONIBLE,
            "Este archivo todavía se está revisando o fue rechazado: no se puede descargar.",
        )
    origen = almacen.AlmacenDocumentos.desde_entorno(almacen.DOCUMENTOS)
    s3 = origen.cliente()
    objeto = {"Bucket": origen.bucket, "Key": f["clave_objeto"]}
    if f["version_objeto"]:
        objeto["VersionId"] = f["version_objeto"]
    try:
        s3.head_object(**objeto)
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") not in ("404", "NoSuchKey", "NoSuchVersion"):
            raise
        raise ErrorApi(
            410, CodigoError.ARCHIVO_VENCIDO, "Este archivo ya no está disponible."
        ) from e
    tipo = TipoArchivo(f["tipo"])
    url = s3.generate_presigned_url(
        "get_object",
        Params=objeto | {"ResponseContentType": tipo.value,
                         "ResponseContentDisposition": _disposicion(f["titulo"], tipo)},
        ExpiresIn=VIGENCIA_DESCARGA,
    )  # fmt: skip
    return RedirectResponse(url, status_code=302, headers=SIN_CACHE)
