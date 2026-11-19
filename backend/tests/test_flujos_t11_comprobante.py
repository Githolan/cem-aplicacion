"""Tanda 11 (19-11-2026, `0112`): otro comprobante para el envío ya registrado de una solicitud
formal, si el antivirus rechazó el vigente (pregunta 17 del propietario, FL-SOL-04). La fecha y el
vencimiento no cambian. Contra PostgreSQL, el almacén S3 de pruebas y el analizador reales. Datos
SINTETICOS / PRUEBA / NO REAL.
"""

import importlib.util
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import create_engine, text

from app.db import transaccion
from tests.conftest import clave_idempotencia, url_rol
from tests.test_documentos_2c1 import (  # noqa: F401  (los fixtures se importan)
    analizar,
    bucket_con_versiones,
    firmas_de_la_imagen,
    pdf,
)
from tests.test_modo_operacion import hoy_de
from tests.test_orden_pago_neutra import nueva_sesion
from tests.test_rutas_egresos import cliente, codigo, post
from tests.test_solicitud_formal_0108 import (
    emitida,
    enviar,
    recordar_hoy,
    sin_cuenta,
    una,
)
from tests.test_vinculo_0014 import base_aparte  # noqa: F401  (es un fixture)

pytestmark = [pytest.mark.db, pytest.mark.redis]
# ruff: noqa: F811

MIGRACION = Path(__file__).resolve().parent.parent / "migrations/versions/0112_flujos_tanda_11.py"
REEMPLAZAR = "reemplazar_comprobante"
CON_CONTENIDO_ACTIVO = pdf(js="app.alert(1)")
"""El analizador lo rechaza (`contenido_activo`) sin depender de la base de firmas."""


@pytest.fixture
def x(motor_migrate, motor_app):
    return recordar_hoy(motor_app, sin_cuenta(motor_migrate, motor_app))


def analizar_todo(motor_app) -> None:
    """Corre la cola hasta vaciarla: el rechazo encola el borrado de su archivo, que puede ir antes
    que el análisis del comprobante nuevo."""
    while analizar(motor_app):
        pass


def subir(e, quien, s: dict, contenido: bytes):
    with cliente(quien, e.sesion[quien]) as c:
        return c.post(
            f"/api/v1/consorcios/{e.cons}/solicitudes-formales/{s['id']}/adjuntos",
            params={"para": "envio", "titulo": "Comprobante SINTETICO"},
            content=contenido,
            headers={"Content-Type": "application/pdf"} | clave_idempotencia(),
        )


def adjunto(e, quien, s: dict, contenido: bytes) -> str:
    r = subir(e, quien, s, contenido)
    assert r.status_code == 201, r.text
    return r.json()["adjunto_id"]


def reemplazar(e, quien, s: dict, adjunto_id: str):
    return post(e, quien, f"solicitudes-formales/{s['id']}/envio/comprobante",
                {"adjunto_id": adjunto_id})  # fmt: skip


def con_envio_rechazado(e, motor_app) -> dict:
    """Emitida, con el envío registrado y su comprobante rechazado por el analizador."""
    s = emitida(e)
    r = enviar(e, e.mc1, s, adjunto(e, e.mc1, s, CON_CONTENIDO_ACTIVO))
    assert r.status_code == 200, r.text
    assert analizar(motor_app) == 1
    return s


def test_con_el_comprobante_rechazado_se_sube_otro_y_la_fecha_no_cambia(
    x, motor_migrate, motor_app, bucket_con_versiones, firmas_de_la_imagen
):
    s = emitida(x)
    r = enviar(x, x.mc1, s, adjunto(x, x.mc1, s, CON_CONTENIDO_ACTIVO))
    assert r.status_code == 200, r.text
    envio = r.json()["envio"]
    # Control: en cuarentena todavía no se ofrece ni se acepta reemplazarlo.
    assert envio["estado_archivo"] == "en_cuarentena"
    assert REEMPLAZAR not in una(x, x.mc1, s)["acciones"]
    assert codigo(subir(x, x.mc2, s, pdf())) == (409, "solicitud_no_admite")
    assert codigo(reemplazar(x, x.mc1, s, str(uuid4()))) == (409, "solicitud_no_admite")

    assert analizar(motor_app) == 1
    vista = una(x, x.mc1, s)
    assert vista["envio"]["estado_archivo"] == "rechazado"
    assert REEMPLAZAR in vista["acciones"]
    nuevo = adjunto(x, x.mc2, s, pdf())
    # El que subió otra persona no sirve; tampoco uno inventado.
    assert codigo(reemplazar(x, x.mc1, s, nuevo)) == (422, "adjunto_invalido")
    assert codigo(reemplazar(x, x.mc2, s, str(uuid4()))) == (422, "adjunto_invalido")
    r = reemplazar(x, x.mc2, s, nuevo)
    assert r.status_code == 200, r.text
    despues = r.json()
    for campo in ("medio", "detalle", "fecha_envio", "vence_el", "registrado_por_nombre"):
        assert despues["envio"][campo] == envio[campo], campo
    assert despues["envio"]["version_documento_id"] != envio["version_documento_id"]
    assert despues["envio"]["estado_archivo"] == "en_cuarentena"
    assert despues["estado"] == "enviada"
    assert REEMPLAZAR not in despues["acciones"]
    # Con el nuevo vigente y sin rechazar, no se reemplaza otra vez.
    assert codigo(reemplazar(x, x.mc2, s, nuevo)) == (409, "solicitud_no_admite")
    with motor_migrate.connect() as c:
        evento = c.execute(
            text("select despues from auditoria.eventos where consorcio_id = :c"
                 " and accion = 'consorcio.solicitud_formal.reemplazar_comprobante'"),
            {"c": x.cons},
        ).one()  # fmt: skip
    assert evento.despues["adjunto_id"] == nuevo
    # El nuevo también se analiza; limpio, queda disponible.
    analizar_todo(motor_app)
    assert una(x, x.mc1, s)["envio"]["estado_archivo"] == "disponible"


