"""Transacciones con el ámbito fijado para la seguridad por fila (documento 13, §7.2 y §8.5).

Toda consulta de negocio pasa por `transaccion()`: abre la transacción y fija con
`set_config(..., true)` (equivalente a `SET LOCAL`) la persona, el consorcio y la lista de
consorcios de la persona. Las políticas de PostgreSQL filtran con esos valores; si faltan,
no devuelven filas (fallan cerradas).

Sin `CEM_DATABASE_URL` la API arranca igual (`/health/deps` lo informa) y no hay motor:
`transaccion()` lanza `SinBase` recién cuando un pedido necesita la base, así lo que se
rechaza antes de consultarla (sin sesión, sin la cookie del enlace) responde lo mismo que con
base. `app/errores.py` convierte `SinBase` y `OperationalError` en 503.
"""

from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from uuid import UUID

from sqlalchemy import Connection, Engine, create_engine, text

ESPERA_CONEXION_S = 3
"""Tope para abrir una conexión: si PostgreSQL no responde, la petición falla en vez de colgarse."""


class SinBase(RuntimeError):
    """El proceso no tiene motor: `CEM_DATABASE_URL` sin configurar."""


def crear_motor(url: str) -> Engine:
    # `hide_parameters`: el texto de un error de la base no lleva los valores de la sentencia
    # (una cuenta destino, un correo), que de otro modo llegarían al registro con la traza.
    return create_engine(
        url,
        pool_pre_ping=True,
        hide_parameters=True,
        connect_args={"connect_timeout": ESPERA_CONEXION_S},
    )


def fijar_ambito(
    conn: Connection,
    *,
    persona_id: UUID | None,
    consorcio_id: UUID | None = None,
    consorcios_persona: Iterable[UUID] = (),
) -> None:
    """Fija el ámbito hasta el final de la transacción en curso. Se puede volver a llamar."""
    lista = "{" + ",".join(str(c) for c in consorcios_persona) + "}"
    conn.execute(
        text(
            "select set_config('app.persona_id', :persona, true),"
            " set_config('app.consorcio_id', :consorcio, true),"
            " set_config('app.consorcios_persona', :lista, true)"
        ),
        {
            "persona": str(persona_id) if persona_id else "",
            "consorcio": str(consorcio_id) if consorcio_id else "",
            "lista": lista,
        },
    )


@contextmanager
def transaccion(
    motor: Engine | None,
    *,
    persona_id: UUID | None,
    consorcio_id: UUID | None = None,
    consorcios_persona: Iterable[UUID] = (),
) -> Iterator[Connection]:
    """`persona_id` es None solo en los procesos sin persona: trabajos y planificador."""
    if motor is None:
        raise SinBase("CEM_DATABASE_URL sin configurar")
    with motor.begin() as conn:
        fijar_ambito(
            conn,
            persona_id=persona_id,
            consorcio_id=consorcio_id,
            consorcios_persona=consorcios_persona,
        )
        yield conn
