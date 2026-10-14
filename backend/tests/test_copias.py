"""Copia diaria verificada (documento 13, §17; plan H1, semana 4: H0b).

Todo real: PostgreSQL 18 (`cem-postgres-pruebas`), el rol `cem_backup` de
`scripts/crear_roles.sql`, `pg_dump`/`pg_restore`/`initdb` 18, `age` con un par de claves que
genera cada prueba y un almacén S3 compatible (`cem-almacen-s3-pruebas`, compose.yml).
"""

import os
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from botocore.exceptions import EndpointConnectionError
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import DBAPIError

from app.plataforma import copias
from tests.conftest import ADMIN_URL, url_rol

VENTANA = datetime(2026, 10, 15, 3, 30, tzinfo=UTC)
ESPERA_ALMACEN_S = 15


@pytest.fixture
def par_age(tmp_path: Path) -> tuple[Path, str]:
    """Identidad privada (archivo) y clave pública `age1…` generadas para la prueba."""
    identidad = tmp_path / "identidad.txt"
    subprocess.run(["age-keygen", "-o", str(identidad)], check=True, capture_output=True)
    publica = subprocess.run(
        ["age-keygen", "-y", str(identidad)], check=True, capture_output=True, text=True
    ).stdout.strip()
    return identidad, publica


@pytest.fixture
def cfg(base_de_prueba, par_age) -> copias.ConfigCopias:
    """Configuración real contra el almacén de pruebas, con un bucket nuevo por prueba."""
    config = copias.ConfigCopias(
        url_backup=url_rol("backup"),
        clave_publica=par_age[1],
        bucket=f"cem-copias-prueba-{uuid4().hex[:12]}",
        s3_endpoint=os.environ["CEM_TEST_S3_ENDPOINT"],
        s3_region=os.environ["CEM_TEST_S3_REGION"],
        s3_clave_id=os.environ["CEM_TEST_S3_CLAVE_ID"],
        s3_clave_secreta=os.environ["CEM_TEST_S3_CLAVE_SECRETA"],
    )
    s3 = copias.cliente_s3(config)
    limite = time.monotonic() + ESPERA_ALMACEN_S
    while True:  # el almacén puede tardar un instante en escuchar después de arrancar
        try:
            s3.create_bucket(Bucket=config.bucket)
            return config
        except EndpointConnectionError:
            assert time.monotonic() < limite, "el almacén S3 de pruebas no responde"
            time.sleep(0.2)


@pytest.fixture
def tabla_de_prueba(motor_migrate):
    with motor_migrate.begin() as c:
        c.execute(text("create table plataforma.prueba_copias (n int not null)"))
    yield
    with motor_migrate.begin() as c:
        c.execute(text("drop table plataforma.prueba_copias"))


def libpq(url: URL) -> str:
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


def leer_objeto(cfg: copias.ConfigCopias, clave: str) -> bytes:
    return copias.cliente_s3(cfg).get_object(Bucket=cfg.bucket, Key=clave)["Body"].read()


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


# Copia completa -----------------------------------------------------------------------


@pytest.mark.db
def test_la_copia_se_sube_cifrada_y_se_descifra_y_restaura_igual(
    motor_migrate, datos, cfg, par_age, tmp_path
):
    resultado = copias.copia_diaria(cfg, VENTANA)

    clave = "diarias/2026-10-15/cem_prueba-20261015T033000Z.dump.age"
    assert resultado["objetos"] == [clave]
    subido = leer_objeto(cfg, clave)
    assert len(subido) == resultado["bytes"]
    assert copias.hashlib.sha256(subido).hexdigest() == resultado["sha256"]
    assert subido.startswith(b"age-encryption.org/v1\n-> X25519 ")
    assert b"Edificio Alvear" not in subido  # nada legible sin la identidad privada

    cifrado = tmp_path / "copia.dump.age"
    cifrado.write_bytes(subido)
    volcado = tmp_path / "copia.dump"
    otra = tmp_path / "otra.txt"
    subprocess.run(["age-keygen", "-o", str(otra)], check=True, capture_output=True)
    ajena = subprocess.run(
        ["age", "-d", "-i", str(otra), "-o", str(volcado), str(cifrado)], capture_output=True
    )
    assert ajena.returncode != 0  # otra identidad no la abre
    subprocess.run(
        ["age", "-d", "-i", str(par_age[0]), "-o", str(volcado), str(cifrado)],
        check=True,
        capture_output=True,
    )

    # Restauración completa, con dueños y permisos, en otra base del mismo servidor.
    admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text("drop database if exists cem_prueba_restaurada with (force)"))
        c.execute(text("create database cem_prueba_restaurada"))
    restaurada = make_url(ADMIN_URL).set(database="cem_prueba_restaurada")
    subprocess.run(
        ["pg_restore", "--exit-on-error", "--dbname", libpq(restaurada), str(volcado)],
        check=True,
        capture_output=True,
    )
    motor_restaurada = create_engine(restaurada)
    try:
        with motor_migrate.begin() as c:
            originales = copias.controles(c)
        with motor_restaurada.begin() as c:
            assert copias.controles(c) == originales
            nombres = c.execute(text("select nombre from nucleo.consorcio order by 1")).scalars()
            assert {"Edificio Alvear 100", "Edificio Belgrano 200"} <= set(nombres)
        assert resultado["tablas"] == len(originales)
    finally:
        motor_restaurada.dispose()
        with admin.connect() as c:
            c.execute(text("drop database cem_prueba_restaurada with (force)"))
        admin.dispose()


