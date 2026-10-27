"""Supresión y rectificación del nombre por soporte (H2d §2, §6 y §10; 0040).

Cada caso parte de uno confirmado por el titular con el código de su correo (2d-1). Cada negación
lleva su control: el mismo pedido que sí cumple pasa.
"""

import io
import json
import os
import re
import threading
import time
from datetime import timedelta

import pytest
from alembic import command
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from scripts import privacidad
from tests.conftest import url_rol
from tests.test_modo_operacion import consorcio_con_persona
from tests.test_privacidad_2d1 import (  # noqa: F401
    abrir,
    caso,
    codigo_del_correo,
    con_url,
    confiable,
    confirmar_caso,
)
from tests.test_vinculo_0014 import base_aparte, cadena_rota, escena  # noqa: F401

pytestmark = [pytest.mark.db, pytest.mark.redis]

ESPERAS = 50
INTERVALO = 0.1
"""Hasta 5 s para ver la supresión esperando el cerrojo de la persona."""
NUEVAS = (
    "plataforma.privacidad_plan_supresion(uuid)",
    "plataforma.privacidad_ejecutar_supresion(uuid, jsonb, text, text)",
    "plataforma.privacidad_rectificar_nombre(uuid, text)",
)
INTERNAS = (
    "plataforma.privacidad_caso_para_resolver(uuid, text)",
    "plataforma.privacidad_supresion(plataforma.caso_privacidad, boolean)",
    "plataforma.privacidad_correo_confiable(uuid, uuid)",
    "plataforma.privacidad_borrar_correo(uuid)",
)


def confirmado(capsys, motor_migrate, correo, derecho="supresion", *alcance) -> str:
    """Un caso abierto por el correo del titular y confirmado con su código."""
    k = abrir(capsys, "--correo", correo, "--derecho", derecho, *alcance)
    r, err = confirmar_caso(k, codigo_del_correo(motor_migrate, k), capsys)
    assert r == 0, err
    return k


def suprimir(capsys, k, *extra) -> tuple[int, str, str]:
    r = privacidad.main(["suprimir", "--caso", k, *extra])
    salida = capsys.readouterr()
    return r, salida.out, salida.err


def aplicar(capsys, k) -> tuple[int, str, str]:
    """Como soporte: mira el plan y lo aplica con su huella."""
    r, out, err = suprimir(capsys, k)
    if r != 0:
        return r, out, err
    return suprimir(capsys, k, "--ejecutar", re.search(r"^huella: (\w+)$", out, re.M).group(1))


def uno(motor_migrate, sql, **p):
    with motor_migrate.connect() as c:
        return c.execute(text(sql), p).one()


def cuenta(motor_migrate, sql, **p) -> int:
    with motor_migrate.connect() as c:
        return c.execute(text(sql), p).scalar_one()


ACCESOS = ("sesion", "enlace_acceso", "codigo_acceso")
CREDENCIALES = ("contrasena", "factor_totp", "codigo_recuperacion", "desafio_contrasena")


def accesos_y_credenciales(motor_migrate, persona, cons) -> None:
    """Una fila en cada tabla global de la persona (accesos, credenciales e idempotencia),
    cargadas después de confirmar el caso (con contraseña, no se confirma por correo)."""
    h = os.urandom
    with motor_migrate.begin() as c:
        c.execute(text("insert into nucleo.sesion (persona_id, token_hash, dispositivo, ip,"
                       " vence_en) values (:p, :t, gen_random_uuid(), '203.0.113.9',"
                       " now() + interval '1 day')"), {"p": persona, "t": h(32)})  # fmt: skip
        enlace = c.execute(text("insert into nucleo.enlace_acceso (persona_id, token_hash,"
                                " nonce_hash, dispositivo, ip, vence_en) values (:p, :t, :n,"
                                " gen_random_uuid(), '203.0.113.9', now() + interval '5 minutes')"
                                " returning id"),
                           {"p": persona, "t": h(32), "n": h(32)}).scalar_one()  # fmt: skip
        c.execute(text("insert into nucleo.codigo_acceso (enlace_id, persona_id, codigo_hash,"
                       " vence_en) values (:e, :p, :h, now() + interval '5 minutes')"),
                  {"e": enlace, "p": persona, "h": h(32)})  # fmt: skip
        c.execute(text("insert into nucleo.contrasena (persona_id, hash)"
                       " values (:p, '$argon2id$v=19$prueba')"), {"p": persona})  # fmt: skip
        c.execute(text("insert into nucleo.factor_totp (persona_id, secreto_cifrado,"
                       " confirmado_en) values (:p, :s, now())"),
                  {"p": persona, "s": h(48)})  # fmt: skip
        c.execute(text("insert into nucleo.codigo_recuperacion (persona_id, codigo_hash)"
                       " values (:p, :h)"), {"p": persona, "h": h(32)})  # fmt: skip
        c.execute(text("insert into nucleo.desafio_contrasena (persona_id, token_hash, vence_en)"
                       " values (:p, :t, now() + interval '2 minutes')"),
                  {"p": persona, "t": h(32)})  # fmt: skip
        c.execute(text("insert into plataforma.idempotencia (consorcio_id, persona_id, metodo,"
                       " ruta, clave, huella, estado_http, respuesta, vence_en) values (:c, :p,"
                       " 'POST', '/api/v1/x', 'k', :h, 200, '{}', now() + interval '1 day')"),
                  {"c": cons, "p": persona, "h": h(32)})  # fmt: skip


