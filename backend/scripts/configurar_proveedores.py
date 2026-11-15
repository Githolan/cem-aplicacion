"""Cambia la configuración de proveedores de un edificio, en nombre del equipo (RF50 configurable;
sí del propietario del 15-11-2026; `0104`).

Cada valor vive en la base, por consorcio, con un valor por defecto. Hasta que exista la
aplicación de instalación y mantenimiento, el equipo lo cambia con este script. Un valor que no se
pasa queda como está. Cada cambio deja una fila en `nucleo.configuracion_proveedores`, con el
origen `soporte` y la fecha, que nadie puede reescribir. Corre dentro del contenedor de
`cem-backend`, con su `CEM_DATABASE_URL` (`cem_app`, sujeto a la RLS):

    python -m scripts.configurar_proveedores --consorcio <uuid> [--dias-por-vencer 30]
        [--modo-exigencia avisar|bloquear] [--admite-no-vence si|no]
        [--roles-que-ven miembro_consejo,administrador,...]
        [--requisitos-agregados codigo,...] [--requisitos-omitidos codigo,...]
        [--roles-que-autorizan miembro_consejo,...] [--autoriza-por-persona si|no]

`--roles-que-autorizan` y `--autoriza-por-persona` (`0105`) deciden quién autoriza a un habitante
a ver los proveedores y si esas autorizaciones cuentan.

Una lista vacía se pasa como `--roles-que-ven ""`. Sin ningún valor, solo imprime la configuración
que vale. Imprime en JSON la configuración que queda.
"""

import argparse
import json
import sys
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.config import cargar
from app.db import crear_motor, transaccion

SI_NO = {"si": True, "no": False}


def _lista(valor: str) -> list[str]:
    return [x.strip() for x in valor.split(",") if x.strip()]


def main(argumentos: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--consorcio", type=UUID, required=True)
    p.add_argument("--dias-por-vencer", type=int)
    p.add_argument("--modo-exigencia", choices=("avisar", "bloquear"))
    p.add_argument("--admite-no-vence", choices=tuple(SI_NO))
    p.add_argument("--roles-que-ven", type=_lista)
    p.add_argument("--requisitos-agregados", type=_lista)
    p.add_argument("--requisitos-omitidos", type=_lista)
    p.add_argument("--roles-que-autorizan", type=_lista)
    p.add_argument("--autoriza-por-persona", choices=tuple(SI_NO))
    a = p.parse_args(argumentos)
    config = cargar()
    if not config.database_url:
        print("falta CEM_DATABASE_URL", file=sys.stderr)
        return 2

    valores = {
        "dias": a.dias_por_vencer,
        "modo": a.modo_exigencia,
        "no_vence": None if a.admite_no_vence is None else SI_NO[a.admite_no_vence],
        "roles": a.roles_que_ven,
        "agregados": a.requisitos_agregados,
        "omitidos": a.requisitos_omitidos,
    }
    autorizacion = {
        "roles": a.roles_que_autorizan,
        "por_persona": None if a.autoriza_por_persona is None else SI_NO[a.autoriza_por_persona],
    }
    cambios = [
        (
            "select nucleo.configurar_proveedores(:dias, :modo, :no_vence, :roles, :agregados,"
            " :omitidos, null, 'soporte')",
            valores,
        ),
        (
            "select nucleo.configurar_autorizacion_proveedores(:roles, :por_persona, null,"
            " 'soporte')",
            autorizacion,
        ),
    ]
    motor = crear_motor(config.database_url)
    try:
        with transaccion(motor, persona_id=None, consorcio_id=a.consorcio) as conn:
            for sql, parametros in cambios:
                if all(v is None for v in parametros.values()):
                    continue
                try:
                    conn.execute(text(sql), parametros)
                except DBAPIError as e:
                    print(f"la base rechazó la configuración ({e.orig.diag.message_hint}):"
                          f" {e.orig.diag.message_primary}", file=sys.stderr)  # fmt: skip
                    return 1
            vigente = conn.execute(
                text("select * from nucleo.configuracion_proveedores_soporte()")
            ).mappings().one()  # fmt: skip
    finally:
        motor.dispose()
    print(json.dumps({"consorcio_id": str(a.consorcio), **vigente}, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
