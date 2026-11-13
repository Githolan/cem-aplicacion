"""Bandeja honesta (informe de fricción, tanda 2; decisiones del propietario del 12-11-2026).

- **Rechazo visto, por persona** (decisión 3). `nucleo.rechazo_visto` guarda quién vio el aviso
  de una orden rechazada y cuándo: una fila por orden y persona, que no se cambia ni se borra. La
  escribe solo `nucleo.marcar_rechazo_visto` (con `egresos.preparar_orden`, sin el gesto: no mueve
  dinero; solo una orden en Rechazada; repetirla no hace nada). `nucleo.rechazos_vistos_lectura`
  dice cuáles vio quien pide. RLS habilitada y forzada; `cem_app` no toca la tabla.
- **Borrador que nadie envió** (decisión 4, cambia la del 09-11): un borrador lo retira
  cualquiera con `egresos.preparar_orden`; en EnRevision sigue retirándola solo quien la preparó.
  Las dos, solo mientras nadie aprobó la versión vigente, y quedan quién y cuándo (`0093`).
- **Cerrar sin pagar** (decisión 12). `nucleo.orden_pago` suma `cerrada_sin_pago_por`,
  `cerrada_sin_pago_en` y `motivo_cierre_sin_pago`, que van juntas y solo en Fallida (el estado
  no cambia: sigue siendo «No se pagó»). `nucleo.cerrar_orden_sin_pago`, con
  `egresos.preparar_orden` y sin el gesto (no mueve dinero, como retirar), deja el evento
  `consorcio.orden_pago.cerrar_sin_pagar`. Una orden cerrada ya no cambia de estado: no se vuelve
  a pagar (el disparador `cerrada_sin_pago_no_cambia`). El motivo no admite una cuenta, como los
  demás (`0060`). `nucleo.cierre_sin_pago_lectura` dice quién la cerró, cuándo y por qué.
- **Cotejo de cada orden** (P-05). `nucleo.cotejo_de_ordenes_lectura`: cuándo se cotejó el pago
  conforme de cada orden, con cotejo vigente. La lee quien lee egresos (`exigir_lectura_egresos`):
  la ficha y la consulta lo muestran a todos.
- **Fecha de cada orden en la consulta** (decisión 5). `nucleo.pagos_consulta_lectura` suma
  `creada_en`.

No toca la separación de funciones, la regla de la cuenta ni la vigencia de 7 días.

Downgrade: se niega (`downgrade_con_datos`) si hay un rechazo visto o una orden cerrada sin pagar:
se perdería la constancia. Sin eso, todo vuelve a como estaba en `0095`.

Revisión: 0096
Anterior: 0095
"""

from alembic import op
from sqlalchemy import text

revision = "0096"
down_revision = "0095"
branch_labels = None
depends_on = None

MARCAR = "nucleo.marcar_rechazo_visto(uuid, uuid, text, uuid)"
VISTOS = "nucleo.rechazos_vistos_lectura(uuid, text, uuid)"
CERRAR = "nucleo.cerrar_orden_sin_pago(uuid, integer, text, uuid, text, uuid)"
CIERRE = "nucleo.cierre_sin_pago_lectura(uuid, text, uuid, text, uuid)"
COTEJOS = "nucleo.cotejo_de_ordenes_lectura(uuid, text, uuid, text, uuid)"
RUTAS = (MARCAR, VISTOS, CERRAR, CIERRE, COTEJOS)

RETIRAR = "nucleo.retirar_orden_pago(uuid, integer, uuid, text, uuid)"
SOLO_QUIEN_PREPARO = "    if not exists (select 1 from nucleo.orden_pago_version x\n"
BORRADOR_CUALQUIERA = (
    "    if o.estado <> 'borrador' and not exists (select 1 from nucleo.orden_pago_version x\n"
)

CONSULTA = "nucleo.pagos_consulta_lectura(text, text[], integer, integer, text, uuid, text, uuid)"
CONSULTA_CAMBIOS = (
    (
        " importe_pagado_centavos bigint)\n",
        " importe_pagado_centavos bigint, creada_en timestamptz)\n",
    ),
    (
        "                  then nucleo.centavos_de(f.importe_real) end\n        from pagina g\n",
        "                  then nucleo.centavos_de(f.importe_real) end,\n"
        "             o.creada_en\n        from pagina g\n",
    ),
)

