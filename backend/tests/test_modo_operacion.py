"""Modo de operación y decisión formal (migración 0010, corte 2b-1) contra PostgreSQL real.

PA31: sin una decisión de asamblea registrada del mismo consorcio el modo no cambia y el
anterior sigue vigente. También: `cem_app` no escribe las tablas directo, la vigencia de
`require_scope` usa la fecha del consorcio y la migración baja y sube.
"""

from datetime import date, timedelta
from uuid import UUID

import pytest
from alembic import command
from cem_contracts.estados import ModoOperacion
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, ProgrammingError

from app.config import Config
from app.contexto import Identidad, obtener_identidad, require_scope
from app.db import transaccion
from app.main import crear_app
from tests.conftest import alembic_config, url_rol, vaciar_mandatos

pytestmark = pytest.mark.db

DESDE = date(2026, 1, 1)


def consorcio_con_persona(motor_migrate, zona: str = "America/Argentina/Buenos_Aires"):
    """Un consorcio propio de la prueba, con una unidad y su propietaria desde 2020."""
    with motor_migrate.begin() as c:
        cons = c.execute(
            text(
                "insert into nucleo.consorcio (nombre, zona_horaria)"
                " values ('Prueba', :z) returning id"
            ),
            {"z": zona},
        ).scalar_one()
        uni = c.execute(
            text(
                "insert into nucleo.unidad (consorcio_id, designacion)"
                " values (:c, '1° A') returning id"
            ),
            {"c": cons},
        ).scalar_one()
        per = c.execute(
            text("insert into nucleo.persona (correo, nombre) values (:c, 'Prueba') returning id"),
            {"c": f"modo-{cons}@ejemplo.com"},
        ).scalar_one()
        c.execute(
            text(
                "insert into nucleo.vinculo"
                " (consorcio_id, unidad_id, persona_id, tipo, vigente_desde, fuente, origen)"
                " values (:c, :u, :p, 'propietario', '2020-01-01', 'documento', 'soporte')"
            ),
            {"c": cons, "u": uni, "p": per},
        )
    return cons, uni, per


def decidir(motor_app, cons: UUID, per: UUID, tipo: str = "asamblea") -> UUID:
    with transaccion(motor_app, persona_id=per, consorcio_id=cons) as c:
        return c.execute(
            text(
                "select nucleo.registrar_decision(:t, :f, 'Aprueba la plataforma',"
                " 'Acta 12, libro 3, folio 40', :p, 'api')"
            ),
            {"t": tipo, "f": DESDE, "p": per},
        ).scalar_one()


def cambiar(
    motor_app, cons: UUID, per: UUID, modo: str, decision: UUID | None, desde: date = DESDE
):
    with transaccion(motor_app, persona_id=per, consorcio_id=cons) as c:
        return c.execute(
            text("select nucleo.cambiar_modo(:m, :d, :desde, :p, 'api')"),
            {"m": modo, "d": decision, "desde": desde, "p": per},
        ).scalar_one()


def historial(motor_app, cons: UUID, per: UUID) -> list[tuple[str, date, date | None]]:
    with transaccion(motor_app, persona_id=per, consorcio_id=cons) as c:
        return [
            tuple(f)
            for f in c.execute(
                text(
                    "select modo::text, vigente_desde, vigente_hasta from nucleo.modo_operacion"
                    " order by vigente_desde"
                )
            ).all()
        ]


def test_el_enum_de_la_base_es_el_del_contrato(motor_app):
    with motor_app.connect() as c:
        valores = c.execute(
            text("select unnest(enum_range(null::nucleo.tipo_modo))::text")
        ).scalars()
        assert list(valores) == [m.value for m in ModoOperacion]


def test_cambio_valido_cierra_el_anterior_y_audita(motor_migrate, motor_app):
    cons, _, per = consorcio_con_persona(motor_migrate)
    acta = decidir(motor_app, cons, per)
    cambiar(motor_app, cons, per, "consejo", acta)
    otra = decidir(motor_app, cons, per)
    cambiar(motor_app, cons, per, "autogestion", otra, DESDE + timedelta(days=90))

    assert historial(motor_app, cons, per) == [
        ("consejo", DESDE, DESDE + timedelta(days=90)),
        ("autogestion", DESDE + timedelta(days=90), None),
    ]
    with transaccion(motor_app, persona_id=per, consorcio_id=cons) as c:
        acciones = c.execute(
            text("select accion from auditoria.eventos where consorcio_id = :c order by secuencia"),
            {"c": cons},
        ).scalars()
        assert list(acciones) == [
            "consorcio.decision.registrar",
            "consorcio.modo.cambiar",
            "consorcio.decision.registrar",
            "consorcio.modo.cambiar",
        ]