def filas_globales(motor_migrate, persona) -> dict[str, int]:
    tablas = [f"nucleo.{t}" for t in ACCESOS + CREDENCIALES] + ["plataforma.idempotencia"]
    with motor_migrate.connect() as c:
        return {t: c.execute(text(f"select count(*) from {t} where persona_id = :p"),
                             {"p": persona}).scalar_one() for t in tablas}  # fmt: skip


def terminar_vinculo(motor_migrate, vinculo, cons) -> None:
    with motor_migrate.begin() as c:
        hoy = c.execute(text("select nucleo.hoy_del_consorcio(:c)"), {"c": cons}).scalar_one()
        c.execute(text("update nucleo.vinculo set vigente_desde = :d, vigente_hasta = :h"
                       " where id = :v"), {"d": hoy - timedelta(days=30), "h": hoy,
                                            "v": vinculo})  # fmt: skip


def plan_de(k) -> list[dict]:
    soporte = create_engine(url_rol("soporte_privacidad"))
    try:
        with soporte.connect() as c:
            return c.execute(
                text("select plataforma.privacidad_plan_supresion(:k)"), {"k": k}
            ).scalar_one()
    finally:
        soporte.dispose()


def punto(plan, que) -> dict:
    return next(p for p in plan if p["que"] == que)


# Permisos ------------------------------------------------------------------------------------


def test_solo_soporte_ejecuta_las_funciones_y_nadie_las_internas(motor_migrate):
    with motor_migrate.connect() as c:
        for f in NUEVAS:
            assert c.execute(text("select has_function_privilege('cem_app', :f, 'execute'),"
                                  " has_function_privilege('cem_soporte_privacidad', :f,"
                                  " 'execute')"), {"f": f}).one() == (False, True)  # fmt: skip
        for f in INTERNAS:
            assert c.execute(text("select has_function_privilege('cem_app', :f, 'execute'),"
                                  " has_function_privilege('cem_soporte_privacidad', :f,"
                                  " 'execute')"), {"f": f}).one() == (False, False)  # fmt: skip
        # Control: la consulta distingue un permiso concedido.
        assert c.execute(text("select has_function_privilege('cem_app',"
                              " 'plataforma.persona_actual()', 'execute')")
                         ).scalar_one()  # fmt: skip


# El caso que se puede resolver ---------------------------------------------------------------


def test_sin_confirmar_o_de_otro_derecho_no_hay_plan(confiable, motor_migrate, con_url, capsys):  # noqa: F811
    abierto = abrir(capsys, "--correo", confiable["correo"], "--derecho", "supresion")
    r, _, err = suprimir(capsys, abierto)
    assert r == 1 and "caso_sin_confirmar" in err
    privacidad.main(["rechazar", "--caso", abierto, "--motivo", "Duplicado"])
    capsys.readouterr()
    acceso = confirmado(capsys, motor_migrate, confiable["correo"], "acceso")
    r, _, err = suprimir(capsys, acceso)
    assert r == 1 and "otro_derecho" in err
    # Control: el de supresión confirmado sí tiene plan.
    k = confirmado(capsys, motor_migrate, confiable["correo"])
    r, out, err = suprimir(capsys, k)
    assert r == 0, err
    assert "plan (sin aplicar)" in out


def test_el_plan_no_cambia_nada_ni_cierra_el_caso(confiable, motor_migrate, con_url, capsys):  # noqa: F811
    k = confirmado(capsys, motor_migrate, confiable["correo"])
    accesos_y_credenciales(motor_migrate, confiable["inq"], confiable["cons"])
    antes = uno(motor_migrate, "select (select count(*) from nucleo.sesion where persona_id = :p),"
                " (select correo from nucleo.persona where id = :p),"
                " (select count(*) from plataforma.outbox)", p=confiable["inq"])  # fmt: skip
    assert suprimir(capsys, k)[0] == 0
    despues = uno(motor_migrate, "select (select count(*) from nucleo.sesion"
                  " where persona_id = :p), (select correo from nucleo.persona where id = :p),"
                  " (select count(*) from plataforma.outbox)", p=confiable["inq"])  # fmt: skip
    assert despues == antes
    assert caso(motor_migrate, k)["estado"] == "confirmado"


# Alcance cuenta ------------------------------------------------------------------------------


