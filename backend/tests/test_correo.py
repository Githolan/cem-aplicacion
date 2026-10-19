"""`notificar.correo` contra un SMTP real (Mailpit) y PostgreSQL real (documento 13, §12.4).

Mailpit (`cem-correo-pruebas` en `pruebas/compose.yml`) exige STARTTLS con un certificado
autofirmado para su nombre y autenticación; su "chaos" responde 4xx o 5xx a pedido. El
certificado se confía con `SSL_CERT_FILE` (lo lee `ssl.create_default_context`), igual que
en producción se confía en las CA del sistema: el código no tiene un atajo para pruebas.
"""

import json
import os
import smtplib
import ssl
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import text

from app.db import transaccion
from app.plataforma import cola, correo, outbox
from app.plataforma import tareas as _tareas  # noqa: F401  (registra notificar.correo y su ruta)
from app.plataforma.trabajos import Trabajador
from tests.conftest import url_rol

RAIZ = Path(__file__).resolve().parent.parent
SERVIDOR = os.environ.get("CEM_TEST_SMTP_SERVIDOR", "localhost")
PUERTO = os.environ.get("CEM_TEST_SMTP_PUERTO", "1025")
USUARIO = os.environ.get("CEM_TEST_SMTP_USUARIO", "usuario-pruebas")
CLAVE = os.environ.get("CEM_TEST_SMTP_CLAVE", "clave-pruebas")
API_CORREO = os.environ.get("CEM_TEST_CORREO_API", "http://localhost:8025")
REMITENTE = f"no-responder@{SERVIDOR}.ejemplo.com"

PARA = "vecina.correo@ejemplo.com"
ASUNTO = "Tu enlace para entrar"
# Una frase que la redacción no toca: si aparece en un registro, se registró el cuerpo.
CUERPO = "Entrá con este enlace: frase-del-cuerpo-7f3a"
HTML = "<p>Entrá con <a href='https://ejemplo.com/e'>este enlace</a>: frase-del-cuerpo-7f3a</p>"


# Servidor de pruebas -------------------------------------------------------------------


class Mailpit:
    def __init__(self, url: str) -> None:
        self.http = httpx.Client(base_url=url, timeout=10)

    def vaciar(self) -> None:
        self.http.delete("/api/v1/messages").raise_for_status()

    def caos(self, **disparadores: int) -> None:
        """`Recipient=451` hace que el servidor responda 451 a todo RCPT TO; sin nada, apaga."""
        cuerpo = {d: {"ErrorCode": c, "Probability": 100} for d, c in disparadores.items()}
        for d in ("Sender", "Recipient", "Authentication"):
            cuerpo.setdefault(d, {"ErrorCode": 451, "Probability": 0})
        self.http.put("/api/v1/chaos", json=cuerpo).raise_for_status()

    def mensajes(self) -> list[dict]:
        r = self.http.get("/api/v1/messages")
        r.raise_for_status()
        return r.json()["messages"]

    def mensaje(self, id_: str) -> dict:
        r = self.http.get(f"/api/v1/message/{id_}")
        r.raise_for_status()
        return r.json()

    def encabezados(self, id_: str) -> dict[str, list[str]]:
        r = self.http.get(f"/api/v1/message/{id_}/headers")
        r.raise_for_status()
        return r.json()


@pytest.fixture(scope="session")
def certificado_smtp(tmp_path_factory) -> Path:
    """El certificado que presenta Mailpit, tomado por STARTTLS sin verificar (solo acá)."""
    sin_verificar = ssl.create_default_context()
    sin_verificar.check_hostname = False
    sin_verificar.verify_mode = ssl.CERT_NONE
    with smtplib.SMTP(SERVIDOR, int(PUERTO), timeout=10) as smtp:
        smtp.starttls(context=sin_verificar)
        der = smtp.sock.getpeercert(binary_form=True)
    archivo = tmp_path_factory.mktemp("smtp") / "mailpit.pem"
    archivo.write_text(ssl.DER_cert_to_PEM_cert(der))
    return archivo


def entorno_smtp(certificado: Path) -> dict[str, str]:
    return {
        "CEM_SMTP_SERVIDOR": SERVIDOR,
        "CEM_SMTP_PUERTO": PUERTO,
        "CEM_SMTP_USUARIO": USUARIO,
        "CEM_SMTP_CLAVE": CLAVE,
        "CEM_CORREO_REMITENTE": REMITENTE,
        "SSL_CERT_FILE": str(certificado),
    }


