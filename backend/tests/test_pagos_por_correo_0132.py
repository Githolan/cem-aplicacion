"""Pagos informados por correo, cuarto corte (`0132`; PI-S4 del diseño «Pagos informados», reglas
PC-1 a PC-5, decisiones del propietario Q1 y Q3 y la revisión de PC-4, PI-D22; PA07 entre la app y
el correo).

Contra servicios reales: PostgreSQL, el almacén S3 de pruebas, ClamAV y, para el modelo, el servidor
HTTP local de `test_llm.py`. El correo lo arma la prueba como lo entregaría el Worker de Cloudflare:
entero, con el encabezado de autenticación de `mx.cloudflare.net` arriba de todo. **SIN VERIFICAR**
acá: el formato exacto de ese encabezado en un correo real (lo prueba el corte PI-S5). La escena es
la de `0129`. Cada negación lleva su control. Datos SINTETICOS / NO REAL.
"""

import json
from email.message import EmailMessage
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from cem_contracts import pagos_informados as contrato
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.config import Config
from app.consorcios import lectura_comprobantes, pagos_por_correo
from app.db import transaccion
from app.main import crear_app
from tests.conftest import clave_idempotencia, url_rol
from tests.test_documentos_2c1 import analizar
from tests.test_liquidacion_oficial_0119 import (  # noqa: F401  (los fixtures se importan)
    armar_escena,
    bucket,
    firmas_de_la_imagen,
    ruta,
    vincular,
)
from tests.test_llm import configurar
from tests.test_llm import proveedor as proveedor  # noqa: F401  (es un fixture)
from tests.test_pagos_informados_0129 import (
    _migracion,
    archivo,
    ayuda,
    comprobante,
    datos,
    informar,
)
from tests.test_pagos_informados_0129 import e as e  # noqa: F401  (es un fixture)
from tests.test_reclamos_liquidacion_0123 import correo_de
from tests.test_rutas_egresos import IP, cliente
from tests.test_vinculo_0014 import base_aparte

pytestmark = [pytest.mark.db, pytest.mark.redis]
# ruff: noqa: F811

__all__ = ["base_aparte", "bucket", "firmas_de_la_imagen"]

MIGRACION = Path(__file__).resolve().parent.parent / "migrations/versions/0132_pagos_por_correo.py"
CLAVE = "clave-de-prueba-SINTETICA-0132"
ENTRADA = "/api/v1/entrada/correo-pagos"
DOMINIO_AJENO = "otro-dominio.ejemplo.org"


def m():
    return _migracion(MIGRACION)


@pytest.fixture(autouse=True)
def clave(monkeypatch):
    monkeypatch.setenv(pagos_por_correo.VARIABLE_CLAVE, CLAVE)
    monkeypatch.delenv(lectura_comprobantes.VARIABLE, raising=False)


def autenticacion(dominio: str, resultado: str = "pass",
                  servidor: str = pagos_por_correo.AUTENTICADOR) -> str:  # fmt: skip
    return (f"{servidor}; dkim=pass header.d={dominio} header.s=s1 header.b=AbCdEf;"
            f" spf=pass (mx.cloudflare.net: domain of x@{dominio} designates 192.0.2.1 as"
            f" permitted sender) smtp.mailfrom=x@{dominio};"
            f" dmarc={resultado} header.from={dominio}")  # fmt: skip


def correo(de: str, adjuntos: list[tuple[bytes, str, str]] | None = None, *,
           autenticaciones: list[str] | None = None, mensaje_id: str | None = None,
           asunto: str = "Pago de expensas noviembre",
           en_linea: bytes | None = None, en_linea_tipo: str = "image/png",
           como_gmail: bool = False) -> bytes:  # fmt: skip
    """El correo crudo, con los encabezados de autenticación en el orden dado (el primero arriba).
    Cada adjunto es (contenido, tipo, nombre). `en_linea` va pegada en el texto; `como_gmail` la
    arma como Gmail al arrastrar una foto al cuerpo (`inline` con nombre de archivo)."""
    msg = EmailMessage()
    for a in autenticaciones if autenticaciones is not None else [
            autenticacion(de.rpartition("@")[2])]:  # fmt: skip
        msg["Authentication-Results"] = a
    msg["From"] = f"Vecina SINTETICA <{de}>"
    msg["To"] = "pagos@cem-pagos-staging.holancloud.com"
    msg["Subject"] = asunto
    msg["Message-ID"] = mensaje_id or f"<{uuid4()}@{de.rpartition('@')[2]}>"
    msg.set_content("Les mando el comprobante. Saludos.")
    if en_linea is not None:
        principal, sub = en_linea_tipo.split("/")
        extra = {"disposition": "inline", "filename": "comprobante.png"} if como_gmail else {}
        msg.add_related(en_linea, maintype=principal, subtype=sub, cid="<logo>", **extra)
    for contenido, tipo, nombre in adjuntos if adjuntos is not None else [
            (archivo(str(uuid4())), "application/pdf", "comprobante.pdf")]:  # fmt: skip
        principal, sub = tipo.split("/")
        msg.add_attachment(contenido, maintype=principal, subtype=sub, filename=nombre)
    return msg.as_bytes()


