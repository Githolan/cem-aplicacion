"""Egresos y pagos de la tanda 8 (`0110`; 18-11-2026): pedido de salteo (pregunta 3), motivo al
quitar un rubro legal (pregunta 4), lo que el habitante autorizado no recibe, la orden con el
proveedor sin cuenta (FL-ORD-06), por qué no se ofrece aprobar (FL-APR-02), el acta de una
decisión que quedó sin ella (FL-DEC-01) y lo cotejado en la bandeja (FL-CON-01).

Cada regla con su control. Contra PostgreSQL real. Datos SINTETICOS / PRUEBA / NO REAL.
"""

from uuid import UUID

import pytest
from sqlalchemy import text

from tests.conftest import clave_idempotencia
from tests.test_documentos_2c1 import (  # noqa: F401  (`bucket` y `firmas_de_la_imagen`: fixtures)
    bucket,
    firmas_de_la_imagen,
    pdf,
)
from tests.test_lectura_carga_0088 import documento_comun, subir
from tests.test_orden_pago_neutra import exigir, nueva_sesion, nuevo_proveedor
from tests.test_quitar_retirar_0089 import comun_con_archivo, versiones_de
from tests.test_rf50_autorizacion_0105 import cambiar
from tests.test_rf50_salteo_0106 import bloqueado, saltear
from tests.test_rutas_egresos import ORDEN, cliente, codigo, get, huella, post, preparar
from tests.test_rutas_egresos import e as e  # noqa: F401  (es un fixture)

pytestmark = [pytest.mark.db, pytest.mark.redis]
# ruff: noqa: F811

MOTIVO = "SINTETICO: se corta el agua del edificio, el plomero viene hoy"
PEDIDO = {"motivo": MOTIVO, "importe_centavos": 5_000_000, "moneda": "ARS"}


def pedir(e, quien: UUID | None = None, proveedor=None, cuerpo=None):
    return post(e, quien or e.adm, f"proveedores/{proveedor or e.proveedor}/pedidos-salteo",
                cuerpo or PEDIDO)  # fmt: skip


def pedidos(e, quien: UUID) -> list[dict]:
    r = get(e, quien, "pedidos-salteo")
    assert r.status_code == 200, r.text
    return r.json()["elementos"]


# Pregunta 3 (FL-SAL-01, FL-SAL-02): el pedido de salteo ------------------------------------------


def test_el_pedido_espera_al_consejo_y_queda_atendido_con_el_salteo(motor_migrate, e):
    bloqueado(e)
    r = pedir(e)
    assert r.status_code == 201, r.text
    p = r.json()
    assert (p["estado"], p["motivo"], p["importe_centavos"], p["propio"]) == (
        "pendiente", MOTIVO, 5_000_000, True)  # fmt: skip
    # Uno por vez: otro pedido mientras espera se niega.
    assert codigo(pedir(e)) == (409, "pedido_salteo_pendiente")
    # El Consejo lo lee pendiente, y no es suyo.
    [visto] = pedidos(e, e.mc1)
    assert (visto["numero"], visto["estado"], visto["propio"]) == (p["numero"], "pendiente", False)
    # El Consejo saltea: el pedido queda atendido, para los dos.
    s = saltear(e)
    assert s.status_code == 201, s.text
    for quien in (e.adm, e.mc1):
        [x] = pedidos(e, quien)
        assert (x["estado"], x["salteo_numero"]) == ("atendido", s.json()["numero"])
    # Con un salteo que le sirve, no se pide otro.
    assert codigo(pedir(e)) == (409, "salteo_vigente")
    # Queda en la auditoría, sin el motivo (E31), y no se cambia ni se borra.
    with motor_migrate.connect() as c:
        [ev] = c.execute(text(
            "select actor_persona_id, despues from auditoria.eventos"
            " where accion = 'consorcio.proveedor.pedido_salteo' and consorcio_id = :c"),
            {"c": e.cons}).all()  # fmt: skip
    assert ev.actor_persona_id == e.adm and "motivo" not in ev.despues
    with pytest.raises(Exception) as error, motor_migrate.begin() as c:
        c.execute(text("delete from nucleo.pedido_salteo_documentacion"))
    assert error.value.orig.diag.message_hint == "historia_inmutable"


