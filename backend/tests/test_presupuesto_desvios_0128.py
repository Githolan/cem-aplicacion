"""El presupuesto, los desvíos por rubro y los vencimientos en el panel (`0128`; RF15 en su forma de
Modo Consejo, RF48; diseño P0 S9).

Contra servicios reales: PostgreSQL, el almacén S3 de pruebas y ClamAV. La liquidación es la
inventada de H5-1 (`test_mis_expensas.liquidacion`): rubro 1 por 1.500,50 y rubro 2 por 1.000,00,
con vencimientos el 13-10-2026 y el 23-10-2026; los otros importes salen de corregir el dato
oficial al confirmar. La escena es la de `0119` (Modo Consejo). Cada negación lleva su control: el
mismo pedido de quien sí puede pasa. Datos SINTETICOS / NO REAL.
"""

import importlib.util
import json
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from cem_contracts import presupuesto as contrato
from cem_contracts.ayuda import ENCABEZADO_AYUDA
from cem_contracts.importacion_liquidacion import PERIODOS_DE_GASTO
from psycopg.errors import InsufficientPrivilege
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db import transaccion
from scripts import exportar_consorcio
from tests.conftest import clave_idempotencia
from tests.test_exportacion_2c3 import (  # noqa: F401  (`claves` y `con_url` son fixtures)
    argumentos,
    claves,
    con_url,
    descifrar,
    entidades,
    verificar_manifiesto,
)
from tests.test_importacion_liquidacion_0113 import bucket, firmas_de_la_imagen
from tests.test_liquidacion_oficial_0119 import (
    OCTUBRE,
    SEPTIEMBRE,
    armar_escena,
    confirmada,
    confirmar,
    de_octubre,
    get,
    importar,
    ruta,
)
from tests.test_mandatos import otra_persona
from tests.test_mandatos_0012 import alta
from tests.test_modo_operacion import hoy_de
from tests.test_orden_pago_neutra import nueva_sesion
from tests.test_panel_liquidacion_oficial_0120 import del_periodo, nombres_de_los_rubros
from tests.test_reclamos_liquidacion_0123 import correo_de
from tests.test_rutas_egresos import cliente
from tests.test_vinculo_0014 import base_aparte

pytestmark = [pytest.mark.db, pytest.mark.redis]
# ruff: noqa: F811

__all__ = ["base_aparte", "bucket", "firmas_de_la_imagen"]

MIGRACION = (
    Path(__file__).resolve().parent.parent / "migrations/versions/0128_presupuesto_y_desvios.py"
)
TABLAS = ("nucleo.presupuesto", "nucleo.presupuesto_rubro")
AGOSTO = "2026-08-01"
NOVIEMBRE = "2026-11-01"
DICIEMBRE = "2026-12-01"
APROBACION = "Asamblea ordinaria del 12-03-2026 (SINTETICA)"
REGISTRAR = ("select nucleo.registrar_presupuesto(:d, :h, :a, cast(:r as jsonb), null, null,"
             " :actor, 'api', :s)")  # fmt: skip
LECTURAS = (
    "nucleo.presupuestos_lectura()",
    f"nucleo.panel_desvios_presupuesto({PERIODOS_DE_GASTO})",
    "nucleo.panel_vencimientos_liquidacion_vigente()",
)


def _migracion():
    spec = importlib.util.spec_from_file_location("migracion_0128", MIGRACION)
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def armar(motor_migrate, motor_app) -> dict:
    """La escena de `0119` (Modo Consejo, el Consejo, la administración, la propietaria confirmada,
    una sin confirmar y una inquilina), más una cuenta sin vínculo que va a ayudar."""
    e = armar_escena(motor_migrate, motor_app)
    e["hoy"] = hoy_de(motor_app, e["cons"])
    e["ayu"] = otra_persona(motor_migrate, e["cons"], verificada=False)
    e["sesion_ayu"] = nueva_sesion(motor_migrate, e["ayu"])
    return e


