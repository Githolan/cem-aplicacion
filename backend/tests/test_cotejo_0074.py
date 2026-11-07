"""Cotejar con el banco contra PostgreSQL real (`0074`). Datos SINTETICOS / PRUEBA / NO REAL: las
cuentas no son de un banco (prefijo 999), los extractos los arma esta prueba y no se mueve dinero.

Diseño: `Rediseno_Circuito_Pagos_2026-11-06.md`, §10 y §14. La escena es la de
`test_pago_manual_0055`: la administración prepara y ejecuta, mc1 aprueba y mc2 concilia. Por eso
coteja mc1 o mc2, y la administración no.
"""

import importlib.util
from datetime import date, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.config import Config
from app.contexto import Identidad, obtener_identidad
from app.db import transaccion
from app.main import crear_app
from tests.conftest import clave_idempotencia, url_rol
from tests.test_contexto_mandatos import IP
from tests.test_orden_pago_neutra import (
    Escena,
    escena_economica,
    nueva_sesion,
    otra_cuenta_en_el_consejo,
)
from tests.test_pago_manual_0055 import (
    CONCILIAR,
    CUENTA,
    IMPORTE,
    con_ejecutar,
    emitida,
    llamar,
    orden,
    registrada,
    segunda_apertura,
)
from tests.test_rutas_egresos import cliente, codigo, get, post, url
from tests.test_vinculo_0014 import base_aparte  # noqa: F401  (es un fixture)

pytestmark = pytest.mark.db
# `base_aparte` es un fixture importado: el parámetro con su nombre no lo redefine.
# ruff: noqa: F811

TABLAS = (
    "nucleo.cotejo_pago",
    "nucleo.importacion_extracto",
    "nucleo.movimiento_extracto",
    "nucleo.columnas_extracto",
)
COLUMNAS = {"fecha": "Fecha", "importe": "Importe", "numero_operacion": "Referencia",
            "signo": "con_signo"}  # fmt: skip
ENCABEZADO = "Fecha;Descripción;Importe;Referencia"
PESOS = f"{IMPORTE // 100},{IMPORTE % 100:02d}"


@pytest.fixture
def e(motor_migrate, motor_app) -> Escena:
    return escena_economica(motor_migrate, motor_app)


def pagado(motor_migrate, e, **registro) -> tuple[UUID, UUID]:
    """Una orden pagada: el pago conforme que falta cotejar."""
    o = emitida(motor_migrate, e)
    p = registrada(motor_migrate, e, o, **registro)
    llamar(motor_migrate, e, e.mc2, CONCILIAR, orden=o, pago=p, resultado="conforme", motivo=None)
    return o, p


def dato_pago(motor_migrate, p) -> dict:
    with motor_migrate.connect() as c:
        return dict(c.execute(
            text("select numero_operacion, fecha_operacion, importe_centavos from"
                 " nucleo.pago_manual where id = :p"), {"p": p},
        ).mappings().one())  # fmt: skip


def linea(
    fecha: date, importe: str, referencia: str = "", descripcion: str = "Transferencia"
) -> str:
    return f"{fecha.strftime('%d/%m/%Y')};{descripcion};{importe};{referencia}"


def extracto(*lineas: str) -> bytes:
    return "\n".join((ENCABEZADO, *lineas)).encode()


def linea_del_pago(motor_migrate, p, **cambios) -> str:
    d = dato_pago(motor_migrate, p)
    datos = {"fecha": d["fecha_operacion"], "importe": f"-{PESOS}",
             "referencia": d["numero_operacion"]} | cambios  # fmt: skip
    return linea(**datos)


def subir(e, quien, cuerpo: bytes, columnas: dict | None = COLUMNAS, clave=None):
    with cliente(quien, e.sesion[quien]) as c:
        return c.post(
            url(e, "pagos/cotejo/extracto"), content=cuerpo, params=columnas or {},
            headers=(clave or clave_idempotencia()) | {"Content-Type": "text/csv"},
        )  # fmt: skip


def subio(e, quien, cuerpo: bytes, columnas: dict | None = COLUMNAS) -> dict:
    r = subir(e, quien, cuerpo, columnas)
    assert r.status_code == 200, r.json()
    assert CUENTA not in r.text
    return r.json()


def vista(e, quien, cuerpo: bytes):
    with cliente(quien, e.sesion[quien]) as c:
        return c.post(url(e, "pagos/cotejo/columnas"), content=cuerpo,
                      headers={"Content-Type": "text/csv"})  # fmt: skip


def pendientes(e, quien, parametros: str = "") -> dict:
    r = get(e, quien, f"pagos/cotejo{parametros}")
    assert r.status_code == 200, r.json()
    assert CUENTA not in r.text
    return r.json()


