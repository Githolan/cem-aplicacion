#!/bin/bash
# Prepara una sesión de Claude Code en la nube para correr las pruebas de cem-aplicacion
# contra los mismos motores que producción: PostgreSQL 18 (PGDG) y Redis 7 (plan, documento 14, regla 5).
# Idempotente: lo que ya está instalado o corriendo no se repite.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

RAIZ="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"
PG_VERSION=18
PG_PUERTO=5432

# PostgreSQL 18 del repositorio PGDG; con el 16 de Ubuntu falla test_la_suite_oficial_corre_en_postgresql_18.
if [ ! -x "/usr/lib/postgresql/${PG_VERSION}/bin/postgres" ]; then
  if [ ! -f /etc/apt/sources.list.d/pgdg.list ] && [ ! -f /etc/apt/sources.list.d/pgdg.sources ]; then
    /usr/share/postgresql-common/pgdg/apt.postgresql.org.sh -y >/dev/null
  fi
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "postgresql-${PG_VERSION}" >/dev/null
fi

# Un clúster propio en el puerto 5432 con el superusuario postgres/postgres que esperan las pruebas.
# UTF8 explícito: sin locale el contenedor lo crea en SQL_ASCII y psycopg devuelve bytes.
if ! pg_lsclusters -h 2>/dev/null | awk -v v="$PG_VERSION" '$1==v && $2=="main"' | grep -q .; then
  pg_createcluster "$PG_VERSION" main --port "$PG_PUERTO" --locale C.UTF-8 --encoding UTF8 >/dev/null
fi
for v in $(pg_lsclusters -h | awk -v v="$PG_VERSION" '$1!=v && $4=="online" {print $1}'); do
  pg_ctlcluster "$v" main stop || true
done
if ! pg_lsclusters -h | awk -v v="$PG_VERSION" '$1==v && $2=="main" && $4=="online"' | grep -q .; then
  pg_ctlcluster "$PG_VERSION" main start
fi
su postgres -c "psql -q -p ${PG_PUERTO} -c \"alter user postgres password 'postgres'\""

# age y las herramientas de PostgreSQL 18 en el PATH (pg_dump, initdb) para las pruebas de copias.
if ! command -v age >/dev/null 2>&1; then
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq age >/dev/null
fi

# Redis 7 para el límite de tasa.
if ! redis-cli ping >/dev/null 2>&1; then
  redis-server --daemonize yes --save '' --appendonly no >/dev/null
fi

# Correo y S3 de prueba, los mismos que backend/pruebas/compose.yml, como binarios (no hay Docker).
MAILPIT_VERSION=v1.31.4
VERSITYGW_VERSION=v1.0.18
DATOS="${TMPDIR:-/tmp}/cem-pruebas"
mkdir -p "$DATOS/s3"
if ! command -v mailpit >/dev/null 2>&1; then
  curl -fsSL "https://github.com/axllent/mailpit/releases/download/${MAILPIT_VERSION}/mailpit-linux-amd64.tar.gz" \
    | tar xz -C /usr/local/bin mailpit
fi
if ! command -v versitygw >/dev/null 2>&1; then
  curl -fsSL "https://github.com/versity/versitygw/releases/download/${VERSITYGW_VERSION}/versitygw_${VERSITYGW_VERSION}_Linux_x86_64.tar.gz" \
    | tar xz -C "$DATOS"
  install -m 0755 "$(find "$DATOS" -name versitygw -type f | head -1)" /usr/local/bin/versitygw
fi
if ! pgrep -x mailpit >/dev/null; then
  MP_SMTP_TLS_CERT=sans:localhost MP_SMTP_TLS_KEY=sans:localhost MP_SMTP_REQUIRE_STARTTLS=true \
    MP_SMTP_AUTH=usuario-pruebas:clave-pruebas MP_ENABLE_CHAOS=true \
    nohup mailpit --quiet >"$DATOS/mailpit.log" 2>&1 &
fi
if ! pgrep -x versitygw >/dev/null; then
  ROOT_ACCESS_KEY_ID=clave-id-pruebas ROOT_SECRET_ACCESS_KEY=clave-secreta-pruebas VGW_REGION=sa-santiago-1 \
    nohup versitygw --quiet posix "$DATOS/s3" >"$DATOS/versitygw.log" 2>&1 &
fi

# Dependencias de Python (backend instala ../contratos) y del frontend.
cd "$RAIZ/backend"
if [ ! -x .venv/bin/python ]; then
  python3.12 -m venv .venv
fi
.venv/bin/pip install -q -r requirements.txt
cd "$RAIZ/frontend"
pnpm install --silent >/dev/null

if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  {
    echo "export CEM_TEST_ADMIN_URL=postgresql+psycopg://postgres:postgres@localhost:${PG_PUERTO}/postgres"
    echo "export CEM_TEST_REDIS_URL=redis://localhost:6379/0"
    echo "export PATH=/usr/lib/postgresql/${PG_VERSION}/bin:\$PATH"
    echo "export CEM_TEST_S3_ENDPOINT=http://localhost:7070"
    echo "export CEM_TEST_S3_REGION=sa-santiago-1"
    echo "export CEM_TEST_S3_CLAVE_ID=clave-id-pruebas"
    echo "export CEM_TEST_S3_CLAVE_SECRETA=clave-secreta-pruebas"
  } >> "$CLAUDE_ENV_FILE"
fi
