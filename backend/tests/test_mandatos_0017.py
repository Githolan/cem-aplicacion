"""E15 (H2b 2b-4b, PR 3): un mandato que vence por fecha revoca las sesiones del mandatario.

Diseño: `doc/diseno/H2b_4b_Diseno.md` del repositorio CEM, §6. La función se prueba con sus
controles (lo que no tiene que tocar), el trabajo de punta a punta con el planificador y el
trabajador reales, y el downgrade en una base aparte.
"""

from datetime import UTC, date, datetime, timedelta

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db import transaccion
from app.plataforma import tareas
from app.plataforma.planificador import TAREAS, programar
from app.plataforma.trabajos import Trabajador
from tests.test_cola import cola_vacia  # noqa: F401  (fixture)
from tests.test_mandatos import otra_persona, sesiones_abiertas
from tests.test_mandatos_0012 import alta, baja, externo
from tests.test_modo_operacion import (
    consorcio_con_persona,
    decidir_equipo,
    zona_con_otra_fecha,
)
from tests.test_vinculo_0014 import base_aparte  # noqa: F401  (fixture)

pytestmark = pytest.mark.db

DESDE = date(2026, 1, 1)
TIPO = "consorcio.revocar_por_vencimiento"


def _vencido(motor_app, cons, persona, hasta=None):
    """Un mandato de Consejo por soporte que ya terminó por su fecha (A1: exclusivo)."""
    return alta(motor_app, cons, None, decidir_equipo(motor_app, cons), persona=persona,
                tipo="miembro_consejo", hasta=hasta or date.today() - timedelta(days=2),
                origen="soporte")  # fmt: skip


def _procesar(motor_app) -> int:
    with transaccion(motor_app, persona_id=None) as c:
        return c.execute(text("select nucleo.revocar_sesiones_por_vencimiento(1000)")).scalar_one()


def _marca(motor_migrate, mandato):
    with motor_migrate.connect() as c:
        return c.execute(
            text("select vencimiento_procesado_en from nucleo.mandato where id = :m"),
            {"m": mandato},
        ).scalar_one()


def _eventos(motor_migrate, mandato) -> list[dict]:
    with motor_migrate.connect() as c:
        return [
            dict(f._mapping)
            for f in c.execute(
                text(
                    "select origen, actor_persona_id, despues from auditoria.eventos"
                    " where entidad_id = :m and accion = 'consorcio.mandato.vencimiento'"
                ),
                {"m": mandato},
            )
        ]


@pytest.fixture
def escena(motor_migrate, motor_app):
    cons, _, per = consorcio_con_persona(motor_migrate)
    return {"cons": cons, "per": per, "mc": otra_persona(motor_migrate, cons)}


def test_el_vencimiento_revoca_las_sesiones_una_sola_vez(escena, motor_app, motor_migrate):
    e = escena
    m = _vencido(motor_app, e["cons"], e["mc"])
    assert sesiones_abiertas(motor_migrate, e["mc"], 2) == 2
    assert _procesar(motor_app) >= 1
    assert sesiones_abiertas(motor_migrate, e["mc"]) == 0
    assert _marca(motor_migrate, m) is not None
    [evento] = _eventos(motor_migrate, m)
    assert (evento["origen"], evento["actor_persona_id"]) == ("planificador", None)
    assert evento["despues"]["persona_id"] == str(e["mc"])
    assert evento["despues"]["sesiones_revocadas"] == 2
    # Idempotente: una sesión nueva de la persona sobrevive a la corrida siguiente.
    assert sesiones_abiertas(motor_migrate, e["mc"], 1) == 1
    _procesar(motor_app)
    assert sesiones_abiertas(motor_migrate, e["mc"]) == 1
    assert len(_eventos(motor_migrate, m)) == 1


def test_hoy_es_el_primer_dia_sin_mandato(escena, motor_app, motor_migrate):
    """A1: `vigente_hasta` es exclusivo; el que termina hoy ya venció."""
    e = escena
    with motor_migrate.connect() as c:
        hoy = c.execute(text("select nucleo.hoy_del_consorcio(:c)"), {"c": e["cons"]}).scalar_one()
    m = _vencido(motor_app, e["cons"], e["mc"], hasta=hoy)
    sesiones_abiertas(motor_migrate, e["mc"], 1)
    _procesar(motor_app)
    assert _marca(motor_migrate, m) is not None
    assert sesiones_abiertas(motor_migrate, e["mc"]) == 0


