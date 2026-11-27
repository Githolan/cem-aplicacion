"""La pantalla dice por qué el antivirus no termina (regla del propietario del 27-11-2026: ningún
sistema falla en silencio).

El 27-11-2026 ClamAV llevaba dos días sin publicar firmas. `cem-trabajos` no aprobaba nada, como
corresponde, pero quien subió la liquidación solo veía «El antivirus está tardando más de lo
habitual; se sigue esperando.». El motivo estaba en la cola y nadie lo veía.

`nucleo.estado_de_mi_version` (`0097`) suma tres columnas, leídas del trabajo
`documentos.verificar` de la versión (`plataforma.jobs`, clave única
`documentos.verificar:<versión>`). Solo valen mientras la versión sigue `en_cuarentena`:

- `demora`: por qué falló el último intento. `firmas_viejas` (`FirmasViejas`: las firmas pasan de
  `CEM_ANTIVIRUS_FIRMAS_HORAS`), `antivirus_no_disponible` (`AntivirusNoDisponible`: `clamd` no
  responde o falta su configuración) u `otra_falla` (cualquier otra excepción). Sin intento
  fallido, nula. Se reconoce por el nombre de la excepción con que la cola empieza `error`
  (`app/plataforma/trabajos.py`: `f"{type(e).__name__}: {e}"`).
- `se_abandono`: la cola agotó los intentos (`muerto`). La pantalla deja de esperar y pide subirlo
  de nuevo.
- `proximo_intento`: cuándo vuelve a probar, mientras está `pendiente` después de una falla.

Nada más cambia: la misma barrera, las mismas filas (`0097`). El texto del error nunca sale de la
base: puede nombrar claves de objeto o rutas internas.

Downgrade: vuelve a la función de `0097`.

Revisión: 0136
Anterior: 0135
"""

from alembic import op

revision = "0136"
down_revision = "0135"
branch_labels = None
depends_on = None

MI_VERSION = "nucleo.estado_de_mi_version(uuid, uuid)"

CABECERA = """
create function nucleo.estado_de_mi_version(documento uuid, version uuid)
  returns table ({columnas})
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
"""

PIE = """
  end
  $$;
"""

NUEVA = (
    CABECERA.format(
        columnas="estado text, motivo_rechazo text, demora text, se_abandono boolean,"
        " proximo_intento timestamptz"
    )
    + """
      select v.estado, v.motivo_rechazo,
             case
               when v.estado <> 'en_cuarentena' or j.error is null then null
               when split_part(j.error, ':', 1) = 'FirmasViejas' then 'firmas_viejas'
               when split_part(j.error, ':', 1) = 'AntivirusNoDisponible'
                 then 'antivirus_no_disponible'
               else 'otra_falla'
             end,
             coalesce(v.estado = 'en_cuarentena' and j.estado = 'muerto', false),
             case
               when v.estado = 'en_cuarentena' and j.estado = 'pendiente' and j.error is not null
                 then j.disponible_desde
             end
        from nucleo.version_documento v
        join nucleo.documento d on d.consorcio_id = v.consorcio_id and d.id = v.documento_id
        left join plataforma.jobs j on j.clave_unica = 'documentos.verificar:' || v.id::text
       where v.consorcio_id = c and v.documento_id = documento and v.id = version
         and v.subida_por = quien and d.estado = 'vigente';
"""
    + PIE
)

ANTERIOR = (
    CABECERA.format(columnas="estado text, motivo_rechazo text")
    + """
      select v.estado, v.motivo_rechazo
        from nucleo.version_documento v
        join nucleo.documento d on d.consorcio_id = v.consorcio_id and d.id = v.documento_id
       where v.consorcio_id = c and v.documento_id = documento and v.id = version
         and v.subida_por = quien and d.estado = 'vigente';
"""
    + PIE
)


def _crear(definicion: str) -> None:
    op.execute(f"drop function {MI_VERSION}")
    op.execute(definicion)
    op.execute(f"revoke all on function {MI_VERSION} from public")
    op.execute(f"grant execute on function {MI_VERSION} to cem_app")


def upgrade() -> None:
    _crear(NUEVA)


def downgrade() -> None:
    _crear(ANTERIOR)
