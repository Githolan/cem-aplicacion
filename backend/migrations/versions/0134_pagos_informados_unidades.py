"""Pagos informados, pantallas (PI-S6 del diseño «Pagos informados» del repositorio CEM): las
unidades para las que la persona puede informar un pago.

La pantalla tiene que ofrecer solo esas (regla de pantallas: lo que no se puede hacer no
aparece). Ninguna ruta las daba: `GET …/unidades` lista todas y `/me/consorcios` dice los tipos
de vínculo, sin las unidades.

**`unidades_para_informar_lectura`** (ruta): las unidades sin borrar de
`unidades_para_pagos_informados` (`0129`: vínculo de propietario, inquilino u ocupante vigente,
confirmado y que no es un importado pendiente), por designación. La barrera es la de leer pagos
informados (`exigir_lectura_pagos_informados`, `0129`), con la misma persona que la lectura
de los pagos. La API las agrega a `ListaPagosInformados.unidades`.

Datos de prueba: SINTETICOS / NO REAL.

Revisión: 0134
Anterior: 0133
"""

from alembic import op

revision = "0134"
down_revision = "0133"
branch_labels = None
depends_on = None

RUTAS = ("nucleo.unidades_para_informar_lectura(uuid, text, uuid)",)

FUNCIONES = """
create function nucleo.unidades_para_informar_lectura(actor uuid, origen_pedido text, sesion uuid)
  returns table (unidad_id uuid, designacion text)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
  begin
    perform nucleo.exigir_lectura_pagos_informados(c, actor, sesion, origen_pedido);
    return query
      select u.id, u.designacion
        from nucleo.unidad u
       where u.consorcio_id = c and u.eliminado_en is null
         and u.id = any(nucleo.unidades_para_pagos_informados(c, actor))
       order by u.designacion, u.id;
  end
  $$;
"""


def upgrade() -> None:
    op.execute(FUNCIONES)
    for funcion in RUTAS:
        op.execute(f"revoke all on function {funcion} from public")
        op.execute(f"grant execute on function {funcion} to cem_app")


def downgrade() -> None:
    for funcion in RUTAS:
        op.execute(f"drop function {funcion}")