@pytest.fixture
def e(motor_migrate, motor_app, bucket) -> dict:
    return armar(motor_migrate, motor_app)


def cuerpo(desde: str, hasta: str, rubros: list[tuple[int, str, str]], **extra) -> dict:
    return {
        "desde": desde, "hasta": hasta, "aprobacion": APROBACION,
        "rubros": [{"numero": n, "nombre": nombre, "importe": importe}
                   for n, nombre, importe in rubros],
    } | extra  # fmt: skip


def registrar(e: dict, datos: dict, quien: str = "mc", encabezados: dict | None = None):
    with cliente(e[quien], e[f"sesion_{quien}"]) as c:
        return c.post(ruta(e, "presupuestos"), json=datos,
                      headers=clave_idempotencia() | (encabezados or {}))  # fmt: skip


def registrado(e: dict, datos: dict, quien: str = "mc") -> dict:
    r = registrar(e, datos, quien)
    assert r.status_code == 201, r.text
    return r.json()


def codigo(r) -> tuple[int, str]:
    return r.status_code, r.json()["codigo"]


def hint(error: pytest.ExceptionInfo) -> str | None:
    return error.value.orig.diag.message_hint


def _d(valor) -> Decimal | None:
    return None if valor is None else Decimal(valor)


def panel(e: dict, quien: str = "mc") -> dict:
    r = get(e, quien, "panel")
    assert r.status_code == 200, r.text
    return r.json()


def desvios(datos: dict) -> list[tuple]:
    """(período, rubro, presupuestado, liquidado, desvío, porcentaje) de cada fila, en su orden."""
    return [
        (f["periodo"], f["numero"], _d(f["presupuestado"]), _d(f["liquidado"]), _d(f["desvio"]),
         _d(f["porcentaje"]))
        for f in datos["desvios_presupuesto"]
    ]  # fmt: skip


def cantidad(motor_migrate, cons) -> int:
    with motor_migrate.connect() as c:
        return c.execute(text("select count(*) from nucleo.presupuesto where consorcio_id = :c"),
                         {"c": cons}).scalar_one()  # fmt: skip


# --- Los desvíos ------------------------------------------------------------------------------


def test_los_desvios_son_exactos_por_periodo_y_por_rubro(e, motor_app):
    """Septiembre tal como llegó; octubre con el rubro 2 en 1.200,00; noviembre con el rubro 1 en
    1.400,00. El presupuesto A cubre septiembre y octubre (con un rubro 3 que la liquidación no
    trae); el B, noviembre y diciembre, con el rubro 2 en cero."""
    confirmada(e, motor_app)
    confirmada(e, motor_app, de_octubre(), corregir={"rubro:2": {"total": "1200.00"}})
    noviembre = confirmada(e, motor_app, del_periodo(2026, 11),
                           corregir={"rubro:1": {"total": "1400.00"}})  # fmt: skip
    nombres = nombres_de_los_rubros(e, noviembre)
    a = registrado(e, cuerpo(SEPTIEMBRE, OCTUBRE, [(1, "Remuneraciones", "1500.00"),
                                                   (2, "Servicios", "1100.00"),
                                                   (3, "Mantenimiento", "300.00")]))  # fmt: skip
    b = registrado(e, cuerpo(NOVIEMBRE, DICIEMBRE, [(1, "Remuneraciones", "1600.00"),
                                                    (2, "Servicios", "0.00")]))  # fmt: skip
    for quien in ("mc", "ad"):
        d = panel(e, quien)
        assert desvios(d) == [
            # 0,50 × 100 / 1.500 = 0,0333…; −100 × 100 / 1.100 = −9,0909…
            (SEPTIEMBRE, 1, Decimal("1500.00"), Decimal("1500.50"), Decimal("0.50"),
             Decimal("0.03")),
            (SEPTIEMBRE, 2, Decimal("1100.00"), Decimal("1000.00"), Decimal("-100.00"),
             Decimal("-9.09")),
            # El rubro 3 no está en la liquidación: sin gasto que comparar, sin desvío.
            (SEPTIEMBRE, 3, Decimal("300.00"), None, None, None),
            (OCTUBRE, 1, Decimal("1500.00"), Decimal("1500.50"), Decimal("0.50"), Decimal("0.03")),
            (OCTUBRE, 2, Decimal("1100.00"), Decimal("1200.00"), Decimal("100.00"),
             Decimal("9.09")),
            (OCTUBRE, 3, Decimal("300.00"), None, None, None),
            # −200 × 100 / 1.600 = −12,5; con lo presupuestado en cero, el desvío sin porcentaje.
            (NOVIEMBRE, 1, Decimal("1600.00"), Decimal("1400.00"), Decimal("-200.00"),
             Decimal("-12.50")),
            (NOVIEMBRE, 2, Decimal("0.00"), Decimal("1000.00"), Decimal("1000.00"), None),
        ]  # fmt: skip
        filas = d["desvios_presupuesto"]
        assert [f["presupuesto_id"] for f in filas] == [a["id"]] * 6 + [b["id"]] * 2
        # El nombre es el de la liquidación; el del rubro que solo está en el presupuesto, el suyo.
        assert [f["nombre"] for f in filas if f["periodo"] == NOVIEMBRE] == [nombres[1], nombres[2]]
        assert [f["nombre"] for f in filas if f["numero"] == 3] == ["Mantenimiento"] * 2


