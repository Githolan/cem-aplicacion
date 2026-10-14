"""Proceso `cem-trabajos`: consume la cola (documento 13, §12.1).

    python -m app.plataforma.trabajos

Se pueden correr varias réplicas: cada trabajo lo toma una sola a la vez. Ante SIGTERM
termina el lote en curso y sale; un trabajo que quede reservado por un proceso muerto
vuelve a tomarse cuando vence su plazo.

`CEM_TRABAJOS_LOTE` (por defecto 1) dice cuántos trabajos reserva cada toma. Tomar cuenta un
intento y fija el plazo de todos los del lote a la vez, pero se ejecutan de a uno: con un lote
de N, los que esperan turno detrás de uno lento pueden vencer, otro proceso los vuelve a tomar
(otro intento gastado) y, si era el último, quedan `muertos` sin haberse ejecutado nunca.
"""

import logging
import os
import signal
import socket
import threading
import time
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import Engine

from app import registros
from app.config import cargar
from app.db import crear_motor, transaccion
from app.plataforma import cola, latido

log = logging.getLogger("cem.trabajos")

LOTE_POR_DEFECTO = 1


def lote_configurado() -> int:
    """`CEM_TRABAJOS_LOTE`, un entero de 1 en adelante."""
    valor = os.environ.get("CEM_TRABAJOS_LOTE", str(LOTE_POR_DEFECTO))
    if not valor.isdecimal() or int(valor) < 1:
        raise ValueError(f"CEM_TRABAJOS_LOTE debe ser un entero mayor que 0, no {valor!r}")
    return int(valor)


class _ReservaPerdida(Exception):
    pass


class Trabajador:
    def __init__(
        self,
        motor: Engine,
        *,
        nombre: str | None = None,
        lote: int = LOTE_POR_DEFECTO,
        plazo: timedelta = timedelta(minutes=5),
        registro: cola.Registro = cola.REGISTRO,
    ) -> None:
        self.motor = motor
        self.nombre = nombre or f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:6]}"
        self.lote = lote
        self.plazo = plazo
        self.registro = registro

    def procesar_lote(self) -> int:
        """Toma y ejecuta hasta `lote` trabajos. Devuelve cuántos tomó."""
        with self.motor.begin() as conn:
            trabajos = cola.tomar(conn, self.nombre, self.lote, self.plazo)
        for trabajo in trabajos:
            self._ejecutar(trabajo)
        return len(trabajos)

    def _ejecutar(self, trabajo: cola.Trabajo) -> None:
        # Las líneas del trabajo llevan su id como id de correlación y su consorcio (§16.1).
        with registros.contexto(
            id_correlacion=str(trabajo.id),
            consorcio_id=str(trabajo.consorcio_id) if trabajo.consorcio_id else None,
            tipo_trabajo=trabajo.tipo,
            intento=trabajo.intentos,
        ):
            self._ejecutar_en_contexto(trabajo, time.perf_counter())

    def _ejecutar_en_contexto(self, trabajo: cola.Trabajo, inicio: float) -> None:
        tipo = self.registro.tipos.get(trabajo.tipo)
        try:
            if tipo is None:
                raise cola.ErrorDefinitivo(f"tipo de trabajo desconocido: {trabajo.tipo}")
            # El efecto en la base y la marca de hecho van en la misma transacción.
            with transaccion(self.motor, persona_id=None, consorcio_id=trabajo.consorcio_id) as c:
                resultado = tipo.manejador(c, trabajo)
                if not cola.terminar(c, trabajo, self.nombre, resultado):
                    raise _ReservaPerdida
        except _ReservaPerdida:
            log.warning(
                "reserva perdida, se descarta el resultado: %s %s", trabajo.tipo, trabajo.id
            )
            return
        except Exception as e:
            definitivo = isinstance(e, cola.ErrorDefinitivo)
            reintento = None if definitivo or tipo is None else tipo.retroceso(trabajo.intentos)
            with self.motor.begin() as conn:
                nuevo = cola.fallar(
                    conn, trabajo, self.nombre, f"{type(e).__name__}: {e}", reintento
                )
            nivel = logging.ERROR if nuevo == "muerto" else logging.WARNING
            log.log(nivel, "trabajo fallido (%s): %s %s intento %s",
                    nuevo, trabajo.tipo, trabajo.id, trabajo.intentos, exc_info=e,
                    extra={"campos": {"duracion_ms": _ms(inicio)}})  # fmt: skip
            return
        log.info("trabajo hecho: %s %s", trabajo.tipo, trabajo.id,
                 extra={"campos": {"duracion_ms": _ms(inicio)}})  # fmt: skip

    def ejecutar(self, detener: threading.Event, espera: float = 1.0) -> None:
        log.info("cem-trabajos %s en marcha", self.nombre)
        while not detener.is_set():
            try:
                tomados = self.procesar_lote()
            except Exception:
                log.exception("no se pudo procesar la cola; se reintenta")
                detener.wait(5)
                continue
            latido.latir()
            if tomados == 0:
                detener.wait(espera)
        log.info("cem-trabajos %s detenido", self.nombre)


def _ms(inicio: float) -> float:
    return round((time.perf_counter() - inicio) * 1000, 1)


def main() -> None:
    registros.configurar()
    import app.plataforma.tareas  # noqa: F401  (registra los tipos de trabajo)

    lote = lote_configurado()
    detener = threading.Event()
    for senal in (signal.SIGTERM, signal.SIGINT):
        signal.signal(senal, lambda *_: detener.set())
    Trabajador(crear_motor(cargar().database_url), lote=lote).ejecutar(detener)


if __name__ == "__main__":
    main()
