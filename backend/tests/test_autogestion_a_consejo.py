"""Autogestión → Consejo y ningún paso a Consejo con órdenes de pago sin terminar (`0090`).

Diseño: repositorio CEM, `doc/diseno/Transicion_Autogestion_a_Consejo.md` (D1, D2). Datos
SINTETICOS / PRUEBA / NO REAL. Cada negación tiene su control: con la orden terminada, en otro
consorcio o hacia otro modo, el mismo cambio pasa.
"""

from uuid import UUID

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db import transaccion
from tests.test_cambio_de_modo import periodos
from tests.test_mandatos import otra_persona
from tests.test_mandatos_0012 import alta, baja
from tests.test_modo_operacion import (
    DESDE,
    cambiar,
    consorcio_con_persona,
    decidir,
    decidir_equipo,
    hoy_de,
)
from tests.test_orden_pago_neutra import (
    ESENCIALES,
    PREPARAR,
    cbu_sintetico,
    nueva_sesion,
    nuevo_proveedor,
)
from tests.test_vinculo_0014 import base_aparte  # noqa: F401  (fixture)

pytestmark = pytest.mark.db

ESTADOS = (
    "borrador",
    "en_revision",
    "rechazada",
    "aprobada",
    "en_ejecucion",
    "en_verificacion",
    "pagada",
    "fallida",
    "conciliada",
)
TERMINADOS = {"rechazada", "conciliada"}


def consorcio_en(motor_migrate, motor_app, modo: str) -> dict:
    """Un consorcio en `modo` desde el 01-01-2026, con su administrador y un proveedor."""
    cons, _, prop = consorcio_con_persona(motor_migrate)
    acta = decidir_equipo(motor_app, cons)
    cambiar(motor_app, cons, modo, acta)
    adm = otra_persona(motor_migrate, cons)
    alta(motor_app, cons, prop, acta, persona=adm)
    with motor_migrate.connect() as c:
        mandato = c.execute(
            text("select id from nucleo.mandato where consorcio_id = :c"
                 " and tipo = 'administrador'"), {"c": cons},
        ).scalar_one()  # fmt: skip
    return {
        "cons": cons,
        "prop": prop,
        "adm": adm,
        "mandato": mandato,
        "sesion": nueva_sesion(motor_migrate, adm),
        "proveedor": nuevo_proveedor(motor_migrate, cons, adm, cuenta=cbu_sintetico(7)),
    }


def orden_en(motor_migrate, motor_app, e: dict, estado: str) -> UUID:
    """Una orden preparada por el administrador y llevada a `estado` como dueño de la base: los
    recorridos de cada estado se prueban en las pruebas de órdenes; aquí importa el estado."""
    params = {**ESENCIALES, "proveedor": e["proveedor"], "actor": e["adm"], "origen": "api",
              "sesion": e["sesion"]}  # fmt: skip
    with transaccion(motor_app, persona_id=e["adm"], consorcio_id=e["cons"]) as c:
        orden = c.execute(text(PREPARAR), params).scalar_one()
    with motor_migrate.begin() as c:
        c.execute(text("alter table nucleo.orden_pago disable trigger transicion_orden_pago"))
        # Las columnas que el estado exige (0045: el motivo del rechazo; 0050: desde cuándo
        # está aprobada).
        c.execute(
            text("update nucleo.orden_pago set estado = cast(:e as nucleo.estado_orden_pago),"
                 " motivo_rechazo = case when :e = 'rechazada'"
                 " then 'Rechazo SINTETICO de prueba' end,"
                 " aprobada_en = case when :e in ('borrador', 'en_revision', 'rechazada')"
                 " then null else now() end where id = :o"),
            {"e": estado, "o": orden},
        )  # fmt: skip
        c.execute(text("alter table nucleo.orden_pago enable trigger transicion_orden_pago"))
    with motor_migrate.connect() as c:
        leido = c.execute(text("select estado::text from nucleo.orden_pago where id = :o"),
                          {"o": orden}).scalar_one()  # fmt: skip
    assert leido == estado
    return orden


def termina_la_administracion(motor_app, e: dict) -> None:
    baja(motor_app, e["cons"], None, e["mandato"], "renuncia", instrumento="Nota de renuncia",
         descripcion="Renuncia SINTETICA", origen="soporte")  # fmt: skip


def _hint(err: pytest.ExceptionInfo) -> str:
    return err.value.orig.diag.message_hint


