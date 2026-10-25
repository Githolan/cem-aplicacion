"""Lectura de los esquemas de coeficientes y sus reglas de reparto (RF05; D4).

Diseño: `doc/diseno/H2b_5_Diseno.md` del repositorio CEM, §3: por la API, solo lectura, para quien
tiene `padron.ver` o `coeficientes.proponer`. No hay facultades nuevas: dos políticas de lectura
deciden cada coeficiente con esas facultades, fila por fila, como el padrón:
`coeficientes.ver_por_unidad` (`padron.ver` sobre la unidad del coeficiente) y
`coeficientes.ver_por_grupo` (`coeficientes.proponer` sobre el grupo del esquema, sin el respaldo
documental que pide proponer: leer no propone nada). Un esquema sale si quien lee ve alguno de sus
coeficientes o lo lee por su grupo.

Las funciones de `0029` exigen, como segunda barrera, un mandato vigente con alguna de las dos
facultades. Si la base niega y el evaluador concede alguna, no coinciden: la ruta falla fuerte
(500), nunca devuelve una lista vacía. Si niegan las dos, la negación queda auditada con
`coeficientes.ver_por_unidad` (la lectura que el diseño nombra primero) y responde 403.

Si la base deja leer pero ninguna política concede nada, una negación por filtro (sin modo, un
recurso inválido) se audita y responde 403, aunque haya también negaciones por alcance. La lista
vacía solo vale si lo único que faltó fue el alcance (una facultad acotada que no cubre ningún
esquema).
"""

from typing import Annotated
from uuid import UUID

from cem_contracts.coeficientes import (
    CoeficienteUnidad,
    EsquemaCoeficientes,
    ListaEsquemasCoeficientes,
    ReglaReparto,
)
from cem_contracts.politicas import TipoRecurso
from fastapi import APIRouter, Depends
from psycopg.errors import InsufficientPrivilege
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.autorizacion.evaluador import Entidad, Motivo
from app.autorizacion.recursos import MotivoRecurso
from app.autorizacion.requiere import Permiso, requiere
from app.consorcios.padron import Discrepancia, sobre_unidad
from app.contexto import MotorDep
from app.db import transaccion
from app.errores import sin_cache

NO_ALCANZA = MotivoRecurso.NO_ALCANZA

router = APIRouter(prefix="/api/v1", tags=["coeficientes"], dependencies=[Depends(sin_cache)])


def sobre_grupo(grupo: str) -> Entidad:
    return Entidad(recursos={TipoRecurso.GRUPO: grupo})


@router.get(
    "/consorcios/{consorcio_id}/coeficientes",
    response_model=ListaEsquemasCoeficientes,
    summary="Esquemas de coeficientes y reglas de reparto, por grupo y versión",
    responses={
        403: {"description": "Sin `padron.ver` ni `coeficientes.proponer` en el consorcio."}
    },
)
def listar_coeficientes(
    por_unidad: Annotated[Permiso, Depends(requiere("coeficientes.ver_por_unidad"))],
    por_grupo: Annotated[Permiso, Depends(requiere("coeficientes.ver_por_grupo"))],
    motor: MotorDep,
) -> ListaEsquemasCoeficientes:
    """Los porcentajes tal como los guarda la base (D4). Sin la referencia de la fuente, la
    decisión de cada versión ni quién la registró."""
    ctx = por_unidad.ctx
    try:
        with transaccion(motor, persona_id=ctx.persona_id, consorcio_id=ctx.consorcio_id) as conn:
            filas = (
                conn.execute(
                    text(
                        "select esquema_id, grupo, version, vigente_desde, vigente_hasta,"
                        " unidad_id, designacion, porcentaje"
                        " from nucleo.coeficientes_del_consorcio()"
                    )
                )
                .mappings()
                .all()
            )
            reglas = (
                conn.execute(
                    text(
                        "select esquema_id, version, metodo, centavos"
                        " from nucleo.reglas_de_reparto_del_consorcio()"
                    )
                )
                .mappings()
                .all()
            )
    except DBAPIError as e:
        if not isinstance(e.orig, InsufficientPrivilege):
            raise
        if por_unidad.decidir(Entidad()).permitido or por_grupo.decidir(Entidad()).permitido:
            raise Discrepancia(
                "la base niega leer los coeficientes y el evaluador lo concede"
            ) from e
        por_unidad.verificar(Entidad())  # niega, audita y responde 403
        raise Discrepancia("el evaluador niega y después concede la lectura") from e

    negadas: list[tuple[Permiso, Entidad, str]] = []
    concedio = False

    def permite(permiso: Permiso, entidad: Entidad) -> bool:
        nonlocal concedio
        decision = permiso.decidir(entidad)
        if decision.permitido:
            concedio = True
        else:
            negadas.append((permiso, entidad, decision.motivo_interno))
        return decision.permitido

    reglas_por_esquema: dict[UUID, list[ReglaReparto]] = {}
    for r in reglas:
        reglas_por_esquema.setdefault(r["esquema_id"], []).append(
            ReglaReparto(version=r["version"], metodo=r["metodo"], centavos=r["centavos"])
        )
    esquemas: dict[UUID, dict] = {}
    for f in filas:
        e = esquemas.get(f["esquema_id"])
        if e is None:
            e = esquemas[f["esquema_id"]] = {
                "grupo": f["grupo"],
                "version": f["version"],
                "vigente_desde": f["vigente_desde"],
                "vigente_hasta": f["vigente_hasta"],
                "por_grupo": permite(por_grupo, sobre_grupo(f["grupo"])),
                "coeficientes": [],
            }
        if f["unidad_id"] is None:
            continue
        if e["por_grupo"] or permite(por_unidad, sobre_unidad(f["unidad_id"])):
            e["coeficientes"].append(
                CoeficienteUnidad(
                    unidad_id=f["unidad_id"],
                    designacion=f["designacion"],
                    porcentaje=f["porcentaje"],
                )
            )
    elementos = [
        EsquemaCoeficientes(
            grupo=e["grupo"],
            version=e["version"],
            vigente_desde=e["vigente_desde"],
            vigente_hasta=e["vigente_hasta"],
            reglas=reglas_por_esquema.get(esquema_id, []),
            coeficientes=e["coeficientes"],
        )
        for esquema_id, e in esquemas.items()
        if e["por_grupo"] or e["coeficientes"]
    ]
    if not elementos and not concedio:
        # La base dejó leer, pero ninguna política concedió. El evaluador mira el alcance antes
        # que los filtros: una negación por filtro (sin modo, un recurso inválido) gana sobre una
        # por alcance y se audita con 403. La lista vacía solo vale si lo único que faltó fue el
        # alcance (una facultad acotada que no cubre ningún esquema).
        for permiso in (por_unidad, por_grupo):
            permite(permiso, Entidad())
        if not concedio:
            por_filtro = [n for n in negadas if n[2] not in (NO_ALCANZA, Motivo.SIN_CONCESION)]
            alcance = any(n[2] == NO_ALCANZA for n in negadas)
            if por_filtro or not alcance:
                permiso, entidad, _ = (por_filtro or negadas)[0]
                permiso.verificar(entidad)  # niega, audita y responde 403
                raise Discrepancia("el evaluador niega y después concede la lectura")
    return ListaEsquemasCoeficientes(elementos=elementos)
