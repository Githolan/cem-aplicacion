"""Pagos informados por correo, corte PI-S4 (`0132`; diseño «Pagos informados» del repositorio CEM,
reglas PC-1 a PC-5, decisiones del propietario Q1 y Q3 y la revisión de PC-4, PI-D22).

- **`POST /api/v1/entrada/correo-pagos`**: la entrada del Worker de Cloudflare
  (`cem-pagos-por-correo[-staging]`), que pasa el correo entero (RFC 5322) en el cuerpo con la clave
  compartida en `X-CEM-Clave-Correo` (`CEM_CORREO_PAGOS_CLAVE`). No hay sesión: lo que identifica a
  quien manda es la dirección de `From` con **DMARC alineado** (PC-3) en el encabezado de
  autenticación que agrega Cloudflare arriba de todo. Un rechazo responde 422 con el texto del
  rebote en `mensaje`; el Worker lo pasa a `setReject` y el remitente lo recibe.
- **`GET …/pagos-informados/por-correo`**: lo que la persona mandó y le falta completar, con lo que
  propone la lectura (RN21: la IA propone, la persona decide).
- **`POST …/por-correo/{r}/informe`**: la persona elige la unidad, revisa los datos y lo informa;
  responde como `POST …/pagos-informados` (PA07 incluido).
- **`POST …/por-correo/{r}/descarte`**: un adjunto que no era un comprobante.
- **`GET …/por-correo/{r}/comprobante`**: el archivo, si el antivirus lo aceptó.

El cuerpo del correo no se guarda (PC-5): solo quién, cuándo, el asunto y los adjuntos.
"""

import email
import email.policy
import hashlib
import hmac
import logging
import os
import re
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Annotated
from uuid import UUID

import anyio
from botocore.exceptions import ClientError
from cem_contracts.documentos import EstadoVersion, TipoArchivo
from cem_contracts.errores import CodigoError
from cem_contracts.pagos_informados import (
    ENCABEZADO_CLAVE_CORREO,
    MAXIMO_COMPROBANTES_POR_CORREO,
    TOPE_CORREO_BYTES,
    EntradaCorreoRecibida,
    EstadoPagoPorCorreo,
    ListaPagosPorCorreo,
    PagoInformadoRespuesta,
    PagoPorCorreo,
    PagoPorCorreoDescartado,
    PagoPorCorreoInforme,
)
from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError

from app import ayuda
from app.autorizacion.requiere import NO_ENCONTRADO
from app.consorcios.documentos import (
    CABECERAS_CONTENIDO,
    INTENTOS_VERIFICAR,
    LECTURA_MAXIMA,
    TOPE_BYTES,
    TRABAJO_VERIFICAR,
    _objeto,
    _tramos,
    _vencido,
    tipo_real,
)
from app.consorcios.padron import sobre_unidad
from app.consorcios.pagos_informados import (
    BASE,
    RESPUESTAS,
    PermisosDep,
    VerDep,
    _fila,
    _pago,
    ejecutar,
)
from app.contexto import MotorDep
from app.db import transaccion
from app.errores import ErrorApi, sin_cache
from app.plataforma import almacen, cola, correo
from app.plataforma.idempotencia import IdempotenciaDep, responder_una_vez

log = logging.getLogger("cem.pagos_por_correo")

router = APIRouter(prefix="/api/v1", tags=["consorcios"], dependencies=[Depends(sin_cache)])
entrada = APIRouter(prefix="/api/v1/entrada", tags=["entrada"], dependencies=[Depends(sin_cache)])

VARIABLE_CLAVE = "CEM_CORREO_PAGOS_CLAVE"
VARIABLE_DIRECCION = "CEM_CORREO_PAGOS_DIRECCION"
"""La dirección de este entorno (`pagos@cem-pagos[-staging].holancloud.com`, PC-1). La pantalla la
ofrece solo si está, junto con la clave: sin las dos, un correo no entraría."""
"""El secreto compartido con el Worker; distinto por entorno. Sin él, la entrada no recibe nada."""
AUTENTICADOR = "mx.cloudflare.net"
"""El `authserv-id` del encabezado que agrega Cloudflare Email Routing. Solo vale el primero: los
de más abajo los puede haber escrito cualquiera."""
POR_CORREO = BASE + "/por-correo"

