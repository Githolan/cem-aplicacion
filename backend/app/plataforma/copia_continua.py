"""Proceso `cem-copia-continua`: WAL continuo y copia base física semanal verificada (H2c §6 del
repositorio CEM; documento 13, §17.2 y §17.3; RNF06: RPO de 15 minutos).

    python -m app.plataforma.copia_continua      (CEM_PROCESO=copia_continua en arranque.sh)

Un `pg_dump` diario (`copias.py`) no admite aplicarle WAL. Para restaurar a un momento hace falta
una copia base física y todo el WAL desde ella. Este proceso hace las dos cosas:

1. **WAL continuo.** `pg_receivewal` con el slot físico `cem_copia_continua` (lo crea si falta)
   escribe en el directorio de trabajo. Cada segmento completo se comprime con gzip, se cifra con
   `age` a `CEM_COPIAS_CLAVE_PUBLICA`, se sube a `wal/`, se relee comparando SHA-256 y recién
   entonces se borra del disco. Cada `INTERVALO_PARCIAL` el segmento en curso (`.partial`),
   comprimido y cifrado igual, va a `wal-parcial/` con el segmento y el momento en el nombre: un
   segmento de 16 MB tarda horas en completarse con poco movimiento, y así se cumple el RPO.
2. **Copia base semanal** (`pg_basebackup`, formato tar, con su manifiesto) si en `base/` no hay
   ninguna verificada de los últimos `VIGENCIA_BASE`, y en el acto si la cadena de WAL se cortó
   (slot perdido o desaparecido, o un hueco entre lo subido y lo recibido).
3. **Verificación de cada copia base** antes de subirla: `pg_verifybackup` contra su manifiesto y
   restauración en un PostgreSQL efímero (solo socket Unix) con el WAL hasta su fin; controles:
   cantidad de consorcios igual a la leída en la base real al terminar la copia, cadena de
   auditoría íntegra y un recorrido completo de cada tabla. Una copia que no verificó no se sube y
   no cuenta (§17.3). Solo cuenta la que tiene su marca `base/<momento>/verificada.json`, que se
   sube al final, después de releer cada objeto.

Compresión
----------
`pg_receivewal --compress` de la imagen acepta gzip y lz4, no zstd (medido: "compression with ZSTD
is not yet supported"). No se usa: con compresión, el `.partial` es un flujo comprimido a medio
escribir, que no se puede leer como archivo válido mientras crece. El receptor escribe sin
comprimir (el `.partial` ocupa el segmento entero, relleno con ceros) y este proceso comprime cada
archivo con gzip (biblioteca estándar) antes de cifrarlo: el relleno casi no ocupa, y lo que queda
depende del WAL escrito. **SIN MEDIR** con el movimiento real: se mide en staging (H2c §6), con el
control diario de tamaño. Restaurar: `age -d` y `gunzip`, y el archivo vuelve a ser el segmento
tal cual.

Restauración en una imagen distinta de la base
----------------------------------------------
`cem-postgres` es `postgres:18-alpine` (musl) y crea las bases con el locale `en_US.utf8`; esta
imagen es Debian (glibc). La copia física no deja conectar a una base cuyo locale no existe en el
sistema, por eso el Dockerfile genera `en_US.UTF-8`. Pero el orden de intercalación de glibc no
es el de musl (musl compara por bytes): los índices de texto de la copia no sirven en esta
imagen. Los controles leen **solo con recorridos completos de tabla** (`enable_indexscan`,
`enable_indexonlyscan` y `enable_bitmapscan` apagados), como `copias.controles()`, y no dependen
de ningún índice. El ensayo PA26 real se hace con `postgres:18-alpine` (H2c §6).

Conexiones y permisos
---------------------
`cem_copia_continua` tiene `REPLICATION` y nada más (`scripts/crear_rol_copia_continua.sql`),
con un tope de 3 conexiones: el receptor, `pg_basebackup` con `--wal-method=fetch` (una sola
conexión; el WAL que necesita lo retiene el slot) y una consulta breve por vuelta a
`pg_replication_slots` y `pg_current_wal_lsn()` sobre la base que nombra
`CEM_DATABASE_URL_COPIA_CONTINUA` (`postgres`, que PUBLIC puede abrir: el rol no tiene permiso
de conexión a `cem`). La cantidad de consorcios de la base real la lee `cem_backup`
(`CEM_DATABASE_URL_BACKUP`) con una consulta al terminar cada copia base.

Avisos a Kuma (opcionales; sin la variable, un WARNING al arrancar y se sigue)
------------------------------------------------------------------------------
- `CEM_COPIA_CONTINUA_AVISO_URL`: cada `INTERVALO_AVISO`, `up` si el receptor corre, está al día
  (atraso de menos de `ATRASO_MAXIMO`), lo recibido está subido (hace menos de `ATRASO_MAXIMO`)
  y la cadena no está cortada; `down` con el motivo si no. Un corte se avisa en el acto.
- `CEM_COPIA_BASE_AVISO_URL`: `up` por cada copia base verificada y subida, `down` si falla.
Las URL llevan el token del monitor: nunca van a un registro.
"""

import gzip
import hashlib
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import tarfile
import tempfile
import threading
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

from app import registros
from app.plataforma import copias, latido

log = logging.getLogger("cem.copia_continua")

SLOT = "cem_copia_continua"
"""Slot físico del receptor. Lo crea el propio proceso (`--create-slot --if-not-exists`)."""

