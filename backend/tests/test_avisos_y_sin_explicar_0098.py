"""Tanda 8 de fricción contra PostgreSQL real (`0098`; preguntas 7 y 8 del propietario, 13-11-2026).
Datos SINTETICOS / PRUEBA / NO REAL: ninguna cuenta es de un banco (prefijo 999), ningún correo sale
(se lee lo que queda en el outbox) y no se mueve dinero.

- **Aviso por correo** (pregunta 7): a quien tiene algo para aprobar, pagar o conciliar, sin datos
  de dinero, como máximo uno por día. Contra `3c2d638` ninguna de estas pruebas corre: no existen
  `avisos_pagos` ni `nucleo.pendientes_para_avisar`.
- **Lo que el extracto no explica** (pregunta 8, opción c): en la bandeja de quien concilia y, solo
  para leer, en la de la administración. Contra `3c2d638` la ruta da 404 y la bandeja no trae
  `sin_explicar`.
- **Columnas reconocidas solas** (P-23): la vista del extracto las trae.
"""

import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.config import Config
from app.consorcios import avisos_pagos
from app.db import transaccion
from app.plataforma import cola
from tests.conftest import url_rol
from tests.test_cotejo_0074 import (
    COLUMNAS,
    dato_pago,
    extracto,
    linea,
    linea_del_pago,
    pagado,
    subio,
    vista,
)
from tests.test_egresos_1a import aprobada, enviada
from tests.test_mandatos import otra_persona
from tests.test_mandatos_0012 import alta
from tests.test_orden_pago_neutra import (
    EMITIR,
    ESENCIALES,
    RAZON_SOCIAL,
    Escena,
    escena_economica,
    llamar,
    nueva_sesion,
    otra_cuenta_en_el_consejo,
)
from tests.test_pago_manual_0055 import CUENTA, acta, emitida, registrada
from tests.test_rutas_egresos import get

pytestmark = pytest.mark.db

ORIGEN = "https://cem.ejemplo.com"
CONFIG = Config(url_rol("app"), "0.1.0", "sha", "sha", "memory://", origen_publico=ORIGEN)
HOY = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
PLAZO = timedelta(hours=CONFIG.pagos_horas_tomar)

# Detector de datos de dinero en un correo ---------------------------------------------------------
MONEDA = re.compile(r"\$|\bARS\b|\bUSD\b|U\$S")
IMPORTE_CON_SEPARADORES = re.compile(r"\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?|\d+[.,]\d{2}\b")
DIGITOS_LARGOS = re.compile(r"\d{7,}|\d{2}-\d{8}-\d")
"""Un CBU (22), una cuenta, un CUIT con o sin guiones o un importe en centavos."""


def datos_de_dinero(texto: str, sembrados: list[str]) -> list[str]:
    """Lo que en `texto` parece un importe, una moneda, una cuenta o un dato sembrado."""
    hallados = [m.group() for p in (MONEDA, IMPORTE_CON_SEPARADORES, DIGITOS_LARGOS)
                for m in p.finditer(texto)]  # fmt: skip
    return hallados + [s for s in sembrados if s in texto]


def sembrados() -> list[str]:
    """Lo de la escena que un aviso nunca puede llevar: importe en todas sus formas, cuenta,
    proveedor y concepto."""
    centavos = ESENCIALES["importe"]
    pesos, resto = divmod(centavos, 100)
    return [str(centavos), f"{pesos},{resto:02d}", f"{pesos}.{resto:02d}",
            f"{pesos:,}".replace(",", ".") + f",{resto:02d}", CUENTA, CUENTA[-4:],
            RAZON_SOCIAL, ESENCIALES["concepto"]]  # fmt: skip


@pytest.fixture
def e(motor_migrate, motor_app) -> Escena:
    return escena_economica(motor_migrate, motor_app)


def correo_de(motor_migrate, persona: UUID) -> str:
    with motor_migrate.connect() as c:
        return c.execute(
            text("select correo from nucleo.persona where id = :p"), {"p": persona}
        ).scalar_one()


def avisar(motor_app, ventana: datetime = HOY, config: Config = CONFIG) -> dict:
    with transaccion(motor_app, persona_id=None) as c:
        return avisos_pagos.avisar(c, ventana, config)


def avisos_a(motor_migrate, persona: UUID) -> list[dict]:
    """Los correos pedidos para esa persona, en orden."""
    with motor_migrate.connect() as c:
        filas = c.execute(
            text("select consorcio_id, carga from plataforma.outbox"
                 " where evento = 'correo.solicitado' and carga->>'para' = :para order by id"),
            {"para": correo_de(motor_migrate, persona)},
        ).mappings().all()  # fmt: skip
    return [dict(f) for f in filas]


# El detector y su control --------------------------------------------------------------------


