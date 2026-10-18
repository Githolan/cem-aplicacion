"""Evaluador puro de permisos (tabla de verdad, §1).

`evaluar(accion, contexto, entidad) -> Decision`, sin base ni red. El orden es el de §1.2:

1. **Ámbito:** vínculo vigente o mandato vigente de **este** consorcio. Si no, 404, igual que
   si el consorcio no existiera (R2).
2. **Acción no declarada:** se niega (R1).
3. **Concesión:** por vínculo declarado, por ámbito declarado o por una facultad con el código
   exacto, de un mandato vigente de este consorcio, cuyo recurso alcanza (R7, R11).
4. **Filtros, todos:** modo (R6), separación por `persona_id` (R8), respaldo (R9),
   destinatario único (A14) y económicas (R10). Cualquiera niega; **ninguno concede**.

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
    REQUIERE_CONTROLES_2B6 = "requiere_controles_2b6"


@dataclass(frozen=True)
class Vinculo:
    """Vínculo vigente hoy de la persona en el consorcio del contexto (C5: con su unidad)."""

    tipo: TipoVinculo
    unidad_id: UUID


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
            if v.tipo in por_vinculo.tipos and (
                not por_vinculo.misma_unidad or str(v.unidad_id) == unidad
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
    # Separación de funciones (R8, A4, A5): por persona; sin identidad, se niega.
    for codigo in politica.separacion:
        motivo = _separacion(codigo, contexto.persona_id, entidad)
        if motivo is not None:
            return motivo
    # Respaldo documental (R9). Su contenido lo valida la base (0011, 0012).
    if politica.respaldo is not None and not entidad.respaldo_presente:
        return Motivo.SIN_RESPALDO
    # Destinatario único (A14): la transición que emite exige el administrador vigente. En
    # una solicitud formal, emite la segunda firma, es decir, cuando ya hay una.
    if politica.destinatario_unico and entidad.firmas and not contexto.hay_administrador_vigente:
        return Motivo.SIN_ADMINISTRADOR_VIGENTE
    # Económicas (R10): se niegan siempre hasta 2b-6.
    if politica.economica:
        return Motivo.REQUIERE_CONTROLES_2B6
    return None


def _separacion(codigo: CodigoSeparacion, actor: UUID | None, entidad: Entidad) -> str | None:
    if actor is None:
        return Motivo.IDENTIDAD_INDETERMINADA
    match codigo:
        case CodigoSeparacion.NO_ES_QUIEN_PREPARO:
            otras: tuple[UUID | None, ...] = (entidad.preparado_por,)
        case CodigoSeparacion.NO_ES_QUIEN_SOLICITO:
            otras = (entidad.solicitado_por,)
        case CodigoSeparacion.NO_ES_QUIEN_PROPUSO:
            otras = (entidad.propuesto_por,)
        case CodigoSeparacion.NO_ES_SU_PROPIO_MANDATO | CodigoSeparacion.NO_ES_SU_PROPIO_VINCULO:
            otras = (entidad.persona_objetivo,)
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
    return None