def entrada():
    app = crear_app(Config(url_rol("app"), "0.1.0", "sha-imagen", "sha-contratos",
                           redis_url="memory://",
                           origen_publico="https://cem.ejemplo.com"))  # fmt: skip
    return TestClient(app, client=IP)


def mandar(crudo: bytes, clave_: str | None = CLAVE):
    encabezados = {"Content-Type": "message/rfc822"}
    if clave_ is not None:
        encabezados[contrato.ENCABEZADO_CLAVE_CORREO] = clave_
    with entrada() as c:
        return c.post(ENTRADA, content=crudo, headers=encabezados)


def de(motor_migrate, e, quien: str) -> str:
    return correo_de(motor_migrate, e[quien])


def recibidos(motor_migrate, cons, persona=None) -> list[dict]:
    with motor_migrate.connect() as c:
        return [dict(f) for f in c.execute(text(
            "select * from nucleo.pago_por_correo where consorcio_id = :c"
            " and (cast(:p as uuid) is null or persona_id = :p) order by recibido_en, id"),
            {"c": cons, "p": persona}).mappings()]  # fmt: skip


def por_correo(e, quien: str, resto: str = "", **kwargs):
    with cliente(e[quien], e[f"sesion_{quien}"]) as c:
        url = ruta(e, "pagos-informados", "por-correo") + resto
        if "json" in kwargs or kwargs.pop("post", False):
            return c.post(url, headers=clave_idempotencia() | kwargs.pop("headers", {}), **kwargs)
        return c.get(url, **kwargs)


def informe(unidad, **cambios) -> dict:
    return {"unidad_id": str(unidad), "importe": "45678.90", "fecha_pago": "2026-11-02",
            "medio": "transferencia", "numero_operacion": "OP-SINTETICA-1"} | cambios  # fmt: skip


def acuses(motor_migrate, para: str) -> int:
    with motor_migrate.connect() as c:
        return c.execute(text(
            "select count(*) from plataforma.outbox where evento = 'correo.solicitado'"
            " and carga->>'para' = :p"), {"p": para}).scalar_one()  # fmt: skip


def rechazo(r) -> tuple[int, str]:
    return r.status_code, r.json()["codigo"]


# --- El recorrido -------------------------------------------------------------------------------


def test_un_comprobante_por_correo_queda_por_completar_y_la_persona_lo_informa(
    e, motor_migrate, motor_app
):
    direccion = de(motor_migrate, e, "prop")
    # Un asunto plegado en dos líneas (RFC 5322) se guarda en una.
    crudo = correo(direccion, asunto="Expensas de noviembre")
    plegado = crudo.replace(b"Subject: Expensas de noviembre", b"Subject: Expensas\n de noviembre")
    assert plegado != crudo
    r = mandar(plegado)
    assert r.status_code == 200, r.text
    assert r.json() == {"nuevos": 1, "ya_recibidos": 0, "ya_informados": 0}
    [fila] = recibidos(motor_migrate, e["cons"])
    assert (fila["persona_id"], fila["estado"], fila["asunto"]) == (
        e["prop"],
        "por_completar",
        "Expensas de noviembre",
    )
    assert acuses(motor_migrate, direccion) == 1
    # El cuerpo no se guarda (PC-5): ni en la fila ni en la auditoría.
    with motor_migrate.connect() as c:
        auditado = json.dumps([dict(x) for x in c.execute(text(
            "select accion, despues from auditoria.eventos where entidad = 'pago_por_correo'"
            " and consorcio_id = :c"), {"c": e["cons"]}).mappings()], default=str)  # fmt: skip
    assert "comprobante. Saludos" not in json.dumps(fila, default=str) + auditado
    assert "consorcio.pago_por_correo.recibir" in auditado

    lista = por_correo(e, "prop")
    assert lista.status_code == 200, lista.text
    [item] = lista.json()["elementos"]
    assert (item["comprobante_estado"], item["leido"]) == ("en_cuarentena", False)
    assert por_correo(e, "prop", f"/{item['id']}/comprobante").status_code == 409

    assert analizar(motor_app) >= 1
    [item] = por_correo(e, "prop").json()["elementos"]
    assert item["comprobante_estado"] == "disponible"
    archivo_visto = por_correo(e, "prop", f"/{item['id']}/comprobante")
    assert archivo_visto.status_code == 200
    assert archivo_visto.headers["content-type"] == "application/pdf"

    r = por_correo(e, "prop", f"/{item['id']}/informe", json=informe(e["u"]["1-A"]))
    assert r.status_code == 201, r.text
    pago = r.json()
    assert (pago["origen"], pago["estado"], pago["ya_informado"]) == ("correo", "informado", False)
    assert pago["comprobante_version_id"] == item["comprobante_version_id"]
    [fila] = recibidos(motor_migrate, e["cons"])
    assert (fila["estado"], str(fila["pago_id"])) == ("informado", pago["id"])
    assert por_correo(e, "prop").json()["elementos"] == []
    # Informarlo otra vez responde el mismo pago.
    r = por_correo(e, "prop", f"/{item['id']}/informe", json=informe(e["u"]["1-A"]))
    assert (r.status_code, r.json()["id"], r.json()["ya_informado"]) == (200, pago["id"], True)


