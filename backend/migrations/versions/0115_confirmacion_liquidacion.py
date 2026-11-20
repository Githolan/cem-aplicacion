"""Confirmación campo por campo de la liquidación importada, H5-3 (diseño H5 §5; RN30, PA32, D2-A).

Quien importa (`liquidacion.importar`) lee cada campo con su valor, su confianza y el renglón del
documento de donde salió, y lo confirma, lo corrige o lo descarta. Cuando no queda ninguno
pendiente, confirma la importación entera; desde ese momento sus campos confirmados y corregidos son
lo único que una lectura oficial puede usar (`campos_oficiales_liquidacion`, de `0113`). Una
importación que no sirve se descarta con su motivo. Nada se borra.

**El renglón como texto** (`campo_preliminar.fragmento`): la lectura guarda, con cada campo, el
texto del renglón donde lo encontró, para que quien confirma lo compare sin abrir el PDF. Las
lecturas anteriores a `0115` lo tienen vacío.

**Las decisiones de un campo** (`resolver_campo_preliminar`): confirmar, corregir (con el valor
corregido, de las mismas claves que el leído) o descartar. Mientras la importación está
`para_confirmar`, una decisión se puede cambiar; cada una queda en la auditoría. El valor leído no
cambia nunca.

**La importación** (`confirmar_importacion_liquidacion`, `descartar_importacion_liquidacion`): se
confirma solo `para_confirmar` y sin campos pendientes (`campos_pendientes`); se descarta
`para_confirmar` o `no_reconocida`, con motivo. Las dos quedan con quién y cuándo, y no vuelven.

Downgrade: se niega (`downgrade_con_datos`) si hay importaciones decididas o campos con decisión.

Revisión: 0115
Anterior: 0114
"""

import importlib.util
from pathlib import Path

from alembic import op

revision = "0115"
down_revision = "0114"
branch_labels = None
depends_on = None

FACULTAD = "liquidacion.importar"
TRABAJO = "liquidacion.leer"
ACCIONES = (
    ("consorcio.liquidacion.decidir_dato", "Decidió sobre un dato leído de la liquidación",
     "Dato de la liquidación importada", ("clave", "decision")),
    ("consorcio.liquidacion.confirmar_importada", "Confirmó la liquidación importada",
     "Liquidación importada", ("campos",)),
    ("consorcio.liquidacion.descartar_importada", "Descartó la liquidación importada",
     "Liquidación importada", ("estado",)),
)  # fmt: skip
LARGO_FRAGMENTO = 500
LARGO_MOTIVO = 500


def _de_0113(firma: str) -> str:
    """El `create function` de `0113` que empieza con `firma`, tal como lo escribió (vuelve igual
    en el downgrade)."""
    ruta = Path(__file__).with_name("0113_importacion_liquidacion.py")
    spec = importlib.util.spec_from_file_location("migracion_0113", ruta)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    inicio = modulo.FUNCIONES.index(f"create function {firma}(")
    fin = modulo.FUNCIONES.index("  $$;", inicio) + len("  $$;")
    return modulo.FUNCIONES[inicio:fin]


GUARDAR = "nucleo.guardar_lectura_liquidacion"
_UNIDAD = (
    "             nucleo.unidad_de_la_lectura(c, e.campo->'valor'->>'depto',"
    " e.campo->'valor'->>'uf')\n"
)
GUARDAR_CAMBIOS = (
    ("       renglon, unidad_id)\n", "       renglon, unidad_id, fragmento)\n"),
    (_UNIDAD + "           end\n", _UNIDAD + "           end,\n           e.campo->>'fragmento'\n"),
)


def _guardar(*, al_reves: bool) -> str:
    definicion = _de_0113(GUARDAR).replace("create function", "create or replace function", 1)
    if al_reves:
        return definicion
    for antes, despues in GUARDAR_CAMBIOS:
        if definicion.count(antes) != 1:
            raise RuntimeError(f"0115: {GUARDAR} no tiene exactamente una vez {antes!r}")
        definicion = definicion.replace(antes, despues)
    return definicion


