-- Crea el rol de base de soporte de privacidad (H2d §7 del repositorio CEM, corte 2d-1): lo usa
-- `scripts.privacidad` para abrir, confirmar, corroborar y rechazar los casos de RF37. Sin
-- BYPASSRLS y sin permisos sobre ninguna tabla: solo ejecuta las funciones de la migración 0039.
-- Lo incluye `crear_roles.sql`; en un servidor que ya existe se corre solo, con un superusuario y
-- la clave como variable de psql (nunca en el repositorio):
--
--   psql "$URL_SUPERUSUARIO" -v base=cem -v clave_soporte_privacidad="$CLAVE" \
--     -f scripts/crear_rol_soporte_privacidad.sql
--
-- Es idempotente: no toca el rol si ya existe.

\set ON_ERROR_STOP on

select format(
  'create role cem_soporte_privacidad login nobypassrls nosuperuser nocreaterole nocreatedb noreplication connection limit 2 password %L',
  :'clave_soporte_privacidad')
where not exists (select 1 from pg_roles where rolname = 'cem_soporte_privacidad') \gexec

grant connect on database :"base" to cem_soporte_privacidad;
