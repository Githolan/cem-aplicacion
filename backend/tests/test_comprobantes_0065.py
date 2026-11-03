"""La evidencia y la conciliación por ejecución, no por orden (`0065`, revisión final de H3).

`0063`/`0064` daban por «vio la cuenta» haberla visto en cualquier ejecución manual de la orden, y
`exigir_no_ejecuto` miraba solo la ejecución que se concilia. Quien vio la cuenta en una ejecución
anterior podía subir el archivo que el registro de la siguiente toma como evidencia, y después
conciliarla. La escena es la de `test_pago_manual_0055`. Datos SINTETICOS / NO REAL.
"""

import importlib.util
from pathlib import Path
from uuid import UUID

import pytest
from alembic import command
from sqlalchemy import text

from app.db import transaccion
from tests.test_cobertura_comprobantes import documento_del_pago, mandato_de
from tests.test_comprobantes_0063 import CREAR, TITULO, VERSION, con_version
from tests.test_orden_pago_neutra import (
    APROBAR,
    EMITIR,
    MODIFICAR,
    huella,
    llamar,
    niega,
    orden,
    otorgar,
)
from tests.test_pago_manual_0055 import (
    CONCILIAR,
    CONFIRMAR,
    CUENTA,
    DECLARAR,
    REGISTRAR,
    VER,
    acta,
    emitida,
    evidencia,
    otro_ejecutor,
    registrada,
)
from tests.test_pago_manual_0055 import e as e  # noqa: F401  (es un fixture)
from tests.test_vinculo_0014 import base_aparte  # noqa: F401  (es un fixture)

pytestmark = pytest.mark.db


def ver_version(motor_migrate, e, quien, o, version: int) -> dict:
    with transaccion(motor_migrate, persona_id=quien, consorcio_id=e.cons) as c:
        return dict(c.execute(text(VER), {
            "orden": o, "version": version, "huella": huella(motor_migrate, o, version),
            "actor": quien, "origen": "api", "sesion": e.sesion[quien],
        }).mappings().one())  # fmt: skip


def segunda_ejecucion(motor_migrate, motor_app, e) -> tuple[UUID, UUID]:
    """x (del Consejo, con `ejecutar_pago` y la carga de documentos) ve la cuenta de la primera
    ejecución y declara que no se pagó; mc2 lo confirma. La administración hace una versión
    nueva, mc1 la aprueba y la administración la emite: la segunda ejecución."""
    o = emitida(motor_migrate, e)
    x = otro_ejecutor(motor_migrate, motor_app, e)
    otorgar(motor_migrate, e.cons, mandato_de(motor_migrate, e, x), acta(motor_migrate, e),
            "documentos.cargar")  # fmt: skip
    ver_version(motor_migrate, e, x, o, 1)
    d = llamar(motor_migrate, e, x, DECLARAR, orden=o, version=1,
               motivo="SINTETICO: el banco rechazó la transferencia")  # fmt: skip
    llamar(motor_migrate, e, e.mc2, CONFIRMAR, orden=o, declaracion=d)
    assert orden(motor_migrate, o)["estado"] == "fallida"
    llamar(motor_migrate, e, e.adm, MODIFICAR, orden=o, version=1, concepto="Reintento SINTETICO")
    if orden(motor_migrate, o)["estado"] == "en_revision":
        llamar(motor_migrate, e, e.mc1, APROBAR, orden=o, version=2,
               huella=huella(motor_migrate, o, 2))  # fmt: skip
    llamar(motor_migrate, e, e.adm, EMITIR, orden=o, version=2)
    assert orden(motor_migrate, o)["estado"] == "en_ejecucion"
    return o, x


def test_quien_vio_la_cuenta_de_una_ejecucion_anterior_no_carga_la_evidencia_de_otra(
    motor_migrate, motor_app, e
):
    o, x = segunda_ejecucion(motor_migrate, motor_app, e)
    ver_version(motor_migrate, e, e.adm, o, 2)
    niega(motor_migrate, e, x, CREAR, "no_es_quien_ejecuto", orden=o, titulo=TITULO)
    doc = llamar(motor_migrate, e, e.adm, CREAR, orden=o, titulo=TITULO)  # control
    con_version(motor_migrate, e, doc, e.adm)
    niega(motor_migrate, e, x, VERSION, "no_encontrado", documento=doc)
    assert llamar(motor_migrate, e, e.adm, VERSION, documento=doc) is not None  # control


