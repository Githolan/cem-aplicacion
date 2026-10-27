"""Soporte borra un documento o cambia su categoría con su rol propio (H2c §1.1 y §4; 0037).

`scripts.documento_soporte` corre como `cem_soporte_documentos`: sin `BYPASSRLS`, con el
consorcio en el contexto y solo las columnas del borrado y de la categoría. Cada negación lleva
su control: el mismo pedido de quien sí tiene la capacidad pasa.
"""

from datetime import date, timedelta

import pytest
from alembic import command
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from app.db import transaccion
from scripts import documento_soporte
from tests.conftest import url_rol
from tests.test_contexto_mandatos import cliente
from tests.test_documentos_2c1 import crear, url
from tests.test_mandatos import otra_persona
from tests.test_mandatos_0012 import alta
from tests.test_modo_operacion import cambiar, consorcio_con_persona, decidir_equipo, hoy_de
from tests.test_vinculo_0014 import base_aparte  # noqa: F401  (`base_aparte` es un fixture)

pytestmark = [pytest.mark.db, pytest.mark.redis]


def _edificio(motor_migrate, motor_app) -> dict:
    """Consorcio en Modo Consejo con un administrador (`documentos.ver` y `documentos.cargar`) y
    un miembro del Consejo (`documentos.ver`, sin `documentos.cargar`)."""
    cons, _, prop = consorcio_con_persona(motor_migrate)
    acta = decidir_equipo(motor_app, cons)
    cambiar(motor_app, cons, "consejo", acta)
    ad = otra_persona(motor_migrate, cons)
    m_ad = alta(motor_app, cons, None, acta, persona=ad, tipo="administrador", origen="soporte")
    mc = otra_persona(motor_migrate, cons)
    alta(motor_app, cons, None, acta, persona=mc, tipo="miembro_consejo", origen="soporte")
    # El día del pedido se compara con el del consorcio (Buenos Aires), no con el del contenedor
    # (UTC): entre las 21 y las 24 de Argentina son días distintos.
    hoy = hoy_de(motor_app, cons)
    return {"cons": cons, "prop": prop, "ad": ad, "m_ad": m_ad, "mc": mc, "hoy": hoy}


@pytest.fixture
def edificio(motor_migrate, motor_app):
    return _edificio(motor_migrate, motor_app)


@pytest.fixture
def con_url(monkeypatch):
    monkeypatch.setenv(documento_soporte.VARIABLE_URL, url_rol("soporte_documentos"))


def documento(e, categoria="actas_y_decisiones") -> str:
    r = crear(e["ad"], e["cons"], categoria=categoria, titulo=f"Doc {categoria}")
    assert r.status_code == 201, r.text
    return r.json()["id"]


def correr(e, accion, doc, solicitante, *, fecha=None, categoria=None, consorcio=None) -> int:
    fecha = fecha or e["hoy"]
    argumentos = [
        accion, "--consorcio", str(consorcio or e["cons"]), "--documento", str(doc),
        "--solicitante", str(solicitante), "--fecha-pedido", fecha.isoformat(),
        "--referencia", "Nota 12/2026", "--motivo", "Cargado por error",
    ]  # fmt: skip
    if categoria:
        argumentos += ["--categoria", categoria]
    return documento_soporte.main(argumentos)


def fila(motor_migrate, doc) -> dict:
    with motor_migrate.connect() as c:
        return dict(
            c.execute(text("select * from nucleo.documento where id = :d"), {"d": doc})
            .mappings()
            .one()
        )


def eventos(motor_migrate, cons) -> int:
    with motor_migrate.connect() as c:
        return c.execute(
            text("select count(*) from auditoria.eventos where consorcio_id = :c"), {"c": cons}
        ).scalar_one()


def visibles(persona, cons) -> set[str]:
    with cliente(persona) as c:
        r = c.get(url(cons))
    assert r.status_code == 200, r.text
    return {d["id"] for d in r.json()["documentos"]}


# Borrado ------------------------------------------------------------------------------


