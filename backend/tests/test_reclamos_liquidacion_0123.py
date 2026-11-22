"""Reclamos sobre la liquidación oficial (`0123`; RF34 en Modo Consejo, diseño P0 S7), con el
borrador sin conexión que el servidor revalida al recibirlo (RF38, PA16).

Contra servicios reales: PostgreSQL, el almacén S3 de pruebas y ClamAV. La escena es la de `0119`
(Modo Consejo): un miembro del Consejo, la administración sin vínculo, la propietaria confirmada de
«1° A» y «1-A» (la unidad 002 de la liquidación; «1° A» no figura), una propietaria sin confirmar
de «1-A», una inquilina confirmada de «PB-A», más una ocupante confirmada de «PB-A». Cada negación
lleva su control: el mismo pedido de quien sí puede, o en el estado que sí lo admite, pasa. Datos
SINTETICOS / NO REAL.
"""

import importlib.util
import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from cem_contracts import reclamos as contrato
from psycopg.errors import InsufficientPrivilege
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db import fijar_ambito
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
from tests.test_liquidacion_oficial_0119 import (  # noqa: F401  (los fixtures se importan)
    armar_escena,
    bucket,
    confirmada,
    confirmar,
    firmas_de_la_imagen,
    get,
    importar,
    persona_con_vinculo,
)
from tests.test_modo_operacion import hoy_de
from tests.test_orden_pago_neutra import cbu_sintetico, nueva_sesion
from tests.test_rutas_egresos import cliente
from tests.test_vinculo_0014 import base_aparte

pytestmark = [pytest.mark.db, pytest.mark.redis]
# ruff: noqa: F811

__all__ = ["base_aparte", "bucket", "firmas_de_la_imagen"]

VERSIONES = Path(__file__).resolve().parent.parent / "migrations/versions"
MIGRACION = VERSIONES / "0123_reclamos_liquidacion.py"
TEXTO = "El saldo anterior no coincide con lo que pagué en agosto.\nDatos de PRUEBA SINTETICA."
RESPUESTA = "Se revisó: el pago de agosto entró el 2 de septiembre. Datos de PRUEBA."
CUENTA = cbu_sintetico(43)
TABLAS = ("nucleo.reclamo_liquidacion", "nucleo.reclamo_liquidacion_estado")
PRESENTAR = text(
    "select * from nucleo.presentar_reclamo_liquidacion(:i, :u, null, 'T', false, :a, 'api', :s)"
)
LEER = text("select * from nucleo.reclamos_liquidacion_lectura(:a, 'api', :s)")
SOLICITUD = {
    "objeto": "periodo",
    "referencia": "Liquidación de septiembre de 2026 SINTETICA",
    "documentacion": "La base del saldo de la unidad 1-A.",
}


def _migracion():
    spec = importlib.util.spec_from_file_location("migracion_0123", MIGRACION)
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def armar(motor_migrate, motor_app) -> dict:
    """La escena de `0119` con la liquidación de septiembre confirmada, y una ocupante confirmada
    de «PB-A»."""
    e = armar_escena(motor_migrate, motor_app)
    e["oc"] = persona_con_vinculo(motor_migrate, e["cons"], e["u"]["PB-A"], "ocupante",
                                  confirmado=True)  # fmt: skip
    e["sesion_oc"] = nueva_sesion(motor_migrate, e["oc"])
    e["hoy"] = hoy_de(motor_app, e["cons"])
    e["liq"] = confirmada(e, motor_app)
    return e


@pytest.fixture
def e(motor_migrate, motor_app, bucket) -> dict:
    return armar(motor_migrate, motor_app)


def url(e: dict, *partes: str) -> str:
    return "/".join([f"/api/v1/consorcios/{e['cons']}/reclamos-liquidacion", *partes])


def cuerpo(e: dict, unidad: str = "1-A", **cambios) -> dict:
    return {"liquidacion_id": e["liq"], "unidad_id": str(e["u"][unidad]), "texto": TEXTO} | cambios


def presentar(e: dict, quien: str = "prop", datos: dict | None = None, clave: dict | None = None):
    with cliente(e[quien], e[f"sesion_{quien}"]) as c:
        return c.post(url(e), json=cuerpo(e) if datos is None else datos,
                      headers=clave or clave_idempotencia())  # fmt: skip


def presentado(e: dict, quien: str = "prop", **cambios) -> dict:
    r = presentar(e, quien, cuerpo(e, **cambios))
    assert r.status_code == 201, r.text
    return r.json()


