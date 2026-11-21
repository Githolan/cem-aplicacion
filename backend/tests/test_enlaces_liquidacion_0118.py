"""Los enlaces de la liquidación y el archivo traído de uno, H5-5 (`0118`; diseño H5 §5; SSRF).

Contra servicios reales: PostgreSQL, el almacén S3 de pruebas y ClamAV. El archivo del enlace lo
sirve la prueba por HTTPS desde la misma máquina (`test_descarga.py`): la ruta usa la descarga de
la aplicación con el resolver, el control de direcciones, el certificado y el puerto de la prueba.
Datos SINTETICOS / NO REAL.
"""

import functools

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.consorcios.documentos import tipo_real
from app.liquidacion import descarga, importacion
from tests.conftest import clave_idempotencia
from tests.test_descarga import NOMBRE, PDF, resolver, servidor, solo_local, tls
from tests.test_documentos_2c1 import analizar, pdf
from tests.test_enlaces import con_anotacion
from tests.test_importacion_liquidacion_0113 import (
    CATEGORIA,
    bucket,
    firmas_de_la_imagen,
    importar,
    pdf_de,
    ver,
)
from tests.test_importacion_liquidacion_0113 import (
    escena as escena,  # noqa: F401  (es un fixture)
)
from tests.test_liquidacion_imagen_0117 import subida
from tests.test_mis_expensas import liquidacion
from tests.test_rutas_egresos import cliente
from tests.test_vinculo_0014 import base_aparte  # noqa: F401  (es un fixture)

pytestmark = [pytest.mark.db, pytest.mark.redis]
# ruff: noqa: F811

__all__ = ["bucket", "firmas_de_la_imagen", "servidor", "tls"]


def importada_con(e, motor_app, url: str) -> dict:
    """Una liquidación con el formato conocido y un enlace anotado, ya leída."""
    doc, v = subida(e, motor_app, con_anotacion(pdf_de(liquidacion()), url), "application/pdf")
    r = importar(e, doc, v)
    assert r.status_code == 201, r.text
    assert analizar(motor_app) == 1
    i = ver(e, r.json()["id"]).json()
    assert i["estado"] == "para_confirmar", i
    return i


def traer(e, i: str, numero: int, quien=None, clave: dict[str, str] | None = None):
    with cliente(quien or e["mc"], e["sesion"] if quien is None else None) as c:
        return c.post(
            f"/api/v1/consorcios/{e['cons']}/liquidaciones-importadas/{i}/enlaces/{numero}/descarga",
            headers=clave or clave_idempotencia(),
        )


@pytest.fixture
def al_servidor_de_prueba(monkeypatch, servidor, tls):
    """La ruta descarga con el código de la aplicación, hacia el servidor local de la prueba."""
    monkeypatch.setattr(importacion.descarga, "traer", functools.partial(
        descarga.traer, resolver=resolver, publica=solo_local, contexto_tls=tls[0],
        puerto=servidor.server_address[1],
    ))  # fmt: skip
    return servidor


def documentos_de(motor_migrate, cons) -> int:
    with motor_migrate.connect() as c:
        return c.execute(
            text("select count(*) from nucleo.documento"
                 " where consorcio_id = :c and categoria = :k"),
            {"c": cons, "k": CATEGORIA},
        ).scalar_one()  # fmt: skip


def test_la_lectura_anota_los_enlaces_sin_seguirlos(escena, bucket, motor_app, motor_migrate,
                                                     servidor):  # fmt: skip
    url = f"https://{NOMBRE}:{servidor.server_address[1]}/liq.pdf"
    i = importada_con(escena, motor_app, url)
    assert i["enlaces"] == [{"orden": 1, "url": url, "origen": "anotacion", "pagina": 1}]
    assert servidor.pedidos == []  # anotar no es pedir


def test_traer_un_enlace_lo_guarda_como_documento_en_cuarentena_y_lo_audita(
    escena, bucket, motor_app, motor_migrate, al_servidor_de_prueba
):
    url = f"https://{NOMBRE}:{al_servidor_de_prueba.server_address[1]}/liq.pdf"
    traido = pdf()  # un PDF válido: el antivirus rechaza uno mal formado
    al_servidor_de_prueba.respuestas["/liq.pdf"] = (
        200,
        {"Content-Type": "application/pdf"},
        traido,
    )
    i = importada_con(escena, motor_app, url)
    antes = documentos_de(motor_migrate, escena["cons"])
    r = traer(escena, i["id"], 1)
    assert r.status_code == 201, r.text
    t = r.json()
    assert (t["tipo"], t["tamano"]) == ("application/pdf", len(traido))
    assert documentos_de(motor_migrate, escena["cons"]) == antes + 1
    with motor_migrate.connect() as c:
        estado = c.execute(text("select estado from nucleo.version_documento where id = :v"),
                           {"v": t["version_documento_id"]}).scalar_one()  # fmt: skip
        evento = c.execute(text(
            "select despues from auditoria.eventos"
            " where accion = 'consorcio.liquidacion.traer_enlace' and entidad_id = :i"
        ), {"i": i["id"]}).scalar_one()  # fmt: skip
    assert estado == "en_cuarentena"
    assert evento == {"enlace": 1, "url": url, "documento": t["documento_id"],
                      "version": t["version_documento_id"]}  # fmt: skip
    # Pasa por el antivirus como cualquier carga.
    assert analizar(motor_app) == 1
    with motor_migrate.connect() as c:
        assert c.execute(text("select estado from nucleo.version_documento where id = :v"),
                         {"v": t["version_documento_id"]}).scalar_one() == "disponible"  # fmt: skip


