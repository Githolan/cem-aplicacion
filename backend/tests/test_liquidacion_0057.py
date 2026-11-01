"""Liquidación neutral (`0057`): borrador por período, gastos pagados y simulación del reparto.

Mandato del propietario del 31-10-2026: preparar y simular sin depender de las decisiones 4.2 y
5. La escena es la de egresos (`escena_economica`): la administración tiene `liquidacion.preparar`
por su paquete; el Consejo no.
"""

from decimal import ROUND_FLOOR, Decimal
from uuid import UUID, uuid4

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db import transaccion
from tests.test_coeficientes_0021 import registrar
from tests.test_contexto_mandatos import otra_unidad
from tests.test_orden_pago_neutra import llamar, niega, nueva_sesion, orden
from tests.test_pago_manual_0055 import (
    CONCILIAR,
    IMPORTE,
    _en_paralelo,
    acta,
    emitida,
    registrada,
)
from tests.test_pago_manual_0055 import e as e  # noqa: F401  (es un fixture)
from tests.test_rutas_egresos import cliente, codigo, get, post, url
from tests.test_vinculo_0014 import base_aparte  # noqa: F401  (es un fixture)

pytestmark = pytest.mark.db

PERIODO = "2026-10-01"
PREPARAR = "select nucleo.preparar_liquidacion(:periodo, :actor, :origen, :sesion)"
AGREGAR = (
    "select nucleo.agregar_gasto_liquidacion(:liquidacion, :orden, :grupo, :actor, :origen,"
    " :sesion)"
)
QUITAR = "select nucleo.quitar_gasto_liquidacion(:liquidacion, :gasto, :actor, :origen, :sesion)"


def porcentajes(n: int) -> list[Decimal]:
    """40 % para la primera unidad y el resto de 59,96 % en partes iguales, truncadas: suman menos
    de 100 %, como una fuente real, y el motor reparte el total exacto igual."""
    resto = (Decimal("59.96") / (n - 1)).quantize(Decimal("0.000001"), rounding=ROUND_FLOOR)
    return [Decimal("40")] + [resto] * (n - 1)


def unidades(motor_migrate, e) -> list[UUID]:
    with motor_migrate.connect() as c:
        return list(
            c.execute(
                text("select id from nucleo.unidad where consorcio_id = :c"
                     " and eliminado_en is null order by designacion"), {"c": e.cons},
            ).scalars()
        )  # fmt: skip


@pytest.fixture
def esquema(motor_migrate, motor_app, e) -> UUID:
    """`expensas_comunes` con todas las unidades activas de la escena (al menos tres), vigente
    desde 2024."""
    while len(unidades(motor_migrate, e)) < 3:
        otra_unidad(motor_migrate, e.cons, f"LIQ {uuid4().hex[:6]}")
    us = unidades(motor_migrate, e)
    pesos = porcentajes(len(us))
    coeficientes = {str(u): f"{p:.6f}" for u, p in zip(us, pesos, strict=True)}
    return registrar(motor_app, e.cons, coeficientes, decision=acta(motor_migrate, e))


def pagada(motor_migrate, e) -> UUID:
    """Preparada, aprobada, emitida, pagada a mano y conciliada por otro consejero."""
    o = emitida(motor_migrate, e)
    p = registrada(motor_migrate, e, o)
    llamar(motor_migrate, e, e.mc2, CONCILIAR, orden=o, pago=p, resultado="conforme", motivo=None)
    assert orden(motor_migrate, o)["estado"] == "pagada"
    return o


def borrador(motor_migrate, e, periodo=PERIODO) -> UUID:
    return llamar(motor_migrate, e, e.adm, PREPARAR, periodo=periodo)


# El caso del piloto, de punta a punta por HTTP ---------------------------------------------


