"""Subir la factura que falta a una orden ya aprobada o pagada (`0099`; decisión del propietario del
13-11-2026; informe de fricción, AC-44): se adjunta con constancia (quién, cuándo), sin versión
nueva y sin volver a aprobación.

Contra servicios reales: PostgreSQL, el almacén S3 de pruebas y ClamAV. Cada negación lleva su
control: el mismo pedido de quien sí puede, o sobre una orden que sí lo admite, pasa. Datos
SINTETICOS / PRUEBA / NO REAL: ninguna cuenta es de un banco y no se mueve dinero.
"""

from datetime import timedelta
from uuid import UUID

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from scripts import documento_soporte
from tests.conftest import clave_idempotencia, url_rol
from tests.test_documentos_2c1 import (  # noqa: F401  (`bucket` y `firmas_de_la_imagen` son fixtures)
    analizar,
    bucket,
    firmas_de_la_imagen,
    pdf,
)
from tests.test_egresos_1a import RENOVAR, aprobada, envejecer, enviada
from tests.test_modo_operacion import hoy_de
from tests.test_orden_pago_neutra import (
    eventos,
    huella,
    llamar,
    niega,
    orden,
    versiones,
)
from tests.test_pago_manual_0055 import EMITIR
from tests.test_panel_del_consejo_0094 import aprobada_con_factura, panel
from tests.test_quitar_retirar_0089 import retirar
from tests.test_rutas_egresos import cliente, codigo, get
from tests.test_rutas_egresos import e as e  # noqa: F401  (es un fixture)
from tests.test_vinculo_0014 import base_aparte  # noqa: F401  (es un fixture)

pytestmark = [pytest.mark.db, pytest.mark.redis]
# Los fixtures importados no se redefinen con el parámetro de su nombre.
# ruff: noqa: F811

TITULO = "Factura SINTETICA 0001 (NO REAL)"
ACCION = "consorcio.orden_pago.adjuntar_factura"
TABLAS_DE_LA_ORDEN = (
    "nucleo.orden_pago",
    "nucleo.orden_pago_version",
    "nucleo.orden_pago_aprobacion",
    "nucleo.ejecucion_orden_pago",
)
"""Lo que es la orden: sus datos, sus versiones (con la huella y la factura de cada una), sus
aprobaciones y sus ejecuciones. El acto no toca ninguna."""


def subir(e, o, version: int = 1, quien: UUID | None = None, archivo: bytes | None = None,
          titulo: str = TITULO, clave: dict | None = None,
          reemplaza: str | None = None):  # fmt: skip
    quien = quien or e.adm
    params = {"version_esperada": version, "titulo": titulo}
    if reemplaza is not None:
        params["reemplaza"] = reemplaza
    with cliente(quien, e.sesion[quien]) as c:
        return c.post(
            f"/api/v1/consorcios/{e.cons}/ordenes-pago/{o}/factura",
            params=params,
            content=archivo or pdf(),
            headers={"Content-Type": "application/pdf"} | (clave or clave_idempotencia()),
        )


def vaciar_cola(motor_app) -> int:
    """Corre la cola hasta que no quede nada: un rechazo encola además el borrado del archivo."""
    hechos = 0
    while lote := analizar(motor_app):
        hechos += lote
    return hechos


def ficha(e, o, quien: UUID | None = None) -> dict:
    r = get(e, quien or e.adm, f"pagos/{o}")
    assert r.status_code == 200, r.text
    return r.json()


def faltantes(e, quien: UUID | None = None) -> dict[str, dict]:
    r = panel(e, quien or e.mc1)
    assert r.status_code == 200, r.text
    return {f["orden_id"]: f for f in r.json()["comprobantes_faltantes"]}


def foto(motor_migrate, o) -> dict[str, list[dict]]:
    """Cada fila de la orden en cada tabla, entera."""
    with motor_migrate.connect() as c:
        return {
            t: [dict(f) for f in c.execute(
                text(f"select * from {t} where {'id' if t == 'nucleo.orden_pago' else 'orden_id'}"
                     " = :o order by 1, 2, 3"), {"o": o},
            ).mappings()]
            for t in TABLAS_DE_LA_ORDEN
        }  # fmt: skip


