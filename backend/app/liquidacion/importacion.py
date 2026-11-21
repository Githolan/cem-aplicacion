"""Importación de la liquidación del administrador (H5-1; diseño H5 §4 y §5; migración `0113`).

- **`POST …/liquidaciones-importadas`** (`liquidacion.importar`): pide importar una versión
  `disponible` de un documento de la categoría `liquidaciones_del_administrador`. Encola la lectura
  en la misma transacción.
- **Lectura en `cem-trabajos`** (`liquidacion.leer`): palabras con su recuadro (`palabras_pdf`),
  formato Mis Expensas (`mis_expensas`) y cada dato como campo preliminar con su renglón de origen.
  Si el formato no se reconoce y el PDF tiene texto, lo lee el modelo (`lectura_ia`, H5-2), dentro
  del tope mensual de gasto que lleva la base (`0114`, D4). Una foto (JPEG o PNG) o un PDF sin
  texto van al modelo como imágenes de tamaño acotado (`imagenes`, H5-5, `0117`). Si tampoco así
  se lee, la importación queda `no_reconocida` con el motivo.
- **`GET …/liquidaciones-importadas`** y **`GET …/liquidaciones-importadas/{id}`**
  (`liquidacion.importar`): la lista del consorcio y una importación con sus diferencias y sus
  campos, con su estado. No es una lectura oficial: lo oficial sale solo de
  `nucleo.campos_oficiales_liquidacion` (RN30).
- **Confirmación (H5-3, `0115`)**, con la misma facultad (D2-A): la decisión sobre cada campo
  (`…/campos/{campo}/decision`: confirmar, corregir o descartar), la confirmación de la importación
  entera sin campos pendientes (`…/confirmacion`) y su descarte con motivo (`…/descarte`). Cada una
  devuelve la importación como queda.
- **Recálculo (H5-4, `0116`)**, con la misma facultad: `GET …/{id}/recalculo` reparte el gasto de
  cada grupo de una importación confirmada con los coeficientes vigentes en el período y lo compara
  con lo informado (`recalculo`); `POST …/{id}/correspondencias` dice qué esquema de coeficientes
  corresponde a un grupo (el edificio lo recuerda) y devuelve el recálculo como queda.
"""

import json
import logging
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

