"""Mandatos y facultades otorgadas (migración 0011, corte 2b-2) contra PostgreSQL real.

Cierran el corte (diseño de H2b, §7): la baja revoca las sesiones y no borra nada (RN22); la
superposición se rechaza; `cem_app` no escribe directo; un mandato en el edificio A no da nada en
el B; mandato o facultad sin decisión de respaldo, rechazados (criterio 9). También el script de
arranque de D3 y que la migración baja y sube.
"""

import json
import os
from datetime import date, timedelta
from uuid import UUID

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, ProgrammingError

from app.db import transaccion
from scripts import alta_mandato
from tests.conftest import alembic_config, url_rol
from tests.test_modo_operacion import consorcio_con_persona, decidir

pytestmark = pytest.mark.db

DESDE = date(2026, 1, 1)
PAQUETE_CONSEJO = sorted(
    [
        "documentos.ver",
        "fiscalizacion.ver_panel",
        "fondo_reserva.autorizar",
        "invitaciones.crear",
        "liquidacion.revisar",
        "padron.ver",
        "solicitud_formal.firmar",
        "solicitud_formal.preparar",
    ]
)


def alta(motor_app, cons, actor, decision, *, tipo="miembro_consejo", persona=None,
         nombre=None, facultades=None, desde=DESDE, hasta=None) -> UUID:  # fmt: skip
    with transaccion(motor_app, persona_id=actor, consorcio_id=cons) as c:
        return c.execute(
            text(
                "select nucleo.alta_mandato(:t, :p, :n, null, null, :d, :desde, :hasta, :f,"
                " :a, 'api')"
            ),
            {
                "t": tipo,
                "p": persona,
                "n": nombre,
                "d": decision,
                "desde": desde,
                "hasta": hasta,
                "f": facultades,
                "a": actor,
            },
        ).scalar_one()


def baja(motor_app, cons, actor, mandato) -> int:
    with transaccion(motor_app, persona_id=actor, consorcio_id=cons) as c:
        return c.execute(
            text("select nucleo.baja_mandato(:m, 'Renuncia aceptada en asamblea', :a, 'api')"),
            {"m": mandato, "a": actor},
        ).scalar_one()


def facultades_de(motor_app, cons, actor, mandato) -> list[str]:
    with transaccion(motor_app, persona_id=actor, consorcio_id=cons) as c:
        return list(
            c.execute(
                text(
                    "select facultad from nucleo.facultad_otorgada where mandato_id = :m"
                    " order by facultad"
                ),
                {"m": mandato},
            ).scalars()
        )


def sesiones_abiertas(motor_migrate, persona, n: int = 0) -> int:
    """Abre `n` sesiones vigentes para la persona y devuelve cuántas tiene abiertas."""
    with motor_migrate.begin() as c:
        for _ in range(n):
            c.execute(
                text(
                    "insert into nucleo.sesion (persona_id, token_hash, dispositivo, ip, vence_en)"
                    " values (:p, sha256(gen_random_uuid()::text::bytea), gen_random_uuid(),"
                    " '127.0.0.1', now() + interval '1 day')"
                ),
                {"p": persona},
            )
        return c.execute(
            text(
                "select count(*) from nucleo.sesion where persona_id = :p"
                " and revocada_en is null and vence_en > now()"
            ),
            {"p": persona},
        ).scalar_one()


def otra_persona(motor_migrate, cons) -> UUID:
    with motor_migrate.begin() as c:
        return c.execute(
            text("insert into nucleo.persona (correo, nombre) values (:c, 'Otra') returning id"),
            {"c": f"otra-{os.urandom(6).hex()}-{cons}@ejemplo.com"},
        ).scalar_one()


def test_alta_con_el_paquete_del_tipo_y_auditada(motor_migrate, motor_app):
    cons, _, per = consorcio_con_persona(motor_migrate)
    m = alta(motor_app, cons, per, decidir(motor_app, cons, per), persona=per)
    assert facultades_de(motor_app, cons, per, m) == PAQUETE_CONSEJO
    with transaccion(motor_app, persona_id=per, consorcio_id=cons) as c:
        accion, despues = c.execute(
            text("select accion, despues from auditoria.eventos where entidad_id = :m"),
            {"m": m},
        ).one()
    assert accion == "consorcio.mandato.alta"
    assert despues["facultades"] == PAQUETE_CONSEJO