def ids(respuesta: dict) -> list[str]:
    return [x["pago_id"] for x in respuesta["elementos"]]


def cuantos(motor_migrate, tabla: str, cons: UUID) -> int:
    with motor_migrate.connect() as c:
        return c.execute(
            text(f"select count(*) from {tabla} where consorcio_id = :c"), {"c": cons}
        ).scalar_one()


def cotejo(motor_migrate, p) -> dict | None:
    with motor_migrate.connect() as c:
        fila = c.execute(text("select * from nucleo.cotejo_pago where pago_id = :p"),
                         {"p": p}).mappings().one_or_none()  # fmt: skip
    return None if fila is None else dict(fila)


def tildar(e, quien, *pagos):
    return post(e, quien, "pagos/cotejo", {"pagos": [str(p) for p in pagos]})


# A mano ----------------------------------------------------------------------------------------


def test_lo_conciliado_aparece_y_lo_que_falta_conciliar_no(motor_migrate, e):
    o, p = pagado(motor_migrate, e)
    sin_conciliar = registrada(motor_migrate, e, emitida(motor_migrate, e))
    respuesta = pendientes(e, e.mc1)
    assert ids(respuesta) == [str(p)]
    assert str(sin_conciliar) not in ids(respuesta)
    assert respuesta["elementos"][0]["orden_id"] == str(o)
    assert respuesta["elementos"][0]["puede_cotejar"] is True


def test_tildar_guarda_quien_y_cuando_y_no_cambia_la_orden(motor_migrate, e):
    o, p = pagado(motor_migrate, e)
    antes = orden(motor_migrate, o)
    r = tildar(e, e.mc1, p)
    assert r.status_code == 200 and r.json() == {"cotejados": 1}
    fila = cotejo(motor_migrate, p)
    assert fila["cotejado_por"] == e.mc1 and fila["cotejado_en"] is not None
    assert fila["origen"] == "manual" and fila["movimiento_id"] is None
    assert orden(motor_migrate, o) == antes
    assert pendientes(e, e.mc1)["elementos"] == []


def test_tildar_dos_veces_deja_un_solo_cotejo(motor_migrate, e):
    _, p = pagado(motor_migrate, e)
    assert tildar(e, e.mc1, p).json() == {"cotejados": 1}
    assert tildar(e, e.mc2, p).json() == {"cotejados": 0}
    assert cotejo(motor_migrate, p)["cotejado_por"] == e.mc1
    assert cuantos(motor_migrate, "nucleo.cotejo_pago", e.cons) == 1


def test_lo_que_falta_conciliar_no_se_tilda(motor_migrate, e):
    p = registrada(motor_migrate, e, emitida(motor_migrate, e))
    assert codigo(tildar(e, e.mc1, p)) == (409, "pago_sin_conciliar")
    assert cotejo(motor_migrate, p) is None


def test_un_pago_inventado_no_se_tilda_y_no_deja_nada_a_medias(motor_migrate, e):
    _, p = pagado(motor_migrate, e)
    assert codigo(tildar(e, e.mc1, p, uuid4())) == (404, "no_encontrado")
    assert cotejo(motor_migrate, p) is None
    assert tildar(e, e.mc1, p).status_code == 200  # control


def test_la_pagina_sigue_igual_aunque_se_coteje_en_el_medio(motor_migrate, e):
    pagos = [pagado(motor_migrate, e)[1] for _ in range(3)]
    primera = pendientes(e, e.mc1, "?limite=1")
    assert ids(primera) == [str(pagos[0])] and primera["siguiente"] == str(pagos[0])
    assert tildar(e, e.mc1, pagos[0]).status_code == 200
    segunda = pendientes(e, e.mc1, f"?limite=1&despues={primera['siguiente']}")
    assert ids(segunda) == [str(pagos[1])]
    tercera = pendientes(e, e.mc1, f"?limite=1&despues={segunda['siguiente']}")
    assert ids(tercera) == [str(pagos[2])] and tercera["siguiente"] is None


# Quién -----------------------------------------------------------------------------------------


def test_quien_vio_la_cuenta_no_coteja_y_otro_consejero_si(motor_migrate, e):
    o = emitida(motor_migrate, e)
    con_ejecutar(motor_migrate, e, e.mc2)
    p = registrada(motor_migrate, e, o)
    segunda_apertura(motor_migrate, e, e.mc2, o)
    llamar(motor_migrate, e, e.mc1, CONCILIAR, orden=o, pago=p, resultado="conforme", motivo=None)
    assert pendientes(e, e.mc2)["elementos"][0]["puede_cotejar"] is False
    assert codigo(tildar(e, e.mc2, p)) == (403, "no_es_quien_ejecuto")
    assert cotejo(motor_migrate, p) is None
    assert pendientes(e, e.mc1)["elementos"][0]["puede_cotejar"] is True  # control
    assert tildar(e, e.mc1, p).status_code == 200


