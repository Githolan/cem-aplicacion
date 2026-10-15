"""Identidad: segundo factor TOTP, códigos de recuperación y verificación reciente.

Documento 13: §10.3 (contraseña + TOTP para roles con facultades de dinero), TB-CEM-3 (las
acciones privilegiadas piden un segundo factor verificado en los últimos 10 minutos) y §15.2.

- `nucleo.factor_totp`: una fila por persona. El secreto TOTP se tiene que poder leer para
  verificar un código, así que no puede ser un hash: queda cifrado con AES-256-GCM con una
  clave derivada de `CEM_CODIGO_CLAVE`, que vive solo en el entorno de la API. Un volcado de la
  base sola no sirve para generar códigos. `ultimo_paso` es el último intervalo de 30 segundos
  aceptado: un código no se puede usar dos veces (RFC 6238, §5.2).
- `nucleo.codigo_recuperacion`: diez por persona, de un solo uso, guardados como HMAC-SHA256
  con otra clave derivada de la misma.
- `nucleo.sesion.segundo_factor_en`: cuándo se verificó el segundo factor en esa sesión. La
  verificación es por sesión: otra sesión de la misma persona no la hereda.

Todo es de la persona: RLS forzada por `persona_actual()`, sin funciones `security definer`.

Revisión: 0008
Anterior: 0007
"""

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

UPGRADE = r"""
create table nucleo.factor_totp (
  persona_id       uuid primary key references nucleo.persona (id),
  -- nonce (12 bytes) || AES-256-GCM(secreto de 20 bytes) con etiqueta (16 bytes).
  secreto_cifrado  bytea not null check (length(secreto_cifrado) = 48),
  creado_en        timestamptz not null default now(),
  -- Nulo mientras el alta espera el primer código correcto.
  confirmado_en    timestamptz,
  -- Último intervalo de 30 s aceptado (época Unix / 30): no se acepta uno igual o anterior.
  ultimo_paso      bigint not null default 0 check (ultimo_paso >= 0),
  check (confirmado_en is null or confirmado_en >= creado_en)
);

create table nucleo.codigo_recuperacion (
  id           uuid primary key default plataforma.uuid_v7(),
  persona_id   uuid not null references nucleo.persona (id),
  -- HMAC-SHA256 con una clave de la API de la persona y el código (nunca el código).
  codigo_hash  bytea not null check (length(codigo_hash) = 32),
  creado_en    timestamptz not null default now(),
  usado_en     timestamptz,
  unique (persona_id, codigo_hash)
);
create index codigo_recuperacion_persona on nucleo.codigo_recuperacion (persona_id);

alter table nucleo.sesion add column segundo_factor_en timestamptz
  check (segundo_factor_en is null or segundo_factor_en >= creada_en);

alter table nucleo.factor_totp enable row level security;
alter table nucleo.factor_totp force row level security;
create policy propio on nucleo.factor_totp
  using (persona_id = plataforma.persona_actual())
  with check (persona_id = plataforma.persona_actual());

alter table nucleo.codigo_recuperacion enable row level security;
alter table nucleo.codigo_recuperacion force row level security;
create policy propios on nucleo.codigo_recuperacion
  using (persona_id = plataforma.persona_actual())
  with check (persona_id = plataforma.persona_actual());

-- Privilegios (§8.6) ------------------------------------------------------------------

grant select, insert, delete, update (secreto_cifrado, creado_en, confirmado_en, ultimo_paso)
  on nucleo.factor_totp to cem_app;
grant select, insert, delete, update (usado_en) on nucleo.codigo_recuperacion to cem_app;
grant update (segundo_factor_en) on nucleo.sesion to cem_app;
"""

DOWNGRADE = """
revoke update (segundo_factor_en) on nucleo.sesion from cem_app;
alter table nucleo.sesion drop column if exists segundo_factor_en;
drop table if exists nucleo.codigo_recuperacion, nucleo.factor_totp;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
