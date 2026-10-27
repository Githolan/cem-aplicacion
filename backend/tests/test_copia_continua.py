"""Copia continua: WAL continuo, copia base física verificada y PA26 en pequeño (H2c §6 del
repositorio CEM; documento 13, §17).

Todo real: `cem-postgres-pruebas` (postgres:18-alpine, musl, `en_US.utf8`, como las bases reales)
con la línea `host replication cem_copia_continua` de `pruebas/postgres-replicacion.sh`, el rol de
`scripts/crear_rol_copia_continua.sql`, `pg_receivewal`/`pg_basebackup`/`pg_verifybackup` 18,
`age` con un par de claves de prueba, el almacén S3 de pruebas y un push de Kuma de prueba. La
copia física de una base musl se restaura en esta imagen Debian, como lo hará
`cem-copia-continua`.
"""

import dataclasses
import gzip
import hashlib
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from botocore.exceptions import EndpointConnectionError
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError

from app.plataforma import copia_continua as cc
from app.plataforma import copias, latido
from tests.conftest import ADMIN_URL, BASE, CLAVES, RAIZ, url_rol
from tests.test_copias import kuma, par_age, url_push  # noqa: F401  (fixtures)

ESPERA_ALMACEN_S = 15
ESPERA_RECEPTOR_S = 60


def url_copia_continua(base: str = "postgres") -> str:
    return (
        make_url(ADMIN_URL)
        .set(username="cem_copia_continua", password=CLAVES["copia_continua"], database=base)
        .render_as_string(hide_password=False)
    )


def libpq(url: str, **extra: str) -> str:
    u = make_url(url)
    partes = {"host": u.host, "port": str(u.port or 5432), "user": u.username,
              "password": u.password, "dbname": u.database, **extra}  # fmt: skip
    return " ".join(f"{k}={v}" for k, v in partes.items())


def esperar(condicion, segundos: float = ESPERA_RECEPTOR_S, paso: float = 0.2):
    limite = time.monotonic() + segundos
    while not (resultado := condicion()):
        assert time.monotonic() < limite, "no se cumplió a tiempo"
        time.sleep(paso)
    return resultado


def admin(sql: str, **p):
    motor = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    try:
        with motor.connect() as c:
            resultado = c.execute(text(sql), p)
            return resultado.all() if resultado.returns_rows else []
    finally:
        motor.dispose()


def borrar_slot() -> None:
    admin(
        "select pg_drop_replication_slot(slot_name) from pg_replication_slots where slot_name = :s",
        s=cc.SLOT,
    )


@pytest.fixture
def cfg(base_de_prueba, par_age, tmp_path) -> cc.ConfigCopiaContinua:  # noqa: F811
    config = cc.ConfigCopiaContinua(
        url_copia_continua=url_copia_continua(),
        copias=copias.ConfigCopias(
            url_backup=url_rol("backup"),
            clave_publica=par_age[1],
            bucket=f"cem-copias-continua-{uuid4().hex[:12]}",
            s3_endpoint=os.environ["CEM_TEST_S3_ENDPOINT"],
            s3_region=os.environ["CEM_TEST_S3_REGION"],
            s3_clave_id=os.environ["CEM_TEST_S3_CLAVE_ID"],
            s3_clave_secreta=os.environ["CEM_TEST_S3_CLAVE_SECRETA"],
        ),
        directorio=tmp_path / "trabajo-copia-continua",
    )
    s3 = copias.cliente_s3(config.copias)
    limite = time.monotonic() + ESPERA_ALMACEN_S
    while True:
        try:
            s3.create_bucket(Bucket=config.copias.bucket)
            return config
        except EndpointConnectionError:
            assert time.monotonic() < limite, "el almacén S3 de pruebas no responde"
            time.sleep(0.2)


@pytest.fixture
def proceso(cfg):
    """Un `CopiaContinua` sin avisos. Al terminar detiene el receptor, espera la copia base en
    curso y borra el slot: un slot olvidado retendría WAL en el servidor de pruebas."""
    borrar_slot()
    creados: list[cc.CopiaContinua] = []

    def crear(entorno: dict[str, str] | None = None) -> cc.CopiaContinua:
        creados.append(cc.CopiaContinua(cfg, entorno or {}))
        return creados[-1]

    yield crear
    for p in creados:
        p.receptor.detener()
        p.esperar_base()
    borrar_slot()


@pytest.fixture
def tabla_pa26(motor_migrate):
    with motor_migrate.begin() as c:
        c.execute(text("create table plataforma.prueba_pa26 (fila text not null)"))
    yield
    with motor_migrate.begin() as c:
        c.execute(text("drop table plataforma.prueba_pa26"))


def lsn_actual() -> int:
    return cc.lsn(admin("select pg_current_wal_lsn()::text")[0][0])


def esperar_recibido(posicion: int, carpeta: Path | None = None) -> None:
    """Hasta que el receptor confirma (`restart_lsn`, con `--synchronous`) `posicion` y, si se
    da la carpeta, hasta que su `.partial` existe (el slot toma posición al empezar a recibir,
    un instante antes de que el receptor cree el archivo)."""
    esperar(lambda: (cc.estado_servidor(url_copia_continua()).restart_lsn or 0) >= posicion)
    if carpeta is not None:
        esperar(lambda: any(carpeta.glob("*.partial")))


def descifrar(identidad: Path, cifrado: bytes) -> bytes:
    return subprocess.run(
        ["age", "-d", "-i", str(identidad)], input=cifrado, check=True, capture_output=True
    ).stdout


def objetos(cfg: cc.ConfigCopiaContinua, prefijo: str) -> list[str]:
    s3 = copias.cliente_s3(cfg.copias)
    return sorted(o["Key"] for o in cc.claves(s3, cfg.copias.bucket, prefijo))


def leer(cfg: cc.ConfigCopiaContinua, clave: str) -> bytes:
    s3 = copias.cliente_s3(cfg.copias)
    return s3.get_object(Bucket=cfg.copias.bucket, Key=clave)["Body"].read()


def bajar_base(cfg, identidad: Path, sello: str, destino: Path) -> Path:
    destino.mkdir()
    for nombre in cc.ARCHIVOS_BASE:
        cifrado = leer(cfg, f"{cc.PREFIJO_BASE}{sello}/{nombre}.age")
        (destino / nombre).write_bytes(descifrar(identidad, cifrado))
    return destino


