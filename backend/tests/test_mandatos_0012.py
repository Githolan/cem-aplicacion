"""Migración 0012 (H2b 2b-3) contra PostgreSQL real.

Diseño: `doc/diseno/H2b_3_Tabla_de_verdad.md` del repositorio CEM, §5.1 a §5.3. Cada regla se
prueba con su control (el caso que tiene que pasar) y las exclusiones, con una mutación que
demuestra que la prueba detectaría quitarlas. El upgrade y el downgrade se prueban en una base
aparte, para no depender de los datos de la sesión.
"""

import threading
from datetime import date, timedelta
from uuid import UUID

import pytest
from alembic import command
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from app.db import transaccion
from tests.conftest import ADMIN_URL, CLAVES, alembic_config
from tests.test_mandatos import facultades_de, otra_persona, sesiones_abiertas
from tests.test_modo_operacion import consorcio_con_persona, decidir

pytestmark = pytest.mark.db

DESDE = date(2026, 1, 1)
CUIT_X = "20123456786"
CUIT_Y = "30500010912"
CUIT_RESTO_10 = "20200000090"  # el resto da 10: inválido, nunca NULL

RESTRICCIONES = {
    "mandato_instrumento_referencia_largo",
    "mandato_baja_decision_mismo_consorcio",
    "mandato_terminado_con_causa",
    "mandato_baja_un_respaldo",
    "mandato_remocion_con_decision",
    "mandato_otras_causas_con_instrumento",
    "mandato_con_cuenta",
    "mandato_externo_con_cuit",
    "mandato_cuit_valido",
    "mandato_consejo_sin_superposicion",
    "mandato_administracion_unica",
}
FUNCIONES = {
    "nucleo.cuit_valido(text)": False,
    "nucleo.mandato_vigente(uuid,date)": False,
    "nucleo.consorcios_con_mandato_vigente()": True,
    "nucleo.alta_mandato(nucleo.tipo_mandato,uuid,text,text,text,uuid,date,date,text[],uuid,text)": True,  # noqa: E501
    "nucleo.baja_mandato(uuid,nucleo.causa_baja,uuid,text,text,uuid,text)": True,
}


# Ayudantes ----------------------------------------------------------------------------------


def alta(motor_app, cons, actor, decision, *, tipo="administrador", persona=None, nombre=None,
         cuit=None, desde=DESDE, hasta=None, facultades=None, origen="api") -> UUID:  # fmt: skip
    with transaccion(motor_app, persona_id=actor, consorcio_id=cons) as c:
        return c.execute(
            text(
                "select nucleo.alta_mandato(:t, :p, :n, :cuit, null, :d, :desde, :hasta, :f,"
                " :a, :o)"
            ),
            {
                "t": tipo,
                "p": persona,
                "n": nombre,
                "cuit": cuit,
                "d": decision,
                "desde": desde,
                "hasta": hasta,
                "f": facultades,
                "a": actor if origen == "api" else None,
                "o": origen,
            },  # fmt: skip
        ).scalar_one()


def externo(motor_app, cons, actor, decision, cuit=CUIT_X, **kw) -> UUID:
    return alta(motor_app, cons, actor, decision, tipo="administrador_externo",
                nombre="Estudio X", cuit=cuit, **kw)  # fmt: skip


def baja(motor_app, cons, actor, mandato, causa="renuncia", *, decision=None,
         instrumento="Nota de renuncia del 05-11", descripcion="Renuncia",
         origen="api") -> int:  # fmt: skip
    with transaccion(motor_app, persona_id=actor, consorcio_id=cons) as c:
        return c.execute(
            text("select nucleo.baja_mandato(:m, :causa, :d, :i, :desc, :a, :o)"),
            {
                "m": mandato,
                "causa": causa,
                "d": decision,
                "i": instrumento,
                "desc": descripcion,
                "a": actor,
                "o": origen,
            },  # fmt: skip
        ).scalar_one()


def fila(motor_app, cons, actor, mandato) -> dict:
    with transaccion(motor_app, persona_id=actor, consorcio_id=cons) as c:
        return dict(
            c.execute(
                text(
                    "select vigente_desde, vigente_hasta, causa_baja::text as causa,"
                    " nucleo.hoy_del_consorcio(consorcio_id) as hoy"
                    " from nucleo.mandato where id = :m"
                ),
                {"m": mandato},
            )
            .mappings()
            .one()
        )


