"""Reparaciones a cargo del consorcio (RF19, con la regla de cierre de RF20; Modo Consejo, `0124`).

Diseño P0 S10 a S12 y plan §5.2 y §6.8 del repositorio CEM; decisiones del 22-11-2026. Todas las
rutas cuelgan de `…/reparaciones`; cada paso de quien gestiona es un `POST` con su nombre (como los
actos de la solicitud formal y de los reclamos):

- **`POST …/reparaciones`** (`reparaciones.pedir`): la descripción y el lugar (una unidad propia o
  un espacio común). Sale un correo a cada persona con `reparaciones.gestionar`.
- **`POST …/{r}/adjuntos?tipo=…`**: un archivo (el cuerpo crudo, como en Documentos), que entra en
  cuarentena hasta que el antivirus lo acepta. Las fotos las sube quien pidió (`reparaciones.pedir`)
  mientras está abierta; el presupuesto, la constancia de aprobación y la factura, quien gestiona,
  en el estado del acto que los va a citar.
- **`GET …/{r}/adjuntos/{a}/contenido`** (`reparaciones.ver`): el archivo, si está disponible.
- **`POST …/{r}/gestion`**, **`…/presupuesto`**, **`…/aprobacion`**, **`…/terminacion`**,
  **`…/gasto`** y **`…/no-realizada`** (`reparaciones.gestionar`), y **`…/retiro`** (quien la pidió,
  mientras está pedida). Cada uno le avisa por correo a quien la pidió, salvo que lo haga ella.
- **`GET …/reparaciones`** y **`GET …/{r}`** (`reparaciones.ver`): quien gestiona ve todas; quien
  pidió, las suyas. La ficha trae solo las acciones que valen ahora y, para quien gestiona, lo que
  puede elegir en ellas.

La política decide al entrar; la base repite la barrera en cada función. Si la base niega lo que el
evaluador concedió, es una discrepancia (500).
"""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

import anyio
from botocore.exceptions import ClientError
from cem_contracts.documentos import EstadoVersion, TipoArchivo, VersionCargada
from cem_contracts.errores import CodigoError
from cem_contracts.reparaciones import (
    ABIERTOS,
    FOTOS_MAXIMO,
    LARGO_APROBADO_POR,
    LARGO_DESCRIPCION,
    LARGO_LUGAR,
    LARGO_MOTIVO,
    LARGO_PROVEEDOR,
    AccionReparacion,
    AdjuntoReparacion,
    AdjuntoReparacionSubido,
    AprobacionReparacion,
    DecisionCitable,
    EstadoReparacion,
    GastoOficial,
    GastoReparacion,
    GestionReparacion,
    GestionVigente,
    ListaReparaciones,
    MotivoReparacion,
    PasoReparacion,
    Persona,
    PresupuestoReparacion,
    Reparacion,
    ReparacionNueva,
    ReparacionResumen,
    TerminacionReparacion,
    TipoAdjunto,
    UnidadParaPedir,
)
from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import StreamingResponse
from psycopg.errors import InsufficientPrivilege
from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError

from app.autorizacion.requiere import NO_ENCONTRADO, SIN_PERMISO, VOLVER_A_ENTRAR, Permiso, requiere
from app.consorcios.documentos import (
    CABECERAS_CONTENIDO,
    RESPUESTAS_SUBIDA,
    SUBIDA_OPENAPI,
    _objeto,
    _tramos,
    _vencido,
    subir_archivo,
)
from app.consorcios.padron import Discrepancia
from app.contexto import MotorDep
from app.db import transaccion
from app.errores import ErrorApi, detalle_de_campo, sin_cache
from app.identidad.invitaciones import origen_publico
from app.plataforma import almacen, correo
from app.plataforma.idempotencia import ClaveIdempotenciaDep, IdempotenciaDep, responder_una_vez

router = APIRouter(prefix="/api/v1", tags=["consorcios"], dependencies=[Depends(sin_cache)])

VER = "reparaciones.ver"
PEDIR = "reparaciones.pedir"
GESTIONAR = "reparaciones.gestionar"
RUTA_REPARACIONES = "/reparaciones"
"""La pantalla «Seguimiento» (`frontend/src/reparaciones/Reparaciones.tsx`)."""
BASE = "/consorcios/{consorcio_id}/reparaciones"
UNA = BASE + "/{reparacion_id}"

