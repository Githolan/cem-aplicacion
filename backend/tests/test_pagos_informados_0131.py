"""Pagos informados, tercer corte (`0131`; PI-S3 del diseño «Pagos informados», reglas PI-7 y
PI-8): el cotejo con la liquidación oficial, la bandeja y el aviso diario.

Contra servicios reales: PostgreSQL, el almacén S3 de pruebas y ClamAV. La escena es la de
`test_pagos_informados_0130.py` (Modo Consejo). La liquidación es la inventada de H5-1
(`test_mis_expensas.liquidacion`) llevada a noviembre de 2026 (`del_periodo`): «PB-A» (unidad 001)
trae 1.000,00 en la columna «Pagos», «1-A» (unidad 002) 0,00 y «1-B» (003) 10,00, que
`noviembre` pasa a 0,00; los otros importes salen de
corregir el dato oficial al confirmar. «1° A» no figura. Por la decisión Q2 del propietario cuentan
los pagos de octubre (SIN VERIFICAR contra una liquidación real). Cada negación lleva su control:
el mismo pedido de quien sí puede pasa. Datos SINTETICOS / NO REAL.
"""

import importlib.util
import json
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from cem_contracts import pagos_informados as contrato
from cem_contracts import politicas
from cem_contracts.ayuda import ENCABEZADO_AYUDA
from psycopg.errors import InsufficientPrivilege
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.consorcios import avisos_consejo
from app.db import transaccion
from tests.test_avisos_y_sin_explicar_0098 import avisar, datos_de_dinero
from tests.test_bandeja_consejo_0126 import LECTURA as PENDIENTES
from tests.test_bandeja_consejo_0126 import nombre, renglones, textos
from tests.test_liquidacion_oficial_0119 import (  # noqa: F401  (los fixtures se importan)
    SEPTIEMBRE,
    bucket,
    confirmada,
    confirmar,
    firmas_de_la_imagen,
    importar,
    ruta,
)
from tests.test_pagos_informados_0129 import ayuda
from tests.test_pagos_informados_0130 import (  # noqa: F401  (`e` es un fixture)
    MOTIVO,
    e,
    efectivo,
    paso,
)
from tests.test_panel_liquidacion_oficial_0120 import del_periodo
from tests.test_reclamos_liquidacion_0123 import correo_de
from tests.test_rutas_egresos import cliente
from tests.test_vinculo_0014 import base_aparte

pytestmark = [pytest.mark.db, pytest.mark.redis]
# ruff: noqa: F811

__all__ = ["base_aparte", "bucket", "firmas_de_la_imagen"]

MIGRACION = (
    Path(__file__).resolve().parent.parent / "migrations/versions/0131_pagos_informados_cotejo.py"
)
NOVIEMBRE = "2026-11-01"
OCTUBRE = "2026-10-01"
LEER = text("select * from nucleo.cotejo_pagos_informados_lectura(:p, :a, 'api', :s)")
UNA_DIFERENCIA = "en una unidad lo informado y confirmado no suma lo que dice la liquidación"


def _migracion():
    spec = importlib.util.spec_from_file_location("migracion_0131", MIGRACION)
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def noviembre(e: dict, motor_app, **pagos: str | None) -> str:
    """La liquidación de noviembre confirmada, con la columna «Pagos» de cada unidad corregida
    (`uf` = valor; «001» es «PB-A», «002» es «1-A» y «003» es «1-B»). «1-B» trae 10,00: salvo
    que se pida otra cosa, pasa a 0,00 para que solo difiera lo que la prueba arma."""
    return confirmada(
        e, motor_app, del_periodo(2026, 11), corregir=_pagos({"u003": "0.00"} | pagos)
    )


def _pagos(pagos: dict) -> dict:
    return {f"saldo_unidad:{uf.lstrip('u')}": {"pagos": v} for uf, v in pagos.items()}


def confirmado(e: dict, quien: str, unidad: str, importe: str, fecha: str, **cambios) -> dict:
    """Un pago en efectivo informado por `quien` y confirmado por la administración."""
    pago = efectivo(e, quien, unidad, importe=importe, fecha_pago=fecha, **cambios)
    r = paso(e, "ad", pago["id"], "confirmacion")
    assert r.status_code == 200, r.text
    return r.json()


