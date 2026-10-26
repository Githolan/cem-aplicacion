"""Documentos: quién ve y quién carga cada categoría, la lista y la descarga (H2c 2c-2; 0036).

Diseño: `doc/diseno/H2c_Diseno.md` del repositorio CEM, §4. Contra servicios reales: PostgreSQL,
el almacén S3 de pruebas y ClamAV (las versiones se aprueban con el análisis de verdad). Cada
negación lleva su control: el mismo pedido de quien sí puede pasa.
"""

from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx
import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db import transaccion
from tests import test_documentos_2c1 as carga
from tests.test_condicion_habilitante_consejo import unidad, vinculo
from tests.test_contexto_mandatos import cliente
from tests.test_documentos_2c1 import _s3, analizar, crear, pdf, subir, url, version
from tests.test_mandatos import otra_persona
from tests.test_mandatos_0012 import alta
from tests.test_modo_operacion import cambiar, consorcio_con_persona, decidir_equipo
from tests.test_vinculo_0014 import base_aparte  # noqa: F401  (`base_aparte` es un fixture)

pytestmark = [pytest.mark.db, pytest.mark.redis]

# Los fixtures de 2c-1: la escena (AE con `documentos.cargar`, MC, propietaria), los buckets de
# prueba y las firmas de la imagen (automático).
escena = carga.escena
bucket = carga.bucket
bucket_con_versiones = carga.bucket_con_versiones
firmas_de_la_imagen = carga.firmas_de_la_imagen

TODAS = [
    "institucional", "actas_y_decisiones", "seguros", "contratos_proveedores",
    "facturas_y_comprobantes", "presupuestos", "mantenimiento_y_certificados", "otros",
]  # fmt: skip
RESTRINGIDAS = {"contratos_proveedores", "otros"}
OPERATIVAS = {"actas_y_decisiones", "presupuestos", "mantenimiento_y_certificados"}


def documento_con_archivo(e, motor_app, categoria: str, contenido: bytes | None = None):
    """Un documento de la categoría con una versión aprobada por el análisis, cargado por el
    administrador externo. Devuelve (documento, versión, contenido)."""
    contenido = contenido or pdf()
    r = crear(e["ae"], e["cons"], categoria=categoria, titulo=f"Doc {categoria}")
    assert r.status_code == 201, r.text
    doc = r.json()["id"]
    r = subir(e["ae"], e["cons"], doc, contenido, "application/pdf")
    assert r.status_code == 201, r.text
    assert analizar(motor_app) == 1
    return doc, r.json()["id"], contenido


def listar(persona, cons):
    with cliente(persona) as c:
        return c.get(url(cons))


def descargar(persona, cons, doc, v):
    with cliente(persona) as c:
        return c.get(url(cons, f"/{doc}/versiones/{v}/descargar"), follow_redirects=False)


def categorias(r) -> set[str]:
    assert r.status_code == 200, r.text
    return {d["categoria"] for d in r.json()["documentos"]}


def denegadas(motor_migrate, cons, accion: str) -> list[str]:
    with motor_migrate.connect() as c:
        return list(
            c.execute(
                text("select despues->>'motivo' from auditoria.eventos"
                     " where consorcio_id = :c and accion = 'autorizacion.denegada'"
                     " and despues->>'accion' = :a order by secuencia"),
                {"c": cons, "a": accion},
            ).scalars()
        )  # fmt: skip


# Lista ----------------------------------------------------------------------------------


def test_el_propietario_ve_seis_categorias_y_el_consejo_las_ocho(escena, bucket, motor_app):
    e = escena
    for categoria in TODAS:
        documento_con_archivo(e, motor_app, categoria)
    # La propietaria (por su vínculo) no ve contratos ni otros; el Consejo, con la facultad
    # `documentos.ver`, ve las ocho.
    assert categorias(listar(e["prop"], e["cons"])) == set(TODAS) - RESTRINGIDAS
    assert categorias(listar(e["mc"], e["cons"])) == set(TODAS)


def test_inquilino_y_administrador_externo_no_listan(escena, bucket, motor_app, motor_migrate):
    e = escena
    documento_con_archivo(e, motor_app, "institucional")
    inquilino = otra_persona(motor_migrate, e["cons"])
    vinculo(motor_migrate, e["cons"], unidad(motor_migrate, e["cons"], "9° Z"), inquilino,
            tipo="inquilino")  # fmt: skip
    assert listar(inquilino, e["cons"]).status_code == 403  # A6
    assert listar(e["ae"], e["cons"]).status_code == 403  # solo carga
    assert denegadas(motor_migrate, e["cons"], "documentos.ver") == ["sin_concesion"] * 2
    ajena = otra_persona(motor_migrate, consorcio_con_persona(motor_migrate)[0])
    assert listar(ajena, e["cons"]).status_code == 404  # fuera de ámbito, sin auditar
    assert categorias(listar(e["prop"], e["cons"])) == {"institucional"}  # control


