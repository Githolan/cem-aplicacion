"""Recálculo de la liquidación importada con los coeficientes del edificio, H5-4 (`0116`; diseño
H5 §5; RN30, RN33, D5-A).

Contra servicios reales: PostgreSQL, el almacén S3 de pruebas y ClamAV. La liquidación es la
inventada de H5-1 (`test_mis_expensas.liquidacion`), septiembre de 2026: «Gastos Comunes» reparte
2.000,00 en 50/30/20 (PB-A, 1-A, 1-B) y «AySA» 500,50 en 60/40 (1-B no participa). El padrón
tiene además «1° A», que la liquidación no menciona. Datos SINTETICOS / NO REAL.
"""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.test_coeficientes_0021 import registrar
from tests.test_confirmacion_liquidacion_0115 import decidir, leida, post, ruta
from tests.test_importacion_liquidacion_0113 import bucket, firmas_de_la_imagen
from tests.test_mandatos import otra_persona
from tests.test_mandatos_0012 import alta
from tests.test_modo_operacion import cambiar, consorcio_con_persona, decidir_equipo
from tests.test_orden_pago_neutra import nueva_sesion
from tests.test_rutas_egresos import cliente

pytestmark = [pytest.mark.db, pytest.mark.redis]
# ruff: noqa: F811

__all__ = ["bucket", "firmas_de_la_imagen"]  # fixtures; el segundo es autouse

D = Decimal
DESDE = date(2024, 1, 1)


@pytest.fixture
def escena(motor_migrate, motor_app):
    """Como la de `0113` (Modo Consejo, un miembro del Consejo con `liquidacion.importar`), con la
    decisión del equipo a mano para registrar coeficientes."""
    cons, _, prop = consorcio_con_persona(motor_migrate)
    acta = decidir_equipo(motor_app, cons)
    cambiar(motor_app, cons, "consejo", acta)
    mc = otra_persona(motor_migrate, cons)
    alta(motor_app, cons, None, acta, persona=mc, tipo="miembro_consejo", origen="soporte")
    with motor_migrate.begin() as c:
        for designacion in ("PB-A", "1-A"):
            c.execute(text("insert into nucleo.unidad (consorcio_id, designacion) values (:c, :d)"),
                      {"c": cons, "d": designacion})  # fmt: skip
    return {"cons": cons, "prop": prop, "mc": mc, "acta": acta,
            "sesion": nueva_sesion(motor_migrate, mc)}  # fmt: skip


def padron(motor_migrate, e) -> list[tuple[str, str]]:
    """Las unidades activas del edificio: (id, designación). Dos pueden compartir designación."""
    with motor_migrate.connect() as c:
        return [(str(u), d) for u, d in c.execute(
            text("select id, designacion from nucleo.unidad where consorcio_id = :c"
                 " and eliminado_en is null"), {"c": e["cons"]})]  # fmt: skip


def esquema(motor_app, motor_migrate, e, grupo: str, por: dict[str, str], desde=DESDE):
    """Cada unidad con el porcentaje de su designación; las que no figuran, con cero."""
    coeficientes = {u: por.get(d, "0.000000") for u, d in padron(motor_migrate, e)}
    registrar(motor_app, e["cons"], coeficientes, grupo=grupo, desde=desde,
              decision=e["acta"])  # fmt: skip


def confirmada(e, motor_app, motor_migrate, corregir: dict[str, dict] | None = None) -> dict:
    i = leida(e, motor_app, motor_migrate)
    for c in i["campos"]:
        if corregir and c["clave"] in corregir:
            assert decidir(e, i, c["clave"], "corregido", corregir[c["clave"]]).status_code == 200
        else:
            assert decidir(e, i, c["clave"], "confirmado").status_code == 200
    r = post(e, ruta(e, i["id"], "confirmacion"))
    assert r.status_code == 200, r.text
    return i


def recalculo(e, i, quien=None, sesion=None):
    with cliente(quien or e["mc"], sesion or e["sesion"]) as c:
        return c.get(ruta(e, i["id"], "recalculo"))


def corresponder(e, i, nombre: str, grupo: str):
    return post(e, ruta(e, i["id"], "correspondencias"), {"nombre": nombre, "grupo": grupo})