def test_con_vinculo_vigente_se_conserva_la_persona_y_sus_credenciales(
    confiable,  # noqa: F811
    motor_migrate,
    con_url,  # noqa: F811
    capsys,
):
    k = confirmado(capsys, motor_migrate, confiable["correo"])
    accesos_y_credenciales(motor_migrate, confiable["inq"], confiable["cons"])
    eventos = cuenta(motor_migrate, "select count(*) from auditoria.eventos")
    r, out, err = aplicar(capsys, k)
    assert r == 0, err
    persona = uno(motor_migrate, "select correo, nombre, eliminado_en from nucleo.persona"
                  " where id = :p", p=confiable["inq"])  # fmt: skip
    assert persona.correo == confiable["correo"] and persona.eliminado_en is None
    # Las credenciales quedan; los accesos y la idempotencia se borran (alcance cuenta).
    quedan = filas_globales(motor_migrate, confiable["inq"])
    assert all(quedan[f"nucleo.{t}"] == 1 for t in CREDENCIALES), quedan
    assert all(quedan[f"nucleo.{t}"] == 0 for t in ACCESOS), quedan
    assert quedan["plataforma.idempotencia"] == 0
    assert cuenta(motor_migrate, "select count(*) from auditoria.eventos") == eventos
    fila = caso(motor_migrate, k)
    assert fila["estado"] == "resuelto"
    plan = fila["resolucion"]["supresion"]
    assert punto(plan, "persona: correo y nombre")["tratamiento"] == "conservar"
    credenciales = punto(plan, "contraseña, TOTP y códigos de recuperación")
    assert (credenciales["tratamiento"], credenciales["filas"]) == ("conservar", len(CREDENCIALES))


def test_sin_vinculo_ni_mandato_vigente_se_anonimiza_y_libera_el_correo(
    confiable,  # noqa: F811
    motor_migrate,
    con_url,  # noqa: F811
    capsys,
):
    k = confirmado(capsys, motor_migrate, confiable["correo"])
    terminar_vinculo(motor_migrate, confiable["v_inq"], confiable["cons"])
    accesos_y_credenciales(motor_migrate, confiable["inq"], confiable["cons"])
    eventos = cuenta(motor_migrate, "select count(*) from auditoria.eventos")
    r, out, err = aplicar(capsys, k)
    assert r == 0, err
    persona = uno(motor_migrate, "select id, correo, nombre, eliminado_en from nucleo.persona"
                  " where id = :p", p=confiable["inq"])  # fmt: skip
    assert persona.correo == f"suprimida+{persona.id}@invalid.cem"
    assert persona.nombre == "Persona suprimida" and persona.eliminado_en is not None
    quedan = filas_globales(motor_migrate, confiable["inq"])
    assert set(quedan.values()) == {0}, quedan
    # La invitación que aceptó queda sin su correo ni su nombre; el vínculo se conserva.
    assert cuenta(motor_migrate, "select count(*) from nucleo.invitacion where correo = :c",
                  c=confiable["correo"]) == 0  # fmt: skip
    assert cuenta(motor_migrate, "select count(*) from nucleo.invitacion where persona_id = :p"
                  " and nombre = 'Persona suprimida'", p=confiable["inq"]) == 1  # fmt: skip
    assert cuenta(motor_migrate, "select count(*) from nucleo.vinculo where id = :v",
                  v=confiable["v_inq"]) == 1  # fmt: skip
    # El correo queda libre, la auditoría intacta y la resolución va al correo de antes.
    with motor_migrate.begin() as c:
        c.execute(text("insert into nucleo.persona (correo, nombre) values (:c, 'Otra')"),
                  {"c": confiable["correo"]})  # fmt: skip
    assert cuenta(motor_migrate, "select count(*) from auditoria.eventos") == eventos
    assert cadena_rota(motor_migrate, confiable["cons"]) == 0
    texto = cuenta(motor_migrate, "select carga ->> 'texto' from plataforma.outbox"
                   " where carga ->> 'para' = :c and carga ->> 'asunto' = :a",
                   c=confiable["correo"], a=privacidad.ASUNTO_RESOLUCION)  # fmt: skip
    assert "Anonimizado: persona: correo y nombre" in texto
    assert confiable["correo"] not in texto
    assert "Anonimizado: persona" in out


def test_un_mandato_vigente_tambien_conserva_la_persona(escena, motor_migrate, con_url, capsys):  # noqa: F811
    """El miembro del Consejo: su vínculo de propietario se termina, el mandato sigue."""
    correo = uno(motor_migrate, "select correo from nucleo.persona where id = :p",
                 p=escena["mc"]).correo  # fmt: skip
    k = abrir(capsys, "--correo", correo, "--derecho", "supresion")
    codigo = codigo_del_correo(motor_migrate, k)
    if confirmar_caso(k, codigo, capsys)[0] != 0:
        assert privacidad.main(["corroborar", "--caso", k, "--texto", "Nota del Consejo"]) == 0
        capsys.readouterr()
        assert confirmar_caso(k, codigo, capsys)[0] == 0
    terminar_vinculo(motor_migrate, escena["v_prop_mc"], escena["cons"])
    plan = plan_de(k)
    assert punto(plan, "persona: correo y nombre")["tratamiento"] == "conservar"


# Alcance de un consorcio ---------------------------------------------------------------------


