"""Comunicados oficiales del consorcio (`0122`; RF28 mínimo para el piloto en Modo Consejo, diseño
P0 S8, plan §6.7 «RF28 sin estados de lectura»).

Contra PostgreSQL real. La escena es la de `0119` (Modo Consejo): un miembro del Consejo con su
vínculo confirmado, la administración sin vínculo, la propietaria confirmada de «1° A» y «1-A»,
una propietaria sin confirmar y una inquilina confirmada, más una ocupante confirmada. Cada
negación lleva su control: el mismo pedido de quien sí puede pasa. Datos SINTETICOS / NO REAL.
"""

import importlib.util
import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from cem_contracts import comunicados as contrato
from psycopg.errors import InsufficientPrivilege
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db import fijar_ambito
from scripts import exportar_consorcio
from tests.conftest import clave_idempotencia
from tests.test_exportacion_2c3 import (  # noqa: F401  (`claves` y `con_url` son fixtures)
    argumentos,
    claves,
    con_url,
    descifrar,
    entidades,
    verificar_manifiesto,
)
from tests.test_liquidacion_oficial_0119 import armar_escena, persona_con_vinculo
from tests.test_mandatos import otra_persona
from tests.test_mandatos_0012 import alta
from tests.test_modo_operacion import cambiar, consorcio_con_persona, decidir_equipo, hoy_de
from tests.test_orden_pago_neutra import cbu_sintetico, nueva_sesion
from tests.test_rutas_egresos import cliente
from tests.test_vinculo_0014 import base_aparte

pytestmark = [pytest.mark.db, pytest.mark.redis]
# ruff: noqa: F811

__all__ = ["base_aparte"]

MIGRACION = Path(__file__).resolve().parent.parent / "migrations/versions/0122_comunicados.py"
COMUNICADO = {
    "titulo": "Corte de agua el martes SINTETICO",
    "texto": "El martes de 9 a 13 se corta el agua por el cambio del tanque.\nDatos de PRUEBA.",
}
CUENTA = cbu_sintetico(41)
ENLACE = "https://cem.ejemplo.com/comunicados"
TABLAS = ("nucleo.comunicado", "nucleo.comunicado_destinatario", "nucleo.comunicado_envio")
EMITIR = text("select * from nucleo.emitir_comunicado(:t, :x, null, :a, 'api', :s)")
LEER = text("select * from nucleo.comunicados_lectura(:a, 'api', :s)")
REGISTRAR = text("select nucleo.registrar_envios_comunicado(:c, cast(:e as jsonb), :a, 'api', :s)")


def _migracion():
    spec = importlib.util.spec_from_file_location("migracion_0122", MIGRACION)
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def armar(motor_migrate, motor_app) -> dict:
    """La escena de `0119` más una ocupante confirmada de «PB-A»."""
    e = armar_escena(motor_migrate, motor_app)
    e["oc"] = persona_con_vinculo(motor_migrate, e["cons"], e["u"]["PB-A"], "ocupante",
                                  confirmado=True)  # fmt: skip
    e["sesion_oc"] = nueva_sesion(motor_migrate, e["oc"])
    e["hoy"] = hoy_de(motor_app, e["cons"])
    return e


@pytest.fixture
def e(motor_migrate, motor_app) -> dict:
    return armar(motor_migrate, motor_app)


def url(e: dict, *partes: str) -> str:
    return "/".join([f"/api/v1/consorcios/{e['cons']}/comunicados", *partes])


def emitir(e: dict, quien: str = "mc", cuerpo: dict | None = None, clave: dict | None = None):
    with cliente(e[quien], e[f"sesion_{quien}"]) as c:
        return c.post(url(e), json=COMUNICADO if cuerpo is None else cuerpo,
                      headers=clave or clave_idempotencia())  # fmt: skip


def emitido(e: dict, quien: str = "mc", **cambios) -> dict:
    r = emitir(e, quien, COMUNICADO | cambios)
    assert r.status_code == 201, r.text
    return r.json()


def leer(e: dict, quien: str, *partes: str):
    with cliente(e[quien], e[f"sesion_{quien}"]) as c:
        return c.get(url(e, *partes))


