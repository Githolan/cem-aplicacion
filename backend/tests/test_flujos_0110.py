"""Datos y reglas de la revisión de fricción por flujos (`0110`; 18-11-2026, tanda 8), con las
respuestas del propietario del mismo día.

Cada regla nueva se prueba con su control: lo que antes se negaba ahora pasa, y lo que tiene que
seguir negándose se sigue negando.

Contra PostgreSQL real. Datos SINTETICOS / PRUEBA / NO REAL.
"""

from datetime import date, timedelta

import pytest
from alembic import command
from sqlalchemy import text

from tests.test_contactos_emergencia import EMPRESA, accion, crear, eventos, leer
from tests.test_contactos_emergencia import escena as escena  # noqa: F401  (fixture)
from tests.test_mandatos import otra_persona
from tests.test_vinculo_0014 import base_aparte  # noqa: F401  (es un fixture)

pytestmark = [pytest.mark.db]
# ruff: noqa: F811

CAMBIADAS = (
    "nucleo.senalar_contacto(uuid, text, uuid, text)",
    "nucleo.registrar_respuesta_solicitud_formal(uuid, text, date, uuid, uuid, text, uuid)",
    "nucleo.solicitudes_formales_lectura(uuid, text, uuid)",
    "nucleo.mis_invitaciones()",
)
RUBRO_ANTES = "nucleo.asignar_rubro_legal_proveedor(uuid, text, boolean, uuid, text, uuid)"
NUEVAS = (
    "nucleo.asignar_rubro_legal_proveedor(uuid, text, boolean, text, uuid, text, uuid)",
    "nucleo.pedir_salteo_documentacion(uuid, text, bigint, text, uuid, text, uuid)",
    "nucleo.pedidos_salteo_lectura(text, uuid, text, uuid)",
    "nucleo.retiros_rubro_con_motivo_lectura(uuid, text, uuid, text, uuid)",
    "nucleo.razon_social_por_aclarar(uuid, text, uuid)",
    "nucleo.ordenes_sin_cuenta_lectura(text, uuid, text, uuid)",
    "nucleo.candidatos_a_mandato_datos()",
    "nucleo.vinculos_que_sostienen_consejo()",
    "nucleo.vinculos_de_invitaciones()",
    "nucleo.invitacion_aceptada_por(bytea, uuid)",
    "nucleo.autoriza_por_persona_lectura(text, uuid, text, uuid)",
    "nucleo.ven_proveedores_por_rol_lectura(uuid, text, uuid)",
    "nucleo.adjuntar_acta_decision(uuid, text, uuid, text)",
    "nucleo.grupos_del_periodo_lectura(date, uuid, text, uuid)",
)
FUNCIONES = (*CAMBIADAS, RUBRO_ANTES, *NUEVAS)
TABLAS = ("nucleo.pedido_salteo_documentacion", "nucleo.rubro_legal_retiro_motivo")


def _definiciones(motor) -> dict:
    with motor.connect() as c:
        return {
            f: c.execute(
                text("select pg_get_functiondef(p.oid), coalesce(p.proacl::text, ''), p.prosecdef"
                     " from pg_proc p where p.oid = to_regprocedure(:f)"), {"f": f},
            ).one_or_none()
            for f in FUNCIONES
        }  # fmt: skip


def _estado(motor) -> tuple:
    with motor.connect() as c:
        tablas = [c.execute(text("select to_regclass(:t)"), {"t": t}).scalar() for t in TABLAS]
        paquete = c.execute(text(
            "select count(*) from nucleo.paquete_facultad"
            " where tipo = 'miembro_consejo' and facultad = 'padron.confirmar_vinculo'"
        )).scalar_one()  # fmt: skip
        proyeccion = c.execute(text(
            "select count(*) from auditoria.proyeccion"
            " where accion = 'consorcio.decision.adjuntar_acta'"
        )).scalar_one()  # fmt: skip
    return _definiciones(motor), tablas, (paquete, proyeccion)


def test_0110_ida_y_vuelta_deja_todo_como_estaba(base_aparte):
    cfg, motor = base_aparte
    command.upgrade(cfg, "0109")
    antes = _estado(motor)
    command.upgrade(cfg, "0110")
    con, tablas, paquete = _estado(motor)
    # Control: la 0110 cambió cada función, con el mismo permiso y `security definer`.
    assert all(antes[0][f] is not None and con[f][0] != antes[0][f][0] for f in CAMBIADAS)
    assert all(con[f][1:] == antes[0][f][1:] for f in CAMBIADAS)
    assert con[RUBRO_ANTES] is None and antes[0][RUBRO_ANTES] is not None
    assert all(antes[0][f] is None and "cem_app=X" in con[f][1] and con[f][2] for f in NUEVAS)
    assert all(t is not None for t in tablas) and paquete == (1, 1)
    assert antes[1] == [None, None] and antes[2] == (0, 0)
    command.downgrade(cfg, "0109")
    assert _estado(motor) == antes


# Pregunta 16 (FL-CTO-01): señalar un contacto con `contactos.ver` ------------------------------


@pytest.mark.redis
def test_quien_ve_los_contactos_senala_un_dato_mal(escena, motor_migrate):
    e = escena
    k = crear(e["mc"], e["cons"]).json()["id"]
    r = accion(e["prop"], e["cons"], k, "senalar", {"motivo": "El número no atiende"})
    assert r.status_code == 200, r.text
    assert leer(e["prop"], e["cons"]).json()["elementos"][0]["observado"] is True
    [g] = leer(e["mc"], e["cons"], "/gestion").json()["elementos"]
    assert (g["observacion"], g["observado_por"]) == ("El número no atiende", str(e["prop"]))
    [f] = [f for f in eventos(motor_migrate, e["cons"]) if f.accion.endswith(".senalar")]
    assert f.despues["funcion"] == EMPRESA["funcion"]
    # Sigue sin teléfonos ni correos en el motivo.
    r = accion(e["prop"], e["cons"], k, "senalar", {"motivo": "Llamar al 11 4000 0000"})
    assert r.status_code == 422
    # Lo demás sigue siendo de quien gestiona.
    assert accion(e["prop"], e["cons"], k, "verificar").status_code == 403
    assert accion(e["prop"], e["cons"], k, "retirar").status_code == 403
    # Control: quien gestiona verifica y deja de figurar observado.
    assert accion(e["mc"], e["cons"], k, "verificar").status_code == 200
    assert leer(e["prop"], e["cons"]).json()["elementos"][0]["observado"] is False


@pytest.mark.redis
def test_un_contacto_futuro_lo_senala_solo_quien_gestiona(escena, motor_migrate):
    e = escena
    futuro = crear(e["mc"], e["cons"], vigente_desde=str(date.today() + timedelta(days=30)))
    k = futuro.json()["id"]
    r = accion(e["prop"], e["cons"], k, "senalar", {"motivo": "Todavía no rige"})
    assert (r.status_code, r.json()["codigo"]) == (404, "no_encontrado")
    # Sin ámbito en el edificio, ni siquiera llega a la base.
    ajena = otra_persona(motor_migrate, e["cons"])
    assert accion(ajena, e["cons"], k, "senalar", {"motivo": "Algo"}).status_code == 404
    # Control: el Consejo, que gestiona, sí.
    assert accion(e["mc"], e["cons"], k, "senalar", {"motivo": "Revisar"}).status_code == 200