def actuar(e: dict, quien: str, reclamo: dict, acto: str, datos: dict):
    with cliente(e[quien], e[f"sesion_{quien}"]) as c:
        return c.post(url(e, reclamo["id"], acto), json=datos, headers=clave_idempotencia())


def leer(e: dict, quien: str, *partes: str):
    with cliente(e[quien], e[f"sesion_{quien}"]) as c:
        return c.get(url(e, *partes))


def preparar_solicitud(e: dict, quien: str, reclamo: dict | None):
    datos = SOLICITUD | ({} if reclamo is None else {"reclamo_id": reclamo["id"]})
    with cliente(e[quien], e[f"sesion_{quien}"]) as c:
        return c.post(f"/api/v1/consorcios/{e['cons']}/solicitudes-formales", json=datos,
                      headers=clave_idempotencia())  # fmt: skip


def correos(motor_migrate, cons) -> list[dict]:
    with motor_migrate.connect() as c:
        return [dict(f) for f in c.execute(
            text("select id, carga from plataforma.outbox where evento = 'correo.solicitado'"
                 " and consorcio_id = :c order by id"), {"c": cons},
        ).mappings()]  # fmt: skip


def correo_de(motor_migrate, persona) -> str:
    with motor_migrate.connect() as c:
        return c.execute(
            text("select correo from nucleo.persona where id = :p"), {"p": persona}
        ).scalar_one()


def cuantos(motor_migrate, tabla: str, cons) -> int:
    with motor_migrate.connect() as c:
        return c.execute(
            text(f"select count(*) from {tabla} where consorcio_id = :c"), {"c": cons}
        ).scalar_one()


def codigo(r) -> tuple[int, str]:
    return r.status_code, r.json()["codigo"]


def hint(error: pytest.ExceptionInfo) -> str | None:
    return error.value.orig.diag.message_hint


def en_la_base(motor_app, e: dict, quien: str, sql, **p):
    """La función de la base con la persona y su sesión, deshecha al final."""
    with motor_app.connect() as cx, cx.begin() as t:
        fijar_ambito(cx, persona_id=e[quien], consorcio_id=e["cons"])
        filas = cx.execute(sql, {"a": e[quien], "s": e[f"sesion_{quien}"]} | p).all()
        t.rollback()
        return filas


def terminar_vinculo(motor_migrate, e: dict, persona: str, unidad: str) -> None:
    """El vínculo de la persona con la unidad deja de estar vigente (la venta, por ejemplo)."""
    with motor_migrate.begin() as c:
        cambiadas = c.execute(text(
            "update nucleo.vinculo set vigente_hasta = vigente_desde + 1"
            " where consorcio_id = :c and persona_id = :p and unidad_id = :u"),
            {"c": e["cons"], "p": e[persona], "u": e["u"][unidad]}).rowcount  # fmt: skip
    assert cambiadas == 1


# Constantes: una sola fuente ----------------------------------------------------------------


def test_la_migracion_copia_las_constantes_del_contrato():
    m = _migracion()
    assert (m.LARGO_TEXTO, m.LARGO_MOTIVO) == (contrato.LARGO_TEXTO, contrato.LARGO_MOTIVO)
    assert set(m.CAMPOS_CITABLES) == {c.value for c in contrato.CampoCitado}
    assert set(m.ESTADOS) == {x.value for x in contrato.EstadoReclamo}


# Presentar ----------------------------------------------------------------------------------


def test_presentar_sobre_su_unidad_avisa_a_quien_atiende_sin_el_texto(e, motor_migrate):
    r = presentado(e, campo="saldo_unidad")
    assert (r["estado"], r["campo"], r["propio"], r["liquidacion_vigente"]) == (
        "presentado", "saldo_unidad", True, True)  # fmt: skip
    assert r["texto"] == TEXTO and r["designacion"] == "1-A" and r["periodo"] == "2026-09-01"
    assert [p["estado"] for p in r["historia"]] == ["presentado"]
    assert r["acciones"] == ["retirar"]
    enviados = correos(motor_migrate, e["cons"])
    para = sorted(x["carga"]["para"] for x in enviados)
    # Un correo a cada persona con `reclamos.atender`: el Consejo y la administración.
    assert para == sorted(correo_de(motor_migrate, e[q]) for q in ("mc", "ad"))
    for quien in ("prop", "inq", "sin_confirmar"):
        assert correo_de(motor_migrate, e[quien]) not in para
    # El título y el enlace; nunca el texto, la unidad ni quién lo presentó.
    for x in enviados:
        carga = x["carga"]
        assert "https://cem.ejemplo.com/reclamos" in carga["texto"]
        assert "09/2026" in carga["texto"]
        assert "agosto" not in carga["texto"] + carga["asunto"]
        assert "1-A" not in carga["texto"] + carga["asunto"]
        assert "@" not in carga["texto"] + carga["asunto"]


