"""Pagos informados, segundo corte (`0130`; PI-S2 del diseño «Pagos informados», reglas PI-3,
PI-4, PI-5 y PI-9): confirmar, rechazar, anular, corregir, la bandeja y el aviso diario.

Contra servicios reales: PostgreSQL, el almacén S3 de pruebas y ClamAV. La escena es la de
`test_pagos_informados_0129.py` (Modo Consejo): el miembro del Consejo («mc», que por su mandato
tiene también un vínculo de propietaria confirmado en una unidad propia) y la administración
(«ad») tienen la facultad nueva; la propietaria de «1-A», la inquilina y la ocupante de «PB-A»
informan. Cada negación lleva su control: el mismo pedido de quien sí puede pasa. Datos
SINTETICOS / NO REAL.
"""

import importlib.util
import json
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from cem_contracts import pagos_informados as contrato
from cem_contracts import politicas
from cem_contracts.ayuda import ENCABEZADO_AYUDA
from psycopg.errors import InsufficientPrivilege
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.consorcios import avisos_consejo
from app.db import transaccion
from scripts import exportar_consorcio
from tests.conftest import clave_idempotencia
from tests.test_avisos_y_sin_explicar_0098 import avisar, datos_de_dinero
from tests.test_bandeja_consejo_0126 import LECTURA as PENDIENTES
from tests.test_bandeja_consejo_0126 import nombre, renglones, textos
from tests.test_documentos_2c1 import analizar, pdf
from tests.test_exportacion_2c3 import (  # noqa: F401  (`claves` y `con_url` son fixtures)
    argumentos,
    claves,
    con_url,
    descifrar,
    entidades,
    verificar_manifiesto,
)
from tests.test_liquidacion_oficial_0119 import (  # noqa: F401  (los fixtures se importan)
    bucket,
    firmas_de_la_imagen,
    ruta,
)
from tests.test_mandatos import otra_persona
from tests.test_mandatos_0012 import alta
from tests.test_modo_operacion import cambiar, decidir_equipo
from tests.test_orden_pago_neutra import cbu_sintetico, nueva_sesion
from tests.test_pagos_informados_0129 import (
    NUMERO,
    NUMERO_OTRA_FORMA,
    archivo,
    armar,
    ayuda,
    codigo,
    comprobante,
    datos,
    informar,
    listar,
)
from tests.test_reclamos_liquidacion_0123 import correo_de
from tests.test_rutas_egresos import cliente
from tests.test_vinculo_0014 import base_aparte

pytestmark = [pytest.mark.db, pytest.mark.redis]
# ruff: noqa: F811

__all__ = ["base_aparte", "bucket", "firmas_de_la_imagen"]

MIGRACION = (
    Path(__file__).resolve().parent.parent
    / "migrations/versions/0130_pagos_informados_confirmar.py"
)
HISTORIA = "nucleo.pago_informado_estado"
MOTIVO = "El comprobante no corresponde a esta unidad. SINTETICO"
CONFIRMAR = text("select nucleo.confirmar_pago_informado(:p, :a, 'api', :s)")
RECHAZAR = text("select nucleo.rechazar_pago_informado(:p, :m, :a, 'api', :s)")
UN_PAGO = "un pago informado espera confirmación"


def _migracion():
    spec = importlib.util.spec_from_file_location("migracion_0130", MIGRACION)
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


@pytest.fixture
def e(motor_migrate, motor_app, bucket) -> dict:
    e = armar(motor_migrate, motor_app)
    with motor_migrate.connect() as c:
        e["unidad_mc"] = c.execute(text(
            "select unidad_id from nucleo.vinculo where consorcio_id = :c and persona_id = :p"
            " and confirmado_en is not null"),
            {"c": e["cons"], "p": e["mc"]}).scalar_one()  # fmt: skip
    return e


def hint(error: pytest.ExceptionInfo) -> str | None:
    return error.value.orig.diag.message_hint


def efectivo(e: dict, quien: str, unidad: str = "1-A", **cambios) -> dict:
    """Un pago en efectivo, sin comprobante ni número: se confirma sin esperar al antivirus."""
    u = e["u"][unidad] if unidad in e["u"] else unidad
    r = informar(e, quien, datos(u, None, None, medio="efectivo") | cambios)
    assert r.status_code == 201, r.text
    return r.json()


def paso(e: dict, quien: str, pago: str, que: str, motivo: str | None = None,
         encabezados: dict | None = None):  # fmt: skip
    cuerpo = None if motivo is None else {"motivo": motivo}
    with cliente(e[quien], e[f"sesion_{quien}"]) as c:
        return c.post(ruta(e, "pagos-informados", pago, que), json=cuerpo,
                      headers=clave_idempotencia() | (encabezados or {}))  # fmt: skip


def visto(e: dict, quien: str, pago: str) -> dict:
    [p] = [p for p in listar(e, quien).json()["elementos"] if p["id"] == pago]
    return p


