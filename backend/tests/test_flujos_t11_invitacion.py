"""Tanda 11 (19-11-2026, `0112`): abrir una invitación del padrón cuyo vínculo ya no corresponde lo
dice en `paso`, en los mismos dos lugares en que `aceptar_invitacion` (`0025`) la niega. Contra
PostgreSQL 18 y Redis 7 reales. Datos SINTETICOS / NO REAL.
"""

import secrets
from datetime import timedelta

import pytest
from alembic import command
from sqlalchemy import text

from app.identidad import sesiones
from tests.test_identidad import app, csrf, navegador  # noqa: F401  (`app` es un fixture)
from tests.test_identidad_cierre_y_purga import entrar_como
from tests.test_importacion_padron import (  # noqa: F401  (fixtures)
    correo_nuevo,
    edificio,
    escena,
    escena_padron,
    f,
    invitaciones_de,
    pedir_aplicar,
    planilla,
    vinculos_de,
)
from tests.test_vinculo_0014 import _hoy, base_aparte  # noqa: F401  (`base_aparte` es un fixture)

pytestmark = [pytest.mark.db, pytest.mark.redis]
# ruff: noqa: F811

CONSULTAR = "/api/v1/auth/invitacion/consultar"
ACEPTAR = "/api/v1/auth/invitacion/aceptar"
PASO = "nucleo.paso_invitacion(bytea, uuid)"


def importada(e, motor_migrate) -> tuple[str, dict, str]:
    """Una fila del padrón que crea persona, vínculo e invitación que lo nombra. Le pone a la
    invitación un token conocido (la importación no la envía)."""
    hoy = _hoy(motor_migrate, e["cons"])
    correo = correo_nuevo()
    # Desde hace diez días: así puede terminar hoy (el vínculo no termina el día que empieza).
    archivo = planilla(f"2° B,Pia,{correo},ocupante,{f(hoy - timedelta(days=10))},")
    assert pedir_aplicar(e["admin"], e["cons"], archivo).status_code == 200
    [inv] = invitaciones_de(motor_migrate, correo)
    assert inv["vinculo_id"] is not None
    token = secrets.token_urlsafe(32)
    with motor_migrate.begin() as c:
        c.execute(
            text("update nucleo.invitacion set token_hash = :h where id = :i"),
            {"h": sesiones.hash_token(token), "i": inv["id"]},
        )
    return correo, inv, token


def paso(cliente, token: str) -> str:
    r = cliente.post(CONSULTAR, json={"token": token}, headers=csrf(cliente))
    assert r.status_code == 200, r.text
    return r.json()["paso"]


@pytest.mark.parametrize("caso", ["borrado", "terminado", "otro_caracter", "otra_unidad"])
def test_con_su_sesion_el_vinculo_que_no_corresponde_se_dice_al_abrirla(
    app, escena, motor_migrate, caso
):
    e = escena
    correo, inv, token = importada(e, motor_migrate)
    suya = entrar_como(app, motor_migrate, correo)
    assert paso(suya, token) == "aceptar"  # control: antes del cambio, se puede aceptar
    [v] = vinculos_de(motor_migrate, correo)
    with motor_migrate.begin() as c:
        if caso == "borrado":
            c.execute(text("delete from nucleo.vinculo where id = :v"), {"v": v["id"]})
        elif caso == "terminado":
            c.execute(
                text("update nucleo.vinculo set vigente_hasta = :h where id = :v"),
                {"h": _hoy(motor_migrate, e["cons"]), "v": v["id"]},
            )
        else:
            columna = {"otro_caracter": "tipo = 'inquilino'", "otra_unidad": "unidad_id = :u"}[caso]
            c.execute(
                text(f"update nucleo.invitacion set {columna} where id = :i"),
                {"i": inv["id"], "u": e["u1"]},
            )
    assert paso(suya, token) == "vinculo_no_corresponde"
    # Lo que dice la consulta es lo que hace la aceptación.
    r = suya.post(ACEPTAR, json={"token": token}, headers=csrf(suya))
    assert (r.status_code, r.json()["codigo"]) == (409, "invitacion_vinculo_no_corresponde")
    # Sin sesión, como `aceptar_invitacion`, primero se pide entrar: no se le dice a cualquiera.
    assert navegador(app).post(CONSULTAR, json={"token": token}).json()["paso"] == "entrar"


def test_control_un_vinculo_que_termina_manana_todavia_corresponde(app, escena, motor_migrate):
    e = escena
    correo, _, token = importada(e, motor_migrate)
    [v] = vinculos_de(motor_migrate, correo)
    with motor_migrate.begin() as c:
        c.execute(
            text("update nucleo.vinculo set vigente_hasta = :h where id = :v"),
            {"h": _hoy(motor_migrate, e["cons"]) + timedelta(days=1), "v": v["id"]},
        )
    assert paso(entrar_como(app, motor_migrate, correo), token) == "aceptar"


def test_sin_persona_del_correo_se_dice_a_quien_tiene_el_enlace(app, escena, motor_migrate):
    """`0025`: si la persona que nombra la invitación ya no existe, la aceptación responde
    `vinculo_no_corresponde` antes de mirar la sesión; la consulta, lo mismo."""
    e = escena
    correo, _, token = importada(e, motor_migrate)
    sin_sesion = navegador(app)
    assert sin_sesion.post(CONSULTAR, json={"token": token}).json()["paso"] == "entrar"  # control
    with motor_migrate.begin() as c:
        c.execute(
            text("update nucleo.persona set eliminado_en = now() where correo = :c"), {"c": correo}
        )
    assert sin_sesion.post(CONSULTAR, json={"token": token}).json()["paso"] == (
        "vinculo_no_corresponde"
    )
    r = sin_sesion.post(ACEPTAR, json={"token": token, "nombre": "Pia"})
    assert (r.status_code, r.json()["codigo"]) == (409, "invitacion_vinculo_no_corresponde")


def test_las_dos_barreras_miran_solo_las_invitaciones_con_vinculo(motor_migrate):
    """Sin `vinculo_id` (las manuales) nada cambia: el correo sin cuenta crea la cuenta."""
    with motor_migrate.connect() as c:
        codigo = c.execute(
            text(f"select pg_get_functiondef(to_regprocedure('{PASO}'))")
        ).scalar_one()  # fmt: skip
    assert codigo.count("i.vinculo_id is not null") == 2
    assert "return case when persona_sesion is null then 'crear_cuenta'" in codigo


def test_0112_ida_y_vuelta_conserva_los_permisos(base_aparte):
    cfg, motor = base_aparte

    def estado() -> tuple:
        with motor.connect() as c:
            return tuple(c.execute(text(
                "select pg_get_functiondef(p.oid) like '%vinculo_no_corresponde%', p.prosecdef,"
                " has_function_privilege('cem_app', p.oid, 'EXECUTE'),"
                " has_function_privilege('public', p.oid, 'EXECUTE')"
                " from pg_proc p where p.oid = to_regprocedure(:f)"
            ), {"f": PASO}).one())  # fmt: skip

    command.upgrade(cfg, "0111")
    assert estado() == (False, True, True, False)
    command.upgrade(cfg, "0112")
    assert estado() == (True, True, True, False)
    command.downgrade(cfg, "0111")
    assert estado() == (False, True, True, False)
