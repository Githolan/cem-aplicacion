#!/bin/sh
# Pruebas de extremo a extremo (Playwright) contra lo que de verdad se despliega: construye las
# imágenes de cem-frontend y cem-backend desde la raíz, las levanta en una red de Docker propia
# con Redis (el backend no arranca sin él) y corre Playwright contra el nginx de la imagen, no
# contra el servidor de desarrollo de Vite. Al terminar, bien o mal, borra todo lo que levantó.
#
# Uso (desde cualquier carpeta): frontend/scripts/probar-extremo-a-extremo.sh [argumentos de
# "playwright test"], o "pnpm e2e" desde frontend/. Necesita Docker, pnpm y Chromium de Playwright
# (si falta: "pnpm exec playwright install chromium").
set -eu

RAIZ=$(cd "$(dirname "$0")/../.." && pwd)
IMAGEN_FRONTEND=cem-frontend
IMAGEN_BACKEND=cem-backend
# La misma imagen de Redis que la suite oficial del backend (backend/pruebas/compose.yml).
IMAGEN_REDIS=redis:7-alpine
# Nombres propios de esta corrida (sufijo = PID), así dos corridas no se pisan.
SUFIJO=$$
RED="cem-frontend-e2e-red-$SUFIJO"
REDIS="cem-frontend-e2e-redis-$SUFIJO"
BACKEND="cem-frontend-e2e-backend-$SUFIJO"
FRONTEND="cem-frontend-e2e-$SUFIJO"
FRONTEND_SIN_BACKEND="cem-frontend-e2e-sin-backend-$SUFIJO"
# Alias de red como en Coolify (custom_network_aliases): el frontend llega al backend por nombre.
ALIAS_BACKEND="cem-backend-e2e"
ALIAS_REDIS="cem-redis-e2e"
# Segundos que se espera a que el frontend reenvíe /health al backend antes de rendirse.
ESPERA_S=60

limpiar() {
  docker rm -f "$FRONTEND" "$FRONTEND_SIN_BACKEND" "$BACKEND" "$REDIS" >/dev/null 2>&1 || true
  docker network rm "$RED" >/dev/null 2>&1 || true
}
falla() { echo "FALLA: $*" >&2; exit 1; }
trap limpiar EXIT
trap 'exit 130' INT TERM

docker build -q -f "$RAIZ/frontend/Dockerfile" -t "$IMAGEN_FRONTEND" "$RAIZ" >/dev/null
docker build -q -f "$RAIZ/backend/Dockerfile" -t "$IMAGEN_BACKEND" "$RAIZ" >/dev/null

# La SHA que el backend informa en /health; la prueba exige verla en la página.
SHA=$(git -C "$RAIZ" rev-parse HEAD)

docker network create "$RED" >/dev/null
SUBRED=$(docker network inspect -f '{{(index .IPAM.Config 0).Subnet}}' "$RED")
docker run -d --name "$REDIS" --network "$RED" --network-alias "$ALIAS_REDIS" "$IMAGEN_REDIS" >/dev/null
docker run -d --name "$BACKEND" --network "$RED" --network-alias "$ALIAS_BACKEND" \
  -e CEM_REDIS_URL="redis://$ALIAS_REDIS:6379/0" \
  -e FORWARDED_ALLOW_IPS="$SUBRED" \
  -e CEM_SHA_IMAGEN="$SHA" \
  "$IMAGEN_BACKEND" >/dev/null
# Puertos del anfitrión elegidos por Docker (siempre libres), solo en 127.0.0.1.
docker run -d --name "$FRONTEND" --network "$RED" -p 127.0.0.1::8080 \
  -e CEM_BACKEND_DESTINO="$ALIAS_BACKEND:8000" "$IMAGEN_FRONTEND" >/dev/null
docker run -d --name "$FRONTEND_SIN_BACKEND" --network "$RED" -p 127.0.0.1::8080 \
  -e CEM_BACKEND_DESTINO="$ALIAS_REDIS:8000" "$IMAGEN_FRONTEND" >/dev/null

puerto() { docker port "$1" 8080/tcp | head -1 | sed 's/.*://'; }
URL="http://127.0.0.1:$(puerto "$FRONTEND")"
URL_SIN_BACKEND="http://127.0.0.1:$(puerto "$FRONTEND_SIN_BACKEND")"

esperado=0
until [ "$(curl -s -o /dev/null -w '%{http_code}' "$URL/health")" = 200 ]; do
  esperado=$((esperado + 1))
  if [ "$esperado" -ge "$ESPERA_S" ]; then
    docker logs "$BACKEND" >&2 || true
    falla "el backend no respondió /health a través del frontend en $ESPERA_S s"
  fi
  sleep 1
done
until [ "$(curl -s -o /dev/null -w '%{http_code}' "$URL_SIN_BACKEND/frontend-salud")" = 200 ]; do
  esperado=$((esperado + 1))
  [ "$esperado" -lt "$ESPERA_S" ] || falla "el frontend sin backend no arrancó en $ESPERA_S s"
  sleep 1
done

echo "cem-frontend en $URL (sin backend: $URL_SIN_BACKEND); backend con SHA $SHA"
cd "$RAIZ/frontend"
CEM_E2E_URL="$URL" CEM_E2E_URL_SIN_BACKEND="$URL_SIN_BACKEND" CEM_E2E_SHA="$SHA" \
  pnpm exec playwright test "$@"