def bajar_wal(cfg, identidad: Path, destino: Path) -> Path:
    """`wal/` y, para el segmento que no está completo, el parcial más nuevo con su nombre."""
    destino.mkdir()
    for clave in objetos(cfg, cc.PREFIJO_WAL):
        nombre = clave.removeprefix(cc.PREFIJO_WAL).removesuffix(".gz.age")
        (destino / nombre).write_bytes(gzip.decompress(descifrar(identidad, leer(cfg, clave))))
    parciales: dict[str, str] = {}
    for clave in objetos(cfg, cc.PREFIJO_PARCIAL):  # ordenadas: queda la más nueva
        parciales[clave.removeprefix(cc.PREFIJO_PARCIAL).split("-")[0]] = clave
    for segmento, clave in parciales.items():
        if not (destino / segmento).exists():
            contenido = gzip.decompress(descifrar(identidad, leer(cfg, clave)))
            (destino / segmento).write_bytes(contenido)
    return destino


def consultar(socket: Path, sql: str):
    url = make_url(url_rol("backup"))
    motor = create_engine(f"postgresql+psycopg://{url.username}@/{url.database}?host={socket}")
    try:
        with motor.connect() as c:
            return c.execute(text(sql)).all()
    finally:
        motor.dispose()


# Rol cem_copia_continua ---------------------------------------------------------------


@pytest.mark.db
def test_el_rol_replica_y_no_lee_tablas(base_de_prueba):
    atributos = admin(
        "select rolsuper, rolbypassrls, rolreplication, rolcreaterole, rolcreatedb,"
        " rolconnlimit, has_database_privilege(oid, :b, 'CONNECT')"
        " from pg_roles where rolname = 'cem_copia_continua'",
        b=BASE,
    )
    assert atributos == [(False, False, True, False, False, 3, False)]
    # Sin permiso de conexión a la base de datos: ni llega a pedir una tabla.
    motor = create_engine(url_copia_continua(BASE))
    with pytest.raises(OperationalError, match="permission denied for database"):
        with motor.connect() as c:
            c.execute(text("select count(*) from nucleo.consorcio"))
    motor.dispose()
    # Control: la replicación sí la acepta (y `pg_hba` solo la abre para este rol).
    identidad = subprocess.run(
        ["psql", libpq(url_copia_continua(), replication="true"), "-At", "-c", "IDENTIFY_SYSTEM"],
        capture_output=True, text=True,
    )  # fmt: skip
    assert identidad.returncode == 0, identidad.stderr
    assert identidad.stdout.split("|")[0].isdecimal()  # el identificador del sistema
    ajeno = subprocess.run(
        ["psql", libpq(url_rol("backup"), replication="true"), "-At", "-c", "IDENTIFY_SYSTEM"],
        capture_output=True, text=True,
    )  # fmt: skip
    assert ajeno.returncode != 0
    assert "replication connection" in ajeno.stderr and "cem_backup" in ajeno.stderr


# WAL continuo -------------------------------------------------------------------------


@pytest.mark.db
def test_un_segmento_y_un_parcial_llegan_cifrados_y_se_descifran_iguales(
    cfg,
    proceso,
    par_age,  # noqa: F811
    motor_migrate,
    tabla_pa26,
):
    p = proceso()
    ahora = datetime.now(UTC)
    p.iniciar(ahora)
    assert p.corte == "no hay WAL en wal/: la cadena empieza ahora"
    with motor_migrate.begin() as c:
        c.execute(text("insert into plataforma.prueba_pa26 values ('uno')"))
    esperar_recibido(lsn_actual(), p.carpeta_wal)
    p.receptor.detener()  # quieto, para comparar el parcial con lo que se subió
    [parcial] = list(p.carpeta_wal.glob("*.partial"))
    local = parcial.read_bytes()
    p.subir(ahora)
    [clave_parcial] = objetos(cfg, cc.PREFIJO_PARCIAL)
    segmento = parcial.name.removesuffix(".partial")
    assert clave_parcial == f"wal-parcial/{segmento}-{ahora:%Y%m%dT%H%M%SZ}.partial.gz.age"
    subido = leer(cfg, clave_parcial)
    assert subido.startswith(b"age-encryption.org/v1\n")
    assert len(subido) < len(local)  # comprimido antes de cifrar
    assert gzip.decompress(descifrar(par_age[0], subido)) == local
    assert len(local) == p.tamano_segmento  # el parcial ocupa el segmento entero

    # El segmento se completa: va a wal/ y sale del disco.
    with motor_migrate.begin() as c:
        c.execute(text("insert into plataforma.prueba_pa26 values ('dos')"))
    admin("select pg_switch_wal()")
    p.receptor.arrancar()
    completo = esperar(lambda: (p.carpeta_wal / segmento).exists() and p.carpeta_wal / segmento)
    contenido = completo.read_bytes()
    p.subir(ahora + timedelta(minutes=1))  # antes de 5 minutos: el parcial no se repite
    assert not completo.exists()
    assert objetos(cfg, cc.PREFIJO_WAL) == [f"wal/{segmento}.gz.age"]
    assert objetos(cfg, cc.PREFIJO_PARCIAL) == [clave_parcial]
    subido = leer(cfg, f"wal/{segmento}.gz.age")
    assert gzip.decompress(descifrar(par_age[0], subido)) == contenido
    assert p.ultimo == cc.numero_segmento(segmento, p.tamano_segmento)
    assert p.error_subida is None

    # Control: un objeto alterado en el almacén falla la relectura y no se descifra.
    s3 = copias.cliente_s3(cfg.copias)
    suma = s3.head_object(Bucket=cfg.copias.bucket, Key=f"wal/{segmento}.gz.age")["Metadata"]
    copias.verificar_objeto(s3, cfg.copias.bucket, f"wal/{segmento}.gz.age", suma["sha256"])
    alterado = subido[:-10] + bytes(10)
    s3.put_object(Bucket=cfg.copias.bucket, Key=f"wal/{segmento}.gz.age", Body=alterado)
    with pytest.raises(copias.ErrorCopia, match="no coincide"):
        copias.verificar_objeto(s3, cfg.copias.bucket, f"wal/{segmento}.gz.age", suma["sha256"])
    with pytest.raises(subprocess.CalledProcessError):
        descifrar(par_age[0], alterado)


