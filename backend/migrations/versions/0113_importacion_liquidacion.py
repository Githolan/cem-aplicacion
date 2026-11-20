"""Importación de la liquidación del administrador, H5-1 (diseño H5 §4 y §5; RF42, RN30, PA32).

En Modo Consejo el registro económico se construye a partir de las liquidaciones importadas
(documento 08, §8). Esta migración pone la base: la facultad, la categoría del documento, la
importación con su lectura y los campos leídos. La confirmación campo por campo es H5-3.

**`liquidacion.importar`** (D2-A, 19-11-2026): sube la liquidación y, en H5-3, confirma lo leído.
No es económica. Entra en los paquetes `miembro_consejo` y `administrador`; como en `0036`, un acta
reduce un paquete y nunca lo amplía: los mandatos ya registrados no la reciben.

**Categoría `liquidaciones_del_administrador`.** El PDF entra como cualquier documento, con
antivirus y cuarentena (`0035`). Es operativa: la carga el Consejo con
`documentos.cargar_operativos` y la administración con `documentos.cargar`. La ven quienes ven
documentos.

**`nucleo.importacion_liquidacion`.** Una por versión de documento. `leyendo` al crearla; la lectura
de `cem-trabajos` la pasa a `para_confirmar` (con formato, período y diferencias) o a
`no_reconocida` (con el motivo). Un `trigger` permite solo ese paso y nada más: nada se borra.
`confirmada` y `descartada` quedan en el `check` para H5-3, que suma su paso.

**`nucleo.campo_preliminar`.** Cada dato leído: tipo, clave estable dentro de la importación, valor
(`jsonb`, los importes como texto decimal), confianza, origen (`determinista` o `ia`) y el renglón
de la página de donde salió. Las filas de unidades llevan la unidad del padrón si su departamento o
su número coinciden con una sola designación; si ninguna, la lectura suma la diferencia
`unidad_fuera_del_padron`, y si más de una (`1-A` y `1-a`), `unidad_ambigua`.
Solo se agregan: un `trigger` niega cambiarlas o borrarlas (H5-3 abre la
confirmación con su propia función).

**RN30 en la base** (`campos_oficiales_liquidacion`): solo campos `confirmado` o `corregido` de una
importación `confirmada`. Hasta H5-3 no hay ninguno, y una prueba lo exige (PA32, parte base).

Downgrade: se niega (`downgrade_con_datos`) si hay importaciones, documentos de la categoría o algún
mandato con la facultad.

Revisión: 0113
Anterior: 0112
"""

from alembic import op

revision = "0113"
down_revision = "0112"
branch_labels = None
depends_on = None

FACULTAD = "liquidacion.importar"
CATEGORIA = "liquidaciones_del_administrador"
TRABAJO = "liquidacion.leer"
"""El de `TRABAJO_LEER`, en `app/liquidacion/importacion.py`."""
ACCION = "consorcio.liquidacion.importar"

CATEGORIAS_ANTES = (
    "institucional",
    "actas_y_decisiones",
    "seguros",
    "contratos_proveedores",
    "facturas_y_comprobantes",
    "presupuestos",
    "mantenimiento_y_certificados",
    "otros",
)
OPERATIVAS_ANTES = ("actas_y_decisiones", "presupuestos", "mantenimiento_y_certificados")
TIPOS_CAMPO = ("total", "rubro", "gasto", "unidad", "importe_unidad", "saldo_unidad")
ESTADOS = ("leyendo", "para_confirmar", "no_reconocida", "confirmada", "descartada")


def _lista(valores: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in valores)


