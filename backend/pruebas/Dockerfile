# Imagen que corre las pruebas del backend en Linux contra PostgreSQL 18 y Redis 7 reales
# (documento 14, §6.3). La usa compose.yml de esta carpeta; el código se monta en /src, así que
# solo hay que reconstruirla cuando cambian los requisitos.
FROM python:3.12-slim
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
# psql corre scripts/crear_roles.sql en conftest.py. Las copias (§17) necesitan, igual que la
# imagen de producción, pg_dump 18, el servidor 18 para la base efímera, age y el locale
# en_US.UTF-8 de la copia física (ver backend/Dockerfile).
RUN apt-get update \
 && apt-get install -y --no-install-recommends postgresql-common ca-certificates \
 && sed -i 's/^#\? *create_main_cluster.*/create_main_cluster = false/' \
      /etc/postgresql-common/createcluster.conf \
 && /usr/share/postgresql-common/pgdg/apt.postgresql.org.sh -y \
 && apt-get install -y --no-install-recommends postgresql-18 age locales \
 && localedef -i en_US -f UTF-8 en_US.UTF-8 \
 && rm -rf /var/lib/apt/lists/*
COPY backend/requirements.prod.txt backend/requirements.txt /tmp/requisitos/
# El paquete de contratos llega por PYTHONPATH desde el código montado, no instalado.
RUN grep -v '^-e ' /tmp/requisitos/requirements.txt > /tmp/requisitos/desarrollo.txt \
 && python -m venv /venv \
 && /venv/bin/pip install -r /tmp/requisitos/desarrollo.txt
ENV PATH=/venv/bin:/usr/lib/postgresql/18/bin:$PATH PYTHONPATH=/src/contratos \
    PYTHONDONTWRITEBYTECODE=1
# Sin root, como cem en producción: initdb (base efímera de las copias) se niega a correr
# como root.
RUN useradd --uid 10001 --create-home cem
USER cem
WORKDIR /src/backend