def vigente(motor_app, cons, actor, mandato, fecha) -> bool:
    with transaccion(motor_app, persona_id=actor, consorcio_id=cons) as c:
        return c.execute(
            text("select nucleo.mandato_vigente(:m, :f)"), {"m": mandato, "f": fecha}
        ).scalar_one()


@pytest.fixture
def escena(motor_migrate, motor_app):
    """Un consorcio, quien registra (su propietaria), dos personas más y una decisión."""
    cons, _, per = consorcio_con_persona(motor_migrate)
    return {
        "cons": cons,
        "per": per,
        "p1": otra_persona(motor_migrate, cons),
        "p2": otra_persona(motor_migrate, cons),
        "acta": decidir(motor_app, cons, per),
    }


# Catálogo y permisos -------------------------------------------------------------------------


def test_catalogo_restricciones_con_nombre_y_sin_el_exclude_de_0011(motor_migrate):
    with motor_migrate.connect() as c:
        nombres = set(
            c.execute(
                text(
                    "select conname from pg_constraint where conrelid = 'nucleo.mandato'::regclass"
                )
            ).scalars()
        )
    assert RESTRICCIONES <= nombres
    assert "mandato_consorcio_id_tipo_persona_id_daterange_excl" not in nombres


def test_permisos_de_las_funciones_public_no_cem_app_si(motor_migrate):
    with motor_migrate.connect() as c:
        for firma, definer in FUNCIONES.items():
            publico, app, es_definer = c.execute(
                text(
                    "select (select count(*) from pg_proc p, aclexplode(p.proacl) a"
                    "         where p.oid = cast(:f as regprocedure) and a.grantee = 0),"
                    " has_function_privilege('cem_app', cast(:f as regprocedure), 'execute'),"
                    " (select prosecdef from pg_proc where oid = cast(:f as regprocedure))"
                ),
                {"f": firma},
            ).one()
            assert (publico, app, es_definer) == (0, True, definer), firma
        # Control: la baja de 0011 ya no existe.
        assert (
            c.execute(
                text("select to_regprocedure('nucleo.baja_mandato(uuid,text,uuid,text)')")
            ).scalar_one()
            is None
        )


# Paquetes (C2, A2) ---------------------------------------------------------------------------


def test_paquetes_nuevos_para_mc_y_ad_y_nada_para_ae(escena, motor_app):
    e = escena
    mc = alta(motor_app, e["cons"], e["per"], e["acta"], tipo="miembro_consejo", persona=e["p1"])
    ad = alta(motor_app, e["cons"], e["per"], e["acta"], persona=e["p2"])
    f_mc = facultades_de(motor_app, e["cons"], e["per"], mc)
    f_ad = facultades_de(motor_app, e["cons"], e["per"], ad)
    for f in ("mandatos.registrar", "decisiones.registrar"):
        assert f in f_mc and f in f_ad
    assert "fiscalizacion.ver_panel" in f_ad  # A2
    with pytest.raises(DBAPIError, match="facultad_fuera_de_paquete|fuera del paquete"):
        externo(motor_app, e["cons"], e["per"], e["acta"], facultades=["mandatos.registrar"],
                desde=DESDE + timedelta(days=4000))  # fmt: skip


# Identidad (A11, A12) ------------------------------------------------------------------------


def test_cuit_valido_digito_verificador_y_resto_10(motor_app, escena):
    with transaccion(motor_app, persona_id=escena["per"], consorcio_id=escena["cons"]) as c:
        valores = dict(
            c.execute(
                text("select v, nucleo.cuit_valido(v) from unnest(cast(:vs as text[])) v"),
                {
                    "vs": [
                        CUIT_X,
                        CUIT_Y,
                        "20123456780",
                        CUIT_RESTO_10,
                        "20200000099",
                        "2012345678",
                        "20-12345678-6",
                    ]
                },  # fmt: skip
            ).all()
        )
    assert valores == {CUIT_X: True, CUIT_Y: True, "20123456780": False, CUIT_RESTO_10: False,
                       "20200000099": False, "2012345678": False,
                       "20-12345678-6": False}  # fmt: skip


@pytest.mark.parametrize(
    ("cuit", "esperado"),
    [("20123456780", "cuit_invalido|no es válido"), (CUIT_RESTO_10, "no es válido"),
     ("20-12345678-6", "no es válido|mandatario_cuit_check"), (None, "necesita su CUIT")],
)  # fmt: skip
def test_ae_sin_cuit_o_con_cuit_invalido_rechazado(escena, motor_app, cuit, esperado):
    with pytest.raises(DBAPIError, match=esperado):
        externo(motor_app, escena["cons"], escena["per"], escena["acta"], cuit=cuit)


