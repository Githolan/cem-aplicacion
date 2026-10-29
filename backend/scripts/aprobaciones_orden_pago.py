"""Fija cuántas aprobaciones necesita una orden de pago en un consorcio, en nombre del equipo
(decisión 2 del propietario, 29-10-2026; 0046).

Sin configuración rige una. El equipo carga 1 o 2 con el acta que lo respalda, ya registrada con
`nucleo.registrar_decision`: de asamblea o del Consejo, otro tipo de decisión se rechaza (P3 80,
0047). Rige desde hoy o una fecha posterior, nunca hacia atrás, y nunca se modifica una carga (un
cambio es otra con una fecha posterior). Las órdenes ya preparadas conservan la cantidad que
tenían. Corre dentro del contenedor de `cem-backend`, con su `CEM_DATABASE_URL` (`cem_app`, sujeto
a la RLS):

    python -m scripts.aprobaciones_orden_pago --consorcio <uuid> --cantidad 2 \\
        --desde 2026-11-01 --decision <uuid>

Imprime en JSON la carga.
"""

import argparse
import json
import sys
from datetime import date
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.config import cargar
from app.db import crear_motor, transaccion


def main(argumentos: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--consorcio", type=UUID, required=True)
    p.add_argument("--cantidad", type=int, choices=(1, 2), required=True)
    p.add_argument("--desde", type=date.fromisoformat, required=True)
    p.add_argument("--decision", type=UUID, required=True)
    a = p.parse_args(argumentos)
    config = cargar()
    if not config.database_url:
        print("falta CEM_DATABASE_URL", file=sys.stderr)
        return 2

    motor = crear_motor(config.database_url)
    try:
        with transaccion(motor, persona_id=None, consorcio_id=a.consorcio) as conn:
            try:
                carga = conn.execute(
                    text(
                        "select nucleo.fijar_aprobaciones_orden_pago(cast(:n as smallint), :d,"
                        " :dec, null, 'soporte')"
                    ),
                    {"n": a.cantidad, "d": a.desde, "dec": a.decision},
                ).scalar_one()
            except DBAPIError as e:
                print(f"la base rechazó la cantidad ({e.orig.diag.message_hint}):"
                      f" {e.orig.diag.message_primary}", file=sys.stderr)  # fmt: skip
                return 1
    finally:
        motor.dispose()
    print(json.dumps({"carga_id": str(carga), "cantidad": a.cantidad,
                      "desde": a.desde.isoformat()}))  # fmt: skip
    return 0


if __name__ == "__main__":
    sys.exit(main())
