"""Registra el arranque de un consorcio en nombre del equipo de la plataforma (D3, H2b 2b-2).

El primer mandato no lo puede crear nadie desde la aplicación: todavía no hay quien tenga
`mandatos.registrar`. Este script registra, con origen `soporte` y en una sola transacción, la
decisión que respalda el arranque, el modo inicial (opcional) y los primeros mandatos. Corre
dentro del contenedor de `cem-backend`, con su `CEM_DATABASE_URL` (`cem_app`):

    python -m scripts.alta_mandato --archivo arranque.json [--simular]

    {
      "consorcio_id": "<uuid>",
      "decision": {
        "tipo": "asamblea",
        "fecha": "2026-08-15",
        "descripcion": "Designa el Consejo y adopta el Modo Consejo",
        "referencia_acta": "<número, fecha, libro y folio del acta REAL>",
        "sha256_acta": "<opcional: SHA-256 del archivo escaneado>"
      },
      "modo_inicial": {"modo": "consejo", "desde": "2026-08-15"},
      "mandatos": [
        {"tipo": "miembro_consejo", "persona_id": "<uuid>", "desde": "2026-08-15"},
        {"tipo": "administrador_externo", "mandatario_nombre": "…", "mandatario_cuit": "…",
         "desde": "2026-08-15", "facultades": ["documentos.cargar"]}
      ]
    }

**Criterio 9:** la referencia tiene que ser la del acta o instrumento real. Si el documento no
está, no se corre el script: nunca se inventa una referencia. `facultades` omitida = el paquete
del tipo (D2); si viene, solo puede achicarlo. `miembro_consejo` y `administrador` necesitan
`persona_id` (A12); `administrador_externo` necesita su CUIT, que se normaliza quitando guiones y
espacios (A11). Toda regla (decisión de este consorcio, paquete, CUIT válido, un solo
administrador por consorcio y fecha, modo solo con asamblea) la valida la base, que es la fuente
única. Si algo falla, no se escribe nada.
`--simular` hace todo y deshace la transacción al final: prueba las mismas reglas sin escribir.
"""

import argparse
import sys
from datetime import date
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.config import cargar
from app.db import crear_motor, transaccion

ORIGEN = "soporte"