def test_quien_preparo_con_otra_cuenta_del_consejo_no_coteja(motor_migrate, motor_app, e):
    _, p = pagado(motor_migrate, e)
    otra = otra_cuenta_en_el_consejo(motor_migrate, motor_app, e, e.adm)
    assert pendientes(e, otra)["elementos"][0]["puede_cotejar"] is False
    assert tildar(e, otra, p).status_code == 403
    assert cotejo(motor_migrate, p) is None


def test_la_administracion_no_llega_al_cotejo(motor_migrate, e):
    _, p = pagado(motor_migrate, e)
    assert codigo(get(e, e.adm, "pagos/cotejo")) == (403, "sin_permiso")
    assert codigo(tildar(e, e.adm, p)) == (403, "sin_permiso")
    assert codigo(subir(e, e.adm, extracto(linea(date.today(), "-1,00")))) == (403, "sin_permiso")
    assert codigo(vista(e, e.adm, extracto(linea(date.today(), "-1,00")))) == (403, "sin_permiso")
    assert get(e, e.mc1, "pagos/cotejo").status_code == 200  # control


def test_escribir_pide_el_gesto_y_leer_no(motor_migrate, e):
    _, p = pagado(motor_migrate, e)
    e.sesion[e.mc1] = nueva_sesion(motor_migrate, e.mc1, totp_min_atras=None)
    assert get(e, e.mc1, "pagos/cotejo").status_code == 200
    assert codigo(tildar(e, e.mc1, p)) == (403, "segundo_factor_requerido")
    assert codigo(subir(e, e.mc1, extracto(linea(date.today(), "-1,00")))) == (
        403, "segundo_factor_requerido",
    )  # fmt: skip
    assert cotejo(motor_migrate, p) is None


# Con el extracto -------------------------------------------------------------------------------


def test_lo_que_coincide_por_numero_queda_cotejado_solo(motor_migrate, e):
    o, p = pagado(motor_migrate, e)
    antes = orden(motor_migrate, o)
    resultado = subio(e, e.mc1, extracto(linea_del_pago(motor_migrate, p)))
    assert resultado["cotejados"] == 1 and resultado["movimientos_nuevos"] == 1
    assert resultado["aca_no_banco"] == [] and resultado["banco_no_aca"] == []
    fila = cotejo(motor_migrate, p)
    assert fila["origen"] == "extracto" and fila["cotejado_por"] == e.mc1
    assert fila["movimiento_id"] is not None
    assert orden(motor_migrate, o) == antes
    assert pendientes(e, e.mc1)["elementos"] == []


def test_sin_numero_compara_por_importe_y_fecha(motor_migrate, e):
    _, p = pagado(motor_migrate, e)
    resultado = subio(e, e.mc1, extracto(linea_del_pago(motor_migrate, p, referencia="")))
    assert resultado["cotejados"] == 1
    assert cotejo(motor_migrate, p)["origen"] == "extracto"


def test_sin_la_columna_del_numero_tambien_compara_por_importe_y_fecha(motor_migrate, e):
    _, p = pagado(motor_migrate, e)
    columnas = {k: v for k, v in COLUMNAS.items() if k != "numero_operacion"}
    resultado = subio(e, e.mc1, extracto(linea_del_pago(motor_migrate, p)), columnas)
    assert resultado["cotejados"] == 1


def test_el_mismo_numero_con_otro_importe_no_coincide(motor_migrate, e):
    _, p = pagado(motor_migrate, e)
    resultado = subio(e, e.mc1, extracto(linea_del_pago(motor_migrate, p, importe="-1,00")))
    assert resultado["cotejados"] == 0 and cotejo(motor_migrate, p) is None
    assert [x["pago_id"] for x in resultado["aca_no_banco"]] == [str(p)]
    assert resultado["banco_no_aca"][0]["importe_centavos"] == 100


def test_dos_pagos_iguales_y_una_linea_sin_numero_se_miran(motor_migrate, e):
    pagos = [pagado(motor_migrate, e)[1] for _ in range(2)]
    linea_sola = linea_del_pago(motor_migrate, pagos[0], referencia="")
    resultado = subio(e, e.mc1, extracto(linea_sola))
    assert resultado["cotejados"] == 0
    assert sorted(x["pago_id"] for x in resultado["aca_no_banco"]) == sorted(map(str, pagos))
    assert resultado["total_banco_no_aca"] == 1
    # Control: con las dos líneas no hay duda.
    otra = subio(e, e.mc1, extracto(linea_sola, linea_sola))
    assert otra["cotejados"] == 2 and otra["total_aca_no_banco"] == 0


