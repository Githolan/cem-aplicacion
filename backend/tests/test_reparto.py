"""Motor de reparto: casos conocidos y propiedades (documento 13, §7.3)."""

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.liquidacion.reparto import RepartoBloqueado, repartir

D = Decimal


def test_tres_partes_iguales_reparte_el_centavo_por_unidad_id():
    assert repartir(D("100.00"), {"c": D(1), "a": D(1), "b": D(1)}) == {
        "a": D("33.34"), "b": D("33.33"), "c": D("33.33"),
    }  # fmt: skip


def test_coeficientes_de_un_edificio():
    r = repartir(D("250000.00"), {"1A": D("0.3125"), "1B": D("0.2875"), "PB": D("0.40")})
    assert r == {"1A": D("78125.00"), "1B": D("71875.00"), "PB": D("100000.00")}


def test_total_negativo_se_reparte_con_el_mismo_signo():
    r = repartir(D("-10.00"), {"a": D(1), "b": D(2)})
    assert r == {"a": D("-3.33"), "b": D("-6.67")} and sum(r.values()) == D("-10.00")


def test_unidad_con_peso_cero_no_paga():
    assert repartir(D("9.99"), {"a": D(0), "b": D(1)}) == {"a": D("0.00"), "b": D("9.99")}


@pytest.mark.parametrize(
    "total,pesos",
    [
        (D("10.00"), {}),
        (D("10.00"), {"a": D(0), "b": D(0)}),
        (D("10.00"), {"a": D(-1), "b": D(2)}),
        (D("10.00"), {"a": D("NaN")}),
        (D("10.001"), {"a": D(1)}),
        (10, {"a": D(1)}),
    ],
)
def test_grupo_sin_pesos_validos_bloquea(total, pesos):
    with pytest.raises(RepartoBloqueado):
        repartir(total, pesos)


centavos = st.integers(min_value=-(10**12), max_value=10**12).map(lambda c: D(c) / 100)
peso = st.decimals(min_value=0, max_value=1000, places=6, allow_nan=False, allow_infinity=False)
grupo = st.dictionaries(
    st.text("abcdefghij0123456789", min_size=1, max_size=4), peso, min_size=1, max_size=40
).filter(lambda p: sum(p.values()) > 0)


@given(centavos, grupo)
def test_conserva_el_total(total, pesos):
    assert sum(repartir(total, pesos).values()) == total


@given(centavos, grupo)
def test_cada_parte_esta_a_menos_de_un_centavo_de_la_exacta(total, pesos):
    suma = sum(pesos.values())
    for u, parte in repartir(total, pesos).items():
        assert abs(parte - total * pesos[u] / suma) < D("0.01")
        assert parte == parte.quantize(D("0.01"))


@given(centavos, grupo)
def test_es_determinista_e_independiente_del_orden(total, pesos):
    invertido = dict(reversed(list(pesos.items())))
    assert repartir(total, pesos) == repartir(total, pesos) == repartir(total, invertido)


@given(centavos, grupo)
def test_monotonia_respecto_de_los_pesos(total, pesos):
    r = repartir(total, pesos)
    for u in pesos:
        for v in pesos:
            if pesos[u] > pesos[v]:
                assert abs(r[u]) >= abs(r[v])