def constancias(motor_migrate, o) -> list[dict]:
    with motor_migrate.connect() as c:
        return [dict(f) for f in c.execute(
            text("select * from nucleo.factura_adjunta_orden_pago where orden_id = :o"
                 " order by adjuntada_en, id"), {"o": o},
        ).mappings()]  # fmt: skip


# El acto ---------------------------------------------------------------------------------------


def test_subir_la_factura_a_una_aprobada_la_saca_de_faltantes(motor_migrate, motor_app, e, bucket):
    o = aprobada(motor_migrate, e)
    otra = aprobada(motor_migrate, e)
    antes = faltantes(e)
    assert antes[str(o)]["version"] == 1 and antes[str(o)]["factura_subida"] is None
    f = ficha(e, o)
    assert (f["puede_subir_factura"], f["factura_adjunta"]) == (True, None)

    r = subir(e, o)
    assert r.status_code == 201, r.text
    cuerpo = r.json()
    assert (cuerpo["orden_id"], cuerpo["version"]) == (str(o), 1)
    assert cuerpo["archivo"]["estado"] == "en_cuarentena"

    # Mientras el antivirus la revisa, sigue faltando y lo dice; no se ofrece ni se acepta otra.
    assert faltantes(e)[str(o)]["factura_subida"] == "en_cuarentena"
    f = ficha(e, o)
    assert f["puede_subir_factura"] is False
    assert f["factura_adjunta"]["estado"] == "en_cuarentena"
    assert codigo(subir(e, o)) == (409, "factura_ya_adjunta")

    assert analizar(motor_app) == 1
    despues = faltantes(e)
    assert str(o) not in despues
    assert str(otra) in despues  # control: la otra, sin factura, sigue faltando
    f = ficha(e, o)
    adjunta = f["factura_adjunta"]
    assert (adjunta["estado"], adjunta["version"]) == ("disponible", 1)
    assert (adjunta["documento_id"], adjunta["factura_version_id"]) == (
        cuerpo["documento_id"], cuerpo["archivo"]["id"])  # fmt: skip
    assert adjunta["adjuntada_por"] == str(e.adm) and adjunta["adjuntada_por_nombre"]
    # Una orden que ya tiene factura no ofrece subir otra, y la base no la acepta.
    assert f["puede_subir_factura"] is False
    assert codigo(subir(e, o)) == (409, "factura_ya_adjunta")
    # El Consejo la ve en la ficha, con quién y cuándo.
    assert ficha(e, o, e.mc1)["factura_adjunta"] == adjunta


def test_la_constancia_y_el_evento_dicen_quien_y_cuando(motor_migrate, motor_app, e, bucket):
    o = aprobada(motor_migrate, e)
    r = subir(e, o)
    assert r.status_code == 201, r.text
    [constancia] = constancias(motor_migrate, o)
    assert (constancia["version"], constancia["adjuntada_por"]) == (1, e.adm)
    assert str(constancia["factura_version_id"]) == r.json()["archivo"]["id"]
    assert constancia["identidad_adjunto"] is not None and constancia["adjuntada_en"] is not None
    [evento] = [x for x in eventos(motor_migrate, o) if x["accion"] == ACCION]
    assert evento["actor_persona_id"] == e.adm
    assert evento["despues"] == {"version": 1, "documento_id": r.json()["documento_id"],
                                 "factura_version_id": r.json()["archivo"]["id"]}  # fmt: skip
    # Sin `estado`: la exportación no la cuenta como una transición de la orden.
    assert "estado" not in evento["despues"]
    # La constancia no se cambia ni se borra.
    for sql in ("update nucleo.factura_adjunta_orden_pago set version = 1 where orden_id = :o",
                "delete from nucleo.factura_adjunta_orden_pago where orden_id = :o"):  # fmt: skip
        with pytest.raises(DBAPIError), motor_migrate.begin() as c:
            c.execute(text(sql), {"o": o})
    assert len(constancias(motor_migrate, o)) == 1