def test_el_resultado_solo_trae_lo_que_hay_que_mirar_y_del_periodo(motor_migrate, e):
    _, coincide = pagado(motor_migrate, e)
    _, falta_en_el_banco = pagado(motor_migrate, e)
    fecha = dato_pago(motor_migrate, coincide)["fecha_operacion"]
    resultado = subio(e, e.mc1, extracto(
        linea_del_pago(motor_migrate, coincide),
        linea(fecha, "-12.400,00", "0048213999"),
    ))  # fmt: skip
    assert resultado["cotejados"] == 1
    assert [x["pago_id"] for x in resultado["aca_no_banco"]] == [str(falta_en_el_banco)]
    assert resultado["banco_no_aca"] == [{
        "fecha": fecha.isoformat(), "importe_centavos": 1_240_000,
        "numero_operacion": "0048213999",
    }]  # fmt: skip
    # Control: un extracto de otro período no dice nada de ese pago.
    viejo = fecha - timedelta(days=40)
    otro = subio(e, e.mc1, extracto(linea(viejo, "-5,00", "0048210000")))
    assert otro["aca_no_banco"] == [] and otro["total_banco_no_aca"] == 1


def test_lo_que_entro_a_la_cuenta_no_es_un_debito_sin_registrar(motor_migrate, e):
    _, p = pagado(motor_migrate, e)
    fecha = dato_pago(motor_migrate, p)["fecha_operacion"]
    resultado = subio(e, e.mc1, extracto(
        linea_del_pago(motor_migrate, p), linea(fecha, "10.000,00", "0048213998"),
    ))  # fmt: skip
    assert resultado["total_banco_no_aca"] == 0
    assert cuantos(motor_migrate, "nucleo.movimiento_extracto", e.cons) == 1


def test_lo_registrado_sin_conciliar_no_es_un_debito_sin_registrar_ni_se_coteja(motor_migrate, e):
    o = emitida(motor_migrate, e)
    p = registrada(motor_migrate, e, o)
    resultado = subio(e, e.mc1, extracto(linea_del_pago(motor_migrate, p)))
    assert resultado["esperan_conciliar"] == 1 and resultado["cotejados"] == 0
    assert resultado["banco_no_aca"] == [] and resultado["aca_no_banco"] == []
    assert cotejo(motor_migrate, p) is None


def test_quien_vio_la_cuenta_sube_el_extracto_y_su_pago_queda_para_otro(motor_migrate, e):
    o = emitida(motor_migrate, e)
    con_ejecutar(motor_migrate, e, e.mc2)
    p = registrada(motor_migrate, e, o)
    segunda_apertura(motor_migrate, e, e.mc2, o)
    llamar(motor_migrate, e, e.mc1, CONCILIAR, orden=o, pago=p, resultado="conforme", motivo=None)
    cuerpo = extracto(linea_del_pago(motor_migrate, p))
    resultado = subio(e, e.mc2, cuerpo)
    assert resultado["para_otra_persona"] == 1 and resultado["cotejados"] == 0
    assert cotejo(motor_migrate, p) is None
    # Control: otro consejero sube lo mismo y queda cotejado.
    assert subio(e, e.mc1, cuerpo)["cotejados"] == 1


def test_la_base_no_acepta_un_par_que_no_coincide(motor_migrate, motor_app, e):
    _, p = pagado(motor_migrate, e)
    fecha = dato_pago(motor_migrate, p)["fecha_operacion"]
    subio(e, e.mc1, extracto(linea(fecha, "-1,00", "0048213999")))
    with motor_migrate.connect() as c:
        m = c.execute(text("select id from nucleo.movimiento_extracto where consorcio_id = :c"),
                      {"c": e.cons}).scalar_one()  # fmt: skip
    with pytest.raises(DBAPIError) as error:
        with transaccion(motor_app, persona_id=e.mc1, consorcio_id=e.cons) as c:
            c.execute(
                text("select nucleo.cotejar_por_extracto(cast(:p as jsonb), :a, 'api', :s)"),
                {"p": f'[{{"movimiento": "{m}", "pago": "{p}"}}]', "a": e.mc1,
                 "s": e.sesion[e.mc1]},
            )  # fmt: skip
    assert error.value.orig.diag.message_hint == "movimiento_no_coincide"
    assert cotejo(motor_migrate, p) is None


# Reimportar ------------------------------------------------------------------------------------