def grupo(respuesta: dict, nombre: str) -> dict:
    return next(g for g in respuesta["grupos"] if g["nombre"] == nombre)


GC = {"1° A": "0.000000", "PB-A": "50.000000", "1-A": "30.000000", "1-B": "20.000000"}
AGUA = {"1° A": "0.000000", "PB-A": "50.000000", "1-A": "50.000000", "1-B": "0.000000"}


def test_sin_confirmar_no_se_recalcula(escena, bucket, motor_app, motor_migrate):
    i = leida(escena, motor_app, motor_migrate)
    r = recalculo(escena, i)
    assert r.status_code == 409 and r.json()["codigo"] == "importacion_sin_confirmar", r.text
    r = corresponder(escena, i, "Gastos Comunes", "expensas_comunes")
    assert r.status_code == 409, r.text
    with motor_migrate.connect() as c:
        guardadas = c.execute(text(
            "select count(*) from nucleo.correspondencia_grupo_liquidacion"
            " where consorcio_id = :c"), {"c": escena["cons"]}).scalar_one()  # fmt: skip
    assert guardadas == 0


def test_cada_grupo_con_sus_coeficientes_frente_a_lo_informado(
    escena, bucket, motor_app, motor_migrate
):
    e = escena
    i = confirmada(e, motor_app, motor_migrate)
    esquema(motor_app, motor_migrate, e, "expensas_comunes", GC)
    esquema(motor_app, motor_migrate, e, "agua", AGUA)
    esquema(motor_app, motor_migrate, e, "cocheras", GC, desde=date(2027, 1, 1))
    r = recalculo(e, i)
    assert r.status_code == 200, r.text
    datos = r.json()
    assert (datos["periodo"], datos["sin_grupos"]) == ("2026-09-01", False)
    # Para elegir, los esquemas vigentes en septiembre de 2026: cocheras empieza en 2027.
    assert [x["grupo"] for x in datos["esquemas"]] == ["agua", "expensas_comunes"]
    # Por nombre: el orden de las columnas del documento no se guarda.
    assert [(g["nombre"], g["estado"]) for g in datos["grupos"]] == [
        ("AySA", "sin_correspondencia"), ("Gastos Comunes", "sin_correspondencia")]  # fmt: skip

    r = corresponder(e, i, "Gastos Comunes", "expensas_comunes")
    assert r.status_code == 200, r.text
    gc, aysa = grupo(r.json(), "Gastos Comunes"), grupo(r.json(), "AySA")
    assert (gc["estado"], gc["grupo"], gc["total"], gc["senaladas"]) == (
        "recalculado", "expensas_comunes", "2000.00", 0)  # fmt: skip
    por = {u["designacion"]: u for u in gc["unidades"]}
    pa = por["PB-A"]
    resumen = (pa["esperado"], pa["informado"], pa["coeficiente"], pa["porcentaje_informado"])
    assert resumen == ("1000.00", "1000.00", "50.000000", "50.00")
    # La unidad del padrón que la liquidación no menciona tiene coeficiente cero: nada que señalar.
    assert (por["1° A"]["informado"], por["1° A"]["esperado"], por["1° A"]["senalada"]) == (
        None, "0.00", False)  # fmt: skip
    assert aysa["estado"] == "sin_correspondencia"

    # «aysa» se compara como las designaciones: el nombre en minúsculas es el mismo grupo.
    r = corresponder(e, i, "aysa", "agua")
    assert r.status_code == 200, r.text
    aysa = grupo(r.json(), "AySA")
    por = {u["designacion"]: u for u in aysa["unidades"]}
    assert aysa["senaladas"] == 2
    assert (por["PB-A"]["esperado"], por["PB-A"]["informado"], por["PB-A"]["diferencia"]) == (
        "250.25", "300.30", "50.05")  # fmt: skip
    assert (por["1-A"]["esperado"], por["1-A"]["informado"], por["1-A"]["diferencia"]) == (
        "250.25", "200.20", "-50.05")  # fmt: skip
    assert por["1-B"]["senalada"] is False


