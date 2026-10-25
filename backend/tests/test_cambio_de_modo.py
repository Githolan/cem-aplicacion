"""El cambio de modo por soporte y su historia (RF41-C, 2b-6b, migración 0027; cierra E29).

Diseño: decisiones del propietario del 24-10-2026 (repositorio CEM, `doc/diseno/H2b_6_Diseno.md`,
§5). Cada negación tiene su control: el mismo pedido con el dato correcto pasa.
"""

from datetime import date, timedelta
from itertools import permutations
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db import transaccion
from tests.test_contexto_mandatos import cliente, denegaciones
from tests.test_mandatos import otra_persona
from tests.test_mandatos_0012 import CUIT_X, alta, baja
from tests.test_modo_operacion import (
    DESDE,
    cambiar,
    consorcio_con_persona,
    decidir,
    decidir_equipo,
    hoy_de,
)

pytestmark = pytest.mark.db

MODOS = ("consejo", "administrador_aumentado", "autogestion")
DIAGRAMA_12 = {
    ("consejo", "administrador_aumentado"),
    ("consejo", "autogestion"),
    ("administrador_aumentado", "autogestion"),
    ("autogestion", "administrador_aumentado"),
    ("administrador_aumentado", "consejo"),
}


def _hint(e: pytest.ExceptionInfo) -> str:
    return e.value.orig.diag.message_hint


def periodos(motor_migrate, cons: UUID) -> list[tuple[str, date, date | None]]:
    with motor_migrate.connect() as c:
        return [
            tuple(f)
            for f in c.execute(
                text(
                    "select modo::text, vigente_desde, vigente_hasta from nucleo.modo_operacion"
                    " where consorcio_id = :c order by vigente_desde"
                ),
                {"c": cons},
            )
        ]


@pytest.fixture
def en_consejo(motor_migrate, motor_app):
    """Un consorcio en Consejo desde el 01-01-2026, sin mandato de administración."""
    cons, _, per = consorcio_con_persona(motor_migrate)
    acta = decidir(motor_app, cons, per)
    cambiar(motor_app, cons, "consejo", acta)
    return {"cons": cons, "per": per, "acta": acta, "hoy": hoy_de(motor_app, cons)}


# E29 ------------------------------------------------------------------------------------------


def test_una_decision_respalda_un_solo_modo(en_consejo, motor_migrate, motor_app):
    e = en_consejo
    with pytest.raises(DBAPIError) as err:
        cambiar(motor_app, e["cons"], "administrador_aumentado", e["acta"], e["hoy"])
    assert _hint(err) == "decision_ya_usada"
    assert periodos(motor_migrate, e["cons"]) == [("consejo", DESDE, None)]
    # Control: con otra decisión de asamblea, el cambio se registra.
    nueva = decidir(motor_app, e["cons"], e["per"])
    cambiar(motor_app, e["cons"], "administrador_aumentado", nueva, e["hoy"])
    assert periodos(motor_migrate, e["cons"])[-1] == ("administrador_aumentado", e["hoy"], None)


@pytest.mark.parametrize("dias", [-1, 1, 30])
def test_un_cambio_no_es_retroactivo_ni_anticipado(en_consejo, motor_migrate, motor_app, dias):
    e = en_consejo
    nueva = decidir(motor_app, e["cons"], e["per"])
    with pytest.raises(DBAPIError) as err:
        cambiar(motor_app, e["cons"], "administrador_aumentado", nueva, e["hoy"] + timedelta(dias))
    assert _hint(err) == "fecha_invalida"
    assert periodos(motor_migrate, e["cons"]) == [("consejo", DESDE, None)]
    # Control: hoy, en la fecha del consorcio.
    cambiar(motor_app, e["cons"], "administrador_aumentado", nueva, e["hoy"])


def test_dos_cambios_el_mismo_dia_no(en_consejo, motor_app):
    e = en_consejo
    primera = decidir(motor_app, e["cons"], e["per"])
    cambiar(motor_app, e["cons"], "administrador_aumentado", primera, e["hoy"])
    with pytest.raises(DBAPIError) as err:
        cambiar(motor_app, e["cons"], "consejo", decidir(motor_app, e["cons"], e["per"]), e["hoy"])
    assert _hint(err) == "fecha_invalida"


