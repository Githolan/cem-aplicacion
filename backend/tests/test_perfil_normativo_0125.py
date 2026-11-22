"""Perfil normativo mínimo (CABA) y la señal de la liquidación sin el enlace a la documentación
(`0125`; RF01, RF47; diseño P0 S5).

Contra servicios reales: PostgreSQL, el almacén S3 de pruebas, ClamAV y `age`. La escena es la de
`0119` (Modo Consejo); la liquidación es la inventada de H5-1 (`test_mis_expensas.liquidacion`, sin
enlaces), y con un enlace anotado cuando hace falta (`test_enlaces.con_anotacion`). La lectura
con IA queda apagada: ninguna prueba manda nada a un modelo. Datos SINTETICOS / NO REAL.
"""

import importlib.util
from dataclasses import replace
from pathlib import Path
from uuid import UUID

import pytest
from alembic import command
from cem_contracts import perfil_normativo as contrato
from psycopg.errors import InsufficientPrivilege
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db import fijar_ambito, transaccion
from app.liquidacion import imagenes
from scripts import exportar_consorcio
from tests.test_enlaces import con_anotacion
from tests.test_exportacion_2c3 import (  # noqa: F401  (`claves` y `con_url` son fixtures)
    argumentos,
    claves,
    con_url,
    descifrar,
    entidades,
    verificar_manifiesto,
)
from tests.test_exportacion_importadas_0121 import importar
from tests.test_importacion_liquidacion_0113 import bucket, firmas_de_la_imagen, pdf_de
from tests.test_liquidacion_imagen_0117 import escaneado, foto, leida
from tests.test_liquidacion_oficial_0119 import (
    OCTUBRE,
    armar_escena,
    confirmar,
    de_octubre,
    escena,
    get,
)
from tests.test_mis_expensas import liquidacion
from tests.test_modo_operacion import hoy_de
from tests.test_panel_liquidacion_oficial_0120 import del_periodo
from tests.test_vinculo_0014 import base_aparte

pytestmark = [pytest.mark.db, pytest.mark.redis]
# ruff: noqa: F811

__all__ = ["base_aparte", "bucket", "escena", "firmas_de_la_imagen"]

_ruta = Path(__file__).parents[1] / "migrations" / "versions" / "0125_perfil_normativo.py"
_spec = importlib.util.spec_from_file_location("migracion_0125", _ruta)
migracion = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migracion)

URL = "https://comprobantes.ejemplo.com/consorcio/2026-09/comprobantes.pdf"
CABA = migracion.CABA_1
SENAL = text("select * from nucleo.enlace_documentacion_liquidaciones()")
TABLAS_DEL_CONSORCIO = ("nucleo.busqueda_enlaces_liquidacion", "nucleo.consorcio_perfil_normativo")
TABLAS = migracion.TABLAS_NUEVAS


def senal(estado: str) -> dict:
    return {"estado": estado, "jurisdiccion": "caba", "version": 1, "norma": CABA["norma"],
            "fuente": CABA["fuente"]}  # fmt: skip


def hint(error: pytest.ExceptionInfo) -> str | None:
    return error.value.orig.diag.message_hint


def vincular(motor_app, cons: UUID, jurisdiccion: str = "caba") -> tuple:
    """Como `scripts.alta_consorcio`: la plataforma, sin persona, con el consorcio en el ámbito."""
    with transaccion(motor_app, persona_id=None, consorcio_id=cons) as c:
        return tuple(c.execute(text("select * from nucleo.vincular_perfil_normativo(:j)"),
                               {"j": jurisdiccion}).one())  # fmt: skip


def vinculos(motor_migrate, cons: UUID) -> list[tuple]:
    with motor_migrate.connect() as c:
        return [tuple(f) for f in c.execute(text(
            "select b.orden, p.jurisdiccion, p.version from nucleo.consorcio_perfil_normativo b"
            " join nucleo.perfil_normativo p on p.id = b.perfil_id where b.consorcio_id = :c"
            " order by b.orden"), {"c": cons})]  # fmt: skip