def test_quien_vio_la_cuenta_de_una_ejecucion_anterior_no_concilia_la_siguiente(
    motor_migrate, motor_app, e
):
    o, x = segunda_ejecucion(motor_migrate, motor_app, e)
    ver_version(motor_migrate, e, e.adm, o, 2)
    doc = llamar(motor_migrate, e, e.adm, CREAR, orden=o, titulo=TITULO)
    con_version(motor_migrate, e, doc, e.adm)
    p = llamar(motor_migrate, e, e.adm, REGISTRAR,
               **evidencia(motor_migrate, e, o, version=2, huella=huella(motor_migrate, o, 2),
                           documento=doc))  # fmt: skip
    datos = {"orden": o, "pago": p, "resultado": "conforme", "motivo": None}
    niega(motor_migrate, e, x, CONCILIAR, "no_es_quien_ejecuto", **datos)
    llamar(motor_migrate, e, e.mc2, CONCILIAR, **datos)  # control
    assert orden(motor_migrate, o)["estado"] == "pagada"


def test_quien_vio_la_cuenta_de_una_ejecucion_anterior_no_confirma_que_no_se_pago_otra(
    motor_migrate, motor_app, e
):
    o, x = segunda_ejecucion(motor_migrate, motor_app, e)
    ver_version(motor_migrate, e, e.adm, o, 2)
    d = llamar(motor_migrate, e, e.adm, DECLARAR, orden=o, version=2,
               motivo="SINTETICO: otra vez rechazada")  # fmt: skip
    niega(motor_migrate, e, x, CONFIRMAR, "no_es_quien_ejecuto", orden=o, declaracion=d)
    llamar(motor_migrate, e, e.mc2, CONFIRMAR, orden=o, declaracion=d)  # control
    assert orden(motor_migrate, o)["estado"] == "fallida"


def test_la_administracion_que_ejecuto_las_dos_carga_el_comprobante_de_la_segunda(motor_migrate, e):
    """El caso del piloto: la misma administración ve la cuenta de las dos ejecuciones; en la
    segunda sigue cargando el comprobante y registrando, y otro consejero concilia."""
    o = emitida(motor_migrate, e)
    ver_version(motor_migrate, e, e.adm, o, 1)
    d = llamar(motor_migrate, e, e.adm, DECLARAR, orden=o, version=1,
               motivo="SINTETICO: el banco rechazó la transferencia")  # fmt: skip
    llamar(motor_migrate, e, e.mc2, CONFIRMAR, orden=o, declaracion=d)
    llamar(motor_migrate, e, e.adm, MODIFICAR, orden=o, version=1, concepto="Reintento SINTETICO")
    if orden(motor_migrate, o)["estado"] == "en_revision":
        llamar(motor_migrate, e, e.mc1, APROBAR, orden=o, version=2,
               huella=huella(motor_migrate, o, 2))  # fmt: skip
    llamar(motor_migrate, e, e.adm, EMITIR, orden=o, version=2)
    ver_version(motor_migrate, e, e.adm, o, 2)
    doc = llamar(motor_migrate, e, e.adm, CREAR, orden=o, titulo=TITULO)
    con_version(motor_migrate, e, doc, e.adm)
    p = llamar(motor_migrate, e, e.adm, REGISTRAR,
               **evidencia(motor_migrate, e, o, version=2, huella=huella(motor_migrate, o, 2),
                           documento=doc))  # fmt: skip
    llamar(motor_migrate, e, e.mc2, CONCILIAR, orden=o, pago=p, resultado="conforme",
           motivo=None)  # fmt: skip
    assert orden(motor_migrate, o)["estado"] == "pagada"


def test_con_la_orden_pagada_nadie_cambia_la_evidencia(motor_migrate, e):
    o = emitida(motor_migrate, e)
    p = registrada(motor_migrate, e, o)
    doc = documento_del_pago(motor_migrate, p)
    assert llamar(motor_migrate, e, e.adm, VERSION, documento=doc) is not None  # antes: sube
    llamar(motor_migrate, e, e.mc2, CONCILIAR, orden=o, pago=p, resultado="conforme",
           motivo=None)  # fmt: skip
    niega(motor_migrate, e, e.adm, VERSION, "no_encontrado", documento=doc)


@pytest.mark.parametrize("numero", [f"CBU{CUENTA}", f"{CUENTA}7", f"7{CUENTA}"])
def test_el_numero_de_operacion_no_lleva_la_cuenta_adentro(motor_migrate, e, numero):
    o = emitida(motor_migrate, e)
    ver_version(motor_migrate, e, e.adm, o, 1)
    niega(motor_migrate, e, e.adm, REGISTRAR, "numero_operacion_invalido",
          **evidencia(motor_migrate, e, o, numero=numero))  # fmt: skip
    # Control: 24 dígitos que no contienen ninguna cuenta válida pasan.
    llamar(motor_migrate, e, e.adm, REGISTRAR, **evidencia(motor_migrate, e, o, numero="1" * 24))


