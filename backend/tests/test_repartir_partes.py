"""Reparto de los archivos de prueba en partes para correrlas a la vez (ci.ps1)."""

import pytest
from tests.conftest import repartir

ARCHIVOS = {f"tests/test_{i:03}.py": (i * 37) % 53 + 1 for i in range(120)}


@pytest.mark.parametrize("partes", [1, 2, 4, 6])
def test_cada_archivo_va_a_una_sola_parte(partes):
    grupos = repartir(ARCHIVOS, partes)
    assert len(grupos) == partes
    assert sorted(a for g in grupos for a in g) == sorted(ARCHIVOS)


def test_es_determinista():
    assert repartir(ARCHIVOS, 4) == repartir(dict(reversed(list(ARCHIVOS.items()))), 4)


def test_las_partes_quedan_parejas():
    cargas = [sum(ARCHIVOS[a] for a in g) for g in repartir(ARCHIVOS, 4)]
    assert max(cargas) - min(cargas) <= max(ARCHIVOS.values())


def test_control_un_reparto_que_pierde_archivos_se_nota():
    grupos = repartir(ARCHIVOS, 4)
    grupos[0].pop()
    assert sorted(a for g in grupos for a in g) != sorted(ARCHIVOS)
