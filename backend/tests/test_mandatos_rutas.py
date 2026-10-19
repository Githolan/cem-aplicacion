"""Rutas de decisiones y mandatos (H2b 2b-4b, PR 2) contra PostgreSQL real.

Diseño: `doc/diseno/H2b_4b_Diseno.md` del repositorio CEM, §2 y §5. Cada negación se prueba con
su 403 auditado y su control (el mismo pedido de otra persona pasa); A15, además, con la
decisión del equipo y con la baja por instrumento, que no la activan.
"""

from datetime import date, timedelta

import pytest
from sqlalchemy import text

from tests.conftest import clave_idempotencia
from tests.test_contexto_mandatos import cliente, denegaciones
from tests.test_mandatos import otra_persona, sesiones_abiertas
from tests.test_mandatos_0012 import CUIT_X, alta
from tests.test_modo_operacion import consorcio_con_persona, decidir_equipo

pytestmark = [pytest.mark.db, pytest.mark.redis]

DESDE = date(2026, 1, 1)
CUIT_DIGITO_MAL = "20123456780"


@pytest.fixture
def escena(motor_migrate, motor_app):
    """Consorcio con su propietaria, dos miembros del Consejo (con `mandatos.registrar` y
    `decisiones.registrar`, C2) y una persona más sin mandato."""
    cons, _, prop = consorcio_con_persona(motor_migrate)
    acta = decidir_equipo(motor_app, cons)
    mc1, mc2 = otra_persona(motor_migrate, cons), otra_persona(motor_migrate, cons)
    m_mc1 = alta(motor_app, cons, None, acta, persona=mc1, tipo="miembro_consejo",
                 origen="soporte")  # fmt: skip
    alta(motor_app, cons, None, acta, persona=mc2, tipo="miembro_consejo", origen="soporte")
    return {"cons": cons, "prop": prop, "acta": acta, "mc1": mc1, "mc2": mc2, "m_mc1": m_mc1,
            "vecina": otra_persona(motor_migrate, cons)}  # fmt: skip


def _post(persona, url, cuerpo, clave=None):
    with cliente(persona) as c:
        return c.post(url, json=cuerpo, headers=clave or clave_idempotencia())


def decision(persona, cons, tipo="asamblea", clave=None):
    return _post(persona, f"/api/v1/consorcios/{cons}/decisiones",
                 {"tipo": tipo, "fecha": "2026-03-10", "descripcion": "Designa al Consejo",
                  "referencia": "Acta 14, libro 3, folio 52"}, clave)  # fmt: skip


def alta_http(persona, cons, decision_id, clave=None, **cuerpo):
    datos = {"tipo": "miembro_consejo", "decision_id": str(decision_id), "desde": str(DESDE)}
    datos |= {k: str(v) if hasattr(v, "hex") else v for k, v in cuerpo.items()}
    return _post(persona, f"/api/v1/consorcios/{cons}/mandatos", datos, clave)


def baja_http(persona, cons, mandato, clave=None, **cuerpo):
    datos = {"causa": "renuncia", "instrumento": "Nota del 10-03", "descripcion": "Renuncia"}
    datos |= cuerpo
    return _post(persona, f"/api/v1/consorcios/{cons}/mandatos/{mandato}/baja", datos, clave)


def _motivos(motor_app, e, persona) -> list[tuple[str, str]]:
    return [
        (d["accion"], d["motivo"]) for d in denegaciones(motor_app, e["cons"], e["mc2"], persona)
    ]


def _mandatos_de(motor_migrate, cons, persona) -> int:
    with motor_migrate.connect() as c:
        return c.execute(
            text("select count(*) from nucleo.mandato where consorcio_id = :c and persona_id = :p"),
            {"c": cons, "p": persona},
        ).scalar_one()


# Decisiones ---------------------------------------------------------------------------------


def test_registrar_una_decision_con_reintento_idempotente(escena, motor_migrate):
    e = escena
    clave = clave_idempotencia()
    r = decision(e["mc1"], e["cons"], clave=clave)
    assert r.status_code == 201, r.text
    repetido = decision(e["mc1"], e["cons"], clave=clave)
    assert (repetido.status_code, repetido.json(), repetido.headers["Idempotent-Replayed"]) == (
        201, r.json(), "true")  # fmt: skip
    with motor_migrate.connect() as c:
        fila = c.execute(
            text("select registrada_por, origen, referencia_acta from nucleo.decision_formal"
                 " where id = :d"), {"d": r.json()["id"]},
        ).one()  # fmt: skip
    assert (fila.registrada_por, fila.origen, fila.referencia_acta) == (
        e["mc1"], "api", "Acta 14, libro 3, folio 52")  # fmt: skip


