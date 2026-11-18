"""Solicitudes formales de la tanda 8 (`0110`; 18-11-2026): la respuesta de una solicitud vencida
que llegó por otro medio (pregunta 15, FL-SOL-03), si hay administración vigente (tanda 7) y el
estado del archivo adjunto al envío (FL-SOL-04).

Contra servicios reales. Datos SINTETICOS / PRUEBA / NO REAL.
"""

import pytest

from tests.test_documentos_2c1 import (  # noqa: F401  (los fixtures se importan)
    analizar,
    bucket_con_versiones,
    firmas_de_la_imagen,
)
from tests.test_rutas_egresos import codigo, post
from tests.test_solicitud_formal_0108 import (
    adjunto,
    correr_el_envio,
    emitida,
    enviar,
    firmar,
    lista,
    preparar,
    recordar_hoy,
    sin_cuenta,
    una,
)
from tests.test_solicitud_formal_0108 import e as e  # noqa: F401  (es un fixture)
from tests.test_solicitud_formal_0108 import x as x  # noqa: F401  (es un fixture)

pytestmark = [pytest.mark.db, pytest.mark.redis]
# ruff: noqa: F811

RECIBIDA = {"texto": "Llegó por carta SINTETICA"}


def test_el_consejo_registra_la_respuesta_vencida_de_un_destinatario_con_cuenta(e, motor_migrate):
    s = emitida(e)
    cuerpo = RECIBIDA | {"recibida_el": s["envio"]["fecha_envio"]}
    # Mientras corre el plazo, la da el destinatario en la aplicación (P2).
    assert "registrar_respuesta" not in una(e, e.mc1, s)["acciones"]
    r = post(e, e.mc1, f"solicitudes-formales/{s['id']}/respuesta-recibida", cuerpo)
    assert codigo(r) == (409, "destinatario_con_cuenta")
    correr_el_envio(motor_migrate, s, s["plazo_dias"] + 1)
    assert una(e, e.mc1, s)["estado"] == "vencida"
    assert "registrar_respuesta" in una(e, e.mc1, s)["acciones"]
    cuerpo = RECIBIDA | {"recibida_el": s["envio"]["fecha_envio"]}
    r = post(e, e.mc1, f"solicitudes-formales/{s['id']}/respuesta-recibida", cuerpo)
    assert r.status_code == 200, r.text
    assert (r.json()["estado"], r.json()["respuesta"]["medio"]) == ("respondida", "medio_formal")
    # Una sola respuesta: el destinatario ya no responde.
    r = post(e, e.adm, f"solicitudes-formales/{s['id']}/respuesta", {"texto": "Tarde"})
    assert codigo(r) == (409, "solicitud_no_admite")


def test_la_lista_dice_si_hay_administracion_vigente(e, motor_migrate, motor_app):
    assert lista(e, e.mc1)["hay_administrador_vigente"] is True
    sola = sin_cuenta(motor_migrate, motor_app, con_externo=False)
    assert lista(sola, sola.mc1)["hay_administrador_vigente"] is False


def test_el_envio_dice_como_va_el_analisis_de_su_comprobante(
    x, motor_app, bucket_con_versiones, firmas_de_la_imagen
):
    recordar_hoy(motor_app, x)
    s = preparar(x)
    firmar(x, x.mc1, s)
    firmar(x, x.mc2, s)
    assert enviar(x, x.mc1, s, adjunto(x, x.mc1, s, "envio")).status_code == 200
    envio = una(x, x.mc1, s)["envio"]
    assert (envio["estado_archivo"], envio["descargable"]) == ("en_cuarentena", False)
    assert analizar(motor_app) == 1
    envio = una(x, x.mc1, s)["envio"]
    assert (envio["estado_archivo"], envio["descargable"]) == ("disponible", True)
