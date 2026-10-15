"""Eventos de auditoría encadenada (documento 13, §8.4).

`auditoria.eventos` solo admite agregar (cem_app no tiene UPDATE ni DELETE) y el disparador
`auditoria.encadenar` calcula `hash_previo` y `hash`: los valores que se manden se ignoran.
La fila va al consorcio del ámbito de la transacción (RLS). Nunca lleva datos de contacto: en
`despues` van identificadores, no correos ni nombres, porque el registro no se puede borrar.
"""

import json
from uuid import UUID

from sqlalchemy import Connection, text

ORIGENES = frozenset({"api", "trabajos", "planificador", "soporte"})


def registrar(
    conn: Connection,
    *,
    consorcio_id: UUID,
    actor: UUID | None,
    origen: str,
    accion: str,
    entidad: str,
    entidad_id: UUID | None,
    despues: dict[str, object] | None = None,
    ip: str | None = None,
) -> None:
    if origen not in ORIGENES:
        raise ValueError(f"origen de auditoría desconocido: {origen}")
    conn.execute(
        text(
            "insert into auditoria.eventos (consorcio_id, actor_persona_id, origen, accion,"
            " entidad, entidad_id, despues, ip, hash_previo, hash)"
            " values (:c, :a, :o, :ac, :e, :ei, cast(:d as jsonb), cast(:ip as inet), '', '')"
        ),
        {
            "c": consorcio_id,
            "a": actor,
            "o": origen,
            "ac": accion,
            "e": entidad,
            "ei": entidad_id,
            "d": json.dumps(despues, default=str) if despues is not None else None,
            "ip": ip,
        },
    )
