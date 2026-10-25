"""Fin de vínculo (2b-5, PR 1; PA17): migración 0020 y su ruta, contra PostgreSQL real.

Diseño: `doc/diseno/H2b_5_Diseno.md` del repositorio CEM, §2. Cada regla tiene su caso positivo,
su negativo y una mutación anotada en el PR que demuestra que la prueba la detecta. La migración
de ida y vuelta corre en la base aparte de `test_vinculo_0014`.
"""

from datetime import timedelta
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, ProgrammingError

from app.db import transaccion
from tests.conftest import clave_idempotencia
from tests.test_contexto_mandatos import (  # noqa: F401  (`edificio` es un fixture)
    cliente,
    denegaciones,
    edificio,
    segundo_consorcio_con_mandato,
)
from tests.test_identidad import (  # noqa: F401  (`app` es un fixture)
    app,
    navegador,
)
from tests.test_mandatos import otra_persona, sesiones_abiertas
from tests.test_mandatos_0012 import alta
from tests.test_modo_operacion import cambiar, consorcio_con_persona, decidir_equipo
from tests.test_padron_rutas import escena as escena_padron  # noqa: F401  (es un fixture)
from tests.test_padron_rutas import padron_url
from tests.test_vinculo_0014 import (  # noqa: F401  (`base_aparte` es un fixture)
    _catalogo,
    _consorcio_y_unidad,
    _hoy,
    _poner_vigencia,
    base_aparte,
    cadena_rota,
    confirmar,
    eventos,
    invitar_y_aceptar,
)

pytestmark = pytest.mark.db

MOTIVO = "Fin del contrato de locación del 01-03-2024"
SESION = object()
COLUMNAS_RESPUESTA = {"vinculo_id", "unidad_id", "tipo", "vigente_hasta", "finalizado_en"}


@pytest.fixture
def escena(escena_padron, motor_migrate):  # noqa: F811
    """La escena del padrón, con todos los vínculos empezados hace 30 días: `aceptar_invitacion`
    los crea con fecha de hoy, y así el fin habitual deja `vigente_hasta > vigente_desde`."""
    e = escena_padron
    with motor_migrate.begin() as c:
        c.execute(
            text(
                "update nucleo.vinculo set vigente_desde ="
                " least(vigente_desde, nucleo.hoy_del_consorcio(consorcio_id) - 30)"
                " where consorcio_id = :c"
            ),
            {"c": e["cons"]},
        )
    return e


def finalizar(motor_app, cons, sesion, vinculo, *, motivo=MOTIVO, actor=SESION, origen="api"):
    with transaccion(motor_app, persona_id=sesion, consorcio_id=cons) as c:
        return c.execute(
            text("select nucleo.finalizar_vinculo(:v, :m, :a, :o)"),
            {"v": vinculo, "m": motivo, "a": sesion if actor is SESION else actor, "o": origen},
        ).scalar_one()


def fila(motor_migrate, vinculo) -> dict:
    with motor_migrate.connect() as c:
        return dict(
            c.execute(
                text(
                    "select persona_id, vigente_desde, vigente_hasta, finalizado_por,"
                    " finalizado_en, motivo_fin, confirmado_en, confirmacion_referencia"
                    " from nucleo.vinculo where id = :v"
                ),
                {"v": vinculo},
            )
            .mappings()
            .one()
        )


def motivo_de(error) -> str:
    return error.value.orig.diag.message_hint


def persona_de(motor_migrate, vinculo):
    return fila(motor_migrate, vinculo)["persona_id"]


def finalizar_url(cons, vinculo) -> str:
    return f"/api/v1/consorcios/{cons}/vinculos/{vinculo}/finalizar"


def pedir_fin(c, cons, vinculo, *, motivo=MOTIVO, clave=None):
    return c.post(
        finalizar_url(cons, vinculo), json={"motivo": motivo}, headers=clave or clave_idempotencia()
    )


# La función -----------------------------------------------------------------------------------