def test_pa07_el_archivo_ya_informado_en_la_app_no_entra_por_correo(e, motor_migrate):
    contenido = archivo("pa07-app-primero")
    informado = informar(e, "prop", datos(e["u"]["1-A"], comprobante(
        e, "prop", e["u"]["1-A"], contenido)))  # fmt: skip
    assert informado.status_code == 201, informado.text
    direccion = de(motor_migrate, e, "prop")
    r = mandar(correo(direccion, [(contenido, "application/pdf", "c.pdf")]))
    assert r.json() == {"nuevos": 0, "ya_recibidos": 0, "ya_informados": 1}
    assert recibidos(motor_migrate, e["cons"]) == []
    assert acuses(motor_migrate, direccion) == 0
    # Control: otro archivo sí entra.
    r = mandar(correo(direccion, [(archivo("pa07-otro"), "application/pdf", "c.pdf")]))
    assert r.json()["nuevos"] == 1


def test_pa07_el_mismo_archivo_por_correo_y_despues_en_la_app_queda_en_un_solo_pago(
    e, motor_migrate, motor_app
):
    contenido = archivo("pa07-correo-primero")
    r = mandar(correo(de(motor_migrate, e, "prop"),
                      [(contenido, "application/pdf", "c.pdf")]))  # fmt: skip
    assert r.json()["nuevos"] == 1
    en_la_app = informar(e, "prop", datos(e["u"]["1-A"], comprobante(
        e, "prop", e["u"]["1-A"], contenido)))  # fmt: skip
    assert en_la_app.status_code == 201, en_la_app.text
    [item] = por_correo(e, "prop").json()["elementos"]
    r = por_correo(e, "prop", f"/{item['id']}/informe", json=informe(e["u"]["1-A"]))
    assert (r.status_code, r.json()["id"], r.json()["ya_informado"]) == (
        200,
        en_la_app.json()["id"],
        True,
    )
    with motor_migrate.connect() as c:
        assert c.execute(text("select count(*) from nucleo.pago_informado where consorcio_id = :c"),
                         {"c": e["cons"]}).scalar_one() == 1  # fmt: skip
    [fila] = recibidos(motor_migrate, e["cons"])
    assert (fila["estado"], str(fila["pago_id"])) == ("informado", en_la_app.json()["id"])


def test_un_reintento_de_entrega_no_duplica_y_el_mismo_archivo_por_completar_tampoco(
    e, motor_migrate
):
    direccion = de(motor_migrate, e, "prop")
    contenido = archivo("reintento")
    crudo = correo(direccion, [(contenido, "application/pdf", "c.pdf")])
    assert mandar(crudo).json()["nuevos"] == 1
    assert mandar(crudo).json() == {"nuevos": 0, "ya_recibidos": 1, "ya_informados": 0}
    otro_mensaje = correo(direccion, [(contenido, "application/pdf", "c.pdf")])
    assert mandar(otro_mensaje).json() == {"nuevos": 0, "ya_recibidos": 1, "ya_informados": 0}
    assert len(recibidos(motor_migrate, e["cons"])) == 1
    assert acuses(motor_migrate, direccion) == 1


# --- La clave y quien manda ---------------------------------------------------------------------


def test_sin_la_clave_en_el_entorno_no_recibe_nada(e, motor_migrate, monkeypatch):
    crudo = correo(de(motor_migrate, e, "prop"))
    monkeypatch.delenv(pagos_por_correo.VARIABLE_CLAVE)
    assert rechazo(mandar(crudo)) == (503, "entrada_no_configurada")
    monkeypatch.setenv(pagos_por_correo.VARIABLE_CLAVE, CLAVE)
    assert mandar(crudo).status_code == 200  # control
    assert len(recibidos(motor_migrate, e["cons"])) == 1


@pytest.mark.parametrize("otra", [None, "", "clave-equivocada", CLAVE + "x", CLAVE[:-1]])
def test_una_clave_equivocada_da_401_y_no_guarda_nada(e, motor_migrate, otra):
    crudo = correo(de(motor_migrate, e, "prop"))
    assert rechazo(mandar(crudo, otra)) == (401, "clave_de_entrada_invalida")
    assert recibidos(motor_migrate, e["cons"]) == []
    assert mandar(crudo).status_code == 200  # control: el mismo correo con la clave buena