UPGRADE = """
create table nucleo.rechazo_visto (
  consorcio_id  uuid not null references nucleo.consorcio (id),
  orden_id      uuid not null,
  persona_id    uuid not null references nucleo.persona (id),
  visto_en      timestamptz not null default clock_timestamp(),
  primary key (consorcio_id, orden_id, persona_id),
  foreign key (consorcio_id, orden_id) references nucleo.orden_pago (consorcio_id, id)
);
alter table nucleo.rechazo_visto enable row level security;
alter table nucleo.rechazo_visto force row level security;
create policy por_consorcio on nucleo.rechazo_visto for select
  using (consorcio_id = plataforma.consorcio_actual());
revoke all on nucleo.rechazo_visto from public, cem_app;
create trigger historia_rechazo_visto before update or delete on nucleo.rechazo_visto
  for each row execute function nucleo.negar_cambio_historia_orden_pago();
create trigger historia_rechazo_visto_truncate before truncate on nucleo.rechazo_visto
  for each statement execute function nucleo.negar_cambio_historia_orden_pago();

-- «Entendido»: quien la preparó vio que se rechazó (decisión 3). Repetirlo no cambia nada.
create function nucleo.marcar_rechazo_visto(orden uuid, actor uuid, origen_pedido text,
                                            sesion uuid)
  returns void
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    o nucleo.orden_pago;
  begin
    perform nucleo.exigir_acto_sin_gesto(c, actor, sesion, 'egresos.preparar_orden',
                                         origen_pedido);
    select * into o from nucleo.orden_pago x where x.consorcio_id = c and x.id = orden;
    if o.id is null then
      raise exception 'esa orden no existe en este consorcio' using errcode = 'P0001',
        hint = 'no_encontrado';
    end if;
    if o.estado <> 'rechazada' then
      raise exception 'solo se da por visto un rechazo' using errcode = 'P0001',
        hint = 'estado_invalido';
    end if;
    insert into nucleo.rechazo_visto (consorcio_id, orden_id, persona_id)
    values (c, orden, actor)
    on conflict do nothing;
  end
  $$;

create function nucleo.rechazos_vistos_lectura(actor uuid, origen_pedido text, sesion uuid)
  returns table (orden_id uuid, visto_en timestamptz)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
  begin
    perform nucleo.exigir_lectura_egresos(c, 'egresos.preparar_orden', actor, origen_pedido,
                                          sesion);
    return query
      select v.orden_id, v.visto_en from nucleo.rechazo_visto v
       where v.consorcio_id = c and v.persona_id = actor;
  end
  $$;

alter table nucleo.orden_pago
  add column cerrada_sin_pago_por uuid references nucleo.persona (id),
  add column cerrada_sin_pago_en timestamptz,
  -- Texto libre: no va a la auditoría.
  add column motivo_cierre_sin_pago text
    check (length(btrim(motivo_cierre_sin_pago)) between 1 and 300),
  add constraint orden_pago_cerrada_sin_pago_completa check (
    num_nonnulls(cerrada_sin_pago_por, cerrada_sin_pago_en, motivo_cierre_sin_pago) in (0, 3)
    and (cerrada_sin_pago_en is null or estado = 'fallida'));
create trigger motivo_cierre_sin_pago_sin_cuenta before update of motivo_cierre_sin_pago
  on nucleo.orden_pago
  for each row when (new.motivo_cierre_sin_pago is not null)
  execute function nucleo.negar_cuenta_en_motivo('motivo_cierre_sin_pago');

-- Cerrada sin pagar, la orden queda así: ni se vuelve a pagar ni se reabre.
create function nucleo.negar_cambio_cerrada_sin_pago()
  returns trigger
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  begin
    if old.cerrada_sin_pago_en is not null
       and (new.estado <> old.estado or new.version_vigente <> old.version_vigente
            or new.cerrada_sin_pago_en is distinct from old.cerrada_sin_pago_en
            or new.cerrada_sin_pago_por is distinct from old.cerrada_sin_pago_por
            or new.motivo_cierre_sin_pago is distinct from old.motivo_cierre_sin_pago) then
      raise exception 'la orden se cerró sin pagar' using errcode = 'P0001',
        hint = 'estado_invalido';
    end if;
    return new;
  end
  $$;
revoke all on function nucleo.negar_cambio_cerrada_sin_pago() from public;
create trigger cerrada_sin_pago_no_cambia before update on nucleo.orden_pago
  for each row execute function nucleo.negar_cambio_cerrada_sin_pago();

-- «No hace falta pagarla» (decisión 12): queda No se pagó, con quién, cuándo y por qué.
create function nucleo.cerrar_orden_sin_pago(orden uuid, version_esperada integer, motivo text,
                                             actor uuid, origen_pedido text, sesion uuid)
  returns void
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    o nucleo.orden_pago;
  begin
    perform nucleo.exigir_acto_sin_gesto(c, actor, sesion, 'egresos.preparar_orden',
                                         origen_pedido);
    o := nucleo.orden_pago_bloqueada(c, orden);
    if o.estado <> 'fallida' or o.cerrada_sin_pago_en is not null then
      raise exception 'solo se cierra sin pagar una orden que no se pagó' using errcode = 'P0001',
        hint = 'estado_invalido';
    end if;
    if version_esperada is distinct from o.version_vigente then
      raise exception 'la orden cambió mientras tanto' using errcode = 'P0001',
        hint = 'version_desactualizada';
    end if;
    update nucleo.orden_pago
       set cerrada_sin_pago_por = actor, cerrada_sin_pago_en = now(),
           motivo_cierre_sin_pago = btrim(motivo), actualizada_en = now()
     where id = orden;
    perform nucleo.auditar_orden_pago(c, 'consorcio.orden_pago.cerrar_sin_pagar', orden, actor,
      jsonb_build_object('estado', o.estado),
      jsonb_build_object('estado', o.estado, 'cerrada_sin_pago', true,
                         'version', o.version_vigente));
  end
  $$;

create function nucleo.cierre_sin_pago_lectura(orden uuid, facultad text, actor uuid,
                                               origen_pedido text, sesion uuid)
  returns table (orden_id uuid, cerrada_por_nombre text, cerrada_en timestamptz, motivo text)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
  begin
    perform nucleo.exigir_lectura_egresos(c, facultad, actor, origen_pedido, sesion);
    return query
      select o.id, p.nombre, o.cerrada_sin_pago_en, o.motivo_cierre_sin_pago
        from nucleo.orden_pago o
        join nucleo.persona p on p.id = o.cerrada_sin_pago_por
       where o.consorcio_id = c and o.cerrada_sin_pago_en is not null
         and (orden is null or o.id = orden);
  end
  $$;

-- Cuándo se cotejó el pago conforme de cada orden (P-05): solo lo dice, no da la cuenta.
create function nucleo.cotejo_de_ordenes_lectura(orden uuid, facultad text, actor uuid,
                                                 origen_pedido text, sesion uuid)
  returns table (orden_id uuid, cotejado_en timestamptz)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
  begin
    perform nucleo.exigir_lectura_egresos(c, facultad, actor, origen_pedido, sesion);
    return query
      select f.orden_id, max(t.cotejado_en)
        from nucleo.pagos_del_cotejo(c) f
        join nucleo.cotejo_pago t
          on t.consorcio_id = c and coalesce(t.pago_id, t.diferencia_id) = f.pago_id
         and t.anulado_en is null
       where f.conforme and (orden is null or f.orden_id = orden)
       group by f.orden_id;
  end
  $$;
"""