def test_pa17_cierra_la_vigencia_hoy_conserva_la_fila_y_audita_sin_el_motivo(
    escena,  # noqa: F811
    motor_migrate,
    motor_app,
):
    e = escena
    confirmar(motor_app, e["cons"], e["admin"], e["v_inq_mc"])
    antes = fila(motor_migrate, e["v_inq_mc"])
    finalizar(motor_app, e["cons"], e["admin"], e["v_inq_mc"])
    despues = fila(motor_migrate, e["v_inq_mc"])
    hoy = _hoy(motor_migrate, e["cons"])
    assert despues["vigente_hasta"] == hoy and antes["vigente_hasta"] is None
    assert despues["vigente_desde"] < hoy
    assert (despues["finalizado_por"], despues["motivo_fin"]) == (e["admin"], MOTIVO)
    assert despues["finalizado_en"] is not None
    # La confirmación y el resto de la fila quedan como estaban (historial permitido).
    for k in ("persona_id", "vigente_desde", "confirmado_en", "confirmacion_referencia"):
        assert despues[k] == antes[k]
    [ev] = eventos(motor_migrate, e["cons"], "consorcio.vinculo.finalizar")
    assert (ev["actor_persona_id"], ev["origen"], ev["entidad_id"]) == (
        e["admin"], "api", e["v_inq_mc"],
    )  # fmt: skip
    assert ev["antes"] == {"vigente_hasta": None}
    assert ev["despues"]["vigente_hasta"] == hoy.isoformat()
    assert "Fin del contrato" not in str(ev)
    assert cadena_rota(motor_migrate, e["cons"]) == 0


def test_pa17_sin_otro_acceso_al_consorcio_cierra_sus_sesiones(escena, motor_migrate, motor_app):  # noqa: F811
    e = escena
    inquilino = persona_de(motor_migrate, e["v_inq_mc"])
    abiertas = sesiones_abiertas(motor_migrate, inquilino, 2)  # más la de la cuenta nueva
    assert abiertas == 3
    assert finalizar(motor_app, e["cons"], e["admin"], e["v_inq_mc"]) == abiertas
    assert sesiones_abiertas(motor_migrate, inquilino) == 0
    [ev] = eventos(motor_migrate, e["cons"], "consorcio.vinculo.finalizar")
    assert ev["despues"]["sesiones_revocadas"] == abiertas


def test_pa17_con_otro_vinculo_vigente_conserva_las_sesiones_hasta_el_ultimo(
    escena,  # noqa: F811
    motor_migrate,
    motor_app,
):
    e = escena
    inquilino = persona_de(motor_migrate, e["v_inq_mc"])
    otro = invitar_y_aceptar(motor_migrate, e["cons"], e["u2"], e["admin"], persona=inquilino)
    abiertas = sesiones_abiertas(motor_migrate, inquilino, 1)
    assert finalizar(motor_app, e["cons"], e["admin"], e["v_inq_mc"]) == 0
    assert sesiones_abiertas(motor_migrate, inquilino) == abiertas > 0
    # Control: al terminar el último, sí.
    assert finalizar(motor_app, e["cons"], e["admin"], otro) == abiertas
    assert sesiones_abiertas(motor_migrate, inquilino) == 0


def test_pa17_con_un_mandato_vigente_conserva_las_sesiones(escena, motor_migrate, motor_app):  # noqa: F811
    e = escena
    propio_mc = invitar_y_aceptar(motor_migrate, e["cons"], e["u2"], e["admin"], persona=e["mc"])
    abiertas = sesiones_abiertas(motor_migrate, e["mc"], 1)
    assert finalizar(motor_app, e["cons"], e["admin"], propio_mc) == 0
    assert sesiones_abiertas(motor_migrate, e["mc"]) == abiertas > 0


def test_pa17_un_vinculo_en_otro_consorcio_no_cuenta_como_acceso(escena, motor_migrate, motor_app):  # noqa: F811
    """Las sesiones son de la persona, no del consorcio: con acceso solo en otro consorcio, igual
    se cierran (la persona vuelve a entrar y sigue viendo el otro)."""
    e = escena
    inquilino = persona_de(motor_migrate, e["v_inq_mc"])
    cons_b, _ = segundo_consorcio_con_mandato(motor_migrate, motor_app, inquilino)
    abiertas = sesiones_abiertas(motor_migrate, inquilino, 1)
    assert finalizar(motor_app, e["cons"], e["admin"], e["v_inq_mc"]) == abiertas > 0
    with cliente(inquilino) as c:  # control: en el otro consorcio conserva el ámbito
        assert c.get(f"/api/v1/consorcios/{cons_b}/unidades").status_code == 200


