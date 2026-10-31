"""0055: pago manual asistido y su conciliación (piloto sin riel). Datos SINTETICOS / PRUEBA / NO
REAL: ninguna cuenta es de un banco (prefijo 999) y no se mueve dinero.

La privacidad depende del acto, no de una etiqueta sobre la persona (propietario, 31-10-2026):
quien prepara no recibe la cuenta completa mientras prepara, y si después ejecuta legítimamente
una orden ya aprobada que no aprobó, la recibe solo en el acto de ejecución, con todas las
barreras. Separación: preparar ≠ aprobar, aprobar ≠ ejecutar, ejecutar ≠ conciliar.
"""

import importlib.util
import re
import threading
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.consorcios import pagos
from app.db import transaccion
from tests.conftest import clave_idempotencia
from tests.test_egresos_1a import (
    BAJA_CONTRATO,
    _hoy,
    aprobada,
    confirmado,
    documento_de_contrato,
    envejecer,
    nueva_cuenta,
)
from tests.test_mandatos import otra_persona
from tests.test_mandatos_0012 import alta
from tests.test_orden_pago_neutra import (
    EMITIR,
    ESENCIALES,
    PREPARAR,
    Escena,
    cbu_sintetico,
    escena_economica,
    huella,
    llamar,
    niega,
    nueva_sesion,
    orden,
    otorgar,
    otra_cuenta_en_el_consejo,
)
from tests.test_pagos_0051 import correr, ejecuciones, habilitar_riel, resultados
from tests.test_rutas_egresos import cliente, codigo, get, post
from tests.test_vinculo_0014 import base_aparte  # noqa: F401  (es un fixture)

pytestmark = pytest.mark.db
# `base_aparte` es un fixture importado: el parámetro con su nombre no lo redefine.
# ruff: noqa: F811

CUENTA = cbu_sintetico(1)
"""La del proveedor de la escena."""
IMPORTE = ESENCIALES["importe"]
VER = (
    "select * from nucleo.cuenta_para_pago_manual(:orden, :version, :huella, :actor, :origen,"
    " :sesion)"
)
REGISTRAR = (
    "select nucleo.registrar_pago_manual(:orden, :version, :huella, :numero, :fecha, :importe_p,"
    " :moneda_p, :cuenta, :documento, :actor, :origen, :sesion)"
)
CONCILIAR = (
    "select nucleo.conciliar_pago_manual(:orden, :pago, :resultado, :motivo, :actor, :origen,"
    " :sesion)"
)
DECLARAR = (
    "select nucleo.declarar_pago_no_realizado(:orden, :version, :motivo, :actor, :origen, :sesion)"
)
CONFIRMAR = (
    "select nucleo.confirmar_pago_no_realizado(:orden, :declaracion, :actor, :origen, :sesion)"
)
DIGITOS_DE_CUENTA = re.compile(r"\d{22}")


@pytest.fixture
def e(motor_migrate, motor_app) -> Escena:
    return escena_economica(motor_migrate, motor_app)


def emitida(motor_migrate, e, **extra) -> UUID:
    """Preparada por la administración, aprobada por mc1 y emitida por la administración: la
    ejecución nace manual (el consorcio no tiene el riel habilitado)."""
    o = aprobada(motor_migrate, e, **extra)
    llamar(motor_migrate, e, e.adm, EMITIR, orden=o, version=1)
    return o


def comprobante(motor_migrate, e, estado="disponible", categoria="facturas_y_comprobantes"):
    return documento_de_contrato(motor_migrate, e, categoria=categoria, version=estado)


def evidencia(motor_migrate, e, o, **cambios) -> dict:
    datos = {
        "orden": o, "version": 1, "huella": huella(motor_migrate, o, 1),
        "numero": f"OP-{uuid4().hex[:10]}", "fecha": _hoy(motor_migrate, e),
        "importe_p": IMPORTE, "moneda_p": "ARS", "cuenta": CUENTA,
        "documento": comprobante(motor_migrate, e),
    }  # fmt: skip
    return datos | cambios


def registrada(motor_migrate, e, o, quien=None, **cambios) -> UUID:
    """Quien transfiere entra antes al acto de ejecución (recibe la cuenta) y después registra."""
    quien = quien or e.adm
    ver(motor_migrate, e, quien, o)
    return llamar(motor_migrate, e, quien, REGISTRAR,
                  **evidencia(motor_migrate, e, o, **cambios))  # fmt: skip


def ver(motor_migrate, e, quien, o) -> dict:
    with transaccion(motor_migrate, persona_id=quien, consorcio_id=e.cons) as c:
        return dict(c.execute(text(VER), {
            "orden": o, "version": 1, "huella": huella(motor_migrate, o, 1), "actor": quien,
            "origen": "api", "sesion": e.sesion[quien],
        }).mappings().one())  # fmt: skip


def acta(motor_migrate, e) -> UUID:
    with motor_migrate.connect() as c:
        return c.execute(
            text("select decision_id from nucleo.mandato where consorcio_id = :c"
                 " and persona_id = :p"), {"c": e.cons, "p": e.mc1},
        ).scalar_one()  # fmt: skip


def otro_ejecutor(motor_migrate, motor_app, e) -> UUID:
    """Otra persona del Consejo con `egresos.ejecutar_pago` (otorgada por la prueba): ejecuta lo
    que preparó la administración sin haberlo preparado."""
    x = otra_persona(motor_migrate, e.cons)
    a = acta(motor_migrate, e)
    m = alta(motor_app, e.cons, e.prop, a, tipo="miembro_consejo", persona=x)
    otorgar(motor_migrate, e.cons, m, a, "egresos.ejecutar_pago")
    e.sesion[x] = nueva_sesion(motor_migrate, x)
    return x


def con_ejecutar(motor_migrate, e, persona) -> None:
    with motor_migrate.connect() as c:
        m = c.execute(
            text("select id from nucleo.mandato where consorcio_id = :c and persona_id = :p"),
            {"c": e.cons, "p": persona},
        ).scalar_one()  # fmt: skip
    otorgar(motor_migrate, e.cons, m, acta(motor_migrate, e), "egresos.ejecutar_pago")


def desvincular(motor_migrate, e, persona: UUID) -> None:
    with motor_migrate.begin() as c:
        n = c.execute(
            text("update nucleo.identidad_persona set desvinculada_en = now()"
                 " where consorcio_id = :c and persona_id = :p and desvinculada_en is null"),
            {"c": e.cons, "p": persona},
        ).rowcount  # fmt: skip
    assert n == 1


def eventos_del_consorcio(motor_migrate, cons) -> list[str]:
    with motor_migrate.connect() as c:
        return [f[0] for f in c.execute(
            text("select e::text from auditoria.eventos e where consorcio_id = :c"), {"c": cons}
        )]  # fmt: skip


def acciones(motor_migrate, o) -> list[str]:
    with motor_migrate.connect() as c:
        return [f[0] for f in c.execute(
            text("select accion from auditoria.eventos where entidad = 'orden_pago'"
                 " and entidad_id = :o order by secuencia"), {"o": o},
        )]  # fmt: skip


def pagos_de(motor_migrate, o) -> list[dict]:
    with motor_migrate.connect() as c:
        return [dict(f) for f in c.execute(
            text("select p.*, k.resultado as conciliacion from nucleo.pago_manual p"
                 " join nucleo.ejecucion_orden_pago x on x.id = p.ejecucion_id"
                 " left join nucleo.conciliacion_pago_manual k on k.pago_id = p.id"
                 " where x.orden_id = :o order by p.registrado_en, p.id"), {"o": o},
        ).mappings()]  # fmt: skip


def confirmables(e, o) -> dict[str, bool]:
    """Lo que la revisión del Consejo dice que puede confirmar, por declaración."""
    elementos = get(e, e.mc1, "pagos-manuales/revision").json()["elementos"]
    [elemento] = [x for x in elementos if x["orden"]["id"] == str(o)]
    return {d["id"]: d["confirmable"] for d in elemento["ejecucion"]["declaraciones"]}


# El caso del piloto, de punta a punta por HTTP ---------------------------------------------


