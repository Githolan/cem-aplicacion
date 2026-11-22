"""La exportación de soporte lleva las liquidaciones importadas (`0121`; diseño P0 S4, PA27, RF16,
RF36, RNF08).

Contra servicios reales: PostgreSQL, el almacén S3 de pruebas, ClamAV y `age`. La escena es la de
`0119` (Modo Consejo) con septiembre importado dos veces (la primera, reemplazada; la segunda, la
oficial, con datos corregidos y un enlace anotado), octubre descartado y la correspondencia de un
grupo con dos versiones. La liquidación es la inventada de H5-1 (`test_mis_expensas.liquidacion`).
Datos SINTETICOS / NO REAL.
"""

import hashlib
import json
import re
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import text

from app.db import transaccion
from scripts import exportar_consorcio
from tests.test_confirmacion_liquidacion_0115 import post
from tests.test_confirmacion_liquidacion_0115 import ruta as ruta_importadas
from tests.test_enlaces import con_anotacion
from tests.test_exportacion_2c3 import (  # noqa: F401  (`claves` y `con_url` son fixtures)
    argumentos,
    claves,
    con_url,
    constancias,
    descifrar,
    entidades,
    verificar_manifiesto,
)
from tests.test_importacion_liquidacion_0113 import bucket, firmas_de_la_imagen, importada, pdf_de
from tests.test_liquidacion_oficial_0119 import (
    SEPTIEMBRE,
    armar_escena,
    confirmada,
    confirmar,
    de_octubre,
    escena,
    get,
)
from tests.test_mis_expensas import liquidacion
from tests.test_modo_operacion import hoy_de
from tests.test_orden_pago_neutra import cbu_sintetico
from tests.test_recalculo_liquidacion_0116 import AGUA, GC, esquema
from tests.test_vinculo_0014 import base_aparte

pytestmark = [pytest.mark.db, pytest.mark.redis]
# ruff: noqa: F811

__all__ = ["base_aparte", "bucket", "escena", "firmas_de_la_imagen"]

PARTE = "liquidaciones_importadas"
ENTIDADES = exportar_consorcio.PARTES[PARTE]
URL = "https://comprobantes.ejemplo.com/consorcio/2026-09/comprobantes.pdf"
CUENTA = cbu_sintetico(77)
CONCEPTO_CON_CUENTA = f"SINTETICO: transferir a {CUENTA[:8]} {CUENTA[8:]}"
CONCEPTO_SIN_CUENTA = "SINTETICO: transferir a [cuenta omitida]"
MOTIVO_REEMPLAZO = f"Corrigió el rubro 2; cuenta {CUENTA}"
MOTIVO_DESCARTE = f"Es de otro edificio; cuenta {CUENTA}"
TIPOS_DE_UNIDAD = ("unidad", "importe_unidad", "saldo_unidad")
TABLAS = {
    "importacion_liquidacion": "nucleo.importacion_liquidacion",
    "campo_preliminar": "nucleo.campo_preliminar",
    "correspondencia_grupo": "nucleo.correspondencia_grupo_liquidacion",
    "enlace_liquidacion": "nucleo.enlace_liquidacion",
}
COLUMNAS_DE_PERSONA = {
    "nucleo.importacion_liquidacion": ("subida_por", "decidida_por"),
    "nucleo.campo_preliminar": ("confirmado_por",),
    "nucleo.correspondencia_grupo_liquidacion": ("definida_por",),
}
RESOLVER = text(
    "select nucleo.resolver_campo_preliminar(:i, :f, :d, cast(:v as jsonb), :a, 'api', :s)"
)


def importar(e, motor_app, contenido: bytes, corregir: dict | None = None) -> str:
    """Como `importar` de `0119`, con el archivo dado: leída y con todos sus datos decididos (los
    de `corregir`, corregidos con esos valores)."""
    i = importada(e, motor_app, contenido)
    assert i["estado"] == "para_confirmar", i
    with transaccion(motor_app, persona_id=e["mc"], consorcio_id=e["cons"]) as c:
        for campo in i["campos"]:
            cambio = (corregir or {}).get(campo["clave"])
            valor = None if cambio is None else json.dumps(campo["valor"] | cambio)
            c.execute(RESOLVER, {"i": i["id"], "f": campo["id"], "a": e["mc"], "s": e["sesion"],
                                 "d": "confirmado" if cambio is None else "corregido",
                                 "v": valor})  # fmt: skip
    return i["id"]


