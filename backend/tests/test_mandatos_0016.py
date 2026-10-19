"""Migración 0016 (H2b 2b-4b, PR 1): A15, E26 y R10 contra PostgreSQL real.

Diseño: `doc/diseno/H2b_4b_Diseno.md` del repositorio CEM, §2 a §4. Cada barrera se prueba con
su control (el caso que pasa); la de A15, además, con la decisión que carga el equipo, que no la
activa. El downgrade y el upgrade se prueban en una base aparte.
"""

from datetime import date, timedelta

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db import transaccion
from tests.test_mandatos import PAQUETE_CONSEJO, otra_persona, sesiones_abiertas
from tests.test_mandatos_0012 import CUIT_X, alta, baja, externo
from tests.test_modo_operacion import consorcio_con_persona, decidir, decidir_equipo
from tests.test_vinculo_0014 import base_aparte  # noqa: F401  (fixture)

pytestmark = pytest.mark.db

DESDE = date(2026, 1, 1)


def _motivo(error: pytest.ExceptionInfo) -> str | None:
    return error.value.orig.diag.message_hint


def _mandatos(motor_migrate, cons) -> int:
    with motor_migrate.connect() as c:
        return c.execute(
            text("select count(*) from nucleo.mandato where consorcio_id = :c"), {"c": cons}
        ).scalar_one()


def _eventos(motor_migrate, cons, accion) -> int:
    with motor_migrate.connect() as c:
        return c.execute(
            text("select count(*) from auditoria.eventos where consorcio_id = :c and accion = :a"),
            {"c": cons, "a": accion},
        ).scalar_one()


@pytest.fixture
def escena(motor_migrate, motor_app):
    """Un consorcio; `per` registra decisiones; `otra` es la segunda persona habilitada."""
    cons, _, per = consorcio_con_persona(motor_migrate)
    return {
        "cons": cons,
        "per": per,
        "otra": otra_persona(motor_migrate, cons),
        "mc": otra_persona(motor_migrate, cons),
    }


# A15: alta ----------------------------------------------------------------------------------


def test_a15_quien_registro_la_decision_no_da_de_alta_con_ella(escena, motor_app, motor_migrate):
    e = escena
    acta = decidir(motor_app, e["cons"], e["per"])
    with pytest.raises(DBAPIError) as error:
        alta(motor_app, e["cons"], e["per"], acta, persona=e["mc"], tipo="miembro_consejo")
    assert _motivo(error) == "decision_registrada_por_el_actor"
    assert _mandatos(motor_migrate, e["cons"]) == 0
    assert _eventos(motor_migrate, e["cons"], "consorcio.mandato.alta") == 0
    # Control: otra persona usa esa misma decisión.
    alta(motor_app, e["cons"], e["otra"], acta, persona=e["mc"], tipo="miembro_consejo")
    assert _mandatos(motor_migrate, e["cons"]) == 1


def test_a15_la_decision_del_equipo_no_la_activa(escena, motor_app, motor_migrate):
    e = escena
    alta(motor_app, e["cons"], e["per"], decidir_equipo(motor_app, e["cons"]), persona=e["mc"],
         tipo="miembro_consejo")  # fmt: skip
    assert _mandatos(motor_migrate, e["cons"]) == 1


def test_a15_no_frena_al_equipo_por_script(escena, motor_app, motor_migrate):
    """Con origen `soporte` no hay actor: la regla es para la API."""
    e = escena
    acta = decidir(motor_app, e["cons"], e["per"])
    alta(motor_app, e["cons"], None, acta, persona=e["mc"], tipo="miembro_consejo",
         origen="soporte")  # fmt: skip
    assert _mandatos(motor_migrate, e["cons"]) == 1


# A15: remoción ------------------------------------------------------------------------------


def test_a15_quien_registro_la_asamblea_no_remueve_con_ella(escena, motor_app, motor_migrate):
    e = escena
    m = alta(motor_app, e["cons"], e["per"], decidir_equipo(motor_app, e["cons"]),
             persona=e["mc"], tipo="miembro_consejo")  # fmt: skip
    asamblea = decidir(motor_app, e["cons"], e["per"])
    sesiones_abiertas(motor_migrate, e["mc"], 1)
    with pytest.raises(DBAPIError) as error:
        baja(motor_app, e["cons"], e["per"], m, "remocion", decision=asamblea, instrumento=None,
             descripcion="Remoción por asamblea")  # fmt: skip
    assert _motivo(error) == "decision_registrada_por_el_actor"
    assert sesiones_abiertas(motor_migrate, e["mc"]) == 1
    assert _eventos(motor_migrate, e["cons"], "consorcio.mandato.baja") == 0
    # Control: otra persona remueve con esa misma asamblea y las sesiones se revocan (RN22).
    assert baja(motor_app, e["cons"], e["otra"], m, "remocion", decision=asamblea,
                instrumento=None, descripcion="Remoción por asamblea") == 1  # fmt: skip
    assert sesiones_abiertas(motor_migrate, e["mc"]) == 0


