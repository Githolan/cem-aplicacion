"""«Pagué otra cosa» y «Volver a pagar» contra PostgreSQL real (`0075`). Datos SINTETICOS / PRUEBA /
NO REAL: ninguna cuenta es de un banco (prefijo 999) y no se mueve dinero.

Diseño: `Rediseno_Circuito_Pagos_2026-11-06.md`, §7, §9, §14 y §17. La escena es la de
`test_pago_manual_0055`: la administración prepara y ejecuta, mc1 aprueba y mc2 concilia.
"""

import importlib.util
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db import transaccion
from tests.conftest import clave_idempotencia
from tests.test_circuito_pagos_0072 import (
    consulta,
    declarada,
    ficha,
    fila,
    ids,
    observada,
    paso,
    segundo_ejecutor,
)
from tests.test_cotejo_0074 import extracto, linea, pendientes, subio, tildar
from tests.test_egresos_1a import _hoy, aprobada, enviada
from tests.test_liquidacion_0057 import (  # noqa: F401  (`esquema` es un fixture)
    AGREGAR,
    borrador,
    esquema,
    pagada,
)
from tests.test_orden_pago_neutra import (
    APROBAR,
    EMITIR,
    ENVIAR,
    ESENCIALES,
    MODIFICAR,
    PREPARAR,
    cbu_sintetico,
    escena_economica,
    huella,
    llamar,
    niega,
    nueva_sesion,
    orden,
    otra_cuenta_en_el_consejo,
    preparada,
    versiones,
)
from tests.test_ordenes_trabadas_0067 import dar_de_baja, por_aclarar
from tests.test_pago_manual_0055 import (
    CONFIRMAR,
    CUENTA,
    DECLARAR,
    IMPORTE,
    REGISTRAR,
    VER,
    acciones,
    comprobante,
    con_ejecutar,
    emitida,
    evidencia,
    registrada,
    segunda_apertura,
    ver,
)
from tests.test_pago_manual_0055 import e as e  # noqa: F401  (es un fixture)
from tests.test_rutas_egresos import codigo, get, post
from tests.test_vinculo_0014 import base_aparte  # noqa: F401  (es un fixture)

pytestmark = [pytest.mark.db, pytest.mark.redis]
# `base_aparte`, `e` y `esquema` son fixtures importados: el parámetro con su nombre no los
# redefine.
# ruff: noqa: F811

OTRA_CUENTA = cbu_sintetico(2)
REAL_DE_MENOS = Decimal(IMPORTE - 2700) / 100
REAL_DE_MAS = Decimal(IMPORTE + 1250) / 100
APROBADO = Decimal(IMPORTE) / 100
DIFERENCIA = (
    "select nucleo.registrar_diferencia_pago_manual(:orden, :version, :huella, :numero, :fecha,"
    " :importe_real, :moneda_p, :cuenta, :documento, :explicacion, :actor, :origen, :sesion)"
)
RESOLVER = (
    "select nucleo.resolver_diferencia_pago_manual(:orden, :diferencia, :resolucion, :actor,"
    " :origen, :sesion)"
)
LEER = text("select id from nucleo.diferencia_pago_manual")
RECUPERAR = (
    "select nucleo.recuperar_diferencia_pago_manual(:orden, :diferencia, :actor, :origen, :sesion)"
)


def datos(motor_migrate, e, o, **cambios) -> dict:
    return evidencia(motor_migrate, e, o) | {
        "importe_real": REAL_DE_MENOS, "explicacion": "SINTETICO: me equivoqué al escribirlo",
    } | cambios  # fmt: skip


def con_diferencia(motor_migrate, e, o, quien=None, **cambios) -> UUID:
    """Quien transfiere ve la cuenta y registra lo que salió del banco."""
    quien = quien or e.adm
    ver(motor_migrate, e, quien, o)
    return llamar(motor_migrate, e, quien, DIFERENCIA, **datos(motor_migrate, e, o, **cambios))


def resuelta(motor_migrate, e, o, d, resolucion, quien=None) -> str:
    return llamar(motor_migrate, e, quien or e.mc2, RESOLVER, orden=o, diferencia=d,
                  resolucion=resolucion)  # fmt: skip


def fila_de(motor_migrate, d) -> dict:
    with motor_migrate.connect() as c:
        return dict(
            c.execute(text("select * from nucleo.diferencia_pago_manual where id = :d"), {"d": d})
            .mappings()
            .one()
        )


def resultados(motor_migrate, o) -> list[str]:
    with motor_migrate.connect() as c:
        return list(c.execute(
            text("select r.resultado from nucleo.resultado_ejecucion_orden_pago r"
                 " join nucleo.ejecucion_orden_pago e on e.id = r.ejecucion_id"
                 " where e.orden_id = :o order by r.registrado_en"), {"o": o},
        ).scalars())  # fmt: skip


def cuerpo_http(motor_migrate, e, o, **cambios) -> dict:
    return {
        "version": 1, "huella": huella(motor_migrate, o, 1).hex(),
        "numero_operacion": f"OP-{uuid4().hex[:10]}",
        "fecha_operacion": str(_hoy(motor_migrate, e)), "importe_real": str(REAL_DE_MENOS),
        "moneda": "ARS", "cuenta_real": CUENTA,
        "documento_id": str(comprobante(motor_migrate, e, orden=o)),
        "explicacion": "SINTETICO: me equivoqué",
    } | cambios  # fmt: skip


# Registrar lo que pasó ------------------------------------------------------------------------


def test_la_orden_no_queda_pagada_y_el_tipo_lo_deduce_la_base(motor_migrate, e):
    o = emitida(motor_migrate, e)
    d = con_diferencia(motor_migrate, e, o)
    f = fila_de(motor_migrate, d)
    assert (f["tipo"], f["importe_real"], f["resolucion"]) == ("importe", REAL_DE_MENOS, None)
    assert orden(motor_migrate, o)["estado"] == "en_verificacion"
    assert resultados(motor_migrate, o) == []
    assert "consorcio.orden_pago.registrar_diferencia_pago_manual" in acciones(motor_migrate, o)


@pytest.mark.parametrize(
    ("cambios", "tipo"),
    [
        ({"importe_real": REAL_DE_MAS}, "importe"),
        ({"importe_real": APROBADO, "moneda_p": "USD"}, "importe"),
        ({"importe_real": APROBADO, "cuenta": OTRA_CUENTA}, "cuenta"),
        ({"importe_real": REAL_DE_MENOS, "cuenta": OTRA_CUENTA}, "ambos"),
    ],
)
def test_que_fue_distinto(motor_migrate, e, cambios, tipo):
    o = emitida(motor_migrate, e)
    d = con_diferencia(motor_migrate, e, o, **cambios)
    assert fila_de(motor_migrate, d)["tipo"] == tipo


