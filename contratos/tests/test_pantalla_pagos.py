"""Las reglas de la tabla de la pantalla de pagos, sobre el archivo mismo (informe de fricción,
§4.2, reglas 1 a 5). Cada regla lleva su control: una tabla rota a propósito no pasa."""

import json
import re

import pytest
from pydantic import ValidationError

from cem_contracts.egresos import AccionPago, EstadoVisible, GrupoPago, PasoPago, SituacionPago
from cem_contracts.pantalla_pagos import ARCHIVO, EXCEPCIONES_REGLA_3, PantallaPagos, cargar

NEGATIVAS = {
    AccionPago.RETIRAR,
    AccionPago.RECHAZAR,
    AccionPago.OBSERVAR,
    AccionPago.DECLARAR_NO_PAGO,
    AccionPago.CERRAR_RECLAMO,
    AccionPago.CERRAR_SIN_PAGAR,
}
"""Las acciones que dejan algo sin hacer o lo cierran: se dibujan en rojo (regla 5)."""

PROHIBIDAS = {"versión", "franja", "huella", "riel", "umbral", "fraccionamiento"}
"""Palabras internas que no se muestran (P-29, informe de fricción §4.4)."""

SEGUNDA_PERSONA = {"vos", "te", "tu", "tus", "ti", "usted", "ustedes", "sos", "tenés", "podés"}
NO_SON_VOSEO = {"más", "además", "atrás", "jamás", "después", "demás", "detrás", "través"}
VOSEO = re.compile(r"\b\w+(?:ás|és)\b")


def _datos() -> dict:
    return json.loads(ARCHIVO.read_text(encoding="utf-8"))


def _rompe(cambio) -> None:
    datos = _datos()
    cambio(datos)
    with pytest.raises(ValidationError):
        PantallaPagos.model_validate(datos)


def _es_para_hacer(lista: GrupoPago | None) -> bool:
    return lista is not None and lista.value.startswith("para_")


def test_la_tabla_carga_y_esta_completa():
    tabla = cargar()
    assert set(tabla.situaciones) == set(SituacionPago)
    assert set(tabla.estados) == set(EstadoVisible)
    assert {f.situacion for f in tabla.filas} == set(SituacionPago)


def test_la_lista_unica_de_estados_de_la_decision_11():
    assert list(cargar().estados.values()) == [
        "Borrador",
        "Esperando aprobación",
        "Rechazada",
        "Retirada",
        "Lista para pagar",
        "Pagando",
        "Falta conciliar",
        "Observada",
        "No se pagó",
        "Pagada",
        "Pagada y cotejada",
    ]


def test_regla_1_a_lo_sumo_una_principal_y_ninguna_accion_dos_veces():
    for f in cargar().filas:
        for botones in (f, f.si_el_comprobante_no_coincide):
            if botones is None:
                continue
            todas = ([botones.principal] if botones.principal else []) + [
                *botones.secundarias,
                *botones.negativas,
            ]
            assert len(todas) == len(set(todas)), (f.situacion, f.paso)


def test_regla_1_control_dos_principales_no_entran():
    def dos(datos):
        datos["filas"][0]["principal"] = ["enviar", "modificar"]

    _rompe(dos)


def test_regla_2_nada_en_una_lista_de_hacer_sin_principal():
    tabla = cargar()
    for f in tabla.filas:
        assert _es_para_hacer(f.lista) == (f.principal is not None), (f.situacion, f.paso)
        if f.lista is not None:
            assert f.cuenta_como_pendiente == _es_para_hacer(f.lista)
    for g, lista in tabla.listas.items():
        assert lista.pendiente == _es_para_hacer(g), g
        assert lista.plegada == (not lista.pendiente), g


def test_regla_2_la_rechazada_no_esta_en_para_preparar():
    """P-03, decisión 3: hoy está 7 días en «Para preparar» sin nada que hacer."""
    tabla = cargar()
    rechazadas = [f for f in tabla.filas if f.situacion == SituacionPago.RECHAZADA]
    assert rechazadas and all(f.lista is None and not f.cuenta_como_pendiente for f in rechazadas)
    # La de quien la preparó se avisa una vez, con «Preparar otra orden».
    aviso = tabla.fila(SituacionPago.RECHAZADA, "rechazada")
    assert aviso.aviso_una_vez and aviso.secundarias == [AccionPago.PREPARAR_OTRA]


def test_regla_2_control_pendiente_distinto_de_su_lista_no_entra():
    def mal(datos):
        datos["filas"][0]["cuenta_como_pendiente"] = False

    _rompe(mal)


