"""RF05: lectura de los esquemas de coeficientes y sus reglas de reparto por la API.

Diseño: `doc/diseno/H2b_5_Diseno.md` del repositorio CEM, §3 («Por la API: solo lectura … para
quien tiene `padron.ver` o `coeficientes.proponer`»). Las tablas son las de `0021`; esta migración
solo agrega las dos lecturas de `GET …/coeficientes`, con la misma segunda barrera que
`padron_del_consorcio` (0014): un mandato vigente con `padron.ver` o con `coeficientes.proponer`
en el consorcio, con o sin recurso. El filtro por recurso (la unidad de cada coeficiente para
`padron.ver`, el grupo para `coeficientes.proponer`) lo hace la aplicación, fila por fila.

- **`nucleo.coeficientes_del_consorcio`:** cada versión de cada esquema con sus coeficientes; el
  porcentaje como texto, tal como lo guarda la base (D4), y la designación de la unidad. Sin la
  referencia de la fuente, la decisión, el origen ni quién la registró.
- **`nucleo.reglas_de_reparto_del_consorcio`:** las versiones de la regla de cada esquema; sin la
  decisión.

`cem_app` conserva el `select` de `0021` (el reparto lo usa); la ruta lee solo por estas funciones.

Downgrade: quita las dos funciones. No hay datos nuevos que proteger.

Revisión: 0029
Anterior: 0028
"""

from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None

UPGRADE = r"""
create function nucleo.lee_coeficientes(c uuid, quien uuid)
  returns boolean
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select exists (
      select 1 from nucleo.mandato m
        join nucleo.facultad_otorgada f
          on f.consorcio_id = m.consorcio_id and f.mandato_id = m.id
       where m.consorcio_id = c and m.persona_id = quien
         and f.facultad in ('padron.ver', 'coeficientes.proponer')
         and nucleo.mandato_vigente(m.id, nucleo.hoy_del_consorcio(c)))
  $$;
revoke all on function nucleo.lee_coeficientes(uuid, uuid) from public;

create function nucleo.coeficientes_del_consorcio()
  returns table (esquema_id uuid, grupo text, version integer, vigente_desde date,
                 vigente_hasta date, unidad_id uuid, designacion text, porcentaje text)
  language plpgsql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    quien uuid := plataforma.persona_actual();
  begin
    if c is null or quien is null then
      raise exception 'sin ámbito para leer los coeficientes' using errcode = '42501';
    end if;
    if not nucleo.lee_coeficientes(c, quien) then
      raise exception 'sin padron.ver ni coeficientes.proponer en este consorcio'
        using errcode = '42501';
    end if;
    -- La vigencia se calcula sobre los esquemas, antes de unir los coeficientes.
    return query
      with e as (
        select x.id, x.consorcio_id, x.grupo, x.version, x.vigente_desde,
               lead(x.vigente_desde) over (partition by x.grupo order by x.vigente_desde)
                 as vigente_hasta
          from nucleo.esquema_coeficientes x
         where x.consorcio_id = c)
      select e.id, e.grupo, e.version, e.vigente_desde, e.vigente_hasta,
             cj.unidad_id, u.designacion, cj.porcentaje::text
        from e
        left join nucleo.coeficiente_juridico cj
          on cj.consorcio_id = e.consorcio_id and cj.esquema_id = e.id
        left join nucleo.unidad u on u.consorcio_id = cj.consorcio_id and u.id = cj.unidad_id
       order by e.grupo, e.version, u.designacion, cj.unidad_id;
  end
  $$;

create function nucleo.reglas_de_reparto_del_consorcio()
  returns table (esquema_id uuid, version integer, metodo text, centavos text)
  language plpgsql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    quien uuid := plataforma.persona_actual();
  begin
    if c is null or quien is null then
      raise exception 'sin ámbito para leer las reglas de reparto' using errcode = '42501';
    end if;
    if not nucleo.lee_coeficientes(c, quien) then
      raise exception 'sin padron.ver ni coeficientes.proponer en este consorcio'
        using errcode = '42501';
    end if;
    return query
      select r.esquema_id, r.version, r.metodo::text, r.centavos::text
        from nucleo.regla_reparto r
       where r.consorcio_id = c
       order by r.esquema_id, r.version;
  end
  $$;

revoke all on function
  nucleo.coeficientes_del_consorcio(),
  nucleo.reglas_de_reparto_del_consorcio()
  from public;
grant execute on function
  nucleo.coeficientes_del_consorcio(),
  nucleo.reglas_de_reparto_del_consorcio()
  to cem_app;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(
        "drop function nucleo.reglas_de_reparto_del_consorcio();"
        " drop function nucleo.coeficientes_del_consorcio();"
        " drop function nucleo.lee_coeficientes(uuid, uuid);"
    )
