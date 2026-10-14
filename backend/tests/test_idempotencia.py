"""`Idempotency-Key` contra PostgreSQL real (documento 13, §7.4 y §11.1; RNF05).

Todavía no hay rutas de negocio que creen dinero, cargos o expedientes. Las pruebas registran
una ruta propia, `POST /api/v1/consorcios/{id}/prueba-idempotencia`, que no existe en la app de
producción ni en su OpenAPI: inserta un cargo de prueba en una tabla con RLS forzada (efecto
real en la base) dentro de `responder_una_vez()`, igual que lo hará una ruta de negocio.
"""

import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from cem_contracts.errores import CodigoError
from fastapi import APIRouter, Request, Response
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import text

from app.config import Config
from app.contexto import ContextoDep, Identidad, MotorDep, obtener_identidad
from app.errores import ErrorApi
from app.main import crear_app
from app.plataforma.idempotencia import (
    ENCABEZADO,
    ENCABEZADO_REPETIDA,
    ESPERA_EN_CURSO_MS,
    VIGENCIA,
    IdempotenciaDep,
    responder_una_vez,
)
from scripts.exportar_openapi import openapi_texto
from tests.conftest import url_rol
from tests.test_idempotencia_tabla import vencer

pytestmark = pytest.mark.db

TABLA = "plataforma.prueba_cargos_idempotentes"
PLAZO_HILOS_S = 30
"""Tope de cualquier espera entre hilos: si algo se cuelga, la prueba falla en vez de colgarse."""

router = APIRouter()


class CargoPrueba(BaseModel):
    concepto: str
    importe: Decimal


class CargoCreado(CargoPrueba):
    id: UUID


# La misma ruta con otro nombre: una clave vale por persona, consorcio, método y ruta.
@router.post("/api/v1/consorcios/{consorcio_id}/prueba-idempotencia-bis", status_code=201)
@router.post("/api/v1/consorcios/{consorcio_id}/prueba-idempotencia", status_code=201)
def crear_cargo_de_prueba(
    ctx: ContextoDep, motor: MotorDep, pedido: IdempotenciaDep, datos: CargoPrueba, request: Request
) -> Response:
    def efecto(conn):
        id_ = conn.execute(
            text(f"insert into {TABLA} (consorcio_id, concepto, importe) values (:c, :k, :i)"
                 " returning id"),
            {"c": ctx.consorcio_id, "k": datos.concepto, "i": datos.importe},
        ).scalar_one()  # fmt: skip
        request.app.state.durante_efecto()
        if request.app.state.fallar:
            raise ErrorApi(422, CodigoError.PETICION_INVALIDA, "Falla pedida por la prueba.")
        return 201, CargoCreado(id=id_, concepto=datos.concepto, importe=datos.importe)

    return responder_una_vez(motor, ctx, pedido, efecto)


def ruta(consorcio_id: UUID) -> str:
    return f"/api/v1/consorcios/{consorcio_id}/prueba-idempotencia"


def cliente(persona: UUID) -> TestClient:
    app = crear_app(Config(url_rol("app"), "0.1.0", "sha", "sha", redis_url="memory://"))
    app.include_router(router)
    app.dependency_overrides[obtener_identidad] = lambda: Identidad(persona)
    app.state.durante_efecto = lambda: None
    app.state.fallar = False
    return TestClient(app)


CUERPO = {"concepto": "Expensas de octubre", "importe": "15234.50"}


def pedir(c: TestClient, consorcio: UUID, clave: str | None, cuerpo=CUERPO, consulta: str = ""):
    encabezados = {} if clave is None else {ENCABEZADO: clave}
    return c.post(ruta(consorcio) + consulta, json=cuerpo, headers=encabezados)


@pytest.fixture
def limpia(motor_migrate, datos):
    with motor_migrate.begin() as c:
        c.execute(
            text(
                f"drop table if exists {TABLA};"
                f"create table {TABLA} (id uuid primary key default plataforma.uuid_v7(),"
                " consorcio_id uuid not null references nucleo.consorcio (id),"
                " concepto text not null, importe numeric(18,2) not null);"
                f"alter table {TABLA} enable row level security;"
                f"alter table {TABLA} force row level security;"
                f"create policy por_consorcio on {TABLA}"
                " using (consorcio_id = plataforma.consorcio_actual())"
                " with check (consorcio_id = plataforma.consorcio_actual());"
                f"grant select, insert on {TABLA} to cem_app;"
                "truncate plataforma.idempotencia;"
            )
        )
    return datos


def contar(motor_migrate, tabla: str = TABLA, **filtro) -> int:
    donde = " and ".join(f"{k} = :{k}" for k in filtro) or "true"
    with motor_migrate.connect() as c:
        return c.execute(text(f"select count(*) from {tabla} where {donde}"), filtro).scalar_one()


def esperar(condicion: Callable[[], bool]) -> None:
    limite = time.monotonic() + PLAZO_HILOS_S
    while not condicion():
        assert time.monotonic() < limite, "la condición no se cumplió a tiempo"
        time.sleep(0.02)