INTERVALO_PARCIAL = timedelta(minutes=5)
"""Cada cuánto sube el segmento en curso (H2c §6): con el RPO de 15 minutos de RNF06."""
ATRASO_MAXIMO = timedelta(minutes=15)
"""Atraso del receptor, o de la subida, que se avisa como caída (H2c §6)."""
VIGENCIA_BASE = timedelta(days=7)
"""Separación máxima entre dos copias base verificadas (copia semanal). Con la retención de 42
días de `base/` y `wal/` (35 de ventana más esta semana, H2c §6), cualquier momento de los
últimos 35 días tiene su copia base anterior guardada."""
INTERVALO_REVISAR_BASE = timedelta(hours=1)
"""Cada cuánto se mira `base/` y, si una copia base falló, cada cuánto se reintenta. La copia
nueva se empieza cuando a la última le falta menos que esto para cumplir `VIGENCIA_BASE`: así la
separación no pasa de 7 días por esperar a la revisión siguiente."""
INTERVALO_AVISO = timedelta(minutes=1)
"""Cada cuánto se manda el latido del receptor a Kuma."""
INTERVALO_VUELTA = 10.0
"""Segundos entre vueltas: subir lo completo, mirar el slot y el atraso, latir."""
ESPERA_RESTAURACION = 3600
"""Segundos que `pg_ctl` espera a que la base restaurada termine de aplicar el WAL."""
ESPERA_SONDEO = 0.2
"""Segundos entre miradas a `postmaster.pid` mientras la copia restaurada aplica el WAL."""
POSICION_ESTADO = 7
"""Línea (desde 0) de `postmaster.pid` con el estado del servidor (`LOCK_FILE_LINE_PM_STATUS`)."""
ESPERA_RECEPTOR = 10
"""Segundos que se espera a que `pg_receivewal` salga después de SIGTERM."""
NIVEL_GZIP = 6
LARGO_MENSAJE = 200
"""Caracteres del mensaje a Kuma, que viaja en la URL del push."""

PREFIJO_WAL = "wal/"
PREFIJO_PARCIAL = "wal-parcial/"
PREFIJO_BASE = "base/"
MARCA = "verificada.json"
ARCHIVOS_BASE = ("base.tar.gz", "backup_manifest")
"""Lo que deja `pg_basebackup --format=tar --gzip --wal-method=fetch` (con `fetch`, el WAL de la
copia va dentro de `base.tar.gz`), y se sube cifrado."""

VARIABLE_URL = "CEM_DATABASE_URL_COPIA_CONTINUA"
VARIABLE_DIRECTORIO = "CEM_COPIA_CONTINUA_DIRECTORIO"
"""Opcional: directorio de trabajo del receptor. Sin ella, `cem-copia-continua` dentro del
directorio temporal del sistema. Un volumen persistente evita que un contenedor recreado pierda
lo recibido y no subido (ver `revisar_continuidad`)."""
VARIABLE_AVISO_CONTINUA = "CEM_COPIA_CONTINUA_AVISO_URL"
VARIABLE_AVISO_BASE = "CEM_COPIA_BASE_AVISO_URL"

SEGMENTO = re.compile(r"^[0-9A-F]{24}$")
HISTORIA = re.compile(r"^[0-9A-F]{8}\.history$")

AJUSTES_RESTAURADA = {
    # Solo socket Unix en el directorio temporal: nadie más llega a esta base.
    "listen_addresses": "",
    # Sin modo de solo lectura durante la recuperación: así no se exige que max_connections y
    # los demás topes sean al menos los de la base real, y llega a `promote` al terminar.
    "hot_standby": "off",
    "recovery_target_action": "promote",
    "archive_mode": "off",
    # Descartable: sin fsync aplica más rápido y no hay nada que proteger.
    "fsync": "off",
    "shared_buffers": "16MB",
    "max_connections": "10",
    "max_wal_senders": "0",
    "logging_collector": "off",
}
"""Se agregan al final de `postgresql.auto.conf` de la copia, que manda sobre lo anterior."""

SIN_INDICES = (
    "set local enable_indexscan = off",
    "set local enable_indexonlyscan = off",
    "set local enable_bitmapscan = off",
)
"""Recorridos completos de tabla: los índices de texto de una copia de musl no sirven en glibc."""

_FORMULA_CADENA = (
    "encode(sha256(convert_to(concat_ws('|', hash_previo, consorcio_id, id, secuencia,"
    " to_char(creado_en at time zone 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS.US'),"
    " actor_persona_id, origen, {accion}, entidad, entidad_id, antes, despues, motivo, ip),"
    " 'UTF8')), 'hex')"
)
CADENA = text(
    "with e as (select *, lag(hash) over (partition by consorcio_id order by secuencia)"
    " as anterior from auditoria.eventos)"
    " select count(*),"
    " count(*) filter (where hash_previo <> coalesce(anterior, '')),"
    f" count(*) filter (where hash <> {_FORMULA_CADENA.format(accion='accion')}),"
    f" count(*) filter (where hash <> {_FORMULA_CADENA.format(accion="accion || 'x'")})"
    " from e"
)
"""La cadena de `auditoria.encadenar` (migración 0001), consorcio por consorcio, como la
comprueba el smoke de staging (`pruebas/smoke_staging.py`): total, enlaces rotos, hashes
distintos y, de control, cuántos hashes distintos da la misma cuenta con la acción alterada
(tienen que ser todos: si no, la fórmula no está comprobando nada)."""


class ErrorCopiaContinua(copias.ErrorCopia):
    """Una copia base que no se hizo o no verificó, o una cadena de WAL que no se puede seguir."""


# Configuración ------------------------------------------------------------------------


@dataclass(frozen=True)
class ConfigCopiaContinua:
    url_copia_continua: str
    """Rol `cem_copia_continua`; la base que nombra solo se usa para mirar el slot."""
    copias: copias.ConfigCopias
    """`CEM_DATABASE_URL_BACKUP` (cantidad de consorcios), la clave pública y el almacén."""
    directorio: Path

    @classmethod
    def desde_entorno(cls, entorno: Mapping[str, str] = os.environ) -> "ConfigCopiaContinua":
        variables = [VARIABLE_URL, *copias.VARIABLES.values()]
        faltan = [v for v in variables if not entorno.get(v)]
        if faltan:
            raise ErrorCopiaContinua("faltan variables de la copia continua: " + ", ".join(faltan))
        directorio = entorno.get(VARIABLE_DIRECTORIO) or str(
            Path(tempfile.gettempdir()) / "cem-copia-continua"
        )
        return cls(
            url_copia_continua=entorno[VARIABLE_URL],
            copias=copias.ConfigCopias.desde_entorno(entorno),
            directorio=Path(directorio),
        )


