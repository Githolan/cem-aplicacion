"""Réplica diaria de documentos y control de tamaño del bucket de copias (H2c §6 del repositorio
CEM; documento 13, §17.2).

Contra servicios reales: una base aparte migrada a la última versión (la réplica lee todas las
versiones `disponible` de la base, y la de la sesión tiene las de otras pruebas, con sus objetos
en otros buckets), `cem_backup`, el almacén S3 de pruebas con un bucket de documentos con
versiones y uno de copias, `age` con un par de prueba y un push de Kuma de prueba.
"""

import hashlib
import os
import subprocess
import time
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from alembic import command
from botocore.exceptions import EndpointConnectionError
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.plataforma import almacen, copias, replica_documentos
from tests.conftest import ADMIN_URL, CLAVES, alembic_config
from tests.test_copias import kuma, par_age, url_push  # noqa: F401  (fixtures)

BASE_REPLICA = "cem_prueba_replica"
ESPERA_ALMACEN_S = 15


def url(rol: str) -> str:
    return (
        make_url(ADMIN_URL)
        .set(username=f"cem_{rol}", password=CLAVES[rol], database=BASE_REPLICA)
        .render_as_string(hide_password=False)
    )


@pytest.fixture
def base_replica(base_de_prueba):
    admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f"drop database if exists {BASE_REPLICA} with (force)"))
        c.execute(text(f"create database {BASE_REPLICA} owner cem_migrate"))
        c.execute(text(f"revoke all on database {BASE_REPLICA} from public"))
        c.execute(text(f"grant connect on database {BASE_REPLICA} to cem_app, cem_backup"))
    cfg = alembic_config()
    cfg.set_main_option("sqlalchemy.url", url("migrate").replace("%", "%%"))
    command.upgrade(cfg, "head")
    motor = create_engine(url("migrate"))
    yield motor
    motor.dispose()
    with admin.connect() as c:
        c.execute(text(f"drop database if exists {BASE_REPLICA} with (force)"))
    admin.dispose()


def _bucket(s3, versiones: bool) -> str:
    nombre = f"cem-prueba-{uuid4().hex[:12]}"
    limite = time.monotonic() + ESPERA_ALMACEN_S
    while True:
        try:
            s3.create_bucket(Bucket=nombre)
            break
        except EndpointConnectionError:
            assert time.monotonic() < limite, "el almacén S3 de pruebas no responde"
            time.sleep(0.2)
    if versiones:
        s3.put_bucket_versioning(Bucket=nombre, VersioningConfiguration={"Status": "Enabled"})
    return nombre


@pytest.fixture
def escena(base_replica, par_age):  # noqa: F811
    cfg = copias.ConfigCopias(
        url_backup=url("backup"),
        clave_publica=par_age[1],
        bucket="",
        s3_endpoint=os.environ["CEM_TEST_S3_ENDPOINT"],
        s3_region=os.environ["CEM_TEST_S3_REGION"],
        s3_clave_id=os.environ["CEM_TEST_S3_CLAVE_ID"],
        s3_clave_secreta=os.environ["CEM_TEST_S3_CLAVE_SECRETA"],
    )
    s3 = copias.cliente_s3(cfg)
    cfg = copias.ConfigCopias(**(vars(cfg) | {"bucket": _bucket(s3, versiones=False)}))
    origen = almacen.AlmacenDocumentos(
        _bucket(s3, versiones=True), cfg.s3_endpoint, cfg.s3_region, cfg.s3_clave_id,
        cfg.s3_clave_secreta,
    )  # fmt: skip
    with base_replica.begin() as c:
        consorcio = c.execute(
            text("insert into nucleo.consorcio (nombre) values ('Edificio Réplica') returning id")
        ).scalar_one()
        persona = c.execute(
            text("insert into nucleo.persona (correo, nombre) values ('r@ejemplo.com', 'R')"
                 " returning id")
        ).scalar_one()  # fmt: skip
        documento = c.execute(
            text("insert into nucleo.documento (consorcio_id, categoria, titulo, fecha,"
                 " cargado_por) values (:c, 'seguros', 'Póliza', current_date, :p) returning id"),
            {"c": consorcio, "p": persona},
        ).scalar_one()  # fmt: skip
    return {"cfg": cfg, "origen": origen, "s3": s3, "motor": base_replica,
            "consorcio": consorcio, "persona": persona, "documento": documento}  # fmt: skip