def test_prepara_agrega_simula_y_quita_por_http(motor_migrate, e, esquema):
    o = pagada(motor_migrate, e)
    r = post(e, e.adm, "liquidaciones", {"periodo": PERIODO})
    assert r.status_code == 201, r.json()
    liq = r.json()["id"]
    [lista] = get(e, e.adm, "liquidaciones").json()["elementos"]
    assert (lista["id"], lista["periodo"], lista["version"], lista["estado"]) == (
        liq,
        PERIODO,
        1,
        "borrador",
    )
    liquidables = get(e, e.adm, "liquidaciones/ordenes-liquidables").json()["elementos"]
    assert [x["orden_id"] for x in liquidables] == [str(o)]

    detalle = get(e, e.adm, f"liquidaciones/{liq}").json()
    assert [g["grupo"] for g in detalle["grupos"]] == ["expensas_comunes"]
    assert detalle["gastos"] == [] and detalle["simulacion"]["unidades"] == []

    r = post(
        e, e.adm, f"liquidaciones/{liq}/gastos", {"orden_id": str(o), "grupo": "expensas_comunes"}
    )
    assert r.status_code == 201, r.json()
    gasto = r.json()["id"]
    detalle = get(e, e.adm, f"liquidaciones/{liq}").json()
    [g] = detalle["gastos"]
    assert (g["orden_id"], g["importe_centavos"], g["esquema_id"], g["esquema_version"]) == (
        str(o),
        IMPORTE,
        str(esquema),
        1,
    )
    sim = detalle["simulacion"]
    assert sim["bloqueo"] is None and sim["total_centavos"] == IMPORTE
    # RN05: lo repartido es exactamente el total, aunque los coeficientes no sumen 100 %.
    assert sum(u["total_centavos"] for u in sim["unidades"]) == IMPORTE
    pesos = porcentajes(len(unidades(motor_migrate, e)))
    assert sum(pesos) < 100 and len(sim["unidades"]) == len(pesos)
    assert all(u["por_grupo"] == {"expensas_comunes": u["total_centavos"]} for u in sim["unidades"])
    mayor = max(sim["unidades"], key=lambda u: u["total_centavos"])
    exacto = Decimal(IMPORTE) * pesos[0] / sum(pesos)
    assert abs(mayor["total_centavos"] - exacto) < 1
    assert get(e, e.adm, "liquidaciones/ordenes-liquidables").json()["elementos"] == []

    r = post(e, e.adm, f"liquidaciones/{liq}/gastos/{gasto}/quitar", {})
    assert r.status_code == 200, r.json()
    assert get(e, e.adm, f"liquidaciones/{liq}").json()["gastos"] == []
    assert len(get(e, e.adm, "liquidaciones/ordenes-liquidables").json()["elementos"]) == 1
    assert codigo(post(e, e.adm, f"liquidaciones/{liq}/gastos/{gasto}/quitar", {})) == (
        409,
        "liquidacion_no_admite",
    )
    # Quitada, vuelve a entrar: la historia guarda las dos.
    r = post(
        e, e.adm, f"liquidaciones/{liq}/gastos", {"orden_id": str(o), "grupo": "expensas_comunes"}
    )
    assert r.status_code == 201, r.json()
    with motor_migrate.connect() as c:
        assert c.execute(text("select count(*) from nucleo.liquidacion_gasto"
                              " where orden_id = :o"), {"o": o}).scalar_one() == 2  # fmt: skip
        acciones = c.execute(
            text("select accion from auditoria.eventos where entidad = 'liquidacion'"
                 " and entidad_id = :l order by secuencia"), {"l": liq},
        ).scalars().all()  # fmt: skip
    assert acciones == [
        "consorcio.liquidacion.preparar",
        "consorcio.liquidacion.agregar_gasto",
        "consorcio.liquidacion.quitar_gasto",
        "consorcio.liquidacion.agregar_gasto",
    ]


def test_el_reintento_con_la_misma_clave_no_abre_otro_borrador(e, esquema):
    clave = {"Idempotency-Key": str(uuid4())}
    primero = post(e, e.adm, "liquidaciones", {"periodo": PERIODO}, clave)
    segundo = post(e, e.adm, "liquidaciones", {"periodo": PERIODO}, clave)
    assert primero.status_code == segundo.status_code == 201
    assert primero.json() == segundo.json()
    assert len(get(e, e.adm, "liquidaciones").json()["elementos"]) == 1


