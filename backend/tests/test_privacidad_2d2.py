"""El titular confirma su caso y recibe la copia de sus datos en la aplicación (H2d §5, §9.1; 0042).

Decisión del propietario del 28-10-2026, opción (a): solo dentro de la aplicación y solo al titular
autenticado, con reautenticación reciente (contraseña si la tiene; si no, entrar de nuevo con el
enlace; y el TOTP verificado si está activo). Cada negación lleva su control: el mismo pedido que
sí cumple pasa.
"""

import re
from datetime import timedelta
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db import transaccion
from app.identidad import contrasena, sesiones
from app.identidad import segundo_factor as sf
from scripts import privacidad
from tests.test_identidad import app, csrf  # noqa: F401  (`app` es un fixture)
from tests.test_identidad_cierre_y_purga import entrar_como
from tests.test_identidad_tabla import nueva_persona
from tests.test_privacidad_2d1 import abrir, caso, codigo_del_correo, con_url, confiable  # noqa: F401
from tests.test_segundo_factor import con_factor, reloj  # noqa: F401
from tests.test_vinculo_0014 import base_aparte, escena  # noqa: F401

pytestmark = [pytest.mark.db, pytest.mark.redis]

BASE = "/api/v1/privacidad/casos"
CONTRASENA = "una frase larga de prueba"
MIGRACIONES = Path(__file__).resolve().parents[1] / "migrations" / "versions"


def caso_de(capsys, correo: str, derecho: str = "acceso") -> str:
    """Abre un caso por soporte y lo corrobora (la persona de prueba no tiene un correo
    confiable)."""
    k = abrir(capsys, "--correo", correo, "--derecho", derecho)
    assert privacidad.main(["corroborar", "--caso", k, "--texto", "Nota del Consejo"]) == 0
    capsys.readouterr()
    return k


def titular(app, motor_migrate, capsys, derecho: str = "acceso"):  # noqa: F811
    """Una persona nueva con sesión recién abierta y un caso corroborado."""
    persona, correo = nueva_persona(motor_migrate)
    c = entrar_como(app, motor_migrate, correo)
    k = caso_de(capsys, correo, derecho)
    return persona, correo, c, k, codigo_del_correo(motor_migrate, k)


def confirmar(c, k, codigo, **extra):
    return c.post(f"{BASE}/{k}/confirmar", json={"codigo": codigo, **extra}, headers=csrf(c))


def pedir_copia(c, k, **extra):
    return c.post(f"{BASE}/{k}/copia", json=extra, headers=csrf(c))


def envejecer_sesiones(motor_migrate, persona) -> None:
    """La sesión empezó hace más de 10 minutos (la vigencia de la reautenticación)."""
    with motor_migrate.begin() as conn:
        conn.execute(
            text(
                "update nucleo.sesion set creada_en = now() - :v - interval '1 minute'"
                " where persona_id = :p"
            ),
            {"p": persona, "v": sesiones.VIGENCIA_SEGUNDO_FACTOR_RECIENTE},
        )


def con_contrasena(motor_migrate, persona) -> None:
    with motor_migrate.begin() as conn:
        conn.execute(
            text("insert into nucleo.contrasena (persona_id, hash) values (:p, :h)"),
            {"p": persona, "h": contrasena.hashear(CONTRASENA)},
        )


def falta(r) -> str:
    assert r.status_code == 403, r.text
    assert r.json()["codigo"] == "reautenticacion_requerida"
    return r.json()["detalle"]["falta"]


def eventos_totales(motor_migrate) -> int:
    with motor_migrate.connect() as conn:
        return conn.execute(text("select count(*) from auditoria.eventos")).scalar_one()


# Solo el titular ------------------------------------------------------------------------------