def importadas(e, motor_app, motor_migrate) -> dict:
    """Septiembre reemplazado (la primera con una cuenta escrita en el concepto corregido), la
    oficial con el rubro 2 y dos unidades corregidas y un enlace, octubre descartado y la
    correspondencia de «AySA» en dos versiones."""
    with motor_migrate.begin() as c:
        e["u_pb_b"] = c.execute(text(
            "insert into nucleo.unidad (consorcio_id, designacion) values (:c, 'PB-B')"
            " returning id"), {"c": e["cons"]}).scalar_one()  # fmt: skip
    esquema(motor_app, motor_migrate, e, "expensas_comunes", GC)
    esquema(motor_app, motor_migrate, e, "agua", AGUA)
    a = importar(e, motor_app, pdf_de(liquidacion()),
                 {"gasto:1:1": {"concepto": CONCEPTO_CON_CUENTA}})  # fmt: skip
    assert confirmar(e, a).status_code == 200
    # La oficial: «unidad:002» pasa a «PB-B» (escrita como «pb-b», como la compara el padrón) e
    # «importe_unidad:002» a «Z-9», que no está en el padrón.
    b = importar(e, motor_app, con_anotacion(pdf_de(liquidacion()), URL), {
        "rubro:2": {"total": "999.99"}, "unidad:002": {"depto": "pb-b"},
        "importe_unidad:002": {"depto": "Z-9"},
    })  # fmt: skip
    r = confirmar(e, b, {"reemplaza_a": a, "motivo": MOTIVO_REEMPLAZO})
    assert r.status_code == 200, r.text
    for grupo in ("agua", "expensas_comunes"):
        r = post(e, ruta_importadas(e, b, "correspondencias"), {"nombre": "AySA", "grupo": grupo})
        assert r.status_code == 200, r.text
    d = importada(e, motor_app, pdf_de(de_octubre()))["id"]
    r = post(e, ruta_importadas(e, d, "descarte"), {"motivo": MOTIVO_DESCARTE})
    assert r.status_code == 200, r.text
    e["hoy"] = hoy_de(motor_app, e["cons"])
    return {"reemplazada": a, "oficial": b, "descartada": d}


def exportar(e, claves, tmp_path):
    salida = tmp_path / f"exportacion-{uuid4().hex[:8]}.zip.age"
    assert exportar_consorcio.main(argumentos(e, claves, salida)) == 0
    return descifrar(salida, claves["privada"])


def clave_designacion(designacion: str) -> str:
    """`nucleo.clave_designacion` (`0113`)."""
    return re.sub(r"\s+", " ", designacion.strip()).lower()


def unidad_de_la_lectura(unidades: list[dict], depto: str | None, uf: str | None) -> str | None:
    """`nucleo.unidad_de_la_lectura` (`0113`) sobre el padrón del paquete: las unidades cuya
    designación coincide con el departamento; si ninguna, con el número; una sola o ninguna."""
    for buscado in (depto, uf):
        if buscado is None:
            continue
        ids = [u["id"] for u in unidades
               if clave_designacion(u["designacion"]) == clave_designacion(buscado)]  # fmt: skip
        if ids:
            return ids[0] if len(ids) == 1 else None
    return None


def oficiales_del_paquete(datos: dict, importacion: str) -> list[tuple]:
    """RN30 rehecho solo con el paquete: los datos confirmados y corregidos de la importación, con
    el valor corregido y la unidad del padrón del valor corregido (`campos_oficiales_de` de
    `0119`)."""
    [cabeza] = [x for x in datos["importacion_liquidacion"] if x["id"] == importacion]
    assert cabeza["estado"] in ("confirmada", "reemplazada")
    filas = []
    for p in sorted(datos["campo_preliminar"], key=lambda p: p["orden"]):
        if p["importacion_id"] != importacion or p["estado"] not in ("confirmado", "corregido"):
            continue
        corregido = p["valor_corregido"]
        if corregido is None:
            unidad = p["unidad_id"]
        elif p["tipo"] in TIPOS_DE_UNIDAD:
            unidad = unidad_de_la_lectura(datos["unidad"], corregido.get("depto"),
                                          corregido.get("uf"))  # fmt: skip
        else:
            unidad = None
        filas.append((p["orden"], p["tipo"], p["clave"], corregido or p["valor"], unidad))
    return filas


