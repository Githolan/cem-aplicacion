"""RF05: lectura de los esquemas de coeficientes y sus reglas por la API (migración 0029).

Diseño: `doc/diseno/H2b_5_Diseno.md` del repositorio CEM, §3: solo lectura, para quien tiene
`padron.ver` o `coeficientes.proponer`. Cada coeficiente se decide con esas dos políticas, fila por
fila: `padron.ver` sobre la unidad, `coeficientes.proponer` sobre el grupo. Los porcentajes son
sintéticos (suman 99,96 % como los de Pareja 3750, sin sus datos reales).
"""

from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.autorizacion.evaluador import Decision
from app.autorizacion.requiere import Permiso
from app.consorcios.padron import Discrepancia
from app.db import transaccion
from tests.test_coeficientes_0021 import AGUA, EXPENSAS, porcentajes, registrar
from tests.test_contexto_mandatos import cliente, denegaciones, otra_unidad
from tests.test_identidad import app, navegador  # noqa: F401  (`app` es un fixture)
from tests.test_mandatos import otra_persona
from tests.test_mandatos_0012 import alta
from tests.test_modo_operacion import cambiar, consorcio_con_persona, decidir_equipo

pytestmark = pytest.mark.db

DESDE = date(2024, 1, 1)
CAMBIO = date(2025, 1, 1)
EXPENSAS_V2 = ("20.000000", "20.000000", "20.000000", "20.000000", "19.960000")


@pytest.fixture
def edificio(motor_migrate, motor_app):
    """Cinco unidades, administrador aumentado, expensas en dos versiones y agua en una."""
    cons, u1, per = consorcio_con_persona(motor_migrate)
    unidades = [u1] + [otra_unidad(motor_migrate, cons, f"{n}° A")[0] for n in range(2, 6)]
    acta = decidir_equipo(motor_app, cons)
    cambiar(motor_app, cons, "administrador_aumentado", acta)
    e = {"cons": cons, "u": unidades, "per": per, "acta": acta}
    registrar(motor_app, cons, porcentajes(e, *EXPENSAS), decision=acta, desde=DESDE)
    registrar(motor_app, cons, porcentajes(e, *EXPENSAS_V2), decision=decidir_equipo(motor_app,
              cons), desde=CAMBIO)  # fmt: skip
    registrar(motor_app, cons, porcentajes(e, *AGUA), grupo="agua", decision=acta, desde=DESDE)
    return e


def lector(motor_migrate, motor_app, e, *, tipo="administrador", facultades=None):
    quien = otra_persona(motor_migrate, e["cons"])
    alta(motor_app, e["cons"], None, decidir_equipo(motor_app, e["cons"]), tipo=tipo,
         persona=quien, facultades=facultades, origen="soporte")  # fmt: skip
    return quien


def leer(persona, cons):
    with cliente(persona) as c:
        return c.get(f"/api/v1/consorcios/{cons}/coeficientes")


def coeficientes_de(cuerpo, grupo, version) -> dict[str, str]:
    [esquema] = [x for x in cuerpo["elementos"] if (x["grupo"], x["version"]) == (grupo, version)]
    return {c["unidad_id"]: c["porcentaje"] for c in esquema["coeficientes"]}


@pytest.mark.redis
def test_la_administracion_lee_todo_tal_como_esta_guardado(edificio, motor_migrate, motor_app):
    e = edificio
    r = leer(lector(motor_migrate, motor_app, e), e["cons"])
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store"
    cuerpo = r.json()
    assert [(x["grupo"], x["version"], x["vigente_desde"], x["vigente_hasta"])
            for x in cuerpo["elementos"]] == [
        ("agua", 1, str(DESDE), None),
        ("expensas_comunes", 1, str(DESDE), str(CAMBIO)),
        ("expensas_comunes", 2, str(CAMBIO), None),
    ]  # fmt: skip
    # D4: tal como dice la fuente, sin normalizar (suma 99,96 %).
    assert coeficientes_de(cuerpo, "expensas_comunes", 1) == porcentajes(e, *EXPENSAS)
    assert coeficientes_de(cuerpo, "expensas_comunes", 2) == porcentajes(e, *EXPENSAS_V2)
    assert coeficientes_de(cuerpo, "agua", 1) == porcentajes(e, *AGUA)
    assert all(
        x["reglas"]
        == [{"version": 1, "metodo": "proporcional_a_coeficientes", "centavos": "restos_mayores"}]
        for x in cuerpo["elementos"]
    )
    # Lo mínimo: sin la referencia de la fuente, la decisión ni quién registró.
    assert all(set(x) == {"grupo", "version", "vigente_desde", "vigente_hasta", "reglas",
                          "coeficientes"} for x in cuerpo["elementos"])  # fmt: skip
    assert all(set(c) == {"unidad_id", "designacion", "porcentaje"}
               for x in cuerpo["elementos"] for c in x["coeficientes"])  # fmt: skip
    assert "Reglamento" not in r.text