def test_control_el_detector_encuentra_un_importe_y_una_cuenta_sembrados():
    assert datos_de_dinero("Hay que pagar $ 12.345,00 a la cuenta.", []) == ["$", "12.345,00"]
    assert datos_de_dinero(f"Cuenta {CUENTA}.", []) == [CUENTA]
    assert datos_de_dinero("Importe 12345.00", []) == ["12345.00"]
    assert datos_de_dinero("CUIT 20-12345678-9", []) == ["20-12345678-9"]
    assert datos_de_dinero(f"Pagar a {RAZON_SOCIAL}", [RAZON_SOCIAL]) == [RAZON_SOCIAL]
    # Y no confunde una cantidad de órdenes con un importe.
    assert datos_de_dinero("- Prueba: 2 órdenes esperan su aprobación.", sembrados()) == []


# Pregunta 7: el aviso -------------------------------------------------------------------------


def test_quien_tiene_algo_para_aprobar_recibe_un_aviso_sin_datos_de_dinero(
    motor_migrate, motor_app, e
):
    enviada(motor_migrate, e)
    avisar(motor_app)
    for consejero in (e.mc1, e.mc2):
        [aviso] = avisos_a(motor_migrate, consejero)
        carga = aviso["carga"]
        assert aviso["consorcio_id"] is None  # uno por persona, aunque tenga varios edificios
        assert carga["asunto"] == avisos_pagos.ASUNTO
        assert "- Prueba: una orden espera su aprobación." in carga["texto"]
        assert f"{ORIGEN}/pagos" in carga["texto"]
        assert set(carga) == {"para", "asunto", "texto"}
        assert datos_de_dinero(carga["asunto"] + carga["texto"], sembrados()) == []
    # Quien la preparó no tiene nada para aprobar, pagar ni conciliar: no recibe nada.
    assert avisos_a(motor_migrate, e.adm) == []


def test_la_bandeja_y_el_aviso_dicen_lo_mismo(motor_migrate, motor_app, e):
    """El aviso sale de la misma función que la bandeja: lo que avisa está en la bandeja con el
    paso de aprobar y le toca a quien lo recibe."""
    o = enviada(motor_migrate, e)
    r = get(e, e.mc1, "pagos/bandeja")
    assert r.status_code == 200, r.json()
    [fila] = [x for x in r.json()["elementos"] if x["orden_id"] == str(o)]
    assert (fila["paso"], fila["le_toca_a"]) == ("aprobar", "vos")
    avisar(motor_app)
    assert len(avisos_a(motor_migrate, e.mc1)) == 1


def test_cada_paso_le_llega_a_quien_le_toca(motor_migrate, motor_app, e):
    o = aprobada(motor_migrate, e)
    avisar(motor_app)
    [para_pagar] = avisos_a(motor_migrate, e.adm)
    assert "- Prueba: un pago para hacer." in para_pagar["carga"]["texto"]
    assert avisos_a(motor_migrate, e.mc2) == []  # nada para aprobar ni conciliar
    llamar_emitir_y_registrar(motor_migrate, e, o)
    avisar(motor_app, HOY + timedelta(days=1))
    [para_conciliar] = avisos_a(motor_migrate, e.mc2)
    assert "- Prueba: un pago para conciliar." in para_conciliar["carga"]["texto"]
    assert datos_de_dinero(para_conciliar["carga"]["texto"], sembrados()) == []


def llamar_emitir_y_registrar(motor_migrate, e, o) -> None:
    llamar(motor_migrate, e, e.adm, EMITIR, orden=o, version=1)
    registrada(motor_migrate, e, o)


def test_quien_preparo_no_recibe_el_aviso_de_aprobar_ni_con_otra_cuenta(
    motor_migrate, motor_app, e
):
    otra = otra_cuenta_en_el_consejo(motor_migrate, motor_app, e, de=e.adm)
    enviada(motor_migrate, e)
    avisar(motor_app)
    assert avisos_a(motor_migrate, otra) == []
    # Control: un consejero que no la preparó sí lo recibe en la misma corrida.
    assert len(avisos_a(motor_migrate, e.mc1)) == 1


def test_varias_ordenes_en_un_solo_correo(motor_migrate, motor_app, e):
    enviada(motor_migrate, e)
    enviada(motor_migrate, e, concepto="SINTETICO / PRUEBA / NO REAL: otra cosa")
    avisar(motor_app)
    [aviso] = avisos_a(motor_migrate, e.mc1)
    assert "- Prueba: 2 órdenes esperan su aprobación." in aviso["carga"]["texto"]


