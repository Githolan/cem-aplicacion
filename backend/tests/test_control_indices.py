"""Control diario de los índices GiST (E58; `app/plataforma/indices.py`, migración 0022).

Todo real: PostgreSQL 18 con los roles `cem_backup` (lee) y `cem_app` (`REINDEX` con `MAINTAIN`)
y el push de Kuma de `test_copias` (un servidor HTTP de verdad en el proceso de la prueba).

Para probar la reparación hace falta un índice que no coincida con su tabla. El fallo de
PostgreSQL que motivó el control no se provoca a pedido, así que el desajuste se hace con una
causa legítima del mismo efecto: un índice GiST sobre una función declarada `immutable` que en
realidad depende de una tabla. Al cambiar esa tabla, la lectura secuencial calcula la clave nueva
y el índice sigue guardando la vieja, hasta que un `REINDEX` la recalcula.
"""

from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

import app.plataforma.tareas  # noqa: F401  (registra el tipo de trabajo)
from app.db import transaccion
from app.plataforma import cola, indices, planificador
from app.plataforma.trabajos import Trabajador
from tests.conftest import ADMIN_URL, BASE, url_rol
from tests.test_cola import cola_vacia  # noqa: F401  (es un fixture)
from tests.test_contexto_mandatos import edificio  # noqa: F401  (es un fixture)
from tests.test_copias import kuma, url_push  # noqa: F401  (`kuma` es un fixture)
from tests.test_padron_rutas import escena  # noqa: F401  (es un fixture)

ESQUEMA = "prueba_indices"
INDICE = f"{ESQUEMA}.relacion_clave_vigencia_excl"
CONSORCIO_A = "01a00000-0000-7000-8000-00000000000a"
CONSORCIO_B = "01a00000-0000-7000-8000-00000000000b"


@pytest.fixture
def entorno(kuma):  # noqa: F811
    return {indices.VARIABLE_BACKUP: url_rol("backup"), indices.VARIABLE_AVISO: url_push(kuma)}


def avisos(kuma) -> list[tuple[str, str]]:  # noqa: F811
    """`(status, msg)` de cada push que llegó a Kuma."""
    return [
        (q["status"][0], q["msg"][0]) for q in (parse_qs(urlsplit(p).query) for p in kuma.pedidos)
    ]


def gist_de_nucleo(motor_migrate) -> int:
    with motor_migrate.connect() as c:
        return c.execute(
            text(
                "select count(*) from pg_index x join pg_class i on i.oid = x.indexrelid"
                " join pg_am a on a.oid = i.relam join pg_class t on t.oid = x.indrelid"
                " join pg_namespace n on n.oid = t.relnamespace"
                " where a.amname = 'gist' and n.nspname = 'nucleo'"
            )
        ).scalar_one()


@pytest.fixture
def desajustable(motor_migrate, base_de_prueba):
    """Tabla con un GiST de exclusión sobre una clave que se puede desajustar a pedido. Tres
    filas de dos consorcios, con vigencias que no se solapan entre ninguna (así el `REINDEX`
    sobre la clave común no choca con la exclusión)."""
    with motor_migrate.begin() as c:
        c.execute(
            text(
                f"""
                create schema {ESQUEMA};
                create table {ESQUEMA}.ajuste (corrido boolean not null);
                insert into {ESQUEMA}.ajuste values (false);
                create function {ESQUEMA}.clave(c uuid) returns uuid language sql immutable as $$
                  select case when (select corrido from {ESQUEMA}.ajuste)
                              then '00000000-0000-0000-0000-000000000000'::uuid else c end $$;
                create table {ESQUEMA}.relacion (
                  consorcio_id uuid not null, desde date not null, hasta date not null,
                  constraint relacion_clave_vigencia_excl exclude using gist (
                    {ESQUEMA}.clave(consorcio_id) with =, daterange(desde, hasta) with &&));
                insert into {ESQUEMA}.relacion values
                  ('{CONSORCIO_A}', '2024-01-01', '2024-02-01'),
                  ('{CONSORCIO_A}', '2024-03-01', '2024-04-01'),
                  ('{CONSORCIO_B}', '2024-05-01', '2024-06-01');
                grant usage on schema {ESQUEMA} to cem_app;
                grant maintain on {ESQUEMA}.relacion to cem_app;
                """
            )
        )
    yield
    with motor_migrate.begin() as c:
        c.execute(text(f"drop schema {ESQUEMA} cascade"))


def desajustar(motor_migrate) -> None:
    with motor_migrate.begin() as c:
        c.execute(text(f"update {ESQUEMA}.ajuste set corrido = true"))


# Contra los índices reales de `nucleo` --------------------------------------------------------


@pytest.mark.db
def test_los_gist_de_nucleo_coinciden_con_sus_tablas_y_avisa_up(
    escena,  # noqa: F811
    motor_migrate,
    entorno,
    kuma,  # noqa: F811
):
    # Control: los GiST de `nucleo` contados en el catálogo, aparte del módulo (cuatro hoy).
    esperados = gist_de_nucleo(motor_migrate)
    assert esperados == 4
    r = indices.controlar(url_rol("app"), entorno)
    assert r["indices"] == esperados
    assert r["valores"] >= 3  # el consorcio de la escena, en vínculos, mandatos y modo
    assert r["diferencias"] == []
    assert r["aviso"] == "enviado"
    assert avisos(kuma) == [("up", f"4 índices GiST, {r['valores']} valores, sin diferencias")]