def test_ae_sin_cuenta_con_cuit_valido_pasa(escena, motor_app):
    m = externo(motor_app, escena["cons"], escena["per"], escena["acta"])
    assert facultades_de(motor_app, escena["cons"], escena["per"], m) == ["documentos.cargar"]


@pytest.mark.parametrize("tipo", ["miembro_consejo", "administrador"])
def test_a12_mc_y_ad_sin_cuenta_rechazados(escena, motor_app, tipo):
    with pytest.raises(DBAPIError, match="necesita la cuenta"):
        alta(motor_app, escena["cons"], escena["per"], escena["acta"], tipo=tipo,
             nombre="Sin cuenta")  # fmt: skip


def test_a12_tambien_lo_exige_la_tabla(escena, motor_migrate):
    with pytest.raises(DBAPIError, match="mandato_con_cuenta"):
        with motor_migrate.begin() as c:
            c.execute(
                text(
                    "insert into nucleo.mandato (consorcio_id, tipo, mandatario_nombre,"
                    " decision_id, vigente_desde, origen) values (:c, 'administrador', 'X', :d,"
                    " '2026-01-01', 'soporte')"
                ),
                {"c": escena["cons"], "d": escena["acta"]},
            )


# A4 ------------------------------------------------------------------------------------------


def test_a4_nadie_se_da_un_mandato_ni_se_da_de_baja_a_si_mismo(escena, motor_app):
    e = escena
    with pytest.raises(DBAPIError, match="a sí mismo"):
        alta(motor_app, e["cons"], e["p1"], e["acta"], tipo="miembro_consejo", persona=e["p1"])
    m = alta(motor_app, e["cons"], e["per"], e["acta"], tipo="miembro_consejo", persona=e["p1"])
    with pytest.raises(DBAPIError, match="a sí mismo"):
        baja(motor_app, e["cons"], e["p1"], m)
    assert baja(motor_app, e["cons"], e["per"], m) == 0  # control: otra persona sí


def test_baja_por_api_sin_actor_rechazada(escena, motor_app):
    e = escena
    m = alta(motor_app, e["cons"], e["per"], e["acta"], persona=e["p1"])
    with transaccion(motor_app, persona_id=e["per"], consorcio_id=e["cons"]) as c:
        with pytest.raises(DBAPIError, match="actor_requerido|quién la registra"):
            c.execute(
                text("select nucleo.baja_mandato(:m, 'renuncia', null, 'Nota', 'x', null, 'api')"),
                {"m": m},
            )


# A14: administrador único --------------------------------------------------------------------


def test_a14_casos_1_a_4_cualquier_segundo_administrador_superpuesto(escena, motor_app):
    e = escena
    ad = alta(motor_app, e["cons"], e["per"], e["acta"], persona=e["p1"], hasta=date(2027, 1, 1))
    for intento in (
        lambda: alta(motor_app, e["cons"], e["per"], e["acta"], persona=e["p2"],
                     desde=date(2026, 6, 1)),  # 1. AD + otro AD
        lambda: externo(motor_app, e["cons"], e["per"], e["acta"],
                        desde=date(2026, 6, 1)),  # 2. AD + AE
    ):  # fmt: skip
        with pytest.raises(DBAPIError, match="mandato de administración"):
            intento()
    # 3 y 4: con un AE vigente, ni un AD ni otro AE con otro CUIT.
    baja(motor_app, e["cons"], e["per"], ad, "error_carga", instrumento="Corrección")
    externo(motor_app, e["cons"], e["per"], e["acta"], hasta=date(2027, 1, 1))
    for intento in (
        lambda: alta(motor_app, e["cons"], e["per"], e["acta"], persona=e["p2"],
                     desde=date(2026, 6, 1)),
        lambda: externo(motor_app, e["cons"], e["per"], e["acta"], cuit=CUIT_Y,
                        desde=date(2026, 6, 1)),
        lambda: externo(motor_app, e["cons"], e["per"], e["acta"],
                        desde=date(2026, 6, 1)),  # mismo CUIT (caso de borde 16)
    ):  # fmt: skip
        with pytest.raises(DBAPIError, match="administracion_ocupada|mandato de administración"):
            intento()


