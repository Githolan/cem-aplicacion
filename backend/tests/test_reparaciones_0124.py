"""Reparaciones a cargo del consorcio (`0124`; RF19 con la regla de cierre de RF20, Modo Consejo,
diseño P0 S10 a S12 y plan §5.2 y §6.8 del repositorio CEM).

Contra servicios reales: PostgreSQL, el almacén S3 de pruebas y ClamAV. La escena es la de `0123`
(Modo Consejo con la liquidación de septiembre confirmada): un miembro del Consejo, la
administración sin vínculo, la propietaria confirmada de «1° A» y «1-A», una propietaria sin
confirmar de «1-A», una inquilina confirmada de «PB-A» y una ocupante confirmada de «PB-A». Cada
negación lleva su control: el mismo pedido de quien sí puede, o en el estado que sí lo admite, pasa.
Datos SINTETICOS / NO REAL.
"""

import importlib.util
import json
import threading
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from cem_contracts import reparaciones as contrato
from psycopg.errors import InsufficientPrivilege
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.db import fijar_ambito, transaccion
from scripts import exportar_consorcio
from tests.conftest import clave_idempotencia
from tests.test_documentos_2c1 import EICAR, PNG, analizar, pdf
from tests.test_exportacion_2c3 import (  # noqa: F401  (`claves` y `con_url` son fixtures)
    argumentos,
    claves,
    con_url,
    descifrar,
    entidades,
    verificar_manifiesto,
)
from tests.test_liquidacion_oficial_0119 import (  # noqa: F401  (los fixtures se importan)
    armar_escena,
    bucket,
    confirmada,
    confirmar,
    esperar_trabado,
    firmas_de_la_imagen,
    importar,
    persona_con_vinculo,
)
from tests.test_modo_operacion import decidir_equipo, hoy_de
from tests.test_orden_pago_neutra import cbu_sintetico, nueva_sesion
from tests.test_rutas_egresos import cliente
from tests.test_vinculo_0014 import base_aparte

pytestmark = [pytest.mark.db, pytest.mark.redis]
# ruff: noqa: F811

__all__ = ["base_aparte", "bucket", "firmas_de_la_imagen"]

VERSIONES = Path(__file__).resolve().parent.parent / "migrations/versions"
MIGRACION = VERSIONES / "0124_reparaciones.py"
DESCRIPCION = "Pierde agua la canilla del baño y mancha el techo de abajo.\nPRUEBA SINTETICA."
MOTIVO = "El proveedor no consiguió el repuesto. Datos de PRUEBA."
CUENTA = cbu_sintetico(44)
TABLAS = (
    "nucleo.reparacion",
    "nucleo.reparacion_adjunto",
    "nucleo.reparacion_gestion",
    "nucleo.reparacion_estado",
)
PEDIR = text("select * from nucleo.pedir_reparacion('D', :u, :l, :a, 'api', :s)")
LEER = text("select * from nucleo.reparaciones_lectura(:a, 'api', :s)")
ENLAZAR = text("select * from nucleo.enlazar_gasto_reparacion(:r, :i, :k, :a, 'api', :s)")
ORDEN = ("en_gestion", "presupuestada", "aprobada", "terminada_gasto_pendiente", "cerrada")


def _migracion():
    spec = importlib.util.spec_from_file_location("migracion_0124", MIGRACION)
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def armar(motor_migrate, motor_app, *, con_liquidacion: bool = True) -> dict:
    """La escena de `0119` con una ocupante confirmada de «PB-A» y, si se pide, la liquidación de
    septiembre confirmada (con sus gastos)."""
    e = armar_escena(motor_migrate, motor_app)
    e["oc"] = persona_con_vinculo(motor_migrate, e["cons"], e["u"]["PB-A"], "ocupante",
                                  confirmado=True)  # fmt: skip
    e["sesion_oc"] = nueva_sesion(motor_migrate, e["oc"])
    e["motor_app"] = motor_app
    e["motor_migrate"] = motor_migrate
    e["hoy"] = hoy_de(motor_app, e["cons"])
    e["liq"] = confirmada(e, motor_app) if con_liquidacion else None
    return e


@pytest.fixture
def e(motor_migrate, motor_app, bucket) -> dict:
    return armar(motor_migrate, motor_app)


def url(e: dict, *partes: str) -> str:
    return "/".join([f"/api/v1/consorcios/{e['cons']}/reparaciones", *map(str, partes)])


def cuerpo(e: dict, unidad: str | None = "1-A", **cambios) -> dict:
    base = {"descripcion": DESCRIPCION}
    if unidad is not None:
        base["unidad_id"] = str(e["u"][unidad])
    return base | cambios


def pedir(e: dict, quien: str = "prop", datos: dict | None = None, clave: dict | None = None):
    with cliente(e[quien], e[f"sesion_{quien}"]) as c:
        return c.post(url(e), json=cuerpo(e) if datos is None else datos,
                      headers=clave or clave_idempotencia())  # fmt: skip


def pedida(e: dict, quien: str = "prop", unidad: str | None = "1-A", **cambios) -> dict:
    r = pedir(e, quien, cuerpo(e, unidad, **cambios))
    assert r.status_code == 201, r.text
    return r.json()


def actuar(e: dict, quien: str, rep: dict, acto: str, datos: dict):
    with cliente(e[quien], e[f"sesion_{quien}"]) as c:
        return c.post(url(e, rep["id"], acto), json=datos, headers=clave_idempotencia())


def leer(e: dict, quien: str, *partes):
    with cliente(e[quien], e[f"sesion_{quien}"]) as c:
        return c.get(url(e, *partes))


def subir(e: dict, quien: str, rep: dict, tipo: str, contenido: bytes | None = None,
          mime: str = "application/pdf"):  # fmt: skip
    with cliente(e[quien], e[f"sesion_{quien}"]) as c:
        return c.post(url(e, rep["id"], "adjuntos"), params={"tipo": tipo},
                      content=pdf() if contenido is None else contenido,
                      headers={"Content-Type": mime} | clave_idempotencia())  # fmt: skip


def disponible(e: dict, quien: str, rep: dict, tipo: str, **kw) -> str:
    """Sube el archivo y corre el antivirus: devuelve el `adjunto_id`, ya disponible."""
    r = subir(e, quien, rep, tipo, **kw)
    assert r.status_code == 201, r.text
    assert analizar(e["motor_app"]) >= 1
    return r.json()["adjunto_id"]


