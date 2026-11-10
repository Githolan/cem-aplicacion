"""Panel del Consejo (`0094`, H4a corte 1; RF48) contra PostgreSQL real.

Diseño: `doc/diseno/H4a_1_Panel_del_Consejo.md` del repositorio CEM. La escena es la de egresos
(`escena_economica`, Modo Administrador Aumentado): `mc1` y `mc2` son del Consejo, `adm` es la
administración y `prop` es la propietaria, sin mandato.
"""

from datetime import date
from uuid import UUID

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db import transaccion
from tests.test_condicion_habilitante_consejo import unidad, vinculo
from tests.test_documentos_2c2 import denegadas
from tests.test_egresos_1a import aprobada, documento_de_contrato, enviada
from tests.test_liquidacion_0057 import pagada
from tests.test_liquidacion_0058 import fechar_pago, hoy, mes
from tests.test_mandatos import otra_persona
from tests.test_modo_operacion import consorcio_con_persona
from tests.test_orden_pago_neutra import (
    APROBAR,
    ENVIAR,
    ESENCIALES,
    cbu_sintetico,
    cuit_sintetico,
    escena_economica,
    huella,
    llamar,
    orden,
)
from tests.test_pago_manual_0055 import CONCILIAR, emitida, registrada
from tests.test_pago_manual_0055 import e as e  # noqa: F401  (es un fixture)
from tests.test_rutas_egresos import cliente, get
from tests.test_vinculo_0014 import base_aparte  # noqa: F401  (es un fixture)

pytestmark = pytest.mark.db

ACCION = "fiscalizacion.ver_panel"
LECTURAS = ("panel_ordenes_por_estado", "panel_gasto_pagado", "panel_comprobantes_faltantes")
PREPARAR_CON_FACTURA = (
    "select nucleo.preparar_orden_pago(:importe, :moneda, :concepto, :proveedor, :comprobante,"
    " :reserva, :contrato, :actor, :origen, :sesion, :factura)"
)
IMPORTE = ESENCIALES["importe"]


def panel(e, quien: UUID):
    return get(e, quien, "panel")


def panel_sin_sesion(cons: UUID, quien: UUID):
    """Para quien no tiene sesión en la escena (inquilino, otro consorcio)."""
    with cliente(quien, None) as c:
        return c.get(url_de(cons))


def url_de(cons: UUID) -> str:
    return f"/api/v1/consorcios/{cons}/panel"


def proveedor_con_rubro(motor_migrate, e, rubro: str, cuenta: str) -> UUID:
    """Un proveedor con su versión 1 y un rubro, como `nuevo_proveedor`."""
    with motor_migrate.begin() as c:
        p = c.execute(
            text("insert into nucleo.proveedor (consorcio_id, cuit, alta_por, identidad_alta)"
                 " values (:c, :cuit, :q, nucleo.identidad_humana_de(:c, :q)) returning id"),
            {"c": e.cons, "q": e.adm, "cuit": cuit_sintetico()},
        ).scalar_one()  # fmt: skip
        c.execute(
            text("insert into nucleo.proveedor_version (consorcio_id, proveedor_id, numero,"
                 " razon_social, rubro, estado, cuenta_destino, huella, preparada_por,"
                 " identidad_preparo) values (:c, :p, 1, 'Limpieza SINTETICA (NO REAL)', :rubro,"
                 " 'activo', :cta, nucleo.huella_proveedor(:p, 1, null,"
                 " 'Limpieza SINTETICA (NO REAL)', :cta), :q, nucleo.identidad_humana_de(:c, :q))"),
            {"c": e.cons, "p": p, "rubro": rubro, "cta": cuenta, "q": e.adm},
        )  # fmt: skip
    return p


def pagada_con(motor_migrate, e, cuenta: str, **extra) -> UUID:
    """Como `pagada`, con otro proveedor o con el fondo de reserva."""
    o = emitida(motor_migrate, e, **extra)
    p = registrada(motor_migrate, e, o, cuenta=cuenta)
    llamar(motor_migrate, e, e.mc2, CONCILIAR, orden=o, pago=p, resultado="conforme", motivo=None)
    assert orden(motor_migrate, o)["estado"] == "pagada"
    return o


def aprobada_con_factura(motor_migrate, e) -> UUID:
    doc = documento_de_contrato(motor_migrate, e, categoria="facturas_y_comprobantes")
    with motor_migrate.connect() as c:
        factura = c.execute(
            text("select id from nucleo.version_documento where documento_id = :d"), {"d": doc}
        ).scalar_one()
    o = llamar(motor_migrate, e, e.adm, PREPARAR_CON_FACTURA, factura=factura)
    llamar(motor_migrate, e, e.adm, ENVIAR, orden=o, version=1)
    llamar(motor_migrate, e, e.mc1, APROBAR, orden=o, version=1, huella=huella(motor_migrate, o, 1))
    assert orden(motor_migrate, o)["estado"] == "aprobada"
    return o


