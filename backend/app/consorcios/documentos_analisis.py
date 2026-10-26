"""Análisis de cada versión en cuarentena, en `cem-trabajos` (H2c §3, pasos 5 y 6).

1. Descarga el objeto con la clave de análisis (§3.1) y vuelve a comprobar tamaño, tipo y
   SHA-256 contra lo que guardó la carga.
2. En un PDF rechaza el contenido activo (`/JavaScript`, `/JS`, `/OpenAction`, `/AA`,
   `/Launch`, `/EmbeddedFile`, `/RichMedia`, XFA), el cifrado y lo que el analizador no puede
   leer.
3. Lo manda a `clamd`. Con firmas de más de `FIRMAS_VIGENTES` no aprueba nada.
4. Limpio → `disponible`. Detectado → `rechazado` con el motivo (auditado por `0035`) y el
   binario borrado (por `VersionId` en un bucket con versiones).

Si no se puede completar (`clamd` caído, firmas viejas, almacén que falla), el trabajo falla, la
versión sigue en cuarentena y la cola reintenta con espera creciente; `vigilar_cuarentena` avisa
a Kuma si alguna queda demorada o si las firmas envejecen.
"""

import hashlib
import io
import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from botocore.exceptions import ClientError
from pypdf import PdfReader
from pypdf.errors import PyPdfError
from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject, NameObject
from sqlalchemy import Connection, text

from app.consorcios.documentos import TRABAJO_VERIFICAR, tipo_real
from app.plataforma import almacen, antivirus, cola, copias
from app.plataforma.planificador import periodica

log = logging.getLogger("cem.documentos")

FIRMAS_VIGENTES = timedelta(hours=48)
"""Firmas más viejas: `freshclam` está fallando y no se aprueba nada (§3, paso 6)."""
DEMORA_AVISO = timedelta(hours=2)
"""Una versión en cuarentena por más tiempo es un análisis que no se completa."""
VARIABLE_AVISO = "CEM_DOCUMENTOS_AVISO_URL"
"""Push del monitor de Kuma del análisis. Opcional: sin ella el análisis corre igual."""

CLAVES_ACTIVAS = frozenset(
    {"/JavaScript", "/JS", "/OpenAction", "/AA", "/Launch", "/EmbeddedFile", "/EmbeddedFiles",
     "/RichMedia", "/XFA"}
)  # fmt: skip
ACCIONES_ACTIVAS = frozenset({"/JavaScript", "/Launch", "/RichMediaExecute"})
OBJETO_AUSENTE = frozenset({"NoSuchKey", "NoSuchVersion", "404"})


class FirmasViejas(RuntimeError):
    """Las firmas de `clamd` pasan de 48 horas: se reintenta, nunca se aprueba."""


def motivo_pdf(contenido: bytes) -> str | None:
    """`pdf_cifrado`, `pdf_mal_formado`, `contenido_activo` o None.

    Recorre todos los objetos alcanzables desde el catálogo (también los que están dentro de
    flujos de objetos comprimidos, que una búsqueda en los bytes crudos no ve)."""
    try:
        lector = PdfReader(io.BytesIO(contenido), strict=False)
        if lector.is_encrypted:
            return "pdf_cifrado"
        pendientes: list[Any] = [lector.trailer]
        vistos: set[tuple[int, int]] = set()
        while pendientes:
            objeto = pendientes.pop()
            if isinstance(objeto, IndirectObject):
                ref = (objeto.idnum, objeto.generation)
                if ref in vistos:
                    continue
                vistos.add(ref)
                objeto = objeto.get_object()
            if isinstance(objeto, DictionaryObject):
                if CLAVES_ACTIVAS.intersection(objeto.keys()):
                    return "contenido_activo"
                accion = objeto.get("/S")
                if isinstance(accion, NameObject) and accion in ACCIONES_ACTIVAS:
                    return "contenido_activo"
                pendientes.extend(objeto.values())
            elif isinstance(objeto, ArrayObject):
                pendientes.extend(objeto)
    # `PyPdfError` incluye `PdfReadError` y `LimitReachedError` (un flujo que se expande más allá
    # del límite de pypdf, una bomba de compresión): lo que no se puede leer se rechaza (§3).
    except (PyPdfError, ValueError, KeyError, TypeError, RecursionError) as e:
        log.info("pdf ilegible", extra={"error": type(e).__name__})
        return "pdf_mal_formado"
    return None


def _version(conn: Connection, version: UUID):
    return (
        conn.execute(
            text("select * from nucleo.version_para_analizar(:v)"), {"v": version}
        ).mappings().one_or_none()
    )  # fmt: skip