def test_a14_casos_5_y_6_sin_superposicion_y_sucesion_por_renuncia(escena, motor_app):
    e = escena
    alta(motor_app, e["cons"], e["per"], e["acta"], persona=e["p1"], hasta=date(2026, 3, 1))
    # 5. Empieza el día en que termina el anterior.
    externo(motor_app, e["cons"], e["per"], e["acta"], desde=date(2026, 3, 1),
            hasta=date(2026, 6, 1))  # fmt: skip
    # 6. Renuncia del vigente (corta hoy) y sucesor desde hoy; control: superpuesto, no.
    ad = alta(motor_app, e["cons"], e["per"], e["acta"], persona=e["p1"], desde=date(2026, 6, 1))
    baja(motor_app, e["cons"], e["per"], ad)
    hoy = fila(motor_app, e["cons"], e["per"], ad)["hoy"]
    with pytest.raises(DBAPIError, match="mandato de administración"):
        alta(motor_app, e["cons"], e["per"], e["acta"], persona=e["p2"],
             desde=hoy - timedelta(days=1))  # fmt: skip
    alta(motor_app, e["cons"], e["per"], e["acta"], persona=e["p2"], desde=hoy)


def test_a14_caso_7_error_carga_libera_las_mismas_fechas(escena, motor_app):
    e = escena
    malo = alta(motor_app, e["cons"], e["per"], e["acta"], persona=e["p1"],
                hasta=date(2027, 1, 1))  # fmt: skip
    with pytest.raises(DBAPIError, match="mandato de administración"):  # control
        externo(motor_app, e["cons"], e["per"], e["acta"], hasta=date(2027, 1, 1))
    antes = fila(motor_app, e["cons"], e["per"], malo)
    baja(motor_app, e["cons"], e["per"], malo, "error_carga", instrumento="Corrección del alta")
    despues = fila(motor_app, e["cons"], e["per"], malo)
    assert (despues["vigente_desde"], despues["vigente_hasta"]) == (
        antes["vigente_desde"], antes["vigente_hasta"])  # fmt: skip
    assert despues["causa"] == "error_carga"
    externo(motor_app, e["cons"], e["per"], e["acta"], hasta=date(2027, 1, 1))
    with pytest.raises(DBAPIError, match="mandato de administración"):  # un tercero, no
        alta(motor_app, e["cons"], e["per"], e["acta"], persona=e["p2"], desde=date(2026, 3, 1))


def test_a14_caso_8_mc_simultaneos_y_misma_persona_mc_y_ad(escena, motor_app):
    e = escena
    alta(motor_app, e["cons"], e["per"], e["acta"], persona=e["p1"])
    alta(motor_app, e["cons"], e["per"], e["acta"], tipo="miembro_consejo", persona=e["p1"])
    alta(motor_app, e["cons"], e["per"], e["acta"], tipo="miembro_consejo", persona=e["p2"])
    with pytest.raises(DBAPIError, match="ya hay un mandato de miembro_consejo"):  # control
        alta(motor_app, e["cons"], e["per"], e["acta"], tipo="miembro_consejo", persona=e["p2"],
             desde=date(2026, 5, 1))  # fmt: skip


def test_a14_caso_9_mismo_ae_en_otro_consorcio_si(escena, motor_migrate, motor_app):
    e = escena
    externo(motor_app, e["cons"], e["per"], e["acta"])
    cons_b, _, per_b = consorcio_con_persona(motor_migrate)
    externo(motor_app, cons_b, per_b, decidir(motor_app, cons_b, per_b))


def test_a14_futuros_y_rango_vacio(escena, motor_app):
    e = escena
    alta(motor_app, e["cons"], e["per"], e["acta"], persona=e["p1"], desde=date(2030, 1, 1),
         hasta=date(2031, 1, 1))  # fmt: skip
    with pytest.raises(DBAPIError, match="mandato de administración"):
        alta(motor_app, e["cons"], e["per"], e["acta"], persona=e["p2"], desde=date(2030, 6, 1))
    # Un rango vacío no ocupa nada.
    alta(motor_app, e["cons"], e["per"], e["acta"], persona=e["p2"], desde=date(2030, 6, 1),
         hasta=date(2030, 6, 1))  # fmt: skip


