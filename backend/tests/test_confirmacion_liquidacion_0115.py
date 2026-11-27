"""Confirmación campo por campo de la liquidación importada, H5-3 (`0115`; diseño H5 §5; PA32).

Contra servicios reales: PostgreSQL, el almacén S3 de pruebas y ClamAV. La liquidación es la
inventada de H5-1 (`test_mis_expensas.liquidacion`), leída sin IA. Datos SINTETICOS / NO REAL.
"""

import json
import threading
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db import transaccion
from app.liquidacion.importacion import forma_igual
from tests.conftest import clave_idempotencia
from tests.test_cerrojos_0041 import esperar_trabado
from tests.test_documentos_2c1 import pdf
from tests.test_importacion_liquidacion_0113 import (
    bucket,
    con_1b,
    firmas_de_la_imagen,
    importada,
    pdf_de,
)
from tests.test_importacion_liquidacion_0113 import (
    escena as escena,  # noqa: F401  (es un fixture)
)
from tests.test_mis_expensas import liquidacion
from tests.test_orden_pago_neutra import nueva_sesion
from tests.test_rutas_egresos import cliente
from tests.test_vinculo_0014 import base_aparte  # noqa: F401  (es un fixture)

pytestmark = [pytest.mark.db, pytest.mark.redis]
# ruff: noqa: F811

__all__ = ["bucket", "firmas_de_la_imagen"]


def ruta(e, *partes: str) -> str:
    return "/".join((f"/api/v1/consorcios/{e['cons']}/liquidaciones-importadas", *partes))


def post(e, camino: str, cuerpo: dict | None = None, quien=None, sesion=None):
    with cliente(quien or e["mc"], sesion or e["sesion"]) as c:
        return c.post(camino, json=cuerpo, headers=clave_idempotencia())


def decidir(e, i: dict, clave: str, decision: str, corregido: dict | None = None, **kw):
    campo = next(c for c in i["campos"] if c["clave"] == clave)
    return post(e, ruta(e, i["id"], "campos", campo["id"], "decision"),
                {"decision": decision, "valor_corregido": corregido}, **kw)  # fmt: skip


def leida(e, motor_app, motor_migrate) -> dict:
    con_1b(motor_migrate, e)
    return importada(e, motor_app, pdf_de(liquidacion()))


def oficiales(e, motor_app, i) -> list[tuple]:
    with transaccion(motor_app, persona_id=e["mc"], consorcio_id=e["cons"]) as c:
        return [tuple(f) for f in c.execute(
            text("select clave, valor from nucleo.campos_oficiales_liquidacion(:i, :a, 'api', :s)"),
            {"i": i["id"], "a": e["mc"], "s": e["sesion"]},
        )]  # fmt: skip


def test_la_lista_y_el_renglon_de_cada_dato(escena, bucket, motor_app, motor_migrate):
    i = leida(escena, motor_app, motor_migrate)
    with cliente(escena["mc"], escena["sesion"]) as c:
        r = c.get(ruta(escena))
    assert r.status_code == 200, r.text
    (fila,) = r.json()
    resumen = (fila["id"], fila["estado"], fila["campos"], fila["pendientes"], fila["diferencias"])
    assert resumen == (i["id"], "para_confirmar", 16, 16, 0)
    # Cada dato lleva el texto del renglón del documento de donde salió.
    luz = next(c for c in i["campos"] if c["clave"] == "gasto:1:1")
    assert "1.000,00" in luz["fragmento"]
    assert all(c["fragmento"] for c in i["campos"])
    assert {(c["confirmado_por"], c["confirmado_en"]) for c in i["campos"]} == {(None, None)}


