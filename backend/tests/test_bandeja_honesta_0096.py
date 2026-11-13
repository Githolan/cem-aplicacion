"""Bandeja honesta y contrato de la pantalla de pagos contra PostgreSQL real (`0096`; informe de
fricción, tanda 2; decisiones del propietario del 12-11-2026). Datos SINTETICOS / PRUEBA / NO REAL:
ninguna cuenta es de un banco (prefijo 999) y no se mueve dinero.

La escena es la de `test_pago_manual_0055`: la administración prepara y ejecuta, mc1 aprueba y mc2
concilia. Cada decisión lleva su caso y su control. Al final, las pruebas de §4.4 que tocan la base:
«no repetir» (después de una acción, la ficha de esa persona ya no la ofrece) y «nada terminado en
hacer» (toda fila de una lista de hacer trae una principal que la base acepta).
"""

import importlib.util
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db import transaccion
from tests.conftest import alembic_config
from tests.test_circuito_pagos_0072 import (
    con,
    conforme,
    consulta,
    declarada,
    ficha,
    fila,
    ids,
    rechazada,
)
from tests.test_egresos_1a import RENOVAR, aprobada, envejecer, enviada
from tests.test_mandatos import otra_persona
from tests.test_mandatos_0012 import alta
from tests.test_orden_pago_neutra import (
    APROBAR,
    ENVIAR,
    MODIFICAR,
    PREPARAR,
    RECHAZAR,
    Escena,
    escena_economica,
    eventos,
    huella,
    llamar,
    niega,
    nueva_sesion,
    orden,
    preparada,
)
from tests.test_pago_manual_0055 import CONFIRMAR, acta, emitida, registrada, ver
from tests.test_rutas_egresos import codigo, get, post
from tests.test_vinculo_0014 import base_aparte  # noqa: F401  (es un fixture)

pytestmark = pytest.mark.db
# `base_aparte` es un fixture importado: el parámetro con su nombre no lo redefine.
# ruff: noqa: F811

MARCAR = "select nucleo.marcar_rechazo_visto(:orden, :actor, :origen, :sesion)"
CERRAR = "select nucleo.cerrar_orden_sin_pago(:orden, :version, :motivo, :actor, :origen, :sesion)"
RETIRAR = "select nucleo.retirar_orden_pago(:orden, :version, :actor, :origen, :sesion)"


@pytest.fixture
def e(motor_migrate, motor_app) -> Escena:
    return escena_economica(motor_migrate, motor_app)


def otro_que_prepara(motor_migrate, motor_app, e) -> UUID:
    """Otra persona del equipo con `egresos.preparar_orden`, que no preparó la orden."""
    x = otra_persona(motor_migrate, e.cons)
    alta(motor_app, e.cons, e.prop, acta(motor_migrate, e), tipo="miembro_consejo", persona=x)
    con(motor_migrate, e, x, "egresos.preparar_orden")
    e.sesion[x] = nueva_sesion(motor_migrate, x)
    return x


def bandeja(e, quien) -> dict:
    r = get(e, quien, "pagos/bandeja")
    assert r.status_code == 200, r.json()
    return r.json()


def acciones(e, quien, o) -> list[str]:
    return [a["codigo"] for a in ficha(e, quien, o)["acciones"]]


def principal(f: dict) -> str | None:
    [*principales] = [a["codigo"] for a in f["acciones"] if a["tipo"] == "principal"]
    assert len(principales) <= 1, f["acciones"]  # una sola principal (§4.2, regla 1)
    return principales[0] if principales else None


def no_se_pago(motor_migrate, e) -> UUID:
    """Emitida, la administración vio la cuenta y declaró; mc2 confirmó que no se pagó."""
    o = emitida(motor_migrate, e)
    ver(motor_migrate, e, e.adm, o)
    d = declarada(motor_migrate, e, e.adm, o)
    llamar(motor_migrate, e, e.mc2, CONFIRMAR, orden=o, declaracion=d)
    assert orden(motor_migrate, o)["estado"] == "fallida"
    return o


def pagada(motor_migrate, e) -> tuple[UUID, UUID]:
    o = emitida(motor_migrate, e)
    p = registrada(motor_migrate, e, o)
    conforme(motor_migrate, e, o, p)
    return o, p


