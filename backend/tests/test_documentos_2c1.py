"""Documentos: carga a cuarentena y análisis (H2c 2c-1; RF24, primera parte; migración 0035).

Diseño: `doc/diseno/H2c_Diseno.md` del repositorio CEM, §2, §3 y §7. Todo contra servicios
reales: PostgreSQL, el almacén S3 de pruebas (con y sin versiones) y ClamAV. Cada negación lleva
su control: el mismo pedido de quien sí puede, o el mismo archivo sin lo que lo hace inválido,
pasa.
"""

import io
import os
import time
import zlib
from datetime import timedelta
from uuid import uuid4

import boto3
import pytest
from alembic import command
from botocore.config import Config as ConfigBotocore
from botocore.exceptions import EndpointConnectionError
from cem_contracts.documentos import TOPE_BYTES
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, TextStringObject
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.consorcios import documentos_analisis
from app.consorcios.documentos import TRABAJO_VERIFICAR
from app.db import transaccion
from app.plataforma import almacen, cola
from app.plataforma.trabajos import Trabajador
from tests.conftest import clave_idempotencia
from tests.test_contexto_mandatos import cliente
from tests.test_mandatos import otra_persona
from tests.test_mandatos_0012 import alta
from tests.test_modo_operacion import cambiar, consorcio_con_persona, decidir_equipo
from tests.test_vinculo_0014 import base_aparte  # noqa: F401  (`base_aparte` es un fixture)

pytestmark = [pytest.mark.db, pytest.mark.redis]

ESPERA_ALMACEN_S = 10
# EICAR armado en ejecución: el texto entero en un archivo del repositorio lo borraría el
# antivirus de la PC que lo clona.
EICAR = ("X5O!P%@AP[4\\PZX54(P^)7CC)7}$" + "EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*").encode()
PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
    b"\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xcf\xc0\xf0\x1f\x00\x05\x00\x01\xff\x89\x99"
    b"=\x1d\x00\x00\x00\x00IEND\xaeB`\x82"
)
JPEG = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xdb\x00C\x00"
    + bytes(range(1, 65)) + b"\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4\x00"
    b"\x14\x00\x01" + bytes(15) + b"\x00\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xd2\xcf \xff\xd9"
)  # fmt: skip


def pdf(js: str | None = None, clave: str | None = None, flujo: bytes | None = None) -> bytes:
    w = PdfWriter()
    pagina = w.add_blank_page(width=200, height=200)
    if flujo is not None:
        # Un flujo sin comprimir como contenido de la página: ClamAV extrae cada flujo de un PDF
        # y lo analiza como un archivo aparte.
        contenido = DecodedStreamObject()
        contenido.set_data(flujo)
        pagina[NameObject("/Contents")] = w._add_object(contenido)
    if js:
        # Una acción de apertura con JavaScript, como la que arma un PDF malicioso.
        w._root_object[NameObject("/OpenAction")] = DictionaryObject(
            {NameObject("/S"): NameObject("/JavaScript"), NameObject("/JS"): TextStringObject(js)}
        )
    if clave:
        w.encrypt(clave)
    salida = io.BytesIO()
    w.write(salida)
    return salida.getvalue()


@pytest.fixture(autouse=True)
def firmas_de_la_imagen(monkeypatch):
    """Las pruebas corren sin freshclam, con las firmas que trae la imagen, que pueden tener
    días: el tope de 48 horas se prueba aparte (`test_con_firmas_viejas_no_se_aprueba_nada`)."""
    monkeypatch.setattr(documentos_analisis, "FIRMAS_VIGENTES", timedelta(days=3650))


@pytest.fixture
def escena(motor_migrate, motor_app):
    """Consorcio en Modo Consejo: un administrador externo (`documentos.cargar`) y un miembro del
    Consejo (sin `documentos.cargar`)."""
    cons, _, prop = consorcio_con_persona(motor_migrate)
    acta = decidir_equipo(motor_app, cons)
    cambiar(motor_app, cons, "consejo", acta)
    ae = otra_persona(motor_migrate, cons)
    alta(
        motor_app,
        cons,
        None,
        acta,
        persona=ae,
        tipo="administrador_externo",
        nombre="Estudio",
        cuit="20111111112",
        origen="soporte",
    )
    mc = otra_persona(motor_migrate, cons)
    alta(motor_app, cons, None, acta, persona=mc, tipo="miembro_consejo", origen="soporte")
    return {"cons": cons, "prop": prop, "ae": ae, "mc": mc}


def _s3():
    return boto3.client(
        "s3", endpoint_url=os.environ["CEM_TEST_S3_ENDPOINT"],
        region_name=os.environ["CEM_TEST_S3_REGION"],
        aws_access_key_id=os.environ["CEM_TEST_S3_CLAVE_ID"],
        aws_secret_access_key=os.environ["CEM_TEST_S3_CLAVE_SECRETA"],
        config=ConfigBotocore(s3={"addressing_style": "path"},
                              request_checksum_calculation="when_required",
                              response_checksum_validation="when_required"),
    )  # fmt: skip


