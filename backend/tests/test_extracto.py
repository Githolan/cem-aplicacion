"""Leer un extracto y compararlo con los pagos, sin base. Datos SINTETICOS / NO REAL."""

import hashlib
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from cem_contracts.cotejo import ColumnasExtracto, SignoExtracto

from app.consorcios import extracto
from app.consorcios.extracto import (
    ExtractoInvalido,
    MovimientoGuardado,
    PagoComparable,
    comparar,
    leer_fecha,
    leer_importe,
    leer_movimientos,
    leer_numero,
    leer_tabla,
    muestra,
    signo_sugerido,
)

MAXIMO = 1000
COLUMNAS = ColumnasExtracto(fecha="Fecha", importe="Importe", numero_operacion="Referencia",
                            signo=SignoExtracto.CON_SIGNO)  # fmt: skip
DIA = date(2026, 11, 5)


@pytest.mark.parametrize(
    ("celda", "esperado"),
    [
        ("184350,00", "184350.00"),
        ("184.350,00", "184350.00"),
        ("1.184.350,5", "1184350.50"),
        ("184350.00", "184350.00"),
        ("184,350.00", "184350.00"),
        ("1.234", "1234.00"),
        ("12.34", "12.34"),
        ("-184.350,00", "-184350.00"),
        ("184.350,00-", "-184350.00"),
        ("(184.350,00)", "-184350.00"),
        ("$ 184.350,00", "184350.00"),
        ("-$ 184.350,00", "-184350.00"),
        ("$ -184.350,00", "-184350.00"),
        ("+1.000,00", "1000.00"),
        ("ARS 10,00", "10.00"),
    ],
)
def test_importes_que_se_leen(celda, esperado):
    assert leer_importe(celda) == Decimal(esperado)


@pytest.mark.parametrize(
    "celda",
    ["", "abc", "1,234,5", "12.345.67", "1.2345", "=1+1", "1e5", "NaN", "Infinity", "1,2,3",
     "99999999999999999,00", "--5", "٣٤"],
)  # fmt: skip
def test_lo_que_no_es_un_importe(celda):
    assert leer_importe(celda) is None


def test_un_importe_nunca_pasa_por_float():
    assert isinstance(leer_importe("0,10"), Decimal)
    assert int(leer_importe("1.000.000,07") * 100) == 100_000_007


@pytest.mark.parametrize(
    ("celda", "esperado"),
    [
        ("05/11/2026", DIA),
        ("5/11/2026", DIA),
        ("05-11-2026", DIA),
        ("05/11/26", DIA),
        ("2026-11-05", DIA),
        ("2026-11-05T10:00:00", DIA),
        ("05/11/2026 10:15", DIA),
    ],
)
def test_fechas_que_se_leen(celda, esperado):
    assert leer_fecha(celda) == esperado


@pytest.mark.parametrize("celda", ["", "31/02/2026", "11/2026", "hoy", "=TODAY()", "05/11/1899"])
def test_lo_que_no_es_una_fecha(celda):
    assert leer_fecha(celda) is None


@pytest.mark.parametrize(
    ("celda", "esperado"),
    [
        ("0048-213.377", "0048213377"),
        ("op 123a", "OP123A"),
        ("", None),
        ("123", None),
        ("=1+1234", None),
        ("@SUMA1234", None),
        ("+54911", None),
        ("9990000300000000000017", None),
        ("X" * 41, None),
    ],
)
def test_numero_de_operacion(celda, esperado):
    assert leer_numero(celda) == esperado


def test_acepta_utf8_con_marca_cp1252_y_utf16():
    texto = "Fecha;Descripción;Importe\n05/11/2026;Débito;-1,00\n"
    for cuerpo in (texto.encode("utf-8-sig"), texto.encode("cp1252"), texto.encode("utf-16")):
        assert leer_tabla(cuerpo, MAXIMO).columnas == ["Fecha", "Descripción", "Importe"]


