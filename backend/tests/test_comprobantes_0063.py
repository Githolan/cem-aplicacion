"""El comprobante de un pago manual queda restringido desde que se carga (`0063`, P3 154).

La escena es la de `test_pago_manual_0055`: la administración prepara, mc1 aprueba y la
administración ejecuta. Datos SINTETICOS / NO REAL.
"""

import importlib.util
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db import transaccion
from tests.test_comprobantes_0061 import comprobante_subido_por, descarga, lista
from tests.test_orden_pago_neutra import llamar, niega, nueva_sesion
from tests.test_pago_manual_0055 import REGISTRAR, emitida, evidencia, ver
from tests.test_pago_manual_0055 import e as e  # noqa: F401  (es un fixture)
from tests.test_rutas_egresos import codigo, get, post
from tests.test_vinculo_0014 import base_aparte  # noqa: F401  (es un fixture)

pytestmark = pytest.mark.db

CREAR = (
    "select nucleo.crear_comprobante_de_pago(:orden, :titulo, current_date, :actor, :origen,"
    " :sesion)"
)
TITULO = "Transferencia SINTETICA (NO REAL)"


def creado(motor_migrate, e, quien, o) -> UUID:
    return llamar(motor_migrate, e, quien, CREAR, orden=o, titulo=TITULO)


def con_version(motor_migrate, e, doc, persona) -> UUID:
    with motor_migrate.begin() as c:
        return c.execute(
            text("insert into nucleo.version_documento (consorcio_id, documento_id, estado, tipo,"
                 " tamano, sha256, clave_objeto, antivirus, firmas_en, subida_por, analizada_en)"
                 " values (:c, :d, 'disponible', 'application/pdf', 1, repeat('a', 64),"
                 " 'sintetico/' || :d, 'ClamAV SINTETICO', now(), :p, now()) returning id"),
            {"c": e.cons, "d": doc, "p": persona},
        ).scalar_one()  # fmt: skip


def ids_por_http(e, persona, sesion=None) -> dict[str, str | None]:
    r = get(e, persona, "documentos", sesion=sesion)
    assert r.status_code == 200, r.text
    return {d["id"]: d["orden_pago_id"] for d in r.json()["documentos"]}


def test_por_http_lo_carga_quien_vio_la_cuenta_y_nace_restringido(motor_migrate, motor_app, e):
    o = emitida(motor_migrate, e)
    cuerpo = {"titulo": TITULO, "fecha": "2026-11-02"}
    # Antes del acto de ejecución nadie lo carga, tampoco quien va a ejecutar.
    r = post(e, e.adm, f"ordenes-pago/{o}/pago-manual/comprobante", cuerpo)
    assert codigo(r) == (403, "no_es_quien_ejecuto"), r.text
    ver(motor_migrate, e, e.adm, o)
    r = post(e, e.adm, f"ordenes-pago/{o}/pago-manual/comprobante", cuerpo)
    assert r.status_code == 201, r.text
    doc = UUID(r.json()["id"])
    v = con_version(motor_migrate, e, doc, e.adm)
    control, _ = comprobante_subido_por(motor_migrate, e, e.adm)  # sin orden
    # Antes de registrarlo: la propietaria no lo ve (sí el documento sin orden); el Consejo que
    # no preparó y quien lo subió, sí, con la orden.
    sesion = nueva_sesion(motor_migrate, e.prop)
    assert str(control) in ids_por_http(e, e.prop, sesion)
    assert str(doc) not in ids_por_http(e, e.prop, sesion)
    assert descarga(motor_app, e, e.prop, doc, v) == []
    assert ids_por_http(e, e.mc2)[str(doc)] == str(o)
    assert ids_por_http(e, e.adm)[str(doc)] == str(o)
    assert ids_por_http(e, e.adm)[str(control)] is None
    # Y con él se registra el pago.
    llamar(motor_migrate, e, e.adm, REGISTRAR, **evidencia(motor_migrate, e, o, documento=doc))


def test_quien_preparo_y_no_lo_subio_no_lo_ve_antes_de_registrarlo(motor_migrate, motor_app, e):
    """La administración preparó; el comprobante con la orden lo subió mc1. Antes del registro
    ya no lo ve, aunque tenga `documentos.ver`."""
    o = emitida(motor_migrate, e)
    doc, v = comprobante_subido_por(motor_migrate, e, e.mc1, orden=o)
    otro, otra_v = comprobante_subido_por(motor_migrate, e, e.mc1)  # sin orden: control
    assert (otro, otra_v) in lista(motor_app, e, e.adm)
    assert doc not in {d for d, _ in lista(motor_app, e, e.adm)}
    assert descarga(motor_app, e, e.adm, doc, v) == []
    assert (doc, v) in lista(motor_app, e, e.mc1)  # quien lo subió
    assert (doc, v) in lista(motor_app, e, e.mc2)  # el Consejo que no preparó


