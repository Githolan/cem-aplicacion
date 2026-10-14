"""Rutas de soporte: solo para operadores que pasaron por Cloudflare Access (`app/acceso.py`)."""

from cem_contracts.soporte import SesionSoporte
from fastapi import APIRouter, Depends

from app.acceso import OperadorDep, require_operador

# La dependencia del router cubre toda ruta nueva aunque se olvide pedir el operador.
router = APIRouter(
    prefix="/api/v1/soporte", tags=["soporte"], dependencies=[Depends(require_operador)]
)


@router.get("/sesion", response_model=SesionSoporte)
def sesion(operador: OperadorDep) -> SesionSoporte:
    """Quién es el operador según Access: comprueba la cadena Access → nginx → backend."""
    return SesionSoporte(identidad=operador.identidad, tipo=operador.tipo)