@pytest.mark.parametrize("separador", [";", ",", "\t", "|"])
def test_reconoce_el_separador(separador):
    cuerpo = separador.join(("Fecha", "Importe", "Ref")) + "\n"
    cuerpo += separador.join(("05/11/2026", '"-1,00"', "0048")) + "\n"
    tabla = leer_tabla(cuerpo.encode(), MAXIMO)
    assert tabla.columnas == ["Fecha", "Importe", "Ref"]
    assert tabla.filas[0][1][1] == "-1,00"


def test_el_punto_y_coma_le_gana_a_la_coma_decimal():
    tabla = leer_tabla(b"Fecha;Importe;Ref\n05/11/2026;-1,00;0048\n05/11/2026;-2,00;0049\n", MAXIMO)
    assert tabla.columnas == ["Fecha", "Importe", "Ref"]


def test_las_comas_de_una_descripcion_no_le_ganan_al_separador():
    """Codex en #211: por comas hay más columnas repetidas, pero no son una tabla: la primera línea
    de datos quedaría de encabezado."""
    cuerpo = (
        "Fecha;Descripcion;Importe;Referencia\n"
        "05/11/2026;Pago a, b, c, d, e;-1,00;0048\n"
        "06/11/2026;Pago f, g, h, i, j;-2,00;0049\n"
        "07/11/2026;Pago sin comas;-3,00;0050\n"
    )
    tabla = leer_tabla(cuerpo.encode(), MAXIMO)
    assert tabla.columnas == ["Fecha", "Descripcion", "Importe", "Referencia"]
    assert len(tabla.filas) == 3


def test_sin_fechas_que_se_lean_gana_la_estructura_de_mas_lineas():
    cuerpo = (
        "Dia;Descripcion;Importe\n5 nov;a, b, c, d;-1,00\n6 nov;e, f, g, h;-2,00\n7 nov;i;-3,00\n"
    )
    assert leer_tabla(cuerpo.encode(), MAXIMO).columnas == ["Dia", "Descripcion", "Importe"]


def test_un_archivo_con_comas_sigue_separando_por_comas():
    cuerpo = b'Fecha,Detalle,Importe\n05/11/2026,"a; b; c; d",-1.00\n06/11/2026,x,-2.00\n'
    assert leer_tabla(cuerpo, MAXIMO).columnas == ["Fecha", "Detalle", "Importe"]


def test_saltea_el_titulo_y_los_datos_de_la_cuenta():
    cuerpo = (b"Banco SINTETICO\nCuenta;999\nSaldo;1\nPeriodo;Oct\n\n"
              b"Fecha;Detalle;Importe;Ref\n05/11/2026;x;-1,00;0048\n")  # fmt: skip
    tabla = leer_tabla(cuerpo, MAXIMO)
    assert tabla.columnas == ["Fecha", "Detalle", "Importe", "Ref"]
    assert tabla.filas == [(7, ["05/11/2026", "x", "-1,00", "0048"])]


def test_columnas_sin_nombre_o_repetidas():
    tabla = leer_tabla(b"Fecha;;Importe;Importe\n05/11/2026;x;-1,00;2\n", MAXIMO)
    assert tabla.columnas == ["Fecha", "Columna 2", "Importe", "Importe (2)"]


@pytest.mark.parametrize(
    "cuerpo",
    [
        b"",
        b"una sola columna\notra linea\n",
        b"PK\x03\x04\x00\x00\x00",
        b"Fecha;Importe\n\xff\xfe",
        b'Fecha;Importe\n"sin cerrar;1\n',
        b"=cmd;Importe\n05/11/2026;1\n",
        b"@cmd;Importe\n05/11/2026;1\n",
        b"\xff\xfeF",
    ],
)
def test_archivos_que_no_son_un_extracto(cuerpo):
    with pytest.raises(ExtractoInvalido):
        leer_tabla(cuerpo, MAXIMO)


def test_demasiadas_lineas():
    cuerpo = b"Fecha;Importe\n" + b"05/11/2026;-1,00\n" * 11
    with pytest.raises(extracto.DemasiadasLineas):
        leer_tabla(cuerpo, 10)
    assert leer_tabla(cuerpo, 12).columnas == ["Fecha", "Importe"]  # control


