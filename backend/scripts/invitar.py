"""Invita a una persona a una unidad en nombre del equipo de la plataforma (RF03).

Sirve para dar de alta a las primeras personas de un consorcio, cuando todavía no hay ningún
propietario que pueda invitar desde la aplicación. Corre dentro del contenedor de
`cem-backend`, con su `CEM_DATABASE_URL` (`cem_app`, sujeto a la RLS) y su
`CEM_ORIGEN_PUBLICO`:

    python -m scripts.invitar --consorcio <uuid> --unidad <uuid> --tipo propietario \\
        --correo ana@ejemplo.com --nombre "Ana"

La invitación queda sin `invitada_por`, que dice que la emitió el equipo, y sin evento de
auditoría: soporte no forma parte de la auditoría funcional (decisión del propietario del
26-10-2026, corte S2a). Aceptarla sí queda auditado. El correo sale por la cola como cualquier
otro. No imprime el enlace: le llega solo a la persona invitada.
"""

import argparse
import sys
from uuid import UUID

from cem_contracts.estados import TipoVinculo
from cem_contracts.invitaciones import InvitacionNueva
from pydantic import ValidationError

from app.config import cargar
from app.db import crear_motor, transaccion
from app.errores import ErrorApi
from app.identidad import invitaciones
from app.identidad.router import validar_origen


def main(argumentos: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--consorcio", type=UUID, required=True)
    p.add_argument("--unidad", type=UUID, required=True)
    p.add_argument("--tipo", choices=[t.value for t in TipoVinculo], required=True)
    p.add_argument("--correo", required=True)
    p.add_argument("--nombre", required=True)
    a = p.parse_args(argumentos)
    config = cargar()
    if not config.database_url or not config.origen_publico:
        print("faltan CEM_DATABASE_URL o CEM_ORIGEN_PUBLICO", file=sys.stderr)
        return 2
    validar_origen(config.origen_publico)
    try:
        nueva = InvitacionNueva(
            unidad_id=a.unidad, tipo=TipoVinculo(a.tipo), correo=a.correo, nombre=a.nombre
        )
    except ValidationError as e:
        print(f"datos inválidos: {e}", file=sys.stderr)
        return 2
    motor = crear_motor(config.database_url)
    try:
        with transaccion(motor, persona_id=None, consorcio_id=a.consorcio) as conn:
            creada = invitaciones.crear(
                conn,
                consorcio_id=a.consorcio,
                nueva=nueva,
                invitada_por=None,
                origen_publico=config.origen_publico,
                origen="soporte",
            )
    except ErrorApi as e:  # la pendiente igual es de la importación: no se reemplaza
        print(e.mensaje, file=sys.stderr)
        return 1
    finally:
        motor.dispose()
    if creada is None:
        print("la unidad no existe en ese consorcio o está dada de baja", file=sys.stderr)
        return 1
    print(f"invitación {creada.invitacion.id} creada; vence {creada.invitacion.vence_en}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