def cotejo(e: dict, quien: str, periodo: str | None = None, encabezados: dict | None = None):
    with cliente(e[quien], e[f"sesion_{quien}"]) as c:
        return c.get(ruta(e, "pagos-informados", "cotejo"),
                     params={} if periodo is None else {"periodo": periodo},
                     headers=encabezados or {})  # fmt: skip


def leido(e: dict, quien: str = "mc", periodo: str | None = None) -> dict:
    r = cotejo(e, quien, periodo)
    assert r.status_code == 200, r.text
    return r.json()


def _d(valor) -> Decimal | None:
    return None if valor is None else Decimal(valor)


def unidades(datos: dict) -> dict[str, tuple]:
    """Designación → (resultado, informado, confirmados, liquidación, diferencia, sin pagos)."""
    return {
        u["unidad_designacion"]: (u["resultado"], _d(u["informado_confirmado"]),
                                  u["pagos_confirmados"], _d(u["liquidacion"]),
                                  _d(u["diferencia"]), u["sin_pagos_informados"])
        for u in datos["liquidacion"]["unidades"]
    }  # fmt: skip


# --- Una sola fuente ------------------------------------------------------------------------------


def test_la_migracion_copia_el_contrato_y_la_politica():
    m = _migracion()
    assert m.MESES_ANTES == contrato.MESES_ANTES_COLUMNA_PAGOS
    assert m.RESULTADOS == tuple(contrato.ResultadoCotejoLiquidacion)
    assert m.ESTADO_QUE_CUENTA == contrato.EstadoPagoInformado.CONFIRMADO
    leidas = {p["accion"]: p for archivo_ in politicas.archivos()
              for p in json.loads(archivo_.read_text(encoding="utf-8"))["politicas"]}  # fmt: skip
    politica = leidas[m.ACCION_LECTURA]
    assert (politica["facultad"], tuple(politica["modos"])) == (m.FACULTAD_PANEL, m.MODOS)


# --- PI-7: la regla ------------------------------------------------------------------------------


def test_coincide_difiere_y_sin_datos(e, motor_app):
    """«PB-A»: dos pagos de octubre que suman lo de la liquidación. «1-A»: lo confirmado suma
    250,00 y la liquidación dice 300,00. «1° A»: no figura en la liquidación."""
    confirmado(e, "inq", "PB-A", "600.00", "2026-10-05")
    confirmado(e, "oc", "PB-A", "400.00", "2026-10-20")
    confirmado(e, "prop", "1-A", "250.00", "2026-10-10")
    i = noviembre(e, motor_app, u002="300.00")
    for quien in ("mc", "ad"):
        datos = leido(e, quien)["liquidacion"]
        assert (datos["importacion_id"], datos["periodo"]) == (i, NOVIEMBRE)
        assert (datos["pagos_desde"], datos["pagos_hasta"]) == (OCTUBRE, "2026-10-31")
        u = unidades(leido(e, quien))
        assert u["PB-A"] == ("coincide", Decimal("1000.00"), 2, Decimal("1000.00"),
                             Decimal("0.00"), False)  # fmt: skip
        assert u["1-A"] == ("difiere", Decimal("250.00"), 1, Decimal("300.00"),
                            Decimal("50.00"), False)  # fmt: skip
        assert u["1° A"] == ("sin_datos", Decimal("0.00"), 0, None, None, True)
        assert datos["para_revisar"] == 1


