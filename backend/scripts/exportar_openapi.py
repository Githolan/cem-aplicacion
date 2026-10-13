"""Exporta el OpenAPI de la API a contratos/openapi.json (instantánea aprobada, comprometida).

De ese archivo el frontend genera sus tipos TypeScript (documento 13, §6.1 y §11.2).
"""

import json
from pathlib import Path

from app.config import Config
from app.main import crear_app

DESTINO = Path(__file__).resolve().parents[2] / "contratos" / "openapi.json"


def openapi_texto() -> str:
    app = crear_app(Config("", "0.1.0", "", ""))
    return json.dumps(app.openapi(), indent=2, ensure_ascii=False, sort_keys=True) + "\n"


if __name__ == "__main__":
    DESTINO.write_text(openapi_texto(), encoding="utf-8")
    print(f"escrito {DESTINO}")