def test_un_rubro_sin_presupuesto_se_dice_asi_y_no_es_un_desvio_del_cien_por_ciento(e, motor_app):
    """Agosto no lo cubre ningún presupuesto; el de septiembre no trae el rubro 2."""
    confirmada(e, motor_app, del_periodo(2026, 8))
    confirmada(e, motor_app)
    p = registrado(e, cuerpo(SEPTIEMBRE, SEPTIEMBRE, [(1, "Remuneraciones", "1500.50")]))
    d = panel(e)
    assert desvios(d) == [
        (AGOSTO, 1, None, Decimal("1500.50"), None, None),
        (AGOSTO, 2, None, Decimal("1000.00"), None, None),
        (SEPTIEMBRE, 1, Decimal("1500.50"), Decimal("1500.50"), Decimal("0.00"), Decimal("0.00")),
        (SEPTIEMBRE, 2, None, Decimal("1000.00"), None, None),
    ]
    assert [f["presupuesto_id"] for f in d["desvios_presupuesto"]] == [None, None, p["id"], p["id"]]
    # Ninguna fila sin presupuesto lleva un desvío ni un porcentaje (ni 100 ni −100).
    for f in d["desvios_presupuesto"]:
        if f["presupuestado"] is None:
            assert (f["desvio"], f["porcentaje"]) == (None, None), f


def test_solo_cuentan_las_liquidaciones_oficiales_confirmadas(e, motor_app):
    """La de septiembre reemplazada por otra con el rubro 1 en 1.700,00; la de octubre importada
    sin confirmar. El presupuesto cubre los dos meses."""
    a = confirmada(e, motor_app)
    b = importar(e, motor_app, corregir={"rubro:1": {"total": "1700.00"}})
    assert confirmar(e, b, {"reemplaza_a": a, "motivo": "Corrigió el rubro 1"}).status_code == 200
    importar(e, motor_app, de_octubre())  # para confirmar: nunca cuenta (RN30)
    registrado(e, cuerpo(SEPTIEMBRE, OCTUBRE, [(1, "Remuneraciones", "1500.00"),
                                               (2, "Servicios", "1000.00")]))  # fmt: skip
    assert desvios(panel(e)) == [
        # 200 × 100 / 1.500 = 13,333…: la reemplazada (1.500,50) ya no cuenta.
        (SEPTIEMBRE, 1, Decimal("1500.00"), Decimal("1700.00"), Decimal("200.00"),
         Decimal("13.33")),
        (SEPTIEMBRE, 2, Decimal("1000.00"), Decimal("1000.00"), Decimal("0.00"), Decimal("0.00")),
    ]  # fmt: skip


