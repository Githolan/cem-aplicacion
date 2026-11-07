"""Bandeja, ficha y consulta de pagos contra PostgreSQL real (`0072`). Datos SINTETICOS / PRUEBA /
NO REAL: ninguna cuenta es de un banco (prefijo 999) y no se mueve dinero.

Diseño: `Rediseno_Circuito_Pagos_2026-11-06.md`, §2, §3, §11, §12 y §14. La escena es la de
`test_pago_manual_0055`: la administración prepara y ejecuta, mc1 aprueba y mc2 concilia. Por cada
`paso`, un caso que aparece y un control que no. Cada sí de la base se compara con lo que hace el
acto: si la bandeja ofrece algo, la ruta lo acepta.
"""

from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.consorcios import egresos
from app.db import transaccion
from tests.test_egresos_1a import aprobada, envejecer, enviada, nueva_cuenta
from tests.test_mandatos import otra_persona
from tests.test_mandatos_0012 import alta
from tests.test_orden_pago_neutra import (
    APROBAR,
    PREPARAR,
    RECHAZAR,
    Escena,
    escena_economica,
    exigir,
    huella,
    llamar,
    nueva_sesion,
    otorgar,
    otra_cuenta_en_el_consejo,
)
from tests.test_pago_manual_0055 import (
    CONCILIAR,
    CONFIRMAR,
    CUENTA,
    DECLARAR,
    REGISTRAR,
    acta,
    comprobante,
    con_ejecutar,
    emitida,
    evidencia,
    registrada,
    segunda_apertura,
    ver,
)
from tests.test_rutas_egresos import codigo, get, post
from tests.test_vinculo_0014 import base_aparte  # noqa: F401  (es un fixture)

pytestmark = pytest.mark.db
# `base_aparte` es un fixture importado: el parámetro con su nombre no lo redefine.
# ruff: noqa: F811

ACCIONES = (
    "select * from nucleo.acciones_de_ordenes_lectura(:orden, :actor, :origen, :sesion,"
    " interval '24 hours')"
)


@pytest.fixture
def e(motor_migrate, motor_app) -> Escena:
    return escena_economica(motor_migrate, motor_app)


def fila(e, quien, o) -> dict | None:
    """La orden en la bandeja de `quien`; sin ella, nada."""
    r = get(e, quien, "pagos/bandeja")
    assert r.status_code == 200, r.json()
    assert CUENTA not in r.text
    return next((x for x in r.json()["elementos"] if x["orden_id"] == str(o)), None)


def paso(e, quien, o) -> str | None:
    x = fila(e, quien, o)
    return None if x is None else x["paso"]


def ficha(e, quien, o) -> dict:
    r = get(e, quien, f"pagos/{o}")
    assert r.status_code == 200, r.json()
    assert CUENTA not in r.text and "cuenta_destino" not in r.text
    return r.json()


def consulta(e, quien, parametros: str = "") -> dict:
    r = get(e, quien, f"pagos/consulta{parametros}")
    assert r.status_code == 200, r.json()
    assert CUENTA not in r.text
    return r.json()


def ids(respuesta: dict) -> list[str]:
    return [x["orden_id"] for x in respuesta["elementos"]]


def con(motor_migrate, e, persona, *facultades) -> None:
    """Facultades fuera del paquete del mandato de `persona`."""
    with motor_migrate.connect() as c:
        m = c.execute(
            text("select id from nucleo.mandato where consorcio_id = :c and persona_id = :p"),
            {"c": e.cons, "p": persona},
        ).scalar_one()  # fmt: skip
    for f in facultades:
        otorgar(motor_migrate, e.cons, m, acta(motor_migrate, e), f)


def segundo_ejecutor(motor_migrate, motor_app, e) -> UUID:
    """Otra persona que puede todo lo de pagar: no aprobó ni preparó nada."""
    x = otra_persona(motor_migrate, e.cons)
    a = acta(motor_migrate, e)
    m = alta(motor_app, e.cons, e.prop, a, tipo="miembro_consejo", persona=x)
    for f in ("egresos.ejecutar_pago", "egresos.preparar_orden", "documentos.cargar"):
        otorgar(motor_migrate, e.cons, m, a, f)
    e.sesion[x] = nueva_sesion(motor_migrate, x)
    return x


def observada(motor_migrate, e, o, p) -> None:
    llamar(motor_migrate, e, e.mc2, CONCILIAR, orden=o, pago=p, resultado="observado",
           motivo="SINTETICO: no coincide")  # fmt: skip


def conforme(motor_migrate, e, o, p) -> None:
    llamar(motor_migrate, e, e.mc2, CONCILIAR, orden=o, pago=p, resultado="conforme", motivo=None)


def declarada(motor_migrate, e, quien, o) -> UUID:
    return llamar(motor_migrate, e, quien, DECLARAR, orden=o, version=1, motivo="SINTETICO")


# Enviar y aprobar ----------------------------------------------------------------------------


