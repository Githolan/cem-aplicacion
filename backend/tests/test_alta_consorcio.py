"""Alta de un consorcio por el equipo de la plataforma (`scripts/alta_consorcio.py`).

Contra PostgreSQL 18 real y con `cem_app`, como corre dentro de `cem-backend`: la RLS está
activa y el script tiene que poner el consorcio nuevo en el ámbito para poder crearlo. Cada
prueba arma su propio archivo con un `id` nuevo, así nada depende de las otras.
"""

import io
import json
import re
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from app.db import transaccion
from scripts import alta_consorcio
from scripts import invitar as script_invitar
from tests.conftest import url_rol
from tests.test_identidad import ORIGEN, app, navegador  # noqa: F401  (`app` es un fixture)
from tests.test_invitaciones import ACEPTAR, correo_nuevo, token_invitacion

pytestmark = pytest.mark.db

UNIDADES = ["PB", "1° A", "1° B"]


def contenido(consorcio_id: UUID | None, **cambios) -> dict:
    consorcio = {
        "nombre": "Edificio Prueba de Alta",
        "cuit": "30712345678",
        "zona_horaria": "America/Argentina/Buenos_Aires",
    }
    if consorcio_id is not None:
        consorcio["id"] = str(consorcio_id)
    unidades = cambios.pop("unidades", UNIDADES)
    consorcio.update(cambios)
    return {"consorcio": consorcio, "unidades": [{"designacion": d} for d in unidades]}


@pytest.fixture
def correr(tmp_path, monkeypatch, capsys):
    """Corre el script con un archivo y devuelve (código, salida, errores)."""
    monkeypatch.setenv("CEM_DATABASE_URL", url_rol("app"))

    def _correr(datos: dict, *extra: str) -> tuple[int, str, str]:
        ruta = tmp_path / f"{uuid4().hex}.json"
        ruta.write_text(json.dumps(datos, ensure_ascii=False), encoding="utf-8")
        codigo = alta_consorcio.main(["--archivo", str(ruta), *extra])
        salida = capsys.readouterr()
        return codigo, salida.out, salida.err

    return _correr


def totales(motor_migrate) -> tuple[int, int, int]:
    """Filas de consorcios, unidades y eventos de auditoría en toda la base."""
    with motor_migrate.connect() as conn:
        return conn.execute(
            text(
                "select (select count(*) from nucleo.consorcio),"
                " (select count(*) from nucleo.unidad),"
                " (select count(*) from auditoria.eventos)"
            )
        ).one()


def unidades_impresas(salida: str) -> dict[str, UUID]:
    return {d: UUID(i) for i, d in re.findall(r"^  unidad ([0-9a-f-]{36})  (.+)$", salida, re.M)}


def test_crea_el_consorcio_y_sus_unidades_e_imprime_los_ids(correr, motor_migrate):
    cid = uuid4()
    codigo, salida, _ = correr(contenido(cid))
    assert codigo == 0
    assert f"consorcio {cid} 'Edificio Prueba de Alta': creado" in salida
    impresas = unidades_impresas(salida)
    assert list(impresas) == UNIDADES
    with motor_migrate.connect() as conn:
        c = conn.execute(
            text("select nombre, cuit, zona_horaria from nucleo.consorcio where id = :c"),
            {"c": cid},
        ).one()
        en_base = dict(
            conn.execute(
                text("select designacion, id from nucleo.unidad where consorcio_id = :c"),
                {"c": cid},
            ).all()
        )
    assert tuple(c) == ("Edificio Prueba de Alta", "30712345678", "America/Argentina/Buenos_Aires")
    assert en_base == impresas


def test_la_auditoria_queda_encadenada_con_origen_soporte(correr, motor_migrate):
    cid = uuid4()
    assert correr(contenido(cid))[0] == 0
    with motor_migrate.connect() as conn:
        eventos = conn.execute(
            text(
                "select origen, actor_persona_id, accion, entidad, entidad_id, despues,"
                " hash_previo, hash from auditoria.eventos where consorcio_id = :c"
                " order by secuencia"
            ),
            {"c": cid},
        ).all()
    assert [e.accion for e in eventos] == ["consorcios.consorcio.crear"] + [
        "consorcios.unidad.crear"
    ] * len(UNIDADES)
    assert {(e.origen, e.actor_persona_id) for e in eventos} == {("soporte", None)}
    assert (eventos[0].entidad, eventos[0].entidad_id) == ("consorcio", cid)
    assert [e.despues["designacion"] for e in eventos[1:]] == UNIDADES
    # Cadena: el primero del consorcio arranca vacío y cada uno guarda el hash del anterior.
    assert eventos[0].hash_previo == ""
    for anterior, siguiente in zip(eventos, eventos[1:], strict=False):
        assert siguiente.hash_previo == anterior.hash


def test_correrlo_dos_veces_no_duplica_nada(correr, motor_migrate):
    cid = uuid4()
    primera = correr(contenido(cid))
    antes = totales(motor_migrate)
    segunda = correr(contenido(cid))
    assert segunda[0] == 0
    assert "ya existía igual; no se escribió nada" in segunda[1]
    assert unidades_impresas(segunda[1]) == unidades_impresas(primera[1])
    assert totales(motor_migrate) == antes
    # El orden de las unidades en el archivo no es una diferencia.
    assert correr(contenido(cid, unidades=list(reversed(UNIDADES))))[0] == 0
    assert totales(motor_migrate) == antes


@pytest.mark.parametrize(
    ("cambios", "esperado"),
    [
        ({"nombre": "Otro nombre"}, "consorcio.nombre: en la base 'Edificio Prueba de Alta'"),
        ({"cuit": None}, "consorcio.cuit: en la base '30712345678', en el archivo None"),
        ({"zona_horaria": "America/Argentina/Cordoba"}, "consorcio.zona_horaria"),
        ({"unidades": [*UNIDADES, "2° A"]}, "unidad '2° A': está en el archivo y no en la base"),
        ({"unidades": UNIDADES[:2]}, "unidad '1° B': está en la base y no en el archivo"),
    ],
)
def test_un_archivo_distinto_falla_y_no_escribe_nada(correr, motor_migrate, cambios, esperado):
    cid = uuid4()
    assert correr(contenido(cid))[0] == 0
    antes = totales(motor_migrate)
    for extra in ((), ("--simular",)):
        codigo, salida, errores = correr(contenido(cid, **cambios), *extra)
        assert codigo == 1
        assert "ya existe y no coincide con el archivo; no se escribió nada" in errores
        assert esperado in errores
        assert salida == ""
    assert totales(motor_migrate) == antes


def test_un_consorcio_dado_de_baja_no_se_reactiva(correr, motor_migrate):
    cid = uuid4()
    assert correr(contenido(cid))[0] == 0
    with motor_migrate.begin() as conn:
        conn.execute(
            text("update nucleo.consorcio set eliminado_en = now() where id = :c"), {"c": cid}
        )
    codigo, _, errores = correr(contenido(cid))
    assert codigo == 1
    assert "consorcio dado de baja" in errores


def test_simular_muestra_lo_que_crearia_sin_escribir(correr, motor_migrate):
    cid = uuid4()
    antes = totales(motor_migrate)
    codigo, salida, _ = correr(contenido(cid), "--simular")
    assert codigo == 0
    assert "SIMULACIÓN: se crearía (no se escribió nada)" in salida
    assert salida.count("unidad (nueva)") == len(UNIDADES)
    assert totales(motor_migrate) == antes
    # Simular un consorcio que ya existe igual muestra sus ids, también sin escribir.
    real = correr(contenido(cid))[1]
    despues = totales(motor_migrate)
    codigo, salida, _ = correr(contenido(cid), "--simular")
    assert codigo == 0
    assert unidades_impresas(salida) == unidades_impresas(real)
    assert totales(motor_migrate) == despues


def test_sin_id_no_escribe_y_propone_uno(correr, motor_migrate):
    antes = totales(motor_migrate)
    codigo, _, errores = correr(contenido(None))
    assert codigo == 2
    [propuesto] = re.findall(r'"id": "([0-9a-f-]{36})"', errores)
    assert UUID(propuesto).version == 7
    assert totales(motor_migrate) == antes