@pytest.mark.parametrize(
    "caso",
    [
        "sin_encabezado",
        "dmarc_falla",
        "otro_dominio",
        "otro_servidor_arriba",
        "dmarc_ninguno",
        "dos_remitentes",
    ],
)
def test_sin_dmarc_alineado_rebota_con_el_aviso(e, motor_migrate, caso):
    direccion = de(motor_migrate, e, "prop")
    dominio = direccion.rpartition("@")[2]
    autenticaciones = {
        "sin_encabezado": [],
        "dmarc_falla": [autenticacion(dominio, "fail")],
        "otro_dominio": [autenticacion(DOMINIO_AJENO)],
        # El de arriba no es de Cloudflare: el que dice pass, más abajo, lo pudo escribir
        # cualquiera.
        "otro_servidor_arriba": [
            autenticacion(dominio, servidor="mx.otro.example"),
            autenticacion(dominio),
        ],
        "dmarc_ninguno": [autenticacion(dominio, "none")],
        "dos_remitentes": [autenticacion(dominio)],
    }[caso]
    crudo = correo(direccion, autenticaciones=autenticaciones)
    if caso == "dos_remitentes":
        crudo = crudo.replace(
            b"From: Vecina SINTETICA <",
            b"From: otra@" + dominio.encode() + b", Vecina SINTETICA <",
            1,
        )
    r = mandar(crudo)
    assert rechazo(r) == (422, "remitente_no_identificado")
    assert r.json()["mensaje"] == pagos_por_correo.REBOTE_REMITENTE
    assert recibidos(motor_migrate, e["cons"]) == []
    assert mandar(correo(direccion)).status_code == 200  # control: alineado entra


@pytest.mark.parametrize("quien", ["desconocida", "ad", "sin_confirmar"])
def test_sin_cuenta_o_sin_un_vinculo_que_cuente_rebota(e, motor_migrate, quien):
    direccion = (f"desconocida-{uuid4().hex[:8]}@ejemplo.com" if quien == "desconocida"
                 else de(motor_migrate, e, quien))  # fmt: skip
    r = mandar(correo(direccion))
    assert rechazo(r) == (422, "remitente_no_identificado")
    assert recibidos(motor_migrate, e["cons"]) == []
    for control in ("inq", "oc"):  # Q1: el inquilino y el ocupante también mandan
        assert mandar(correo(de(motor_migrate, e, control))).status_code == 200
    assert {f["persona_id"] for f in recibidos(motor_migrate, e["cons"])} == {e["inq"], e["oc"]}


def test_con_unidades_en_dos_edificios_rebota(e, motor_migrate, motor_app):
    direccion = de(motor_migrate, e, "prop")
    assert mandar(correo(direccion)).status_code == 200  # control: con uno solo, entra
    otro = armar_escena(motor_migrate, motor_app)
    # Un vínculo sin confirmar en el otro edificio no cuenta: sigue entrando en el suyo.
    vincular(motor_migrate, otro["cons"], e["prop"], otro["u"]["1-A"], "propietario",
             confirmado=False)  # fmt: skip
    assert mandar(correo(direccion)).status_code == 200
    vincular(motor_migrate, otro["cons"], e["prop"], otro["u"]["PB-A"], "propietario",
             confirmado=True)  # fmt: skip
    r = mandar(correo(direccion))
    assert rechazo(r) == (422, "remitente_en_varios_consorcios")
    assert r.json()["mensaje"] == pagos_por_correo.REBOTE_VARIOS
    assert len(recibidos(motor_migrate, e["cons"])) == 2
    assert recibidos(motor_migrate, otro["cons"]) == []


# --- Los adjuntos -------------------------------------------------------------------------------


def tipo_de(motor_migrate, fila) -> str:
    with motor_migrate.connect() as c:
        return c.execute(text("select tipo from nucleo.version_documento where id = :v"),
                         {"v": fila["version_documento_id"]}).scalar_one()  # fmt: skip


def test_sin_comprobantes_rebota(e, motor_migrate):
    direccion = de(motor_migrate, e, "prop")
    casos = [
        dict(adjuntos=[]),
        dict(adjuntos=[(b"texto que dice ser un PDF", "application/pdf", "falso.pdf")]),
        # En línea cuenta solo una imagen: un PDF pegado en el texto no.
        dict(adjuntos=[], en_linea=archivo(str(uuid4())), en_linea_tipo="application/pdf"),
        # Una imagen en línea que no es una imagen de verdad tampoco.
        dict(adjuntos=[], en_linea=b"no soy un PNG"),
    ]
    for caso in casos:
        r = mandar(correo(direccion, **caso))
        assert rechazo(r) == (422, "sin_comprobantes"), caso
        assert r.json()["mensaje"] == pagos_por_correo.REBOTE_SIN_COMPROBANTES
    assert recibidos(motor_migrate, e["cons"]) == []


