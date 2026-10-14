"""Encabezado `Idempotency-Key`: reintento seguro de los pedidos que crean algo (documento 13,
§7.4 y §11.1).

Toda ruta que crea dinero, cargos o expedientes declara `IdempotenciaDep` (el encabezado queda
obligatorio y figura en el OpenAPI) y hace su efecto dentro de `responder_una_vez()`:

    @router.post("/consorcios/{consorcio_id}/cargos", status_code=201)
    def crear_cargo(ctx: ContextoDep, motor: MotorDep, pedido: IdempotenciaDep, datos: ...):
        return responder_una_vez(motor, ctx, pedido, lambda conn: (201, crear(conn, datos)))

`crear` devuelve un modelo del contrato: se serializa con Pydantic, así los importes `Decimal`
salen como texto y nunca como `float`.

Qué pasa con cada pedido, según la clave de la persona en el consorcio, el método y la ruta:

- **Clave nueva**: corre el efecto y guarda la respuesta en `plataforma.idempotencia` en la
  misma transacción. Si el efecto falla no queda nada: ni el efecto ni la respuesta, y el
  cliente puede reintentar con la misma clave.
- **Misma clave y mismo pedido** (huella SHA-256 de consulta y cuerpo): devuelve la respuesta
  guardada, con el mismo estado y el mismo texto, sin repetir el efecto; lleva el encabezado
  `Idempotent-Replayed: true`.
- **Misma clave y otro pedido**: 422 `idempotencia_reutilizada`.
- **Dos pedidos a la vez con la misma clave**: un bloqueo consultivo de transacción los
  serializa. El segundo espera a que el primero termine y recibe su respuesta guardada; si la
  espera supera `ESPERA_EN_CURSO_MS`, 409 `idempotencia_en_curso` y el cliente reintenta.
- **Clave vencida** (`VIGENCIA`, 24 horas): se borra y el pedido cuenta como nuevo. La tarea
  diaria `plataforma.purgar_cola` borra además todas las vencidas.

Los códigos 409 y 422 siguen el borrador de la IETF `draft-ietf-httpapi-idempotency-key-header`.
Solo se guardan respuestas 2xx: un error sale como excepción y deshace la transacción.
"""

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated, Any

from cem_contracts.errores import CodigoError
from fastapi import Depends, Header, Request, Response
from psycopg.errors import LockNotAvailable
from pydantic import BaseModel
from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import OperationalError

from app.contexto import Contexto
from app.db import transaccion
from app.errores import ErrorApi

ENCABEZADO = "Idempotency-Key"
ENCABEZADO_REPETIDA = "Idempotent-Replayed"
"""Marca la respuesta que sale de lo guardado y no de una ejecución nueva."""
VIGENCIA = timedelta(hours=24)
"""Cuánto se guarda la respuesta (§7.4)."""
ESPERA_EN_CURSO_MS = 2000
"""Cuánto espera un pedido a otro con la misma clave antes de responder 409."""
LARGO_MAXIMO_CLAVE = 255
"""El mismo tope que la restricción `check` de la migración 0003."""
FORMATO_CLAVE = rf"^[\x21-\x7e]{{1,{LARGO_MAXIMO_CLAVE}}}$"
"""ASCII visible, sin espacios: un UUID v4 generado por el cliente es lo recomendado."""


@dataclass(frozen=True)
class PedidoIdempotente:
    clave: str
    metodo: str
    ruta: str
    huella: bytes


async def pedido_idempotente(
    request: Request,
    clave: Annotated[
        str,
        Header(
            alias=ENCABEZADO,
            pattern=FORMATO_CLAVE,
            description="Clave única por intento lógico (UUID v4). Un reintento repite la misma"
            " clave y el mismo cuerpo y recibe la respuesta guardada durante 24 horas.",
        ),
    ],
) -> PedidoIdempotente:
    cuerpo = await request.body()
    huella = hashlib.sha256(request.scope["query_string"] + b"\0" + cuerpo).digest()
    return PedidoIdempotente(clave, request.method, request.url.path, huella)