# --- Versiones --------------------------------------------------------------------------------


def test_una_version_reemplaza_a_la_vigente_y_la_anterior_queda_como_historia(
    e, motor_app, motor_migrate
):
    confirmada(e, motor_app)
    a = registrado(e, cuerpo(SEPTIEMBRE, OCTUBRE, [(1, "Remuneraciones", "1400.00")]))
    assert desvios(panel(e))[0][2] == Decimal("1400.00")  # control: rige A
    # Sin motivo, el reemplazo no entra (lo frena el contrato).
    sin_motivo = cuerpo(SEPTIEMBRE, OCTUBRE, [(1, "Remuneraciones", "1500.00")],
                        reemplaza_a=a["id"])  # fmt: skip
    assert registrar(e, sin_motivo).status_code == 422
    # Otro que se superpone sin reemplazar: se niega.
    r = registrar(e, cuerpo(OCTUBRE, NOVIEMBRE, [(1, "Remuneraciones", "1.00")]))
    assert codigo(r) == (409, "presupuesto_superpuesto")
    b = registrado(e, cuerpo(SEPTIEMBRE, OCTUBRE, [(1, "Remuneraciones", "1500.00"),
                                                   (2, "Servicios", "900.00")],
                             reemplaza_a=a["id"], motivo="El acta decía 1.500"))  # fmt: skip
    assert (b["reemplaza_a"], b["motivo"], b["reemplazado_por"]) == (a["id"], "El acta decía 1.500",
                                                                     None)  # fmt: skip
    assert desvios(panel(e)) == [
        (SEPTIEMBRE, 1, Decimal("1500.00"), Decimal("1500.50"), Decimal("0.50"), Decimal("0.03")),
        (SEPTIEMBRE, 2, Decimal("900.00"), Decimal("1000.00"), Decimal("100.00"),
         Decimal("11.11")),
    ]  # fmt: skip
    # La reemplazada ya no se reemplaza; la vigente sí (control).
    r = registrar(e, cuerpo(SEPTIEMBRE, SEPTIEMBRE, [(1, "R", "1.00")], reemplaza_a=a["id"],
                            motivo="Otra vez"))  # fmt: skip
    assert codigo(r) == (409, "reemplazo_invalido")
    # La historia: las dos, la más nueva primero, la anterior marcada.
    lista = get(e, "mc", "presupuestos").json()
    assert [(p["id"], p["reemplazado_por"]) for p in lista["elementos"]] == [
        (b["id"], None), (a["id"], b["id"])]  # fmt: skip
    assert lista["elementos"][1]["rubros"] == [
        {"numero": 1, "nombre": "Remuneraciones", "importe": "1400.00"}]  # fmt: skip
    assert lista["puede_registrar"] is True
    c = registrado(e, cuerpo(SEPTIEMBRE, SEPTIEMBRE, [(1, "Remuneraciones", "1450.00")],
                             reemplaza_a=b["id"], motivo="Cambió el rango"))  # fmt: skip
    assert desvios(panel(e))[0][2] == Decimal("1450.00")
    # Nada se cambia ni se borra en la base.
    intentos = [
        "update nucleo.presupuesto set aprobacion = 'Otra' where id = :p",
        "delete from nucleo.presupuesto where id = :p",
        "update nucleo.presupuesto_rubro set importe = 1 where presupuesto_id = :p",
        "delete from nucleo.presupuesto_rubro where presupuesto_id = :p",
        "truncate nucleo.presupuesto_rubro",
    ]
    for sql in intentos:
        with pytest.raises(DBAPIError) as error, motor_migrate.begin() as cx:
            cx.execute(text(sql), {"p": a["id"]})
        assert hint(error) == "historia_inmutable", sql
    # Cada registro queda en la auditoría, con quién reemplaza.
    with motor_migrate.connect() as cx:
        eventos = cx.execute(text(
            "select entidad_id, actor_persona_id, despues->>'reemplaza_a' from auditoria.eventos"
            " where consorcio_id = :c and accion = 'consorcio.presupuesto.registrar'"
            " order by secuencia"), {"c": e["cons"]}).all()  # fmt: skip
    assert [tuple(f) for f in eventos] == [
        (UUID(a["id"]), e["mc"], None), (UUID(b["id"]), e["mc"], a["id"]),
        (UUID(c["id"]), e["mc"], b["id"])]  # fmt: skip


