"""El comprobante en un paso, verlo en la pantalla y corregir con lo cargado (`0073`).

Diseño: `Rediseno_Circuito_Pagos_2026-11-06.md`, §4, §8, §10 y §14, repositorio CEM. Contra servicios
reales: PostgreSQL, el almacén S3 de pruebas y ClamAV. Datos SINTETICOS / NO REAL: ninguna cuenta
es de un banco (prefijo 999) y no se mueve dinero. La escena de pagos es la de
`test_pago_manual_0055`: la administración prepara y ejecuta, mc1 aprueba y mc2 concilia.
"""

from uuid import UUID, uuid4

import pytest
from alembic import command
from sqlalchemy import text

from tests import test_documentos_2c1 as carga
from tests.conftest import clave_idempotencia
from tests.test_circuito_pagos_0072 import (
    ficha,
    observada,
    segundo_ejecutor,
)
from tests.test_comprobantes_0063 import CREAR, TITULO
from tests.test_comprobantes_0065 import ver_version
from tests.test_condicion_habilitante_consejo import unidad, vinculo
from tests.test_contexto_mandatos import cliente
from tests.test_documentos_2c1 import (  # noqa: F401  (`bucket` y `firmas_de_la_imagen` son fixtures)
    _s3,
    analizar,
    bucket,
    bucket_con_versiones,
    crear,
    firmas_de_la_imagen,
    pdf,
    subir,
    url,
    version,
)
from tests.test_documentos_2c2 import denegadas, documento_con_archivo
from tests.test_egresos_1a import _hoy
from tests.test_mandatos import otra_persona
from tests.test_modo_operacion import cambiar, consorcio_con_persona, decidir_equipo
from tests.test_orden_pago_neutra import (
    APROBAR,
    EMITIR,
    MODIFICAR,
    huella,
    llamar,
    niega,
    nueva_sesion,
    orden,
)
from tests.test_ordenes_trabadas_0067 import dar_de_baja, por_aclarar
from tests.test_pago_manual_0055 import (
    CONFIRMAR,
    CUENTA,
    DECLARAR,
    IMPORTE,
    REGISTRAR,
    comprobante,
    emitida,
    evidencia,
    registrada,
    ver,
)
from tests.test_pago_manual_0055 import e as e  # noqa: F401  (es un fixture)
from tests.test_registro_sin_facultad_0068 import VERSION
from tests.test_rutas_egresos import cliente as con_sesion
from tests.test_rutas_egresos import codigo, get, post
from tests.test_vinculo_0014 import base_aparte  # noqa: F401  (es un fixture)

pytestmark = [pytest.mark.db, pytest.mark.redis]

escena = carga.escena
ANTIVIRUS_VIEJO = {"horas_atras": 1, "totp_min_atras": 11}
"""Una sesión de hace una hora cuyo gesto ya venció."""
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
# `base_aparte`, `bucket` y `e` son fixtures importados: el parámetro con su nombre no los redefine.
# ruff: noqa: F811


def contenido(persona, cons, doc, v):
    with cliente(persona) as c:
        return c.get(url(cons, f"/{doc}/versiones/{v}/contenido"))


# Ver el archivo en la pantalla ----------------------------------------------------------------


def test_contenido_sirve_el_archivo_con_los_encabezados_de_4(escena, bucket, motor_app):
    e = escena
    doc, v, archivo = documento_con_archivo(e, motor_app, "seguros")
    r = contenido(e["prop"], e["cons"], doc, v)
    assert r.status_code == 200, r.text
    assert r.content == archivo  # el mismo archivo, byte por byte
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["content-disposition"] == "inline"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["content-security-policy"] == "sandbox; default-src 'none'"
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["content-length"] == str(len(archivo))


def test_el_tipo_es_el_verificado_al_subirlo(escena, bucket, motor_app, motor_migrate):
    """Un archivo no elige cómo se sirve: aunque el declarado sea otro, sale el de la base."""
    e = escena
    doc, v, _ = documento_con_archivo(e, motor_app, "seguros")
    with motor_migrate.begin() as c:
        c.execute(text("update nucleo.version_documento set tipo = 'image/png' where id = :v"),
                  {"v": v})  # fmt: skip
    r = contenido(e["prop"], e["cons"], doc, v)
    assert r.headers["content-type"] == "image/png"