def bloqueos_en_espera(motor_migrate) -> int:
    """Pedidos parados en el bloqueo consultivo de una clave (otra conexión real lo tiene)."""
    with motor_migrate.connect() as c:
        return c.execute(
            text(
                "select count(*) from pg_locks l join pg_database d on d.oid = l.database"
                " where l.locktype = 'advisory' and not l.granted"
                " and d.datname = current_database()"
            )
        ).scalar_one()


def retener_efecto(c: TestClient) -> tuple[threading.Event, threading.Event]:
    """El efecto del próximo pedido avisa que entró y espera a que la prueba lo suelte."""
    entro, soltar = threading.Event(), threading.Event()

    def durante() -> None:
        entro.set()
        assert soltar.wait(PLAZO_HILOS_S)

    c.app.state.durante_efecto = durante
    return entro, soltar


# Repetición y reutilización -----------------------------------------------------------------


def test_repetir_el_mismo_pedido_devuelve_la_respuesta_guardada_sin_repetir_el_efecto(
    limpia, motor_migrate
):
    c, clave = cliente(limpia.persona_a), str(uuid4())
    primera = pedir(c, limpia.consorcio_a, clave)
    assert primera.status_code == 201 and ENCABEZADO_REPETIDA not in primera.headers
    for _ in range(2):
        otra = pedir(c, limpia.consorcio_a, clave)
        assert otra.status_code == 201 and otra.headers[ENCABEZADO_REPETIDA] == "true"
        assert otra.content == primera.content
    assert contar(motor_migrate) == 1
    with motor_migrate.connect() as conn:
        fila = conn.execute(
            text(
                "select metodo, ruta, vence_en - creado_en as vigencia from plataforma.idempotencia"
            )
        ).one()
    assert fila.metodo == "POST" and fila.ruta == ruta(limpia.consorcio_a)
    assert fila.vigencia == VIGENCIA


def test_misma_clave_con_otro_pedido_se_rechaza(limpia, motor_migrate):
    c, clave = cliente(limpia.persona_a), str(uuid4())
    assert pedir(c, limpia.consorcio_a, clave).status_code == 201
    for distinto in (
        pedir(c, limpia.consorcio_a, clave, {**CUERPO, "importe": "15234.51"}),
        pedir(c, limpia.consorcio_a, clave, consulta="?otra=1"),
    ):
        assert distinto.status_code == 422
        assert distinto.json()["codigo"] == CodigoError.IDEMPOTENCIA_REUTILIZADA
    assert contar(motor_migrate) == 1
    # En otra ruta la misma clave es otra clave: ejecuta.
    bis = c.post(ruta(limpia.consorcio_a) + "-bis", json=CUERPO, headers={ENCABEZADO: clave})
    assert bis.status_code == 201 and ENCABEZADO_REPETIDA not in bis.headers
    assert bis.json()["importe"] == CUERPO["importe"]
    assert contar(motor_migrate) == 2


@pytest.mark.parametrize("clave", [None, "", "con espacio", "x" * 256])
def test_sin_clave_o_con_una_clave_invalida_no_hay_efecto(limpia, motor_migrate, clave):
    r = pedir(cliente(limpia.persona_a), limpia.consorcio_a, clave)
    assert r.status_code == 422 and r.json()["codigo"] == CodigoError.PETICION_INVALIDA
    assert f"header.{ENCABEZADO}" in r.json()["detalle"]["campos"]
    assert contar(motor_migrate) == 0


def test_un_efecto_que_falla_no_guarda_nada_y_el_reintento_ejecuta(limpia, motor_migrate):
    c, clave = cliente(limpia.persona_a), str(uuid4())
    c.app.state.fallar = True
    assert pedir(c, limpia.consorcio_a, clave).status_code == 422
    # El cargo ya se había insertado cuando falló: la transacción deshizo los dos.
    assert contar(motor_migrate) == 0
    assert contar(motor_migrate, "plataforma.idempotencia") == 0
    c.app.state.fallar = False
    r = pedir(c, limpia.consorcio_a, clave)
    assert r.status_code == 201 and ENCABEZADO_REPETIDA not in r.headers
    assert contar(motor_migrate) == 1


def test_sin_autenticacion_no_se_llega_a_la_clave(limpia, motor_migrate):
    app = crear_app(Config(url_rol("app"), "0.1.0", "sha", "sha", redis_url="memory://"))
    app.include_router(router)
    r = pedir(TestClient(app), limpia.consorcio_a, str(uuid4()))
    assert r.status_code == 401 and contar(motor_migrate, "plataforma.idempotencia") == 0


# Concurrencia: cada pedido usa su propia conexión real a PostgreSQL --------------------------


