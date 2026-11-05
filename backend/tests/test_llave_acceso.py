"""Llave de acceso (WebAuthn; migración 0070) contra PostgreSQL 18 y Redis 7 reales.

`Autenticador` hace lo que el dispositivo: guarda una clave ES256 y firma lo que le pide el
navegador. Las respuestas las comprueba la biblioteca real, sin nada simulado del lado del
servidor.
"""

import base64
import hashlib
import json
import struct
from datetime import timedelta
from urllib.parse import urlsplit
from uuid import uuid4

import cbor2
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy import text

from app.identidad import segundo_factor as sf
from tests.test_identidad import (  # noqa: F401  (`app` es un fixture)
    ORIGEN,
    Navegadores,
    app,
    csrf,
    navegador,
)
from tests.test_identidad_cierre_y_purga import entrar_como
from tests.test_identidad_tabla import nueva_persona
from tests.test_segundo_factor import BASE as BASE_TOTP
from tests.test_segundo_factor import avisos, con_factor, reloj  # noqa: F401

pytestmark = [pytest.mark.db, pytest.mark.redis]

BASE = "/api/v1/auth/llave"
SITIO = urlsplit(ORIGEN).hostname
PRESENCIA, GESTO, CON_CREDENCIAL = 0x01, 0x04, 0x40


def b64(datos: bytes) -> str:
    return base64.urlsafe_b64encode(datos).rstrip(b"=").decode()


class Autenticador:
    def __init__(self, sitio: str = SITIO, origen: str = ORIGEN) -> None:
        self.clave = ec.generate_private_key(ec.SECP256R1())
        self.credencial = uuid4().bytes + uuid4().bytes
        self.sitio, self.origen = sitio, origen
        self.contador = 0

    def _cliente(self, tipo: str, desafio: str) -> bytes:
        return json.dumps({"type": tipo, "challenge": desafio, "origin": self.origen}).encode()

    def _datos(self, banderas: int, extra: bytes = b"") -> bytes:
        sitio = hashlib.sha256(self.sitio.encode()).digest()
        return sitio + bytes([banderas]) + struct.pack(">I", self.contador) + extra

    def crear(self, opciones: dict, gesto: bool = True) -> dict:
        numeros = self.clave.public_key().public_numbers()
        publica = cbor2.dumps(
            {
                1: 2,
                3: -7,
                -1: 1,
                -2: numeros.x.to_bytes(32, "big"),
                -3: numeros.y.to_bytes(32, "big"),
            }
        )
        extra = bytes(16) + struct.pack(">H", len(self.credencial)) + self.credencial + publica
        banderas = PRESENCIA | CON_CREDENCIAL | (GESTO if gesto else 0)
        objeto = cbor2.dumps(
            {"fmt": "none", "attStmt": {}, "authData": self._datos(banderas, extra)}
        )
        return {
            "id": b64(self.credencial),
            "rawId": b64(self.credencial),
            "type": "public-key",
            "clientExtensionResults": {},
            "response": {
                "clientDataJSON": b64(self._cliente("webauthn.create", opciones["challenge"])),
                "attestationObject": b64(objeto),
            },
        }

    def firmar(self, opciones: dict, gesto: bool = True, contador: int | None = None) -> dict:
        self.contador = self.contador + 1 if contador is None else contador
        datos = self._datos(PRESENCIA | (GESTO if gesto else 0))
        cliente = self._cliente("webauthn.get", opciones["challenge"])
        firma = self.clave.sign(datos + hashlib.sha256(cliente).digest(), ec.ECDSA(hashes.SHA256()))
        return {
            "id": b64(self.credencial),
            "rawId": b64(self.credencial),
            "type": "public-key",
            "clientExtensionResults": {},
            "response": {
                "clientDataJSON": b64(cliente),
                "authenticatorData": b64(datos),
                "signature": b64(firma),
            },
        }