def historia(motor_migrate, pago: str) -> list[tuple]:
    with motor_migrate.connect() as c:
        return [tuple(f) for f in c.execute(text(
            f"select de, a, motivo from {HISTORIA} where pago_id = :p order by registrado_en"),
            {"p": pago}).all()]  # fmt: skip


# --- Una sola fuente ------------------------------------------------------------------------------


def test_la_migracion_copia_el_contrato_y_la_politica():
    m = _migracion()
    assert m.ESTADOS == tuple(contrato.EstadoPagoInformado)
    assert m.TRANSICIONES == contrato.TRANSICIONES
    assert m.ESTADOS_CON_MOTIVO == contrato.ESTADOS_CON_MOTIVO
    assert m.ESTADOS_QUE_SE_CORRIGEN == contrato.ESTADOS_QUE_NO_CUENTAN
    assert m.LARGO_MOTIVO == contrato.LARGO_MOTIVO
    leidas = {p["accion"]: p for archivo_ in politicas.archivos()
              for p in json.loads(archivo_.read_text(encoding="utf-8"))["politicas"]}  # fmt: skip
    politica = leidas[m.ACCION_POLITICA]
    assert (politica["facultad"], tuple(politica["modos"])) == (m.FACULTAD, m.MODOS)
    assert politica["separacion"] == ["no_es_quien_informo"]
    assert politica["economica"] is False and politica.get("ayuda") is None
    assert set(m.ACCIONES) == {b for _, b in m.TRANSICIONES}
    assert set(m.VERSIONES_QUE_NO_SE_CONFIRMAN) == {"en_cuarentena", "rechazado"}


# --- PI-4 y PI-9: los pasos y la historia --------------------------------------------------------


def test_confirmar_y_anular_dejan_cada_paso_en_la_historia(e, motor_migrate):
    pago = efectivo(e, "prop")
    assert pago["acciones"] == [] and pago["historia"] == []  # quien informa no decide
    assert visto(e, "mc", pago["id"])["acciones"] == ["confirmar", "rechazar"]
    r = paso(e, "mc", pago["id"], "confirmacion")
    assert r.status_code == 200, r.text
    assert (r.json()["estado"], r.json()["acciones"]) == ("confirmado", ["anular"])
    [h] = r.json()["historia"]
    assert (h["de"], h["a"], h["motivo"]) == ("informado", "confirmado", None)
    # La anulación (PI-9) la hace otra persona con la facultad; el confirmado queda en la historia.
    r = paso(e, "ad", pago["id"], "anulacion", MOTIVO)
    assert r.status_code == 200, r.text
    assert (r.json()["estado"], r.json()["acciones"]) == ("anulado", [])
    assert historia(motor_migrate, pago["id"]) == [
        ("informado", "confirmado", None), ("confirmado", "anulado", MOTIVO)]  # fmt: skip
    # Quien informó ve el motivo y puede corregirlo.
    propio = visto(e, "prop", pago["id"])
    assert propio["historia"][-1]["motivo"] == MOTIVO
    assert propio["acciones"] == ["corregir"]
    # La auditoría registra cada paso.
    with motor_migrate.connect() as c:
        acciones = c.execute(text(
            "select accion, actor_persona_id, despues->>'a' from auditoria.eventos"
            " where consorcio_id = :c and entidad = 'pago_informado' and entidad_id = :p"
            " order by secuencia"), {"c": e["cons"], "p": pago["id"]}).all()  # fmt: skip
    assert [tuple(a) for a in acciones] == [
        ("consorcio.pago_informado.informar", e["prop"], None),
        ("consorcio.pago_informado.confirmar", e["mc"], "confirmado"),
        ("consorcio.pago_informado.anular", e["ad"], "anulado"),
    ]


def test_rechazar_con_motivo(e, motor_migrate):
    pago = efectivo(e, "inq", "PB-A")
    r = paso(e, "ad", pago["id"], "rechazo", MOTIVO)
    assert r.status_code == 200, r.text
    assert r.json()["estado"] == "rechazado"
    assert historia(motor_migrate, pago["id"]) == [("informado", "rechazado", MOTIVO)]
    # La ocupante de la misma unidad, que no lo informó, también lo puede corregir.
    assert visto(e, "oc", pago["id"])["acciones"] == ["corregir"]


def test_los_pasos_que_no_admite_el_estado_dan_409(e):
    confirmado = efectivo(e, "prop")
    assert paso(e, "mc", confirmado["id"], "confirmacion").status_code == 200
    rechazado = efectivo(e, "inq", "PB-A")
    assert paso(e, "mc", rechazado["id"], "rechazo", MOTIVO).status_code == 200
    informado = efectivo(e, "oc", "PB-A", fecha_pago="2026-11-01")
    for pago, que, motivo in (
        (confirmado, "confirmacion", None),
        (confirmado, "rechazo", MOTIVO),
        (rechazado, "confirmacion", None),
        (rechazado, "anulacion", MOTIVO),
        (informado, "anulacion", MOTIVO),
    ):
        assert codigo(paso(e, "ad", pago["id"], que, motivo)) == (409, "pago_informado_decidido")
    # Control: el paso que sí admite.
    assert paso(e, "ad", confirmado["id"], "anulacion", MOTIVO).status_code == 200
    assert codigo(paso(e, "ad", confirmado["id"], "anulacion", MOTIVO)) == (
        409, "pago_informado_decidido")  # fmt: skip
    # Un pago que no existe, o de otro edificio: 404.
    assert codigo(paso(e, "mc", str(uuid4()), "confirmacion")) == (404, "no_encontrado")


