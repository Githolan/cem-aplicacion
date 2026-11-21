"""Las imágenes que van al modelo (H5-5, `app/liquidacion/imagenes.py`), sin base ni red. Las dibuja
la prueba con Pillow. Datos SINTETICOS / NO REAL.
"""

import io

import pytest
from PIL import Image

from app.liquidacion import imagenes

ORIENTACION = 0x0112
"""La etiqueta EXIF de la orientación."""
GIRADA_90 = 6
"""El valor que guarda un celular sostenido de costado."""


def jpeg(ancho: int, alto: int, exif: bytes | None = None) -> bytes:
    salida = io.BytesIO()
    Image.new("RGB", (ancho, alto), "white").save(salida, format="JPEG",
                                                  **({"exif": exif} if exif else {}))  # fmt: skip
    return salida.getvalue()


def abrir(datos: bytes) -> Image.Image:
    return Image.open(io.BytesIO(datos))


def test_una_foto_grande_se_reduce_a_los_topes_y_sale_jpeg():
    salida = abrir(imagenes.de_foto(jpeg(4000, 3000)))
    assert salida.format == "JPEG"
    assert max(salida.size) <= imagenes.LADO_MAXIMO
    assert salida.width * salida.height <= imagenes.PIXELES_MAXIMOS
    # Conserva la proporción.
    assert abs(salida.width / salida.height - 4 / 3) < 0.01


def test_una_foto_chica_no_se_agranda():
    assert abrir(imagenes.de_foto(jpeg(300, 200))).size == (300, 200)


def test_la_foto_se_endereza_segun_su_exif_y_pierde_los_metadatos():
    exif = Image.Exif()
    exif[ORIENTACION] = GIRADA_90
    salida = abrir(imagenes.de_foto(jpeg(300, 200, exif.tobytes())))
    assert salida.size == (200, 300)
    assert ORIENTACION not in salida.getexif()
    # Control: sin la etiqueta, queda como estaba.
    assert abrir(imagenes.de_foto(jpeg(300, 200))).size == (300, 200)


def test_un_png_tambien_sale_jpeg():
    salida = io.BytesIO()
    Image.new("RGBA", (100, 50), (255, 255, 255, 0)).save(salida, format="PNG")
    assert abrir(imagenes.de_foto(salida.getvalue())).format == "JPEG"


def test_lo_que_no_es_una_imagen_no_se_abre():
    with pytest.raises(imagenes.ImagenIlegible):
        imagenes.de_foto(b"no es una imagen")


def pdf_de_hojas(n: int, tamano: tuple[int, int] = (595, 842)) -> bytes:
    salida = io.BytesIO()
    hojas = [Image.new("RGB", tamano, "white") for _ in range(n)]
    hojas[0].save(salida, format="PDF", save_all=True, append_images=hojas[1:], resolution=72)
    return salida.getvalue()


def test_cada_pagina_del_pdf_se_dibuja_hasta_los_topes():
    paginas = [abrir(p) for p in imagenes.de_pdf(pdf_de_hojas(3))]
    assert len(paginas) == 3
    for p in paginas:
        assert p.format == "JPEG"
        assert p.width * p.height <= imagenes.PIXELES_MAXIMOS
        # Una A4 a un píxel por punto quedaría en 595 × 842: se dibuja más grande, hasta el tope.
        assert p.width * p.height > 0.98 * imagenes.PIXELES_MAXIMOS


def test_un_pdf_de_mas_paginas_que_el_tope_no_se_dibuja():
    with pytest.raises(imagenes.DemasiadasPaginas):
        imagenes.de_pdf(pdf_de_hojas(imagenes.MAXIMO_PAGINAS + 1))
    # Control: justo en el tope, sí.
    assert len(imagenes.de_pdf(pdf_de_hojas(imagenes.MAXIMO_PAGINAS))) == imagenes.MAXIMO_PAGINAS


def test_lo_que_no_es_un_pdf_no_se_dibuja():
    with pytest.raises(imagenes.ImagenIlegible):
        imagenes.de_pdf(b"%PDF-1.4 roto")
