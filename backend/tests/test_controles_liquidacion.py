"""Controles de la liquidación leída, en palabras (rediseño de la confirmación, RL1-A).

La liquidación es la sintética de noviembre de las lecciones (SINTETICOS / NO REAL): tres gastos,
dos grupos, cinco unidades. PB paga $ 500 más de lo que le toca por su porcentaje y la 2° B, $ 500
menos (las sumas cierran igual: los cruces de D5 no lo ven); la 1° B debe octubre con 3 % de
interés.
"""

from __future__ import annotations

import copy
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from cem_contracts.importacion_liquidacion import EstadoControl

from app.liquidacion.controles import Dato, comprobante_de, controles, pesos, porcentaje

GC, AYSA = "Gastos Comunes", "AySA"
UNIDADES = [
    # uf, depto, % GC, monto GC, deuda, interés
    ("1", "PB", "16.00", "32500.00", "0.00", "0.00"),
    ("2", "1° A", "20.00", "40000.00", "0.00", "0.00"),
    ("3", "1° B", "24.00", "48000.00", "48000.00", "1440.00"),
    ("4", "2° A", "19.00", "38000.00", "0.00", "0.00"),
    ("5", "2° B", "21.00", "41500.00", "0.00", "0.00"),
]
IDS = {uf: uuid4() for uf, *_ in UNIDADES}


def _t(x: Decimal) -> str:
    return f"{x:.2f}"


def noviembre() -> list[dict]:
    """Lo leído, como `importacion.campos` lo deja (tipo, clave, valor, unidad)."""
    leidos = [
        {
            "tipo": "total",
            "clave": "total:gastos",
            "valor": {
                "grupos": {GC: "200000.00", AYSA: "30000.00"},
                "total": "230000.00",
                "vencimientos": ["10/12/2026"],
            },
        },
        {
            "tipo": "rubro",
            "clave": "rubro:1",
            "valor": {
                "numero": 1,
                "nombre": "Servicios",
                "porcentaje": "34.78",
                "total": "80000.00",
            },
        },
        {
            "tipo": "gasto",
            "clave": "gasto:1:1",
            "valor": {
                "rubro": 1,
                "concepto": "Luz",
                "importes": {GC: "50000.00", AYSA: "0.00"},
                "total": "50000.00",
            },
        },
        {
            "tipo": "gasto",
            "clave": "gasto:1:2",
            "valor": {
                "rubro": 1,
                "concepto": "Agua",
                "importes": {GC: "0.00", AYSA: "30000.00"},
                "total": "30000.00",
            },
        },
        {
            "tipo": "rubro",
            "clave": "rubro:2",
            "valor": {
                "numero": 2,
                "nombre": "Limpieza",
                "porcentaje": "65.22",
                "total": "150000.00",
            },
        },
        {
            "tipo": "gasto",
            "clave": "gasto:2:1",
            "valor": {
                "rubro": 2,
                "concepto": "Limpieza",
                "importes": {GC: "150000.00", AYSA: "0.00"},
                "total": "150000.00",
            },
        },
    ]
    suma = {k: Decimal(0) for k in ("saldo_anterior", "pagos", "deuda", "interes", "total")}
    for uf, depto, pct, monto, deuda, interes in UNIDADES:
        total = Decimal(monto) + Decimal("6000.00") + Decimal(deuda) + Decimal(interes)
        saldo = Decimal(deuda)  # el mes anterior le cobraban la deuda; no pagó nada
        base = {"uf": uf, "depto": depto}
        leidos += [
            {
                "tipo": "unidad",
                "clave": f"unidad:{uf}",
                "valor": base | {"nombre": "SINTETICO"},
                "unidad": IDS[uf],
            },
            {
                "tipo": "saldo_unidad",
                "clave": f"saldo_unidad:{uf}",
                "valor": base
                | {
                    "saldo_anterior": _t(saldo),
                    "pagos": "0.00",
                    "deuda": deuda,
                    "interes": interes,
                    "disponible": "0.00",
                },
                "unidad": IDS[uf],
            },
            {
                "tipo": "importe_unidad",
                "clave": f"importe_unidad:{uf}",
                "valor": base
                | {
                    "grupos": {
                        GC: {"porcentaje": pct, "monto": monto},
                        AYSA: {"porcentaje": "20.00", "monto": "6000.00"},
                    },
                    "otros_gastos": "0.00",
                    "redondeo": "0.00",
                    "total": _t(total),
                },
                "unidad": IDS[uf],
            },
        ]
        for k, v in (
            ("saldo_anterior", saldo),
            ("deuda", Decimal(deuda)),
            ("interes", Decimal(interes)),
            ("total", total),
        ):
            suma[k] += v
    leidos.append(
        {
            "tipo": "total",
            "clave": "total:prorrateo",
            "valor": {
                "saldo_anterior": _t(suma["saldo_anterior"]),
                "pagos": "0.00",
                "deuda": _t(suma["deuda"]),
                "interes": _t(suma["interes"]),
                "disponible": "0.00",
                "grupos": {
                    GC: {"porcentaje": "100.00", "monto": "200000.00"},
                    AYSA: {"porcentaje": "100.00", "monto": "30000.00"},
                },
                "otros_gastos": "0.00",
                "redondeo": "0.00",
                "total": _t(suma["total"]),
            },
        }
    )
    return leidos


