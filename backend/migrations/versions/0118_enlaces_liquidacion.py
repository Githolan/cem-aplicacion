"""Los enlaces de la liquidación del administrador y el archivo traído de uno, H5-5 (diseño H5 §5).

La Disposición 1146/24 de la Ciudad pide que la liquidación lleve enlaces (y su QR) a los
comprobantes del período. `cem-trabajos` los anota al leerla, **sin seguirlos**
(`app/liquidacion/enlaces.py`); quien importa puede pedir que la plataforma traiga el archivo de
uno (`app/liquidacion/descarga.py`, con la descarga acotada en destino, tamaño y tipo), que entra
como un documento nuevo de la misma categoría y pasa por el antivirus como cualquier carga.

- **`enlace_liquidacion`**: por importación, cada enlace en orden, con su origen (`anotacion`,
  `texto` o `qr`) y su página. Nada se cambia ni se borra.
- **`guardar_enlaces_liquidacion`** (`cem-trabajos`): los anota mientras la importación se lee;
  una lectura repetida no los duplica.
- **`enlaces_liquidacion_lectura`** (`liquidacion.importar`): los de una importación.
- **`enlace_para_traer`** (`liquidacion.importar`): la dirección de un enlace, para traerla.
- **`registrar_enlace_traido`** (`liquidacion.importar`): deja en la auditoría qué enlace se trajo
  y a qué documento fue (`consorcio.liquidacion.traer_enlace`).

Downgrade: se niega (`downgrade_con_datos`) si hay enlaces anotados.

Datos de prueba: SINTETICOS / NO REAL.

Revisión: 0118
Anterior: 0117
"""

from alembic import op

revision = "0118"
down_revision = "0117"
branch_labels = None
depends_on = None

ACCION = "consorcio.liquidacion.traer_enlace"
MAXIMO_ENLACES = 20
"""El de `enlaces.MAXIMO_ENLACES`."""
LARGO_URL = 2000
"""El de `descarga.LARGO_MAXIMO_URL`."""

TABLAS = f"""
create table nucleo.enlace_liquidacion (
  consorcio_id    uuid not null references nucleo.consorcio (id),
  importacion_id  uuid not null,
  orden           integer not null check (orden between 1 and {MAXIMO_ENLACES}),
  url             text not null check (length(url) between 1 and {LARGO_URL}
                                       and url ~* '^https?://'),
  origen          text not null check (origen in ('anotacion', 'texto', 'qr')),
  pagina          integer not null check (pagina >= 1),
  primary key (consorcio_id, importacion_id, orden),
  foreign key (consorcio_id, importacion_id)
    references nucleo.importacion_liquidacion (consorcio_id, id)
);
alter table nucleo.enlace_liquidacion enable row level security;
alter table nucleo.enlace_liquidacion force row level security;
create policy por_consorcio on nucleo.enlace_liquidacion for select
  using (consorcio_id = plataforma.consorcio_actual());
revoke all on nucleo.enlace_liquidacion from public, cem_app;

create function nucleo.negar_cambio_enlace_liquidacion()
  returns trigger
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  begin
    raise exception 'un enlace leído no cambia ni se borra' using errcode = 'P0001',
      hint = 'historia_inmutable';
  end
  $$;
revoke all on function nucleo.negar_cambio_enlace_liquidacion() from public;
create trigger historia_enlace_liquidacion before update or delete
  on nucleo.enlace_liquidacion
  for each row execute function nucleo.negar_cambio_enlace_liquidacion();
"""

