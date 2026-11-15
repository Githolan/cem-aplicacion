"""Las herramientas del equipo que dicen correr dentro del contenedor de `cem-backend` están en la
imagen (E60): el Dockerfile copia `backend/scripts/` archivo por archivo, y un script nuevo que no
se agrega a esa lista falla en el servidor con `No module named scripts.…`."""

import ast
import re

from tests.conftest import RAIZ

DOCKERFILE = RAIZ / "Dockerfile"
MARCA = "dentro del contenedor"


def copiados() -> set[str]:
    """Los archivos de `backend/scripts/` que el Dockerfile copia a la imagen."""
    texto = DOCKERFILE.read_text("utf-8").replace("\\\n", " ")
    return {
        nombre
        for linea in texto.splitlines()
        if linea.startswith("COPY ")
        for nombre in re.findall(r"backend/scripts/([\w.]+\.py)", linea)
    }


def para_el_contenedor() -> set[str]:
    """Los scripts cuyo docstring dice que corren dentro del contenedor (sin importar dónde corta
    la línea)."""
    return {
        ruta.name
        for ruta in (RAIZ / "scripts").glob("*.py")
        if MARCA in " ".join((ast.get_docstring(ast.parse(ruta.read_text("utf-8"))) or "").split())
    }


def test_la_marca_encuentra_los_scripts_que_ya_estaban_en_la_imagen():
    # Control: si la búsqueda no encontrara nada, la prueba de abajo pasaría sin probar nada.
    assert {"alta_consorcio.py", "invitar.py", "mandato_soporte.py"} <= para_el_contenedor()
    assert {"__init__.py", "alta_consorcio.py", "invitar.py", "mandato_soporte.py"} <= copiados()


def test_cada_script_para_el_contenedor_esta_en_la_imagen():
    assert para_el_contenedor() - copiados() == set()


def fuentes_de_copy(texto: str) -> list[str]:
    """Las fuentes de cada `COPY` (sin las opciones ni el destino), con las líneas continuadas."""
    unidas = texto.replace("\\\n", " ")
    fuentes = []
    for linea in unidas.splitlines():
        # `COPY --from=…` copia de otra etapa de la imagen, no del contexto del repositorio.
        if not linea.startswith("COPY ") or "--from=" in linea:
            continue
        partes = [p for p in linea.split()[1:] if not p.startswith("--")]
        fuentes += partes[:-1]
    return fuentes


def test_cada_fuente_de_copy_existe():
    """Un `\\n` escrito como texto en lugar de un salto de línea queda como una fuente más que no
    existe, y la imagen no se construye (Codex en #249). La lista de arriba no lo ve: busca nombres
    de archivo en el texto."""
    texto = DOCKERFILE.read_text("utf-8")
    contexto = RAIZ.parent
    faltan = [f for f in fuentes_de_copy(texto) if not (contexto / f).exists()]
    assert faltan == []
    # Control: la misma comprobación ve una fuente inventada y el escape literal.
    roto = "COPY backend/scripts/a.py \\n     backend/scripts/b.py scripts/\n"
    assert [f for f in fuentes_de_copy(roto) if not (contexto / f).exists()] == [
        "backend/scripts/a.py",
        "\\n",
        "backend/scripts/b.py",
    ]