def oficiales_de_la_base(motor_migrate, e, importacion: str) -> list[tuple]:
    with transaccion(motor_migrate, persona_id=None, consorcio_id=e["cons"]) as c:
        return [
            (f.orden, f.tipo, f.clave, f.valor, None if f.unidad_id is None else str(f.unidad_id))
            for f in c.execute(text("select * from nucleo.campos_oficiales_de(:c, :i)"),
                               {"c": e["cons"], "i": importacion})
        ]  # fmt: skip


def textos(paquete) -> str:
    return "\n".join(paquete.read(n).decode("latin-1") for n in paquete.namelist()).lower()


# --- PA27 ---------------------------------------------------------------------------------------


def test_pa27_con_el_paquete_solo_se_rehace_la_liquidacion_oficial_del_periodo(
    escena, bucket, con_url, claves, tmp_path, motor_app, motor_migrate
):
    e = escena
    i = importadas(e, motor_app, motor_migrate)
    paquete = exportar(e, claves, tmp_path)
    assert verificar_manifiesto(paquete) == []
    datos = entidades(paquete)
    # Cada entidad nueva, en su archivo, en el manifiesto y en la constancia, con todas las filas
    # del consorcio.
    manifiesto = json.loads(paquete.read(exportar_consorcio.MANIFIESTO))
    rutas = {a["ruta"] for a in manifiesto["archivos"]}
    assert {f"{PARTE}/{x}.json" for x in ENTIDADES} <= rutas
    with motor_migrate.connect() as c:
        en_la_base = {
            x: c.execute(text(f"select count(*) from {t} where consorcio_id = :c"),
                         {"c": e["cons"]}).scalar_one()
            for x, t in TABLAS.items()
        }  # fmt: skip
    assert set(TABLAS) == set(ENTIDADES)
    assert en_la_base["importacion_liquidacion"] == 3 and en_la_base["correspondencia_grupo"] == 2
    assert en_la_base["campo_preliminar"] > 3 * 10 and en_la_base["enlace_liquidacion"] == 1
    assert {x: len(datos[x]) for x in ENTIDADES} == en_la_base
    [constancia] = constancias(motor_migrate, e["cons"])
    assert {x: constancia["cantidades"][x] for x in ENTIDADES} == en_la_base
    # Las tres importaciones, con su historia.
    cabezas = {x["id"]: x for x in datos["importacion_liquidacion"]}
    reemplazada, oficial, descartada = (cabezas[i[k]] for k in
                                        ("reemplazada", "oficial", "descartada"))  # fmt: skip
    assert (reemplazada["estado"], oficial["estado"], descartada["estado"]) == (
        "reemplazada", "confirmada", "descartada")  # fmt: skip
    assert (oficial["periodo"], oficial["formato"], oficial["reemplaza_a"]) == (
        SEPTIEMBRE, "mis_expensas", i["reemplazada"])  # fmt: skip
    assert oficial["motivo_reemplazo"] == "Corrigió el rubro 2; cuenta [cuenta omitida]"
    assert descartada["motivo_descarte"] == "Es de otro edificio; cuenta [cuenta omitida]"
    assert None not in (
        oficial["decidida_por_nombre"],
        oficial["decidida_en"],
        descartada["decidida_por_nombre"],
        oficial["subida_por_nombre"],
    )
    with motor_migrate.connect() as c:
        diferencias = c.execute(text(
            "select diferencias from nucleo.importacion_liquidacion where id = :i"),
            {"i": i["oficial"]}).scalar_one()  # fmt: skip
    assert oficial["diferencias"] == diferencias
    # Las evidencias, por su índice: la versión de cada importación está en el paquete, con su
    # suma.
    versiones = {v["id"]: v for v in datos["version"]}
    for x in cabezas.values():
        v = versiones[x["version_documento_id"]]
        assert v["documento_id"] == x["documento_id"]
        assert hashlib.sha256(paquete.read(v["archivo"])).hexdigest() == v["sha256"]
    # El enlace y la correspondencia del recálculo.
    assert [
        (n["importacion_id"], n["orden"], n["url"], n["origen"], n["pagina"])
        for n in datos["enlace_liquidacion"]
    ] == [(i["oficial"], 1, URL, "anotacion", 1)]
    assert [(k["clave"], k["nombre"], k["grupo"], k["version"])
            for k in datos["correspondencia_grupo"]] == [
        ("aysa", "AySA", "agua", 1), ("aysa", "AySA", "expensas_comunes", 2)]  # fmt: skip
    assert None not in {k["definida_por_nombre"] for k in datos["correspondencia_grupo"]}
    # Cada dato, con su renglón y la decisión.
    campos = {(p["importacion_id"], p["clave"]): p for p in datos["campo_preliminar"]}
    rubro = campos[(i["oficial"], "rubro:2")]
    assert (rubro["estado"], rubro["valor_corregido"]["total"], rubro["valor"]["total"]) == (
        "corregido", "999.99", "1000.00")  # fmt: skip
    assert rubro["fragmento"] and rubro["pagina"] >= 1 and rubro["confirmado_por_nombre"]
    assert {p["estado"] for k, p in campos.items() if k[0] == i["descartada"]} == {"pendiente"}
    # Lo que importa: la oficial de septiembre, rehecha solo con el paquete, es la de la base y la
    # que lee el Consejo.
    del_paquete = oficiales_del_paquete(datos, i["oficial"])
    assert del_paquete == oficiales_de_la_base(motor_migrate, e, i["oficial"])
    r = get(e, "mc", "liquidacion-oficial", i["oficial"])
    assert r.status_code == 200, r.text
    assert del_paquete == [(f["orden"], f["tipo"], f["clave"], f["valor"], f["unidad_id"])
                           for f in r.json()["campos"]]  # fmt: skip
    # Con las correcciones: la unidad corregida va a «PB-B» y la que no está en el padrón, a
    # ninguna; lo leído conserva la suya.
    unidad = {clave: u for _, _, clave, _, u in del_paquete}
    assert unidad["unidad:002"] == str(e["u_pb_b"]) and unidad["importe_unidad:002"] is None
    assert unidad["saldo_unidad:002"] == str(e["u"]["1-A"])
    # Control: la comparación ve una diferencia; la reemplazada no es la oficial.
    assert oficiales_del_paquete(datos, i["reemplazada"]) != del_paquete


