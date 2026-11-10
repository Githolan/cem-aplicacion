"""R10: la marca del segundo factor la pone solo la base, gastando la prueba que la justifica.

Revisión de seguridad transversal de H3 (repositorio CEM,
`doc/diseno/H3_Revision_Seguridad_Transversal_2026-10-31.md`, «Otros endurecimientos anotados
(P3)»): «R10 confía en columnas que `cem_app` puede escribir (`nucleo.sesion.segundo_factor_en`)
y en el booleano de `resolver_desafio_contrasena`».

**Qué cambia.**

- `cem_app` ya no puede escribir `nucleo.sesion.segundo_factor_en`, ni `ultimo_paso` y
  `confirmado_en` de `nucleo.factor_totp`, ni `usado_en` de `nucleo.codigo_recuperacion`, ni
  `contador` y `ultimo_uso_en` de `nucleo.llave_acceso`. Tampoco puede leer
  `nucleo.codigo_recuperacion.codigo_hash`: los códigos se comparan dentro de la base.
- La marca la ponen funciones `security definer` (`search_path` fijo, revocadas a `public`,
  concedidas a `cem_app`) que, en la misma sentencia, gastan la prueba de que el segundo factor se
  verificó, y solo sobre una sesión vigente de la persona del ámbito:
  - `nucleo.verificar_sesion_con_totp(sesion, paso)`: avanza `factor_totp.ultimo_paso` al paso
    aceptado; si el paso no es mayor que el último (un código repetido) no marca nada.
  - `nucleo.confirmar_totp(sesion, paso)`: lo mismo para el alta pendiente, y la confirma.
  - `nucleo.verificar_sesion_con_recuperacion(sesion, hash)`: consume el código de recuperación
    (si existe, no está usado y la persona tiene un medio activo).
  - `nucleo.verificar_sesion_con_llave(sesion, llave, contador)`: registra el uso de una llave de
    la persona; el contador no puede retroceder (WebAuthn §6.1.1: si alguno no es cero, el nuevo
    tiene que ser mayor).
  - `nucleo.confirmar_llave(sesion, llave)`: la llave tiene que haberse registrado en la misma
    transacción.
  - `nucleo.olvidar_segundo_factor()`: borra la marca de todas las sesiones de la persona (las
    bajas de un medio).
  - `nucleo.marcar_segundo_factor(sesion, persona)`: la escritura en sí. No se concede a nadie:
    solo la llaman las anteriores. Una sesión que no es vigente o no es de la persona es un error,
    no un «no»: la API ya la validó al recibir la petición.
- `nucleo.resolver_desafio_contrasena` deja de recibir `acierto boolean`. Recibe el paso TOTP
  aceptado o el hash del código de recuperación (uno de los dos, o ninguno si el código no
  sirvió) y es ella la que decide el acierto gastando esa prueba sobre la persona del desafío.

**Por qué no más.** La comprobación criptográfica del código TOTP sigue en la API: el secreto está
cifrado con una clave que solo tiene `cem-backend` (`0008`), justamente para que un volcado de la
base no alcance para generar códigos; tampoco la firma de la llave se puede comprobar en
PostgreSQL. Por eso la base no puede saber si un paso es el de un código correcto. Lo que sí impone
es que toda marca gaste algo que no se repite (un paso mayor, un código sin usar, un contador que
avanza), que exista el medio y que la sesión sea de la persona. Para los códigos de recuperación
la prueba es completa: el HMAC lleva la clave de la API y `cem_app` ya no lee los hashes. No se
pone una ventana de tiempo al paso en la base: no impediría falsificar uno (solo lo limitaría a uno
cada 30 segundos) y ataría la base al reloj de la API. Lo mismo vale para el desafío de la
contraseña: no hay forma de que la base compruebe el código sin el secreto, así que en lugar del
booleano recibe la prueba y la gasta.

Downgrade: vuelve `resolver_desafio_contrasena` con el booleano (la definición de `0009`), borra las
funciones nuevas y devuelve los permisos de `0008` y `0070`.

Revisión: 0094
Anterior: 0093
"""

