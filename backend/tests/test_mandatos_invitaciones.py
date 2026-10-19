"""A7: invitación de mandato con cuatro ojos y desasociación (H2b 2b-4, PR 4; migración 0015).

Diseño: `doc/diseno/H2b_4_Diseno.md` de CEM, §7, §10 y §17 (casos 1 a 10 sexies, 27 a 30).
"""

import re
import threading
from datetime import timedelta
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, ProgrammingError

from app.db import transaccion
from scripts import mandato_soporte
from tests.conftest import clave_idempotencia, url_rol
from tests.test_contexto_mandatos import cliente, denegaciones, otra_unidad
from tests.test_identidad import (  # noqa: F401  (`app` es un fixture)
    app,
    csrf,
    navegador,
)
from tests.test_identidad_cierre_y_purga import entrar_como
from tests.test_mandatos import otra_persona, sesiones_abiertas
from tests.test_mandatos_0012 import CUIT_X, alta, baja, externo
from tests.test_modo_operacion import consorcio_con_persona, decidir_equipo
from tests.test_vinculo_0014 import _catalogo, base_aparte  # noqa: F401  (fixture)

pytestmark = [pytest.mark.db, pytest.mark.redis]

REFERENCIA = "Contrato de administración del 01-10-2026, cláusula 12"
ACEPTAR = "/api/v1/auth/invitacion-mandato/aceptar"
CONSULTAR = "/api/v1/auth/invitacion-mandato/consultar"
TOKEN = re.compile(r"/invitacion-mandato#token=([A-Za-z0-9_-]{43})")


def correo_nuevo() -> str:
    return f"mandatario-{uuid4().hex[:10]}@ejemplo.com"


@pytest.fixture
def escena(motor_migrate, motor_app):
    """Consorcio con su propietaria, dos miembros del Consejo (con `mandatos.registrar`) y un
    administrador externo vigente sin cuenta."""
    cons, u1, prop = consorcio_con_persona(motor_migrate)
    acta = decidir_equipo(motor_app, cons)
    mc1, mc2 = otra_persona(motor_migrate, cons), otra_persona(motor_migrate, cons)
    m_mc1 = alta(motor_app, cons, prop, acta, persona=mc1, tipo="miembro_consejo")
    alta(motor_app, cons, prop, acta, persona=mc2, tipo="miembro_consejo")
    ae = externo(motor_app, cons, prop, acta)
    return {"cons": cons, "u1": u1, "prop": prop, "acta": acta, "mc1": mc1, "mc2": mc2,
            "m_mc1": m_mc1, "ae": ae}  # fmt: skip


def url_mandato(cons, m, resto="") -> str:
    return f"/api/v1/consorcios/{cons}/mandatos/{m}{resto}"


def url_invitacion(cons, i, accion) -> str:
    return f"/api/v1/consorcios/{cons}/invitaciones-mandato/{i}/{accion}"


def emitir(persona, cons, m, correo, referencia=REFERENCIA):
    with cliente(persona) as c:
        return c.post(
            url_mandato(cons, m, "/invitaciones"),
            json={"correo": correo, "referencia": referencia},
            headers=clave_idempotencia(),
        )


def aprobar(persona, cons, i, clave=None):
    with cliente(persona) as c:
        return c.post(url_invitacion(cons, i, "aprobar"), headers=clave or clave_idempotencia())


def listar(persona, cons, m):
    with cliente(persona) as c:
        return c.get(url_mandato(cons, m, "/invitaciones"))


def emitida_y_aprobada(e, correo, mandato=None) -> tuple[str, str]:
    """Emite el Consejo 1, aprueba el Consejo 2; devuelve la invitación y el token del correo."""
    r = emitir(e["mc1"], e["cons"], mandato or e["ae"], correo)
    assert r.status_code == 201, r.text
    i = r.json()["id"]
    assert aprobar(e["mc2"], e["cons"], i).status_code == 200
    return i, token_de(correo)


_MOTOR = {}


def token_de(correo) -> str:
    with _MOTOR["m"].connect() as c:
        texto = c.execute(
            text(
                "select carga->>'texto' from plataforma.outbox where carga->>'para' = :p"
                " order by id desc limit 1"
            ),
            {"p": correo},
        ).scalar_one()
    [token] = TOKEN.findall(texto)
    return token


@pytest.fixture(autouse=True)
def _motor(motor_migrate):
    _MOTOR["m"] = motor_migrate


def mandato(motor_migrate, m) -> dict:
    with motor_migrate.connect() as c:
        return dict(
            c.execute(
                text(
                    "select persona_id, mandatario_cuit, vigente_desde, vigente_hasta, causa_baja"
                    " from nucleo.mandato where id = :m"
                ),
                {"m": m},
            )
            .mappings()
            .one()
        )


