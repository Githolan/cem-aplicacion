"""Tipos de trabajo, rutas del outbox y tareas periódicas de `plataforma` (documento 13, §12.4).

Los procesos `cem-trabajos` y `cem-planificador` importan este módulo al arrancar. Los
módulos de negocio agregarán aquí la importación de sus propias tareas a medida que existan.
"""

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Connection, text

from app.consorcios import documentos_analisis, pagos  # noqa: F401 (registran sus tipos)
from app.identidad import sesiones
from app.plataforma import almacen, cola, copias, correo, indices, outbox, replica_documentos
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


@cola.REGISTRO.tipo(
    "plataforma.replicar_documentos",
    # Cada intento copia hasta 3 minutos y sigue donde quedó el anterior: seis intentos, con
    # esperas de 1, 2, 4, 8 y 16 minutos entre ellos, unos 50 minutos en total, antes de quedar
    # muerto y a la vista del monitor de la cola.
    maximo_intentos=6,
    retroceso_base=timedelta(minutes=1),
)
def replicar_documentos(conn: Connection, trabajo: cola.Trabajo) -> dict[str, Any]:
    """Réplica diaria de las versiones `disponible` al prefijo `documentos/` del bucket de copias
    (H2c §6, documento 13 §17.2). Lee con `cem_backup` y la clave de análisis de documentos;
    escribe con la clave de copias."""
    return replica_documentos.replicar(
        copias.ConfigCopias.desde_entorno(),
        almacen.AlmacenDocumentos.desde_entorno(almacen.DOCUMENTOS_ANALISIS),
    )


# Todos los días a las 05:15 UTC: después de la copia de las 03:30, del control de índices de las
# 04:15 y de la limpieza de objetos sin fila de las 04:45 (no compite por las dos conexiones de
# cem_backup con la copia ni por el almacén con la limpieza), y antes de la purga de las 06:00.
periodica(
    "plataforma.replicar_documentos", timedelta(days=1), desfase=timedelta(hours=5, minutes=15)
)


@cola.REGISTRO.tipo("plataforma.controlar_tamano_copias")
def controlar_tamano_copias(conn: Connection, trabajo: cola.Trabajo) -> dict[str, Any]:
    """Tamaño de `base/`, `wal/`, `wal-parcial/` y `documentos/` contra
    `CEM_COPIAS_PRESUPUESTO_BYTES`, con aviso a Kuma (H2c §6)."""
    return replica_documentos.controlar_tamano(copias.ConfigCopias.desde_entorno())


# Todos los días a las 05:45 UTC, después de la réplica: mide lo que dejó.
periodica(
    "plataforma.controlar_tamano_copias", timedelta(days=1), desfase=timedelta(hours=5, minutes=45)
)


@cola.REGISTRO.tipo("plataforma.verificar_indices")
def verificar_indices(conn: Connection, trabajo: cola.Trabajo) -> dict[str, Any]:
    """E58: cada índice GiST contra su tabla, `REINDEX` si difieren y aviso a Kuma. Usa sus
    propias conexiones (`cem_backup` para leer, `cem_app` en autocommit para reconstruir)."""
    return indices.controlar(conn.engine.url)


# Todos los días a las 04:15 UTC, después de la copia de las 03:30.
periodica("plataforma.verificar_indices", timedelta(days=1), desfase=timedelta(hours=4, minutes=15))


TOPE_VENCIMIENTOS = 200
"""Mandatos por corrida: cada uno revoca y audita en milisegundos, así que el lote termina muy
dentro del plazo de cinco minutos del trabajador; un atraso mayor se reparte entre corridas."""


@cola.REGISTRO.tipo("consorcio.revocar_por_vencimiento")
def revocar_por_vencimiento(conn: Connection, trabajo: cola.Trabajo) -> dict[str, int]:
    """E15 (criterio 3, RN22): un mandato que venció por su fecha revoca las sesiones del
    mandatario, como la baja. Idempotente por la marca de `0017`."""
    procesados = conn.execute(
        text("select nucleo.revocar_sesiones_por_vencimiento(:t)"), {"t": TOPE_VENCIMIENTOS}
    ).scalar_one()
    return {"mandatos_procesados": procesados}


# Cada hora, a los 5 minutos: el cambio de día de cualquier zona horaria (también las de
# desfase :30 o :45) espera menos de una hora a la corrida siguiente.
periodica("consorcio.revocar_por_vencimiento", timedelta(hours=1), desfase=timedelta(minutes=5))


# Correo transaccional (§12.4): un módulo publica `correo.solicitado` con `correo.solicitar()`
# y el despachador lo convierte en `notificar.correo`, que envía `cem-trabajos`.
cola.REGISTRO.tipo(
    correo.TIPO,
    maximo_intentos=correo.MAXIMO_INTENTOS,
    retroceso_base=correo.RETROCESO_BASE,
    retroceso_tope=correo.RETROCESO_TOPE,
)(correo.notificar_correo)
outbox.ruta(correo.EVENTO, correo.TIPO)