def _bucket(monkeypatch, versiones: bool) -> str:
    bucket = f"cem-documentos-prueba-{uuid4().hex[:12]}"
    s3 = _s3()
    limite = time.monotonic() + ESPERA_ALMACEN_S
    while True:
        try:
            s3.create_bucket(Bucket=bucket)
            break
        except EndpointConnectionError:
            assert time.monotonic() < limite, "el almacén S3 de pruebas no responde"
            time.sleep(0.2)
    if versiones:
        s3.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={"Status": "Enabled"})
    for prefijo in (almacen.DOCUMENTOS, almacen.DOCUMENTOS_ANALISIS):
        for sufijo, variable in (
            ("ENDPOINT", "CEM_TEST_S3_ENDPOINT"),
            ("REGION", "CEM_TEST_S3_REGION"),
            ("CLAVE_ID", "CEM_TEST_S3_CLAVE_ID"),
            ("CLAVE_SECRETA", "CEM_TEST_S3_CLAVE_SECRETA"),
        ):
            monkeypatch.setenv(f"{prefijo}_{sufijo}", os.environ[variable])
    monkeypatch.setenv(almacen.VARIABLE_BUCKET_DOCUMENTOS, bucket)
    monkeypatch.setenv("CEM_ANTIVIRUS_SERVIDOR", os.environ["CEM_TEST_ANTIVIRUS_SERVIDOR"])
    monkeypatch.setenv("CEM_ANTIVIRUS_PUERTO", os.environ["CEM_TEST_ANTIVIRUS_PUERTO"])
    return bucket


@pytest.fixture
def bucket(monkeypatch, motor_migrate):
    with motor_migrate.begin() as c:
        c.execute(text("truncate plataforma.jobs"))
    return _bucket(monkeypatch, versiones=False)


@pytest.fixture
def bucket_con_versiones(monkeypatch, motor_migrate):
    with motor_migrate.begin() as c:
        c.execute(text("truncate plataforma.jobs"))
    return _bucket(monkeypatch, versiones=True)


def url(cons, resto=""):
    return f"/api/v1/consorcios/{cons}/documentos{resto}"


def crear(persona, cons, **datos):
    cuerpo = {
        "categoria": "actas_y_decisiones",
        "titulo": "Acta de asamblea",
        "fecha": "2026-10-20",
    }
    with cliente(persona) as c:
        return c.post(url(cons), json=cuerpo | datos, headers=clave_idempotencia())


def subir(persona, cons, doc, contenido: bytes, tipo: str, clave: dict | None = None):
    with cliente(persona) as c:
        return c.post(url(cons, f"/{doc}/versiones"), content=contenido,
                      headers={"Content-Type": tipo} | (clave or clave_idempotencia()))  # fmt: skip


def documento(e) -> str:
    r = crear(e["ae"], e["cons"])
    assert r.status_code == 201, r.text
    return r.json()["id"]


def version(motor_migrate, v):
    with motor_migrate.connect() as c:
        return c.execute(
            text("select estado, motivo_rechazo, clave_objeto, version_objeto, antivirus, firmas_en"
                 " from nucleo.version_documento where id = :v"), {"v": v},
        ).mappings().one()  # fmt: skip


def trabajos(motor_migrate):
    with motor_migrate.connect() as c:
        return c.execute(
            text("select estado::text, intentos, error from plataforma.jobs"
                 " where tipo = :t"), {"t": TRABAJO_VERIFICAR},
        ).all()  # fmt: skip


def analizar(motor_app) -> int:
    return Trabajador(motor_app).procesar_lote()


def existe(bucket, clave) -> bool:
    return _s3().list_objects_v2(Bucket=bucket, Prefix=clave).get("KeyCount", 0) > 0


# Carga --------------------------------------------------------------------------------


def test_un_pdf_limpio_entra_en_cuarentena_y_el_analisis_lo_aprueba(
    escena, bucket, motor_app, motor_migrate
):
    e = escena
    doc = documento(e)
    contenido = pdf()
    r = subir(e["ae"], e["cons"], doc, contenido, "application/pdf")
    assert r.status_code == 201, r.text
    v = r.json()
    assert (v["estado"], v["tipo"], v["tamano"]) == ("en_cuarentena", "application/pdf",
                                                     len(contenido))  # fmt: skip
    fila = version(motor_migrate, v["id"])
    assert fila["clave_objeto"] == f"cem/{e['cons']}/{doc}/{v['id']}"
    assert existe(bucket, fila["clave_objeto"])
    assert fila["version_objeto"] is None  # bucket sin versiones, como staging
    assert [t[0] for t in trabajos(motor_migrate)] == ["pendiente"]
    assert analizar(motor_app) == 1
    fila = version(motor_migrate, v["id"])
    assert fila["estado"] == "disponible", trabajos(motor_migrate)
    assert fila["antivirus"].startswith("ClamAV ") and fila["firmas_en"] is not None
    assert [t[0] for t in trabajos(motor_migrate)] == ["hecho"]


