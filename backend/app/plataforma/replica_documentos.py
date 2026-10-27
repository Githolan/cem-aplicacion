"""Réplica diaria de los documentos y control de tamaño del bucket de copias (H2c §6 del
repositorio CEM; documento 13, §17.2).

Réplica (`plataforma.replicar_documentos`, encolada por `cem-planificador` a las 05:15 UTC y
corrida por `cem-trabajos`):

1. Lee con `cem_backup` (`CEM_DATABASE_URL_BACKUP`) las versiones `disponible`: nunca una en
   cuarentena ni una rechazada, que no son documentos.
2. Por cada una, si `documentos/<clave_objeto>.age` ya está en el bucket de copias con el mismo
   SHA-256 del contenido (metadato `sha256-claro`), no se vuelve a copiar. Si está con otro, es un
   defecto: el trabajo falla (el bucket no admite sobrescribir).
3. Si no está, lee el binario con la clave de análisis de documentos
   (`CEM_DOCUMENTOS_ANALISIS_S3_*`, `CEM_DOCUMENTOS_BUCKET`; por `VersionId` si el bucket tiene
   versiones), comprueba tamaño y SHA-256 contra la fila, lo cifra con `age` a
   `CEM_COPIAS_CLAVE_PUBLICA` como el resto del bucket de copias, lo sube con la clave de copias
   y relee el objeto comparando el SHA-256 del cifrado.

El prefijo `documentos/` no tiene regla de borrado por antigüedad; borrar no es cosa de este
código. Cada corrida se corta a `DURACION_MAXIMA_REPLICA`, por debajo del plazo de reserva del
trabajador (5 minutos): lo que falte sigue en la corrida del día siguiente y queda en el
resultado como `pendientes`.

Control de tamaño (`plataforma.controlar_tamano_copias`, 05:45 UTC): suma lo que ocupan `base/`,
`wal/`, `wal-parcial/` y `documentos/` y lo compara con `CEM_COPIAS_PRESUPUESTO_BYTES`
(obligatoria para este control, sin valor por defecto). Por encima, ERROR en los registros y
`down` al push de Kuma de `CEM_COPIAS_TAMANO_AVISO_URL` (opcional); por debajo, `up`. Informa
también `diarias/` y `mensuales/`, que no entran en la suma.
"""

import hashlib
import logging
import os
import tempfile
import time
from collections.abc import Mapping
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from app.plataforma import almacen, copias

log = logging.getLogger("cem.copias")

PREFIJO = "documentos/"
DURACION_MAXIMA_REPLICA = timedelta(minutes=3)
"""Tope de cada corrida, por debajo de los 5 minutos de reserva de `cem-trabajos`."""

PREFIJOS_PRESUPUESTO = ("base/", "wal/", "wal-parcial/", "documentos/")
PREFIJOS_INFORMADOS = ("diarias/", "mensuales/")
VARIABLE_PRESUPUESTO = "CEM_COPIAS_PRESUPUESTO_BYTES"
VARIABLE_AVISO_TAMANO = "CEM_COPIAS_TAMANO_AVISO_URL"

VERSIONES = text(
    "select id, clave_objeto, version_objeto, sha256, tamano from nucleo.version_documento"
    " where estado = 'disponible' order by subida_en, id"
)


def clave_replica(clave_objeto: str) -> str:
    return f"{PREFIJO}{clave_objeto}.age"