def test_pa17_la_persona_pierde_el_acceso_que_le_daba_el_vinculo(escena, motor_migrate, motor_app):  # noqa: F811
    e = escena
    inquilino = persona_de(motor_migrate, e["v_inq_mc"])
    url = f"/api/v1/consorcios/{e['cons']}/unidades"
    with cliente(inquilino) as c:
        assert c.get(url).status_code == 200
    finalizar(motor_app, e["cons"], e["admin"], e["v_inq_mc"])
    with cliente(inquilino) as c:
        assert c.get(url).status_code == 404
    with cliente(e["admin"]) as c:  # y no sale en el padrón
        filas = c.get(padron_url(e["cons"])).json()["elementos"]
    assert str(e["v_inq_mc"]) not in {f["vinculo_id"] for f in filas}
    assert str(e["v_inq_admin"]) in {f["vinculo_id"] for f in filas}


def test_pa17_uno_que_empezo_hoy_queda_con_vigencia_vacia(escena, motor_migrate, motor_app):  # noqa: F811
    e = escena
    hoy = _hoy(motor_migrate, e["cons"])
    with motor_migrate.begin() as c:
        c.execute(
            text("update nucleo.vinculo set vigente_desde = :h where id = :v"),
            {"h": hoy, "v": e["v_inq_mc"]},
        )
    finalizar(motor_app, e["cons"], e["admin"], e["v_inq_mc"])
    f = fila(motor_migrate, e["v_inq_mc"])
    assert f["vigente_desde"] == f["vigente_hasta"] == hoy


def test_pa17_un_vinculo_terminado_o_futuro_no_se_finaliza(escena, motor_migrate, motor_app):  # noqa: F811
    e = escena
    hoy = _hoy(motor_migrate, e["cons"])
    finalizar(motor_app, e["cons"], e["admin"], e["v_inq_mc"])
    with pytest.raises(DBAPIError) as error:
        finalizar(motor_app, e["cons"], e["admin"], e["v_inq_mc"], motivo="Otra vez")
    assert motivo_de(error) == "vinculo_finalizado"
    assert fila(motor_migrate, e["v_inq_mc"])["motivo_fin"] == MOTIVO
    with motor_migrate.begin() as c:
        c.execute(
            text("update nucleo.vinculo set vigente_hasta = :h where id = :v"),
            {"h": hoy, "v": e["v_inq_admin"]},
        )
    with pytest.raises(DBAPIError) as error:  # terminó por su fecha, sin fin registrado
        finalizar(motor_app, e["cons"], e["admin"], e["v_inq_admin"])
    assert motivo_de(error) == "vinculo_finalizado"
    with motor_migrate.begin() as c:
        c.execute(
            text(
                "update nucleo.vinculo set vigente_desde = :d, vigente_hasta = null where id = :v"
            ),
            {"d": hoy.replace(year=hoy.year + 1), "v": e["v_prop1"]},
        )
    with pytest.raises(DBAPIError) as error:
        finalizar(motor_app, e["cons"], e["admin"], e["v_prop1"])
    assert motivo_de(error) == "vinculo_no_iniciado"
    assert fila(motor_migrate, e["v_prop1"])["finalizado_en"] is None


@pytest.mark.parametrize(
    ("origen", "actor", "con_sesion", "motivo"),
    [
        ("api", "otro", True, "actor_no_coincide"),
        ("api", None, True, "actor_requerido"),
        ("soporte", "propio", False, "origen_invalido"),
        ("soporte", None, True, "origen_invalido"),
        ("script", "propio", True, "origen_invalido"),
    ],
)
def test_origen_y_actor_como_en_la_baja_de_mandato(
    escena,  # noqa: F811
    motor_migrate,
    motor_app,
    origen,
    actor,
    con_sesion,
    motivo,
):
    e = escena
    actores = {"otro": e["mc"], "propio": e["admin"], None: None}
    with pytest.raises(DBAPIError) as error:
        finalizar(motor_app, e["cons"], e["admin"] if con_sesion else None, e["v_inq_mc"],
                  actor=actores[actor], origen=origen)  # fmt: skip
    assert motivo_de(error) == motivo
    assert fila(motor_migrate, e["v_inq_mc"])["finalizado_en"] is None