@pytest.fixture
def mailpit(certificado_smtp, monkeypatch, motor_migrate, datos):
    for nombre, valor in entorno_smtp(certificado_smtp).items():
        monkeypatch.setenv(nombre, valor)
    with motor_migrate.begin() as c:
        c.execute(text("truncate plataforma.jobs, plataforma.outbox"))
    servidor = Mailpit(API_CORREO)
    servidor.caos()
    servidor.vaciar()
    yield servidor
    servidor.caos()
    servidor.http.close()


def carga(**cambios) -> dict:
    return {"para": PARA, "asunto": ASUNTO, "texto": CUERPO, "html": HTML} | cambios


def encolar_correo(motor, consorcio_id, datos: dict):
    with transaccion(motor, persona_id=None, consorcio_id=consorcio_id) as c:
        return cola.encolar(c, correo.TIPO, carga=datos, consorcio_id=consorcio_id)


def fila(motor, id_):
    with motor.connect() as c:
        return c.execute(text("select * from plataforma.jobs where id = :i"), {"i": id_}).one()


def adelantar(motor, id_) -> None:
    """Hace disponible ya el reintento (el retroceso real es de 7,5 minutos)."""
    with motor.begin() as c:
        c.execute(
            text("update plataforma.jobs set disponible_desde = now() where id = :i"), {"i": id_}
        )


# Carga y configuración (sin servicios) ---------------------------------------------------


def test_una_carga_valida_se_acepta_y_las_claves_del_outbox_se_ignoran():
    datos = carga(evento_id="5d1c4a0e-9a43-4c5a-9a37-6a1c1b8f2a10", evento=correo.EVENTO)
    assert correo.validar(datos) == correo.CargaCorreo(
        para=PARA, asunto=ASUNTO, texto=CUERPO, html=HTML
    )
    assert correo.validar(carga(html=None)).html is None


@pytest.mark.parametrize(
    ("cambios", "problema"),
    [
        ({"para": "sin-arroba"}, "para: Value error, no es una dirección simple"),
        ({"para": "Ana <ana@ejemplo.com>"}, "para: Value error, no es una dirección simple"),
        ({"para": "a@ejemplo.com, b@ejemplo.com"}, "para: Value error, no es una dirección"),
        ({"para": "ñandú@ejemplo.com"}, "para: Value error, no es una dirección simple"),
        ({"para": "a" * 250 + "@ejemplo.com"}, "para: Value error, la dirección supera 254"),
        ({"asunto": "Hola\r\nBcc: otro@ejemplo.com"}, "asunto: Value error, el asunto tiene"),
        ({"asunto": "  "}, "asunto: Value error, el asunto está vacío"),
        ({"texto": ""}, "texto: Value error, está vacío"),
        ({"texto": 5}, "texto: Input should be a valid string"),
        ({"destinatario": PARA}, "destinatario: Extra inputs are not permitted"),
    ],
)
def test_una_carga_invalida_es_un_error_definitivo_que_no_repite_el_valor(cambios, problema):
    with pytest.raises(cola.ErrorDefinitivo) as e:
        correo.validar(carga(**cambios))
    assert problema in str(e.value)
    for valor in cambios.values():
        if isinstance(valor, str) and valor.strip():
            assert valor not in str(e.value)


def test_falta_un_campo_obligatorio():
    datos = carga()
    del datos["para"]
    with pytest.raises(cola.ErrorDefinitivo, match="para: Field required"):
        correo.validar(datos)


def test_reintentos_de_notificacion_segun_la_especificacion():
    """§12.4: 5 intentos con retroceso hasta 1 hora."""
    tipo = cola.REGISTRO.tipos[correo.TIPO]
    assert tipo.maximo_intentos == 5
    assert [tipo.retroceso(i).total_seconds() / 60 for i in range(1, 5)] == [7.5, 15, 30, 60]
    assert tipo.retroceso(10) == timedelta(hours=1)
    assert outbox.RUTAS[correo.EVENTO] == (correo.TIPO,)


