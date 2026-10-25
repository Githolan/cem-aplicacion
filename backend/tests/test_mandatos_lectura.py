"""Rutas de lectura de mandatos y decisiones y destinatario por la API (2b-4c) contra
PostgreSQL real.

Diseño: `doc/diseno/H2b_4c_Diseno.md` del repositorio CEM, §3 a §6. Cada negación se prueba con
su 403 auditado y su control; los detalles inexistentes, con el mismo 403 para quien no puede
leer (sin oráculo) y 404 solo para quien puede.
"""

import pytest

from app.autorizacion.evaluador import Decision
from app.autorizacion.requiere import Permiso
from app.consorcios.mandatos import Discrepancia
from tests.conftest import verificar_en_el_consorcio
from tests.test_contexto_mandatos import cliente, denegaciones
from tests.test_identidad import app, navegador  # noqa: F401  (`app` es un fixture)
from tests.test_mandatos import otra_persona, vinculo_confirmado
from tests.test_mandatos_0012 import CUIT_X, alta
from tests.test_mandatos_rutas import alta_http, decision, escena  # noqa: F401  (fixture)
from tests.test_modo_operacion import consorcio_con_persona, decidir_equipo

pytestmark = [pytest.mark.db, pytest.mark.redis]

INEXISTENTE = "01a10000-0000-7000-8000-000000000001"
COLUMNAS_MANDATO = {
    "id", "tipo", "nombre", "cuit", "vigente_desde", "vigente_hasta", "estado", "con_cuenta",
}  # fmt: skip
COLUMNAS_DETALLE = COLUMNAS_MANDATO | {
    "matricula", "decision_id", "facultades", "causa_baja", "baja_decision_id",
    "instrumento_referencia", "motivo_fin", "finalizado_en",
}  # fmt: skip


def _get(persona, url):
    with cliente(persona) as c:
        return c.get(url)


def _url(cons, resto=""):
    return f"/api/v1/consorcios/{cons}/{resto}"


def _motivos(motor_app, e, persona):
    return [
        (d["accion"], d["motivo"]) for d in denegaciones(motor_app, e["cons"], e["mc2"], persona)
    ]


# Mandatos -----------------------------------------------------------------------------------


def test_el_consejo_lista_los_mandatos_con_lo_minimo(escena, motor_app):  # noqa: F811
    e = escena
    alta(motor_app, e["cons"], None, e["acta"], tipo="administrador_externo", nombre="Estudio",
         cuit=CUIT_X, origen="soporte")  # fmt: skip
    r = _get(e["mc1"], _url(e["cons"], "mandatos"))
    assert r.status_code == 200, r.text
    filas = r.json()["elementos"]
    assert len(filas) == 3 and all(set(f) == COLUMNAS_MANDATO for f in filas)
    por_tipo = {f["tipo"]: f for f in filas}
    assert (por_tipo["administrador_externo"]["cuit"], por_tipo["miembro_consejo"]["cuit"]) == (
        CUIT_X, None)  # fmt: skip
    assert "@" not in r.text and "persona_id" not in r.text


def test_la_propietaria_no_lista_mandatos_y_queda_auditado(escena, motor_app):  # noqa: F811
    e = escena
    r = _get(e["prop"], _url(e["cons"], "mandatos"))
    assert (r.status_code, r.json()["codigo"]) == (403, "sin_permiso")
    assert _motivos(motor_app, e, e["prop"]) == [("mandatos.ver", "sin_concesion")]


def test_el_detalle_suma_lo_autorizado(escena):  # noqa: F811
    e = escena
    r = _get(e["mc1"], _url(e["cons"], f"mandatos/{e['m_mc1']}"))
    assert r.status_code == 200, r.text
    cuerpo = r.json()
    assert set(cuerpo) == COLUMNAS_DETALLE
    assert {"mandatos.ver", "decisiones.ver", "mandatos.registrar"} <= set(cuerpo["facultades"])
    assert cuerpo["decision_id"] == str(e["acta"])