def test_el_acto_no_cambia_nada_de_la_orden_ademas_del_archivo(motor_migrate, motor_app, e,
                                                               bucket):  # fmt: skip
    """Ni el estado, ni la versión vigente, ni `actualizada_en`, ni ninguna columna de la versión
    (la huella, la factura), ni las aprobaciones, ni las ejecuciones: fila por fila, enteras."""
    o = aprobada(motor_migrate, e)
    antes = foto(motor_migrate, o)
    # Control del instrumento: la foto ve un cambio en cualquier columna de la orden.
    with motor_migrate.begin() as c:
        c.execute(text("update nucleo.orden_pago set actualizada_en = actualizada_en"
                       " + interval '1 microsecond' where id = :o"), {"o": o})  # fmt: skip
    assert foto(motor_migrate, o) != antes
    antes = foto(motor_migrate, o)

    assert subir(e, o).status_code == 201
    assert analizar(motor_app) == 1
    assert foto(motor_migrate, o) == antes
    [v] = antes["nucleo.orden_pago_version"]
    assert v["factura_version_id"] is None and v["documento_id"] is None


def test_no_crea_version_ni_vuelve_a_aprobacion(motor_migrate, motor_app, e, bucket):
    o = aprobada(motor_migrate, e)
    h = huella(motor_migrate, o, 1)
    assert subir(e, o).status_code == 201
    assert analizar(motor_app) == 1
    assert len(versiones(motor_migrate, o)) == 1
    o_despues = orden(motor_migrate, o)
    assert (o_despues["estado"], o_despues["version_vigente"]) == ("aprobada", 1)
    assert huella(motor_migrate, o, 1) == h
    # La aprobación sigue valiendo: se emite la versión 1 sin aprobar de nuevo.
    llamar(motor_migrate, e, e.adm, EMITIR, orden=o, version=1)
    assert orden(motor_migrate, o)["estado"] == "en_ejecucion"
    # Una vez emitida, la factura sigue ahí y la orden no falta.
    assert str(o) not in faltantes(e)


def test_si_el_antivirus_la_rechaza_lo_dice_y_se_sube_otra(motor_migrate, motor_app, e, bucket):
    o = aprobada(motor_migrate, e)
    assert subir(e, o, archivo=pdf(js="app.alert(1)")).status_code == 201
    assert vaciar_cola(motor_app) >= 1
    assert faltantes(e)[str(o)]["factura_subida"] == "rechazado"
    f = ficha(e, o)
    assert (f["factura_adjunta"]["estado"], f["puede_subir_factura"]) == ("rechazado", True)

    r = subir(e, o)
    assert r.status_code == 201, r.text
    assert faltantes(e)[str(o)]["factura_subida"] == "en_cuarentena"
    assert vaciar_cola(motor_app) == 1
    assert str(o) not in faltantes(e)
    assert ficha(e, o)["factura_adjunta"]["factura_version_id"] == r.json()["archivo"]["id"]
    # Las dos constancias quedan: nada se borra.
    assert len(constancias(motor_migrate, o)) == 2


def test_tambien_a_una_pagada_o_emitida(motor_migrate, motor_app, e, bucket):
    o = aprobada(motor_migrate, e)
    llamar(motor_migrate, e, e.adm, EMITIR, orden=o, version=1)
    assert orden(motor_migrate, o)["estado"] == "en_ejecucion"
    assert ficha(e, o)["puede_subir_factura"] is True
    assert subir(e, o).status_code == 201


# Quién y cuándo --------------------------------------------------------------------------------


def test_quien_no_prepara_no_la_sube(motor_migrate, e, bucket):
    o = aprobada(motor_migrate, e)
    assert ficha(e, o, e.mc1)["puede_subir_factura"] is False
    r = subir(e, o, quien=e.mc1)
    assert r.status_code == 403, r.text
    assert constancias(motor_migrate, o) == []
    assert subir(e, o).status_code == 201  # control: la administración sí


