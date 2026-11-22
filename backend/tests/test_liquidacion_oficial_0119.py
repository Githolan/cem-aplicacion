"""La liquidación oficial del período y quién la lee, H5 (`0119`; diseño H5 §1, §4 y §6, D3-A).

Contra servicios reales: PostgreSQL, el almacén S3 de pruebas y ClamAV. La liquidación es la
inventada de H5-1 (`test_mis_expensas.liquidacion`, septiembre de 2026), leída sin IA; «1-A» es la
unidad 002 de la liquidación y «1° A» no figura. Datos SINTETICOS / NO REAL.
"""

import json
import threading
import time
from dataclasses import replace
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db import transaccion
from tests.conftest import clave_idempotencia
from tests.test_importacion_liquidacion_0113 import (
    bucket,
    con_1b,
    firmas_de_la_imagen,
    importada,
    pdf_de,
)
from tests.test_mandatos import otra_persona
from tests.test_mandatos_0012 import alta
from tests.test_mis_expensas import liquidacion
from tests.test_modo_operacion import cambiar, consorcio_con_persona, decidir_equipo
from tests.test_orden_pago_neutra import nueva_sesion
from tests.test_privacidad_2d3 import ESPERAS, INTERVALO
from tests.test_rutas_egresos import cliente

pytestmark = [pytest.mark.db, pytest.mark.redis]
# ruff: noqa: F811

__all__ = ["bucket", "firmas_de_la_imagen"]

SEPTIEMBRE = "2026-09-01"
OCTUBRE = "2026-10-01"
CONFIRMAR = text("select nucleo.confirmar_importacion_liquidacion(:i, :r, :m, :a, 'api', :s)")


def de_octubre():
    """La misma liquidación inventada, de octubre de 2026."""
    return [
        replace(p, palabras=tuple(replace(w, texto=w.texto.replace("Septiembre", "Octubre"))
                                  for w in p.palabras))
        for p in liquidacion()
    ]  # fmt: skip


def persona_con_vinculo(motor_migrate, cons, unidad, tipo: str, *, confirmado: bool) -> UUID:
    persona = otra_persona(motor_migrate, cons)
    vincular(motor_migrate, cons, persona, unidad, tipo, confirmado=confirmado)
    return persona


def vincular(motor_migrate, cons, persona, unidad, tipo: str, *, confirmado: bool) -> None:
    with motor_migrate.begin() as c:
        v = c.execute(text(
            "insert into nucleo.vinculo (consorcio_id, unidad_id, persona_id, tipo, vigente_desde,"
            " fuente, origen) values (:c, :u, :p, :t, '2020-01-01', 'documento', 'soporte')"
            " returning id"),
            {"c": cons, "u": unidad, "p": persona, "t": tipo}).scalar_one()  # fmt: skip
    if confirmado:
        confirmar_vinculo(motor_migrate, v)


def confirmar_vinculo(motor_migrate, vinculo) -> None:
    with motor_migrate.begin() as c:
        quien = c.execute(text(
            "insert into nucleo.persona (correo, nombre) values (:c, 'Confirma') returning id"),
            {"c": f"confirma-{uuid4()}@ejemplo.com"}).scalar_one()  # fmt: skip
        c.execute(text(
            "update nucleo.vinculo set confirmado_por = :q, confirmado_en = now(),"
            " confirmacion_referencia = 'Escritura SINTETICA' where id = :v"),
            {"q": quien, "v": vinculo})  # fmt: skip