def test_con_importes_iguales_no_hay_diferencia(motor_migrate, e):
    o = emitida(motor_migrate, e)
    ver(motor_migrate, e, e.adm, o)
    niega(motor_migrate, e, e.adm, DIFERENCIA, "sin_diferencia",
          **datos(motor_migrate, e, o, importe_real=APROBADO))  # fmt: skip
    # Control: un centavo menos sí es una diferencia, con los mismos datos.
    llamar(motor_migrate, e, e.adm, DIFERENCIA,
           **datos(motor_migrate, e, o, importe_real=APROBADO - Decimal("0.01")))  # fmt: skip


def test_por_http_con_importes_iguales_da_422(motor_migrate, e):
    o = emitida(motor_migrate, e)
    ver(motor_migrate, e, e.adm, o)
    ruta = f"ordenes-pago/{o}/pago-manual/diferencia"
    r = post(e, e.adm, ruta, cuerpo_http(motor_migrate, e, o, importe_real=str(APROBADO)))
    assert codigo(r) == (422, "sin_diferencia"), r.text
    assert orden(motor_migrate, o)["estado"] == "en_ejecucion"
    r = post(e, e.adm, ruta, cuerpo_http(motor_migrate, e, o))
    assert r.status_code == 201, r.text  # control: con diferencia, pasa
    assert orden(motor_migrate, o)["estado"] == "en_verificacion"


def test_quien_no_vio_la_cuenta_no_prueba_cuentas(motor_migrate, e):
    o = emitida(motor_migrate, e)
    # Igual para una cuenta buena que para una mala: no dice cuál es la de la orden.
    for cuenta in (CUENTA, OTRA_CUENTA):
        niega(motor_migrate, e, e.adm, DIFERENCIA, "no_es_quien_ejecuto",
              **datos(motor_migrate, e, o, cuenta=cuenta))  # fmt: skip


def test_quien_aprobo_no_registra(motor_migrate, e):
    o = emitida(motor_migrate, e)
    niega(motor_migrate, e, e.mc1, DIFERENCIA, "42501", **datos(motor_migrate, e, o))


def test_el_gesto_sigue_siendo_obligatorio(motor_migrate, e):
    o = emitida(motor_migrate, e)
    ver(motor_migrate, e, e.adm, o)
    vieja = nueva_sesion(motor_migrate, e.adm, horas_atras=1, totp_min_atras=11)
    niega(motor_migrate, e, e.adm, DIFERENCIA, "requiere_segundo_factor_reciente", sesion=vieja,
          **datos(motor_migrate, e, o))  # fmt: skip


@pytest.mark.parametrize(
    "cambios",
    [{"numero": "X"}, {"fecha": None}, {"documento": None}],
    ids=["numero", "fecha", "comprobante"],
)
def test_la_evidencia_es_la_del_registro(motor_migrate, e, cambios):
    o = emitida(motor_migrate, e)
    ver(motor_migrate, e, e.adm, o)
    motivo = {"numero": "numero_operacion_invalido", "fecha": "fecha_operacion_invalida",
              "documento": "comprobante_invalido"}[next(iter(cambios))]  # fmt: skip
    niega(motor_migrate, e, e.adm, DIFERENCIA, motivo, **datos(motor_migrate, e, o, **cambios))


def test_el_numero_no_puede_ser_de_otra_orden(motor_migrate, e):
    a = emitida(motor_migrate, e)
    numero = f"OP-{uuid4().hex[:10]}"
    registrada(motor_migrate, e, a, numero=numero)
    b = emitida(motor_migrate, e)
    ver(motor_migrate, e, e.adm, b)
    niega(motor_migrate, e, e.adm, DIFERENCIA, "operacion_de_otra_orden",
          **datos(motor_migrate, e, b, numero=numero))  # fmt: skip


def test_la_explicacion_no_lleva_una_cuenta(motor_migrate, e):
    o = emitida(motor_migrate, e)
    ver(motor_migrate, e, e.adm, o)
    niega(motor_migrate, e, e.adm, DIFERENCIA, "motivo_con_cuenta",
          **datos(motor_migrate, e, o, explicacion=f"fue a {OTRA_CUENTA}"))  # fmt: skip
    r = post(e, e.adm, f"ordenes-pago/{o}/pago-manual/diferencia",
             cuerpo_http(motor_migrate, e, o, explicacion=f"fue a {OTRA_CUENTA}"))  # fmt: skip
    assert codigo(r) == (422, "motivo_con_cuenta"), r.text
    # Control: con los números cortos sí pasa.
    llamar(motor_migrate, e, e.adm, DIFERENCIA,
           **datos(motor_migrate, e, o, explicacion="fue a la terminada en 1234"))  # fmt: skip


def test_con_una_diferencia_no_entra_nada_mas(motor_migrate, e):
    o = emitida(motor_migrate, e)
    con_diferencia(motor_migrate, e, o)
    niega(motor_migrate, e, e.adm, REGISTRAR, "pago_ya_registrado",
          **evidencia(motor_migrate, e, o))  # fmt: skip
    niega(motor_migrate, e, e.adm, DIFERENCIA, "pago_ya_registrado", **datos(motor_migrate, e, o))
    niega(motor_migrate, e, e.adm, DECLARAR, "pago_ya_registrado", orden=o, version=1,
          motivo="SINTETICO")  # fmt: skip
    with (
        pytest.raises(DBAPIError) as error,
        transaccion(motor_migrate, persona_id=e.adm, consorcio_id=e.cons) as c,
    ):
        c.execute(text(VER), {
            "orden": o, "version": 1, "huella": huella(motor_migrate, o, 1), "actor": e.adm,
            "origen": "api", "sesion": e.sesion[e.adm],
        })  # fmt: skip
    assert error.value.orig.diag.message_hint == "pago_ya_registrado"


def test_desde_un_registro_observado(motor_migrate, e):
    """§8: «si la observación es porque se pagó mal», y sin abrir la cuenta otra vez."""
    o = emitida(motor_migrate, e)
    observada(motor_migrate, e, o, registrada(motor_migrate, e, o))
    assert ficha(e, e.adm, o)["puede_registrar"]
    d = llamar(motor_migrate, e, e.adm, DIFERENCIA, **datos(motor_migrate, e, o))
    f = ficha(e, e.adm, o)
    assert (f["situacion"], f["observacion_vigente"]) == ("con_diferencia", None)
    assert f["diferencia_vigente"]["id"] == str(d)


