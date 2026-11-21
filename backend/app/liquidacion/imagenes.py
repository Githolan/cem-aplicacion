"""Las imágenes que van al modelo cuando la liquidación no tiene texto (H5-5; diseño H5 §5).

Una foto (JPEG o PNG) o un PDF escaneado no tienen palabras que leer por posición. Este módulo los
convierte en imágenes de tamaño acotado para `lectura_ia`. El tamaño lo fija la plataforma, no el
archivo: así la cota de fichas de cada imagen es conocida antes de llamar y el tope de gasto (D4)
se puede reservar.

- **Una foto** se gira según su EXIF (el celular guarda la orientación aparte), se reduce y se
  vuelve a codificar como JPEG. Volver a codificar descarta todo lo que no es la imagen: metadatos,
  perfiles y miniaturas.
- **Un PDF sin texto** se dibuja página por página con `pypdfium2`, la misma biblioteca que trae
  `pdfplumber`.
"""

from __future__ import annotations

import io
from math import sqrt

import pypdfium2
from PIL import Image, ImageOps, UnidentifiedImageError

LADO_MAXIMO = 1568
"""Píxeles del lado más largo: el que Anthropic usa sin volver a reducir la imagen."""
PIXELES_MAXIMOS = 1_150_000
"""Píxeles en total. Con Anthropic, una imagen cuesta unas ancho × alto / 750 fichas: con este tope,
menos de 1534. `lectura_ia.FICHAS_POR_IMAGEN` toma una cota mayor, que cubre también la cuenta por
mosaicos de los proveedores compatibles con OpenAI."""
MAXIMO_PAGINAS = 10
"""Una liquidación de 60 unidades tiene unas cinco páginas. Una más larga no se manda: se carga a
mano, como el texto que pasa de `lectura_ia.LARGO_MAXIMO`."""
CALIDAD_JPEG = 85
TIPO = "image/jpeg"
"""El tipo de toda imagen que sale de acá."""


class ImagenIlegible(ValueError):
    """El archivo no se pudo abrir como imagen o como PDF. El mensaje no lleva su contenido."""


class DemasiadasPaginas(ValueError):
    """El PDF tiene más de `MAXIMO_PAGINAS` páginas."""


def _escala(ancho: float, alto: float) -> float:
    """El factor que lleva la imagen hasta el primero de los dos topes."""
    return min(LADO_MAXIMO / max(ancho, alto), sqrt(PIXELES_MAXIMOS / (ancho * alto)))


def _dentro(imagen: Image.Image) -> Image.Image:
    """La imagen achicada hasta los topes, si se pasa; nunca agrandada."""
    factor = _escala(*imagen.size)
    if factor >= 1.0:
        return imagen
    tamano = (max(1, int(imagen.width * factor)), max(1, int(imagen.height * factor)))
    return imagen.resize(tamano, Image.Resampling.LANCZOS)


def _jpeg(imagen: Image.Image) -> bytes:
    salida = io.BytesIO()
    imagen.convert("RGB").save(salida, format="JPEG", quality=CALIDAD_JPEG)
    return salida.getvalue()


def de_foto(contenido: bytes) -> bytes:
    """La foto derecha, dentro de los topes y como JPEG."""
    try:
        with Image.open(io.BytesIO(contenido)) as abierta:
            abierta.load()
            derecha = ImageOps.exif_transpose(abierta)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as e:
        raise ImagenIlegible(type(e).__name__) from None
    return _jpeg(_dentro(derecha))  # una foto no se agranda: no ganaría nada legible


def de_pdf(contenido: bytes) -> list[bytes]:
    """Cada página del PDF dibujada dentro de los topes, como JPEG."""
    try:
        doc = pypdfium2.PdfDocument(contenido)
    except pypdfium2.PdfiumError as e:
        raise ImagenIlegible(type(e).__name__) from None
    try:
        if len(doc) > MAXIMO_PAGINAS:
            raise DemasiadasPaginas(f"{len(doc)} páginas")
        imagenes = []
        for i in range(len(doc)):
            pagina = doc[i]
            # El tamaño de la página está en puntos (1/72 de pulgada) y la escala 1 da un píxel por
            # punto: una A4 quedaría en 595 × 842, poco para leer. Se dibuja hasta los topes;
            # pdfium redondea cada lado hacia arriba, así que después se recorta lo que se pase.
            ancho, alto = pagina.get_size()
            dibujada = pagina.render(scale=_escala(ancho, alto)).to_pil()
            imagenes.append(_jpeg(_dentro(dibujada)))
        return imagenes
    finally:
        doc.close()
