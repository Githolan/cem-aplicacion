"""Ejecución lógica del pago de una orden emitida, en `cem-trabajos` (migración `0051`).

**No hay riel real.** `RielDePago` es el contrato que cumplirá el adaptador de un banco o
proveedor de pagos cuando el propietario lo elija y autorice; hasta entonces no hay ninguno
configurado y los dos trabajos terminan sin hacer nada (`sin_riel`): la orden emitida queda En
ejecución y no se mueve dinero. Las pruebas usan un riel simulado, que vive solo en las pruebas.

1. `egresos.ordenar_pago` (lo encola `emitir_orden_pago`, un solo intento): manda el pedido con
   la clave de la ejecución. Si el riel no contesta, contesta algo que no se entiende o la llamada
   falla, el resultado es `incierta` y la orden pasa a En verificación. Nunca se reintenta.
2. `egresos.verificar_pago` (15 minutos después): **solo consulta**. Pagada o fallida, lo
   registra. Incierta, encola la consulta siguiente; después de `CONSULTAS_MAXIMAS` el trabajo
   muere y queda a la vista del monitor de la cola, para una persona. Si el riel dice que nunca
   recibió la clave, la orden queda Fallida: no se reenvía; para pagar hay que prepararla de
   nuevo, revisarla y aprobarla.

Los dos leen la ejecución con `nucleo.pedido_de_pago`, que la bloquea hasta confirmar: nunca
corren a la vez sobre el mismo pago, ni siquiera con varios trabajadores.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import Connection, text

from app.plataforma import cola

TRABAJO_ORDENAR = "egresos.ordenar_pago"
TRABAJO_VERIFICAR = "egresos.verificar_pago"
ESPERA_ENTRE_CONSULTAS = timedelta(minutes=15)
CONSULTAS_MAXIMAS = 12
"""Unas 3 horas de consultas inciertas: después, una persona mira el caso."""
INTENTOS_VERIFICAR = 3
"""Ante una falla técnica de la consulta; igual al de la migración `0051`."""


class EstadoRiel(StrEnum):
    PAGADA = "pagada"
    FALLIDA = "fallida"
    INCIERTA = "incierta"
    DESCONOCIDA = "desconocida"
    """Solo al consultar: el riel no tiene ningún pedido con esa clave."""


@dataclass(frozen=True)
class PedidoPago:
    clave: UUID
    cuenta_destino: str
    importe_centavos: int
    moneda: str


@dataclass(frozen=True)
class RespuestaRiel:
    estado: EstadoRiel
    referencia: str | None = None
    motivo: str | None = None


class RielDePago(Protocol):
    """Lo que tiene que cumplir un riel para conectarse. Son requisitos, no sugerencias: sin
    ellos, ningún adaptador se conecta.

    - `ordenar` es idempotente por `pedido.clave`: el mismo pedido dos veces paga una sola vez y
      contesta lo mismo. Si no sabe, contesta `INCIERTA`.
    - `ordenar` tiene un plazo total de 60 segundos que hace cumplir el propio adaptador (cliente
      con plazo, sin reintentos después): pasado el plazo no queda ningún envío en curso. El
      bloqueo de la ejecución dura lo que la conexión a la base; un envío que siguiera después de
      cortarse esa conexión podría pagar algo que verificar ya dio por fallido.
    - `consultar` no mueve dinero. `DESCONOCIDA` es definitiva y consistente: el riel nunca recibió
      esa clave y, desde esa respuesta, la rechaza para siempre. Un riel que acepta un pedido y
      todavía no lo informa no puede contestar `DESCONOCIDA`, sino `INCIERTA`.
    """

    def ordenar(self, pedido: PedidoPago) -> RespuestaRiel: ...

    def consultar(self, clave: UUID) -> RespuestaRiel: ...


_riel: RielDePago | None = None


def configurar_riel(riel: RielDePago | None) -> None:
    """Conecta el riel. Hoy no lo llama ningún proceso: no hay riel autorizado."""
    global _riel
    _riel = riel


@dataclass(frozen=True)
class _Pedido:
    clave: UUID
    cuenta_destino: str
    importe_centavos: int
    moneda: str
    estado_orden: str
    resultado_definitivo: str | None


def _pedido(conn: Connection, ejecucion: UUID) -> _Pedido:
    fila = (
        conn.execute(
            text(
                "select clave, cuenta_destino, importe_centavos, moneda, estado_orden,"
                " resultado_definitivo from nucleo.pedido_de_pago(:e)"
            ),
            {"e": ejecucion},
        )
        .mappings()
        .one_or_none()
    )
    if fila is None:
        raise cola.ErrorDefinitivo(f"la ejecución {ejecucion} no existe en el consorcio")
    return _Pedido(**fila)


LARGO_TEXTO_RIEL = 200
"""Lo que guarda la base de la referencia y el motivo del riel (0051)."""


def _texto(valor: str | None) -> str | None:
    """El texto del riel como lo guarda la base: sin espacios de más, vacío como nulo y cortado al
    largo de la columna. Un pago hecho nunca deja de registrarse por cómo vino escrita su
    referencia."""
    if valor is None:
        return None
    limpio = valor.replace("\x00", "").strip()
    return limpio[:LARGO_TEXTO_RIEL] or None


def _registrar(conn: Connection, ejecucion: UUID, r: RespuestaRiel, origen: str) -> str:
    return conn.execute(
        text("select nucleo.registrar_resultado_pago(:e, :r, :o, :ref, :m)"),
        {
            "e": ejecucion,
            "r": r.estado.value,
            "o": origen,
            "ref": _texto(r.referencia),
            "m": _texto(r.motivo),
        },
    ).scalar_one()


@cola.REGISTRO.tipo(TRABAJO_ORDENAR, maximo_intentos=1)
def ordenar_pago(conn: Connection, trabajo: cola.Trabajo) -> dict[str, Any]:
    if _riel is None:
        return {"estado": "sin_riel"}
    ejecucion = UUID(trabajo.carga["ejecucion"])
    p = _pedido(conn, ejecucion)
    if p.resultado_definitivo is not None or p.estado_orden != "en_ejecucion":
        return {"estado": "sin_cambios", "orden": p.estado_orden}
    pedido = PedidoPago(p.clave, p.cuenta_destino, p.importe_centavos, p.moneda)
    try:
        respuesta = _riel.ordenar(pedido)
    except Exception as e:  # noqa: BLE001 (cualquier falla de la llamada deja el pago incierto)
        motivo = f"la llamada falló: {type(e).__name__}"
        respuesta = RespuestaRiel(EstadoRiel.INCIERTA, motivo=motivo)
    if respuesta.estado is EstadoRiel.DESCONOCIDA:
        motivo = "el riel contestó desconocida al ordenar"
        respuesta = RespuestaRiel(EstadoRiel.INCIERTA, motivo=motivo)
    return {"estado": _registrar(conn, ejecucion, respuesta, "ordenar")}


@cola.REGISTRO.tipo(
    TRABAJO_VERIFICAR, maximo_intentos=INTENTOS_VERIFICAR, retroceso_base=timedelta(minutes=5)
)
def verificar_pago(conn: Connection, trabajo: cola.Trabajo) -> dict[str, Any]:
    if _riel is None:
        return {"estado": "sin_riel"}
    ejecucion = UUID(trabajo.carga["ejecucion"])
    consulta = int(trabajo.carga["consulta"])
    p = _pedido(conn, ejecucion)
    if p.resultado_definitivo is not None:
        return {"estado": "resuelta", "orden": p.estado_orden}
    respuesta = _riel.consultar(p.clave)
    if respuesta.estado is EstadoRiel.DESCONOCIDA:
        respuesta = RespuestaRiel(EstadoRiel.FALLIDA, motivo="el riel nunca recibió la orden")
    if respuesta.estado is not EstadoRiel.INCIERTA:
        return {"estado": _registrar(conn, ejecucion, respuesta, "consultar")}
    if consulta >= CONSULTAS_MAXIMAS:
        raise cola.ErrorDefinitivo(f"el pago sigue incierto después de {consulta} consultas")
    estado = p.estado_orden
    if estado == "en_ejecucion":
        estado = _registrar(conn, ejecucion, respuesta, "consultar")
    cola.encolar(
        conn,
        TRABAJO_VERIFICAR,
        carga={"ejecucion": str(ejecucion), "consulta": consulta + 1},
        consorcio_id=trabajo.consorcio_id,
        clave_unica=f"{TRABAJO_VERIFICAR}:{ejecucion}:{consulta + 1}",
        disponible_desde=datetime.now(UTC) + ESPERA_ENTRE_CONSULTAS,
    )
    return {"estado": estado, "consulta": consulta}