def test_el_cotejo_no_ofrece_el_registro_que_la_diferencia_reemplazo(motor_migrate, e):
    def en_el_cotejo() -> list[UUID]:
        with motor_migrate.connect() as c:
            return list(
                c.execute(text("select pago_id from nucleo.pagos_del_cotejo(:c)"), {"c": e.cons})
                .scalars()
            )  # fmt: skip

    o = emitida(motor_migrate, e)
    p = registrada(motor_migrate, e, o)
    observada(motor_migrate, e, o, p)
    assert p in en_el_cotejo()  # control: sin la diferencia, figura
    llamar(motor_migrate, e, e.adm, DIFERENCIA, **datos(motor_migrate, e, o))
    assert p not in en_el_cotejo()


# Cotejo: la transferencia de una diferencia ----------------------------------------------------


def linea_de(motor_migrate, d, **cambios) -> str:
    """El débito que dejó la transferencia en el extracto: el importe real, no el aprobado."""
    f = fila_de(motor_migrate, d)
    centavos = int(f["importe_real"] * 100)
    datos = {"fecha": f["fecha_operacion"], "importe": f"-{centavos // 100},{centavos % 100:02d}",
             "referencia": f["numero_operacion"]} | cambios  # fmt: skip
    return linea(**datos)


def cotejo_de(motor_migrate, d) -> dict | None:
    with motor_migrate.connect() as c:
        fila_cotejo = c.execute(
            text("select * from nucleo.cotejo_pago where diferencia_id = :d"), {"d": d}
        ).mappings().one_or_none()  # fmt: skip
    return None if fila_cotejo is None else dict(fila_cotejo)


def test_el_extracto_reconoce_la_transferencia_de_una_diferencia(motor_migrate, e):
    o = emitida(motor_migrate, e)
    d = con_diferencia(motor_migrate, e, o)
    cuerpo = extracto(linea_de(motor_migrate, d))
    # Sin resolver espera, como un pago sin conciliar: tampoco es un débito sin registrar.
    r = subio(e, e.mc1, cuerpo)
    assert (r["esperan_conciliar"], r["cotejados"], r["total_banco_no_aca"]) == (1, 0, 0)
    assert cotejo_de(motor_migrate, d) is None
    resuelta(motor_migrate, e, o, d, "pagada_por_lo_real")
    antes = orden(motor_migrate, o)
    r = subio(e, e.mc1, cuerpo)
    assert (r["cotejados"], r["total_banco_no_aca"], r["aca_no_banco"]) == (1, 0, [])
    atado = cotejo_de(motor_migrate, d)
    assert (atado["origen"], atado["pago_id"]) == ("extracto", None)
    assert atado["movimiento_id"] is not None and atado["cotejado_por"] == e.mc1
    assert orden(motor_migrate, o) == antes
    # Reimportar no la vuelve a contar ni la devuelve como débito sin registrar.
    r = subio(e, e.mc1, cuerpo)
    assert (r["cotejados"], r["ya_cotejados"], r["total_banco_no_aca"]) == (0, 1, 0)


def test_un_debito_por_lo_aprobado_no_es_el_de_la_diferencia(motor_migrate, e):
    """Control del anterior: el extracto sí ve un débito sin registrar, y compara con lo real."""
    o = emitida(motor_migrate, e)
    d = con_diferencia(motor_migrate, e, o)
    resuelta(motor_migrate, e, o, d, "pagada_por_lo_real")
    aprobado = f"-{IMPORTE // 100},{IMPORTE % 100:02d}"
    r = subio(e, e.mc1, extracto(linea_de(motor_migrate, d, importe=aprobado)))
    assert (r["cotejados"], r["total_banco_no_aca"]) == (0, 1)
    assert [x["pago_id"] for x in r["aca_no_banco"]] == [str(d)]
    assert r["aca_no_banco"][0]["importe_centavos"] == int(REAL_DE_MENOS * 100)
    assert cotejo_de(motor_migrate, d) is None


@pytest.mark.parametrize("recuperada", [False, True])
def test_lo_reclamado_igual_salio_del_banco(motor_migrate, e, recuperada):
    """También con la orden fallida: la plata volvió después, pero el débito está."""
    o = emitida(motor_migrate, e)
    d = con_diferencia(motor_migrate, e, o, cuenta=OTRA_CUENTA)
    resuelta(motor_migrate, e, o, d, "reclamo")
    if recuperada:
        llamar(motor_migrate, e, e.mc2, RECUPERAR, orden=o, diferencia=d)
        assert orden(motor_migrate, o)["estado"] == "fallida"
    r = subio(e, e.mc1, extracto(linea_de(motor_migrate, d)))
    assert (r["cotejados"], r["total_banco_no_aca"]) == (1, 0)


def test_a_mano_se_tilda_una_sola_vez_y_con_lo_real(motor_migrate, e):
    o = emitida(motor_migrate, e)
    d = con_diferencia(motor_migrate, e, o)
    assert pendientes(e, e.mc1)["elementos"] == []  # sin resolver, todavía no
    resuelta(motor_migrate, e, o, d, "pagada_por_lo_real")
    [pendiente] = pendientes(e, e.mc1)["elementos"]
    assert pendiente["pago_id"] == str(d)
    assert pendiente["importe_centavos"] == int(REAL_DE_MENOS * 100)
    assert pendiente["puede_cotejar"] is True
    assert tildar(e, e.mc1, d).json() == {"cotejados": 1}
    assert tildar(e, e.mc1, d).json() == {"cotejados": 0}
    assert cotejo_de(motor_migrate, d)["origen"] == "manual"
    assert pendientes(e, e.mc1)["elementos"] == []


def test_quien_registro_la_diferencia_no_la_coteja(motor_migrate, e):
    o = emitida(motor_migrate, e)
    con_ejecutar(motor_migrate, e, e.mc2)
    d = con_diferencia(motor_migrate, e, o, quien=e.mc2)
    resuelta(motor_migrate, e, o, d, "pagada_por_lo_real", quien=e.mc1)
    assert pendientes(e, e.mc2)["elementos"][0]["puede_cotejar"] is False
    assert codigo(tildar(e, e.mc2, d)) == (403, "no_es_quien_ejecuto")
    assert cotejo_de(motor_migrate, d) is None
    assert pendientes(e, e.mc1)["elementos"][0]["puede_cotejar"] is True  # control
    assert tildar(e, e.mc1, d).status_code == 200


