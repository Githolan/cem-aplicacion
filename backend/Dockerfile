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
# Copias (documento 13, §17): pg_dump de la misma versión mayor que cem-postgres (18), el
# servidor para restaurar cada copia en una base efímera (§17.3) y age para cifrarla. Debian
# trae PostgreSQL 17; el 18 sale del repositorio oficial de PostgreSQL (PGDG), cuya clave viene
# en postgresql-common de Debian. Sin clúster por defecto: la base efímera la crea cada copia.
RUN apt-get update \
 && apt-get install -y --no-install-recommends postgresql-common ca-certificates \
 && sed -i 's/^#\? *create_main_cluster.*/create_main_cluster = false/' \
      /etc/postgresql-common/createcluster.conf \
 && /usr/share/postgresql-common/pgdg/apt.postgresql.org.sh -y \
 && apt-get install -y --no-install-recommends postgresql-18 age \
 && rm -rf /var/lib/apt/lists/*
ENV PATH=/venv/bin:/usr/lib/postgresql/18/bin:$PATH PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN useradd --system --uid 10001 --home /app cem
COPY --from=dependencias /venv /venv
WORKDIR /app
COPY --chown=cem backend/app app
COPY --chown=cem backend/migrations migrations
COPY --chown=cem backend/alembic.ini backend/arranque.sh ./
USER cem
EXPOSE 8000
# La misma imagen corre cem-backend, cem-trabajos o cem-planificador según CEM_PROCESO
# (api, trabajos o planificador; ver arranque.sh). La comprobación se adapta al proceso.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-m", "app.plataforma.latido"]
CMD ["sh", "arranque.sh"]