@pytest.mark.db
def test_un_segmento_ya_subido_con_otro_contenido_es_un_defecto(cfg, proceso, motor_migrate):
    p = proceso()
    p.iniciar(datetime.now(UTC))
    p.receptor.detener()
    falso = p.carpeta_wal / "000000010000000000000099"
    falso.write_bytes(b"\x01" * 1024)
    s3 = copias.cliente_s3(cfg.copias)
    s3.put_object(Bucket=cfg.copias.bucket, Key=f"wal/{falso.name}.gz.age", Body=b"x",
                  Metadata={"sha256-claro": "otra"})  # fmt: skip
    with pytest.raises(cc.ErrorCopiaContinua, match="con otro contenido"):
        p.subir_completo(falso)
    assert falso.exists()  # no se borra lo que no quedó en el almacén
    # Control: el mismo contenido ya subido (un reinicio entre subir y borrar), que se relee bien
    # contra el SHA-256 de su cifrado, se da por subido.
    s3.put_object(Bucket=cfg.copias.bucket, Key=f"wal/{falso.name}.gz.age", Body=b"x",
                  Metadata={"sha256-claro": copias.sha256(falso),
                            "sha256": hashlib.sha256(b"x").hexdigest()})  # fmt: skip
    p.subir_completo(falso)
    assert not falso.exists()


@pytest.mark.db
def test_un_segmento_ya_subido_que_no_se_relee_no_se_borra(cfg, proceso, motor_migrate):
    """Una subida cuya relectura falló deja el objeto en el almacén con los metadatos que puso el
    cliente. La vuelta siguiente no puede darlo por bueno por el metadato: lo relee."""
    p = proceso()
    p.iniciar(datetime.now(UTC))
    p.receptor.detener()
    falso = p.carpeta_wal / "000000010000000000000098"
    falso.write_bytes(bytes([2]) * 1024)
    s3 = copias.cliente_s3(cfg.copias)
    clave = f"wal/{falso.name}.gz.age"
    s3.put_object(Bucket=cfg.copias.bucket, Key=clave, Body=b"cifrado alterado",
                  Metadata={"sha256-claro": copias.sha256(falso),
                            "sha256": hashlib.sha256(b"cifrado sano").hexdigest()})  # fmt: skip
    with pytest.raises(copias.ErrorCopia, match="no coincide"):
        p.subir_completo(falso)
    assert falso.exists()
    # Y en la vuelta es un defecto que se avisa en el acto, no a los 15 minutos.
    p.subir(datetime.now(UTC))
    assert falso.exists() and p.defecto is not None
    estado = cc.EstadoServidor(True, "reserved", 0, 0, p.tamano_segmento)
    assert any(
        x.startswith("objeto que no coincide") for x in p.problemas(estado, datetime.now(UTC))
    )


@pytest.mark.db
def test_una_marca_de_copia_base_que_no_se_relee_no_cuenta(cfg, evento):
    momento = datetime.now(UTC)
    resumen = cc.copia_base(cfg, momento)
    s3 = copias.cliente_s3(cfg.copias)
    assert cc.ultima_base_verificada(s3, cfg.copias.bucket) == momento.replace(microsecond=0)
    clave = f"base/{resumen['momento']}/{cc.MARCA}"
    meta = s3.head_object(Bucket=cfg.copias.bucket, Key=clave)["Metadata"]
    cuerpo = s3.get_object(Bucket=cfg.copias.bucket, Key=clave)["Body"].read()
    s3.put_object(Bucket=cfg.copias.bucket, Key=clave, Body=cuerpo + b" ", Metadata=meta)
    assert cc.ultima_base_verificada(s3, cfg.copias.bucket) is None
    # Control: la marca intacta pero un objeto que no es el que nombra, tampoco cuenta.
    s3.put_object(Bucket=cfg.copias.bucket, Key=clave, Body=cuerpo, Metadata=meta)
    assert cc.ultima_base_verificada(s3, cfg.copias.bucket) == momento.replace(microsecond=0)
    objeto = resumen["objetos"]["backup_manifest"]["clave"]
    s3.put_object(Bucket=cfg.copias.bucket, Key=objeto, Body=b"otro", Metadata={"sha256": "0"})
    assert cc.ultima_base_verificada(s3, cfg.copias.bucket) is None


@pytest.mark.db
def test_un_hueco_en_la_cadena_la_corta(cfg):
    p = cc.CopiaContinua(cfg, {})
    p.tamano_segmento = 16 * 1024 * 1024
    p.ultimo = (1, 5)
    p.revisar_continuidad("000000010000000000000006", datetime.now(UTC))  # el siguiente
    assert (p.corte, p.ultimo) == (None, (1, 5))
    p.revisar_continuidad("000000010000000000000008", datetime.now(UTC))  # falta el 7
    assert p.corte.startswith("hueco en el WAL: llegó 000000010000000000000008")
    assert p.ultimo == (1, 7)  # la cadena nueva empieza en el 8
    # Otra línea de tiempo también corta.
    p.corte = None
    p.revisar_continuidad("000000020000000000000009", datetime.now(UTC))
    assert p.corte is not None


@pytest.mark.db
def test_lo_que_subio_otro_contenedor_mientras_se_esperaba_el_slot_no_es_un_hueco(cfg):
    """Despliegue progresivo sin volumen: el viejo sube los segmentos 6 y 7 mientras el nuevo
    espera el slot con `wal/` leído hasta el 5. Cuando el nuevo recibe el 8, no hay corte."""
    p = cc.CopiaContinua(cfg, {})
    p.tamano_segmento = 16 * 1024 * 1024
    p.ultimo = (1, 5)
    s3 = copias.cliente_s3(cfg.copias)
    for n in (6, 7):
        s3.put_object(Bucket=cfg.copias.bucket, Key=f"wal/0000000100000000000000{n:02X}.gz.age",
                      Body=b"x")  # fmt: skip
    p.revisar_continuidad("000000010000000000000008", datetime.now(UTC))
    assert (p.corte, p.ultimo) == (None, (1, 7))
    assert objetos(cfg, cc.PREFIJO_CORTES) == []
    # Control: el 10 sigue siendo un hueco (falta el 9 también en el almacén).
    p.revisar_continuidad("00000001000000000000000A", datetime.now(UTC))
    assert p.corte is not None and p.corte.startswith("hueco en el WAL")


