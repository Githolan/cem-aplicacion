"""2c-2: soporte borra un documento o cambia su categoría, con su rol propio (RF24; H2c §1.1, §4).

Diseño: `doc/diseno/H2c_Diseno.md` del repositorio CEM, §1.1 (soporte fuera de la auditoría
funcional, decisión del propietario del 26-10-2026) y §4 (borrado y categoría solo por soporte).

- **`cem_soporte_documentos`:** el rol lo crea un superusuario (`scripts/crear_roles.sql`, que
  incluye `crear_rol_soporte_documentos.sql`), porque `cem_migrate` no tiene `CREATEROLE`. Esta
  migración solo da los permisos: `SELECT` sobre documentos y versiones, y `UPDATE` solo de las
  columnas del borrado y de la categoría. Sin `BYPASSRLS`: las políticas `por_consorcio` (que ya
  filtran la lectura) y la nueva `soporte_actualiza` le dejan ver y cambiar solo las filas del
  consorcio fijado en el contexto. Sin `DELETE` y sin tocar las versiones. Ninguna política
  nombra al rol: la copia diaria se verifica restaurando sin roles (`pg_restore --no-acl`).
- **La constancia queda en la fila del documento**, no en la auditoría funcional (§1.1): el motivo,
  la referencia del respaldo, la fecha, quién lo pidió y el mandato con el que tenía la capacidad.
  Del cambio de categoría, también la categoría anterior (la del último cambio).
- **`nucleo.capacidad_del_solicitante(consorcio, persona, facultades, fecha)`:** el mandato vigente
  ese día con todas las facultades pedidas, cada una general (sin recurso), o null. Exige que el
  consorcio sea el del contexto y que el día no sea posterior a hoy en el consorcio; solo la
  ejecuta este rol y no devuelve otros datos.
- **Un documento borrado no vuelve:** un disparador niega cualquier cambio a un documento
  `borrado`. El borrado es lógico: sus versiones y binarios se conservan.

Downgrade: se niega si hay algún documento borrado o con un cambio de categoría (es constancia);
si no, quita las columnas, la política, la función, el disparador y los permisos (el rol queda:
lo borra un superusuario).

Revisión: 0037
Anterior: 0036
"""

from alembic import op

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None

ROL = "cem_soporte_documentos"
COLUMNAS_SOPORTE = (
    "estado", "borrado_en", "borrado_motivo", "borrado_referencia", "borrado_solicitado_por",
    "borrado_mandato", "categoria", "categoria_anterior", "categoria_cambiada_en",
    "categoria_motivo", "categoria_referencia", "categoria_solicitada_por", "categoria_mandato",
)  # fmt: skip