def test_enviar_lo_ve_quien_prepara_y_el_consejo_no(motor_migrate, e):
    o = llamar(motor_migrate, e, e.adm, PREPARAR)
    assert ficha(e, e.adm, o)["puede_modificar"] is True
    x = fila(e, e.adm, o)
    assert (x["paso"], x["grupo"], x["le_toca_a"]) == ("enviar", "para_preparar", "vos")
    f = ficha(e, e.adm, o)
    assert (f["situacion"], f["puede_enviar"]) == ("borrador", True)
    # Control: un borrador es trabajo de quien prepara.
    assert paso(e, e.mc1, o) is None
    assert codigo(get(e, e.mc1, f"pagos/{o}")) == (404, "no_encontrado")


def test_aprobar_lo_ve_el_consejo_y_quien_preparo_espera(motor_migrate, e):
    o = enviada(motor_migrate, e)
    assert fila(e, e.mc1, o)["grupo"] == "para_aprobar"
    assert paso(e, e.mc1, o) == "aprobar"
    assert ficha(e, e.mc1, o)["puede_aprobar"] is True
    x = fila(e, e.adm, o)
    assert (x["paso"], x["grupo"], x["le_toca_a"]) == ("espera_aprobar", "esperando", "consejo")
    assert ficha(e, e.adm, o)["puede_aprobar"] is False


def test_quien_preparo_con_otra_cuenta_del_consejo_espera_y_la_ruta_niega(
    motor_migrate, motor_app, e
):
    otra = otra_cuenta_en_el_consejo(motor_migrate, motor_app, e, e.adm)
    o = enviada(motor_migrate, e)
    assert paso(e, otra, o) == "espera_aprobar"
    f = ficha(e, otra, o)
    assert (f["puede_aprobar"], f["puede_rechazar"]) == (False, False)
    cuerpo = {"version": 1, "huella": f["versiones"][0]["huella"]}
    assert post(e, otra, f"ordenes-pago/{o}/aprobaciones", cuerpo).status_code == 403


def test_quien_ya_aprobo_espera_a_la_otra_persona(motor_migrate, motor_app, e):
    exigir(motor_app, e.cons, 2)
    o = enviada(motor_migrate, e)
    llamar(motor_migrate, e, e.mc1, APROBAR, orden=o, version=1,
           huella=huella(motor_migrate, o, 1))  # fmt: skip
    assert paso(e, e.mc1, o) == "espera_aprobar"
    f = ficha(e, e.mc1, o)
    assert (f["puede_aprobar"], f["puede_rechazar"]) == (False, True)
    assert paso(e, e.mc2, o) == "aprobar"  # control


def test_el_hueco_de_quien_cargo_la_cuenta_y_ya_no_prepara_queda_cerrado(motor_migrate, e):
    nueva_cuenta(motor_migrate, e, 1, por=e.mc2)  # firma la misma cuenta y no tiene preparar
    o = enviada(motor_migrate, e)
    assert paso(e, e.mc2, o) == "espera_aprobar"
    f = ficha(e, e.mc2, o)
    assert (f["puede_aprobar"], f["puede_rechazar"]) == (False, True)
    cuerpo = {"version": 1, "huella": f["versiones"][0]["huella"]}
    r = post(e, e.mc2, f"ordenes-pago/{o}/aprobaciones", cuerpo)
    assert codigo(r) == (403, "no_es_quien_cargo_la_cuenta")
    # Control: otro consejero la tiene en la bandeja y la ruta concede.
    assert paso(e, e.mc1, o) == "aprobar"
    assert post(e, e.mc1, f"ordenes-pago/{o}/aprobaciones", cuerpo).status_code == 201


# Pagar, renovar y subir el comprobante -------------------------------------------------------


def test_pagar_lo_ve_quien_ejecuta_y_no_aprobo(motor_migrate, e):
    con(motor_migrate, e, e.mc1, "egresos.ejecutar_pago", "egresos.preparar_orden")
    con(motor_migrate, e, e.mc2, "egresos.ejecutar_pago", "egresos.preparar_orden")
    o = aprobada(motor_migrate, e)  # la aprobó mc1
    assert fila(e, e.adm, o)["grupo"] == "para_pagar"
    assert paso(e, e.mc2, o) == "pagar"
    f = ficha(e, e.mc2, o)
    assert (f["situacion"], f["puede_pagar"]) == ("para_pagar", True)
    # Control: quien aprobó tiene las mismas facultades y no le toca, y la ruta niega.
    assert paso(e, e.mc1, o) is None
    f = ficha(e, e.mc1, o)
    assert f["puede_pagar"] is False
    cuerpo = {"version": 1, "huella": f["versiones"][0]["huella"]}
    assert post(e, e.mc1, f"ordenes-pago/{o}/emitir", cuerpo).status_code == 403
    assert post(e, e.mc2, f"ordenes-pago/{o}/emitir", cuerpo).status_code == 200


def test_sin_ejecutar_no_se_ve_pagar(motor_migrate, e):
    o = aprobada(motor_migrate, e)
    assert paso(e, e.mc2, o) is None
    assert ficha(e, e.mc2, o)["puede_pagar"] is False