def test_un_texto_con_cuenta_y_los_rubros_invalidos_se_rechazan(e, motor_app):
    cuenta = "0" * 22
    r = registrar(e, cuerpo(SEPTIEMBRE, SEPTIEMBRE, [(1, "R", "1.00")]) | {
        "aprobacion": f"Asamblea, pagar a {cuenta}"})  # fmt: skip
    assert codigo(r) == (422, "texto_con_cuenta")
    for malo in (
        cuerpo(SEPTIEMBRE, SEPTIEMBRE, [(1, "R", "1.00"), (1, "R", "2.00")]),  # repetido
        cuerpo(SEPTIEMBRE, SEPTIEMBRE, []),
        cuerpo(SEPTIEMBRE, SEPTIEMBRE, [(1, "R", "-1.00")]),
        cuerpo(SEPTIEMBRE, SEPTIEMBRE, [(1, "R", "1.001")]),
        cuerpo("2026-09-02", SEPTIEMBRE, [(1, "R", "1.00")]),
        cuerpo(OCTUBRE, SEPTIEMBRE, [(1, "R", "1.00")]),
        cuerpo(SEPTIEMBRE, SEPTIEMBRE, [(1, "R", "1.00")], motivo="Sin reemplazo"),
    ):
        assert registrar(e, malo).status_code == 422, malo
    assert registrar(e, cuerpo(SEPTIEMBRE, SEPTIEMBRE, [(1, "R", "1.00")])).status_code == 201


# --- Quién registra y quién lee ---------------------------------------------------------------


