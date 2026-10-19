"""Base de prueba real con las políticas de seguridad por fila activas (documento 13, §8.5).

Las pruebas marcadas `db` crean una base descartable en el servidor de
`CEM_TEST_ADMIN_URL` (un superusuario local), crean los roles con
`scripts/crear_roles.sql`, aplican las migraciones como `cem_migrate` y consultan como
`cem_app`. Si el servidor no responde, fallan: no se saltean en silencio.
Las marcadas `redis` usan el Redis de `CEM_TEST_REDIS_URL` y borran solo las claves `cem:rl:*`.
Para correr solo las pruebas sin servicios: `pytest -m "not db and not redis"`.
"""

import os
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from redis import Redis
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url

RAIZ = Path(__file__).resolve().parent.parent
ADMIN_URL = os.environ.get(
    "CEM_TEST_ADMIN_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/postgres"
)
REDIS_URL = os.environ.get("CEM_TEST_REDIS_URL", "redis://localhost:6379/0")
BASE = "cem_prueba"
CLAVES = {
    "migrate": "migrate_prueba",
    "app": "app_prueba",
    "ro": "ro_prueba",
    "backup": "backup_prueba",
}


def url_rol(rol: str) -> str:
    return (
        make_url(ADMIN_URL)
        .set(username=f"cem_{rol}", password=CLAVES[rol], database=BASE)
        .render_as_string(hide_password=False)
    )


def alembic_config() -> AlembicConfig:
    cfg = AlembicConfig(str(RAIZ / "alembic.ini"))
    cfg.set_main_option("script_location", str(RAIZ / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url_rol("migrate").replace("%", "%%"))
    return cfg


@pytest.fixture(scope="session")
def base_de_prueba() -> None:
    admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f"drop database if exists {BASE} with (force)"))
    admin.dispose()
    libpq = make_url(ADMIN_URL).set(drivername="postgresql").render_as_string(False)
    subprocess.run(
        [
            "psql", libpq, "-q",
            "-v", f"base={BASE}",
            "-v", f"clave_migrate={CLAVES['migrate']}",
            "-v", f"clave_app={CLAVES['app']}",
            "-v", f"clave_ro={CLAVES['ro']}",
            "-v", f"clave_backup={CLAVES['backup']}",
            "-f", str(RAIZ / "scripts" / "crear_roles.sql"),
        ],
        check=True,
    )  # fmt: skip
    # Ida, vuelta e ida: la migración se puede revertir y volver a aplicar.
    cfg = alembic_config()
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")


def vaciar_mandatos(motor_migrate: Engine) -> None:
    """Borra los mandatos de la base **descartable** de la sesión antes de bajar migraciones.

    El downgrade de 0012 se niega si existe cualquier mandato (`downgrade_con_datos`), y está
    bien que así sea en una base real. Las pruebas que bajan la cadena por debajo de 0012 llaman
    a esto primero; `cem_migrate` es el dueño de las tablas.

    Lo mismo con 0014, que se niega a bajar si hay un vínculo confirmado y, al volver a subir,
    si algún vínculo no tiene su evento de aceptación (`vinculo_sin_procedencia`). Los datos de
    prueba cargan vínculos a mano: aquí se les da una invitación y un evento de aceptación
    sintéticos (correo `.invalid`), solo en esta base.
    """
    with motor_migrate.begin() as c:
        c.execute(text("delete from nucleo.facultad_otorgada"))
        c.execute(text("delete from nucleo.mandato"))
        c.execute(
            text(
                "update nucleo.vinculo set confirmado_por = null, confirmado_en = null,"
                " confirmacion_referencia = null where confirmado_en is not null"
            )
        )
    dar_procedencia_sintetica(motor_migrate)
    # Ya con su evento sintético, la procedencia de esos vínculos es la invitación: así el
    # downgrade de 0014 no se niega por vínculos de otra fuente (si la base está en 0014).
    with motor_migrate.begin() as c:
        c.execute(
            text(
                "do $$ begin"
                " if exists (select 1 from information_schema.columns"
                "            where table_schema = 'nucleo' and table_name = 'vinculo'"
                "              and column_name = 'fuente') then"
                "   update nucleo.vinculo set fuente = 'invitacion', origen = 'soporte',"
                "          registrado_por = null where fuente <> 'invitacion';"
                " end if; end $$"
            )
        )


