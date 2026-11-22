"""Bandeja y correo del Consejo (`0126`; RF40, diseño P0 S6) y el aviso de vencimiento de las
solicitudes formales (RF49, §5 de su diseño). Contra servicios reales: PostgreSQL, el almacén S3 de
pruebas y ClamAV. Datos SINTETICOS / PRUEBA / NO REAL: ningún correo sale (se lee lo que queda en el
outbox).

- Lo del Consejo va en el aviso diario que ya existe (`avisos_pagos`), con el mismo tope: cuántas
  liquidaciones esperan confirmación, solicitudes por vencer o recién vencidas, reclamos que esperan
  respuesta y pedidos de reparación sin responsable. A quien lo puede resolver, nunca el texto de un
  reclamo, una unidad, una persona, un importe ni una cuenta, y con el enlace al inicio.
- `por_vencer` de la API y el renglón del correo salen de la misma regla (`avisos_consejo`).

Cada negación lleva su control. Contra `cf9e70a` ninguna de estas pruebas corre: no existen
`avisos_consejo` ni `nucleo.pendientes_consejo_para_avisar`.
"""

import importlib.util
import json
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import pytest
from alembic import command
from cem_contracts import fiscalizacion as contrato
from cem_contracts import politicas
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.consorcios import avisos_consejo, avisos_pagos
from app.db import transaccion
from tests.test_avisos_y_sin_explicar_0098 import (
    HOY,
    ORIGEN,
    avisar,
    avisos_a,
    datos_de_dinero,
    pasa_un_dia,
)
from tests.test_liquidacion_oficial_0119 import (  # noqa: F401  (los fixtures se importan)
    bucket,
    firmas_de_la_imagen,
    importar,
)
from tests.test_orden_pago_neutra import Escena, escena_economica
from tests.test_reclamos_liquidacion_0123 import TEXTO as TEXTO_RECLAMO
from tests.test_reclamos_liquidacion_0123 import armar, presentado
from tests.test_reparaciones_0124 import DESCRIPCION as TEXTO_REPARACION
from tests.test_reparaciones_0124 import pedida
from tests.test_solicitud_formal_0108 import correr_el_envio, emitida, una
from tests.test_vinculo_0014 import base_aparte  # noqa: F401  (es un fixture)

pytestmark = [pytest.mark.db, pytest.mark.redis]
# ruff: noqa: F811

__all__ = ["base_aparte", "bucket", "firmas_de_la_imagen"]

MIGRACION = Path(__file__).resolve().parent.parent / "migrations/versions/0126_bandeja_consejo.py"
LECTURA = "select * from nucleo.pendientes_consejo_para_avisar()"
DIAS = contrato.DIAS_AVISO_VENCIMIENTO
PLAZO = contrato.PLAZO_POR_DEFECTO_DIAS
POR_VENCER = f"una solicitud formal vence en {DIAS} días o menos"
VENCIDA = "una solicitud formal venció sin respuesta"
IMPORTACION = "una liquidación del administrador espera confirmación"
RECLAMO = "un reclamo espera respuesta"
REPARACION = "un pedido de reparación espera responsable"


def _migracion():
    spec = importlib.util.spec_from_file_location("migracion_0126", MIGRACION)
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def textos(motor_migrate, persona: UUID) -> list[str]:
    """Los avisos diarios pedidos para esa persona (no los correos de cada acto)."""
    return [a["carga"]["texto"] for a in avisos_a(motor_migrate, persona)
            if a["carga"]["asunto"] == avisos_pagos.ASUNTO]  # fmt: skip


def renglones(texto: str) -> list[str]:
    return [r for r in texto.splitlines() if r.startswith("- ")]


def nombre(motor_migrate, cons) -> str:
    with motor_migrate.connect() as c:
        return c.execute(
            text("select nombre from nucleo.consorcio where id = :c"), {"c": cons}
        ).scalar_one()


# --- Una sola fuente ------------------------------------------------------------------------------


def test_la_migracion_copia_los_modos_y_las_facultades_de_las_politicas():
    leidas = {p["accion"]: p for archivo in politicas.archivos()
              for p in json.loads(archivo.read_text(encoding="utf-8"))["politicas"]}  # fmt: skip
    migracion = _migracion()
    assert set(migracion.MODOS) == set(migracion.FACULTADES)
    for accion, modos in migracion.MODOS.items():
        assert tuple(leidas[accion]["modos"]) == modos, accion
        assert leidas[accion]["facultad"] == migracion.FACULTADES[accion], accion
    assert migracion.SOLICITUDES_QUE_CUENTAN == (
        contrato.EstadoSolicitudFormal.ENVIADA, contrato.EstadoSolicitudFormal.VENCIDA,
    )  # fmt: skip