def test_pa27_lo_que_nunca_va_tampoco_va_en_las_importadas(
    escena, bucket, con_url, claves, tmp_path, motor_app, motor_migrate
):
    """Como `0069`: ningún identificador de persona y ninguna cuenta de 22 dígitos, tampoco en los
    valores leídos o corregidos, los motivos y las diferencias."""
    e = escena
    i = importadas(e, motor_app, motor_migrate)
    paquete = exportar(e, claves, tmp_path)
    todo = textos(paquete)
    with motor_migrate.connect() as c:
        filas = [
            f for tabla, columnas in COLUMNAS_DE_PERSONA.items() for columna in columnas
            for f in c.execute(text(f"select r::text as x, r.{columna} as p from {tabla} r"
                                    f" where r.consorcio_id = :c and r.{columna} is not null"),
                               {"c": e["cons"]})
        ]  # fmt: skip
        en_campos = c.execute(text(
            "select count(*) from nucleo.campo_preliminar p where p.consorcio_id = :c"
            " and p::text like :k"), {"c": e["cons"], "k": f"%{CONCEPTO_CON_CUENTA}%"},
        ).scalar_one()  # fmt: skip
        en_motivos = c.execute(text(
            "select count(*) from nucleo.importacion_liquidacion x where x.consorcio_id = :c"
            " and x::text like :k"), {"c": e["cons"], "k": f"%{CUENTA}%"}).scalar_one()  # fmt: skip
    personas = {str(f.p) for f in filas}
    assert str(e["mc"]) in personas  # la búsqueda tiene qué buscar
    assert {p for p in personas if p in todo} == set()
    # Control: la misma búsqueda, en las filas de la base, los encuentra todos.
    assert {p for p in personas if p in " ".join(f.x for f in filas)} == personas
    # Ninguna cuenta: ni con el separador con que se escribió ni como tira de 22 dígitos.
    assert CUENTA not in todo and CONCEPTO_CON_CUENTA.lower() not in todo
    tira = re.compile(r"(?<!\d)\d{22}(?!\d)")
    nuevos = [f"{PARTE}/{x}.json" for x in ENTIDADES]
    assert [n for n in nuevos if tira.search(paquete.read(n).decode())] == []
    # Control: en la base está, en el concepto corregido y en los dos motivos.
    assert (en_campos, en_motivos) == (1, 2)
    # Va el texto sin la cuenta, en el valor corregido de la reemplazada.
    datos = entidades(paquete)
    [gasto] = [p for p in datos["campo_preliminar"]
               if (p["importacion_id"], p["clave"]) == (i["reemplazada"], "gasto:1:1")]  # fmt: skip
    assert gasto["valor_corregido"]["concepto"] == CONCEPTO_SIN_CUENTA
    # La suma del gasto en IA no va: no hay ningún archivo de `plataforma`.
    assert not [n for n in paquete.namelist() if "gasto_ia" in n]


