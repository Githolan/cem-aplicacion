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
- `se_abandono`: la cola agotó los intentos (`muerto`) por una falla que no es del antivirus
  (`otra_falla`). La pantalla deja de esperar y pide avisar a soporte. No ofrece subir otro
  archivo: con la versión en cuarentena, la factura (`0099`) y la documentación del proveedor
  (`0103`) rechazan otro.
- `proximo_intento`: cuándo vuelve a probar, mientras está `pendiente` después de una falla.

Nada más cambia en esa función: la misma barrera, las mismas filas (`0097`). El texto del error
nunca sale de la base: puede nombrar claves de objeto o rutas internas.

**Si la falla es del antivirus, el archivo no tiene la culpa.** `nucleo.revivir_verificaciones()`
vuelve a encolar (`pendiente`, intentos en cero) cada `documentos.verificar` muerto por
`FirmasViejas` o `AntivirusNoDisponible` cuya versión siga en cuarentena. La llama
`documentos.vigilar_cuarentena` (cada 5 minutos) solo cuando las firmas están al día y `clamd`
responde: el archivo se revisa solo, sin que nadie lo vuelva a subir. Mientras tanto la pantalla
dice que CEM lo vuelve a probar cuando el antivirus se recupere. Como `versiones_demoradas`
(`0035`), solo corre sin persona en la sesión, que es como corre un trabajo.

Downgrade: borra `revivir_verificaciones` y vuelve a la función de `0097`.

Revisión: 0136
Anterior: 0135
"""

from alembic import op

revision = "0136"
down_revision = "0135"
branch_labels = None
depends_on = None

MI_VERSION = "nucleo.estado_de_mi_version(uuid, uuid)"
REVIVIR = "nucleo.revivir_verificaciones()"

CAUSAS_DEL_ANTIVIRUS = "('FirmasViejas', 'AntivirusNoDisponible')"

REVIVIR_SQL = f"""
create function {REVIVIR}
  returns integer
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    n integer;
  begin
    if plataforma.persona_actual() is not null then
      raise exception 'solo un trabajo revive las verificaciones' using errcode = '42501';
    end if;
    update plataforma.jobs j
       set estado = 'pendiente', intentos = 0, disponible_desde = now(), terminado_en = null
      from nucleo.version_documento v
     where j.tipo = 'documentos.verificar' and j.estado = 'muerto'
       and split_part(j.error, ':', 1) in {CAUSAS_DEL_ANTIVIRUS}
       and j.clave_unica = 'documentos.verificar:' || v.id::text
       and v.estado = 'en_cuarentena';
    get diagnostics n = row_count;
    return n;
  end
  $$;
"""

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
             coalesce(
               v.estado = 'en_cuarentena' and j.estado = 'muerto'
               and split_part(j.error, ':', 1) not in ('FirmasViejas', 'AntivirusNoDisponible'),
               false
             ),
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
    op.execute(REVIVIR_SQL)
    op.execute(f"revoke all on function {REVIVIR} from public")
    op.execute(f"grant execute on function {REVIVIR} to cem_app")


def downgrade() -> None:
    op.execute(f"drop function {REVIVIR}")
    _crear(ANTERIOR)