def test_la_configuracion_dice_que_variable_falta_sin_mostrar_valores():
    completo = entorno_smtp(Path("x")) | {"CEM_SMTP_CLAVE": "clave-que-no-se-muestra"}
    config = correo.ConfigCorreo.desde_entorno(completo)
    assert config.puerto == int(PUERTO) and config.dominio_remitente == f"{SERVIDOR}.ejemplo.com"
    assert "clave-que-no-se-muestra" not in repr(config)
    with pytest.raises(correo.ErrorConfigCorreo, match="faltan variables del correo: CEM_SMTP_CL"):
        correo.ConfigCorreo.desde_entorno(completo | {"CEM_SMTP_CLAVE": ""})
    with pytest.raises(correo.ErrorConfigCorreo, match="CEM_SMTP_PUERTO debe ser"):
        correo.ConfigCorreo.desde_entorno(completo | {"CEM_SMTP_PUERTO": "587a"})


# Envío real ----------------------------------------------------------------------------


@pytest.mark.db
def test_el_correo_llega_con_asunto_remitente_cuerpo_y_encabezados(
    motor_app, motor_migrate, mailpit, datos
):
    id_ = encolar_correo(motor_app, datos.consorcio_a, carga())
    assert Trabajador(motor_app).procesar_lote() == 1
    f = fila(motor_migrate, id_)
    assert (f.estado, f.intentos) == ("hecho", 1)
    [m] = mailpit.mensajes()
    assert m["Subject"] == ASUNTO
    assert m["From"] == {"Name": "Consorcio en Mano", "Address": REMITENTE}
    assert [x["Address"] for x in m["To"]] == [PARA]
    detalle = mailpit.mensaje(m["ID"])
    assert detalle["Text"].strip() == CUERPO
    assert detalle["HTML"].strip() == HTML
    clave = f"trabajo-{id_}"
    assert detalle["MessageID"] == f"cem.{clave}@{SERVIDOR}.ejemplo.com"
    encabezados = mailpit.encabezados(m["ID"])
    assert encabezados["X-Cem-Clave-Envio"] == [clave]
    assert encabezados["X-Cem-Intento"] == ["1"]
    assert f.resultado == {
        "clave_envio": clave,
        "message_id": f"<cem.{clave}@{SERVIDOR}.ejemplo.com>",
    }


@pytest.mark.db
def test_a_un_dominio_invalid_no_se_intenta_enviar(motor_app, motor_migrate, mailpit, datos):
    """P3: las cuentas sintéticas del smoke de staging usan `.invalid` y no sale ningún correo.
    Control: un dominio que solo contiene `.invalid` en el medio sí se envía."""
    reservado = encolar_correo(
        motor_app, datos.consorcio_a, carga(para="smoke-ae@cem-prueba.invalid")
    )
    parecido = encolar_correo(motor_app, datos.consorcio_a, carga(para="a@x.invalid.ejemplo.com"))
    trabajador = Trabajador(motor_app)
    assert trabajador.procesar_lote() + trabajador.procesar_lote() == 2
    f = fila(motor_migrate, reservado)
    assert (f.estado, f.intentos) == ("hecho", 1)
    assert f.resultado == {"clave_envio": f"trabajo-{reservado}", "no_enviado": "dominio_reservado"}
    assert fila(motor_migrate, parecido).estado == "hecho"
    assert [[x["Address"] for x in m["To"]] for m in mailpit.mensajes()] == [
        ["a@x.invalid.ejemplo.com"]
    ]


@pytest.mark.db
def test_un_4xx_se_reintenta_y_entrega_una_sola_vez(motor_app, motor_migrate, mailpit, datos):
    id_ = encolar_correo(motor_app, datos.consorcio_a, carga(html=None))
    trabajador = Trabajador(motor_app)
    mailpit.caos(Recipient=451)
    assert trabajador.procesar_lote() == 1
    f = fila(motor_migrate, id_)
    assert (f.estado, f.intentos) == ("pendiente", 1)
    assert f.error.startswith("ConnectionError: el servidor rechazó al destinatario por ahora: 451")
    assert PARA not in f.error
    # El reintento espera el retroceso real.
    assert trabajador.procesar_lote() == 0
    assert mailpit.mensajes() == []

    mailpit.caos()
    adelantar(motor_migrate, id_)
    assert trabajador.procesar_lote() == 1
    f = fila(motor_migrate, id_)
    assert (f.estado, f.intentos) == ("hecho", 2)
    [m] = mailpit.mensajes()
    assert mailpit.encabezados(m["ID"])["X-Cem-Intento"] == ["2"]
    assert mailpit.mensaje(m["ID"])["MessageID"] == f"cem.trabajo-{id_}@{SERVIDOR}.ejemplo.com"