def _exigir_carga(operativas: tuple[str, ...]) -> str:
    """`exigir_carga_documentos` de `0036` con la lista de categorías operativas dada."""
    return f"""
create or replace function nucleo.exigir_carga_documentos(c uuid, actor uuid, origen_pedido text,
                                                          categoria_pedida text)
  returns void
  language plpgsql stable
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    hoy date;
  begin
    if c is null then
      raise exception 'sin consorcio en el ámbito' using errcode = '42501';
    end if;
    if origen_pedido is distinct from 'api' then
      raise exception 'origen inválido' using errcode = 'P0001', hint = 'origen_invalido';
    end if;
    hoy := nucleo.hoy_del_consorcio(c);
    if actor is null or actor is distinct from plataforma.persona_actual()
       or not (nucleo.tiene_facultad_vigente(c, actor, 'documentos.cargar', hoy)
               or ((categoria_pedida is null or categoria_pedida in ({_lista(operativas)}))
                   and nucleo.tiene_facultad_vigente(c, actor, 'documentos.cargar_operativos',
                                                     hoy))) then
      raise exception 'sin facultad de carga para esta categoría' using errcode = '42501';
    end if;
  end
  $$;
"""


def _categorias(categorias: tuple[str, ...]) -> str:
    return (
        "alter table nucleo.documento drop constraint documento_categoria_check,"
        f" add constraint documento_categoria_check check (categoria in ({_lista(categorias)}));"
    )