@pytest.mark.parametrize("como_gmail", [False, True], ids=["relacionada", "como-gmail"])
def test_sin_adjuntos_la_imagen_en_linea_entra_como_comprobante(e, motor_migrate, como_gmail):
    """PI-D36 (propietario, 26-11-2026): la foto del comprobante pegada en el cuerpo del correo."""
    direccion = de(motor_migrate, e, "prop")
    r = mandar(correo(direccion, [], en_linea=pdf_a_png(), como_gmail=como_gmail))
    assert r.status_code == 200, r.text
    assert r.json()["nuevos"] == 1
    [fila] = recibidos(motor_migrate, e["cons"])
    assert tipo_de(motor_migrate, fila) == "image/png"


def test_con_un_adjunto_que_sirve_la_imagen_en_linea_no_cuenta(e, motor_migrate):
    """El logo de una firma no entra cuando el correo trae su comprobante adjunto."""
    direccion = de(motor_migrate, e, "prop")
    r = mandar(correo(direccion, [(archivo(str(uuid4())), "application/pdf", "pago.pdf")],
                      en_linea=pdf_a_png(), como_gmail=True))  # fmt: skip
    assert r.json()["nuevos"] == 1
    [fila] = recibidos(motor_migrate, e["cons"])
    assert tipo_de(motor_migrate, fila) == "application/pdf"
    # Con un adjunto que no sirve (no es PDF, JPG ni PNG de verdad), la imagen en línea sí cuenta.
    r = mandar(correo(direccion, [(b"texto que dice ser un PDF", "application/pdf", "falso.pdf")],
                      en_linea=pdf_a_png(b"\x00\x00\xff\x00")))  # fmt: skip
    assert r.json()["nuevos"] == 1
    assert [tipo_de(motor_migrate, f) for f in recibidos(motor_migrate, e["cons"])] == [
        "application/pdf", "image/png"]


def pdf_a_png(pixel: bytes = b"\x00\xff\x00\x00") -> bytes:
    """Un PNG mínimo válido (1 × 1, sintético); otro `pixel` da otro archivo."""
    import struct
    import zlib

    def trozo(tipo: bytes, datos_: bytes) -> bytes:
        return (struct.pack(">I", len(datos_)) + tipo + datos_
                + struct.pack(">I", zlib.crc32(tipo + datos_) & 0xFFFFFFFF))  # fmt: skip

    cabecera = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + trozo(b"IHDR", cabecera)
            + trozo(b"IDAT", zlib.compress(pixel)) + trozo(b"IEND", b""))  # fmt: skip


def test_mas_comprobantes_de_los_que_se_aceptan_juntos_rebota(e, motor_migrate):
    direccion = de(motor_migrate, e, "prop")
    tope = contrato.MAXIMO_COMPROBANTES_POR_CORREO
    de_mas = [(archivo(f"tope-{i}"), "application/pdf", f"c{i}.pdf") for i in range(tope + 1)]
    r = mandar(correo(direccion, de_mas))
    assert rechazo(r) == (422, "demasiados_comprobantes")
    assert recibidos(motor_migrate, e["cons"]) == []
    assert mandar(correo(direccion, de_mas[:tope])).json()["nuevos"] == tope  # control


def test_el_tope_diario_por_persona_rebota(e, motor_migrate, motor_app):
    tope = contrato.MAXIMO_COMPROBANTES_POR_DIA
    recibir = text("select * from nucleo.recibir_pago_por_correo(:p, :m, null,"
                   " 'application/pdf', 10, :s)")  # fmt: skip
    with transaccion(motor_app, persona_id=e["prop"], consorcio_id=e["cons"]) as c:
        for i in range(tope - 1):
            c.execute(recibir, {"p": e["prop"], "m": f"<tope-{i}>", "s": f"{i:064x}"}).one()
    direccion = de(motor_migrate, e, "prop")
    assert mandar(correo(direccion)).json()["nuevos"] == 1  # control: el último que entra
    r = mandar(correo(direccion))
    assert rechazo(r) == (422, "demasiados_comprobantes")
    assert r.json()["mensaje"] == pagos_por_correo.REBOTE_TOPE_DIARIO
    assert len(recibidos(motor_migrate, e["cons"])) == tope
    # Otra persona no comparte el tope.
    assert mandar(correo(de(motor_migrate, e, "inq"))).json()["nuevos"] == 1


# --- La lectura propone; la persona decide ------------------------------------------------------


