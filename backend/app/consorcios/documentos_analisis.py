"""Análisis de cada versión en cuarentena, en `cem-trabajos` (H2c §3, pasos 5 y 6).

1. Descarga el objeto con la clave de análisis (§3.1) y vuelve a comprobar tamaño, tipo y
   SHA-256 contra lo que guardó la carga.
2. En un PDF rechaza el contenido activo (`/JavaScript`, `/JS`, `/OpenAction`, `/AA`,
   `/Launch`, `/EmbeddedFile`, `/RichMedia`, XFA), el cifrado y lo que el analizador no puede
   leer.
3. Lo manda a `clamd`. Con firmas de más de `FIRMAS_VIGENTES` no aprueba nada.
4. Limpio → `disponible`. Detectado → `rechazado` con el motivo (auditado por `0035`) y, en la
   misma transacción, el trabajo `documentos.borrar_rechazado`, que borra el binario (por
   `VersionId` en un bucket con versiones) y se reintenta por su cuenta.

`documentos.limpiar_huerfanos` borra cada día los objetos que quedaron sin fila (una carga que
subió el archivo y no llegó a confirmar).

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

from app.consorcios.documentos import INTENTOS_VERIFICAR, TRABAJO_VERIFICAR, tipo_real
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
TRABAJO_BORRAR = "documentos.borrar_rechazado"
YA_BORRADO = frozenset({"NoSuchKey", "NoSuchVersion"})
"""Respuestas del almacén que, para una versión ya rechazada y confirmada, significan que el
borrado anterior llegó a hacerse."""
ANTIGUEDAD_HUERFANO = timedelta(days=1)
"""Un objeto sin fila más viejo que esto quedó de una carga que no confirmó; ninguna carga en
curso dura tanto."""
LOTE_CLAVES = 1000
TOPE_HUERFANOS = 20
"""Más objetos sin fila que esto en una corrida no son cargas fallidas sino una base atrasada o
equivocada (una restauración a un momento anterior, otra base): la tarea falla sin borrar
nada."""


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
    maximo_intentos=INTENTOS_VERIFICAR,
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
    lugar = _lugar(origen, v)
    # Un objeto ausente (o una clave mal configurada, que OCI suele responder igual) hace fallar
    # el trabajo: la versión sigue en cuarentena, se reintenta y `vigilar_cuarentena` avisa. Nunca
    # se resuelve como rechazo en silencio.
    contenido = s3.get_object(**lugar)["Body"].read()

    motivo = None
    firmas = None
    if (
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
    if resuelta and motivo is not None:
        # Un archivo rechazado nunca fue un documento: su binario no se conserva (§3, paso 5). El
        # borrado es otro trabajo, encolado en esta misma transacción: el rechazo y la tarea de
        # borrar quedan juntos o no queda ninguno, y el borrado se reintenta por su cuenta.
        cola.encolar(
            conn,
            TRABAJO_BORRAR,
            carga={"version": str(version)},
            consorcio_id=trabajo.consorcio_id,
            clave_unica=f"{TRABAJO_BORRAR}:{version}",
            maximo_intentos=INTENTOS_VERIFICAR,
        )
    return {"estado": "rechazado" if motivo else "disponible", "motivo": motivo}


def _lugar(origen: almacen.AlmacenDocumentos, v) -> dict[str, str]:
    lugar = {"Bucket": origen.bucket, "Key": v["clave_objeto"]}
    if v["version_objeto"]:
        lugar["VersionId"] = v["version_objeto"]
    return lugar


@cola.REGISTRO.tipo(
    TRABAJO_BORRAR,
    maximo_intentos=INTENTOS_VERIFICAR,
    retroceso_base=timedelta(minutes=1),
    retroceso_tope=timedelta(hours=1),
)
def borrar_rechazado(conn: Connection, trabajo: cola.Trabajo) -> dict[str, Any]:
    """Borra el binario de una versión rechazada (por `VersionId` si el bucket tiene versiones).
    Solo una versión `rechazado`: cualquier otra es un defecto y el trabajo muere sin borrar."""
    version = UUID(trabajo.carga["version"])
    v = _version(conn, version)
    if v is None or v["estado"] != "rechazado":
        raise cola.ErrorDefinitivo(f"la versión {version} no está rechazada: no se borra")
    origen = almacen.AlmacenDocumentos.desde_entorno(almacen.DOCUMENTOS_ANALISIS)
    try:
        origen.cliente().delete_object(**_lugar(origen, v))
    except ClientError as e:
        if e.response["Error"]["Code"] not in YA_BORRADO:
            raise
        return {"borrado": "ya no estaba"}
    return {"borrado": "hecho"}


@cola.REGISTRO.tipo("documentos.limpiar_huerfanos")
def limpiar_huerfanos(conn: Connection, trabajo: cola.Trabajo) -> dict[str, Any]:
    """Borra los objetos de `cem/` que no tienen fila en la base y tienen más de
    `ANTIGUEDAD_HUERFANO`: quedaron de una carga cuya transacción no confirmó después de subir
    (nunca se analizaron ni se van a servir).

    Es un borrado simple, sin `VersionId`: en un bucket con versiones (producción) la versión
    queda 35 días por la regla del bucket y se puede recuperar si la base estaba atrasada. Con más
    de `TOPE_HUERFANOS` no borra nada y falla."""
    origen = almacen.AlmacenDocumentos.desde_entorno(almacen.DOCUMENTOS_ANALISIS)
    s3 = origen.cliente()
    limite = datetime.now(UTC) - ANTIGUEDAD_HUERFANO
    paginas = s3.get_paginator("list_objects_v2").paginate(Bucket=origen.bucket, Prefix="cem/")
    candidatas = [
        o["Key"] for pagina in paginas for o in pagina.get("Contents", [])
        if o["LastModified"] < limite
    ]  # fmt: skip
    registradas: set[str] = set()
    for i in range(0, len(candidatas), LOTE_CLAVES):
        registradas.update(
            conn.execute(
                text("select nucleo.claves_registradas(:c)"),
                {"c": candidatas[i : i + LOTE_CLAVES]},
            ).scalars()
        )
    huerfanas = [c for c in candidatas if c not in registradas]
    if len(huerfanas) > TOPE_HUERFANOS:
        raise RuntimeError(
            f"{len(huerfanas)} objetos sin fila (tope {TOPE_HUERFANOS}): ¿la base está atrasada?"
            " No se borra nada."
        )
    for clave in huerfanas:
        s3.delete_object(Bucket=origen.bucket, Key=clave)
    if huerfanas:
        log.warning("objetos sin fila borrados", extra={"cantidad": len(huerfanas)})
    return {"revisadas": len(candidatas), "borradas": len(huerfanas)}


# Todos los días a las 04:45 UTC.
periodica("documentos.limpiar_huerfanos", timedelta(days=1), desfase=timedelta(hours=4, minutes=45))


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