def test_confirmar_corregir_y_descartar_cada_dato(escena, bucket, motor_app, motor_migrate):
    e = escena
    i = leida(e, motor_app, motor_migrate)
    r = decidir(e, i, "gasto:1:1", "confirmado")
    assert r.status_code == 200, r.text
    luz = next(c for c in r.json()["campos"] if c["clave"] == "gasto:1:1")
    assert (luz["estado"], luz["confirmado_por"], luz["valor_corregido"]) == (
        "confirmado", str(e["mc"]), None)  # fmt: skip
    rubro = next(c for c in i["campos"] if c["clave"] == "rubro:2")
    corregido = rubro["valor"] | {"total": "999.99"}
    r = decidir(e, i, "rubro:2", "corregido", corregido)
    assert r.status_code == 200, r.text
    rubro_ = next(c for c in r.json()["campos"] if c["clave"] == "rubro:2")
    assert rubro_["estado"] == "corregido" and rubro_["valor_corregido"] == corregido
    assert rubro_["valor"] == rubro["valor"]  # lo leído no cambia
    r = decidir(e, i, "unidad:003", "descartado")
    assert r.status_code == 200, r.text
    # Mientras se confirma, una decisión se cambia; cada una queda en la auditoría.
    assert decidir(e, i, "gasto:1:1", "descartado").status_code == 200
    with motor_migrate.connect() as c:
        eventos = c.execute(text(
            "select despues->>'clave', despues->>'decision' from auditoria.eventos"
            " where accion = 'consorcio.liquidacion.decidir_dato' and consorcio_id = :c"
            " order by secuencia"), {"c": e["cons"]}).all()  # fmt: skip
    assert [tuple(x) for x in eventos] == [
        ("gasto:1:1", "confirmado"),
        ("rubro:2", "corregido"),
        ("unidad:003", "descartado"),
        ("gasto:1:1", "descartado"),
    ]


def test_dos_decisiones_a_la_vez_sobre_un_dato_se_ordenan_y_la_auditoria_lo_dice(
    escena, bucket, motor_app, motor_migrate
):
    """La segunda decisión espera a la primera y audita como `antes` lo que la primera dejó."""
    e = escena
    i = leida(e, motor_app, motor_migrate)
    campo = next(c["id"] for c in i["campos"] if c["clave"] == "gasto:1:1")
    resolver = text("select nucleo.resolver_campo_preliminar(:i, :f, :d, null, :a, 'api', :s)")
    valores = {"i": i["id"], "f": campo, "a": e["mc"], "s": e["sesion"]}
    fallas: list[BaseException] = []

    def descartar():
        try:
            with transaccion(motor_app, persona_id=e["mc"], consorcio_id=e["cons"]) as c:
                c.execute(resolver, valores | {"d": "descartado"})
        except BaseException as falla:  # se vuelve a lanzar abajo, en el hilo de la prueba
            fallas.append(falla)

    with transaccion(motor_app, persona_id=e["mc"], consorcio_id=e["cons"]) as primera:
        pid = primera.execute(text("select pg_backend_pid()")).scalar_one()
        primera.execute(resolver, valores | {"d": "confirmado"})
        hilo = threading.Thread(target=descartar)
        hilo.start()
        assert esperar_trabado(motor_migrate, pid) == 1  # control: la segunda espera a la primera
    hilo.join()
    if fallas:
        raise fallas[0]
    with motor_migrate.connect() as c:
        eventos = c.execute(text(
            "select antes->>'estado', despues->>'decision' from auditoria.eventos"
            " where accion = 'consorcio.liquidacion.decidir_dato' and entidad_id = :f"
            " order by secuencia"), {"f": campo}).all()  # fmt: skip
    pasos = [tuple(x) for x in eventos]
    assert pasos == [("pendiente", "confirmado"), ("confirmado", "descartado")]


@pytest.mark.parametrize(
    "cambio",
    [
        pytest.param({"total": "999,99"}, id="importe con coma"),
        pytest.param({"total": 999.99}, id="importe como número"),
        pytest.param({"otra": "x"}, id="una clave de más"),
        pytest.param({"nombre": "Pagar a 0170099220000067797370"}, id="una cuenta"),
        pytest.param({"numero": "2"}, id="un número como texto"),
        pytest.param({}, id="sin cambios"),
    ],
)
def test_una_correccion_sin_la_forma_del_leido_no_entra(
    escena, bucket, motor_app, motor_migrate, cambio
):
    i = leida(escena, motor_app, motor_migrate)
    rubro = next(c for c in i["campos"] if c["clave"] == "rubro:2")
    r = decidir(escena, i, "rubro:2", "corregido", rubro["valor"] | cambio)
    assert r.status_code == 422 and r.json()["codigo"] == "correccion_invalida", r.text