@pytest.mark.redis
def test_el_consejo_lee_con_padron_ver_y_la_propietaria_no(edificio, motor_migrate, motor_app):
    e = edificio
    mc = lector(motor_migrate, motor_app, e, tipo="miembro_consejo")
    r = leer(mc, e["cons"])
    assert r.status_code == 200, r.text
    assert coeficientes_de(r.json(), "agua", 1) == porcentajes(e, *AGUA)
    # La propietaria, sin mandato: 403 auditado, con la acción que nombra el diseño.
    r = leer(e["per"], e["cons"])
    assert (r.status_code, r.json()["codigo"]) == (403, "sin_permiso")
    [d] = denegaciones(motor_app, e["cons"], mc, e["per"])
    assert (d["accion"], d["motivo"]) == ("coeficientes.ver_por_unidad", "sin_concesion")
    # No hay ruta para escribir coeficientes.
    with cliente(mc) as c:
        assert c.post(f"/api/v1/consorcios/{e['cons']}/coeficientes", json={}).status_code == 405


@pytest.mark.redis
def test_solo_con_coeficientes_proponer_tambien_lee(edificio, motor_migrate, motor_app):
    e = edificio
    quien = lector(motor_migrate, motor_app, e, facultades=["coeficientes.proponer"])
    r = leer(quien, e["cons"])
    assert r.status_code == 200, r.text
    assert coeficientes_de(r.json(), "expensas_comunes", 2) == porcentajes(e, *EXPENSAS_V2)


def otorgar(motor_migrate, motor_app, e, persona, facultad, recurso):
    """Un mandato sin lecturas y una facultad acotada a un recurso."""
    m = alta_sin_lectura(motor_app, e, persona)
    with motor_migrate.begin() as c:
        c.execute(
            text("insert into nucleo.facultad_otorgada (consorcio_id, mandato_id, facultad,"
                 " recurso, decision_id) values (:c, :m, :f, :r, :d)"),
            {"c": e["cons"], "m": m, "f": facultad, "r": recurso, "d": e["acta"]},
        )  # fmt: skip


def alta_sin_lectura(motor_app, e, persona):
    return alta(motor_app, e["cons"], None, decidir_equipo(motor_app, e["cons"]),
                persona=persona, facultades=["invitaciones.crear"], origen="soporte")  # fmt: skip


@pytest.mark.redis
def test_padron_ver_acotado_a_una_unidad_ve_solo_esa(edificio, motor_migrate, motor_app):
    e = edificio
    quien = otra_persona(motor_migrate, e["cons"])
    otorgar(motor_migrate, motor_app, e, quien, "padron.ver", f"unidad:{e['u'][2]}")
    r = leer(quien, e["cons"])
    assert r.status_code == 200, r.text
    for grupo, version in (("agua", 1), ("expensas_comunes", 1), ("expensas_comunes", 2)):
        assert list(coeficientes_de(r.json(), grupo, version)) == [str(e["u"][2])]


@pytest.mark.redis
def test_proponer_acotado_a_un_grupo_ve_solo_ese(edificio, motor_migrate, motor_app):
    e = edificio
    quien = otra_persona(motor_migrate, e["cons"])
    otorgar(motor_migrate, motor_app, e, quien, "coeficientes.proponer", "grupo:agua")
    r = leer(quien, e["cons"])
    assert r.status_code == 200, r.text
    assert [x["grupo"] for x in r.json()["elementos"]] == ["agua"]
    assert coeficientes_de(r.json(), "agua", 1) == porcentajes(e, *AGUA)