def test_demasiadas_columnas():
    fila = ";".join(f"c{i}" for i in range(extracto.MAXIMO_COLUMNAS + 1)).encode() + b"\n"
    with pytest.raises(ExtractoInvalido):
        leer_tabla(fila + fila, MAXIMO)


def test_la_muestra_no_lleva_cuentas_ni_formulas_y_se_corta():
    tabla = leer_tabla(
        b"Fecha;Detalle;Otro;Importe\n"
        b"05/11/2026;CBU 9990000300000000000017;=HYPERLINK(1);-1,00\n", MAXIMO,
    )  # fmt: skip
    celdas = muestra(tabla)
    assert "9990000300000000000017" not in celdas[1]
    assert celdas[2].startswith("'=")
    assert celdas[3] == "-1,00"
    largo = leer_tabla(("A;B\n" + "x" * 500 + ";1\n").encode(), MAXIMO)
    assert len(muestra(largo)[0]) == extracto.LARGO_MUESTRA


def test_una_cuenta_en_el_encabezado_no_queda_como_nombre_de_columna():
    tabla = leer_tabla(b"Cuenta;9990000300000000000017\n05/11/2026;-1,00\n", MAXIMO)
    assert tabla.columnas == ["Cuenta", "…"]


def test_el_cuit_tampoco_sale_en_la_muestra():
    tabla = leer_tabla(b"Fecha;Detalle\n05/11/2026;CUIT 30-71234567-9\n", MAXIMO)
    assert "71234567" not in muestra(tabla)[1]


def test_sugiere_el_signo_por_lo_que_ve():
    con = leer_tabla(b"Fecha;Importe\n05/11/2026;-1,00\n", MAXIMO)
    sin = leer_tabla(b"Fecha;Debito\n05/11/2026;1,00\n", MAXIMO)
    assert signo_sugerido(con) == SignoExtracto.CON_SIGNO
    assert signo_sugerido(sin) == SignoExtracto.SIN_SIGNO


def _tabla(*lineas: str):
    return leer_tabla("\n".join(("Fecha;Importe;Referencia", *lineas)).encode(), MAXIMO)


def test_con_signo_solo_cuenta_lo_negativo():
    lectura = leer_movimientos(
        _tabla("05/11/2026;-1,00;0048", "06/11/2026;2,00;0049", "04/11/2026;0,00;0050"), COLUMNAS
    )
    assert [m.importe_centavos for m in lectura.movimientos] == [100]
    assert (lectura.desde, lectura.hasta) == (date(2026, 11, 4), date(2026, 11, 6))


def test_sin_signo_lo_vacio_es_una_linea_que_no_es_debito():
    columnas = ColumnasExtracto(fecha="Fecha", importe="Importe", signo=SignoExtracto.SIN_SIGNO)
    lectura = leer_movimientos(_tabla("05/11/2026;1,00;", "06/11/2026;;"), columnas)
    assert [m.importe_centavos for m in lectura.movimientos] == [100]
    assert lectura.filas_no_leidas == []
    assert lectura.hasta == date(2026, 11, 6)


def test_dice_que_lineas_no_pudo_leer():
    lectura = leer_movimientos(
        _tabla("05/11/2026;-1,00;0048", "ayer;-1,00;0049", "05/11/2026;mucho;0050", "corta;1"),
        COLUMNAS,
    )
    assert lectura.filas_no_leidas == [3, 4, 5]
    assert len(lectura.movimientos) == 1


def test_sin_ninguna_linea_leida_no_hay_lectura():
    assert leer_movimientos(_tabla("ayer;-1,00;0048"), COLUMNAS) is None


def test_una_columna_que_no_esta():
    otras = ColumnasExtracto(fecha="Dia", importe="Importe", signo=SignoExtracto.CON_SIGNO)
    with pytest.raises(extracto.ColumnasNoEncontradas):
        leer_movimientos(_tabla("05/11/2026;-1,00;0048"), otras)


def test_el_hash_no_depende_del_texto_y_si_de_la_vez():
    a = leer_movimientos(
        _tabla("05/11/2026;-1.000,00;0048-1", "05/11/2026;-1000,00;00481"), COLUMNAS
    )
    b = leer_movimientos(_tabla("2026-11-05;-1000.00;00481", "05/11/26;-1000,00;00481"), COLUMNAS)
    claves = [m.hash_linea for m in a.movimientos]
    assert len(set(claves)) == 2
    assert claves == [m.hash_linea for m in b.movimientos]