def test_otro_consorcio_no_lo_ve(escena, bucket, motor_app, motor_migrate):
    e = escena
    doc, v, _ = documento_con_archivo(e, motor_app, "seguros")
    otro, _, ajena = consorcio_con_persona(motor_migrate)
    cambiar(motor_app, otro, "consejo", decidir_equipo(motor_app, otro))
    assert contenido(ajena, e["cons"], doc, v).status_code == 404  # fuera de su ámbito
    assert contenido(ajena, otro, doc, v).status_code == 404  # su ámbito, documento ajeno
    assert contenido(e["prop"], e["cons"], doc, v).status_code == 200  # control


def test_sin_la_facultad_403_y_queda_auditado(escena, bucket, motor_app, motor_migrate):
    e = escena
    doc, v, _ = documento_con_archivo(e, motor_app, "seguros")
    inquilino = otra_persona(motor_migrate, e["cons"])
    vinculo(motor_migrate, e["cons"], unidad(motor_migrate, e["cons"], "9° Z"), inquilino,
            tipo="inquilino")  # fmt: skip
    r = contenido(inquilino, e["cons"], doc, v)
    assert r.status_code == 403 and b"%PDF" not in r.content, r.text
    assert contenido(e["ae"], e["cons"], doc, v).status_code == 403  # solo carga
    assert denegadas(motor_migrate, e["cons"], "documentos.ver") == ["sin_concesion"] * 2
    assert contenido(e["mc"], e["cons"], doc, v).status_code == 200  # control


def test_en_cuarentena_o_rechazada_no_se_sirve(escena, bucket, motor_app, motor_migrate):
    e = escena
    doc = crear(e["mc"], e["cons"], categoria="presupuestos").json()["id"]
    archivo = pdf()
    v = subir(e["mc"], e["cons"], doc, archivo, "application/pdf").json()["id"]
    r = contenido(e["mc"], e["cons"], doc, v)
    assert codigo(r) == (409, "version_no_disponible")
    assert archivo not in r.content
    assert contenido(e["prop"], e["cons"], doc, v).status_code == 404  # de otro: no existe
    rechazada = subir(e["mc"], e["cons"], doc, pdf(js="app.alert(1)"), "application/pdf")
    analizar(motor_app)
    analizar(motor_app)
    assert version(motor_migrate, rechazada.json()["id"])["estado"] == "rechazado"
    r = contenido(e["mc"], e["cons"], doc, rechazada.json()["id"])
    assert codigo(r) == (409, "version_no_disponible")
    r = contenido(e["prop"], e["cons"], doc, v)  # control: la limpia, sí
    assert (r.status_code, r.content) == (200, archivo)


def test_una_restringida_da_404_al_propietario(escena, bucket, motor_app):
    e = escena
    doc, v, _ = documento_con_archivo(e, motor_app, "otros")
    assert contenido(e["prop"], e["cons"], doc, v).status_code == 404
    assert contenido(e["mc"], e["cons"], doc, v).status_code == 200  # control


def test_version_de_otro_documento_da_404(escena, bucket, motor_app):
    e = escena
    doc, v, _ = documento_con_archivo(e, motor_app, "seguros")
    _, otra_v, _ = documento_con_archivo(e, motor_app, "seguros")
    assert contenido(e["prop"], e["cons"], doc, otra_v).status_code == 404
    assert contenido(e["prop"], e["cons"], doc, str(uuid4())).status_code == 404
    assert contenido(e["prop"], e["cons"], doc, v).status_code == 200  # control


def test_sin_el_objeto_en_el_almacen_da_410(escena, bucket, motor_app, motor_migrate):
    e = escena
    doc, v, _ = documento_con_archivo(e, motor_app, "seguros")
    assert contenido(e["prop"], e["cons"], doc, v).status_code == 200  # control: antes, sí
    _s3().delete_object(Bucket=bucket, Key=version(motor_migrate, v)["clave_objeto"])
    r = contenido(e["prop"], e["cons"], doc, v)
    assert codigo(r) == (410, "archivo_vencido")