def datos(leidos: list[dict]) -> list[Dato]:
    return [
        Dato(tipo=x["tipo"], clave=x["clave"], valor=x["valor"], unidad_id=x.get("unidad"))
        for x in leidos
    ]


def por_numero(leidos: list[dict], anterior: dict[UUID, Decimal] | None = None) -> dict:
    return {c.numero: c for c in controles(datos(leidos), anterior)}


def valor(leidos: list[dict], clave: str) -> dict:
    return next(x["valor"] for x in leidos if x["clave"] == clave)


def test_noviembre_dice_lo_que_hay_para_mirar() -> None:
    c = por_numero(noviembre())
    assert [n for n, x in c.items() if x.estado == EstadoControl.BIEN] == [1, 2, 3, 4, 5, 6, 10]
    assert [n for n, x in c.items() if x.estado == EstadoControl.PARA_MIRAR] == [7, 8, 11]
    assert c[11].lineas == [
        "«Luz» ($ 50.000) no dice su comprobante.",
        "«Agua» ($ 30.000) no dice su comprobante.",
        "«Limpieza» ($ 150.000) no dice su comprobante.",
    ]
    assert c[9].estado == EstadoControl.NO_APLICA
    assert c[7].lineas == [
        "PB (UF 1) paga $ 32.500 de Gastos Comunes: $ 500 más de lo que le toca por su 16 % de"
        " $ 200.000 ($ 32.000).",
        "2° B (UF 5) paga $ 41.500 de Gastos Comunes: $ 500 menos de lo que le toca por su 21 % de"
        " $ 200.000 ($ 42.000).",
    ]
    assert c[8].lineas == [
        "1° B (UF 3) debe $ 48.000 del mes anterior, más $ 1.440 de interés (3 % de la deuda)."
        " Este mes paga $ 103.440."
    ]
    assert c[4].texto == (
        "Cada grupo suma sus gastos y se reparte entero: Gastos Comunes: $ 200.000 ·"
        " AySA: $ 30.000."
    )
    assert c[10].texto == "Las 5 unidades de la liquidación están en el padrón."


def test_sin_datos_no_hay_controles() -> None:
    assert controles([], None) == []


@pytest.mark.parametrize(
    ("numero", "clave", "camino", "nuevo"),
    [
        (1, "gasto:1:1", ("total",), "50001.00"),
        (2, "rubro:2", ("total",), "150100.00"),
        (3, "rubro:1", ("porcentaje",), "30.00"),
        (4, "total:gastos", ("grupos", GC), "199000.00"),
        (5, "total:gastos", ("total",), "231000.00"),
        (6, "importe_unidad:2", ("total",), "46100.00"),
        (6, "total:prorrateo", ("total",), "999999.00"),
        (6, "total:prorrateo", ("pagos",), "10.00"),
        (7, "importe_unidad:4", ("grupos", GC, "porcentaje"), "18.00"),
        (8, "saldo_unidad:5", ("deuda",), "100.00"),
    ],
)
def test_cada_control_ve_su_cambio(numero: int, clave: str, camino: tuple, nuevo: str) -> None:
    """Mutación: cambiar un solo número pone su control para mirar (los otros pueden cambiar
    también, como en la lectura)."""
    leidos = copy.deepcopy(noviembre())
    v = valor(leidos, clave)
    for k in camino[:-1]:
        v = v[k]
    v[camino[-1]] = nuevo
    c = por_numero(leidos)
    assert c[numero].estado == EstadoControl.PARA_MIRAR
    assert c[numero].lineas


