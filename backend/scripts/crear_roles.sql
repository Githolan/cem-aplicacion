-- Crea los roles de base de datos y la base de Consorcio en Mano (documento 13, §8.6).
-- Se ejecuta UNA vez por servidor con un superusuario; las migraciones no pueden crear roles
-- porque cem_migrate no tiene CREATEROLE. Las claves llegan como variables de psql y nunca
-- se escriben en el repositorio:
--
--   psql "$URL_SUPERUSUARIO" -v base=cem \
--     -v clave_migrate="$CLAVE_MIGRATE" -v clave_app="$CLAVE_APP" -v clave_ro="$CLAVE_RO" \
--     -v clave_backup="$CLAVE_BACKUP" \
--     -v clave_soporte_documentos="$CLAVE_SOPORTE_DOCUMENTOS" \
--     -v clave_soporte_exportacion="$CLAVE_SOPORTE_EXPORTACION" \
--     -v clave_copia_continua="$CLAVE_COPIA_CONTINUA" -f scripts/crear_roles.sql
--
-- Es idempotente: no toca roles ni bases que ya existen. En un servidor creado antes de las
-- copias, volver a correrlo solo agrega cem_backup. Los roles de soporte de documentos (2c-2), de
-- exportación (2c-3) y de la copia continua (2c-4) están cada uno en su propio archivo, para
-- crearlos solos en un servidor que ya existe.

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

-- cem_backup: copia diaria con pg_dump (§17.2). Lee todo (pg_read_all_data) y no escribe nada.
-- BYPASSRLS es necesario: las tablas tienen RLS forzada y pg_dump se niega a volcar una tabla
-- cuyas filas filtraría una política. Sin REPLICATION: la copia continua del WAL tiene su propio
-- rol, cem_copia_continua (crear_rol_copia_continua.sql). Dos conexiones: la que exporta la
-- instantánea y la de pg_dump.
select format(
  'create role cem_backup login bypassrls noreplication nosuperuser nocreaterole nocreatedb connection limit 2 password %L',
  :'clave_backup')
where not exists (select 1 from pg_roles where rolname = 'cem_backup') \gexec

grant pg_read_all_data to cem_backup;
alter role cem_backup set default_transaction_read_only = on;

select format('create database %I owner cem_migrate', :'base')
where not exists (select 1 from pg_database where datname = :'base') \gexec

revoke all on database :"base" from public;
grant connect on database :"base" to cem_app, cem_ro, cem_backup;

-- Sin `clave_soporte_documentos` (volver a correrlo en un servidor que ya existe, como para
-- agregar cem_backup), no se toca ese rol.
\if :{?clave_soporte_documentos}
\ir crear_rol_soporte_documentos.sql
\else
\echo 'sin clave_soporte_documentos: cem_soporte_documentos no se crea (crear_rol_soporte_documentos.sql)'
\endif

-- Lo mismo sin `clave_soporte_exportacion` (2c-3).
\if :{?clave_soporte_exportacion}
\ir crear_rol_soporte_exportacion.sql
\else
\echo 'sin clave_soporte_exportacion: cem_soporte_exportacion no se crea (crear_rol_soporte_exportacion.sql)'
\endif

-- Lo mismo sin `clave_copia_continua`: cem_copia_continua no se toca.
\if :{?clave_copia_continua}
\ir crear_rol_copia_continua.sql
\else
\echo 'sin clave_copia_continua: cem_copia_continua no se crea (crear_rol_copia_continua.sql)'
\endif