# --- PI-5: quién decide y la separación ----------------------------------------------------------


def test_nadie_decide_sobre_un_pago_que_informo(e, motor_app):
    """El miembro del Consejo, propietario de su unidad, informa un pago: no lo confirma, ni lo
    rechaza; la administración sí."""
    propio = efectivo(e, "mc", e["unidad_mc"])
    assert (propio["propio"], propio["acciones"]) == (True, [])
    for que, motivo in (("confirmacion", None), ("rechazo", MOTIVO)):
        r = paso(e, "mc", propio["id"], que, motivo)
        assert codigo(r) == (403, "ya_participo"), que
        assert r.json()["detalle"] == {"separacion": "no_es_quien_informo"}
    # La base repite la barrera (la llamada directa, sin el evaluador).
    with (
        pytest.raises(DBAPIError) as error,
        transaccion(motor_app, persona_id=e["mc"], consorcio_id=e["cons"]) as c,
    ):
        c.execute(CONFIRMAR, {"p": propio["id"], "a": e["mc"], "s": e["sesion_mc"]})
    assert hint(error) == "no_es_quien_informo"
    # Control: la administración lo confirma; y después el Consejo tampoco lo anula.
    assert paso(e, "ad", propio["id"], "confirmacion").status_code == 200
    assert codigo(paso(e, "mc", propio["id"], "anulacion", MOTIVO)) == (403, "ya_participo")
    assert visto(e, "mc", propio["id"])["acciones"] == []
    assert visto(e, "ad", propio["id"])["acciones"] == ["anular"]


def test_ni_quien_lo_informo_ayudando(e, motor_app, motor_migrate):
    """Quien ayuda a la propietaria (RF53) y además es del Consejo informa por ella: el pago queda a
    nombre de la propietaria, y quien ayudó no lo decide con su propia cuenta."""
    alta(motor_app, e["cons"], None, e["acta"], persona=e["ayu"], tipo="miembro_consejo",
         origen="soporte")  # fmt: skip
    autorizacion = ayuda(e, motor_migrate, ["consultar_expensas", "preparar_reclamos"])
    r = informar(e, "ayu", datos(e["u"]["1-A"], None, None, medio="efectivo"),
                 encabezados={ENCABEZADO_AYUDA: autorizacion})  # fmt: skip
    assert r.status_code == 201, r.text
    pago = r.json()["id"]
    # Ayudando, nada que decidir (la política no admite ayuda).
    r = paso(e, "ayu", pago, "confirmacion", encabezados={ENCABEZADO_AYUDA: autorizacion})
    assert r.status_code == 403
    # Con su cuenta y su mandato: tampoco, porque lo informó él.
    assert visto(e, "ayu", pago)["acciones"] == []
    assert codigo(paso(e, "ayu", pago, "confirmacion")) == (403, "ya_participo")
    with (
        pytest.raises(DBAPIError) as error,
        transaccion(motor_app, persona_id=e["ayu"], consorcio_id=e["cons"]) as c,
    ):
        c.execute(CONFIRMAR, {"p": pago, "a": e["ayu"], "s": e["sesion_ayu"]})
    assert hint(error) == "no_es_quien_informo"
    # Control: decide sobre otro pago de la misma unidad, que no informó.
    otro = efectivo(e, "prop", fecha_pago="2026-11-01")
    assert visto(e, "ayu", otro["id"])["acciones"] == ["confirmar", "rechazar"]
    assert paso(e, "ayu", otro["id"], "confirmacion").status_code == 200
    assert paso(e, "mc", pago, "confirmacion").status_code == 200


