"""El ensayo sintético de Autogestión (`pruebas/ensayo_autogestion_staging.py`) corre entero contra
un backend real, como el smoke; corre una sola vez por consorcio LAB; su guarda se niega fuera de
staging; y el fixture `pruebas/cem_lab_autogestion.json` lleva las marcas de NO REAL.

Los datos son los del fixture con otros identificadores y correos: la base de pruebas es
compartida entre pruebas, y el ensayo se niega a repetir un consorcio LAB."""

import base64
import json
import re
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text

from pruebas import ensayo_autogestion_staging as ensayo
from tests.test_smoke_staging import HOST, ORIGEN_STAGING, entorno, servidor  # noqa: F401

FIXTURE = Path(__file__).resolve().parents[1] / "pruebas" / "cem_lab_autogestion.json"


def datos_frescos() -> dict:
    datos = json.loads(FIXTURE.read_text(encoding="utf-8"))
    marca = uuid4().hex[:8]
    for camino in datos["caminos"].values():
        camino["consorcio_id"] = str(uuid4())
        for p in camino["personas"].values():
            p["correo"] = p["correo"].replace("@", f"-{marca}@")
    return datos


def correr(servidor, base, datos, capsys) -> tuple[int, dict | None, str]:  # noqa: F811
    cifrado = base64.b64encode(json.dumps(datos).encode()).decode()
    codigo = ensayo.main(["--datos", cifrado, "--base", base, "--url", servidor,
                          "--host", HOST])  # fmt: skip
    salida = capsys.readouterr()
    inicio = salida.out.find("\n{")
    return codigo, (json.loads(salida.out[inicio:]) if inicio >= 0 else None), salida.err


# El fixture ----------------------------------------------------------------------------------


def test_el_fixture_esta_marcado_como_no_real_y_sin_datos_de_personas():
    crudo = FIXTURE.read_text(encoding="utf-8")
    datos = json.loads(crudo)
    assert "NO USAR COMO EVIDENCIA DE PILOTO REAL" in datos["aviso"]
    assert len(datos["unidades"]) == 37 == len(set(datos["unidades"]))
    assert all(u.startswith("LAB ") for u in datos["unidades"])
    for camino in datos["caminos"].values():
        assert all(m in camino["nombre"] for m in ("LAB", "SINTETICO", "PRUEBA", "NO REAL"))
        for p in camino["personas"].values():
            assert p["correo"].endswith("@example.invalid")
            assert "Sintético" in p["nombre"] and "NO REAL" in p["nombre"]
            assert p["unidad"] in datos["unidades"]
        for acta in camino["actas"].values():
            assert "ACTA SINTETICA DE PRUEBA — SIN VALIDEZ JURIDICA" in acta["referencia"]
            assert acta["descripcion"].startswith("SINTETICO / PRUEBA / NO REAL")
    assert "SIN VALIDEZ / SOLO PRUEBA" in datos["caminos"]["A"]["matricula_rpa"]
    # Nada con forma de documento, CUIT, CBU o teléfono: ningún número de 7 cifras o más.
    assert not re.search(r"\d{7,}", crudo)


# El ensayo -----------------------------------------------------------------------------------


