"""Casos de privacidad de RF37 por soporte (H2d §4 y §7; 0039).

`scripts.privacidad` corre como `cem_soporte_privacidad`, que solo ejecuta las funciones de
`0039`. El código de confirmación va solo al correo del titular (el destino lo elige la base) y
el titular lo devuelve a soporte. Cada negación lleva su control: el mismo pedido que sí cumple
pasa.
"""

import io
import os
import re

import pytest
from alembic import command
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

import app.plataforma.tareas  # noqa: F401  (registra la ruta correo.solicitado → notificar.correo)
from app.plataforma import outbox
from scripts import privacidad
from tests.conftest import url_rol
from tests.test_mandatos_0012 import alta
from tests.test_modo_operacion import consorcio_con_persona
from tests.test_vinculo_0014 import (  # noqa: F401
    base_aparte,
    confirmar,
    escena,
    invitar_y_aceptar,
)

pytestmark = [pytest.mark.db, pytest.mark.redis]

REFERENCIA = "Correo a soporte del 27-10"


@pytest.fixture
def con_url(monkeypatch):
    monkeypatch.setenv(privacidad.VARIABLE_URL, url_rol("soporte_privacidad"))


def correo_de(motor_migrate, persona) -> str:
    with motor_migrate.connect() as c:
        return c.execute(
            text("select correo from nucleo.persona where id = :p"), {"p": persona}
        ).scalar_one()


def abrir(capsys, *args) -> str:
    """Abre un caso con el script y devuelve su id (lo que imprime el script)."""
    assert privacidad.main(["abrir", *args, "--origen", "titular",
                            "--referencia", REFERENCIA]) == 0  # fmt: skip
    salida = capsys.readouterr().out
    return re.search(r"^caso: (\S+)$", salida, re.M).group(1)


def codigo_del_correo(motor_migrate, caso) -> str:
    """El código como lo lee el titular en su correo (la carga del evento del outbox)."""
    with motor_migrate.connect() as c:
        texto = c.execute(
            text(
                "select o.carga ->> 'texto' from plataforma.outbox o"
                " join plataforma.caso_privacidad k on k.codigo_evento_id = o.id"
                " where k.id = :k"
            ),
            {"k": caso},
        ).scalar_one()
    return re.search(r"^([A-Z2-7]{5}(?:-[A-Z2-7]{1,5})+)$", texto, re.M).group(1)


def confirmar_caso(caso, codigo, capsys) -> tuple[int, str]:
    r = privacidad.main(["confirmar", "--caso", caso, "--como", "Respuesta por correo"],
                        entrada=io.StringIO(codigo + "\n"))  # fmt: skip
    return r, capsys.readouterr().err


def caso(motor_migrate, caso_id) -> dict:
    with motor_migrate.connect() as c:
        return dict(
            c.execute(text("select * from plataforma.caso_privacidad where id = :k"),
                      {"k": caso_id}).mappings().one()
        )  # fmt: skip


def eventos_totales(motor_migrate) -> int:
    with motor_migrate.connect() as c:
        return c.execute(text("select count(*) from auditoria.eventos")).scalar_one()


@pytest.fixture
def confiable(escena, motor_migrate, motor_app):  # noqa: F811
    """El inquilino aceptó una invitación a su correo y el Consejo confirmó su vínculo (quien lo
    registró, el administrador, no puede)."""
    confirmar(motor_app, escena["cons"], escena["mc"], escena["v_inq"])
    with motor_migrate.connect() as c:
        inq = c.execute(
            text("select persona_id from nucleo.vinculo where id = :v"), {"v": escena["v_inq"]}
        ).scalar_one()
    return escena | {"inq": inq, "correo": correo_de(motor_migrate, inq)}


# Permisos ------------------------------------------------------------------------------------