def eventos(motor_migrate, cons, prefijo="consorcio.mandato.") -> list[dict]:
    with motor_migrate.connect() as c:
        return [
            dict(f._mapping)
            for f in c.execute(
                text(
                    "select accion, actor_persona_id, origen, antes, despues, e::text as todo"
                    " from auditoria.eventos e where consorcio_id = :c and accion like :p"
                    " order by secuencia"
                ),
                {"c": cons, "p": prefijo + "%"},
            )
        ]


def persona_por_correo(motor_migrate, correo):
    with motor_migrate.connect() as c:
        return c.execute(
            text("select id from nucleo.persona where correo = :c"), {"c": correo}
        ).scalar_one_or_none()


def consorcios_de(persona) -> list[str]:
    with cliente(persona) as c:
        return [x["id"] for x in c.get("/api/v1/me/consorcios").json()]


def aceptar(navegador_, token, **cuerpo):
    return navegador_.post(ACEPTAR, json={"token": token, **cuerpo})


# Flujo completo ------------------------------------------------------------------------------


def test_cuatro_ojos_y_cuenta_nueva_asocian_el_mandato(app, motor_migrate, escena):  # noqa: F811
    e = escena
    correo = correo_nuevo()
    r = emitir(e["mc1"], e["cons"], e["ae"], correo)
    assert r.status_code == 201, r.text
    assert r.json()["estado"] == "por_aprobar"
    i = r.json()["id"]
    with motor_migrate.connect() as c:  # todavía no sale ningún correo
        assert (
            c.execute(
                text("select count(*) from plataforma.outbox where carga->>'para' = :p"),
                {"p": correo},
            ).scalar_one()
            == 0
        )
    [fila] = listar(e["mc2"], e["cons"], e["ae"]).json()["elementos"]
    assert (fila["estado"], fila["correo"]) == ("por_aprobar", correo)
    assert fila["referencia"] == REFERENCIA
    aprobada = aprobar(e["mc2"], e["cons"], i)
    assert aprobada.status_code == 200, aprobada.text
    assert aprobada.json()["estado"] == "pendiente"
    token = token_de(correo)

    with motor_migrate.connect() as c:
        texto = c.execute(
            text("select carga->>'texto' from plataforma.outbox where carga->>'para' = :p"),
            {"p": correo},
        ).scalar_one()
    assert CUIT_X not in texto and REFERENCIA not in texto and "Estudio X" in texto
    [fila] = listar(e["mc2"], e["cons"], e["ae"]).json()["elementos"]
    assert (fila["estado"], fila["correo"], fila["referencia"]) == ("pendiente", None, None)

    beto = navegador(app)
    vista = beto.post(CONSULTAR, json={"token": token})
    assert vista.status_code == 200, vista.text
    assert vista.json()["mandatario"] == "Estudio X" and CUIT_X not in vista.text
    r = aceptar(beto, token, nombre="Beto Estudio")
    assert r.status_code == 200, r.text
    assert r.json()["cuenta_nueva"] is True and r.json()["mandato_id"] == str(e["ae"])
    nueva = persona_por_correo(motor_migrate, correo)
    m = mandato(motor_migrate, e["ae"])
    assert (m["persona_id"], m["mandatario_cuit"]) == (nueva, CUIT_X)
    assert str(e["cons"]) in consorcios_de(nueva)  # C4, desde el pedido siguiente

    acciones = [(x["accion"], x["actor_persona_id"]) for x in eventos(motor_migrate, e["cons"])
                if x["accion"] not in ("consorcio.mandato.alta",)]  # fmt: skip
    assert acciones == [
        ("consorcio.mandato.invitar_persona", e["mc1"]),
        ("consorcio.mandato.aprobar_invitacion", e["mc2"]),
        ("consorcio.mandato.asociar_persona", nueva),
    ]
    todo = " ".join(x["todo"] for x in eventos(motor_migrate, e["cons"]))
    assert correo not in todo and REFERENCIA not in todo


def test_quien_emitio_no_aprueba(motor_migrate, motor_app, escena):
    e = escena
    i = emitir(e["mc1"], e["cons"], e["ae"], correo_nuevo()).json()["id"]
    r = aprobar(e["mc1"], e["cons"], i)
    assert (r.status_code, r.json()["codigo"]) == (403, "sin_permiso")
    [d] = denegaciones(motor_app, e["cons"], e["mc2"], e["mc1"])
    assert (d["accion"], d["motivo"]) == ("mandatos.aprobar_asociacion", "no_es_quien_emitio")
    # La base también lo impide (función y `check`).
    with (
        pytest.raises(DBAPIError) as error,
        transaccion(motor_app, persona_id=e["mc1"], consorcio_id=e["cons"]) as c,
    ):
        c.execute(
            text("select * from nucleo.aprobar_invitacion_mandato(:i, :h, now() + interval"
                 " '1 day', :a, 'api')"),
            {"i": i, "h": bytes(32), "a": e["mc1"]},
        )  # fmt: skip
    assert error.value.orig.diag.message_hint == "aprobacion_de_si_mismo"
    with pytest.raises(DBAPIError, match="invitacion_mandato_aprobador_no_emisor"):
        with motor_migrate.begin() as c:
            c.execute(
                text("update nucleo.invitacion_mandato set aprobada_por = emitida_por,"
                     " aprobada_en = now(), token_hash = :h, vence_en = now() + interval"
                     " '1 day' where id = :i"),
                {"i": i, "h": bytes(32)},
            )  # fmt: skip


