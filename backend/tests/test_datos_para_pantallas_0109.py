"""Datos para que la pantalla no ofrezca un acto que la base va a negar (`0109`; informe de fricción
del 17-11-2026, criterio 8, tanda de backend).

Cada dato se prueba contra el acto que predice: lo que dice la lectura es lo que después responde
la ruta que decide, con su control. Las invitaciones (C8G-11, C8G-12) se prueban junto a sus
aceptaciones en `test_invitaciones.py`, `test_autoinvitacion.py` y
`test_mandatos_invitaciones.py`; el padrón (C8G-13) en `test_vinculo_fin_0020.py`; la invitación
importada (C8G-10) en `test_importacion_padron.py`; la solicitud formal (C8P-08) en
`test_solicitud_formal_0108.py`.

Contra PostgreSQL real. Datos SINTETICOS / PRUEBA / NO REAL.
"""

from datetime import timedelta

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db import transaccion
from tests.test_condicion_habilitante_consejo import consejo  # noqa: F401  (fixture)
from tests.test_contexto_mandatos import cliente
from tests.test_mandatos import otra_persona
from tests.test_revision_mandato import cortar, pendiente, revisar_http  # noqa: F401  (fixture)
from tests.test_rf50_configurable_0104 import configurar
from tests.test_rf50_salteo_0106 import bloqueado, configurar_salteo, saltear
from tests.test_rutas_egresos import ORDEN, codigo, get, post, preparar
from tests.test_rutas_egresos import e as e  # noqa: F401  (es un fixture)
from tests.test_vinculo_0014 import base_aparte  # noqa: F401  (es un fixture)

pytestmark = [pytest.mark.db, pytest.mark.redis]
# ruff: noqa: F811

FUNCIONES = (
    "nucleo.salteos_documentacion_lectura(uuid, uuid, text, uuid, text, uuid)",
    "nucleo.condicion_consejo_hoy(uuid)",
    "nucleo.paso_invitacion(bytea, uuid)",
    "nucleo.paso_invitacion_mandato(bytea, uuid)",
)


def _definiciones(motor) -> dict:
    with motor.connect() as c:
        return {
            f: c.execute(
                text("select pg_get_functiondef(p.oid), coalesce(p.proacl::text, ''), p.prosecdef"
                     " from pg_proc p where p.oid = to_regprocedure(:f)"), {"f": f},
            ).one_or_none()
            for f in FUNCIONES
        }  # fmt: skip


def test_0109_ida_y_vuelta_deja_todo_como_estaba(base_aparte):
    cfg, motor = base_aparte
    command.upgrade(cfg, "0108")
    antes = _definiciones(motor)
    command.upgrade(cfg, "0109")
    con = _definiciones(motor)
    lectura, nuevas = FUNCIONES[0], FUNCIONES[1:]
    # Control: la 0109 cambió la lectura (mismo permiso y `security definer`) y creó las nuevas.
    assert antes[lectura] is not None and con[lectura][0] != antes[lectura][0]
    assert con[lectura][1:] == antes[lectura][1:] and "cem_app=X" in con[lectura][1]
    assert "de_quien_prepara boolean" in con[lectura][0]
    assert all(antes[f] is None and "cem_app=X" in con[f][1] and con[f][2] for f in nuevas)
    command.downgrade(cfg, "0108")
    assert _definiciones(motor) == antes


# Salteo por urgencia: C8P-02, C8P-03 y el comentario 40 del propietario -------------------------


def _lista(e) -> dict:
    r = get(e, e.adm, "proveedores")
    assert r.status_code == 200, r.text
    return r.json()


def _de_la_lista(e) -> tuple[bool, bool]:
    """Si el proveedor de la escena figura con un salteo que le sirve a la administración, y con
    uno que no le sirve porque lo registró ella."""
    lista = _lista(e)
    p = str(e.proveedor)
    return p in lista["con_salteo"], p in lista["salteo_de_quien_prepara"]


def test_propio_dice_si_lo_registro_la_cuenta_que_lee(e):
    bloqueado(e)
    r = saltear(e, e.mc1)
    assert r.status_code == 201, r.text
    assert r.json()["propio"] is True
    ficha = f"consulta-proveedores/{e.proveedor}"
    [f] = get(e, e.mc1, ficha).json()["salteos"]
    assert f["propio"] is True
    # Lo que predice es lo que responde la base: esa cuenta no registra otro.
    assert codigo(saltear(e, e.mc1)) == (409, "salteo_vigente")
    # Control: para otro miembro del Consejo no es propio, y registra el suyo.
    [f] = get(e, e.mc2, ficha).json()["salteos"]
    assert f["propio"] is False
    assert saltear(e, e.mc2).status_code == 201
    assert [s["propio"] for s in get(e, e.mc2, ficha).json()["salteos"]] == [False, True]
    # La ficha de quien prepara tampoco los ve propios, y no dice quién es la persona.
    salteos = get(e, e.adm, f"proveedores/{e.proveedor}").json()["salteos"]
    assert [s["propio"] for s in salteos] == [False, False]
    assert str(e.mc1) not in str(salteos) and str(e.mc2) not in str(salteos)