@pytest.mark.db
def test_un_5xx_del_destinatario_queda_muerto_con_el_error_redactado(
    motor_app, motor_migrate, mailpit, datos
):
    id_ = encolar_correo(motor_app, datos.consorcio_a, carga())
    mailpit.caos(Recipient=550)
    assert Trabajador(motor_app).procesar_lote() == 1
    f = fila(motor_migrate, id_)
    assert (f.estado, f.intentos) == ("muerto", 1)
    assert f.error.startswith("ErrorDefinitivo: el servidor rechazó al destinatario: 550")
    assert PARA not in f.error and "vecina.correo" not in f.error
    assert mailpit.mensajes() == []


@pytest.mark.db
def test_una_credencial_rechazada_queda_muerta(
    motor_app, motor_migrate, mailpit, datos, monkeypatch
):
    """Control de que la autenticación se usa de verdad: con otra clave, 535 y muerto."""
    monkeypatch.setenv("CEM_SMTP_CLAVE", "otra-clave")
    id_ = encolar_correo(motor_app, datos.consorcio_a, carga())
    assert Trabajador(motor_app).procesar_lote() == 1
    f = fila(motor_migrate, id_)
    assert (f.estado, f.intentos) == ("muerto", 1)
    assert f.error.startswith("ErrorDefinitivo: el servidor rechazó el envío (SMTPAuthentication")
    assert "otra-clave" not in f.error
    assert mailpit.mensajes() == []


@pytest.mark.db
def test_una_carga_invalida_muere_al_primer_intento_sin_enviar(
    motor_app, motor_migrate, mailpit, datos
):
    id_ = encolar_correo(motor_app, datos.consorcio_a, carga(para="no-es-una-direccion"))
    assert Trabajador(motor_app).procesar_lote() == 1
    f = fila(motor_migrate, id_)
    assert (f.estado, f.intentos) == ("muerto", 1)
    assert f.error.startswith("ErrorDefinitivo: carga de correo inválida: para:")
    assert "no-es-una-direccion" not in f.error
    assert mailpit.mensajes() == []


@pytest.mark.db
def test_sin_confiar_en_el_certificado_no_se_envia(
    motor_app, motor_migrate, mailpit, datos, monkeypatch
):
    """Control de que el certificado se verifica: con las CA del sistema, Mailpit no pasa."""
    monkeypatch.delenv("SSL_CERT_FILE")
    id_ = encolar_correo(motor_app, datos.consorcio_a, carga())
    assert Trabajador(motor_app).procesar_lote() == 1
    f = fila(motor_migrate, id_)
    assert (f.estado, f.intentos) == ("pendiente", 1)
    assert "CERTIFICATE_VERIFY_FAILED" in f.error
    assert mailpit.mensajes() == []


@pytest.mark.db
def test_sin_configuracion_se_reintenta_y_dice_que_falta(
    motor_app, motor_migrate, mailpit, datos, monkeypatch
):
    monkeypatch.delenv("CEM_SMTP_SERVIDOR")
    id_ = encolar_correo(motor_app, datos.consorcio_a, carga())
    assert Trabajador(motor_app).procesar_lote() == 1
    f = fila(motor_migrate, id_)
    assert (f.estado, f.intentos) == ("pendiente", 1)
    assert f.error == "ErrorConfigCorreo: faltan variables del correo: CEM_SMTP_SERVIDOR"


@pytest.mark.db
def test_el_evento_del_outbox_produce_exactamente_un_envio(
    motor_app, motor_migrate, mailpit, datos
):
    # Sin consorcio, como el enlace para entrar de una persona.
    with transaccion(motor_app, persona_id=None) as c:
        evento = correo.solicitar(c, para=PARA, asunto=ASUNTO, texto=CUERPO)
    assert outbox.despachar(motor_app) == 1
    assert outbox.despachar(motor_app) == 0
    trabajador = Trabajador(motor_app)
    while trabajador.procesar_lote():
        pass
    with motor_migrate.connect() as c:
        jobs = c.execute(
            text("select tipo, estado::text, clave_unica, consorcio_id from plataforma.jobs")
        ).all()
    assert jobs == [(correo.TIPO, "hecho", f"{correo.TIPO}:{evento}", None)]
    [m] = mailpit.mensajes()
    assert mailpit.encabezados(m["ID"])["X-Cem-Clave-Envio"] == [f"evento-{evento}"]
    assert mailpit.mensaje(m["ID"])["MessageID"] == f"cem.evento-{evento}@{SERVIDOR}.ejemplo.com"


