# Imagen de cem-frontend (documento 13, §6.3). Se construye desde la RAÍZ del monorrepositorio
# (AD-23) para leer contratos/openapi.json y generar los tipos (AD-06): en Coolify, Base Directory
# "/" y Dockerfile "frontend/Dockerfile". Sin secretos: la API es del mismo origen.
FROM node:20.19-alpine AS construccion
ENV PNPM_HOME=/pnpm PATH=/pnpm:$PATH COREPACK_ENABLE_DOWNLOAD_PROMPT=0
WORKDIR /build/frontend
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN corepack enable \
 && corepack prepare "$(node -p "require('./package.json').packageManager")" --activate \
 && pnpm install --frozen-lockfile
COPY contratos/openapi.json /build/contratos/openapi.json
COPY frontend/ ./
RUN pnpm run build

FROM nginxinc/nginx-unprivileged:1.27-alpine
COPY frontend/nginx/cem-frontend.conf /etc/nginx/conf.d/default.conf
COPY frontend/nginx/cabeceras-seguridad.conf /etc/nginx/cem/cabeceras-seguridad.conf
COPY --from=construccion /build/frontend/dist /usr/share/nginx/html
# La imagen base ya corre como el usuario sin privilegios "nginx" (uid 101) y escucha en 8080.
USER 101
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD wget -q -O /dev/null http://127.0.0.1:8080/ || exit 1
