"""Lectura de la auditoría con exposición mínima (RF35-B, 2b-6c) contra PostgreSQL real.

Diseño: `doc/diseno/H2b_6_Diseno.md` del repositorio CEM, §6. Cada negación lleva su control:
la lista blanca contra el evento crudo, el nombre del actor con y sin `padron.ver`, la segunda
barrera de la base contra el evaluador, y la paginación contra la lista completa.
"""

import base64
import re
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.autorizacion.evaluador import Decision
from app.autorizacion.requiere import Permiso
from app.consorcios.mandatos import Discrepancia
from app.db import transaccion
from tests.test_contexto_mandatos import cliente, denegaciones
from tests.test_identidad import app, navegador  # noqa: F401  (`app` es un fixture)
from tests.test_mandatos import otra_persona
from tests.test_mandatos_0012 import alta
from tests.test_mandatos_rutas import decision, escena  # noqa: F401  (fixture)
from tests.test_modo_operacion import consorcio_con_persona, decidir_equipo

pytestmark = [pytest.mark.db, pytest.mark.redis]

UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
CAMPOS_PROPIO = {"fecha", "accion", "recurso", "origen", "cambios"}
CAMPOS_CONSORCIO = CAMPOS_PROPIO | {"actor"}
SECRETOS = ("hash", "sha256", "secuencia", "ip", "testclient", "persona_id", "decision_id", "@")


def _get(persona, url):
    with cliente(persona) as c:
        return c.get(url)


def _url(cons, resto=""):
    return f"/api/v1/consorcios/{cons}/auditoria{resto}"


def _nombre(motor_migrate, persona, nombre):
    with motor_migrate.begin() as c:
        c.execute(text("update nucleo.persona set nombre = :n where id = :p"),
                  {"n": nombre, "p": persona})  # fmt: skip


def _crudo(motor_migrate, cons, accion):
    with motor_migrate.begin() as c:
        return (
            c.execute(
                text("select * from auditoria.eventos where consorcio_id = :c and accion = :a"),
                {"c": cons, "a": accion},
            )
            .mappings()
            .all()
        )


def _sin_datos_tecnicos(r):
    assert UUID.search(r.text) is None, r.text
    cuerpo = r.text.lower()
    for clave in SECRETOS:
        assert f'"{clave}' not in cuerpo, clave


def _insertar(motor_migrate, cons, accion, actor=None, origen="soporte", creado_en=None,
              despues=None):  # fmt: skip
    with motor_migrate.begin() as c:
        c.execute(
            text(
                "insert into auditoria.eventos (consorcio_id, actor_persona_id, origen, accion,"
                " entidad, despues, creado_en, hash_previo, hash)"
                " values (:c, :a, :o, :ac, 'prueba', cast(:d as jsonb),"
                " coalesce(:t, clock_timestamp()), '', '')"
            ),
            {"c": cons, "a": actor, "o": origen, "ac": accion, "d": despues, "t": creado_en},
        )


# Eventos propios ----------------------------------------------------------------------------


def test_cada_uno_lee_sus_eventos_con_la_proyeccion(escena, motor_migrate):  # noqa: F811
    e = escena
    assert decision(e["mc1"], e["cons"]).status_code == 201
    r = _get(e["mc1"], _url(e["cons"], "/mis-eventos"))
    assert r.status_code == 200, r.text
    filas = r.json()["elementos"]
    assert [(f["accion"], f["recurso"], f["origen"]) for f in filas] == [
        ("Registró una decisión", "Decisión", "aplicacion")]  # fmt: skip
    assert set(filas[0]) == CAMPOS_PROPIO
    assert filas[0]["cambios"] == [
        {"campo": "tipo", "antes": None, "despues": "asamblea"},
        {"campo": "fecha", "antes": None, "despues": "2026-03-10"},
    ]
    _sin_datos_tecnicos(r)
    # Control: el evento crudo sí tiene el actor, la decisión y lo que la proyección deja afuera.
    (crudo,) = [f for f in _crudo(motor_migrate, e["cons"], "consorcio.decision.registrar")
                if f["origen"] == "api"]  # fmt: skip
    assert crudo["actor_persona_id"] == e["mc1"] and crudo["entidad_id"] and crudo["hash"]
    assert crudo["ip"] is None and crudo["despues"]["tipo"] == "asamblea"
    # Otro miembro del Consejo no lo ve entre los suyos.
    otro = _get(e["mc2"], _url(e["cons"], "/mis-eventos"))
    assert (otro.status_code, otro.json()["elementos"]) == (200, [])


