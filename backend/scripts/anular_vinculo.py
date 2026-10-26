"""Anula en nombre del equipo un vínculo confirmado que todavía no empezó (RF02, corte 3; 0025).

Por la API solo se anula un vínculo sin confirmar. Uno confirmado (se puede confirmar antes de
que empiece) lo anula el equipo, con la referencia del respaldo (la escritura corregida, la nota
del administrador): así un título erróneo descubierto antes de la fecha no llega a dar acceso.
El motivo se exige y no se guarda (E31). Un vínculo que ya empezó no se anula: se finaliza.

Soporte no escribe eventos de auditoría (decisión del propietario del 26-10-2026; `0034`): la
base no conserva rastro de la anulación. La constancia es el respaldo que conserva el equipo y
lo que imprime este script (el vínculo, la referencia y las invitaciones retiradas, nunca el
motivo), que se adjunta al documento de operación. Corre dentro del contenedor de
`cem-backend`, con su `CEM_DATABASE_URL` (`cem_app`, sujeto a la RLS):

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
        # Lectura repetible: si el vínculo cambia entre la lectura de abajo y el bloqueo de la
        # función (otra persona lo confirma, por ejemplo), la función falla por serialización y no
        # se anula nada. Así lo impreso es siempre lo que se borró (Codex en app #126).
        repetible = motor.execution_options(isolation_level="REPEATABLE READ")
        with transaccion(repetible, persona_id=None, consorcio_id=a.consorcio) as conn:
            # Lo que se anula, leído antes de borrarlo: la fila desaparece. Sin bloqueo (`cem_app`
            # no tiene `update` sobre el vínculo); la función bloquea como siempre.
            vinculo = (
                conn.execute(
                    text(
                        "select unidad_id, tipo, vigente_desde, vigente_hasta, fuente,"
                        " confirmado_en is not null as confirmado, importacion_id"
                        " from nucleo.vinculo where id = :v"
                    ),
                    {"v": a.vinculo},
                )
                .mappings()
                .one_or_none()
            )
            conn.execute(
                text("select nucleo.anular_vinculo(:v, :m, :r, null, 'soporte')"),
                {"v": a.vinculo, "m": a.motivo, "r": a.referencia},
            )
            # Las que retiró esta transacción: `now()` es el mismo instante que escribió la función.
            retiradas = (
                conn.execute(
                    text(
                        "select id from nucleo.invitacion where vinculo_id = :v"
                        " and revocada_en = now() order by id"
                    ),
                    {"v": a.vinculo},
                )
                .scalars()
                .all()
            )
    except DBAPIError as e:
        print(f"no se anuló: {e.orig.diag.message_hint or e.orig}", file=sys.stderr)
        return 1
    finally:
        motor.dispose()
    # Recién acá, con la transacción confirmada.
    print(f"vínculo {a.vinculo} anulado")
    for campo, valor in vinculo.items():
        print(f"  {campo}: {valor}")
    print(f"  referencia: {a.referencia.strip()}")
    print(f"  invitaciones retiradas: {', '.join(str(x) for x in retiradas) or 'ninguna'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