def test_con_versiones_sirve_la_version_del_objeto(
    escena, bucket_con_versiones, motor_app, motor_migrate
):
    e = escena
    doc, v, archivo = documento_con_archivo(e, motor_app, "seguros")
    fila = version(motor_migrate, v)
    assert fila["version_objeto"]
    _s3().put_object(Bucket=bucket_con_versiones, Key=fila["clave_objeto"], Body=b"otra cosa")
    assert contenido(e["prop"], e["cons"], doc, v).content == archivo


# Subir sin gesto ------------------------------------------------------------------------------


def subida_por_http(motor_app, e, o, quien=None, archivo=None) -> tuple[str, str]:
    """Crea el comprobante desde el pago y sube el archivo, por las dos rutas, sin analizarlo."""
    quien = quien or e.adm
    r = post(e, quien, f"ordenes-pago/{o}/pago-manual/comprobante",
             {"titulo": TITULO, "fecha": "2026-11-05"})  # fmt: skip
    assert r.status_code == 201, r.text
    doc = r.json()["id"]
    with con_sesion(quien, e.sesion[quien]) as c:
        r = c.post(f"/api/v1/consorcios/{e.cons}/documentos/{doc}/versiones",
                   content=archivo or pdf(),
                   headers={"Content-Type": "application/pdf"} | clave_idempotencia())  # fmt: skip
    assert r.status_code == 201, r.text
    return doc, r.json()["id"]


def registro(motor_migrate, e, o, doc, **cambios) -> dict:
    return {
        "version": 1, "huella": huella(motor_migrate, o, 1).hex(),
        "numero_operacion": f"OP-{uuid4().hex[:10]}",
        "fecha_operacion": str(_hoy(motor_migrate, e)), "importe_centavos": IMPORTE,
        "moneda": "ARS", "cuenta_destino": CUENTA, "documento_id": str(doc),
    } | cambios  # fmt: skip


def test_por_http_subir_no_pide_el_gesto_y_registrar_si(motor_migrate, motor_app, e, bucket):
    o = emitida(motor_migrate, e)
    ver(motor_migrate, e, e.adm, o)
    e.sesion[e.adm] = nueva_sesion(motor_migrate, e.adm, **ANTIVIRUS_VIEJO)
    doc, _ = subida_por_http(motor_app, e, o)
    assert analizar(motor_app) == 1
    r = post(e, e.adm, f"ordenes-pago/{o}/pago-manual", registro(motor_migrate, e, o, doc))
    assert codigo(r) == (403, "segundo_factor_requerido")  # control: registrar lo sigue pidiendo
    e.sesion[e.adm] = nueva_sesion(motor_migrate, e.adm)
    r = post(e, e.adm, f"ordenes-pago/{o}/pago-manual", registro(motor_migrate, e, o, doc))
    assert r.status_code == 201, r.text


def test_en_la_base_subir_no_pide_el_gesto_y_sigue_pidiendo_todo_lo_demas(
    motor_migrate, motor_app, e
):
    o = emitida(motor_migrate, e)
    # Sin haber visto la cuenta, tampoco sin gesto.
    sin_gesto = nueva_sesion(motor_migrate, e.adm, **ANTIVIRUS_VIEJO)
    niega(motor_migrate, e, e.adm, CREAR, "no_es_quien_ejecuto", sesion=sin_gesto, orden=o,
          titulo=TITULO)  # fmt: skip
    ver(motor_migrate, e, e.adm, o)
    assert llamar(motor_migrate, e, e.adm, CREAR, sesion=sin_gesto, orden=o, titulo=TITULO)
    larga = nueva_sesion(motor_migrate, e.adm, horas_atras=13)
    niega(motor_migrate, e, e.adm, CREAR, "requiere_sesion_reciente", sesion=larga, orden=o,
          titulo=TITULO)  # fmt: skip
    # Registrar sigue en la base, no solo en la ruta.
    niega(motor_migrate, e, e.adm, REGISTRAR, "requiere_segundo_factor_reciente", sesion=sin_gesto,
          **evidencia(motor_migrate, e, o))  # fmt: skip