def test_la_lista_dice_si_quien_prepara_tiene_un_salteo_que_le_sirve(motor_app, e):
    bloqueado(e)
    assert str(e.proveedor) in _lista(e)["documentacion_faltante"]
    assert _de_la_lista(e) == (False, False)  # bloqueado, sin salteo
    # La administración registra el suyo: la base no lo toma para la orden que ella prepara.
    configurar_salteo(motor_app, e, roles=["administrador", "miembro_consejo"])
    assert saltear(e, e.adm).status_code == 201
    assert _de_la_lista(e) == (False, True)
    r = post(e, e.adm, "ordenes-pago", ORDEN | {"proveedor_id": str(e.proveedor)})
    assert codigo(r) == (422, "salteo_de_quien_prepara")
    # Control: con el de un miembro del Consejo, la lista dice que sirve y la orden pasa.
    assert saltear(e, e.mc1).status_code == 201
    assert _de_la_lista(e) == (True, False)
    preparar(e)
    # El del Consejo se usó; queda el de la administración, que no le sirve.
    assert _de_la_lista(e) == (False, True)


def test_la_misma_identidad_humana_tampoco_le_sirve_en_la_lista(motor_migrate, e):
    bloqueado(e)
    assert saltear(e, e.mc1).status_code == 201
    assert _de_la_lista(e) == (True, False)  # control: de otra persona
    hecha = preparar(e, proveedor_id=str(e.otro))
    with motor_migrate.begin() as c:
        identidad = c.execute(
            text("select identidad_preparo from nucleo.orden_pago_version where orden_id = :o"),
            {"o": hecha},
        ).scalar_one()
        c.execute(text("alter table nucleo.salteo_documentacion_proveedor disable trigger user"))
        c.execute(text("update nucleo.salteo_documentacion_proveedor set identidad = :i"
                       " where consorcio_id = :c"), {"i": identidad, "c": e.cons})  # fmt: skip
        c.execute(text("alter table nucleo.salteo_documentacion_proveedor enable trigger user"))
    assert _de_la_lista(e) == (False, True)
    r = post(e, e.adm, "ordenes-pago", ORDEN | {"proveedor_id": str(e.proveedor)})
    assert codigo(r) == (422, "salteo_de_quien_prepara")


def test_en_modo_avisar_nada_bloquea_y_la_lista_no_marca_salteos(motor_app, e):
    bloqueado(e)
    assert saltear(e, e.mc1).status_code == 201
    assert _de_la_lista(e) == (True, False)  # control: en bloquear
    configurar(motor_app, e, modo="avisar")
    assert _de_la_lista(e) == (False, False)
    preparar(e)  # nada bloquea: la orden pasa sin usar el salteo


# Mantener un mandato del Consejo: C8G-04 ---------------------------------------------------------


def _detalle(e, mandato) -> dict:
    with cliente(e["adm"]) as c:
        r = c.get(f"/api/v1/consorcios/{e['cons']}/mandatos/{mandato}")
    assert r.status_code == 200, r.text
    return r.json()


def test_el_detalle_dice_si_hoy_cumple_la_condicion(pendiente, motor_migrate):
    e = pendiente
    d = _detalle(e, e["m"])
    assert (d["estado"], d["condicion_cumplida_hoy"]) == ("pendiente_revision", True)
    # Otro estado: nulo (la revisión no se pide).
    assert _detalle(e, e["m_mc2"])["condicion_cumplida_hoy"] is None
    # La 6° F terminó hoy: ya no la cumple, y la revisión que lo mantiene se niega.
    cortar(motor_migrate, e["v_nuevo"], e["hoy"])
    d = _detalle(e, e["m"])
    assert (d["estado"], d["condicion_cumplida_hoy"]) == ("pendiente_revision", False)
    r = revisar_http(e["adm"], e["cons"], e["m"], e["acta"], e["hoy"] + timedelta(days=1))
    assert (r.status_code, r.json()["codigo"]) == (422, "sin_condicion_habilitante")
    # Control: con el vínculo de vuelta, la cumple y la revisión pasa.
    cortar(motor_migrate, e["v_nuevo"], None)
    assert _detalle(e, e["m"])["condicion_cumplida_hoy"] is True
    r = revisar_http(e["adm"], e["cons"], e["m"], e["acta"], e["hoy"] + timedelta(days=1))
    assert r.status_code == 201, r.text


def test_la_condicion_pide_mandatos_ver(pendiente, motor_migrate, motor_app):
    e = pendiente
    sql = text("select nucleo.condicion_consejo_hoy(:m)")
    nadie = otra_persona(motor_migrate, e["cons"])
    with (
        pytest.raises(DBAPIError) as error,
        transaccion(motor_app, persona_id=nadie, consorcio_id=e["cons"]) as c,
    ):
        c.execute(sql, {"m": e["m"]})
    assert error.value.orig.sqlstate == "42501"
    # Control: con `mandatos.ver`, responde.
    with transaccion(motor_app, persona_id=e["adm"], consorcio_id=e["cons"]) as c:
        assert c.execute(sql, {"m": e["m"]}).scalar_one() is True
