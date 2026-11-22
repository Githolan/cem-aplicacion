"""La liquidación oficial del período en Modo Consejo, y quién la lee (diseño H5 §1, §4 y §6, D3-A).

**Una oficial por consorcio y período.** La oficial es la importación `confirmada`. Confirmar otra
del mismo período se niega (`periodo_ya_confirmado`) salvo que diga, con motivo, que reemplaza a la
oficial actual: entonces la anterior pasa a `reemplazada` en la misma transacción. La reemplazada
queda como historia, se lee y no se borra; cada importación se reemplaza una sola vez.

- **Columnas** `reemplaza_a` y `motivo_reemplazo` en `nucleo.importacion_liquidacion`, juntas o
  ninguna, solo en una confirmada (o en una que después fue reemplazada).
- **La última barrera** frente a dos confirmaciones a la vez: el índice único parcial
  `importacion_oficial_por_periodo` (una `confirmada` por consorcio y período) y la restricción
  `importacion_reemplaza_una_vez` (una sola importación reemplaza a otra). Antes, un candado del
  período ordena las confirmaciones para que la segunda vea a la primera y responda con su motivo.
- **`confirmar_importacion_liquidacion`** suma `reemplaza` y `motivo_pedido` (la misma facultad,
  `liquidacion.importar`). Sin los dos, como hasta `0118`. La auditoría de la confirmación lleva a
  quién reemplaza; el motivo es texto libre y queda en la fila, no en la auditoría (E31), como el
  del descarte (`0115`).
- **El `trigger`** suma un paso: de `confirmada` a `reemplazada`, sin cambiar nada más.
- **`campos_oficiales_liquidacion`** sigue dando los datos de una reemplazada (historia, RN30).

**Quién lee la liquidación oficial (D3-A).** Cada lectura es `security definer` con su propia
segunda barrera, y `cem_app` solo recibe `EXECUTE` sobre ellas:

- **El Consejo y la administración** (`liquidacion.ver_importada`, facultad
  `fiscalizacion.ver_panel`, barrera `exigir_ver_panel` de `0094`): la lista de las oficiales
  (vigentes y reemplazadas) y cada una con sus datos oficiales y sus diferencias. Nunca una
  importación que no se confirmó (RN30).
- **Cada propietario, el resumen** (`fiscalizacion.ver_resumen`): los períodos con liquidación
  vigente y, de uno, el gasto por rubro, los totales por grupo, el total y los vencimientos. Sin
  gastos, diferencias ni unidades.
- **Cada propietario, su unidad** (`cuenta.ver_propia`): lo que la liquidación vigente dice de cada
  unidad donde es propietario (saldo e importe), por la unidad oficial (con las correcciones). Una
  unidad propia que no figura vuelve marcada así. Nunca otra unidad (RN15).

La barrera de las dos lecturas del propietario es un vínculo de propietario vigente, confirmado
(P4) y que no sea un importado pendiente (`0024`), como `unidades_propias_confirmadas` de `0030`.
Inquilinos y ocupantes no leen ninguna de las tres en este corte.

Downgrade: se niega (`downgrade_con_datos`) si hay importaciones reemplazadas o que reemplazan.

Datos de prueba: SINTETICOS / NO REAL.

Revisión: 0119
Anterior: 0118
"""

import importlib.util
from pathlib import Path

from alembic import op

revision = "0119"
down_revision = "0118"
branch_labels = None
depends_on = None

LARGO_MOTIVO = 500
"""El de `ConfirmacionImportacion.motivo`."""
TRABAJO = "liquidacion.leer"
ACCION_CONFIRMAR = "consorcio.liquidacion.confirmar_importada"
CAMPOS_AUDITORIA_ANTES = "array['campos']"
CAMPOS_AUDITORIA_AHORA = "array['campos', 'reemplaza_a']"
ESTADOS_ANTES = "('leyendo', 'para_confirmar', 'no_reconocida', 'confirmada', 'descartada')"
ESTADOS_AHORA = (
    "('leyendo', 'para_confirmar', 'no_reconocida', 'confirmada', 'descartada', 'reemplazada')"
)