GUARDA_DOWNGRADE = """
lock table nucleo.orden_pago in access exclusive mode;
do $$
begin
  if exists (select 1 from nucleo.rechazo_visto)
     or exists (select 1 from nucleo.orden_pago where cerrada_sin_pago_en is not null) then
    raise exception '0096 no baja con rechazos vistos u órdenes cerradas sin pagar'
      using errcode = 'P0001', hint = 'downgrade_con_datos';
  end if;
end
$$;
"""

DOWNGRADE = f"""
drop function {COTEJOS};
drop function {CIERRE};
drop function {CERRAR};
drop trigger cerrada_sin_pago_no_cambia on nucleo.orden_pago;
drop function nucleo.negar_cambio_cerrada_sin_pago();
drop trigger motivo_cierre_sin_pago_sin_cuenta on nucleo.orden_pago;
alter table nucleo.orden_pago
  drop constraint orden_pago_cerrada_sin_pago_completa,
  drop column motivo_cierre_sin_pago,
  drop column cerrada_sin_pago_en,
  drop column cerrada_sin_pago_por;
drop function {VISTOS};
drop function {MARCAR};
drop table nucleo.rechazo_visto;
"""


def _definicion(firma: str) -> str:
    return (
        op.get_bind()
        .execute(text("select pg_get_functiondef(cast(:f as regprocedure))"), {"f": firma})
        .scalar_one()
    )


def _reemplazar(firma: str, antes: str, despues: str) -> None:
    vigente = _definicion(firma)
    if vigente.count(antes) != 1:
        raise RuntimeError(f"0096: {firma} no tiene una sola vez «{antes}»")
    op.execute(vigente.replace(antes, despues))


def _consulta(*, al_reves: bool = False) -> None:
    """Lo que devuelve cambia: se borra y se crea de nuevo, con su permiso."""
    definicion = _definicion(CONSULTA)
    for antes, despues in reversed(CONSULTA_CAMBIOS) if al_reves else CONSULTA_CAMBIOS:
        if al_reves:
            antes, despues = despues, antes
        if definicion.count(antes) != 1:
            raise RuntimeError(f"0096: {CONSULTA} no tiene exactamente una vez {antes!r}")
        definicion = definicion.replace(antes, despues)
    op.execute(f"drop function {CONSULTA}")
    op.execute(definicion)
    op.execute(f"revoke all on function {CONSULTA} from public")
    op.execute(f"grant execute on function {CONSULTA} to cem_app")


def upgrade() -> None:
    op.execute(UPGRADE)
    for funcion in RUTAS:
        op.execute(f"revoke all on function {funcion} from public")
        op.execute(f"grant execute on function {funcion} to cem_app")
    _reemplazar(RETIRAR, SOLO_QUIEN_PREPARO, BORRADOR_CUALQUIERA)
    _consulta()


def downgrade() -> None:
    op.execute(GUARDA_DOWNGRADE)
    _consulta(al_reves=True)
    _reemplazar(RETIRAR, BORRADOR_CUALQUIERA, SOLO_QUIEN_PREPARO)
    op.execute(DOWNGRADE)