def test_a15_no_toca_las_bajas_por_instrumento(escena, motor_app):
    """La renuncia no usa decisión: quien registró decisiones la puede cargar."""
    e = escena
    decidir(motor_app, e["cons"], e["per"])
    m = alta(motor_app, e["cons"], e["otra"], decidir_equipo(motor_app, e["cons"]),
             persona=e["mc"], tipo="miembro_consejo")  # fmt: skip
    baja(motor_app, e["cons"], e["per"], m, "renuncia")


# E26 ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("cambios", "motivo"),
    [
        ({"tipo": "administrador_externo", "nombre": None, "cuit": CUIT_X}, "nombre_invalido"),
        ({"tipo": "administrador_externo", "nombre": "   ", "cuit": CUIT_X}, "nombre_invalido"),
        ({"tipo": "administrador_externo", "nombre": "x" * 201, "cuit": CUIT_X},
         "nombre_invalido"),
        ({"hasta": DESDE - timedelta(days=1)}, "vigencia_invalida"),
        ({"desde": None}, "vigencia_invalida"),
        ({"facultades": [PAQUETE_CONSEJO[0], None]}, "facultad_fuera_de_paquete"),
    ],
)  # fmt: skip
def test_e26_el_alta_traduce_los_errores(escena, motor_app, motor_migrate, cambios, motivo):
    e = escena
    datos = {"persona": e["mc"], "tipo": "miembro_consejo"} | cambios
    if datos["tipo"] == "administrador_externo":
        datos["persona"] = None
    with pytest.raises(DBAPIError) as error:
        alta(motor_app, e["cons"], e["per"], decidir_equipo(motor_app, e["cons"]), **datos)
    assert _motivo(error) == motivo
    assert _mandatos(motor_migrate, e["cons"]) == 0


def test_e26_la_vigencia_vacia_sigue_admitida(escena, motor_app):
    """`hasta = desde` es una vigencia vacía (A1, exclusiva): la tabla la acepta."""
    e = escena
    alta(motor_app, e["cons"], e["per"], decidir_equipo(motor_app, e["cons"]), persona=e["mc"],
         tipo="miembro_consejo", hasta=DESDE)  # fmt: skip


@pytest.mark.parametrize("matricula", ["   ", "m" * 101])
def test_e26_la_matricula_se_traduce(escena, motor_app, matricula):
    e = escena
    with pytest.raises(DBAPIError) as error:
        with transaccion(motor_app, persona_id=e["per"], consorcio_id=e["cons"]) as c:
            c.execute(
                text(
                    "select nucleo.alta_mandato('administrador', :p, null, null, :mat, :d,"
                    " :desde, null, null, :a, 'api')"
                ),
                {"p": e["mc"], "mat": matricula, "d": decidir_equipo(motor_app, e["cons"]),
                 "desde": DESDE, "a": e["per"]},
            )  # fmt: skip
    assert _motivo(error) == "matricula_invalida"


@pytest.mark.parametrize(
    ("descripcion", "instrumento", "motivo"),
    [(None, "Nota", "descripcion_invalida"), ("  ", "Nota", "descripcion_invalida"),
     ("d" * 501, "Nota", "descripcion_invalida"), ("Renuncia", "i" * 301, "respaldo_invalido")],
)  # fmt: skip
def test_e26_la_baja_traduce_los_errores(escena, motor_app, descripcion, instrumento, motivo):
    e = escena
    m = alta(motor_app, e["cons"], e["per"], decidir_equipo(motor_app, e["cons"]),
             persona=e["mc"], tipo="miembro_consejo")  # fmt: skip
    with pytest.raises(DBAPIError) as error:
        baja(motor_app, e["cons"], e["per"], m, "renuncia", instrumento=instrumento,
             descripcion=descripcion)  # fmt: skip
    assert _motivo(error) == motivo


