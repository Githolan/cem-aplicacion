"""Importación de la liquidación del administrador (H5-1; diseño H5 §4 y §5; migración `0113`).

- **`POST …/liquidaciones-importadas`** (`liquidacion.importar`): pide importar una versión
  `disponible` de un documento de la categoría `liquidaciones_del_administrador`. Encola la lectura
  en la misma transacción.
- **Lectura en `cem-trabajos`** (`liquidacion.leer`): palabras con su recuadro (`palabras_pdf`),
  formato Mis Expensas (`mis_expensas`) y cada dato como campo preliminar con su renglón de origen.
  Si el formato no se reconoce y el PDF tiene texto, lo lee el modelo (`lectura_ia`, H5-2), dentro
  del tope mensual de gasto que lleva la base (`0114`, D4). Si tampoco así se lee, la importación
  queda `no_reconocida` con el motivo.
- **`GET …/liquidaciones-importadas/{id}`** (`liquidacion.importar`): la importación, sus
  diferencias y sus campos, con su estado. No es una lectura oficial: lo oficial sale solo de
  `nucleo.campos_oficiales_liquidacion` (RN30).
"""

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

from cem_contracts.errores import CodigoError
from cem_contracts.importacion_liquidacion import (
    CampoPreliminar,
    DiferenciaLeida,
    ImportacionCreada,
    ImportacionLiquidacion,
    ImportacionNueva,
)
from fastapi import APIRouter, Depends, Response
from pdfplumber.utils.exceptions import PdfminerException
from psycopg.errors import InsufficientPrivilege
from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError

from app import llm
from app.autorizacion.evaluador import Entidad
from app.autorizacion.requiere import NO_ENCONTRADO, Permiso, requiere
from app.consorcios import lectura_comprobantes
from app.consorcios.padron import Discrepancia
from app.contexto import MotorDep
from app.db import transaccion
from app.errores import ErrorApi, sin_cache
from app.liquidacion import lectura_ia, mis_expensas, palabras_pdf
from app.plataforma import almacen, cola
from app.plataforma.idempotencia import IdempotenciaDep, responder_una_vez

log = logging.getLogger("cem.liquidacion")

ACCION = "liquidacion.importar"
TRABAJO_LEER = "liquidacion.leer"
"""El de `TRABAJO` en `0113`."""
FORMATO = "mis_expensas"
CONFIANZA_DETERMINISTA = Decimal("1.00")
"""Un dato leído por posición y verificado por las sumas; la IA (H5-2) informa la suya."""
INTENTOS_LEER = 5
"""Una falla pasajera (el almacén, un reinicio) se reintenta; agotados, la lectura informa
`falla_de_lectura`. Un PDF que no se lee o sin el formato no es una falla: se guarda."""
LARGO_MOTIVO = 500
"""El de `nucleo.importacion_liquidacion.motivo`."""
PDF_ILEGIBLE = "El archivo no se pudo leer como PDF."

router = APIRouter(prefix="/api/v1", tags=["liquidaciones"], dependencies=[Depends(sin_cache)])

ERRORES = {
    "documento_invalido": (
        422, CodigoError.DOCUMENTO_INVALIDO,
        "La versión no es un PDF disponible de un documento de liquidaciones del administrador.",
    ),
    "ya_importada": (
        409, CodigoError.LIQUIDACION_YA_IMPORTADA, "Esa versión de la liquidación ya se importó.",
    ),
}  # fmt: skip


# --- Lo leído como campos ----------------------------------------------------------------------


def _texto(importe: Decimal | None) -> str | None:
    return None if importe is None else f"{importe:.2f}"


def _campo(tipo: str, clave: str, valor: dict[str, Any], origen: mis_expensas.Origen) -> dict:
    return {
        "tipo": tipo, "clave": clave, "valor": valor, "confianza": str(CONFIANZA_DETERMINISTA),
        "origen": "determinista", "pagina": origen.pagina, "renglon": str(origen.renglon),
    }  # fmt: skip