ERRORES = {
    "texto_con_cuenta": (
        422, CodigoError.TEXTO_CON_CUENTA,
        "Una reparación no puede llevar el número de una cuenta: hay que sacarlo del texto. Si no"
        " es una cuenta, separar los números con una coma.",
    ),
    "reparacion_no_admite": (
        409, CodigoError.REPARACION_NO_ADMITE,
        "La reparación ya no admite ese paso: otra persona la movió o ya terminó. Hay que volver a"
        " abrirla para ver cómo quedó.",
    ),
    "responsable_invalido": (
        422, CodigoError.RESPONSABLE_INVALIDO,
        "Esa persona no puede ser responsable: no gestiona reparaciones en este edificio.",
    ),
    "constancia_invalida": (
        422, CodigoError.CONSTANCIA_INVALIDA,
        "La constancia cita una decisión formal de este edificio o un documento firmado, uno de"
        " los dos.",
    ),
    "gasto_no_oficial": (
        422, CodigoError.GASTO_NO_OFICIAL,
        "Ese gasto no figura en una liquidación oficial vigente de este edificio.",
    ),
    "gasto_ya_enlazado": (
        409, CodigoError.GASTO_YA_ENLAZADO,
        "Ese gasto ya cierra otra reparación: un gasto cierra una sola.",
    ),
    "adjunto_invalido": (
        422, CodigoError.ADJUNTO_INVALIDO,
        "El archivo no sirve para este paso: todavía se está revisando, el antivirus lo rechazó, ya"
        " se usó o lo subió otra persona.",
    ),
    "cuit_invalido": (422, CodigoError.CUIT_INVALIDO, "El CUIT no es válido."),
    "fotos_completas": (
        409, CodigoError.FOTOS_COMPLETAS,
        f"La reparación ya tiene {FOTOS_MAXIMO} fotos, las que admite.",
    ),
    "no_encontrado": (404, CodigoError.NO_ENCONTRADO, NO_ENCONTRADO),
    "sesion_invalida": (403, *VOLVER_A_ENTRAR),
}  # fmt: skip
"""Motivos de las funciones de `0124` que la persona puede provocar. Los demás (`modo_no_admitido`,
`origen_invalido`) son un defecto si llegan: el evaluador o la ruta ya los impiden."""
CAMPO_DEL_MOTIVO = {
    "responsable_invalido": "responsable_id",
    "constancia_invalida": "decision_id",
    "gasto_no_oficial": "clave",
    "gasto_ya_enlazado": "clave",
    "adjunto_invalido": "adjunto_id",
    "cuit_invalido": "proveedor_cuit",
}
"""El campo del cuerpo que motiva el error, para que la pantalla lo marque. `texto_con_cuenta`
depende del acto: lo dice cada ruta."""

RESPUESTAS = {
    403: {"description": "Sin un vínculo confirmado que cuente o sin la facultad."},
    404: {"description": "La reparación no existe en este consorcio, o no es de esta persona."},
}
RESPUESTAS_ACTO = RESPUESTAS | {
    409: {"description": "La reparación no admite ese paso en su estado (`reparacion_no_admite`)."},
    422: {"description": "Un dato no sirve (el archivo, el responsable, la constancia, el CUIT)."},
}
ASUNTO_PEDIDA = "Pedido de reparación N.º {numero} en {consorcio}"
TEXTO_PEDIDA = (
    "Hola:\n\n"
    "Hay un pedido de reparación nuevo, el N.º {numero}, en {consorcio} en Consorcio en Mano.\n\n"
    "Se lee y se gestiona en la aplicación: {enlace}\n"
)
ASUNTO_ESTADO = "La reparación N.º {numero} de {consorcio} cambió de estado"
TEXTO_ESTADO = (
    "Hola:\n\n"
    "La reparación N.º {numero} que pidió en {consorcio} cambió de estado en Consorcio en Mano.\n\n"
    "Se ve en la aplicación: {enlace}\n"
)
"""Solo el número y el enlace: ni la descripción, ni el lugar, ni las fotos, ni quién la pidió."""


def _una_linea(texto_: str) -> str:
    return " ".join(texto_.split())