def gastos(e: dict, liquidacion: str | None = None) -> list[str]:
    with e["motor_migrate"].connect() as c:
        sql = text("select clave from nucleo.campos_oficiales_de(:c, :i) where tipo = 'gasto'"
                   " order by orden")  # fmt: skip
        return list(c.execute(sql, {"c": e["cons"], "i": liquidacion or e["liq"]}).scalars())


def paso(e: dict, rep: dict, estado: str, quien: str = "mc", **kw):
    """El acto que lleva la reparación a `estado`, con lo que pide; devuelve la respuesta."""
    if estado == "en_gestion":
        return actuar(e, quien, rep, "gestion", {"responsable_id": str(e[quien]),
                                                 "prioridad": "normal"})  # fmt: skip
    if estado == "presupuestada":
        a = kw.get("adjunto") or disponible(e, quien, rep, "presupuesto")
        return actuar(
            e,
            quien,
            rep,
            "presupuesto",
            {"adjunto_id": a, "proveedor_nombre": "Plomería SINTETICA"},
        )
    if estado == "aprobada":
        return actuar(e, quien, rep, "aprobacion", {"aprobado_por": "Asamblea SINTETICA",
                                                    "decision_id": str(e["acta"])})  # fmt: skip
    if estado == "terminada_gasto_pendiente":
        a = kw.get("adjunto") or disponible(e, quien, rep, "factura")
        return actuar(e, quien, rep, "terminacion", {"adjunto_id": a})
    assert estado == "cerrada"
    return actuar(e, quien, rep, "gasto", {"liquidacion_id": kw.get("liquidacion", e["liq"]),
                                           "clave": kw["clave"]})  # fmt: skip


def hasta(e: dict, rep: dict, estado: str, **kw) -> dict:
    for siguiente in ORDEN[: ORDEN.index(estado) + 1]:
        r = paso(e, rep, siguiente, **(kw if siguiente == estado else {}))
        assert r.status_code == 200, r.text
        assert r.json()["estado"] == siguiente
        rep = r.json()
    return rep


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


def cuantas(motor_migrate, tabla: str, cons) -> int:
    with motor_migrate.connect() as c:
        return c.execute(
            text(f"select count(*) from {tabla} where consorcio_id = :c"), {"c": cons}
        ).scalar_one()


def codigo(r) -> tuple[int, str]:
    return r.status_code, r.json()["codigo"]


def hint(error: pytest.ExceptionInfo) -> str | None:
    return error.value.orig.diag.message_hint


def restriccion(error: pytest.ExceptionInfo) -> str | None:
    return error.value.orig.diag.constraint_name


def en_la_base(motor_app, e: dict, quien: str, sql, **p):
    """La función de la base con la persona y su sesión, deshecha al final."""
    with motor_app.connect() as cx, cx.begin() as t:
        fijar_ambito(cx, persona_id=e[quien], consorcio_id=e["cons"])
        filas = cx.execute(sql, {"a": e[quien], "s": e[f"sesion_{quien}"]} | p).all()
        t.rollback()
        return filas


def insertar_estado(motor_migrate, e: dict, rep: dict, **columnas) -> None:
    """Un paso escrito directo en la tabla, salteando las funciones (la última barrera)."""
    fila = {"consorcio_id": e["cons"], "reparacion_id": rep["id"], "registrado_por": e["mc"]}
    fila |= columnas
    nombres = ", ".join(fila)
    valores = ", ".join(f":{k}" for k in fila)
    with motor_migrate.begin() as cx:
        cx.execute(text(f"insert into nucleo.reparacion_estado ({nombres}) values ({valores})"),
                   fila)  # fmt: skip


# Constantes: una sola fuente ----------------------------------------------------------------


def test_la_migracion_copia_las_constantes_del_contrato():
    m = _migracion()
    assert (m.LARGO_DESCRIPCION, m.LARGO_LUGAR, m.LARGO_MOTIVO, m.LARGO_PROVEEDOR,
            m.LARGO_APROBADO_POR, m.FOTOS_MAXIMO) == (
        contrato.LARGO_DESCRIPCION, contrato.LARGO_LUGAR, contrato.LARGO_MOTIVO,
        contrato.LARGO_PROVEEDOR, contrato.LARGO_APROBADO_POR, contrato.FOTOS_MAXIMO)  # fmt: skip
    assert set(m.ESTADOS) == {x.value for x in contrato.EstadoReparacion}
    assert set(m.ABIERTOS) == {x.value for x in contrato.ABIERTOS}
    assert set(m.PRIORIDADES) == {x.value for x in contrato.Prioridad}
    assert set(m.TIPOS_ADJUNTO) == {x.value for x in contrato.TipoAdjunto}


# Pedir --------------------------------------------------------------------------------------


def test_propietaria_inquilina_y_ocupante_piden_y_avisa_a_quien_gestiona_sin_el_texto(
    e, motor_migrate
):
    a = pedida(e)
    assert (a["numero"], a["estado"], a["designacion"], a["propia"]) == (1, "pedida", "1-A", True)
    assert a["descripcion"] == DESCRIPCION and a["gestion"] is None
    assert [p["estado"] for p in a["historia"]] == ["pedida"]
    assert a["acciones"] == ["agregar_foto", "retirar"]
    assert pedida(e, "inq", "PB-A")["numero"] == 2
    assert pedida(e, "oc", "PB-A")["numero"] == 3
    # Un espacio común, con el lugar.
    comun = pedida(e, "inq", None, lugar="Palier del 2.º piso")
    assert (comun["unidad_id"], comun["designacion"], comun["lugar"]) == (
        None, None, "Palier del 2.º piso")  # fmt: skip
    enviados = correos(motor_migrate, e["cons"])
    gestionan = sorted(correo_de(motor_migrate, e[q]) for q in ("mc", "ad"))
    assert sorted(x["carga"]["para"] for x in enviados) == sorted(gestionan * 4)
    for x in enviados:
        carga = x["carga"]
        assert "https://cem.ejemplo.com/reparaciones" in carga["texto"]
        assert "N.º" in carga["asunto"]
        for prohibido in ("canilla", "1-A", "PB-A", "Palier", "@"):
            assert prohibido not in carga["texto"] + carga["asunto"], prohibido


