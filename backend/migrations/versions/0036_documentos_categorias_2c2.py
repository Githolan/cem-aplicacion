"""2c-2: quién ve y quién carga cada categoría, la lista y la descarga (RF24, segunda parte).

Diseño: `doc/diseno/H2c_Diseno.md` del repositorio CEM, §4. El reparto por categoría es una
inferencia que va al propietario (§8); mientras tanto se aplica así:

- **Lectura:** `documentos.ver` (el propietario por su vínculo, o la facultad) para seis
  categorías; `contratos_proveedores` y `otros` solo con la facultad `documentos.ver` vigente,
  nunca por el vínculo (`documentos.ver_restringidos`). El inquilino y el ocupante no ven nada
  (A6).
- **Carga:** `documentos.cargar` para todas, o la facultad nueva `documentos.cargar_operativos`
  (paquete de MC) solo para actas, presupuestos y mantenimiento. Los mandatos que ya existen no
  la reciben, como `auditoria.ver` en `0028`: un acta reduce un paquete, nunca lo amplía.
- **`nucleo.documentos_del_consorcio()`:** los documentos vigentes de las categorías que la
  persona de la sesión puede leer, con sus versiones `disponible` y, de las otras, solo las que
  subió ella. Segunda barrera, como `coeficientes_del_consorcio` (`0029`): la base vuelve a
  exigir el vínculo o la facultad; el filtro fino (modo, recurso) lo hace la aplicación.
- **`nucleo.categoria_para_cargar(documento)`:** la categoría de un documento, para que la ruta
  de carga elija la acción antes de leer el archivo; solo para quien puede cargar algo.
- **`nucleo.version_para_descargar(documento, version)`:** la clave del objeto de una versión de
  un documento vigente, con la misma barrera y la de las categorías restringidas.

Downgrade: se niega si algún mandato ya tiene `documentos.cargar_operativos` (es constancia del
acta; sin ella nadie pudo cargar con esa facultad); si no, vuelve a las escrituras de `0035`.

Revisión: 0036
Anterior: 0035
"""

from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None

FACULTAD = "documentos.cargar_operativos"
OPERATIVAS = ("actas_y_decisiones", "presupuestos", "mantenimiento_y_certificados")
RESTRINGIDAS = ("contratos_proveedores", "otros")


def _lista(valores: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in valores)


def _escrituras(exigir: str, exigir_categoria: str) -> str:
    """Las tres escrituras de `0035`. `exigir` es la verificación sin categoría; `exigir_categoria`
    la que conoce la categoría (`cat`). En `0035` las dos son la misma."""
    return f"""
create or replace function nucleo.crear_documento(categoria_nueva text, titulo_nuevo text,
                                                  fecha_nueva date, actor uuid,
                                                  origen_pedido text)
  returns uuid
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    cat text := categoria_nueva;
    d nucleo.documento;
  begin
    perform {exigir_categoria};
    insert into nucleo.documento (consorcio_id, categoria, titulo, fecha, cargado_por)
    values (c, categoria_nueva, btrim(titulo_nuevo), fecha_nueva, actor)
    returning * into d;
    -- El título no va a la auditoría: es texto libre.
    perform nucleo.auditar_documento(c, 'consorcio.documento.crear', 'documento', d.id, actor,
      origen_pedido, jsonb_build_object('categoria', d.categoria, 'fecha', d.fecha));
    return d.id;
  end
  $$;

create or replace function nucleo.nueva_version(documento uuid, tipo_nuevo text,
                                                tamano_nuevo integer, sha256_nuevo text,
                                                actor uuid, origen_pedido text)
  returns table (id uuid, clave_objeto text)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    v uuid := plataforma.uuid_v7();
    cat text;
    clave text;
  begin
    -- Primero sin categoría: quien no puede cargar nada no averigua si el documento existe.
    perform {exigir};
    select d.categoria into cat from nucleo.documento d
     where d.consorcio_id = c and d.id = documento and d.estado = 'vigente';
    if not found then
      raise exception 'el documento no existe' using errcode = 'P0001', hint = 'no_encontrado';
    end if;
    perform {exigir_categoria};
    clave := 'cem/' || c || '/' || documento || '/' || v;
    insert into nucleo.version_documento
      (consorcio_id, documento_id, id, tipo, tamano, sha256, clave_objeto, subida_por)
    values (c, documento, v, tipo_nuevo, tamano_nuevo, sha256_nuevo, clave, actor);
    perform nucleo.auditar_documento(c, 'consorcio.documento.cargar_version', 'version_documento',
      v, actor, origen_pedido, jsonb_build_object('tipo', tipo_nuevo, 'tamano', tamano_nuevo));
    return query select v, clave;
  end
  $$;

-- El identificador de versión del almacén, para borrar por `VersionId` un rechazado (§3). Solo
-- la versión que subió `actor` en cuarentena, en la misma transacción que `nueva_version`, que
-- ya verificó la categoría.
create or replace function nucleo.fijar_version_objeto(version uuid, version_almacen text,
                                                       actor uuid, origen_pedido text)
  returns void
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
  begin
    perform {exigir};
    update nucleo.version_documento x set version_objeto = version_almacen
     where x.consorcio_id = c and x.id = version and x.subida_por = actor
       and x.estado = 'en_cuarentena' and x.version_objeto is null;
    if not found then
      raise exception 'la versión no existe' using errcode = 'P0001', hint = 'no_encontrado';
    end if;
  end
  $$;
"""


