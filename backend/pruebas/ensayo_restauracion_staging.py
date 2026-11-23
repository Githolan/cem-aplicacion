"""Ensayo PA26 de staging: restauración a un momento entre dos marcas, cronometrada (RNF06).

H6 del piloto (documento 13, §17.4 y RNF06: RPO de 15 minutos y RTO de 4 horas; H2c §6 del
repositorio CEM). El 27-10-2026 pasó con la base en `0038`; este script lo repite sobre el
esquema actual y deja el procedimiento escrito, en lugar de un orquestador suelto. Corre **en la
PC**, habla con la VM por `ssh` y no escribe nada en producción:

1. **Guarda dura.** Los contenedores salen de las etiquetas de Coolify (`coolify.projectName` =
   `consorcio-en-mano-staging` y `coolify.resourceName` = `cem-copia-continua-staging`,
   `cem-backend-staging` y `cem-postgres-staging`), nunca de un nombre pasado a mano; un recurso
   con dos contenedores (despliegue a medias) se niega. Además: la copia continua y el backend
   tienen la misma imagen; el `CEM_ORIGEN_PUBLICO` del backend y el `CEM_COPIAS_BUCKET` de la
   copia continua terminan en `-staging`; y el host de `CEM_DATABASE_URL` del backend es un
   nombre de red de `cem-postgres-staging`. La versión de la base tiene que ser
   `--version-esperada`.
2. **Marca A.** En `cem-backend-staging`, un consorcio nuevo `CEM LAB — Ensayo PA26 <corrida>`
   (`scripts.alta_consorcio`, una unidad) y una decisión de soporte con la descripción
   `PA26 <corrida> marca A` (`nucleo.registrar_decision`: una fila y un evento de auditoría).
3. **RPO observado** (corregido por el hallazgo P2 de Codex en #287: un parcial subido después
   de A puede no tener A si el receptor está atrasado). Se toma la posición de A
   (`pg_current_wal_flush_lsn()` apenas A vuelve confirmada); se espera a que el receptor la
   haya escrito y sincronizado (`flush_lsn` de `pg_stat_replication` del slot
   `cem_copia_continua`, con `pg_receivewal --synchronous`) y se anota ese momento; recién un
   parcial (`wal-parcial/<segmento>-<sello>.partial.gz.age`) con sello posterior a ese momento,
   o el segmento completo, cuenta. Se informa el tiempo desde que A quedó confirmada hasta que
   se ve en el bucket (y hasta su `LastModified`). **La prueba de que ese artefacto tiene A** es
   el paso 7: una segunda restauración con solo lo que había en el almacén en ese momento.
4. **Marca B**, igual, y `pg_switch_wal()` (superusuario, por `psql`): el segmento que tiene a B
   se completa y `cem-copia-continua-staging` lo sube a `wal/`. Se espera a que esté.
5. **Restauración cronometrada** desde el inicio de la descarga hasta la base promovida:
   - la copia base verificada más nueva anterior a A (`base/<momento>/verificada.json`) y los
     segmentos de `wal/` desde su inicio hasta el de B, bajados **cifrados** por
     `cem-copia-continua-staging` (su clave S3) a una carpeta temporal de la VM;
   - un contenedor efímero de la imagen del backend **sin red** recibe la identidad `age` por la
     entrada estándar, la deja solo en su tmpfs, descifra por tubería a un volumen **tmpfs**
     (memoria) y la borra; nada en claro toca el disco. Ese contenedor sigue vivo, durmiendo,
     hasta la limpieza: un volumen tmpfs se vacía cuando ningún contenedor lo monta;
   - `postgres:18-alpine` (el de `cem-postgres-staging`), sin red, arranca sobre ese volumen con
     `recovery_target_time` en el punto medio entre A y B y `recovery_target_action=promote`;
   - el reloj se detiene cuando el registro dice `database system is ready to accept
     connections` después de la línea de promoción (`selected new timeline ID`). El plazo es
     uno solo, desde el inicio de la descarga: la espera de la promoción recibe lo que queda del
     RTO, y el total se compara con el RTO (hallazgo P2 de Codex en #287).
6. **Controles en la base restaurada**, cada uno con su control en la base real (solo lectura):
   A sí y B no (en la real, las dos); la cadena de auditoría con la fórmula de
   `copia_continua.CADENA` (total, enlaces rotos, hashes distintos y el control con la acción
   alterada, que tiene que dar todos distintos); la cantidad de consorcios y de eventos (la real,
   al menos los mismos, y al menos una decisión más: B, porque las decisiones de soporte no
   escriben eventos desde `0032`); y las tablas de `0119` a `0128` (sacadas de las migraciones)
   con la RLS activa, la forzada y la cantidad de políticas iguales a las de la base real.
7. **Comprobación del RPO:** otra restauración, sin objetivo (aplica todo y se promueve), con la
   misma copia base, los segmentos completos de `wal/` que había en el momento del RPO y el
   parcial contado como su segmento; tiene que tener A (y no B, escrita después).
8. **Copia diaria** (`--diaria`): el objeto de hoy de `diarias/AAAA-MM-DD/` que registró el
   trabajo `plataforma.copia_diaria` se baja cifrado, se compara su SHA-256 con el del trabajo,
   se descifra igual que arriba y se restaura con `pg_restore --no-owner --no-acl` (lo mismo que
   `copias.verificar_volcado`) en otro `postgres:18-alpine` sin red; las tablas y filas tienen
   que ser las que el trabajo registró.
9. **Limpieza** siempre, también ante un fallo: contenedores, volúmenes y la carpeta cifrada de la
   VM; después se comprueba que no queda ninguno (`docker ps -a`, `docker volume ls`, `test -e`).

**La identidad privada.** Es `cem-clave-copias-age`, en la bóveda `cem-claves` de OCI (la misma
de producción: staging cifra a la misma pública). Con `--identidad-boveda`, este proceso la lee
con el SDK de OCI y el perfil `DEFAULT` de la PC, la tiene solo en memoria y la escribe en la
entrada estándar del `ssh` del contenedor que descifra: nunca va a un argumento, a un archivo ni
a la pantalla. Por eso se corre con el Python que tiene el SDK (`C:/tmp/cemoci/v`, el de los
scripts de OCI); sin `--identidad-boveda`, se niega antes de escribir nada.

    C:/tmp/cemoci/v/Scripts/python.exe backend/pruebas/ensayo_restauracion_staging.py \\
        --identidad-boveda --version-esperada 0128 --diaria

Sin `--ejecutar` solo hace la guarda y lo que leería (descubre contenedores, versión, copia base
y segmentos); con `--ejecutar`, todo. Imprime un resumen JSON. Sale con 0 solo si todo dio lo
esperado.

**Qué deja en staging.** Un consorcio `CEM LAB — Ensayo PA26 <corrida> (SINTETICO, PRUEBA, NO
REAL)` con una unidad y dos decisiones de soporte, y sus eventos de auditoría (de solo agregar).
Ninguna persona, sesión ni correo. No toca la configuración de staging ni de Coolify. La
restauración nunca va sobre la base de staging: va a un PostgreSQL efímero y separado.

No va en la imagen: corre en la PC. Las funciones puras (nombres de segmento, elección de la
copia base y del WAL, punto medio, tablas de las migraciones, guardas) tienen pruebas en
`tests/test_ensayo_restauracion.py`.
"""

import argparse
import base64
import json
import re
import shlex
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