@pytest.mark.parametrize("estado", ["borrador", "en_revision"])
def test_una_orden_sin_aprobar_no_la_admite(motor_migrate, e, bucket, estado):
    if estado == "borrador":
        o = llamar(motor_migrate, e, e.adm, "select nucleo.preparar_orden_pago(:importe, :moneda,"
                   " :concepto, :proveedor, :comprobante, :reserva, :contrato, :actor, :origen,"
                   " :sesion)")  # fmt: skip
    else:
        o = enviada(motor_migrate, e)
    assert orden(motor_migrate, o)["estado"] == estado
    assert ficha(e, o)["puede_subir_factura"] is False
    assert codigo(subir(e, o)) == (409, "orden_no_admite")
    assert constancias(motor_migrate, o) == []
    assert subir(e, aprobada(motor_migrate, e)).status_code == 201  # control


def test_con_otra_version_a_la_vista_se_niega(motor_migrate, e, bucket):
    o = aprobada(motor_migrate, e)
    assert codigo(subir(e, o, version=2)) == (409, "version_desactualizada")
    assert subir(e, o, version=1).status_code == 201  # control


def test_una_orden_con_factura_no_la_ofrece_ni_la_acepta(motor_migrate, e, bucket):
    o = aprobada_con_factura(motor_migrate, e)
    assert ficha(e, o)["puede_subir_factura"] is False
    assert codigo(subir(e, o)) == (409, "factura_ya_adjunta")
    assert constancias(motor_migrate, o) == []
    assert subir(e, aprobada(motor_migrate, e)).status_code == 201  # control


def test_reintentar_con_la_misma_clave_no_adjunta_dos_veces(motor_migrate, e, bucket):
    o = aprobada(motor_migrate, e)
    clave = clave_idempotencia()
    primera = subir(e, o, clave=clave)
    segunda = subir(e, o, clave=clave)
    assert (primera.status_code, segunda.status_code) == (201, 201)
    assert segunda.json() == primera.json()
    assert segunda.headers.get("Idempotent-Replayed") == "true"
    assert len(constancias(motor_migrate, o)) == 1


def test_un_titulo_con_un_correo_se_niega(motor_migrate, e, bucket):
    o = aprobada(motor_migrate, e)
    r = subir(e, o, titulo="Factura de pagos@ejemplo.com")
    assert r.status_code == 422, r.text
    assert constancias(motor_migrate, o) == []
    assert subir(e, o).status_code == 201  # control


def test_el_documento_adjunto_ya_no_se_retira(motor_migrate, motor_app, e, bucket):
    o = aprobada(motor_migrate, e)
    r = subir(e, o)
    assert analizar(motor_app) == 1
    assert codigo(retirar(e, r.json()["documento_id"])) == (409, "documento_referenciado")


# Reemplazar la factura adjunta (decisión del propietario del 13-11-2026) -------------------------

ACCION_REEMPLAZO = "consorcio.orden_pago.reemplazar_factura"


def adjunta_que_vale(motor_app, e, o) -> dict:
    """Sube la factura a una orden aprobada y la pasa por el antivirus: la adjunta que vale."""
    assert subir(e, o).status_code == 201
    assert analizar(motor_app) == 1
    adjunta = ficha(e, o)["factura_adjunta"]
    assert adjunta["estado"] == "disponible"
    return adjunta


