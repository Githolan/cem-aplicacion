"""El Consejo no ve el comprobante de una orden que esa misma persona preparó (`0062`, P3 156).

La escena es la de `test_pago_manual_0055`: la administración prepara, mc1 aprueba y la
administración ejecuta y registra. Salvo donde se dice, el comprobante lo sube mc1, así que la
administración no lo ve por haberlo subido.
"""

from uuid import UUID

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from app.db import transaccion
from tests.test_comprobantes_0061 import comprobante_subido_por, registrar_con
from tests.test_mandatos import alta
from tests.test_orden_pago_neutra import escena_economica, otra_cuenta_en_el_consejo
from tests.test_pago_manual_0055 import desvincular, emitida, ver
from tests.test_pago_manual_0055 import e as e  # noqa: F401  (es un fixture)
from tests.test_vinculo_0014 import base_aparte  # noqa: F401  (es un fixture)

pytestmark = pytest.mark.db

LISTA = "select documento_id, version_id from nucleo.documentos_del_consorcio()"
DESCARGA = "select clave_objeto from nucleo.version_para_descargar(:d, :v)"


def ve(motor_app, cons, persona, doc, v) -> tuple[bool | str, bool]:
    """Qué ve `persona`, en el ámbito de `cons`: en la lista, `True` si tiene esa versión,
    `"titulo"` si tiene el documento con otra versión o sin ninguna (título, fecha y categoría),
    `False` si no hay rastro del documento; y si la descarga (la base devuelve la clave del objeto
    solo a quien puede descargarla)."""
    with transaccion(motor_app, persona_id=persona, consorcio_id=cons) as c:
        filas = {(f.documento_id, f.version_id) for f in c.execute(text(LISTA))}
        descarga = c.execute(text(DESCARGA), {"d": doc, "v": v}).all()
    en_lista = True if (doc, v) in filas else "titulo" if doc in {d for d, _ in filas} else False
    return en_lista, descarga != []


def atado(motor_migrate, e, subio) -> tuple[UUID, UUID]:
    """La administración prepara y ejecuta; el comprobante lo sube `subio` después de ver la
    cuenta y la administración registra el pago con él."""
    o = emitida(motor_migrate, e)
    ver(motor_migrate, e, e.adm, o)
    doc, v = comprobante_subido_por(motor_migrate, e, subio, orden=o)
    registrar_con(motor_migrate, e, o, doc)
    return doc, v


def consejo_en_la_misma_cuenta(motor_migrate, motor_app, e) -> None:
    """Decisión 6: la misma cuenta del administrador recibe también un mandato del Consejo."""
    with motor_migrate.connect() as c:
        acta = c.execute(
            text("select decision_id from nucleo.mandato where consorcio_id = :c"
                 " and persona_id = :p"), {"c": e.cons, "p": e.adm},
        ).scalar_one()  # fmt: skip
    alta(motor_app, e.cons, e.prop, acta, tipo="miembro_consejo", persona=e.adm)


def es_del_consejo(motor_migrate, e, persona) -> bool:
    with motor_migrate.connect() as c:
        return c.execute(
            text("select nucleo.ve_comprobantes_de_pago(:c, :p)"), {"c": e.cons, "p": persona}
        ).scalar_one()


def test_administrador_y_consejero_que_preparo_no_lo_ve_por_el_consejo(motor_migrate, motor_app,
                                                                        e):  # fmt: skip
    doc, v = atado(motor_migrate, e, e.mc1)
    consejo_en_la_misma_cuenta(motor_migrate, motor_app, e)
    assert es_del_consejo(motor_migrate, e, e.adm)  # control: tiene la facultad del Consejo
    assert ve(motor_app, e.cons, e.adm, doc, v) == (False, False)
    # Control: otro consejero, que no preparó ni subió, sí; quien lo subió, también.
    assert ve(motor_app, e.cons, e.mc2, doc, v) == (True, True)
    assert ve(motor_app, e.cons, e.mc1, doc, v) == (True, True)


def test_la_misma_identidad_con_otra_cuenta_del_consejo_tampoco(motor_migrate, motor_app, e):
    otra = otra_cuenta_en_el_consejo(motor_migrate, motor_app, e, e.adm)
    doc, v = atado(motor_migrate, e, e.mc1)
    assert es_del_consejo(motor_migrate, e, otra)
    assert ve(motor_app, e.cons, otra, doc, v) == (False, False)
    assert ve(motor_app, e.cons, e.mc2, doc, v) == (True, True)  # control


def test_otro_consejero_que_no_preparo_ni_subio_lo_ve(motor_migrate, motor_app, e):
    doc, v = atado(motor_migrate, e, e.adm)
    assert ve(motor_app, e.cons, e.mc2, doc, v) == (True, True)
    assert ve(motor_app, e.cons, e.prop, doc, v) == (False, False)  # control: otra persona