def test_solo_sobre_una_unidad_propia_y_un_dato_que_figura(e, motor_migrate):
    # «PB-A» es de la inquilina: la propietaria no reclama por ella.
    assert codigo(presentar(e, datos=cuerpo(e, "PB-A"))) == (403, "sin_permiso")
    # «1° A» es suya y no figura en la liquidación: reclama sin citar un dato…
    sin_dato = presentado(e, unidad_id=str(e["u"]["1° A"]))
    assert sin_dato["designacion"] == "1° A" and sin_dato["campo"] is None
    # …pero un dato que no figura para esa unidad no se cita.
    r = presentar(e, datos=cuerpo(e, "1° A", campo="importe_unidad"))
    assert codigo(r) == (422, "peticion_invalida")
    assert r.json()["detalle"] == {"campos": ["body.campo"]}
    # Control: el mismo dato de «1-A», que figura, sí.
    assert presentado(e, campo="importe_unidad")["campo"] == "importe_unidad"
    assert cuantos(motor_migrate, "nucleo.reclamo_liquidacion", e["cons"]) == 2


def test_una_importacion_que_no_es_oficial_no_se_cita(e, motor_app, motor_migrate):
    sin_confirmar = importar(e, motor_app)  # leída y decidida, sin confirmar
    r = presentar(e, datos=cuerpo(e, liquidacion_id=sin_confirmar))
    assert codigo(r) == (422, "peticion_invalida")
    assert r.json()["detalle"] == {"campos": ["body.liquidacion_id"]}
    r = presentar(e, datos=cuerpo(e, liquidacion_id=str(uuid4())))
    assert codigo(r) == (422, "peticion_invalida")
    assert cuantos(motor_migrate, "nucleo.reclamo_liquidacion", e["cons"]) == 0


def test_inquilina_ocupante_y_sin_confirmar_no_presentan_y_la_base_niega_igual(e, motor_app):
    assert codigo(presentar(e, "inq", cuerpo(e, "PB-A"))) == (403, "sin_permiso")
    assert codigo(presentar(e, "oc", cuerpo(e, "PB-A"))) == (403, "sin_permiso")
    assert codigo(presentar(e, "sin_confirmar", cuerpo(e, "1-A"))) == (403, "sin_permiso")
    # Segunda barrera: la base niega por su cuenta (42501).
    for quien, unidad in (("inq", "PB-A"), ("oc", "PB-A"), ("sin_confirmar", "1-A"),
                          ("prop", "PB-A")):  # fmt: skip
        with pytest.raises(DBAPIError) as error:
            en_la_base(motor_app, e, quien, PRESENTAR, i=e["liq"], u=e["u"][unidad])
        assert isinstance(error.value.orig, InsufficientPrivilege), quien
    # Control: la propietaria confirmada, sobre «1-A», pasa en la base.
    assert len(en_la_base(motor_app, e, "prop", PRESENTAR, i=e["liq"], u=e["u"]["1-A"])) == 2


def test_una_cuenta_en_el_texto_se_rechaza(e, motor_migrate):
    for texto in (f"Transferí a {CUENTA}", f"CBU {CUENTA[:8]} {CUENTA[8:]}."):
        r = presentar(e, datos=cuerpo(e, texto=texto))
        assert codigo(r) == (422, "texto_con_cuenta"), texto
        assert r.json()["detalle"] == {"campos": ["body.texto"]}
    assert cuantos(motor_migrate, "nucleo.reclamo_liquidacion", e["cons"]) == 0
    assert correos(motor_migrate, e["cons"]) == []
    # Control: 21 dígitos no son una cuenta.
    presentado(e, texto=f"Expediente {CUENTA[:21]}.")
    # Y la respuesta tampoco la lleva.
    reclamo = presentado(e)
    r = actuar(e, "mc", reclamo, "respuesta", {"texto": f"Pagar a {CUENTA}"})
    assert codigo(r) == (422, "texto_con_cuenta")
    assert actuar(e, "mc", reclamo, "respuesta", {"texto": RESPUESTA}).status_code == 200


