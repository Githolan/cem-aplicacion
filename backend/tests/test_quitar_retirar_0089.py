"""Quitar un archivo subido por error y retirar un documento (`0089`; pedido del propietario del
08-11-2026: «hacé 1 y 2»).

Contra servicios reales: PostgreSQL, el almacén S3 de pruebas y ClamAV. Nada se borra: cada prueba
que dice que algo salió de la lista mira también que la fila, sus versiones y el objeto siguen.
Cada negación lleva su control: el mismo pedido de quien sí puede, o sobre un documento sin citas,
pasa. Datos SINTETICOS / NO REAL.
"""

import importlib.util
from uuid import UUID, uuid4

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db import transaccion
from tests.conftest import RAIZ, clave_idempotencia
from tests.test_contexto_mandatos import cliente as cliente_documentos
from tests.test_documentos_2c1 import (  # noqa: F401  (`bucket` y `firmas_de_la_imagen` son fixtures)
    analizar,
    bucket,
    existe,
    firmas_de_la_imagen,
    pdf,
    url,
)
from tests.test_exportacion_2c3 import (  # noqa: F401  (`edificio`, `con_url`, `claves`: fixtures)
    claves,
    con_url,
    descifrar,
    edificio,
    entidades,
    exportar,
)
from tests.test_lectura_carga_0088 import documento_comun, factura, para_carga, subir
from tests.test_orden_pago_neutra import cbu_sintetico, versiones
from tests.test_rutas_egresos import ORDEN, codigo, con_cuit, get, post, preparar
from tests.test_rutas_egresos import cliente as con_sesion
from tests.test_rutas_egresos import e as e  # noqa: F401  (es un fixture)
from tests.test_vinculo_0014 import base_aparte  # noqa: F401  (es un fixture)

pytestmark = [pytest.mark.db, pytest.mark.redis]
# Los fixtures importados no se redefinen con el parámetro de su nombre.
# ruff: noqa: F811

MIGRACION = RAIZ / "migrations" / "versions" / "0089_quitar_y_retirar_documentos.py"
MOTIVO = "Póliza vencida: la reemplaza la del período nuevo"


def _migracion():
    spec = importlib.util.spec_from_file_location("migracion_0089", MIGRACION)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def quitar(e, doc, quien: UUID | None = None):
    quien = quien or e.adm
    with con_sesion(quien, e.sesion[quien]) as c:
        return c.post(f"/api/v1/consorcios/{e.cons}/documentos/{doc}/quitar",
                      headers=clave_idempotencia())  # fmt: skip


def retirar(e, doc, motivo: str = MOTIVO, quien: UUID | None = None):
    return post(e, quien or e.adm, f"documentos/{doc}/retiro", {"motivo": motivo})


def en_la_lista(e, quien: UUID | None = None) -> set[str]:
    r = get(e, quien or e.adm, "documentos")
    assert r.status_code == 200, r.text
    return {d["id"] for d in r.json()["documentos"]}


def documento(motor_migrate, doc) -> dict:
    with motor_migrate.connect() as c:
        return dict(c.execute(text("select * from nucleo.documento where id = :d"),
                              {"d": doc}).mappings().one())  # fmt: skip


def versiones_de(motor_migrate, doc) -> list[dict]:
    with motor_migrate.connect() as c:
        return [dict(f) for f in c.execute(
            text("select id, estado, sha256, clave_objeto from nucleo.version_documento"
                 " where documento_id = :d order by subida_en"), {"d": doc},
        ).mappings()]  # fmt: skip


def eventos(motor_migrate, doc) -> list[tuple[str, dict]]:
    with motor_migrate.connect() as c:
        return [tuple(f) for f in c.execute(
            text("select accion, despues from auditoria.eventos where entidad_id = :d"
                 " order by id"), {"d": doc},
        )]  # fmt: skip


