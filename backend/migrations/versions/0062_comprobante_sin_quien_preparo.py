"""El Consejo no ve el comprobante de una orden que esa misma persona preparó (P3 156, 02-11).

`0061` dejó el comprobante de un pago manual para el Consejo (`egresos.aprobar_orden`) y, de cada
versión, para quien la subió. Una misma persona puede tener mandato de Administración y de Consejo
(decisión 6): con eso recuperaba, por su rol de Consejo, el comprobante de una orden que preparó y
cuya cuenta el flujo de preparación le ocultó. El propietario decidió el 02-11-2026:

- quien subió la versión la sigue viendo (también si preparó la orden y después la ejecutó);
- el Consejo que no preparó esa orden lo ve;
- el Consejo que preparó esa orden, y no subió la versión, no lo ve;
- nadie más lo ve.

«Preparó» se compara por la identidad humana guardada en cada versión de la orden
(`identidad_preparo`, `0045`), no por la cuenta ni por los vínculos de hoy, más la barrera de
`0054`: la misma cuenta o la misma identidad de hoy que quien preparó. Si quien lee no tiene
identidad humana vigente en el consorcio, no lo ve por el Consejo (falla cerrada).

`nucleo.preparo_la_orden_del_comprobante(c, documento, quien)` lo dice para un documento atado;
la lista y la descarga de `0061` lo piden junto con `ve_comprobantes_de_pago`. La exportación
formal (`0038`) no cambia (P3 155, aceptado por el propietario).
"""

from alembic import op
from sqlalchemy import text

revision = "0062"
down_revision = "0061"
branch_labels = None
depends_on = None

AYUDANTE = """
-- Si quien lee preparó alguna versión de la orden que respalda el comprobante: por la identidad
-- guardada al preparar, o por la misma cuenta o identidad de hoy (`0054`). Sin identidad, sí.
create function nucleo.preparo_la_orden_del_comprobante(c uuid, documento uuid, quien uuid)
  returns boolean
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select nucleo.identidad_humana_de(c, quien) is null
        or exists (
             select 1 from nucleo.comprobante_pago_manual p
               join nucleo.ejecucion_orden_pago e
                 on e.consorcio_id = p.consorcio_id and e.id = p.ejecucion_id
               join nucleo.orden_pago o on o.consorcio_id = e.consorcio_id and o.id = e.orden_id
              where p.consorcio_id = c and p.documento_id = documento
                and (nucleo.misma_persona_humana(c, quien, o.preparada_por)
                     or exists (select 1 from nucleo.orden_pago_version v
                                 where v.consorcio_id = c and v.orden_id = o.id
                                   and (v.identidad_preparo = nucleo.identidad_humana_de(c, quien)
                                        or nucleo.misma_persona_humana(c, quien,
                                                                       v.preparada_por)))))
  $$;
revoke all on function nucleo.preparo_la_orden_del_comprobante(uuid, uuid, uuid) from public;
"""

LISTA = "nucleo.documentos_del_consorcio()"
DESCARGA = "nucleo.version_para_descargar(uuid, uuid)"

CONSEJO_QUE_NO_PREPARO = (
    "         and ((consejo and not nucleo.preparo_la_orden_del_comprobante(c, d.id, quien))\n"
)
SUBIDA_POR = "              or x.subida_por = quien\n"
NO_ES_COMPROBANTE = "              or not nucleo.es_comprobante_de_pago(c, d.id)\n"

CAMBIOS = {
    LISTA: [
        (
            "         and (consejo or x.subida_por = quien\n",
            CONSEJO_QUE_NO_PREPARO + SUBIDA_POR,
        ),
        (
            "         and (consejo or not nucleo.es_comprobante_de_pago(c, d.id)\n",
            CONSEJO_QUE_NO_PREPARO + NO_ES_COMPROBANTE,
        ),
    ],
    DESCARGA: [
        (
            "       and not nucleo.ve_comprobantes_de_pago(c, quien)\n",
            "       and not (nucleo.ve_comprobantes_de_pago(c, quien)\n"
            "                and not nucleo.preparo_la_orden_del_comprobante(c, documento,\n"
            "                                                                quien))\n",
        ),
    ],
}


def _reemplazar(funcion: str, cambios: list[tuple[str, str]]) -> None:
    definicion = (
        op.get_bind()
        .execute(text("select pg_get_functiondef(cast(:f as regprocedure))"), {"f": funcion})
        .scalar_one()
    )
    for antes, despues in cambios:
        if definicion.count(antes) != 1:
            raise RuntimeError(f"0062: {funcion} no tiene exactamente una vez {antes!r}")
        definicion = definicion.replace(antes, despues)
    op.execute(definicion)


def upgrade() -> None:
    op.execute(AYUDANTE)
    for funcion, cambios in CAMBIOS.items():
        _reemplazar(funcion, cambios)


def downgrade() -> None:
    for funcion, cambios in CAMBIOS.items():
        _reemplazar(funcion, [(despues, antes) for antes, despues in reversed(cambios)])
    op.execute("drop function nucleo.preparo_la_orden_del_comprobante(uuid, uuid, uuid)")