def test_autogestion_pasa_a_consejo_con_la_administracion_terminada(motor_migrate, motor_app):
    e = consorcio_en(motor_migrate, motor_app, "autogestion")
    hoy = hoy_de(motor_app, e["cons"])
    nueva = decidir(motor_app, e["cons"], e["prop"])
    with pytest.raises(DBAPIError) as err:
        cambiar(motor_app, e["cons"], "consejo", nueva, hoy)
    assert _hint(err) == "transicion_requiere_fin_de_mandato"
    assert periodos(motor_migrate, e["cons"]) == [("autogestion", DESDE, None)]
    # Control: con la renuncia registrada, el mismo cambio pasa y la historia queda entera.
    termina_la_administracion(motor_app, e)
    cambiar(motor_app, e["cons"], "consejo", nueva, hoy)
    assert periodos(motor_migrate, e["cons"]) == [("autogestion", DESDE, hoy),
                                                  ("consejo", hoy, None)]  # fmt: skip
    # Soporte no escribe eventos de clase B (0043, S2b): la evidencia es la fila del modo nuevo,
    # con su decisión, y el período anterior cerrado en la suya.
    with motor_migrate.connect() as c:
        eventos, decision = c.execute(
            text("select (select count(*) from auditoria.eventos where consorcio_id = :c"
                 " and accion = 'consorcio.modo.cambiar'),"
                 " (select decision_id from nucleo.modo_operacion where consorcio_id = :c"
                 " and vigente_hasta is null)"), {"c": e["cons"]},
        ).one()  # fmt: skip
    assert (eventos, decision) == (0, nueva)


@pytest.mark.parametrize("modo", ["autogestion", "administrador_aumentado"])
@pytest.mark.parametrize("estado", ESTADOS)
def test_a_consejo_no_se_pasa_con_ordenes_sin_terminar(motor_migrate, motor_app, modo, estado):
    e = consorcio_en(motor_migrate, motor_app, modo)
    orden_en(motor_migrate, motor_app, e, estado)
    termina_la_administracion(motor_app, e)
    hoy = hoy_de(motor_app, e["cons"])
    nueva = decidir(motor_app, e["cons"], e["prop"])
    if estado in TERMINADOS:
        cambiar(motor_app, e["cons"], "consejo", nueva, hoy)
        assert periodos(motor_migrate, e["cons"])[-1] == ("consejo", hoy, None)
        return
    with pytest.raises(DBAPIError) as err:
        cambiar(motor_app, e["cons"], "consejo", nueva, hoy)
    assert _hint(err) == "transicion_con_ordenes_abiertas"
    assert periodos(motor_migrate, e["cons"]) == [(modo, DESDE, None)]
    with motor_migrate.connect() as c:
        quedo = c.execute(
            text("select estado::text from nucleo.orden_pago where consorcio_id = :c"),
            {"c": e["cons"]},
        ).scalar_one()
    assert quedo == estado  # nunca se cierra ni se rechaza de oficio


def test_la_orden_abierta_de_otro_consorcio_no_frena(motor_migrate, motor_app):
    ajeno = consorcio_en(motor_migrate, motor_app, "autogestion")
    orden_en(motor_migrate, motor_app, ajeno, "aprobada")
    e = consorcio_en(motor_migrate, motor_app, "autogestion")
    termina_la_administracion(motor_app, e)
    hoy = hoy_de(motor_app, e["cons"])
    cambiar(motor_app, e["cons"], "consejo", decidir(motor_app, e["cons"], e["prop"]), hoy)
    assert periodos(motor_migrate, e["cons"])[-1] == ("consejo", hoy, None)


def test_hacia_otro_modo_la_orden_abierta_no_frena(motor_migrate, motor_app):
    """D2 frena solo el paso a Consejo: en AA y AG las órdenes siguen su curso."""
    e = consorcio_en(motor_migrate, motor_app, "autogestion")
    orden_en(motor_migrate, motor_app, e, "aprobada")
    termina_la_administracion(motor_app, e)
    hoy = hoy_de(motor_app, e["cons"])
    cambiar(motor_app, e["cons"], "administrador_aumentado",
            decidir(motor_app, e["cons"], e["prop"]), hoy)  # fmt: skip
    assert periodos(motor_migrate, e["cons"])[-1] == ("administrador_aumentado", hoy, None)


def test_0090_ida_y_vuelta(base_aparte):  # noqa: F811
    cfg, motor = base_aparte
    fuente = text("select prosrc from pg_proc where oid = 'nucleo.cambiar_modo(nucleo.tipo_modo,"
                  " uuid, date, uuid, text)'::regprocedure")  # fmt: skip
    command.upgrade(cfg, "0090")
    with motor.connect() as c:
        texto = c.execute(fuente).scalar_one()
    assert "transicion_con_ordenes_abiertas" in texto and "('autogestion', 'consejo')" in texto
    assert "-- S2b" in texto  # la marca de 0043 sigue: se cambió la definición vigente
    command.downgrade(cfg, "-1")
    with motor.connect() as c:
        texto = c.execute(fuente).scalar_one()
    # Control: la definición de 0027 vuelve entera, con su diagrama sin AG → Consejo.
    assert "transicion_con_ordenes_abiertas" not in texto
    assert "('autogestion', 'consejo')" not in texto and "transicion_no_admitida" in texto
    assert "-- S2b" in texto
    command.upgrade(cfg, "0090")
