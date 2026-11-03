"""Cada ruta con ámbito de consorcio declara su acción con `requiere` (tabla de verdad, §6.5).

Una ruta que usa `ContextoDep` sin `requiere` decidiría a mano y saltearía las políticas. La
prueba recorre el árbol de dependencias de cada ruta de la app; el control arma una ruta así a
propósito y comprueba que el recorrido la detecta.
"""

from typing import Annotated

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.routing import APIRoute

from app.autorizacion.requiere import Permiso, Requiere, requiere
from app.config import Config
from app.contexto import ContextoDep, require_scope
from app.main import crear_app

_QUIEN_VIO_LA_CUENTA = (
    "0067 y 0068, P3 179 y 184: quien vio la cuenta y perdió la facultad ya no tiene ámbito en el"
    " consorcio; la ruta parte de la sesión y la base exige todo (responde 404 a cualquier otra"
    " persona)"
)
SIN_AMBITO = {
    f"['POST'] /api/v1/consorcios/{{consorcio_id}}/pagos-por-aclarar/{{orden_id}}/{resto}":
        _QUIEN_VIO_LA_CUENTA
    for resto in ("no-realizado", "comprobante", "comprobante/{documento_id}/versiones", "pago")
}  # fmt: skip
"""Las únicas rutas con `{consorcio_id}` sin `requiere`, con su motivo. No usan el ámbito: no
deciden con un `Contexto` de políticas (`test_las_rutas_sin_ambito_no_usan_el_ambito`)."""


def llamadas(dependant) -> list:
    out = []
    for d in dependant.dependencies:
        out.append(d.call)
        out.extend(llamadas(d))
    return out


def rutas(routes) -> list[APIRoute]:
    """Las rutas de la app, bajando por los routers incluidos (FastAPI 0.141 los anida)."""
    out = []
    for r in routes:
        if isinstance(r, APIRoute):
            out.append(r)
        elif hasattr(r, "original_router"):
            out.extend(rutas(r.original_router.routes))
    return out


def sin_accion(app: FastAPI) -> list[str]:
    """Las rutas con ámbito de consorcio que no declaran su acción."""
    faltan = []
    for r in rutas(app.routes):
        calls = llamadas(r.dependant)
        con_ambito = "{consorcio_id}" in r.path or require_scope in calls
        nombre = f"{sorted(r.methods)} {r.path}"
        if (
            con_ambito
            and nombre not in SIN_AMBITO
            and not any(isinstance(c, Requiere) for c in calls)
        ):
            faltan.append(nombre)
    return faltan


def app_de_prueba() -> FastAPI:
    return crear_app(Config("", "0.1.0", "sha-imagen", "sha-contratos", redis_url="memory://"))


def test_cada_ruta_con_ambito_declara_su_accion():
    app = app_de_prueba()
    con_ambito = {r.path for r in rutas(app.routes) if "{consorcio_id}" in r.path}
    # Control: el recorrido encuentra las mismas rutas con ámbito que publica el OpenAPI.
    assert con_ambito == {x for x in app.openapi()["paths"] if "{consorcio_id}" in x}
    assert len(con_ambito) >= 3
    assert sin_accion(app) == []


def test_las_rutas_sin_ambito_no_usan_el_ambito():
    app = app_de_prueba()
    por_nombre = {f"{sorted(r.methods)} {r.path}": r for r in rutas(app.routes)}
    for nombre in SIN_AMBITO:
        calls = llamadas(por_nombre[nombre].dependant)  # existe
        assert require_scope not in calls and not any(isinstance(c, Requiere) for c in calls)


def test_control_una_ruta_con_contexto_sin_requiere_se_detecta():
    app = app_de_prueba()
    extra = APIRouter()

    @extra.get("/api/v1/consorcios/{consorcio_id}/a-mano")
    def a_mano(ctx: ContextoDep) -> dict:
        return {}

    @extra.get("/api/v1/consorcios/{consorcio_id}/declarada")
    def declarada(permiso: Annotated[Permiso, Depends(requiere("unidades.listar"))]) -> dict:
        return {}

    app.include_router(extra)
    assert sin_accion(app) == ["['GET'] /api/v1/consorcios/{consorcio_id}/a-mano"]


def test_una_accion_sin_politica_falla_al_declararse():
    with pytest.raises(ValueError, match="sin política declarada"):
        requiere("unidades.borrar_todo")
