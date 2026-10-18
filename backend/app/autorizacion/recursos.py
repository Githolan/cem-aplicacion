"""Recurso de una facultad (A8; tabla de verdad, §1.3).

`facultad_otorgada.recurso` no es texto libre: es `tipo:id`, con un solo `:`. La evaluación
sigue el orden de §1.3:

1. Sin recurso: la facultad general alcanza a todo el consorcio del mandato, solo si la acción
   la admite (una acción sin recurso declarado solo acepta la general).
2. Con recurso: se parsea; lo que no se puede parsear (más de un `:`, tipo desconocido o no
   canónico) no concede (`recurso_invalido`).
3. El tipo tiene que ser uno de los que acepta la acción (`recurso_tipo_inesperado`).
4. El id se valida según su tipo: UUID canónico en minúsculas o código de grupo
   (`recurso_invalido`).
5. Se compara por **igualdad exacta** con el recurso de ese tipo de la entidad
   (`recurso_no_alcanza`). Nunca por prefijo ni por subcadena.

Los motivos son internos (auditoría); hacia afuera, 403 genérico.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from cem_contracts.politicas import RecursoAceptado, TipoRecurso

UUID_CANONICO = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
CODIGO_GRUPO = re.compile(r"[a-z][a-z0-9_]{0,62}")
"""Se usan siempre con `fullmatch`: `$` aceptaría un salto de línea final."""


class MotivoRecurso(StrEnum):
    INVALIDO = "recurso_invalido"
    TIPO_INESPERADO = "recurso_tipo_inesperado"
    NO_ALCANZA = "recurso_no_alcanza"


@dataclass(frozen=True)
class RecursoParseado:
    tipo: TipoRecurso
    id: str


def parsear(valor: str) -> RecursoParseado | None:
    """`tipo:id` con un tipo conocido (en minúsculas, exacto) y un solo `:`; si no, None."""
    if valor.count(":") != 1:
        return None
    tipo, id_ = valor.split(":")
    if tipo not in {t.value for t in TipoRecurso}:
        return None
    return RecursoParseado(TipoRecurso(tipo), id_)


def id_valido(tipo: TipoRecurso, id_: str) -> bool:
    patron = CODIGO_GRUPO if tipo is TipoRecurso.GRUPO else UUID_CANONICO
    return patron.fullmatch(id_) is not None


def verificar(
    recurso: str | None,
    aceptado: RecursoAceptado | None,
    recursos_entidad: Mapping[TipoRecurso, str],
) -> MotivoRecurso | None:
    """None si el recurso de la facultad alcanza a la entidad; si no, el motivo."""
    if recurso is None:
        if aceptado is None or aceptado.general:
            return None
        return MotivoRecurso.TIPO_INESPERADO
    parseado = parsear(recurso)
    if parseado is None:
        return MotivoRecurso.INVALIDO
    if aceptado is None or parseado.tipo not in aceptado.tipos:
        return MotivoRecurso.TIPO_INESPERADO
    if not id_valido(parseado.tipo, parseado.id):
        return MotivoRecurso.INVALIDO
    if recursos_entidad.get(parseado.tipo) != parseado.id:
        return MotivoRecurso.NO_ALCANZA
    return None