def correos(motor_migrate, cons) -> list[dict]:
    with motor_migrate.connect() as c:
        return [dict(f) for f in c.execute(
            text("select id, carga from plataforma.outbox where evento = 'correo.solicitado'"
                 " and consorcio_id = :c order by id"), {"c": cons},
        ).mappings()]  # fmt: skip


def correo_de(motor_migrate, persona) -> str:
    with motor_migrate.connect() as c:
        return c.execute(
            text("select correo from nucleo.persona where id = :p"), {"p": persona}
        ).scalar_one()


def cuantos(motor_migrate, tabla: str, cons) -> int:
    with motor_migrate.connect() as c:
        return c.execute(
            text(f"select count(*) from {tabla} where consorcio_id = :c"), {"c": cons}
        ).scalar_one()


def hint(error: pytest.ExceptionInfo) -> str | None:
    return error.value.orig.diag.message_hint


# Constantes: una sola fuente ----------------------------------------------------------------


def test_la_migracion_copia_las_constantes_del_contrato():
    m = _migracion()
    assert (m.LARGO_TITULO, m.LARGO_TEXTO) == (contrato.LARGO_TITULO, contrato.LARGO_TEXTO)


# Emitir ------------------------------------------------------------------------------------


def test_emitir_encola_un_correo_por_persona_con_vinculo_confirmado(e, motor_migrate):
    lista = leer(e, "mc").json()
    assert (lista["puede_emitir"], lista["destinatarios_posibles"]) == (True, 4)
    c = emitido(e)
    assert c["envios"] == {"destinatarios": 4, "enviados": 4}
    assert c["texto"] == COMUNICADO["texto"] and c["corrige_a"] is None
    enviados = correos(motor_migrate, e["cons"])
    para = [x["carga"]["para"] for x in enviados]
    esperados = {correo_de(motor_migrate, e[q]) for q in ("mc", "prop", "inq", "oc")}
    # Uno por persona: la propietaria de dos unidades recibe uno solo.
    assert sorted(para) == sorted(esperados)
    # Ni la propietaria sin confirmar ni la administración sin vínculo.
    for quien in ("sin_confirmar", "ad"):
        assert correo_de(motor_migrate, e[quien]) not in para
    # El correo lleva el título y el enlace; nunca el texto ni otras direcciones.
    for x in enviados:
        carga = x["carga"]
        assert COMUNICADO["titulo"] in carga["asunto"] and COMUNICADO["titulo"] in carga["texto"]
        assert ENLACE in carga["texto"]
        assert "tanque" not in carga["texto"]
        assert "@" not in carga["texto"] + carga["asunto"]
    # Cada envío anotado es el correo de su destinatario.
    with motor_migrate.connect() as cx:
        anotados = dict(cx.execute(text(
            "select n.evento_id, p.correo from nucleo.comunicado_envio n"
            " join nucleo.persona p on p.id = n.persona_id where n.comunicado_id = :c"),
            {"c": c["id"]}).all())  # fmt: skip
    assert anotados == {x["id"]: x["carga"]["para"] for x in enviados}


def test_otro_consorcio_no_recibe_y_su_propio_comunicado_si(e, motor_migrate, motor_app):
    otro = armar(motor_migrate, motor_app)
    emitido(e)
    ajena = correo_de(motor_migrate, otro["inq"])
    assert ajena not in [x["carga"]["para"] for x in correos(motor_migrate, e["cons"])]
    assert correos(motor_migrate, otro["cons"]) == []
    # Control: la inquilina del otro edificio recibe el comunicado de su edificio.
    emitido(otro)
    assert ajena in [x["carga"]["para"] for x in correos(motor_migrate, otro["cons"])]


