"""Solicitud formal de documentación al administrador (`0108`; RF49, CU12, PA36, PG08).

Diseño: `doc/diseno/RF49_Solicitud_Formal.md` del repositorio CEM, §4. Contra servicios reales:
PostgreSQL, el almacén S3 de pruebas y ClamAV. Cada negación lleva su control: el mismo pedido de
quien sí puede, o en el estado que sí lo admite, pasa. Datos SINTETICOS / PRUEBA / NO REAL.
"""

import hashlib
import importlib.util
from datetime import date, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from cem_contracts import fiscalizacion as contrato
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from app.db import transaccion
from tests.conftest import clave_idempotencia, url_rol
from tests.test_documentos_2c1 import (  # noqa: F401  (los fixtures se importan)
    analizar,
    bucket_con_versiones,
    firmas_de_la_imagen,
    pdf,
)
from tests.test_mandatos import otra_persona
from tests.test_mandatos_0012 import alta, externo
from tests.test_modo_operacion import cambiar, consorcio_con_persona, decidir_equipo, hoy_de
from tests.test_orden_pago_neutra import (
    Escena,
    escena_economica,
    nueva_sesion,
    otra_cuenta_en_el_consejo,
)
from tests.test_rutas_egresos import cliente, codigo, get, post
from tests.test_vinculo_0014 import base_aparte  # noqa: F401  (es un fixture)

pytestmark = [pytest.mark.db, pytest.mark.redis]
# Los fixtures importados no se redefinen con el parámetro de su nombre.
# ruff: noqa: F811

MIGRACION = Path(__file__).resolve().parent.parent / "migrations/versions/0108_solicitud_formal.py"
SOLICITUD = {
    "objeto": "periodo",
    "referencia": "Liquidación de octubre de 2026 SINTETICA",
    "documentacion": "Extractos bancarios del período.\nComprobantes de los gastos de luz.",
}
TABLAS = [
    "nucleo.solicitud_formal", "nucleo.solicitud_formal_firma", "nucleo.solicitud_formal_emision",
    "nucleo.solicitud_formal_adjunto", "nucleo.solicitud_formal_envio",
    "nucleo.solicitud_formal_respuesta", "nucleo.solicitud_formal_retiro",
]  # fmt: skip
ABIERTAS = "select nucleo.panel_solicitudes_abiertas()"


def _migracion():
    spec = importlib.util.spec_from_file_location("migracion_0108", MIGRACION)
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


@pytest.fixture
def e(motor_migrate, motor_app) -> Escena:
    """Administrador Aumentado: un administrador con cuenta (`e.adm`) y dos consejeros."""
    return escena_economica(motor_migrate, motor_app)


def sin_cuenta(motor_migrate, motor_app, *, con_externo: bool = True) -> Escena:
    """Modo Consejo, dos consejeros y, si `con_externo`, un administrador externo sin cuenta."""
    cons, _, prop = consorcio_con_persona(motor_migrate)
    acta = decidir_equipo(motor_app, cons)
    cambiar(motor_app, cons, "consejo", acta)
    mc1, mc2 = (otra_persona(motor_migrate, cons) for _ in range(2))
    for p in (mc1, mc2):
        alta(motor_app, cons, prop, acta, tipo="miembro_consejo", persona=p)
    if con_externo:
        externo(motor_app, cons, prop, acta)
    sesion = {p: nueva_sesion(motor_migrate, p) for p in (mc1, mc2)}
    return Escena(cons, prop, None, mc1, mc2, sesion, None, None)


@pytest.fixture
def x(motor_migrate, motor_app) -> Escena:
    return sin_cuenta(motor_migrate, motor_app)


def preparar(e: Escena, quien=None, **cambios) -> dict:
    r = post(e, quien or e.mc1, "solicitudes-formales", SOLICITUD | cambios)
    assert r.status_code == 201, r.text
    return r.json()


def firmar(e: Escena, quien, s: dict):
    return post(e, quien, f"solicitudes-formales/{s['id']}/firma", {})


def emitida(e: Escena) -> dict:
    s = preparar(e)
    assert firmar(e, e.mc1, s).status_code == 200
    r = firmar(e, e.mc2, s)
    assert r.status_code == 200, r.text
    return r.json()


def lista(e: Escena, quien) -> dict:
    r = get(e, quien, "solicitudes-formales")
    assert r.status_code == 200, r.text
    return r.json()


def una(e: Escena, quien, s: dict) -> dict:
    return next(x for x in lista(e, quien)["elementos"] if x["id"] == s["id"])


def adjuntar(e: Escena, quien, s: dict, para: str):
    with cliente(quien, e.sesion[quien]) as c:
        return c.post(
            f"/api/v1/consorcios/{e.cons}/solicitudes-formales/{s['id']}/adjuntos",
            params={"para": para, "titulo": "Comprobante SINTETICO"},
            content=pdf(),
            headers={"Content-Type": "application/pdf"} | clave_idempotencia(),
        )


