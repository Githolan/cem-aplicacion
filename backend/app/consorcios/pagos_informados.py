"""Pagos informados por los vecinos, cortes PI-S1 (`0129`) y PI-S2 (`0130`).

Diseño «Pagos informados» del repositorio CEM (reglas PI-1 a PI-6 y PI-9). Todas las rutas
cuelgan de `…/pagos-informados`:

- **`POST …/comprobantes?unidad_id=…`** (`pagos_informados.informar`): el comprobante (el cuerpo
  crudo, como en Documentos), que entra en cuarentena hasta que el antivirus lo acepta.
- **`POST …/pagos-informados`** (`pagos_informados.informar`, con `Idempotency-Key`): informa el
  pago de una unidad. Un solo registro por pago (PA07): si ya estaba informado, 200 con el
  existente y `ya_informado`; si no, 201. Con `corrige_a`, corrige un rechazado o anulado (PI-3).
- **`GET …/pagos-informados`** (`pagos_informados.ver`): los que la persona puede ver; la base
  filtra por unidad salvo para quien tiene `fiscalizacion.ver_panel`. Cada pago trae su historia y
  `acciones` (lo que quien lee puede hacer ahora, C9); la lista trae `por_confirmar`, el número de
  la bandeja del Consejo.
- **`GET …/{p}/comprobante`** (`pagos_informados.ver`): el archivo, si está disponible.
- **`POST …/{p}/confirmacion`**, **`POST …/{p}/rechazo`** y **`POST …/{p}/anulacion`**
  (`pagos_informados.confirmar`, con `Idempotency-Key`; PI-4, PI-5, PI-9): confirmar un pago
  `informado` (con el comprobante ya aceptado), rechazarlo con motivo, o anular uno `confirmado`
  con motivo. Nadie decide sobre un pago que informó él mismo (`no_es_quien_informo`).

La política decide al entrar o apenas se conoce la unidad; la base repite la barrera en cada
función. Si la base niega lo que el evaluador concedió, es una discrepancia (500).
"""

from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

import anyio
from botocore.exceptions import ClientError
from cem_contracts.documentos import EstadoVersion, TipoArchivo, VersionCargada
from cem_contracts.errores import CodigoError
from cem_contracts.pagos_informados import (
    LARGO_MOTIVO,
    LARGO_NUMERO_OPERACION,
    AccionPagoInformado,
    ComprobantePagoSubido,
    EstadoPagoInformado,
    ListaPagosInformados,
    MotivoPagoInformado,
    PagoInformado,
    PagoInformadoNuevo,
    PagoInformadoRespuesta,
    PasoPagoInformado,
)
from cem_contracts.politicas import CodigoSeparacion
from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import StreamingResponse
from psycopg.errors import InsufficientPrivilege
from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError

from app.autorizacion.evaluador import Entidad
from app.autorizacion.requiere import (
    NO_ENCONTRADO,
    VOLVER_A_ENTRAR,
    YA_PARTICIPO,
    Permiso,
    requiere,
)
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
CONFIRMAR = "pagos_informados.confirmar"
PANEL = "fiscalizacion.ver_panel"
"""Decidir exige también ver el panel (PI-D14), como la base (`exigir_decidir_pago_informado`):
quien tiene solo la facultad de confirmar y un vínculo ve los pagos de su unidad, pero no los
decide ni se los cuenta `por_confirmar` (observación de Codex en #290)."""
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
    "correccion_de_pago_invalida": (
        422, CodigoError.CORRECCION_DE_PAGO_INVALIDA,
        "Solo se corrige un pago rechazado o anulado de la misma unidad.",
    ),
    "pago_ya_corregido": (
        409, CodigoError.PAGO_YA_CORREGIDO,
        "Ese pago ya tiene su corrección: hay que volver a abrir la lista para verla.",
    ),
    "pago_informado_decidido": (
        409, CodigoError.PAGO_INFORMADO_DECIDIDO,
        "El pago ya no admite ese paso: otra persona lo confirmó, lo rechazó o lo anuló. Hay que"
        " volver a abrir la lista para ver cómo quedó.",
    ),
    "comprobante_en_revision": (
        409, CodigoError.COMPROBANTE_EN_REVISION,
        "El antivirus todavía revisa el comprobante: el pago se confirma cuando lo acepte.",
    ),
    "comprobante_rechazado": (
        409, CodigoError.COMPROBANTE_RECHAZADO,
        "El antivirus rechazó el comprobante: el pago no se confirma. Se puede rechazar para que"
        " quien lo informó lo corrija con otro archivo.",
    ),
    "motivo_requerido": (422, CodigoError.MOTIVO_REQUERIDO, "Hay que escribir el motivo."),
    "motivo_con_cuenta": (
        422, CodigoError.MOTIVO_CON_CUENTA,
        "El motivo no puede llevar el número de una cuenta: hay que sacarlo.",
    ),
    "no_es_quien_informo": (
        403, CodigoError.YA_PARTICIPO,
        YA_PARTICIPO[(CONFIRMAR, CodigoSeparacion.NO_ES_QUIEN_INFORMO)],
    ),
    "no_encontrado": (404, CodigoError.NO_ENCONTRADO, NO_ENCONTRADO),
    "sesion_invalida": (403, *VOLVER_A_ENTRAR),
}  # fmt: skip
"""Motivos de las funciones de `0129` y `0130` que la persona puede provocar. Los demás
(`modo_no_admitido`, `origen_invalido`) son un defecto si llegan: el evaluador o la ruta ya los
impiden. `no_es_quien_informo` lo frena antes el evaluador; la base lo repite."""
CAMPO_DEL_MOTIVO = {
    "fecha_futura": "fecha_pago",
    "texto_con_cuenta": "numero_operacion",
    "comprobante_invalido": "comprobante_version_id",
    "comprobante_de_otra_unidad": "comprobante_version_id",
    "correccion_de_pago_invalida": "corrige_a",
    "pago_ya_corregido": "corrige_a",
    "motivo_requerido": "motivo",
    "motivo_con_cuenta": "motivo",
}
"""El campo del cuerpo que motiva el error, para que la pantalla lo marque."""

RESPUESTAS = {
    403: {"description": "Sin un vínculo confirmado con la unidad que cuente."},
    404: {"description": "Sin vínculo ni mandato en el consorcio."},
}
LEER = "select * from nucleo.pagos_informados_lectura(:actor, 'api', :sesion)"
INFORMAR_SQL = (
    "select pago_id, ya_informado from nucleo.informar_pago(:u, :importe, :fecha, :medio,"
    " :numero, :comprobante, :corrige, :actor, 'api', :sesion)"
)
INTERNAS = ("informado_por", "informo_ayudando")
"""Columnas de la lectura que solo usa la API (la separación de PI-5): no se muestran."""


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


@dataclass(frozen=True)
class Permisos:
    """Lo que la persona puede hacer con los pagos informados; `ver`, que se verifica al entrar,
    da el contexto. Para decidir qué ofrecer: cada acto vuelve a verificar su acción."""

    ver: Permiso
    informar: Permiso
    confirmar: Permiso
    panel: Permiso


def _permisos(
    ver: Annotated[Permiso, Depends(requiere(VER, al_entrar=True))],
    informar: Annotated[Permiso, Depends(requiere(INFORMAR))],
    confirmar: Annotated[Permiso, Depends(requiere(CONFIRMAR))],
    panel: Annotated[Permiso, Depends(requiere(PANEL))],
) -> Permisos:
    return Permisos(ver, informar, confirmar, panel)


PermisosDep = Annotated[Permisos, Depends(_permisos)]