def test_renovar_reemplaza_a_pagar_cuando_la_aprobacion_vencio(motor_migrate, e):
    o = aprobada(motor_migrate, e)
    envejecer(motor_migrate, o, timedelta(days=6))
    assert paso(e, e.adm, o) == "pagar"  # control: todavía rige
    envejecer(motor_migrate, o, timedelta(days=8))
    assert fila(e, e.adm, o)["grupo"] == "para_preparar"
    assert paso(e, e.adm, o) == "renovar"
    f = ficha(e, e.adm, o)
    assert (f["puede_renovar"], f["puede_pagar"]) == (True, False)
    assert paso(e, e.mc2, o) is None  # control: es de quien prepara
    r = post(e, e.adm, f"ordenes-pago/{o}/renovar", {"version_esperada": 1})
    assert r.status_code == 200, r.json()


def test_subir_comprobante_lo_ve_quien_vio_la_cuenta(motor_migrate, motor_app, e):
    x = segundo_ejecutor(motor_migrate, motor_app, e)
    o = emitida(motor_migrate, e)
    assert paso(e, e.adm, o) == "pagar"  # todavía nadie la vio
    assert ficha(e, e.adm, o)["situacion"] == "pagando"
    ver(motor_migrate, e, e.adm, o)
    assert fila(e, e.adm, o)["grupo"] == "para_pagar"
    assert paso(e, e.adm, o) == "subir_comprobante"
    f = ficha(e, e.adm, o)
    assert (f["puede_subir_comprobante"], f["puede_registrar"]) == (True, True)
    assert f["puede_reabrir_cuenta"] is True
    # Control: quien no vio la cuenta no sube nada.
    assert ficha(e, x, o)["puede_subir_comprobante"] is False
    assert ficha(e, x, o)["puede_reabrir_cuenta"] is False
    assert paso(e, e.mc1, o) is None


def test_con_la_cuenta_ya_vista_otro_ejecutor_no_recibe_pagar(motor_migrate, motor_app, e):
    """Para que la misma orden no se transfiera dos veces. La base tampoco se lo acepta (`0078`)."""
    x = segundo_ejecutor(motor_migrate, motor_app, e)
    o = emitida(motor_migrate, e)
    # Control: mientras nadie la vio, los dos pueden.
    assert (paso(e, e.adm, o), paso(e, x, o)) == ("pagar", "pagar")
    assert ficha(e, x, o)["puede_pagar"] is True
    ver(motor_migrate, e, e.adm, o)
    y = fila(e, x, o)
    assert (y["paso"], y["grupo"], y["le_toca_a"]) == (
        "espera_comprobante", "esperando", "quien_vio_la_cuenta",
    )  # fmt: skip
    assert ficha(e, x, o)["puede_pagar"] is False
    assert paso(e, e.mc1, o) is None  # control: quien no ejecuta no espera nada acá


def test_un_pago_con_la_aprobacion_vencida_que_nadie_vio_no_queda_invisible(motor_migrate, e):
    """La base no renueva una orden en ejecución y ya no da la cuenta: la salida es declarar que no
    se pagó, confirmarlo y volver a pagar."""
    o = emitida(motor_migrate, e)
    assert paso(e, e.adm, o) == "pagar"  # control: con la aprobación vigente
    assert paso(e, e.mc2, o) is None
    envejecer(motor_migrate, o, timedelta(days=8))
    x = fila(e, e.adm, o)
    assert (x["paso"], x["grupo"]) == ("declarar_no_pago", "para_pagar")
    f = ficha(e, e.adm, o)
    assert (f["puede_declarar"], f["puede_pagar"], f["puede_renovar"]) == (True, False, False)
    y = fila(e, e.mc2, o)
    assert (y["paso"], y["le_toca_a"]) == ("espera_declarar", "quien_ejecuta")
    # La base: renovar no se admite, y por eso `renovar` no se ofrece.
    r = post(e, e.adm, f"ordenes-pago/{o}/renovar", {"version_esperada": 1})
    assert codigo(r) == (409, "orden_no_admite")
    r = post(e, e.adm, f"ordenes-pago/{o}/pago-manual/no-realizado",
             {"version": 1, "motivo": "SINTETICO: venció la aprobación"})  # fmt: skip
    assert r.status_code == 201, r.json()
    d = r.json()["id"]
    assert paso(e, e.mc2, o) == "confirmar_no_pago"
    r = post(e, e.mc2, f"ordenes-pago/{o}/pago-manual/no-realizado/{d}/confirmacion", {})
    assert r.status_code == 200, r.json()
    assert paso(e, e.adm, o) == "volver_a_pagar"


def test_con_la_aprobacion_vencida_quien_vio_la_cuenta_sigue_registrando(motor_migrate, e):
    """Control del anterior: alguien la vio, así que no está trabada (`0067`, fuera de término)."""
    o = emitida(motor_migrate, e)
    ver(motor_migrate, e, e.adm, o)
    envejecer(motor_migrate, o, timedelta(days=8))
    assert paso(e, e.adm, o) == "subir_comprobante"
    assert paso(e, e.mc2, o) is None