def test_la_cuarentena_la_ve_solo_quien_subio(escena, bucket, motor_app):
    e = escena
    r = crear(e["mc"], e["cons"], categoria="actas_y_decisiones", titulo="Acta del Consejo")
    assert r.status_code == 201, r.text
    doc = r.json()["id"]
    v = subir(e["mc"], e["cons"], doc, pdf(), "application/pdf").json()["id"]

    def versiones(persona):
        r = listar(persona, e["cons"])
        assert r.status_code == 200, r.text
        (d,) = [d for d in r.json()["documentos"] if d["id"] == doc]
        return [(x["id"], x["estado"]) for x in d["versiones"]]

    assert versiones(e["mc"]) == [(v, "en_cuarentena")]
    assert versiones(e["prop"]) == []  # el documento figura, su versión todavía no
    assert analizar(motor_app) == 1
    assert versiones(e["prop"]) == [(v, "disponible")]  # control: aprobada, la ve


def test_un_documento_de_otro_consorcio_no_figura(escena, bucket, motor_app, motor_migrate):
    e = escena
    documento_con_archivo(e, motor_app, "seguros")
    otro, _, prop_otro = consorcio_con_persona(motor_migrate)
    cambiar(motor_app, otro, "consejo", decidir_equipo(motor_app, otro))
    assert listar(prop_otro, otro).json()["documentos"] == []
    assert categorias(listar(e["prop"], e["cons"])) == {"seguros"}  # control


# Carga del Consejo ----------------------------------------------------------------------


@pytest.mark.parametrize("categoria", TODAS)
def test_el_consejo_carga_solo_las_operativas(escena, bucket, motor_migrate, categoria):
    e = escena
    r = crear(e["mc"], e["cons"], categoria=categoria)
    if categoria in OPERATIVAS:
        assert r.status_code == 201, r.text
    else:
        assert r.status_code == 403, r.text
        assert denegadas(motor_migrate, e["cons"], "documentos.cargar") == ["sin_concesion"]
        assert crear(e["ae"], e["cons"], categoria=categoria).status_code == 201  # control


def test_el_consejo_sube_archivos_solo_a_documentos_operativos(escena, bucket):
    e = escena
    seguros = crear(e["ae"], e["cons"], categoria="seguros").json()["id"]
    actas = crear(e["ae"], e["cons"], categoria="actas_y_decisiones").json()["id"]
    assert subir(e["mc"], e["cons"], seguros, pdf(), "application/pdf").status_code == 403
    assert subir(e["mc"], e["cons"], actas, pdf(), "application/pdf").status_code == 201
    # Un documento que no existe da 404 a quien puede cargar algo, y 403 a quien no (no
    # averigua si existe).
    assert subir(e["mc"], e["cons"], str(uuid4()), pdf(), "application/pdf").status_code == 404
    assert subir(e["prop"], e["cons"], actas, pdf(), "application/pdf").status_code == 403


def test_un_mandato_de_consejo_sin_la_facultad_nueva_no_carga(escena, bucket, motor_app,
                                                               motor_migrate):  # fmt: skip
    """Los mandatos que ya existían no reciben `documentos.cargar_operativos` (criterio de 0028):
    el acta que los otorgó no la nombraba."""
    e = escena
    viejo = otra_persona(motor_migrate, e["cons"])
    alta(motor_app, e["cons"], None, decidir_equipo(motor_app, e["cons"]), persona=viejo,
         tipo="miembro_consejo", origen="soporte",
         facultades=["documentos.ver", "padron.ver"])  # fmt: skip
    assert crear(viejo, e["cons"], categoria="actas_y_decisiones").status_code == 403
    assert crear(e["mc"], e["cons"], categoria="actas_y_decisiones").status_code == 201  # control


# Descarga -------------------------------------------------------------------------------