SSH = ["ssh", "-o", "BatchMode=yes"]
SUFIJO_STAGING = "-staging"
IMAGEN_POSTGRES = "postgres:18-alpine"
"""La de `cem-postgres-staging` (AD-08): la copia física se abre con el mismo musl."""
UID_POSTGRES = 70
"""Usuario `postgres` de la imagen alpine: dueño de los datos restaurados."""
TAMANO_SEGMENTO = 16 * 1024 * 1024
"""`show wal_segment_size` de staging (16MB, medido el 23-11-2026); se vuelve a leer al correr."""
MIGRACIONES_NUEVAS = range(119, 129)
"""`0119` a `0128`: P0 en Modo Consejo, lo que el 27-10 no existía."""
PREFIJO_CARPETA = "/tmp/cem-pa26-"
NOMBRE_EFIMERO = "cem-pa26-{corrida}-{que}"
TMPFS_MAXIMO = "2g"
"""Tope del volumen en memoria (el clúster de staging pesa unas decenas de MB)."""
ESPERA_RPO_S = 15 * 60
"""Lo más que se espera al WAL de A: el RPO de RNF06."""
ESPERA_SEGMENTO_S = 10 * 60
"""Espera de preparación (el segmento de B en el bucket): no mide ningún umbral de RNF06."""
TOPE_PASO_S = 3600
"""Tope de un paso por `ssh` (bajar, descifrar, `pg_restore`), acotado además por lo que quede
del plazo total del camino al que pertenece (`tope_paso`)."""
ESPERA_ARRANQUE_DIARIA_S = 300
ESPERA_PROMOCION_S = 4 * 3600
"""El RTO de RNF06: pasado esto, el ensayo falla."""
SONDEO_S = 5
PROYECTO_STAGING = "consorcio-en-mano-staging"
COPIA, BACKEND, POSTGRES = (
    "cem-copia-continua-staging",
    "cem-backend-staging",
    "cem-postgres-staging",
)
"""Los nombres de recurso de Coolify (`coolify.resourceName`), los de `REGISTRO_RECURSOS`."""
SLOT_RECEPTOR = "cem_copia_continua"
"""El slot de `cem-copia-continua` (`copia_continua.SLOT`)."""
SECRETO = "cem-clave-copias-age"
BOVEDA = "cem-claves"
COMPARTIMENTO = "consorcio-en-mano"
SEGMENTO = re.compile(r"^[0-9A-F]{24}$")
CREAR_TABLA = re.compile(r"create table (?:if not exists )?([a-z_]+\.[a-z_]+)", re.IGNORECASE)


class Negado(Exception):
    """La guarda: algo no es de staging, o falta lo que hace falta. No se escribió nada."""


class Fallo(Exception):
    """Un paso no dio lo esperado."""


# Funciones puras -----------------------------------------------------------------------------


def lsn(valor: str) -> int:
    alto, bajo = valor.split("/")
    return (int(alto, 16) << 32) | int(bajo, 16)


def segmento_de(linea: int, posicion: int, tamano: int = TAMANO_SEGMENTO) -> str:
    """El nombre del archivo de WAL que tiene la posición `posicion` (como `pg_walfile_name`)."""
    numero = posicion // tamano
    por_id = 0x100000000 // tamano
    return f"{linea:08X}{numero // por_id:08X}{numero % por_id:08X}"


def numero_de(nombre: str, tamano: int = TAMANO_SEGMENTO) -> tuple[int, int]:
    por_id = 0x100000000 // tamano
    return int(nombre[:8], 16), int(nombre[8:16], 16) * por_id + int(nombre[16:24], 16)


