"""Seguridad por fila contra PostgreSQL real (documento 13, §8.5; prueba PA15)."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError, ProgrammingError

from app.db import transaccion

pytestmark = pytest.mark.db

TABLAS = ["nucleo.consorcio", "nucleo.persona", "nucleo.unidad", "nucleo.vinculo",
          "auditoria.eventos"]  # fmt: skip


def contar(conn, tabla: str) -> int:
    return conn.execute(text(f"select count(*) from {tabla}")).scalar_one()


def test_roles_tienen_los_atributos_del_diseno(motor_app):
    with motor_app.connect() as conn:
        filas = conn.execute(
            text(
                "select rolname, rolbypassrls, rolsuper, rolcreaterole from pg_roles"
                " where rolname in ('cem_app', 'cem_migrate', 'cem_ro')"
            )
        ).all()
    roles = {f[0]: tuple(f[1:]) for f in filas}
    assert roles["cem_app"] == (False, False, False)
    assert roles["cem_migrate"] == (True, False, False)
    assert roles["cem_ro"] == (False, False, False)


@pytest.mark.parametrize("tabla", TABLAS)
def test_sin_ambito_no_se_ve_ninguna_fila(motor_app, datos, tabla):
    with motor_app.connect() as conn:
        assert contar(conn, tabla) == 0


def test_pa15_con_ambito_solo_se_ven_las_unidades_del_consorcio(motor_app, datos):
    with transaccion(motor_app, persona_id=datos.persona_a, consorcio_id=datos.consorcio_a) as c:
        ids = set(c.execute(text("select id from nucleo.unidad")).scalars())
        assert ids == {datos.unidad_a1, datos.unidad_a2}
        # Pedir explícitamente una fila del otro consorcio tampoco la devuelve.
        otra = c.execute(
            text("select count(*) from nucleo.unidad where id = :u"), {"u": datos.unidad_b1}
        ).scalar_one()
        assert otra == 0


def test_el_ambito_vence_con_la_transaccion(motor_app, datos):
    with transaccion(motor_app, persona_id=datos.persona_a, consorcio_id=datos.consorcio_a):
        pass
    with motor_app.connect() as conn:
        assert contar(conn, "nucleo.unidad") == 0


def test_pa15_no_se_puede_escribir_en_otro_consorcio(motor_app, datos):
    with pytest.raises(ProgrammingError, match="row-level security"):
        with transaccion(
            motor_app, persona_id=datos.persona_a, consorcio_id=datos.consorcio_a
        ) as c:
            c.execute(
                text("insert into nucleo.unidad (consorcio_id, designacion) values (:c, 'X')"),
                {"c": datos.consorcio_b},
            )


def test_pa15_cem_app_no_puede_cambiar_el_consorcio_de_ninguna_fila(motor_migrate):
    """Desde 0043 `cem_app` no tiene `UPDATE` sobre `consorcio` ni `unidad`, y en ninguna tabla con
    `consorcio_id` puede cambiar esa columna: mover una fila de consorcio no llega ni a la RLS.
    Control: el mismo chequeo ve el `UPDATE` que sí tiene (`invitacion.revocada_en`)."""
    with motor_migrate.connect() as c:
        tablas = (
            c.execute(
                text(
                    "select a.attrelid::regclass::text from pg_attribute a"
                    " join pg_class k on k.oid = a.attrelid and k.relkind = 'r'"
                    " where a.attname = 'consorcio_id' and not a.attisdropped"
                    " and k.relnamespace::regnamespace::text"
                    " in ('nucleo', 'plataforma', 'auditoria')"
                    " and has_column_privilege('cem_app', a.attrelid, a.attnum, 'UPDATE')"
                )
            )
            .scalars()
            .all()
        )
        control = c.execute(
            text("select has_column_privilege('cem_app', 'nucleo.invitacion', 'revocada_en',"
                 " 'UPDATE')")
        ).scalar_one()  # fmt: skip
    assert tablas == []
    assert control


def test_pa15_no_se_puede_mover_una_fila_a_otro_consorcio(motor_app, datos):
    # Desde 0043 (S2b) `cem_app` ni siquiera tiene UPDATE sobre `unidad`: antes la frenaba la RLS.
    with pytest.raises(ProgrammingError, match="permission denied"):
        with transaccion(
            motor_app, persona_id=datos.persona_a, consorcio_id=datos.consorcio_a
        ) as c:
            c.execute(
                text("update nucleo.unidad set consorcio_id = :b where id = :u"),
                {"b": datos.consorcio_b, "u": datos.unidad_a1},
            )


def test_la_persona_solo_se_ve_a_si_misma(motor_app, datos):
    with transaccion(motor_app, persona_id=datos.persona_a) as c:
        assert list(c.execute(text("select id from nucleo.persona")).scalars()) == [datos.persona_a]


def test_la_persona_ve_sus_vinculos_en_todos_sus_consorcios(motor_app, datos):
    with transaccion(motor_app, persona_id=datos.persona_ab) as c:
        consorcios = set(c.execute(text("select consorcio_id from nucleo.vinculo")).scalars())
    assert consorcios == {datos.consorcio_a, datos.consorcio_b}


def test_lista_de_consorcios_de_la_persona(motor_app, datos):
    with transaccion(
        motor_app, persona_id=datos.persona_a, consorcios_persona=[datos.consorcio_a]
    ) as c:
        assert set(c.execute(text("select id from nucleo.consorcio")).scalars()) == {
            datos.consorcio_a
        }
        # La política alternativa es solo de lectura.
        with pytest.raises(ProgrammingError, match="row-level security"):
            c.execute(text("insert into nucleo.consorcio (nombre) values ('Intruso')"))


def test_pa15_clave_compuesta_impide_referencias_entre_consorcios(motor_migrate, datos):
    with pytest.raises(IntegrityError, match="foreign key"):
        with motor_migrate.begin() as c:
            c.execute(
                text(
                    "insert into nucleo.vinculo"
                    " (consorcio_id, unidad_id, persona_id, tipo, vigente_desde, fuente, origen)"
                    " values (:a, :ub, :p, 'ocupante', '2026-01-01', 'documento', 'soporte')"
                ),
                {"a": datos.consorcio_a, "ub": datos.unidad_b1, "p": datos.persona_a},
            )


def test_vigencias_del_mismo_vinculo_no_se_solapan(motor_migrate, datos):
    with pytest.raises(IntegrityError, match="exclusion"):
        with motor_migrate.begin() as c:
            c.execute(
                text(
                    "insert into nucleo.vinculo"
                    " (consorcio_id, unidad_id, persona_id, tipo, vigente_desde, fuente, origen)"
                    " values (:a, :u, :p, 'propietario', '2026-01-01', 'documento', 'soporte')"
                ),
                {"a": datos.consorcio_a, "u": datos.unidad_a1, "p": datos.persona_a},
            )


def _auditar(c, consorcio, accion: str) -> None:
    c.execute(
        text(
            "insert into auditoria.eventos"
            " (consorcio_id, origen, accion, entidad, hash_previo, hash)"
            " values (:c, 'api', :a, 'unidad', 'ignorado', 'ignorado')"
        ),
        {"c": consorcio, "a": accion},
    )


def _ultimo_hash(motor_app, cons, persona) -> tuple[int, str]:
    with transaccion(motor_app, persona_id=persona, consorcio_id=cons) as c:
        fila = c.execute(
            text("select secuencia, hash from auditoria.eventos order by secuencia desc limit 1")
        ).one_or_none()
    return (fila.secuencia, fila.hash) if fila else (0, "")


def test_auditoria_encadena_por_consorcio(motor_app, datos):
    # La base de la sesión puede tener eventos previos en A (p. ej. los de procedencia que
    # `vaciar_mandatos` agrega antes de bajar migraciones): la cadena sigue desde el último.
    desde, hash_anterior = _ultimo_hash(motor_app, datos.consorcio_a, datos.persona_a)
    for cons, persona in [
        (datos.consorcio_a, datos.persona_a),
        (datos.consorcio_b, datos.persona_b),
    ]:
        for accion in ("crear", "modificar", "eliminar"):
            with transaccion(motor_app, persona_id=persona, consorcio_id=cons) as c:
                _auditar(c, cons, accion)
    with transaccion(motor_app, persona_id=datos.persona_a, consorcio_id=datos.consorcio_a) as c:
        filas = c.execute(
            text(
                "select hash_previo, hash, consorcio_id from auditoria.eventos"
                " where secuencia > :d order by secuencia"
            ),
            {"d": desde},
        ).all()
    assert len(filas) == 3 and {f.consorcio_id for f in filas} == {datos.consorcio_a}
    assert filas[0].hash_previo == hash_anterior
    for previa, actual in zip(filas, filas[1:], strict=False):
        assert actual.hash_previo == previa.hash
    assert all(len(f.hash) == 64 and f.hash != "ignorado" for f in filas)


@pytest.mark.parametrize(
    "sentencia", ["update auditoria.eventos set accion = 'x'", "delete from auditoria.eventos"]
)
def test_auditoria_no_se_modifica_ni_se_borra(motor_app, datos, sentencia):
    with pytest.raises(DBAPIError, match="permission denied"):
        with transaccion(
            motor_app, persona_id=datos.persona_a, consorcio_id=datos.consorcio_a
        ) as c:
            c.execute(text(sentencia))


def test_uuid_v7_es_ordenable_por_tiempo(motor_migrate):
    with motor_migrate.connect() as c:
        ids = c.execute(text("select plataforma.uuid_v7() from generate_series(1, 50)")).scalars()
        ids = list(ids)
    assert all(u.version == 7 for u in ids)
    assert [u.bytes[:6] for u in ids] == sorted(u.bytes[:6] for u in ids)


def test_la_suite_oficial_corre_en_postgresql_18(motor_app):
    # Documento 14, §6.3: producción usa PostgreSQL 18; una corrida en otra versión no vale
    # como evidencia de la cola, el outbox ni la seguridad por fila.
    with motor_app.connect() as conn:
        assert conn.execute(text("show server_version_num")).scalar_one() >= "180000"