def test_sin_pagos_informados_cero_coincide_y_mas_que_cero_difiere(e, motor_app):
    """Nadie informó nada: «1-A» (0,00 en la liquidación) coincide; «PB-A» (1.000,00) difiere,
    marcada como sin pagos informados para que la pantalla no hable de un error (PI-8)."""
    noviembre(e, motor_app)
    u = unidades(leido(e))
    assert u["1-A"] == ("coincide", Decimal("0.00"), 0, Decimal("0.00"), Decimal("0.00"), True)
    assert u["PB-A"] == ("difiere", Decimal("0.00"), 0, Decimal("1000.00"), Decimal("1000.00"),
                         True)  # fmt: skip
    # Sin pagos confirmados no hay nada que revisar: ni el inicio ni el aviso diario la cuentan.
    assert leido(e)["liquidacion"]["para_revisar"] == 0
    with transaccion(motor_app, persona_id=None) as c:
        filas = {f["persona_id"]: f for f in c.execute(text(PENDIENTES)).mappings()
                 if f["consorcio_id"] == e["cons"]}  # fmt: skip
    assert filas[e["mc"]]["diferencias_cotejo"] == 0
    # Control: con un pago confirmado que no alcanza, sí cuenta.
    confirmado(e, "inq", "PB-A", "900.00", "2026-10-10")
    assert leido(e)["liquidacion"]["para_revisar"] == 1
    with transaccion(motor_app, persona_id=None) as c:
        filas = {f["persona_id"]: f for f in c.execute(text(PENDIENTES)).mappings()
                 if f["consorcio_id"] == e["cons"]}  # fmt: skip
    assert filas[e["mc"]]["diferencias_cotejo"] == 1


@pytest.mark.parametrize("valor", [None, "sin leer", "12.345"])
def test_pagos_nulo_o_que_no_es_un_importe_da_sin_datos(e, motor_app, valor):
    confirmado(e, "prop", "1-A", "300.00", "2026-10-10")
    noviembre(e, motor_app, u002=valor)
    assert unidades(leido(e))["1-A"] == ("sin_datos", Decimal("300.00"), 1, None, None, False)
    # Control: «PB-A», con su importe, sí se coteja.
    assert unidades(leido(e))["PB-A"][0] == "difiere"


def test_solo_cuentan_los_confirmados_del_mes_anterior_y_la_ultima_correccion(e, motor_app):
    """En «1-A»: el primero y el último día de octubre cuentan; el 1 de noviembre (el mes de la
    liquidación) y el 30 de septiembre, no. Uno sin decidir, uno anulado y uno rechazado no
    cuentan; la corrección confirmada del rechazado, sí. Suma 100 + 200 + 50 = 350."""
    confirmado(e, "prop", "1-A", "100.00", "2026-10-01")
    confirmado(e, "prop", "1-A", "200.00", "2026-10-31")
    confirmado(e, "prop", "1-A", "400.00", "2026-11-01")
    confirmado(e, "prop", "1-A", "800.00", "2026-09-30")
    efectivo(e, "prop", "1-A", importe="1600.00", fecha_pago="2026-10-15")  # sin decidir
    rechazado = efectivo(e, "prop", "1-A", importe="3200.00", fecha_pago="2026-10-15")
    assert paso(e, "ad", rechazado["id"], "rechazo", MOTIVO).status_code == 200
    confirmado(e, "prop", "1-A", "50.00", "2026-10-15", corrige_a=rechazado["id"])
    anulado = confirmado(e, "prop", "1-A", "6400.00", "2026-10-16")
    assert paso(e, "mc", anulado["id"], "anulacion", MOTIVO).status_code == 200
    # De otra unidad: no se mezcla.
    confirmado(e, "inq", "PB-A", "12800.00", "2026-10-16")
    noviembre(e, motor_app, u002="350.00", u001="12800.00")
    u = unidades(leido(e))
    assert u["1-A"] == ("coincide", Decimal("350.00"), 3, Decimal("350.00"), Decimal("0.00"),
                        False)  # fmt: skip
    assert u["PB-A"][:3] == ("coincide", Decimal("12800.00"), 1)


