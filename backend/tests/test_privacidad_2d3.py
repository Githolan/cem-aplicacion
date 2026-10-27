"""Supresión y rectificación del nombre por soporte (H2d §2, §6 y §10; 0040).

Cada caso parte de uno confirmado por el titular con el código de su correo (2d-1). Cada negación
lleva su control: el mismo pedido que sí cumple pasa.
"""

import io
import json
import os
from datetime import timedelta

import pytest
from alembic import command
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from scripts import privacidad
from tests.conftest import url_rol
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


def uno(motor_migrate, sql, **p):
    with motor_migrate.connect() as c:
        return c.execute(text(sql), p).one()


def cuenta(motor_migrate, sql, **p) -> int:
    with motor_migrate.connect() as c:
        return c.execute(text(sql), p).scalar_one()


def accesos_y_credenciales(motor_migrate, persona) -> None:
    """Una sesión abierta, una contraseña y una respuesta de idempotencia, cargadas después de
    confirmar el caso (con contraseña, el caso no se confirma por correo)."""
    with motor_migrate.begin() as c:
        c.execute(text("insert into nucleo.sesion (persona_id, token_hash, dispositivo, ip,"
                       " vence_en) values (:p, :t, gen_random_uuid(), '203.0.113.9',"
                       " now() + interval '1 day')"),
                  {"p": persona, "t": os.urandom(32)})  # fmt: skip
        c.execute(text("insert into nucleo.contrasena (persona_id, hash)"
                       " values (:p, '$argon2id$v=19$prueba')"), {"p": persona})  # fmt: skip


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
    accesos_y_credenciales(motor_migrate, confiable["inq"])
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
    accesos_y_credenciales(motor_migrate, confiable["inq"])
    eventos = cuenta(motor_migrate, "select count(*) from auditoria.eventos")
    r, out, err = suprimir(capsys, k, "--ejecutar")
    assert r == 0, err
    persona = uno(motor_migrate, "select correo, nombre, eliminado_en from nucleo.persona"
                  " where id = :p", p=confiable["inq"])  # fmt: skip
    assert persona.correo == confiable["correo"] and persona.eliminado_en is None
    assert cuenta(motor_migrate, "select count(*) from nucleo.contrasena where persona_id = :p",
                  p=confiable["inq"]) == 1  # fmt: skip
    # Las sesiones sí se borran (alcance cuenta), y la auditoría no se toca.
    assert cuenta(motor_migrate, "select count(*) from nucleo.sesion where persona_id = :p",
                  p=confiable["inq"]) == 0  # fmt: skip
    assert cuenta(motor_migrate, "select count(*) from auditoria.eventos") == eventos
    fila = caso(motor_migrate, k)
    assert fila["estado"] == "resuelto"
    plan = fila["resolucion"]["supresion"]
    assert punto(plan, "persona: correo y nombre")["tratamiento"] == "conservar"
    assert punto(plan, "contraseña, TOTP y códigos de recuperación")["tratamiento"] == "conservar"


def test_sin_vinculo_ni_mandato_vigente_se_anonimiza_y_libera_el_correo(
    confiable,  # noqa: F811
    motor_migrate,
    con_url,  # noqa: F811
    capsys,
):
    k = confirmado(capsys, motor_migrate, confiable["correo"])
    terminar_vinculo(motor_migrate, confiable["v_inq"], confiable["cons"])
    accesos_y_credenciales(motor_migrate, confiable["inq"])
    eventos = cuenta(motor_migrate, "select count(*) from auditoria.eventos")
    r, out, err = suprimir(capsys, k, "--ejecutar")
    assert r == 0, err
    persona = uno(motor_migrate, "select id, correo, nombre, eliminado_en from nucleo.persona"
                  " where id = :p", p=confiable["inq"])  # fmt: skip
    assert persona.correo == f"suprimida+{persona.id}@invalid.cem"
    assert persona.nombre == "Persona suprimida" and persona.eliminado_en is not None
    for tabla in ("contrasena", "sesion"):
        assert cuenta(motor_migrate, f"select count(*) from nucleo.{tabla} where persona_id = :p",
                      p=confiable["inq"]) == 0  # fmt: skip
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
    accesos_y_credenciales(motor_migrate, confiable["inq"])
    globales = "select (select count(*) from nucleo.contrasena where persona_id = :p),"
    globales += " (select count(*) from nucleo.sesion where persona_id = :p)"
    antes = uno(motor_migrate, globales, p=confiable["inq"])
    assert antes[0] == 1 and antes[1] >= 1  # control: hay qué tocar
    r, _, err = suprimir(capsys, k, "--ejecutar")
    assert r == 0, err
    persona = uno(motor_migrate, "select correo, eliminado_en from nucleo.persona where id = :p",
                  p=confiable["inq"])  # fmt: skip
    assert persona.correo == confiable["correo"] and persona.eliminado_en is None
    assert uno(motor_migrate, globales, p=confiable["inq"]) == antes
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
    r, _, err = suprimir(capsys, k, "--ejecutar")
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
    assert suprimir(capsys, k, "--ejecutar")[0] == 0
    with motor_migrate.connect() as c:
        quedan = sorted(c.execute(text("select estado::text from plataforma.jobs"
                                       " where clave_unica like :k"),
                                  {"k": f"pytest-2d3-%-{k}"}).scalars())  # fmt: skip
    assert quedan == ["en_curso", "pendiente"]  # el en curso suyo y el pendiente de la otra


# La ejecución contra el plan visto -----------------------------------------------------------


def test_si_el_plan_cambio_no_se_aplica_nada(confiable, motor_migrate, con_url, capsys):  # noqa: F811
    k = confirmado(capsys, motor_migrate, confiable["correo"])
    plan = plan_de(k)
    accesos_y_credenciales(motor_migrate, confiable["inq"])  # una sesión más: el plan cambia
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
    assert suprimir(capsys, k, "--ejecutar")[0] == 0
    r, _, err = suprimir(capsys, k, "--ejecutar")
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
