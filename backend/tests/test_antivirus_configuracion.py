"""El plazo de las firmas del antivirus vive fuera del código (`CEM_ANTIVIRUS_FIRMAS_HORAS`).

Regla del propietario del 27-11-2026: ningún valor configurable en el código. Sin la variable, o mal
escrita, `cem-trabajos` no arranca; nunca se toma un valor por omisión en silencio.
"""

from datetime import timedelta

import pytest

from app.plataforma import antivirus


def test_lee_las_horas_de_la_variable(monkeypatch):
    monkeypatch.setenv(antivirus.VARIABLE_FIRMAS_HORAS, "168")
    assert antivirus.firmas_vigentes() == timedelta(days=7)
    monkeypatch.setenv(antivirus.VARIABLE_FIRMAS_HORAS, "48")  # control: otro valor, otro plazo
    assert antivirus.firmas_vigentes() == timedelta(hours=48)


@pytest.mark.parametrize(
    ("valor", "mensaje"),
    [
        (None, "falta la variable de entorno CEM_ANTIVIRUS_FIRMAS_HORAS"),
        ("", "falta la variable de entorno CEM_ANTIVIRUS_FIRMAS_HORAS"),
        ("siete", "tiene que ser un número entero de horas"),
        ("1.5", "tiene que ser un número entero de horas"),
        ("0", "tiene que ser positiva"),
        ("-48", "tiene que ser positiva"),
    ],
)
def test_sin_la_variable_o_mal_escrita_no_arranca(monkeypatch, valor, mensaje):
    if valor is None:
        monkeypatch.delenv(antivirus.VARIABLE_FIRMAS_HORAS, raising=False)
    else:
        monkeypatch.setenv(antivirus.VARIABLE_FIRMAS_HORAS, valor)
    with pytest.raises(antivirus.ConfiguracionDelAntivirus, match=mensaje):
        antivirus.exigir_configuracion()


def test_al_arrancar_trabajos_exige_el_plazo(monkeypatch):
    """`main` de `cem-trabajos` lo pide antes de tomar ningún trabajo."""
    from app.plataforma import trabajos

    monkeypatch.delenv(antivirus.VARIABLE_FIRMAS_HORAS, raising=False)
    monkeypatch.setattr(trabajos.lectura_comprobantes, "exigir_configuracion", lambda: None)
    monkeypatch.setattr(trabajos.registros, "configurar", lambda: None)

    def paso_de_largo():
        raise AssertionError("arrancó sin el plazo de las firmas")

    # Si `main` no lo exigiera, seguiría hasta acá: falla sin quedarse consumiendo la cola.
    monkeypatch.setattr(trabajos, "lote_configurado", paso_de_largo)
    with pytest.raises(antivirus.ConfiguracionDelAntivirus):
        trabajos.main()