def test_sin_confirmar_no_pide_ni_por_una_unidad_ajena_y_la_base_niega_igual(
    e, motor_app, motor_migrate
):
    assert codigo(pedir(e, "sin_confirmar", cuerpo(e, "1-A"))) == (403, "sin_permiso")
    assert codigo(pedir(e, "prop", cuerpo(e, "PB-A"))) == (403, "sin_permiso")
    assert codigo(pedir(e, "inq", cuerpo(e, "1-A"))) == (403, "sin_permiso")
    assert cuantas(motor_migrate, "nucleo.reparacion", e["cons"]) == 0
    assert correos(motor_migrate, e["cons"]) == []
    # Segunda barrera: la base niega por su cuenta (42501).
    for quien, unidad in (("sin_confirmar", "1-A"), ("prop", "PB-A"), ("inq", "1-A")):
        with pytest.raises(DBAPIError) as error:
            en_la_base(motor_app, e, quien, PEDIR, u=e["u"][unidad], l=None)
        assert isinstance(error.value.orig, InsufficientPrivilege), quien
    with pytest.raises(DBAPIError) as error:
        en_la_base(motor_app, e, "sin_confirmar", PEDIR, u=None, l="Terraza")
    assert isinstance(error.value.orig, InsufficientPrivilege)
    # Controles: la propietaria por «1-A» y quien gestiona por cualquier unidad pasan.
    assert len(en_la_base(motor_app, e, "prop", PEDIR, u=e["u"]["1-A"], l=None)) == 2
    assert len(en_la_base(motor_app, e, "ad", PEDIR, u=e["u"]["PB-A"], l=None)) == 1
    assert pedida(e, "mc", "PB-A")["propia"] is True


def test_el_numero_es_correlativo_por_consorcio_y_unico(e, motor_migrate, motor_app):
    otro = armar(motor_migrate, motor_app, con_liquidacion=False)
    assert [pedida(e)["numero"] for _ in range(3)] == [1, 2, 3]
    assert pedida(otro)["numero"] == 1  # control: cada edificio cuenta desde 1
    with pytest.raises(IntegrityError) as error:
        with motor_migrate.begin() as cx:
            cx.execute(text(
                "insert into nucleo.reparacion (consorcio_id, numero, descripcion, unidad_id,"
                " pedida_por) values (:c, 2, 'D', :u, :p)"),
                {"c": e["cons"], "u": e["u"]["1-A"], "p": e["prop"]})  # fmt: skip
    assert restriccion(error) == "reparacion_consorcio_id_numero_key"


def test_una_cuenta_en_un_texto_se_rechaza(e, motor_migrate):
    for datos, campo in ((cuerpo(e, descripcion=f"Pagar a {CUENTA}"), "descripcion"),
                         (cuerpo(e, None, lugar=f"CBU {CUENTA}"), "descripcion")):  # fmt: skip
        r = pedir(e, datos=datos)
        assert codigo(r) == (422, "texto_con_cuenta")
        assert r.json()["detalle"] == {"campos": [f"body.{campo}"]}
    assert cuantas(motor_migrate, "nucleo.reparacion", e["cons"]) == 0
    assert correos(motor_migrate, e["cons"]) == []
    rep = pedida(e, descripcion=f"Expediente {CUENTA[:21]}.")  # control: 21 dígitos no son
    r = actuar(e, "mc", rep, "no-realizada", {"motivo": f"Transferir a {CUENTA}"})
    assert codigo(r) == (422, "texto_con_cuenta")
    assert r.json()["detalle"] == {"campos": ["body.motivo"]}
    assert actuar(e, "mc", rep, "no-realizada", {"motivo": MOTIVO}).status_code == 200


def test_un_espacio_comun_lleva_el_lugar(e, motor_migrate):
    assert codigo(pedir(e, datos=cuerpo(e, None))) == (422, "peticion_invalida")
    with pytest.raises(IntegrityError) as error:
        with motor_migrate.begin() as cx:
            cx.execute(text(
                "insert into nucleo.reparacion (consorcio_id, numero, descripcion, pedida_por)"
                " values (:c, 9, 'D', :p)"), {"c": e["cons"], "p": e["prop"]})  # fmt: skip
    assert restriccion(error) == "reparacion_con_lugar"
    control = pedida(e, "prop", None, lugar="Sala de máquinas")
    assert control["lugar"] == "Sala de máquinas"


def test_reintentar_con_la_misma_clave_no_repite_el_pedido_ni_los_correos(e, motor_migrate):
    clave = clave_idempotencia()
    primera = pedir(e, clave=clave)
    segunda = pedir(e, clave=clave)
    assert segunda.status_code == 201 and segunda.headers.get("Idempotent-Replayed") == "true"
    assert segunda.json() == primera.json()
    assert cuantas(motor_migrate, "nucleo.reparacion", e["cons"]) == 1
    assert len(correos(motor_migrate, e["cons"])) == 2
    assert pedir(e).status_code == 201  # control: otra clave es otro pedido
    assert len(correos(motor_migrate, e["cons"])) == 4


# Fotos: por el antivirus, hasta el máximo ---------------------------------------------------