def oficial(e, motor_app, contenido: bytes, cuerpo: dict | None = None) -> str:
    i = importar(e, motor_app, contenido)
    r = confirmar(e, i, cuerpo)
    assert r.status_code == 200, r.text
    return i


def en_la_lista(e, quien: str = "mc") -> dict[str, dict | None]:
    r = get(e, quien, "liquidacion-oficial")
    assert r.status_code == 200, r.text
    return {f["id"]: f["enlace_documentacion"] for f in r.json()}


def del_detalle(e, i: str, quien: str = "mc") -> dict | None:
    r = get(e, quien, "liquidacion-oficial", i)
    assert r.status_code == 200, r.text
    return r.json()["enlace_documentacion"]


def del_panel(e, quien: str = "mc") -> dict:
    r = get(e, quien, "panel")
    assert r.status_code == 200, r.text
    return r.json()["liquidacion_vigente"]


def busquedas(motor_migrate, cons: UUID) -> dict[str, bool]:
    with motor_migrate.connect() as c:
        return {str(i): completa for i, completa in c.execute(text(
            "select importacion_id, completa from nucleo.busqueda_enlaces_liquidacion"
            " where consorcio_id = :c"), {"c": cons})}  # fmt: skip


def cambiar_busqueda(motor_migrate, cons: UUID, i: str, completa: bool | None) -> None:
    """Solo en esta base: la búsqueda como la habría dejado otra lectura (`None`: ninguna, como en
    una liquidación leída antes de `0125`)."""
    with motor_migrate.begin() as c:
        c.execute(text("alter table nucleo.busqueda_enlaces_liquidacion disable trigger user"))
        c.execute(text("delete from nucleo.busqueda_enlaces_liquidacion where consorcio_id = :c"
                       " and importacion_id = :i"), {"c": cons, "i": i})  # fmt: skip
        if completa is not None:
            c.execute(text(
                "insert into nucleo.busqueda_enlaces_liquidacion (consorcio_id, importacion_id,"
                " completa) values (:c, :i, :k)"), {"c": cons, "i": i, "k": completa})  # fmt: skip
        c.execute(text("alter table nucleo.busqueda_enlaces_liquidacion enable trigger user"))


@pytest.fixture
def lectura_apagada(monkeypatch):
    monkeypatch.delenv("CEM_LECTURA_COMPROBANTES", raising=False)


# --- La señal (RF47) ----------------------------------------------------------------------------


def test_rf47_la_oficial_sin_enlace_se_senala_en_la_lista_el_detalle_y_el_panel(
    escena, bucket, motor_app, motor_migrate, lectura_apagada
):
    e = escena
    assert vincular(motor_app, e["cons"])[1:] == (1, True)
    sin = oficial(e, motor_app, pdf_de(liquidacion()))
    for quien in ("mc", "ad"):
        assert en_la_lista(e, quien) == {sin: senal("no_encontrado")}, quien
        assert del_detalle(e, sin, quien) == senal("no_encontrado"), quien
        assert del_panel(e, quien)["enlace_documentacion"] == senal("no_encontrado"), quien
    # Control: la que la reemplaza trae un enlace; la reemplazada no se señala (no es la oficial).
    con = oficial(e, motor_app, con_anotacion(pdf_de(liquidacion()), URL),
                  {"reemplaza_a": sin, "motivo": "La administración agregó el enlace"})  # fmt: skip
    assert en_la_lista(e) == {con: senal("encontrado"), sin: None}
    assert (del_detalle(e, con), del_detalle(e, sin)) == (senal("encontrado"), None)
    assert del_panel(e) == {"id": con, "periodo": "2026-09-01", "diferencias": 0,
                            "enlace_documentacion": senal("encontrado")}  # fmt: skip
    # Octubre sin enlace pasa a ser la vigente del panel; septiembre sigue con el suyo.
    octubre = oficial(e, motor_app, pdf_de(de_octubre()))
    assert del_panel(e)["id"] == octubre and del_panel(e)["periodo"] == OCTUBRE
    assert del_panel(e)["enlace_documentacion"] == senal("no_encontrado")
    assert en_la_lista(e) == {octubre: senal("no_encontrado"), con: senal("encontrado"),
                              sin: None}  # fmt: skip