def test_piloto_la_administracion_prepara_y_ejecuta_y_otro_consejero_concilia(motor_migrate, e):
    """Punto 15 del 31-10: la administración prepara, mc1 aprueba, la misma administración entra
    al acto de ejecución y recién ahí recibe la cuenta completa, registra el pago y mc2 concilia."""
    r = post(e, e.adm, "ordenes-pago", {
        "importe_centavos": IMPORTE, "moneda": "ARS", "proveedor_id": str(e.proveedor),
        "concepto": "SINTETICO: reparación de la bomba", "comprobante": None,
        "usa_fondo_reserva": False,
    })  # fmt: skip
    assert r.status_code == 201, r.json()
    o = UUID(r.json()["id"])
    assert post(e, e.adm, f"ordenes-pago/{o}/enviar-a-revision",
                {"version_esperada": 1}).status_code == 200  # fmt: skip
    h = next(v["huella"] for v in get(e, e.mc1, f"ordenes-pago/{o}/revision").json()["versiones"])
    # Antes de aprobar, el acto de ejecución no existe para nadie: sin la cuenta.
    r = post(e, e.adm, f"ordenes-pago/{o}/pago-manual/cuenta", {"version": 1, "huella": h})
    assert (r.status_code, r.json()["codigo"]) == (403, "sin_permiso")
    assert CUENTA not in r.text
    assert post(e, e.mc1, f"ordenes-pago/{o}/aprobaciones",
                {"version": 1, "huella": h}).status_code == 201  # fmt: skip
    # Aprobada y todavía sin emitir: tampoco.
    r = post(e, e.adm, f"ordenes-pago/{o}/pago-manual/cuenta", {"version": 1, "huella": h})
    assert (r.status_code, r.json()["codigo"]) == (409, "orden_no_admite")
    assert post(e, e.adm, f"ordenes-pago/{o}/emitir",
                {"version": 1, "huella": h}).status_code == 200  # fmt: skip
    # La preparación (la ficha y la lista de pagos) sigue sin la cuenta completa.
    for lectura in (f"ordenes-pago/{o}", "pagos-manuales", "proveedores", "ordenes-pago"):
        r = get(e, e.adm, lectura)
        assert r.status_code == 200, (lectura, r.json())
        assert CUENTA not in r.text, lectura
    [elemento] = get(e, e.adm, "pagos-manuales").json()["elementos"]
    assert (elemento["orden"]["estado"], elemento["ejecucion"]["pagos"]) == ("en_ejecucion", [])
    # El acto de ejecución: la cuenta completa, solo acá.
    r = post(e, e.adm, f"ordenes-pago/{o}/pago-manual/cuenta", {"version": 1, "huella": h})
    assert r.status_code == 200, r.json()
    datos = r.json()
    assert (datos["cuenta_destino"], datos["importe_centavos"], datos["moneda"]) == (
        CUENTA,
        IMPORTE,
        "ARS",
    )
    assert datos["razon_social"] == "Proveedor SINTETICO (NO REAL)"
    # Registra la transferencia sintética con su comprobante.
    doc = comprobante(motor_migrate, e)
    r = post(e, e.adm, f"ordenes-pago/{o}/pago-manual", {
        "version": 1, "huella": h, "numero_operacion": "0001-234567", "fecha_operacion":
        str(_hoy(motor_migrate, e)), "importe_centavos": IMPORTE, "moneda": "ARS",
        "cuenta_destino": CUENTA, "documento_id": str(doc),
    })  # fmt: skip
    assert r.status_code == 201, r.json()
    pago = r.json()["id"]
    assert orden(motor_migrate, o)["estado"] == "en_verificacion"
    assert resultados(motor_migrate, o) == []  # registrado no es pagado
    # mc2 concilia conforme: recién ahí, Pagada.
    [pendiente] = get(e, e.mc2, "pagos-manuales/revision").json()["elementos"]
    assert pendiente["ejecucion"]["pagos"][0]["numero_operacion"] == "0001234567"
    assert CUENTA not in get(e, e.mc2, "pagos-manuales/revision").text
    r = post(e, e.mc2, f"ordenes-pago/{o}/pago-manual/{pago}/conciliacion",
             {"resultado": "conforme"})  # fmt: skip
    assert r.status_code == 200, r.json()
    assert r.json()["estado"] == "pagada"
    assert orden(motor_migrate, o)["estado"] == "pagada"
    assert resultados(motor_migrate, o) == [("pagada", "manual")]
    with motor_migrate.connect() as c:
        referencia = c.execute(
            text("select r.referencia from nucleo.resultado_ejecucion_orden_pago r"
                 " join nucleo.ejecucion_orden_pago x on x.id = r.ejecucion_id"
                 " where x.orden_id = :o"), {"o": o},
        ).scalar_one()  # fmt: skip
    assert referencia == "0001234567"
    assert acciones(motor_migrate, o)[-3:] == [
        "consorcio.orden_pago.ver_cuenta_para_pago",
        "consorcio.orden_pago.registrar_pago_manual",
        "consorcio.orden_pago.conciliar_pago_manual",
    ]
    # Ninguna cuenta completa en la auditoría (control: el evento del acto tiene los 4 últimos).
    todos = eventos_del_consorcio(motor_migrate, e.cons)
    assert not [t for t in todos if DIGITOS_DE_CUENTA.search(t)]
    assert any(CUENTA[-4:] in t and "ver_cuenta_para_pago" in t for t in todos)


def test_no_hay_ejecucion_manual_sin_evidencia_y_sin_conciliacion(motor_migrate, e):
    """No hay un botón «pagado»: emitir y ver la cuenta no pagan nada."""
    o = emitida(motor_migrate, e)
    ver(motor_migrate, e, e.adm, o)
    assert orden(motor_migrate, o)["estado"] == "en_ejecucion"
    assert resultados(motor_migrate, o) == []


# Negativos de privacidad (punto 16) --------------------------------------------------------


def test_la_cuenta_solo_sale_en_el_acto_con_la_orden_emitida(motor_migrate, e):
    o = llamar(motor_migrate, e, e.adm, PREPARAR)
    niega(motor_migrate, e, e.adm, VER, "estado_invalido", orden=o, version=1,
          huella=huella(motor_migrate, o, 1))  # fmt: skip
    o = aprobada(motor_migrate, e)
    niega(motor_migrate, e, e.adm, VER, "estado_invalido", orden=o, version=1,
          huella=huella(motor_migrate, o, 1))  # fmt: skip
    llamar(motor_migrate, e, e.adm, EMITIR, orden=o, version=1)
    assert ver(motor_migrate, e, e.adm, o)["cuenta_destino"] == CUENTA  # control


def test_sin_la_facultad_de_ejecutar_no_hay_cuenta(motor_migrate, e):
    """mc2 tiene `egresos.aprobar_orden` y recibe `egresos.preparar_orden`, pero no ejecuta."""
    o = emitida(motor_migrate, e)
    with motor_migrate.connect() as c:
        m = c.execute(
            text("select id from nucleo.mandato where consorcio_id = :c and persona_id = :p"),
            {"c": e.cons, "p": e.mc2},
        ).scalar_one()  # fmt: skip
    otorgar(motor_migrate, e.cons, m, acta(motor_migrate, e), "egresos.preparar_orden")
    niega(motor_migrate, e, e.mc2, VER, "42501", orden=o, version=1,
          huella=huella(motor_migrate, o, 1))  # fmt: skip
    r = post(e, e.mc2, f"ordenes-pago/{o}/pago-manual/cuenta",
             {"version": 1, "huella": huella(motor_migrate, o, 1).hex()})  # fmt: skip
    assert (r.status_code, r.json()["codigo"]) == (403, "sin_permiso")
    assert CUENTA not in r.text