def ejecutar(
    conn: Connection, permiso: Permiso, sql: str, parametros: dict, campo_texto: str | None = None
):
    """Llama a una función de `0124` con la persona y la sesión, y traduce sus motivos."""
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
            campo = campo_texto if motivo == "texto_con_cuenta" else CAMPO_DEL_MOTIVO.get(motivo)
            raise ErrorApi(*ERRORES[motivo], detalle=detalle_de_campo(campo)) from e
        raise


@dataclass(frozen=True)
class Permisos:
    """Lo que la persona puede hacer con las reparaciones; `ver`, que se verifica al entrar, da el
    contexto. Para decidir qué ofrecer: cada acto vuelve a verificar su acción."""

    ver: Permiso
    pedir: Permiso
    gestionar: Permiso


def _permisos(
    ver: Annotated[Permiso, Depends(requiere(VER, al_entrar=True))],
    pedir: Annotated[Permiso, Depends(requiere(PEDIR))],
    gestionar: Annotated[Permiso, Depends(requiere(GESTIONAR))],
) -> Permisos:
    return Permisos(ver, pedir, gestionar)


PermisosDep = Annotated[Permisos, Depends(_permisos)]


@contextmanager
def _lectura(permiso: Permiso, motor) -> Iterator[Connection]:
    ctx = permiso.ctx
    with transaccion(motor, persona_id=ctx.persona_id, consorcio_id=ctx.consorcio_id) as c:
        yield c


LEER = "select * from nucleo.reparaciones_lectura(:actor, 'api', :sesion)"
OPCIONES = "select clase, fila from nucleo.opciones_reparaciones(:actor, 'api', :sesion)"


def _filas(conn: Connection, permiso: Permiso, reparacion: UUID | None = None) -> list:
    sql = LEER if reparacion is None else LEER + " where id = :r"
    return ejecutar(conn, permiso, sql, {"r": reparacion}).mappings().all()


def _opciones(conn: Connection, permiso: Permiso) -> dict[str, list[dict]]:
    opciones: dict[str, list[dict]] = {"unidad": [], "responsable": [], "decision": [], "gasto": []}
    for f in ejecutar(conn, permiso, OPCIONES, {}).mappings():
        opciones[f["clase"]].append(f["fila"])
    return opciones


def _gasto(liquidacion, periodo, clave: str, valor: dict) -> GastoOficial:
    return GastoOficial(
        liquidacion_id=liquidacion, periodo=periodo, clave=clave,
        concepto=valor.get("concepto"), total=valor.get("total"),
    )  # fmt: skip


def _resumen(f) -> dict:
    return {
        "id": f["id"], "numero": f["numero"], "estado": f["estado"], "unidad_id": f["unidad_id"],
        "designacion": f["designacion"], "lugar": f["lugar"],
        "gestion": None if f["gestion"] is None else GestionVigente(**f["gestion"]),
        "pedida_por_nombre": f["pedida_por_nombre"], "pedida_en": f["pedida_en"],
        "propia": f["propia"],
    }  # fmt: skip


def _paso(p: dict) -> PasoReparacion:
    gasto = None
    if p["gasto_clave"] is not None:
        gasto = _gasto(p["liquidacion_id"], p["periodo"], p["gasto_clave"], p["gasto"] or {})
    campos = {k: v for k, v in p.items() if k not in {"liquidacion_id", "periodo", "gasto_clave"}}
    return PasoReparacion(**(campos | {"gasto": gasto}))


