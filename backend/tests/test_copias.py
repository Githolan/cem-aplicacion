"""Copia diaria verificada (documento 13, §17; plan H1, semana 4: H0b). Parte 2a.

Todo real: PostgreSQL 18 (`cem-postgres-pruebas`), el rol `cem_backup` de
`scripts/crear_roles.sql` y `pg_dump`/`pg_restore`/`initdb` 18 de la imagen.
"""

import subprocess

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from app.plataforma import copias
from tests.conftest import url_rol


@pytest.fixture
def tabla_de_prueba(motor_migrate):
    with motor_migrate.begin() as c:
        c.execute(text("create table plataforma.prueba_copias (n int not null)"))
    yield
    with motor_migrate.begin() as c:
        c.execute(text("drop table plataforma.prueba_copias"))


# Rol cem_backup -----------------------------------------------------------------------


@pytest.mark.db
def test_cem_backup_lee_todo_sin_ambito_y_no_puede_escribir(motor_migrate, datos):
    backup = create_engine(url_rol("backup"))
    with motor_migrate.connect() as c:
        total = c.execute(text("select count(*) from nucleo.consorcio")).scalar_one()
    with backup.connect() as c:
        # Sin ámbito fijado cem_app no ve nada (RLS forzada); cem_backup tiene que ver todo.
        assert c.execute(text("select count(*) from nucleo.consorcio")).scalar_one() == total >= 2
        assert c.execute(
            text("select rolsuper, rolbypassrls, rolreplication, rolcreaterole, rolcreatedb"
                 " from pg_roles where rolname = current_user")
        ).one() == (False, True, False, False, False)  # fmt: skip
    with pytest.raises(DBAPIError, match="read-only transaction|permission denied"):
        with backup.begin() as c:
            c.execute(text("insert into nucleo.consorcio (nombre) values ('intruso')"))
    backup.dispose()


# Controles negativos: una copia mala no cuenta ---------------------------------------


@pytest.mark.db
def test_un_volcado_truncado_no_pasa_la_verificacion(datos, tmp_path):
    volcado = tmp_path / "cem.dump"
    esperados = copias.volcar(url_rol("backup"), volcado)
    copias.verificar_volcado(volcado, esperados)  # el sano pasa
    contenido = volcado.read_bytes()
    volcado.write_bytes(contenido[: len(contenido) // 2])
    with pytest.raises(copias.ErrorCopia, match="pg_restore"):
        copias.verificar_volcado(volcado, esperados)


@pytest.mark.db
def test_un_volcado_que_no_coincide_con_los_controles_no_pasa(
    motor_migrate, datos, tabla_de_prueba, tmp_path
):
    viejo = tmp_path / "viejo.dump"
    copias.volcar(url_rol("backup"), viejo)
    with motor_migrate.begin() as c:
        c.execute(text("insert into plataforma.prueba_copias values (1)"))
    esperados_nuevos = copias.volcar(url_rol("backup"), tmp_path / "nuevo.dump")
    with pytest.raises(copias.ErrorCopia, match=r"plataforma\.prueba_copias"):
        copias.verificar_volcado(viejo, esperados_nuevos)


@pytest.mark.db
def test_los_controles_salen_de_la_misma_instantanea_que_el_volcado(
    motor_migrate, datos, tabla_de_prueba, tmp_path, monkeypatch
):
    """Filas escritas justo antes y justo después de pg_dump: no están en el volcado ni en los
    controles, porque ambos leen la instantánea exportada al empezar."""
    correr = copias._correr

    def insertar() -> None:
        with motor_migrate.begin() as c:
            c.execute(text("insert into plataforma.prueba_copias values (2)"))

    def escribir_y_correr(argumentos, entorno=None):
        if argumentos[0] == "pg_dump":
            insertar()
        correr(argumentos, entorno)
        if argumentos[0] == "pg_dump":
            insertar()

    monkeypatch.setattr(copias, "_correr", escribir_y_correr)
    volcado = tmp_path / "cem.dump"
    esperados = copias.volcar(url_rol("backup"), volcado)
    monkeypatch.undo()
    assert esperados["plataforma.prueba_copias"][0] == 0
    copias.verificar_volcado(volcado, esperados)


def test_la_imagen_trae_las_herramientas_de_la_version_del_servidor():
    for programa in ("pg_dump", "pg_restore", "initdb", "pg_ctl"):
        salida = subprocess.run([programa, "--version"], check=True, capture_output=True, text=True)
        assert " 18." in salida.stdout, salida.stdout
    subprocess.run(["age", "--version"], check=True, capture_output=True)
