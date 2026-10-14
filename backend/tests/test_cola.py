"""Cola, outbox y planificador contra PostgreSQL real (documento 13, §12; plan H1, semana 2)."""

import multiprocessing
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, ProgrammingError

from app.db import transaccion
from app.plataforma import cola, latido, outbox
from app.plataforma.planificador import Planificador, TareaPeriodica, programar
from app.plataforma.trabajos import Trabajador
from tests import tareas_prueba
from tests.conftest import url_rol
from tests.test_api import cliente

SPAWN = multiprocessing.get_context("spawn")
DIARIA = TareaPeriodica("prueba.registrar", timedelta(days=1), desfase=timedelta(hours=6))


@pytest.fixture
def cola_vacia(motor_migrate, datos):
    with motor_migrate.begin() as c:
        c.execute(
            text(
                "create table if not exists plataforma.prueba_efectos"
                " (trabajo_id uuid not null, ambito uuid);"
                "create table if not exists plataforma.prueba_correos (evento_id uuid primary key);"
                "create table if not exists plataforma.prueba_cargos (id serial, evento text);"
                "create table if not exists plataforma.prueba_proveedor (caido boolean not null);"
                "grant select, insert, update on plataforma.prueba_efectos,"
                " plataforma.prueba_correos, plataforma.prueba_cargos,"
                " plataforma.prueba_proveedor to cem_app;"
                "grant usage on sequence plataforma.prueba_cargos_id_seq to cem_app;"
                "truncate plataforma.jobs, plataforma.outbox, plataforma.ejecuciones_programadas,"
                " plataforma.prueba_efectos, plataforma.prueba_correos, plataforma.prueba_cargos,"
                " plataforma.prueba_proveedor;"
                "insert into plataforma.prueba_proveedor values (false);"
            )
        )
    return datos


def uno(motor, sql: str, **p):
    with motor.connect() as c:
        return c.execute(text(sql), p).scalar_one()


def trabajo(motor, id_):
    with motor.connect() as c:
        return c.execute(text("select * from plataforma.jobs where id = :i"), {"i": id_}).one()


def encolar_en(motor, consorcio_id, tipo, **kw):
    with transaccion(motor, persona_id=None, consorcio_id=consorcio_id) as c:
        return cola.encolar(c, tipo, consorcio_id=consorcio_id, **kw)


# Cola --------------------------------------------------------------------------------


@pytest.mark.db
def test_un_rollback_no_deja_trabajo(motor_app, motor_migrate, cola_vacia):
    with pytest.raises(RuntimeError):
        with transaccion(motor_app, persona_id=None, consorcio_id=cola_vacia.consorcio_a) as c:
            cola.encolar(c, "prueba.registrar", consorcio_id=cola_vacia.consorcio_a)
            raise RuntimeError("la transacción de negocio falla")
    assert uno(motor_migrate, "select count(*) from plataforma.jobs") == 0


@pytest.mark.db
def test_n_procesos_consumen_mil_trabajos_exactamente_una_vez(motor_app, motor_migrate, cola_vacia):
    for consorcio in (cola_vacia.consorcio_a, cola_vacia.consorcio_b):
        with transaccion(motor_app, persona_id=None, consorcio_id=consorcio) as c:
            for _ in range(500):
                cola.encolar(c, "prueba.registrar", consorcio_id=consorcio)
    procesos = [
        SPAWN.Process(target=tareas_prueba.vaciar_cola, args=(url_rol("app"),)) for _ in range(4)
    ]
    for p in procesos:
        p.start()
    for p in procesos:
        p.join(timeout=120)
        assert p.exitcode == 0
    with motor_migrate.connect() as c:
        assert c.execute(text("select count(*), count(distinct trabajo_id)"
                              " from plataforma.prueba_efectos")).one() == (1000, 1000)  # fmt: skip
        assert c.execute(
            text("select estado::text, count(*) from plataforma.jobs group by 1")
        ).all() == [("hecho", 1000)]
        # Cada trabajo corrió con el ámbito de su propio consorcio.
        assert c.execute(text(
            "select count(*) from plataforma.prueba_efectos e join plataforma.jobs j"
            " on j.id = e.trabajo_id where e.ambito is distinct from j.consorcio_id"
        )).scalar_one() == 0  # fmt: skip