def test_reimportar_el_mismo_extracto_no_duplica(motor_migrate, e):
    _, p = pagado(motor_migrate, e)
    _, otro = pagado(motor_migrate, e)
    fecha = dato_pago(motor_migrate, p)["fecha_operacion"]
    cuerpo = extracto(linea_del_pago(motor_migrate, p), linea(fecha, "-12,00", "0048213999"))
    primero = subio(e, e.mc1, cuerpo)
    assert primero["movimientos_nuevos"] == 2 and primero["cotejados"] == 1
    tablas = {t: cuantos(motor_migrate, t, e.cons) for t in TABLAS}
    assert tablas == {"nucleo.cotejo_pago": 1, "nucleo.importacion_extracto": 1,
                      "nucleo.movimiento_extracto": 2, "nucleo.columnas_extracto": 1}  # fmt: skip

    segundo = subio(e, e.mc2, cuerpo)
    assert {t: cuantos(motor_migrate, t, e.cons) for t in TABLAS} == tablas
    assert segundo["movimientos_nuevos"] == 0 and segundo["movimientos_repetidos"] == 2
    assert segundo["cotejados"] == 0 and segundo["ya_cotejados"] == 1
    # Lo que hay que mirar sigue igual.
    assert [x["pago_id"] for x in segundo["aca_no_banco"]] == [str(otro)]
    assert segundo["total_banco_no_aca"] == 1
    assert cotejo(motor_migrate, p)["cotejado_por"] == e.mc1


def test_el_mismo_extracto_con_otra_codificacion_y_otro_separador_no_duplica(motor_migrate, e):
    _, p = pagado(motor_migrate, e)
    subio(e, e.mc1, extracto(linea_del_pago(motor_migrate, p, descripcion="Débito")))
    d = dato_pago(motor_migrate, p)
    otro = "\r\n".join((
        ENCABEZADO.replace(";", "\t"),
        f"{d['fecha_operacion'].isoformat()}\tDébito\t-{PESOS}\t{d['numero_operacion']}",
    )).encode("cp1252")  # fmt: skip
    resultado = subio(e, e.mc2, otro)
    assert resultado["movimientos_nuevos"] == 0 and resultado["ya_cotejados"] == 1
    assert cuantos(motor_migrate, "nucleo.movimiento_extracto", e.cons) == 1


def test_dos_debitos_iguales_en_un_dia_son_dos_y_reimportar_sigue_siendo_dos(motor_migrate, e):
    hoy = date.today()
    igual = linea(hoy, "-500,00")
    assert subio(e, e.mc1, extracto(igual, igual))["movimientos_nuevos"] == 2
    assert subio(e, e.mc1, extracto(igual, igual))["movimientos_nuevos"] == 0
    assert cuantos(motor_migrate, "nucleo.movimiento_extracto", e.cons) == 2


def test_un_extracto_que_se_superpone_con_otro_solo_suma_lo_nuevo(motor_migrate, e):
    hoy = date.today()
    a, b = linea(hoy, "-500,00", "0048210001"), linea(hoy, "-600,00", "0048210002")
    subio(e, e.mc1, extracto(a))
    resultado = subio(e, e.mc1, extracto(a, b))
    assert resultado["movimientos_nuevos"] == 1 and resultado["movimientos_repetidos"] == 1
    assert cuantos(motor_migrate, "nucleo.importacion_extracto", e.cons) == 2


def test_reimportar_coteja_lo_que_mientras_tanto_se_concilio(motor_migrate, e):
    o = emitida(motor_migrate, e)
    p = registrada(motor_migrate, e, o)
    cuerpo = extracto(linea_del_pago(motor_migrate, p))
    assert subio(e, e.mc1, cuerpo)["esperan_conciliar"] == 1
    llamar(motor_migrate, e, e.mc2, CONCILIAR, orden=o, pago=p, resultado="conforme", motivo=None)
    resultado = subio(e, e.mc1, cuerpo)
    assert resultado["cotejados"] == 1 and resultado["movimientos_nuevos"] == 0


def test_un_reintento_con_la_misma_clave_repite_la_respuesta(motor_migrate, e):
    _, p = pagado(motor_migrate, e)
    cuerpo = extracto(linea_del_pago(motor_migrate, p))
    clave = clave_idempotencia()
    primero = subir(e, e.mc1, cuerpo, clave=clave)
    segundo = subir(e, e.mc1, cuerpo, clave=clave)
    assert primero.status_code == 200 and segundo.json() == primero.json()
    assert segundo.json()["cotejados"] == 1


def test_un_reintento_repite_la_respuesta_aunque_las_columnas_hayan_cambiado(motor_migrate, e):
    """Codex en #211: el reintento de lo que ya salió bien no depende de lo guardado después."""
    _, p = pagado(motor_migrate, e)
    cuerpo = extracto(linea_del_pago(motor_migrate, p))
    subio(e, e.mc1, extracto(linea(date.today(), "-1,00")))  # deja las columnas guardadas
    clave = clave_idempotencia()
    primero = subir(e, e.mc1, cuerpo, None, clave=clave)
    assert primero.status_code == 200 and primero.json()["cotejados"] == 1
    otras = {"fecha": "Dia", "importe": "Debito", "signo": "sin_signo"}
    subio(e, e.mc2, b"Dia;Debito;Comprobante\n05/11/2026;100,00;0048213377\n", otras)
    segundo = subir(e, e.mc1, cuerpo, None, clave=clave)
    assert segundo.status_code == 200 and segundo.json() == primero.json()
    # Control: con otra clave es otro pedido, y ese sí mira las columnas de ahora.
    assert codigo(subir(e, e.mc1, cuerpo, None)) == (422, "columnas_no_encontradas")