# Quién lo ve -----------------------------------------------------------------------------------


def test_consejo_y_administracion_ven_el_panel(e):
    for quien in (e.mc1, e.adm):
        r = panel(e, quien)
        assert r.status_code == 200, r.text
        assert set(r.json()) == {"ordenes_por_estado", "gasto_pagado", "comprobantes_faltantes"}


def test_la_propietaria_y_el_inquilino_no_y_queda_el_motivo(motor_migrate, e):
    inquilino = otra_persona(motor_migrate, e.cons)
    vinculo(motor_migrate, e.cons, unidad(motor_migrate, e.cons, "9° P"), inquilino,
            tipo="inquilino")  # fmt: skip
    assert panel_sin_sesion(e.cons, e.prop).status_code == 403
    assert panel_sin_sesion(e.cons, inquilino).status_code == 403
    assert denegadas(motor_migrate, e.cons, ACCION) == ["sin_concesion"] * 2
    assert panel(e, e.mc1).status_code == 200  # control: la misma escena, con la facultad


def test_otro_consorcio_es_404_y_no_se_audita(motor_migrate, e):
    ajena = otra_persona(motor_migrate, consorcio_con_persona(motor_migrate)[0])
    r = panel_sin_sesion(e.cons, ajena)
    assert r.status_code == 404 and r.json()["codigo"] == "no_encontrado"
    assert denegadas(motor_migrate, e.cons, ACCION) == []


def test_sin_modo_vigente_tampoco(motor_migrate, motor_app):
    sin_modo = escena_economica(motor_migrate, motor_app, con_modo=False)
    assert panel(sin_modo, sin_modo.mc1).status_code == 403
    assert denegadas(motor_migrate, sin_modo.cons, ACCION) == ["sin_modo_registrado"]


def test_la_base_repite_la_barrera(motor_app, e):
    """Segunda barrera: aunque la API concediera, la base niega a quien no tiene la facultad."""
    for funcion in LECTURAS:
        with pytest.raises(DBAPIError) as error:
            with transaccion(motor_app, persona_id=e.prop, consorcio_id=e.cons) as c:
                c.execute(text(f"select * from nucleo.{funcion}()")).all()
        assert error.value.orig.diag.sqlstate == "42501", funcion
        with transaccion(motor_app, persona_id=e.mc1, consorcio_id=e.cons) as c:
            c.execute(text(f"select * from nucleo.{funcion}()")).all()  # control


def test_cem_app_no_llama_a_la_barrera_interna(motor_app, e):
    with pytest.raises(DBAPIError) as error:
        with transaccion(motor_app, persona_id=e.mc1, consorcio_id=e.cons) as c:
            c.execute(text("select nucleo.exigir_ver_panel(:c, :p)"), {"c": e.cons, "p": e.mc1})
    assert error.value.orig.diag.sqlstate == "42501"


# Qué muestra -----------------------------------------------------------------------------------


