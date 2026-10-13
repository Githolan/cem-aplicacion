"""Transacciones con el ámbito fijado para la seguridad por fila (documento 13, §7.2 y §8.5).

Toda consulta de negocio pasa por `transaccion()`: abre la transacción y fija con
`set_config(..., true)` (equivalente a `SET LOCAL`) la persona, el consorcio y la lista de
consorcios de la persona. Las políticas de PostgreSQL filtran con esos valores; si faltan,
no devuelven filas (fallan cerradas).
"""

from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from uuid import UUID

from sqlalchemy import Connection, Engine, create_engine, text


def crear_motor(url: str) -> Engine:
    return create_engine(url, pool_pre_ping=True)


@contextmanager
def transaccion(
    motor: Engine,
    *,
    persona_id: UUID,
    consorcio_id: UUID | None = None,
    consorcios_persona: Iterable[UUID] = (),
) -> Iterator[Connection]:
    lista = "{" + ",".join(str(c) for c in consorcios_persona) + "}"
    with motor.begin() as conn:
        conn.execute(
            text(
                "select set_config('app.persona_id', :persona, true),"
                " set_config('app.consorcio_id', :consorcio, true),"
                " set_config('app.consorcios_persona', :lista, true)"
            ),
            {
                "persona": str(persona_id),
                "consorcio": str(consorcio_id) if consorcio_id else "",
                "lista": lista,
            },
        )
        yield conn