def test_quien_aprobo_no_ejecuta_ni_con_otra_cuenta(motor_migrate, motor_app, e):
    o = emitida(motor_migrate, e)
    con_ejecutar(motor_migrate, e, e.mc1)
    niega(motor_migrate, e, e.mc1, VER, "no_es_quien_aprobo", orden=o, version=1,
          huella=huella(motor_migrate, o, 1))  # fmt: skip
    r = post(e, e.mc1, f"ordenes-pago/{o}/pago-manual/cuenta",
             {"version": 1, "huella": huella(motor_migrate, o, 1).hex()})  # fmt: skip
    assert (r.status_code, r.json()["codigo"]) == (403, "sin_permiso")
    assert CUENTA not in r.text
    otra = otra_cuenta_en_el_consejo(motor_migrate, motor_app, e, e.mc1)
    con_ejecutar(motor_migrate, e, otra)
    niega(motor_migrate, e, otra, VER, "no_es_quien_aprobo", orden=o, version=1,
          huella=huella(motor_migrate, o, 1))  # fmt: skip
    niega(motor_migrate, e, otra, REGISTRAR, "no_es_quien_aprobo",
          **evidencia(motor_migrate, e, o))  # fmt: skip


def test_quien_aprobo_no_ejecuta_aunque_su_cuenta_se_desvincule(motor_migrate, motor_app, e):
    """Identidad histórica: la aprobación guardó la identidad; desvincular la cuenta con la que
    aprobó no habilita a otra cuenta del mismo ser humano."""
    o = emitida(motor_migrate, e)
    otra = otra_cuenta_en_el_consejo(motor_migrate, motor_app, e, e.mc1)
    con_ejecutar(motor_migrate, e, otra)
    desvincular(motor_migrate, e, e.mc1)
    niega(motor_migrate, e, otra, VER, "no_es_quien_aprobo", orden=o, version=1,
          huella=huella(motor_migrate, o, 1))  # fmt: skip


def test_otro_ejecutor_que_no_aprobo_si_recibe_la_cuenta(motor_migrate, motor_app, e):
    o = emitida(motor_migrate, e)
    x = otro_ejecutor(motor_migrate, motor_app, e)
    assert ver(motor_migrate, e, x, o)["cuenta_destino"] == CUENTA


def test_aprobacion_vencida_no_da_la_cuenta(motor_migrate, e):
    o = emitida(motor_migrate, e)
    envejecer(motor_migrate, o, timedelta(days=8))
    niega(motor_migrate, e, e.adm, VER, "aprobacion_vencida", orden=o, version=1,
          huella=huella(motor_migrate, o, 1))  # fmt: skip


def test_proveedor_con_otra_cuenta_no_da_la_cuenta_hasta_otra_aprobacion(motor_migrate, e):
    o = emitida(motor_migrate, e)
    nueva_cuenta(motor_migrate, e, 77)
    niega(motor_migrate, e, e.adm, VER, "proveedor_cambiado", orden=o, version=1,
          huella=huella(motor_migrate, o, 1))  # fmt: skip


def test_contrato_de_baja_no_da_la_cuenta_de_una_rutinaria(motor_migrate, e):
    previa = aprobada(motor_migrate, e)  # el proveedor pasa a conocido
    llamar(motor_migrate, e, e.adm, EMITIR, orden=previa, version=1)
    k = confirmado(motor_migrate, e)
    o = aprobada(motor_migrate, e, contrato=k)
    assert orden(motor_migrate, o)["estado"] == "aprobada"
    llamar(motor_migrate, e, e.adm, EMITIR, orden=o, version=1)
    llamar(motor_migrate, e, e.adm, BAJA_CONTRATO, contrato=k)
    niega(motor_migrate, e, e.adm, VER, "contrato_no_vigente", orden=o, version=1,
          huella=huella(motor_migrate, o, 1))  # fmt: skip


def test_la_version_y_la_huella_vistas(motor_migrate, e):
    o = emitida(motor_migrate, e)
    niega(motor_migrate, e, e.adm, VER, "version_desactualizada", orden=o, version=2,
          huella=huella(motor_migrate, o, 1))  # fmt: skip
    niega(motor_migrate, e, e.adm, VER, "huella_distinta", orden=o, version=1,
          huella=bytes(32))  # fmt: skip


def test_r10_sin_segundo_factor_reciente_no_hay_cuenta(motor_migrate, e):
    o = emitida(motor_migrate, e)
    vieja = nueva_sesion(motor_migrate, e.adm, horas_atras=1, totp_min_atras=11)
    niega(motor_migrate, e, e.adm, VER, "requiere_segundo_factor_reciente", sesion=vieja,
          orden=o, version=1, huella=huella(motor_migrate, o, 1))  # fmt: skip


def test_con_el_pago_registrado_la_cuenta_ya_no_sale(motor_migrate, e):
    o = emitida(motor_migrate, e)
    registrada(motor_migrate, e, o)
    niega(motor_migrate, e, e.adm, VER, "pago_ya_registrado", orden=o, version=1,
          huella=huella(motor_migrate, o, 1))  # fmt: skip


# La evidencia --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("cambio", "motivo"),
    [
        ({"importe_p": IMPORTE + 1}, "no_coincide_con_la_orden"),
        ({"moneda_p": "USD"}, "no_coincide_con_la_orden"),
        ({"cuenta": cbu_sintetico(2)}, "no_coincide_con_la_orden"),
        ({"numero": "12"}, "numero_operacion_invalido"),
        ({"numero": None}, "numero_operacion_invalido"),
        ({"numero": CUENTA}, "numero_operacion_invalido"),
        ({"numero": cbu_sintetico(5)}, "numero_operacion_invalido"),
    ],
)
def test_lo_pagado_tiene_que_ser_lo_aprobado(motor_migrate, e, cambio, motivo):
    o = emitida(motor_migrate, e)
    ver(motor_migrate, e, e.adm, o)
    niega(motor_migrate, e, e.adm, REGISTRAR, motivo, **evidencia(motor_migrate, e, o, **cambio))
    assert orden(motor_migrate, o)["estado"] == "en_ejecucion"
    registrada(motor_migrate, e, o)  # control


def test_la_fecha_entre_la_emision_y_el_vencimiento(motor_migrate, e):
    o = emitida(motor_migrate, e)
    ver(motor_migrate, e, e.adm, o)
    hoy = _hoy(motor_migrate, e)
    for fecha in (hoy + timedelta(days=1), hoy - timedelta(days=1), None):
        niega(motor_migrate, e, e.adm, REGISTRAR, "fecha_operacion_invalida",
              **evidencia(motor_migrate, e, o, fecha=fecha))  # fmt: skip
    registrada(motor_migrate, e, o, fecha=hoy)  # control


@pytest.mark.parametrize(
    ("estado", "categoria"),
    [
        ("en_cuarentena", "facturas_y_comprobantes"),
        ("rechazado", "facturas_y_comprobantes"),
        (None, "facturas_y_comprobantes"),
        ("disponible", "contratos_proveedores"),
    ],
)
def test_el_comprobante_paso_el_antivirus(motor_migrate, e, estado, categoria):
    o = emitida(motor_migrate, e)
    ver(motor_migrate, e, e.adm, o)
    doc = comprobante(motor_migrate, e, estado, categoria)
    niega(motor_migrate, e, e.adm, REGISTRAR, "comprobante_invalido",
          **evidencia(motor_migrate, e, o, documento=doc))  # fmt: skip
    niega(motor_migrate, e, e.adm, REGISTRAR, "comprobante_invalido",
          **evidencia(motor_migrate, e, o, documento=uuid4()))  # fmt: skip


def test_la_evidencia_es_el_archivo_analizado_y_no_cambia(motor_migrate, e):
    o = emitida(motor_migrate, e)
    doc = comprobante(motor_migrate, e)
    registrada(motor_migrate, e, o, documento=doc)
    [antes] = pagos_de(motor_migrate, o)
    with motor_migrate.begin() as c:
        c.execute(
            text("insert into nucleo.version_documento (consorcio_id, documento_id, estado, tipo,"
                 " tamano, sha256, clave_objeto, antivirus, firmas_en, subida_por, analizada_en)"
                 " values (:c, :d, 'disponible', 'application/pdf', 2, repeat('b', 64),"
                 " :clave, 'ClamAV SINTETICO', now(), :p, now())"),
            {"c": e.cons, "d": doc, "clave": f"sintetico/{doc}/2", "p": e.adm},
        )  # fmt: skip
    [despues] = pagos_de(motor_migrate, o)
    assert despues["version_documento_id"] == antes["version_documento_id"]