def test_padron_dice_la_unidad_que_falta() -> None:
    leidos = noviembre()
    for x in leidos:
        if x["clave"] == "unidad:4":
            x["unidad"] = None
    c = por_numero(leidos)
    assert c[10].estado == EstadoControl.PARA_MIRAR
    assert c[10].lineas == ["2° A (UF 4) no está en el padrón del edificio."]


def test_saldo_anterior_con_la_liquidacion_del_mes_pasado() -> None:
    cobrado = {IDS[uf]: Decimal(deuda) for uf, _, _, _, deuda, _ in UNIDADES}
    assert por_numero(noviembre(), cobrado)[9].estado == EstadoControl.BIEN
    cobrado[IDS["2"]] = Decimal("46000.00")
    c = por_numero(noviembre(), cobrado)[9]
    assert c.estado == EstadoControl.PARA_MIRAR
    assert c.lineas == [
        "1° A (UF 2): el saldo anterior dice $ 0 y la liquidación del mes pasado le cobraba"
        " $ 46.000."
    ]


def test_un_porcentaje_redondeado_no_es_para_mirar() -> None:
    """Tres partes iguales de $ 30.000 con porcentajes de dos decimales que suman 100 (33,33 +
    33,33 + 33,34): un porcentaje puede estar ajustado hasta un centésimo de punto ($ 3,01 sobre ese
    total), así que $ 10.000 está bien en las tres."""
    leidos = noviembre()
    for uf, pct, monto in (
        ("1", "33.33", "10000.00"),
        ("2", "33.33", "10000.00"),
        ("3", "33.34", "10000.00"),
    ):
        valor(leidos, f"importe_unidad:{uf}")["grupos"][AYSA] = {"porcentaje": pct, "monto": monto}
    for uf in ("4", "5"):
        valor(leidos, f"importe_unidad:{uf}")["grupos"][AYSA] = {
            "porcentaje": "0.00",
            "monto": "0.00",
        }
    c = por_numero(leidos)[7]
    assert all("AySA" not in linea for linea in c.lineas), c.lineas


def test_porcentajes_que_no_suman_cien() -> None:
    leidos = noviembre()
    valor(leidos, "importe_unidad:5")["grupos"][AYSA]["porcentaje"] = "10.00"
    c = por_numero(leidos)[7]
    assert "Los porcentajes de AySA suman 90 %, no 100 %." in c.lineas


def test_lectura_con_ia_sin_columnas_no_aplica() -> None:
    """Una lectura con IA trae menos columnas: los controles que no tienen sus datos lo dicen."""
    leidos = [
        {"tipo": "total", "clave": "total:gastos", "valor": {"total": "230000.00"}},
        {
            "tipo": "rubro",
            "clave": "rubro:1",
            "valor": {"numero": 1, "nombre": "Servicios", "total": "230000.00"},
        },
        {
            "tipo": "gasto",
            "clave": "gasto:1:1",
            "valor": {"rubro": 1, "concepto": "Luz", "total": "230000.00"},
        },
        {
            "tipo": "unidad",
            "clave": "unidad:1",
            "valor": {"uf": "1", "depto": "PB"},
            "unidad": IDS["1"],
        },
        {
            "tipo": "importe_unidad",
            "clave": "importe_unidad:1",
            "valor": {"uf": "1", "depto": "PB", "total": "230000.00"},
            "unidad": IDS["1"],
        },
    ]
    c = por_numero(leidos)
    assert {n for n, x in c.items() if x.estado == EstadoControl.NO_APLICA} == {1, 3, 4, 6, 7, 8, 9}
    assert {n for n, x in c.items() if x.estado == EstadoControl.BIEN} == {2, 5, 10}
    assert c[11].estado == EstadoControl.PARA_MIRAR  # «Luz» no dice su comprobante


@pytest.mark.parametrize(
    ("importe", "texto"),
    [("230000", "$ 230.000"), ("1234.5", "$ 1.234,50"), ("-500", "-$ 500"), ("0", "$ 0")],
)
def test_pesos(importe: str, texto: str) -> None:
    assert pesos(Decimal(importe)) == texto


def test_porcentaje() -> None:
    assert porcentaje(Decimal("16.00")) == "16 %"
    assert porcentaje(Decimal("3.50")) == "3,5 %"


