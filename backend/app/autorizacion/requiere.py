"""`requiere(accion)`: cada ruta con `ContextoDep` declara su acción (tabla de verdad, §6.5).

La dependencia arma el `Contexto` (ámbito, 404 si no hay) y da un `Permiso` atado a la acción.
Las acciones sin entidad (p. ej. `unidades.listar`) se verifican al entrar; las que dependen de
la entidad (la unidad de una invitación) las verifica la ruta con `permiso.verificar(entidad)`,
apenas la conoce y antes de cualquier efecto.

Una denegación responde el error genérico (403, o el 404 que pida la ruta para no revelar que
algo existe) y deja el **motivo interno** en la auditoría encadenada del consorcio. El 404 por
falta de ámbito no se audita en el consorcio: quien lo recibe no tiene nada que ver con él.

Una acción que no está en las políticas es un error de programación: `requiere` falla al
importar la ruta, no en el pedido.
"""

import logging
from dataclasses import dataclass, replace
from typing import Annotated
from uuid import UUID

from cem_contracts.errores import CodigoError
from fastapi import Depends, Request
from sqlalchemy import text

from app import auditoria
from app.autorizacion.evaluador import (
    Decision,
    Entidad,
    Motivo,
    evaluar,
    separa_seres_humanos,
)
from app.autorizacion.politicas import POLITICAS
from app.contexto import Contexto, MotorDep, require_scope
from app.db import transaccion
from app.errores import ErrorApi
from app.identidad import sesiones

log = logging.getLogger(__name__)

NO_ENCONTRADO = "No encontramos lo que buscás."
SIN_PERMISO = "No tenés permiso para hacer esto."
RENOVAR = {
    Motivo.REQUIERE_SEGUNDO_FACTOR_RECIENTE: (
        CodigoError.SEGUNDO_FACTOR_REQUERIDO,
        "Para esto tenés que confirmar con el código de tu aplicación de autenticación.",
    ),
    Motivo.REQUIERE_SESION_RECIENTE: (
        CodigoError.SESION_RECIENTE_REQUERIDA,
        "Para esto tenés que volver a entrar.",
    ),
}
"""Las negaciones de R10 que la persona resuelve renovando la sesión. Se dicen tal cual porque el
evaluador las mira al final (`_filtrar`): solo llegan acá si todo lo demás ya concedía."""


@dataclass(frozen=True)
class Permiso:
    accion: str
    ctx: Contexto
    motor: object
    request: Request

    def decidir(self, entidad: Entidad | None = None) -> Decision:
        """Solo evalúa, sin efectos (p. ej. para filtrar una lista fila por fila)."""
        entidad = entidad or Entidad()
        return evaluar(self.accion, self._con_identidades(entidad).autorizacion, entidad)

    def _con_identidades(self, entidad: Entidad) -> Contexto:
        """Si la acción separa seres humanos (A13-C), agrega las identidades verificadas en el
        consorcio de las personas de la entidad. Las de quien actúa ya vienen en el contexto."""
        if not any(separa_seres_humanos(self.accion, c) for c in POLITICAS[self.accion].separacion):
            return self.ctx
        personas = _personas(entidad) - {self.ctx.persona_id}
        if not personas:
            return self.ctx
        with transaccion(
            self.motor, persona_id=self.ctx.persona_id, consorcio_id=self.ctx.consorcio_id
        ) as conn:
            filas = conn.execute(
                text(
                    "select persona_id, identidad_id from nucleo.identidad_persona"
                    " where consorcio_id = :c and persona_id = any(:p)"
                    " and desvinculada_en is null"
                ),
                {"c": self.ctx.consorcio_id, "p": list(personas)},
            )
            identidades = {f.persona_id: f.identidad_id for f in filas}
        return replace(self.ctx, identidades=identidades)

    def verificar(self, entidad: Entidad | None = None, *, estado_negado: int = 403) -> Decision:
        """Evalúa y, si se niega, audita el motivo y responde el error genérico.

        `estado_negado=404` para no revelar que la entidad existe (p. ej. una invitación de
        una unidad ajena)."""
        decision = self.decidir(entidad)
        if decision.permitido:
            return decision
        if decision.estado == 404:
            raise ErrorApi(404, CodigoError.NO_ENCONTRADO, NO_ENCONTRADO)
        self._auditar(decision)
        if estado_negado == 404:
            raise ErrorApi(404, CodigoError.NO_ENCONTRADO, NO_ENCONTRADO)
        if decision.motivo_interno in RENOVAR:
            raise ErrorApi(403, *RENOVAR[decision.motivo_interno])
        raise ErrorApi(403, CodigoError.SIN_PERMISO, SIN_PERMISO)

    def _auditar(self, decision: Decision) -> None:
        with transaccion(
            self.motor, persona_id=self.ctx.persona_id, consorcio_id=self.ctx.consorcio_id
        ) as conn:
            auditoria.registrar(
                conn,
                consorcio_id=self.ctx.consorcio_id,
                actor=self.ctx.persona_id,
                origen="api",
                accion="autorizacion.denegada",
                entidad="accion",
                entidad_id=None,
                despues={"accion": self.accion, "motivo": decision.motivo_interno},
                ip=sesiones.ip_cliente(self.request),
            )
        log.info(
            "permiso denegado",
            extra={
                "campos": {
                    "consorcio_id": str(self.ctx.consorcio_id),
                    "persona_id": str(self.ctx.persona_id),
                    "accion": self.accion,
                    "motivo": decision.motivo_interno,
                }
            },
        )


def _personas(entidad: Entidad) -> set[UUID]:
    """Las personas que la entidad pone en algún lugar de una separación."""
    candidatas = (
        entidad.preparado_por, entidad.solicitado_por, entidad.propuesto_por,
        entidad.persona_objetivo, entidad.registrado_por, entidad.emitido_por,
        entidad.decision_registrada_por, *entidad.aprobado_por, *entidad.firmas,
    )  # fmt: skip
    return {p for p in candidatas if p is not None}


class Requiere:
    """Dependencia de FastAPI: `permiso: Annotated[Permiso, Depends(requiere("x"))]`."""

    def __init__(self, accion: str, *, al_entrar: bool) -> None:
        if accion not in POLITICAS:
            raise ValueError(f"acción sin política declarada: {accion!r}")
        self.accion = accion
        self.al_entrar = al_entrar

    def __call__(
        self, request: Request, motor: MotorDep, ctx: Annotated[Contexto, Depends(require_scope)]
    ) -> Permiso:
        permiso = Permiso(self.accion, ctx, motor, request)
        if self.al_entrar:
            permiso.verificar()
        return permiso


def requiere(accion: str, *, al_entrar: bool = False) -> Requiere:
    """`al_entrar=True` para las acciones que no dependen de la entidad."""
    return Requiere(accion, al_entrar=al_entrar)