@pytest.mark.parametrize(
    "contenido,tipo", [(PNG, "image/png"), (JPEG, "image/jpeg")], ids=["png", "jpeg"]
)
def test_un_png_y_un_jpeg_pasan(escena, bucket, motor_app, motor_migrate, contenido, tipo):
    e = escena
    r = subir(e["ae"], e["cons"], documento(e), contenido, tipo)
    assert r.status_code == 201, r.text
    assert analizar(motor_app) == 1
    assert version(motor_migrate, r.json()["id"])["estado"] == "disponible"


def test_sin_documentos_cargar_no_se_crea_ni_se_sube(escena, bucket):
    e = escena
    assert crear(e["mc"], e["cons"]).status_code == 403
    assert crear(e["prop"], e["cons"]).status_code == 403
    doc = documento(e)  # control: el administrador externo sí
    assert subir(e["mc"], e["cons"], doc, pdf(), "application/pdf").status_code == 403
    assert subir(e["ae"], e["cons"], doc, pdf(), "application/pdf").status_code == 201


@pytest.mark.parametrize(
    "contenido,tipo",
    [
        (b"MZ\x90\x00" + bytes(200), "application/pdf"),  # un ejecutable con nombre de PDF
        (b"PK\x03\x04" + bytes(200), "application/pdf"),  # un ZIP (y un DOCX, que es un ZIP)
        (PNG, "application/pdf"),  # un PNG declarado como PDF
        (pdf(), "application/zip"),  # un tipo declarado que no se admite
        (b"", "application/pdf"),
    ],
    ids=["ejecutable", "zip", "png-como-pdf", "tipo-zip", "vacio"],
)
def test_otro_tipo_da_415_sin_escribir_nada(escena, bucket, motor_migrate, contenido, tipo):
    e = escena
    doc = documento(e)
    r = subir(e["ae"], e["cons"], doc, contenido, tipo)
    assert r.status_code == 415, r.text
    assert r.json()["codigo"] == "tipo_no_admitido"
    assert not existe(bucket, f"cem/{e['cons']}/{doc}/")
    assert trabajos(motor_migrate) == []


def test_veinte_megas_pasan_y_un_byte_mas_da_413(escena, bucket, motor_app, motor_migrate):
    e = escena
    doc = documento(e)
    base = pdf()
    exacto = base + b"%" * (TOPE_BYTES - len(base))
    r = subir(e["ae"], e["cons"], doc, exacto + b"%", "application/pdf")
    assert r.status_code == 413, r.text
    assert r.json()["codigo"] == "archivo_demasiado_grande"
    assert not existe(bucket, f"cem/{e['cons']}/{doc}/")
    # Control: exactamente 20 MB se suben y el análisis (ClamAV incluido) los aprueba.
    r = subir(e["ae"], e["cons"], doc, exacto, "application/pdf")
    assert r.status_code == 201, r.text
    assert r.json()["tamano"] == TOPE_BYTES
    assert analizar(motor_app) == 1
    assert version(motor_migrate, r.json()["id"])["estado"] == "disponible", trabajos(motor_migrate)


def test_un_documento_de_otro_consorcio_da_404(escena, bucket, motor_migrate, motor_app):
    e = escena
    otro, _, prop_otro = consorcio_con_persona(motor_migrate)
    doc = documento(e)
    # Desde otro consorcio, su documento no existe: el administrador no tiene ámbito allá, y en
    # la ruta propia un documento ajeno o inexistente da 404 (PA15).
    assert subir(e["ae"], otro, doc, pdf(), "application/pdf").status_code in (403, 404)
    r = subir(e["ae"], e["cons"], str(uuid4()), pdf(), "application/pdf")
    assert r.status_code == 404, r.text


def test_la_carga_queda_auditada_sin_el_titulo(escena, bucket, motor_migrate):
    e = escena
    doc = documento(e)
    subir(e["ae"], e["cons"], doc, pdf(), "application/pdf")
    with motor_migrate.connect() as c:
        filas = c.execute(
            text("select accion, actor_persona_id, origen, despues from auditoria.eventos"
                 " where consorcio_id = :c and accion like 'consorcio.documento.%'"
                 " order by secuencia"), {"c": e["cons"]},
        ).all()  # fmt: skip
    assert [(f.accion, f.actor_persona_id, f.origen) for f in filas] == [
        ("consorcio.documento.crear", e["ae"], "api"),
        ("consorcio.documento.cargar_version", e["ae"], "api"),
    ]
    assert set(filas[0].despues) == {"categoria", "fecha"}
    assert set(filas[1].despues) == {"tipo", "tamano"}


