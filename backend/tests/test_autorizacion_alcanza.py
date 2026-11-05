"""`alcanza`: lo que la interfaz muestra, sin entidad (documento 13, §10.5).

Cada negativo lleva su control: el mismo contexto sin lo que lo invalida alcanza. La tabla de
verdad entera sirve de red: lo que `evaluar` concede se alcanza siempre, y lo que queda fuera de
ámbito no se alcanza nunca.
"""

from dataclasses import replace
from datetime import timedelta

import pytest
from cem_contracts.estados import ModoOperacion, TipoVinculo
from cem_contracts.politicas import RecursoAceptado, TipoRecurso

from app.autorizacion import recursos
from app.autorizacion.evaluador import (
    CAUSA_ERROR_CARGA,
    ContextoAutorizacion,
    Entidad,
    Facultad,
    Vinculo,
    alcanza,
    evaluar,
)
from app.autorizacion.politicas import POLITICAS
from tests.escenarios_autorizacion import (
    CATALOGO,
    HOY,
    OTRO_CONSORCIO,
    SIMBOLOS,
    escenario,
    leer_tabla,
)
from tests.test_autorizacion_evaluador import (
    IDENTIDADES,
    MANDATO,
    OTRO,
    TODO_PASA,
    U3A,
    P,
    contexto,
    mandato,
)

ECONOMICA = "egresos.preparar_orden"
"""Por facultad, con recurso de cuenta o de grupo o general, solo en AA y AG, y sin `sin_modo`."""
AG = ModoOperacion.AUTOGESTION
SOLO_CONFIRMADOS = "coeficientes.ver_propia_unidad"
"""Por vínculo, con `misma_unidad` y `solo_confirmados`."""
CUENTA = f"cuenta:{SIMBOLOS['X']}"


def con_facultad(*recursos_: str | None, **kw) -> ContextoAutorizacion:
    """Mandato vigente con `ECONOMICA` sobre cada recurso, en un modo que la admite."""
    ctx = contexto(facultades=(), modo=kw.pop("modo", AG), **kw)
    return replace(ctx, facultades=tuple(Facultad(ECONOMICA, r, MANDATO) for r in recursos_))


def test_control_la_facultad_general_alcanza():
    assert alcanza(ECONOMICA, con_facultad(None))


# --- Ámbito y acción no declarada -----------------------------------------------------------


def test_sin_ambito_no_se_alcanza_nada_ni_lo_que_concede_el_ambito():
    assert not any(alcanza(a, contexto()) for a in POLITICAS)
    # Control: con un mandato vigente, lo concedido por ámbito se alcanza.
    assert alcanza("unidades.listar", contexto(facultades=()))


def test_una_accion_no_declarada_no_se_alcanza():
    assert not alcanza("unidades.borrar_todo", contexto(facultades=tuple(CATALOGO)))


def test_un_mandato_sin_facultades_alcanza_solo_lo_concedido_por_ambito():
    ctx = contexto(facultades=())
    esperadas = sorted(a for a, p in POLITICAS.items() if p.concede_por_ambito)
    assert sorted(a for a in POLITICAS if alcanza(a, ctx)) == esperadas


def test_modo_cambiar_no_lo_alcanza_nadie():
    assert not alcanza("modo.cambiar", contexto(vinculos=(P(),), facultades=tuple(CATALOGO)))


# --- Facultad: mandato y recurso ------------------------------------------------------------


def test_la_facultad_de_un_mandato_de_otro_consorcio_no_cuenta():
    # Con un vínculo, para que el ámbito no tape lo que se prueba.
    ajeno = con_facultad(None, vinculos=(P(),), consorcio_mandato=OTRO_CONSORCIO)
    assert alcanza("unidades.listar", ajeno) and not alcanza(ECONOMICA, ajeno)


@pytest.mark.parametrize(
    "cambio",
    [
        {"vigente_hasta": HOY},
        {"vigente_desde": HOY + timedelta(days=1)},
        {"causa_baja": CAUSA_ERROR_CARGA},
    ],
    ids=["vencido", "futuro", "error_carga"],
)
def test_la_facultad_de_un_mandato_que_no_vale_no_cuenta(cambio):
    ctx = con_facultad(None, vinculos=(P(),))
    assert alcanza(ECONOMICA, ctx)  # control
    assert not alcanza(ECONOMICA, replace(ctx, mandatos=(replace(mandato(), **cambio),)))


def test_el_ultimo_dia_de_vigencia_todavia_cuenta():
    vigente = replace(mandato(), vigente_hasta=HOY + timedelta(days=1))
    assert alcanza(ECONOMICA, replace(con_facultad(None), mandatos=(vigente,)))