def test_las_fotos_entran_por_el_antivirus_y_solo_se_ven_disponibles(e, motor_migrate):
    rep = pedida(e)
    r = subir(e, "prop", rep, "foto", PNG, "image/png")
    assert r.status_code == 201, r.text
    assert r.json()["archivo"]["estado"] == "en_cuarentena"
    foto = r.json()["adjunto_id"]
    # Quien la subió la ve en cuarentena; quien gestiona, todavía no; el contenido, aún no.
    assert [(a["id"], a["estado"]) for a in leer(e, "prop", rep["id"]).json()["adjuntos"]] == [
        (foto, "en_cuarentena")]  # fmt: skip
    assert leer(e, "mc", rep["id"]).json()["adjuntos"] == []
    assert leer(e, "prop", rep["id"], "adjuntos", foto, "contenido").status_code == 409
    assert leer(e, "mc", rep["id"], "adjuntos", foto, "contenido").status_code == 404
    assert analizar(e["motor_app"]) >= 1
    vista = leer(e, "mc", rep["id"]).json()["adjuntos"]
    assert [(a["id"], a["tipo"], a["estado"], a["propio"]) for a in vista] == [
        (foto, "foto", "disponible", False)]  # fmt: skip
    contenido = leer(e, "mc", rep["id"], "adjuntos", foto, "contenido")
    assert contenido.status_code == 200 and contenido.content == PNG
    assert contenido.headers["x-content-type-options"] == "nosniff"
    # Una con virus queda rechazada: nadie más la ve y no cuenta para el máximo.
    mala = subir(e, "prop", rep, "foto", pdf(flujo=EICAR))
    assert mala.status_code == 201
    assert analizar(e["motor_app"]) >= 1
    assert [a["id"] for a in leer(e, "mc", rep["id"]).json()["adjuntos"]] == [foto]
    propias = {a["id"]: a["estado"] for a in leer(e, "prop", rep["id"]).json()["adjuntos"]}
    assert propias[mala.json()["adjunto_id"]] == "rechazado"
    # Hasta el máximo; la siguiente se niega y la ficha ya no la ofrece.
    for _ in range(contrato.FOTOS_MAXIMO - 1):
        assert subir(e, "prop", rep, "foto", PNG, "image/png").status_code == 201
    assert codigo(subir(e, "prop", rep, "foto", PNG, "image/png")) == (409, "fotos_completas")
    assert "agregar_foto" not in leer(e, "prop", rep["id"]).json()["acciones"]
    # Solo quien la pidió: otra persona con permiso de pedir, 404; quien gestiona, también.
    otra = pedida(e, "inq", "PB-A")
    assert "agregar_foto" in otra["acciones"]  # control: en la suya, sí
    assert codigo(subir(e, "prop", otra, "foto", PNG, "image/png")) == (404, "no_encontrado")
    assert codigo(subir(e, "mc", otra, "foto", PNG, "image/png")) == (404, "no_encontrado")
    # Una foto la sube quien pide; un presupuesto, solo quien gestiona.
    assert codigo(subir(e, "inq", otra, "presupuesto")) == (403, "sin_permiso")
    # El documento queda en «Otros» (restringido) y respalda la reparación: no se retira.
    with motor_migrate.connect() as cx:
        categorias = set(
            cx.execute(
                text(
                    "select d.categoria from nucleo.reparacion_adjunto a join nucleo.documento d"
                    " on d.id = a.documento_id where a.consorcio_id = :c"
                ),
                {"c": e["cons"]},
            ).scalars()
        )
        assert categorias == {"otros"}
        assert cx.execute(text(
            "select nucleo.documento_referenciado(:c, d.id) from nucleo.reparacion_adjunto a"
            " join nucleo.documento d on d.id = a.documento_id where a.id = :a"),
            {"c": e["cons"], "a": foto}).scalar_one() is True  # fmt: skip


# Estados ------------------------------------------------------------------------------------


def test_cada_paso_valido_y_un_paso_fuera_de_orden_se_niega(e, motor_migrate):
    rep = pedida(e)
    claves = gastos(e)
    assert claves, "la liquidación de la escena tiene gastos"
    fuera = {  # en cada estado, el acto de otro estado
        "pedida": ("terminacion", {"adjunto_id": str(uuid4())}),
        "en_gestion": ("aprobacion", {"aprobado_por": "X", "decision_id": str(e["acta"])}),
        "presupuestada": ("gasto", {"liquidacion_id": e["liq"], "clave": claves[0]}),
        "aprobada": ("gasto", {"liquidacion_id": e["liq"], "clave": claves[0]}),
        "terminada_gasto_pendiente": ("aprobacion", {"aprobado_por": "X",
                                                     "decision_id": str(e["acta"])}),
    }  # fmt: skip
    estado = "pedida"
    for siguiente in ORDEN:
        acto, datos = fuera[estado]
        assert codigo(actuar(e, "mc", rep, acto, datos)) == (409, "reparacion_no_admite"), estado
        r = paso(e, rep, siguiente, clave=claves[0])
        assert r.status_code == 200, r.text
        rep, estado = r.json(), siguiente
        assert rep["estado"] == siguiente
    assert [p["estado"] for p in rep["historia"]] == ["pedida", *ORDEN]
    assert rep["acciones"] == []
    # Cerrada, ya nada: ni no realizada, ni otra gestión.
    assert codigo(actuar(e, "mc", rep, "no-realizada", {"motivo": MOTIVO})) == (
        409, "reparacion_no_admite")  # fmt: skip
    assert codigo(paso(e, rep, "en_gestion")) == (409, "reparacion_no_admite")
    aprobada = next(p for p in rep["historia"] if p["estado"] == "aprobada")
    assert (aprobada["aprobado_por"], aprobada["facultad"], aprobada["decision_id"]) == (
        "Asamblea SINTETICA", "reparaciones.gestionar", str(e["acta"]))  # fmt: skip
    presupuesto = next(p for p in rep["historia"] if p["estado"] == "presupuestada")
    assert presupuesto["proveedor_nombre"] == "Plomería SINTETICA"
    cierre = rep["historia"][-1]
    assert cierre["gasto"]["clave"] == claves[0] and cierre["gasto"]["liquidacion_id"] == e["liq"]


def test_la_base_impone_las_transiciones_aunque_se_salte_la_funcion(e, motor_migrate):
    rep = pedida(e)
    # Saltear un estado, o citar un anterior que no es el último, choca con la base.
    for columnas, nombre in (
        ({"estado": "aprobada", "anterior": "pedida", "aprobado_por": "X",
          "facultad": "reparaciones.gestionar", "decision_id": e["acta"]},
         "reparacion_transicion_valida"),
        ({"estado": "no_realizada", "anterior": "aprobada", "motivo": "M"},
         "reparacion_estado_reparacion_id_anterior_fkey"),
        ({"estado": "pedida", "anterior": None}, "reparacion_estado_pkey"),
    ):  # fmt: skip
        with pytest.raises(IntegrityError) as error:
            insertar_estado(motor_migrate, e, rep, **columnas)
        assert restriccion(error) == nombre, columnas
    # Control: el paso válido entra; un segundo sucesor del mismo estado, no.
    insertar_estado(motor_migrate, e, rep, estado="en_gestion", anterior="pedida")
    with pytest.raises(IntegrityError) as error:
        insertar_estado(motor_migrate, e, rep, estado="retirada", anterior="pedida", motivo="M")
    assert restriccion(error) == "reparacion_un_sucesor"
    assert leer(e, "prop", rep["id"]).json()["estado"] == "en_gestion"