@pytest.mark.usefixtures("con_url")
def test_un_caso_de_otra_persona_no_existe_para_ella(app, motor_migrate, capsys):  # noqa: F811
    persona, _, c, k, codigo = titular(app, motor_migrate, capsys)
    _, otro = nueva_persona(motor_migrate)
    ajena = entrar_como(app, motor_migrate, otro)
    r = confirmar(ajena, k, codigo)
    assert r.status_code == 404 and r.json()["codigo"] == "no_encontrado"
    assert pedir_copia(ajena, k).status_code == 404
    assert ajena.get(BASE).json() == {"elementos": []}
    assert caso(motor_migrate, k)["estado"] == "abierto"
    # Control: el titular, con el mismo código, sí.
    listado = c.get(BASE).json()["elementos"]
    assert [x["id"] for x in listado] == [k] and listado[0]["estado"] == "abierto"
    assert listado[0]["codigo_vence_en"] is not None
    assert confirmar(c, k, codigo).status_code == 204
    assert caso(motor_migrate, k)["estado"] == "confirmado"
    assert c.get(BASE).json()["elementos"][0]["codigo_vence_en"] is None


@pytest.mark.usefixtures("con_url")
def test_sin_sesion_no_hay_ruta(app, motor_migrate, capsys):  # noqa: F811
    _, _, c, k, codigo = titular(app, motor_migrate, capsys)
    sin_sesion = app.abrir(None)
    assert sin_sesion.get(BASE).status_code == 401
    assert sin_sesion.post(f"{BASE}/{k}/confirmar", json={"codigo": codigo}).status_code in (
        401,
        403,
    )
    assert caso(motor_migrate, k)["estado"] == "abierto"


# Reautenticación reciente ---------------------------------------------------------------------


@pytest.mark.usefixtures("con_url")
def test_sin_contrasena_hace_falta_haber_entrado_hace_poco(app, motor_migrate, capsys):  # noqa: F811
    persona, correo, c, k, codigo = titular(app, motor_migrate, capsys)
    envejecer_sesiones(motor_migrate, persona)
    assert falta(confirmar(c, k, codigo)) == "entrar_de_nuevo"
    # Escribir una contraseña que no tiene no reemplaza volver a entrar.
    assert falta(confirmar(c, k, codigo, contrasena=CONTRASENA)) == "entrar_de_nuevo"
    assert caso(motor_migrate, k)["estado"] == "abierto"
    # Control: entra de nuevo con el enlace y confirma.
    nueva = entrar_como(app, motor_migrate, correo)
    assert confirmar(nueva, k, codigo).status_code == 204


@pytest.mark.usefixtures("con_url")
def test_con_contrasena_la_pide_en_el_mismo_pedido(app, motor_migrate, capsys):  # noqa: F811
    persona, _, c, k, codigo = titular(app, motor_migrate, capsys)
    con_contrasena(motor_migrate, persona)
    assert falta(confirmar(c, k, codigo)) == "contrasena"
    assert falta(confirmar(c, k, codigo, contrasena="otra frase que no es")) == "contrasena"
    assert caso(motor_migrate, k)["estado"] == "abierto"
    # Control: con la contraseña correcta confirma, aunque la sesión no sea nueva.
    envejecer_sesiones(motor_migrate, persona)
    assert confirmar(c, k, codigo, contrasena=CONTRASENA).status_code == 204


@pytest.mark.usefixtures("con_url")
def test_con_totp_hace_falta_haberlo_verificado_hace_poco(app, motor_migrate, capsys, reloj):  # noqa: F811
    c, secreto, _, correo = con_factor(app, motor_migrate, reloj)
    with motor_migrate.begin() as conn:
        persona = conn.execute(
            text("select id from nucleo.persona where correo = :c"), {"c": correo}
        ).scalar_one()
        conn.execute(
            text("update nucleo.sesion set segundo_factor_en = null where persona_id = :p"),
            {"p": persona},
        )
    k = caso_de(capsys, correo)
    codigo = codigo_del_correo(motor_migrate, k)
    assert falta(confirmar(c, k, codigo)) == "segundo_factor"
    # Una verificación de hace más de 10 minutos tampoco alcanza, aunque la contraseña sí (la
    # sesión es vieja: sin contraseña faltaría primero entrar de nuevo).
    con_contrasena(motor_migrate, persona)
    with motor_migrate.begin() as conn:
        conn.execute(
            text(
                "update nucleo.sesion set creada_en = now() - interval '1 hour',"
                " segundo_factor_en = now() - :v - interval '1 second' where persona_id = :p"
            ),
            {"p": persona, "v": sesiones.VIGENCIA_SEGUNDO_FACTOR_RECIENTE},
        )
    assert falta(confirmar(c, k, codigo, contrasena=CONTRASENA)) == "segundo_factor"
    # Control: verifica con el código de la aplicación y confirma.
    reloj.paso += 1
    r = c.post(
        "/api/v1/auth/totp/verificar",
        json={"codigo": sf.codigo_en(secreto, reloj.paso)},
        headers=csrf(c),
    )
    assert r.status_code == 200, r.text
    assert confirmar(c, k, codigo, contrasena=CONTRASENA).status_code == 204