@pytest.mark.parametrize("tipo", ["consejo", "contrato_administracion", "inscripcion_rpa"])
def test_pa31_sin_decision_de_asamblea_el_modo_no_cambia(motor_migrate, motor_app, tipo):
    cons, _, per = consorcio_con_persona(motor_migrate)
    cambiar(motor_app, cons, per, "consejo", decidir(motor_app, cons, per))
    respaldo = decidir(motor_app, cons, per, tipo)

    with pytest.raises(DBAPIError, match="solo cambia con una decisión de asamblea"):
        cambiar(motor_app, cons, per, "autogestion", respaldo, DESDE + timedelta(days=30))
    assert historial(motor_app, cons, per) == [("consejo", DESDE, None)]


def test_pa31_sin_decision_o_con_la_de_otro_consorcio_no_cambia(motor_migrate, motor_app):
    cons, _, per = consorcio_con_persona(motor_migrate)
    otro, _, per_otro = consorcio_con_persona(motor_migrate)
    cambiar(motor_app, cons, per, "consejo", decidir(motor_app, cons, per))
    ajena = decidir(motor_app, otro, per_otro)

    for respaldo in (None, ajena):
        with pytest.raises(DBAPIError, match="solo cambia con una decisión de asamblea"):
            cambiar(motor_app, cons, per, "autogestion", respaldo, DESDE + timedelta(days=30))
    assert historial(motor_app, cons, per) == [("consejo", DESDE, None)]


def test_pa31_cem_app_no_escribe_el_modo_ni_las_decisiones_directo(motor_migrate, motor_app):
    cons, _, per = consorcio_con_persona(motor_migrate)
    acta = decidir(motor_app, cons, per)
    cambiar(motor_app, cons, per, "consejo", acta)
    sentencias = [
        "update nucleo.modo_operacion set modo = 'autogestion'",
        "delete from nucleo.modo_operacion",
        "insert into nucleo.modo_operacion (consorcio_id, modo, vigente_desde, decision_id)"
        f" values ('{cons}', 'autogestion', '2027-01-01', '{acta}')",
        "update nucleo.decision_formal set tipo = 'consejo'",
        "delete from nucleo.decision_formal",
    ]
    for sql in sentencias:
        with pytest.raises(ProgrammingError, match="permission denied"):
            with transaccion(motor_app, persona_id=per, consorcio_id=cons) as c:
                c.execute(text(sql))
    assert historial(motor_app, cons, per) == [("consejo", DESDE, None)]


def test_mismo_modo_o_fecha_anterior_se_rechazan(motor_migrate, motor_app):
    cons, _, per = consorcio_con_persona(motor_migrate)
    cambiar(motor_app, cons, per, "consejo", decidir(motor_app, cons, per))
    with pytest.raises(DBAPIError, match="ya está en ese modo"):
        cambiar(
            motor_app,
            cons,
            per,
            "consejo",
            decidir(motor_app, cons, per),
            DESDE + timedelta(days=1),
        )
    with pytest.raises(DBAPIError, match="después del vigente"):
        cambiar(motor_app, cons, per, "autogestion", decidir(motor_app, cons, per), DESDE)


def test_sin_consorcio_en_el_ambito_no_registra_nada(motor_migrate, motor_app):
    _, _, per = consorcio_con_persona(motor_migrate)
    with pytest.raises(DBAPIError, match="sin consorcio en el ámbito"):
        with transaccion(motor_app, persona_id=per) as c:
            c.execute(
                text(
                    "select nucleo.registrar_decision("
                    "'asamblea', '2026-01-01', 'x', 'Acta 1', :p, 'api')"
                ),
                {"p": per},
            )


