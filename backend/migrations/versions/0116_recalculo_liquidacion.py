"""Recálculo de la liquidación importada con los coeficientes del edificio, H5-4 (diseño H5 §5).

La liquidación del administrador reparte el gasto por **grupo** (las columnas del formato Mis
Expensas: «Gastos Comunes», «AySA», «Cocheras»…), con el porcentaje y el monto de cada unidad. El
edificio tiene sus coeficientes por **esquema** (`esquema_coeficientes.grupo`: `expensas_comunes`,
`agua`, `cocheras`…, RF05). Nada une los dos nombres, así que quien importa dice una vez qué
esquema corresponde a cada grupo y el edificio lo recuerda para los meses siguientes.

- **`correspondencia_grupo_liquidacion`**: por edificio y por grupo (comparado como las
  designaciones, `clave_designacion`: espacios juntados y sin mayúsculas), el esquema que le
  corresponde. Cambiarla agrega una versión; nada se borra ni se modifica.
- **`definir_correspondencia_grupo`** (`liquidacion.importar`, D2-A): el esquema tiene que existir
  en el edificio. Repetir la que ya está no agrega nada.
- **`correspondencias_grupo_lectura`**: la vigente de cada grupo.
- **`grupos_para_recalculo`**: los esquemas vigentes al último día del período de la importación
  (como `grupos_del_periodo_lectura` de `0110`), para elegir.
- **`base_recalculo_liquidacion`**: por cada grupo con correspondencia, el esquema vigente en el
  período y el coeficiente de cada unidad activa (sin coeficiente: vacío, y el recálculo no se
  hace). El reparto lo hace la API con el mismo motor que la liquidación propia (`reparto.py`,
  restos mayores), sobre los datos oficiales de la importación (RN30,
  `campos_oficiales_liquidacion`).

Datos de prueba: SINTETICOS / NO REAL.

Revision ID: 0116
Anterior: 0115
"""

from alembic import op

revision = "0116"
down_revision = "0115"
branch_labels = None
depends_on = None

ACCION = "consorcio.liquidacion.corresponder_grupo"
LARGO_NOMBRE = 80

TABLAS = f"""
create table nucleo.correspondencia_grupo_liquidacion (
  consorcio_id  uuid not null references nucleo.consorcio (id),
  id            uuid not null default plataforma.uuid_v7(),
  clave         text not null check (length(clave) between 1 and {LARGO_NOMBRE}),
  nombre        text not null check (length(btrim(nombre)) between 1 and {LARGO_NOMBRE}),
  grupo         text not null check (grupo ~ '^[a-z][a-z0-9_]{{1,39}}$'),
  version       integer not null check (version >= 1),
  definida_por  uuid not null references nucleo.persona (id),
  definida_en   timestamptz not null default clock_timestamp(),
  primary key (id),
  unique (consorcio_id, clave, version)
);
alter table nucleo.correspondencia_grupo_liquidacion enable row level security;
alter table nucleo.correspondencia_grupo_liquidacion force row level security;
create policy por_consorcio on nucleo.correspondencia_grupo_liquidacion for select
  using (consorcio_id = plataforma.consorcio_actual());
revoke all on nucleo.correspondencia_grupo_liquidacion from public, cem_app;

create function nucleo.negar_cambio_correspondencia_grupo()
  returns trigger
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  begin
    raise exception 'una correspondencia no cambia: se agrega otra versión' using errcode = 'P0001',
      hint = 'historia_inmutable';
  end
  $$;
revoke all on function nucleo.negar_cambio_correspondencia_grupo() from public;
create trigger historia_correspondencia_grupo before update or delete
  on nucleo.correspondencia_grupo_liquidacion
  for each row execute function nucleo.negar_cambio_correspondencia_grupo();
create trigger historia_correspondencia_grupo_truncate before truncate
  on nucleo.correspondencia_grupo_liquidacion
  for each statement execute function nucleo.negar_cambio_correspondencia_grupo();
"""