def test_la_vigencia_de_la_base_es_la_de_la_api():
    """La base repite los 10 minutos de `VIGENCIA_SEGUNDO_FACTOR_RECIENTE`."""
    fuente = (MIGRACIONES / "0042_copia_titular_2d2.py").read_text(encoding="utf-8")
    minutos = {int(x) for x in re.findall(r"interval '(\d+) minutes'", fuente)}
    assert minutos == {int(sesiones.VIGENCIA_SEGUNDO_FACTOR_RECIENTE / timedelta(minutes=1))}


# Código y estado -----------------------------------------------------------------------------


@pytest.mark.usefixtures("con_url")
def test_un_codigo_incorrecto_vencido_o_usado_no_confirma(app, motor_migrate, capsys):  # noqa: F811
    _, _, c, k, codigo = titular(app, motor_migrate, capsys)
    r = confirmar(c, k, "AAAAA-AAAAA-AAAAA-AAAAA-AAAAA-A")
    assert r.status_code == 400 and r.json()["codigo"] == "codigo_caso_invalido"
    with motor_migrate.begin() as conn:
        conn.execute(
            text(
                "update plataforma.caso_privacidad"
                " set codigo_vence_en = now() - interval '1 second'"
                " where id = :k"
            ),
            {"k": k},
        )
    assert confirmar(c, k, codigo).json()["codigo"] == "codigo_caso_invalido"
    with motor_migrate.begin() as conn:
        conn.execute(
            text(
                "update plataforma.caso_privacidad set codigo_vence_en = now() + interval '1 hour'"
                " where id = :k"
            ),
            {"k": k},
        )
    # Control: vigente y correcto, confirma; escrito en minúsculas y con espacios, también.
    assert confirmar(c, k, codigo.lower().replace("-", " ")).status_code == 204
    r = confirmar(c, k, codigo)
    assert r.status_code == 409 and r.json()["codigo"] == "caso_no_disponible"


@pytest.mark.usefixtures("con_url")
def test_sin_correo_confiable_hace_falta_la_corroboracion(app, motor_migrate, capsys):  # noqa: F811
    persona, correo = nueva_persona(motor_migrate)
    c = entrar_como(app, motor_migrate, correo)
    k = abrir(capsys, "--correo", correo, "--derecho", "acceso")
    codigo = codigo_del_correo(motor_migrate, k)
    r = confirmar(c, k, codigo)
    assert r.status_code == 409 and r.json()["codigo"] == "caso_no_disponible"
    assert caso(motor_migrate, k)["codigo_usado_en"] is None
    # Control: con la corroboración, el mismo código sirve.
    assert privacidad.main(["corroborar", "--caso", k, "--texto", "Nota del Consejo"]) == 0
    assert confirmar(c, k, codigo).status_code == 204