def test_no_se_aprueba_una_invitacion_reemplazada(escena):
    """Caso 27: si quien emitió reemite mientras el aprobador mira, lo que vio ya no vale."""
    e = escena
    vista = emitir(e["mc1"], e["cons"], e["ae"], correo_nuevo()).json()["id"]
    otra = emitir(e["mc1"], e["cons"], e["ae"], correo_nuevo()).json()["id"]
    r = aprobar(e["mc2"], e["cons"], vista)
    assert (r.status_code, r.json()["codigo"]) == (404, "invitacion_invalida")
    filas = listar(e["mc2"], e["cons"], e["ae"]).json()["elementos"]
    estados = {x["id"]: x["estado"] for x in filas}
    assert estados == {vista: "revocada", otra: "por_aprobar"}
    assert aprobar(e["mc2"], e["cons"], otra).status_code == 200  # control


def test_aprobar_con_la_misma_clave_no_manda_otro_correo(motor_migrate, escena):
    e = escena
    correo = correo_nuevo()
    i = emitir(e["mc1"], e["cons"], e["ae"], correo).json()["id"]
    clave = clave_idempotencia()
    primera = aprobar(e["mc2"], e["cons"], i, clave)
    segunda = aprobar(e["mc2"], e["cons"], i, clave)
    assert primera.status_code == segunda.status_code == 200
    assert segunda.headers["Idempotent-Replayed"] == "true"
    with motor_migrate.connect() as c:
        assert (
            c.execute(
                text("select count(*) from plataforma.outbox where carga->>'para' = :p"),
                {"p": correo},
            ).scalar_one()
            == 1
        )
    otra = aprobar(e["mc2"], e["cons"], i)  # otra clave: ya está aprobada (diseño §12.1)
    assert (otra.status_code, otra.json()["codigo"]) == (409, "invitacion_no_pendiente")


# Qué ve cada uno ----------------------------------------------------------------------------


def test_la_lectura_es_de_quien_tiene_mandatos_registrar(motor_app, escena):
    e = escena
    emitir(e["mc1"], e["cons"], e["ae"], correo_nuevo())
    r = listar(e["prop"], e["cons"], e["ae"])
    assert (r.status_code, r.json()["codigo"]) == (403, "sin_permiso")
    [d] = denegaciones(motor_app, e["cons"], e["mc1"], e["prop"])
    assert d["accion"] == "mandatos.ver_asociaciones"
    assert listar(e["mc1"], e["cons"], e["ae"]).json()["elementos"][0]["emitida_por_mi"] is True
    assert listar(e["mc2"], e["cons"], uuid4()).status_code == 404


def test_cem_app_no_lee_correo_referencia_ni_motivo_ni_escribe(escena, motor_app):
    e = escena
    emitir(e["mc1"], e["cons"], e["ae"], correo_nuevo())
    for columna in ("correo", "referencia", "motivo_soporte", "token_hash"):
        with (
            pytest.raises(ProgrammingError, match="permission denied"),
            transaccion(motor_app, persona_id=e["mc1"], consorcio_id=e["cons"]) as c,
        ):
            c.execute(text(f"select {columna} from nucleo.invitacion_mandato"))
    for sql in (
        "update nucleo.invitacion_mandato set revocada_en = now()",
        "delete from nucleo.invitacion_mandato",
        "select referencia from nucleo.desasociacion_mandato",
        "update nucleo.mandato set persona_id = null",
    ):
        with (
            pytest.raises(ProgrammingError, match="permission denied"),
            transaccion(motor_app, persona_id=e["mc1"], consorcio_id=e["cons"]) as c,
        ):
            c.execute(text(sql))
    with transaccion(motor_app, persona_id=e["mc1"], consorcio_id=e["cons"]) as c:  # control
        assert c.execute(text("select count(*) from nucleo.invitacion_mandato")).scalar_one() == 1


# Qué mandatos admiten una cuenta ------------------------------------------------------------


def test_solo_un_externo_sin_cuenta_vigente_o_futuro_admite_la_invitacion(
    motor_migrate, motor_app, escena
):
    e = escena
    r = emitir(e["mc1"], e["cons"], e["m_mc1"], correo_nuevo())
    assert (r.status_code, r.json()["codigo"]) == (409, "mandato_no_asociable")
    assert emitir(e["mc1"], e["cons"], uuid4(), correo_nuevo()).status_code == 404
    otro_cons, _, otra_prop = consorcio_con_persona(motor_migrate)
    ajeno = externo(motor_app, otro_cons, otra_prop, decidir_equipo(motor_app, otro_cons))
    r = emitir(e["mc1"], e["cons"], ajeno, correo_nuevo())  # de otro consorcio: como inexistente
    assert (r.status_code, r.json()["codigo"]) == (404, "no_encontrado")
    baja(motor_app, e["cons"], e["mc1"], e["ae"], "error_carga", instrumento="Nota",
         descripcion="Error")  # fmt: skip
    r = emitir(e["mc1"], e["cons"], e["ae"], correo_nuevo())
    assert (r.status_code, r.json()["codigo"]) == (409, "mandato_no_vigente")


def test_un_mandato_vencido_no_admite_la_invitacion(motor_migrate, motor_app):
    cons, _, prop = consorcio_con_persona(motor_migrate)
    acta = decidir_equipo(motor_app, cons)
    mc = otra_persona(motor_migrate, cons)
    alta(motor_app, cons, prop, acta, persona=mc, tipo="miembro_consejo")
    vencido = externo(motor_app, cons, prop, acta)
    with motor_migrate.begin() as c:
        c.execute(
            text("update nucleo.mandato set vigente_hasta = '2026-02-01' where id = :m"),
            {"m": vencido},
        )
    r = emitir(mc, cons, vencido, correo_nuevo())
    assert (r.status_code, r.json()["codigo"]) == (409, "mandato_no_vigente")


def test_un_mandato_ya_asociado_no_admite_otra(app, motor_migrate, escena):  # noqa: F811
    e = escena
    correo = correo_nuevo()
    _, token = emitida_y_aprobada(e, correo)
    assert aceptar(navegador(app), token).status_code == 200
    r = emitir(e["mc1"], e["cons"], e["ae"], correo_nuevo())
    assert (r.status_code, r.json()["codigo"]) == (409, "mandato_ya_asociado")


def test_un_mandato_futuro_se_asocia_y_aparece_en_c4_desde_su_inicio(
    app,  # noqa: F811
    motor_migrate,
    motor_app,
):
    """Caso 1 bis."""
    cons, _, prop = consorcio_con_persona(motor_migrate)
    acta = decidir_equipo(motor_app, cons)
    mc1, mc2 = otra_persona(motor_migrate, cons), otra_persona(motor_migrate, cons)
    alta(motor_app, cons, prop, acta, persona=mc1, tipo="miembro_consejo")
    alta(motor_app, cons, prop, acta, persona=mc2, tipo="miembro_consejo")
    with motor_migrate.connect() as c:
        hoy = c.execute(text("select nucleo.hoy_del_consorcio(:c)"), {"c": cons}).scalar_one()
    futuro = externo(motor_app, cons, prop, acta, desde=hoy + timedelta(days=30))
    e = {"cons": cons, "mc1": mc1, "mc2": mc2}
    correo = correo_nuevo()
    _, token = emitida_y_aprobada(e, correo, mandato=futuro)
    r = aceptar(navegador(app), token)
    assert r.status_code == 200, r.text
    assert r.json()["vigente_desde"] == str(hoy + timedelta(days=30))
    persona = persona_por_correo(motor_migrate, correo)
    assert str(cons) not in consorcios_de(persona)
    with motor_migrate.begin() as c:  # control: el día que empieza, aparece
        c.execute(text("update nucleo.mandato set vigente_desde = :h where id = :m"),
                  {"h": hoy, "m": futuro})  # fmt: skip
    assert str(cons) in consorcios_de(persona)


# Aceptar ------------------------------------------------------------------------------------


def test_un_enlace_vencido_no_sirve(app, motor_migrate, escena):  # noqa: F811
    """Caso 7 bis."""
    e = escena
    correo = correo_nuevo()
    i, token = emitida_y_aprobada(e, correo)
    with motor_migrate.begin() as c:
        c.execute(
            text("update nucleo.invitacion_mandato set aprobada_en = now() - interval '8 days',"
                 " vence_en = now() - interval '1 day' where id = :i"),
            {"i": i},
        )  # fmt: skip
    beto = navegador(app)
    assert beto.post(CONSULTAR, json={"token": token}).status_code == 404
    r = aceptar(beto, token)
    assert (r.status_code, r.json()["codigo"]) == (404, "invitacion_invalida")
    assert persona_por_correo(motor_migrate, correo) is None
    assert mandato(motor_migrate, e["ae"])["persona_id"] is None