def test_el_atraso_se_mide_en_tiempo_desde_lo_ultimo_confirmado():
    p = cc.CopiaContinua.__new__(cc.CopiaContinua)
    p.observaciones = []
    t0 = datetime(2026, 10, 26, 12, tzinfo=UTC)

    def estado(servidor: int, recibido: int | None) -> cc.EstadoServidor:
        return cc.EstadoServidor(True, "reserved", recibido, servidor, 16 * 1024 * 1024)

    p.medir_atraso(estado(100, 100), t0)
    assert p.atraso == timedelta(0)
    p.medir_atraso(estado(200, 100), t0 + timedelta(minutes=10))
    p.medir_atraso(estado(300, 150), t0 + timedelta(minutes=20))
    assert p.atraso == timedelta(minutes=20) > cc.ATRASO_MAXIMO  # nada nuevo confirmado
    # Control: cuando confirma lo del minuto 10, el atraso baja a 10 minutos; al día, a cero.
    p.medir_atraso(estado(300, 200), t0 + timedelta(minutes=20))
    assert p.atraso == timedelta(minutes=10)
    p.medir_atraso(estado(300, 300), t0 + timedelta(minutes=21))
    assert p.atraso == timedelta(0)


# Copia base ---------------------------------------------------------------------------


@pytest.fixture
def evento(motor_migrate, datos):
    """Un evento de auditoría real (lo encadena el disparador) en el consorcio A."""
    with motor_migrate.begin() as c:
        return c.execute(
            text(
                "insert into auditoria.eventos (consorcio_id, origen, accion, entidad, motivo,"
                " hash_previo, hash) values (:c, 'soporte', 'prueba.copia_continua', 'prueba',"
                " 'original', '', '') returning id"
            ),
            {"c": datos.consorcio_a},
        ).scalar_one()


@pytest.mark.db
def test_la_copia_base_se_verifica_restaurandola_y_se_sube(
    cfg,
    par_age,  # noqa: F811
    motor_migrate,
    evento,
):
    momento = datetime.now(UTC)
    resumen = cc.copia_base(cfg, momento)
    with motor_migrate.connect() as c:
        consorcios = c.execute(text("select count(*) from nucleo.consorcio")).scalar_one()
    assert resumen["consorcios"] == consorcios >= 2
    # La base de origen es como las reales (musl, `en_US.utf8`): la restauración en esta imagen
    # Debian solo conecta porque el locale existe.
    assert admin("select datcollate from pg_database where datname = :b", b=BASE) == [
        ("en_US.utf8",)
    ]
    assert resumen["eventos"] >= 1 and resumen["control_cadena"] == resumen["eventos"]
    assert resumen["eventos_rotos"] == resumen["eventos_distintos"] == 0
    sello = f"{momento:%Y%m%dT%H%M%SZ}"
    assert objetos(cfg, cc.PREFIJO_BASE) == [
        f"base/{sello}/backup_manifest.age", f"base/{sello}/base.tar.gz.age",
        f"base/{sello}/verificada.json",
    ]  # fmt: skip
    s3 = copias.cliente_s3(cfg.copias)
    assert cc.ultima_base_verificada(s3, cfg.copias.bucket) == momento.replace(microsecond=0)
    # Se descifra con la clave privada y el manifiesto es el de PostgreSQL.
    manifiesto = descifrar(par_age[0], leer(cfg, f"base/{sello}/backup_manifest.age"))
    assert b'"PostgreSQL-Backup-Manifest-Version"' in manifiesto
    assert b"Edificio Alvear" not in leer(cfg, f"base/{sello}/base.tar.gz.age")


@pytest.mark.db
def test_una_copia_base_con_un_archivo_corrupto_no_verifica_y_no_cuenta(cfg, monkeypatch, evento):
    tomar = cc.tomar_base

    def tomar_y_corromper(url, destino, sello):
        tomar(url, destino, sello)
        archivo = destino / "base.tar.gz"
        contenido = bytearray(archivo.read_bytes())
        mitad = len(contenido) // 2
        contenido[mitad : mitad + 64] = bytes(64)
        archivo.write_bytes(bytes(contenido))

    monkeypatch.setattr(cc, "tomar_base", tomar_y_corromper)
    with pytest.raises(copias.ErrorCopia, match="pg_verifybackup"):
        cc.copia_base(cfg, datetime.now(UTC))
    assert objetos(cfg, cc.PREFIJO_BASE) == []  # no se subió nada
    s3 = copias.cliente_s3(cfg.copias)
    assert cc.ultima_base_verificada(s3, cfg.copias.bucket) is None
    # Control: la misma copia sin corromper verifica y cuenta.
    monkeypatch.undo()
    cc.copia_base(cfg, datetime.now(UTC))
    assert cc.ultima_base_verificada(s3, cfg.copias.bucket) is not None


@pytest.mark.db
def test_la_verificacion_compara_consorcios_y_la_cadena(cfg, motor_migrate, evento, tmp_path):
    destino = tmp_path / "base"
    cc.tomar_base(cfg.url_copia_continua, destino, "prueba")
    consorcios = cc.contar_consorcios(url_rol("backup"))
    cc.verificar_base(destino, url_rol("backup"), consorcios)  # control: la sana pasa
    with pytest.raises(cc.ErrorCopiaContinua, match="consorcios en la copia"):
        cc.verificar_base(destino, url_rol("backup"), consorcios + 1)
    # Un evento alterado en la base real: su copia no verifica.
    try:
        with motor_migrate.begin() as c:
            c.execute(text("update auditoria.eventos set motivo = 'alterado' where id = :e"),
                      {"e": evento})  # fmt: skip
        alterada = tmp_path / "alterada"
        cc.tomar_base(cfg.url_copia_continua, alterada, "alterada")
        with pytest.raises(
            cc.ErrorCopiaContinua, match=r"cadena de auditoría: 0 enlaces rotos y 1"
        ):
            cc.verificar_base(alterada, url_rol("backup"), consorcios)
    finally:
        with motor_migrate.begin() as c:
            c.execute(text("update auditoria.eventos set motivo = 'original' where id = :e"),
                      {"e": evento})  # fmt: skip


# PA26 en pequeño ----------------------------------------------------------------------