def test_cifras_de_una_escena_con_todos_los_casos(motor_migrate, e):
    d = hoy(motor_migrate, e)
    este_mes, mes_pasado = date.fromisoformat(mes(d, 0)), date.fromisoformat(mes(d, -1))
    limpieza = proveedor_con_rubro(motor_migrate, e, "  Limpieza ", cbu_sintetico(3))

    sin_rubro = pagada(motor_migrate, e)  # el proveedor de la escena no tiene rubro
    con_rubro = pagada_con(motor_migrate, e, cbu_sintetico(3), proveedor=limpieza)
    del_fondo = pagada_con(motor_migrate, e, cbu_sintetico(3), proveedor=limpieza, reserva=True)
    fechar_pago(motor_migrate, del_fondo, operacion=mes_pasado)
    sin_factura = aprobada(motor_migrate, e)
    con_factura = aprobada_con_factura(motor_migrate, e)
    en_revision = enviada(motor_migrate, e)
    llamar(motor_migrate, e, e.adm, "select nucleo.preparar_orden_pago(:importe, :moneda,"
           " :concepto, :proveedor, :comprobante, :reserva, :contrato, :actor, :origen,"
           " :sesion)")  # un borrador, que no cuenta  # fmt: skip

    r = panel(e, e.mc1)
    assert r.status_code == 200, r.text
    datos = r.json()
    assert datos["ordenes_por_estado"] == [
        {"estado": "en_revision", "moneda": "ARS", "cantidad": 1, "importe_centavos": IMPORTE},
        {"estado": "aprobada", "moneda": "ARS", "cantidad": 2, "importe_centavos": 2 * IMPORTE},
        {"estado": "pagada", "moneda": "ARS", "cantidad": 3, "importe_centavos": 3 * IMPORTE},
    ]
    assert datos["gasto_pagado"] == [
        {"periodo": mes_pasado.isoformat(), "rubro": "Limpieza", "fondo_reserva": True,
         "moneda": "ARS", "cantidad": 1, "importe_centavos": IMPORTE},
        {"periodo": este_mes.isoformat(), "rubro": "Limpieza", "fondo_reserva": False,
         "moneda": "ARS", "cantidad": 1, "importe_centavos": IMPORTE},
        {"periodo": este_mes.isoformat(), "rubro": None, "fondo_reserva": False,
         "moneda": "ARS", "cantidad": 1, "importe_centavos": IMPORTE},
    ]  # fmt: skip
    faltantes = {f["orden_id"]: f["estado"] for f in datos["comprobantes_faltantes"]}
    assert faltantes == {
        str(sin_rubro): "pagada", str(con_rubro): "pagada", str(del_fondo): "pagada",
        str(sin_factura): "aprobada",
    }  # fmt: skip
    # Control: la que tiene factura y la que no se aprobó existen y no están.
    assert str(con_factura) not in faltantes and str(en_revision) not in faltantes
    assert orden(motor_migrate, con_factura)["estado"] == "aprobada"
    # La administración ve lo mismo.
    assert panel(e, e.adm).json() == datos


def test_una_pagada_sin_fecha_todavia_no_suma_gasto(motor_migrate, e):
    """Pagada a mano sin conciliar no está pagada: queda en ejecución o verificación."""
    o = emitida(motor_migrate, e)
    registrada(motor_migrate, e, o)
    datos = panel(e, e.mc1).json()
    assert datos["gasto_pagado"] == []
    assert [f["orden_id"] for f in datos["comprobantes_faltantes"]] == [str(o)]
    assert orden(motor_migrate, o)["estado"] != "pagada"  # control del supuesto


def test_lo_de_otro_consorcio_no_aparece(motor_migrate, motor_app, e):
    otra = escena_economica(motor_migrate, motor_app)
    ajena = pagada(motor_migrate, otra)
    propia = aprobada(motor_migrate, e)
    datos = panel(e, e.mc1).json()
    assert [f["orden_id"] for f in datos["comprobantes_faltantes"]] == [str(propia)]
    assert datos["gasto_pagado"] == []
    # Control: en su consorcio, la ajena sí está.
    assert [f["orden_id"] for f in panel(otra, otra.mc1).json()["comprobantes_faltantes"]] == [
        str(ajena)
    ]


def test_el_panel_no_trae_cuentas_cuit_ni_personas(motor_migrate, e):
    pagada(motor_migrate, e)
    texto = panel(e, e.mc1).text
    assert cbu_sintetico(1) not in texto
    for persona in (e.adm, e.mc1, e.mc2):
        assert str(persona) not in texto
    with motor_migrate.connect() as c:
        cuit = c.execute(text("select cuit from nucleo.proveedor where id = :p"),
                         {"p": e.proveedor}).scalar_one()  # fmt: skip
    assert cuit not in texto
    assert "Proveedor SINTETICO" in texto  # control: la razón social sí


# Migración -------------------------------------------------------------------------------------


def test_0094_ida_y_vuelta(base_aparte):  # noqa: F811
    cfg, motor = base_aparte

    def funciones() -> list[tuple[str, str]]:
        with motor.connect() as c:
            return c.execute(text(
                "select p.proname, coalesce(p.proacl::text, '') from pg_proc p"
                " join pg_namespace n on n.oid = p.pronamespace"
                " where n.nspname = 'nucleo' and (p.proname like 'panel\\_%'"
                " or p.proname = 'exigir_ver_panel') order by p.proname"
            )).all()  # fmt: skip

    command.upgrade(cfg, "0093")
    assert funciones() == []
    command.upgrade(cfg, "0094")
    creadas = funciones()
    assert [n for n, _ in creadas] == sorted([*LECTURAS, "exigir_ver_panel"])
    acl = dict(creadas)
    assert all("cem_app=X" in acl[f] for f in LECTURAS)
    assert "cem_app" not in acl["exigir_ver_panel"]
    command.downgrade(cfg, "0093")
    assert funciones() == []
    command.upgrade(cfg, "0094")  # control
    assert funciones() == creadas
