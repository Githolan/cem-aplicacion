"""Mandatos de la tanda 8 (`0110`; 18-11-2026): candidatos con la condición del Consejo y sus
mandatos (pregunta 8, FL-MAN-02, FL-MAN-03), el alta de una administración externa con la
invitación por aprobar (pregunta 17, FL-MAN-06) y el estado de la invitación en la lista
(FL-MAN-04).

Contra PostgreSQL real. Datos SINTETICOS / PRUEBA / NO REAL.
"""

import pytest
from sqlalchemy import text

from tests.conftest import clave_idempotencia
from tests.test_contexto_mandatos import cliente
from tests.test_mandatos import otra_persona
from tests.test_mandatos_0012 import CUIT_X
from tests.test_mandatos_rutas import alta_http
from tests.test_mandatos_rutas import escena as escena  # noqa: F401  (es un fixture)

pytestmark = [pytest.mark.db, pytest.mark.redis]
# ruff: noqa: F811

INVITACION = {"correo": "estudio-sintetico@ejemplo.com", "referencia": "Contrato de prueba 1"}
EXTERNO = {"tipo": "administrador_externo", "nombre": "Estudio SINTETICO", "cuit": CUIT_X}


def leer(persona, cons, resto: str) -> dict:
    with cliente(persona) as c:
        r = c.get(f"/api/v1/consorcios/{cons}/{resto}")
    assert r.status_code == 200, r.text
    return r.json()


def inquilina(motor_migrate, cons):
    """Una persona con un vínculo confirmado de inquilina: candidata, sin la condición del
    Consejo (el catálogo de `0023` pide propietario)."""
    p, confirma = otra_persona(motor_migrate, cons), otra_persona(motor_migrate, cons)
    with motor_migrate.begin() as c:
        u = c.execute(text("insert into nucleo.unidad (consorcio_id, designacion)"
                           " values (:c, 'UF INQ') returning id"),
                      {"c": cons}).scalar_one()  # fmt: skip
        c.execute(text(
            "insert into nucleo.vinculo (consorcio_id, unidad_id, persona_id, tipo, vigente_desde,"
            " fuente, origen, confirmado_por, confirmado_en, confirmacion_referencia)"
            " values (:c, :u, :p, 'inquilino', '2020-01-01', 'documento', 'soporte', :k,"
            " '2020-01-01', 'Contrato de prueba')"),
            {"c": cons, "u": u, "p": p, "k": confirma})  # fmt: skip
    return p


def test_los_candidatos_dicen_si_habilitan_el_consejo_y_que_mandatos_tienen(escena, motor_migrate):
    e = escena
    inq = inquilina(motor_migrate, e["cons"])
    assert alta_http(e["mc1"], e["cons"], e["acta"], tipo="administrador",
                     persona_id=e["vecina"]).status_code == 201  # fmt: skip
    candidatos = {c["persona_id"]: c for c in leer(e["mc1"], e["cons"],
                                                   "mandatos/candidatos")["elementos"]}  # fmt: skip
    assert candidatos[str(e["prop"])]["habilita_consejo"] is True
    assert candidatos[str(e["prop"])]["mandatos"] == []
    assert candidatos[str(inq)]["habilita_consejo"] is False
    assert candidatos[str(e["vecina"])]["mandatos"] == ["administrador"]
    # Lo que dice la lista es lo que mide la base (E62): sin la condición, el mandato del
    # Consejo nace sin operar; con ella, vigente.
    estados = {}
    for persona in (inq, e["prop"]):
        r = alta_http(e["mc1"], e["cons"], e["acta"], persona_id=persona)
        assert r.status_code == 201, r.text
        estados[persona] = leer(e["mc1"], e["cons"], f"mandatos/{r.json()['id']}")["estado"]
    assert estados == {inq: "pendiente_revision", e["prop"]: "vigente"}


def test_el_alta_del_externo_deja_la_invitacion_por_aprobar(escena):
    e = escena
    r = alta_http(e["mc1"], e["cons"], e["acta"], invitacion=INVITACION, **EXTERNO)
    assert r.status_code == 201, r.text
    mandato, invitacion = r.json()["id"], r.json()["invitacion_id"]
    assert invitacion is not None
    for quien, mia in ((e["mc1"], True), (e["mc2"], False)):
        [m] = [x for x in leer(quien, e["cons"], "mandatos")["elementos"] if x["id"] == mandato]
        assert (m["invitacion"], m["invitacion_emitida_por_mi"]) == ("por_aprobar", mia)
        detalle = leer(quien, e["cons"], f"mandatos/{mandato}")
        assert detalle["invitacion"] == "por_aprobar"
    # Otra persona la aprueba (cuatro ojos), y la lista lo dice.
    with cliente(e["mc2"]) as c:
        a = c.post(f"/api/v1/consorcios/{e['cons']}/invitaciones-mandato/{invitacion}/aprobar",
                   headers=clave_idempotencia())  # fmt: skip
    assert a.status_code == 200, a.text
    [m] = [x for x in leer(e["mc1"], e["cons"], "mandatos")["elementos"] if x["id"] == mandato]
    assert m["invitacion"] == "pendiente"


def test_la_invitacion_solo_va_con_un_externo_y_sin_ella_no_hay(escena):
    e = escena
    r = alta_http(e["mc1"], e["cons"], e["acta"], invitacion=INVITACION, persona_id=e["prop"])
    assert r.status_code == 422
    # Control: el externo sin invitación se da de alta, sin invitación en la lista.
    r = alta_http(e["mc1"], e["cons"], e["acta"], **EXTERNO)
    assert r.status_code == 201, r.text
    assert r.json()["invitacion_id"] is None
    [m] = [x for x in leer(e["mc1"], e["cons"], "mandatos")["elementos"]
           if x["id"] == r.json()["id"]]  # fmt: skip
    assert (m["invitacion"], m["invitacion_emitida_por_mi"]) == (None, False)