def test_rf34_un_reclamo_no_altera_ningun_importe_ni_la_liquidacion(e, motor_migrate):
    """RF34: «sin alterar el saldo por sí sola». Los datos oficiales, la cuenta y el resumen del
    propietario y las filas de la importación quedan byte a byte iguales después de presentar,
    responder, retirar y pasar a una solicitud formal."""

    def foto() -> tuple:
        with motor_migrate.connect() as c:
            filas = tuple(c.execute(text(
                "select (select md5(string_agg(x::text, '|' order by x.id))"
                "   from nucleo.importacion_liquidacion x where x.consorcio_id = :c),"
                " (select md5(string_agg(p::text, '|' order by p.id))"
                "   from nucleo.campo_preliminar p where p.consorcio_id = :c),"
                " (select md5(string_agg(f::text, '|' order by f.orden))"
                "   from nucleo.campos_oficiales_de(:c, :i) f)"),
                {"c": e["cons"], "i": e["liq"]}).one())  # fmt: skip
        cuenta = get(e, "prop", "mi-cuenta")
        resumen = get(e, "prop", "resumen")
        assert cuenta.status_code == resumen.status_code == 200
        return filas, cuenta.content, resumen.content

    antes = foto()
    assert all(antes[0]) and b"1326.00" in antes[1]  # control: hay qué comparar
    a = presentado(e, campo="saldo_unidad")
    actuar(e, "mc", a, "respuesta", {"texto": RESPUESTA})
    b = presentado(e, campo="importe_unidad")
    actuar(e, "prop", b, "retiro", {"motivo": "Ya lo aclaré con la administración."})
    c = presentado(e)
    assert preparar_solicitud(e, "mc", c).status_code == 201
    assert foto() == antes


def test_reintentar_con_la_misma_clave_no_repite_el_reclamo_ni_los_correos(e, motor_migrate):
    """El borrador reintenta con su clave: si el primer envío llegó, no queda repetido."""
    clave = clave_idempotencia()
    primera = presentar(e, clave=clave)
    assert primera.status_code == 201, primera.text
    segunda = presentar(e, clave=clave)
    assert segunda.status_code == 201
    assert segunda.headers.get("Idempotent-Replayed") == "true"
    assert segunda.json() == primera.json()
    assert cuantos(motor_migrate, "nucleo.reclamo_liquidacion", e["cons"]) == 1
    assert len(correos(motor_migrate, e["cons"])) == 2
    # Control: otra clave es otro reclamo, con sus correos.
    assert presentar(e).status_code == 201
    assert cuantos(motor_migrate, "nucleo.reclamo_liquidacion", e["cons"]) == 2
    assert len(correos(motor_migrate, e["cons"])) == 4


# El borrador sin conexión: se revalida al sincronizar (RF38, PA16) --------------------------


def test_pa16_borrador_con_el_permiso_vencido_se_rechaza_al_sincronizar(e, motor_migrate,
                                                                       motor_app):  # fmt: skip
    """El borrador se escribió con el vínculo vigente; cuando llega, el vínculo ya terminó. Se
    rechaza y no queda nada: ni el reclamo ni los correos."""
    borrador = cuerpo(e, campo="saldo_unidad")
    clave = clave_idempotencia()  # la que el borrador guarda desde que se pidió enviar
    # Control: con el vínculo vigente, el mismo pedido sobre la otra unidad propia pasa.
    assert presentar(e, datos=cuerpo(e, "1° A")).status_code == 201
    correos_antes = len(correos(motor_migrate, e["cons"]))
    terminar_vinculo(motor_migrate, e, "prop", "1-A")
    r = presentar(e, datos=borrador, clave=clave)
    assert codigo(r) == (403, "sin_permiso")
    assert cuantos(motor_migrate, "nucleo.reclamo_liquidacion", e["cons"]) == 1
    assert len(correos(motor_migrate, e["cons"])) == correos_antes
    # La base, por su cuenta, también lo niega.
    with pytest.raises(DBAPIError) as error:
        en_la_base(motor_app, e, "prop", PRESENTAR, i=e["liq"], u=e["u"]["1-A"])
    assert isinstance(error.value.orig, InsufficientPrivilege)
    # Sin ningún vínculo en el edificio, el mismo borrador ni siquiera encuentra el edificio.
    terminar_vinculo(motor_migrate, e, "prop", "1° A")
    assert codigo(presentar(e, datos=borrador, clave=clave)) == (404, "no_encontrado")
    assert cuantos(motor_migrate, "nucleo.reclamo_liquidacion", e["cons"]) == 1


