#!/bin/sh
# Script de inicio de cem-postgres-pruebas (compose.yml): habilita la replicación física para el
# rol cem_copia_continua desde la red de las pruebas, como `pg_hba` de las bases reales
# (`host replication cem_copia_continua <red interna> scram-sha-256`, H2c §6 del repositorio
# CEM). La imagen solo trae replicación local; `wal_level` ya es `replica` por defecto.
set -eu
echo "host replication cem_copia_continua all scram-sha-256" >> "$PGDATA/pg_hba.conf"
