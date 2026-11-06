"""Qué se le dice a cada persona de una orden (`app/consorcios/circuito_pagos.py`), sin base.

Diseño: `Rediseno_Circuito_Pagos_2026-11-06.md`, §2 y §3. Cada caso lleva su control: lo mismo sin
el dato que lo decide da otra cosa. Contra PostgreSQL real: `test_circuito_pagos_0072.py`.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from cem_contracts.egresos import (
    GRUPO_DE_PASO,
    CondicionBeneficiario,
    ConsultaPagos,
    EstadoOrdenPago,
    FichaPago,
    Franja,
    GrupoPago,
    LeTocaA,
    OrdenPago,
    PasoPago,
    SituacionPago,
)

from app.config import PAGOS_DIAS_BANDEJA, cargar
from app.consorcios.circuito_pagos import (
    SIN_ACCIONES,
    SITUACION_POR_ESTADO,
    AccionesOrden,
    HechosPago,
    bandeja,
    le_toca_a,
    paso_de,
    situacion_de,
)

E = EstadoOrdenPago
S = SituacionPago
P = PasoPago
AHORA = datetime(2026, 11, 6, 12, tzinfo=UTC)
HACE_UNA_SEMANA = AHORA - timedelta(days=7)


# Situación: un caso por fila de §3 -------------------------------------------------------------


@pytest.mark.parametrize(
    ("estado", "hechos", "esperada"),
    [
        (E.APROBADA, HechosPago(), S.PARA_PAGAR),
        (E.EN_EJECUCION, HechosPago(manual=True), S.PAGANDO),
        (E.EN_EJECUCION, HechosPago(manual=True, declaracion_vigente=True), S.NO_PAGO_DECLARADO),
        (
            E.EN_VERIFICACION,
            HechosPago(manual=True, pago_observado=True, declaracion_vigente=True),
            S.NO_PAGO_DECLARADO,
        ),
        (E.EN_VERIFICACION, HechosPago(manual=True, pago_sin_conciliar=True), S.FALTA_CONCILIAR),
        (E.EN_VERIFICACION, HechosPago(manual=True, pago_observado=True), S.OBSERVADO),
        (E.FALLIDA, HechosPago(), S.NO_SE_PAGO),
        (E.BORRADOR, HechosPago(), S.BORRADOR),
        (E.EN_REVISION, HechosPago(), S.EN_REVISION),
        (E.RECHAZADA, HechosPago(), S.RECHAZADA),
        (E.PAGADA, HechosPago(), S.PAGADA),
        (E.CONCILIADA, HechosPago(), S.PAGADA),
    ],
)
def test_situacion(estado, hechos, esperada):
    assert situacion_de(estado, hechos) == esperada


def test_todo_estado_tiene_situacion():
    assert set(SITUACION_POR_ESTADO) | {E.EN_EJECUCION, E.EN_VERIFICACION} == set(E)


def test_una_orden_cerrada_no_mira_los_hechos():
    """Una orden pagada conserva sus hechos viejos: el estado manda."""
    assert situacion_de(E.FALLIDA, HechosPago(pago_observado=True)) == S.NO_SE_PAGO


def test_lo_de_la_fase_4_esta_en_los_enums_y_nada_lo_produce():
    assert {P.RESOLVER_DIFERENCIA, P.ESPERA_RESOLVER} <= set(P)
    assert {S.CON_DIFERENCIA, S.RECLAMO} <= set(S)
    posibles = [situacion_de(e, h) for e in E for h in (
        HechosPago(), HechosPago(declaracion_vigente=True), HechosPago(pago_sin_conciliar=True),
        HechosPago(pago_observado=True),
    )]  # fmt: skip
    assert not {S.CON_DIFERENCIA, S.RECLAMO} & set(posibles)


# Paso: por cada uno, un caso que aparece y un control que no -----------------------------------


@pytest.mark.parametrize(
    ("situacion", "acciones", "prepara", "esperado", "control"),
    [
        (S.BORRADOR, SIN_ACCIONES, True, P.ENVIAR, None),
        (S.PARA_PAGAR, AccionesOrden(puede_pagar=True), False, P.PAGAR, None),
        (S.PAGANDO, AccionesOrden(puede_pagar=True), False, P.PAGAR, None),
        (
            S.PAGANDO,
            AccionesOrden(puede_pagar=True, puede_subir_comprobante=True),
            False,
            P.SUBIR_COMPROBANTE,
            P.PAGAR,
        ),
        (S.PARA_PAGAR, AccionesOrden(puede_renovar=True), True, P.RENOVAR, None),
        (S.EN_REVISION, AccionesOrden(puede_aprobar=True), False, P.APROBAR, P.ESPERA_APROBAR),
        (
            S.FALTA_CONCILIAR,
            AccionesOrden(puede_conciliar=True),
            False,
            P.CONCILIAR,
            P.ESPERA_CONCILIAR,
        ),
        (S.OBSERVADO, AccionesOrden(puede_corregir=True), True, P.CORREGIR, P.ESPERA_CORREGIR),
        (
            S.NO_PAGO_DECLARADO,
            AccionesOrden(puede_confirmar=True),
            False,
            P.CONFIRMAR_NO_PAGO,
            P.ESPERA_CONFIRMAR,
        ),
    ],
)
def test_paso_con_y_sin_lo_que_lo_habilita(situacion, acciones, prepara, esperado, control):
    assert paso_de(situacion, acciones, prepara) == esperado
    sin = AccionesOrden(puede_pagar=True) if esperado == P.SUBIR_COMPROBANTE else SIN_ACCIONES
    if esperado in (P.ENVIAR, P.RENOVAR):
        assert paso_de(situacion, SIN_ACCIONES, False) is None
    else:
        assert paso_de(situacion, sin, prepara) == control


@pytest.mark.parametrize(
    ("situacion", "esperado"),
    [(S.BORRADOR, P.ENVIAR), (S.RECHAZADA, P.RECHAZADA), (S.NO_SE_PAGO, P.VOLVER_A_PAGAR)],
)
def test_lo_de_quien_prepara_no_lo_ve_el_consejo(situacion, esperado):
    assert paso_de(situacion, SIN_ACCIONES, True) == esperado
    assert paso_de(situacion, SIN_ACCIONES, False) is None


def test_con_una_declaracion_vigente_quien_falta_declarar_no_recibe_pagar():
    falta = AccionesOrden(
        puede_pagar=True, puede_subir_comprobante=True, falta_que_declares=True
    )  # fmt: skip
    assert paso_de(S.NO_PAGO_DECLARADO, falta, True) == P.SUBIR_COMPROBANTE
    # Control: quien ya declaró espera, aunque la base le aceptaría otra cosa.
    ya = AccionesOrden(puede_pagar=True, puede_subir_comprobante=True)
    assert paso_de(S.NO_PAGO_DECLARADO, ya, True) == P.ESPERA_CONFIRMAR


def test_un_pago_observado_no_se_vuelve_a_pagar():
    a = AccionesOrden(puede_pagar=True, puede_subir_comprobante=True)
    assert paso_de(S.OBSERVADO, a, True) == P.ESPERA_CORREGIR


def test_lo_pagado_no_tiene_paso():
    assert paso_de(S.PAGADA, SIN_ACCIONES, True) is None


# A quién le toca y grupos ----------------------------------------------------------------------


def test_todo_paso_tiene_grupo():
    assert set(GRUPO_DE_PASO) == set(P)
    assert {p for p, g in GRUPO_DE_PASO.items() if g == GrupoPago.ESPERANDO} == {
        p for p in P if p.value.startswith("espera_")
    }


@pytest.mark.parametrize(
    ("paso", "hechos", "esperado"),
    [
        (P.PAGAR, HechosPago(), LeTocaA.VOS),
        (P.ESPERA_APROBAR, HechosPago(), LeTocaA.CONSEJO),
        (P.ESPERA_CONCILIAR, HechosPago(), LeTocaA.CONSEJO),
        (P.ESPERA_CORREGIR, HechosPago(), LeTocaA.QUIEN_REGISTRO),
        (P.ESPERA_CONFIRMAR, HechosPago(declaracion_confirmable=True), LeTocaA.CONSEJO),
        (P.ESPERA_CONFIRMAR, HechosPago(), LeTocaA.QUIEN_VIO_LA_CUENTA),
    ],
)
def test_le_toca_a(paso, hechos, esperado):
    assert le_toca_a(paso, hechos) == esperado


# Bandeja ---------------------------------------------------------------------------------------


def orden(estado: EstadoOrdenPago, actualizada_en: datetime = AHORA) -> OrdenPago:
    return OrdenPago(
        id=uuid4(), estado=estado, version_vigente=1, preparada_por=uuid4(), creada_en=AHORA,
        actualizada_en=actualizada_en, motivo_rechazo=None, importe_centavos=100, moneda="ARS",
        concepto="SINTETICO", proveedor_id=uuid4(), razon_social="Proveedor SINTETICO",
        condicion_beneficiario=CondicionBeneficiario.NUEVO, usa_fondo_reserva=False,
        aprobaciones_requeridas=1, aprobaciones=0, franja=Franja.BENEFICIARIO_NUEVO,
        alerta_fraccionamiento=False, aprobada_en=None, aprobacion_vence_en=None,
        aprobada_por_vos=False,
    )  # fmt: skip


def test_lo_rechazado_queda_los_dias_de_la_bandeja_y_despues_sale():
    reciente = orden(E.RECHAZADA, HACE_UNA_SEMANA + timedelta(seconds=1))
    vieja = orden(E.RECHAZADA, HACE_UNA_SEMANA - timedelta(seconds=1))
    elementos = bandeja([reciente, vieja], {}, True, HACE_UNA_SEMANA)
    assert [x.orden_id for x in elementos] == [reciente.id]


def test_lo_pagado_sale_y_sin_nada_pendiente_no_hay_elementos():
    assert bandeja([orden(E.PAGADA), orden(E.CONCILIADA)], {}, True, HACE_UNA_SEMANA) == []
    assert bandeja([], {}, True, HACE_UNA_SEMANA) == []


def test_va_por_grupo_y_de_lo_mas_viejo_a_lo_mas_nuevo():
    espera = orden(E.EN_REVISION, AHORA - timedelta(days=9))
    nueva = orden(E.APROBADA, AHORA)
    vieja = orden(E.APROBADA, AHORA - timedelta(days=1))
    pagar = AccionesOrden(puede_pagar=True)
    elementos = bandeja(
        [espera, nueva, vieja], {nueva.id: pagar, vieja.id: pagar}, True, HACE_UNA_SEMANA
    )
    assert [x.orden_id for x in elementos] == [vieja.id, nueva.id, espera.id]
    assert [x.grupo for x in elementos] == [
        GrupoPago.PARA_PAGAR, GrupoPago.PARA_PAGAR, GrupoPago.ESPERANDO,
    ]  # fmt: skip


def test_una_orden_sin_acciones_de_la_base_no_recibe_ningun_acto():
    """Lo que la base no contestó no se presume: queda sin pagar y sin aprobar."""
    assert bandeja([orden(E.APROBADA)], {}, True, HACE_UNA_SEMANA) == []
    [x] = bandeja([orden(E.EN_REVISION)], {}, True, HACE_UNA_SEMANA)
    assert x.paso == P.ESPERA_APROBAR


# La cuenta completa no está en el contrato -----------------------------------------------------


def _propiedades(esquema) -> set[str]:
    if isinstance(esquema, dict):
        propias = set(esquema.get("properties", {}))
        return propias.union(*(_propiedades(v) for v in esquema.values()))
    if isinstance(esquema, list):
        return set().union(*(_propiedades(v) for v in esquema))
    return set()


@pytest.mark.parametrize("modelo", [FichaPago, ConsultaPagos])
def test_el_contrato_no_tiene_lugar_para_la_cuenta_completa(modelo):
    propiedades = _propiedades(modelo.model_json_schema())
    assert "cuenta_destino" not in propiedades
    assert "orden_id" in propiedades or "orden" in propiedades  # control: la búsqueda ve campos


# La variable -----------------------------------------------------------------------------------


def test_los_dias_de_la_bandeja_salen_de_la_variable(monkeypatch):
    monkeypatch.delenv("CEM_PAGOS_DIAS_BANDEJA", raising=False)
    assert cargar().pagos_dias_bandeja == PAGOS_DIAS_BANDEJA == 7
    monkeypatch.setenv("CEM_PAGOS_DIAS_BANDEJA", "3")
    assert cargar().pagos_dias_bandeja == 3


@pytest.mark.parametrize("valor", ["siete", "", "-1"])
def test_un_valor_mal_escrito_impide_el_arranque(monkeypatch, valor):
    monkeypatch.setenv("CEM_PAGOS_DIAS_BANDEJA", valor)
    with pytest.raises(ValueError):
        cargar()
