"""Ejecución lógica del pago de una orden emitida (H3, mensaje del propietario del 30-10-2026).

Sin riel real: nada de esto mueve dinero. Deja hecho el flujo que un riel usará después: estados,
idempotencia, autorización y el resultado incierto. Ningún pedido se reintenta a ciegas.

**La ejecución** (`nucleo.ejecucion_orden_pago`): una fila por versión emitida, creada por
`emitir_orden_pago` en la misma transacción que pasa la orden a EnEjecucion. Guarda la foto de lo
que se paga (proveedor y su versión, cuenta destino, importe, moneda) y la `clave` que se le da al
riel como clave de idempotencia. `unique (orden_id, version)`: una versión se ejecuta una sola vez.
Si falla, la orden queda Fallida; para volver a pagar hay que modificarla (versión nueva), que
vuelve a revisión y a aprobarse. Nunca se reenvía la misma.

**Los resultados** (`nucleo.resultado_ejecucion_orden_pago`): solo se agregan.
- `pagada`: la orden pasa a Pagada;
- `fallida`: pasa a Fallida;
- `incierta`: el riel no contestó o no se sabe; la orden pasa a EnVerificacion y se consulta,
  nunca se vuelve a ordenar.
A lo sumo un resultado definitivo (`pagada` o `fallida`) por ejecución (índice único parcial):
un segundo resultado definitivo se rechaza con `resultado_repetido`.

**Nunca a la vez:** `pedido_de_pago` bloquea la ejecución (`for update`) antes de leerla, y el
trabajo la tiene bloqueada hasta confirmar, también mientras llama al riel. Verificar nunca
consulta en medio de una orden: si llega primero (ordenar todavía en la cola) y el riel no
conoce la clave, la orden queda Fallida y ordenar, al tomarla, ya no llama al riel.

**Quién los escribe:** solo un trabajo de `cem-trabajos` (`nucleo.exigir_trabajo`: con consorcio
y sin persona). Ninguna ruta de la API los llama; el origen auditado es `trabajos`.
"""

from alembic import op
from sqlalchemy import text

revision = "0051"
down_revision = "0050"
branch_labels = None
depends_on = None

RESULTADOS = ("pagada", "fallida", "incierta")
ORIGENES = ("ordenar", "consultar")
EMITIR = "nucleo.emitir_orden_pago(uuid, integer, uuid, text, uuid)"
TRABAJO_ORDENAR = "egresos.ordenar_pago"
TRABAJO_VERIFICAR = "egresos.verificar_pago"
ESPERA_VERIFICAR_MINUTOS = 15
"""Más que el plazo de un trabajo tomado (5 minutos): cuando se verifica, ordenar ya terminó o
murió, y nunca corren a la vez."""
INTENTOS_VERIFICAR = 3
"""Ante una falla técnica de la consulta; un resultado incierto encola la consulta siguiente."""

ANTES = (
    "    update nucleo.orden_pago set estado = 'en_ejecucion', actualizada_en = now()\n"
    "     where id = orden;\n"
)
DESPUES = ANTES + ("    perform nucleo.crear_ejecucion_orden_pago(c, orden, version_vista);\n")

_resultados = ", ".join(f"'{r}'" for r in RESULTADOS)
_origenes = ", ".join(f"'{o}'" for o in ORIGENES)