def test_sin_bloqueo_no_se_pide_y_el_consejo_no_pide(e):
    assert codigo(pedir(e)) == (409, "salteo_innecesario")
    bloqueado(e)
    assert codigo(pedir(e, quien=e.mc1)) == (403, "sin_permiso")
    for cuerpo in (PEDIDO | {"motivo": "  "}, PEDIDO | {"importe_centavos": 0}):
        assert pedir(e, cuerpo=cuerpo).status_code == 422
    assert pedir(e).status_code == 201  # control: con el bloqueo, quien prepara pide


# Pregunta 4 (FL-LEG-10): quitar un rubro con faltantes pide motivo -------------------------------


def retirar_rubro(e, nombre: str, cuerpo: dict | None = None):
    with cliente(e.adm, e.sesion[e.adm]) as c:
        return c.request(
            "DELETE", f"/api/v1/consorcios/{e.cons}/proveedores/{e.proveedor}/rubros-legales/"
            f"{nombre}", json=cuerpo, headers=clave_idempotencia(),
        )  # fmt: skip


def test_quitar_un_rubro_con_faltantes_pide_motivo_y_el_consejo_lo_lee(e):
    bloqueado(e)
    assert codigo(retirar_rubro(e, "ascensores")) == (422, "motivo_requerido")
    assert retirar_rubro(e, "ascensores", {"motivo": " "}).status_code == 422
    r = retirar_rubro(e, "ascensores", {"motivo": "SINTETICO: no hace ascensores, se cargó mal"})
    assert r.status_code == 200, r.text
    # Destraba: la orden se prepara.
    assert preparar(e)
    [x] = get(e, e.mc1, f"consulta-proveedores/{e.proveedor}").json()["retiros_con_motivo"]
    assert (x["rubro_legal"], x["motivo"]) == ("ascensores", "SINTETICO: no hace ascensores,"
                                                             " se cargó mal")  # fmt: skip
    assert len(x["faltaban"]) > 0
    [y] = get(e, e.adm, f"proveedores/{e.proveedor}").json()["retiros_con_motivo"]
    assert y == x


# Privacidad (tanda 7): el habitante autorizado no recibe la historia de salteos ----------------


def test_el_habitante_autorizado_no_recibe_salteos_ni_retiros(motor_migrate, e):
    e.sesion[e.prop] = nueva_sesion(motor_migrate, e.prop)
    bloqueado(e)
    assert saltear(e).status_code == 201
    assert cambiar(e, e.prop).status_code == 200
    ficha = get(e, e.prop, f"consulta-proveedores/{e.proveedor}")
    assert ficha.status_code == 200, ficha.text
    assert ficha.json()["salteos"] == [] and ficha.json()["retiros_con_motivo"] == []
    # Control: el Consejo sí los recibe.
    assert len(get(e, e.mc1, f"consulta-proveedores/{e.proveedor}").json()["salteos"]) == 1
    # Y la lista dice hasta cuándo sirve el salteo, a quien prepara.
    lista = get(e, e.adm, "proveedores").json()
    assert [v["proveedor_id"] for v in lista["vence_salteo"]] == [str(e.proveedor)]


# FL-ORD-06: la orden aprobada a un proveedor sin cuenta ------------------------------------------


def aprobada(e, proveedor: UUID) -> UUID:
    o = preparar(e, proveedor_id=str(proveedor))
    r = post(e, e.adm, f"ordenes-pago/{o}/enviar-a-revision", {"version_esperada": 1})
    assert r.status_code == 200, r.text
    r = post(e, e.mc1, f"ordenes-pago/{o}/aprobaciones", {"version": 1, "huella": huella(e, o, 1)})
    assert r.status_code == 201, r.text
    return o


def fila(e, quien, o) -> dict | None:
    r = get(e, quien, "pagos/bandeja")
    assert r.status_code == 200, r.text
    return next((x for x in r.json()["elementos"] if x["orden_id"] == str(o)), None)