@pytest.mark.db
def test_pa26_restaurar_a_un_momento_recupera_lo_anterior_y_no_lo_posterior(
    cfg,
    proceso,
    par_age,  # noqa: F811
    motor_migrate,
    tabla_pa26,
    tmp_path,
):
    p = proceso()
    inicio = datetime.now(UTC)
    p.iniciar(inicio)
    momento_base = datetime.now(UTC)
    resumen = cc.copia_base(cfg, momento_base)
    with motor_migrate.begin() as c:
        c.execute(text("insert into plataforma.prueba_pa26 values ('antes')"))
    admin("select pg_switch_wal()")  # 'antes' queda en un segmento completo (wal/)
    [(objetivo,)] = admin("select clock_timestamp()")
    time.sleep(1.1)
    with motor_migrate.begin() as c:
        c.execute(text("insert into plataforma.prueba_pa26 values ('despues')"))
    esperar_recibido(lsn_actual())  # 'despues' queda en el segmento en curso (wal-parcial/)
    p.receptor.detener()
    p.subir(datetime.now(UTC))
    assert objetos(cfg, cc.PREFIJO_WAL) and objetos(cfg, cc.PREFIJO_PARCIAL)

    base = bajar_base(cfg, par_age[0], resumen["momento"], tmp_path / "base")
    wal = bajar_wal(cfg, par_age[0], tmp_path / "wal")
    destino = {"recovery_target_time": objetivo.isoformat(sep=" "),
               "recovery_target_inclusive": "false"}  # fmt: skip
    medido = time.monotonic()
    with cc.restaurada(base, destino, wal) as socket:
        filas = consultar(socket, "select fila from plataforma.prueba_pa26 order by 1")
    assert time.monotonic() - medido < 4 * 3600  # RTO de 4 horas (RNF06)
    assert filas == [("antes",)]
    # Control: con el mismo WAL y sin momento, aplica todo y la fila posterior sí está; su
    # ausencia de arriba la decide el momento, no un WAL que faltaba.
    with cc.restaurada(base, {}, wal) as socket:
        filas = consultar(socket, "select fila from plataforma.prueba_pa26 order by 1")
    assert filas == [("antes",), ("despues",)]


# Slot perdido -------------------------------------------------------------------------


def pedidos(kuma) -> list[tuple[str, str]]:  # noqa: F811
    return [
        (q["status"][0], q["msg"][0])
        for q in (parse_qs(urlsplit(x).query, keep_blank_values=True) for x in kuma.pedidos)
    ]


@pytest.fixture
def tope_de_slot():
    """Como en las bases reales (H2c §6 pide 5 GB): sin tope, la copia continua avisa caída."""
    admin("alter system set max_slot_wal_keep_size = '1GB'")
    admin("select pg_reload_conf()")
    yield
    admin("alter system reset max_slot_wal_keep_size")
    admin("select pg_reload_conf()")


@pytest.mark.db
def test_slot_desaparecido_avisa_caida_y_rehace_la_copia_base(
    cfg,
    proceso,
    kuma,  # noqa: F811
    tope_de_slot,
):
    entorno = {cc.VARIABLE_AVISO_CONTINUA: url_push(kuma), cc.VARIABLE_AVISO_BASE: url_push(kuma)}
    p = proceso(entorno)
    t0 = datetime.now(UTC)
    p.iniciar(t0)
    p.vuelta(t0)  # cadena nueva: copia base en el acto, en su hilo
    p.esperar_base()
    assert p.corte is None and p.error_base is None
    p.vuelta(t0 + timedelta(minutes=2))
    assert pedidos(kuma)[-1][0] == "up"
    bases = objetos(cfg, cc.PREFIJO_BASE)

    p.receptor.detener()
    borrar_slot()
    t1 = t0 + timedelta(minutes=4)
    p.vuelta(t1)
    assert ("down", "cadena cortada: el slot no existía") in pedidos(kuma)
    assert p.receptor.vivo()
    assert cc.estado_servidor(url_copia_continua()).slot_existe
    p.esperar_base()
    marcas = [k for k in objetos(cfg, cc.PREFIJO_BASE) if k.endswith(cc.MARCA)]
    assert len(marcas) == 2 and set(bases) < set(objetos(cfg, cc.PREFIJO_BASE))
    assert pedidos(kuma)[-1][0] == "up" and "verificada" in pedidos(kuma)[-1][1]
    assert p.corte is None
    p.vuelta(t1 + timedelta(minutes=2))
    assert pedidos(kuma)[-1][0] == "up" and pedidos(kuma)[-1][1].startswith("al día")


@pytest.fixture
def tope_de_slot_minimo():
    admin("alter system set max_slot_wal_keep_size = '1MB'")
    admin("select pg_reload_conf()")
    yield
    admin("alter system reset max_slot_wal_keep_size")
    admin("select pg_reload_conf()")


@pytest.mark.db
def test_slot_perdido_se_rehace_y_corta_la_cadena(
    cfg, proceso, motor_migrate, tabla_pa26, tope_de_slot_minimo
):
    p = proceso()
    p.iniciar(datetime.now(UTC))
    p.corte = None  # como si la cadena estuviera abierta
    p.ultimo = (1, 0)
    # El slot que crea `pg_receivewal --create-slot` reserva WAL recién cuando el receptor empieza
    # a recibir: sin eso no tiene posición y nunca se pierde.
    esperar_recibido(lsn_actual())
    p.receptor.detener()

    def perdido() -> bool:
        with motor_migrate.begin() as c:
            c.execute(text("insert into plataforma.prueba_pa26 select 'relleno' from"
                           " generate_series(1, 2000)"))  # fmt: skip
        admin("select pg_switch_wal()")
        admin("checkpoint")
        return cc.estado_servidor(url_copia_continua()).wal_status == "lost"

    esperar(perdido, paso=0)
    p.vuelta(datetime.now(UTC))
    assert p.corte == "slot perdido: se pasó de max_slot_wal_keep_size"
    estado = cc.estado_servidor(url_copia_continua())
    assert estado.slot_existe and estado.wal_status != "lost"
    assert p.receptor.vivo()
    p.esperar_base()
    assert p.corte is None  # la copia base nueva la volvió a abrir


# Proceso y configuración --------------------------------------------------------------


def test_sin_configuracion_no_arranca_y_nombra_cada_variable(tmp_path):
    entorno = {k: v for k, v in os.environ.items() if not k.startswith("CEM_")}
    hecho = subprocess.run(
        ["sh", "arranque.sh"], cwd=RAIZ, capture_output=True, text=True, timeout=60,
        env=entorno | {"CEM_PROCESO": "copia_continua", "CEM_COPIAS_BUCKET": "b",
                       "PATH": f"{Path(sys.executable).parent}{os.pathsep}{entorno['PATH']}"},
    )  # fmt: skip
    assert hecho.returncode != 0
    assert "faltan variables de la copia continua" in hecho.stderr
    for variable in (cc.VARIABLE_URL, *copias.VARIABLES.values()):
        assert (variable in hecho.stderr) is (variable != "CEM_COPIAS_BUCKET")