def test_registra_quien_tiene_la_facultad_y_nunca_quien_ayuda(e, motor_app, motor_migrate):
    # El Consejo y la administración registran (cada uno el suyo, sin superponerse).
    registrado(e, cuerpo(SEPTIEMBRE, SEPTIEMBRE, [(1, "R", "1.00")]), "mc")
    registrado(e, cuerpo(OCTUBRE, OCTUBRE, [(1, "R", "1.00")]), "ad")
    antes = cantidad(motor_migrate, e["cons"])
    for quien in ("prop", "inq", "sin_confirmar"):
        r = registrar(e, cuerpo(NOVIEMBRE, NOVIEMBRE, [(1, "R", "1.00")]), quien)
        assert r.status_code == 403, quien
    # Un mandato del Consejo al que el acta dejó solo el panel: lee, no registra.
    e["acotado"] = otra_persona(motor_migrate, e["cons"])
    alta(motor_app, e["cons"], None, e["acta"], persona=e["acotado"], tipo="miembro_consejo",
         origen="soporte", facultades=["fiscalizacion.ver_panel"])  # fmt: skip
    e["sesion_acotado"] = nueva_sesion(motor_migrate, e["acotado"])
    r = registrar(e, cuerpo(NOVIEMBRE, NOVIEMBRE, [(1, "R", "1.00")]), "acotado")
    assert r.status_code == 403
    lista = get(e, "acotado", "presupuestos")
    assert lista.status_code == 200 and lista.json()["puede_registrar"] is False
    # La lista, solo con el panel.
    for quien in ("prop", "inq"):
        assert get(e, quien, "presupuestos").status_code == 403, quien
    # Ayudando (RF53): el Consejo, propietario confirmado, autoriza a otra cuenta; ayudándolo, el
    # registro se niega aunque la persona asistida pueda.
    with cliente(e["mc"], e["sesion_mc"]) as c:
        r = c.post(ruta(e, "ayudas"), headers=clave_idempotencia(), json={
            "correo": correo_de(motor_migrate, e["ayu"]),
            "alcances": ["consultar_expensas", "preparar_reclamos"]})  # fmt: skip
    assert r.status_code == 201, r.text
    ayuda = r.json()["id"]
    r = registrar(e, cuerpo(NOVIEMBRE, NOVIEMBRE, [(1, "R", "1.00")]), "ayu",
                  {ENCABEZADO_AYUDA: ayuda})  # fmt: skip
    # La respuesta es la genérica; el motivo queda en la auditoría, con quién actuó y por quién.
    assert codigo(r) == (403, "sin_permiso")
    with motor_migrate.connect() as cx:
        negacion = cx.execute(text(
            "select actor_persona_id, en_representacion_de, despues->>'motivo',"
            " despues->>'accion' from auditoria.eventos where consorcio_id = :c"
            " and accion = 'autorizacion.denegada' order by secuencia desc limit 1"),
            {"c": e["cons"]}).one()  # fmt: skip
    assert tuple(negacion) == (e["ayu"], e["mc"], "ayuda_no_admitida", "presupuesto.registrar")
    assert cantidad(motor_migrate, e["cons"]) == antes
    # Control: la asistida misma registra.
    registrado(e, cuerpo(NOVIEMBRE, NOVIEMBRE, [(1, "R", "1.00")]), "mc")
    # La base: con la sesión de quien ayuda, la de la asistida no vale (ni con la ayuda en la
    # transacción: el registro no pide alcance); sin la facultad, 42501.
    rubros = json.dumps([{"numero": 1, "nombre": "R", "importe": "1.00"}])
    p = {"d": DICIEMBRE, "h": DICIEMBRE, "a": APROBACION, "r": rubros}
    for autorizacion in (None, ayuda):
        with motor_app.connect() as cx, cx.begin() as t:
            cx.execute(text(
                "select set_config('app.persona_id', :p, true),"
                " set_config('app.consorcio_id', :c, true), set_config('app.ayuda_id', :a, true)"),
                {"p": str(e["mc"]), "c": str(e["cons"]),
                 "a": "" if autorizacion is None else autorizacion})  # fmt: skip
            with pytest.raises(DBAPIError) as error:
                cx.execute(text(REGISTRAR), p | {"actor": e["mc"], "s": e["sesion_ayu"]})
            t.rollback()
        assert hint(error) == "sesion_invalida", autorizacion
    with (
        pytest.raises(DBAPIError) as error,
        transaccion(motor_app, persona_id=e["prop"], consorcio_id=e["cons"]) as cx,
    ):
        cx.execute(text(REGISTRAR), p | {"actor": e["prop"], "s": e["sesion_prop"]})
    assert isinstance(error.value.orig, InsufficientPrivilege)
    with transaccion(motor_app, persona_id=e["mc"], consorcio_id=e["cons"]) as cx:
        assert cx.execute(text(REGISTRAR), p | {"actor": e["mc"], "s": e["sesion_mc"]}).scalar_one()


def test_la_base_repite_la_barrera_del_panel_en_las_lecturas(e, motor_app):
    confirmada(e, motor_app)
    registrado(e, cuerpo(SEPTIEMBRE, SEPTIEMBRE, [(1, "R", "1.00")]))
    for funcion in LECTURAS:
        for quien in ("prop", "inq"):
            with (
                pytest.raises(DBAPIError) as error,
                transaccion(motor_app, persona_id=e[quien], consorcio_id=e["cons"]) as c,
            ):
                c.execute(text(f"select * from {funcion}")).all()
            assert error.value.orig.sqlstate == "42501", (funcion, quien)
        with transaccion(motor_app, persona_id=e["mc"], consorcio_id=e["cons"]) as c:
            assert c.execute(text(f"select * from {funcion}")).all(), funcion  # control
    # El panel por la API: la propietaria no (su resumen no lleva el presupuesto).
    assert get(e, "prop", "panel").status_code == 403
    assert "desvios_presupuesto" not in get(e, "prop", "resumen").json()


