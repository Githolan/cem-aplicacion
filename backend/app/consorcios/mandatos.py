"""Operación de mandatos y decisiones por la aplicación (H2b 2b-4b, PR 2).

Diseño: `doc/diseno/H2b_4b_Diseno.md` de CEM, §2 y §5 (D3: "desde ahí, todo cambio se hace desde
la aplicación"; A15, decisión P2 del propietario).

**Quién.** Las tres acciones piden una facultad (`decisiones.registrar` o `mandatos.registrar`,
MC o AD por C2), en todos los modos y sin modo. La política decide antes de cualquier efecto:
- nadie se da un mandato, se da de baja ni revisa su propio mandato (A4);
- quien registró una decisión no la usa para dar de alta, remover ni revisar (A15);
- el respaldo es obligatorio (criterio 9, C6).

Las funciones de la base (`0014`, `0016`) vuelven a garantizarlo. Un motivo de separación que
llega a la base es una discrepancia y responde 500.

**Qué devuelven.** Identificadores y fechas: ningún nombre, CUIT ni referencia.

**Lectura (2b-4c, `doc/diseno/H2b_4c_Diseno.md`).** `mandatos.ver` y `decisiones.ver` (MC y AD).
La política decide antes de leer; las funciones de `0018` vuelven a exigir la facultad como
segunda barrera. Si la base niega lo que el evaluador concedió, es una discrepancia (500), nunca
una lista vacía. Un detalle inexistente responde 404 solo a quien tiene la facultad: quien no la
tiene recibe el mismo 403 auditado exista o no (sin oráculo).

**Destinatario (decisión 3).** Por la API, el Consejo y el administrador se dan solo a personas
con un vínculo confirmado, vigente o futuro, en el consorcio, y el administrador externo entra
sin cuenta (la asocia A7). Lo garantiza `alta_mandato` (`0018`), después de la política: un
pedido sin permiso nunca llega a validar a la persona.
"""

import logging
from typing import Annotated
from uuid import UUID

from cem_contracts.errores import CodigoError
from cem_contracts.mandatos import (
    BajaMandato,
    CandidatoMandato,
    DecisionDetalle,
    DecisionNueva,
    DecisionRegistrada,
    DecisionResumen,
    ListaCandidatos,
    ListaDecisiones,
    ListaMandatos,
    MandatoDadoDeBaja,
    MandatoDetalle,
    MandatoNuevo,
    MandatoRegistrado,
    MandatoResumen,
    MandatoRevisado,
    RevisionMandato,
)
from fastapi import APIRouter, Depends, Response
from psycopg.errors import InsufficientPrivilege
from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError

from app.autorizacion.evaluador import Entidad
from app.autorizacion.requiere import NO_ENCONTRADO, Permiso, requiere
from app.contexto import MotorDep
from app.db import transaccion
from app.errores import ErrorApi, sin_cache
from app.plataforma.idempotencia import IdempotenciaDep, responder_una_vez

router = APIRouter(prefix="/api/v1", tags=["mandatos"], dependencies=[Depends(sin_cache)])
log = logging.getLogger("cem.mandatos")

