"""El comprobante de un pago manual lo ven solo el Consejo y quien lo subió (`0061`, P3 148).

La escena y los ayudantes son los de `test_pago_manual_0055`: la administración prepara y ejecuta,
mc1 aprueba y mc2 concilia; la propietaria lee los documentos por su vínculo.
"""

import importlib.util
from pathlib import Path
from uuid import UUID

import pytest
from alembic import command
from sqlalchemy import text

from app.db import transaccion
from tests.test_orden_pago_neutra import llamar, niega, nueva_sesion
from tests.test_pago_manual_0055 import REGISTRAR, emitida, evidencia, registrada, ver
from tests.test_pago_manual_0055 import e as e  # noqa: F401  (es un fixture)
from tests.test_rutas_egresos import get
from tests.test_vinculo_0014 import base_aparte  # noqa: F401  (es un fixture)

pytestmark = pytest.mark.db

LISTA = "select documento_id, version_id from nucleo.documentos_del_consorcio()"
DESCARGA = "select clave_objeto from nucleo.version_para_descargar(:d, :v)"


def comprobante_subido_por(motor_migrate, e, persona, orden=None) -> tuple[UUID, UUID]:
    """Un comprobante con una versión `disponible` que subió `persona`. Con `orden`, cargado desde
    el pago de esa orden (`0063`): el único que el registro ata."""
    with motor_migrate.begin() as c:
        doc = c.execute(
            text("insert into nucleo.documento (consorcio_id, categoria, titulo, fecha,"
                 " cargado_por, orden_comprobante) values (:c, 'facturas_y_comprobantes',"
                 " 'Comprobante SINTETICO (NO REAL)', current_date, :p, :o) returning id"),
            {"c": e.cons, "p": persona, "o": orden},
        ).scalar_one()  # fmt: skip
        v = c.execute(
            text("insert into nucleo.version_documento (consorcio_id, documento_id, estado, tipo,"
                 " tamano, sha256, clave_objeto, antivirus, firmas_en, subida_por, analizada_en)"
                 " values (:c, :d, 'disponible', 'application/pdf', 1, repeat('a', 64),"
                 " 'sintetico/' || :d, 'ClamAV SINTETICO', now(), :p, now()) returning id"),
            {"c": e.cons, "d": doc, "p": persona},
        ).scalar_one()  # fmt: skip
    return doc, v


def lista(motor_app, e, persona) -> set[tuple[UUID, UUID | None]]:
    with transaccion(motor_app, persona_id=persona, consorcio_id=e.cons) as c:
        return {(f.documento_id, f.version_id) for f in c.execute(text(LISTA))}


def descarga(motor_app, e, persona, doc, v) -> list[str]:
    with transaccion(motor_app, persona_id=persona, consorcio_id=e.cons) as c:
        return [f.clave_objeto for f in c.execute(text(DESCARGA), {"d": doc, "v": v})]


def registrar_con(motor_migrate, e, o, doc) -> UUID:
    """La administración ve la cuenta en el acto de ejecución y registra con ese comprobante."""
    ver(motor_migrate, e, e.adm, o)
    return llamar(
        motor_migrate, e, e.adm, REGISTRAR, **evidencia(motor_migrate, e, o, documento=doc)
    )


def test_atado_a_un_pago_lo_ven_solo_el_consejo_y_quien_lo_subio(motor_migrate, motor_app, e):
    """Lo subió mc1 (no quien preparó): la administración, que tiene `documentos.ver`, y la
    propietaria dejan de verlo; mc1 y mc2 (el Consejo) lo siguen viendo."""
    o = emitida(motor_migrate, e)
    ver(motor_migrate, e, e.adm, o)  # el comprobante es posterior al acto de ejecución
    doc, v = comprobante_subido_por(motor_migrate, e, e.mc1, orden=o)
    otro, otra_v = comprobante_subido_por(motor_migrate, e, e.mc1)  # sin orden: control
    # Desde 0063 nace restringido: antes de atarlo ya lo ven solo el Consejo y quien lo subió.
    for persona in (e.prop, e.adm):
        assert doc not in {d for d, _ in lista(motor_app, e, persona)}, persona
        assert descarga(motor_app, e, persona, doc, v) == [], persona
        assert (otro, otra_v) in lista(motor_app, e, persona), persona  # control
    assert (doc, v) in lista(motor_app, e, e.mc2)
    registrar_con(motor_migrate, e, o, doc)
    for persona in (e.prop, e.adm):
        assert doc not in {d for d, _ in lista(motor_app, e, persona)}, persona
        assert descarga(motor_app, e, persona, doc, v) == [], persona
        # El que no respalda ningún pago sigue a la vista.
        assert (otro, otra_v) in lista(motor_app, e, persona), persona
        assert descarga(motor_app, e, persona, otro, otra_v) == [f"sintetico/{otro}"]
    for persona in (e.mc1, e.mc2):
        assert (doc, v) in lista(motor_app, e, persona), persona
        assert descarga(motor_app, e, persona, doc, v) == [f"sintetico/{doc}"], persona