@pytest.mark.db
def test_0022_da_maintain_a_cem_app_solo_sobre_las_tablas_con_gist(motor_migrate, base_de_prueba):
    with motor_migrate.connect() as c:
        tiene = c.execute(
            text(
                "select has_table_privilege('cem_app', 'nucleo.vinculo', 'MAINTAIN'),"
                " has_table_privilege('cem_app', 'nucleo.mandato', 'MAINTAIN'),"
                " has_table_privilege('cem_app', 'nucleo.modo_operacion', 'MAINTAIN'),"
                " has_table_privilege('cem_app', 'nucleo.unidad', 'MAINTAIN'),"
                " has_table_privilege('cem_backup', 'nucleo.vinculo', 'MAINTAIN')"
            )
        ).one()
    assert tuple(tiene) == (True, True, True, False, False)


# Desajuste, reparación y fallas ----------------------------------------------------------------


@pytest.mark.db
def test_un_indice_desajustado_se_reconstruye_y_avisa_down(
    desajustable,
    motor_migrate,
    entorno,
    kuma,  # noqa: F811
):
    antes = indices.controlar(url_rol("app"), entorno, esquemas=[ESQUEMA])
    assert (antes["indices"], antes["valores"], antes["diferencias"]) == (1, 2, [])

    desajustar(motor_migrate)
    r = indices.controlar(url_rol("app"), entorno, esquemas=[ESQUEMA])
    cero = "00000000-0000-0000-0000-000000000000"
    assert r["diferencias"] == [
        {"indice": INDICE, "tabla": f"{ESQUEMA}.relacion", "valor": cero, "secuencial": 3,
         "por_indice": 0}
    ]  # fmt: skip
    assert r["reconstruidos"] == [INDICE]
    assert avisos(kuma)[-1] == ("down", f"reparado con REINDEX: {INDICE}")
    # Después del REINDEX el índice ve las tres filas con la clave nueva.
    assert indices.verificar(url_rol("backup"), [ESQUEMA]).diferencias == []


@pytest.mark.db
def test_sin_maintain_no_repara_avisa_down_y_falla(desajustable, motor_migrate, entorno, kuma):  # noqa: F811
    with motor_migrate.begin() as c:
        c.execute(text(f"revoke maintain on {ESQUEMA}.relacion from cem_app"))
    desajustar(motor_migrate)
    with pytest.raises(indices.ErrorIndices, match="no se pudo reconstruir"):
        indices.controlar(url_rol("app"), entorno, esquemas=[ESQUEMA])
    assert avisos(kuma) == [("down", f"no se pudo reconstruir {INDICE}: ProgrammingError")]
    assert len(indices.verificar(url_rol("backup"), [ESQUEMA]).diferencias) == 1


@pytest.mark.db
def test_si_el_planificador_no_usa_el_gist_el_control_falla(desajustable, entorno):
    """Un índice que el planificador no puede usar (aquí, marcado inválido, como queda un
    `create index concurrently` que falló): el control no probaría nada y tiene que decirlo."""
    admin = create_engine(ADMIN_URL.rsplit("/", 1)[0] + "/" + BASE, poolclass=NullPool)
    with admin.begin() as c:
        c.execute(
            text("update pg_index set indisvalid = false where indexrelid = cast(:i as regclass)"),
            {"i": INDICE},
        )
    admin.dispose()
    with pytest.raises(cola.ErrorDefinitivo, match=r"no_verificable: .* usó \[\]"):
        indices.controlar(url_rol("app"), entorno, esquemas=[ESQUEMA])


def test_sin_url_de_backup_es_error_definitivo():
    with pytest.raises(cola.ErrorDefinitivo, match=indices.VARIABLE_BACKUP):
        indices.controlar(url_rol("app"), {})


@pytest.mark.db
def test_sin_aviso_configurado_el_control_corre_igual(desajustable, motor_migrate, caplog):
    desajustar(motor_migrate)
    r = indices.controlar(url_rol("app"), {indices.VARIABLE_BACKUP: url_rol("backup")}, [ESQUEMA])
    assert (r["reconstruidos"], r["aviso"]) == ([INDICE], "sin_configurar")
    assert indices.VARIABLE_AVISO in caplog.text


# El trabajo programado ---------------------------------------------------------------------------


def test_el_control_esta_programado_todos_los_dias():
    tarea = next(t for t in planificador.TAREAS if t.tipo == "plataforma.verificar_indices")
    assert tarea.periodo.days == 1
    assert tarea.ventana(datetime(2026, 10, 22, 12, tzinfo=UTC)) == datetime(
        2026, 10, 22, 4, 15, tzinfo=UTC
    )


@pytest.mark.db
def test_cem_trabajos_corre_el_control_con_sus_propias_conexiones(
    escena,  # noqa: F811
    cola_vacia,  # noqa: F811
    motor_app,
    motor_migrate,
    entorno,
    kuma,  # noqa: F811
    monkeypatch,
):
    for nombre, valor in entorno.items():
        monkeypatch.setenv(nombre, valor)
    with transaccion(motor_app, persona_id=None) as c:
        id_ = cola.encolar(c, "plataforma.verificar_indices", carga={"ventana": "2026-10-22"})
    assert Trabajador(motor_app).procesar_lote() == 1
    with motor_migrate.connect() as c:
        fila = c.execute(
            text("select estado, resultado from plataforma.jobs where id = :i"), {"i": id_}
        ).one()
    assert fila.estado == "hecho"
    assert (fila.resultado["indices"], fila.resultado["diferencias"]) == (4, [])
    assert avisos(kuma)[-1][0] == "up"