def adjunto(e: Escena, quien, s: dict, para: str) -> str:
    r = adjuntar(e, quien, s, para)
    assert r.status_code == 201, r.text
    return r.json()["adjunto_id"]


def enviar(e: Escena, quien, s: dict, adjunto_id: str | None, fecha=None, medio="carta_documento"):
    cuerpo = {
        "medio": medio,
        "detalle": "CD 123456789 SINTETICA",
        "fecha_envio": (fecha or hoy_de_escena(e)).isoformat(),
        "adjunto_id": adjunto_id,
    }
    return post(e, quien, f"solicitudes-formales/{s['id']}/envio",
                {k: v for k, v in cuerpo.items() if v is not None})  # fmt: skip


_HOY: dict = {}
"""El día del consorcio de cada escena (cada una tiene el suyo), para no leerlo en cada paso."""


def hoy_de_escena(e: Escena):
    return _HOY[e.cons]


def recordar_hoy(motor_app, e: Escena) -> Escena:
    _HOY[e.cons] = hoy_de(motor_app, e.cons)
    return e


def correr_el_envio(motor_migrate, s: dict, dias: int) -> None:
    """Datos de prueba: el envío pasa a ser de hace `dias` días (la historia no se cambia por la
    API; acá, en la base descartable, con los disparadores apagados)."""
    with motor_migrate.begin() as c:
        c.execute(text("alter table nucleo.solicitud_formal_envio disable trigger user"))
        c.execute(
            text("update nucleo.solicitud_formal_envio set fecha_envio = fecha_envio - :d,"
                 " vence_el = vence_el - :d where solicitud_id = :s"),
            {"d": dias, "s": s["id"]},
        )  # fmt: skip
        c.execute(text("alter table nucleo.solicitud_formal_envio enable trigger user"))


def correos_a(motor_migrate, correo: str) -> list[dict]:
    with motor_migrate.connect() as c:
        return [dict(f) for f in c.execute(
            text("select consorcio_id, carga from plataforma.outbox"
                 " where evento = 'correo.solicitado' and carga->>'para' = :p order by id"),
            {"p": correo},
        ).mappings()]  # fmt: skip


def correo_de(motor_migrate, persona) -> str:
    with motor_migrate.connect() as c:
        return c.execute(
            text("select correo from nucleo.persona where id = :p"), {"p": persona}
        ).scalar_one()


# Constantes: una sola fuente ----------------------------------------------------------------


def test_la_migracion_copia_las_constantes_del_contrato():
    m = _migracion()
    assert (m.PLAZO_MINIMO_DIAS, m.PLAZO_MAXIMO_DIAS) == (
        contrato.PLAZO_MINIMO_DIAS, contrato.PLAZO_MAXIMO_DIAS)  # fmt: skip
    assert m.LARGO_REFERENCIA == contrato.LARGO_REFERENCIA
    assert m.LARGO_DOCUMENTACION_PEDIDA == contrato.LARGO_DOCUMENTACION_PEDIDA
    assert m.LARGO_RESPUESTA == contrato.LARGO_RESPUESTA
    assert m.LARGO_MOTIVO_RETIRO == contrato.LARGO_MOTIVO_RETIRO
    assert m.LARGO_DETALLE_ENVIO == contrato.LARGO_DETALLE_ENVIO
    assert set(m.OBJETOS) == set(contrato.ObjetoSolicitud)
    assert set(m.MEDIOS_ENVIO) == set(contrato.MedioEnvio)
    assert set(m.MEDIOS_RESPUESTA) == set(contrato.MedioRespuesta)
    assert set(m.PARA) == set(contrato.ParaAdjunto)
    assert set(m.ABIERTAS) == set(contrato.ABIERTAS)
    assert contrato.PLAZO_MINIMO_DIAS <= contrato.PLAZO_POR_DEFECTO_DIAS
    assert contrato.PLAZO_POR_DEFECTO_DIAS <= contrato.PLAZO_MAXIMO_DIAS


def test_el_plazo_fuera_de_rango_se_rechaza_y_el_de_por_defecto_es_el_del_contrato(e):
    for plazo in (contrato.PLAZO_MINIMO_DIAS - 1, contrato.PLAZO_MAXIMO_DIAS + 1):
        r = post(e, e.mc1, "solicitudes-formales", SOLICITUD | {"plazo_dias": plazo})
        assert r.status_code == 422, plazo
    # Control: sin plazo, el de por defecto; los extremos, aceptados.
    assert preparar(e)["plazo_dias"] == contrato.PLAZO_POR_DEFECTO_DIAS
    for plazo in (contrato.PLAZO_MINIMO_DIAS, contrato.PLAZO_MAXIMO_DIAS):
        assert preparar(e, plazo_dias=plazo)["plazo_dias"] == plazo
    lectura = lista(e, e.mc1)
    assert (lectura["plazo_por_defecto_dias"], lectura["plazo_minimo_dias"],
            lectura["plazo_maximo_dias"]) == (contrato.PLAZO_POR_DEFECTO_DIAS,
                                              contrato.PLAZO_MINIMO_DIAS,
                                              contrato.PLAZO_MAXIMO_DIAS)  # fmt: skip