def test_quien_preparo_no_coteja_la_diferencia(motor_migrate, motor_app, e):
    o = emitida(motor_migrate, e)
    d = con_diferencia(motor_migrate, e, o)
    resuelta(motor_migrate, e, o, d, "pagada_por_lo_real")
    otra = otra_cuenta_en_el_consejo(motor_migrate, motor_app, e, e.adm)
    assert tildar(e, otra, d).status_code == 403
    assert cotejo_de(motor_migrate, d) is None


def test_la_diferencia_de_otro_consorcio_no_se_coteja(motor_migrate, motor_app, e):
    o = emitida(motor_migrate, e)
    d = con_diferencia(motor_migrate, e, o)
    resuelta(motor_migrate, e, o, d, "pagada_por_lo_real")
    otra = escena_economica(motor_migrate, motor_app)
    assert pendientes(otra, otra.mc1)["elementos"] == []
    assert codigo(tildar(otra, otra.mc1, d)) == (404, "no_encontrado")
    assert cotejo_de(motor_migrate, d) is None
    assert [x["pago_id"] for x in pendientes(e, e.mc1)["elementos"]] == [str(d)]  # control


@pytest.mark.parametrize("con_pago", [True, False])
def test_un_cotejo_es_de_un_pago_o_de_una_diferencia(motor_migrate, e, con_pago):
    """Sin pasar por la función, con el dueño de la tabla: lo niega la base."""
    o = emitida(motor_migrate, e)
    p = registrada(motor_migrate, e, o)
    observada(motor_migrate, e, o, p)
    d = llamar(motor_migrate, e, e.adm, DIFERENCIA, **datos(motor_migrate, e, o))
    insertar = text(
        "insert into nucleo.cotejo_pago (consorcio_id, pago_id, diferencia_id, origen,"
        " cotejado_por, identidad_cotejo) select consorcio_id, :p, :d, 'manual', registrada_por,"
        " identidad_registro from nucleo.diferencia_pago_manual where id = :d0"
    )
    with pytest.raises(DBAPIError) as error, motor_migrate.begin() as c:
        c.execute(insertar, {"p": p if con_pago else None, "d": d if con_pago else None, "d0": d})
    assert error.value.orig.diag.constraint_name == "cotejo_de_un_pago_o_una_diferencia"
    with motor_migrate.begin() as c:  # control: la misma sentencia pasa con una sola cosa
        c.execute(insertar, {"p": None, "d": d, "d0": d})


def test_una_declaracion_anterior_queda_superada(motor_migrate, e):
    o = emitida(motor_migrate, e)
    ver(motor_migrate, e, e.adm, o)
    decl = declarada(motor_migrate, e, e.adm, o)
    assert ficha(e, e.mc2, o)["situacion"] == "no_pago_declarado"  # control
    llamar(motor_migrate, e, e.adm, DIFERENCIA, **datos(motor_migrate, e, o))
    f = ficha(e, e.mc2, o)
    assert (f["situacion"], f["declaracion_vigente"]) == ("con_diferencia", None)
    assert not f["puede_confirmar"]
    assert not f["ejecucion"]["declaraciones"][0]["confirmable"]
    niega(motor_migrate, e, e.mc2, CONFIRMAR, "pago_ya_registrado", orden=o, declaracion=decl)


def test_por_aclarar_no_pide_declarar_lo_que_ya_se_dijo(motor_migrate, motor_app, e):
    o = emitida(motor_migrate, e)
    con_diferencia(motor_migrate, e, o)
    dar_de_baja(motor_migrate, motor_app, e, e.adm)
    assert por_aclarar(motor_migrate, e, e.adm) == []


# Resolver -------------------------------------------------------------------------------------


def test_de_menos_queda_pagada_por_lo_real(motor_migrate, e):
    o = emitida(motor_migrate, e)
    d = con_diferencia(motor_migrate, e, o)
    assert resuelta(motor_migrate, e, o, d, "pagada_por_lo_real") == "pagada"
    assert orden(motor_migrate, o)["estado"] == "pagada"
    assert resultados(motor_migrate, o) == ["pagada"]
    x = ficha(e, e.mc2, o)["diferencia_vigente"]
    assert (x["falta_centavos"], x["sobra_centavos"]) == (2700, None)
    assert x["resolucion"] == "pagada_por_lo_real"


def test_de_mas_queda_pagada_y_dice_cuanto_sobro(motor_migrate, e):
    o = emitida(motor_migrate, e)
    d = con_diferencia(motor_migrate, e, o, importe_real=REAL_DE_MAS)
    assert resuelta(motor_migrate, e, o, d, "pagada_por_lo_real") == "pagada"
    x = ficha(e, e.adm, o)
    assert x["diferencia_vigente"]["sobra_centavos"] == 1250
    assert x["diferencia_vigente"]["falta_centavos"] is None
    assert x["paso"] is None and not x["puede_preparar_faltante"]


@pytest.mark.parametrize(
    "cambios",
    [
        {"importe_real": APROBADO, "cuenta": OTRA_CUENTA},
        {"importe_real": REAL_DE_MENOS, "cuenta": OTRA_CUENTA},
        {"moneda_p": "USD"},
    ],
    ids=["cuenta", "ambos", "moneda"],
)
def test_otra_cuenta_o_moneda_solo_se_reclama(motor_migrate, e, cambios):
    o = emitida(motor_migrate, e)
    d = con_diferencia(motor_migrate, e, o, **cambios)
    assert not ficha(e, e.mc2, o)["diferencia_vigente"]["se_puede_dar_por_pagada"]
    niega(motor_migrate, e, e.mc2, RESOLVER, "solo_reclamo", orden=o, diferencia=d,
          resolucion="pagada_por_lo_real")  # fmt: skip
    assert orden(motor_migrate, o)["estado"] == "en_verificacion"
    # Control: reclamar sí.
    assert resuelta(motor_migrate, e, o, d, "reclamo") == "en_verificacion"


def test_otra_cuenta_tampoco_se_da_por_pagada_en_la_tabla(motor_migrate, e):
    """Sin pasar por la función, con el dueño de la tabla: lo niega la base."""
    o = emitida(motor_migrate, e)
    d = con_diferencia(motor_migrate, e, o, importe_real=APROBADO, cuenta=OTRA_CUENTA)
    actualizar = text(
        "update nucleo.diferencia_pago_manual set resolucion = :r, resuelta_por = :p,"
        " identidad_resolvio = identidad_registro, resuelta_en = now() where id = :d"
    )
    with pytest.raises(DBAPIError) as error, motor_migrate.begin() as c:
        c.execute(actualizar, {"r": "pagada_por_lo_real", "p": e.mc2, "d": d})
    assert error.value.orig.diag.constraint_name == "diferencia_otra_cuenta_solo_reclamo"
    with motor_migrate.begin() as c:  # control: la misma sentencia pasa con `reclamo`
        c.execute(actualizar, {"r": "reclamo", "p": e.mc2, "d": d})