# Columnas --------------------------------------------------------------------------------------


def test_las_columnas_quedan_guardadas_por_edificio(motor_migrate, motor_app, e):
    _, p = pagado(motor_migrate, e)
    cuerpo = extracto(linea_del_pago(motor_migrate, p))
    assert codigo(subir(e, e.mc1, cuerpo, None)) == (409, "columnas_sin_elegir")
    assert vista(e, e.mc1, cuerpo).json()["columnas_guardadas"] is None
    subio(e, e.mc1, cuerpo)
    guardadas = vista(e, e.mc1, cuerpo).json()["columnas_guardadas"]
    assert guardadas == COLUMNAS
    assert subio(e, e.mc2, cuerpo, None)["ya_cotejados"] == 1  # sin elegir de nuevo
    otro = escena_economica(motor_migrate, motor_app)
    assert vista(otro, otro.mc1, cuerpo).json()["columnas_guardadas"] is None
    assert codigo(subir(otro, otro.mc1, cuerpo, None)) == (409, "columnas_sin_elegir")


def test_un_archivo_sin_las_columnas_guardadas_vuelve_a_preguntar(motor_migrate, e):
    subio(e, e.mc1, extracto(linea(date.today(), "-1,00")))
    distinto = b"Dia;Debito;Comprobante\n05/11/2026;100,00;0048213377\n"
    assert vista(e, e.mc1, distinto).json()["columnas_guardadas"] is None
    assert codigo(subir(e, e.mc1, distinto, None)) == (422, "columnas_no_encontradas")


def test_columnas_equivocadas_no_guardan_nada(motor_migrate, e):
    cuerpo = extracto(linea(date.today(), "-1,00", "0048213377"))
    columnas = COLUMNAS | {"fecha": "Importe", "importe": "Fecha"}
    assert codigo(subir(e, e.mc1, cuerpo, columnas)) == (422, "extracto_sin_movimientos")
    for tabla in TABLAS:
        assert cuantos(motor_migrate, tabla, e.cons) == 0


def test_columnas_a_medias_o_con_forma_de_formula_se_rechazan(e):
    cuerpo = extracto(linea(date.today(), "-1,00"))
    assert subir(e, e.mc1, cuerpo, {"fecha": "Fecha"}).status_code == 422
    assert subir(e, e.mc1, cuerpo, COLUMNAS | {"fecha": "=Fecha"}).status_code == 422


# El archivo no es de confianza -----------------------------------------------------------------


def _con_tope(persona: UUID, sesion: UUID, tope: int) -> TestClient:
    app = crear_app(Config(url_rol("app"), "0.1.0", "sha-imagen", "sha-contratos",
                           redis_url="memory://", origen_publico="https://cem.ejemplo.com",
                           cotejo_tope_bytes=tope))  # fmt: skip
    app.dependency_overrides[obtener_identidad] = lambda: Identidad(persona, sesion)
    return TestClient(app, client=IP)


def test_el_tope_sale_de_la_configuracion(e):
    cuerpo = extracto(linea(date.today(), "-1,00"))
    with _con_tope(e.mc1, e.sesion[e.mc1], len(cuerpo) - 1) as c:
        cabeceras = clave_idempotencia() | {"Content-Type": "text/csv"}
        r = c.post(url(e, "pagos/cotejo/extracto"), content=cuerpo, params=COLUMNAS,
                   headers=cabeceras)  # fmt: skip
        assert codigo(r) == (413, "archivo_demasiado_grande")
        lista = c.get(url(e, "pagos/cotejo"))
        assert lista.json()["tope_bytes"] == len(cuerpo) - 1
    with _con_tope(e.mc1, e.sesion[e.mc1], len(cuerpo)) as c:  # control
        r = c.post(url(e, "pagos/cotejo/extracto"), content=cuerpo, params=COLUMNAS,
                   headers=clave_idempotencia() | {"Content-Type": "text/csv"})  # fmt: skip
        assert r.status_code == 200, r.json()