def test_terminar_sin_la_factura_se_niega(e, motor_migrate):
    rep = hasta(e, pedida(e), "presupuestada")
    # Una constancia de aprobación propia, disponible y sin usar (se aprueba con la decisión).
    constancia = disponible(e, "mc", rep, "aprobacion")
    r = paso(e, rep, "aprobada")
    assert r.status_code == 200, r.text
    rep = r.json()
    assert codigo(actuar(e, "mc", rep, "terminacion", {})) == (422, "peticion_invalida")
    # Un documento de otro tipo no es la factura, aunque esté disponible y sin usar.
    r = actuar(e, "mc", rep, "terminacion", {"adjunto_id": constancia})
    assert codigo(r) == (422, "adjunto_invalido")
    # Un presupuesto no es una factura; una factura en cuarentena, todavía no sirve.
    presupuesto = next(p["adjunto_id"] for p in rep["historia"] if p["estado"] == "presupuestada")
    r = actuar(e, "mc", rep, "terminacion", {"adjunto_id": presupuesto})
    assert codigo(r) == (422, "adjunto_invalido")
    assert r.json()["detalle"] == {"campos": ["body.adjunto_id"]}
    en_cuarentena = subir(e, "mc", rep, "factura")
    assert en_cuarentena.status_code == 201
    factura = en_cuarentena.json()["adjunto_id"]
    assert codigo(actuar(e, "mc", rep, "terminacion", {"adjunto_id": factura})) == (
        422, "adjunto_invalido")  # fmt: skip
    # La que subió otra persona que gestiona tampoco.
    ajena = disponible(e, "ad", rep, "factura")
    assert codigo(actuar(e, "mc", rep, "terminacion", {"adjunto_id": ajena})) == (
        422, "adjunto_invalido")  # fmt: skip
    # Y la base no deja un paso terminado sin documento.
    with pytest.raises(IntegrityError) as error:
        insertar_estado(motor_migrate, e, rep, estado="terminada_gasto_pendiente",
                        anterior="aprobada")  # fmt: skip
    assert restriccion(error) == "reparacion_documento_del_estado"
    # Control: la factura propia, ya disponible, sí.
    analizar(e["motor_app"])
    r = actuar(e, "mc", rep, "terminacion", {"adjunto_id": factura})
    assert r.status_code == 200, r.text
    assert r.json()["estado"] == "terminada_gasto_pendiente"


def test_terminada_espera_el_gasto_y_se_cierra_al_enlazarlo(motor_migrate, motor_app, bucket):
    e = armar(motor_migrate, motor_app, con_liquidacion=False)
    e["liq"] = None
    rep = pedida(e)
    for siguiente in ORDEN[:-1]:
        r = paso(e, rep, siguiente)
        assert r.status_code == 200, r.text
        rep = r.json()
    # Sin liquidación oficial no hay gasto: no se ofrece el cierre y la reparación espera.
    assert rep["estado"] == "terminada_gasto_pendiente"
    assert "enlazar_gasto" not in rep["acciones"] and rep["gastos_enlazables"] == []
    assert rep["acciones"] == ["gestionar", "no_realizada"]
    # Llega la liquidación: el gasto aparece para enlazar.
    e["liq"] = confirmada(e, motor_app)
    rep = leer(e, "mc", rep["id"]).json()
    assert rep["acciones"][0] == "enlazar_gasto"
    clave = gastos(e)[0]
    assert {(g["liquidacion_id"], g["clave"]) for g in rep["gastos_enlazables"]} == {
        (e["liq"], k) for k in gastos(e)}  # fmt: skip
    antes = len(correos(motor_migrate, e["cons"]))
    r = paso(e, rep, "cerrada", clave=clave)
    assert r.status_code == 200, r.text
    assert r.json()["estado"] == "cerrada"
    nuevos = correos(motor_migrate, e["cons"])[antes:]
    assert [x["carga"]["para"] for x in nuevos] == [correo_de(motor_migrate, e["prop"])]
    # El gasto enlazado ya no se ofrece para otra.
    assert clave not in {g["clave"] for g in leer(e, "mc", rep["id"]).json()["gastos_enlazables"]}


def test_pa11_un_gasto_cierra_una_sola_reparacion(e, motor_migrate, motor_app):
    """Análogo de PA11: la factura de una reparación queda enlazada a un solo gasto, y ese gasto
    no cierra otra (ni por la API, ni con dos enlaces a la vez, ni salteando la función)."""
    claves = gastos(e)
    assert len(claves) >= 2, claves
    a, b, c, d = (hasta(e, pedida(e), "terminada_gasto_pendiente") for _ in range(4))
    assert paso(e, a, "cerrada", clave=claves[0]).status_code == 200
    r = paso(e, b, "cerrada", clave=claves[0])
    assert codigo(r) == (409, "gasto_ya_enlazado")
    assert r.json()["detalle"] == {"campos": ["body.clave"]}
    assert leer(e, "mc", b["id"]).json()["estado"] == "terminada_gasto_pendiente"
    # Dos enlaces del mismo gasto a la vez: el segundo espera el candado y pierde.
    fallas: list[BaseException] = []

    def otro():
        try:
            with transaccion(motor_app, persona_id=e["mc"], consorcio_id=e["cons"]) as cx:
                cx.execute(ENLAZAR, {"r": d["id"], "i": e["liq"], "k": claves[1], "a": e["mc"],
                                     "s": e["sesion_mc"]})  # fmt: skip
        except BaseException as falla:  # se mira en el hilo de la prueba
            fallas.append(falla)

    with transaccion(motor_app, persona_id=e["mc"], consorcio_id=e["cons"]) as cx:
        pid = cx.execute(text("select pg_backend_pid()")).scalar_one()
        cx.execute(ENLAZAR, {"r": c["id"], "i": e["liq"], "k": claves[1], "a": e["mc"],
                             "s": e["sesion_mc"]})  # fmt: skip
        hilo = threading.Thread(target=otro)
        hilo.start()
        assert esperar_trabado(motor_migrate, pid) == 1  # control: el segundo espera
    hilo.join()
    assert len(fallas) == 1 and isinstance(fallas[0], DBAPIError), fallas
    assert fallas[0].orig.diag.message_hint == "gasto_ya_enlazado"
    assert [leer(e, "mc", x["id"]).json()["estado"] for x in (c, d)] == [
        "cerrada", "terminada_gasto_pendiente"]  # fmt: skip
    # La última barrera: un cierre escrito directo con el mismo gasto choca con el índice.
    with pytest.raises(IntegrityError) as error:
        insertar_estado(motor_migrate, e, d, estado="cerrada",
                        anterior="terminada_gasto_pendiente", importacion_id=e["liq"],
                        gasto_clave=claves[1])  # fmt: skip
    assert restriccion(error) == "reparacion_un_gasto"