EXIGIR_0035 = "nucleo.exigir_carga_documentos(c, actor, origen_pedido)"
EXIGIR_ALGUNA = "nucleo.exigir_carga_documentos(c, actor, origen_pedido, null)"
EXIGIR_CATEGORIA = "nucleo.exigir_carga_documentos(c, actor, origen_pedido, cat)"

UPGRADE = f"""
insert into nucleo.facultad (codigo, area, economica) values ('{FACULTAD}', 'documentos', false);
insert into nucleo.paquete_facultad (tipo, facultad) values ('miembro_consejo', '{FACULTAD}');

-- Quién carga: solo por la API, la persona de la sesión con `documentos.cargar` vigente, o con
-- `{FACULTAD}` para las categorías operativas. Sin categoría (null), alcanza con cualquiera de
-- las dos: es el paso previo de `nueva_version`, antes de saber la del documento.
create function nucleo.exigir_carga_documentos(c uuid, actor uuid, origen_pedido text,
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
               or ((categoria_pedida is null or categoria_pedida in ({_lista(OPERATIVAS)}))
                   and nucleo.tiene_facultad_vigente(c, actor, '{FACULTAD}', hoy))) then
      raise exception 'sin facultad de carga para esta categoría' using errcode = '42501';
    end if;
  end
  $$;
revoke all on function nucleo.exigir_carga_documentos(uuid, uuid, text, text) from public;
{_escrituras(EXIGIR_ALGUNA, EXIGIR_CATEGORIA)}
-- La categoría de un documento vigente, para que la ruta de carga evalúe su acción antes de
-- leer el archivo. Solo para quien puede cargar algo; si no existe, ninguna fila.
create function nucleo.categoria_para_cargar(documento uuid)
  returns text
  language plpgsql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    cat text;
  begin
    perform nucleo.exigir_carga_documentos(c, plataforma.persona_actual(), 'api', null);
    select d.categoria into cat from nucleo.documento d
     where d.consorcio_id = c and d.id = documento and d.estado = 'vigente';
    return cat;
  end
  $$;

-- Lectura: el vínculo de propietario vigente (que no sea un importado pendiente, `0024`) o la
-- facultad general `documentos.ver` vigente; para las restringidas, solo la facultad. Solo la
-- llaman las funciones de este archivo, siempre con `plataforma.persona_actual()`.
create function nucleo.lee_documentos(c uuid, quien uuid, restringidos boolean)
  returns boolean
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select nucleo.tiene_facultad_vigente(c, quien, 'documentos.ver', nucleo.hoy_del_consorcio(c))
        or (not restringidos and exists (
              select 1 from nucleo.vinculo v
               where v.consorcio_id = c and v.persona_id = quien and v.tipo = 'propietario'
                 and daterange(v.vigente_desde, v.vigente_hasta) @> nucleo.hoy_del_consorcio(c)
                 and v.id not in (select nucleo.vinculos_importados_pendientes())))
  $$;
revoke all on function nucleo.lee_documentos(uuid, uuid, boolean) from public;

create function nucleo.documentos_del_consorcio()
  returns table (documento_id uuid, categoria text, titulo text, fecha date,
                 cargado_en timestamptz, version_id uuid, estado text, motivo_rechazo text,
                 tipo text, tamano integer, sha256 text, subida_en timestamptz)
  language plpgsql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    quien uuid := plataforma.persona_actual();
    restringidos boolean;
  begin
    if c is null or quien is null then
      raise exception 'sin ámbito para leer los documentos' using errcode = '42501';
    end if;
    if not nucleo.lee_documentos(c, quien, false) then
      raise exception 'sin documentos.ver en este consorcio' using errcode = '42501';
    end if;
    restringidos := nucleo.lee_documentos(c, quien, true);
    return query
      select d.id, d.categoria, d.titulo, d.fecha, d.cargado_en, x.id, x.estado,
             x.motivo_rechazo, x.tipo, x.tamano, x.sha256, x.subida_en
        from nucleo.documento d
        left join nucleo.version_documento x
          on x.consorcio_id = d.consorcio_id and x.documento_id = d.id
         and (x.estado = 'disponible' or x.subida_por = quien)
       where d.consorcio_id = c and d.estado = 'vigente'
         and (restringidos or d.categoria not in ({_lista(RESTRINGIDAS)}))
       order by d.fecha desc, d.cargado_en desc, d.id, x.subida_en desc, x.id;
  end
  $$;

-- La versión pedida de un documento vigente, para firmar su descarga. La aplicación responde
-- 409 si no está `disponible` y la subió quien pide, y 404 si es de otro (como la lista, que no
-- se la muestra); acá solo se filtra el ámbito y la lectura.
create function nucleo.version_para_descargar(documento uuid, version uuid)
  returns table (categoria text, estado text, tipo text, clave_objeto text, version_objeto text,
                 titulo text, propia boolean)
  language plpgsql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    quien uuid := plataforma.persona_actual();
    cat text;
  begin
    if c is null or quien is null then
      raise exception 'sin ámbito para leer los documentos' using errcode = '42501';
    end if;
    if not nucleo.lee_documentos(c, quien, false) then
      raise exception 'sin documentos.ver en este consorcio' using errcode = '42501';
    end if;
    select d.categoria into cat from nucleo.documento d
     where d.consorcio_id = c and d.id = documento and d.estado = 'vigente';
    if not found then
      return;
    end if;
    if cat in ({_lista(RESTRINGIDAS)}) and not nucleo.lee_documentos(c, quien, true) then
      raise exception 'sin documentos.ver para esta categoría'
        using errcode = '42501', hint = 'categoria_restringida';
    end if;
    return query
      select cat, x.estado, x.tipo, x.clave_objeto, x.version_objeto, d.titulo,
             x.subida_por = quien
        from nucleo.version_documento x
        join nucleo.documento d on d.consorcio_id = x.consorcio_id and d.id = x.documento_id
       where x.consorcio_id = c and x.documento_id = documento and x.id = version;
  end
  $$;

drop function nucleo.exigir_carga_documentos(uuid, uuid, text);

revoke all on function
  nucleo.categoria_para_cargar(uuid),
  nucleo.documentos_del_consorcio(),
  nucleo.version_para_descargar(uuid, uuid)
  from public;
grant execute on function
  nucleo.categoria_para_cargar(uuid),
  nucleo.documentos_del_consorcio(),
  nucleo.version_para_descargar(uuid, uuid)
  to cem_app;
"""


