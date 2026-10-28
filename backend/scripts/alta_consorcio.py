"""Da de alta un consorcio y sus unidades en nombre del equipo de la plataforma (RF01, RF02).

Sirve para crear el primer edificio de un consorcio, antes de que exista la pantalla de alta
(H2 2b), y poder invitar a sus primeras personas con `scripts.invitar`. Corre dentro del
contenedor de `cem-backend`, con su `CEM_DATABASE_URL` (`cem_app`, sujeto a la RLS):

    python -m scripts.alta_consorcio --archivo edificio.json [--simular]
    python -m scripts.alta_consorcio --archivo - < edificio.json      # por la entrada estándar

El archivo es JSON y no admite campos que la base todavía no guarda:

    {
      "consorcio": {
        "id": "<uuid>",
        "nombre": "Edificio Alvear 100",
        "cuit": "30712345678",
        "zona_horaria": "America/Argentina/Buenos_Aires"
      },
      "unidades": [{"designacion": "PB"}, {"designacion": "1° A"}]
    }

`id` es obligatorio: con la RLS, `cem_app` solo ve el consorcio del ámbito, así que la única
forma de saber si ya existe es buscarlo por su identificador. Si falta, el script no escribe
nada y propone uno nuevo (uuid v7 de la base) para pegar en el archivo. `cuit` es opcional.

Todo va en una sola transacción, con el consorcio en el ámbito. No escribe eventos de auditoría:
lo opera soporte de plataforma y, desde la decisión del propietario del 28-10-2026 (S2b, `0043`),
soporte no deja eventos de clase B; la constancia son las filas del consorcio y sus unidades,
que `cem_app` ya no puede modificar ni borrar. Es idempotente: si el consorcio
ya existe con los mismos datos y las mismas unidades, no escribe nada y vuelve a imprimir los
identificadores; si algo difiere (un dato del consorcio, una unidad de más o de menos), falla
con la lista de diferencias y no escribe nada. Corregir un consorcio existente queda para la
pantalla de 2b. `--simular` hace las mismas comprobaciones en una transacción de solo lectura.
"""

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import Connection, text

from app.config import cargar
from app.db import crear_motor, transaccion

ESTADOS = {
    "creado": "creado",
    "existente": "ya existía igual; no se escribió nada",
    "se_crearia": "SIMULACIÓN: se crearía (no se escribió nada)",
}


