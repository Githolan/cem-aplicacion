"""Carga un esquema de coeficientes y su regla de reparto en nombre del equipo (D4, RF05; 0021).

Mientras proponer y aprobar por la API esperan las decisiones de 2b-6, el equipo carga el
esquema con la decisión que lo respalda (acta o reglamento, ya registrada con
`nucleo.registrar_decision`). Corre dentro del contenedor de `cem-backend`, con su
`CEM_DATABASE_URL` (`cem_app`, sujeto a la RLS):

    python -m scripts.cargar_coeficientes --consorcio <uuid> --grupo expensas_comunes \\
        --fuente "Reglamento, escritura 812, cláusula 4" --desde 2024-01-01 \\
        --decision <uuid> --archivo coeficientes.csv      # o --archivo - por la entrada estándar

El archivo es CSV con encabezado `designacion,porcentaje`, una fila por unidad activa del
consorcio. El porcentaje va **tal como dice la fuente**, con punto decimal y hasta 6 decimales
(`20.12`, `0`, `33.320000`). No se redondea ni se ajusta para que la suma dé 100: si la fuente
suma 99,96 %, se guarda 99,96 %. La regla es proporcional a los coeficientes, con los centavos
por restos mayores. Imprime en JSON el esquema creado, su versión y la suma.
"""

import argparse
import csv
import io
import json
import sys
from datetime import date
from pathlib import Path
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.config import cargar
from app.db import crear_motor, transaccion
from app.liquidacion.esquemas import CENTAVOS, METODO

ENCABEZADO = ["designacion", "porcentaje"]


def leer_planilla(contenido: str) -> list[tuple[str, str]]:
    """Las filas `(designación, porcentaje)` sin espacios alrededor. Falla si el encabezado no
    es el esperado o si una designación se repite."""
    filas = list(csv.reader(io.StringIO(contenido)))
    if not filas or [c.strip() for c in filas[0]] != ENCABEZADO:
        raise ValueError(f"el encabezado tiene que ser: {','.join(ENCABEZADO)}")
    datos = []
    for n, fila in enumerate(filas[1:], start=2):
        if not any(c.strip() for c in fila):
            continue
        if len(fila) != len(ENCABEZADO):
            raise ValueError(f"la fila {n} no tiene {len(ENCABEZADO)} columnas")
        datos.append((fila[0].strip(), fila[1].strip()))
    repetidas = sorted({d for d, _ in datos if [x for x, _ in datos].count(d) > 1})
    if repetidas:
        raise ValueError(f"designaciones repetidas: {', '.join(repetidas)}")
    if not datos:
        raise ValueError("la planilla no tiene filas")
    return datos


def main(argumentos: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--consorcio", type=UUID, required=True)
    p.add_argument("--grupo", required=True)
    p.add_argument("--fuente", required=True)
    p.add_argument("--desde", type=date.fromisoformat, required=True)
    p.add_argument("--decision", type=UUID, required=True)
    p.add_argument("--archivo", required=True, help="ruta del CSV, o - para la entrada estándar")
    a = p.parse_args(argumentos)
    config = cargar()
    if not config.database_url:
        print("falta CEM_DATABASE_URL", file=sys.stderr)
        return 2
    contenido = sys.stdin.read() if a.archivo == "-" else Path(a.archivo).read_text("utf-8")
    try:
        filas = leer_planilla(contenido)
    except ValueError as e:
        print(f"planilla inválida: {e}", file=sys.stderr)
        return 2

    motor = crear_motor(config.database_url)
    try:
        with transaccion(motor, persona_id=None, consorcio_id=a.consorcio) as conn:
            unidades = dict(
                conn.execute(
                    text(
                        "select designacion, id::text from nucleo.unidad"
                        " where consorcio_id = :c and eliminado_en is null"
                    ),
                    {"c": a.consorcio},
                ).all()
            )
            desconocidas = sorted(d for d, _ in filas if d not in unidades)
            if desconocidas:
                print(f"unidades que no están en el consorcio: {', '.join(desconocidas)}",
                      file=sys.stderr)  # fmt: skip
                return 2
            coeficientes = {unidades[d]: porcentaje for d, porcentaje in filas}
            try:
                esquema = conn.execute(
                    text(
                        "select nucleo.registrar_esquema_coeficientes(:g, :f, :d, :dec,"
                        " cast(:co as jsonb), :m, :ce, null, 'soporte')"
                    ),
                    {
                        "g": a.grupo,
                        "f": a.fuente,
                        "d": a.desde,
                        "dec": a.decision,
                        "co": json.dumps(coeficientes),
                        "m": METODO,
                        "ce": CENTAVOS,
                    },  # fmt: skip
                ).scalar_one()
            except DBAPIError as e:
                print(f"la base rechazó el esquema ({e.orig.diag.message_hint}):"
                      f" {e.orig.diag.message_primary}", file=sys.stderr)  # fmt: skip
                return 1
            fila = conn.execute(
                text(
                    "select s.version, sum(c.porcentaje)::text as suma"
                    " from nucleo.esquema_coeficientes s"
                    " join nucleo.coeficiente_juridico c on c.esquema_id = s.id"
                    " where s.id = :e group by s.version"
                ),
                {"e": esquema},
            ).one()
    finally:
        motor.dispose()
    print(json.dumps({"esquema_id": str(esquema), "grupo": a.grupo, "version": fila.version,
                      "suma": fila.suma}))  # fmt: skip
    return 0


if __name__ == "__main__":
    sys.exit(main())