def test_la_decision_y_su_valor_van_juntos(escena, bucket, motor_app, motor_migrate):
    i = leida(escena, motor_app, motor_migrate)
    r = decidir(escena, i, "rubro:2", "corregido")
    assert r.status_code == 422 and r.json()["codigo"] == "decision_invalida", r.text
    r = decidir(escena, i, "rubro:2", "confirmado", {"total": "1.00"})
    assert r.status_code == 422 and r.json()["codigo"] == "decision_invalida", r.text
    r = decidir(escena, i, "rubro:2", "pendiente")
    assert r.status_code == 422, r.text


def test_la_importacion_se_confirma_sin_pendientes_y_solo_eso_es_oficial(
    escena, bucket, motor_app, motor_migrate
):
    """PA32 completa (RN30): con datos pendientes no se confirma; confirmada, la lectura oficial da
    los confirmados y los corregidos (con el valor corregido), y no los descartados."""
    e = escena
    i = leida(e, motor_app, motor_migrate)
    r = post(e, ruta(e, i["id"], "confirmacion"))
    assert r.status_code == 409 and r.json()["codigo"] == "campos_pendientes", r.text
    assert oficiales(e, motor_app, i) == []
    corregido = None
    for c in i["campos"]:
        if c["clave"] == "rubro:2":
            corregido = c["valor"] | {"total": "999.99"}
            assert decidir(e, i, c["clave"], "corregido", corregido).status_code == 200
        elif c["tipo"] == "saldo_unidad":
            assert decidir(e, i, c["clave"], "descartado").status_code == 200
        else:
            assert decidir(e, i, c["clave"], "confirmado").status_code == 200
    assert oficiales(e, motor_app, i) == []  # todo decidido, pero la importación no
    r = post(e, ruta(e, i["id"], "confirmacion"))
    assert r.status_code == 200, r.text
    assert (r.json()["estado"], r.json()["decidida_por"]) == ("confirmada", str(e["mc"]))
    claves = {clave: valor for clave, valor in oficiales(e, motor_app, i)}
    assert len(claves) == 13 and not any(k.startswith("saldo_unidad") for k in claves)
    assert claves["rubro:2"] == corregido
    # Confirmada, no vuelve: ni decisiones, ni otra confirmación, ni descarte.
    for respuesta in (
        decidir(e, i, "gasto:1:1", "descartado"),
        post(e, ruta(e, i["id"], "confirmacion")),
        post(e, ruta(e, i["id"], "descarte"), {"motivo": "otro"}),
    ):
        assert respuesta.status_code == 409, respuesta.text
        assert respuesta.json()["codigo"] == "importacion_decidida"


def test_aceptar_la_liquidacion_entera_confirma_lo_pendiente_con_quien_acepta(
    escena, bucket, motor_app, motor_migrate
):
    """RL1-A (26-11-2026): aceptar la liquidación entera confirma cada dato pendiente a nombre de
    quien acepta, en la misma transacción; lo corregido y lo descartado quedan como estaban. La
    importación vuelve con sus controles en palabras."""
    e = escena
    i = leida(e, motor_app, motor_migrate)
    assert [c["numero"] for c in i["controles"]] == list(range(1, 11))
    assert {c["estado"] for c in i["controles"]} <= {"bien", "para_mirar", "no_aplica"}
    assert next(c for c in i["controles"] if c["numero"] == 9)["estado"] == "no_aplica"
    rubro = next(c for c in i["campos"] if c["clave"] == "rubro:2")
    corregido = rubro["valor"] | {"total": "999.99"}
    assert decidir(e, i, "rubro:2", "corregido", corregido).status_code == 200
    assert decidir(e, i, "unidad:003", "descartado").status_code == 200
    # Control: sin `aceptar_pendientes`, la confirmación sigue negándose con datos pendientes.
    r = post(e, ruta(e, i["id"], "confirmacion"))
    assert r.status_code == 409 and r.json()["codigo"] == "campos_pendientes", r.text
    r = post(e, ruta(e, i["id"], "confirmacion"), {"aceptar_pendientes": True})
    assert r.status_code == 200, r.text
    hecha = r.json()
    assert (hecha["estado"], hecha["decidida_por"]) == ("confirmada", str(e["mc"]))
    estados = {c["clave"]: c["estado"] for c in hecha["campos"]}
    assert estados.pop("rubro:2") == "corregido" and estados.pop("unidad:003") == "descartado"
    assert set(estados.values()) == {"confirmado"}
    assert {c["confirmado_por"] for c in hecha["campos"]} == {str(e["mc"])}
    claves = {clave: valor for clave, valor in oficiales(e, motor_app, i)}
    assert len(claves) == 15 and claves["rubro:2"] == corregido and "unidad:003" not in claves
    with motor_migrate.connect() as c:
        confirmados = c.execute(text(
            "select count(*) from auditoria.eventos"
            " where accion = 'consorcio.liquidacion.decidir_dato' and consorcio_id = :c"
            " and despues->>'decision' = 'confirmado'"), {"c": e["cons"]},
        ).scalar_one()  # fmt: skip
    assert confirmados == 14  # cada dato aceptado queda auditado, como si se confirmara de a uno