def test_un_pedido_igual_concurrente_espera_y_recibe_la_respuesta_del_primero(
    limpia, motor_migrate
):
    c, clave = cliente(limpia.persona_a), str(uuid4())
    entro, soltar = retener_efecto(c)
    with ThreadPoolExecutor(2) as hilos:
        primero = hilos.submit(pedir, c, limpia.consorcio_a, clave)
        assert entro.wait(PLAZO_HILOS_S)
        segundo = hilos.submit(pedir, c, limpia.consorcio_a, clave)
        # El segundo está parado en el bloqueo de la clave, que tiene la conexión del primero.
        esperar(lambda: bloqueos_en_espera(motor_migrate) == 1)
        assert not segundo.done()
        soltar.set()
        r1, r2 = primero.result(PLAZO_HILOS_S), segundo.result(PLAZO_HILOS_S)
    assert r1.status_code == r2.status_code == 201
    assert ENCABEZADO_REPETIDA not in r1.headers and r2.headers[ENCABEZADO_REPETIDA] == "true"
    assert r1.content == r2.content
    assert contar(motor_migrate) == 1


def test_si_el_primero_tarda_mas_que_la_espera_el_segundo_recibe_409(limpia, motor_migrate):
    c, clave = cliente(limpia.persona_a), str(uuid4())
    entro, soltar = retener_efecto(c)
    with ThreadPoolExecutor(1) as hilos:
        primero = hilos.submit(pedir, c, limpia.consorcio_a, clave)
        assert entro.wait(PLAZO_HILOS_S)
        inicio = time.monotonic()
        r2 = pedir(c, limpia.consorcio_a, clave)
        espera_ms = (time.monotonic() - inicio) * 1000
        assert r2.status_code == 409
        assert r2.json()["codigo"] == CodigoError.IDEMPOTENCIA_EN_CURSO
        assert espera_ms >= ESPERA_EN_CURSO_MS
        soltar.set()
        r1 = primero.result(PLAZO_HILOS_S)
    assert r1.status_code == 201
    r3 = pedir(c, limpia.consorcio_a, clave)
    assert r3.headers[ENCABEZADO_REPETIDA] == "true" and r3.content == r1.content
    assert contar(motor_migrate) == 1


def test_muchos_pedidos_iguales_a_la_vez_producen_un_solo_efecto(limpia, motor_migrate):
    c, clave, n = cliente(limpia.persona_a), str(uuid4()), 8
    largada = threading.Barrier(n)

    def a_la_vez():
        largada.wait(PLAZO_HILOS_S)
        return pedir(c, limpia.consorcio_a, clave)

    with ThreadPoolExecutor(n) as hilos:
        respuestas = [f.result(PLAZO_HILOS_S) for f in [hilos.submit(a_la_vez) for _ in range(n)]]
    assert {r.status_code for r in respuestas} == {201}
    assert len({r.content for r in respuestas}) == 1
    assert sum(ENCABEZADO_REPETIDA not in r.headers for r in respuestas) == 1
    assert contar(motor_migrate) == 1


# Aislamiento ------------------------------------------------------------------------------


def test_la_clave_es_de_la_persona_en_cada_consorcio(limpia, motor_migrate):
    clave = str(uuid4())
    carla, ana = cliente(limpia.persona_ab), cliente(limpia.persona_a)
    en_a = pedir(carla, limpia.consorcio_a, clave)
    en_b = pedir(carla, limpia.consorcio_b, clave)
    de_ana = pedir(ana, limpia.consorcio_a, clave)
    for r in (en_a, en_b, de_ana):
        assert r.status_code == 201 and ENCABEZADO_REPETIDA not in r.headers
    assert len({en_a.json()["id"], en_b.json()["id"], de_ana.json()["id"]}) == 3
    assert contar(motor_migrate) == 3


# Vencimiento ------------------------------------------------------------------------------


def test_una_clave_vencida_cuenta_como_nueva(limpia, motor_migrate):
    c, clave = cliente(limpia.persona_a), str(uuid4())
    assert pedir(c, limpia.consorcio_a, clave).status_code == 201
    vencer(motor_migrate, clave)
    otro = pedir(c, limpia.consorcio_a, clave, {**CUERPO, "concepto": "Otro"})
    assert otro.status_code == 201 and ENCABEZADO_REPETIDA not in otro.headers
    assert contar(motor_migrate) == 2
    assert contar(motor_migrate, "plataforma.idempotencia", clave=clave) == 1


# Contrato ---------------------------------------------------------------------------------


def test_la_ruta_de_prueba_no_se_publica_y_el_encabezado_figura_como_obligatorio():
    assert "prueba-idempotencia" not in openapi_texto()
    app = crear_app(Config("", "0.1.0", "", "", redis_url="memory://"))
    app.include_router(router)
    operacion = app.openapi()["paths"]["/api/v1/consorcios/{consorcio_id}/prueba-idempotencia"]
    [encabezado] = [p for p in operacion["post"]["parameters"] if p["in"] == "header"]
    assert encabezado["name"] == ENCABEZADO and encabezado["required"] is True
