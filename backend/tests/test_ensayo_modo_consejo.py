"""El ensayo del P0 en Modo Consejo (`pruebas/ensayo_modo_consejo_staging.py`, H6) corre entero
contra un backend real, como el smoke: uvicorn con la base y el Redis de prueba, el almacén S3 y
el ClamAV de prueba, y un hilo que hace de `cem-trabajos` (antivirus y lectura de la liquidación).
Después corre su exportación con la credencial de exportación. Corre una sola vez por consorcio y
su guarda se niega fuera de staging sin escribir. Datos SINTETICOS / NO REAL."""

import base64
import json
import re
import threading
from dataclasses import replace
from datetime import date
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from app.plataforma.trabajos import Trabajador
from pruebas import ensayo_modo_consejo_staging as ensayo
from tests.test_documentos_2c1 import bucket, firmas_de_la_imagen  # noqa: F401
from tests.test_exportacion_2c3 import con_url  # noqa: F401
from tests.test_mandatos import otra_persona
from tests.test_mandatos_0012 import alta
from tests.test_modo_operacion import consorcio_con_persona, decidir_equipo
from tests.test_orden_pago_neutra import nueva_sesion
from tests.test_smoke_staging import HOST, ORIGEN_STAGING, entorno, servidor  # noqa: F401

# ruff: noqa: F811

__all__ = ["bucket", "firmas_de_la_imagen"]


@pytest.fixture
def trabajos(motor_app, bucket):  # noqa: F811
    """Lo que en staging hace `cem-trabajos`: procesa la cola mientras corre el ensayo."""
    alto = threading.Event()

    def bucle():
        trabajador = Trabajador(motor_app)
        while not alto.is_set():
            if trabajador.procesar_lote() == 0:
                alto.wait(0.5)

    hilo = threading.Thread(target=bucle, daemon=True)
    hilo.start()
    yield
    alto.set()
    hilo.join(30)


def correr(argumentos: list[str], capsys) -> tuple[int, dict | None, str]:
    codigo = ensayo.main(argumentos)
    salida = capsys.readouterr()
    inicio = salida.out.find("\n{")
    inicio = 0 if salida.out.startswith("{") else inicio
    return codigo, (json.loads(salida.out[inicio:]) if inicio >= 0 else None), salida.err


def corrida(servidor, base: str, consorcio, *extra: str) -> list[str]:  # noqa: F811
    return ["--consorcio", str(consorcio), "--base", base, "--url", servidor, "--host", HOST,
            *extra]  # fmt: skip


# Lo que no necesita la base ----------------------------------------------------------------


def test_la_liquidacion_sintetica_la_lee_el_lector_determinista_sin_diferencias():
    pdf = ensayo.pdf_de(ensayo.liquidacion_sintetica(), "Liquidacion LAB SINTETICA")
    esperada = ensayo.lectura_esperada(pdf)
    # 2 rubros, 3 gastos, 3 unidades con su importe y su saldo, y los dos totales.
    assert esperada == ensayo.LecturaEsperada(date(2026, 9, 1), 16, 0, "no_encontrado")
    assert b"SINTETICA" in pdf


def test_un_pdf_que_el_lector_no_reconoce_no_se_sube():
    with pytest.raises(ensayo.Negado, match="iría al modelo"):
        ensayo.lectura_esperada(ensayo.pdf_documento("Factura LAB SINTETICA"))


def test_cuenta_cuit_e_id_sinteticos():
    cuenta = ensayo.cbu_sintetico(4_200_012_345)
    assert len(cuenta) == 22 and cuenta.startswith("999")
    pesos = ensayo.PESOS_1
    assert cuenta[7] == ensayo._digito(cuenta[:7], pesos)
    assert cuenta[21] == ensayo._digito(cuenta[8:21], ensayo.PESOS_2)
    cuit = ensayo.cuit_sintetico(70_000_020)
    pares = zip(cuit[:10], (5, 4, 3, 2, 7, 6, 5, 4, 3, 2), strict=True)
    resto = 11 - sum(int(x) * w for x, w in pares) % 11
    assert len(cuit) == 11 and int(cuit[10]) == (0 if resto == 11 else resto)
    for _ in range(200):
        assert re.search(r"[0-9]{5}", ensayo.id_de_corrida()) is None