@pytest.mark.db
def test_el_proceso_arranca_late_y_sale_limpio_con_sigterm(cfg, tmp_path):
    entorno = os.environ | {
        "CEM_PROCESO": "copia_continua",
        cc.VARIABLE_URL: cfg.url_copia_continua,
        cc.VARIABLE_DIRECTORIO: str(cfg.directorio),
        "CEM_LATIDO": str(tmp_path / "latido"),
        "PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}",
        **{v: getattr(cfg.copias, campo) for campo, v in copias.VARIABLES.items()},
    }
    borrar_slot()
    proceso = subprocess.Popen(
        ["sh", "arranque.sh"], cwd=RAIZ, env=entorno, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )  # fmt: skip
    try:
        esperar(lambda: (tmp_path / "latido").exists())
        esperar(lambda: cc.estado_servidor(url_copia_continua()).slot_existe)
        esperar(lambda: any(k.endswith(cc.MARCA) for k in objetos(cfg, cc.PREFIJO_BASE)), 120)
    finally:
        proceso.send_signal(signal.SIGTERM)
        salida, error = proceso.communicate(timeout=30)
        borrar_slot()
    assert proceso.returncode == 0, error
    assert "cem-copia-continua detenido" in salida + error


def test_latido_de_la_copia_continua(monkeypatch, tmp_path):
    monkeypatch.setattr(latido, "ARCHIVO", tmp_path / "latido")
    monkeypatch.setenv("CEM_PROCESO", "copia_continua")
    assert not latido.sano()
    latido.latir()
    assert latido.sano()
    viejo = time.time() - latido.EDAD_MAXIMA - 1
    os.utime(tmp_path / "latido", (viejo, viejo))
    assert not latido.sano()
    # Control: un proceso que arranque.sh no conoce nunca está sano, aunque haya latido.
    latido.latir()
    monkeypatch.setenv("CEM_PROCESO", "otro")
    assert not latido.sano()


@pytest.mark.db
def test_la_copia_base_semanal_no_deja_pasar_mas_de_7_dias(cfg, monkeypatch):
    """Retención (H2c §7): con 42 días de `base/` y `wal/`, un momento de hace 34 días tiene su
    copia base anterior guardada solo si dos copias nunca se separan más de `VIGENCIA_BASE`."""
    p = cc.CopiaContinua(cfg, {})
    lanzadas: list[datetime] = []
    monkeypatch.setattr(p, "hacer_base", lambda ahora, cortes: lanzadas.append(ahora))
    s3 = copias.cliente_s3(cfg.copias)
    ahora = datetime(2026, 10, 26, 12, tzinfo=UTC)
    vieja = ahora - cc.VIGENCIA_BASE + cc.INTERVALO_REVISAR_BASE + timedelta(minutes=1)
    marca = b'{"objetos": {}}'  # una marca que se relee bien
    s3.put_object(Bucket=cfg.copias.bucket, Body=marca,
                  Key=f"base/{vieja:%Y%m%dT%H%M%SZ}/{cc.MARCA}",
                  Metadata={"sha256": hashlib.sha256(marca).hexdigest()})  # fmt: skip
    p.revisar_base(ahora)
    p.esperar_base()
    assert lanzadas == []  # le falta más de una revisión para cumplir 7 días
    p.revisar_base(ahora + timedelta(minutes=30))
    assert lanzadas == []  # y no se vuelve a mirar antes de la revisión siguiente
    siguiente = ahora + cc.INTERVALO_REVISAR_BASE
    p.revisar_base(siguiente)
    p.esperar_base()
    assert lanzadas == [siguiente]
    assert siguiente - vieja < cc.VIGENCIA_BASE
    assert cc.ultima_base_verificada(s3, cfg.copias.bucket) == vieja
    # Control: con la cadena cortada se lanza en el acto, sin esperar la revisión.
    p.corte = "prueba"
    p.revisar_base(siguiente + timedelta(minutes=1))
    p.esperar_base()
    assert lanzadas == [siguiente, siguiente + timedelta(minutes=1)]


@pytest.mark.db
def test_un_slot_sin_tope_de_wal_es_un_problema(cfg, tope_de_slot_minimo):
    p = cc.CopiaContinua(cfg, {})
    p.receptor.proceso = None
    estado = cc.estado_servidor(url_copia_continua())
    assert estado.tope_slot_mb == 1
    assert not any("max_slot_wal_keep_size" in x for x in p.problemas(estado, datetime.now(UTC)))
    # El servidor de pruebas, como uno recién creado, viene sin tope (-1).
    admin("alter system reset max_slot_wal_keep_size")
    admin("select pg_reload_conf()")
    estado = esperar(
        lambda: (e := cc.estado_servidor(url_copia_continua())).tope_slot_mb == -1 and e
    )
    assert "max_slot_wal_keep_size sin tope en la base" in p.problemas(estado, datetime.now(UTC))


def test_cerrar_un_corte_y_cortar_no_se_pisan(cfg, monkeypatch):
    """El hilo de la copia base compara y borra el corte bajo el mismo cerrojo que `cortar`."""
    p = cc.CopiaContinua(cfg, {})
    p.corte, p.cortes = "viejo", 1
    resumen = {"momento": "x", "consorcios": 1, "eventos": 1, "tablas": 1, "objetos": {}}
    monkeypatch.setattr(cc, "copia_base", lambda cfg, ahora: resumen)
    with p.cerrojo:
        hilo = threading.Thread(target=p.hacer_base, args=(datetime.now(UTC), 1))
        hilo.start()
        hilo.join(0.5)
        assert hilo.is_alive()  # espera el cerrojo: no puede borrar a mitad de un corte
        p.corte, p.cortes = "nuevo", 2  # lo que hace `cortar` mientras tanto
    hilo.join()
    assert p.corte == "nuevo"
    # Control: sin un corte en el medio, la copia base lo cierra.
    p.hacer_base(datetime.now(UTC), 2)
    assert p.corte is None