def _de(archivo: str, atributo: str, firma: str) -> str:
    """La definición de la función `firma` tal como la escribió una migración anterior, desde su
    `create` hasta su `$$;` (vuelve igual en el downgrade)."""
    ruta = Path(__file__).with_name(archivo)
    spec = importlib.util.spec_from_file_location(f"migracion_{archivo[:4]}", ruta)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    texto = getattr(modulo, atributo)
    medio = texto.index(f"function {firma}(")
    inicio = texto.rindex("create", 0, medio)
    fin = texto.index("  $$;", medio) + len("  $$;")
    return texto[inicio:fin].replace("create function", "create or replace function", 1)


GUARDA_SUBIDA = """
do $guarda$
begin
  if exists (select 1 from nucleo.importacion_liquidacion
              where estado = 'confirmada'
              group by consorcio_id, periodo having count(*) > 1) then
    raise exception '0119 no sube: hay dos liquidaciones confirmadas del mismo período en un'
      ' consorcio; hay que decidir cuál es la oficial antes de migrar'
      using errcode = 'P0001', hint = 'liquidaciones_oficiales_duplicadas';
  end if;
end
$guarda$;
"""

TABLAS = f"""
alter table nucleo.importacion_liquidacion
  drop constraint importacion_liquidacion_estado_check,
  add constraint importacion_liquidacion_estado_check check (estado in {ESTADOS_AHORA}),
  add column reemplaza_a uuid,
  add column motivo_reemplazo text
    check (length(btrim(motivo_reemplazo)) between 1 and {LARGO_MOTIVO}),
  add constraint importacion_reemplazo_con_motivo
    check ((reemplaza_a is null) = (motivo_reemplazo is null)),
  add constraint importacion_reemplazo_confirmada
    check (reemplaza_a is null or estado in ('confirmada', 'reemplazada')),
  add constraint importacion_reemplazo_de_otra check (reemplaza_a <> id),
  add constraint importacion_reemplaza_una_vez unique (consorcio_id, reemplaza_a),
  add constraint importacion_reemplaza_existente foreign key (consorcio_id, reemplaza_a)
    references nucleo.importacion_liquidacion (consorcio_id, id),
  drop constraint importacion_decidida_con_quien,
  add constraint importacion_decidida_con_quien
    check ((estado in ('confirmada', 'descartada', 'reemplazada'))
           = (decidida_por is not null and decidida_en is not null));

-- La última barrera: una sola liquidación oficial vigente por consorcio y período.
create unique index importacion_oficial_por_periodo
  on nucleo.importacion_liquidacion (consorcio_id, periodo) where estado = 'confirmada';

-- La lectura: de `leyendo` a `para_confirmar` o `no_reconocida` (`0113`). La decisión: de
-- `para_confirmar` a `confirmada` (con a quién reemplaza, si reemplaza), o de `para_confirmar` o
-- `no_reconocida` a `descartada` (`0115`). El reemplazo: de `confirmada` a `reemplazada`, sin
-- cambiar quién la decidió ni cuándo. Lo demás no cambia y nada se borra.
create or replace function nucleo.negar_cambio_importacion_liquidacion()
  returns trigger
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  begin
    if tg_op <> 'UPDATE' then
      raise exception 'una importación no se borra' using errcode = 'P0001',
        hint = 'historia_inmutable';
    end if;
    if (new.consorcio_id, new.id, new.documento_id, new.version_documento_id, new.subida_por,
        new.creada_en)
       is distinct from
       (old.consorcio_id, old.id, old.documento_id, old.version_documento_id, old.subida_por,
        old.creada_en)
       or not (
         (old.estado = 'leyendo' and new.estado in ('para_confirmar', 'no_reconocida')
          and new.decidida_por is null)
         or (old.estado = 'para_confirmar' and new.estado = 'confirmada')
         or (old.estado in ('para_confirmar', 'no_reconocida') and new.estado = 'descartada')
         or (old.estado = 'confirmada' and new.estado = 'reemplazada'
             and (new.decidida_por, new.decidida_en, new.motivo_descarte)
                 is not distinct from
                 (old.decidida_por, old.decidida_en, old.motivo_descarte)))
       or (old.estado <> 'leyendo'
           and (new.formato, new.periodo, new.motivo, new.diferencias, new.leida_en)
               is distinct from
               (old.formato, old.periodo, old.motivo, old.diferencias, old.leida_en))
       or (not (old.estado = 'para_confirmar' and new.estado = 'confirmada')
           and (new.reemplaza_a, new.motivo_reemplazo)
               is distinct from (old.reemplaza_a, old.motivo_reemplazo)) then
      raise exception 'ese paso no existe para una importación' using errcode = 'P0001',
        hint = 'historia_inmutable';
    end if;
    return new;
  end
  $$;
"""