def test_un_caso_de_consorcio_no_toca_nada_global(confiable, motor_migrate, con_url, capsys):  # noqa: F811
    k = confirmado(capsys, motor_migrate, confiable["correo"], "supresion",
                   "--consorcio", str(confiable["cons"]))  # fmt: skip
    terminar_vinculo(motor_migrate, confiable["v_inq"], confiable["cons"])
    accesos_y_credenciales(motor_migrate, confiable["inq"], confiable["cons"])
    antes = filas_globales(motor_migrate, confiable["inq"])
    assert 0 not in antes.values(), antes  # control: hay qué tocar en cada tabla
    r, _, err = aplicar(capsys, k)
    assert r == 0, err
    persona = uno(motor_migrate, "select correo, eliminado_en from nucleo.persona where id = :p",
                  p=confiable["inq"])  # fmt: skip
    assert persona.correo == confiable["correo"] and persona.eliminado_en is None
    assert filas_globales(motor_migrate, confiable["inq"]) == antes
    # Control: lo de ese consorcio sí se trató (la invitación aceptada, anonimizada).
    assert cuenta(motor_migrate, "select count(*) from nucleo.invitacion where persona_id = :p"
                  " and nombre = 'Persona suprimida'", p=confiable["inq"]) == 1  # fmt: skip
    plan = caso(motor_migrate, k)["resolucion"]["supresion"]
    assert punto(plan, "datos de la cuenta (persona, credenciales, accesos)")["tratamiento"] == (
        "conservar"
    )


# El titular sin cuenta y la cola -------------------------------------------------------------


def test_invitado_sin_cuenta_su_invitacion_pendiente_se_borra(
    escena,  # noqa: F811
    motor_migrate,
    con_url,  # noqa: F811
    capsys,
):
    correo = f"pendiente-{os.urandom(4).hex()}@ejemplo.com"
    with motor_migrate.begin() as c:
        c.execute(text("insert into nucleo.invitacion (consorcio_id, unidad_id, tipo, correo,"
                       " nombre, token_hash, invitada_por, vence_en) values (:c, :u,"
                       " 'inquilino', :co, 'Pendiente', sha256(:t), :i, now() + interval '1 day')"),
                  {"c": escena["cons"], "u": escena["u1"], "co": correo, "t": os.urandom(32),
                   "i": escena["admin"]})  # fmt: skip
    k = abrir(capsys, "--correo", correo, "--derecho", "supresion")
    codigo = codigo_del_correo(motor_migrate, k)
    assert privacidad.main(["corroborar", "--caso", k, "--texto", "Nota de la administración"]) == 0
    capsys.readouterr()
    assert confirmar_caso(k, codigo, capsys)[0] == 0
    r, _, err = aplicar(capsys, k)
    assert r == 0, err
    assert cuenta(motor_migrate, "select count(*) from nucleo.invitacion where correo = :c",
                  c=correo) == 0  # fmt: skip
    plan = caso(motor_migrate, k)["resolucion"]["supresion"]
    assert punto(plan, "invitaciones pendientes")["filas"] == 1
    assert not any(p["que"].startswith("persona") for p in plan)


def test_la_cola_pierde_los_correos_pendientes_y_fallidos_y_no_los_en_curso(
    confiable,  # noqa: F811
    motor_migrate,
    con_url,  # noqa: F811
    capsys,
):
    k = confirmado(capsys, motor_migrate, confiable["correo"])
    with motor_migrate.begin() as c:
        for estado, clave in (("pendiente", "a"), ("muerto", "b"), ("en_curso", "c")):
            c.execute(text("insert into plataforma.jobs (tipo, carga, estado, bloqueado_por,"
                           " bloqueado_hasta, clave_unica) values ('notificar.correo',"
                           " jsonb_build_object('para', cast(:p as text), 'asunto', 'x',"
                           " 'texto', 'y'), cast(:e as plataforma.estado_trabajo),"
                           " case when cast(:e as text) = 'en_curso' then 'pytest' end,"
                           " case when cast(:e as text) = 'en_curso'"
                           " then now() + interval '5 minutes' end,"
                           " :k)"),
                      {"p": confiable["correo"], "e": estado,
                       "k": f"pytest-2d3-{clave}-{k}"})  # fmt: skip
        # Control: el correo de otra persona no se toca.
        c.execute(text("insert into plataforma.jobs (tipo, carga, clave_unica) values"
                       " ('notificar.correo', jsonb_build_object('para', 'otra@ejemplo.com',"
                       " 'asunto', 'x', 'texto', 'y'), :k)"),
                  {"k": f"pytest-2d3-d-{k}"})  # fmt: skip
    assert aplicar(capsys, k)[0] == 0
    with motor_migrate.connect() as c:
        quedan = sorted(c.execute(text("select estado::text from plataforma.jobs"
                                       " where clave_unica like :k"),
                                  {"k": f"pytest-2d3-%-{k}"}).scalars())  # fmt: skip
    assert quedan == ["en_curso", "pendiente"]  # el en curso suyo y el pendiente de la otra


# La ejecución contra el plan visto -----------------------------------------------------------