@pytest.mark.usefixtures("con_url")
def test_confirma_supresion_y_borra_el_correo_sin_auditar(app, motor_migrate, capsys):  # noqa: F811
    persona, _, c, k, codigo = titular(app, motor_migrate, capsys, derecho="supresion")
    con_contrasena(motor_migrate, persona)
    antes = eventos_totales(motor_migrate)
    evento = caso(motor_migrate, k)["codigo_evento_id"]
    assert confirmar(c, k, codigo, contrasena=CONTRASENA).status_code == 204
    k_ = caso(motor_migrate, k)
    assert k_["estado"] == "confirmado" and "En la aplicación" in k_["confirmacion"]
    with motor_migrate.connect() as conn:
        assert (
            conn.execute(
                text("select count(*) from plataforma.outbox where id = :e"), {"e": evento}
            ).scalar_one()
            == 0
        )
    assert eventos_totales(motor_migrate) == antes
    # La copia es solo para un caso de acceso.
    r = pedir_copia(c, k, contrasena=CONTRASENA)
    assert r.status_code == 409 and r.json()["codigo"] == "caso_no_disponible"


# La copia -------------------------------------------------------------------------------------


@pytest.mark.usefixtures("con_url")
def test_la_copia_de_la_cuenta_se_entrega_una_vez_sin_secretos(app, motor_migrate, capsys):  # noqa: F811
    persona, correo, c, k, codigo = titular(app, motor_migrate, capsys)
    con_contrasena(motor_migrate, persona)
    # Sin confirmar no hay copia.
    r = pedir_copia(c, k, contrasena=CONTRASENA)
    assert r.status_code == 409 and r.json()["codigo"] == "caso_no_disponible"
    assert confirmar(c, k, codigo, contrasena=CONTRASENA).status_code == 204
    # Sin la reautenticación tampoco.
    assert falta(pedir_copia(c, k)) == "contrasena"
    antes = eventos_totales(motor_migrate)
    r = pedir_copia(c, k, contrasena=CONTRASENA)
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store"
    copia = r.json()
    assert copia["caso_id"] == k and copia["alcance_consorcio_id"] is None
    datos = copia["datos"]
    assert set(datos) == {"alcance", "persona", "sesiones", "credenciales", "casos_privacidad"}
    assert datos["persona"]["correo"] == correo
    assert datos["credenciales"]["contrasena"] is True
    assert datos["credenciales"]["segundo_factor"] is False
    assert len(datos["sesiones"]) >= 1 and {"ip", "agente"} <= set(datos["sesiones"][0])
    assert [x["id"] for x in datos["casos_privacidad"]] == [k]
    with motor_migrate.connect() as conn:
        guardado = conn.execute(
            text("select hash from nucleo.contrasena where persona_id = :p"), {"p": persona}
        ).scalar_one()
        token_sesion = conn.execute(
            text("select encode(token_hash, 'hex') from nucleo.sesion where persona_id = :p"),
            {"p": persona},
        ).first()[0]
    assert guardado not in r.text and "argon2" not in r.text and token_sesion not in r.text
    assert "Nota del Consejo" not in r.text and "codigo_hash" not in r.text
    assert eventos_totales(motor_migrate) == antes
    k_ = caso(motor_migrate, k)
    assert k_["estado"] == "resuelto"
    assert k_["resolucion"]["partes"] == sorted(datos)
    assert correo not in str(k_["resolucion"])
    # Una sola entrega por caso.
    r = pedir_copia(c, k, contrasena=CONTRASENA)
    assert r.status_code == 409 and r.json()["codigo"] == "caso_no_disponible"