def test_la_guarda_exige_staging_la_base_y_el_host():
    base = "postgres://u:c@cem-postgres-staging/cem"
    ensayo.exigir_staging("https://cem-staging.ejemplo.com", base, "cem-postgres-staging",
                          "cem-staging.ejemplo.com")  # fmt: skip
    for origen, b, host, motivo in (
        ("https://cem.ejemplo.com", "cem-postgres-staging", HOST, "no es de staging"),
        (ORIGEN_STAGING, "cem-postgres", HOST, "no apunta a --base"),
        (ORIGEN_STAGING, "cem-postgres-staging", "cem.ejemplo.com", "no es el de"),
    ):
        with pytest.raises(ensayo.Negado, match=motivo):
            ensayo.exigir_staging(origen, base, b, host)


# El ensayo -----------------------------------------------------------------------------------


@pytest.mark.db
@pytest.mark.redis
def test_el_ensayo_recorre_todo_exporta_y_no_se_repite(
    servidor, entorno, trabajos, con_url, capsys, motor_migrate, monkeypatch
):
    consorcio = uuid4()
    codigo, resumen, error = correr(corrida(servidor, entorno, consorcio, "--simular"), capsys)
    assert codigo == 0, error
    assert resumen["lectura_esperada"]["enlace"] == "no_encontrado"
    with motor_migrate.connect() as c:
        assert c.execute(text("select count(*) from nucleo.consorcio where id = :c"),
                         {"c": consorcio}).scalar_one() == 0  # simular no escribe  # fmt: skip

    codigo, resumen, error = correr(corrida(servidor, entorno, consorcio), capsys)
    assert codigo == 0, error
    assert resumen["sesiones_abiertas_al_cerrar"] == 0
    assert resumen["navegadores_que_siguen_entrando"] == []
    assert len(resumen["controles"]) == 15, resumen["controles"]
    exportacion = resumen["exportacion"]
    with motor_migrate.connect() as c:
        nombre, perfil, modo = c.execute(text(
            "select c.nombre, (select count(*) from nucleo.consorcio_perfil_normativo p"
            " where p.consorcio_id = c.id), (select modo::text from nucleo.modo_operacion m"
            " where m.consorcio_id = c.id) from nucleo.consorcio c where c.id = :c"),
            {"c": consorcio}).one()  # fmt: skip
        estados = c.execute(text(
            "select estado::text, count(*) from nucleo.importacion_liquidacion"
            " where consorcio_id = :c group by 1"), {"c": consorcio}).all()  # fmt: skip
        vigentes = c.execute(text(
            "select count(*) from nucleo.mandato where consorcio_id = :c"
            " and (vigente_hasta is null or vigente_hasta > vigente_desde)"),
            {"c": consorcio}).scalar_one()  # fmt: skip
    assert (nombre, perfil, modo) == (ensayo.NOMBRE_CONSORCIO, 1, "consejo")
    assert dict(estados) == {"reemplazada": 1, "confirmada": 1}
    assert vigentes == 1  # el de consejo1, para la exportación

    datos = base64.b64encode(json.dumps({"exportacion": exportacion}).encode()).decode()
    codigo, salida, error = correr(["--exportar", "--base", entorno, "--datos", datos], capsys)
    assert codigo == 0, error
    assert len(salida["controles"]) == 2, salida
    with motor_migrate.connect() as c:
        vigentes = c.execute(text(
            "select count(*) from nucleo.mandato where consorcio_id = :c"
            " and (vigente_hasta is null or vigente_hasta > vigente_desde)"),
            {"c": consorcio}).scalar_one()  # fmt: skip
    assert vigentes == 0

    # Corre una sola vez por consorcio.
    codigo, _, error = correr(corrida(servidor, entorno, consorcio), capsys)
    assert codigo == 4 and "ya existe" in error


@pytest.mark.db
@pytest.mark.redis
def test_la_guarda_se_niega_fuera_de_staging_sin_escribir(
    servidor, entorno, capsys, monkeypatch, motor_migrate
):
    consorcio = uuid4()
    monkeypatch.setenv("CEM_ORIGEN_PUBLICO", "https://cem.ejemplo.com")
    codigo, _, error = correr(corrida(servidor, entorno, consorcio), capsys)
    assert codigo == 3 and "no es de staging" in error
    monkeypatch.setenv("CEM_ORIGEN_PUBLICO", ORIGEN_STAGING)
    codigo, _, error = correr(corrida(servidor, "cem-postgres", consorcio), capsys)
    assert codigo == 3 and "no apunta a --base" in error
    codigo, _, error = correr(["--consorcio", str(consorcio), "--base", entorno, "--url",
                               servidor, "--host", "cem.ejemplo.com"], capsys)  # fmt: skip
    assert codigo == 3 and "no es el de" in error
    with motor_migrate.connect() as c:
        assert c.execute(text("select count(*) from nucleo.consorcio where id = :c"),
                         {"c": consorcio}).scalar_one() == 0  # fmt: skip