def test_la_liquidacion_reemplazada_desde_el_borrador_se_niega_y_se_puede_citar_la_vigente(
    e, motor_app, motor_migrate
):
    borrador = cuerpo(e, campo="saldo_unidad")
    b = importar(e, motor_app)
    r = confirmar(e, b, {"reemplaza_a": e["liq"], "motivo": "Llegó corregida"})
    assert r.status_code == 200, r.text
    r = presentar(e, datos=borrador)
    assert codigo(r) == (409, "liquidacion_reemplazada")
    assert cuantos(motor_migrate, "nucleo.reclamo_liquidacion", e["cons"]) == 0
    assert correos(motor_migrate, e["cons"]) == []
    # Citar la vigente pasa…
    vigente = presentado(e, campo="saldo_unidad", liquidacion_id=b)
    assert (vigente["liquidacion_id"], vigente["liquidacion_vigente"]) == (b, True)
    # …y también reclamar sobre la reemplazada a sabiendas, que queda marcada así.
    sobre_vieja = presentado(e, campo="saldo_unidad", acepta_reemplazada=True)
    assert (sobre_vieja["liquidacion_id"], sobre_vieja["liquidacion_vigente"]) == (
        e["liq"], False)  # fmt: skip


# Atender: responder o pasar a una solicitud formal --------------------------------------------


def test_responder_solo_quien_atiende_y_una_sola_vez(e, motor_migrate, motor_app):
    reclamo = presentado(e)
    assert codigo(actuar(e, "prop", reclamo, "respuesta", {"texto": RESPUESTA})) == (
        403, "sin_permiso")  # fmt: skip
    with pytest.raises(DBAPIError) as error:
        en_la_base(motor_app, e, "prop", text(
            "select * from nucleo.responder_reclamo_liquidacion(:r, 'X', :a, 'api', :s)"),
            r=reclamo["id"])  # fmt: skip
    assert isinstance(error.value.orig, InsufficientPrivilege)
    antes = len(correos(motor_migrate, e["cons"]))
    r = actuar(e, "ad", reclamo, "respuesta", {"texto": RESPUESTA})
    assert r.status_code == 200, r.text
    assert r.json()["estado"] == "respondido" and r.json()["acciones"] == []
    assert [(p["estado"], p["texto"]) for p in r.json()["historia"]] == [
        ("presentado", None), ("respondido", RESPUESTA)]  # fmt: skip
    # Un correo a quien lo presentó, sin la respuesta.
    nuevos = correos(motor_migrate, e["cons"])[antes:]
    assert [x["carga"]["para"] for x in nuevos] == [correo_de(motor_migrate, e["prop"])]
    assert "pago de agosto" not in nuevos[0]["carga"]["texto"]
    assert "https://cem.ejemplo.com/mis-expensas" in nuevos[0]["carga"]["texto"]
    # La propietaria ve la respuesta.
    assert leer(e, "prop", reclamo["id"]).json()["historia"][-1]["texto"] == RESPUESTA
    # Una sola vez: ya cerrado, 409.
    assert codigo(actuar(e, "mc", reclamo, "respuesta", {"texto": RESPUESTA})) == (
        409, "reclamo_cerrado")  # fmt: skip


def test_pasar_a_solicitud_formal_solo_quien_atiende_y_prepara(e, motor_migrate):
    reclamo = presentado(e)
    # La administración atiende pero no prepara solicitudes formales: no se le ofrece.
    assert leer(e, "ad", reclamo["id"]).json()["acciones"] == ["responder"]
    assert codigo(preparar_solicitud(e, "ad", reclamo)) == (403, "sin_permiso")
    assert codigo(preparar_solicitud(e, "prop", reclamo)) == (403, "sin_permiso")
    assert leer(e, "mc", reclamo["id"]).json()["acciones"] == ["responder", "pasar_a_solicitud"]
    r = preparar_solicitud(e, "mc", reclamo)
    assert r.status_code == 201, r.text
    solicitud = r.json()
    visto = leer(e, "prop", reclamo["id"]).json()
    assert visto["estado"] == "derivado_a_solicitud"
    paso = visto["historia"][-1]
    assert (paso["solicitud_id"], paso["solicitud_numero"]) == (solicitud["id"],
                                                                solicitud["numero"])  # fmt: skip
    # Otra solicitud para el mismo reclamo: 409 y la solicitud no queda.
    solicitudes = cuantos(motor_migrate, "nucleo.solicitud_formal", e["cons"])
    assert codigo(preparar_solicitud(e, "mc", reclamo)) == (409, "reclamo_cerrado")
    assert cuantos(motor_migrate, "nucleo.solicitud_formal", e["cons"]) == solicitudes
    # Control: sin reclamo, la misma solicitud se prepara como siempre.
    assert preparar_solicitud(e, "mc", None).status_code == 201


