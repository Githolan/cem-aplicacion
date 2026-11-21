"""Los enlaces de la liquidación (H5-5, `app/liquidacion/enlaces.py`), sin base ni red. Los PDF y
las imágenes los arma la prueba; los QR, `zxing-cpp`. Datos SINTETICOS / NO REAL.
"""

import io

import zxingcpp
from PIL import Image
from pypdf import PdfReader, PdfWriter
from pypdf.annotations import Link

from app.liquidacion import enlaces, imagenes, palabras_pdf
from tests.test_importacion_liquidacion_0113 import pdf_de
from tests.test_lectura_ia import pagina

COMPROBANTES = "https://comprobantes.prueba/2026-09/listado.pdf"
ESTADO = "https://estado.prueba/pagos?periodo=2026-09"
MANUAL = "https://manual.prueba/buenas-practicas.pdf"


def qr(texto: str, escala: int = 6) -> Image.Image:
    return Image.fromarray(zxingcpp.create_barcode(texto, zxingcpp.BarcodeFormat.QRCode)
                           .to_image(scale=escala))  # fmt: skip


def hoja_con_qr(*textos: str) -> Image.Image:
    hoja = Image.new("RGB", (1200, 1600), "white")
    for k, t in enumerate(textos):
        hoja.paste(qr(t), (80 + 400 * k, 1200))
    return hoja


def jpeg(imagen: Image.Image) -> bytes:
    salida = io.BytesIO()
    imagen.save(salida, format="JPEG", quality=90)
    return salida.getvalue()


def con_anotacion(contenido: bytes, url: str) -> bytes:
    escritor = PdfWriter(clone_from=PdfReader(io.BytesIO(contenido)))
    escritor.add_annotation(page_number=0, annotation=Link(rect=(40, 40, 200, 60), url=url))
    salida = io.BytesIO()
    escritor.write(salida)
    return salida.getvalue()


def test_los_qr_de_una_foto():
    foto = imagenes.de_foto(jpeg(hoja_con_qr(COMPROBANTES, ESTADO)))
    hallados = enlaces.de_imagenes([foto])
    assert {(e.url, e.origen, e.pagina) for e in hallados} == {
        (COMPROBANTES, "qr", 1), (ESTADO, "qr", 1)
    }  # fmt: skip


def test_una_foto_sin_qr_no_tiene_enlaces():
    assert (
        enlaces.de_imagenes([imagenes.de_foto(jpeg(Image.new("RGB", (600, 800), "white")))]) == []
    )


def test_un_pdf_con_anotacion_texto_y_qr_los_da_una_vez_cada_uno_en_orden():
    renglones = ("Liquidacion SINTETICA", f"Comprobantes en {COMPROBANTES}.", f"Estado: {ESTADO}")
    contenido = con_anotacion(pdf_de([pagina(renglones)]), MANUAL)
    paginas = palabras_pdf.paginas(contenido)
    # El QR va en otra página dibujada (la de un escaneo): repite un enlace y suma uno nuevo.
    dibujadas = [jpeg(hoja_con_qr(COMPROBANTES, "https://qr.prueba/solo-en-el-qr"))]
    hallados = enlaces.de_pdf(contenido, paginas, dibujadas)
    assert [(e.url, e.origen) for e in hallados] == [
        (MANUAL, "anotacion"),
        (COMPROBANTES, "texto"),  # sin el punto que cierra la oración
        (ESTADO, "texto"),
        ("https://qr.prueba/solo-en-el-qr", "qr"),
    ]


def test_solo_http_y_https_y_hasta_el_tope():
    raros = ["mailto:a@b.prueba", "javascript:alert(1)", "ftp://x.prueba/a", "https://"]
    candidatos = [enlaces.Enlace(u, "texto", 1) for u in raros]
    candidatos += [enlaces.Enlace(f"https://e{k}.prueba/", "texto", 1) for k in range(30)]
    salida = enlaces._unicos(candidatos)
    assert len(salida) == enlaces.MAXIMO_ENLACES
    assert all(e.url.startswith("https://e") for e in salida)


def test_una_anotacion_o_un_qr_quedan_exactos_y_el_texto_pierde_el_punto_final():
    """Codex: `?token=abc!` en una anotación o un QR es parte de la dirección."""
    exacta = "https://comprobantes.prueba/descarga?token=abc!"
    candidatos = [enlaces.Enlace(exacta, "anotacion", 1), enlaces.Enlace(exacta + "?", "qr", 1),
                  enlaces.Enlace("https://escrito.prueba/a.", "texto", 1)]  # fmt: skip
    assert [e.url for e in enlaces._unicos(candidatos)] == [
        exacta, exacta + "?", "https://escrito.prueba/a"
    ]  # fmt: skip