def test_el_administrador_pide_y_soporte_borra_sin_eventos(edificio, con_url, motor_migrate,
                                                           capsys):  # fmt: skip
    e = edificio
    doc = documento(e)
    assert doc in visibles(e["prop"], e["cons"])
    antes = eventos(motor_migrate, e["cons"])
    assert correr(e, "borrar", doc, e["ad"]) == 0
    salida = capsys.readouterr().out
    assert f"documento: {doc}" in salida and f"mandato: {e['m_ad']}" in salida
    f = fila(motor_migrate, doc)
    assert (f["estado"], f["borrado_motivo"], f["borrado_referencia"]) == (
        "borrado", "Cargado por error", "Nota 12/2026")  # fmt: skip
    assert (f["borrado_solicitado_por"], f["borrado_mandato"], f["borrado_fecha_pedido"]) == (
        e["ad"], e["m_ad"], e["hoy"])  # fmt: skip
    assert eventos(motor_migrate, e["cons"]) == antes  # soporte no escribe en la auditoría
    assert doc not in visibles(e["prop"], e["cons"])
    assert doc not in visibles(e["mc"], e["cons"])


@pytest.mark.parametrize("quien", ["mc", "prop"])
def test_sin_la_capacidad_no_se_borra(edificio, con_url, motor_migrate, quien, capsys):
    e = edificio
    doc = documento(e)
    assert correr(e, "borrar", doc, e[quien]) == 1
    assert "no tenía ese día un mandato vigente" in capsys.readouterr().err
    assert fila(motor_migrate, doc)["estado"] == "vigente"
    assert correr(e, "borrar", doc, e["ad"]) == 0  # control


def test_la_capacidad_se_mide_el_dia_del_pedido(edificio, con_url, motor_migrate, capsys):
    e = edificio
    doc = documento(e)
    # Antes del mandato (desde el 1-1-2026, `DESDE` de las pruebas) no tenía la capacidad.
    assert correr(e, "borrar", doc, e["ad"], fecha=date(2025, 12, 31)) == 1
    assert correr(e, "borrar", doc, e["ad"], fecha=e["hoy"] + timedelta(days=2)) == 1
    assert "fecha_futura" in capsys.readouterr().err
    assert fila(motor_migrate, doc)["estado"] == "vigente"
    assert correr(e, "borrar", doc, e["ad"], fecha=date(2026, 1, 1)) == 0  # control


def test_un_documento_de_otro_consorcio_no_se_toca(edificio, con_url, motor_migrate, motor_app,
                                                   capsys):  # fmt: skip
    e = edificio
    otro = _edificio(motor_migrate, motor_app)
    doc_otro = documento(otro)
    assert correr(e, "borrar", doc_otro, e["ad"]) == 1  # con el consorcio propio en el contexto
    assert "no existe en este consorcio" in capsys.readouterr().err
    assert correr(e, "borrar", doc_otro, e["ad"], consorcio=otro["cons"]) == 1  # sin capacidad
    assert fila(motor_migrate, doc_otro)["estado"] == "vigente"
    assert correr(otro, "borrar", doc_otro, otro["ad"]) == 0  # control


def test_un_borrado_no_vuelve_ni_cambia_de_categoria(edificio, con_url, motor_migrate):
    e = edificio
    doc = documento(e)
    assert correr(e, "borrar", doc, e["ad"]) == 0
    assert correr(e, "borrar", doc, e["ad"]) == 1
    assert correr(e, "cambiar-categoria", doc, e["ad"], categoria="seguros") == 1
    # Ni con el dueño de las tablas: el disparador lo niega.
    with pytest.raises(DBAPIError) as error, motor_migrate.begin() as c:
        c.execute(text("update nucleo.documento set estado = 'vigente', borrado_en = null"
                       " where id = :d"), {"d": doc})  # fmt: skip
    assert error.value.orig.diag.message_hint == "documento_borrado"


# Cambio de categoría ------------------------------------------------------------------