def test_el_primer_modo_no_empieza_antes_de_su_decision(motor_migrate, motor_app):
    cons, _, per = consorcio_con_persona(motor_migrate)
    acta = decidir(motor_app, cons, per)  # del 01-01-2026
    with pytest.raises(DBAPIError) as err:
        cambiar(motor_app, cons, "consejo", acta, DESDE - timedelta(days=1))
    assert _hint(err) == "fecha_invalida"
    # Control: desde la fecha de la decisión. El primer modo puede ser cualquiera (frenado).
    cambiar(motor_app, cons, "administrador_aumentado", acta, DESDE)
    assert periodos(motor_migrate, cons) == [("administrador_aumentado", DESDE, None)]


@pytest.mark.parametrize(("origen", "destino"), list(permutations(MODOS, 2)))
def test_solo_las_transiciones_del_diagrama_12(motor_migrate, motor_app, origen, destino):
    cons, _, per = consorcio_con_persona(motor_migrate)
    cambiar(motor_app, cons, origen, decidir(motor_app, cons, per))
    hoy = hoy_de(motor_app, cons)
    nueva = decidir(motor_app, cons, per)
    if (origen, destino) in DIAGRAMA_12:
        cambiar(motor_app, cons, destino, nueva, hoy)
        assert periodos(motor_migrate, cons) == [(origen, DESDE, hoy), (destino, hoy, None)]
    else:
        assert (origen, destino) == ("autogestion", "consejo")  # la única que falta
        with pytest.raises(DBAPIError) as err:
            cambiar(motor_app, cons, destino, nueva, hoy)
        assert _hint(err) == "transicion_no_admitida"
        assert periodos(motor_migrate, cons) == [(origen, DESDE, None)]


# Mandatos al salir de un modo -------------------------------------------------------------------


def mandatos_terminados(motor_migrate, cons: UUID) -> int:
    with motor_migrate.connect() as c:
        return c.execute(
            text("select count(*) from nucleo.mandato where consorcio_id = :c"
                 " and causa_baja is not null"), {"c": cons},
        ).scalar_one()  # fmt: skip


@pytest.mark.parametrize(
    ("modo", "tipo", "destino"),
    [
        ("administrador_aumentado", "administrador", "consejo"),
        ("administrador_aumentado", "administrador", "autogestion"),
        ("autogestion", "administrador", "administrador_aumentado"),
        ("consejo", "administrador_externo", "administrador_aumentado"),
        ("consejo", "administrador_externo", "autogestion"),
    ],
)
def test_salir_con_el_mandato_de_administracion_vigente_se_bloquea(
    motor_migrate, motor_app, modo, tipo, destino
):
    """Nunca se infiere ni se ejecuta una baja: la transición se niega, el mandato sigue igual, y
    con la baja ya registrada (que corta hoy) la misma transición pasa."""
    cons, _, per = consorcio_con_persona(motor_migrate)
    cambiar(motor_app, cons, modo, decidir(motor_app, cons, per))
    acta = decidir_equipo(motor_app, cons)
    if tipo == "administrador":
        m = alta(motor_app, cons, None, acta, persona=otra_persona(motor_migrate, cons),
                 origen="soporte")  # fmt: skip
    else:
        m = alta(motor_app, cons, None, acta, tipo=tipo, nombre="Estudio de prueba", cuit=CUIT_X,
                 origen="soporte")  # fmt: skip
    hoy = hoy_de(motor_app, cons)
    nueva = decidir(motor_app, cons, per)
    with pytest.raises(DBAPIError) as err:
        cambiar(motor_app, cons, destino, nueva, hoy)
    assert _hint(err) == "transicion_requiere_fin_de_mandato"
    assert mandatos_terminados(motor_migrate, cons) == 0
    assert periodos(motor_migrate, cons) == [(modo, DESDE, None)]
    # Control: dada de baja la administración (corta hoy), la transición se registra.
    baja(motor_app, cons, None, m, "renuncia", instrumento="Nota de renuncia",
         descripcion="Renuncia", origen="soporte")  # fmt: skip
    cambiar(motor_app, cons, destino, nueva, hoy)
    assert periodos(motor_migrate, cons)[-1] == (destino, hoy, None)


