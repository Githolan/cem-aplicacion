"""Comunicados oficiales del consorcio (RF28, mínimo para el piloto en Modo Consejo; `0122`).

Diseño P0 S8 del repositorio CEM y plan §6.7 («RF28 sin estados de lectura»).

- **`POST …/comunicados`** (`comunicados.emitir`): emite un comunicado a todas las personas con un
  vínculo confirmado en el edificio. En la misma transacción publica un correo por persona (una
  vez aunque tenga varias unidades) por el outbox de siempre (`correo.solicitar`) y los anota
  (`registrar_envios_comunicado`): o quedan el comunicado y todos sus correos, o nada. Con
  `Idempotency-Key`: reintentar con la misma clave devuelve lo mismo, sin otro comunicado ni otros
  correos. El correo lleva el título y el enlace; el texto se lee en la aplicación, y nunca la
  lista de destinatarios.
- **`GET …/comunicados`** y **`GET …/comunicados/{c}`** (`comunicados.ver`): los vínculos
  confirmados y quien emite. Quien emite ve además cuántas personas lo recibieron y cuántos correos
  salieron, nunca quiénes ni sus direcciones.

La política decide al entrar; la base repite la barrera en cada función. Si la base niega lo que el
evaluador concedió, es una discrepancia (500).
"""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated
from uuid import UUID

from cem_contracts.comunicados import (
    LARGO_TEXTO,
    LARGO_TITULO,
    Comunicado,
    ComunicadoNuevo,
    ComunicadoResumen,
    EnviosComunicado,
    ListaComunicados,
    ReferenciaComunicado,
)
from cem_contracts.errores import CodigoError
from fastapi import APIRouter, Depends, Request, Response
from psycopg.errors import InsufficientPrivilege
from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError

from app.autorizacion.requiere import NO_ENCONTRADO, VOLVER_A_ENTRAR, Permiso, requiere
from app.consorcios.padron import Discrepancia
from app.contexto import MotorDep
from app.db import transaccion
from app.errores import ErrorApi, detalle_de_campo, sin_cache
from app.identidad.invitaciones import origen_publico
from app.plataforma import correo
from app.plataforma.idempotencia import IdempotenciaDep, responder_una_vez

router = APIRouter(prefix="/api/v1", tags=["consorcios"], dependencies=[Depends(sin_cache)])

VER = "comunicados.ver"
EMITIR = "comunicados.emitir"
RUTA_COMUNICADOS = "/comunicados"
"""La pantalla de la aplicación (`frontend/src/comunicados/Comunicados.tsx`)."""

ERRORES = {
    "texto_con_cuenta": (
        422, CodigoError.TEXTO_CON_CUENTA,
        "Un comunicado no puede llevar el número de una cuenta: hay que sacarlo del título y del"
        " texto. Si no es una cuenta, separar los números con una coma.",
    ),
    "sin_destinatarios": (
        409, CodigoError.SIN_DESTINATARIOS,
        "Nadie tiene hoy un vínculo confirmado en el edificio: el comunicado no tendría a quién"
        " llegar. Los vínculos se confirman en el padrón.",
    ),
    "corrige_a_invalido": (
        422, CodigoError.PETICION_INVALIDA,
        "El comunicado que se corrige no es de este edificio.",
    ),
    "sesion_invalida": (403, *VOLVER_A_ENTRAR),
}  # fmt: skip
"""Motivos de las funciones de `0122` que la persona puede provocar. Los demás (`modo_no_admitido`,
`origen_invalido`, `envios_*`) son un defecto si llegan: el evaluador o esta ruta ya los impiden."""

CAMPO_DEL_MOTIVO = {"corrige_a_invalido": "corrige_a"}
"""El campo del cuerpo que motiva el error, para que la pantalla lo marque."""

RESPUESTAS = {
    403: {"description": "Sin la facultad, sin un vínculo confirmado o sin modo registrado."},
    404: {"description": "El comunicado no existe en este consorcio."},
}
ASUNTO = "Comunicado de {consorcio}: {titulo}"
TEXTO_CORREO = (
    "Hola:\n\n"
    "Hay un comunicado oficial de {consorcio} en Consorcio en Mano:\n\n"
    "{titulo}\n\n"
    "El texto completo se lee en la aplicación: {enlace}\n"
)
"""Solo el título y el enlace: el texto, los nombres y quién más lo recibe quedan en la
aplicación."""
POSIBLES = "select nucleo.comunicados_destinatarios_posibles(:actor, 'api', :sesion)"


def _una_linea(texto: str) -> str:
    return " ".join(texto.split())


def _ejecutar(conn: Connection, permiso: Permiso, sql: str, parametros: dict):
    """Llama a una función de `0122` con la persona y la sesión, y traduce sus motivos."""
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


@contextmanager
def _lectura(permiso: Permiso, motor) -> Iterator[Connection]:
    ctx = permiso.ctx
    with transaccion(motor, persona_id=ctx.persona_id, consorcio_id=ctx.consorcio_id) as c:
        yield c


LEER = "select * from nucleo.comunicados_lectura(:actor, 'api', :sesion)"