def test_pa27_las_importadas_de_otro_consorcio_no_aparecen(
    escena, bucket, con_url, claves, tmp_path, motor_app, motor_migrate
):
    e = escena
    importadas(e, motor_app, motor_migrate)
    otro = armar_escena(motor_migrate, motor_app)
    ajena = confirmada(otro, motor_app)
    paquete = exportar(e, claves, tmp_path)
    todo = textos(paquete)

    def ids(cons) -> set[str]:
        with motor_migrate.connect() as c:
            return {str(x) for x in c.execute(text(
                "select id from nucleo.importacion_liquidacion where consorcio_id = :c union all"
                " select id from nucleo.campo_preliminar where consorcio_id = :c"),
                {"c": cons}).scalars()}  # fmt: skip

    propios, ajenos = ids(e["cons"]), ids(otro["cons"])
    assert ajena in ajenos and len(ajenos) > 10 and propios.isdisjoint(ajenos)
    assert {x for x in ajenos if x in todo} == set()
    assert {x for x in propios if x in todo} == propios  # control: la búsqueda encuentra lo propio


# --- La migración -------------------------------------------------------------------------------


def test_0121_ida_y_vuelta(base_aparte):
    cfg, motor = base_aparte
    firma = "nucleo.exportar_liquidaciones_importadas(uuid,uuid)"

    def estado() -> tuple:
        with motor.connect() as c:
            existe = c.execute(text("select to_regprocedure(:f) is not null"),
                               {"f": firma}).scalar_one()  # fmt: skip
            auxiliar = c.execute(text("select to_regprocedure('nucleo.jsonb_sin_cuenta(jsonb)')"
                                      " is not null")).scalar_one()  # fmt: skip
            if not existe:
                return existe, auxiliar, None
            quienes = c.execute(text(
                "select r.rolname from pg_roles r where r.rolname like 'cem\\_%'"
                " and has_function_privilege(r.oid, cast(:f as regprocedure), 'execute')"),
                {"f": firma}).scalars().all()  # fmt: skip
            return existe, auxiliar, sorted(quienes)

    command.upgrade(cfg, "0120")
    assert estado() == (False, False, None)
    command.upgrade(cfg, "0121")
    assert estado() == (True, True, ["cem_migrate", "cem_soporte_exportacion"])
    command.downgrade(cfg, "0120")
    assert estado() == (False, False, None)
    command.upgrade(cfg, "0121")  # control: vuelve a subir limpio
    assert estado() == (True, True, ["cem_migrate", "cem_soporte_exportacion"])