OFICIALES = f"""
-- RN30: los datos oficiales de una importación confirmada o reemplazada: los campos confirmados y
-- los corregidos (con el valor corregido). Si se corrigió `uf` o `depto` de un campo de unidad, la
-- unidad oficial sale del valor corregido (`0115`). Interna: la usan las lecturas de abajo.
create function nucleo.campos_oficiales_de(c uuid, importacion uuid)
  returns table (orden integer, tipo text, clave text, valor jsonb, unidad_id uuid)
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select p.orden, p.tipo, p.clave, coalesce(p.valor_corregido, p.valor),
           case when p.valor_corregido is null then p.unidad_id
                when p.tipo in ('unidad', 'importe_unidad', 'saldo_unidad') then
                  nucleo.unidad_de_la_lectura(c, p.valor_corregido->>'depto',
                                              p.valor_corregido->>'uf')
           end
      from nucleo.campo_preliminar p
      join nucleo.importacion_liquidacion x
        on x.consorcio_id = p.consorcio_id and x.id = p.importacion_id
     where p.consorcio_id = c and p.importacion_id = importacion
       and x.estado in ('confirmada', 'reemplazada') and p.estado in ('confirmado', 'corregido')
     order by p.orden
  $$;

-- Para quien importa (el recálculo): los datos oficiales, también de una reemplazada.
create or replace function nucleo.campos_oficiales_liquidacion(importacion uuid, actor uuid,
                                                               origen_pedido text, sesion uuid)
  returns table (orden integer, tipo text, clave text, valor jsonb, unidad_id uuid)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
  begin
    perform nucleo.exigir_acto_importacion(c, actor, sesion, origen_pedido);
    return query select * from nucleo.campos_oficiales_de(c, importacion);
  end
  $$;

-- Las importaciones del consorcio, la más nueva primero, para quien importa (reemplaza la de
-- `0115`): con a quién reemplaza y por qué.
create function nucleo.importaciones_liquidacion_lectura(actor uuid, origen_pedido text,
                                                         sesion uuid)
  returns table (id uuid, documento_id uuid, version_documento_id uuid, subida_por uuid,
                 creada_en timestamptz, estado text, formato text, periodo date, motivo text,
                 diferencias integer, campos integer, pendientes integer, decidida_por uuid,
                 decidida_en timestamptz, motivo_descarte text, reemplaza_a uuid,
                 motivo_reemplazo text)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
  begin
    perform nucleo.exigir_acto_importacion(c, actor, sesion, origen_pedido);
    return query
      select x.id, x.documento_id, x.version_documento_id, x.subida_por, x.creada_en,
             case when x.estado = 'leyendo' and exists (
                    select 1 from plataforma.jobs j
                     where j.consorcio_id = c and j.clave_unica = '{TRABAJO}:' || x.id::text
                       and j.estado::text = 'muerto')
                  then 'falla_de_lectura' else x.estado end,
             x.formato, x.periodo, x.motivo, jsonb_array_length(x.diferencias),
             (select count(*)::integer from nucleo.campo_preliminar p
               where p.consorcio_id = c and p.importacion_id = x.id),
             (select count(*)::integer from nucleo.campo_preliminar p
               where p.consorcio_id = c and p.importacion_id = x.id and p.estado = 'pendiente'),
             x.decidida_por, x.decidida_en, x.motivo_descarte, x.reemplaza_a, x.motivo_reemplazo
        from nucleo.importacion_liquidacion x
       where x.consorcio_id = c
       order by x.creada_en desc, x.id desc;
  end
  $$;

-- La importación para quien importa, con la decisión y el reemplazo (reemplaza la de `0115`).
create function nucleo.importacion_liquidacion_lectura(importacion uuid, actor uuid,
                                                       origen_pedido text, sesion uuid)
  returns table (id uuid, documento_id uuid, version_documento_id uuid, subida_por uuid,
                 creada_en timestamptz, estado text, formato text, periodo date, motivo text,
                 diferencias jsonb, leida_en timestamptz, decidida_por uuid,
                 decidida_en timestamptz, motivo_descarte text, reemplaza_a uuid,
                 motivo_reemplazo text)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
  begin
    perform nucleo.exigir_acto_importacion(c, actor, sesion, origen_pedido);
    return query
      select x.id, x.documento_id, x.version_documento_id, x.subida_por, x.creada_en,
             case when x.estado = 'leyendo' and exists (
                    select 1 from plataforma.jobs j
                     where j.consorcio_id = c and j.clave_unica = '{TRABAJO}:' || x.id::text
                       and j.estado::text = 'muerto')
                  then 'falla_de_lectura' else x.estado end,
             x.formato, x.periodo, x.motivo, x.diferencias, x.leida_en, x.decidida_por,
             x.decidida_en, x.motivo_descarte, x.reemplaza_a, x.motivo_reemplazo
        from nucleo.importacion_liquidacion x
       where x.consorcio_id = c and x.id = importacion;
  end
  $$;
"""

