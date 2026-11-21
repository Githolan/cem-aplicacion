"""Los enlaces que trae la liquidación: anotados, escritos o en un QR (H5-5; diseño H5 §5).

La Disposición 1146/24 de la Ciudad pide que la liquidación lleve enlaces (y su QR) a los
comprobantes del período y al estado de los pagos. Se anotan al leerla, **sin seguirlos**: son datos
del documento. Seguir uno es un pedido aparte de una persona (`descarga`).

Se buscan en tres lugares, en este orden, y cada dirección queda una sola vez:

- las **anotaciones** de enlace del PDF (el texto azul que se toca);
- el **texto** de cada página, con `http://` o `https://` escrito;
- los **QR** de cada página dibujada (`imagenes`) o de la foto, con `zxing-cpp`.

Se guardan solo `http` y `https`, hasta `MAXIMO_ENLACES` y de hasta `descarga.LARGO_MAXIMO_URL`
caracteres; lo demás se ignora (un `mailto:` o un `javascript:` no son un enlace a un archivo).
"""

from __future__ import annotations

import io
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

import pdfplumber
import zxingcpp
from PIL import Image

from app.liquidacion import descarga
from app.liquidacion.mis_expensas import Pagina

MAXIMO_ENLACES = 20
ESQUEMAS = frozenset({"http", "https"})
EN_EL_TEXTO = re.compile(r"https?://[^\s<>\"'()\[\]{}]+", re.IGNORECASE)
PUNTUACION_FINAL = ".,;:!?»"
"""Lo que termina una oración y no el enlace: «ver https://x.org/a.» deja `https://x.org/a`."""

Origen = Literal["anotacion", "texto", "qr"]


@dataclass(frozen=True)
class Enlace:
    url: str
    origen: Origen
    pagina: int


def _admitido(url: str) -> str | None:
    url = url.strip().rstrip(PUNTUACION_FINAL)
    if not url or len(url) > descarga.LARGO_MAXIMO_URL:
        return None
    try:
        partes = urlsplit(url)
    except ValueError:
        return None
    if partes.scheme.lower() not in ESQUEMAS or not partes.hostname:
        return None
    return url


def _unicos(candidatos: Iterable[Enlace]) -> list[Enlace]:
    vistos: set[str] = set()
    salida: list[Enlace] = []
    for e in candidatos:
        url = _admitido(e.url)
        if url is None or url in vistos:
            continue
        vistos.add(url)
        salida.append(Enlace(url, e.origen, e.pagina))
        if len(salida) == MAXIMO_ENLACES:
            break
    return salida


def _qr(imagen: bytes, pagina: int) -> list[Enlace]:
    with Image.open(io.BytesIO(imagen)) as abierta:
        codigos = zxingcpp.read_barcodes(abierta, formats=zxingcpp.BarcodeFormat.QRCode)
    return [Enlace(c.text, "qr", pagina) for c in codigos if c.valid]


def de_imagenes(imagenes: Sequence[bytes]) -> list[Enlace]:
    """Los QR de las imágenes que ya se armaron para el modelo (una foto o un PDF sin texto)."""
    return _unicos(e for k, i in enumerate(imagenes, 1) for e in _qr(i, k))


def de_pdf(contenido: bytes, paginas: Sequence[Pagina],
           dibujadas: Sequence[bytes]) -> list[Enlace]:  # fmt: skip
    """Los enlaces de un PDF con texto: anotaciones, texto y los QR de sus páginas `dibujadas`."""
    anotados: list[Enlace] = []
    with pdfplumber.open(io.BytesIO(contenido)) as doc:
        for k, p in enumerate(doc.pages, 1):
            anotados.extend(Enlace(str(h["uri"]), "anotacion", k) for h in p.hyperlinks
                            if h.get("uri"))  # fmt: skip
    escritos = [
        Enlace(m.group(0), "texto", k)
        for k, p in enumerate(paginas, 1)
        for m in EN_EL_TEXTO.finditer(" ".join(w.texto for w in p.palabras))
    ]
    en_qr = [e for k, i in enumerate(dibujadas, 1) for e in _qr(i, k)]
    return _unicos([*anotados, *escritos, *en_qr])