def test_reemplazar_deja_la_anterior_como_reemplazada(motor_migrate, motor_app, e, bucket):
    o = aprobada(motor_migrate, e)
    anterior = adjunta_que_vale(motor_app, e, o)
    f = ficha(e, o)
    assert (f["puede_reemplazar_factura"], f["puede_subir_factura"]) == (True, False)
    assert (f["factura_reemplazo"], f["facturas_anteriores"]) == (None, [])
    antes = foto(motor_migrate, o)

    r = subir(e, o, titulo="Factura SINTETICA 0002 (NO REAL)", reemplaza=anterior["id"])
    assert r.status_code == 201, r.text
    # Mientras el antivirus revisa la nueva, vale la anterior y no se acepta otro reemplazo.
    f = ficha(e, o)
    assert f["factura_adjunta"] == anterior
    assert f["factura_reemplazo"]["estado"] == "en_cuarentena"
    assert f["puede_reemplazar_factura"] is False
    assert codigo(subir(e, o, reemplaza=anterior["id"])) == (409, "factura_ya_adjunta")
    assert str(o) not in faltantes(e)

    assert analizar(motor_app) == 1
    f = ficha(e, o)
    nueva = f["factura_adjunta"]
    assert (nueva["factura_version_id"], nueva["reemplaza_a"]) == (
        r.json()["archivo"]["id"], anterior["id"])  # fmt: skip
    assert f["facturas_anteriores"] == [anterior]
    assert f["factura_reemplazo"] is None and f["puede_reemplazar_factura"] is True
    # El Consejo ve lo mismo: la que vale y la reemplazada.
    consejo = ficha(e, o, e.mc1)
    assert (consejo["factura_adjunta"], consejo["facturas_anteriores"]) == (nueva, [anterior])
    # Nada se borra: las dos constancias quedan, y el documento anterior no se retira.
    assert len(constancias(motor_migrate, o)) == 2
    assert codigo(retirar(e, anterior["documento_id"])) == (409, "documento_referenciado")
    # Ninguna otra cosa de la orden cambia: ni el estado, ni la versión, ni la huella.
    assert foto(motor_migrate, o) == antes
    with motor_migrate.connect() as c:
        [evento] = c.execute(
            text("select antes, despues from auditoria.eventos where entidad = 'orden_pago'"
                 " and entidad_id = :o and accion = :a"), {"o": o, "a": ACCION_REEMPLAZO},
        ).mappings().all()  # fmt: skip
    assert evento["antes"] == {"version": 1, "documento_id": anterior["documento_id"],
                               "factura_version_id": anterior["factura_version_id"]}  # fmt: skip
    assert evento["despues"] == {"version": 1, "documento_id": nueva["documento_id"],
                                 "factura_version_id": nueva["factura_version_id"]}  # fmt: skip


def test_renovar_lleva_la_que_reemplazo(motor_migrate, motor_app, e, bucket):
    o = aprobada(motor_migrate, e)
    anterior = adjunta_que_vale(motor_app, e, o)
    r = subir(e, o, reemplaza=anterior["id"])
    assert analizar(motor_app) == 1
    envejecer(motor_migrate, o, timedelta(days=8))
    assert llamar(motor_migrate, e, e.adm, RENOVAR, orden=o, version=1) == 2
    v2 = versiones(motor_migrate, o)[1]
    assert str(v2["factura_version_id"]) == r.json()["archivo"]["id"]
    assert str(v2["factura_version_id"]) != anterior["factura_version_id"]  # control


def test_si_el_antivirus_rechaza_el_reemplazo_sigue_valiendo_la_anterior(motor_migrate, motor_app,
                                                                         e, bucket):  # fmt: skip
    o = aprobada(motor_migrate, e)
    anterior = adjunta_que_vale(motor_app, e, o)
    r = subir(e, o, archivo=pdf(js="app.alert(1)"), reemplaza=anterior["id"])
    assert r.status_code == 201, r.text
    assert vaciar_cola(motor_app) >= 1
    f = ficha(e, o)
    assert f["factura_adjunta"] == anterior
    assert f["factura_reemplazo"]["estado"] == "rechazado"
    assert (f["facturas_anteriores"], f["puede_reemplazar_factura"]) == ([], True)
    assert str(o) not in faltantes(e)
    # Se reemplaza de nuevo, contra la misma que sigue valiendo.
    assert subir(e, o, reemplaza=anterior["id"]).status_code == 201


