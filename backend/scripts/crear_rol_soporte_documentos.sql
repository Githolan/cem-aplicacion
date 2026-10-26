-- Crea el rol de base de soporte de documentos (H2c §1.1 del repositorio CEM, corte 2c-2): lo
-- usa `scripts.documento_soporte` para borrar un documento o cambiar su categoría. Sin BYPASSRLS:
-- ve y cambia solo las filas del consorcio fijado en el contexto, y solo las columnas que le da
-- la migración 0037. Lo incluye `crear_roles.sql`; en un servidor que ya existe se corre solo,
-- con un superusuario y la clave como variable de psql (nunca en el repositorio):
--
--   psql "$URL_SUPERUSUARIO" -v base=cem -v clave_soporte_documentos="$CLAVE" \
--     -f scripts/crear_rol_soporte_documentos.sql
--
-- Es idempotente: no toca el rol si ya existe.

\set ON_ERROR_STOP on

select format(
  'create role cem_soporte_documentos login nobypassrls nosuperuser nocreaterole nocreatedb noreplication connection limit 2 password %L',
  :'clave_soporte_documentos')
where not exists (select 1 from pg_roles where rolname = 'cem_soporte_documentos') \gexec

grant connect on database :"base" to cem_soporte_documentos;
