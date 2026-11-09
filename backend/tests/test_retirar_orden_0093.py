"""Retirar una orden de pago mientras nadie la aprobó (`0092`, `0093`). Comentario 23 de la
revisión de pantallas, decisión del propietario del 09-11-2026: «solo mientras nadie la aprobó,
queda registrado quién y cuándo, y sale de la bandeja». Datos SINTETICOS / PRUEBA / NO REAL."""

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db import transaccion
from tests.conftest import alembic_config
from tests.test_circuito_pagos_0072 import con, consulta, ficha, fila, ids
from tests.test_mandatos import otra_persona
from tests.test_mandatos_0012 import alta
from tests.test_orden_pago_neutra import (
    APROBAR,
    ENVIAR,
    MODIFICAR,
    Escena,
    escena_economica,
    eventos,
    exigir,
    huella,
    llamar,
    niega,
    nueva_sesion,
    orden,
    preparada,
)
from tests.test_pago_manual_0055 import acta
from tests.test_rutas_egresos import codigo, post
from tests.test_vinculo_0014 import base_aparte  # noqa: F401  (es un fixture)

pytestmark = pytest.mark.db

RETIRAR = "select nucleo.retirar_orden_pago(:orden, :version, :actor, :origen, :sesion)"


@pytest.fixture
def e(motor_migrate, motor_app) -> Escena:
    return escena_economica(motor_migrate, motor_app)


def otro_que_prepara(motor_migrate, motor_app, e):
    """Otra persona del equipo con `egresos.preparar_orden`, que no preparó la orden."""
    x = otra_persona(motor_migrate, e.cons)
    alta(motor_app, e.cons, e.prop, acta(motor_migrate, e), tipo="miembro_consejo", persona=x)
    con(motor_migrate, e, x, "egresos.preparar_orden")
    e.sesion[x] = nueva_sesion(motor_migrate, x)
    return x


# En la base ----------------------------------------------------------------------------------


@pytest.mark.parametrize("en_revision", [False, True])
def test_quien_la_preparo_la_retira_y_queda_quien_y_cuando(motor_migrate, e, en_revision):
    o = preparada(motor_migrate, e, en_revision=en_revision)
    llamar(motor_migrate, e, e.adm, RETIRAR, orden=o, version=1)
    fila_orden = orden(motor_migrate, o)
    assert fila_orden["estado"] == "retirada"
    assert fila_orden["retirada_por"] == e.adm and fila_orden["retirada_en"] is not None
    assert fila_orden["aprobada_en"] is None
    ultimo = eventos(motor_migrate, o)[-1]
    assert ultimo["accion"] == "consorcio.orden_pago.retirar"
    assert ultimo["actor_persona_id"] == e.adm
    assert ultimo["despues"] == {"estado": "retirada", "version": 1}
    # Una orden retirada no sale de ahí: ni se envía, ni se vuelve a retirar.
    niega(motor_migrate, e, e.adm, RETIRAR, "estado_invalido", orden=o, version=1)
    niega(motor_migrate, e, e.adm, ENVIAR, "estado_invalido", orden=o, version=1)


def test_con_una_aprobacion_ya_no_se_retira(motor_migrate, motor_app, e):
    exigir(motor_app, e.cons, 2)
    o = preparada(motor_migrate, e, en_revision=True)
    llamar(motor_migrate, e, e.mc1, APROBAR, orden=o, version=1, huella=huella(motor_migrate, o, 1))
    assert orden(motor_migrate, o)["estado"] == "en_revision"  # falta la segunda
    niega(motor_migrate, e, e.adm, RETIRAR, "orden_con_aprobaciones", orden=o, version=1)
    assert orden(motor_migrate, o)["estado"] == "en_revision"


def test_aprobada_no_se_retira(motor_migrate, e):
    o = preparada(motor_migrate, e, en_revision=True)
    llamar(motor_migrate, e, e.mc1, APROBAR, orden=o, version=1, huella=huella(motor_migrate, o, 1))
    assert orden(motor_migrate, o)["estado"] == "aprobada"
    niega(motor_migrate, e, e.adm, RETIRAR, "estado_invalido", orden=o, version=1)


def test_solo_la_retira_quien_la_preparo(motor_migrate, motor_app, e):
    o = preparada(motor_migrate, e)
    otro = otro_que_prepara(motor_migrate, motor_app, e)
    niega(motor_migrate, e, otro, RETIRAR, "retirar_solo_quien_preparo", orden=o, version=1)
    # Sin la facultad de preparar, la base no dice nada de la orden.
    niega(motor_migrate, e, e.mc1, RETIRAR, "42501", orden=o, version=1)
    assert orden(motor_migrate, o)["estado"] == "borrador"
    llamar(motor_migrate, e, e.adm, RETIRAR, orden=o, version=1)  # control


def test_con_la_version_vieja_no_se_retira(motor_migrate, e):
    o = preparada(motor_migrate, e)
    llamar(motor_migrate, e, e.adm, MODIFICAR, orden=o, version=1, importe=2_000_000)
    niega(motor_migrate, e, e.adm, RETIRAR, "version_desactualizada", orden=o, version=1)
    llamar(motor_migrate, e, e.adm, RETIRAR, orden=o, version=2)  # control


