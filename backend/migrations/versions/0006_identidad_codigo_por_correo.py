"""Identidad: código de seis dígitos por correo para entrar desde otro dispositivo.

Documento 13, §10.3: seis dígitos, 10 minutos, cinco intentos, para quien no puede abrir
enlaces o lee el correo en otro dispositivo que el que pidió la entrada (el enlace queda ligado
por el `nonce` al navegador que lo pidió y en otro no sirve).

Un pedido de entrada (`POST /api/v1/auth/enlace`) registra el enlace y, en la misma
transacción, su código: una fila de `nucleo.codigo_acceso` por cada `nucleo.enlace_acceso`.
El "uso" es uno solo y vive en `enlace_acceso.usado_en`: canjear el enlace o el código consume
el pedido entero, así una entrada no deja abierta una segunda credencial.

- Solo vale el código del último pedido de la persona: pedir otro deja muertos los anteriores
  (con varios vigentes, cada intento tendría varias chances de acertar).
- Cinco intentos fallidos agotan el código, aunque después llegue el correcto. El enlace del
  mismo pedido sigue sirviendo: está ligado al navegador que lo pidió y quien agota el código
  desde otro lado no lo tiene.
- En la base queda `HMAC-SHA256(clave del servidor, correo || código)`: seis dígitos son 10⁶
  valores y un SHA-256 sin clave se invierte probándolos todos en menos de un segundo. La
  clave (`CEM_CODIGO_CLAVE`) vive solo en el entorno de la API, nunca en la base ni en sus
  copias: con un volcado de la base sola no se puede probar ningún código.
- La comparación es en tiempo constante: recorre siempre los 32 bytes y acumula la diferencia.
- Los códigos se borran con su enlace (`on delete cascade`): la purga diaria de la 0005
  (`nucleo.purgar_identidad`) los limpia sin cambiar.

`cem_app` no tiene ningún privilegio sobre `nucleo.codigo_acceso`: la tocan solo las dos
funciones `security definer` de abajo, que reciben hashes y devuelven lo justo.

Revisión: 0006
Anterior: 0005
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

UPGRADE = r"""
create table nucleo.codigo_acceso (
  id           uuid primary key default plataforma.uuid_v7(),
  -- El pedido al que pertenece: el mismo que el enlace. Uno por pedido.
  enlace_id    uuid not null unique
               references nucleo.enlace_acceso (id) on delete cascade,
  persona_id   uuid not null references nucleo.persona (id),
  -- HMAC-SHA256 con la clave de la API del correo y el código (nunca el código).
  codigo_hash  bytea not null check (length(codigo_hash) = 32),
  -- Intentos fallidos. Al quinto (INTENTOS_CODIGO) queda agotado.
  intentos     smallint not null default 0 check (intentos between 0 and 5),
  creado_en    timestamptz not null default now(),
  vence_en     timestamptz not null,
  check (vence_en > creado_en),
  -- 10 minutos como máximo (VIGENCIA_CODIGO): la base lo impone aunque la API se equivoque.
  check (vence_en - creado_en <= interval '10 minutes')
);
-- El último código de la persona es el único que vale.
create index codigo_acceso_persona on nucleo.codigo_acceso (persona_id, creado_en desc, id desc);

alter table nucleo.codigo_acceso enable row level security;
alter table nucleo.codigo_acceso force row level security;

-- Agrega el código al enlace recién pedido (por el hash de su token), en la misma transacción
-- que `nucleo.pedir_enlace`. Sin ese enlace es un error de programación: falla.
create function nucleo.registrar_codigo(token bytea, codigo bytea, vigencia interval)
  returns void
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    e nucleo.enlace_acceso;
  begin
    select * into e from nucleo.enlace_acceso where token_hash = token;
    if not found then
      raise exception 'registrar_codigo: no existe el enlace';
    end if;
    insert into nucleo.codigo_acceso (enlace_id, persona_id, codigo_hash, vence_en)
    values (e.id, e.persona_id, codigo, e.creado_en + vigencia);
  end
  $$;

-- Canjea un código y crea la sesión en la misma transacción. `resultado` es 'ok' o el motivo
-- del rechazo (para los registros; la API responde igual a todos). Un intento fallido suma uno
-- y la API confirma la transacción aunque responda 401: el descuento no se deshace.
create function nucleo.canjear_codigo(
  correo_pedido text, codigo bytea, sesion_token bytea, dispositivo_pedido uuid,
  agente_pedido text, ip_pedido inet, vigencia interval, maximo_intentos integer)
  returns table (resultado text, persona uuid, sesion uuid, vence timestamptz)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    p uuid;
    c nucleo.codigo_acceso;
    e nucleo.enlace_acceso;
    diferencia integer := 0;
  begin
    if length(codigo) is distinct from 32 then
      raise exception 'canjear_codigo: el hash del código debe tener 32 bytes';
    end if;
    select x.id into p from nucleo.persona x
     where x.correo = lower(btrim(correo_pedido)) and x.eliminado_en is null;
    if p is null then
      return query select 'sin_cuenta'::text, null::uuid, null::uuid, null::timestamptz;
      return;
    end if;
    -- El último código de la persona, bloqueado: dos intentos simultáneos no se pisan el
    -- contador.
    select * into c from nucleo.codigo_acceso x
     where x.persona_id = p order by x.creado_en desc, x.id desc limit 1 for update;
    if not found then
      return query select 'sin_codigo'::text, null::uuid, null::uuid, null::timestamptz;
      return;
    end if;
    select * into e from nucleo.enlace_acceso where id = c.enlace_id for update;
    if e.usado_en is not null then
      return query select 'usado'::text, null::uuid, null::uuid, null::timestamptz;
      return;
    end if;
    if c.vence_en <= now() then
      return query select 'vencido'::text, null::uuid, null::uuid, null::timestamptz;
      return;
    end if;
    if c.intentos >= maximo_intentos then
      return query select 'agotado'::text, null::uuid, null::uuid, null::timestamptz;
      return;
    end if;
    -- Tiempo constante: siempre los 32 bytes.
    for i in 0..31 loop
      diferencia := diferencia | (get_byte(c.codigo_hash, i) # get_byte(codigo, i));
    end loop;
    if diferencia <> 0 then
      update nucleo.codigo_acceso set intentos = intentos + 1 where id = c.id;
      return query select 'incorrecto'::text, null::uuid, null::uuid, null::timestamptz;
      return;
    end if;
    update nucleo.enlace_acceso set usado_en = now() where id = e.id;
    return query
    insert into nucleo.sesion as s (persona_id, token_hash, dispositivo, agente, ip, vence_en)
    values (p, sesion_token, dispositivo_pedido, agente_pedido, ip_pedido, now() + vigencia)
    returning 'ok'::text, s.persona_id, s.id, s.vence_en;
  end
  $$;

-- Privilegios (§8.6) ------------------------------------------------------------------

revoke all on nucleo.codigo_acceso from cem_app;
revoke all on function
  nucleo.registrar_codigo(bytea, bytea, interval),
  nucleo.canjear_codigo(text, bytea, bytea, uuid, text, inet, interval, integer)
  from public;
grant execute on function
  nucleo.registrar_codigo(bytea, bytea, interval),
  nucleo.canjear_codigo(text, bytea, bytea, uuid, text, inet, interval, integer)
  to cem_app;
"""

DOWNGRADE = """
drop function if exists
  nucleo.registrar_codigo(bytea, bytea, interval),
  nucleo.canjear_codigo(text, bytea, bytea, uuid, text, inet, interval, integer);
drop table if exists nucleo.codigo_acceso;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