def test_la_del_primer_dia_del_mes_tambien_va_a_mensuales():
    assert copias.claves_destino("cem", datetime(2026, 11, 1, 3, 30, tzinfo=UTC)) == [
        "diarias/2026-11-01/cem-20261101T033000Z.dump.age",
        "mensuales/2026-11/cem-20261101T033000Z.dump.age",
    ]
    assert copias.claves_destino("cem", VENTANA) == [
        "diarias/2026-10-15/cem-20261015T033000Z.dump.age"
    ]


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


@pytest.mark.db
def test_un_objeto_alterado_en_el_almacen_se_detecta(cfg):
    s3 = copias.cliente_s3(cfg)
    contenido = b"age-encryption.org/v1\n" + bytes(range(256)) * 64
    suma = copias.hashlib.sha256(contenido).hexdigest()
    s3.put_object(Bucket=cfg.bucket, Key="diarias/x", Body=contenido)
    copias.verificar_objeto(s3, cfg.bucket, "diarias/x", suma)
    s3.put_object(Bucket=cfg.bucket, Key="diarias/x", Body=contenido[:-1] + b"\x00")
    with pytest.raises(copias.ErrorCopia, match="no coincide"):
        copias.verificar_objeto(s3, cfg.bucket, "diarias/x", suma)


@pytest.mark.db
def test_una_clave_publica_invalida_hace_fallar_la_copia(datos, cfg):
    mala = copias.ConfigCopias(**(vars(cfg) | {"clave_publica": "age1noesunaclave"}))
    with pytest.raises(copias.ErrorCopia, match="age"):
        copias.copia_diaria(mala, VENTANA)
    s3 = copias.cliente_s3(cfg)
    assert s3.list_objects_v2(Bucket=cfg.bucket)["KeyCount"] == 0  # nada subido


def test_sin_configuracion_falla_y_nombra_cada_variable():
    with pytest.raises(copias.ErrorCopia) as error:
        copias.ConfigCopias.desde_entorno({"CEM_COPIAS_BUCKET": "cem-copias-seguridad"})
    for variable in copias.VARIABLES.values():
        assert (variable in str(error.value)) is (variable != "CEM_COPIAS_BUCKET")


# Planificador y trabajador -----------------------------------------------------------


@pytest.mark.db
def test_el_planificador_la_encola_a_las_0330_y_el_trabajador_la_hace(
    motor_app, motor_migrate, datos, cfg, monkeypatch
):
    # Se importa acá para no agregar las tareas reales a las otras pruebas del planificador.
    import app.plataforma.tareas  # noqa: F401
    from app.plataforma.planificador import TAREAS, programar
    from app.plataforma.trabajos import Trabajador

    for campo, variable in copias.VARIABLES.items():
        monkeypatch.setenv(variable, getattr(cfg, campo))
    with motor_migrate.begin() as c:
        c.execute(text("truncate plataforma.jobs, plataforma.ejecuciones_programadas"))
    copia = [t for t in TAREAS if t.tipo == "plataforma.copia_diaria"]
    assert len(copia) == 1
    assert copia[0].ventana(VENTANA - timedelta(minutes=1)) == VENTANA - timedelta(days=1)
    assert programar(motor_app, VENTANA + timedelta(minutes=1), copia) == 1
    assert Trabajador(motor_app).procesar_lote() == 1
    with motor_migrate.connect() as c:
        estado, carga, resultado = c.execute(
            text("select estado::text, carga, resultado from plataforma.jobs")
        ).one()
    assert carga["ventana"] == VENTANA.isoformat()
    assert estado == "hecho"
    assert resultado["objetos"] == ["diarias/2026-10-15/cem_prueba-20261015T033000Z.dump.age"]
    assert (
        copias.hashlib.sha256(leer_objeto(cfg, resultado["objetos"][0])).hexdigest()
        == (resultado["sha256"])
    )


def test_la_imagen_trae_las_herramientas_de_la_version_del_servidor():
    for programa in ("pg_dump", "pg_restore", "initdb", "pg_ctl"):
        salida = subprocess.run([programa, "--version"], check=True, capture_output=True, text=True)
        assert " 18." in salida.stdout, salida.stdout
    subprocess.run(["age", "--version"], check=True, capture_output=True)
