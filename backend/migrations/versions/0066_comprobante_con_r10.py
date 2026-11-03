"""La carga del comprobante de un pago manual exige R10 también en la base (P3 170).

`nucleo.crear_comprobante_de_pago` (`0063`) exigía la carga de documentos y haber visto la cuenta
de la ejecución abierta (`0065`), pero no la sesión reciente ni el segundo factor: los exigía solo
la ruta HTTP. Ahora recibe la sesión y aplica `nucleo.exigir_acto_economico` con
`egresos.ejecutar_pago`, la misma barrera del acto de ejecución en el que se vio la cuenta
(`0055`): facultad vigente, modo que admite egresos, sesión válida y reciente, segundo factor
reciente.

Cambia la firma (se agrega `sesion`): durante el despliegue el backend viejo llama la de cinco
argumentos y da error hasta que arranca el nuevo (P3 141: sin usuarios reales antes del piloto).

Downgrade: vuelve a la función de `0063`, sin sesión. No hay datos que dependan de esta.

Revisión: 0066
Anterior: 0065
"""

from alembic import op

revision = "0066"
down_revision = "0065"
branch_labels = None
depends_on = None

CATEGORIA = "facturas_y_comprobantes"
ABRIO = "nucleo.abrio_la_cuenta_de_la_orden"
CREAR_0063 = "nucleo.crear_comprobante_de_pago(uuid, text, date, uuid, text)"
CREAR_R10 = "nucleo.crear_comprobante_de_pago(uuid, text, date, uuid, text, uuid)"

CUERPO = f"""\
    perform nucleo.exigir_carga_documentos(c, actor, origen_pedido, '{CATEGORIA}');
    if not {ABRIO}(c, orden, actor) then
      raise exception 'carga el comprobante quien vio la cuenta en el acto de ejecución'
        using errcode = 'P0001', hint = 'no_es_quien_ejecuto';
    end if;
    insert into nucleo.documento (consorcio_id, categoria, titulo, fecha, cargado_por,
                                  orden_comprobante)
    values (c, '{CATEGORIA}', btrim(titulo_nuevo), fecha_nueva, actor, orden)
    returning * into d;
    -- El título no va a la auditoría: es texto libre.
    perform nucleo.auditar_documento(c, 'consorcio.documento.crear', 'documento', d.id, actor,
      origen_pedido, jsonb_build_object('categoria', d.categoria, 'fecha', d.fecha));
    return d.id;
  end
  $$;
"""

CON_R10 = f"""
drop function {CREAR_0063};
create function nucleo.crear_comprobante_de_pago(orden uuid, titulo_nuevo text,
                                                  fecha_nueva date, actor uuid,
                                                  origen_pedido text, sesion uuid)
  returns uuid
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    d nucleo.documento;
  begin
    -- R10, como el acto de ejecución en el que se vio la cuenta (0066).
    perform nucleo.exigir_acto_economico(c, actor, sesion, 'egresos.ejecutar_pago',
                                         origen_pedido);
{CUERPO}
revoke all on function {CREAR_R10} from public;
grant execute on function {CREAR_R10} to cem_app;
"""

SIN_R10 = f"""
drop function {CREAR_R10};
create function nucleo.crear_comprobante_de_pago(orden uuid, titulo_nuevo text,
                                                  fecha_nueva date, actor uuid,
                                                  origen_pedido text)
  returns uuid
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    d nucleo.documento;
  begin
{CUERPO}
revoke all on function {CREAR_0063} from public;
grant execute on function {CREAR_0063} to cem_app;
"""


def upgrade() -> None:
    op.execute(CON_R10)


def downgrade() -> None:
    op.execute(SIN_R10)