def test_regla_3_lo_terminado_no_tiene_principal_ni_lista():
    tabla = cargar()
    finales = {s for s, x in tabla.situaciones.items() if x.final}
    assert finales == {
        SituacionPago.RECHAZADA,
        SituacionPago.RETIRADA,
        SituacionPago.PAGADA_Y_COTEJADA,
    }
    excepciones = []
    for f in tabla.filas:
        if f.situacion in finales and f.excepcion_regla_3 is None:
            assert f.principal is None and f.lista is None, (f.situacion, f.paso)
        elif f.excepcion_regla_3 is not None:
            excepciones.append((f.situacion, f.paso, f.principal))
    # La única excepción, la de la decisión 2 del 13-11, y explícita.
    assert excepciones == [
        (SituacionPago.PAGADA_Y_COTEJADA, PasoPago.PREPARAR_FALTANTE, AccionPago.PREPARAR_FALTANTE)
    ]
    assert EXCEPCIONES_REGLA_3 == {(SituacionPago.PAGADA_Y_COTEJADA, PasoPago.PREPARAR_FALTANTE)}


def _fila(datos: dict, situacion: str, paso: str | None) -> dict:
    return next(f for f in datos["filas"] if f["situacion"] == situacion and f["paso"] == paso)


def test_regla_3_control_sin_la_marca_la_excepcion_no_entra():
    _rompe(lambda d: _fila(d, "pagada_y_cotejada", "preparar_faltante").pop("excepcion_regla_3"))


def test_regla_3_control_la_marca_no_abre_otra_fila_terminada():
    """No es un agujero general: marcar otra fila terminada no la deja tener acción principal."""

    def otra(d: dict) -> None:
        f = _fila(d, "rechazada", "rechazada")
        f["principal"] = "preparar_otra"
        f["secundarias"] = []
        f["excepcion_regla_3"] = "SINTETICO"

    _rompe(otra)


def test_regla_3_control_la_marca_sobra_en_una_fila_que_no_la_necesita():
    _rompe(lambda d: _fila(d, "pagada", "preparar_faltante").update(excepcion_regla_3="SINTETICO"))


def test_regla_3_control_una_terminada_con_principal_y_sin_marca_no_entra():
    def con_principal(d: dict) -> None:
        f = _fila(d, "retirada", None)
        f["principal"] = "preparar_otra"

    _rompe(con_principal)


def test_regla_4_estado_y_barra_son_de_la_situacion():
    """La fila no puede traer su propio estado ni su barra: no cambian con quien mira."""

    def barra_en_la_fila(datos):
        datos["filas"][0]["barra"] = {"hechos": 0, "actual": "ahora"}

    _rompe(barra_en_la_fila)
    barras = {s: x.barra for s, x in cargar().situaciones.items()}
    assert barras[SituacionPago.BORRADOR] != barras[SituacionPago.EN_REVISION]  # P-06
    assert barras[SituacionPago.PAGADA].hechos == 4  # falta cotejar (P-05)
    assert barras[SituacionPago.PAGADA].actual == "ahora"
    assert barras[SituacionPago.PAGADA_Y_COTEJADA].hechos == 5
    assert barras[SituacionPago.PAGADA_Y_COTEJADA].actual is None
    assert barras[SituacionPago.NO_SE_PAGO] is not None  # P-06: no desaparece


def test_regla_5_las_negativas_son_las_que_cierran_y_el_texto_es_impersonal():
    for f in cargar().filas:
        assert set(f.negativas) <= NEGATIVAS, (f.situacion, f.paso)
        assert not set(f.secundarias) & NEGATIVAS, (f.situacion, f.paso)
        palabras = set(re.findall(r"\w+", f.que_decir.lower()))
        assert not palabras & SEGUNDA_PERSONA, f.que_decir
        assert not palabras & PROHIBIDAS, f.que_decir
        assert not set(VOSEO.findall(f.que_decir.lower())) - NO_SON_VOSEO, f.que_decir


def test_regla_5_control_el_detector_de_voseo_ve_el_voseo():
    assert VOSEO.findall("revisá lo que tenés") == ["tenés"]
    assert set(re.findall(r"\w+", "te toca a vos")) & SEGUNDA_PERSONA


def test_nada_se_ofrece_que_no_sea_una_accion():
    def inventada(datos):
        datos["filas"][0]["secundarias"] = ["borrar"]

    _rompe(inventada)


def test_cada_par_aparece_una_vez():
    def repetida(datos):
        datos["filas"].append(dict(datos["filas"][0]))

    _rompe(repetida)