def test_el_equipo_finaliza_por_soporte_sin_actor(escena, motor_migrate, motor_app):  # noqa: F811
    e = escena
    finalizar(motor_app, e["cons"], None, e["v_inq_mc"], actor=None, origen="soporte")
    f = fila(motor_migrate, e["v_inq_mc"])
    assert f["finalizado_en"] is not None and f["finalizado_por"] is None
    [ev] = eventos(motor_migrate, e["cons"], "consorcio.vinculo.finalizar")
    assert (ev["actor_persona_id"], ev["origen"]) == (None, "soporte")


@pytest.mark.parametrize("motivo", [None, "", "   ", "x" * 301])
def test_el_motivo_es_obligatorio_y_acotado(escena, motor_migrate, motor_app, motivo):  # noqa: F811
    e = escena
    with pytest.raises(DBAPIError) as error:
        finalizar(motor_app, e["cons"], e["admin"], e["v_inq_mc"], motivo=motivo)
    assert motivo_de(error) == "motivo_requerido"
    # Control: 300 caracteres sí.
    finalizar(motor_app, e["cons"], e["admin"], e["v_inq_mc"], motivo="x" * 300)


def test_un_vinculo_de_otro_consorcio_o_inexistente_es_invalido(escena, motor_migrate, motor_app):  # noqa: F811
    e = escena
    cons_b, _ = segundo_consorcio_con_mandato(motor_migrate, motor_app, e["admin"])
    with motor_migrate.connect() as c:
        ajeno = c.execute(
            text("select id from nucleo.vinculo where consorcio_id = :c limit 1"), {"c": cons_b}
        ).scalar_one()
    for vinculo in (ajeno, uuid4()):
        with pytest.raises(DBAPIError) as error:
            finalizar(motor_app, e["cons"], e["admin"], vinculo)
        assert motivo_de(error) == "vinculo_invalido"
    assert fila(motor_migrate, ajeno)["finalizado_en"] is None
    with pytest.raises(DBAPIError) as error:  # sin consorcio en el ámbito
        finalizar(motor_app, None, e["admin"], e["v_inq_mc"])
    assert error.value.orig.sqlstate == "42501"


@pytest.mark.parametrize(
    "cambio",
    [
        "vigente_hasta = vigente_hasta + 365",
        "vigente_hasta = null",
        "motivo_fin = 'Otro motivo'",
        "finalizado_en = now() - interval '1 day'",
        "finalizado_por = null",
    ],
)
def test_un_fin_registrado_no_se_mueve_aunque_escriba_el_duenio(
    escena,  # noqa: F811
    motor_migrate,
    motor_app,
    cambio,
):
    """Si la vigencia de un vínculo finalizado se pudiera correr hacia adelante, volvería a dar
    acceso: `require_scope`, el padrón y `confirmar_vinculo` miran solo la vigencia (Codex)."""
    e = escena
    finalizar(motor_app, e["cons"], e["admin"], e["v_inq_mc"])
    antes = fila(motor_migrate, e["v_inq_mc"])
    with pytest.raises(DBAPIError) as error, motor_migrate.begin() as c:
        c.execute(text(f"update nucleo.vinculo set {cambio} where id = :v"), {"v": e["v_inq_mc"]})
    assert motivo_de(error) == "fin_inalterable"
    assert fila(motor_migrate, e["v_inq_mc"]) == antes


def test_un_fin_equivocado_se_anula_entero(escena, motor_migrate, motor_app):  # noqa: F811
    e = escena
    inquilino = persona_de(motor_migrate, e["v_inq_mc"])
    finalizar(motor_app, e["cons"], e["admin"], e["v_inq_mc"])
    with motor_migrate.begin() as c:
        c.execute(
            text(
                "update nucleo.vinculo set vigente_hasta = null, finalizado_en = null,"
                " finalizado_por = null, motivo_fin = null where id = :v"
            ),
            {"v": e["v_inq_mc"]},
        )
    with cliente(inquilino) as c:  # vuelve a regir: el control de que el trigger no frena esto
        assert c.get(f"/api/v1/consorcios/{e['cons']}/unidades").status_code == 200


