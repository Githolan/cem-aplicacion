"""Tipos de trabajo de prueba y procesos que se lanzan aparte (multiprocessing, spawn).

Las tablas `plataforma.prueba_*` las crea la fixture `cola_vacia` de `test_cola.py`.
"""

import time
from datetime import timedelta

from sqlalchemy import Connection, text

from app.db import crear_motor
from app.plataforma import cola
from app.plataforma.trabajos import Trabajador

SIN_ESPERA = timedelta(0)


@cola.REGISTRO.tipo("prueba.registrar")
def registrar(conn: Connection, trabajo: cola.Trabajo) -> dict[str, int]:
    conn.execute(
        text(
            "insert into plataforma.prueba_efectos (trabajo_id, ambito)"
            " values (:t, plataforma.consorcio_actual())"
        ),
        {"t": trabajo.id},
    )
    return {"ok": 1}


@cola.REGISTRO.tipo("prueba.fallar", maximo_intentos=2, retroceso_base=SIN_ESPERA)
def fallar(conn: Connection, trabajo: cola.Trabajo) -> None:
    raise RuntimeError("falla de prueba")


@cola.REGISTRO.tipo("prueba.invalido", retroceso_base=SIN_ESPERA)
def invalido(conn: Connection, trabajo: cola.Trabajo) -> None:
    raise cola.ErrorDefinitivo("datos inválidos")


@cola.REGISTRO.tipo("prueba.dormir")
def dormir(conn: Connection, trabajo: cola.Trabajo) -> None:
    # Solo el primer intento se cuelga: es el que la prueba mata con kill -9.
    if trabajo.intentos == 1:
        time.sleep(trabajo.carga["segundos"])
    registrar(conn, trabajo)


@cola.REGISTRO.tipo("prueba.correo", maximo_intentos=5, retroceso_base=SIN_ESPERA)
def correo(conn: Connection, trabajo: cola.Trabajo) -> None:
    """Proveedor de correo simulado: falla mientras `prueba_proveedor.caido` sea verdadero.

    El envío es idempotente por evento, como lo será el envío real (§12.2).
    """
    if conn.execute(text("select caido from plataforma.prueba_proveedor")).scalar_one():
        raise ConnectionError("proveedor de correo caído")
    conn.execute(
        text(
            "insert into plataforma.prueba_correos (evento_id) values (:e) on conflict do nothing"
        ),
        {"e": trabajo.carga["evento_id"]},
    )


def vaciar_cola(url: str, plazo_segundos: float = 300) -> None:
    """Proceso trabajador: procesa hasta que la cola queda vacía dos veces seguidas."""
    trabajador = Trabajador(crear_motor(url), lote=10, plazo=timedelta(seconds=plazo_segundos))
    vacias = 0
    while vacias < 2:
        vacias = vacias + 1 if trabajador.procesar_lote() == 0 else 0