def _acciones(f, adjuntos: list[AdjuntoReparacion], permisos: Permisos, opciones: dict):
    estado = EstadoReparacion(f["estado"])
    acciones: list[AccionReparacion] = []
    if permisos.gestionar.decidir().permitido and estado in ABIERTOS:
        # Primero el paso siguiente; sin un gasto para enlazar no se ofrece el cierre (la ficha
        # avisa que llega con la próxima liquidación oficial).
        siguiente = {
            EstadoReparacion.PEDIDA: AccionReparacion.GESTIONAR,
            EstadoReparacion.EN_GESTION: AccionReparacion.PRESUPUESTAR,
            EstadoReparacion.PRESUPUESTADA: AccionReparacion.APROBAR,
            EstadoReparacion.APROBADA: AccionReparacion.TERMINAR,
            EstadoReparacion.TERMINADA_GASTO_PENDIENTE: AccionReparacion.ENLAZAR_GASTO
            if opciones["gasto"]
            else None,
        }[estado]
        if siguiente is not None:
            acciones.append(siguiente)
        if estado is not EstadoReparacion.PEDIDA:
            acciones.append(AccionReparacion.GESTIONAR)  # cambiar el responsable o la fecha
        acciones.append(AccionReparacion.NO_REALIZADA)
    if f["propia"] and permisos.pedir.decidir().permitido:
        fotos = [a for a in adjuntos if a.tipo is TipoAdjunto.FOTO]
        vigentes = [a for a in fotos if a.estado is not EstadoVersion.RECHAZADO]
        if estado in ABIERTOS and len(vigentes) < FOTOS_MAXIMO:
            acciones.append(AccionReparacion.AGREGAR_FOTO)
        if estado is EstadoReparacion.PEDIDA:
            acciones.append(AccionReparacion.RETIRAR)
    return acciones


def _reparacion(f, permisos: Permisos, opciones: dict) -> Reparacion:
    adjuntos = [AdjuntoReparacion(**a) for a in f["adjuntos"]]
    acciones = _acciones(f, adjuntos, permisos, opciones)
    return Reparacion(
        **_resumen(f),
        descripcion=f["descripcion"],
        historia=[_paso(p) for p in f["historia"]],
        adjuntos=adjuntos,
        acciones=acciones,
        responsables_posibles=[Persona(**p) for p in opciones["responsable"]]
        if AccionReparacion.GESTIONAR in acciones
        else [],
        decisiones_citables=[DecisionCitable(**d) for d in opciones["decision"]]
        if AccionReparacion.APROBAR in acciones
        else [],
        gastos_enlazables=[
            _gasto(g["liquidacion_id"], g["periodo"], g["clave"], g["valor"])
            for g in opciones["gasto"]
        ]
        if AccionReparacion.ENLAZAR_GASTO in acciones
        else [],
    )


def _una(conn: Connection, permisos: Permisos, reparacion: UUID) -> Reparacion:
    filas = _filas(conn, permisos.ver, reparacion)
    if not filas:
        raise ErrorApi(404, CodigoError.NO_ENCONTRADO, NO_ENCONTRADO)
    return _reparacion(filas[0], permisos, _opciones(conn, permisos.ver))


def _avisar(conn: Connection, permiso: Permiso, filas, enlace: str) -> None:
    """El correo del cambio de estado a quien la pidió (una fila con `persona_id` nulo, o ninguna,
    si no hay a quién)."""
    for a in filas:
        if a["persona_id"] is None:
            continue
        nombre = _una_linea(a["consorcio_nombre"])
        correo.solicitar(
            conn, para=a["correo"], asunto=ASUNTO_ESTADO.format(numero=a["numero"],
                                                              consorcio=nombre),
            texto=TEXTO_ESTADO.format(numero=a["numero"], consorcio=nombre, enlace=enlace),
            consorcio_id=permiso.ctx.consorcio_id,
        )  # fmt: skip


@router.get(
    BASE,
    response_model=ListaReparaciones,
    summary="Reparaciones a cargo del consorcio: todas, para quien gestiona; las propias, para"
    " quien las pidió",
    responses={403: RESPUESTAS[403]},
)
def listar_reparaciones(permisos: PermisosDep, motor: MotorDep) -> ListaReparaciones:
    with _lectura(permisos.ver, motor) as c:
        elementos = [ReparacionResumen(**_resumen(f)) for f in _filas(c, permisos.ver)]
        opciones = _opciones(c, permisos.ver)
    return ListaReparaciones(
        elementos=elementos,
        gestiona=permisos.gestionar.decidir().permitido,
        pide=permisos.pedir.decidir().permitido,
        unidades=[UnidadParaPedir(**u) for u in opciones["unidad"]],
        largo_descripcion=LARGO_DESCRIPCION,
        largo_lugar=LARGO_LUGAR,
        largo_motivo=LARGO_MOTIVO,
        largo_proveedor=LARGO_PROVEEDOR,
        largo_aprobado_por=LARGO_APROBADO_POR,
        fotos_maximo=FOTOS_MAXIMO,
    )