TABLAS = f"""
create table nucleo.importacion_liquidacion (
  consorcio_id          uuid not null,
  id                    uuid not null default plataforma.uuid_v7(),
  documento_id          uuid not null,
  version_documento_id  uuid not null unique references nucleo.version_documento (id),
  subida_por            uuid not null references nucleo.persona (id),
  creada_en             timestamptz not null default clock_timestamp(),
  estado                text not null default 'leyendo' check (estado in ({_lista(ESTADOS)})),
  formato               text check (formato in ('mis_expensas')),
  periodo               date check (periodo = date_trunc('month', periodo)::date),
  motivo                text check (length(motivo) between 1 and 500),
  diferencias           jsonb not null default '[]'::jsonb
                          check (jsonb_typeof(diferencias) = 'array'),
  leida_en              timestamptz,
  primary key (id),
  unique (consorcio_id, id),
  foreign key (consorcio_id, documento_id) references nucleo.documento (consorcio_id, id),
  constraint importacion_leida_si_no_leyendo check ((estado = 'leyendo') = (leida_en is null)),
  constraint importacion_no_reconocida_con_motivo
    check (estado <> 'no_reconocida' or (motivo is not null and formato is null)),
  constraint importacion_leida_con_formato
    check (estado in ('leyendo', 'no_reconocida') or (formato is not null and periodo is not null))
);
alter table nucleo.importacion_liquidacion enable row level security;
alter table nucleo.importacion_liquidacion force row level security;
create policy por_consorcio on nucleo.importacion_liquidacion for select
  using (consorcio_id = plataforma.consorcio_actual());
revoke all on nucleo.importacion_liquidacion from public, cem_app;

-- Solo el paso de la lectura: de `leyendo` a `para_confirmar` o `no_reconocida`, completando lo
-- leído. Lo demás no cambia y nada se borra.
create function nucleo.negar_cambio_importacion_liquidacion()
  returns trigger
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  begin
    if tg_op <> 'UPDATE' then
      raise exception 'una importación no se borra' using errcode = 'P0001',
        hint = 'historia_inmutable';
    end if;
    if old.estado <> 'leyendo' or new.estado not in ('para_confirmar', 'no_reconocida')
       or (new.consorcio_id, new.id, new.documento_id, new.version_documento_id, new.subida_por,
           new.creada_en)
          is distinct from
          (old.consorcio_id, old.id, old.documento_id, old.version_documento_id, old.subida_por,
           old.creada_en) then
      raise exception 'una importación solo pasa de leyendo a leída' using errcode = 'P0001',
        hint = 'historia_inmutable';
    end if;
    return new;
  end
  $$;
revoke all on function nucleo.negar_cambio_importacion_liquidacion() from public;
create trigger historia_importacion_liquidacion before update or delete
  on nucleo.importacion_liquidacion
  for each row execute function nucleo.negar_cambio_importacion_liquidacion();
create trigger historia_importacion_liquidacion_truncate before truncate
  on nucleo.importacion_liquidacion
  for each statement execute function nucleo.negar_cambio_importacion_liquidacion();

create table nucleo.campo_preliminar (
  consorcio_id     uuid not null,
  id               uuid not null default plataforma.uuid_v7(),
  importacion_id   uuid not null,
  orden            integer not null check (orden >= 0),
  tipo             text not null check (tipo in ({_lista(TIPOS_CAMPO)})),
  clave            text not null check (length(clave) between 1 and 200),
  valor            jsonb not null check (jsonb_typeof(valor) = 'object'),
  confianza        numeric(3, 2) not null check (confianza between 0 and 1),
  origen           text not null check (origen in ('determinista', 'ia')),
  pagina           integer not null check (pagina >= 1),
  renglon          numeric(7, 1) not null check (renglon >= 0),
  unidad_id        uuid,
  estado           text not null default 'pendiente'
                     check (estado in ('pendiente', 'confirmado', 'corregido', 'descartado')),
  valor_corregido  jsonb check (jsonb_typeof(valor_corregido) = 'object'),
  confirmado_por   uuid references nucleo.persona (id),
  confirmado_en    timestamptz,
  primary key (id),
  unique (consorcio_id, id),
  unique (importacion_id, clave),
  unique (importacion_id, orden),
  foreign key (consorcio_id, importacion_id)
    references nucleo.importacion_liquidacion (consorcio_id, id),
  foreign key (consorcio_id, unidad_id) references nucleo.unidad (consorcio_id, id),
  constraint campo_corregido_con_valor
    check ((estado = 'corregido') = (valor_corregido is not null)),
  constraint campo_pendiente_sin_confirmacion
    check ((estado = 'pendiente') = (confirmado_por is null and confirmado_en is null))
);
alter table nucleo.campo_preliminar enable row level security;
alter table nucleo.campo_preliminar force row level security;
create policy por_consorcio on nucleo.campo_preliminar for select
  using (consorcio_id = plataforma.consorcio_actual());
revoke all on nucleo.campo_preliminar from public, cem_app;

create function nucleo.negar_cambio_campo_preliminar()
  returns trigger
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  begin
    raise exception 'lo leído de una liquidación solo se agrega' using errcode = 'P0001',
      hint = 'historia_inmutable';
  end
  $$;
revoke all on function nucleo.negar_cambio_campo_preliminar() from public;
create trigger historia_campo_preliminar before update or delete on nucleo.campo_preliminar
  for each row execute function nucleo.negar_cambio_campo_preliminar();
create trigger historia_campo_preliminar_truncate before truncate on nucleo.campo_preliminar
  for each statement execute function nucleo.negar_cambio_campo_preliminar();
"""