def test_solo_decide_quien_tiene_la_facultad(e, motor_app, motor_migrate):
    pago = efectivo(e, "prop")
    for quien in ("prop", "inq", "oc", "sin_confirmar"):
        assert paso(e, quien, pago["id"], "rechazo", MOTIVO).status_code in (403, 404), quien
    # Un mandato del Consejo al que el acta dejó solo el panel: ve el pago, no lo decide.
    e["acotado"] = otra_persona(motor_migrate, e["cons"])
    alta(motor_app, e["cons"], None, e["acta"], persona=e["acotado"], tipo="miembro_consejo",
         origen="soporte", facultades=["fiscalizacion.ver_panel"])  # fmt: skip
    e["sesion_acotado"] = nueva_sesion(motor_migrate, e["acotado"])
    lista = listar(e, "acotado").json()
    assert lista["por_confirmar"] == 0
    assert visto(e, "acotado", pago["id"])["acciones"] == []
    assert codigo(paso(e, "acotado", pago["id"], "confirmacion")) == (403, "sin_permiso")
    with (
        pytest.raises(DBAPIError) as error,
        transaccion(motor_app, persona_id=e["acotado"], consorcio_id=e["cons"]) as c,
    ):
        c.execute(CONFIRMAR, {"p": pago["id"], "a": e["acotado"], "s": e["sesion_acotado"]})
    assert isinstance(error.value.orig, InsufficientPrivilege)
    # Al revés: un mandato al que el acta dejó solo confirmar, sin el panel (PI-D14, observación de
    # Codex en #290). No ve los pagos, así que la base no lo deja decidir y la bandeja no le cuenta.
    e["solo_decide"] = otra_persona(motor_migrate, e["cons"])
    alta(motor_app, e["cons"], None, e["acta"], persona=e["solo_decide"], tipo="miembro_consejo",
         origen="soporte", facultades=["pagos.confirmar_informado"])  # fmt: skip
    e["sesion_solo_decide"] = nueva_sesion(motor_migrate, e["solo_decide"])
    with (
        pytest.raises(DBAPIError) as error,
        transaccion(motor_app, persona_id=e["solo_decide"], consorcio_id=e["cons"]) as c,
    ):
        c.execute(CONFIRMAR, {"p": pago["id"], "a": e["solo_decide"],
                              "s": e["sesion_solo_decide"]})  # fmt: skip
    assert isinstance(error.value.orig, InsufficientPrivilege)
    with transaccion(motor_app, persona_id=None) as c:
        avisados = {f["persona_id"] for f in c.execute(text(PENDIENTES)).mappings()
                    if f["consorcio_id"] == e["cons"] and f["pagos_informados"]}  # fmt: skip
    assert e["solo_decide"] not in avisados
    assert e["mc"] in avisados  # control: el Consejo completo sí ve el pendiente
    # Con solo confirmar y un vínculo (la ocupante de «PB-A»): ve el pago de su unidad, pero la API
    # no le ofrece decidirlo ni se lo cuenta, y si lo intenta recibe 403, no un 500.
    alta(motor_app, e["cons"], None, e["acta"], persona=e["oc"], tipo="miembro_consejo",
         origen="soporte", facultades=["pagos.confirmar_informado"])  # fmt: skip
    de_la_unidad = efectivo(e, "inq", "PB-A")
    assert visto(e, "oc", de_la_unidad["id"])["acciones"] == []
    assert listar(e, "oc").json()["por_confirmar"] == 0
    assert codigo(paso(e, "oc", de_la_unidad["id"], "confirmacion")) == (403, "sin_permiso")
    assert visto(e, "mc", de_la_unidad["id"])["acciones"] == ["confirmar", "rechazar"]  # control
    # La sesión de quien ayuda, con la ayuda en la transacción: la base la niega (sin alcance).
    autorizacion = ayuda(e, motor_migrate, ["consultar_expensas", "preparar_reclamos"])
    with motor_app.connect() as cx, cx.begin() as t:
        cx.execute(text(
            "select set_config('app.persona_id', :p, true),"
            " set_config('app.consorcio_id', :c, true), set_config('app.ayuda_id', :a, true)"),
            {"p": str(e["prop"]), "c": str(e["cons"]), "a": autorizacion})  # fmt: skip
        with pytest.raises(DBAPIError) as error:
            cx.execute(RECHAZAR, {"p": pago["id"], "m": MOTIVO, "a": e["prop"],
                                  "s": e["sesion_ayu"]})  # fmt: skip
        t.rollback()
    assert isinstance(error.value.orig, InsufficientPrivilege)
    # Control: el Consejo lo rechaza por la base.
    with transaccion(motor_app, persona_id=e["mc"], consorcio_id=e["cons"]) as c:
        c.execute(RECHAZAR, {"p": pago["id"], "m": MOTIVO, "a": e["mc"], "s": e["sesion_mc"]})
    assert visto(e, "mc", pago["id"])["estado"] == "rechazado"


@pytest.mark.parametrize("modo", ["administrador_aumentado", "autogestion"])
def test_se_confirma_en_los_tres_modos(motor_migrate, motor_app, bucket, modo):
    e = armar(motor_migrate, motor_app)
    cambiar(motor_app, e["cons"], modo, decidir_equipo(motor_app, e["cons"]), e["hoy"])
    pago = efectivo(e, "prop")
    assert paso(e, "ad", pago["id"], "confirmacion").status_code == 200
    assert paso(e, "mc", pago["id"], "anulacion", MOTIVO).status_code == 200


# --- PI-4: el comprobante -----------------------------------------------------------------------