def _m(importe=100, numero="0048", fecha=DIA, pago=None, moneda="ARS"):
    return MovimientoGuardado(uuid4(), fecha, importe, moneda, numero, pago)


def _p(
    importe=100, numero="0048", fecha=DIA, conforme=True, cotejado=False, puede=True, moneda="ARS"
):
    return PagoComparable(uuid4(), numero, fecha, importe, moneda, conforme, cotejado, False, puede)


def test_por_numero_en_otra_moneda_no_coincide():
    assert _comparar([_m()], [_p(moneda="USD")]).pares == []
    assert len(_comparar([_m(moneda="USD")], [_p(moneda="USD")]).pares) == 1  # control


def test_por_importe_y_fecha_en_otra_moneda_no_coincide():
    assert _comparar([_m(numero=None)], [_p(moneda="USD")]).pares == []
    assert len(_comparar([_m(numero=None)], [_p()]).pares) == 1  # control


def test_el_hash_de_pesos_no_cambia_y_el_de_otra_moneda_si():
    pesos = leer_movimientos(_tabla("05/11/2026;-1000,00;00481"), COLUMNAS).movimientos[0]
    dolares = leer_movimientos(
        _tabla("05/11/2026;-1000,00;00481"), COLUMNAS.model_copy(update={"moneda": "USD"})
    ).movimientos[0]
    texto = "2026-11-05|100000|00481|0"
    assert pesos.hash_linea == hashlib.sha256(texto.encode()).hexdigest()
    assert dolares.hash_linea != pesos.hash_linea
    assert (pesos.moneda, dolares.moneda) == ("ARS", "USD")


@pytest.mark.parametrize("celda", ["USD 1.000,00", "U$S 1.000,00", "US$ 1.000,00", "1.000,00 USD"])
def test_un_importe_en_dolares_se_lee(celda):
    assert leer_importe(celda) == Decimal("1000.00")


def _comparar(movimientos, pagos):
    return comparar(movimientos, pagos, date(2026, 11, 1), date(2026, 11, 30))


def test_por_numero_aunque_cambie_la_fecha():
    m, p = _m(fecha=date(2026, 11, 8)), _p()
    assert _comparar([m], [p]).pares == [(m, p)]


def test_por_numero_con_otro_importe_se_miran_los_dos():
    m, p = _m(importe=101), _p()
    r = _comparar([m], [p])
    assert r.pares == [] and r.sin_pago == [m] and r.sin_movimiento == [p]


def test_un_numero_que_no_es_de_ningun_pago_no_cae_a_importe_y_fecha():
    m, p = _m(numero="9999"), _p()
    r = _comparar([m], [p])
    assert r.pares == [] and r.sin_pago == [m] and r.sin_movimiento == [p]


def test_dos_lineas_con_el_mismo_numero_atan_una_sola():
    a, b, p = _m(), _m(), _p()
    r = _comparar([a, b], [p])
    assert r.pares == [(a, p)] and r.sin_pago == [b]


def test_importe_y_fecha_sin_numero():
    m, p = _m(numero=None), _p()
    assert _comparar([m], [p]).pares == [(m, p)]
    otra_fecha = _m(numero=None, fecha=date(2026, 11, 6))
    r = _comparar([otra_fecha], [p])
    assert r.pares == [] and r.sin_pago == [otra_fecha]


def test_importe_y_fecha_con_duda_no_empareja():
    m, a, b = _m(numero=None), _p(numero="0001"), _p(numero="0002")
    r = _comparar([m], [a, b])
    assert r.pares == [] and r.sin_pago == [m] and r.sin_movimiento == [a, b]


def test_tantas_lineas_como_pagos_iguales_se_emparejan():
    m, n, a, b = _m(numero=None), _m(numero=None), _p(numero="0001"), _p(numero="0002")
    assert len(_comparar([m, n], [a, b]).pares) == 2


