"""Identidad: llave de acceso (WebAuthn) como medio de verificación.

Diseño `doc/diseno/Llave_de_Acceso_Diseno.md` del repositorio CEM, aprobado por el propietario el
04-11-2026. La llave es otro medio de escribir `nucleo.sesion.segundo_factor_en`: las reglas de
dinero (R10) no cambian.

- `nucleo.llave_acceso`: una fila por llave. Guarda la clave pública, nunca un secreto. Se da de
  baja borrando, como `nucleo.factor_totp`.
- `nucleo.desafio_llave`: el desafío vigente de cada sesión. De un solo uso, vence a los 5 minutos
  y se borra con la sesión. No es secreto: lo que prueba es la firma.
- `nucleo.tiene_segundo_factor` ahora también ve las llaves: quien solo tiene llave pasa por la
  misma reautenticación de privacidad (`0039`, `0042`) que quien tiene TOTP.
- `nucleo.tiene_totp_activo` conserva el criterio anterior para la entrada con contraseña (`0009`):
  ese camino pide un código, y una persona con llave y sin TOTP no tiene código que dar.
- La supresión de una cuenta (`0040`) también borra sus llaves.

Revisión: 0070
Anterior: 0069
"""

from alembic import op
from sqlalchemy import text

revision = "0070"
down_revision = "0069"
branch_labels = None
depends_on = None

ENTRAR = "nucleo.entrar_con_contrasena(uuid, text, bytea, uuid, text, inet, interval)"
DESAFIO = "nucleo.pedir_desafio_contrasena(uuid, text, bytea, interval)"
SUPRESION = "plataforma.privacidad_supresion(plataforma.caso_privacidad, boolean)"

LLAMADA_ANTES = "nucleo.tiene_segundo_factor(persona_pedida)"
LLAMADA_DESPUES = "nucleo.tiene_totp_activo(persona_pedida)"
CAMBIOS_SUPRESION = (
    (
        "      perform 1 from nucleo.factor_totp x where x.persona_id = k.persona_id for update;\n",
        "      perform 1 from nucleo.factor_totp x where x.persona_id = k.persona_id for update;\n"
        "      perform 1 from nucleo.llave_acceso x where x.persona_id = k.persona_id\n"
        "       order by x.id for update;\n",
    ),
    (
        "             + (select count(*) from nucleo.factor_totp x"
        " where x.persona_id = k.persona_id)\n",
        "             + (select count(*) from nucleo.factor_totp x"
        " where x.persona_id = k.persona_id)\n"
        "             + (select count(*) from nucleo.llave_acceso x"
        " where x.persona_id = k.persona_id)\n",
    ),
    (
        "          delete from nucleo.factor_totp where persona_id = k.persona_id;\n",
        "          delete from nucleo.factor_totp where persona_id = k.persona_id;\n"
        "          delete from nucleo.llave_acceso where persona_id = k.persona_id;\n",
    ),
)

UPGRADE = r"""
create table nucleo.llave_acceso (
  id             uuid primary key default plataforma.uuid_v7(),
  persona_id     uuid not null references nucleo.persona (id),
  credencial_id  bytea not null unique check (length(credencial_id) between 16 and 1023),
  clave_publica  bytea not null check (length(clave_publica) between 1 and 1024),
  -- Cuántas veces dice el dispositivo que se usó. Muchos informan siempre 0.
  contador       bigint not null default 0 check (contador >= 0),
  nombre         text not null check (length(btrim(nombre)) between 1 and 60),
  creada_en      timestamptz not null default now(),
  ultimo_uso_en  timestamptz
);
create index llave_acceso_persona on nucleo.llave_acceso (persona_id);

create table nucleo.desafio_llave (
  sesion_id   uuid primary key references nucleo.sesion (id) on delete cascade,
  persona_id  uuid not null references nucleo.persona (id),
  proposito   text not null check (proposito in ('alta', 'verificar')),
  desafio     bytea not null check (length(desafio) = 32),
  vence_en    timestamptz not null
);

alter table nucleo.llave_acceso enable row level security;
alter table nucleo.llave_acceso force row level security;
create policy propia on nucleo.llave_acceso
  using (persona_id = plataforma.persona_actual())
  with check (persona_id = plataforma.persona_actual());

alter table nucleo.desafio_llave enable row level security;
alter table nucleo.desafio_llave force row level security;
create policy propio on nucleo.desafio_llave
  using (persona_id = plataforma.persona_actual())
  with check (persona_id = plataforma.persona_actual());

grant select, insert, delete, update (contador, ultimo_uso_en)
  on nucleo.llave_acceso to cem_app;
grant select, insert, delete on nucleo.desafio_llave to cem_app;

create function nucleo.tiene_totp_activo(persona_pedida uuid)
  returns boolean
  language sql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
    select exists (select 1 from nucleo.factor_totp f
                    where f.persona_id = persona_pedida and f.confirmado_en is not null)
  $$;
revoke all on function nucleo.tiene_totp_activo(uuid) from public;

create or replace function nucleo.tiene_segundo_factor(persona_pedida uuid)
  returns boolean
  language sql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
    select nucleo.tiene_totp_activo(persona_pedida)
        or exists (select 1 from nucleo.llave_acceso l where l.persona_id = persona_pedida)
  $$;
"""

DOWNGRADE = r"""
create or replace function nucleo.tiene_segundo_factor(persona_pedida uuid)
  returns boolean
  language sql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
    select exists (select 1 from nucleo.factor_totp f
                    where f.persona_id = persona_pedida and f.confirmado_en is not null)
  $$;
drop function nucleo.tiene_totp_activo(uuid);
drop table nucleo.desafio_llave, nucleo.llave_acceso;
"""


def _definicion(firma: str) -> str:
    return (
        op.get_bind()
        .execute(text("select pg_get_functiondef(cast(:f as regprocedure))"), {"f": firma})
        .scalar_one()
    )


def _cambiar(firma: str, cambios: tuple[tuple[str, str], ...]) -> None:
    """Reemplaza cada texto de una función vigente; si no está una sola vez, la definición no es
    la esperada y la migración se detiene."""
    definicion = _definicion(firma)
    for antes, despues in cambios:
        if definicion.count(antes) != 1:
            raise RuntimeError(f"0070: {firma} no es la esperada: {antes!r}")
        definicion = definicion.replace(antes, despues)
    op.execute(definicion)


def upgrade() -> None:
    op.execute(UPGRADE)
    _cambiar(ENTRAR, ((LLAMADA_ANTES, LLAMADA_DESPUES),))
    _cambiar(DESAFIO, ((LLAMADA_ANTES, LLAMADA_DESPUES),))
    _cambiar(SUPRESION, CAMBIOS_SUPRESION)


def downgrade() -> None:
    _cambiar(SUPRESION, tuple((despues, antes) for antes, despues in CAMBIOS_SUPRESION))
    _cambiar(DESAFIO, ((LLAMADA_DESPUES, LLAMADA_ANTES),))
    _cambiar(ENTRAR, ((LLAMADA_DESPUES, LLAMADA_ANTES),))
    op.execute(DOWNGRADE)
