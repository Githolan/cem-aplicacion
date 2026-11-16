"""Aviso diario por correo a quien tiene algo para aprobar, pagar o conciliar (informe de fricción,
P-17; pregunta 7 del propietario, 13-11-2026), y a quien tiene que revisar lo que el extracto no
explica (comentario 29 del propietario, 09-11-2026; el correo lo confirmó el 16-11-2026).

**Qué dice.** Que hay algo para hacer, en qué edificio y cuántas órdenes o pagos, con el enlace a la
bandeja. **Nunca** un importe, una cuenta, un CBU, un proveedor ni un concepto: la carga de
`nucleo.pendientes_para_avisar` (`0098`) ni siquiera los trae. Lo demás se ve en Consorcio en Mano.

**A quién.** A las personas con `egresos.preparar_orden` o `egresos.aprobar_orden` vigente en el
edificio, y solo si la bandeja les muestra algo para aprobar, pagar o conciliar: la base decide qué
puede hacer cada una con la misma función que la bandeja (`acciones_de_ordenes_de`) y acá se elige
el paso con la misma regla (`circuito_pagos.para_avisar`). Quien preparó una orden no recibe el
aviso de aprobarla, porque no puede. Una persona con pendientes en varios edificios recibe un
solo correo.

**Lo que el extracto no explica** va en un renglón aparte, solo con cuántos movimientos son: sin
fecha, importe, número de operación ni cuenta. Lo recibe quien lo ve en la bandeja: quien concilia
y la administración (pregunta 8, opción c), con la regla de `exigir_lectura_extracto` que repite
`nucleo.extracto_sin_explicar_para_avisar` (`0106`). La cuenta sale de la misma comparación que la
bandeja (`extracto.movimientos_sin_pago`). Va en el mismo correo y con el mismo tope: nadie recibe
un correo de más por esto.

**Cuándo y cuántos.** Una corrida del planificador por día a `CEM_AVISOS_HORA_UTC`; con
`CEM_AVISOS_TOPE_DIARIO` = N, N corridas por día repartidas desde esa hora, y la base
(`registrar_aviso_pendientes`) corta en N avisos por persona en 24 horas contadas desde los envíos
reales: ni mover la hora ni dos corridas atrasadas que salen juntas mandan de más. Con 0 no se
programa ninguna corrida.

El correo sale por el camino de siempre (`correo.solicitar`, outbox, `notificar.correo`), en la
misma transacción que registra el aviso: o quedan los dos o ninguno.
"""

import logging
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any
from uuid import UUID

from cem_contracts.egresos import EstadoOrdenPago
from sqlalchemy import Connection, text

from app.config import Config, cargar
from app.consorcios import circuito_pagos, extracto
from app.plataforma import cola, correo
from app.plataforma.planificador import periodica

log = logging.getLogger("cem.avisos")

TIPO = "pagos.avisar_pendientes"
DIA = timedelta(days=1)
MARGEN = timedelta(hours=1)
"""Lo que el tope deja de contar de las 24 horas: la corrida de ayer pudo terminar algo después
que la de hoy (cola ocupada, reintento). Nunca pasa de medio período entre corridas."""
RUTA_BANDEJA = "/pagos"
"""La bandeja de pagos del frontend (`RUTA_PAGOS` en `frontend/src/pagos/rutas.ts`)."""

DATOS_DE_LA_ORDEN = ("consorcio_id", "consorcio", "persona_id", "correo", "quien_prepara", "estado")
"""Lo que `pendientes_para_avisar` agrega a las acciones de cada orden."""

ASUNTO = "Hay algo para hacer en Consorcio en Mano"
TEXTO = """Hola:

Hay algo para hacer en Consorcio en Mano.

{renglones}

Para verlo, entrar a Consorcio en Mano:

{enlace}

Este aviso sale como máximo {veces} por día. No lleva importes ni datos de cuentas: se ven al
entrar.
"""
QUE_HACER = {
    "aprobar": ("una orden espera su aprobación", "{n} órdenes esperan su aprobación"),
    "pagar": ("un pago para hacer", "{n} pagos para hacer"),
    "conciliar": ("un pago para conciliar", "{n} pagos para conciliar"),
}
"""El orden de los renglones es el del circuito."""
SIN_EXPLICAR = (
    "hay un movimiento del extracto sin explicar para revisar",
    "hay {n} movimientos del extracto sin explicar para revisar",
)


class ErrorConfigAvisos(RuntimeError):
    """Falta `CEM_ORIGEN_PUBLICO` en `cem-trabajos`. Se reintenta: se arregla configurando."""


@dataclass
class Aviso:
    correo: str
    pendientes: dict[str, Counter[str]] = field(default_factory=lambda: defaultdict(Counter))
    """Edificio → qué hay que hacer → cuántas órdenes."""
    sin_explicar: dict[str, int] = field(default_factory=dict)
    """Edificio → cuántos movimientos del extracto no explica ningún pago."""


def agrupar(filas: list[dict[str, Any]]) -> dict[UUID, Aviso]:
    """Cada persona con algo para avisar, con lo que tiene que hacer en cada edificio."""
    avisos: dict[UUID, Aviso] = {}
    for fila in filas:
        datos = dict(fila)
        persona, nombre, direccion = datos["persona_id"], datos["consorcio"], datos["correo"]
        estado, prepara = EstadoOrdenPago(datos["estado"]), datos["quien_prepara"]
        for clave in DATOS_DE_LA_ORDEN + ("orden_id",):
            datos.pop(clave)
        que = circuito_pagos.para_avisar(estado, circuito_pagos.AccionesOrden(**datos), prepara)
        if que is None:
            continue
        aviso = avisos.setdefault(persona, Aviso(direccion))
        aviso.pendientes[_edificio(nombre)][que] += 1
    return avisos