OFICIALES = "nucleo.campos_oficiales_liquidacion"
# RN30 con correcciones: si quien importa corrigió `uf` o `depto` de un campo de unidad, la unidad
# oficial sale del valor corregido (la de `unidad_id` es la del valor leído y queda como se leyó).
OFICIALES_0115 = """
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
    return query
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
         and x.estado = 'confirmada' and p.estado in ('confirmado', 'corregido')
       order by p.orden;
  end
  $$;
"""

TABLAS = f"""
alter table nucleo.campo_preliminar
  add column fragmento text check (length(fragmento) between 1 and {LARGO_FRAGMENTO});

alter table nucleo.importacion_liquidacion
  add column decidida_por uuid references nucleo.persona (id),
  add column decidida_en timestamptz,
  add column motivo_descarte text
    check (length(btrim(motivo_descarte)) between 1 and {LARGO_MOTIVO}),
  add constraint importacion_decidida_con_quien
    check ((estado in ('confirmada', 'descartada'))
           = (decidida_por is not null and decidida_en is not null)),
  add constraint importacion_descartada_con_motivo
    check ((estado = 'descartada') = (motivo_descarte is not null)),
  -- Una descartada sin haberse reconocido no tiene formato ni período.
  drop constraint importacion_leida_con_formato,
  add constraint importacion_leida_con_formato
    check (estado in ('leyendo', 'no_reconocida', 'descartada')
           or (formato is not null and periodo is not null));

-- La lectura: de `leyendo` a `para_confirmar` o `no_reconocida` (`0113`). La decisión: de
-- `para_confirmar` a `confirmada`, o de `para_confirmar` o `no_reconocida` a `descartada`, con
-- quién y cuándo. Lo demás no cambia y nada se borra.
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
         or (old.estado in ('para_confirmar', 'no_reconocida') and new.estado = 'descartada'))
       or (old.estado <> 'leyendo'
           and (new.formato, new.periodo, new.motivo, new.diferencias, new.leida_en)
               is distinct from
               (old.formato, old.periodo, old.motivo, old.diferencias, old.leida_en)) then
      raise exception 'ese paso no existe para una importación' using errcode = 'P0001',
        hint = 'historia_inmutable';
    end if;
    return new;
  end
  $$;

-- Un campo: solo su decisión (estado, valor corregido, quién y cuándo), mientras la importación
-- está `para_confirmar`. Lo leído no cambia y nada se borra.
create or replace function nucleo.negar_cambio_campo_preliminar()
  returns trigger
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  begin
    if tg_op <> 'UPDATE' then
      raise exception 'lo leído de una liquidación no se borra' using errcode = 'P0001',
        hint = 'historia_inmutable';
    end if;
    if (new.consorcio_id, new.id, new.importacion_id, new.orden, new.tipo, new.clave, new.valor,
        new.confianza, new.origen, new.pagina, new.renglon, new.unidad_id, new.fragmento)
       is distinct from
       (old.consorcio_id, old.id, old.importacion_id, old.orden, old.tipo, old.clave, old.valor,
        old.confianza, old.origen, old.pagina, old.renglon, old.unidad_id, old.fragmento)
       or new.estado = 'pendiente'
       or not exists (select 1 from nucleo.importacion_liquidacion x
                       where x.consorcio_id = new.consorcio_id and x.id = new.importacion_id
                         and x.estado = 'para_confirmar') then
      raise exception 'lo leído de una liquidación solo recibe su decisión mientras se confirma'
        using errcode = 'P0001', hint = 'historia_inmutable';
    end if;
    return new;
  end
  $$;
"""