def test_un_titulo_con_telefono_o_correo_se_rechaza(escena, bucket):
    e = escena
    assert crear(e["ae"], e["cons"], titulo="Llamar al 11 4000 0000").status_code == 422
    assert crear(e["ae"], e["cons"], titulo="Escribir a a@b.com").status_code == 422
    assert crear(e["ae"], e["cons"], titulo="Acta del 20-10").status_code == 201  # control


def test_reintentar_con_la_misma_clave_no_sube_otra_version(escena, bucket, motor_migrate):
    e = escena
    doc = documento(e)
    clave = clave_idempotencia()
    primera = subir(e["ae"], e["cons"], doc, pdf(), "application/pdf", clave)
    assert primera.status_code == 201
    repetida = subir(e["ae"], e["cons"], doc, pdf(), "application/pdf", clave)
    assert repetida.status_code == 201 and repetida.json() == primera.json()
    assert repetida.headers["idempotent-replayed"] == "true"
    # La misma clave con otro cuerpo es un error del cliente.
    otra = subir(e["ae"], e["cons"], doc, pdf() + b"%otro" + bytes([10]), "application/pdf", clave)
    assert otra.status_code == 422, otra.text
    with motor_migrate.connect() as c:
        n = c.execute(text("select count(*) from nucleo.version_documento where documento_id = :d"),
                      {"d": doc}).scalar_one()  # fmt: skip
    assert n == 1


# Análisis: rechazos -----------------------------------------------------------------


@pytest.mark.parametrize(
    "contenido,tipo,motivo",
    [
        (EICAR, "application/pdf", None),  # se arma abajo: EICAR solo no es un PDF
        (pdf(js="app.alert(1)"), "application/pdf", "contenido_activo"),
        (pdf(clave="secreta"), "application/pdf", "pdf_cifrado"),
    ],
    ids=["eicar", "javascript", "cifrado"],
)
def test_lo_detectado_queda_rechazado_sin_binario(
    escena, bucket_con_versiones, motor_app, motor_migrate, contenido, tipo, motivo
):
    e = escena
    if motivo is None:
        # EICAR en un flujo de un PDF válido: pasa el tipo y el analizador de PDF, y lo detecta
        # ClamAV. (El archivo EICAR solo, que empieza con `X5O`, ya da 415 por su tipo.)
        contenido, motivo = pdf(flujo=EICAR), "virus"
    r = subir(e["ae"], e["cons"], documento(e), contenido, tipo)
    assert r.status_code == 201, r.text
    fila = version(motor_migrate, r.json()["id"])
    assert fila["version_objeto"]  # bucket con versiones, como producción
    assert analizar(motor_app) == 1
    fila = version(motor_migrate, r.json()["id"])
    assert (fila["estado"], fila["motivo_rechazo"]) == ("rechazado", motivo), trabajos(
        motor_migrate
    )
    # El rechazo encoló su borrado en la misma transacción; el objeto sigue hasta que corre.
    assert existe(bucket_con_versiones, fila["clave_objeto"])
    assert analizar(motor_app) == 1
    # Borrado por `VersionId`: no queda ninguna versión del objeto, tampoco una marca.
    versiones = _s3().list_object_versions(Bucket=bucket_con_versiones, Prefix=fila["clave_objeto"])
    assert versiones.get("Versions", []) == [] and versiones.get("DeleteMarkers", []) == []
    with motor_migrate.connect() as c:
        ev = c.execute(
            text("select actor_persona_id, origen, despues from auditoria.eventos"
                 " where consorcio_id = :c and accion = 'consorcio.documento.rechazar_version'"),
            {"c": e["cons"]},
        ).one()  # fmt: skip
    assert (ev.actor_persona_id, ev.origen, ev.despues) == (None, "trabajos", {"motivo": motivo})


def test_un_limpio_en_bucket_con_versiones_se_conserva(
    escena, bucket_con_versiones, motor_app, motor_migrate
):
    """Control del anterior: el mismo camino con un PDF limpio deja el objeto."""
    e = escena
    r = subir(e["ae"], e["cons"], documento(e), pdf(), "application/pdf")
    assert analizar(motor_app) == 1
    fila = version(motor_migrate, r.json()["id"])
    assert fila["estado"] == "disponible"
    versiones = _s3().list_object_versions(Bucket=bucket_con_versiones, Prefix=fila["clave_objeto"])
    assert [v["VersionId"] for v in versiones["Versions"]] == [fila["version_objeto"]]


def test_un_pdf_ilegible_queda_rechazado(escena, bucket, motor_app, motor_migrate):
    e = escena
    r = subir(e["ae"], e["cons"], documento(e), b"%PDF-1.7\n" + bytes(range(256)) * 4,
              "application/pdf")  # fmt: skip
    assert analizar(motor_app) == 1
    fila = version(motor_migrate, r.json()["id"])
    assert (fila["estado"], fila["motivo_rechazo"]) == ("rechazado", "pdf_mal_formado")