@pytest.mark.redis
def test_sin_ninguna_de_las_dos_403_auditado(edificio, motor_migrate, motor_app):
    e = edificio
    quien = otra_persona(motor_migrate, e["cons"])
    alta_sin_lectura(motor_app, e, quien)
    r = leer(quien, e["cons"])
    assert (r.status_code, r.json()["codigo"]) == (403, "sin_permiso")
    [d] = denegaciones(motor_app, e["cons"], quien, quien)
    assert (d["accion"], d["motivo"]) == ("coeficientes.ver_por_unidad", "sin_concesion")


@pytest.mark.redis
def test_un_mandato_en_otro_consorcio_no_da_lectura_en_este(edificio, motor_migrate, motor_app):
    e = edificio
    otro, _, _ = consorcio_con_persona(motor_migrate)
    adm = otra_persona(motor_migrate, otro)
    alta(motor_app, otro, None, decidir_equipo(motor_app, otro), persona=adm, origen="soporte")
    assert leer(adm, e["cons"]).status_code == 404
    # Control: en su consorcio lee (sin esquemas, la lista vacía).
    r = leer(adm, otro)
    assert (r.status_code, r.json()) == (200, {"elementos": []})


@pytest.mark.parametrize(
    "funcion", ["coeficientes_del_consorcio", "reglas_de_reparto_del_consorcio"]
)
def test_la_base_niega_sin_ninguna_de_las_dos_facultades(edificio, motor_migrate, motor_app,
                                                         funcion):  # fmt: skip
    e = edificio
    with pytest.raises(DBAPIError, match="sin padron.ver ni coeficientes.proponer"):
        with transaccion(motor_app, persona_id=e["per"], consorcio_id=e["cons"]) as c:
            c.execute(text(f"select * from nucleo.{funcion}()")).all()
    # Sin persona en la sesión tampoco.
    with pytest.raises(DBAPIError, match="sin ámbito"):
        with transaccion(motor_app, persona_id=None, consorcio_id=e["cons"]) as c:
            c.execute(text(f"select * from nucleo.{funcion}()")).all()
    # Control: con `coeficientes.proponer` acotado a un grupo la base deja leer; el filtro por
    # recurso lo hace la aplicación.
    quien = otra_persona(motor_migrate, e["cons"])
    otorgar(motor_migrate, motor_app, e, quien, "coeficientes.proponer", "grupo:agua")
    with transaccion(motor_app, persona_id=quien, consorcio_id=e["cons"]) as c:
        assert len(c.execute(text(f"select * from nucleo.{funcion}()")).all()) > 0


def test_las_lecturas_son_security_definer_y_solo_las_ejecuta_cem_app(motor_migrate):
    with motor_migrate.connect() as c:
        filas = c.execute(
            text(
                "select p.proname, p.prosecdef,"
                " has_function_privilege('cem_app', p.oid, 'execute') as app,"
                " has_function_privilege('public', p.oid, 'execute') as publico,"
                " array_to_string(p.proconfig, ',') as config"
                " from pg_proc p join pg_namespace n on n.oid = p.pronamespace"
                " where n.nspname = 'nucleo' and p.proname in"
                " ('coeficientes_del_consorcio', 'reglas_de_reparto_del_consorcio',"
                " 'lee_coeficientes') order by p.proname"
            )
        ).all()
    assert [tuple(f) for f in filas] == [
        ("coeficientes_del_consorcio", True, True, False, "search_path=pg_catalog, pg_temp"),
        ("lee_coeficientes", False, False, False, "search_path=pg_catalog, pg_temp"),
        ("reglas_de_reparto_del_consorcio", True, True, False, "search_path=pg_catalog, pg_temp"),
    ]


@pytest.mark.redis
def test_un_mandato_vencido_no_da_lectura(edificio, motor_migrate, motor_app):
    e = edificio
    _, quien = otra_unidad(motor_migrate, e["cons"], "6° A")  # con vínculo: tiene ámbito
    alta(motor_app, e["cons"], None, decidir_equipo(motor_app, e["cons"]), persona=quien,
         desde=date(2020, 1, 1), hasta=date(2021, 1, 1), origen="soporte")  # fmt: skip
    assert leer(quien, e["cons"]).status_code == 403
    # Control: con un mandato vigente, la misma persona lee.
    alta(motor_app, e["cons"], None, decidir_equipo(motor_app, e["cons"]), persona=quien,
         origen="soporte")  # fmt: skip
    assert leer(quien, e["cons"]).status_code == 200