def test_la_negacion_no_sale_entre_los_eventos_propios(escena, motor_app):  # noqa: F811
    e = escena
    r = _get(e["prop"], _url(e["cons"]))
    assert (r.status_code, r.json()["codigo"]) == (403, "sin_permiso")
    # Control: la negación quedó auditada, con su motivo interno.
    assert [d["accion"] for d in denegaciones(motor_app, e["cons"], e["mc2"], e["prop"])] == [
        "auditoria.ver"]  # fmt: skip
    propios = _get(e["prop"], _url(e["cons"], "/mis-eventos"))
    assert (propios.status_code, propios.json()["elementos"]) == (200, [])
    assert "denegad" not in propios.text and "sin_concesion" not in propios.text


def test_sin_vinculo_ni_mandato_no_hay_eventos_propios(escena, motor_migrate):  # noqa: F811
    e = escena
    ajena = otra_persona(motor_migrate, e["cons"])
    r = _get(ajena, _url(e["cons"], "/mis-eventos"))
    assert r.status_code == 404
    # Control: con vínculo en el consorcio, la misma ruta responde.
    assert _get(e["vecina"], _url(e["cons"], "/mis-eventos")).status_code == 200


# Eventos del consorcio ----------------------------------------------------------------------


def test_el_consejo_lee_el_consorcio_con_el_actor_segun_padron(escena, motor_migrate):  # noqa: F811
    e = escena
    _nombre(motor_migrate, e["mc1"], "Diego Prueba")
    assert decision(e["mc1"], e["cons"]).status_code == 201
    assert decision(e["mc2"], e["cons"]).status_code == 201
    r = _get(e["mc2"], _url(e["cons"]))
    assert r.status_code == 200, r.text
    filas = r.json()["elementos"]
    assert all(set(f) == CAMPOS_CONSORCIO for f in filas)
    decisiones = [f["actor"] for f in filas if f["accion"] == "Registró una decisión"]
    # La más reciente es la propia; la otra, con el nombre (MC tiene `padron.ver`); la del acta
    # de la escena la cargó el equipo.
    assert decisiones == [{"tipo": "vos", "nombre": None},
                          {"tipo": "persona", "nombre": "Diego Prueba"},
                          {"tipo": "soporte", "nombre": None}]  # fmt: skip
    # Las altas de la escena las registró el equipo: sin nombre.
    altas = [f for f in filas if f["accion"] == "Dio de alta un mandato"]
    assert len(altas) == 2
    assert {(f["actor"]["tipo"], f["actor"]["nombre"], f["origen"]) for f in altas} == {
        ("soporte", None, "soporte")}  # fmt: skip
    assert {c["campo"] for c in altas[0]["cambios"]} <= {"tipo", "desde", "hasta"}
    _sin_datos_tecnicos(r)


def test_sin_padron_ver_el_actor_no_tiene_nombre(escena, motor_migrate, motor_app):  # noqa: F811
    e = escena
    _nombre(motor_migrate, e["mc1"], "Diego Prueba")
    assert decision(e["mc1"], e["cons"]).status_code == 201
    lector = otra_persona(motor_migrate, e["cons"])
    alta(motor_app, e["cons"], None, decidir_equipo(motor_app, e["cons"]), tipo="miembro_consejo",
         persona=lector, facultades=["auditoria.ver"], origen="soporte")  # fmt: skip
    r = _get(lector, _url(e["cons"]))
    assert r.status_code == 200, r.text
    de_la_api = ("Registró una decisión", "aplicacion")
    actores = [f["actor"] for f in r.json()["elementos"] if (f["accion"], f["origen"]) == de_la_api]
    assert actores == [{"tipo": "persona", "nombre": None}]
    assert "Diego" not in r.text
    # Control: quien tiene `padron.ver` ve el nombre del mismo evento.
    assert "Diego Prueba" in _get(e["mc2"], _url(e["cons"])).text


