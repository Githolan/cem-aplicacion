"""`0034`: `anular_vinculo` por soporte deja de escribir eventos.

La prueba del acto (con su control por la API) está junto a las demás de la anulación, en
`test_importacion_padron.py`. Aquí, la ida y vuelta de la migración y los permisos.
"""

import pytest
from alembic import command
from sqlalchemy import text

from tests.test_vinculo_0014 import base_aparte  # noqa: F401  (`base_aparte` es un fixture)

pytestmark = pytest.mark.db

ANULAR = "nucleo.anular_vinculo(uuid, text, text, uuid, text)"


def _funcion(motor) -> tuple:
    """Definición, permisos, `security definer` y quién puede ejecutarla."""
    with motor.connect() as c:
        return c.execute(
            text(
                "select pg_get_functiondef(p.oid), p.proacl::text, p.prosecdef,"
                " has_function_privilege('cem_app', p.oid, 'execute'),"
                " has_function_privilege('public', p.oid, 'execute')"
                " from pg_proc p where p.oid = to_regprocedure(:f)"
            ),
            {"f": ANULAR},
        ).one()


def test_0034_ida_y_vuelta_deja_la_funcion_como_estaba(base_aparte):  # noqa: F811
    cfg, motor = base_aparte
    command.upgrade(cfg, "0033")
    antes = _funcion(motor)
    command.upgrade(cfg, "0034")
    despues = _funcion(motor)
    # Control: la subida cambió la definición (las dos inserciones, envueltas y marcadas) y
    # conservó los permisos de `0025`: `cem_app` la ejecuta y `public` no.
    assert despues[0] != antes[0]
    assert despues[0].count("end if;  -- 0034") == 2
    assert despues[1:] == antes[1:]
    assert despues[3:] == (True, False)
    command.downgrade(cfg, "0033")
    assert _funcion(motor) == antes
