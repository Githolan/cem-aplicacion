"""Exporta el OpenAPI de la API a tests/snapshots/openapi.json (instantánea aprobada)."""

import json
from pathlib import Path

from app.config import Config
from app.main import crear_app

DESTINO = Path(__file__).resolve().parent.parent / "tests" / "snapshots" / "openapi.json"


def openapi_texto() -> str:
    app = crear_app(Config("", "0.1.0", "", ""))
    return json.dumps(app.openapi(), indent=2, ensure_ascii=False, sort_keys=True) + "\n"


if __name__ == "__main__":
    DESTINO.write_text(openapi_texto(), encoding="utf-8")
    print(f"escrito {DESTINO}")