def test_la_base_tambien_controla_el_plazo(e, motor_app):
    sql = text("select nucleo.preparar_solicitud_formal('otro', 'Ref SINTETICA', 'Doc', :p, :a,"
               " 'api', :s)")  # fmt: skip
    with (
        pytest.raises(DBAPIError) as error,
        transaccion(motor_app, persona_id=e.mc1, consorcio_id=e.cons) as c,
    ):
        c.execute(sql, {"p": contrato.PLAZO_MAXIMO_DIAS + 1, "a": e.mc1, "s": e.sesion[e.mc1]})
    assert error.value.orig.sqlstate == "23514"
    with transaccion(motor_app, persona_id=e.mc1, consorcio_id=e.cons) as c:  # control
        c.execute(sql, {"p": contrato.PLAZO_MAXIMO_DIAS, "a": e.mc1, "s": e.sesion[e.mc1]})


# Preparar y firmar (PA36, A5, A13-C, A14) ---------------------------------------------------


def test_preparar_no_cuenta_como_firma_y_dos_personas_emiten(e, motor_migrate):
    s = preparar(e)
    assert (s["numero"], s["estado"], s["firmas"]) == (1, "borrador", [])
    assert "firmar" in s["acciones"] and "retirar" in s["acciones"]
    # Quien preparó firma (A5): preparar no cuenta como firma.
    r = firmar(e, e.mc1, s)
    assert r.status_code == 200, r.text
    assert r.json()["estado"] == "borrador" and len(r.json()["firmas"]) == 1
    assert r.json()["firmas"][0]["propia"] is True
    # La segunda firma, de otra persona, emite y envía (el administrador tiene cuenta).
    r = firmar(e, e.mc2, s)
    assert r.status_code == 200, r.text
    hecho = r.json()
    assert hecho["estado"] == "enviada"
    assert hecho["destinatario_con_cuenta"] is True and hecho["destinatario_nombre"]
    assert hecho["envio"]["medio"] == "aplicacion"
    envio = hecho["envio"]
    assert envio["fecha_envio"] == hoy_de(motor_migrate, e.cons).isoformat()
    vence = date.fromisoformat(envio["fecha_envio"]) + timedelta(
        days=contrato.PLAZO_POR_DEFECTO_DIAS
    )
    assert envio["vence_el"] == vence.isoformat()
    texto = hecho["texto_emitido"]
    for parte in ("N.º 1", "2064", SOLICITUD["referencia"], "Extractos bancarios",
                  f"{contrato.PLAZO_POR_DEFECTO_DIAS} días corridos", "Firmantes:"):  # fmt: skip
        assert parte in texto, parte
    assert hecho["huella_texto"] == hashlib.sha256(texto.encode()).hexdigest()
    # Una tercera firma ya no se ofrece ni se acepta.
    assert "firmar" not in hecho["acciones"]


def test_dos_firmas_de_la_misma_persona_se_niegan(e):
    s = preparar(e)
    assert firmar(e, e.mc1, s).status_code == 200
    r = firmar(e, e.mc1, s)
    assert codigo(r) == (403, "ya_participo")
    assert una(e, e.mc1, s)["estado"] == "borrador"
    assert firmar(e, e.mc2, s).status_code == 200  # control: otra persona emite


def test_reintentar_una_firma_hecha_devuelve_la_respuesta_guardada(e):
    """Una firma que se hizo y cuya respuesta se perdió se reintenta con la misma clave: recibe la
    respuesta guardada, no `ya_participo` ni «ya emitida» (Codex en app #255)."""
    s = preparar(e)
    clave = clave_idempotencia()
    primera = post(e, e.mc1, f"solicitudes-formales/{s['id']}/firma", {}, clave)
    assert primera.status_code == 200, primera.text
    otra = post(e, e.mc1, f"solicitudes-formales/{s['id']}/firma", {}, clave)
    assert (otra.status_code, otra.json()) == (200, primera.json())
    assert codigo(firmar(e, e.mc1, s)) == (403, "ya_participo")  # control: clave nueva, se niega
    clave2 = clave_idempotencia()
    segunda = post(e, e.mc2, f"solicitudes-formales/{s['id']}/firma", {}, clave2)
    assert segunda.status_code == 200, segunda.text
    assert segunda.json()["estado"] == "enviada"
    otra = post(e, e.mc2, f"solicitudes-formales/{s['id']}/firma", {}, clave2)
    assert (otra.status_code, otra.json()) == (200, segunda.json())
    assert len(una(e, e.mc1, s)["firmas"]) == 2


def test_otra_cuenta_del_mismo_ser_humano_no_pone_la_segunda_firma(e, motor_migrate, motor_app):
    s = preparar(e)
    assert firmar(e, e.mc1, s).status_code == 200
    otra = otra_cuenta_en_el_consejo(motor_migrate, motor_app, e, e.mc1)
    r = firmar(e, otra, s)
    assert codigo(r) == (403, "ya_participo")
    assert "otra cuenta de la misma persona" in r.json()["mensaje"].lower()
    assert firmar(e, e.mc2, s).status_code == 200  # control