def test_lo_ya_atado_no_se_compara_de_nuevo():
    p = _p()
    m = _m(pago=p.pago_id)
    r = _comparar([m], [p])
    assert r.pares == [] and r.ya_atados == 1 and r.sin_pago == [] and r.sin_movimiento == []


def test_fuera_del_periodo_o_sin_conciliar_o_cotejado_no_falta_en_el_banco():
    fuera = _p(numero="0001", fecha=date(2026, 10, 31))
    sin_conciliar = _p(numero="0002", conforme=False)
    cotejado = _p(numero="0003", cotejado=True)
    falta = _p(numero="0004")
    assert _comparar([], [fuera, sin_conciliar, cotejado, falta]).sin_movimiento == [falta]


# Columnas reconocidas solas (P-23) ------------------------------------------------------------


def _encabezado_y_filas(encabezado: str, *lineas: str) -> extracto.Tabla:
    return leer_tabla("\n".join((encabezado, *lineas)).encode(), MAXIMO)


def test_fecha_debito_y_numero_de_operacion_se_reconocen_solas():
    """El caso del informe (P-23): sin la pantalla de columnas."""
    tabla = _encabezado_y_filas("Fecha;Débito;Número de operación", "05/11/2026;1.234,00;48213901")
    assert extracto.reconocer_columnas(tabla) == ColumnasExtracto(
        fecha="Fecha", importe="Débito", numero_operacion="Número de operación",
        signo=SignoExtracto.SIN_SIGNO,
    )  # fmt: skip


def test_una_columna_de_importes_con_negativos_va_con_signo():
    tabla = _encabezado_y_filas(
        "Fecha;Descripción;Importe;Referencia", "05/11/2026;Transf;-1.234,00;4821"
    )
    assert extracto.reconocer_columnas(tabla) == COLUMNAS


@pytest.mark.parametrize(
    ("encabezado", "fila"),
    [
        # Sin ningún negativo, una columna de importes no dice cuáles son los débitos.
        ("Fecha;Importe", "05/11/2026;1.234,00"),
        # Dos que pueden ser la fecha, o el importe, o el número: se pregunta.
        ("Fecha;Fecha de operación;Importe", "05/11/2026;05/11/2026;-1,00"),
        ("Fecha;Importe;Débito", "05/11/2026;-1,00;1,00"),
        ("Fecha;Débito;Referencia;Comprobante", "05/11/2026;1,00;A1;B2"),
        # Sin fecha o sin importe.
        ("Día;Débito", "05/11/2026;1,00"),
        ("Fecha;Crédito", "05/11/2026;1,00"),
    ],
)
def test_con_alguna_duda_no_se_reconocen(encabezado, fila):
    assert extracto.reconocer_columnas(_encabezado_y_filas(encabezado, fila)) is None


def test_control_la_misma_columna_de_importes_con_un_negativo_si_se_reconoce():
    tabla = _encabezado_y_filas("Fecha;Importe", "05/11/2026;1.234,00", "06/11/2026;-5,00")
    columnas = extracto.reconocer_columnas(tabla)
    assert columnas is not None and columnas.signo == SignoExtracto.CON_SIGNO
    assert columnas.numero_operacion is None


def test_los_nombres_se_comparan_sin_tildes_mayusculas_ni_signos():
    tabla = _encabezado_y_filas(
        "FECHA;N.º de Operación;DEBITOS;Fecha valor", "05/11/2026;4821;1,00;05/11/2026"
    )
    columnas = extracto.reconocer_columnas(tabla)
    assert columnas is not None
    assert (columnas.fecha, columnas.importe, columnas.numero_operacion) == (
        "FECHA", "DEBITOS", "N.º de Operación")  # fmt: skip


def test_una_columna_de_debitos_con_negativos_va_con_signo():
    """Si no, los débitos negativos se perderían: con signo, un negativo es un débito."""
    tabla = _encabezado_y_filas("Fecha;Débito", "05/11/2026;-1.234,00")
    columnas = extracto.reconocer_columnas(tabla)
    assert columnas is not None and columnas.signo == SignoExtracto.CON_SIGNO
    assert [m.importe_centavos for m in leer_movimientos(tabla, columnas).movimientos] == [123_400]
