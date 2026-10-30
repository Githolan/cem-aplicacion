"""Registra el umbral de las órdenes de pago de un consorcio, en nombre del equipo (decisión 1A
del propietario, 30-10-2026; 0050).

No hay umbral de la plataforma: el importe lo decide cada consorcio, y el equipo solo lo registra
con el acta que lo respalda, ya registrada con `nucleo.registrar_decision` (de asamblea o del
Consejo; otro tipo se rechaza). Sin umbral, todo gasto no rutinario pide aprobación. Rige desde
hoy o una fecha posterior, nunca hacia atrás, y nunca se modifica uno registrado (un cambio es
otro con una fecha posterior). Las órdenes ya preparadas conservan el umbral con que se
evaluaron. Corre dentro del contenedor de `cem-backend`, con su `CEM_DATABASE_URL` (`cem_app`,
sujeto a la RLS):

    python -m scripts.umbral_orden_pago --consorcio <uuid> --importe-centavos 50000000 \\
        --moneda ARS --desde 2026-11-01 --decision <uuid>

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
    p.add_argument("--importe-centavos", type=int, required=True)
    p.add_argument("--moneda", required=True)
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
                        "select nucleo.fijar_umbral_orden_pago(:i, :m, :d, :dec, null, 'soporte')"
                    ),
                    {"i": a.importe_centavos, "m": a.moneda, "d": a.desde, "dec": a.decision},
                ).scalar_one()
            except DBAPIError as e:
                print(f"la base rechazó el umbral ({e.orig.diag.message_hint}):"
                      f" {e.orig.diag.message_primary}", file=sys.stderr)  # fmt: skip
                return 1
    finally:
        motor.dispose()
    print(json.dumps({"carga_id": str(carga), "importe_centavos": a.importe_centavos,
                      "moneda": a.moneda, "desde": a.desde.isoformat()}))  # fmt: skip
    return 0


if __name__ == "__main__":
    sys.exit(main())
