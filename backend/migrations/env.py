"""Entorno de Alembic. Las migraciones se escriben en SQL explícito, sin autogenerar.

Corre con el rol `cem_migrate` (propietario de los esquemas, BYPASSRLS). La tabla de
versiones vive en el esquema `plataforma`.
"""

import os

from alembic import context
from sqlalchemy import create_engine, text

url = context.config.get_main_option("sqlalchemy.url") or os.environ["CEM_DATABASE_URL_MIGRATE"]


def run_migrations_online() -> None:
    motor = create_engine(url)
    with motor.connect() as conn:
        conn.execute(text("create schema if not exists plataforma"))
        conn.commit()
        context.configure(
            connection=conn,
            version_table_schema="plataforma",
            transaction_per_migration=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    raise SystemExit("Las migraciones de CEM se aplican siempre contra una base real.")
run_migrations_online()
