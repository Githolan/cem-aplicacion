"""El panel del Consejo y el resumen del propietario leídos de las liquidaciones oficiales (`0120`;
diseño P0 S3, H4a §4 y §5, plan §6.7 «Gráficos»).

Contra servicios reales: PostgreSQL, el almacén S3 de pruebas y ClamAV. La liquidación es la
inventada de H5-1 (`test_mis_expensas.liquidacion`): rubro 1 por 1.500,50, rubro 2 por 1.000,00,
«Gastos Comunes» por 2.000,00, «AySA» por 500,50 y el total del gasto por 2.500,50. La escena es la
de `0119` (Modo Consejo). Datos SINTETICOS / NO REAL.
"""

from dataclasses import replace
from decimal import Decimal

import pytest
from alembic import command
from cem_contracts.importacion_liquidacion import PERIODOS_DE_GASTO
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db import transaccion
from tests.test_importacion_liquidacion_0113 import bucket, firmas_de_la_imagen
from tests.test_liquidacion_oficial_0119 import (
    OCTUBRE,
    SEPTIEMBRE,
    confirmada,
    confirmar,
    de_octubre,
    escena,
    get,
    importar,
)
from tests.test_mis_expensas import liquidacion
from tests.test_vinculo_0014 import base_aparte

pytestmark = [pytest.mark.db, pytest.mark.redis]
# ruff: noqa: F811

__all__ = ["base_aparte", "bucket", "escena", "firmas_de_la_imagen"]

MESES = ("Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre",
         "Octubre", "Noviembre", "Diciembre")  # fmt: skip
PANEL = (f"panel_gasto_liquidado_por_periodo({PERIODOS_DE_GASTO})",
         f"panel_gasto_liquidado_por_rubro({PERIODOS_DE_GASTO})",
         "panel_diferencias_liquidacion_vigente()")  # fmt: skip
GRUPOS = {"Gastos Comunes": Decimal("2000.00"), "AySA": Decimal("500.50")}
"""Los totales por grupo de la liquidación inventada."""
RUBROS = [(1, Decimal("1500.50")), (2, Decimal("1000.00"))]
TOTAL = Decimal("2500.50")
VENCIMIENTOS = ["2026-10-13", "2026-10-23"]
"""Los de la liquidación inventada (`0128` los suma al panel); el cambio de mes no los toca."""


def del_periodo(anio: int, mes: int, **kw):
    """La liquidación inventada, de otro mes: cambia el mes y el año del período, no los
    vencimientos."""
    nombre = MESES[mes - 1]
    cambios = {"Septiembre": nombre, "2026": str(anio), "Septiembre/2026": f"{nombre}/{anio}"}
    return [
        replace(p, palabras=tuple(replace(w, texto=cambios.get(w.texto, w.texto))
                                  for w in p.palabras))
        for p in liquidacion(**kw)
    ]  # fmt: skip


def panel(e, quien: str) -> dict:
    r = get(e, quien, "panel")
    assert r.status_code == 200, r.text
    return r.json()


def gasto(datos: dict) -> list[tuple]:
    return [
        (f["periodo"], None if f["total"] is None else Decimal(f["total"]),
         {g["nombre"]: Decimal(g["total"]) for g in f["grupos"]})
        for f in datos["gasto_liquidado"]
    ]  # fmt: skip


def rubros(datos: dict) -> list[tuple]:
    return [(f["periodo"], f["numero"], Decimal(f["total"])) for f in datos["rubros_liquidados"]]


def nombres_de_los_rubros(e, importacion: str) -> dict[int, str]:
    """Los nombres de los rubros como quedaron en la liquidación oficial (su propia lectura)."""
    campos = get(e, "mc", "liquidacion-oficial", importacion).json()["campos"]
    return {f["valor"]["numero"]: f["valor"]["nombre"] for f in campos if f["tipo"] == "rubro"}


# --- El panel completo (fiscalizacion.ver_panel) ------------------------------------------------