def opciones_alta(c) -> dict:
    r = c.post(f"{BASE}/alta/opciones", headers=csrf(c))
    assert r.status_code == 200, r.text
    return r.json()["opciones"]


def opciones_verificar(c) -> dict:
    r = c.post(f"{BASE}/verificar/opciones", headers=csrf(c))
    assert r.status_code == 200, r.text
    return r.json()["opciones"]


def registrar(c, autenticador: Autenticador) -> dict:
    credencial = autenticador.crear(opciones_alta(c))
    r = c.post(f"{BASE}/alta", json={"credencial": credencial, "nombre": "Mi PC"}, headers=csrf(c))
    assert r.status_code == 201, r.text
    return r.json()


def verificar(c, respuesta: dict):
    return c.post(f"{BASE}/verificar", json={"credencial": respuesta}, headers=csrf(c))


def con_llave(app, motor_migrate):  # noqa: F811
    _, correo = nueva_persona(motor_migrate)
    c = entrar_como(app, motor_migrate, correo)
    autenticador = Autenticador()
    alta = registrar(c, autenticador)
    return c, autenticador, alta, correo


def sin_verificar(motor_migrate, correo: str) -> None:
    """La verificación de las sesiones de la persona vence."""
    with motor_migrate.begin() as conn:
        conn.execute(
            text(
                "update nucleo.sesion s set segundo_factor_en = null from nucleo.persona p"
                " where p.id = s.persona_id and p.correo = :c"
            ),
            {"c": correo},
        )


def verificada(motor_migrate, correo: str) -> bool:
    with motor_migrate.connect() as conn:
        return conn.execute(
            text(
                "select bool_or(s.segundo_factor_en is not null) from nucleo.sesion s"
                " join nucleo.persona p on p.id = s.persona_id where p.correo = :c"
            ),
            {"c": correo},
        ).scalar_one()


# Alta ---------------------------------------------------------------------------------------


def test_el_alta_entrega_codigos_verifica_y_avisa(app, motor_migrate):  # noqa: F811
    c, _, alta, correo = con_llave(app, motor_migrate)
    assert len(alta["codigos_recuperacion"]) == sf.CANTIDAD_RECUPERACION
    assert verificada(motor_migrate, correo)
    assert "Agregaste una llave de acceso" in avisos(motor_migrate, correo)
    llaves = c.get(BASE).json()
    assert [k["nombre"] for k in llaves] == ["Mi PC"]
    assert c.get(BASE_TOTP).json()["codigos_recuperacion_restantes"] == sf.CANTIDAD_RECUPERACION


def test_las_opciones_exigen_el_gesto_y_el_sitio_real(app, motor_migrate):  # noqa: F811
    _, correo = nueva_persona(motor_migrate)
    c = entrar_como(app, motor_migrate, correo)
    opciones = opciones_alta(c)
    assert opciones["rp"]["id"] == SITIO
    assert opciones["authenticatorSelection"]["userVerification"] == "required"
    assert opciones["attestation"] == "none"


def test_sin_gesto_no_se_registra(app, motor_migrate):  # noqa: F811
    _, correo = nueva_persona(motor_migrate)
    c = entrar_como(app, motor_migrate, correo)
    credencial = Autenticador().crear(opciones_alta(c), gesto=False)
    r = c.post(f"{BASE}/alta", json={"credencial": credencial, "nombre": "x"}, headers=csrf(c))
    assert r.status_code == 400
    assert c.get(BASE).json() == []


def test_una_entrada_vieja_no_alcanza_para_la_primera_llave(app, motor_migrate):  # noqa: F811
    _, correo = nueva_persona(motor_migrate)
    c = entrar_como(app, motor_migrate, correo)
    with motor_migrate.begin() as conn:
        conn.execute(
            text(
                "update nucleo.sesion s set creada_en = creada_en - :v from nucleo.persona p"
                " where p.id = s.persona_id and p.correo = :c"
            ),
            {"c": correo, "v": timedelta(minutes=11)},
        )
    r = c.post(f"{BASE}/alta/opciones", headers=csrf(c))
    assert r.status_code == 403
    assert r.json()["detalle"] == {"falta": "entrar_de_nuevo"}