@pytest.mark.db
def test_un_corte_sigue_abierto_despues_de_reiniciar(cfg, proceso):
    """Codex en #133: un reinicio entre el corte y la copia base nueva no lo olvida."""
    p1 = proceso()
    p1.iniciar(datetime.now(UTC))
    assert p1.corte is not None
    p1.receptor.detener()  # se reinicia antes de hacer la copia base
    s3 = copias.cliente_s3(cfg.copias)
    assert [k for k in objetos(cfg, cc.PREFIJO_CORTES)]  # el corte quedó en el almacén
    # Ya hay WAL subido: el arranque nuevo no corta por eso, solo por el corte guardado.
    s3.put_object(Bucket=cfg.copias.bucket, Key="wal/000000010000000000000001.gz.age", Body=b"x")
    p2 = proceso()
    p2.iniciar(datetime.now(UTC))
    assert p2.corte is not None and p2.corte.startswith("no hay WAL en wal/")
    p2.vuelta(datetime.now(UTC))  # copia base en el acto
    p2.esperar_base()
    assert p2.corte is None
    p2.receptor.detener()
    # Control: con la copia base posterior al último corte, el arranque siguiente no corta.
    p3 = proceso()
    p3.iniciar(datetime.now(UTC))
    assert p3.corte is None


@pytest.mark.db
def test_un_parcial_sin_cambios_se_vuelve_a_subir_pasado_un_dia(cfg, proceso, motor_migrate):
    p = proceso()
    p.iniciar(datetime.now(UTC))
    esperar_recibido(lsn_actual(), p.carpeta_wal)
    p.receptor.detener()
    t = datetime.now(UTC)
    p.subir_parcial(t)
    assert len(objetos(cfg, cc.PREFIJO_PARCIAL)) == 1
    p.subir_parcial(t + timedelta(minutes=6))  # sin cambios: no se repite
    assert len(objetos(cfg, cc.PREFIJO_PARCIAL)) == 1
    p.subir_parcial(t + cc.REPETIR_PARCIAL + timedelta(minutes=1))  # antes de que lo borre la regla
    assert len(objetos(cfg, cc.PREFIJO_PARCIAL)) == 2


@pytest.mark.db
def test_con_la_base_caida_el_wal_recibido_igual_se_sube_y_se_avisa(
    cfg,
    proceso,
    kuma,  # noqa: F811
    motor_migrate,
):
    p = proceso({cc.VARIABLE_AVISO_CONTINUA: url_push(kuma)})
    p.iniciar(datetime.now(UTC))
    esperar_recibido(lsn_actual(), p.carpeta_wal)
    p.receptor.detener()
    assert objetos(cfg, cc.PREFIJO_PARCIAL) == []
    # La base deja de responder (otro puerto: conexión rechazada).
    caida = make_url(cfg.url_copia_continua).set(port=1).render_as_string(hide_password=False)
    p.cfg = dataclasses.replace(cfg, url_copia_continua=caida)
    p.vuelta(datetime.now(UTC) + timedelta(minutes=2))
    assert len(objetos(cfg, cc.PREFIJO_PARCIAL)) == 1  # lo recibido llegó al almacén
    assert p.error_servidor is not None
    estado, mensaje = pedidos(kuma)[-1]
    assert estado == "down" and "no se puede consultar la base" in mensaje


@pytest.mark.db
def test_sin_copiar_no_hay_latido_aunque_el_proceso_siga_vivo(
    cfg, proceso, monkeypatch, tmp_path, tope_de_slot
):
    """Sin monitor de Kuma, el healthcheck es lo único que ve un proceso vivo que no copia."""
    monkeypatch.setattr(latido, "ARCHIVO", tmp_path / "latido")
    p = proceso()
    t = datetime.now(UTC)
    p.iniciar(t)
    latido.ARCHIVO.unlink()  # el arranque late: sin esto, el control no podría fallar
    p.vuelta(t)
    assert latido.ARCHIVO.exists()  # sano (el corte inicial tiene su copia base en curso)
    p.esperar_base()
    latido.ARCHIVO.unlink()
    p.receptor.detener()
    p.receptor_caido_desde = t - cc.ATRASO_MAXIMO - timedelta(minutes=1)
    p.vuelta(t + timedelta(minutes=1))
    assert not latido.ARCHIVO.exists()
    assert p.problemas_de_salud(t + timedelta(minutes=1))[0].startswith("receptor caído")
    # Control: con el receptor de nuevo en marcha (lo rearrancó la vuelta), vuelve el latido.
    p.vuelta(t + timedelta(minutes=2))
    assert latido.ARCHIVO.exists()
    # Y una subida atrasada también lo corta.
    latido.ARCHIVO.unlink()
    p.al_dia_subida = t - cc.ATRASO_MAXIMO - timedelta(minutes=5)
    assert p.problemas_de_salud(t)[0].startswith("sin poder subir")


@pytest.mark.db
def test_al_detenerse_sube_lo_completo_y_el_parcial(cfg, proceso, motor_migrate, tabla_pa26):
    p = proceso()
    t = datetime.now(UTC)
    p.iniciar(t)
    esperar_recibido(lsn_actual(), p.carpeta_wal)
    p.subir(t)  # el primer parcial
    with motor_migrate.begin() as c:
        c.execute(text("insert into plataforma.prueba_pa26 values ('antes de parar')"))
    admin("select pg_switch_wal()")
    esperar(lambda: any(cc.SEGMENTO.match(a.name) for a in p.carpeta_wal.iterdir()))
    with motor_migrate.begin() as c:
        c.execute(text("insert into plataforma.prueba_pa26 values ('en curso')"))
    esperar_recibido(lsn_actual())
    parciales = len(objetos(cfg, cc.PREFIJO_PARCIAL))
    assert objetos(cfg, cc.PREFIJO_WAL) == []
    p.cerrar()  # SIGTERM: antes de los 5 minutos del parcial siguiente
    assert len(objetos(cfg, cc.PREFIJO_WAL)) == 1
    assert len(objetos(cfg, cc.PREFIJO_PARCIAL)) == parciales + 1
    assert not any(cc.SEGMENTO.match(a.name) for a in p.carpeta_wal.iterdir())


def test_dos_procesos_no_usan_el_mismo_directorio(cfg):
    p1, p2 = cc.CopiaContinua(cfg, {}), cc.CopiaContinua(cfg, {})
    assert p1.tomar_directorio()
    assert not p2.tomar_directorio()
    p1.soltar_directorio()
    assert p2.tomar_directorio()  # control: suelto, el otro lo toma
    p2.soltar_directorio()