def test_cem_app_no_ve_los_casos_y_soporte_solo_ejecuta_las_funciones(motor_app):
    with motor_app.connect() as c:
        with pytest.raises(DBAPIError, match="permission denied"):
            c.execute(text("select count(*) from plataforma.caso_privacidad"))
    with motor_app.connect() as c:
        with pytest.raises(DBAPIError, match="permission denied"):
            c.execute(text("select * from plataforma.privacidad_ver(gen_random_uuid())"))
    soporte = create_engine(url_rol("soporte_privacidad"))
    try:
        with soporte.connect() as c:
            with pytest.raises(DBAPIError, match="permission denied"):
                c.execute(text("select count(*) from plataforma.caso_privacidad"))
        with soporte.connect() as c:
            with pytest.raises(DBAPIError, match="permission denied"):
                c.execute(text("select count(*) from nucleo.persona"))
        # Control: la función sí la ejecuta.
        with soporte.connect() as c:
            assert (
                c.execute(
                    text("select count(*) from plataforma.privacidad_ver(gen_random_uuid())")
                ).scalar_one()
                == 0
            )
    finally:
        soporte.dispose()


def test_el_script_se_niega_con_otro_rol(confiable, monkeypatch, capsys):
    monkeypatch.setenv(privacidad.VARIABLE_URL, url_rol("app"))
    assert privacidad.main(["abrir", "--correo", confiable["correo"], "--derecho", "acceso",
                            "--origen", "titular", "--referencia", REFERENCIA]) == 1  # fmt: skip
    assert "no como cem_soporte_privacidad" in capsys.readouterr().err


# Abrir y el código ---------------------------------------------------------------------------


def test_el_codigo_va_solo_al_correo_del_titular_y_no_se_imprime(
    confiable, motor_migrate, con_url, capsys
):
    k = abrir(capsys, "--correo", confiable["correo"].upper(), "--derecho", "supresion")
    fila = caso(motor_migrate, k)
    assert fila["persona_id"] == confiable["inq"] and fila["estado"] == "abierto"
    assert len(fila["codigo_hash"]) == 32 and fila["codigo_vence_en"] is not None
    with motor_migrate.connect() as c:
        carga = c.execute(
            text("select carga from plataforma.outbox where id = :e"),
            {"e": fila["codigo_evento_id"]},
        ).scalar_one()
    assert carga["para"] == confiable["correo"]
    codigo = codigo_del_correo(motor_migrate, k)
    assert privacidad.hash_codigo(codigo) == bytes(fila["codigo_hash"])
    # El script no lo mostró.
    privacidad.main(["ver", "--caso", k])
    assert codigo not in capsys.readouterr().out


def test_abrir_sin_persona_ni_invitacion_con_ese_correo_se_niega(motor_migrate, con_url, capsys):
    assert privacidad.main(["abrir", "--correo", "nadie@ejemplo.com", "--derecho", "acceso",
                            "--origen", "titular", "--referencia", REFERENCIA]) == 1  # fmt: skip
    assert "titular_inexistente" in capsys.readouterr().err


# Confirmar -----------------------------------------------------------------------------------


def test_confirma_con_su_codigo_y_borra_el_correo_sin_auditar(
    confiable, motor_migrate, con_url, capsys
):
    k = abrir(capsys, "--correo", confiable["correo"], "--derecho", "acceso")
    evento = caso(motor_migrate, k)["codigo_evento_id"]
    antes = eventos_totales(motor_migrate)
    r, err = confirmar_caso(k, codigo_del_correo(motor_migrate, k).lower().replace("-", " "),
                            capsys)  # fmt: skip
    assert r == 0, err
    fila = caso(motor_migrate, k)
    assert fila["estado"] == "confirmado" and fila["codigo_usado_en"] is not None
    with motor_migrate.connect() as c:
        assert c.execute(text("select count(*) from plataforma.outbox where id = :e"),
                         {"e": evento}).scalar_one() == 0  # fmt: skip
    assert eventos_totales(motor_migrate) == antes


@pytest.mark.parametrize("motivo", ["incorrecto", "vencido", "usado"])
def test_un_codigo_incorrecto_vencido_o_usado_no_confirma(
    confiable, motor_migrate, con_url, capsys, motivo
):
    k = abrir(capsys, "--correo", confiable["correo"], "--derecho", "acceso")
    codigo = codigo_del_correo(motor_migrate, k)
    if motivo == "incorrecto":
        r, err = confirmar_caso(k, privacidad.codigo_nuevo(), capsys)
        esperado = "codigo_incorrecto"
    elif motivo == "vencido":
        with motor_migrate.begin() as c:
            c.execute(text("update plataforma.caso_privacidad set codigo_vence_en = now()"
                           " - interval '1 second' where id = :k"), {"k": k})  # fmt: skip
        r, err = confirmar_caso(k, codigo, capsys)
        esperado = "codigo_vencido"
    else:
        assert confirmar_caso(k, codigo, capsys)[0] == 0
        r, err = confirmar_caso(k, codigo, capsys)
        esperado = "caso_no_abierto"
    assert r == 1 and esperado in err
    if motivo != "usado":
        assert caso(motor_migrate, k)["estado"] == "abierto"


