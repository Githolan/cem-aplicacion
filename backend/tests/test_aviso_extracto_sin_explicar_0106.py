"""El aviso diario cuenta lo que el extracto no explica (`0106`; comentario 29 del propietario,
09-11-2026, y su «si» del 16-11-2026). Contra PostgreSQL real. Datos SINTETICOS / PRUEBA / NO REAL:
ninguna cuenta es de un banco (prefijo 999), ningún correo sale (se lee lo que queda en el outbox) y
no se mueve dinero.

Lo reciben quienes lo ven en la bandeja (quien concilia y la administración), en un renglón aparte,
sin fecha, importe, número de operación ni cuenta, dentro del mismo correo y con el mismo tope.
Contra `c4d024a` ninguna de estas pruebas pasa: no existe `nucleo.extracto_sin_explicar_para_avisar`
y el aviso no habla del extracto.
"""

from datetime import timedelta
from uuid import UUID

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.consorcios import avisos_pagos
from app.db import transaccion
from tests.test_avisos_y_sin_explicar_0098 import (
    HOY,
    avisar,
    avisos_a,
    bandeja,
    datos_de_dinero,
    no_es_pago,
    pasa_un_dia,
    retirar,
    sembrados,
    sin_explicar,
)
from tests.test_cotejo_0074 import dato_pago, extracto, linea, linea_del_pago, pagado, subio
from tests.test_egresos_1a import enviada
from tests.test_mandatos import otra_persona
from tests.test_mandatos_0012 import alta
from tests.test_orden_pago_neutra import Escena, escena_economica, nueva_sesion
from tests.test_pago_manual_0055 import acta, emitida, registrada
from tests.test_vinculo_0014 import base_aparte  # noqa: F401  (es un fixture)

pytestmark = pytest.mark.db

UN_MOVIMIENTO = "- Prueba: hay un movimiento del extracto sin explicar para revisar."
DOS_MOVIMIENTOS = "- Prueba: hay 2 movimientos del extracto sin explicar para revisar."
REFERENCIA = "0048213999"
"""El número de operación del débito que nada explica: tampoco va en el correo."""
COMISION = ("-12.400,00", "12.400,00", "1240000", "12400")
"""El importe del débito que nada explica, en todas las formas que podría tomar."""


@pytest.fixture
def e(motor_migrate, motor_app) -> Escena:
    return escena_economica(motor_migrate, motor_app)


def textos(motor_migrate, persona: UUID) -> list[str]:
    return [a["carga"]["texto"] for a in avisos_a(motor_migrate, persona)]


def sin_dinero(motor_migrate, persona: UUID) -> list[str]:
    """Lo que en los correos de esa persona parece un dato de dinero o del extracto."""
    prohibido = [*sembrados(), *COMISION[1:], REFERENCIA]
    return [h for t in textos(motor_migrate, persona) for h in datos_de_dinero(t, prohibido)]


def un_debito_sin_explicar(motor_migrate, e) -> UUID:
    """Un pago conciliado y cotejado por su línea, y otro débito del mismo día que nada explica."""
    _, p = pagado(motor_migrate, e)
    fecha = dato_pago(motor_migrate, p)["fecha_operacion"]
    subio(
        e, e.mc2, extracto(linea_del_pago(motor_migrate, p), linea(fecha, COMISION[0], REFERENCIA))
    )
    return p


def test_quien_concilia_y_la_administracion_reciben_el_renglon_sin_datos_de_dinero(
    motor_migrate, motor_app, e
):
    p = un_debito_sin_explicar(motor_migrate, e)
    # Control: el pago tiene su orden conciliada; sin el extracto nadie tiene nada que hacer.
    assert bandeja(e, e.adm)["sin_explicar"] == 1
    avisar(motor_app)
    for quien in (e.mc1, e.mc2, e.adm):
        [aviso] = avisos_a(motor_migrate, quien)
        assert aviso["carga"]["asunto"] == avisos_pagos.ASUNTO
        assert UN_MOVIMIENTO in aviso["carga"]["texto"]
        assert "/pagos" in aviso["carga"]["texto"]
        assert sin_dinero(motor_migrate, quien) == []
        assert str(dato_pago(motor_migrate, p)["numero_operacion"]) not in aviso["carga"]["texto"]
    # Control del detector: el importe y la referencia sembrados sí aparecerían si estuvieran.
    assert datos_de_dinero(f"Comisión {COMISION[1]} ref {REFERENCIA}", []) != []