def test_la_regla_de_por_vencer_y_de_vencida_hace_poco_en_sus_bordes():
    hoy = HOY.date()
    enviada = contrato.EstadoSolicitudFormal.ENVIADA
    vencida = contrato.EstadoSolicitudFormal.VENCIDA
    assert avisos_consejo.por_vencer(enviada, hoy + timedelta(days=DIAS), hoy)
    assert avisos_consejo.por_vencer(enviada, hoy, hoy)  # vence hoy: sigue enviada
    assert not avisos_consejo.por_vencer(enviada, hoy + timedelta(days=DIAS + 1), hoy)
    assert not avisos_consejo.por_vencer(vencida, hoy - timedelta(days=1), hoy)
    assert not avisos_consejo.por_vencer(enviada, None, hoy)
    assert avisos_consejo.vencio_hace_poco(vencida, hoy - timedelta(days=DIAS), hoy)
    assert not avisos_consejo.vencio_hace_poco(vencida, hoy - timedelta(days=DIAS + 1), hoy)
    assert not avisos_consejo.vencio_hace_poco(enviada, hoy - timedelta(days=1), hoy)


# --- Solicitudes formales (Administrador Aumentado: el administrador tiene cuenta) ---------------


@pytest.fixture
def e(motor_migrate, motor_app) -> Escena:
    return escena_economica(motor_migrate, motor_app)


def test_por_vencer_en_la_api_y_en_el_correo_con_la_misma_regla(motor_migrate, motor_app, e):
    s = emitida(e)
    assert una(e, e.mc1, s)["estado"] == "enviada"
    assert una(e, e.mc1, s)["por_vencer"] is False
    avisar(motor_app)
    assert textos(motor_migrate, e.mc1) == []
    # Control del borde: vence en DIAS + 1 días, todavía no.
    correr_el_envio(motor_migrate, s, PLAZO - DIAS - 1)
    assert una(e, e.mc1, s)["por_vencer"] is False
    avisar(motor_app, HOY + timedelta(days=1))
    assert textos(motor_migrate, e.mc1) == []
    # Un día después: vence en DIAS días.
    correr_el_envio(motor_migrate, s, 1)
    for quien in (e.mc1, e.mc2, e.adm):
        assert una(e, quien, s)["por_vencer"] is True, quien
    avisar(motor_app, HOY + timedelta(days=2))
    edificio = nombre(motor_migrate, e.cons)
    for quien in (e.mc1, e.mc2):
        [texto] = textos(motor_migrate, quien)
        assert renglones(texto) == [f"- {edificio}: {POR_VENCER}."], quien
        assert f"{ORIGEN}/\n" in texto
        assert f"{ORIGEN}/pagos" not in texto
    # La administración es la destinataria: no firma, no sigue la solicitud. La ve en el inicio
    # como «espera respuesta».
    assert textos(motor_migrate, e.adm) == []


def test_la_lista_dice_desde_cuantos_dias_esta_por_vencer(e):
    from tests.test_solicitud_formal_0108 import lista

    assert lista(e, e.mc1)["dias_aviso_vencimiento"] == DIAS


def test_la_vencida_se_avisa_unos_dias_y_despues_ya_no(motor_migrate, motor_app, e):
    s = emitida(e)
    correr_el_envio(motor_migrate, s, PLAZO + 1)
    assert una(e, e.mc1, s)["estado"] == "vencida"
    assert una(e, e.mc1, s)["por_vencer"] is False
    avisar(motor_app)
    [texto] = textos(motor_migrate, e.mc1)
    assert renglones(texto) == [f"- {nombre(motor_migrate, e.cons)}: {VENCIDA}."]
    # DIAS días después del vencimiento todavía sale; uno más, ya no (sigue en el inicio).
    pasa_un_dia(motor_migrate, e.mc1)
    correr_el_envio(motor_migrate, s, DIAS - 1)
    avisar(motor_app, HOY + timedelta(days=1))
    assert len(textos(motor_migrate, e.mc1)) == 2
    pasa_un_dia(motor_migrate, e.mc1)
    correr_el_envio(motor_migrate, s, 1)
    avisar(motor_app, HOY + timedelta(days=2))
    assert len(textos(motor_migrate, e.mc1)) == 2
    assert una(e, e.mc1, s)["estado"] == "vencida"


def test_respondida_ya_no_se_avisa(motor_migrate, motor_app, e):
    from tests.test_rutas_egresos import post

    s = emitida(e)
    correr_el_envio(motor_migrate, s, PLAZO - DIAS)
    r = post(e, e.adm, f"solicitudes-formales/{s['id']}/respuesta",
             {"texto": "Se adjunta lo pedido. SINTETICA"})  # fmt: skip
    assert r.status_code == 200, r.text
    assert r.json()["por_vencer"] is False
    avisar(motor_app)
    assert textos(motor_migrate, e.mc1) == []
    # Control: otra enviada que vence pronto, sí.
    otra = emitida(e)
    correr_el_envio(motor_migrate, otra, PLAZO - DIAS)
    avisar(motor_app, HOY + timedelta(days=1))
    [texto] = textos(motor_migrate, e.mc1)
    assert POR_VENCER in texto