ORIGEN_SOPORTE = "soporte"
SIN_OBJETO = Entidad(objetivo_sin_cuenta=True, sin_decision=True, respaldo_presente=True)
"""Una baja sin mandato que exista: los filtros de separación no tienen persona que comparar,
así que decide solo la concesión (facultad, modo y ámbito)."""
DATOS = (422, CodigoError.PETICION_INVALIDA)
ERRORES = {
    # 404: lo que se nombra en la ruta.
    "mandato_invalido": (404, CodigoError.NO_ENCONTRADO, NO_ENCONTRADO),
    # 409: el estado del consorcio o del mandato.
    "administracion_ocupada": (
        409, CodigoError.ADMINISTRACION_OCUPADA,
        "Ya hay un mandato de administración en esas fechas en este consorcio.",
    ),
    "mandato_superpuesto": (
        409, CodigoError.MANDATO_SUPERPUESTO,
        "La persona ya tiene un mandato de ese tipo en esas fechas.",
    ),
    "consejo_incompatible_con_administracion": (
        409, CodigoError.CONSEJO_INCOMPATIBLE_CON_ADMINISTRACION,
        "La persona administra este consorcio como externa en esas fechas: el Consejo"
        " fiscaliza y no co-administra.",
    ),
    "mandato_ya_dado_de_baja": (
        409, CodigoError.MANDATO_YA_DADO_DE_BAJA, "El mandato ya tiene una baja registrada.",
    ),
    "mandato_terminado": (
        409, CodigoError.MANDATO_TERMINADO,
        "El mandato ya terminó por su fecha: solo admite una baja por error de carga.",
    ),
    "mandato_no_pendiente": (
        409, CodigoError.MANDATO_NO_PENDIENTE,
        "La revisión solo vale para un mandato del Consejo pendiente de revisión.",
    ),
    "revision_duplicada": (
        409, CodigoError.REVISION_DUPLICADA, "El mandato ya tiene una revisión desde ese día.",
    ),
    # 422: lo que se envió.
    "decision_invalida": (
        422, CodigoError.DECISION_INVALIDA,
        "La decisión no existe en este consorcio o no corresponde (la remoción y la revisión"
        " piden una asamblea).",
    ),
    "eficaz_desde_invalida": (
        422, CodigoError.EFICAZ_DESDE_INVALIDA,
        "La revisión rige desde hoy o después, no antes de su decisión, y dentro del mandato.",
    ),
    "sin_condicion_habilitante": (
        422, CodigoError.SIN_CONDICION_HABILITANTE,
        "Ese día la persona no cumple ninguna condición para integrar el Consejo.",
    ),
    "persona_sin_vinculo": (
        422, CodigoError.PERSONA_SIN_VINCULO,
        "La persona no tiene un vínculo confirmado, vigente o futuro, en este consorcio.",
    ),
    "externo_por_invitacion": (
        422, CodigoError.EXTERNO_POR_INVITACION,
        "El administrador externo se da de alta sin cuenta: la cuenta se asocia por invitación.",
    ),
    "mandato_sin_cuenta": (
        422, CodigoError.MANDATO_SIN_CUENTA,
        "Un miembro del Consejo o un administrador necesita su cuenta.",
    ),
    "cuit_requerido": (
        422, CodigoError.CUIT_INVALIDO, "Un administrador externo necesita su CUIT.",
    ),
    "cuit_invalido": (422, CodigoError.CUIT_INVALIDO, "El CUIT no es válido."),
    "sin_facultades": (
        422, CodigoError.FACULTAD_FUERA_DE_PAQUETE, "El mandato necesita al menos una facultad.",
    ),
    "facultad_fuera_de_paquete": (
        422, CodigoError.FACULTAD_FUERA_DE_PAQUETE,
        "Alguna facultad no está en el paquete de ese tipo de mandato.",
    ),
    "respaldo_invalido": (
        422, CodigoError.RESPALDO_INVALIDO,
        "La remoción se respalda con una asamblea; cualquier otra causa, con el instrumento.",
    ),
    "nombre_invalido": (*DATOS, "Falta el nombre del mandatario o es demasiado largo."),
    "matricula_invalida": (*DATOS, "La matrícula está vacía o es demasiado larga."),
    "vigencia_invalida": (
        *DATOS, "La vigencia necesita un desde, y el hasta no puede ser anterior.",
    ),
    "descripcion_invalida": (*DATOS, "La descripción está vacía o es demasiado larga."),
    "referencia_invalida": (*DATOS, "La referencia del acta está vacía o es demasiado larga."),
    "sha256_invalido": (*DATOS, "La huella del acta no es un SHA-256."),
    "decision_incompleta": (*DATOS, "La decisión necesita tipo y fecha."),
    "fecha_futura": (
        422, CodigoError.FECHA_FUTURA,
        "La fecha de la decisión no puede ser posterior a hoy en el consorcio.",
    ),
}  # fmt: skip
"""Motivos de las funciones de `0014`, `0016`, `0018`, `0019` y `0023`. Los de separación
(`alta_de_si_mismo`, `baja_de_si_mismo`, `revision_de_si_mismo`,
`decision_registrada_por_el_actor`) y los de actor u
origen los niega antes el evaluador o los fija esta ruta: si llegan a la base, es una
discrepancia (500)."""


