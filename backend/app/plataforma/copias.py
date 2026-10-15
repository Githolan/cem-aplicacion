"""Copia diaria verificada de la base `cem` (documento 13, §17; plan H1, semana 4: H0b).

`cem-planificador` encola `plataforma.copia_diaria` a las 03:30 UTC (§17.2) y `cem-trabajos`
la ejecuta con esta secuencia; cualquier paso que falle hace fallar el trabajo, que se
reintenta y, agotado, queda `muerto` y visible en `/api/v1/health/cola`:

1. Volcado: `pg_dump -Fc` con el rol `cem_backup` sobre una instantánea exportada
   (`pg_export_snapshot`). En la misma instantánea se calculan las consultas de control, así
   que el volcado y los controles describen exactamente el mismo estado de la base.
2. Verificación (§17.3): el volcado se restaura en un PostgreSQL efímero que este proceso
   levanta en un directorio temporal (solo socket Unix, sin puerto) y se repiten los controles.
   Si algo difiere, la copia no cuenta y no se sube.
3. Cifrado con `age` a la clave pública (`CEM_COPIAS_CLAVE_PUBLICA`). La identidad privada no
   está en la VM (§15.2): el descifrado lo prueba el ensayo trimestral (§17.4).
4. Subida por la API S3 de OCI a `diarias/AAAA-MM-DD/` (y el día 1 también a `mensuales/AAAA-MM/`)
   y relectura del objeto subido: su SHA-256 tiene que ser el del archivo cifrado.

Controles: por cada tabla, cantidad de filas y una huella (md5 de las huellas de cada fila,
ordenadas). Cubre la cantidad de consorcios y cualquier suma de §17.3, porque una fila distinta
cambia la huella de su tabla.

Aviso a Uptime Kuma
-------------------
Una copia que falla deja el trabajo muerto y lo ve el monitor de la cola, pero una que no corre
(planificador o trabajador detenidos, tarea sin encolar) no deja rastro. Por eso, después de una
copia verificada y subida, `cem-trabajos` hace un GET al push de un monitor "Push" de Kuma
(`CEM_COPIAS_AVISO_URL`, con el token del monitor). El monitor espera un latido cada ~26 h:
si una noche no llega, se pone rojo.

El aviso no forma parte de la copia: si falla, la copia igual cuenta (el trabajo queda hecho y
no se repite el volcado), pero queda un ERROR en los registros y `"aviso": "fallido"` en el
resultado del trabajo; sin la variable, un WARNING y `"aviso": "sin_configurar"`. En los dos
casos el monitor de Kuma se pone rojo a las ~26 h, que es lo que tiene que pasar. Solo cuenta
como enviado un 200 con `{"ok": true}`, la respuesta del push de Kuma: un 302 (p. ej. hacia el
inicio de sesión de Cloudflare Access) o una página HTML son fallas. La URL lleva el token,
así que nunca va a un registro ni a un error.
"""

import hashlib
import json
import logging
import os
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import boto3
from botocore.config import Config as ConfigBotocore
from sqlalchemy import Connection, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

log = logging.getLogger("cem.copias")

VARIABLES = {
    "url_backup": "CEM_DATABASE_URL_BACKUP",
    "clave_publica": "CEM_COPIAS_CLAVE_PUBLICA",
    "bucket": "CEM_COPIAS_BUCKET",
    "s3_endpoint": "CEM_OCI_S3_ENDPOINT",
    "s3_region": "CEM_OCI_S3_REGION",
    "s3_clave_id": "CEM_OCI_S3_CLAVE_ID",
    "s3_clave_secreta": "CEM_OCI_S3_CLAVE_SECRETA",
}

VARIABLE_AVISO = "CEM_COPIAS_AVISO_URL"
"""URL del push del monitor de Kuma de la copia. Opcional: sin ella la copia se hace igual."""