# Análisis que no se completa ------------------------------------------------------


def test_con_clamd_caido_sigue_en_cuarentena_y_se_reintenta(
    escena, bucket, motor_app, motor_migrate, monkeypatch
):
    e = escena
    r = subir(e["ae"], e["cons"], documento(e), pdf(), "application/pdf")
    monkeypatch.setenv("CEM_ANTIVIRUS_PUERTO", "1")  # nadie escucha
    assert analizar(motor_app) == 1
    assert version(motor_migrate, r.json()["id"])["estado"] == "en_cuarentena"
    [(estado, intentos, error)] = trabajos(motor_migrate)
    assert (estado, intentos) == ("pendiente", 1) and "clamd no responde" in error
    # Control: con clamd de vuelta, el reintento la aprueba.
    monkeypatch.setenv("CEM_ANTIVIRUS_PUERTO", os.environ["CEM_TEST_ANTIVIRUS_PUERTO"])
    with motor_migrate.begin() as c:
        c.execute(text("update plataforma.jobs set disponible_desde = now()"))
    assert analizar(motor_app) == 1
    assert version(motor_migrate, r.json()["id"])["estado"] == "disponible"


def test_con_firmas_viejas_no_se_aprueba_nada(
    escena, bucket, motor_app, motor_migrate, monkeypatch
):
    e = escena
    r = subir(e["ae"], e["cons"], documento(e), pdf(), "application/pdf")
    # Con un margen de cero, cualquier firma es vieja.
    monkeypatch.setattr(documentos_analisis, "FIRMAS_VIGENTES", timedelta(0))
    assert analizar(motor_app) == 1
    assert version(motor_migrate, r.json()["id"])["estado"] == "en_cuarentena"
    [(estado, _, error)] = trabajos(motor_migrate)
    assert estado == "pendiente" and "no se aprueba nada" in error
    monkeypatch.setattr(documentos_analisis, "FIRMAS_VIGENTES", timedelta(days=3650))  # control
    with motor_migrate.begin() as c:
        c.execute(text("update plataforma.jobs set disponible_desde = now()"))
    assert analizar(motor_app) == 1
    assert version(motor_migrate, r.json()["id"])["estado"] == "disponible"


# La base -------------------------------------------------------------------------------


def test_cem_app_no_lee_las_tablas_y_otro_consorcio_no_ve_la_version(
    escena, bucket, motor_app, motor_migrate
):
    e = escena
    r = subir(e["ae"], e["cons"], documento(e), pdf(), "application/pdf")
    v = r.json()["id"]
    for tabla in ("nucleo.documento", "nucleo.version_documento"):
        with pytest.raises(DBAPIError, match="permission denied"):
            with transaccion(motor_app, persona_id=None, consorcio_id=e["cons"]) as c:
                c.execute(text(f"select count(*) from {tabla}"))
    otro = consorcio_con_persona(motor_migrate)[0]
    with transaccion(motor_app, persona_id=None, consorcio_id=otro) as c:
        assert c.execute(text("select * from nucleo.version_para_analizar(:v)"),
                         {"v": v}).all() == []  # fmt: skip
    with transaccion(motor_app, persona_id=None, consorcio_id=e["cons"]) as c:  # control
        assert c.execute(text("select estado from nucleo.version_para_analizar(:v)"),
                         {"v": v}).scalar_one() == "en_cuarentena"  # fmt: skip


def test_las_funciones_del_analisis_se_niegan_con_persona_en_la_sesion(escena, bucket, motor_app):
    e = escena
    v = subir(e["ae"], e["cons"], documento(e), pdf(), "application/pdf").json()["id"]
    for sql in (
        "select * from nucleo.version_para_analizar(:v)",
        "select nucleo.resolver_version(:v, null, 'x', now())",
        "select nucleo.versiones_demoradas('1 hour')",
    ):
        with pytest.raises(DBAPIError, match="solo un trabajo"):
            with transaccion(motor_app, persona_id=e["ae"], consorcio_id=e["cons"]) as c:
                c.execute(text(sql), {"v": v})
    with transaccion(motor_app, persona_id=None, consorcio_id=None) as c:  # control
        assert c.execute(text("select nucleo.versiones_demoradas('1 hour')")).scalar_one() == 0


def test_resolver_dos_veces_no_cambia_lo_resuelto(escena, bucket, motor_app, motor_migrate):
    e = escena
    v = subir(e["ae"], e["cons"], documento(e), pdf(), "application/pdf").json()["id"]
    assert analizar(motor_app) == 1
    with transaccion(motor_app, persona_id=None, consorcio_id=e["cons"]) as c:
        assert c.execute(text("select nucleo.resolver_version(:v, 'virus', null, null)"),
                         {"v": v}).scalar_one() is False  # fmt: skip
    assert version(motor_migrate, v)["estado"] == "disponible"