def test_por_http_la_otra_cuenta_da_409(motor_migrate, e):
    o = emitida(motor_migrate, e)
    d = con_diferencia(motor_migrate, e, o, cuenta=OTRA_CUENTA)
    r = post(e, e.mc2, f"ordenes-pago/{o}/pago-manual/diferencia/{d}/resolucion",
             {"resolucion": "pagada_por_lo_real"})  # fmt: skip
    assert codigo(r) == (409, "solo_reclamo"), r.text


def test_reclamo_y_recuperada_deja_fallida(motor_migrate, e):
    o = emitida(motor_migrate, e)
    d = con_diferencia(motor_migrate, e, o, cuenta=OTRA_CUENTA)
    ruta = f"ordenes-pago/{o}/pago-manual/diferencia/{d}"
    r = post(e, e.mc2, f"{ruta}/recuperada", {})
    assert codigo(r) == (409, "orden_no_admite"), r.text  # control: sin reclamo, no se recupera
    r = post(e, e.mc2, f"{ruta}/resolucion", {"resolucion": "reclamo"})
    assert (r.status_code, r.json()["estado"]) == (200, "en_verificacion"), r.text
    f = ficha(e, e.mc2, o)
    assert (f["situacion"], f["paso"], f["puede_recuperar"]) == (
        "reclamo", "resolver_diferencia", True,
    )  # fmt: skip
    assert not f["puede_resolver_diferencia"]
    assert paso(e, e.adm, o) == "espera_resolver"
    assert resultados(motor_migrate, o) == []
    r = post(e, e.mc2, f"{ruta}/recuperada", {})
    assert (r.status_code, r.json()["estado"]) == (200, "fallida"), r.text
    assert orden(motor_migrate, o)["estado"] == "fallida"
    assert resultados(motor_migrate, o) == ["fallida"]
    assert fila_de(motor_migrate, d)["recuperada_por"] == e.mc2
    x = ficha(e, e.adm, o)
    assert (x["situacion"], x["paso"]) == ("no_se_pago", "volver_a_pagar")
    assert x["diferencia_vigente"]["recuperada_en"] is not None


def test_no_se_resuelve_dos_veces(motor_migrate, e):
    o = emitida(motor_migrate, e)
    d = con_diferencia(motor_migrate, e, o)
    resuelta(motor_migrate, e, o, d, "reclamo")
    niega(motor_migrate, e, e.mc2, RESOLVER, "diferencia_ya_resuelta", orden=o, diferencia=d,
          resolucion="pagada_por_lo_real")  # fmt: skip
    r = post(e, e.mc2, f"ordenes-pago/{o}/pago-manual/diferencia/{d}/resolucion",
             {"resolucion": "reclamo"})  # fmt: skip
    assert codigo(r) == (409, "diferencia_ya_resuelta"), r.text


def test_separacion_de_quien_resuelve(motor_migrate, motor_app, e):
    """La de `conciliar_pago_manual`: quien vio la cuenta, también con otra cuenta suya."""
    o = emitida(motor_migrate, e)
    x = segundo_ejecutor(motor_migrate, motor_app, e)
    otra = otra_cuenta_en_el_consejo(motor_migrate, motor_app, e, x)
    d = con_diferencia(motor_migrate, e, o)
    segunda_apertura(motor_migrate, e, x, o)
    pedido = {"orden": o, "diferencia": d, "resolucion": "reclamo"}
    niega(motor_migrate, e, e.adm, RESOLVER, "42501", **pedido)  # registró y no aprueba
    niega(motor_migrate, e, x, RESOLVER, "no_es_quien_ejecuto", **pedido)
    niega(motor_migrate, e, otra, RESOLVER, "no_es_quien_ejecuto", **pedido)
    assert resuelta(motor_migrate, e, o, d, "reclamo") == "en_verificacion"  # control: otra persona


def test_quien_vio_la_cuenta_sin_registrar_no_ve_resolver_y_da_403(motor_migrate, motor_app, e):
    """§14: un miembro del Consejo que también ejecuta y solo vio la cuenta."""
    o = emitida(motor_migrate, e)
    x = segundo_ejecutor(motor_migrate, motor_app, e)
    d = con_diferencia(motor_migrate, e, o)
    segunda_apertura(motor_migrate, e, x, o)
    f = ficha(e, x, o)
    assert (f["paso"], f["puede_resolver_diferencia"]) == ("espera_resolver", False)
    assert ficha(e, e.mc1, o)["puede_resolver_diferencia"]  # control: mc1 no la vio
    r = post(e, x, f"ordenes-pago/{o}/pago-manual/diferencia/{d}/resolucion",
             {"resolucion": "reclamo"})  # fmt: skip
    assert r.status_code == 403, r.text
    assert orden(motor_migrate, o)["estado"] == "en_verificacion"
    assert fila_de(motor_migrate, d)["resolucion"] is None


def test_quien_preparo_no_resuelve(motor_migrate, motor_app, e):
    x = segundo_ejecutor(motor_migrate, motor_app, e)
    o = llamar(motor_migrate, e, x, PREPARAR)
    llamar(motor_migrate, e, x, ENVIAR, orden=o, version=1)
    if orden(motor_migrate, o)["estado"] == "en_revision":
        llamar(motor_migrate, e, e.mc1, APROBAR, orden=o, version=1,
               huella=huella(motor_migrate, o, 1))  # fmt: skip
    llamar(motor_migrate, e, e.adm, EMITIR, orden=o, version=1)
    d = con_diferencia(motor_migrate, e, o)
    niega(motor_migrate, e, x, RESOLVER, "no_es_quien_preparo", orden=o, diferencia=d,
          resolucion="reclamo")  # fmt: skip
    assert not ficha(e, x, o)["puede_resolver_diferencia"]
    assert ficha(e, e.mc2, o)["puede_resolver_diferencia"]  # control


