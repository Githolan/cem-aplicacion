"""Defectos previos a 2b-4, encontrados al diseñarlo (tracker de CEM: E25, E28 y E30).

- **E25:** invitar a una unidad que no existe en el consorcio respondía 403 "Solo quien es
  propietario…" aunque la persona tuviera la facultad general. Ahora es 404, igual que cualquier
  id ajeno, y no se crea nada.
- **E28:** `require_scope` no miraba `consorcio.eliminado_en`: un consorcio dado de baja seguía
  dando ámbito. Ahora responde 404, como `/me/consorcios`, que ya lo excluía.
- **E30:** el listado de invitaciones devolvía el correo también en las aceptadas, donde ya es el
  correo con el que la persona entra. D6: nadie lo ve de forma automática.
"""

from uuid import uuid4

import pytest
from sqlalchemy import text

from tests.conftest import clave_idempotencia
from tests.test_contexto_mandatos import (  # noqa: F401  (`edificio` es un fixture)
    cliente,
    edificio,
    invitacion,
)
from tests.test_mandatos import otra_persona
from tests.test_mandatos_0012 import alta

pytestmark = pytest.mark.db


def invitaciones_url(cons) -> str:
    return f"/api/v1/consorcios/{cons}/invitaciones"


def test_e25_unidad_inexistente_con_la_facultad_general_responde_404_y_no_crea_nada(
    motor_migrate,
    motor_app,
    edificio,  # noqa: F811
):
    e = edificio
    mc = otra_persona(motor_migrate, e["cons"])
    alta(motor_app, e["cons"], e["prop1"], e["acta"], persona=mc, tipo="miembro_consejo")
    with cliente(mc) as c:
        r = c.post(
            invitaciones_url(e["cons"]), json=invitacion(uuid4()), headers=clave_idempotencia()
        )
        assert r.status_code == 404, r.text
        assert r.json()["codigo"] == "no_encontrado"
        # Control: la misma persona invita a una unidad que sí existe.
        assert (
            c.post(
                invitaciones_url(e["cons"]), json=invitacion(e["u1"]), headers=clave_idempotencia()
            ).status_code
            == 201
        )
    with motor_migrate.connect() as c:
        assert (
            c.execute(
                text("select count(*) from nucleo.invitacion where consorcio_id = :c"),
                {"c": e["cons"]},
            ).scalar_one()
            == 1
        )


def test_e25_la_unidad_de_otro_consorcio_responde_igual_que_una_inexistente(
    motor_migrate,
    motor_app,
    edificio,  # noqa: F811
):
    e = edificio
    mc = otra_persona(motor_migrate, e["cons"])
    alta(motor_app, e["cons"], e["prop1"], e["acta"], persona=mc, tipo="miembro_consejo")
    with motor_migrate.begin() as c:
        otro = c.execute(
            text("insert into nucleo.consorcio (nombre) values ('Otro E25') returning id")
        ).scalar_one()
        ajena = c.execute(
            text("insert into nucleo.unidad (consorcio_id, designacion) values (:c, 'X')"
                 " returning id"),
            {"c": otro},
        ).scalar_one()  # fmt: skip
    with cliente(mc) as c:
        r = c.post(
            invitaciones_url(e["cons"]), json=invitacion(ajena), headers=clave_idempotencia()
        )
        assert (r.status_code, r.json()["codigo"]) == (404, "no_encontrado")


def test_e25_la_propietaria_sigue_recibiendo_403_por_otra_unidad(edificio):  # noqa: F811
    """Control: el 403 de la política no cambia; solo cambia el caso de la unidad inexistente.
    Sin la facultad, una unidad ajena y una inexistente responden igual (no se distinguen)."""
    e = edificio
    with cliente(e["prop1"]) as c:
        for unidad in (e["u2"], uuid4()):
            r = c.post(
                invitaciones_url(e["cons"]), json=invitacion(unidad), headers=clave_idempotencia()
            )
            assert (r.status_code, r.json()["codigo"]) == (403, "sin_permiso")


def test_e25_una_unidad_dada_de_baja_responde_404(motor_migrate, motor_app, edificio):  # noqa: F811
    e = edificio
    mc = otra_persona(motor_migrate, e["cons"])
    alta(motor_app, e["cons"], e["prop1"], e["acta"], persona=mc, tipo="miembro_consejo")
    with motor_migrate.begin() as c:
        c.execute(
            text("update nucleo.unidad set eliminado_en = now() where id = :u"), {"u": e["u2"]}
        )
    with cliente(mc) as c:
        r = c.post(
            invitaciones_url(e["cons"]), json=invitacion(e["u2"]), headers=clave_idempotencia()
        )
        assert (r.status_code, r.json()["codigo"]) == (404, "no_encontrado")


def test_e28_un_consorcio_dado_de_baja_no_da_ambito(motor_migrate, edificio):  # noqa: F811
    e = edificio
    url = f"/api/v1/consorcios/{e['cons']}/unidades"
    with cliente(e["prop1"]) as c:
        assert c.get(url).status_code == 200  # control: antes de la baja entra
    with motor_migrate.begin() as c:
        c.execute(
            text("update nucleo.consorcio set eliminado_en = now() where id = :c"),
            {"c": e["cons"]},
        )
    with cliente(e["prop1"]) as c:
        r = c.get(url)
        assert (r.status_code, r.json()["codigo"]) == (404, "no_encontrado")
        # Igual que un consorcio que no existe.
        assert c.get(f"/api/v1/consorcios/{uuid4()}/unidades").status_code == 404


def test_e30_el_listado_no_devuelve_el_correo_de_las_aceptadas(motor_migrate, edificio):  # noqa: F811
    e = edificio
    url = invitaciones_url(e["cons"])
    with cliente(e["prop1"]) as c:
        pendiente = c.post(url, json=invitacion(e["u1"], "p"), headers=clave_idempotencia()).json()
        aceptada = c.post(
            url, json={**invitacion(e["u1"], "a"), "tipo": "ocupante"}, headers=clave_idempotencia()
        ).json()
    persona = otra_persona(motor_migrate, e["cons"])
    with motor_migrate.begin() as c:
        c.execute(
            text("update nucleo.invitacion set aceptada_en = now(), persona_id = :p where id = :i"),
            {"p": persona, "i": aceptada["id"]},
        )
    with cliente(e["prop1"]) as c:
        filas = {i["id"]: i for i in c.get(url).json()["elementos"]}
    assert filas[aceptada["id"]]["estado"] == "aceptada"
    assert filas[aceptada["id"]]["correo"] is None
    # Control: la pendiente conserva el correo que escribió quien invitó.
    assert filas[pendiente["id"]]["correo"] == pendiente["correo"]