def test_reemplazar_una_que_no_vale_se_niega(motor_migrate, motor_app, e, bucket):
    o = aprobada(motor_migrate, e)
    anterior = adjunta_que_vale(motor_app, e, o)
    assert subir(e, o, reemplaza=anterior["id"]).status_code == 201
    assert analizar(motor_app) == 1
    # La anterior ya quedó reemplazada: reemplazarla otra vez es mirar una ficha vieja.
    assert codigo(subir(e, o, reemplaza=anterior["id"])) == (409, "factura_no_vigente")
    assert len(constancias(motor_migrate, o)) == 2
    # Ni en una orden sin adjunta, ni en una cuya factura es la de la versión.
    sin = aprobada(motor_migrate, e)
    assert ficha(e, sin)["puede_reemplazar_factura"] is False
    assert codigo(subir(e, sin, reemplaza=anterior["id"])) == (409, "factura_no_vigente")
    con = aprobada_con_factura(motor_migrate, e)
    assert ficha(e, con)["puede_reemplazar_factura"] is False
    assert codigo(subir(e, con, reemplaza=anterior["id"])) == (409, "factura_no_vigente")
    # Control: contra la que vale, pasa.
    assert subir(e, o, reemplaza=ficha(e, o)["factura_adjunta"]["id"]).status_code == 201


def test_quien_no_prepara_no_la_reemplaza(motor_migrate, motor_app, e, bucket):
    o = aprobada(motor_migrate, e)
    anterior = adjunta_que_vale(motor_app, e, o)
    assert ficha(e, o, e.mc1)["puede_reemplazar_factura"] is False
    r = subir(e, o, quien=e.mc1, reemplaza=anterior["id"])
    assert r.status_code == 403, r.text
    assert len(constancias(motor_migrate, o)) == 1
    assert subir(e, o, reemplaza=anterior["id"]).status_code == 201  # control: la administración


# Soporte borra el documento de una adjunta (`scripts.documento_soporte`) ------------------------


@pytest.fixture
def soporte(monkeypatch):
    monkeypatch.setenv(documento_soporte.VARIABLE_URL, url_rol("soporte_documentos"))


def borrar_por_soporte(motor_app, e, documento_id: str) -> None:
    """Lo que hace soporte a pedido de la administración: el documento queda borrado (lógico)."""
    assert documento_soporte.main([
        "borrar", "--consorcio", str(e.cons), "--documento", str(documento_id),
        "--solicitante", str(e.adm), "--fecha-pedido", hoy_de(motor_app, e.cons).isoformat(),
        "--referencia", "Nota SINTETICA 1/2026", "--motivo", "Cargado por error",
    ]) == 0  # fmt: skip


def test_borrada_por_soporte_la_adjunta_vuelve_a_faltar_y_se_sube_otra(
    motor_migrate, motor_app, e, bucket, soporte
):
    o = aprobada(motor_migrate, e)
    adjunta = adjunta_que_vale(motor_app, e, o)
    assert str(o) not in faltantes(e)  # control: con la adjunta, no falta
    borrar_por_soporte(motor_app, e, adjunta["documento_id"])

    # Una sola regla, la de la base: ya no vale; la ficha ofrece subir otra y no reemplazarla.
    f = ficha(e, o)
    assert (f["factura_adjunta"], f["factura_reemplazo"]) == (None, None)
    assert (f["puede_subir_factura"], f["puede_reemplazar_factura"]) == (True, False)
    [historia] = f["facturas_anteriores"]
    assert (historia["id"], historia["documento_vigente"]) == (adjunta["id"], False)
    assert faltantes(e)[str(o)]["factura_subida"] is None
    assert codigo(subir(e, o, reemplaza=adjunta["id"])) == (409, "factura_no_vigente")
    # Lo que la ficha ofrece, el acto lo acepta.
    r = subir(e, o)
    assert r.status_code == 201, r.text
    assert analizar(motor_app) == 1
    assert ficha(e, o)["factura_adjunta"]["factura_version_id"] == r.json()["archivo"]["id"]
    assert len(constancias(motor_migrate, o)) == 2  # la borrada queda como historia