@pytest.mark.parametrize(
    ("tipo", "fecha", "descripcion", "referencia", "sha", "motivo"),
    [
        (None, DESDE, "x", "Acta 1", None, "decision_incompleta"),
        ("asamblea", None, "x", "Acta 1", None, "decision_incompleta"),
        ("asamblea", DESDE, None, "Acta 1", None, "descripcion_invalida"),
        ("asamblea", DESDE, "d" * 501, "Acta 1", None, "descripcion_invalida"),
        ("asamblea", DESDE, "x", None, None, "referencia_invalida"),
        ("asamblea", DESDE, "x", "r" * 301, None, "referencia_invalida"),
        ("asamblea", DESDE, "x", "Acta 1", "AB" * 31, "sha256_invalido"),
    ],
)
def test_e26_la_decision_traduce_los_errores(escena, motor_app, motor_migrate, tipo, fecha,
                                             descripcion, referencia, sha, motivo):  # fmt: skip
    e = escena
    with pytest.raises(DBAPIError) as error:
        with transaccion(motor_app, persona_id=e["per"], consorcio_id=e["cons"]) as c:
            c.execute(
                text("select nucleo.registrar_decision(:t, :f, :d, :r, :a, 'api', :s)"),
                {"t": tipo, "f": fecha, "d": descripcion, "r": referencia, "a": e["per"],
                 "s": sha},
            )  # fmt: skip
    assert _motivo(error) == motivo
    with motor_migrate.connect() as c:
        assert c.execute(
            text("select count(*) from nucleo.decision_formal where consorcio_id = :c"),
            {"c": e["cons"]},
        ).scalar_one() == 0  # fmt: skip


# R10 y P1-d ---------------------------------------------------------------------------------


def _ae_asociado(motor_app, e, **kw):
    return externo(motor_app, e["cons"], e["per"], decidir_equipo(motor_app, e["cons"]),
                   persona=e["mc"], **kw)  # fmt: skip


def test_r10_un_ae_asociado_no_entra_al_consejo(escena, motor_app, motor_migrate):
    e = escena
    _ae_asociado(motor_app, e)
    with pytest.raises(DBAPIError) as error:
        alta(motor_app, e["cons"], e["per"], decidir_equipo(motor_app, e["cons"]),
             persona=e["mc"], tipo="miembro_consejo")  # fmt: skip
    assert _motivo(error) == "consejo_incompatible_con_administracion"
    # Control: otra persona sí entra al Consejo.
    alta(motor_app, e["cons"], e["per"], decidir_equipo(motor_app, e["cons"]), persona=e["otra"],
         tipo="miembro_consejo")  # fmt: skip
    assert _mandatos(motor_migrate, e["cons"]) == 2


def test_r10_sin_superposicion_entra(escena, motor_app):
    """El AE sigue vigente hoy; el Consejo empieza el día en que el AE termina (A1)."""
    e = escena
    corte = date.today() + timedelta(days=60)
    _ae_asociado(motor_app, e, hasta=corte)
    alta(motor_app, e["cons"], e["per"], decidir_equipo(motor_app, e["cons"]), persona=e["mc"],
         tipo="miembro_consejo", desde=corte)  # fmt: skip


def test_r10_un_ae_dado_de_baja_por_error_de_carga_no_cuenta(escena, motor_app):
    e = escena
    ae = _ae_asociado(motor_app, e)
    baja(motor_app, e["cons"], e["per"], ae, "error_carga", instrumento="Nota", descripcion="Error")
    alta(motor_app, e["cons"], e["per"], decidir_equipo(motor_app, e["cons"]), persona=e["mc"],
         tipo="miembro_consejo")  # fmt: skip


def test_r10_el_alta_toma_el_bloqueo_de_la_aceptacion(escena, motor_app, motor_migrate):
    """Otra transacción con el bloqueo `persona:<correo>` de A7 detiene el alta de Consejo."""
    e = escena
    with motor_migrate.connect() as c:
        correo = c.execute(
            text("select correo from nucleo.persona where id = :p"), {"p": e["mc"]}
        ).scalar_one()
    with motor_migrate.connect() as dueño:
        dueño.execute(text("begin"))
        dueño.execute(
            text("select pg_advisory_xact_lock(hashtextextended('persona:' || :c, 0))"),
            {"c": correo},
        )
        with pytest.raises(DBAPIError) as error:
            with transaccion(motor_app, persona_id=e["per"], consorcio_id=e["cons"]) as c:
                c.execute(text("set local lock_timeout = '300ms'"))
                c.execute(
                    text(
                        "select nucleo.alta_mandato('miembro_consejo', :p, null, null, null, :d,"
                        " :desde, null, null, :a, 'api')"
                    ),
                    {"p": e["mc"], "d": decidir_equipo(motor_app, e["cons"]), "desde": DESDE,
                     "a": e["per"]},
                )  # fmt: skip
        assert error.value.orig.sqlstate == "55P03"  # lock_not_available
        dueño.execute(text("rollback"))
    # Control: sin el bloqueo ajeno, la misma alta pasa.
    alta(motor_app, e["cons"], e["per"], decidir_equipo(motor_app, e["cons"]), persona=e["mc"],
         tipo="miembro_consejo")  # fmt: skip


