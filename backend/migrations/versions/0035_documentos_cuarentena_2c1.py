"""2c-1: documentos y versiones, carga a cuarentena y resultado del análisis (RF24, primera parte).

Diseño: `doc/diseno/H2c_Diseno.md` del repositorio CEM, §2 y §3, con las decisiones del
propietario del 25-10-2026 (`Decisiones_Propietario_2026-10-25.md`, §1.3).

- **`nucleo.documento`:** categoría (las 8 de §4; quién ve cada una llega en 2c-2), título sin
  teléfonos ni correos (`nucleo.sin_contacto_libre`, la regla de 2b-7), fecha, estado y quién lo
  cargó. El borrado lógico (por soporte, 2c-2) deja sus columnas listas y vacías.
- **`nucleo.version_documento`:** una fila por archivo subido. Solo cambia su estado:
  `en_cuarentena` → `disponible` o `rechazado`. La clave del objeto es
  `cem/{consorcio}/{documento}/{version}` y nunca se sobrescribe.
- **Carga (API):** `crear_documento` y `nueva_version` exigen `documentos.cargar` vigente de la
  persona de la sesión y auditan. En este corte toda categoría se carga con `documentos.cargar`;
  `documentos.cargar_operativos` llega en 2c-2 con su política (§4).
- **Análisis (`cem-trabajos`):** `version_para_analizar`, `resolver_version`,
  `versiones_demoradas` y `claves_registradas` solo corren sin persona en la sesión, que es como
  corre un trabajo; un pedido de la API siempre la tiene. El rechazo queda en la auditoría con
  origen `trabajos` (§3, paso 5).
- RLS forzada en las dos tablas; `cem_app` no las lee ni las escribe directo. Las funciones son
  `security definer` de `cem_migrate`, que tiene `BYPASSRLS`: cada una filtra por el consorcio
  del contexto.

Downgrade: se niega si hay algún documento (`downgrade_con_datos`); si no, quita todo.

Revisión: 0035
Anterior: 0034
"""

from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None

FACULTAD = "documentos.cargar"
CATEGORIAS = (
    "institucional",
    "actas_y_decisiones",
    "seguros",
    "contratos_proveedores",
    "facturas_y_comprobantes",
    "presupuestos",
    "mantenimiento_y_certificados",
    "otros",
)
TIPOS = ("application/pdf", "image/jpeg", "image/png")
TOPE_BYTES = 20 * 1024 * 1024
MOTIVOS_RECHAZO = ("virus", "contenido_activo", "pdf_cifrado", "pdf_mal_formado", "no_coincide")
PROYECCION = (
    ("consorcio.documento.crear", "Cargó un documento", "array['categoria', 'fecha']"),
    ("consorcio.documento.cargar_version", "Subió un archivo de un documento",
     "array['tipo', 'tamano']"),
    ("consorcio.documento.rechazar_version", "Un archivo de un documento fue rechazado",
     "array['motivo']"),
)  # fmt: skip


def _lista(valores: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in valores)


