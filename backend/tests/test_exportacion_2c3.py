"""Exportación de un consorcio por soporte (H2c §1.1, §5 y §7 «Exportación»; RF36; 0038).

`scripts.exportar_consorcio` corre como `cem_soporte_exportacion`: sin `BYPASSRLS` ni `SELECT`
directo, lee por las funciones de exportación y escribe solo `plataforma.exportacion`. Todo
contra servicios reales: PostgreSQL, el almacén S3 de pruebas, ClamAV (para que las versiones
lleguen a `disponible` o `rechazado` por el camino real) y `age`. Cada negación lleva su control:
el mismo pedido de quien sí tiene la capacidad pasa; cada búsqueda que da 0 se prueba antes en
un lugar donde lo que busca sí está.
"""

import hashlib
import io
import json
import os
import re
import subprocess
import zipfile
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from app import auditoria
from app.db import fijar_ambito, transaccion
from scripts import exportar_consorcio
from tests.conftest import url_rol
from tests.test_coeficientes_0021 import EXPENSAS, porcentajes, registrar
from tests.test_condicion_habilitante_consejo import consejo, estado  # noqa: F401  (fixture)
from tests.test_contexto_mandatos import otra_unidad
from tests.test_documentos_2c1 import (  # noqa: F401  (`bucket` y `firmas_de_la_imagen` son fixtures)
    JPEG,
    PNG,
    _s3,
    analizar,
    bucket,
    crear,
    firmas_de_la_imagen,
    pdf,
    subir,
)
from tests.test_egresos_1a import BAJA_CONTRATO, confirmado, enviada, nueva_cuenta, umbral
from tests.test_liquidacion_0057 import AGREGAR, QUITAR, borrador, pagada, unidades
from tests.test_liquidacion_0057 import porcentajes as pesos_del_esquema
from tests.test_mandatos import otra_persona
from tests.test_mandatos_0012 import alta
from tests.test_modo_operacion import (
    cambiar,
    consorcio_con_persona,
    decidir,
    decidir_equipo,
    hoy_de,
)
from tests.test_orden_pago_neutra import (
    PREPARAR,
    RECHAZAR,
    cbu_sintetico,
    escena_economica,
    exigir,
    llamar,
    otorgar,
)
from tests.test_pago_manual_0055 import CONFIRMAR, DECLARAR, emitida, ver
from tests.test_pago_manual_0055 import acta as acta_de_la_escena
from tests.test_pague_otra_cosa_0075 import con_diferencia, resuelta
from tests.test_vinculo_0014 import base_aparte  # noqa: F401  (`base_aparte` es un fixture)
from tests.test_vinculo_fin_0020 import finalizar

pytestmark = [pytest.mark.db, pytest.mark.redis]

FACULTADES = list(exportar_consorcio.FACULTADES)
IP = "203.0.113.77"
CONSENTIMIENTO = "Consentimiento firmado CONSENT-7781"
HASH_CONTRASENA = "$argon2id$v=19$m=65536,t=3,p=4$c2FsLWV4cG9ydA$HASH-DE-PRUEBA-EXPORTACION"
FUNCIONES = [f"exportar_{p}" for p in exportar_consorcio.PARTES] + ["exportar_objetos_documentos"]
PARTES_ECONOMICAS = ("proveedores", "ordenes_pago", "pagos", "liquidaciones")
ENTIDADES_ECONOMICAS = {
    entidad for parte in PARTES_ECONOMICAS for entidad in exportar_consorcio.PARTES[parte]
}
EGRESOS_DEL_CONSEJO = ["egresos.aprobar_orden"]

# Las claves de cada fila del paquete, una por una. Una clave que no está acá es un dato que
# alguien agregó sin decidir que va (H2c §5, «Nunca van»).
CLAVES_POR_ENTIDAD = {
    "consorcio": {"id", "nombre", "cuit", "zona_horaria"},
    "unidad": {"id", "designacion"},
    "vinculo": {"id", "unidad_id", "designacion", "nombre", "tipo", "vigente_desde",
                "vigente_hasta", "confirmado"},
    "documento": {"id", "categoria", "titulo", "fecha", "cargado_en", "lectura_carga", "estado",
                  "retirado_en", "retirado_motivo"},
    "version": {"id", "documento_id", "tipo", "tamano", "sha256", "subida_en", "archivo"},
    "decision": {"id", "tipo", "fecha", "descripcion", "referencia_acta", "sha256_acta"},
    "mandato": {"id", "tipo", "nombre", "cuit", "vigente_desde", "vigente_hasta", "estado",
                "con_cuenta", "matricula", "decision_id", "causa_baja", "baja_decision_id",
                "instrumento_referencia", "motivo_fin", "finalizado_en"},
    "facultad": {"mandato_id", "facultad", "recurso"},
    "revision": {"id", "mandato_id", "decision_id", "eficaz_desde"},
    "modo": {"modo", "vigente_desde", "vigente_hasta", "decision_id"},
    "coeficiente": {"esquema_id", "grupo", "version", "vigente_desde", "vigente_hasta",
                    "unidad_id", "designacion", "porcentaje"},
    "contacto": {"id", "funcion", "naturaleza", "nombre", "telefono", "indicaciones",
                 "vigente_desde", "vigente_hasta", "actualizado_en", "verificado_en",
                 "observado"},
    "evento": {"fecha", "accion", "recurso", "origen", "actor", "actor_nombre", "cambios"},
    "proveedor": {"id", "cuit", "creado_en", "alta_por_nombre"},
    "proveedor_version": {"proveedor_id", "version", "razon_social", "rubro", "contacto", "estado",
                          "documento_id", "cuenta_ultimos_digitos", "preparada_por_nombre",
                          "creada_en"},
    "contrato": {"id", "proveedor_id", "tipo", "alcance", "importe_maximo_centavos", "moneda",
                 "vigente_desde", "vigente_hasta", "documento_id", "decision_id",
                 "registrado_por_nombre", "creado_en"},
    "contrato_confirmacion": {"contrato_id", "proveedor_id", "proveedor_version",
                              "confirmada_por_nombre", "creada_en"},
    "contrato_baja": {"contrato_id", "baja_por_nombre", "creada_en"},
    "orden": {"id", "estado", "version_vigente", "preparada_por_nombre", "creada_en",
              "actualizada_en", "aprobada_en", "motivo_rechazo"},
    "orden_version": {"orden_id", "numero", "importe_centavos", "moneda", "concepto",
                      "proveedor_id", "proveedor_version", "razon_social",
                      "cuenta_ultimos_digitos", "condicion_beneficiario", "comprobante",
                      "usa_fondo_reserva", "aprobaciones_requeridas", "franja", "umbral_id",
                      "contrato_id", "documento_id", "factura_version_id", "alerta_fraccionamiento",
                      "preparada_por_nombre", "creada_en"},
    "orden_aprobacion": {"id", "orden_id", "version", "aprobada_por_nombre", "creada_en"},
    "factura_adjunta": {"id", "orden_id", "version", "documento_id", "factura_version_id",
                        "adjuntada_por_nombre", "adjuntada_en", "reemplaza_a"},
    "orden_transicion": {"orden_id", "fecha", "accion", "origen", "desde", "hacia", "version",
                         "actor_nombre"},
    "umbral": {"id", "importe_centavos", "moneda", "vigente_desde", "decision_id"},
    "aprobaciones_configuradas": {"id", "cantidad", "vigente_desde", "decision_id"},
    "ejecucion": {"id", "orden_id", "version", "modalidad", "proveedor_id", "proveedor_version",
                  "cuenta_ultimos_digitos", "importe_centavos", "moneda", "creada_en"},
    "ejecucion_resultado": {"id", "ejecucion_id", "resultado", "origen", "referencia", "motivo",
                            "registrado_en"},
    "apertura_cuenta": {"id", "ejecucion_id", "abierta_por_nombre", "abierta_en"},
    "comprobante": {"documento_id", "ejecucion_id", "atado_en"},
    "pago": {"id", "ejecucion_id", "corrige_a", "numero_operacion", "fecha_operacion",
             "importe_centavos", "moneda", "documento_id", "version_documento_id",
             "registrado_por_nombre", "registrado_en", "fuera_de_termino", "sin_facultad",
             "lectura_disputada"},
    "conciliacion": {"id", "pago_id", "resultado", "motivo", "conciliado_por_nombre",
                     "conciliado_en"},
    "diferencia_pago": {"id", "orden_id", "ejecucion_id", "tipo", "importe_real", "moneda",
                        "cuenta_ultimos_digitos", "numero_operacion", "fecha_operacion",
                        "documento_id", "version_documento_id", "explicacion",
                        "fuera_de_termino", "sin_facultad", "corrige_a",
                        "registrada_por_nombre",
                        "registrada_en",
                        "resolucion", "resuelta_por_nombre", "resuelta_en",
                        "recuperada_por_nombre", "recuperada_en", "cerrada_por_nombre",
                        "cerrada_en", "motivo_cierre"},
    "declaracion_no_pagado": {"id", "ejecucion_id", "motivo", "declarado_por_nombre",
                              "declarado_en", "sin_facultad"},
    "confirmacion_no_pagado": {"id", "declaracion_id", "confirmado_por_nombre", "confirmado_en"},
    "toma_pago": {"id", "ejecucion_id", "tomado_por_nombre", "tomada_en"},
    "cotejo": {"id", "pago_id", "diferencia_id", "origen", "movimiento_id",
               "cotejado_por_nombre", "cotejado_en", "anulado_por_nombre", "anulado_en",
               "motivo_anulacion"},
    "importacion_extracto": {"id", "hash_archivo", "desde", "hasta", "importado_por_nombre",
                             "importado_en"},
    "movimiento_extracto": {"id", "importacion_id", "hash_linea", "fecha", "importe_centavos",
                            "moneda", "numero_operacion"},
    "movimiento_no_es_pago": {"id", "movimiento_id", "marcado_por_nombre", "marcado_en", "motivo",
                              "retirada_por_nombre", "retirada_en", "motivo_retiro"},
    "columnas_extracto": {"columna_fecha", "columna_importe", "columna_numero", "signo", "moneda",
                          "guardadas_por_nombre", "guardadas_en"},
    "lectura_comprobante": {"id", "version_documento_id", "documento_id", "ejecucion_id", "modelo",
                            "version_prompt", "legible", "error", "importe", "moneda",
                            "cuenta_ultimos_digitos", "cuenta_coincide", "titular_destino",
                            "numero_operacion", "fecha", "leida_en"},
    "lectura_carga": {"id", "version_documento_id", "documento_id", "tipo", "modelo",
                      "version_prompt", "legible", "error", "cuit", "cuit_invalido",
                      "razon_social", "domicilio", "telefono", "correo", "numero_comprobante",
                      "fecha", "importe", "moneda", "concepto", "leida_en"},
    "liquidacion": {"id", "periodo", "version", "estado", "preparada_por_nombre", "creada_en",
                    "descartada_en", "descartada_por_nombre", "motivo_descarte"},
    "gasto": {"id", "liquidacion_id", "orden_id", "ejecucion_id", "importe_centavos", "moneda",
              "esquema_id", "agregado_por_nombre", "agregado_en"},
    "gasto_quitado": {"gasto_id", "quitado_por_nombre", "quitado_en"},
}  # fmt: skip


