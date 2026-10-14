"""Outbox de eventos (documento 13, §12.2).

El servicio de dominio llama a `publicar` en la misma transacción que su cambio. El
despachador, que corre en `cem-planificador`, convierte cada evento en los trabajos de
sus rutas, con la clave única `<tipo>:<id del evento>`: despachar dos veces el mismo
evento no duplica ningún efecto.
"""

import json
import logging
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, Engine, text

from app.db import fijar_ambito
from app.plataforma import cola

log = logging.getLogger("cem.outbox")

RUTAS: dict[str, tuple[str, ...]] = {}
"""Evento → tipos de trabajo que dispara. Cada módulo agrega las suyas con `ruta()`."""


def ruta(evento: str, *tipos: str) -> None:
    RUTAS[evento] = RUTAS.get(evento, ()) + tipos


def publicar(
    conn: Connection,
    evento: str,
    *,
    carga: dict[str, Any] | None = None,
    consorcio_id: UUID | None = None,
) -> UUID:
    """Registra el evento dentro de la transacción de `conn`; el consorcio es el del ámbito."""
    return conn.execute(
        text(
            "insert into plataforma.outbox (evento, consorcio_id, carga)"
            " values (:e, :c, cast(:carga as jsonb)) returning id"
        ),
        {"e": evento, "c": consorcio_id, "carga": json.dumps(carga or {})},
    ).scalar_one()


def despachar(motor: Engine, cantidad: int = 100) -> int:
    """Convierte hasta `cantidad` eventos pendientes en trabajos. Devuelve cuántos tomó.

    Todo ocurre en una transacción: si algo falla, los eventos quedan sin despachar.
    """
    with motor.begin() as conn:
        eventos = conn.execute(
            text(
                "select id, evento, consorcio_id, carga"
                " from plataforma.tomar_eventos(:n) order by id"
            ),
            {"n": cantidad},
        ).mappings()
        eventos = list(eventos)
        for e in eventos:
            tipos = RUTAS.get(e["evento"], ())
            if not tipos:
                log.warning("evento sin rutas: %s %s", e["evento"], e["id"])
                continue
            # La RLS exige que el trabajo se encole en el consorcio del ámbito.
            fijar_ambito(conn, persona_id=None, consorcio_id=e["consorcio_id"])
            for tipo in tipos:
                cola.encolar(
                    conn,
                    tipo,
                    carga={"evento_id": str(e["id"]), "evento": e["evento"], **e["carga"]},
                    consorcio_id=e["consorcio_id"],
                    clave_unica=f"{tipo}:{e['id']}",
                )
        return len(eventos)
