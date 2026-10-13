-- Crea los roles de base de datos y la base de Consorcio en Mano (documento 13, §8.6).
-- Se ejecuta UNA vez por servidor con un superusuario; las migraciones no pueden crear roles
-- porque cem_migrate no tiene CREATEROLE. Las claves llegan como variables de psql y nunca
-- se escriben en el repositorio:
--
--   psql "$URL_SUPERUSUARIO" -v base=cem \
--     -v clave_migrate="$CLAVE_MIGRATE" -v clave_app="$CLAVE_APP" -v clave_ro="$CLAVE_RO" \
--     -f scripts/crear_roles.sql
--
-- Es idempotente: no toca roles ni bases que ya existen.
-- Pendiente: cem_backup (pg_read_all_data + REPLICATION) llega con las copias (§17).

\set ON_ERROR_STOP on

select format(
  'create role cem_migrate login bypassrls nosuperuser nocreaterole nocreatedb password %L',
  :'clave_migrate')
where not exists (select 1 from pg_roles where rolname = 'cem_migrate') \gexec

select format(
  'create role cem_app login nobypassrls nosuperuser nocreaterole nocreatedb connection limit 40 password %L',
  :'clave_app')
where not exists (select 1 from pg_roles where rolname = 'cem_app') \gexec

select format(
  'create role cem_ro login nobypassrls nosuperuser nocreaterole nocreatedb connection limit 3 password %L',
  :'clave_ro')
where not exists (select 1 from pg_roles where rolname = 'cem_ro') \gexec

alter role cem_ro set default_transaction_read_only = on;

select format('create database %I owner cem_migrate', :'base')
where not exists (select 1 from pg_database where datname = :'base') \gexec

revoke all on database :"base" from public;
grant connect on database :"base" to cem_app, cem_ro;