def test_la_orden_sin_cuenta_le_dice_a_quien_prepara_que_falta_la_cuenta(motor_migrate, e):
    sin_cuenta = nuevo_proveedor(motor_migrate, e.cons, e.adm, cuenta=None, razon="Sin cuenta SINT")
    o = aprobada(e, sin_cuenta)
    x = fila(e, e.adm, o)
    assert x is not None and x["paso"] == "falta_cuenta"
    f = get(e, e.adm, f"pagos/{o}").json()
    assert (f["paso"], f["motivo_sin_acto"]) == ("falta_cuenta", "proveedor_sin_cuenta")
    # Control: con cuenta, la misma orden aprobada está para pagar, sin motivo.
    con = aprobada(e, e.proveedor)
    assert fila(e, e.adm, con)["paso"] != "falta_cuenta"
    assert get(e, e.adm, f"pagos/{con}").json()["motivo_sin_acto"] is None


# FL-APR-02: por qué no se ofrece aprobar ---------------------------------------------------------


def test_quien_ya_aprobo_sabe_por_que_no_aprueba(motor_app, e):
    exigir(motor_app, e.cons, 2)
    o = preparar(e)
    assert post(e, e.adm, f"ordenes-pago/{o}/enviar-a-revision",
                {"version_esperada": 1}).status_code == 200  # fmt: skip
    r = post(e, e.mc1, f"ordenes-pago/{o}/aprobaciones", {"version": 1, "huella": huella(e, o, 1)})
    assert r.status_code == 201, r.text
    assert get(e, e.mc1, f"pagos/{o}").json()["motivo_sin_acto"] == "ya_aprobaste"
    # Control: quien no aprobó todavía puede aprobar, y no hay motivo.
    assert get(e, e.mc2, f"pagos/{o}").json()["motivo_sin_acto"] is None


# FL-CON-01: lo cotejado, contado en la bandeja ---------------------------------------------------


def test_la_bandeja_cuenta_lo_cotejado(e):
    # Sin nada cotejado, cero para quien coteja y para quien no.
    assert get(e, e.mc2, "pagos/bandeja").json()["cotejados"] == 0
    assert get(e, e.adm, "pagos/bandeja").json()["cotejados"] == 0


# FL-DEC-01: el acta de una decisión que quedó sin ella -------------------------------------------


def decision_sin_acta(e) -> str:
    r = post(e, e.adm, "decisiones", {
        "tipo": "consejo", "fecha": "2026-11-01", "descripcion": "Aprueba SINTETICO",
        "referencia": "Acta 7, libro 2, folio 3"})  # fmt: skip
    assert r.status_code == 201, r.text
    return r.json()["id"]


def adjuntar(e, decision, sha: str, quien: UUID | None = None, clave=None):
    quien = quien or e.adm
    with cliente(quien, e.sesion[quien]) as c:
        return c.put(f"/api/v1/consorcios/{e.cons}/decisiones/{decision}/acta",
                     json={"sha256": sha}, headers=clave or clave_idempotencia())  # fmt: skip


def tiene_acta(e, decision) -> bool:
    lista = get(e, e.adm, "decisiones").json()["elementos"]
    return next(d["tiene_acta"] for d in lista if d["id"] == decision)