@pytest.mark.db
def test_solicitar_valida_antes_de_publicar(motor_app, motor_migrate, mailpit):
    with pytest.raises(cola.ErrorDefinitivo, match="asunto: Value error"):
        with transaccion(motor_app, persona_id=None) as c:
            correo.solicitar(c, para=PARA, asunto="a\nb", texto=CUERPO)
    with motor_migrate.connect() as c:
        assert c.execute(text("select count(*) from plataforma.outbox")).scalar_one() == 0


# Registros -----------------------------------------------------------------------------


@pytest.mark.db
def test_los_registros_de_cem_trabajos_no_tienen_direccion_asunto_ni_cuerpo(
    motor_app, motor_migrate, mailpit, datos, certificado_smtp
):
    """Proceso real con los registros JSON de `cem-trabajos` (§16.1): un envío y un rechazo."""
    a = datos.consorcio_a
    bueno = encolar_correo(motor_app, a, carga())
    orden = (
        "import app.plataforma.tareas; from tests import tareas_prueba;"
        f" tareas_prueba.vaciar_cola_con_registros({url_rol('app')!r})"
    )
    entorno = os.environ | entorno_smtp(certificado_smtp) | {"CEM_PROCESO": "trabajos"}

    def correr() -> list[dict]:
        proceso = subprocess.run(
            [sys.executable, "-c", orden], cwd=RAIZ, env=entorno,
            capture_output=True, text=True, timeout=60,
        )  # fmt: skip
        assert proceso.returncode == 0, proceso.stderr
        for secreto in (PARA, "vecina.correo", ASUNTO, "frase-del-cuerpo-7f3a", CLAVE):
            assert secreto not in proceso.stdout
            assert secreto not in proceso.stderr
        return [json.loads(linea) for linea in proceso.stdout.splitlines()]

    lineas = correr()
    [enviado] = [x for x in lineas if x["mensaje"].startswith("correo enviado")]
    assert (enviado["modulo"], enviado["clave_envio"]) == ("cem.correo", f"trabajo-{bueno}")
    assert fila(motor_migrate, bueno).estado == "hecho"

    mailpit.caos(Recipient=550)
    malo = encolar_correo(motor_app, a, carga())
    lineas = correr()
    [fallido] = [x for x in lineas if x["mensaje"].startswith("trabajo fallido (muerto)")]
    assert "el servidor rechazó al destinatario: 550" in fallido["excepcion"]
    assert fila(motor_migrate, malo).estado == "muerto"


# TB-CEM-6 ------------------------------------------------------------------------------

LEER_ENTORNO = """
import os, sys

class Registrador(dict):
    leidas = set()
    def __getitem__(self, k):
        self.leidas.add(k); return super().__getitem__(k)
    def get(self, k, d=None):
        self.leidas.add(k); return super().get(k, d)
    def __contains__(self, k):
        self.leidas.add(k); return super().__contains__(k)

os.environ = Registrador(os.environ)
from fastapi.testclient import TestClient
from app.config import cargar
from app.main import crear_app
from app.db import transaccion
from app.plataforma import correo
from sqlalchemy import create_engine

app = crear_app(cargar())
assert TestClient(app).get("/health").status_code == 200
with transaccion(create_engine(os.environ["CEM_DATABASE_URL"]), persona_id=None) as c:
    correo.solicitar(c, para="a@ejemplo.com", asunto="x", texto="y")
api = sorted(k for k in Registrador.leidas if k.startswith(("CEM_SMTP", "CEM_CORREO")))
correo.ConfigCorreo.desde_entorno()
control = sorted(k for k in Registrador.leidas if k.startswith(("CEM_SMTP", "CEM_CORREO")))
print(repr((api, control)))
"""


@pytest.mark.db
def test_la_api_no_lee_las_variables_del_correo(mailpit, certificado_smtp):
    """TB-CEM-6: la API arranca, atiende y publica un correo sin leer ninguna variable SMTP.

    Control: el mismo registrador sí ve las cinco cuando lee la configuración del correo.
    """
    entorno = os.environ | entorno_smtp(certificado_smtp) | {
        "CEM_DATABASE_URL": url_rol("app"), "CEM_REDIS_URL": "memory://",
    }  # fmt: skip
    proceso = subprocess.run(
        [sys.executable, "-c", LEER_ENTORNO], cwd=RAIZ, env=entorno,
        capture_output=True, text=True, timeout=60,
    )  # fmt: skip
    assert proceso.returncode == 0, proceso.stderr
    assert proceso.stdout.strip() == str(([], sorted(correo.VARIABLES.values())))
