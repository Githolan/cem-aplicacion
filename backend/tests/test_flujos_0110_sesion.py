"""FL-CTA-01 (tanda 3, pedido a la tanda 8, 18-11-2026): `GET /auth/sesion` dice hasta cuándo la
sesión puede activar la confirmación, con la misma regla que el alta. La pantalla no repite los
10 minutos.

Contra servicios reales. Datos SINTETICOS / PRUEBA / NO REAL.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from app.identidad import segundo_factor as sf
from app.identidad import sesiones
from tests.test_identidad import app, csrf  # noqa: F401  (`app` es un fixture)
from tests.test_identidad_cierre_y_purga import entrar_como
from tests.test_identidad_tabla import nueva_persona
from tests.test_segundo_factor import BASE, con_factor, envejecer_sesion, reloj  # noqa: F401

pytestmark = [pytest.mark.db, pytest.mark.redis]
# ruff: noqa: F811

MARGEN = timedelta(minutes=1)
"""Lo que puede tardar la prueba entre la base y este reloj."""


def hasta(c) -> datetime | None:
    r = c.get("/api/v1/auth/sesion")
    assert r.status_code == 200, r.text
    valor = r.json()["alta_confirmacion_hasta"]
    return None if valor is None else datetime.fromisoformat(valor)


def test_sin_medio_cuenta_desde_que_se_entro_y_coincide_con_el_alta(app, motor_migrate):
    _, correo = nueva_persona(motor_migrate)
    c = entrar_como(app, motor_migrate, correo)
    limite = hasta(c)
    ahora = datetime.now(UTC)
    assert ahora < limite <= ahora + sf.VIGENCIA_VERIFICACION + MARGEN
    envejecer_sesion(motor_migrate, c)
    assert hasta(c) < datetime.now(UTC)
    # Lo que dice la sesión es lo que decide el alta.
    r = c.post(f"{BASE}/alta", headers=csrf(c))
    assert (r.status_code, r.json()["detalle"]) == (403, {"falta": "entrar_de_nuevo"})


def test_con_un_medio_cuenta_desde_la_ultima_verificacion(app, motor_migrate, reloj):
    c, _, _, _ = con_factor(app, motor_migrate, reloj)
    token = sesiones.hash_token(c.cookies[sesiones.COOKIE_SESION])

    def verificada(cuando) -> None:
        with motor_migrate.begin() as conn:
            conn.execute(text("update nucleo.sesion set segundo_factor_en = :v"
                              " where token_hash = :t"), {"v": cuando, "t": token})  # fmt: skip

    verificada(None)
    assert hasta(c) is None
    r = c.post(f"{BASE}/alta", headers=csrf(c))
    assert (r.status_code, r.json()["codigo"]) == (403, "segundo_factor_requerido")
    verificada(datetime.now(UTC))
    ahora = datetime.now(UTC)
    assert ahora < hasta(c) <= ahora + sf.VIGENCIA_VERIFICACION + MARGEN
    # Control: la entrada vieja no cuenta cuando ya hay un medio.
    envejecer_sesion(motor_migrate, c)
    assert hasta(c) > datetime.now(UTC)
