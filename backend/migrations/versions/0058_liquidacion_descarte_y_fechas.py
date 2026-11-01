"""Liquidación: descartar un borrador, período acotado y gasto pagado hasta el fin del período.

Revisión crítica de los P3 129 y 130 (mandato del propietario del 31-10-2026: no dejarlos por
inercia si tocan cálculo, historia o publicación):

- **P3 129.** `nucleo.liquidacion` solo agregaba filas, así que un borrador no salía nunca de
  `borrador`: con el índice de un solo borrador por período, cada período tenía para siempre una
  sola liquidación (la versión 2 y la publicada eran inalcanzables), y un borrador abierto por
  error quedaba vivo. Ahora `descartar_liquidacion` quita sus gastos activos (la baja de siempre,
  que deja la historia) y lo pasa a `descartada` con quién, cuándo y por qué. Esa es la única
  modificación que admite la tabla: el trigger niega cualquier otra, y la fila conserva todo lo
  demás. Además el período no puede ser posterior al mes que viene (en la fecha del consorcio).
- **P3 130.** Un gasto entraba en cualquier período, aunque la orden se hubiera pagado después
  del último día. Ahora la fecha del pago tiene que ser a lo sumo el último día del período
  (`pagada_despues_del_periodo`). Con pago manual es la fecha de la operación del pago conciliado
  `conforme` (`pago_manual.fecha_operacion`), no la de la conciliación: un pago del 30 conciliado
  el 2 del mes siguiente va en el período del 30. Con el riel, `registrado_en` en la zona del
  consorcio. Sin fecha, no entra. Una orden pagada antes y olvidada sí entra en un
  período posterior. Decisión técnica por defecto, reversible.

Las funciones de `0057` se reescriben sobre su texto (`pg_get_functiondef`), como `0056`.
"""

from alembic import op
from sqlalchemy import text

revision = "0058"
down_revision = "0057"
branch_labels = None
depends_on = None

PREPARAR = "nucleo.preparar_liquidacion(date, uuid, text, uuid)"
AGREGAR = "nucleo.agregar_gasto_liquidacion(uuid, uuid, text, uuid, text, uuid)"
DESCARTAR = "nucleo.descartar_liquidacion(uuid, text, uuid, text, uuid)"

PREPARAR_ANTES = (
    "    perform pg_advisory_xact_lock(\n"
    "      hashtextextended('liquidacion:' || c::text || ':' || periodo_pedido::text, 0));\n"
)
PREPARAR_DESPUES = (
    "    if periodo_pedido > (date_trunc('month', nucleo.hoy_del_consorcio(c))\n"
    "                         + interval '1 month')::date then\n"
    "      raise exception 'el período no puede ser posterior al mes que viene'\n"
    "        using errcode = 'P0001', hint = 'periodo_invalido';\n"
    "    end if;\n" + PREPARAR_ANTES
)

AGREGAR_ANTES = (
    "        hint = 'orden_no_pagada';\n"
    "    end if;\n"
    "    if exists (select 1 from nucleo.liquidacion_gasto g\n"
    "                where g.consorcio_id = c and g.orden_id = o.id\n"
)
AGREGAR_DESPUES = (
    "        hint = 'orden_no_pagada';\n"
    "    end if;\n"
    "    if (select case when r.origen = 'manual' then\n"
    "                 (select p.fecha_operacion from nucleo.pago_manual p\n"
    "                    join nucleo.conciliacion_pago_manual k\n"
    "                      on k.pago_id = p.id and k.resultado = 'conforme'\n"
    "                   where p.consorcio_id = c and p.ejecucion_id = e.id)\n"
    "               else (r.registrado_en at time zone x.zona_horaria)::date end\n"
    "          from nucleo.resultado_ejecucion_orden_pago r\n"
    "          join nucleo.consorcio x on x.id = c\n"
    "         where r.ejecucion_id = e.id and r.resultado = 'pagada')\n"
    "       <= (l.periodo + interval '1 month' - interval '1 day')::date is not true then\n"
    "      raise exception 'la orden se pagó después del último día del período'\n"
    "        using errcode = 'P0001', hint = 'pagada_despues_del_periodo';\n"
    "    end if;\n"
    "    if exists (select 1 from nucleo.liquidacion_gasto g\n"
    "                where g.consorcio_id = c and g.orden_id = o.id\n"
)

