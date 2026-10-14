# Imagen que corre las pruebas del backend en Linux contra PostgreSQL 18 y Redis 7 reales
# (documento 14, §6.3). La usa compose.yml de esta carpeta; el código se monta en /src, así que
# solo hay que reconstruirla cuando cambian los requisitos.
FROM python:3.12-slim
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
# psql corre scripts/crear_roles.sql en conftest.py.
RUN apt-get update \
 && apt-get install -y --no-install-recommends postgresql-client \
 && rm -rf /var/lib/apt/lists/*
COPY backend/requirements.prod.txt backend/requirements.txt /tmp/requisitos/
# El paquete de contratos llega por PYTHONPATH desde el código montado, no instalado.
RUN grep -v '^-e ' /tmp/requisitos/requirements.txt > /tmp/requisitos/desarrollo.txt \
 && python -m venv /venv \
 && /venv/bin/pip install -r /tmp/requisitos/desarrollo.txt
ENV PATH=/venv/bin:$PATH PYTHONPATH=/src/contratos PYTHONDONTWRITEBYTECODE=1
WORKDIR /src/backend
