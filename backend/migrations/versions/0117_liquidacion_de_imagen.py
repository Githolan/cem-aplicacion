"""La liquidación del administrador también como foto o PDF escaneado, H5-5 (diseño H5 §5).

Hasta `0116`, solo se importaba un PDF y la lectura con IA (`0114`) recibía su texto. Una foto de la
liquidación impresa (JPEG o PNG) o un PDF escaneado no tienen texto: `cem-trabajos` los manda al
modelo como imágenes de tamaño acotado (`app/liquidacion/imagenes.py`) y el modelo devuelve su
transcripción y los datos que cita de ella (`app/liquidacion/lectura_ia.py`).

- **`crear_importacion_liquidacion`** acepta una versión `disponible` de tipo PDF, JPEG o PNG, los
  tres que admiten los documentos (`0035`). El resto de la función queda como en `0113`.
- **Formato `lectura_ia_imagen`** en `nucleo.importacion_liquidacion`: leída con IA a partir de
  imágenes. La pantalla avisa que cada fragmento es la transcripción del modelo, no el texto del
  archivo.

Downgrade: se niega (`downgrade_con_datos`) si hay importaciones de una foto o leídas de imágenes.

Datos de prueba: SINTETICOS / NO REAL.

Revisión: 0117
Anterior: 0116
"""

from alembic import op

revision = "0117"
down_revision = "0116"
branch_labels = None
depends_on = None

CATEGORIA = "liquidaciones_del_administrador"
ACCION = "consorcio.liquidacion.importar"
TIPOS_AHORA = "('application/pdf', 'image/jpeg', 'image/png')"
TIPOS_ANTES = "('application/pdf')"
FORMATOS_AHORA = "('mis_expensas', 'lectura_ia', 'lectura_ia_imagen')"
FORMATOS_ANTES = "('mis_expensas', 'lectura_ia')"

CREAR = """
create or replace function nucleo.crear_importacion_liquidacion(documento uuid, version uuid,
                                                                actor uuid, origen_pedido text,
                                                                sesion uuid)
  returns uuid
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    v nucleo.version_documento;
    i uuid;
  begin
    perform nucleo.exigir_acto_importacion(c, actor, sesion, origen_pedido);
    select x.* into v from nucleo.version_documento x
      join nucleo.documento d on d.consorcio_id = x.consorcio_id and d.id = x.documento_id
     where x.consorcio_id = c and x.id = version and d.id = documento and d.estado = 'vigente'
       and d.categoria = '{categoria}';
    if v.id is null or v.estado <> 'disponible' or v.tipo not in {tipos} then
      raise exception '{mensaje}'
        using errcode = 'P0001', hint = 'documento_invalido';
    end if;
    if exists (select 1 from nucleo.importacion_liquidacion x
                where x.consorcio_id = c and x.version_documento_id = version) then
      raise exception 'esa versión ya se importó' using errcode = 'P0001', hint = 'ya_importada';
    end if;
    insert into nucleo.importacion_liquidacion
      (consorcio_id, documento_id, version_documento_id, subida_por)
    values (c, documento, version, actor)
    returning id into i;
    insert into auditoria.eventos
      (consorcio_id, actor_persona_id, origen, accion, entidad, entidad_id, despues,
       hash_previo, hash)
    values (c, actor, origen_pedido, '{accion}', 'importacion_liquidacion', i,
            jsonb_build_object('documento', documento, 'version', version), '', '');
    return i;
  end
  $$;
"""

FORMATO = """
alter table nucleo.importacion_liquidacion
  drop constraint importacion_liquidacion_formato_check,
  add constraint importacion_liquidacion_formato_check check (formato in {formatos});
"""

GUARDA = """
do $guarda$
begin
  if exists (select 1 from nucleo.importacion_liquidacion i
               join nucleo.version_documento v
                 on v.consorcio_id = i.consorcio_id and v.id = i.version_documento_id
              where v.tipo <> 'application/pdf' or i.formato = 'lectura_ia_imagen') then
    raise exception '0117 no baja: hay importaciones de una foto o leídas de imágenes'
      using errcode = 'P0001', hint = 'downgrade_con_datos';
  end if;
end
$guarda$;
"""


def upgrade() -> None:
    op.execute(FORMATO.format(formatos=FORMATOS_AHORA))
    op.execute(CREAR.format(
        categoria=CATEGORIA, accion=ACCION, tipos=TIPOS_AHORA,
        mensaje="la versión no es un PDF, JPEG o PNG disponible de una liquidación",
    ))  # fmt: skip


def downgrade() -> None:
    op.execute(GUARDA)
    op.execute(CREAR.format(
        categoria=CATEGORIA, accion=ACCION, tipos=TIPOS_ANTES,
        mensaje="la versión no es un PDF disponible de una liquidación",
    ))  # fmt: skip
    op.execute(FORMATO.format(formatos=FORMATOS_ANTES))
