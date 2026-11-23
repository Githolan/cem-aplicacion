"""Pagos informados por los vecinos, primer corte (PI-S1; `0129`).

Diseño «Pagos informados» del repositorio CEM (reglas PI-1, PI-2, PI-3 y PI-6). Todas las rutas
cuelgan de `…/pagos-informados`:

- **`POST …/comprobantes?unidad_id=…`** (`pagos_informados.informar`): el comprobante (el cuerpo
  crudo, como en Documentos), que entra en cuarentena hasta que el antivirus lo acepta.
- **`POST …/pagos-informados`** (`pagos_informados.informar`, con `Idempotency-Key`): informa el
  pago de una unidad. Un solo registro por pago (PA07): si ya estaba informado, 200 con el
  existente y `ya_informado`; si no, 201.
- **`GET …/pagos-informados`** (`pagos_informados.ver`): los que la persona puede ver; la base
  filtra por unidad salvo para quien tiene `fiscalizacion.ver_panel`.
- **`GET …/{p}/comprobante`** (`pagos_informados.ver`): el archivo, si está disponible.

La política decide al entrar o apenas se conoce la unidad; la base repite la barrera en cada
función. Si la base niega lo que el evaluador concedió, es una discrepancia (500).
"""

from typing import Annotated
from uuid import UUID

import anyio
from botocore.exceptions import ClientError
from cem_contracts.documentos import EstadoVersion, TipoArchivo, VersionCargada
from cem_contracts.errores import CodigoError
from cem_contracts.pagos_informados import (
    LARGO_NUMERO_OPERACION,
    ComprobantePagoSubido,
    ListaPagosInformados,
    PagoInformado,
    PagoInformadoNuevo,
    PagoInformadoRespuesta,
)
from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import StreamingResponse
from psycopg.errors import InsufficientPrivilege
from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError

from app.autorizacion.requiere import NO_ENCONTRADO, VOLVER_A_ENTRAR, Permiso, requiere
from app.consorcios.documentos import (
    CABECERAS_CONTENIDO,
    RESPUESTAS_SUBIDA,
    SUBIDA_OPENAPI,
    _objeto,
    _tramos,
    _vencido,
    subir_archivo,
)
from app.consorcios.padron import Discrepancia, sobre_unidad
from app.contexto import MotorDep
from app.db import transaccion
from app.errores import ErrorApi, detalle_de_campo, sin_cache
from app.plataforma import almacen
from app.plataforma.idempotencia import ClaveIdempotenciaDep, IdempotenciaDep, responder_una_vez

router = APIRouter(prefix="/api/v1", tags=["consorcios"], dependencies=[Depends(sin_cache)])

INFORMAR = "pagos_informados.informar"
VER = "pagos_informados.ver"
BASE = "/consorcios/{consorcio_id}/pagos-informados"

ERRORES = {
    "fecha_futura": (
        422, CodigoError.FECHA_FUTURA, "La fecha del pago no puede ser posterior a hoy.",
    ),
    "texto_con_cuenta": (
        422, CodigoError.TEXTO_CON_CUENTA,
        "El número de operación no puede ser el número de una cuenta.",
    ),
    "comprobante_invalido": (
        422, CodigoError.COMPROBANTE_INVALIDO,
        "El comprobante no sirve: no lo subió esta cuenta para esa unidad, lo quitaron o el"
        " antivirus lo rechazó. Hay que subirlo de nuevo.",
    ),
    "comprobante_de_otra_unidad": (
        409, CodigoError.COMPROBANTE_DE_OTRA_UNIDAD,
        "Ese comprobante ya se informó como el pago de otra unidad.",
    ),
    "sesion_invalida": (403, *VOLVER_A_ENTRAR),
}  # fmt: skip
"""Motivos de las funciones de `0129` que la persona puede provocar. Los demás (`modo_no_admitido`,
`origen_invalido`) son un defecto si llegan: el evaluador o la ruta ya los impiden."""
CAMPO_DEL_MOTIVO = {
    "fecha_futura": "fecha_pago",
    "texto_con_cuenta": "numero_operacion",
    "comprobante_invalido": "comprobante_version_id",
    "comprobante_de_otra_unidad": "comprobante_version_id",
}
"""El campo del cuerpo que motiva el error, para que la pantalla lo marque."""