def test_la_propietaria_sin_facultad_no_registra_decisiones(escena, motor_app):
    e = escena
    r = decision(e["prop"], e["cons"])
    assert (r.status_code, r.json()["codigo"]) == (403, "sin_permiso")
    assert _motivos(motor_app, e, e["prop"]) == [("decisiones.registrar", "sin_concesion")]


def test_la_decision_sin_referencia_no_llega_a_la_base(escena):
    e = escena
    r = _post(e["mc1"], f"/api/v1/consorcios/{e['cons']}/decisiones",
              {"tipo": "asamblea", "fecha": "2026-03-10", "descripcion": "x",
               "referencia": "   "})  # fmt: skip
    assert (r.status_code, r.json()["codigo"]) == (422, "peticion_invalida")


# Alta y A15 ---------------------------------------------------------------------------------


def test_a15_quien_registro_la_decision_no_da_el_alta(escena, motor_app, motor_migrate):
    e = escena
    d = decision(e["mc1"], e["cons"]).json()["id"]
    r = alta_http(e["mc1"], e["cons"], d, persona_id=e["vecina"])
    assert (r.status_code, r.json()["codigo"]) == (403, "sin_permiso")
    assert _motivos(motor_app, e, e["mc1"]) == [("mandatos.alta", "no_registro_la_decision")]
    assert _mandatos_de(motor_migrate, e["cons"], e["vecina"]) == 0
    # Control: la otra integrante del Consejo usa esa misma decisión.
    r = alta_http(e["mc2"], e["cons"], d, persona_id=e["vecina"])
    assert r.status_code == 201, r.text
    assert _mandatos_de(motor_migrate, e["cons"], e["vecina"]) == 1


def test_a15_la_decision_del_equipo_no_la_activa(escena, motor_migrate):
    e = escena
    r = alta_http(e["mc1"], e["cons"], e["acta"], persona_id=e["vecina"])
    assert r.status_code == 201, r.text


def test_a4_nadie_se_da_un_mandato_a_si_mismo(escena, motor_app):
    e = escena
    r = alta_http(e["mc1"], e["cons"], e["acta"], tipo="administrador", persona_id=e["mc1"])
    assert r.status_code == 403
    assert _motivos(motor_app, e, e["mc1"]) == [("mandatos.alta", "no_es_su_propio_mandato")]


def test_una_decision_que_no_existe_da_422_solo_a_quien_puede(escena, motor_app):
    e = escena
    inexistente = "01a10000-0000-7000-8000-000000000000"
    r = alta_http(e["mc1"], e["cons"], inexistente, persona_id=e["vecina"])
    assert (r.status_code, r.json()["codigo"]) == (422, "decision_invalida")
    # Control: sin la facultad, la misma petición se niega antes (no revela nada).
    r = alta_http(e["prop"], e["cons"], inexistente, persona_id=e["vecina"])
    assert (r.status_code, r.json()["codigo"]) == (403, "sin_permiso")


@pytest.mark.parametrize(("cuit", "codigo"), [(None, "cuit_invalido"),
                                              (CUIT_DIGITO_MAL, "cuit_invalido")])  # fmt: skip
def test_el_externo_necesita_un_cuit_valido(escena, cuit, codigo):
    e = escena
    r = alta_http(e["mc1"], e["cons"], e["acta"], tipo="administrador_externo",
                  nombre="Estudio Prueba", cuit=cuit)  # fmt: skip
    assert (r.status_code, r.json()["codigo"]) == (422, codigo)
    # Control: con un CUIT válido, entra.
    r = alta_http(e["mc1"], e["cons"], e["acta"], tipo="administrador_externo",
                  nombre="Estudio Prueba", cuit=CUIT_X)  # fmt: skip
    assert r.status_code == 201, r.text


def test_la_administracion_es_unica(escena):
    e = escena
    assert alta_http(e["mc1"], e["cons"], e["acta"], tipo="administrador",
                     persona_id=e["vecina"]).status_code == 201  # fmt: skip
    r = alta_http(e["mc1"], e["cons"], e["acta"], tipo="administrador", persona_id=e["prop"])
    assert (r.status_code, r.json()["codigo"]) == (409, "administracion_ocupada")