def test_el_gasto_es_de_una_liquidacion_confirmada_del_mismo_consorcio(e, motor_migrate, motor_app):
    rep = hasta(e, pedida(e), "terminada_gasto_pendiente")
    vieja = e["liq"]
    clave = gastos(e)[0]
    otro = armar(motor_migrate, motor_app)
    # La de otro edificio, una sin confirmar y una clave que no figura: no.
    assert codigo(paso(e, rep, "cerrada", liquidacion=otro["liq"], clave=clave)) == (
        422, "gasto_no_oficial")  # fmt: skip
    sin_confirmar = importar(e, motor_app)
    assert codigo(paso(e, rep, "cerrada", liquidacion=sin_confirmar, clave=clave)) == (
        422, "gasto_no_oficial")  # fmt: skip
    assert codigo(paso(e, rep, "cerrada", clave="gasto:99:99")) == (422, "gasto_no_oficial")
    # La reemplazada tampoco, aunque el gasto figure.
    r = confirmar(e, sin_confirmar, {"reemplaza_a": vieja, "motivo": "Llegó corregida"})
    assert r.status_code == 200, r.text
    assert codigo(paso(e, rep, "cerrada", liquidacion=vieja, clave=clave)) == (
        422, "gasto_no_oficial")  # fmt: skip
    # Control: el mismo gasto de la vigente, sí.
    r = paso(e, rep, "cerrada", liquidacion=sin_confirmar, clave=clave)
    assert r.status_code == 200, r.text
    assert r.json()["historia"][-1]["gasto"]["liquidacion_id"] == sin_confirmar


def test_no_realizada_lleva_el_motivo(e, motor_migrate):
    rep = hasta(e, pedida(e), "presupuestada")
    assert codigo(actuar(e, "mc", rep, "no-realizada", {})) == (422, "peticion_invalida")
    assert codigo(actuar(e, "mc", rep, "no-realizada", {"motivo": " "})) == (
        422, "peticion_invalida")  # fmt: skip
    with pytest.raises(IntegrityError) as error:
        insertar_estado(motor_migrate, e, rep, estado="no_realizada", anterior="presupuestada")
    assert restriccion(error) == "reparacion_motivo_del_estado"
    # Quien pide no la da por no realizada.
    assert codigo(actuar(e, "prop", rep, "no-realizada", {"motivo": MOTIVO})) == (
        403, "sin_permiso")  # fmt: skip
    r = actuar(e, "ad", rep, "no-realizada", {"motivo": MOTIVO})
    assert r.status_code == 200, r.text
    assert (r.json()["estado"], r.json()["historia"][-1]["motivo"]) == ("no_realizada", MOTIVO)
    assert r.json()["acciones"] == []
    # También desde la terminada que espera el gasto (RF20: sin evidencia, con motivo).
    otra = hasta(e, pedida(e), "terminada_gasto_pendiente")
    assert actuar(e, "mc", otra, "no-realizada", {"motivo": MOTIVO}).status_code == 200


def test_retirar_solo_quien_la_pidio_mientras_esta_pedida(e, motor_migrate):
    rep = pedida(e)
    motivo = {"motivo": "Lo arreglé por mi cuenta."}
    assert codigo(actuar(e, "mc", rep, "retiro", motivo)) == (403, "sin_permiso")
    assert codigo(actuar(e, "inq", rep, "retiro", motivo)) == (404, "no_encontrado")
    r = actuar(e, "prop", rep, "retiro", motivo)
    assert r.status_code == 200, r.text
    hecho = r.json()
    assert (hecho["estado"], hecho["historia"][-1]["motivo"]) == ("retirada", motivo["motivo"])
    assert codigo(actuar(e, "prop", rep, "retiro", motivo)) == (409, "reparacion_no_admite")
    # En gestión ya no se retira.
    otra = hasta(e, pedida(e), "en_gestion")
    assert "retirar" not in leer(e, "prop", otra["id"]).json()["acciones"]
    assert codigo(actuar(e, "prop", otra, "retiro", motivo)) == (409, "reparacion_no_admite")


def test_el_responsable_tiene_la_facultad_y_el_cuit_se_valida(e):
    rep = pedida(e)
    r = actuar(e, "mc", rep, "gestion", {"responsable_id": str(e["prop"]), "prioridad": "urgente"})
    assert codigo(r) == (422, "responsable_invalido")
    assert r.json()["detalle"] == {"campos": ["body.responsable_id"]}
    assert leer(e, "mc", rep["id"]).json()["estado"] == "pedida"
    r = actuar(e, "mc", rep, "gestion", {"responsable_id": str(e["ad"]), "prioridad": "urgente",
                                         "fecha_comprometida": "2026-12-01"})  # fmt: skip
    assert r.status_code == 200, r.text
    rep = r.json()
    assert rep["gestion"] == {"responsable_id": str(e["ad"]), "responsable_nombre":
                              rep["gestion"]["responsable_nombre"], "prioridad": "urgente",
                              "fecha_comprometida": "2026-12-01"}  # fmt: skip
    assert {p["id"] for p in rep["responsables_posibles"]} == {str(e["mc"]), str(e["ad"])}
    # Cambiar el responsable no es otro estado.
    r = actuar(e, "mc", rep, "gestion", {"responsable_id": str(e["mc"]), "prioridad": "normal"})
    assert r.json()["estado"] == "en_gestion" and r.json()["gestion"]["responsable_id"] == str(
        e["mc"])  # fmt: skip
    presupuesto = disponible(e, "mc", rep, "presupuesto")
    r = actuar(e, "mc", rep, "presupuesto", {"adjunto_id": presupuesto, "proveedor_nombre": "P",
                                             "proveedor_cuit": "20123456780"})  # fmt: skip
    assert codigo(r) == (422, "cuit_invalido")
    r = actuar(e, "mc", rep, "presupuesto", {"adjunto_id": presupuesto, "proveedor_nombre": "P",
                                             "proveedor_cuit": "20123456786"})  # fmt: skip
    assert r.status_code == 200, r.text
    assert r.json()["historia"][-1]["proveedor_cuit"] == "20123456786"