RESPUESTAS = {
    403: {"description": "Sin un vínculo confirmado con la unidad que cuente."},
    404: {"description": "Sin vínculo ni mandato en el consorcio."},
}
LEER = "select * from nucleo.pagos_informados_lectura(:actor, 'api', :sesion)"
INFORMAR_SQL = (
    "select pago_id, ya_informado from nucleo.informar_pago(:u, :importe, :fecha, :medio,"
    " :numero, :comprobante, :actor, 'api', :sesion)"
)


def ejecutar(conn: Connection, permiso: Permiso, sql: str, parametros: dict):
    """Llama a una función de `0129` con la persona y la sesión, y traduce sus motivos."""
    ctx = permiso.ctx
    try:
        return conn.execute(
            text(sql), parametros | {"actor": ctx.persona_id, "sesion": ctx.sesion_id}
        )
    except DBAPIError as e:
        if isinstance(e.orig, InsufficientPrivilege):
            raise Discrepancia(f"la base niega {permiso.accion} y el evaluador lo concede") from e
        motivo = e.orig.diag.message_hint
        if motivo in ERRORES:
            raise ErrorApi(
                *ERRORES[motivo], detalle=detalle_de_campo(CAMPO_DEL_MOTIVO.get(motivo))
            ) from e
        raise


def leer(conn: Connection, permiso: Permiso) -> list[PagoInformado]:
    return [PagoInformado(**f) for f in ejecutar(conn, permiso, LEER, {}).mappings()]


VerDep = Annotated[Permiso, Depends(requiere(VER, al_entrar=True))]
InformarDep = Annotated[Permiso, Depends(requiere(INFORMAR))]


@router.get(
    BASE,
    response_model=ListaPagosInformados,
    summary="Los pagos informados que la persona puede ver, del más nuevo al más viejo",
    responses=RESPUESTAS,
)
def listar_pagos_informados(ver: VerDep, motor: MotorDep) -> ListaPagosInformados:
    ctx = ver.ctx
    with transaccion(motor, persona_id=ctx.persona_id, consorcio_id=ctx.consorcio_id) as c:
        elementos = leer(c, ver)
    return ListaPagosInformados(elementos=elementos, largo_numero_operacion=LARGO_NUMERO_OPERACION)