FUNCIONES = f"""
-- Las importaciones del consorcio, la más nueva primero, para quien importa.
create function nucleo.importaciones_liquidacion_lectura(actor uuid, origen_pedido text,
                                                         sesion uuid)
  returns table (id uuid, documento_id uuid, version_documento_id uuid, subida_por uuid,
                 creada_en timestamptz, estado text, formato text, periodo date, motivo text,
                 diferencias integer, campos integer, pendientes integer, decidida_por uuid,
                 decidida_en timestamptz, motivo_descarte text)
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
             x.decidida_por, x.decidida_en, x.motivo_descarte
        from nucleo.importacion_liquidacion x
       where x.consorcio_id = c
       order by x.creada_en desc, x.id desc;
  end
  $$;

-- La importación para quien importa, con la decisión (reemplaza la de `0113`).
create function nucleo.importacion_liquidacion_lectura(importacion uuid, actor uuid,
                                                       origen_pedido text, sesion uuid)
  returns table (id uuid, documento_id uuid, version_documento_id uuid, subida_por uuid,
                 creada_en timestamptz, estado text, formato text, periodo date, motivo text,
                 diferencias jsonb, leida_en timestamptz, decidida_por uuid,
                 decidida_en timestamptz, motivo_descarte text)
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
             x.decidida_en, x.motivo_descarte
        from nucleo.importacion_liquidacion x
       where x.consorcio_id = c and x.id = importacion;
  end
  $$;

-- Los campos leídos, en orden, con el renglón como texto y quién decidió (reemplaza la de `0113`).
create function nucleo.campos_preliminares_lectura(importacion uuid, actor uuid,
                                                   origen_pedido text, sesion uuid)
  returns table (id uuid, orden integer, tipo text, clave text, valor jsonb, confianza numeric,
                 origen text, pagina integer, renglon numeric, unidad_id uuid, estado text,
                 valor_corregido jsonb, fragmento text, confirmado_por uuid,
                 confirmado_en timestamptz)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
  begin
    perform nucleo.exigir_acto_importacion(c, actor, sesion, origen_pedido);
    return query
      select p.id, p.orden, p.tipo, p.clave, p.valor, p.confianza, p.origen, p.pagina,
             p.renglon, p.unidad_id, p.estado, p.valor_corregido, p.fragmento, p.confirmado_por,
             p.confirmado_en
        from nucleo.campo_preliminar p
       where p.consorcio_id = c and p.importacion_id = importacion
       order by p.orden;
  end
  $$;

-- La importación de un campo, bloqueada, si está para confirmar; si no, el motivo.
create function nucleo.importacion_para_decidir(c uuid, importacion uuid)
  returns text
  language plpgsql volatile
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    actual text;
  begin
    select x.estado into actual from nucleo.importacion_liquidacion x
     where x.consorcio_id = c and x.id = importacion for update;
    if actual is null then
      raise exception 'importación inexistente' using errcode = 'P0001', hint = 'no_encontrado';
    end if;
    return actual;
  end
  $$;

-- Confirmar, corregir o descartar un campo. Corregido lleva un valor con las mismas claves que el
-- leído; los otros, ninguno. Se puede cambiar mientras la importación está para confirmar.
create function nucleo.resolver_campo_preliminar(importacion uuid, campo uuid, decision text,
                                                 corregido jsonb, actor uuid, origen_pedido text,
                                                 sesion uuid)
  returns void
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    p nucleo.campo_preliminar;
  begin
    perform nucleo.exigir_acto_importacion(c, actor, sesion, origen_pedido);
    -- Primero el bloqueo de la importación y después el campo: dos decisiones a la vez sobre el
    -- mismo campo se ordenan, y la segunda lee (y audita como `antes`) lo que dejó la primera.
    if nucleo.importacion_para_decidir(c, importacion) <> 'para_confirmar' then
      raise exception 'la importación ya no se confirma' using errcode = 'P0001',
        hint = 'importacion_decidida';
    end if;
    select x.* into p from nucleo.campo_preliminar x
     where x.consorcio_id = c and x.id = campo and x.importacion_id = importacion;
    if p.id is null then
      raise exception 'campo inexistente' using errcode = 'P0001', hint = 'no_encontrado';
    end if;
    if decision not in ('confirmado', 'corregido', 'descartado')
       or (decision = 'corregido') <> (corregido is not null) then
      raise exception 'decisión inválida' using errcode = 'P0001', hint = 'decision_invalida';
    end if;
    if decision = 'corregido' and (
         jsonb_typeof(corregido) <> 'object'
         or (select array_agg(k order by k) from jsonb_object_keys(corregido) k)
            is distinct from
            (select array_agg(k order by k) from jsonb_object_keys(p.valor) k)
         or corregido = p.valor) then
      raise exception 'el valor corregido no tiene las claves del leído o no cambia nada'
        using errcode = 'P0001', hint = 'correccion_invalida';
    end if;
    update nucleo.campo_preliminar
       set estado = decision, valor_corregido = corregido, confirmado_por = actor,
           confirmado_en = clock_timestamp()
     where consorcio_id = c and id = campo;
    insert into auditoria.eventos
      (consorcio_id, actor_persona_id, origen, accion, entidad, entidad_id, antes, despues,
       hash_previo, hash)
    values (c, actor, origen_pedido, 'consorcio.liquidacion.decidir_dato', 'campo_preliminar',
            campo, jsonb_build_object('estado', p.estado, 'valor_corregido', p.valor_corregido),
            jsonb_build_object('clave', p.clave, 'decision', decision, 'valor_corregido',
                               corregido),
            '', '');
  end
  $$;

-- Confirmar la importación entera: sin campos pendientes.
create function nucleo.confirmar_importacion_liquidacion(importacion uuid, actor uuid,
                                                         origen_pedido text, sesion uuid)
  returns void
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    n integer;
  begin
    perform nucleo.exigir_acto_importacion(c, actor, sesion, origen_pedido);
    if nucleo.importacion_para_decidir(c, importacion) <> 'para_confirmar' then
      raise exception 'la importación no está para confirmar' using errcode = 'P0001',
        hint = 'importacion_decidida';
    end if;
    if exists (select 1 from nucleo.campo_preliminar p
                where p.consorcio_id = c and p.importacion_id = importacion
                  and p.estado = 'pendiente') then
      raise exception 'quedan campos pendientes' using errcode = 'P0001',
        hint = 'campos_pendientes';
    end if;
    select count(*) into n from nucleo.campo_preliminar p
     where p.consorcio_id = c and p.importacion_id = importacion
       and p.estado in ('confirmado', 'corregido');
    update nucleo.importacion_liquidacion
       set estado = 'confirmada', decidida_por = actor, decidida_en = clock_timestamp()
     where consorcio_id = c and id = importacion;
    insert into auditoria.eventos
      (consorcio_id, actor_persona_id, origen, accion, entidad, entidad_id, despues,
       hash_previo, hash)
    values (c, actor, origen_pedido, 'consorcio.liquidacion.confirmar_importada',
            'importacion_liquidacion',
            importacion, jsonb_build_object('campos', n), '', '');
  end
  $$;

-- Descartar una importación que no sirve, con su motivo.
create function nucleo.descartar_importacion_liquidacion(importacion uuid, motivo_pedido text,
                                                         actor uuid, origen_pedido text,
                                                         sesion uuid)
  returns void
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
  begin
    perform nucleo.exigir_acto_importacion(c, actor, sesion, origen_pedido);
    if nucleo.importacion_para_decidir(c, importacion) not in ('para_confirmar', 'no_reconocida')
    then
      raise exception 'la importación no se puede descartar' using errcode = 'P0001',
        hint = 'importacion_decidida';
    end if;
    if motivo_pedido is null or length(btrim(motivo_pedido)) not between 1 and {LARGO_MOTIVO} then
      raise exception 'motivo inválido' using errcode = 'P0001', hint = 'motivo_invalido';
    end if;
    update nucleo.importacion_liquidacion
       set estado = 'descartada', decidida_por = actor, decidida_en = clock_timestamp(),
           motivo_descarte = btrim(motivo_pedido)
     where consorcio_id = c and id = importacion;
    insert into auditoria.eventos
      (consorcio_id, actor_persona_id, origen, accion, entidad, entidad_id, despues,
       hash_previo, hash)
    values (c, actor, origen_pedido, 'consorcio.liquidacion.descartar_importada',
            'importacion_liquidacion',
            importacion, jsonb_build_object('estado', 'descartada'), '', '');
  end
  $$;
"""