def test_demasiadas_lineas_se_dice_aparte(e):
    cuerpo = extracto(*[linea(date.today(), "-1,00", f"00482{i:05d}") for i in range(3)])
    app = crear_app(Config(url_rol("app"), "0.1.0", "sha-imagen", "sha-contratos",
                           redis_url="memory://", origen_publico="https://cem.ejemplo.com",
                           cotejo_maximo_lineas=2))  # fmt: skip
    app.dependency_overrides[obtener_identidad] = lambda: Identidad(e.mc1, e.sesion[e.mc1])
    with TestClient(app, client=IP) as c:
        r = c.post(url(e, "pagos/cotejo/columnas"), content=cuerpo,
                   headers={"Content-Type": "text/csv"})  # fmt: skip
    assert codigo(r) == (422, "extracto_demasiadas_lineas")
    assert vista(e, e.mc1, cuerpo).status_code == 200  # control


def test_la_lista_dice_cuantos_pagos_entran_en_un_pedido(motor_migrate, e):
    maximo = pendientes(e, e.mc1)["maximo_pagos"]
    _, p = pagado(motor_migrate, e)
    r = post(e, e.mc1, "pagos/cotejo", {"pagos": [str(p)] + [str(uuid4()) for _ in range(maximo)]})
    assert r.status_code == 422
    assert cotejo(motor_migrate, p) is None
    assert tildar(e, e.mc1, p).status_code == 200  # control


def test_solo_se_acepta_un_csv(e):
    cuerpo = extracto(linea(date.today(), "-1,00"))
    with cliente(e.mc1, e.sesion[e.mc1]) as c:
        r = c.post(url(e, "pagos/cotejo/columnas"), content=cuerpo,
                   headers={"Content-Type": "application/pdf"})  # fmt: skip
    assert codigo(r) == (415, "tipo_no_admitido")
    assert codigo(vista(e, e.mc1, b"PK\x03\x04\x00\x00binario")) == (422, "extracto_invalido")
    assert codigo(vista(e, e.mc1, b"una sola linea de texto")) == (422, "extracto_invalido")
    assert codigo(vista(e, e.mc1, b'=SUM(A1);Importe\n05/11/2026;-1,00\n')) == (
        422, "extracto_invalido",
    )  # fmt: skip
    assert vista(e, e.mc1, cuerpo).status_code == 200  # control


def test_una_formula_en_una_celda_no_llega_a_ningun_lado(motor_migrate, e):
    hoy = date.today()
    fecha = hoy.strftime("%d/%m/%Y")
    cuerpo = extracto(
        f'{fecha};Pago;=HYPERLINK("http://ejemplo.invalid");0048213377',
        f"{fecha};Pago;-1,00;=1+1",
        linea(hoy, "-2,00", "0048213378"),
    )
    resultado = subio(e, e.mc1, cuerpo)
    assert resultado["lineas_no_leidas"] == 1 and resultado["filas_no_leidas"] == [2]
    with motor_migrate.connect() as c:
        numeros = set(c.execute(
            text("select numero_operacion from nucleo.movimiento_extracto"
                 " where consorcio_id = :c"), {"c": e.cons},
        ).scalars())  # fmt: skip
    assert numeros == {None, "0048213378"}


def test_una_cuenta_no_se_muestra_ni_se_guarda(motor_migrate, e):
    cuenta = "9990000300000000000017"
    hoy = date.today()
    cuerpo = extracto(linea(hoy, "-2,00", cuenta, descripcion=f"Transferencia a {cuenta}"))
    r = vista(e, e.mc1, cuerpo)
    assert r.status_code == 200 and cuenta not in r.text
    assert any("Transferencia a" in celda for celda in r.json()["muestra"])
    resultado = subir(e, e.mc1, cuerpo)
    assert resultado.status_code == 200 and cuenta not in resultado.text
    with motor_migrate.connect() as c:
        fila = c.execute(
            text("select numero_operacion from nucleo.movimiento_extracto"
                 " where consorcio_id = :c"), {"c": e.cons},
        ).scalar_one()  # fmt: skip
    assert fila is None


# Tablas ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("tabla", TABLAS)
def test_rls_habilitada_y_forzada(motor_migrate, tabla):
    with motor_migrate.connect() as c:
        fila = c.execute(
            text("select relrowsecurity, relforcerowsecurity from pg_class"
                 " where oid = cast(:t as regclass)"), {"t": tabla},
        ).one()  # fmt: skip
    assert tuple(fila) == (True, True)