def test_retirar_solo_quien_lo_presento_mientras_esta_presentado(e, motor_migrate):
    reclamo = presentado(e)
    motivo = {"motivo": "Ya lo aclaré con la administración."}
    # Quien atiende lo ve, pero no lo retira.
    assert codigo(actuar(e, "mc", reclamo, "retiro", motivo)) == (403, "sin_permiso")
    # Otra propietaria confirmada de «1-A» ni lo ve.
    e["prop2"] = persona_con_vinculo(motor_migrate, e["cons"], e["u"]["1-A"], "propietario",
                                     confirmado=True)  # fmt: skip
    e["sesion_prop2"] = nueva_sesion(motor_migrate, e["prop2"])
    assert leer(e, "prop2").json()["elementos"] == []
    assert codigo(actuar(e, "prop2", reclamo, "retiro", motivo)) == (404, "no_encontrado")
    # Quien lo presentó, sí, una vez.
    r = actuar(e, "prop", reclamo, "retiro", motivo)
    assert r.status_code == 200, r.text
    assert (r.json()["estado"], r.json()["historia"][-1]["texto"]) == ("retirado", motivo["motivo"])
    assert codigo(actuar(e, "prop", reclamo, "retiro", motivo)) == (409, "reclamo_cerrado")
    # Retirado, ya no se responde.
    assert codigo(actuar(e, "mc", reclamo, "respuesta", {"texto": RESPUESTA})) == (
        409, "reclamo_cerrado")  # fmt: skip


def test_un_reclamo_respondido_ya_no_se_retira(e):
    reclamo = presentado(e)
    assert actuar(e, "mc", reclamo, "respuesta", {"texto": RESPUESTA}).status_code == 200
    r = actuar(e, "prop", reclamo, "retiro", {"motivo": "Tarde"})
    assert codigo(r) == (409, "reclamo_cerrado")


# Lo presentado no se cambia -----------------------------------------------------------------


def test_un_reclamo_no_se_cambia_ni_se_borra(e, motor_migrate):
    reclamo = presentado(e)
    intentos = [
        "update nucleo.reclamo_liquidacion set texto = 'Otro' where id = :r",
        "delete from nucleo.reclamo_liquidacion where id = :r",
        "update nucleo.reclamo_liquidacion_estado set registrado_en = now() where reclamo_id = :r",
        "delete from nucleo.reclamo_liquidacion_estado where reclamo_id = :r",
        "truncate nucleo.reclamo_liquidacion_estado",
    ]
    for sql in intentos:
        with pytest.raises(DBAPIError) as error:
            with motor_migrate.begin() as cx:
                cx.execute(text(sql), {"r": reclamo["id"]})
        assert hint(error) == "historia_inmutable", sql
    assert leer(e, "prop", reclamo["id"]).json() == reclamo  # sigue igual
    # Un segundo cierre choca con la última barrera, aunque se salte la función.
    with motor_migrate.begin() as cx:
        quien = cx.execute(text("select presentado_por from nucleo.reclamo_liquidacion"
                                " where id = :r"), {"r": reclamo["id"]}).scalar_one()  # fmt: skip
        cx.execute(text(
            "insert into nucleo.reclamo_liquidacion_estado (consorcio_id, reclamo_id, estado,"
            " texto, registrado_por) values (:c, :r, 'respondido', 'R', :q)"),
            {"c": e["cons"], "r": reclamo["id"], "q": quien})  # fmt: skip
    with pytest.raises(DBAPIError) as error:
        with motor_migrate.begin() as cx:
            cx.execute(text(
                "insert into nucleo.reclamo_liquidacion_estado (consorcio_id, reclamo_id, estado,"
                " texto, registrado_por) values (:c, :r, 'retirado', 'M', :q)"),
                {"c": e["cons"], "r": reclamo["id"], "q": quien})  # fmt: skip
    assert error.value.orig.diag.constraint_name == "reclamo_un_cierre"
    # Y `cem_app` no escribe en ninguna de las tablas, que tienen la RLS habilitada y forzada.
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
                text("select has_table_privilege('cem_app', :t, 'insert, update, delete')"),
                {"t": tabla}).scalar_one(), tabla  # fmt: skip


# Quién lee ----------------------------------------------------------------------------------


