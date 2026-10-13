#!/bin/sh
# Arranque de cem-backend: aplica las migraciones con cem_migrate (si la variable existe) y
# levanta la API con cem_app. Si la migración falla, el contenedor no arranca y Coolify
# conserva la versión anterior.
set -eu
if [ -n "${CEM_DATABASE_URL_MIGRATE:-}" ]; then
  alembic upgrade head
fi
exec uvicorn "app.main:crear_app" --factory --host 0.0.0.0 --port 8000 --workers 2 --proxy-headers