def test_sin_perfil_vinculado_o_antes_de_la_version_no_hay_senal(
    escena, bucket, motor_app, motor_migrate, lectura_apagada
):
    e = escena
    sin = oficial(e, motor_app, pdf_de(liquidacion()))
    assert en_la_lista(e) == {sin: None}
    assert del_panel(e)["enlace_documentacion"] is None
    vincular(motor_app, e["cons"])
    assert en_la_lista(e) == {sin: senal("no_encontrado")}  # control: con el perfil, sí
    # La versión 1 rige desde el 31-7-2024: julio de 2024 no se mide con ella; agosto sí.
    julio = oficial(e, motor_app, pdf_de(del_periodo(2024, 7)))
    agosto = oficial(e, motor_app, pdf_de(del_periodo(2024, 8)))
    lista = en_la_lista(e)
    assert (lista[julio], lista[agosto]) == (None, senal("no_encontrado"))


def test_una_version_que_no_exige_el_enlace_no_senala(
    escena, bucket, motor_app, motor_migrate, lectura_apagada
):
    """Lo que cuenta es lo que dice la versión vinculada. Una versión 2 que no lo exige, vinculada
    después, se prueba en una transacción que no queda (la base de la sesión sigue con la 1)."""
    e = escena
    vincular(motor_app, e["cons"])
    sin = oficial(e, motor_app, pdf_de(liquidacion()))
    with motor_migrate.connect() as c:
        t = c.begin()
        fijar_ambito(c, persona_id=e["mc"], consorcio_id=e["cons"])
        assert [f.estado for f in c.execute(SENAL)] == ["no_encontrado"]  # control
        v2 = c.execute(text(
            "insert into nucleo.perfil_normativo (jurisdiccion, version, vigente_desde,"
            " verificado_el) values ('caba', 2, '2025-01-01', '2026-11-22') returning id"),
        ).scalar_one()  # fmt: skip
        c.execute(text(
            "insert into nucleo.perfil_normativo_requisito (perfil_id, codigo, exige, norma,"
            " fuente) values (:p, :r, false, 'Norma SINTETICA', 'https://norma.ejemplo.com/2')"),
            {"p": v2, "r": migracion.ENLACE})  # fmt: skip
        c.execute(text("insert into nucleo.consorcio_perfil_normativo (consorcio_id, orden,"
                       " perfil_id) values (:c, 2, :p)"), {"c": e["cons"], "p": v2})  # fmt: skip
        assert c.execute(SENAL).all() == []
        t.rollback()
    assert en_la_lista(e) == {sin: senal("no_encontrado")}


def test_la_lectura_deja_la_busqueda_y_si_no_fue_en_todo_el_documento_no_afirma_que_falta(
    escena, bucket, motor_app, motor_migrate, lectura_apagada
):
    e = escena
    sin = oficial(e, motor_app, pdf_de(liquidacion()))
    # Una foto (solo QR), un PDF escaneado (sin texto) y un PDF con texto de más páginas que las que
    # se dibujan (sin QR): se buscó, no en todo. Sin IA quedan no reconocidas; la búsqueda queda.
    de_foto = leida(e, motor_app, foto(), "image/jpeg")["id"]
    escaneada = leida(e, motor_app, escaneado(), "application/pdf")["id"]
    larga = leida(e, motor_app, pdf_de(liquidacion() * (imagenes.MAXIMO_PAGINAS + 1)),
                  "application/pdf")["id"]  # fmt: skip
    assert busquedas(motor_migrate, e["cons"]) == {sin: True, de_foto: False, escaneada: False,
                                                   larga: False}  # fmt: skip
    vincular(motor_app, e["cons"])
    assert en_la_lista(e) == {sin: senal("no_encontrado")}  # control
    cambiar_busqueda(motor_migrate, e["cons"], sin, False)
    assert en_la_lista(e) == {sin: senal("sin_buscar")}
    cambiar_busqueda(motor_migrate, e["cons"], sin, None)  # leída antes de 0125
    assert en_la_lista(e) == {sin: senal("sin_buscar")}
    assert del_panel(e)["enlace_documentacion"] == senal("sin_buscar")