def test_sin_nada_sin_explicar_no_sale_el_renglon_ni_el_correo(motor_migrate, motor_app, e):
    o = emitida(motor_migrate, e)
    p = registrada(motor_migrate, e, o)
    subio(e, e.mc2, extracto(linea_del_pago(motor_migrate, p)))
    assert bandeja(e, e.adm)["sin_explicar"] == 0
    avisar(motor_app)
    for quien in (e.mc1, e.mc2, e.adm):
        assert all("extracto" not in t for t in textos(motor_migrate, quien)), quien
    assert avisos_a(motor_migrate, e.mc1) == []  # mc1 aprobó: no concilia ni tiene nada más
    # Control: un débito más que nada explica, al día siguiente, sí sale.
    fecha = dato_pago(motor_migrate, p)["fecha_operacion"]
    subio(e, e.mc2, extracto(linea_del_pago(motor_migrate, p), linea(fecha, "-7,50", "")))
    avisar(motor_app, HOY + timedelta(days=1))
    [aviso] = avisos_a(motor_migrate, e.mc1)
    assert UN_MOVIMIENTO in aviso["carga"]["texto"]


def test_el_correo_cuenta_lo_mismo_que_la_bandeja(motor_migrate, motor_app, e):
    _, p = pagado(motor_migrate, e)
    fecha = dato_pago(motor_migrate, p)["fecha_operacion"]
    subio(e, e.mc2, extracto(linea_del_pago(motor_migrate, p), linea(fecha, "-7,50", ""),
                             linea(fecha, COMISION[0], REFERENCIA)))  # fmt: skip
    assert sin_explicar(e, e.adm)["total"] == 2
    avisar(motor_app)
    [aviso] = avisos_a(motor_migrate, e.adm)
    assert DOS_MOVIMIENTOS in aviso["carga"]["texto"]
    assert UN_MOVIMIENTO not in aviso["carga"]["texto"]
    assert sin_dinero(motor_migrate, e.adm) == []


def test_lo_marcado_como_no_es_pago_sale_del_correo(motor_migrate, motor_app, e):
    un_debito_sin_explicar(motor_migrate, e)
    [m] = sin_explicar(e, e.mc1)["elementos"]
    assert no_es_pago(e, e.mc1, m["movimiento_id"]).status_code == 200
    avisar(motor_app)
    for quien in (e.mc1, e.mc2, e.adm):
        assert avisos_a(motor_migrate, quien) == [], quien
    # Control: retirada la marca, vuelve al correo.
    r = retirar(e, e.mc2, m["movimiento_id"])
    assert r.status_code == 200, r.json()
    avisar(motor_app, HOY + timedelta(days=1))
    assert UN_MOVIMIENTO in avisos_a(motor_migrate, e.adm)[0]["carga"]["texto"]


def test_quien_no_lee_el_extracto_no_lo_recibe(motor_migrate, motor_app, e):
    un_debito_sin_explicar(motor_migrate, e)
    vecina = otra_persona(motor_migrate, e.cons)
    alta(motor_app, e.cons, e.prop, acta(motor_migrate, e), tipo="miembro_consejo",
         persona=vecina)  # fmt: skip
    e.sesion[vecina] = nueva_sesion(motor_migrate, vecina)
    avisar(motor_app)
    # El propietario de la escena no tiene ninguna facultad de egresos.
    assert avisos_a(motor_migrate, e.prop) == []
    # Control: una consejera nueva, que concilia, sí.
    [aviso] = avisos_a(motor_migrate, vecina)
    assert UN_MOVIMIENTO in aviso["carga"]["texto"]


def test_un_solo_correo_con_las_ordenes_y_el_extracto_y_el_mismo_tope(motor_migrate, motor_app, e):
    un_debito_sin_explicar(motor_migrate, e)
    enviada(motor_migrate, e)
    avisar(motor_app)
    [aviso] = avisos_a(motor_migrate, e.mc1)
    assert "- Prueba: una orden espera su aprobación." in aviso["carga"]["texto"]
    assert UN_MOVIMIENTO in aviso["carga"]["texto"]
    # El mismo día, otra ventana: el extracto no suma un correo.
    avisar(motor_app, HOY + timedelta(hours=3))
    for quien in (e.mc1, e.mc2, e.adm):
        assert len(avisos_a(motor_migrate, quien)) == 1, quien
    # Control: un día después del envío real, sale el siguiente.
    pasa_un_dia(motor_migrate, e.mc1)
    avisar(motor_app, HOY + timedelta(days=1))
    assert len(avisos_a(motor_migrate, e.mc1)) == 2


def test_el_tope_usado_por_las_ordenes_tambien_frena_el_extracto(motor_migrate, motor_app, e):
    enviada(motor_migrate, e)
    avisar(motor_app)
    assert len(avisos_a(motor_migrate, e.mc1)) == 1  # control: el tope del día ya se usó
    un_debito_sin_explicar(motor_migrate, e)
    avisar(motor_app, HOY + timedelta(hours=6))
    assert len(avisos_a(motor_migrate, e.mc1)) == 1
    assert all("extracto" not in t for t in textos(motor_migrate, e.mc1))