def test_quien_ya_no_ejecuta_tampoco_necesita_el_gesto_para_subir(motor_migrate, motor_app, e):
    o = emitida(motor_migrate, e)
    ver(motor_migrate, e, e.adm, o)
    dar_de_baja(motor_migrate, motor_app, e, e.adm)
    sin_gesto = nueva_sesion(motor_migrate, e.adm, **ANTIVIRUS_VIEJO)
    doc = llamar(motor_migrate, e, e.adm, CREAR, sesion=sin_gesto, orden=o, titulo=TITULO)
    v = llamar(motor_migrate, e, e.adm, VERSION, sesion=sin_gesto, orden=o, documento=doc)
    assert v is not None
    larga = nueva_sesion(motor_migrate, e.adm, horas_atras=13)
    niega(motor_migrate, e, e.adm, VERSION, "requiere_sesion_reciente", sesion=larga, orden=o,
          documento=doc)  # fmt: skip
    # Control: registrar, sin la facultad, sigue pidiendo el gesto.
    niega(motor_migrate, e, e.adm, REGISTRAR, "requiere_segundo_factor_reciente", sesion=sin_gesto,
          **evidencia(motor_migrate, e, o, documento=doc))  # fmt: skip


# El comprobante de la ficha -------------------------------------------------------------------


def test_de_punta_a_punta_el_archivo_subido_es_el_que_ve_el_consejo(
    motor_migrate, motor_app, e, bucket
):
    o = emitida(motor_migrate, e)
    ver(motor_migrate, e, e.adm, o)
    archivo = pdf()
    doc, v = subida_por_http(motor_app, e, o, archivo=archivo)
    f = ficha(e, e.adm, o)
    assert f["comprobante_actual"] is None
    assert f["comprobante_subido"] == {
        "documento_id": doc, "version_documento_id": v, "estado": "en_cuarentena"
    }  # fmt: skip
    assert analizar(motor_app) == 1
    f = ficha(e, e.adm, o)
    assert f["comprobante_subido"] is None
    assert f["comprobante_actual"] == {"documento_id": doc, "version_documento_id": v}
    r = post(e, e.adm, f"ordenes-pago/{o}/pago-manual", registro(motor_migrate, e, o, doc))
    assert r.status_code == 201, r.text
    f = ficha(e, e.mc2, o)
    assert f["comprobante_actual"] == {"documento_id": doc, "version_documento_id": v}
    assert f["paso"] == "conciliar"
    # Ve el archivo quien concilia y quien lo subió.
    for persona in (e.mc2, e.adm):
        r = get(e, persona, f"documentos/{doc}/versiones/{v}/contenido")
        assert (r.status_code, r.content) == (200, archivo), persona


def test_a_quien_preparo_y_no_lo_subio_no_se_le_ofrece_ni_se_le_sirve(
    motor_migrate, motor_app, e, bucket
):
    o = emitida(motor_migrate, e)
    x = segundo_ejecutor(motor_migrate, motor_app, e)
    ver(motor_migrate, e, x, o)
    doc, v = subida_por_http(motor_app, e, o, quien=x)
    assert analizar(motor_app) == 1
    r = post(e, x, f"ordenes-pago/{o}/pago-manual", registro(motor_migrate, e, o, doc))
    assert r.status_code == 201, r.text
    assert ficha(e, e.adm, o)["comprobante_actual"] is None
    r = get(e, e.adm, f"documentos/{doc}/versiones/{v}/contenido")
    assert r.status_code == 404
    # Control: el Consejo que no preparó lo recibe en la ficha y lo abre.
    assert ficha(e, e.mc2, o)["comprobante_actual"]["documento_id"] == doc
    assert get(e, e.mc2, f"documentos/{doc}/versiones/{v}/contenido").status_code == 200