FUNCIONES = f"""
-- La designación de una unidad como la compara la importación del padrón (`clave_designacion`):
-- espacios juntados y sin mayúsculas.
create function nucleo.clave_designacion(designacion text)
  returns text
  language sql immutable
  set search_path = pg_catalog, pg_temp
  as $$ select lower(regexp_replace(btrim(designacion), '\\s+', ' ', 'g')) $$;

-- Las unidades vigentes cuya designación coincide con el departamento leído; si ninguna, con el
-- número de la unidad. Más de una es una ambigüedad (como `_unidades` del padrón, RF02).
create function nucleo.unidades_que_coinciden(c uuid, depto text, uf text)
  returns uuid[]
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select coalesce(
      (select array_agg(u.id) from nucleo.unidad u
        where u.consorcio_id = c and u.eliminado_en is null
          and nucleo.clave_designacion(u.designacion) = nucleo.clave_designacion(depto)),
      (select array_agg(u.id) from nucleo.unidad u
        where u.consorcio_id = c and u.eliminado_en is null
          and nucleo.clave_designacion(u.designacion) = nucleo.clave_designacion(uf)),
      '{{}}'::uuid[])
  $$;

-- La unidad de una fila leída: solo si coincide exactamente una.
create function nucleo.unidad_de_la_lectura(c uuid, depto text, uf text)
  returns uuid
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select case when cardinality(x) = 1 then x[1] end
      from nucleo.unidades_que_coinciden(c, depto, uf) as x
  $$;

-- Quien importa, por la API, con su sesión y sin gesto (no mueve dinero).
create function nucleo.crear_importacion_liquidacion(documento uuid, version uuid, actor uuid,
                                                     origen_pedido text, sesion uuid)
  returns uuid
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    v nucleo.version_documento;
    i uuid;
  begin
    perform nucleo.exigir_acto_sin_gesto(c, actor, sesion, '{FACULTAD}', origen_pedido);
    select x.* into v from nucleo.version_documento x
      join nucleo.documento d on d.consorcio_id = x.consorcio_id and d.id = x.documento_id
     where x.consorcio_id = c and x.id = version and d.id = documento and d.estado = 'vigente'
       and d.categoria = '{CATEGORIA}';
    if v.id is null or v.estado <> 'disponible' or v.tipo <> 'application/pdf' then
      raise exception 'la versión no es un PDF disponible de una liquidación'
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
    values (c, actor, origen_pedido, '{ACCION}', 'importacion_liquidacion', i,
            jsonb_build_object('documento', documento, 'version', version), '', '');
    return i;
  end
  $$;

-- Para `cem-trabajos`: la versión a leer y si la importación todavía espera su lectura.
create function nucleo.importacion_para_leer(importacion uuid)
  returns table (estado text, version_documento_id uuid)
  language plpgsql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
  begin
    perform nucleo.exigir_trabajo(c);
    return query select x.estado, x.version_documento_id from nucleo.importacion_liquidacion x
                  where x.consorcio_id = c and x.id = importacion;
  end
  $$;

-- Lo que leyó `cem-trabajos`. Con formato: los campos, en el orden dado, y las diferencias de la
-- lectura más las unidades que no están en el padrón. Sin formato: el motivo, sin campos.
-- Devuelve falso si la importación ya estaba leída: un trabajo repetido no la vuelve a escribir.
create function nucleo.guardar_lectura_liquidacion(importacion uuid, formato_leido text,
                                                   periodo_leido date, motivo_leido text,
                                                   diferencias_leidas jsonb, campos jsonb)
  returns boolean
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    actual text;
    fuera jsonb;
  begin
    perform nucleo.exigir_trabajo(c);
    select x.estado into actual from nucleo.importacion_liquidacion x
     where x.consorcio_id = c and x.id = importacion for update;
    if actual is null then
      raise exception 'importación inexistente' using errcode = 'P0001', hint = 'no_encontrado';
    end if;
    if actual <> 'leyendo' then
      return false;
    end if;
    if formato_leido is null then
      update nucleo.importacion_liquidacion
         set estado = 'no_reconocida', motivo = motivo_leido, leida_en = clock_timestamp()
       where consorcio_id = c and id = importacion;
      return true;
    end if;
    insert into nucleo.campo_preliminar
      (consorcio_id, importacion_id, orden, tipo, clave, valor, confianza, origen, pagina,
       renglon, unidad_id)
    select c, importacion, (e.ord - 1)::integer, e.campo->>'tipo', e.campo->>'clave',
           e.campo->'valor', (e.campo->>'confianza')::numeric, e.campo->>'origen',
           (e.campo->>'pagina')::integer, (e.campo->>'renglon')::numeric,
           case when e.campo->>'tipo' in ('unidad', 'importe_unidad', 'saldo_unidad') then
             nucleo.unidad_de_la_lectura(c, e.campo->'valor'->>'depto', e.campo->'valor'->>'uf')
           end
      from jsonb_array_elements(campos) with ordinality as e (campo, ord);
    select coalesce(jsonb_agg(jsonb_build_object(
             'regla', case when cardinality(nucleo.unidades_que_coinciden(
                                  c, p.valor->>'depto', p.valor->>'uf')) > 1
                           then 'unidad_ambigua' else 'unidad_fuera_del_padron' end,
             'detalle', 'UF ' || (p.valor->>'uf') || ' ' || (p.valor->>'depto'),
             'pagina', p.pagina, 'renglon', p.renglon) order by p.orden), '[]'::jsonb)
      into fuera
      from nucleo.campo_preliminar p
     where p.consorcio_id = c and p.importacion_id = importacion and p.tipo = 'unidad'
       and p.unidad_id is null;
    update nucleo.importacion_liquidacion
       set estado = 'para_confirmar', formato = formato_leido, periodo = periodo_leido,
           diferencias = diferencias_leidas || fuera, leida_en = clock_timestamp()
     where consorcio_id = c and id = importacion;
    return true;
  end
  $$;

-- La importación para quien importa. Sin filas si no existe en el consorcio: la ruta responde 404.
create function nucleo.importacion_liquidacion_lectura(importacion uuid, actor uuid,
                                                       origen_pedido text, sesion uuid)
  returns table (id uuid, documento_id uuid, version_documento_id uuid, subida_por uuid,
                 creada_en timestamptz, estado text, formato text, periodo date, motivo text,
                 diferencias jsonb, leida_en timestamptz)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
  begin
    perform nucleo.exigir_acto_sin_gesto(c, actor, sesion, '{FACULTAD}', origen_pedido);
    return query
      select x.id, x.documento_id, x.version_documento_id, x.subida_por, x.creada_en,
             -- Si la lectura agotó sus intentos (`muerto`), la importación no queda «leyendo»
             -- para siempre: se dice, y se reintenta con una versión nueva del documento.
             case when x.estado = 'leyendo' and exists (
                    select 1 from plataforma.jobs j
                     where j.consorcio_id = c and j.clave_unica = '{TRABAJO}:' || x.id::text
                       and j.estado::text = 'muerto')
                  then 'falla_de_lectura' else x.estado end,
             x.formato, x.periodo, x.motivo, x.diferencias, x.leida_en
        from nucleo.importacion_liquidacion x
       where x.consorcio_id = c and x.id = importacion;
  end
  $$;

-- Los campos leídos de una importación, en orden, para quien importa. Todos, con su estado: es la
-- pantalla de confirmación, no una lectura oficial.
create function nucleo.campos_preliminares_lectura(importacion uuid, actor uuid,
                                                   origen_pedido text, sesion uuid)
  returns table (id uuid, orden integer, tipo text, clave text, valor jsonb, confianza numeric,
                 origen text, pagina integer, renglon numeric, unidad_id uuid, estado text,
                 valor_corregido jsonb)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
  begin
    perform nucleo.exigir_acto_sin_gesto(c, actor, sesion, '{FACULTAD}', origen_pedido);
    return query
      select p.id, p.orden, p.tipo, p.clave, p.valor, p.confianza, p.origen, p.pagina,
             p.renglon, p.unidad_id, p.estado, p.valor_corregido
        from nucleo.campo_preliminar p
       where p.consorcio_id = c and p.importacion_id = importacion
       order by p.orden;
  end
  $$;

-- RN30: lo único que una lectura oficial (recálculo, panel, resumen) puede usar de una
-- importación. Solo campos confirmados o corregidos (con el valor corregido) de una importación
-- confirmada.
create function nucleo.campos_oficiales_liquidacion(importacion uuid, actor uuid,
                                                    origen_pedido text, sesion uuid)
  returns table (orden integer, tipo text, clave text, valor jsonb, unidad_id uuid)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
  begin
    perform nucleo.exigir_acto_sin_gesto(c, actor, sesion, '{FACULTAD}', origen_pedido);
    return query
      select p.orden, p.tipo, p.clave, coalesce(p.valor_corregido, p.valor), p.unidad_id
        from nucleo.campo_preliminar p
        join nucleo.importacion_liquidacion x
          on x.consorcio_id = p.consorcio_id and x.id = p.importacion_id
       where p.consorcio_id = c and p.importacion_id = importacion
         and x.estado = 'confirmada' and p.estado in ('confirmado', 'corregido')
       order by p.orden;
  end
  $$;
"""

