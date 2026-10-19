"""Evaluador puro de permisos (tabla de verdad, §1 y §2; plan de implementación, PR 1).

Complementa la tabla de verdad (`test_tabla_de_verdad.py`) con las propiedades que la tabla
no puede expresar fila por fila: que ningún filtro concede, que toda económica se niega, que
la carga de políticas falla fuerte, y los controles de cada regla.
"""

import json
from dataclasses import replace
from datetime import date
from uuid import UUID

import pytest
from cem_contracts.estados import ModoOperacion, TipoVinculo
from cem_contracts.politicas import Politica, TipoRecurso
from pydantic import ValidationError

from app.autorizacion.evaluador import (
    ContextoAutorizacion,
    Entidad,
    Facultad,
    Mandato,
    Vinculo,
    evaluar,
)
from app.autorizacion.politicas import POLITICAS, PoliticasInvalidas, cargar
from tests.escenarios_autorizacion import CATALOGO, CONSORCIO, HOY, OTRO_CONSORCIO, SIMBOLOS

YO = UUID("0192f0c4-0000-7000-8000-000000000001")
OTRO = UUID("0192f0c4-0000-7000-8000-000000000002")
U3A = UUID(SIMBOLOS["3A"])
MANDATO = UUID(int=1)


def mandato(consorcio: UUID = CONSORCIO) -> Mandato:
    return Mandato(id=MANDATO, consorcio_id=consorcio, vigente_desde=date(2026, 1, 1))


def contexto(
    *,
    vinculos: tuple[Vinculo, ...] = (),
    facultades: tuple[str, ...] | None = None,
    modo: ModoOperacion | None = ModoOperacion.CONSEJO,
    consorcio_mandato: UUID = CONSORCIO,
    administrador: bool = True,
    persona: UUID | None = YO,
) -> ContextoAutorizacion:
    """`facultades=None`: sin mandato; `()`: un mandato vigente sin ninguna facultad."""
    mandatos = () if facultades is None else (mandato(consorcio_mandato),)
    return ContextoAutorizacion(
        persona_id=persona,
        consorcio_id=CONSORCIO,
        hoy=HOY,
        vinculos=vinculos,
        modo=modo,
        mandatos=mandatos,
        facultades=tuple(Facultad(c, None, MANDATO) for c in facultades or ()),
        hay_administrador_vigente=administrador,
    )


def P(unidad: UUID = U3A) -> Vinculo:
    return Vinculo(TipoVinculo.PROPIETARIO, unidad)


def I(unidad: UUID = U3A) -> Vinculo:  # noqa: E743
    return Vinculo(TipoVinculo.INQUILINO, unidad)


def O(unidad: UUID = U3A) -> Vinculo:  # noqa: E743
    return Vinculo(TipoVinculo.OCUPANTE, unidad)


# Entidad en la que todos los filtros pasan: otra persona preparó, aprobó, solicitó, propuso,
# registró y emitió; sin firmas previas; con respaldo; recursos de todos los tipos.
TODO_PASA = Entidad(
    recursos={
        TipoRecurso.UNIDAD: str(U3A),
        TipoRecurso.CUENTA: SIMBOLOS["X"],
        TipoRecurso.GRUPO: "agua",
    },
    preparado_por=OTRO,
    aprobado_por=(OTRO,),
    solicitado_por=OTRO,
    propuesto_por=OTRO,
    persona_objetivo=OTRO,
    registrado_por=OTRO,
    emitido_por=OTRO,
    decision_registrada_por=OTRO,
    respaldo_presente=True,
)


def modo_que_pasa(p: Politica) -> ModoOperacion | None:
    return p.modos[0] if p.modos else None


# --- Ningún filtro concede (criterio de aceptación del PR 1) -------------------------------


@pytest.mark.parametrize(
    "accion", sorted(a for a, p in POLITICAS.items() if not p.concede_por_ambito)
)
def test_ningun_filtro_concede(accion):
    p = POLITICAS[accion]
    # En el ámbito por un mandato vigente sin facultades y sin vínculo: no hay concesión.
    ctx = contexto(facultades=(), modo=modo_que_pasa(p))
    d = evaluar(accion, ctx, TODO_PASA)
    assert (d.permitido, d.estado, d.motivo_interno) == (False, 403, "sin_concesion")


@pytest.mark.parametrize("accion", sorted(a for a, p in POLITICAS.items() if p.facultad))
def test_control_con_su_facultad_la_concesion_pasa(accion):
    # Control de la prueba anterior: con la facultad exacta, lo que decide son los filtros.
    p = POLITICAS[accion]
    d = evaluar(accion, contexto(facultades=(p.facultad,), modo=modo_que_pasa(p)), TODO_PASA)
    esperado = "requiere_controles_2b6" if p.economica else "concedido_por_facultad"
    assert d.motivo_interno == esperado