TIEMPO_DE_ESPERA_AVISO = 10
"""Segundos para el aviso a Kuma: corto, para que un Kuma colgado no retenga al trabajador
cerca del plazo de reserva (5 minutos en `cem-trabajos`)."""

TROZO = 1024 * 1024
"""Bytes por lectura al calcular sumas: el volcado no se carga entero en memoria."""

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


@dataclass(frozen=True)
class ConfigCopias:
    url_backup: str
    """Conexión con el rol `cem_backup` a la base `cem`."""
    clave_publica: str
    """Clave pública `age1…`; la privada vive en la bóveda y con el custodio."""
    bucket: str
    s3_endpoint: str
    s3_region: str
    s3_clave_id: str
    s3_clave_secreta: str

    @classmethod
    def desde_entorno(cls, entorno: Mapping[str, str] = os.environ) -> "ConfigCopias":
        faltan = [v for v in VARIABLES.values() if not entorno.get(v)]
        if faltan:
            raise ErrorCopia("faltan variables de las copias: " + ", ".join(faltan))
        return cls(**{campo: entorno[v] for campo, v in VARIABLES.items()})


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


def cifrar(volcado: Path, clave_publica: str, destino: Path) -> None:
    """Cifra a la clave pública. Que el resultado se descifra con la identidad privada lo
    prueba `test_copias.py` con un par de prueba; con la real, el ensayo trimestral (§17.4)."""
    _correr(["age", "--recipient", clave_publica, "--output", str(destino), str(volcado)])


def sha256(archivo: Path) -> str:
    suma = hashlib.sha256()
    with archivo.open("rb") as f:
        while trozo := f.read(TROZO):
            suma.update(trozo)
    return suma.hexdigest()


def cliente_s3(cfg: ConfigCopias):
    return boto3.client(
        "s3",
        endpoint_url=cfg.s3_endpoint,
        region_name=cfg.s3_region,
        aws_access_key_id=cfg.s3_clave_id,
        aws_secret_access_key=cfg.s3_clave_secreta,
        config=ConfigBotocore(
            # La API compatible de OCI usa rutas (`/bucket/objeto`), no subdominios por bucket.
            s3={"addressing_style": "path"},
            # Las sumas CRC por defecto de boto3 ≥ 1.36 no son parte de la API compatible de
            # OCI; la integridad la comprueba `verificar_objeto` con SHA-256.
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        ),
    )


def verificar_objeto(s3, bucket: str, clave: str, suma: str) -> None:
    """Relee el objeto subido y compara su SHA-256."""
    leida = hashlib.sha256()
    cuerpo = s3.get_object(Bucket=bucket, Key=clave)["Body"]
    for trozo in cuerpo.iter_chunks(TROZO):
        leida.update(trozo)
    if leida.hexdigest() != suma:
        raise ErrorCopia(f"el objeto subido {clave} no coincide con la copia cifrada")


def claves_destino(base: str, ventana: datetime) -> list[str]:
    """`diarias/AAAA-MM-DD/…`; la del día 1 también va a `mensuales/AAAA-MM/…` (§17.2)."""
    nombre = f"{base}-{ventana:%Y%m%dT%H%M%SZ}.dump.age"
    claves = [f"diarias/{ventana:%Y-%m-%d}/{nombre}"]
    if ventana.day == 1:
        claves.append(f"mensuales/{ventana:%Y-%m}/{nombre}")
    return claves


