"""Reclamos sobre la liquidación oficial (RF34, Modo Consejo; `0123`), con el borrador sin conexión
de la pantalla (RF38, PA16).

Diseño P0 S7 del repositorio CEM y decisiones del 22-11-2026.

- **`POST …/reclamos-liquidacion`** (`reclamos_liquidacion.presentar`, sobre la unidad citada): el
  propietario confirmado reclama sobre una liquidación oficial de una unidad suya. Es también lo
  que manda el borrador de la pantalla al volver la conexión: la política y la base revalidan
  todo (el vínculo, la unidad, la liquidación y el dato citado). Si la liquidación citada fue
  reemplazada, 409 `liquidacion_reemplazada`, salvo `acepta_reemplazada`. En la misma transacción
  sale un correo a cada persona con `reclamos.atender`, con el título y el enlace. Con
  `Idempotency-Key`: el borrador reintenta con su clave y no queda repetido.
- **`GET …/reclamos-liquidacion`** y **`GET …/reclamos-liquidacion/{r}`**
  (`reclamos_liquidacion.ver`): quien atiende ve todos; el propietario, los que presentó.
- **`POST …/{r}/respuesta`** (`reclamos_liquidacion.atender`): cierra el reclamo con la respuesta
  y le avisa por correo a quien lo presentó.
- **`POST …/{r}/retiro`** (`reclamos_liquidacion.presentar`, sobre la unidad del reclamo): solo
  quien lo presentó, mientras está presentado.
- **Pasar a una solicitud formal:** no tiene ruta propia. Se prepara la solicitud por la ruta de
  siempre (`POST …/solicitudes-formales`, RF49) con `reclamo_id`: en la misma transacción el
  reclamo pasa a `derivado_a_solicitud` (`derivar` de acá).

Ningún acto cambia un importe ni la liquidación (RF34). La política decide al entrar; la base
repite la barrera en cada función. Si la base niega lo que el evaluador concedió, es una
discrepancia (500).
"""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from typing import Annotated
from uuid import UUID

from cem_contracts.errores import CodigoError
from cem_contracts.reclamos import (
    LARGO_MOTIVO,
    LARGO_TEXTO,
    AccionReclamo,
    EstadoReclamo,
    ListaReclamos,
    PasoReclamo,
    Reclamo,
    ReclamoNuevo,
    ReclamoResumen,
    RespuestaReclamo,
    RetiroReclamo,
)
from fastapi import APIRouter, Depends, Request, Response
from psycopg.errors import InsufficientPrivilege
from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError

from app.autorizacion.requiere import (
    NO_ENCONTRADO,
    SIN_PERMISO,
    VOLVER_A_ENTRAR,
    Permiso,
    requiere,
)
from app.consorcios.padron import Discrepancia, sobre_unidad
from app.contexto import MotorDep
from app.db import transaccion
from app.errores import ErrorApi, detalle_de_campo, sin_cache
from app.identidad.invitaciones import origen_publico
from app.plataforma import correo
from app.plataforma.idempotencia import IdempotenciaDep, responder_una_vez

router = APIRouter(prefix="/api/v1", tags=["consorcios"], dependencies=[Depends(sin_cache)])

VER = "reclamos_liquidacion.ver"
PRESENTAR = "reclamos_liquidacion.presentar"
ATENDER = "reclamos_liquidacion.atender"
PREPARAR_SOLICITUD = "solicitud_formal.preparar"
RUTA_RECLAMOS = "/reclamos"
"""La pantalla de quien atiende (`frontend/src/fiscalizacion/Reclamos.tsx`)."""
RUTA_MIS_EXPENSAS = "/mis-expensas"
"""La pantalla del propietario, con sus reclamos (`frontend/src/fiscalizacion/MisExpensas.tsx`)."""