def test_la_propietaria_y_el_externo_no_leen_el_consorcio(escena, motor_migrate, motor_app):  # noqa: F811
    e = escena
    externo = otra_persona(motor_migrate, e["cons"])
    alta(motor_app, e["cons"], None, decidir_equipo(motor_app, e["cons"]),
         tipo="administrador_externo", persona=externo, nombre="Estudio", cuit="20111111112",
         origen="soporte")  # fmt: skip
    for quien in (e["prop"], externo):
        r = _get(quien, _url(e["cons"]))
        assert (r.status_code, r.json()["codigo"]) == (403, "sin_permiso")
    motivos = [d["motivo"] for p in (e["prop"], externo)
               for d in denegaciones(motor_app, e["cons"], e["mc2"], p)]  # fmt: skip
    assert motivos == ["sin_concesion", "sin_concesion"]
    # Control: los dos leen sus propios eventos.
    assert {_get(q, _url(e["cons"], "/mis-eventos")).status_code for q in (e["prop"], externo)} == {
        200}  # fmt: skip


def test_la_lista_blanca_deja_afuera_lo_que_no_esta(escena, motor_migrate):  # noqa: F811
    e = escena
    _insertar(motor_migrate, e["cons"], "identidad.humana.verificar",
              despues='{"metodo": "presencial"}')  # fmt: skip
    _insertar(motor_migrate, e["cons"], "consorcio.vinculo.anular",
              despues='{"referencia": "Nota de Juana Pérez", "tipo": "propietario"}')  # fmt: skip
    r = _get(e["mc1"], _url(e["cons"]))
    assert r.status_code == 200, r.text
    acciones = [f["accion"] for f in r.json()["elementos"]]
    assert "Anuló un vínculo importado" in acciones  # control: la acción de la lista sale
    assert not any("identidad" in a.lower() or "verific" in a.lower() for a in acciones)
    assert "presencial" not in r.text and "Juana" not in r.text and "referencia" not in r.text
    anulado = next(f for f in r.json()["elementos"] if f["accion"] == "Anuló un vínculo importado")
    assert anulado["cambios"] == [{"campo": "tipo", "antes": None, "despues": "propietario"}]


def test_no_se_ven_eventos_de_otro_consorcio(escena, motor_migrate):  # noqa: F811
    e = escena
    otro, _, _ = consorcio_con_persona(motor_migrate)
    _insertar(motor_migrate, otro, "consorcio.padron.importar")
    r = _get(e["mc1"], _url(e["cons"]))
    assert all(f["accion"] != "Importó el padrón" for f in r.json()["elementos"])
    # Control: el mismo evento en este consorcio sale.
    _insertar(motor_migrate, e["cons"], "consorcio.padron.importar")
    acciones = [f["accion"] for f in _get(e["mc1"], _url(e["cons"])).json()["elementos"]]
    assert acciones.count("Importó el padrón") == 1


# Paginación ---------------------------------------------------------------------------------


def _todas(persona, url, limite):
    filas, cursor, paginas = [], None, 0
    while True:
        r = _get(persona, f"{url}?limite={limite}" + (f"&cursor={cursor}" if cursor else ""))
        assert r.status_code == 200, r.text
        filas += r.json()["elementos"]
        cursor, paginas = r.json()["siguiente"], paginas + 1
        if cursor is None:
            return filas, paginas


def test_el_cursor_recorre_todo_una_vez_aun_con_fechas_iguales(escena, motor_migrate):  # noqa: F811
    e = escena
    # Más adelante que todo lo de la escena: quedan primeros y la página de 2 corta entre ellos.
    misma = datetime(2030, 6, 1, 12, 0, 0, 123456, tzinfo=UTC)
    for _ in range(3):
        _insertar(motor_migrate, e["cons"], "consorcio.decision.registrar", creado_en=misma,
                  despues='{"tipo": "asamblea"}')  # fmt: skip
    for _ in range(2):
        assert decision(e["mc1"], e["cons"]).status_code == 201
    completa = _get(e["mc1"], _url(e["cons"]) + "?limite=100").json()
    assert completa["siguiente"] is None
    fechas = [f["fecha"] for f in completa["elementos"]]
    assert fechas == sorted(fechas, reverse=True)
    iguales = [i for i, f in enumerate(completa["elementos"])
               if datetime.fromisoformat(f["fecha"]) == misma]  # fmt: skip
    assert iguales == [0, 1, 2]
    for limite in (1, 2, 3):
        filas, paginas = _todas(e["mc1"], _url(e["cons"]), limite)
        assert filas == completa["elementos"], limite
        assert paginas >= len(filas) // limite
    # El cursor no lleva la secuencia ni identificadores: solo la fecha y cuántos saltar.
    cursor = _get(e["mc1"], _url(e["cons"]) + "?limite=2").json()["siguiente"]
    fecha, saltar = base64.urlsafe_b64decode(cursor).decode().split("|")
    assert datetime.fromisoformat(fecha) == misma and saltar == "2"