def test_con_el_comprobante_en_cuarentena_o_rechazado_no_se_confirma(e, motor_app):
    unidad = e["u"]["1-A"]
    r = informar(e, "prop", datos(unidad, comprobante(e, "prop", unidad, archivo("c1"))))
    pago = r.json()
    assert pago["comprobante_estado"] == "en_cuarentena"
    assert visto(e, "mc", pago["id"])["acciones"] == ["rechazar"]  # C9: sin «confirmar»
    assert codigo(paso(e, "mc", pago["id"], "confirmacion")) == (409, "comprobante_en_revision")
    # Uno con contenido activo: el antivirus lo rechaza.
    malo = pdf(js="app.alert(1)", flujo=b"BT (Comprobante SINTETICO malo) Tj ET")
    r = informar(e, "prop", datos(unidad, comprobante(e, "prop", unidad, malo), "MALO-1"))
    rechazado = r.json()
    # El trabajador toma un análisis por lote: se corre hasta que no queda ninguno.
    lotes = 0
    while analizar(motor_app):
        lotes += 1
    assert lotes >= 2
    assert visto(e, "mc", rechazado["id"])["comprobante_estado"] == "rechazado"
    assert visto(e, "mc", rechazado["id"])["acciones"] == ["rechazar"]
    assert codigo(paso(e, "mc", rechazado["id"], "confirmacion")) == (409, "comprobante_rechazado")
    assert paso(e, "mc", rechazado["id"], "rechazo", MOTIVO).status_code == 200
    # Control: aceptado por el antivirus, se confirma.
    assert visto(e, "mc", pago["id"])["acciones"] == ["confirmar", "rechazar"]
    assert paso(e, "mc", pago["id"], "confirmacion").status_code == 200


# --- El motivo ---------------------------------------------------------------------------------


def test_el_motivo_es_obligatorio_corto_y_sin_cuenta(e, motor_app, motor_migrate):
    pago = efectivo(e, "prop")
    with cliente(e["mc"], e["sesion_mc"]) as c:
        r = c.post(ruta(e, "pagos-informados", pago["id"], "rechazo"), json={},
                   headers=clave_idempotencia())  # fmt: skip
    assert r.status_code == 422
    for motivo in ("   ", "x" * (contrato.LARGO_MOTIVO + 1), "dos\nrenglones"):
        assert paso(e, "mc", pago["id"], "rechazo", motivo).status_code == 422, motivo
    r = paso(e, "mc", pago["id"], "rechazo", f"Cuenta {cbu_sintetico(3)}")
    assert codigo(r) == (422, "motivo_con_cuenta")
    assert r.json()["detalle"] == {"campos": ["body.motivo"]}
    # La base repite: sin motivo, y con uno más largo que el límite.
    for motivo, esperado in (("", "motivo_requerido"), (None, "motivo_requerido")):
        with (
            pytest.raises(DBAPIError) as error,
            transaccion(motor_app, persona_id=e["mc"], consorcio_id=e["cons"]) as c,
        ):
            c.execute(RECHAZAR, {"p": pago["id"], "m": motivo, "a": e["mc"], "s": e["sesion_mc"]})
        assert hint(error) == esperado
    with (
        pytest.raises(IntegrityError) as error,
        transaccion(motor_app, persona_id=e["mc"], consorcio_id=e["cons"]) as c,
    ):
        c.execute(RECHAZAR, {"p": pago["id"], "m": "x" * (contrato.LARGO_MOTIVO + 1),
                             "a": e["mc"], "s": e["sesion_mc"]})  # fmt: skip
    assert error.value.orig.diag.constraint_name == "pago_informado_estado_motivo_check"
    assert historia(motor_migrate, pago["id"]) == []
    # Control: el más largo que admite.
    assert paso(e, "mc", pago["id"], "rechazo", "x" * contrato.LARGO_MOTIVO).status_code == 200


# --- PI-3: la corrección -------------------------------------------------------------------------