def test_una_diferencia_de_otra_orden_es_404(motor_migrate, e):
    a = emitida(motor_migrate, e)
    b = emitida(motor_migrate, e)
    d = con_diferencia(motor_migrate, e, a)
    ver(motor_migrate, e, e.adm, b)
    llamar(motor_migrate, e, e.adm, DIFERENCIA, **datos(motor_migrate, e, b))
    niega(motor_migrate, e, e.mc2, RESOLVER, "no_encontrado", orden=b, diferencia=d,
          resolucion="reclamo")  # fmt: skip
    r = post(e, e.mc2, f"ordenes-pago/{b}/pago-manual/diferencia/{uuid4()}/resolucion",
             {"resolucion": "reclamo"})  # fmt: skip
    assert r.status_code == 404, r.text


def test_la_misma_clave_devuelve_lo_mismo(motor_migrate, e):
    o = emitida(motor_migrate, e)
    ver(motor_migrate, e, e.adm, o)
    clave = clave_idempotencia()
    ruta = f"ordenes-pago/{o}/pago-manual/diferencia"
    cuerpo = cuerpo_http(motor_migrate, e, o)
    primera = post(e, e.adm, ruta, cuerpo, clave)
    segunda = post(e, e.adm, ruta, cuerpo, clave)
    assert (primera.status_code, segunda.status_code) == (201, 201), segunda.text
    assert primera.json() == segunda.json()
    assert segunda.headers["Idempotent-Replayed"] == "true"


# Lo que se ve ---------------------------------------------------------------------------------


def test_bandeja_y_ficha_sin_la_cuenta_real(motor_migrate, e):
    o = emitida(motor_migrate, e)
    con_diferencia(motor_migrate, e, o, cuenta=OTRA_CUENTA)
    assert paso(e, e.mc2, o) == "resolver_diferencia"
    assert fila(e, e.mc2, o)["grupo"] == "para_resolver"
    x = fila(e, e.adm, o)
    assert (x["paso"], x["grupo"], x["le_toca_a"]) == ("espera_resolver", "esperando", "consejo")
    for quien in (e.adm, e.mc2):
        r = get(e, quien, f"pagos/{o}")
        assert OTRA_CUENTA not in r.text
        assert "cuenta_real" not in r.text
        f = r.json()
        assert f["situacion"] == "con_diferencia"
        assert f["diferencia_vigente"]["cuenta_ultimos_digitos"] == OTRA_CUENTA[-4:]
        assert not f["puede_pagar"] and not f["puede_registrar"]
        assert not f["puede_declarar"] and not f["puede_reabrir_cuenta"]


def test_el_consejo_ve_el_comprobante_de_la_diferencia(motor_migrate, e):
    o = emitida(motor_migrate, e)
    d = con_diferencia(motor_migrate, e, o)
    f = ficha(e, e.mc2, o)
    assert f["comprobante_actual"]["version_documento_id"] == str(
        fila_de(motor_migrate, d)["version_documento_id"]
    )


def test_sin_diferencia_la_ficha_no_habla_de_ella(motor_migrate, e):
    o = emitida(motor_migrate, e)
    registrada(motor_migrate, e, o)
    f = ficha(e, e.mc2, o)
    assert (f["situacion"], f["diferencia_vigente"]) == ("falta_conciliar", None)


def test_consulta_por_numero_y_con_lo_pagado(motor_migrate, e):
    o = emitida(motor_migrate, e)
    numero = f"DIF{uuid4().hex[:10]}".upper()
    d = con_diferencia(motor_migrate, e, o, numero=numero)
    assert ids(consulta(e, e.mc2, f"?busqueda={numero}")) == [str(o)]
    assert ids(consulta(e, e.mc2, "?busqueda=NOESTA123")) == []  # control
    [x] = consulta(e, e.mc2, f"?busqueda={numero}")["elementos"]
    assert (x["situacion"], x["numero_operacion"]) == ("con_diferencia", numero)
    assert x["importe_pagado_centavos"] is None
    resuelta(motor_migrate, e, o, d, "pagada_por_lo_real")
    [x] = consulta(e, e.mc2, f"?busqueda={numero}")["elementos"]
    assert (x["situacion"], x["importe_centavos"]) == ("pagada", IMPORTE)
    assert x["importe_pagado_centavos"] == IMPORTE - 2700


def test_lo_que_falta_se_ve_y_no_se_crea_solo(motor_migrate, e):
    o = emitida(motor_migrate, e)
    d = con_diferencia(motor_migrate, e, o)
    with motor_migrate.connect() as c:
        antes = c.execute(text("select count(*) from nucleo.orden_pago")).scalar_one()
    resuelta(motor_migrate, e, o, d, "pagada_por_lo_real")
    with motor_migrate.connect() as c:
        assert c.execute(text("select count(*) from nucleo.orden_pago")).scalar_one() == antes
    x = fila(e, e.adm, o)
    assert (x["paso"], x["grupo"]) == ("preparar_faltante", "para_preparar")
    assert ficha(e, e.adm, o)["puede_preparar_faltante"]
    # Control: al Consejo no le toca, y nadie puede nada más sobre una orden pagada.
    assert fila(e, e.mc2, o) is None
    f = ficha(e, e.mc2, o)
    assert f["paso"] is None
    assert not any(v for k, v in f.items() if k.startswith("puede_"))


def test_lo_que_falta_se_va_de_la_bandeja_a_los_dias(motor_migrate, e):
    o = emitida(motor_migrate, e)
    resuelta(motor_migrate, e, o, con_diferencia(motor_migrate, e, o), "pagada_por_lo_real")
    with motor_migrate.begin() as c:
        c.execute(text("alter table nucleo.orden_pago disable trigger user"))
        c.execute(text("update nucleo.orden_pago set actualizada_en = now() - interval '8 days'"
                       " where id = :o"), {"o": o})  # fmt: skip
        c.execute(text("alter table nucleo.orden_pago enable trigger user"))
    assert fila(e, e.adm, o) is None
    assert ficha(e, e.adm, o)["puede_preparar_faltante"]


# Inmutable y aislada --------------------------------------------------------------------------


def test_lo_registrado_no_cambia_y_la_resolucion_una_sola_vez(motor_migrate, e):
    o = emitida(motor_migrate, e)
    d = con_diferencia(motor_migrate, e, o)

    def niega_sql(sql: str, **p) -> None:
        with pytest.raises(DBAPIError) as error, motor_migrate.begin() as c:
            c.execute(text(sql), {"d": d} | p)
        assert error.value.orig.diag.message_hint == "historia_inmutable"

    niega_sql("update nucleo.diferencia_pago_manual set importe_real = 1 where id = :d")
    niega_sql("delete from nucleo.diferencia_pago_manual where id = :d")
    resuelta(motor_migrate, e, o, d, "reclamo")
    niega_sql("update nucleo.diferencia_pago_manual set resolucion = 'pagada_por_lo_real'"
              " where id = :d")  # fmt: skip
    with pytest.raises(DBAPIError) as error, motor_migrate.begin() as c:
        c.execute(text("truncate nucleo.diferencia_pago_manual cascade"))
    assert error.value.orig.diag.message_hint == "historia_inmutable"