def test_mutacion_sin_las_exclusiones_se_aceptarian_los_casos_1_y_7(escena, motor_migrate):
    e = escena

    def insertar(c, persona, causa=None):
        c.execute(
            text(
                "insert into nucleo.mandato (consorcio_id, tipo, persona_id, decision_id,"
                " vigente_desde, origen, finalizado_en, motivo_fin, causa_baja,"
                " instrumento_referencia) values (:c, 'administrador', :p, :d, '2026-01-01',"
                " 'soporte', :fin, :motivo, :causa, :inst)"
            ),
            {
                "c": e["cons"],
                "p": persona,
                "d": e["acta"],
                "fin": "2026-02-01" if causa else None,
                "motivo": causa,
                "causa": causa,
                "inst": "Corrección" if causa else None,
            },  # fmt: skip
        )

    # Sin mandato_administracion_unica, dos AD superpuestos entran: la prueba lo detectaría.
    with motor_migrate.connect() as c:
        t = c.begin()
        c.execute(text("alter table nucleo.mandato drop constraint mandato_administracion_unica"))
        insertar(c, e["p1"])
        insertar(c, e["p2"])
        t.rollback()
    # Sin el predicado de error_carga, el correcto en las mismas fechas se rechazaría.
    with motor_migrate.connect() as c:
        t = c.begin()
        # Sin otras filas de la sesión, que también chocarían; la transacción se deshace.
        c.execute(text("delete from nucleo.facultad_otorgada"))
        c.execute(text("delete from nucleo.mandato"))
        c.execute(
            text(
                "alter table nucleo.mandato drop constraint mandato_administracion_unica,"
                " add constraint mandato_administracion_unica exclude using gist"
                " (consorcio_id with =, daterange(vigente_desde, vigente_hasta) with &&)"
                " where (tipo in ('administrador', 'administrador_externo'))"
            )
        )
        insertar(c, e["p1"], "error_carga")
        with pytest.raises(DBAPIError, match="mandato_administracion_unica"):
            insertar(c, e["p2"])
        t.rollback()
    # Control: con la restricción real, el caso 1 se rechaza.
    with pytest.raises(DBAPIError, match="mandato_administracion_unica"):
        with motor_migrate.begin() as c:
            insertar(c, e["p1"])
            insertar(c, e["p2"])


def test_a14_concurrencia_la_segunda_carga_espera_y_se_rechaza(escena, motor_app):
    e = escena
    empezo, resultado = threading.Event(), {}

    def segunda():
        empezo.set()
        try:
            externo(motor_app, e["cons"], e["per"], e["acta"])
            resultado["r"] = "aceptada"
        except DBAPIError as error:
            resultado["r"] = str(error.orig)

    with transaccion(motor_app, persona_id=e["per"], consorcio_id=e["cons"]) as c:
        c.execute(
            text(
                "select nucleo.alta_mandato('administrador', :p, null, null, null, :d,"
                " '2026-01-01', null, null, :a, 'api')"
            ),
            {"p": e["p1"], "d": e["acta"], "a": e["per"]},
        )
        hilo = threading.Thread(target=segunda)
        hilo.start()
        empezo.wait(5)
        hilo.join(2)
        assert hilo.is_alive(), "la segunda carga tiene que esperar a la primera"
    hilo.join(30)
    assert "mandato de administración" in resultado["r"]


# C6, A9 y A10: bajas -------------------------------------------------------------------------


def test_baja_ordinaria_corta_hoy_revoca_y_no_admite_otra(escena, motor_migrate, motor_app):
    e = escena
    m = alta(motor_app, e["cons"], e["per"], e["acta"], tipo="miembro_consejo", persona=e["p1"])
    assert sesiones_abiertas(motor_migrate, e["p1"], 2) == 2
    assert baja(motor_app, e["cons"], e["per"], m) == 2
    f = fila(motor_app, e["cons"], e["per"], m)
    assert (f["vigente_hasta"], f["causa"]) == (f["hoy"], "renuncia")
    assert not vigente(motor_app, e["cons"], e["per"], m, f["hoy"])
    assert vigente(motor_app, e["cons"], e["per"], m, f["hoy"] - timedelta(days=1))  # control
    with pytest.raises(DBAPIError, match="ya tiene una baja"):
        baja(motor_app, e["cons"], e["per"], m, "error_carga", instrumento="Corrección")


