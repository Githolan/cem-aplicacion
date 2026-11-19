"""Tanda 10 de la revisión por flujos (18-11-2026): códigos de recuperación nuevos sin desactivar el
medio (FL-CTA-05). Contra PostgreSQL 18 y Redis 7 reales. Datos SINTETICOS / NO REAL.
"""

import threading
import time

import pytest
from sqlalchemy import text

from app.identidad import segundo_factor as sf
from tests.test_identidad import app, csrf  # noqa: F401  (`app` es un fixture)
from tests.test_identidad_cierre_y_purga import entrar_como
from tests.test_identidad_tabla import nueva_persona
from tests.test_segundo_factor import BASE, avisos, con_factor, reloj  # noqa: F401  (fixture)

pytestmark = [pytest.mark.db, pytest.mark.redis]
# ruff: noqa: F811

NUEVOS = f"{BASE}/codigos-recuperacion"
ESPERA_PRIMERO_S = 1.0
"""Lo que el primer reemplazo tarda en confirmar después de que el segundo arrancó."""


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


def test_dos_pedidos_a_la_vez_dejan_un_solo_juego_de_diez(app, motor_migrate, reloj, monkeypatch):
    """Codex en el PR #262: sin el cerrojo por persona, dos reemplazos superpuestos borraban el
    juego viejo cada uno e insertaban diez cada uno (veinte válidos). El primero reemplaza y queda
    con la transacción abierta; el segundo arranca su reemplazo antes de que el primero confirme."""
    _, _, viejos, correo = con_factor(app, motor_migrate, reloj)
    clientes = []
    for codigo in viejos[:2]:
        c = entrar_como(app, motor_migrate, correo)
        r = c.post(f"{BASE}/verificar", json={"codigo": codigo}, headers=csrf(c))
        assert r.status_code == 200, r.text
        clientes.append(c)
    real = sf.emitir_codigos
    barrera = threading.Barrier(2, timeout=10)
    llegadas: list[int] = []
    turno = threading.Lock()

    def superpuesta(conn, claves, persona_id):
        with turno:
            primero = not llegadas
            llegadas.append(1)
        if primero:
            codigos = real(conn, claves, persona_id)  # reemplazó, sin confirmar todavía
            barrera.wait()
            time.sleep(ESPERA_PRIMERO_S)  # el segundo ya empezó su reemplazo
            return codigos
        barrera.wait()
        return real(conn, claves, persona_id)

    monkeypatch.setattr(sf, "emitir_codigos", superpuesta)
    respuestas: list = [None, None]

    def pedir(i: int) -> None:
        respuestas[i] = clientes[i].post(NUEVOS, headers=csrf(clientes[i]))

    hilos = [threading.Thread(target=pedir, args=(i,)) for i in range(2)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join(30)
    assert [r.status_code for r in respuestas] == [200, 200]
    with motor_migrate.connect() as conn:
        validos = conn.execute(
            text("select count(*) from nucleo.codigo_recuperacion r join nucleo.persona p"
                 " on p.id = r.persona_id where p.correo = :c and r.usado_en is null"),
            {"c": correo},
        ).scalar_one()  # fmt: skip
    assert validos == sf.CANTIDAD_RECUPERACION
