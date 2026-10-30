"""H3: lecturas de órdenes de pago y proveedores, y permiso de `cem_app` para las rutas.

Diseño: `doc/diseno/H3_Proveedor_Beneficiario_Cuenta.md` del repositorio CEM, «Rutas», con las
decisiones del propietario del 29-10-2026 (`Decisiones_Propietario_2026-10-29.md`): aprobar es
del Consejo (decisión 2), preparar sigue el paquete vigente, y no se fija umbral, franjas ni plazo
(decisión 1).

**Qué agrega.**
- `nucleo.exigir_lectura_egresos`: la barrera de toda lectura. Admite solo
  `egresos.preparar_orden` y `egresos.aprobar_orden` y aplica `exigir_acto_economico`, la misma de
  las escrituras: facultad vigente, modo con egresos, sesión de menos de 12 horas, segundo factor
  de menos de 10 minutos e identidad humana verificada. Cualquier otro código se niega (42501).
- `nucleo.ordenes_pago_lectura` y `nucleo.orden_pago_versiones_lectura`: la orden con su versión
  vigente, y cada versión con el proveedor exacto que fija (razón social, CUIT y cuenta) y quiénes
  la aprobaron. Con `egresos.aprobar_orden` no se ven los borradores: una orden que nunca se
  envió a revisión es trabajo de quien prepara.
- `nucleo.proveedores_lectura` y `nucleo.proveedor_versiones_lectura`, con
  `egresos.preparar_orden`: quien aprueba ve el proveedor dentro de la versión de la orden.
- `cem_app` recibe EXECUTE sobre esas lecturas y sobre preparar, modificar, enviar a revisión,
  aprobar, rechazar, el alta de proveedor y su versión nueva. **No** sobre `emitir_orden_pago`:
  emitir es el paso hacia el pago, y no hay ruta para eso.

**Lo que no hace.** No cambia ninguna regla de las escrituras, no crea facultades y no da acceso a
ninguna tabla: todo pasa por las funciones.

**Downgrade.** Quita los permisos y las lecturas; no toca datos.

Revisión: 0049
Anterior: 0048
"""

from alembic import op

revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None

LECTURAS = (
    "nucleo.ordenes_pago_lectura(uuid, text, uuid, text, uuid)",
    "nucleo.orden_pago_versiones_lectura(uuid, text, uuid, text, uuid)",
    "nucleo.proveedores_lectura(uuid, uuid, text, uuid)",
    "nucleo.proveedor_versiones_lectura(uuid, uuid, text, uuid)",
)
ESCRITURAS = (
    "nucleo.preparar_orden_pago(bigint, text, text, uuid, text, boolean, uuid, text, uuid)",
    "nucleo.modificar_orden_pago(uuid, integer, bigint, text, text, uuid, text, boolean, uuid,"
    " text, uuid)",
    "nucleo.enviar_orden_a_revision(uuid, integer, uuid, text, uuid)",
    "nucleo.registrar_aprobacion_orden_pago(uuid, integer, bytea, uuid, text, uuid)",
    "nucleo.rechazar_orden_pago(uuid, integer, text, uuid, text, uuid)",
    "nucleo.alta_proveedor(text, text, text, text, uuid, text, uuid, text, uuid)",
    "nucleo.nueva_version_proveedor(uuid, integer, text, text, text, nucleo.estado_proveedor,"
    " uuid, text, uuid, text, uuid)",
)
BARRERA = "nucleo.exigir_lectura_egresos(uuid, text, uuid, text, uuid)"