def version(e, estado: str, contenido: bytes, **extra) -> dict:
    """Sube `contenido` al bucket de documentos y carga su versión en `estado`."""
    clave = f"cem/{e['consorcio']}/{e['documento']}/{uuid4()}"
    subido = e["s3"].put_object(Bucket=e["origen"].bucket, Key=clave, Body=contenido)
    columnas = {
        "en_cuarentena": {},
        "disponible": {"antivirus": "ClamAV 1.5.4", "firmas_en": "now()", "analizada_en": "now()"},
        "rechazado": {"motivo_rechazo": "'virus'", "analizada_en": "now()"},
    }[estado]
    valores = {k: (f"'{v}'" if k == "antivirus" else v) for k, v in columnas.items()}
    fila = {
        "sha256": hashlib.sha256(contenido).hexdigest(),
        "tamano": len(contenido),
        **extra,
    }
    with e["motor"].begin() as c:
        vid = c.execute(
            text(
                "insert into nucleo.version_documento (consorcio_id, documento_id, estado, tipo,"
                " tamano, sha256, clave_objeto, version_objeto, subida_por"
                + "".join(f", {k}" for k in valores)
                + ") values (:c, :d, :estado, 'application/pdf', :tamano, :sha256, :clave, :vo,"
                " :p" + "".join(f", {v}" for v in valores.values()) + ") returning id"
            ),
            {"c": e["consorcio"], "d": e["documento"], "estado": estado, "clave": clave,
             "vo": subido.get("VersionId"), "p": e["persona"], **fila},
        ).scalar_one()  # fmt: skip
    return {"id": vid, "clave": clave, "contenido": contenido}


def descifrar(identidad, cifrado: bytes) -> bytes:
    return subprocess.run(
        ["age", "-d", "-i", str(identidad)], input=cifrado, check=True, capture_output=True
    ).stdout


def replica(e) -> list[str]:
    paginas = (
        e["s3"]
        .get_paginator("list_objects_v2")
        .paginate(Bucket=e["cfg"].bucket, Prefix=replica_documentos.PREFIJO)
    )
    return sorted(o["Key"] for p in paginas for o in p.get("Contents", []))


# Réplica ------------------------------------------------------------------------------


@pytest.mark.db
def test_copia_solo_las_disponibles_cifradas_y_no_repite(escena, par_age):  # noqa: F811
    e = escena
    disponible = version(e, "disponible", b"%PDF-1.7 disponible")
    cuarentena = version(e, "en_cuarentena", b"%PDF-1.7 en cuarentena")
    rechazada = version(e, "rechazado", b"%PDF-1.7 rechazada")

    assert replica_documentos.replicar(e["cfg"], e["origen"]) == {
        "versiones": 1, "copiadas": 1, "ya_estaban": 0, "pendientes": 0,
    }  # fmt: skip
    clave = replica_documentos.clave_replica(disponible["clave"])
    assert replica(e) == [clave]  # control negativo: ni la de cuarentena ni la rechazada
    assert replica_documentos.clave_replica(cuarentena["clave"]) not in replica(e)
    assert replica_documentos.clave_replica(rechazada["clave"]) not in replica(e)
    cifrado = e["s3"].get_object(Bucket=e["cfg"].bucket, Key=clave)["Body"].read()
    assert b"disponible" not in cifrado
    assert descifrar(par_age[0], cifrado) == disponible["contenido"]

    # No repite: la segunda corrida la encuentra con el mismo SHA-256 y no la sube otra vez.
    antes = e["s3"].head_object(Bucket=e["cfg"].bucket, Key=clave)["LastModified"]
    time.sleep(1.1)
    assert replica_documentos.replicar(e["cfg"], e["origen"])["ya_estaban"] == 1
    assert e["s3"].head_object(Bucket=e["cfg"].bucket, Key=clave)["LastModified"] == antes

    # Control: cuando la de cuarentena pasa a disponible, la corrida siguiente la copia.
    with e["motor"].begin() as c:
        c.execute(
            text("update nucleo.version_documento set estado = 'disponible',"
                 " antivirus = 'ClamAV 1.5.4', firmas_en = now(), analizada_en = now()"
                 " where id = :v"), {"v": cuarentena["id"]},
        )  # fmt: skip
    assert replica_documentos.replicar(e["cfg"], e["origen"])["copiadas"] == 1
    assert replica_documentos.clave_replica(cuarentena["clave"]) in replica(e)


@pytest.mark.db
def test_una_en_la_replica_con_otro_contenido_es_un_defecto(escena):
    e = escena
    v = version(e, "disponible", b"%PDF-1.7 original")
    e["s3"].put_object(Bucket=e["cfg"].bucket, Key=replica_documentos.clave_replica(v["clave"]),
                       Body=b"x", Metadata={"sha256-claro": "0" * 64})  # fmt: skip
    with pytest.raises(copias.ErrorCopia, match="con otro contenido"):
        replica_documentos.replicar(e["cfg"], e["origen"])