def de_quien_informo(f) -> Entidad:
    """La entidad de la separación PI-5: quien informó y, si informó ayudando, quien ayudó."""
    return Entidad(informado_por=tuple(p for p in (f["informado_por"], f["informo_ayudando"]) if p))


def _acciones(f, permisos: Permisos) -> list[AccionPagoInformado]:
    estado = EstadoPagoInformado(f["estado"])
    if estado in (EstadoPagoInformado.RECHAZADO, EstadoPagoInformado.ANULADO):
        if (
            f["corregido_por"] is None
            and permisos.informar.decidir(sobre_unidad(f["unidad_id"])).permitido
        ):
            return [AccionPagoInformado.CORREGIR]
        return []
    if not (
        permisos.panel.decidir().permitido
        and permisos.confirmar.decidir(de_quien_informo(f)).permitido
    ):
        return []
    if estado is EstadoPagoInformado.CONFIRMADO:
        return [AccionPagoInformado.ANULAR]
    acciones = []
    if f["comprobante_version_id"] is None or f["comprobante_estado"] == EstadoVersion.DISPONIBLE:
        acciones.append(AccionPagoInformado.CONFIRMAR)
    acciones.append(AccionPagoInformado.RECHAZAR)
    return acciones


def _pago(f, permisos: Permisos) -> PagoInformado:
    datos = {k: v for k, v in f.items() if k not in INTERNAS and k != "historia"}
    return PagoInformado(
        **datos,
        historia=[PasoPagoInformado(**p) for p in f["historia"]],
        acciones=_acciones(f, permisos),
    )


def filas(conn: Connection, permisos: Permisos, pago: UUID | None = None) -> list:
    sql = LEER if pago is None else LEER + " where id = :p"
    return ejecutar(conn, permisos.ver, sql, {"p": pago}).mappings().all()


def leer(conn: Connection, permisos: Permisos) -> list[PagoInformado]:
    return [_pago(f, permisos) for f in filas(conn, permisos)]


def _fila(conn: Connection, permisos: Permisos, pago: UUID):
    encontradas = filas(conn, permisos, pago)
    if not encontradas:
        raise ErrorApi(404, CodigoError.NO_ENCONTRADO, NO_ENCONTRADO)
    return encontradas[0]


VerDep = Annotated[Permiso, Depends(requiere(VER, al_entrar=True))]
InformarDep = Annotated[Permiso, Depends(requiere(INFORMAR))]