IdempotenciaDep = Annotated[PedidoIdempotente, Depends(pedido_idempotente)]

Efecto = Callable[[Connection], tuple[int, BaseModel]]
"""Recibe la conexión con el ámbito fijado y devuelve el estado 2xx y el modelo de la respuesta.

Los errores se señalan con `ErrorApi` (u otra excepción): deshacen el efecto y no se guardan.
"""

_CLAVE = (
    "consorcio_id = :consorcio and persona_id = :persona"
    " and metodo = :metodo and ruta = :ruta and clave = :clave"
)


def responder_una_vez(
    motor: Engine, ctx: Contexto, pedido: PedidoIdempotente, efecto: Efecto
) -> Response:
    claves = {
        "consorcio": ctx.consorcio_id,
        "persona": ctx.persona_id,
        "metodo": pedido.metodo,
        "ruta": pedido.ruta,
        "clave": pedido.clave,
    }
    with transaccion(motor, persona_id=ctx.persona_id, consorcio_id=ctx.consorcio_id) as conn:
        _esperar_turno(conn, claves)
        conn.execute(text(f"delete from plataforma.idempotencia where {_CLAVE}"
                          " and vence_en <= now()"), claves)  # fmt: skip
        guardada = conn.execute(
            text(
                "select huella, estado_http, respuesta::text as respuesta"
                f" from plataforma.idempotencia where {_CLAVE}"
            ),
            claves,
        ).one_or_none()
        if guardada is not None:
            if guardada.huella != pedido.huella:
                raise ErrorApi(
                    422,
                    CodigoError.IDEMPOTENCIA_REUTILIZADA,
                    "Esa clave de reintento ya se usó con otros datos. Generá una nueva.",
                )
            return Response(
                guardada.respuesta,
                status_code=guardada.estado_http,
                media_type="application/json",
                headers={ENCABEZADO_REPETIDA: "true"},
            )
        estado, cuerpo = efecto(conn)
        if not 200 <= estado <= 299:
            raise RuntimeError(f"un efecto idempotente devolvió {estado}: los errores se lanzan")
        texto = cuerpo.model_dump_json()
        conn.execute(
            text(
                "insert into plataforma.idempotencia (consorcio_id, persona_id, metodo, ruta,"
                " clave, huella, estado_http, respuesta, vence_en) values (:consorcio, :persona,"
                " :metodo, :ruta, :clave, :huella, :estado, :respuesta, now() + :vigencia)"
            ),
            claves
            | {
                "huella": pedido.huella,
                "estado": estado,
                "respuesta": texto,
                "vigencia": VIGENCIA,
            },
        )
    return Response(texto, status_code=estado, media_type="application/json")


def _esperar_turno(conn: Connection, claves: dict[str, Any]) -> None:
    """Bloqueo consultivo de la clave hasta el final de la transacción, con espera acotada.

    Un pedido igual que ya tiene el bloqueo termina (y guarda su respuesta) o se deshace antes
    de que este siga; la consulta siguiente ya ve lo que el otro confirmó (READ COMMITTED).
    """
    previo = conn.execute(text("select current_setting('lock_timeout')")).scalar_one()
    conn.execute(
        text("select set_config('lock_timeout', :t, true)"), {"t": f"{ESPERA_EN_CURSO_MS}ms"}
    )
    try:
        conn.execute(
            text(
                "select pg_advisory_xact_lock(hashtextextended('idempotencia:' || concat_ws("
                "'|', cast(:consorcio as text), cast(:persona as text), cast(:metodo as text),"
                " cast(:ruta as text), cast(:clave as text)),"
                " 0))"
            ),
            claves,
        )
    except OperationalError as e:
        if not isinstance(e.orig, LockNotAvailable):
            raise
        raise ErrorApi(
            409,
            CodigoError.IDEMPOTENCIA_EN_CURSO,
            "Ese pedido todavía se está procesando. Probá de nuevo en unos segundos.",
        ) from e
    conn.execute(text("select set_config('lock_timeout', :t, true)"), {"t": previo})