def test_los_controles_usan_la_unidad_del_valor_corregido(escena, bucket, motor_app, motor_migrate):
    """Revisión de Codex (cem-aplicacion 297): corregir la unidad de un dato a una que no está en el
    padrón la saca del control del padrón, como la sacaría de lo oficial (`0135`)."""
    e = escena
    i = leida(e, motor_app, motor_migrate)
    padron = next(c for c in i["controles"] if c["numero"] == 10)
    assert padron["estado"] == "bien", padron  # control: antes de corregir, todas en el padrón
    unidad = next(c for c in i["campos"] if c["tipo"] == "unidad")
    r = decidir(e, i, unidad["clave"], "corregido", unidad["valor"] | {"depto": "ZZ-9"})
    assert r.status_code == 200, r.text
    padron = next(c for c in r.json()["controles"] if c["numero"] == 10)
    assert padron["estado"] == "para_mirar"
    assert len(padron["lineas"]) == 1 and "ZZ-9" in padron["lineas"][0]


def test_aceptar_entera_no_pisa_una_correccion_que_llega_a_la_vez(
    escena, bucket, motor_app, motor_migrate
):
    """Revisión de Codex (cem-aplicacion 297): la aceptación traba la importación antes de mirar
    qué sigue pendiente (`0135`). Si otra persona corrige un dato a la vez, la aceptación espera y
    deja la corrección como está."""
    e = escena
    i = leida(e, motor_app, motor_migrate)
    rubro = next(c for c in i["campos"] if c["clave"] == "rubro:2")
    corregido = rubro["valor"] | {"total": "999.99"}
    resolver = text("select nucleo.resolver_campo_preliminar(:i, :f, 'corregido',"
                    " cast(:v as jsonb), :a, 'api', :s)")  # fmt: skip
    aceptar = text("select nucleo.aceptar_datos_pendientes(:i, :a, 'api', :s)")
    valores = {"i": i["id"], "a": e["mc"], "s": e["sesion"]}
    aceptados: list[int] = []
    fallas: list[BaseException] = []

    def aceptar_todo():
        try:
            with transaccion(motor_app, persona_id=e["mc"], consorcio_id=e["cons"]) as c:
                aceptados.append(c.execute(aceptar, valores).scalar_one())
        except BaseException as falla:  # se vuelve a lanzar abajo, en el hilo de la prueba
            fallas.append(falla)

    with transaccion(motor_app, persona_id=e["mc"], consorcio_id=e["cons"]) as primera:
        pid = primera.execute(text("select pg_backend_pid()")).scalar_one()
        primera.execute(resolver, valores | {"f": rubro["id"], "v": json.dumps(corregido)})
        hilo = threading.Thread(target=aceptar_todo)
        hilo.start()
        assert esperar_trabado(motor_migrate, pid) == 1  # control: la aceptación espera
    hilo.join()
    if fallas:
        raise fallas[0]
    assert aceptados == [15]  # los 16 menos el corregido
    with cliente(e["mc"], e["sesion"]) as c:
        campos = {x["clave"]: x for x in c.get(ruta(e, i["id"])).json()["campos"]}
    assert campos["rubro:2"]["estado"] == "corregido"
    assert campos["rubro:2"]["valor_corregido"] == corregido


