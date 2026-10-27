"""2d-1: el expediente de un pedido de privacidad y su confirmación por el titular (RF37, CU10).

Diseño: `doc/diseno/H2d_RF37_Diseno.md` del repositorio CEM, §4 y §7. Decisión del propietario
del 25-10-2026 (§1.5): lo tramita el equipo CEM, con sesión o control del correo y sin documento
por defecto; figurar en el padrón no es un atajo. Decisión del 26-10: soporte no va a la
auditoría funcional.

- **`plataforma.caso_privacidad`:** un pedido con un solo titular (`persona_id`, `sujeto_correo`
  o `sujeto_descripcion`, exactamente uno), un alcance (`cuenta` = sin consorcio, o un consorcio)
  y un derecho. Fuera de `auditoria`: es trazabilidad operativa de soporte. Con RLS forzada y sin
  políticas: **`cem_app` no tiene ningún permiso** (no hay ruta), y el rol de soporte solo llega
  por las funciones de abajo. Un caso resuelto o rechazado no cambia más.
- **Código de confirmación (§4.2):** el script lo genera, lo manda solo al correo del titular
  (el destino lo elige la base, no soporte) y la base guarda solo su SHA-256; vence a las 72 horas
  y sirve una vez. El titular lo devuelve a soporte, que lo carga: eso prueba el control del
  correo y que el pedido es suyo.
- **Quién puede confirmar por correo (§4.2):** solo si el correo es el único factor de la persona.
  Con contraseña o con TOTP dado de alta (`nucleo.tiene_segundo_factor`), `confirmar_caso` se niega
  (`requiere_aplicacion`): eso espera la decisión del canal (§9.1).
- **Correo confiable (§4.2, punto 4):** la persona aceptó una invitación a ese correo (la
  invitación lleva su `persona_id`, que escribe la aceptación junto con su evento) y tiene, en
  el mismo consorcio, un vínculo confirmado por un tercero (P4) o un mandato; en el alcance
  `cuenta`, en algún consorcio. Si no
  (también `sujeto_correo`, siempre), hace falta además la corroboración de la administración o
  del Consejo. Un titular sin correo (`sujeto_descripcion`) no se confirma en este corte: queda
  como caso manual.
- **El correo con el código** pasa por el outbox como cualquier otro; al confirmar o rechazar se
  borran su evento y su trabajo (si no está en curso), para no guardar el código más de lo
  necesario.

El rol `cem_soporte_privacidad` lo crea un superusuario (`scripts/crear_rol_soporte_privacidad.sql`,
incluido en `crear_roles.sql`): `cem_migrate` no tiene `CREATEROLE`. Ninguna política nombra al
rol (la copia diaria se verifica restaurando sin roles).

Downgrade: se niega si hay casos (son la constancia de que se atendió un pedido).

Revisión: 0039
Anterior: 0038
"""

from alembic import op

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None

ROL = "cem_soporte_privacidad"
FUNCIONES = (
    "plataforma.privacidad_abrir(text, uuid, text, text, text, text)",
    "plataforma.privacidad_enviar_codigo(uuid, bytea, text, text)",
    "plataforma.privacidad_confirmar(uuid, bytea, text)",
    "plataforma.privacidad_corroborar(uuid, text)",
    "plataforma.privacidad_rechazar(uuid, text)",
    "plataforma.privacidad_ver(uuid)",
)