def test_un_comprobante_respalda_una_sola_orden(motor_migrate, e):
    o1, o2 = emitida(motor_migrate, e), emitida(motor_migrate, e)
    doc = comprobante(motor_migrate, e)
    registrada(motor_migrate, e, o1, documento=doc)
    ver(motor_migrate, e, e.adm, o2)
    niega(motor_migrate, e, e.adm, REGISTRAR, "comprobante_de_otra_orden",
          **evidencia(motor_migrate, e, o2, documento=doc))  # fmt: skip


# El número de operación ---------------------------------------------------------------------


def test_un_numero_de_operacion_respalda_una_sola_orden_para_siempre(motor_migrate, e):
    o1, o2 = emitida(motor_migrate, e), emitida(motor_migrate, e)
    p1 = registrada(motor_migrate, e, o1, numero="0001-23")
    ver(motor_migrate, e, e.adm, o2)
    # Normalizado: «000123» es el mismo número.
    niega(motor_migrate, e, e.adm, REGISTRAR, "operacion_de_otra_orden",
          **evidencia(motor_migrate, e, o2, numero="000123"))  # fmt: skip
    # Tampoco después de observar el registro de la primera.
    llamar(motor_migrate, e, e.mc2, CONCILIAR, orden=o1, pago=p1, resultado="observado",
           motivo="SINTETICO: el comprobante es de otra fecha")  # fmt: skip
    niega(motor_migrate, e, e.adm, REGISTRAR, "operacion_de_otra_orden",
          **evidencia(motor_migrate, e, o2, numero="0001 23"))  # fmt: skip
    # La misma ejecución sí lo vuelve a usar al corregir.
    p2 = registrada(motor_migrate, e, o1, numero="000123")
    assert [p["id"] for p in pagos_de(motor_migrate, o1)] == [p1, p2]
    assert pagos_de(motor_migrate, o1)[1]["corrige_a"] == p1


def test_la_base_no_admite_un_numero_atado_a_otra_ejecucion(motor_migrate, e):
    """Aunque se saltee la función: la clave foránea compuesta lo impide."""
    o1, o2 = emitida(motor_migrate, e), emitida(motor_migrate, e)
    p1 = registrada(motor_migrate, e, o1, numero="ABC12345")
    [x2] = ejecuciones(motor_migrate, o2)
    with pytest.raises(DBAPIError) as error, motor_migrate.begin() as c:
        c.execute(
            text("insert into nucleo.pago_manual (consorcio_id, ejecucion_id, numero_operacion,"
                 " fecha_operacion, importe_centavos, moneda, documento_id,"
                 " version_documento_id, registrado_por, identidad_registro)"
                 " select consorcio_id, :x, numero_operacion, fecha_operacion,"
                 " importe_centavos, moneda, documento_id, version_documento_id,"
                 " registrado_por, identidad_registro from nucleo.pago_manual where id = :p"),
            {"x": x2["id"], "p": p1},
        )  # fmt: skip
    assert error.value.orig.diag.sqlstate == "23503"


# Doble pago y concurrencia -------------------------------------------------------------------


def test_un_solo_registro_vigente_por_ejecucion(motor_migrate, e):
    o = emitida(motor_migrate, e)
    registrada(motor_migrate, e, o)
    niega(motor_migrate, e, e.adm, REGISTRAR, "pago_ya_registrado",
          **evidencia(motor_migrate, e, o))  # fmt: skip


def _en_paralelo(motor_migrate, e, primero, segundo) -> dict:
    """`primero` corre y deja su transacción abierta (con la orden bloqueada); `segundo` espera;
    al confirmar el primero, el segundo ve el estado nuevo."""
    actor1, sentencia1, kw1 = primero
    conn = motor_migrate.connect()
    tx = conn.begin()
    conn.execute(text("select set_config('app.persona_id', :p, true),"
                      " set_config('app.consorcio_id', :c, true)"),
                 {"p": str(actor1), "c": str(e.cons)})  # fmt: skip
    conn.execute(text(sentencia1), {**kw1, "actor": actor1, "origen": "api",
                                    "sesion": e.sesion[actor1]})  # fmt: skip
    resultado: dict = {}

    def correr_segundo():
        actor2, sentencia2, kw2 = segundo
        try:
            resultado["ok"] = llamar(motor_migrate, e, actor2, sentencia2, **kw2)
        except DBAPIError as error:
            resultado["motivo"] = error.orig.diag.message_hint

    hilo = threading.Thread(target=correr_segundo)
    hilo.start()
    hilo.join(1.0)
    resultado["esperaba"] = hilo.is_alive()
    tx.commit()
    conn.close()
    hilo.join(10)
    return resultado


def test_dos_registros_a_la_vez_uno_solo_gana(motor_migrate, e):
    o = emitida(motor_migrate, e)
    ver(motor_migrate, e, e.adm, o)
    r = _en_paralelo(motor_migrate, e, (e.adm, REGISTRAR, evidencia(motor_migrate, e, o)),
                     (e.adm, REGISTRAR, evidencia(motor_migrate, e, o)))  # fmt: skip
    assert r == {"esperaba": True, "motivo": "pago_ya_registrado"}
    assert len(pagos_de(motor_migrate, o)) == 1


def test_dos_conciliaciones_a_la_vez_una_sola_gana(motor_migrate, motor_app, e):
    o = emitida(motor_migrate, e)
    p = registrada(motor_migrate, e, o)
    datos = {"orden": o, "pago": p, "resultado": "conforme", "motivo": None}
    r = _en_paralelo(motor_migrate, e, (e.mc1, CONCILIAR, datos), (e.mc2, CONCILIAR, datos))
    # El segundo espera el bloqueo de la orden y la encuentra Pagada.
    assert r == {"esperaba": True, "motivo": "estado_invalido"}
    assert resultados(motor_migrate, o) == [("pagada", "manual")]


def test_pagada_no_admite_otro_registro_ni_otra_ejecucion(motor_migrate, e):
    o = emitida(motor_migrate, e)
    p = registrada(motor_migrate, e, o)
    llamar(motor_migrate, e, e.mc2, CONCILIAR, orden=o, pago=p, resultado="conforme",
           motivo=None)  # fmt: skip
    niega(motor_migrate, e, e.adm, REGISTRAR, "estado_invalido", **evidencia(motor_migrate, e, o))
    niega(motor_migrate, e, e.adm, EMITIR, "estado_invalido", orden=o, version=1)
    niega(motor_migrate, e, e.adm, DECLARAR, "estado_invalido", orden=o, version=1,
          motivo="SINTETICO")  # fmt: skip


def test_un_comprobante_a_la_vez_para_dos_ordenes_uno_solo_gana(motor_migrate, e):
    """La clave del comprobante, no una consulta previa: dos órdenes distintas (cada una con su
    bloqueo) no lo usan las dos aunque registren a la vez."""
    o1, o2 = emitida(motor_migrate, e), emitida(motor_migrate, e)
    doc = comprobante(motor_migrate, e)
    ver(motor_migrate, e, e.adm, o1)
    ver(motor_migrate, e, e.adm, o2)
    uno = (e.adm, REGISTRAR, evidencia(motor_migrate, e, o1, documento=doc))
    otro = (e.adm, REGISTRAR, evidencia(motor_migrate, e, o2, documento=doc))
    r = _en_paralelo(motor_migrate, e, uno, otro)
    assert r == {"esperaba": True, "motivo": "comprobante_de_otra_orden"}
    assert (len(pagos_de(motor_migrate, o1)), len(pagos_de(motor_migrate, o2))) == (1, 0)