def campos(lq: mis_expensas.LiquidacionLeida) -> list[dict]:
    """Cada dato de la liquidación como campo preliminar, en el orden del documento."""
    salida = [
        _campo("total", "total:gastos", {
            "grupos": {g: _texto(i) for g, i in lq.totales_gastos.items()},
            "total": _texto(lq.total_gastos), "vencimientos": list(lq.vencimientos),
        }, lq.origen_totales_gastos),
    ]  # fmt: skip
    for r in lq.rubros:
        salida.append(_campo("rubro", f"rubro:{r.numero}", {
            "numero": r.numero, "nombre": r.nombre, "porcentaje": _texto(r.porcentaje),
            "total": _texto(r.total),
        }, r.origen))  # fmt: skip
        for k, g in enumerate(r.gastos, 1):
            salida.append(_campo("gasto", f"gasto:{r.numero}:{k}", {
                "rubro": r.numero, "concepto": g.concepto,
                "importes": {n: _texto(i) for n, i in g.importes.items()},
                "total": _texto(g.total),
            }, g.origen))  # fmt: skip
    for u in lq.unidades:
        base = {"uf": u.uf, "depto": u.depto}
        salida.append(_campo("unidad", f"unidad:{u.uf}", base | {"nombre": u.nombre}, u.origen))
        salida.append(_campo("saldo_unidad", f"saldo_unidad:{u.uf}", base | {
            "saldo_anterior": _texto(u.saldo_anterior), "pagos": _texto(u.pagos),
            "deuda": _texto(u.deuda), "interes": _texto(u.interes),
            "disponible": _texto(u.disponible),
        }, u.origen))  # fmt: skip
        salida.append(_campo("importe_unidad", f"importe_unidad:{u.uf}", base | {
            "grupos": {n: {"porcentaje": _texto(p.porcentaje), "monto": _texto(p.monto)}
                       for n, p in u.grupos.items()},
            "otros_gastos": _texto(u.otros_gastos), "redondeo": _texto(u.redondeo),
            "total": _texto(u.total),
        }, u.origen))  # fmt: skip
    tp = lq.totales_prorrateo
    salida.append(_campo("total", "total:prorrateo", {
        "saldo_anterior": _texto(tp.saldo_anterior), "pagos": _texto(tp.pagos),
        "deuda": _texto(tp.deuda), "interes": _texto(tp.interes),
        "disponible": _texto(tp.disponible),
        "grupos": {n: {"porcentaje": _texto(p.porcentaje), "monto": _texto(p.monto)}
                   for n, p in tp.grupos.items()},
        "otros_gastos": _texto(tp.otros_gastos), "redondeo": _texto(tp.redondeo),
        "total": _texto(tp.total),
    }, tp.origen))  # fmt: skip
    return salida


def diferencias(lq: mis_expensas.LiquidacionLeida) -> list[dict]:
    return [
        {"regla": d.regla, "detalle": d.detalle, "esperado": _texto(d.esperado),
         "informado": _texto(d.informado), "pagina": d.origen.pagina,
         "renglon": str(d.origen.renglon)}
        for d in lq.diferencias
    ]  # fmt: skip


# --- Lectura en cem-trabajos -------------------------------------------------------------------


def _guardar(
    conn: Connection,
    importacion: UUID,
    formato: str | None,
    periodo: date | None,
    motivo: str | None,
    difs: list[dict],
    leidos: list[dict],
) -> bool:
    return conn.execute(
        text("select nucleo.guardar_lectura_liquidacion(:i, :f, :p, :m, cast(:d as jsonb),"
             " cast(:c as jsonb))"),
        {"i": importacion, "f": formato, "p": periodo, "m": motivo, "d": json.dumps(difs),
         "c": json.dumps(leidos)},
    ).scalar_one()  # fmt: skip


