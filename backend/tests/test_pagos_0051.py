"""Ejecución lógica del pago (0051, `app/consorcios/pagos.py`). Sin riel real: el riel de estas
pruebas es un simulador que vive solo acá y cuenta cuántas veces se pagó cada clave."""

from dataclasses import dataclass, field
from uuid import UUID

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.consorcios import pagos
from app.consorcios.pagos import EstadoRiel, PedidoPago, RespuestaRiel
from app.db import transaccion
from app.plataforma import cola
from tests.test_egresos_1a import aprobada, e  # noqa: F401  (`e` es un fixture)
from tests.test_orden_pago_neutra import EMITIR, ESENCIALES, MODIFICAR, llamar, orden
from tests.test_vinculo_0014 import (  # noqa: F401  (`base_aparte` es un fixture)
    _consorcio_y_unidad,
    _persona,
    base_aparte,
)

pytestmark = pytest.mark.db
# `e` y `base_aparte` son fixtures importados: los parámetros con su nombre no los redefinen.
# ruff: noqa: F811


@dataclass
class RielSimulado:
    """Idempotente por clave, como exige `RielDePago`: cuenta los pagos de verdad hechos."""

    al_ordenar: EstadoRiel = EstadoRiel.PAGADA
    al_consultar: EstadoRiel | None = None
    falla: Exception | None = None
    pagos: dict[UUID, RespuestaRiel] = field(default_factory=dict)
    ordenes: int = 0
    consultas: int = 0

    def ordenar(self, pedido: PedidoPago) -> RespuestaRiel:
        self.ordenes += 1
        if self.falla is not None:
            raise self.falla
        if pedido.clave not in self.pagos:
            self.pagos[pedido.clave] = RespuestaRiel(
                self.al_ordenar, referencia=f"SIM-{len(self.pagos) + 1}"
            )
        return self.pagos[pedido.clave]

    def consultar(self, clave: UUID) -> RespuestaRiel:
        self.consultas += 1
        if self.al_consultar is not None:
            return RespuestaRiel(self.al_consultar)
        return self.pagos.get(clave, RespuestaRiel(EstadoRiel.DESCONOCIDA))


@pytest.fixture
def riel():
    simulado = RielSimulado()
    pagos.configurar_riel(simulado)
    try:
        yield simulado
    finally:
        pagos.configurar_riel(None)


def trabajos(motor_migrate, tipo: str, cons: UUID) -> list[cola.Trabajo]:
    with motor_migrate.connect() as c:
        filas = c.execute(
            text("select id, tipo, consorcio_id, carga, intentos, maximo_intentos"
                 " from plataforma.jobs where tipo = :t and consorcio_id = :c"
                 " and estado = 'pendiente' order by creado_en, id"), {"t": tipo, "c": cons},
        ).mappings()  # fmt: skip
        return [cola.Trabajo(**f) for f in filas]


def correr(motor_migrate, motor_app, tipo: str, cons: UUID) -> list[dict]:
    """Corre los trabajos pendientes de ese tipo como `cem-trabajos`: con `cem_app`, el consorcio
    fijado y sin persona; los marca hechos solo si terminan bien (como la cola)."""
    manejador = cola.REGISTRO.tipos[tipo].manejador
    salidas = []
    for t in trabajos(motor_migrate, tipo, cons):
        with transaccion(motor_app, persona_id=None, consorcio_id=cons) as c:
            salidas.append(manejador(c, t))
        with motor_migrate.begin() as c:
            c.execute(text("update plataforma.jobs set estado = 'hecho', terminado_en = now()"
                           " where id = :i"), {"i": t.id})  # fmt: skip
    return salidas


def emitida(motor_migrate, e) -> UUID:
    o = aprobada(motor_migrate, e)
    llamar(motor_migrate, e, e.adm, EMITIR, orden=o, version=1)
    return o


def ejecuciones(motor_migrate, o) -> list[dict]:
    with motor_migrate.connect() as c:
        return [dict(f) for f in c.execute(
            text("select * from nucleo.ejecucion_orden_pago where orden_id = :o order by version"),
            {"o": o},
        ).mappings()]  # fmt: skip


def resultados(motor_migrate, o) -> list[tuple[str, str]]:
    with motor_migrate.connect() as c:
        return [tuple(f) for f in c.execute(
            text("select r.resultado, r.origen from nucleo.resultado_ejecucion_orden_pago r"
                 " join nucleo.ejecucion_orden_pago x on x.id = r.ejecucion_id"
                 " where x.orden_id = :o order by r.registrado_en, r.id"), {"o": o},
        )]  # fmt: skip