from alembic import op

revision = "0094"
down_revision = "0093"
branch_labels = None
depends_on = None

UPGRADE = r"""
create function nucleo.marcar_segundo_factor(sesion_pedida uuid, persona_pedida uuid)
  returns timestamptz
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    marcada timestamptz;
  begin
    update nucleo.sesion s set segundo_factor_en = now()
     where s.id = sesion_pedida and s.persona_id = persona_pedida
       and s.revocada_en is null and s.vence_en > now()
    returning s.segundo_factor_en into marcada;
    if marcada is null then
      raise exception 'la sesión no es vigente o no es de la persona' using errcode = 'P0001',
        hint = 'sesion_invalida';
    end if;
    return marcada;
  end
  $$;

create function nucleo.persona_del_segundo_factor()
  returns uuid
  language plpgsql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    persona uuid := plataforma.persona_actual();
  begin
    if persona is null then
      raise exception 'el segundo factor necesita a la persona en el ámbito' using
        errcode = 'P0001', hint = 'sin_persona';
    end if;
    return persona;
  end
  $$;

create function nucleo.verificar_sesion_con_totp(sesion_pedida uuid, paso_aceptado bigint)
  returns timestamptz
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    persona uuid := nucleo.persona_del_segundo_factor();
  begin
    update nucleo.factor_totp f set ultimo_paso = paso_aceptado
     where f.persona_id = persona and f.confirmado_en is not null
       and f.ultimo_paso < paso_aceptado;
    if not found then
      return null;
    end if;
    return nucleo.marcar_segundo_factor(sesion_pedida, persona);
  end
  $$;

create function nucleo.confirmar_totp(sesion_pedida uuid, paso_aceptado bigint)
  returns timestamptz
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    persona uuid := nucleo.persona_del_segundo_factor();
  begin
    update nucleo.factor_totp f set ultimo_paso = paso_aceptado, confirmado_en = now()
     where f.persona_id = persona and f.confirmado_en is null
       and f.ultimo_paso < paso_aceptado;
    if not found then
      return null;
    end if;
    return nucleo.marcar_segundo_factor(sesion_pedida, persona);
  end
  $$;

create function nucleo.verificar_sesion_con_recuperacion(sesion_pedida uuid, hash_pedido bytea)
  returns timestamptz
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    persona uuid := nucleo.persona_del_segundo_factor();
  begin
    if not nucleo.tiene_segundo_factor(persona) then
      return null;
    end if;
    update nucleo.codigo_recuperacion c set usado_en = now()
     where c.persona_id = persona and c.codigo_hash = hash_pedido and c.usado_en is null;
    if not found then
      return null;
    end if;
    return nucleo.marcar_segundo_factor(sesion_pedida, persona);
  end
  $$;

create function nucleo.verificar_sesion_con_llave(
  sesion_pedida uuid, llave_pedida uuid, contador_nuevo bigint)
  returns timestamptz
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    persona uuid := nucleo.persona_del_segundo_factor();
  begin
    update nucleo.llave_acceso l set contador = contador_nuevo, ultimo_uso_en = now()
     where l.id = llave_pedida and l.persona_id = persona
       and (contador_nuevo > l.contador or (contador_nuevo = 0 and l.contador = 0));
    if not found then
      return null;
    end if;
    return nucleo.marcar_segundo_factor(sesion_pedida, persona);
  end
  $$;

create function nucleo.confirmar_llave(sesion_pedida uuid, llave_pedida uuid)
  returns timestamptz
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    persona uuid := nucleo.persona_del_segundo_factor();
  begin
    -- `creada_en` toma `now()`, el comienzo de la transacción: solo una llave registrada en esta
    -- misma transacción lo tiene igual.
    perform 1 from nucleo.llave_acceso l
     where l.id = llave_pedida and l.persona_id = persona and l.creada_en = now()
       and l.ultimo_uso_en is null;
    if not found then
      return null;
    end if;
    return nucleo.marcar_segundo_factor(sesion_pedida, persona);
  end
  $$;

create function nucleo.olvidar_segundo_factor()
  returns integer
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    persona uuid := nucleo.persona_del_segundo_factor();
    cuantas integer;
  begin
    update nucleo.sesion s set segundo_factor_en = null
     where s.persona_id = persona and s.segundo_factor_en is not null;
    get diagnostics cuantas = row_count;
    return cuantas;
  end
  $$;

drop function nucleo.resolver_desafio_contrasena(
  bytea, boolean, bytea, uuid, text, inet, interval, integer);

-- Resuelve el desafío con la prueba del código que la API comprobó en la misma transacción: el
-- paso TOTP aceptado o el hash del código de recuperación. La base decide el acierto gastándola
-- (paso mayor que el último, código sin usar); sin prueba, o si no se puede gastar, es un intento
-- fallido. Correcto: borra el desafío y abre la sesión con el segundo factor ya verificado.
create function nucleo.resolver_desafio_contrasena(
  token bytea, paso_aceptado bigint, recuperacion_hash bytea, sesion_token bytea,
  dispositivo_pedido uuid, agente_pedido text, ip_pedido inet, vigencia interval,
  maximo_intentos integer)
  returns table (resultado text, persona uuid, sesion uuid, vence timestamptz)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    d nucleo.desafio_contrasena;
    acierto boolean := false;
  begin
    if paso_aceptado is not null and recuperacion_hash is not null then
      raise exception 'una sola prueba por intento' using errcode = 'P0001',
        hint = 'dos_pruebas';
    end if;
    select * into d from nucleo.desafio_contrasena x where x.token_hash = token for update;
    if not found then
      return query select 'inexistente'::text, null::uuid, null::uuid, null::timestamptz;
      return;
    end if;
    if d.vence_en <= now() then
      return query select 'vencido'::text, null::uuid, null::uuid, null::timestamptz;
      return;
    end if;
    if d.intentos >= maximo_intentos then
      return query select 'agotado'::text, null::uuid, null::uuid, null::timestamptz;
      return;
    end if;
    if paso_aceptado is not null then
      update nucleo.factor_totp f set ultimo_paso = paso_aceptado
       where f.persona_id = d.persona_id and f.confirmado_en is not null
         and f.ultimo_paso < paso_aceptado;
      acierto := found;
    elsif recuperacion_hash is not null and nucleo.tiene_segundo_factor(d.persona_id) then
      update nucleo.codigo_recuperacion c set usado_en = now()
       where c.persona_id = d.persona_id and c.codigo_hash = recuperacion_hash
         and c.usado_en is null;
      acierto := found;
    end if;
    if not acierto then
      update nucleo.desafio_contrasena set intentos = intentos + 1 where persona_id = d.persona_id;
      return query select 'incorrecto'::text, null::uuid, null::uuid, null::timestamptz;
      return;
    end if;
    delete from nucleo.desafio_contrasena where persona_id = d.persona_id;
    return query
    insert into nucleo.sesion as s
      (persona_id, token_hash, dispositivo, agente, ip, vence_en, segundo_factor_en)
    values (d.persona_id, sesion_token, dispositivo_pedido, agente_pedido, ip_pedido,
            now() + vigencia, now())
    returning 'ok'::text, s.persona_id, s.id, s.vence_en;
  end
  $$;

-- Privilegios (§8.6) ------------------------------------------------------------------

revoke update (segundo_factor_en) on nucleo.sesion from cem_app;
revoke update (confirmado_en, ultimo_paso) on nucleo.factor_totp from cem_app;
revoke update (usado_en) on nucleo.codigo_recuperacion from cem_app;
revoke select on nucleo.codigo_recuperacion from cem_app;
grant select (id, persona_id, creado_en, usado_en) on nucleo.codigo_recuperacion to cem_app;
revoke update (contador, ultimo_uso_en) on nucleo.llave_acceso from cem_app;

revoke all on function
  nucleo.marcar_segundo_factor(uuid, uuid),
  nucleo.persona_del_segundo_factor(),
  nucleo.verificar_sesion_con_totp(uuid, bigint),
  nucleo.confirmar_totp(uuid, bigint),
  nucleo.verificar_sesion_con_recuperacion(uuid, bytea),
  nucleo.verificar_sesion_con_llave(uuid, uuid, bigint),
  nucleo.confirmar_llave(uuid, uuid),
  nucleo.olvidar_segundo_factor(),
  nucleo.resolver_desafio_contrasena(
    bytea, bigint, bytea, bytea, uuid, text, inet, interval, integer)
  from public;
grant execute on function
  nucleo.verificar_sesion_con_totp(uuid, bigint),
  nucleo.confirmar_totp(uuid, bigint),
  nucleo.verificar_sesion_con_recuperacion(uuid, bytea),
  nucleo.verificar_sesion_con_llave(uuid, uuid, bigint),
  nucleo.confirmar_llave(uuid, uuid),
  nucleo.olvidar_segundo_factor(),
  nucleo.resolver_desafio_contrasena(
    bytea, bigint, bytea, bytea, uuid, text, inet, interval, integer)
  to cem_app;
"""