def test_el_consejo_que_preparo_no_lo_ve_antes_de_registrarlo(motor_migrate, motor_app, e):
    """Decisión 6 y P3 156 antes del registro: la administración, que preparó, tiene también un
    mandato del Consejo; el comprobante con la orden lo subió mc1 y todavía no se registró."""
    from tests.test_comprobantes_0062 import consejo_en_la_misma_cuenta, es_del_consejo, ve

    o = emitida(motor_migrate, e)
    doc, v = comprobante_subido_por(motor_migrate, e, e.mc1, orden=o)
    consejo_en_la_misma_cuenta(motor_migrate, motor_app, e)
    assert es_del_consejo(motor_migrate, e, e.adm)  # control: tiene la facultad del Consejo
    assert ve(motor_app, e.cons, e.adm, doc, v) == (False, False)
    assert ve(motor_app, e.cons, e.mc2, doc, v) == (True, True)  # control: otro consejero


def test_lo_carga_solo_quien_vio_la_cuenta_de_esa_orden(motor_migrate, e):
    o1, o2 = emitida(motor_migrate, e), emitida(motor_migrate, e)
    ver(motor_migrate, e, e.adm, o1)
    niega(motor_migrate, e, e.adm, CREAR, "no_es_quien_ejecuto", orden=o2, titulo=TITULO)
    niega(motor_migrate, e, e.adm, CREAR, "no_es_quien_ejecuto", orden=uuid4(), titulo=TITULO)
    doc = creado(motor_migrate, e, e.adm, o1)  # control
    with motor_migrate.connect() as c:
        sql = "select categoria, orden_comprobante, cargado_por from nucleo.documento where id = :d"
        fila = c.execute(text(sql), {"d": doc}).one()
    assert tuple(fila) == ("facturas_y_comprobantes", o1, e.adm)


VERSION = (
    "select id from nucleo.nueva_version(:documento, 'application/pdf', 1, repeat('c', 64),"
    " :actor, :origen)"
)


def test_una_version_del_comprobante_la_sube_solo_quien_vio_la_cuenta(motor_migrate, e):
    """Revisión independiente de 0063: la administración tiene `documentos.cargar` pero no vio la
    cuenta de o2; una versión del comprobante de o2 cambiaría la evidencia de una corrección. Para
    ella el documento no existe; después de ver la cuenta, sí sube."""
    o1, o2 = emitida(motor_migrate, e), emitida(motor_migrate, e)
    ver(motor_migrate, e, e.adm, o1)
    doc, _ = comprobante_subido_por(motor_migrate, e, e.mc1, orden=o2)
    sin_orden, _ = comprobante_subido_por(motor_migrate, e, e.mc1)
    niega(motor_migrate, e, e.adm, VERSION, "no_encontrado", documento=doc)
    assert llamar(motor_migrate, e, e.adm, VERSION, documento=sin_orden) is not None  # control
    ver(motor_migrate, e, e.adm, o2)
    assert llamar(motor_migrate, e, e.adm, VERSION, documento=doc) is not None


def test_sin_facultad_de_carga_la_base_lo_niega(motor_migrate, e):
    """mc1 aprobó y no tiene `documentos.cargar`: la base corta antes de mirar el acto."""
    o = emitida(motor_migrate, e)
    with pytest.raises(DBAPIError) as error:
        creado(motor_migrate, e, e.mc1, o)
    assert error.value.orig.sqlstate == "42501"


def test_el_registro_ya_no_ata_un_documento_sin_orden(motor_migrate, e):
    o = emitida(motor_migrate, e)
    ver(motor_migrate, e, e.adm, o)
    sin_orden, _ = comprobante_subido_por(motor_migrate, e, e.adm)  # posterior al acto
    niega(motor_migrate, e, e.adm, REGISTRAR, "comprobante_invalido",
          **evidencia(motor_migrate, e, o, documento=sin_orden))  # fmt: skip
    with motor_migrate.connect() as c:
        sql = "select count(*) from nucleo.comprobante_pago_manual where documento_id = :d"
        assert c.execute(text(sql), {"d": sin_orden}).scalar_one() == 0
    doc = creado(motor_migrate, e, e.adm, o)  # control: cargado desde el pago, sí
    con_version(motor_migrate, e, doc, e.adm)
    llamar(motor_migrate, e, e.adm, REGISTRAR, **evidencia(motor_migrate, e, o, documento=doc))


def test_la_orden_de_un_comprobante_no_cambia(motor_migrate, e):
    o1, o2 = emitida(motor_migrate, e), emitida(motor_migrate, e)
    ver(motor_migrate, e, e.adm, o1)
    doc = creado(motor_migrate, e, e.adm, o1)
    for nueva in (o2, None):
        with pytest.raises(DBAPIError) as error, motor_migrate.begin() as c:
            c.execute(text("update nucleo.documento set orden_comprobante = :o where id = :d"),
                      {"o": nueva, "d": doc})  # fmt: skip
        assert error.value.orig.diag.message_hint == "historia_inmutable"
    with motor_migrate.begin() as c:  # control: otra columna sí cambia
        c.execute(text("update nucleo.documento set titulo = 'Otro SINTETICO' where id = :d"),
                  {"d": doc})  # fmt: skip


