#!/bin/sh
# Prueba la imagen de cem-frontend ya construida, sin backend: usuario, cabeceras, caché,
# fallback de la SPA y el 404 de /assets/. Uso: scripts/probar-imagen.sh [imagen]
# (desde la raíz: docker build -f frontend/Dockerfile -t cem-frontend .)
set -eu
IMAGEN="${1:-cem-frontend}"
PUERTO=18080
NOMBRE="cem-frontend-prueba-$$"
falla() { echo "FALLA: $*" >&2; docker rm -f "$NOMBRE" >/dev/null 2>&1; exit 1; }

# Sin CEM_BACKEND_DESTINO el contenedor no debe arrancar.
if docker run --rm "$IMAGEN" >/dev/null 2>&1; then falla "arrancó sin CEM_BACKEND_DESTINO"; fi

docker run -d --name "$NOMBRE" -p "$PUERTO:8080" -e CEM_BACKEND_DESTINO=sin-backend:8000 "$IMAGEN" >/dev/null
trap 'docker rm -f "$NOMBRE" >/dev/null 2>&1' EXIT
for _ in 1 2 3 4 5 6 7 8 9 10; do
  curl -fs "http://127.0.0.1:$PUERTO/frontend-salud" >/dev/null && break
  sleep 1
done
URL="http://127.0.0.1:$PUERTO"
cabecera() { curl -sI "$URL$1" | tr -d '\r' | grep -i "^$2:" | cut -d' ' -f2- || true; }
codigo() { curl -s -o /dev/null -w '%{http_code}' "$URL$1"; }

[ "$(docker exec "$NOMBRE" id -u)" = "101" ] || falla "el contenedor no corre como uid 101"
UIDS=$(docker top "$NOMBRE" -o pid,uid,args | awk '/nginx/ {print $2}' | sort -u)
[ "$UIDS" = "101" ] || falla "procesos de nginx con uid '$UIDS' (se espera solo 101)"
[ "$(codigo /)" = 200 ] || falla "/ no da 200"
[ "$(cabecera / cache-control)" = "no-cache" ] || falla "index.html no lleva no-cache"
cabecera / content-security-policy | grep -q "default-src 'self'" || falla "falta la CSP"
[ "$(cabecera / x-frame-options)" = "DENY" ] || falla "falta X-Frame-Options"
curl -s "$URL/una/ruta/de/la/spa" | grep -q '<div id="raiz">' || falla "la SPA no cae en index.html"
ACTIVO=$(curl -s "$URL/" | grep -o '/assets/index-[^"]*\.js' | head -1)
cabecera "$ACTIVO" cache-control | grep -q immutable || falla "el activo con hash no es inmutable"
[ "$(codigo /assets/no-existe.js)" = 404 ] || falla "un activo inexistente no da 404"
curl -s "$URL/assets/no-existe.js" | grep -q '<div id="raiz">' && falla "un activo inexistente devuelve index.html"
cabecera /assets/no-existe.js cache-control | grep -q immutable && falla "un 404 de /assets/ quedó inmutable"
[ "$(cabecera /manifest.webmanifest content-type)" = "application/manifest+json" ] || falla "tipo del manifiesto"
[ "$(cabecera /sw.js cache-control)" = "no-cache" ] || falla "sw.js no lleva no-cache"
# Extensión del service worker para el enlace de entrada (src/swEntrar.ts): sw.js la carga, se
# sirve como JavaScript, sin caché, y /entrar nunca sale de la caché del service worker.
curl -s "$URL/sw.js" | grep -q 'importScripts("/sw-entrar.js")' || falla "sw.js no carga sw-entrar.js"
curl -s "$URL/sw.js" | grep -q '\^\\/entrar' || falla "sw.js no excluye /entrar de navigateFallback"
[ "$(codigo /sw-entrar.js)" = 200 ] || falla "/sw-entrar.js no da 200"
[ "$(cabecera /sw-entrar.js cache-control)" = "no-cache" ] || falla "sw-entrar.js no lleva no-cache"
cabecera /sw-entrar.js content-type | grep -q '^application/javascript' || falla "tipo de sw-entrar.js"
curl -s "$URL/sw-entrar.js" | grep -q 'const RUTA_ENTRAR = "/entrar"' || falla "sw-entrar.js sin la ruta de entrada"
[ "$(codigo /health)" = 502 ] || falla "/health sin backend debería dar 502 (prueba que se reenvía)"