def test_quien_vio_la_cuenta_y_no_prepara_no_recibe_lo_que_la_ruta_le_niega(motor_migrate, e):
    """La ruta del comprobante lee la orden como quien prepara."""
    con_ejecutar(motor_migrate, e, e.mc2)
    o = emitida(motor_migrate, e)
    ver(motor_migrate, e, e.mc2, o)
    assert paso(e, e.mc2, o) is None
    assert ficha(e, e.mc2, o)["puede_subir_comprobante"] is False
    cuerpo = {"titulo": "Comprobante SINTETICO", "fecha": "2026-11-06"}
    r = post(e, e.mc2, f"ordenes-pago/{o}/pago-manual/comprobante", cuerpo)
    assert r.status_code == 403


# Conciliar y el hueco ------------------------------------------------------------------------


def test_conciliar_lo_ve_otro_consejero_y_quien_registro_espera(motor_migrate, e):
    o = emitida(motor_migrate, e)
    p = registrada(motor_migrate, e, o)
    assert fila(e, e.mc2, o)["grupo"] == "para_conciliar"
    assert paso(e, e.mc2, o) == "conciliar"
    f = ficha(e, e.mc2, o)
    assert (f["situacion"], f["puede_conciliar"]) == ("falta_conciliar", True)
    assert f["comprobante_actual"]["documento_id"] == f["ejecucion"]["pagos"][-1]["documento_id"]
    x = fila(e, e.adm, o)
    assert (x["paso"], x["le_toca_a"]) == ("espera_conciliar", "consejo")
    r = post(e, e.mc2, f"ordenes-pago/{o}/pago-manual/{p}/conciliacion", {"resultado": "conforme"})
    assert r.status_code == 200, r.json()


def test_quien_vio_la_cuenta_sin_registrar_no_ve_conciliar_y_si_igual_pide_403(motor_migrate, e):
    """§14: el hueco de `test_hueco_conocido_quien_vio_la_cuenta_sin_registrar`, cerrado."""
    o = emitida(motor_migrate, e)
    con_ejecutar(motor_migrate, e, e.mc2)
    p = registrada(motor_migrate, e, o)
    segunda_apertura(motor_migrate, e, e.mc2, o)
    assert paso(e, e.mc2, o) == "espera_conciliar"
    assert ficha(e, e.mc2, o)["puede_conciliar"] is False
    conciliacion = f"ordenes-pago/{o}/pago-manual/{p}/conciliacion"
    r = post(e, e.mc2, conciliacion, {"resultado": "conforme"})
    assert codigo(r) == (403, "no_es_quien_ejecuto")
    # Control: otro consejero, que no vio la cuenta. La bandeja se lo ofrece y la ruta concede.
    assert paso(e, e.mc1, o) == "conciliar"
    assert post(e, e.mc1, conciliacion, {"resultado": "conforme"}).status_code == 200


def test_quien_preparo_con_otra_cuenta_no_ve_conciliar(motor_migrate, motor_app, e):
    otra = otra_cuenta_en_el_consejo(motor_migrate, motor_app, e, e.adm)
    o = emitida(motor_migrate, e)
    registrada(motor_migrate, e, o)
    assert paso(e, otra, o) == "espera_conciliar"
    assert ficha(e, otra, o)["puede_conciliar"] is False


def test_otra_cuenta_de_quien_vio_la_cuenta_tampoco_concilia(motor_migrate, motor_app, e):
    """La apertura guarda la identidad humana, no la cuenta."""
    con_ejecutar(motor_migrate, e, e.mc2)
    otra = otra_cuenta_en_el_consejo(motor_migrate, motor_app, e, e.mc2)
    o = emitida(motor_migrate, e)
    registrada(motor_migrate, e, o)
    segunda_apertura(motor_migrate, e, e.mc2, o)
    assert paso(e, otra, o) == "espera_conciliar"
    assert paso(e, e.mc1, o) == "conciliar"  # control


# Corregir ------------------------------------------------------------------------------------


def test_corregir_lo_ve_quien_registro(motor_migrate, motor_app, e):
    x = segundo_ejecutor(motor_migrate, motor_app, e)
    o = emitida(motor_migrate, e)
    p = registrada(motor_migrate, e, o)
    segunda_apertura(motor_migrate, e, x, o)
    observada(motor_migrate, e, o, p)
    assert fila(e, e.adm, o)["grupo"] == "para_corregir"
    assert paso(e, e.adm, o) == "corregir"
    f = ficha(e, e.adm, o)
    assert (f["situacion"], f["puede_corregir"], f["puede_pagar"]) == ("observado", True, False)
    assert f["observacion_vigente"]["pago_id"] == str(p)
    assert f["observacion_vigente"]["motivo"] == "SINTETICO: no coincide"
    assert f["comprobante_actual"]["documento_id"] == f["ejecucion"]["pagos"][-1]["documento_id"]
    # Control: quien también vio la cuenta y el Consejo esperan; el observado no desaparece.
    for quien in (x, e.mc2):
        y = fila(e, quien, o)
        assert (y["paso"], y["le_toca_a"]) == ("espera_corregir", "quien_registro")
    assert ficha(e, x, o)["puede_corregir"] is False