def test_se_calcula_al_leer_y_usa_la_liquidacion_vigente(e, motor_app):
    """PI-7: un pago confirmado o anulado después, y una liquidación que reemplaza a la anterior,
    se ven en el cotejo siguiente. Sin período, la del último mes; con período, la de ese mes."""
    assert leido(e) == {"liquidacion": None}
    septiembre = confirmada(e, motor_app)  # sin correcciones: «1-A» trae 0,00
    a = noviembre(e, motor_app, u002="300.00")
    assert unidades(leido(e))["1-A"][0] == "difiere"
    pago = confirmado(e, "prop", "1-A", "300.00", "2026-10-10")
    assert unidades(leido(e))["1-A"][:2] == ("coincide", Decimal("300.00"))
    assert paso(e, "mc", pago["id"], "anulacion", MOTIVO).status_code == 200
    assert unidades(leido(e))["1-A"][:2] == ("difiere", Decimal("0.00"))
    # La reemplaza otra de noviembre que trae 0,00: el cotejo usa la nueva.
    b = importar(e, motor_app, del_periodo(2026, 11), corregir=_pagos({"u003": "0.00"}))
    r = confirmar(e, b, {"reemplaza_a": a, "motivo": "Pagos corregidos SINTETICO"})
    assert r.status_code == 200, r.text
    datos = leido(e)
    assert datos["liquidacion"]["importacion_id"] == b
    assert unidades(datos)["1-A"][0] == "coincide"
    # Con período: septiembre, con la ventana de agosto.
    datos = leido(e, periodo=SEPTIEMBRE)["liquidacion"]
    assert (datos["importacion_id"], datos["pagos_desde"], datos["pagos_hasta"]) == (
        septiembre, "2026-08-01", "2026-08-31")  # fmt: skip
    assert leido(e, periodo=NOVIEMBRE)["liquidacion"]["importacion_id"] == b
    r = cotejo(e, "mc", OCTUBRE)
    assert (r.status_code, r.json()["codigo"]) == (404, "no_encontrado")
    r = cotejo(e, "mc", "2026-11-15")
    assert (r.status_code, r.json()["codigo"]) == (422, "peticion_invalida")


# --- Quién ve qué ---------------------------------------------------------------------------------


def test_el_panel_ve_todas_y_cada_vecino_solo_las_suyas(e, motor_app, motor_migrate):
    confirmado(e, "prop", "1-A", "250.00", "2026-10-10")
    noviembre(e, motor_app, u002="300.00")
    todas = set(unidades(leido(e, "mc")))
    assert {"PB-A", "1-A", "1° A"} <= todas
    assert set(unidades(leido(e, "ad"))) == todas
    # La propietaria: «1° A» y «1-A», con los mismos importes que ve el panel.
    propias = unidades(leido(e, "prop"))
    assert set(propias) == {"1° A", "1-A"}
    assert propias["1-A"] == unidades(leido(e, "mc"))["1-A"]
    assert leido(e, "prop")["liquidacion"]["para_revisar"] == 1
    # La inquilina y la ocupante pasan la barrera, pero no ven la columna «Pagos» (P0-3: es del
    # propietario, como «Mis expensas»).
    for quien in ("inq", "oc"):
        assert set(unidades(leido(e, quien))) == set(), quien
    # Sin un vínculo confirmado ni la facultad: nada (control: la propietaria confirmada sí ve).
    assert cotejo(e, "sin_confirmar").status_code == 403
    assert cotejo(e, "ayu").status_code == 404  # sin vínculo ni mandato en el consorcio
    # Quien ayuda con `consultar_expensas` ve solo lo de la persona asistida.
    con = {ENCABEZADO_AYUDA: ayuda(e, motor_migrate, ["consultar_expensas"])}
    r = cotejo(e, "ayu", encabezados=con)
    assert r.status_code == 200, r.text
    assert set(unidades(r.json())) == {"1° A", "1-A"}