def test_la_base_niega_la_firma_repetida_aunque_el_evaluador_no_mire(e, motor_app):
    s = preparar(e)
    sql = text("select * from nucleo.firmar_solicitud_formal(:s, :a, 'api', :ses)")
    with transaccion(motor_app, persona_id=e.mc1, consorcio_id=e.cons) as c:
        c.execute(sql, {"s": s["id"], "a": e.mc1, "ses": e.sesion[e.mc1]})
    with (
        pytest.raises(DBAPIError) as error,
        transaccion(motor_app, persona_id=e.mc1, consorcio_id=e.cons) as c,
    ):
        c.execute(sql, {"s": s["id"], "a": e.mc1, "ses": e.sesion[e.mc1]})
    assert error.value.orig.diag.message_hint == "firma_repetida"


def test_sin_administrador_vigente_la_segunda_firma_se_niega_y_queda_en_borrador(
    motor_migrate, motor_app
):
    e = sin_cuenta(motor_migrate, motor_app, con_externo=False)
    s = preparar(e)
    # La primera firma no necesita destinatario (caso 23).
    assert firmar(e, e.mc1, s).status_code == 200
    r = firmar(e, e.mc2, s)
    assert codigo(r) == (409, "sin_administrador_vigente")
    hoy = una(e, e.mc1, s)
    assert hoy["estado"] == "borrador" and len(hoy["firmas"]) == 1
    # Se le ofrece igual, para que la API le diga por qué no.
    assert "firmar" in una(e, e.mc2, s)["acciones"]
    # Control: con un administrador vigente, la misma firma emite y fija el destinatario.
    externo(motor_app, e.cons, e.prop, decidir_equipo(motor_app, e.cons))
    r = firmar(e, e.mc2, s)
    assert r.status_code == 200, r.text
    assert r.json()["estado"] == "envio_pendiente"
    assert r.json()["destinatario_nombre"] == "Estudio X"


@pytest.mark.parametrize("modo", ["autogestion", None])
def test_sin_un_modo_que_lo_admita_no_se_prepara_ni_se_firma(motor_migrate, motor_app, modo):
    e = sin_cuenta(motor_migrate, motor_app)
    s = preparar(e)  # control: en Consejo, sí
    # Datos de prueba: el modo cambia sin la transición real (pasar a Autogestión pide terminar
    # el mandato de administración), con los disparadores apagados en la base descartable.
    sentencia = ("delete from nucleo.modo_operacion where consorcio_id = :c" if modo is None
                 else "update nucleo.modo_operacion set modo = 'autogestion'"
                      " where consorcio_id = :c")  # fmt: skip
    with motor_migrate.begin() as c:
        c.execute(text("alter table nucleo.modo_operacion disable trigger user"))
        c.execute(text(sentencia), {"c": e.cons})
        c.execute(text("alter table nucleo.modo_operacion enable trigger user"))
    r = post(e, e.mc1, "solicitudes-formales", SOLICITUD)
    assert codigo(r) == (403, "sin_permiso")
    assert codigo(firmar(e, e.mc1, s)) == (403, "sin_permiso")
    # La base también: aunque la ruta no mirara, el modo no lo admite.
    with (
        pytest.raises(DBAPIError) as error,
        transaccion(motor_app, persona_id=e.mc1, consorcio_id=e.cons) as c,
    ):
        c.execute(text("select * from nucleo.firmar_solicitud_formal(:s, :a, 'api', :ses)"),
                  {"s": s["id"], "a": e.mc1, "ses": e.sesion[e.mc1]})  # fmt: skip
    assert error.value.orig.diag.message_hint == "modo_no_admitido"


def test_la_administracion_no_prepara_ni_firma(e):
    r = post(e, e.adm, "solicitudes-formales", SOLICITUD)
    assert codigo(r) == (403, "sin_permiso")
    s = preparar(e)  # control: el Consejo sí
    assert codigo(firmar(e, e.adm, s)) == (403, "sin_permiso")


# Envío, vencimiento y aviso ------------------------------------------------------------------


def test_con_cuenta_el_envio_es_al_emitir_y_se_avisa_sin_el_contenido(e, motor_migrate):
    para = correo_de(motor_migrate, e.adm)
    antes = len(correos_a(motor_migrate, para))
    s = emitida(e)
    avisos = correos_a(motor_migrate, para)
    assert len(avisos) == antes + 1
    carga = avisos[-1]["carga"]
    assert str(avisos[-1]["consorcio_id"]) == str(e.cons)
    assert "/solicitudes" in carga["texto"]
    assert s["envio"]["vence_el"][8:10] in carga["texto"]
    # P3: sin el contenido de la solicitud.
    assert SOLICITUD["referencia"] not in carga["texto"]
    assert "Extractos" not in carga["texto"]
    # Una primera firma no avisa (control: el aviso es de la emisión).
    preparada = preparar(e)
    firmar(e, e.mc1, preparada)
    assert len(correos_a(motor_migrate, para)) == antes + 1


