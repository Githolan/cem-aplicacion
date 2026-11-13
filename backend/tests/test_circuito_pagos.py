"""Qué se le dice a cada persona de una orden (`app/consorcios/circuito_pagos.py`), sin base.

Diseño: `Rediseno_Circuito_Pagos_2026-11-06.md`, §2 y §3. Cada caso lleva su control: lo mismo sin
el dato que lo decide da otra cosa. Contra PostgreSQL real: `test_circuito_pagos_0072.py`.
"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from cem_contracts.egresos import (
    AccionPago,
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
    TipoAccion,
)
from cem_contracts.pantalla_pagos import cargar as tabla

from app.config import (
    PAGOS_DIAS_BANDEJA,
    PAGOS_DIAS_BORRADOR_VIEJO,
    PAGOS_DIAS_REPETIDA,
    cargar,
)
from app.consorcios.circuito_pagos import (
    MODIFICABLES,
    SIN_ACCIONES,
    SITUACION_POR_ESTADO,
    AccionesOrden,
    HechosPago,
    Plazos,
    acciones_ofrecidas,
    avisos_de_rechazo,
    bandeja,
    barra_de,
    fila_de,
    le_toca_a,
    ofrece_pagar,
    parecidas,
    paso_de,
    reabre_cuenta,
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


def test_con_la_cuenta_ya_vista_otro_ejecutor_espera_el_comprobante():
    a = AccionesOrden(puede_pagar=True, cuenta_vista=True)
    assert paso_de(S.PAGANDO, a, True) == P.ESPERA_COMPROBANTE
    assert ofrece_pagar(S.PAGANDO, a) is False
    assert le_toca_a(P.ESPERA_COMPROBANTE, a) == LeTocaA.QUIEN_VIO_LA_CUENTA
    # Control: sin nadie que la haya visto, paga.
    b = AccionesOrden(puede_pagar=True)
    assert paso_de(S.PAGANDO, b, True) == P.PAGAR
    assert ofrece_pagar(S.PAGANDO, b) is True


def test_un_pago_sin_salida_lo_ve_quien_declara_y_el_resto_espera():
    trabado = AccionesOrden(pago_sin_salida=True)
    assert paso_de(S.PAGANDO, trabado, False) == P.ESPERA_DECLARAR
    assert le_toca_a(P.ESPERA_DECLARAR, trabado) == LeTocaA.QUIEN_EJECUTA
    puede = AccionesOrden(pago_sin_salida=True, puede_declarar=True)
    assert paso_de(S.PAGANDO, puede, True) == P.DECLARAR_NO_PAGO
    # Control: poder declarar no alcanza; sin estar trabada, a quien no paga no le aparece.
    assert paso_de(S.PAGANDO, AccionesOrden(puede_declarar=True), True) is None


def test_un_pago_observado_no_se_vuelve_a_pagar():
    a = AccionesOrden(puede_pagar=True, puede_subir_comprobante=True)
    assert paso_de(S.OBSERVADO, a, True) == P.ESPERA_CORREGIR


def test_lo_pagado_no_tiene_paso():
    assert paso_de(S.PAGADA, SIN_ACCIONES, True) is None


# A quién le toca y grupos ----------------------------------------------------------------------


def test_todo_paso_tiene_su_fila_y_lo_que_espera_va_plegado():
    filas = tabla().filas
    assert {f.paso for f in filas} == set(P) | {None}
    assert {f.paso for f in filas if f.lista == GrupoPago.ESPERANDO} == {
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


PROVEEDOR = uuid4()
PLAZOS = Plazos(
    cerradas_desde=HACE_UNA_SEMANA, viejos_desde=HACE_UNA_SEMANA, dias_repetida=PAGOS_DIAS_REPETIDA
)


def orden(
    estado: EstadoOrdenPago,
    actualizada_en: datetime = AHORA,
    *,
    creada_en: datetime = AHORA,
    proveedor=None,
    importe: int = 100,
    motivo_rechazo: str | None = None,
    aprobacion_vence_en: datetime | None = None,
) -> OrdenPago:
    return OrdenPago(
        id=uuid4(), estado=estado, version_vigente=1, preparada_por=uuid4(), creada_en=creada_en,
        actualizada_en=actualizada_en, motivo_rechazo=motivo_rechazo, importe_centavos=importe,
        moneda="ARS", concepto="SINTETICO", proveedor_id=proveedor or uuid4(),
        razon_social="Proveedor SINTETICO",
        condicion_beneficiario=CondicionBeneficiario.NUEVO, usa_fondo_reserva=False,
        aprobaciones_requeridas=1, aprobaciones=0, franja=Franja.BENEFICIARIO_NUEVO,
        alerta_fraccionamiento=False, aprobada_en=None, aprobacion_vence_en=aprobacion_vence_en,
        aprobada_por_vos=False,
    )  # fmt: skip


def test_lo_rechazado_no_esta_en_la_bandeja_ni_cuenta_como_pendiente():
    """P-03, decisión 3: hoy queda 7 días en «Para preparar» sin nada que hacer."""
    reciente = orden(E.RECHAZADA, HACE_UNA_SEMANA + timedelta(seconds=1))
    assert bandeja([reciente], {}, True, PLAZOS) == []
    # Control: un borrador del mismo momento sí está, y cuenta.
    [borrador] = bandeja([orden(E.BORRADOR, AHORA)], {}, True, PLAZOS)
    assert borrador.grupo == GrupoPago.PARA_PREPARAR and borrador.pendiente


def test_lo_pagado_de_menos_queda_los_dias_de_la_bandeja_y_despues_sale():
    falta = AccionesOrden(pagada_de_menos=True)
    reciente = orden(E.PAGADA, HACE_UNA_SEMANA + timedelta(seconds=1))
    vieja = orden(E.PAGADA, HACE_UNA_SEMANA - timedelta(seconds=1))
    elementos = bandeja([reciente, vieja], {reciente.id: falta, vieja.id: falta}, True, PLAZOS)
    assert [x.orden_id for x in elementos] == [reciente.id]


def test_lo_pagado_sale_y_sin_nada_pendiente_no_hay_elementos():
    assert bandeja([orden(E.PAGADA), orden(E.CONCILIADA)], {}, True, PLAZOS) == []
    assert bandeja([], {}, True, PLAZOS) == []


def test_va_por_grupo_y_de_lo_mas_viejo_a_lo_mas_nuevo():
    espera = orden(E.EN_REVISION, AHORA - timedelta(days=9))
    nueva = orden(E.APROBADA, AHORA)
    vieja = orden(E.APROBADA, AHORA - timedelta(days=1))
    pagar = AccionesOrden(puede_pagar=True)
    elementos = bandeja([espera, nueva, vieja], {nueva.id: pagar, vieja.id: pagar}, True, PLAZOS)
    assert [x.orden_id for x in elementos] == [vieja.id, nueva.id, espera.id]
    assert [x.grupo for x in elementos] == [
        GrupoPago.PARA_PAGAR, GrupoPago.PARA_PAGAR, GrupoPago.ESPERANDO,
    ]  # fmt: skip


def test_una_orden_sin_acciones_de_la_base_no_recibe_ningun_acto():
    """Lo que la base no contestó no se presume: queda sin pagar y sin aprobar."""
    assert bandeja([orden(E.APROBADA)], {}, True, PLAZOS) == []
    [x] = bandeja([orden(E.EN_REVISION)], {}, True, PLAZOS)
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


# La ficha: reabrir la cuenta y modificar -------------------------------------------------------


def test_reabre_la_cuenta_quien_la_vio_y_todavia_se_puede_abrir():
    vio = AccionesOrden(cuenta_vista=True, puede_pagar=True, puede_registrar=True)
    assert reabre_cuenta(S.PAGANDO, vio) is True
    # Controles: otro ejecutor, y quien la vio con la aprobación vencida.
    assert reabre_cuenta(S.PAGANDO, replace(vio, puede_registrar=False)) is False
    assert reabre_cuenta(S.PAGANDO, replace(vio, puede_pagar=False)) is False


def test_un_registro_observado_se_corrige_sin_reabrir_la_cuenta():
    a = AccionesOrden(cuenta_vista=True, puede_pagar=True, puede_registrar=True)
    assert reabre_cuenta(S.OBSERVADO, a) is False


def test_retomar_no_abre_la_cuenta_si_otra_persona_la_vio_sin_declarar():
    base = AccionesOrden(declaracion_vigente=True, puede_pagar=True)
    assert reabre_cuenta(S.NO_PAGO_DECLARADO, base) is False
    assert reabre_cuenta(S.NO_PAGO_DECLARADO, replace(base, declaracion_confirmable=True)) is True
    assert reabre_cuenta(S.NO_PAGO_DECLARADO, replace(base, falta_que_declares=True)) is True


def test_se_modifica_lo_que_acepta_la_base():
    assert {E.BORRADOR, E.EN_REVISION, E.APROBADA, E.FALLIDA} == MODIFICABLES
    assert E.EN_EJECUCION not in MODIFICABLES  # control


# Tanda 2 del informe de fricción: la tabla de la pantalla manda ------------------------------


def test_la_fila_de_la_bandeja_trae_estado_fecha_y_la_principal_de_la_tabla():
    """P-02 y P-09: la fila dice la fecha y el estado, y su verbo es la acción principal."""
    o = orden(E.EN_REVISION, creada_en=AHORA - timedelta(days=3))
    [x] = bandeja([o], {o.id: AccionesOrden(puede_aprobar=True)}, False, PLAZOS)
    assert x.creada_en == o.creada_en and x.actualizada_en == o.actualizada_en
    assert x.estado_visible == "esperando_aprobacion" and x.situacion == S.EN_REVISION
    assert x.principal == AccionPago.APROBAR and x.pendiente
    # Control: a quien espera no le toca nada.
    [y] = bandeja([o], {}, True, PLAZOS)
    assert y.principal is None and not y.pendiente


def test_un_borrador_sin_movimiento_pasa_a_borradores_viejos_y_no_cuenta():
    """Decisión 4: a los 7 días sin movimiento, plegado y fuera de los pendientes."""
    viejo = orden(E.BORRADOR, HACE_UNA_SEMANA - timedelta(seconds=1))
    nuevo = orden(E.BORRADOR, HACE_UNA_SEMANA + timedelta(seconds=1))
    elementos = {x.orden_id: x for x in bandeja([viejo, nuevo], {}, True, PLAZOS)}
    assert elementos[viejo.id].grupo == GrupoPago.BORRADORES_VIEJOS
    assert not elementos[viejo.id].pendiente
    assert elementos[nuevo.id].grupo == GrupoPago.PARA_PREPARAR  # control
    assert elementos[nuevo.id].pendiente
    # Lo viejo va después de todo lo demás.
    assert [x.orden_id for x in bandeja([viejo, nuevo], {}, True, PLAZOS)] == [nuevo.id, viejo.id]


def test_los_dias_del_borrador_viejo_y_de_la_repetida_salen_de_la_variable(monkeypatch):
    monkeypatch.delenv("CEM_PAGOS_DIAS_BORRADOR_VIEJO", raising=False)
    monkeypatch.delenv("CEM_PAGOS_DIAS_REPETIDA", raising=False)
    assert cargar().pagos_dias_borrador_viejo == PAGOS_DIAS_BORRADOR_VIEJO == 7
    assert cargar().pagos_dias_repetida == PAGOS_DIAS_REPETIDA == 30
    monkeypatch.setenv("CEM_PAGOS_DIAS_BORRADOR_VIEJO", "3")
    monkeypatch.setenv("CEM_PAGOS_DIAS_REPETIDA", "10")
    assert cargar().pagos_dias_borrador_viejo == 3
    assert cargar().pagos_dias_repetida == 10
    monkeypatch.setenv("CEM_PAGOS_DIAS_REPETIDA", "-1")
    with pytest.raises(ValueError):
        cargar()


def test_aviso_de_orden_repetida_con_la_fecha_y_el_estado_de_la_otra():
    """Decisión 5: mismo proveedor y mismo importe en 30 días; no bloquea nada."""
    p = uuid4()
    pagada = orden(E.PAGADA, creada_en=AHORA - timedelta(days=3), proveedor=p, importe=15_000_000)
    copia = orden(E.BORRADOR, creada_en=AHORA, proveedor=p, importe=15_000_000)
    situaciones = {pagada.id: S.PAGADA, copia.id: S.BORRADOR}
    r = parecidas([pagada, copia], situaciones, PAGOS_DIAS_REPETIDA)
    assert r[copia.id].orden_id == pagada.id
    assert r[copia.id].creada_en == pagada.creada_en and r[copia.id].estado_visible == "pagada"
    assert r[pagada.id].orden_id == copia.id
    [x] = bandeja([pagada, copia], {}, True, PLAZOS)
    assert x.orden_id == copia.id and x.posible_duplicada_de.orden_id == pagada.id


@pytest.mark.parametrize(
    "cambio",
    [
        {"importe": 15_000_001},
        {"proveedor": "otro"},
        {"creada_en": AHORA - timedelta(days=PAGOS_DIAS_REPETIDA, seconds=1)},
        {"estado": E.RECHAZADA},
        {"estado": E.RETIRADA},
    ],
)
def test_control_no_avisa_si_cambia_lo_que_la_hace_igual(cambio):
    p = uuid4()
    otra = dict(proveedor=p, importe=15_000_000, creada_en=AHORA - timedelta(days=3))
    cambio = dict(cambio)
    estado = cambio.pop("estado", E.PAGADA)
    if cambio.get("proveedor") == "otro":
        cambio["proveedor"] = uuid4()
    otra.update(cambio)
    previa = orden(estado, **otra)
    copia = orden(E.BORRADOR, creada_en=AHORA, proveedor=p, importe=15_000_000)
    situaciones = {previa.id: situacion_de(estado, HechosPago()), copia.id: S.BORRADOR}
    assert parecidas([previa, copia], situaciones, PAGOS_DIAS_REPETIDA) == {}


def test_la_rechazada_se_avisa_a_quien_la_preparo_hasta_entendido():
    """Decisión 3: aviso arriba de la bandeja, no en una lista de pendientes."""
    o = orden(E.RECHAZADA, motivo_rechazo="Falta la factura SINTETICA")
    [aviso] = avisos_de_rechazo([o], True, {o.id})
    assert aviso.orden_id == o.id and aviso.motivo == "Falta la factura SINTETICA"
    assert aviso.rechazada_en == o.actualizada_en
    assert avisos_de_rechazo([o], True, set()) == []  # ya la vio, o no la preparó
    assert avisos_de_rechazo([o], False, {o.id}) == []  # quien no prepara no recibe avisos
    assert avisos_de_rechazo([orden(E.BORRADOR)], True, {o.id}) == []  # control: solo rechazadas


def test_pagada_y_cotejada_completa_la_barra_y_no_tiene_paso():
    """P-05: hoy «Cotejar» no se marca nunca."""
    cotejada = HechosPago(cotejada=True)
    assert situacion_de(E.PAGADA, cotejada) == S.PAGADA_Y_COTEJADA
    assert situacion_de(E.CONCILIADA, cotejada) == S.PAGADA_Y_COTEJADA
    assert situacion_de(E.PAGADA, HechosPago()) == S.PAGADA  # control
    o = orden(E.PAGADA)
    assert barra_de(S.PAGADA_Y_COTEJADA, o, AHORA).hechos == 5
    assert barra_de(S.PAGADA_Y_COTEJADA, o, AHORA).actual is None
    assert barra_de(S.PAGADA, o, AHORA).hechos == 4
    assert barra_de(S.PAGADA, o, AHORA).actual == "ahora"
    falta = AccionesOrden(pagada_de_menos=True, cotejada=True)
    assert paso_de(S.PAGADA_Y_COTEJADA, falta, True) is None


def test_la_barra_es_de_la_orden_y_avisa_la_aprobacion_vencida():
    vencida = orden(E.APROBADA, aprobacion_vence_en=AHORA - timedelta(seconds=1))
    vigente = orden(E.APROBADA, aprobacion_vence_en=AHORA + timedelta(days=1))
    assert barra_de(S.PARA_PAGAR, vencida, AHORA).actual == "alerta"
    assert barra_de(S.PARA_PAGAR, vigente, AHORA).actual == "ahora"
    assert barra_de(S.BORRADOR, vigente, AHORA) != barra_de(S.EN_REVISION, vigente, AHORA)
    assert barra_de(S.RETIRADA, vigente, AHORA) is None


def test_no_se_pago_cerrada_sale_de_la_bandeja():
    """Decisión 12: cerrada sin pagar, ya no se vuelve a pagar ni queda pendiente."""
    o = orden(E.FALLIDA)
    assert paso_de(S.NO_SE_PAGO, HechosPago(cerrada_sin_pago=True), True) is None
    assert bandeja([o], {o.id: AccionesOrden(cerrada_sin_pago=True)}, True, PLAZOS) == []
    [x] = bandeja([o], {}, True, PLAZOS)  # control: sin cerrar, sigue para volver a pagar
    assert x.paso == P.VOLVER_A_PAGAR and x.principal == AccionPago.VOLVER_A_PAGAR


def test_la_tabla_no_ofrece_lo_que_la_base_niega():
    fila = fila_de(S.EN_REVISION, P.APROBAR)
    todo = {AccionPago.APROBAR: True, AccionPago.RECHAZAR: True, AccionPago.MODIFICAR: True}
    assert [(x.codigo, x.tipo) for x in acciones_ofrecidas(fila, todo)] == [
        (AccionPago.APROBAR, TipoAccion.PRINCIPAL),
        (AccionPago.MODIFICAR, TipoAccion.SECUNDARIA),
        (AccionPago.RECHAZAR, TipoAccion.NEGATIVA),
    ]
    sin_rechazar = {**todo, AccionPago.RECHAZAR: False}
    assert AccionPago.RECHAZAR not in {x.codigo for x in acciones_ofrecidas(fila, sin_rechazar)}
    assert acciones_ofrecidas(fila, {}) == []  # sin el sí de la base, nada


def test_si_el_comprobante_no_coincide_observar_pasa_a_principal():
    fila = fila_de(S.FALTA_CONCILIAR, P.CONCILIAR)
    puede = {AccionPago.CONCILIAR: True, AccionPago.OBSERVAR: True}
    normal = acciones_ofrecidas(fila, puede)
    assert normal[0].codigo == AccionPago.CONCILIAR and normal[0].tipo == TipoAccion.PRINCIPAL
    distinto = acciones_ofrecidas(fila, puede, comprobante_no_coincide=True)
    assert distinto[0].codigo == AccionPago.OBSERVAR and distinto[0].tipo == TipoAccion.PRINCIPAL
    assert sum(x.tipo == TipoAccion.PRINCIPAL for x in distinto) == 1


CAMPOS_DE_LA_BASE = [
    "puede_aprobar", "puede_pagar", "puede_renovar", "puede_registrar", "puede_declarar",
    "puede_subir_comprobante", "puede_corregir", "puede_conciliar", "puede_confirmar",
    "falta_que_declares", "pago_sin_salida", "puede_resolver_diferencia", "pago_de_otro",
    "puede_tomar", "proveedor_cambiado", "cuenta_vista", "pagada_de_menos", "cotejada",
    "cerrada_sin_pago", "declaracion_confirmable",
]  # fmt: skip


def _combinaciones():
    """Combinaciones de hasta tres sí de la base: la tabla tiene que conocer cada par que
    `paso_de` deriva (si no, la ficha daría un error)."""
    for i in range(2 ** len(CAMPOS_DE_LA_BASE)):
        if i.bit_count() > 3:
            continue
        yield AccionesOrden(**{c: bool(i >> n & 1) for n, c in enumerate(CAMPOS_DE_LA_BASE)})


def test_todo_par_que_deriva_el_backend_esta_en_la_tabla():
    vistos = set()
    for a in _combinaciones():
        for s in S:
            for prepara in (True, False):
                paso = paso_de(s, a, prepara)
                fila_de(s, paso)  # sin la fila, LookupError
                vistos.add((s, paso))
    # Control: el recorrido llega a toda la tabla.
    assert vistos == {(f.situacion, f.paso) for f in tabla().filas}