def test_como_maximo_uno_por_dia_aunque_cambie_la_hora(motor_migrate, motor_app, e):
    enviada(motor_migrate, e)
    primera = avisar(motor_app, HOY)
    assert primera["avisos"] >= 1 and len(avisos_a(motor_migrate, e.mc1)) == 1
    # Alguien movió la hora: otra ventana el mismo día. Con tope 1, no sale otro.
    avisar(motor_app, HOY + timedelta(hours=3))
    assert len(avisos_a(motor_migrate, e.mc1)) == 1
    # Control: al día siguiente, a la misma hora, sí.
    avisar(motor_app, HOY + timedelta(days=1))
    assert len(avisos_a(motor_migrate, e.mc1)) == 2


def test_con_tope_dos_salen_dos_por_dia_y_no_tres(motor_migrate, motor_app, e):
    enviada(motor_migrate, e)
    dos = replace(CONFIG, avisos_tope_diario=2)
    for horas in (0, 12, 18):
        avisar(motor_app, HOY + timedelta(hours=horas), dos)
    assert len(avisos_a(motor_migrate, e.mc1)) == 2
    assert "como máximo 2 veces por día" in avisos_a(motor_migrate, e.mc1)[0]["carga"]["texto"]


def test_sin_nada_pendiente_no_sale_ningun_correo(motor_migrate, motor_app, e):
    avisar(motor_app)
    for persona in (e.adm, e.mc1, e.mc2):
        assert avisos_a(motor_migrate, persona) == []


def test_sin_el_origen_publico_el_trabajo_falla_y_no_manda_nada(
    motor_migrate, motor_app, e, monkeypatch
):
    enviada(motor_migrate, e)
    monkeypatch.delenv("CEM_ORIGEN_PUBLICO", raising=False)
    trabajo = cola.Trabajo(id=UUID(int=1), tipo=avisos_pagos.TIPO, consorcio_id=None,
                           carga={"ventana": HOY.isoformat()}, intentos=1,
                           maximo_intentos=1)  # fmt: skip
    with pytest.raises(avisos_pagos.ErrorConfigAvisos):
        with transaccion(motor_app, persona_id=None) as c:
            avisos_pagos.avisar_pendientes(c, trabajo)
    assert avisos_a(motor_migrate, e.mc1) == []
    # Control: con la variable, el mismo trabajo lo manda.
    monkeypatch.setenv("CEM_ORIGEN_PUBLICO", ORIGEN)
    with transaccion(motor_app, persona_id=None) as c:
        resultado = avisos_pagos.avisar_pendientes(c, trabajo)
    assert resultado["avisos"] >= 1 and len(avisos_a(motor_migrate, e.mc1)) == 1


def test_las_acciones_del_aviso_son_las_de_la_bandeja(motor_migrate, motor_app, e):
    """`0098` movió el cuerpo de `acciones_de_ordenes_lectura` a `acciones_de_ordenes_de`: con la
    sesión de la persona y sin ella dan las mismas acciones."""
    enviada(motor_migrate, e)
    aprobada(motor_migrate, e)
    for persona in (e.adm, e.mc1, e.mc2):
        with transaccion(motor_app, persona_id=persona, consorcio_id=e.cons) as c:
            api = c.execute(
                text("select * from nucleo.acciones_de_ordenes_lectura(null, :a, 'api', :s, :p)"
                     " order by orden_id"), {"a": persona, "s": e.sesion[persona], "p": PLAZO},
            ).mappings().all()  # fmt: skip
        with transaccion(motor_app, persona_id=None) as c:
            aviso = c.execute(
                text("select * from nucleo.pendientes_para_avisar(:p)"
                     " where consorcio_id = :c and persona_id = :a order by orden_id"),
                {"p": PLAZO, "c": e.cons, "a": persona},
            ).mappings().all()  # fmt: skip
        assert len(api) == 2  # control: hay algo que comparar
        sin_datos = [{k: v for k, v in f.items() if k not in avisos_pagos.DATOS_DE_LA_ORDEN}
                     for f in aviso]  # fmt: skip
        assert sin_datos == [dict(f) for f in api]


def test_los_pendientes_de_todos_solo_los_lee_el_trabajo(motor_app, e):
    with pytest.raises(DBAPIError) as error:
        with transaccion(motor_app, persona_id=e.mc1, consorcio_id=e.cons) as c:
            c.execute(text("select * from nucleo.pendientes_para_avisar(:p)"), {"p": PLAZO})
    assert error.value.orig.diag.sqlstate == "42501"
    with pytest.raises(DBAPIError) as error:
        with transaccion(motor_app, persona_id=None, consorcio_id=e.cons) as c:
            c.execute(text("select * from nucleo.pendientes_para_avisar(:p)"), {"p": PLAZO})
    assert error.value.orig.diag.sqlstate == "42501"
    with pytest.raises(DBAPIError) as error:
        with transaccion(motor_app, persona_id=None) as c:
            c.execute(text("select nucleo.registrar_aviso_pendientes(:a, :v, 0)"),
                      {"a": e.mc1, "v": HOY})  # fmt: skip
    assert error.value.orig.diag.message_hint == "tope_invalido"
    # `cem_app` no toca la tabla del tope.
    with pytest.raises(DBAPIError) as error:
        with transaccion(motor_app, persona_id=None) as c:
            c.execute(text("select count(*) from nucleo.aviso_pendientes"))
    assert error.value.orig.diag.sqlstate == "42501"
    # Control: sin persona ni consorcio, la lectura funciona.
    with transaccion(motor_app, persona_id=None) as c:
        c.execute(text("select * from nucleo.pendientes_para_avisar(:p)"), {"p": PLAZO}).all()