# Preparar ----------------------------------------------------------------------------------


def test_un_solo_borrador_por_periodo_y_versiones(motor_migrate, e):
    primero = borrador(motor_migrate, e)
    niega(motor_migrate, e, e.adm, PREPARAR, "liquidacion_en_borrador", periodo=PERIODO)
    assert codigo(post(e, e.adm, "liquidaciones", {"periodo": PERIODO})) == (
        409,
        "liquidacion_no_admite",
    )
    otro = borrador(motor_migrate, e, "2026-11-01")  # control: otro período sí
    assert otro != primero
    niega(motor_migrate, e, e.adm, PREPARAR, "periodo_invalido", periodo="2026-10-15")
    assert codigo(post(e, e.adm, "liquidaciones", {"periodo": "2026-10-15"}))[0] == 422


def test_dos_borradores_del_mismo_periodo_a_la_vez_uno_solo(motor_migrate, e):
    r = _en_paralelo(motor_migrate, e, (e.adm, PREPARAR, {"periodo": PERIODO}),
                     (e.adm, PREPARAR, {"periodo": PERIODO}))  # fmt: skip
    assert r["esperaba"] and r.get("motivo") == "liquidacion_en_borrador", r


def test_la_publicada_es_una_sola_por_periodo_pa04(motor_migrate):
    with motor_migrate.connect() as c:
        indices = dict(c.execute(text(
            "select indexname, indexdef from pg_indexes where schemaname = 'nucleo'"
            " and tablename = 'liquidacion'")).all())  # fmt: skip
    assert "WHERE (estado = 'publicada'::text)" in indices["liquidacion_publicada_unica"]
    assert "(consorcio_id, periodo)" in indices["liquidacion_publicada_unica"]
    assert "WHERE (estado = 'borrador'::text)" in indices["liquidacion_borrador_unico"]


# Agregar y quitar --------------------------------------------------------------------------


def test_solo_se_liquida_una_orden_pagada_y_una_sola_vez(motor_migrate, e, esquema):
    liq = borrador(motor_migrate, e)
    sin_pagar = emitida(motor_migrate, e)
    niega(motor_migrate, e, e.adm, AGREGAR, "orden_no_pagada", liquidacion=liq, orden=sin_pagar,
          grupo="expensas_comunes")  # fmt: skip
    o = pagada(motor_migrate, e)
    llamar(motor_migrate, e, e.adm, AGREGAR, liquidacion=liq, orden=o, grupo="expensas_comunes")
    niega(motor_migrate, e, e.adm, AGREGAR, "gasto_ya_liquidado", liquidacion=liq, orden=o,
          grupo="expensas_comunes")  # fmt: skip
    otra = borrador(motor_migrate, e, "2026-11-01")
    niega(motor_migrate, e, e.adm, AGREGAR, "gasto_ya_liquidado", liquidacion=otra, orden=o,
          grupo="expensas_comunes")  # fmt: skip
    r = post(
        e, e.adm, f"liquidaciones/{otra}/gastos", {"orden_id": str(o), "grupo": "expensas_comunes"}
    )
    assert codigo(r) == (409, "orden_no_liquidable")
    r = post(
        e,
        e.adm,
        f"liquidaciones/{liq}/gastos",
        {"orden_id": str(sin_pagar), "grupo": "expensas_comunes"},
    )
    assert codigo(r) == (409, "orden_no_liquidable")


def test_dos_borradores_no_toman_la_misma_orden_a_la_vez(motor_migrate, e, esquema):
    o = pagada(motor_migrate, e)
    a, b = borrador(motor_migrate, e), borrador(motor_migrate, e, "2026-11-01")
    datos = {"orden": o, "grupo": "expensas_comunes"}
    r = _en_paralelo(motor_migrate, e, (e.adm, AGREGAR, datos | {"liquidacion": a}),
                     (e.adm, AGREGAR, datos | {"liquidacion": b}))  # fmt: skip
    assert r["esperaba"] and r.get("motivo") == "gasto_ya_liquidado", r