UPGRADE = f"""
lock table nucleo.liquidacion in access exclusive mode;

alter table nucleo.liquidacion
  add column descartada_en       timestamptz,
  add column descartada_por      uuid references nucleo.persona (id),
  add column identidad_descarto  uuid,
  add column motivo_descarte     text check (length(btrim(motivo_descarte)) between 1 and 500),
  add foreign key (consorcio_id, identidad_descarto)
    references nucleo.identidad_humana (consorcio_id, id),
  drop constraint liquidacion_estado_check,
  add constraint liquidacion_estado_check
    check (estado in ('borrador', 'publicada', 'descartada')),
  add constraint liquidacion_descarte_completo check (
    (estado = 'descartada') = (descartada_en is not null and descartada_por is not null
                               and identidad_descarto is not null
                               and motivo_descarte is not null)
    and (estado = 'descartada' or (descartada_en is null and descartada_por is null
                                   and identidad_descarto is null and motivo_descarte is null)));

-- La única modificación de una liquidación: de borrador a descartada, sin tocar lo demás.
create function nucleo.liquidacion_solo_se_descarta()
  returns trigger
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  begin
    -- Toda columna que no sea del descarte queda igual, también las que se agreguen después.
    if tg_op = 'UPDATE' and old.estado = 'borrador' and new.estado = 'descartada'
       and to_jsonb(new) - array['estado', 'descartada_en', 'descartada_por',
                                 'identidad_descarto', 'motivo_descarte']
           = to_jsonb(old) - array['estado', 'descartada_en', 'descartada_por',
                                   'identidad_descarto', 'motivo_descarte'] then
      return new;
    end if;
    raise exception 'la historia de una liquidación solo se agrega'
      using errcode = 'P0001', hint = 'historia_inmutable';
  end
  $$;
revoke all on function nucleo.liquidacion_solo_se_descarta() from public;

drop trigger historia_liquidacion on nucleo.liquidacion;
create trigger historia_liquidacion before update or delete on nucleo.liquidacion
  for each row execute function nucleo.liquidacion_solo_se_descarta();

-- Descartar un borrador: sus gastos activos se quitan (queda la baja) y el período se libera
-- para un borrador nuevo, versión siguiente.
create function {DESCARTAR.replace("(uuid, text, uuid, text, uuid)", "")}(
  liquidacion uuid, motivo text, actor uuid, origen_pedido text, sesion uuid)
  returns uuid
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    identidad uuid;
    l nucleo.liquidacion;
    quitados integer;
  begin
    identidad := nucleo.exigir_acto_economico(c, actor, sesion, 'liquidacion.preparar',
                                              origen_pedido);
    if motivo is null or length(btrim(motivo)) not between 1 and 500 then
      raise exception 'falta el motivo' using errcode = 'P0001', hint = 'motivo_invalido';
    end if;
    select * into l from nucleo.liquidacion x
     where x.consorcio_id = c and x.id = liquidacion for update;
    if not found then
      raise exception 'la liquidación no existe' using errcode = 'P0001', hint = 'no_encontrado';
    end if;
    if l.estado <> 'borrador' then
      raise exception 'la liquidación no es un borrador' using errcode = 'P0001',
        hint = 'liquidacion_no_admite';
    end if;
    insert into nucleo.liquidacion_gasto_quitado (consorcio_id, gasto_id, quitado_por,
                                                  identidad_quito)
    select c, g.id, actor, identidad from nucleo.liquidacion_gasto g
     where g.consorcio_id = c and g.liquidacion_id = l.id
       and not exists (select 1 from nucleo.liquidacion_gasto_quitado q where q.gasto_id = g.id);
    get diagnostics quitados = row_count;
    update nucleo.liquidacion x
       set estado = 'descartada', descartada_en = now(), descartada_por = actor,
           identidad_descarto = identidad, motivo_descarte = btrim(motivo)
     where x.consorcio_id = c and x.id = l.id;
    perform nucleo.auditar_liquidacion(c, 'consorcio.liquidacion.descartar', l.id, actor,
      jsonb_build_object('periodo', l.periodo, 'version', l.version,
                         'gastos_quitados', quitados));
    return l.id;
  end
  $$;
revoke all on function {DESCARTAR} from public;
grant execute on function {DESCARTAR} to cem_app;
"""

DOWNGRADE = f"""
lock table nucleo.liquidacion in access exclusive mode;
do $$
begin
  if exists (select 1 from nucleo.liquidacion where estado = 'descartada') then
    raise exception 'hay liquidaciones descartadas: bajar 0058 reinterpretaría la historia'
      using errcode = 'P0001', hint = 'downgrade_con_datos';
  end if;
end
$$;
drop function {DESCARTAR};
drop trigger historia_liquidacion on nucleo.liquidacion;
create trigger historia_liquidacion before update or delete on nucleo.liquidacion
  for each row execute function nucleo.negar_cambio_historia_liquidacion();
drop function nucleo.liquidacion_solo_se_descarta();
alter table nucleo.liquidacion
  drop constraint liquidacion_descarte_completo,
  drop constraint liquidacion_estado_check,
  add constraint liquidacion_estado_check check (estado in ('borrador', 'publicada')),
  drop column motivo_descarte,
  drop column identidad_descarto,
  drop column descartada_por,
  drop column descartada_en;
"""


def _reemplazar(funcion: str, antes: str, despues: str) -> None:
    definicion = (
        op.get_bind()
        .execute(text("select pg_get_functiondef(cast(:f as regprocedure))"), {"f": funcion})
        .scalar_one()
    )
    if definicion.count(antes) != 1:
        raise RuntimeError(f"0058: {funcion} no tiene exactamente una vez {antes!r}")
    op.execute(definicion.replace(antes, despues))


def upgrade() -> None:
    op.execute(UPGRADE)
    _reemplazar(PREPARAR, PREPARAR_ANTES, PREPARAR_DESPUES)
    _reemplazar(AGREGAR, AGREGAR_ANTES, AGREGAR_DESPUES)


def downgrade() -> None:
    op.execute(DOWNGRADE)
    _reemplazar(AGREGAR, AGREGAR_DESPUES, AGREGAR_ANTES)
    _reemplazar(PREPARAR, PREPARAR_DESPUES, PREPARAR_ANTES)
