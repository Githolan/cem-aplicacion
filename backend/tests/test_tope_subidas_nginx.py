"""Toda ruta que recibe un archivo pasa por la regla de 21 MB de nginx (H-C12, auditoría 16-11).

nginx corta en 1 MB por omisión, con un 413 suyo, antes de que el backend vea el pedido. La
regla de `frontend/nginx/cem-frontend.conf.template` que sube el tope a 21 MB nombra cada ruta de
carga en una expresión: una subida nueva que no se agrega ahí queda con 1 MB sin que nada falle
en las pruebas del backend (así quedaron la factura de la orden, la documentación del proveedor
y los adjuntos de la solicitud formal). Esta prueba recorre el OpenAPI de la app: cada operación
cuyo cuerpo es un archivo (`format: binary`) tiene que coincidir con la expresión, y ninguna otra.
"""

import re

from app.config import Config
from app.main import crear_app
from tests.conftest import RAIZ

PLANTILLA = RAIZ.parent / "frontend" / "nginx" / "cem-frontend.conf.template"
_REGLA = re.compile(r"location ~ (\S+) \{\s*client_max_body_size 21m;")
_PARAMETRO = re.compile(r"\{[^}]+\}")
ENTRADA_CORREO = "POST /api/v1/entrada/correo-pagos"
"""La entrada del correo de pagos (`0132`): un correo entero de hasta 25 MiB, con su regla."""
_REGLA_CORREO = re.compile(
    r"location = /api/v1/entrada/correo-pagos \{\s*client_max_body_size 27m;\s*"
    r"proxy_request_buffering off;"
)


def expresion_de_21m(plantilla: str) -> re.Pattern:
    regla = _REGLA.findall(plantilla)
    assert len(regla) == 1, f"se esperaba una sola regla de 21 MB, hay {len(regla)}"
    return re.compile(regla[0])


def operaciones(openapi: dict) -> tuple[set[str], set[str]]:
    """(rutas con cuerpo de archivo, rutas con otro cuerpo o sin cuerpo), con método."""
    con_archivo, otras = set(), set()
    for ruta, metodos in openapi["paths"].items():
        for metodo, op in metodos.items():
            contenido = op.get("requestBody", {}).get("content", {})
            binario = any(c.get("schema", {}).get("format") == "binary" for c in contenido.values())
            (con_archivo if binario else otras).add(f"{metodo.upper()} {ruta}")
    return con_archivo, otras


def coincide(expresion: re.Pattern, operacion: str) -> bool:
    ruta = _PARAMETRO.sub("6f1c2a7e-0000-4000-8000-000000000001", operacion.split(" ", 1)[1])
    return expresion.search(ruta) is not None


def test_toda_subida_de_archivo_tiene_el_tope_de_21_mb():
    expresion = expresion_de_21m(PLANTILLA.read_text(encoding="utf-8"))
    app = crear_app(Config("", "0.1.0", "", "", redis_url="memory://"))
    con_archivo, otras = operaciones(app.openapi())

    # Control del recorrido: encuentra las subidas que ya se sabe que existen.
    assert {
        "POST /api/v1/consorcios/{consorcio_id}/documentos/{documento_id}/versiones",
        "POST /api/v1/consorcios/{consorcio_id}/pagos/cotejo/extracto",
    } <= con_archivo

    assert ENTRADA_CORREO in con_archivo
    con_archivo.discard(ENTRADA_CORREO)
    sin_tope = sorted(o for o in con_archivo if not coincide(expresion, o))
    assert sin_tope == [], f"subidas que nginx corta en 1 MB: {sin_tope}"

    # La regla no es un comodín: ninguna ruta sin archivo cae en ella.
    de_mas = sorted(o for o in otras if coincide(expresion, o))
    assert de_mas == [], f"rutas sin archivo con el tope de 21 MB: {de_mas}"


def test_la_entrada_del_correo_tiene_su_regla_de_27_mb():
    """El correo de pagos (`0132`) llega entero: 25 MiB de Email Routing más la codificación. Su
    regla es exacta (`location =`) y no la toma la de 21 MB."""
    plantilla = PLANTILLA.read_text(encoding="utf-8")
    assert len(_REGLA_CORREO.findall(plantilla)) == 1
    assert not coincide(expresion_de_21m(plantilla), ENTRADA_CORREO)
    # Control: la misma búsqueda no encuentra la regla con otro tope.
    otra = plantilla.replace("client_max_body_size 27m", "client_max_body_size 1m")
    assert not _REGLA_CORREO.findall(otra)


def test_el_control_detecta_una_subida_que_falta():
    """Con la expresión anterior al arreglo, la prueba nombra las rutas que quedaban fuera y las
    subidas que se sumaron después (`0124`: los adjuntos de una reparación; `0129`: el comprobante
    de un pago informado)."""
    anterior = re.compile(
        r"^/api/v1/consorcios/[^/]+/((documentos/[^/]+|pagos-por-aclarar/[^/]+/comprobante/"
        r"[^/]+)/versiones|pagos/cotejo/(columnas|extracto))$"
    )
    app = crear_app(Config("", "0.1.0", "", "", redis_url="memory://"))
    con_archivo, _ = operaciones(app.openapi())
    assert sorted(o for o in con_archivo if not coincide(anterior, o)) == [
        "POST /api/v1/consorcios/{consorcio_id}/ordenes-pago/{orden_id}/factura",
        "POST /api/v1/consorcios/{consorcio_id}/pagos-informados/comprobantes",
        "POST /api/v1/consorcios/{consorcio_id}/proveedores/{proveedor_id}/documentacion",
        "POST /api/v1/consorcios/{consorcio_id}/reparaciones/{reparacion_id}/adjuntos",
        "POST /api/v1/consorcios/{consorcio_id}/solicitudes-formales/{solicitud_id}/adjuntos",
        ENTRADA_CORREO,
    ]