def test_pi3_la_correccion_con_el_mismo_numero_y_el_mismo_archivo(e, motor_app, motor_migrate):
    unidad = e["u"]["1-A"]
    version = comprobante(e, "prop", unidad, archivo("corregir"))
    primero = informar(e, "prop", datos(unidad, version)).json()
    # Mientras cuenta, el mismo pago vuelve como «ya informado» (PA07).
    r = informar(e, "prop", datos(unidad, version, NUMERO_OTRA_FORMA))
    assert (r.status_code, r.json()["id"]) == (200, primero["id"])
    # Corregir uno que todavía cuenta: no.
    r = informar(e, "prop", datos(unidad, None, "OTRO-1", medio="efectivo",
                                  corrige_a=primero["id"]))  # fmt: skip
    assert codigo(r) == (422, "correccion_de_pago_invalida")
    assert paso(e, "mc", primero["id"], "rechazo", MOTIVO).status_code == 200
    # La corrección: el mismo número y la misma versión del comprobante, con otro importe.
    r = informar(e, "prop", datos(unidad, version, importe="45000.00", corrige_a=primero["id"]))
    assert r.status_code == 201, r.text
    correccion = r.json()
    assert (correccion["corrige_a"], correccion["ya_informado"]) == (primero["id"], False)
    assert visto(e, "prop", primero["id"])["corregido_por"] == correccion["id"]
    assert visto(e, "prop", primero["id"])["acciones"] == []  # ya corregido: nada que ofrecer
    # El reintento de la corrección (otra clave): la corrección ya cuenta y vuelve como existente.
    r = informar(e, "prop", datos(unidad, version, importe="45000.00", corrige_a=primero["id"]))
    assert (r.status_code, r.json()["id"]) == (200, correccion["id"])
    # Una segunda corrección del mismo rechazado, con otros datos: no.
    r = informar(e, "prop", datos(unidad, None, "OTRO-2", medio="efectivo",
                                  corrige_a=primero["id"]))  # fmt: skip
    assert codigo(r) == (409, "pago_ya_corregido")
    # Corregir el de otra unidad, o uno que no existe: no.
    ajeno = efectivo(e, "inq", "PB-A")
    assert paso(e, "mc", ajeno["id"], "rechazo", MOTIVO).status_code == 200
    for destino in (ajeno["id"], str(uuid4())):
        r = informar(e, "prop", datos(unidad, None, "OTRO-3", medio="efectivo",
                                      corrige_a=destino))  # fmt: skip
        assert codigo(r) == (422, "correccion_de_pago_invalida"), destino
    # La corrección se confirma (con el comprobante ya aceptado), se anula y se corrige a su vez,
    # otra vez con el mismo número.
    while analizar(motor_app):
        pass
    assert paso(e, "ad", correccion["id"], "confirmacion").status_code == 200
    assert paso(e, "ad", correccion["id"], "anulacion", MOTIVO).status_code == 200
    r = informar(e, "prop", datos(unidad, version, corrige_a=correccion["id"]))
    assert r.status_code == 201, r.text
    # Tres registros con el mismo número: uno solo cuenta (PA07 para los vigentes).
    with motor_migrate.connect() as c:
        estados = c.execute(text(
            "select estado from nucleo.pago_informado where consorcio_id = :c and unidad_id = :u"
            " and numero_operacion_normalizado = :n order by informado_en"),
            {"c": e["cons"], "u": unidad, "n": NUMERO_OTRA_FORMA}).scalars().all()  # fmt: skip
    assert estados == ["rechazado", "anulado", "informado"]
    # Y la base impide otro que cuente con ese número, aunque se escriba por fuera de la función.
    with pytest.raises(IntegrityError) as error, motor_migrate.begin() as c:
        c.execute(text(
            "insert into nucleo.pago_informado (consorcio_id, unidad_id, importe, fecha_pago,"
            " medio, numero_operacion, origen, informado_por) values (:c, :u, 1, '2026-11-01',"
            " 'efectivo', :n, 'app', :p)"), {"c": e["cons"], "u": unidad, "n": NUMERO,
                                              "p": e["prop"]})  # fmt: skip
    assert error.value.orig.diag.constraint_name == "pago_informado_un_numero"


# --- Inmutabilidad, RLS, permisos y exportación --------------------------------------------------


def test_el_estado_solo_cambia_por_la_funcion_y_la_historia_no_se_toca(e, motor_migrate):
    pago = efectivo(e, "prop")["id"]
    bandera = text(f"select set_config('{_migracion().BANDERA}', :p, true)")
    paso_ = text(f"insert into {HISTORIA} (consorcio_id, pago_id, de, a, registrado_por)"
                 " values (:c, :p, 'informado', 'confirmado', :m)")  # fmt: skip
    cambiar = text("update nucleo.pago_informado set estado = 'confirmado' where id = :p")
    p = {"c": e["cons"], "p": pago, "m": e["mc"]}
    casos = {
        "sin bandera": ([paso_], cambiar),
        "sin historia": ([bandera], cambiar),
        "otra columna": (
            [bandera, paso_],
            text(
                "update nucleo.pago_informado set estado = 'confirmado', importe = 2 where id = :p"
            ),
        ),
        "paso no admitido": (
            [bandera],
            text("update nucleo.pago_informado set estado = 'anulado' where id = :p"),
        ),
        "borrar": ([bandera, paso_], text("delete from nucleo.pago_informado where id = :p")),
    }
    for caso, (antes, sentencia) in casos.items():
        with pytest.raises(DBAPIError) as error, motor_migrate.begin() as c:
            for s in antes:
                c.execute(s, p)
            c.execute(sentencia, p)
        assert hint(error) == "historia_inmutable", caso
    # Control: con la bandera y su paso escrito, el cambio pasa (en una transacción que se deshace).
    with motor_migrate.connect() as c, c.begin() as t:
        c.execute(bandera, p)
        c.execute(paso_, p)
        assert c.execute(cambiar, p).rowcount == 1
        t.rollback()
    # Un paso que no parte del estado actual, o no admitido: la historia no lo acepta.
    with pytest.raises(DBAPIError) as error, motor_migrate.begin() as c:
        c.execute(
            text(
                f"insert into {HISTORIA} (consorcio_id, pago_id, de, a, motivo,"
                " registrado_por) values (:c, :p, 'confirmado', 'anulado', 'M', :m)"
            ),
            p,
        )
    assert hint(error) == "pago_informado_decidido"
    with pytest.raises(IntegrityError) as error, motor_migrate.begin() as c:
        c.execute(text(f"insert into {HISTORIA} (consorcio_id, pago_id, de, a, motivo,"
                       " registrado_por) values (:c, :p, 'informado', 'anulado', 'M', :m)"), p)
    assert error.value.orig.diag.constraint_name == "pago_informado_estado_paso"
    # La historia escrita no se cambia ni se borra.
    assert paso(e, "mc", pago, "rechazo", MOTIVO).status_code == 200
    for sentencia in (f"update {HISTORIA} set motivo = 'Otro'", f"delete from {HISTORIA}",
                      f"truncate {HISTORIA}"):  # fmt: skip
        with pytest.raises(DBAPIError) as error, motor_migrate.begin() as c:
            c.execute(text(sentencia))
        assert hint(error) == "historia_inmutable", sentencia
    assert historia(motor_migrate, pago) == [("informado", "rechazado", MOTIVO)]


