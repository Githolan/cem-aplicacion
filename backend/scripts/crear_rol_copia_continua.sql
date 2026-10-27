-- Crea el rol de la copia continua (H2c §6 del repositorio CEM, corte 2c-4): lo usa
-- `cem-copia-continua` para `pg_receivewal` (WAL continuo con su slot) y `pg_basebackup` (copia
-- base semanal). Las dos herramientas solo necesitan el atributo REPLICATION y una línea
-- `host replication` en `pg_hba`; ningún permiso sobre bases, esquemas ni tablas. Por eso no se
-- le da `connect` a `cem` ni nada más: con REPLICATION igual lee físicamente todo el clúster, y
-- su clave es tan sensible como la de cem_backup. Cuatro conexiones: el receptor, la copia base
-- (dos: la copia y su WAL por `--wal-method=stream`) y la consulta breve al slot (sobre la base
-- `postgres`, que PUBLIC puede abrir).
--
-- Lo incluye `crear_roles.sql`; en un servidor que ya existe se corre solo, con un superusuario
-- y la clave como variable de psql (nunca en el repositorio):
--
--   psql "$URL_SUPERUSUARIO" -v clave_copia_continua="$CLAVE" \
--     -f scripts/crear_rol_copia_continua.sql
--
-- Además, en el servidor (no lo hace este script): `pg_hba.conf` con
-- `host replication cem_copia_continua <red interna> scram-sha-256` y
-- `max_slot_wal_keep_size = 5GB`.
--
-- Es idempotente: si el rol ya existe, solo le ajusta el tope de conexiones.

\set ON_ERROR_STOP on

select format(
  'create role cem_copia_continua login replication nobypassrls nosuperuser nocreaterole nocreatedb connection limit 4 password %L',
  :'clave_copia_continua')
where not exists (select 1 from pg_roles where rolname = 'cem_copia_continua') \gexec

-- Un rol creado antes con otro tope queda con el de ahora (el resto de sus atributos no se toca).
alter role cem_copia_continua connection limit 4;