def test_un_mandato_de_administracion_futuro_tambien_bloquea(en_consejo, motor_migrate, motor_app):
    e = en_consejo
    alta(motor_app, e["cons"], None, decidir_equipo(motor_app, e["cons"]),
         tipo="administrador_externo", nombre="Estudio que asume", cuit=CUIT_X,
         desde=e["hoy"] + timedelta(days=10), origen="soporte")  # fmt: skip
    with pytest.raises(DBAPIError) as err:
        cambiar(motor_app, e["cons"], "administrador_aumentado",
                decidir(motor_app, e["cons"], e["per"]), e["hoy"])  # fmt: skip
    assert _hint(err) == "transicion_requiere_fin_de_mandato"


def test_un_historial_sin_periodo_abierto_es_ambiguo(en_consejo, motor_migrate, motor_app):
    e = en_consejo
    with motor_migrate.begin() as c:
        c.execute(
            text("update nucleo.modo_operacion set vigente_hasta = :h where consorcio_id = :c"),
            {"h": e["hoy"], "c": e["cons"]},
        )
    with pytest.raises(DBAPIError) as err:
        cambiar(motor_app, e["cons"], "administrador_aumentado",
                decidir(motor_app, e["cons"], e["per"]), e["hoy"])  # fmt: skip
    assert _hint(err) == "modo_ambiguo"


def test_un_pedido_api_no_cambia_el_modo(en_consejo, motor_migrate, motor_app):
    e = en_consejo
    nueva = decidir(motor_app, e["cons"], e["per"])
    with pytest.raises(DBAPIError) as err:
        with transaccion(motor_app, persona_id=e["per"], consorcio_id=e["cons"]) as c:
            c.execute(
                text("select nucleo.cambiar_modo('administrador_aumentado', :d, :h, :p, 'api')"),
                {"d": nueva, "h": e["hoy"], "p": e["per"]},
            )
    assert _hint(err) == "origen_invalido"
    assert periodos(motor_migrate, e["cons"]) == [("consejo", DESDE, None)]


# GET …/modos --------------------------------------------------------------------------------------


@pytest.mark.redis
def test_el_consejo_lee_la_historia_minima_y_la_propietaria_no(en_consejo, motor_migrate,
                                                               motor_app):  # fmt: skip
    e = en_consejo
    mc = otra_persona(motor_migrate, e["cons"])
    alta(motor_app, e["cons"], None, decidir_equipo(motor_app, e["cons"]), tipo="miembro_consejo",
         persona=mc, origen="soporte")  # fmt: skip
    nueva = decidir(motor_app, e["cons"], e["per"])
    cambiar(motor_app, e["cons"], "administrador_aumentado", nueva, e["hoy"])
    url = f"/api/v1/consorcios/{e['cons']}/modos"
    with cliente(mc) as c:
        r = c.get(url)
    assert r.status_code == 200, r.text
    assert r.json() == {"elementos": [
        {"modo": "administrador_aumentado", "vigente_desde": str(e["hoy"]), "vigente_hasta": None,
         "decision_id": str(nueva)},
        {"modo": "consejo", "vigente_desde": str(DESDE), "vigente_hasta": str(e["hoy"]),
         "decision_id": str(e["acta"])},
    ]}  # fmt: skip
    # La propietaria, sin `decisiones.ver`: 403 auditado.
    with cliente(e["per"]) as c:
        r = c.get(url)
    assert (r.status_code, r.json()["codigo"]) == (403, "sin_permiso")
    [d] = denegaciones(motor_app, e["cons"], mc, e["per"])
    assert (d["accion"], d["motivo"]) == ("modos.ver", "sin_concesion")
    # No hay ruta para escribir el modo.
    with cliente(mc) as c:
        assert c.post(url, json={}).status_code == 405


def test_la_base_niega_la_historia_a_quien_no_lee_decisiones(en_consejo, motor_app):
    e = en_consejo
    with pytest.raises(DBAPIError, match="sin decisiones.ver"):
        with transaccion(motor_app, persona_id=e["per"], consorcio_id=e["cons"]) as c:
            c.execute(text("select * from nucleo.modos_del_consorcio()")).all()