def test_emitir_crea_una_ejecucion_con_la_foto_y_encola_ordenar_y_verificar(motor_migrate, e):
    o = emitida(motor_migrate, e)
    [x] = ejecuciones(motor_migrate, o)
    assert (x["version"], x["importe_centavos"], x["moneda"]) == (1, ESENCIALES["importe"], "ARS")
    assert x["cuenta_destino"] is not None and len(x["cuenta_destino"]) == 22
    [ordenar] = trabajos(motor_migrate, pagos.TRABAJO_ORDENAR, e.cons)
    assert (ordenar.maximo_intentos, ordenar.carga) == (1, {"ejecucion": str(x["id"])})
    with motor_migrate.connect() as c:
        verificar = c.execute(
            text("select maximo_intentos, carga, disponible_desde - creado_en as espera"
                 " from plataforma.jobs where tipo = :t and carga->>'ejecucion' = :x"),
            {"t": pagos.TRABAJO_VERIFICAR, "x": str(x["id"])},
        ).one()  # fmt: skip
    assert verificar.maximo_intentos == pagos.INTENTOS_VERIFICAR
    assert verificar.carga == {"ejecucion": str(x["id"]), "consulta": 1}
    assert verificar.espera == pagos.ESPERA_ENTRE_CONSULTAS


def test_sin_riel_no_se_mueve_nada(motor_migrate, motor_app, e):
    o = emitida(motor_migrate, e)
    assert correr(motor_migrate, motor_app, pagos.TRABAJO_ORDENAR, e.cons) == [
        {"estado": "sin_riel"}
    ]
    assert orden(motor_migrate, o)["estado"] == "en_ejecucion"
    assert resultados(motor_migrate, o) == []


def test_pagada_una_sola_vez_aunque_ordenar_se_repita(motor_migrate, motor_app, e, riel):
    o = emitida(motor_migrate, e)
    [t] = trabajos(motor_migrate, pagos.TRABAJO_ORDENAR, e.cons)
    assert correr(motor_migrate, motor_app, pagos.TRABAJO_ORDENAR, e.cons) == [{"estado": "pagada"}]
    assert orden(motor_migrate, o)["estado"] == "pagada"
    # El mismo trabajo otra vez (el trabajador murió después de confirmar): no vuelve a llamar.
    with transaccion(motor_app, persona_id=None, consorcio_id=e.cons) as c:
        assert pagos.ordenar_pago(c, t) == {"estado": "sin_cambios", "orden": "pagada"}
    assert (riel.ordenes, len(riel.pagos)) == (1, 1)
    assert resultados(motor_migrate, o) == [("pagada", "ordenar")]
    # La verificación posterior ve el resultado y no consulta.
    assert correr(motor_migrate, motor_app, pagos.TRABAJO_VERIFICAR, e.cons) == [
        {"estado": "resuelta", "orden": "pagada"}
    ]
    assert riel.consultas == 0


def test_incierto_no_se_reintenta_se_verifica(motor_migrate, motor_app, e, riel):
    riel.falla = TimeoutError()
    o = emitida(motor_migrate, e)
    correr(motor_migrate, motor_app, pagos.TRABAJO_ORDENAR, e.cons)
    assert orden(motor_migrate, o)["estado"] == "en_verificacion"
    assert resultados(motor_migrate, o) == [("incierta", "ordenar")]
    # El riel sí pagó (se perdió la respuesta): la consulta lo encuentra y no se vuelve a ordenar.
    [x] = ejecuciones(motor_migrate, o)
    riel.pagos[x["clave"]] = RespuestaRiel(EstadoRiel.PAGADA, referencia="SIM-1")
    correr(motor_migrate, motor_app, pagos.TRABAJO_VERIFICAR, e.cons)
    assert orden(motor_migrate, o)["estado"] == "pagada"
    assert riel.ordenes == 1
    assert resultados(motor_migrate, o) == [("incierta", "ordenar"), ("pagada", "consultar")]


