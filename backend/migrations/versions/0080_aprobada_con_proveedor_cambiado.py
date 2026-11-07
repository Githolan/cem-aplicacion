"""Aprobada con el proveedor cambiado: un paso nuevo en «Para preparar» (decisión 4 del propietario,
07-11-2026).

Con una orden aprobada y una versión nueva del proveedor, `exigir_proveedor_sin_cambios` niega pagar
(`proveedor_cambiado`) y la orden desaparecía de la bandeja: nadie veía que había que modificarla.
`nucleo.proveedor_cambio_tras_aprobar` dice solo sí o no, y `acciones_de_ordenes_lectura` se lo da a
quien prepara. No cambia ninguna regla: Modificar ya aceptaba esa orden, y la versión nueva vuelve a
pasar por aprobación.

Downgrade: vuelve a la definición anterior. No hay datos que dependan de esto.

Revisión: 0080
Anterior: 0079
"""

from alembic import op
from sqlalchemy import text

revision = "0080"
down_revision = "0079"
branch_labels = None
depends_on = None

ACCIONES = "nucleo.acciones_de_ordenes_lectura(uuid, uuid, text, uuid, interval)"
CAMBIO = "nucleo.proveedor_cambio_tras_aprobar(uuid, nucleo.orden_pago)"

FUNCION = """
create function nucleo.proveedor_cambio_tras_aprobar(c uuid, o nucleo.orden_pago)
  returns boolean
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select o.estado = 'aprobada' and exists (
      select 1 from nucleo.orden_pago_version v
       where v.orden_id = o.id and v.numero = o.version_vigente
         and (nucleo.proveedor_vigente(c, v.proveedor_id)).numero
             is distinct from v.proveedor_version)
  $$;
"""

ACCIONES_CAMBIOS = (
    (
        " tomar_desde timestamp with time zone)\n",
        " tomar_desde timestamp with time zone, proveedor_cambiado boolean)\n",
    ),
    (
        "             w.desde + plazo_toma\n",
        "             w.desde + plazo_toma,\n"
        "             prepara and nucleo.proveedor_cambio_tras_aprobar(c, o)\n",
    ),
)


def _acciones(*, al_reves: bool) -> None:
    """Cambia lo que devuelve: se borra y se crea de nuevo, con su permiso."""
    definicion = (
        op.get_bind()
        .execute(text("select pg_get_functiondef(cast(:f as regprocedure))"), {"f": ACCIONES})
        .scalar_one()
    )
    for antes, despues in ACCIONES_CAMBIOS:
        if al_reves:
            antes, despues = despues, antes
        if definicion.count(antes) != 1:
            raise RuntimeError(f"0080: {ACCIONES} no tiene exactamente una vez {antes!r}")
        definicion = definicion.replace(antes, despues)
    op.execute(f"drop function {ACCIONES}")
    op.execute(definicion)
    op.execute(f"revoke all on function {ACCIONES} from public")
    op.execute(f"grant execute on function {ACCIONES} to cem_app")


def upgrade() -> None:
    op.execute(FUNCION)
    op.execute(f"revoke all on function {CAMBIO} from public")
    _acciones(al_reves=False)


def downgrade() -> None:
    _acciones(al_reves=True)
    op.execute(f"drop function {CAMBIO}")