CONFIRMAR = f"""
-- Confirmar la importación entera: sin campos pendientes, y la única oficial de su período. Si el
-- período ya tiene una, solo reemplazándola con motivo: la anterior pasa a `reemplazada` en la
-- misma transacción.
create function nucleo.confirmar_importacion_liquidacion(importacion uuid, reemplaza uuid,
                                                         motivo_pedido text, actor uuid,
                                                         origen_pedido text, sesion uuid)
  returns void
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    n integer;
    p date;
    actual uuid;
    restriccion text;
  begin
    perform nucleo.exigir_acto_importacion(c, actor, sesion, origen_pedido);
    if nucleo.importacion_para_decidir(c, importacion) <> 'para_confirmar' then
      raise exception 'la importación no está para confirmar' using errcode = 'P0001',
        hint = 'importacion_decidida';
    end if;
    if exists (select 1 from nucleo.campo_preliminar x
                where x.consorcio_id = c and x.importacion_id = importacion
                  and x.estado = 'pendiente') then
      raise exception 'quedan campos pendientes' using errcode = 'P0001',
        hint = 'campos_pendientes';
    end if;
    if (reemplaza is null) <> (motivo_pedido is null)
       or (motivo_pedido is not null
           and length(btrim(motivo_pedido)) not between 1 and {LARGO_MOTIVO}) then
      raise exception 'el reemplazo va con su motivo' using errcode = 'P0001',
        hint = 'motivo_invalido';
    end if;
    select x.periodo into p from nucleo.importacion_liquidacion x
     where x.consorcio_id = c and x.id = importacion;
    -- Dos confirmaciones del mismo período se ordenan: la segunda ve la que dejó la primera.
    perform pg_advisory_xact_lock(hashtextextended(
      'liquidacion_oficial:' || c::text || ':' || p::text, 0));
    select x.id into actual from nucleo.importacion_liquidacion x
     where x.consorcio_id = c and x.periodo = p and x.estado = 'confirmada';
    if reemplaza is not null and reemplaza is distinct from actual then
      raise exception 'lo que se reemplaza no es la liquidación oficial de ese período'
        using errcode = 'P0001', hint = 'reemplazo_invalido';
    end if;
    if actual is not null and reemplaza is null then
      raise exception 'el período ya tiene su liquidación oficial' using errcode = 'P0001',
        hint = 'periodo_ya_confirmado';
    end if;
    select count(*) into n from nucleo.campo_preliminar x
     where x.consorcio_id = c and x.importacion_id = importacion
       and x.estado in ('confirmado', 'corregido');
    begin
      if actual is not null then
        update nucleo.importacion_liquidacion set estado = 'reemplazada'
         where consorcio_id = c and id = actual;
      end if;
      update nucleo.importacion_liquidacion
         set estado = 'confirmada', decidida_por = actor, decidida_en = clock_timestamp(),
             reemplaza_a = reemplaza, motivo_reemplazo = btrim(motivo_pedido)
       where consorcio_id = c and id = importacion;
    exception when unique_violation then
      get stacked diagnostics restriccion = constraint_name;
      if restriccion = 'importacion_oficial_por_periodo' then
        raise exception 'el período ya tiene su liquidación oficial' using errcode = 'P0001',
          hint = 'periodo_ya_confirmado';
      end if;
      if restriccion = 'importacion_reemplaza_una_vez' then
        raise exception 'esa liquidación ya se reemplazó' using errcode = 'P0001',
          hint = 'reemplazo_invalido';
      end if;
      raise;
    end;
    insert into auditoria.eventos
      (consorcio_id, actor_persona_id, origen, accion, entidad, entidad_id, despues,
       hash_previo, hash)
    values (c, actor, origen_pedido, '{ACCION_CONFIRMAR}', 'importacion_liquidacion',
            importacion, jsonb_build_object('campos', n, 'reemplaza_a', reemplaza), '', '');
  end
  $$;
"""