RUTAS = (
    "nucleo.crear_importacion_liquidacion(uuid, uuid, uuid, text, uuid)",
    "nucleo.importacion_para_leer(uuid)",
    "nucleo.guardar_lectura_liquidacion(uuid, text, date, text, jsonb, jsonb)",
    "nucleo.importacion_liquidacion_lectura(uuid, uuid, text, uuid)",
    "nucleo.campos_preliminares_lectura(uuid, uuid, text, uuid)",
    "nucleo.campos_oficiales_liquidacion(uuid, uuid, text, uuid)",
)
INTERNAS = (
    "nucleo.clave_designacion(text)",
    "nucleo.unidades_que_coinciden(uuid, text, text)",
    "nucleo.unidad_de_la_lectura(uuid, text, text)",
)

GUARDA = f"""
do $guarda$
begin
  if exists (select 1 from nucleo.importacion_liquidacion)
     or exists (select 1 from nucleo.documento where categoria = '{CATEGORIA}')
     or exists (select 1 from nucleo.facultad_otorgada where facultad = '{FACULTAD}') then
    raise exception '0113 no baja: hay importaciones, liquidaciones cargadas o mandatos con la'
      ' facultad' using errcode = 'P0001', hint = 'downgrade_con_datos';
  end if;
end
$guarda$;
"""


