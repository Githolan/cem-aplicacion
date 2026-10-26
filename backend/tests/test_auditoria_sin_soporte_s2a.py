"""S2a (`0033`): soporte deja de escribir los eventos de clase A.

Las pruebas de cada acto (con su control por la API) están junto a las de la función que lo hace:
`test_mandatos_invitaciones.py`, `test_vinculo_fin_0020.py`, `test_mandatos_0012.py`,
`test_identidad_humana.py` y `test_alta_consorcio.py`. Aquí, la ida y vuelta de la migración.
"""

import pytest
from alembic import command
from sqlalchemy import text

from tests.test_vinculo_0014 import base_aparte  # noqa: F401  (`base_aparte` es un fixture)

pytestmark = pytest.mark.db

CAMBIADAS = (
    "nucleo.invitar_a_mandato(uuid, text, text, text, bytea, timestamptz, uuid, text)",
    "nucleo.revocar_invitacion_mandato(uuid, uuid, text)",
    "nucleo.desasociar_persona_de_mandato(uuid, text, text, uuid, text)",
    "nucleo.finalizar_vinculo(uuid, text, uuid, text)",
    "nucleo.baja_mandato(uuid, nucleo.causa_baja, uuid, text, text, uuid, text)",
    "nucleo.verificar_identidad_humana(uuid, smallint[], bytea[], text, text, text)",
    "nucleo.vincular_identidad_humana(uuid, smallint[], bytea[], text, text, text)",
    "nucleo.desvincular_identidad_humana(uuid, text, text, text)",
    "nucleo.corregir_identidad_humana(uuid, smallint[], bytea[], text, text, text, text)",
)
AUDITAR = "nucleo.auditar_identidad(uuid, text, uuid, uuid, text)"


def _funciones(motor) -> dict[str, tuple | None]:
    """Definición, permisos y `security definer` de cada función; None si no existe."""
    with motor.connect() as c:
        return {
            f: c.execute(
                text(
                    "select pg_get_functiondef(p.oid), p.proacl::text, p.prosecdef"
                    " from pg_proc p where p.oid = to_regprocedure(:f)"
                ),
                {"f": f},
            ).one_or_none()
            for f in (*CAMBIADAS, AUDITAR)
        }


def test_0033_ida_y_vuelta_deja_las_funciones_como_estaban(base_aparte):  # noqa: F811
    cfg, motor = base_aparte
    command.upgrade(cfg, "0032")
    antes = _funciones(motor)
    assert all(antes[f] is not None for f in antes)
    command.upgrade(cfg, "0033")
    despues = _funciones(motor)
    # Control: la subida cambió cada función, y conservó permisos y `security definer`.
    assert all(despues[f][0] != antes[f][0] for f in CAMBIADAS)
    assert all(despues[f][1:] == antes[f][1:] for f in CAMBIADAS)
    assert despues[AUDITAR] is None
    assert all("auditar_identidad" not in despues[f][0] for f in CAMBIADAS[5:])
    command.downgrade(cfg, "0032")
    assert _funciones(motor) == antes
