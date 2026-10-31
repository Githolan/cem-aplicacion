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
corren a la vez sobre el mismo pago, ni siquiera con varios trabajadores. Nadie espera ese
bloqueo más de `ESPERA_BLOQUEO`: vencida la espera, el trabajo falla sin llamar al riel ni
registrar nada (`PagoBloqueado`), que no es lo mismo que un pago incierto.

Cada paso deja una línea en el registro (`cem.pagos`) con la ejecución y el estado, nunca la
cuenta destino.
"""

import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID

from psycopg.errors import LockNotAvailable
from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError

from app.plataforma import cola

TRABAJO_ORDENAR = "egresos.ordenar_pago"
TRABAJO_VERIFICAR = "egresos.verificar_pago"
ESPERA_ENTRE_CONSULTAS = timedelta(minutes=15)
CONSULTAS_MAXIMAS = 12
"""Unas 3 horas de consultas inciertas: después, una persona mira el caso."""
INTENTOS_VERIFICAR = 3
"""Ante una falla técnica de la consulta; igual al de la migración `0051`."""
PLAZO_RIEL = timedelta(seconds=60)
"""El plazo total de `ordenar`: pasado `PedidoPago.vence_en`, el riel no envía nada."""
ESPERA_BLOQUEO = timedelta(seconds=5)
"""Lo más que un trabajo espera el bloqueo de una ejecución. Quien la tiene puede estar
llamando al riel hasta `PLAZO_RIEL`: verificar no lo espera, falla y la cola lo reintenta con
su retroceso (minutos)."""

log = logging.getLogger("cem.pagos")


class EstadoRiel(StrEnum):
    PAGADA = "pagada"
    FALLIDA = "fallida"
    INCIERTA = "incierta"
    DESCONOCIDA = "desconocida"
    """Solo al consultar: el riel no tiene ningún pedido con esa clave."""


@dataclass(frozen=True)
class PedidoPago:
    clave: UUID
    cuenta_destino: str = field(repr=False)
    """Fuera del `repr`: la cuenta no sale en registros ni en trazas de error."""
    importe_centavos: int
    moneda: str
    vence_en: datetime


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
    - Una clave es de un solo pedido: la misma clave con otra cuenta, otro importe u otra moneda
      se rechaza sin pagar (`FALLIDA`), nunca paga lo nuevo ni contesta lo viejo como si fuera
      lo pedido. Cada ejecución tiene su clave (`0051`, única).
    - `PAGADA` lleva la referencia del riel, que es con lo que se concilia. Sin referencia, o
      con una respuesta que no es una `RespuestaRiel` con un `EstadoRiel` y textos (`str` o
      `None`), el pago queda incierto y se consulta.
    - El `motivo` nunca lleva la cuenta destino; si la trae, se guarda tapada (`[cuenta]`).
    - La `referencia` es el identificador de la operación en el riel, nunca la cuenta destino:
      no se tapa porque es la clave para conciliar, así que una cuenta escrita ahí quedaría
      guardada entera (P3 114).
    - `ordenar` tiene un plazo total (`PLAZO_RIEL`, hasta `pedido.vence_en`) que hace cumplir el
      propio adaptador (cliente con plazo, sin reintentos después): pasado el plazo no envía y
      no queda ningún envío en curso. El
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
    cuenta_destino: str = field(repr=False)
    importe_centavos: int
    moneda: str
    estado_orden: str
    resultado_definitivo: str | None


class PagoBloqueado(Exception):
    """Otro trabajo tiene la ejecución desde hace más de `ESPERA_BLOQUEO`. No es un resultado
    del riel: no se llamó al riel ni se registró nada. Verificar se reintenta; ordenar, de un
    solo intento, muere y la verificación decide."""


def _pedido(conn: Connection, ejecucion: UUID) -> _Pedido:
    # Solo tomar el bloqueo tiene tope: lo que el trabajo registra después vuelve a la espera
    # de siempre, así un resultado ya obtenido del riel no se pierde por una espera corta.
    previo = conn.execute(text("select current_setting('lock_timeout')")).scalar_one()
    espera = f"{int(ESPERA_BLOQUEO.total_seconds() * 1000)}ms"
    conn.execute(text("select set_config('lock_timeout', :t, true)"), {"t": espera})
    try:
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
    except DBAPIError as e:
        if not isinstance(e.orig, LockNotAvailable):
            raise
        log.warning(
            "pago bloqueado por otro trabajo", extra={"campos": {"ejecucion": str(ejecucion)}}
        )
        raise PagoBloqueado(f"la ejecución {ejecucion} la tiene otro trabajo") from None
    conn.execute(text("select set_config('lock_timeout', :t, true)"), {"t": previo})
    if fila is None:
        raise cola.ErrorDefinitivo(f"la ejecución {ejecucion} no existe en el consorcio")
    return _Pedido(**fila)


LARGO_TEXTO_RIEL = 200
"""Lo que guarda la base de la referencia y el motivo del riel (0051)."""


CUENTA_EN_TEXTO = re.compile(r"(?<!\d)\d(?:[ .\-]?\d){21}(?!\d)")
"""Una cuenta destino escrita en el motivo del riel, también con espacios, puntos o guiones entre
los dígitos: se guarda tapada. La referencia no se tapa: es la clave para conciliar con el riel,
y un identificador de operación de 22 dígitos se perdería (P3 114)."""


def _texto(valor: str | None, tapar: bool = False) -> str | None:
    """El texto del riel como lo guarda la base: sin nulos, sin espacios de más, vacío como nulo
    y cortado al largo de la columna; con `tapar`, la cuenta tapada. Se tapa después de sacar los
    nulos (un nulo entre los dígitos la escondería) y antes de cortar (un corte en medio de la
    cuenta deja 21 dígitos que el patrón ya no reconoce). Un pago hecho nunca deja de registrarse
    por cómo vino escrita su referencia."""
    if valor is None:
        return None
    limpio = valor.replace("\x00", "")
    if tapar:
        limpio = CUENTA_EN_TEXTO.sub("[cuenta]", limpio)
    limpio = limpio.strip()
    return limpio[:LARGO_TEXTO_RIEL].rstrip() or None


def _textos_validos(respuesta: RespuestaRiel) -> bool:
    return all(isinstance(t, str | None) for t in (respuesta.referencia, respuesta.motivo))


def _entendida(respuesta: object) -> RespuestaRiel:
    """La respuesta como la exige `RielDePago`; si no, el pago queda incierto."""
    if (
        not isinstance(respuesta, RespuestaRiel)
        or not isinstance(respuesta.estado, EstadoRiel)
        or not _textos_validos(respuesta)
    ):
        return RespuestaRiel(EstadoRiel.INCIERTA, motivo="el riel contestó algo que no se entiende")
    if respuesta.estado is EstadoRiel.PAGADA and _texto(respuesta.referencia) is None:
        return RespuestaRiel(EstadoRiel.INCIERTA, motivo="el riel contestó pagada sin referencia")
    return respuesta


def _registrar(conn: Connection, ejecucion: UUID, r: RespuestaRiel, origen: str) -> str:
    log.info(
        "resultado del riel",
        extra={
            "campos": {"ejecucion": str(ejecucion), "resultado": r.estado.value, "origen": origen}
        },
    )
    return conn.execute(
        text("select nucleo.registrar_resultado_pago(:e, :r, :o, :ref, :m)"),
        {
            "e": ejecucion,
            "r": r.estado.value,
            "o": origen,
            "ref": _texto(r.referencia),
            "m": _texto(r.motivo, tapar=True),
        },
    ).scalar_one()


class ConsultaFallida(Exception):
    """La consulta al riel falló. La cola la reintenta (`INTENTOS_VERIFICAR`); el pago sigue
    como estaba."""


@cola.REGISTRO.tipo(TRABAJO_ORDENAR, maximo_intentos=1)
def ordenar_pago(conn: Connection, trabajo: cola.Trabajo) -> dict[str, Any]:
    ejecucion = UUID(trabajo.carga["ejecucion"])
    if _riel is None:
        log.warning("pago sin riel", extra={"campos": {"ejecucion": str(ejecucion)}})
        return {"estado": "sin_riel"}
    p = _pedido(conn, ejecucion)
    if p.resultado_definitivo is not None or p.estado_orden != "en_ejecucion":
        return {"estado": "sin_cambios", "orden": p.estado_orden}
    vence = datetime.now(UTC) + PLAZO_RIEL
    pedido = PedidoPago(p.clave, p.cuenta_destino, p.importe_centavos, p.moneda, vence)
    log.info("pago enviado al riel", extra={"campos": {"ejecucion": str(ejecucion)}})
    try:
        respuesta = _entendida(_riel.ordenar(pedido))
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
    ejecucion = UUID(trabajo.carga["ejecucion"])
    if _riel is None:
        log.warning("pago sin riel", extra={"campos": {"ejecucion": str(ejecucion)}})
        return {"estado": "sin_riel"}
    consulta = int(trabajo.carga["consulta"])
    p = _pedido(conn, ejecucion)
    if p.resultado_definitivo is not None:
        return {"estado": "resuelta", "orden": p.estado_orden}
    log.info(
        "consulta al riel", extra={"campos": {"ejecucion": str(ejecucion), "consulta": consulta}}
    )
    try:
        respuesta = _entendida(_riel.consultar(p.clave))
    except Exception as e:  # noqa: BLE001 (solo el tipo: el texto del riel no va al registro)
        raise ConsultaFallida(type(e).__name__) from None
    if respuesta.estado is EstadoRiel.DESCONOCIDA:
        respuesta = RespuestaRiel(EstadoRiel.FALLIDA, motivo="el riel nunca recibió la orden")
    if respuesta.estado is not EstadoRiel.INCIERTA:
        return {"estado": _registrar(conn, ejecucion, respuesta, "consultar")}
    if consulta >= CONSULTAS_MAXIMAS:
        log.error(
            "pago incierto sin más consultas",
            extra={"campos": {"ejecucion": str(ejecucion), "consulta": consulta}},
        )
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
