"""La tabla de verdad de 2b-3 como datos (tabla de verdad, §2.3, §3 y §4; §6, puntos 3 y 4).

Cada fila de `tabla_de_verdad.csv` es un caso: figura, modo, recurso, situación del mandato,
administrador vigente, y el resultado y el motivo esperados. La prueba recorre el archivo
entero. La mutación demuestra que la tabla no pasa por casualidad: una celda o una política
alteradas a propósito hacen fallar la verificación.
"""

from collections.abc import Mapping
from dataclasses import replace

import pytest
from cem_contracts.estados import ModoOperacion
from cem_contracts.politicas import Politica

from app.autorizacion.evaluador import evaluar
from app.autorizacion.politicas import POLITICAS
from tests.escenarios_autorizacion import Fila, escenario, leer_tabla

FILAS = leer_tabla()


def discrepancia(fila: Fila, politicas: Mapping[str, Politica] = POLITICAS) -> str | None:
    contexto, entidad = escenario(fila)
    d = evaluar(fila.accion, contexto, entidad, politicas)
    if (d.estado, d.motivo_interno) != (fila.esperado, fila.motivo):
        obtenido = f"{d.estado} {d.motivo_interno}"
        return f"{fila}: esperado {fila.esperado} {fila.motivo}, obtenido {obtenido}"
    if d.permitido != (fila.esperado == 200):
        return f"{fila}: permitido={d.permitido} no coincide con el estado {d.estado}"
    return None


def verificar(filas: list[Fila], politicas: Mapping[str, Politica] = POLITICAS) -> list[str]:
    return [m for f in filas if (m := discrepancia(f, politicas)) is not None]


@pytest.mark.parametrize("fila", FILAS, ids=lambda f: f"{f.numero}-{f.caso}-{f.accion}")
def test_fila_de_la_tabla(fila):
    assert discrepancia(fila) is None, discrepancia(fila)


def test_la_tabla_cubre_cada_celda_y_cada_regla():
    casos = {f.caso for f in FILAS}
    assert {f"R{n}" for n in range(1, 13)} <= casos
    # §2.3: 20 filas de acciones por 6 figuras, más las filas extra de modo.
    assert sum(f.caso == "§2.3" for f in FILAS) >= 120
    # Cada acción declarada aparece al menos una vez.
    declaradas = set(POLITICAS) - {"consorcios.mis"}
    assert declaradas <= {f.accion for f in FILAS}
    # Hay de los tres resultados y cada regla tiene un positivo o un negativo explícito.
    assert {f.esperado for f in FILAS} == {200, 403, 404}


def test_mutacion_de_una_celda_del_csv_falla():
    assert verificar(FILAS) == []  # control: la tabla original pasa entera
    i = next(i for i, f in enumerate(FILAS) if f.accion == "documentos.ver" and f.figura == "I")
    mutadas = list(FILAS)
    mutadas[i] = replace(FILAS[i], esperado=200, motivo="concedido_por_vinculo")
    fallas = verificar(mutadas)
    assert len(fallas) == 1 and f"fila {FILAS[i].numero} " in fallas[0]


def test_mutacion_de_una_politica_falla():
    # Liquidación también en Consejo: las filas de "AD en Consejo, filtro de modo" fallan.
    politicas = dict(POLITICAS)
    p = politicas["liquidacion.preparar"]
    politicas["liquidacion.preparar"] = p.model_copy(
        update={"modos": [*p.modos, ModoOperacion.CONSEJO]}
    )
    fallas = verificar(FILAS, politicas)
    assert fallas and all("liquidacion.preparar" in m for m in fallas)


def test_mutacion_quitar_una_separacion_falla():
    politicas = dict(POLITICAS)
    p = politicas["mandatos.alta"]
    politicas["mandatos.alta"] = p.model_copy(update={"separacion": []})
    fallas = verificar(FILAS, politicas)
    assert any("no_es_su_propio_mandato" in m for m in fallas)
