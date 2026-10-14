"""Proceso `cem-planificador` (documento 13, §12.3).

    python -m app.plataforma.planificador

Un solo planificador activo: el que obtiene `pg_advisory_lock` es el líder y los demás
esperan. En cada vuelta despacha el outbox y encola las tareas periódicas cuya ventana
empezó. Cada ventana se registra en `plataforma.ejecuciones_programadas` con clave
`(tipo, ventana)`, así que ni un reinicio ni un segundo planificador la repiten.
"""

import logging
import signal
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import Connection, Engine, text

from app.config import cargar
from app.db import crear_motor, transaccion
from app.plataforma import cola, latido, outbox

log = logging.getLogger("cem.planificador")

LIDERAZGO = "select pg_try_advisory_lock(hashtextextended('cem-planificador', 0))"
ORIGEN = datetime(2026, 1, 1, tzinfo=UTC)


@dataclass(frozen=True)
class TareaPeriodica:
    tipo: str
    periodo: timedelta
    desfase: timedelta = timedelta(0)
    """Corrimiento desde la medianoche UTC: diaria con desfase de 6 h = 06:00 UTC."""

    def ventana(self, ahora: datetime) -> datetime:
        """Inicio de la ventana que contiene `ahora`."""
        inicio = ORIGEN + self.desfase
        return inicio + (ahora - inicio) // self.periodo * self.periodo


TAREAS: list[TareaPeriodica] = []
"""Tareas periódicas de la aplicación. Cada módulo agrega las suyas con `periodica()`."""


def periodica(tipo: str, periodo: timedelta, desfase: timedelta = timedelta(0)) -> None:
    TAREAS.append(TareaPeriodica(tipo, periodo, desfase))


def programar(
    motor: Engine, ahora: datetime | None = None, tareas: list[TareaPeriodica] | None = None
) -> int:
    """Encola las ventanas nuevas. Devuelve cuántos trabajos encoló."""
    ahora = ahora or datetime.now(UTC)
    nuevas = 0
    with transaccion(motor, persona_id=None) as conn:
        for tarea in TAREAS if tareas is None else tareas:
            ventana = tarea.ventana(ahora)
            registrada = conn.execute(
                text(
                    "insert into plataforma.ejecuciones_programadas (tipo, ventana)"
                    " values (:t, :v) on conflict do nothing returning tipo"
                ),
                {"t": tarea.tipo, "v": ventana},
            ).first()
            if registrada is None:
                continue
            trabajo = cola.encolar(
                conn,
                tarea.tipo,
                carga={"ventana": ventana.isoformat()},
                clave_unica=f"{tarea.tipo}@{ventana.isoformat()}",
            )
            conn.execute(
                text(
                    "update plataforma.ejecuciones_programadas set trabajo_id = :j"
                    " where tipo = :t and ventana = :v"
                ),
                {"j": trabajo, "t": tarea.tipo, "v": ventana},
            )
            nuevas += 1
    return nuevas


class Planificador:
    def __init__(self, motor: Engine, *, intervalo: float = 5.0, lote_outbox: int = 100) -> None:
        self.motor = motor
        self.intervalo = intervalo
        self.lote_outbox = lote_outbox
        self.es_lider = False

    def vuelta(self, ahora: datetime | None = None) -> None:
        while outbox.despachar(self.motor, self.lote_outbox) == self.lote_outbox:
            pass
        programar(self.motor, ahora)

    def ejecutar(self, detener: threading.Event) -> None:
        # La conexión del liderazgo queda abierta mientras el proceso viva: si se cae, el
        # bloqueo se libera, la comprobación de cada vuelta falla y el proceso sale para que
        # Coolify lo reinicie.
        with self.motor.connect() as lider:
            try:
                self._esperar_liderazgo(lider, detener)
                while not detener.is_set():
                    lider.execute(text("select 1"))
                    lider.commit()
                    try:
                        self.vuelta()
                    except Exception:
                        log.exception("vuelta del planificador fallida; se reintenta")
                    latido.latir()
                    detener.wait(self.intervalo)
            finally:
                # La conexión vuelve al pool: sin esto el bloqueo seguiría tomado.
                if self.es_lider:
                    lider.execute(text("select pg_advisory_unlock_all()"))
                    lider.commit()
                    self.es_lider = False
        log.info("cem-planificador detenido")

    def _esperar_liderazgo(self, lider: Connection, detener: threading.Event) -> None:
        while not detener.is_set():
            self.es_lider = lider.execute(text(LIDERAZGO)).scalar_one()
            lider.commit()
            if self.es_lider:
                log.info("cem-planificador es el líder")
                return
            log.info("otro planificador es el líder; en espera")
            latido.latir()
            detener.wait(self.intervalo)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    # Con `python -m` este archivo corre como __main__, que es otra copia del módulo: las
    # tareas que registra `tareas` quedan en `app.plataforma.planificador`, y de ahí se
    # toma la clase para que lea esa lista.
    import app.plataforma.tareas  # noqa: F401  (registra rutas y tareas periódicas)
    from app.plataforma.planificador import Planificador as PlanificadorRegistrado

    detener = threading.Event()
    for senal in (signal.SIGTERM, signal.SIGINT):
        signal.signal(senal, lambda *_: detener.set())
    PlanificadorRegistrado(crear_motor(cargar().database_url)).ejecutar(detener)


if __name__ == "__main__":
    main()