class _Estricto(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Decision(_Estricto):
    tipo: Literal["asamblea", "consejo", "contrato_administracion", "inscripcion_rpa"]
    fecha: date
    descripcion: str = Field(min_length=1, max_length=500)
    referencia_acta: str = Field(min_length=1, max_length=300)
    sha256_acta: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")


class Modo(_Estricto):
    modo: Literal["consejo", "administrador_aumentado", "autogestion"]
    desde: date


class Mandato(_Estricto):
    tipo: Literal["administrador", "miembro_consejo", "administrador_externo"]
    persona_id: UUID | None = None
    mandatario_nombre: str | None = Field(default=None, min_length=1, max_length=200)
    mandatario_cuit: str | None = Field(default=None, pattern=r"^[0-9]{11}$")

    @field_validator("mandatario_cuit", mode="before")
    @classmethod
    def normalizar_cuit(cls, valor: object) -> object:
        # El dígito verificador lo valida la base (`nucleo.cuit_valido`); acá solo el formato.
        if isinstance(valor, str):
            return valor.replace("-", "").replace(" ", "")
        return valor

    matricula: str | None = Field(default=None, min_length=1, max_length=100)
    desde: date
    hasta: date | None = None
    facultades: list[str] | None = None

    @model_validator(mode="after")
    def identificado(self) -> "Mandato":
        if self.persona_id is None and self.mandatario_nombre is None:
            raise ValueError("cada mandato necesita persona_id o mandatario_nombre")
        if self.tipo != "administrador_externo" and self.persona_id is None:
            raise ValueError(f"un mandato de {self.tipo} necesita persona_id (A12)")
        if self.tipo == "administrador_externo" and self.mandatario_cuit is None:
            raise ValueError("un administrador_externo necesita mandatario_cuit (A11)")
        return self


class Archivo(_Estricto):
    consorcio_id: UUID
    decision: Decision
    modo_inicial: Modo | None = None
    mandatos: list[Mandato] = Field(default_factory=list)

    @model_validator(mode="after")
    def algo_que_registrar(self) -> "Archivo":
        if self.modo_inicial is None and not self.mandatos:
            raise ValueError("el archivo no registra nada: falta modo_inicial o mandatos")
        return self


def leer(ruta: str) -> Archivo:
    contenido = sys.stdin.read() if ruta == "-" else Path(ruta).read_text(encoding="utf-8")
    return Archivo.model_validate_json(contenido)


class _Deshacer(Exception):
    """Sale de la transacción de una simulación para que se deshaga."""

    def __init__(self, resultado: dict) -> None:
        super().__init__("simulación")
        self.resultado = resultado


def registrar(motor, archivo: Archivo, *, simular: bool) -> dict:
    """Registra todo en una transacción; con `simular`, la deshace al final."""
    try:
        return _registrar(motor, archivo, simular=simular)
    except _Deshacer as d:
        return d.resultado


def _registrar(motor, archivo: Archivo, *, simular: bool) -> dict:
    d = archivo.decision
    with transaccion(motor, persona_id=None, consorcio_id=archivo.consorcio_id) as conn:
        decision_id = conn.execute(
            text("select nucleo.registrar_decision(:t, :f, :desc, :ref, null, :o, :sha)"),
            {
                "t": d.tipo,
                "f": d.fecha,
                "desc": d.descripcion,
                "ref": d.referencia_acta,
                "o": ORIGEN,
                "sha": d.sha256_acta,
            },
        ).scalar_one()
        modo_id = None
        if archivo.modo_inicial:
            modo_id = conn.execute(
                text("select nucleo.cambiar_modo(:m, :d, :desde, null, :o)"),
                {
                    "m": archivo.modo_inicial.modo,
                    "d": decision_id,
                    "desde": archivo.modo_inicial.desde,
                    "o": ORIGEN,
                },
            ).scalar_one()
        mandatos = [
            conn.execute(
                text(
                    "select nucleo.alta_mandato(:t, :p, :n, :cuit, :mat, :d, :desde, :hasta,"
                    " :fac, null, :o)"
                ),
                {
                    "t": m.tipo,
                    "p": m.persona_id,
                    "n": m.mandatario_nombre,
                    "cuit": m.mandatario_cuit,
                    "mat": m.matricula,
                    "d": decision_id,
                    "desde": m.desde,
                    "hasta": m.hasta,
                    "fac": m.facultades,
                    "o": ORIGEN,
                },
            ).scalar_one()
            for m in archivo.mandatos
        ]
        resultado = {"decision": decision_id, "modo": modo_id, "mandatos": mandatos}
        if simular:
            raise _Deshacer(resultado)
    return resultado


def main(argumentos: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--archivo", required=True, help="JSON del arranque; '-' lee la entrada estándar"
    )
    p.add_argument("--simular", action="store_true", help="valida todo y no escribe nada")
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
        r = registrar(motor, archivo, simular=a.simular)
    except DBAPIError as e:
        print(f"la base rechazó el arranque; no se escribió nada:\n  {e.orig}", file=sys.stderr)
        return 1
    finally:
        motor.dispose()
    prefijo = "SIMULACIÓN (no se escribió nada): " if a.simular else ""
    print(f"{prefijo}consorcio {archivo.consorcio_id}")
    print(f"  decisión {r['decision']}  {archivo.decision.referencia_acta}")
    if r["modo"]:
        print(f"  modo {r['modo']}  {archivo.modo_inicial.modo}")
    for mandato_id, m in zip(r["mandatos"], archivo.mandatos, strict=True):
        print(f"  mandato {mandato_id}  {m.tipo}  {m.persona_id or m.mandatario_nombre}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