def test_error_carga_conserva_fechas_y_no_vale_para_ninguna_fecha(escena, motor_migrate, motor_app):
    e = escena
    m = alta(motor_app, e["cons"], e["per"], e["acta"], tipo="miembro_consejo", persona=e["p1"],
             hasta=date(2026, 6, 1))  # fmt: skip
    assert vigente(motor_app, e["cons"], e["per"], m, date(2026, 2, 1))  # control previo
    # Ya vencido por fecha: error_carga se acepta; una renuncia no ("ya terminó").
    with pytest.raises(DBAPIError, match="ya terminó"):
        baja(motor_app, e["cons"], e["per"], m)
    assert sesiones_abiertas(motor_migrate, e["p1"], 1) == 1
    assert baja(motor_app, e["cons"], e["per"], m, "error_carga", instrumento="Corrección") == 1
    f = fila(motor_app, e["cons"], e["per"], m)
    assert (f["vigente_desde"], f["vigente_hasta"]) == (DESDE, date(2026, 6, 1))
    assert not vigente(motor_app, e["cons"], e["per"], m, date(2026, 2, 1))
    with transaccion(motor_app, persona_id=e["per"], consorcio_id=e["cons"]) as c:
        # Control: por fechas solas respondería que sí. Por eso está prohibido para autorizar.
        assert c.execute(
            text(
                "select daterange(vigente_desde, vigente_hasta) @> date '2026-02-01'"
                " from nucleo.mandato where id = :m"
            ),
            {"m": m},
        ).scalar_one()
        evento = c.execute(
            text(
                "select despues from auditoria.eventos where entidad_id = :m"
                " and accion = 'consorcio.mandato.baja'"
            ),
            {"m": m},
        ).scalar_one()
    assert evento["causa"] == "error_carga" and evento["vigente_hasta"] == "2026-06-01"
    with pytest.raises(DBAPIError, match="ya tiene una baja"):
        baja(motor_app, e["cons"], e["per"], m, "error_carga", instrumento="Otra vez")


@pytest.mark.parametrize(
    ("causa", "decision", "instrumento", "esperado"),
    [
        ("remocion", None, "Nota", "solo con una decisión"),
        ("remocion", "consejo", None, "decisión de asamblea"),
        ("remocion", "otro_consorcio", None, "decisión de asamblea"),
        ("renuncia", "asamblea", None, "solo con la referencia"),
        ("renuncia", "asamblea", "Nota", "solo con la referencia"),
        ("otro", None, None, "solo con la referencia"),
        ("fallecimiento_incapacidad", None, "   ", "solo con la referencia"),
    ],
)  # fmt: skip
def test_c6_respaldo_invalido_rechazado(escena, motor_migrate, motor_app, causa, decision,
                                        instrumento, esperado):  # fmt: skip
    e = escena
    m = alta(motor_app, e["cons"], e["per"], e["acta"], tipo="miembro_consejo", persona=e["p1"])
    ids = {"asamblea": e["acta"], "consejo": decidir(motor_app, e["cons"], e["per"], "consejo")}
    if decision == "otro_consorcio":
        cons_b, _, per_b = consorcio_con_persona(motor_migrate)
        ids[decision] = decidir(motor_app, cons_b, per_b)
    with pytest.raises(DBAPIError, match=esperado):
        baja(motor_app, e["cons"], e["per"], m, causa, decision=ids.get(decision),
             instrumento=instrumento)  # fmt: skip
    assert fila(motor_app, e["cons"], e["per"], m)["causa"] is None  # no tocó nada


def test_c6_remocion_con_decision_de_asamblea_pasa(escena, motor_app):
    e = escena
    m = alta(motor_app, e["cons"], e["per"], e["acta"], tipo="miembro_consejo", persona=e["p1"])
    baja(motor_app, e["cons"], e["per"], m, "remocion", decision=e["acta"], instrumento=None)
    assert fila(motor_app, e["cons"], e["per"], m)["causa"] == "remocion"


# C4 y aislamiento ----------------------------------------------------------------------------


def test_c4_consorcios_con_mandato_vigente_y_sin_propios_en_la_rls(escena, motor_migrate,
                                                                   motor_app):  # fmt: skip
    e = escena
    cons_b, _, per_b = consorcio_con_persona(motor_migrate)
    acta_b = decidir(motor_app, cons_b, per_b)
    cons_c, _, per_c = consorcio_con_persona(motor_migrate)
    m_a = alta(motor_app, e["cons"], e["per"], e["acta"], tipo="miembro_consejo", persona=e["p1"])
    m_b = alta(motor_app, cons_b, per_b, acta_b, tipo="miembro_consejo", persona=e["p1"])
    m_c = alta(motor_app, cons_c, per_c, decidir(motor_app, cons_c, per_c),
               tipo="miembro_consejo", persona=e["p1"])  # fmt: skip
    baja(motor_app, cons_c, per_c, m_c, "error_carga", instrumento="Corrección")
    alta(motor_app, cons_b, per_b, acta_b, tipo="miembro_consejo", persona=e["p2"])

    def mis(persona) -> set[UUID]:
        with transaccion(motor_app, persona_id=persona) as c:
            return set(c.execute(text("select nucleo.consorcios_con_mandato_vigente()")).scalars())

    assert mis(e["p1"]) == {e["cons"], cons_b}  # C, corregido por error_carga, no
    assert mis(e["p2"]) == {cons_b}
    # Dentro del ámbito de A, la misma persona no ve su mandato de B (no hay política propios).
    with transaccion(motor_app, persona_id=e["p1"], consorcio_id=e["cons"]) as c:
        vistos = set(
            c.execute(
                text("select id from nucleo.mandato where persona_id = :p"), {"p": e["p1"]}
            ).scalars()
        )
    assert vistos == {m_a} and m_b not in vistos