def test_el_propietario_ve_los_suyos_y_quien_atiende_ve_todos(e, motor_migrate, motor_app):
    propio = presentado(e)
    e["prop2"] = persona_con_vinculo(motor_migrate, e["cons"], e["u"]["PB-A"], "propietario",
                                     confirmado=True)  # fmt: skip
    e["sesion_prop2"] = nueva_sesion(motor_migrate, e["prop2"])
    ajeno = presentado(e, "prop2", unidad_id=str(e["u"]["PB-A"]))
    de_prop = leer(e, "prop").json()
    assert [x["id"] for x in de_prop["elementos"]] == [propio["id"]] and not de_prop["atiende"]
    assert leer(e, "prop", ajeno["id"]).status_code == 404
    for quien in ("mc", "ad"):
        lista = leer(e, quien).json()
        assert lista["atiende"] is True
        assert [x["id"] for x in lista["elementos"]] == [ajeno["id"], propio["id"]], quien
        assert {x["propio"] for x in lista["elementos"]} == {False}
    # Inquilina, ocupante y sin confirmar: 403. Control: la propietaria, 200 arriba.
    for quien in ("inq", "oc", "sin_confirmar"):
        assert leer(e, quien).status_code == 403, quien
    # La base niega la lectura a quien no tiene vínculo de propietario confirmado ni facultad.
    with pytest.raises(DBAPIError) as error:
        en_la_base(motor_app, e, "inq", LEER)
    assert isinstance(error.value.orig, InsufficientPrivilege)
    assert len(en_la_base(motor_app, e, "prop", LEER)) == 1  # control


def test_otro_consorcio_no_ve_ni_recibe_y_su_propio_reclamo_si(e, motor_migrate, motor_app):
    otro = armar(motor_migrate, motor_app)
    reclamo = presentado(e)
    ajenos = {correo_de(motor_migrate, otro[q]) for q in ("mc", "ad")}
    assert not ajenos & {x["carga"]["para"] for x in correos(motor_migrate, e["cons"])}
    with cliente(otro["mc"], otro["sesion_mc"]) as cx:
        assert cx.get(url(e)).status_code == 404
        assert cx.get(url(e, reclamo["id"])).status_code == 404
        assert cx.post(url(e, reclamo["id"], "respuesta"), json={"texto": RESPUESTA},
                       headers=clave_idempotencia()).status_code == 404  # fmt: skip
    assert leer(otro, "mc", reclamo["id"]).status_code == 404
    assert leer(otro, "mc").json()["elementos"] == []
    # Su liquidación no se cita desde este edificio.
    r = presentar(e, datos=cuerpo(e, liquidacion_id=otro["liq"]))
    assert codigo(r) == (422, "peticion_invalida")
    # Control: el otro edificio recibe el aviso de su propio reclamo.
    presentado(otro)
    assert ajenos <= {x["carga"]["para"] for x in correos(motor_migrate, otro["cons"])}


# Facultad y migración -----------------------------------------------------------------------


def test_un_mandato_registrado_antes_de_0123_no_recibe_la_facultad(base_aparte):
    cfg, motor = base_aparte
    command.upgrade(cfg, "0122")

    def alta_soporte() -> UUID:
        with motor.begin() as c:
            cons = c.execute(text("insert into nucleo.consorcio (nombre) values ('A')"
                                  " returning id")).scalar_one()  # fmt: skip
            dec = c.execute(text(
                "insert into nucleo.decision_formal (consorcio_id, tipo, fecha, descripcion,"
                " referencia_acta, origen) values (:c, 'asamblea', '2026-01-01', 'x', 'Acta',"
                " 'soporte') returning id"), {"c": cons}).scalar_one()  # fmt: skip
            per = c.execute(text(
                "insert into nucleo.persona (correo, nombre) values (:c, 'V') returning id"),
                {"c": f"v-{uuid4().hex[:8]}@e.com"}).scalar_one()  # fmt: skip
            fijar_ambito(c, persona_id=None, consorcio_id=cons)
            return c.execute(text(
                "select nucleo.alta_mandato('administrador', :p, null, null, null, :d,"
                " '2026-01-01', null, null, null, 'soporte')"), {"p": per, "d": dec},
            ).scalar_one()  # fmt: skip

    def facultades(mandato: UUID) -> set[str]:
        sql = "select facultad from nucleo.facultad_otorgada where mandato_id = :m"
        with motor.connect() as c:
            return set(c.execute(text(sql), {"m": mandato}).scalars())

    def paquetes() -> set[str]:
        sql = "select tipo::text from nucleo.paquete_facultad where facultad = 'reclamos.atender'"
        with motor.connect() as c:
            return set(c.execute(text(sql)).scalars())

    antes = alta_soporte()
    assert "comunicados.emitir" in facultades(antes)  # control: el paquete se copió
    command.upgrade(cfg, "0123")
    assert paquetes() == {"miembro_consejo", "administrador"}
    assert "reclamos.atender" not in facultades(antes)
    command.downgrade(cfg, "0122")  # sin datos de 0123: baja
    assert paquetes() == set()
    command.upgrade(cfg, "0123")  # control: vuelve a subir limpio
    despues = alta_soporte()
    assert "reclamos.atender" in facultades(despues)  # control: el alta nueva sí la recibe
    with pytest.raises(DBAPIError) as error:
        command.downgrade(cfg, "0122")
    assert hint(error) == "downgrade_con_datos"