def _edificio(motor_migrate, motor_app, con_documentos: bool = True, externo: bool = False) -> dict:
    """Consorcio en Modo Consejo con:
    - un administrador (paquete completo) que carga los documentos;
    - un miembro del Consejo con el paquete completo (`mc`), uno limitado a `padron.ver`
      (`mc_padron`), uno con las cinco facultades pero `padron.ver` acotado a una unidad
      (`mc_acotado`) y uno con las cinco y ninguna económica (`mc_sin_economica`);
    - documentos con una versión PDF y una PNG `disponible`, una rechazada y una en cuarentena;
    - lo que nunca va: un evento con IP, la cadena de hashes, una negación, una contraseña, una
      sesión y la referencia del consentimiento de un contacto."""
    cons, uni, prop = consorcio_con_persona(motor_migrate)
    unidades = [uni] + [otra_unidad(motor_migrate, cons, f"{n}° A")[0] for n in range(2, 6)]
    with motor_migrate.begin() as c:
        c.execute(text("update nucleo.persona set nombre = 'Ana Exportada' where id = :p"),
                  {"p": prop})  # fmt: skip
    acta = decidir_equipo(motor_app, cons)
    cambiar(motor_app, cons, "consejo", acta)
    # El día del pedido es el del consorcio (Buenos Aires), no el del contenedor (UTC).
    e = {"cons": cons, "u": unidades, "prop": prop, "acta": acta, "hoy": hoy_de(motor_app, cons)}
    registrar(motor_app, cons, porcentajes(e, *EXPENSAS), decision=acta)

    def persona(nombre):
        p = otra_persona(motor_migrate, cons)
        with motor_migrate.begin() as c:
            c.execute(text("update nucleo.persona set nombre = :n where id = :p"),
                      {"n": nombre, "p": p})  # fmt: skip
        return p

    if externo:
        # La administración es un administrador externo con las cinco facultades generales. Su
        # paquete solo trae `documentos.cargar` (`alta_mandato` niega otorgarle más), así que se
        # le otorgan directo, como en `test_coeficientes_lectura.otorgar`. Va en lugar del
        # administrador: hay una sola administración a la vez (A14).
        e["ad"] = persona("Estudio Externo")
        e["m_ad"] = alta(motor_app, cons, None, acta, persona=e["ad"],
                         tipo="administrador_externo", nombre="Estudio Externo",
                         cuit="20111111112", origen="soporte")  # fmt: skip
        with motor_migrate.begin() as c:
            for f in FACULTADES:
                c.execute(
                    text("insert into nucleo.facultad_otorgada (consorcio_id, mandato_id,"
                         " facultad, decision_id) values (:c, :m, :f, :d)"),
                    {"c": cons, "m": e["m_ad"], "f": f, "d": acta},
                )  # fmt: skip
    else:
        e["ad"] = persona("Adela Administradora")
        e["m_ad"] = alta(motor_app, cons, None, acta, persona=e["ad"], tipo="administrador",
                         origen="soporte")  # fmt: skip
        # Una decisión que registra una persona: `registrada_por` es su identificador.
        e["decision_ad"] = decidir(motor_app, cons, e["ad"])
    e["mc"] = persona("Marta Consejera")
    e["m_mc"] = alta(motor_app, cons, None, acta, persona=e["mc"], tipo="miembro_consejo",
                     origen="soporte")  # fmt: skip
    e["mc_padron"] = persona("Pedro Padrón")
    alta(motor_app, cons, None, acta, persona=e["mc_padron"], tipo="miembro_consejo",
         facultades=["padron.ver"], origen="soporte")  # fmt: skip
    e["mc_acotado"] = persona("Ana Acotada")
    m = alta(motor_app, cons, None, acta, persona=e["mc_acotado"], tipo="miembro_consejo",
             facultades=[f for f in FACULTADES if f != "padron.ver"], origen="soporte")  # fmt: skip
    with motor_migrate.begin() as c:
        c.execute(
            text("insert into nucleo.facultad_otorgada (consorcio_id, mandato_id, facultad,"
                 " recurso, decision_id) values (:c, :m, 'padron.ver', :r, :d)"),
            {"c": cons, "m": m, "r": f"unidad:{uni}", "d": acta},
        )  # fmt: skip
    e["mc_sin_economica"] = persona("Elena Sin Económica")
    e["m_sin_economica"] = alta(motor_app, cons, None, acta, persona=e["mc_sin_economica"],
                                tipo="miembro_consejo", facultades=FACULTADES,
                                origen="soporte")  # fmt: skip
    if con_documentos:
        e |= _documentos(e, motor_migrate, motor_app)
    e |= _lo_que_nunca_va(e, motor_migrate)
    return e


def _documentos(e, motor_migrate, motor_app) -> dict:
    def documento(categoria, titulo):
        r = crear(e["ad"], e["cons"], categoria=categoria, titulo=titulo)
        assert r.status_code == 201, r.text
        return r.json()["id"]

    def version(doc, contenido, tipo):
        r = subir(e["ad"], e["cons"], doc, contenido, tipo)
        assert r.status_code == 201, r.text
        return r.json()["id"]

    acta, otros, malo = (documento("actas_y_decisiones", "Acta de asamblea"),
                         documento("otros", "Plano"), documento("seguros", "Póliza"))  # fmt: skip
    binarios = {version(acta, pdf(), "application/pdf"): pdf(),
                version(otros, PNG, "image/png"): PNG}  # fmt: skip
    rechazada = version(malo, pdf(js="app.alert(1)"), "application/pdf")
    while analizar(motor_app):  # cada lote toma un trabajo; el rechazo encola el borrado
        pass
    en_cuarentena = version(acta, JPEG, "image/jpeg")  # sin analizar
    with motor_migrate.connect() as c:
        estados = dict(c.execute(
            text("select id::text, estado from nucleo.version_documento where consorcio_id = :c"),
            {"c": e["cons"]},
        ).all())  # fmt: skip
    assert estados == {**{v: "disponible" for v in binarios}, rechazada: "rechazado",
                       en_cuarentena: "en_cuarentena"}  # fmt: skip
    return {"binarios": binarios, "rechazada": rechazada, "en_cuarentena": en_cuarentena,
            "documentos": {acta, otros, malo}}  # fmt: skip


def _lo_que_nunca_va(e, motor_migrate) -> dict:
    # Una negación de verdad, por la API: la propietaria no carga documentos.
    assert crear(e["prop"], e["cons"]).status_code == 403
    token = os.urandom(32)
    with motor_migrate.begin() as c:
        evento = c.execute(
            text("insert into auditoria.eventos (consorcio_id, actor_persona_id, origen, accion,"
                 " entidad, entidad_id, despues, ip, hash_previo, hash) values (:c, :p, 'api',"
                 " 'consorcio.decision.registrar', 'decision', :d, '{\"tipo\": \"asamblea\"}',"
                 " :ip, '', '') returning id"),
            {"c": e["cons"], "p": e["ad"], "d": e["acta"], "ip": IP},
        ).scalar_one()  # fmt: skip
        c.execute(text("insert into nucleo.contrasena (persona_id, hash) values (:p, :h)"),
                  {"p": e["ad"], "h": HASH_CONTRASENA})  # fmt: skip
        c.execute(
            text("insert into nucleo.sesion (persona_id, token_hash, dispositivo, ip, vence_en)"
                 " values (:p, :t, :d, :ip, now() + interval '1 day')"),
            {"p": e["ad"], "t": token, "d": uuid4(), "ip": IP},
        )  # fmt: skip
        c.execute(
            text("insert into nucleo.contacto_emergencia (consorcio_id, funcion, naturaleza,"
                 " nombre, telefono, consentimiento_referencia, vigente_desde) values (:c,"
                 " 'emergencias', 'profesional', 'Plomería Rápida', '+54 11 4000-0000', :r,"
                 " '2026-01-01')"),
            {"c": e["cons"], "r": CONSENTIMIENTO},
        )  # fmt: skip
    return {"evento_con_ip": evento, "token": token.hex()}


@pytest.fixture
def edificio(motor_migrate, motor_app, bucket):  # noqa: F811
    return _edificio(motor_migrate, motor_app)


@pytest.fixture
def con_url(monkeypatch):
    monkeypatch.setenv(exportar_consorcio.VARIABLE_URL, url_rol("soporte_exportacion"))


@pytest.fixture
def claves(tmp_path) -> dict:
    """Un par de claves `age` de prueba, generado en el momento."""
    privada = tmp_path / "destinatario.txt"
    hecho = subprocess.run(["age-keygen", "-o", str(privada)], capture_output=True, text=True)
    assert hecho.returncode == 0, hecho.stderr
    publica = next(linea.split(": ", 1)[1] for linea in privada.read_text().splitlines()
                   if linea.startswith("# public key: "))  # fmt: skip
    return {"privada": privada, "publica": publica}


def argumentos(e, claves, salida: Path, solicitante=None, **cambios) -> list[str]:
    valores = {
        "--consorcio": str(e["cons"]),
        "--referencia-solicitud": "Nota 3/2026 del Consejo",
        "--destinatario-nombre": "Estudio Nuevo SRL",
        "--destinatario-documento": "30712345671",
        "--solicitante": str(solicitante or e["mc"]),
        "--fecha-pedido": e["hoy"].isoformat(),
        "--referencia-respaldo": "Acta 45, libro 3, folio 12",
        "--clave-publica": claves["publica"],
        "--salida": str(salida),
    } | {f"--{k.replace('_', '-')}": v for k, v in cambios.items()}
    return ["armar"] + [x for k, v in valores.items() if v is not None for x in (k, v)]


def exportar(e, claves, tmp_path, solicitante=None, **cambios) -> tuple[int, Path]:
    salida = tmp_path / f"exportacion-{uuid4().hex[:8]}.zip.age"
    return exportar_consorcio.main(argumentos(e, claves, salida, solicitante, **cambios)), salida


def descifrar(salida: Path, privada: Path) -> zipfile.ZipFile:
    hecho = subprocess.run(["age", "--decrypt", "--identity", str(privada), str(salida)],
                           capture_output=True)  # fmt: skip
    assert hecho.returncode == 0, hecho.stderr
    return zipfile.ZipFile(io.BytesIO(hecho.stdout))


def entidades(paquete: zipfile.ZipFile) -> dict[str, list[dict]]:
    return {
        Path(nombre).stem: json.loads(paquete.read(nombre))
        for nombre in paquete.namelist()
        if nombre.endswith(".json") and nombre != exportar_consorcio.MANIFIESTO
    }


def constancias(motor_migrate, cons) -> list[dict]:
    with motor_migrate.connect() as c:
        return [dict(f) for f in c.execute(
            text("select * from plataforma.exportacion where consorcio_id = :c order by creada_en"),
            {"c": cons},
        ).mappings()]  # fmt: skip


def verificar_manifiesto(paquete: zipfile.ZipFile) -> list[str]:
    """Los archivos que no coinciden con el manifiesto (vacío si todo coincide)."""
    manifiesto = json.loads(paquete.read(exportar_consorcio.MANIFIESTO))
    listados = {a["ruta"]: a for a in manifiesto["archivos"]}
    problemas = sorted(set(paquete.namelist()) - set(listados) - {exportar_consorcio.MANIFIESTO})
    for ruta, a in listados.items():
        contenido = paquete.read(ruta) if ruta in paquete.namelist() else None
        if contenido is None or (exportar_consorcio.hashlib.sha256(contenido).hexdigest(),
                                 len(contenido)) != (a["sha256"], a["tamano"]):  # fmt: skip
            problemas.append(ruta)
    return problemas


# El paquete ---------------------------------------------------------------------------


def test_el_paquete_se_descifra_y_el_manifiesto_coincide(edificio, con_url, claves, tmp_path,
                                                         motor_migrate, capsys):  # fmt: skip
    e = edificio
    codigo, salida = exportar(e, claves, tmp_path)
    assert codigo == 0, capsys.readouterr().err
    impreso = capsys.readouterr().out
    paquete = descifrar(salida, claves["privada"])
    assert verificar_manifiesto(paquete) == []
    # Control: la verificación encuentra un archivo alterado, y otra clave privada no descifra.
    alterado = io.BytesIO()
    with zipfile.ZipFile(alterado, "w") as z:
        for nombre in paquete.namelist():
            contenido = paquete.read(nombre)
            z.writestr(nombre, contenido + b" " if nombre == "padron/unidad.json" else contenido)
    assert verificar_manifiesto(zipfile.ZipFile(alterado)) == ["padron/unidad.json"]
    otra = tmp_path / "otra.txt"
    subprocess.run(["age-keygen", "-o", str(otra)], capture_output=True, check=True)
    ajena = subprocess.run(["age", "--decrypt", "--identity", str(otra), str(salida)],
                           capture_output=True)  # fmt: skip
    assert ajena.returncode != 0 and not ajena.stdout
    # Los binarios son los que se subieron.
    datos = entidades(paquete)
    for v in datos["version"]:
        assert paquete.read(v["archivo"]) == e["binarios"][v["id"]]
    # Cada fila trae solo las claves decididas, y cada entidad con filas está.
    for entidad, filas in datos.items():
        for fila in filas:
            assert set(fila) == CLAVES_POR_ENTIDAD[entidad], entidad
    assert {k for k, v in datos.items() if v} == (
        set(CLAVES_POR_ENTIDAD) - {"revision"} - ENTIDADES_ECONOMICAS
    )
    # El padrón vuelve con los nombres.
    assert "Ana Exportada" in {v["nombre"] for v in datos["vinculo"]}
    # La constancia: el paquete cifrado que quedó en disco, con su suma y su tamaño.
    [fila] = constancias(motor_migrate, e["cons"])
    suma, tamano = exportar_consorcio._sha256(salida)
    assert (fila["paquete_sha256"], fila["paquete_tamano"]) == (suma, tamano)
    assert (fila["solicitante"], fila["mandato_id"], fila["fecha_pedido"]) == (
        e["mc"], e["m_mc"], e["hoy"])  # fmt: skip
    assert (fila["destinatario_nombre"], fila["destinatario_documento"]) == (
        "Estudio Nuevo SRL", "30712345671")  # fmt: skip
    assert fila["solicitud_referencia"] == "Nota 3/2026 del Consejo"
    assert fila["respaldo_referencia"] == "Acta 45, libro 3, folio 12"
    assert fila["facultades_verificadas"] == FACULTADES + EGRESOS_DEL_CONSEJO
    assert fila["armada_en"] is not None and fila["entregada_en"] is None
    assert fila["cantidades"] == {k: len(v) for k, v in datos.items()} | {"archivos": 2}
    assert f"exportacion: {fila['id']}" in impreso and f"sha256: {suma}" in impreso