def mover(motor_migrate, o, dias: int) -> None:
    with motor_migrate.begin() as c:
        c.execute(
            text("update nucleo.orden_pago set actualizada_en = now() - :d where id = :o"),
            {"d": timedelta(days=dias), "o": o},
        )


# Decisión 3: la rechazada sale de «Para preparar» y se avisa una vez --------------------------


def test_la_rechazada_no_esta_en_para_preparar_y_se_avisa_a_quien_la_preparo(motor_migrate, e):
    o = rechazada(motor_migrate, e)
    b = bandeja(e, e.adm)
    assert str(o) not in [x["orden_id"] for x in b["elementos"]]
    [aviso] = b["avisos_rechazo"]
    assert (aviso["orden_id"], aviso["motivo"]) == (str(o), "SINTETICO")
    assert aviso["proveedor_id"] == str(e.proveedor) and aviso["creada_en"]
    # Quien no prepara no recibe el aviso.
    assert bandeja(e, e.mc1)["avisos_rechazo"] == []
    # La ficha ofrece preparar otra y no tiene principal (terminada, §4.2 regla 3).
    f = ficha(e, e.adm, o)
    assert principal(f) is None and acciones(e, e.adm, o) == ["preparar_otra"]
    assert f["estado_visible"] == "rechazada"


def test_la_rechazada_no_se_avisa_a_otro_que_prepara(motor_migrate, motor_app, e):
    o = rechazada(motor_migrate, e)
    otro = otro_que_prepara(motor_migrate, motor_app, e)
    assert bandeja(e, otro)["avisos_rechazo"] == []
    assert [a["orden_id"] for a in bandeja(e, e.adm)["avisos_rechazo"]] == [str(o)]  # control


def test_entendido_queda_por_persona_y_la_orden_pasa_a_la_consulta(motor_migrate, e):
    o = rechazada(motor_migrate, e)
    r = post(e, e.adm, f"ordenes-pago/{o}/rechazo-visto", {})
    assert r.status_code == 200, r.json()
    assert r.json() == {"orden_id": str(o)}
    assert bandeja(e, e.adm)["avisos_rechazo"] == []
    assert str(o) in ids(consulta(e, e.adm, "?filtro=rechazados"))
    with motor_migrate.connect() as c:
        filas = c.execute(
            text("select persona_id, visto_en from nucleo.rechazo_visto where orden_id = :o"),
            {"o": o},
        ).all()
    assert len(filas) == 1 and filas[0].persona_id == e.adm and filas[0].visto_en is not None
    # Repetirlo no cambia nada ni falla.
    llamar(motor_migrate, e, e.adm, MARCAR, orden=o)
    with motor_migrate.connect() as c:
        n = c.execute(
            text("select count(*) from nucleo.rechazo_visto where orden_id = :o"), {"o": o}
        ).scalar_one()
    assert n == 1
    # La orden no cambió: sigue rechazada, con su motivo.
    assert orden(motor_migrate, o)["estado"] == "rechazada"


def test_entendido_solo_sobre_una_rechazada_y_con_la_facultad_de_preparar(motor_migrate, e):
    o = enviada(motor_migrate, e)
    r = post(e, e.adm, f"ordenes-pago/{o}/rechazo-visto", {})
    assert codigo(r) == (409, "orden_no_admite")
    niega(motor_migrate, e, e.adm, MARCAR, "estado_invalido", orden=o)
    x = rechazada(motor_migrate, e)
    niega(motor_migrate, e, e.mc1, MARCAR, "42501", orden=x)  # sin preparar
    llamar(motor_migrate, e, e.adm, MARCAR, orden=x)  # control