def test_un_mandato_inexistente_da_404_solo_a_quien_puede_leer(escena, motor_app):  # noqa: F811
    e = escena
    r = _get(e["mc1"], _url(e["cons"], f"mandatos/{INEXISTENTE}"))
    assert (r.status_code, r.json()["codigo"]) == (404, "no_encontrado")
    # Quien no puede leer recibe el mismo 403 auditado exista o no el mandato.
    inexistente = _get(e["prop"], _url(e["cons"], f"mandatos/{INEXISTENTE}"))
    existente = _get(e["prop"], _url(e["cons"], f"mandatos/{e['m_mc1']}"))
    assert (inexistente.status_code, existente.status_code) == (403, 403)
    assert inexistente.json()["mensaje"] == existente.json()["mensaje"]
    assert _motivos(motor_app, e, e["prop"]) == [("mandatos.ver", "sin_concesion")] * 2


def test_el_mandato_de_otro_consorcio_no_existe_en_este(escena, motor_migrate, motor_app):  # noqa: F811
    e = escena
    otro, _, _ = consorcio_con_persona(motor_migrate)
    ajeno = alta(motor_app, otro, None, decidir_equipo(motor_app, otro), tipo="miembro_consejo",
                 persona=otra_persona(motor_migrate, otro), origen="soporte")  # fmt: skip
    r = _get(e["mc1"], _url(e["cons"], f"mandatos/{ajeno}"))
    assert r.status_code == 404


# Decisiones ---------------------------------------------------------------------------------


def test_las_decisiones_salen_sin_referencia_y_el_detalle_avisa_a15(escena, motor_app):  # noqa: F811
    e = escena
    propia = decision(e["mc1"], e["cons"]).json()["id"]
    r = _get(e["mc1"], _url(e["cons"], "decisiones"))
    assert r.status_code == 200, r.text
    filas = r.json()["elementos"]
    assert {f["id"] for f in filas} == {str(e["acta"]), propia}
    assert all(set(f) == {"id", "tipo", "fecha", "descripcion"} for f in filas)
    assert "Acta 14" not in r.text  # la referencia va solo en el detalle
    mia = _get(e["mc1"], _url(e["cons"], f"decisiones/{propia}")).json()
    del_equipo = _get(e["mc1"], _url(e["cons"], f"decisiones/{e['acta']}")).json()
    de_otro = _get(e["mc2"], _url(e["cons"], f"decisiones/{propia}")).json()
    assert (mia["referencia"], mia["registrada_por_vos"], mia["del_equipo"]) == (
        "Acta 14, libro 3, folio 52", True, False)  # fmt: skip
    assert (del_equipo["registrada_por_vos"], del_equipo["del_equipo"]) == (False, True)
    assert (de_otro["registrada_por_vos"], de_otro["del_equipo"]) == (False, False)
    assert "registrada_por" not in mia  # quién la registró no sale


def test_una_decision_inexistente_da_404_solo_a_quien_puede_leer(escena, motor_app):  # noqa: F811
    e = escena
    assert _get(e["mc1"], _url(e["cons"], f"decisiones/{INEXISTENTE}")).status_code == 404
    assert _get(e["prop"], _url(e["cons"], f"decisiones/{INEXISTENTE}")).status_code == 403
    assert _get(e["prop"], _url(e["cons"], f"decisiones/{e['acta']}")).status_code == 403
    assert _motivos(motor_app, e, e["prop"]) == [("decisiones.ver", "sin_concesion")] * 2


# Candidatos ---------------------------------------------------------------------------------