def _textos_del_paquete(paquete: zipfile.ZipFile) -> str:
    return "\n".join(paquete.read(n).decode("latin-1") for n in paquete.namelist()).lower()


def _textos_de_la_base(motor_migrate, e) -> str:
    """Las mismas tablas de las que sale el paquete, fila entera como texto."""
    consultas = [
        "select x::text from auditoria.eventos x where x.consorcio_id = :c",
        "select x::text from nucleo.sesion x where x.persona_id = :ad",
        "select x::text from nucleo.contrasena x where x.persona_id = :ad",
        "select x::text from nucleo.contacto_emergencia x where x.consorcio_id = :c",
        "select x::text from nucleo.version_documento x where x.consorcio_id = :c",
    ]
    with motor_migrate.connect() as c:
        return "\n".join(
            t for q in consultas
            for t in c.execute(text(q), {"c": e["cons"], "ad": e["ad"]}).scalars()
        ).lower()  # fmt: skip


def _lo_que_nunca_va_buscado(motor_migrate, e) -> dict[str, list[str]]:
    with motor_migrate.connect() as c:
        hashes = c.execute(
            text("select hash from auditoria.eventos where consorcio_id = :c union"
                 " select hash_previo from auditoria.eventos where consorcio_id = :c"
                 " and hash_previo <> ''"), {"c": e["cons"]},
        ).scalars().all()  # fmt: skip
        objetos = c.execute(
            text("select clave_objeto from nucleo.version_documento where consorcio_id = :c"
                 " and clave_objeto is not null"), {"c": e["cons"]},
        ).scalars().all()  # fmt: skip
    assert len(hashes) > 5 and len(objetos) >= 3  # la búsqueda tiene qué buscar
    return {
        "ip": [IP],
        "hashes de la cadena": hashes,
        "hash de la contraseña": [HASH_CONTRASENA.lower()],
        "token de la sesión": [e["token"]],
        "referencia del consentimiento": [CONSENTIMIENTO.lower()],
        "claves de los objetos": objetos,
        "antivirus": ["clamav"],
    }


def _encontrados(buscado: dict[str, list[str]], textos: str) -> dict[str, int]:
    return {que: sum(t.lower() in textos for t in valores) for que, valores in buscado.items()}


def test_lo_que_nunca_va_no_esta_en_el_paquete_y_si_en_la_base(
    edificio, con_url, claves, tmp_path, motor_migrate
):
    e = edificio
    codigo, salida = exportar(e, claves, tmp_path)
    assert codigo == 0
    buscado = _lo_que_nunca_va_buscado(motor_migrate, e)
    en_el_paquete = _encontrados(buscado, _textos_del_paquete(descifrar(salida, claves["privada"])))
    assert en_el_paquete == {que: 0 for que in buscado}
    # Control: la misma búsqueda, en la base de la que salió, los encuentra todos.
    en_la_base = _encontrados(buscado, _textos_de_la_base(motor_migrate, e))
    assert en_la_base == {que: len(valores) for que, valores in buscado.items()}
    # Y el evento que tenía la IP sí va, sin ella, por la proyección.
    datos = entidades(descifrar(salida, claves["privada"]))
    assert any(ev["accion"] == "Registró una decisión" and ev["actor"] == "persona"
               for ev in datos["evento"]), datos["evento"]  # fmt: skip


PERSONAS_DEL_CONSORCIO = """
    select x::text, p from (
      select v::text as x, v.persona_id as p from nucleo.vinculo v where v.consorcio_id = :c
      union all select m::text, m.persona_id from nucleo.mandato m where m.consorcio_id = :c
      union all select d::text, d.registrada_por from nucleo.decision_formal d
                 where d.consorcio_id = :c
      union all select e::text, e.actor_persona_id from auditoria.eventos e
                 where e.consorcio_id = :c
      union all select d::text, d.cargado_por from nucleo.documento d where d.consorcio_id = :c
      union all select v::text, v.subida_por from nucleo.version_documento v
                 where v.consorcio_id = :c
      union all select k::text, k.actualizado_por from nucleo.contacto_emergencia k
                 where k.consorcio_id = :c
      union all select k::text, k.verificado_por from nucleo.contacto_emergencia k
                 where k.consorcio_id = :c
      union all select k::text, k.observado_por from nucleo.contacto_emergencia k
                 where k.consorcio_id = :c
      union all select m::text, m.registrado_por from nucleo.mandato m where m.consorcio_id = :c
      union all select r::text, r.registrada_por from nucleo.revision_mandato r
                 where r.consorcio_id = :c
    ) t (x, p) where p is not null
"""


def test_ningun_identificador_de_persona_va_en_el_paquete(edificio, con_url, claves, tmp_path,
                                                         motor_migrate):  # fmt: skip
    """La API no muestra el identificador de las personas (salvo a quien da un alta): cruzado
    entre consorcios, identifica a la misma cuenta. Ninguno de los que nombran las tablas de las
    que sale el paquete va en él."""
    e = edificio
    codigo, salida = exportar(e, claves, tmp_path)
    assert codigo == 0
    with motor_migrate.connect() as c:
        filas = c.execute(text(PERSONAS_DEL_CONSORCIO), {"c": e["cons"]}).all()
    personas = {str(f.p) for f in filas}
    # La búsqueda tiene qué buscar: la decisión que registró la administradora, entre otros.
    assert {str(e["ad"]), str(e["mc"]), str(e["prop"])} <= personas
    paquete = _textos_del_paquete(descifrar(salida, claves["privada"]))
    assert {p for p in personas if p in paquete} == set()
    # Control: la misma búsqueda, en las filas de la base que los nombran, los encuentra todos.
    base = " ".join(f.x for f in filas)
    assert {p for p in personas if p in base} == personas


def test_la_auditoria_lleva_exactamente_los_eventos_proyectados(
    edificio, con_url, claves, tmp_path, motor_migrate
):
    """El paquete muestra el texto de la acción, no su código: buscar `autorizacion.denegada` en
    él no puede fallar. Esto sí: los eventos del paquete son, uno por uno y en orden, los de la
    base con una acción de la proyección y que no son de soporte; la negación sembrada y los
    eventos de soporte quedan afuera."""
    e = edificio
    # Desde S2b (0043) soporte ya no escribe eventos, pero los anteriores siguen en la base (en
    # staging, por ejemplo): se siembra uno, con una acción de la proyección.
    with motor_migrate.begin() as c:
        auditoria.registrar(c, consorcio_id=e["cons"], actor=None, origen="soporte",
                            accion="consorcio.decision.registrar", entidad="decision",
                            entidad_id=uuid4(), despues={"tipo": "asamblea"})  # fmt: skip
    codigo, salida = exportar(e, claves, tmp_path)
    assert codigo == 0
    del_paquete = [(datetime.fromisoformat(ev["fecha"]), ev["accion"])
                   for ev in entidades(descifrar(salida, claves["privada"]))["evento"]]  # fmt: skip
    with motor_migrate.connect() as c:
        proyectados = [tuple(f) for f in c.execute(
            text("select e.creado_en, p.texto from auditoria.eventos e"
                 " join auditoria.proyeccion p on p.accion = e.accion"
                 " where e.consorcio_id = :c and e.origen <> 'soporte'"
                 " order by e.creado_en, e.secuencia"), {"c": e["cons"]},
        )]  # fmt: skip
        por_tipo = dict(c.execute(
            text("select case when e.accion = 'autorizacion.denegada' then 'negacion'"
                 " when e.origen = 'soporte' then 'soporte' else 'otro' end, count(*)"
                 " from auditoria.eventos e where e.consorcio_id = :c group by 1"),
            {"c": e["cons"]},
        ).all())  # fmt: skip
    # Control: hay qué dejar afuera (la negación de la propietaria y lo que cargó el equipo).
    assert por_tipo["negacion"] >= 1 and por_tipo["soporte"] >= 1
    assert len(proyectados) < sum(por_tipo.values())
    assert del_paquete == proyectados
    # Y ninguno es una negación, aunque la proyección llegara a incluirla: ningún evento
    # `autorizacion.denegada` de la base tiene la fecha de un evento del paquete.
    with motor_migrate.connect() as c:
        negaciones = set(c.execute(
            text("select creado_en from auditoria.eventos where consorcio_id = :c"
                 " and accion = 'autorizacion.denegada'"), {"c": e["cons"]},
        ).scalars())  # fmt: skip
    assert negaciones and not negaciones & {fecha for fecha, _ in del_paquete}


def test_las_versiones_en_cuarentena_o_rechazadas_no_van(edificio, con_url, claves, tmp_path):
    e = edificio
    codigo, salida = exportar(e, claves, tmp_path)
    assert codigo == 0
    paquete = descifrar(salida, claves["privada"])
    versiones = {v["id"] for v in entidades(paquete)["version"]}
    archivos = " ".join(paquete.namelist())
    for fuera in (e["rechazada"], e["en_cuarentena"]):
        assert fuera not in versiones and fuera not in archivos
    # Control: las `disponible` sí, con su binario.
    assert versiones == set(e["binarios"])
    assert all(v in archivos for v in e["binarios"])


def test_exportar_no_escribe_eventos_de_auditoria(edificio, con_url, claves, tmp_path,
                                                  motor_migrate):  # fmt: skip
    e = edificio
    contar = "select count(*) from auditoria.eventos where consorcio_id = :c"
    # Control: la consulta cuenta un evento que sí existe (el sembrado con la IP).
    with motor_migrate.connect() as c:
        antes = c.execute(text(contar), {"c": e["cons"]}).scalar_one()
        assert c.execute(text(contar + " and id = :e"),
                         {"c": e["cons"], "e": e["evento_con_ip"]}).scalar_one() == 1  # fmt: skip
        ultimo = c.execute(text("select max(secuencia) from auditoria.eventos")).scalar_one()
    assert antes > 0
    assert exportar(e, claves, tmp_path)[0] == 0
    with motor_migrate.connect() as c:
        assert c.execute(text(contar), {"c": e["cons"]}).scalar_one() == antes
        # En ningún consorcio: soporte no escribe en la auditoría.
        assert c.execute(text("select count(*) from auditoria.eventos where secuencia > :s"),
                         {"s": ultimo}).scalar_one() == 0  # fmt: skip


# Quién la puede pedir -----------------------------------------------------------------


@pytest.mark.parametrize(
    "caso,cambios",
    [
        ("sin destinatario", {"destinatario_nombre": "  "}),
        ("sin documento del destinatario", {"destinatario_documento": ""}),
        ("sin referencia de la solicitud", {"referencia_solicitud": " "}),
        ("sin referencia del respaldo", {"referencia_respaldo": ""}),
        ("solicitante sin mandato", {"solicitante": "prop"}),
        ("consejo limitado a padron.ver", {"solicitante": "mc_padron"}),
        ("padron.ver acotado a una unidad", {"solicitante": "mc_acotado"}),
        ("sin ninguna facultad económica", {"solicitante": "mc_sin_economica"}),
        ("antes del mandato", {"fecha_pedido": "2025-12-31"}),
    ],
)
def test_se_niega_sin_escribir_nada(edificio, con_url, claves, tmp_path, motor_migrate, capsys,
                                    caso, cambios):  # fmt: skip
    e = edificio
    if "solicitante" in cambios:
        cambios = cambios | {"solicitante": str(e[cambios["solicitante"]])}
    codigo, salida = exportar(e, claves, tmp_path, **cambios)
    assert codigo == 1, caso
    assert "no se escribió nada" in capsys.readouterr().err
    assert not salida.exists() and constancias(motor_migrate, e["cons"]) == []
    # Control: el mismo pedido de un miembro del Consejo con el paquete completo pasa.
    assert exportar(e, claves, tmp_path)[0] == 0
    assert len(constancias(motor_migrate, e["cons"])) == 1


@pytest.mark.parametrize("falta", ["--destinatario-nombre", "--destinatario-documento",
                                   "--referencia-solicitud", "--referencia-respaldo"])  # fmt: skip
def test_sin_el_argumento_no_corre(edificio, con_url, claves, tmp_path, motor_migrate, falta):
    e = edificio
    salida = tmp_path / "x.zip.age"
    a = argumentos(e, claves, salida)
    i = a.index(falta)
    with pytest.raises(SystemExit) as fin:
        exportar_consorcio.main(a[:i] + a[i + 2 :])
    assert fin.value.code == 2
    assert not salida.exists() and constancias(motor_migrate, e["cons"]) == []
    assert exportar_consorcio.main(a) == 0  # control