@pytest.mark.parametrize("accion", sorted(a for a, p in POLITICAS.items() if p.economica))
def test_toda_economica_se_niega_aunque_todo_pase(accion):
    todas = tuple(CATALOGO)
    d = evaluar(accion, contexto(facultades=todas, modo=ModoOperacion.AUTOGESTION), TODO_PASA)
    assert (d.estado, d.motivo_interno) == (403, "requiere_controles_2b6")


def test_modo_cambiar_no_lo_concede_nadie():
    todas = tuple(CATALOGO)
    for ctx in (
        contexto(vinculos=(P(), I(), O()), facultades=todas),
        contexto(vinculos=(P(),), facultades=todas, modo=None),
    ):
        d = evaluar("modo.cambiar", ctx, TODO_PASA)
        assert (d.estado, d.motivo_interno) == (403, "sin_concesion")


# --- Ámbito y aislamiento (R2, R11) --------------------------------------------------------


def test_sin_vinculo_ni_mandato_es_404_y_con_mandato_entra():
    assert evaluar("unidades.listar", contexto(), TODO_PASA).estado == 404
    assert evaluar("unidades.listar", contexto(facultades=()), TODO_PASA).estado == 200


def test_facultad_de_otro_consorcio_no_cuenta():
    otro = contexto(vinculos=(P(),), facultades=("padron.ver",), consorcio_mandato=OTRO_CONSORCIO)
    assert evaluar("padron.ver", otro, TODO_PASA).motivo_interno == "sin_concesion"
    # Sin vínculo, el mandato de otro consorcio ni siquiera da ámbito.
    solo = contexto(facultades=("padron.ver",), consorcio_mandato=OTRO_CONSORCIO)
    assert evaluar("padron.ver", solo, TODO_PASA).estado == 404
    # Control: el mismo mandato en este consorcio concede.
    aca = contexto(vinculos=(P(),), facultades=("padron.ver",))
    assert evaluar("padron.ver", aca, TODO_PASA).motivo_interno == "concedido_por_facultad"


def test_facultad_de_un_mandato_que_no_esta_en_el_contexto_no_cuenta():
    ctx = contexto(vinculos=(P(),), facultades=())
    huerfana = Facultad("padron.ver", None, UUID(int=99))
    ctx = replace(ctx, facultades=(huerfana,))
    assert evaluar("padron.ver", ctx, TODO_PASA).motivo_interno == "sin_concesion"


def test_accion_no_declarada_se_niega():
    d = evaluar("padron.inventada", contexto(vinculos=(P(),)), TODO_PASA)
    assert (d.estado, d.motivo_interno) == (403, "accion_no_declarada")


# --- Concesión por ámbito (unidades.listar) -------------------------------------------------


def test_unidades_listar_por_ambito_y_control_sin_la_declaracion():
    ad_sin_vinculo = contexto(facultades=("documentos.cargar",))
    d = evaluar("unidades.listar", ad_sin_vinculo, Entidad())
    assert (d.estado, d.motivo_interno) == (200, "concedido_por_ambito")
    politicas = dict(POLITICAS)
    politicas["unidades.listar"] = POLITICAS["unidades.listar"].model_copy(
        update={"concede_por_ambito": False}
    )
    d = evaluar("unidades.listar", ad_sin_vinculo, Entidad(), politicas)
    assert (d.estado, d.motivo_interno) == (403, "sin_concesion")


def test_solo_unidades_listar_concede_por_ambito():
    assert [a for a, p in POLITICAS.items() if p.concede_por_ambito] == ["unidades.listar"]


# --- Una acción, una vista; A6 --------------------------------------------------------------


def test_p_ve_el_resumen_y_no_el_panel_completo():
    ctx = contexto(vinculos=(P(),))
    assert evaluar("fiscalizacion.ver_resumen", ctx, TODO_PASA).permitido
    assert not evaluar("fiscalizacion.ver_panel", ctx, TODO_PASA).permitido
    # Control: con la facultad del panel, lo ve.
    mc = contexto(facultades=("fiscalizacion.ver_panel",))
    assert evaluar("fiscalizacion.ver_panel", mc, TODO_PASA).permitido


@pytest.mark.parametrize("vinculo", [I, O])
def test_inquilino_u_ocupante_no_ven_documentos_comunes(vinculo):
    d = evaluar("documentos.ver", contexto(vinculos=(vinculo(),)), TODO_PASA)
    assert (d.estado, d.motivo_interno) == (403, "sin_concesion")
    # Control: con un mandato con documentos.ver, lo ve por la facultad.
    con_fac = contexto(vinculos=(vinculo(),), facultades=("documentos.ver",))
    assert evaluar("documentos.ver", con_fac, TODO_PASA).motivo_interno == "concedido_por_facultad"


def test_control_p_ve_documentos_comunes():
    d = evaluar("documentos.ver", contexto(vinculos=(P(),)), TODO_PASA)
    assert d.motivo_interno == "concedido_por_vinculo"