def test_un_reintento_recibe_lo_guardado_sin_volver_a_pedir_el_enlace(
    escena, bucket, motor_app, motor_migrate, al_servidor_de_prueba
):
    """Si la respuesta se perdió y el enlace ya no sirve, el reintento con la misma clave recibe el
    documento ya guardado, no un 422 (Codex)."""
    url = f"https://{NOMBRE}:{al_servidor_de_prueba.server_address[1]}/liq.pdf"
    al_servidor_de_prueba.respuestas["/liq.pdf"] = (200, {}, pdf())
    i = importada_con(escena, motor_app, url)
    clave = clave_idempotencia()
    primera = traer(escena, i["id"], 1, clave=clave)
    assert primera.status_code == 201, primera.text
    pedidos = len(al_servidor_de_prueba.pedidos)
    del al_servidor_de_prueba.respuestas["/liq.pdf"]  # el enlace venció: ahora da 404
    antes = documentos_de(motor_migrate, escena["cons"])
    otra = traer(escena, i["id"], 1, clave=clave)
    assert otra.status_code == 201, otra.text
    assert otra.json() == primera.json()
    assert otra.headers.get("Idempotent-Replayed") == "true"
    assert len(al_servidor_de_prueba.pedidos) == pedidos  # no se volvió a pedir
    assert documentos_de(motor_migrate, escena["cons"]) == antes
    # Control: con otra clave, el mismo enlace vencido sí se pide y no se trae.
    nueva = traer(escena, i["id"], 1)
    assert nueva.status_code == 422
    assert nueva.json()["mensaje"] == importacion.MOTIVOS_DESCARGA["respuesta_no_valida"]
    assert len(al_servidor_de_prueba.pedidos) == pedidos + 1


def test_un_enlace_sin_https_no_se_trae_ni_crea_nada(escena, bucket, motor_app, motor_migrate):
    i = importada_con(escena, motor_app, "http://comprobantes.prueba/liq.pdf")
    antes = documentos_de(motor_migrate, escena["cons"])
    r = traer(escena, i["id"], 1)
    assert r.status_code == 422 and r.json()["codigo"] == "enlace_no_traido"
    assert r.json()["mensaje"] == importacion.MOTIVOS_DESCARGA["esquema_no_admitido"]
    assert documentos_de(motor_migrate, escena["cons"]) == antes


def test_un_enlace_a_la_red_interna_no_se_pide(escena, bucket, motor_app, motor_migrate):
    """SSRF con el control de la aplicación: el nombre de un servicio de la red de Docker resuelve a
    una dirección privada y no se pide."""
    i = importada_con(escena, motor_app, "https://cem-postgres-pruebas/liq.pdf")
    r = traer(escena, i["id"], 1)
    assert r.status_code == 422
    assert r.json()["mensaje"] == importacion.MOTIVOS_DESCARGA["destino_no_admitido"]


def test_una_pagina_web_no_se_guarda(escena, bucket, motor_app, motor_migrate,
                                     al_servidor_de_prueba):  # fmt: skip
    url = f"https://{NOMBRE}:{al_servidor_de_prueba.server_address[1]}/listado"
    al_servidor_de_prueba.respuestas["/listado"] = (200, {}, b"<html>comprobantes</html>")
    i = importada_con(escena, motor_app, url)
    antes = documentos_de(motor_migrate, escena["cons"])
    r = traer(escena, i["id"], 1)
    assert r.status_code == 422
    assert r.json()["mensaje"] == importacion.MOTIVOS_DESCARGA["tipo_no_admitido"]
    assert documentos_de(motor_migrate, escena["cons"]) == antes


def test_un_enlace_que_no_existe_da_404(escena, bucket, motor_app, servidor):
    i = importada_con(escena, motor_app, f"https://{NOMBRE}/liq.pdf")
    assert traer(escena, i["id"], 2).status_code == 404


def test_sin_la_facultad_no_se_trae(escena, bucket, motor_app, servidor):
    i = importada_con(escena, motor_app, f"https://{NOMBRE}/liq.pdf")
    assert traer(escena, i["id"], 1, quien=escena["prop"]).status_code == 403


def test_un_enlace_no_cambia_ni_se_borra(escena, bucket, motor_app, motor_migrate, servidor):
    importada_con(escena, motor_app, f"https://{NOMBRE}/liq.pdf")
    with pytest.raises(DBAPIError, match="historia_inmutable|no cambia"):
        with motor_migrate.begin() as c:
            c.execute(text("update nucleo.enlace_liquidacion set url = 'https://otro.prueba/'"))
    with pytest.raises(DBAPIError, match="historia_inmutable|no cambia"):
        with motor_migrate.begin() as c:
            c.execute(text("delete from nucleo.enlace_liquidacion"))


def test_tipo_real_es_el_de_las_cargas():
    """La descarga comprueba el tipo como una carga: por los primeros bytes."""
    assert tipo_real(PDF) is not None and tipo_real(b"<html>") is None