def test_una_correccion_en_medio_de_la_lectura_no_rompe_la_ficha(motor_migrate, e, monkeypatch):
    """La ficha cruza acciones y registros: los dos tienen que salir del mismo momento."""
    o = emitida(motor_migrate, e)
    p = registrada(motor_migrate, e, o)
    observada(motor_migrate, e, o, p)
    original = egresos._ejecucion_vigente

    def corrigiendo_antes(*argumentos):
        llamar(motor_migrate, e, e.adm, REGISTRAR, **evidencia(motor_migrate, e, o))
        return original(*argumentos)

    monkeypatch.setattr(egresos, "_ejecucion_vigente", corrigiendo_antes)
    f = ficha(e, e.adm, o)
    assert f["situacion"] == "observado"
    assert f["observacion_vigente"]["pago_id"] == str(p)
    monkeypatch.undo()
    # Control: la corrección se confirmó, y la lectura siguiente ya la ve.
    assert ficha(e, e.adm, o)["situacion"] == "falta_conciliar"


def test_el_comprobante_nuevo_reemplaza_al_observado_para_quien_lo_subio(motor_migrate, e):
    o = emitida(motor_migrate, e)
    p = registrada(motor_migrate, e, o)
    observada(motor_migrate, e, o, p)
    anterior = ficha(e, e.adm, o)["comprobante_actual"]["documento_id"]
    nuevo = comprobante(motor_migrate, e, orden=o)
    assert ficha(e, e.adm, o)["comprobante_actual"]["documento_id"] == str(nuevo)
    assert str(nuevo) != anterior
    # Control: el Consejo sigue viendo el del registro: el nuevo no se registró.
    assert ficha(e, e.mc2, o)["comprobante_actual"]["documento_id"] == anterior


def test_un_comprobante_en_cuarentena_no_es_el_actual(motor_migrate, e):
    o = emitida(motor_migrate, e)
    ver(motor_migrate, e, e.adm, o)
    comprobante(motor_migrate, e, estado="en_cuarentena", orden=o)
    assert ficha(e, e.adm, o)["comprobante_actual"] is None
    disponible = comprobante(motor_migrate, e, orden=o)  # control
    assert ficha(e, e.adm, o)["comprobante_actual"]["documento_id"] == str(disponible)


# No se pagó ----------------------------------------------------------------------------------


def test_confirmar_lo_ve_el_consejo_y_quien_declaro_espera(motor_migrate, e):
    o = emitida(motor_migrate, e)
    ver(motor_migrate, e, e.adm, o)
    d = declarada(motor_migrate, e, e.adm, o)
    assert fila(e, e.mc2, o)["grupo"] == "para_confirmar"
    assert paso(e, e.mc2, o) == "confirmar_no_pago"
    f = ficha(e, e.mc2, o)
    assert (f["situacion"], f["puede_confirmar"]) == ("no_pago_declarado", True)
    assert f["declaracion_vigente"]["id"] == str(d)
    x = fila(e, e.adm, o)
    assert (x["paso"], x["le_toca_a"]) == ("espera_confirmar", "consejo")
    f = ficha(e, e.adm, o)
    assert (f["puede_pagar"], f["puede_confirmar"]) == (False, False)
    r = post(e, e.mc2, f"ordenes-pago/{o}/pago-manual/no-realizado/{d}/confirmacion", {})
    assert r.status_code == 200, r.json()


def test_si_falta_una_declaracion_la_ficha_dice_a_quien_le_toca(motor_migrate, motor_app, e):
    x = segundo_ejecutor(motor_migrate, motor_app, e)
    o = emitida(motor_migrate, e)
    ver(motor_migrate, e, e.adm, o)
    segunda_apertura(motor_migrate, e, x, o)
    d = declarada(motor_migrate, e, e.adm, o)
    # Falta x: el Consejo espera y la confirmación se niega. Se ve igual: no desaparece.
    y = fila(e, e.mc2, o)
    assert (y["paso"], y["le_toca_a"]) == ("espera_confirmar", "quien_vio_la_cuenta")
    assert ficha(e, e.mc2, o)["situacion"] == "no_pago_declarado"
    r = post(e, e.mc2, f"ordenes-pago/{o}/pago-manual/no-realizado/{d}/confirmacion", {})
    assert r.status_code == 409
    assert paso(e, x, o) == "subir_comprobante"
    # Retoma quien falta declarar; quien ya declaró no, porque x todavía puede estar pagando.
    assert ficha(e, x, o)["puede_reabrir_cuenta"] is True
    assert ficha(e, e.adm, o)["puede_reabrir_cuenta"] is False
    # Control: declara x y ahora sí le toca al Consejo.
    declarada(motor_migrate, e, x, o)
    assert paso(e, x, o) == "espera_confirmar"
    assert paso(e, e.mc2, o) == "confirmar_no_pago"