def test_quien_lo_subio_ve_solo_su_version(motor_migrate, motor_app, e):
    """La administración subió el comprobante y registró el pago: sigue viendo su versión; la de
    otro en el mismo documento, no."""
    o = emitida(motor_migrate, e)
    p = registrada(motor_migrate, e, o)
    with motor_migrate.connect() as c:
        doc = c.execute(text("select documento_id from nucleo.pago_manual where id = :p"),
                        {"p": p}).scalar_one()  # fmt: skip
        propia = c.execute(text("select id from nucleo.version_documento where documento_id = :d"),
                           {"d": doc}).scalar_one()  # fmt: skip
    with motor_migrate.begin() as c:
        ajena = c.execute(
            text("insert into nucleo.version_documento (consorcio_id, documento_id, estado, tipo,"
                 " tamano, sha256, clave_objeto, antivirus, firmas_en, subida_por, analizada_en)"
                 " values (:c, :d, 'disponible', 'application/pdf', 1, repeat('b', 64),"
                 " 'sintetico/otra', 'ClamAV SINTETICO', now(), :p, now()) returning id"),
            {"c": e.cons, "d": doc, "p": e.mc1},
        ).scalar_one()  # fmt: skip
    assert {x for d, x in lista(motor_app, e, e.adm) if d == doc} == {propia}
    assert descarga(motor_app, e, e.adm, doc, propia) != []
    assert descarga(motor_app, e, e.adm, doc, ajena) == []
    # Control: el Consejo ve las dos.
    assert {x for d, x in lista(motor_app, e, e.mc2) if d == doc} == {propia, ajena}
    assert descarga(motor_app, e, e.mc2, doc, ajena) == ["sintetico/otra"]


def test_por_http_la_propietaria_no_lo_lista_y_su_descarga_es_404(motor_migrate, e):
    o = emitida(motor_migrate, e)
    ver(motor_migrate, e, e.adm, o)
    doc, v = comprobante_subido_por(motor_migrate, e, e.adm, orden=o)
    control, _ = comprobante_subido_por(motor_migrate, e, e.adm)  # sin orden
    sesion = nueva_sesion(motor_migrate, e.prop)

    def ids(persona, s=None) -> set[str]:
        r = get(e, persona, "documentos", sesion=s)
        assert r.status_code == 200, r.text
        return {d["id"] for d in r.json()["documentos"]}

    assert str(control) in ids(e.prop, sesion)  # control: lee documentos
    assert str(doc) not in ids(e.prop, sesion)  # nace restringido (0063)
    registrar_con(motor_migrate, e, o, doc)
    assert str(doc) not in ids(e.prop, sesion)
    assert str(doc) in ids(e.mc2)  # control: el Consejo
    r = get(e, e.prop, f"documentos/{doc}/versiones/{v}/descargar", sesion=sesion)
    assert r.status_code == 404, r.text
    assert r.json()["codigo"] == "no_encontrado"


