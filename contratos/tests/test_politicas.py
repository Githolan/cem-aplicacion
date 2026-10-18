"""Modelo de políticas de autorización y su instantánea (H2b 2b-3; tabla de verdad, §6)."""

import json

import pytest
from pydantic import ValidationError

from cem_contracts.politicas import ESQUEMA, ArchivoPoliticas, Politica, archivos, esquema_texto

MINIMA = {
    "accion": "padron.ver",
    "facultad": "padron.ver",
    "modos": ["consejo"],
    "sin_modo": False,
    "economica": False,
    "fuente_normativa": "D2",
}


def test_esquema_coincide_con_la_instantanea_aprobada():
    assert ESQUEMA.read_text(encoding="utf-8") == esquema_texto(), (
        "El modelo de políticas cambió. Si el cambio es intencional, regenerar con "
        "`python -m cem_contracts.politicas` desde contratos/ y revisarlo en el PR."
    )


def test_todos_los_archivos_validan_y_hay_uno_por_modulo():
    nombres = {p.stem for p in archivos()}
    assert nombres == {
        "consorcios", "invitaciones", "padron", "mandatos", "fiscalizacion",
        "documentos", "liquidacion", "egresos", "fondo_reserva", "coeficientes",
    }  # fmt: skip
    for ruta in archivos():
        ArchivoPoliticas.model_validate(json.loads(ruta.read_text(encoding="utf-8")))


def test_control_la_politica_minima_valida():
    assert Politica.model_validate(MINIMA).concede_por_ambito is False


@pytest.mark.parametrize("fuente", [None, "", "   "])
def test_sin_fuente_normativa_no_valida(fuente):
    datos = {k: v for k, v in MINIMA.items() if k != "fuente_normativa"}
    if fuente is not None:
        datos["fuente_normativa"] = fuente
    with pytest.raises(ValidationError, match="fuente_normativa"):
        Politica.model_validate(datos)


@pytest.mark.parametrize(
    "cambio",
    [
        {"concede_por_vinculos": None},  # campo mal escrito: extra="forbid"
        {"recurso": {"tipos": ["edificio"], "general": True}},
        {"recurso": {"tipos": [], "general": True}},
        {"recurso": {"tipos": ["unidad", "unidad"], "general": True}},
        {"modos": ["consejo", "consejo"]},
        {"modos": ["otro"]},
        {"separacion": ["no_es_quien_preparo", "no_es_quien_preparo"]},
        {"separacion": ["inventada"]},
        {"concede_por_vinculo": {"tipos": [], "misma_unidad": True}},
        {"accion": "Padron.Ver"},
        {"accion": "padron"},
        {"facultad": None, "recurso": {"tipos": ["unidad"], "general": True}},
    ],
)
def test_politica_mal_formada_no_valida(cambio):
    with pytest.raises(ValidationError):
        Politica.model_validate(MINIMA | cambio)


BOOLEANOS = [
    ("concede_por_ambito", lambda v: {"concede_por_ambito": v}),
    ("sin_modo", lambda v: {"sin_modo": v}),
    ("economica", lambda v: {"economica": v}),
    ("destinatario_unico", lambda v: {"destinatario_unico": v}),
    (
        "misma_unidad",
        lambda v: {"concede_por_vinculo": {"tipos": ["inquilino"], "misma_unidad": v}},
    ),
    ("general", lambda v: {"recurso": {"tipos": ["unidad"], "general": v}}),
]


@pytest.mark.parametrize("campo,cambio", BOOLEANOS, ids=[c for c, _ in BOOLEANOS])
@pytest.mark.parametrize("valor", ["yes", "no", "true", 1, 0])
def test_los_booleanos_no_aceptan_valores_convertibles(campo, cambio, valor):
    """Deciden concesiones: un `"yes"` escrito a mano no se convierte en `true`."""
    with pytest.raises(ValidationError, match=campo):
        Politica.model_validate(MINIMA | cambio(valor))


@pytest.mark.parametrize("campo,cambio", BOOLEANOS, ids=[c for c, _ in BOOLEANOS])
def test_control_los_booleanos_aceptan_true_y_false(campo, cambio):
    for valor in (True, False):
        Politica.model_validate(MINIMA | cambio(valor))


def test_las_politicas_cargadas_son_inmutables():
    """Las mismas instancias sirven a todo el proceso: ni un campo ni una colección se alteran."""
    p = Politica.model_validate(
        MINIMA | {"separacion": ["no_es_su_propio_vinculo"], "concede_por_vinculo": None}
    )
    assert isinstance(p.modos, tuple) and isinstance(p.separacion, tuple)
    with pytest.raises(AttributeError):
        p.modos.append("autogestion")  # type: ignore[attr-defined]
    with pytest.raises(AttributeError):
        p.separacion.clear()  # type: ignore[attr-defined]
    with pytest.raises(ValidationError):
        p.economica = True  # control: el modelo ya era frozen en sus campos