REBOTE_REMITENTE = (
    "No se pudo identificar a quien envía este correo. El pago se puede informar desde la"
    " aplicación Consorcio en Mano."
)
"""El aviso de Q3 (propietario, 23-11-2026)."""
REBOTE_VARIOS = (
    "La cuenta tiene unidades en más de un edificio y no se puede saber de cuál es este pago."
    " Hay que informarlo desde la aplicación Consorcio en Mano."
)
REBOTE_SIN_COMPROBANTES = (
    "El correo no trae ningún comprobante: se aceptan archivos PDF, JPG o PNG adjuntos de hasta"
    " 20 MB."
)
REBOTE_DEMASIADOS = (
    f"El correo trae más de {MAXIMO_COMPROBANTES_POR_CORREO} comprobantes: hay que mandarlos en"
    " varios correos."
)
REBOTE_TOPE_DIARIO = (
    "Se recibieron demasiados comprobantes de esta cuenta en las últimas 24 horas. El pago se"
    " puede informar desde la aplicación Consorcio en Mano."
)
ASUNTO_ACUSE = "Comprobante de pago recibido"
TEXTO_ACUSE = (
    "Consorcio en Mano recibió {n}.\n\n"
    "Para que el Consejo lo vea, hay que entrar a Consorcio en Mano, abrir «Pagos informados»,"
    " revisar los datos e informar el pago. Si un archivo no era un comprobante, se descarta ahí"
    " mismo.\n"
)

ERRORES = {
    "no_encontrado": (404, CodigoError.NO_ENCONTRADO, NO_ENCONTRADO),
    "pago_por_correo_descartado": (
        409, CodigoError.PAGO_POR_CORREO_DESCARTADO,
        "Ese comprobante se descartó: no se puede informar. Si era un pago, hay que subirlo de"
        " nuevo.",
    ),
    "pago_por_correo_informado": (
        409, CodigoError.PAGO_POR_CORREO_INFORMADO,
        "Ese comprobante ya se informó: no se puede descartar.",
    ),
}  # fmt: skip
"""Los motivos propios de `0132` que la persona puede provocar; los de `informar_pago` los traduce
`pagos_informados.ejecutar`."""


# --- La entrada del correo ---------------------------------------------------------------------


@dataclass(frozen=True)
class Adjunto:
    contenido: bytes
    tipo: TipoArchivo


@dataclass(frozen=True)
class Correo:
    remitente: str
    mensaje_id: str
    asunto: str | None
    adjuntos: tuple[Adjunto, ...]


def _rechazo(codigo: CodigoError, mensaje: str) -> ErrorApi:
    return ErrorApi(422, codigo, mensaje)


def exigir_clave(recibida: str | None) -> None:
    """La clave del Worker. Sin la variable en el entorno, 503: la entrada está apagada a la vista
    de todos, no abierta."""
    esperada = os.environ.get(VARIABLE_CLAVE, "")
    if not esperada:
        raise ErrorApi(
            503, CodigoError.ENTRADA_NO_CONFIGURADA,
            "La entrada de correo de pagos no está configurada en este entorno.",
        )  # fmt: skip
    if recibida is None or not hmac.compare_digest(recibida.encode(), esperada.encode()):
        raise ErrorApi(401, CodigoError.CLAVE_DE_ENTRADA_INVALIDA, "Clave equivocada.")


async def _leer_correo(request: Request) -> bytes:
    declarado = request.headers.get("content-length")
    if declarado is not None and declarado.isdigit() and int(declarado) > TOPE_CORREO_BYTES:
        raise _demasiado_grande()
    cuerpo = bytearray()
    try:
        with anyio.fail_after(LECTURA_MAXIMA):
            async for tramo in request.stream():
                cuerpo += tramo
                if len(cuerpo) > TOPE_CORREO_BYTES:
                    raise _demasiado_grande()
    except TimeoutError:
        raise ErrorApi(408, CodigoError.PETICION_INVALIDA, "El correo tardó en llegar.") from None
    return bytes(cuerpo)


def _demasiado_grande() -> ErrorApi:
    return ErrorApi(413, CodigoError.ARCHIVO_DEMASIADO_GRANDE, "El correo pasa del tope.")


DMARC = re.compile(r"(?:^|;)\s*dmarc\s*=\s*([a-z]+)\b([^;]*)", re.IGNORECASE)
HEADER_FROM = re.compile(r"\bheader\.from\s*=\s*\"?([^\s;\"]+)", re.IGNORECASE)


def dominio_dmarc_aprobado(autenticacion: str | None) -> str | None:
    """El dominio con DMARC aprobado del primer `Authentication-Results`, si lo escribió
    `AUTENTICADOR`; si no, nada. `authserv-id` es lo primero del encabezado (RFC 8601)."""
    if not autenticacion:
        return None
    servidor, _, resto = autenticacion.partition(";")
    if servidor.strip().split()[0:1] != [AUTENTICADOR]:
        return None
    hallado = DMARC.search(";" + resto)
    if hallado is None or hallado.group(1).lower() != "pass":
        return None
    dominio = HEADER_FROM.search(hallado.group(2))
    return dominio.group(1).lower().rstrip(".") if dominio else None


def remitente_autenticado(mensaje: EmailMessage) -> str | None:
    """La dirección de `From` si es una sola y su dominio pasó DMARC según el primer encabezado de
    autenticación (PC-3). Un SPF o DKIM aprobado de otro dominio no alcanza."""
    try:
        de = mensaje["From"]
        direcciones = de.addresses if de is not None else ()
    except (IndexError, ValueError, AttributeError):
        return None
    if len(direcciones) != 1 or not direcciones[0].addr_spec or "@" not in direcciones[0].addr_spec:
        return None
    direccion = direcciones[0].addr_spec.strip().lower()
    autenticaciones = mensaje.get_all("Authentication-Results") or []
    aprobado = dominio_dmarc_aprobado(str(autenticaciones[0]) if autenticaciones else None)
    if aprobado is None or aprobado != direccion.rpartition("@")[2]:
        return None
    return direccion


def _un_renglon(valor: object, largo: int) -> str | None:
    limpio = " ".join(re.sub(r"[\x00-\x1f\x7f]", " ", str(valor or "")).split())
    return limpio[:largo] or None


def adjuntos(mensaje: EmailMessage) -> list[Adjunto]:
    """Las partes adjuntas (no las imágenes en línea, que suelen ser logos) con un tipo real PDF,
    JPG o PNG y hasta 20 MB. El tipo lo decide el contenido, no lo que declara el correo."""
    encontrados = []
    for parte in mensaje.walk():
        if parte.is_multipart():
            continue
        disposicion = parte.get_content_disposition()
        if disposicion != "attachment" and not (disposicion is None and parte.get_filename()):
            continue
        contenido = parte.get_payload(decode=True)
        if not contenido or len(contenido) > TOPE_BYTES:
            continue
        tipo = tipo_real(contenido)
        if tipo is not None:
            encontrados.append(Adjunto(contenido, tipo))
    return encontrados


def leer_correo(crudo: bytes) -> Correo:
    mensaje = email.message_from_bytes(crudo, policy=email.policy.default)
    remitente = remitente_autenticado(mensaje)
    if remitente is None:
        raise _rechazo(CodigoError.REMITENTE_NO_IDENTIFICADO, REBOTE_REMITENTE)
    mensaje_id = _un_renglon(mensaje.get("Message-ID"), 998)
    if mensaje_id is None:
        mensaje_id = "sin-id:" + hashlib.sha256(crudo).hexdigest()
    encontrados = adjuntos(mensaje)
    if not encontrados:
        raise _rechazo(CodigoError.SIN_COMPROBANTES, REBOTE_SIN_COMPROBANTES)
    if len(encontrados) > MAXIMO_COMPROBANTES_POR_CORREO:
        raise _rechazo(CodigoError.DEMASIADOS_COMPROBANTES, REBOTE_DEMASIADOS)
    return Correo(remitente, mensaje_id, _un_renglon(mensaje.get("Subject"), 200),
                  tuple(encontrados))  # fmt: skip