@pytest.mark.parametrize("factor", ["contrasena", "totp"])
def test_con_contrasena_va_por_la_aplicacion_y_un_totp_sin_terminar_no_cuenta(
    confiable, motor_migrate, con_url, capsys, factor
):
    k = abrir(capsys, "--correo", confiable["correo"], "--derecho", "supresion")
    with motor_migrate.begin() as c:
        if factor == "contrasena":
            c.execute(
                text(
                    "insert into nucleo.contrasena (persona_id, hash)"
                    " values (:p, '$argon2id$v=19$prueba')"
                ),
                {"p": confiable["inq"]},
            )
        else:
            # Un alta sin terminar no cuenta (mismo criterio que `tiene_segundo_factor`)...
            c.execute(
                text(
                    "insert into nucleo.factor_totp (persona_id, secreto_cifrado) values (:p, :s)"
                ),
                {"p": confiable["inq"], "s": os.urandom(48)},
            )
    codigo = codigo_del_correo(motor_migrate, k)
    if factor == "totp":
        r, err = confirmar_caso(k, codigo, capsys)
        assert r == 0, err
        return
    r, err = confirmar_caso(k, codigo, capsys)
    assert r == 1 and "requiere_aplicacion" in err


def test_totp_dado_de_alta_tambien_va_por_la_aplicacion(confiable, motor_migrate, con_url, capsys):
    k = abrir(capsys, "--correo", confiable["correo"], "--derecho", "supresion")
    with motor_migrate.begin() as c:
        c.execute(
            text(
                "insert into nucleo.factor_totp (persona_id, secreto_cifrado, confirmado_en)"
                " values (:p, :s, now())"
            ),
            {"p": confiable["inq"], "s": os.urandom(48)},
        )
    r, err = confirmar_caso(k, codigo_del_correo(motor_migrate, k), capsys)
    assert r == 1 and "requiere_aplicacion" in err


# Correo confiable y corroboración ------------------------------------------------------------


def test_sin_invitacion_aceptada_hace_falta_la_corroboracion(
    escena,  # noqa: F811
    motor_migrate,
    motor_app,
    con_url,
    capsys,
):
    """La propietaria la cargó el equipo y nunca aceptó una invitación (tiene una pendiente a su
    correo). Aunque su vínculo esté confirmado por un tercero (P4) y haya entrado una vez (tiene
    sesión), su correo no es confiable por sí solo: falla por la invitación, no por el vínculo."""
    confirmar(motor_app, escena["cons"], escena["mc"], escena["v_prop"])
    correo = correo_de(motor_migrate, escena["prop"])
    with motor_migrate.begin() as c:
        # Una invitación a su correo que nunca aceptó tampoco alcanza.
        c.execute(
            text(
                "insert into nucleo.invitacion (consorcio_id, unidad_id, tipo, correo, nombre,"
                " token_hash, invitada_por, vence_en) values (:c, :u, 'inquilino', :co,"
                " 'Pendiente', sha256(:t), :i, now() + interval '1 day')"
            ),
            {"c": escena["cons"], "u": escena["u1"], "co": correo, "t": os.urandom(32),
             "i": escena["admin"]},
        )  # fmt: skip
        c.execute(
            text(
                "insert into nucleo.sesion (persona_id, token_hash, dispositivo, ip,"
                " vence_en) values (:p, :t, gen_random_uuid(), '127.0.0.1',"
                " now() + interval '1 day')"
            ),
            {"p": escena["prop"], "t": os.urandom(32)},
        )
    k = abrir(capsys, "--correo", correo_de(motor_migrate, escena["prop"]), "--derecho",
              "supresion", "--consorcio", str(escena["cons"]))  # fmt: skip
    codigo = codigo_del_correo(motor_migrate, k)
    r, err = confirmar_caso(k, codigo, capsys)
    assert r == 1 and "requiere_corroboracion" in err
    assert privacidad.main(["corroborar", "--caso", k, "--texto", "Nota del Consejo"]) == 0
    capsys.readouterr()
    r, err = confirmar_caso(k, codigo, capsys)
    assert r == 0, err