@pytest.mark.db
def test_kill_9_a_mitad_de_trabajo_reaparece_al_vencer_el_plazo(
    motor_app, motor_migrate, cola_vacia
):
    id_ = encolar_en(motor_app, cola_vacia.consorcio_a, "prueba.dormir", carga={"segundos": 60})
    proceso = SPAWN.Process(target=tareas_prueba.vaciar_cola, args=(url_rol("app"), 2))
    proceso.start()
    limite = time.monotonic() + 30
    while trabajo(motor_migrate, id_).estado != "en_curso":
        assert time.monotonic() < limite, "el proceso no tomó el trabajo"
        time.sleep(0.1)
    os.kill(proceso.pid, signal.SIGKILL)
    proceso.join()
    assert trabajo(motor_migrate, id_).estado == "en_curso"

    otro = Trabajador(motor_app, plazo=timedelta(seconds=30))
    assert otro.procesar_lote() == 0  # el plazo de 2 segundos todavía no venció
    limite = time.monotonic() + 10
    while otro.procesar_lote() == 0:
        assert time.monotonic() < limite, "el trabajo no reapareció"
        time.sleep(0.2)
    fila = trabajo(motor_migrate, id_)
    assert (fila.estado, fila.intentos) == ("hecho", 2)
    assert uno(motor_migrate, "select count(*) from plataforma.prueba_efectos") == 1


@pytest.mark.db
def test_un_trabajo_que_falla_dos_veces_queda_muerto_y_alerta(motor_app, motor_migrate, cola_vacia):
    id_ = encolar_en(motor_app, cola_vacia.consorcio_a, "prueba.fallar")
    api = cliente(url_rol("app"))
    assert api.get("/api/v1/health/cola").json()["estado"] == "ok"

    trabajador = Trabajador(motor_app)
    assert trabajador.procesar_lote() == 1
    fila = trabajo(motor_migrate, id_)
    assert (fila.estado, fila.intentos) == ("pendiente", 1)
    assert "falla de prueba" in fila.error
    assert trabajador.procesar_lote() == 1
    fila = trabajo(motor_migrate, id_)
    assert (fila.estado, fila.intentos) == ("muerto", 2)
    assert trabajador.procesar_lote() == 0

    r = api.get("/api/v1/health/cola")
    assert r.status_code == 200
    assert r.json() | {"segundos_mas_viejo": 0} == {
        "estado": "alerta", "pendientes": 0, "en_curso": 0, "muertos": 1, "segundos_mas_viejo": 0,
    }  # fmt: skip


@pytest.mark.db
def test_un_error_definitivo_no_se_reintenta(motor_app, motor_migrate, cola_vacia):
    id_ = encolar_en(motor_app, cola_vacia.consorcio_a, "prueba.invalido")
    desconocido = encolar_en(motor_app, cola_vacia.consorcio_a, "prueba.no_existe")
    Trabajador(motor_app).procesar_lote()
    for i in (id_, desconocido):
        fila = trabajo(motor_migrate, i)
        assert (fila.estado, fila.intentos) == ("muerto", 1)


@pytest.mark.db
def test_un_trabajador_que_perdio_la_reserva_no_lo_marca_hecho(
    motor_app, motor_migrate, cola_vacia
):
    encolar_en(motor_app, cola_vacia.consorcio_a, "prueba.registrar")
    with motor_app.begin() as c:
        [primero] = cola.tomar(c, "lento", 1, timedelta(0))
    with motor_app.begin() as c:
        [segundo] = cola.tomar(c, "rapido", 1, timedelta(minutes=5))
    assert segundo.id == primero.id and segundo.intentos == 2
    with motor_app.begin() as c:
        assert cola.terminar(c, primero, "lento", None) is False
        assert cola.terminar(c, segundo, "rapido", None) is True


@pytest.mark.db
def test_un_trabajo_disponible_mas_tarde_espera(motor_app, cola_vacia):
    encolar_en(
        motor_app, cola_vacia.consorcio_a, "prueba.registrar",
        disponible_desde=datetime.now(UTC) + timedelta(hours=1),
    )  # fmt: skip
    assert Trabajador(motor_app).procesar_lote() == 0


@pytest.mark.db
def test_la_clave_unica_evita_encolar_dos_veces(motor_app, motor_migrate, cola_vacia):
    a = cola_vacia.consorcio_a
    assert encolar_en(motor_app, a, "prueba.registrar", clave_unica="x") is not None
    assert encolar_en(motor_app, a, "prueba.registrar", clave_unica="x") is None
    assert uno(motor_migrate, "select count(*) from plataforma.jobs") == 1


def test_retroceso_exponencial_con_tope():
    tipo = cola.TipoTrabajo(
        "x.y", lambda c, t: None, 5, timedelta(seconds=30), timedelta(minutes=3)
    )
    assert [tipo.retroceso(i).total_seconds() for i in range(1, 6)] == [30, 60, 120, 180, 180]


# Seguridad por fila --------------------------------------------------------------------