def test_solo_el_trabajo_lee_lo_que_el_extracto_no_explica_en_todos(motor_app, e):
    lectura = "select * from nucleo.extracto_sin_explicar_para_avisar()"
    for persona, consorcio in ((e.mc1, e.cons), (None, e.cons), (e.mc1, None)):
        with pytest.raises(DBAPIError) as error:
            with transaccion(motor_app, persona_id=persona, consorcio_id=consorcio) as c:
                c.execute(text(lectura))
        assert error.value.orig.diag.sqlstate == "42501", (persona, consorcio)
    # Las internas no se conceden a `cem_app`.
    for interna in ("nucleo.movimientos_sin_cotejo_de(cast(:c as uuid))",
                    "nucleo.pagos_para_explicar_de(cast(:c as uuid), '{}', current_date,"
                    " current_date)"):  # fmt: skip
        with pytest.raises(DBAPIError) as error:
            with transaccion(motor_app, persona_id=None) as c:
                c.execute(text(f"select * from {interna}"), {"c": e.cons})
        assert error.value.orig.diag.sqlstate == "42501", interna
    # Control: sin persona ni consorcio, la lectura funciona (y este edificio no tiene extracto).
    with transaccion(motor_app, persona_id=None) as c:
        filas = c.execute(text(lectura)).mappings().all()
    assert [f for f in filas if f["consorcio_id"] == e.cons] == []


def test_la_lectura_del_trabajo_no_trae_cuentas_ni_proveedores(motor_migrate, motor_app, e):
    un_debito_sin_explicar(motor_migrate, e)
    with transaccion(motor_app, persona_id=None) as c:
        [fila] = c.execute(
            text("select * from nucleo.extracto_sin_explicar_para_avisar()"
                 " where consorcio_id = :c"),
            {"c": e.cons},
        ).mappings().all()  # fmt: skip
    assert fila["consorcio_id"] == e.cons
    assert {str(x["persona_id"]) for x in fila["lectores"]} == {
        str(e.mc1), str(e.mc2), str(e.adm)}  # fmt: skip
    assert {k for m in fila["movimientos"] for k in m} == {
        "movimiento_id", "fecha", "importe_centavos", "moneda", "numero_operacion"}  # fmt: skip
    assert {k for p in fila["pagos"] for k in p} <= {
        "pago_id", "numero_operacion", "fecha_operacion", "importe_centavos", "moneda",
        "conforme", "cotejado", "anulado"}  # fmt: skip
    crudo = str(dict(fila))
    assert [s for s in sembrados() if s in crudo and not s.isdigit()] == []


def test_el_texto_en_singular_y_en_plural():
    aviso = avisos_pagos.Aviso("x@ejemplo.com", sin_explicar={"Prueba": 1, "Otra": 3})
    cuerpo = avisos_pagos.texto(aviso, "https://cem.ejemplo.com/pagos", 1)
    assert "- Otra: hay 3 movimientos del extracto sin explicar para revisar." in cuerpo
    assert UN_MOVIMIENTO in cuerpo
    assert cuerpo.index("- Otra:") < cuerpo.index("- Prueba:")


def _definiciones(motor) -> dict:
    funciones = (
        "nucleo.movimientos_sin_cotejo_lectura(uuid, text, uuid)",
        "nucleo.pagos_para_explicar_lectura(text[], date, date, uuid, text, uuid)",
        "nucleo.movimientos_sin_cotejo_de(uuid)",
        "nucleo.pagos_para_explicar_de(uuid, text[], date, date)",
        "nucleo.extracto_sin_explicar_para_avisar()",
    )
    with motor.connect() as c:
        return {
            f: c.execute(
                text("select pg_get_functiondef(p.oid), coalesce(p.proacl::text, ''), p.prosecdef"
                     " from pg_proc p where p.oid = to_regprocedure(:f)"), {"f": f},
            ).one_or_none()
            for f in funciones
        }  # fmt: skip


def test_0106_ida_y_vuelta_deja_todo_como_estaba(base_aparte):  # noqa: F811
    cfg, motor = base_aparte
    command.upgrade(cfg, "0105")
    antes = _definiciones(motor)
    command.upgrade(cfg, "0106")
    con = _definiciones(motor)
    api, nuevas = list(antes)[:2], list(antes)[2:]
    # Control: la 0106 cambió las dos de la API y creó las tres nuevas.
    assert all(antes[f] is not None and con[f][0] != antes[f][0] for f in api)
    assert all(antes[f] is None and con[f] is not None for f in nuevas)
    assert "cem_app" in con["nucleo.extracto_sin_explicar_para_avisar()"][1]
    assert all("cem_app" not in con[f][1] for f in nuevas[:2])
    command.downgrade(cfg, "0105")
    assert _definiciones(motor) == antes
    command.upgrade(cfg, "0106")
    assert _definiciones(motor) == con