def test_vence_con_el_dia_del_consorcio_despues_del_vencimiento(e, motor_migrate):
    s = emitida(e)
    plazo = s["plazo_dias"]
    # Un día antes del vencimiento (y el mismo día): enviada.
    correr_el_envio(motor_migrate, s, plazo)
    assert una(e, e.mc1, s)["estado"] == "enviada"
    # Pasado el vencimiento: vencida.
    correr_el_envio(motor_migrate, s, 1)
    assert una(e, e.mc1, s)["estado"] == "vencida"


def test_sin_cuenta_queda_con_el_envio_pendiente_y_no_vence(x, motor_migrate, motor_app):
    s = preparar(x)
    firmar(x, x.mc1, s)
    r = firmar(x, x.mc2, s)
    assert r.json()["estado"] == "envio_pendiente" and r.json()["envio"] is None
    assert r.json()["destinatario_con_cuenta"] is False
    # Aunque pase mucho más que el plazo desde la emisión, sin envío no vence.
    with motor_migrate.begin() as c:
        c.execute(text("alter table nucleo.solicitud_formal_emision disable trigger user"))
        c.execute(text("update nucleo.solicitud_formal_emision set emitida_el = emitida_el - 90"
                       " where solicitud_id = :s"), {"s": s["id"]})  # fmt: skip
        c.execute(text("alter table nucleo.solicitud_formal_emision enable trigger user"))
    assert una(x, x.mc1, s)["estado"] == "envio_pendiente"
    assert "registrar_envio" in una(x, x.mc1, s)["acciones"]


def test_registrar_el_envio_exige_medio_fecha_y_comprobante_y_solo_una_vez(
    x, motor_app, bucket_con_versiones, firmas_de_la_imagen
):
    recordar_hoy(motor_app, x)
    s = preparar(x)
    firmar(x, x.mc1, s)
    firmar(x, x.mc2, s)
    hoy = hoy_de_escena(x)
    a = adjunto(x, x.mc1, s, "envio")
    # Sin comprobante.
    assert codigo(enviar(x, x.mc1, s, None)) == (422, "peticion_invalida")
    # Fecha anterior a la emisión, o posterior a hoy.
    for fecha in (hoy - timedelta(days=1), hoy + timedelta(days=1)):
        assert codigo(enviar(x, x.mc1, s, a, fecha=fecha)) == (422, "fecha_envio_invalida")
    # El medio `aplicacion` no se registra: es el de la emisión con cuenta.
    assert codigo(enviar(x, x.mc1, s, a, medio="aplicacion")) == (422, "peticion_invalida")
    # El comprobante subido por otra persona no sirve.
    assert codigo(enviar(x, x.mc2, s, a)) == (422, "adjunto_invalido")
    # Control: con todo, se registra y desde esa fecha corre el plazo.
    r = enviar(x, x.mc1, s, a)
    assert r.status_code == 200, r.text
    envio = r.json()["envio"]
    assert (r.json()["estado"], envio["medio"], envio["fecha_envio"]) == (
        "enviada", "carta_documento", hoy.isoformat())  # fmt: skip
    assert envio["vence_el"] == (hoy + timedelta(days=s["plazo_dias"])).isoformat()
    assert envio["documento_id"] and envio["version_documento_id"]
    # Solo una vez: ni otro comprobante ni otro registro.
    assert codigo(adjuntar(x, x.mc2, s, "envio")) == (409, "solicitud_no_admite")
    r = enviar(x, x.mc1, s, a)
    assert codigo(r) == (409, "solicitud_no_admite")
    # El comprobante entra en cuarentena y el antivirus lo acepta.
    assert analizar(motor_app) == 1


def test_quien_no_puede_firmar_no_registra_el_envio(x, motor_migrate, motor_app,
                                                   bucket_con_versiones):  # fmt: skip
    recordar_hoy(motor_app, x)
    s = preparar(x)
    firmar(x, x.mc1, s)
    firmar(x, x.mc2, s)
    prop_sesion = nueva_sesion(motor_migrate, x.prop)
    x.sesion[x.prop] = prop_sesion
    r = adjuntar(x, x.prop, s, "envio")
    assert r.status_code in {403, 404}
    assert codigo(enviar(x, x.prop, s, str(uuid4()))) in {(403, "sin_permiso"),
                                                         (404, "no_encontrado")}  # fmt: skip


def test_con_cuenta_no_hay_envio_que_registrar(e, bucket_con_versiones):
    s = emitida(e)
    assert "registrar_envio" not in una(e, e.mc1, s)["acciones"]
    assert codigo(adjuntar(e, e.mc1, s, "envio")) == (409, "solicitud_no_admite")


# Responder (P2, P5) -------------------------------------------------------------------------