@pytest.fixture
def escena(motor_migrate, motor_app):
    """Como la de `0113` (Modo Consejo, un miembro del Consejo, el padrón de la liquidación), más
    la administración (sin vínculo), la propietaria de «1° A» y «1-A» con los vínculos
    confirmados, una propietaria sin confirmar de «1-A» y una inquilina confirmada de «PB-A»."""
    cons, _, prop = consorcio_con_persona(motor_migrate)
    acta = decidir_equipo(motor_app, cons)
    cambiar(motor_app, cons, "consejo", acta)
    mc = otra_persona(motor_migrate, cons)
    alta(motor_app, cons, None, acta, persona=mc, tipo="miembro_consejo", origen="soporte")
    with motor_migrate.begin() as c:
        for designacion in ("PB-A", "1-A"):
            c.execute(text("insert into nucleo.unidad (consorcio_id, designacion) values (:c, :d)"),
                      {"c": cons, "d": designacion})  # fmt: skip
    e = {"cons": cons, "prop": prop, "mc": mc, "sesion": nueva_sesion(motor_migrate, mc)}
    con_1b(motor_migrate, e)
    with motor_migrate.begin() as c:
        e["u"] = dict(c.execute(text(
            "select designacion, id from nucleo.unidad where consorcio_id = :c"),
            {"c": e["cons"]}).all())  # fmt: skip
        vinculo_prop = c.execute(text(
            "select id from nucleo.vinculo where consorcio_id = :c and persona_id = :p"),
            {"c": e["cons"], "p": e["prop"]}).scalar_one()  # fmt: skip
    confirmar_vinculo(motor_migrate, vinculo_prop)
    vincular(motor_migrate, e["cons"], e["prop"], e["u"]["1-A"], "propietario", confirmado=True)
    e["ad"] = otra_persona(motor_migrate, e["cons"])
    alta(motor_app, e["cons"], None, acta, persona=e["ad"], tipo="administrador",
         origen="soporte")  # fmt: skip
    e["sin_confirmar"] = persona_con_vinculo(
        motor_migrate, e["cons"], e["u"]["1-A"], "propietario", confirmado=False
    )
    e["inq"] = persona_con_vinculo(
        motor_migrate, e["cons"], e["u"]["PB-A"], "inquilino", confirmado=True
    )
    for quien in ("prop", "ad", "sin_confirmar", "inq"):
        e[f"sesion_{quien}"] = nueva_sesion(motor_migrate, e[quien])
    e["sesion_mc"] = e["sesion"]
    return e


def importar(e, motor_app, paginas=None, *, corregir: dict | None = None) -> str:
    """Una importación leída con todos sus datos decididos (los de `corregir`, corregidos), para
    confirmar. Devuelve su id."""
    i = importada(e, motor_app, pdf_de(paginas or liquidacion()))
    assert i["estado"] == "para_confirmar", i
    resolver = text("select nucleo.resolver_campo_preliminar(:i, :f, :d, cast(:v as jsonb), :a,"
                    " 'api', :s)")  # fmt: skip
    with transaccion(motor_app, persona_id=e["mc"], consorcio_id=e["cons"]) as c:
        for campo in i["campos"]:
            cambio = (corregir or {}).get(campo["clave"])
            valor = None if cambio is None else json.dumps(campo["valor"] | cambio)
            c.execute(resolver, {"i": i["id"], "f": campo["id"], "a": e["mc"], "s": e["sesion"],
                                 "d": "confirmado" if cambio is None else "corregido",
                                 "v": valor})  # fmt: skip
    return i["id"]


def ruta(e, *partes: str) -> str:
    return "/".join((f"/api/v1/consorcios/{e['cons']}", *partes))


def confirmar(e, i: str, cuerpo: dict | None = None):
    with cliente(e["mc"], e["sesion"]) as c:
        return c.post(ruta(e, "liquidaciones-importadas", i, "confirmacion"), json=cuerpo,
                      headers=clave_idempotencia())  # fmt: skip


def get(e, quien: str, *partes: str, params: dict | None = None):
    with cliente(e[quien], e[f"sesion_{quien}"]) as c:
        return c.get(ruta(e, *partes), params=params)


def estado(e, i: str) -> dict:
    r = get(e, "mc", "liquidaciones-importadas", i)
    assert r.status_code == 200, r.text
    return r.json()


def confirmada(e, motor_app, paginas=None, **kw) -> str:
    i = importar(e, motor_app, paginas, **kw)
    r = confirmar(e, i)
    assert r.status_code == 200, r.text
    return i


def codigo(r) -> tuple[int, str]:
    return r.status_code, r.json()["codigo"]


# --- Una oficial por período --------------------------------------------------------------------


