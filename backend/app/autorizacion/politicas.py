"""Carga y valida las políticas al importar (tabla de verdad, §6).

Lee todos los `politicas_datos/*.json` del paquete `cem_contracts`, los valida con el modelo
estricto `ArchivoPoliticas` y arma el índice por acción. Falla fuerte, sin seguir con un
subconjunto: un archivo que no valida o una acción declarada dos veces impiden importar el
módulo (y, desde el PR 3, arrancar la API).
"""

import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from types import MappingProxyType

from cem_contracts.politicas import ArchivoPoliticas, Politica, archivos
from pydantic import ValidationError


class PoliticasInvalidas(RuntimeError):
    """Algún archivo de políticas no valida, o hay acciones duplicadas."""


def cargar(rutas: Iterable[Path]) -> Mapping[str, Politica]:
    politicas: dict[str, Politica] = {}
    origen: dict[str, str] = {}
    leidos = 0
    for ruta in rutas:
        leidos += 1
        try:
            archivo = ArchivoPoliticas.model_validate(json.loads(ruta.read_text(encoding="utf-8")))
        except (ValidationError, ValueError) as e:
            raise PoliticasInvalidas(f"{ruta.name}: {e}") from e
        for p in archivo.politicas:
            if p.accion in politicas:
                raise PoliticasInvalidas(
                    f"acción duplicada {p.accion!r}: en {origen[p.accion]} y en {ruta.name}"
                )
            politicas[p.accion] = p
            origen[p.accion] = ruta.name
    if leidos == 0:
        raise PoliticasInvalidas("no se encontró ningún archivo de políticas")
    return MappingProxyType(politicas)


POLITICAS: Mapping[str, Politica] = cargar(archivos())
