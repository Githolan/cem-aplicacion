"""El smoke de staging (`pruebas/smoke_staging.py`, P3) corre entero contra un backend real
(uvicorn con la base y el Redis de prueba), y su guarda se niega fuera de staging sin escribir.

En staging corre detrás del nginx del frontend; acá, directo contra el backend: las cookies y el
CSRF son los mismos, nginx solo reenvía."""

import json
import socket
import threading
import time
from uuid import uuid4

import pytest
import uvicorn
from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.config import Config
from app.main import crear_app
from pruebas import smoke_staging
from tests.conftest import REDIS_URL, url_rol

pytestmark = [pytest.mark.db, pytest.mark.redis]

ORIGEN_STAGING = "https://cem-staging.ejemplo.com"
HOST = "cem-staging.ejemplo.com"


@pytest.fixture(scope="module")
def servidor():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        puerto = s.getsockname()[1]
    app = crear_app(
        Config(url_rol("app"), "0.1.0", "sha", "sha", redis_url=REDIS_URL,
               origen_publico=ORIGEN_STAGING)
    )  # fmt: skip
    servidor = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=puerto, log_level="warning")
    )
    hilo = threading.Thread(target=servidor.run, daemon=True)
    hilo.start()
    while not servidor.started:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{puerto}"
    servidor.should_exit = True
    hilo.join(10)


@pytest.fixture
def entorno(monkeypatch, redis_limpio):
    monkeypatch.setenv("CEM_DATABASE_URL", url_rol("app"))
    monkeypatch.setenv("CEM_ORIGEN_PUBLICO", ORIGEN_STAGING)
    return make_url(url_rol("app")).host


def correr(servidor, base, consorcio, capsys) -> tuple[int, dict | None, str]:
    codigo = smoke_staging.main(["--consorcio", str(consorcio), "--base", base,
                                 "--url", servidor, "--host", HOST])  # fmt: skip
    salida = capsys.readouterr()
    inicio = salida.out.find("{")
    return codigo, (json.loads(salida.out[inicio:]) if inicio >= 0 else None), salida.err


def contar(motor_migrate, consorcio) -> tuple[int, int]:
    with motor_migrate.connect() as c:
        return c.execute(
            text("select (select count(*) from nucleo.consorcio where id = :c),"
                 " (select count(*) from nucleo.unidad where consorcio_id = :c)"),
            {"c": consorcio},
        ).one()  # fmt: skip


def test_el_smoke_recorre_todo_y_deja_las_sesiones_revocadas(
    servidor, entorno, capsys, motor_migrate
):
    consorcio = uuid4()
    codigo, resumen, error = correr(servidor, entorno, consorcio, capsys)
    assert codigo == 0, error
    assert resumen["sesiones_abiertas_al_cerrar"] == 0
    assert resumen["navegadores_que_siguen_entrando"] == []
    assert len(resumen["controles"]) == 11
    assert set(resumen["personas"]) == {
        "propietaria", "consejo1", "consejo2", "administracion", "externo",
    }  # fmt: skip
    with motor_migrate.connect() as c:
        nombre = c.execute(
            text("select nombre from nucleo.consorcio where id = :c"), {"c": consorcio}
        ).scalar_one()
        vigentes = c.execute(
            text("select count(*) from nucleo.mandato where consorcio_id = :c"
                 " and (vigente_hasta is null or vigente_hasta > vigente_desde)"),
            {"c": consorcio},
        ).scalar_one()  # fmt: skip
        correos = c.execute(
            text("select distinct split_part(correo, '@', 2) from nucleo.persona"
                 " where id = any(:p)"), {"p": list(resumen["personas"].values())},
        ).scalars().all()  # fmt: skip
    assert nombre == smoke_staging.NOMBRE_CONSORCIO
    assert vigentes == 0  # todos los mandatos de la corrida terminaron (rango vacío)
    assert correos == [smoke_staging.DOMINIO]

    # El consorcio es permanente: una segunda corrida lo reusa.
    codigo, resumen, error = correr(servidor, entorno, consorcio, capsys)
    assert codigo == 0, error
    assert contar(motor_migrate, consorcio) == (1, 8)


def test_ante_una_excepcion_inesperada_igual_cierra(servidor, entorno, monkeypatch, motor_migrate):
    """Revisión independiente: un error que no es un `Fallo` (un 403 de un proxy en HTML, un
    tiempo agotado) no puede dejar sesiones abiertas ni mandatos vigentes."""

    def rota(corrida):
        raise RuntimeError("se cortó la red")

    monkeypatch.setattr(smoke_staging, "cadena", rota)
    consorcio = uuid4()
    with pytest.raises(RuntimeError, match="se cortó la red"):
        smoke_staging.main(["--consorcio", str(consorcio), "--base", entorno,
                            "--url", servidor, "--host", HOST])  # fmt: skip
    with motor_migrate.connect() as c:
        mandatos, sin_terminar, sesiones, abiertas = c.execute(
            text("select (select count(*) from nucleo.mandato where consorcio_id = :c),"
                 " (select count(*) from nucleo.mandato where consorcio_id = :c"
                 " and finalizado_en is null),"
                 " (select count(*) from nucleo.sesion s where exists (select 1"
                 " from nucleo.vinculo v where v.persona_id = s.persona_id"
                 " and v.consorcio_id = :c)),"
                 " (select count(*) from nucleo.sesion s where s.revocada_en is null"
                 " and s.vence_en > now() and exists (select 1 from nucleo.vinculo v"
                 " where v.persona_id = s.persona_id and v.consorcio_id = :c))"),
            {"c": consorcio},
        ).one()  # fmt: skip
    # Control: el corte llegó con mandatos y sesiones creados (si no, no probaría nada).
    assert mandatos >= 4 and sesiones >= 4
    assert (sin_terminar, abiertas) == (0, 0)


def test_la_guarda_se_niega_fuera_de_staging_sin_escribir(
    servidor, entorno, capsys, monkeypatch, motor_migrate
):
    consorcio = uuid4()
    monkeypatch.setenv("CEM_ORIGEN_PUBLICO", "https://cem.ejemplo.com")
    codigo, _, error = correr(servidor, entorno, consorcio, capsys)
    assert (codigo, contar(motor_migrate, consorcio)) == (3, (0, 0))
    assert "no es de staging" in error

    monkeypatch.setenv("CEM_ORIGEN_PUBLICO", ORIGEN_STAGING)
    codigo, _, error = correr(servidor, "cem-postgres", consorcio, capsys)
    assert (codigo, contar(motor_migrate, consorcio)) == (3, (0, 0))
    assert "no a --base" in error


def test_la_guarda_se_niega_con_un_consorcio_que_no_es_el_de_la_prueba(
    servidor, entorno, capsys, motor_migrate
):
    with motor_migrate.begin() as c:
        ajeno = c.execute(
            text("insert into nucleo.consorcio (nombre) values ('Edificio real') returning id")
        ).scalar_one()
    codigo, _, error = correr(servidor, entorno, ajeno, capsys)
    assert (codigo, contar(motor_migrate, ajeno)) == (3, (1, 0))
    assert "no es el de la prueba" in error