def test_aceptar_entera_con_un_dato_mal_leido_no_deja_nada_a_medias(
    escena, bucket, motor_app, motor_migrate
):
    """Si la confirmación se niega después de aceptar los pendientes (aquí: el período ya tiene su
    oficial), nada queda confirmado: es una sola transacción."""
    e = escena
    i = leida(e, motor_app, motor_migrate)
    r = post(e, ruta(e, i["id"], "confirmacion"), {"aceptar_pendientes": True})
    assert r.status_code == 200, r.text
    otra = importada(e, motor_app, pdf_de(liquidacion()))
    r = post(e, ruta(e, otra["id"], "confirmacion"), {"aceptar_pendientes": True})
    assert r.status_code == 409 and r.json()["codigo"] == "periodo_ya_confirmado", r.text
    with cliente(e["mc"], e["sesion"]) as c:
        sigue = c.get(ruta(e, otra["id"])).json()
    assert sigue["estado"] == "para_confirmar"
    assert {c["estado"] for c in sigue["campos"]} == {"pendiente"}


def test_una_unidad_corregida_es_oficial_con_la_unidad_del_valor_corregido(
    escena, bucket, motor_app, motor_migrate
):
    """Si quien importa corrige el departamento de un dato de unidad, la lectura oficial lo asocia a
    la unidad del padrón del valor corregido; lo leído conserva su unidad."""
    e = escena
    i = leida(e, motor_app, motor_migrate)
    # A una unidad sin datos en la liquidación: «PB-A» ya tiene el suyo, y desde `0119` dos datos
    # del mismo tipo en una unidad no se confirman (`unidad_repetida`).
    with motor_migrate.begin() as c:
        c.execute(text("insert into nucleo.unidad (consorcio_id, designacion) values (:c, 'PB-B')"),
                  {"c": e["cons"]})  # fmt: skip
        padron = dict(c.execute(
            text("select designacion, id from nucleo.unidad where consorcio_id = :c"),
            {"c": e["cons"]}).all())  # fmt: skip
    cambios = {"unidad:002": {"depto": "PB-B"}, "importe_unidad:002": {"depto": "Z-9"}}
    for c in i["campos"]:
        if c["clave"] in cambios:
            r = decidir(e, i, c["clave"], "corregido", c["valor"] | cambios[c["clave"]])
            assert r.status_code == 200, r.text
        else:
            assert decidir(e, i, c["clave"], "confirmado").status_code == 200
    assert post(e, ruta(e, i["id"], "confirmacion")).status_code == 200
    with transaccion(motor_app, persona_id=e["mc"], consorcio_id=e["cons"]) as c:
        unidad = dict(c.execute(
            text("select clave, unidad_id from nucleo.campos_oficiales_liquidacion("
                 ":i, :a, 'api', :s)"),
            {"i": i["id"], "a": e["mc"], "s": e["sesion"]}).all())  # fmt: skip
    leido = {c["clave"]: c["unidad_id"] for c in i["campos"]}
    assert leido["unidad:002"] == leido["importe_unidad:002"] == str(padron["1-A"])
    # Control: lo confirmado sin corregir sigue con la unidad leída.
    assert unidad["saldo_unidad:002"] == padron["1-A"]
    assert unidad["unidad:002"] == padron["PB-B"]
    assert unidad["importe_unidad:002"] is None  # «Z-9» no está en el padrón


def test_una_importacion_que_no_sirve_se_descarta_con_su_motivo(
    escena, bucket, motor_app, motor_migrate
):
    e = escena
    sin_formato = importada(e, motor_app, pdf())
    assert sin_formato["estado"] == "no_reconocida"
    r = post(e, ruta(e, sin_formato["id"], "descarte"), {"motivo": ""})
    assert r.status_code == 422, r.text
    r = post(
        e, ruta(e, sin_formato["id"], "descarte"), {"motivo": "  Es una foto, se carga a mano "}
    )
    assert r.status_code == 200, r.text
    d = r.json()
    assert (d["estado"], d["motivo_descarte"], d["decidida_por"]) == (
        "descartada", "Es una foto, se carga a mano", str(e["mc"]))  # fmt: skip
    # El motivo es texto libre: queda en la importación y no en la auditoría (E31).
    with motor_migrate.connect() as c:
        despues = c.execute(text(
            "select despues from auditoria.eventos where entidad_id = :i"
            " and accion = 'consorcio.liquidacion.descartar_importada'"),
            {"i": sin_formato["id"]}).scalar_one()  # fmt: skip
    assert despues == {"estado": "descartada"}
    # Una leída también se descarta, y sus datos quedan como estaban.
    i = leida(e, motor_app, motor_migrate)
    r = post(e, ruta(e, i["id"], "descarte"), {"motivo": "Es de otro edificio"})
    assert r.status_code == 200 and len(r.json()["campos"]) == 16, r.text
    assert oficiales(e, motor_app, i) == []