def test_el_rechazo_visto_no_lo_ve_otro_consorcio_ni_lo_escribe_cem_app(
    motor_migrate, motor_app, e
):
    o = rechazada(motor_migrate, e)
    llamar(motor_migrate, e, e.adm, MARCAR, orden=o)
    otro = escena_economica(motor_migrate, motor_app)
    with transaccion(motor_app, persona_id=otro.adm, consorcio_id=otro.cons) as c:
        with pytest.raises(DBAPIError):
            c.execute(text("select count(*) from nucleo.rechazo_visto"))
    with motor_migrate.connect() as c:
        politicas = c.execute(
            text(
                "select relrowsecurity, relforcerowsecurity from pg_class"
                " where oid = 'nucleo.rechazo_visto'::regclass"
            )
        ).one()
    assert tuple(politicas) == (True, True)
    # La lectura de otro consorcio no trae nada; la del mismo, sí (control).
    vistos = "select orden_id from nucleo.rechazos_vistos_lectura(:actor, 'api', :sesion)"
    with transaccion(motor_app, persona_id=otro.adm, consorcio_id=otro.cons) as c:
        assert (
            c.execute(text(vistos), {"actor": otro.adm, "sesion": otro.sesion[otro.adm]}).all()
            == []
        )
    with transaccion(motor_app, persona_id=e.adm, consorcio_id=e.cons) as c:
        assert c.execute(
            text(vistos), {"actor": e.adm, "sesion": e.sesion[e.adm]}
        ).scalars().all() == [o]
    with (
        pytest.raises(DBAPIError),
        transaccion(motor_app, persona_id=e.adm, consorcio_id=e.cons) as c,
    ):
        c.execute(
            text("insert into nucleo.rechazo_visto (consorcio_id, orden_id, persona_id)"
                 " values (:c, :o, :p)"), {"c": e.cons, "o": o, "p": e.mc1},
        )  # fmt: skip


# Decisión 4: el borrador lo retira cualquiera que prepara y envejece plegado ----------------


def test_un_borrador_lo_retira_otro_que_prepara_con_constancia(motor_migrate, motor_app, e):
    o = preparada(motor_migrate, e)
    otro = otro_que_prepara(motor_migrate, motor_app, e)
    assert ficha(e, otro, o)["puede_retirar"] is True
    assert "retirar" in acciones(e, otro, o)
    r = post(e, otro, f"ordenes-pago/{o}/retirar", {"version_esperada": 1})
    assert r.status_code == 200, r.json()
    fila_orden = orden(motor_migrate, o)
    assert fila_orden["estado"] == "retirada" and fila_orden["retirada_por"] == otro
    assert eventos(motor_migrate, o)[-1]["actor_persona_id"] == otro
    assert ficha(e, e.adm, o)["retiro"]["retirada_por"] == str(otro)


def test_en_revision_la_sigue_retirando_solo_quien_la_preparo(motor_migrate, motor_app, e):
    o = preparada(motor_migrate, e, en_revision=True)
    otro = otro_que_prepara(motor_migrate, motor_app, e)
    assert ficha(e, otro, o)["puede_retirar"] is False
    niega(motor_migrate, e, otro, RETIRAR, "retirar_solo_quien_preparo", orden=o, version=1)
    llamar(motor_migrate, e, e.adm, RETIRAR, orden=o, version=1)  # control


def test_un_borrador_sin_movimiento_va_a_borradores_viejos_y_no_cuenta(motor_migrate, e):
    o = preparada(motor_migrate, e)
    x = fila(e, e.adm, o)
    assert (x["grupo"], x["pendiente"], x["principal"]) == ("para_preparar", True, "enviar")
    mover(motor_migrate, o, 8)
    x = fila(e, e.adm, o)
    assert (x["grupo"], x["pendiente"]) == ("borradores_viejos", False)
    assert orden(motor_migrate, o)["estado"] == "borrador"  # nada se borra


# Decisión 5: aviso de orden repetida y fecha en la bandeja y la consulta ---------------------


def test_dos_ordenes_iguales_se_avisan_con_la_fecha_y_el_estado_de_la_otra(motor_migrate, e):
    a = enviada(motor_migrate, e)
    b = preparada(motor_migrate, e)  # el mismo proveedor y el mismo importe
    x = fila(e, e.adm, b)
    assert x["posible_duplicada_de"]["orden_id"] == str(a)
    assert x["posible_duplicada_de"]["estado_visible"] == "esperando_aprobacion"
    assert x["posible_duplicada_de"]["creada_en"] == ficha(e, e.adm, a)["orden"]["creada_en"]
    assert ficha(e, e.adm, b)["posible_duplicada_de"]["orden_id"] == str(a)
    # La segunda se envía igual: el aviso no bloquea.
    llamar(motor_migrate, e, e.adm, ENVIAR, orden=b, version=1)


def test_control_otro_importe_no_se_avisa(motor_migrate, e):
    enviada(motor_migrate, e)
    b = llamar(motor_migrate, e, e.adm, PREPARAR, importe=1_234_500)
    assert fila(e, e.adm, b)["posible_duplicada_de"] is None


