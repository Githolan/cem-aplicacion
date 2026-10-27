-- Crea el rol de base de la exportación por soporte (H2c §1.1 y §5 del repositorio CEM, corte
-- 2c-3): lo usa `scripts.exportar_consorcio` para armar el paquete cifrado de un consorcio. Sin
-- BYPASSRLS y sin SELECT directo sobre ninguna tabla del consorcio: lee por las funciones de
-- exportación y escribe solo la constancia (`plataforma.exportacion`), según la migración 0038.
-- Lo incluye `crear_roles.sql`; en un servidor que ya existe se corre solo, con un superusuario
-- y la clave como variable de psql (nunca en el repositorio):
--
--   psql "$URL_SUPERUSUARIO" -v base=cem -v clave_soporte_exportacion="$CLAVE" \
--     -f scripts/crear_rol_soporte_exportacion.sql
--
-- Es idempotente: no toca el rol si ya existe. Va antes de la migración 0038, que le da los
-- permisos.

\set ON_ERROR_STOP on

select format(
  'create role cem_soporte_exportacion login nobypassrls nosuperuser nocreaterole nocreatedb noreplication connection limit 2 password %L',
  :'clave_soporte_exportacion')
where not exists (select 1 from pg_roles where rolname = 'cem_soporte_exportacion') \gexec

grant connect on database :"base" to cem_soporte_exportacion;