def test_otra_del_mismo_periodo_entra_solo_reemplazando_y_la_anterior_queda_como_historia(
    escena, bucket, motor_app, motor_migrate
):
    e = escena
    a = confirmada(e, motor_app)
    b = importar(e, motor_app)
    assert codigo(confirmar(e, b)) == (409, "periodo_ya_confirmado")
    assert estado(e, b)["estado"] == "para_confirmar"  # la negativa no cambió nada
    r = confirmar(e, b, {"reemplaza_a": a, "motivo": "  La administración mandó otra versión  "})
    assert r.status_code == 200, r.text
    nueva = r.json()
    assert (nueva["estado"], nueva["reemplaza_a"], nueva["motivo_reemplazo"]) == (
        "confirmada", a, "La administración mandó otra versión")  # fmt: skip
    anterior = estado(e, a)
    assert (anterior["estado"], anterior["decidida_por"]) == ("reemplazada", str(e["mc"]))
    lista = {f["id"]: f for f in get(e, "mc", "liquidaciones-importadas").json()}
    assert (lista[a]["estado"], lista[b]["estado"], lista[b]["reemplaza_a"]) == (
        "reemplazada", "confirmada", a)  # fmt: skip
    # La auditoría dice a quién reemplazó; el motivo es texto libre y queda en la fila (E31).
    with motor_migrate.connect() as c:
        despues = c.execute(text(
            "select despues from auditoria.eventos where entidad_id = :i"
            " and accion = 'consorcio.liquidacion.confirmar_importada'"),
            {"i": b}).scalar_one()  # fmt: skip
    assert despues == {"campos": 16, "reemplaza_a": a}
    # La reemplazada conserva sus datos oficiales (historia, RN30): el recálculo los sigue leyendo.
    with transaccion(motor_app, persona_id=e["mc"], consorcio_id=e["cons"]) as c:
        n = c.execute(text(
            "select count(*) from nucleo.campos_oficiales_liquidacion(:i, :a, 'api', :s)"),
            {"i": a, "a": e["mc"], "s": e["sesion"]}).scalar_one()  # fmt: skip
    assert n == 16
    assert get(e, "mc", "liquidaciones-importadas", a, "recalculo").status_code == 200


def test_un_reemplazo_que_no_es_la_oficial_del_periodo_se_niega(
    escena, bucket, motor_app, motor_migrate
):
    e = escena
    octubre = confirmada(e, motor_app, de_octubre())
    assert estado(e, octubre)["periodo"] == OCTUBRE
    b = importar(e, motor_app)
    motivo = "Otra versión"
    # Septiembre todavía no tiene oficial: no hay nada que reemplazar.
    assert codigo(confirmar(e, b, {"reemplaza_a": octubre, "motivo": motivo})) == (
        409, "reemplazo_invalido")  # fmt: skip
    a = confirmada(e, motor_app)
    for otra in (octubre, str(uuid4()), b):  # de otro período, inexistente, ella misma
        r = confirmar(e, b, {"reemplaza_a": otra, "motivo": motivo})
        assert codigo(r) == (409, "reemplazo_invalido"), otra
    assert confirmar(e, b, {"reemplaza_a": a, "motivo": motivo}).status_code == 200
    # La reemplazada ya no es la oficial: reemplazarla otra vez se niega.
    c = importar(e, motor_app)
    assert codigo(confirmar(e, c, {"reemplaza_a": a, "motivo": motivo})) == (
        409, "reemplazo_invalido")  # fmt: skip
    # Control: reemplazando la oficial vigente, entra.
    assert confirmar(e, c, {"reemplaza_a": b, "motivo": motivo}).status_code == 200
    assert [estado(e, x)["estado"] for x in (a, b, c, octubre)] == [
        "reemplazada", "reemplazada", "confirmada", "confirmada"]  # fmt: skip


def test_el_reemplazo_va_con_su_motivo(escena, bucket, motor_app, motor_migrate):
    e = escena
    a = confirmada(e, motor_app)
    b = importar(e, motor_app)
    assert codigo(confirmar(e, b, {"reemplaza_a": a, "motivo": "   "})) == (
        422, "peticion_invalida")  # fmt: skip
    assert confirmar(e, b, {"reemplaza_a": a}).status_code == 422
    assert confirmar(e, b, {"reemplaza_a": a, "motivo": "x" * 501}).status_code == 422
    # En la base: el reemplazo sin motivo, o el motivo sin reemplazo, se niegan con su motivo.
    for r, m in ((a, None), (None, "Otra versión")):
        with (
            pytest.raises(DBAPIError) as error,
            transaccion(motor_app, persona_id=e["mc"], consorcio_id=e["cons"]) as c,
        ):
            c.execute(CONFIRMAR, {"i": b, "r": r, "m": m, "a": e["mc"], "s": e["sesion"]})
        assert error.value.orig.diag.message_hint == "motivo_invalido"
    assert estado(e, a)["estado"] == "confirmada" and estado(e, b)["estado"] == "para_confirmar"