RECLAMO_CERRADO = (
    409, CodigoError.RECLAMO_CERRADO,
    "El reclamo ya se respondió, se pasó a una solicitud formal o se retiró: hay que volver a"
    " abrirlo para ver cómo quedó.",
)  # fmt: skip
ERRORES = {
    "texto_con_cuenta": (
        422, CodigoError.TEXTO_CON_CUENTA,
        "Un reclamo no puede llevar el número de una cuenta: hay que sacarlo del texto. Si no es"
        " una cuenta, separar los números con una coma.",
    ),
    "liquidacion_reemplazada": (
        409, CodigoError.LIQUIDACION_REEMPLAZADA,
        "La liquidación que cita el reclamo fue reemplazada por otra del mismo período. Se puede"
        " citar la vigente o reclamar igual sobre la reemplazada.",
    ),
    "liquidacion_no_oficial": (
        422, CodigoError.PETICION_INVALIDA,
        "La liquidación citada no es una liquidación oficial de este edificio.",
    ),
    "campo_no_figura": (
        422, CodigoError.PETICION_INVALIDA,
        "Ese dato no figura para esa unidad en la liquidación citada.",
    ),
    "reclamo_cerrado": RECLAMO_CERRADO,
    "no_encontrado": (404, CodigoError.NO_ENCONTRADO, NO_ENCONTRADO),
    "sesion_invalida": (403, *VOLVER_A_ENTRAR),
}  # fmt: skip
"""Motivos de las funciones de `0123` que la persona puede provocar. Los demás (`modo_no_admitido`,
`origen_invalido`, `solicitud_invalida`) son un defecto si llegan: el evaluador o la ruta ya los
impiden."""

CAMPO_DEL_MOTIVO = {
    "texto_con_cuenta": "texto",
    "liquidacion_no_oficial": "liquidacion_id",
    "campo_no_figura": "campo",
}
"""El campo del cuerpo que motiva el error, para que la pantalla lo marque."""

RESPUESTAS = {
    403: {"description": "Sin un vínculo de propietario confirmado o sin la facultad."},
    404: {"description": "El reclamo no existe en este consorcio, o no es de esta persona."},
}
ASUNTO_PRESENTADO = "Reclamo sobre la liquidación de {consorcio}"
TEXTO_PRESENTADO = (
    "Hola:\n\n"
    "Un propietario presentó un reclamo sobre la liquidación del período {periodo} de {consorcio}"
    " en Consorcio en Mano.\n\n"
    "Se lee y se atiende en la aplicación: {enlace}\n"
)
ASUNTO_RESPONDIDO = "Respuesta al reclamo presentado en {consorcio}"
TEXTO_RESPONDIDO = (
    "Hola:\n\n"
    "Hay una respuesta al reclamo presentado sobre la liquidación del período {periodo} de"
    " {consorcio} en"
    " Consorcio en Mano.\n\n"
    "Se lee en la aplicación: {enlace}\n"
)
"""Solo el título y el enlace: ni el texto del reclamo ni la respuesta ni quién lo presentó."""


def _periodo(p: date) -> str:
    return f"{p:%m/%Y}"


def _una_linea(texto: str) -> str:
    return " ".join(texto.split())


def ejecutar(conn: Connection, permiso: Permiso, sql: str, parametros: dict):
    """Llama a una función de `0123` con la persona y la sesión, y traduce sus motivos."""
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


def derivar(conn: Connection, atender: Permiso, reclamo: UUID, solicitud: UUID) -> None:
    """El reclamo pasa a `derivado_a_solicitud`, atendido por la solicitud recién preparada en la
    misma transacción (lo llama la ruta de preparar de RF49)."""
    ejecutar(
        conn, atender,
        "select nucleo.derivar_reclamo_liquidacion(:r, :s, :actor, 'api', :sesion)",
        {"r": reclamo, "s": solicitud},
    )  # fmt: skip


@dataclass(frozen=True)
class Permisos:
    """Lo que la persona puede hacer con los reclamos; `ver`, que se verifica al entrar, da el
    contexto. Para decidir qué ofrecer: cada acto vuelve a verificar su acción."""

    ver: Permiso
    presentar: Permiso
    atender: Permiso
    preparar_solicitud: Permiso


def _permisos(
    ver: Annotated[Permiso, Depends(requiere(VER, al_entrar=True))],
    presentar: Annotated[Permiso, Depends(requiere(PRESENTAR))],
    atender: Annotated[Permiso, Depends(requiere(ATENDER))],
    preparar_solicitud: Annotated[Permiso, Depends(requiere(PREPARAR_SOLICITUD))],
) -> Permisos:
    return Permisos(ver, presentar, atender, preparar_solicitud)


PermisosDep = Annotated[Permisos, Depends(_permisos)]


@contextmanager
def _lectura(permiso: Permiso, motor) -> Iterator[Connection]:
    ctx = permiso.ctx
    with transaccion(motor, persona_id=ctx.persona_id, consorcio_id=ctx.consorcio_id) as c:
        yield c