def test_el_acta_puede_achicar_el_paquete_pero_no_ampliarlo(motor_migrate, motor_app):
    cons, _, per = consorcio_con_persona(motor_migrate)
    acta = decidir(motor_app, cons, per)
    m = alta(motor_app, cons, per, acta, persona=per, facultades=["padron.ver"])
    assert facultades_de(motor_app, cons, per, m) == ["padron.ver"]
    otra = otra_persona(motor_migrate, cons)
    with pytest.raises(
        DBAPIError, match="fuera del paquete de miembro_consejo: egresos.ejecutar_pago"
    ):
        alta(motor_app, cons, per, acta, persona=otra,
             facultades=["padron.ver", "egresos.ejecutar_pago"])  # fmt: skip
    with pytest.raises(DBAPIError, match="al menos una facultad"):
        alta(motor_app, cons, per, acta, persona=otra, facultades=[])


def test_administrador_externo_sin_cuenta_con_nombre(motor_migrate, motor_app):
    cons, _, per = consorcio_con_persona(motor_migrate)
    acta = decidir(motor_app, cons, per, "contrato_administracion")
    m = alta(motor_app, cons, per, acta, tipo="administrador_externo", nombre="Administración X")
    assert facultades_de(motor_app, cons, per, m) == ["documentos.cargar"]
    with pytest.raises(DBAPIError, match="mandato_check"):
        alta(motor_app, cons, per, acta, tipo="administrador_externo")


def test_criterio_9_sin_decision_de_respaldo_no_hay_mandato(motor_migrate, motor_app):
    cons, _, per = consorcio_con_persona(motor_migrate)
    for decision in (None, UUID("01a14826-0000-7000-8000-000000000000")):
        with pytest.raises(DBAPIError, match="decisión de respaldo de este consorcio"):
            alta(motor_app, cons, per, decision, persona=per)
    with transaccion(motor_app, persona_id=per, consorcio_id=cons) as c:
        assert c.execute(text("select count(*) from nucleo.mandato")).scalar_one() == 0


def test_superposicion_del_mismo_tipo_y_persona_rechazada(motor_migrate, motor_app):
    cons, _, per = consorcio_con_persona(motor_migrate)
    acta = decidir(motor_app, cons, per)
    alta(motor_app, cons, per, acta, persona=per, hasta=DESDE + timedelta(days=365))
    with pytest.raises(DBAPIError, match="ya hay un mandato de miembro_consejo"):
        alta(motor_app, cons, per, acta, persona=per, desde=DESDE + timedelta(days=100))
    # Controles: otro tipo en las mismas fechas, y el mismo tipo después del primero, sí.
    alta(motor_app, cons, per, acta, tipo="administrador", persona=per)
    alta(motor_app, cons, per, acta, persona=per, desde=DESDE + timedelta(days=365))


def test_rn22_la_baja_revoca_las_sesiones_y_no_borra_nada(motor_migrate, motor_app):
    cons, _, per = consorcio_con_persona(motor_migrate)
    mandatario = otra_persona(motor_migrate, cons)
    ajena = otra_persona(motor_migrate, cons)
    m = alta(motor_app, cons, per, decidir(motor_app, cons, per), persona=mandatario)
    assert sesiones_abiertas(motor_migrate, mandatario, 2) == 2
    assert sesiones_abiertas(motor_migrate, ajena, 1) == 1

    assert baja(motor_app, cons, per, m) == 2

    assert sesiones_abiertas(motor_migrate, mandatario) == 0
    assert sesiones_abiertas(motor_migrate, ajena) == 1  # control: otra persona sigue
    assert facultades_de(motor_app, cons, per, m) == PAQUETE_CONSEJO  # intactas
    with transaccion(motor_app, persona_id=per, consorcio_id=cons) as c:
        hasta, hoy, motivo = c.execute(
            text(
                "select vigente_hasta, nucleo.hoy_del_consorcio(consorcio_id), motivo_fin"
                " from nucleo.mandato where id = :m"
            ),
            {"m": m},
        ).one()
        vigentes = c.execute(
            text(
                "select count(*) from nucleo.mandato where id = :m"
                " and daterange(vigente_desde, vigente_hasta) @> nucleo.hoy_del_consorcio(:c)"
            ),
            {"m": m, "c": cons},
        ).scalar_one()
        acciones = list(
            c.execute(
                text(
                    "select accion from auditoria.eventos where entidad_id = :m order by secuencia"
                ),
                {"m": m},
            ).scalars()
        )
    assert (hasta, motivo, vigentes) == (hoy, "Renuncia aceptada en asamblea", 0)
    assert acciones == ["consorcio.mandato.alta", "consorcio.mandato.baja"]
    with pytest.raises(DBAPIError, match="ya terminó"):
        baja(motor_app, cons, per, m)


