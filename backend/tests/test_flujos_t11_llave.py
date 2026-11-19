"""Tanda 11 (19-11-2026): dos altas de la primera llave a la vez no reemplazan los códigos que se le
mostraron a la primera. Contra PostgreSQL 18 y Redis 7 reales. Datos SINTETICOS / NO REAL.
"""

import threading
import time

import pytest
from sqlalchemy import text

from app.identidad import segundo_factor as sf
from tests.test_identidad import app, csrf  # noqa: F401  (`app` es un fixture)
from tests.test_identidad_cierre_y_purga import entrar_como
from tests.test_identidad_tabla import nueva_persona
from tests.test_llave_acceso import BASE, Autenticador, opciones_alta
from tests.test_segundo_factor import BASE as BASE_TOTP

pytestmark = [pytest.mark.db, pytest.mark.redis]
# ruff: noqa: F811

ESPERA_PRIMERA_S = 1.0
"""Lo que la primera alta tarda en confirmar después de dejar pasar a la segunda."""
ESPERA_MAXIMA_S = 10
"""Lo que la segunda espera, como mucho, a que la primera haya emitido sus códigos."""


def test_dos_altas_a_la_vez_no_reemplazan_los_codigos_de_la_primera(
    app, motor_migrate, monkeypatch
):
    """La primera alta emite sus diez códigos y queda con la transacción abierta; recién entonces
    la segunda pasa el permiso de alta y llega a contar los códigos. Sin el cerrojo antes de
    contar, la segunda contaba cero y emitía otro juego: los códigos que vio la primera dejaban
    de servir."""
    _, correo = nueva_persona(motor_migrate)
    clientes = [entrar_como(app, motor_migrate, correo) for _ in range(2)]
    credenciales = [Autenticador().crear(opciones_alta(c)) for c in clientes]

    permiso_real, emitir_real = sf.exigir_permiso_de_alta, sf.emitir_codigos
    emitio = threading.Event()
    turno = threading.Lock()
    llegadas: list[int] = []

    def permiso(conn, identidad):
        with turno:
            segunda = bool(llegadas)
            llegadas.append(1)
        if segunda:
            assert emitio.wait(ESPERA_MAXIMA_S), "la primera alta no llegó a emitir"
        return permiso_real(conn, identidad)

    def emitir(conn, claves, persona_id):
        codigos = emitir_real(conn, claves, persona_id)
        if not emitio.is_set():
            emitio.set()
            time.sleep(ESPERA_PRIMERA_S)  # la segunda ya pasó el permiso y va a contar
        return codigos

    monkeypatch.setattr(sf, "exigir_permiso_de_alta", permiso)
    monkeypatch.setattr(sf, "emitir_codigos", emitir)
    respuestas: list = [None, None]

    def alta(i: int) -> None:
        respuestas[i] = clientes[i].post(
            f"{BASE}/alta",
            json={"credencial": credenciales[i], "nombre": f"Equipo {i + 1}"},
            headers=csrf(clientes[i]),
        )

    hilos = [threading.Thread(target=alta, args=(i,)) for i in range(2)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join(30)
    assert [r.status_code for r in respuestas] == [201, 201], [r.text for r in respuestas]
    juegos = [r.json()["codigos_recuperacion"] for r in respuestas]
    entregados = [j for j in juegos if j is not None]
    # Una sola alta entrega códigos, y son los que quedaron en la base.
    assert len(entregados) == 1, juegos
    with motor_migrate.connect() as conn:
        validos = conn.execute(
            text("select count(*) from nucleo.codigo_recuperacion r join nucleo.persona p"
                 " on p.id = r.persona_id where p.correo = :c and r.usado_en is null"),
            {"c": correo},
        ).scalar_one()  # fmt: skip
    assert validos == sf.CANTIDAD_RECUPERACION
    # Control: el primer código entregado verifica una sesión nueva.
    otra = entrar_como(app, motor_migrate, correo)
    r = otra.post(f"{BASE_TOTP}/verificar", json={"codigo": entregados[0][0]}, headers=csrf(otra))
    assert (r.status_code, r.json()["codigos_recuperacion_restantes"]) == (200, 9), r.text
