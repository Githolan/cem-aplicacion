"""Asocia o desasocia la cuenta de un administrador externo en nombre del equipo (A7, P1-e).

Solo cuando el flujo de cuatro ojos de la aplicación no es viable (por ejemplo, todavía no hay
dos personas con `mandatos.registrar` en el consorcio). Lleva la misma referencia obligatoria
que la aplicación y, además, el motivo de por qué no hubo cuatro ojos; los dos quedan en la
fila, con el origen `soporte`. Soporte no escribe eventos de auditoría funcional (decisión del
propietario del 26-10-2026, corte S2a); aceptar la invitación, que hace la persona, sí. Corre
dentro del contenedor de `cem-backend`, con su `CEM_DATABASE_URL` (`cem_app`, sujeto a la RLS)
y su `CEM_ORIGEN_PUBLICO`:

    python -m scripts.mandato_soporte invitar --consorcio <uuid> --mandato <uuid> \\
        --correo admin@estudio.com --referencia "Contrato del 01-10" --motivo "Un solo MC"
    python -m scripts.mandato_soporte desasociar --consorcio <uuid> --mandato <uuid> \\
        --referencia "Nota del 05-11" --motivo "Un solo MC"

Invitar emite y aprueba en un solo paso y manda el correo por la cola. No imprime el enlace:
le llega solo al correo invitado.
"""

import argparse
import sys
from uuid import UUID
from zoneinfo import ZoneInfo

from cem_contracts.mandatos_invitaciones import Desasociacion, InvitacionMandatoNueva
from pydantic import ValidationError
from sqlalchemy import text

from app.config import cargar
from app.consorcios.mandatos_invitaciones import (
    RUTA_INVITACION_MANDATO,
    VIGENCIA_INVITACION_MANDATO,
)
from app.db import crear_motor, transaccion
from app.identidad import eventos, sesiones
from app.identidad.router import validar_origen

LARGO_MAXIMO_MOTIVO = 300
"""La migración 0015 impone el mismo tope."""


def main(argumentos: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="accion", required=True)
    for nombre in ("invitar", "desasociar"):
        s = sub.add_parser(nombre)
        s.add_argument("--consorcio", type=UUID, required=True)
        s.add_argument("--mandato", type=UUID, required=True)
        s.add_argument("--referencia", required=True)
        s.add_argument("--motivo", required=True)
        if nombre == "invitar":
            s.add_argument("--correo", required=True)
    a = p.parse_args(argumentos)
    config = cargar()
    if not config.database_url or not config.origen_publico:
        print("faltan CEM_DATABASE_URL o CEM_ORIGEN_PUBLICO", file=sys.stderr)
        return 2
    validar_origen(config.origen_publico)
    motivo = a.motivo.strip()
    if not 1 <= len(motivo) <= LARGO_MAXIMO_MOTIVO:
        print("el motivo es obligatorio, de hasta 300 caracteres", file=sys.stderr)
        return 2
    try:
        if a.accion == "invitar":
            datos = InvitacionMandatoNueva(correo=a.correo, referencia=a.referencia)
        else:
            datos = Desasociacion(referencia=a.referencia)
    except ValidationError as e:
        print(f"datos inválidos: {e}", file=sys.stderr)
        return 2
    motor = crear_motor(config.database_url)
    try:
        with transaccion(motor, persona_id=None, consorcio_id=a.consorcio) as conn:
            if a.accion == "invitar":
                token = sesiones.nuevo_token()
                fila = conn.execute(
                    text(
                        "select nucleo.invitar_a_mandato(:m, :correo, :r, :motivo, :h,"
                        " now() + :v, null, 'soporte') as id, now() + :v as vence,"
                        " c.nombre as consorcio, c.zona_horaria, m.mandatario_nombre"
                        " from nucleo.consorcio c join nucleo.mandato m on m.consorcio_id = c.id"
                        " where c.id = :c and m.id = :m"
                    ),
                    {
                        "m": a.mandato,
                        "correo": datos.correo,
                        "r": datos.referencia,
                        "motivo": motivo,
                        "h": sesiones.hash_token(token),
                        "v": VIGENCIA_INVITACION_MANDATO,
                        "c": a.consorcio,
                    },
                ).one_or_none()
                if fila is None:
                    print("el mandato no existe en ese consorcio", file=sys.stderr)
                    return 1
                eventos.solicitar_invitacion_mandato(
                    conn,
                    consorcio_id=a.consorcio,
                    para=datos.correo.lower(),
                    quien=None,
                    consorcio=fila.consorcio,
                    mandatario=fila.mandatario_nombre,
                    enlace=f"{config.origen_publico}{RUTA_INVITACION_MANDATO}#token={token}",
                    vence=fila.vence.astimezone(ZoneInfo(fila.zona_horaria)).strftime(
                        "%d/%m/%Y a las %H:%M"
                    ),
                )
                print(f"invitación {fila.id} emitida y aprobada; el correo sale por la cola")
            else:
                revocadas = conn.execute(
                    text(
                        "select nucleo.desasociar_persona_de_mandato(:m, :r, :motivo, null,"
                        " 'soporte')"
                    ),
                    {"m": a.mandato, "r": datos.referencia, "motivo": motivo},
                ).scalar_one()
                print(f"mandato {a.mandato} desasociado; {revocadas} sesiones revocadas")
    finally:
        motor.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