def test_un_vinculo_finalizado_no_se_confirma(escena, motor_app):  # noqa: F811
    e = escena
    finalizar(motor_app, e["cons"], e["admin"], e["v_inq_mc"])
    with pytest.raises(DBAPIError) as error:
        confirmar(motor_app, e["cons"], e["admin"], e["v_inq_mc"])
    assert motivo_de(error) == "vinculo_finalizado"


def test_cem_app_lee_quien_y_cuando_pero_no_el_motivo(escena, motor_app):  # noqa: F811
    e = escena
    with transaccion(motor_app, persona_id=e["admin"], consorcio_id=e["cons"]) as c:
        c.execute(text("select finalizado_por, finalizado_en from nucleo.vinculo limit 1"))
    with pytest.raises(ProgrammingError, match="permission denied"):
        with transaccion(motor_app, persona_id=e["admin"], consorcio_id=e["cons"]) as c:
            c.execute(text("select motivo_fin from nucleo.vinculo limit 1"))


@pytest.mark.parametrize(
    ("cambio", "restriccion"),
    [
        ("finalizado_en = now()", "vinculo_fin_completo"),
        ("motivo_fin = 'x'", "vinculo_fin_completo"),
        ("finalizado_por = persona_id", "vinculo_fin_completo"),
        (
            "finalizado_en = now(), motivo_fin = ' ', vigente_hasta = vigente_desde + 1",
            "vinculo_motivo_fin",
        ),
        ("finalizado_en = now(), motivo_fin = 'x'", "vinculo_finalizado_con_vigencia"),
        ("vigente_hasta = vigente_desde - 1", "vinculo_vigencia_valida"),
        ("vigente_hasta = vigente_desde", "vinculo_vigencia_valida"),  # vacía sin fin registrado
    ],
)
def test_las_restricciones_valen_aunque_escriba_el_duenio_de_la_tabla(
    escena,  # noqa: F811
    motor_migrate,
    cambio,
    restriccion,
):
    with pytest.raises(DBAPIError) as error, motor_migrate.begin() as c:
        c.execute(text(f"update nucleo.vinculo set {cambio} where id = :v"),
                  {"v": escena["v_inq_mc"]})  # fmt: skip
    assert error.value.orig.diag.constraint_name == restriccion


# La ruta --------------------------------------------------------------------------------------


def test_finalizar_responde_lo_minimo_y_no_devuelve_el_motivo(escena, motor_migrate):  # noqa: F811
    e = escena
    with cliente(e["admin"]) as c:
        r = pedir_fin(c, e["cons"], e["v_inq_mc"])
    assert r.status_code == 200, r.text
    cuerpo = r.json()
    assert set(cuerpo) == COLUMNAS_RESPUESTA
    assert (cuerpo["vinculo_id"], cuerpo["unidad_id"], cuerpo["tipo"]) == (
        str(e["v_inq_mc"]), str(e["u1"]), "inquilino",
    )  # fmt: skip
    assert cuerpo["vigente_hasta"] == _hoy(motor_migrate, e["cons"]).isoformat()
    assert MOTIVO not in r.text
    assert fila(motor_migrate, e["v_inq_mc"])["motivo_fin"] == MOTIVO


def test_un_reintento_con_la_misma_clave_no_repite_el_efecto(escena, motor_migrate):  # noqa: F811
    e = escena
    clave = clave_idempotencia()
    with cliente(e["admin"]) as c:
        primera = pedir_fin(c, e["cons"], e["v_inq_mc"], clave=clave)
        segunda = pedir_fin(c, e["cons"], e["v_inq_mc"], clave=clave)
        nueva = pedir_fin(c, e["cons"], e["v_inq_mc"])
    assert primera.status_code == segunda.status_code == 200
    assert segunda.json() == primera.json()
    assert len(eventos(motor_migrate, e["cons"], "consorcio.vinculo.finalizar")) == 1
    # Control: con otra clave ya no es un reintento, y el vínculo ya terminó.
    assert (nueva.status_code, nueva.json()["codigo"]) == (409, "vinculo_finalizado")