def _filas(conn: Connection, permiso: Permiso, comunicado: UUID | None = None) -> list:
    sql = LEER if comunicado is None else LEER + " where id = :c"
    return _ejecutar(conn, permiso, sql, {"c": comunicado}).mappings().all()


def _referencia(datos: dict | None) -> ReferenciaComunicado | None:
    return None if datos is None else ReferenciaComunicado(**datos)


def _resumen(f) -> dict:
    return {
        "id": f["id"], "titulo": f["titulo"], "emitido_por_nombre": f["emitido_por_nombre"],
        "emitido_en": f["emitido_en"], "corrige_a": _referencia(f["corrige_a"]),
        "corregido": len(f["corregido_por"]) > 0,
    }  # fmt: skip


def _comunicado(f) -> Comunicado:
    envios = (
        None
        if f["destinatarios"] is None
        else EnviosComunicado(destinatarios=f["destinatarios"], enviados=f["enviados"])
    )
    return Comunicado(
        **_resumen(f),
        texto=f["texto"],
        corregido_por=[ReferenciaComunicado(**r) for r in f["corregido_por"]],
        envios=envios,
    )


def _uno(conn: Connection, permiso: Permiso, comunicado: UUID) -> Comunicado:
    filas = _filas(conn, permiso, comunicado)
    if not filas:
        raise ErrorApi(404, CodigoError.NO_ENCONTRADO, NO_ENCONTRADO)
    return _comunicado(filas[0])


VerDep = Annotated[Permiso, Depends(requiere(VER, al_entrar=True))]
EmitirDep = Annotated[Permiso, Depends(requiere(EMITIR))]


@router.get(
    "/consorcios/{consorcio_id}/comunicados",
    response_model=ListaComunicados,
    summary="Comunicados oficiales del edificio, del más nuevo al más viejo",
    responses={403: RESPUESTAS[403]},
)
def listar_comunicados(ver: VerDep, emitir: EmitirDep, motor: MotorDep) -> ListaComunicados:
    puede_emitir = emitir.decidir().permitido
    with _lectura(ver, motor) as c:
        elementos = [ComunicadoResumen(**_resumen(f)) for f in _filas(c, ver)]
        posibles = _ejecutar(c, emitir, POSIBLES, {}).scalar_one() if puede_emitir else None
    return ListaComunicados(
        elementos=elementos,
        puede_emitir=puede_emitir,
        destinatarios_posibles=posibles,
        largo_titulo=LARGO_TITULO,
        largo_texto=LARGO_TEXTO,
    )


@router.get(
    "/consorcios/{consorcio_id}/comunicados/{comunicado_id}",
    response_model=Comunicado,
    summary="Un comunicado con su texto; para quien emite, cuántos lo recibieron",
    responses=RESPUESTAS,
)
def ver_comunicado(comunicado_id: UUID, ver: VerDep, motor: MotorDep) -> Comunicado:
    with _lectura(ver, motor) as c:
        return _uno(c, ver, comunicado_id)


@router.post(
    "/consorcios/{consorcio_id}/comunicados",
    status_code=201,
    response_model=Comunicado,
    summary="Emitir un comunicado a todo el edificio: un correo a cada persona con un vínculo"
    " confirmado, con el título y el enlace",
    responses={
        **RESPUESTAS,
        409: {"description": "Nadie tiene un vínculo confirmado (`sin_destinatarios`)."},
        422: {"description": "El título o el texto lleva una cuenta (`texto_con_cuenta`)."},
    },
)
def emitir_comunicado(
    request: Request,
    cuerpo: ComunicadoNuevo,
    emitir: EmitirDep,
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    emitir.verificar()
    enlace = f"{origen_publico(request)}{RUTA_COMUNICADOS}"
    consorcio = emitir.ctx.consorcio_id

    def efecto(conn: Connection) -> tuple[int, Comunicado]:
        destinatarios = _ejecutar(
            conn, emitir,
            "select * from nucleo.emitir_comunicado(:titulo, :texto, :corrige, :actor, 'api',"
            " :sesion)",
            {"titulo": cuerpo.titulo, "texto": cuerpo.texto, "corrige": cuerpo.corrige_a},
        ).mappings().all()  # fmt: skip
        nuevo = destinatarios[0]["comunicado_id"]
        nombre, titulo = _una_linea(destinatarios[0]["consorcio_nombre"]), _una_linea(cuerpo.titulo)
        envios = [
            {"persona_id": str(d["persona_id"]),
             "evento_id": str(correo.solicitar(
                 conn, para=d["correo"], asunto=ASUNTO.format(consorcio=nombre, titulo=titulo),
                 texto=TEXTO_CORREO.format(consorcio=nombre, titulo=titulo, enlace=enlace),
                 consorcio_id=consorcio))}
            for d in destinatarios
        ]  # fmt: skip
        _ejecutar(
            conn, emitir,
            "select nucleo.registrar_envios_comunicado(:c, cast(:envios as jsonb), :actor, 'api',"
            " :sesion)",
            {"c": nuevo, "envios": json.dumps(envios)},
        )  # fmt: skip
        # Quien emite lee lo emitido: la barrera de la lectura lo admite por la facultad.
        return 201, _uno(conn, emitir, nuevo)

    return responder_una_vez(motor, emitir.ctx, idempotencia, efecto)