@pytest.mark.db
def test_mientras_otro_receptor_tiene_el_slot_el_nuevo_espera_sin_avisar(
    cfg,
    proceso,
    kuma,  # noqa: F811
    tmp_path,
):
    viejo = proceso()
    viejo.iniciar(datetime.now(UTC))
    esperar(lambda: cc.estado_servidor(url_copia_continua()).slot_activo)
    otra = dataclasses.replace(cfg, directorio=tmp_path / "otro-directorio")
    nuevo = cc.CopiaContinua(otra, {cc.VARIABLE_AVISO_CONTINUA: url_push(kuma)})
    nuevo.iniciar(datetime.now(UTC))
    esperar(lambda: not nuevo.receptor.vivo())  # el slot está ocupado: el receptor sale
    antes = len(kuma.pedidos)
    nuevo.vuelta(datetime.now(UTC) + timedelta(minutes=2))
    assert len(kuma.pedidos) == antes  # no avisa una caída que no es
    # Control: con el viejo detenido, el nuevo toma el slot y avisa.
    viejo.receptor.detener()
    # El slot queda libre, o ya lo tomó el receptor que la vuelta anterior volvió a lanzar.
    esperar(lambda: nuevo.receptor.vivo()
            or not cc.estado_servidor(url_copia_continua()).slot_activo)  # fmt: skip
    nuevo.vuelta(datetime.now(UTC) + timedelta(minutes=4))
    assert len(kuma.pedidos) == antes + 1
    nuevo.receptor.detener()
    nuevo.esperar_base()  # la vuelta lanzó la copia base del corte inicial


def test_las_herramientas_reciben_solo_path_y_la_clave(monkeypatch):
    monkeypatch.setenv("CEM_COPIAS_S3_CLAVE_SECRETA", "no-tiene-que-pasar")
    argumentos, entorno = cc.conexion_libpq("postgresql+psycopg://u:clave@h:5433/postgres")
    assert set(entorno) == {"PATH", "PGPASSWORD"} and entorno["PGPASSWORD"] == "clave"
    assert "clave" not in " ".join(argumentos)  # la clave nunca en los argumentos (ps)
    assert argumentos == ["--host=h", "--username=u", "--no-password", "--port=5433"]


def test_el_registro_del_receptor_se_recorta(tmp_path):
    registro = tmp_path / "receptor.log"
    contenido = os.urandom(2 * cc.TOPE_REGISTRO_RECEPTOR)
    registro.write_bytes(contenido)
    r = cc.Receptor("postgresql+psycopg://u:c@h/postgres", tmp_path, registro)
    r.recortar_registro()
    assert registro.read_bytes() == contenido[-cc.FINAL_REGISTRO_RECEPTOR :]
    # Control: uno chico no se toca.
    registro.write_bytes(b"corto")
    r.recortar_registro()
    assert registro.read_bytes() == b"corto"


def test_una_herramienta_colgada_se_corta_por_su_tope():
    inicio = time.monotonic()
    with pytest.raises(copias.ErrorCopia, match="sleep pasó su tope de 0.3 s"):
        copias._correr(["sleep", "5"], tope=0.3)
    assert time.monotonic() - inicio < 3
    copias._correr(["sleep", "0"], tope=5)  # control: dentro del tope, pasa


def test_pg_basebackup_y_pg_verifybackup_corren_con_tope(monkeypatch, tmp_path):
    topes = {}

    def correr(argumentos, entorno=None, tope=None):
        topes[argumentos[0]] = tope

    monkeypatch.setattr(copias, "_correr", correr)
    cc.tomar_base("postgresql+psycopg://u:c@h/postgres", tmp_path / "b", "x")
    with pytest.raises(FileNotFoundError):  # sigue a la restauración, que acá no hay
        cc.verificar_base(tmp_path / "b", "postgresql+psycopg://u:c@h/cem", 1)
    assert topes == {"pg_basebackup": cc.TOPE_COPIA_FISICA,
                     "pg_verifybackup": cc.TOPE_VERIFICAR_MANIFIESTO}  # fmt: skip


@pytest.mark.db
def test_un_corte_abierto_mas_que_una_copia_base_entera_cuenta_para_la_salud(cfg):
    p = cc.CopiaContinua(cfg, {})
    ahora = datetime.now(UTC)
    p.cortar("prueba", ahora)
    p.hilo_base = threading.Thread(target=lambda: None)  # como una copia base en curso
    assert p.problemas_de_salud(ahora + cc.DURACION_MAXIMA_COPIA_BASE) == []
    tarde = ahora + cc.DURACION_MAXIMA_COPIA_BASE + timedelta(minutes=1)
    assert p.problemas_de_salud(tarde)[0].startswith("cadena cortada desde")


@pytest.mark.db
def test_un_corte_con_su_copia_base_fallida_deja_sin_latido(cfg, monkeypatch, tmp_path):
    monkeypatch.setattr(latido, "ARCHIVO", tmp_path / "latido")
    p = cc.CopiaContinua(cfg, {})
    ahora = datetime.now(UTC)
    p.cortar("prueba", ahora)

    def falla(cfg, momento):
        raise copias.ErrorCopia("pg_basebackup terminó con 1")

    monkeypatch.setattr(cc, "copia_base", falla)
    p.hacer_base(ahora, p.cortes)
    assert p.error_base is not None
    assert p.problemas_de_salud(ahora)[0].startswith("cadena cortada y la copia base falla")
    # Control: mientras corre otra copia base (el reintento), esa falla no cuenta.
    p.hilo_base = threading.Thread(target=time.sleep, args=(1,))
    p.hilo_base.start()
    assert p.problemas_de_salud(ahora) == []
    p.hilo_base.join()


@pytest.mark.db
def test_una_copia_base_que_falla_no_frena_la_de_un_corte_nuevo(cfg, monkeypatch):
    p = cc.CopiaContinua(cfg, {})
    ahora = datetime.now(UTC)
    p.cortar("primero", ahora)
    cortes = p.cortes

    def falla_con_corte_en_el_medio(cfg, momento):
        p.cortar("segundo", ahora)  # mientras la copia del primero corre
        raise copias.ErrorCopia("pg_basebackup terminó con 1")

    monkeypatch.setattr(cc, "copia_base", falla_con_corte_en_el_medio)
    p.hacer_base(ahora, cortes)
    assert p.corte == "segundo" and p.error_base is None  # la del segundo sigue siendo urgente