@pytest.mark.parametrize("tabla", TABLAS)
def test_otro_consorcio_no_ve_las_filas(motor_migrate, motor_app, e, tabla):
    """La política sola, contra PostgreSQL real: `cem_app` no tiene permiso sobre estas tablas, y
    acá se le da mientras dura la prueba."""
    _, p = pagado(motor_migrate, e)
    subio(e, e.mc1, extracto(linea_del_pago(motor_migrate, p)))
    otro = escena_economica(motor_migrate, motor_app)
    with pytest.raises(DBAPIError) as error:
        with transaccion(motor_app, persona_id=e.mc1, consorcio_id=e.cons) as c:
            c.execute(text(f"select count(*) from {tabla}"))
    assert error.value.orig.sqlstate == "42501"
    with motor_migrate.begin() as c:
        c.execute(text(f"grant select on {tabla} to cem_app"))
    try:
        with transaccion(motor_app, persona_id=otro.mc1, consorcio_id=otro.cons) as c:
            assert c.execute(text(f"select count(*) from {tabla}")).scalar_one() == 0
        with transaccion(motor_app, persona_id=e.mc1, consorcio_id=e.cons) as c:
            assert c.execute(text(f"select count(*) from {tabla}")).scalar_one() == 1  # control
    finally:
        with motor_migrate.begin() as c:
            c.execute(text(f"revoke select on {tabla} from cem_app"))


def test_otro_consorcio_no_llega_por_las_rutas_ni_por_la_base(motor_migrate, motor_app, e):
    _, p = pagado(motor_migrate, e)
    otro = escena_economica(motor_migrate, motor_app)
    assert pendientes(otro, otro.mc1)["elementos"] == []
    assert codigo(tildar(otro, otro.mc1, p)) == (404, "no_encontrado")
    assert cotejo(motor_migrate, p) is None
    a = f"/api/v1/consorcios/{e.cons}/pagos/cotejo"
    with cliente(otro.mc1, otro.sesion[otro.mc1]) as c:
        assert codigo(c.get(a)) == (404, "no_encontrado")
    assert ids(pendientes(e, e.mc1)) == [str(p)]  # control


def test_el_mismo_extracto_en_otro_consorcio_es_otro(motor_migrate, motor_app, e):
    cuerpo = extracto(linea(date.today(), "-1,00", "0048213377"))
    otro = escena_economica(motor_migrate, motor_app)
    assert subio(e, e.mc1, cuerpo)["movimientos_nuevos"] == 1
    assert subio(otro, otro.mc1, cuerpo)["movimientos_nuevos"] == 1


def test_la_historia_no_se_borra(motor_migrate, e):
    _, p = pagado(motor_migrate, e)
    subio(e, e.mc1, extracto(linea_del_pago(motor_migrate, p)))
    for tabla in ("nucleo.cotejo_pago", "nucleo.movimiento_extracto",
                  "nucleo.importacion_extracto"):  # fmt: skip
        with pytest.raises(DBAPIError):
            with motor_migrate.begin() as c:
                c.execute(text(f"delete from {tabla} where consorcio_id = :c"), {"c": e.cons})
    assert cotejo(motor_migrate, p) is not None


def test_cotejar_deja_auditoria(motor_migrate, e):
    o, p = pagado(motor_migrate, e)
    assert tildar(e, e.mc1, p).status_code == 200
    subio(e, e.mc1, extracto(linea(date.today(), "-1,00")))
    with motor_migrate.connect() as c:
        acciones = set(c.execute(
            text("select accion from auditoria.eventos where consorcio_id = :c"), {"c": e.cons},
        ).scalars())  # fmt: skip
    assert {"consorcio.orden_pago.cotejar_pago", "consorcio.extracto.importar"} <= acciones


# Migración -------------------------------------------------------------------------------------


def _migracion_0074():
    ruta = Path(__file__).parents[1] / "migrations" / "versions" / "0074_cotejo_con_el_banco.py"
    spec = importlib.util.spec_from_file_location("migracion_0074", ruta)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def test_la_guarda_del_downgrade_se_niega_con_un_cotejo(motor_migrate, e):
    guarda = _migracion_0074().GUARDA_DOWNGRADE
    _, p = pagado(motor_migrate, e)
    assert tildar(e, e.mc1, p).status_code == 200
    with motor_migrate.connect() as c, c.begin() as tx:
        with pytest.raises(DBAPIError) as error, c.begin_nested():
            c.execute(text(guarda))
        assert error.value.orig.diag.message_hint == "downgrade_con_datos"
        tx.rollback()


def test_0074_ida_y_vuelta(base_aparte):
    cfg, motor = base_aparte
    anterior = _migracion_0074().down_revision
    command.upgrade(cfg, "0074")
    command.downgrade(cfg, anterior)
    with motor.connect() as c:
        assert c.execute(text("select to_regclass('nucleo.cotejo_pago')")).scalar_one() is None
        definicion = c.execute(
            text("select pg_get_functiondef('nucleo.exportar_pagos(uuid, uuid)'::regprocedure)")
        ).scalar_one()
    assert "cotejo_pago" not in definicion and "confirmacion_no_pagado" in definicion
    command.upgrade(cfg, "0074")
    with motor.connect() as c:
        assert c.execute(text("select to_regclass('nucleo.cotejo_pago')")).scalar_one() is not None