def test_el_registro_vigente_es_el_ultimo_de_la_cadena_no_el_mas_nuevo(motor_migrate, e):
    """Una corrección con una marca anterior a la del registro observado (su transacción empezó
    antes) sigue siendo la vigente: la cadena de `corrige_a` manda, no el reloj."""
    o = emitida(motor_migrate, e)
    p1 = registrada(motor_migrate, e, o)
    llamar(motor_migrate, e, e.mc2, CONCILIAR, orden=o, pago=p1, resultado="observado",
           motivo="SINTETICO: el comprobante no se lee")  # fmt: skip
    with motor_migrate.begin() as c:
        p2 = c.execute(
            text("insert into nucleo.pago_manual (consorcio_id, ejecucion_id, corrige_a,"
                 " numero_operacion, fecha_operacion, importe_centavos, moneda, documento_id,"
                 " version_documento_id, registrado_por, identidad_registro, registrado_en)"
                 " select consorcio_id, ejecucion_id, id, numero_operacion, fecha_operacion,"
                 " importe_centavos, moneda, documento_id, version_documento_id,"
                 " registrado_por, identidad_registro, registrado_en - interval '1 minute'"
                 " from nucleo.pago_manual where id = :p returning id"),
            {"p": p1},
        ).scalar_one()  # fmt: skip
    niega(motor_migrate, e, e.adm, VER, "pago_ya_registrado", orden=o, version=1,
          huella=huella(motor_migrate, o, 1))  # fmt: skip
    niega(motor_migrate, e, e.adm, DECLARAR, "pago_ya_registrado", orden=o, version=1,
          motivo="SINTETICO")  # fmt: skip
    # La lectura también sigue el orden de los hechos: la corrección va última (la que mira la
    # pantalla), aunque su marca de hora sea anterior.
    for ruta, quien in (("pagos-manuales", e.adm), ("pagos-manuales/revision", e.mc1)):
        [elemento] = get(e, quien, ruta).json()["elementos"]
        assert [p["id"] for p in elemento["ejecucion"]["pagos"]] == [str(p1), str(p2)], ruta


# La conciliación -----------------------------------------------------------------------------


def test_quien_registro_no_concilia_ni_con_otra_cuenta_ni_desvinculado(motor_migrate, motor_app, e):
    o = emitida(motor_migrate, e)
    p = registrada(motor_migrate, e, o)
    datos = {"orden": o, "pago": p, "resultado": "conforme", "motivo": None}
    niega(motor_migrate, e, e.adm, CONCILIAR, "42501", **datos)  # sin aprobar_orden
    otra = otra_cuenta_en_el_consejo(motor_migrate, motor_app, e, e.adm)
    niega(motor_migrate, e, otra, CONCILIAR, "no_es_quien_ejecuto", **datos)
    r = post(e, otra, f"ordenes-pago/{o}/pago-manual/{p}/conciliacion", {"resultado": "conforme"})
    assert (r.status_code, r.json()["codigo"]) == (403, "sin_permiso")
    desvincular(motor_migrate, e, e.adm)
    # La identidad guardada al registrar sigue mandando, aunque la cuenta ya no esté vinculada.
    niega(motor_migrate, e, otra, CONCILIAR, "no_es_quien_ejecuto", **datos)
    # Y con la cuenta de quien registró sin identidad vigente, nadie concilia: falla cerrada,
    # como la emisión con un aprobador desvinculado (`exigir_separacion_humana`).
    niega(motor_migrate, e, e.mc1, CONCILIAR, "identidad_no_verificada", **datos)
    assert orden(motor_migrate, o)["estado"] == "en_verificacion"


def test_quien_aprobo_si_concilia_lo_que_registro_otro(motor_migrate, e):
    o = emitida(motor_migrate, e)
    p = registrada(motor_migrate, e, o)
    llamar(motor_migrate, e, e.mc1, CONCILIAR, orden=o, pago=p, resultado="conforme",
           motivo=None)  # fmt: skip
    assert orden(motor_migrate, o)["estado"] == "pagada"


def test_quien_preparo_no_concilia_aunque_otro_haya_ejecutado(motor_migrate, motor_app, e):
    o = emitida(motor_migrate, e)
    x = otro_ejecutor(motor_migrate, motor_app, e)
    p = registrada(motor_migrate, e, o, quien=x)
    otra = otra_cuenta_en_el_consejo(motor_migrate, motor_app, e, e.adm)
    datos = {"orden": o, "pago": p, "resultado": "conforme", "motivo": None}
    niega(motor_migrate, e, otra, CONCILIAR, "no_es_quien_preparo", **datos)
    r = post(e, otra, f"ordenes-pago/{o}/pago-manual/{p}/conciliacion", {"resultado": "conforme"})
    assert (r.status_code, r.json()["codigo"]) == (403, "sin_permiso")
    llamar(motor_migrate, e, e.mc2, CONCILIAR, **datos)  # control


def test_observado_no_paga_y_deja_la_historia(motor_migrate, e):
    o = emitida(motor_migrate, e)
    p1 = registrada(motor_migrate, e, o)
    llamar(motor_migrate, e, e.mc2, CONCILIAR, orden=o, pago=p1, resultado="observado",
           motivo="SINTETICO: el importe del comprobante no se lee")  # fmt: skip
    assert orden(motor_migrate, o)["estado"] == "en_verificacion"
    assert resultados(motor_migrate, o) == []
    niega(motor_migrate, e, e.mc1, CONCILIAR, "conciliacion_repetida", orden=o, pago=p1,
          resultado="conforme", motivo=None)  # fmt: skip
    # Se ve de nuevo la cuenta para volver a transferir si hiciera falta, y se registra otro.
    assert ver(motor_migrate, e, e.adm, o)["cuenta_destino"] == CUENTA
    p2 = registrada(motor_migrate, e, o)
    llamar(motor_migrate, e, e.mc2, CONCILIAR, orden=o, pago=p2, resultado="conforme",
           motivo=None)  # fmt: skip
    assert [(p["id"], p["conciliacion"]) for p in pagos_de(motor_migrate, o)] == [
        (p1, "observado"),
        (p2, "conforme"),
    ]
    assert orden(motor_migrate, o)["estado"] == "pagada"


@pytest.mark.parametrize(("resultado", "motivo"), [("observado", None), ("conforme", "x"),
                                                    ("otro", None)])  # fmt: skip
def test_la_conciliacion_mal_formada_no_pasa(motor_migrate, e, resultado, motivo):
    o = emitida(motor_migrate, e)
    p = registrada(motor_migrate, e, o)
    niega(motor_migrate, e, e.mc2, CONCILIAR, "conciliacion_invalida", orden=o, pago=p,
          resultado=resultado, motivo=motivo)  # fmt: skip


def test_registra_solo_quien_vio_la_cuenta_de_esa_ejecucion(motor_migrate, motor_app, e):
    """El que registra es el que transfirió: recibió la cuenta en el acto de esta ejecución."""
    o = emitida(motor_migrate, e)
    x = otro_ejecutor(motor_migrate, motor_app, e)
    ver(motor_migrate, e, e.adm, o)
    niega(motor_migrate, e, x, REGISTRAR, "no_es_quien_ejecuto", **evidencia(motor_migrate, e, o))
    # Antes de comparar la cuenta: el registro no sirve para probar cuentas sin haberla visto.
    niega(motor_migrate, e, x, REGISTRAR, "no_es_quien_ejecuto",
          **evidencia(motor_migrate, e, o, cuenta=cbu_sintetico(2)))  # fmt: skip
    # Haber visto la cuenta de otra orden no alcanza.
    otra_orden = emitida(motor_migrate, e)
    ver(motor_migrate, e, x, otra_orden)
    niega(motor_migrate, e, x, REGISTRAR, "no_es_quien_ejecuto", **evidencia(motor_migrate, e, o))
    assert orden(motor_migrate, o)["estado"] == "en_ejecucion"
    registrada(motor_migrate, e, o, quien=x)  # control: vio la cuenta y registra


def test_quien_vio_la_cuenta_no_concilia_aunque_registre_otro(motor_migrate, e):
    """mc2 (con `aprobar_orden` y además `ejecutar_pago`, sin haber aprobado) abre el acto y
    transfiere; la administración también lo abre y registra. mc2 no concilia ese pago."""
    o = emitida(motor_migrate, e)
    con_ejecutar(motor_migrate, e, e.mc2)
    ver(motor_migrate, e, e.mc2, o)
    p = registrada(motor_migrate, e, o)
    datos = {"orden": o, "pago": p, "resultado": "conforme", "motivo": None}
    niega(motor_migrate, e, e.mc2, CONCILIAR, "no_es_quien_ejecuto", **datos)
    llamar(motor_migrate, e, e.mc1, CONCILIAR, **datos)  # control: no vio la cuenta
    assert orden(motor_migrate, o)["estado"] == "pagada"