FUNCIONES = f"""
-- La correspondencia vigente de cada grupo del edificio (la última versión).
create function nucleo.correspondencias_grupo_lectura(actor uuid, origen_pedido text, sesion uuid)
  returns table (clave text, nombre text, grupo text, version integer, definida_por uuid,
                 definida_en timestamptz)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
  begin
    perform nucleo.exigir_acto_importacion(c, actor, sesion, origen_pedido);
    return query
      select distinct on (x.clave) x.clave, x.nombre, x.grupo, x.version, x.definida_por,
             x.definida_en
        from nucleo.correspondencia_grupo_liquidacion x
       where x.consorcio_id = c
       order by x.clave, x.version desc;
  end
  $$;

-- Qué esquema de coeficientes corresponde a un grupo de la liquidación. Otra versión si cambia.
create function nucleo.definir_correspondencia_grupo(nombre_grupo text, grupo_esquema text,
                                                     actor uuid, origen_pedido text,
                                                     sesion uuid)
  returns void
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    k text;
    anterior nucleo.correspondencia_grupo_liquidacion;
    nueva uuid;
  begin
    perform nucleo.exigir_acto_importacion(c, actor, sesion, origen_pedido);
    if nombre_grupo is null or length(btrim(nombre_grupo)) not between 1 and {LARGO_NOMBRE} then
      raise exception 'nombre de grupo inválido' using errcode = 'P0001', hint = 'nombre_invalido';
    end if;
    if not exists (select 1 from nucleo.esquema_coeficientes e
                    where e.consorcio_id = c and e.grupo = grupo_esquema) then
      raise exception 'el edificio no tiene ese esquema de coeficientes' using errcode = 'P0001',
        hint = 'grupo_inexistente';
    end if;
    k := nucleo.clave_designacion(nombre_grupo);
    -- Dos pedidos a la vez sobre el mismo grupo se ordenan: la versión siguiente es una sola.
    perform pg_advisory_xact_lock(hashtextextended('correspondencia_grupo:' || c::text || ':' || k,
                                                   0));
    select x.* into anterior from nucleo.correspondencia_grupo_liquidacion x
     where x.consorcio_id = c and x.clave = k
     order by x.version desc limit 1;
    if anterior.id is not null and anterior.grupo = grupo_esquema then
      return;
    end if;
    insert into nucleo.correspondencia_grupo_liquidacion
      (consorcio_id, clave, nombre, grupo, version, definida_por)
    values (c, k, btrim(nombre_grupo), grupo_esquema, coalesce(anterior.version, 0) + 1, actor)
    returning id into nueva;
    insert into auditoria.eventos
      (consorcio_id, actor_persona_id, origen, accion, entidad, entidad_id, antes, despues,
       hash_previo, hash)
    values (c, actor, origen_pedido, '{ACCION}', 'correspondencia_grupo_liquidacion', nueva,
            case when anterior.id is null then null
                 else jsonb_build_object('grupo', anterior.grupo) end,
            jsonb_build_object('nombre', btrim(nombre_grupo), 'grupo', grupo_esquema), '', '');
  end
  $$;

-- El último día del período de una importación del consorcio; sin período, el motivo.
create function nucleo.fin_periodo_importacion(c uuid, importacion uuid)
  returns date
  language plpgsql stable
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    p date;
  begin
    select x.periodo into p from nucleo.importacion_liquidacion x
     where x.consorcio_id = c and x.id = importacion;
    if not found then
      raise exception 'importación inexistente' using errcode = 'P0001', hint = 'no_encontrado';
    end if;
    return (p + interval '1 month' - interval '1 day')::date;
  end
  $$;

-- Los esquemas vigentes al último día del período de la importación, para elegir.
create function nucleo.grupos_para_recalculo(importacion uuid, actor uuid, origen_pedido text,
                                             sesion uuid)
  returns table (grupo text, esquema_version integer, vigente_desde date)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    fin date;
  begin
    perform nucleo.exigir_acto_importacion(c, actor, sesion, origen_pedido);
    fin := nucleo.fin_periodo_importacion(c, importacion);
    return query
      select distinct on (e.grupo) e.grupo, e.version, e.vigente_desde
        from nucleo.esquema_coeficientes e
       where e.consorcio_id = c and e.vigente_desde <= fin
       order by e.grupo, e.vigente_desde desc;
  end
  $$;

-- Por cada grupo con correspondencia: el esquema vigente en el período (o nada) y el coeficiente
-- de cada unidad activa (nulo si le falta).
create function nucleo.base_recalculo_liquidacion(importacion uuid, actor uuid,
                                                  origen_pedido text, sesion uuid)
  returns table (clave text, grupo text, esquema_id uuid, esquema_version integer,
                 vigente_desde date, unidad_id uuid, designacion text, porcentaje numeric)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    fin date;
  begin
    perform nucleo.exigir_acto_importacion(c, actor, sesion, origen_pedido);
    fin := nucleo.fin_periodo_importacion(c, importacion);
    return query
      with correspondencia as (
        select distinct on (x.clave) x.clave, x.grupo
          from nucleo.correspondencia_grupo_liquidacion x
         where x.consorcio_id = c
         order by x.clave, x.version desc
      ), esquema as (
        select distinct on (e.grupo) e.id, e.grupo, e.version, e.vigente_desde
          from nucleo.esquema_coeficientes e
         where e.consorcio_id = c and e.vigente_desde <= fin
         order by e.grupo, e.vigente_desde desc
      )
      select k.clave, k.grupo, s.id, s.version, s.vigente_desde, u.id, u.designacion,
             cj.porcentaje
        from correspondencia k
        left join esquema s on s.grupo = k.grupo
        left join nucleo.unidad u
          on s.id is not null and u.consorcio_id = c and u.eliminado_en is null
        left join nucleo.coeficiente_juridico cj
          on cj.esquema_id = s.id and cj.unidad_id = u.id
       order by k.clave, u.designacion;
  end
  $$;
"""