def esperar_trabado(motor_migrate, pid: int) -> int:
    """Cuántas sesiones esperan un cerrojo de la del `pid` (también un candado consultivo, que
    `test_cerrojos_0041.esperar_trabado` no cuenta)."""
    consulta = text("select count(*) from pg_stat_activity where :p = any(pg_blocking_pids(pid))")
    with motor_migrate.connect() as c:
        for _ in range(ESPERAS):
            n = c.execute(consulta, {"p": pid}).scalar_one()
            if n:
                return n
            time.sleep(INTERVALO)
    return 0


def _a_la_vez(e, motor_app, motor_migrate, primera: dict, segunda: dict) -> BaseException | None:
    """La primera confirmación queda abierta; la segunda espera su candado y corre cuando la
    primera termina. Devuelve la falla de la segunda (o nada)."""
    fallas: list[BaseException] = []

    def otra():
        try:
            with transaccion(motor_app, persona_id=e["mc"], consorcio_id=e["cons"]) as c:
                c.execute(CONFIRMAR, segunda | {"a": e["mc"], "s": e["sesion"]})
        except BaseException as falla:  # se mira en el hilo de la prueba
            fallas.append(falla)

    with transaccion(motor_app, persona_id=e["mc"], consorcio_id=e["cons"]) as c:
        pid = c.execute(text("select pg_backend_pid()")).scalar_one()
        c.execute(CONFIRMAR, primera | {"a": e["mc"], "s": e["sesion"]})
        hilo = threading.Thread(target=otra)
        hilo.start()
        assert esperar_trabado(motor_migrate, pid) == 1  # control: la segunda espera a la primera
    hilo.join()
    return fallas[0] if fallas else None


def test_pa04_consejo_dos_confirmaciones_del_mismo_periodo_a_la_vez_dejan_una_oficial(
    escena, bucket, motor_app, motor_migrate
):
    """Análogo de PA04 para la liquidación del Consejo: dos personas confirman a la vez dos
    importaciones del mismo período; queda exactamente una oficial y la otra recibe
    `periodo_ya_confirmado`."""
    e = escena
    a, b = importar(e, motor_app), importar(e, motor_app)
    falla = _a_la_vez(e, motor_app, motor_migrate, {"i": a, "r": None, "m": None},
                      {"i": b, "r": None, "m": None})  # fmt: skip
    assert isinstance(falla, DBAPIError), falla
    assert falla.orig.diag.message_hint == "periodo_ya_confirmado"
    assert [estado(e, x)["estado"] for x in (a, b)] == ["confirmada", "para_confirmar"]


def test_dos_reemplazos_a_la_vez_de_la_misma_oficial_dejan_uno(
    escena, bucket, motor_app, motor_migrate
):
    e = escena
    a = confirmada(e, motor_app)
    b, c = importar(e, motor_app), importar(e, motor_app)
    falla = _a_la_vez(e, motor_app, motor_migrate, {"i": b, "r": a, "m": "Primera"},
                      {"i": c, "r": a, "m": "Segunda"})  # fmt: skip
    assert isinstance(falla, DBAPIError), falla
    assert falla.orig.diag.message_hint == "reemplazo_invalido"
    assert [estado(e, x)["estado"] for x in (a, b, c)] == [
        "reemplazada", "confirmada", "para_confirmar"]  # fmt: skip