def test_un_dato_de_otra_importacion_o_inexistente_es_404(escena, bucket, motor_app, motor_migrate):
    e = escena
    i = leida(e, motor_app, motor_migrate)
    otra = importada(e, motor_app, pdf_de(liquidacion(gasto_luz=("1.000,00", "0,00", "1.000,05"))))
    campo_ajeno = otra["campos"][0]["id"]
    r = post(e, ruta(e, i["id"], "campos", campo_ajeno, "decision"), {"decision": "confirmado"})
    assert r.status_code == 404, r.text
    r = post(e, ruta(e, str(uuid4()), "confirmacion"))
    assert r.status_code == 404, r.text


def test_sin_la_facultad_no_se_ve_ni_se_decide(escena, bucket, motor_app, motor_migrate):
    e = escena
    i = leida(e, motor_app, motor_migrate)
    sesion = nueva_sesion(motor_migrate, e["prop"])
    with cliente(e["prop"], sesion) as c:
        assert c.get(ruta(e)).status_code == 403
    r = decidir(e, i, "gasto:1:1", "confirmado", quien=e["prop"], sesion=sesion)
    assert r.status_code == 403, r.text
    r = post(e, ruta(e, i["id"], "confirmacion"), quien=e["prop"], sesion=sesion)
    assert r.status_code == 403, r.text


def test_lo_leido_no_cambia_y_la_decision_solo_mientras_se_confirma(
    escena, bucket, motor_app, motor_migrate
):
    e = escena
    i = leida(e, motor_app, motor_migrate)
    for sentencia in (
        "update nucleo.campo_preliminar set valor = '{}' where importacion_id = :i",
        "update nucleo.campo_preliminar set fragmento = 'otro' where importacion_id = :i",
        "update nucleo.campo_preliminar set estado = 'pendiente' where importacion_id = :i",
        "delete from nucleo.campo_preliminar where importacion_id = :i",
        "update nucleo.importacion_liquidacion set estado = 'confirmada' where id = :i",
    ):
        with pytest.raises(DBAPIError) as error, motor_migrate.begin() as c:
            c.execute(text(sentencia), {"i": i["id"]})
        hint = error.value.orig.diag.message_hint
        # La importación sin quién la decidió choca antes con su restricción.
        assert hint == "historia_inmutable" or "decidida" in str(error.value), sentencia
    assert post(e, ruta(e, i["id"], "descarte"), {"motivo": "prueba"}).status_code == 200
    with pytest.raises(DBAPIError) as error, motor_migrate.begin() as c:
        c.execute(text("update nucleo.campo_preliminar set estado = 'confirmado', confirmado_por"
                       " = :p, confirmado_en = now() where importacion_id = :i"),
                  {"i": i["id"], "p": e["mc"]})  # fmt: skip
    assert error.value.orig.diag.message_hint == "historia_inmutable"


@pytest.mark.parametrize(
    ("leido", "corregido", "vale"),
    [
        ({"a": "1.00"}, {"a": "2.00"}, True),
        ({"a": "1.00"}, {"a": None}, True),
        ({"a": None}, {"a": "Texto"}, True),
        ({"a": {"b": "1.00"}}, {"a": {"b": "1,00"}}, False),
        ({"a": 1}, {"a": 2}, True),
        ({"a": 1}, {"a": True}, False),
        ({"a": ["x"]}, {"a": ["y"]}, True),
        ({"a": "x"}, {"a": " "}, False),
        # Un importe vacío (la IA puede no leer el total de un rubro) se corrige con un importe.
        ({"nombre": "Luz", "total": None}, {"nombre": "Luz", "total": "12.50"}, True),
        ({"nombre": "Luz", "total": None}, {"nombre": "Luz", "total": "no es un importe"}, False),
        ({"nombre": "Luz", "total": None}, {"nombre": "Luz", "total": "1,25"}, False),
        ({"total": None}, {"total": "12345678901234567.00"}, False),
        ({"importes": {"AySA": None}}, {"importes": {"AySA": "texto"}}, False),
        ({"grupos": {"A": {"monto": None}}}, {"grupos": {"A": {"monto": "x"}}}, False),
    ],
)
def test_forma_igual(leido, corregido, vale):
    assert forma_igual(leido, corregido) is vale