def test_la_bandeja_y_la_consulta_traen_la_fecha_de_cada_orden(motor_migrate, e):
    o = preparada(motor_migrate, e)
    creada = ficha(e, e.adm, o)["orden"]["creada_en"]
    assert fila(e, e.adm, o)["creada_en"] == creada
    [x] = consulta(e, e.adm)["elementos"]
    assert (x["creada_en"], x["estado_visible"]) == (creada, "borrador")


# Decisión 12: «No se pagó» se cierra con motivo y constancia ---------------------------------


def test_no_se_pago_se_cierra_sin_pagar_con_quien_cuando_y_por_que(motor_migrate, e):
    o = no_se_pago(motor_migrate, e)
    x = fila(e, e.adm, o)
    assert x["principal"] == "volver_a_pagar"
    assert "cerrar_sin_pagar" in acciones(e, e.adm, o)
    sin_gesto = nueva_sesion(motor_migrate, e.adm, totp_min_atras=None)
    llamar(motor_migrate, e, e.adm, CERRAR, sesion=sin_gesto, orden=o, version=1,
           motivo="SINTETICO: se pagó por caja chica")  # fmt: skip
    assert fila(e, e.adm, o) is None
    f = ficha(e, e.adm, o)
    assert f["cierre_sin_pago"]["motivo"] == "SINTETICO: se pagó por caja chica"
    assert f["cierre_sin_pago"]["cerrada_por_nombre"] and f["cierre_sin_pago"]["cerrada_en"]
    assert (f["situacion"], f["estado_visible"], f["paso"]) == ("no_se_pago", "no_se_pago", None)
    assert f["acciones"] == [] and f["puede_cerrar_sin_pagar"] is False
    assert eventos(motor_migrate, o)[-1]["accion"] == "consorcio.orden_pago.cerrar_sin_pagar"
    # Cerrada, no se vuelve a pagar ni se cierra dos veces.
    niega(motor_migrate, e, e.adm, MODIFICAR, "estado_invalido", orden=o, version=1)
    niega(motor_migrate, e, e.adm, CERRAR, "estado_invalido", orden=o, version=1,
          motivo="SINTETICO")  # fmt: skip
    assert orden(motor_migrate, o)["estado"] == "fallida"


def test_cerrar_sin_pagar_por_la_ruta_y_lo_que_niega(motor_migrate, e):
    o = no_se_pago(motor_migrate, e)
    r = post(e, e.mc1, f"ordenes-pago/{o}/cerrar-sin-pagar",
             {"version_esperada": 1, "motivo": "SINTETICO"})  # fmt: skip
    assert codigo(r) == (403, "sin_permiso")
    r = post(e, e.adm, f"ordenes-pago/{o}/cerrar-sin-pagar",
             {"version_esperada": 1, "motivo": "a la 9990003100000000001234"})  # fmt: skip
    assert codigo(r) == (422, "motivo_con_cuenta")
    a = enviada(motor_migrate, e)
    r = post(e, e.adm, f"ordenes-pago/{a}/cerrar-sin-pagar",
             {"version_esperada": 1, "motivo": "SINTETICO"})  # fmt: skip
    assert codigo(r) == (409, "orden_no_admite")
    r = post(e, e.adm, f"ordenes-pago/{o}/cerrar-sin-pagar",
             {"version_esperada": 1, "motivo": "SINTETICO"})  # fmt: skip
    assert r.status_code == 200, r.json()  # control


def test_cem_app_no_cierra_a_mano(motor_app, motor_migrate, e):
    o = no_se_pago(motor_migrate, e)
    with (
        pytest.raises(DBAPIError),
        transaccion(motor_app, persona_id=e.adm, consorcio_id=e.cons) as c,
    ):
        c.execute(
            text("update nucleo.orden_pago set cerrada_sin_pago_por = :p,"
                 " cerrada_sin_pago_en = now(), motivo_cierre_sin_pago = 'x' where id = :o"),
            {"p": e.adm, "o": o},
        )  # fmt: skip
    with pytest.raises(DBAPIError) as error, motor_migrate.begin() as c:
        c.execute(
            text("update nucleo.orden_pago set cerrada_sin_pago_en = now() where id = :o"),
            {"o": o},
        )
    assert error.value.orig.diag.sqlstate == "23514"  # van las tres juntas


# P-05 y P-24: el cotejo en la barra y el grupo «Para cotejar» --------------------------------