def test_el_indice_unico_es_la_ultima_barrera(escena, bucket, motor_app, motor_migrate):
    """Sin pasar por la función (como dueño de la tabla), una segunda `confirmada` del período o un
    segundo reemplazo de la misma chocan con la base."""
    e = escena
    a, b, c = (importar(e, motor_app) for _ in range(3))
    confirmar_a_mano = text(
        "update nucleo.importacion_liquidacion set estado = 'confirmada', decidida_por = :p,"
        " decidida_en = now(), reemplaza_a = :r, motivo_reemplazo = :m where id = :i"
    )
    with motor_migrate.begin() as x:
        x.execute(confirmar_a_mano, {"i": a, "p": e["mc"], "r": None, "m": None})
    with pytest.raises(DBAPIError) as error, motor_migrate.begin() as x:
        x.execute(confirmar_a_mano, {"i": b, "p": e["mc"], "r": None, "m": None})
    assert error.value.orig.diag.constraint_name == "importacion_oficial_por_periodo"
    with motor_migrate.begin() as x:
        x.execute(text("update nucleo.importacion_liquidacion set estado = 'reemplazada'"
                       " where id = :i"), {"i": a})  # fmt: skip
        x.execute(confirmar_a_mano, {"i": b, "p": e["mc"], "r": a, "m": "Primera"})
    with motor_migrate.begin() as x:
        x.execute(text("update nucleo.importacion_liquidacion set estado = 'reemplazada'"
                       " where id = :i"), {"i": b})  # fmt: skip
    with pytest.raises(DBAPIError) as error, motor_migrate.begin() as x:
        x.execute(confirmar_a_mano, {"i": c, "p": e["mc"], "r": a, "m": "Segunda"})
    assert error.value.orig.diag.constraint_name == "importacion_reemplaza_una_vez"


def test_el_trigger_sigue_negando_cualquier_otro_cambio(escena, bucket, motor_app, motor_migrate):
    e = escena
    a = confirmada(e, motor_app)
    b = importar(e, motor_app)
    assert confirmar(e, b, {"reemplaza_a": a, "motivo": "Otra versión"}).status_code == 200
    for sentencia, i in (
        ("update nucleo.importacion_liquidacion set estado = 'confirmada' where id = :i", a),
        ("update nucleo.importacion_liquidacion set decidida_en = now() where id = :i", a),
        ("update nucleo.importacion_liquidacion set estado = 'para_confirmar' where id = :i", b),
        ("update nucleo.importacion_liquidacion set reemplaza_a = null, motivo_reemplazo = null"
         " where id = :i", b),
        ("update nucleo.importacion_liquidacion set motivo_reemplazo = 'Otro' where id = :i", b),
        ("update nucleo.importacion_liquidacion set periodo = '2026-10-01' where id = :i", b),
        ("delete from nucleo.importacion_liquidacion where id = :i", a),
    ):  # fmt: skip
        with pytest.raises(DBAPIError) as error, motor_migrate.begin() as c:
            c.execute(text(sentencia), {"i": i})
        hint = error.value.orig.diag.message_hint
        assert hint == "historia_inmutable" or "importacion_" in str(error.value), sentencia
    # Control: el único paso desde la confirmada, a reemplazada sin tocar nada más, existe.
    c_ = importar(e, motor_app, de_octubre())
    assert confirmar(e, c_).status_code == 200
    with motor_migrate.begin() as c:
        c.execute(text("update nucleo.importacion_liquidacion set estado = 'reemplazada'"
                       " where id = :i"), {"i": c_})  # fmt: skip


# --- El Consejo y la administración (liquidacion.ver_importada) ---------------------------------


def test_el_consejo_y_la_administracion_ven_las_oficiales_y_nunca_una_sin_confirmar(
    escena, bucket, motor_app, motor_migrate
):
    e = escena
    a = confirmada(e, motor_app)
    b = importar(e, motor_app, corregir={"rubro:2": {"total": "999.99"}})
    assert confirmar(e, b, {"reemplaza_a": a, "motivo": "Corrigió el rubro 2"}).status_code == 200
    sin_confirmar = importar(e, motor_app, de_octubre())  # RN30: nunca aparece
    for quien in ("mc", "ad"):
        r = get(e, quien, "liquidacion-oficial")
        assert r.status_code == 200, r.text
        filas = {f["id"]: f for f in r.json()}
        assert set(filas) == {a, b}, quien
        assert (filas[a]["vigente"], filas[a]["reemplazada_por"]) == (False, b)
        assert (filas[b]["vigente"], filas[b]["reemplaza_a"], filas[b]["motivo_reemplazo"]) == (
            True, a, "Corrigió el rubro 2")  # fmt: skip
        assert get(e, quien, "liquidacion-oficial", sin_confirmar).status_code == 404
        assert get(e, quien, "liquidacion-oficial", str(uuid4())).status_code == 404
    r = get(e, "mc", "liquidacion-oficial", b)
    assert r.status_code == 200, r.text
    d = r.json()
    assert (d["periodo"], d["vigente"], d["diferencias"]) == (SEPTIEMBRE, True, [])
    campos = {f["clave"]: f for f in d["campos"]}
    assert len(campos) == 16 and campos["rubro:2"]["valor"]["total"] == "999.99"
    assert campos["importe_unidad:002"]["designacion"] == "1-A"
    # La reemplazada se lee como historia, con sus datos de entonces.
    viejo = get(e, "mc", "liquidacion-oficial", a).json()
    assert viejo["vigente"] is False
    assert {f["clave"]: f for f in viejo["campos"]}["rubro:2"]["valor"]["total"] != "999.99"