# «No se pagó» --------------------------------------------------------------------------------


def test_fallida_manual_la_declara_uno_y_la_confirma_otro(motor_migrate, motor_app, e):
    o = emitida(motor_migrate, e)
    d = llamar(motor_migrate, e, e.adm, DECLARAR, orden=o, version=1,
               motivo="SINTETICO: el banco rechazó la transferencia")  # fmt: skip
    assert orden(motor_migrate, o)["estado"] == "en_ejecucion"
    otra = otra_cuenta_en_el_consejo(motor_migrate, motor_app, e, e.adm)
    niega(motor_migrate, e, otra, CONFIRMAR, "no_es_quien_ejecuto", orden=o, declaracion=d)
    niega(motor_migrate, e, e.adm, CONFIRMAR, "42501", orden=o, declaracion=d)
    llamar(motor_migrate, e, e.mc1, CONFIRMAR, orden=o, declaracion=d)
    assert orden(motor_migrate, o)["estado"] == "fallida"
    assert resultados(motor_migrate, o) == [("fallida", "manual")]


def test_no_se_declara_ni_confirma_con_un_pago_vigente(motor_migrate, e):
    o = emitida(motor_migrate, e)
    d = llamar(motor_migrate, e, e.adm, DECLARAR, orden=o, version=1, motivo="SINTETICO")
    registrada(motor_migrate, e, o)
    niega(motor_migrate, e, e.adm, DECLARAR, "pago_ya_registrado", orden=o, version=1,
          motivo="SINTETICO")  # fmt: skip
    niega(motor_migrate, e, e.mc1, CONFIRMAR, "pago_ya_registrado", orden=o, declaracion=d)


def test_la_declaracion_queda_superada_por_un_pago_registrado_despues(motor_migrate, e):
    """Declarada «no se pagó», después se transfirió y registró, y ese registro se observó: la
    declaración vieja ya no se confirma (abriría un segundo pago). Una nueva sí."""
    o = emitida(motor_migrate, e)
    d = llamar(motor_migrate, e, e.adm, DECLARAR, orden=o, version=1, motivo="SINTETICO")
    p = registrada(motor_migrate, e, o)
    llamar(motor_migrate, e, e.mc2, CONCILIAR, orden=o, pago=p, resultado="observado",
           motivo="SINTETICO: falta el comprobante correcto")  # fmt: skip
    niega(motor_migrate, e, e.mc1, CONFIRMAR, "estado_invalido", orden=o, declaracion=d)
    assert confirmables(e, o) == {str(d): False}
    assert orden(motor_migrate, o)["estado"] == "en_verificacion"
    d2 = llamar(motor_migrate, e, e.adm, DECLARAR, orden=o, version=1,
                motivo="SINTETICO: el banco devolvió la transferencia")  # fmt: skip
    llamar(motor_migrate, e, e.mc1, CONFIRMAR, orden=o, declaracion=d2)  # control
    assert orden(motor_migrate, o)["estado"] == "fallida"


def test_superada_aunque_quien_registra_haya_visto_la_cuenta_antes_de_declarar(
    motor_migrate, motor_app, e
):
    """x recibió la cuenta, declaró «no se pagó», y después transfirió y registró: el registro
    posterior, no una apertura, deja superada la declaración."""
    o = emitida(motor_migrate, e)
    x = otro_ejecutor(motor_migrate, motor_app, e)
    ver(motor_migrate, e, x, o)
    d = llamar(motor_migrate, e, x, DECLARAR, orden=o, version=1, motivo="SINTETICO")
    p = llamar(motor_migrate, e, x, REGISTRAR, **evidencia(motor_migrate, e, o))
    llamar(motor_migrate, e, e.mc2, CONCILIAR, orden=o, pago=p, resultado="observado",
           motivo="SINTETICO: el comprobante no se lee")  # fmt: skip
    niega(motor_migrate, e, e.mc1, CONFIRMAR, "estado_invalido", orden=o, declaracion=d)
    assert orden(motor_migrate, o)["estado"] == "en_verificacion"


def test_la_declaracion_queda_superada_si_despues_alguien_vio_la_cuenta(
    motor_migrate, motor_app, e
):
    o = emitida(motor_migrate, e)
    d = llamar(motor_migrate, e, e.adm, DECLARAR, orden=o, version=1, motivo="SINTETICO")
    x = otro_ejecutor(motor_migrate, motor_app, e)
    ver(motor_migrate, e, x, o)
    niega(motor_migrate, e, e.mc1, CONFIRMAR, "estado_invalido", orden=o, declaracion=d)
    assert confirmables(e, o) == {str(d): False}
    assert orden(motor_migrate, o)["estado"] == "en_ejecucion"
    # Aunque x declare después, la vieja sigue superada: se confirma la nueva.
    d2 = llamar(motor_migrate, e, x, DECLARAR, orden=o, version=1, motivo="SINTETICO")
    niega(motor_migrate, e, e.mc1, CONFIRMAR, "estado_invalido", orden=o, declaracion=d)
    llamar(motor_migrate, e, e.mc1, CONFIRMAR, orden=o, declaracion=d2)  # control
    assert orden(motor_migrate, o)["estado"] == "fallida"


def test_quien_vio_la_cuenta_no_confirma_que_no_se_pago(motor_migrate, motor_app, e):
    o = emitida(motor_migrate, e)
    con_ejecutar(motor_migrate, e, e.mc2)
    ver(motor_migrate, e, e.mc2, o)
    x = otro_ejecutor(motor_migrate, motor_app, e)
    ver(motor_migrate, e, x, o)
    dm = llamar(motor_migrate, e, e.mc2, DECLARAR, orden=o, version=1, motivo="SINTETICO")
    dx = llamar(motor_migrate, e, x, DECLARAR, orden=o, version=1, motivo="SINTETICO")
    niega(motor_migrate, e, e.mc2, CONFIRMAR, "no_es_quien_ejecuto", orden=o, declaracion=dx)
    llamar(motor_migrate, e, e.mc1, CONFIRMAR, orden=o, declaracion=dm)  # control
    assert orden(motor_migrate, o)["estado"] == "fallida"


def test_si_alguien_vio_la_cuenta_declara_quien_la_vio(motor_migrate, motor_app, e):
    """x recibió la cuenta y pudo haber transferido: «no se pagó» no lo dicen otros dos."""
    o = emitida(motor_migrate, e)
    x = otro_ejecutor(motor_migrate, motor_app, e)
    ver(motor_migrate, e, x, o)
    niega(motor_migrate, e, e.adm, DECLARAR, "no_es_quien_ejecuto", orden=o, version=1,
          motivo="SINTETICO")  # fmt: skip
    d = llamar(motor_migrate, e, x, DECLARAR, orden=o, version=1, motivo="SINTETICO")  # control
    llamar(motor_migrate, e, e.mc1, CONFIRMAR, orden=o, declaracion=d)
    assert orden(motor_migrate, o)["estado"] == "fallida"


def test_cada_quien_vio_la_cuenta_declara_despues_de_su_ultima_apertura(
    motor_migrate, motor_app, e
):
    """La administración declara y vuelve a abrir el acto (pudo transferir después); x abre y
    declara. Falta la declaración de la administración posterior a su última apertura."""
    o = emitida(motor_migrate, e)
    x = otro_ejecutor(motor_migrate, motor_app, e)
    ver(motor_migrate, e, e.adm, o)
    llamar(motor_migrate, e, e.adm, DECLARAR, orden=o, version=1, motivo="SINTETICO")
    ver(motor_migrate, e, e.adm, o)
    ver(motor_migrate, e, x, o)
    dx = llamar(motor_migrate, e, x, DECLARAR, orden=o, version=1, motivo="SINTETICO")
    niega(motor_migrate, e, e.mc1, CONFIRMAR, "estado_invalido", orden=o, declaracion=dx)
    assert confirmables(e, o)[str(dx)] is False  # la lectura dice lo mismo que la confirmación
    da = llamar(motor_migrate, e, e.adm, DECLARAR, orden=o, version=1, motivo="SINTETICO")
    assert confirmables(e, o)[str(dx)] is True and confirmables(e, o)[str(da)] is True
    llamar(motor_migrate, e, e.mc1, CONFIRMAR, orden=o, declaracion=dx)  # control
    assert set(confirmables(e, o).values()) == {False}  # confirmada: ninguna más
    assert orden(motor_migrate, o)["estado"] == "fallida"