def _downgrade() -> str:
    return f"""
do $guarda$
begin
  if exists (select 1 from nucleo.facultad_otorgada where facultad = '{FACULTAD}') then
    raise exception 'el downgrade de 0036 se niega: hay mandatos con {FACULTAD}'
      using errcode = 'P0001', hint = 'downgrade_con_datos',
            detail = 'las facultades otorgadas son constancia de lo que decidió el acta';
  end if;
end
$guarda$;
drop function nucleo.version_para_descargar(uuid, uuid);
drop function nucleo.documentos_del_consorcio();
drop function nucleo.categoria_para_cargar(uuid);
drop function nucleo.lee_documentos(uuid, uuid, boolean);

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
         or not nucleo.tiene_facultad_vigente(c, actor, 'documentos.cargar',
                                              nucleo.hoy_del_consorcio(c)) then
      raise exception 'sin documentos.cargar en este consorcio' using errcode = '42501';
    end if;
  end
  $$;
revoke all on function nucleo.exigir_carga_documentos(uuid, uuid, text) from public;
{_escrituras(EXIGIR_0035, EXIGIR_0035)}
drop function nucleo.exigir_carga_documentos(uuid, uuid, text, text);
delete from nucleo.paquete_facultad where facultad = '{FACULTAD}';
delete from nucleo.facultad where codigo = '{FACULTAD}';
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(_downgrade())