def comun_con_archivo(
    motor_app, e, categoria: str = "seguros", archivo: bytes | None = None
) -> tuple[str, str]:
    """Un documento cargado desde Documentos, con su archivo aprobado."""
    doc = documento_comun(e, categoria)
    version = subir(e, doc, archivo)
    assert analizar(motor_app) == 1
    return doc, version


# Quitar ----------------------------------------------------------------------------------------


def test_quitar_saca_el_documento_de_la_lista_y_no_borra_nada(motor_migrate, motor_app, e, bucket):
    doc, version = factura(motor_app, e)
    otro, _ = factura(motor_app, e)
    assert {doc, otro} <= en_la_lista(e)  # control: antes, los dos
    antes = versiones_de(motor_migrate, doc)
    r = quitar(e, doc)
    assert r.status_code == 200, r.text
    assert r.json() == {"id": doc}
    lista = en_la_lista(e)
    assert doc not in lista and otro in lista
    fila = documento(motor_migrate, doc)
    assert fila["estado"] == "descartado" and fila["descartado_en"] is not None
    assert fila["retirado_en"] is None
    # La versión no cambia y el archivo sigue en el almacén.
    assert versiones_de(motor_migrate, doc) == antes
    assert existe(bucket, antes[0]["clave_objeto"])
    r = get(e, e.adm, f"documentos/{doc}/versiones/{version}/descargar")
    assert codigo(r) == (404, "no_encontrado")
    assert [a for a, _ in eventos(motor_migrate, doc)][-1] == "consorcio.documento.quitar"
    assert eventos(motor_migrate, doc)[-1][1] == {"categoria": "facturas_y_comprobantes"}


def test_quitar_tambien_mientras_lo_revisa_el_antivirus(motor_migrate, e, bucket):
    doc = para_carga(e).json()["id"]
    subir(e, doc)  # sin analizar: en cuarentena
    assert quitar(e, doc).status_code == 200
    assert documento(motor_migrate, doc)["estado"] == "descartado"


def test_quitar_dos_veces_es_404_y_el_reintento_con_la_misma_clave_es_la_misma_respuesta(
    motor_migrate, motor_app, e, bucket
):
    doc, _ = factura(motor_app, e)
    clave = clave_idempotencia()
    with con_sesion(e.adm, e.sesion[e.adm]) as c:
        url = f"/api/v1/consorcios/{e.cons}/documentos/{doc}/quitar"
        primero, reintento = c.post(url, headers=clave), c.post(url, headers=clave)
        otra = c.post(url, headers=clave_idempotencia())
    assert primero.status_code == reintento.status_code == 200
    assert reintento.json() == primero.json()
    assert codigo(otra) == (404, "no_encontrado")
    assert [a for a, _ in eventos(motor_migrate, doc)].count("consorcio.documento.quitar") == 1


@pytest.mark.parametrize("ajeno", ["documento", "version"])
def test_lo_que_subio_otro_no_se_quita(motor_migrate, motor_app, e, bucket, ajeno):
    doc, _ = factura(motor_app, e)
    control, _ = factura(motor_app, e)
    # Datos sintéticos: otra persona creó el documento, o subió una de sus versiones.
    with motor_migrate.begin() as c:
        if ajeno == "documento":
            c.execute(text("update nucleo.documento set cargado_por = :p where id = :d"),
                      {"p": e.mc1, "d": doc})  # fmt: skip
        else:
            c.execute(
                text("insert into nucleo.version_documento (consorcio_id, documento_id, tipo,"
                     " tamano, sha256, clave_objeto, subida_por) values (:c, :d,"
                     " 'application/pdf', 10, :s, :k, :p)"),
                {"c": e.cons, "d": doc, "s": "a" * 64, "k": f"cem/sintetica/{uuid4()}",
                 "p": e.mc1},
            )  # fmt: skip
    assert codigo(quitar(e, doc)) == (403, "no_es_quien_lo_subio")
    assert documento(motor_migrate, doc)["estado"] == "vigente"
    assert quitar(e, control).status_code == 200  # control: lo suyo sí