def test_un_pdf_con_una_pagina_sin_texto_no_es_una_busqueda_completa(
    escena, bucket, motor_app, motor_migrate, lectura_apagada
):
    """Codex en #282: una página escaneada puede llevar la dirección impresa, que no se lee. Un PDF
    con una página de texto y otra sin texto, sin ningún enlace: la búsqueda no es completa y la
    señal no afirma que falte. Control: el mismo PDF sin la página vacía sí es completo."""
    e = escena
    vincular(motor_app, e["cons"])
    texto = liquidacion()
    mixto = pdf_de([*texto, replace(texto[0], palabras=())])
    i = oficial(e, motor_app, mixto)
    assert busquedas(motor_migrate, e["cons"]) == {i: False}
    assert en_la_lista(e) == {i: senal("sin_buscar")}
    assert del_panel(e)["enlace_documentacion"] == senal("sin_buscar")
    reemplazo = {"reemplaza_a": i, "motivo": "La administración mandó otra versión"}
    solo_texto = oficial(e, motor_app, pdf_de(texto), reemplazo)
    assert busquedas(motor_migrate, e["cons"])[solo_texto] is True
    assert en_la_lista(e)[solo_texto] == senal("no_encontrado")


def test_lo_ven_solo_quienes_ven_el_panel_y_otro_consorcio_no_ve_nada(
    escena, bucket, motor_app, motor_migrate, lectura_apagada
):
    e = escena
    vincular(motor_app, e["cons"])
    oficial(e, motor_app, pdf_de(liquidacion()))
    otro = armar_escena(motor_migrate, motor_app)
    vincular(motor_app, otro["cons"])
    with transaccion(motor_app, persona_id=e["mc"], consorcio_id=e["cons"]) as c:
        assert len(c.execute(SENAL).all()) == 1  # control
    with transaccion(motor_app, persona_id=otro["mc"], consorcio_id=otro["cons"]) as c:
        assert c.execute(SENAL).all() == []
    for quien in ("prop", "inq", "sin_confirmar"):
        with (
            pytest.raises(DBAPIError) as error,
            transaccion(motor_app, persona_id=e[quien], consorcio_id=e["cons"]) as c,
        ):
            c.execute(SENAL).all()
        assert isinstance(error.value.orig, InsufficientPrivilege), quien
    with motor_migrate.connect() as cx:
        rls = cx.execute(text(
            "select c.oid::regclass::text, c.relrowsecurity, c.relforcerowsecurity,"
            " (select p.qual from pg_policies p where p.schemaname = 'nucleo'"
            "   and p.tablename = c.relname and p.policyname = 'por_consorcio')"
            " from pg_class c where c.oid = any(cast(:t as regclass[])) order by 1"),
            {"t": list(TABLAS_DEL_CONSORCIO)}).all()  # fmt: skip
        politica = "(consorcio_id = plataforma.consorcio_actual())"
        assert [tuple(f) for f in rls] == [(t, True, True, politica)
                                           for t in sorted(TABLAS_DEL_CONSORCIO)]  # fmt: skip
        for tabla in TABLAS:
            assert not cx.execute(
                text("select has_table_privilege('cem_app', :t, 'select, insert, update, delete')"),
                {"t": tabla}).scalar_one(), tabla  # fmt: skip


# --- El perfil (RF01) ---------------------------------------------------------------------------