def test_borrado_el_reemplazo_vale_la_anterior_en_la_ficha_y_al_renovar(
    motor_migrate, motor_app, e, bucket, soporte
):
    o = aprobada(motor_migrate, e)
    anterior = adjunta_que_vale(motor_app, e, o)
    r = subir(e, o, reemplaza=anterior["id"])
    assert analizar(motor_app) == 1
    assert ficha(e, o)["factura_adjunta"]["documento_id"] == r.json()["documento_id"]  # control
    borrar_por_soporte(motor_app, e, r.json()["documento_id"])

    f = ficha(e, o)
    assert f["factura_adjunta"] == anterior
    assert f["factura_reemplazo"] is None
    assert [x["documento_vigente"] for x in f["facturas_anteriores"]] == [False]
    assert (f["puede_subir_factura"], f["puede_reemplazar_factura"]) == (False, True)
    # La que la ficha muestra es la que lleva la renovación.
    envejecer(motor_migrate, o, timedelta(days=8))
    assert llamar(motor_migrate, e, e.adm, RENOVAR, orden=o, version=1) == 2
    v2 = versiones(motor_migrate, o)[1]
    assert str(v2["factura_version_id"]) == anterior["factura_version_id"]
    # Y el reemplazo que la ficha ofrece, el acto lo acepta.
    o2 = aprobada(motor_migrate, e)
    otra = adjunta_que_vale(motor_app, e, o2)
    s = subir(e, o2, reemplaza=otra["id"])
    assert analizar(motor_app) == 1
    borrar_por_soporte(motor_app, e, s.json()["documento_id"])
    assert ficha(e, o2)["puede_reemplazar_factura"] is True
    assert subir(e, o2, reemplaza=otra["id"]).status_code == 201


def test_borrado_un_reemplazo_en_el_antivirus_ya_no_frena(motor_migrate, motor_app, e, bucket,
                                                         soporte):  # fmt: skip
    o = aprobada(motor_migrate, e)
    anterior = adjunta_que_vale(motor_app, e, o)
    r = subir(e, o, reemplaza=anterior["id"])
    assert ficha(e, o)["puede_reemplazar_factura"] is False  # control: en el antivirus, frena
    borrar_por_soporte(motor_app, e, r.json()["documento_id"])
    f = ficha(e, o)
    assert (f["factura_reemplazo"], f["puede_reemplazar_factura"]) == (None, True)
    assert subir(e, o, reemplaza=anterior["id"]).status_code == 201


# Renovar ---------------------------------------------------------------------------------------


def test_renovar_conserva_la_factura_adjunta(motor_migrate, motor_app, e, bucket):
    o = aprobada(motor_migrate, e)
    r = subir(e, o)
    assert analizar(motor_app) == 1
    sin = aprobada(motor_migrate, e)
    for x in (o, sin):
        envejecer(motor_migrate, x, timedelta(days=8))
        assert llamar(motor_migrate, e, e.adm, RENOVAR, orden=x, version=1) == 2
    v2 = versiones(motor_migrate, o)[1]
    assert str(v2["factura_version_id"]) == r.json()["archivo"]["id"]
    assert str(v2["documento_id"]) == r.json()["documento_id"]
    assert orden(motor_migrate, o)["estado"] == "en_revision"
    # Control: la que no tenía factura sigue sin ella.
    assert versiones(motor_migrate, sin)[1]["factura_version_id"] is None


# La base ---------------------------------------------------------------------------------------