def test_pagada_marca_cotejar_como_actual_y_cotejada_completa_la_barra(motor_migrate, e):
    o, p = pagada(motor_migrate, e)
    f = ficha(e, e.adm, o)
    assert (f["situacion"], f["estado_visible"]) == ("pagada", "pagada")
    assert f["barra"] == {"hechos": 4, "actual": "ahora"} and f["cotejado_en"] is None
    assert bandeja(e, e.mc1)["por_cotejar"] == 1
    assert bandeja(e, e.adm)["por_cotejar"] == 0  # quien no coteja
    r = post(e, e.mc1, "pagos/cotejo", {"pagos": [str(p)]})
    assert r.status_code == 200, r.json()
    for quien in (e.adm, e.mc1, e.mc2):  # la barra es de la orden, no de quien mira
        f = ficha(e, quien, o)
        assert (f["situacion"], f["estado_visible"]) == ("pagada_y_cotejada", "pagada_y_cotejada")
        assert f["barra"] == {"hechos": 5, "actual": None} and f["cotejado_en"] is not None
    [x] = consulta(e, e.adm, "?filtro=pagados")["elementos"]
    assert (x["situacion"], x["estado_visible"]) == ("pagada_y_cotejada", "pagada_y_cotejada")
    assert bandeja(e, e.mc1)["por_cotejar"] == 0


# §4.4: no repetir y nada terminado en hacer, contra la base ----------------------------------


def coherente(e, o, *personas) -> None:
    """Para cada persona: una sola principal; si la orden está en una lista de hacer, su principal
    está entre lo que la base acepta (la ficha). El estado y la barra no cambian con quien mira."""
    vistas = set()
    for quien in personas:
        f = ficha(e, quien, o)
        vistas.add((f["estado_visible"], str(f["barra"])))
        x = fila(e, quien, o)
        if x is not None and x["grupo"].startswith("para_"):
            assert x["principal"] is not None and x["principal"] == principal(f), (quien, x, f)
        elif x is not None:
            assert x["principal"] is None and not x["pendiente"]
    assert len(vistas) == 1, vistas


def test_no_repetir_y_nada_terminado_en_hacer_por_el_circuito(motor_migrate, motor_app, e):
    gente = (e.adm, e.mc1, e.mc2)
    # Enviar (T3).
    o = preparada(motor_migrate, e)
    coherente(e, o, *gente)
    assert "enviar" in acciones(e, e.adm, o)
    assert (
        post(e, e.adm, f"ordenes-pago/{o}/enviar-a-revision", {"version_esperada": 1}).status_code
        == 200
    )
    assert "enviar" not in acciones(e, e.adm, o)
    coherente(e, o, *gente)
    # Aprobar (T4).
    assert "aprobar" in acciones(e, e.mc1, o)
    llamar(motor_migrate, e, e.mc1, APROBAR, orden=o, version=1, huella=huella(motor_migrate, o, 1))
    assert "aprobar" not in acciones(e, e.mc1, o) and "rechazar" not in acciones(e, e.mc1, o)
    coherente(e, o, *gente)
    # Pagar (T8 y T9): después de ver la cuenta, no se ofrece pagar otra vez.
    assert "pagar" in acciones(e, e.adm, o)
    llamar(motor_migrate, e, e.adm, "select nucleo.emitir_orden_pago(:orden, :version, :actor,"
           " :origen, :sesion)", orden=o, version=1)  # fmt: skip
    ver(motor_migrate, e, e.adm, o)
    assert "pagar" not in acciones(e, e.adm, o)
    coherente(e, o, *gente)
    # Declarar y confirmar que no se pagó (T15, T16).
    assert "declarar_no_pago" in acciones(e, e.adm, o)
    d = declarada(motor_migrate, e, e.adm, o)
    assert "declarar_no_pago" not in acciones(e, e.adm, o)
    coherente(e, o, *gente)
    assert "confirmar_no_pago" in acciones(e, e.mc2, o)
    llamar(motor_migrate, e, e.mc2, CONFIRMAR, orden=o, declaracion=d)
    assert "confirmar_no_pago" not in acciones(e, e.mc2, o)
    coherente(e, o, *gente)
    # Cerrar sin pagar (decisión 12).
    assert "cerrar_sin_pagar" in acciones(e, e.adm, o)
    llamar(motor_migrate, e, e.adm, CERRAR, orden=o, version=1, motivo="SINTETICO")
    assert acciones(e, e.adm, o) == []
    coherente(e, o, *gente)


