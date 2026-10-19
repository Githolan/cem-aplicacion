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
from dataclasses import dataclass
from typing import Annotated

from cem_contracts.errores import CodigoError
from fastapi import Depends, Request

from app import auditoria
from app.autorizacion.evaluador import Decision, Entidad, evaluar
from app.autorizacion.politicas import POLITICAS
from app.contexto import Contexto, MotorDep, require_scope
from app.db import transaccion
from app.errores import ErrorApi
from app.identidad import sesiones

log = logging.getLogger(__name__)

NO_ENCONTRADO = "No encontramos lo que buscás."
SIN_PERMISO = "No tenés permiso para hacer esto."


@dataclass(frozen=True)
class Permiso:
    accion: str
    ctx: Contexto
    motor: object
    request: Request

    def decidir(self, entidad: Entidad | None = None) -> Decision:
        """Solo evalúa, sin efectos (p. ej. para filtrar una lista fila por fila)."""
        return evaluar(self.accion, self.ctx.autorizacion, entidad or Entidad())

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
