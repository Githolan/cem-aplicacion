"""Tipos de trabajo, rutas del outbox y tareas periódicas de `plataforma` (documento 13, §12.4).

Los procesos `cem-trabajos` y `cem-planificador` importan este módulo al arrancar. Los
módulos de negocio agregarán aquí la importación de sus propias tareas a medida que existan.
"""

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Connection, text

from app.identidad import sesiones
from app.plataforma import cola, copias, correo, outbox
from app.plataforma.planificador import periodica

CONSERVACION = timedelta(days=30)


@cola.REGISTRO.tipo("plataforma.purgar_cola")
def purgar_cola(conn: Connection, trabajo: cola.Trabajo) -> dict[str, int]:
    """Limpieza diaria: trabajos hechos, eventos despachados y ventanas con más de 30 días, las
    respuestas de `Idempotency-Key` vencidas (documento 13, §7.4) y las sesiones y enlaces de
    acceso que dejaron de servir hace más de 90 días (§15.6); los códigos por correo se borran
    con su enlace (`on delete cascade`, migración 0006)."""
    borrados = conn.execute(
        text("select plataforma.purgar_cola(:a)"), {"a": CONSERVACION}
    ).scalar_one()
    vencidas = conn.execute(text("select plataforma.purgar_idempotencia()")).scalar_one()
    identidad = conn.execute(
        text("select sesiones, enlaces from nucleo.purgar_identidad(:r)"),
        {"r": sesiones.RETENCION_ACCESO},
    ).one()
    return {
        "borrados": borrados,
        "idempotencia_vencidas": vencidas,
        "sesiones_purgadas": identidad.sesiones,
        "enlaces_purgados": identidad.enlaces,
    }


# Todos los días a las 06:00 UTC (03:00 en Buenos Aires).
periodica("plataforma.purgar_cola", timedelta(days=1), desfase=timedelta(hours=6))


@cola.REGISTRO.tipo("plataforma.copia_diaria")
def copia_diaria(conn: Connection, trabajo: cola.Trabajo) -> dict[str, Any]:
    """Copia de la base `cem` volcada, verificada, cifrada y subida (documento 13, §17), y
    avisada al monitor Push de Kuma para que una noche sin copia dé alerta."""
    ventana = datetime.fromisoformat(trabajo.carga["ventana"])
    resumen = copias.copia_diaria(copias.ConfigCopias.desde_entorno(), ventana)
    return resumen | {"aviso": copias.avisar_copia(resumen)}


# Todos los días a las 03:30 UTC, después de la ventana de Coolify (§17.2).
periodica("plataforma.copia_diaria", timedelta(days=1), desfase=timedelta(hours=3, minutes=30))


# Correo transaccional (§12.4): un módulo publica `correo.solicitado` con `correo.solicitar()`
# y el despachador lo convierte en `notificar.correo`, que envía `cem-trabajos`.
cola.REGISTRO.tipo(
    correo.TIPO,
    maximo_intentos=correo.MAXIMO_INTENTOS,
    retroceso_base=correo.RETROCESO_BASE,
    retroceso_tope=correo.RETROCESO_TOPE,
)(correo.notificar_correo)
outbox.ruta(correo.EVENTO, correo.TIPO)