DOWNGRADE = r"""
drop function nucleo.resolver_desafio_contrasena(
  bytea, bigint, bytea, bytea, uuid, text, inet, interval, integer);

-- La de `0009`, sin cambios.
create function nucleo.resolver_desafio_contrasena(
  token bytea, acierto boolean, sesion_token bytea, dispositivo_pedido uuid,
  agente_pedido text, ip_pedido inet, vigencia interval, maximo_intentos integer)
  returns table (resultado text, persona uuid, sesion uuid, vence timestamptz)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    d nucleo.desafio_contrasena;
  begin
    select * into d from nucleo.desafio_contrasena x where x.token_hash = token for update;
    if not found then
      return query select 'inexistente'::text, null::uuid, null::uuid, null::timestamptz;
      return;
    end if;
    if d.vence_en <= now() then
      return query select 'vencido'::text, null::uuid, null::uuid, null::timestamptz;
      return;
    end if;
    if d.intentos >= maximo_intentos then
      return query select 'agotado'::text, null::uuid, null::uuid, null::timestamptz;
      return;
    end if;
    if not acierto then
      update nucleo.desafio_contrasena set intentos = intentos + 1 where persona_id = d.persona_id;
      return query select 'incorrecto'::text, null::uuid, null::uuid, null::timestamptz;
      return;
    end if;
    delete from nucleo.desafio_contrasena where persona_id = d.persona_id;
    return query
    insert into nucleo.sesion as s
      (persona_id, token_hash, dispositivo, agente, ip, vence_en, segundo_factor_en)
    values (d.persona_id, sesion_token, dispositivo_pedido, agente_pedido, ip_pedido,
            now() + vigencia, now())
    returning 'ok'::text, s.persona_id, s.id, s.vence_en;
  end
  $$;
revoke all on function
  nucleo.resolver_desafio_contrasena(bytea, boolean, bytea, uuid, text, inet, interval, integer)
  from public;
grant execute on function
  nucleo.resolver_desafio_contrasena(bytea, boolean, bytea, uuid, text, inet, interval, integer)
  to cem_app;

drop function
  nucleo.olvidar_segundo_factor(),
  nucleo.confirmar_llave(uuid, uuid),
  nucleo.verificar_sesion_con_llave(uuid, uuid, bigint),
  nucleo.verificar_sesion_con_recuperacion(uuid, bytea),
  nucleo.confirmar_totp(uuid, bigint),
  nucleo.verificar_sesion_con_totp(uuid, bigint),
  nucleo.persona_del_segundo_factor(),
  nucleo.marcar_segundo_factor(uuid, uuid);

grant update (contador, ultimo_uso_en) on nucleo.llave_acceso to cem_app;
revoke select (id, persona_id, creado_en, usado_en) on nucleo.codigo_recuperacion from cem_app;
grant select, update (usado_en) on nucleo.codigo_recuperacion to cem_app;
grant update (confirmado_en, ultimo_paso) on nucleo.factor_totp to cem_app;
grant update (segundo_factor_en) on nucleo.sesion to cem_app;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
