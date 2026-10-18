"""Gramática del recurso de una facultad (A8; tabla de verdad, §1.3 y R7)."""

import pytest
from cem_contracts.politicas import RecursoAceptado, TipoRecurso

from app.autorizacion.recursos import MotivoRecurso, parsear, verificar

X = "0192f0c4-7a1e-7c3b-9d2a-5b8e4f1a2c3d"
Y = "0192f0c4-7a1e-7c3b-9d2a-5b8e4f1a2c3f"
CUENTA = RecursoAceptado(tipos=[TipoRecurso.CUENTA], general=True)
UNIDAD = RecursoAceptado(tipos=[TipoRecurso.UNIDAD], general=True)
EGRESOS = RecursoAceptado(tipos=[TipoRecurso.CUENTA, TipoRecurso.GRUPO], general=True)
ORDEN = {TipoRecurso.CUENTA: X, TipoRecurso.GRUPO: "agua"}


def test_control_cuenta_exacta_alcanza():
    assert verificar(f"cuenta:{X}", CUENTA, ORDEN) is None


def test_control_grupo_exacto_alcanza():
    assert verificar("grupo:agua", EGRESOS, ORDEN) is None


def test_control_sin_recurso_alcanza_todo_el_consorcio():
    assert verificar(None, CUENTA, ORDEN) is None
    assert verificar(None, None, {}) is None  # acción sin recurso: solo la general


@pytest.mark.parametrize(
    "recurso,aceptado,motivo",
    [
        ("cuenta:", CUENTA, MotivoRecurso.INVALIDO),
        ("cuenta:abc", CUENTA, MotivoRecurso.INVALIDO),
        (f"CUENTA:{X}", CUENTA, MotivoRecurso.INVALIDO),
        (f"cuenta:{X.upper()}", CUENTA, MotivoRecurso.INVALIDO),
        (f"edificio:{X}", CUENTA, MotivoRecurso.INVALIDO),
        (f"unidad:{X}", CUENTA, MotivoRecurso.TIPO_INESPERADO),
        (f"cuenta:{X}:x", CUENTA, MotivoRecurso.INVALIDO),
        (f"cuenta:{X}", None, MotivoRecurso.TIPO_INESPERADO),  # acción sin recurso
        (f"cuenta:{Y}", CUENTA, MotivoRecurso.NO_ALCANZA),
        (f"cuenta:{X}\n", CUENTA, MotivoRecurso.INVALIDO),  # `$` aceptaría el salto final
        (f"cuenta: {X}", CUENTA, MotivoRecurso.INVALIDO),
        (f"cuenta:{{{X}}}", CUENTA, MotivoRecurso.INVALIDO),
        (f"cuenta:{X.replace('-', '')}", CUENTA, MotivoRecurso.INVALIDO),
        ("grupo:Agua", EGRESOS, MotivoRecurso.INVALIDO),
        ("grupo:1agua", EGRESOS, MotivoRecurso.INVALIDO),
        ("grupo:" + "a" * 64, EGRESOS, MotivoRecurso.INVALIDO),
        ("grupo:agua-fria", EGRESOS, MotivoRecurso.INVALIDO),
        ("grupo:", EGRESOS, MotivoRecurso.INVALIDO),
        (":agua", EGRESOS, MotivoRecurso.INVALIDO),
        ("agua", EGRESOS, MotivoRecurso.INVALIDO),
        ("", EGRESOS, MotivoRecurso.INVALIDO),
    ],
)
def test_casos_que_nunca_conceden(recurso, aceptado, motivo):
    assert verificar(recurso, aceptado, ORDEN) == motivo


def test_codigo_de_grupo_de_63_caracteres_vale():
    largo = "a" * 63
    assert verificar(f"grupo:{largo}", EGRESOS, {TipoRecurso.GRUPO: largo}) is None


@pytest.mark.parametrize(
    "facultad,entidad",
    [
        ("grupo:agua", "agua_caliente"),  # prefijo
        ("grupo:agua_caliente", "agua"),  # la entidad es prefijo de la facultad
        ("grupo:gua", "agua"),  # subcadena
        (f"unidad:{X}", X[:-1] + "e"),  # unidad:X no alcanza a unidad:X2
    ],
)
def test_nunca_por_prefijo_ni_subcadena(facultad, entidad):
    tipo = TipoRecurso(facultad.split(":")[0])
    aceptado = UNIDAD if tipo is TipoRecurso.UNIDAD else EGRESOS
    assert verificar(facultad, aceptado, {tipo: entidad}) == MotivoRecurso.NO_ALCANZA


def test_tipo_e_id_se_comparan_por_separado():
    # cuenta:X no alcanza a una entidad cuyo recurso es unidad:X.
    assert verificar(f"cuenta:{X}", EGRESOS, {TipoRecurso.UNIDAD: X}) == MotivoRecurso.NO_ALCANZA


def test_entidad_sin_ese_tipo_no_alcanza():
    assert verificar(f"cuenta:{X}", CUENTA, {}) == MotivoRecurso.NO_ALCANZA


def test_accion_sin_facultad_general():
    sin_general = RecursoAceptado(tipos=[TipoRecurso.CUENTA], general=False)
    assert verificar(None, sin_general, ORDEN) == MotivoRecurso.TIPO_INESPERADO
    assert verificar(f"cuenta:{X}", sin_general, ORDEN) is None  # control


def test_parsear():
    assert parsear(f"cuenta:{X}") is not None
    assert parsear(f"cuenta:{X}").tipo is TipoRecurso.CUENTA
    assert parsear("cuenta") is None
    assert parsear("a:b:c") is None
    assert parsear("Unidad:x") is None