UPGRADE = f"""
alter table nucleo.documento
  add column borrado_solicitado_por uuid references nucleo.persona (id),
  add column borrado_mandato uuid,
  add column categoria_anterior text,
  add column categoria_cambiada_en timestamptz,
  add column categoria_motivo text check (length(btrim(categoria_motivo)) between 1 and 300),
  add column categoria_referencia text
    check (length(btrim(categoria_referencia)) between 1 and 300),
  add column categoria_solicitada_por uuid references nucleo.persona (id),
  add column categoria_mandato uuid,
  add foreign key (consorcio_id, borrado_mandato) references nucleo.mandato (consorcio_id, id),
  add foreign key (consorcio_id, categoria_mandato) references nucleo.mandato (consorcio_id, id),
  add check (borrado_en is null
             or (borrado_solicitado_por is not null and borrado_mandato is not null)),
  add check (categoria_anterior is null or categoria_anterior <> categoria),
  add check ((categoria_cambiada_en is null) = (categoria_anterior is null)
             and (categoria_cambiada_en is null) = (categoria_motivo is null)
             and (categoria_cambiada_en is null) = (categoria_referencia is null)
             and (categoria_cambiada_en is null) = (categoria_solicitada_por is null)
             and (categoria_cambiada_en is null) = (categoria_mandato is null));

-- Un documento borrado no se cambia más: ni vuelve a vigente ni cambia de categoría.
create function nucleo.documento_borrado_no_cambia()
  returns trigger
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  begin
    if old.estado = 'borrado' then
      raise exception 'el documento está borrado: no se cambia'
        using errcode = 'P0001', hint = 'documento_borrado';
    end if;
    return new;
  end
  $$;
create trigger documento_borrado_no_cambia before update on nucleo.documento
  for each row execute function nucleo.documento_borrado_no_cambia();

create function nucleo.capacidad_del_solicitante(c uuid, persona uuid, facultades text[],
                                                 fecha date)
  returns uuid
  language plpgsql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    m uuid;
  begin
    if c is null or c is distinct from plataforma.consorcio_actual() then
      raise exception 'el consorcio no es el del contexto' using errcode = '42501';
    end if;
    if persona is null or fecha is null or coalesce(cardinality(facultades), 0) = 0 then
      raise exception 'faltan la persona, la fecha o las facultades'
        using errcode = 'P0001', hint = 'pedido_incompleto';
    end if;
    if fecha > nucleo.hoy_del_consorcio(c) then
      raise exception 'la fecha del pedido es posterior a hoy en el consorcio'
        using errcode = 'P0001', hint = 'fecha_futura';
    end if;
    select x.id into m from nucleo.mandato x
     where x.consorcio_id = c and x.persona_id = persona and nucleo.mandato_vigente(x.id, fecha)
       and (select count(distinct f.facultad) from nucleo.facultad_otorgada f
             where f.consorcio_id = c and f.mandato_id = x.id and f.recurso is null
               and f.facultad = any(facultades)) = cardinality(array(
                 select distinct unnest(facultades)))
     order by x.id
     limit 1;
    return m;
  end
  $$;
revoke all on function nucleo.capacidad_del_solicitante(uuid, uuid, text[], date) from public;
grant execute on function nucleo.capacidad_del_solicitante(uuid, uuid, text[], date) to {ROL};

grant usage on schema nucleo, plataforma to {ROL};
grant execute on function plataforma.consorcio_actual() to {ROL};
grant select on nucleo.documento, nucleo.version_documento to {ROL};
grant update ({", ".join(COLUMNAS_SOPORTE)}) on nucleo.documento to {ROL};
-- Sin `to {ROL}`: como `por_consorcio`, vale para todos los roles, y solo este tiene `UPDATE`
-- sobre la tabla (`cem_migrate` tiene BYPASSRLS). Una política que nombra un rol rompe la
-- restauración de la copia diaria en el PostgreSQL efímero (`pg_restore --no-acl` no la omite y
-- el rol no existe ahí: `ci.ps1` sobre ed56553).
create policy soporte_actualiza on nucleo.documento for update
  using (consorcio_id = plataforma.consorcio_actual())
  with check (consorcio_id = plataforma.consorcio_actual());
"""

DOWNGRADE = f"""
do $guarda$
begin
  if exists (select 1 from nucleo.documento
              where estado = 'borrado' or categoria_cambiada_en is not null) then
    raise exception 'el downgrade de 0037 se niega: hay documentos borrados o recategorizados'
      using errcode = 'P0001', hint = 'downgrade_con_datos',
            detail = 'las columnas de soporte son la constancia del borrado y del cambio';
  end if;
end
$guarda$;
drop policy soporte_actualiza on nucleo.documento;
revoke update on nucleo.documento from {ROL};
revoke select on nucleo.documento, nucleo.version_documento from {ROL};
revoke execute on function plataforma.consorcio_actual() from {ROL};
revoke usage on schema nucleo, plataforma from {ROL};
drop function nucleo.capacidad_del_solicitante(uuid, uuid, text[], date);
drop trigger documento_borrado_no_cambia on nucleo.documento;
drop function nucleo.documento_borrado_no_cambia();
alter table nucleo.documento
  drop column categoria_mandato,
  drop column categoria_solicitada_por,
  drop column categoria_referencia,
  drop column categoria_motivo,
  drop column categoria_cambiada_en,
  drop column categoria_anterior,
  drop column borrado_mandato,
  drop column borrado_solicitado_por;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