def test_sin_vinculo_confirmado_la_invitacion_aceptada_no_alcanza(
    escena,  # noqa: F811
    motor_migrate,
    con_url,
    capsys,
):
    """Control del fixture `confiable`: el mismo inquilino, sin la confirmación P4."""
    with motor_migrate.connect() as c:
        inq = c.execute(text("select persona_id from nucleo.vinculo where id = :v"),
                        {"v": escena["v_inq"]}).scalar_one()  # fmt: skip
    k = abrir(capsys, "--correo", correo_de(motor_migrate, inq), "--derecho", "acceso")
    r, err = confirmar_caso(k, codigo_del_correo(motor_migrate, k), capsys)
    assert r == 1 and "requiere_corroboracion" in err


def test_el_criterio_vale_en_el_consorcio_del_caso(confiable, motor_migrate, con_url, capsys):
    otro, _, _ = consorcio_con_persona(motor_migrate)
    k = abrir(capsys, "--correo", confiable["correo"], "--derecho", "acceso",
              "--consorcio", str(otro))  # fmt: skip
    r, err = confirmar_caso(k, codigo_del_correo(motor_migrate, k), capsys)
    assert r == 1 and "requiere_corroboracion" in err
    # Control: en su consorcio sí confirma sin corroboración.
    k2 = abrir(capsys, "--correo", confiable["correo"], "--derecho", "acceso",
               "--consorcio", str(confiable["cons"]))  # fmt: skip
    r, err = confirmar_caso(k2, codigo_del_correo(motor_migrate, k2), capsys)
    assert r == 0, err