def test_un_mandato_en_el_edificio_a_no_da_nada_en_el_b(motor_migrate, motor_app):
    cons_a, _, per_a = consorcio_con_persona(motor_migrate)
    cons_b, _, per_b = consorcio_con_persona(motor_migrate)
    acta_a = decidir(motor_app, cons_a, per_a)
    m = alta(motor_app, cons_a, per_a, acta_a, persona=per_a)

    def visibles(cons, per) -> tuple[int, int]:
        with transaccion(motor_app, persona_id=per, consorcio_id=cons) as c:
            return c.execute(
                text(
                    "select (select count(*) from nucleo.mandato where id = :m),"
                    " (select count(*) from nucleo.facultad_otorgada where mandato_id = :m)"
                ),
                {"m": m},
            ).one()

    assert visibles(cons_a, per_a) == (1, len(PAQUETE_CONSEJO))  # control
    assert visibles(cons_b, per_a) == (0, 0)
    # La decisión de A no respalda un mandato en B, ni la baja de B alcanza el mandato de A.
    with pytest.raises(DBAPIError, match="decisión de respaldo de este consorcio"):
        alta(motor_app, cons_b, per_b, acta_a, persona=per_a)
    with pytest.raises(DBAPIError, match="no existe en este consorcio"):
        baja(motor_app, cons_b, per_b, m)


def test_cem_app_no_escribe_mandatos_ni_facultades_directo(motor_migrate, motor_app):
    cons, _, per = consorcio_con_persona(motor_migrate)
    acta = decidir(motor_app, cons, per)
    m = alta(motor_app, cons, per, acta, persona=per)
    sentencias = [
        "update nucleo.mandato set vigente_hasta = null",
        "delete from nucleo.mandato",
        "insert into nucleo.mandato (consorcio_id, tipo, persona_id, decision_id, vigente_desde,"
        f" origen) values ('{cons}', 'administrador', '{per}', '{acta}', '2026-01-01', 'soporte')",
        "delete from nucleo.facultad_otorgada",
        "insert into nucleo.facultad_otorgada (consorcio_id, mandato_id, facultad, decision_id)"
        f" values ('{cons}', '{m}', 'egresos.ejecutar_pago', '{acta}')",
        "insert into nucleo.facultad values ('dinero.todo', 'dinero', true)",
        "insert into nucleo.paquete_facultad values ('miembro_consejo', 'egresos.ejecutar_pago')",
    ]
    for sql in sentencias:
        with pytest.raises(ProgrammingError, match="permission denied"):
            with transaccion(motor_app, persona_id=per, consorcio_id=cons) as c:
                c.execute(text(sql))
    assert facultades_de(motor_app, cons, per, m) == PAQUETE_CONSEJO


def test_la_decision_guarda_el_sha256_del_acta(motor_migrate, motor_app):
    cons, _, per = consorcio_con_persona(motor_migrate)
    sha = "AB" * 32
    with transaccion(motor_app, persona_id=per, consorcio_id=cons) as c:
        d = c.execute(
            text(
                "select nucleo.registrar_decision('asamblea', '2026-01-01', 'x', 'Acta 1',"
                " :p, 'api', :s)"
            ),
            {"p": per, "s": sha},
        ).scalar_one()
        assert (
            c.execute(
                text("select sha256_acta from nucleo.decision_formal where id = :d"), {"d": d}
            ).scalar_one()
            == sha.lower()
        )
    with pytest.raises(DBAPIError, match="sha256_acta_check"):
        with transaccion(motor_app, persona_id=per, consorcio_id=cons) as c:
            c.execute(
                text(
                    "select nucleo.registrar_decision('asamblea', '2026-01-01', 'x', 'Acta 1',"
                    " :p, 'api', 'no-es-un-hash')"
                ),
                {"p": per},
            )


# Script de arranque (D3) -----------------------------------------------------------------