def test_con_una_llave_otra_pide_confirmar(app, motor_migrate):  # noqa: F811
    c, autenticador, _, correo = con_llave(app, motor_migrate)
    sin_verificar(motor_migrate, correo)
    r = c.post(f"{BASE}/alta/opciones", headers=csrf(c))
    assert r.status_code == 403
    assert r.json()["codigo"] == "segundo_factor_requerido"
    # Control: verificada, sí puede, y los códigos no se reemplazan.
    assert verificar(c, autenticador.firmar(opciones_verificar(c))).status_code == 200
    assert registrar(c, Autenticador())["codigos_recuperacion"] is None


def test_con_totp_una_llave_pide_confirmar(app, motor_migrate, reloj):  # noqa: F811
    c, _, _, correo = con_factor(app, motor_migrate, reloj)
    sin_verificar(motor_migrate, correo)
    assert c.post(f"{BASE}/alta/opciones", headers=csrf(c)).status_code == 403


def test_con_llave_el_totp_pide_confirmar(app, motor_migrate):  # noqa: F811
    c, _, _, correo = con_llave(app, motor_migrate)
    sin_verificar(motor_migrate, correo)
    assert c.post(f"{BASE_TOTP}/alta", headers=csrf(c)).status_code == 403


# Verificación -------------------------------------------------------------------------------


def test_verificar_deja_la_sesion_verificada(app, motor_migrate):  # noqa: F811
    c, autenticador, _, correo = con_llave(app, motor_migrate)
    sin_verificar(motor_migrate, correo)
    assert not verificada(motor_migrate, correo)
    opciones = opciones_verificar(c)
    assert opciones["userVerification"] == "required"
    r = verificar(c, autenticador.firmar(opciones))
    assert r.status_code == 200, r.text
    assert verificada(motor_migrate, correo)
    assert c.get(BASE).json()[0]["ultimo_uso_en"] is not None


def rechaza(c, motor_migrate, correo, respuesta) -> None:
    sin_verificar(motor_migrate, correo)
    r = verificar(c, respuesta)
    assert r.status_code == 400, r.text
    assert r.json()["codigo"] == "segundo_factor_invalido"
    assert not verificada(motor_migrate, correo)


def test_el_desafio_no_se_repite(app, motor_migrate):  # noqa: F811
    c, autenticador, _, correo = con_llave(app, motor_migrate)
    opciones = opciones_verificar(c)
    assert verificar(c, autenticador.firmar(opciones)).status_code == 200
    rechaza(c, motor_migrate, correo, autenticador.firmar(opciones))


def test_una_respuesta_mala_gasta_el_desafio(app, motor_migrate):  # noqa: F811
    """El desafío se gasta aparte: si volviera con el error, se podría insistir con él."""
    c, autenticador, _, correo = con_llave(app, motor_migrate)
    opciones = opciones_verificar(c)
    mala = autenticador.firmar(opciones)
    mala["response"]["signature"] = b64(b"firma falsa")
    rechaza(c, motor_migrate, correo, mala)
    rechaza(c, motor_migrate, correo, autenticador.firmar(opciones))


def test_el_desafio_vence(app, motor_migrate):  # noqa: F811
    c, autenticador, _, correo = con_llave(app, motor_migrate)
    opciones = opciones_verificar(c)
    with motor_migrate.begin() as conn:
        conn.execute(text("update nucleo.desafio_llave set vence_en = now() - interval '1 second'"))
    rechaza(c, motor_migrate, correo, autenticador.firmar(opciones))