def test_vigilar_avisa_las_demoradas_y_al_resolverse_vuelve_a_cero(
    escena, bucket, motor_app, motor_migrate, monkeypatch
):
    e = escena
    v = subir(e["ae"], e["cons"], documento(e), pdf(), "application/pdf").json()["id"]
    monkeypatch.delenv(documentos_analisis.VARIABLE_AVISO, raising=False)

    def vigilar():
        with transaccion(motor_app, persona_id=None, consorcio_id=None) as c:
            return documentos_analisis.vigilar_cuarentena(c, None)

    assert vigilar()["demoradas"] == 0  # recién subida: todavía no es una demora
    with motor_migrate.begin() as c:
        c.execute(text("update nucleo.version_documento set subida_en = now() - interval '3 hours'"
                       " where id = :v"), {"v": v})  # fmt: skip
    r = vigilar()
    assert r["demoradas"] == 1 and r["problemas"] and r["aviso"] == "sin CEM_DOCUMENTOS_AVISO_URL"
    assert analizar(motor_app) == 1  # control: resuelta, deja de contar
    assert vigilar() == {"demoradas": 0, "problemas": [],
                         "aviso": "sin CEM_DOCUMENTOS_AVISO_URL"}  # fmt: skip


def test_0035_ida_y_vuelta(base_aparte):  # noqa: F811
    cfg, motor = base_aparte
    command.upgrade(cfg, "0035")
    with motor.connect() as c:
        privilegios = c.execute(
            text(
                "select has_function_privilege('cem_app', :f, 'execute'),"
                " has_function_privilege('public', :f, 'execute'),"
                " has_table_privilege('cem_app', 'nucleo.version_documento', 'select')"
            ),
            {"f": "nucleo.nueva_version(uuid, text, integer, text, uuid, text)"},
        ).one()
    assert tuple(privilegios) == (True, False, False)
    command.downgrade(cfg, "0034")
    with motor.connect() as c:
        assert c.execute(text("select to_regclass('nucleo.documento')")).scalar_one() is None
        assert (
            c.execute(
                text(
                    "select count(*) from auditoria.proyeccion"
                    " where accion like 'consorcio.documento.%'"
                )
            ).scalar_one()
            == 0
        )
    command.upgrade(cfg, "0035")  # control: vuelve a subir limpio
    with motor.connect() as c:
        assert c.execute(text("select to_regclass('nucleo.documento')")).scalar_one() is not None


# Casos de la revisión de #127 ------------------------------------------------------------


def _pdf_con_flujo_de_objetos(catalogo: bytes, relleno: int = 0) -> bytes:
    """Un PDF cuyo catálogo y árbol de páginas están dentro de un flujo de objetos comprimido
    (con una tabla de referencias en flujo), como los que arman muchos programas."""
    paginas = b"<< /Type /Pages /Kids [] /Count 0 >>"
    cabeza = (b"1 0 3 %d " % len(catalogo)).ljust(12)
    datos = zlib.compress(cabeza + catalogo + paginas + b" " * relleno, 9)
    salida = io.BytesIO()
    salida.write(b"%PDF-1.7\n")
    pos4 = salida.tell()
    salida.write(b"4 0 obj\n<< /Type /ObjStm /N 2 /First 12 /Filter /FlateDecode /Length %d >>\n"
                 b"stream\n" % len(datos))  # fmt: skip
    salida.write(datos + b"\nendstream\nendobj\n")
    pos5 = salida.tell()
    filas = b"".join([
        bytes([0, 0, 0, 0, 0, 0]), bytes([2, 0, 0, 0, 4, 0]), bytes([0, 0, 0, 0, 0, 0]),
        bytes([2, 0, 0, 0, 4, 1]), bytes([1]) + pos4.to_bytes(4, "big") + b"\x00",
        bytes([1]) + pos5.to_bytes(4, "big") + b"\x00",
    ])  # fmt: skip
    salida.write(b"5 0 obj\n<< /Type /XRef /Size 6 /W [1 4 1] /Root 1 0 R /Length %d >>\n"
                 b"stream\n" % len(filas))  # fmt: skip
    salida.write(filas + b"\nendstream\nendobj\nstartxref\n%d\n%%%%EOF\n" % pos5)
    return salida.getvalue()


def test_una_bomba_de_compresion_en_un_pdf_se_rechaza_como_ilegible():
    catalogo = b"<< /Type /Catalog /Pages 3 0 R >>"
    # Control: el mismo PDF sin la bomba se lee y no tiene nada activo; con JavaScript dentro del
    # flujo comprimido, el recorrido lo encuentra.
    assert documentos_analisis.motivo_pdf(_pdf_con_flujo_de_objetos(catalogo)) is None
    js = b"<< /Type /Catalog /Pages 3 0 R /OpenAction << /S /JavaScript /JS (x) >> >>"
    assert documentos_analisis.motivo_pdf(_pdf_con_flujo_de_objetos(js)) == "contenido_activo"
    bomba = _pdf_con_flujo_de_objetos(catalogo, relleno=300 * 1024 * 1024)
    assert len(bomba) < 2 * 1024 * 1024  # pocos KB comprimidos que se expanden a 300 MB
    assert documentos_analisis.motivo_pdf(bomba) == "pdf_mal_formado"