LECTURAS = """
-- Segunda barrera de las lecturas del propietario: un vínculo de propietario vigente, confirmado
-- (P4) y que no sea un importado pendiente (`0024`). Devuelve sus unidades.
create function nucleo.exigir_propietario_confirmado(c uuid, quien uuid)
  returns uuid[]
  language plpgsql stable
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    propias uuid[];
  begin
    if c is null or quien is null then
      raise exception 'sin ámbito para leer la liquidación' using errcode = '42501';
    end if;
    select coalesce(array_agg(distinct v.unidad_id), '{}') into propias
      from nucleo.vinculo v
     where v.consorcio_id = c and v.persona_id = quien and v.tipo = 'propietario'
       and v.confirmado_en is not null
       and daterange(v.vigente_desde, v.vigente_hasta) @> nucleo.hoy_del_consorcio(c)
       and v.id not in (select nucleo.vinculos_importados_pendientes());
    if cardinality(propias) = 0 then
      raise exception 'sin un vínculo de propietario confirmado en este consorcio'
        using errcode = '42501';
    end if;
    return propias;
  end
  $$;

-- La liquidación vigente (la confirmada) del período pedido; sin período, la del último que la
-- tiene. Nula si no hay.
create function nucleo.liquidacion_vigente(c uuid, periodo_pedido date)
  returns uuid
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select x.id from nucleo.importacion_liquidacion x
     where x.consorcio_id = c and x.estado = 'confirmada'
       and (periodo_pedido is null or x.periodo = periodo_pedido)
     order by x.periodo desc
     limit 1
  $$;

-- El Consejo y la administración (`fiscalizacion.ver_panel`): las liquidaciones oficiales, la
-- vigente y las reemplazadas de cada período, el período más nuevo primero.
create function nucleo.liquidaciones_oficiales_lectura()
  returns table (id uuid, periodo date, formato text, decidida_por uuid, decidida_en timestamptz,
                 vigente boolean, reemplaza_a uuid, motivo_reemplazo text, reemplazada_por uuid,
                 diferencias integer)
  language plpgsql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
  begin
    perform nucleo.exigir_ver_panel(c, plataforma.persona_actual());
    return query
      select x.id, x.periodo, x.formato, x.decidida_por, x.decidida_en, x.estado = 'confirmada',
             x.reemplaza_a, x.motivo_reemplazo,
             (select y.id from nucleo.importacion_liquidacion y
               where y.consorcio_id = c and y.reemplaza_a = x.id),
             jsonb_array_length(x.diferencias)
        from nucleo.importacion_liquidacion x
       where x.consorcio_id = c and x.estado in ('confirmada', 'reemplazada')
       order by x.periodo desc, x.decidida_en desc;
  end
  $$;

-- Una liquidación oficial con sus diferencias. Sin filas si no es oficial (RN30): 404.
create function nucleo.liquidacion_oficial_lectura(importacion uuid)
  returns table (id uuid, periodo date, formato text, decidida_por uuid, decidida_en timestamptz,
                 vigente boolean, reemplaza_a uuid, motivo_reemplazo text, reemplazada_por uuid,
                 diferencias jsonb)
  language plpgsql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
  begin
    perform nucleo.exigir_ver_panel(c, plataforma.persona_actual());
    return query
      select x.id, x.periodo, x.formato, x.decidida_por, x.decidida_en, x.estado = 'confirmada',
             x.reemplaza_a, x.motivo_reemplazo,
             (select y.id from nucleo.importacion_liquidacion y
               where y.consorcio_id = c and y.reemplaza_a = x.id),
             x.diferencias
        from nucleo.importacion_liquidacion x
       where x.consorcio_id = c and x.id = importacion
         and x.estado in ('confirmada', 'reemplazada');
  end
  $$;

-- Los datos oficiales de una liquidación oficial, con la designación del padrón de cada unidad.
create function nucleo.campos_liquidacion_oficial_lectura(importacion uuid)
  returns table (orden integer, tipo text, clave text, valor jsonb, unidad_id uuid,
                 designacion text)
  language plpgsql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
  begin
    perform nucleo.exigir_ver_panel(c, plataforma.persona_actual());
    return query
      select f.orden, f.tipo, f.clave, f.valor, f.unidad_id, u.designacion
        from nucleo.campos_oficiales_de(c, importacion) f
        left join nucleo.unidad u on u.consorcio_id = c and u.id = f.unidad_id
       order by f.orden;
  end
  $$;

-- Cada propietario: los períodos con liquidación vigente, el más nuevo primero.
create function nucleo.periodos_liquidacion_oficial()
  returns table (periodo date, confirmada_en timestamptz)
  language plpgsql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
  begin
    perform nucleo.exigir_propietario_confirmado(c, plataforma.persona_actual());
    return query
      select x.periodo, x.decidida_en from nucleo.importacion_liquidacion x
       where x.consorcio_id = c and x.estado = 'confirmada'
       order by x.periodo desc;
  end
  $$;

-- Cada propietario, el resumen de la liquidación vigente de un período: el gasto por rubro
-- (número, nombre y total) y los totales (por grupo, el total y los vencimientos). Sin gastos,
-- diferencias ni unidades. Una fila sin tipo si la liquidación no tiene ninguno de esos datos;
-- ninguna si el período no tiene liquidación vigente.
create function nucleo.resumen_liquidacion_oficial(periodo_pedido date)
  returns table (periodo date, confirmada_en timestamptz, tipo text, valor jsonb)
  language plpgsql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    v uuid;
  begin
    perform nucleo.exigir_propietario_confirmado(c, plataforma.persona_actual());
    v := nucleo.liquidacion_vigente(c, periodo_pedido);
    return query
      select x.periodo, x.decidida_en, f.tipo,
             case when f.tipo = 'rubro' then
                    jsonb_build_object('numero', f.valor->'numero', 'nombre', f.valor->'nombre',
                                       'total', f.valor->'total')
                  when f.tipo = 'total' then
                    jsonb_build_object('grupos', f.valor->'grupos', 'total', f.valor->'total',
                                       'vencimientos', f.valor->'vencimientos')
             end
        from nucleo.importacion_liquidacion x
        left join lateral (
          select o.orden, o.tipo, o.valor from nucleo.campos_oficiales_de(c, x.id) o
           where o.tipo = 'rubro' or o.clave = 'total:gastos') f on true
       where x.consorcio_id = c and x.id = v
       order by f.orden;
  end
  $$;

-- Cada propietario, sus unidades en la liquidación vigente de un período: el saldo y el importe
-- de cada unidad donde es propietario, por la unidad oficial. Una unidad propia que no figura
-- vuelve con una fila sin tipo. Ninguna fila si el período no tiene liquidación vigente.
create function nucleo.mi_cuenta_liquidacion_oficial(periodo_pedido date)
  returns table (periodo date, confirmada_en timestamptz, vencimientos jsonb, unidad_id uuid,
                 designacion text, tipo text, valor jsonb)
  language plpgsql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    propias uuid[];
    v uuid;
  begin
    propias := nucleo.exigir_propietario_confirmado(c, plataforma.persona_actual());
    v := nucleo.liquidacion_vigente(c, periodo_pedido);
    return query
      with oficiales as (select * from nucleo.campos_oficiales_de(c, v))
      select x.periodo, x.decidida_en,
             (select o.valor->'vencimientos' from oficiales o where o.clave = 'total:gastos'),
             u.id, u.designacion, f.tipo, f.valor
        from nucleo.importacion_liquidacion x
        join nucleo.unidad u on u.consorcio_id = c and u.id = any(propias)
        left join oficiales f
          on f.unidad_id = u.id and f.tipo in ('saldo_unidad', 'importe_unidad')
       where x.consorcio_id = c and x.id = v
       order by u.designacion, f.tipo;
  end
  $$;
"""