def _llamar(conn: Connection, sql: str, parametros: dict):
    """Ejecuta una función de la base y traduce sus motivos a errores de la API."""
    try:
        return conn.execute(text(sql), parametros)
    except DBAPIError as e:
        motivo = e.orig.diag.message_hint
        if motivo in ERRORES:
            estado, codigo, mensaje = ERRORES[motivo]
            raise ErrorApi(estado, codigo, mensaje) from e
        raise


def _sobre_decision(conn: Connection, consorcio_id: UUID, decision_id: UUID | None) -> dict:
    """Los datos de A15 de la decisión que se usa.

    Sin decisión, o con una que no existe en este consorcio, no hay quién la registró:
    `sin_decision`. La base rechaza después lo que corresponda (`decision_invalida` o
    `respaldo_invalido`), siempre después de que la política dejó pasar a quien pide.
    """
    fila = None
    if decision_id is not None:
        fila = conn.execute(
            text(
                "select registrada_por, origen from nucleo.decision_formal"
                " where id = :d and consorcio_id = :c"
            ),
            {"d": decision_id, "c": consorcio_id},
        ).one_or_none()
    if fila is None:
        return {"sin_decision": True}
    return {
        "decision_registrada_por": fila.registrada_por,
        "decision_del_equipo": fila.registrada_por is None and fila.origen == ORIGEN_SOPORTE,
    }