def test_cambiar_la_categoria_deja_la_anterior_en_la_fila(edificio, con_url, motor_migrate):
    e = edificio
    doc = documento(e, "actas_y_decisiones")
    antes = eventos(motor_migrate, e["cons"])
    assert correr(e, "cambiar-categoria", doc, e["ad"], categoria="otros") == 0
    f = fila(motor_migrate, doc)
    cambio = (f["categoria"], f["categoria_anterior"], f["categoria_mandato"],
              f["categoria_fecha_pedido"])  # fmt: skip
    assert cambio == ("otros", "actas_y_decisiones", e["m_ad"], e["hoy"])
    assert eventos(motor_migrate, e["cons"]) == antes
    # Ahora es restringida: la propietaria ya no la ve; el Consejo, con la facultad, sí.
    assert doc not in visibles(e["prop"], e["cons"])
    assert doc in visibles(e["mc"], e["cons"])
    assert correr(e, "cambiar-categoria", doc, e["ad"], categoria="otros") == 1  # la misma


# El rol -------------------------------------------------------------------------------


def _como_soporte(cons, sql, parametros=None):
    motor = create_engine(url_rol("soporte_documentos"))
    try:
        with transaccion(motor, persona_id=None, consorcio_id=cons) as c:
            return c.execute(text(sql), parametros or {})
    finally:
        motor.dispose()


@pytest.mark.parametrize(
    "sql",
    [
        "update nucleo.documento set titulo = 'Otro' where id = :d",
        "update nucleo.documento set cargado_por = cargado_por where id = :d",
        "delete from nucleo.documento where id = :d",
        "update nucleo.version_documento set estado = estado where documento_id = :d",
        "insert into auditoria.eventos (consorcio_id, origen, accion, entidad, hash_previo, hash)"
        " values (gen_random_uuid(), 'soporte', 'x', 'x', '', '')",
    ],
    ids=["titulo", "cargado_por", "delete", "versiones", "auditoria"],
)
def test_el_rol_no_toca_mas_que_lo_suyo(edificio, sql):
    e = edificio
    doc = documento(e)
    with pytest.raises(DBAPIError) as error:
        _como_soporte(e["cons"], sql, {"d": doc})
    assert error.value.orig.sqlstate == "42501"
    # Control: la columna de la categoría sí.
    r = _como_soporte(e["cons"], "update nucleo.documento set categoria = categoria"
                                 " where id = :d", {"d": doc})  # fmt: skip
    assert r.rowcount == 1


def test_cambiar_la_categoria_sin_su_constancia_se_niega(edificio, motor_migrate):
    e = edificio
    doc = documento(e)
    with pytest.raises(DBAPIError) as error:
        _como_soporte(e["cons"], "update nucleo.documento set categoria = 'otros' where id = :d",
                      {"d": doc})  # fmt: skip
    assert error.value.orig.diag.message_hint == "cambio_sin_constancia"
    # Ni dejando una categoría anterior que no es la que tenía.
    with pytest.raises(DBAPIError) as error:
        _como_soporte(
            e["cons"],
            "update nucleo.documento set categoria = 'otros', categoria_anterior = 'seguros',"
            " categoria_cambiada_en = now(), categoria_motivo = 'm', categoria_referencia = 'r',"
            " categoria_solicitada_por = :p, categoria_mandato = :m,"
            " categoria_fecha_pedido = current_date where id = :d",
            {"d": doc, "p": e["ad"], "m": e["m_ad"]},
        )
    assert error.value.orig.diag.message_hint == "cambio_sin_constancia"
    assert fila(motor_migrate, doc)["categoria"] == "actas_y_decisiones"


