"""Aceptar la liquidación importada entera (rediseño de la confirmación, RL1-A del 26-11-2026).

Quien acepta la liquidación confirma, en la misma transacción, cada dato que seguía pendiente: es
la confirmación humana de RN30 hecha de una vez, después de ver los controles.

**`aceptar_datos_pendientes`** (ruta, `liquidacion.importar` por `exigir_acto_importacion`, `0113`):
traba la importación (`importacion_para_decidir`, `0115`) **antes** de mirar qué datos siguen
pendientes, y confirma cada uno con `resolver_campo_preliminar` (`0115`): queda con quien aceptó,
la hora y su evento de auditoría, como si se hubiera confirmado de a uno. Un dato que otra persona
corrigió o descartó mientras tanto ya no está pendiente y no se toca: la traba ordena las dos
decisiones (revisión de Codex en cem-aplicacion 297). Devuelve cuántos confirmó. La importación
tiene que estar `para_confirmar`; si no, `importacion_decidida`.

Datos de prueba: SINTETICOS / NO REAL.

Revisión: 0135
Anterior: 0134
"""

from alembic import op

revision = "0135"
down_revision = "0134"
branch_labels = None
depends_on = None

RUTAS = ("nucleo.aceptar_datos_pendientes(uuid, uuid, text, uuid)",)

FUNCIONES = """
create function nucleo.aceptar_datos_pendientes(importacion uuid, actor uuid,
                                                origen_pedido text, sesion uuid)
  returns integer
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    campo uuid;
    n integer := 0;
  begin
    perform nucleo.exigir_acto_importacion(c, actor, sesion, origen_pedido);
    if nucleo.importacion_para_decidir(c, importacion) <> 'para_confirmar' then
      raise exception 'la importación no está para confirmar' using errcode = 'P0001',
        hint = 'importacion_decidida';
    end if;
    for campo in
      select x.id from nucleo.campo_preliminar x
       where x.consorcio_id = c and x.importacion_id = importacion and x.estado = 'pendiente'
       order by x.orden
    loop
      perform nucleo.resolver_campo_preliminar(importacion, campo, 'confirmado', null, actor,
                                               origen_pedido, sesion);
      n := n + 1;
    end loop;
    return n;
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
