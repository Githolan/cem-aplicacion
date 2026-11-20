"""Recálculo de la liquidación importada, función pura (H5-4, `recalculo`). Datos SINTETICOS."""

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from cem_contracts.importacion_liquidacion import EstadoGrupoRecalculado as Estado

from app.liquidacion.recalculo import Base, Oficial, clave_de, recalcular

A, B, C = (UUID(int=1), UUID(int=2), UUID(int=3))
ESQUEMA = uuid4()
DESDE = date(2024, 1, 1)
D1000 = Decimal("1000.00")


def base(porcentajes: dict[UUID, str | None], *, clave="gastos comunes", esquema=ESQUEMA):
    nombres = {A: "PB-A", B: "1-A", C: "1-B"}
    return [
        Base(clave=clave, grupo="expensas_comunes", esquema_id=esquema, esquema_version=1,
             vigente_desde=DESDE, unidad_id=u, designacion=nombres[u],
             porcentaje=None if p is None else Decimal(p))
        for u, p in porcentajes.items()
    ]  # fmt: skip


def total(**grupos: str) -> Oficial:
    nombres = {"gastos_comunes": "Gastos Comunes", "aysa": "AySA"}
    return Oficial("total", "total:gastos",
                   {"grupos": {nombres[g]: v for g, v in grupos.items()}, "total": "0.00"},
                   None)  # fmt: skip


def unidad(u: UUID | None, uf: str, monto: str, porcentaje: str | None = "33.33") -> Oficial:
    return Oficial("importe_unidad", f"importe_unidad:{uf}",
                   {"uf": uf, "depto": "X", "grupos": {"Gastos Comunes": {
                       "porcentaje": porcentaje, "monto": monto}}}, u)  # fmt: skip


COEFICIENTES = {A: "50.000000", B: "30.000000", C: "20.000000"}


def test_lo_que_coincide_no_se_senala():
    oficiales = [total(gastos_comunes="1000.00"), unidad(A, "1", "500.00"),
                 unidad(B, "2", "300.00"), unidad(C, "3", "200.00")]  # fmt: skip
    (g,) = recalcular(oficiales, base(COEFICIENTES))
    resumen = (g.nombre, g.estado, g.grupo, g.total, g.senaladas)
    assert resumen == ("Gastos Comunes", Estado.RECALCULADO, "expensas_comunes", D1000, 0)
    assert [(x.designacion, x.esperado, x.informado, x.diferencia) for x in g.unidades] == [
        ("1-A", Decimal("300.00"), Decimal("300.00"), Decimal("0.00")),
        ("1-B", Decimal("200.00"), Decimal("200.00"), Decimal("0.00")),
        ("PB-A", Decimal("500.00"), Decimal("500.00"), Decimal("0.00")),
    ]


def test_un_centavo_no_se_senala_y_dos_si():
    """D5-A: dos maneras correctas de repartir los centavos difieren a lo sumo en uno."""
    oficiales = [total(gastos_comunes="100.01"), unidad(A, "1", "50.00"),
                 unidad(B, "2", "30.00"), unidad(C, "3", "20.03")]  # fmt: skip
    (g,) = recalcular(oficiales, base(COEFICIENTES))
    por = {x.designacion: x for x in g.unidades}
    # El reparto da el centavo de más a la mayor parte (PB-A: 50,01).
    assert por["PB-A"].esperado == Decimal("50.01") and not por["PB-A"].senalada
    assert por["1-B"].diferencia == Decimal("0.03") and por["1-B"].senalada
    assert g.senaladas == 1


def test_la_base_de_la_diferencia_queda_a_la_vista():
    oficiales = [total(gastos_comunes="1000.00"), unidad(A, "1", "400.00", "40.00"),
                 unidad(B, "2", "350.00", "35.00"), unidad(C, "3", "250.00", "25.00")]  # fmt: skip
    (g,) = recalcular(oficiales, base(COEFICIENTES))
    pa = next(x for x in g.unidades if x.designacion == "PB-A")
    assert (pa.coeficiente, pa.porcentaje_informado, pa.esperado, pa.informado, pa.diferencia) == (
        Decimal("50.000000"), Decimal("40.00"), Decimal("500.00"), Decimal("400.00"),
        Decimal("-100.00"))  # fmt: skip
    assert g.senaladas == 3


def test_una_unidad_que_no_figura_y_una_fila_fuera_del_padron():
    oficiales = [total(gastos_comunes="1000.00"), unidad(A, "1", "500.00"),
                 unidad(B, "2", "300.00"), unidad(None, "9", "200.00")]  # fmt: skip
    (g,) = recalcular(oficiales, base(COEFICIENTES))
    falta = next(x for x in g.unidades if x.designacion == "1-B")
    assert (falta.informado, falta.esperado, falta.diferencia, falta.senalada) == (
        None, Decimal("200.00"), Decimal("-200.00"), True)  # fmt: skip
    ajena = g.unidades[-1]
    resumen = (ajena.unidad_id, ajena.designacion, ajena.esperado, ajena.informado, ajena.senalada)
    assert resumen == (None, "UF 9 X", None, Decimal("200.00"), True)


@pytest.mark.parametrize(
    ("filas", "oficiales", "estado"),
    [
        pytest.param([], [total(gastos_comunes="1.00")], Estado.SIN_CORRESPONDENCIA, id="sin"),
        pytest.param(base(COEFICIENTES, esquema=None)[:1], [total(gastos_comunes="1.00")],
                     Estado.SIN_ESQUEMA, id="sin esquema"),
        pytest.param(base(COEFICIENTES), [unidad(A, "1", "1.00")], Estado.SIN_TOTAL,
                     id="sin total confirmado"),
        pytest.param(base({A: "50", B: None}), [total(gastos_comunes="1.00")],
                     Estado.COEFICIENTES_INCOMPLETOS, id="falta un coeficiente"),
        pytest.param(base({A: "0", B: "0"}), [total(gastos_comunes="1.00")],
                     Estado.COEFICIENTES_INCOMPLETOS, id="suman cero"),
    ],
)  # fmt: skip
def test_lo_que_no_se_puede_recalcular_dice_por_que(filas, oficiales, estado):
    (g,) = recalcular(oficiales, filas)
    assert (g.estado, g.unidades, g.senaladas) == (estado, [], 0)


def test_sin_grupos_no_hay_nada_que_recalcular():
    """Una liquidación leída con IA informa el total, no el reparto por grupo."""
    ia = [Oficial("total", "total:gastos", {"total": "10.00"}, None),
          Oficial("importe_unidad", "importe_unidad:1", {"uf": "1", "depto": "", "total": "5.00"},
                  A)]  # fmt: skip
    assert recalcular(ia, base(COEFICIENTES)) == []


def test_el_grupo_se_compara_como_las_designaciones():
    assert clave_de("  Gastos   Comunes ") == clave_de("gastos comunes") == "gastos comunes"
    oficiales = [total(gastos_comunes="10.00"), total(aysa="5.00")]
    estados = [(g.nombre, g.estado) for g in recalcular(oficiales, base(COEFICIENTES))]
    # Por nombre, no en el orden del documento.
    assert estados == [("AySA", Estado.SIN_CORRESPONDENCIA), ("Gastos Comunes", Estado.RECALCULADO)]