def test_una_declaracion_superada_deja_de_estar_vigente(motor_migrate, e):
    o = emitida(motor_migrate, e)
    ver(motor_migrate, e, e.adm, o)
    declarada(motor_migrate, e, e.adm, o)
    f = ficha(e, e.adm, o)
    assert (f["situacion"], f["puede_pagar"], f["puede_reabrir_cuenta"]) == (
        "no_pago_declarado", False, True,
    )  # fmt: skip
    ver(motor_migrate, e, e.adm, o)  # la vuelve a abrir: la base la deja sin efecto
    f = ficha(e, e.adm, o)
    assert (f["situacion"], f["declaracion_vigente"]) == ("pagando", None)
    assert paso(e, e.adm, o) == "subir_comprobante"


def test_lo_que_dice_confirmable_coincide_con_la_lectura_que_ya_existia(
    motor_migrate, motor_app, e
):
    x = segundo_ejecutor(motor_migrate, motor_app, e)
    o = emitida(motor_migrate, e)
    ver(motor_migrate, e, e.adm, o)
    segunda_apertura(motor_migrate, e, x, o)
    declarada(motor_migrate, e, e.adm, o)

    def las_dos() -> tuple[bool, bool]:
        with transaccion(motor_migrate, persona_id=e.mc1, consorcio_id=e.cons) as c:
            hechos = c.execute(text(ACCIONES), {
                "orden": o, "actor": e.mc1, "origen": "api", "sesion": e.sesion[e.mc1],
            }).mappings().one()  # fmt: skip
            lectura = c.execute(
                text("select confirmable from nucleo.declaraciones_pago_no_realizado_lectura("
                     ":o, 'egresos.aprobar_orden', :a, 'api', :s)"),
                {"o": o, "a": e.mc1, "s": e.sesion[e.mc1]},
            ).scalars().all()  # fmt: skip
        return hechos["declaracion_confirmable"], lectura[-1]

    assert las_dos() == (False, False)
    declarada(motor_migrate, e, x, o)
    assert las_dos() == (True, True)


def test_volver_a_pagar_lo_ve_quien_prepara(motor_migrate, e):
    o = emitida(motor_migrate, e)
    ver(motor_migrate, e, e.adm, o)
    d = declarada(motor_migrate, e, e.adm, o)
    llamar(motor_migrate, e, e.mc2, CONFIRMAR, orden=o, declaracion=d)
    assert fila(e, e.adm, o)["grupo"] == "para_preparar"
    assert paso(e, e.adm, o) == "volver_a_pagar"
    assert ficha(e, e.adm, o)["situacion"] == "no_se_pago"
    assert paso(e, e.mc2, o) is None  # control
    f = ficha(e, e.mc2, o)
    assert (f["situacion"], f["paso"], f["le_toca_a"]) == ("no_se_pago", None, None)


# Rechazada, pagada y la consulta -------------------------------------------------------------


def rechazada(motor_migrate, e) -> UUID:
    o = enviada(motor_migrate, e)
    llamar(motor_migrate, e, e.mc1, RECHAZAR, orden=o, version=1, motivo="SINTETICO")
    return o


def test_lo_rechazado_queda_en_la_bandeja_los_dias_de_la_configuracion(motor_migrate, e):
    o = rechazada(motor_migrate, e)
    assert fila(e, e.adm, o)["grupo"] == "para_preparar"
    assert paso(e, e.adm, o) == "rechazada"
    assert ficha(e, e.adm, o)["orden"]["motivo_rechazo"] == "SINTETICO"
    assert paso(e, e.mc1, o) is None  # control: es de quien prepara
    with motor_migrate.begin() as c:
        c.execute(
            text("update nucleo.orden_pago set actualizada_en = now() - interval '8 days'"
                 " where id = :o"), {"o": o},
        )  # fmt: skip
    assert paso(e, e.adm, o) is None
    assert ficha(e, e.adm, o)["paso"] == "rechazada"  # la ficha lo sigue diciendo
    assert str(o) in ids(consulta(e, e.adm, "?filtro=rechazados"))


def test_lo_pagado_sale_de_la_bandeja_y_queda_en_la_consulta(motor_migrate, e):
    o = emitida(motor_migrate, e)
    p = registrada(motor_migrate, e, o)
    assert fila(e, e.mc2, o) is not None  # control: en curso sí está
    conforme(motor_migrate, e, o, p)
    assert fila(e, e.mc2, o) is None and fila(e, e.adm, o) is None
    f = ficha(e, e.mc2, o)
    assert (f["situacion"], f["paso"]) == ("pagada", None)
    [x] = consulta(e, e.mc2, "?filtro=pagados")["elementos"]
    assert (x["orden_id"], x["situacion"], x["estado"]) == (str(o), "pagada", "pagada")
    assert x["numero_operacion"] == orden_numero(motor_migrate, p)
    assert consulta(e, e.mc2, "?filtro=rechazados")["elementos"] == []  # control


def orden_numero(motor_migrate, p) -> str:
    with motor_migrate.connect() as c:
        return c.execute(
            text("select numero_operacion from nucleo.pago_manual where id = :p"), {"p": p}
        ).scalar_one()