def _upgrade() -> str:
    proyeccion = ",\n  ".join(f"('{a}', '{t}', 'Documento', {c})" for a, t, c in PROYECCION)
    return f"""
insert into auditoria.proyeccion (accion, texto, recurso, campos) values
  {proyeccion};

create table nucleo.documento (
  consorcio_id        uuid not null references nucleo.consorcio (id),
  id                  uuid primary key default plataforma.uuid_v7(),
  categoria           text not null check (categoria in ({_lista(CATEGORIAS)})),
  titulo              text not null
    check (length(btrim(titulo)) between 1 and 200 and nucleo.sin_contacto_libre(titulo)),
  fecha               date not null,
  estado              text not null default 'vigente' check (estado in ('vigente', 'borrado')),
  cargado_por         uuid not null references nucleo.persona (id),
  cargado_en          timestamptz not null default now(),
  borrado_en          timestamptz,
  borrado_motivo      text check (length(btrim(borrado_motivo)) between 1 and 300),
  borrado_referencia  text check (length(btrim(borrado_referencia)) between 1 and 300),
  unique (consorcio_id, id),
  check ((estado = 'borrado') = (borrado_en is not null)),
  check (borrado_en is null or (borrado_motivo is not null and borrado_referencia is not null))
);
alter table nucleo.documento enable row level security;
alter table nucleo.documento force row level security;
create policy por_consorcio on nucleo.documento for select
  using (consorcio_id = plataforma.consorcio_actual());
revoke all on nucleo.documento from public, cem_app;

create table nucleo.version_documento (
  consorcio_id    uuid not null,
  documento_id    uuid not null,
  id              uuid primary key default plataforma.uuid_v7(),
  estado          text not null default 'en_cuarentena'
    check (estado in ('en_cuarentena', 'disponible', 'rechazado')),
  motivo_rechazo  text check (motivo_rechazo in ({_lista(MOTIVOS_RECHAZO)})),
  tipo            text not null check (tipo in ({_lista(TIPOS)})),
  tamano          integer not null check (tamano between 1 and {TOPE_BYTES}),
  sha256          text not null check (sha256 ~ '^[0-9a-f]{{64}}$'),
  clave_objeto    text not null unique,
  version_objeto  text check (length(version_objeto) between 1 and 1024),
  antivirus       text check (length(antivirus) between 1 and 200),
  firmas_en       timestamptz,
  subida_por      uuid not null references nucleo.persona (id),
  subida_en       timestamptz not null default now(),
  analizada_en    timestamptz,
  foreign key (consorcio_id, documento_id) references nucleo.documento (consorcio_id, id),
  check ((estado = 'rechazado') = (motivo_rechazo is not null)),
  check ((estado = 'en_cuarentena') = (analizada_en is null)),
  check (estado <> 'disponible' or (antivirus is not null and firmas_en is not null))
);
create index version_documento_por_documento on nucleo.version_documento (documento_id);
alter table nucleo.version_documento enable row level security;
alter table nucleo.version_documento force row level security;
create policy por_consorcio on nucleo.version_documento for select
  using (consorcio_id = plataforma.consorcio_actual());
revoke all on nucleo.version_documento from public, cem_app;

-- Quién carga: solo por la API, la persona de la sesión con la facultad vigente.
create function nucleo.exigir_carga_documentos(c uuid, actor uuid, origen_pedido text)
  returns void
  language plpgsql stable
  set search_path = pg_catalog, pg_temp
  as $$
  begin
    if c is null then
      raise exception 'sin consorcio en el ámbito' using errcode = '42501';
    end if;
    if origen_pedido is distinct from 'api' then
      raise exception 'origen inválido' using errcode = 'P0001', hint = 'origen_invalido';
    end if;
    if actor is null or actor is distinct from plataforma.persona_actual()
         or not nucleo.tiene_facultad_vigente(c, actor, '{FACULTAD}',
                                              nucleo.hoy_del_consorcio(c)) then
      raise exception 'sin {FACULTAD} en este consorcio' using errcode = '42501';
    end if;
  end
  $$;
revoke all on function nucleo.exigir_carga_documentos(uuid, uuid, text) from public;

create function nucleo.auditar_documento(c uuid, accion text, entidad text, entidad_id uuid,
                                         actor uuid, origen_pedido text, despues jsonb)
  returns void
  language sql volatile
  set search_path = pg_catalog, pg_temp
  as $$
    insert into auditoria.eventos
      (consorcio_id, actor_persona_id, origen, accion, entidad, entidad_id, despues,
       hash_previo, hash)
    values (c, actor, origen_pedido, accion, entidad, entidad_id, despues, '', '')
  $$;
revoke all on function nucleo.auditar_documento(uuid, text, text, uuid, uuid, text, jsonb)
  from public;

create function nucleo.crear_documento(categoria_nueva text, titulo_nuevo text, fecha_nueva date,
                                       actor uuid, origen_pedido text)
  returns uuid
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    d nucleo.documento;
  begin
    perform nucleo.exigir_carga_documentos(c, actor, origen_pedido);
    insert into nucleo.documento (consorcio_id, categoria, titulo, fecha, cargado_por)
    values (c, categoria_nueva, btrim(titulo_nuevo), fecha_nueva, actor)
    returning * into d;
    -- El título no va a la auditoría: es texto libre.
    perform nucleo.auditar_documento(c, 'consorcio.documento.crear', 'documento', d.id, actor,
      origen_pedido, jsonb_build_object('categoria', d.categoria, 'fecha', d.fecha));
    return d.id;
  end
  $$;

-- Reserva la versión en cuarentena y devuelve la clave del objeto que el backend sube a
-- continuación, en la misma transacción: si la subida falla, la fila no queda.
create function nucleo.nueva_version(documento uuid, tipo_nuevo text, tamano_nuevo integer,
                                     sha256_nuevo text, actor uuid, origen_pedido text)
  returns table (id uuid, clave_objeto text)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    v uuid := plataforma.uuid_v7();
    clave text;
  begin
    perform nucleo.exigir_carga_documentos(c, actor, origen_pedido);
    perform 1 from nucleo.documento d
     where d.consorcio_id = c and d.id = documento and d.estado = 'vigente';
    if not found then
      raise exception 'el documento no existe' using errcode = 'P0001', hint = 'no_encontrado';
    end if;
    clave := 'cem/' || c || '/' || documento || '/' || v;
    insert into nucleo.version_documento
      (consorcio_id, documento_id, id, tipo, tamano, sha256, clave_objeto, subida_por)
    values (c, documento, v, tipo_nuevo, tamano_nuevo, sha256_nuevo, clave, actor);
    perform nucleo.auditar_documento(c, 'consorcio.documento.cargar_version', 'version_documento',
      v, actor, origen_pedido, jsonb_build_object('tipo', tipo_nuevo, 'tamano', tamano_nuevo));
    return query select v, clave;
  end
  $$;

-- El identificador de versión del almacén, para borrar por `VersionId` un rechazado (§3).
-- Un bucket sin versiones (staging) no devuelve ninguno, y la columna queda vacía.
create function nucleo.fijar_version_objeto(version uuid, version_almacen text, actor uuid,
                                            origen_pedido text)
  returns void
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
  begin
    perform nucleo.exigir_carga_documentos(c, actor, origen_pedido);
    update nucleo.version_documento x set version_objeto = version_almacen
     where x.consorcio_id = c and x.id = version and x.subida_por = actor
       and x.estado = 'en_cuarentena' and x.version_objeto is null;
    if not found then
      raise exception 'la versión no existe' using errcode = 'P0001', hint = 'no_encontrado';
    end if;
  end
  $$;

-- Solo un trabajo: sin persona en la sesión y con el consorcio del trabajo fijado.
create function nucleo.exigir_trabajo(c uuid)
  returns void
  language plpgsql stable
  set search_path = pg_catalog, pg_temp
  as $$
  begin
    if c is null or plataforma.persona_actual() is not null then
      raise exception 'solo un trabajo analiza documentos' using errcode = '42501';
    end if;
  end
  $$;
revoke all on function nucleo.exigir_trabajo(uuid) from public;

create function nucleo.version_para_analizar(version uuid)
  returns table (estado text, tipo text, tamano integer, sha256 text, clave_objeto text,
                 version_objeto text)
  language plpgsql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
  begin
    perform nucleo.exigir_trabajo(c);
    return query
      select x.estado, x.tipo, x.tamano, x.sha256, x.clave_objeto, x.version_objeto
        from nucleo.version_documento x
       where x.consorcio_id = c and x.id = version;
  end
  $$;

-- Pasa una versión en cuarentena a `disponible` (con el antivirus y la fecha de sus firmas) o
-- a `rechazado` (con el motivo, auditado). Devuelve falso si ya no estaba en cuarentena: un
-- trabajo repetido no la vuelve a resolver.
create function nucleo.resolver_version(version uuid, motivo text, antivirus_usado text,
                                        firmas timestamptz)
  returns boolean
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
  begin
    perform nucleo.exigir_trabajo(c);
    update nucleo.version_documento x
       set estado = case when motivo is null then 'disponible' else 'rechazado' end,
           motivo_rechazo = motivo, antivirus = antivirus_usado, firmas_en = firmas,
           analizada_en = now()
     where x.consorcio_id = c and x.id = version and x.estado = 'en_cuarentena';
    if not found then
      return false;
    end if;
    if motivo is not null then
      perform nucleo.auditar_documento(c, 'consorcio.documento.rechazar_version',
        'version_documento', version, null, 'trabajos', jsonb_build_object('motivo', motivo));
    end if;
    return true;
  end
  $$;

-- Para el aviso a Kuma (tarea periódica, sin consorcio): cuántas versiones de todos los
-- consorcios llevan más de `demora` en cuarentena. Solo el número.
create function nucleo.versiones_demoradas(demora interval)
  returns integer
  language plpgsql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    n integer;
  begin
    if plataforma.persona_actual() is not null then
      raise exception 'solo un trabajo vigila la cuarentena' using errcode = '42501';
    end if;
    select count(*) into n from nucleo.version_documento x
     where x.estado = 'en_cuarentena' and x.subida_en < now() - demora;
    return n;
  end
  $$;

-- Para la limpieza de objetos sin fila (tarea periódica, sin consorcio): de las claves dadas,
-- las que tienen una versión registrada. No devuelve nada más.
create function nucleo.claves_registradas(claves text[])
  returns setof text
  language plpgsql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
  begin
    if plataforma.persona_actual() is not null then
      raise exception 'solo un trabajo limpia el almacén' using errcode = '42501';
    end if;
    return query
      select x.clave_objeto from nucleo.version_documento x where x.clave_objeto = any(claves);
  end
  $$;

revoke all on function
  nucleo.claves_registradas(text[]),
  nucleo.versiones_demoradas(interval),
  nucleo.crear_documento(text, text, date, uuid, text),
  nucleo.nueva_version(uuid, text, integer, text, uuid, text),
  nucleo.fijar_version_objeto(uuid, text, uuid, text),
  nucleo.version_para_analizar(uuid),
  nucleo.resolver_version(uuid, text, text, timestamptz)
  from public;
grant execute on function
  nucleo.claves_registradas(text[]),
  nucleo.versiones_demoradas(interval),
  nucleo.crear_documento(text, text, date, uuid, text),
  nucleo.nueva_version(uuid, text, integer, text, uuid, text),
  nucleo.fijar_version_objeto(uuid, text, uuid, text),
  nucleo.version_para_analizar(uuid),
  nucleo.resolver_version(uuid, text, text, timestamptz)
  to cem_app;
"""


DOWNGRADE = """
do $guarda$
begin
  if exists (select 1 from nucleo.documento) then
    raise exception 'hay documentos: no se bajan'
      using errcode = 'P0001', hint = 'downgrade_con_datos';
  end if;
end
$guarda$;
drop function nucleo.claves_registradas(text[]);
drop function nucleo.versiones_demoradas(interval);
drop function nucleo.resolver_version(uuid, text, text, timestamptz);
drop function nucleo.version_para_analizar(uuid);
drop function nucleo.exigir_trabajo(uuid);
drop function nucleo.fijar_version_objeto(uuid, text, uuid, text);
drop function nucleo.nueva_version(uuid, text, integer, text, uuid, text);
drop function nucleo.crear_documento(text, text, date, uuid, text);
drop function nucleo.auditar_documento(uuid, text, text, uuid, uuid, text, jsonb);
drop function nucleo.exigir_carga_documentos(uuid, uuid, text);
drop table nucleo.version_documento;
drop table nucleo.documento;
delete from auditoria.proyeccion where accion like 'consorcio.documento.%';
"""


def upgrade() -> None:
    op.execute(_upgrade())


def downgrade() -> None:
    op.execute(DOWNGRADE)