@pytest.mark.db
@pytest.mark.parametrize("tabla", ["plataforma.jobs", "plataforma.outbox"])
def test_cola_y_outbox_aislados_por_consorcio(motor_app, cola_vacia, tabla):
    a, b = cola_vacia.consorcio_a, cola_vacia.consorcio_b
    with transaccion(motor_app, persona_id=None, consorcio_id=b) as c:
        if tabla == "plataforma.jobs":
            cola.encolar(c, "prueba.registrar", consorcio_id=b)
        else:
            outbox.publicar(c, "prueba.publicada", consorcio_id=b)
    with motor_app.connect() as c:
        assert c.execute(text(f"select count(*) from {tabla}")).scalar_one() == 0
    with transaccion(motor_app, persona_id=None, consorcio_id=a) as c:
        assert c.execute(text(f"select count(*) from {tabla}")).scalar_one() == 0
    with transaccion(motor_app, persona_id=None, consorcio_id=b) as c:
        assert c.execute(text(f"select count(*) from {tabla}")).scalar_one() == 1
    with pytest.raises(ProgrammingError, match="row-level security"):
        with transaccion(motor_app, persona_id=None, consorcio_id=a) as c:
            if tabla == "plataforma.jobs":
                cola.encolar(c, "prueba.registrar", consorcio_id=b)
            else:
                outbox.publicar(c, "prueba.publicada", consorcio_id=b)
    with pytest.raises(ProgrammingError, match="row-level security"):
        with transaccion(motor_app, persona_id=None) as c:
            cola.encolar(c, "prueba.registrar", consorcio_id=b)
    with pytest.raises(DBAPIError, match="permission denied"):
        with transaccion(motor_app, persona_id=None, consorcio_id=b) as c:
            c.execute(text(f"delete from {tabla}"))


# Outbox ------------------------------------------------------------------------------


@pytest.mark.db
def test_pa05_aviso_con_el_correo_caido_no_repite_el_cambio(
    motor_app, motor_migrate, cola_vacia, monkeypatch
):
    """PA05: la publicación queda una sola vez y el aviso se reintenta hasta salir."""
    a = cola_vacia.consorcio_a
    monkeypatch.setitem(outbox.RUTAS, "prueba.publicada", ("prueba.correo",))
    with motor_migrate.begin() as c:
        c.execute(text("update plataforma.prueba_proveedor set caido = true"))
    with transaccion(motor_app, persona_id=None, consorcio_id=a) as c:
        c.execute(text("insert into plataforma.prueba_cargos (evento) values ('publicar')"))
        evento = outbox.publicar(c, "prueba.publicada", consorcio_id=a)

    assert outbox.despachar(motor_app) == 1
    assert outbox.despachar(motor_app) == 0
    trabajador = Trabajador(motor_app)
    for intento in (1, 2):
        assert trabajador.procesar_lote() == 1
        fila = uno(motor_migrate, "select row(estado::text, intentos)::text from plataforma.jobs")
        assert fila == f"(pendiente,{intento})"
    with motor_migrate.begin() as c:
        c.execute(text("update plataforma.prueba_proveedor set caido = false"))
    assert trabajador.procesar_lote() == 1

    with motor_migrate.connect() as c:
        assert c.execute(text("select count(*) from plataforma.prueba_cargos")).scalar_one() == 1
        assert c.execute(text("select evento_id from plataforma.prueba_correos")).all() == [
            (evento,)
        ]
        j = c.execute(text("select estado::text, intentos, consorcio_id, clave_unica"
                           " from plataforma.jobs")).one()  # fmt: skip
        assert tuple(j) == ("hecho", 3, a, f"prueba.correo:{evento}")


@pytest.mark.db
def test_un_evento_revertido_no_se_despacha(motor_app, motor_migrate, cola_vacia):
    with pytest.raises(RuntimeError):
        with transaccion(motor_app, persona_id=None, consorcio_id=cola_vacia.consorcio_a) as c:
            outbox.publicar(c, "prueba.publicada", consorcio_id=cola_vacia.consorcio_a)
            raise RuntimeError
    assert outbox.despachar(motor_app) == 0


# Planificador ------------------------------------------------------------------------


def test_ventanas_de_una_tarea_diaria():
    t = DIARIA
    assert t.ventana(datetime(2026, 10, 14, 5, 59, tzinfo=UTC)) == datetime(
        2026, 10, 13, 6, tzinfo=UTC
    )
    assert t.ventana(datetime(2026, 10, 14, 6, 0, tzinfo=UTC)) == datetime(
        2026, 10, 14, 6, tzinfo=UTC
    )


