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
# Herramientas del equipo que corren dentro del contenedor (`docker exec … python -m scripts.…`):
# alta de un consorcio, invitación de sus primeras personas, arranque con sus primeros mandatos
# (D3), asociación por soporte de la cuenta de un administrador externo (A7, P1-e), carga de un
# esquema de coeficientes (D4), anulación por soporte de un vínculo confirmado no iniciado
# (RF02), identidad humana (A13-C), borrado y recategorización de documentos (2c-2) y exportación
# de un consorcio (2c-3, RF36). Las demás (roles, OpenAPI, cobertura de PA) y el smoke de staging
# (`backend/pruebas/`) corren fuera de la imagen. `tests/test_scripts_en_la_imagen.py` falla si
# un script que dice correr dentro del contenedor no está en esta lista.
COPY --chown=cem backend/scripts/__init__.py backend/scripts/alta_consorcio.py \
     backend/scripts/invitar.py backend/scripts/alta_mandato.py \
     backend/scripts/mandato_soporte.py backend/scripts/cargar_coeficientes.py \
     backend/scripts/anular_vinculo.py backend/scripts/identidad_humana.py \
     backend/scripts/documento_soporte.py backend/scripts/exportar_consorcio.py scripts/
USER cem
EXPOSE 8000
# La misma imagen corre cem-backend, cem-trabajos o cem-planificador según CEM_PROCESO
# (api, trabajos o planificador; ver arranque.sh). La comprobación se adapta al proceso.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-m", "app.plataforma.latido"]
CMD ["sh", "arranque.sh"]