def test_la_constancia_de_aprobacion_cita_una_decision_del_edificio_o_un_documento(
    e, motor_app, motor_migrate
):
    rep = hasta(e, pedida(e), "presupuestada")
    assert {d["id"] for d in rep["decisiones_citables"]} >= {str(e["acta"])}
    otro = armar(motor_migrate, motor_app, con_liquidacion=False)
    ajena = decidir_equipo(motor_app, otro["cons"])
    r = actuar(e, "mc", rep, "aprobacion", {"aprobado_por": "Asamblea", "decision_id": str(ajena)})
    assert codigo(r) == (422, "constancia_invalida")
    firmado = disponible(e, "mc", rep, "aprobacion")
    ambas = {"aprobado_por": "A", "decision_id": str(e["acta"]), "adjunto_id": firmado}
    assert codigo(actuar(e, "mc", rep, "aprobacion", ambas)) == (422, "peticion_invalida")
    r = actuar(e, "mc", rep, "aprobacion", {"aprobado_por": "Administración SINTETICA",
                                            "adjunto_id": firmado})  # fmt: skip
    assert r.status_code == 200, r.text
    paso_ = r.json()["historia"][-1]
    assert (paso_["adjunto_id"], paso_["decision_id"], paso_["facultad"]) == (
        firmado, None, "reparaciones.gestionar")  # fmt: skip


# Lo registrado no se cambia -----------------------------------------------------------------


def test_una_reparacion_no_se_cambia_ni_se_borra_y_cem_app_no_escribe(e, motor_migrate):
    rep = hasta(e, pedida(e), "en_gestion")
    intentos = [
        "update nucleo.reparacion set descripcion = 'Otra' where id = :r",
        "delete from nucleo.reparacion where id = :r",
        "update nucleo.reparacion_estado set registrado_en = now() where reparacion_id = :r",
        "delete from nucleo.reparacion_gestion where reparacion_id = :r",
        "truncate nucleo.reparacion_estado",
        "truncate nucleo.reparacion_gestion",
    ]
    for sql in intentos:
        with pytest.raises(DBAPIError) as error:
            with motor_migrate.begin() as cx:
                cx.execute(text(sql), {"r": rep["id"]})
        assert hint(error) == "historia_inmutable", sql
    assert leer(e, "mc", rep["id"]).json() == rep  # sigue igual
    with motor_migrate.connect() as cx:
        rls = cx.execute(text(
            "select c.oid::regclass::text, c.relrowsecurity, c.relforcerowsecurity,"
            " (select p.qual from pg_policies p where p.schemaname = 'nucleo'"
            "   and p.tablename = c.relname and p.policyname = 'por_consorcio')"
            " from pg_class c where c.oid = any(cast(:t as regclass[])) order by 1"),
            {"t": list(TABLAS)}).all()  # fmt: skip
        politica = "(consorcio_id = plataforma.consorcio_actual())"
        assert [tuple(f) for f in rls] == [(t, True, True, politica) for t in sorted(TABLAS)]
        for tabla in TABLAS:
            assert not cx.execute(
                text("select has_table_privilege('cem_app', :t, 'select, insert, update, delete')"),
                {"t": tabla}).scalar_one(), tabla  # fmt: skip


# Quién lee ----------------------------------------------------------------------------------


def test_quien_la_pidio_ve_las_suyas_quien_gestiona_todas_y_nadie_mas(e, motor_app):
    propia = pedida(e)
    ajena = pedida(e, "inq", "PB-A")
    de_prop = leer(e, "prop").json()
    assert [x["id"] for x in de_prop["elementos"]] == [propia["id"]]
    assert (de_prop["gestiona"], de_prop["pide"]) == (False, True)
    assert {u["designacion"] for u in de_prop["unidades"]} == {"1° A", "1-A"}
    assert leer(e, "prop", ajena["id"]).status_code == 404
    # La ocupante de la misma unidad que la inquilina no ve lo que pidió la inquilina.
    assert leer(e, "oc").json()["elementos"] == []
    for quien in ("mc", "ad"):
        lista = leer(e, quien).json()
        assert lista["gestiona"] is True
        assert [x["id"] for x in lista["elementos"]] == [ajena["id"], propia["id"]], quien
        assert {u["designacion"] for u in lista["unidades"]} == set(e["u"])
    assert leer(e, "sin_confirmar").status_code == 403
    with pytest.raises(DBAPIError) as error:
        en_la_base(motor_app, e, "sin_confirmar", LEER)
    assert isinstance(error.value.orig, InsufficientPrivilege)
    assert len(en_la_base(motor_app, e, "prop", LEER)) == 1  # control


def test_otro_consorcio_no_ve_ni_recibe_y_su_propio_pedido_si(e, motor_migrate, motor_app):
    otro = armar(motor_migrate, motor_app, con_liquidacion=False)
    rep = pedida(e)
    ajenos = {correo_de(motor_migrate, otro[q]) for q in ("mc", "ad")}
    assert not ajenos & {x["carga"]["para"] for x in correos(motor_migrate, e["cons"])}
    with cliente(otro["mc"], otro["sesion_mc"]) as cx:
        assert cx.get(url(e)).status_code == 404
        assert cx.get(url(e, rep["id"])).status_code == 404
        assert (
            cx.post(
                url(e, rep["id"], "gestion"),
                json={"responsable_id": str(otro["mc"]), "prioridad": "normal"},
                headers=clave_idempotencia(),
            ).status_code
            == 404
        )
    assert leer(otro, "mc", rep["id"]).status_code == 404
    assert leer(otro, "mc").json()["elementos"] == []
    pedida(otro)  # control: el otro edificio recibe el aviso de su propio pedido
    assert ajenos <= {x["carga"]["para"] for x in correos(motor_migrate, otro["cons"])}


# Correos ------------------------------------------------------------------------------------


