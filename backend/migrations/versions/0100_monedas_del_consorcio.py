"""Las monedas en que opera cada edificio (informe de fricción, P-14; sí del propietario del
14-11-2026): el formulario de la orden de pago muestra «Moneda» solo en los edificios que operan en
más de una, y la base no acepta una orden en una moneda que el edificio no tiene habilitada.

**Las monedas** (`nucleo.moneda_consorcio`). El peso (`MONEDA_BASE`) rige siempre en todo edificio
y no tiene fila: no hay forma de que un edificio quede sin él. Cada otra moneda habilitada es una
fila con el edificio, la moneda, cuándo y quién la habilitó (`origen`: el equipo, `soporte`, o esta
migración, `migracion`). Solo se agrega: no se modifica ni se borra (los triggers de la historia de
la orden). Hoy solo se habilita el dólar (`MONEDAS_HABILITABLES`); deshabilitar no existe.

**Quién la habilita**: el equipo, con `nucleo.habilitar_moneda_consorcio`
(`scripts/moneda_consorcio.py`), como el umbral de `0050`: corre como `cem_app` sin persona, con
el edificio en el ámbito y `origen_pedido = 'soporte'`. Como el resto de la clase B (S2b, `0043`),
soporte no escribe evento en `auditoria.eventos`: la constancia es la fila, con su origen y su
fecha, que nadie puede reescribir.

**Lo que ya existe no se rompe.** Al subir, todo edificio que ya tiene una orden de pago (alguna
versión), un umbral o un contrato en una moneda que no es el peso queda con esa moneda habilitada
(`origen = 'migracion'`). Los demás operan solo en pesos.

**La barrera** (`nucleo.exigir_moneda_habilitada`, trigger `before insert` sobre
`nucleo.orden_pago_version`): toda versión nueva de una orden (preparar, modificar, renovar) en una
moneda que el edificio no tiene habilitada se niega (`moneda_no_habilitada`). Es la segunda barrera:
la API la traduce a un 422 con el mismo código.

**La lectura** (`nucleo.monedas_consorcio`): el peso y las habilitadas, en orden. `cem_app` lee la
tabla con la RLS (el edificio del ámbito o los de la persona, `consorcios_persona`, para
`/me/consorcios`).

**Downgrade** se niega (`downgrade_con_datos`) si el equipo habilitó alguna moneda: esa fila no se
puede reconstruir. Las de la migración se recalculan al volver a subir.

Revisión: 0100
Anterior: 0099
"""

from alembic import op

revision = "0100"
down_revision = "0099"
branch_labels = None
depends_on = None

MONEDA_BASE = "ARS"
"""La moneda en que opera todo edificio, sin fila: la de `MONEDA_SUGERIDA` del contrato."""
MONEDAS_HABILITABLES = ("USD",)
"""Las que el equipo puede habilitar hoy (P-14: pesos y dólares)."""

TABLA = "nucleo.moneda_consorcio"
MONEDAS = "nucleo.monedas_consorcio(uuid)"
HABILITAR = "nucleo.habilitar_moneda_consorcio(text, uuid, text)"
EXIGIR = "nucleo.exigir_moneda_habilitada()"

_habilitables = ", ".join(f"'{m}'" for m in MONEDAS_HABILITABLES)

TABLA_SQL = f"""
-- Nadie escribe órdenes, umbrales ni contratos entre el relleno y la barrera.
lock table nucleo.orden_pago_version, nucleo.umbral_orden_pago, nucleo.contrato_proveedor
  in share row exclusive mode;

create table {TABLA} (
  consorcio_id   uuid not null references nucleo.consorcio (id),
  moneda         text not null check (moneda ~ '^[A-Z]{{3}}$' and moneda <> '{MONEDA_BASE}'),
  -- Quién la habilitó: el equipo (`soporte`, sin persona) o la migración que la dedujo de lo que
  -- el edificio ya tenía (`migracion`).
  origen         text not null check (origen in ('soporte', 'migracion')),
  habilitada_en  timestamptz not null default now(),
  primary key (consorcio_id, moneda)
);
alter table {TABLA} enable row level security;
alter table {TABLA} force row level security;
create policy por_consorcio on {TABLA} for select
  using (consorcio_id = plataforma.consorcio_actual());
create policy por_consorcios_persona on {TABLA} for select
  using (consorcio_id = any (plataforma.consorcios_persona()));
revoke all on {TABLA} from public;
grant select on {TABLA} to cem_app;

-- Solo se agregan filas, como la historia de la orden.
create trigger historia_moneda_consorcio before update or delete on {TABLA}
  for each row execute function nucleo.negar_cambio_historia_orden_pago();
create trigger historia_moneda_consorcio_truncate before truncate on {TABLA}
  for each statement execute function nucleo.negar_cambio_historia_orden_pago();
"""