@pytest.mark.parametrize(
    ("datos", "esperado"),
    [
        (lambda c: {**c, "unidades": c["unidades"] * 2}, "unidades repetidas en el archivo"),
        (
            lambda c: {**c, "unidades": [{"designacion": "PB", "coeficiente": "0.25"}]},
            "coeficiente",
        ),
        (
            lambda c: {**c, "consorcio": {**c["consorcio"], "domicilio": "Alvear 100"}},
            "domicilio",
        ),
        (
            lambda c: {**c, "consorcio": {**c["consorcio"], "zona_horaria": "Marte/Olympus"}},
            "zona horaria desconocida",
        ),
        (lambda c: {**c, "consorcio": {**c["consorcio"], "cuit": "30-71234567-8"}}, "cuit"),
        (lambda c: {**c, "unidades": []}, "unidades"),
    ],
)
def test_archivos_invalidos_no_llegan_a_la_base(correr, motor_migrate, datos, esperado):
    antes = totales(motor_migrate)
    codigo, _, errores = correr(datos(contenido(uuid4())))
    assert codigo == 2
    assert errores.startswith("archivo inválido")
    assert esperado in errores
    assert totales(motor_migrate) == antes


def test_lee_el_archivo_de_la_entrada_estandar(monkeypatch, capsys):
    monkeypatch.setenv("CEM_DATABASE_URL", url_rol("app"))
    cid = uuid4()
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(contenido(cid))))
    assert alta_consorcio.main(["--archivo", "-"]) == 0
    assert f"consorcio {cid}" in capsys.readouterr().out


def test_otro_consorcio_no_ve_lo_creado(correr, motor_app, datos):
    """PA15: con otro consorcio en el ámbito, el alta nueva no aparece en ninguna tabla."""
    cid = uuid4()
    assert correr(contenido(cid))[0] == 0
    consultas = {
        "consorcio": "select count(*) from nucleo.consorcio where id = :c",
        "unidad": "select count(*) from nucleo.unidad where consorcio_id = :c",
        "eventos": "select count(*) from auditoria.eventos where consorcio_id = :c",
    }
    with transaccion(motor_app, persona_id=None, consorcio_id=datos.consorcio_a) as conn:
        assert {
            k: conn.execute(text(q), {"c": cid}).scalar_one() for k, q in consultas.items()
        } == {
            "consorcio": 0,
            "unidad": 0,
            "eventos": 0,
        }
    # Control: con su propio consorcio en el ámbito, las mismas consultas sí lo ven.
    with transaccion(motor_app, persona_id=None, consorcio_id=cid) as conn:
        assert {
            k: conn.execute(text(q), {"c": cid}).scalar_one() for k, q in consultas.items()
        } == {
            "consorcio": 1,
            "unidad": len(UNIDADES),
            "eventos": 1 + len(UNIDADES),
        }


@pytest.mark.redis
def test_invitar_con_los_ids_impresos_y_aceptar(app, correr, motor_migrate, monkeypatch):  # noqa: F811
    """El recorrido del propietario: alta, invitación por el equipo y aceptación por la API."""
    cid = uuid4()
    codigo, salida, _ = correr(contenido(cid))
    assert codigo == 0
    unidad = unidades_impresas(salida)["1° A"]
    correo = correo_nuevo()
    monkeypatch.setenv("CEM_ORIGEN_PUBLICO", ORIGEN)
    argumentos = ["--consorcio", str(cid), "--unidad", str(unidad), "--tipo", "propietario"]
    assert script_invitar.main([*argumentos, "--correo", correo, "--nombre", "Dueño"]) == 0
    nuevo = navegador(app)
    r = nuevo.post(ACEPTAR, json={"token": token_invitacion(motor_migrate, correo)})
    assert r.status_code == 200, r.text
    assert r.json()["cuenta_nueva"] is True
    [mio] = nuevo.get("/api/v1/me/consorcios").json()
    assert (mio["id"], mio["nombre"], mio["vinculos"]) == (
        str(cid),
        "Edificio Prueba de Alta",
        ["propietario"],
    )
    # S2a: la invitación del equipo no escribe evento (queda con `invitada_por` nulo); aceptarla,
    # que es un acto de la persona por la API, sí.
    with motor_migrate.connect() as c:
        acciones = c.execute(
            text("select accion, origen from auditoria.eventos where consorcio_id = :c"
                 " and accion like 'identidad.invitacion.%' order by secuencia"), {"c": cid},
        ).all()  # fmt: skip
    assert [tuple(x) for x in acciones] == [("identidad.invitacion.aceptar", "api")]