def test_rls_forzada_en_la_historia_y_otro_consorcio_no_la_ve(e, motor_app, motor_migrate):
    otro = armar(motor_migrate, motor_app)
    pago = efectivo(e, "prop")
    assert paso(e, "mc", pago["id"], "rechazo", MOTIVO).status_code == 200
    assert listar(otro, "mc").json()["elementos"] == []
    # Desde el otro edificio, ni el paso: su id no existe allí.
    assert codigo(paso(otro, "mc", pago["id"], "anulacion", MOTIVO)) == (404, "no_encontrado")
    with motor_migrate.connect() as cx:
        fila = cx.execute(text(
            "select c.relrowsecurity, c.relforcerowsecurity,"
            " (select p.qual from pg_policies p where p.schemaname = 'nucleo'"
            "   and p.tablename = c.relname and p.policyname = 'por_consorcio'),"
            " has_table_privilege('cem_app', c.oid, 'select, insert, update, delete')"
            " from pg_class c where c.oid = cast(:t as regclass)"),
            {"t": HISTORIA}).one()  # fmt: skip
    assert tuple(fila) == (True, True, "(consorcio_id = plataforma.consorcio_actual())", False)
    with (
        pytest.raises(DBAPIError) as error,
        transaccion(motor_app, persona_id=e["mc"], consorcio_id=e["cons"]) as c,
    ):
        c.execute(text(f"select * from {HISTORIA}")).all()
    assert isinstance(error.value.orig, InsufficientPrivilege)
    # Control: en su edificio, el Consejo ve el paso.
    assert [h["a"] for h in visto(e, "mc", pago["id"])["historia"]] == ["rechazado"]


def test_las_rutas_son_solo_para_cem_app_y_las_internas_para_nadie(motor_migrate):
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
        for f, _ in m.REEMPLAZADAS:
            if f not in m.RUTAS:  # la firma vieja ya no existe
                existe = c.execute(text("select to_regprocedure(:f)"), {"f": f}).scalar_one()
                assert existe is None, f
        assert c.execute(puede, {"r": "cem_app", "f": m.PENDIENTES}).scalar_one() is True


def test_la_exportacion_lleva_la_historia_sin_identificadores(
    e, motor_migrate, con_url, claves, tmp_path
):
    pago = efectivo(e, "prop")
    assert paso(e, "mc", pago["id"], "confirmacion").status_code == 200
    assert paso(e, "ad", pago["id"], "anulacion", MOTIVO).status_code == 200
    salida = tmp_path / f"exportacion-{uuid4().hex[:8]}.zip.age"
    assert exportar_consorcio.main(argumentos(e, claves, salida)) == 0
    paquete = descifrar(salida, claves["privada"])
    assert verificar_manifiesto(paquete) == []
    datos_ = entidades(paquete)
    assert [(x["pago_id"], x["de"], x["a"], x["motivo"]) for x in datos_["pago_informado_estado"]
            ] == [(pago["id"], "informado", "confirmado", None),
                  (pago["id"], "confirmado", "anulado", MOTIVO)]  # fmt: skip
    assert [x["estado"] for x in datos_["pago_informado"]] == ["anulado"]
    crudo = json.dumps(datos_["pago_informado_estado"])
    for persona in ("mc", "ad"):
        assert str(e[persona]) not in crudo
        assert correo_de(motor_migrate, e[persona]) not in crudo


# --- Bandeja y aviso diario ----------------------------------------------------------------------