def test_r10_un_miembro_del_consejo_no_entra_como_externo(escena, motor_app):
    """P1-d al revés: el alta de un AE con cuenta mira el Consejo de la persona."""
    e = escena
    alta(motor_app, e["cons"], e["per"], decidir_equipo(motor_app, e["cons"]), persona=e["mc"],
         tipo="miembro_consejo")  # fmt: skip
    with pytest.raises(DBAPIError) as error:
        _ae_asociado(motor_app, e)
    assert _motivo(error) == "consejo_incompatible_con_administracion"
    # Control: el mismo AE sin cuenta entra (la cuenta se asocia después, por A7).
    externo(motor_app, e["cons"], e["per"], decidir_equipo(motor_app, e["cons"]))


def test_r10_un_ae_futuro_superpuesto_cuenta(escena, motor_app):
    e = escena
    _ae_asociado(motor_app, e, desde=date.today() + timedelta(days=30))
    with pytest.raises(DBAPIError) as error:
        alta(motor_app, e["cons"], e["per"], decidir_equipo(motor_app, e["cons"]),
             persona=e["mc"], tipo="miembro_consejo", desde=date.today())  # fmt: skip
    assert _motivo(error) == "consejo_incompatible_con_administracion"


def test_r10_un_ae_que_ya_termino_no_cuenta(escena, motor_app):
    """Como en A7: el otro mandato tiene que estar vigente o ser futuro."""
    e = escena
    _ae_asociado(motor_app, e, hasta=date.today() - timedelta(days=1))
    alta(motor_app, e["cons"], e["per"], decidir_equipo(motor_app, e["cons"]), persona=e["mc"],
         tipo="miembro_consejo")  # fmt: skip


def test_r10_un_consejo_historico_no_lo_activa(escena, motor_app):
    """Un alta que ya terminó (carga histórica) no choca con el AE de hoy, como en A7."""
    e = escena
    _ae_asociado(motor_app, e)
    alta(motor_app, e["cons"], e["per"], decidir_equipo(motor_app, e["cons"]), persona=e["mc"],
         tipo="miembro_consejo", hasta=date.today() - timedelta(days=1))  # fmt: skip


def test_r10_el_administrador_con_cuenta_no_entra_en_p1d(escena, motor_app):
    """P1-d habla del AE: un `administrador` y el Consejo de la misma persona no chocan acá."""
    e = escena
    alta(motor_app, e["cons"], e["per"], decidir_equipo(motor_app, e["cons"]), persona=e["mc"],
         tipo="administrador")  # fmt: skip
    alta(motor_app, e["cons"], e["per"], decidir_equipo(motor_app, e["cons"]), persona=e["mc"],
         tipo="miembro_consejo")  # fmt: skip


# Actor, origen y precedencia ----------------------------------------------------------------


@pytest.mark.parametrize("tipo", ["miembro_consejo", "administrador"])
def test_sin_cuenta_ni_nombre_sigue_saliendo_mandato_sin_cuenta(escena, motor_app, tipo):
    """E26 no le gana a A12: el defecto real es la falta de cuenta."""
    e = escena
    with pytest.raises(DBAPIError) as error:
        alta(motor_app, e["cons"], e["per"], decidir_equipo(motor_app, e["cons"]), tipo=tipo)
    assert _motivo(error) == "mandato_sin_cuenta"


def _alta_con(motor_app, e, sesion, actor, origen):
    with transaccion(motor_app, persona_id=sesion, consorcio_id=e["cons"]) as c:
        return c.execute(
            text(
                "select nucleo.alta_mandato('miembro_consejo', :p, null, null, null, :d, :desde,"
                " null, null, :a, :o)"
            ),
            {"p": e["mc"], "d": decidir_equipo(motor_app, e["cons"]), "desde": DESDE, "a": actor,
             "o": origen},
        ).scalar_one()  # fmt: skip