# Upgrade y downgrade en una base aparte -------------------------------------------------------

BASE_APARTE = "cem_prueba_0012"


def _url(rol: str) -> str:
    return (
        make_url(ADMIN_URL)
        .set(username=f"cem_{rol}", password=CLAVES[rol], database=BASE_APARTE)
        .render_as_string(hide_password=False)
    )


@pytest.fixture
def base_aparte(base_de_prueba):
    admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f"drop database if exists {BASE_APARTE} with (force)"))
        c.execute(text(f"create database {BASE_APARTE} owner cem_migrate"))
        c.execute(text(f"revoke all on database {BASE_APARTE} from public"))
        c.execute(text(f"grant connect on database {BASE_APARTE} to cem_app, cem_ro, cem_backup"))
    cfg = alembic_config()
    cfg.set_main_option("sqlalchemy.url", _url("migrate").replace("%", "%%"))
    motor = create_engine(_url("migrate"))
    yield cfg, motor
    motor.dispose()
    with admin.connect() as c:
        c.execute(text(f"drop database if exists {BASE_APARTE} with (force)"))
    admin.dispose()


def _catalogo(motor) -> dict:
    with motor.connect() as c:
        return {
            "restricciones": sorted(
                c.execute(
                    text(
                        "select conname || ' ' || pg_get_constraintdef(oid) from pg_constraint"
                        " where conrelid = 'nucleo.mandato'::regclass"
                    )
                ).scalars()
            ),
            "funciones": sorted(
                c.execute(
                    text(
                        "select p.oid::regprocedure::text || md5(p.prosrc) || coalesce("
                        " array_to_string(p.proacl, ','), '') from pg_proc p"
                        " join pg_namespace n on n.oid = p.pronamespace"
                        " where n.nspname = 'nucleo' and p.proname like '%mandato%'"
                        " or p.proname = 'cuit_valido'"
                    )
                ).scalars()
            ),
            "paquetes": c.execute(text("select count(*) from nucleo.paquete_facultad")).scalar(),
            "columnas": sorted(
                c.execute(
                    text(
                        "select column_name from information_schema.columns"
                        " where table_schema = 'nucleo' and table_name = 'mandato'"
                    )
                ).scalars()
            ),
        }


def _insertar_mandato(motor, **cols) -> None:
    with motor.begin() as c:
        cons = c.execute(
            text("insert into nucleo.consorcio (nombre) values ('Aparte') returning id")
        ).scalar_one()
        dec = c.execute(
            text(
                "insert into nucleo.decision_formal (consorcio_id, tipo, fecha, descripcion,"
                " referencia_acta, origen) values (:c, 'asamblea', '2026-01-01', 'x', 'Acta',"
                " 'soporte') returning id"
            ),
            {"c": cons},
        ).scalar_one()
        valores = {"tipo": "administrador_externo", "mandatario_nombre": "X"} | cols
        columnas = ", ".join(valores)
        marcas = ", ".join(f":{k}" for k in valores)
        c.execute(
            text(
                f"insert into nucleo.mandato (consorcio_id, decision_id, vigente_desde, origen,"
                f" {columnas}) values (:cons, :dec, '2026-01-01', 'soporte', {marcas})"
            ),
            {"cons": cons, "dec": dec} | valores,
        )


def test_upgrade_desde_0011_y_downgrade_vacio_vuelve_exacto(base_aparte):
    cfg, motor = base_aparte
    command.upgrade(cfg, "0011")
    en_0011 = _catalogo(motor)
    command.upgrade(cfg, "0012")
    en_0012 = _catalogo(motor)
    assert en_0012 != en_0011 and en_0012["paquetes"] == en_0011["paquetes"] + 5
    assert "causa_baja" in en_0012["columnas"]
    command.downgrade(cfg, "0011")
    assert _catalogo(motor) == en_0011