def test_ni_propietarios_ni_inquilinos_ven_la_liquidacion_completa(
    escena, bucket, motor_app, motor_migrate
):
    e = escena
    a = confirmada(e, motor_app)
    for quien in ("prop", "inq", "sin_confirmar"):
        assert get(e, quien, "liquidacion-oficial").status_code == 403, quien
        assert get(e, quien, "liquidacion-oficial", a).status_code == 403, quien
    # La base repite la barrera: la propietaria sin la facultad del panel no lee nada.
    with (
        pytest.raises(DBAPIError) as error,
        transaccion(motor_app, persona_id=e["prop"], consorcio_id=e["cons"]) as c,
    ):
        c.execute(text("select * from nucleo.liquidaciones_oficiales_lectura()")).all()
    assert error.value.orig.sqlstate == "42501"


# --- Cada propietario: el resumen y su unidad -------------------------------------------------


def test_la_propietaria_ve_el_resumen_de_la_vigente_sin_gastos_ni_diferencias_ni_unidades(
    escena, bucket, motor_app, motor_migrate
):
    e = escena
    r = get(e, "prop", "resumen")
    assert r.status_code == 200 and r.json() == {"periodos": [], "liquidacion": None}, r.text
    a = confirmada(e, motor_app)
    b = importar(e, motor_app, corregir={"rubro:2": {"total": "999.99"}})
    assert confirmar(e, b, {"reemplaza_a": a, "motivo": "Corrigió el rubro 2"}).status_code == 200
    confirmada(e, motor_app, de_octubre())
    r = get(e, "prop", "resumen", params={"periodo": SEPTIEMBRE})
    assert r.status_code == 200, r.text
    cuerpo = r.json()
    assert [p["periodo"] for p in cuerpo["periodos"]] == [OCTUBRE, SEPTIEMBRE]
    s = cuerpo["liquidacion"]
    assert set(s) == {"periodo", "confirmada_en", "rubros", "grupos", "total", "vencimientos"}
    assert s["periodo"] == SEPTIEMBRE
    # La vigente (la que reemplazó), con la corrección; ni la reemplazada ni sus gastos.
    assert [(x["numero"], Decimal(x["total"])) for x in s["rubros"]] == [
        (1, Decimal("1500.50")), (2, Decimal("999.99"))]  # fmt: skip
    assert all(set(x) == {"numero", "nombre", "total"} for x in s["rubros"])
    assert {g["nombre"] for g in s["grupos"]} == {"Gastos Comunes", "AySA"}
    assert s["vencimientos"] and s["total"] is not None
    # Sin período, el último que tiene liquidación.
    assert get(e, "prop", "resumen").json()["liquidacion"]["periodo"] == OCTUBRE
    assert get(e, "prop", "resumen", params={"periodo": "2026-08-01"}).status_code == 404
    assert get(e, "prop", "resumen", params={"periodo": "2026-09-15"}).status_code == 422


def test_el_resumen_lo_ven_solo_propietarios_con_el_vinculo_confirmado(
    escena, bucket, motor_app, motor_migrate
):
    e = escena
    confirmada(e, motor_app)
    assert get(e, "prop", "resumen").status_code == 200  # control
    for quien in ("inq", "sin_confirmar", "ad"):
        assert get(e, quien, "resumen").status_code == 403, quien
    # La base repite la barrera para quien la evaluación ya negó.
    for quien in ("inq", "sin_confirmar", "ad"):
        for funcion in ("resumen_liquidacion_oficial(null)", "periodos_liquidacion_oficial()",
                        "mi_cuenta_liquidacion_oficial(null)"):  # fmt: skip
            with (
                pytest.raises(DBAPIError) as error,
                transaccion(motor_app, persona_id=e[quien], consorcio_id=e["cons"]) as c,
            ):
                c.execute(text(f"select * from nucleo.{funcion}")).all()
            assert error.value.orig.sqlstate == "42501", (quien, funcion)