def test_solo_el_trabajo_lee_los_pendientes_del_consejo(motor_app, e):
    for persona, consorcio in ((e.mc1, e.cons), (None, e.cons), (e.mc1, None)):
        with pytest.raises(DBAPIError) as error:
            with transaccion(motor_app, persona_id=persona, consorcio_id=consorcio) as c:
                c.execute(text(LECTURA))
        assert error.value.orig.diag.sqlstate == "42501", (persona, consorcio)
    # Control: sin persona ni consorcio, la lectura funciona.
    with transaccion(motor_app, persona_id=None) as c:
        filas = c.execute(text(LECTURA)).mappings().all()
    mias = {f["persona_id"]: f for f in filas if f["consorcio_id"] == e.cons}
    assert mias[e.mc1]["solicitudes"] == []
    assert e.adm not in mias or mias[e.adm]["solicitudes"] is None


# --- Modo Consejo: importaciones, reclamos y reparaciones ----------------------------------------


@pytest.fixture
def k(motor_migrate, motor_app, bucket) -> dict:
    return armar(motor_migrate, motor_app)


def test_un_solo_correo_con_todo_lo_del_consejo_sin_textos_unidades_ni_dinero(
    motor_migrate, motor_app, k
):
    importar(k, motor_app)
    presentado(k)
    pedida(k)
    avisar(motor_app)
    edificio = nombre(motor_migrate, k["cons"])
    [texto] = textos(motor_migrate, k["mc"])
    prohibido = [TEXTO_RECLAMO, TEXTO_RECLAMO.splitlines()[0], TEXTO_REPARACION, *k["u"],
                 str(k["liq"])]  # fmt: skip
    assert datos_de_dinero(texto, prohibido) == []
    assert renglones(texto) == [f"- {edificio}: {IMPORTACION}, {RECLAMO} y {REPARACION}."]
    assert f"{ORIGEN}/\n" in texto
    # Control del detector: el texto del reclamo y una unidad sí aparecerían si estuvieran.
    assert datos_de_dinero(f"{TEXTO_RECLAMO} 1-A $ 12.345,00", prohibido) != []
    # La propietaria, que reclamó y pidió, no atiende ni gestiona: no recibe el aviso.
    assert textos(motor_migrate, k["prop"]) == []
    # Otra corrida el mismo día no suma un correo (el tope de siempre).
    avisar(motor_app, HOY + timedelta(hours=3))
    assert len(textos(motor_migrate, k["mc"])) == 1


def test_cada_cosa_a_quien_la_resuelve(motor_migrate, motor_app, k):
    presentado(k)
    pedida(k)
    avisar(motor_app)
    edificio = nombre(motor_migrate, k["cons"])
    # La administración atiende reclamos y gestiona reparaciones (`0123`, `0124`).
    [texto] = textos(motor_migrate, k["ad"])
    assert renglones(texto) == [f"- {edificio}: {RECLAMO} y {REPARACION}."]
    for quien in ("prop", "inq", "oc", "sin_confirmar"):
        assert textos(motor_migrate, k[quien]) == [], quien


def test_lo_atendido_sale_del_correo(motor_migrate, motor_app, k):
    from tests.test_reclamos_liquidacion_0123 import actuar
    from tests.test_reparaciones_0124 import paso

    reclamo = presentado(k)
    rep = pedida(k)
    r = actuar(k, "mc", reclamo, "respuesta", {"texto": "Respondido. SINTETICA"})
    assert r.status_code == 200, r.text
    assert paso(k, rep, "en_gestion").status_code == 200
    avisar(motor_app)
    assert textos(motor_migrate, k["mc"]) == []
    # Control: un reclamo nuevo vuelve a salir.
    presentado(k)
    avisar(motor_app, HOY + timedelta(days=1))
    [texto] = textos(motor_migrate, k["mc"])
    assert renglones(texto) == [f"- {nombre(motor_migrate, k['cons'])}: {RECLAMO}."]


def test_otro_consorcio_no_suma(motor_migrate, motor_app, k):
    otro = armar(motor_migrate, motor_app)
    presentado(otro)
    presentado(otro, texto="Otro reclamo sobre el mismo saldo. Datos de PRUEBA SINTETICA.")
    avisar(motor_app)
    assert textos(motor_migrate, k["mc"]) == []
    [texto] = textos(motor_migrate, otro["mc"])
    assert "2 reclamos esperan respuesta" in texto
    # Control: uno en el edificio propio cuenta uno solo.
    presentado(k)
    avisar(motor_app, HOY + timedelta(days=1))
    [texto] = textos(motor_migrate, k["mc"])
    assert renglones(texto) == [f"- {nombre(motor_migrate, k['cons'])}: {RECLAMO}."]


# --- La migración ---------------------------------------------------------------------------------


def test_la_migracion_0126_baja_y_vuelve_a_subir(base_aparte):
    cfg, motor = base_aparte

    def existe() -> bool:
        with motor.connect() as c:
            return c.execute(text(
                "select to_regprocedure('nucleo.pendientes_consejo_para_avisar()') is not null"
            )).scalar_one()  # fmt: skip

    command.upgrade(cfg, "0126")
    assert existe()
    command.downgrade(cfg, "0125")
    assert not existe()
    command.upgrade(cfg, "0126")
    assert existe()
