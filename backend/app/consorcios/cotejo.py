"""`/pagos/cotejo`: cotejar con el banco, a mano o con el extracto (RF12; `0074`).

Diseño: `doc/diseno/Rediseno_Circuito_Pagos_2026-11-06.md` del repositorio CEM, §10. Quién puede y
qué se guarda están en `0074`; cómo se lee y se compara el archivo, en `extracto.py`.

Quién: las reglas de `egresos.conciliar_pago`. Esa política tiene separaciones que dependen del
pago, así que al entrar se pide `egresos.revisar_orden` (la misma facultad y los mismos modos, sin
separación), como en la ruta de conciliar, y la base aplica las separaciones pago por pago.
Leer, sin gesto; escribir, con gesto. Subir el mismo extracto dos veces da el mismo resultado, sin
duplicar nada, y si mientras tanto se concilió otro pago, lo coteja: por eso una línea que ya
está guardada se vuelve a comparar.

Nada de esta ruta toca la orden ni su ejecución.
"""

import json
from typing import Annotated
from uuid import UUID

import anyio
from cem_contracts.cotejo import (
    LIMITE_COTEJO_MAXIMO,
    LIMITE_COTEJO_POR_DEFECTO,
    MAXIMO_FILAS_NO_LEIDAS,
    MAXIMO_FILAS_RESULTADO,
    MAXIMO_PAGOS_POR_COTEJO,
    MONEDA_SUGERIDA,
    AnulacionCotejo,
    ColumnasExtracto,
    CotejoAnulado,
    ListaCotejados,
    ListaCotejo,
    ListaMarcasNoEsPago,
    ListaSinExplicar,
    MarcaNoEsPago,
    MonedaExtracto,
    MovimientoMarcado,
    MovimientoSinRegistrar,
    NoEsPago,
    NombreColumna,
    PagoCotejado,
    PagoPorCotejar,
    PagosACotejar,
    PagosCotejados,
    ResultadoCotejo,
    SignoExtracto,
    VistaExtracto,
)
from cem_contracts.errores import CodigoError
from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import Connection

from app.autorizacion.requiere import Permiso, requiere
from app.consorcios import extracto
from app.consorcios.documentos import ESPERA_TURNO, LECTURA_MAXIMA, _semaforo
from app.consorcios.egresos import (
    REVISAR,
    Preparar,
    Revisar,
    _ejecutar,
    _lectura,
    lector_del_extracto,
    sin_explicar,
)
from app.contexto import MotorDep
from app.errores import ErrorApi, sin_cache
from app.plataforma.idempotencia import (
    ClaveIdempotenciaDep,
    IdempotenciaDep,
    pedido_con_cuerpo,
    responder_una_vez,
)

router = APIRouter(prefix="/api/v1", tags=["egresos"], dependencies=[Depends(sin_cache)])

TIPO_CSV = "text/csv"
BYTES_POR_MB = 1024 * 1024

ERRORES = {
    "pago_sin_conciliar": (
        409, CodigoError.PAGO_SIN_CONCILIAR, "Ese pago todavía no está conciliado.",
    ),
    "movimiento_no_coincide": (
        409, CodigoError.MOVIMIENTO_NO_COINCIDE, "Ese movimiento no coincide con el pago.",
    ),
    "movimiento_no_es_pago": (
        409, CodigoError.MOVIMIENTO_YA_EXPLICADO,
        "Ese débito está marcado como que no es el pago de una orden: no se coteja con un pago.",
    ),
}  # fmt: skip
"""Además de los de `egresos`: `no_encontrado` y las separaciones, igual que al conciliar."""
ERRORES_NO_ES_PAGO = {
    "estado_invalido": (
        409, CodigoError.MOVIMIENTO_YA_EXPLICADO, "Ese débito ya lo explica un pago.",
    ),
}  # fmt: skip
"""Además de los de `egresos`: `no_encontrado` y `motivo_con_cuenta`."""
ERRORES_RETIRO = {
    "sin_marca_vigente": (
        409, CodigoError.SIN_MARCA_VIGENTE,
        "Ese débito no tiene una marca vigente: ya se retiró o no se marcó.",
    ),
}  # fmt: skip
"""Además de los de `egresos`: `no_encontrado` y `motivo_con_cuenta`."""

RESPUESTAS_LECTURA = {
    403: {"description": "Sin `egresos.conciliar_pago`, sin el modo, o falta renovar la sesión."}
}
RESPUESTAS_ESCRITURA = RESPUESTAS_LECTURA | {
    404: {"description": "Ese pago no existe en este consorcio."},
    409: {"description": "El pago todavía no está conciliado."},
}
RESPUESTAS_ARCHIVO = RESPUESTAS_LECTURA | {
    408: {"description": "El cuerpo no llegó entero a tiempo: no se guardó nada."},
    413: {"description": "El archivo pasa del tope (`archivo_demasiado_grande`)."},
    415: {"description": "No se declaró como `text/csv`."},
    422: {"description": "El archivo no se puede leer como un extracto (`extracto_invalido`)."},
    503: {"description": "Demasiadas cargas a la vez: reintentar en un momento."},
}
ARCHIVO_OPENAPI = {
    "requestBody": {
        "required": True,
        "content": {TIPO_CSV: {"schema": {"type": "string", "format": "binary"}}},
    }
}

Leer = Annotated[Permiso, Depends(requiere(REVISAR, al_entrar=True, sin_gesto=True))]
Escribir = Annotated[Permiso, Depends(requiere(REVISAR, al_entrar=True))]


def _pago(fila) -> PagoPorCotejar:
    return PagoPorCotejar(**{k: fila[k] for k in PagoPorCotejar.model_fields})


@router.get(
    "/consorcios/{consorcio_id}/pagos/cotejo",
    response_model=ListaCotejo,
    summary="Lo que falta cotejar con el banco: pagos conciliados conformes, sin la cuenta",
    responses=RESPUESTAS_LECTURA,
)
def listar_cotejo(
    request: Request,
    permiso: Leer,
    motor: MotorDep,
    despues: UUID | None = None,
    limite: Annotated[int, Query(ge=1, le=LIMITE_COTEJO_MAXIMO)] = LIMITE_COTEJO_POR_DEFECTO,
) -> ListaCotejo:
    with _lectura(permiso, motor) as c:
        # Uno de más, para saber si hay otra página.
        filas = _ejecutar(
            c, permiso,
            "select * from nucleo.cotejo_lectura(:d, :l, :actor, 'api', :sesion)",
            {"d": despues, "l": limite + 1},
        ).mappings().all()  # fmt: skip
        guardadas = _columnas_guardadas(c, permiso)
    pagina = [_pago(f) for f in filas[:limite]]
    return ListaCotejo(
        elementos=pagina,
        siguiente=pagina[-1].pago_id if len(filas) > limite else None,
        tope_bytes=request.app.state.config.cotejo_tope_bytes,
        maximo_pagos=MAXIMO_PAGOS_POR_COTEJO,
        moneda_extracto=MONEDA_SUGERIDA if guardadas is None else guardadas.moneda,
    )


@router.post(
    "/consorcios/{consorcio_id}/pagos/cotejo",
    response_model=PagosCotejados,
    summary="Cotejar a mano: el pago salió del banco. No cambia la orden",
    responses=RESPUESTAS_ESCRITURA,
)
def cotejar_a_mano(
    pedido: PagosACotejar, permiso: Escribir, idempotencia: IdempotenciaDep, motor: MotorDep
) -> Response:
    def efecto(conn: Connection) -> tuple[int, PagosCotejados]:
        hechos = _ejecutar(
            conn, permiso,
            "select nucleo.cotejar_pagos(cast(:p as uuid[]), :actor, 'api', :sesion)",
            {"p": list(pedido.pagos)}, ERRORES,
        ).scalar_one()  # fmt: skip
        return 200, PagosCotejados(cotejados=hechos)

    return responder_una_vez(motor, permiso.ctx, idempotencia, efecto)


@router.get(
    "/consorcios/{consorcio_id}/pagos/cotejo/cotejados",
    response_model=ListaCotejados,
    summary="Lo cotejado y vigente, para deshacer un cotejo. Sin la cuenta",
    responses=RESPUESTAS_LECTURA,
)
def listar_cotejados(
    permiso: Leer,
    motor: MotorDep,
    despues: UUID | None = None,
    limite: Annotated[int, Query(ge=1, le=LIMITE_COTEJO_MAXIMO)] = LIMITE_COTEJO_POR_DEFECTO,
) -> ListaCotejados:
    with _lectura(permiso, motor) as c:
        filas = _ejecutar(
            c, permiso,
            "select * from nucleo.cotejados_lectura(:d, :l, :actor, 'api', :sesion)",
            {"d": despues, "l": limite + 1},
        ).mappings().all()  # fmt: skip
    pagina = [PagoCotejado(**f) for f in filas[:limite]]
    return ListaCotejados(
        elementos=pagina, siguiente=pagina[-1].pago_id if len(filas) > limite else None
    )


@router.post(
    "/consorcios/{consorcio_id}/pagos/cotejo/{pago_id}/anulacion",
    response_model=CotejoAnulado,
    summary="Deshacer un cotejo, con motivo y las reglas de cotejar. No se borra y no vuelve a"
    " tildarse solo. No cambia la orden",
    responses=RESPUESTAS_ESCRITURA,
)
def anular_cotejo(
    pago_id: UUID,
    anulacion: AnulacionCotejo,
    permiso: Escribir,
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    def efecto(conn: Connection) -> tuple[int, CotejoAnulado]:
        _ejecutar(
            conn, permiso,
            "select nucleo.anular_cotejo(:p, :motivo, :actor, 'api', :sesion)",
            {"p": pago_id, "motivo": anulacion.motivo}, ERRORES,
        )  # fmt: skip
        return 200, CotejoAnulado(pago_id=pago_id)

    return responder_una_vez(motor, permiso.ctx, idempotencia, efecto)


@router.get(
    "/consorcios/{consorcio_id}/pagos/extracto/sin-explicar",
    response_model=ListaSinExplicar,
    summary="Lo que el extracto del banco no explica: débitos sin un pago en Consorcio en Mano."
    " Lo lee quien concilia y, solo para leer, la administración. Sin la cuenta",
    responses={403: {"description": "Sin `egresos.revisar_orden` ni `egresos.preparar_orden`,"
                                    " sin el modo, o falta renovar la sesión."}},
)  # fmt: skip
def listar_sin_explicar(preparar: Preparar, revisar: Revisar, motor: MotorDep) -> ListaSinExplicar:
    lector = lector_del_extracto(preparar, revisar)
    with _lectura(lector, motor) as c:
        todos = sin_explicar(c, lector)
    return ListaSinExplicar(elementos=todos[:MAXIMO_FILAS_RESULTADO], total=len(todos))


@router.post(
    "/consorcios/{consorcio_id}/pagos/extracto/sin-explicar/{movimiento_id}/no-es-pago",
    response_model=MovimientoMarcado,
    summary="Marcar que un débito del extracto no es el pago de una orden, con motivo. Lo hace"
    " quien concilia, con el gesto. No se borra: queda quién, cuándo y por qué",
    responses=RESPUESTAS_LECTURA
    | {
        404: {"description": "Ese débito no existe en este consorcio."},
        409: {"description": "Ese débito ya lo explica un pago (`movimiento_ya_explicado`)."},
        422: {"description": "El motivo lleva una cuenta (`motivo_con_cuenta`)."},
    },
)
def marcar_no_es_pago(
    movimiento_id: UUID,
    pedido: NoEsPago,
    permiso: Escribir,
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    def efecto(conn: Connection) -> tuple[int, MovimientoMarcado]:
        _ejecutar(
            conn, permiso,
            "select nucleo.marcar_no_es_pago(:m, :motivo, :actor, 'api', :sesion)",
            {"m": movimiento_id, "motivo": pedido.motivo}, ERRORES_NO_ES_PAGO,
        )  # fmt: skip
        return 200, MovimientoMarcado(movimiento_id=movimiento_id)

    return responder_una_vez(motor, permiso.ctx, idempotencia, efecto)


@router.get(
    "/consorcios/{consorcio_id}/pagos/extracto/no-es-pago",
    response_model=ListaMarcasNoEsPago,
    summary="Los débitos con la marca vigente «No es el pago de una orden», con su motivo. Los lee"
    " quien concilia y, solo para leer, la administración",
    responses={403: {"description": "Sin `egresos.revisar_orden` ni `egresos.preparar_orden`,"
                                    " sin el modo, o falta renovar la sesión."}},
)  # fmt: skip
def listar_marcas_no_es_pago(
    preparar: Preparar, revisar: Revisar, motor: MotorDep
) -> ListaMarcasNoEsPago:
    lector = lector_del_extracto(preparar, revisar)
    with _lectura(lector, motor) as c:
        filas = _ejecutar(
            c, lector, "select * from nucleo.marcas_no_es_pago_lectura(:actor, 'api', :sesion)", {}
        ).mappings().all()  # fmt: skip
    marcas = [MarcaNoEsPago(**f) for f in filas]
    return ListaMarcasNoEsPago(elementos=marcas[:MAXIMO_FILAS_RESULTADO], total=len(marcas))


@router.post(
    "/consorcios/{consorcio_id}/pagos/extracto/no-es-pago/{movimiento_id}/retiro",
    response_model=MovimientoMarcado,
    summary="Retirar la marca «No es el pago de una orden», con motivo. Lo hace quien concilia, con"
    " el gesto. No se borra: queda quién la retiró, cuándo y por qué, y el débito vuelve a lo que"
    " el extracto no explica",
    responses=RESPUESTAS_LECTURA
    | {
        404: {"description": "Ese débito no existe en este consorcio."},
        409: {"description": "Ese débito no tiene una marca vigente (`sin_marca_vigente`)."},
        422: {"description": "El motivo lleva una cuenta (`motivo_con_cuenta`)."},
    },
)
def retirar_no_es_pago(
    movimiento_id: UUID,
    pedido: NoEsPago,
    permiso: Escribir,
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    def efecto(conn: Connection) -> tuple[int, MovimientoMarcado]:
        _ejecutar(
            conn, permiso,
            "select nucleo.retirar_no_es_pago(:m, :motivo, :actor, 'api', :sesion)",
            {"m": movimiento_id, "motivo": pedido.motivo}, ERRORES_RETIRO,
        )  # fmt: skip
        return 200, MovimientoMarcado(movimiento_id=movimiento_id)

    return responder_una_vez(motor, permiso.ctx, idempotencia, efecto)


def _demasiado_grande(tope: int) -> ErrorApi:
    return ErrorApi(
        413, CodigoError.ARCHIVO_DEMASIADO_GRANDE,
        f"El archivo pasa de {tope // BYTES_POR_MB} MB: no se guardó.",
    )  # fmt: skip


async def _cuerpo(request: Request, tope: int) -> bytes:
    """El archivo en tramos, con un contador: corta al pasar el tope."""
    declarado = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if declarado != TIPO_CSV:
        raise ErrorApi(
            415, CodigoError.TIPO_NO_ADMITIDO, "Solo se aceptan archivos CSV: no se guardó."
        )
    largo = request.headers.get("content-length")
    if largo is not None and largo.isdigit() and int(largo) > tope:
        raise _demasiado_grande(tope)
    cuerpo = bytearray()
    try:
        with anyio.fail_after(LECTURA_MAXIMA):
            async for tramo in request.stream():
                cuerpo += tramo
                if len(cuerpo) > tope:
                    raise _demasiado_grande(tope)
    except TimeoutError:
        raise ErrorApi(
            408, CodigoError.PETICION_INVALIDA,
            "El archivo tardó demasiado en llegar: no se guardó.",
        ) from None  # fmt: skip
    return bytes(cuerpo)


class _Turno:
    """Las mismas cargas por proceso que los documentos: cada extracto ocupa memoria."""

    async def __aenter__(self) -> None:
        self.turnos = _semaforo()
        try:
            with anyio.fail_after(ESPERA_TURNO):
                await self.turnos.acquire()
        except TimeoutError:
            raise ErrorApi(
                503, CodigoError.SERVICIO_NO_DISPONIBLE,
                "Hay muchas cargas en curso. Reintentar en un momento.",
                {"Retry-After": str(ESPERA_TURNO)},
            ) from None  # fmt: skip

    async def __aexit__(self, *_) -> None:
        self.turnos.release()


def _tabla(cuerpo: bytes, request: Request) -> extracto.Tabla:
    try:
        return extracto.leer_tabla(cuerpo, request.app.state.config.cotejo_maximo_lineas)
    except extracto.DemasiadasLineas as e:
        raise ErrorApi(
            422, CodigoError.EXTRACTO_DEMASIADAS_LINEAS,
            "El archivo tiene demasiadas líneas: hay que bajar del banco un período más corto.",
        ) from e  # fmt: skip
    except extracto.ExtractoInvalido as e:
        raise ErrorApi(
            422, CodigoError.EXTRACTO_INVALIDO,
            "El archivo no se pudo leer como un extracto en CSV: tiene que ser el que se bajó del"
            " banco.",
        ) from e  # fmt: skip


def _columnas_guardadas(c: Connection, permiso: Permiso) -> ColumnasExtracto | None:
    fila = _ejecutar(
        c, permiso, "select * from nucleo.columnas_extracto_lectura(:actor, 'api', :sesion)", {}
    ).mappings().one_or_none()  # fmt: skip
    if fila is None:
        return None
    return ColumnasExtracto(
        fecha=fila["columna_fecha"], importe=fila["columna_importe"],
        numero_operacion=fila["columna_numero"], signo=fila["signo"], moneda=fila["moneda"],
    )  # fmt: skip


@router.post(
    "/consorcios/{consorcio_id}/pagos/cotejo/columnas",
    response_model=VistaExtracto,
    summary="Las columnas de un extracto, para elegir qué es cada una. No guarda nada",
    openapi_extra=ARCHIVO_OPENAPI,
    responses=RESPUESTAS_ARCHIVO,
)
async def columnas_del_extracto(request: Request, permiso: Leer, motor: MotorDep) -> VistaExtracto:
    tope = request.app.state.config.cotejo_tope_bytes

    def leer(cuerpo: bytes) -> VistaExtracto:
        tabla = _tabla(cuerpo, request)
        with _lectura(permiso, motor) as c:
            guardadas = _columnas_guardadas(c, permiso)
        if guardadas is not None and not extracto.tiene_columnas(tabla, guardadas):
            guardadas = None
        return VistaExtracto(
            columnas=tabla.columnas, muestra=extracto.muestra(tabla),
            signo_sugerido=extracto.signo_sugerido(tabla), columnas_guardadas=guardadas,
            columnas_reconocidas=extracto.reconocer_columnas(tabla),
        )  # fmt: skip

    async with _Turno():
        cuerpo = await _cuerpo(request, tope)
        return await anyio.to_thread.run_sync(leer, cuerpo)


def _comparables(filas) -> list[extracto.PagoComparable]:
    return [
        extracto.PagoComparable(**{k: f[k] for k in extracto.PagoComparable.__dataclass_fields__})
        for f in filas
    ]


def _comparar_y_cotejar(
    conn: Connection, permiso: Permiso, lectura: extracto.Lectura, columnas: ColumnasExtracto,
    hash_archivo: str,
) -> ResultadoCotejo:  # fmt: skip
    guardados = _ejecutar(
        conn, permiso,
        "select * from nucleo.importar_extracto(:a, :d, :h, cast(:m as jsonb), :cf, :ci, :cn, :s,"
        " :actor, 'api', :sesion, :moneda)",
        {
            "a": hash_archivo, "d": lectura.desde, "h": lectura.hasta,
            "m": json.dumps([{
                "hash_linea": m.hash_linea, "fecha": m.fecha.isoformat(),
                "importe_centavos": m.importe_centavos, "numero_operacion": m.numero_operacion,
            } for m in lectura.movimientos]),
            "cf": columnas.fecha, "ci": columnas.importe, "cn": columnas.numero_operacion,
            "s": columnas.signo.value, "moneda": columnas.moneda,
        },
    ).mappings().all()  # fmt: skip
    # Los marcados como «no es el pago de una orden» no se comparan: la base tampoco los coteja.
    marcados = set(_ejecutar(
        conn, permiso,
        "select * from nucleo.no_es_pago_lectura(cast(:m as uuid[]), :actor, 'api', :sesion)",
        {"m": [g["movimiento_id"] for g in guardados]},
    ).scalars())  # fmt: skip
    movimientos = [
        extracto.MovimientoGuardado(
            id=g["movimiento_id"],
            fecha=g["fecha"],
            importe_centavos=g["importe_centavos"],
            moneda=g["moneda"],
            numero_operacion=g["numero_operacion"],
            pago_id=g["pago_id"],
            no_es_pago=g["movimiento_id"] in marcados,
        )  # fmt: skip
        for g in guardados
    ]
    numeros = sorted({m.numero_operacion for m in movimientos if m.numero_operacion is not None})
    filas = _ejecutar(
        conn, permiso,
        "select * from nucleo.pagos_para_comparar_lectura(cast(:n as text[]), :d, :h, :actor,"
        " 'api', :sesion)",
        {"n": numeros, "d": lectura.desde, "h": lectura.hasta},
    ).mappings().all()  # fmt: skip
    comparacion = extracto.comparar(movimientos, _comparables(filas), lectura.desde, lectura.hasta)

    a_cotejar = [
        {"movimiento": str(m.id), "pago": str(p.pago_id)}
        for m, p in comparacion.pares
        if p.conforme and not p.cotejado and not p.anulado and p.puede_cotejar
    ]
    cotejados = 0
    if a_cotejar:
        cotejados = _ejecutar(
            conn, permiso,
            "select nucleo.cotejar_por_extracto(cast(:p as jsonb), :actor, 'api', :sesion)",
            {"p": json.dumps(a_cotejar)}, ERRORES,
        ).scalar_one()  # fmt: skip

    pagos = [p for _, p in comparacion.pares]
    por_id = {f["pago_id"]: f for f in filas}
    nuevos = sum(1 for g in guardados if g["nuevo"])
    return ResultadoCotejo(
        desde=lectura.desde, hasta=lectura.hasta,
        movimientos_nuevos=nuevos, movimientos_repetidos=len(guardados) - nuevos,
        cotejados=cotejados,
        ya_cotejados=comparacion.ya_atados + sum(1 for p in pagos if p.cotejado),
        esperan_conciliar=sum(1 for p in pagos if not p.conforme),
        para_otra_persona=sum(
            1 for p in pagos
            if p.conforme and not p.cotejado and not p.anulado and not p.puede_cotejar
        ),
        anulados=sum(1 for p in pagos if p.conforme and not p.cotejado and p.anulado),
        lineas_no_leidas=len(lectura.filas_no_leidas),
        filas_no_leidas=lectura.filas_no_leidas[:MAXIMO_FILAS_NO_LEIDAS],
        aca_no_banco=[
            _pago(por_id[p.pago_id])
            for p in comparacion.sin_movimiento[:MAXIMO_FILAS_RESULTADO]
        ],
        total_aca_no_banco=len(comparacion.sin_movimiento),
        banco_no_aca=[
            MovimientoSinRegistrar(
                fecha=m.fecha, importe_centavos=m.importe_centavos, moneda=m.moneda,
                numero_operacion=m.numero_operacion,
            )  # fmt: skip
            for m in comparacion.sin_pago[:MAXIMO_FILAS_RESULTADO]
        ],
        total_banco_no_aca=len(comparacion.sin_pago),
    )  # fmt: skip


def _columnas_elegidas(
    fecha: NombreColumna | None,
    importe: NombreColumna | None,
    numero_operacion: NombreColumna | None,
    signo: SignoExtracto | None,
) -> ColumnasExtracto | None:
    """Las del pedido; sin ninguna, las guardadas del edificio."""
    if fecha is None and importe is None and numero_operacion is None and signo is None:
        return None
    if fecha is None or importe is None or signo is None:
        raise ErrorApi(
            422, CodigoError.PETICION_INVALIDA, "Faltan la fecha, el importe o cómo van los pagos."
        )
    return ColumnasExtracto(
        fecha=fecha, importe=importe, numero_operacion=numero_operacion, signo=signo
    )


@router.post(
    "/consorcios/{consorcio_id}/pagos/cotejo/extracto",
    response_model=ResultadoCotejo,
    summary="Subir un extracto: lo que coincide queda cotejado, y responde lo que hay que mirar."
    " No cambia ninguna orden",
    openapi_extra=ARCHIVO_OPENAPI,
    responses=RESPUESTAS_ARCHIVO
    | {409: {"description": "Falta elegir las columnas (`columnas_sin_elegir`)."}},
)
async def subir_extracto(
    request: Request,
    permiso: Escribir,
    clave: ClaveIdempotenciaDep,
    motor: MotorDep,
    fecha: NombreColumna | None = None,
    importe: NombreColumna | None = None,
    numero_operacion: NombreColumna | None = None,
    signo: SignoExtracto | None = None,
    moneda: MonedaExtracto | None = None,
) -> Response:
    elegidas = _columnas_elegidas(fecha, importe, numero_operacion, signo)
    tope = request.app.state.config.cotejo_tope_bytes

    def procesar(cuerpo: bytes) -> Response:
        # Todo adentro del efecto: un reintento recibe la respuesta guardada, aunque mientras tanto
        # hayan cambiado las columnas del edificio o el máximo de líneas.
        def efecto(conn: Connection) -> tuple[int, ResultadoCotejo]:
            tabla = _tabla(cuerpo, request)
            columnas = elegidas or _columnas_guardadas(conn, permiso)
            if columnas is None:
                raise ErrorApi(
                    409, CodigoError.COLUMNAS_SIN_ELEGIR, "Falta elegir qué columna es cada dato."
                )
            if moneda is not None:
                columnas = columnas.model_copy(update={"moneda": moneda})
            try:
                lectura = extracto.leer_movimientos(tabla, columnas)
            except extracto.ColumnasNoEncontradas as e:
                raise ErrorApi(
                    422, CodigoError.COLUMNAS_NO_ENCONTRADAS,
                    "El archivo no tiene las columnas elegidas: hay que elegirlas de nuevo.",
                ) from e  # fmt: skip
            if lectura is None:
                raise ErrorApi(
                    422, CodigoError.EXTRACTO_SIN_MOVIMIENTOS,
                    "No se pudo leer ninguna línea con esas columnas: hay que revisar cuál es "
                    "cada dato.",
                )  # fmt: skip
            suma = extracto.hash_archivo(cuerpo)
            return 200, _comparar_y_cotejar(conn, permiso, lectura, columnas, suma)

        pedido = pedido_con_cuerpo(request, clave, cuerpo)
        return responder_una_vez(motor, permiso.ctx, pedido, efecto)

    async with _Turno():
        cuerpo = await _cuerpo(request, tope)
        return await anyio.to_thread.run_sync(procesar, cuerpo)