def test_la_facultad_de_un_mandato_que_no_esta_en_el_contexto_no_cuenta():
    ctx = con_facultad(vinculos=(P(),))
    assert not alcanza(ECONOMICA, replace(ctx, facultades=(Facultad(ECONOMICA, None, OTRO),)))


def test_una_facultad_con_otro_codigo_no_cuenta():
    # Código exacto: `egresos.ejecutar_pago` no concede preparar.
    ctx = replace(con_facultad(), facultades=(Facultad("egresos.ejecutar_pago", None, MANDATO),))
    assert alcanza("egresos.ejecutar_pago", ctx) and not alcanza(ECONOMICA, ctx)


@pytest.mark.parametrize("recurso", [CUENTA, "grupo:agua"])
def test_un_recurso_bien_formado_de_tipo_aceptado_alcanza_sin_entidad(recurso):
    ctx = con_facultad(recurso)
    assert alcanza(ECONOMICA, ctx)
    # Control: `evaluar`, sobre otra entidad, niega; alcanzar no es poder.
    otra = Entidad(recursos={TipoRecurso.CUENTA: SIMBOLOS["Y"], TipoRecurso.GRUPO: "gas"})
    assert not evaluar(ECONOMICA, ctx, otra).permitido


@pytest.mark.parametrize(
    "recurso",
    [
        "",
        "cuenta",
        f"{CUENTA}:extra",
        f"cuenta:{SIMBOLOS['X_MAYUS']}",
        f"CUENTA:{SIMBOLOS['X']}",
        "cuenta:no-es-un-uuid",
        "grupo:Agua",
        f"{CUENTA}\n",
        f"unidad:{SIMBOLOS['3A']}",
        "piso:3",
    ],
)
def test_un_recurso_mal_formado_o_de_tipo_no_aceptado_no_cuenta(recurso):
    assert not alcanza(ECONOMICA, con_facultad(recurso))
    # Control: una mal formada no anula a otra válida.
    assert alcanza(ECONOMICA, con_facultad(recurso, CUENTA))


def test_la_facultad_general_no_cuenta_si_la_accion_no_la_admite():
    p = POLITICAS[ECONOMICA]
    recurso = RecursoAceptado(tipos=p.recurso.tipos, general=False)
    politicas = {ECONOMICA: p.model_copy(update={"recurso": recurso})}
    assert not alcanza(ECONOMICA, con_facultad(None), politicas)
    assert alcanza(ECONOMICA, con_facultad(CUENTA), politicas)  # control


def test_una_accion_sin_recurso_declarado_solo_admite_la_general():
    politicas = {ECONOMICA: POLITICAS[ECONOMICA].model_copy(update={"recurso": None})}
    assert not alcanza(ECONOMICA, con_facultad(CUENTA), politicas)
    assert alcanza(ECONOMICA, con_facultad(None), politicas)  # control


ACEPTADOS = [
    None,
    RecursoAceptado(tipos=(TipoRecurso.CUENTA,), general=True),
    RecursoAceptado(tipos=(TipoRecurso.CUENTA, TipoRecurso.GRUPO), general=False),
    RecursoAceptado(tipos=(TipoRecurso.UNIDAD,), general=False),
]
RECURSOS = [
    None, "", CUENTA, "grupo:agua", f"unidad:{SIMBOLOS['3A']}", "cuenta:mal", "grupo:Agua",
    f"{CUENTA}:x", "piso:3",
]  # fmt: skip


@pytest.mark.parametrize("aceptado", ACEPTADOS)
@pytest.mark.parametrize("recurso", RECURSOS)
def test_admisible_es_verificar_contra_la_entidad_que_coincide(recurso, aceptado):
    # Las dos funciones no pueden separarse: lo admisible es lo que `verificar` deja llegar hasta
    # la comparación con la entidad.
    parseado = recursos.parsear(recurso) if recurso else None
    entidad = {parseado.tipo: parseado.id} if parseado else {}
    assert recursos.admisible(recurso, aceptado) is (
        recursos.verificar(recurso, aceptado, entidad) is None
    )


# --- Vínculo --------------------------------------------------------------------------------


def test_un_vinculo_sin_confirmar_no_cuenta_si_la_politica_pide_confirmados():
    sin_confirmar = Vinculo(TipoVinculo.PROPIETARIO, U3A)
    assert not alcanza(SOLO_CONFIRMADOS, contexto(vinculos=(sin_confirmar,)))
    confirmado = replace(sin_confirmar, confirmado=True)
    assert alcanza(SOLO_CONFIRMADOS, contexto(vinculos=(confirmado,)))  # control


