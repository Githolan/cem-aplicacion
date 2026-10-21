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