UPGRADE = f"""
create table nucleo.ejecucion_orden_pago (
  consorcio_id       uuid not null,
  id                 uuid not null default plataforma.uuid_v7(),
  orden_id           uuid not null,
  version            integer not null,
  -- La clave de idempotencia que recibe el riel: el mismo pedido nunca paga dos veces.
  clave              uuid not null default gen_random_uuid() unique,
  proveedor_id       uuid not null,
  proveedor_version  integer not null,
  cuenta_destino     text not null check (nucleo.cbu_valido(cuenta_destino)),
  importe_centavos   bigint not null check (importe_centavos > 0),
  moneda             text not null check (moneda ~ '^[A-Z]{{3}}$'),
  creada_en          timestamptz not null default now(),
  primary key (id),
  unique (consorcio_id, id),
  unique (orden_id, version),
  foreign key (orden_id, version) references nucleo.orden_pago_version (orden_id, numero),
  foreign key (proveedor_id, proveedor_version)
    references nucleo.proveedor_version (proveedor_id, numero),
  foreign key (consorcio_id, orden_id) references nucleo.orden_pago (consorcio_id, id)
);

create table nucleo.resultado_ejecucion_orden_pago (
  consorcio_id   uuid not null,
  id             uuid not null default plataforma.uuid_v7(),
  ejecucion_id   uuid not null,
  resultado      text not null check (resultado in ({_resultados})),
  origen         text not null check (origen in ({_origenes})),
  referencia     text check (length(btrim(referencia)) between 1 and 200),
  motivo         text check (length(btrim(motivo)) between 1 and 200),
  registrado_en  timestamptz not null default now(),
  primary key (id),
  foreign key (consorcio_id, ejecucion_id)
    references nucleo.ejecucion_orden_pago (consorcio_id, id)
);
create unique index resultado_definitivo_unico on nucleo.resultado_ejecucion_orden_pago
  (ejecucion_id) where resultado <> 'incierta';

alter table nucleo.ejecucion_orden_pago enable row level security;
alter table nucleo.ejecucion_orden_pago force row level security;
create policy por_consorcio on nucleo.ejecucion_orden_pago for select
  using (consorcio_id = plataforma.consorcio_actual());
alter table nucleo.resultado_ejecucion_orden_pago enable row level security;
alter table nucleo.resultado_ejecucion_orden_pago force row level security;
create policy por_consorcio on nucleo.resultado_ejecucion_orden_pago for select
  using (consorcio_id = plataforma.consorcio_actual());

create trigger historia_ejecucion before update or delete on nucleo.ejecucion_orden_pago
  for each row execute function nucleo.negar_cambio_historia_orden_pago();
create trigger historia_ejecucion_truncate before truncate on nucleo.ejecucion_orden_pago
  for each statement execute function nucleo.negar_cambio_historia_orden_pago();
create trigger historia_resultado before update or delete
  on nucleo.resultado_ejecucion_orden_pago
  for each row execute function nucleo.negar_cambio_historia_orden_pago();
create trigger historia_resultado_truncate before truncate
  on nucleo.resultado_ejecucion_orden_pago
  for each statement execute function nucleo.negar_cambio_historia_orden_pago();

-- La foto de lo que se paga, desde la versión emitida (0050 ya exigió proveedor sin cambios y
-- con cuenta), y en la misma transacción los dos trabajos: ordenar (un solo intento: nunca se
-- reenvía) y verificar después de que el plazo de ordenar haya vencido seguro.
create function nucleo.crear_ejecucion_orden_pago(c uuid, orden uuid, version_vista integer)
  returns void
  language plpgsql volatile
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    e uuid;
  begin
    insert into nucleo.ejecucion_orden_pago
      (consorcio_id, orden_id, version, proveedor_id, proveedor_version, cuenta_destino,
       importe_centavos, moneda)
    select c, orden, v.numero, v.proveedor_id, v.proveedor_version, pv.cuenta_destino,
           v.importe_centavos, v.moneda
      from nucleo.orden_pago_version v
      join nucleo.proveedor_version pv
        on pv.proveedor_id = v.proveedor_id and pv.numero = v.proveedor_version
     where v.consorcio_id = c and v.orden_id = orden and v.numero = version_vista
    returning id into e;
    if e is null then
      raise exception 'la versión emitida no tiene proveedor con cuenta' using errcode = 'P0001',
        hint = 'proveedor_sin_cuenta';
    end if;
    insert into plataforma.jobs
      (tipo, consorcio_id, carga, clave_unica, disponible_desde, maximo_intentos)
    values
      ('{TRABAJO_ORDENAR}', c, jsonb_build_object('ejecucion', e), '{TRABAJO_ORDENAR}:' || e,
       now(), 1),
      ('{TRABAJO_VERIFICAR}', c, jsonb_build_object('ejecucion', e, 'consulta', 1),
       '{TRABAJO_VERIFICAR}:' || e || ':' || 1,
       now() + interval '{ESPERA_VERIFICAR_MINUTOS} minutes',
       {INTENTOS_VERIFICAR});
  end
  $$;

create function nucleo.pedido_de_pago(ejecucion uuid)
  returns table (orden_id uuid, version integer, clave uuid, cuenta_destino text,
                 importe_centavos bigint, moneda text, estado_orden text,
                 resultado_definitivo text)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
  begin
    perform nucleo.exigir_trabajo(c);
    -- El bloqueo primero y la lectura después, en otra sentencia: así ve el resultado que
    -- confirmó quien tenía el bloqueo.
    perform 1 from nucleo.ejecucion_orden_pago x
     where x.consorcio_id = c and x.id = ejecucion for update;
    return query
      select e.orden_id, e.version, e.clave, e.cuenta_destino, e.importe_centavos, e.moneda,
             o.estado::text,
             (select r.resultado from nucleo.resultado_ejecucion_orden_pago r
               where r.ejecucion_id = e.id and r.resultado <> 'incierta')
        from nucleo.ejecucion_orden_pago e
        join nucleo.orden_pago o on o.id = e.orden_id
       where e.consorcio_id = c and e.id = ejecucion;
  end
  $$;

-- Registra lo que dijo el riel y mueve la orden. Devuelve el estado nuevo de la orden.
create function nucleo.registrar_resultado_pago(ejecucion uuid, resultado_nuevo text,
                                                origen_nuevo text, referencia_nueva text,
                                                motivo_nuevo text)
  returns text
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    e nucleo.ejecucion_orden_pago;
    o nucleo.orden_pago;
    hacia nucleo.estado_orden_pago;
  begin
    perform nucleo.exigir_trabajo(c);
    select * into e from nucleo.ejecucion_orden_pago x
     where x.consorcio_id = c and x.id = ejecucion;
    if not found then
      raise exception 'esa ejecución no existe en este consorcio' using errcode = 'P0001',
        hint = 'ejecucion_inexistente';
    end if;
    o := nucleo.orden_pago_bloqueada(c, e.orden_id);
    if exists (select 1 from nucleo.resultado_ejecucion_orden_pago r
                where r.ejecucion_id = ejecucion and r.resultado <> 'incierta') then
      raise exception 'la ejecución ya tiene un resultado definitivo' using errcode = 'P0001',
        hint = 'resultado_repetido';
    end if;
    if o.version_vigente <> e.version or o.estado not in ('en_ejecucion', 'en_verificacion') then
      raise exception 'la orden no está esperando el resultado de esta ejecución'
        using errcode = 'P0001', hint = 'estado_invalido';
    end if;
    insert into nucleo.resultado_ejecucion_orden_pago
      (consorcio_id, ejecucion_id, resultado, origen, referencia, motivo)
    values (c, ejecucion, resultado_nuevo, origen_nuevo, referencia_nueva, motivo_nuevo);
    hacia := case resultado_nuevo
               when 'pagada' then 'pagada'
               when 'fallida' then 'fallida'
               else 'en_verificacion'
             end;
    if hacia <> o.estado then
      update nucleo.orden_pago set estado = hacia, actualizada_en = now() where id = o.id;
      insert into auditoria.eventos
        (consorcio_id, actor_persona_id, origen, accion, entidad, entidad_id, antes, despues,
         hash_previo, hash)
      values (c, null, 'trabajos', 'consorcio.orden_pago.resultado', 'orden_pago', o.id,
              jsonb_build_object('estado', o.estado),
              jsonb_build_object('estado', hacia, 'version', e.version, 'resultado',
                                 resultado_nuevo, 'origen', origen_nuevo), '', '');
    end if;
    return hacia::text;
  end
  $$;

revoke all on function nucleo.crear_ejecucion_orden_pago(uuid, uuid, integer) from public;
revoke all on function nucleo.pedido_de_pago(uuid) from public;
revoke all on function nucleo.registrar_resultado_pago(uuid, text, text, text, text) from public;
grant execute on function nucleo.pedido_de_pago(uuid) to cem_app;
grant execute on function nucleo.registrar_resultado_pago(uuid, text, text, text, text)
  to cem_app;
"""