def test_vincular_lo_hace_la_plataforma_con_la_version_vigente_y_no_repite(
    escena, motor_app, motor_migrate
):
    e = escena
    with (
        pytest.raises(DBAPIError) as error,
        transaccion(motor_app, persona_id=e["mc"], consorcio_id=e["cons"]) as c,
    ):
        c.execute(text("select * from nucleo.vincular_perfil_normativo('caba')")).all()
    assert isinstance(error.value.orig, InsufficientPrivilege)
    assert vinculos(motor_migrate, e["cons"]) == []
    primero = vincular(motor_app, e["cons"])
    assert primero[1:] == (1, True)
    assert vincular(motor_app, e["cons"]) == (primero[0], 1, False)  # la misma: no escribe
    assert vinculos(motor_migrate, e["cons"]) == [(1, "caba", 1)]
    with pytest.raises(DBAPIError) as error:
        vincular(motor_app, e["cons"], "pba")
    assert hint(error) == "sin_perfil_vigente"
    # Una versión nueva vigente hoy: el consorcio pasa a ella con otro vínculo; el primero queda.
    with motor_migrate.connect() as c:
        t = c.begin()
        c.execute(text(
            "insert into nucleo.perfil_normativo (jurisdiccion, version, vigente_desde,"
            " verificado_el) values ('caba', 2, '2025-01-01', '2026-11-22')"))  # fmt: skip
        fijar_ambito(c, persona_id=None, consorcio_id=e["cons"])
        vinculo = text("select version, vinculado from nucleo.vincular_perfil_normativo('caba')")
        assert c.execute(vinculo).one() == (2, True)
        assert c.execute(text(
            "select b.orden, p.version from nucleo.consorcio_perfil_normativo b"
            " join nucleo.perfil_normativo p on p.id = b.perfil_id where b.consorcio_id = :c"
            " order by b.orden"), {"c": e["cons"]}).all() == [(1, 1), (2, 2)]  # fmt: skip
        actual = text("select jurisdiccion, version from nucleo.perfil_normativo_del_consorcio()")
        assert c.execute(actual).one() == ("caba", 2)
        t.rollback()


def test_cambiar_el_perfil_es_publicar_otra_version_nunca_un_cambio(escena, motor_app,
                                                                    motor_migrate):  # fmt: skip
    e = escena
    vincular(motor_app, e["cons"])
    intentos = [
        "update nucleo.perfil_normativo set vigente_desde = '2020-01-01'",
        "delete from nucleo.perfil_normativo",
        "truncate nucleo.perfil_normativo cascade",
        "update nucleo.perfil_normativo_requisito set exige = false",
        "delete from nucleo.perfil_normativo_requisito",
        "update nucleo.consorcio_perfil_normativo set orden = 9",
        "delete from nucleo.consorcio_perfil_normativo",
        # La versión 1 ya la usa un consorcio: no se le agrega ni se le cambia un requisito.
        "insert into nucleo.perfil_normativo_requisito (perfil_id, codigo, exige, norma, fuente)"
        " select id, 'liquidacion_enlace_documentacion', false, 'x', 'https://x.ejemplo.com'"
        " from nucleo.perfil_normativo where version = 1",
    ]
    for sql in intentos:
        with pytest.raises(DBAPIError) as error:
            with motor_migrate.begin() as c:
                c.execute(text(sql))
        assert hint(error) in ("historia_inmutable",), sql
    nueva = ("insert into nucleo.perfil_normativo (jurisdiccion, version, vigente_desde,"
             " verificado_el) values ('caba', :v, :d, '2026-11-22')")  # fmt: skip
    for version, desde in ((3, "2027-01-01"), (2, CABA["vigente_desde"]), (2, "2024-01-01")):
        with pytest.raises(DBAPIError) as error:
            with motor_migrate.begin() as c:
                c.execute(text(nueva), {"v": version, "d": desde})
        assert hint(error) == "version_de_perfil_invalida", (version, desde)
    with motor_migrate.connect() as c:
        t = c.begin()
        c.execute(text(nueva), {"v": 2, "d": "2027-01-01"})  # control: la siguiente, después
        t.rollback()
        actual = c.execute(text(
            "select p.version, p.vigente_desde::text, p.verificado_el::text, r.codigo, r.exige,"
            " r.norma, r.fuente from nucleo.perfil_normativo p"
            " join nucleo.perfil_normativo_requisito r on r.perfil_id = p.id"
            " where p.jurisdiccion = 'caba'")).all()  # fmt: skip
    assert [tuple(f) for f in actual] == [
        (1, CABA["vigente_desde"], CABA["verificado_el"], migracion.ENLACE, True, CABA["norma"],
         CABA["fuente"])
    ]  # fmt: skip