@router.post(
    "/consorcios/{consorcio_id}/decisiones",
    status_code=201,
    response_model=DecisionRegistrada,
    summary="Registrar una decisión formal ya tomada, con la referencia del acta",
    responses={
        403: {"description": "Sin `decisiones.registrar` en el consorcio."},
        422: {
            "description": "Faltan datos, la referencia no es válida o la fecha es posterior a"
            " hoy en el consorcio (`fecha_futura`)."
        },
    },
)
def registrar_decision(
    nueva: DecisionNueva,
    permiso: Annotated[Permiso, Depends(requiere("decisiones.registrar"))],
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    """Carga un acto ya tomado: no decide nada (el circuito de asambleas es H4b). La referencia
    queda en la fila, no en la auditoría (E31)."""
    ctx = permiso.ctx
    permiso.verificar(Entidad(respaldo_presente=True))  # el contrato exige la referencia
    # Un acto ya tomado (2b-4b §7.4): hasta hoy en el consorcio. La base lo repite (`0019`).
    if nueva.fecha > ctx.hoy:
        raise ErrorApi(*ERRORES["fecha_futura"])

    def efecto(conn: Connection) -> tuple[int, DecisionRegistrada]:
        nueva_id = _llamar(
            conn,
            "select nucleo.registrar_decision(:t, :f, :d, :r, :a, 'api', :s)",
            {"t": nueva.tipo.value, "f": nueva.fecha, "d": nueva.descripcion,
             "r": nueva.referencia, "a": ctx.persona_id, "s": nueva.sha256},
        ).scalar_one()  # fmt: skip
        return 201, DecisionRegistrada(id=nueva_id)

    return responder_una_vez(motor, ctx, idempotencia, efecto)


@router.post(
    "/consorcios/{consorcio_id}/mandatos",
    status_code=201,
    response_model=MandatoRegistrado,
    summary="Dar de alta un mandato con una decisión de respaldo",
    responses={
        403: {
            "description": "Sin `mandatos.registrar`, a sí mismo (A4), o con una decisión que"
            " registró la misma persona (A15)."
        },
        409: {"description": "Administración ocupada, mandato superpuesto o Consejo con AE."},
        422: {"description": "La decisión, la cuenta, el CUIT o las facultades no sirven."},
    },
)
def alta_mandato(
    nuevo: MandatoNuevo,
    permiso: Annotated[Permiso, Depends(requiere("mandatos.alta"))],
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    ctx = permiso.ctx
    with transaccion(motor, persona_id=ctx.persona_id, consorcio_id=ctx.consorcio_id) as conn:
        decision = _sobre_decision(conn, ctx.consorcio_id, nuevo.decision_id)
    permiso.verificar(
        Entidad(
            persona_objetivo=nuevo.persona_id,
            objetivo_sin_cuenta=nuevo.persona_id is None,
            respaldo_presente=True,  # el contrato exige la decisión
            **decision,
        )
    )

    def efecto(conn: Connection) -> tuple[int, MandatoRegistrado]:
        nuevo_id = _llamar(
            conn,
            "select nucleo.alta_mandato(:t, :p, :n, :cuit, :mat, :d, :desde, :hasta, :f, :a,"
            " 'api')",
            {"t": nuevo.tipo.value, "p": nuevo.persona_id, "n": nuevo.nombre, "cuit": nuevo.cuit,
             "mat": nuevo.matricula, "d": nuevo.decision_id, "desde": nuevo.desde,
             "hasta": nuevo.hasta, "f": nuevo.facultades, "a": ctx.persona_id},
        ).scalar_one()  # fmt: skip
        return 201, MandatoRegistrado(id=nuevo_id)

    return responder_una_vez(motor, ctx, idempotencia, efecto)


@router.post(
    "/consorcios/{consorcio_id}/mandatos/{mandato_id}/baja",
    response_model=MandatoDadoDeBaja,
    summary="Dar de baja un mandato (irreversible); revoca las sesiones del mandatario",
    responses={
        403: {
            "description": "Sin `mandatos.registrar`, el propio (A4), o una remoción con una"
            " asamblea que registró la misma persona (A15)."
        },
        404: {"description": "El mandato no existe en este consorcio."},
        409: {"description": "Ya tiene una baja, o ya terminó."},
        422: {"description": "El respaldo no corresponde a la causa (C6)."},
    },
)
def baja_mandato(
    mandato_id: UUID,
    baja: BajaMandato,
    permiso: Annotated[Permiso, Depends(requiere("mandatos.baja"))],
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    """Revoca las sesiones del mandatario (RN22); cuántas, queda en la auditoría."""
    ctx = permiso.ctx
    with transaccion(motor, persona_id=ctx.persona_id, consorcio_id=ctx.consorcio_id) as conn:
        mandato = conn.execute(
            text("select persona_id from nucleo.mandato where id = :m and consorcio_id = :c"),
            {"m": mandato_id, "c": ctx.consorcio_id},
        ).one_or_none()
        if mandato is None:
            # Sin mandato no hay a quién separar: se evalúa solo la concesión, para que quien no
            # puede dar de baja reciba el mismo 403 auditado que con un mandato que existe y no
            # distinga un id válido de uno inexistente (Codex).
            permiso.verificar(SIN_OBJETO)
            raise ErrorApi(404, CodigoError.NO_ENCONTRADO, NO_ENCONTRADO)
        decision = _sobre_decision(conn, ctx.consorcio_id, baja.decision_id)
    permiso.verificar(
        Entidad(
            persona_objetivo=mandato.persona_id,
            objetivo_sin_cuenta=mandato.persona_id is None,
            respaldo_presente=True,  # el contrato exige el respaldo según la causa
            **decision,
        )
    )

    def efecto(conn: Connection) -> tuple[int, MandatoDadoDeBaja]:
        _llamar(
            conn,
            "select nucleo.baja_mandato(:m, :causa, :d, :i, :desc, :a, 'api')",
            {"m": mandato_id, "causa": baja.causa.value, "d": baja.decision_id,
             "i": baja.instrumento, "desc": baja.descripcion, "a": ctx.persona_id},
        )  # fmt: skip
        hasta = conn.execute(
            text("select vigente_hasta from nucleo.mandato where id = :m"), {"m": mandato_id}
        ).scalar_one()
        return 200, MandatoDadoDeBaja(id=mandato_id, causa=baja.causa, vigente_hasta=hasta)

    return responder_una_vez(motor, ctx, idempotencia, efecto)


@router.post(
    "/consorcios/{consorcio_id}/mandatos/{mandato_id}/revision",
    status_code=201,
    response_model=MandatoRevisado,
    summary="Revisar un mandato del Consejo pendiente de revisión: vuelve a operar desde una fecha",
    responses={
        403: {
            "description": "Sin `mandatos.registrar`, el propio (A4), o con una asamblea que"
            " registró la misma persona (A15)."
        },
        404: {"description": "El mandato no existe en este consorcio."},
        409: {
            "description": "No está pendiente de revisión (opera, no es del Consejo, tiene baja o"
            " terminó), o ya tiene una revisión desde ese día."
        },
        422: {
            "description": "La decisión no es una asamblea del consorcio con fecha hasta hoy, la"
            " fecha no sirve, o ese día la persona no cumple ninguna condición habilitante."
        },
    },
)
def revisar_mandato(
    mandato_id: UUID,
    revision: RevisionMandato,
    permiso: Annotated[Permiso, Depends(requiere("mandatos.resolver_revision"))],
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    """E62, alternativa 1 (`doc/diseno/Decisiones_Propietario_2026-10-21.md` de CEM, §2.3): un
    acto sobre el mismo mandato, hacia adelante. No toca la fila del mandato ni cubre el período
    sin efecto; lo valida `resolver_revision_mandato` (`0023`)."""
    ctx = permiso.ctx
    with transaccion(motor, persona_id=ctx.persona_id, consorcio_id=ctx.consorcio_id) as conn:
        mandato = conn.execute(
            text("select persona_id from nucleo.mandato where id = :m and consorcio_id = :c"),
            {"m": mandato_id, "c": ctx.consorcio_id},
        ).one_or_none()
        if mandato is None:
            # Como en la baja: el mismo 403 auditado para quien no puede, exista o no.
            permiso.verificar(SIN_OBJETO)
            raise ErrorApi(404, CodigoError.NO_ENCONTRADO, NO_ENCONTRADO)
        decision = _sobre_decision(conn, ctx.consorcio_id, revision.decision_id)
    permiso.verificar(
        Entidad(
            persona_objetivo=mandato.persona_id,
            objetivo_sin_cuenta=mandato.persona_id is None,
            respaldo_presente=True,  # el contrato exige la decisión
            **decision,
        )
    )

    def efecto(conn: Connection) -> tuple[int, MandatoRevisado]:
        nueva_id = _llamar(
            conn,
            "select nucleo.resolver_revision_mandato(:m, :d, :e, :a, 'api')",
            {"m": mandato_id, "d": revision.decision_id, "e": revision.eficaz_desde,
             "a": ctx.persona_id},
        ).scalar_one()  # fmt: skip
        return 201, MandatoRevisado(
            id=nueva_id, mandato_id=mandato_id, eficaz_desde=revision.eficaz_desde
        )

    return responder_una_vez(motor, ctx, idempotencia, efecto)


class Discrepancia(RuntimeError):
    """La base niega lo que el evaluador concedió: un defecto, no un caso de uso."""


def _leer(permiso: Permiso, motor, sql: str, parametros: dict) -> list:
    """Lee con una función de `0018` después de que la política concedió."""
    ctx = permiso.ctx
    permiso.verificar(Entidad())
    try:
        with transaccion(motor, persona_id=ctx.persona_id, consorcio_id=ctx.consorcio_id) as c:
            return list(c.execute(text(sql), parametros).mappings().all())
    except DBAPIError as e:
        if isinstance(e.orig, InsufficientPrivilege):
            raise Discrepancia(f"la base niega {permiso.accion} y el evaluador lo concede") from e
        raise


COLUMNAS_RESUMEN = (
    "id, tipo::text as tipo, nombre, cuit, vigente_desde, vigente_hasta, estado, con_cuenta"
)


@router.get(
    "/consorcios/{consorcio_id}/mandatos",
    response_model=ListaMandatos,
    summary="Mandatos del consorcio, con lo mínimo",
    responses={403: {"description": "Sin `mandatos.ver` en el consorcio."}},
)
def listar_mandatos(
    permiso: Annotated[Permiso, Depends(requiere("mandatos.ver"))], motor: MotorDep
) -> ListaMandatos:
    """Sin referencias, instrumentos ni facultades (van en el detalle); el CUIT, solo del
    administrador externo."""
    filas = _leer(
        permiso, motor, f"select {COLUMNAS_RESUMEN} from nucleo.mandatos_del_consorcio()", {}
    )
    return ListaMandatos(elementos=[MandatoResumen(**f) for f in filas])


@router.get(
    "/consorcios/{consorcio_id}/mandatos/candidatos",
    response_model=ListaCandidatos,
    summary="Personas a las que se les puede dar un mandato de Consejo o de administración",
    responses={403: {"description": "Sin `mandatos.registrar` en el consorcio."}},
)
def candidatos(
    permiso: Annotated[Permiso, Depends(requiere("mandatos.ver_candidatos"))], motor: MotorDep
) -> ListaCandidatos:
    """Las personas con un vínculo confirmado, vigente o futuro, en el consorcio (decisión 3).
    Sin correo ni teléfono."""
    filas = _leer(
        permiso, motor, "select persona_id, nombre, unidades from nucleo.candidatos_a_mandato()", {}
    )
    return ListaCandidatos(elementos=[CandidatoMandato(**f) for f in filas])


@router.get(
    "/consorcios/{consorcio_id}/mandatos/{mandato_id}",
    response_model=MandatoDetalle,
    summary="Detalle de un mandato",
    responses={
        403: {"description": "Sin `mandatos.ver` en el consorcio (exista o no el mandato)."},
        404: {"description": "El mandato no existe en este consorcio."},
    },
)
def detalle_mandato(
    mandato_id: UUID,
    permiso: Annotated[Permiso, Depends(requiere("mandatos.ver"))],
    motor: MotorDep,
) -> MandatoDetalle:
    filas = _leer(
        permiso,
        motor,
        f"select {COLUMNAS_RESUMEN}, matricula, decision_id, facultades, causa_baja,"
        " baja_decision_id, instrumento_referencia, motivo_fin, finalizado_en"
        " from nucleo.mandato_detalle(:m)",
        {"m": mandato_id},
    )
    if not filas:
        raise ErrorApi(404, CodigoError.NO_ENCONTRADO, NO_ENCONTRADO)
    return MandatoDetalle(**filas[0])


@router.get(
    "/consorcios/{consorcio_id}/decisiones",
    response_model=ListaDecisiones,
    summary="Decisiones formales del consorcio, sin la referencia del acta",
    responses={403: {"description": "Sin `decisiones.ver` en el consorcio."}},
)
def listar_decisiones(
    permiso: Annotated[Permiso, Depends(requiere("decisiones.ver"))], motor: MotorDep
) -> ListaDecisiones:
    filas = _leer(
        permiso,
        motor,
        "select id, tipo::text as tipo, fecha, descripcion from nucleo.decisiones_del_consorcio()",
        {},
    )
    return ListaDecisiones(elementos=[DecisionResumen(**f) for f in filas])


@router.get(
    "/consorcios/{consorcio_id}/decisiones/{decision_id}",
    response_model=DecisionDetalle,
    summary="Detalle de una decisión formal, con la referencia del acta",
    responses={
        403: {"description": "Sin `decisiones.ver` en el consorcio (exista o no la decisión)."},
        404: {"description": "La decisión no existe en este consorcio."},
    },
)
def detalle_decision(
    decision_id: UUID,
    permiso: Annotated[Permiso, Depends(requiere("decisiones.ver"))],
    motor: MotorDep,
) -> DecisionDetalle:
    """Quién la registró no sale: solo si fue quien lee (A15) o el equipo."""
    filas = _leer(
        permiso,
        motor,
        "select id, tipo::text as tipo, fecha, descripcion, referencia_acta, sha256_acta, origen,"
        " registrada_por from nucleo.decision_detalle(:d)",
        {"d": decision_id},
    )
    if not filas:
        raise ErrorApi(404, CodigoError.NO_ENCONTRADO, NO_ENCONTRADO)
    f = filas[0]
    return DecisionDetalle(
        id=f["id"],
        tipo=f["tipo"],
        fecha=f["fecha"],
        descripcion=f["descripcion"],
        referencia=f["referencia_acta"],
        sha256=f["sha256_acta"],
        registrada_por_vos=f["registrada_por"] == permiso.ctx.persona_id,
        del_equipo=f["registrada_por"] is None and f["origen"] == ORIGEN_SOPORTE,
    )