def test_la_lectura_propone_y_lo_que_se_informa_es_lo_que_decide_la_persona(
    e, motor_migrate, motor_app, monkeypatch, proveedor
):
    configurar(monkeypatch, "anthropic", proveedor)
    monkeypatch.setenv(lectura_comprobantes.VARIABLE, lectura_comprobantes.ENCENDIDA)
    proveedor.texto = json.dumps({
        "importe": "45678.90", "moneda": "ARS", "cuenta_destino": "9990000000000000000017",
        "titular_destino": "Consorcio SINTETICO", "numero_operacion": "OP-0048213377",
        "fecha": "2026-11-02", "legible": True,
    })  # fmt: skip
    assert mandar(correo(de(motor_migrate, e, "prop"))).json()["nuevos"] == 1
    assert analizar(motor_app) >= 1  # el antivirus, que encola la lectura
    assert analizar(motor_app) >= 1  # la lectura
    [item] = por_correo(e, "prop").json()["elementos"]
    assert (item["leido"], item["legible"], item["importe"], item["moneda"],
            item["numero_operacion"], item["fecha_pago"]) == (
        True, True, "45678.90", "ARS", "OP-0048213377", "2026-11-02")  # fmt: skip
    crudo = json.dumps(recibidos(motor_migrate, e["cons"]), default=str)
    assert "9990000000000000000017" not in crudo  # de la cuenta, nada
    # La persona corrige el importe: vale lo suyo.
    r = por_correo(e, "prop", f"/{item['id']}/informe",
                   json=informe(e["u"]["1-A"], importe="40000.00"))  # fmt: skip
    assert r.status_code == 201, r.text
    assert r.json()["importe"] == "40000.00"


def test_una_lectura_con_una_cuenta_en_el_numero_se_descarta(e, motor_migrate, motor_app,
                                                             monkeypatch, proveedor):  # fmt: skip
    """Como en `0077`: la lectura no valida y no se guarda nada de ella, ni el importe."""
    configurar(monkeypatch, "anthropic", proveedor)
    monkeypatch.setenv(lectura_comprobantes.VARIABLE, lectura_comprobantes.ENCENDIDA)
    proveedor.texto = json.dumps({
        "importe": "100.00", "moneda": "ARS", "cuenta_destino": None, "titular_destino": None,
        "numero_operacion": "9990000000000000000017", "fecha": "2026-11-02", "legible": True,
    })  # fmt: skip
    assert mandar(correo(de(motor_migrate, e, "prop"))).json()["nuevos"] == 1
    analizar(motor_app)
    analizar(motor_app)
    [fila] = recibidos(motor_migrate, e["cons"])
    assert (fila["lectura_legible"], fila["lectura_importe"], fila["lectura_numero"]) == (
        False,
        None,
        None,
    )


def test_con_la_lectura_apagada_no_llama_a_nadie(e, motor_migrate, motor_app, monkeypatch,
                                                 proveedor):  # fmt: skip
    configurar(monkeypatch, "anthropic", proveedor)
    assert mandar(correo(de(motor_migrate, e, "prop"))).json()["nuevos"] == 1
    analizar(motor_app)
    analizar(motor_app)
    assert proveedor.pedidos == []
    [item] = por_correo(e, "prop").json()["elementos"]
    assert (item["comprobante_estado"], item["leido"]) == ("disponible", False)


# --- Descartar y quién ve -----------------------------------------------------------------------


def test_descartar_lo_saca_de_la_lista_y_no_se_informa_despues(e, motor_migrate):
    direccion = de(motor_migrate, e, "prop")
    assert mandar(correo(direccion)).json()["nuevos"] == 1
    [item] = por_correo(e, "prop").json()["elementos"]
    r = por_correo(e, "prop", f"/{item['id']}/descarte", post=True)
    assert (r.status_code, r.json()["estado"]) == (200, "descartado")
    assert por_correo(e, "prop").json()["elementos"] == []
    assert por_correo(e, "prop", f"/{item['id']}/descarte", post=True).status_code == 200
    r = por_correo(e, "prop", f"/{item['id']}/informe", json=informe(e["u"]["1-A"]))
    assert rechazo(r) == (409, "pago_por_correo_descartado")
    # Control al revés: uno informado no se descarta.
    assert mandar(correo(direccion)).json()["nuevos"] == 1
    [otro] = por_correo(e, "prop").json()["elementos"]
    assert por_correo(e, "prop", f"/{otro['id']}/informe",
                      json=informe(e["u"]["1-A"])).status_code == 201  # fmt: skip
    r = por_correo(e, "prop", f"/{otro['id']}/descarte", post=True)
    assert rechazo(r) == (409, "pago_por_correo_informado")


def test_solo_quien_lo_mando_lo_ve_y_lo_completa(e, motor_migrate):
    assert mandar(correo(de(motor_migrate, e, "prop"))).json()["nuevos"] == 1
    [item] = por_correo(e, "prop").json()["elementos"]
    assert por_correo(e, "inq").json()["elementos"] == []
    # La inquilina puede informar por su unidad, pero lo que mandó otra persona no existe para ella.
    r = por_correo(e, "inq", f"/{item['id']}/informe", json=informe(e["u"]["PB-A"]))
    assert rechazo(r) == (404, "no_encontrado")
    assert rechazo(por_correo(e, "inq", f"/{item['id']}/descarte", post=True)) == (
        404,
        "no_encontrado",
    )
    assert por_correo(e, "inq", f"/{item['id']}/comprobante").status_code == 404
    # Y la propietaria no informa por una unidad que no es suya.
    r = por_correo(e, "prop", f"/{item['id']}/informe", json=informe(e["u"]["PB-A"]))
    assert r.status_code == 403
    assert recibidos(motor_migrate, e["cons"])[0]["estado"] == "por_completar"