def test_cada_cambio_de_estado_le_avisa_a_quien_la_pidio_sin_el_texto(e, motor_migrate):
    rep = pedida(e)
    antes = len(correos(motor_migrate, e["cons"]))
    rep = hasta(e, rep, "cerrada", clave=gastos(e)[0])
    nuevos = correos(motor_migrate, e["cons"])[antes:]
    assert [x["carga"]["para"] for x in nuevos] == [correo_de(motor_migrate, e["prop"])] * 5
    for x in nuevos:
        assert f"N.º {rep['numero']}" in x["carga"]["asunto"]
        assert "https://cem.ejemplo.com/reparaciones" in x["carga"]["texto"]
        assert "canilla" not in x["carga"]["texto"] and "Plomería" not in x["carga"]["texto"]
    # Lo que hace la propia persona no le llega: su retiro, ni lo que gestiona de lo que pidió.
    otra = pedida(e)
    antes = len(correos(motor_migrate, e["cons"]))
    actuar(e, "prop", otra, "retiro", {"motivo": "Ya no hace falta."})
    assert len(correos(motor_migrate, e["cons"])) == antes
    del_consejo = pedida(e, "mc", "1-A")
    avisados = [x["carga"]["para"] for x in correos(motor_migrate, e["cons"])[antes:]]
    assert avisados == [correo_de(motor_migrate, e["ad"])]  # el aviso del pedido, no a sí mismo
    antes = len(correos(motor_migrate, e["cons"]))
    hasta(e, del_consejo, "en_gestion")
    assert len(correos(motor_migrate, e["cons"])) == antes


# Facultad y migración -----------------------------------------------------------------------


def test_un_mandato_registrado_antes_de_0124_no_recibe_la_facultad(base_aparte):
    cfg, motor = base_aparte
    command.upgrade(cfg, "0123")

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
        sql = ("select tipo::text from nucleo.paquete_facultad"
               " where facultad = 'reparaciones.gestionar'")  # fmt: skip
        with motor.connect() as c:
            return set(c.execute(text(sql)).scalars())

    def definiciones() -> tuple[str, str]:
        with motor.connect() as c:
            return tuple(
                c.execute(
                    text(
                        "select pg_get_functiondef(cast(f as regprocedure)) from unnest(array["
                        "'nucleo.documento_referenciado(uuid, uuid)',"
                        " 'nucleo.fijar_version_objeto(uuid, text, uuid, text)']) f"
                    )
                ).scalars()
            )

    antes = alta_soporte()
    previas = definiciones()
    assert "reclamos.atender" in facultades(antes)  # control: el paquete se copió
    command.upgrade(cfg, "0124")
    assert paquetes() == {"miembro_consejo", "administrador"}
    assert "reparaciones.gestionar" not in facultades(antes)
    assert all("reparacion" in d for d in definiciones())
    command.downgrade(cfg, "0123")  # sin datos de 0124: baja y deja todo como estaba
    assert paquetes() == set() and definiciones() == previas
    command.upgrade(cfg, "0124")  # control: vuelve a subir limpio
    despues = alta_soporte()
    assert "reparaciones.gestionar" in facultades(despues)  # control: el alta nueva sí
    with pytest.raises(DBAPIError) as error:
        command.downgrade(cfg, "0123")
    assert hint(error) == "downgrade_con_datos"


# Exportación --------------------------------------------------------------------------------


def test_la_exportacion_lleva_las_reparaciones_sin_identificadores_de_personas(
    e, motor_migrate, con_url, claves, tmp_path
):
    a = hasta(e, pedida(e), "cerrada", clave=gastos(e)[0])
    b = pedida(e, "inq", None, lugar="Terraza")
    actuar(e, "inq", b, "retiro", {"motivo": "Ya se arregló"})
    salida = tmp_path / f"exportacion-{uuid4().hex[:8]}.zip.age"
    assert exportar_consorcio.main(argumentos(e, claves, salida)) == 0
    paquete = descifrar(salida, claves["privada"])
    assert verificar_manifiesto(paquete) == []
    datos = entidades(paquete)
    filas = [(x["id"], x["numero"], x["unidad_designacion"], x["lugar"], x["descripcion"])
             for x in datos["reparacion"]]  # fmt: skip
    assert filas == [(a["id"], 1, "1-A", None, DESCRIPCION),
                     (b["id"], 2, None, "Terraza", DESCRIPCION)]  # fmt: skip
    pasos = [(x["reparacion_id"], x["estado"]) for x in datos["reparacion_estado"]]
    assert pasos == [(a["id"], "pedida"), *((a["id"], s) for s in ORDEN), (b["id"], "pedida"),
                     (b["id"], "retirada")]  # fmt: skip
    assert {x["tipo"] for x in datos["reparacion_adjunto"]} == {"presupuesto", "factura"}
    assert [x["prioridad"] for x in datos["reparacion_gestion"]] == ["normal"]
    nombres = (
        {x["pedida_por_nombre"] for x in datos["reparacion"]}
        | {x["registrado_por_nombre"] for x in datos["reparacion_estado"]}
        | {x["responsable_nombre"] for x in datos["reparacion_gestion"]}
        | {x["subido_por_nombre"] for x in datos["reparacion_adjunto"]}
    )
    assert all(nombres)
    crudo = json.dumps(
        [
            datos[k]
            for k in ("reparacion", "reparacion_estado", "reparacion_gestion", "reparacion_adjunto")
        ]
    )
    with motor_migrate.connect() as cx:
        personas = cx.execute(text(
            "select p.id::text, p.correo from nucleo.persona p where p.id in"
            " (select pedida_por from nucleo.reparacion where consorcio_id = :c"
            "  union select registrado_por from nucleo.reparacion_estado where consorcio_id = :c)"),
            {"c": e["cons"]}).all()  # fmt: skip
    assert len(personas) == 3  # control: la propietaria, la inquilina y el Consejo
    for pid, direccion in personas:
        assert pid not in crudo and direccion not in crudo


def test_la_exportacion_tapa_una_cuenta_aunque_la_entrada_la_rechace(e, motor_migrate, con_url,
                                                                   claves, tmp_path):  # fmt: skip
    rep = pedida(e)
    with motor_migrate.begin() as cx:
        cx.execute(text("alter table nucleo.reparacion disable trigger user"))
        cx.execute(text("update nucleo.reparacion set descripcion = :t where id = :r"),
                   {"t": f"Cuenta {CUENTA}", "r": rep["id"]})  # fmt: skip
        cx.execute(text("alter table nucleo.reparacion enable trigger user"))
    salida = tmp_path / f"exportacion-{uuid4().hex[:8]}.zip.age"
    assert exportar_consorcio.main(argumentos(e, claves, salida)) == 0
    [fila] = entidades(descifrar(salida, claves["privada"]))["reparacion"]
    assert CUENTA not in fila["descripcion"] and "Cuenta " in fila["descripcion"]