def test_un_rechazado_otra_vez_se_vuelve_a_reemplazar(
    x, motor_migrate, motor_app, bucket_con_versiones, firmas_de_la_imagen
):
    s = con_envio_rechazado(x, motor_app)
    segundo = adjunto(x, x.mc1, s, CON_CONTENIDO_ACTIVO)
    assert reemplazar(x, x.mc1, s, segundo).status_code == 200
    analizar_todo(motor_app)
    assert una(x, x.mc1, s)["envio"]["estado_archivo"] == "rechazado"
    tercero = adjunto(x, x.mc1, s, pdf())
    assert reemplazar(x, x.mc1, s, tercero).status_code == 200
    with motor_migrate.connect() as c:
        cadena = c.execute(
            text("select count(*) from nucleo.solicitud_formal_envio_comprobante"
                 " where consorcio_id = :c"),
            {"c": x.cons},
        ).scalar_one()  # fmt: skip
    assert cadena == 2


def test_quien_no_registra_envios_no_reemplaza(
    x, motor_migrate, motor_app, bucket_con_versiones, firmas_de_la_imagen
):
    s = con_envio_rechazado(x, motor_app)
    x.sesion[x.prop] = nueva_sesion(motor_migrate, x.prop)
    assert subir(x, x.prop, s, pdf()).status_code in {403, 404}
    assert codigo(reemplazar(x, x.prop, s, str(uuid4()))) in {(403, "sin_permiso"),
                                                             (404, "no_encontrado")}  # fmt: skip
    # Control: quien sí registra envíos, sí.
    assert reemplazar(x, x.mc1, s, adjunto(x, x.mc1, s, pdf())).status_code == 200


def test_la_exportacion_incluye_los_reemplazos(
    x, motor_migrate, motor_app, bucket_con_versiones, firmas_de_la_imagen
):
    s = con_envio_rechazado(x, motor_app)
    assert reemplazar(x, x.mc2, s, adjunto(x, x.mc2, s, pdf())).status_code == 200
    hoy = hoy_de(motor_migrate, x.cons)
    motor = create_engine(url_rol("soporte_exportacion"))
    try:
        with transaccion(motor, persona_id=None, consorcio_id=x.cons) as c:
            pedido = c.execute(
                text("select id from nucleo.registrar_exportacion(:c, 'Nota 1', 'Estudio',"
                     " '30712345671', :p, :h, 'Acta 1')"),
                {"c": x.cons, "p": x.mc1, "h": hoy},
            ).scalar_one()  # fmt: skip
            filas = c.execute(
                text("select entidad, fila from nucleo.exportar_mandatos(:c, :e)"),
                {"c": x.cons, "e": pedido},
            ).all()
    finally:
        motor.dispose()
    entidades = [f.entidad for f in filas]
    [fila] = [f.fila for f in filas if f.entidad == "solicitud_formal_envio_comprobante"]
    # En el orden que espera el script de exportación: después del envío, antes de la respuesta.
    assert entidades.index("solicitud_formal_envio_comprobante") > entidades.index(
        "solicitud_formal_envio"
    )
    assert fila["reemplazado_por_nombre"] and str(x.mc2) not in str(filas)


def _migracion():
    spec = importlib.util.spec_from_file_location("migracion_0112", MIGRACION)
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def test_0112_ida_y_vuelta_deja_todo_como_estaba(base_aparte):
    cfg, motor = base_aparte
    m = _migracion()

    def definiciones() -> dict[str, str]:
        with motor.connect() as c:
            return {
                f: c.execute(text("select pg_get_functiondef(to_regprocedure(:f))"),
                             {"f": f}).scalar_one()
                for f, _ in m.EN_SU_LUGAR
            }  # fmt: skip

    def tabla() -> object:
        with motor.connect() as c:
            return c.execute(text("select to_regclass(:t)"), {"t": m.TABLA}).scalar()

    command.upgrade(cfg, "0111")
    antes = definiciones()
    command.upgrade(cfg, "0112")
    con = definiciones()
    assert all(con[f] != antes[f] for f in antes)  # control: cambió las cinco
    assert tabla() is not None
    command.downgrade(cfg, "0111")
    assert definiciones() == antes
    assert tabla() is None
    command.upgrade(cfg, "0112")
    assert definiciones() == con