def test_una_cuenta_en_el_titulo_o_el_texto_se_rechaza(e, motor_migrate):
    con_cuenta = [
        {"titulo": f"Pagar a {CUENTA}"},
        {"texto": f"Transferir a {CUENTA[:8]} {CUENTA[8:]} antes del 10."},
        {"texto": f"Cuenta {'-'.join(CUENTA[i : i + 4] for i in range(0, 22, 4))}."},
    ]
    for cambio in con_cuenta:
        r = emitir(e, cuerpo=COMUNICADO | cambio)
        assert (r.status_code, r.json()["codigo"]) == (422, "texto_con_cuenta"), cambio
    assert cuantos(motor_migrate, "nucleo.comunicado", e["cons"]) == 0
    assert correos(motor_migrate, e["cons"]) == []
    # Control: 21 dígitos no son una cuenta, y separados por comas tampoco.
    emitido(e, texto=f"Expediente {CUENTA[:21]}.")
    emitido(e, texto=f"Facturas {CUENTA[:11]}, {CUENTA[11:]}.")


def test_sin_destinatarios_se_niega_y_no_queda_nada(motor_migrate, motor_app):
    cons, _, _ = consorcio_con_persona(motor_migrate)  # su propietaria, sin confirmar
    acta = decidir_equipo(motor_app, cons)
    cambiar(motor_app, cons, "consejo", acta)
    ad = otra_persona(motor_migrate, cons)
    alta(motor_app, cons, None, acta, persona=ad, tipo="administrador", origen="soporte")
    e = {"cons": cons, "ad": ad, "sesion_ad": nueva_sesion(motor_migrate, ad)}
    assert leer(e, "ad").json()["destinatarios_posibles"] == 0
    r = emitir(e, "ad")
    assert (r.status_code, r.json()["codigo"]) == (409, "sin_destinatarios")
    assert cuantos(motor_migrate, "nucleo.comunicado", cons) == 0
    assert correos(motor_migrate, cons) == []
    # Control: con un vínculo confirmado, el mismo pedido pasa.
    persona_con_vinculo(motor_migrate, cons, _unidad(motor_migrate, cons), "inquilino",
                        confirmado=True)  # fmt: skip
    assert emitir(e, "ad").status_code == 201


def _unidad(motor_migrate, cons) -> UUID:
    with motor_migrate.connect() as c:
        return c.execute(text("select id from nucleo.unidad where consorcio_id = :c limit 1"),
                         {"c": cons}).scalar_one()  # fmt: skip


def test_reintentar_con_la_misma_clave_no_repite_el_comunicado_ni_los_correos(e, motor_migrate):
    clave = clave_idempotencia()
    primera = emitir(e, clave=clave)
    assert primera.status_code == 201, primera.text
    segunda = emitir(e, clave=clave)
    assert segunda.status_code == 201
    assert segunda.headers.get("Idempotent-Replayed") == "true"
    assert segunda.json() == primera.json()
    assert cuantos(motor_migrate, "nucleo.comunicado", e["cons"]) == 1
    assert len(correos(motor_migrate, e["cons"])) == 4
    # Control: otra clave es otro comunicado, con sus correos.
    assert emitir(e).status_code == 201
    assert cuantos(motor_migrate, "nucleo.comunicado", e["cons"]) == 2
    assert len(correos(motor_migrate, e["cons"])) == 8


# Lo emitido no se cambia --------------------------------------------------------------------


def test_un_comunicado_no_se_cambia_ni_se_borra(e, motor_migrate):
    c = emitido(e)
    intentos = [
        "update nucleo.comunicado set titulo = 'Otro' where id = :c",
        "delete from nucleo.comunicado where id = :c",
        "update nucleo.comunicado_destinatario set persona_id = persona_id"
        " where comunicado_id = :c",
        "delete from nucleo.comunicado_destinatario where comunicado_id = :c",
        "update nucleo.comunicado_envio set enviado_en = now() where comunicado_id = :c",
        "delete from nucleo.comunicado_envio where comunicado_id = :c",
        "truncate nucleo.comunicado_envio",
    ]
    for sql in intentos:
        with pytest.raises(DBAPIError) as error:
            with motor_migrate.begin() as cx:
                cx.execute(text(sql), {"c": c["id"]})
        assert hint(error) == "historia_inmutable", sql
    assert leer(e, "mc", c["id"]).json() == c  # sigue igual
    # Y `cem_app` no escribe en ninguna de las tablas, que tienen la RLS habilitada y forzada.
    with motor_migrate.connect() as cx:
        rls = cx.execute(text(
            "select c.oid::regclass::text, c.relrowsecurity, c.relforcerowsecurity,"
            " (select p.qual from pg_policies p where p.schemaname = 'nucleo'"
            "   and p.tablename = c.relname and p.policyname = 'por_consorcio')"
            " from pg_class c where c.oid = any(cast(:t as regclass[])) order by 1"),
            {"t": list(TABLAS)}).all()  # fmt: skip
        assert [tuple(f) for f in rls] == [
            (t, True, True, "(consorcio_id = plataforma.consorcio_actual())")
            for t in sorted(TABLAS)
        ]
        for tabla in TABLAS:
            assert not cx.execute(
                text("select has_table_privilege('cem_app', :t, 'insert, update, delete')"),
                {"t": tabla}).scalar_one(), tabla  # fmt: skip


def test_una_correccion_es_otro_comunicado_que_cita_al_que_corrige(e, motor_migrate, motor_app):
    a = emitido(e)
    b = emitido(e, titulo="Corrección: el corte es el miércoles", corrige_a=a["id"])
    assert b["corrige_a"]["id"] == a["id"] and b["corrige_a"]["titulo"] == a["titulo"]
    de_a = leer(e, "inq", a["id"]).json()
    assert de_a["corregido"] is True and [r["id"] for r in de_a["corregido_por"]] == [b["id"]]
    assert de_a["texto"] == a["texto"]  # el corregido no cambia
    # El de otro edificio no se corrige desde este.
    otro = armar(motor_migrate, motor_app)
    ajeno = emitido(otro)
    r = emitir(e, cuerpo=COMUNICADO | {"corrige_a": ajeno["id"]})
    assert (r.status_code, r.json()["codigo"]) == (422, "peticion_invalida")
    assert r.json()["detalle"] == {"campos": ["body.corrige_a"]}


# Quién lee ----------------------------------------------------------------------------------


def test_quien_lee_y_quien_no(e, motor_migrate, motor_app):
    c = emitido(e)
    for quien in ("prop", "inq", "oc"):
        lista = leer(e, quien)
        assert lista.status_code == 200, quien
        assert [x["id"] for x in lista.json()["elementos"]] == [c["id"]], quien
        assert (lista.json()["puede_emitir"], lista.json()["destinatarios_posibles"]) == (
            False, None)  # fmt: skip
        detalle = leer(e, quien, c["id"]).json()
        assert detalle["texto"] == COMUNICADO["texto"] and detalle["envios"] is None, quien
    # Quien emite ve la historia y las cuentas, aunque no tenga vínculo.
    de_ad = leer(e, "ad", c["id"]).json()
    assert de_ad["envios"] == {"destinatarios": 4, "enviados": 4}
    # Sin confirmar: 403. Control: confirmada (la propietaria), 200 arriba.
    assert leer(e, "sin_confirmar").status_code == 403
    # De otro edificio: 404, también el comunicado de este pedido desde allá.
    otro = armar(motor_migrate, motor_app)
    with cliente(otro["inq"], otro["sesion_inq"]) as cx:
        assert cx.get(url(e)).status_code == 404
        assert cx.get(url(e, c["id"])).status_code == 404
    assert leer(otro, "inq", c["id"]).status_code == 404
    assert leer(otro, "inq").json()["elementos"] == []


def test_sin_facultad_403_y_la_base_niega_igual(e, motor_app):
    for quien in ("prop", "inq"):
        r = emitir(e, quien)
        assert (r.status_code, r.json()["codigo"]) == (403, "sin_permiso"), quien

    def llamar(quien: str, sql, **p):
        with motor_app.connect() as cx, cx.begin() as t:
            fijar_ambito(cx, persona_id=e[quien], consorcio_id=e["cons"])
            filas = cx.execute(sql, {"a": e[quien], "s": e[f"sesion_{quien}"]} | p).all()
            t.rollback()
            return filas

    # Segunda barrera: la base niega a quien no tiene la facultad (42501).
    with pytest.raises(DBAPIError) as error:
        llamar("inq", EMITIR, t="T", x="X")
    assert isinstance(error.value.orig, InsufficientPrivilege)
    assert len(llamar("mc", EMITIR, t="T", x="X")) == 4  # control
    # Y la lectura a quien no tiene un vínculo confirmado.
    with pytest.raises(DBAPIError) as error:
        llamar("sin_confirmar", LEER)
    assert isinstance(error.value.orig, InsufficientPrivilege)
    assert llamar("inq", LEER) == []  # control


def test_los_envios_son_los_correos_de_cada_destinatario(e, motor_app):
    """`registrar_envios_comunicado`: un evento que no es el correo de esa persona, o un
    destinatario sin el suyo, y no queda nada."""

    def intento(envios_de) -> str | None:
        with motor_app.connect() as cx, cx.begin() as t:
            fijar_ambito(cx, persona_id=e["mc"], consorcio_id=e["cons"])
            p = {"a": e["mc"], "s": e["sesion_mc"]}
            filas = cx.execute(EMITIR, p | {"t": "T", "x": "X"}).mappings().all()
            envios = envios_de(cx, filas)
            try:
                cx.execute(REGISTRAR, p | {"c": filas[0]["comunicado_id"],
                                           "e": json.dumps(envios)})  # fmt: skip
                return None
            except DBAPIError as error:
                return error.orig.diag.message_hint
            finally:
                t.rollback()

    def publicar(cx, para: str) -> str:
        return str(cx.execute(text(
            "insert into plataforma.outbox (evento, consorcio_id, carga) values"
            " ('correo.solicitado', :c, cast(:k as jsonb)) returning id"),
            {"c": e["cons"], "k": json.dumps({"para": para, "asunto": "A", "texto": "T"})},
        ).scalar_one())  # fmt: skip

    def todos(cx, filas):
        return [{"persona_id": str(f["persona_id"]), "evento_id": publicar(cx, f["correo"])}
                for f in filas]  # fmt: skip

    def cruzados(cx, filas):
        envios = todos(cx, filas)
        primero, segundo = envios[0]["evento_id"], envios[1]["evento_id"]
        envios[0]["evento_id"], envios[1]["evento_id"] = segundo, primero
        return envios

    assert intento(todos) is None  # control
    assert intento(cruzados) == "envio_sin_correo"
    assert intento(lambda cx, filas: todos(cx, filas)[1:]) == "envios_incompletos"
    assert intento(lambda cx, filas: [{**x, "evento_id": str(uuid4())}
                                      for x in todos(cx, filas)]) == "envio_sin_correo"  # fmt: skip


# Facultad y migración -----------------------------------------------------------------------


def test_un_mandato_registrado_antes_de_0122_no_recibe_la_facultad(base_aparte):
    cfg, motor = base_aparte
    command.upgrade(cfg, "0121")

    def alta_soporte() -> UUID:
        with motor.begin() as c:
            cons = c.execute(text("insert into nucleo.consorcio (nombre) values ('A')"
                                  " returning id")).scalar_one()  # fmt: skip
            dec = c.execute(text(
                "insert into nucleo.decision_formal (consorcio_id, tipo, fecha, descripcion,"
                " referencia_acta, origen) values (:c, 'asamblea', '2026-01-01', 'x', 'Acta',"
                " 'soporte') returning id"), {"c": cons}).scalar_one()  # fmt: skip
            per = c.execute(text(
                "insert into nucleo.persona (correo, nombre) values (:c, 'V') returning id"),
                {"c": f"v-{uuid4().hex[:8]}@e.com"}).scalar_one()  # fmt: skip
            fijar_ambito(c, persona_id=None, consorcio_id=cons)
            return c.execute(text(
                "select nucleo.alta_mandato('administrador', :p, null, null, null, :d,"
                " '2026-01-01', null, null, null, 'soporte')"), {"p": per, "d": dec},
            ).scalar_one()  # fmt: skip

    def facultades(mandato: UUID) -> set[str]:
        sql = "select facultad from nucleo.facultad_otorgada where mandato_id = :m"
        with motor.connect() as c:
            return set(c.execute(text(sql), {"m": mandato}).scalars())

    def paquetes() -> set[str]:
        sql = "select tipo::text from nucleo.paquete_facultad where facultad = 'comunicados.emitir'"
        with motor.connect() as c:
            return set(c.execute(text(sql)).scalars())

    antes = alta_soporte()
    assert "liquidacion.importar" in facultades(antes)  # control: el paquete se copió
    command.upgrade(cfg, "0122")
    assert paquetes() == {"miembro_consejo", "administrador"}
    assert "comunicados.emitir" not in facultades(antes)
    command.downgrade(cfg, "0121")  # sin datos de 0122: baja
    assert paquetes() == set()
    command.upgrade(cfg, "0122")  # control: vuelve a subir limpio
    despues = alta_soporte()
    assert "comunicados.emitir" in facultades(despues)  # control: el alta nueva sí la recibe
    with pytest.raises(DBAPIError) as error:
        command.downgrade(cfg, "0121")
    assert hint(error) == "downgrade_con_datos"