def test_correo_con_cuenta_pide_su_sesion(app, motor_migrate, escena):  # noqa: F811
    """Caso 29 y la sesión de otra persona."""
    e = escena
    correo = correo_nuevo()
    persona = otra_persona(motor_migrate, e["cons"])
    with motor_migrate.begin() as c:
        c.execute(text("update nucleo.persona set correo = :c where id = :p"),
                  {"c": correo, "p": persona})  # fmt: skip
    _, token = emitida_y_aprobada(e, correo)
    r = aceptar(navegador(app), token)
    assert (r.status_code, r.json()["codigo"]) == (409, "invitacion_requiere_sesion")
    prop = entrar_como(app, motor_migrate, f"modo-{e['cons']}@ejemplo.com")
    r = prop.post(ACEPTAR, json={"token": token}, headers=csrf(prop))
    assert (r.status_code, r.json()["codigo"]) == (409, "invitacion_de_otra_persona")
    assert mandato(motor_migrate, e["ae"])["persona_id"] is None
    dueña = entrar_como(app, motor_migrate, correo)
    r = dueña.post(ACEPTAR, json={"token": token}, headers=csrf(dueña))
    assert r.status_code == 200, r.text
    assert (r.json()["cuenta_nueva"], mandato(motor_migrate, e["ae"])["persona_id"]) == (
        False, persona,
    )  # fmt: skip


def test_quien_emitio_o_aprobo_no_acepta_para_si(app, motor_migrate, escena):  # noqa: F811
    e = escena
    with motor_migrate.connect() as c:
        correo_mc2 = c.execute(
            text("select correo from nucleo.persona where id = :p"), {"p": e["mc2"]}
        ).scalar_one()
    _, token = emitida_y_aprobada(e, correo_mc2)
    mc2 = entrar_como(app, motor_migrate, correo_mc2)
    r = mc2.post(ACEPTAR, json={"token": token}, headers=csrf(mc2))
    assert (r.status_code, r.json()["codigo"]) == (409, "asociacion_de_si_mismo")
    assert mandato(motor_migrate, e["ae"])["persona_id"] is None


@pytest.mark.parametrize("desde_futuro", [False, True])
def test_quien_tiene_un_mandato_de_consejo_superpuesto_no_se_asocia(
    app,  # noqa: F811
    motor_migrate,
    motor_app,
    escena,
    desde_futuro,
):
    """P1-d (caso 10) y su variante futura (caso 28)."""
    e = escena
    persona = otra_persona(motor_migrate, e["cons"])
    with motor_migrate.connect() as c:
        hoy = c.execute(text("select nucleo.hoy_del_consorcio(:c)"), {"c": e["cons"]}).scalar_one()
        correo = c.execute(
            text("select correo from nucleo.persona where id = :p"), {"p": persona}
        ).scalar_one()
    desde = hoy + timedelta(days=10) if desde_futuro else hoy
    alta(motor_app, e["cons"], e["prop"], e["acta"], persona=persona, tipo="miembro_consejo",
         desde=desde)  # fmt: skip
    _, token = emitida_y_aprobada(e, correo)
    sesion = entrar_como(app, motor_migrate, correo)
    r = sesion.post(ACEPTAR, json={"token": token}, headers=csrf(sesion))
    assert (r.status_code, r.json()["codigo"]) == (409, "asociacion_incompatible_con_consejo")
    assert mandato(motor_migrate, e["ae"])["persona_id"] is None


def test_si_quien_emitio_pierde_su_mandato_el_enlace_no_sirve(
    app,  # noqa: F811
    motor_migrate,
    motor_app,
    escena,
):
    """Caso 10 ter."""
    e = escena
    correo = correo_nuevo()
    _, token = emitida_y_aprobada(e, correo)
    baja(motor_app, e["cons"], e["mc2"], e["m_mc1"], "renuncia")
    r = aceptar(navegador(app), token)
    assert (r.status_code, r.json()["codigo"]) == (404, "invitacion_invalida")
    assert persona_por_correo(motor_migrate, correo) is None


def test_sin_sesion_no_hay_reintento_y_con_sesion_solo_si_sigue_siendo_suyo(
    app,  # noqa: F811
    motor_migrate,
    motor_app,
    escena,
):
    e = escena
    correo = correo_nuevo()
    _, token = emitida_y_aprobada(e, correo)
    beto = navegador(app)
    assert aceptar(beto, token).status_code == 200
    r = aceptar(navegador(app), token)  # otro navegador, sin sesión
    assert (r.status_code, r.json()["codigo"]) == (404, "invitacion_invalida")
    r = beto.post(ACEPTAR, json={"token": token}, headers=csrf(beto))
    assert (r.status_code, r.json()["cuenta_nueva"]) == (200, False)
    persona = persona_por_correo(motor_migrate, correo)
    with transaccion(motor_app, persona_id=e["mc1"], consorcio_id=e["cons"]) as c:
        c.execute(text("select nucleo.desasociar_persona_de_mandato(:m, 'Nota', null, :a,"
                       " 'api')"), {"m": e["ae"], "a": e["mc1"]})  # fmt: skip
    otra = entrar_como(app, motor_migrate, correo)
    r = otra.post(ACEPTAR, json={"token": token}, headers=csrf(otra))
    assert (r.status_code, r.json()["codigo"]) == (404, "invitacion_invalida")
    assert mandato(motor_migrate, e["ae"])["persona_id"] is None
    assert persona is not None