def copia_diaria(cfg: ConfigCopias, ventana: datetime) -> dict[str, Any]:
    """Vuelca, verifica, cifra y sube. Devuelve el resumen que queda en el trabajo."""
    with tempfile.TemporaryDirectory(prefix="cem-copia-") as carpeta:
        volcado = Path(carpeta) / "cem.dump"
        cifrado = Path(carpeta) / "cem.dump.age"
        esperados = volcar(cfg.url_backup, volcado)
        verificar_volcado(volcado, esperados)
        cifrar(volcado, cfg.clave_publica, cifrado)
        suma = sha256(cifrado)
        s3 = cliente_s3(cfg)
        base = make_url(cfg.url_backup).database
        if not base:
            raise ErrorCopia("CEM_DATABASE_URL_BACKUP no nombra la base")
        claves = claves_destino(base, ventana)
        for clave in claves:
            with cifrado.open("rb") as cuerpo:
                s3.put_object(Bucket=cfg.bucket, Key=clave, Body=cuerpo, Metadata={"sha256": suma})
            verificar_objeto(s3, cfg.bucket, clave, suma)
        return {
            "objetos": claves,
            "bytes": cifrado.stat().st_size,
            "sha256": suma,
            "tablas": len(esperados),
            "filas": sum(filas for filas, _ in esperados.values()),
        }


# Aviso a Uptime Kuma ------------------------------------------------------------------


class ErrorAviso(Exception):
    """Kuma no confirmó el latido. Nunca lleva la URL: tiene el token del monitor."""


class _SinRedirecciones(urllib.request.HTTPRedirectHandler):
    # Una redirección no es un latido: es Access o una URL mal copiada.
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_ABRIDOR_AVISO = urllib.request.build_opener(_SinRedirecciones)


def url_aviso(url: str, resumen: Mapping[str, Any]) -> str:
    """La URL del push con `status=up` y el resumen como mensaje (Kuma toma como caída
    cualquier `status` distinto de `up`). Reemplaza la consulta que trae la URL copiada del
    panel (`?status=up&msg=OK&ping=`)."""
    partes = urllib.parse.urlsplit(url)
    if partes.scheme not in ("http", "https") or not partes.netloc:
        raise ErrorAviso(f"{VARIABLE_AVISO} no es una URL http(s)")
    mensaje = (
        f"copia verificada: {resumen['tablas']} tablas, {resumen['filas']} filas,"
        f" {resumen['bytes']} bytes"
    )
    consulta = urllib.parse.urlencode({"status": "up", "msg": mensaje, "ping": ""})
    return urllib.parse.urlunsplit(partes._replace(query=consulta, fragment=""))


def avisar(url: str, resumen: Mapping[str, Any], espera: float = TIEMPO_DE_ESPERA_AVISO) -> None:
    """GET al push de Kuma. Sin un 200 con `{"ok": true}` → `ErrorAviso`."""
    destino = url_aviso(url, resumen)
    try:
        with _ABRIDOR_AVISO.open(destino, timeout=espera) as respuesta:
            estado, cuerpo = respuesta.status, respuesta.read(4096)
    except urllib.error.HTTPError as e:  # incluye las redirecciones que no se siguen
        raise ErrorAviso(f"Kuma respondió {e.code}") from None
    except (urllib.error.URLError, OSError) as e:
        motivo = e.reason if isinstance(e, urllib.error.URLError) else e
        raise ErrorAviso(f"no se pudo llegar a Kuma: {type(motivo).__name__}: {motivo}") from None
    try:
        confirmado = estado == 200 and json.loads(cuerpo).get("ok") is True
    except (ValueError, AttributeError):
        confirmado = False
    if not confirmado:
        raise ErrorAviso(f"Kuma respondió {estado} sin confirmar el latido")


def avisar_copia(resumen: Mapping[str, Any], entorno: Mapping[str, str] = os.environ) -> str:
    """Avisa la copia hecha a Kuma. Devuelve `enviado`, `fallido` o `sin_configurar`; nunca
    lanza: la copia ya está verificada y subida, y repetirla no arregla el aviso."""
    url = entorno.get(VARIABLE_AVISO)
    if not url:
        log.warning("copia hecha sin aviso a Kuma: falta %s", VARIABLE_AVISO)
        return "sin_configurar"
    try:
        avisar(url, resumen)
    except ErrorAviso as e:
        log.error("copia hecha pero el aviso a Kuma falló: %s", e)
        return "fallido"
    log.info("copia avisada a Kuma")
    return "enviado"
