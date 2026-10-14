#!/bin/sh
# Arranque de la imagen del backend. CEM_PROCESO elige el proceso (documento 13, §4.2):
#   api (por defecto)  cem-backend: aplica las migraciones con cem_migrate (si la variable
#                      existe) y levanta la API con cem_app. Si la migración falla, el
#                      contenedor no arranca y Coolify conserva la versión anterior.
#   trabajos           cem-trabajos: consume la cola. No migra.
#   planificador       cem-planificador: despacha el outbox y encola tareas periódicas. No migra.
set -eu
case "${CEM_PROCESO:-api}" in
  api)
    # IP real para el límite de tasa (documento 13, §10.6): uvicorn acepta X-Forwarded-For solo
    # de estas direcciones, las de la red de Docker por la que llega el nginx de cem-frontend.
    # Sin ella, todas las peticiones compartirían la clave del proxy, que es el defecto TB-8.
    : "${FORWARDED_ALLOW_IPS:?falta FORWARDED_ALLOW_IPS: la red de Docker del frontend, en CIDR}"
    if [ -n "${CEM_DATABASE_URL_MIGRATE:-}" ]; then
      alembic upgrade head
    fi
    exec uvicorn "app.main:crear_app" --factory --host 0.0.0.0 --port 8000 --workers 2 \
      --proxy-headers --forwarded-allow-ips "$FORWARDED_ALLOW_IPS"
    ;;
  trabajos)
    exec python -m app.plataforma.trabajos
    ;;
  planificador)
    exec python -m app.plataforma.planificador
    ;;
  *)
    echo "CEM_PROCESO desconocido: ${CEM_PROCESO}" >&2
    exit 1
    ;;
esac
