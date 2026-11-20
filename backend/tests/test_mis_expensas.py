"""Lectura del formato Mis Expensas (H5-1): una liquidación inventada con la misma geometría.

Las posiciones imitan las medidas sobre las cinco liquidaciones anonimizadas del 19-11-2026
(encabezados, márgenes, columnas alineadas a la derecha); los nombres y los importes son
inventados. Ningún dato sale de una liquidación real.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.liquidacion.mis_expensas import (
    FormatoNoReconocido,
    Origen,
    Pagina,
    Palabra,
    leer,
)

D = Decimal
LETRA = 4.0
ESPACIO = 3.0


def izq(texto: str, x0: float) -> list[tuple[str, float, float]]:
    """Palabras alineadas a la izquierda desde `x0`, separadas por un espacio."""
    salida, x = [], x0
    for p in texto.split():
        salida.append((p, x, x + LETRA * len(p)))
        x += LETRA * len(p) + ESPACIO
    return salida


def der(texto: str, x1: float) -> list[tuple[str, float, float]]:
    """Un valor alineado a la derecha que termina en `x1`."""
    return [(texto, x1 - LETRA * len(texto), x1)]


def renglon(top: float, *celdas: list[tuple[str, float, float]]) -> list[Palabra]:
    return [Palabra(t, x0, x1, top, top + 8) for celda in celdas for t, x0, x1 in celda]


def pagina(*renglones: list[Palabra], apaisada: bool = False) -> Pagina:
    ancho, alto = (841.0, 595.0) if apaisada else (595.0, 841.0)
    return Pagina(ancho, alto, tuple(p for r in renglones for p in r))


# Columnas de la tabla de gastos: donde termina cada importe.
GC, AYSA, TOT = 355, 425, 565


def encabezado_gastos(top: float, con_rubros: bool = True) -> list[Palabra]:
    rubros = [izq("RUBROS - CONCEPTO", 124)] if con_rubros else []
    return renglon(top, *rubros, izq("Gastos Comunes", 295), izq("AySA", 383), izq("TOTAL", 521))


def importes(top: float, texto: str, gc: str, aysa: str, total: str, x0: float = 43):
    return renglon(top, izq(texto, x0), der(gc, GC), der(aysa, AYSA), der(total, TOT))


# Columnas del prorrateo: donde termina cada valor.
SALDO, PAGOS, DEUDA, INTERES, DISP = 280, 334, 377, 420, 461
PCT_GC, MONTO_GC, PCT_AYSA, MONTO_AYSA, OTROS, RED, TOTAL_U = 486, 540, 564, 614, 729, 758, 811


def encabezado_prorrateo(disponible: bool = True) -> list[list[Palabra]]:
    disp = [izq("Disponible", 425)] if disponible else []
    return [
        renglon(37, izq("ESTADO DE CUENTAS Y PRORRATEO DE GASTOS", 336)),
        renglon(54, izq("Gastos Comunes", 476), izq("AySA", 570)),
        renglon(55, izq("Saldo", 246), izq("Pagos", 299)),
        renglon(
            61,
            izq("UF", 34),
            izq("Depto.", 51),
            izq("Propietario", 134),
            izq("Deuda", 347),
            izq("Interés", 389),
            *disp,
            izq("Otros gastos", 640),
            izq("Red.", 734),
            izq("TOTAL", 776),
        ),
        renglon(66, izq("Anterior", 242), izq("Recibidos", 290)),
        renglon(67, izq("%", 472), izq("Monto", 495), izq("%", 551), izq("Monto", 575)),
    ]


def fila_unidad(top: float, uf: str, depto: str, nombre: str, valores: list[str]) -> list[Palabra]:
    columnas = (
        SALDO,
        PAGOS,
        DEUDA,
        INTERES,
        DISP,
        PCT_GC,
        MONTO_GC,
        PCT_AYSA,
        MONTO_AYSA,
        OTROS,
        RED,
        TOTAL_U,
    )
    return renglon(
        top,
        izq(uf, 32),
        izq(depto, 53),
        izq(nombre, 77),
        *(der(v, x) for v, x in zip(valores, columnas, strict=True)),
    )


UNIDAD_1 = [
    "1.000,00",
    "1.000,00",
    "0,00",
    "0,00",
    "0,00",
    "50,00",
    "1.000,00",
    "60,00",
    "300,30",
    "0,00",
    "0,70",
    "1.301,00",
]
UNIDAD_2 = [
    "500,00",
    "0,00",
    "500,00",
    "25,00",
    "0,00",
    "30,00",
    "600,00",
    "40,00",
    "200,20",
    "0,00",
    "0,80",
    "1.326,00",
]
UNIDAD_3 = [
    "300,00",
    "310,00",
    "0,00",
    "0,00",
    "10,00",
    "20,00",
    "400,00",
    "-",
    "0,00",
    "0,00",
    "0,00",
    "390,00",
]
TOTALES_PRORRATEO = [
    "1.800,00",
    "1.310,00",
    "500,00",
    "25,00",
    "10,00",
    "100,00",
    "2.000,00",
    "100,00",
    "500,50",
    "0,00",
    "1,50",
    "3.017,00",
]


def liquidacion(
    *,
    gasto_luz: tuple[str, str, str] = ("1.000,00", "0,00", "1.000,00"),
    unidad_1: list[str] | None = None,
    unidad_3: list[str] | None = None,
    totales_prorrateo: list[str] | None = None,
    con_encabezado_gastos: bool = True,
    disponible: bool = True,
) -> list[Pagina]:
    cab = [encabezado_gastos(220), encabezado_gastos(54), encabezado_gastos(143, con_rubros=False)]
    cab = cab if con_encabezado_gastos else [[], [], []]
    p1 = pagina(
        renglon(39, izq("EXPENSAS - Liquidación de Septiembre 2026", 202)),
        renglon(111, izq("Período: Septiembre 2026", 445)),
        renglon(122, izq("1er Vencimiento: 13/10/2026", 445)),
        renglon(133, izq("2do Vencimiento: 23/10/2026", 445)),
        renglon(200, izq("(Conf. Art. 10) PAGOS DEL PERIODO", 37)),
        cab[0],
        renglon(233, izq("1 SERVICIOS PUBLICOS", 31)),
        renglon(247, izq("EMPRESA DE LUZ - CUIT 30-00000000-0", 43)),
        renglon(258, izq("2 puertas de acceso", 43)),
        importes(269, "Periodo Septiembre/2026 - Factura B", *gasto_luz),
        importes(282, "AGUA SA - Factura B", "0,00", "500,50", "500,50"),
        renglon(295, izq("TOTAL CATEGORIA 1", 31), der("60,01%", 494), der("1.500,50", TOT)),
    )
    p2 = pagina(
        renglon(36, izq("(Conf. Art. 10) PAGOS DEL PERIODO", 37)),
        cab[1],
        renglon(67, izq("2 MANTENIMIENTO", 33)),
        renglon(80, izq("PLOMERO - Reparación de cañería", 43)),
        renglon(
            91,
            izq("Saldo $", 43),
            der("300.000,00", 248),
            der("1.000,00", GC),
            der("0,00", AYSA),
            der("1.000,00", TOT),
        ),
        renglon(104, izq("TOTAL CATEGORIA 2", 31), der("39,99%", 494), der("1.000,00", TOT)),
        renglon(125, izq("TOTAL DE GASTOS", 266)),
        cab[2],
        renglon(
            156, izq("TOTALES", 31), der("2.000,00", GC), der("500,50", AYSA), der("2.500,50", TOT)
        ),
        renglon(190, izq("ESTADO FINANCIERO", 259)),
        renglon(203, izq("SALDO INICIAL", 31), der("9.000,00", TOT)),
    )
    p3 = pagina(
        *encabezado_prorrateo(disponible),
        fila_unidad(80, "001", "PB-A", "Ana Prueba", unidad_1 or UNIDAD_1),
        fila_unidad(93, "002", "1-A", "Beto Ensayo", UNIDAD_2),
        apaisada=True,
    )
    p4 = pagina(
        *encabezado_prorrateo(disponible),
        fila_unidad(80, "003", "1-B", "Ceci Muestra", unidad_3 or UNIDAD_3),
        renglon(
            93,
            izq("TOTALES", 31),
            *(
                der(v, x)
                for v, x in zip(
                    totales_prorrateo or TOTALES_PRORRATEO,
                    (
                        SALDO,
                        PAGOS,
                        DEUDA,
                        INTERES,
                        DISP,
                        PCT_GC,
                        MONTO_GC,
                        PCT_AYSA,
                        MONTO_AYSA,
                        OTROS,
                        RED,
                        TOTAL_U,
                    ),
                    strict=True,
                )
            ),
        ),
        apaisada=True,
    )
    return [p1, p2, p3, p4]


def test_lee_encabezado_gastos_y_prorrateo_de_una_liquidacion_que_cierra():
    lq = leer(liquidacion())
    assert lq.periodo == (2026, 9)
    assert lq.vencimientos == ("2026-10-13", "2026-10-23")
    assert lq.grupos == ("Gastos Comunes", "AySA")
    assert [(r.numero, r.nombre, r.total, r.porcentaje) for r in lq.rubros] == [
        (1, "SERVICIOS PUBLICOS", D("1500.50"), D("60.01")),
        (2, "MANTENIMIENTO", D("1000.00"), D("39.99")),
    ]
    luz, agua = lq.rubros[0].gastos
    # Un gasto de varios renglones: el renglón sangrado que empieza con «2» no abre el rubro 2.
    assert luz.concepto.split("\n") == [
        "EMPRESA DE LUZ - CUIT 30-00000000-0",
        "2 puertas de acceso",
        "Periodo Septiembre/2026 - Factura B",
    ]
    assert luz.importes == {"Gastos Comunes": D("1000.00"), "AySA": D("0.00")}
    assert luz.total == D("1000.00") and luz.origen == Origen(1, 269)
    assert agua.importes["AySA"] == D("500.50")
    # Un importe a la izquierda de las columnas es parte del texto, no un importe del gasto.
    (plomero,) = lq.rubros[1].gastos
    assert plomero.concepto.endswith("Saldo $ 300.000,00") and plomero.total == D("1000.00")
    assert lq.totales_gastos == {"Gastos Comunes": D("2000.00"), "AySA": D("500.50")}
    assert lq.total_gastos == D("2500.50")

    assert [(u.uf, u.depto, u.nombre) for u in lq.unidades] == [
        ("001", "PB-A", "Ana Prueba"),
        ("002", "1-A", "Beto Ensayo"),
        ("003", "1-B", "Ceci Muestra"),
    ]
    beto = lq.unidades[1]
    assert (beto.saldo_anterior, beto.pagos, beto.deuda, beto.interes, beto.disponible) == (
        D("500.00"),
        D("0.00"),
        D("500.00"),
        D("25.00"),
        D("0.00"),
    )
    assert beto.grupos["Gastos Comunes"].porcentaje == D("30.00")
    assert beto.grupos["AySA"].monto == D("200.20")
    assert (beto.redondeo, beto.total, beto.origen) == (D("0.80"), D("1326.00"), Origen(3, 93))
    ceci = lq.unidades[2]
    assert ceci.grupos["AySA"].porcentaje is None and ceci.grupos["AySA"].monto == D("0.00")
    assert lq.totales_prorrateo.total == D("3017.00")
    assert lq.totales_prorrateo.origen == Origen(4, 93)
    assert lq.diferencias == ()


def test_un_gasto_cuyos_grupos_no_suman_su_total_se_informa():
    lq = leer(liquidacion(gasto_luz=("1.000,00", "0,00", "1.000,05")))
    reglas = {(d.regla, d.esperado, d.informado) for d in lq.diferencias}
    assert ("gasto_suma_grupos", D("1000.00"), D("1000.05")) in reglas
    assert ("rubro_suma_gastos", D("1500.55"), D("1500.50")) in reglas


def test_el_total_de_una_unidad_se_compara_con_un_centavo_de_tolerancia():
    un_centavo = [*UNIDAD_1[:-1], "1.301,01"]
    # La unidad y la columna TOTAL quedan a un centavo: D5 no lo informa.
    assert leer(liquidacion(unidad_1=un_centavo)).diferencias == ()
    dos_centavos = [*UNIDAD_1[:-1], "1.301,02"]
    dif = leer(liquidacion(unidad_1=dos_centavos)).diferencias
    assert [(d.regla, d.detalle, d.esperado, d.informado) for d in dif] == [
        ("unidad_total", "UF 001 PB-A", D("1301.00"), D("1301.02")),
        ("prorrateo_columna", "total", D("3017.02"), D("3017.00")),
    ]


def test_porcentajes_que_no_suman_cien_y_prorrateo_menor_que_el_gasto_se_informan():
    unidad_3 = [*UNIDAD_3[:5], "19,96", "399,20", *UNIDAD_3[7:-1], "389,20"]
    totales = [*TOTALES_PRORRATEO[:5], "99,96", "1.999,20", *TOTALES_PRORRATEO[7:-1], "3.016,20"]
    dif = leer(liquidacion(unidad_3=unidad_3, totales_prorrateo=totales)).diferencias
    assert [(d.regla, d.detalle, d.esperado, d.informado) for d in dif] == [
        ("prorrateo_grupo_vs_gastos", "Gastos Comunes", D("2000.00"), D("1999.20")),
        ("prorrateo_porcentaje_100", "Gastos Comunes", D("100"), D("99.96")),
    ]
    assert dif[0].origen == Origen(4, 93)


def test_sin_encabezado_de_gastos_no_se_lee_nada():
    with pytest.raises(FormatoNoReconocido, match="tabla de gastos"):
        leer(liquidacion(con_encabezado_gastos=False))


def test_un_prorrateo_sin_la_columna_disponible_no_se_lee():
    with pytest.raises(FormatoNoReconocido, match="disponible"):
        leer(liquidacion(disponible=False))


def test_dos_importes_en_la_misma_columna_cortan_la_lectura():
    paginas = liquidacion()
    extra = Palabra("5,00", GC - 60, GC - 40, 269, 277)  # dentro de la columna Gastos Comunes
    paginas[0] = Pagina(595.0, 841.0, (*paginas[0].palabras, extra))
    with pytest.raises(FormatoNoReconocido, match="dos importes"):
        leer(paginas)


def test_un_documento_sin_texto_no_es_una_liquidacion():
    with pytest.raises(FormatoNoReconocido, match="no tiene texto"):
        leer([Pagina(595.0, 841.0, ())])


def test_el_porcentaje_de_totales_se_compara_con_la_suma_de_las_unidades():
    totales = [*TOTALES_PRORRATEO[:5], "90,00", *TOTALES_PRORRATEO[6:]]
    dif = leer(liquidacion(totales_prorrateo=totales)).diferencias
    assert [(d.regla, d.detalle, d.esperado, d.informado) for d in dif] == [
        ("prorrateo_columna", "% Gastos Comunes", D("100.00"), D("90.00")),
    ]


def test_un_encabezado_de_prorrateo_incompleto_no_se_lee():
    paginas = liquidacion()
    # Queda UF, Depto. y Propietario; se quitan los demás rótulos del encabezado.
    corte = [p for p in paginas[2].palabras if not (54 <= p.top <= 67 and p.x0 > 200)]
    paginas[2] = Pagina(841.0, 595.0, tuple(corte))
    with pytest.raises(FormatoNoReconocido, match="sin columna"):
        leer(paginas)


def test_un_departamento_con_espacio_queda_entero():
    paginas = liquidacion()
    sin_002 = [p for p in paginas[2].palabras if p.top != 93]
    fila = fila_unidad(93, "002", "PB A", "Beto Ensayo", UNIDAD_2)
    paginas[2] = Pagina(841.0, 595.0, (*sin_002, *fila))
    beto = leer(paginas).unidades[1]
    assert (beto.uf, beto.depto, beto.nombre) == ("002", "PB A", "Beto Ensayo")
