"""La barrera entre la API y `cem-trabajos` (mensaje del propietario del 30-10-2026, puntos 37 a
39). Un solo rol de base (`cem_app`) sirve a los dos procesos; lo que separa a un trabajo de una
persona es el ámbito: un trabajo tiene consorcio y no tiene persona (`nucleo.exigir_trabajo`), y
todo acto económico exige que el actor sea la persona del ámbito (`exigir_acto_economico`).

Lo que se prueba acá:
- ninguna parte de la API abre un ámbito de trabajo (consorcio sin persona): solo los procesos sin
  persona lo hacen, y la lista es cerrada;
- con persona, las funciones de trabajo se niegan; sin persona, los actos de una persona también;
- los roles de soporte no ejecutan las funciones de pago.

Lo que NO cubre (INFERENCIA, documentado): quien ejecutara código dentro del proceso de la API
podría fijar cualquier ámbito, también uno de trabajo. Contra eso haría falta un rol de base
propio de `cem-trabajos`.
"""

import ast
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db import transaccion
from tests.test_egresos_1a import aprobada, e  # noqa: F401  (`e` es un fixture)
from tests.test_pagos_0051 import ejecuciones, emitida

pytestmark = pytest.mark.db
# ruff: noqa: F811

APP = Path(__file__).resolve().parents[1] / "app"
SIN_PERSONA = {"plataforma/trabajos.py", "plataforma/planificador.py", "plataforma/outbox.py"}
"""Los únicos módulos que abren un ámbito con consorcio y sin persona: los de `cem-trabajos` y
`cem-planificador` (el outbox lo despacha un trabajo)."""
FUNCIONES_DE_PAGO = (
    "nucleo.registrar_resultado_pago(uuid, text, text, text, text)",
    "nucleo.pedido_de_pago(uuid)",
)


def _ambitos_de_trabajo() -> set[str]:
    """Los módulos con una llamada a `transaccion` o `fijar_ambito` con `persona_id=None` y un
    `consorcio_id` que no es `None`."""
    encontrados = set()
    for archivo in APP.rglob("*.py"):
        for nodo in ast.walk(ast.parse(archivo.read_text(encoding="utf-8"))):
            if not isinstance(nodo, ast.Call):
                continue
            nombre = getattr(nodo.func, "id", getattr(nodo.func, "attr", None))
            if nombre not in ("transaccion", "fijar_ambito"):
                continue
            args = {k.arg: k.value for k in nodo.keywords}
            persona, consorcio = args.get("persona_id"), args.get("consorcio_id")
            sin_persona = isinstance(persona, ast.Constant) and persona.value is None
            con_consorcio = consorcio is not None and not (
                isinstance(consorcio, ast.Constant) and consorcio.value is None
            )
            if sin_persona and con_consorcio:
                encontrados.add(archivo.relative_to(APP).as_posix())
    return encontrados


def test_ninguna_parte_de_la_api_abre_un_ambito_de_trabajo():
    encontrados = _ambitos_de_trabajo()
    assert encontrados <= SIN_PERSONA, encontrados - SIN_PERSONA
    # Control: la búsqueda encuentra el de `cem-trabajos`, que sí existe.
    assert "plataforma/trabajos.py" in encontrados


def test_con_persona_no_se_registra_un_pago_ni_se_lee_su_pedido(motor_migrate, motor_app, e):
    o = emitida(motor_migrate, e)
    [x] = ejecuciones(motor_migrate, o)
    for sql in (
        "select nucleo.registrar_resultado_pago(:x, 'pagada', 'ordenar', 'SIM-1', null)",
        "select * from nucleo.pedido_de_pago(:x)",
    ):
        with (
            pytest.raises(DBAPIError) as error,
            transaccion(motor_app, persona_id=e.adm, consorcio_id=e.cons) as c,
        ):
            c.execute(text(sql), {"x": x["id"]})
        assert error.value.orig.diag.sqlstate == "42501"


def test_un_trabajo_no_actua_como_persona(motor_migrate, motor_app, e):
    """Sin persona en el ámbito, emitir en nombre del administrador se niega: el actor tiene que
    ser la persona del ámbito."""
    o = aprobada(motor_migrate, e)
    with (
        pytest.raises(DBAPIError) as error,
        transaccion(motor_app, persona_id=None, consorcio_id=e.cons) as c,
    ):
        c.execute(
            text("select nucleo.emitir_orden_pago(:o, 1, :actor, 'api', null)"),
            {"o": o, "actor": e.adm},
        )
    assert error.value.orig.diag.sqlstate == "42501"
    with motor_migrate.connect() as c:
        estado = c.execute(
            text("select estado from nucleo.orden_pago where id = :o"), {"o": o}
        ).scalar_one()
    assert estado == "aprobada"


def test_soporte_no_ejecuta_las_funciones_de_pago(motor_migrate):
    with motor_migrate.connect() as c:
        roles = c.execute(
            text("select rolname from pg_roles where rolname like 'cem_soporte%' order by 1")
        ).scalars().all()  # fmt: skip
        assert len(roles) >= 3, roles  # control: los roles de soporte existen en esta base
        for rol in roles:
            for funcion in FUNCIONES_DE_PAGO:
                puede = c.execute(
                    text("select has_function_privilege(:r, :f, 'EXECUTE')"),
                    {"r": rol, "f": funcion},
                ).scalar_one()
                assert puede is False, (rol, funcion)
        # Control: `cem_app` (el proceso de trabajos) sí las ejecuta.
        for funcion in FUNCIONES_DE_PAGO:
            assert c.execute(
                text("select has_function_privilege('cem_app', :f, 'EXECUTE')"), {"f": funcion}
            ).scalar_one()