def test_el_panel_suma_solo_las_oficiales_vigentes_con_sus_valores_exactos(
    escena, bucket, motor_app, motor_migrate
):
    e = escena
    vacio = panel(e, "mc")
    assert (vacio["gasto_liquidado"], vacio["rubros_liquidados"], vacio["liquidacion_vigente"]) == (
        [], [], None)  # fmt: skip
    a = confirmada(e, motor_app)
    # La reemplazante corrige el rubro 2 y el total; la reemplazada deja de contar.
    b = importar(e, motor_app, corregir={"rubro:2": {"total": "999.99"},
                                          "total:gastos": {"total": "2500.49"}})  # fmt: skip
    assert confirmar(e, b, {"reemplaza_a": a, "motivo": "Corrigió el rubro 2"}).status_code == 200
    octubre = confirmada(e, motor_app, de_octubre())
    importar(e, motor_app, del_periodo(2026, 11))  # para confirmar: nunca cuenta (RN30)
    nombres = nombres_de_los_rubros(e, octubre)
    assert set(nombres) == {1, 2}
    for quien in ("mc", "ad"):
        d = panel(e, quien)
        assert gasto(d) == [(SEPTIEMBRE, Decimal("2500.49"), GRUPOS), (OCTUBRE, TOTAL, GRUPOS)]
        assert rubros(d) == [
            (SEPTIEMBRE, 1, Decimal("1500.50")), (SEPTIEMBRE, 2, Decimal("999.99")),
            (OCTUBRE, 1, Decimal("1500.50")), (OCTUBRE, 2, Decimal("1000.00")),
        ]  # fmt: skip
        assert [f["nombre"] for f in d["rubros_liquidados"]] == [nombres[1], nombres[2]] * 2
        # Sin perfil normativo vinculado (`0125`), nada que señalar del enlace.
        assert d["liquidacion_vigente"] == {"id": octubre, "periodo": OCTUBRE, "diferencias": 0,
                                            "enlace_documentacion": None,
                                            "vencimientos": VENCIMIENTOS}  # fmt: skip
        # En la liquidación inventada, la suma de los rubros es el total que informa.
        assert sum(t for p, _, t in rubros(d) if p == OCTUBRE) == TOTAL
        # Las secciones de las órdenes siguen ahí, vacías en Modo Consejo.
        assert (d["ordenes_por_estado"], d["gasto_pagado"], d["comprobantes_faltantes"]) == (
            [], [], [])  # fmt: skip


def test_lo_que_no_cierra_es_lo_de_la_vigente(escena, bucket, motor_app, motor_migrate):
    e = escena
    # Septiembre con un gasto que no suma: dos diferencias en la lectura (`0113`).
    septiembre = confirmada(e, motor_app, liquidacion(gasto_luz=("1.000,00", "0,00", "1.000,05")))
    assert panel(e, "mc")["liquidacion_vigente"] == {
        "id": septiembre, "periodo": SEPTIEMBRE, "diferencias": 2,
        "enlace_documentacion": None, "vencimientos": VENCIMIENTOS}  # fmt: skip
    # Octubre, sin diferencias, pasa a ser la vigente: lo de septiembre ya no se cuenta.
    octubre = confirmada(e, motor_app, de_octubre())
    assert panel(e, "mc")["liquidacion_vigente"] == {
        "id": octubre, "periodo": OCTUBRE, "diferencias": 0,
        "enlace_documentacion": None, "vencimientos": VENCIMIENTOS}  # fmt: skip


def test_la_ventana_son_los_ultimos_doce_periodos_con_liquidacion(
    escena, bucket, motor_app, motor_migrate
):
    """Trece liquidaciones oficiales en catorce meses (sin la de enero de 2026): entran las doce
    últimas que existen, no los últimos doce meses del calendario."""
    e = escena
    meses = [(2025, m) for m in range(8, 13)] + [(2026, m) for m in range(2, 10)]
    assert len(meses) == PERIODOS_DE_GASTO + 1
    for anio, mes in meses:
        confirmada(e, motor_app, del_periodo(anio, mes))
    esperados = [f"{anio}-{mes:02d}-01" for anio, mes in meses[1:]]
    d = panel(e, "mc")
    assert [p for p, _, _ in gasto(d)] == esperados
    assert sorted({p for p, _, _ in rubros(d)}) == esperados
    assert d["liquidacion_vigente"]["periodo"] == esperados[-1]
    serie = get(e, "prop", "resumen").json()["gasto_por_periodo"]
    assert [(f["periodo"], Decimal(f["total"])) for f in serie] == [(p, TOTAL) for p in esperados]


def test_el_panel_lo_ven_solo_quienes_tienen_la_facultad_y_la_base_repite_la_barrera(
    escena, bucket, motor_app, motor_migrate
):
    e = escena
    confirmada(e, motor_app)
    assert get(e, "mc", "panel").status_code == 200  # control
    for quien in ("prop", "inq", "sin_confirmar"):
        assert get(e, quien, "panel").status_code == 403, quien
    # La base: la propietaria no lee ninguna de las tres; el Consejo sí (control).
    for funcion in PANEL:
        with (
            pytest.raises(DBAPIError) as error,
            transaccion(motor_app, persona_id=e["prop"], consorcio_id=e["cons"]) as c,
        ):
            c.execute(text(f"select * from nucleo.{funcion}")).all()
        assert error.value.orig.sqlstate == "42501", funcion
        with transaccion(motor_app, persona_id=e["mc"], consorcio_id=e["cons"]) as c:
            assert c.execute(text(f"select * from nucleo.{funcion}")).all(), funcion