def test_incierto_en_la_consulta_encola_la_siguiente_y_al_final_muere(
    motor_migrate, motor_app, e, riel
):
    riel.al_ordenar = EstadoRiel.INCIERTA
    riel.al_consultar = EstadoRiel.INCIERTA
    o = emitida(motor_migrate, e)
    correr(motor_migrate, motor_app, pagos.TRABAJO_ORDENAR, e.cons)
    [salida] = correr(motor_migrate, motor_app, pagos.TRABAJO_VERIFICAR, e.cons)
    assert salida == {"estado": "en_verificacion", "consulta": 1}
    [siguiente] = trabajos(motor_migrate, pagos.TRABAJO_VERIFICAR, e.cons)
    assert siguiente.carga["consulta"] == 2
    ultima = cola.Trabajo(siguiente.id, siguiente.tipo, siguiente.consorcio_id,
                          {**siguiente.carga, "consulta": pagos.CONSULTAS_MAXIMAS},
                          0, siguiente.maximo_intentos)  # fmt: skip
    with (
        pytest.raises(cola.ErrorDefinitivo),
        transaccion(motor_app, persona_id=None, consorcio_id=e.cons) as c,
    ):
        pagos.verificar_pago(c, ultima)
    assert riel.ordenes == 1
    assert orden(motor_migrate, o)["estado"] == "en_verificacion"


def test_el_riel_que_nunca_recibio_la_orden_la_deja_fallida_sin_reenviar(
    motor_migrate, motor_app, e, riel
):
    o = emitida(motor_migrate, e)
    # Ordenar murió antes de llamar (venció su plazo): solo queda la verificación.
    with motor_migrate.begin() as c:
        c.execute(text("update plataforma.jobs set estado = 'muerto', terminado_en = now()"
                       " where tipo = :t and consorcio_id = :c"),
                  {"t": pagos.TRABAJO_ORDENAR, "c": e.cons})  # fmt: skip
    correr(motor_migrate, motor_app, pagos.TRABAJO_VERIFICAR, e.cons)
    assert orden(motor_migrate, o)["estado"] == "fallida"
    assert riel.ordenes == 0


def test_fallida_se_vuelve_a_pagar_solo_con_una_version_nueva_y_otra_aprobacion(
    motor_migrate, motor_app, e, riel
):
    riel.al_ordenar = EstadoRiel.FALLIDA
    o = emitida(motor_migrate, e)
    correr(motor_migrate, motor_app, pagos.TRABAJO_ORDENAR, e.cons)
    assert orden(motor_migrate, o)["estado"] == "fallida"
    # Emitir otra vez la misma versión no se puede.
    with pytest.raises(DBAPIError):
        llamar(motor_migrate, e, e.adm, EMITIR, orden=o, version=1)
    llamar(motor_migrate, e, e.adm, MODIFICAR, orden=o, version=1, concepto="Reintento SINTETICO")
    assert orden(motor_migrate, o)["estado"] == "en_revision"
    assert len(ejecuciones(motor_migrate, o)) == 1


def test_un_segundo_resultado_definitivo_se_rechaza(motor_migrate, motor_app, e, riel):
    o = emitida(motor_migrate, e)
    correr(motor_migrate, motor_app, pagos.TRABAJO_ORDENAR, e.cons)
    [x] = ejecuciones(motor_migrate, o)
    with (
        pytest.raises(DBAPIError) as error,
        transaccion(motor_app, persona_id=None, consorcio_id=e.cons) as c,
    ):
        c.execute(text("select nucleo.registrar_resultado_pago(:x, 'fallida', 'consultar',"
                       " null, null)"), {"x": x["id"]})  # fmt: skip
    assert error.value.orig.diag.message_hint == "resultado_repetido"
    assert orden(motor_migrate, o)["estado"] == "pagada"


def test_solo_un_trabajo_registra_resultados(motor_migrate, motor_app, e):
    o = emitida(motor_migrate, e)
    [x] = ejecuciones(motor_migrate, o)
    for funcion in ("select nucleo.registrar_resultado_pago(:x, 'pagada', 'ordenar', null, null)",
                    "select * from nucleo.pedido_de_pago(:x)"):  # fmt: skip
        with (
            pytest.raises(DBAPIError) as error,
            transaccion(motor_app, persona_id=e.adm, consorcio_id=e.cons) as c,
        ):
            c.execute(text(funcion), {"x": x["id"]})
        assert error.value.orig.diag.sqlstate == "42501"
    # Control: el mismo pedido como trabajo sí lee.
    with transaccion(motor_app, persona_id=None, consorcio_id=e.cons) as c:
        assert (
            c.execute(
                text("select estado_orden from nucleo.pedido_de_pago(:x)"), {"x": x["id"]}
            ).scalar_one()
            == "en_ejecucion"
        )
    assert orden(motor_migrate, o)["estado"] == "en_ejecucion"


