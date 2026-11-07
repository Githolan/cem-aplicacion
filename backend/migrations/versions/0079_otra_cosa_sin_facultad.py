"""«Pagué otra cosa» para quien perdió la facultad (decisión 3 del propietario, 07-11-2026).

En «Por aclarar» quien vio la cuenta y ya no ejecuta solo tenía `no-realizado` o `pago`. Si
transfirió otro importe o a otra cuenta, ninguna de las dos era verdad.
`registrar_diferencia_pago_manual` toma la rama de `0068`: sin `egresos.ejecutar_pago`, solo quien
vio la cuenta de la ejecución abierta, con R10 y su identidad verificada, y primero si la vio. La
fila queda con `sin_facultad`, como `pago_manual`, y lo resuelve el Consejo igual que siempre.

**Exportación económica (`0069`):** `diferencia_pago` suma `sin_facultad`.

Downgrade: se niega (`downgrade_con_datos`) si hay una diferencia registrada sin la facultad.

Revisión: 0079
Anterior: 0078
"""

from alembic import op
from sqlalchemy import text

revision = "0079"
down_revision = "0078"
branch_labels = None
depends_on = None

REGISTRAR = (
    "nucleo.registrar_diferencia_pago_manual(uuid, integer, bytea, text, date, numeric, text, text,"
    " uuid, text, uuid, text, uuid)"
)
EXPORTAR = "nucleo.exportar_pagos(uuid, uuid)"

REGISTRAR_CAMBIOS = (
    (
        "    nueva uuid;\n  begin\n",
        "    nueva uuid;\n    sin_facultad boolean := false;\n  begin\n",
    ),
    (
        "    identidad := nucleo.exigir_acto_economico(c, actor, sesion, 'egresos.ejecutar_pago',\n"
        "                                              origen_pedido);\n",
        "    if c is null or nucleo.tiene_facultad_vigente(c, actor, 'egresos.ejecutar_pago',\n"
        "                                                  nucleo.hoy_del_consorcio(c)) then\n"
        "      identidad := nucleo.exigir_acto_economico(c, actor, sesion,"
        " 'egresos.ejecutar_pago',\n"
        "                                                origen_pedido);\n"
        "    else\n"
        "      -- Quien vio la cuenta de la ejecución abierta y dejó de ejecutar (como 0068).\n"
        "      -- Primero si la vio: a nadie más se le dice nada del consorcio ni de la orden.\n"
        "      if actor is null or actor is distinct from plataforma.persona_actual()\n"
        "         or not nucleo.abrio_la_cuenta_de_la_orden(c, orden, actor) then\n"
        "        raise exception 'sin egresos.ejecutar_pago en este consorcio'"
        " using errcode = '42501';\n"
        "      end if;\n"
        "      identidad := nucleo.exigir_sesion_economica(c, actor, sesion, origen_pedido);\n"
        "      sin_facultad := true;\n"
        "    end if;\n",
    ),
    (
        "       fuera_de_termino, registrada_por, identidad_registro)\n",
        "       fuera_de_termino, registrada_por, identidad_registro, sin_facultad)\n",
    ),
    (
        "            btrim(explicacion_nueva), tarde, actor, identidad)\n",
        "            btrim(explicacion_nueva), tarde, actor, identidad, sin_facultad)\n",
    ),
    (
        "                         'fuera_de_termino', tarde));\n",
        "                         'fuera_de_termino', tarde, 'sin_facultad', sin_facultad));\n",
    ),
)
EXPORTAR_CAMBIOS = (
    (
        "                                'fuera_de_termino', f.fuera_de_termino,\n"
        "                                'registrada_por_nombre',",
        "                                'fuera_de_termino', f.fuera_de_termino,\n"
        "                                'sin_facultad', f.sin_facultad,\n"
        "                                'registrada_por_nombre',",
    ),
)

COLUMNA = """
-- La registró quien vio la cuenta y ya no tenía egresos.ejecutar_pago (0079).
alter table nucleo.diferencia_pago_manual
  add column sin_facultad boolean not null default false;
"""

GUARDA_DOWNGRADE = """
lock table nucleo.diferencia_pago_manual in access exclusive mode;
do $$
begin
  if exists (select 1 from nucleo.diferencia_pago_manual where sin_facultad) then
    raise exception '0079 no baja con diferencias registradas sin la facultad'
      using errcode = 'P0001', hint = 'downgrade_con_datos';
  end if;
end
$$;
"""


def _reemplazar(funcion: str, cambios, *, al_reves: bool = False) -> None:
    definicion = (
        op.get_bind()
        .execute(text("select pg_get_functiondef(cast(:f as regprocedure))"), {"f": funcion})
        .scalar_one()
    )
    for antes, despues in reversed(cambios) if al_reves else cambios:
        if al_reves:
            antes, despues = despues, antes
        if definicion.count(antes) != 1:
            raise RuntimeError(f"0079: {funcion} no tiene exactamente una vez {antes!r}")
        definicion = definicion.replace(antes, despues)
    op.execute(definicion)


def upgrade() -> None:
    op.execute(COLUMNA)
    _reemplazar(REGISTRAR, REGISTRAR_CAMBIOS)
    _reemplazar(EXPORTAR, EXPORTAR_CAMBIOS)


def downgrade() -> None:
    op.execute(GUARDA_DOWNGRADE)
    _reemplazar(EXPORTAR, EXPORTAR_CAMBIOS, al_reves=True)
    _reemplazar(REGISTRAR, REGISTRAR_CAMBIOS, al_reves=True)
    op.execute("alter table nucleo.diferencia_pago_manual drop column sin_facultad")