def test_el_destinatario_responde_y_nadie_mas(e, motor_migrate, motor_app):
    s = emitida(e)
    assert "responder" in una(e, e.adm, s)["acciones"]
    cuerpo = {"texto": "Se adjuntan los extractos SINTETICOS."}
    # El Consejo no responde: para él, como para cualquier otra persona, no es suya.
    for quien in (e.mc1, e.prop):
        e.sesion.setdefault(quien, nueva_sesion(motor_migrate, quien))
        r = post(e, quien, f"solicitudes-formales/{s['id']}/respuesta", cuerpo)
        assert codigo(r) == (404, "no_encontrado"), quien
    # El Consejo tampoco registra la de un destinatario con cuenta (P2).
    r = post(e, e.mc1, f"solicitudes-formales/{s['id']}/respuesta-recibida",
             cuerpo | {"recibida_el": s["envio"]["fecha_envio"]})  # fmt: skip
    assert codigo(r) == (409, "destinatario_con_cuenta")
    # El administrador de otro consorcio: ni la ve.
    otra = escena_economica(motor_migrate, motor_app)
    with cliente(otra.adm, otra.sesion[otra.adm]) as c:
        r = c.post(f"/api/v1/consorcios/{e.cons}/solicitudes-formales/{s['id']}/respuesta",
                   json=cuerpo, headers=clave_idempotencia())  # fmt: skip
    assert r.status_code == 404
    # Control: el destinatario responde.
    r = post(e, e.adm, f"solicitudes-formales/{s['id']}/respuesta", cuerpo)
    assert r.status_code == 200, r.text
    respuesta = r.json()["respuesta"]
    assert (r.json()["estado"], respuesta["medio"], respuesta["fuera_de_plazo"]) == (
        "respondida", "aplicacion", False)  # fmt: skip
    # Una sola respuesta.
    r = post(e, e.adm, f"solicitudes-formales/{s['id']}/respuesta", cuerpo)
    assert codigo(r) == (409, "solicitud_no_admite")


def test_el_destinatario_ve_solo_las_suyas(e, motor_migrate, motor_app):
    s = emitida(e)
    borrador = preparar(e)
    # El administrador tiene el panel: las ve todas. Un propietario sin panel, ninguna.
    assert {x["id"] for x in lista(e, e.adm)["elementos"]} == {s["id"], borrador["id"]}
    e.sesion[e.prop] = nueva_sesion(motor_migrate, e.prop)
    assert lista(e, e.prop)["elementos"] == []
    assert lista(e, e.prop)["puede_preparar"] is False
    assert lista(e, e.mc1)["puede_preparar"] is True  # control


def test_una_respuesta_fuera_de_plazo_se_acepta_y_queda_marcada(e, motor_migrate):
    s = emitida(e)
    correr_el_envio(motor_migrate, s, s["plazo_dias"] + 1)
    assert una(e, e.adm, s)["estado"] == "vencida"
    r = post(e, e.adm, f"solicitudes-formales/{s['id']}/respuesta", {"texto": "Tarde SINTETICA"})
    assert r.status_code == 200, r.text
    assert r.json()["respuesta"]["fuera_de_plazo"] is True


def test_el_consejo_registra_la_respuesta_de_un_destinatario_sin_cuenta(
    x, motor_app, bucket_con_versiones, firmas_de_la_imagen
):
    recordar_hoy(motor_app, x)
    s = preparar(x)
    firmar(x, x.mc1, s)
    firmar(x, x.mc2, s)
    hoy = hoy_de_escena(x)
    cuerpo = {"texto": "Llegó por carta SINTETICA", "recibida_el": hoy.isoformat()}
    # Antes del envío no espera respuesta.
    r = post(x, x.mc1, f"solicitudes-formales/{s['id']}/respuesta-recibida", cuerpo)
    assert codigo(r) == (409, "solicitud_no_admite")
    assert enviar(x, x.mc1, s, adjunto(x, x.mc1, s, "envio")).status_code == 200
    # Una fecha futura no.
    r = post(x, x.mc1, f"solicitudes-formales/{s['id']}/respuesta-recibida",
             cuerpo | {"recibida_el": (hoy + timedelta(days=1)).isoformat()})  # fmt: skip
    assert codigo(r) == (422, "fecha_respuesta_invalida")
    # Un archivo que subió otra persona no sirve.
    a = adjunto(x, x.mc2, s, "respuesta")
    r = post(x, x.mc1, f"solicitudes-formales/{s['id']}/respuesta-recibida",
             cuerpo | {"adjunto_id": a})  # fmt: skip
    assert codigo(r) == (422, "adjunto_invalido")
    # Control: quien lo subió lo usa.
    r = post(x, x.mc2, f"solicitudes-formales/{s['id']}/respuesta-recibida",
             cuerpo | {"adjunto_id": a})  # fmt: skip
    assert r.status_code == 200, r.text
    respuesta = r.json()["respuesta"]
    assert (respuesta["medio"], respuesta["respondida_el"]) == ("medio_formal", hoy.isoformat())
    assert respuesta["documento_id"]
    # Los dos archivos, el del envío y el de la respuesta, pasan por el antivirus.
    analizados = 0
    while (lote := analizar(motor_app)) > 0:
        analizados += lote
    assert analizados == 2


# Retirar (P4) -------------------------------------------------------------------------------