UPGRADE = """
create function nucleo.exigir_lectura_egresos(c uuid, facultad text, actor uuid,
                                              origen_pedido text, sesion uuid)
  returns void
  language plpgsql volatile
  set search_path = pg_catalog, pg_temp
  as $$
  begin
    if facultad is null
       or facultad not in ('egresos.preparar_orden', 'egresos.aprobar_orden') then
      raise exception 'esa facultad no lee egresos' using errcode = '42501';
    end if;
    perform nucleo.exigir_acto_economico(c, actor, sesion, facultad, origen_pedido);
  end
  $$;

create function nucleo.ordenes_pago_lectura(orden uuid, facultad text, actor uuid,
                                            origen_pedido text, sesion uuid)
  returns table (id uuid, estado text, version_vigente integer, preparada_por uuid,
                 creada_en timestamptz, actualizada_en timestamptz, motivo_rechazo text,
                 importe_centavos bigint, moneda text, concepto text, proveedor_id uuid,
                 razon_social text, condicion_beneficiario text, usa_fondo_reserva boolean,
                 aprobaciones_requeridas smallint, aprobaciones integer)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
  begin
    perform nucleo.exigir_lectura_egresos(c, facultad, actor, origen_pedido, sesion);
    return query
      select o.id, o.estado::text, o.version_vigente, o.preparada_por, o.creada_en,
             o.actualizada_en, o.motivo_rechazo, v.importe_centavos, v.moneda, v.concepto,
             v.proveedor_id, pv.razon_social, v.condicion_beneficiario, v.usa_fondo_reserva,
             v.aprobaciones_requeridas,
             (select count(*)::integer from nucleo.orden_pago_aprobacion a
               where a.consorcio_id = c and a.orden_id = o.id and a.version = o.version_vigente)
        from nucleo.orden_pago o
        join nucleo.orden_pago_version v
          on v.consorcio_id = o.consorcio_id and v.orden_id = o.id
         and v.numero = o.version_vigente
        join nucleo.proveedor_version pv
          on pv.consorcio_id = v.consorcio_id and pv.proveedor_id = v.proveedor_id
         and pv.numero = v.proveedor_version
       where o.consorcio_id = c
         and (orden is null or o.id = orden)
         and (facultad = 'egresos.preparar_orden' or o.estado <> 'borrador')
       order by o.creada_en desc, o.id;
  end
  $$;

create function nucleo.orden_pago_versiones_lectura(orden uuid, facultad text, actor uuid,
                                                    origen_pedido text, sesion uuid)
  returns table (numero integer, importe_centavos bigint, moneda text, concepto text,
                 comprobante text, usa_fondo_reserva boolean, huella text, preparada_por uuid,
                 creada_en timestamptz, aprobaciones_requeridas smallint, proveedor_id uuid,
                 proveedor_version integer, condicion_beneficiario text, razon_social text,
                 cuit text, cuenta_destino text, aprobada_por uuid[])
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
  begin
    perform nucleo.exigir_lectura_egresos(c, facultad, actor, origen_pedido, sesion);
    return query
      select v.numero, v.importe_centavos, v.moneda, v.concepto, v.comprobante,
             v.usa_fondo_reserva, encode(v.huella, 'hex'), v.preparada_por, v.creada_en,
             v.aprobaciones_requeridas, v.proveedor_id, v.proveedor_version,
             v.condicion_beneficiario, pv.razon_social, p.cuit, pv.cuenta_destino,
             coalesce((select array_agg(a.persona_id order by a.creada_en, a.id)
                         from nucleo.orden_pago_aprobacion a
                        where a.consorcio_id = c and a.orden_id = v.orden_id
                          and a.version = v.numero), '{}')
        from nucleo.orden_pago o
        join nucleo.orden_pago_version v on v.consorcio_id = o.consorcio_id and v.orden_id = o.id
        join nucleo.proveedor p on p.consorcio_id = v.consorcio_id and p.id = v.proveedor_id
        join nucleo.proveedor_version pv
          on pv.consorcio_id = v.consorcio_id and pv.proveedor_id = v.proveedor_id
         and pv.numero = v.proveedor_version
       where o.consorcio_id = c and o.id = orden
         and (facultad = 'egresos.preparar_orden' or o.estado <> 'borrador')
       order by v.numero;
  end
  $$;

create function nucleo.proveedores_lectura(proveedor uuid, actor uuid, origen_pedido text,
                                           sesion uuid)
  returns table (id uuid, cuit text, version integer, razon_social text, rubro text,
                 contacto text, estado text, documento_id uuid, cuenta_destino text, huella text,
                 alta_por uuid, creado_en timestamptz, actualizado_en timestamptz)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
  begin
    perform nucleo.exigir_lectura_egresos(c, 'egresos.preparar_orden', actor, origen_pedido,
                                          sesion);
    return query
      select p.id, p.cuit, pv.numero, pv.razon_social, pv.rubro, pv.contacto, pv.estado::text,
             pv.documento_id, pv.cuenta_destino, encode(pv.huella, 'hex'), p.alta_por,
             p.creado_en, pv.creada_en
        from nucleo.proveedor p
        join nucleo.proveedor_version pv
          on pv.consorcio_id = p.consorcio_id and pv.proveedor_id = p.id
         and pv.numero = (select max(x.numero) from nucleo.proveedor_version x
                           where x.consorcio_id = p.consorcio_id and x.proveedor_id = p.id)
       where p.consorcio_id = c and (proveedor is null or p.id = proveedor)
       order by pv.razon_social, p.id;
  end
  $$;

create function nucleo.proveedor_versiones_lectura(proveedor uuid, actor uuid,
                                                   origen_pedido text, sesion uuid)
  returns table (version integer, razon_social text, rubro text, contacto text, estado text,
                 documento_id uuid, cuenta_destino text, huella text, preparada_por uuid,
                 creada_en timestamptz)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
  begin
    perform nucleo.exigir_lectura_egresos(c, 'egresos.preparar_orden', actor, origen_pedido,
                                          sesion);
    return query
      select pv.numero, pv.razon_social, pv.rubro, pv.contacto, pv.estado::text,
             pv.documento_id, pv.cuenta_destino, encode(pv.huella, 'hex'), pv.preparada_por,
             pv.creada_en
        from nucleo.proveedor_version pv
       where pv.consorcio_id = c and pv.proveedor_id = proveedor
       order by pv.numero;
  end
  $$;
"""

UPGRADE += f"revoke all on function {BARRERA} from public;\n"
UPGRADE += "".join(f"revoke all on function {f} from public;\n" for f in LECTURAS)
UPGRADE += "".join(f"grant execute on function {f} to cem_app;\n" for f in LECTURAS + ESCRITURAS)

DOWNGRADE = "".join(f"revoke execute on function {f} from cem_app;\n" for f in ESCRITURAS)
DOWNGRADE += "".join(f"drop function {f};\n" for f in LECTURAS)
DOWNGRADE += f"drop function {BARRERA};\n"


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