@router.get(
    UNA,
    response_model=Reparacion,
    summary="Una reparación con su historia, sus archivos y lo que la persona puede hacer ahora",
    responses=RESPUESTAS,
)
def ver_reparacion(reparacion_id: UUID, permisos: PermisosDep, motor: MotorDep) -> Reparacion:
    with _lectura(permisos.ver, motor) as c:
        return _una(c, permisos, reparacion_id)


@router.post(
    BASE,
    status_code=201,
    response_model=Reparacion,
    summary="Pedir una reparación a cargo del consorcio; avisa por correo a quien gestiona",
    responses={**RESPUESTAS, 422: {"description": "La descripción o el lugar llevan una cuenta."}},
)
def pedir_reparacion(
    request: Request,
    cuerpo: ReparacionNueva,
    permisos: PermisosDep,
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    pedir = permisos.pedir
    pedir.verificar()
    enlace = f"{origen_publico(request)}{RUTA_REPARACIONES}"

    def efecto(conn: Connection) -> tuple[int, Reparacion]:
        if cuerpo.unidad_id is not None:
            # Quien no gestiona pide solo por una unidad suya (la base lo repite).
            unidades = {str(u["id"]) for u in _opciones(conn, permisos.ver)["unidad"]}
            if str(cuerpo.unidad_id) not in unidades:
                raise ErrorApi(403, CodigoError.SIN_PERMISO, SIN_PERMISO)
        avisar = ejecutar(
            conn, pedir,
            "select * from nucleo.pedir_reparacion(:descripcion, :u, :lugar, :actor, 'api',"
            " :sesion)",
            {"descripcion": cuerpo.descripcion, "u": cuerpo.unidad_id, "lugar": cuerpo.lugar},
            campo_texto="descripcion",
        ).mappings().all()  # fmt: skip
        nueva = avisar[0]["reparacion_id"]
        nombre = _una_linea(avisar[0]["consorcio_nombre"])
        numero = avisar[0]["numero"]
        for a in avisar:
            if a["persona_id"] is None:
                continue
            correo.solicitar(
                conn, para=a["correo"], asunto=ASUNTO_PEDIDA.format(numero=numero,
                                                                  consorcio=nombre),
                texto=TEXTO_PEDIDA.format(numero=numero, consorcio=nombre, enlace=enlace),
                consorcio_id=pedir.ctx.consorcio_id,
            )  # fmt: skip
        return 201, _una(conn, permisos, nueva)

    return responder_una_vez(motor, pedir.ctx, idempotencia, efecto)


@router.post(
    UNA + "/adjuntos",
    status_code=201,
    response_model=AdjuntoReparacionSubido,
    summary="Subir una foto del pedido, o el presupuesto, la constancia de aprobación o la factura:"
    " entra en cuarentena hasta que el antivirus lo acepta",
    openapi_extra=SUBIDA_OPENAPI,
    responses=RESPUESTAS_ACTO | RESPUESTAS_SUBIDA,
)
async def subir_adjunto(
    reparacion_id: UUID,
    tipo: Annotated[TipoAdjunto, Query(description="Qué es el archivo.")],
    request: Request,
    permisos: PermisosDep,
    clave: ClaveIdempotenciaDep,
    motor: MotorDep,
) -> Response:
    """Una foto la sube quien pidió la reparación mientras está abierta; lo demás, quien gestiona,
    en el estado del acto que lo va a citar. El acto lo cita recién cuando está disponible."""
    permiso = permisos.pedir if tipo is TipoAdjunto.FOTO else permisos.gestionar
    await anyio.to_thread.run_sync(permiso.verificar)

    def nueva(conn: Connection, tipo_archivo: str, tamano: int, suma: str):
        return ejecutar(
            conn, permiso,
            "select nueva_version_id as id, nueva_clave_objeto as clave_objeto,"
            " nuevo_documento_id as documento_id, nuevo_adjunto_id as adjunto_id"
            " from nucleo.subir_adjunto_reparacion(:r, :tipo, :t, :n, :suma, :actor, 'api',"
            " :sesion)",
            {"r": reparacion_id, "tipo": tipo.value, "t": tipo_archivo, "n": tamano, "suma": suma},
        ).one()  # fmt: skip

    def respuesta(fila, cargada: VersionCargada) -> AdjuntoReparacionSubido:
        return AdjuntoReparacionSubido(adjunto_id=fila.adjunto_id, documento_id=fila.documento_id,
                                       archivo=cargada)  # fmt: skip

    return await subir_archivo(request, permiso.ctx, clave, motor, nueva, respuesta)


@router.get(
    UNA + "/adjuntos/{adjunto_id}/contenido",
    response_class=StreamingResponse,
    summary="Ver un archivo de la reparación: por el mismo origen, si el antivirus lo aceptó",
    responses={
        200: {
            "description": "El archivo, con `inline`, `nosniff` y una CSP que no deja ejecutar"
            " nada.",
            "content": {
                t.value: {"schema": {"type": "string", "format": "binary"}} for t in TipoArchivo
            },
        },
        **RESPUESTAS,
        409: {"description": "Lo subió quien pregunta y todavía se revisa o fue rechazado."},
        410: {"description": "El archivo ya no está en el almacén (staging, a los 30 días)."},
    },
)
def contenido_de_adjunto(
    reparacion_id: UUID, adjunto_id: UUID, permisos: PermisosDep, motor: MotorDep
) -> StreamingResponse:
    with _lectura(permisos.ver, motor) as c:
        filas = ejecutar(
            c, permisos.ver,
            "select * from nucleo.adjunto_reparacion_para_ver(:r, :a, :actor, 'api', :sesion)",
            {"r": reparacion_id, "a": adjunto_id},
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


def _acto(
    request: Request,
    reparacion_id: UUID,
    permisos: PermisosDep,
    idempotencia,
    motor,
    sql: str,
    parametros: dict,
    campo_texto: str | None = None,
) -> Response:
    """Un paso de quien gestiona: lo hace la función de la base y le avisa a quien la pidió."""
    gestionar = permisos.gestionar
    gestionar.verificar()
    enlace = f"{origen_publico(request)}{RUTA_REPARACIONES}"

    def efecto(conn: Connection) -> tuple[int, Reparacion]:
        avisar = ejecutar(
            conn, gestionar, sql, parametros | {"r": reparacion_id}, campo_texto
        ).mappings().all()  # fmt: skip
        _avisar(conn, gestionar, avisar, enlace)
        return 200, _una(conn, permisos, reparacion_id)

    return responder_una_vez(motor, gestionar.ctx, idempotencia, efecto)


@router.post(
    UNA + "/gestion",
    response_model=Reparacion,
    summary="Registrar el responsable, la prioridad y la fecha comprometida: la primera vez la"
    " reparación pasa a «En gestión»",
    responses=RESPUESTAS_ACTO,
)
def gestionar_reparacion(
    request: Request,
    reparacion_id: UUID,
    cuerpo: GestionReparacion,
    permisos: PermisosDep,
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    return _acto(
        request, reparacion_id, permisos, idempotencia, motor,
        "select * from nucleo.gestionar_reparacion(:r, :responsable, :prioridad, :fecha, :actor,"
        " 'api', :sesion)",
        {"responsable": cuerpo.responsable_id, "prioridad": cuerpo.prioridad.value,
         "fecha": cuerpo.fecha_comprometida},
    )  # fmt: skip


@router.post(
    UNA + "/presupuesto",
    response_model=Reparacion,
    summary="Registrar el presupuesto con el proveedor",
    responses=RESPUESTAS_ACTO,
)
def presupuestar_reparacion(
    request: Request,
    reparacion_id: UUID,
    cuerpo: PresupuestoReparacion,
    permisos: PermisosDep,
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    return _acto(
        request, reparacion_id, permisos, idempotencia, motor,
        "select * from nucleo.presupuestar_reparacion(:r, :adjunto, :proveedor, :cuit, :actor,"
        " 'api', :sesion)",
        {"adjunto": cuerpo.adjunto_id, "proveedor": cuerpo.proveedor_nombre,
         "cuit": cuerpo.proveedor_cuit},
        campo_texto="proveedor_nombre",
    )  # fmt: skip


@router.post(
    UNA + "/aprobacion",
    response_model=Reparacion,
    summary="Registrar la constancia de aprobación del presupuesto: quién aprobó y qué la respalda"
    " (no es una aprobación de pago)",
    responses=RESPUESTAS_ACTO,
)
def aprobar_reparacion(
    request: Request,
    reparacion_id: UUID,
    cuerpo: AprobacionReparacion,
    permisos: PermisosDep,
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    return _acto(
        request, reparacion_id, permisos, idempotencia, motor,
        "select * from nucleo.aprobar_reparacion(:r, :aprobado, :decision, :adjunto, :actor,"
        " 'api', :sesion)",
        {"aprobado": cuerpo.aprobado_por, "decision": cuerpo.decision_id,
         "adjunto": cuerpo.adjunto_id},
        campo_texto="aprobado_por",
    )  # fmt: skip


@router.post(
    UNA + "/terminacion",
    response_model=Reparacion,
    summary="Registrar que se hizo, con la factura: queda esperando el gasto de la liquidación",
    responses=RESPUESTAS_ACTO,
)
def terminar_reparacion(
    request: Request,
    reparacion_id: UUID,
    cuerpo: TerminacionReparacion,
    permisos: PermisosDep,
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    return _acto(
        request, reparacion_id, permisos, idempotencia, motor,
        "select * from nucleo.terminar_reparacion(:r, :adjunto, :actor, 'api', :sesion)",
        {"adjunto": cuerpo.adjunto_id},
    )  # fmt: skip


@router.post(
    UNA + "/gasto",
    response_model=Reparacion,
    summary="Enlazar el gasto de una liquidación oficial vigente: cierra la reparación (RF19)",
    responses={
        **RESPUESTAS_ACTO,
        409: {"description": "La reparación no espera el gasto (`reparacion_no_admite`) o ese"
              " gasto ya cierra otra (`gasto_ya_enlazado`)."},
    },
)  # fmt: skip
def enlazar_gasto(
    request: Request,
    reparacion_id: UUID,
    cuerpo: GastoReparacion,
    permisos: PermisosDep,
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    return _acto(
        request, reparacion_id, permisos, idempotencia, motor,
        "select * from nucleo.enlazar_gasto_reparacion(:r, :liquidacion, :clave, :actor, 'api',"
        " :sesion)",
        {"liquidacion": cuerpo.liquidacion_id, "clave": cuerpo.clave},
    )  # fmt: skip


@router.post(
    UNA + "/no-realizada",
    response_model=Reparacion,
    summary="Terminarla sin evidencia, con el motivo de la imposibilidad (RF20)",
    responses=RESPUESTAS_ACTO,
)
def no_realizada(
    request: Request,
    reparacion_id: UUID,
    cuerpo: MotivoReparacion,
    permisos: PermisosDep,
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    return _acto(
        request, reparacion_id, permisos, idempotencia, motor,
        "select * from nucleo.no_realizar_reparacion(:r, :motivo, :actor, 'api', :sesion)",
        {"motivo": cuerpo.motivo},
        campo_texto="motivo",
    )  # fmt: skip


@router.post(
    UNA + "/retiro",
    response_model=Reparacion,
    summary="Retirar una reparación propia mientras está pedida, con el motivo",
    responses=RESPUESTAS_ACTO,
)
def retirar_reparacion(
    reparacion_id: UUID,
    cuerpo: MotivoReparacion,
    permisos: PermisosDep,
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    with _lectura(permisos.ver, motor) as c:
        filas = _filas(c, permisos.ver, reparacion_id)
    if not filas:
        raise ErrorApi(404, CodigoError.NO_ENCONTRADO, NO_ENCONTRADO)
    if not filas[0]["propia"]:
        # Quien gestiona la ve, pero solo la retira quien la pidió.
        raise ErrorApi(403, CodigoError.SIN_PERMISO, SIN_PERMISO)
    pedir = permisos.pedir
    pedir.verificar()

    def efecto(conn: Connection) -> tuple[int, Reparacion]:
        ejecutar(
            conn, pedir,
            "select nucleo.retirar_reparacion(:r, :motivo, :actor, 'api', :sesion)",
            {"r": reparacion_id, "motivo": cuerpo.motivo},
            campo_texto="motivo",
        )  # fmt: skip
        return 200, _una(conn, permisos, reparacion_id)

    return responder_una_vez(motor, pedir.ctx, idempotencia, efecto)
