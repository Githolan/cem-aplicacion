"""Separaciones entre seres humanos (A13-C, migración 0026): la API y la segunda barrera.

Diseño: decisiones del propietario del 24-10-2026 (repositorio CEM, `doc/diseno/H2b_6_Diseno.md`).
Dos cuentas de la misma identidad verificada son la misma persona para A4, A7 y A15; si a una
parte le falta la identidad verificada en el consorcio, se niega. La base lo exige aunque el
pedido no pase por el evaluador. A4 al quitar (baja, desasociar) sigue por cuenta: frenado y
presentado al propietario, porque la regla fuerte bloquearía sacar una cuenta sin verificar.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db import transaccion
from tests.conftest import misma_identidad, verificar_en_el_consorcio
from tests.test_contexto_mandatos import denegaciones
from tests.test_mandatos import otra_persona, vinculo_confirmado
from tests.test_mandatos_0012 import alta, baja
from tests.test_mandatos_rutas import alta_http, escena  # noqa: F401  (`escena` es un fixture)
from tests.test_modo_operacion import consorcio_con_persona, decidir_equipo

pytestmark = [pytest.mark.db, pytest.mark.redis]


def ultimo_motivo(motor_app, e, persona) -> str:
    return denegaciones(motor_app, e["cons"], e["mc2"], persona)[-1]["motivo"]


def _hint(e: pytest.ExceptionInfo) -> str:
    return e.value.orig.diag.message_hint


# La API ---------------------------------------------------------------------------------------


def test_alta_a_una_cuenta_sin_identidad_verificada_se_niega(escena, motor_migrate, motor_app):  # noqa: F811
    e = escena
    nueva = otra_persona(motor_migrate, e["cons"], verificada=False)
    vinculo_confirmado(motor_app, e["cons"], nueva)
    r = alta_http(e["mc1"], e["cons"], e["acta"], persona_id=nueva)
    assert (r.status_code, r.json()["codigo"]) == (403, "sin_permiso")
    assert ultimo_motivo(motor_app, e, e["mc1"]) == "identidad_no_verificada"
    # Control: verificada por soporte, el mismo pedido pasa.
    verificar_en_el_consorcio(motor_migrate, e["cons"], nueva)
    assert alta_http(e["mc1"], e["cons"], e["acta"], persona_id=nueva).status_code == 201


def test_alta_a_otra_cuenta_del_mismo_ser_humano_se_niega(escena, motor_migrate, motor_app):  # noqa: F811
    e = escena
    segunda = otra_persona(motor_migrate, e["cons"], verificada=False)
    vinculo_confirmado(motor_app, e["cons"], segunda)
    misma_identidad(motor_migrate, e["cons"], e["mc1"], segunda)
    r = alta_http(e["mc1"], e["cons"], e["acta"], persona_id=segunda)
    assert (r.status_code, r.json()["codigo"]) == (403, "sin_permiso")
    assert ultimo_motivo(motor_app, e, e["mc1"]) == "no_es_su_propio_mandato"
    # Control: otra persona del Consejo sí le da el alta.
    assert alta_http(e["mc2"], e["cons"], e["acta"], persona_id=segunda).status_code == 201


# La segunda barrera, sin el evaluador -----------------------------------------------------------


def test_la_base_niega_el_alta_a_otra_cuenta_suya(motor_migrate, motor_app):
    cons, _, prop = consorcio_con_persona(motor_migrate)
    acta = decidir_equipo(motor_app, cons)
    segunda = otra_persona(motor_migrate, cons, verificada=False)
    misma_identidad(motor_migrate, cons, prop, segunda)
    with pytest.raises(DBAPIError) as err:
        alta(motor_app, cons, prop, acta, persona=segunda, tipo="miembro_consejo")
    assert _hint(err) == "alta_de_si_mismo"
    # Sin verificar, tampoco.
    sin = otra_persona(motor_migrate, cons, verificada=False)
    with pytest.raises(DBAPIError) as err:
        alta(motor_app, cons, prop, acta, persona=sin, tipo="miembro_consejo")
    assert _hint(err) == "identidad_no_verificada"
    # Control: otro ser humano verificado.
    otra = otra_persona(motor_migrate, cons)
    assert alta(motor_app, cons, prop, acta, persona=otra, tipo="miembro_consejo")


def test_la_base_niega_usar_la_decision_que_registro_otra_cuenta_suya(motor_migrate, motor_app):
    """A15 entre seres humanos: la asamblea la cargó una cuenta y la usa otra del mismo humano."""
    cons, _, prop = consorcio_con_persona(motor_migrate)
    registradora = otra_persona(motor_migrate, cons, verificada=False)
    misma_identidad(motor_migrate, cons, prop, registradora)
    with transaccion(motor_app, persona_id=registradora, consorcio_id=cons) as c:
        acta = c.execute(
            text(
                "select nucleo.registrar_decision('asamblea', '2026-01-01', 'Designa',"
                " 'Acta 3, folio 7', :a, 'api')"
            ),
            {"a": registradora},
        ).scalar_one()
    otra = otra_persona(motor_migrate, cons)
    with pytest.raises(DBAPIError) as err:
        alta(motor_app, cons, prop, acta, persona=otra, tipo="miembro_consejo")
    assert _hint(err) == "decision_registrada_por_el_actor"
    # Control: la usa un tercero.
    tercera = otra_persona(motor_migrate, cons)
    assert alta(motor_app, cons, tercera, acta, persona=otra, tipo="miembro_consejo")


def test_la_base_niega_remover_con_la_asamblea_que_registro_otra_cuenta_suya(
    motor_migrate, motor_app
):
    """A15 de la baja (remoción) entre seres humanos, sin el evaluador."""
    cons, _, prop = consorcio_con_persona(motor_migrate)
    registradora = otra_persona(motor_migrate, cons, verificada=False)
    misma_identidad(motor_migrate, cons, prop, registradora)
    with transaccion(motor_app, persona_id=registradora, consorcio_id=cons) as c:
        acta = c.execute(
            text(
                "select nucleo.registrar_decision('asamblea', '2026-01-01', 'Remueve',"
                " 'Acta 4, folio 9', :a, 'api')"
            ),
            {"a": registradora},
        ).scalar_one()
    otra = otra_persona(motor_migrate, cons)
    m = alta(motor_app, cons, None, acta, persona=otra, tipo="miembro_consejo", origen="soporte")
    with pytest.raises(DBAPIError) as err:
        baja(motor_app, cons, prop, m, "remocion", decision=acta, instrumento=None,
             descripcion="Remoción")  # fmt: skip
    assert _hint(err) == "decision_registrada_por_el_actor"
    # Sin verificar quien la registró, tampoco.
    sin = otra_persona(motor_migrate, cons, verificada=False)
    with transaccion(motor_app, persona_id=sin, consorcio_id=cons) as c:
        acta_sin = c.execute(
            text(
                "select nucleo.registrar_decision('asamblea', '2026-01-02', 'Remueve',"
                " 'Acta 5, folio 2', :a, 'api')"
            ),
            {"a": sin},
        ).scalar_one()
    with pytest.raises(DBAPIError) as err:
        baja(motor_app, cons, prop, m, "remocion", decision=acta_sin, instrumento=None,
             descripcion="Remoción")  # fmt: skip
    assert _hint(err) == "identidad_no_verificada"
    # Control: un tercero verificado remueve con esa asamblea.
    tercera = otra_persona(motor_migrate, cons)
    baja(motor_app, cons, tercera, m, "remocion", decision=acta, instrumento=None,
         descripcion="Remoción")  # fmt: skip


def test_a4_al_quitar_sigue_por_cuenta(motor_migrate, motor_app):
    """Una cuenta sin identidad verificada, con un mandato que le dio soporte, se puede dar de
    baja por la API: la regla fuerte bloquearía el remedio (frenado, decisión pendiente)."""
    cons, _, prop = consorcio_con_persona(motor_migrate)
    acta = decidir_equipo(motor_app, cons)
    sin = otra_persona(motor_migrate, cons, verificada=False)
    m = alta(motor_app, cons, None, acta, persona=sin, tipo="miembro_consejo", origen="soporte")
    baja(motor_app, cons, prop, m)
    with motor_migrate.connect() as c:
        hasta = c.execute(
            text("select vigente_hasta from nucleo.mandato where id = :m"), {"m": m}
        ).scalar_one()
    assert hasta is not None
    # Control: A4 por cuenta sigue: nadie se da de baja a sí mismo.
    m2 = alta(motor_app, cons, None, acta, persona=prop, tipo="miembro_consejo", origen="soporte")
    with pytest.raises(DBAPIError) as err:
        baja(motor_app, cons, prop, m2)
    assert _hint(err) == "baja_de_si_mismo"