FUNCIONES = f"""
-- Para `cem-trabajos`: los enlaces de la importación que se está leyendo, en orden.
create function nucleo.guardar_enlaces_liquidacion(importacion uuid, enlaces jsonb)
  returns integer
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    actual text;
    n integer;
  begin
    perform nucleo.exigir_trabajo(c);
    select x.estado into actual from nucleo.importacion_liquidacion x
     where x.consorcio_id = c and x.id = importacion for update;
    if actual is null then
      raise exception 'importación inexistente' using errcode = 'P0001', hint = 'no_encontrado';
    end if;
    if actual <> 'leyendo' or exists (select 1 from nucleo.enlace_liquidacion e
                                       where e.consorcio_id = c
                                         and e.importacion_id = importacion) then
      return 0;
    end if;
    insert into nucleo.enlace_liquidacion (consorcio_id, importacion_id, orden, url, origen, pagina)
    select c, importacion, x.ord::integer, x.e->>'url', x.e->>'origen', (x.e->>'pagina')::integer
      from jsonb_array_elements(enlaces) with ordinality as x (e, ord);
    get diagnostics n = row_count;
    return n;
  end
  $$;

create function nucleo.enlaces_liquidacion_lectura(importacion uuid, actor uuid,
                                                   origen_pedido text, sesion uuid)
  returns table (orden integer, url text, origen text, pagina integer)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
  begin
    perform nucleo.exigir_acto_importacion(c, actor, sesion, origen_pedido);
    return query
      select e.orden, e.url, e.origen, e.pagina from nucleo.enlace_liquidacion e
       where e.consorcio_id = c and e.importacion_id = importacion
       order by e.orden;
  end
  $$;

create function nucleo.enlace_para_traer(importacion uuid, numero integer, actor uuid,
                                         origen_pedido text, sesion uuid)
  returns text
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    direccion text;
  begin
    perform nucleo.exigir_acto_importacion(c, actor, sesion, origen_pedido);
    select e.url into direccion from nucleo.enlace_liquidacion e
     where e.consorcio_id = c and e.importacion_id = importacion and e.orden = numero;
    if direccion is null then
      raise exception 'ese enlace no existe' using errcode = 'P0001', hint = 'no_encontrado';
    end if;
    return direccion;
  end
  $$;

create function nucleo.registrar_enlace_traido(importacion uuid, numero integer, documento uuid,
                                               version uuid, actor uuid, origen_pedido text,
                                               sesion uuid)
  returns void
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    direccion text;
  begin
    direccion := nucleo.enlace_para_traer(importacion, numero, actor, origen_pedido, sesion);
    insert into auditoria.eventos
      (consorcio_id, actor_persona_id, origen, accion, entidad, entidad_id, despues,
       hash_previo, hash)
    values (c, actor, origen_pedido, '{ACCION}', 'importacion_liquidacion', importacion,
            jsonb_build_object('enlace', numero, 'url', direccion, 'documento', documento,
                               'version', version), '', '');
  end
  $$;
"""

RUTAS = (
    "nucleo.guardar_enlaces_liquidacion(uuid, jsonb)",
    "nucleo.enlaces_liquidacion_lectura(uuid, uuid, text, uuid)",
    "nucleo.enlace_para_traer(uuid, integer, uuid, text, uuid)",
    "nucleo.registrar_enlace_traido(uuid, integer, uuid, uuid, uuid, text, uuid)",
)

GUARDA = """
do $guarda$
begin
  if exists (select 1 from nucleo.enlace_liquidacion) then
    raise exception '0118 no baja: hay enlaces de liquidaciones anotados'
      using errcode = 'P0001', hint = 'downgrade_con_datos';
  end if;
end
$guarda$;
"""


def upgrade() -> None:
    op.execute(TABLAS)
    op.execute(FUNCIONES)
    for funcion in RUTAS:
        op.execute(f"revoke all on function {funcion} from public")
        op.execute(f"grant execute on function {funcion} to cem_app")
    op.execute(
        "insert into auditoria.proyeccion (accion, texto, recurso, campos) values"
        f" ('{ACCION}', 'Trajo el archivo de un enlace de la liquidación del administrador',"
        " 'Liquidación importada', array['enlace', 'url', 'documento'])"
    )


def downgrade() -> None:
    op.execute(GUARDA)
    op.execute(f"delete from auditoria.proyeccion where accion = '{ACCION}'")
    for funcion in reversed(RUTAS):
        op.execute(f"drop function {funcion}")
    op.execute("drop table nucleo.enlace_liquidacion")
    op.execute("drop function nucleo.negar_cambio_enlace_liquidacion()")