def test_downgrade_con_un_mandato_se_niega_y_no_toca_nada(base_aparte):
    cfg, motor = base_aparte
    command.upgrade(cfg, "0012")
    _insertar_mandato(motor, mandatario_cuit=CUIT_X)
    antes = _catalogo(motor)
    with pytest.raises(DBAPIError, match="downgrade de 0012 se niega"):
        command.downgrade(cfg, "0011")
    assert _catalogo(motor) == antes
    with motor.connect() as c:
        assert c.execute(text("select version_num from plataforma.alembic_version")).scalar() == (
            "0012"
        )
        assert c.execute(text("select count(*) from nucleo.mandato")).scalar() == 1


def test_upgrade_con_datos_que_violan_falla_y_no_transforma(base_aparte):
    cfg, motor = base_aparte
    command.upgrade(cfg, "0011")
    _insertar_mandato(motor)  # AE sin CUIT: válido en 0011, no en 0012
    with pytest.raises(DBAPIError, match="mandato_externo_con_cuit"):
        command.upgrade(cfg, "0012")
    with motor.connect() as c:
        assert c.execute(text("select version_num from plataforma.alembic_version")).scalar() == (
            "0011"
        )
        assert c.execute(text("select mandatario_cuit from nucleo.mandato")).scalar() is None


def test_el_actor_es_la_persona_de_la_sesion(escena, motor_app):
    """Con origen `api` el actor es la persona de la sesión; con `soporte`, no hay persona."""
    e = escena
    llamada = (
        "select nucleo.alta_mandato('miembro_consejo', :p, null, null, null, :d, '2026-01-01',"
        " null, null, :a, :o)"
    )
    # La sesión es de p1, pero dice actuar como per para darse un mandato a sí misma.
    with pytest.raises(DBAPIError, match="actor_no_coincide|no es la persona de la sesión"):
        with transaccion(motor_app, persona_id=e["p1"], consorcio_id=e["cons"]) as c:
            c.execute(text(llamada), {"p": e["p1"], "d": e["acta"], "a": e["per"], "o": "api"})
    # Con una persona en la sesión, no puede declararse soporte.
    with pytest.raises(DBAPIError, match="origen_invalido|origen soporte"):
        with transaccion(motor_app, persona_id=e["p1"], consorcio_id=e["cons"]) as c:
            c.execute(text(llamada), {"p": e["p1"], "d": e["acta"], "a": None, "o": "soporte"})
    # Controles: la sesión de per da el mandato a p1; soporte sin persona en la sesión también.
    m = alta(motor_app, e["cons"], e["per"], e["acta"], tipo="miembro_consejo", persona=e["p1"])
    with transaccion(motor_app, persona_id=None, consorcio_id=e["cons"]) as c:
        c.execute(text(llamada), {"p": e["p2"], "d": e["acta"], "a": None, "o": "soporte"})
    # La baja, igual: la sesión de p2 no puede registrarla en nombre de per.
    with pytest.raises(DBAPIError, match="actor_no_coincide|no es la persona de la sesión"):
        with transaccion(motor_app, persona_id=e["p2"], consorcio_id=e["cons"]) as c:
            c.execute(
                text("select nucleo.baja_mandato(:m, 'renuncia', null, 'Nota', 'x', :a, 'api')"),
                {"m": m, "a": e["per"]},
            )


@pytest.mark.parametrize("origen", ["trabajos", "planificador"])
def test_solo_se_admiten_los_origenes_api_y_soporte(escena, motor_app, origen):
    """La auditoría acepta más orígenes, pero alta y baja solo `api` y `soporte`."""
    e = escena
    m = alta(motor_app, e["cons"], e["per"], e["acta"], tipo="miembro_consejo", persona=e["p1"])
    with pytest.raises(DBAPIError, match="origen no admitido"):
        with transaccion(motor_app, persona_id=e["p1"], consorcio_id=e["cons"]) as c:
            c.execute(
                text("select nucleo.baja_mandato(:m, 'renuncia', null, 'Nota', 'x', :a, :o)"),
                {"m": m, "a": e["per"], "o": origen},
            )
    with pytest.raises(DBAPIError, match="origen no admitido"):
        with transaccion(motor_app, persona_id=e["p1"], consorcio_id=e["cons"]) as c:
            c.execute(
                text(
                    "select nucleo.alta_mandato('miembro_consejo', :p, null, null, null, :d,"
                    " '2027-01-01', null, null, :a, :o)"
                ),
                {"p": e["p1"], "d": e["acta"], "a": e["per"], "o": origen},
            )
    assert fila(motor_app, e["cons"], e["per"], m)["causa"] is None  # control: no se tocó