def _de_quien(motor, remitente: str) -> tuple[UUID, UUID]:
    """La persona y su único consorcio; si no, el rebote que corresponde."""
    with transaccion(motor, persona_id=None) as conn:
        fila = conn.execute(
            text("select * from nucleo.remitente_de_pagos_por_correo(:c)"), {"c": remitente}
        ).one_or_none()
    if fila is None or not fila.consorcios:
        raise _rechazo(CodigoError.REMITENTE_NO_IDENTIFICADO, REBOTE_REMITENTE)
    if len(fila.consorcios) > 1:
        raise _rechazo(CodigoError.REMITENTE_EN_VARIOS_CONSORCIOS, REBOTE_VARIOS)
    return fila.persona_id, fila.consorcios[0]


def guardar(motor, persona: UUID, consorcio: UUID, recibido: Correo) -> EntradaCorreoRecibida:
    """Cada adjunto en una transacción con todos: la fila, el objeto, su `VersionId`, el antivirus
    y el acuse al remitente. Un objeto que quede sin fila lo borra
    `documentos.limpiar_huerfanos`."""
    destino = almacen.AlmacenDocumentos.desde_entorno(almacen.DOCUMENTOS)
    cuenta = {"nuevo": 0, "ya_recibido": 0, "ya_informado": 0}
    try:
        # En el ámbito de quien manda: la API nunca abre uno sin persona (barrera de trabajos).
        with transaccion(motor, persona_id=persona, consorcio_id=consorcio) as conn:
            for adjunto in recibido.adjuntos:
                suma = hashlib.sha256(adjunto.contenido).hexdigest()
                fila = conn.execute(
                    text("select * from nucleo.recibir_pago_por_correo(:p, :m, :a, :t, :n, :s)"),
                    {"p": persona, "m": recibido.mensaje_id, "a": recibido.asunto,
                     "t": adjunto.tipo.value, "n": len(adjunto.contenido), "s": suma},
                ).one()  # fmt: skip
                cuenta[fila.resultado] += 1
                if fila.resultado != "nuevo":
                    continue
                subida = destino.cliente().put_object(
                    Bucket=destino.bucket, Key=fila.nueva_clave_objeto, Body=adjunto.contenido,
                    ContentType=adjunto.tipo.value, Metadata={"sha256": suma},
                )  # fmt: skip
                if subida.get("VersionId"):
                    conn.execute(
                        text("select nucleo.fijar_version_pago_por_correo(:v, :vo, :p)"),
                        {"v": fila.nueva_version_id, "vo": subida["VersionId"], "p": persona},
                    )
                cola.encolar(
                    conn,
                    TRABAJO_VERIFICAR,
                    carga={"version": str(fila.nueva_version_id)},
                    consorcio_id=consorcio,
                    clave_unica=f"{TRABAJO_VERIFICAR}:{fila.nueva_version_id}",
                    maximo_intentos=INTENTOS_VERIFICAR,
                )
            if cuenta["nuevo"]:
                n = cuenta["nuevo"]
                cuantos = "un comprobante de pago" if n == 1 else f"{n} comprobantes de pago"
                correo.solicitar(
                    conn, para=recibido.remitente, asunto=ASUNTO_ACUSE,
                    texto=TEXTO_ACUSE.format(n=cuantos), consorcio_id=consorcio,
                )  # fmt: skip
    except DBAPIError as e:
        motivo = getattr(e.orig, "diag", None) and e.orig.diag.message_hint
        if motivo == "demasiados_comprobantes":
            raise _rechazo(CodigoError.DEMASIADOS_COMPROBANTES, REBOTE_TOPE_DIARIO) from e
        if motivo in ("sin_vinculo", "modo_no_admitido"):
            raise _rechazo(CodigoError.REMITENTE_NO_IDENTIFICADO, REBOTE_REMITENTE) from e
        raise
    return EntradaCorreoRecibida(
        nuevos=cuenta["nuevo"], ya_recibidos=cuenta["ya_recibido"],
        ya_informados=cuenta["ya_informado"],
    )  # fmt: skip