def dar_procedencia_sintetica(motor_migrate: Engine) -> None:
    """Invitación aceptada y evento de aceptación sintéticos (correo `.invalid`) para cada
    vínculo de la base **descartable** que no tiene su evento, como los que cargan a mano los
    datos de prueba. Sin eso, el upgrade a 0014 se niega (`vinculo_sin_procedencia`, criterio
    9), como debe hacerlo en una base real. Sirve desde 0007, que trae las invitaciones.
    """
    with motor_migrate.begin() as c:
        c.execute(
            text(
                "with sin as ("
                "  select v.* from nucleo.vinculo v where not exists ("
                "    select 1 from auditoria.eventos e"
                "     where e.accion = 'identidad.invitacion.aceptar'"
                "       and e.despues ->> 'vinculo_id' = v.id::text)),"
                " inv as ("
                "  insert into nucleo.invitacion (consorcio_id, unidad_id, tipo, correo, nombre,"
                "    token_hash, vence_en, aceptada_en, persona_id)"
                "  select s.consorcio_id, s.unidad_id, s.tipo,"
                "    'sintetica-' || s.id || '@ejemplo.invalid', 'Sintética',"
                "    sha256(s.id::text::bytea), now() + interval '1 day', now(), s.persona_id"
                "    from sin s"
                "  returning id, consorcio_id, persona_id, token_hash)"
                " insert into auditoria.eventos (consorcio_id, actor_persona_id, origen, accion,"
                "   entidad, entidad_id, despues, hash_previo, hash)"
                " select i.consorcio_id, i.persona_id, 'api', 'identidad.invitacion.aceptar',"
                "   'invitacion', i.id, jsonb_build_object('vinculo_id', s.id), '', ''"
                "   from inv i join sin s on sha256(s.id::text::bytea) = i.token_hash"
            )
        )
        # Bajar por debajo de 0007 borra las invitaciones y deja sus eventos: se recrean con
        # el mismo id, para que el primer evento de cada vínculo vuelva a tener su invitación.
        c.execute(
            text(
                "insert into nucleo.invitacion (id, consorcio_id, unidad_id, tipo, correo, nombre,"
                "   token_hash, vence_en, aceptada_en, persona_id)"
                " select distinct on (e.entidad_id) e.entidad_id, v.consorcio_id, v.unidad_id,"
                "   v.tipo, 'recreada-' || e.entidad_id || '@ejemplo.invalid', 'Recreada',"
                "   sha256(e.entidad_id::text::bytea), now() + interval '1 day', now(),"
                "   v.persona_id"
                "  from auditoria.eventos e"
                "  join nucleo.vinculo v on v.id::text = e.despues ->> 'vinculo_id'"
                " where e.accion = 'identidad.invitacion.aceptar'"
                "   and not exists (select 1 from nucleo.invitacion i where i.id = e.entidad_id)"
            )
        )


def volver_a_subir(cfg, motor_migrate: Engine) -> None:
    """Sube a `head` después de bajar por debajo de 0007, que se lleva las invitaciones."""
    command.upgrade(cfg, "0013")
    dar_procedencia_sintetica(motor_migrate)
    command.upgrade(cfg, "head")


@pytest.fixture
def redis_limpio() -> Iterator[Redis]:
    """Redis real sin claves del límite de tasa de pruebas anteriores."""
    cliente = Redis.from_url(REDIS_URL)

    def limpiar() -> None:
        for clave in cliente.scan_iter("cem:rl:*"):
            cliente.delete(clave)

    limpiar()
    yield cliente
    limpiar()
    cliente.close()


@pytest.fixture(scope="session")
def motor_migrate(base_de_prueba) -> Engine:
    return create_engine(url_rol("migrate"))


@pytest.fixture(scope="session")
def motor_app(base_de_prueba) -> Engine:
    return create_engine(url_rol("app"))


@dataclass(frozen=True)
class Datos:
    consorcio_a: UUID
    consorcio_b: UUID
    unidad_a1: UUID
    unidad_a2: UUID
    unidad_b1: UUID
    persona_a: UUID
    """Propietaria en A."""
    persona_b: UUID
    """Inquilina en B."""
    persona_ab: UUID
    """Propietaria en A e inquilina en B."""
    persona_vencida: UUID
    """Tuvo un vínculo en A que ya terminó."""


@pytest.fixture(scope="session")
def datos(motor_migrate: Engine) -> Datos:
    """Carga sintética con cem_migrate (BYPASSRLS)."""

    def uno(conn, sql: str, **p) -> UUID:
        return conn.execute(text(sql + " returning id"), p).scalar_one()

    with motor_migrate.begin() as c:
        ca = uno(c, "insert into nucleo.consorcio (nombre) values ('Edificio Alvear 100')")
        cb = uno(c, "insert into nucleo.consorcio (nombre) values ('Edificio Belgrano 200')")
        ins_u = "insert into nucleo.unidad (consorcio_id, designacion) values (:c, :d)"
        a1 = uno(c, ins_u, c=ca, d="1° A")
        a2 = uno(c, ins_u, c=ca, d="1° B")
        b1 = uno(c, ins_u, c=cb, d="PB")
        ins_p = "insert into nucleo.persona (correo, nombre) values (:c, :n)"
        pa = uno(c, ins_p, c="ana@ejemplo.com", n="Ana")
        pb = uno(c, ins_p, c="beto@ejemplo.com", n="Beto")
        pab = uno(c, ins_p, c="carla@ejemplo.com", n="Carla")
        pv = uno(c, ins_p, c="dario@ejemplo.com", n="Darío")
        ins_v = (
            "insert into nucleo.vinculo"
            " (consorcio_id, unidad_id, persona_id, tipo, vigente_desde, vigente_hasta, fuente,"
            " origen)"
            " values (:c, :u, :p, :t, :desde, :hasta, 'documento', 'soporte')"
        )
        for cons, uni, per, tipo, desde, hasta in [
            (ca, a1, pa, "propietario", "2020-01-01", None),
            (cb, b1, pb, "inquilino", "2024-03-01", None),
            (ca, a2, pab, "propietario", "2019-05-01", None),
            (cb, b1, pab, "inquilino", "2025-01-01", None),
            (ca, a2, pv, "inquilino", "2018-01-01", "2019-01-01"),
        ]:
            uno(c, ins_v, c=cons, u=uni, p=per, t=tipo, desde=desde, hasta=hasta)
    return Datos(ca, cb, a1, a2, b1, pa, pb, pab, pv)


def clave_idempotencia() -> dict[str, str]:
    """Encabezado `Idempotency-Key` nuevo: un intento lógico distinto por llamada."""
    return {"Idempotency-Key": str(uuid4())}