def test_un_documento_comun_no_se_quita(motor_migrate, motor_app, e, bucket):
    """Quitar es solo para el archivo de un formulario; lo de Documentos se retira."""
    doc, _ = comun_con_archivo(motor_app, e, "facturas_y_comprobantes")
    assert codigo(quitar(e, doc)) == (404, "no_encontrado")
    assert documento(motor_migrate, doc)["estado"] == "vigente"


def test_sin_preparar_ordenes_no_se_quita(motor_app, e, bucket):
    doc, _ = factura(motor_app, e)
    assert quitar(e, doc, e.mc1).status_code == 403
    assert quitar(e, doc).status_code == 200  # control


def test_la_factura_de_una_orden_no_se_quita_ni_se_retira(motor_migrate, motor_app, e, bucket):
    doc, version = factura(motor_app, e)
    libre, _ = factura(motor_app, e)
    o = preparar(e, factura_version_id=version)
    antes = versiones(motor_migrate, o)
    assert codigo(quitar(e, doc)) == (409, "documento_referenciado")
    assert codigo(retirar(e, doc)) == (409, "documento_referenciado")
    # La orden, su factura y su huella no cambian; el documento sigue vigente y en la lista.
    assert versiones(motor_migrate, o) == antes
    assert antes[0]["factura_version_id"] == UUID(version)
    assert documento(motor_migrate, doc)["estado"] == "vigente"
    assert doc in en_la_lista(e)
    assert quitar(e, libre).status_code == 200  # control: sin orden, sí


def test_el_documento_de_un_proveedor_no_se_quita(motor_app, e, bucket):
    doc = para_carga(e, "proveedor").json()["id"]
    subir(e, doc)
    assert analizar(motor_app) == 1
    r = post(e, e.adm, "proveedores", con_cuit() | {"documento_id": doc})
    assert r.status_code == 201, r.text
    assert codigo(quitar(e, doc)) == (409, "documento_referenciado")
    assert codigo(retirar(e, doc)) == (409, "documento_referenciado")


def test_un_documento_quitado_no_respalda_una_orden_nueva(motor_app, e, bucket):
    doc, version = factura(motor_app, e)
    assert quitar(e, doc).status_code == 200
    r = post(
        e,
        e.adm,
        "ordenes-pago",
        ORDEN | {"proveedor_id": str(e.proveedor), "factura_version_id": version},
    )
    assert codigo(r) == (422, "documento_invalido")


# Retirar ---------------------------------------------------------------------------------------


def test_retirar_saca_el_documento_de_la_lista_y_deja_la_constancia(
    motor_migrate, motor_app, e, bucket
):
    doc, version = comun_con_archivo(motor_app, e)
    otro, _ = comun_con_archivo(motor_app, e)
    assert {doc, otro} <= en_la_lista(e) and doc in en_la_lista(e, e.mc1)  # control
    antes = versiones_de(motor_migrate, doc)
    r = retirar(e, doc, f"  {MOTIVO}  ")
    assert r.status_code == 200, r.text
    assert doc not in en_la_lista(e) and otro in en_la_lista(e)
    assert doc not in en_la_lista(e, e.mc1)
    fila = documento(motor_migrate, doc)
    assert fila["estado"] == "retirado"
    assert (fila["retirado_por"], fila["retirado_motivo"]) == (e.adm, MOTIVO)
    assert fila["retirado_en"] is not None and fila["descartado_en"] is None
    assert versiones_de(motor_migrate, doc) == antes
    assert existe(bucket, antes[0]["clave_objeto"])
    r = get(e, e.adm, f"documentos/{doc}/versiones/{version}/contenido")
    assert codigo(r) == (404, "no_encontrado")
    accion, despues = eventos(motor_migrate, doc)[-1]
    # El motivo es texto libre: no va a la auditoría.
    assert (accion, despues) == ("consorcio.documento.retirar", {"categoria": "seguros"})
    assert codigo(retirar(e, doc)) == (404, "no_encontrado")  # ya no está vigente


