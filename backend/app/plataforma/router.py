from cem_contracts import VERSION_CONTRATO
from cem_contracts.salud import EstadoCola, Salud
from fastapi import APIRouter, Request

from app.contexto import MotorDep
from app.db import transaccion
from app.plataforma import cola

router = APIRouter(tags=["plataforma"])

UMBRAL_ESPERA = 15 * 60
"""Segundos de espera del pendiente más viejo antes de alertar (documento 13, §16.3)."""


@router.get("/health", response_model=Salud)
def salud(request: Request) -> Salud:
    cfg = request.app.state.config
    return Salud(
        estado="ok",
        version=cfg.version,
        sha_imagen=cfg.sha_imagen,
        sha_contratos=cfg.sha_contratos,
        version_contrato=VERSION_CONTRATO,
    )


@router.get("/api/v1/health/cola", response_model=EstadoCola)
def salud_cola(motor: MotorDep) -> EstadoCola:
    """Totales de la cola de trabajos para Kuma; sin datos de ningún consorcio."""
    with transaccion(motor, persona_id=None) as conn:
        e = cola.estado(conn)
    alerta = e.muertos > 0 or e.segundos_mas_viejo > UMBRAL_ESPERA
    return EstadoCola(
        estado="alerta" if alerta else "ok",
        pendientes=e.pendientes,
        en_curso=e.en_curso,
        muertos=e.muertos,
        segundos_mas_viejo=round(e.segundos_mas_viejo, 1),
    )
