"""RF05: el coeficiente de la propia unidad, para quien tiene el vínculo confirmado.

Decisión del propietario del 25-10-2026 (`doc/diseno/Decisiones_Propietario_2026-10-25.md` del
repositorio CEM, §1.8): una persona con vínculo **confirmado** (P4) ve el coeficiente de su propia
unidad sin `padron.ver`, y nunca el de una unidad ajena por esa vía.

Las dos lecturas de `0029` cambian así:

- **Quien tiene un mandato vigente con `padron.ver` o `coeficientes.proponer`** lee como en
  `0029`: todos los esquemas, y la aplicación filtra fila por fila.
- **Si no, quien tiene vínculos vigentes y confirmados** lee solo los coeficientes de esas
  unidades, los esquemas que los contienen y sus reglas. La base ya no le devuelve nada más: es
  la segunda barrera de `coeficientes.ver_propia_unidad`. El importado pendiente no cuenta (0024).
- Sin ninguna de las dos cosas, la base sigue negando (`42501`).

Downgrade: vuelve a las lecturas de `0029` y quita la función auxiliar. No hay datos nuevos.

Revisión: 0030
Anterior: 0029
"""

from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None

AUXILIAR = r"""
create function nucleo.unidades_propias_confirmadas(c uuid, quien uuid)
  returns uuid[]
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select coalesce(array_agg(distinct v.unidad_id), '{}')
      from nucleo.vinculo v
     where v.consorcio_id = c and v.persona_id = quien
       and v.confirmado_en is not null
       and daterange(v.vigente_desde, v.vigente_hasta) @> nucleo.hoy_del_consorcio(c)
       and v.id not in (select nucleo.vinculos_importados_pendientes())
  $$;
revoke all on function nucleo.unidades_propias_confirmadas(uuid, uuid) from public;
"""

# `amplia`: la lectura de `0029`. `propias`: solo las unidades del vínculo confirmado.
LECTURAS = r"""
create or replace function nucleo.coeficientes_del_consorcio()
  returns table (esquema_id uuid, grupo text, version integer, vigente_desde date,
                 vigente_hasta date, unidad_id uuid, designacion text, porcentaje text)
  language plpgsql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    quien uuid := plataforma.persona_actual();
    amplia boolean;
    propias uuid[];
  begin
    if c is null or quien is null then
      raise exception 'sin ámbito para leer los coeficientes' using errcode = '42501';
    end if;
    amplia := nucleo.lee_coeficientes(c, quien);
    propias := case when amplia then '{}' else nucleo.unidades_propias_confirmadas(c, quien) end;
    if not amplia and cardinality(propias) = 0 then
      raise exception 'sin padron.ver ni coeficientes.proponer ni un vínculo confirmado'
        ' en este consorcio' using errcode = '42501';
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
       where amplia or cj.unidad_id = any (propias)
       order by e.grupo, e.version, u.designacion, cj.unidad_id;
  end
  $$;

create or replace function nucleo.reglas_de_reparto_del_consorcio()
  returns table (esquema_id uuid, version integer, metodo text, centavos text)
  language plpgsql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    quien uuid := plataforma.persona_actual();
    amplia boolean;
    propias uuid[];
  begin
    if c is null or quien is null then
      raise exception 'sin ámbito para leer las reglas de reparto' using errcode = '42501';
    end if;
    amplia := nucleo.lee_coeficientes(c, quien);
    propias := case when amplia then '{}' else nucleo.unidades_propias_confirmadas(c, quien) end;
    if not amplia and cardinality(propias) = 0 then
      raise exception 'sin padron.ver ni coeficientes.proponer ni un vínculo confirmado'
        ' en este consorcio' using errcode = '42501';
    end if;
    return query
      select r.esquema_id, r.version, r.metodo::text, r.centavos::text
        from nucleo.regla_reparto r
       where r.consorcio_id = c
         and (amplia or exists (
           select 1 from nucleo.coeficiente_juridico cj
            where cj.consorcio_id = c and cj.esquema_id = r.esquema_id
              and cj.unidad_id = any (propias)))
       order by r.esquema_id, r.version;
  end
  $$;
"""

LECTURAS_0029 = r"""
create or replace function nucleo.coeficientes_del_consorcio()
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

create or replace function nucleo.reglas_de_reparto_del_consorcio()
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
"""


def upgrade() -> None:
    op.execute(AUXILIAR + LECTURAS)


def downgrade() -> None:
    op.execute(LECTURAS_0029 + "drop function nucleo.unidades_propias_confirmadas(uuid, uuid);")