def test_el_desafio_de_otra_sesion_no_sirve(app, motor_migrate):  # noqa: F811
    c, autenticador, _, correo = con_llave(app, motor_migrate)
    otra = entrar_como(app, motor_migrate, correo)
    opciones = opciones_verificar(otra)
    sin_verificar(motor_migrate, correo)
    assert verificar(c, autenticador.firmar(opciones)).status_code == 400
    assert not verificada(motor_migrate, correo)


def test_el_desafio_de_alta_no_verifica(app, motor_migrate):  # noqa: F811
    c, autenticador, _, correo = con_llave(app, motor_migrate)
    opciones = opciones_alta(c)
    rechaza(c, motor_migrate, correo, autenticador.firmar(opciones))


def test_la_llave_de_otra_persona_no_sirve(app, motor_migrate):  # noqa: F811
    c, _, _, correo = con_llave(app, motor_migrate)
    _, ajena, _, _ = con_llave(app, motor_migrate)
    rechaza(c, motor_migrate, correo, ajena.firmar(opciones_verificar(c)))


def test_sin_gesto_no_verifica(app, motor_migrate):  # noqa: F811
    c, autenticador, _, correo = con_llave(app, motor_migrate)
    rechaza(c, motor_migrate, correo, autenticador.firmar(opciones_verificar(c), gesto=False))


def test_otro_origen_no_sirve(app, motor_migrate):  # noqa: F811
    c, autenticador, _, correo = con_llave(app, motor_migrate)
    autenticador.origen = "https://cem.falso.test"
    rechaza(c, motor_migrate, correo, autenticador.firmar(opciones_verificar(c)))


def test_otro_sitio_no_sirve(app, motor_migrate):  # noqa: F811
    c, autenticador, _, correo = con_llave(app, motor_migrate)
    autenticador.sitio = "cem.falso.test"
    rechaza(c, motor_migrate, correo, autenticador.firmar(opciones_verificar(c)))


def test_un_contador_que_retrocede_no_sirve(app, motor_migrate):  # noqa: F811
    c, autenticador, _, correo = con_llave(app, motor_migrate)
    assert verificar(c, autenticador.firmar(opciones_verificar(c), contador=5)).status_code == 200
    rechaza(c, motor_migrate, correo, autenticador.firmar(opciones_verificar(c), contador=5))


def test_un_identificador_ilegible_no_rompe(app, motor_migrate):  # noqa: F811
    c, autenticador, _, correo = con_llave(app, motor_migrate)
    respuesta = autenticador.firmar(opciones_verificar(c))
    respuesta["rawId"] = 7
    rechaza(c, motor_migrate, correo, respuesta)


def test_sin_llave_no_hay_opciones(app, motor_migrate):  # noqa: F811
    _, correo = nueva_persona(motor_migrate)
    c = entrar_como(app, motor_migrate, correo)
    r = c.post(f"{BASE}/verificar/opciones", headers=csrf(c))
    assert r.status_code == 409
    assert r.json()["codigo"] == "segundo_factor_sin_alta"


def test_sin_csrf_se_niega(app, motor_migrate):  # noqa: F811
    c, _, _, _ = con_llave(app, motor_migrate)
    assert c.post(f"{BASE}/verificar/opciones").status_code == 403


# Recuperación y baja ------------------------------------------------------------------------


def test_un_codigo_de_recuperacion_sirve_sin_totp_y_deja_sumar_otra(app, motor_migrate):  # noqa: F811
    """Perder el dispositivo: el código verifica la sesión y deja registrar otra llave."""
    c, _, alta, correo = con_llave(app, motor_migrate)
    sin_verificar(motor_migrate, correo)
    r = c.post(
        f"{BASE_TOTP}/verificar",
        json={"codigo": alta["codigos_recuperacion"][0]},
        headers=csrf(c),
    )
    assert r.status_code == 200, r.text
    assert registrar(c, Autenticador())["codigos_recuperacion"] is None
    assert len(c.get(BASE).json()) == 2


