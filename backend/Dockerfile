# Imagen de cem-backend (documento 13, §6.2). Se construye desde la RAÍZ del monorrepositorio
# (AD-23) para poder copiar contratos/: en Coolify, Base Directory "/" y Dockerfile
# "backend/Dockerfile". La construye Coolify en la VM (ARM64); no clona nada ni usa secretos.
FROM python:3.12-slim AS dependencias
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
COPY contratos/ contratos/
COPY backend/requirements.prod.txt backend/requirements.prod.txt
RUN python -m venv /venv \
 && /venv/bin/pip install -r backend/requirements.prod.txt ./contratos

FROM python:3.12-slim
ENV PATH=/venv/bin:$PATH PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN useradd --system --uid 10001 --home /app cem
COPY --from=dependencias /venv /venv
WORKDIR /app
COPY --chown=cem backend/app app
COPY --chown=cem backend/migrations migrations
COPY --chown=cem backend/alembic.ini backend/arranque.sh ./
USER cem
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"
CMD ["sh", "arranque.sh"]