def test_0056_confirmable_mira_el_estado_la_version_y_el_resultado(motor_migrate, e):
    """`confirmable` leído directo de la base, como lo vería otro cliente que no filtra por
    estado (revisión de #183): cada condición de `0056` por separado, con una orden para cada
    una. Los cambios se fuerzan con el rol de migración, por las transiciones permitidas."""

    def confirmable(o, d) -> bool:
        with transaccion(motor_migrate, persona_id=e.mc1, consorcio_id=e.cons) as c:
            filas = c.execute(
                text("select id, confirmable from nucleo.declaraciones_pago_no_realizado_lectura("
                     ":o, 'egresos.aprobar_orden', :actor, 'api', :sesion)"),
                {"o": o, "actor": e.mc1, "sesion": e.sesion[e.mc1]},
            ).all()  # fmt: skip
        return {f.id: f.confirmable for f in filas}[d]

    def forzar(sql: str, o) -> None:
        with motor_migrate.begin() as c:
            c.execute(text(sql), {"o": o})

    def declarada() -> tuple[UUID, UUID]:
        o = emitida(motor_migrate, e)
        d = llamar(motor_migrate, e, e.adm, DECLARAR, orden=o, version=1, motivo="SINTETICO")
        assert confirmable(o, d) is True  # control
        return o, d

    resultado = ("insert into nucleo.resultado_ejecucion_orden_pago (consorcio_id, ejecucion_id,"
                 " resultado, origen) select consorcio_id, id, '{}', 'manual'"
                 " from nucleo.ejecucion_orden_pago where orden_id = :o")  # fmt: skip
    o, d = declarada()
    forzar(resultado.format("incierta"), o)
    assert confirmable(o, d) is True  # una incierta no cierra la ejecución
    forzar(resultado.format("fallida"), o)
    assert confirmable(o, d) is False  # un resultado definitivo sí

    o, d = declarada()
    forzar("update nucleo.orden_pago set version_vigente = version_vigente + 1 where id = :o", o)
    assert confirmable(o, d) is False  # la ejecución es de una versión vieja

    o, d = declarada()
    forzar("update nucleo.orden_pago set estado = 'en_verificacion' where id = :o", o)
    assert confirmable(o, d) is True
    forzar("update nucleo.orden_pago set estado = 'pagada' where id = :o", o)
    assert confirmable(o, d) is False  # la orden ya no espera su pago


def test_si_dos_vieron_la_cuenta_declaran_los_dos(motor_migrate, motor_app, e):
    o = emitida(motor_migrate, e)
    x = otro_ejecutor(motor_migrate, motor_app, e)
    ver(motor_migrate, e, e.adm, o)
    ver(motor_migrate, e, x, o)
    d = llamar(motor_migrate, e, e.adm, DECLARAR, orden=o, version=1, motivo="SINTETICO")
    niega(motor_migrate, e, e.mc1, CONFIRMAR, "estado_invalido", orden=o, declaracion=d)
    assert confirmables(e, o) == {str(d): False}  # la pantalla del Consejo no la ofrece
    dx = llamar(motor_migrate, e, x, DECLARAR, orden=o, version=1, motivo="SINTETICO")
    assert confirmables(e, o) == {str(d): True, str(dx): True}
    llamar(motor_migrate, e, e.mc1, CONFIRMAR, orden=o, declaracion=d)  # control
    assert orden(motor_migrate, o)["estado"] == "fallida"


def test_quien_aprobo_no_declara_que_no_se_pago(motor_migrate, e):
    """A5 también en la base, no solo en la política de la ruta."""
    o = emitida(motor_migrate, e)
    con_ejecutar(motor_migrate, e, e.mc1)
    niega(motor_migrate, e, e.mc1, DECLARAR, "no_es_quien_aprobo", orden=o, version=1,
          motivo="SINTETICO")  # fmt: skip
    llamar(motor_migrate, e, e.adm, DECLARAR, orden=o, version=1, motivo="SINTETICO")  # control


# Riel y manual, nunca las dos ----------------------------------------------------------------


def test_una_ejecucion_manual_no_encola_el_riel_y_el_riel_no_la_toma(motor_migrate, motor_app, e):
    o = emitida(motor_migrate, e)
    [x] = ejecuciones(motor_migrate, o)
    assert x["modalidad"] == "manual"
    with motor_migrate.connect() as c:
        encolados = c.execute(
            text("select count(*) from plataforma.jobs where consorcio_id = :c"
                 " and carga->>'ejecucion' = :x"), {"c": e.cons, "x": str(x["id"])},
        ).scalar_one()  # fmt: skip
    assert encolados == 0
    with (
        pytest.raises(DBAPIError) as error,
        transaccion(motor_app, persona_id=None, consorcio_id=e.cons) as c,
    ):
        c.execute(text("select * from nucleo.pedido_de_pago(:x)"), {"x": x["id"]})
    assert error.value.orig.diag.message_hint == "modalidad_manual"
    with (
        pytest.raises(DBAPIError) as error,
        transaccion(motor_app, persona_id=None, consorcio_id=e.cons) as c,
    ):
        c.execute(text("select nucleo.registrar_resultado_pago(:x, 'pagada', 'ordenar', 'SIM-1',"
                       " null)"), {"x": x["id"]})  # fmt: skip
    assert error.value.orig.diag.message_hint == "modalidad_manual"


def test_una_ejecucion_del_riel_no_se_registra_a_mano(motor_migrate, motor_app, e):
    habilitar_riel(motor_migrate, e.cons)
    o = emitida(motor_migrate, e)
    [x] = ejecuciones(motor_migrate, o)
    assert x["modalidad"] == "riel"
    assert correr(motor_migrate, motor_app, pagos.TRABAJO_ORDENAR, e.cons) == [
        {"estado": "sin_riel"}
    ]
    niega(motor_migrate, e, e.adm, REGISTRAR, "modalidad_riel", **evidencia(motor_migrate, e, o))
    niega(motor_migrate, e, e.adm, VER, "modalidad_riel", orden=o, version=1,
          huella=huella(motor_migrate, o, 1))  # fmt: skip
    niega(motor_migrate, e, e.adm, DECLARAR, "modalidad_riel", orden=o, version=1,
          motivo="SINTETICO")  # fmt: skip


# Otro consorcio (punto 26) -------------------------------------------------------------------


def test_otro_consorcio_no_lee_ni_registra_ni_concilia(motor_migrate, motor_app, e):
    o = emitida(motor_migrate, e)
    p = registrada(motor_migrate, e, o)
    b = escena_economica(motor_migrate, motor_app)
    # Por la base, con el ámbito de B: la orden de A no existe.
    for quien, sentencia, datos in (
        (b.adm, VER, {"orden": o, "version": 1, "huella": huella(motor_migrate, o, 1)}),
        (b.adm, REGISTRAR, evidencia(motor_migrate, e, o)),
        (b.mc2, CONCILIAR, {"orden": o, "pago": p, "resultado": "conforme", "motivo": None}),
        (b.adm, DECLARAR, {"orden": o, "version": 1, "motivo": "SINTETICO"}),
    ):
        niega(motor_migrate, b, quien, sentencia, "no_encontrado", **datos)
    with transaccion(motor_migrate, persona_id=b.adm, consorcio_id=b.cons) as c:
        for lectura in ("pagos_manuales_lectura", "ejecuciones_manuales_lectura",
                        "declaraciones_pago_no_realizado_lectura"):  # fmt: skip
            filas = c.execute(
                text(f"select * from nucleo.{lectura}(null, 'egresos.preparar_orden', :a, 'api',"
                     " :s)"), {"a": b.adm, "s": b.sesion[b.adm]},
            ).all()  # fmt: skip
            assert filas == [], lectura
    # Por HTTP, contra las rutas de A: sin mandato en A.
    a = f"/api/v1/consorcios/{e.cons}"
    with cliente(b.adm, b.sesion[b.adm]) as c:
        assert codigo(c.get(f"{a}/pagos-manuales")) == (404, "no_encontrado")
        r = c.post(f"{a}/ordenes-pago/{o}/pago-manual/cuenta",
                   json={"version": 1, "huella": huella(motor_migrate, o, 1).hex()})  # fmt: skip
        assert codigo(r) == (404, "no_encontrado") and CUENTA not in r.text
    with cliente(b.mc2, b.sesion[b.mc2]) as c:
        assert codigo(c.get(f"{a}/pagos-manuales/revision")) == (404, "no_encontrado")
        r = c.post(f"{a}/ordenes-pago/{o}/pago-manual/{p}/conciliacion",
                   json={"resultado": "conforme"}, headers=clave_idempotencia())  # fmt: skip
        assert codigo(r) == (404, "no_encontrado")
    assert orden(motor_migrate, o)["estado"] == "en_verificacion"


