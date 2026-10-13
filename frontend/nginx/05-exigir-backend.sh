#!/bin/sh
# Corre antes del envsubst de la imagen oficial: sin destino del backend, el contenedor no
# arranca y Coolify conserva la versión anterior.
set -eu
if [ -z "${CEM_BACKEND_DESTINO:-}" ]; then
  echo "cem-frontend: falta CEM_BACKEND_DESTINO (p. ej. cem-backend:8000)" >&2
  exit 1
fi