def test_el_total_impreso_del_prorrateo_se_compara_con_sus_unidades() -> None:
    """Revisión de Codex (cem-aplicacion 297): el total impreso de una columna del prorrateo que no
    es la suma de las unidades se dice, como `prorrateo_columna` de D5."""
    leidos = noviembre()
    valor(leidos, "total:prorrateo")["total"] = "999999.00"
    c = por_numero(leidos)[6]
    assert c.lineas == [
        "La columna total del prorrateo dice $ 999.999 y sus unidades suman $ 279.440."
    ]


def _con_comprobantes(leidos: list[dict]) -> list[dict]:
    conceptos = {
        "gasto:1:1": "Luz - EDENOR SINTETICA - CUIT 30-11111111-1 - Factura B Nº 0001-00001234",
        "gasto:1:2": "Agua - AYSA SINTETICA - CUIT 30-22222222-2 - Factura B Nº 0002-00000077",
        "gasto:2:1": "Limpieza - SINTETICA - CUIT 20-33333333-3 - Otros Nº 4455",
    }
    for clave, concepto in conceptos.items():
        valor(leidos, clave)["concepto"] = concepto
    return leidos


def test_cada_gasto_dice_su_comprobante() -> None:
    c = por_numero(_con_comprobantes(noviembre()))[11]
    assert c.estado == EstadoControl.BIEN
    assert c.texto == "Los 3 gastos dicen su comprobante y ninguna factura se repite."


def test_una_factura_en_dos_gastos_es_para_mirar() -> None:
    leidos = _con_comprobantes(noviembre())
    valor(leidos, "gasto:1:2")["concepto"] = (
        "Luz otra vez - EDENOR SINTETICA - CUIT 30-11111111-1 - Factura B Nº 0001-00001234"
    )
    c = por_numero(leidos)[11]
    assert len(c.lineas) == 1
    assert c.lineas[0].startswith("Factura B Nº 0001-00001234 figura en dos gastos: «Luz - EDENOR")
    assert "» y «Luz otra vez - EDENOR" in c.lineas[0]


def test_la_misma_factura_de_otro_proveedor_no_se_repite() -> None:
    leidos = _con_comprobantes(noviembre())
    valor(leidos, "gasto:1:2")["concepto"] = (
        "Agua - AYSA SINTETICA - CUIT 30-22222222-2 - Factura B Nº 0001-00001234"
    )
    assert por_numero(leidos)[11].estado == EstadoControl.BIEN


def test_un_numero_en_cero_no_cuenta_como_repetido() -> None:
    """Un número sin cifras distintas de cero no identifica un comprobante (así quedan las
    liquidaciones anonimizadas)."""
    leidos = _con_comprobantes(noviembre())
    for clave in ("gasto:1:1", "gasto:1:2"):
        valor(leidos, clave)["concepto"] = "X - CUIT 30-00000000-0 - Factura B Nº 0001-00000000"
    assert por_numero(leidos)[11].estado == EstadoControl.BIEN


def test_el_detalle_del_sueldo_y_un_gasto_en_cero_no_piden_comprobante() -> None:
    leidos = _con_comprobantes(noviembre())
    valor(leidos, "rubro:2")["nombre"] = "DETALLE DE SUELDO Y CARGAS SOCIALES"
    valor(leidos, "gasto:2:1")["concepto"] = "Sueldo básico, cargas, total neto"
    valor(leidos, "gasto:1:2")["concepto"] = "Sin movimientos"
    valor(leidos, "gasto:1:2")["total"] = "0.00"
    c = por_numero(leidos)[11]
    assert c.estado == EstadoControl.BIEN, c.lineas
    assert c.texto == "El gasto dice su comprobante."


def test_una_letra_ilegible_igual_cuenta_como_comprobante() -> None:
    """Medido sobre las anonimizadas: la letra de la factura puede quedar ilegible («[N141]»)."""
    leidos = _con_comprobantes(noviembre())
    valor(leidos, "gasto:1:1")["concepto"] = "Limpieza - Factura [N141] Nº 0001-00000001"
    assert por_numero(leidos)[11].estado == EstadoControl.BIEN
    assert comprobante_de("Limpieza - Factura [N141] Nº 0001-00000001") == (
        "Factura [N141] Nº 0001-00000001"
    )