def test_si_el_plan_cambio_no_se_aplica_nada(confiable, motor_migrate, con_url, capsys):  # noqa: F811
    k = confirmado(capsys, motor_migrate, confiable["correo"])
    plan = plan_de(k)
    accesos_y_credenciales(
        motor_migrate, confiable["inq"], confiable["cons"]
    )  # una sesión más: el plan cambia
    sesiones = "select count(*) from nucleo.sesion where persona_id = :p"
    antes = cuenta(motor_migrate, sesiones, p=confiable["inq"])
    soporte = create_engine(url_rol("soporte_privacidad"))
    try:
        with soporte.connect() as c:
            with pytest.raises(DBAPIError, match="plan_cambio"):
                c.execute(text("select plataforma.privacidad_ejecutar_supresion(:k,"
                               " cast(:p as jsonb), 'a', 'b')"),
                          {"k": k, "p": json.dumps(plan)})  # fmt: skip
        assert caso(motor_migrate, k)["estado"] == "confirmado"
        assert cuenta(motor_migrate, sesiones, p=confiable["inq"]) == antes
        # Control: con el plan de ahora, sí.
        with soporte.begin() as c:
            c.execute(text("select plataforma.privacidad_ejecutar_supresion(:k,"
                           " cast(:p as jsonb), 'a', 'b')"),
                      {"k": k, "p": json.dumps(plan_de(k))})  # fmt: skip
        assert caso(motor_migrate, k)["estado"] == "resuelto"
    finally:
        soporte.dispose()


def test_un_caso_resuelto_no_se_suprime_de_nuevo(confiable, motor_migrate, con_url, capsys):  # noqa: F811
    k = confirmado(capsys, motor_migrate, confiable["correo"])
    assert aplicar(capsys, k)[0] == 0
    r, _, err = aplicar(capsys, k)
    assert r == 1 and "caso_cerrado" in err


# Rectificación del nombre --------------------------------------------------------------------


def rectificar(capsys, k, nombre) -> tuple[int, str]:
    r = privacidad.main(["rectificar", "--caso", k], entrada=io.StringIO(nombre + "\n"))
    return r, capsys.readouterr().err


def test_rectificar_el_nombre_de_la_cuenta_sin_guardarlo_en_el_caso(
    confiable,  # noqa: F811
    motor_migrate,
    con_url,  # noqa: F811
    capsys,
):
    k = confirmado(capsys, motor_migrate, confiable["correo"], "rectificacion_nombre")
    r, err = rectificar(capsys, k, "  ")
    assert r == 1 and "falta el nombre nuevo" in err
    r, err = rectificar(capsys, k, "N" * 101)
    assert r == 1 and "nombre_invalido" in err
    r, err = rectificar(capsys, k, "  Nombre Corregido  ")
    assert r == 0, err
    assert uno(motor_migrate, "select nombre from nucleo.persona where id = :p",
               p=confiable["inq"]).nombre == "Nombre Corregido"  # fmt: skip
    fila = caso(motor_migrate, k)
    assert fila["estado"] == "resuelto"
    assert fila["resolucion"] == {"rectificado": "persona.nombre", "filas": 1}
    assert "Corregido" not in json.dumps(fila["resolucion"])


def test_rectificar_en_un_consorcio_sin_nombre_declarado_se_niega(
    confiable,  # noqa: F811
    motor_migrate,
    con_url,  # noqa: F811
    capsys,
):
    """El inquilino entró por invitación: no tiene `nombre_declarado` (es de la importación)."""
    k = confirmado(capsys, motor_migrate, confiable["correo"], "rectificacion_nombre",
                   "--consorcio", str(confiable["cons"]))  # fmt: skip
    nombre = uno(motor_migrate, "select nombre from nucleo.persona where id = :p",
                 p=confiable["inq"]).nombre  # fmt: skip
    r, err = rectificar(capsys, k, "Nombre Corregido")
    assert r == 1 and "nada_que_rectificar" in err
    # Lo global no se tocó y el caso sigue abierto para resolverlo de otro modo.
    assert uno(motor_migrate, "select nombre from nucleo.persona where id = :p",
               p=confiable["inq"]).nombre == nombre  # fmt: skip
    assert caso(motor_migrate, k)["estado"] == "confirmado"


def test_rectificar_en_un_consorcio_cambia_el_nombre_declarado(
    confiable,  # noqa: F811
    motor_migrate,
    con_url,  # noqa: F811
    capsys,
):
    """Como si el vínculo viniera de la importación del padrón (con su nombre declarado)."""
    with motor_migrate.begin() as c:
        imp = c.execute(text("insert into nucleo.importacion_padron (consorcio_id, archivo_sha256,"
                             " registrada_por) values (:c, :h, :a) returning id"),
                        {"c": confiable["cons"], "h": os.urandom(32),
                         "a": confiable["admin"]}).scalar_one()  # fmt: skip
        c.execute(text("update nucleo.vinculo set fuente = 'importacion', importacion_id = :i,"
                       " nombre_declarado = 'Mal Escrito' where id = :v"),
                  {"i": imp, "v": confiable["v_inq"]})  # fmt: skip
    k = confirmado(capsys, motor_migrate, confiable["correo"], "rectificacion_nombre",
                   "--consorcio", str(confiable["cons"]))  # fmt: skip
    r, err = rectificar(capsys, k, "Bien Escrito")
    assert r == 0, err
    assert uno(motor_migrate, "select nombre_declarado from nucleo.vinculo where id = :v",
               v=confiable["v_inq"]).nombre_declarado == "Bien Escrito"  # fmt: skip
    assert caso(motor_migrate, k)["resolucion"] == {
        "rectificado": "vinculo.nombre_declarado", "filas": 1,
    }  # fmt: skip