@entrada.post(
    "/correo-pagos",
    response_model=EntradaCorreoRecibida,
    summary="Entrada del correo de pagos (Worker de Cloudflare): guarda cada comprobante adjunto"
    " para que quien lo mandó lo complete en la app",
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"message/rfc822": {"schema": {"type": "string", "format": "binary"}}},
        }
    },
    responses={
        401: {"description": "Clave equivocada o ausente."},
        413: {"description": "El correo pasa del tope de Email Routing."},
        422: {"description": "Rebote: `mensaje` es el texto que recibe el remitente (sin DMARC"
              " alineado o sin cuenta, unidades en varios edificios, sin comprobantes o"
              " demasiados)."},
        503: {"description": "La entrada no tiene su clave en este entorno."},
    },
)  # fmt: skip
async def recibir_correo_de_pagos(
    request: Request,
    motor: MotorDep,
    clave: Annotated[str | None, Header(alias=ENCABEZADO_CLAVE_CORREO)] = None,
) -> Response:
    exigir_clave(clave)
    crudo = await _leer_correo(request)

    def procesar() -> EntradaCorreoRecibida:
        recibido = leer_correo(crudo)
        persona, consorcio = _de_quien(motor, recibido.remitente)
        return guardar(motor, persona, consorcio, recibido)

    resultado = await anyio.to_thread.run_sync(procesar)
    log.info(
        "correo de pagos: %s nuevos, %s ya recibidos, %s ya informados",
        resultado.nuevos, resultado.ya_recibidos, resultado.ya_informados,
    )  # fmt: skip
    return JSONResponse(resultado.model_dump(mode="json"))


# --- Lo que completa la persona ----------------------------------------------------------------


def _sin_ayuda() -> None:
    """Lo recibido por correo lo completa quien lo mandó: ayudando no se ve ni se toca (PI-D23).
    La base lo repite (`exigir_remitente_pagos_por_correo`)."""
    if ayuda.vigente() is not None:
        raise ErrorApi(
            403, CodigoError.AYUDA_NO_ADMITIDA,
            "Los comprobantes que llegan por correo los completa quien los mandó.",
        )  # fmt: skip


def _ejecutar(conn: Connection, permiso, sql: str, parametros: dict):
    try:
        return ejecutar(conn, permiso, sql, parametros)
    except DBAPIError as e:
        motivo = getattr(e.orig, "diag", None) and e.orig.diag.message_hint
        if motivo in ERRORES:
            raise ErrorApi(*ERRORES[motivo]) from e
        raise


@router.get(
    POR_CORREO,
    response_model=ListaPagosPorCorreo,
    summary="Los comprobantes que la persona mandó por correo y le falta completar, con lo que"
    " propone la lectura",
    responses=RESPUESTAS,
)
def listar_pagos_por_correo(ver: VerDep, motor: MotorDep) -> ListaPagosPorCorreo:
    _sin_ayuda()
    ctx = ver.ctx
    with transaccion(motor, persona_id=ctx.persona_id, consorcio_id=ctx.consorcio_id) as c:
        filas = _ejecutar(
            c, ver, "select * from nucleo.pagos_por_correo_lectura(:actor, 'api', :sesion)", {}
        ).mappings().all()  # fmt: skip
    configurada = os.environ.get(VARIABLE_CLAVE) and os.environ.get(VARIABLE_DIRECCION)
    return ListaPagosPorCorreo(
        elementos=[PagoPorCorreo(**f) for f in filas],
        direccion=os.environ[VARIABLE_DIRECCION] if configurada else None,
    )