LEER = "select * from nucleo.reclamos_liquidacion_lectura(:actor, 'api', :sesion)"


def _filas(conn: Connection, permiso: Permiso, reclamo: UUID | None = None) -> list:
    sql = LEER if reclamo is None else LEER + " where id = :r"
    return ejecutar(conn, permiso, sql, {"r": reclamo}).mappings().all()


def _resumen(f) -> dict:
    return {
        "id": f["id"], "liquidacion_id": f["importacion_id"], "periodo": f["periodo"],
        "liquidacion_vigente": f["liquidacion_vigente"], "unidad_id": f["unidad_id"],
        "designacion": f["designacion"], "campo": f["campo"],
        "presentado_por_nombre": f["presentado_por_nombre"], "presentado_en": f["presentado_en"],
        "estado": f["estado"], "propio": f["propio"],
    }  # fmt: skip


def _acciones(f, permisos: Permisos) -> list[AccionReclamo]:
    if f["estado"] != EstadoReclamo.PRESENTADO:
        return []
    acciones = []
    if permisos.atender.decidir().permitido:
        acciones.append(AccionReclamo.RESPONDER)
        if permisos.preparar_solicitud.decidir().permitido:
            acciones.append(AccionReclamo.PASAR_A_SOLICITUD)
    if f["propio"] and permisos.presentar.decidir(sobre_unidad(f["unidad_id"])).permitido:
        acciones.append(AccionReclamo.RETIRAR)
    return acciones


def _reclamo(f, permisos: Permisos) -> Reclamo:
    return Reclamo(
        **_resumen(f),
        texto=f["texto"],
        historia=[PasoReclamo(**p) for p in f["historia"]],
        acciones=_acciones(f, permisos),
    )


def _uno(conn: Connection, permisos: Permisos, reclamo: UUID) -> Reclamo:
    filas = _filas(conn, permisos.ver, reclamo)
    if not filas:
        raise ErrorApi(404, CodigoError.NO_ENCONTRADO, NO_ENCONTRADO)
    return _reclamo(filas[0], permisos)


@router.get(
    "/consorcios/{consorcio_id}/reclamos-liquidacion",
    response_model=ListaReclamos,
    summary="Reclamos sobre la liquidación: todos, para quien atiende; los propios, para el"
    " propietario",
    responses={403: RESPUESTAS[403]},
)
def listar_reclamos(permisos: PermisosDep, motor: MotorDep) -> ListaReclamos:
    with _lectura(permisos.ver, motor) as c:
        elementos = [ReclamoResumen(**_resumen(f)) for f in _filas(c, permisos.ver)]
    return ListaReclamos(
        elementos=elementos,
        atiende=permisos.atender.decidir().permitido,
        largo_texto=LARGO_TEXTO,
        largo_motivo=LARGO_MOTIVO,
    )


@router.get(
    "/consorcios/{consorcio_id}/reclamos-liquidacion/{reclamo_id}",
    response_model=Reclamo,
    summary="Un reclamo con su texto, su historia y lo que la persona puede hacer con él",
    responses=RESPUESTAS,
)
def ver_reclamo(reclamo_id: UUID, permisos: PermisosDep, motor: MotorDep) -> Reclamo:
    with _lectura(permisos.ver, motor) as c:
        return _uno(c, permisos, reclamo_id)