# Migración -----------------------------------------------------------------------------------


def test_0040_ida_y_vuelta(base_aparte):  # noqa: F811
    cfg, motor = base_aparte
    command.upgrade(cfg, "0040")

    def funcion() -> bool:
        with motor.connect() as c:
            return c.execute(
                text("select to_regprocedure('plataforma.privacidad_plan_supresion(uuid)')"
                     " is not null")
            ).scalar_one()  # fmt: skip

    assert funcion()
    command.downgrade(cfg, "0039")
    assert not funcion()
    command.upgrade(cfg, "0040")  # control: vuelve a subir limpio
    assert funcion()


# Controles de la revisión (Codex y revisión independiente de #140) ---------------------------


def otro_consorcio(motor_migrate, persona, correo):
    """Un segundo consorcio con una invitación pendiente al correo de la persona, un correo
    pendiente en su cola y un vínculo importado (con nombre declarado) de la persona."""
    cons2, u2, prop2 = consorcio_con_persona(motor_migrate)
    with motor_migrate.begin() as c:
        c.execute(text("insert into nucleo.invitacion (consorcio_id, unidad_id, tipo, correo,"
                       " nombre, token_hash, invitada_por, vence_en) values (:c, :u,"
                       " 'inquilino', :co, 'En el otro', :t, :i, now() + interval '1 day')"),
                  {"c": cons2, "u": u2, "co": correo, "t": os.urandom(32), "i": prop2})  # fmt: skip
        c.execute(text("insert into plataforma.jobs (tipo, consorcio_id, carga, clave_unica)"
                       " values ('notificar.correo', :c, jsonb_build_object('para',"
                       " cast(:co as text), 'asunto', 'x', 'texto', 'y'), :k)"),
                  {"c": cons2, "co": correo, "k": f"pytest-2d3-otro-{cons2}"})  # fmt: skip
        imp = c.execute(text("insert into nucleo.importacion_padron (consorcio_id, archivo_sha256,"
                             " registrada_por) values (:c, :h, :a) returning id"),
                        {"c": cons2, "h": os.urandom(32), "a": prop2}).scalar_one()  # fmt: skip
        c.execute(text("insert into nucleo.vinculo (consorcio_id, unidad_id, persona_id, tipo,"
                       " vigente_desde, fuente, origen, importacion_id, nombre_declarado)"
                       " values (:c, :u, :p, 'inquilino', current_date - 1, 'importacion',"
                       " 'soporte', :i, 'Nombre En El Otro')"),
                  {"c": cons2, "u": u2, "p": persona, "i": imp})  # fmt: skip
    return cons2


def lo_del_otro(motor_migrate, cons2) -> tuple:
    return tuple(uno(motor_migrate, "select (select count(*) from nucleo.invitacion where"
                     " consorcio_id = :c and nombre = 'En el otro'), (select count(*) from"
                     " plataforma.jobs where consorcio_id = :c), (select nombre_declarado from"
                     " nucleo.vinculo where consorcio_id = :c and nombre_declarado is not null)",
                     c=cons2))  # fmt: skip


def test_ejecutar_exige_la_huella_del_plan_revisado(confiable, motor_migrate, con_url, capsys):  # noqa: F811
    k = confirmado(capsys, motor_migrate, confiable["correo"])
    terminar_vinculo(motor_migrate, confiable["v_inq"], confiable["cons"])
    r, out, err = suprimir(capsys, k)
    assert r == 0, err
    vista = re.search(r"^huella: (\w+)$", out, re.M).group(1)
    # Entre la revisión y la ejecución aparecen accesos y credenciales nuevos: el plan cambia.
    accesos_y_credenciales(motor_migrate, confiable["inq"], confiable["cons"])
    r, _, err = suprimir(capsys, k, "--ejecutar", vista)
    assert r == 1 and "el plan cambió" in err
    assert caso(motor_migrate, k)["estado"] == "confirmado"
    assert 0 not in filas_globales(motor_migrate, confiable["inq"]).values()
    # Control: con la huella del plan de ahora, se aplica.
    assert aplicar(capsys, k)[0] == 0
    assert caso(motor_migrate, k)["estado"] == "resuelto"