def test_la_funcion_nueva_la_ejecuta_solo_cem_app(motor_migrate):
    f = "nucleo.crear_comprobante_de_pago(uuid, text, date, uuid, text, uuid)"  # 0066
    with motor_migrate.connect() as c:
        for rol, esperado in (("public", False), ("cem_app", True)):
            sql = f"select has_function_privilege('{rol}', cast(:f as regprocedure), 'execute')"
            assert c.execute(text(sql), {"f": f}).scalar_one() is esperado, rol
        sql = ("select has_function_privilege('cem_app', 'nucleo.documentos_del_consorcio()',"
               " 'execute'), has_function_privilege('public', 'nucleo.documentos_del_consorcio()',"
               " 'execute')")  # fmt: skip
        assert tuple(c.execute(text(sql)).one()) == (True, False)


def test_otro_consorcio_no_carga_un_comprobante_de_esta_orden(motor_migrate, motor_app, e):
    from tests.test_orden_pago_neutra import escena_economica

    o = emitida(motor_migrate, e)
    ver(motor_migrate, e, e.adm, o)
    otro = escena_economica(motor_migrate, motor_app)
    with pytest.raises(DBAPIError) as error, transaccion(motor_migrate, persona_id=otro.adm,
                                                         consorcio_id=otro.cons) as c:  # fmt: skip
        c.execute(text(CREAR), {"orden": o, "titulo": TITULO, "actor": otro.adm,
                                "origen": "api", "sesion": otro.sesion[otro.adm]})  # fmt: skip
    assert error.value.orig.diag.message_hint == "no_es_quien_ejecuto"
    with motor_migrate.connect() as c:
        sql = "select count(*) from nucleo.documento where orden_comprobante = :o"
        assert c.execute(text(sql), {"o": o}).scalar_one() == 0
    creado(motor_migrate, e, e.adm, o)  # control


def test_0063_ida_y_vuelta(base_aparte):  # noqa: F811
    cfg, motor = base_aparte

    def definiciones() -> tuple:
        with motor.connect() as c:
            sql = ("select pg_get_functiondef('nucleo.documentos_del_consorcio()'::regprocedure),"
                   " pg_get_functiondef('nucleo.es_comprobante_de_pago(uuid, uuid)'::regprocedure),"
                   " pg_get_functiondef("
                   "'nucleo.preparo_la_orden_del_comprobante(uuid, uuid, uuid)'::regprocedure),"
                   " to_regprocedure("
                   "'nucleo.crear_comprobante_de_pago(uuid, text, date, uuid, text)'),"
                   " has_function_privilege('cem_app', 'nucleo.documentos_del_consorcio()',"
                   " 'execute'),"
                   " pg_get_functiondef(cast(:registro as regprocedure)),"
                   " pg_get_functiondef(cast(:version as regprocedure))")  # fmt: skip
            m = migracion_0063()
            return tuple(c.execute(text(sql), {"registro": m.REGISTRO, "version": m.VERSION}).one())

    command.upgrade(cfg, "0062")
    antes = definiciones()
    assert antes[3] is None
    assert "orden_comprobante" not in antes[0] + antes[1] + antes[2] + antes[5] + antes[6]
    command.upgrade(cfg, "0063")
    despues = definiciones()
    assert despues[3] is not None and despues[4] is True
    assert "orden_comprobante" in despues[0] and "orden_comprobante" in despues[1]
    assert "orden_comprobante" in despues[5] and "abrio_la_cuenta" in despues[6]
    command.downgrade(cfg, "0062")
    assert definiciones() == antes
    command.upgrade(cfg, "0063")  # control
    assert definiciones()[3] is not None


def migracion_0063():
    ruta = Path(__file__).resolve().parents[1] / "migrations" / "versions"
    archivo = ruta / "0063_comprobante_desde_el_pago.py"
    spec = importlib.util.spec_from_file_location("migracion_0063", archivo)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def test_0063_no_baja_con_un_comprobante_sin_registrar(motor_migrate, e):
    """La guarda del downgrade, sobre la escena y en una transacción que se deshace."""
    guarda = migracion_0063().SIN_REGISTRAR
    o = emitida(motor_migrate, e)
    ver(motor_migrate, e, e.adm, o)
    doc = creado(motor_migrate, e, e.adm, o)
    with motor_migrate.connect() as c:
        with pytest.raises(DBAPIError) as error:
            c.execute(text(guarda))
        assert error.value.orig.diag.message_hint == "downgrade_con_datos"
        c.rollback()
    con_version(motor_migrate, e, doc, e.adm)
    llamar(motor_migrate, e, e.adm, REGISTRAR, **evidencia(motor_migrate, e, o, documento=doc))
    # Control: registrado, la condición de la guarda ya no lo cuenta (la base de pruebas es
    # compartida: otros comprobantes sin registrar la siguen disparando).
    with motor_migrate.connect() as c:
        sql = ("select exists (select 1 from nucleo.documento d where d.id = :d"
               " and d.orden_comprobante is not null and not exists (select 1"
               " from nucleo.comprobante_pago_manual p where p.consorcio_id = d.consorcio_id"
               " and p.documento_id = d.id))")  # fmt: skip
        assert c.execute(text(sql), {"d": doc}).scalar_one() is False