def test_la_consulta_busca_por_proveedor_concepto_y_numero_de_operacion(motor_migrate, e):
    o = emitida(motor_migrate, e, concepto="SINTETICO: reparación de la bomba")
    numero = f"OP-{uuid4().hex[:10]}"
    registrada(motor_migrate, e, o, numero=numero)
    otra = aprobada(motor_migrate, e, concepto="SINTETICO: limpieza del tanque")

    assert ids(consulta(e, e.adm, "?busqueda=BOMBA")) == [str(o)]
    assert ids(consulta(e, e.adm, "?busqueda=tanque")) == [str(otra)]
    # Como se guarda: sin guiones y en mayúsculas.
    assert ids(consulta(e, e.adm, f"?busqueda={numero.lower()}")) == [str(o)]
    assert set(ids(consulta(e, e.adm, "?busqueda=proveedor"))) == {str(o), str(otra)}
    assert ids(consulta(e, e.adm, "?busqueda=no-existe-esto")) == []  # control
    # Un comodín de SQL es un carácter más.
    assert ids(consulta(e, e.adm, "?busqueda=%25")) == []


def test_la_consulta_filtra_y_deriva_la_situacion(motor_migrate, e):
    en_curso = aprobada(motor_migrate, e)
    r = rechazada(motor_migrate, e)
    assert ids(consulta(e, e.adm, "?filtro=en_curso")) == [str(en_curso)]
    assert ids(consulta(e, e.adm, "?filtro=rechazados")) == [str(r)]
    assert set(ids(consulta(e, e.adm))) == {str(en_curso), str(r)}  # control: todos
    situaciones = {x["orden_id"]: x["situacion"] for x in consulta(e, e.adm)["elementos"]}
    assert situaciones == {str(en_curso): "para_pagar", str(r): "rechazada"}


def test_la_consulta_es_paginada(motor_migrate, e):
    ordenes = [llamar(motor_migrate, e, e.adm, PREPARAR) for _ in range(3)]
    primera = consulta(e, e.adm, "?limite=2")
    assert (len(primera["elementos"]), primera["siguiente"]) == (2, 2)
    ultima = consulta(e, e.adm, f"?limite=2&saltar={primera['siguiente']}")
    assert (len(ultima["elementos"]), ultima["siguiente"]) == (1, None)
    assert set(ids(primera) + ids(ultima)) == {str(o) for o in ordenes}


def test_la_consulta_no_le_muestra_borradores_al_consejo(motor_migrate, e):
    o = llamar(motor_migrate, e, e.adm, PREPARAR)
    assert ids(consulta(e, e.mc1)) == []
    assert ids(consulta(e, e.adm)) == [str(o)]  # control


@pytest.mark.parametrize(
    ("parametros", "estado"),
    [("?limite=0", 422), ("?limite=101", 422), ("?saltar=-1", 422), ("?filtro=otro", 422)],
)
def test_la_consulta_niega_lo_que_la_base_no_recibe(e, parametros, estado):
    assert get(e, e.adm, f"pagos/consulta{parametros}").status_code == estado


# Quién lee -----------------------------------------------------------------------------------


def test_otro_consorcio_no_ve_nada(motor_migrate, motor_app, e):
    o = emitida(motor_migrate, e)
    otro = escena_economica(motor_migrate, motor_app)
    assert codigo(get(otro, otro.adm, f"pagos/{o}")) == (404, "no_encontrado")
    assert get(otro, otro.adm, "pagos/bandeja").json()["elementos"] == []
    assert consulta(otro, otro.adm)["elementos"] == []
    with transaccion(motor_app, persona_id=otro.adm, consorcio_id=otro.cons) as c:
        filas = c.execute(text(ACCIONES), {
            "orden": o, "actor": otro.adm, "origen": "api", "sesion": otro.sesion[otro.adm],
        }).all()  # fmt: skip
    assert filas == []
    assert fila(e, e.adm, o) is not None  # control


def test_sin_facultad_no_se_lee_y_la_base_lo_niega_igual(motor_migrate, motor_app, e):
    e.sesion[e.prop] = nueva_sesion(motor_migrate, e.prop)
    for ruta in ("pagos/bandeja", "pagos/consulta", f"pagos/{uuid4()}"):
        assert codigo(get(e, e.prop, ruta)) == (403, "sin_permiso")
    assert get(e, e.adm, "pagos/bandeja").status_code == 200  # control
    with pytest.raises(DBAPIError) as error:
        with transaccion(motor_app, persona_id=e.prop, consorcio_id=e.cons) as c:
            c.execute(text(ACCIONES), {
                "orden": None, "actor": e.prop, "origen": "api", "sesion": e.sesion[e.prop],
            })  # fmt: skip
    assert error.value.orig.sqlstate == "42501"


def test_la_base_no_contesta_por_otra_persona(motor_app, e):
    with pytest.raises(DBAPIError) as error:
        with transaccion(motor_app, persona_id=e.adm, consorcio_id=e.cons) as c:
            c.execute(text(ACCIONES), {
                "orden": None, "actor": e.mc1, "origen": "api", "sesion": e.sesion[e.adm],
            })  # fmt: skip
    assert error.value.orig.sqlstate == "42501"