def test_el_grupo_necesita_un_esquema_vigente_en_el_periodo(motor_migrate, motor_app, e, esquema):
    o = pagada(motor_migrate, e)
    viejo = borrador(motor_migrate, e, "2023-12-01")  # antes de que rija el esquema (2024)
    niega(motor_migrate, e, e.adm, AGREGAR, "sin_esquema", liquidacion=viejo, orden=o,
          grupo="expensas_comunes")  # fmt: skip
    liq = borrador(motor_migrate, e)
    niega(motor_migrate, e, e.adm, AGREGAR, "sin_esquema", liquidacion=liq, orden=o,
          grupo="agua")  # fmt: skip
    r = post(e, e.adm, f"liquidaciones/{liq}/gastos", {"orden_id": str(o), "grupo": "agua"})
    assert codigo(r) == (422, "sin_esquema")
    assert get(e, e.adm, f"liquidaciones/{viejo}").json()["grupos"] == []
    llamar(motor_migrate, e, e.adm, AGREGAR, liquidacion=liq, orden=o,
           grupo="expensas_comunes")  # control  # fmt: skip


def test_quitar_un_gasto_de_otra_liquidacion_no_existe(motor_migrate, e, esquema):
    o = pagada(motor_migrate, e)
    liq, otra = borrador(motor_migrate, e), borrador(motor_migrate, e, "2026-11-01")
    gasto = llamar(motor_migrate, e, e.adm, AGREGAR, liquidacion=liq, orden=o,
                   grupo="expensas_comunes")  # fmt: skip
    niega(motor_migrate, e, e.adm, QUITAR, "no_encontrado", liquidacion=otra, gasto=gasto)
    assert codigo(post(e, e.adm, f"liquidaciones/{otra}/gastos/{gasto}/quitar", {})) == (
        404,
        "no_encontrado",
    )
    llamar(motor_migrate, e, e.adm, QUITAR, liquidacion=liq, gasto=gasto)  # control


# Simulación --------------------------------------------------------------------------------


def test_la_simulacion_se_bloquea_si_el_esquema_no_coincide_con_las_unidades(
    motor_migrate, e, esquema
):
    o = pagada(motor_migrate, e)
    liq = borrador(motor_migrate, e)
    llamar(motor_migrate, e, e.adm, AGREGAR, liquidacion=liq, orden=o, grupo="expensas_comunes")
    assert get(e, e.adm, f"liquidaciones/{liq}").json()["simulacion"]["bloqueo"] is None  # control
    otra_unidad(motor_migrate, e.cons, "LIQ nueva sin coeficiente")
    sim = get(e, e.adm, f"liquidaciones/{liq}").json()["simulacion"]
    assert "LIQ nueva sin coeficiente" in sim["bloqueo"]
    assert sim["unidades"] == [] and sim["total_centavos"] == IMPORTE