def test_r10_un_externo_asociado_no_entra_al_consejo(escena):
    e = escena
    r = alta_http(e["mc1"], e["cons"], e["acta"], tipo="administrador_externo",
                  persona_id=e["vecina"], nombre="Estudio", cuit=CUIT_X)  # fmt: skip
    assert r.status_code == 201, r.text
    r = alta_http(e["mc1"], e["cons"], e["acta"], persona_id=e["vecina"])
    assert (r.status_code, r.json()["codigo"]) == (409, "consejo_incompatible_con_administracion")


def test_la_vigencia_al_reves_no_llega_a_la_base(escena):
    e = escena
    r = alta_http(e["mc1"], e["cons"], e["acta"], persona_id=e["vecina"],
                  hasta=str(DESDE - timedelta(days=1)))  # fmt: skip
    assert (r.status_code, r.json()["codigo"]) == (422, "peticion_invalida")


# Baja y A15 ---------------------------------------------------------------------------------


def test_a15_quien_registro_la_asamblea_no_remueve(escena, motor_app, motor_migrate):
    e = escena
    d = decision(e["mc1"], e["cons"]).json()["id"]
    sesiones_abiertas(motor_migrate, e["vecina"], 2)
    m = alta_http(e["mc2"], e["cons"], d, persona_id=e["vecina"]).json()["id"]
    r = baja_http(e["mc1"], e["cons"], m, causa="remocion", decision_id=d, instrumento=None,
                  descripcion="Remoción por asamblea")  # fmt: skip
    assert r.status_code == 403
    assert _motivos(motor_app, e, e["mc1"]) == [("mandatos.baja", "no_registro_la_decision")]
    assert sesiones_abiertas(motor_migrate, e["vecina"]) == 2
    # Control: la otra integrante remueve con esa asamblea; las sesiones se revocan (RN22).
    clave = clave_idempotencia()
    r = baja_http(e["mc2"], e["cons"], m, clave, causa="remocion", decision_id=d,
                  instrumento=None, descripcion="Remoción por asamblea")  # fmt: skip
    assert r.status_code == 200, r.text
    assert r.json()["causa"] == "remocion" and r.json()["vigente_hasta"] is not None
    assert sesiones_abiertas(motor_migrate, e["vecina"]) == 0
    repetido = baja_http(e["mc2"], e["cons"], m, clave, causa="remocion", decision_id=d,
                         instrumento=None, descripcion="Remoción por asamblea")  # fmt: skip
    assert (repetido.status_code, repetido.json()) == (200, r.json())
    otra_vez = baja_http(e["mc2"], e["cons"], m, causa="remocion", decision_id=d,
                         instrumento=None, descripcion="Remoción por asamblea")  # fmt: skip
    assert (otra_vez.status_code, otra_vez.json()["codigo"]) == (409, "mandato_ya_dado_de_baja")


def test_a15_no_toca_la_renuncia(escena):
    e = escena
    decision(e["mc1"], e["cons"])
    m = alta_http(e["mc2"], e["cons"], e["acta"], persona_id=e["vecina"]).json()["id"]
    r = baja_http(e["mc1"], e["cons"], m)
    assert r.status_code == 200, r.text


def test_la_remocion_con_una_decision_que_no_es_asamblea(escena):
    e = escena
    d = decision(e["mc1"], e["cons"], tipo="consejo").json()["id"]
    m = alta_http(e["mc1"], e["cons"], e["acta"], persona_id=e["vecina"]).json()["id"]
    r = baja_http(e["mc2"], e["cons"], m, causa="remocion", decision_id=d, instrumento=None)
    assert (r.status_code, r.json()["codigo"]) == (422, "decision_invalida")


def test_a4_nadie_se_da_de_baja_a_si_mismo(escena, motor_app):
    e = escena
    r = baja_http(e["mc1"], e["cons"], e["m_mc1"])
    assert r.status_code == 403
    assert _motivos(motor_app, e, e["mc1"]) == [("mandatos.baja", "no_es_su_propio_mandato")]
    # Control: la otra integrante registra esa renuncia.
    assert baja_http(e["mc2"], e["cons"], e["m_mc1"]).status_code == 200