LECTURAS_0115 = (
    "nucleo.importaciones_liquidacion_lectura(uuid, text, uuid)",
    "nucleo.importacion_liquidacion_lectura(uuid, uuid, text, uuid)",
)
CONFIRMAR_0115 = "nucleo.confirmar_importacion_liquidacion(uuid, uuid, text, uuid)"
RUTAS = (
    *LECTURAS_0115,
    "nucleo.confirmar_importacion_liquidacion(uuid, uuid, text, uuid, text, uuid)",
    "nucleo.liquidaciones_oficiales_lectura()",
    "nucleo.liquidacion_oficial_lectura(uuid)",
    "nucleo.campos_liquidacion_oficial_lectura(uuid)",
    "nucleo.periodos_liquidacion_oficial()",
    "nucleo.resumen_liquidacion_oficial(date)",
    "nucleo.mi_cuenta_liquidacion_oficial(date)",
)
INTERNAS = (
    "nucleo.campos_oficiales_de(uuid, uuid)",
    "nucleo.exigir_propietario_confirmado(uuid, uuid)",
    "nucleo.liquidacion_vigente(uuid, date)",
)

GUARDA_BAJADA = """
do $guarda$
begin
  if exists (select 1 from nucleo.importacion_liquidacion
              where estado = 'reemplazada' or reemplaza_a is not null) then
    raise exception '0119 no baja: hay liquidaciones reemplazadas'
      using errcode = 'P0001', hint = 'downgrade_con_datos';
  end if;
end
$guarda$;
"""