def test_solo_un_borrador_se_retira_con_motivo(e):
    s = preparar(e)
    r = post(e, e.mc2, f"solicitudes-formales/{s['id']}/retiro", {"motivo": ""})
    assert r.status_code == 422
    r = post(e, e.mc2, f"solicitudes-formales/{s['id']}/retiro",
             {"motivo": "Se pidió por otra vía SINTETICA"})  # fmt: skip
    assert r.status_code == 200, r.text
    assert r.json()["estado"] == "retirada" and r.json()["retiro"]["motivo"]
    assert codigo(firmar(e, e.mc1, s)) == (409, "solicitud_no_admite")
    # Una emitida ya no se retira.
    hecha = emitida(e)
    r = post(e, e.mc1, f"solicitudes-formales/{hecha['id']}/retiro", {"motivo": "Tarde"})
    assert codigo(r) == (409, "solicitud_no_admite")
    # La administración no retira.
    otra = preparar(e)
    r = post(e, e.adm, f"solicitudes-formales/{otra['id']}/retiro", {"motivo": "No"})
    assert codigo(r) == (403, "sin_permiso")


# Panel ---------------------------------------------------------------------------------------


def test_el_panel_cuenta_las_abiertas(e):
    def abiertas() -> int:
        r = get(e, e.mc1, "panel")
        assert r.status_code == 200, r.text
        return r.json()["solicitudes_abiertas"]

    assert abiertas() == 0
    s = emitida(e)
    retirada = preparar(e)
    post(e, e.mc1, f"solicitudes-formales/{retirada['id']}/retiro", {"motivo": "SINTETICO"})
    preparar(e)
    assert abiertas() == 2  # la enviada y el borrador; la retirada no
    post(e, e.adm, f"solicitudes-formales/{s['id']}/respuesta", {"texto": "SINTETICA"})
    assert abiertas() == 1


# Barreras de la base ------------------------------------------------------------------------


def test_las_tablas_no_se_cambian_ni_se_borran(e, motor_migrate):
    s = emitida(e)
    for tabla in TABLAS[:3] + TABLAS[4:5]:
        for sentencia in (f"update {tabla} set consorcio_id = consorcio_id",
                          f"delete from {tabla}"):  # fmt: skip
            with pytest.raises(DBAPIError) as error, motor_migrate.begin() as c:
                c.execute(text(sentencia))
            assert error.value.orig.diag.message_hint == "historia_inmutable", sentencia
    assert una(e, e.mc1, s)["estado"] == "enviada"  # control: sigue entera


def test_cem_app_no_toca_las_tablas_directo(e, motor_app):
    emitida(e)
    for tabla in TABLAS:
        with (
            pytest.raises(DBAPIError) as error,
            transaccion(motor_app, persona_id=e.mc1, consorcio_id=e.cons) as c,
        ):
            c.execute(text(f"select count(*) from {tabla}"))
        assert error.value.orig.sqlstate == "42501", tabla
    # Control: por la lectura, sí.
    with transaccion(motor_app, persona_id=e.mc1, consorcio_id=e.cons) as c:
        filas = c.execute(
            text("select * from nucleo.solicitudes_formales_lectura(:a, 'api', :s)"),
            {"a": e.mc1, "s": e.sesion[e.mc1]},
        ).all()
    assert len(filas) == 1


def test_rls_forzada_y_otro_consorcio_no_ve_nada(e, motor_migrate, motor_app):
    emitida(e)
    with motor_migrate.connect() as c:
        flags = c.execute(text(
            "select relname, relrowsecurity, relforcerowsecurity from pg_class"
            " where oid = any(cast(:t as regclass[]))"), {"t": TABLAS}).all()  # fmt: skip
    assert len(flags) == len(TABLAS) and all(f[1] and f[2] for f in flags)
    otra = escena_economica(motor_migrate, motor_app)
    assert lista(otra, otra.mc1)["elementos"] == []
    assert len(lista(e, e.mc1)["elementos"]) == 1  # control
    # Cada tabla tiene solo la política de lectura por el consorcio del ámbito.
    with motor_migrate.connect() as c:
        politicas = c.execute(text(
            "select tablename, cmd, qual from pg_policies where schemaname = 'nucleo'"
            " and tablename like 'solicitud_formal%'")).all()  # fmt: skip
    assert len(politicas) == len(TABLAS)
    assert all(p.cmd == "SELECT" and "consorcio_actual()" in p.qual for p in politicas)