def test_rls_forzada_y_otro_consorcio_no_la_ve(motor_migrate, motor_app, e):
    o = emitida(motor_migrate, e)
    d = con_diferencia(motor_migrate, e, o)
    with motor_migrate.connect() as c:
        rls = c.execute(text(
            "select relrowsecurity, relforcerowsecurity from pg_class"
            " where oid = 'nucleo.diferencia_pago_manual'::regclass"
        )).one()  # fmt: skip
        puede_leer = c.execute(text(
            "select has_table_privilege('cem_app', 'nucleo.diferencia_pago_manual', 'select')"
        )).scalar_one()  # fmt: skip
    assert tuple(rls) == (True, True)
    assert not puede_leer
    # Con el permiso dado solo para esta prueba: la política recorta por el consorcio del ámbito.
    with motor_migrate.begin() as c:
        c.execute(text("grant select on nucleo.diferencia_pago_manual to cem_app"))
    try:
        otro = uuid4()
        with transaccion(motor_app, persona_id=e.adm, consorcio_id=e.cons) as c:
            propias = c.execute(LEER).scalars().all()
        with transaccion(motor_app, persona_id=e.adm, consorcio_id=otro) as c:
            ajenas = c.execute(LEER).scalars().all()
    finally:
        with motor_migrate.begin() as c:
            c.execute(text("revoke select on nucleo.diferencia_pago_manual from cem_app"))
    assert d in propias  # control: la consulta ve la fila desde su consorcio
    assert ajenas == []


def test_la_lectura_de_otro_consorcio_no_la_trae(motor_migrate, motor_app, e):
    o = emitida(motor_migrate, e)
    con_diferencia(motor_migrate, e, o)
    otra = escena_economica(motor_migrate, motor_app)
    r = get(otra, otra.adm, f"pagos/{o}")
    assert r.status_code == 404, r.text
    r = post(otra, otra.mc2, f"ordenes-pago/{o}/pago-manual/diferencia/{uuid4()}/resolucion",
             {"resolucion": "reclamo"})  # fmt: skip
    assert r.status_code == 404, r.text


def test_las_funciones_las_ejecuta_solo_cem_app(motor_migrate):
    rutas = (
        "nucleo.registrar_diferencia_pago_manual(uuid, integer, bytea, text, date, numeric, text,"
        " text, uuid, text, uuid, text, uuid, uuid)",
        "nucleo.resolver_diferencia_pago_manual(uuid, uuid, text, uuid, text, uuid)",
        "nucleo.recuperar_diferencia_pago_manual(uuid, uuid, uuid, text, uuid)",
        "nucleo.diferencias_pago_manual_lectura(uuid, text, uuid, text, uuid)",
    )
    internas = (
        "nucleo.importe_pagado_ejecucion(uuid, uuid)",
        "nucleo.admite_resolver_diferencia(uuid, nucleo.orden_pago, uuid, uuid)",
        "nucleo.diferencia_para_resolver(uuid, nucleo.orden_pago, uuid, uuid, uuid)",
    )
    sql = text("select has_function_privilege(:r, cast(:f as regprocedure), 'execute')")
    with motor_migrate.connect() as c:
        for f in rutas:
            assert c.execute(sql, {"r": "cem_app", "f": f}).scalar_one(), f
            assert not c.execute(sql, {"r": "public", "f": f}).scalar_one(), f
        for f in internas:
            assert not c.execute(sql, {"r": "cem_app", "f": f}).scalar_one(), f


# Liquidación (§17) ----------------------------------------------------------------------------


def gasto(motor_migrate, e, o) -> dict:
    liq = borrador(motor_migrate, e)
    g = llamar(motor_migrate, e, e.adm, AGREGAR, liquidacion=liq, orden=o,
               grupo="expensas_comunes")  # fmt: skip
    with motor_migrate.connect() as c:
        return dict(c.execute(text("select * from nucleo.liquidacion_gasto where id = :g"),
                              {"g": g}).mappings().one())  # fmt: skip


def liquidable(e, o) -> dict | None:
    r = get(e, e.adm, "liquidaciones/ordenes-liquidables")
    assert r.status_code == 200, r.text
    return next((x for x in r.json()["elementos"] if x["orden_id"] == str(o)), None)


def test_la_liquidacion_toma_lo_real_de_menos(motor_migrate, e, esquema):
    o = emitida(motor_migrate, e)
    resuelta(motor_migrate, e, o, con_diferencia(motor_migrate, e, o), "pagada_por_lo_real")
    assert liquidable(e, o)["importe_centavos"] == IMPORTE - 2700
    assert gasto(motor_migrate, e, o)["importe_centavos"] == IMPORTE - 2700


def test_la_liquidacion_toma_lo_real_de_mas(motor_migrate, e, esquema):
    o = emitida(motor_migrate, e)
    d = con_diferencia(motor_migrate, e, o, importe_real=REAL_DE_MAS)
    resuelta(motor_migrate, e, o, d, "pagada_por_lo_real")
    assert gasto(motor_migrate, e, o)["importe_centavos"] == IMPORTE + 1250


def test_la_simulacion_reparte_lo_real(motor_migrate, e, esquema):
    o = emitida(motor_migrate, e)
    resuelta(motor_migrate, e, o, con_diferencia(motor_migrate, e, o), "pagada_por_lo_real")
    liq = gasto(motor_migrate, e, o)["liquidacion_id"]
    detalle = get(e, e.adm, f"liquidaciones/{liq}").json()
    assert detalle["simulacion"]["total_centavos"] == IMPORTE - 2700


def test_la_fecha_del_gasto_es_la_de_la_operacion(motor_migrate, e, esquema):
    """Sin esto, `fecha_pago_ejecucion` era nula y la orden nunca entraba en un período."""
    o = emitida(motor_migrate, e)
    d = con_diferencia(motor_migrate, e, o)
    resuelta(motor_migrate, e, o, d, "pagada_por_lo_real")
    assert liquidable(e, o)["pagada_el"] == str(fila_de(motor_migrate, d)["fecha_operacion"])


def test_sin_diferencia_la_liquidacion_no_cambia(motor_migrate, e, esquema):
    """Control: una orden conciliada sigue tomando el importe de la ejecución."""
    o = pagada(motor_migrate, e)
    assert liquidable(e, o)["importe_centavos"] == IMPORTE
    assert gasto(motor_migrate, e, o)["importe_centavos"] == IMPORTE