def test_el_invitado_que_acepto_despues_de_confirmar_no_se_resuelve_por_su_correo(
    escena,  # noqa: F811
    motor_migrate,
    con_url,  # noqa: F811
    capsys,
):
    correo = f"pendiente-{os.urandom(4).hex()}@ejemplo.com"
    with motor_migrate.begin() as c:
        c.execute(text("insert into nucleo.invitacion (consorcio_id, unidad_id, tipo, correo,"
                       " nombre, token_hash, invitada_por, vence_en) values (:c, :u,"
                       " 'inquilino', :co, 'Pendiente', sha256(:t), :i, now() + interval '1 day')"),
                  {"c": escena["cons"], "u": escena["u1"], "co": correo, "t": os.urandom(32),
                   "i": escena["admin"]})  # fmt: skip
    k = abrir(capsys, "--correo", correo, "--derecho", "supresion")
    codigo = codigo_del_correo(motor_migrate, k)
    assert privacidad.main(["corroborar", "--caso", k, "--texto", "Nota de la administración"]) == 0
    capsys.readouterr()
    assert confirmar_caso(k, codigo, capsys)[0] == 0
    assert suprimir(capsys, k)[0] == 0  # control: antes de tener cuenta, hay plan
    with motor_migrate.begin() as c:
        c.execute(text("insert into nucleo.persona (correo, nombre) values (:c, 'Ya entró')"),
                  {"c": correo})  # fmt: skip
    r, _, err = suprimir(capsys, k)
    assert r == 1 and "titular_con_cuenta" in err


def test_con_la_persona_ya_suprimida_otro_caso_no_se_resuelve(
    confiable,  # noqa: F811
    motor_migrate,
    con_url,  # noqa: F811
    capsys,
):
    del_consorcio = confirmado(capsys, motor_migrate, confiable["correo"], "supresion",
                               "--consorcio", str(confiable["cons"]))  # fmt: skip
    assert suprimir(capsys, del_consorcio)[0] == 0  # control: hay plan
    cuenta_entera = confirmado(capsys, motor_migrate, confiable["correo"])
    terminar_vinculo(motor_migrate, confiable["v_inq"], confiable["cons"])
    assert aplicar(capsys, cuenta_entera)[0] == 0
    r, _, err = suprimir(capsys, del_consorcio)
    assert r == 1 and "titular_suprimido" in err


def test_un_caso_de_consorcio_no_toca_otro_consorcio_ni_los_correos_de_acceso(
    confiable,  # noqa: F811
    motor_migrate,
    con_url,  # noqa: F811
    capsys,
):
    cons2 = otro_consorcio(motor_migrate, confiable["inq"], confiable["correo"])
    with motor_migrate.begin() as c:
        c.execute(text("insert into plataforma.jobs (tipo, carga, clave_unica) values"
                       " ('notificar.correo', jsonb_build_object('para', cast(:co as text),"
                       " 'asunto', 'Entrar', 'texto', 'y'), :k)"),
                  {"co": confiable["correo"], "k": f"pytest-2d3-acceso-{cons2}"})  # fmt: skip
    otro = lo_del_otro(motor_migrate, cons2)
    assert otro == (1, 1, "Nombre En El Otro")  # control: hay qué tocar
    k = confirmado(capsys, motor_migrate, confiable["correo"], "supresion",
                   "--consorcio", str(confiable["cons"]))  # fmt: skip
    # El de la cuenta entera se confirma antes: después, la invitación aceptada queda anonimizada
    # y el correo ya no es confiable sin corroboración.
    entera = confirmado(capsys, motor_migrate, confiable["correo"])
    assert aplicar(capsys, k)[0] == 0
    assert lo_del_otro(motor_migrate, cons2) == otro
    assert cuenta(motor_migrate, "select count(*) from plataforma.jobs where clave_unica = :k",
                  k=f"pytest-2d3-acceso-{cons2}") == 1  # fmt: skip
    # Control: el caso de la cuenta entera sí toca la invitación, la cola del otro consorcio y
    # los correos de acceso.
    assert aplicar(capsys, entera)[0] == 0
    assert lo_del_otro(motor_migrate, cons2)[:2] == (0, 0)
    assert cuenta(motor_migrate, "select count(*) from plataforma.jobs where clave_unica = :k",
                  k=f"pytest-2d3-acceso-{cons2}") == 0  # fmt: skip


def test_los_eventos_del_outbox_sin_trabajo_se_borran(confiable, motor_migrate, con_url, capsys):  # noqa: F811
    with motor_migrate.begin() as c:
        for para in (confiable["correo"], "otra@ejemplo.com"):
            c.execute(text("insert into plataforma.outbox (evento, carga) values"
                           " ('correo.solicitado', jsonb_build_object('para', cast(:p as text),"
                           " 'asunto', 'Prueba 2d-3', 'texto', 'y'))"), {"p": para})  # fmt: skip
    k = confirmado(capsys, motor_migrate, confiable["correo"])
    r, out, err = suprimir(capsys, k)
    assert r == 0, err
    assert "eventos de correo sin trabajo en la cola (1)" in out
    assert aplicar(capsys, k)[0] == 0
    with motor_migrate.connect() as c:
        quedan = sorted(c.execute(text("select carga ->> 'para' from plataforma.outbox"
                                       " where carga ->> 'asunto' = 'Prueba 2d-3'")
                                  ).scalars())  # fmt: skip
    assert quedan == ["otra@ejemplo.com"]


