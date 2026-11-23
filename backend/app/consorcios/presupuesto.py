"""El presupuesto del consorcio (RF15 en su forma de Modo Consejo, RF48; diseño P0 S9; `0128`).

- **`GET …/presupuestos`** (`fiscalizacion.ver_panel`): todas las versiones, la más nueva primero,
  con sus rubros, y si la persona puede registrar (`presupuesto.registrar`).
- **`POST …/presupuestos`** (`presupuesto.registrar`): registra el presupuesto que aprobó la
  asamblea, o la versión que reemplaza a uno vigente con su motivo. Con `Idempotency-Key`.

Los desvíos y los vencimientos van en el panel (`GET …/panel`, `app.consorcios.fiscalizacion`).

La política decide al entrar; la base repite la barrera en cada función. Si la base niega lo que el
evaluador concedió, es una discrepancia (500).
"""

import json
from typing import Annotated
from uuid import UUID

from cem_contracts.errores import CodigoError
from cem_contracts.presupuesto import (
    LARGO_APROBACION,
    LARGO_MOTIVO,
    LARGO_NOMBRE_RUBRO,
    MAXIMO_RUBROS,
    ListaPresupuestos,
    Presupuesto,
    PresupuestoNuevo,
)
from fastapi import APIRouter, Depends, Response
from psycopg.errors import InsufficientPrivilege
from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError

from app.autorizacion.requiere import VOLVER_A_ENTRAR, Permiso, requiere
from app.consorcios.padron import Discrepancia
from app.contexto import MotorDep
from app.db import transaccion
from app.errores import ErrorApi, detalle_de_campo, sin_cache
from app.plataforma.idempotencia import IdempotenciaDep, responder_una_vez

router = APIRouter(prefix="/api/v1", tags=["consorcios"], dependencies=[Depends(sin_cache)])

VER = "fiscalizacion.ver_panel"
REGISTRAR = "presupuesto.registrar"

ERRORES = {
    "presupuesto_superpuesto": (
        409, CodigoError.PRESUPUESTO_SUPERPUESTO,
        "Otro presupuesto vigente ya cubre alguno de esos períodos. Para cambiarlo, se registra"
        " una versión que lo reemplaza, con el motivo.",
    ),
    "reemplazo_invalido": (
        409, CodigoError.REEMPLAZO_INVALIDO,
        "El presupuesto que se reemplaza ya no está vigente: otra versión lo reemplazó.",
    ),
    "texto_con_cuenta": (
        422, CodigoError.TEXTO_CON_CUENTA,
        "La aprobación y el motivo no pueden llevar el número de una cuenta.",
    ),
    "sesion_invalida": (403, *VOLVER_A_ENTRAR),
}  # fmt: skip
"""Motivos de las funciones de `0128` que la persona puede provocar. Los demás (`modo_no_admitido`,
`origen_invalido`, `rubros_invalidos`) son un defecto si llegan: el evaluador o el contrato ya los
impiden."""

CAMPO_DEL_MOTIVO = {"reemplazo_invalido": "reemplaza_a", "presupuesto_superpuesto": "desde"}
"""El campo del cuerpo que motiva el error, para que la pantalla lo marque."""

LEER = "select * from nucleo.presupuestos_lectura()"
REGISTRAR_SQL = (
    "select nucleo.registrar_presupuesto(:desde, :hasta, :aprobacion, cast(:rubros as jsonb),"
    " :reemplaza, :motivo, :actor, 'api', :sesion)"
)


def _ejecutar(conn: Connection, permiso: Permiso, sql: str, parametros: dict):
    """Llama a una función de `0128` y traduce sus motivos a errores de la API."""
    try:
        return conn.execute(text(sql), parametros)
    except DBAPIError as e:
        if isinstance(e.orig, InsufficientPrivilege):
            raise Discrepancia(f"la base niega {permiso.accion} y el evaluador lo concede") from e
        motivo = e.orig.diag.message_hint
        if motivo in ERRORES:
            raise ErrorApi(
                *ERRORES[motivo], detalle=detalle_de_campo(CAMPO_DEL_MOTIVO.get(motivo))
            ) from e
        raise


def _presupuesto(f) -> Presupuesto:
    return Presupuesto(**{k: v for k, v in f.items()})


def leer_presupuestos(conn: Connection, permiso: Permiso) -> list[Presupuesto]:
    return [_presupuesto(f) for f in _ejecutar(conn, permiso, LEER, {}).mappings()]


VerDep = Annotated[Permiso, Depends(requiere(VER, al_entrar=True))]
RegistrarDep = Annotated[Permiso, Depends(requiere(REGISTRAR))]


@router.get(
    "/consorcios/{consorcio_id}/presupuestos",
    response_model=ListaPresupuestos,
    summary="Los presupuestos registrados, la versión más nueva primero",
    responses={403: {"description": "Sin `fiscalizacion.ver_panel` en el consorcio."}},
)
def listar_presupuestos(ver: VerDep, registrar: RegistrarDep, motor: MotorDep) -> ListaPresupuestos:
    ctx = ver.ctx
    with transaccion(motor, persona_id=ctx.persona_id, consorcio_id=ctx.consorcio_id) as c:
        elementos = leer_presupuestos(c, ver)
    return ListaPresupuestos(
        elementos=elementos,
        puede_registrar=registrar.decidir().permitido,
        largo_aprobacion=LARGO_APROBACION,
        largo_motivo=LARGO_MOTIVO,
        largo_nombre_rubro=LARGO_NOMBRE_RUBRO,
        maximo_rubros=MAXIMO_RUBROS,
    )


@router.post(
    "/consorcios/{consorcio_id}/presupuestos",
    status_code=201,
    response_model=Presupuesto,
    summary="Registrar el presupuesto que aprobó la asamblea, o la versión que reemplaza a uno"
    " vigente, con su motivo",
    responses={
        403: {"description": "Sin la facultad, fuera de Modo Consejo o ayudando a otra persona."},
        409: {
            "description": "Otro vigente cubre esos períodos (`presupuesto_superpuesto`) o el que"
            " se reemplaza ya no está vigente (`reemplazo_invalido`)."
        },
        422: {"description": "La aprobación o el motivo llevan una cuenta (`texto_con_cuenta`)."},
    },
)
def registrar_presupuesto(
    cuerpo: PresupuestoNuevo,
    registrar: RegistrarDep,
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    registrar.verificar()
    ctx = registrar.ctx
    rubros = [
        {"numero": r.numero, "nombre": r.nombre, "importe": str(r.importe)} for r in cuerpo.rubros
    ]

    def efecto(conn: Connection) -> tuple[int, Presupuesto]:
        nuevo: UUID = _ejecutar(
            conn, registrar, REGISTRAR_SQL,
            {"desde": cuerpo.desde, "hasta": cuerpo.hasta, "aprobacion": cuerpo.aprobacion,
             "rubros": json.dumps(rubros), "reemplaza": cuerpo.reemplaza_a,
             "motivo": cuerpo.motivo, "actor": ctx.persona_id, "sesion": ctx.sesion_id},
        ).scalar_one()  # fmt: skip
        # La lectura de la base admite a quien registra aunque no vea el panel.
        [registrado] = [p for p in leer_presupuestos(conn, registrar) if p.id == nuevo]
        return 201, registrado

    return responder_una_vez(motor, ctx, idempotencia, efecto)