@pytest.mark.db
def test_un_binario_que_no_coincide_con_su_fila_no_se_copia(escena):
    e = escena
    v = version(e, "disponible", b"%PDF-1.7 original")
    e["s3"].put_object(Bucket=e["origen"].bucket, Key=v["clave"], Body=b"%PDF-1.7 cambiado")
    with e["motor"].begin() as c:  # la fila apunta a la versión nueva del objeto, que no coincide
        c.execute(text("update nucleo.version_documento set version_objeto = null where id = :v"),
                  {"v": v["id"]})  # fmt: skip
    with pytest.raises(copias.ErrorCopia, match="no coincide con su fila"):
        replica_documentos.replicar(e["cfg"], e["origen"])
    assert replica(e) == []


@pytest.mark.db
def test_una_corrida_larga_se_corta_y_sigue_en_la_siguiente(escena):
    e = escena
    for i in range(3):
        version(e, "disponible", f"%PDF-1.7 {i}".encode())
    primera = replica_documentos.replicar(e["cfg"], e["origen"], timedelta(0))
    assert (primera["copiadas"], primera["pendientes"]) == (1, 2)
    segunda = replica_documentos.replicar(e["cfg"], e["origen"])
    assert segunda == {"versiones": 3, "copiadas": 2, "ya_estaban": 1, "pendientes": 0}


# Control de tamaño --------------------------------------------------------------------


@pytest.mark.db
def test_el_control_de_tamano_avisa_caida_sobre_el_presupuesto(escena, kuma):  # noqa: F811
    e = escena
    for prefijo, tamano in (("base/a", 1000), ("wal/b", 200), ("wal-parcial/c", 30),
                            ("documentos/d", 4), ("diarias/e", 50000)):  # fmt: skip
        e["s3"].put_object(Bucket=e["cfg"].bucket, Key=prefijo, Body=b"x" * tamano)
    entorno = {replica_documentos.VARIABLE_AVISO_TAMANO: url_push(kuma)}

    # Por debajo (la suma es 1234; diarias/ no entra), sube.
    debajo = replica_documentos.controlar_tamano(
        e["cfg"], entorno | {replica_documentos.VARIABLE_PRESUPUESTO: "1234"}
    )
    assert (debajo["total"], debajo["excedido"], debajo["aviso"]) == (1234, False, "enviado")
    assert debajo["informados"]["diarias/"] == 50000
    # Control: un byte menos de presupuesto, y avisa caída.
    encima = replica_documentos.controlar_tamano(
        e["cfg"], entorno | {replica_documentos.VARIABLE_PRESUPUESTO: "1233"}
    )
    assert (encima["excedido"], encima["aviso"]) == (True, "enviado")
    estados = [parse_qs(urlsplit(p).query)["status"][0] for p in kuma.pedidos]
    assert estados == ["up", "down"]
    assert (
        "sobre el presupuesto: 1234 de 1233" in parse_qs(urlsplit(kuma.pedidos[1]).query)["msg"][0]
    )


@pytest.mark.parametrize("valor", [None, "", "0", "-5", "20GB"])
def test_sin_presupuesto_valido_el_control_falla(valor):
    entorno = {} if valor is None else {replica_documentos.VARIABLE_PRESUPUESTO: valor}
    with pytest.raises(copias.ErrorCopia, match="CEM_COPIAS_PRESUPUESTO_BYTES"):
        replica_documentos.presupuesto(entorno)
    assert replica_documentos.presupuesto({replica_documentos.VARIABLE_PRESUPUESTO: "1"}) == 1


# Planificador ------------------------------------------------------------------------


def test_las_tareas_diarias_no_chocan_con_las_otras():
    import app.plataforma.tareas  # noqa: F401
    from app.plataforma.planificador import TAREAS

    horas = {t.tipo: t.desfase for t in TAREAS if t.periodo == timedelta(days=1)}
    assert horas["plataforma.replicar_documentos"] == timedelta(hours=5, minutes=15)
    assert horas["plataforma.controlar_tamano_copias"] == timedelta(hours=5, minutes=45)
    # Ninguna otra diaria a menos de 15 minutos de la réplica ni del control de tamaño.
    for tipo in ("plataforma.replicar_documentos", "plataforma.controlar_tamano_copias"):
        otras = [d for t, d in horas.items() if t != tipo]
        assert all(abs(d - horas[tipo]) >= timedelta(minutes=15) for d in otras), tipo
    assert horas["plataforma.copia_diaria"] < horas["plataforma.replicar_documentos"]