def _edificio(nombre: str) -> str:
    return " ".join(nombre.split())


def sumar_sin_explicar(avisos: dict[UUID, Aviso], filas: list[dict[str, Any]]) -> None:
    """Suma a cada lector del extracto cuántos movimientos no explica ningún pago en cada
    edificio. Un edificio donde la comparación los explica todos no suma nada."""
    for fila in filas:
        movimientos = extracto.movimientos_guardados([
            {**m, "movimiento_id": UUID(m["movimiento_id"]),
             "fecha": date.fromisoformat(m["fecha"])}
            for m in fila["movimientos"]
        ])  # fmt: skip
        pagos = [
            {**f, "pago_id": UUID(f["pago_id"]),
             "fecha_operacion": date.fromisoformat(f["fecha_operacion"])}
            for f in fila["pagos"]
        ]  # fmt: skip
        n = len(extracto.movimientos_sin_pago(movimientos, pagos))
        if n == 0:
            continue
        for lector in fila["lectores"]:
            aviso = avisos.setdefault(UUID(lector["persona_id"]), Aviso(lector["correo"]))
            aviso.sin_explicar[_edificio(fila["consorcio"])] = n


def _cuantas(que: str, n: int) -> str:
    una, varias = QUE_HACER[que]
    return una if n == 1 else varias.format(n=n)


def _sin_explicar(n: int) -> str:
    uno, varios = SIN_EXPLICAR
    return uno if n == 1 else varios.format(n=n)


def _enumerar(partes: list[str]) -> str:
    return partes[0] if len(partes) == 1 else ", ".join(partes[:-1]) + " y " + partes[-1]


def _renglones(aviso: Aviso, edificio: str) -> list[str]:
    """Las órdenes del edificio en un renglón y lo que el extracto no explica en otro."""
    renglones = []
    cuantas = aviso.pendientes.get(edificio, Counter())
    partes = [_cuantas(q, cuantas[q]) for q in QUE_HACER if cuantas[q] > 0]
    if partes:
        renglones.append(f"- {edificio}: {_enumerar(partes)}.")
    if edificio in aviso.sin_explicar:
        renglones.append(f"- {edificio}: {_sin_explicar(aviso.sin_explicar[edificio])}.")
    return renglones


def texto(aviso: Aviso, enlace: str, tope: int) -> str:
    edificios = sorted(set(aviso.pendientes) | set(aviso.sin_explicar))
    renglones = "\n".join(r for edificio in edificios for r in _renglones(aviso, edificio))
    veces = "una vez" if tope == 1 else f"{tope} veces"
    return TEXTO.format(renglones=renglones, enlace=enlace, veces=veces)


def periodo(tope: int) -> timedelta:
    return DIA / tope


def desfase(config: Config) -> timedelta:
    """La primera corrida del día a la hora configurada; las demás, cada `periodo`."""
    hora = timedelta(hours=config.avisos_hora_utc.hour, minutes=config.avisos_hora_utc.minute)
    return hora % periodo(config.avisos_tope_diario)


def avisar(conn: Connection, ventana: datetime, config: Config) -> dict[str, int]:
    """Publica un correo por persona con pendientes, hasta el tope. Devuelve las cuentas."""
    if not config.origen_publico:
        raise ErrorConfigAvisos("CEM_ORIGEN_PUBLICO sin configurar: el aviso no tiene enlace")
    plazo = timedelta(hours=config.pagos_horas_tomar)
    filas = conn.execute(
        text("select * from nucleo.pendientes_para_avisar(:plazo)"), {"plazo": plazo}
    ).mappings().all()  # fmt: skip
    avisos = agrupar(list(filas))
    extractos = conn.execute(
        text("select * from nucleo.extracto_sin_explicar_para_avisar()")
    ).mappings().all()  # fmt: skip
    sumar_sin_explicar(avisos, [dict(f) for f in extractos])
    enlace = f"{config.origen_publico}{RUTA_BANDEJA}"
    margen = min(MARGEN, periodo(config.avisos_tope_diario) / 2)
    enviados = 0
    for persona, aviso in avisos.items():
        admite = conn.execute(
            text("select nucleo.registrar_aviso_pendientes(:p, :v, :t, :m)"),
            {"p": persona, "v": ventana, "t": config.avisos_tope_diario, "m": margen},
        ).scalar_one()  # fmt: skip
        if not admite:
            continue
        correo.solicitar(
            conn, para=aviso.correo, asunto=ASUNTO,
            texto=texto(aviso, enlace, config.avisos_tope_diario),
        )  # fmt: skip
        enviados += 1
    return {"personas_con_pendientes": len(avisos), "avisos": enviados,
            "salteados_por_tope": len(avisos) - enviados}  # fmt: skip


@cola.REGISTRO.tipo(TIPO)
def avisar_pendientes(conn: Connection, trabajo: cola.Trabajo) -> dict[str, int]:
    config = cargar()
    if config.avisos_tope_diario == 0:
        # Una corrida que quedó encolada antes de apagar los avisos no manda nada.
        return {"personas_con_pendientes": 0, "avisos": 0, "salteados_por_tope": 0}
    resultado = avisar(conn, datetime.fromisoformat(trabajo.carga["ventana"]), config)
    log.info("avisos de pendientes: %s", resultado, extra={"campos": resultado})
    return resultado


def programar(config: Config) -> None:
    if config.avisos_tope_diario > 0:
        periodica(TIPO, periodo(config.avisos_tope_diario), desfase=desfase(config))


programar(cargar())