def test_sin_documentos_ver_no_se_ofrece_el_del_registro(motor_migrate, e):
    o = emitida(motor_migrate, e)
    registrada(motor_migrate, e, o)
    assert ficha(e, e.adm, o)["comprobante_actual"] is not None  # control
    with motor_migrate.begin() as c:
        n = c.execute(
            text("delete from nucleo.facultad_otorgada f using nucleo.mandato m"
                 " where m.id = f.mandato_id and m.persona_id = :p"
                 " and f.facultad = 'documentos.ver'"),
            {"p": e.adm},
        ).rowcount  # fmt: skip
        # La escena la hace propietaria, y eso también da la lectura.
        c.execute(text("update nucleo.vinculo set tipo = 'inquilino' where persona_id = :p"),
                  {"p": e.adm})  # fmt: skip
    assert n == 1
    assert ficha(e, e.adm, o)["comprobante_actual"] is None


def test_lo_rechazado_lo_dice_la_ficha_y_el_actual_sigue(motor_migrate, e):
    o = emitida(motor_migrate, e)
    ver(motor_migrate, e, e.adm, o)
    bueno = comprobante(motor_migrate, e, orden=o)
    malo = comprobante(motor_migrate, e, estado="rechazado", orden=o)
    f = ficha(e, e.adm, o)
    assert f["comprobante_actual"]["documento_id"] == str(bueno)
    assert (f["comprobante_subido"]["documento_id"], f["comprobante_subido"]["estado"]) == (
        str(malo), "rechazado"
    )  # fmt: skip
    # Otra persona no ve lo que subió la administración.
    assert ficha(e, e.mc2, o)["comprobante_subido"] is None


def test_subir_de_nuevo_reemplaza_aunque_el_antivirus_termine_al_reves(motor_migrate, e):
    o = emitida(motor_migrate, e)
    ver(motor_migrate, e, e.adm, o)
    viejo = comprobante(motor_migrate, e, orden=o)
    nuevo = comprobante(motor_migrate, e, orden=o)
    assert ficha(e, e.adm, o)["comprobante_actual"]["documento_id"] == str(nuevo)
    with motor_migrate.begin() as c:  # el primero terminó después
        c.execute(
            text("update nucleo.version_documento set analizada_en = now() + interval '1 minute'"
                 " where documento_id = :d"),
            {"d": viejo},
        )  # fmt: skip
    assert ficha(e, e.adm, o)["comprobante_actual"]["documento_id"] == str(nuevo)


def test_con_un_registro_vigente_no_se_habla_de_lo_subido(motor_migrate, e):
    o = emitida(motor_migrate, e)
    p = registrada(motor_migrate, e, o)
    comprobante(motor_migrate, e, estado="en_cuarentena", orden=o)
    assert ficha(e, e.adm, o)["comprobante_subido"] is None
    observada(motor_migrate, e, o, p)
    assert ficha(e, e.adm, o)["comprobante_subido"]["estado"] == "en_cuarentena"  # control


# Corregir -------------------------------------------------------------------------------------


def aperturas(motor_migrate, o) -> int:
    with motor_migrate.connect() as c:
        return c.execute(
            text("select count(*) from nucleo.apertura_cuenta_pago_manual a"
                 " join nucleo.ejecucion_orden_pago e on e.id = a.ejecucion_id"
                 " where e.orden_id = :o"),
            {"o": o},
        ).scalar_one()  # fmt: skip


def documento_del_registro(motor_migrate, p) -> UUID:
    with motor_migrate.connect() as c:
        return c.execute(text("select documento_id from nucleo.pago_manual where id = :p"),
                         {"p": p}).scalar_one()  # fmt: skip