LECTURAS_0113 = (
    "nucleo.importacion_liquidacion_lectura(uuid, uuid, text, uuid)",
    "nucleo.campos_preliminares_lectura(uuid, uuid, text, uuid)",
)
RUTAS = (
    "nucleo.importaciones_liquidacion_lectura(uuid, text, uuid)",
    *LECTURAS_0113,
    "nucleo.resolver_campo_preliminar(uuid, uuid, text, jsonb, uuid, text, uuid)",
    "nucleo.confirmar_importacion_liquidacion(uuid, uuid, text, uuid)",
    "nucleo.descartar_importacion_liquidacion(uuid, text, uuid, text, uuid)",
)
INTERNAS = ("nucleo.importacion_para_decidir(uuid, uuid)",)

GUARDA = """
do $guarda$
begin
  if exists (select 1 from nucleo.importacion_liquidacion
              where estado in ('confirmada', 'descartada'))
     or exists (select 1 from nucleo.campo_preliminar where estado <> 'pendiente')
     or exists (select 1 from nucleo.campo_preliminar where fragmento is not null) then
    raise exception '0115 no baja: hay importaciones o campos decididos, o renglones guardados'
      using errcode = 'P0001', hint = 'downgrade_con_datos';
  end if;
end
$guarda$;
"""


