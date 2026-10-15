"""El informe de PA encuentra las pruebas `test_paNN_` y cuenta el P0-piloto (documento 14, §7)."""

from scripts import cobertura_pa


def test_el_conjunto_p0_piloto_tiene_las_25_pa_del_plan():
    assert len(cobertura_pa.P0_PILOTO) == len(set(cobertura_pa.P0_PILOTO)) == 25
    bordes = {"PA02", "PA12", "PA14", "PA17", "PA25", "PA32", "PA36", "PA37"}
    assert bordes <= set(cobertura_pa.P0_PILOTO)
    assert not {"PA01", "PA13", "PA18", "PA33"} & set(cobertura_pa.P0_PILOTO)


def test_encuentra_las_pruebas_ya_nombradas():
    encontradas = cobertura_pa.cobertura()
    assert "backend/tests/test_rls.py" in encontradas["PA15"]
    assert "backend/tests/test_cola.py" in encontradas["PA05"]


def test_estricto_falla_solo_si_falta_alguna(capsys):
    faltan = set(cobertura_pa.P0_PILOTO) - set(cobertura_pa.cobertura())
    assert cobertura_pa.main([]) == 0
    assert cobertura_pa.main(["--estricto"]) == (1 if faltan else 0)
    assert "Sin prueba:" in capsys.readouterr().out