UPGRADE = f"""
create table plataforma.caso_privacidad (
  id                    uuid primary key default plataforma.uuid_v7(),
  persona_id            uuid references nucleo.persona (id),
  sujeto_correo         text check (sujeto_correo = lower(btrim(sujeto_correo))
                                    and position('@' in sujeto_correo) > 1),
  sujeto_descripcion    text check (length(btrim(sujeto_descripcion)) between 1 and 200),
  alcance_consorcio_id  uuid references nucleo.consorcio (id),
  derecho               text not null
    check (derecho in ('acceso', 'rectificacion_nombre', 'supresion')),
  origen_pedido         text not null check (origen_pedido in ('titular', 'administracion')),
  referencia_pedido     text not null check (length(btrim(referencia_pedido)) between 1 and 300),
  abierto_en            timestamptz not null default now(),
  codigo_hash           bytea check (length(codigo_hash) = 32),
  codigo_vence_en       timestamptz,
  codigo_evento_id      uuid,
  codigo_usado_en       timestamptz,
  corroboracion         text check (length(btrim(corroboracion)) between 1 and 300),
  corroborado_en        timestamptz,
  confirmado_en         timestamptz,
  confirmacion          text check (length(btrim(confirmacion)) between 1 and 300),
  estado                text not null default 'abierto'
    check (estado in ('abierto', 'confirmado', 'resuelto', 'rechazado')),
  resolucion            jsonb check (jsonb_typeof(resolucion) = 'object'),
  resuelto_en           timestamptz,
  check (num_nonnulls(persona_id, sujeto_correo, sujeto_descripcion) = 1),
  check ((codigo_hash is null) = (codigo_vence_en is null)),
  check (codigo_usado_en is null or codigo_hash is not null),
  check ((corroboracion is null) = (corroborado_en is null)),
  check ((confirmado_en is null) = (confirmacion is null)),
  check ((estado in ('confirmado', 'resuelto')) = (confirmado_en is not null)
         or estado = 'rechazado'),
  check ((estado in ('resuelto', 'rechazado')) = (resuelto_en is not null)),
  check ((estado in ('resuelto', 'rechazado')) = (resolucion is not null))
);
create index caso_privacidad_persona on plataforma.caso_privacidad (persona_id)
  where persona_id is not null;
alter table plataforma.caso_privacidad enable row level security;
alter table plataforma.caso_privacidad force row level security;
-- Sin políticas: nadie sin BYPASSRLS ve una fila. Las funciones de abajo son de `cem_migrate`.

-- Un caso cerrado no cambia, y un código usado no se reemplaza.
create function plataforma.caso_privacidad_fijo()
  returns trigger
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  begin
    if old.estado in ('resuelto', 'rechazado') then
      raise exception 'el caso está cerrado: no cambia'
        using errcode = 'P0001', hint = 'caso_cerrado';
    end if;
    if old.codigo_usado_en is not null
       and (new.codigo_hash, new.codigo_vence_en, new.codigo_usado_en)
           is distinct from (old.codigo_hash, old.codigo_vence_en, old.codigo_usado_en) then
      raise exception 'el código del caso ya se usó'
        using errcode = 'P0001', hint = 'codigo_usado';
    end if;
    if (new.persona_id, new.sujeto_correo, new.sujeto_descripcion, new.alcance_consorcio_id,
        new.derecho, new.origen_pedido, new.referencia_pedido, new.abierto_en)
       is distinct from
       (old.persona_id, old.sujeto_correo, old.sujeto_descripcion, old.alcance_consorcio_id,
        old.derecho, old.origen_pedido, old.referencia_pedido, old.abierto_en) then
      raise exception 'el titular, el alcance y el pedido de un caso no cambian'
        using errcode = 'P0001', hint = 'caso_inmutable';
    end if;
    return new;
  end
  $$;
create trigger caso_privacidad_fijo before update on plataforma.caso_privacidad
  for each row execute function plataforma.caso_privacidad_fijo();
-- §4.2, punto 4: el correo de la persona es confiable si aceptó una invitación a ese correo y
-- tiene, en el mismo consorcio, un vínculo confirmado por un tercero o un mandato. `c` nulo es
-- el alcance `cuenta`: alcanza con un consorcio donde se cumplan las dos cosas. La aceptación se
-- lee de la invitación: `aceptar_invitacion` (0007, 0014) escribe `persona_id` y el evento
-- `identidad.invitacion.aceptar` en la misma transacción, y un check de 0007 ata `persona_id` a
-- `aceptada_en`. Interna.
create function plataforma.privacidad_correo_confiable(p uuid, c uuid)
  returns boolean
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select exists (
      select 1
        from nucleo.persona per
        join nucleo.invitacion i on i.persona_id = per.id and i.correo = per.correo
       where per.id = p and per.eliminado_en is null
         and (c is null or i.consorcio_id = c)
         and (exists (select 1 from nucleo.vinculo v
                       where v.consorcio_id = i.consorcio_id and v.persona_id = p
                         and v.confirmado_en is not null)
              or exists (select 1 from nucleo.mandato m
                          where m.consorcio_id = i.consorcio_id and m.persona_id = p)))
  $$;
revoke all on function plataforma.privacidad_correo_confiable(uuid, uuid) from public;

-- Abre el caso. El titular se busca por correo: primero una persona (sin suprimir), si no, un
-- invitado que nunca aceptó (`sujeto_correo`). Con `descripcion` y sin correo, un titular sin
-- cuenta ni correo (caso manual). Devuelve el caso y el correo al que irá el código (nulo si no
-- hay correo): el destino lo decide la base.
create function plataforma.privacidad_abrir(correo text, alcance uuid, descripcion text,
                                            derecho_pedido text, origen text, referencia text)
  returns table (caso uuid, destino text)
  language plpgsql security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c text := lower(btrim(correo));
    p uuid;
    sujeto text;
    nuevo uuid;
  begin
    if (c is null) = (descripcion is null) then
      raise exception 'se pide el correo del titular o una descripción, no los dos'
        using errcode = 'P0001', hint = 'titular_ambiguo';
    end if;
    if alcance is not null and not exists (select 1 from nucleo.consorcio x where x.id = alcance)
    then
      raise exception 'el consorcio del alcance no existe'
        using errcode = 'P0001', hint = 'alcance_inexistente';
    end if;
    if derecho_pedido = 'rectificacion_nombre' and alcance is null and c is null then
      raise exception 'la rectificación pide un titular con correo'
        using errcode = 'P0001', hint = 'titular_sin_correo';
    end if;
    if c is not null then
      select x.id into p from nucleo.persona x where x.correo = c and x.eliminado_en is null;
      if p is null then
        if not exists (select 1 from nucleo.invitacion i
                        where i.correo = c and i.persona_id is null
                          and (alcance is null or i.consorcio_id = alcance)) then
          raise exception 'no hay ninguna persona ni invitación con ese correo'
            using errcode = 'P0001', hint = 'titular_inexistente';
        end if;
        sujeto := c;
      end if;
    end if;
    insert into plataforma.caso_privacidad
      (persona_id, sujeto_correo, sujeto_descripcion, alcance_consorcio_id, derecho,
       origen_pedido, referencia_pedido)
    values (p, sujeto, btrim(descripcion), alcance, derecho_pedido, origen, btrim(referencia))
    returning id into nuevo;
    return query select nuevo, c;
  end
  $$;

-- Registra el hash del código y publica el correo con el código al correo del titular, que
-- sale del caso. Solo una vez por caso, con el caso abierto.
create function plataforma.privacidad_enviar_codigo(caso_pedido uuid, hash bytea, asunto text,
                                                    texto text)
  returns uuid
  language plpgsql security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    k plataforma.caso_privacidad;
    para text;
    evento uuid;
  begin
    select * into k from plataforma.caso_privacidad where id = caso_pedido for update;
    if not found then
      raise exception 'el caso no existe' using errcode = 'P0001', hint = 'caso_inexistente';
    end if;
    if k.estado <> 'abierto' or k.codigo_hash is not null then
      raise exception 'el caso ya tiene un código o no está abierto'
        using errcode = 'P0001', hint = 'codigo_ya_enviado';
    end if;
    para := coalesce((select x.correo from nucleo.persona x where x.id = k.persona_id),
                     k.sujeto_correo);
    if para is null then
      raise exception 'el titular no tiene correo: es un caso manual'
        using errcode = 'P0001', hint = 'titular_sin_correo';
    end if;
    insert into plataforma.outbox (evento, consorcio_id, carga)
    values ('correo.solicitado', k.alcance_consorcio_id,
            jsonb_build_object('para', para, 'asunto', asunto, 'texto', texto))
    returning id into evento;
    update plataforma.caso_privacidad
       set codigo_hash = hash, codigo_vence_en = now() + interval '72 hours',
           codigo_evento_id = evento
     where id = caso_pedido;
    return evento;
  end
  $$;

-- Borra el correo con el código: el evento del outbox y su trabajo, salvo que el trabajo esté
-- en curso (el trabajador lo está mandando). Interna.
create function plataforma.privacidad_borrar_correo(evento_correo uuid)
  returns void
  language sql
  set search_path = pg_catalog, pg_temp
  as $$
    delete from plataforma.jobs j
     where j.clave_unica = 'notificar.correo:' || privacidad_borrar_correo.evento_correo
       and j.estado <> 'en_curso';
    delete from plataforma.outbox o
     where o.id = privacidad_borrar_correo.evento_correo
       and not exists (select 1 from plataforma.jobs j
                        where j.clave_unica
                              = 'notificar.correo:' || privacidad_borrar_correo.evento_correo);
  $$;
revoke all on function plataforma.privacidad_borrar_correo(uuid) from public;

create function plataforma.privacidad_corroborar(caso_pedido uuid, texto text)
  returns void
  language plpgsql security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    k plataforma.caso_privacidad;
  begin
    select * into k from plataforma.caso_privacidad where id = caso_pedido for update;
    if not found then
      raise exception 'el caso no existe' using errcode = 'P0001', hint = 'caso_inexistente';
    end if;
    if k.estado <> 'abierto' then
      raise exception 'el caso no está abierto' using errcode = 'P0001', hint = 'caso_no_abierto';
    end if;
    if k.corroboracion is not null then
      raise exception 'el caso ya tiene su corroboración'
        using errcode = 'P0001', hint = 'ya_corroborado';
    end if;
    update plataforma.caso_privacidad
       set corroboracion = btrim(texto), corroborado_en = now()
     where id = caso_pedido;
  end
  $$;

-- El titular devolvió el código: se compara su hash, el vencimiento y el uso. Se niega si la
-- persona tiene contraseña o TOTP (§4.2: su confirmación va por la aplicación) y si el correo no
-- es confiable y falta la corroboración.
create function plataforma.privacidad_confirmar(caso_pedido uuid, hash bytea, como text)
  returns void
  language plpgsql security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    k plataforma.caso_privacidad;
  begin
    select * into k from plataforma.caso_privacidad where id = caso_pedido for update;
    if not found then
      raise exception 'el caso no existe' using errcode = 'P0001', hint = 'caso_inexistente';
    end if;
    if k.estado <> 'abierto' then
      raise exception 'el caso no está abierto' using errcode = 'P0001', hint = 'caso_no_abierto';
    end if;
    if k.codigo_hash is null then
      raise exception 'el caso no tiene código: es un caso manual o falta enviarlo'
        using errcode = 'P0001', hint = 'sin_codigo';
    end if;
    if k.codigo_usado_en is not null then
      raise exception 'el código ya se usó' using errcode = 'P0001', hint = 'codigo_usado';
    end if;
    if k.codigo_vence_en <= now() then
      raise exception 'el código venció' using errcode = 'P0001', hint = 'codigo_vencido';
    end if;
    if hash is distinct from k.codigo_hash then
      raise exception 'el código no es el del caso'
        using errcode = 'P0001', hint = 'codigo_incorrecto';
    end if;
    -- Un invitado que aceptó después de abrir el caso ya tiene cuenta: el caso por su correo no
    -- vale (saltearía el control de contraseña o TOTP). Se rechaza y se abre uno para la persona.
    if k.sujeto_correo is not null
       and exists (select 1 from nucleo.persona x
                    where x.correo = k.sujeto_correo and x.eliminado_en is null) then
      raise exception 'el titular ya tiene cuenta: el caso se abre de nuevo para la persona'
        using errcode = 'P0001', hint = 'titular_con_cuenta';
    end if;
    if k.persona_id is not null
       and (exists (select 1 from nucleo.contrasena x where x.persona_id = k.persona_id)
            or nucleo.tiene_segundo_factor(k.persona_id)) then
      raise exception 'la persona tiene contraseña o segundo factor: confirma en la aplicación'
        using errcode = 'P0001', hint = 'requiere_aplicacion';
    end if;
    if k.corroboracion is null
       and (k.persona_id is null
            or not plataforma.privacidad_correo_confiable(k.persona_id, k.alcance_consorcio_id))
    then
      raise exception 'el correo no es confiable: falta la corroboración'
        using errcode = 'P0001', hint = 'requiere_corroboracion';
    end if;
    update plataforma.caso_privacidad
       set codigo_usado_en = now(), confirmado_en = now(), confirmacion = btrim(como),
           estado = 'confirmado'
     where id = caso_pedido;
    perform plataforma.privacidad_borrar_correo(k.codigo_evento_id);
  end
  $$;

create function plataforma.privacidad_rechazar(caso_pedido uuid, motivo text)
  returns void
  language plpgsql security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    k plataforma.caso_privacidad;
  begin
    select * into k from plataforma.caso_privacidad where id = caso_pedido for update;
    if not found then
      raise exception 'el caso no existe' using errcode = 'P0001', hint = 'caso_inexistente';
    end if;
    if k.estado not in ('abierto', 'confirmado') then
      raise exception 'el caso ya está cerrado' using errcode = 'P0001', hint = 'caso_cerrado';
    end if;
    if length(btrim(coalesce(motivo, ''))) not between 1 and 300 then
      raise exception 'el rechazo pide un motivo' using errcode = 'P0001', hint = 'sin_motivo';
    end if;
    update plataforma.caso_privacidad
       set estado = 'rechazado', resuelto_en = now(),
           resolucion = jsonb_build_object('rechazado', btrim(motivo))
     where id = caso_pedido;
    if k.codigo_evento_id is not null then
      perform plataforma.privacidad_borrar_correo(k.codigo_evento_id);
    end if;
  end
  $$;

-- Lo que soporte ve de un caso: nunca el hash del código.
create function plataforma.privacidad_ver(caso_pedido uuid)
  returns table (id uuid, titular text, alcance_consorcio_id uuid, derecho text,
                 origen_pedido text, referencia_pedido text, abierto_en timestamptz,
                 codigo_vence_en timestamptz, codigo_usado_en timestamptz,
                 corroborado_en timestamptz, confirmado_en timestamptz, estado text,
                 correo_confiable boolean, solo_correo boolean, resolucion jsonb)
  language sql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
    select k.id,
           case when k.persona_id is not null then 'persona'
                when k.sujeto_correo is not null then 'invitado sin cuenta'
                else 'sin cuenta ni correo' end,
           k.alcance_consorcio_id, k.derecho, k.origen_pedido, k.referencia_pedido, k.abierto_en,
           k.codigo_vence_en, k.codigo_usado_en, k.corroborado_en, k.confirmado_en, k.estado,
           k.persona_id is not null
             and plataforma.privacidad_correo_confiable(k.persona_id, k.alcance_consorcio_id),
           k.persona_id is not null
             and not exists (select 1 from nucleo.contrasena x where x.persona_id = k.persona_id)
             and not nucleo.tiene_segundo_factor(k.persona_id),
           k.resolucion
      from plataforma.caso_privacidad k
     where k.id = caso_pedido
  $$;

grant usage on schema plataforma to {ROL};
"""

for _f in FUNCIONES:
    UPGRADE += f"revoke all on function {_f} from public;\n"
    UPGRADE += f"grant execute on function {_f} to {ROL};\n"

DOWNGRADE = f"""
do $guarda$
begin
  if exists (select 1 from plataforma.caso_privacidad) then
    raise exception 'el downgrade de 0039 se niega: hay casos de privacidad'
      using errcode = 'P0001', hint = 'downgrade_con_datos',
            detail = 'cada caso es la constancia de que se atendió un pedido';
  end if;
end
$guarda$;
revoke usage on schema plataforma from {ROL};
drop function {FUNCIONES[5]};
drop function {FUNCIONES[4]};
drop function {FUNCIONES[3]};
drop function {FUNCIONES[2]};
drop function {FUNCIONES[1]};
drop function {FUNCIONES[0]};
drop function plataforma.privacidad_borrar_correo(uuid);
drop function plataforma.privacidad_correo_confiable(uuid, uuid);
drop trigger caso_privacidad_fijo on plataforma.caso_privacidad;
drop function plataforma.caso_privacidad_fijo();
drop table plataforma.caso_privacidad;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