def test_los_eventos_propios_tambien_se_paginan(escena):  # noqa: F811
    e = escena
    for _ in range(3):
        assert decision(e["mc1"], e["cons"]).status_code == 201
    completa = _get(e["mc1"], _url(e["cons"], "/mis-eventos")).json()["elementos"]
    filas, _ = _todas(e["mc1"], _url(e["cons"], "/mis-eventos"), 2)
    assert filas == completa and len(filas) == 3


@pytest.mark.parametrize(
    "consulta",
    ["limite=0", "limite=101", "cursor=no-es-base64!", "cursor=" + base64.urlsafe_b64encode(
        b"2026-06-01T12:00:00|1").decode(), "cursor=" + base64.urlsafe_b64encode(
        b"2026-06-01T12:00:00+00:00|0").decode(), "cursor=" + "A" * 201],
)  # fmt: skip
def test_un_cursor_o_limite_invalido_da_422(escena, consulta):  # noqa: F811
    r = _get(escena["mc1"], _url(escena["cons"]) + "?" + consulta)
    assert r.status_code == 422, r.text
    # Control: un cursor válido pasa.
    valido = base64.urlsafe_b64encode(b"2026-06-01T12:00:00+00:00|1").decode()
    assert _get(escena["mc1"], _url(escena["cons"]) + f"?cursor={valido}").status_code == 200


# Segunda barrera en la base -----------------------------------------------------------------


def _leer_base(motor_app, cons, persona, sql):
    with transaccion(motor_app, persona_id=persona, consorcio_id=cons) as c:
        return c.execute(text(sql)).all()


def test_la_base_exige_auditoria_ver(escena, motor_app):  # noqa: F811
    e = escena
    sql = "select * from auditoria.eventos_del_consorcio(null, 0, 10)"
    with pytest.raises(DBAPIError, match="sin auditoria.ver"):
        _leer_base(motor_app, e["cons"], e["prop"], sql)
    assert _leer_base(motor_app, e["cons"], e["mc1"], sql)  # control


def test_la_base_da_solo_los_eventos_del_que_lee(escena, motor_app):  # noqa: F811
    e = escena
    assert decision(e["mc1"], e["cons"]).status_code == 201
    sql = "select accion from auditoria.eventos_propios(null, 0, 10)"
    assert _leer_base(motor_app, e["cons"], e["mc2"], sql) == []
    assert [f[0] for f in _leer_base(motor_app, e["cons"], e["mc1"], sql)] == [
        "Registró una decisión"]  # fmt: skip


def test_la_base_niega_la_lista_blanca_y_una_pagina_invalida(escena, motor_app):  # noqa: F811
    e = escena
    with pytest.raises(DBAPIError, match="permission denied"):
        _leer_base(motor_app, e["cons"], e["mc1"], "select * from auditoria.proyeccion")
    for pagina in ("null, 0, 101", "null, 1, 10", "now(), -1, 10"):
        with pytest.raises(DBAPIError, match="página inválida"):
            _leer_base(motor_app, e["cons"], e["mc1"],
                       f"select * from auditoria.eventos_propios({pagina})")  # fmt: skip
    # Control: el tope exacto pasa.
    _leer_base(motor_app, e["cons"], e["mc1"],
               "select * from auditoria.eventos_propios(null, 0, 100)")  # fmt: skip


def test_la_facultad_esta_en_los_paquetes_de_mc_y_ad(motor_migrate):
    with motor_migrate.begin() as c:
        tipos = (
            c.execute(
                text(
                    "select tipo::text from nucleo.paquete_facultad"
                    " where facultad = 'auditoria.ver'"
                )
            )
            .scalars()
            .all()
        )
    assert sorted(tipos) == ["administrador", "miembro_consejo"]


def test_si_la_base_niega_y_el_evaluador_concede_falla_fuerte(escena, monkeypatch):  # noqa: F811
    e = escena
    monkeypatch.setattr(
        Permiso,
        "verificar",
        lambda self, entidad=None, **kw: Decision(True, 200, "concedido_por_facultad"),
    )
    with cliente(e["prop"]) as c, pytest.raises(Discrepancia):
        c.get(_url(e["cons"]))


@pytest.mark.parametrize("resto", ["", "/mis-eventos"])
def test_sin_sesion_responde_401(app, escena, resto):  # noqa: F811
    assert navegador(app).get(_url(escena["cons"], resto)).status_code == 401