def test_la_bandeja_y_el_aviso_diario_cuentan_los_pagos_por_confirmar(e, motor_app, motor_migrate):
    efectivo(e, "prop")
    efectivo(e, "inq", "PB-A")
    del_mc = efectivo(e, "mc", e["unidad_mc"])  # el Consejo no lo confirma: no le cuenta
    # La API (el número del inicio) y la lectura del aviso diario dicen lo mismo.
    assert listar(e, "mc").json()["por_confirmar"] == 2
    assert listar(e, "ad").json()["por_confirmar"] == 3
    assert listar(e, "prop").json()["por_confirmar"] == 0
    with transaccion(motor_app, persona_id=None) as c:
        filas = {f["persona_id"]: f for f in c.execute(text(PENDIENTES)).mappings()
                 if f["consorcio_id"] == e["cons"]}  # fmt: skip
    assert (filas[e["mc"]]["pagos_informados"], filas[e["ad"]]["pagos_informados"]) == (2, 3)
    assert avisos_consejo.cuentas_de(dict(filas[e["mc"]]))["pagos_informados"] == 2
    avisar(motor_app)
    edificio = nombre(motor_migrate, e["cons"])
    [texto] = textos(motor_migrate, e["mc"])
    assert renglones(texto) == [f"- {edificio}: 2 pagos informados esperan confirmación."]
    # Solo cuántos: ni la unidad, ni el importe, ni quién informó.
    prohibido = [*e["u"], "45678", correo_de(motor_migrate, e["prop"])]
    assert datos_de_dinero(texto, prohibido) == []
    assert datos_de_dinero(f"{texto} 1-A", prohibido) != []  # control del detector
    [del_ad] = textos(motor_migrate, e["ad"])
    assert renglones(del_ad) == [f"- {edificio}: 3 pagos informados esperan confirmación."]
    for quien in ("prop", "inq", "oc"):
        assert textos(motor_migrate, e[quien]) == [], quien
    # Lo decidido sale de la cuenta.
    for p in listar(e, "ad").json()["elementos"]:
        if p["id"] != del_mc["id"]:
            assert paso(e, "ad", p["id"], "confirmacion").status_code == 200
    assert listar(e, "mc").json()["por_confirmar"] == 0
    assert listar(e, "ad").json()["por_confirmar"] == 1
    # El correo de mañana (el tope por persona cuenta en la hora real, así que se mira lo que
    # leería el trabajo): para el Consejo nada; para la administración, un renglón en singular.
    with transaccion(motor_app, persona_id=None) as c:
        leidas = {f["persona_id"]: f["cuentas"] for f in avisos_consejo.leer(c)
                  if f["consorcio_id"] == e["cons"]}  # fmt: skip
    assert e["mc"] not in leidas
    assert leidas[e["ad"]] == {"pagos_informados": 1}
    assert avisos_consejo.partes(leidas[e["ad"]]) == [UN_PAGO]


# --- La migración ---------------------------------------------------------------------------------


def test_0130_ida_y_vuelta_y_no_baja_con_datos(base_aparte):
    cfg, motor = base_aparte
    command.upgrade(cfg, "0129")

    def estado() -> tuple:
        with motor.connect() as c:
            return c.execute(text(
                "select to_regclass('nucleo.pago_informado_estado') is not null,"
                " (select array_agg(tipo::text order by tipo::text) from nucleo.paquete_facultad"
                "   where facultad = 'pagos.confirmar_informado'),"
                " to_regprocedure('nucleo.informar_pago(uuid, numeric, date, text, text, uuid,"
                "   uuid, text, uuid)') is not null,"
                " (select count(*) from pg_proc where proname = 'pendientes_consejo_para_avisar'"
                "   and pg_get_function_result(oid) like '%pagos_informados%')")).one()  # fmt: skip

    antes = (False, None, True, 0)
    assert tuple(estado()) == antes
    command.upgrade(cfg, "0130")
    assert tuple(estado()) == (True, ["administrador", "miembro_consejo"], False, 1)
    command.downgrade(cfg, "0129")  # sin datos: baja y deja las de 0129 y 0126 como estaban
    assert tuple(estado()) == antes
    command.upgrade(cfg, "0130")  # control: vuelve a subir limpio
    with motor.begin() as c:
        cons = c.execute(text("insert into nucleo.consorcio (nombre) values ('A') returning id"))\
            .scalar_one()  # fmt: skip
        uni = c.execute(text("insert into nucleo.unidad (consorcio_id, designacion)"
                             " values (:c, '1') returning id"),
                        {"c": cons}).scalar_one()  # fmt: skip
        per = c.execute(text(
            "insert into nucleo.persona (correo, nombre) values (:c, 'P') returning id"),
            {"c": f"p-{uuid4().hex[:8]}@e.com"}).scalar_one()  # fmt: skip
        pago = c.execute(text(
            "insert into nucleo.pago_informado (consorcio_id, unidad_id, importe, fecha_pago,"
            " medio, origen, informado_por) values (:c, :u, 1, '2026-11-01', 'efectivo', 'app',"
            " :p) returning id"), {"c": cons, "u": uni, "p": per}).scalar_one()  # fmt: skip
        c.execute(text(f"insert into {HISTORIA} (consorcio_id, pago_id, de, a, motivo,"
                       " registrado_por) values (:c, :g, 'informado', 'rechazado', 'M', :p)"),
                  {"c": cons, "g": pago, "p": per})  # fmt: skip
    with pytest.raises(DBAPIError) as error:
        command.downgrade(cfg, "0129")
    assert hint(error) == "downgrade_con_datos"
    assert estado()[0] is True