def conexion_libpq(url_texto: str) -> tuple[list[str], dict[str, str]]:
    """Argumentos de conexión para las herramientas de PostgreSQL y el entorno con la clave: en
    los argumentos la vería cualquiera con `ps`."""
    url = make_url(url_texto)
    argumentos = [f"--host={url.host}", f"--username={url.username}", "--no-password"]
    if url.port is not None:
        argumentos.append(f"--port={url.port}")
    return argumentos, dict(os.environ) | {"PGPASSWORD": url.password or ""}


def _sello(momento: datetime) -> str:
    return f"{momento.astimezone(UTC):%Y%m%dT%H%M%SZ}"


def lsn(valor: str) -> int:
    """`0/16B3748` → entero."""
    alto, bajo = valor.split("/")
    return (int(alto, 16) << 32) | int(bajo, 16)


# Slot y servidor ----------------------------------------------------------------------


@dataclass(frozen=True)
class EstadoServidor:
    slot_existe: bool
    wal_status: str | None
    """`reserved`, `extended`, `unreserved` o `lost` (`pg_replication_slots`)."""
    restart_lsn: int | None
    """Hasta dónde confirmó el receptor (con `--synchronous`, lo que escribió en disco)."""
    lsn_servidor: int
    tamano_segmento: int
    tope_slot_mb: int | None = None
    """`max_slot_wal_keep_size` en MB; -1 es sin tope: un receptor caído llenaría el disco de la
    base (H2c §6 pide 5 GB)."""
    slot_activo: bool = False
    """El slot tiene un receptor conectado (el propio o el de otro contenedor)."""


def estado_servidor(url_copia_continua: str) -> EstadoServidor:
    motor = create_engine(url_copia_continua, poolclass=NullPool)
    try:
        with motor.connect() as conn:
            fila = conn.execute(
                text(
                    "select s.slot_name is not null, s.wal_status, s.restart_lsn::text,"
                    " pg_current_wal_lsn()::text,"
                    " (select setting::bigint from pg_settings where name = 'wal_segment_size'),"
                    " (select setting::bigint from pg_settings"
                    "   where name = 'max_slot_wal_keep_size'),"
                    " coalesce(s.active, false)"
                    " from (select 1) uno"
                    " left join pg_replication_slots s on s.slot_name = :s"
                ),
                {"s": SLOT},
            ).one()
    finally:
        motor.dispose()
    existe, wal_status, restart, actual, tamano, tope, activo = fila
    return EstadoServidor(
        existe, wal_status, lsn(restart) if restart else None, lsn(actual), int(tamano),
        int(tope), activo,
    )  # fmt: skip


def crear_slot(url_copia_continua: str) -> None:
    argumentos, entorno = conexion_libpq(url_copia_continua)
    copias._correr(
        ["pg_receivewal", "--create-slot", "--if-not-exists", f"--slot={SLOT}", *argumentos],
        entorno,
    )


def borrar_slot(url_copia_continua: str) -> None:
    argumentos, entorno = conexion_libpq(url_copia_continua)
    copias._correr(["pg_receivewal", "--drop-slot", f"--slot={SLOT}", *argumentos], entorno)


def numero_segmento(nombre: str, tamano_segmento: int) -> tuple[int, int]:
    """(línea de tiempo, número de segmento) de un nombre de 24 hexadecimales."""
    por_id = 0x100000000 // tamano_segmento
    return int(nombre[:8], 16), int(nombre[8:16], 16) * por_id + int(nombre[16:24], 16)


# Archivos cifrados en el almacén ------------------------------------------------------