def test_el_reintento_de_un_retiro_hecho_recibe_la_misma_respuesta(
    motor_migrate, motor_app, e, bucket
):
    doc, _ = comun_con_archivo(motor_app, e)
    clave = clave_idempotencia()
    primero = post(e, e.adm, f"documentos/{doc}/retiro", {"motivo": MOTIVO}, clave)
    reintento = post(e, e.adm, f"documentos/{doc}/retiro", {"motivo": MOTIVO}, clave)
    assert primero.status_code == reintento.status_code == 200, reintento.text
    assert reintento.json() == primero.json() == {"id": doc}
    # Control: otra clave sobre el documento ya retirado se niega.
    assert codigo(retirar(e, doc)) == (404, "no_encontrado")
    assert [a for a, _ in eventos(motor_migrate, doc)].count("consorcio.documento.retirar") == 1


def test_tambien_se_retira_un_documento_para_carga_que_nadie_cita(motor_migrate, motor_app, e,
                                                                  bucket):  # fmt: skip
    doc, _ = factura(motor_app, e)
    assert retirar(e, doc).status_code == 200
    assert documento(motor_migrate, doc)["estado"] == "retirado"


def test_sin_cargar_la_categoria_no_se_retira(motor_migrate, motor_app, e, bucket):
    doc, _ = comun_con_archivo(motor_app, e)
    assert doc in en_la_lista(e, e.mc1)  # el Consejo lo ve, pero no carga seguros
    assert retirar(e, doc, quien=e.mc1).status_code == 403
    assert documento(motor_migrate, doc)["estado"] == "vigente"
    assert retirar(e, doc).status_code == 200  # control: la administración sí


@pytest.mark.parametrize(
    "motivo", ["", "   ", "x" * 301, "Llamar al 11 5555-0000", "escribir a a@b.invalid"]
)
def test_el_motivo_es_obligatorio_y_sin_contactos(motor_migrate, motor_app, e, bucket, motivo):
    doc, _ = comun_con_archivo(motor_app, e)
    assert retirar(e, doc, motivo).status_code == 422
    assert documento(motor_migrate, doc)["estado"] == "vigente"
    assert retirar(e, doc, "x" * 300).status_code == 200  # control: 300 entra


def test_sin_motivo_tampoco_en_la_base(motor_migrate, motor_app, e, bucket):
    doc, _ = comun_con_archivo(motor_app, e)
    for motivo in (None, " "):
        with pytest.raises(DBAPIError) as error:
            with transaccion(motor_app, persona_id=e.adm, consorcio_id=e.cons) as c:
                c.execute(text("select nucleo.retirar_documento(:d, :m, :a, 'api')"),
                          {"d": doc, "m": motivo, "a": e.adm})  # fmt: skip
        assert error.value.orig.diag.sqlstate == "23514"
    assert documento(motor_migrate, doc)["estado"] == "vigente"


def test_una_cuenta_en_el_motivo_no_entra_en_la_base(motor_migrate, motor_app, e, bucket):
    doc, _ = comun_con_archivo(motor_app, e)
    with pytest.raises(DBAPIError) as error:
        with transaccion(motor_app, persona_id=e.adm, consorcio_id=e.cons) as c:
            c.execute(text("select nucleo.retirar_documento(:d, :m, :a, 'api')"),
                      {"d": doc, "m": f"Pagado a {cbu_sintetico(7)}", "a": e.adm})  # fmt: skip
    assert error.value.orig.diag.message_hint == "motivo_con_cuenta"
    assert documento(motor_migrate, doc)["estado"] == "vigente"