def test_despues_de_una_observacion_quien_ejecuto_sube_otra_version_y_registra_de_nuevo(
    motor_migrate, motor_app, e
):
    """P3 176: con la orden en verificación (pago observado) la ejecución sigue abierta, así que
    quien vio su cuenta le sube otra versión al comprobante y vuelve a registrar con ese mismo
    documento; otro consejero concilia. Quien no vio la cuenta no lo encuentra."""
    o = emitida(motor_migrate, e)
    p1 = registrada(motor_migrate, e, o)
    doc = documento_del_pago(motor_migrate, p1)
    llamar(motor_migrate, e, e.mc2, CONCILIAR, orden=o, pago=p1, resultado="observado",
           motivo="SINTETICO: el comprobante no se lee")  # fmt: skip
    assert orden(motor_migrate, o)["estado"] == "en_verificacion"
    x = otro_ejecutor(motor_migrate, motor_app, e)
    otorgar(motor_migrate, e.cons, mandato_de(motor_migrate, e, x), acta(motor_migrate, e),
            "documentos.cargar")  # fmt: skip
    niega(motor_migrate, e, x, VERSION, "no_encontrado", documento=doc)
    v = llamar(motor_migrate, e, e.adm, VERSION, documento=doc)  # control: quien vio la cuenta
    with motor_migrate.begin() as c:  # el antivirus la analiza
        c.execute(text("update nucleo.version_documento set estado = 'disponible',"
                       " antivirus = 'ClamAV SINTETICO', firmas_en = now(), analizada_en = now()"
                       " where id = :v"), {"v": v})  # fmt: skip
    p2 = llamar(motor_migrate, e, e.adm, REGISTRAR, **evidencia(motor_migrate, e, o, documento=doc))
    llamar(motor_migrate, e, e.mc2, CONCILIAR, orden=o, pago=p2, resultado="conforme",
           motivo=None)  # fmt: skip
    assert orden(motor_migrate, o)["estado"] == "pagada"


def test_un_resultado_incierto_deja_abierta_la_ejecucion_y_uno_definitivo_la_cierra(
    motor_migrate, e
):
    """P3 176: `incierta` no es un resultado definitivo, así que quien vio la cuenta sigue
    pudiendo subir la evidencia (para verificar, no para reintentar a ciegas); con `fallida` la
    ejecución se cierra y nadie cambia el comprobante. Los resultados se fuerzan con el rol de
    migración, como en `test_0056_confirmable_mira_el_estado_la_version_y_el_resultado`."""
    o = emitida(motor_migrate, e)
    ver_version(motor_migrate, e, e.adm, o, 1)
    doc = llamar(motor_migrate, e, e.adm, CREAR, orden=o, titulo=TITULO)

    def forzar(resultado: str) -> None:
        with motor_migrate.begin() as c:
            c.execute(text("insert into nucleo.resultado_ejecucion_orden_pago (consorcio_id,"
                           " ejecucion_id, resultado, origen) select consorcio_id, id, :r,"
                           " 'manual' from nucleo.ejecucion_orden_pago where orden_id = :o"),
                      {"r": resultado, "o": o})  # fmt: skip

    forzar("incierta")
    assert llamar(motor_migrate, e, e.adm, VERSION, documento=doc) is not None
    forzar("fallida")
    niega(motor_migrate, e, e.adm, VERSION, "no_encontrado", documento=doc)


def test_0065_ida_y_vuelta(base_aparte):  # noqa: F811
    cfg, motor = base_aparte
    m = migracion_0065()

    def definiciones() -> tuple:
        with motor.connect() as c:
            sql = ("select pg_get_functiondef("
                   "'nucleo.abrio_la_cuenta_de_la_orden(uuid, uuid, uuid)'::regprocedure),"
                   " pg_get_functiondef("
                   "'nucleo.exigir_no_ejecuto(uuid, uuid, uuid, uuid)'::regprocedure),"
                   " pg_get_functiondef(cast(:r as regprocedure)),"
                   " to_regprocedure('nucleo.contiene_cbu(text)')")  # fmt: skip
            return tuple(c.execute(text(sql), {"r": m.REGISTRO}).one())

    command.upgrade(cfg, "0064")
    antes = definiciones()
    assert antes[3] is None and "contiene_cbu" not in antes[2]
    command.upgrade(cfg, "0065")
    despues = definiciones()
    assert despues[3] is not None and "contiene_cbu" in despues[2]
    assert despues[0] != antes[0] and despues[1] != antes[1]
    command.downgrade(cfg, "0064")
    assert definiciones() == antes
    command.upgrade(cfg, "0065")  # control
    assert definiciones()[3] is not None


def migracion_0065():
    ruta = Path(__file__).resolve().parents[1] / "migrations" / "versions"
    archivo = ruta / "0065_evidencia_por_ejecucion.py"
    spec = importlib.util.spec_from_file_location("migracion_0065", archivo)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo
