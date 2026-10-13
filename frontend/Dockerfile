# Imagen de cem-frontend (documento 13, §6.3). Se construye desde la RAÍZ del monorrepositorio
# (AD-23) para leer contratos/openapi.json: en Coolify, Base Directory "/" y Dockerfile
# "frontend/Dockerfile". La construye Coolify en la VM (ARM64); no clona nada ni usa secretos.
FROM node:22-alpine AS construccion
ENV COREPACK_ENABLE_DOWNLOAD_PROMPT=0 CI=true
RUN corepack enable
WORKDIR /build/frontend
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY contratos/openapi.json /build/contratos/openapi.json
COPY frontend/ ./
# Genera los tipos desde el contrato y compila con tsc antes de empaquetar: si el contrato
# cambió de forma incompatible, la imagen no se construye (AD-06).
RUN pnpm build

FROM nginx:1.27-alpine
RUN rm /etc/nginx/conf.d/default.conf
COPY frontend/nginx/cabeceras.conf frontend/nginx/proxy.conf /etc/nginx/cem/
COPY frontend/nginx/cem-frontend.conf.template /etc/nginx/templates/
COPY frontend/nginx/05-exigir-backend.sh /docker-entrypoint.d/
COPY --from=construccion /build/frontend/dist /usr/share/nginx/html
EXPOSE 80
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD wget -q -O /dev/null http://127.0.0.1/frontend-salud || exit 1