def test_la_administracion_tambien_la_pide(edificio, con_url, claves, tmp_path, motor_migrate):
    e = edificio
    assert exportar(e, claves, tmp_path, solicitante=str(e["ad"]))[0] == 0
    assert constancias(motor_migrate, e["cons"])[0]["mandato_id"] == e["m_ad"]


def test_una_clave_publica_que_age_no_acepta_se_niega(edificio, con_url, claves, tmp_path,
                                                      motor_migrate, capsys):  # fmt: skip
    e = edificio
    codigo, salida = exportar(e, claves, tmp_path, clave_publica="age1noesunaclave")
    assert codigo == 1 and "age" in capsys.readouterr().err
    assert not salida.exists() and constancias(motor_migrate, e["cons"]) == []


def test_no_sobrescribe_un_archivo(edificio, con_url, claves, tmp_path, motor_migrate, capsys):
    e = edificio
    salida = tmp_path / "ya.zip.age"
    salida.write_bytes(b"otro")
    assert exportar_consorcio.main(argumentos(e, claves, salida)) == 1
    assert "ya existe" in capsys.readouterr().err
    assert salida.read_bytes() == b"otro" and constancias(motor_migrate, e["cons"]) == []


def test_un_archivo_que_aparece_en_el_medio_no_se_toca(
    edificio, con_url, claves, tmp_path, motor_migrate, monkeypatch, capsys
):
    """Otro proceso crea el archivo de salida justo antes de que el script lo abra: se niega, el
    archivo ajeno queda intacto y no hay constancia."""
    e = edificio
    salida = tmp_path / "carrera.zip.age"
    original = exportar_consorcio._exigir_clave_age

    def y_aparece(clave):
        original(clave)
        salida.write_bytes(b"de otro proceso")

    monkeypatch.setattr(exportar_consorcio, "_exigir_clave_age", y_aparece)
    assert exportar_consorcio.main(argumentos(e, claves, salida)) == 1
    assert "ya existe" in capsys.readouterr().err
    assert salida.read_bytes() == b"de otro proceso"
    assert constancias(motor_migrate, e["cons"]) == []
    # Control: sin el otro proceso, la misma salida (libre) se arma.
    salida.unlink()
    monkeypatch.setattr(exportar_consorcio, "_exigir_clave_age", original)
    assert exportar_consorcio.main(argumentos(e, claves, salida)) == 0
    assert salida.read_bytes().startswith(b"age-encryption.org/v1")


def test_si_el_commit_falla_no_queda_el_paquete(edificio, con_url, claves, tmp_path,
                                                motor_migrate, monkeypatch, capsys):  # fmt: skip
    """La transacción falla al terminar, después de armar y de registrar el armado: no queda la
    fila, y tampoco el archivo cifrado (se da por bueno recién después del commit)."""
    e = edificio
    original = exportar_consorcio.transaccion

    @contextmanager
    def falla_al_confirmar(motor, **ambito):
        with original(motor, **ambito) as conn:
            yield conn
            conn.execute(text("select 1 / 0"))  # una falla de la base al cerrar la transacción

    monkeypatch.setattr(exportar_consorcio, "transaccion", falla_al_confirmar)
    codigo, salida = exportar(e, claves, tmp_path)
    assert codigo == 1 and "no se escribió nada" in capsys.readouterr().err
    assert not salida.exists() and constancias(motor_migrate, e["cons"]) == []
    # Control: sin la falla, el mismo pedido deja el archivo y la fila.
    monkeypatch.setattr(exportar_consorcio, "transaccion", original)
    codigo, salida = exportar(e, claves, tmp_path)
    assert codigo == 0 and salida.exists() and len(constancias(motor_migrate, e["cons"])) == 1


def test_un_binario_que_no_coincide_no_deja_paquete_ni_constancia(edificio, con_url, claves,
                                                                  tmp_path, motor_migrate,
                                                                  bucket):  # noqa: F811  # fmt: skip
    e = edificio
    v = next(iter(e["binarios"]))
    with motor_migrate.connect() as c:
        clave = c.execute(text("select clave_objeto from nucleo.version_documento where id = :v"),
                          {"v": v}).scalar_one()  # fmt: skip
    _s3().put_object(Bucket=bucket, Key=clave, Body=b"%PDF-otro contenido")
    salida = tmp_path / "x.zip.age"
    with pytest.raises(exportar_consorcio.ArchivoQueNoCoincide):
        exportar_consorcio.main(argumentos(e, claves, salida))
    assert not salida.exists() and constancias(motor_migrate, e["cons"]) == []


def test_con_la_url_de_otro_rol_se_niega(edificio, monkeypatch, claves, tmp_path, motor_migrate,
                                         capsys):  # fmt: skip
    e = edificio
    monkeypatch.setenv(exportar_consorcio.VARIABLE_URL, url_rol("app"))
    codigo, salida = exportar(e, claves, tmp_path)
    assert codigo == 1 and "conecta como cem_app" in capsys.readouterr().err
    assert not salida.exists() and constancias(motor_migrate, e["cons"]) == []


def test_sin_la_variable_no_corre(edificio, monkeypatch, claves, tmp_path, capsys):
    monkeypatch.delenv(exportar_consorcio.VARIABLE_URL, raising=False)
    assert exportar(edificio, claves, tmp_path)[0] == 2
    assert exportar_consorcio.VARIABLE_URL in capsys.readouterr().err


# La entrega ---------------------------------------------------------------------------


def test_la_entrega_se_registra_una_vez(edificio, con_url, claves, tmp_path, motor_migrate,
                                        capsys):  # fmt: skip
    e = edificio
    codigo, salida = exportar(e, claves, tmp_path)
    assert codigo == 0
    [fila] = constancias(motor_migrate, e["cons"])
    # Otro archivo (otro SHA-256) no se da por entregado.
    otro_archivo = tmp_path / "otro.zip.age"
    otro_archivo.write_bytes(b"no es el paquete")
    entregar = ["entregar", "--consorcio", str(e["cons"]), "--exportacion", str(fila["id"])]
    assert exportar_consorcio.main(entregar + ["--paquete", str(otro_archivo)]) == 1
    assert "otro SHA-256" in capsys.readouterr().err
    assert exportar_consorcio.main(entregar + ["--paquete", str(tmp_path / "no-esta")]) == 1
    assert "no existe o no es un archivo" in capsys.readouterr().err
    assert constancias(motor_migrate, e["cons"])[0]["entregada_en"] is None
    entregar += ["--paquete", str(salida)]
    assert exportar_consorcio.main(entregar) == 0
    assert f"sha256: {fila['paquete_sha256']}" in capsys.readouterr().out
    entregada = constancias(motor_migrate, e["cons"])[0]["entregada_en"]
    assert entregada is not None
    assert exportar_consorcio.main(entregar) == 1
    assert "entrega_registrada" in capsys.readouterr().err
    assert constancias(motor_migrate, e["cons"])[0]["entregada_en"] == entregada
    # Una exportación que no existe en el consorcio del contexto.
    assert exportar_consorcio.main(entregar[:3] + ["--exportacion", str(uuid4()),
                                                   "--paquete", str(salida)]) == 1  # fmt: skip
    otro = consorcio_con_persona(motor_migrate)[0]
    assert exportar_consorcio.main(["entregar", "--consorcio", str(otro), "--exportacion",
                                    str(fila["id"]), "--paquete", str(salida)]) == 1  # fmt: skip
    assert "no existe en este consorcio" in capsys.readouterr().err


def test_el_armado_registrado_no_cambia(edificio, con_url, claves, tmp_path, motor_migrate):
    e = edificio
    assert exportar(e, claves, tmp_path)[0] == 0
    [fila] = constancias(motor_migrate, e["cons"])
    with pytest.raises(DBAPIError) as error:
        _como_soporte(e["cons"], "update plataforma.exportacion set paquete_sha256 = :s"
                                 " where id = :id", {"s": "0" * 64, "id": fila["id"]})  # fmt: skip
    assert error.value.orig.diag.message_hint == "armado_registrado"
    # Ni las columnas del pedido: el rol no tiene `UPDATE` sobre ellas.
    with pytest.raises(DBAPIError) as error:
        _como_soporte(e["cons"], "update plataforma.exportacion set destinatario_nombre = 'X'"
                                 " where id = :id", {"id": fila["id"]})  # fmt: skip
    assert error.value.orig.sqlstate == "42501"
    # Control: la entrega, que todavía no está, sí se registra.
    r = _como_soporte(e["cons"], "update plataforma.exportacion set entregada_en = now()"
                                 " where id = :id", {"id": fila["id"]})  # fmt: skip
    assert r.rowcount == 1


# El rol y las funciones ---------------------------------------------------------------


def _como_soporte(cons, sql, parametros=None):
    motor = create_engine(url_rol("soporte_exportacion"))
    try:
        with transaccion(motor, persona_id=None, consorcio_id=cons) as c:
            return c.execute(text(sql), parametros or {})
    finally:
        motor.dispose()


@pytest.fixture
def sin_documentos(motor_migrate, motor_app):
    return _edificio(motor_migrate, motor_app, con_documentos=False)


REGISTRAR = (
    "select * from nucleo.registrar_exportacion(:c, 'Nota 1', 'Estudio', '30712345671',"
    " :p, :h, 'Acta 1')"
)


def pedido(e, solicitante=None, cons=None) -> object:
    """Una constancia registrada por `registrar_exportacion`, como el rol desde la base, en su
    propia transacción (confirmada)."""
    cons = cons or e["cons"]
    return _como_soporte(cons, REGISTRAR, {"c": cons, "p": solicitante or e["mc"],
                                           "h": e["hoy"]}).one()  # fmt: skip


def _registrar_en(c, e, solicitante=None):
    """Registra en la transacción `c`, que tiene que tener el consorcio de `e` en el contexto."""
    return c.execute(text(REGISTRAR), {"c": e["cons"], "p": solicitante or e["mc"],
                                       "h": e["hoy"]}).one()  # fmt: skip


def _en_una_transaccion(cons, pasos):
    """Corre `pasos(c)` como el rol, en una sola transacción con `cons` en el contexto."""
    motor = create_engine(url_rol("soporte_exportacion"))
    try:
        with transaccion(motor, persona_id=None, consorcio_id=cons) as c:
            return pasos(c)
    finally:
        motor.dispose()


@pytest.mark.parametrize("funcion", FUNCIONES)
def test_cada_funcion_exige_el_rol_el_consorcio_y_una_exportacion_sin_armar(
    sin_documentos, motor_migrate, motor_app, funcion
):
    """Con la URL del rol no alcanza para leer un consorcio desde la base: cada función exige una
    constancia del consorcio del contexto, registrada (con la capacidad comprobada) en la misma
    transacción y todavía sin armar. Cada caso registra la suya en su transacción, para que lo
    único que falte sea lo que prueba."""
    e = sin_documentos
    otro = _edificio(motor_migrate, motor_app, con_documentos=False)
    llamada = text(f"select * from nucleo.{funcion}(:c, :e)")
    with (
        pytest.raises(DBAPIError) as error,
        transaccion(motor_app, persona_id=e["mc"], consorcio_id=e["cons"]) as c,
    ):
        c.execute(llamada, {"c": e["cons"], "e": uuid4()})
    assert error.value.orig.sqlstate == "42501"  # cem_app, aun con el contexto correcto

    def con_contexto(contexto):
        def pasos(c):
            propio = _registrar_en(c, e).id
            fijar_ambito(c, persona_id=None, consorcio_id=contexto)
            return c.execute(llamada, {"c": e["cons"], "e": propio}).all()

        return pasos

    def con_pedido(elegir):
        def pasos(c):
            propio = _registrar_en(c, e).id
            return c.execute(llamada, {"c": e["cons"], "e": elegir(c, propio)}).all()

        return pasos

    def de_otro_consorcio(c, _):
        fijar_ambito(c, persona_id=None, consorcio_id=otro["cons"])
        suyo = _registrar_en(c, otro).id
        fijar_ambito(c, persona_id=None, consorcio_id=e["cons"])
        return suyo

    def ya_armada(c, propio):
        c.execute(text("update plataforma.exportacion set armada_en = clock_timestamp(),"
                       " paquete_sha256 = repeat('0', 64), paquete_tamano = 1,"
                       " cantidades = '{}' where id = :e"), {"e": propio})  # fmt: skip
        return propio

    casos = {
        "sin consorcio en el contexto": con_contexto(None),
        "con otro consorcio en el contexto": con_contexto(otro["cons"]),
        "sin exportación": con_pedido(lambda c, _: None),
        "una que no existe": con_pedido(lambda c, _: uuid4()),
        "la de otro consorcio": con_pedido(de_otro_consorcio),
        "una ya armada": con_pedido(ya_armada),
    }
    for caso, pasos in casos.items():
        with pytest.raises(DBAPIError) as error:
            _en_una_transaccion(e["cons"], pasos)
        assert error.value.orig.sqlstate == "42501", caso
    # Control: con la recién registrada, en la misma transacción, responde.
    _en_una_transaccion(e["cons"], con_pedido(lambda c, propio: propio))