def test_un_esquema_nuevo_vigente_en_el_periodo_bloquea_los_gastos_viejos(
    motor_migrate, motor_app, e, esquema
):
    o = pagada(motor_migrate, e)
    liq = borrador(motor_migrate, e)
    gasto = llamar(motor_migrate, e, e.adm, AGREGAR, liquidacion=liq, orden=o,
                   grupo="expensas_comunes")  # fmt: skip
    assert get(e, e.adm, f"liquidaciones/{liq}").json()["simulacion"]["bloqueo"] is None  # control
    us = unidades(motor_migrate, e)
    pesos = list(reversed(porcentajes(len(us))))
    coeficientes = {str(u): f"{p:.6f}" for u, p in zip(us, pesos, strict=True)}
    nuevo = registrar(motor_app, e.cons, coeficientes, desde="2026-10-15",
                      decision=acta(motor_migrate, e))  # fmt: skip
    detalle = get(e, e.adm, f"liquidaciones/{liq}").json()
    assert detalle["grupos"][0]["esquema_id"] == str(nuevo)
    sim = detalle["simulacion"]
    assert "expensas_comunes" in sim["bloqueo"] and sim["unidades"] == []
    llamar(motor_migrate, e, e.adm, QUITAR, liquidacion=liq, gasto=gasto)
    llamar(motor_migrate, e, e.adm, AGREGAR, liquidacion=liq, orden=o, grupo="expensas_comunes")
    detalle = get(e, e.adm, f"liquidaciones/{liq}").json()
    assert [g["esquema_id"] for g in detalle["gastos"]] == [str(nuevo)]
    assert detalle["simulacion"]["bloqueo"] is None and detalle["simulacion"]["unidades"]


# Barreras ----------------------------------------------------------------------------------


def test_sin_liquidacion_preparar_no_se_prepara_ni_se_lee(motor_migrate, e):
    niega(motor_migrate, e, e.mc1, PREPARAR, "42501", periodo=PERIODO)
    assert codigo(post(e, e.mc1, "liquidaciones", {"periodo": PERIODO})) == (403, "sin_permiso")
    assert codigo(get(e, e.mc1, "liquidaciones")) == (403, "sin_permiso")
    assert get(e, e.adm, "liquidaciones").status_code == 200  # control


def test_r10_la_base_pide_el_segundo_factor_reciente(motor_migrate, e):
    vieja_totp = nueva_sesion(motor_migrate, e.adm, horas_atras=1, totp_min_atras=11)
    assert codigo(get(e, e.adm, "liquidaciones", sesion=vieja_totp)) == (
        403,
        "segundo_factor_requerido",
    )
    larga = nueva_sesion(motor_migrate, e.adm, horas_atras=13)
    assert codigo(get(e, e.adm, "liquidaciones", sesion=larga)) == (
        403,
        "sesion_reciente_requerida",
    )
    assert get(e, e.adm, "liquidaciones").status_code == 200  # control


def test_otro_consorcio_no_ve_la_liquidacion(motor_migrate, e):
    liq = borrador(motor_migrate, e)
    with cliente(e.adm, e.sesion[e.adm]) as c:
        otra = c.get(url(e, f"liquidaciones/{liq}").replace(str(e.cons), str(uuid4())))
    assert codigo(otra)[0] in (403, 404)
    assert get(e, e.adm, f"liquidaciones/{liq}").status_code == 200  # control


def test_cem_app_no_lee_ni_cambia_las_tablas(motor_migrate, motor_app, e):
    liq = borrador(motor_migrate, e)
    with transaccion(motor_app, persona_id=e.adm, consorcio_id=e.cons) as c:
        for tabla in ("liquidacion", "liquidacion_gasto", "liquidacion_gasto_quitado"):
            assert not c.execute(
                text("select has_table_privilege('cem_app', :t, 'select')"),
                {"t": f"nucleo.{tabla}"},
            ).scalar_one()  # fmt: skip
    with pytest.raises(DBAPIError) as error, motor_migrate.begin() as c:
        c.execute(text("update nucleo.liquidacion set version = 2 where id = :l"), {"l": liq})
    assert error.value.orig.diag.message_hint == "historia_inmutable"


# Migración ---------------------------------------------------------------------------------


def test_0057_ida_y_vuelta(base_aparte):  # noqa: F811
    cfg, motor = base_aparte

    def tablas() -> int:
        with motor.connect() as c:
            return c.execute(text("select count(*) from pg_tables where schemaname = 'nucleo'"
                                  " and tablename like 'liquidacion%'")).scalar_one()  # fmt: skip

    command.upgrade(cfg, "0057")
    assert tablas() == 3
    command.downgrade(cfg, "0056")
    assert tablas() == 0
    command.upgrade(cfg, "0057")  # control
    assert tablas() == 3