def elegir_base(marcas: dict[str, dict], antes_de: datetime) -> tuple[str, dict]:
    """La copia base verificada más nueva tomada antes de `antes_de`. `marcas` es
    {momento `AAAAMMDDTHHMMSSZ`: contenido de `verificada.json`}."""
    candidatas = sorted(
        (m for m in marcas if datetime.strptime(m, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
         < antes_de),
        reverse=True,
    )  # fmt: skip
    if not candidatas:
        raise Fallo(f"no hay copia base verificada anterior a {antes_de.isoformat()}")
    return candidatas[0], marcas[candidatas[0]]


def segmentos_necesarios(claves: list[str], desde: str, hasta: str) -> list[str]:
    """Las claves de `wal/` de la misma línea de tiempo entre `desde` y `hasta` (incluidos), y
    los `.history`. Falla si falta alguno en el medio: la recuperación se cortaría ahí."""
    tli = numero_de(desde)[0]
    inicio, fin = numero_de(desde)[1], numero_de(hasta)[1]
    hay: dict[int, str] = {}
    historias = []
    for clave in claves:
        nombre = clave.removeprefix("wal/").removesuffix(".gz.age")
        if re.fullmatch(r"[0-9A-F]{8}\.history", nombre):
            historias.append(clave)
        elif SEGMENTO.match(nombre):
            t, n = numero_de(nombre)
            if t == tli and inicio <= n <= fin:
                hay[n] = clave
    faltan = [n for n in range(inicio, fin + 1) if n not in hay]
    if faltan:
        raise Fallo(f"faltan {len(faltan)} segmentos de WAL en el bucket entre {desde} y {hasta}")
    return sorted(historias) + [hay[n] for n in range(inicio, fin + 1)]


def punto_medio(despues_de_a: datetime, antes_de_b: datetime) -> datetime:
    if not despues_de_a < antes_de_b:
        raise Fallo("la marca B no es posterior a la marca A")
    return despues_de_a + (antes_de_b - despues_de_a) / 2


def tablas_de_migraciones(carpeta: Path, numeros=MIGRACIONES_NUEVAS) -> list[str]:
    """Las tablas que crean las migraciones `numeros` (solo en `upgrade`)."""
    tablas: set[str] = set()
    for numero in numeros:
        [archivo] = carpeta.glob(f"{numero:04d}_*.py")
        texto = archivo.read_text(encoding="utf-8")
        subir = texto.split("def downgrade", 1)[0]
        tablas.update(t.lower() for t in CREAR_TABLA.findall(subir))
    return sorted(tablas)


def es_staging(valor: str) -> bool:
    """Un nombre de staging: su primer nivel (o él mismo) termina en `-staging`."""
    return bool(valor) and valor.split(".")[0].endswith(SUFIJO_STAGING)


def host_publico(origen: str) -> str:
    return re.sub(r"^https?://", "", origen).split("/")[0].split(":")[0]


def parcial_posterior(claves: list[str], segmento: str, momento: datetime) -> str | None:
    """Un parcial del `segmento` con sello posterior al segundo de `momento`, o un segmento
    completo igual o posterior.

    `momento` tiene que ser el instante en que el receptor ya **había escrito y sincronizado** la
    posición de A (`flush_lsn` de `pg_stat_replication` ≥ la posición de A, con `pg_receivewal
    --synchronous`), no el de la confirmación de A: con el receptor atrasado, un parcial subido
    después de A puede tener solo lo anterior a A (hallazgo P2 de Codex en #287). La foto del
    parcial se toma después de su sello (`subir_parcial` sella con el momento de la vuelta y copia
    después), y del mismo segundo podría ser anterior: por eso el sello tiene que ser
    estrictamente posterior al segundo de `momento`."""
    sello_minimo = f"{momento.astimezone(UTC):%Y%m%dT%H%M%SZ}"
    linea, numero = numero_de(segmento)
    for clave in sorted(claves):
        nombre = clave.split("/", 1)[1]
        if clave.startswith("wal-parcial/"):
            m = re.fullmatch(r"([0-9A-F]{24})-(\d{8}T\d{6}Z)\.partial\.gz\.age", nombre)
            if m and m.group(1) == segmento and m.group(2) > sello_minimo:
                return clave
        elif clave.startswith("wal/"):
            completo = nombre.removesuffix(".gz.age")
            if SEGMENTO.match(completo) and numero_de(completo) >= (linea, numero):
                return clave
    return None


def artefactos_al_rpo(claves_wal: list[str], desde: str, encontrado: str) -> list[str]:
    """Lo que había en el almacén en el momento del RPO para llegar a A: los segmentos completos
    desde la copia base y, si lo que se encontró es un parcial del segmento `S`, los completos
    hasta `S - 1` y ese parcial (que la restauración usa como el segmento `S`). Con esto se
    restaura otra vez, sin el WAL que se subió después, y se comprueba que A está."""
    nombre = encontrado.split("/", 1)[1]
    if encontrado.startswith("wal/"):
        return segmentos_necesarios(claves_wal, desde, nombre.removesuffix(".gz.age"))
    segmento = nombre[:24]
    linea, numero = numero_de(segmento)
    if numero_de(desde) > (linea, numero):
        raise Fallo(f"el parcial {segmento} es anterior a la copia base ({desde})")
    if numero_de(desde) == (linea, numero):
        return [encontrado]
    anterior = segmento_de(linea, (numero - 1) * TAMANO_SEGMENTO)
    return segmentos_necesarios(claves_wal, desde, anterior) + [encontrado]


def restante(inicio: float, ahora: float, limite: float) -> float:
    """Lo que queda del plazo total `limite` (segundos) que empezó en `inicio`. Sin plazo, falla:
    la espera de la promoción no empieza con un plazo nuevo (hallazgo P2 de Codex en #287)."""
    queda = limite - (ahora - inicio)
    if queda <= 0:
        raise Fallo(f"pasó el plazo total de {limite:.0f} s (RTO o RPO) antes del paso siguiente")
    return queda


def exigir_rto(segundos: float, limite: float) -> None:
    if segundos > limite:
        raise Fallo(f"la restauración tardó {segundos:.1f} s, más que el RTO de {limite:.0f} s")


def exigir_rpo(segundos: float, limite: float) -> None:
    """El RPO medido desde la confirmación de A no puede pasar el de RNF06 (hallazgo P2 de Codex
    en #287, tercera vuelta: antes se registraba sin compararlo)."""
    if segundos > limite:
        raise Fallo(f"el WAL de A tardó {segundos:.1f} s en el almacén, más que el RPO de"
                    f" {limite:.0f} s")  # fmt: skip


def tope_paso(inicio: float, ahora: float, limite: float, maximo: float = TOPE_PASO_S) -> float:
    """El tope de un paso dentro de un camino con plazo total: el menor entre `maximo` y lo que
    queda del plazo (falla si no queda nada). Ningún paso recibe un plazo nuevo."""
    return min(maximo, restante(inicio, ahora, limite))


def ajustes_recuperacion(objetivo: datetime | None, wal: str) -> str:
    """Lo que se agrega a `postgresql.auto.conf` de la copia (manda sobre lo anterior). Los
    mismos criterios que `copia_continua.AJUSTES_RESTAURADA`. Sin `objetivo`, aplica todo el WAL
    que haya y se promueve (la restauración con lo disponible en el momento del RPO)."""
    momento = (
        {}
        if objetivo is None
        else {"recovery_target_time": objetivo.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f+00")}
    )
    ajustes = {
        "listen_addresses": "",
        "hot_standby": "off",
        **momento,
        "recovery_target_action": "promote",
        "restore_command": f"test -f {wal}/%f.gz && gunzip -c {wal}/%f.gz > %p",
        "archive_mode": "off",
        "fsync": "off",
        "max_wal_senders": "0",
        "logging_collector": "off",
        "shared_buffers": "32MB",
        "max_connections": "10",
    }
    lineas = ["", "# ensayo PA26: restauración efímera"]
    lineas += [f"{k} = '{v.replace(chr(39), chr(39) * 2)}'" for k, v in ajustes.items()]
    return "\n".join(lineas) + "\n"


# Programas que corren en los contenedores ---------------------------------------------------

PROGRAMA_MARCA = r"""
import argparse, json, os, sys
from datetime import date
from urllib.parse import urlsplit
from uuid import UUID
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

p = argparse.ArgumentParser()
p.add_argument("--consorcio", type=UUID, required=True)
p.add_argument("--corrida", required=True)
p.add_argument("--marca", choices=("A", "B"), required=True)
p.add_argument("--base", required=True)
a = p.parse_args()
host = urlsplit(os.environ.get("CEM_ORIGEN_PUBLICO", "")).hostname or ""
if not host.split(".")[0].endswith("-staging"):
    sys.exit(f"NEGADO: CEM_ORIGEN_PUBLICO no es de staging: {host!r}")
url = os.environ["CEM_DATABASE_URL"]
if make_url(url).host != a.base:
    sys.exit(f"NEGADO: CEM_DATABASE_URL no apunta a {a.base!r}")
from app.db import transaccion
motor = create_engine(url, poolclass=NullPool)
nombre = f"CEM LAB — Ensayo PA26 {a.corrida} (SINTETICO, PRUEBA, NO REAL)"
if a.marca == "A":
    from scripts import alta_consorcio
    archivo = alta_consorcio.Archivo.model_validate({
        "consorcio": {"id": str(a.consorcio), "nombre": nombre,
                      "zona_horaria": "America/Argentina/Buenos_Aires", "jurisdiccion": "caba"},
        "unidades": [{"designacion": "LAB-1"}]})
    r = alta_consorcio.dar_de_alta(motor, archivo, simular=False)
    if r.estado != "creado":
        sys.exit(f"NEGADO: el consorcio {a.consorcio} ya existe")
with transaccion(motor, persona_id=None, consorcio_id=a.consorcio) as conn:
    if conn.execute(text("select nombre from nucleo.consorcio where id = :c"),
                    {"c": a.consorcio}).scalar_one() != nombre:
        sys.exit("NEGADO: el consorcio no es el del ensayo")
    antes = conn.execute(text("select clock_timestamp()")).scalar_one()
    hoy = conn.execute(text("select nucleo.hoy_del_consorcio(:c)"), {"c": a.consorcio}).scalar_one()
    decision = conn.execute(
        text("select nucleo.registrar_decision('asamblea', :h, :d, :r, null, 'soporte')"),
        {"h": hoy, "d": f"PA26 {a.corrida} marca {a.marca}",
         "r": f"ENSAYO PA26 SINTETICO {a.corrida} — SIN VALIDEZ JURIDICA"}).scalar_one()
with motor.connect() as conn:
    despues = conn.execute(text("select clock_timestamp()")).scalar_one()
motor.dispose()
print(json.dumps({"decision": str(decision), "antes": antes.isoformat(),
                  "despues": despues.isoformat()}))
"""
"""Corre en `cem-backend-staging` (`python -`): la marca, con la guarda dentro del contenedor."""

PROGRAMA_ALMACEN = r"""
import io, json, sys, tarfile
from app.plataforma import copias
cfg = copias.ConfigCopias.desde_entorno()
if not cfg.bucket.endswith("-staging"):
    sys.exit(f"NEGADO: el bucket no es de staging: {cfg.bucket!r}")
s3 = copias.cliente_s3(cfg)
orden = sys.argv[1]
if orden == "listar":
    salida = []
    for prefijo in sys.argv[2:]:
        for pagina in s3.get_paginator("list_objects_v2").paginate(Bucket=cfg.bucket,
                                                                  Prefix=prefijo):
            salida += [{"clave": o["Key"], "bytes": o["Size"],
                        "modificado": o["LastModified"].isoformat()}
                       for o in pagina.get("Contents", [])]
    print(json.dumps(salida))
elif orden == "leer":
    sys.stdout.write(s3.get_object(Bucket=cfg.bucket, Key=sys.argv[2])["Body"].read().decode())
elif orden == "bajar":
    claves = json.loads(sys.argv[2])
    with tarfile.open(fileobj=sys.stdout.buffer, mode="w|") as tar:
        for clave in claves:
            cuerpo = s3.get_object(Bucket=cfg.bucket, Key=clave)["Body"].read()
            info = tarfile.TarInfo(clave)
            info.size, info.mode = len(cuerpo), 0o644
            tar.addfile(info, io.BytesIO(cuerpo))
else:
    sys.exit(f"orden desconocida {orden!r}")
"""
"""Corre en `cem-copia-continua-staging`: lista, lee una marca o baja objetos **cifrados**
como un tar por la salida estándar. Nunca descifra (ese contenedor no tiene la identidad)."""

DESCIFRAR_PITR = r"""
set -eu
umask 077
cat > /run/clave/identidad
test -s /run/clave/identidad
mkdir -p /restaurar/datos /restaurar/wal
chmod 700 /restaurar/datos
age -d -i /run/clave/identidad /cifrado/base/base.tar.gz.age | tar -xz -C /restaurar/datos
mkdir -p /restaurar/datos/pg_wal
age -d -i /run/clave/identidad /cifrado/base/pg_wal.tar.gz.age | tar -xz -C /restaurar/datos/pg_wal
for f in /cifrado/wal/*.gz.age; do
  age -d -i /run/clave/identidad -o "/restaurar/wal/$(basename "$f" .age)" "$f"
done
printf 'local all all trust\n' > /restaurar/datos/pg_hba.conf
cat /cifrado/ajustes.conf >> /restaurar/datos/postgresql.auto.conf
touch /restaurar/datos/recovery.signal
rm -f /restaurar/datos/postmaster.pid
chmod 700 /restaurar/datos
echo "descifrado: $(ls /restaurar/wal | wc -l) archivos de WAL"
"""
"""Corre en un contenedor efímero de la imagen del backend, sin red, como el usuario 70. La
identidad llega por la entrada estándar y queda solo en el tmpfs `/run/clave`. El WAL queda
descifrado pero comprimido (`<segmento>.gz`): lo descomprime `restore_command` de a un segmento,
así una semana de WAL no ocupa 16 MB por segmento en la memoria."""

DESCIFRAR_DIARIA = r"""
set -eu
umask 077
cat > /run/clave/identidad
test -s /run/clave/identidad
age -d -i /run/clave/identidad -o /restaurar/cem.dump /cifrado/diaria.dump.age
echo "descifrado: $(wc -c < /restaurar/cem.dump) bytes"
"""

CONTAR_TABLAS = r"""
do $$
declare t text; n bigint; total bigint := 0; tablas int := 0;
begin
  for t in select format('%I.%I', n.nspname, c.relname) from pg_class c
           join pg_namespace n on n.oid = c.relnamespace
           where c.relkind in ('r', 'p') and n.nspname not in ('pg_catalog', 'information_schema')
           and n.nspname not like 'pg\_toast%' loop
    execute 'select count(*) from ' || t into n;
    total := total + n; tablas := tablas + 1;
  end loop;
  raise notice 'CONTEO % %', tablas, total;
end $$;
"""
"""Tablas y filas con la misma lista que `copias.TABLAS` (las cuenta `copia_diaria`)."""


# Hablar con la VM ----------------------------------------------------------------------------


@dataclass
class Vm:
    destino: str

    def correr(self, comando: str, entrada: bytes | str | None = None, *, tope: float = 600,
               binario: bool = False) -> str | bytes:  # fmt: skip
        datos = entrada.encode() if isinstance(entrada, str) else entrada
        hecho = subprocess.run([*SSH, self.destino, comando], input=datos, capture_output=True,
                               timeout=tope)  # fmt: skip
        if hecho.returncode != 0:
            raise Fallo(f"falló en la VM ({hecho.returncode}): {comando[:160]}\n"
                        f"{hecho.stderr.decode(errors='replace')[-1500:]}")  # fmt: skip
        return hecho.stdout if binario else hecho.stdout.decode(errors="replace")

    def psql(self, contenedor: str, sql: str, base: str = "cem") -> list[list[str]]:
        salida = self.correr(
            f"docker exec -i {shlex.quote(contenedor)} psql -U postgres -d {base} -At -F '|'"
            " -v ON_ERROR_STOP=1", sql)  # fmt: skip
        return [linea.split("|") for linea in salida.splitlines() if linea]

    def variable(self, contenedor: str, nombre: str) -> str:
        """Una variable que no es secreta (origen, proceso, bucket)."""
        return self.correr(
            f"docker exec {shlex.quote(contenedor)} printenv {nombre} || true"
        ).strip()


@dataclass
class Staging:
    copia: str
    backend: str
    postgres: str
    imagen: str
    bucket: str
    base: str
    origen: str


def recursos(salida: str) -> dict[str, tuple[str, str]]:
    """{nombre del recurso de Coolify: (contenedor, imagen)} de los contenedores del proyecto
    `PROYECTO_STAGING`, desde `docker inspect` (nombre, imagen, `coolify.resourceName`,
    `coolify.projectName`). Un recurso con dos contenedores (un despliegue a medias) se niega."""
    vistos: dict[str, list[tuple[str, str]]] = {}
    for linea in salida.splitlines():
        partes = linea.split()
        if len(partes) == 4 and partes[3] == PROYECTO_STAGING:
            vistos.setdefault(partes[2], []).append((partes[0].lstrip("/"), partes[1]))
    dobles = sorted(r for r, c in vistos.items() if len(c) > 1)
    if dobles:
        raise Negado(f"recursos con más de un contenedor (¿despliegue en curso?): {dobles}")
    return {r: c[0] for r, c in vistos.items()}


def descubrir(vm: Vm) -> Staging:
    """Los contenedores salen de las etiquetas que pone Coolify, no de un nombre a mano: el
    nombre del contenedor cambia en cada despliegue y el del recurso no."""
    salida = vm.correr(
        "docker ps -q | xargs docker inspect --format '{{.Name}} {{.Config.Image}}"
        ' {{index .Config.Labels "coolify.resourceName"}}'
        " {{index .Config.Labels \"coolify.projectName\"}}'")  # fmt: skip
    hay = recursos(salida)
    faltan = [r for r in (COPIA, BACKEND, POSTGRES) if r not in hay]
    if faltan:
        raise Negado(f"no encuentro {faltan} en el proyecto {PROYECTO_STAGING}")
    copia, imagen = hay[COPIA]
    backend, imagen_backend = hay[BACKEND]
    postgres, imagen_postgres = hay[POSTGRES]
    if imagen != imagen_backend:
        raise Negado(f"la copia continua ({imagen}) y el backend ({imagen_backend}) tienen otra"
                     " imagen: el despliegue no terminó")  # fmt: skip
    if imagen_postgres != IMAGEN_POSTGRES:
        raise Negado(f"{POSTGRES} es {imagen_postgres}, no {IMAGEN_POSTGRES}")
    origen = host_publico(vm.variable(backend, "CEM_ORIGEN_PUBLICO"))
    bucket = vm.variable(copia, "CEM_COPIAS_BUCKET")
    if not es_staging(origen) or not es_staging(bucket):
        raise Negado(f"el origen ({origen!r}) o el bucket ({bucket!r}) no son de staging")
    base = vm.correr(
        f"docker exec {shlex.quote(backend)} python -c 'import os; from sqlalchemy.engine"
        ' import make_url; print(make_url(os.environ["CEM_DATABASE_URL"]).host)\''
    ).strip()
    redes = vm.correr(
        f"docker inspect --format '{{{{json .NetworkSettings.Networks}}}}' {shlex.quote(postgres)}"
    )
    if base not in (postgres, *re.findall(r'"([^"]+)"', redes)):
        raise Negado(f"el host de la base del backend ({base!r}) no es {POSTGRES}")
    return Staging(copia, backend, postgres, imagen, bucket, base, origen)


# El ensayo -----------------------------------------------------------------------------------


@dataclass
class Ensayo:
    vm: Vm
    st: Staging
    identidad: bytes | None
    corrida: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    resumen: dict = field(default_factory=dict)
    limpiar: list[str] = field(default_factory=list)
    rpo: dict = field(default_factory=dict)
    marca_base: dict = field(default_factory=dict)

    @property
    def carpeta(self) -> str:
        return f"{PREFIJO_CARPETA}{self.corrida}"

    def efimero(self, que: str) -> str:
        return NOMBRE_EFIMERO.format(corrida=self.corrida, que=que)

    def ok(self, que: str) -> None:
        self.resumen.setdefault("controles", []).append(que)
        print(f"ok  {que}", flush=True)

    def almacen(self, *argumentos: str) -> str:
        comando = (f"docker exec -i -w /app -e PYTHONPATH=/app {shlex.quote(self.st.copia)}"
                   f" python - {' '.join(shlex.quote(a) for a in argumentos)}")  # fmt: skip
        return self.vm.correr(comando, PROGRAMA_ALMACEN)

    def listar(self, *prefijos: str) -> list[dict]:
        return json.loads(self.almacen("listar", *prefijos))

    def bajar(self, claves: list[str], destino: str, tope: float = TOPE_PASO_S) -> None:
        """Baja las claves cifradas a `destino` en la VM, como un tar desde la copia continua."""
        lista = shlex.quote(json.dumps(claves))
        self.vm.correr(
            f"set -o pipefail; mkdir -p {shlex.quote(destino)} && docker exec -i -w /app -e"
            f" PYTHONPATH=/app {shlex.quote(self.st.copia)} python - bajar {lista}"
            f" | tar -x -C {shlex.quote(destino)} && chmod -R a+rX {shlex.quote(self.carpeta)}",
            PROGRAMA_ALMACEN, tope=tope)  # fmt: skip

    def marca(self, consorcio: uuid.UUID, letra: str) -> dict:
        salida = self.vm.correr(
            f"docker exec -i -w /app -e PYTHONPATH=/app {shlex.quote(self.st.backend)} python -"
            f" --consorcio {consorcio} --corrida {self.corrida} --marca {letra}"
            f" --base {shlex.quote(self.st.base)}", PROGRAMA_MARCA)  # fmt: skip
        datos = json.loads(salida.strip().splitlines()[-1])
        return {"decision": datos["decision"],
                "antes": datetime.fromisoformat(datos["antes"]),
                "despues": datetime.fromisoformat(datos["despues"])}  # fmt: skip

    def real(self, sql: str) -> list[list[str]]:
        return self.vm.psql(self.st.postgres, sql)

    def descifrar(self, guion: str, volumen: str, cifrado: str, que: str,
                  tope: float = TOPE_PASO_S) -> str:  # fmt: skip
        """El contenedor sin red que descifra: la identidad, solo por la entrada estándar, a su
        tmpfs, y se borra al terminar. El contenedor **queda vivo** (durmiendo) hasta la
        limpieza: un volumen tmpfs vive mientras algún contenedor lo tenga montado, y el
        PostgreSQL efímero lo monta después (medido el 23-11-2026: con `docker run --rm` el
        volumen llegaba vacío al PostgreSQL)."""
        if self.identidad is None:
            raise Negado("sin --identidad-boveda no hay con qué descifrar")
        contenedor = self.efimero(f"{que}-descifra")
        self.limpiar.append(f"contenedor:{contenedor}")
        self.vm.correr(
            f"docker run -d --name {contenedor} --network none"
            f" --user {UID_POSTGRES}:{UID_POSTGRES}"
            f" --tmpfs /run/clave:size=1m,uid={UID_POSTGRES},gid={UID_POSTGRES},mode=0700"
            f" -v {shlex.quote(cifrado)}:/cifrado:ro -v {volumen}:/restaurar"
            f" --entrypoint sleep {shlex.quote(self.st.imagen)} infinity >/dev/null")  # fmt: skip
        try:
            salida = self.vm.correr(f"docker exec -i {contenedor} sh -c {shlex.quote(guion)}",
                                    self.identidad, tope=tope)  # fmt: skip
        finally:
            self.vm.correr(f"docker exec {contenedor} rm -f /run/clave/identidad")
        self.vm.correr(f"docker exec {contenedor} sh -c 'test ! -e /run/clave/identidad"
                       " && test -z \"$(ls -A /run/clave)\"'")  # fmt: skip
        return salida + "identidad borrada del tmpfs del contenedor que descifra"

    def volumen(self, que: str) -> str:
        nombre = self.efimero(que)
        self.limpiar.append(f"volumen:{nombre}")
        self.vm.correr(
            f"docker volume create --driver local --opt type=tmpfs --opt device=tmpfs"
            f" --opt o=size={TMPFS_MAXIMO},uid={UID_POSTGRES},gid={UID_POSTGRES},mode=0700"
            f" {nombre} >/dev/null")  # fmt: skip
        return nombre

    def esperar(self, que: str, tope: float, condicion):
        inicio = time.monotonic()
        while time.monotonic() - inicio < tope:
            if (r := condicion()) is not None:
                return r, time.monotonic() - inicio
            time.sleep(SONDEO_S)
        raise Fallo(f"{que}: no llegó en {tope:.0f} s")

    # 1 a 4 ------------------------------------------------------------------------------------

    def marcas(self) -> tuple[dict, dict, str]:
        consorcio = uuid.uuid4()
        self.resumen["consorcio"] = str(consorcio)
        a = self.marca(consorcio, "A")
        # Cota de A: la posición sincronizada cuando A ya volvió confirmada (synchronous_commit por
        # defecto): el registro de la confirmación de A termina en ella o antes.
        [[lsn_a, seg_a]] = self.real(
            "select l, pg_walfile_name(l) from (select pg_current_wal_flush_lsn() as l) x"
        )
        a["lsn"] = lsn_a
        # Un solo plazo para todo el camino del RPO, desde que A volvió confirmada (un instante
        # después de la confirmación real: el control final usa la hora exacta de A).
        inicio_rpo = time.monotonic() - (datetime.now(UTC) - a["despues"]).total_seconds()
        self.ok(f"marca A {a['decision']} confirmada a las {a['despues'].isoformat()}"
                f" (posición {lsn_a}, segmento {seg_a})")  # fmt: skip

        def receptor_alcanzo():
            filas = self.real(
                f"select r.flush_lsn >= '{lsn_a}'::pg_lsn, r.flush_lsn,"
                " extract(epoch from clock_timestamp()) from pg_stat_replication r"
                " join pg_replication_slots s on s.active_pid = r.pid"
                f" where s.slot_name = '{SLOT_RECEPTOR}'")  # fmt: skip
            if filas and filas[0][0] == "t":
                return filas[0][1], datetime.fromtimestamp(float(filas[0][2]), UTC)
            return None

        (flush, alcanzo), _ = self.esperar(
            "el receptor con A sincronizada",
            restante(inicio_rpo, time.monotonic(), ESPERA_RPO_S), receptor_alcanzo)  # fmt: skip
        self.ok(f"el receptor sincronizó {flush} ≥ {lsn_a} a las {alcanzo.isoformat()}"
                f" ({(alcanzo - a['despues']).total_seconds():.1f} s después de A)")  # fmt: skip

        def subido():
            objetos = self.listar("wal-parcial/", "wal/")
            clave = parcial_posterior([o["clave"] for o in objetos], seg_a, alcanzo)
            if clave is None:
                return None
            return clave, next(o for o in objetos if o["clave"] == clave), objetos

        (encontrado, objeto, objetos), _ = self.esperar(
            "el WAL de A en el bucket",
            restante(inicio_rpo, time.monotonic(), ESPERA_RPO_S), subido)  # fmt: skip
        rpo = (datetime.now(UTC) - a["despues"]).total_seconds()
        subido_en = datetime.fromisoformat(objeto["modificado"])
        self.rpo = {
            "clave": encontrado,
            "segmento_a": seg_a,
            "lsn_a": lsn_a,
            "claves_wal": [o["clave"] for o in objetos if o["clave"].startswith("wal/")],
        }
        self.resumen["rpo_observado_s"] = round(rpo, 1)
        self.resumen["rpo_subido_s"] = round((subido_en - a["despues"]).total_seconds(), 1)
        self.resumen["rpo_artefacto"] = encontrado
        exigir_rpo(rpo, ESPERA_RPO_S)
        self.ok(f"el WAL de A está en el bucket ({encontrado}, subido"
                f" {self.resumen['rpo_subido_s']:.0f} s después de A; visto a los {rpo:.0f} s),"
                f" contra un RPO de {ESPERA_RPO_S // 60} min; se comprueba al final restaurando"
                " solo con lo que había en ese momento")  # fmt: skip
        b = self.marca(consorcio, "B")
        [[fin, seg_b]] = self.real(
            "select s, pg_walfile_name(s - 1) from (select pg_switch_wal() as s) x"
        )
        self.ok(f"marca B {b['decision']} a las {b['antes'].isoformat()}; pg_switch_wal() en"
                f" {fin}, segmento {seg_b}")  # fmt: skip
        clave_b = f"wal/{seg_b}.gz.age"
        self.esperar(f"{clave_b} en el bucket", ESPERA_SEGMENTO_S,
                     lambda: True if any(o["clave"] == clave_b for o in self.listar(clave_b))
                     else None)  # fmt: skip
        self.ok(f"{clave_b} subido")
        return a, b, seg_b

    # 5 y 6 ------------------------------------------------------------------------------------

    def base_anterior(self, antes_de: datetime) -> tuple[str, str]:
        marcas = {}
        for o in self.listar("base/"):
            partes = o["clave"].split("/")
            if len(partes) == 3 and partes[2] == "verificada.json":
                marcas[partes[1]] = json.loads(self.almacen("leer", o["clave"]))
        momento, marca = elegir_base(marcas, antes_de)
        self.marca_base = marca
        return momento, segmento_de(marca["linea_de_tiempo"], lsn(marca["inicio_lsn"]))

    def restaurar_con(self, que: str, momento: str, wal: list[str], objetivo: datetime | None,
                      inicio: float, limite: float) -> tuple[str, str, int]:  # fmt: skip
        """Baja la copia base `momento` y las claves `wal` cifradas, las descifra a un tmpfs y
        levanta `postgres:18-alpine` sin red hasta que se promueve. Un parcial va como su
        segmento. La espera de la promoción usa lo que queda del plazo total `limite` desde
        `inicio`, no un plazo nuevo."""
        base = [f"base/{momento}/{n}.age" for n in ("base.tar.gz", "pg_wal.tar.gz")]
        self.limpiar.append(f"carpeta:{self.carpeta}")
        cifrado = f"{self.carpeta}/{que}"
        self.bajar(base + wal, cifrado, tope_paso(inicio, time.monotonic(), limite))
        renombrar = "".join(
            f" && mv {cifrado}/{c} {cifrado}/wal/{c.split('/', 1)[1][:24]}.gz.age"
            for c in wal if c.startswith("wal-parcial/"))  # fmt: skip
        self.vm.correr(f"mkdir -p {cifrado}/wal && mv {cifrado}/base/{momento}/* {cifrado}/base/"
                       f" && rmdir {cifrado}/base/{momento}{renombrar}"
                       f" && cat > {cifrado}/ajustes.conf",
                       ajustes_recuperacion(objetivo, "/restaurar/wal"))  # fmt: skip
        bajado = int(self.vm.correr(f"du -sb {cifrado} | cut -f1").strip())
        volumen = self.volumen(que)
        print(
            self.descifrar(
                DESCIFRAR_PITR, volumen, cifrado, que, tope_paso(inicio, time.monotonic(), limite)
            ).strip(),
            flush=True,
        )
        contenedor = self.efimero(que)
        self.limpiar.append(f"contenedor:{contenedor}")
        self.vm.correr(
            f"docker run -d --name {contenedor} --network none --user {UID_POSTGRES}:{UID_POSTGRES}"
            f" -v {volumen}:/restaurar --entrypoint postgres {IMAGEN_POSTGRES}"
            " -D /restaurar/datos >/dev/null")  # fmt: skip

        def promovida():
            registro = self.vm.correr(f"docker logs {contenedor} 2>&1")
            _, promovido, despues = registro.partition("selected new timeline ID")
            if promovido and "database system is ready to accept connections" in despues:
                return registro
            if self.vm.correr(f"docker inspect -f '{{{{.State.Running}}}}' {contenedor}"
                              ).strip() != "true":  # fmt: skip
                raise Fallo(f"la base restaurada se detuvo:\n{registro[-2000:]}")
            return None

        queda = restante(inicio, time.monotonic(), limite)
        registro, _ = self.esperar("la promoción", queda, promovida)
        return contenedor, registro, bajado

    def restaurar(self, a: dict, b: dict, seg_b: str) -> None:
        objetivo = punto_medio(a["despues"], b["antes"])
        self.resumen["objetivo"] = objetivo.isoformat()
        inicio = time.monotonic()
        momento, desde = self.base_anterior(a["antes"])
        wal = segmentos_necesarios([o["clave"] for o in self.listar("wal/")], desde, seg_b)
        self.resumen["copia_base"] = {
            "momento": momento,
            "inicio_lsn": self.marca_base["inicio_lsn"],
            "segmentos": len(wal),
            "desde": desde,
            "hasta": seg_b,
        }
        contenedor, registro, bajado = self.restaurar_con("pitr", momento, wal, objetivo, inicio,
                                                          ESPERA_PROMOCION_S)  # fmt: skip
        segundos = time.monotonic() - inicio
        parada = [x for x in registro.splitlines() if "recovery stopping" in x]
        self.resumen["restauracion"] = {
            "segundos": round(segundos, 1), "bytes_cifrados_bajados": bajado,
            "parada": parada[0].split("LOG:", 1)[-1].strip() if parada else None,
            "rto_s": ESPERA_PROMOCION_S,
        }  # fmt: skip
        exigir_rto(segundos, ESPERA_PROMOCION_S)
        if not parada:
            raise Fallo("el registro no dice dónde se detuvo la recuperación")
        self.ok(
            f"restaurada y promovida en {segundos:.1f} s, dentro del RTO de"
            f" {ESPERA_PROMOCION_S} s ({bajado / 1e6:.1f} MB cifrados);"
            f" {parada[0].split('LOG:', 1)[-1].strip()}"
        )
        self.controles(contenedor)

    def comprobar_rpo(self, a: dict) -> None:
        """Restaura otra vez, sin objetivo, solo con lo que había en el almacén en el momento del
        RPO (la copia base, los segmentos completos de entonces y el parcial encontrado), y exige
        A. Es la prueba de que el artefacto contado tiene A (hallazgo P2 de Codex en #287); el
        orden «receptor sincronizado antes de la foto» es solo la condición para elegirlo."""
        momento, desde = self.base_anterior(a["antes"])
        wal = artefactos_al_rpo(self.rpo["claves_wal"], desde, self.rpo["clave"])
        inicio = time.monotonic()
        contenedor, _, bajado = self.restaurar_con("rpo", momento, wal, None, inicio,
                                                   ESPERA_PROMOCION_S)  # fmt: skip
        segundos = time.monotonic() - inicio
        consorcio = self.resumen["consorcio"]
        [[con_a, con_b]] = self.vm.psql(contenedor, (
            f"select (select count(*) from nucleo.decision_formal where consorcio_id ="
            f" '{consorcio}' and descripcion = 'PA26 {self.corrida} marca A'),"
            f" (select count(*) from nucleo.decision_formal where consorcio_id ="
            f" '{consorcio}' and descripcion = 'PA26 {self.corrida} marca B')"))  # fmt: skip
        self.resumen["rpo_comprobado"] = {"archivos_wal": len(wal), "ultimo": wal[-1],
                                          "marca_a": int(con_a), "marca_b": int(con_b),
                                          "segundos": round(segundos, 1),
                                          "bytes_cifrados_bajados": bajado}  # fmt: skip
        exigir_rto(segundos, ESPERA_PROMOCION_S)
        if int(con_a) != 1:
            raise Fallo(f"con lo que había al RPO ({wal[-1]}) la restaurada no tiene A: el RPO"
                        " informado no vale")  # fmt: skip
        self.ok(f"con lo que había en el almacén al RPO ({len(wal)} archivos de WAL, el último"
                f" {wal[-1]}) la restaurada tiene A ({con_a}) y no B ({con_b}): el RPO contado"
                " es real")  # fmt: skip

    def controles(self, contenedor: str) -> None:
        consorcio = self.resumen["consorcio"]
        cadena = self.vm.correr(
            f"docker exec -w /app -e PYTHONPATH=/app {shlex.quote(self.st.backend)} python -c 'from"
            " app.plataforma.copia_continua import CADENA; print(CADENA.text)'"
        ).strip()
        marcas = (f"select (select count(*) from nucleo.decision_formal where consorcio_id ="
                  f" '{consorcio}' and descripcion = 'PA26 {self.corrida} marca A'),"
                  f" (select count(*) from nucleo.decision_formal where consorcio_id ="
                  f" '{consorcio}' and descripcion = 'PA26 {self.corrida} marca B'),"
                  " (select count(*) from nucleo.consorcio),"
                  " (select count(*) from nucleo.decision_formal)")  # fmt: skip
        tablas = tablas_de_migraciones(Path(__file__).resolve().parents[1] / "migrations"
                                       / "versions")  # fmt: skip
        lista = ", ".join(f"'{t}'" for t in tablas)
        rls = ("select n.nspname || '.' || c.relname, c.relrowsecurity, c.relforcerowsecurity,"
               " (select count(*) from pg_policy p where p.polrelid = c.oid) from pg_class c"
               " join pg_namespace n on n.oid = c.relnamespace where c.relkind = 'r'"
               f" and n.nspname || '.' || c.relname in ({lista}) order by 1")  # fmt: skip

        def leer(psql) -> dict:
            [[con_a, con_b, consorcios, decisiones]] = psql(marcas)
            [[total, rotos, distintos, control]] = psql(cadena)
            return {"marca_a": int(con_a), "marca_b": int(con_b), "consorcios": int(consorcios),
                    "decisiones": int(decisiones),
                    "eventos": int(total), "rotos": int(rotos), "distintos": int(distintos),
                    "control_cadena": int(control),
                    "tablas_nuevas": {f[0]: f[1:] for f in psql(rls)}}  # fmt: skip

        restaurada = leer(lambda sql: self.vm.psql(contenedor, sql))
        real = leer(self.real)
        self.resumen["restaurada"], self.resumen["real"] = restaurada, real
        if (restaurada["marca_a"], restaurada["marca_b"]) != (1, 0):
            raise Fallo(
                f"la restaurada tiene A={restaurada['marca_a']} y B={restaurada['marca_b']}"
            )
        if (real["marca_a"], real["marca_b"]) != (1, 1):
            raise Fallo(f"control: la real tiene A={real['marca_a']} y B={real['marca_b']}")
        self.ok("la restaurada tiene la marca A y no la B; control: la real tiene las dos")
        if restaurada["rotos"] or restaurada["distintos"] or (
                restaurada["control_cadena"] != restaurada["eventos"]):  # fmt: skip
            raise Fallo(f"cadena de la restaurada: {restaurada}")
        # Las decisiones de soporte no escriben eventos (soporte fuera de la auditoría, `0032` y
        # S2): el control de «la real es posterior» son las decisiones, B y las de después.
        if real["eventos"] < restaurada["eventos"] or (
                real["decisiones"] < restaurada["decisiones"] + 1):  # fmt: skip
            raise Fallo(f"control: la real tiene {real['eventos']} eventos y"
                        f" {real['decisiones']} decisiones; la restaurada, {restaurada['eventos']}"
                        f" y {restaurada['decisiones']}")  # fmt: skip
        self.ok(
            f"cadena íntegra en la restaurada: {restaurada['eventos']} eventos, 0 rotos, 0"
            f" distintos; control con la acción alterada: {restaurada['control_cadena']}"
            f" distintos. La real: {real['eventos']} eventos, {real['decisiones']} decisiones"
            f" (B y las de después) y {real['consorcios']} consorcios; la restaurada,"
            f" {restaurada['decisiones']} decisiones y {restaurada['consorcios']} consorcios"
        )
        faltan = sorted(set(tablas) - set(restaurada["tablas_nuevas"]))
        sin_rls = sorted(t for t, (rls_, forzada, _) in restaurada["tablas_nuevas"].items()
                         if rls_ != "t" or forzada != "t")  # fmt: skip
        distintas = sorted(
            t for t in tablas if restaurada["tablas_nuevas"].get(t) != real["tablas_nuevas"].get(t)
        )
        if faltan or distintas:
            raise Fallo(f"tablas de 0119-0128: faltan {faltan}; distintas de la real {distintas}")
        self.resumen["tablas_nuevas_sin_rls_forzada"] = sin_rls
        self.ok(
            f"las {len(tablas)} tablas de 0119 a 0128 están en la restaurada con la misma RLS,"
            f" RLS forzada y políticas que la real; sin RLS forzada: {sin_rls or 'ninguna'}"
        )

    # 7 ----------------------------------------------------------------------------------------

    def diaria(self) -> None:
        filas = self.real(
            "select resultado->'objetos'->>0, resultado->>'sha256', resultado->>'tablas',"
            " resultado->>'filas', terminado_en from plataforma.jobs"
            " where tipo = 'plataforma.copia_diaria' and estado = 'hecho'"
            " and terminado_en >= date_trunc('day', now() at time zone 'UTC') at time zone 'UTC'"
            " order by terminado_en desc limit 1")  # fmt: skip
        if not filas:
            raise Fallo("no hay copia diaria hecha hoy (UTC)")
        [[clave, suma, tablas, filas_esperadas, terminado]] = filas
        cifrado = f"{self.carpeta}/diaria"
        self.limpiar.append(f"carpeta:{self.carpeta}")
        inicio = time.monotonic()
        self.bajar([clave], cifrado, tope_paso(inicio, time.monotonic(), ESPERA_PROMOCION_S))
        self.vm.correr(f"mv {cifrado}/{clave} {cifrado}/diaria.dump.age")
        leida = self.vm.correr(f"sha256sum {cifrado}/diaria.dump.age | cut -d' ' -f1").strip()
        if leida != suma:
            raise Fallo(f"el SHA-256 de {clave} no es el del trabajo")
        volumen = self.volumen("diaria")
        print(self.descifrar(DESCIFRAR_DIARIA, volumen, cifrado, "diaria",
                             tope_paso(inicio, time.monotonic(), ESPERA_PROMOCION_S)).strip(),
              flush=True)  # fmt: skip
        contenedor = self.efimero("diaria")
        self.limpiar.append(f"contenedor:{contenedor}")
        self.vm.correr(
            f"docker run -d --name {contenedor} --network none --user {UID_POSTGRES}:{UID_POSTGRES}"
            f" -e POSTGRES_HOST_AUTH_METHOD=trust -e PGDATA=/restaurar/datos"
            f" -v {volumen}:/restaurar {IMAGEN_POSTGRES} -c listen_addresses='' -c fsync=off"
            " >/dev/null")  # fmt: skip
        arranque = tope_paso(inicio, time.monotonic(), ESPERA_PROMOCION_S,
                             ESPERA_ARRANQUE_DIARIA_S)  # fmt: skip
        self.esperar("el PostgreSQL de la diaria", arranque, lambda: True if (
            "database system is ready to accept connections" in self.vm.correr(
                f"docker logs {contenedor} 2>&1").split("init process complete", 1)[-1])
            else None)  # fmt: skip
        self.vm.correr(
            f"docker exec {contenedor} sh -c 'createdb -U postgres verificacion && pg_restore"
            " --exit-on-error --no-owner --no-acl -U postgres -d verificacion /restaurar/cem.dump'",
            tope=tope_paso(inicio, time.monotonic(), ESPERA_PROMOCION_S))  # fmt: skip
        segundos = time.monotonic() - inicio
        exigir_rto(segundos, ESPERA_PROMOCION_S)
        salida = self.vm.correr(
            f"docker exec -i {contenedor} psql -U postgres -d verificacion -v ON_ERROR_STOP=1"
            " 2>&1", CONTAR_TABLAS)  # fmt: skip
        m = re.search(r"CONTEO (\d+) (\d+)", salida)
        if m is None:
            raise Fallo(f"no se pudo contar la diaria: {salida[-500:]}")
        obtenidas = (int(m.group(1)), int(m.group(2)))
        self.resumen["diaria"] = {
            "objeto": clave,
            "sha256": suma,
            "terminado_en": terminado,
            "esperado": [int(tablas), int(filas_esperadas)],
            "restaurado": list(obtenidas),
            "segundos": round(segundos, 1),
        }
        if obtenidas != (int(tablas), int(filas_esperadas)):
            raise Fallo(f"la diaria restaurada tiene {obtenidas} y el trabajo registró"
                        f" {(tablas, filas_esperadas)}")  # fmt: skip
        self.ok(f"copia diaria {clave}: SHA-256 igual al del trabajo, descifrada y restaurada en"
                f" {segundos:.1f} s con {obtenidas[0]} tablas y {obtenidas[1]} filas, las que"
                " registró el trabajo")  # fmt: skip

    # 8 ----------------------------------------------------------------------------------------

    def limpieza(self) -> list[str]:
        quedan = []
        for cosa in reversed(self.limpiar):
            tipo, nombre = cosa.split(":", 1)
            orden = {"contenedor": f"docker rm -f {nombre} >/dev/null 2>&1 || true",
                     "volumen": f"docker volume rm {nombre} >/dev/null 2>&1 || true",
                     "carpeta": f"rm -rf {shlex.quote(nombre)}"}[tipo]  # fmt: skip
            self.vm.correr(orden)
        restos = self.vm.correr(
            f"docker ps -a --format '{{{{.Names}}}}' | grep -c 'cem-pa26-{self.corrida}' || true;"
            f" docker volume ls --format '{{{{.Name}}}}' | grep -c 'cem-pa26-{self.corrida}' ||"
            f" true; test -e {self.carpeta} && echo hay || echo no").split()  # fmt: skip
        if restos != ["0", "0", "no"]:
            quedan.append(f"quedaron restos: contenedores {restos[0]}, volúmenes {restos[1]},"
                          f" carpeta {restos[2]}")  # fmt: skip
        # Control: la misma cuenta encuentra un contenedor que sí existe.
        control = self.vm.correr(
            f"docker ps -a --format '{{{{.Names}}}}' | grep -c {shlex.quote(self.st.postgres)}"
            " || true").strip()  # fmt: skip
        self.resumen["limpieza"] = {"restos": restos, "control_cuenta_existente": control}
        if control != "1":
            quedan.append("el control de la limpieza no encuentra el contenedor que existe")
        return quedan


def leer_identidad() -> bytes:
    """La identidad de la bóveda, solo en memoria. Necesita el SDK de OCI (el Python de
    `C:/tmp/cemoci/v`) y el perfil `DEFAULT` de la PC."""
    import oci  # noqa: PLC0415 — solo en la PC del operador

    cfg = oci.config.from_file()
    identidad = oci.identity.IdentityClient(cfg)
    [comp] = [c for c in identidad.list_compartments(cfg["tenancy"]).data
              if c.name == COMPARTIMENTO and c.lifecycle_state == "ACTIVE"]  # fmt: skip
    [secreto] = [s for s in oci.vault.VaultsClient(cfg).list_secrets(comp.id, name=SECRETO).data
                 if s.lifecycle_state == "ACTIVE"]  # fmt: skip
    contenido = oci.secrets.SecretsClient(cfg).get_secret_bundle(secreto.id).data
    texto = base64.b64decode(contenido.secret_bundle_content.content)
    linea = [x for x in texto.splitlines() if x.startswith(b"AGE-SECRET-KEY-")]
    if len(linea) != 1:
        raise Negado(f"el secreto {SECRETO} no tiene una sola identidad age")
    return linea[0] + b"\n"


def main(argumentos: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("--vm", default="oci", help="destino de ssh (por defecto `oci`)")
    p.add_argument("--version-esperada", required=True, help="versión de alembic de staging")
    p.add_argument("--identidad-boveda", action="store_true")
    p.add_argument("--diaria", action="store_true", help="también la copia diaria de hoy")
    p.add_argument("--ejecutar", action="store_true", help="sin esto, solo la guarda")
    a = p.parse_args(argumentos)
    vm = Vm(a.vm)
    try:
        st = descubrir(vm)
        [[version]] = vm.psql(st.postgres, "select version_num from plataforma.alembic_version")
        if version != a.version_esperada:
            raise Negado(f"staging está en {version}, no en {a.version_esperada}")
        [[tamano]] = vm.psql(st.postgres, "show wal_segment_size")
        if tamano != "16MB":
            raise Negado(f"wal_segment_size es {tamano}: este script asume 16MB")
        if a.ejecutar and not a.identidad_boveda:
            raise Negado("--ejecutar necesita --identidad-boveda")
    except Negado as e:
        print(f"NEGADO: {e}", file=sys.stderr)
        return 4
    ensayo = Ensayo(vm, st, None)
    ensayo.resumen.update({"staging": st.__dict__, "version": version,
                           "imagen": st.imagen.rsplit(":", 1)[-1][:7]})  # fmt: skip
    print(f"staging: {json.dumps(ensayo.resumen['staging'])}; base en {version}", flush=True)
    if not a.ejecutar:
        ultima = max(o["clave"] for o in ensayo.listar("base/") if o["clave"].endswith(".json"))
        marca = json.loads(ensayo.almacen("leer", ultima))
        desde = numero_de(segmento_de(marca["linea_de_tiempo"], lsn(marca["inicio_lsn"])))
        wal = [o for o in ensayo.listar("wal/") if SEGMENTO.match(n := o["clave"][4:28])
               and numero_de(n) >= desde]  # fmt: skip
        print(
            json.dumps(
                {
                    "simulado": True,
                    "copia_base": ultima,
                    "segmentos_desde_ella": len(wal),
                    "bytes_cifrados": sum(o["bytes"] for o in wal),
                },
                indent=2,
            )
        )
        return 0
    ensayo.identidad = leer_identidad()
    codigo = 0
    try:
        a_, b_, seg_b = ensayo.marcas()
        ensayo.restaurar(a_, b_, seg_b)
        ensayo.comprobar_rpo(a_)
        if a.diaria:
            ensayo.diaria()
    except Fallo as e:
        ensayo.resumen["fallo"] = str(e)
        print(f"FALLÓ: {e}", file=sys.stderr)
        codigo = 1
    finally:
        ensayo.identidad = None
        quedan = ensayo.limpieza()
        if quedan:
            ensayo.resumen["fallo_limpieza"] = quedan
            print(f"FALLÓ la limpieza: {quedan}", file=sys.stderr)
            codigo = codigo or 1
        else:
            ensayo.ok("limpieza: 0 contenedores, 0 volúmenes y sin carpeta del ensayo en la VM;"
                      " control: la misma cuenta encuentra cem-postgres-staging")  # fmt: skip
    print(json.dumps(ensayo.resumen, indent=2, ensure_ascii=False, default=str))
    return codigo


if __name__ == "__main__":
    sys.exit(main())