def test_ayudando_no_se_ve_ni_se_completa(e, motor_migrate):
    assert mandar(correo(de(motor_migrate, e, "prop"))).json()["nuevos"] == 1
    autorizacion = ayuda(e, motor_migrate, ["consultar_expensas", "preparar_reclamos"])
    con_ayuda = {"X-CEM-Ayuda": autorizacion}
    with cliente(e["ayu"], e["sesion_ayu"]) as c:
        r = c.get(ruta(e, "pagos-informados", "por-correo"), headers=con_ayuda)
    assert rechazo(r) == (403, "ayuda_no_admitida")
    with cliente(e["ayu"], e["sesion_ayu"]) as c:  # control: la lista de pagos sí la ve
        assert c.get(ruta(e, "pagos-informados"), headers=con_ayuda).status_code == 200


# --- La base ------------------------------------------------------------------------------------


def test_la_fila_cambia_solo_como_se_permite(e, motor_migrate, motor_app):
    assert mandar(correo(de(motor_migrate, e, "prop"))).json()["nuevos"] == 1
    [fila] = recibidos(motor_migrate, e["cons"])
    cambios = [
        "update nucleo.pago_por_correo set persona_id = :otra where id = :r",
        "update nucleo.pago_por_correo set asunto = 'otro' where id = :r",
        "delete from nucleo.pago_por_correo where id = :r",
    ]
    for sql in cambios:
        with pytest.raises(DBAPIError) as error:
            with motor_migrate.begin() as c:
                c.execute(text(sql), {"r": fila["id"], "otra": e["inq"]})
        assert error.value.orig.diag.message_hint == "historia_inmutable", sql
    leer = text("update nucleo.pago_por_correo set lectura_en = now(), lectura_legible = false"
                " where id = :r")  # fmt: skip
    with motor_migrate.begin() as c:  # control: la lectura se escribe una vez
        c.execute(leer, {"r": fila["id"]})
    with pytest.raises(DBAPIError) as error:
        with motor_migrate.begin() as c:
            c.execute(text("update nucleo.pago_por_correo set lectura_legible = true,"
                           " lectura_importe = 1 where id = :r"), {"r": fila["id"]})  # fmt: skip
    assert error.value.orig.diag.message_hint == "historia_inmutable"
    with motor_migrate.begin() as c:
        c.execute(text("update nucleo.pago_por_correo set estado = 'descartado' where id = :r"),
                  {"r": fila["id"]})  # fmt: skip
    with pytest.raises(DBAPIError) as error:
        with motor_migrate.begin() as c:
            c.execute(text("update nucleo.pago_por_correo set estado = 'por_completar'"
                           " where id = :r"), {"r": fila["id"]})  # fmt: skip
    assert error.value.orig.diag.message_hint == "historia_inmutable"


def test_la_entrada_corre_solo_en_el_ambito_de_quien_manda(e, motor_app):
    """Ni en el de otra persona ni en uno sin persona: la API no abre ámbitos de trabajo."""
    recibir = text("select * from nucleo.recibir_pago_por_correo(:p, '<x>', null,"
                   " 'application/pdf', 10, :s)")  # fmt: skip
    for ambito in (e["inq"], None):
        with pytest.raises(DBAPIError) as error:
            with transaccion(motor_app, persona_id=ambito, consorcio_id=e["cons"]) as c:
                c.execute(recibir, {"p": e["prop"], "s": "a" * 64})
        assert error.value.orig.sqlstate == "42501", ambito
    with transaccion(motor_app, persona_id=e["prop"], consorcio_id=e["cons"]) as c:  # control
        assert c.execute(recibir, {"p": e["prop"], "s": "a" * 64}).one().resultado == "nuevo"


def test_la_migracion_copia_los_limites_del_contrato():
    assert m().MAXIMO_POR_DIA == contrato.MAXIMO_COMPROBANTES_POR_DIA
    assert m().LARGO_NUMERO_LEIDO == contrato.LARGO_NUMERO_OPERACION
    assert m().ESTADOS == tuple(contrato.EstadoPagoPorCorreo)
    assert m().ESTADOS_QUE_NO_CUENTAN == contrato.ESTADOS_QUE_NO_CUENTAN