def test_la_migracion_y_el_contrato_dicen_lo_mismo():
    assert migracion.JURISDICCIONES == tuple(contrato.Jurisdiccion)
    assert migracion.REQUISITOS == tuple(contrato.RequisitoNormativo)
    assert migracion.ESTADOS == tuple(contrato.EstadoEnlaceDocumentacion)
    assert migracion.LARGO_NORMA == contrato.LARGO_NORMA
    assert migracion.ENLACE == contrato.RequisitoNormativo.LIQUIDACION_ENLACE_DOCUMENTACION
    assert len(CABA["norma"]) <= contrato.LARGO_NORMA


def test_la_migracion_vincula_los_consorcios_que_hay_y_baja_solo_sin_datos(base_aparte):
    cfg, motor = base_aparte

    def tablas() -> list[bool]:
        with motor.connect() as c:
            return [c.execute(text("select to_regclass(:t) is not null"), {"t": t}).scalar_one()
                    for t in TABLAS]  # fmt: skip

    command.upgrade(cfg, "0125")  # sin consorcios
    assert tablas() == [True] * len(TABLAS)
    command.downgrade(cfg, "0124")
    assert tablas() == [False] * len(TABLAS)
    with motor.begin() as c:
        cons = c.execute(text("insert into nucleo.consorcio (nombre) values ('A') returning id"),
                         ).scalar_one()  # fmt: skip
    command.upgrade(cfg, "0125")
    assert vinculos(motor, cons) == [(1, "caba", 1)]
    with pytest.raises(DBAPIError) as error:
        command.downgrade(cfg, "0124")
    assert hint(error) == "downgrade_con_datos"


# --- Exportación --------------------------------------------------------------------------------


def test_la_exportacion_lleva_el_perfil_vinculado_y_las_busquedas_del_consorcio(
    escena, bucket, con_url, claves, tmp_path, motor_app, motor_migrate, lectura_apagada
):
    e = escena
    vincular(motor_app, e["cons"])
    sin = oficial(e, motor_app, pdf_de(liquidacion()))
    otro = armar_escena(motor_migrate, motor_app)
    vincular(motor_app, otro["cons"])
    ajena = oficial(otro, motor_app, pdf_de(liquidacion()))
    e["hoy"] = hoy_de(motor_app, e["cons"])
    salida = tmp_path / "exportacion-perfil.zip.age"
    assert exportar_consorcio.main(argumentos(e, claves, salida)) == 0
    paquete = descifrar(salida, claves["privada"])
    assert verificar_manifiesto(paquete) == []
    datos = entidades(paquete)
    [perfil] = datos["perfil_normativo"]
    assert perfil.pop("vinculado_en")
    assert perfil == {
        "orden": 1, "jurisdiccion": "caba", "version": 1,
        "vigente_desde": CABA["vigente_desde"], "verificado_el": CABA["verificado_el"],
        "requisitos": [{"codigo": migracion.ENLACE, "exige": True, "norma": CABA["norma"],
                        "fuente": CABA["fuente"]}],
    }  # fmt: skip
    [busqueda] = datos["busqueda_enlaces_liquidacion"]
    assert (busqueda["importacion_id"], busqueda["completa"]) == (sin, True)
    assert ajena not in str(datos)  # control: la del otro consorcio existe y no va
    assert busquedas(motor_migrate, otro["cons"]) == {ajena: True}