@pytest.mark.usefixtures("con_url")
def test_copia_de_un_consorcio_sin_terceros(app, confiable, motor_migrate, capsys):  # noqa: F811
    """PA30: la copia del inquilino trae lo suyo en el consorcio y nada que identifique al
    administrador, al Consejo ni a la propietaria (ids, correos ni nombres)."""
    cons, inq = confiable["cons"], confiable["inq"]
    # Un acto de otra persona sobre el titular, con su IP y un motivo que la nombra: de él sale
    # solo que fue «otra persona».
    with motor_migrate.begin() as conn:
        conn.execute(
            text(
                "insert into auditoria.eventos (consorcio_id, actor_persona_id, origen, accion,"
                " entidad, entidad_id, motivo, ip, hash_previo, hash)"
                " values (:c, :a, 'api', 'prueba.sobre_persona', 'persona', :p,"
                " (select nombre || ' ' || correo from nucleo.persona where id = :a),"
                " '203.0.113.77', '', '')"
            ),
            {"c": cons, "a": confiable["admin"], "p": inq},
        )
    c = entrar_como(app, motor_migrate, confiable["correo"])
    k = abrir(
        capsys, "--correo", confiable["correo"], "--derecho", "acceso", "--consorcio", str(cons)
    )
    assert confirmar(c, k, codigo_del_correo(motor_migrate, k)).status_code == 204
    r = pedir_copia(c, k)
    assert r.status_code == 200, r.text
    datos = r.json()["datos"]
    assert r.json()["alcance_consorcio_id"] == str(cons)
    assert set(datos) == {
        "alcance", "consorcio_id", "vinculos", "mandatos", "invitaciones", "eventos",
        "documentos", "versiones_subidas", "identidad_humana",
    }  # fmt: skip
    assert [v["tipo"] for v in datos["vinculos"]] == ["inquilino"]
    assert datos["vinculos"][0]["confirmado_en"] is not None
    assert len(datos["invitaciones"]) == 1
    assert datos["eventos"], "la aceptación de su invitación es un evento suyo"
    assert {e["quien"] for e in datos["eventos"]} <= {
        "titular", "otra persona", "soporte", "trabajos", "planificador",
    }  # fmt: skip
    ajeno = [e for e in datos["eventos"] if e["accion"] == "prueba.sobre_persona"]
    assert ajeno == [
        {"creado_en": ajeno[0]["creado_en"], "accion": "prueba.sobre_persona", "entidad": "persona",
         "papel": "entidad", "quien": "otra persona", "ip": None}
    ]  # fmt: skip
    assert "203.0.113.77" not in r.text
    assert all(e["ip"] is None for e in datos["eventos"] if e["quien"] != "titular")
    assert all(set(e) == {"creado_en", "accion", "entidad", "papel", "quien", "ip"}
               for e in datos["eventos"])  # fmt: skip
    assert datos["identidad_humana"] == {"verificada": False, "desde": None}
    with motor_migrate.connect() as conn:
        otras = conn.execute(
            text("select id::text, correo, nombre from nucleo.persona where id in (:a, :m, :p)"),
            {"a": confiable["admin"], "m": confiable["mc"], "p": confiable["prop"]},
        ).all()
    assert len(otras) == 3
    for id_, correo, _ in otras:
        assert id_ not in r.text and correo not in r.text
    assert str(inq) not in r.text  # tampoco su propio id interno: no hace falta


def test_cem_app_sigue_sin_la_tabla_y_sin_las_funciones_internas(motor_app):
    with transaccion(motor_app, persona_id=None) as conn:
        with pytest.raises(DBAPIError, match="permission denied"):
            conn.execute(text("select * from plataforma.caso_privacidad"))
    for funcion in (
        "plataforma.privacidad_copia(null::uuid, null::uuid)",
        "plataforma.privacidad_reautenticacion(null::uuid, null::uuid, true)",
        "plataforma.privacidad_confirmar(null::uuid, null::bytea, 'x')",
    ):
        with transaccion(motor_app, persona_id=None) as conn:
            with pytest.raises(DBAPIError, match="permission denied"):
                conn.execute(text(f"select {funcion}"))
    # Control: la función pública sí la ejecuta (y sin persona en la sesión se niega adentro).
    with transaccion(motor_app, persona_id=None) as conn:
        with pytest.raises(DBAPIError, match="sin persona en la sesión"):
            conn.execute(text("select * from plataforma.privacidad_mis_casos()"))


def test_0042_ida_y_vuelta(base_aparte):  # noqa: F811
    cfg, motor = base_aparte
    command.upgrade(cfg, "0042")

    def existe() -> bool:
        with motor.connect() as conn:
            return conn.execute(
                text("select to_regprocedure('plataforma.privacidad_entregar_copia(uuid, uuid,"
                     " boolean)') is not null")
            ).scalar_one()  # fmt: skip

    assert existe()
    command.downgrade(cfg, "0041")
    assert not existe()
    command.upgrade(cfg, "0042")  # control: vuelve a subir limpio
    assert existe()