# --- Modos (R6, A3) -------------------------------------------------------------------------


@pytest.mark.parametrize("accion", ["solicitud_formal.preparar", "solicitud_formal.firmar"])
@pytest.mark.parametrize(
    "modo,permitido",
    [
        (ModoOperacion.CONSEJO, True),
        (ModoOperacion.ADMINISTRADOR_AUMENTADO, True),
        (ModoOperacion.AUTOGESTION, False),
        (None, False),
    ],
)
def test_solicitud_formal_solo_en_consejo_y_administrador_aumentado(accion, modo, permitido):
    d = evaluar(accion, contexto(facultades=(POLITICAS[accion].facultad,), modo=modo), Entidad())
    assert d.permitido is permitido


# --- Destinatario único (A14) ---------------------------------------------------------------


def test_segunda_firma_sin_administrador_vigente():
    ctx = contexto(facultades=("solicitud_formal.firmar",), administrador=False)
    segunda = Entidad(firmas=(OTRO,))
    d = evaluar("solicitud_formal.firmar", ctx, segunda)
    assert (d.estado, d.motivo_interno) == (403, "sin_administrador_vigente")
    # Control: la primera firma no necesita destinatario, y con administrador emite.
    assert evaluar("solicitud_formal.firmar", ctx, Entidad()).permitido
    con_ad = contexto(facultades=("solicitud_formal.firmar",), administrador=True)
    assert evaluar("solicitud_formal.firmar", con_ad, segunda).permitido


# --- Separación por persona (R8, A4, A5) ----------------------------------------------------


def test_actor_nulo_se_niega_y_control_con_persona_pasa():
    sin_actor = contexto(facultades=("liquidacion.revisar",), modo=ModoOperacion.AUTOGESTION,
                         persona=None)  # fmt: skip
    d = evaluar("liquidacion.revisar", sin_actor, TODO_PASA)
    assert d.motivo_interno == "identidad_indeterminada"
    con_actor = contexto(facultades=("liquidacion.revisar",), modo=ModoOperacion.AUTOGESTION)
    assert evaluar("liquidacion.revisar", con_actor, TODO_PASA).permitido


def test_respaldo_ausente_niega_y_control_presente_pasa():
    ctx = contexto(facultades=("decisiones.registrar",))
    assert evaluar("decisiones.registrar", ctx, Entidad()).motivo_interno == "sin_respaldo"
    assert evaluar("decisiones.registrar", ctx, Entidad(respaldo_presente=True)).permitido


# --- Políticas: fuente normativa, catálogo y carga ------------------------------------------


def test_toda_politica_tiene_fuente_normativa():
    assert all(p.fuente_normativa.strip() for p in POLITICAS.values())
    datos = POLITICAS["padron.ver"].model_dump(mode="json")
    del datos["fuente_normativa"]
    with pytest.raises(ValidationError, match="fuente_normativa"):
        Politica.model_validate(datos)
    with pytest.raises(ValidationError, match="fuente_normativa"):
        Politica.model_validate({**datos, "fuente_normativa": " "})


def test_las_politicas_usan_el_catalogo_de_d2_y_su_marca_economica():
    for accion, p in POLITICAS.items():
        if p.facultad is not None:
            assert p.facultad in CATALOGO, accion
            assert p.economica == CATALOGO[p.facultad], accion
    # Las 21 facultades del catálogo tienen alguna acción que las usa.
    assert {p.facultad for p in POLITICAS.values()} - {None} == set(CATALOGO)


def test_las_acciones_de_la_tabla():
    assert len(POLITICAS) == 34  # 5 de §2.1, 24 de §2.2 y las 5 de A7 (2b-4, PR 4)


def _archivo(tmp_path, nombre, politicas):
    ruta = tmp_path / nombre
    ruta.write_text(json.dumps({"politicas": politicas}), encoding="utf-8")
    return ruta


def test_carga_falla_con_acciones_duplicadas(tmp_path):
    p = POLITICAS["padron.ver"].model_dump(mode="json")
    a = _archivo(tmp_path, "a.json", [p])
    b = _archivo(tmp_path, "b.json", [p])
    assert set(cargar([a])) == {"padron.ver"}  # control
    with pytest.raises(PoliticasInvalidas, match="duplicada"):
        cargar([a, b])


def test_carga_falla_con_una_politica_invalida(tmp_path):
    p = POLITICAS["padron.ver"].model_dump(mode="json")
    mala = _archivo(tmp_path, "mala.json", [{**p, "modo": ["consejo"]}])
    with pytest.raises(PoliticasInvalidas, match="mala.json"):
        cargar([mala])
    roto = tmp_path / "roto.json"
    roto.write_text("{", encoding="utf-8")
    with pytest.raises(PoliticasInvalidas, match="roto.json"):
        cargar([roto])
    with pytest.raises(PoliticasInvalidas):
        cargar([])