def test_la_base_repite_la_barrera_y_filtra_por_unidad(e, motor_app):
    """La función, sin la API: el vecino recibe solo sus unidades; quien no tiene vínculo, 42501;
    otro consorcio en el ámbito no ve la liquidación de este."""
    noviembre(e, motor_app)
    with transaccion(motor_app, persona_id=e["inq"], consorcio_id=e["cons"]) as c:
        filas = c.execute(LEER, {"p": None, "a": e["inq"], "s": e["sesion_inq"]}).mappings().all()
    assert {f["unidad_designacion"] for f in filas} == {None}  # la inquilina: sin unidades (P0-3)
    with transaccion(motor_app, persona_id=e["prop"], consorcio_id=e["cons"]) as c:
        filas = c.execute(LEER, {"p": None, "a": e["prop"], "s": e["sesion_prop"]}).mappings().all()
    assert {f["unidad_designacion"] for f in filas} == {"1° A", "1-A"}
    with transaccion(motor_app, persona_id=e["sin_confirmar"], consorcio_id=e["cons"]) as c:
        with pytest.raises(DBAPIError) as error:
            c.execute(LEER, {"p": None, "a": e["sin_confirmar"], "s": e["sesion_sin_confirmar"]})
    assert isinstance(error.value.orig, InsufficientPrivilege)
    with transaccion(motor_app, persona_id=e["mc"], consorcio_id=e["cons"]) as c:
        filas = c.execute(LEER, {"p": None, "a": e["mc"], "s": e["sesion_mc"]}).mappings().all()
    assert {"PB-A", "1-A"} <= {f["unidad_designacion"] for f in filas}


def test_otro_consorcio_no_ve_el_cotejo(e, motor_app, motor_migrate):
    i = noviembre(e, motor_app)
    with motor_migrate.begin() as c:
        otro = c.execute(text("insert into nucleo.consorcio (nombre) values ('Otro SINTETICO')"
                              " returning id")).scalar_one()  # fmt: skip
        dentro = c.execute(text("select count(*) from nucleo.cotejo_de(:c, :i)"),
                           {"c": e["cons"], "i": i}).scalar_one()  # fmt: skip
        fuera = c.execute(text("select count(*) from nucleo.cotejo_de(:c, :i)"),
                          {"c": otro, "i": i}).scalar_one()  # fmt: skip
    assert dentro > 0 and fuera == 0
    # Por la ruta, con el ámbito de otro consorcio: la base niega (sin sesión ni modo allí).
    with transaccion(motor_app, persona_id=e["mc"], consorcio_id=otro) as c:
        with pytest.raises(DBAPIError):
            c.execute(LEER, {"p": None, "a": e["mc"], "s": e["sesion_mc"]})


def test_la_ruta_es_solo_para_cem_app_y_la_interna_para_nadie(motor_migrate):
    m = _migracion()
    puede = text("select has_function_privilege(:r, :f, 'EXECUTE')")
    definidor = text("select prosecdef from pg_proc where oid = cast(:f as regprocedure)")
    with motor_migrate.connect() as c:
        for f in m.RUTAS:
            assert c.execute(puede, {"r": "cem_app", "f": f}).scalar_one() is True, f
            assert c.execute(puede, {"r": "cem_ro", "f": f}).scalar_one() is False, f
            assert c.execute(definidor, {"f": f}).scalar_one() is True, f
        for f in m.INTERNAS:
            assert c.execute(puede, {"r": "cem_app", "f": f}).scalar_one() is False, f
            assert c.execute(definidor, {"f": f}).scalar_one() is False, f
        assert c.execute(puede, {"r": "cem_app", "f": m.PENDIENTES}).scalar_one() is True


# --- Bandeja y aviso diario ----------------------------------------------------------------------