def test_de_las_funciones_security_definer_el_rol_ejecuta_solo_la_capacidad(motor_migrate):
    with motor_migrate.connect() as c:
        ejecutables = set(
            c.execute(
                text("select p.oid::regprocedure::text from pg_proc p"
                     " join pg_namespace n on n.oid = p.pronamespace"
                     " where n.nspname in ('nucleo', 'plataforma', 'auditoria') and p.prosecdef"
                     " and has_function_privilege('cem_soporte_documentos', p.oid, 'execute')"),
            ).scalars()
        )  # fmt: skip
        # Control: la búsqueda encuentra funciones security definer que cem_app sí ejecuta.
        de_app = c.execute(
            text("select count(*) from pg_proc p join pg_namespace n on n.oid = p.pronamespace"
                 " where n.nspname = 'nucleo' and p.prosecdef"
                 " and has_function_privilege('cem_app', p.oid, 'execute')"),
        ).scalar_one()  # fmt: skip
    assert de_app > 10
    assert ejecutables == {
        "nucleo.capacidad_del_solicitante(uuid,uuid,text[],date)",
    }


def test_crear_roles_se_puede_volver_a_correr_sin_la_clave_nueva():
    """Volver a correrlo (como para agregar cem_backup) sin `clave_soporte_documentos` no falla:
    ese rol se crea con su propio archivo."""
    import subprocess

    from sqlalchemy.engine import make_url

    from tests.conftest import ADMIN_URL, BASE, CLAVES, RAIZ

    libpq = make_url(ADMIN_URL).set(drivername="postgresql").render_as_string(False)
    comun = ["psql", libpq, "-q", "-v", f"base={BASE}",
             "-v", f"clave_migrate={CLAVES['migrate']}", "-v", f"clave_app={CLAVES['app']}",
             "-v", f"clave_ro={CLAVES['ro']}", "-v", f"clave_backup={CLAVES['backup']}",
             "-f", str(RAIZ / "scripts" / "crear_roles.sql")]  # fmt: skip
    hecho = subprocess.run(comun, capture_output=True, text=True)
    assert hecho.returncode == 0, hecho.stderr
    assert "cem_soporte_documentos no se crea" in hecho.stdout
    assert "cem_copia_continua no se crea" in hecho.stdout
    # Control: con las claves, también termina bien (los roles ya existen y no se tocan, salvo
    # el tope de conexiones de cem_copia_continua, que se ajusta). La de
    # exportación (2c-3) va también: sin ella, su rol tampoco se crea y lo dice.
    con = comun[:-2] + ["-v", f"clave_soporte_documentos={CLAVES['soporte_documentos']}",
                        "-v", f"clave_soporte_exportacion={CLAVES['soporte_exportacion']}",
                        "-v", f"clave_copia_continua={CLAVES['copia_continua']}",
                        *comun[-2:]]  # fmt: skip
    hecho = subprocess.run(con, capture_output=True, text=True)
    assert hecho.returncode == 0 and "no se crea" not in hecho.stdout, hecho.stderr


def test_el_rol_ve_solo_el_consorcio_del_contexto(edificio, motor_migrate, motor_app):
    e = edificio
    doc = documento(e)
    otro = _edificio(motor_migrate, motor_app)
    contar = "select count(*) from nucleo.documento where id = :d"
    assert _como_soporte(None, contar, {"d": doc}).scalar_one() == 0
    assert _como_soporte(otro["cons"], contar, {"d": doc}).scalar_one() == 0
    cambio = "update nucleo.documento set categoria = 'seguros' where id = :d"
    assert _como_soporte(otro["cons"], cambio, {"d": doc}).rowcount == 0
    assert fila(motor_migrate, doc)["categoria"] == "actas_y_decisiones"
    assert _como_soporte(e["cons"], contar, {"d": doc}).scalar_one() == 1  # control


