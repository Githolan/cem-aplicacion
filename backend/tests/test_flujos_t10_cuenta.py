"""Tanda 10 de la revisión por flujos (18-11-2026): códigos de recuperación nuevos sin desactivar el
medio (FL-CTA-05). Contra PostgreSQL 18 y Redis 7 reales. Datos SINTETICOS / NO REAL.
"""

import pytest

from app.identidad import segundo_factor as sf
from tests.test_identidad import app, csrf  # noqa: F401  (`app` es un fixture)
from tests.test_identidad_cierre_y_purga import entrar_como
from tests.test_identidad_tabla import nueva_persona
from tests.test_segundo_factor import BASE, avisos, con_factor, reloj  # noqa: F401  (fixture)

pytestmark = [pytest.mark.db, pytest.mark.redis]
# ruff: noqa: F811

NUEVOS = f"{BASE}/codigos-recuperacion"


def test_codigos_nuevos_piden_verificacion_reciente_e_invalidan_los_anteriores(
    app, motor_migrate, reloj
):
    _, secreto, viejos, correo = con_factor(app, motor_migrate, reloj)
    c = entrar_como(app, motor_migrate, correo)
    # Sin verificar en esta sesión, se pide el segundo factor (como la baja).
    r = c.post(NUEVOS, headers=csrf(c))
    assert (r.status_code, r.json()["codigo"]) == (403, "segundo_factor_requerido")
    assert c.post(NUEVOS).status_code == 403  # sin CSRF
    # Gasta uno de los viejos para verificar: quedan 9.
    r = c.post(f"{BASE}/verificar", json={"codigo": viejos[0]}, headers=csrf(c))
    assert r.json()["codigos_recuperacion_restantes"] == 9
    r = c.post(NUEVOS, headers=csrf(c))
    assert r.status_code == 200, r.text
    assert r.headers["Cache-Control"] == "no-store"
    nuevos = r.json()["codigos"]
    assert len(nuevos) == sf.CANTIDAD_RECUPERACION and not set(nuevos) & set(viejos)
    assert c.get(BASE).json()["codigos_recuperacion_restantes"] == sf.CANTIDAD_RECUPERACION
    assert c.get(BASE).json()["activo"] is True  # el medio sigue
    assert avisos(motor_migrate, correo)[-1] == sf.ASUNTO_CODIGOS
    # Los viejos ya no sirven; los nuevos sí (control).
    otra = entrar_como(app, motor_migrate, correo)
    assert otra.post(f"{BASE}/verificar", json={"codigo": viejos[1]},
                     headers=csrf(otra)).status_code == 400  # fmt: skip
    r = otra.post(f"{BASE}/verificar", json={"codigo": nuevos[0]}, headers=csrf(otra))
    assert (r.status_code, r.json()["codigos_recuperacion_restantes"]) == (200, 9)
    # Con el código de la aplicación también se verifica (control de que el medio sigue activo).
    reloj.paso += 1
    tercera = entrar_como(app, motor_migrate, correo)
    r = tercera.post(f"{BASE}/verificar", json={"codigo": sf.codigo_en(secreto, reloj.paso)},
                     headers=csrf(tercera))  # fmt: skip
    assert r.status_code == 200


def test_sin_medio_no_hay_codigos_que_renovar(app, motor_migrate, reloj):
    _, correo = nueva_persona(motor_migrate)
    c = entrar_como(app, motor_migrate, correo)
    # Sin medio no hay verificación posible: se niega antes, por falta de verificación.
    assert c.post(NUEVOS, headers=csrf(c)).status_code == 403
    assert c.get(BASE).json()["codigos_recuperacion_restantes"] == 0