def test_la_bandeja_y_el_aviso_diario_cuentan_las_diferencias(e, motor_app, motor_migrate):
    confirmado(e, "prop", "1-A", "250.00", "2026-10-10")
    confirmado(e, "inq", "PB-A", "1000.00", "2026-10-10")
    noviembre(e, motor_app, u002="300.00")  # «1-A» difiere; «PB-A» coincide
    with transaccion(motor_app, persona_id=None) as c:
        filas = {f["persona_id"]: f for f in c.execute(text(PENDIENTES)).mappings()
                 if f["consorcio_id"] == e["cons"]}  # fmt: skip
    assert (filas[e["mc"]]["diferencias_cotejo"], filas[e["ad"]]["diferencias_cotejo"]) == (1, 1)
    assert leido(e)["liquidacion"]["para_revisar"] == 1  # el mismo número que el inicio
    assert avisos_consejo.cuentas_de(dict(filas[e["mc"]])) == {"diferencias_cotejo": 1}
    for quien in ("prop", "inq", "oc"):
        assert e[quien] not in filas, quien
    avisar(motor_app)
    edificio = nombre(motor_migrate, e["cons"])
    [texto] = textos(motor_migrate, e["mc"])
    assert renglones(texto) == [f"- {edificio}: {UNA_DIFERENCIA}."]
    # Solo cuántas: ni la unidad, ni el importe, ni quién informó.
    prohibido = [*e["u"], "250", "300", correo_de(motor_migrate, e["prop"])]
    assert datos_de_dinero(texto, prohibido) == []
    assert datos_de_dinero(f"{texto} 1-A", prohibido) != []  # control del detector
    for quien in ("prop", "inq", "oc"):
        assert textos(motor_migrate, e[quien]) == [], quien
    # Otra diferencia: dos unidades, en plural.
    pagos_b = _pagos({"u002": "300.00", "u001": "900.00", "u003": "0.00"})
    noviembre_b = importar(e, motor_app, del_periodo(2026, 11), corregir=pagos_b)
    vigente = leido(e)["liquidacion"]["importacion_id"]
    r = confirmar(e, noviembre_b, {"reemplaza_a": vigente, "motivo": "Otra SINTETICA"})
    assert r.status_code == 200, r.text
    with transaccion(motor_app, persona_id=None) as c:
        leidas = {f["persona_id"]: f["cuentas"] for f in avisos_consejo.leer(c)
                  if f["consorcio_id"] == e["cons"]}  # fmt: skip
    assert leidas[e["mc"]] == {"diferencias_cotejo": 2}
    assert avisos_consejo.partes(leidas[e["mc"]]) == [
        "en 2 unidades lo informado y confirmado no suma lo que dice la liquidación"
    ]
    # Lo que coincide sale de la cuenta.
    confirmado(e, "prop", "1-A", "50.00", "2026-10-11")
    with transaccion(motor_app, persona_id=None) as c:
        leidas = {f["persona_id"]: f["cuentas"] for f in avisos_consejo.leer(c)
                  if f["consorcio_id"] == e["cons"]}  # fmt: skip
    assert leidas[e["mc"]] == {"diferencias_cotejo": 1}


def test_sin_liquidacion_la_bandeja_no_cuenta_diferencias(e, motor_app):
    confirmado(e, "prop", "1-A", "250.00", "2026-10-10")
    with transaccion(motor_app, persona_id=None) as c:
        filas = {f["persona_id"]: f for f in c.execute(text(PENDIENTES)).mappings()
                 if f["consorcio_id"] == e["cons"]}  # fmt: skip
    assert filas[e["mc"]]["diferencias_cotejo"] == 0
    assert "diferencias_cotejo" not in avisos_consejo.cuentas_de(dict(filas[e["mc"]]))


# --- La migración ---------------------------------------------------------------------------------


def test_0131_ida_y_vuelta(base_aparte):
    cfg, motor = base_aparte
    command.upgrade(cfg, "0130")

    def estado() -> tuple:
        with motor.connect() as c:
            return tuple(c.execute(text(
                "select to_regprocedure('nucleo.cotejo_de(uuid, uuid)') is not null,"
                " to_regprocedure('nucleo.cotejo_pagos_informados_lectura(date, uuid, text,"
                "   uuid)') is not null,"
                " (select count(*) from pg_proc where proname = 'pendientes_consejo_para_avisar'"
                "   and pg_get_function_result(oid) like '%diferencias_cotejo%'),"
                " (select count(*) from pg_proc where proname = 'pendientes_consejo_para_avisar'"
                "   and pg_get_function_result(oid) like '%pagos_informados%')"),
            ).one())  # fmt: skip

    antes = (False, False, 0, 1)
    assert estado() == antes
    command.upgrade(cfg, "0131")
    assert estado() == (True, True, 1, 1)
    command.downgrade(cfg, "0130")  # deja la de 0130 como estaba
    assert estado() == antes
    command.upgrade(cfg, "0131")  # control: vuelve a subir limpio
    assert estado() == (True, True, 1, 1)
