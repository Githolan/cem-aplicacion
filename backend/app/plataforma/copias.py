"""Copia diaria verificada de la base `cem` (documento 13, §17; plan H1, semana 4: H0b).

Primera parte (2a): volcar y verificar. El cifrado con `age`, la subida a OCI y la tarea
periódica llegan en la segunda (2b).

1. Volcado: `pg_dump -Fc` con el rol `cem_backup` sobre una instantánea exportada
   (`pg_export_snapshot`). En la misma instantánea se calculan las consultas de control, así
   que el volcado y los controles describen exactamente el mismo estado de la base.
2. Verificación (§17.3): el volcado se restaura en un PostgreSQL efímero que este proceso
   levanta en un directorio temporal (solo socket Unix, sin puerto) y se repiten los controles.
   Si algo difiere, la copia no cuenta.

Controles: por cada tabla, cantidad de filas y una huella (md5 de las huellas de cada fila,
ordenadas). Cubre la cantidad de consorcios y cualquier suma de §17.3, porque una fila distinta
cambia la huella de su tabla.
"""

import os
import subprocess
import tempfile
from collections.abc import Mapping
from pathlib import Path

from sqlalchemy import Connection, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

USUARIO_EFIMERO = "verificador"
BASE_EFIMERA = "verificacion"
AJUSTES_EFIMERO = (
    # Solo socket Unix en el directorio temporal: nadie más llega a esta base.
    "-c listen_addresses=''",
    # Es descartable: sin fsync la restauración es más rápida y no hay nada que proteger.
    "-c fsync=off",
    "-c full_page_writes=off",
    # Poca memoria: corre dentro de cem-trabajos en la VM compartida (§19.2).
    "-c shared_buffers=16MB",
    "-c max_connections=5",
)

AJUSTES_CONTROL = (
    # Mismo texto de cada fila en producción y en la restauración, sin importar la
    # configuración de cada servidor.
    "set local timezone = 'UTC'",
    "set local datestyle = 'ISO, YMD'",
    "set local intervalstyle = 'postgres'",
    "set local extra_float_digits = 3",
    "set local bytea_output = 'hex'",
)

TABLAS = text(
    "select format('%I.%I', n.nspname, c.relname) from pg_class c"
    " join pg_namespace n on n.oid = c.relnamespace"
    " where c.relkind in ('r', 'p')"
    " and n.nspname not in ('pg_catalog', 'information_schema')"
    " and n.nspname not like 'pg\\_toast%'"
    " order by 1"
)


class ErrorCopia(Exception):
    """La copia no se hizo o no se pudo verificar: no cuenta como copia (§17.3)."""


Controles = dict[str, tuple[int, str]]
"""Tabla calificada → (filas, huella)."""


def controles(conn: Connection) -> Controles:
    """Consultas de control sobre la transacción abierta de `conn`."""
    for ajuste in AJUSTES_CONTROL:
        conn.execute(text(ajuste))
    resultado: Controles = {}
    for (tabla,) in conn.execute(TABLAS).all():
        filas, huella = conn.execute(
            text(
                "select count(*), coalesce(md5(string_agg(h, '' order by h collate \"C\")), '')"
                f" from (select md5(cem_fila::text) as h from {tabla} cem_fila) f"
            )
        ).one()
        resultado[tabla] = (filas, huella)
    return resultado


def _correr(argumentos: list[str], entorno: Mapping[str, str] | None = None) -> None:
    hecho = subprocess.run(
        argumentos, env=None if entorno is None else dict(entorno), capture_output=True, text=True
    )
    if hecho.returncode != 0:
        raise ErrorCopia(f"{argumentos[0]} terminó con {hecho.returncode}: {hecho.stderr.strip()}")


def volcar(url_backup: str, destino: Path) -> Controles:
    """`pg_dump -Fc` y controles de la misma instantánea de la base."""
    url = make_url(url_backup)
    motor = create_engine(url, poolclass=NullPool)
    try:
        with motor.connect().execution_options(isolation_level="REPEATABLE READ") as conn:
            instantanea = conn.execute(text("select pg_export_snapshot()")).scalar_one()
            # La contraseña va por el entorno: en los argumentos la vería cualquiera con `ps`.
            # Sin puerto en la URL, pg_dump usa el de libpq, igual que psycopg.
            puerto = [] if url.port is None else [f"--port={url.port}"]
            _correr(
                [
                    "pg_dump", "--format=custom", "--no-password",
                    f"--snapshot={instantanea}", f"--file={destino}",
                    f"--host={url.host}", *puerto,
                    f"--username={url.username}", f"--dbname={url.database}",
                ],
                os.environ | {"PGPASSWORD": url.password or ""},
            )  # fmt: skip
            esperados = controles(conn)
            conn.rollback()
    finally:
        motor.dispose()
    if not esperados:
        raise ErrorCopia("la base no tiene tablas: no hay nada que verificar")
    return esperados


def verificar_volcado(volcado: Path, esperados: Controles) -> None:
    """Restaura en un PostgreSQL efímero y compara los controles (§17.3)."""
    with tempfile.TemporaryDirectory(prefix="cem-verif-") as carpeta:
        datos = Path(carpeta) / "datos"
        _correr(["initdb", "--pgdata", str(datos), "--username", USUARIO_EFIMERO,
                 "--auth=trust", "--encoding=UTF8", "--locale=C", "--no-sync"])  # fmt: skip
        conexion = ["--host", carpeta, "--username", USUARIO_EFIMERO]
        _correr(["pg_ctl", "--pgdata", str(datos), "--wait", "--silent",
                 "--log", str(Path(carpeta) / "postgres.log"),
                 "-o", " ".join((*AJUSTES_EFIMERO, f"-k {carpeta}")), "start"])  # fmt: skip
        try:
            _correr(["createdb", *conexion, BASE_EFIMERA])
            # Sin dueños ni permisos: los roles de producción no existen en la base efímera.
            _correr(["pg_restore", "--exit-on-error", "--no-owner", "--no-acl",
                     *conexion, f"--dbname={BASE_EFIMERA}", str(volcado)])  # fmt: skip
            motor = create_engine(
                f"postgresql+psycopg://{USUARIO_EFIMERO}@/{BASE_EFIMERA}?host={carpeta}",
                poolclass=NullPool,
            )
            try:
                with motor.begin() as conn:
                    obtenidos = controles(conn)
            finally:
                motor.dispose()
        finally:
            _correr(["pg_ctl", "--pgdata", str(datos), "--wait", "--silent", "-m", "fast", "stop"])
    if obtenidos != esperados:
        distintas = sorted(
            t for t in esperados.keys() | obtenidos.keys() if esperados.get(t) != obtenidos.get(t)
        )
        raise ErrorCopia(f"la restauración no coincide con la base en: {', '.join(distintas)}")