@pytest.mark.parametrize("funcion", FUNCIONES)
def test_una_exportacion_confirmada_no_se_lee_en_otra_transaccion(sin_documentos, funcion):
    """Registrada y confirmada, la constancia no abre las lecturas en otra transacción: si no,
    con la URL del rol se leería el consorcio cuantas veces se quisiera, sin armar nunca."""
    e = sin_documentos
    llamada = text(f"select * from nucleo.{funcion}(:c, :e)")
    confirmada = pedido(e).id
    with pytest.raises(DBAPIError) as error:
        _en_una_transaccion(
            e["cons"], lambda c: c.execute(llamada, {"c": e["cons"], "e": confirmada}).all()
        )
    assert error.value.orig.sqlstate == "42501"

    # Control: registrada en la misma transacción, responde.
    def en_la_misma(c):
        return c.execute(llamada, {"c": e["cons"], "e": _registrar_en(c, e).id}).all()

    _en_una_transaccion(e["cons"], en_la_misma)


def test_con_el_contexto_correcto_el_padron_vuelve_con_nombres(sin_documentos):
    e = sin_documentos
    filas = _en_una_transaccion(e["cons"], lambda c: c.execute(
        text("select entidad, fila from nucleo.exportar_padron(:c, :e)"),
        {"c": e["cons"], "e": _registrar_en(c, e).id}).all())  # fmt: skip
    nombres = {f.fila["nombre"] for f in filas if f.entidad == "vinculo"}
    assert {"Ana Exportada", "Marta Consejera"} <= nombres
    assert {f.entidad for f in filas} == {"consorcio", "unidad", "vinculo"}


def test_registrar_exportacion_fija_la_capacidad_adentro(sin_documentos, motor_migrate):
    """Quien llama no elige las facultades ni los tipos: un solicitante sin la capacidad no deja
    constancia. Control: el Consejo con el paquete completo sí, con las cinco verificadas."""
    e = sin_documentos
    for quien in ("prop", "mc_padron", "mc_acotado", "mc_sin_economica"):
        with pytest.raises(DBAPIError) as error:
            pedido(e, e[quien])
        assert error.value.orig.diag.message_hint == "sin_capacidad", quien
    assert constancias(motor_migrate, e["cons"]) == []
    fila = pedido(e)
    assert fila.mandato_id == e["m_mc"]
    [constancia] = constancias(motor_migrate, e["cons"])
    assert constancia["facultades_verificadas"] == FACULTADES + EGRESOS_DEL_CONSEJO
    assert constancia["armada_en"] is None and constancia["entregada_en"] is None


@pytest.mark.parametrize(
    "sql",
    [
        "select count(*) from nucleo.persona",
        "select count(*) from nucleo.vinculo",
        "select count(*) from nucleo.documento",
        "select count(*) from nucleo.mandato",
        "select count(*) from auditoria.eventos",
        "select count(*) from nucleo.sesion",
        "delete from plataforma.exportacion",
        # `creada_en` decide qué exportación habilita las lecturas: el rol no la puede tocar.
        "update plataforma.exportacion set creada_en = now()",
        "insert into plataforma.exportacion (consorcio_id, solicitud_referencia,"
        " destinatario_nombre, destinatario_documento, solicitante, mandato_id,"
        " facultades_verificadas, fecha_pedido, respaldo_referencia, armada_en, paquete_sha256,"
        " paquete_tamano, cantidades) select consorcio_id, 'r', 'd', '1', solicitante, mandato_id,"
        " facultades_verificadas, fecha_pedido, 'r', now(), repeat('0', 64), 1, '{}'"
        " from plataforma.exportacion",
        "insert into auditoria.eventos (consorcio_id, origen, accion, entidad, hash_previo, hash)"
        " values (gen_random_uuid(), 'soporte', 'x', 'x', '', '')",
    ],
)
def test_el_rol_no_lee_tablas_ni_escribe_mas_que_la_constancia(sin_documentos, sql):
    e = sin_documentos
    with pytest.raises(DBAPIError) as error:
        _como_soporte(e["cons"], sql)
    assert error.value.orig.sqlstate == "42501"
    # Control: la constancia sí la lee.
    assert _como_soporte(e["cons"], "select count(*) from plataforma.exportacion").scalar_one() == 0


def test_la_constancia_no_la_lee_cem_app_y_el_rol_ve_solo_su_consorcio(edificio, con_url, claves,
                                                                      tmp_path, motor_app,
                                                                      motor_migrate):  # fmt: skip
    e = edificio
    assert exportar(e, claves, tmp_path)[0] == 0
    with (
        pytest.raises(DBAPIError) as error,
        transaccion(motor_app, persona_id=e["mc"], consorcio_id=e["cons"]) as c,
    ):
        c.execute(text("select count(*) from plataforma.exportacion"))
    assert error.value.orig.sqlstate == "42501"
    contar = "select count(*) from plataforma.exportacion"
    otro = consorcio_con_persona(motor_migrate)[0]
    assert _como_soporte(None, contar).scalar_one() == 0
    assert _como_soporte(otro, contar).scalar_one() == 0
    assert _como_soporte(e["cons"], contar).scalar_one() == 1  # control
    # Ni escribir una constancia de otro consorcio.
    with pytest.raises(DBAPIError) as error:
        _como_soporte(otro, "insert into plataforma.exportacion (consorcio_id,"
                            " solicitud_referencia, destinatario_nombre, destinatario_documento,"
                            " solicitante, mandato_id, facultades_verificadas, fecha_pedido,"
                            " respaldo_referencia) values (:c, 'r', 'd', '1', :p, :m, '{x}',"
                            " current_date, 'r')",
                      {"c": e["cons"], "p": e["mc"], "m": e["m_mc"]})  # fmt: skip
    assert error.value.orig.sqlstate == "42501"


def test_de_las_funciones_security_definer_el_rol_ejecuta_solo_las_suyas(motor_migrate):
    consulta = (
        "select p.oid::regprocedure::text from pg_proc p"
        " join pg_namespace n on n.oid = p.pronamespace"
        " where n.nspname in ('nucleo', 'plataforma', 'auditoria') and p.prosecdef"
        " and has_function_privilege(:rol, p.oid, 'execute')"
    )
    with motor_migrate.connect() as c:
        del_rol = set(c.execute(text(consulta), {"rol": "cem_soporte_exportacion"}).scalars())
        de_app = set(c.execute(text(consulta), {"rol": "cem_app"}).scalars())
        de_documentos = set(c.execute(text(consulta),
                                      {"rol": "cem_soporte_documentos"}).scalars())  # fmt: skip
    assert len(de_app) > 10  # control: la búsqueda encuentra las de cem_app
    assert del_rol == {f"nucleo.{f}(uuid,uuid)" for f in FUNCIONES} | {
        "nucleo.registrar_exportacion(uuid,text,text,text,uuid,date,text)"}  # fmt: skip
    assert not del_rol & de_app
    assert de_documentos == {"nucleo.capacidad_del_solicitante(uuid,uuid,text[],date)"}
    # La capacidad interna no la ejecuta nadie más que su dueño.
    with motor_migrate.connect() as c:
        quienes = c.execute(
            text("select r.rolname from pg_roles r where r.rolname like 'cem\\_%' and"
                 " has_function_privilege(r.oid,"
                 " 'nucleo.capacidad_para_exportar(uuid,uuid,date)', 'execute')"),
        ).scalars().all()  # fmt: skip
    assert set(quienes) == {"cem_migrate"}


def test_crear_roles_sin_la_clave_nueva_no_crea_el_rol_y_lo_dice():
    from sqlalchemy.engine import make_url

    from tests.conftest import ADMIN_URL, BASE, CLAVES, RAIZ

    libpq = make_url(ADMIN_URL).set(drivername="postgresql").render_as_string(False)
    comun = ["psql", libpq, "-q", "-v", f"base={BASE}",
             "-v", f"clave_migrate={CLAVES['migrate']}", "-v", f"clave_app={CLAVES['app']}",
             "-v", f"clave_ro={CLAVES['ro']}", "-v", f"clave_backup={CLAVES['backup']}",
             "-v", f"clave_soporte_documentos={CLAVES['soporte_documentos']}",
             "-f", str(RAIZ / "scripts" / "crear_roles.sql")]  # fmt: skip
    hecho = subprocess.run(comun, capture_output=True, text=True)
    assert hecho.returncode == 0, hecho.stderr
    assert "cem_soporte_exportacion no se crea" in hecho.stdout
    # La de la copia continua (2c-4) va también: sin ella, su rol tampoco se crea y lo dice.
    con = comun[:-2] + ["-v", f"clave_soporte_exportacion={CLAVES['soporte_exportacion']}",
                        "-v", f"clave_copia_continua={CLAVES['copia_continua']}",
                        "-v", f"clave_soporte_privacidad={CLAVES['soporte_privacidad']}",
                        *comun[-2:]]  # fmt: skip
    hecho = subprocess.run(con, capture_output=True, text=True)
    assert hecho.returncode == 0 and "no se crea" not in hecho.stdout, hecho.stderr  # control
    # El rol quedó como lo pide el diseño (§1.1).
    motor = create_engine(ADMIN_URL)
    try:
        with motor.connect() as c:
            rol = c.execute(
                text("select rolbypassrls, rolsuper, rolcreaterole, rolcreatedb, rolreplication,"
                     " rolconnlimit, rolcanlogin from pg_roles"
                     " where rolname = 'cem_soporte_exportacion'"),
            ).one()  # fmt: skip
    finally:
        motor.dispose()
    assert tuple(rol) == (False, False, False, False, False, 2, True)


# Migración ----------------------------------------------------------------------------


def test_0038_ida_y_vuelta_y_se_niega_con_una_exportacion(base_aparte):  # noqa: F811
    cfg, motor = base_aparte
    command.upgrade(cfg, "0038")

    def funciones() -> int:
        with motor.connect() as c:
            return c.execute(
                text("select count(*) from pg_proc p join pg_namespace n"
                     " on n.oid = p.pronamespace where n.nspname = 'nucleo'"
                     " and p.proname like 'exportar\\_%'"),
            ).scalar_one()  # fmt: skip

    assert funciones() == len(FUNCIONES) - len(PARTES_ECONOMICAS)
    command.downgrade(cfg, "0037")
    assert funciones() == 0
    command.upgrade(cfg, "0038")  # control: vuelve a subir limpio
    with motor.begin() as c:
        cons = c.execute(text("insert into nucleo.consorcio (nombre) values ('A') returning id"))
        cons = cons.scalar_one()
        dec = c.execute(
            text("insert into nucleo.decision_formal (consorcio_id, tipo, fecha, descripcion,"
                 " referencia_acta, origen) values (:c, 'asamblea', '2026-01-01', 'x', 'Acta',"
                 " 'soporte') returning id"), {"c": cons},
        ).scalar_one()  # fmt: skip
        per = c.execute(
            text("insert into nucleo.persona (correo, nombre) values ('x@e.com', 'X')"
                 " returning id")).scalar_one()  # fmt: skip
        m = c.execute(
            text("insert into nucleo.mandato (consorcio_id, tipo, persona_id, decision_id,"
                 " vigente_desde, origen) values (:c, 'administrador', :p, :d, '2026-01-01',"
                 " 'soporte') returning id"), {"c": cons, "p": per, "d": dec},
        ).scalar_one()  # fmt: skip
        c.execute(
            text("insert into plataforma.exportacion (consorcio_id, solicitud_referencia,"
                 " destinatario_nombre, destinatario_documento, solicitante, mandato_id,"
                 " facultades_verificadas, fecha_pedido, respaldo_referencia) values"
                 " (:c, 'r', 'd', '1', :p, :m, '{padron.ver}', '2026-01-02', 'r')"),
            {"c": cons, "p": per, "m": m},
        )  # fmt: skip
    with pytest.raises(DBAPIError) as error:
        command.downgrade(cfg, "0037")
    assert error.value.orig.diag.message_hint == "downgrade_con_datos"


