"""Palabras de un PDF con su recuadro (H5-1): un PDF mínimo armado en la prueba."""

from __future__ import annotations

import pytest
from pdfplumber.utils.exceptions import PdfminerException

from app.liquidacion.palabras_pdf import paginas

ALTO = 841


def pdf(textos: list[tuple[float, float, str]]) -> bytes:
    """Un PDF de una página A4 con cada texto en (x, y) en Helvetica 10, coordenadas PDF."""
    flujo = "".join(f"BT /F1 10 Tf {x} {y} Td ({t}) Tj ET\n" for x, y, t in textos).encode()
    objetos = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 {ALTO}] /Contents 4 0 R "
        "/Resources << /Font << /F1 5 0 R >> >> >>".encode(),
        b"<< /Length " + str(len(flujo)).encode() + b" >>\nstream\n" + flujo + b"endstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    salida = bytearray(b"%PDF-1.4\n")
    posiciones = []
    for i, cuerpo in enumerate(objetos, 1):
        posiciones.append(len(salida))
        salida += f"{i} 0 obj\n".encode() + cuerpo + b"\nendobj\n"
    xref = len(salida)
    salida += f"xref\n0 {len(objetos) + 1}\n0000000000 65535 f \n".encode()
    salida += b"".join(f"{p:010d} 00000 n \n".encode() for p in posiciones)
    salida += (
        f"trailer\n<< /Size {len(objetos) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(salida)


def test_cada_palabra_sale_con_su_recuadro_en_coordenadas_desde_arriba():
    (pag,) = paginas(
        pdf([(43, 700, "Abono ascensor"), (320, 700, "450.000,00"), (31, 680, "TOTAL")])
    )
    assert (pag.ancho, pag.alto) == (595, ALTO)
    textos = [(p.texto, round(p.x0)) for p in sorted(pag.palabras, key=lambda p: (p.top, p.x0))]
    # «Abono » mide 31,7 puntos en Helvetica 10 (anchos 667+556·4+278 milésimas): 43 + 31,7 ≈ 75.
    assert textos == [("Abono", 43), ("ascensor", 75), ("450.000,00", 320), ("TOTAL", 31)]
    abono, ascensor, importe, total = sorted(pag.palabras, key=lambda p: (p.top, p.x0))
    assert abono.top == ascensor.top == importe.top
    assert total.top - abono.top == pytest.approx(20)  # 20 puntos más abajo en la página
    assert ALTO - 700 - 10 < abono.top < ALTO - 700
    assert abono.x1 < ascensor.x0 and importe.x1 > importe.x0


def test_un_archivo_que_no_es_pdf_falla():
    with pytest.raises(PdfminerException):
        paginas(b"no es un pdf")