def test_no_pide_el_gesto(motor_migrate, e):
    o = preparada(motor_migrate, e)
    sin_gesto = nueva_sesion(motor_migrate, e.adm, totp_min_atras=None)
    llamar(motor_migrate, e, e.adm, RETIRAR, sesion=sin_gesto, orden=o, version=1)
    assert orden(motor_migrate, o)["estado"] == "retirada"


def test_cem_app_no_escribe_el_retiro_a_mano(motor_app, motor_migrate, e):
    o = preparada(motor_migrate, e)
    with (
        pytest.raises(DBAPIError),
        transaccion(motor_app, persona_id=e.adm, consorcio_id=e.cons) as c,
    ):
        c.execute(
            text("update nucleo.orden_pago set estado = 'retirada', retirada_por = :p,"
                 " retirada_en = now() where id = :o"),
            {"p": e.adm, "o": o},
        )  # fmt: skip
    assert orden(motor_migrate, o)["estado"] == "borrador"


def test_retirada_va_con_quien_y_cuando(motor_migrate, e):
    o = preparada(motor_migrate, e)
    with pytest.raises(DBAPIError) as error, motor_migrate.begin() as c:
        c.execute(text("update nucleo.orden_pago set estado = 'retirada' where id = :o"), {"o": o})
    assert error.value.orig.diag.sqlstate == "23514"


# Por la API ------------------------------------------------------------------------------------


def test_la_ruta_retira_y_la_ficha_dice_quien_y_cuando(motor_migrate, e):
    o = preparada(motor_migrate, e, en_revision=True)
    antes = ficha(e, e.adm, o)
    assert antes["puede_retirar"] is True and antes["retiro"] is None
    # Quien aprueba no la retira: ni se le ofrece.
    assert ficha(e, e.mc1, o)["puede_retirar"] is False
    assert fila(e, e.mc1, o) is not None
    r = post(e, e.adm, f"ordenes-pago/{o}/retirar", {"version_esperada": 1})
    assert r.status_code == 200, r.json()
    despues = ficha(e, e.adm, o)
    assert despues["situacion"] == "retirada" and despues["paso"] is None
    assert despues["puede_retirar"] is False and despues["puede_enviar"] is False
    assert despues["retiro"]["retirada_por"] == str(e.adm)
    assert despues["retiro"]["retirada_en"] is not None
    assert despues["retiro"]["retirada_por_nombre"]
    # Sale de la bandeja de todos y queda en la consulta.
    assert fila(e, e.adm, o) is None and fila(e, e.mc1, o) is None
    assert str(o) in ids(consulta(e, e.adm, "?filtro=retirados"))
    assert str(o) not in ids(consulta(e, e.adm, "?filtro=rechazados"))


def test_la_ruta_dice_por_que_no(motor_migrate, motor_app, e):
    exigir(motor_app, e.cons, 2)
    o = preparada(motor_migrate, e, en_revision=True)
    llamar(motor_migrate, e, e.mc1, APROBAR, orden=o, version=1, huella=huella(motor_migrate, o, 1))
    assert ficha(e, e.adm, o)["puede_retirar"] is False
    r = post(e, e.adm, f"ordenes-pago/{o}/retirar", {"version_esperada": 1})
    assert codigo(r) == (409, "orden_con_aprobaciones")
    otro = otro_que_prepara(motor_migrate, motor_app, e)
    b = preparada(motor_migrate, e)
    assert ficha(e, otro, b)["puede_retirar"] is False
    r = post(e, otro, f"ordenes-pago/{b}/retirar", {"version_esperada": 1})
    assert codigo(r) == (403, "retirar_solo_quien_preparo")


# Migración -------------------------------------------------------------------------------------


def test_0093_no_baja_con_ordenes_retiradas(motor_migrate, e):
    o = preparada(motor_migrate, e)
    llamar(motor_migrate, e, e.adm, RETIRAR, orden=o, version=1)
    with pytest.raises(DBAPIError) as error:
        command.downgrade(alembic_config(), "0091")
    assert error.value.orig.diag.message_hint == "downgrade_con_datos"
    # Control: la migración no bajó y la orden sigue retirada.
    assert orden(motor_migrate, o)["estado"] == "retirada"


def test_0093_baja_y_sube(base_aparte):  # noqa: F811
    cfg, motor = base_aparte
    consultas = {
        "transiciones": "select count(*) from nucleo.transicion_orden_pago"
        " where hacia = 'retirada'",
        "funcion": "select count(*) from pg_proc where proname = 'retirar_orden_pago'",
        "columnas": "select count(*) from information_schema.columns where table_schema = 'nucleo'"
        " and table_name = 'orden_pago' and column_name in ('retirada_por', 'retirada_en')",
        "modo": "select count(*) from pg_proc where proname = 'cambiar_modo'"
        " and prosrc like '%''retirada''%'",
    }

    def contar() -> dict[str, int]:
        with motor.connect() as c:
            return {k: c.execute(text(q)).scalar_one() for k, q in consultas.items()}

    command.upgrade(cfg, "0093")
    assert contar() == {"transiciones": 2, "funcion": 1, "columnas": 2, "modo": 1}
    command.downgrade(cfg, "0091")
    assert contar() == {"transiciones": 0, "funcion": 0, "columnas": 0, "modo": 0}
    command.upgrade(cfg, "0093")
    assert contar() == {"transiciones": 2, "funcion": 1, "columnas": 2, "modo": 1}