def test_quitar_pide_verificacion_reciente(app, motor_migrate):  # noqa: F811
    c, _, _, correo = con_llave(app, motor_migrate)
    id_ = c.get(BASE).json()[0]["id"]
    sin_verificar(motor_migrate, correo)
    assert c.delete(f"{BASE}/{id_}", headers=csrf(c)).status_code == 403
    assert len(c.get(BASE).json()) == 1


def test_quitar_la_ultima_borra_los_codigos(app, motor_migrate):  # noqa: F811
    c, _, _, correo = con_llave(app, motor_migrate)
    id_ = c.get(BASE).json()[0]["id"]
    assert c.delete(f"{BASE}/{id_}", headers=csrf(c)).status_code == 204
    assert c.get(BASE).json() == []
    assert c.get(BASE_TOTP).json()["codigos_recuperacion_restantes"] == 0
    assert not verificada(motor_migrate, correo)
    assert "Quitaste una llave de acceso" in avisos(motor_migrate, correo)


def test_quitar_una_de_dos_conserva_los_codigos(app, motor_migrate):  # noqa: F811
    c, _, _, _ = con_llave(app, motor_migrate)
    registrar(c, Autenticador())
    id_ = c.get(BASE).json()[0]["id"]
    assert c.delete(f"{BASE}/{id_}", headers=csrf(c)).status_code == 204
    assert c.get(BASE_TOTP).json()["codigos_recuperacion_restantes"] == sf.CANTIDAD_RECUPERACION


def test_quitar_una_ajena_da_404(app, motor_migrate):  # noqa: F811
    c, _, _, _ = con_llave(app, motor_migrate)
    otra, _, _, _ = con_llave(app, motor_migrate)
    id_ = otra.get(BASE).json()[0]["id"]
    assert c.delete(f"{BASE}/{id_}", headers=csrf(c)).status_code == 404
    assert len(otra.get(BASE).json()) == 1


def test_bajar_el_totp_conserva_los_codigos_si_hay_llave(app, motor_migrate, reloj):  # noqa: F811
    c, _, _, _ = con_factor(app, motor_migrate, reloj)
    registrar(c, Autenticador())
    assert c.post(f"{BASE_TOTP}/baja", headers=csrf(c)).status_code == 204
    assert c.get(BASE_TOTP).json()["codigos_recuperacion_restantes"] == sf.CANTIDAD_RECUPERACION


# Base ---------------------------------------------------------------------------------------


def test_la_rls_solo_deja_ver_lo_propio(app, motor_migrate, motor_app):  # noqa: F811
    con_llave(app, motor_migrate)
    with motor_app.connect() as conn:
        assert conn.execute(text("select count(*) from nucleo.llave_acceso")).scalar_one() == 0


def test_la_llave_cuenta_para_la_privacidad_y_no_para_la_contrasena(app, motor_migrate):  # noqa: F811
    """La entrada con contraseña pide un código: quien solo tiene llave no tiene cuál dar."""
    _, _, _, correo = con_llave(app, motor_migrate)
    with motor_migrate.connect() as conn:
        fila = conn.execute(
            text(
                "select nucleo.tiene_segundo_factor(id) as factor,"
                " nucleo.tiene_totp_activo(id) as totp from nucleo.persona where correo = :c"
            ),
            {"c": correo},
        ).one()
    assert fila.factor is True
    assert fila.totp is False


def test_la_supresion_borra_las_llaves(motor_migrate, datos):
    firma = "plataforma.privacidad_supresion(plataforma.caso_privacidad, boolean)"
    with motor_migrate.connect() as conn:
        definicion = conn.execute(
            text("select pg_get_functiondef(cast(:f as regprocedure))"), {"f": firma}
        ).scalar_one()
    assert "delete from nucleo.llave_acceso where persona_id = k.persona_id" in definicion