def subir_cifrado(
    s3,
    cfg: copias.ConfigCopias,
    origen: Path,
    clave: str,
    trabajo: Path,
    *,
    comprimir: bool,
    metadatos: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Comprime (opcional), cifra a la clave pública, sube y relee comparando SHA-256."""
    entrada = origen
    comprimido = trabajo / f"{origen.name}.gz"
    cifrado = trabajo / f"{origen.name}.age"
    try:
        if comprimir:
            with origen.open("rb") as f, gzip.GzipFile(comprimido, "wb", NIVEL_GZIP, mtime=0) as g:
                shutil.copyfileobj(f, g, copias.TROZO)
            entrada = comprimido
        copias.cifrar(entrada, cfg.clave_publica, cifrado)
        suma = copias.sha256(cifrado)
        with cifrado.open("rb") as cuerpo:
            s3.put_object(
                Bucket=cfg.bucket, Key=clave, Body=cuerpo,
                Metadata={"sha256": suma, **(metadatos or {})},
            )  # fmt: skip
        copias.verificar_objeto(s3, cfg.bucket, clave, suma)
        return {"clave": clave, "bytes": cifrado.stat().st_size, "sha256": suma}
    finally:
        comprimido.unlink(missing_ok=True)
        cifrado.unlink(missing_ok=True)


def claves(s3, bucket: str, prefijo: str) -> Iterator[dict[str, Any]]:
    paginas = s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefijo)
    for pagina in paginas:
        yield from pagina.get("Contents", [])


def ultimo_segmento_subido(s3, bucket: str, tamano_segmento: int) -> tuple[int, int] | None:
    """El segmento completo más alto de `wal/` (línea de tiempo, número), o None si no hay."""
    numeros = [
        numero_segmento(nombre, tamano_segmento)
        for o in claves(s3, bucket, PREFIJO_WAL)
        if SEGMENTO.match(nombre := o["Key"].removeprefix(PREFIJO_WAL).removesuffix(".gz.age"))
    ]
    return max(numeros, default=None)


def marca_releida(s3, bucket: str, clave_marca: str) -> bool:
    """La marca se relee contra su `sha256` y cada objeto que nombra existe con el `sha256` que
    dice la marca. Una marca cuya relectura falló al subirla queda en el almacén (no se puede
    borrar), y así no cuenta."""
    meta = copias.metadatos(s3, bucket, clave_marca)
    if meta is None or "sha256" not in meta:
        return False
    cuerpo = s3.get_object(Bucket=bucket, Key=clave_marca)["Body"].read()
    if hashlib.sha256(cuerpo).hexdigest() != meta["sha256"]:
        return False
    try:
        objetos = json.loads(cuerpo)["objetos"]
    except (ValueError, KeyError, TypeError):
        return False
    for o in objetos.values():
        meta_objeto = copias.metadatos(s3, bucket, o["clave"])
        if meta_objeto is None or meta_objeto.get("sha256") != o["sha256"]:
            return False
    return True


def ultima_base_verificada(s3, bucket: str) -> datetime | None:
    """El momento de la copia base más nueva cuya marca de verificada se relee bien, o None."""
    marcas = []
    for o in claves(s3, bucket, PREFIJO_BASE):
        partes = o["Key"].removeprefix(PREFIJO_BASE).split("/")
        if len(partes) == 2 and partes[1] == MARCA:
            momento = datetime.strptime(partes[0], "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
            marcas.append((momento, o["Key"]))
    for momento, clave in sorted(marcas, reverse=True):
        if marca_releida(s3, bucket, clave):
            return momento
        log.error("la marca %s no se relee: esa copia base no cuenta", clave)
    return None


# Copia base y su verificación ---------------------------------------------------------


def contar_consorcios(url_backup: str) -> int:
    motor = create_engine(url_backup, poolclass=NullPool)
    try:
        with motor.connect() as conn:
            return conn.execute(text("select count(*) from nucleo.consorcio")).scalar_one()
    finally:
        motor.dispose()


def _extraer(archivo: Path, destino: Path) -> None:
    with tarfile.open(archivo, "r:*") as tar:
        tar.extractall(destino, filter="data")


def _conf(valor: str) -> str:
    return "'" + valor.replace("'", "''") + "'"


@contextmanager
def restaurada(
    carpeta_base: Path, objetivo: Mapping[str, str], carpeta_wal: Path | None = None
) -> Iterator[Path]:
    """Restaura la copia base de `carpeta_base` (los archivos de `ARCHIVOS_BASE`, sin cifrar) en
    un PostgreSQL efímero y lo deja corriendo mientras dure el bloque. Devuelve el directorio
    de su socket Unix.

    `objetivo` son los parámetros de recuperación (`recovery_target = 'immediate'` para llegar
    solo al fin de la copia; `recovery_target_time` para un momento; vacío para aplicar todo el
    WAL disponible). `carpeta_wal` tiene los segmentos que pida `restore_command` (sin cifrar ni
    comprimir; un `.partial` renombrado como su segmento)."""
    with tempfile.TemporaryDirectory(prefix="cem-restaurada-") as carpeta:
        raiz = Path(carpeta)
        datos = raiz / "datos"
        datos.mkdir(mode=0o700)
        _extraer(carpeta_base / "base.tar.gz", datos)
        wal = carpeta_wal or raiz / "sin-wal"
        wal.mkdir(exist_ok=True)
        # Solo el socket local, sin claves: los usuarios son los de la base real.
        (datos / "pg_hba.conf").write_text("local all all trust\n")
        (datos / "recovery.signal").touch()
        ajustes = AJUSTES_RESTAURADA | {
            "unix_socket_directories": carpeta,
            "restore_command": f"cp {wal}/%f %p",
            **objetivo,
        }
        with (datos / "postgresql.auto.conf").open("a") as conf:
            conf.write("\n# cem-copia-continua: restauración efímera\n")
            for nombre, valor in ajustes.items():
                conf.write(f"{nombre} = {_conf(valor)}\n")
        registro = raiz / "postgres.log"
        hecho = subprocess.run(
            ["pg_ctl", "--pgdata", str(datos), "--wait", f"--timeout={ESPERA_RESTAURACION}",
             "--silent", "--log", str(registro), "start"],
            capture_output=True, text=True,
        )  # fmt: skip
        if hecho.returncode != 0:
            raise ErrorCopiaContinua(
                f"la copia restaurada no arrancó: {hecho.stderr.strip()}\n{_final(registro)}"
            )
        try:
            _esperar_promocion(datos, registro)
            yield raiz
        finally:
            if (datos / "postmaster.pid").exists():
                copias._correr(
                    ["pg_ctl", "--pgdata", str(datos), "--wait", "--silent", "-m", "fast", "stop"]
                )


def _final(registro: Path) -> str:
    return registro.read_text(errors="replace")[-2000:] if registro.exists() else ""


def _esperar_promocion(datos: Path, registro: Path) -> None:
    """`pg_ctl --wait` vuelve cuando el servidor queda en recuperación (estado `standby` en
    `postmaster.pid`); sin modo de solo lectura no acepta conexiones hasta terminar de aplicar
    el WAL y promoverse (`ready`). Si el servidor se cae (un WAL que falta antes del momento
    pedido, una copia rota), el archivo desaparece y es un error con el final del registro."""
    limite = time.monotonic() + ESPERA_RESTAURACION
    pid = datos / "postmaster.pid"
    while time.monotonic() < limite:
        if not pid.exists():
            raise ErrorCopiaContinua(f"la copia restaurada se detuvo:\n{_final(registro)}")
        lineas = pid.read_text().splitlines()
        if len(lineas) > POSICION_ESTADO and lineas[POSICION_ESTADO].strip() == "ready":
            return
        time.sleep(ESPERA_SONDEO)
    raise ErrorCopiaContinua(
        f"la copia restaurada no terminó de aplicar el WAL en {ESPERA_RESTAURACION} s"
    )


def controles_restaurada(conn: Connection) -> dict[str, Any]:
    """Controles de §17.3 sobre la transacción abierta de `conn`, sin usar índices."""
    for ajuste in SIN_INDICES:
        conn.execute(text(ajuste))
    consorcios = conn.execute(text("select count(*) from nucleo.consorcio")).scalar_one()
    total, rotos, distintos, control = conn.execute(CADENA).one()
    tablas = copias.controles(conn)  # cada tabla leída entera
    return {
        "consorcios": consorcios,
        "eventos": total,
        "eventos_rotos": rotos,
        "eventos_distintos": distintos,
        "control_cadena": control,
        "tablas": len(tablas),
        "filas": sum(filas for filas, _ in tablas.values()),
    }


def consultar_restaurada(socket: Path, url_backup: str) -> dict[str, Any]:
    """Los controles en la copia restaurada, con el usuario y la base de `url_backup`."""
    url = make_url(url_backup)
    motor = create_engine(
        f"postgresql+psycopg://{url.username}@/{url.database}?host={socket}", poolclass=NullPool
    )
    try:
        with motor.begin() as conn:
            return controles_restaurada(conn)
    finally:
        motor.dispose()


def verificar_base(carpeta_base: Path, url_backup: str, consorcios: int) -> dict[str, Any]:
    """`pg_verifybackup` y restauración hasta el fin de la copia. Lanza `ErrorCopiaContinua` si
    algo no coincide: la copia no cuenta."""
    copias._correr(["pg_verifybackup", "--format=tar", "--no-parse-wal", str(carpeta_base)])
    with restaurada(carpeta_base, {"recovery_target": "immediate"}) as socket:
        resultado = consultar_restaurada(socket, url_backup)
    problemas = []
    if resultado["consorcios"] != consorcios:
        problemas.append(
            f"{resultado['consorcios']} consorcios en la copia y {consorcios} en la base real"
        )
    if resultado["eventos_rotos"] or resultado["eventos_distintos"]:
        problemas.append(
            f"cadena de auditoría: {resultado['eventos_rotos']} enlaces rotos y"
            f" {resultado['eventos_distintos']} hashes distintos de {resultado['eventos']}"
        )
    if resultado["control_cadena"] != resultado["eventos"]:
        problemas.append("el control de la cadena no detecta una acción alterada")
    if problemas:
        raise ErrorCopiaContinua("la copia base no verificó: " + "; ".join(problemas))
    return resultado


def _rango_wal(manifiesto: Path) -> dict[str, Any]:
    rangos = json.loads(manifiesto.read_text())["WAL-Ranges"]
    return {
        "linea_de_tiempo": rangos[-1]["Timeline"],
        "inicio_lsn": rangos[0]["Start-LSN"],
        "fin_lsn": rangos[-1]["End-LSN"],
    }


def tomar_base(url_copia_continua: str, destino: Path, sello: str) -> None:
    """`pg_basebackup` en formato tar comprimido, con el WAL de la copia dentro (`fetch`: una
    sola conexión) y el manifiesto con SHA-256 de cada archivo."""
    argumentos, entorno = conexion_libpq(url_copia_continua)
    copias._correr(
        ["pg_basebackup", f"--pgdata={destino}", "--format=tar", "--gzip",
         "--wal-method=fetch", "--checkpoint=fast", "--manifest-checksums=SHA256",
         f"--label=cem-copia-base-{sello}", *argumentos],
        entorno,
    )  # fmt: skip


def copia_base(cfg: ConfigCopiaContinua, momento: datetime) -> dict[str, Any]:
    """Copia base física, verificada, cifrada y subida a `base/<momento>/`. La marca
    `verificada.json` va al final: sin ella, la copia no cuenta."""
    sello = _sello(momento)
    s3 = copias.cliente_s3(cfg.copias)
    with tempfile.TemporaryDirectory(prefix="cem-base-") as carpeta:
        destino = Path(carpeta) / "base"
        trabajo = Path(carpeta) / "trabajo"
        trabajo.mkdir()
        tomar_base(cfg.url_copia_continua, destino, sello)
        consorcios = contar_consorcios(cfg.copias.url_backup)
        controles = verificar_base(destino, cfg.copias.url_backup, consorcios)
        objetos = {
            nombre: subir_cifrado(
                s3, cfg.copias, destino / nombre, f"{PREFIJO_BASE}{sello}/{nombre}.age", trabajo,
                comprimir=False,
            )
            for nombre in ARCHIVOS_BASE
        }  # fmt: skip
        resumen = {
            "momento": sello,
            **_rango_wal(destino / "backup_manifest"),
            "objetos": objetos,
            **controles,
        }
        marca = trabajo / MARCA
        marca.write_text(json.dumps(resumen, indent=2))
        clave_marca = f"{PREFIJO_BASE}{sello}/{MARCA}"
        suma = copias.sha256(marca)
        with marca.open("rb") as cuerpo:
            s3.put_object(Bucket=cfg.copias.bucket, Key=clave_marca, Body=cuerpo,
                          Metadata={"sha256": suma})  # fmt: skip
        copias.verificar_objeto(s3, cfg.copias.bucket, clave_marca, suma)
    return resumen


def mensaje_base(resumen: Mapping[str, Any]) -> str:
    bytes_ = sum(o["bytes"] for o in resumen["objetos"].values())
    return (
        f"copia base {resumen['momento']} verificada: {resumen['consorcios']} consorcios,"
        f" {resumen['eventos']} eventos íntegros, {resumen['tablas']} tablas, {bytes_} bytes"
    )


# Receptor -----------------------------------------------------------------------------


class Receptor:
    """`pg_receivewal` como proceso hijo. `--no-loop`: ante un error sale, y la vuelta decide
    (el slot pudo perderse); `--synchronous`: escribe en disco y confirma al servidor cada
    mensaje, así el `restart_lsn` del slot mide lo recibido de verdad."""

    def __init__(self, url_copia_continua: str, carpeta: Path, registro: Path) -> None:
        self.url = url_copia_continua
        self.carpeta = carpeta
        self.registro = registro
        self.proceso: subprocess.Popen | None = None

    def arrancar(self) -> None:
        argumentos, entorno = conexion_libpq(self.url)
        with self.registro.open("ab") as salida:
            self.proceso = subprocess.Popen(
                ["pg_receivewal", f"--directory={self.carpeta}", f"--slot={SLOT}",
                 "--synchronous", "--no-loop", *argumentos],
                env=entorno, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=salida,
            )  # fmt: skip
        log.info("receptor de WAL en marcha", extra={"campos": {"pid": self.proceso.pid}})

    def vivo(self) -> bool:
        return self.proceso is not None and self.proceso.poll() is None

    def final_del_registro(self) -> str:
        if not self.registro.exists():
            return ""
        return self.registro.read_bytes()[-1000:].decode(errors="replace").strip()

    def detener(self) -> None:
        if not self.vivo():
            return
        assert self.proceso is not None
        self.proceso.send_signal(signal.SIGTERM)
        try:
            self.proceso.wait(ESPERA_RECEPTOR)
        except subprocess.TimeoutExpired:
            self.proceso.kill()
            self.proceso.wait()


# Proceso ------------------------------------------------------------------------------


class CopiaContinua:
    def __init__(self, cfg: ConfigCopiaContinua, entorno: Mapping[str, str] = os.environ) -> None:
        self.cfg = cfg
        self.entorno = entorno
        self.carpeta_wal = cfg.directorio / "wal"
        self.trabajo = cfg.directorio / "trabajo"
        self.receptor = Receptor(
            cfg.url_copia_continua, self.carpeta_wal, cfg.directorio / "receptor.log"
        )
        self.s3 = copias.cliente_s3(cfg.copias)
        self.tamano_segmento = 0
        self.ultimo: tuple[int, int] | None = None
        """Último segmento de la cadena ya cubierto por `wal/`; None: la cadena empieza con el
        próximo archivo que aparezca."""
        self.corte: str | None = None
        """Motivo del último corte de la cadena, hasta que lo cierre una copia base posterior."""
        self.cortes = 0
        self.cerrojo = threading.Lock()
        """Protege `corte` y `cortes`. El hilo de la copia base cierra el corte solo si no hubo
        otro mientras copiaba; comparar y borrar tienen que ser una sola operación frente a
        `cortar` (que corre en la vuelta): si no, un corte nuevo entre las dos se perdería."""
        self.observaciones: list[tuple[datetime, int]] = []
        self.atraso = timedelta(0)
        self.al_dia_subida: datetime | None = None
        self.error_subida: str | None = None
        self.defecto: str | None = None
        """Un objeto del almacén que no coincide con lo que se subió: se avisa en el acto."""
        self.ultimo_parcial: datetime | None = None
        self.suma_parcial: str | None = None
        self.ultimo_aviso: datetime | None = None
        self.proxima_revision_base: datetime | None = None
        self.hilo_base: threading.Thread | None = None
        self.ultima_base: dict[str, Any] | None = None
        self.error_base: str | None = None
        self.avisa_continua = bool(entorno.get(VARIABLE_AVISO_CONTINUA))
        self.avisa_base = bool(entorno.get(VARIABLE_AVISO_BASE))

    # Arranque --------------------------------------------------------------------------

    def iniciar(self, ahora: datetime) -> None:
        for variable, avisa in ((VARIABLE_AVISO_CONTINUA, self.avisa_continua),
                                (VARIABLE_AVISO_BASE, self.avisa_base)):  # fmt: skip
            if not avisa:
                log.warning("copia continua sin aviso a Kuma: falta %s", variable)
        self.carpeta_wal.mkdir(parents=True, exist_ok=True)
        self.trabajo.mkdir(parents=True, exist_ok=True)
        estado = estado_servidor(self.cfg.url_copia_continua)
        self.tamano_segmento = estado.tamano_segmento
        self.ultimo = ultimo_segmento_subido(self.s3, self.cfg.copias.bucket, self.tamano_segmento)
        if self.ultimo is None:
            self.cortar("no hay WAL en wal/: la cadena empieza ahora", ahora)
        self.asegurar_slot(estado, ahora)
        self.al_dia_subida = ahora
        self.receptor.arrancar()

    def asegurar_slot(self, estado: EstadoServidor, ahora: datetime) -> None:
        """Crea el slot si falta y lo rehace si está perdido. En los dos casos la cadena quedó
        cortada (el servidor pudo borrar WAL que nadie recibió)."""
        if estado.slot_existe and estado.wal_status != "lost":
            return
        if estado.slot_existe:
            borrar_slot(self.cfg.url_copia_continua)
            self.cortar("slot perdido: se pasó de max_slot_wal_keep_size", ahora)
        elif self.ultimo is not None:
            self.cortar("el slot no existía", ahora)
        self.vaciar(ahora)
        crear_slot(self.cfg.url_copia_continua)
        log.info("slot %s creado", SLOT)

    def vaciar(self, ahora: datetime) -> None:
        """Antes de rehacer el slot: sube lo que quedó del tramo anterior (los completos a `wal/`,
        el parcial a `wal-parcial/`) y borra el parcial. El receptor lo retomaría pidiendo un
        segmento que el servidor ya no tiene, y saldría con error en cada vuelta."""
        for archivo in sorted(self.carpeta_wal.iterdir()):
            if SEGMENTO.match(archivo.name) or HISTORIA.match(archivo.name):
                self.subir_completo(archivo)
        self.subir_parcial(ahora)
        for parcial in self.carpeta_wal.glob("*.partial"):
            parcial.unlink()
        # La cadena nueva empieza con el primer archivo del receptor nuevo.
        self.ultimo = None

    def cortar(self, motivo: str, ahora: datetime) -> None:
        """La cadena de WAL quedó cortada: aviso de caída ya, y una copia base nueva en el acto,
        que vuelve a abrirla."""
        with self.cerrojo:
            self.corte = motivo
            self.cortes += 1
        self.ultimo = None
        self.error_base = None
        log.error("cadena de WAL cortada: %s", motivo)
        self.avisar_continua("down", f"cadena cortada: {motivo}", ahora)

    # Vuelta ----------------------------------------------------------------------------

    def vuelta(self, ahora: datetime) -> None:
        latido.latir()
        estado = estado_servidor(self.cfg.url_copia_continua)
        if not self.receptor.vivo():
            if self.receptor.proceso is not None:
                log.error(
                    "el receptor de WAL terminó con %s: %s",
                    self.receptor.proceso.returncode,
                    self.receptor.final_del_registro(),
                )
            self.asegurar_slot(estado, ahora)
            self.receptor.arrancar()
        elif not estado.slot_existe or estado.wal_status == "lost":
            self.receptor.detener()
            self.asegurar_slot(estado, ahora)
            self.receptor.arrancar()
        self.medir_atraso(estado, ahora)
        self.subir(ahora)
        self.revisar_base(ahora)
        if self.ultimo_aviso is None or ahora - self.ultimo_aviso >= INTERVALO_AVISO:
            problemas = self.problemas(estado, ahora)
            if problemas:
                self.avisar_continua("down", "; ".join(problemas), ahora)
            else:
                self.avisar_continua(
                    "up", f"al día: atraso {int(self.atraso.total_seconds())} s", ahora
                )

    def medir_atraso(self, estado: EstadoServidor, ahora: datetime) -> None:
        """Atraso en tiempo: desde el último momento cuya posición del servidor ya confirmó el
        receptor. Sin slot o sin posición, crece desde la última observación alcanzada."""
        self.observaciones.append((ahora, estado.lsn_servidor))
        recibido = estado.restart_lsn if estado.slot_existe else None
        alcanzadas = [
            i for i, (_, pos) in enumerate(self.observaciones)
            if recibido is not None and pos <= recibido
        ]  # fmt: skip
        if alcanzadas:
            del self.observaciones[: alcanzadas[-1]]
        self.atraso = ahora - self.observaciones[0][0]

    def revisar_continuidad(self, nombre: str, ahora: datetime) -> None:
        """Cada archivo del receptor tiene que seguir a lo que ya cubre `wal/`. Un salto (otro
        contenedor recibió segmentos y no los subió, o se perdió el directorio de trabajo) es un
        corte: lo que falta ya no está en el servidor."""
        tli, numero = numero_segmento(nombre, self.tamano_segmento)
        if self.ultimo is not None and (tli != self.ultimo[0] or numero > self.ultimo[1] + 1):
            self.cortar(
                f"hueco en el WAL: llegó {nombre} y wal/ cubre hasta el segmento"
                f" {self.ultimo[1]} de la línea de tiempo {self.ultimo[0]}",
                ahora,
            )
        if self.ultimo is None:
            # El primer archivo de una cadena nueva: desde acá, sin huecos.
            self.ultimo = (tli, numero - 1)

    def subir(self, ahora: datetime) -> None:
        """Los segmentos completos, en orden, y cada `INTERVALO_PARCIAL` el segmento en curso.
        Una falla se registra y se reintenta en la vuelta siguiente; pasado `ATRASO_MAXIMO` sin
        poder subir, Kuma recibe la caída."""
        try:
            for archivo in sorted(self.carpeta_wal.iterdir()):
                nombre = archivo.name.removesuffix(".partial")
                if HISTORIA.match(archivo.name):
                    self.subir_completo(archivo)
                elif SEGMENTO.match(nombre):
                    self.revisar_continuidad(nombre, ahora)
                    if nombre == archivo.name:
                        self.subir_completo(archivo)
            if self.ultimo_parcial is None or ahora - self.ultimo_parcial >= INTERVALO_PARCIAL:
                self.subir_parcial(ahora)
        except Exception as e:
            self.error_subida = f"{type(e).__name__}: {e}"
            if isinstance(e, copias.ErrorCopia):
                # Un objeto que no coincide no es una falla de red: se avisa ya, no a los 15 min.
                self.defecto = self.error_subida
            log.exception("no se pudo subir el WAL; se reintenta")
            return
        self.error_subida = None
        self.defecto = None
        self.al_dia_subida = ahora

    def subir_completo(self, archivo: Path) -> None:
        clave = f"{PREFIJO_WAL}{archivo.name}.gz.age"
        suma = copias.sha256(archivo)
        existente = copias.metadatos(self.s3, self.cfg.copias.bucket, clave)
        if existente is None:
            subir_cifrado(self.s3, self.cfg.copias, archivo, clave, self.trabajo, comprimir=True,
                          metadatos={"sha256-claro": suma})  # fmt: skip
        elif existente.get("sha256-claro") != suma:
            # El bucket no admite sobrescribir, y un segmento no cambia: es un defecto.
            raise ErrorCopiaContinua(f"{clave} ya está en el almacén con otro contenido")
        else:
            # Ya estaba (un reinicio entre subir y borrar, o una subida cuya relectura falló): el
            # metadato lo escribió este proceso y no prueba nada. Se relee entero contra el SHA-256
            # del cifrado antes de borrar el archivo local; si no coincide, el archivo se queda.
            if "sha256" not in existente:
                raise ErrorCopiaContinua(f"{clave} está en el almacén sin su sha256")
            copias.verificar_objeto(self.s3, self.cfg.copias.bucket, clave, existente["sha256"])
        archivo.unlink()
        if SEGMENTO.match(archivo.name):
            tli, numero = numero_segmento(archivo.name, self.tamano_segmento)
            if self.ultimo is None or (tli, numero) > self.ultimo:
                self.ultimo = (tli, numero)
        log.info("WAL subido", extra={"campos": {"segmento": archivo.name}})

    def subir_parcial(self, ahora: datetime) -> None:
        parciales = sorted(self.carpeta_wal.glob("*.partial"))
        if not parciales:
            return
        actual = parciales[-1]
        foto = self.trabajo / actual.name
        try:
            # Una foto del archivo que el receptor sigue escribiendo: lo que quede a medias al
            # final lo descarta la recuperación (cada registro del WAL lleva su CRC).
            shutil.copyfile(actual, foto)
            suma = copias.sha256(foto)
            if suma != self.suma_parcial:
                segmento = actual.name.removesuffix(".partial")
                clave = f"{PREFIJO_PARCIAL}{segmento}-{_sello(ahora)}.partial.gz.age"
                subir_cifrado(self.s3, self.cfg.copias, foto, clave, self.trabajo,
                              comprimir=True, metadatos={"sha256-claro": suma})  # fmt: skip
                self.suma_parcial = suma
                log.info("WAL en curso subido", extra={"campos": {"segmento": segmento}})
        finally:
            foto.unlink(missing_ok=True)
        self.ultimo_parcial = ahora

    def problemas(self, estado: EstadoServidor, ahora: datetime) -> list[str]:
        problemas = []
        if not self.receptor.vivo():
            problemas.append("el receptor de WAL no está corriendo")
        if not estado.slot_existe:
            problemas.append("el slot no existe")
        elif estado.wal_status in ("unreserved", "lost"):
            problemas.append(f"slot {estado.wal_status}")
        if estado.tope_slot_mb == -1:
            problemas.append("max_slot_wal_keep_size sin tope en la base")
        if self.atraso > ATRASO_MAXIMO:
            problemas.append(f"receptor atrasado {int(self.atraso.total_seconds())} s")
        if self.al_dia_subida is not None and ahora - self.al_dia_subida > ATRASO_MAXIMO:
            problemas.append(f"sin poder subir desde {self.al_dia_subida:%H:%M} UTC:"
                             f" {self.error_subida}")  # fmt: skip
        if self.defecto is not None:
            problemas.append(f"objeto que no coincide: {self.defecto}")
        if self.corte is not None:
            problemas.append(f"cadena cortada: {self.corte}")
        return problemas

    # Copia base ------------------------------------------------------------------------

    def revisar_base(self, ahora: datetime) -> None:
        """Lanza la copia base si la cadena está cortada (en el acto; si esa copia falla, se
        reintenta a `INTERVALO_REVISAR_BASE`) o si en `base/` no hay una verificada de los
        últimos `VIGENCIA_BASE` (se mira cada `INTERVALO_REVISAR_BASE`)."""
        if self.hilo_base is not None and self.hilo_base.is_alive():
            return
        urgente = self.corte is not None and self.error_base is None
        if not urgente and self.proxima_revision_base is not None:
            if ahora < self.proxima_revision_base:
                return
        self.proxima_revision_base = ahora + INTERVALO_REVISAR_BASE
        if self.corte is None:
            ultima = ultima_base_verificada(self.s3, self.cfg.copias.bucket)
            if ultima is not None and ahora - ultima < VIGENCIA_BASE - INTERVALO_REVISAR_BASE:
                return
        self.error_base = None
        self.hilo_base = threading.Thread(
            target=self.hacer_base, args=(ahora, self.cortes), name="copia-base", daemon=True
        )
        self.hilo_base.start()

    def hacer_base(self, ahora: datetime, cortes: int) -> None:
        """Corre en su propio hilo: la vuelta sigue subiendo el WAL mientras tanto. Una falla se
        registra, se avisa a Kuma y se reintenta a `INTERVALO_REVISAR_BASE`. Cierra el corte solo
        si no hubo otro mientras se hacía."""
        try:
            resumen = copia_base(self.cfg, ahora)
        except Exception as e:
            self.error_base = f"{type(e).__name__}: {e}"
            log.exception("la copia base falló; no cuenta")
            self.avisar_base("down", f"copia base fallida: {self.error_base}")
            return
        self.ultima_base = resumen
        with self.cerrojo:
            if self.cortes == cortes:
                self.corte = None
        log.info(mensaje_base(resumen))
        self.avisar_base("up", mensaje_base(resumen))

    def esperar_base(self) -> None:
        if self.hilo_base is not None:
            self.hilo_base.join()

    # Avisos ----------------------------------------------------------------------------

    def avisar_continua(self, estado: str, mensaje: str, ahora: datetime) -> None:
        self.ultimo_aviso = ahora
        if self.avisa_continua:
            copias.avisar_estado(
                VARIABLE_AVISO_CONTINUA, estado, mensaje[:LARGO_MENSAJE], self.entorno
            )

    def avisar_base(self, estado: str, mensaje: str) -> None:
        if self.avisa_base:
            copias.avisar_estado(VARIABLE_AVISO_BASE, estado, mensaje[:LARGO_MENSAJE], self.entorno)

    # Ciclo -----------------------------------------------------------------------------

    def ejecutar(self, detener: threading.Event) -> None:
        self.iniciar(datetime.now(UTC))
        try:
            while not detener.is_set():
                try:
                    self.vuelta(datetime.now(UTC))
                except Exception:
                    # Base caída, almacén que no responde: queda en el registro y se reintenta.
                    # Sin vuelta no hay latido a Kuma, que se pone rojo solo.
                    log.exception("vuelta de la copia continua fallida; se reintenta")
                detener.wait(INTERVALO_VUELTA)
        finally:
            self.receptor.detener()
        log.info("cem-copia-continua detenido")


def main() -> None:
    registros.configurar()
    cfg = ConfigCopiaContinua.desde_entorno()
    detener = threading.Event()
    for senal in (signal.SIGTERM, signal.SIGINT):
        signal.signal(senal, lambda *_: detener.set())
    CopiaContinua(cfg).ejecutar(detener)


if __name__ == "__main__":
    main()