def test_un_vinculo_futuro_responde_409(escena, motor_migrate):  # noqa: F811
    e = escena
    hoy = _hoy(motor_migrate, e["cons"])
    with motor_migrate.begin() as c:
        c.execute(
            text("update nucleo.vinculo set vigente_desde = :d where id = :v"),
            {"d": hoy.replace(year=hoy.year + 1), "v": e["v_inq_mc"]},
        )
    with cliente(e["admin"]) as c:
        r = pedir_fin(c, e["cons"], e["v_inq_mc"])
    assert (r.status_code, r.json()["codigo"]) == (409, "vinculo_no_iniciado")


def test_sin_padron_gestionar_o_fuera_de_su_unidad_responde_403(escena, motor_migrate, motor_app):  # noqa: F811
    e = escena
    with cliente(e["mc"]) as c:  # el Consejo no tiene padron.gestionar
        r = pedir_fin(c, e["cons"], e["v_inq_mc"])
    assert (r.status_code, r.json()["codigo"]) == (403, "sin_permiso")
    [d] = denegaciones(motor_app, e["cons"], e["admin"], e["mc"])
    assert d["accion"] == "padron.gestionar"
    with motor_migrate.begin() as c:
        n = c.execute(
            text(
                "update nucleo.facultad_otorgada set recurso = :r where mandato_id = :m"
                " and facultad = 'padron.gestionar'"
            ),
            {"r": f"unidad:{e['u2']}", "m": e["m_admin"]},
        ).rowcount
    assert n == 1
    with cliente(e["admin"]) as c:
        r = pedir_fin(c, e["cons"], e["v_inq_mc"])  # u1, fuera de su recurso
        assert r.status_code == 403
        assert pedir_fin(c, e["cons"], e["v_inq_admin"]).status_code == 200  # control: u2 sí
    assert fila(motor_migrate, e["v_inq_mc"])["finalizado_en"] is None


def test_un_vinculo_inexistente_o_de_otro_consorcio_responde_404(escena, motor_migrate, motor_app):  # noqa: F811
    e = escena
    cons_b, _ = segundo_consorcio_con_mandato(motor_migrate, motor_app, e["admin"])
    with motor_migrate.connect() as c:
        ajeno = c.execute(
            text("select id from nucleo.vinculo where consorcio_id = :c limit 1"), {"c": cons_b}
        ).scalar_one()
    with cliente(e["admin"]) as c:
        for vinculo in (uuid4(), ajeno):
            r = pedir_fin(c, e["cons"], vinculo)
            assert (r.status_code, r.json()["codigo"]) == (404, "no_encontrado")


@pytest.mark.parametrize("motivo", ["", "   ", "a\nb", "x" * 301])
def test_el_motivo_del_pedido_es_obligatorio_y_acotado(escena, motor_migrate, motivo):  # noqa: F811
    e = escena
    with cliente(e["admin"]) as c:
        r = pedir_fin(c, e["cons"], e["v_inq_mc"], motivo=motivo)
    assert (r.status_code, r.json()["codigo"]) == (422, "peticion_invalida")
    assert fila(motor_migrate, e["v_inq_mc"])["finalizado_en"] is None


def test_finalizar_sin_sesion_responde_401(app, escena):  # noqa: F811
    r = navegador(app).post(finalizar_url(escena["cons"], escena["v_inq_mc"]),
                            json={"motivo": MOTIVO}, headers=clave_idempotencia())  # fmt: skip
    assert r.status_code == 401


# Ida y vuelta ---------------------------------------------------------------------------------


