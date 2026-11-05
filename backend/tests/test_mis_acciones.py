"""`GET /api/v1/consorcios/{id}/mis-acciones` contra PostgreSQL real (documento 13, §10.5).

La lista sale del contexto que arma `require_scope` con los vínculos, mandatos, facultades, modo e
identidad reales. Cada prueba lleva su control contra lo que deciden las rutas.
"""

import secrets

import pytest

from app.autorizacion.politicas import POLITICAS
from app.identidad import sesiones
from tests.test_contexto_mandatos import cliente, denegaciones
from tests.test_identidad import app, entrar, es_401, navegador  # noqa: F401  (`app` es un fixture)
from tests.test_mandatos import otra_persona
from tests.test_mandatos_0012 import alta
from tests.test_mandatos_rutas import escena  # noqa: F401  (es un fixture)
from tests.test_modo_operacion import cambiar, consorcio_con_persona

pytestmark = [pytest.mark.db, pytest.mark.redis]

ECONOMICA = "fondo_reserva.autorizar"
"""Económica, por una facultad del Consejo, que corre en modo Consejo."""


def url(cons, resto="mis-acciones"):
    return f"/api/v1/consorcios/{cons}/{resto}"


def acciones(persona, cons) -> list[str]:
    with cliente(persona) as c:
        r = c.get(url(cons))
    assert r.status_code == 200, r.text
    assert set(r.json()) == {"acciones"}
    return r.json()["acciones"]


def test_dos_personas_con_roles_distintos_dan_listas_distintas(escena, motor_app):  # noqa: F811
    e = escena
    cambiar(motor_app, e["cons"], "consejo", e["acta"])
    consejo, propietaria = acciones(e["mc1"], e["cons"]), acciones(e["prop"], e["cons"])
    assert consejo == sorted(consejo) and propietaria == sorted(propietaria)
    assert set(consejo) | set(propietaria) <= set(POLITICAS)
    # Las dos ven lo concedido por ámbito; lo demás, según lo que cada una es.
    assert {"consorcios.mis_acciones", "unidades.listar"} <= set(consejo) & set(propietaria)
    assert "mandatos.ver" in consejo and "mandatos.ver" not in propietaria
    assert "documentos.ver" in propietaria
    assert consejo != propietaria
    # Control: las rutas deciden lo mismo que la lista.
    with cliente(e["mc1"]) as c:
        assert c.get(url(e["cons"], "mandatos")).status_code == 200
    with cliente(e["prop"]) as c:
        assert c.get(url(e["cons"], "mandatos")).status_code == 403


def test_el_modo_real_del_consorcio_filtra_la_lista(escena, motor_app):  # noqa: F811
    e = escena
    p = POLITICAS[ECONOMICA]
    assert not p.sin_modo and "consejo" in p.modos
    sin_modo = acciones(e["mc1"], e["cons"])
    assert ECONOMICA not in sin_modo and "unidades.listar" in sin_modo
    cambiar(motor_app, e["cons"], "consejo", e["acta"])
    assert ECONOMICA in acciones(e["mc1"], e["cons"])  # control: con modo, aparece


def test_una_economica_pide_identidad_y_no_el_segundo_factor(motor_migrate, escena, motor_app):  # noqa: F811
    e = escena
    cambiar(motor_app, e["cons"], "consejo", e["acta"])
    sin_identidad = otra_persona(motor_migrate, e["cons"], verificada=False)
    alta(motor_app, e["cons"], None, e["acta"], persona=sin_identidad, tipo="miembro_consejo",
         origen="soporte")  # fmt: skip
    # `cliente` no trae sesión: el contexto real dice que no hay segundo factor reciente.
    verificada = acciones(e["mc1"], e["cons"])
    sin_verificar = acciones(sin_identidad, e["cons"])
    assert ECONOMICA in verificada and ECONOMICA not in sin_verificar
    # Control: la diferencia entre dos miembros del Consejo son solo las económicas.
    economicas = {a for a, p in POLITICAS.items() if p.economica}
    assert set(verificada) - set(sin_verificar) <= economicas


def test_lo_que_no_se_alcanza_no_se_audita(escena, motor_app):  # noqa: F811
    e = escena
    assert "mandatos.ver" not in acciones(e["prop"], e["cons"])
    assert denegaciones(motor_app, e["cons"], e["mc2"], e["prop"]) == []
    # Control: el intento de verdad sí queda auditado.
    with cliente(e["prop"]) as c:
        assert c.get(url(e["cons"], "mandatos")).status_code == 403
    assert len(denegaciones(motor_app, e["cons"], e["mc2"], e["prop"])) == 1


def test_una_persona_de_otro_consorcio_recibe_404(motor_migrate, escena):  # noqa: F811
    _, _, ajena = consorcio_con_persona(motor_migrate)
    with cliente(ajena) as c:
        r = c.get(url(escena["cons"]))
    assert (r.status_code, r.json()["codigo"]) == (404, "no_encontrado")


def test_con_sesion_real_su_consorcio_si_y_el_ajeno_404(app, motor_migrate, datos):  # noqa: F811
    c = entrar(app, motor_migrate)  # Ana, propietaria en A, nada en B
    propio = c.get(url(datos.consorcio_a))
    assert propio.status_code == 200, propio.text
    assert "unidades.listar" in propio.json()["acciones"]
    ajeno = c.get(url(datos.consorcio_b))
    assert (ajeno.status_code, ajeno.json()["codigo"]) == (404, "no_encontrado")


def test_sin_sesion_responde_401(app, motor_migrate, datos):  # noqa: F811
    c = navegador(app)
    assert es_401(c.get(url(datos.consorcio_a)), "no_autenticado")
    c.cookies.set(sesiones.COOKIE_SESION, secrets.token_urlsafe(32), domain="testserver.local")
    assert es_401(c.get(url(datos.consorcio_a)), "no_autenticado")
    # Control: con una sesión de verdad, la misma ruta responde.
    assert entrar(app, motor_migrate).get(url(datos.consorcio_a)).status_code == 200
