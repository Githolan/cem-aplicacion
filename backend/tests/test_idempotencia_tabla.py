"""`plataforma.idempotencia` contra PostgreSQL real: RLS, privilegios, purga y migración
(documento 13, §7.4, §8.5 y §8.6). Las filas se escriben como `cem_app` con el ámbito fijado,
como lo hará la API."""

from uuid import UUID, uuid4

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError, ProgrammingError

from app.db import transaccion
from app.plataforma import cola, tareas
from tests.conftest import alembic_config, vaciar_mandatos, volver_a_subir

pytestmark = pytest.mark.db

INSERTAR = (
    "insert into plataforma.idempotencia (consorcio_id, persona_id, metodo, ruta, clave,"
    " huella, estado_http, respuesta, vence_en)"
    " values (:c, :p, 'POST', '/api/v1/x', :k, sha256('cuerpo'), 201, '{\"id\": 1}',"
    " now() + interval '24 hours')"
)


@pytest.fixture
def tabla_limpia(motor_migrate, datos):
    with motor_migrate.begin() as c:
        c.execute(text("truncate plataforma.idempotencia"))
    return datos


def guardar(motor_app, persona: UUID, consorcio: UUID, clave: str) -> None:
    with transaccion(motor_app, persona_id=persona, consorcio_id=consorcio) as conn:
        conn.execute(text(INSERTAR), {"c": consorcio, "p": persona, "k": clave})


def vencer(motor_migrate, clave: str) -> None:
    """Corre el reloj de una clave: creada hace 25 horas y vencida hace una."""
    with motor_migrate.begin() as c:
        c.execute(
            text(
                "update plataforma.idempotencia set creado_en = now() - interval '25 hours',"
                " vence_en = now() - interval '1 hour' where clave = :k"
            ),
            {"k": clave},
        )


def test_rls_cada_persona_ve_solo_sus_claves_en_el_consorcio_del_ambito(tabla_limpia, motor_app):
    d, clave = tabla_limpia, str(uuid4())
    a, b = d.consorcio_a, d.consorcio_b
    guardar(motor_app, d.persona_ab, a, clave)
    guardar(motor_app, d.persona_a, a, clave)

    def visibles(persona, consorcio) -> set[tuple[UUID, UUID]]:
        with transaccion(motor_app, persona_id=persona, consorcio_id=consorcio) as conn:
            filas = conn.execute(
                text("select consorcio_id, persona_id from plataforma.idempotencia")
            ).all()
        return {tuple(f) for f in filas}

    assert visibles(d.persona_ab, a) == {(a, d.persona_ab)}
    assert visibles(d.persona_a, a) == {(a, d.persona_a)}
    assert visibles(d.persona_ab, b) == set()
    assert visibles(d.persona_b, b) == set()
    with motor_app.connect() as conn:
        assert conn.execute(text("select count(*) from plataforma.idempotencia")).scalar_one() == 0


def test_rls_no_se_escribe_en_otro_consorcio_ni_a_nombre_de_otra_persona(tabla_limpia, motor_app):
    d = tabla_limpia
    with pytest.raises(ProgrammingError, match="row-level security"):
        with transaccion(motor_app, persona_id=d.persona_ab, consorcio_id=d.consorcio_b) as conn:
            conn.execute(text(INSERTAR), {"c": d.consorcio_a, "p": d.persona_ab, "k": "k1"})
    with pytest.raises(ProgrammingError, match="row-level security"):
        with transaccion(motor_app, persona_id=d.persona_ab, consorcio_id=d.consorcio_a) as conn:
            conn.execute(text(INSERTAR), {"c": d.consorcio_a, "p": d.persona_a, "k": "k2"})


def test_una_respuesta_guardada_no_se_reescribe(tabla_limpia, motor_app):
    d = tabla_limpia
    guardar(motor_app, d.persona_a, d.consorcio_a, "k")
    with pytest.raises(DBAPIError, match="permission denied"):
        with transaccion(motor_app, persona_id=d.persona_a, consorcio_id=d.consorcio_a) as conn:
            conn.execute(text("update plataforma.idempotencia set estado_http = 200"))
    with pytest.raises(IntegrityError, match="duplicate key"):
        guardar(motor_app, d.persona_a, d.consorcio_a, "k")


def test_la_purga_diaria_borra_solo_las_vencidas_de_todos_los_consorcios(
    tabla_limpia, motor_migrate, motor_app
):
    d = tabla_limpia
    guardar(motor_app, d.persona_ab, d.consorcio_a, "vencida-a")
    guardar(motor_app, d.persona_ab, d.consorcio_b, "vencida-b")
    guardar(motor_app, d.persona_ab, d.consorcio_a, "vigente")
    vencer(motor_migrate, "vencida-a")
    vencer(motor_migrate, "vencida-b")
    trabajo = cola.Trabajo(uuid4(), "plataforma.purgar_cola", None, {}, 1, 3)
    # Como en cem-trabajos: cem_app sin persona ni consorcio en el ámbito.
    with transaccion(motor_app, persona_id=None) as conn:
        salida = tareas.purgar_cola(conn, trabajo)
    assert salida["idempotencia_vencidas"] == 2
    with motor_migrate.connect() as conn:
        quedan = conn.execute(text("select clave from plataforma.idempotencia")).scalars().all()
    assert quedan == ["vigente"]


def test_la_migracion_0003_baja_y_vuelve_a_subir(tabla_limpia, motor_migrate):
    def estado() -> tuple[bool, bool, bool]:
        with motor_migrate.connect() as c:
            return tuple(
                c.execute(
                    text(
                        "select to_regclass('plataforma.idempotencia') is not null,"
                        " to_regprocedure('plataforma.purgar_idempotencia()') is not null,"
                        " coalesce((select relrowsecurity and relforcerowsecurity from pg_class"
                        "  where oid = to_regclass('plataforma.idempotencia')), false)"
                    )
                ).one()
            )

    assert estado() == (True, True, True)
    vaciar_mandatos(motor_migrate)
    command.downgrade(alembic_config(), "0002")
    assert estado() == (False, False, False)
    volver_a_subir(alembic_config(), motor_migrate)  # bajó por debajo de 0007
    assert estado() == (True, True, True)
