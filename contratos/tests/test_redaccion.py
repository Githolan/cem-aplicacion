"""La regla de redacción (pregunta 9 del propietario, 13-11-2026) clasifica bien sus controles.

El mismo archivo de controles lo usa la prueba del frontend (`src/redaccion.test.ts`): si una de
las dos implementaciones cambia, un control deja de dar lo mismo.
"""

import pytest

from cem_contracts.redaccion import faltas, reglas


@pytest.mark.parametrize("texto", reglas().control_con_falta)
def test_cada_control_con_voseo_tuteo_o_primera_persona_falla(texto):
    assert faltas(texto), texto


@pytest.mark.parametrize("texto", reglas().control_sin_falta)
def test_cada_control_bien_escrito_pasa(texto):
    assert faltas(texto) == [], texto


def test_las_palabras_se_comparan_enteras():
    # «vos» dentro de «archivos» y «sos» dentro de «Sosa» no cuentan; sueltas, sí.
    assert faltas("Los archivos de la Sra. Sosa") == []
    assert faltas("Sos vos") == ["sos (segunda persona)", "vos (segunda persona)"]


def test_el_motivo_de_cada_regla():
    assert faltas("Listo: rechazaste la orden.") == ["rechazaste (pretérito en segunda persona)"]
    assert faltas("Estamos revisando.") == ["estamos (primera persona del plural)"]
    assert faltas("Probá de nuevo.") == ["probá (voseo, tuteo o primera persona)"]
    assert faltas("Renová la orden.") == ["renová (voseo, tuteo o primera persona)"]
    assert faltas("Instalé la aplicación.") == ["instalé (voseo o primera persona al empezar)"]