def test_aceptar_espera_a_un_retiro_concurrente_y_lo_respeta(app, motor_migrate, escena):  # noqa: F811
    """Orden de bloqueo: con el retiro en curso (mandato e invitación bloqueados), aceptar
    espera; al confirmarse el retiro, responde que la invitación no sirve. Sin 40P01."""
    e = escena
    correo = correo_nuevo()
    i, token = emitida_y_aprobada(e, correo)
    retiro = motor_migrate.connect()
    tx = retiro.begin()
    retiro.execute(text("select set_config('app.consorcio_id', :c, true)"), {"c": str(e["cons"])})
    retiro.execute(text("select set_config('app.persona_id', '', true)"))
    retiro.execute(text("select nucleo.revocar_invitacion_mandato(:i, null, 'soporte')"), {"i": i})
    resultado = {}
    hilo = threading.Thread(target=lambda: resultado.update(r=aceptar(navegador(app), token)))
    hilo.start()
    hilo.join(1.0)
    assert hilo.is_alive()  # espera el bloqueo del mandato
    tx.commit()
    retiro.close()
    hilo.join(10)
    assert (resultado["r"].status_code, resultado["r"].json()["codigo"]) == (
        404, "invitacion_invalida",
    )  # fmt: skip
    assert persona_por_correo(motor_migrate, correo) is None


# Retirar y desasociar -----------------------------------------------------------------------


def test_retirar_dos_veces_responde_igual_con_un_evento(motor_migrate, escena):
    """Caso 30."""
    e = escena
    i = emitir(e["mc1"], e["cons"], e["ae"], correo_nuevo()).json()["id"]
    with cliente(e["mc2"]) as c:
        for _ in range(2):
            r = c.post(url_invitacion(e["cons"], i, "revocar"), headers=clave_idempotencia())
            assert r.status_code == 204, r.text
        assert c.post(url_invitacion(e["cons"], uuid4(), "revocar"),
                      headers=clave_idempotencia()).status_code == 404  # fmt: skip
    assert [x["accion"] for x in eventos(motor_migrate, e["cons"])].count(
        "consorcio.mandato.revocar_invitacion"
    ) == 1


def test_aprobar_una_aceptada_responde_409(app, escena):  # noqa: F811
    e = escena
    i, token = emitida_y_aprobada(e, correo_nuevo())
    assert aceptar(navegador(app), token).status_code == 200
    r = aprobar(e["mc2"], e["cons"], i)
    assert (r.status_code, r.json()["codigo"]) == (409, "invitacion_no_pendiente")
    assert "aceptó" in r.json()["mensaje"]


def test_retirar_una_aceptada_responde_409(app, escena):  # noqa: F811
    e = escena
    i, token = emitida_y_aprobada(e, correo_nuevo())
    assert aceptar(navegador(app), token).status_code == 200
    with cliente(e["mc2"]) as c:
        r = c.post(url_invitacion(e["cons"], i, "revocar"), headers=clave_idempotencia())
    assert (r.status_code, r.json()["codigo"]) == (409, "invitacion_no_pendiente")


def test_desasociar_quita_el_acceso_y_conserva_el_mandato(app, motor_migrate, escena):  # noqa: F811
    """Caso 10 sexies (P1-c)."""
    e = escena
    correo = correo_nuevo()
    _, token = emitida_y_aprobada(e, correo)
    assert aceptar(navegador(app), token).status_code == 200
    persona = persona_por_correo(motor_migrate, correo)
    assert sesiones_abiertas(motor_migrate, persona, 2) >= 2
    antes = mandato(motor_migrate, e["ae"])
    clave = clave_idempotencia()
    with cliente(e["mc2"]) as c:
        r = c.post(url_mandato(e["cons"], e["ae"], "/desasociar-persona"),
                   json={"referencia": "Nota del estudio del 05-11"}, headers=clave)  # fmt: skip
    assert r.status_code == 200, r.text
    assert r.json()["sesiones_revocadas"] >= 2
    with cliente(e["mc2"]) as c:  # el reintento con la misma clave repite el 200 guardado
        repetido = c.post(url_mandato(e["cons"], e["ae"], "/desasociar-persona"),
                          json={"referencia": "Nota del estudio del 05-11"},
                          headers=clave)  # fmt: skip
    assert (repetido.status_code, repetido.headers["Idempotent-Replayed"]) == (200, "true")
    assert repetido.json() == r.json()
    despues = mandato(motor_migrate, e["ae"])
    assert despues == {**antes, "persona_id": None}  # CUIT y vigencia intactos
    assert sesiones_abiertas(motor_migrate, persona) == 0
    assert str(e["cons"]) not in consorcios_de(persona)
    with motor_migrate.connect() as c:
        fila = c.execute(
            text("select persona_id, referencia, actor from nucleo.desasociacion_mandato"
                 " where mandato_id = :m"), {"m": e["ae"]},
        ).one()  # fmt: skip
    assert (fila.persona_id, fila.referencia, fila.actor) == (
        persona, "Nota del estudio del 05-11", e["mc2"],
    )  # fmt: skip
    [evento] = [x for x in eventos(motor_migrate, e["cons"])
                if x["accion"] == "consorcio.mandato.desasociar_persona"]  # fmt: skip
    assert "Nota del estudio" not in evento["todo"]