@cola.REGISTRO.tipo(
    TRABAJO_VERIFICAR,
    maximo_intentos=10,
    retroceso_base=timedelta(minutes=1),
    retroceso_tope=timedelta(hours=1),
)
def verificar(conn: Connection, trabajo: cola.Trabajo) -> dict[str, Any]:
    version = UUID(trabajo.carga["version"])
    v = _version(conn, version)
    if v is None:
        raise cola.ErrorDefinitivo(f"la versión {version} no existe en el consorcio del trabajo")
    if v["estado"] != "en_cuarentena":
        return {"estado": v["estado"], "repetido": True}
    origen = almacen.AlmacenDocumentos.desde_entorno(almacen.DOCUMENTOS_ANALISIS)
    s3 = origen.cliente()
    lugar = {"Bucket": origen.bucket, "Key": v["clave_objeto"]}
    if v["version_objeto"]:
        lugar["VersionId"] = v["version_objeto"]
    try:
        contenido = s3.get_object(**lugar)["Body"].read()
    except ClientError as e:
        if e.response["Error"]["Code"] not in OBJETO_AUSENTE:
            raise
        # El binario ya no está (p. ej. un rechazo anterior que borró el objeto pero no llegó a
        # confirmar): nunca se va a poder servir. Se resuelve como rechazado en vez de reintentar
        # para siempre.
        contenido = None

    motivo = None
    firmas = None
    if contenido is None:
        motivo = "no_coincide"
    elif (
        len(contenido) != v["tamano"]
        or hashlib.sha256(contenido).hexdigest() != v["sha256"]
        or tipo_real(contenido) is None
        or tipo_real(contenido).value != v["tipo"]
    ):
        motivo = "no_coincide"
    elif v["tipo"] == "application/pdf":
        motivo = motivo_pdf(contenido)
    if motivo is None:
        firmas = antivirus.firmas()
        if datetime.now(UTC) - firmas.fecha > FIRMAS_VIGENTES:
            raise FirmasViejas(f"firmas de {firmas.fecha.isoformat()}: no se aprueba nada")
        if antivirus.analizar(contenido) is not None:
            motivo = "virus"

    resuelta = conn.execute(
        text("select nucleo.resolver_version(:v, :m, :av, :f)"),
        {"v": version, "m": motivo, "av": firmas.antivirus if firmas else None,
         "f": firmas.fecha if firmas else None},
    ).scalar_one()  # fmt: skip
    if resuelta and motivo is not None and contenido is not None:
        # Un archivo rechazado nunca fue un documento: su binario no se conserva (§3, paso 5).
        s3.delete_object(**lugar)
    return {"estado": "rechazado" if motivo else "disponible", "motivo": motivo}


@cola.REGISTRO.tipo("documentos.vigilar_cuarentena")
def vigilar_cuarentena(conn: Connection, trabajo: cola.Trabajo) -> dict[str, Any]:
    """Avisa a Kuma: `up` si ninguna versión lleva más de `DEMORA_AVISO` en cuarentena y las
    firmas están al día; `down` con el motivo si no. Sin la variable del aviso, solo informa."""
    demoradas = conn.execute(
        text("select nucleo.versiones_demoradas(:d)"), {"d": DEMORA_AVISO}
    ).scalar_one()
    problemas = []
    if demoradas:
        problemas.append(f"{demoradas} versiones en cuarentena hace más de {DEMORA_AVISO}")
    try:
        f = antivirus.firmas()
        if datetime.now(UTC) - f.fecha > FIRMAS_VIGENTES:
            problemas.append(f"firmas de {f.fecha.isoformat()}")
    except antivirus.AntivirusNoDisponible as e:
        problemas.append(str(e))
    resumen = {"demoradas": demoradas, "problemas": problemas}
    url = os.environ.get(VARIABLE_AVISO)
    if not url:
        return resumen | {"aviso": "sin CEM_DOCUMENTOS_AVISO_URL"}
    estado = "down" if problemas else "up"
    # Un aviso que Kuma no confirma hace fallar el trabajo: el monitor alerta igual si deja de
    # recibir latidos.
    copias.empujar(copias.url_push(url, VARIABLE_AVISO, estado, "; ".join(problemas) or "ok"))
    return resumen | {"aviso": estado}


# Cada 5 minutos; el monitor de Kuma alerta si deja de recibirlo o recibe `down`.
periodica("documentos.vigilar_cuarentena", timedelta(minutes=5))