@router.post(
    "/consorcios/{consorcio_id}/reclamos-liquidacion",
    status_code=201,
    response_model=Reclamo,
    summary="Presentar un reclamo sobre la liquidación oficial de una unidad propia (también lo"
    " que manda el borrador sin conexión); avisa por correo a quien atiende",
    responses={
        **RESPUESTAS,
        409: {"description": "La liquidación citada fue reemplazada (`liquidacion_reemplazada`)."},
        422: {"description": "El texto lleva una cuenta, la liquidación no es oficial o el dato"
              " citado no figura para la unidad."},
    },
)  # fmt: skip
def presentar_reclamo(
    request: Request,
    cuerpo: ReclamoNuevo,
    permisos: PermisosDep,
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    presentar = permisos.presentar
    presentar.verificar(sobre_unidad(cuerpo.unidad_id))
    enlace = f"{origen_publico(request)}{RUTA_RECLAMOS}"
    consorcio = presentar.ctx.consorcio_id

    def efecto(conn: Connection) -> tuple[int, Reclamo]:
        avisar = ejecutar(
            conn, presentar,
            "select * from nucleo.presentar_reclamo_liquidacion(:i, :u, :campo, :texto,"
            " :acepta, :actor, 'api', :sesion)",
            {"i": cuerpo.liquidacion_id, "u": cuerpo.unidad_id,
             "campo": None if cuerpo.campo is None else cuerpo.campo.value, "texto": cuerpo.texto,
             "acepta": cuerpo.acepta_reemplazada},
        ).mappings().all()  # fmt: skip
        nuevo = avisar[0]["reclamo_id"]
        nombre = _una_linea(avisar[0]["consorcio_nombre"])
        periodo = _periodo(avisar[0]["periodo"])
        for a in avisar:
            if a["persona_id"] is None:
                continue
            correo.solicitar(
                conn, para=a["correo"], asunto=ASUNTO_PRESENTADO.format(consorcio=nombre),
                texto=TEXTO_PRESENTADO.format(periodo=periodo, consorcio=nombre, enlace=enlace),
                consorcio_id=consorcio,
            )  # fmt: skip
        return 201, _uno(conn, permisos, nuevo)

    return responder_una_vez(motor, presentar.ctx, idempotencia, efecto)


@router.post(
    "/consorcios/{consorcio_id}/reclamos-liquidacion/{reclamo_id}/respuesta",
    response_model=Reclamo,
    summary="Responder un reclamo: lo cierra y le avisa por correo a quien lo presentó",
    responses={**RESPUESTAS, 409: {"description": "El reclamo ya se cerró (`reclamo_cerrado`)."}},
)
def responder_reclamo(
    request: Request,
    reclamo_id: UUID,
    cuerpo: RespuestaReclamo,
    permisos: PermisosDep,
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    atender = permisos.atender
    atender.verificar()
    enlace = f"{origen_publico(request)}{RUTA_MIS_EXPENSAS}"

    def efecto(conn: Connection) -> tuple[int, Reclamo]:
        [a] = ejecutar(
            conn, atender,
            "select * from nucleo.responder_reclamo_liquidacion(:r, :texto, :actor, 'api',"
            " :sesion)",
            {"r": reclamo_id, "texto": cuerpo.texto},
        ).mappings().all()  # fmt: skip
        nombre = _una_linea(a["consorcio_nombre"])
        correo.solicitar(
            conn, para=a["correo"], asunto=ASUNTO_RESPONDIDO.format(consorcio=nombre),
            texto=TEXTO_RESPONDIDO.format(periodo=_periodo(a["periodo"]), consorcio=nombre,
                                          enlace=enlace),
            consorcio_id=atender.ctx.consorcio_id,
        )  # fmt: skip
        return 200, _uno(conn, permisos, reclamo_id)

    return responder_una_vez(motor, atender.ctx, idempotencia, efecto)


@router.post(
    "/consorcios/{consorcio_id}/reclamos-liquidacion/{reclamo_id}/retiro",
    response_model=Reclamo,
    summary="Retirar un reclamo propio mientras está presentado, con el motivo",
    responses={**RESPUESTAS, 409: {"description": "El reclamo ya se cerró (`reclamo_cerrado`)."}},
)
def retirar_reclamo(
    reclamo_id: UUID,
    cuerpo: RetiroReclamo,
    permisos: PermisosDep,
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    with _lectura(permisos.ver, motor) as c:
        visto = _uno(c, permisos, reclamo_id)
    if not visto.propio:
        # Quien atiende lo ve, pero solo lo retira quien lo presentó.
        raise ErrorApi(403, CodigoError.SIN_PERMISO, SIN_PERMISO)
    presentar = permisos.presentar
    presentar.verificar(sobre_unidad(visto.unidad_id))

    def efecto(conn: Connection) -> tuple[int, Reclamo]:
        ejecutar(
            conn, presentar,
            "select nucleo.retirar_reclamo_liquidacion(:r, :motivo, :actor, 'api', :sesion)",
            {"r": reclamo_id, "motivo": cuerpo.motivo},
        )  # fmt: skip
        return 200, _uno(conn, permisos, reclamo_id)

    return responder_una_vez(motor, presentar.ctx, idempotencia, efecto)