def test_no_repetir_al_rechazar_retirar_renovar_conciliar(motor_migrate, e):
    gente = (e.adm, e.mc1, e.mc2)
    # Rechazar (T5).
    o = enviada(motor_migrate, e)
    assert "rechazar" in acciones(e, e.mc1, o)
    llamar(motor_migrate, e, e.mc1, RECHAZAR, orden=o, version=1, motivo="SINTETICO")
    assert "rechazar" not in acciones(e, e.mc1, o) and "aprobar" not in acciones(e, e.mc1, o)
    coherente(e, o, *gente)
    # Retirar (T6).
    r = enviada(motor_migrate, e)
    assert "retirar" in acciones(e, e.adm, r)
    llamar(motor_migrate, e, e.adm, RETIRAR, orden=r, version=1)
    assert acciones(e, e.adm, r) == []
    coherente(e, r, *gente)
    # Renovar (T7).
    v = aprobada(motor_migrate, e)
    envejecer(motor_migrate, v, timedelta(days=8))
    assert ficha(e, e.adm, v)["barra"]["actual"] == "alerta"
    assert "renovar" in acciones(e, e.adm, v)
    llamar(motor_migrate, e, e.adm, RENOVAR, orden=v, version=1)
    assert "renovar" not in acciones(e, e.adm, v)
    coherente(e, v, *gente)
    # Conciliar (T13): se sigue con el mismo paso.
    c = emitida(motor_migrate, e)
    p = registrada(motor_migrate, e, c)
    f = ficha(e, e.mc2, c)
    assert principal(f) == "conciliar" and f["despues"] == "mismo_paso"
    conforme(motor_migrate, e, c, p)
    assert "conciliar" not in acciones(e, e.mc2, c) and "observar" not in acciones(e, e.mc2, c)
    coherente(e, c, *gente)


# Migración -------------------------------------------------------------------------------------


def _migracion_0096():
    ruta = Path(__file__).parents[1] / "migrations" / "versions" / "0096_bandeja_honesta.py"
    spec = importlib.util.spec_from_file_location("migracion_0096", ruta)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def test_0096_no_baja_con_rechazos_vistos(motor_migrate, e):
    """La guarda del downgrade, en una transacción que se revierte (como en `0093`)."""
    o = rechazada(motor_migrate, e)
    llamar(motor_migrate, e, e.adm, MARCAR, orden=o)
    guarda = _migracion_0096().GUARDA_DOWNGRADE
    with motor_migrate.connect() as c, c.begin() as tx:
        with pytest.raises(DBAPIError) as error, c.begin_nested():
            c.execute(text(guarda))
        assert error.value.orig.diag.message_hint == "downgrade_con_datos"
        tx.rollback()
    with motor_migrate.connect() as c:
        actual = c.execute(text("select version_num from plataforma.alembic_version")).scalar_one()
    assert actual == ScriptDirectory.from_config(alembic_config()).get_current_head()


def test_0096_baja_y_sube(base_aparte):
    cfg, motor = base_aparte
    consultas = {
        "tabla": "select count(*) from pg_class where relname = 'rechazo_visto'",
        "funciones": "select count(*) from pg_proc where proname in ('marcar_rechazo_visto',"
        " 'rechazos_vistos_lectura', 'cerrar_orden_sin_pago', 'cierre_sin_pago_lectura',"
        " 'cotejo_de_ordenes_lectura')",
        "columnas": "select count(*) from information_schema.columns where table_schema = 'nucleo'"
        " and table_name = 'orden_pago' and column_name like '%sin_pago%'",
        "retirar": "select count(*) from pg_proc where proname = 'retirar_orden_pago'"
        " and prosrc like '%o.estado <> ''borrador'' and not exists%'",
        "consulta": "select count(*) from pg_proc where proname = 'pagos_consulta_lectura'"
        " and 'creada_en' = any(proargnames)",
    }

    def contar() -> dict[str, int]:
        with motor.connect() as c:
            return {k: c.execute(text(q)).scalar_one() for k, q in consultas.items()}

    arriba = {"tabla": 1, "funciones": 5, "columnas": 3, "retirar": 1, "consulta": 1}
    command.upgrade(cfg, "0096")
    assert contar() == arriba
    command.downgrade(cfg, "0095")
    assert contar() == dict.fromkeys(arriba, 0)
    command.upgrade(cfg, "0096")
    assert contar() == arriba