def test_quien_conserva_un_vinculo_sigue_en_el_consorcio_sin_las_facultades_del_mandato(
    app,  # noqa: F811
    motor_migrate,
    escena,
):
    """Caso 10 septies: el consorcio deja C4 solo si el mandato era su única fuente de ámbito."""
    e = escena
    _, persona = otra_unidad(motor_migrate, e["cons"])
    with motor_migrate.connect() as c:
        correo = c.execute(
            text("select correo from nucleo.persona where id = :p"), {"p": persona}
        ).scalar_one()
    _, token = emitida_y_aprobada(e, correo)
    sesion = entrar_como(app, motor_migrate, correo)
    assert sesion.post(ACEPTAR, json={"token": token}, headers=csrf(sesion)).status_code == 200

    def facultades() -> list[str]:
        with motor_migrate.connect() as c:
            return list(
                c.execute(
                    text("select f.facultad from nucleo.facultad_otorgada f join nucleo.mandato m"
                         " on m.id = f.mandato_id where m.persona_id = :p and m.consorcio_id = :c"),
                    {"p": persona, "c": e["cons"]},
                ).scalars()
            )  # fmt: skip

    assert facultades() == ["documentos.cargar"]  # control: el mandato le daba algo
    with cliente(e["mc2"]) as c:
        r = c.post(url_mandato(e["cons"], e["ae"], "/desasociar-persona"),
                   json={"referencia": "Nota"}, headers=clave_idempotencia())  # fmt: skip
    assert r.status_code == 200, r.text
    assert facultades() == []
    assert sesiones_abiertas(motor_migrate, persona) == 0
    [fila] = [x for x in cliente(persona).get("/api/v1/me/consorcios").json()
              if x["id"] == str(e["cons"])]  # fmt: skip
    assert fila["vinculos"] == ["propietario"]


def test_desasociar_el_propio_otro_consorcio_o_sin_cuenta(
    app,  # noqa: F811
    motor_migrate,
    motor_app,
    escena,
):
    """Caso 10 quinquies."""
    e = escena
    correo = correo_nuevo()
    url = url_mandato(e["cons"], e["ae"], "/desasociar-persona")
    with cliente(e["mc1"]) as c:
        r = c.post(url, json={"referencia": "x"}, headers=clave_idempotencia())
    assert (r.status_code, r.json()["codigo"]) == (409, "mandato_sin_persona")
    _, token = emitida_y_aprobada(e, correo)
    assert aceptar(navegador(app), token).status_code == 200
    persona = persona_por_correo(motor_migrate, correo)
    with cliente(persona) as c:  # el propio: 403 por el evaluador (A4)
        r = c.post(url, json={"referencia": "x"}, headers=clave_idempotencia())
    assert r.status_code in (403, 404)
    with (
        pytest.raises(DBAPIError) as error,
        transaccion(motor_app, persona_id=persona, consorcio_id=e["cons"]) as c,
    ):
        c.execute(text("select nucleo.desasociar_persona_de_mandato(:m, 'x', null, :a, 'api')"),
                  {"m": e["ae"], "a": persona})  # fmt: skip
    assert error.value.orig.diag.message_hint == "desasociacion_de_si_mismo"
    with cliente(e["mc1"]) as c:
        assert c.post(url_mandato(e["cons"], uuid4(), "/desasociar-persona"),
                      json={"referencia": "x"},
                      headers=clave_idempotencia()).status_code == 404  # fmt: skip
    assert mandato(motor_migrate, e["ae"])["persona_id"] == persona


def test_las_escrituras_piden_referencia_y_clave(escena):
    e = escena
    with cliente(e["mc1"]) as c:
        url = url_mandato(e["cons"], e["ae"], "/invitaciones")
        assert c.post(url, json={"correo": correo_nuevo(), "referencia": "  "},
                      headers=clave_idempotencia()).status_code == 422  # fmt: skip
        assert c.post(url, json={"correo": correo_nuevo(), "referencia": REFERENCIA}
                      ).status_code == 422  # fmt: skip
        assert c.post(url_invitacion(e["cons"], uuid4(), "revocar")).status_code == 422


# Soporte (P1-e) y base ----------------------------------------------------------------------