class _Estricto(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ConsorcioArchivo(_Estricto):
    id: UUID | None = None
    nombre: str = Field(min_length=1)
    cuit: str | None = Field(default=None, pattern=r"^[0-9]{11}$")
    zona_horaria: str

    @field_validator("zona_horaria")
    @classmethod
    def zona_existente(cls, valor: str) -> str:
        try:
            ZoneInfo(valor)
        except (ZoneInfoNotFoundError, ValueError) as e:
            raise ValueError(f"zona horaria desconocida: {valor}") from e
        return valor


class UnidadArchivo(_Estricto):
    designacion: str = Field(min_length=1)


class Archivo(_Estricto):
    consorcio: ConsorcioArchivo
    unidades: list[UnidadArchivo] = Field(min_length=1)

    @field_validator("unidades")
    @classmethod
    def sin_repetidas(cls, unidades: list[UnidadArchivo]) -> list[UnidadArchivo]:
        designaciones = [u.designacion for u in unidades]
        repetidas = sorted({d for d in designaciones if designaciones.count(d) > 1})
        if repetidas:
            raise ValueError(f"unidades repetidas en el archivo: {', '.join(repetidas)}")
        return unidades


class Diferente(Exception):
    """El consorcio ya existe y no coincide con el archivo. No se escribió nada."""

    def __init__(self, diferencias: list[str]) -> None:
        super().__init__("\n".join(diferencias))
        self.diferencias = diferencias


@dataclass(frozen=True)
class Resultado:
    consorcio_id: UUID
    estado: Literal["creado", "existente", "se_crearia"]
    """`existente`: ya estaba igual y no se escribió nada; `se_crearia`: simulación de un alta."""
    unidades: dict[str, UUID | None]
    """Designación → id; None solo en `se_crearia`."""


def pedidas(archivo: Archivo) -> list[str]:
    return [u.designacion for u in archivo.unidades]


def leer(ruta: str) -> Archivo:
    contenido = sys.stdin.read() if ruta == "-" else Path(ruta).read_text(encoding="utf-8")
    return Archivo.model_validate_json(contenido)


def comparar(conn: Connection, archivo: Archivo, consorcio_id: UUID) -> dict[str, UUID] | None:
    """Unidades del consorcio existente si coincide con el archivo; None si no existe."""
    fila = (
        conn.execute(
            text(
                "select nombre, cuit, zona_horaria, eliminado_en from nucleo.consorcio"
                " where id = :c"
            ),
            {"c": consorcio_id},
        )
        .mappings()
        .one_or_none()
    )
    if fila is None:
        return None
    c = archivo.consorcio
    diferencias = [
        f"consorcio.{campo}: en la base {fila[campo]!r}, en el archivo {valor!r}"
        for campo, valor in (
            ("nombre", c.nombre),
            ("cuit", c.cuit),
            ("zona_horaria", c.zona_horaria),
        )
        if fila[campo] != valor
    ]
    if fila["eliminado_en"] is not None:
        diferencias.append(f"consorcio dado de baja el {fila['eliminado_en']}")
    existentes = {
        d: i
        for d, i in conn.execute(
            text(
                "select designacion, id from nucleo.unidad"
                " where consorcio_id = :c and eliminado_en is null"
            ),
            {"c": consorcio_id},
        )
    }
    en_archivo = set(pedidas(archivo))
    diferencias += [
        f"unidad {d!r}: está en el archivo y no en la base"
        for d in sorted(en_archivo - existentes.keys())
    ]
    diferencias += [
        f"unidad {d!r}: está en la base y no en el archivo"
        for d in sorted(existentes.keys() - en_archivo)
    ]
    if diferencias:
        raise Diferente(diferencias)
    return {d: existentes[d] for d in pedidas(archivo)}


def crear(conn: Connection, archivo: Archivo, consorcio_id: UUID) -> dict[str, UUID]:
    c = archivo.consorcio
    conn.execute(
        text(
            "insert into nucleo.consorcio (id, nombre, cuit, zona_horaria)"
            " values (:id, :n, :cuit, :z)"
        ),
        {"id": consorcio_id, "n": c.nombre, "cuit": c.cuit, "z": c.zona_horaria},
    )
    unidades: dict[str, UUID] = {}
    for u in archivo.unidades:
        unidad_id = conn.execute(
            text(
                "insert into nucleo.unidad (consorcio_id, designacion) values (:c, :d) returning id"
            ),
            {"c": consorcio_id, "d": u.designacion},
        ).scalar_one()
        unidades[u.designacion] = unidad_id
    return unidades


def dar_de_alta(motor, archivo: Archivo, *, simular: bool) -> Resultado:
    consorcio_id = archivo.consorcio.id
    if consorcio_id is None:
        raise ValueError("falta consorcio.id")
    with transaccion(motor, persona_id=None, consorcio_id=consorcio_id) as conn:
        if simular:
            conn.execute(text("set transaction read only"))
        existentes = comparar(conn, archivo, consorcio_id)
        if existentes is not None:
            return Resultado(consorcio_id, "existente", existentes)
        if simular:
            return Resultado(consorcio_id, "se_crearia", dict.fromkeys(pedidas(archivo)))
        return Resultado(consorcio_id, "creado", crear(conn, archivo, consorcio_id))


def id_sugerido(motor) -> UUID:
    with transaccion(motor, persona_id=None) as conn:
        return conn.execute(text("select plataforma.uuid_v7()")).scalar_one()


def main(argumentos: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--archivo", required=True, help="JSON del consorcio; '-' lee la entrada estándar"
    )
    p.add_argument("--simular", action="store_true", help="comprueba y muestra, sin escribir nada")
    a = p.parse_args(argumentos)
    config = cargar()
    if not config.database_url:
        print("falta CEM_DATABASE_URL", file=sys.stderr)
        return 2
    try:
        archivo = leer(a.archivo)
    except (OSError, ValidationError) as e:
        print(f"archivo inválido: {e}", file=sys.stderr)
        return 2
    motor = crear_motor(config.database_url)
    try:
        if archivo.consorcio.id is None:
            print(
                "falta consorcio.id: agregalo al archivo y volvé a correr. Uno nuevo:\n"
                f'  "id": "{id_sugerido(motor)}"',
                file=sys.stderr,
            )
            return 2
        try:
            r = dar_de_alta(motor, archivo, simular=a.simular)
        except Diferente as e:
            print(
                f"el consorcio {archivo.consorcio.id} ya existe y no coincide con el archivo;"
                " no se escribió nada:",
                file=sys.stderr,
            )
            for d in e.diferencias:
                print(f"  - {d}", file=sys.stderr)
            return 1
    finally:
        motor.dispose()
    print(f"consorcio {r.consorcio_id} {archivo.consorcio.nombre!r}: {ESTADOS[r.estado]}")
    for designacion, unidad_id in r.unidades.items():
        print(f"  unidad {unidad_id or '(nueva)'}  {designacion}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