# IP real hacia el backend (límite de tasa, TB-8): X-Forwarded-For sale de CF-Connecting-IP y
# nunca del que mande el cliente; sin CF-Connecting-IP va la IP de quien llama. Un backend de eco
# devuelve el X-Forwarded-For que recibe.
RED="cem-frontend-prueba-red-$$"
ECO="cem-backend-eco-$$"
CON_ECO="$NOMBRE-eco"
trap 'docker rm -f "$NOMBRE" "$ECO" "$CON_ECO" >/dev/null 2>&1; docker network rm "$RED" >/dev/null 2>&1' EXIT
ECO_PY='import http.server as h
class E(h.BaseHTTPRequestHandler):
    def do_GET(self):
        b = (self.headers.get("X-Forwarded-For") or "").encode()
        self.send_response(200); self.send_header("Content-Length", str(len(b)))
        self.end_headers(); self.wfile.write(b)
h.HTTPServer(("0.0.0.0", 8000), E).serve_forever()'
docker network create "$RED" >/dev/null
docker run -d --name "$ECO" --network "$RED" --network-alias cem-backend-eco \
  python:3.12-slim python -c "$ECO_PY" >/dev/null
docker run -d --name "$CON_ECO" --network "$RED" -p "$((PUERTO + 1)):8080" \
  -e CEM_BACKEND_DESTINO=cem-backend-eco:8000 "$IMAGEN" >/dev/null
URL_ECO="http://127.0.0.1:$((PUERTO + 1))"
for _ in 1 2 3 4 5 6 7 8 9 10; do
  [ "$(curl -s -o /dev/null -w '%{http_code}' "$URL_ECO/health")" = 200 ] && break
  sleep 1
done
CON_CF=$(curl -s -H 'CF-Connecting-IP: 203.0.113.9' -H 'X-Forwarded-For: 198.51.100.66' "$URL_ECO/health")
[ "$CON_CF" = "203.0.113.9" ] || falla "con CF-Connecting-IP el backend recibió '$CON_CF'"
SIN_CF=$(curl -s -H 'X-Forwarded-For: 198.51.100.66' "$URL_ECO/api/v1/x")
case "$SIN_CF" in
  "" | *198.51.100.66*) falla "sin CF-Connecting-IP el backend recibió '$SIN_CF'" ;;
esac
# El registro de acceso no lleva la consulta (documento 13, §16.1): un token o un correo en `?…`
# no puede quedar en `docker logs` (Dozzle). Se prueba con el backend de eco, para que nginx no
# escriba errores de upstream: su registro de errores siempre trae la línea del pedido completa y
# no tiene formato configurable (por eso el enlace lleva el token en el fragmento y el canje va
# en el cuerpo). Control: los pedidos sí quedaron registrados, por su ruta.
SECRETO="secreto-$$"
curl -s -o /dev/null "$URL_ECO/entrar?token=$SECRETO&correo=ana@ejemplo.com"
curl -s -o /dev/null "$URL_ECO/api/v1/x?token=$SECRETO"
curl -s -o /dev/null -H "Referer: $URL_ECO/otra?token=$SECRETO" "$URL_ECO/"
REGISTRO=$(docker logs "$CON_ECO" 2>&1)
echo "$REGISTRO" | grep -q '"GET /entrar[ ?]' || falla "el pedido a /entrar no quedó en el registro de acceso"
echo "$REGISTRO" | grep -q '"GET /api/v1/x[ ?]' || falla "el pedido a /api/ no quedó en el registro de acceso"
if echo "$REGISTRO" | grep -q -e "$SECRETO" -e 'ana@ejemplo.com'; then
  falla "la consulta quedó en el registro de acceso"
fi
echo "OK: imagen $IMAGEN"