def test_rls_forzada_y_otro_consorcio_no_ve_el_presupuesto(e, motor_app, motor_migrate):
    otro = armar(motor_migrate, motor_app)
    confirmada(e, motor_app)
    confirmada(otro, motor_app)
    p = registrado(e, cuerpo(SEPTIEMBRE, SEPTIEMBRE, [(1, "R", "1.00")]))
    # El otro edificio: ni el presupuesto ni sus desvíos.
    assert get(otro, "mc", "presupuestos").json()["elementos"] == []
    assert {f["presupuesto_id"] for f in panel(otro)["desvios_presupuesto"]} == {None}
    assert {f["presupuesto_id"] for f in panel(e)["desvios_presupuesto"]} == {p["id"]}  # control
    # Con el ámbito del otro edificio, la base no lo encuentra (ni pidiéndolo por su id).
    with transaccion(motor_app, persona_id=otro["mc"], consorcio_id=otro["cons"]) as c:
        assert c.execute(text("select * from nucleo.presupuestos_lectura()")).all() == []
    with transaccion(motor_app, persona_id=e["mc"], consorcio_id=e["cons"]) as c:
        assert len(c.execute(text("select * from nucleo.presupuestos_lectura()")).all()) == 1
    # Reemplazar el de otro edificio: no es un vigente de este.
    r = registrar(otro, cuerpo(SEPTIEMBRE, SEPTIEMBRE, [(1, "R", "2.00")], reemplaza_a=p["id"],
                               motivo="Ajeno"))  # fmt: skip
    assert codigo(r) == (409, "reemplazo_invalido")
    with motor_migrate.connect() as cx:
        rls = cx.execute(text(
            "select c.oid::regclass::text, c.relrowsecurity, c.relforcerowsecurity,"
            " (select p.qual from pg_policies p where p.schemaname = 'nucleo'"
            "   and p.tablename = c.relname and p.policyname = 'por_consorcio')"
            " from pg_class c where c.oid = any(cast(:t as regclass[])) order by 1"),
            {"t": list(TABLAS)}).all()  # fmt: skip
        politica = "(consorcio_id = plataforma.consorcio_actual())"
        assert [tuple(f) for f in rls] == [(t, True, True, politica) for t in sorted(TABLAS)]
        for tabla in TABLAS:
            assert not cx.execute(
                text("select has_table_privilege('cem_app', :t, 'select, insert, update, delete')"),
                {"t": tabla}).scalar_one(), tabla  # fmt: skip


# --- Los vencimientos -------------------------------------------------------------------------


def test_los_vencimientos_son_los_de_la_vigente_tal_como_se_leyeron(e, motor_app):
    assert panel(e)["liquidacion_vigente"] is None
    confirmada(e, motor_app, del_periodo(2026, 8))
    septiembre = confirmada(e, motor_app)
    vigente = panel(e)["liquidacion_vigente"]
    assert (vigente["id"], vigente["vencimientos"]) == (septiembre, ["2026-10-13", "2026-10-23"])
    # Una vigente cuyo dato de los totales no trae vencimientos: la lista vacía, nada inventado.
    sin_vencimientos = {"total:gastos": {"vencimientos": []}}
    octubre = confirmada(e, motor_app, de_octubre(), corregir=sin_vencimientos)
    vigente = panel(e)["liquidacion_vigente"]
    assert (vigente["id"], vigente["vencimientos"]) == (octubre, [])


# --- Contrato, privilegios, exportación y migración ---------------------------------------------


def test_la_migracion_copia_los_limites_del_contrato():
    m = _migracion()
    assert (m.LARGO_APROBACION, m.LARGO_MOTIVO, m.LARGO_NOMBRE_RUBRO, m.MAXIMO_RUBROS) == (
        contrato.LARGO_APROBACION, contrato.LARGO_MOTIVO, contrato.LARGO_NOMBRE_RUBRO,
        contrato.MAXIMO_RUBROS)  # fmt: skip