def upgrade() -> None:
    op.execute(
        f"insert into nucleo.facultad (codigo, area, economica)"
        f" values ('{FACULTAD}', 'liquidacion', false);"
        f" insert into nucleo.paquete_facultad (tipo, facultad)"
        f" values ('miembro_consejo', '{FACULTAD}'), ('administrador', '{FACULTAD}');"
    )
    op.execute(_categorias((*CATEGORIAS_ANTES, CATEGORIA)))
    op.execute(_exigir_carga((*OPERATIVAS_ANTES, CATEGORIA)))
    op.execute(TABLAS)
    op.execute(FUNCIONES)
    for funcion in INTERNAS + RUTAS:
        op.execute(f"revoke all on function {funcion} from public")
    for funcion in RUTAS:
        op.execute(f"grant execute on function {funcion} to cem_app")
    op.execute(
        "insert into auditoria.proyeccion (accion, texto, recurso, campos) values"
        f" ('{ACCION}', 'Subió la liquidación del administrador para leerla',"
        " 'Liquidación importada', array['documento'])"
    )


def downgrade() -> None:
    op.execute(GUARDA)
    op.execute(f"delete from auditoria.proyeccion where accion = '{ACCION}'")
    for funcion in reversed(RUTAS + INTERNAS):
        op.execute(f"drop function {funcion}")
    op.execute("drop table nucleo.campo_preliminar")
    op.execute("drop function nucleo.negar_cambio_campo_preliminar()")
    op.execute("drop table nucleo.importacion_liquidacion")
    op.execute("drop function nucleo.negar_cambio_importacion_liquidacion()")
    op.execute(_exigir_carga(OPERATIVAS_ANTES))
    op.execute(_categorias(CATEGORIAS_ANTES))
    op.execute(f"delete from nucleo.paquete_facultad where facultad = '{FACULTAD}'")
    op.execute(f"delete from nucleo.facultad where codigo = '{FACULTAD}'")