@cola.REGISTRO.tipo(
    TRABAJO_LEER,
    maximo_intentos=INTENTOS_LEER,
    retroceso_base=timedelta(minutes=1),
    retroceso_tope=timedelta(hours=1),
)
def leer_importacion(conn: Connection, trabajo: cola.Trabajo) -> dict[str, Any]:
    """Lee el PDF de una importación y guarda lo leído. El resultado no lleva lo leído:
    `plataforma.jobs` la lee soporte."""
    importacion = UUID(trabajo.carga["importacion"])
    fila = conn.execute(
        text("select * from nucleo.importacion_para_leer(:i)"), {"i": importacion}
    ).mappings().one_or_none()  # fmt: skip
    if fila is None:
        raise cola.ErrorDefinitivo(f"la importación {importacion} no existe")
    if fila["estado"] != "leyendo":
        return {"leida": False, "motivo": "ya_leida"}
    v = conn.execute(
        text("select * from nucleo.version_para_analizar(:v)"),
        {"v": fila["version_documento_id"]},
    ).mappings().one_or_none()  # fmt: skip
    if v is None or v["estado"] != "disponible":
        raise cola.ErrorDefinitivo(f"la versión de la importación {importacion} no está disponible")
    origen = almacen.AlmacenDocumentos.desde_entorno(almacen.DOCUMENTOS_ANALISIS)
    lugar = {"Bucket": origen.bucket, "Key": v["clave_objeto"]}
    if v["version_objeto"]:
        lugar["VersionId"] = v["version_objeto"]
    contenido = origen.cliente().get_object(**lugar)["Body"].read()
    try:
        paginas = palabras_pdf.paginas(contenido)
    except PdfminerException:
        _guardar(conn, importacion, None, None, PDF_ILEGIBLE, [], [])
        return {"leida": False, "motivo": "pdf_ilegible"}
    try:
        lq = mis_expensas.leer(paginas)
    except mis_expensas.FormatoNoReconocido as e:
        return _leer_con_ia(conn, trabajo, importacion, paginas, str(e))
    ano, mes = lq.periodo
    _guardar(conn, importacion, FORMATO, date(ano, mes, 1), None, diferencias(lq), campos(lq))
    return {"leida": True, "diferencias": len(lq.diferencias)}


def _aparte(conn: Connection, trabajo: cola.Trabajo):
    """Una transacción propia, que queda aunque la del trabajo se deshaga: el gasto en IA se anota
    antes y después de la llamada, porque la llamada se paga aunque lo demás falle."""
    return transaccion(conn.engine, persona_id=None, consorcio_id=trabajo.consorcio_id)


def _leer_con_ia(
    conn: Connection,
    trabajo: cola.Trabajo,
    importacion: UUID,
    paginas: list[mis_expensas.Pagina],
    sin_formato: str,
) -> dict[str, Any]:
    """Lo que el formato conocido no resuelve, con el modelo. Cada salida sin lectura guarda la
    importación `no_reconocida` con el motivo: después se carga a mano (H5-3)."""

    def no_reconocida(clave: str) -> dict[str, Any]:
        motivo = f"{lectura_ia.MOTIVOS[clave]} Sin IA: {sin_formato}"
        _guardar(conn, importacion, None, None, motivo[:LARGO_MOTIVO], [], [])
        return {"leida": False, "motivo": clave}

    if not lectura_comprobantes.encendida() or not any(p.palabras for p in paginas):
        _guardar(conn, importacion, None, None, sin_formato[:LARGO_MOTIVO], [], [])
        return {"leida": False, "motivo": "formato_no_reconocido"}
    doc = lectura_ia.documento(paginas)
    if doc.largo > lectura_ia.LARGO_MAXIMO:
        return no_reconocida("largo")
    with _aparte(conn, trabajo) as c:
        reserva, motivo = c.execute(
            text("select * from plataforma.reservar_gasto_ia(:u, :i, :m, :e, :s)"),
            {"u": TRABAJO_LEER, "i": importacion, "m": llm.exigir_variables()["CEM_LLM_MODEL"],
             "e": doc.fichas_maximas_entrada, "s": lectura_ia.MAX_TOKENS},
        ).one()  # fmt: skip
    if reserva is None:
        return no_reconocida(motivo)
    try:
        respuesta: llm.Respuesta | None = lectura_ia.pedir(doc)
    except llm.ErrorDeModelo as e:
        log.warning("la lectura con IA de la liquidación falló: %s", e)
        respuesta = None
    with _aparte(conn, trabajo) as c:
        # Una llamada que falló pudo haberse cobrado: sin modelo informado, cuenta el máximo.
        c.execute(
            text("select plataforma.registrar_gasto_ia(:r, :m, :e, :s)"),
            {"r": reserva} | (
                {"m": None, "e": None, "s": None} if respuesta is None else
                {"m": respuesta.modelo, "e": respuesta.fichas_entrada,
                 "s": respuesta.fichas_salida}
            ),
        )  # fmt: skip
    if respuesta is None:
        return no_reconocida("falla_del_modelo")
    try:
        leida = lectura_ia.interpretar(respuesta.texto, doc)
    except lectura_ia.RespuestaInvalida as e:
        log.warning("la lectura con IA de la liquidación no validó: %s", e)
        return no_reconocida("respuesta_invalida")
    if isinstance(leida, str):
        return no_reconocida(leida)
    _guardar(conn, importacion, lectura_ia.FORMATO, leida.periodo, None, leida.diferencias,
             leida.campos)  # fmt: skip
    return {"leida": True, "con_ia": True, "prompt": lectura_ia.VERSION_PROMPT,
            "modelo": respuesta.modelo, "diferencias": len(leida.diferencias)}  # fmt: skip


