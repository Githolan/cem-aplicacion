"""El OpenAPI publicado contra la instantánea aprobada (documento 13, §11.2).

Para aprobar un cambio intencional: `python -m scripts.exportar_openapi` y revisar la
diferencia de `contratos/openapi.json` en el PR.
"""

import json

from scripts.exportar_openapi import DESTINO as INSTANTANEA
from scripts.exportar_openapi import openapi_texto


def test_openapi_coincide_con_la_instantanea():
    assert INSTANTANEA.read_text(encoding="utf-8") == openapi_texto(), (
        "El contrato de la API cambió. Si es intencional: python -m scripts.exportar_openapi"
    )


def test_toda_ruta_de_negocio_vive_bajo_api_v1():
    rutas = json.loads(openapi_texto())["paths"]
    assert all(r == "/health" or r.startswith("/api/v1/") for r in rutas)