def test_lo_reclamado_no_se_liquida(motor_migrate, e, esquema):
    o = emitida(motor_migrate, e)
    resuelta(motor_migrate, e, o, con_diferencia(motor_migrate, e, o), "reclamo")
    assert liquidable(e, o) is None
    niega(motor_migrate, e, e.adm, AGREGAR, "orden_no_pagada",
          liquidacion=borrador(motor_migrate, e), orden=o, grupo="expensas_comunes")  # fmt: skip


def test_centavos_exactos_con_el_tope_del_tipo(motor_migrate):
    with motor_migrate.connect() as c:
        assert c.execute(
            text("select nucleo.centavos_de(9999999999999999.99::numeric(18,2))")
        ).scalar_one() == 999999999999999999  # fmt: skip


# Volver a pagar (§9) --------------------------------------------------------------------------


def fallida(motor_migrate, e) -> UUID:
    o = emitida(motor_migrate, e)
    ver(motor_migrate, e, e.adm, o)
    d = declarada(motor_migrate, e, e.adm, o)
    llamar(motor_migrate, e, e.mc2, CONFIRMAR, orden=o, declaracion=d)
    assert orden(motor_migrate, o)["estado"] == "fallida"
    return o


def test_desde_fallida_la_version_nueva_se_acepta_sin_cambios(motor_migrate, e):
    o = fallida(motor_migrate, e)
    assert llamar(motor_migrate, e, e.adm, MODIFICAR, orden=o, version=1) == 2
    x = orden(motor_migrate, o)
    assert (x["estado"], x["version_vigente"], x["aprobada_en"]) == ("en_revision", 2, None)
    uno, dos = versiones(motor_migrate, o)
    assert (uno["importe_centavos"], uno["concepto"]) == (dos["importe_centavos"], dos["concepto"])
    assert uno["huella"] != dos["huella"]


@pytest.mark.parametrize("hasta", ["borrador", "en_revision", "aprobada"])
def test_desde_otro_estado_sigue_sin_cambios(motor_migrate, e, hasta):
    o = {"borrador": preparada, "en_revision": enviada, "aprobada": aprobada}[hasta](
        motor_migrate, e
    )
    assert orden(motor_migrate, o)["estado"] == hasta  # control: la escena es la que dice
    niega(motor_migrate, e, e.adm, MODIFICAR, "sin_cambios", orden=o, version=1)


def test_por_http_volver_a_pagar_y_pasa_otra_vez_por_aprobacion(motor_migrate, e):
    o = fallida(motor_migrate, e)
    assert paso(e, e.adm, o) == "volver_a_pagar"
    v = ficha(e, e.adm, o)["versiones"][0]
    r = post(e, e.adm, f"ordenes-pago/{o}/versiones", {
        "version_esperada": 1, "importe_centavos": v["importe_centavos"], "moneda": v["moneda"],
        "concepto": v["concepto"], "proveedor_id": v["proveedor_id"],
        "comprobante": v["comprobante"], "usa_fondo_reserva": v["usa_fondo_reserva"],
        "contrato_id": v["contrato_id"],
    })  # fmt: skip
    assert (r.status_code, r.json()["version"]) == (200, 2), r.text
    assert paso(e, e.mc1, o) == "aprobar"
    assert paso(e, e.adm, o) == "espera_aprobar"


def test_despues_de_recuperar_tambien_se_vuelve_a_pagar(motor_migrate, e):
    o = emitida(motor_migrate, e)
    d = con_diferencia(motor_migrate, e, o)
    resuelta(motor_migrate, e, o, d, "reclamo")
    llamar(motor_migrate, e, e.mc2, RECUPERAR, orden=o, diferencia=d)
    assert llamar(motor_migrate, e, e.adm, MODIFICAR, orden=o, version=1) == 2
    assert ficha(e, e.adm, o)["diferencia_vigente"] is None  # es de la ejecución anterior


def test_una_orden_pagada_no_se_vuelve_a_pagar(motor_migrate, e):
    o = emitida(motor_migrate, e)
    resuelta(motor_migrate, e, o, con_diferencia(motor_migrate, e, o), "pagada_por_lo_real")
    niega(motor_migrate, e, e.adm, MODIFICAR, "estado_invalido", orden=o, version=1)


# Migración ------------------------------------------------------------------------------------


def test_0075_ida_y_vuelta(base_aparte):
    cfg, motor = base_aparte
    firmas = (
        "nucleo.exigir_sin_pago_vigente(uuid)",
        "nucleo.fecha_pago_ejecucion(uuid, uuid)",
        "nucleo.ordenes_liquidables_lectura(uuid, text, uuid)",
        "nucleo.acciones_de_ordenes_lectura(uuid, uuid, text, uuid)",
        "nucleo.hechos_de_pago_manual(uuid, nucleo.orden_pago)",
    )

    def definiciones() -> dict:
        with motor.connect() as c:
            return {
                f: c.execute(
                    text("select pg_get_functiondef(cast(:f as regprocedure))"), {"f": f}
                ).scalar_one()
                for f in firmas
            }

    anterior = "0074"
    command.upgrade(cfg, anterior)
    antes = definiciones()
    assert not any("diferencia" in d for d in antes.values())
    command.upgrade(cfg, "0075")
    assert all("diferencia" in d or "importe_pagado" in d for d in definiciones().values())
    command.downgrade(cfg, anterior)
    assert definiciones() == antes
    with motor.connect() as c:
        assert c.execute(
            text("select to_regclass('nucleo.diferencia_pago_manual')")
        ).scalar_one() is None  # fmt: skip
    command.upgrade(cfg, "0075")  # control: vuelve a subir limpio


def test_0075_no_baja_con_diferencias(motor_migrate, e):
    ruta = Path(__file__).resolve().parents[1] / "migrations" / "versions"
    spec = importlib.util.spec_from_file_location("m0075", ruta / "0075_pague_otra_cosa.py")
    migracion = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migracion)
    o = emitida(motor_migrate, e)
    con_diferencia(motor_migrate, e, o)
    with pytest.raises(DBAPIError) as error, motor_migrate.begin() as c:
        c.execute(text(migracion.GUARDA_DOWNGRADE))
    assert error.value.orig.diag.message_hint == "downgrade_con_datos"


def test_el_importe_de_la_escena_es_el_esperado():
    """Control de lo que suponen estas pruebas."""
    assert ESENCIALES["importe"] == IMPORTE > 2700