def test_quien_preparo_ejecuto_y_subio_lo_ve_por_haberlo_subido(motor_migrate, motor_app, e):
    consejo_en_la_misma_cuenta(motor_migrate, motor_app, e)
    doc, v = atado(motor_migrate, e, e.adm)
    assert ve(motor_app, e.cons, e.adm, doc, v) == (True, True)
    # Control: la versión que subió otro en el mismo documento no la ve por el Consejo.
    with motor_migrate.begin() as c:
        ajena = c.execute(
            text("insert into nucleo.version_documento (consorcio_id, documento_id, estado, tipo,"
                 " tamano, sha256, clave_objeto, antivirus, firmas_en, subida_por, analizada_en)"
                 " values (:c, :d, 'disponible', 'application/pdf', 1, repeat('b', 64),"
                 " 'sintetico/otra/' || :d, 'ClamAV SINTETICO', now(), :p, now()) returning id"),
            {"c": e.cons, "d": doc, "p": e.mc1},
        ).scalar_one()  # fmt: skip
    assert ve(motor_app, e.cons, e.adm, doc, ajena) == ("titulo", False)
    assert ve(motor_app, e.cons, e.mc2, doc, ajena) == (True, True)


def test_el_consejo_de_otro_consorcio_no_lo_ve(motor_migrate, motor_app, e):
    doc, v = atado(motor_migrate, e, e.mc1)
    otro = escena_economica(motor_migrate, motor_app)
    assert ve(motor_app, otro.cons, otro.mc2, doc, v) == (False, False)
    # En el ámbito del consorcio del comprobante, la base ni siquiera le da la lista.
    with pytest.raises(ProgrammingError, match="sin documentos.ver"):
        ve(motor_app, e.cons, otro.mc2, doc, v)
    assert ve(motor_app, e.cons, e.mc2, doc, v) == (True, True)  # control


def test_desvincular_despues_no_cambia_la_identidad_historica(motor_migrate, motor_app, e):
    """La administración preparó; después su cuenta se desvincula de su identidad. La otra cuenta
    de la misma persona, en el Consejo, ya no es «la misma persona de hoy» que la cuenta que
    preparó, pero la identidad guardada al preparar sigue siendo la suya."""
    otra = otra_cuenta_en_el_consejo(motor_migrate, motor_app, e, e.adm)
    doc, v = atado(motor_migrate, e, e.mc1)
    desvincular(motor_migrate, e, e.adm)
    with motor_migrate.connect() as c:
        sql = "select nucleo.misma_persona_humana(:c, :a, :b)"
        assert c.execute(text(sql), {"c": e.cons, "a": otra, "b": e.adm}).scalar_one() is False
    assert ve(motor_app, e.cons, otra, doc, v) == (False, False)
    assert ve(motor_app, e.cons, e.mc2, doc, v) == (True, True)  # control


def test_el_consejo_sin_identidad_vigente_no_lo_ve(motor_migrate, motor_app, e):
    """Falla cerrada: una cuenta del Consejo que no preparó, pero sin identidad humana vigente en
    el consorcio, no lo ve por el Consejo."""
    otra = otra_cuenta_en_el_consejo(motor_migrate, motor_app, e, e.mc2)
    doc, v = atado(motor_migrate, e, e.mc1)
    assert ve(motor_app, e.cons, otra, doc, v) == (True, True)  # control: con identidad, sí
    desvincular(motor_migrate, e, otra)
    assert es_del_consejo(motor_migrate, e, otra)
    assert ve(motor_app, e.cons, otra, doc, v) == (False, False)
    assert ve(motor_app, e.cons, e.mc2, doc, v) == (True, True)  # control


def test_la_funcion_nueva_no_la_ejecuta_public(motor_migrate):
    f = "nucleo.preparo_la_orden_del_comprobante(uuid, uuid, uuid)"
    with motor_migrate.connect() as c:
        for rol in ("public", "cem_app"):
            sql = f"select has_function_privilege('{rol}', cast(:f as regprocedure), 'execute')"
            assert c.execute(text(sql), {"f": f}).scalar_one() is False, rol


def test_0062_ida_y_vuelta(base_aparte):  # noqa: F811
    cfg, motor = base_aparte

    def definiciones() -> tuple[str, str, object]:
        with motor.connect() as c:
            sql = ("select pg_get_functiondef('nucleo.documentos_del_consorcio()'::regprocedure),"
                   " pg_get_functiondef("
                   "'nucleo.version_para_descargar(uuid, uuid)'::regprocedure),"
                   " to_regprocedure("
                   "'nucleo.preparo_la_orden_del_comprobante(uuid, uuid, uuid)')")  # fmt: skip
            return tuple(c.execute(text(sql)).one())

    command.upgrade(cfg, "0061")
    antes = definiciones()
    assert antes[2] is None and "preparo_la_orden" not in antes[0] + antes[1]
    command.upgrade(cfg, "0062")
    despues = definiciones()
    assert despues[2] is not None
    assert despues[0].count("preparo_la_orden_del_comprobante") == 2
    assert "preparo_la_orden_del_comprobante" in despues[1]
    command.downgrade(cfg, "0061")
    assert definiciones() == antes
    command.upgrade(cfg, "0062")  # control
    assert definiciones()[2] is not None