def _en_tramos(contenido: bytes, tramo: int = 1024 * 1024):
    for i in range(0, len(contenido), tramo):
        yield contenido[i : i + tramo]


def test_sin_content_length_el_contador_corta_al_pasar_el_tope(escena, bucket, motor_migrate):
    """Detrás de nginx sin búfer el cuerpo llega en tramos, sin `Content-Length`: el tope lo da el
    contador, no la cabecera."""
    e = escena
    doc = documento(e)
    base = pdf()
    exceso = base + b"%" * (TOPE_BYTES + 1 - len(base))
    with cliente(e["ae"]) as c:
        r = c.post(url(e["cons"], f"/{doc}/versiones"), content=_en_tramos(exceso),
                   headers={"Content-Type": "application/pdf"} | clave_idempotencia())  # fmt: skip
        assert "content-length" not in {k.lower() for k in r.request.headers}
    assert r.status_code == 413, r.text
    assert not existe(bucket, f"cem/{e['cons']}/{doc}/")
    with cliente(e["ae"]) as c:  # control: en tramos y dentro del tope, pasa
        r = c.post(url(e["cons"], f"/{doc}/versiones"), content=_en_tramos(base, 64),
                   headers={"Content-Type": "application/pdf"} | clave_idempotencia())  # fmt: skip
    assert r.status_code == 201, r.text
    assert r.json()["tamano"] == len(base)


def test_si_el_objeto_no_esta_el_trabajo_falla_y_la_version_sigue_en_cuarentena(
    escena, bucket, motor_app, motor_migrate
):
    """Un objeto ausente (o una clave que no llega a él) no se resuelve en silencio: el trabajo
    falla con el error del almacén y se reintenta; Kuma avisa si se demora."""
    e = escena
    v = subir(e["ae"], e["cons"], documento(e), pdf(), "application/pdf").json()["id"]
    _s3().delete_object(Bucket=bucket, Key=version(motor_migrate, v)["clave_objeto"])
    assert analizar(motor_app) == 1
    assert version(motor_migrate, v)["estado"] == "en_cuarentena"
    [(estado, intentos, error)] = trabajos(motor_migrate)
    assert (estado, intentos) == ("pendiente", 1) and "NoSuchKey" in error


def test_un_archivo_que_no_coincide_con_lo_cargado_queda_rechazado(escena, bucket, motor_app,
                                                                   motor_migrate):  # fmt: skip
    e = escena
    v = subir(e["ae"], e["cons"], documento(e), pdf(), "application/pdf").json()["id"]
    # Otro contenido en la misma clave (con el usuario raíz del almacén de pruebas): la suma ya no
    # es la que guardó la carga.
    _s3().put_object(Bucket=bucket, Key=version(motor_migrate, v)["clave_objeto"],
                     Body=pdf(js="x"))  # fmt: skip
    assert analizar(motor_app) == 1
    fila = version(motor_migrate, v)
    assert (fila["estado"], fila["motivo_rechazo"]) == ("rechazado", "no_coincide")
    assert analizar(motor_app) == 1  # el borrado, otro trabajo
    assert not existe(bucket, fila["clave_objeto"])


def _jobs(motor_migrate, tipo):
    with motor_migrate.connect() as c:
        return c.execute(
            text("select estado::text, maximo_intentos, resultado, error from plataforma.jobs"
                 " where tipo = :t"), {"t": tipo},
        ).all()  # fmt: skip


def test_el_analisis_se_encola_con_sus_diez_intentos(escena, bucket, motor_migrate):
    """El límite viaja con el trabajo: el proceso de la API no carga el registro de los trabajos."""
    e = escena
    subir(e["ae"], e["cons"], documento(e), pdf(), "application/pdf")
    [(estado, maximo, _, _)] = _jobs(motor_migrate, TRABAJO_VERIFICAR)
    assert (estado, maximo) == ("pendiente", 10)


def test_si_el_borrado_ya_se_habia_hecho_el_trabajo_termina(escena, bucket, motor_app,
                                                            motor_migrate):  # fmt: skip
    """Un borrado cuya respuesta se perdió: el rechazo ya está confirmado y el objeto ya no está."""
    e = escena
    v = subir(e["ae"], e["cons"], documento(e), pdf(js="x"), "application/pdf").json()["id"]
    assert analizar(motor_app) == 1
    _s3().delete_object(Bucket=bucket, Key=version(motor_migrate, v)["clave_objeto"])
    assert analizar(motor_app) == 1
    [(estado, _, resultado, _)] = _jobs(motor_migrate, documentos_analisis.TRABAJO_BORRAR)
    # Sin versiones, S3 responde 204 aunque no esté; con versiones, `NoSuchVersion`. Las dos
    # terminan bien.
    assert estado == "hecho" and resultado["borrado"] in ("hecho", "ya no estaba")