GUARDA = """
lock table nucleo.orden_pago in share row exclusive mode;

do $$
begin
  if exists (select 1 from nucleo.orden_pago
              where estado not in ('borrador', 'en_revision', 'rechazada', 'aprobada')) then
    raise exception '0051 no sube con órdenes ya emitidas: no tienen su ejecución'
      using errcode = 'P0001', hint = 'upgrade_con_datos';
  end if;
end
$$;
"""

DOWNGRADE_GUARDA = """
lock table nucleo.orden_pago in share row exclusive mode;

do $$
begin
  if exists (select 1 from nucleo.ejecucion_orden_pago) then
    raise exception '0051 no baja con ejecuciones: se perdería su historia'
      using errcode = 'P0001', hint = 'downgrade_con_datos';
  end if;
end
$$;
"""

DOWNGRADE_FINAL = """
drop function nucleo.registrar_resultado_pago(uuid, text, text, text, text);
drop function nucleo.pedido_de_pago(uuid);
drop function nucleo.crear_ejecucion_orden_pago(uuid, uuid, integer);
drop table nucleo.resultado_ejecucion_orden_pago;
drop table nucleo.ejecucion_orden_pago;
"""


def _emitir(antes: str, despues: str) -> None:
    definicion = (
        op.get_bind()
        .execute(text("select pg_get_functiondef(cast(:f as regprocedure))"), {"f": EMITIR})
        .scalar_one()
    )
    if definicion.count(antes) != 1:
        raise RuntimeError(f"0051: {EMITIR} no tiene exactamente una vez {antes!r}")
    op.execute(definicion.replace(antes, despues))


def upgrade() -> None:
    op.execute(GUARDA)
    op.execute(UPGRADE)
    _emitir(ANTES, DESPUES)


def downgrade() -> None:
    op.execute(DOWNGRADE_GUARDA)
    _emitir(DESPUES, ANTES)
    op.execute(DOWNGRADE_FINAL)