def test_las_rutas_son_solo_para_cem_app_y_las_internas_para_nadie(motor_migrate):
    puede = text("select has_function_privilege(:r, :f, 'EXECUTE')")
    with motor_migrate.connect() as c:
        for f in m().RUTAS:
            assert c.execute(puede, {"r": "cem_app", "f": f}).scalar_one() is True, f
            assert c.execute(puede, {"r": "cem_ro", "f": f}).scalar_one() is False, f
        for f in m().INTERNAS:
            assert c.execute(puede, {"r": "cem_app", "f": f}).scalar_one() is False, f


# --- El encabezado de autenticación -------------------------------------------------------------


@pytest.mark.parametrize(
    ("encabezado", "dominio"),
    [
        ("mx.cloudflare.net; dmarc=pass header.from=ejemplo.com", "ejemplo.com"),
        (
            "mx.cloudflare.net;\r\n dkim=pass header.d=ejemplo.com;\r\n DMARC=Pass (p=NONE sp=NONE)"
            ' header.from="Ejemplo.COM"',
            "ejemplo.com",
        ),
        ("mx.cloudflare.net 1; spf=pass smtp.mailfrom=a@b.c; dmarc=pass header.from=b.c", "b.c"),
        ("mx.cloudflare.net; dmarc=fail header.from=ejemplo.com", None),
        ("mx.cloudflare.net; dmarc=pass", None),
        ("mx.cloudflare.net; dkim=pass header.from=ejemplo.com", None),
        ("mx.cloudflare.net.ejemplo.com; dmarc=pass header.from=ejemplo.com", None),
        ("otro.servidor; dmarc=pass header.from=ejemplo.com", None),
        ("", None),
    ],
)
def test_lee_solo_el_dmarc_aprobado_que_escribio_cloudflare(encabezado, dominio):
    assert pagos_por_correo.dominio_dmarc_aprobado(encabezado) == dominio


def test_0132_ida_y_vuelta_y_no_baja_con_datos(base_aparte):
    cfg, motor = base_aparte
    command.upgrade(cfg, "0131")

    def informar_def() -> str:
        with motor.connect() as c:
            return c.execute(text("select pg_get_functiondef(cast(:f as regprocedure))"),
                             {"f": m().INFORMAR}).scalar_one()  # fmt: skip

    def existe() -> bool:
        with motor.connect() as c:
            return c.execute(text("select to_regclass('nucleo.pago_por_correo') is not null"))\
                .scalar_one()  # fmt: skip

    antes = informar_def()
    assert not existe()
    command.upgrade(cfg, "0132")
    assert existe() and "origen_del_comprobante" in informar_def()
    command.downgrade(cfg, "0131")
    assert (existe(), informar_def()) == (False, antes)
    command.upgrade(cfg, "0132")  # control: vuelve a subir limpio
    with motor.begin() as c:
        cons = c.execute(
            text("insert into nucleo.consorcio (nombre) values ('A') returning id")
        ).scalar_one()
        per = c.execute(text(
            "insert into nucleo.persona (correo, nombre) values (:c, 'P') returning id"),
            {"c": f"p-{uuid4().hex[:8]}@e.com"}).scalar_one()  # fmt: skip
        doc = c.execute(text(
            "insert into nucleo.documento (consorcio_id, categoria, titulo, fecha, cargado_por)"
            " values (:c, 'otros', 'T', '2026-11-01', :p) returning id"),
            {"c": cons, "p": per}).scalar_one()  # fmt: skip
        ver = c.execute(text(
            "insert into nucleo.version_documento (consorcio_id, documento_id, tipo, tamano,"
            " sha256, clave_objeto, subida_por) values (:c, :d, 'application/pdf', 10, :s, 'k',"
            " :p) returning id"),
            {"c": cons, "d": doc, "s": "b" * 64, "p": per}).scalar_one()  # fmt: skip
        c.execute(text(
            "insert into nucleo.pago_por_correo (consorcio_id, persona_id, mensaje_id,"
            " documento_id, version_documento_id, sha256) values (:c, :p, '<m>', :d, :v, :s)"),
            {"c": cons, "p": per, "d": doc, "v": ver, "s": "b" * 64})  # fmt: skip
    with pytest.raises(DBAPIError) as error:
        command.downgrade(cfg, "0131")
    assert error.value.orig.diag.message_hint == "downgrade_con_datos"


def test_la_direccion_se_ofrece_solo_con_la_entrada_configurada(e, monkeypatch):
    """`ListaPagosPorCorreo.direccion` (PI-S6): con la dirección y la clave; sin una, nula."""
    direccion = "pagos@cem-pagos-pruebas.ejemplo.com"
    monkeypatch.delenv(pagos_por_correo.VARIABLE_DIRECCION, raising=False)
    assert por_correo(e, "prop").json()["direccion"] is None  # control: sin la dirección
    monkeypatch.setenv(pagos_por_correo.VARIABLE_DIRECCION, direccion)
    assert por_correo(e, "prop").json()["direccion"] == direccion
    monkeypatch.delenv(pagos_por_correo.VARIABLE_CLAVE)
    assert por_correo(e, "prop").json()["direccion"] is None
