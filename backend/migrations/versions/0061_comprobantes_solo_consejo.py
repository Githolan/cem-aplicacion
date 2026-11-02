"""El comprobante de un pago manual lo ven solo el Consejo y quien lo subió (P3 148, 02-11).

Un comprobante bancario suele mostrar la cuenta destino completa. Hasta acá era un documento más de
«Facturas y comprobantes»: lo descargaba todo propietario por su vínculo y quien tenía la facultad
`documentos.ver`, también quien preparó la orden. El propietario decidió el 02-11-2026 que lo vea
solo el Consejo.

Un documento atado a un pago manual (`nucleo.comprobante_pago_manual`, desde `0055`) ya no aparece
en `nucleo.documentos_del_consorcio()` ni se descarga con `nucleo.version_para_descargar()`, salvo:

- para quien tiene `egresos.aprobar_orden` vigente: el Consejo, que aprueba y concilia (`0046`);
- de cada versión, para quien la subió: ya tiene el archivo.

A los demás la base les devuelve lo mismo que si el documento no existiera (la descarga responde
404). La atadura no se borra nunca, así que el comprobante queda restringido aunque el registro se
observe. Antes de registrarlo es un documento más de la categoría: el tramo entre la carga y el
registro queda como pendiente (P3).

Como atar un documento lo deja solo para el Consejo, el registro exige además que el documento del
comprobante se haya creado después del primer acto de ejecución de esa orden, y con él todas sus
versiones (`comprobante_invalido`): un comprobante bancario no existe antes de ver la cuenta, y así
una factura vieja, ni una versión nueva de ella, no se ata por error (revisiones de #189).

La exportación formal del consorcio (`0038`) no cambia: entrega todos los documentos a quien designa
el pedido, también los de las categorías restringidas.
"""

from alembic import op
from sqlalchemy import text

revision = "0061"
down_revision = "0060"
branch_labels = None
depends_on = None

AYUDANTES = """
-- Si el documento respalda un pago manual. La atadura no se borra (`0055`).
create function nucleo.es_comprobante_de_pago(c uuid, documento uuid)
  returns boolean
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select exists (select 1 from nucleo.comprobante_pago_manual p
                    where p.consorcio_id = c and p.documento_id = documento)
  $$;
revoke all on function nucleo.es_comprobante_de_pago(uuid, uuid) from public;

-- Quién ve los comprobantes de pago: el Consejo, que aprueba y concilia (`0046`).
create function nucleo.ve_comprobantes_de_pago(c uuid, quien uuid)
  returns boolean
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select nucleo.tiene_facultad_vigente(c, quien, 'egresos.aprobar_orden',
                                         nucleo.hoy_del_consorcio(c))
  $$;
revoke all on function nucleo.ve_comprobantes_de_pago(uuid, uuid) from public;
"""

LISTA = "nucleo.documentos_del_consorcio()"
REGISTRO = (
    "nucleo.registrar_pago_manual(uuid, integer, bytea, text, date, bigint, text, text, uuid,"
    " uuid, text, uuid)"
)
DESCARGA = "nucleo.version_para_descargar(uuid, uuid)"

FILTRO_0036 = (
    "         and (restringidos or d.categoria not in ('contratos_proveedores', 'otros'))\n"
)

COMPROBANTE_DE_OTRA = (
    "      raise exception 'ese comprobante ya respalda otro pago' using errcode = 'P0001',\n"
    "        hint = 'comprobante_de_otra_orden';\n"
    "    end if;\n"
)
POSTERIOR_AL_ACTO = """\
    -- El documento es posterior al acto de ejecución (0061), y así todas sus versiones: atar una
    -- factura vieja (o una versión nueva de ella) la dejaría solo para el Consejo. Si falla, la
    -- atadura de arriba se deshace con la transacción.
    if not exists (
         select 1 from nucleo.documento d
          where d.consorcio_id = c and d.id = documento
            and d.cargado_en >= (select min(a.abierta_en)
                                   from nucleo.apertura_cuenta_pago_manual a
                                  where a.consorcio_id = c and a.ejecucion_id = e.id)) then
      raise exception 'el comprobante es anterior al acto de ejecución'
        using errcode = 'P0001', hint = 'comprobante_invalido';
    end if;
"""

CAMBIOS = {
    LISTA: [
        (
            "    restringidos boolean;\n  begin\n",
            "    restringidos boolean;\n    consejo boolean;\n  begin\n",
        ),
        (
            "    restringidos := nucleo.lee_documentos(c, quien, true);\n",
            "    restringidos := nucleo.lee_documentos(c, quien, true);\n"
            "    consejo := nucleo.ve_comprobantes_de_pago(c, quien);\n",
        ),
        (
            "         and (x.estado = 'disponible' or x.subida_por = quien)\n",
            "         and (x.estado = 'disponible' or x.subida_por = quien)\n"
            "         and (consejo or x.subida_por = quien\n"
            "              or not nucleo.es_comprobante_de_pago(c, d.id))\n",
        ),
        (
            FILTRO_0036,
            FILTRO_0036 + "         and (consejo or not nucleo.es_comprobante_de_pago(c, d.id)\n"
            "              or exists (select 1 from nucleo.version_documento y\n"
            "                          where y.consorcio_id = c and y.documento_id = d.id\n"
            "                            and y.subida_por = quien))\n",
        ),
    ],
    REGISTRO: [(COMPROBANTE_DE_OTRA, COMPROBANTE_DE_OTRA + POSTERIOR_AL_ACTO)],
    DESCARGA: [
        (
            "    return query\n      select cat, x.estado",
            "    -- Comprobante de un pago: solo el Consejo o quien subió esa versión (0061).\n"
            "    if nucleo.es_comprobante_de_pago(c, documento)\n"
            "       and not nucleo.ve_comprobantes_de_pago(c, quien)\n"
            "       and not exists (select 1 from nucleo.version_documento y\n"
            "                        where y.consorcio_id = c and y.documento_id = documento\n"
            "                          and y.id = version and y.subida_por = quien) then\n"
            "      return;\n"
            "    end if;\n"
            "    return query\n      select cat, x.estado",
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
            raise RuntimeError(f"0061: {funcion} no tiene exactamente una vez {antes!r}")
        definicion = definicion.replace(antes, despues)
    op.execute(definicion)


def upgrade() -> None:
    op.execute(AYUDANTES)
    for funcion, cambios in CAMBIOS.items():
        _reemplazar(funcion, cambios)


def downgrade() -> None:
    for funcion, cambios in CAMBIOS.items():
        _reemplazar(funcion, [(despues, antes) for antes, despues in reversed(cambios)])
    op.execute("drop function nucleo.ve_comprobantes_de_pago(uuid, uuid)")
    op.execute("drop function nucleo.es_comprobante_de_pago(uuid, uuid)")