RELLENO = f"""
-- Lo que el edificio ya usa sigue andando: toda moneda que no es el peso en una versión de una
-- orden, un umbral o un contrato queda habilitada.
insert into {TABLA} (consorcio_id, moneda, origen)
select distinct consorcio_id, moneda, 'migracion'
  from (select consorcio_id, moneda from nucleo.orden_pago_version
        union select consorcio_id, moneda from nucleo.umbral_orden_pago
        union select consorcio_id, moneda from nucleo.contrato_proveedor) usadas
 where moneda <> '{MONEDA_BASE}';
"""

FUNCIONES = f"""
-- El peso primero y después las habilitadas, en orden alfabético.
create function {MONEDAS}
  returns text[]
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select array['{MONEDA_BASE}'] || coalesce(
      (select array_agg(m.moneda order by m.moneda) from {TABLA} m where m.consorcio_id = $1),
      '{{}}')
  $$;

create function {EXIGIR}
  returns trigger
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  begin
    if new.moneda <> all (nucleo.monedas_consorcio(new.consorcio_id)) then
      raise exception 'el edificio no opera en %', new.moneda using errcode = 'P0001',
        hint = 'moneda_no_habilitada';
    end if;
    return new;
  end
  $$;
create trigger moneda_habilitada before insert on nucleo.orden_pago_version
  for each row execute function {EXIGIR};

create function nucleo.habilitar_moneda_consorcio(moneda_nueva text, actor uuid,
                                                  origen_pedido text)
  returns timestamptz
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    cuando timestamptz;
  begin
    if c is null then
      raise exception 'sin consorcio en el ámbito' using errcode = '42501';
    end if;
    -- La habilita solo el equipo (P-14): ninguna persona del consorcio la cambia.
    if origen_pedido is distinct from 'soporte' then
      raise exception 'las monedas del edificio las habilita solo el equipo (soporte)'
        using errcode = 'P0001', hint = 'origen_invalido';
    end if;
    if plataforma.persona_actual() is not null or actor is not null then
      raise exception 'el origen soporte no admite persona en la sesión ni actor'
        using errcode = 'P0001', hint = 'origen_invalido';
    end if;
    if moneda_nueva is null or moneda_nueva not in ({_habilitables}) then
      raise exception 'solo se habilita %', array[{_habilitables}] using errcode = 'P0001',
        hint = 'moneda_invalida';
    end if;
    if exists (select 1 from nucleo.moneda_consorcio m
                where m.consorcio_id = c and m.moneda = moneda_nueva) then
      raise exception 'el edificio ya opera en %', moneda_nueva using errcode = 'P0001',
        hint = 'moneda_ya_habilitada';
    end if;
    insert into nucleo.moneda_consorcio (consorcio_id, moneda, origen)
    values (c, moneda_nueva, 'soporte')
    returning habilitada_en into cuando;
    return cuando;
  end
  $$;

revoke all on function {MONEDAS} from public;
revoke all on function {EXIGIR} from public;
revoke all on function {HABILITAR} from public;
grant execute on function {MONEDAS} to cem_app;
-- La carga de soporte corre como `cem_app` sin persona (como `fijar_umbral_orden_pago`).
grant execute on function {HABILITAR} to cem_app;
"""

DOWNGRADE = f"""
-- Hasta el fin del downgrade nadie habilita: la guarda vale para lo que se borra.
lock table {TABLA} in access exclusive mode;
do $$
begin
  if exists (select 1 from {TABLA} where origen = 'soporte') then
    raise exception '0100 no baja con monedas que habilitó el equipo: se perdería la constancia'
      using errcode = 'P0001', hint = 'downgrade_con_datos';
  end if;
end
$$;
drop function {HABILITAR};
drop trigger moneda_habilitada on nucleo.orden_pago_version;
drop function {EXIGIR};
drop function {MONEDAS};
drop table {TABLA};
"""


def upgrade() -> None:
    op.execute(TABLA_SQL)
    op.execute(RELLENO)
    op.execute(FUNCIONES)


def downgrade() -> None:
    op.execute(DOWNGRADE)