def test_el_mandato_de_otro_consorcio_no_existe(escena, motor_migrate, motor_app):
    e = escena
    otro, _, _ = consorcio_con_persona(motor_migrate)
    ajeno = alta(motor_app, otro, None, decidir_equipo(motor_app, otro),
                 persona=otra_persona(motor_migrate, otro), tipo="miembro_consejo",
                 origen="soporte")  # fmt: skip
    r = baja_http(e["mc1"], e["cons"], ajeno)
    assert (r.status_code, r.json()["codigo"]) == (404, "no_encontrado")


def test_la_remocion_con_instrumento_no_llega_a_la_base(escena):
    e = escena
    r = baja_http(e["mc2"], e["cons"], e["m_mc1"], causa="remocion", decision_id=str(e["acta"]))
    assert (r.status_code, r.json()["codigo"]) == (422, "peticion_invalida")


# Cada motivo de la base que la persona puede provocar sale con su código, nunca 500 -------


@pytest.mark.parametrize(
    ("cuerpo", "estado", "codigo"),
    [
        ({"persona_id": "01a10000-0000-7000-8000-000000000001"}, 422, "persona_invalida"),
        ({"tipo": "administrador", "nombre": "Sin cuenta"}, 422, "mandato_sin_cuenta"),
        ({"persona_id": "VECINA", "facultades": []}, 422, "facultad_fuera_de_paquete"),
        ({"persona_id": "VECINA", "facultades": ["pagos.autorizar"]}, 422,
         "facultad_fuera_de_paquete"),
    ],
)  # fmt: skip
def test_el_alta_traduce_cada_motivo(escena, cuerpo, estado, codigo):
    e = escena
    cuerpo = {k: (str(e["vecina"]) if v == "VECINA" else v) for k, v in cuerpo.items()}
    r = alta_http(e["mc1"], e["cons"], e["acta"], **cuerpo)
    assert (r.status_code, r.json()["codigo"]) == (estado, codigo), r.text


def test_un_mandato_superpuesto_da_409(escena):
    e = escena
    assert alta_http(e["mc1"], e["cons"], e["acta"], persona_id=e["vecina"]).status_code == 201
    r = alta_http(e["mc1"], e["cons"], e["acta"], persona_id=e["vecina"])
    assert (r.status_code, r.json()["codigo"]) == (409, "mandato_superpuesto")


def test_la_baja_de_un_mandato_que_ya_termino_da_409(escena, motor_app, motor_migrate):
    e = escena
    m = alta(motor_app, e["cons"], None, e["acta"], persona=e["vecina"], tipo="miembro_consejo",
             hasta=date.today() - timedelta(days=2), origen="soporte")  # fmt: skip
    r = baja_http(e["mc1"], e["cons"], m)
    assert (r.status_code, r.json()["codigo"]) == (409, "mandato_terminado")


def test_el_alta_se_repite_con_la_misma_clave(escena, motor_migrate):
    e = escena
    clave = clave_idempotencia()
    r = alta_http(e["mc1"], e["cons"], e["acta"], clave, persona_id=e["vecina"])
    assert r.status_code == 201, r.text
    repetido = alta_http(e["mc1"], e["cons"], e["acta"], clave, persona_id=e["vecina"])
    assert (repetido.status_code, repetido.json(), repetido.headers["Idempotent-Replayed"]) == (
        201, r.json(), "true")  # fmt: skip
    assert _mandatos_de(motor_migrate, e["cons"], e["vecina"]) == 1


def test_sin_la_facultad_un_mandato_inexistente_da_el_mismo_403(escena, motor_app):
    """Quien no puede dar de baja no distingue un id válido de uno que no existe (Codex)."""
    e = escena
    inexistente = "01a10000-0000-7000-8000-0000000000ff"
    assert baja_http(e["prop"], e["cons"], e["m_mc1"]).status_code == 403
    r = baja_http(e["prop"], e["cons"], inexistente)
    assert (r.status_code, r.json()["codigo"]) == (403, "sin_permiso")
    assert _motivos(motor_app, e, e["prop"]) == [("mandatos.baja", "sin_concesion")] * 2
    # Control: con la facultad, el inexistente sigue siendo 404.
    r = baja_http(e["mc1"], e["cons"], inexistente)
    assert (r.status_code, r.json()["codigo"]) == (404, "no_encontrado")