def test_el_acta_se_adjunta_una_vez_con_un_documento_disponible(motor_migrate, motor_app, e,
                                                               bucket):  # fmt: skip
    doc, _ = comun_con_archivo(motor_app, e, "actas_y_decisiones")
    [v] = versiones_de(motor_migrate, doc)
    d = decision_sin_acta(e)
    assert tiene_acta(e, d) is False
    # Una huella que no es de ningún documento del consorcio no sirve.
    assert codigo(adjuntar(e, d, "0" * 64)) == (422, "acta_no_disponible")
    clave = clave_idempotencia()
    r = adjuntar(e, d, v["sha256"], clave=clave)
    assert r.status_code == 200, r.text
    assert tiene_acta(e, d) is True
    assert get(e, e.adm, f"decisiones/{d}").json()["sha256"] == v["sha256"]
    # El reintento con la misma clave recibe lo mismo; otra vez, ya tiene acta.
    assert adjuntar(e, d, v["sha256"], clave=clave).status_code == 200
    assert codigo(adjuntar(e, d, v["sha256"])) == (409, "acta_ya_registrada")
    with motor_migrate.connect() as c:
        [ev] = c.execute(text(
            "select actor_persona_id, antes, despues from auditoria.eventos"
            " where accion = 'consorcio.decision.adjuntar_acta' and entidad_id = :d"),
            {"d": d}).all()  # fmt: skip
    assert ev.actor_persona_id == e.adm and ev.despues["sha256_acta"] == v["sha256"]
    assert ev.antes["sha256_acta"] is None
    # Desde ahí, el documento es el acta: no se retira.
    from tests.test_quitar_retirar_0089 import retirar

    assert codigo(retirar(e, doc)) == (409, "documento_referenciado")


def test_el_acta_en_analisis_no_se_adjunta_y_sin_permiso_tampoco(motor_migrate, e, bucket):
    d = decision_sin_acta(e)
    doc = documento_comun(e, "actas_y_decisiones")
    subir(e, doc, pdf(flujo=b"0 0 7 7 re f"))
    [v] = versiones_de(motor_migrate, doc)
    assert v["estado"] == "en_cuarentena"
    assert codigo(adjuntar(e, d, v["sha256"])) == (422, "acta_no_disponible")
    assert tiene_acta(e, d) is False
    # Sin `decisiones.registrar` (el propietario, sin mandato): 403.
    e.sesion[e.prop] = nueva_sesion(motor_migrate, e.prop)
    assert codigo(adjuntar(e, d, v["sha256"], quien=e.prop)) == (403, "sin_permiso")
    # Una decisión que no existe: 404.
    assert codigo(adjuntar(e, e.proveedor, v["sha256"])) == (404, "no_encontrado")


def test_orden_con_salteo_sigue_pasando(e):
    """Control del pedido: el salteo que lo atiende sigue sirviendo para la orden."""
    bloqueado(e)
    assert pedir(e).status_code == 201
    assert saltear(e).status_code == 201
    r = post(e, e.adm, "ordenes-pago", ORDEN | {"proveedor_id": str(e.proveedor)})
    assert r.status_code == 201, r.text


# FL-ORD-05: «Preparar otra orden» solo si el proveedor la admite hoy ----------------------------


def test_la_rechazada_no_ofrece_otra_orden_a_un_proveedor_bloqueado(e):
    o = preparar(e)
    assert post(e, e.adm, f"ordenes-pago/{o}/enviar-a-revision",
                {"version_esperada": 1}).status_code == 200  # fmt: skip
    r = post(e, e.mc1, f"ordenes-pago/{o}/rechazar", {"version": 1, "motivo": "Falta factura"})
    assert r.status_code == 200, r.text

    def ofrece(f) -> bool:
        return "preparar_otra" in {a["codigo"] for a in f["acciones"]}

    def aviso() -> dict:
        [x] = [a for a in get(e, e.adm, "pagos/bandeja").json()["avisos_rechazo"]
               if a["orden_id"] == str(o)]  # fmt: skip
        return x

    f = get(e, e.adm, f"pagos/{o}").json()
    assert f["proveedor_admite_orden"] is True and ofrece(f)  # control: sin bloqueo
    assert aviso()["proveedor_admite_orden"] is True
    bloqueado(e)
    f = get(e, e.adm, f"pagos/{o}").json()
    assert f["proveedor_admite_orden"] is False and not ofrece(f)
    assert aviso()["proveedor_admite_orden"] is False
    # Con un salteo que le sirve a quien prepara, vuelve a admitirla.
    assert saltear(e).status_code == 201
    f = get(e, e.adm, f"pagos/{o}").json()
    assert f["proveedor_admite_orden"] is True and ofrece(f)
    # Quien no prepara: falso.
    assert get(e, e.mc1, f"pagos/{o}").json()["proveedor_admite_orden"] is False
