"""Regla de redacción en los mensajes del backend (pregunta 9 del propietario, 13-11-2026; tanda 4).

Lo que el backend le dice a la persona (el `mensaje` de un error, `detalle.mensajes`, los avisos y
los correos) va impersonal y en infinitivo, igual que el frontend. Se recorre cada texto del código
de `app/` (sin las docstrings, que no salen) con `cem_contracts.redaccion.faltas`, la misma regla y
el mismo archivo de palabras que usa la prueba del frontend. Cada exclusión dice por qué no se
muestra.
"""

import ast
from pathlib import Path

from cem_contracts.redaccion import faltas, reglas

APP = Path(__file__).resolve().parents[1] / "app"

NO_SE_MUESTRAN = {
    ("consorcios/lectura_carga.py", "INSTRUCCIONES"): "instrucciones al modelo que lee el archivo",
    ("consorcios/lectura_carga.py", "PEDIDO"): "instrucciones al modelo que lee el archivo",
    ("consorcios/lectura_comprobantes.py", "INSTRUCCIONES"): "instrucciones al modelo",
    ("consorcios/lectura_comprobantes.py", "PEDIDO"): "instrucciones al modelo",
}
"""Textos que nunca llegan a la persona: se los lee el modelo que copia los datos de un archivo."""


def _es_identificador(texto: str) -> bool:
    """Una ruta, una clave o un nombre de columna («/me/consorcios», «registrada_por_vos»)."""
    return not any(c.isspace() for c in texto) and any(c in texto for c in "/_:")


def textos_de(fuente: str, archivo: str) -> list[tuple[int, str]]:
    """Cada texto de un módulo que puede llegar a la persona, con su línea."""
    arbol = ast.parse(fuente)
    fuera: set[int] = set()
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            primero = nodo.body[0] if nodo.body else None
            if isinstance(primero, ast.Expr) and isinstance(primero.value, ast.Constant):
                fuera.add(id(primero.value))
        if isinstance(nodo, ast.Assign | ast.AnnAssign):
            destinos = nodo.targets if isinstance(nodo, ast.Assign) else [nodo.target]
            for destino in destinos:
                if isinstance(destino, ast.Name) and (archivo, destino.id) in NO_SE_MUESTRAN:
                    fuera.update(id(n) for n in ast.walk(nodo))
        # La docstring de un atributo: el texto suelto que sigue a una asignación.
        if isinstance(nodo, ast.Module | ast.ClassDef):
            for anterior, actual in zip(nodo.body, nodo.body[1:], strict=False):
                if (
                    isinstance(anterior, ast.Assign | ast.AnnAssign)
                    and isinstance(actual, ast.Expr)
                    and isinstance(actual.value, ast.Constant)
                ):
                    fuera.add(id(actual.value))
    return [
        (n.lineno, n.value)
        for n in ast.walk(arbol)
        if isinstance(n, ast.Constant)
        and isinstance(n.value, str)
        and id(n) not in fuera
        and n.value.strip()
        and not _es_identificador(n.value)
    ]


def _modulos() -> list[Path]:
    return sorted(APP.rglob("*.py"))


def test_control_el_detector_clasifica_los_controles_del_archivo():
    for texto in reglas().control_con_falta:
        assert faltas(texto), texto
    for texto in reglas().control_sin_falta:
        assert not faltas(texto), (texto, faltas(texto))


def test_control_el_recorrido_ve_un_mensaje_con_voseo_y_salta_la_docstring():
    fuente = (
        '"""Docstring: revisá esto."""\n'
        'X = 1\n"""Docstring del atributo: tenés."""\n'
        'def f():\n    raise ValueError("Revisá los datos del formulario.")\n'
    )
    hallados = textos_de(fuente, "otro.py")
    assert hallados == [(5, "Revisá los datos del formulario.")]
    assert faltas(hallados[0][1])


def test_control_el_recorrido_encuentra_un_mensaje_que_existe():
    todos = [t for m in _modulos() for _, t in textos_de(m.read_text("utf-8"), "")]
    assert "No se encontró lo pedido." in todos
    assert len(todos) > 1000


def test_las_exclusiones_existen():
    for archivo, nombre in NO_SE_MUESTRAN:
        assert f"{nombre} = " in (APP / archivo).read_text("utf-8"), (archivo, nombre)


def test_ningun_mensaje_del_backend_tiene_voseo_tuteo_ni_primera_persona():
    encontrados = [
        f"{m.relative_to(APP).as_posix()}:{linea}: {', '.join(f)} :: {texto[:100]}"
        for m in _modulos()
        for linea, texto in textos_de(m.read_text("utf-8"), m.relative_to(APP).as_posix())
        if (f := faltas(texto))
    ]
    assert encontrados == []