# --- Rutas -------------------------------------------------------------------------------------


@contextmanager
def _transaccion(permiso: Permiso, motor) -> Iterator[Connection]:
    ctx = permiso.ctx
    permiso.verificar(Entidad())
    with transaccion(motor, persona_id=ctx.persona_id, consorcio_id=ctx.consorcio_id) as c:
        yield c


def _ejecutar(conn: Connection, permiso: Permiso, sql: str, parametros: dict):
    """Llama a una función de `0113` con la persona y la sesión, y traduce sus motivos."""
    ctx = permiso.ctx
    try:
        return conn.execute(
            text(sql), parametros | {"actor": ctx.persona_id, "sesion": ctx.sesion_id}
        )
    except DBAPIError as e:
        if isinstance(e.orig, InsufficientPrivilege):
            raise Discrepancia(f"la base niega {permiso.accion} y el evaluador lo concede") from e
        motivo = e.orig.diag.message_hint
        if motivo in ERRORES:
            raise ErrorApi(*ERRORES[motivo]) from e
        raise


@router.post(
    "/consorcios/{consorcio_id}/liquidaciones-importadas",
    status_code=201,
    response_model=ImportacionCreada,
    summary="Importar la liquidación del administrador: la lectura corre en segundo plano",
    responses={
        403: {"description": "Sin `liquidacion.importar` en el consorcio."},
        409: {"description": "Esa versión ya se importó (`liquidacion_ya_importada`)."},
        422: {"description": "No es un PDF disponible de una liquidación del administrador."},
    },
)
def importar_liquidacion(
    nueva: ImportacionNueva,
    permiso: Annotated[Permiso, Depends(requiere(ACCION, sin_gesto=True))],
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    ctx = permiso.ctx
    permiso.verificar(Entidad())

    def efecto(conn: Connection) -> tuple[int, ImportacionCreada]:
        i = _ejecutar(
            conn, permiso,
            "select nucleo.crear_importacion_liquidacion(:d, :v, :actor, 'api', :sesion)",
            {"d": nueva.documento_id, "v": nueva.version_documento_id},
        ).scalar_one()  # fmt: skip
        cola.encolar(conn, TRABAJO_LEER, carga={"importacion": str(i)},
                     consorcio_id=ctx.consorcio_id, clave_unica=f"{TRABAJO_LEER}:{i}")  # fmt: skip
        return 201, ImportacionCreada(id=i)

    return responder_una_vez(motor, ctx, idempotencia, efecto)


@router.get(
    "/consorcios/{consorcio_id}/liquidaciones-importadas/{importacion_id}",
    response_model=ImportacionLiquidacion,
    summary="Una liquidación importada: lo leído, campo por campo, y sus diferencias",
    responses={
        403: {"description": "Sin `liquidacion.importar` en el consorcio."},
        404: {"description": "La importación no existe en este consorcio."},
    },
)
def importacion_liquidacion(
    importacion_id: UUID,
    permiso: Annotated[Permiso, Depends(requiere(ACCION, sin_gesto=True))],
    motor: MotorDep,
) -> ImportacionLiquidacion:
    """Mientras `estado` es `leyendo`, se vuelve a preguntar."""
    with _transaccion(permiso, motor) as c:
        cabeza = _ejecutar(
            c, permiso,
            "select * from nucleo.importacion_liquidacion_lectura(:i, :actor, 'api', :sesion)",
            {"i": importacion_id},
        ).mappings().one_or_none()  # fmt: skip
        if cabeza is None:
            raise ErrorApi(404, CodigoError.NO_ENCONTRADO, NO_ENCONTRADO)
        filas = _ejecutar(
            c, permiso,
            "select * from nucleo.campos_preliminares_lectura(:i, :actor, 'api', :sesion)",
            {"i": importacion_id},
        ).mappings().all()  # fmt: skip
    datos = dict(cabeza)
    datos["diferencias"] = [DiferenciaLeida(**d) for d in datos["diferencias"]]
    return ImportacionLiquidacion(**datos, campos=[CampoPreliminar(**f) for f in filas])