def upgrade() -> None:
    op.execute(TABLAS)
    op.execute(_guardar(al_reves=False))
    for funcion in LECTURAS_0113:
        op.execute(f"drop function {funcion}")
    op.execute(FUNCIONES)
    op.execute(OFICIALES_0115)
    for funcion in INTERNAS + RUTAS:
        op.execute(f"revoke all on function {funcion} from public")
    for funcion in RUTAS:
        op.execute(f"grant execute on function {funcion} to cem_app")
    for accion, texto, recurso, campos in ACCIONES:
        lista = ", ".join(f"'{c}'" for c in campos)
        op.execute(
            "insert into auditoria.proyeccion (accion, texto, recurso, campos) values"
            f" ('{accion}', '{texto}', '{recurso}', array[{lista}])"
        )


def downgrade() -> None:
    op.execute(GUARDA)
    for accion, *_ in ACCIONES:
        op.execute(f"delete from auditoria.proyeccion where accion = '{accion}'")
    for funcion in reversed(RUTAS + INTERNAS):
        op.execute(f"drop function {funcion}")
    for firma in ("nucleo.importacion_liquidacion_lectura", "nucleo.campos_preliminares_lectura"):
        op.execute(_de_0113(firma))
    for funcion in LECTURAS_0113:
        op.execute(f"revoke all on function {funcion} from public")
        op.execute(f"grant execute on function {funcion} to cem_app")
    op.execute(_guardar(al_reves=True))
    op.execute(_de_0113(OFICIALES).replace("create function", "create or replace function", 1))
    op.execute(
        """
create or replace function nucleo.negar_cambio_campo_preliminar()
  returns trigger
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  begin
    raise exception 'lo leído de una liquidación solo se agrega' using errcode = 'P0001',
      hint = 'historia_inmutable';
  end
  $$;
"""
    )
    op.execute(
        """
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
"""
    )
    op.execute(
        "alter table nucleo.importacion_liquidacion"
        " drop constraint importacion_leida_con_formato,"
        " add constraint importacion_leida_con_formato check (estado in ('leyendo',"
        " 'no_reconocida') or (formato is not null and periodo is not null)),"
        " drop constraint importacion_descartada_con_motivo,"
        " drop constraint importacion_decidida_con_quien,"
        " drop column motivo_descarte, drop column decidida_en, drop column decidida_por;"
        " alter table nucleo.campo_preliminar drop column fragmento;"
    )
