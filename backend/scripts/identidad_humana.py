"""Verifica, vincula, desvincula o corrige la identidad humana de una cuenta (A13-C).

Solo soporte lo hace (decisión del propietario del 24-10-2026), con el documento a la vista y un
respaldo. Corre dentro del contenedor de `cem-backend`, con su `CEM_DATABASE_URL` (`cem_app`) y
`CEM_IDENTIDAD_CLAVES`:

    python -m scripts.identidad_humana verificar --consorcio <uuid> --persona <uuid> \\
        --tipo dni --metodo documento_presencial --operador "Nombre del equipo" \\
        --referencia "Reunión de Consejo del 12-11, acta 45"
    python -m scripts.identidad_humana vincular  …mismos datos…   (otra cuenta del mismo humano)
    python -m scripts.identidad_humana corregir  …mismos datos… --motivo "Número mal cargado"
    python -m scripts.identidad_humana desvincular --consorcio <uuid> --persona <uuid> \\
        --operador "…" --referencia "…" --motivo "La cuenta no era de esa persona"

**El número del documento** se lee de la entrada estándar (sin eco si es una terminal), nunca de
un argumento ni de un archivo: así no queda en el historial, en `ps` ni en el disco. El script
calcula su huella con cada clave del llavero y manda solo las huellas; el número no se imprime,
no se registra y no llega a la base. La referencia, el motivo y el operador se rechazan si lo
contienen. Las versiones retiradas del llavero no pueden tener identidades: antes de retirar
una, `corregir` las que firmó (la base se niega con `clave_retirada_con_identidades`).

- `verificar`: la cuenta no tiene identidad y el documento no es de ninguna del consorcio.
- `vincular`: la cuenta no tiene identidad y el documento ya es de una: es otra cuenta del mismo
  ser humano. Nunca se vincula por nombre, correo ni ningún otro dato.
- `corregir`: reemplaza la huella de la identidad de la cuenta (un número mal cargado, o una clave
  nueva del llavero). El documento nuevo no puede ser de otra identidad.
- `desvincular`: la cuenta deja de ser de su identidad. No mira ningún documento.

Si la base rechaza el pedido no se escribe nada y se informa el motivo, nunca el número.
"""

import argparse
import getpass
import os
import sys
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.config import cargar
from app.db import crear_motor, transaccion
from app.identidad import humana

METODOS = ("documento_presencial", "documento_por_videollamada")
CON_DOCUMENTO = ("verificar", "vincular", "corregir")
SQL = {
    "verificar": "select nucleo.verificar_identidad_humana(:p, :v, :h, :m, :r, :o)",
    "vincular": "select nucleo.vincular_identidad_humana(:p, :v, :h, :m, :r, :o)",
    "corregir": "select nucleo.corregir_identidad_humana(:p, :v, :h, :m, :r, :mot, :o)",
    "desvincular": "select nucleo.desvincular_identidad_humana(:p, :r, :mot, :o)",
}


def leer_numero() -> str:
    """Una línea de la entrada estándar; sin eco si es una terminal."""
    if sys.stdin.isatty():
        return getpass.getpass("Número del documento (no se muestra): ")
    return sys.stdin.readline().rstrip("\r\n")


def argumentos(lista: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("accion", choices=tuple(SQL))
    p.add_argument("--consorcio", type=UUID, required=True)
    p.add_argument("--persona", type=UUID, required=True)
    p.add_argument("--operador", required=True, help="quién del equipo lo hace")
    p.add_argument("--referencia", required=True, help="dónde está el respaldo; sin el número")
    p.add_argument("--motivo", help="obligatorio para corregir y desvincular")
    p.add_argument("--tipo", choices=[t.value for t in humana.TipoDocumento])
    p.add_argument("--pais", help="solo pasaporte: ISO 3166-1 alfa-3 (p. ej. ARG)")
    p.add_argument("--metodo", choices=METODOS)
    a = p.parse_args(lista)
    if a.accion in CON_DOCUMENTO and (a.tipo is None or a.metodo is None):
        p.error(f"{a.accion} necesita --tipo y --metodo")
    if a.accion == "desvincular" and (a.tipo or a.metodo or a.pais):
        p.error("desvincular no mira ningún documento")
    if a.accion in ("corregir", "desvincular") and not a.motivo:
        p.error(f"{a.accion} necesita --motivo")
    return a


def main(lista: list[str] | None = None) -> int:
    a = argumentos(lista)
    config = cargar()
    if not config.database_url:
        print("falta CEM_DATABASE_URL", file=sys.stderr)
        return 2
    parametros: dict = {
        "p": a.persona, "r": a.referencia, "o": a.operador, "mot": a.motivo, "m": a.metodo,
    }  # fmt: skip
    if a.accion in CON_DOCUMENTO:
        try:
            llavero = humana.cargar_llavero(os.environ.get("CEM_IDENTIDAD_CLAVES", ""))
            documento = humana.canonico(humana.TipoDocumento(a.tipo), leer_numero(), a.pais)
        except (RuntimeError, humana.DocumentoInvalido) as e:
            print(f"no se hizo nada: {e}", file=sys.stderr)
            return 2
        textos = (a.referencia, a.motivo or "", a.operador)
        if any(humana.contiene_documento(t, documento) for t in textos):
            print("no se hizo nada: la referencia, el motivo o el operador contienen el número",
                  file=sys.stderr)  # fmt: skip
            return 2
        parametros["v"], parametros["h"] = humana.huellas(llavero, a.consorcio, documento)
        del documento
    motor = crear_motor(config.database_url)
    try:
        with transaccion(motor, persona_id=None, consorcio_id=a.consorcio) as conn:
            identidad = conn.execute(text(SQL[a.accion]), parametros).scalar_one()
    except DBAPIError as e:
        # Solo el motivo: el mensaje de la base nunca trae parámetros, pero no hace falta más.
        print(f"no se hizo nada: {e.orig.diag.message_hint or 'error de la base'}", file=sys.stderr)
        return 1
    finally:
        motor.dispose()
    print(f"{a.accion}: cuenta {a.persona} → identidad {identidad} (consorcio {a.consorcio})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
