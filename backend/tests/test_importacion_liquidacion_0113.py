"""Importación de la liquidación del administrador, H5-1 (`0113`; diseño H5 §5; RF42, RN30, PA32).

Contra servicios reales: PostgreSQL, el almacén S3 de pruebas y ClamAV. El PDF es una liquidación
inventada con la geometría del formato Mis Expensas (`test_mis_expensas.liquidacion`), escrita en
Helvetica 7: los importes alineados a la derecha en su columna, el texto desde su margen. Datos
SINTETICOS / NO REAL.
"""

from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db import transaccion
from app.liquidacion import importacion  # noqa: F401  (registra el trabajo de la lectura)
from app.liquidacion.mis_expensas import IMPORTE, PORCENTAJE, Pagina
from tests import test_documentos_2c1 as carga
from tests.conftest import clave_idempotencia
from tests.test_documentos_2c1 import analizar, crear, pdf, subir
from tests.test_mandatos import otra_persona
from tests.test_mandatos_0012 import alta
from tests.test_mis_expensas import ESPACIO, liquidacion
from tests.test_modo_operacion import cambiar, consorcio_con_persona, decidir_equipo
from tests.test_orden_pago_neutra import nueva_sesion
from tests.test_rutas_egresos import cliente
from tests.test_vinculo_0014 import base_aparte  # noqa: F401  (`base_aparte` es un fixture)

pytestmark = [pytest.mark.db, pytest.mark.redis]

bucket = carga.bucket
firmas_de_la_imagen = carga.firmas_de_la_imagen

CATEGORIA = "liquidaciones_del_administrador"
CUERPO = 7
"""Tamaño de la letra del PDF, en puntos."""
ASCENSO = 0.718
"""El de Helvetica: `top` de la palabra = alto de la página − base − ascenso × cuerpo."""
ANCHOS = {**dict.fromkeys("0123456789", 556), ",": 278, ".": 278, "%": 889, "-": 333}
"""Anchos de Helvetica (milésimas del cuerpo) de lo que tiene un importe o un porcentaje."""


def _cadena(texto: str) -> str:
    return texto.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _es_valor(texto: str) -> bool:
    return bool(IMPORTE.match(texto) or PORCENTAJE.match(texto) or texto == "-")