def test_un_edificio_sin_el_modo_de_egresos_no_avisa(motor_migrate, motor_app):
    e = escena_economica(motor_migrate, motor_app, con_modo=False)
    with transaccion(motor_app, persona_id=None) as c:
        filas = c.execute(
            text("select * from nucleo.pendientes_para_avisar(:p) where consorcio_id = :c"),
            {"p": PLAZO, "c": e.cons},
        ).all()  # fmt: skip
    assert filas == []


# Pregunta 8: lo que el extracto no explica ------------------------------------------------------


def sin_explicar(e, quien) -> dict:
    r = get(e, quien, "pagos/extracto/sin-explicar")
    assert r.status_code == 200, r.json()
    assert CUENTA not in r.text
    return r.json()


def bandeja(e, quien) -> dict:
    r = get(e, quien, "pagos/bandeja")
    assert r.status_code == 200, r.json()
    return r.json()


def test_lo_que_el_extracto_no_explica_llega_a_quien_concilia_y_a_la_administracion(
    motor_migrate, e
):
    _, p = pagado(motor_migrate, e)
    fecha = dato_pago(motor_migrate, p)["fecha_operacion"]
    # Control: antes de subir un extracto, nada.
    for quien in (e.mc1, e.adm):
        assert sin_explicar(e, quien) == {"elementos": [], "total": 0}
        assert bandeja(e, quien)["sin_explicar"] == 0
    subio(e, e.mc1, extracto(
        linea_del_pago(motor_migrate, p), linea(fecha, "-12.400,00", "0048213999"),
    ))  # fmt: skip
    for quien in (e.mc1, e.mc2, e.adm):
        lista = sin_explicar(e, quien)
        assert lista["total"] == 1
        [m] = lista["elementos"]
        assert (m["fecha"], m["importe_centavos"], m["numero_operacion"]) == (
            fecha.isoformat(), 1_240_000, "0048213999")  # fmt: skip
        assert bandeja(e, quien)["sin_explicar"] == 1


def test_lo_registrado_sin_conciliar_explica_su_debito(motor_migrate, e):
    o = emitida(motor_migrate, e)
    p = registrada(motor_migrate, e, o)
    subio(e, e.mc1, extracto(linea_del_pago(motor_migrate, p)))
    assert sin_explicar(e, e.mc1)["total"] == 0
    # Control: otro débito del mismo extracto, sin pago, sí queda.
    fecha = dato_pago(motor_migrate, p)["fecha_operacion"]
    subio(e, e.mc1, extracto(linea_del_pago(motor_migrate, p), linea(fecha, "-7,50", "")))
    assert sin_explicar(e, e.mc1)["total"] == 1


def test_quien_no_concilia_ni_prepara_no_lo_ve(motor_migrate, motor_app, e):
    vecina = otra_persona(motor_migrate, e.cons)
    alta(motor_app, e.cons, e.prop, acta(motor_migrate, e), tipo="miembro_consejo",
         persona=vecina)  # fmt: skip
    # Sin ninguna facultad de egresos: el propietario de la escena.
    e.sesion[e.prop] = nueva_sesion(motor_migrate, e.prop)
    r = get(e, e.prop, "pagos/extracto/sin-explicar")
    assert r.status_code == 403, r.json()
    # Control: un consejero nuevo, con la facultad de conciliar, sí.
    e.sesion[vecina] = nueva_sesion(motor_migrate, vecina)
    assert get(e, vecina, "pagos/extracto/sin-explicar").status_code == 200


# P-23: columnas reconocidas -------------------------------------------------------------------


def test_la_vista_trae_las_columnas_reconocidas(motor_migrate, e):
    _, p = pagado(motor_migrate, e)
    r = vista(e, e.mc1, extracto(linea_del_pago(motor_migrate, p)))
    assert r.status_code == 200, r.json()
    assert r.json()["columnas_reconocidas"] == COLUMNAS | {"moneda": "ARS"}
    # Control: con dos columnas que pueden ser la fecha, se pregunta.
    dudosa = "Fecha;Fecha de operación;Importe\n05/11/2026;05/11/2026;-10,00".encode()
    assert vista(e, e.mc1, dudosa).json()["columnas_reconocidas"] is None