# Exportación --------------------------------------------------------------------------------


def test_la_exportacion_lleva_los_comunicados_sin_correos_ni_identificadores(
    e, motor_migrate, con_url, claves, tmp_path
):
    a = emitido(e)
    b = emitido(e, titulo="Corrección SINTETICA", corrige_a=a["id"])
    salida = tmp_path / f"exportacion-{uuid4().hex[:8]}.zip.age"
    assert exportar_consorcio.main(argumentos(e, claves, salida)) == 0
    paquete = descifrar(salida, claves["privada"])
    assert verificar_manifiesto(paquete) == []
    datos = entidades(paquete)
    assert [(x["id"], x["corrige_a"]) for x in datos["comunicado"]] == [
        (a["id"], None), (b["id"], a["id"])]  # fmt: skip
    assert datos["comunicado"][0]["texto"] == COMUNICADO["texto"]
    assert {x["emitido_por_nombre"] for x in datos["comunicado"]} == {"Otra"}
    filas = datos["comunicado_destinatario"]
    assert len(filas) == 8 and {x["estado"] for x in filas} == {"enviado"}
    assert all(x["enviado_en"] for x in filas)
    # Ni correos ni identificadores de personas, ni el evento del outbox.
    crudo = json.dumps(datos["comunicado"] + filas)
    with motor_migrate.connect() as cx:
        personas = cx.execute(text(
            "select p.id::text, p.correo from nucleo.comunicado_destinatario d"
            " join nucleo.persona p on p.id = d.persona_id where d.consorcio_id = :c"),
            {"c": e["cons"]}).all()  # fmt: skip
        sql = "select evento_id::text from nucleo.comunicado_envio where consorcio_id = :c"
        eventos = cx.execute(text(sql), {"c": e["cons"]}).scalars().all()
    assert personas and eventos  # control: hay qué buscar
    for pid, direccion in personas:
        assert pid not in crudo and direccion not in crudo
    for evento in eventos:
        assert evento not in crudo
    assert str(e["mc"]) not in crudo


def test_la_exportacion_tapa_una_cuenta_aunque_la_entrada_la_rechace(e, motor_migrate, con_url,
                                                                   claves, tmp_path):  # fmt: skip
    """Defensa en profundidad del paquete: un texto que llegó a la base sin pasar por el
    disparador (en esta base descartable, con los disparadores apagados) sale sin la cuenta."""
    c = emitido(e)
    with motor_migrate.begin() as cx:
        cx.execute(text("alter table nucleo.comunicado disable trigger user"))
        cx.execute(text("update nucleo.comunicado set texto = :t where id = :c"),
                   {"t": f"Cuenta {CUENTA}", "c": c["id"]})  # fmt: skip
        cx.execute(text("alter table nucleo.comunicado enable trigger user"))
    salida = tmp_path / f"exportacion-{uuid4().hex[:8]}.zip.age"
    assert exportar_consorcio.main(argumentos(e, claves, salida)) == 0
    [fila] = entidades(descifrar(salida, claves["privada"]))["comunicado"]
    assert CUENTA not in fila["texto"] and "Cuenta " in fila["texto"]