def test_el_borrado_nunca_toca_una_version_que_no_esta_rechazada(escena, bucket, motor_app,
                                                                 motor_migrate):  # fmt: skip
    e = escena
    v = subir(e["ae"], e["cons"], documento(e), pdf(), "application/pdf").json()["id"]
    assert analizar(motor_app) == 1  # disponible
    with transaccion(motor_app, persona_id=None, consorcio_id=e["cons"]) as c:
        cola.encolar(c, documentos_analisis.TRABAJO_BORRAR, carga={"version": v},
                     consorcio_id=e["cons"])  # fmt: skip
    assert analizar(motor_app) == 1
    [(estado, _, _, error)] = _jobs(motor_migrate, documentos_analisis.TRABAJO_BORRAR)
    assert estado == "muerto" and "no está rechazada" in error
    assert existe(bucket, version(motor_migrate, v)["clave_objeto"])  # el objeto sigue


def test_la_limpieza_borra_solo_los_objetos_sin_fila(escena, bucket_con_versiones, motor_app,
                                                     motor_migrate, monkeypatch):  # fmt: skip
    e = escena
    v = subir(e["ae"], e["cons"], documento(e), pdf(), "application/pdf").json()["id"]
    registrada = version(motor_migrate, v)["clave_objeto"]
    huerfana = f"cem/{e['cons']}/{uuid4()}/{uuid4()}"
    s3 = _s3()
    s3.put_object(Bucket=bucket_con_versiones, Key=huerfana, Body=pdf())
    s3.put_object(Bucket=bucket_con_versiones, Key=huerfana, Body=pdf(js="x"))  # dos versiones
    fuera = "otro/prefijo"
    s3.put_object(Bucket=bucket_con_versiones, Key=fuera, Body=b"x")
    monkeypatch.setattr(documentos_analisis, "ANTIGUEDAD_HUERFANO", timedelta(0))
    with transaccion(motor_app, persona_id=None, consorcio_id=None) as c:
        r = documentos_analisis.limpiar_huerfanos(c, None)
    assert r["borradas"] == 1 and r["revisadas"] >= 2
    assert not existe(bucket_con_versiones, huerfana)
    # Borrado simple: las dos versiones siguen recuperables (quedan hasta la regla de 35 días).
    listado = s3.list_object_versions(Bucket=bucket_con_versiones, Prefix=huerfana)
    assert len(listado.get("Versions", [])) == 2
    # Control: la registrada y lo que está fuera de `cem/` siguen.
    assert existe(bucket_con_versiones, registrada) and existe(bucket_con_versiones, fuera)


def test_con_demasiados_huerfanos_la_limpieza_no_borra_nada(escena, bucket, motor_app,
                                                           monkeypatch):  # fmt: skip
    """Una base atrasada (una restauración) vería como huérfanos a documentos reales."""
    e = escena
    claves = [f"cem/{e['cons']}/{uuid4()}/{uuid4()}" for _ in range(3)]
    for clave in claves:
        _s3().put_object(Bucket=bucket, Key=clave, Body=pdf())
    monkeypatch.setattr(documentos_analisis, "ANTIGUEDAD_HUERFANO", timedelta(0))
    monkeypatch.setattr(documentos_analisis, "TOPE_HUERFANOS", 2)
    with pytest.raises(RuntimeError, match="No se borra nada"):
        with transaccion(motor_app, persona_id=None, consorcio_id=None) as c:
            documentos_analisis.limpiar_huerfanos(c, None)
    assert all(existe(bucket, clave) for clave in claves)
    monkeypatch.setattr(documentos_analisis, "TOPE_HUERFANOS", 3)  # control: dentro del tope
    with transaccion(motor_app, persona_id=None, consorcio_id=None) as c:
        assert documentos_analisis.limpiar_huerfanos(c, None)["borradas"] == 3
    assert not any(existe(bucket, clave) for clave in claves)


def test_la_limpieza_respeta_la_antiguedad(escena, bucket, motor_app, motor_migrate):
    """Con la antigüedad real (un día), un objeto sin fila recién subido (una carga en curso) no
    se toca."""
    e = escena
    huerfana = f"cem/{e['cons']}/{uuid4()}/{uuid4()}"
    _s3().put_object(Bucket=bucket, Key=huerfana, Body=pdf())
    with transaccion(motor_app, persona_id=None, consorcio_id=None) as c:
        r = documentos_analisis.limpiar_huerfanos(c, None)
    assert r == {"revisadas": 0, "borradas": 0}
    assert existe(bucket, huerfana)
