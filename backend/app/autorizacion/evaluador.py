"""Evaluador puro de permisos (tabla de verdad, §1).

`evaluar(accion, contexto, entidad) -> Decision`, sin base ni red. El orden es el de §1.2:

1. **Ámbito:** vínculo vigente o mandato vigente de **este** consorcio. Si no, 404, igual que
   si el consorcio no existiera (R2).
2. **Acción no declarada:** se niega (R1).
3. **Concesión:** por vínculo declarado, por ámbito declarado o por una facultad con el código
   exacto, de un mandato vigente de este consorcio, cuyo recurso alcanza (R7, R11).
4. **Filtros, todos:** modo (R6), separación (R8), respaldo (R9), destinatario único (A14) y
   económicas (R10). Cualquiera niega; **ninguno concede**.

**Separación entre seres humanos (A13-C, 2b-6a).** Una cuenta (`persona_id`) no prueba quién es
el ser humano: las reglas que exigen personas distintas comparan también la identidad humana
verificada en el consorcio (`ContextoAutorizacion.identidades`, migración 0026). Si a
cualquiera de las partes que hay que comparar le falta, se niega con `identidad_no_verificada`
(A5: si la identidad no se puede determinar, se niega). No se aplica a las acciones sin
separación ni cuando la otra parte no es una persona (el equipo, un mandatario sin cuenta, una
baja sin decisión). P4-c (`no_es_su_propio_vinculo`, `no_es_quien_registro`) sigue comparando
solo cuentas: la otra parte es cualquier propietario y el propietario decide aparte si se
verifica a todos.

**Económicas (R10, 2b-6a).** Ya no se niegan siempre: piden la sesión con el segundo factor
verificado hace poco (`segundo_factor_reciente`), la sesión de menos de 12 horas
(`sesion_reciente`) y la identidad humana verificada de quien actúa.

Los tipos de entrada son propios y puros. El PR 3 armará el `ContextoAutorizacion` desde la
base (`hoy_del_consorcio`, `nucleo.mandato_vigente`); acá no se toca `app.contexto`. Aunque el
contexto ya traiga solo lo vigente, el evaluador vuelve a comprobar la vigencia y el consorcio
de cada mandato: una facultad de otro consorcio o de un mandato vencido nunca cuenta.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import Literal
from uuid import UUID

from cem_contracts.estados import ModoOperacion, TipoVinculo
from cem_contracts.politicas import CodigoSeparacion, Politica, TipoRecurso

from app.autorizacion import recursos
from app.autorizacion.politicas import POLITICAS

CAUSA_ERROR_CARGA = "error_carga"


class Motivo(StrEnum):
    """Motivos internos: van a la auditoría, nunca en la respuesta (403 o 404 genéricos)."""

    CONCEDIDO_POR_VINCULO = "concedido_por_vinculo"
    CONCEDIDO_POR_AMBITO = "concedido_por_ambito"
    CONCEDIDO_POR_FACULTAD = "concedido_por_facultad"
    FUERA_DE_AMBITO = "fuera_de_ambito"
    ACCION_NO_DECLARADA = "accion_no_declarada"
    SIN_CONCESION = "sin_concesion"
    SIN_MODO_REGISTRADO = "sin_modo_registrado"
    MODO_NO_ADMITIDO = "modo_no_admitido"
    IDENTIDAD_INDETERMINADA = "identidad_indeterminada"
    SOLICITUD_YA_EMITIDA = "solicitud_ya_emitida"
    SIN_RESPALDO = "sin_respaldo"
    SIN_ADMINISTRADOR_VIGENTE = "sin_administrador_vigente"
    IDENTIDAD_NO_VERIFICADA = "identidad_no_verificada"
    REQUIERE_SEGUNDO_FACTOR_RECIENTE = "requiere_segundo_factor_reciente"
    REQUIERE_SESION_RECIENTE = "requiere_sesion_reciente"


HUMANAS = frozenset(CodigoSeparacion) - {
    CodigoSeparacion.NO_ES_SU_PROPIO_VINCULO,
    CodigoSeparacion.NO_ES_QUIEN_REGISTRO,
}
"""Las separaciones que comparan seres humanos (A13-C). P4-c queda afuera en este corte."""

SOLO_POR_CUENTA = frozenset({
    ("mandatos.baja", CodigoSeparacion.NO_ES_SU_PROPIO_MANDATO),
    ("mandatos.desasociar_persona", CodigoSeparacion.NO_ES_SU_PROPIO_MANDATO),
})  # fmt: skip
"""A4 al quitar, no al dar: dar de baja un mandato o desasociar su cuenta sigue comparando
cuentas. Con la regla fuerte, a una cuenta sin identidad verificada (una mal asociada, que no se
va a verificar nunca) no se la podría sacar por la API: la regla bloquearía el remedio. Frenado y
presentado al propietario (24-10-2026); A15 de la baja sí compara seres humanos."""


def separa_seres_humanos(accion: str, codigo: CodigoSeparacion) -> bool:
    return codigo in HUMANAS and (accion, codigo) not in SOLO_POR_CUENTA


@dataclass(frozen=True)
class Vinculo:
    """Vínculo vigente hoy de la persona en el consorcio del contexto (C5: con su unidad)."""

    tipo: TipoVinculo
    unidad_id: UUID
    confirmado: bool = False
    """Confirmado por quien tiene la facultad (P4, 2b-4)."""


@dataclass(frozen=True)
class Mandato:
    """Mandato de la persona. Vale con `[desde, hasta)` y nunca con causa `error_carga` (A9)."""

    id: UUID
    consorcio_id: UUID
    vigente_desde: date
    vigente_hasta: date | None = None
    causa_baja: str | None = None

    def vigente(self, fecha: date) -> bool:
        if self.causa_baja == CAUSA_ERROR_CARGA:
            return False
        return self.vigente_desde <= fecha and (
            self.vigente_hasta is None or fecha < self.vigente_hasta
        )


@dataclass(frozen=True)
class Facultad:
    codigo: str
    recurso: str | None
    """`tipo:id` (A8) o None: la facultad general, en todo el consorcio del mandato."""
    mandato_id: UUID


@dataclass(frozen=True)
class ContextoAutorizacion:
    persona_id: UUID | None
    """None solo si la identidad no se puede determinar (p. ej. soporte con actor nulo)."""
    consorcio_id: UUID
    hoy: date
    """Fecha del consorcio (`nucleo.hoy_del_consorcio`), nunca la de la base."""
    vinculos: tuple[Vinculo, ...] = ()
    modo: ModoOperacion | None = None
    mandatos: tuple[Mandato, ...] = ()
    facultades: tuple[Facultad, ...] = ()
    hay_administrador_vigente: bool = False
    """Si existe el único mandato vigente de administración del consorcio (A14)."""
    identidades: Mapping[UUID, UUID] = field(default_factory=dict)
    """`persona_id` → identidad humana verificada en este consorcio (A13-C), de quien actúa y
    de las personas de la entidad. Una persona que no está, no tiene identidad verificada."""
    segundo_factor_reciente: bool = False
    """La sesión verificó el segundo factor hace menos de 10 minutos (TB-CEM-3, R10)."""
    sesion_reciente: bool = False
    """La sesión empezó hace menos de 12 horas (R10: sesión de 12 h para lo económico)."""


@dataclass(frozen=True)
class Entidad:
    """Sobre qué se actúa. Cada filtro lee solo lo suyo; un dato que falta, niega."""

    recursos: Mapping[TipoRecurso, str] = field(default_factory=dict)
    """Recurso por tipo, en forma canónica: la unidad de la invitación, la cuenta y el grupo
    de la orden, etc."""
    preparado_por: UUID | None = None
    aprobado_por: tuple[UUID | None, ...] = ()
    firmas: tuple[UUID | None, ...] = ()
    """Firmas ya puestas en la solicitud; preparar no cuenta como firma (A5)."""
    solicitado_por: UUID | None = None
    propuesto_por: UUID | None = None
    persona_objetivo: UUID | None = None
    """La persona del mandato que se da de alta o de baja, o del vínculo que se confirma."""
    objetivo_sin_cuenta: bool = False
    """El mandatario no tiene cuenta (administrador externo sin `persona_id`, A11): no puede ser
    quien actúa, así que "no es su propio mandato" se cumple. Tiene que venir explícito: un
    `persona_objetivo` nulo sin este dato es un dato que falta y niega."""
    registrado_por: UUID | None = None
    """Quien registró el vínculo que se confirma (P4-c)."""
    registrado_por_soporte: bool = False
    """El vínculo lo registró el equipo (origen `soporte`): no hay persona que separar, así que
    "no es quien lo registró" se cumple. Tiene que venir explícito, como `objetivo_sin_cuenta`:
    un `registrado_por` nulo sin este dato es un dato que falta y niega."""
    emitido_por: UUID | None = None
    """Quien emitió la invitación de mandato que se aprueba (A7, cuatro ojos)."""
    decision_registrada_por: UUID | None = None
    """Quien registró la decisión con que se da de alta o se remueve (A15)."""
    decision_del_equipo: bool = False
    """La decisión la cargó el equipo (`soporte`, sin persona): no hay a quién separar."""
    sin_decision: bool = False
    """La baja no usa decisión (renuncia, fallecimiento, `error_carga`, otro). Las dos marcas
    tienen que venir explícitas: una `decision_registrada_por` nula sin ellas niega."""
    respaldo_presente: bool = False


@dataclass(frozen=True)
class Decision:
    permitido: bool
    estado: Literal[200, 403, 404]
    motivo_interno: str


def _permite(motivo: Motivo) -> Decision:
    return Decision(True, 200, motivo)


def _niega(motivo: str) -> Decision:
    return Decision(False, 403, motivo)


def evaluar(
    accion: str,
    contexto: ContextoAutorizacion,
    entidad: Entidad,
    politicas: Mapping[str, Politica] = POLITICAS,
) -> Decision:
    mandatos_validos = {
        m.id
        for m in contexto.mandatos
        if m.consorcio_id == contexto.consorcio_id and m.vigente(contexto.hoy)
    }
    # 1. Ámbito (R2, R11).
    if not contexto.vinculos and not mandatos_validos:
        return Decision(False, 404, Motivo.FUERA_DE_AMBITO)
    # 2. Se niega por defecto (R1).
    politica = politicas.get(accion)
    if politica is None:
        return _niega(Motivo.ACCION_NO_DECLARADA)
    # 3. Concesión.
    concesion = _concesion(politica, contexto, entidad, mandatos_validos)
    if not concesion.permitido:
        return concesion
    # 4. Filtros: cualquiera niega, ninguno concede.
    motivo = _filtrar(politica, contexto, entidad)
    if motivo is not None:
        return _niega(motivo)
    return concesion


def _concesion(
    politica: Politica,
    contexto: ContextoAutorizacion,
    entidad: Entidad,
    mandatos_validos: set[UUID],
) -> Decision:
    por_vinculo = politica.concede_por_vinculo
    if por_vinculo is not None:
        unidad = entidad.recursos.get(TipoRecurso.UNIDAD)
        for v in contexto.vinculos:
            if (
                v.tipo in por_vinculo.tipos
                and (not por_vinculo.misma_unidad or str(v.unidad_id) == unidad)
                and (not por_vinculo.solo_confirmados or v.confirmado)
            ):
                return _permite(Motivo.CONCEDIDO_POR_VINCULO)
    if politica.concede_por_ambito:
        return _permite(Motivo.CONCEDIDO_POR_AMBITO)
    if politica.facultad is None:
        return _niega(Motivo.SIN_CONCESION)
    # Cada facultad se evalúa sola (caso 8): una mal formada no anula a otra válida. Si
    # ninguna alcanza, queda el motivo de recurso de la última que tenía el código exacto.
    motivo: str = Motivo.SIN_CONCESION
    for f in contexto.facultades:
        if f.codigo != politica.facultad or f.mandato_id not in mandatos_validos:
            continue
        falla = recursos.verificar(f.recurso, politica.recurso, entidad.recursos)
        if falla is None:
            return _permite(Motivo.CONCEDIDO_POR_FACULTAD)
        motivo = falla
    return _niega(motivo)


def _filtrar(politica: Politica, contexto: ContextoAutorizacion, entidad: Entidad) -> str | None:
    # Modo (R6): sin modo registrado solo corren las acciones con `sin_modo`.
    if contexto.modo is None:
        if not politica.sin_modo:
            return Motivo.SIN_MODO_REGISTRADO
    elif contexto.modo not in politica.modos:
        return Motivo.MODO_NO_ADMITIDO
    # Separación de funciones (R8, A4, A5, A13-C): por persona y por ser humano; sin
    # identidad, se niega.
    for codigo in politica.separacion:
        motivo = _separacion(politica.accion, codigo, contexto, entidad)
        if motivo is not None:
            return motivo
    # Respaldo documental (R9). Su contenido lo valida la base (0011, 0012).
    if politica.respaldo is not None and not entidad.respaldo_presente:
        return Motivo.SIN_RESPALDO
    # Destinatario único (A14): la transición que emite exige el administrador vigente. En
    # una solicitud formal, emite la segunda firma, es decir, cuando ya hay una.
    if politica.destinatario_unico and entidad.firmas and not contexto.hay_administrador_vigente:
        return Motivo.SIN_ADMINISTRADOR_VIGENTE
    # Económicas (R10): segundo factor reciente, sesión de 12 h e identidad humana de quien
    # actúa (interviene en una separación económica, como quien actúa o como la otra parte).
    if politica.economica:
        if contexto.persona_id not in contexto.identidades:
            return Motivo.IDENTIDAD_NO_VERIFICADA
        if not contexto.segundo_factor_reciente:
            return Motivo.REQUIERE_SEGUNDO_FACTOR_RECIENTE
        if not contexto.sesion_reciente:
            return Motivo.REQUIERE_SESION_RECIENTE
    return None


def _separacion(
    accion: str, codigo: CodigoSeparacion, contexto: ContextoAutorizacion, entidad: Entidad
) -> str | None:
    actor = contexto.persona_id
    if actor is None:
        return Motivo.IDENTIDAD_INDETERMINADA
    match codigo:
        case CodigoSeparacion.NO_ES_QUIEN_PREPARO:
            otras: tuple[UUID | None, ...] = (entidad.preparado_por,)
        case CodigoSeparacion.NO_ES_QUIEN_SOLICITO:
            otras = (entidad.solicitado_por,)
        case CodigoSeparacion.NO_ES_QUIEN_PROPUSO:
            otras = (entidad.propuesto_por,)
        case CodigoSeparacion.NO_ES_SU_PROPIO_MANDATO:
            if entidad.persona_objetivo is None and entidad.objetivo_sin_cuenta:
                return None
            otras = (entidad.persona_objetivo,)
        case CodigoSeparacion.NO_ES_SU_PROPIO_VINCULO:
            otras = (entidad.persona_objetivo,)
        case CodigoSeparacion.NO_ES_QUIEN_REGISTRO:
            if entidad.registrado_por is None and entidad.registrado_por_soporte:
                return None
            otras = (entidad.registrado_por,)
        case CodigoSeparacion.NO_ES_QUIEN_EMITIO:
            otras = (entidad.emitido_por,)
        case CodigoSeparacion.NO_REGISTRO_LA_DECISION:
            if entidad.decision_registrada_por is None and (
                entidad.decision_del_equipo or entidad.sin_decision
            ):
                return None
            otras = (entidad.decision_registrada_por,)
        case CodigoSeparacion.NO_ES_QUIEN_APROBO:
            # Distinta de todos los aprobadores (A5).
            otras = entidad.aprobado_por
        case CodigoSeparacion.FIRMA_REPETIDA:
            # Dos firmas de personas distintas (PA36): con dos ya puestas, no hay otra.
            if len(entidad.firmas) >= 2:
                return Motivo.SOLICITUD_YA_EMITIDA
            otras = entidad.firmas
        case _:
            raise ValueError(f"código de separación sin implementar: {codigo!r}")
    if any(p is None for p in otras):
        return Motivo.IDENTIDAD_INDETERMINADA
    if actor in otras:
        return codigo
    if not separa_seres_humanos(accion, codigo):
        return None
    # A13-C: los mismos lugares, entre seres humanos verificados en el consorcio. La primera firma
    # también la pide: la segunda se compara con ella.
    identidades = contexto.identidades
    if actor not in identidades or any(p not in identidades for p in otras):
        return Motivo.IDENTIDAD_NO_VERIFICADA
    if identidades[actor] in {identidades[p] for p in otras}:
        return codigo
    return None
