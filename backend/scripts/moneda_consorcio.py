"""Habilita otra moneda en un edificio, en nombre del equipo (informe de fricción, P-14; sí del
propietario del 14-11-2026; 0100).

Todo edificio opera en pesos. Si opera también en dólares, el equipo lo habilita con este script:
desde ahí el formulario de la orden de pago muestra «Moneda» y la base acepta órdenes en dólares.
Solo se habilita (no se deshabilita) y queda la constancia en `nucleo.moneda_consorcio`, con el
origen `soporte` y la fecha; como el resto de la clase B, sin evento de auditoría. Corre dentro del
contenedor de `cem-backend`, con su `CEM_DATABASE_URL` (`cem_app`, sujeto a la RLS):

    python -m scripts.moneda_consorcio --consorcio <uuid> --habilitar USD

Imprime en JSON las monedas en que queda operando el edificio.
"""

import argparse
import json
import sys
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.config import cargar
from app.db import crear_motor, transaccion


def main(argumentos: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--consorcio", type=UUID, required=True)
    p.add_argument("--habilitar", required=True, help="Código de la moneda, p. ej. USD.")
    a = p.parse_args(argumentos)
    config = cargar()
    if not config.database_url:
        print("falta CEM_DATABASE_URL", file=sys.stderr)
        return 2

    motor = crear_motor(config.database_url)
    try:
        with transaccion(motor, persona_id=None, consorcio_id=a.consorcio) as conn:
            try:
                cuando = conn.execute(
                    text("select nucleo.habilitar_moneda_consorcio(:m, null, 'soporte')"),
                    {"m": a.habilitar},
                ).scalar_one()
            except DBAPIError as e:
                print(f"la base rechazó la moneda ({e.orig.diag.message_hint}):"
                      f" {e.orig.diag.message_primary}", file=sys.stderr)  # fmt: skip
                return 1
            monedas = conn.execute(
                text("select nucleo.monedas_consorcio(:c)"), {"c": a.consorcio}
            ).scalar_one()
    finally:
        motor.dispose()
    print(json.dumps({"consorcio_id": str(a.consorcio), "habilitada": a.habilitar,
                      "habilitada_en": cuando.isoformat(), "monedas": monedas}))  # fmt: skip
    return 0


if __name__ == "__main__":
    sys.exit(main())