@router.post(
    BASE,
    status_code=201,
    response_model=PagoInformadoRespuesta,
    summary="Informar el pago de una unidad, con su comprobante; si ese pago ya estaba informado,"
    " responde el existente",
    responses={
        200: {
            "description": "Ese pago ya estaba informado (la misma unidad y el mismo número de"
            " operación, o el mismo archivo): el existente, con `ya_informado` (PA07).",
            "model": PagoInformadoRespuesta,
        },
        **RESPUESTAS,
        409: {"description": "El comprobante ya se informó para otra unidad."},
        422: {"description": "La fecha es posterior a hoy, el número es una cuenta o el"
              " comprobante no sirve."},
    },
)  # fmt: skip
def informar_pago(
    cuerpo: PagoInformadoNuevo,
    informar: InformarDep,
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    informar.verificar(sobre_unidad(cuerpo.unidad_id))

    def efecto(conn: Connection) -> tuple[int, PagoInformadoRespuesta]:
        fila = ejecutar(
            conn, informar, INFORMAR_SQL,
            {"u": cuerpo.unidad_id, "importe": cuerpo.importe, "fecha": cuerpo.fecha_pago,
             "medio": cuerpo.medio.value, "numero": cuerpo.numero_operacion,
             "comprobante": cuerpo.comprobante_version_id},
        ).one()  # fmt: skip
        [pago] = [p for p in leer(conn, informar) if p.id == fila.pago_id]
        respuesta = PagoInformadoRespuesta(**pago.model_dump(), ya_informado=fila.ya_informado)
        return (200 if fila.ya_informado else 201), respuesta

    return responder_una_vez(motor, informar.ctx, idempotencia, efecto)


@router.post(
    BASE + "/comprobantes",
    status_code=201,
    response_model=ComprobantePagoSubido,
    summary="Subir el comprobante de un pago de una unidad: entra en cuarentena hasta que el"
    " antivirus lo acepta",
    openapi_extra=SUBIDA_OPENAPI,
    responses=RESPUESTAS | RESPUESTAS_SUBIDA,
)
async def subir_comprobante(
    unidad_id: Annotated[UUID, Query(description="La unidad cuyo pago se va a informar.")],
    request: Request,
    informar: InformarDep,
    clave: ClaveIdempotenciaDep,
    motor: MotorDep,
) -> Response:
    await anyio.to_thread.run_sync(lambda: informar.verificar(sobre_unidad(unidad_id)))

    def nueva(conn: Connection, tipo_archivo: str, tamano: int, suma: str):
        return ejecutar(
            conn, informar,
            "select nueva_version_id as id, nueva_clave_objeto as clave_objeto,"
            " nuevo_documento_id as documento_id"
            " from nucleo.subir_comprobante_pago(:u, :t, :n, :suma, :actor, 'api', :sesion)",
            {"u": unidad_id, "t": tipo_archivo, "n": tamano, "suma": suma},
        ).one()  # fmt: skip

    def respuesta(fila, cargada: VersionCargada) -> ComprobantePagoSubido:
        return ComprobantePagoSubido(documento_id=fila.documento_id, archivo=cargada)

    return await subir_archivo(request, informar.ctx, clave, motor, nueva, respuesta)


@router.get(
    BASE + "/{pago_id}/comprobante",
    response_class=StreamingResponse,
    summary="Ver el comprobante de un pago informado: por el mismo origen, si el antivirus lo"
    " aceptó",
    responses={
        200: {
            "description": "El archivo, con `inline`, `nosniff` y una CSP que no deja ejecutar"
            " nada.",
            "content": {
                t.value: {"schema": {"type": "string", "format": "binary"}} for t in TipoArchivo
            },
        },
        **RESPUESTAS,
        409: {"description": "Lo subió quien pregunta y todavía se revisa o fue rechazado."},
        410: {"description": "El archivo ya no está en el almacén (staging, a los 30 días)."},
    },
)
def contenido_del_comprobante(pago_id: UUID, ver: VerDep, motor: MotorDep) -> StreamingResponse:
    ctx = ver.ctx
    with transaccion(motor, persona_id=ctx.persona_id, consorcio_id=ctx.consorcio_id) as c:
        filas = ejecutar(
            c, ver,
            "select * from nucleo.comprobante_pago_para_ver(:p, :actor, 'api', :sesion)",
            {"p": pago_id},
        ).mappings().all()  # fmt: skip
    if not filas:
        raise ErrorApi(404, CodigoError.NO_ENCONTRADO, NO_ENCONTRADO)
    f = filas[0]
    if f["estado"] != EstadoVersion.DISPONIBLE:
        raise ErrorApi(
            409,
            CodigoError.VERSION_NO_DISPONIBLE,
            "Este archivo todavía se está revisando o fue rechazado: no se puede ver.",
        )
    origen = almacen.AlmacenDocumentos.desde_entorno(almacen.DOCUMENTOS)
    try:
        leido = origen.cliente().get_object(**_objeto(origen.bucket, f))
    except ClientError as e:
        raise _vencido(e) from e
    return StreamingResponse(
        _tramos(leido["Body"]),
        media_type=TipoArchivo(f["tipo"]).value,
        headers=CABECERAS_CONTENIDO | {"Content-Length": str(leido["ContentLength"])},
    )