def arranque(cons: UUID, persona: UUID) -> dict:
    return {
        "consorcio_id": str(cons),
        "decision": {"tipo": "asamblea", "fecha": "2026-01-01",
                     "descripcion": "Designa el Consejo y adopta el Modo Consejo",
                     "referencia_acta": "Acta 7, libro 2, folio 15"},
        "modo_inicial": {"modo": "consejo", "desde": "2026-01-01"},
        "mandatos": [
            {"tipo": "miembro_consejo", "persona_id": str(persona), "desde": "2026-01-01"},
            {"tipo": "administrador_externo", "mandatario_nombre": "Administración X",
             "mandatario_cuit": "20123456789", "desde": "2026-01-01"},
        ],
    }  # fmt: skip


@pytest.fixture
def correr(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CEM_DATABASE_URL", url_rol("app"))

    def _correr(datos: dict, *extra: str) -> tuple[int, str, str]:
        ruta = tmp_path / "arranque.json"
        ruta.write_text(json.dumps(datos), encoding="utf-8")
        codigo = alta_mandato.main(["--archivo", str(ruta), *extra])
        salida = capsys.readouterr()
        return codigo, salida.out, salida.err

    return _correr


def contar(motor_migrate, cons) -> tuple[int, int, int, int]:
    with motor_migrate.connect() as c:
        return c.execute(
            text(
                "select (select count(*) from nucleo.decision_formal where consorcio_id = :c),"
                " (select count(*) from nucleo.modo_operacion where consorcio_id = :c),"
                " (select count(*) from nucleo.mandato where consorcio_id = :c),"
                " (select count(*) from auditoria.eventos where consorcio_id = :c)"
            ),
            {"c": cons},
        ).one()


def test_script_simular_no_escribe_y_el_real_registra_todo(correr, motor_migrate):
    cons, _, per = consorcio_con_persona(motor_migrate)
    codigo, out, _ = correr(arranque(cons, per), "--simular")
    assert codigo == 0 and out.startswith("SIMULACIÓN")
    assert contar(motor_migrate, cons) == (0, 0, 0, 0)

    codigo, out, _ = correr(arranque(cons, per))
    assert codigo == 0 and "Acta 7, libro 2, folio 15" in out
    assert contar(motor_migrate, cons) == (1, 1, 2, 4)
    with motor_migrate.connect() as c:
        origenes = set(
            c.execute(
                text("select origen from auditoria.eventos where consorcio_id = :c"), {"c": cons}
            ).scalars()
        )
    assert origenes == {"soporte"}


def test_script_correrlo_dos_veces_falla_y_no_duplica(correr, motor_migrate):
    cons, _, per = consorcio_con_persona(motor_migrate)
    assert correr(arranque(cons, per))[0] == 0
    codigo, _, err = correr(arranque(cons, per))
    assert codigo == 1 and "no se escribió nada" in err
    assert contar(motor_migrate, cons) == (1, 1, 2, 4)


@pytest.mark.parametrize(
    ("cambio", "esperado"),
    [
        ({"mandatos": [], "modo_inicial": None}, "no registra nada"),
        ({"decision": {"tipo": "asamblea", "fecha": "2026-01-01", "descripcion": "x",
                       "referencia_acta": "  "}}, "referencia_acta"),
        ({"mandatos": [{"tipo": "miembro_consejo", "desde": "2026-01-01"}]},
         "persona_id o mandatario_nombre"),
    ],
)  # fmt: skip
def test_script_archivos_invalidos_no_llegan_a_la_base(correr, motor_migrate, cambio, esperado):
    cons, _, per = consorcio_con_persona(motor_migrate)
    codigo, _, err = correr(arranque(cons, per) | cambio)
    assert codigo == 2 and esperado in err
    assert contar(motor_migrate, cons) == (0, 0, 0, 0)


def test_la_migracion_0011_baja_y_sube(base_de_prueba, motor_migrate):
    def existe() -> tuple[bool, bool, bool]:
        with motor_migrate.connect() as conn:
            return tuple(
                conn.execute(
                    text(
                        "select to_regclass('nucleo.mandato') is not null,"
                        " to_regclass('nucleo.facultad') is not null,"
                        " to_regclass('nucleo.modo_operacion') is not null"
                    )
                ).one()
            )

    assert existe() == (True, True, True)
    cfg = alembic_config()
    command.downgrade(cfg, "0010")
    assert existe() == (False, False, True)  # la 0010 queda
    command.upgrade(cfg, "head")
    assert existe() == (True, True, True)