def test_lo_que_no_vencio_o_ya_tiene_baja_no_se_toca(escena, motor_app, motor_migrate):
    e = escena
    vigente = alta(motor_app, e["cons"], None, decidir_equipo(motor_app, e["cons"]),
                   persona=e["mc"], tipo="miembro_consejo",
                   hasta=date.today() + timedelta(days=30), origen="soporte")  # fmt: skip
    otra = otra_persona(motor_migrate, e["cons"])
    # La baja deja `vigente_hasta` en hoy: sin el filtro de `causa_baja`, el trabajo la
    # tomaría como un vencimiento más.
    con_baja = alta(motor_app, e["cons"], None, decidir_equipo(motor_app, e["cons"]),
                    persona=otra, tipo="miembro_consejo", origen="soporte")  # fmt: skip
    baja(motor_app, e["cons"], None, con_baja, "renuncia", instrumento="Nota",
         descripcion="Renuncia", origen="soporte")  # fmt: skip
    sin_cuenta = externo(motor_app, e["cons"], None, decidir_equipo(motor_app, e["cons"]),
                         hasta=date.today() - timedelta(days=2), origen="soporte")  # fmt: skip
    sesiones_abiertas(motor_migrate, e["mc"], 1)
    sesiones_abiertas(motor_migrate, otra, 1)
    _procesar(motor_app)
    assert [_marca(motor_migrate, m) for m in (vigente, con_baja, sin_cuenta)] == [None] * 3
    assert sesiones_abiertas(motor_migrate, e["mc"]) == 1
    assert sesiones_abiertas(motor_migrate, otra) == 1
    # Control: el mismo proceso sí toma uno vencido de esta escena (otra persona: el período de
    # la renuncia se superpondría).
    vencido = _vencido(motor_app, e["cons"], otra_persona(motor_migrate, e["cons"]))
    _procesar(motor_app)
    assert _marca(motor_migrate, vencido) is not None


def test_usa_la_fecha_del_consorcio_y_no_la_de_la_base(motor_migrate, motor_app):
    """Una zona cuyo día difiere del de la base (UTC) a esta hora. Adelantada: un mandato que
    termina en su hoy ya venció. Atrasada: uno que termina en el hoy de UTC todavía no."""
    zona, desfase = zona_con_otra_fecha(motor_app)
    cons, _, _ = consorcio_con_persona(motor_migrate, zona)
    persona = otra_persona(motor_migrate, cons)
    with motor_migrate.connect() as c:
        hoy, utc = c.execute(
            text("select nucleo.hoy_del_consorcio(:c), (now() at time zone 'UTC')::date"),
            {"c": cons},
        ).one()
    assert (hoy - utc).days == desfase
    m = _vencido(motor_app, cons, persona, hasta=max(hoy, utc))
    _procesar(motor_app)
    assert (_marca(motor_migrate, m) is not None) is (desfase > 0)


def test_un_mandato_bloqueado_se_saltea_y_se_toma_despues(escena, motor_app, motor_migrate):
    """`skip locked`: una baja en curso sobre el mandato no detiene la corrida."""
    e = escena
    m = _vencido(motor_app, e["cons"], e["mc"])
    with motor_migrate.connect() as otra:
        otra.execute(text("begin"))
        otra.execute(text("select 1 from nucleo.mandato where id = :m for update"), {"m": m})
        with transaccion(motor_app, persona_id=None) as c:
            c.execute(text("set local lock_timeout = '300ms'"))
            c.execute(text("select nucleo.revocar_sesiones_por_vencimiento(1000)"))
        assert _marca(motor_migrate, m) is None
        otra.execute(text("rollback"))
    _procesar(motor_app)
    assert _marca(motor_migrate, m) is not None


def test_un_consorcio_dado_de_baja_no_se_procesa(escena, motor_app, motor_migrate):
    e = escena
    m = _vencido(motor_app, e["cons"], e["mc"])
    with motor_migrate.begin() as c:
        c.execute(text("update nucleo.consorcio set eliminado_en = now() where id = :c"),
                  {"c": e["cons"]})  # fmt: skip
    _procesar(motor_app)
    assert _marca(motor_migrate, m) is None


def test_solo_cem_app_ejecuta_la_funcion(motor_migrate):
    with motor_migrate.connect() as c:
        permisos = c.execute(
            text(
                "select has_function_privilege('cem_app', :f, 'execute'),"
                " has_function_privilege('cem_ro', :f, 'execute'),"
                " has_function_privilege('public', :f, 'execute'), p.prosecdef,"
                " array_to_string(p.proconfig, ',')"
                " from pg_proc p where p.oid = to_regprocedure(:f)"
            ),
            {"f": "nucleo.revocar_sesiones_por_vencimiento(integer)"},
        ).one()
    assert tuple(permisos) == (True, False, False, True, "search_path=pg_catalog, pg_temp")