def pdf_de(paginas: list[Pagina]) -> bytes:
    """Un PDF con cada palabra donde la ubica la liquidación inventada: los valores terminan en
    su `x1`; las palabras de un mismo tramo de texto se escriben juntas desde su `x0`."""
    flujos = []
    for p in paginas:
        ops = []
        renglones: dict[float, list] = {}
        for w in p.palabras:
            renglones.setdefault(w.top, []).append(w)
        for top, ws in renglones.items():
            y = p.alto - top - ASCENSO * CUERPO
            tramo: list = []
            for w in sorted(ws, key=lambda w: w.x0) + [None]:
                sigue = (w is not None and tramo and not _es_valor(w.texto)
                         and abs(w.x0 - tramo[-1].x1 - ESPACIO) < 0.01)  # fmt: skip
                if sigue:
                    tramo.append(w)
                    continue
                if tramo:
                    ops.append((tramo[0].x0, y, " ".join(t.texto for t in tramo)))
                tramo = []
                if w is None:
                    break
                if _es_valor(w.texto):
                    ancho = sum(ANCHOS[ch] for ch in w.texto) * CUERPO / 1000
                    ops.append((w.x1 - ancho, y, w.texto))
                else:
                    tramo = [w]
        flujo = "".join(
            f"BT /F1 {CUERPO} Tf {x:.2f} {y:.2f} Td ({_cadena(t)}) Tj ET\n" for x, y, t in ops
        ).encode("cp1252")
        flujos.append((p.ancho, p.alto, flujo))
    n = len(flujos)
    objetos: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        (
            "<< /Type /Pages /Kids ["
            + " ".join(f"{3 + 2 * i} 0 R" for i in range(n))
            + f"] /Count {n} >>"
        ).encode(),  # fmt: skip
    ]
    fuente = 3 + 2 * n
    for i, (ancho, alto, flujo) in enumerate(flujos):
        objetos.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {ancho:g} {alto:g}] /Contents"
            f" {4 + 2 * i} 0 R /Resources << /Font << /F1 {fuente} 0 R >> >> >>".encode()
        )
        objetos.append(b"<< /Length %d >>\nstream\n" % len(flujo) + flujo + b"endstream")
    objetos.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica"
                   b" /Encoding /WinAnsiEncoding >>")  # fmt: skip
    salida = bytearray(b"%PDF-1.4\n")
    posiciones = []
    for i, cuerpo in enumerate(objetos, 1):
        posiciones.append(len(salida))
        salida += f"{i} 0 obj\n".encode() + cuerpo + b"\nendobj\n"
    xref = len(salida)
    salida += f"xref\n0 {len(objetos) + 1}\n0000000000 65535 f \n".encode()
    salida += b"".join(f"{p:010d} 00000 n \n".encode() for p in posiciones)
    salida += (
        f"trailer\n<< /Size {len(objetos) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(salida)


@pytest.fixture
def escena(motor_migrate, motor_app):
    """Consorcio en Modo Consejo con un miembro del Consejo (paquete con `liquidacion.importar` y
    `documentos.cargar_operativos`) y el padrón de la liquidación inventada menos «1-B»."""
    cons, _, prop = consorcio_con_persona(motor_migrate)
    acta = decidir_equipo(motor_app, cons)
    cambiar(motor_app, cons, "consejo", acta)
    mc = otra_persona(motor_migrate, cons)
    alta(motor_app, cons, None, acta, persona=mc, tipo="miembro_consejo", origen="soporte")
    with motor_migrate.begin() as c:
        for designacion in ("PB-A", "1-A"):
            c.execute(text("insert into nucleo.unidad (consorcio_id, designacion) values (:c, :d)"),
                      {"c": cons, "d": designacion})  # fmt: skip
    return {"cons": cons, "prop": prop, "mc": mc, "sesion": nueva_sesion(motor_migrate, mc)}


def con_1b(motor_migrate, e) -> None:
    with motor_migrate.begin() as c:
        c.execute(text("insert into nucleo.unidad (consorcio_id, designacion) values (:c, '1-B')"),
                  {"c": e["cons"]})  # fmt: skip


def subida(e, motor_app, contenido: bytes, categoria: str = CATEGORIA) -> tuple[str, str]:
    """El PDF subido por el Consejo y aprobado por el antivirus: (documento, versión)."""
    r = crear(e["mc"], e["cons"], categoria=categoria, titulo="Liquidación SINTETICA")
    assert r.status_code == 201, r.text
    doc = r.json()["id"]
    r = subir(e["mc"], e["cons"], doc, contenido, "application/pdf")
    assert r.status_code == 201, r.text
    assert analizar(motor_app) == 1
    return doc, r.json()["id"]


def importar(e, doc, v, quien: UUID | None = None, sesion: UUID | None = None):
    quien = quien or e["mc"]
    with cliente(quien, sesion or e["sesion"]) as c:
        return c.post(f"/api/v1/consorcios/{e['cons']}/liquidaciones-importadas",
                      json={"documento_id": doc, "version_documento_id": v},
                      headers=clave_idempotencia())  # fmt: skip


def ver(e, i):
    with cliente(e["mc"], e["sesion"]) as c:
        return c.get(f"/api/v1/consorcios/{e['cons']}/liquidaciones-importadas/{i}")


def importada(e, motor_app, contenido: bytes) -> dict:
    doc, v = subida(e, motor_app, contenido)
    r = importar(e, doc, v)
    assert r.status_code == 201, r.text
    assert analizar(motor_app) == 1  # la lectura
    r = ver(e, r.json()["id"])
    assert r.status_code == 200, r.text
    return r.json()


def test_una_liquidacion_limpia_queda_para_confirmar_con_cada_dato_y_su_renglon(
    escena, bucket, motor_app, motor_migrate
):
    con_1b(motor_migrate, escena)
    i = importada(escena, motor_app, pdf_de(liquidacion()))
    assert (i["estado"], i["formato"], i["periodo"]) == ("para_confirmar", "mis_expensas",
                                                         "2026-09-01")  # fmt: skip
    assert i["diferencias"] == [] and i["motivo"] is None
    campos = i["campos"]
    tipos = [c["tipo"] for c in campos]
    assert tipos.count("rubro") == 2 and tipos.count("gasto") == 3
    assert (
        tipos.count("unidad") == tipos.count("importe_unidad") == tipos.count("saldo_unidad") == 3
    )
    assert len(campos) == 16 and [c["orden"] for c in campos] == list(range(16))
    assert {(c["estado"], c["origen"], c["confianza"]) for c in campos} == {
        ("pendiente", "determinista", "1.00")
    }
    luz = next(c for c in campos if c["clave"] == "gasto:1:1")
    assert luz["valor"]["importes"] == {"Gastos Comunes": "1000.00", "AySA": "0.00"}
    assert luz["valor"]["total"] == "1000.00" and luz["pagina"] == 1
    assert Decimal(luz["renglon"]) == pytest.approx(Decimal(269), abs=Decimal(1))
    beto = next(c for c in campos if c["clave"] == "importe_unidad:002")
    assert beto["valor"]["grupos"]["AySA"] == {"porcentaje": "40.00", "monto": "200.20"}
    assert beto["valor"]["total"] == "1326.00"
    # Cada campo de una unidad lleva la unidad del padrón por su departamento.
    with motor_migrate.connect() as c:
        padron = dict(c.execute(
            text("select designacion, id from nucleo.unidad where consorcio_id = :c"),
            {"c": escena["cons"]}).all())  # fmt: skip
    por_uf = {c["valor"]["uf"]: c["unidad_id"] for c in campos if c["tipo"] == "unidad"}
    assert por_uf == {"001": str(padron["PB-A"]), "002": str(padron["1-A"]),
                      "003": str(padron["1-B"])}  # fmt: skip


def test_un_gasto_que_no_suma_queda_en_las_diferencias(escena, bucket, motor_app, motor_migrate):
    con_1b(motor_migrate, escena)
    i = importada(escena, motor_app, pdf_de(liquidacion(gasto_luz=("1.000,00", "0,00",
                                                                     "1.000,05"))))  # fmt: skip
    assert i["estado"] == "para_confirmar"
    assert [(d["regla"], d["esperado"], d["informado"]) for d in i["diferencias"]] == [
        ("gasto_suma_grupos", "1000.00", "1000.05"),
        ("rubro_suma_gastos", "1500.55", "1500.50"),
    ]


def test_una_unidad_que_no_esta_en_el_padron_queda_en_las_diferencias(escena, bucket, motor_app):
    i = importada(escena, motor_app, pdf_de(liquidacion()))
    assert [(d["regla"], d["detalle"], d["pagina"]) for d in i["diferencias"]] == [
        ("unidad_fuera_del_padron", "UF 003 1-B", 4)
    ]
    ceci = [c for c in i["campos"] if c["valor"].get("uf") == "003"]
    assert len(ceci) == 3 and {c["unidad_id"] for c in ceci} == {None}


def test_un_pdf_sin_el_formato_queda_no_reconocido_sin_campos(escena, bucket, motor_app):
    i = importada(escena, motor_app, pdf())
    assert i["estado"] == "no_reconocida" and i["formato"] is None and i["campos"] == []
    assert "no tiene texto" in i["motivo"]


def test_nada_sin_confirmar_sale_en_la_lectura_oficial(escena, bucket, motor_app, motor_migrate):
    """PA32, parte base (RN30): con 16 campos leídos y ninguno confirmado, la lectura oficial no
    devuelve nada. Control: con la importación confirmada y un campo confirmado (escritos como
    migración, porque la confirmación es H5-3), devuelve ese campo y solo ese."""
    con_1b(motor_migrate, escena)
    i = importada(escena, motor_app, pdf_de(liquidacion()))
    oficial = text("select clave from nucleo.campos_oficiales_liquidacion(:i, :a, 'api', :s)")

    def oficiales() -> list[str]:
        with transaccion(motor_app, persona_id=escena["mc"],
                               consorcio_id=escena["cons"]) as c:  # fmt: skip
            return list(c.execute(oficial, {"i": i["id"], "a": escena["mc"],
                                            "s": escena["sesion"]}).scalars())  # fmt: skip

    assert len(i["campos"]) == 16 and oficiales() == []

    def como_migracion(tabla: str, sentencia: str) -> None:
        with motor_migrate.begin() as c:
            c.execute(text(f"alter table {tabla} disable trigger user"))
            c.execute(text(sentencia), {"p": escena["mc"], "i": i["id"]})
            c.execute(text(f"alter table {tabla} enable trigger user"))

    como_migracion(
        "nucleo.campo_preliminar",
        "update nucleo.campo_preliminar set estado = 'confirmado', confirmado_por = :p,"
        " confirmado_en = now() where importacion_id = :i and clave = 'rubro:2'",
    )
    assert oficiales() == []  # el campo confirmado, pero la importación no
    como_migracion(
        "nucleo.importacion_liquidacion",
        "update nucleo.importacion_liquidacion set estado = 'confirmada'"
        " where id = :i and :p is not null",
    )
    assert oficiales() == ["rubro:2"]


def test_quien_importa_y_que_se_importa(escena, bucket, motor_app, motor_migrate):
    e = escena
    doc, v = subida(e, motor_app, pdf_de(liquidacion()))
    # La propietaria no importa (D3: ve lo de su unidad).
    prop_sesion = nueva_sesion(motor_migrate, e["prop"])
    assert importar(e, doc, v, quien=e["prop"], sesion=prop_sesion).status_code == 403
    # Un PDF de otra categoría no es una liquidación.
    acta, v_acta = subida(e, motor_app, pdf_de(liquidacion()), categoria="actas_y_decisiones")
    r = importar(e, acta, v_acta)
    assert r.status_code == 422 and r.json()["codigo"] == "documento_invalido", r.text
    # Control: la liquidación sí, una vez.
    assert importar(e, doc, v).status_code == 201
    r = importar(e, doc, v)
    assert r.status_code == 409 and r.json()["codigo"] == "liquidacion_ya_importada", r.text


def test_lo_leido_no_se_cambia_ni_se_borra(escena, bucket, motor_app, motor_migrate):
    con_1b(motor_migrate, escena)
    i = importada(escena, motor_app, pdf_de(liquidacion()))
    for sentencia in (
        "update nucleo.campo_preliminar set valor = '{}' where importacion_id = :i",
        "delete from nucleo.campo_preliminar where importacion_id = :i",
        "update nucleo.importacion_liquidacion set estado = 'leyendo', leida_en = null"
        " where id = :i",
        "delete from nucleo.importacion_liquidacion where id = :i",
    ):
        with pytest.raises(DBAPIError) as error, motor_migrate.begin() as c:
            c.execute(text(sentencia), {"i": i["id"]})
        assert error.value.orig.diag.message_hint == "historia_inmutable", sentencia


def test_un_trabajo_repetido_no_vuelve_a_escribir(escena, bucket, motor_app, motor_migrate):
    con_1b(motor_migrate, escena)
    i = importada(escena, motor_app, pdf_de(liquidacion()))
    with transaccion(motor_app, persona_id=None, consorcio_id=escena["cons"]) as c:
        otra = c.execute(
            text("select nucleo.guardar_lectura_liquidacion(:i, null, null, 'otra', '[]', '[]')"),
            {"i": i["id"]},
        ).scalar_one()  # fmt: skip
    assert otra is False
    assert ver(escena, i["id"]).json()["estado"] == "para_confirmar"


def test_dos_unidades_que_coinciden_quedan_como_ambiguas(escena, bucket, motor_app, motor_migrate):
    """`1-B` y `1-b` caben en el padrón (el índice compara el texto exacto) y las dos coinciden con
    el departamento leído: ninguna se asigna."""
    con_1b(motor_migrate, escena)
    with motor_migrate.begin() as c:
        c.execute(text("insert into nucleo.unidad (consorcio_id, designacion) values (:c, '1-b')"),
                  {"c": escena["cons"]})  # fmt: skip
    i = importada(escena, motor_app, pdf_de(liquidacion()))
    assert [(d["regla"], d["detalle"]) for d in i["diferencias"]] == [
        ("unidad_ambigua", "UF 003 1-B")
    ]
    assert {c["unidad_id"] for c in i["campos"] if c["valor"].get("uf") == "003"} == {None}
    assert {c["unidad_id"] is None for c in i["campos"] if c["valor"].get("uf") == "001"} == {False}


def test_una_lectura_que_agoto_sus_intentos_lo_dice(escena, bucket, motor_app, motor_migrate):
    """Una falla pasajera se reintenta (`INTENTOS_LEER`); si el trabajo muere, la importación no
    queda «leyendo»: la lectura dice `falla_de_lectura`. Control: antes de matarlo, `leyendo`."""
    assert importacion.INTENTOS_LEER > 1
    doc, v = subida(escena, motor_app, pdf_de(liquidacion()))
    r = importar(escena, doc, v)
    assert r.status_code == 201, r.text
    i = r.json()["id"]
    assert ver(escena, i).json()["estado"] == "leyendo"
    with motor_migrate.begin() as c:
        c.execute(text("update plataforma.jobs set estado = 'muerto' where clave_unica = :k"),
                  {"k": f"{importacion.TRABAJO_LEER}:{i}"})  # fmt: skip
    assert ver(escena, i).json()["estado"] == "falla_de_lectura"