def test_el_alta_por_la_api_necesita_actor(escena, motor_app):
    e = escena
    with pytest.raises(DBAPIError) as error:
        _alta_con(motor_app, e, None, None, "api")
    assert _motivo(error) == "actor_requerido"


def test_el_alta_por_soporte_no_lleva_actor(escena, motor_app, motor_migrate):
    e = escena
    with pytest.raises(DBAPIError) as error:
        _alta_con(motor_app, e, None, e["per"], "soporte")
    assert _motivo(error) == "origen_invalido"
    # Control: por soporte, sin actor, entra.
    _alta_con(motor_app, e, None, None, "soporte")
    assert _mandatos(motor_migrate, e["cons"]) == 1


# Downgrade ----------------------------------------------------------------------------------


def _funciones(motor) -> dict[str, str]:
    with motor.connect() as c:
        return dict(
            c.execute(
                text(
                    "select p.proname, md5(pg_get_functiondef(p.oid)) || coalesce("
                    " array_to_string(p.proacl, ','), '') from pg_proc p"
                    " join pg_namespace n on n.oid = p.pronamespace where n.nspname = 'nucleo'"
                    " and p.proname in ('alta_mandato', 'baja_mandato', 'registrar_decision')"
                )
            ).all()
        )


def test_el_downgrade_de_0016_restaura_las_funciones_de_0015(base_aparte):  # noqa: F811
    cfg, motor = base_aparte
    command.upgrade(cfg, "0015")
    en_0015 = _funciones(motor)
    command.upgrade(cfg, "0016")
    en_0016 = _funciones(motor)
    assert en_0016.keys() == en_0015.keys() == {"alta_mandato", "baja_mandato",
                                                 "registrar_decision"}  # fmt: skip
    assert all(en_0016[f] != en_0015[f] for f in en_0015)  # las tres cambian
    command.downgrade(cfg, "0015")
    assert _funciones(motor) == en_0015
    command.upgrade(cfg, "0016")
    assert _funciones(motor) == en_0016


def test_el_upgrade_se_niega_con_consejo_y_externo_superpuestos(base_aparte):  # noqa: F811
    """R10 (Codex): lo que se cargó antes por script no queda vivo en silencio."""
    cfg, motor = base_aparte
    command.upgrade(cfg, "0015")
    with motor.begin() as c:
        cons = c.execute(text("insert into nucleo.consorcio (nombre) values ('A') returning id"))
        cons = cons.scalar_one()
        dec = c.execute(
            text("insert into nucleo.decision_formal (consorcio_id, tipo, fecha, descripcion,"
                 " referencia_acta, origen) values (:c, 'asamblea', '2026-01-01', 'x', 'Acta',"
                 " 'soporte') returning id"), {"c": cons},
        ).scalar_one()  # fmt: skip
        per = c.execute(
            text("insert into nucleo.persona (correo, nombre) values ('r10@e.com', 'R')"
                 " returning id")).scalar_one()  # fmt: skip
        consejo = c.execute(
            text("insert into nucleo.mandato (consorcio_id, tipo, persona_id, decision_id,"
                 " vigente_desde, origen) values (:c, 'miembro_consejo', :p, :d, '2026-01-01',"
                 " 'soporte') returning id"), {"c": cons, "p": per, "d": dec},
        ).scalar_one()  # fmt: skip
        c.execute(
            text("insert into nucleo.mandato (consorcio_id, tipo, persona_id, mandatario_nombre,"
                 " mandatario_cuit, decision_id, vigente_desde, origen) values (:c,"
                 " 'administrador_externo', :p, 'Estudio', :cuit, :d, '2026-01-01', 'soporte')"),
            {"c": cons, "p": per, "d": dec, "cuit": CUIT_X},
        )  # fmt: skip
    with pytest.raises(DBAPIError) as error:
        command.upgrade(cfg, "0016")
    assert error.value.orig.diag.message_hint == "mandatos_incompatibles_previos"
    # Control: con el Consejo terminado ayer, el mismo upgrade pasa.
    with motor.begin() as c:
        c.execute(text("update nucleo.mandato set vigente_hasta = current_date - 1,"
                       " vigente_desde = '2025-01-01' where id = :m"), {"m": consejo})  # fmt: skip
    command.upgrade(cfg, "0016")