def test_soporte_emite_y_aprueba_en_un_paso_con_motivo(
    app,  # noqa: F811
    motor_migrate,
    escena,
    monkeypatch,
):
    e = escena
    monkeypatch.setenv("CEM_DATABASE_URL", url_rol("app"))
    monkeypatch.setenv("CEM_ORIGEN_PUBLICO", "https://cem.ejemplo.com")
    correo = correo_nuevo()
    argumentos = ["invitar", "--consorcio", str(e["cons"]), "--mandato", str(e["ae"]),
                  "--correo", correo, "--referencia", REFERENCIA]  # fmt: skip
    with pytest.raises(SystemExit):  # sin motivo, el script ni arranca
        mandato_soporte.main(argumentos)
    assert mandato_soporte.main([*argumentos, "--motivo", "Hay un solo miembro del Consejo"]) == 0
    with motor_migrate.connect() as c:
        fila = c.execute(
            text("select origen, emitida_por, aprobada_por, aprobada_en, motivo_soporte"
                 " from nucleo.invitacion_mandato where mandato_id = :m"), {"m": e["ae"]},
        ).one()  # fmt: skip
    assert (fila.origen, fila.emitida_por, fila.aprobada_por) == ("soporte", None, None)
    assert fila.aprobada_en is not None and fila.motivo_soporte.startswith("Hay un solo")
    [evento] = [x for x in eventos(motor_migrate, e["cons"])
                if x["accion"] == "consorcio.mandato.invitar_persona"]  # fmt: skip
    assert (evento["origen"], evento["despues"]["un_paso"]) == ("soporte", True)
    assert aceptar(navegador(app), token_de(correo)).status_code == 200
    with pytest.raises(DBAPIError) as error, motor_migrate.begin() as c:
        c.execute(text("select set_config('app.consorcio_id', :c, true)"), {"c": str(e["cons"])})
        c.execute(text("select nucleo.desasociar_persona_de_mandato(:m, 'x', null, null,"
                       " 'soporte')"), {"m": e["ae"]})  # fmt: skip
    assert error.value.orig.diag.message_hint == "motivo_requerido"


def test_el_downgrade_de_0015_se_niega_con_datos(base_aparte):  # noqa: F811
    cfg, motor = base_aparte
    command.upgrade(cfg, "0015")
    en_0015 = _catalogo(motor)
    command.downgrade(cfg, "0014")
    command.upgrade(cfg, "0015")
    assert _catalogo(motor) == en_0015  # ida y vuelta sin datos
    with motor.begin() as c:
        cons = c.execute(text("insert into nucleo.consorcio (nombre) values ('A') returning id"))
        cons = cons.scalar_one()
        dec = c.execute(
            text("insert into nucleo.decision_formal (consorcio_id, tipo, fecha, descripcion,"
                 " referencia_acta, origen) values (:c, 'asamblea', '2026-01-01', 'x', 'Acta',"
                 " 'soporte') returning id"), {"c": cons},
        ).scalar_one()  # fmt: skip
        m = c.execute(
            text("insert into nucleo.mandato (consorcio_id, tipo, mandatario_nombre,"
                 " mandatario_cuit, decision_id, vigente_desde, origen) values (:c,"
                 " 'administrador_externo', 'X', :cuit, :d, '2026-01-01', 'soporte')"
                 " returning id"), {"c": cons, "cuit": CUIT_X, "d": dec},
        ).scalar_one()  # fmt: skip
        c.execute(
            text("insert into nucleo.invitacion_mandato (consorcio_id, mandato_id, correo,"
                 " referencia, origen, motivo_soporte, aprobada_en, token_hash, vence_en)"
                 " values (:c, :m, 'a@b.com', 'Ref', 'soporte', 'Un solo MC', now(),"
                 " sha256('x'::bytea), now() + interval '1 day')"),
            {"c": cons, "m": m},
        )  # fmt: skip
    antes = _catalogo(motor)
    with pytest.raises(DBAPIError) as error:
        command.downgrade(cfg, "0014")
    assert error.value.orig.diag.message_hint == "downgrade_con_datos"
    assert _catalogo(motor) == antes


def test_una_invitacion_por_aprobar_y_una_de_soporte_se_pueden_guardar(escena, motor_migrate):
    """El `check` de quien acepta con columnas nulas (`<>`, no `is distinct from`)."""
    e = escena
    emitir(e["mc1"], e["cons"], e["ae"], correo_nuevo())
    with motor_migrate.connect() as c:
        assert c.execute(
            text("select count(*) from nucleo.invitacion_mandato where aprobada_por is null"
                 " and persona_id is null"),
        ).scalar_one() >= 1  # fmt: skip


def test_toda_ruta_de_a7_verifica_la_politica(escena, motor_app):
    """La propietaria, sin `mandatos.registrar`: 403 auditado en cada escritura."""
    e = escena
    with cliente(e["prop"]) as c:
        rutas = [
            (url_mandato(e["cons"], e["ae"], "/invitaciones"),
             {"correo": correo_nuevo(), "referencia": REFERENCIA}),
            (url_invitacion(e["cons"], uuid4(), "revocar"), None),
        ]  # fmt: skip
        for url, cuerpo in rutas:
            r = c.post(url, json=cuerpo, headers=clave_idempotencia())
            assert r.status_code == 403, (url, r.text)
    acciones = [d["accion"] for d in denegaciones(motor_app, e["cons"], e["mc1"], e["prop"])]
    assert acciones == ["mandatos.asociar_persona", "mandatos.revocar_asociacion"]
