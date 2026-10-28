"""Orden de cerrojos de la purga y de la baja del TOTP, y la identidad en el plan de la
supresión (0041; residuales de la revisión de 2d-3).

Las pruebas de orden tienen una transacción con la primera fila del orden tomada, esperan a que
la otra quede trabada en esa transacción (filtrando por su `pid`, no en todo el servidor) y
controlan que la fila siguiente del orden sigue libre: con el orden viejo ya estaba tomada.
"""

import os
import threading
import time
from datetime import timedelta

import pytest
from alembic import command
from sqlalchemy import text

from app.identidad import sesiones
from tests.conftest import verificar_en_el_consorcio
from tests.test_identidad import app, csrf  # noqa: F401  (`app` es un fixture)
from tests.test_identidad_cierre_y_purga import _insertar_enlace, _insertar_sesion
from tests.test_identidad_tabla import nueva_persona
from tests.test_privacidad_2d1 import con_url, confiable  # noqa: F401
from tests.test_privacidad_2d3 import ESPERAS, INTERVALO, confirmado, otro_consorcio, plan_de, punto
from tests.test_segundo_factor import BASE, con_factor, reloj  # noqa: F401
from tests.test_vinculo_0014 import base_aparte, escena  # noqa: F401

pytestmark = [pytest.mark.db, pytest.mark.redis]

IDENTIDAD = "identidad humana de la cuenta"


def esperando_a(conn, pid: int) -> int:
    """Cuántos cerrojos sin conceder esperan a la transacción del `pid`."""
    return conn.execute(
        text(
            "select count(*) from pg_locks w join pg_locks t on t.locktype = 'transactionid'"
            " and t.transactionid = w.transactionid and t.granted"
            " where w.locktype = 'transactionid' and not w.granted and t.pid = :p"
        ),
        {"p": pid},
    ).scalar_one()


def esperar_trabado(motor_migrate, pid: int) -> int:
    with motor_migrate.connect() as c:
        for _ in range(ESPERAS):
            n = esperando_a(c, pid)
            if n:
                return n
            time.sleep(INTERVALO)
    return 0


def test_la_purga_toma_el_codigo_antes_que_el_enlace(motor_migrate, datos):
    persona, _ = nueva_persona(motor_migrate)
    viejo = sesiones.RETENCION_ACCESO + timedelta(days=1)
    with motor_migrate.begin() as c:
        enlace = _insertar_enlace(c, persona, viejo)
        mes = timedelta(days=30)
        sesion = _insertar_sesion(c, persona, viejo + 2 * mes, viejo + mes, viejo)
        c.execute(text("insert into nucleo.codigo_acceso (enlace_id, persona_id, codigo_hash,"
                       " vence_en) values (:e, :p, :h, now() + interval '5 minutes')"),
                  {"e": enlace, "p": persona, "h": os.urandom(32)})  # fmt: skip
    resultado = {}

    def purgar():
        with motor_migrate.begin() as c:
            resultado["fila"] = c.execute(
                text("select sesiones, enlaces from nucleo.purgar_identidad(:r)"),
                {"r": sesiones.RETENCION_ACCESO},
            ).one()

    with motor_migrate.connect() as login:
        pid = login.execute(text("select pg_backend_pid()")).scalar_one()
        login.execute(text("select 1 from nucleo.codigo_acceso where enlace_id = :e for update"),
                      {"e": enlace})  # fmt: skip
        hilo = threading.Thread(target=purgar)
        hilo.start()
        try:
            assert esperar_trabado(motor_migrate, pid) == 1  # control: la purga espera el código
            # Lo que tomaría el login a continuación: el enlace y la sesión siguen libres.
            login.execute(text("select 1 from nucleo.enlace_acceso where id = :e"
                               " for update nowait"), {"e": enlace})  # fmt: skip
            login.execute(text("select 1 from nucleo.sesion where id = :s for update nowait"),
                          {"s": sesion})  # fmt: skip
        finally:
            login.rollback()
            hilo.join()
    assert resultado["fila"].enlaces >= 1 and resultado["fila"].sesiones >= 1
    with motor_migrate.connect() as c:
        assert c.execute(text("select (select count(*) from nucleo.enlace_acceso where id = :e)"
                              " + (select count(*) from nucleo.codigo_acceso where enlace_id"
                              " = :e)"), {"e": enlace}).scalar_one() == 0  # fmt: skip