def test_un_invitado_que_no_acepto_es_sujeto_por_correo_y_pide_corroboracion(
    escena,  # noqa: F811
    motor_migrate,
    con_url,
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
    fila = caso(motor_migrate, k)
    assert fila["persona_id"] is None and fila["sujeto_correo"] == correo
    codigo = codigo_del_correo(motor_migrate, k)
    r, err = confirmar_caso(k, codigo, capsys)
    assert r == 1 and "requiere_corroboracion" in err
    assert privacidad.main(["corroborar", "--caso", k, "--texto", "Nota de la administración"]) == 0
    capsys.readouterr()
    assert confirmar_caso(k, codigo, capsys)[0] == 0


def test_si_el_invitado_acepto_despues_el_caso_por_correo_no_vale(
    escena,  # noqa: F811
    motor_migrate,
    con_url,
    capsys,
):
    correo = f"acepta-{os.urandom(4).hex()}@ejemplo.com"
    with motor_migrate.begin() as c:
        c.execute(
            text(
                "insert into nucleo.invitacion (consorcio_id, unidad_id, tipo, correo, nombre,"
                " token_hash, invitada_por, vence_en) values (:c, :u, 'inquilino', :co,"
                " 'Pendiente', sha256(:t), :i, now() + interval '1 day')"
            ),
            {"c": escena["cons"], "u": escena["u1"], "co": correo, "t": os.urandom(32),
             "i": escena["admin"]},
        )  # fmt: skip
    k = abrir(capsys, "--correo", correo, "--derecho", "supresion")
    codigo = codigo_del_correo(motor_migrate, k)
    assert privacidad.main(["corroborar", "--caso", k, "--texto", "Nota de la administración"]) == 0
    capsys.readouterr()
    # Después de abrir el caso, el invitado tiene cuenta (aceptó y puso contraseña).
    with motor_migrate.begin() as c:
        p = c.execute(
            text("insert into nucleo.persona (correo, nombre) values (:c, 'Acepta') returning id"),
            {"c": correo},
        ).scalar_one()
        c.execute(
            text(
                "insert into nucleo.contrasena (persona_id, hash)"
                " values (:p, '$argon2id$v=19$prueba')"
            ),
            {"p": p},
        )
    r, err = confirmar_caso(k, codigo, capsys)
    assert r == 1 and "titular_con_cuenta" in err
    assert caso(motor_migrate, k)["estado"] == "abierto"


def test_sin_correo_es_un_caso_manual_que_no_se_confirma(motor_migrate, con_url, capsys):
    assert privacidad.main(["abrir", "--descripcion", "Mandatario externo de la nota 3/2026",
                            "--derecho", "acceso", "--origen", "administracion",
                            "--referencia", REFERENCIA]) == 0  # fmt: skip
    salida = capsys.readouterr().out
    assert "caso manual" in salida
    k = re.search(r"^caso: (\S+)$", salida, re.M).group(1)
    r, err = confirmar_caso(k, privacidad.codigo_nuevo(), capsys)
    assert r == 1 and "sin_codigo" in err


# Rechazar y cerrar ---------------------------------------------------------------------------


def test_rechazar_cierra_el_caso_y_borra_el_correo(confiable, motor_migrate, con_url, capsys):
    k = abrir(capsys, "--correo", confiable["correo"], "--derecho", "supresion")
    codigo = codigo_del_correo(motor_migrate, k)
    evento = caso(motor_migrate, k)["codigo_evento_id"]
    assert privacidad.main(["rechazar", "--caso", k, "--motivo", "El titular no lo pidió"]) == 0
    capsys.readouterr()
    fila = caso(motor_migrate, k)
    assert fila["estado"] == "rechazado" and fila["resolucion"] == {
        "rechazado": "El titular no lo pidió"
    }
    with motor_migrate.connect() as c:
        assert c.execute(text("select count(*) from plataforma.outbox where id = :e"),
                         {"e": evento}).scalar_one() == 0  # fmt: skip
    r, err = confirmar_caso(k, codigo, capsys)
    assert r == 1 and "caso_no_abierto" in err


def test_un_caso_cerrado_no_cambia_ni_aunque_escriba_el_duenio(
    confiable, motor_migrate, con_url, capsys
):
    k = abrir(capsys, "--correo", confiable["correo"], "--derecho", "acceso")
    with motor_migrate.begin() as c:
        with pytest.raises(DBAPIError, match="caso_inmutable"):
            c.execute(text("update plataforma.caso_privacidad set derecho = 'supresion'"
                           " where id = :k"), {"k": k})  # fmt: skip
    assert privacidad.main(["rechazar", "--caso", k, "--motivo", "Duplicado"]) == 0
    with motor_migrate.begin() as c:
        with pytest.raises(DBAPIError, match="caso_cerrado"):
            c.execute(text("update plataforma.caso_privacidad set confirmacion = 'x'"
                           " where id = :k"), {"k": k})  # fmt: skip


# Migración -----------------------------------------------------------------------------------


def test_0039_ida_y_vuelta_y_se_niega_con_un_caso(base_aparte):  # noqa: F811
    cfg, motor = base_aparte
    command.upgrade(cfg, "0039")

    def tabla() -> bool:
        with motor.connect() as c:
            return c.execute(
                text("select to_regclass('plataforma.caso_privacidad') is not null")
            ).scalar_one()

    assert tabla()
    command.downgrade(cfg, "0038")
    assert not tabla()
    command.upgrade(cfg, "0039")  # control: vuelve a subir limpio
    with motor.begin() as c:
        c.execute(text("insert into plataforma.caso_privacidad (sujeto_descripcion, derecho,"
                       " origen_pedido, referencia_pedido) values ('Sin cuenta', 'acceso',"
                       " 'titular', 'Prueba')"))  # fmt: skip
    with pytest.raises(DBAPIError, match="downgrade_con_datos"):
        command.downgrade(cfg, "0038")


def test_con_invitacion_aceptada_y_un_mandato_el_correo_es_confiable(
    escena,  # noqa: F811
    motor_migrate,
    motor_app,
    con_url,
    capsys,
):
    """La otra rama del criterio: sin vínculo confirmado, pero con un mandato en el consorcio (el
    del Consejo: la administración ya está ocupada)."""
    v = invitar_y_aceptar(motor_migrate, escena["cons"], escena["u1"], escena["admin"])
    with motor_migrate.connect() as c:
        p = c.execute(
            text("select persona_id from nucleo.vinculo where id = :v"), {"v": v}
        ).scalar_one()
    k = abrir(capsys, "--correo", correo_de(motor_migrate, p), "--derecho", "acceso")
    r, err = confirmar_caso(k, codigo_del_correo(motor_migrate, k), capsys)
    assert r == 1 and "requiere_corroboracion" in err  # control: sin el mandato no alcanza
    alta(motor_app, escena["cons"], None, escena["acta"], tipo="miembro_consejo", persona=p,
         origen="soporte", con_vinculo=False)  # fmt: skip
    k2 = abrir(capsys, "--correo", correo_de(motor_migrate, p), "--derecho", "acceso")
    r, err = confirmar_caso(k2, codigo_del_correo(motor_migrate, k2), capsys)
    assert r == 0, err


def despachar_todo(motor_app) -> None:
    """Como `cem-planificador`, con `cem_app`. Toma de a 100: quedan eventos de otras pruebas."""
    while outbox.despachar(motor_app):
        pass


def trabajo_del_correo(motor_migrate, evento) -> list[str]:
    with motor_migrate.connect() as c:
        return list(
            c.execute(
                text("select estado::text from plataforma.jobs where clave_unica = :k"),
                {"k": f"notificar.correo:{evento}"},
            ).scalars()
        )


@pytest.mark.parametrize("accion", ["confirmar", "rechazar"])
def test_al_cerrar_se_borran_el_evento_y_el_trabajo_del_correo(
    confiable, motor_migrate, motor_app, con_url, capsys, accion
):
    """Como en la realidad: el despachador ya convirtió el evento en el trabajo que mandó el
    correo (con el código en su carga)."""
    k = abrir(capsys, "--correo", confiable["correo"], "--derecho", "acceso")
    evento = caso(motor_migrate, k)["codigo_evento_id"]
    codigo = codigo_del_correo(motor_migrate, k)
    despachar_todo(motor_app)
    assert len(trabajo_del_correo(motor_migrate, evento)) == 1  # control: el trabajo existe
    if accion == "confirmar":
        assert confirmar_caso(k, codigo, capsys)[0] == 0
    else:
        assert privacidad.main(["rechazar", "--caso", k, "--motivo", "Duplicado"]) == 0
    assert trabajo_del_correo(motor_migrate, evento) == []
    with motor_migrate.connect() as c:
        assert c.execute(text("select count(*) from plataforma.outbox where id = :e"),
                         {"e": evento}).scalar_one() == 0  # fmt: skip


def test_un_trabajo_en_curso_no_se_borra(confiable, motor_migrate, motor_app, con_url, capsys):
    k = abrir(capsys, "--correo", confiable["correo"], "--derecho", "acceso")
    evento = caso(motor_migrate, k)["codigo_evento_id"]
    codigo = codigo_del_correo(motor_migrate, k)
    despachar_todo(motor_app)
    with motor_migrate.begin() as c:
        c.execute(text("update plataforma.jobs set estado = 'en_curso', bloqueado_por = 'pytest',"
                 " bloqueado_hasta = now() + interval '5 minutes', intentos = 1"
                 " where clave_unica = :k"),
                  {"k": f"notificar.correo:{evento}"})  # fmt: skip
    assert confirmar_caso(k, codigo, capsys)[0] == 0
    assert trabajo_del_correo(motor_migrate, evento) == ["en_curso"]
    with motor_migrate.connect() as c:
        # El evento queda mientras su trabajo exista: el trabajador lo está mandando.
        assert c.execute(text("select count(*) from plataforma.outbox where id = :e"),
                         {"e": evento}).scalar_one() == 1  # fmt: skip


def test_una_invitacion_aceptada_a_otro_correo_no_hace_confiable_el_suyo(
    confiable, motor_migrate, con_url, capsys
):
    """La invitación que aceptó se había mandado a otro correo: controlar ese otro no dice nada
    del correo de su cuenta."""
    with motor_migrate.begin() as c:
        c.execute(
            text("update nucleo.invitacion set correo = :o where persona_id = :p"),
            {"o": f"otro-{os.urandom(4).hex()}@ejemplo.com", "p": confiable["inq"]},
        )
    k = abrir(capsys, "--correo", confiable["correo"], "--derecho", "acceso")
    r, err = confirmar_caso(k, codigo_del_correo(motor_migrate, k), capsys)
    assert r == 1 and "requiere_corroboracion" in err
