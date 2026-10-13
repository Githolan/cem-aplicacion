"""Exporta el esquema JSON de cada modelo del contrato.

`esquemas.json` en la raíz es la instantánea aprobada; la prueba falla si el
código y la instantánea difieren. Para aprobar un cambio:
`python -m cem_contracts.esquemas > esquemas.json`.
"""

import json

from pydantic import BaseModel

from cem_contracts.consorcios import ConsorcioResumen, ListaUnidades, Unidad
from cem_contracts.errores import Error
from cem_contracts.salud import Salud

MODELOS: tuple[type[BaseModel], ...] = (ConsorcioResumen, Error, ListaUnidades, Salud, Unidad)


def esquemas() -> dict[str, object]:
    return {m.__name__: m.model_json_schema() for m in MODELOS}


def como_texto() -> str:
    return json.dumps(esquemas(), indent=2, ensure_ascii=False, sort_keys=True) + "\n"


if __name__ == "__main__":
    print(como_texto(), end="")