def test_el_paquete_no_pasa_por_el_disco_en_claro(edificio, con_url, claves, tmp_path):
    """Lo único que el script escribe es el archivo cifrado: ningún otro archivo nuevo en la
    carpeta de salida ni en la temporal del sistema, y lo escrito no es un ZIP."""
    import tempfile

    e = edificio
    temporal = Path(tempfile.gettempdir())
    antes = {p for p in temporal.rglob("*") if p.is_file()}
    codigo, salida = exportar(e, claves, tmp_path)
    assert codigo == 0
    nuevos = {p for p in temporal.rglob("*") if p.is_file()} - antes
    assert {p for p in nuevos if not p.is_relative_to(tmp_path)} == set()
    assert {p.name for p in tmp_path.iterdir()} == {claves["privada"].name, salida.name}
    assert salida.read_bytes().startswith(b"age-encryption.org/v1")
    assert not zipfile.is_zipfile(salida)
    # Control: descifrado, es un ZIP.
    assert descifrar(salida, claves["privada"]).testzip() is None


def test_un_pedido_con_fecha_futura_se_niega(edificio, con_url, claves, tmp_path, motor_migrate,
                                             capsys):  # fmt: skip
    e = edificio
    codigo, _ = exportar(
        e, claves, tmp_path, fecha_pedido=(e["hoy"] + timedelta(days=2)).isoformat()
    )
    assert codigo == 1 and "fecha_futura" in capsys.readouterr().err
    assert constancias(motor_migrate, e["cons"]) == []


@pytest.fixture
def con_externo(motor_migrate, motor_app):
    return _edificio(motor_migrate, motor_app, con_documentos=False, externo=True)


def test_un_administrador_externo_con_las_cinco_se_niega(con_externo, con_url, claves, tmp_path,
                                                         motor_migrate, capsys):  # fmt: skip
    """§5 pide un mandato de administración o de Consejo: un administrador externo no alcanza
    aunque tenga las cinco facultades generales."""
    e = con_externo
    codigo, salida = exportar(e, claves, tmp_path, solicitante=str(e["ad"]))
    assert codigo == 1
    assert "de administración o de Consejo" in capsys.readouterr().err
    assert not salida.exists() and constancias(motor_migrate, e["cons"]) == []
    # Control: el mismo pedido del Consejo con el paquete completo, en el mismo consorcio, pasa.
    assert exportar(e, claves, tmp_path)[0] == 0
    assert constancias(motor_migrate, e["cons"])[0]["mandato_id"] == e["m_mc"]


def test_la_capacidad_para_exportar_solo_cuenta_administracion_o_consejo(con_externo, motor_app,
                                                                        motor_migrate):  # fmt: skip
    """Control de que el caso está bien sembrado: la capacidad de 0037, que no mira el tipo,
    sí encuentra el mandato del administrador externo (tiene las cinco, generales);
    `registrar_exportacion` lo niega. Y sí registra al Consejo y, en otro consorcio, a la
    administración."""
    e = con_externo
    with pytest.raises(DBAPIError) as error:
        pedido(e, e["ad"])
    assert error.value.orig.diag.message_hint == "sin_capacidad"
    motor = create_engine(url_rol("soporte_documentos"))
    try:
        with transaccion(motor, persona_id=None, consorcio_id=e["cons"]) as c:
            de_0037 = c.execute(
                text("select nucleo.capacidad_del_solicitante(:c, :p, :f, :h)"),
                {"c": e["cons"], "p": e["ad"], "f": FACULTADES, "h": e["hoy"]},
            ).scalar_one()
    finally:
        motor.dispose()
    assert de_0037 == e["m_ad"]
    assert pedido(e).mandato_id == e["m_mc"]
    o = _edificio(motor_migrate, motor_app, con_documentos=False)
    assert pedido(o, o["ad"]).mandato_id == o["m_ad"]
    # Exige el consorcio del contexto, y cem_app no la ejecuta.
    datos = {"c": e["cons"], "p": e["mc"], "h": e["hoy"]}
    with pytest.raises(DBAPIError) as error:
        _como_soporte(None, REGISTRAR, datos)
    assert error.value.orig.sqlstate == "42501"
    with (
        pytest.raises(DBAPIError) as error,
        transaccion(motor_app, persona_id=e["mc"], consorcio_id=e["cons"]) as c,
    ):
        c.execute(text(REGISTRAR), datos)
    assert error.value.orig.sqlstate == "42501"


def test_un_consejo_sin_su_condicion_sale_pendiente_de_revision(
    consejo,  # noqa: F811
    con_url,
    claves,
    tmp_path,
    motor_app,
):
    """El estado del mandato es el operativo, como en `mandato_detalle` desde 0023: sin la
    condición habilitante, el Consejo no está `vigente`. Pide la administradora del escenario."""
    e = consejo
    finalizar(motor_app, e["cons"], e["adm"], e["v_prop"])
    # Control: la API lo muestra `pendiente_revision`.
    assert estado(e["adm"], e["cons"], e["m"]) == "pendiente_revision"
    codigo, salida = exportar(e, claves, tmp_path, solicitante=str(e["adm"]))
    assert codigo == 0
    mandatos = {m["id"]: m for m in entidades(descifrar(salida, claves["privada"]))["mandato"]}
    assert mandatos[str(e["m"])]["estado"] == "pendiente_revision"
    assert {m["estado"] for i, m in mandatos.items() if i != str(e["m"])} == {"vigente"}


# Lo económico (H-B01, RF36, `0069`) ---------------------------------------------------


CUENTA_DEL_PROVEEDOR = cbu_sintetico(1)
CUENTA_DE_LA_VERSION_2 = cbu_sintetico(41)
CUENTA_DEL_OTRO_PROVEEDOR = cbu_sintetico(2)
CUENTA_EN_EL_CONCEPTO = cbu_sintetico(42)
CUENTA_EN_LA_REFERENCIA = cbu_sintetico(43)
CUENTAS = [CUENTA_DEL_PROVEEDOR, CUENTA_DE_LA_VERSION_2, CUENTA_DEL_OTRO_PROVEEDOR]
ESCRITAS_A_MANO = [CUENTA_EN_EL_CONCEPTO, CUENTA_EN_LA_REFERENCIA]
CONCEPTO_CON_CUENTA = (
    f"SINTETICO: transferir a {CUENTA_EN_EL_CONCEPTO[:8]} {CUENTA_EN_EL_CONCEPTO[8:]}"
)
SIN_LA_CUENTA = "SINTETICO: transferir a [cuenta omitida]"
TABLA_DE_LA_ENTIDAD = {
    "proveedor": "nucleo.proveedor",
    "proveedor_version": "nucleo.proveedor_version",
    "contrato": "nucleo.contrato_proveedor",
    "contrato_confirmacion": "nucleo.contrato_proveedor_confirmacion",
    "contrato_baja": "nucleo.contrato_proveedor_baja",
    "orden": "nucleo.orden_pago",
    "orden_version": "nucleo.orden_pago_version",
    "orden_aprobacion": "nucleo.orden_pago_aprobacion",
    "factura_adjunta": "nucleo.factura_adjunta_orden_pago",
    "umbral": "nucleo.umbral_orden_pago",
    "aprobaciones_configuradas": "nucleo.aprobaciones_orden_pago",
    "ejecucion": "nucleo.ejecucion_orden_pago",
    "ejecucion_resultado": "nucleo.resultado_ejecucion_orden_pago",
    "apertura_cuenta": "nucleo.apertura_cuenta_pago_manual",
    "comprobante": "nucleo.comprobante_pago_manual",
    "pago": "nucleo.pago_manual",
    "conciliacion": "nucleo.conciliacion_pago_manual",
    "diferencia_pago": "nucleo.diferencia_pago_manual",
    "toma_pago": "nucleo.toma_pago_manual",
    "declaracion_no_pagado": "nucleo.declaracion_pago_no_realizado",
    "confirmacion_no_pagado": "nucleo.confirmacion_pago_no_realizado",
    "cotejo": "nucleo.cotejo_pago",
    "importacion_extracto": "nucleo.importacion_extracto",
    "movimiento_extracto": "nucleo.movimiento_extracto",
    "movimiento_no_es_pago": "nucleo.movimiento_no_es_pago",
    "columnas_extracto": "nucleo.columnas_extracto",
    "lectura_comprobante": "nucleo.lectura_comprobante",
    "lectura_carga": "nucleo.lectura_carga",
    "liquidacion": "nucleo.liquidacion",
    "gasto": "nucleo.liquidacion_gasto",
    "gasto_quitado": "nucleo.liquidacion_gasto_quitado",
}
COTEJAR = "select nucleo.cotejar_pagos(cast(:pagos as uuid[]), :actor, :origen, :sesion)"
IMPORTAR = (
    "select movimiento_id from nucleo.importar_extracto(:archivo, :desde, :hasta,"
    " cast(:movimientos as jsonb), 'Fecha', 'Importe', 'Referencia', 'con_signo', :actor, :origen,"
    " :sesion, 'ARS')"
)
COTEJAR_EXTRACTO = (
    "select nucleo.cotejar_por_extracto(cast(:pares as jsonb), :actor, :origen, :sesion)"
)
NO_ES_PAGO = "select nucleo.marcar_no_es_pago(:movimiento, :motivo, :actor, :origen, :sesion)"
RETIRAR_NO_ES_PAGO = (
    "select nucleo.retirar_no_es_pago(:movimiento, :motivo, :actor, :origen, :sesion)"
)
ADJUNTAR_FACTURA = (
    "select nucleo.adjuntar_factura_orden_pago(:orden, :version, null, 'Factura SINTETICA',"
    " 'application/pdf', 1, repeat('b', 64), :actor, :origen, :sesion)"
)
TRANSICIONES = (
    "select count(*) from auditoria.eventos where consorcio_id = :c and entidad = 'orden_pago'"
    " and origen <> 'soporte' and despues ? 'estado'"
)


def _con_objetos_en_el_almacen(motor_migrate, cons, bucket) -> None:  # noqa: F811
    """`documento_de_contrato` siembra versiones `disponible` sin objeto ni suma real: acá lo que
    el script baja del almacén tiene que coincidir con la base."""
    contenido = b"%PDF-1.4 SINTETICO"
    with motor_migrate.begin() as c:
        claves = c.execute(
            text("update nucleo.version_documento set tamano = :t, sha256 = :s"
                 " where consorcio_id = :c and estado = 'disponible' returning clave_objeto"),
            {"t": len(contenido), "s": hashlib.sha256(contenido).hexdigest(), "c": cons},
        ).scalars().all()  # fmt: skip
    for clave in claves:
        _s3().put_object(Bucket=bucket, Key=clave, Body=contenido)


def cotejadas(motor_migrate, s, ordenes) -> None:
    """La primera, tildada a mano; la segunda, por un extracto con una línea que coincide. Otro
    extracto trae un débito que no es el pago de una orden (`0098`): se marca, se retira la marca
    y se vuelve a marcar."""
    with motor_migrate.connect() as c:
        pagos = [dict(c.execute(
            text("select p.id, p.numero_operacion, p.fecha_operacion, p.importe_centavos"
                 " from nucleo.pago_manual p join nucleo.ejecucion_orden_pago x"
                 " on x.id = p.ejecucion_id where x.orden_id = :o"), {"o": o},
        ).mappings().one()) for o in ordenes]  # fmt: skip
    a_mano, por_extracto = pagos
    llamar(motor_migrate, s, s.mc2, COTEJAR, pagos=[a_mano["id"]])
    movimiento = llamar(
        motor_migrate, s, s.mc2, IMPORTAR, archivo="a" * 64,
        desde=por_extracto["fecha_operacion"], hasta=por_extracto["fecha_operacion"],
        movimientos=json.dumps([{
            "hash_linea": "b" * 64, "fecha": por_extracto["fecha_operacion"].isoformat(),
            "importe_centavos": por_extracto["importe_centavos"],
            "numero_operacion": por_extracto["numero_operacion"],
        }]),
    )  # fmt: skip
    pares = json.dumps([{"movimiento": str(movimiento), "pago": str(por_extracto["id"])}])
    assert llamar(motor_migrate, s, s.mc2, COTEJAR_EXTRACTO, pares=pares) == 1
    comision = llamar(
        motor_migrate, s, s.mc2, IMPORTAR, archivo="c" * 64,
        desde=por_extracto["fecha_operacion"], hasta=por_extracto["fecha_operacion"],
        movimientos=json.dumps([{
            "hash_linea": "d" * 64, "fecha": por_extracto["fecha_operacion"].isoformat(),
            "importe_centavos": 750, "numero_operacion": None,
        }]),
    )  # fmt: skip
    llamar(motor_migrate, s, s.mc2, NO_ES_PAGO, movimiento=comision,
           motivo="Comisión del banco SINTETICA")  # fmt: skip
    llamar(motor_migrate, s, s.mc2, RETIRAR_NO_ES_PAGO, movimiento=comision,
           motivo="Marcada por error SINTETICO")  # fmt: skip
    llamar(motor_migrate, s, s.mc2, NO_ES_PAGO, movimiento=comision,
           motivo="Impuesto del banco SINTETICO")  # fmt: skip