def test_el_trabajo_horario_corre_de_punta_a_punta(escena, motor_app, motor_migrate,
                                                   cola_vacia):  # noqa: F811  # fmt: skip
    e = escena
    [tarea] = [t for t in TAREAS if t.tipo == TIPO]
    assert (tarea.periodo, tarea.desfase) == (timedelta(hours=1), timedelta(minutes=5))
    assert tareas.revocar_por_vencimiento is not None
    m = _vencido(motor_app, e["cons"], e["mc"])
    sesiones_abiertas(motor_migrate, e["mc"], 1)
    assert programar(motor_app, datetime.now(UTC), [tarea]) == 1
    trabajador = Trabajador(motor_app)
    while trabajador.procesar_lote():
        pass
    with motor_migrate.connect() as c:
        estado, resultado = c.execute(
            text("select estado::text, resultado from plataforma.jobs where tipo = :t"),
            {"t": TIPO},
        ).one()
    assert estado == "hecho" and resultado["mandatos_procesados"] >= 1
    assert _marca(motor_migrate, m) is not None
    assert sesiones_abiertas(motor_migrate, e["mc"]) == 0


# Downgrade ----------------------------------------------------------------------------------


def test_el_downgrade_de_0017_se_niega_con_vencimientos_procesados(base_aparte):  # noqa: F811
    cfg, motor = base_aparte
    command.upgrade(cfg, "0017")
    command.downgrade(cfg, "0016")
    command.upgrade(cfg, "0017")  # ida y vuelta sin datos
    with motor.begin() as c:
        cons = c.execute(text("insert into nucleo.consorcio (nombre) values ('A') returning id"))
        cons = cons.scalar_one()
        dec = c.execute(
            text("insert into nucleo.decision_formal (consorcio_id, tipo, fecha, descripcion,"
                 " referencia_acta, origen) values (:c, 'asamblea', '2026-01-01', 'x', 'Acta',"
                 " 'soporte') returning id"), {"c": cons},
        ).scalar_one()  # fmt: skip
        per = c.execute(
            text("insert into nucleo.persona (correo, nombre) values ('v@e.com', 'V')"
                 " returning id")).scalar_one()  # fmt: skip
        c.execute(
            text("insert into nucleo.mandato (consorcio_id, tipo, persona_id, decision_id,"
                 " vigente_desde, vigente_hasta, origen, vencimiento_procesado_en) values"
                 " (:c, 'miembro_consejo', :p, :d, '2026-01-01', '2026-02-01', 'soporte',"
                 " now())"), {"c": cons, "p": per, "d": dec},
        )  # fmt: skip
    with pytest.raises(DBAPIError) as error:
        command.downgrade(cfg, "0016")
    assert error.value.orig.diag.message_hint == "downgrade_con_datos"
    with motor.connect() as c:
        assert c.execute(
            text("select count(*) from information_schema.columns where table_schema = 'nucleo'"
                 " and table_name = 'mandato' and column_name = 'vencimiento_procesado_en'")
        ).scalar_one() == 1  # fmt: skip


def test_el_lote_respeta_su_tope(escena, motor_app, motor_migrate):
    """Codex: el trabajo no puede pasarse del plazo del trabajador; lo que queda, sigue."""
    e = escena
    otra = otra_persona(motor_migrate, e["cons"])
    a, b = _vencido(motor_app, e["cons"], e["mc"]), _vencido(motor_app, e["cons"], otra)
    _procesar(motor_app)  # vacía lo que dejaron otras pruebas
    with motor_migrate.begin() as c:
        c.execute(text("update nucleo.mandato set vencimiento_procesado_en = null"
                       " where id in (:a, :b)"), {"a": a, "b": b})  # fmt: skip
    with transaccion(motor_app, persona_id=None) as c:
        assert (
            c.execute(text("select nucleo.revocar_sesiones_por_vencimiento(1)")).scalar_one() == 1
        )
    assert [_marca(motor_migrate, m) is None for m in (a, b)].count(True) == 1
    _procesar(motor_app)
    assert [_marca(motor_migrate, m) for m in (a, b)].count(None) == 0
    with pytest.raises(DBAPIError) as error:
        with transaccion(motor_app, persona_id=None) as c:
            c.execute(text("select nucleo.revocar_sesiones_por_vencimiento(0)"))
    assert error.value.orig.diag.message_hint == "tope_invalido"
