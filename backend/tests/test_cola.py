"""Cola, outbox y planificador contra PostgreSQL real (documento 13, §12; plan H1, semana 2)."""

import json
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
from app.plataforma.trabajos import LOTE_POR_DEFECTO, Trabajador, lote_configurado
from tests import tareas_prueba
from tests.conftest import url_rol
from tests.test_api import cliente

SPAWN = multiprocessing.get_context("spawn")
RAIZ = Path(__file__).resolve().parent.parent
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
    trabajador = Trabajador(motor_app)
    while trabajador.procesar_lote():  # de a uno: el lote por defecto es 1
        pass
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


# Resultado, redacción del error y lote ---------------------------------------------------


@pytest.mark.db
def test_un_resultado_que_no_es_json_deja_el_trabajo_muerto_sin_repetir_el_efecto(
    motor_app, motor_migrate, cola_vacia, tmp_path
):
    """Proceso real: muere en el primer intento, el efecto externo ocurre una sola vez, lo que
    escribió en la base se revierte y el proceso sigue con el trabajo siguiente."""
    a = cola_vacia.consorcio_a
    efectos = tmp_path / "efectos"
    malo = encolar_en(motor_app, a, "prueba.no_json", carga={"archivo": str(efectos)})
    bueno = encolar_en(motor_app, a, "prueba.registrar")
    proceso = SPAWN.Process(target=tareas_prueba.vaciar_cola, args=(url_rol("app"),))
    proceso.start()
    proceso.join(timeout=60)
    assert proceso.exitcode == 0
    fila = trabajo(motor_migrate, malo)
    assert (fila.estado, fila.intentos) == ("muerto", 1)
    assert fila.error.startswith("ErrorDefinitivo: el resultado del trabajo no es JSON:")
    assert "set is not JSON serializable" in fila.error
    assert efectos.read_text().splitlines() == [str(malo)]
    assert trabajo(motor_migrate, bueno).estado == "hecho"
    assert uno(motor_migrate, "select count(*) from plataforma.prueba_efectos"
               " where trabajo_id = :t", t=malo) == 0  # fmt: skip


@pytest.mark.db
@pytest.mark.parametrize("resultado", [{"x": float("nan")}, {"x": object()}, {"x": {1, 2}}])
def test_terminar_rechaza_lo_que_jsonb_no_guarda(motor_app, cola_vacia, resultado):
    encolar_en(motor_app, cola_vacia.consorcio_a, "prueba.registrar")
    with motor_app.begin() as c:
        [t] = cola.tomar(c, "yo", 1, timedelta(minutes=1))
        with pytest.raises(cola.ErrorDefinitivo, match="no es JSON"):
            cola.terminar(c, t, "yo", resultado)


MENSAJE_CON_DATOS = (
    "no se pudo avisar a ana.perez@ejemplo.com por redis://default:S3cr3t0@cem-redis:6379/0"
    " con token=tk_vivo_123 al +54 9 11 4567-8901 por $ 12.345,67"
)
SECRETOS = ("ana.perez", "S3cr3t0", "tk_vivo_123", "4567-8901", "12.345,67")


@pytest.mark.db
def test_el_error_de_un_trabajo_se_guarda_y_se_registra_redactado(
    motor_app, motor_migrate, cola_vacia
):
    """Proceso real con los registros de `cem-trabajos` (§16.1)."""
    a = cola_vacia.consorcio_a
    id_ = encolar_en(motor_app, a, "prueba.filtrar", carga={"mensaje": MENSAJE_CON_DATOS})
    orden = (
        "from tests import tareas_prueba;"
        f" tareas_prueba.vaciar_cola_con_registros({url_rol('app')!r})"
    )
    proceso = subprocess.run(
        [sys.executable, "-c", orden], cwd=RAIZ, env=os.environ | {"CEM_PROCESO": "trabajos"},
        capture_output=True, text=True, timeout=60,
    )  # fmt: skip
    assert proceso.returncode == 0, proceso.stderr
    fila = trabajo(motor_migrate, id_)
    assert fila.estado == "muerto"
    # Igualdad exacta: tapa los datos y deja todo lo demás (control de que no se pasa de largo).
    assert fila.error == (
        "RuntimeError: no se pudo avisar a [correo] por redis://default:[redactado]@cem-redis"
        ":6379/0 con token=[redactado] al [telefono] por [importe]"
    )
    lineas = [json.loads(linea) for linea in proceso.stdout.splitlines()]
    [fallido] = [x for x in lineas if x["mensaje"].startswith("trabajo fallido (muerto)")]
    assert (fallido["nivel"], fallido["proceso"], fallido["modulo"]) == (
        "ERROR", "trabajos", "cem.trabajos",
    )  # fmt: skip
    assert (fallido["id_correlacion"], fallido["consorcio_id"]) == (str(id_), str(a))
    assert fallido["duracion_ms"] >= 0
    assert "RuntimeError: no se pudo avisar a [correo]" in fallido["excepcion"]
    for secreto in SECRETOS:
        assert secreto not in proceso.stdout
        assert secreto not in fila.error


@pytest.mark.db
def test_con_el_lote_por_defecto_un_trabajo_lento_no_hace_morir_al_siguiente(
    motor_app, motor_migrate, cola_vacia
):
    """Con un lote de 5, el segundo quedaba reservado detrás del lento, vencía su plazo y otro
    proceso lo daba por muerto (era su último intento) sin haberlo ejecutado nunca."""
    a = cola_vacia.consorcio_a
    lento = encolar_en(motor_app, a, "prueba.dormir", carga={"segundos": 3})
    siguiente = encolar_en(motor_app, a, "prueba.ultimo_intento")
    primero = Trabajador(motor_app, plazo=timedelta(seconds=1))
    hilo = threading.Thread(target=primero.procesar_lote)
    hilo.start()
    esperar(lambda: trabajo(motor_migrate, lento).estado == "en_curso")
    time.sleep(1.5)  # vence el plazo de lo que tomó el primero
    otro = Trabajador(motor_app, lote=10)
    while otro.procesar_lote():
        pass
    hilo.join()
    fila = trabajo(motor_migrate, siguiente)
    assert (fila.estado, fila.intentos) == ("hecho", 1)


def test_el_lote_se_configura_con_cem_trabajos_lote(monkeypatch):
    monkeypatch.delenv("CEM_TRABAJOS_LOTE", raising=False)
    assert lote_configurado() == LOTE_POR_DEFECTO == 1
    monkeypatch.setenv("CEM_TRABAJOS_LOTE", "4")
    assert lote_configurado() == 4
    for invalido in ("0", "-1", "cinco", ""):
        monkeypatch.setenv("CEM_TRABAJOS_LOTE", invalido)
        with pytest.raises(ValueError, match="CEM_TRABAJOS_LOTE"):
            lote_configurado()


def test_cem_trabajos_no_arranca_con_un_lote_invalido():
    entorno = os.environ | {
        "CEM_PROCESO": "trabajos",
        "CEM_TRABAJOS_LOTE": "cero",
        "PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}",
    }
    proceso = subprocess.run(
        ["sh", "arranque.sh"], cwd=RAIZ, env=entorno, capture_output=True, text=True, timeout=30
    )
    assert proceso.returncode != 0
    assert "CEM_TRABAJOS_LOTE debe ser un entero" in proceso.stderr


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
    salida = {"stdout": subprocess.PIPE, "text": True}
    planificador = subprocess.Popen(
        ["sh", "arranque.sh"], cwd=RAIZ, env=entorno | {"CEM_PROCESO": "planificador"}, **salida
    )
    esperar(lambda: uno(motor_migrate, "select count(*) from plataforma.jobs") == 1)
    trabajos = subprocess.Popen(
        ["sh", "arranque.sh"], cwd=RAIZ, env=entorno | {"CEM_PROCESO": "trabajos"}, **salida
    )
    esperar(lambda: uno(motor_migrate, "select count(*) from plataforma.jobs"
                        " where estado = 'hecho'") == 1)  # fmt: skip
    registros: dict[str, list[dict]] = {}
    for nombre, proceso in (("planificador", planificador), ("trabajos", trabajos)):
        proceso.send_signal(signal.SIGTERM)
        texto, _ = proceso.communicate(timeout=20)
        assert proceso.returncode == 0
        # Registros JSON (§16.1): cada línea es un objeto y dice qué proceso la escribió.
        registros[nombre] = [json.loads(linea) for linea in texto.splitlines()]
        assert {x["proceso"] for x in registros[nombre]} == {nombre}
    assert "cem-planificador es el líder" in [x["mensaje"] for x in registros["planificador"]]
    [hecho] = [x for x in registros["trabajos"] if x["mensaje"].startswith("trabajo hecho")]
    assert hecho["tipo_trabajo"] == "plataforma.purgar_cola" and hecho["consorcio_id"] is None
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
