"""Sondas de salud (documento 13, §16.2). No pasan por el límite de tasa: tienen que responder
aunque Redis esté caído, justamente para informarlo."""

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from cem_contracts import VERSION_CONTRATO
from cem_contracts.salud import EstadoCola, EstadoDependencia, Salud, SaludDependencias
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.contexto import MotorDep
from app.db import transaccion
from app.plataforma import cola

router = APIRouter(tags=["plataforma"])
log = logging.getLogger("cem.salud")

RUTA_SALUD = "/health"
RUTA_DEPENDENCIAS = "/api/v1/health/deps"
SIN_LIMITE_DE_TASA = (RUTA_SALUD, RUTA_DEPENDENCIAS)

UMBRAL_ESPERA = 15 * 60
"""Segundos de espera del pendiente más viejo antes de alertar (documento 13, §16.3)."""


@router.get(RUTA_SALUD, response_model=Salud)
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


@dataclass(frozen=True)
class Comprobacion:
    db: EstadoDependencia
    redis: EstadoDependencia


class CacheDependencias:
    """Reutiliza la última comprobación de las dependencias durante `segundos` (revisión de
    seguridad H3, P3: la ruta es pública y sin límite, y cada llamada tomaba una conexión).

    El candado se tiene mientras se comprueba: las llamadas que llegan a la vez esperan a esa
    única comprobación y reciben su resultado, en lugar de abrir una cada una. Se guarda tanto el
    «ok» como la «falla»: una dependencia caída tampoco se vuelve a sondear antes de tiempo."""

    def __init__(self, segundos: int) -> None:
        self._segundos = segundos
        self._candado = threading.Lock()
        self._ultima: Comprobacion | None = None
        self._vence = 0.0

    def obtener(self, comprobar: Callable[[], Comprobacion]) -> Comprobacion:
        with self._candado:
            ahora = time.monotonic()
            if self._ultima is None or ahora >= self._vence:
                self._ultima = comprobar()
                self._vence = time.monotonic() + self._segundos
            return self._ultima


def _probar(nombre: str, prueba: Callable[[], object]) -> EstadoDependencia:
    try:
        prueba()
    except Exception as e:
        log.error("dependencia %s sin respuesta: %s: %s", nombre, type(e).__name__, e)
        return EstadoDependencia.FALLA
    return EstadoDependencia.OK


def _db(request: Request) -> None:
    motor = request.app.state.motor
    if motor is None:
        raise RuntimeError("CEM_DATABASE_URL sin configurar")
    with motor.connect() as conn:
        conn.execute(text("select 1")).scalar_one()


def _redis(request: Request) -> None:
    cliente = request.app.state.redis
    if cliente is None:
        raise RuntimeError("CEM_REDIS_URL no es un Redis")
    cliente.ping()


@router.get(
    RUTA_DEPENDENCIAS,
    response_model=SaludDependencias,
    responses={503: {"model": SaludDependencias, "description": "Alguna dependencia falla"}},
)
def dependencias(request: Request) -> JSONResponse:
    """PostgreSQL (`select 1` con `cem_app`) y Redis (`PING`)."""
    # El resultado se reutiliza durante `CEM_SALUD_CACHE_SEGUNDOS` (`CacheDependencias`); la
    # respuesta no cambia. El texto de arriba es la descripción del contrato: no se toca.
    c = request.app.state.salud_dependencias.obtener(
        lambda: Comprobacion(
            db=_probar("db", lambda: _db(request)), redis=_probar("redis", lambda: _redis(request))
        )
    )
    falla = EstadoDependencia.FALLA in (c.db, c.redis)
    cuerpo = SaludDependencias(
        estado=EstadoDependencia.FALLA if falla else EstadoDependencia.OK, db=c.db, redis=c.redis
    )
    return JSONResponse(cuerpo.model_dump(mode="json"), status_code=503 if falla else 200)