def test_la_decision_exige_referencia_del_acta(motor_migrate, motor_app):
    cons, _, per = consorcio_con_persona(motor_migrate)
    with pytest.raises(DBAPIError, match="decision_formal_referencia_acta_check"):
        with transaccion(motor_app, persona_id=per, consorcio_id=cons) as c:
            c.execute(
                text(
                    "select nucleo.registrar_decision("
                    "'asamblea', '2026-01-01', 'x', '   ', :p, 'api')"
                ),
                {"p": per},
            )


def zona_con_otra_fecha(motor_app) -> tuple[str, int]:
    """Una zona cuyo día de hoy difiere del de GMT ahora mismo, y el desfase en días.

    Antes de las 11 GMT, Pago Pago (GMT-11) ya está en el día anterior; desde las 10 GMT,
    Kiritimati (GMT+14) ya está en el siguiente. A cualquier hora una de las dos sirve.
    """
    with motor_app.connect() as c:
        hora = c.execute(text("select extract(hour from now() at time zone 'UTC')")).scalar_one()
    return ("Pacific/Pago_Pago", -1) if hora < 11 else ("Pacific/Kiritimati", 1)


def unidades(motor_app, persona: UUID, consorcio: UUID) -> int:
    app = crear_app(
        Config(url_rol("app"), "0.1.0", "sha-imagen", "sha-contratos", redis_url="memory://")
    )
    app.dependency_overrides[obtener_identidad] = lambda: Identidad(persona)
    with TestClient(app) as cliente:
        return cliente.get(f"/api/v1/consorcios/{consorcio}/unidades").status_code


def test_la_vigencia_usa_la_fecha_del_consorcio_y_no_la_de_la_base(motor_migrate, motor_app):
    zona, desfase = zona_con_otra_fecha(motor_app)
    cons, uni, per = consorcio_con_persona(motor_migrate, zona)
    with motor_migrate.begin() as c:
        hoy_local, hoy_base = c.execute(
            text("select nucleo.hoy_del_consorcio(:c), current_date"), {"c": cons}
        ).one()
        assert hoy_local - hoy_base == timedelta(days=desfase)  # control: las fechas difieren
        # El vínculo vale hoy para el consorcio pero no para la base: empieza hoy (zona
        # adelantada) o termina hoy según la base, que ya va un día adelante (zona atrasada).
        if desfase == 1:
            sql, valor = (
                "update nucleo.vinculo set vigente_desde = :v where consorcio_id = :c",
                hoy_local,
            )
        else:
            sql, valor = (
                "update nucleo.vinculo set vigente_hasta = :v where consorcio_id = :c",
                hoy_base,
            )
        c.execute(text(sql), {"v": valor, "c": cons})
        # Control: con la fecha de la base (lo que hacía require_scope) no habría vínculo vigente.
        con_fecha_base = c.execute(
            text(
                "select count(*) from nucleo.vinculo where consorcio_id = :c"
                " and daterange(vigente_desde, vigente_hasta) @> current_date"
            ),
            {"c": cons},
        ).scalar_one()
        assert con_fecha_base == 0
    assert unidades(motor_app, per, cons) == 200


def test_el_contexto_lleva_el_modo_vigente(motor_migrate, motor_app):
    cons, _, per = consorcio_con_persona(motor_migrate)
    assert require_scope(cons, Identidad(per), motor_app).modo is None
    cambiar(motor_app, cons, per, "consejo", decidir(motor_app, cons, per))
    assert require_scope(cons, Identidad(per), motor_app).modo is ModoOperacion.CONSEJO


def test_la_migracion_0010_baja_y_sube(base_de_prueba, motor_migrate):
    def existe() -> tuple[bool, bool, bool]:
        with motor_migrate.connect() as conn:
            return tuple(
                conn.execute(
                    text(
                        "select to_regclass('nucleo.modo_operacion') is not null,"
                        " to_regclass('nucleo.decision_formal') is not null,"
                        " to_regclass('nucleo.contrasena') is not null"
                    )
                ).one()
            )

    assert existe() == (True, True, True)
    vaciar_mandatos(motor_migrate)  # el downgrade de 0012 se niega con mandatos
    cfg = alembic_config()
    command.downgrade(cfg, "0009")
    assert existe() == (False, False, True)  # la 0009 queda
    command.upgrade(cfg, "head")
    assert existe() == (True, True, True)