def test_la_descarga_redirige_a_una_url_firmada_de_cinco_minutos(escena, bucket, motor_app,
                                                                 motor_migrate):  # fmt: skip
    e = escena
    doc, v, contenido = documento_con_archivo(e, motor_app, "actas_y_decisiones")
    r = descargar(e["prop"], e["cons"], doc, v)
    assert r.status_code == 302, r.text
    assert r.headers["cache-control"] == "no-store"
    destino = r.headers["location"]
    partes = urlsplit(destino)
    consulta = parse_qs(partes.query)
    assert consulta["X-Amz-Expires"] == ["300"]
    assert partes.path == f"/{bucket}/{version(motor_migrate, v)['clave_objeto']}"
    archivo = httpx.get(destino)
    assert archivo.status_code == 200, archivo.text
    assert archivo.content == contenido  # el mismo archivo, byte por byte
    assert archivo.headers["content-type"] == "application/pdf"
    assert archivo.headers["content-disposition"].startswith('attachment; filename="Doc actas')
    # Control: la misma URL sin la firma no se descarga.
    sin_firma = httpx.get(f"{partes.scheme}://{partes.netloc}{partes.path}")
    assert sin_firma.status_code == 403, sin_firma.text


def test_una_restringida_da_404_al_propietario_y_302_al_consejo(escena, bucket, motor_app,
                                                                motor_migrate):  # fmt: skip
    e = escena
    doc, v, _ = documento_con_archivo(e, motor_app, "otros")
    r = descargar(e["prop"], e["cons"], doc, v)
    assert r.status_code == 404, r.text
    assert denegadas(motor_migrate, e["cons"], "documentos.ver_restringidos") == ["sin_concesion"]
    assert descargar(e["mc"], e["cons"], doc, v).status_code == 302  # control


def test_en_cuarentena_o_rechazada_da_409(escena, bucket, motor_app, motor_migrate):
    e = escena
    r = crear(e["mc"], e["cons"], categoria="presupuestos")
    doc = r.json()["id"]
    v = subir(e["mc"], e["cons"], doc, pdf(), "application/pdf").json()["id"]
    r = descargar(e["mc"], e["cons"], doc, v)
    assert r.status_code == 409 and r.json()["codigo"] == "version_no_disponible"
    rechazada = subir(e["mc"], e["cons"], doc, pdf(js="app.alert(1)"), "application/pdf")
    analizar(motor_app)
    analizar(motor_app)
    assert version(motor_migrate, rechazada.json()["id"])["estado"] == "rechazado"
    r = descargar(e["mc"], e["cons"], doc, rechazada.json()["id"])
    assert r.status_code == 409 and r.json()["codigo"] == "version_no_disponible"
    assert descargar(e["mc"], e["cons"], doc, v).status_code == 302  # control: la limpia, sí


def test_version_ajena_o_inexistente_da_404(escena, bucket, motor_app):
    e = escena
    doc, v, _ = documento_con_archivo(e, motor_app, "seguros")
    otro_doc, otra_v, _ = documento_con_archivo(e, motor_app, "seguros")
    assert descargar(e["prop"], e["cons"], doc, otra_v).status_code == 404  # de otro documento
    assert descargar(e["prop"], e["cons"], doc, str(uuid4())).status_code == 404
    assert descargar(e["prop"], e["cons"], str(uuid4()), v).status_code == 404
    assert descargar(e["prop"], e["cons"], doc, v).status_code == 302  # control


def test_sin_el_objeto_en_el_almacen_da_410(escena, bucket, motor_app, motor_migrate):
    e = escena
    doc, v, _ = documento_con_archivo(e, motor_app, "seguros")
    assert descargar(e["prop"], e["cons"], doc, v).status_code == 302  # control: antes, sí
    _s3().delete_object(Bucket=bucket, Key=version(motor_migrate, v)["clave_objeto"])
    r = descargar(e["prop"], e["cons"], doc, v)
    assert r.status_code == 410 and r.json()["codigo"] == "archivo_vencido"


def test_con_versiones_la_url_nombra_la_version_del_objeto(escena, bucket_con_versiones,
                                                           motor_app, motor_migrate):  # fmt: skip
    e = escena
    doc, v, contenido = documento_con_archivo(e, motor_app, "seguros")
    version_objeto = version(motor_migrate, v)["version_objeto"]
    assert version_objeto
    r = descargar(e["prop"], e["cons"], doc, v)
    assert parse_qs(urlsplit(r.headers["location"]).query)["versionId"] == [version_objeto]
    assert httpx.get(r.headers["location"]).content == contenido


# Segunda barrera en la base -------------------------------------------------------------


def _como_app(motor_app, persona, cons, sql, parametros=None):
    with transaccion(motor_app, persona_id=persona, consorcio_id=cons) as c:
        return c.execute(text(sql), parametros or {}).all()