def test_las_rutas_son_solo_para_cem_app_y_las_internas_para_nadie(motor_migrate):
    m = _migracion()
    puede = text("select has_function_privilege(:r, :f, 'EXECUTE')")
    with motor_migrate.connect() as c:
        for f in m.RUTAS:
            assert c.execute(puede, {"r": "cem_app", "f": f}).scalar_one() is True, f
            assert c.execute(puede, {"r": "cem_ro", "f": f}).scalar_one() is False, f
        for f in m.INTERNAS:
            assert c.execute(puede, {"r": "cem_app", "f": f}).scalar_one() is False, f


def test_la_exportacion_lleva_los_presupuestos_sin_identificadores(
    e, motor_app, motor_migrate, con_url, claves, tmp_path
):
    a = registrado(e, cuerpo(SEPTIEMBRE, SEPTIEMBRE, [(1, "Remuneraciones", "1500.00")]))
    b = registrado(e, cuerpo(SEPTIEMBRE, SEPTIEMBRE, [(1, "Remuneraciones", "1600.00"),
                                                      (2, "Servicios", "10.25")],
                             reemplaza_a=a["id"], motivo="Corrección SINTETICA"))  # fmt: skip
    salida = tmp_path / f"exportacion-{uuid4().hex[:8]}.zip.age"
    assert exportar_consorcio.main(argumentos(e, claves, salida)) == 0
    paquete = descifrar(salida, claves["privada"])
    assert verificar_manifiesto(paquete) == []
    datos = entidades(paquete)
    assert [(x["id"], x["reemplaza_a"], x["motivo"]) for x in datos["presupuesto"]] == [
        (a["id"], None, None), (b["id"], a["id"], "Corrección SINTETICA")]  # fmt: skip
    assert [(x["presupuesto_id"], x["numero"], Decimal(str(x["importe"])))
            for x in datos["presupuesto_rubro"]] == [
        (a["id"], 1, Decimal("1500.00")), (b["id"], 1, Decimal("1600.00")),
        (b["id"], 2, Decimal("10.25"))]  # fmt: skip
    crudo = json.dumps(datos["presupuesto"])
    assert str(e["mc"]) not in crudo and correo_de(motor_migrate, e["mc"]) not in crudo


def test_0128_ida_y_vuelta_y_no_baja_con_datos(base_aparte):
    cfg, motor = base_aparte
    command.upgrade(cfg, "0127")

    def tablas() -> list[str]:
        with motor.connect() as c:
            return sorted(c.execute(text(
                "select to_regclass(t)::text from unnest(cast(:t as text[])) t"
                " where to_regclass(t) is not null"), {"t": list(TABLAS)}).scalars())  # fmt: skip

    def paquetes() -> set[str]:
        sql = ("select tipo::text from nucleo.paquete_facultad"
               " where facultad = 'presupuesto.registrar'")  # fmt: skip
        with motor.connect() as c:
            return set(c.execute(text(sql)).scalars())

    assert (tablas(), paquetes()) == ([], set())
    command.upgrade(cfg, "0128")
    assert (tablas(), paquetes()) == (sorted(TABLAS), {"miembro_consejo", "administrador"})
    command.downgrade(cfg, "0127")  # sin datos: baja
    assert (tablas(), paquetes()) == ([], set())
    command.upgrade(cfg, "0128")  # control: vuelve a subir limpio
    with motor.begin() as c:
        cons = c.execute(text("insert into nucleo.consorcio (nombre) values ('A') returning id"))\
            .scalar_one()  # fmt: skip
        per = c.execute(text(
            "insert into nucleo.persona (correo, nombre) values (:c, 'P') returning id"),
            {"c": f"p-{uuid4().hex[:8]}@e.com"}).scalar_one()  # fmt: skip
        c.execute(text(
            "insert into nucleo.presupuesto (consorcio_id, desde, hasta, aprobacion,"
            " registrado_por) values (:c, '2026-09-01', '2026-09-01', 'Acta', :p)"),
            {"c": cons, "p": per})  # fmt: skip
    with pytest.raises(DBAPIError) as error:
        command.downgrade(cfg, "0127")
    assert hint(error) == "downgrade_con_datos"
