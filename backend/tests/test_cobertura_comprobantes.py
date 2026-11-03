"""Casos de los comprobantes de pago que las pruebas de `0061` a `0064` no cubrían (P3 160 y 168).

La escena es la de `test_pago_manual_0055`: la administración prepara y ejecuta, mc1 aprueba y mc2
concilia; la propietaria lee los documentos por su vínculo. Datos SINTETICOS / NO REAL.
"""

from uuid import UUID

import pytest
from sqlalchemy import text

from app.db import transaccion
from tests.test_comprobantes_0061 import comprobante_subido_por, lista
from tests.test_comprobantes_0063 import VERSION
from tests.test_comprobantes_0064 import atado_antes_de_0063
from tests.test_orden_pago_neutra import llamar, niega, nueva_sesion, otorgar
from tests.test_pago_manual_0055 import (
    CONCILIAR,
    acta,
    emitida,
    otro_ejecutor,
    registrada,
)
from tests.test_pago_manual_0055 import e as e  # noqa: F401  (es un fixture)
from tests.test_rutas_egresos import get

pytestmark = pytest.mark.db


def documento_del_pago(motor_migrate, pago: UUID) -> UUID:
    with motor_migrate.connect() as c:
        sql = "select documento_id from nucleo.pago_manual where id = :p"
        return c.execute(text(sql), {"p": pago}).scalar_one()


def mandato_de(motor_migrate, e, persona: UUID) -> UUID:
    with motor_migrate.connect() as c:
        return c.execute(
            text("select id from nucleo.mandato where consorcio_id = :c and persona_id = :p"),
            {"c": e.cons, "p": persona},
        ).scalar_one()  # fmt: skip


def test_el_consejo_que_pierde_el_mandato_deja_de_ver_el_comprobante(motor_migrate, motor_app, e):
    """mc2 lo ve mientras es del Consejo; con el mandato terminado, ya no. mc1 sigue viéndolo."""
    o = emitida(motor_migrate, e)
    doc = documento_del_pago(motor_migrate, registrada(motor_migrate, e, o))
    assert doc in {d for d, _ in lista(motor_app, e, e.mc2)}  # antes: lo ve
    with transaccion(motor_app, persona_id=None, consorcio_id=e.cons) as c:
        c.execute(
            text("select nucleo.baja_mandato(:m, 'renuncia', null, 'Nota SINTETICA',"
                 " 'Prueba de P3 160', null, 'soporte')"),
            {"m": mandato_de(motor_migrate, e, e.mc2)},
        )  # fmt: skip
    assert doc not in {d for d, _ in lista(motor_app, e, e.mc2)}
    assert doc in {d for d, _ in lista(motor_app, e, e.mc1)}  # control: el Consejo vigente


def test_la_version_propia_en_cuarentena_se_descarga_con_409_y_la_ajena_es_404(motor_migrate, e):
    """Sobre un comprobante atado: quien sube una versión que sigue en cuarentena recibe 409 (la
    ve, no se descarga todavía); para el Consejo, esa versión de otro no existe (404)."""
    o = emitida(motor_migrate, e)
    doc = documento_del_pago(motor_migrate, registrada(motor_migrate, e, o))
    with motor_migrate.begin() as c:
        v = c.execute(
            text("insert into nucleo.version_documento (consorcio_id, documento_id, estado, tipo,"
                 " tamano, sha256, clave_objeto, subida_por) values (:c, :d, 'en_cuarentena',"
                 " 'application/pdf', 1, repeat('d', 64), 'sintetico/cuarentena', :p)"
                 " returning id"),
            {"c": e.cons, "d": doc, "p": e.adm},
        ).scalar_one()  # fmt: skip
    ruta = f"documentos/{doc}/versiones/{v}/descargar"
    r = get(e, e.adm, ruta)
    assert (r.status_code, r.json()["codigo"]) == (409, "version_no_disponible"), r.text
    r = get(e, e.mc2, ruta)
    assert (r.status_code, r.json()["codigo"]) == (404, "no_encontrado"), r.text


def test_por_http_la_propietaria_tampoco_lo_ve_despues_de_una_observacion(motor_migrate, e):
    o = emitida(motor_migrate, e)
    pago = registrada(motor_migrate, e, o)
    doc = documento_del_pago(motor_migrate, pago)
    control, _ = comprobante_subido_por(motor_migrate, e, e.adm)  # sin orden: a la vista
    llamar(motor_migrate, e, e.mc2, CONCILIAR, orden=o, pago=pago, resultado="observado",
           motivo="SINTETICO: el comprobante no se lee")  # fmt: skip
    sesion = nueva_sesion(motor_migrate, e.prop)
    r = get(e, e.prop, "documentos", sesion=sesion)
    assert r.status_code == 200, r.text
    ids = {d["id"] for d in r.json()["documentos"]}
    assert str(control) in ids  # control: lee documentos
    assert str(doc) not in ids


def test_quien_vio_la_cuenta_sube_la_version_del_mismo_comprobante_atado(
    motor_migrate, motor_app, e
):
    """P3 168: sobre el mismo comprobante atado antes de `0063`, la administración (sin haber
    visto esa cuenta) no sube versión; x, que la vio y registró, sí."""
    o = emitida(motor_migrate, e)
    x = otro_ejecutor(motor_migrate, motor_app, e)
    otorgar(motor_migrate, e.cons, mandato_de(motor_migrate, e, x), acta(motor_migrate, e),
            "documentos.cargar")  # fmt: skip
    doc = atado_antes_de_0063(motor_migrate, e, x, o)
    niega(motor_migrate, e, e.adm, VERSION, "no_encontrado", documento=doc)
    assert llamar(motor_migrate, e, x, VERSION, documento=doc) is not None
