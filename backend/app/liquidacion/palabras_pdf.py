"""Palabras de un PDF con su recuadro, para `app.liquidacion.mis_expensas` (H5-1).

Única parte de la lectura que toca el PDF. Usa `pdfplumber` con las mismas tolerancias con que se
midió el formato sobre las liquidaciones anonimizadas (`doc/descubrimiento/anonimizar_posiciones.py`
del repositorio de documentación).
"""

from __future__ import annotations

import io

import pdfplumber

from app.liquidacion.mis_expensas import Pagina, Palabra

TOLERANCIA_X = 1.5
"""Puntos entre letras para que sigan en la misma palabra."""
TOLERANCIA_Y = 2.0
"""Puntos de diferencia vertical para que dos letras estén en el mismo renglón."""


def paginas(contenido: bytes) -> list[Pagina]:
    """Las páginas del PDF con sus palabras; un PDF ilegible levanta la excepción de pdfplumber."""
    with pdfplumber.open(io.BytesIO(contenido)) as doc:
        return [
            Pagina(
                ancho=float(p.width),
                alto=float(p.height),
                palabras=tuple(
                    Palabra(
                        w["text"],
                        float(w["x0"]),
                        float(w["x1"]),
                        float(w["top"]),
                        float(w["bottom"]),
                    )
                    for w in p.extract_words(x_tolerance=TOLERANCIA_X, y_tolerance=TOLERANCIA_Y)
                ),
            )
            for p in doc.pages
        ]
