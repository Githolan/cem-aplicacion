"""Respuestas guardadas del encabezado `Idempotency-Key`.

Documento 13, §7.4 ("Cualquier `POST` que crea dinero, cargos o expedientes": encabezado
`Idempotency-Key` por identidad con respuesta almacenada 24 horas en PostgreSQL), §8.1
(`plataforma.idempotencia`), §8.5 (RLS forzada) y §11.1 (`Idempotency-Key` obligatorio).

Una fila por consorcio, persona, método, ruta y clave, con la huella SHA-256 del pedido y la
respuesta tal como salió (tipo `json`, que conserva el texto). La fila se escribe en la misma
transacción que el efecto: o quedan los dos o ninguno, y nadie ve nunca una fila a medias.

`cem_app` lee, agrega y borra solo las filas de la persona y el consorcio de su ámbito; sin
`UPDATE`: una respuesta guardada no se reescribe. La purga de las vencidas cruza consorcios
y la hace `purgar_idempotencia()`, `security definer` de `cem_migrate`, como la de la cola.

Revisión: 0003
Anterior: 0002
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

UPGRADE = r"""
create table plataforma.idempotencia (
  consorcio_id  uuid not null references nucleo.consorcio (id),
  persona_id    uuid not null references nucleo.persona (id),
  metodo        text not null check (metodo in ('POST', 'PUT', 'PATCH', 'DELETE')),
  ruta          text not null check (ruta like '/%'),
  -- 255: el mismo tope que la API valida en el encabezado (LARGO_MAXIMO_CLAVE).
  clave         text not null check (length(clave) between 1 and 255),
  -- SHA-256 de la consulta y el cuerpo del pedido original.
  huella        bytea not null check (length(huella) = 32),
  estado_http   smallint not null check (estado_http between 200 and 299),
  respuesta     json not null,
  creado_en     timestamptz not null default now(),
  vence_en      timestamptz not null,
  check (vence_en > creado_en),
  primary key (consorcio_id, persona_id, metodo, ruta, clave)
);
create index idempotencia_vencimiento on plataforma.idempotencia (vence_en);

-- Seguridad por fila (§8.5): la clave es de la identidad (§7.4) dentro del consorcio.
alter table plataforma.idempotencia enable row level security;
alter table plataforma.idempotencia force row level security;
create policy propia_en_el_consorcio on plataforma.idempotencia
  using (consorcio_id = plataforma.consorcio_actual()
         and persona_id = plataforma.persona_actual())
  with check (consorcio_id = plataforma.consorcio_actual()
              and persona_id = plataforma.persona_actual());

-- Borra las respuestas vencidas de todos los consorcios. Devuelve cuántas borró.
create function plataforma.purgar_idempotencia()
  returns bigint
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    borradas bigint;
  begin
    delete from plataforma.idempotencia where vence_en <= now();
    get diagnostics borradas = row_count;
    return borradas;
  end
  $$;

-- Privilegios (§8.6) ------------------------------------------------------------------

grant select, insert, delete on plataforma.idempotencia to cem_app;
revoke all on function plataforma.purgar_idempotencia() from public;
grant execute on function plataforma.purgar_idempotencia() to cem_app;
"""

DOWNGRADE = """
drop function if exists plataforma.purgar_idempotencia();
drop table if exists plataforma.idempotencia;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