def test_las_funciones_las_ejecuta_solo_cem_app(motor_migrate):
    rutas = {"preparar_solicitud_formal", "firmar_solicitud_formal", "retirar_solicitud_formal",
             "subir_adjunto_solicitud_formal", "registrar_envio_solicitud_formal",
             "responder_solicitud_formal", "registrar_respuesta_solicitud_formal",
             "solicitudes_formales_lectura", "panel_solicitudes_abiertas",
             "mandato_administracion_vigente"}  # fmt: skip
    m = _migracion()
    internas = {f.split("(")[0].removeprefix("nucleo.") for f in m.INTERNAS}
    with motor_migrate.connect() as c:
        filas = c.execute(text(
            "select p.proname, has_function_privilege('cem_app', p.oid, 'EXECUTE'),"
            " has_function_privilege('public', p.oid, 'EXECUTE'),"
            " array_to_string(p.proconfig, ',')"
            " from pg_proc p join pg_namespace n on n.oid = p.pronamespace"
            " where n.nspname = 'nucleo' and p.proname = any(:n)"),
            {"n": list(rutas | internas)}).all()  # fmt: skip
    assert {f[0] for f in filas} == rutas | internas
    for nombre, app, publico, camino in filas:
        assert (app, publico) == (nombre in rutas, False), nombre
        assert camino == "search_path=pg_catalog, pg_temp", nombre


# Exportación ---------------------------------------------------------------------------------


def test_la_exportacion_incluye_las_solicitudes(e, motor_migrate):
    s = emitida(e)
    retirada = preparar(e)
    post(e, e.mc1, f"solicitudes-formales/{retirada['id']}/retiro", {"motivo": "SINTETICO"})
    hoy = hoy_de(motor_migrate, e.cons)
    motor = create_engine(url_rol("soporte_exportacion"))
    try:
        with transaccion(motor, persona_id=None, consorcio_id=e.cons) as c:
            pedido = c.execute(
                text("select id from nucleo.registrar_exportacion(:c, 'Nota 1', 'Estudio',"
                     " '30712345671', :p, :h, 'Acta 1')"),
                {"c": e.cons, "p": e.mc1, "h": hoy},
            ).scalar_one()  # fmt: skip
            filas = c.execute(
                text("select entidad, fila from nucleo.exportar_mandatos(:c, :e)"),
                {"c": e.cons, "e": pedido},
            ).all()
    finally:
        motor.dispose()
    por_entidad: dict[str, list] = {}
    for f in filas:
        por_entidad.setdefault(f.entidad, []).append(f.fila)
    assert [x["id"] for x in por_entidad["solicitud_formal"]] == [s["id"], retirada["id"]]
    assert len(por_entidad["solicitud_formal_firma"]) == 2
    [emision] = por_entidad["solicitud_formal_emision"]
    assert emision["huella"] == s["huella_texto"]
    assert len(por_entidad["solicitud_formal_envio"]) == 1
    assert len(por_entidad["solicitud_formal_retiro"]) == 1
    # Ningún identificador de persona: solo nombres.
    texto = str(filas)
    for persona in (e.mc1, e.mc2, e.adm):
        assert str(persona) not in texto


# Migración ----------------------------------------------------------------------------------


def _definiciones(motor) -> dict[str, str]:
    m = _migracion()
    with motor.connect() as c:
        return {
            f: c.execute(text("select pg_get_functiondef(cast(:f as regprocedure))"),
                         {"f": f}).scalar_one()
            for f, _ in m.EN_SU_LUGAR
        }  # fmt: skip


def test_0108_ida_y_vuelta_deja_todo_como_estaba(base_aparte):
    cfg, motor = base_aparte
    command.upgrade(cfg, "0107")
    antes = _definiciones(motor)
    command.upgrade(cfg, "0108")
    con = _definiciones(motor)
    assert all(con[f] != antes[f] for f in antes)  # control: la 0108 cambió las tres
    with motor.connect() as c:
        assert c.execute(text("select to_regclass('nucleo.solicitud_formal')")).scalar()
    command.downgrade(cfg, "0107")
    assert _definiciones(motor) == antes
    with motor.connect() as c:
        for tabla in TABLAS:
            assert c.execute(text("select to_regclass(:t)"), {"t": tabla}).scalar() is None
    command.upgrade(cfg, "0108")
    assert _definiciones(motor) == con


def test_0108_no_baja_con_solicitudes(base_aparte):
    cfg, motor = base_aparte
    command.upgrade(cfg, "0108")
    with motor.begin() as c:
        cons = c.execute(text("insert into nucleo.consorcio (nombre, zona_horaria) values"
                              " ('Prueba', 'America/Argentina/Buenos_Aires') returning id")
                         ).scalar_one()  # fmt: skip
        persona = c.execute(
            text(
                "insert into nucleo.persona (correo, nombre) values"
                " ('baja-0108@ejemplo.com', 'P') returning id"
            )
        ).scalar_one()
        identidad = c.execute(
            text(
                "insert into nucleo.identidad_humana (consorcio_id, huella,"
                " clave_version) values (:c, sha256('baja-0108'::bytea), 1)"
                " returning id"
            ),
            {"c": cons},
        ).scalar_one()
        c.execute(text("insert into nucleo.solicitud_formal (consorcio_id, numero, objeto,"
                       " referencia, documentacion, plazo_dias, preparada_por, identidad_preparo)"
                       " values (:c, 1, 'otro', 'R', 'D', 15, :p, :i)"),
                  {"c": cons, "p": persona, "i": identidad})  # fmt: skip
    with pytest.raises(DBAPIError) as error:
        command.downgrade(cfg, "0107")
    assert error.value.orig.diag.message_hint == "downgrade_con_datos"