def test_los_candidatos_los_lee_quien_puede_dar_el_alta(escena, motor_app):  # noqa: F811
    e = escena
    r = _get(e["mc1"], _url(e["cons"], "mandatos/candidatos"))
    assert r.status_code == 200, r.text
    ids = {f["persona_id"] for f in r.json()["elementos"]}
    assert {str(e["vecina"]), str(e["prop"])} <= ids
    assert "@" not in r.text
    negado = _get(e["prop"], _url(e["cons"], "mandatos/candidatos"))
    assert negado.status_code == 403
    assert _motivos(motor_app, e, e["prop"]) == [("mandatos.ver_candidatos", "sin_concesion")]


# Destinatario por HTTP ----------------------------------------------------------------------


def test_sin_permiso_la_persona_no_se_valida(escena, motor_app):  # noqa: F811
    """Primero la autorización: quien no puede dar el alta recibe 403 aunque la cuenta no
    exista (no aprende nada de la persona)."""
    e = escena
    r = alta_http(e["prop"], e["cons"], e["acta"], persona_id=INEXISTENTE)
    assert (r.status_code, r.json()["codigo"]) == (403, "sin_permiso")


def test_una_cuenta_con_vinculo_en_otro_consorcio_da_el_mismo_motivo(escena, motor_migrate,  # noqa: F811
                                                                     motor_app):  # fmt: skip
    e = escena
    otro, _, _ = consorcio_con_persona(motor_migrate)
    ajena = otra_persona(motor_migrate, otro)
    vinculo_confirmado(motor_app, otro, ajena)
    respuestas = [alta_http(e["mc1"], e["cons"], e["acta"], persona_id=p).json()
                  for p in (ajena, INEXISTENTE)]  # fmt: skip
    # Sin identidad verificada en este consorcio, las dos se niegan igual antes de mirar la cuenta.
    assert [c["codigo"] for c in respuestas] == ["sin_permiso"] * 2
    assert respuestas[0]["mensaje"] == respuestas[1]["mensaje"]
    # Verificada aquí pero sin vínculo: la base da el motivo, sin decir nada del otro consorcio.
    verificar_en_el_consorcio(motor_migrate, e["cons"], ajena)
    r = alta_http(e["mc1"], e["cons"], e["acta"], persona_id=ajena)
    assert (r.status_code, r.json()["codigo"]) == (422, "persona_sin_vinculo")
    # Control: con su vínculo confirmado en este consorcio, la vecina entra.
    assert alta_http(e["mc1"], e["cons"], e["acta"], persona_id=e["vecina"]).status_code == 201


def test_el_externo_con_cuenta_por_la_api_va_por_invitacion(escena):  # noqa: F811
    e = escena
    r = alta_http(e["mc1"], e["cons"], e["acta"], tipo="administrador_externo",
                  persona_id=e["vecina"], nombre="Estudio", cuit=CUIT_X)  # fmt: skip
    assert (r.status_code, r.json()["codigo"]) == (422, "externo_por_invitacion")
    sin_cuenta = alta_http(e["mc1"], e["cons"], e["acta"], tipo="administrador_externo",
                           nombre="Estudio", cuit=CUIT_X)  # fmt: skip
    assert sin_cuenta.status_code == 201, sin_cuenta.text


# Discrepancia y sesión ----------------------------------------------------------------------


@pytest.mark.parametrize("resto", ["mandatos", "decisiones", "mandatos/candidatos"])
def test_si_la_base_niega_y_el_evaluador_concede_falla_fuerte(escena, monkeypatch, resto):  # noqa: F811
    e = escena
    monkeypatch.setattr(
        Permiso,
        "verificar",
        lambda self, entidad=None, **kw: Decision(True, 200, "concedido_por_facultad"),
    )
    with cliente(e["prop"]) as c, pytest.raises(Discrepancia):
        c.get(_url(e["cons"], resto))


@pytest.mark.parametrize("resto", ["mandatos", "decisiones", "mandatos/candidatos"])
def test_sin_sesion_responde_401(app, escena, resto):  # noqa: F811
    assert navegador(app).get(_url(escena["cons"], resto)).status_code == 401