def test_un_documento_anterior_al_acto_de_ejecucion_no_se_ata(motor_migrate, motor_app, e):
    """Un documento anterior al acto de ejecución no es el comprobante de esa transferencia,
    aunque tenga la orden (la ruta de `0063` no lo crea antes del acto; acá lo escribe la
    dueña de la base): el registro se niega y no queda atado."""
    o = emitida(motor_migrate, e)
    viejo, v = comprobante_subido_por(motor_migrate, e, e.adm, orden=o)
    ver(motor_migrate, e, e.adm, o)
    niega(motor_migrate, e, e.adm, REGISTRAR, "comprobante_invalido",
          **evidencia(motor_migrate, e, o, documento=viejo))  # fmt: skip
    with motor_migrate.connect() as c:
        sql = "select count(*) from nucleo.comprobante_pago_manual where documento_id = :d"
        assert c.execute(text(sql), {"d": viejo}).scalar_one() == 0  # la atadura se deshizo
    # Tampoco con una versión nueva del documento viejo: las anteriores quedarían escondidas.
    with motor_migrate.begin() as c:
        c.execute(
            text("insert into nucleo.version_documento (consorcio_id, documento_id, estado, tipo,"
                 " tamano, sha256, clave_objeto, antivirus, firmas_en, subida_por, analizada_en)"
                 " values (:c, :d, 'disponible', 'application/pdf', 1, repeat('c', 64),"
                 " 'sintetico/nueva', 'ClamAV SINTETICO', now(), :p, now())"),
            {"c": e.cons, "d": viejo, "p": e.adm},
        )  # fmt: skip
    niega(motor_migrate, e, e.adm, REGISTRAR, "comprobante_invalido",
          **evidencia(motor_migrate, e, o, documento=viejo))  # fmt: skip
    with motor_migrate.connect() as c:
        sql = "select count(*) from nucleo.comprobante_pago_manual where documento_id = :d"
        assert c.execute(text(sql), {"d": viejo}).scalar_one() == 0
    # Control: con uno subido después del acto, el mismo registro pasa.
    nuevo, _ = comprobante_subido_por(motor_migrate, e, e.adm, orden=o)
    llamar(motor_migrate, e, e.adm, REGISTRAR, **evidencia(motor_migrate, e, o, documento=nuevo))


def test_las_funciones_nuevas_no_las_ejecuta_public(motor_migrate):
    with motor_migrate.connect() as c:
        for f in ("nucleo.es_comprobante_de_pago(uuid, uuid)",
                  "nucleo.ve_comprobantes_de_pago(uuid, uuid)"):  # fmt: skip
            sql = "select has_function_privilege('public', cast(:f as regprocedure), 'execute')"
            assert c.execute(text(sql), {"f": f}).scalar_one() is False, f
            sql = "select has_function_privilege('cem_app', cast(:f as regprocedure), 'execute')"
            assert c.execute(text(sql), {"f": f}).scalar_one() is False, f


def migracion_0061():
    ruta = Path(__file__).resolve().parents[1] / "migrations" / "versions"
    archivo = ruta / "0061_comprobantes_solo_consejo.py"
    spec = importlib.util.spec_from_file_location("migracion_0061", archivo)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def test_0061_ida_y_vuelta(base_aparte):  # noqa: F811
    cfg, motor = base_aparte

    def definiciones() -> tuple[str, str, object, str]:
        with motor.connect() as c:
            sql = ("select pg_get_functiondef('nucleo.documentos_del_consorcio()'::regprocedure),"
                   " pg_get_functiondef("
                   "'nucleo.version_para_descargar(uuid, uuid)'::regprocedure),"
                   " to_regprocedure('nucleo.es_comprobante_de_pago(uuid, uuid)'),"
                   " pg_get_functiondef(cast(:r as regprocedure))")  # fmt: skip
            return tuple(c.execute(text(sql), {"r": migracion_0061().REGISTRO}).one())

    command.upgrade(cfg, "0060")
    antes = definiciones()
    assert antes[2] is None and "es_comprobante_de_pago" not in antes[0] + antes[1]
    command.upgrade(cfg, "0061")
    despues = definiciones()
    assert despues[2] is not None
    assert "es_comprobante_de_pago" in despues[0] and "es_comprobante_de_pago" in despues[1]
    assert "anterior al acto" not in antes[3] and "anterior al acto" in despues[3]
    command.downgrade(cfg, "0060")
    assert definiciones() == antes
    command.upgrade(cfg, "0061")  # control
    assert definiciones()[2] is not None