def leida(motor_migrate, motor_app, s, o) -> None:
    """Como la guarda `cem-trabajos`, sobre el archivo del pago de la orden."""
    with motor_migrate.connect() as c:
        version = c.execute(
            text("select p.version_documento_id from nucleo.pago_manual p"
                 " join nucleo.ejecucion_orden_pago x on x.id = p.ejecucion_id"
                 " where x.orden_id = :o"), {"o": o},
        ).scalar_one()  # fmt: skip
    with transaccion(motor_app, persona_id=None, consorcio_id=s.cons) as c:
        c.execute(
            text("select nucleo.guardar_lectura_comprobante(:v, 'modelo-sintetico',"
                 " 'prompt-sintetico', true, null, 10.00, 'ARS', :cuenta, 'SINTETICO', 'OP-1',"
                 " null)"), {"v": version, "cuenta": cbu_sintetico(1)},
        )  # fmt: skip


def leida_para_carga(motor_migrate, motor_app, s) -> None:
    """Una factura para carga (`0088`) y lo que leyó la IA, como lo guarda `cem-trabajos`."""
    documento = llamar(
        motor_migrate, s, s.adm,
        "select nucleo.crear_documento_para_carga('factura', 'Factura SINTETICA', current_date,"
        " :actor, :origen, :sesion)",
    )  # fmt: skip
    with transaccion(motor_app, persona_id=s.adm, consorcio_id=s.cons) as c:
        version = c.execute(
            text("select id from nucleo.nueva_version(:d, 'application/pdf', 1, repeat('d', 64),"
                 " :actor, 'api')"), {"d": documento, "actor": s.adm},
        ).scalar_one()  # fmt: skip
    with transaccion(motor_app, persona_id=None, consorcio_id=s.cons) as c:
        c.execute(
            text("select nucleo.guardar_lectura_carga(:v, 'modelo-sintetico', 'prompt-sintetico',"
                 " true, null, null, true, 'SINTETICA SRL', null, null, null, 'A 0001-00000001',"
                 " null, 10.00, 'ARS', :concepto)"),
            {"v": version, "concepto": "Reparación SINTETICA"},
        )  # fmt: skip


def _economico(motor_migrate, motor_app, bucket) -> dict:  # noqa: F811
    """La escena de egresos con una fila, al menos, en cada entidad económica: dos contratos (uno
    de baja), una orden en borrador, una rechazada, dos pagadas y conciliadas, una con «no se
    pagó» confirmado, una liquidación con un gasto y otro quitado, y una versión nueva del
    proveedor con otra cuenta. Cada cuenta es sintética (banco 999)."""
    s = escena_economica(motor_migrate, motor_app)
    while len(unidades(motor_migrate, s)) < 3:
        otra_unidad(motor_migrate, s.cons, f"EXP {uuid4().hex[:6]}")
    us = unidades(motor_migrate, s)
    pesos = pesos_del_esquema(len(us))
    registrar(motor_app, s.cons, {str(u): f"{p:.6f}" for u, p in zip(us, pesos, strict=True)},
              decision=acta_de_la_escena(motor_migrate, s))  # fmt: skip
    umbral(motor_app, s.cons, 1)  # todo supera el umbral: toda orden pide aprobación
    exigir(motor_app, s.cons, 1)
    confirmado(motor_migrate, s)
    llamar(motor_migrate, s, s.adm, BAJA_CONTRATO, contrato=confirmado(motor_migrate, s))
    en_borrador = llamar(motor_migrate, s, s.adm, PREPARAR, concepto=CONCEPTO_CON_CUENTA,
                         comprobante=CUENTA_EN_LA_REFERENCIA)  # fmt: skip
    rechazada = enviada(motor_migrate, s)
    llamar(motor_migrate, s, s.mc1, RECHAZAR, orden=rechazada, version=1,
           motivo="SINTETICO: falta la factura")  # fmt: skip
    pagadas = [pagada(motor_migrate, s), pagada(motor_migrate, s)]
    # La factura subida después a una pagada que no la tenía (0099).
    llamar(motor_migrate, s, s.adm, ADJUNTAR_FACTURA, orden=pagadas[1], version=1)
    cotejadas(motor_migrate, s, pagadas)
    leida(motor_migrate, motor_app, s, pagadas[0])
    leida_para_carga(motor_migrate, motor_app, s)
    no_pagada = emitida(motor_migrate, s)
    ver(motor_migrate, s, s.adm, no_pagada)
    declaracion = llamar(motor_migrate, s, s.adm, DECLARAR, orden=no_pagada, version=1,
                         motivo="SINTETICO: el banco la rechazó")  # fmt: skip
    llamar(motor_migrate, s, s.mc1, CONFIRMAR, orden=no_pagada, declaracion=declaracion)
    a_otra_cuenta = emitida(motor_migrate, s)
    diferencia = con_diferencia(motor_migrate, s, a_otra_cuenta, cuenta=cbu_sintetico(50))
    resuelta(motor_migrate, s, a_otra_cuenta, diferencia, "reclamo")
    with motor_migrate.begin() as c:  # esperar el plazo para tomar el pago no entra en la escena
        c.execute(text(
            "insert into nucleo.toma_pago_manual (consorcio_id, ejecucion_id, tomado_por,"
            " identidad_tomo)"
            " select e.consorcio_id, e.id, :q, nucleo.identidad_humana_de(e.consorcio_id, :q)"
            "   from nucleo.ejecucion_orden_pago e where e.orden_id = :o"
        ), {"q": s.mc1, "o": no_pagada})  # fmt: skip
    liquidacion = borrador(motor_migrate, s)
    gastos = [
        llamar(motor_migrate, s, s.adm, AGREGAR, liquidacion=liquidacion, orden=o,
               grupo="expensas_comunes")
        for o in pagadas
    ]  # fmt: skip
    llamar(motor_migrate, s, s.adm, QUITAR, liquidacion=liquidacion, gasto=gastos[1])
    nueva_cuenta(motor_migrate, s, 41)
    _con_objetos_en_el_almacen(motor_migrate, s.cons, bucket)
    return {"cons": s.cons, "hoy": hoy_de(motor_app, s.cons), "mc": s.mc1, "s": s,
            "pagadas": pagadas, "en_borrador": en_borrador}  # fmt: skip


@pytest.fixture
def economico(motor_migrate, motor_app, bucket) -> dict:  # noqa: F811
    return _economico(motor_migrate, motor_app, bucket)


def _paquete(e, claves, tmp_path) -> zipfile.ZipFile:
    codigo, salida = exportar(e, claves, tmp_path)
    assert codigo == 0
    return descifrar(salida, claves["privada"])


def test_lo_economico_va_completo(economico, con_url, claves, tmp_path, motor_migrate):
    """Cada entidad económica tiene todas las filas del consorcio, con las claves decididas."""
    e = economico
    paquete = _paquete(e, claves, tmp_path)
    assert verificar_manifiesto(paquete) == []
    datos = entidades(paquete)
    with motor_migrate.connect() as c:
        en_la_base = {
            entidad: c.execute(text(f"select count(*) from {tabla} where consorcio_id = :c"),
                               {"c": e["cons"]}).scalar_one()
            for entidad, tabla in TABLA_DE_LA_ENTIDAD.items()
        }  # fmt: skip
        en_la_base["orden_transicion"] = c.execute(text(TRANSICIONES),
                                                   {"c": e["cons"]}).scalar_one()  # fmt: skip
    assert set(en_la_base) == ENTIDADES_ECONOMICAS
    # Control: la escena tiene filas en todas; si no, «igual a la base» no prueba nada.
    assert {k for k, v in en_la_base.items() if v == 0} == set()
    assert {k: len(datos[k]) for k in ENTIDADES_ECONOMICAS} == en_la_base
    for entidad in ENTIDADES_ECONOMICAS:
        for fila in datos[entidad]:
            assert set(fila) == CLAVES_POR_ENTIDAD[entidad], entidad
    # La historia va entera, no solo lo vigente.
    assert len(datos["orden"]) == 6
    assert str(e["en_borrador"]) in {o["id"] for o in datos["orden"]}
    assert {v["version"] for v in datos["proveedor_version"]
            if v["proveedor_id"] == str(e["s"].proveedor)} == {1, 2}  # fmt: skip
    assert len(datos["gasto"]) == 2 and len(datos["gasto_quitado"]) == 1
    # Lo cotejado, de las dos formas, y el movimiento atado a su pago.
    assert {t["origen"] for t in datos["cotejo"]} == {"manual", "extracto"}
    [atado] = [t for t in datos["cotejo"] if t["origen"] == "extracto"]
    [del_pago, comision] = sorted(
        datos["movimiento_extracto"], key=lambda m: m["importe_centavos"] == 750
    )
    assert atado["movimiento_id"] == del_pago["id"]
    assert all(t["cotejado_por_nombre"] for t in datos["cotejo"])
    # El débito que no es el pago de una orden, con quién, cuándo y por qué (0098): la marca
    # retirada va con su retiro, y la vigente sin él.
    [retirada, vigente] = datos["movimiento_no_es_pago"]
    assert {retirada["movimiento_id"], vigente["movimiento_id"]} == {comision["id"]}
    assert (retirada["motivo"], retirada["motivo_retiro"]) == (
        "Comisión del banco SINTETICA", "Marcada por error SINTETICO",
    )  # fmt: skip
    assert retirada["retirada_por_nombre"] and retirada["retirada_en"]
    assert (vigente["motivo"], vigente["retirada_en"], vigente["motivo_retiro"]) == (
        "Impuesto del banco SINTETICO", None, None,
    )  # fmt: skip
    assert vigente["marcado_por_nombre"] and vigente["marcado_en"]
    assert datos["columnas_extracto"][0]["columna_importe"] == "Importe"
    # Una orden pagada, paso a paso, por sus transiciones.
    primera = str(e["pagadas"][0])
    assert [t["hacia"] for t in datos["orden_transicion"] if t["orden_id"] == primera] == [
        "borrador", "en_revision", "aprobada", "en_ejecucion", "en_verificacion", "pagada",
    ]  # fmt: skip
    # Quien hizo cada acto, por su nombre.
    assert None not in {o["preparada_por_nombre"] for o in datos["orden"]}
    assert None not in {a["aprobada_por_nombre"] for a in datos["orden_aprobacion"]}
    assert None not in {p["registrado_por_nombre"] for p in datos["pago"]}
    [diferencia] = datos["diferencia_pago"]
    assert (diferencia["tipo"], diferencia["resolucion"]) == ("ambos", "reclamo")
    assert diferencia["cuenta_ultimos_digitos"] == cbu_sintetico(50)[-4:]
    assert None not in (diferencia["registrada_por_nombre"], diferencia["resuelta_por_nombre"])
    [lectura] = datos["lectura_comprobante"]
    assert (lectura["modelo"], lectura["legible"]) == ("modelo-sintetico", True)
    assert lectura["cuenta_ultimos_digitos"] == cbu_sintetico(1)[-4:]
    [carga] = datos["lectura_carga"]
    assert (carga["tipo"], carga["cuit_invalido"], carga["concepto"]) == (
        "factura", True, "Reparación SINTETICA",
    )  # fmt: skip


def test_el_nombre_sale_solo_con_el_criterio_del_paquete(economico, motor_migrate):
    """Con un vínculo o un mandato en el consorcio, el nombre. De otro consorcio o suprimida, nulo:
    un acto económico no agrega al paquete una persona que el padrón no mostraría."""
    e = economico
    ajena = otra_persona(motor_migrate, consorcio_con_persona(motor_migrate)[0])
    suprimida = otra_persona(motor_migrate, e["cons"])
    with motor_migrate.begin() as c:
        c.execute(text("update nucleo.persona set eliminado_en = now() where id = :p"),
                  {"p": suprimida})  # fmt: skip
        nombres = {
            p: c.execute(text("select nucleo.nombre_para_exportar(:c, :p, :h)"),
                         {"c": e["cons"], "p": p, "h": e["hoy"]}).scalar_one()
            for p in (e["s"].adm, ajena, suprimida)
        }  # fmt: skip
    assert nombres[ajena] is None and nombres[suprimida] is None
    assert nombres[e["s"].adm] is not None  # control: la misma consulta encuentra un nombre