def test_la_propietaria_ve_solo_sus_unidades_y_la_que_no_figura_vuelve_marcada(
    escena, bucket, motor_app, motor_migrate
):
    e = escena
    r = get(e, "prop", "mi-cuenta")
    assert r.status_code == 200 and r.json()["liquidacion"] is None, r.text
    a = confirmada(e, motor_app)
    r = get(e, "prop", "mi-cuenta")
    assert r.status_code == 200, r.text
    cuenta = r.json()["liquidacion"]
    assert cuenta["periodo"] == SEPTIEMBRE and cuenta["vencimientos"]
    unidades = {u["designacion"]: u for u in cuenta["unidades"]}
    assert set(unidades) == {"1-A", "1° A"}  # nunca «PB-A» ni «1-B» (RN15)
    beto = unidades["1-A"]
    assert (beto["figura"], Decimal(beto["total"])) == (True, Decimal("1326.00"))
    aysa = next(g for g in beto["grupos"] if g["nombre"] == "AySA")
    assert (Decimal(aysa["porcentaje"]), Decimal(aysa["monto"])) == (
        Decimal("40.00"), Decimal("200.20"))  # fmt: skip
    assert beto["saldo_anterior"] is not None
    ausente = unidades["1° A"]
    assert ausente["figura"] is False and ausente["total"] is None and ausente["grupos"] == []
    # La unidad corregida cuenta por el valor corregido: si 002 pasa a «PB-A», «1-A» no figura.
    b = importar(e, motor_app, corregir={"saldo_unidad:002": {"depto": "PB-A"},
                                          "importe_unidad:002": {"depto": "PB-A"}})  # fmt: skip
    assert confirmar(e, b, {"reemplaza_a": a, "motivo": "Otra unidad"}).status_code == 200
    unidades = {u["designacion"]: u for u in get(e, "prop", "mi-cuenta").json()["liquidacion"][
        "unidades"]}  # fmt: skip
    assert set(unidades) == {"1-A", "1° A"} and unidades["1-A"]["figura"] is False
    assert get(e, "prop", "mi-cuenta", params={"periodo": OCTUBRE}).status_code == 404


def test_mi_cuenta_no_la_ven_inquilinos_ni_mandatos_sin_vinculo_ni_propietarios_sin_confirmar(
    escena, bucket, motor_app, motor_migrate
):
    e = escena
    confirmada(e, motor_app)
    assert get(e, "prop", "mi-cuenta").status_code == 200  # control
    for quien in ("inq", "sin_confirmar", "ad"):
        assert get(e, quien, "mi-cuenta").status_code == 403, quien
    # Una segunda unidad de la inquilina, como propietaria sin confirmar: sigue sin ver nada.
    vincular(motor_migrate, e["cons"], e["inq"], e["u"]["1-B"], "propietario", confirmado=False)
    assert get(e, "inq", "mi-cuenta").status_code == 403


def test_las_lecturas_son_solo_para_cem_app_y_las_internas_para_nadie(motor_migrate):
    rutas = (
        "nucleo.liquidaciones_oficiales_lectura()", "nucleo.liquidacion_oficial_lectura(uuid)",
        "nucleo.campos_liquidacion_oficial_lectura(uuid)", "nucleo.periodos_liquidacion_oficial()",
        "nucleo.resumen_liquidacion_oficial(date)", "nucleo.mi_cuenta_liquidacion_oficial(date)",
        "nucleo.confirmar_importacion_liquidacion(uuid, uuid, text, uuid, text, uuid)",
    )  # fmt: skip
    internas = (
        "nucleo.campos_oficiales_de(uuid, uuid)",
        "nucleo.exigir_propietario_confirmado(uuid, uuid)",
        "nucleo.liquidacion_vigente(uuid, date)",
    )
    puede = text("select has_function_privilege(:r, :f, 'EXECUTE')")
    with motor_migrate.connect() as c:
        for f in rutas:
            assert c.execute(puede, {"r": "cem_app", "f": f}).scalar_one() is True, f
            assert c.execute(puede, {"r": "cem_ro", "f": f}).scalar_one() is False, f
        for f in internas:
            assert c.execute(puede, {"r": "cem_app", "f": f}).scalar_one() is False, f
