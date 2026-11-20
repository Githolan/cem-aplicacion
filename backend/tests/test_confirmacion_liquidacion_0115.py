"""Confirmación campo por campo de la liquidación importada, H5-3 (`0115`; diseño H5 §5; PA32).

Contra servicios reales: PostgreSQL, el almacén S3 de pruebas y ClamAV. La liquidación es la
inventada de H5-1 (`test_mis_expensas.liquidacion`), leída sin IA. Datos SINTETICOS / NO REAL.
"""

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db import transaccion
from app.liquidacion.importacion import forma_igual
from tests.conftest import clave_idempotencia
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
            " where accion = 'consorcio.liquidacion.resolver_campo' and consorcio_id = :c"
            " order by secuencia"), {"c": e["cons"]}).all()  # fmt: skip
    assert [tuple(x) for x in eventos] == [
        ("gasto:1:1", "confirmado"),
        ("rubro:2", "corregido"),
        ("unidad:003", "descartado"),
        ("gasto:1:1", "descartado"),
    ]


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
    ],
)
def test_forma_igual(leido, corregido, vale):
    assert forma_igual(leido, corregido) is vale