def test_rectificar_en_un_consorcio_no_cambia_el_nombre_en_otro(
    confiable,  # noqa: F811
    motor_migrate,
    con_url,  # noqa: F811
    capsys,
):
    cons2 = otro_consorcio(motor_migrate, confiable["inq"], confiable["correo"])
    with motor_migrate.begin() as c:
        imp = c.execute(text("insert into nucleo.importacion_padron (consorcio_id, archivo_sha256,"
                             " registrada_por) values (:c, :h, :a) returning id"),
                        {"c": confiable["cons"], "h": os.urandom(32),
                         "a": confiable["admin"]}).scalar_one()  # fmt: skip
        c.execute(text("update nucleo.vinculo set fuente = 'importacion', importacion_id = :i,"
                       " nombre_declarado = 'Mal Escrito' where id = :v"),
                  {"i": imp, "v": confiable["v_inq"]})  # fmt: skip
    k = confirmado(capsys, motor_migrate, confiable["correo"], "rectificacion_nombre",
                   "--consorcio", str(confiable["cons"]))  # fmt: skip
    assert rectificar(capsys, k, "Bien Escrito")[0] == 0
    assert lo_del_otro(motor_migrate, cons2)[2] == "Nombre En El Otro"
    # Control: en el consorcio del caso sí cambió.
    assert uno(motor_migrate, "select nombre_declarado from nucleo.vinculo where id = :v",
               v=confiable["v_inq"]).nombre_declarado == "Bien Escrito"  # fmt: skip


def test_bloquea_la_invitacion_antes_que_la_persona(confiable, motor_migrate, con_url, capsys):  # noqa: F811
    """Orden único (0025): invitación y después `persona:<correo>`, como `aceptar_invitacion`.
    Con el cerrojo de la persona tomado por otro, la supresión espera ya dueña de la invitación;
    al revés, una aceptación a la vez se trabaría con ella (Codex en #140)."""
    k = confirmado(capsys, motor_migrate, confiable["correo"])
    clave = "select pg_advisory_xact_lock(hashtextextended('persona:' || cast(:c as text), 0))"
    resultado = {}

    def suprimir_en_paralelo():
        resultado["plan"] = plan_de(k)

    with motor_migrate.connect() as otro:
        otro.execute(text(clave), {"c": confiable["correo"]})
        hilo = threading.Thread(target=suprimir_en_paralelo)
        hilo.start()
        try:
            with motor_migrate.connect() as c:
                for _ in range(ESPERAS):
                    esperando = c.execute(text(
                        "select count(*) from pg_locks where locktype = 'advisory'"
                        " and not granted")).scalar_one()  # fmt: skip
                    if esperando:
                        break
                    time.sleep(INTERVALO)
                assert esperando == 1  # control: la supresión quedó esperando a la persona
                with pytest.raises(DBAPIError, match="could not obtain lock"):
                    c.execute(text("select 1 from nucleo.invitacion where persona_id = :p"
                                   " for update nowait"), {"p": confiable["inq"]})  # fmt: skip
        finally:
            otro.rollback()
            hilo.join()
    assert punto(resultado["plan"], "persona: correo y nombre")["tratamiento"] == "conservar"


def test_un_login_a_mitad_de_camino_no_se_traba_con_la_supresion(
    confiable,  # noqa: F811
    motor_migrate,
    con_url,  # noqa: F811
    capsys,
):
    """`canjear_codigo` tiene el código y el enlace y después inserta la sesión (clave foránea a la
    persona). La supresión espera por el código sin haber tomado la persona: el login termina
    (Codex en #140)."""
    k = confirmado(capsys, motor_migrate, confiable["correo"])
    accesos_y_credenciales(motor_migrate, confiable["inq"], confiable["cons"])
    resultado = {}

    def suprimir_en_paralelo():
        resultado["plan"] = plan_de(k)

    with motor_migrate.connect() as login:
        login.execute(text("select 1 from nucleo.codigo_acceso where persona_id = :p"
                           " for update"), {"p": confiable["inq"]})  # fmt: skip
        hilo = threading.Thread(target=suprimir_en_paralelo)
        hilo.start()
        try:
            with motor_migrate.connect() as c:
                for _ in range(ESPERAS):
                    esperando = c.execute(text(
                        "select count(*) from pg_locks where locktype = 'transactionid'"
                        " and not granted")).scalar_one()  # fmt: skip
                    if esperando:
                        break
                    time.sleep(INTERVALO)
                assert esperando == 1  # control: la supresión quedó esperando el código
                # Lo que haría el login a continuación: la clave foránea de su sesión nueva.
                c.execute(text("select 1 from nucleo.persona where id = :p for key share nowait"),
                          {"p": confiable["inq"]})  # fmt: skip
        finally:
            login.rollback()
            hilo.join()
    assert punto(resultado["plan"], "persona: correo y nombre")["tratamiento"] == "conservar"