def test_cem_app_no_escribe_la_constancia_ni_llama_a_la_interna(motor_app, e):
    with pytest.raises(DBAPIError) as error, motor_app.begin() as c:
        c.execute(text("insert into nucleo.factura_adjunta_orden_pago (consorcio_id, orden_id,"
                       " version, documento_id, factura_version_id, adjuntada_por,"
                       " identidad_adjunto) values (:c, :c, 1, :c, :c, :c, :c)"),
                  {"c": e.cons})  # fmt: skip
    assert error.value.orig.diag.sqlstate == "42501"
    with pytest.raises(DBAPIError) as error, motor_app.begin() as c:
        c.execute(text("select nucleo.factura_adjunta_disponible(:c, :c, 1)"), {"c": e.cons})
    assert error.value.orig.diag.sqlstate == "42501"


def test_la_base_exige_la_facultad_de_preparar(motor_migrate, e):
    o = aprobada(motor_migrate, e)
    adjuntar = ("select nucleo.adjuntar_factura_orden_pago(:orden, 1, null, :titulo,"
                " 'application/pdf', 1, repeat('a', 64), :actor, :origen, :sesion)")  # fmt: skip
    niega(motor_migrate, e, e.mc1, adjuntar, "42501", orden=o, titulo=TITULO)
    assert constancias(motor_migrate, o) == []
    llamar(motor_migrate, e, e.adm, adjuntar, orden=o, titulo=TITULO)  # control
    assert len(constancias(motor_migrate, o)) == 1


def test_las_funciones_las_ejecuta_solo_cem_app(motor_migrate):
    with motor_migrate.connect() as c:
        filas = c.execute(text(
            "select p.proname, p.prosecdef, has_function_privilege('cem_app', p.oid, 'EXECUTE'),"
            " has_function_privilege('public', p.oid, 'EXECUTE'), array_to_string(p.proconfig, ',')"
            " from pg_proc p join pg_namespace n on n.oid = p.pronamespace"
            " where n.nspname = 'nucleo' and p.proname in ('adjuntar_factura_orden_pago',"
            " 'factura_adjunta_lectura', 'factura_adjunta_disponible',"
            " 'factura_adjunta_en_revision',"
            " 'panel_comprobantes_faltantes') order by p.proname"
        )).all()  # fmt: skip
    camino = "search_path=pg_catalog, pg_temp"
    assert [tuple(f) for f in filas] == [
        ("adjuntar_factura_orden_pago", True, True, False, camino),
        ("factura_adjunta_disponible", False, False, False, camino),
        ("factura_adjunta_en_revision", False, False, False, camino),
        ("factura_adjunta_lectura", True, True, False, camino),
        ("panel_comprobantes_faltantes", True, True, False, camino),
    ]


def _definiciones(motor) -> dict[str, tuple]:
    funciones = ("nucleo.renovar_orden_vencida(uuid, integer, uuid, text, uuid)",
                 "nucleo.documento_referenciado(uuid, uuid)",
                 "nucleo.exportar_ordenes_pago(uuid, uuid)",
                 "nucleo.panel_comprobantes_faltantes()")  # fmt: skip
    with motor.connect() as c:
        return {
            f: tuple(c.execute(
                text("select pg_get_functiondef(p.oid), coalesce(p.proacl::text, ''), p.prosecdef"
                     " from pg_proc p where p.oid = to_regprocedure(:f)"), {"f": f},
            ).one())
            for f in funciones
        }  # fmt: skip


def test_0099_ida_y_vuelta_deja_todo_como_estaba(base_aparte):
    cfg, motor = base_aparte
    command.upgrade(cfg, "0098")
    antes = _definiciones(motor)
    command.upgrade(cfg, "0099")
    con = _definiciones(motor)
    assert all(con[f] != antes[f] for f in antes)  # control: la 0099 cambió las cuatro
    with motor.connect() as c:
        assert c.execute(text("select to_regclass('nucleo.factura_adjunta_orden_pago')")).scalar()
    command.downgrade(cfg, "0098")
    assert _definiciones(motor) == antes
    with motor.connect() as c:
        assert c.execute(text("select to_regclass('nucleo.factura_adjunta_orden_pago')")).scalar() \
            is None  # fmt: skip
    command.upgrade(cfg, "0099")
    assert _definiciones(motor) == con