# Historia, privilegios y downgrade -----------------------------------------------------------


def test_la_historia_del_pago_manual_solo_se_agrega(motor_migrate, e):
    o = emitida(motor_migrate, e)
    p = registrada(motor_migrate, e, o)
    llamar(motor_migrate, e, e.mc2, CONCILIAR, orden=o, pago=p, resultado="observado",
           motivo="SINTETICO")  # fmt: skip
    for sentencia in (
        "update nucleo.pago_manual set numero_operacion = numero_operacion",
        "delete from nucleo.pago_manual",
        "delete from nucleo.conciliacion_pago_manual",
        "delete from nucleo.operacion_pago_manual",
        "truncate nucleo.operacion_pago_manual cascade",
    ):
        with pytest.raises(DBAPIError) as error, motor_migrate.begin() as c:
            c.execute(text(sentencia))
        assert error.value.orig.diag.message_hint == "historia_inmutable", sentencia


def test_cem_app_solo_ejecuta_las_rutas_del_pago_manual(motor_migrate):
    tablas = ("nucleo.pago_manual", "nucleo.operacion_pago_manual",
              "nucleo.conciliacion_pago_manual", "nucleo.declaracion_pago_no_realizado",
              "nucleo.confirmacion_pago_no_realizado", "nucleo.apertura_cuenta_pago_manual",
              "nucleo.comprobante_pago_manual", "nucleo.riel_de_pago_habilitado")  # fmt: skip
    rutas = {"cuenta_para_pago_manual", "registrar_pago_manual", "conciliar_pago_manual",
             "declarar_pago_no_realizado", "confirmar_pago_no_realizado",
             "ejecuciones_manuales_lectura", "pagos_manuales_lectura",
             "declaraciones_pago_no_realizado_lectura"}  # fmt: skip
    internas = {"ejecucion_manual_abierta", "ultimo_pago_manual", "pago_manual_observado",
                "exigir_sin_pago_vigente", "exigir_no_aprobo", "exigir_no_preparo",
                "orden_para_pago_manual", "exigir_no_ejecuto"}  # fmt: skip
    with motor_migrate.connect() as c:
        privilegios = [
            (t, p) for t in tablas
            for p in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")
            if c.execute(text("select has_table_privilege('cem_app', :t, :p)"),
                         {"t": t, "p": p}).scalar_one()
        ]  # fmt: skip
        funciones = dict(c.execute(
            text("select p.proname, has_function_privilege('cem_app', p.oid, 'EXECUTE')"
                 " from pg_proc p join pg_namespace n on n.oid = p.pronamespace"
                 " where n.nspname = 'nucleo' and p.proname = any(:n)"),
            {"n": list(rutas | internas)},
        ).all())  # fmt: skip
        # La secuencia que ordena los hechos: sin USAGE ni UPDATE (`setval` la haría retroceder).
        secuencia = [p for p in ("USAGE", "SELECT", "UPDATE") if c.execute(
            text("select has_sequence_privilege('cem_app', 'nucleo.paso_pago_manual', :p)"),
            {"p": p}).scalar_one()]  # fmt: skip
    assert privilegios == []
    assert secuencia == []
    assert set(funciones) == rutas | internas  # control: la consulta las encuentra
    assert {f for f, puede in funciones.items() if puede} == rutas


def _tablas_0055(motor) -> int:
    with motor.connect() as c:
        return c.execute(
            text("select count(*) from pg_tables where schemaname = 'nucleo' and tablename in"
                 " ('pago_manual', 'operacion_pago_manual', 'conciliacion_pago_manual',"
                 " 'declaracion_pago_no_realizado', 'confirmacion_pago_no_realizado',"
                 " 'riel_de_pago_habilitado')")
        ).scalar_one()  # fmt: skip


def test_0055_ida_y_vuelta(base_aparte):
    cfg, motor = base_aparte
    command.upgrade(cfg, "0055")
    assert _tablas_0055(motor) == 6
    command.downgrade(cfg, "0054")
    assert _tablas_0055(motor) == 0
    with motor.connect() as c:
        crear = c.execute(
            text("select pg_get_functiondef('nucleo.crear_ejecucion_orden_pago(uuid, uuid,"
                 " integer)'::regprocedure)")
        ).scalar_one()  # fmt: skip
        columnas = c.execute(
            text("select count(*) from information_schema.columns where table_schema = 'nucleo'"
                 " and table_name = 'ejecucion_orden_pago' and column_name = 'modalidad'")
        ).scalar_one()  # fmt: skip
    assert "riel_de_pago_habilitado" not in crear and columnas == 0
    command.upgrade(cfg, "0055")  # control: vuelve a subir limpio
    assert _tablas_0055(motor) == 6


def test_0056_ida_y_vuelta(base_aparte):
    """0056: `confirmable` mira el estado de la orden; baja y sube limpio."""
    cfg, motor = base_aparte

    def lectura() -> str:
        with motor.connect() as c:
            return c.execute(
                text("select pg_get_functiondef('nucleo.declaraciones_pago_no_realizado_lectura("
                     "uuid, text, uuid, text, uuid)'::regprocedure)")
            ).scalar_one()  # fmt: skip

    command.upgrade(cfg, "0056")
    assert "resultado <> 'incierta'" in lectura()
    command.downgrade(cfg, "0055")
    assert "resultado <> 'incierta'" not in lectura()
    command.upgrade(cfg, "0056")  # control
    assert "resultado <> 'incierta'" in lectura()


def test_0055_no_baja_con_el_riel_habilitado(base_aparte):
    """La guarda real del downgrade, sobre una base aparte: bajar encolaría el riel en todos los
    consorcios y perdería a cuál se le habilitó."""
    cfg, motor = base_aparte
    command.upgrade(cfg, "0055")
    with motor.begin() as c:
        habilitado = c.execute(
            text("insert into nucleo.consorcio (nombre, zona_horaria) values"
                 " ('SINTETICO 0055', 'America/Argentina/Buenos_Aires') returning id")
        ).scalar_one()  # fmt: skip
        c.execute(text("insert into nucleo.riel_de_pago_habilitado (consorcio_id) values (:c)"),
                  {"c": habilitado})  # fmt: skip
    with pytest.raises(DBAPIError) as error:
        command.downgrade(cfg, "0054")
    assert error.value.orig.diag.message_hint == "downgrade_con_datos"
    assert _tablas_0055(motor) == 6


def _migracion_0055():
    ruta = Path(__file__).parents[1] / "migrations" / "versions" / "0055_pago_manual.py"
    spec = importlib.util.spec_from_file_location("migracion_0055", ruta)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def test_la_guarda_del_downgrade_se_niega_con_una_ejecucion_manual(motor_migrate, e):
    """La guarda de `0055` tal cual, en una transacción que se deshace. Que pase sin datos lo
    prueba `test_0055_ida_y_vuelta`; esta base compartida puede tener datos de otras pruebas."""
    guarda = _migracion_0055().GUARDA_DOWNGRADE
    o = emitida(motor_migrate, e)
    with motor_migrate.connect() as c, c.begin() as tx:
        with pytest.raises(DBAPIError) as error, c.begin_nested():
            c.execute(text(guarda))
        assert error.value.orig.diag.message_hint == "downgrade_con_datos"
        tx.rollback()
    assert orden(motor_migrate, o)["estado"] == "en_ejecucion"