# Exportación --------------------------------------------------------------------------------


def test_la_exportacion_lleva_los_reclamos_y_su_historia_sin_identificadores_de_personas(
    e, motor_migrate, con_url, claves, tmp_path
):
    a = presentado(e, campo="saldo_unidad")
    actuar(e, "mc", a, "respuesta", {"texto": RESPUESTA})
    b = presentado(e, unidad_id=str(e["u"]["1° A"]))
    actuar(e, "prop", b, "retiro", {"motivo": "Lo aclaré"})
    salida = tmp_path / f"exportacion-{uuid4().hex[:8]}.zip.age"
    assert exportar_consorcio.main(argumentos(e, claves, salida)) == 0
    paquete = descifrar(salida, claves["privada"])
    assert verificar_manifiesto(paquete) == []
    datos = entidades(paquete)
    reclamos = datos["reclamo_liquidacion"]
    assert [(x["id"], x["unidad_designacion"], x["campo"], x["texto"]) for x in reclamos] == [
        (a["id"], "1-A", "saldo_unidad", TEXTO), (b["id"], "1° A", None, TEXTO)]  # fmt: skip
    assert {x["importacion_id"] for x in reclamos} == {e["liq"]}
    historial = datos["reclamo_liquidacion_estado"]
    pasos = [(x["reclamo_id"], x["estado"], x["texto"]) for x in historial]
    assert pasos == [(a["id"], "presentado", None), (a["id"], "respondido", RESPUESTA),
                     (b["id"], "presentado", None), (b["id"], "retirado", "Lo aclaré")]  # fmt: skip
    nombres = {x["presentado_por_nombre"] for x in reclamos} | {
        x["registrado_por_nombre"] for x in datos["reclamo_liquidacion_estado"]}  # fmt: skip
    assert all(nombres)
    # Ni identificadores ni correos de personas.
    crudo = json.dumps(reclamos + datos["reclamo_liquidacion_estado"])
    with motor_migrate.connect() as cx:
        personas = cx.execute(text(
            "select p.id::text, p.correo from nucleo.persona p where p.id in"
            " (select presentado_por from nucleo.reclamo_liquidacion where consorcio_id = :c"
            "  union select registrado_por from nucleo.reclamo_liquidacion_estado"
            "  where consorcio_id = :c)"), {"c": e["cons"]}).all()  # fmt: skip
    assert len(personas) == 2  # control: la propietaria y el Consejo
    for pid, direccion in personas:
        assert pid not in crudo and direccion not in crudo


def test_la_exportacion_tapa_una_cuenta_aunque_la_entrada_la_rechace(e, motor_migrate, con_url,
                                                                   claves, tmp_path):  # fmt: skip
    """Defensa en profundidad del paquete: un texto que llegó a la base sin pasar por el
    disparador (en esta base descartable, con los disparadores apagados) sale sin la cuenta."""
    reclamo = presentado(e)
    with motor_migrate.begin() as cx:
        cx.execute(text("alter table nucleo.reclamo_liquidacion disable trigger user"))
        cx.execute(text("update nucleo.reclamo_liquidacion set texto = :t where id = :r"),
                   {"t": f"Cuenta {CUENTA}", "r": reclamo["id"]})  # fmt: skip
        cx.execute(text("alter table nucleo.reclamo_liquidacion enable trigger user"))
    salida = tmp_path / f"exportacion-{uuid4().hex[:8]}.zip.age"
    assert exportar_consorcio.main(argumentos(e, claves, salida)) == 0
    [fila] = entidades(descifrar(salida, claves["privada"]))["reclamo_liquidacion"]
    assert CUENTA not in fila["texto"] and "Cuenta " in fila["texto"]