def replicar(
    cfg: copias.ConfigCopias,
    origen: almacen.AlmacenDocumentos,
    duracion_maxima: timedelta = DURACION_MAXIMA_REPLICA,
) -> dict[str, int]:
    """Copia al bucket de copias las versiones `disponible` que todavía no están."""
    motor = create_engine(cfg.url_backup, poolclass=NullPool)
    try:
        with motor.connect() as conn:
            versiones = conn.execute(VERSIONES).all()
    finally:
        motor.dispose()
    documentos = origen.cliente()
    destino = copias.cliente_s3(cfg)
    limite = time.monotonic() + duracion_maxima.total_seconds()
    copiadas = ya_estaban = revisadas = 0
    with tempfile.TemporaryDirectory(prefix="cem-replica-") as carpeta:
        for v in versiones:
            if revisadas and time.monotonic() > limite:
                break  # al menos una por corrida: siempre avanza
            revisadas += 1
            clave = clave_replica(v.clave_objeto)
            existente = copias.metadatos(destino, cfg.bucket, clave)
            if existente is not None:
                if existente.get("sha256-claro") != v.sha256:
                    raise copias.ErrorCopia(f"{clave} ya está en la réplica con otro contenido")
                ya_estaban += 1
                continue
            lugar = {"Bucket": origen.bucket, "Key": v.clave_objeto}
            if v.version_objeto:
                lugar["VersionId"] = v.version_objeto
            contenido = documentos.get_object(**lugar)["Body"].read()
            if len(contenido) != v.tamano or hashlib.sha256(contenido).hexdigest() != v.sha256:
                raise copias.ErrorCopia(
                    f"la versión {v.id} no coincide con su fila en el almacén de documentos"
                )
            claro = Path(carpeta) / "claro"
            cifrado = Path(carpeta) / "cifrado.age"
            claro.write_bytes(contenido)
            copias.cifrar(claro, cfg.clave_publica, cifrado)
            suma = copias.sha256(cifrado)
            with cifrado.open("rb") as cuerpo:
                destino.put_object(
                    Bucket=cfg.bucket, Key=clave, Body=cuerpo,
                    Metadata={"sha256": suma, "sha256-claro": v.sha256, "version": str(v.id)},
                )  # fmt: skip
            copias.verificar_objeto(destino, cfg.bucket, clave, suma)
            claro.unlink()
            cifrado.unlink()
            copiadas += 1
    pendientes = len(versiones) - revisadas
    if pendientes:
        log.warning("réplica de documentos incompleta: sigue mañana",
                    extra={"campos": {"pendientes": pendientes}})  # fmt: skip
    return {
        "versiones": len(versiones),
        "copiadas": copiadas,
        "ya_estaban": ya_estaban,
        "pendientes": pendientes,
    }


def presupuesto(entorno: Mapping[str, str] = os.environ) -> int:
    valor = entorno.get(VARIABLE_PRESUPUESTO, "")
    if not valor.isdecimal() or int(valor) < 1:
        raise copias.ErrorCopia(
            f"{VARIABLE_PRESUPUESTO} tiene que ser un entero de bytes mayor que 0"
        )
    return int(valor)


def tamano_prefijo(s3, bucket: str, prefijo: str) -> int:
    paginas = s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefijo)
    return sum(o["Size"] for pagina in paginas for o in pagina.get("Contents", []))


def controlar_tamano(
    cfg: copias.ConfigCopias, entorno: Mapping[str, str] = os.environ
) -> dict[str, Any]:
    """Suma de los prefijos de `PREFIJOS_PRESUPUESTO` contra el presupuesto, y aviso a Kuma."""
    tope = presupuesto(entorno)
    s3 = copias.cliente_s3(cfg)
    tamanos = {p: tamano_prefijo(s3, cfg.bucket, p) for p in PREFIJOS_PRESUPUESTO}
    informados = {p: tamano_prefijo(s3, cfg.bucket, p) for p in PREFIJOS_INFORMADOS}
    total = sum(tamanos.values())
    excedido = total > tope
    detalle = ", ".join(f"{p} {b}" for p, b in tamanos.items())
    if excedido:
        log.error("el bucket de copias pasa su presupuesto: %s de %s bytes (%s)",
                  total, tope, detalle)  # fmt: skip
        mensaje = f"copias sobre el presupuesto: {total} de {tope} bytes ({detalle})"
    else:
        mensaje = f"copias dentro del presupuesto: {total} de {tope} bytes ({detalle})"
    aviso = copias.avisar_estado(
        VARIABLE_AVISO_TAMANO, "down" if excedido else "up", mensaje, entorno
    )
    return {
        "bytes": tamanos,
        "informados": informados,
        "total": total,
        "presupuesto": tope,
        "excedido": excedido,
        "aviso": aviso,
    }
