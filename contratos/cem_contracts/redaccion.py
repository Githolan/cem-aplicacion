"""La regla de redacción de la aplicación (pregunta 9 del propietario, 13-11-2026; tanda 4).

Todo texto que ve la persona va impersonal y en infinitivo: sin voseo, sin tuteo, sin «usted» y
sin primera persona (ni del sistema, «Estamos revisando», ni de quien usa la aplicación, «Pagué
otra cosa»). Las palabras y los controles están en `pantallas_datos/redaccion.json`, que también lee
la prueba del frontend (`frontend/src/redaccion.test.ts`): las dos implementaciones tienen que
clasificar igual los controles del archivo.

La comparación es por palabra entera y en minúscula, así «vos» dentro de «archivos» o «sos»
dentro de «Sosa» no cuentan. Es una red, no un analizador: una forma que no está prevista en el
archivo pasa, y se agrega cuando aparece.
"""

import json
import re
from functools import cache
from pathlib import Path

from pydantic import BaseModel, ConfigDict

ARCHIVO = Path(__file__).resolve().parent / "pantallas_datos" / "redaccion.json"

_PALABRA = re.compile(r"[^\W\d_]+")
# Primera palabra de una oración: al empezar el texto, después de un punto, dos puntos, signo de
# cierre con espacio, o después de un signo de apertura.
_AL_EMPEZAR = re.compile(r"(?:^|[.:!?]\s+|[¿¡«(]\s*)([^\W\d_]+)")
_ACENTO_FINAL = ("á", "é", "í")


class Redaccion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    descripcion: str
    segunda_persona: frozenset[str]
    voseo_terminaciones: tuple[str, ...]
    voseo_no_son: frozenset[str]
    preterito_terminaciones: tuple[str, ...]
    preterito_no_son: frozenset[str]
    primera_plural_terminaciones: tuple[str, ...]
    primera_plural_no_son: frozenset[str]
    imperativo_al_empezar_no_son: frozenset[str]
    formas: frozenset[str]
    control_con_falta: tuple[str, ...]
    control_sin_falta: tuple[str, ...]


@cache
def reglas() -> Redaccion:
    return Redaccion.model_validate(json.loads(ARCHIVO.read_text(encoding="utf-8")))


def faltas(texto: str) -> list[str]:
    """Las palabras de `texto` que rompen la regla, cada una con su motivo. Vacía si está bien."""
    r = reglas()
    encontradas: list[str] = []
    for palabra in (p.lower() for p in _PALABRA.findall(texto)):
        if palabra in r.segunda_persona:
            encontradas.append(f"{palabra} (segunda persona)")
        elif palabra in r.formas:
            encontradas.append(f"{palabra} (voseo, tuteo o primera persona)")
        elif palabra.endswith(r.voseo_terminaciones) and palabra not in r.voseo_no_son:
            encontradas.append(f"{palabra} (voseo)")
        elif palabra.endswith(r.preterito_terminaciones) and palabra not in r.preterito_no_son:
            encontradas.append(f"{palabra} (pretérito en segunda persona)")
        elif (
            palabra.endswith(r.primera_plural_terminaciones)
            and palabra not in r.primera_plural_no_son
        ):
            encontradas.append(f"{palabra} (primera persona del plural)")
    for palabra in (p.lower() for p in _AL_EMPEZAR.findall(texto)):
        if palabra.endswith(_ACENTO_FINAL) and palabra not in r.imperativo_al_empezar_no_son:
            if palabra not in r.formas:
                encontradas.append(f"{palabra} (voseo o primera persona al empezar)")
    return encontradas