RUTAS = (
    "nucleo.correspondencias_grupo_lectura(uuid, text, uuid)",
    "nucleo.definir_correspondencia_grupo(text, text, uuid, text, uuid)",
    "nucleo.grupos_para_recalculo(uuid, uuid, text, uuid)",
    "nucleo.base_recalculo_liquidacion(uuid, uuid, text, uuid)",
)
INTERNAS = ("nucleo.fin_periodo_importacion(uuid, uuid)",)

GUARDA = """
do $guarda$
begin
  if exists (select 1 from nucleo.correspondencia_grupo_liquidacion) then
    raise exception '0116 no baja: hay correspondencias de grupos guardadas'
      using errcode = 'P0001', hint = 'downgrade_con_datos';
  end if;
end
$guarda$;
"""


def upgrade() -> None:
    op.execute(TABLAS)
    op.execute(FUNCIONES)
    for funcion in INTERNAS + RUTAS:
        op.execute(f"revoke all on function {funcion} from public")
    for funcion in RUTAS:
        op.execute(f"grant execute on function {funcion} to cem_app")
    op.execute(
        "insert into auditoria.proyeccion (accion, texto, recurso, campos) values"
        f" ('{ACCION}', 'Indicó qué coeficientes corresponden a un grupo de la liquidación',"
        " 'Liquidación importada', array['nombre', 'grupo'])"
    )


def downgrade() -> None:
    op.execute(GUARDA)
    op.execute(f"delete from auditoria.proyeccion where accion = '{ACCION}'")
    for funcion in reversed(RUTAS + INTERNAS):
        op.execute(f"drop function {funcion}")
    op.execute("drop table nucleo.correspondencia_grupo_liquidacion")
    op.execute("drop function nucleo.negar_cambio_correspondencia_grupo()")