def test_un_documento_dado_de_baja_no_cambia_mas(motor_migrate, motor_app, e, bucket):
    doc, _ = comun_con_archivo(motor_app, e)
    assert retirar(e, doc).status_code == 200
    for cambio in ("estado = 'vigente', retirado_en = null, retirado_por = null,"
                   " retirado_motivo = null", "titulo = 'Otro título'"):  # fmt: skip
        with pytest.raises(DBAPIError) as error:
            with motor_migrate.begin() as c:
                c.execute(text(f"update nucleo.documento set {cambio} where id = :d"), {"d": doc})
        assert error.value.orig.diag.message_hint == "documento_dado_de_baja"


# Lo que cita un documento ----------------------------------------------------------------------


def test_cada_tabla_que_cita_documentos_esta_en_la_lista(motor_migrate):
    """Una tabla nueva con una clave foránea a documentos no pasa sin decidir si los cita."""
    m = _migracion()
    with motor_migrate.connect() as c:
        en_el_catalogo = set(c.execute(text(
            "select distinct conrelid::regclass::text from pg_constraint where contype = 'f'"
            " and confrelid in ('nucleo.documento'::regclass,"
            "                   'nucleo.version_documento'::regclass)"
        )).scalars())  # fmt: skip
        con_disparador = set(c.execute(text(
            "select tgrelid::regclass::text from pg_trigger"
            " where tgname = 'documento_citado_vigente'"
        )).scalars())  # fmt: skip
    citan = {t for t, _ in m.REFERENCIAS}
    # Control: la búsqueda encuentra las dos que se sabe que no citan.
    assert set(m.NO_CITAN) <= en_el_catalogo
    assert en_el_catalogo == citan | set(m.NO_CITAN)
    assert con_disparador == citan


def test_la_cita_de_un_documento_retirado_se_niega(motor_migrate, motor_app, e, bucket):
    """El disparador, antes que la clave foránea: con un documento vigente la misma fila llega a la
    clave foránea (la ejecución no existe) y falla por eso."""
    retirado, _ = comun_con_archivo(motor_app, e)
    vigente, _ = comun_con_archivo(motor_app, e)
    assert retirar(e, retirado).status_code == 200
    insertar = text("insert into nucleo.comprobante_pago_manual (consorcio_id, documento_id,"
                    " ejecucion_id) values (:c, :d, :x)")  # fmt: skip
    with pytest.raises(DBAPIError) as error:
        with motor_migrate.begin() as c:
            c.execute(insertar, {"c": e.cons, "d": retirado, "x": uuid4()})
    assert error.value.orig.diag.message_hint == "documento_invalido"
    with pytest.raises(DBAPIError) as control:
        with motor_migrate.begin() as c:
            c.execute(insertar, {"c": e.cons, "d": vigente, "x": uuid4()})
    assert control.value.orig.diag.sqlstate == "23503"


def test_el_acta_de_una_decision_no_se_retira(motor_migrate, motor_app, e, bucket):
    doc, _ = comun_con_archivo(motor_app, e, "institucional")
    # Otro archivo: el mismo contenido tiene el mismo sha256, y sería el acta también.
    libre, _ = comun_con_archivo(motor_app, e, "institucional", pdf(flujo=b"0.6 g 0 0 9 9 re f"))
    [v] = versiones_de(motor_migrate, doc)
    with motor_migrate.connect() as c:
        decision = c.execute(
            text("select id from nucleo.decision_formal where consorcio_id = :c limit 1"),
            {"c": e.cons},
        ).scalar_one()  # fmt: skip
    # Datos sintéticos: esa decisión registra como acta el archivo de este documento.
    with motor_migrate.begin() as c:
        c.execute(text("update nucleo.decision_formal set sha256_acta = :s where id = :d"),
                  {"s": v["sha256"], "d": decision})  # fmt: skip
    assert codigo(retirar(e, doc)) == (409, "documento_referenciado")
    assert retirar(e, libre).status_code == 200  # control