# La guarda antes de la limpieza (revisión de Codex en #286) ----------------------------------


def _mandato_y_sesion(motor_migrate, motor_app) -> dict:
    """Un mandato vigente y una sesión abierta reales, con los ids que recibiría la limpieza."""
    cons, _, _ = consorcio_con_persona(motor_migrate)
    acta = decidir_equipo(motor_app, cons)
    mc = otra_persona(motor_migrate, cons)
    mandato = alta(motor_app, cons, None, acta, persona=mc, tipo="miembro_consejo",
                   origen="soporte")  # fmt: skip
    sesion = nueva_sesion(motor_migrate, mc)
    return {"consorcio": str(cons), "mandato": str(mandato), "solicitante": str(mc),
            "propietario": str(uuid4()), "hoy": date.today().isoformat(), "cuenta": "x",
            "reclamo": "x", "sesion": sesion}  # fmt: skip


def _intactos(motor_migrate, d: dict) -> tuple[bool, bool]:
    with motor_migrate.connect() as c:
        return c.execute(text(
            "select (select vigente_hasta is null from nucleo.mandato where id = :m),"
            " (select revocada_en is null from nucleo.sesion where id = :s)"),
            {"m": d["mandato"], "s": d["sesion"]}).one()  # fmt: skip


def _exportar(base: str, d: dict, capsys) -> tuple[int, str]:
    datos = base64.b64encode(json.dumps({"exportacion": d | {"sesion": None}}).encode()).decode()
    codigo, _, error = correr(["--exportar", "--base", base, "--datos", datos], capsys)
    return codigo, error


@pytest.mark.db
@pytest.mark.redis
def test_exportar_con_otra_base_se_niega_sin_dar_de_baja_ni_revocar(
    entorno, con_url, capsys, motor_migrate, motor_app
):
    d = _mandato_y_sesion(motor_migrate, motor_app)
    assert _intactos(motor_migrate, d) == (True, True)  # control: vigente y abierta
    codigo, error = _exportar("cem-postgres", d, capsys)
    assert codigo == 3 and "no apunta a --base" in error
    assert _intactos(motor_migrate, d) == (True, True)


@pytest.mark.db
@pytest.mark.redis
def test_exportar_fuera_de_staging_se_niega_sin_dar_de_baja_ni_revocar(
    entorno, con_url, capsys, motor_migrate, motor_app, monkeypatch
):
    d = _mandato_y_sesion(motor_migrate, motor_app)
    monkeypatch.setenv("CEM_ORIGEN_PUBLICO", "https://cem.ejemplo.com")
    codigo, error = _exportar(entorno, d, capsys)
    assert codigo == 3 and "no es de staging" in error
    assert _intactos(motor_migrate, d) == (True, True)


@pytest.mark.db
@pytest.mark.redis
def test_la_limpieza_llamada_sola_fuera_de_staging_no_escribe(
    entorno, con_url, motor_migrate, motor_app, monkeypatch
):
    d = _mandato_y_sesion(motor_migrate, motor_app)
    with pytest.raises(ensayo.Negado, match="no apunta a --base"):
        ensayo.cerrar_exportacion(d, "cem-postgres")
    corrida = ensayo.Corrida(motor_app, UUID(d["consorcio"]), ORIGEN_STAGING, "http://x", HOST,
                             None, "cem-postgres")  # fmt: skip
    corrida.mandatos["consejo1"] = UUID(d["mandato"])
    corrida.personas["consejo1"] = UUID(d["solicitante"])
    with pytest.raises(ensayo.Negado, match="no apunta a --base"):
        ensayo.cerrar(corrida, None)
    monkeypatch.setenv("CEM_ORIGEN_PUBLICO", "https://cem.ejemplo.com")
    with pytest.raises(ensayo.Negado, match="no es de staging"):
        ensayo.cerrar(replace(corrida, base=entorno), None)
    assert _intactos(motor_migrate, d) == (True, True)
    # Control: con la guarda en orden, la misma limpieza sí da de baja y revoca.
    monkeypatch.setenv("CEM_ORIGEN_PUBLICO", ORIGEN_STAGING)
    ensayo.cerrar_exportacion(d, entorno)
    assert _intactos(motor_migrate, d) == (False, False)
