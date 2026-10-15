"""Lista qué pruebas de aceptación (PA) del conjunto P0-piloto ya tienen una prueba.

Documento 14, §3 regla 3 y §11: cada PA lleva una prueba `test_paNN_…` y el informe dice cuáles
están cubiertas y cuáles faltan. Busca `def test_paNN_` en `backend/tests` y títulos que empiecen
con `PANN` en las pruebas del frontend (Vitest y Playwright).

Uso, desde `backend/`:  python -m scripts.cobertura_pa [--estricto]
Con `--estricto` sale con 1 si falta alguna (para la puerta G5); sin él solo informa.
Que una PA tenga prueba no la cierra: puede cubrir solo una parte (por ejemplo, el motor y no la
pantalla). La revisión de cada PA sigue siendo manual.
"""

import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]

# Documento 14, §7: pruebas obligatorias antes del piloto técnico.
P0_PILOTO = (
    [f"PA{n:02d}" for n in range(2, 13)]
    + [f"PA{n:02d}" for n in range(14, 18)]
    + [f"PA{n:02d}" for n in range(25, 33)]
    + ["PA36", "PA37"]
)

PYTHON = re.compile(r"^\s*(?:async\s+)?def\s+test_pa(\d{2})_", re.MULTILINE)
FRONTEND = re.compile(r"""\b(?:it|test|describe)\(\s*['"`]PA(\d{2})\b""")


def cobertura() -> dict[str, list[str]]:
    encontradas: dict[str, list[str]] = {}
    fuentes = [(p, PYTHON) for p in (RAIZ / "backend" / "tests").rglob("test_*.py")]
    for carpeta in (RAIZ / "frontend" / "src", RAIZ / "frontend" / "e2e"):
        pruebas = [p for p in carpeta.rglob("*.ts*") if ".test." in p.name or ".e2e." in p.name]
        fuentes += [(p, FRONTEND) for p in pruebas]
    for ruta, patron in fuentes:
        for numero in patron.findall(ruta.read_text(encoding="utf-8")):
            relativa = str(ruta.relative_to(RAIZ))
            lista = encontradas.setdefault(f"PA{numero}", [])
            if relativa not in lista:
                lista.append(relativa)
    return encontradas


def main(argumentos: list[str]) -> int:
    encontradas = cobertura()
    faltan = [pa for pa in P0_PILOTO if pa not in encontradas]
    cubiertas = len(P0_PILOTO) - len(faltan)
    print(f"PA del conjunto P0-piloto con prueba: {cubiertas} de {len(P0_PILOTO)}")
    for pa in sorted(encontradas):
        fuera = "" if pa in P0_PILOTO else " (fuera del P0-piloto)"
        print(f"  {pa}{fuera}: {', '.join(encontradas[pa])}")
    print(f"Sin prueba: {', '.join(faltan) if faltan else 'ninguna'}")
    return 1 if faltan and "--estricto" in argumentos else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
