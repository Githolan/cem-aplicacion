"""Políticas de autorización por acción (H2b 2b-3; tabla de verdad, §1.2, §1.3 y §6).

Cada acción declara cómo se concede y qué filtros la pueden negar. El modelo es estricto
(`extra="forbid"`): un campo mal escrito no se ignora, invalida el archivo. Los booleanos son
`StrictBool`: `"yes"` o `1` no se convierten en `true`, invalidan el archivo (deciden
concesiones). Las políticas viven en `politicas_datos/<módulo>.json`, dentro de este paquete,
para que el backend las encuentre instalado; el frontend lee los mismos archivos.

- **Concesión:** por vínculo (`concede_por_vinculo`), por ámbito (`concede_por_ambito`, por
  defecto `false`) o por una facultad exacta (`facultad`) cuyo recurso alcance (`recurso`).
- **Filtros, que solo niegan:** `modos` y `sin_modo` (R6), `separacion` (R8), `respaldo` (R9),
  `destinatario_unico` (A14) y `economica` (R10: se niega hasta 2b-6).
- **`fuente_normativa`** (D8): obligatoria y no vacía. No concede ni niega; la revisa el asesor
  antes del piloto (G5).

`esquema.json` es la instantánea aprobada del esquema; la prueba falla si el modelo y la
instantánea difieren. Para aprobar un cambio: `python -m cem_contracts.politicas` desde
`contratos/`, y revisar la diferencia en el PR.
"""

import json
from enum import StrEnum
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StringConstraints, model_validator

from cem_contracts.estados import ModoOperacion, TipoVinculo

DIRECTORIO = Path(__file__).resolve().parent / "politicas_datos"
ESQUEMA = DIRECTORIO / "esquema.json"

CodigoAccion = Annotated[str, StringConstraints(pattern=r"^[a-z_]+\.[a-z_]+$")]
"""Mismo formato que `nucleo.facultad.codigo` (0011): `modulo.verbo`."""

TextoNoVacio = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class TipoRecurso(StrEnum):
    """Tipos de recurso de una facultad (A8, §1.3). `facultad_otorgada.recurso` es `tipo:id`."""

    UNIDAD = "unidad"
    CUENTA = "cuenta"
    GRUPO = "grupo"


class CodigoSeparacion(StrEnum):
    """Incompatibilidades de la separación de funciones (R8, A4 y A5).

    Se comparan siempre por `persona_id`; si una identidad no se puede determinar, se niega.
    El código es también el motivo interno de la negación.
    """

    NO_ES_QUIEN_PREPARO = "no_es_quien_preparo"
    NO_ES_QUIEN_APROBO = "no_es_quien_aprobo"
    """Quien ejecuta es una persona distinta de todos los aprobadores (A5)."""
    NO_ES_QUIEN_SOLICITO = "no_es_quien_solicito"
    NO_ES_QUIEN_PROPUSO = "no_es_quien_propuso"
    FIRMA_REPETIDA = "firma_repetida"
    """Dos firmas de personas distintas (PA36, A5); preparar no cuenta como firma."""
    NO_ES_SU_PROPIO_MANDATO = "no_es_su_propio_mandato"
    """Nadie se da un mandato ni se da de baja a sí mismo (A4)."""
    NO_ES_SU_PROPIO_VINCULO = "no_es_su_propio_vinculo"
    NO_ES_QUIEN_REGISTRO = "no_es_quien_registro"
    """Quien registró el vínculo no lo confirma (P4-c, 2b-4). Un vínculo que registró el equipo
    (soporte) no tiene persona que separar y lo dice explícito `registrado_por_soporte`."""
    NO_ES_QUIEN_EMITIO = "no_es_quien_emitio"
    """Quien emitió la invitación de mandato no la aprueba: cuatro ojos (A7, P1, 2b-4)."""
    NO_REGISTRO_LA_DECISION = "no_registro_la_decision"
    """Quien registró la decisión no la usa para dar de alta ni para remover (A15, P2, 2b-4b).
    Una decisión del equipo (soporte) o una baja sin decisión lo dicen explícito
    (`decision_del_equipo`, `sin_decision`)."""


class _Estricto(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ConcesionPorVinculo(_Estricto):
    tipos: tuple[TipoVinculo, ...] = Field(min_length=1)
    misma_unidad: StrictBool
    """Si es `true`, el vínculo tiene que ser de la unidad de la entidad (p. ej. P de 3-A)."""
    solo_confirmados: StrictBool = False
    """Si es `true`, solo concede un vínculo confirmado (P4, `nucleo.vinculo.confirmado_en`)."""

    @model_validator(mode="after")
    def _sin_repetidos(self) -> "ConcesionPorVinculo":
        if len(set(self.tipos)) != len(self.tipos):
            raise ValueError("tipos de vínculo repetidos")
        return self


class RecursoAceptado(_Estricto):
    """Qué recursos de facultad acepta la acción (§1.3). Sin este campo, solo la general."""

    tipos: tuple[TipoRecurso, ...] = Field(min_length=1)
    general: StrictBool
    """Si admite la facultad sin recurso, que alcanza a todo el consorcio del mandato."""

    @model_validator(mode="after")
    def _sin_repetidos(self) -> "RecursoAceptado":
        if len(set(self.tipos)) != len(self.tipos):
            raise ValueError("tipos de recurso repetidos")
        return self


class Politica(_Estricto):
    accion: CodigoAccion
    concede_por_vinculo: ConcesionPorVinculo | None = None
    concede_por_ambito: StrictBool = False
    """Concede a cualquiera que pase el ámbito. Hoy `unidades.listar` (D6),
    `auditoria.mis_eventos` (RF35-B) y `contactos.ver` (2b-7)."""
    facultad: CodigoAccion | None = None
    """Código exacto de la facultad (catálogo de D2) que concede la acción."""
    modos: tuple[ModoOperacion, ...]
    sin_modo: StrictBool
    """Si corre también sin modo registrado (las acciones marcadas "y —" en §2)."""
    recurso: RecursoAceptado | None = None
    separacion: tuple[CodigoSeparacion, ...] = ()
    respaldo: TextoNoVacio | None = None
    """Respaldo documental que exige la acción (R9). Sin él, se niega."""
    economica: StrictBool
    destinatario_unico: StrictBool = False
    """La transición necesita al único administrador vigente (A14)."""
    fuente_normativa: TextoNoVacio

    @model_validator(mode="after")
    def _coherente(self) -> "Politica":
        if len(set(self.modos)) != len(self.modos):
            raise ValueError("modos repetidos")
        if len(set(self.separacion)) != len(self.separacion):
            raise ValueError("códigos de separación repetidos")
        if self.recurso is not None and self.facultad is None:
            raise ValueError("`recurso` solo tiene sentido con `facultad`")
        return self


class ArchivoPoliticas(_Estricto):
    """Un archivo `politicas_datos/<módulo>.json`."""

    politicas: tuple[Politica, ...] = Field(min_length=1)


def esquema_texto() -> str:
    esquema = ArchivoPoliticas.model_json_schema()
    return json.dumps(esquema, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def archivos() -> list[Path]:
    """Los archivos de políticas, sin el esquema, en orden estable."""
    return sorted(p for p in DIRECTORIO.glob("*.json") if p != ESQUEMA)


if __name__ == "__main__":
    ESQUEMA.write_text(esquema_texto(), encoding="utf-8")
    print(f"escrito {ESQUEMA}")
