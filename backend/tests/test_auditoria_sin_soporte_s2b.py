"""S2b (`0043`): soporte deja de escribir los eventos de clase B.

Las pruebas de cada acto (con su control por la API) están junto a las de la función que lo hace:
`test_decision_y_modo_actor.py`, `test_modo_operacion.py`, `test_mandatos.py`,
`test_mandatos_0012.py`, `test_revision_mandato.py`, `test_coeficientes_0021.py` y
`test_alta_consorcio.py`. Aquí, la ida y vuelta de la migración y la columna nueva.
"""

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from tests.test_mandatos_0012 import alta, baja, escena  # noqa: F401  (`escena` es un fixture)
from tests.test_vinculo_0014 import base_aparte  # noqa: F401  (`base_aparte` es un fixture)

pytestmark = pytest.mark.db

CAMBIADAS = (
    "nucleo.registrar_decision(nucleo.tipo_decision, date, text, text, uuid, text, text)",
    "nucleo.cambiar_modo(nucleo.tipo_modo, uuid, date, uuid, text)",
    "nucleo.alta_mandato(nucleo.tipo_mandato, uuid, text, text, text, uuid, date, date, text[],"
    " uuid, text)",
    "nucleo.resolver_revision_mandato(uuid, uuid, date, uuid, text)",
    "nucleo.registrar_esquema_coeficientes(text, text, date, uuid, jsonb, nucleo.metodo_reparto,"
    " nucleo.tratamiento_centavos, uuid, text)",
    "nucleo.baja_mandato(uuid, nucleo.causa_baja, uuid, text, text, uuid, text)",
)


def _estado(motor) -> tuple[dict, dict, bool]:
    """Definición, permisos y `security definer` de cada función; los permisos de `cem_app` sobre
    consorcio y unidad; si existe la columna nueva."""
    with motor.connect() as c:
        funciones = {
            f: tuple(
                c.execute(
                    text(
                        "select pg_get_functiondef(p.oid), p.proacl::text, p.prosecdef"
                        " from pg_proc p where p.oid = to_regprocedure(:f)"
                    ),
                    {"f": f},
                ).one()
            )
            for f in CAMBIADAS
        }
        permisos = {
            (tabla, privilegio): c.execute(
                text("select has_table_privilege('cem_app', :t, :p)"),
                {"t": tabla, "p": privilegio},
            ).scalar_one()
            for tabla in ("nucleo.consorcio", "nucleo.unidad")
            for privilegio in ("SELECT", "INSERT", "UPDATE", "DELETE")
        }
        columna = c.execute(
            text(
                "select exists (select 1 from information_schema.columns where table_schema ="
                " 'nucleo' and table_name = 'mandato' and column_name = 'vigente_hasta_antes_baja')"
            )
        ).scalar_one()
    return funciones, permisos, columna


def test_0043_ida_y_vuelta_deja_todo_como_estaba(base_aparte):  # noqa: F811
    cfg, motor = base_aparte
    command.upgrade(cfg, "0042")
    antes = _estado(motor)
    assert all(antes[1].values()) and not antes[2]  # control: en 0042, cem_app lo tenía todo
    command.upgrade(cfg, "0043")
    funciones, permisos, columna = _estado(motor)
    # La subida cambió cada función y conservó permisos y `security definer`.
    for f in CAMBIADAS:
        assert funciones[f][0] != antes[0][f][0], f
        assert funciones[f][1:] == antes[0][f][1:], f
        assert "origen_pedido <> 'soporte'" in funciones[f][0], f
    assert "error_carga') then" not in funciones[CAMBIADAS[-1]][0]
    assert permisos == {
        (t, p): p in ("SELECT", "INSERT")
        for t in ("nucleo.consorcio", "nucleo.unidad")
        for p in ("SELECT", "INSERT", "UPDATE", "DELETE")
    }
    assert columna
    command.downgrade(cfg, "0042")
    assert _estado(motor) == antes
    command.upgrade(cfg, "0043")  # control: vuelve a subir limpio
    assert _estado(motor)[2]


def test_el_fin_previo_solo_existe_en_un_mandato_dado_de_baja(escena, motor_migrate, motor_app):  # noqa: F811
    """El check ata `vigente_hasta_antes_baja` a la baja. Control: después de la baja, el mismo
    valor entra."""
    e = escena
    m = alta(motor_app, e["cons"], e["per"], e["acta"], tipo="miembro_consejo", persona=e["p1"])
    poner = text("update nucleo.mandato set vigente_hasta_antes_baja = current_date where id = :m")
    with pytest.raises(IntegrityError, match="mandato_vigente_hasta_antes_baja"):
        with motor_migrate.begin() as c:
            c.execute(poner, {"m": m})
    baja(motor_app, e["cons"], e["per"], m)
    with motor_migrate.begin() as c:
        assert c.execute(poner, {"m": m}).rowcount == 1
