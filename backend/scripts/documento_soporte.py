"""Soporte borra un documento o cambia su categoría, a pedido de quien tiene la capacidad (2c-2).

Diseño: `doc/diseno/H2c_Diseno.md` del repositorio CEM, §1.1 y §4. Por la API nadie borra ni
recategoriza: lo hace el equipo con este script, que corre dentro del contenedor de
`cem-trabajos` con su rol propio, `cem_soporte_documentos` (`CEM_DATABASE_URL_SOPORTE_DOCUMENTOS`):
sin `BYPASSRLS`, con el consorcio fijado en el contexto y solo las columnas del borrado y de la
categoría (`0037`).

- **Exige** el motivo, la referencia del respaldo (la decisión o el documento que sostiene el
  pedido), quién lo pidió y el día del pedido.
- **Comprueba la capacidad:** quien lo pidió tenía ese día un mandato vigente con `documentos.ver`
  y `documentos.cargar`, las dos generales (`nucleo.capacidad_del_solicitante`). Si no, se niega.
  Crear una facultad de borrado es una decisión del propietario (§8).
- **No escribe eventos de auditoría** (decisión del propietario del 26-10-2026): la constancia
  queda en la fila del documento (motivo, referencia, fecha, quién lo pidió y su mandato) y en lo
  que imprime el script, que se adjunta al documento de operación.
- **El borrado es lógico:** el documento deja de figurar; sus versiones y binarios se conservan.
  Un documento borrado no se cambia más.

    python -m scripts.documento_soporte borrar --consorcio <uuid> --documento <uuid> \\
        --solicitante <persona> --fecha-pedido 2026-10-27 --referencia "Nota 12/2026" \\
        --motivo "Cargado en el consorcio equivocado"
    python -m scripts.documento_soporte cambiar-categoria --categoria seguros ... (lo mismo)
"""

import argparse
import os
import sys
from datetime import date
from uuid import UUID

from cem_contracts.documentos import CategoriaDocumento
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db import crear_motor, transaccion

VARIABLE_URL = "CEM_DATABASE_URL_SOPORTE_DOCUMENTOS"
ROL = "cem_soporte_documentos"
FACULTADES = ["documentos.ver", "documentos.cargar"]


class Negado(Exception):
    """El pedido no cumple lo que exige el script: no se cambió nada."""


def _argumentos(argumentos: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="accion", required=True)
    for nombre in ("borrar", "cambiar-categoria"):
        s = sub.add_parser(nombre)
        s.add_argument("--consorcio", type=UUID, required=True)
        s.add_argument("--documento", type=UUID, required=True)
        s.add_argument("--solicitante", type=UUID, required=True)
        s.add_argument("--fecha-pedido", type=date.fromisoformat, required=True)
        s.add_argument("--referencia", required=True)
        s.add_argument("--motivo", required=True)
        if nombre == "cambiar-categoria":
            s.add_argument(
                "--categoria", type=CategoriaDocumento, choices=list(CategoriaDocumento),
                required=True,
            )  # fmt: skip
    return p.parse_args(argumentos)


def ejecutar(motor, a: argparse.Namespace) -> dict:
    """Todo en una transacción: si algo se niega, no se cambia nada."""
    with transaccion(motor, persona_id=None, consorcio_id=a.consorcio) as conn:
        rol = conn.execute(text("select current_user")).scalar_one()
        if rol != ROL:
            raise Negado(f"{VARIABLE_URL} conecta como {rol}, no como {ROL}")
        doc = (
            conn.execute(
                text("select categoria, estado from nucleo.documento where id = :d for update"),
                {"d": a.documento},
            )
            .mappings()
            .one_or_none()
        )
        if doc is None:
            raise Negado("el documento no existe en este consorcio")
        if doc["estado"] == "borrado":
            raise Negado("el documento ya está borrado")
        if a.accion == "cambiar-categoria" and a.categoria == doc["categoria"]:
            raise Negado(f"el documento ya es de la categoría {doc['categoria']}")
        mandato = conn.execute(
            text("select nucleo.capacidad_del_solicitante(:c, :p, :f, :fecha)"),
            {"c": a.consorcio, "p": a.solicitante, "f": FACULTADES, "fecha": a.fecha_pedido},
        ).scalar_one()
        if mandato is None:
            raise Negado(
                "quien lo pidió no tenía ese día un mandato vigente con "
                + " y ".join(FACULTADES) + " generales"
            )  # fmt: skip
        datos = {"d": a.documento, "m": a.motivo, "r": a.referencia, "p": a.solicitante,
                 "mandato": mandato}  # fmt: skip
        if a.accion == "borrar":
            conn.execute(
                text(
                    "update nucleo.documento set estado = 'borrado', borrado_en = now(),"
                    " borrado_motivo = btrim(:m), borrado_referencia = btrim(:r),"
                    " borrado_solicitado_por = :p, borrado_mandato = :mandato where id = :d"
                ),
                datos,
            )
        else:
            conn.execute(
                text(
                    "update nucleo.documento set categoria_anterior = categoria,"
                    " categoria = :cat, categoria_cambiada_en = now(),"
                    " categoria_motivo = btrim(:m), categoria_referencia = btrim(:r),"
                    " categoria_solicitada_por = :p, categoria_mandato = :mandato where id = :d"
                ),
                datos | {"cat": a.categoria.value},
            )
        hecho = conn.execute(text("select now()")).scalar_one()
    return {
        "accion": a.accion,
        "consorcio": str(a.consorcio),
        "documento": str(a.documento),
        "categoria": doc["categoria"]
        if a.accion == "borrar"
        else f"{doc['categoria']} -> {a.categoria.value}",
        "solicitante": str(a.solicitante),
        "mandato": str(mandato),
        "fecha_pedido": a.fecha_pedido.isoformat(),
        "referencia": a.referencia.strip(),
        "hecho_en": hecho.isoformat(),
    }


def main(argumentos: list[str] | None = None) -> int:
    a = _argumentos(argumentos)
    url = os.environ.get(VARIABLE_URL, "")
    if not url:
        print(f"falta {VARIABLE_URL}", file=sys.stderr)
        return 2
    motor = crear_motor(url)
    try:
        constancia = ejecutar(motor, a)
    except Negado as e:
        print(f"no se cambió nada: {e}", file=sys.stderr)
        return 1
    except DBAPIError as e:
        print(f"no se cambió nada: {e.orig.diag.message_hint or e.orig}", file=sys.stderr)
        return 1
    finally:
        motor.dispose()
    for clave, valor in constancia.items():
        print(f"{clave}: {valor}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