def test_la_base_vuelve_a_exigir_la_lectura(escena, bucket, motor_app, motor_migrate):
    e = escena
    doc, v, _ = documento_con_archivo(e, motor_app, "otros")
    inquilino = otra_persona(motor_migrate, e["cons"])
    vinculo(motor_migrate, e["cons"], unidad(motor_migrate, e["cons"], "8° Y"), inquilino,
            tipo="inquilino")  # fmt: skip
    for persona in (inquilino, e["ae"]):
        with pytest.raises(DBAPIError) as error:
            _como_app(motor_app, persona, e["cons"],
                      "select * from nucleo.documentos_del_consorcio()")  # fmt: skip
        assert error.value.orig.sqlstate == "42501"
    # La propietaria lee la lista, pero sin la restringida, y la base le niega su descarga.
    assert _como_app(motor_app, e["prop"], e["cons"],
                     "select * from nucleo.documentos_del_consorcio()") == []  # fmt: skip
    descarga = "select * from nucleo.version_para_descargar(:d, :v)"
    with pytest.raises(DBAPIError) as error:
        _como_app(motor_app, e["prop"], e["cons"], descarga, {"d": doc, "v": v})
    assert error.value.orig.sqlstate == "42501"
    # Control: el Consejo, con la facultad, sí.
    filas = _como_app(motor_app, e["mc"], e["cons"],
                      "select categoria from nucleo.version_para_descargar(:d, :v)",
                      {"d": doc, "v": v})  # fmt: skip
    assert [f.categoria for f in filas] == ["otros"]


def test_la_base_vuelve_a_exigir_la_carga_por_categoria(escena, bucket, motor_app):
    e = escena

    def crear_en_base(categoria):
        return _como_app(motor_app, e["mc"], e["cons"],
                         "select nucleo.crear_documento(:c, 'T', '2026-10-01', :a, 'api')",
                         {"c": categoria, "a": e["mc"]})  # fmt: skip

    with pytest.raises(DBAPIError) as error:
        crear_en_base("seguros")
    assert error.value.orig.sqlstate == "42501"
    assert len(crear_en_base("presupuestos")) == 1  # control
    with pytest.raises(DBAPIError) as error:
        _como_app(motor_app, e["prop"], e["cons"], "select nucleo.categoria_para_cargar(:d)",
                  {"d": str(uuid4())})  # fmt: skip
    assert error.value.orig.sqlstate == "42501"


def test_las_funciones_nuevas_no_las_ejecuta_public(motor_migrate):
    with motor_migrate.connect() as c:
        for funcion in (
            "nucleo.documentos_del_consorcio()",
            "nucleo.version_para_descargar(uuid, uuid)",
            "nucleo.categoria_para_cargar(uuid)",
        ):
            fila = c.execute(
                text("select has_function_privilege('public', :f, 'execute'),"
                     " has_function_privilege('cem_app', :f, 'execute')"), {"f": funcion},
            ).one()  # fmt: skip
            assert tuple(fila) == (False, True), funcion
        for funcion in (
            "nucleo.lee_documentos(uuid, uuid, boolean)",
            "nucleo.exigir_carga_documentos(uuid, uuid, text, text)",
        ):
            fila = c.execute(
                text("select has_function_privilege('cem_app', :f, 'execute')"), {"f": funcion}
            ).one()
            assert tuple(fila) == (False,), funcion


# Migración ------------------------------------------------------------------------------


def test_0036_ida_y_vuelta_y_se_niega_con_la_facultad_otorgada(base_aparte):  # noqa: F811
    cfg, motor = base_aparte
    command.upgrade(cfg, "0036")

    def estado():
        with motor.connect() as c:
            return (
                c.execute(text("select count(*) from nucleo.paquete_facultad"
                               " where facultad = 'documentos.cargar_operativos'")).scalar_one(),
                c.execute(text("select to_regprocedure("
                               "'nucleo.exigir_carga_documentos(uuid, uuid, text)')"
                               " is not null")).scalar_one(),
                c.execute(text("select to_regprocedure('nucleo.documentos_del_consorcio()')"
                               " is not null")).scalar_one(),
            )  # fmt: skip

    assert estado() == (1, False, True)
    command.downgrade(cfg, "0035")
    assert estado() == (0, True, False)
    command.upgrade(cfg, "0036")  # control: vuelve a subir limpio
    assert estado() == (1, False, True)
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
                 " vigente_desde, origen) values (:c, 'miembro_consejo', :p, :d, '2026-01-01',"
                 " 'soporte') returning id"), {"c": cons, "p": per, "d": dec},
        ).scalar_one()  # fmt: skip
        c.execute(
            text("insert into nucleo.facultad_otorgada (consorcio_id, mandato_id, facultad,"
                 " decision_id) values (:c, :m, 'documentos.cargar_operativos', :d)"),
            {"c": cons, "m": m, "d": dec},
        )  # fmt: skip
    with pytest.raises(DBAPIError) as error:
        command.downgrade(cfg, "0035")
    assert error.value.orig.diag.message_hint == "downgrade_con_datos"