def test_ninguna_cuenta_completa_va_en_el_paquete_y_si_en_la_base(
    economico, con_url, claves, tmp_path, motor_migrate
):
    """Regla del propietario: la cuenta completa se ve solo en el acto de ejecución manual. Se
    busca en todos los archivos del paquete, también los binarios y el manifiesto."""
    e = economico
    paquete = _paquete(e, claves, tmp_path)
    textos = _textos_del_paquete(paquete)
    assert {c for c in CUENTAS + ESCRITAS_A_MANO if c in textos} == set()
    # Ni con el separador con que quedó escrita a mano.
    assert CONCEPTO_CON_CUENTA.lower() not in textos
    # Ninguna tira de 22 dígitos en lo económico, por si la cuenta fuera otra.
    economicos = [n for n in paquete.namelist() if n.split("/")[0] in PARTES_ECONOMICAS]
    assert len(economicos) == len(ENTIDADES_ECONOMICAS)
    tira = re.compile(r"(?<!\d)\d{22}(?!\d)")
    assert [n for n in economicos if tira.search(paquete.read(n).decode())] == []
    # Control: la misma búsqueda, en la base de la que salió, las encuentra todas.
    with motor_migrate.connect() as c:
        filas = [
            t
            for tabla in ("nucleo.proveedor_version", "nucleo.ejecucion_orden_pago",
                          "nucleo.orden_pago_version")
            for t in c.execute(text(f"select x::text from {tabla} x where x.consorcio_id = :c"),
                               {"c": e["cons"]}).scalars()
        ]  # fmt: skip
    base = "\n".join(filas)
    sin_separador = base.replace(f"{CUENTA_EN_EL_CONCEPTO[:8]} ", CUENTA_EN_EL_CONCEPTO[:8])
    assert {c for c in CUENTAS + ESCRITAS_A_MANO if c in sin_separador} == set(
        CUENTAS + ESCRITAS_A_MANO
    )
    assert tira.search(base) and CONCEPTO_CON_CUENTA in base
    # Va lo enmascarado: los últimos 4 dígitos, y el texto a mano sin la cuenta.
    datos = entidades(paquete)
    assert {v["cuenta_ultimos_digitos"] for v in datos["proveedor_version"]} == {
        c[-4:] for c in CUENTAS
    }
    assert {x["cuenta_ultimos_digitos"] for x in datos["ejecucion"]} == {CUENTA_DEL_PROVEEDOR[-4:]}
    [en_borrador] = [v for v in datos["orden_version"] if v["orden_id"] == str(e["en_borrador"])]
    assert (en_borrador["concepto"], en_borrador["comprobante"]) == (
        SIN_LA_CUENTA, "[cuenta omitida]")  # fmt: skip


def test_ni_huellas_ni_claves_de_pago_van_en_el_paquete(economico, con_url, claves, tmp_path,
                                                        motor_migrate):  # fmt: skip
    """Las huellas se calculan sobre la cuenta y la clave de la ejecución identifica el pedido ante
    el riel: ninguna va."""
    e = economico
    consultas = {
        "huella del proveedor": ("encode(huella, 'hex')", "nucleo.proveedor_version"),
        "huella de la orden": ("encode(huella, 'hex')", "nucleo.orden_pago_version"),
        "huella del contrato": ("encode(huella, 'hex')", "nucleo.contrato_proveedor"),
        "clave de la ejecución": ("clave::text", "nucleo.ejecucion_orden_pago"),
    }
    with motor_migrate.connect() as c:
        buscado = {
            que: c.execute(text(f"select {columna} from {tabla} where consorcio_id = :c"),
                           {"c": e["cons"]}).scalars().all()
            for que, (columna, tabla) in consultas.items()
        }  # fmt: skip
        en_la_base = "\n".join(
            t for _, tabla in consultas.values()
            for t in c.execute(text(f"select to_jsonb(x)::text from {tabla} x"
                                    " where x.consorcio_id = :c"), {"c": e["cons"]}).scalars()
        ).lower()  # fmt: skip
    assert all(buscado.values())  # la búsqueda tiene qué buscar
    textos = _textos_del_paquete(_paquete(e, claves, tmp_path))
    assert _encontrados(buscado, textos) == {que: 0 for que in buscado}
    # Control: la misma búsqueda, en la base, las encuentra todas.
    assert _encontrados(buscado, en_la_base) == {que: len(v) for que, v in buscado.items()}


_COLUMNAS_DE_PERSONA = {
    "nucleo.proveedor": ("alta_por", "identidad_alta"),
    "nucleo.proveedor_version": ("preparada_por", "identidad_preparo"),
    "nucleo.contrato_proveedor": ("registrado_por", "identidad_registro"),
    "nucleo.contrato_proveedor_confirmacion": ("persona_id", "identidad_id"),
    "nucleo.contrato_proveedor_baja": ("persona_id",),
    "nucleo.orden_pago": ("preparada_por",),
    "nucleo.orden_pago_version": ("preparada_por", "identidad_preparo"),
    "nucleo.orden_pago_aprobacion": ("persona_id", "identidad_id"),
    "nucleo.apertura_cuenta_pago_manual": ("abierta_por", "identidad_abrio"),
    "nucleo.pago_manual": ("registrado_por", "identidad_registro"),
    "nucleo.conciliacion_pago_manual": ("conciliado_por", "identidad_concilio"),
    "nucleo.diferencia_pago_manual": (
        "registrada_por",
        "identidad_registro",
        "resuelta_por",
        "identidad_resolvio",
        "cerrada_por",
        "identidad_cerro",
    ),
    "nucleo.toma_pago_manual": ("tomado_por", "identidad_tomo"),
    "nucleo.declaracion_pago_no_realizado": ("declarado_por", "identidad_declaro"),
    "nucleo.confirmacion_pago_no_realizado": ("confirmado_por", "identidad_confirmo"),
    "nucleo.cotejo_pago": ("cotejado_por", "identidad_cotejo", "anulado_por", "identidad_anulo"),
    "nucleo.importacion_extracto": ("importado_por", "identidad_importo"),
    "nucleo.movimiento_no_es_pago": (
        "marcado_por",
        "identidad_marco",
        "retirada_por",
        "identidad_retiro",
    ),
    "nucleo.columnas_extracto": ("guardadas_por",),
    "nucleo.liquidacion": ("preparada_por", "identidad_preparo"),
    "nucleo.liquidacion_gasto": ("agregado_por", "identidad_agrego"),
    "nucleo.liquidacion_gasto_quitado": ("quitado_por", "identidad_quito"),
}


def test_ningun_identificador_de_persona_va_en_lo_economico(economico, con_url, claves, tmp_path,
                                                            motor_migrate):  # fmt: skip
    """Como `test_ningun_identificador_de_persona_va_en_el_paquete`, con cada columna de persona y
    de identidad humana de las tablas económicas."""
    e = economico
    s = e["s"]
    with motor_migrate.connect() as c:
        filas = [
            f
            for tabla, columnas in _COLUMNAS_DE_PERSONA.items()
            for columna in columnas
            for f in c.execute(
                text(f"select r::text as x, r.{columna} as p from {tabla} r"
                     f" where r.consorcio_id = :c and r.{columna} is not null"),
                {"c": e["cons"]},
            )
        ]  # fmt: skip
    personas = {str(f.p) for f in filas}
    assert {str(s.adm), str(s.mc1), str(s.mc2)} <= personas  # la búsqueda tiene qué buscar
    assert len(personas) >= 6  # con las identidades humanas de los tres
    paquete = _textos_del_paquete(_paquete(e, claves, tmp_path))
    assert {p for p in personas if p in paquete} == set()
    # Control: la misma búsqueda, en las filas de la base que los nombran, los encuentra todos.
    base = " ".join(f.x for f in filas)
    assert {p for p in personas if p in base} == personas


def test_lo_economico_de_otro_consorcio_no_aparece(economico, con_url, claves, tmp_path,
                                                   motor_migrate, motor_app, bucket):  # noqa: F811  # fmt: skip
    e = economico
    otro = _economico(motor_migrate, motor_app, bucket)

    def ids(paquete) -> set[str]:
        datos = entidades(paquete)
        return {f["id"] for entidad in ("proveedor", "contrato", "orden", "orden_aprobacion",
                                        "ejecucion", "pago", "conciliacion",
                                        "declaracion_no_pagado", "liquidacion", "gasto",
                                        "cotejo", "importacion_extracto",
                                        "movimiento_extracto")
                for f in datos[entidad]}  # fmt: skip

    paquete = _paquete(e, claves, tmp_path)
    propios, ajenos = ids(paquete), ids(_paquete(otro, claves, tmp_path))
    # Control: cada paquete encuentra lo suyo, así que la búsqueda de lo ajeno tiene qué buscar.
    assert str(e["s"].proveedor) in propios and str(otro["s"].proveedor) in ajenos
    assert len(propios) >= 10 and len(ajenos) >= 10
    assert propios.isdisjoint(ajenos)
    textos = _textos_del_paquete(paquete)
    assert {i for i in ajenos if i in textos} == set()
    assert {i for i in propios if i in textos} == propios  # control


def test_la_lista_de_facultades_economicas_sale_del_catalogo(motor_migrate):
    """Control de la regla: lo que cuenta es lo económico de egresos del catálogo cruzado con el
    paquete del tipo. Si una migración mueve una facultad, esta prueba avisa antes de que cambie
    quién exporta."""
    with motor_migrate.connect() as c:
        filas = c.execute(
            text("select p.tipo::text, array_agg(p.facultad order by p.facultad)"
                 " from nucleo.paquete_facultad p join nucleo.facultad k on k.codigo = p.facultad"
                 " where k.economica and k.area = 'egresos' group by p.tipo"),
        ).all()  # fmt: skip
    assert {f[0]: f[1] for f in filas} == {
        "administrador": ["egresos.ejecutar_pago", "egresos.preparar_orden"],
        "miembro_consejo": EGRESOS_DEL_CONSEJO,
    }


@pytest.mark.parametrize(
    "caso,facultad,recurso",
    [
        ("no es económica", "liquidacion.revisar", None),
        ("económica y de su paquete, pero no lee órdenes", "fondo_reserva.autorizar", None),
        ("económica pero de otro paquete", "egresos.ejecutar_pago", None),
        ("económica y de su paquete, acotada", "egresos.aprobar_orden", "cuenta:principal"),
    ],
)
def test_sin_una_economica_general_de_su_paquete_se_niega(sin_documentos, motor_migrate, caso,
                                                          facultad, recurso):  # fmt: skip
    """Falla cerrada: se niega con `sin_capacidad` y no queda constancia; lo económico nunca se
    omite en silencio."""
    e = sin_documentos
    with motor_migrate.begin() as c:
        c.execute(
            text("insert into nucleo.facultad_otorgada (consorcio_id, mandato_id, facultad,"
                 " recurso, decision_id) values (:c, :m, :f, :r, :d)"),
            {"c": e["cons"], "m": e["m_sin_economica"], "f": facultad, "r": recurso,
             "d": e["acta"]},
        )  # fmt: skip
    with pytest.raises(DBAPIError) as error:
        pedido(e, e["mc_sin_economica"])
    assert error.value.orig.diag.message_hint == "sin_capacidad", caso
    assert constancias(motor_migrate, e["cons"]) == []
    # Control: la misma persona, con una económica general de su paquete, pasa.
    otorgar(motor_migrate, e["cons"], e["m_sin_economica"], e["acta"], "egresos.aprobar_orden")
    assert pedido(e, e["mc_sin_economica"]).mandato_id == e["m_sin_economica"]
    [constancia] = constancias(motor_migrate, e["cons"])
    assert constancia["facultades_verificadas"] == FACULTADES + EGRESOS_DEL_CONSEJO


def test_0069_ida_y_vuelta(base_aparte):  # noqa: F811
    cfg, motor = base_aparte
    command.upgrade(cfg, "0069")
    marca = "facultades_economicas_para_exportar"

    def estado_de_la_base() -> tuple[int, int, bool, bool]:
        with motor.connect() as c:
            exportar_parte = c.execute(
                text("select count(*) from pg_proc p join pg_namespace n"
                     " on n.oid = p.pronamespace where n.nspname = 'nucleo'"
                     " and p.proname like 'exportar\\_%'"),
            ).scalar_one()  # fmt: skip
            auxiliares = c.execute(
                text("select count(*) from pg_proc p join pg_namespace n"
                     " on n.oid = p.pronamespace where n.nspname = 'nucleo' and p.proname in"
                     " ('texto_sin_cuenta', 'nombre_para_exportar', :m)"), {"m": marca},
            ).scalar_one()  # fmt: skip
            capacidad, registro = (c.execute(
                text("select prosrc from pg_proc where oid = cast(:f as regprocedure)"), {"f": f},
            ).scalar_one() for f in ("nucleo.capacidad_para_exportar(uuid,uuid,date)",
                                     "nucleo.registrar_exportacion(uuid,text,text,text,uuid,date,"
                                     "text)"))  # fmt: skip
            return exportar_parte, auxiliares, marca in capacidad, marca in registro

    assert estado_de_la_base() == (len(FUNCIONES), 3, True, True)
    command.downgrade(cfg, "0068")
    assert estado_de_la_base() == (len(FUNCIONES) - len(PARTES_ECONOMICAS), 0, False, False)
    command.upgrade(cfg, "0069")  # control: vuelve a subir limpio
    assert estado_de_la_base() == (len(FUNCIONES), 3, True, True)