def decision_de(motor_migrate, e) -> UUID:
    with motor_migrate.connect() as c:
        return c.execute(
            text("select id from nucleo.decision_formal where consorcio_id = :c limit 1"),
            {"c": e.cons},
        ).scalar_one()


REGISTRAR_CON_ACTA = text(
    "select nucleo.registrar_decision('asamblea', date '2026-01-01', 'Aprueba SINTETICO',"
    " 'Acta 99, libro 9, folio 9', null, 'soporte', :s)"
)
ACTA_DE_LA_DECISION = text("update nucleo.decision_formal set sha256_acta = :s where id = :d")


def test_no_se_registra_una_decision_con_el_acta_de_un_documento_dado_de_baja(
    motor_migrate, motor_app, e, bucket
):
    retirado, _ = comun_con_archivo(motor_app, e, "actas_y_decisiones")
    vigente, _ = comun_con_archivo(motor_app, e, "actas_y_decisiones", pdf(flujo=b"0 0 5 5 re f"))
    [del_retirado] = versiones_de(motor_migrate, retirado)
    [del_vigente] = versiones_de(motor_migrate, vigente)
    assert retirar(e, retirado).status_code == 200
    with pytest.raises(DBAPIError) as error:
        with transaccion(motor_app, persona_id=None, consorcio_id=e.cons) as c:
            c.execute(REGISTRAR_CON_ACTA, {"s": del_retirado["sha256"]})
    assert error.value.orig.diag.message_hint == "acta_dada_de_baja"
    # Control: con el acta de un documento vigente, la decisión se registra.
    with transaccion(motor_app, persona_id=None, consorcio_id=e.cons) as c:
        assert c.execute(REGISTRAR_CON_ACTA, {"s": del_vigente["sha256"]}).scalar_one()
    # Y ese documento ya no se retira: es el acta de una decisión.
    assert codigo(retirar(e, vigente)) == (409, "documento_referenciado")


def test_retirar_y_registrar_el_acta_se_esperan(motor_migrate, motor_app, e, bucket):
    """Dos conexiones: quien llega primero bloquea el documento y el otro espera (acá, con
    `lock_timeout`, falla por la espera) y después ve lo que hizo el primero."""
    doc, _ = comun_con_archivo(motor_app, e, "actas_y_decisiones")
    otro, _ = comun_con_archivo(motor_app, e, "actas_y_decisiones", pdf(flujo=b"0 0 7 7 re f"))
    [v], [w] = versiones_de(motor_migrate, doc), versiones_de(motor_migrate, otro)
    decision = decision_de(motor_migrate, e)
    retiro = text("select nucleo.retirar_documento(:d, :m, :a, 'api')")
    # Retirar primero: registrar el acta espera y, cuando el retiro se confirma, se niega.
    with transaccion(motor_app, persona_id=e.adm, consorcio_id=e.cons) as a:
        a.execute(retiro, {"d": doc, "m": MOTIVO, "a": e.adm})
        with pytest.raises(DBAPIError) as espera:
            with motor_migrate.begin() as b:
                b.execute(text("set local lock_timeout = '500ms'"))
                b.execute(ACTA_DE_LA_DECISION, {"s": v["sha256"], "d": decision})
        assert espera.value.orig.diag.sqlstate == "55P03"
    with pytest.raises(DBAPIError) as despues:
        with motor_migrate.begin() as b:
            b.execute(ACTA_DE_LA_DECISION, {"s": v["sha256"], "d": decision})
    assert despues.value.orig.diag.message_hint == "acta_dada_de_baja"
    # Registrar primero: retirar espera y, cuando el acta se confirma, se niega.
    with motor_migrate.begin() as b:
        b.execute(ACTA_DE_LA_DECISION, {"s": w["sha256"], "d": decision})
        with pytest.raises(DBAPIError) as espera:
            with transaccion(motor_app, persona_id=e.adm, consorcio_id=e.cons) as a:
                a.execute(text("set local lock_timeout = '500ms'"))
                a.execute(retiro, {"d": otro, "m": MOTIVO, "a": e.adm})
        assert espera.value.orig.diag.sqlstate == "55P03"
    assert codigo(retirar(e, otro)) == (409, "documento_referenciado")
    assert documento(motor_migrate, otro)["estado"] == "vigente"


