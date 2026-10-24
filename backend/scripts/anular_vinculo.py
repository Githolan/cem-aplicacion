"""Anula en nombre del equipo un vínculo confirmado que todavía no empezó (RF02, corte 3; 0025).

Por la API solo se anula un vínculo sin confirmar. Uno confirmado (se puede confirmar antes de
que empiece) lo anula el equipo, con la referencia del respaldo (la escritura corregida, la nota
del administrador): así un título erróneo descubierto antes de la fecha no llega a dar acceso.
La referencia queda en el evento de auditoría; el motivo se exige y no se guarda (E31). Un
vínculo que ya empezó no se anula: se finaliza. Corre dentro del contenedor de `cem-backend`,
con su `CEM_DATABASE_URL` (`cem_app`, sujeto a la RLS):

    python -m scripts.anular_vinculo --consorcio <uuid> --vinculo <uuid> \\
        --referencia "Escritura corregida 815" --motivo "Fecha de inicio cargada por error"
"""

import argparse
import sys
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.config import cargar
from app.db import crear_motor, transaccion


def main(argumentos: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--consorcio", type=UUID, required=True)
    p.add_argument("--vinculo", type=UUID, required=True)
    p.add_argument("--referencia", required=True)
    p.add_argument("--motivo", required=True)
    a = p.parse_args(argumentos)
    config = cargar()
    if not config.database_url:
        print("falta CEM_DATABASE_URL", file=sys.stderr)
        return 2
    motor = crear_motor(config.database_url)
    try:
        with transaccion(motor, persona_id=None, consorcio_id=a.consorcio) as conn:
            conn.execute(
                text("select nucleo.anular_vinculo(:v, :m, :r, null, 'soporte')"),
                {"v": a.vinculo, "m": a.motivo, "r": a.referencia},
            )
    except DBAPIError as e:
        print(f"no se anuló: {e.orig.diag.message_hint or e.orig}", file=sys.stderr)
        return 1
    finally:
        motor.dispose()
    print(f"vínculo {a.vinculo} anulado")
    return 0


if __name__ == "__main__":
    sys.exit(main())