def test_downgrade_con_un_vinculo_finalizado_se_niega_y_sin_el_vuelve_exacto(base_aparte):  # noqa: F811
    cfg, motor = base_aparte
    command.upgrade(cfg, "0019")
    en_0019 = _catalogo(motor)
    command.upgrade(cfg, "0020")
    cons, uni = _consorcio_y_unidad(motor)
    v = invitar_y_aceptar(motor, cons, uni, None)
    with motor.begin() as c:
        c.execute(text("select set_config('app.consorcio_id', :c, true)"), {"c": str(cons)})
        c.execute(text("select nucleo.finalizar_vinculo(:v, 'Venta', null, 'soporte')"), {"v": v})
    antes = _catalogo(motor)
    with pytest.raises(DBAPIError) as error:
        command.downgrade(cfg, "0019")
    assert motivo_de(error) == "downgrade_con_datos"
    assert _catalogo(motor) == antes
    with motor.begin() as c:  # control: sin el vínculo finalizado, baja y vuelve exacto
        c.execute(text("delete from nucleo.vinculo where id = :v"), {"v": v})
    command.downgrade(cfg, "0019")
    assert _catalogo(motor) == en_0019


def test_iniciado_y_el_fin_usan_la_fecha_del_consorcio_y_no_la_de_buenos_aires(
    motor_migrate, motor_app
):
    """El padrón dice `iniciado` con la misma fecha y regla que `finalizar_vinculo` (Codex en app
    #108): un consorcio en otra zona horaria puede estar en otro día que Buenos Aires.

    La base elige la zona que en este momento está en otro día que Buenos Aires: Kiritimati
    (GMT+14) desde las 10 hasta las 3 GMT, con Buenos Aires en X y el consorcio ya en X+1; el
    resto del día, Etc/GMT+12, con el consorcio todavía en X-1 (el caso inverso: la regla de
    Buenos Aires daría por empezado lo que la API no deja finalizar). El caso adelantado no se
    puede ejercitar a toda hora sin fijar now(). No depende de la zona horaria de la PC: las
    fechas las calcula la base."""
    with motor_migrate.connect() as c:
        zona = c.execute(
            text(
                "select z from unnest(array['Pacific/Kiritimati', 'Etc/GMT+12'])"
                " with ordinality as t(z, n)"
                " where (now() at time zone z)::date"
                " <> (now() at time zone 'America/Argentina/Buenos_Aires')::date"
                " order by n limit 1"
            )
        ).scalar_one()
    cons, u1, prop = consorcio_con_persona(motor_migrate, zona)
    acta = decidir_equipo(motor_app, cons)
    cambiar(motor_app, cons, "administrador_aumentado", acta)
    admin = otra_persona(motor_migrate, cons)
    alta(motor_app, cons, None, acta, persona=admin, origen="soporte")
    borde = invitar_y_aceptar(motor_migrate, cons, u1, None)
    futuro = invitar_y_aceptar(motor_migrate, cons, u1, None)
    with motor_migrate.connect() as c:
        hoy_consorcio, hoy_buenos_aires = c.execute(
            text(
                "select nucleo.hoy_del_consorcio(:c),"
                " (now() at time zone 'America/Argentina/Buenos_Aires')::date"
            ),
            {"c": cons},
        ).one()
    assert hoy_consorcio != hoy_buenos_aires  # control: la zona elegida está en otro día
    adelantado = hoy_consorcio > hoy_buenos_aires
    # Empieza el más tardío de los dos "hoy": para una fecha ya empezó y para la otra no.
    _poner_vigencia(motor_migrate, borde, max(hoy_consorcio, hoy_buenos_aires), None)
    _poner_vigencia(motor_migrate, futuro, hoy_consorcio + timedelta(days=1), None)

    with cliente(admin) as c:
        r = c.get(padron_url(cons))
        assert r.status_code == 200, r.text
        filas = {f["vinculo_id"]: f for f in r.json()["elementos"]}
        assert filas[str(borde)]["iniciado"] is adelantado
        assert filas[str(futuro)]["iniciado"] is False  # control inverso
        # El fin decide con la misma regla: lo que el padrón da por empezado se puede finalizar.
        fin = pedir_fin(c, cons, borde)
        if adelantado:
            assert fin.status_code == 200, fin.text
        else:
            assert (fin.status_code, fin.json()["codigo"]) == (409, "vinculo_no_iniciado")
        fin = pedir_fin(c, cons, futuro)
        assert (fin.status_code, fin.json()["codigo"]) == (409, "vinculo_no_iniciado")