# --- El resumen del propietario (fiscalizacion.ver_resumen) -------------------------------------


def test_el_resumen_trae_el_total_de_cada_periodo_de_las_vigentes_y_nada_mas(
    escena, bucket, motor_app, motor_migrate
):
    e = escena
    a = confirmada(e, motor_app)
    b = importar(e, motor_app, corregir={"total:gastos": {"total": "2400.49"}})
    assert confirmar(e, b, {"reemplaza_a": a, "motivo": "Corrigió el total"}).status_code == 200
    confirmada(e, motor_app, de_octubre())
    importar(e, motor_app, del_periodo(2026, 11))  # para confirmar: no cuenta
    for params in (None, {"periodo": SEPTIEMBRE}):
        r = get(e, "prop", "resumen", params=params)
        assert r.status_code == 200, r.text
        serie = r.json()["gasto_por_periodo"]
        assert [(f["periodo"], Decimal(f["total"])) for f in serie] == [
            (SEPTIEMBRE, Decimal("2400.49")), (OCTUBRE, TOTAL)]  # fmt: skip
        assert all(set(f) == {"periodo", "total"} for f in serie)
    assert set(get(e, "prop", "resumen").json()) == {"periodos", "liquidacion", "gasto_por_periodo"}


def test_la_serie_del_resumen_la_lee_solo_el_propietario_confirmado(
    escena, bucket, motor_app, motor_migrate
):
    e = escena
    confirmada(e, motor_app)
    funcion = f"select * from nucleo.resumen_gasto_por_periodo({PERIODOS_DE_GASTO})"
    with transaccion(motor_app, persona_id=e["prop"], consorcio_id=e["cons"]) as c:
        assert len(c.execute(text(funcion)).all()) == 1  # control
    for quien in ("inq", "sin_confirmar", "ad"):
        with (
            pytest.raises(DBAPIError) as error,
            transaccion(motor_app, persona_id=e[quien], consorcio_id=e["cons"]) as c,
        ):
            c.execute(text(funcion)).all()
        assert error.value.orig.sqlstate == "42501", quien


# --- Privilegios y migración ----------------------------------------------------------------


RUTAS = (
    "nucleo.panel_gasto_liquidado_por_periodo(integer)",
    "nucleo.panel_gasto_liquidado_por_rubro(integer)",
    "nucleo.panel_diferencias_liquidacion_vigente()",
    "nucleo.resumen_gasto_por_periodo(integer)",
)
INTERNAS = ("nucleo.ultimas_liquidaciones_oficiales(uuid, integer)",)


def test_las_lecturas_son_solo_para_cem_app_y_la_ventana_para_nadie(motor_migrate):
    puede = text("select has_function_privilege(:r, :f, 'EXECUTE')")
    with motor_migrate.connect() as c:
        for f in RUTAS:
            assert c.execute(puede, {"r": "cem_app", "f": f}).scalar_one() is True, f
            assert c.execute(puede, {"r": "cem_ro", "f": f}).scalar_one() is False, f
        for f in INTERNAS:
            assert c.execute(puede, {"r": "cem_app", "f": f}).scalar_one() is False, f


def test_0120_ida_y_vuelta(base_aparte):
    cfg, motor = base_aparte

    def funciones() -> list[str]:
        with motor.connect() as c:
            return sorted(c.execute(text(
                "select p.oid::regprocedure::text from pg_proc p"
                " join pg_namespace n on n.oid = p.pronamespace where n.nspname = 'nucleo'"
                " and p.proname in ('panel_gasto_liquidado_por_periodo',"
                " 'panel_gasto_liquidado_por_rubro', 'panel_diferencias_liquidacion_vigente',"
                " 'resumen_gasto_por_periodo', 'ultimas_liquidaciones_oficiales')"
            )).scalars())  # fmt: skip

    command.upgrade(cfg, "0119")
    assert funciones() == []
    command.upgrade(cfg, "0120")
    assert funciones() == sorted(f.replace(" ", "") for f in RUTAS + INTERNAS)
    command.downgrade(cfg, "0119")
    assert funciones() == []
    command.upgrade(cfg, "0120")  # control
    assert funciones() == sorted(f.replace(" ", "") for f in RUTAS + INTERNAS)