def test_la_correspondencia_se_recuerda_y_cambiarla_agrega_una_version(
    escena, bucket, motor_app, motor_migrate
):
    e = escena
    i = confirmada(e, motor_app, motor_migrate)
    esquema(motor_app, motor_migrate, e, "expensas_comunes", GC)
    esquema(motor_app, motor_migrate, e, "agua", AGUA)
    assert corresponder(e, i, "AySA", "agua").status_code == 200
    assert corresponder(e, i, "AySA", "agua").status_code == 200  # la misma: no agrega nada
    assert corresponder(e, i, "AySA", "expensas_comunes").status_code == 200
    with motor_migrate.connect() as c:
        versiones = c.execute(text(
            "select version, grupo, nombre from nucleo.correspondencia_grupo_liquidacion"
            " where consorcio_id = :c order by version"), {"c": e["cons"]}).all()  # fmt: skip
        eventos = c.execute(text(
            "select antes, despues from auditoria.eventos where consorcio_id = :c"
            " and accion = 'consorcio.liquidacion.corresponder_grupo' order by secuencia"),
            {"c": e["cons"]}).all()  # fmt: skip
    assert [tuple(v) for v in versiones] == [(1, "agua", "AySA"), (2, "expensas_comunes", "AySA")]
    assert [tuple(x) for x in eventos] == [
        (None, {"nombre": "AySA", "grupo": "agua"}),
        ({"grupo": "agua"}, {"nombre": "AySA", "grupo": "expensas_comunes"}),
    ]
    # Otra importación del mismo edificio ya la tiene.
    aysa = grupo(recalculo(e, i).json(), "AySA")
    assert (aysa["estado"], aysa["grupo"]) == ("recalculado", "expensas_comunes")
    # Lo guardado no cambia ni se borra.
    with pytest.raises(DBAPIError) as error, motor_migrate.begin() as c:
        c.execute(text("update nucleo.correspondencia_grupo_liquidacion set grupo = 'agua'"
                       " where consorcio_id = :c"), {"c": e["cons"]})  # fmt: skip
    assert error.value.orig.diag.message_hint == "historia_inmutable"


def test_el_recalculo_usa_el_valor_corregido(escena, bucket, motor_app, motor_migrate):
    """RN30: lo oficial es lo confirmado o corregido, con el valor corregido."""
    e = escena
    leido = leida(e, motor_app, motor_migrate)
    beto = next(c for c in leido["campos"] if c["clave"] == "importe_unidad:002")
    valor = beto["valor"]
    nuevo = {"Gastos Comunes": {"porcentaje": "30.00", "monto": "650.00"}}
    corregido = valor | {"grupos": valor["grupos"] | nuevo}
    # `confirmada` vuelve a leer otra liquidación: se decide sobre esta misma.
    for c in leido["campos"]:
        if c["clave"] == "importe_unidad:002":
            r = decidir(e, leido, c["clave"], "corregido", corregido)
        else:
            r = decidir(e, leido, c["clave"], "confirmado")
        assert r.status_code == 200, r.text
    assert post(e, ruta(e, leido["id"], "confirmacion")).status_code == 200
    esquema(motor_app, motor_migrate, e, "expensas_comunes", GC)
    r = corresponder(e, leido, "Gastos Comunes", "expensas_comunes")
    gc = grupo(r.json(), "Gastos Comunes")
    por = {u["designacion"]: u for u in gc["unidades"]}
    assert (por["1-A"]["informado"], por["1-A"]["diferencia"], por["1-A"]["senalada"]) == (
        "650.00", "50.00", True)  # fmt: skip


def test_un_esquema_que_el_edificio_no_tiene_no_se_guarda(escena, bucket, motor_app, motor_migrate):
    i = confirmada(escena, motor_app, motor_migrate)
    r = corresponder(escena, i, "AySA", "agua")
    assert r.status_code == 422 and r.json()["codigo"] == "grupo_inexistente", r.text


def test_sin_la_facultad_no_se_ve(escena, bucket, motor_app, motor_migrate):
    i = confirmada(escena, motor_app, motor_migrate)
    sesion = nueva_sesion(motor_migrate, escena["prop"])
    r = recalculo(escena, i, quien=escena["prop"], sesion=sesion)
    assert r.status_code == 403, r.text
    r = post(escena, ruta(escena, i["id"], "correspondencias"), {"nombre": "AySA", "grupo": "agua"},
             quien=escena["prop"], sesion=sesion)  # fmt: skip
    assert r.status_code == 403, r.text