def test_la_baja_del_totp_toma_el_totp_antes_que_la_recuperacion(app, motor_migrate, reloj):  # noqa: F811
    c, _, _, correo = con_factor(app, motor_migrate, reloj)
    with motor_migrate.connect() as m:
        persona = m.execute(text("select id from nucleo.persona where correo = :c"),
                            {"c": correo}).scalar_one()  # fmt: skip
    resultado = {}

    def dar_de_baja():
        resultado["r"] = c.post(f"{BASE}/baja", headers=csrf(c))

    with motor_migrate.connect() as confirmar:
        pid = confirmar.execute(text("select pg_backend_pid()")).scalar_one()
        confirmar.execute(text("select 1 from nucleo.factor_totp where persona_id = :p"
                               " for update"), {"p": persona})  # fmt: skip
        hilo = threading.Thread(target=dar_de_baja)
        hilo.start()
        try:
            assert esperar_trabado(motor_migrate, pid) == 1  # control: la baja espera el TOTP
            # Lo que tomaría `confirmar` a continuación: los códigos de recuperación, libres.
            confirmar.execute(text("select 1 from nucleo.codigo_recuperacion where persona_id = :p"
                                   " for update nowait"), {"p": persona})  # fmt: skip
        finally:
            confirmar.rollback()
            hilo.join()
    assert resultado["r"].status_code == 204, resultado["r"].text
    with motor_migrate.connect() as m:
        assert m.execute(text("select (select count(*) from nucleo.factor_totp where persona_id"
                              " = :p) + (select count(*) from nucleo.codigo_recuperacion where"
                              " persona_id = :p)"), {"p": persona}).scalar_one() == 0  # fmt: skip


def test_el_plan_cuenta_la_identidad_humana_por_alcance(confiable, motor_migrate, con_url, capsys):  # noqa: F811
    # El caso de la cuenta, antes que el de consorcio (que anonimiza la invitación del correo).
    k_cuenta = confirmado(capsys, motor_migrate, confiable["correo"])
    k_cons = confirmado(capsys, motor_migrate, confiable["correo"], "supresion",
                        "--consorcio", str(confiable["cons"]))  # fmt: skip
    assert punto(plan_de(k_cuenta), IDENTIDAD)["filas"] == 0  # control: sin verificar, nada
    cons2 = otro_consorcio(motor_migrate, confiable["inq"], confiable["correo"])
    verificar_en_el_consorcio(motor_migrate, confiable["cons"], confiable["inq"])
    verificar_en_el_consorcio(motor_migrate, cons2, confiable["inq"])
    cuenta, de_cons = punto(plan_de(k_cuenta), IDENTIDAD), punto(plan_de(k_cons), IDENTIDAD)
    assert (cuenta["tratamiento"], cuenta["filas"]) == ("conservar", 2)
    assert (de_cons["tratamiento"], de_cons["filas"]) == ("conservar", 1)


def test_0041_ida_y_vuelta(base_aparte):  # noqa: F811
    cfg, motor = base_aparte
    command.upgrade(cfg, "0041")

    def estado() -> tuple[bool, bool]:
        with motor.connect() as c:
            purga, supresion = (
                c.execute(text("select pg_get_functiondef(cast(:f as regprocedure))"),
                          {"f": f}).scalar_one()
                for f in ("nucleo.purgar_identidad(interval)",
                          "plataforma.privacidad_supresion(plataforma.caso_privacidad, boolean)")
            )  # fmt: skip
        return "delete from nucleo.codigo_acceso" in purga, IDENTIDAD in supresion

    assert estado() == (True, True)
    command.downgrade(cfg, "0040")
    assert estado() == (False, False)
    command.upgrade(cfg, "0041")  # control: vuelve a subir limpio
    assert estado() == (True, True)