def test_el_ensayo_recorre_los_dos_caminos_y_queda_como_fixture(
    servidor,  # noqa: F811
    entorno,  # noqa: F811
    capsys,
    motor_migrate,
):
    datos = datos_frescos()
    codigo, resumen, error = correr(servidor, entorno, datos, capsys)
    assert codigo == 0, error
    assert resumen["sesiones_abiertas_al_cerrar"] == 0
    assert resumen["navegadores_que_siguen_entrando"] == []
    controles = resumen["controles"]
    assert len(controles) == 31, controles
    for motivo in ("decision_ya_usada", "transicion_requiere_fin_de_mandato", "fecha_futura",
                   "decision_invalida", "vigencia_invalida", "transicion_no_admitida",
                   "administracion_ocupada", "mismo_modo"):  # fmt: skip
        assert any(motivo in c for c in controles), motivo
    a = datos["caminos"]["A"]["consorcio_id"]
    b = datos["caminos"]["B"]["consorcio_id"]
    with motor_migrate.connect() as c:
        modos = {
            str(k): v
            for k, v in c.execute(
                text("select consorcio_id, array_agg(modo::text order by vigente_desde)"
                     " from nucleo.modo_operacion where consorcio_id = any(:c)"
                     " group by consorcio_id"), {"c": [a, b]},
            )
        }  # fmt: skip
        mandatos = c.execute(
            text("select count(*) filter (where consorcio_id = :a),"
                 " count(*) filter (where consorcio_id = :b),"
                 " count(*) filter (where tipo = 'administrador_externo')"
                 " from nucleo.mandato where consorcio_id in (:a, :b)"), {"a": a, "b": b},
        ).one()  # fmt: skip
        referencias = c.execute(
            text("select bool_and(referencia_acta like 'ACTA SINTETICA DE PRUEBA — SIN VALIDEZ"
                 " JURIDICA%'), count(*) from nucleo.decision_formal"
                 " where consorcio_id in (:a, :b)"), {"a": a, "b": b},
        ).one()  # fmt: skip
        temporales = c.execute(
            text("select count(*) from nucleo.consorcio where nombre like"
                 " 'CEM LAB — temporal%'")
        ).scalar_one()  # fmt: skip
    assert modos == {a: ["autogestion"], b: ["consejo", "autogestion"]}
    # A: el administrador; B: dos consejeros y el administrador. Ningún externo de los negativos.
    assert tuple(mandatos) == (1, 3, 0)
    assert tuple(referencias) == (True, 3)
    assert temporales == 0

    # Corre una sola vez: con los consorcios ya creados se niega sin escribir.
    codigo, _, error = correr(servidor, entorno, datos, capsys)
    assert codigo == 4 and "ya existe" in error


def test_la_guarda_se_niega_fuera_de_staging_sin_escribir(
    servidor,  # noqa: F811
    entorno,  # noqa: F811
    capsys,
    monkeypatch,
    motor_migrate,
):
    datos = datos_frescos()
    monkeypatch.setenv("CEM_ORIGEN_PUBLICO", "https://cem.ejemplo.com")
    codigo, _, error = correr(servidor, entorno, datos, capsys)
    assert codigo == 3 and "no es de staging" in error
    monkeypatch.setenv("CEM_ORIGEN_PUBLICO", ORIGEN_STAGING)
    codigo, _, error = correr(servidor, "cem-postgres", datos, capsys)
    assert codigo == 3 and "no a --base" in error
    # Un fixture sin las marcas tampoco corre.
    datos["caminos"]["A"]["nombre"] = "Edificio real"
    codigo, _, error = correr(servidor, entorno, datos, capsys)
    assert codigo == 3 and "marcas" in error
    datos["caminos"]["A"]["nombre"] += " LAB SINTETICO NO REAL"
    datos["unidades"][0] = "PB-A"
    codigo, _, error = correr(servidor, entorno, datos, capsys)
    assert codigo == 3 and "una unidad no lleva las marcas" in error
    with motor_migrate.connect() as c:
        creados = c.execute(
            text("select count(*) from nucleo.consorcio where id = any(:c)"),
            {"c": [x["consorcio_id"] for x in datos["caminos"].values()]},
        ).scalar_one()
    assert creados == 0


@pytest.mark.parametrize("motivo", [None, "fecha_invalida"])
def test_un_error_sin_el_motivo_no_pasa_por_negativo_ni_por_control(motor_app, motivo):
    """Revisión independiente de #149: un error cualquiera (sin hint, otro SQLSTATE) no puede
    contar como el rechazo esperado ni como el control positivo «el cambio pasa»."""
    e = ensayo.Ensayo(motor_app, {}, "", "", "", None)

    def roto(c):
        c.execute(text("select 1 / 0"))

    with pytest.raises(ensayo.Fallo):
        e.negado_en_transaccion(uuid4(), lambda c: None, roto, motivo, "control")
    assert e.controles == []