def test_mirar_no_pide_el_gesto_y_si_la_sesion_de_12_horas(motor_migrate, e):
    o = emitida(motor_migrate, e)
    sin_gesto = nueva_sesion(motor_migrate, e.adm, horas_atras=1, totp_min_atras=11)
    r = get(e, e.adm, "pagos/bandeja", sesion=sin_gesto)
    assert r.status_code == 200, r.json()
    # Una falta transitoria no oculta nada: el gesto se pide al actuar.
    assert str(o) in [x["orden_id"] for x in r.json()["elementos"]]
    larga = nueva_sesion(motor_migrate, e.adm, horas_atras=13)
    for ruta in ("pagos/bandeja", "pagos/consulta", f"pagos/{o}"):
        assert codigo(get(e, e.adm, ruta, sesion=larga)) == (403, "sesion_reciente_requerida")


def test_cem_app_no_ejecuta_las_internas(motor_migrate):
    internas = (
        "nucleo.hechos_de_pago_manual(uuid, nucleo.orden_pago)",
        "nucleo.admite_conciliar(uuid, nucleo.orden_pago, uuid, uuid)",
        "nucleo.admite_aprobar(uuid, nucleo.orden_pago, uuid, uuid)",
    )
    with motor_migrate.connect() as c:
        for f in internas:
            permiso = c.execute(
                text("select has_function_privilege('cem_app', cast(:f as regprocedure),"
                     " 'EXECUTE')"), {"f": f},
            ).scalar_one()  # fmt: skip
            assert permiso is False, f
        rutas = c.execute(
            text("select has_function_privilege('cem_app', cast(:f as regprocedure), 'EXECUTE')"),
            {"f": "nucleo.acciones_de_ordenes_lectura(uuid, uuid, text, uuid, interval)"},
        ).scalar_one()  # fmt: skip
    assert rutas is True  # control


def test_las_rutas_fijas_no_se_confunden_con_una_orden(e):
    assert get(e, e.adm, "pagos/bandeja").status_code == 200
    assert get(e, e.adm, "pagos/consulta").status_code == 200
    assert get(e, e.adm, "pagos/no-es-una-orden").status_code == 422  # control


def test_la_cuenta_completa_no_sale_en_ninguna_lectura_aunque_la_haya_visto(motor_migrate, e):
    """Quien vio la cuenta en el acto tampoco la recibe de nuevo por acá."""
    o = emitida(motor_migrate, e)
    assert ver(motor_migrate, e, e.adm, o)["cuenta_destino"] == CUENTA  # control: el acto sí la da
    p = registrada(motor_migrate, e, o)
    for quien in (e.adm, e.mc1, e.mc2):
        for ruta in ("pagos/bandeja", "pagos/consulta", f"pagos/{o}"):
            r = get(e, quien, ruta)
            assert r.status_code == 200, r.json()
            assert CUENTA not in r.text
    assert ficha(e, e.adm, o)["versiones"][0]["cuenta_ultimos_digitos"] == CUENTA[-4:]
    assert str(p) in get(e, e.adm, f"pagos/{o}").text  # control: la respuesta trae el registro


# Migración -----------------------------------------------------------------------------------


def test_0072_ida_y_vuelta(base_aparte):
    cfg, motor = base_aparte

    def funciones() -> int:
        with motor.connect() as c:
            return c.execute(
                text("select count(*) from pg_proc p join pg_namespace n on n.oid = p.pronamespace"
                     " where n.nspname = 'nucleo' and (p.proname like 'admite\\_%'"
                     " or p.proname in ('acciones_de_ordenes_lectura', 'pagos_consulta_lectura',"
                     " 'comprobante_subido_lectura', 'hechos_de_pago_manual', 'falta_que_declare',"
                     " 'registro_el_pago_observado', 'pago_sin_salida'))"),
            ).scalar_one()  # fmt: skip

    command.upgrade(cfg, "0071")
    assert funciones() == 0
    command.upgrade(cfg, "0072")
    assert funciones() == 15
    command.downgrade(cfg, "0071")
    assert funciones() == 0
    command.upgrade(cfg, "0072")  # control: vuelve a subir limpio
    assert funciones() == 15


def test_ver_la_cuenta_encadena_emitir_y_abrir(motor_migrate, e):
    """Lo que hace la ficha con «Ver la cuenta para pagar». El gesto vale para las dos rutas."""
    o = aprobada(motor_migrate, e)
    f = ficha(e, e.adm, o)
    assert (f["situacion"], f["paso"]) == ("para_pagar", "pagar")
    vista = {"version": 1, "huella": f["versiones"][0]["huella"]}
    # Control: sola, la cuenta no pasa de aprobada a en ejecución.
    assert post(e, e.adm, f"ordenes-pago/{o}/pago-manual/cuenta", vista).status_code == 409
    r = post(e, e.adm, f"ordenes-pago/{o}/emitir", vista)
    assert r.status_code == 200, r.json()
    r = post(e, e.adm, f"ordenes-pago/{o}/pago-manual/cuenta", vista)
    assert r.status_code == 200, r.json()
    assert (r.json()["cuenta_destino"], r.json()["version"]) == (CUENTA, 1)
    assert r.json()["huella"] == vista["huella"]
    assert paso(e, e.adm, o) == "subir_comprobante"