@pytest.mark.redis
def test_sin_modo_proponer_no_lee_y_la_negacion_queda_auditada(motor_migrate, motor_app):
    """La base deja leer a quien tiene `coeficientes.proponer`, pero sin modo la política lo
    niega (como a proponer): no es una lista vacía, es un 403 auditado."""
    cons, u1, _ = consorcio_con_persona(motor_migrate)
    e = {"cons": cons, "u": [u1]}
    registrar(motor_app, cons, {str(u1): "100.000000"}, decision=decidir_equipo(motor_app, cons))
    quien = lector(motor_migrate, motor_app, e, facultades=["coeficientes.proponer"])
    r = leer(quien, cons)
    assert (r.status_code, r.json()["codigo"]) == (403, "sin_permiso")
    [d] = denegaciones(motor_app, cons, quien, quien)
    assert (d["accion"], d["motivo"]) == ("coeficientes.ver_por_grupo", "sin_modo_registrado")
    # Control: el Consejo, con `padron.ver`, que corre sin modo, lee el esquema.
    otro = lector(motor_migrate, motor_app, e, tipo="miembro_consejo")
    r = leer(otro, cons)
    assert r.status_code == 200 and coeficientes_de(r.json(), "expensas_comunes", 1) == {
        str(u1): "100.000000"
    }


@pytest.mark.redis
def test_sin_modo_proponer_acotado_a_un_grupo_tampoco_lee(motor_migrate, motor_app):
    """El evaluador niega primero por el alcance: las filas de otros grupos dan `recurso_no_alcanza`
    y las del grupo propio, `sin_modo_registrado`. Gana la negación por filtro: 403 auditado."""
    cons, u1, _ = consorcio_con_persona(motor_migrate)
    acta = decidir_equipo(motor_app, cons)
    registrar(motor_app, cons, {str(u1): "100.000000"}, decision=acta)
    registrar(motor_app, cons, {str(u1): "100.000000"}, grupo="agua", decision=acta)
    quien = otra_persona(motor_migrate, cons)
    otorgar(motor_migrate, motor_app, {"cons": cons, "acta": acta}, quien,
            "coeficientes.proponer", "grupo:agua")  # fmt: skip
    r = leer(quien, cons)
    assert (r.status_code, r.json()["codigo"]) == (403, "sin_permiso")
    [d] = denegaciones(motor_app, cons, quien, quien)
    assert (d["accion"], d["motivo"]) == ("coeficientes.ver_por_grupo", "sin_modo_registrado")


@pytest.mark.redis
def test_una_unidad_que_no_esta_en_un_esquema_lo_deja_afuera(edificio, motor_migrate, motor_app):
    e = edificio
    nueva, _ = otra_unidad(motor_migrate, e["cons"], "7° A")
    quien = otra_persona(motor_migrate, e["cons"])
    otorgar(motor_migrate, motor_app, e, quien, "padron.ver", f"unidad:{nueva}")
    r = leer(quien, e["cons"])
    # Acotada a una unidad sin coeficientes: ningún esquema, y no es una negación.
    assert (r.status_code, r.json()) == (200, {"elementos": []})
    assert denegaciones(motor_app, e["cons"], quien, quien) == []


def test_si_la_base_niega_y_el_evaluador_concede_falla_fuerte(edificio, monkeypatch):
    e = edificio
    monkeypatch.setattr(
        Permiso, "decidir", lambda self, entidad=None: Decision(True, 200, "concedido_por_facultad")
    )
    with cliente(e["per"]) as c, pytest.raises(Discrepancia):
        c.get(f"/api/v1/consorcios/{e['cons']}/coeficientes")


def test_coeficientes_sin_sesion_responde_401(app, edificio):  # noqa: F811
    assert (
        navegador(app).get(f"/api/v1/consorcios/{edificio['cons']}/coeficientes").status_code == 401
    )