def _proyeccion(campos: str) -> str:
    return f"update auditoria.proyeccion set campos = {campos} where accion = '{ACCION_CONFIRMAR}'"


def upgrade() -> None:
    op.execute(GUARDA_SUBIDA)
    op.execute(TABLAS)
    for funcion in (*LECTURAS_0115, CONFIRMAR_0115):
        op.execute(f"drop function {funcion}")
    op.execute(OFICIALES)
    op.execute(CONFIRMAR)
    op.execute(LECTURAS)
    for funcion in INTERNAS + RUTAS:
        op.execute(f"revoke all on function {funcion} from public")
    for funcion in RUTAS:
        op.execute(f"grant execute on function {funcion} to cem_app")
    op.execute(_proyeccion(CAMPOS_AUDITORIA_AHORA))


def downgrade() -> None:
    op.execute(GUARDA_BAJADA)
    op.execute(_proyeccion(CAMPOS_AUDITORIA_ANTES))
    for funcion in reversed(RUTAS):
        op.execute(f"drop function {funcion}")
    for funcion in LECTURAS_0115:
        nombre = funcion.split("(")[0]
        op.execute(_de("0115_confirmacion_liquidacion.py", "FUNCIONES", nombre))
    op.execute(_de("0115_confirmacion_liquidacion.py", "FUNCIONES", CONFIRMAR_0115.split("(")[0]))
    for funcion in (*LECTURAS_0115, CONFIRMAR_0115):
        op.execute(f"revoke all on function {funcion} from public")
        op.execute(f"grant execute on function {funcion} to cem_app")
    op.execute(
        _de("0115_confirmacion_liquidacion.py", "OFICIALES_0115",
            "nucleo.campos_oficiales_liquidacion")
    )  # fmt: skip
    for funcion in reversed(INTERNAS):
        op.execute(f"drop function {funcion}")
    op.execute(
        _de("0115_confirmacion_liquidacion.py", "TABLAS",
            "nucleo.negar_cambio_importacion_liquidacion")
    )  # fmt: skip
    op.execute(
        "drop index nucleo.importacion_oficial_por_periodo;"
        " alter table nucleo.importacion_liquidacion"
        " drop constraint importacion_decidida_con_quien,"
        " add constraint importacion_decidida_con_quien"
        " check ((estado in ('confirmada', 'descartada'))"
        " = (decidida_por is not null and decidida_en is not null)),"
        " drop constraint importacion_reemplaza_existente,"
        " drop constraint importacion_reemplaza_una_vez,"
        " drop constraint importacion_reemplazo_de_otra,"
        " drop constraint importacion_reemplazo_confirmada,"
        " drop constraint importacion_reemplazo_con_motivo,"
        " drop column motivo_reemplazo, drop column reemplaza_a,"
        " drop constraint importacion_liquidacion_estado_check,"
        f" add constraint importacion_liquidacion_estado_check check (estado in {ESTADOS_ANTES})"
    )