from cem_contracts.errores import CodigoError
from cem_contracts.importacion_liquidacion import (
    CampoPreliminar,
    CorrespondenciaNueva,
    DecisionCampo,
    DecisionSobreCampo,
    DescarteImportacion,
    DiferenciaLeida,
    EsquemaDelPeriodo,
    EstadoImportacion,
    ImportacionCreada,
    ImportacionLiquidacion,
    ImportacionNueva,
    ImportacionResumen,
    RecalculoLiquidacion,
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
from app.liquidacion import imagenes, lectura_ia, mis_expensas, palabras_pdf, recalculo
from app.plataforma import almacen, cola, trabajos
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
SIN_TEXTO_FOTO = "Es una foto: no tiene texto que leer por posición."
SIN_TEXTO_PDF = "El PDF no tiene texto (está escaneado)."
LARGO_FRAGMENTO = 500
"""El de `nucleo.campo_preliminar.fragmento` (`0115`)."""
LARGO_TEXTO_CORREGIDO = 300
"""El texto más largo de un valor leído (`lectura_ia.LARGO_CONCEPTO`)."""
IMPORTE_TEXTO = re.compile(r"^-?[0-9]{1,16}\.[0-9]{2}$")

router = APIRouter(prefix="/api/v1", tags=["liquidaciones"], dependencies=[Depends(sin_cache)])

ERRORES = {
    "documento_invalido": (
        422, CodigoError.DOCUMENTO_INVALIDO,
        "La versión no es un PDF, JPEG o PNG disponible de un documento de liquidaciones del"
        " administrador.",
    ),
    "ya_importada": (
        409, CodigoError.LIQUIDACION_YA_IMPORTADA, "Esa versión de la liquidación ya se importó.",
    ),
    "no_encontrado": (404, CodigoError.NO_ENCONTRADO, NO_ENCONTRADO),
    "importacion_decidida": (
        409, CodigoError.IMPORTACION_DECIDIDA,
        "La liquidación importada ya se confirmó o se descartó.",
    ),
    "campos_pendientes": (409, CodigoError.CAMPOS_PENDIENTES, "Quedan datos leídos sin decidir."),
    "correccion_invalida": (
        422, CodigoError.CORRECCION_INVALIDA,
        "El valor corregido no tiene la forma del leído o no cambia nada.",
    ),
    "decision_invalida": (422, CodigoError.DECISION_INVALIDA, "La decisión no es válida."),
    "motivo_invalido": (422, CodigoError.PETICION_INVALIDA, "Falta el motivo."),
    "nombre_invalido": (422, CodigoError.PETICION_INVALIDA, "Falta el nombre del grupo."),
    "grupo_inexistente": (
        422, CodigoError.GRUPO_INEXISTENTE, "El edificio no tiene ese esquema de coeficientes.",
    ),
}  # fmt: skip


# --- Lo leído como campos ----------------------------------------------------------------------


def _texto(importe: Decimal | None) -> str | None:
    return None if importe is None else f"{importe:.2f}"


def campos(lq: mis_expensas.LiquidacionLeida, paginas: list[mis_expensas.Pagina]) -> list[dict]:
    """Cada dato de la liquidación como campo preliminar, en el orden del documento, con el texto
    del renglón de donde salió."""
    textos = {
        r.origen: r.texto for i, p in enumerate(paginas, 1) for r in mis_expensas.renglones_de(p, i)
    }

    def _campo(tipo: str, clave: str, valor: dict[str, Any], origen: mis_expensas.Origen) -> dict:
        return {
            "tipo": tipo, "clave": clave, "valor": valor,
            "confianza": str(CONFIANZA_DETERMINISTA), "origen": "determinista",
            "pagina": origen.pagina, "renglon": str(origen.renglon),
            "fragmento": textos[origen][:LARGO_FRAGMENTO],
        }  # fmt: skip

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
    if v["tipo"] != "application/pdf":
        return _leer_con_ia(conn, trabajo, importacion, lambda: _de_foto(contenido), SIN_TEXTO_FOTO)
    try:
        paginas = palabras_pdf.paginas(contenido)
    except PdfminerException:
        _guardar(conn, importacion, None, None, PDF_ILEGIBLE, [], [])
        return {"leida": False, "motivo": "pdf_ilegible"}
    try:
        lq = mis_expensas.leer(paginas)
    except mis_expensas.FormatoNoReconocido as e:
        if any(p.palabras for p in paginas):
            return _leer_con_ia(conn, trabajo, importacion,
                                lambda: lectura_ia.documento(paginas), str(e))  # fmt: skip
        return _leer_con_ia(conn, trabajo, importacion, lambda: _de_pdf(contenido),
                            f"{e} {SIN_TEXTO_PDF}")  # fmt: skip
    ano, mes = lq.periodo
    _guardar(conn, importacion, FORMATO, date(ano, mes, 1), None, diferencias(lq),
             campos(lq, paginas))  # fmt: skip
    return {"leida": True, "diferencias": len(lq.diferencias)}


class _NoSeManda(Exception):
    """El documento no va al modelo; lleva la clave de `lectura_ia.MOTIVOS`."""


def _de_foto(contenido: bytes) -> lectura_ia.DocumentoImagen:
    try:
        return lectura_ia.DocumentoImagen((imagenes.de_foto(contenido),))
    except imagenes.ImagenIlegible:
        raise _NoSeManda("imagen_ilegible") from None


def _de_pdf(contenido: bytes) -> lectura_ia.DocumentoImagen:
    try:
        return lectura_ia.DocumentoImagen(tuple(imagenes.de_pdf(contenido)))
    except imagenes.DemasiadasPaginas:
        raise _NoSeManda("demasiadas_paginas") from None
    except imagenes.ImagenIlegible:
        raise _NoSeManda("imagen_ilegible") from None


def _leer_con_ia(
    conn: Connection,
    trabajo: cola.Trabajo,
    importacion: UUID,
    armar: Callable[[], lectura_ia.Documento | lectura_ia.DocumentoImagen],
    sin_formato: str,
) -> dict[str, Any]:
    """Lo que el formato conocido no resuelve, con el modelo. `armar` da lo que va al modelo: el
    texto delimitado o las imágenes; se arma recién con la lectura encendida. Cada salida sin
    lectura guarda la importación `no_reconocida` con el motivo: después se carga a mano (H5-3)."""

    def no_reconocida(clave: str) -> dict[str, Any]:
        motivo = f"{lectura_ia.MOTIVOS[clave]} Sin IA: {sin_formato}"
        _guardar(conn, importacion, None, None, motivo[:LARGO_MOTIVO], [], [])
        return {"leida": False, "motivo": clave}

    if not lectura_comprobantes.encendida():
        _guardar(conn, importacion, None, None, sin_formato[:LARGO_MOTIVO], [], [])
        return {"leida": False, "motivo": "formato_no_reconocido"}
    try:
        doc = armar()
    except _NoSeManda as e:
        return no_reconocida(str(e))
    if isinstance(doc, lectura_ia.Documento) and doc.largo > lectura_ia.LARGO_MAXIMO:
        return no_reconocida("largo")
    with trabajos.aparte(conn, trabajo) as c:
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
    with trabajos.aparte(conn, trabajo) as c:
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
    de_imagen = isinstance(doc, lectura_ia.DocumentoImagen)
    formato = lectura_ia.FORMATO_IMAGEN if de_imagen else lectura_ia.FORMATO
    _guardar(conn, importacion, formato, leida.periodo, None, leida.diferencias, leida.campos)
    prompt = lectura_ia.VERSION_PROMPT_IMAGEN if de_imagen else lectura_ia.VERSION_PROMPT
    return {"leida": True, "con_ia": True, "prompt": prompt, "modelo": respuesta.modelo,
            "diferencias": len(leida.diferencias)}  # fmt: skip


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
        return _importacion(c, permiso, importacion_id)


def _importacion(conn: Connection, permiso: Permiso, i: UUID) -> ImportacionLiquidacion:
    cabeza = _ejecutar(
        conn, permiso,
        "select * from nucleo.importacion_liquidacion_lectura(:i, :actor, 'api', :sesion)",
        {"i": i},
    ).mappings().one_or_none()  # fmt: skip
    if cabeza is None:
        raise ErrorApi(404, CodigoError.NO_ENCONTRADO, NO_ENCONTRADO)
    filas = _ejecutar(
        conn, permiso,
        "select * from nucleo.campos_preliminares_lectura(:i, :actor, 'api', :sesion)",
        {"i": i},
    ).mappings().all()  # fmt: skip
    datos = dict(cabeza)
    datos["diferencias"] = [DiferenciaLeida(**d) for d in datos["diferencias"]]
    return ImportacionLiquidacion(**datos, campos=[CampoPreliminar(**f) for f in filas])


@router.get(
    "/consorcios/{consorcio_id}/liquidaciones-importadas",
    response_model=list[ImportacionResumen],
    summary="Las liquidaciones importadas del consorcio, la más nueva primero",
    responses={403: {"description": "Sin `liquidacion.importar` en el consorcio."}},
)
def importaciones_liquidacion(
    permiso: Annotated[Permiso, Depends(requiere(ACCION, sin_gesto=True))],
    motor: MotorDep,
) -> list[ImportacionResumen]:
    with _transaccion(permiso, motor) as c:
        filas = _ejecutar(
            c, permiso,
            "select * from nucleo.importaciones_liquidacion_lectura(:actor, 'api', :sesion)", {},
        ).mappings().all()  # fmt: skip
    return [ImportacionResumen(**f) for f in filas]


# Las claves de un valor leído que son importes (o porcentajes, con la misma forma), y las que
# agrupan importes por nombre de grupo: lo que vale ahí es un importe aunque lo leído esté vacío.
CLAVES_DE_IMPORTE = frozenset({
    "total", "saldo_anterior", "pagos", "deuda", "interes", "disponible", "otros_gastos",
    "redondeo", "porcentaje", "monto",
})  # fmt: skip
CLAVES_DE_GRUPOS = frozenset({"importes", "grupos"})


def forma_igual(leido: Any, corregido: Any, clave: str = "", de: str = "") -> bool:
    """El valor corregido tiene la forma del leído: las mismas claves en cada nivel, un importe
    donde va un importe (por su clave, o porque lo leído lo era; vacío también vale), un número
    donde había un número y un texto corto donde había un texto. Ningún texto con una cuenta
    bancaria."""
    if isinstance(leido, dict):
        return (
            isinstance(corregido, dict)
            and leido.keys() == corregido.keys()
            and all(forma_igual(leido[k], corregido[k], k, clave) for k in leido)
        )
    if isinstance(leido, list):
        return isinstance(corregido, list) and all(forma_igual("", x) for x in corregido)
    if isinstance(leido, bool) or isinstance(corregido, bool):
        return type(leido) is type(corregido)
    if isinstance(leido, int):
        return type(corregido) is int
    es_importe = clave in CLAVES_DE_IMPORTE or de in CLAVES_DE_GRUPOS
    if es_importe or (isinstance(leido, str) and IMPORTE_TEXTO.match(leido)):
        return corregido is None or (
            isinstance(corregido, str) and IMPORTE_TEXTO.match(corregido) is not None
        )
    if corregido is None:
        return True
    return (
        isinstance(corregido, str)
        and 1 <= len(corregido.strip()) <= LARGO_TEXTO_CORREGIDO
        and not lectura_comprobantes.CON_CUENTA.search(corregido)
    )


_ACTOS: dict[int | str, dict[str, Any]] = {
    403: {"description": "Sin `liquidacion.importar` en el consorcio."},
    404: {"description": "La importación o el dato no existen en este consorcio."},
    409: {"description": "Ya se confirmó o se descartó (`importacion_decidida`)."},
}


@router.post(
    "/consorcios/{consorcio_id}/liquidaciones-importadas/{importacion_id}/campos/{campo_id}"
    "/decision",
    response_model=ImportacionLiquidacion,
    summary="Confirmar, corregir o descartar un dato leído de la liquidación",
    responses=_ACTOS
    | {422: {"description": "Decisión o corrección inválida (`correccion_invalida`)."}},
)
def decidir_campo(
    importacion_id: UUID,
    campo_id: UUID,
    decision: DecisionSobreCampo,
    permiso: Annotated[Permiso, Depends(requiere(ACCION, sin_gesto=True))],
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    ctx = permiso.ctx
    permiso.verificar(Entidad())
    corrige = decision.decision == DecisionCampo.CORREGIDO
    if corrige != (decision.valor_corregido is not None):
        raise ErrorApi(*ERRORES["decision_invalida"])

    def efecto(conn: Connection) -> tuple[int, ImportacionLiquidacion]:
        if corrige:
            campos_ = _importacion(conn, permiso, importacion_id).campos
            actual = next((c for c in campos_ if c.id == campo_id), None)
            if actual is None:
                raise ErrorApi(404, CodigoError.NO_ENCONTRADO, NO_ENCONTRADO)
            if not forma_igual(actual.valor, decision.valor_corregido):
                raise ErrorApi(*ERRORES["correccion_invalida"])
        valor = None if decision.valor_corregido is None else json.dumps(decision.valor_corregido)
        _ejecutar(
            conn, permiso,
            "select nucleo.resolver_campo_preliminar(:i, :campo, :d, cast(:v as jsonb), :actor,"
            " 'api', :sesion)",
            {"i": importacion_id, "campo": campo_id, "d": decision.decision.value, "v": valor},
        )  # fmt: skip
        return 200, _importacion(conn, permiso, importacion_id)

    return responder_una_vez(motor, ctx, idempotencia, efecto)


@router.post(
    "/consorcios/{consorcio_id}/liquidaciones-importadas/{importacion_id}/confirmacion",
    response_model=ImportacionLiquidacion,
    summary="Confirmar la liquidación importada: desde ahí sus datos decididos son oficiales",
    responses=_ACTOS
    | {409: {"description": "Ya decidida, o quedan datos sin decidir (`campos_pendientes`)."}},
)
def confirmar_importacion(
    importacion_id: UUID,
    permiso: Annotated[Permiso, Depends(requiere(ACCION, sin_gesto=True))],
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    ctx = permiso.ctx
    permiso.verificar(Entidad())

    def efecto(conn: Connection) -> tuple[int, ImportacionLiquidacion]:
        _ejecutar(
            conn, permiso,
            "select nucleo.confirmar_importacion_liquidacion(:i, :actor, 'api', :sesion)",
            {"i": importacion_id},
        )  # fmt: skip
        return 200, _importacion(conn, permiso, importacion_id)

    return responder_una_vez(motor, ctx, idempotencia, efecto)


@router.post(
    "/consorcios/{consorcio_id}/liquidaciones-importadas/{importacion_id}/descarte",
    response_model=ImportacionLiquidacion,
    summary="Descartar la liquidación importada, con el motivo",
    responses=_ACTOS,
)
def descartar_importacion(
    importacion_id: UUID,
    descarte: DescarteImportacion,
    permiso: Annotated[Permiso, Depends(requiere(ACCION, sin_gesto=True))],
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    ctx = permiso.ctx
    permiso.verificar(Entidad())

    def efecto(conn: Connection) -> tuple[int, ImportacionLiquidacion]:
        _ejecutar(
            conn, permiso,
            "select nucleo.descartar_importacion_liquidacion(:i, :m, :actor, 'api', :sesion)",
            {"i": importacion_id, "m": descarte.motivo},
        )  # fmt: skip
        return 200, _importacion(conn, permiso, importacion_id)

    return responder_una_vez(motor, ctx, idempotencia, efecto)


# --- Recálculo (H5-4, `0116`) -----------------------------------------------------------------


def _recalculo(conn: Connection, permiso: Permiso, i: UUID) -> RecalculoLiquidacion:
    importacion = _importacion(conn, permiso, i)
    if importacion.estado != EstadoImportacion.CONFIRMADA or importacion.periodo is None:
        raise ErrorApi(
            409, CodigoError.IMPORTACION_SIN_CONFIRMAR,
            "La liquidación importada no está confirmada: todavía no tiene datos oficiales.",
        )  # fmt: skip
    oficiales = [
        recalculo.Oficial(tipo=f["tipo"], clave=f["clave"], valor=f["valor"],
                          unidad_id=f["unidad_id"])
        for f in _ejecutar(
            conn, permiso,
            "select * from nucleo.campos_oficiales_liquidacion(:i, :actor, 'api', :sesion)",
            {"i": i},
        ).mappings()
    ]  # fmt: skip
    base = [
        recalculo.Base(**f)
        for f in _ejecutar(
            conn, permiso,
            "select * from nucleo.base_recalculo_liquidacion(:i, :actor, 'api', :sesion)",
            {"i": i},
        ).mappings()
    ]  # fmt: skip
    esquemas = _ejecutar(
        conn, permiso,
        "select * from nucleo.grupos_para_recalculo(:i, :actor, 'api', :sesion)", {"i": i},
    ).mappings().all()  # fmt: skip
    grupos = recalculo.recalcular(oficiales, base)
    return RecalculoLiquidacion(
        importacion_id=i, periodo=importacion.periodo, sin_grupos=not grupos, grupos=grupos,
        esquemas=[EsquemaDelPeriodo(**e) for e in esquemas],
    )  # fmt: skip


_RECALCULO: dict[int | str, dict[str, Any]] = {
    403: {"description": "Sin `liquidacion.importar` en el consorcio."},
    404: {"description": "La importación no existe en este consorcio."},
    409: {"description": "La importación no está confirmada (`importacion_sin_confirmar`)."},
}


@router.get(
    "/consorcios/{consorcio_id}/liquidaciones-importadas/{importacion_id}/recalculo",
    response_model=RecalculoLiquidacion,
    summary="El reparto de cada grupo con los coeficientes del edificio, frente a lo informado",
    responses=_RECALCULO,
)
def recalcular_importacion(
    importacion_id: UUID,
    permiso: Annotated[Permiso, Depends(requiere(ACCION, sin_gesto=True))],
    motor: MotorDep,
) -> RecalculoLiquidacion:
    with _transaccion(permiso, motor) as c:
        return _recalculo(c, permiso, importacion_id)


@router.post(
    "/consorcios/{consorcio_id}/liquidaciones-importadas/{importacion_id}/correspondencias",
    response_model=RecalculoLiquidacion,
    summary="Qué coeficientes del edificio corresponden a un grupo de la liquidación",
    responses=_RECALCULO
    | {422: {"description": "El edificio no tiene ese esquema (`grupo_inexistente`)."}},
)
def definir_correspondencia(
    importacion_id: UUID,
    nueva: CorrespondenciaNueva,
    permiso: Annotated[Permiso, Depends(requiere(ACCION, sin_gesto=True))],
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    ctx = permiso.ctx
    permiso.verificar(Entidad())

    def efecto(conn: Connection) -> tuple[int, RecalculoLiquidacion]:
        _recalculo(conn, permiso, importacion_id)  # 404 o 409 antes de guardar nada
        _ejecutar(
            conn, permiso,
            "select nucleo.definir_correspondencia_grupo(:n, :g, :actor, 'api', :sesion)",
            {"n": nueva.nombre, "g": nueva.grupo},
        )  # fmt: skip
        return 200, _recalculo(conn, permiso, importacion_id)

    return responder_una_vez(motor, ctx, idempotencia, efecto)
