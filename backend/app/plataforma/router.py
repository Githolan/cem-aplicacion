from cem_contracts import VERSION_CONTRATO
from cem_contracts.salud import Salud
from fastapi import APIRouter, Request

router = APIRouter(tags=["plataforma"])


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