def test_corregir_no_abre_la_cuenta_otra_vez(motor_migrate, e):
    o = emitida(motor_migrate, e)
    assert aperturas(motor_migrate, o) == 0
    p = registrada(motor_migrate, e, o)
    observada(motor_migrate, e, o, p)
    assert aperturas(motor_migrate, o) == 1  # control: la medición ve la apertura del acto
    doc = documento_del_registro(motor_migrate, p)
    r = post(e, e.adm, f"ordenes-pago/{o}/pago-manual",
             registro(motor_migrate, e, o, doc, cuenta_destino=None))  # fmt: skip
    assert r.status_code == 201, r.text
    assert aperturas(motor_migrate, o) == 1
    # Quedó el registro nuevo, que corrige al observado, con el mismo comprobante.
    with motor_migrate.connect() as c:
        fila = c.execute(
            text("select corrige_a, documento_id from nucleo.pago_manual where id = :p"),
            {"p": r.json()["id"]},
        ).one()  # fmt: skip
    assert (fila.corrige_a, fila.documento_id) == (p, doc)


def test_un_registro_nuevo_sigue_pidiendo_la_cuenta(motor_migrate, e):
    o = emitida(motor_migrate, e)
    ver(motor_migrate, e, e.adm, o)
    doc = comprobante(motor_migrate, e, orden=o)
    r = post(e, e.adm, f"ordenes-pago/{o}/pago-manual",
             registro(motor_migrate, e, o, doc, cuenta_destino=None))  # fmt: skip
    assert codigo(r) == (422, "no_coincide_con_la_orden"), r.text
    r = post(e, e.adm, f"ordenes-pago/{o}/pago-manual", registro(motor_migrate, e, o, doc))
    assert r.status_code == 201, r.text  # control


def test_al_corregir_una_cuenta_escrita_se_compara_y_el_importe_siempre(motor_migrate, e):
    o = emitida(motor_migrate, e)
    p = registrada(motor_migrate, e, o)
    observada(motor_migrate, e, o, p)
    doc = documento_del_registro(motor_migrate, p)
    ruta = f"ordenes-pago/{o}/pago-manual"
    otra = "9" * 22
    r = post(e, e.adm, ruta, registro(motor_migrate, e, o, doc, cuenta_destino=otra))
    assert r.status_code == 422, r.text
    r = post(e, e.adm, ruta,
             registro(motor_migrate, e, o, doc, cuenta_destino=None, importe_centavos=IMPORTE + 1))  # fmt: skip
    assert codigo(r) == (422, "no_coincide_con_la_orden"), r.text
    r = post(e, e.adm, ruta, registro(motor_migrate, e, o, doc, cuenta_destino=None))
    assert r.status_code == 201, r.text  # control


def test_sin_cuenta_tampoco_corrige_quien_no_vio_la_cuenta(motor_migrate, motor_app, e):
    o = emitida(motor_migrate, e)
    p = registrada(motor_migrate, e, o)
    observada(motor_migrate, e, o, p)
    x = segundo_ejecutor(motor_migrate, motor_app, e)
    doc = documento_del_registro(motor_migrate, p)
    r = post(e, x, f"ordenes-pago/{o}/pago-manual",
             registro(motor_migrate, e, o, doc, cuenta_destino=None))  # fmt: skip
    assert codigo(r) == (403, "no_es_quien_ejecuto"), r.text


def test_el_consejo_ve_quien_registro_y_la_cuenta_con_la_revision(motor_migrate, e):
    o = emitida(motor_migrate, e)
    registrada(motor_migrate, e, o)
    f = ficha(e, e.mc2, o)  # sin la cuenta: lo afirma `ficha`
    with motor_migrate.connect() as c:
        nombre = c.execute(text("select nombre from nucleo.persona where id = :p"),
                           {"p": e.adm}).scalar_one()  # fmt: skip
    assert f["ejecucion"]["pagos"][-1]["registrado_por_nombre"] == nombre
    r = get(e, e.mc2, f"ordenes-pago/{o}/revision")
    assert r.status_code == 200, r.text
    assert r.json()["versiones"][0]["cuenta_destino"] == CUENTA
    # Control: a quien preparó, la misma ruta no se la da.
    assert CUENTA not in get(e, e.adm, f"pagos/{o}").text


# Por aclarar ----------------------------------------------------------------------------------


