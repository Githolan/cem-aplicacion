"""Tipos de trabajo, rutas del outbox y tareas periódicas de `plataforma` (documento 13, §12.4).

Los procesos `cem-trabajos` y `cem-planificador` importan este módulo al arrancar. Los
módulos de negocio agregarán aquí la importación de sus propias tareas a medida que existan.
"""

from datetime import timedelta

from sqlalchemy import Connection, text

from app.plataforma import cola
from app.plataforma.planificador import periodica

CONSERVACION = timedelta(days=30)


@cola.REGISTRO.tipo("plataforma.purgar_cola")
def purgar_cola(conn: Connection, trabajo: cola.Trabajo) -> dict[str, int]:
    """Borra trabajos hechos, eventos despachados y ventanas con más de 30 días."""
    borrados = conn.execute(
        text("select plataforma.purgar_cola(:a)"), {"a": CONSERVACION}
    ).scalar_one()
    return {"borrados": borrados}


# Todos los días a las 06:00 UTC (03:00 en Buenos Aires).
periodica("plataforma.purgar_cola", timedelta(days=1), desfase=timedelta(hours=6))