@pytest.mark.db
def test_un_reinicio_no_repite_la_ventana(motor_app, motor_migrate, cola_vacia):
    ahora = datetime(2026, 10, 14, 12, tzinfo=UTC)
    assert programar(motor_app, ahora, [DIARIA]) == 1
    assert programar(motor_app, ahora + timedelta(hours=1), [DIARIA]) == 0
    assert programar(motor_app, ahora + timedelta(days=1), [DIARIA]) == 1
    assert uno(motor_migrate, "select count(*) from plataforma.jobs") == 2
    assert uno(motor_migrate, "select count(*) from plataforma.ejecuciones_programadas"
               " where trabajo_id is not null") == 2  # fmt: skip


@pytest.mark.db
def test_dos_planificadores_a_la_vez_producen_una_sola_ejecucion(
    motor_app, motor_migrate, cola_vacia
):
    ahora = datetime(2026, 10, 14, 12, tzinfo=UTC)
    barrera = threading.Barrier(8)
    resultados: list[int] = []

    def correr():
        barrera.wait()
        resultados.append(programar(motor_app, ahora, [DIARIA]))

    hilos = [threading.Thread(target=correr) for _ in range(8)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    assert sorted(resultados) == [0] * 7 + [1]
    assert uno(motor_migrate, "select count(*) from plataforma.jobs") == 1


@pytest.mark.db
def test_un_solo_planificador_es_lider(motor_app, cola_vacia, monkeypatch, tmp_path):
    monkeypatch.setattr(latido, "ARCHIVO", tmp_path / "latido")
    primero, segundo = (
        Planificador(motor_app, intervalo=0.1),
        Planificador(motor_app, intervalo=0.1),
    )
    fin_primero, fin_segundo = threading.Event(), threading.Event()
    hilos = [threading.Thread(target=primero.ejecutar, args=(fin_primero,))]
    hilos[0].start()
    esperar(lambda: primero.es_lider)
    hilos.append(threading.Thread(target=segundo.ejecutar, args=(fin_segundo,)))
    hilos[1].start()
    time.sleep(0.5)
    assert not segundo.es_lider
    fin_primero.set()
    hilos[0].join()
    esperar(lambda: segundo.es_lider)  # al irse el líder, el otro toma el bloqueo
    fin_segundo.set()
    hilos[1].join()
    assert (tmp_path / "latido").exists()


@pytest.mark.db
def test_los_procesos_arrancan_con_las_tareas_registradas(motor_migrate, cola_vacia, tmp_path):
    """Como en el contenedor: `sh arranque.sh` con CEM_PROCESO, y salida limpia con SIGTERM."""
    entorno = os.environ | {
        "CEM_DATABASE_URL": url_rol("app"),
        "CEM_LATIDO": str(tmp_path / "l"),
        "PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}",
    }
    raiz = Path(__file__).resolve().parent.parent
    planificador = subprocess.Popen(
        ["sh", "arranque.sh"], cwd=raiz, env=entorno | {"CEM_PROCESO": "planificador"}
    )
    esperar(lambda: uno(motor_migrate, "select count(*) from plataforma.jobs") == 1)
    trabajos = subprocess.Popen(
        ["sh", "arranque.sh"], cwd=raiz, env=entorno | {"CEM_PROCESO": "trabajos"}
    )
    esperar(lambda: uno(motor_migrate, "select count(*) from plataforma.jobs"
                        " where estado = 'hecho'") == 1)  # fmt: skip
    for proceso in (planificador, trabajos):
        proceso.send_signal(signal.SIGTERM)
        assert proceso.wait(timeout=20) == 0
    with motor_migrate.connect() as c:
        assert c.execute(text("select tipo from plataforma.jobs")).scalar_one() == (
            "plataforma.purgar_cola"
        )
        assert c.execute(text("select tipo from plataforma.ejecuciones_programadas")).all() == [
            ("plataforma.purgar_cola",)
        ]


def esperar(condicion, segundos: float = 10) -> None:
    limite = time.monotonic() + segundos
    while not condicion():
        assert time.monotonic() < limite
        time.sleep(0.05)


# Latido ------------------------------------------------------------------------------


def test_latido_de_los_procesos_sin_http(monkeypatch, tmp_path):
    monkeypatch.setenv("CEM_PROCESO", "trabajos")
    monkeypatch.setattr(latido, "ARCHIVO", tmp_path / "latido")
    assert not latido.sano()
    latido.latir()
    assert latido.sano()
    viejo = time.time() - latido.EDAD_MAXIMA - 1
    os.utime(tmp_path / "latido", (viejo, viejo))
    assert not latido.sano()