def test_la_capacidad_exige_el_consorcio_del_contexto_y_no_la_ejecuta_cem_app(edificio,
                                                                            motor_app):  # fmt: skip
    e = edificio
    capacidad = "select nucleo.capacidad_del_solicitante(:c, :p, :f, :h)"
    datos = {
        "c": e["cons"],
        "p": e["ad"],
        "f": ["documentos.ver", "documentos.cargar"],
        "h": e["hoy"],
    }
    with pytest.raises(DBAPIError) as error:
        _como_soporte(None, capacidad, datos)
    assert error.value.orig.sqlstate == "42501"
    with (
        pytest.raises(DBAPIError) as error,
        transaccion(motor_app, persona_id=None, consorcio_id=e["cons"]) as c,
    ):
        c.execute(text(capacidad), datos)
    assert error.value.orig.sqlstate == "42501"
    assert _como_soporte(e["cons"], capacidad, datos).scalar_one() == e["m_ad"]  # control
    # Una facultad que el mandato no tiene (solo está en el paquete del Consejo), aunque tenga
    # las otras, no alcanza.
    sin = datos | {"f": ["documentos.ver", "documentos.cargar", "documentos.cargar_operativos"]}
    assert _como_soporte(e["cons"], capacidad, sin).scalar_one() is None


def test_con_la_url_de_otro_rol_el_script_se_niega(edificio, monkeypatch, motor_migrate, capsys):
    e = edificio
    doc = documento(e)
    monkeypatch.setenv(documento_soporte.VARIABLE_URL, url_rol("app"))
    assert correr(e, "borrar", doc, e["ad"]) == 1
    assert "conecta como cem_app" in capsys.readouterr().err
    assert fila(motor_migrate, doc)["estado"] == "vigente"


def test_sin_la_variable_el_script_no_corre(edificio, monkeypatch, capsys):
    monkeypatch.delenv(documento_soporte.VARIABLE_URL, raising=False)
    assert correr(edificio, "borrar", documento(edificio), edificio["ad"]) == 2
    assert documento_soporte.VARIABLE_URL in capsys.readouterr().err


# Migración ----------------------------------------------------------------------------


def test_0037_ida_y_vuelta_y_se_niega_con_un_borrado(base_aparte):  # noqa: F811
    cfg, motor = base_aparte
    command.upgrade(cfg, "0037")

    def columnas() -> int:
        with motor.connect() as c:
            return c.execute(
                text("select count(*) from information_schema.columns where table_schema ="
                     " 'nucleo' and table_name = 'documento' and column_name like 'categoria_%'"),
            ).scalar_one()  # fmt: skip

    assert columnas() == 7
    command.downgrade(cfg, "0036")
    assert columnas() == 0
    command.upgrade(cfg, "0037")  # control: vuelve a subir limpio
    with motor.begin() as c:
        cons = c.execute(text("insert into nucleo.consorcio (nombre) values ('A') returning id"))
        cons = cons.scalar_one()
        dec = c.execute(
            text("insert into nucleo.decision_formal (consorcio_id, tipo, fecha, descripcion,"
                 " referencia_acta, origen) values (:c, 'asamblea', '2026-01-01', 'x', 'Acta',"
                 " 'soporte') returning id"), {"c": cons},
        ).scalar_one()  # fmt: skip
        per = c.execute(
            text("insert into nucleo.persona (correo, nombre) values ('v@e.com', 'V')"
                 " returning id")).scalar_one()  # fmt: skip
        m = c.execute(
            text("insert into nucleo.mandato (consorcio_id, tipo, persona_id, decision_id,"
                 " vigente_desde, origen) values (:c, 'administrador', :p, :d, '2026-01-01',"
                 " 'soporte') returning id"), {"c": cons, "p": per, "d": dec},
        ).scalar_one()  # fmt: skip
        c.execute(
            text("insert into nucleo.documento (consorcio_id, categoria, titulo, fecha,"
                 " cargado_por, estado, borrado_en, borrado_motivo, borrado_referencia,"
                 " borrado_solicitado_por, borrado_mandato, borrado_fecha_pedido) values"
                 " (:c, 'otros', 'T', '2026-01-01', :p, 'borrado', now(), 'm', 'r', :p, :m,"
                 " '2026-01-02')"),
            {"c": cons, "p": per, "m": m},
        )  # fmt: skip
    with pytest.raises(DBAPIError) as error:
        command.downgrade(cfg, "0036")
    assert error.value.orig.diag.message_hint == "downgrade_con_datos"