@router.get(
    BASE,
    response_model=ListaPagosInformados,
    summary="Los pagos informados que la persona puede ver, del más nuevo al más viejo",
    responses=RESPUESTAS,
)
def listar_pagos_informados(permisos: PermisosDep, motor: MotorDep) -> ListaPagosInformados:
    ctx = permisos.ver.ctx
    with transaccion(motor, persona_id=ctx.persona_id, consorcio_id=ctx.consorcio_id) as c:
        elementos = leer(c, permisos)
    decidibles = {AccionPagoInformado.CONFIRMAR, AccionPagoInformado.RECHAZAR}
    return ListaPagosInformados(
        elementos=elementos,
        largo_numero_operacion=LARGO_NUMERO_OPERACION,
        largo_motivo=LARGO_MOTIVO,
        por_confirmar=sum(1 for p in elementos if decidibles & set(p.acciones)),
    )


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
        409: {"description": "El comprobante ya se informó para otra unidad, o el pago que se"
              " corrige ya tiene su corrección."},
        422: {"description": "La fecha es posterior a hoy, el número es una cuenta, el"
              " comprobante no sirve o lo que se corrige no es un pago rechazado o anulado de"
              " la misma unidad."},
    },
)  # fmt: skip
def informar_pago(
    cuerpo: PagoInformadoNuevo,
    permisos: PermisosDep,
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    informar = permisos.informar
    informar.verificar(sobre_unidad(cuerpo.unidad_id))

    def efecto(conn: Connection) -> tuple[int, PagoInformadoRespuesta]:
        fila = ejecutar(
            conn, informar, INFORMAR_SQL,
            {"u": cuerpo.unidad_id, "importe": cuerpo.importe, "fecha": cuerpo.fecha_pago,
             "medio": cuerpo.medio.value, "numero": cuerpo.numero_operacion,
             "comprobante": cuerpo.comprobante_version_id, "corrige": cuerpo.corrige_a},
        ).one()  # fmt: skip
        pago = _pago(_fila(conn, permisos, fila.pago_id), permisos)
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


RESPUESTAS_DECIDIR = {
    **RESPUESTAS,
    403: {"description": "Sin la facultad `pagos.confirmar_informado`, o el pago lo informó esta"
          " cuenta (`ya_participo`)."},
    404: {"description": "El pago no existe en este consorcio o no se puede ver."},
    409: {"description": "El pago ya no admite ese paso (`pago_informado_decidido`) o, al"
          " confirmar, el antivirus todavía revisa el comprobante o lo rechazó"
          " (`comprobante_en_revision`, `comprobante_rechazado`)."},
}  # fmt: skip
CON_MOTIVO = {422: {"description": "El motivo falta, es largo o lleva una cuenta."}}


def _decidir(
    pago_id: UUID, sql: str, parametros: dict, permisos: Permisos, idempotencia, motor
) -> Response:
    """Un paso de PI-4: la separación (PI-5) la decide el evaluador con quién informó el pago, y
    la base repite todo en la misma transacción."""
    ctx = permisos.ver.ctx
    with transaccion(motor, persona_id=ctx.persona_id, consorcio_id=ctx.consorcio_id) as c:
        visto = _fila(c, permisos, pago_id)
    permisos.panel.verificar()
    confirmar = permisos.confirmar
    confirmar.verificar(de_quien_informo(visto))

    def efecto(conn: Connection) -> tuple[int, PagoInformado]:
        ejecutar(conn, confirmar, sql, parametros | {"p": pago_id})
        return 200, _pago(_fila(conn, permisos, pago_id), permisos)

    return responder_una_vez(motor, confirmar.ctx, idempotencia, efecto)


@router.post(
    BASE + "/{pago_id}/confirmacion",
    response_model=PagoInformado,
    summary="Confirmar un pago informado: el comprobante es creíble (no mueve dinero)",
    responses=RESPUESTAS_DECIDIR,
)
def confirmar_pago(
    pago_id: UUID, permisos: PermisosDep, idempotencia: IdempotenciaDep, motor: MotorDep
) -> Response:
    sql = "select nucleo.confirmar_pago_informado(:p, :actor, 'api', :sesion)"
    return _decidir(pago_id, sql, {}, permisos, idempotencia, motor)


@router.post(
    BASE + "/{pago_id}/rechazo",
    response_model=PagoInformado,
    summary="Rechazar un pago informado, con el motivo que lee quien lo informó",
    responses=RESPUESTAS_DECIDIR | CON_MOTIVO,
)
def rechazar_pago(
    pago_id: UUID,
    cuerpo: MotivoPagoInformado,
    permisos: PermisosDep,
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    sql = "select nucleo.rechazar_pago_informado(:p, :motivo, :actor, 'api', :sesion)"
    return _decidir(pago_id, sql, {"motivo": cuerpo.motivo}, permisos, idempotencia, motor)


@router.post(
    BASE + "/{pago_id}/anulacion",
    response_model=PagoInformado,
    summary="Anular un pago confirmado que resultó mal, con el motivo (nada se borra)",
    responses=RESPUESTAS_DECIDIR | CON_MOTIVO,
)
def anular_pago(
    pago_id: UUID,
    cuerpo: MotivoPagoInformado,
    permisos: PermisosDep,
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    sql = "select nucleo.anular_pago_informado(:p, :motivo, :actor, 'api', :sesion)"
    return _decidir(pago_id, sql, {"motivo": cuerpo.motivo}, permisos, idempotencia, motor)
