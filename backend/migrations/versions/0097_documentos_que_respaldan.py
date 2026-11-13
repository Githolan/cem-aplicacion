"""Lo que la pantalla de Documentos necesita saber antes de ofrecer un acto (informe de fricción,
tanda 6: G-02 y G-09). Dos lecturas nuevas; ninguna tabla cambia y nada se escribe.

- **Qué documentos respaldan algo** (`nucleo.documentos_que_respaldan`, G-02). Retirar un documento
  que algo guardado cita se niega (`0089`, `documento_referenciado`), pero la ficha lo ofrecía igual
  y recién lo decía después de pedir el motivo. Esta función devuelve los documentos vigentes del
  consorcio que algo cita, con la misma regla de `0089` (`nucleo.documento_referenciado`, que no se
  toca). La lee quien tiene la facultad `documentos.ver` (`documentos.ver_restringidos`): la misma
  que pide retirar. A los demás, 42501.
- **Cómo terminó el análisis de un archivo propio** (`nucleo.estado_de_mi_version`, G-09). Quien
  carga sin ver la lista (el administrador externo, documento 08 §4) subía un archivo y nunca se
  enteraba si el antivirus lo rechazaba. Esta función devuelve el estado y el motivo del rechazo de
  una versión **que subió quien pregunta**, de un documento vigente, si tiene alguna facultad de
  carga en el consorcio (`nucleo.exigir_carga_documentos`, la de subir). De otra persona, de otro
  consorcio o de un documento quitado o retirado: ninguna fila (la ruta responde 404).

Las dos son `security definer`, toman el consorcio y la persona de la sesión
(`plataforma.consorcio_actual`, `plataforma.persona_actual`) y solo las ejecuta `cem_app`.

Downgrade: borra las dos funciones; no hay datos que perder.

Revisión: 0097
Anterior: 0096
"""

from alembic import op

revision = "0097"
down_revision = "0096"
branch_labels = None
depends_on = None

RESPALDAN = "nucleo.documentos_que_respaldan()"
MI_VERSION = "nucleo.estado_de_mi_version(uuid, uuid)"

FUNCIONES = """
create function nucleo.documentos_que_respaldan()
  returns setof uuid
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
    -- La facultad `documentos.ver`: la que pide retirar (`0089`).
    if not nucleo.lee_documentos(c, quien, true) then
      raise exception 'sin documentos.ver en este consorcio' using errcode = '42501';
    end if;
    return query
      select d.id from nucleo.documento d
       where d.consorcio_id = c and d.estado = 'vigente'
         and nucleo.documento_referenciado(c, d.id);
  end
  $$;

create function nucleo.estado_de_mi_version(documento uuid, version uuid)
  returns table (estado text, motivo_rechazo text)
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
    -- La misma barrera que subir el archivo, sin categoría: alguna facultad de carga.
    perform nucleo.exigir_carga_documentos(c, quien, 'api', null);
    return query
      select v.estado, v.motivo_rechazo
        from nucleo.version_documento v
        join nucleo.documento d on d.consorcio_id = v.consorcio_id and d.id = v.documento_id
       where v.consorcio_id = c and v.documento_id = documento and v.id = version
         and v.subida_por = quien and d.estado = 'vigente';
  end
  $$;
"""


def upgrade() -> None:
    op.execute(FUNCIONES)
    for funcion in (RESPALDAN, MI_VERSION):
        op.execute(f"revoke all on function {funcion} from public")
        op.execute(f"grant execute on function {funcion} to cem_app")


def downgrade() -> None:
    for funcion in (MI_VERSION, RESPALDAN):
        op.execute(f"drop function {funcion}")