def test_las_funciones_y_quien_las_ejecuta(motor_migrate):
    m = _migracion()
    with motor_migrate.connect() as c:
        for funcion in m.RUTAS:
            assert c.execute(text("select has_function_privilege('cem_app', :f, 'execute')"),
                             {"f": funcion}).scalar_one(), funcion  # fmt: skip
        for funcion in m.INTERNAS:
            assert not c.execute(text("select has_function_privilege('cem_app', :f, 'execute')"),
                                 {"f": funcion}).scalar_one(), funcion  # fmt: skip


# Exportación -----------------------------------------------------------------------------------


def test_la_exportacion_lleva_el_retirado_con_su_estado_y_no_el_descartado(
    edificio, con_url, claves, tmp_path, motor_migrate
):
    ed = edificio
    por_titulo = {}
    with motor_migrate.connect() as c:
        for i, t in c.execute(text("select id::text, titulo from nucleo.documento"
                                   " where consorcio_id = :c"), {"c": ed["cons"]}):  # fmt: skip
            por_titulo[t] = i
    plano, acta = por_titulo["Plano"], por_titulo["Acta de asamblea"]
    with cliente_documentos(ed["ad"]) as c:
        r = c.post(url(ed["cons"], f"/{plano}/retiro"), json={"motivo": MOTIVO},
                   headers=clave_idempotencia())  # fmt: skip
    assert r.status_code == 200, r.text
    # Datos sintéticos: el acta, como si quien la subió la hubiera quitado.
    with motor_migrate.begin() as c:
        c.execute(text("update nucleo.documento set estado = 'descartado', descartado_en = now()"
                       " where id = :d"), {"d": acta})  # fmt: skip
    resultado, salida = exportar(ed, claves, tmp_path)
    assert resultado == 0
    paquete = descifrar(salida, claves["privada"])
    datos = entidades(paquete)
    documentos = {d["id"]: d for d in datos["documento"]}
    assert documentos[plano]["estado"] == "retirado"
    assert documentos[plano]["retirado_motivo"] == MOTIVO
    assert documentos[plano]["retirado_en"] is not None
    assert acta not in documentos
    # Control: el vigente sigue, con su estado.
    assert {d["estado"] for i, d in documentos.items() if i != plano} == {"vigente"}
    versiones_exportadas = {v["documento_id"] for v in datos["version"]}
    archivos = " ".join(paquete.namelist())
    assert plano in versiones_exportadas and acta not in versiones_exportadas
    del_plano = [v for v, b in ed["binarios"].items() if v in archivos]
    assert len(del_plano) == 1  # el binario del plano va; el del acta no
    assert all(ev["accion"] != "consorcio.documento.retirar" for ev in datos["evento"])
    assert any(ev["accion"] == "Retiró un documento" for ev in datos["evento"]), datos["evento"]


# Migración -------------------------------------------------------------------------------------


def test_0089_ida_y_vuelta(base_aparte):
    cfg, motor = base_aparte
    command.upgrade(cfg, "0089")
    columnas = text(
        "select count(*) from information_schema.columns where table_schema = 'nucleo'"
        " and table_name = 'documento' and column_name = 'retirado_motivo'"
    )
    with motor.connect() as c:
        assert c.execute(columnas).scalar_one() == 1
    command.downgrade(cfg, "-1")
    with motor.connect() as c:
        assert c.execute(columnas).scalar_one() == 0
        assert c.execute(text("select to_regprocedure('nucleo.quitar_documento(uuid, uuid, text,"
                              " uuid)')")).scalar() is None  # fmt: skip
    command.upgrade(cfg, "0089")
