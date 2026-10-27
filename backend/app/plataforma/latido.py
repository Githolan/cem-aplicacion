"""Latido de los procesos sin HTTP y comprobación de salud de la imagen.

La imagen es una sola para `cem-backend`, `cem-trabajos`, `cem-planificador` (documento
13, §4.2) y `cem-copia-continua` (H2c §6); `CEM_PROCESO` dice cuál corre. El `HEALTHCHECK` del
Dockerfile llama a `python -m app.plataforma.latido`: la API se comprueba por HTTP y los otros
tres por la edad del archivo de latido, que tocan en cada vuelta de su ciclo (la copia continua,
cada 10 segundos, con la copia base en otro hilo para no dejar de latir mientras dura).
"""

import os
import sys
import time
import urllib.request
from pathlib import Path

ARCHIVO = Path(os.environ.get("CEM_LATIDO", "/tmp/cem-latido"))
PROCESOS = ("api", "trabajos", "planificador", "copia_continua")
"""Los de `arranque.sh`. Uno desconocido nunca está sano (`arranque.sh` tampoco lo arranca)."""
EDAD_MAXIMA = 90
"""Segundos. Las vueltas de ambos ciclos duran unos pocos segundos."""


def latir() -> None:
    ARCHIVO.touch()


def sano() -> bool:
    proceso = os.environ.get("CEM_PROCESO", "api")
    if proceso not in PROCESOS:
        return False
    if proceso == "api":
        try:
            urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=4)
            return True
        except OSError:
            return False
    try:
        return time.time() - ARCHIVO.stat().st_mtime < EDAD_MAXIMA
    except FileNotFoundError:
        return False


if __name__ == "__main__":
    sys.exit(0 if sano() else 1)