def test_por_aclarar_no_toma_el_comprobante_de_otra_ejecucion(motor_migrate, motor_app, e):
    o = emitida(motor_migrate, e)
    p = registrada(motor_migrate, e, o)
    primero = documento_del_registro(motor_migrate, p)
    observada(motor_migrate, e, o, p)
    d = llamar(motor_migrate, e, e.adm, DECLARAR, orden=o, version=1,
               motivo="SINTETICO: el banco devolvió la transferencia")  # fmt: skip
    llamar(motor_migrate, e, e.mc2, CONFIRMAR, orden=o, declaracion=d)
    assert orden(motor_migrate, o)["estado"] == "fallida"
    llamar(motor_migrate, e, e.adm, MODIFICAR, orden=o, version=1, concepto="Reintento SINTETICO")
    if orden(motor_migrate, o)["estado"] == "en_revision":
        llamar(motor_migrate, e, e.mc1, APROBAR, orden=o, version=2,
               huella=huella(motor_migrate, o, 2))  # fmt: skip
    llamar(motor_migrate, e, e.adm, EMITIR, orden=o, version=2)
    ver_version(motor_migrate, e, e.adm, o, 2)
    dar_de_baja(motor_migrate, motor_app, e, e.adm)
    [fila] = por_aclarar(motor_migrate, e, e.adm)
    assert fila["comprobante_id"] is None, primero
    # Control: el de esta ejecución sí se ve, y con él se puede registrar.
    nuevo = llamar(motor_migrate, e, e.adm, CREAR, orden=o, titulo=TITULO)
    [fila] = por_aclarar(motor_migrate, e, e.adm)
    assert fila["comprobante_id"] == nuevo


def test_por_aclarar_sigue_viendo_el_del_registro_observado(motor_migrate, motor_app, e):
    o = emitida(motor_migrate, e)
    p = registrada(motor_migrate, e, o)
    observada(motor_migrate, e, o, p)
    doc = documento_del_registro(motor_migrate, p)
    with motor_migrate.begin() as c:
        c.execute(text("update nucleo.documento set cargado_por = :a where id = :d"),
                  {"a": e.adm, "d": doc})  # fmt: skip
    dar_de_baja(motor_migrate, motor_app, e, e.adm)
    [fila] = por_aclarar(motor_migrate, e, e.adm)
    assert fila["comprobante_id"] == doc


# Migración ------------------------------------------------------------------------------------


def test_0073_ida_y_vuelta(base_aparte):
    cfg, motor = base_aparte

    def definicion(firma: str) -> str:
        with motor.connect() as c:
            return c.execute(
                text("select pg_get_functiondef(cast(:f as regprocedure))"), {"f": firma}
            ).scalar_one()  # fmt: skip

    crear_ = "nucleo.crear_comprobante_de_pago(uuid, text, date, uuid, text, uuid)"
    pagos = "nucleo.pagos_manuales_lectura(uuid, text, uuid, text, uuid)"
    subido = "nucleo.comprobante_subido_lectura(uuid, text, uuid, text, uuid)"
    command.upgrade(cfg, "0072")
    antes = {f: definicion(f) for f in (crear_, pagos, subido)}
    assert "exigir_acto_economico" in antes[crear_]
    command.upgrade(cfg, "0073")
    assert "exigir_acto_economico" not in definicion(crear_)
    assert "registrado_por_nombre" in definicion(pagos)
    assert "estado text" in definicion(subido)
    with motor.connect() as c:
        permisos = c.execute(
            text("select has_function_privilege('cem_app', cast(:f as regprocedure), 'execute'),"
                 " has_function_privilege('public', cast(:f as regprocedure), 'execute')"),
            {"f": subido},
        ).one()  # fmt: skip
    assert tuple(permisos) == (True, False)
    command.downgrade(cfg, "0072")
    despues = {f: definicion(f) for f in (crear_, pagos, subido)}
    assert despues[crear_] == antes[crear_]
    assert despues[pagos] == antes[pagos]
    assert "registrado_por_nombre" not in despues[pagos]
    assert "analizada_en desc" in despues[subido] and "limit 1" in despues[subido]
    command.upgrade(cfg, "0073")  # control: vuelve a subir limpio
    assert "registrado_por_nombre" in definicion(pagos)