def test_un_vinculo_de_otro_tipo_no_cuenta():
    # `documentos.ver` concede a P, no a I.
    inquilino = Vinculo(TipoVinculo.INQUILINO, U3A)
    assert not alcanza("documentos.ver", contexto(vinculos=(inquilino,)))
    assert alcanza("documentos.ver", contexto(vinculos=(P(),)))  # control


def test_misma_unidad_no_se_mira_porque_alcanza_sobre_su_propia_unidad():
    ctx = contexto(vinculos=(Vinculo(TipoVinculo.PROPIETARIO, U3A, confirmado=True),))
    assert alcanza(SOLO_CONFIRMADOS, ctx)
    ajena = Entidad(recursos={TipoRecurso.UNIDAD: SIMBOLOS["5B"]})
    assert not evaluar(SOLO_CONFIRMADOS, ctx, ajena).permitido  # `evaluar` sí la mira


# --- Filtros --------------------------------------------------------------------------------


@pytest.mark.parametrize("modo", list(ModoOperacion))
def test_el_modo_filtra_como_en_evaluar(modo):
    ctx = con_facultad(None, modo=modo)
    assert alcanza(ECONOMICA, ctx) is (modo in POLITICAS[ECONOMICA].modos)


def test_el_modo_no_admitido_niega_con_la_facultad_en_la_mano():
    assert not alcanza(ECONOMICA, con_facultad(None, modo=ModoOperacion.CONSEJO))


def test_sin_modo_solo_alcanza_lo_que_corre_sin_modo():
    ctx = con_facultad(None, modo=None)
    assert not POLITICAS[ECONOMICA].sin_modo and not alcanza(ECONOMICA, ctx)
    assert POLITICAS["unidades.listar"].sin_modo and alcanza("unidades.listar", ctx)  # control


def test_una_economica_sin_identidad_verificada_no_se_alcanza():
    assert not alcanza(ECONOMICA, con_facultad(None, identidades={OTRO: IDENTIDADES[OTRO]}))
    assert not alcanza(ECONOMICA, con_facultad(None, identidades={}))


def test_sin_identidad_lo_que_no_es_economico_se_alcanza_igual():
    p = POLITICAS["fondo_reserva.solicitar"]
    assert not p.economica
    ctx = contexto(facultades=(p.facultad,), modo=p.modos[0], identidades={})
    assert alcanza("fondo_reserva.solicitar", ctx)


@pytest.mark.parametrize(
    "controles,motivo",
    [
        ({"segundo_factor_reciente": False}, "requiere_segundo_factor_reciente"),
        ({"sesion_reciente": False}, "requiere_sesion_reciente"),
    ],
)
def test_el_gesto_y_la_sesion_se_renuevan_y_no_quitan_nada(controles, motivo):
    ctx = con_facultad(None, **controles)
    assert alcanza(ECONOMICA, ctx)
    # Control: la ruta, en cambio, lo pide.
    assert evaluar(ECONOMICA, ctx, TODO_PASA).motivo_interno == motivo


def test_lo_que_depende_de_la_entidad_no_se_mira():
    # Sin entidad, `evaluar` niega por separación o por el dato que falta.
    p = POLITICAS["mandatos.alta"]
    assert p.separacion
    ctx = contexto(facultades=(p.facultad,), modo=p.modos[0])
    assert alcanza("mandatos.alta", ctx)
    assert not evaluar("mandatos.alta", ctx, Entidad()).permitido


# --- La tabla de verdad como red ------------------------------------------------------------

FILAS = leer_tabla()


@pytest.mark.parametrize(
    "fila", [f for f in FILAS if f.esperado == 200], ids=lambda f: f"{f.numero}-{f.accion}"
)
def test_lo_que_evaluar_concede_se_alcanza(fila):
    ctx, _ = escenario(fila)
    assert alcanza(fila.accion, ctx)


@pytest.mark.parametrize(
    "fila", [f for f in FILAS if f.esperado == 404], ids=lambda f: f"{f.numero}-{f.accion}"
)
def test_lo_que_queda_fuera_de_ambito_no_se_alcanza(fila):
    ctx, _ = escenario(fila)
    assert not alcanza(fila.accion, ctx)


def test_control_entre_los_403_hay_de_los_dos():
    # Control de las dos redes: hay 403 que se alcanzan (dependen de la entidad o del gesto) y 403
    # que no (sin concesión o modo no admitido). Una `alcanza` que dijera siempre lo mismo no pasa.
    assert {alcanza(f.accion, escenario(f)[0]) for f in FILAS if f.esperado == 403} == {True, False}