def test_la_ejecucion_y_sus_resultados_solo_se_agregan(motor_migrate, motor_app, e, riel):
    o = emitida(motor_migrate, e)
    correr(motor_migrate, motor_app, pagos.TRABAJO_ORDENAR, e.cons)
    for sentencia in (
        "update nucleo.ejecucion_orden_pago set importe_centavos = 1 where orden_id = :o",
        "delete from nucleo.resultado_ejecucion_orden_pago where ejecucion_id in"
        " (select id from nucleo.ejecucion_orden_pago where orden_id = :o)",
    ):
        with pytest.raises(DBAPIError) as error, motor_migrate.begin() as c:
            c.execute(text(sentencia), {"o": o})
        assert error.value.orig.diag.message_hint == "historia_inmutable"


def test_ordenar_y_verificar_nunca_leen_el_mismo_pago_a_la_vez(motor_migrate, motor_app, e):
    o = emitida(motor_migrate, e)
    [x] = ejecuciones(motor_migrate, o)
    leer = text("select estado_orden from nucleo.pedido_de_pago(:x)")
    with transaccion(motor_app, persona_id=None, consorcio_id=e.cons) as primero:
        assert primero.execute(leer, {"x": x["id"]}).scalar_one() == "en_ejecucion"
        # Mientras el primero la tiene (como ordenar mientras llama al riel), otro no la lee.
        with (
            pytest.raises(DBAPIError) as error,
            transaccion(motor_app, persona_id=None, consorcio_id=e.cons) as segundo,
        ):
            segundo.execute(text("set local lock_timeout = '200ms'"))
            segundo.execute(leer, {"x": x["id"]})
        assert error.value.orig.diag.sqlstate == "55P03"
    # Control: confirmado el primero, el segundo lee.
    with transaccion(motor_app, persona_id=None, consorcio_id=e.cons) as segundo:
        assert segundo.execute(leer, {"x": x["id"]}).scalar_one() == "en_ejecucion"


def test_si_verificar_llega_antes_que_ordenar_no_se_paga(motor_migrate, motor_app, e, riel):
    o = emitida(motor_migrate, e)
    # La cola atrasada: verificar corre antes que ordenar y el riel no conoce la clave.
    correr(motor_migrate, motor_app, pagos.TRABAJO_VERIFICAR, e.cons)
    assert orden(motor_migrate, o)["estado"] == "fallida"
    assert correr(motor_migrate, motor_app, pagos.TRABAJO_ORDENAR, e.cons) == [
        {"estado": "sin_cambios", "orden": "fallida"}
    ]
    assert riel.ordenes == 0


def test_una_referencia_larga_del_riel_no_impide_registrar_el_pago(
    motor_migrate, motor_app, e, riel
):
    o = emitida(motor_migrate, e)
    [x] = ejecuciones(motor_migrate, o)
    riel.pagos[x["clave"]] = RespuestaRiel(EstadoRiel.PAGADA, referencia="  \x00" + "R" * 500)
    correr(motor_migrate, motor_app, pagos.TRABAJO_ORDENAR, e.cons)
    assert orden(motor_migrate, o)["estado"] == "pagada"
    with motor_migrate.connect() as c:
        referencia = c.execute(
            text("select referencia from nucleo.resultado_ejecucion_orden_pago"
                 " where ejecucion_id = :x"), {"x": x["id"]},
        ).scalar_one()  # fmt: skip
    assert referencia == "R" * pagos.LARGO_TEXTO_RIEL


def _tablas(motor) -> int:
    with motor.connect() as c:
        return c.execute(
            text("select count(*) from pg_class c join pg_namespace n on n.oid = c.relnamespace"
                 " where n.nspname = 'nucleo' and c.relname in ('ejecucion_orden_pago',"
                 " 'resultado_ejecucion_orden_pago')")
        ).scalar_one()  # fmt: skip


def test_0051_ida_y_vuelta(base_aparte):
    cfg, motor = base_aparte
    command.upgrade(cfg, "0051")
    assert _tablas(motor) == 2
    command.downgrade(cfg, "0050")
    assert _tablas(motor) == 0
    with motor.connect() as c:
        emitir = c.execute(
            text("select pg_get_functiondef('nucleo.emitir_orden_pago(uuid, integer, uuid, text,"
                 " uuid)'::regprocedure)")
        ).scalar_one()  # fmt: skip
    assert "crear_ejecucion_orden_pago" not in emitir
    command.upgrade(cfg, "0051")  # control: vuelve a subir limpio
    assert _tablas(motor) == 2