@router.post(
    POR_CORREO + "/{recibido_id}/informe",
    status_code=201,
    response_model=PagoInformadoRespuesta,
    summary="Informar un comprobante recibido por correo, con la unidad y los datos que revisó la"
    " persona; si ese pago ya estaba informado, responde el existente",
    responses={
        200: {"description": "Ese pago ya estaba informado (PA07) o este comprobante ya se"
              " informó: el existente, con `ya_informado`.", "model": PagoInformadoRespuesta},
        **RESPUESTAS,
        404: {"description": "No es un comprobante que mandó esta cuenta."},
        409: {"description": "Se descartó, o el archivo ya se informó para otra unidad."},
        422: {"description": "La fecha es posterior a hoy o el número es una cuenta."},
    },
)  # fmt: skip
def informar_pago_por_correo(
    recibido_id: UUID,
    cuerpo: PagoPorCorreoInforme,
    permisos: PermisosDep,
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    _sin_ayuda()
    informar = permisos.informar
    informar.verificar(sobre_unidad(cuerpo.unidad_id))

    def efecto(conn: Connection) -> tuple[int, PagoInformadoRespuesta]:
        fila = _ejecutar(
            conn, informar,
            "select pago_id, ya_informado from nucleo.informar_pago_por_correo(:r, :u, :importe,"
            " :fecha, :medio, :numero, :actor, 'api', :sesion)",
            {"r": recibido_id, "u": cuerpo.unidad_id, "importe": cuerpo.importe,
             "fecha": cuerpo.fecha_pago, "medio": cuerpo.medio.value,
             "numero": cuerpo.numero_operacion},
        ).one()  # fmt: skip
        pago = _pago(_fila(conn, permisos, fila.pago_id), permisos)
        respuesta = PagoInformadoRespuesta(**pago.model_dump(), ya_informado=fila.ya_informado)
        return (200 if fila.ya_informado else 201), respuesta

    return responder_una_vez(motor, informar.ctx, idempotencia, efecto)


@router.post(
    POR_CORREO + "/{recibido_id}/descarte",
    response_model=PagoPorCorreoDescartado,
    summary="Descartar un comprobante recibido por correo que no era un pago",
    responses={
        **RESPUESTAS,
        404: {"description": "No es un comprobante que mandó esta cuenta."},
        409: {"description": "Ya se informó."},
    },
)
def descartar_pago_por_correo(
    recibido_id: UUID, ver: VerDep, idempotencia: IdempotenciaDep, motor: MotorDep
) -> Response:
    _sin_ayuda()

    def efecto(conn: Connection) -> tuple[int, PagoPorCorreoDescartado]:
        _ejecutar(
            conn, ver,
            "select nucleo.descartar_pago_por_correo(:r, :actor, 'api', :sesion)",
            {"r": recibido_id},
        )  # fmt: skip
        return 200, PagoPorCorreoDescartado(id=recibido_id, estado=EstadoPagoPorCorreo.DESCARTADO)

    return responder_una_vez(motor, ver.ctx, idempotencia, efecto)


@router.get(
    POR_CORREO + "/{recibido_id}/comprobante",
    response_class=StreamingResponse,
    summary="Ver un comprobante recibido por correo, si el antivirus lo aceptó",
    responses={
        200: {
            "description": "El archivo, con `inline`, `nosniff` y una CSP que no deja ejecutar"
            " nada.",
            "content": {
                t.value: {"schema": {"type": "string", "format": "binary"}} for t in TipoArchivo
            },
        },
        **RESPUESTAS,
        409: {"description": "Todavía se revisa o fue rechazado."},
        410: {"description": "El archivo ya no está en el almacén (staging, a los 30 días)."},
    },
)
def contenido_pago_por_correo(recibido_id: UUID, ver: VerDep, motor: MotorDep) -> StreamingResponse:
    _sin_ayuda()
    ctx = ver.ctx
    with transaccion(motor, persona_id=ctx.persona_id, consorcio_id=ctx.consorcio_id) as c:
        filas = _ejecutar(
            c, ver,
            "select * from nucleo.comprobante_pago_por_correo_para_ver(:r, :actor, 'api',"
            " :sesion)",
            {"r": recibido_id},
        ).mappings().all()  # fmt: skip
    if not filas:
        raise ErrorApi(404, CodigoError.NO_ENCONTRADO, NO_ENCONTRADO)
    f = filas[0]
    if f["estado"] != EstadoVersion.DISPONIBLE:
        raise ErrorApi(
            409,
            CodigoError.VERSION_NO_DISPONIBLE,
            "Este archivo todavía se está revisando o fue rechazado: no se puede ver.",
        )
    origen = almacen.AlmacenDocumentos.desde_entorno(almacen.DOCUMENTOS)
    try:
        leido = origen.cliente().get_object(**_objeto(origen.bucket, f))
    except ClientError as e:
        raise _vencido(e) from e
    return StreamingResponse(
        _tramos(leido["Body"]),
        media_type=TipoArchivo(f["tipo"]).value,
        headers=CABECERAS_CONTENIDO | {"Content-Length": str(leido["ContentLength"])},
    )
