"""Dos lecturas que pidió la revisión de fricción por flujos (18-11-2026, tanda 10). No cambian
ninguna regla: cada una pasa por los controles de la lectura que llama o de la carga que ya existe.

- **`nucleo.lectura_por_aclarar` (FL-ACL-01).** Lo que la IA leyó del último comprobante que subió
  quien vio la cuenta y ya no ejecuta pagos, para cada orden de `pagos_por_aclarar` (que la llama:
  mismos controles de persona, sesión y origen). Solo con el archivo `disponible`, como la ficha de
  la orden. Las mismas columnas que `lectura_comprobante_lectura` (`0077`): la cuenta leída nunca
  se guardó, así que tampoco sale de acá; solo sus últimos dígitos y si coincide con la de la
  orden. Respeta la regla del propietario sobre la cuenta (quien prepara nunca recibe el CBU
  completo; quien ejecutó lo ve solo en el acto de ejecución manual): esta lectura no lo tiene.
- **`nucleo.mis_cargas_recientes` (FL-DOC-04).** Los archivos que subió la persona del ámbito desde
  una fecha, de documentos vigentes, con cómo terminó el análisis. La misma barrera que
  `estado_de_mi_version` (`0097`): alguna facultad de carga. Es para quien carga sin ver la lista.

Downgrade: borra las dos funciones; no guardan nada.

Revisión: 0111
Anterior: 0110
"""

from alembic import op

revision = "0111"
down_revision = "0110"
branch_labels = None
depends_on = None

TRABAJO_LEER = "documentos.leer"
"""El de `TRABAJO_LEER`, en `app/consorcios/lectura_comprobantes.py` (como en `0077`)."""

POR_ACLARAR = "nucleo.lectura_por_aclarar(uuid, text, uuid)"
MIS_CARGAS = "nucleo.mis_cargas_recientes(timestamptz, integer)"
NUEVAS = (POR_ACLARAR, MIS_CARGAS)

FUNCIONES = f"""
-- FL-ACL-01: la lectura del último comprobante propio de cada pago para aclarar, con los controles
-- de `pagos_por_aclarar` (la llama). Sin la cuenta: la tabla nunca la guardó.
create function nucleo.lectura_por_aclarar(actor uuid, origen_pedido text, sesion uuid)
  returns table (orden_id uuid, version_documento_id uuid, estado text, importe numeric,
                 moneda text, cuenta_ultimos_digitos text, cuenta_coincide boolean,
                 titular_destino text, numero_operacion text, fecha date)
  language sql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
    select a.orden_id, x.id,
           case when l.id is not null then case when l.legible then 'leida' else 'no_leida' end
                when j.estado::text in ('pendiente', 'en_curso') then 'leyendo'
                when j.estado::text = 'muerto' then 'no_leida'
                else 'sin_lectura' end,
           l.importe, l.moneda, l.cuenta_ultimos_digitos, l.cuenta_coincide, l.titular_destino,
           l.numero_operacion, l.fecha
      from nucleo.pagos_por_aclarar(actor, origen_pedido, sesion) a
      cross join lateral (
        select v.id, v.estado from nucleo.version_documento v
         where v.consorcio_id = a.consorcio_id and v.documento_id = a.comprobante_id
         order by v.subida_en desc, v.id desc limit 1) x
      left join nucleo.lectura_comprobante l
        on l.consorcio_id = a.consorcio_id and l.version_documento_id = x.id
      left join plataforma.jobs j
        on j.consorcio_id = a.consorcio_id
       and j.clave_unica = '{TRABAJO_LEER}:' || x.id::text
     where x.estado = 'disponible'
  $$;

-- FL-DOC-04: lo que subió la persona del ámbito desde `desde`, de documentos vigentes, con cómo
-- terminó el análisis. La barrera de `estado_de_mi_version` (0097): alguna facultad de carga.
create function nucleo.mis_cargas_recientes(desde timestamptz, maximo integer)
  returns table (documento_id uuid, version_id uuid, titulo text, categoria text, estado text,
                 motivo_rechazo text, subida_en timestamptz)
  language plpgsql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    quien uuid := plataforma.persona_actual();
  begin
    if c is null or quien is null then
      raise exception 'sin ámbito para leer los documentos' using errcode = '42501';
    end if;
    perform nucleo.exigir_carga_documentos(c, quien, 'api', null);
    return query
      select d.id, v.id, d.titulo, d.categoria::text, v.estado::text, v.motivo_rechazo::text,
             v.subida_en
        from nucleo.version_documento v
        join nucleo.documento d on d.consorcio_id = v.consorcio_id and d.id = v.documento_id
       where v.consorcio_id = c and v.subida_por = quien and d.estado = 'vigente'
         and v.subida_en >= desde
       order by v.subida_en desc, v.id desc
       limit maximo;
  end
  $$;
"""


def upgrade() -> None:
    op.execute(FUNCIONES)
    for funcion in NUEVAS:
        op.execute(f"revoke all on function {funcion} from public")
        op.execute(f"grant execute on function {funcion} to cem_app")


def downgrade() -> None:
    for funcion in reversed(NUEVAS):
        op.execute(f"drop function {funcion}")
