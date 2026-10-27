"""RF37, corte 2d-3: supresión (plan y ejecución) y rectificación del nombre.

Diseño: `doc/diseno/H2d_RF37_Diseno.md` del repositorio CEM, §2, §6 y §7. Las funciones son de
`cem_migrate` (`security definer`) y solo las ejecuta `cem_soporte_privacidad` (0039). Escriben en
el caso, nunca en `auditoria.eventos` (decisión del 26-10-2026). Las dos exigen el caso
`confirmado` y del derecho que corresponde; al terminar lo cierran (`resuelto`).

- **Supresión** (§6). `privacidad_plan_supresion` devuelve el plan sin tocar nada: una lista de
  `{que, tratamiento, filas, motivo}` con `tratamiento` = `borrar`, `anonimizar` o `conservar`.
  `privacidad_ejecutar_supresion` recibe el plan que vio soporte y se niega (`plan_cambio`) si el
  plan de ese momento no es el mismo; si lo es, lo aplica, lo guarda como resolución y encola el
  correo con la resolución al correo del titular, leído **antes** de anonimizarlo.
  - **Alcance `cuenta`** (el caso sin consorcio): lo global. Sesiones, enlaces y códigos de acceso
    e idempotencia se borran. La persona se anonimiza solo si no tiene un vínculo ni un mandato
    sin terminar en ningún consorcio; si lo tiene se conserva, y sus credenciales también (borrarlas
    con la cuenta en uso la dejaría sin segundo factor). Las invitaciones y la cola, de todos los
    consorcios y las sin consorcio.
  - **Alcance de un consorcio:** solo lo de ese consorcio (sus invitaciones y su cola). Lo global
    se conserva y el plan lo dice.
  - Invitaciones a su correo (o aceptadas por la persona): las pendientes se borran; las demás se
    anonimizan (correo `suprimida+<id>@invalid.cem` y, en las de vínculo, nombre «Persona
    suprimida»). Cola: los trabajos `pendiente` o `muerto` con su correo como destino, y los
    eventos del outbox con su correo que ya no tengan trabajo; los `hecho` los purga la tarea de
    siempre (30 días) y los `en_curso` se están mandando.
  - Se conservan, con su cuenta en el plan: vínculos (los terminados con `nombre_declarado`
    quedan pendientes del plazo probatorio de RF01, §9 punto 2), mandatos, verificaciones de
    identidad y eventos de auditoría. Copias y documentos, sin cuenta: las copias se alcanzan al
    vencer y los documentos son del consorcio.
- **Rectificación del nombre** (§2). Con alcance `cuenta`, `persona.nombre`; con un consorcio, el
  `nombre_declarado` de sus vínculos en ese consorcio (el disparador de 0020 no lo impide). La
  resolución no guarda el nombre anterior ni el nuevo.

Riesgo residual (la dirección vieja del titular sigue, con plazo o como constancia):
- el correo con la resolución y los ya enviados o en envío, en el outbox y la cola, hasta que la
  purga de siempre los borra a los 30 días (el plan los cuenta y la resolución lo dice);
- si el despachador convierte un evento en trabajo justo mientras corre la supresión, el evento se
  borra y su trabajo, nacido después de la lectura, se manda igual;
- `caso_privacidad.sujeto_correo` de un caso de invitado: es la constancia del pedido (H2d §9,
  punto 5) y no cambia.

Revisión: 0040
Anterior: 0039
"""

from alembic import op

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None

ROL = "cem_soporte_privacidad"
FUNCIONES = (
    "plataforma.privacidad_plan_supresion(uuid)",
    "plataforma.privacidad_ejecutar_supresion(uuid, jsonb, text, text)",
    "plataforma.privacidad_rectificar_nombre(uuid, text)",
)

UPGRADE = """
-- El caso que se puede resolver: existe, es del derecho pedido y está confirmado. Lo bloquea.
-- Interna.
create function plataforma.privacidad_caso_para_resolver(caso_pedido uuid, derecho_pedido text)
  returns plataforma.caso_privacidad
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    k plataforma.caso_privacidad;
  begin
    select * into k from plataforma.caso_privacidad where id = caso_pedido for update;
    if not found then
      raise exception 'el caso no existe' using errcode = 'P0001', hint = 'caso_inexistente';
    end if;
    if k.estado in ('resuelto', 'rechazado') then
      raise exception 'el caso está cerrado' using errcode = 'P0001', hint = 'caso_cerrado';
    end if;
    if k.derecho <> derecho_pedido then
      raise exception 'el caso es de otro derecho' using errcode = 'P0001', hint = 'otro_derecho';
    end if;
    if k.estado <> 'confirmado' then
      raise exception 'el titular no confirmó el caso'
        using errcode = 'P0001', hint = 'caso_sin_confirmar';
    end if;
    -- Como al confirmar (0039): un invitado que aceptó después ya tiene cuenta, y resolver por su
    -- correo saltearía el control de contraseña o TOTP.
    if k.sujeto_correo is not null
       and exists (select 1 from nucleo.persona x
                    where x.correo = k.sujeto_correo and x.eliminado_en is null) then
      raise exception 'el titular ya tiene cuenta: el caso se abre de nuevo para la persona'
        using errcode = 'P0001', hint = 'titular_con_cuenta';
    end if;
    if exists (select 1 from nucleo.persona x
                where x.id = k.persona_id and x.eliminado_en is not null) then
      raise exception 'la persona ya se suprimió: no queda nada suyo que resolver por este caso'
        using errcode = 'P0001', hint = 'titular_suprimido';
    end if;
    return k;
  end
  $$;
revoke all on function plataforma.privacidad_caso_para_resolver(uuid, text) from public;

-- §6. Con `ejecutar` falso solo cuenta; con verdadero aplica y devuelve lo mismo con las filas
-- tocadas. El orden de la lista es fijo: el plan y la ejecución se comparan entre sí. Interna.
create function plataforma.privacidad_supresion(k plataforma.caso_privacidad, ejecutar boolean)
  returns jsonb
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    alc uuid := k.alcance_consorcio_id;
    correos text[];
    sin_terminar integer := 0;
    anonimizar boolean := false;
    plan jsonb := '[]'::jsonb;
    n integer;
  begin
    if k.persona_id is not null then
      correos := array[(select p.correo from nucleo.persona p where p.id = k.persona_id)];
    elsif k.sujeto_correo is not null then
      correos := array[k.sujeto_correo];
    else
      raise exception 'el caso manual no tiene datos ligados que suprimir'
        using errcode = 'P0001', hint = 'caso_manual';
    end if;
    -- Cerrojos en el orden único del código (0025): las invitaciones que se van a tocar (las de
    -- mandato después de su mandato, como `aceptar_invitacion_mandato`, 0015), la persona por su
    -- correo (el mismo cerrojo que `aceptar_invitacion` e `importar_vinculo`) y su fila: nadie le
    -- da un vínculo nuevo ni acepta una invitación mientras se decide si se anonimiza.
    perform 1 from nucleo.invitacion i
     where (alc is null or i.consorcio_id = alc)
       and (i.correo = any (correos) or i.persona_id = k.persona_id)
     order by i.id for update;
    perform 1 from nucleo.mandato m
     where m.id in (select i.mandato_id from nucleo.invitacion_mandato i
                     where (alc is null or i.consorcio_id = alc)
                       and (i.correo = any (correos) or i.persona_id = k.persona_id))
     order by m.id for update;
    perform 1 from nucleo.invitacion_mandato i
     where (alc is null or i.consorcio_id = alc)
       and (i.correo = any (correos) or i.persona_id = k.persona_id)
     order by i.id for update;
    perform pg_advisory_xact_lock(hashtextextended('persona:' || correos[1], 0));
    perform 1 from nucleo.persona p where p.id = k.persona_id for update;

    -- Lo global: la persona, sus credenciales, sus accesos y la idempotencia.
    if k.persona_id is not null and alc is null then
      select count(*) into sin_terminar from (
        select 1 from nucleo.vinculo v
         where v.persona_id = k.persona_id and v.finalizado_en is null
           and (v.vigente_hasta is null
                or v.vigente_hasta > nucleo.hoy_del_consorcio(v.consorcio_id))
        union all
        select 1 from nucleo.mandato m
         where m.persona_id = k.persona_id and m.finalizado_en is null
           and (m.vigente_hasta is null
                or m.vigente_hasta > nucleo.hoy_del_consorcio(m.consorcio_id))
      ) x;
      anonimizar := sin_terminar = 0;

      if anonimizar then
        if ejecutar then
          update nucleo.persona
             set correo = 'suprimida+' || id || '@invalid.cem', nombre = 'Persona suprimida',
                 eliminado_en = now()
           where id = k.persona_id;
        end if;
        plan := plan || jsonb_build_array(jsonb_build_object(
          'que', 'persona: correo y nombre', 'tratamiento', 'anonimizar', 'filas', 1,
          'motivo', 'la fila no se borra (la referencian los actos del consorcio); el correo queda '
            'libre'));
      else
        plan := plan || jsonb_build_array(jsonb_build_object(
          'que', 'persona: correo y nombre', 'tratamiento', 'conservar', 'filas', 1,
          'motivo', 'tiene un vínculo o un mandato sin terminar en un consorcio: el consorcio '
            'necesita saber quién es'));
      end if;

      select (select count(*) from nucleo.sesion s where s.persona_id = k.persona_id)
             + (select count(*) from nucleo.enlace_acceso e where e.persona_id = k.persona_id)
        into n;
      if ejecutar then
        -- Los códigos de acceso se van con su enlace (on delete cascade, 0006).
        delete from nucleo.enlace_acceso where persona_id = k.persona_id;
        delete from nucleo.sesion where persona_id = k.persona_id;
      end if;
      plan := plan || jsonb_build_array(jsonb_build_object(
        'que', 'sesiones, enlaces y códigos de acceso', 'tratamiento', 'borrar', 'filas', n,
        'motivo', 'datos técnicos; borrarlos cierra las sesiones abiertas'));

      select (select count(*) from nucleo.contrasena x where x.persona_id = k.persona_id)
             + (select count(*) from nucleo.factor_totp x where x.persona_id = k.persona_id)
             + (select count(*) from nucleo.codigo_recuperacion x where x.persona_id = k.persona_id)
             + (select count(*) from nucleo.desafio_contrasena x where x.persona_id = k.persona_id)
        into n;
      if anonimizar then
        if ejecutar then
          delete from nucleo.contrasena where persona_id = k.persona_id;
          delete from nucleo.factor_totp where persona_id = k.persona_id;
          delete from nucleo.codigo_recuperacion where persona_id = k.persona_id;
          delete from nucleo.desafio_contrasena where persona_id = k.persona_id;
        end if;
        plan := plan || jsonb_build_array(jsonb_build_object(
          'que', 'contraseña, TOTP y códigos de recuperación', 'tratamiento', 'borrar',
          'filas', n, 'motivo', 'la cuenta se anonimiza: ya no se puede entrar'));
      else
        plan := plan || jsonb_build_array(jsonb_build_object(
          'que', 'contraseña, TOTP y códigos de recuperación', 'tratamiento', 'conservar',
          'filas', n, 'motivo', 'la cuenta sigue en uso: borrarlos la dejaría sin segundo '
            'factor'));
      end if;

      if ejecutar then
        delete from plataforma.idempotencia where persona_id = k.persona_id;
        get diagnostics n = row_count;
      else
        select count(*) into n from plataforma.idempotencia where persona_id = k.persona_id;
      end if;
      plan := plan || jsonb_build_array(jsonb_build_object(
        'que', 'respuestas guardadas por idempotencia', 'tratamiento', 'borrar', 'filas', n,
        'motivo', 'vencen a las 24 horas de todos modos'));
    elsif k.persona_id is not null then
      plan := plan || jsonb_build_array(jsonb_build_object(
        'que', 'datos de la cuenta (persona, credenciales, accesos)', 'tratamiento', 'conservar',
        'filas', null, 'motivo', 'el caso es de un consorcio: lo global solo se toca con alcance '
          'cuenta'));
    end if;

    -- Invitaciones a su correo o aceptadas por la persona, del alcance.
    if ejecutar then
      delete from nucleo.invitacion i
       where (alc is null or i.consorcio_id = alc)
         and (i.correo = any (correos) or i.persona_id = k.persona_id)
         and i.aceptada_en is null and i.revocada_en is null and i.vence_en > now();
      get diagnostics n = row_count;
    else
      select count(*) into n from nucleo.invitacion i
       where (alc is null or i.consorcio_id = alc)
         and (i.correo = any (correos) or i.persona_id = k.persona_id)
         and i.aceptada_en is null and i.revocada_en is null and i.vence_en > now();
    end if;
    plan := plan || jsonb_build_array(jsonb_build_object(
      'que', 'invitaciones pendientes', 'tratamiento', 'borrar', 'filas', n,
      'motivo', 'una invitación pendiente no prueba nada'));

    if ejecutar then
      update nucleo.invitacion i
         set correo = 'suprimida+' || i.id || '@invalid.cem', nombre = 'Persona suprimida'
       where (alc is null or i.consorcio_id = alc)
         and (i.correo = any (correos) or i.persona_id = k.persona_id)
         and (i.correo, i.nombre)
             is distinct from ('suprimida+' || i.id || '@invalid.cem', 'Persona suprimida');
      get diagnostics n = row_count;
    else
      select count(*) into n from nucleo.invitacion i
       where (alc is null or i.consorcio_id = alc)
         and (i.correo = any (correos) or i.persona_id = k.persona_id)
         and not (i.aceptada_en is null and i.revocada_en is null and i.vence_en > now())
         and (i.correo, i.nombre)
             is distinct from ('suprimida+' || i.id || '@invalid.cem', 'Persona suprimida');
    end if;
    plan := plan || jsonb_build_array(jsonb_build_object(
      'que', 'invitaciones aceptadas, revocadas o vencidas: correo y nombre',
      'tratamiento', 'anonimizar',
      'filas', n, 'motivo', 'son historia del vínculo: se conserva la fila sin el correo ni el '
        'nombre'));

    if ejecutar then
      delete from nucleo.invitacion_mandato i
       where (alc is null or i.consorcio_id = alc)
         and (i.correo = any (correos) or i.persona_id = k.persona_id)
         and i.aceptada_en is null and i.revocada_en is null
         and (i.vence_en is null or i.vence_en > now());
      get diagnostics n = row_count;
    else
      select count(*) into n from nucleo.invitacion_mandato i
       where (alc is null or i.consorcio_id = alc)
         and (i.correo = any (correos) or i.persona_id = k.persona_id)
         and i.aceptada_en is null and i.revocada_en is null
         and (i.vence_en is null or i.vence_en > now());
    end if;
    plan := plan || jsonb_build_array(jsonb_build_object(
      'que', 'invitaciones a un mandato pendientes', 'tratamiento', 'borrar', 'filas', n,
      'motivo', 'una invitación pendiente no prueba nada'));

    if ejecutar then
      update nucleo.invitacion_mandato i
         set correo = 'suprimida+' || i.id || '@invalid.cem'
       where (alc is null or i.consorcio_id = alc)
         and (i.correo = any (correos) or i.persona_id = k.persona_id)
         and i.correo <> 'suprimida+' || i.id || '@invalid.cem';
      get diagnostics n = row_count;
    else
      select count(*) into n from nucleo.invitacion_mandato i
       where (alc is null or i.consorcio_id = alc)
         and (i.correo = any (correos) or i.persona_id = k.persona_id)
         and not (i.aceptada_en is null and i.revocada_en is null
                  and (i.vence_en is null or i.vence_en > now()))
         and i.correo <> 'suprimida+' || i.id || '@invalid.cem';
    end if;
    plan := plan || jsonb_build_array(jsonb_build_object(
      'que', 'invitaciones a un mandato aceptadas, revocadas o vencidas: correo',
      'tratamiento', 'anonimizar',
      'filas', n, 'motivo', 'son historia del mandato: se conserva la fila sin el correo'));

    -- La cola: con alcance cuenta, la sin consorcio (correos de acceso) y la de todos los
    -- consorcios; con un consorcio, la de ese consorcio.
    if ejecutar then
      delete from plataforma.jobs j
       where j.carga ->> 'para' = any (correos) and j.estado in ('pendiente', 'muerto')
         and (alc is null or j.consorcio_id = alc);
      get diagnostics n = row_count;
    else
      select count(*) into n from plataforma.jobs j
       where j.carga ->> 'para' = any (correos) and j.estado in ('pendiente', 'muerto')
         and (alc is null or j.consorcio_id = alc);
    end if;
    plan := plan || jsonb_build_array(jsonb_build_object(
      'que', 'correos pendientes o fallidos en la cola', 'tratamiento', 'borrar', 'filas', n,
      'motivo', 'los enviados los purga la tarea de siempre a los 30 días; los que se están '
        'mandando quedan'));
    -- Sus eventos del outbox, salvo los que todavía tienen un trabajo (enviado o en curso). El
    -- plan los cuenta como quedarán después de borrar los trabajos de arriba.
    if ejecutar then
      delete from plataforma.outbox o
       where o.carga ->> 'para' = any (correos) and (alc is null or o.consorcio_id = alc)
         and not exists (select 1 from plataforma.jobs j
                          where j.clave_unica = 'notificar.correo:' || o.id);
      get diagnostics n = row_count;
    else
      select count(*) into n from plataforma.outbox o
       where o.carga ->> 'para' = any (correos) and (alc is null or o.consorcio_id = alc)
         and not exists (select 1 from plataforma.jobs j
                          where j.clave_unica = 'notificar.correo:' || o.id
                            and not (j.carga ->> 'para' = any (correos)
                                     and j.estado in ('pendiente', 'muerto')
                                     and (alc is null or j.consorcio_id = alc)));
    end if;
    plan := plan || jsonb_build_array(jsonb_build_object(
      'que', 'eventos de correo sin trabajo en la cola', 'tratamiento', 'borrar', 'filas', n,
      'motivo', 'el pedido de envío de un correo que ya no se va a mandar'));
    select count(*) into n from plataforma.jobs j
     where j.carga ->> 'para' = any (correos) and j.estado in ('hecho', 'en_curso')
       and (alc is null or j.consorcio_id = alc);
    plan := plan || jsonb_build_array(jsonb_build_object(
      'que', 'correos ya enviados o que se están enviando', 'tratamiento', 'conservar',
      'filas', n, 'motivo', 'la tarea de siempre los borra a los 30 días de enviados'));

    -- Lo que se conserva, con su cuenta.
    if k.persona_id is not null then
      select count(*) into n from nucleo.vinculo v
       where v.persona_id = k.persona_id and (alc is null or v.consorcio_id = alc);
      plan := plan || jsonb_build_array(jsonb_build_object(
        'que', 'vínculos con unidades', 'tratamiento', 'conservar', 'filas', n,
        'motivo', 'son la historia de quién tuvo cada unidad: actos del consorcio'));
      select count(*) into n from nucleo.vinculo v
       where v.persona_id = k.persona_id and (alc is null or v.consorcio_id = alc)
         and v.finalizado_en is not null and v.nombre_declarado is not null;
      plan := plan || jsonb_build_array(jsonb_build_object(
        'que', 'nombre declarado de vínculos terminados', 'tratamiento', 'conservar', 'filas', n,
        'motivo', 'pendiente: se anonimiza cuando RF01 fije el plazo probatorio (H2d §9, punto '
          '2)'));
      select count(*) into n from nucleo.mandato m
       where m.persona_id = k.persona_id and (alc is null or m.consorcio_id = alc);
      plan := plan || jsonb_build_array(jsonb_build_object(
        'que', 'mandatos', 'tratamiento', 'conservar', 'filas', n,
        'motivo', 'prueba de quién tuvo el poder: actos del consorcio'));
      select count(*) into n from nucleo.verificacion_identidad vi
       where vi.persona_id = k.persona_id and (alc is null or vi.consorcio_id = alc);
      plan := plan || jsonb_build_array(jsonb_build_object(
        'que', 'verificaciones de identidad', 'tratamiento', 'conservar', 'filas', n,
        'motivo', 'prueba de los controles de R10; la huella es un HMAC por consorcio, sin el '
          'documento'));
      select count(*) into n from auditoria.eventos e
       where (e.actor_persona_id = k.persona_id or e.entidad_id = k.persona_id)
         and (alc is null or e.consorcio_id = alc);
      plan := plan || jsonb_build_array(jsonb_build_object(
        'que', 'eventos de auditoría', 'tratamiento', 'conservar', 'filas', n,
        'motivo', 'la cadena de hashes no se edita; llevan ids opacos y pueden tener IP o textos '
          'libres que lo nombren'));
    end if;
    plan := plan || jsonb_build_array(
      jsonb_build_object('que', 'copias de la base y de los documentos', 'tratamiento', 'conservar',
        'filas', null, 'motivo', 'no se editan: la supresión las alcanza cuando vencen'),
      jsonb_build_object('que', 'contenido de documentos del consorcio', 'tratamiento', 'conservar',
        'filas', null, 'motivo', 'son del consorcio: un documento con datos del titular es un caso '
          'de borrado por soporte'));
    return plan;
  end
  $$;
revoke all on function plataforma.privacidad_supresion(plataforma.caso_privacidad, boolean)
  from public;

create function plataforma.privacidad_plan_supresion(caso_pedido uuid)
  returns jsonb
  language plpgsql security definer
  set search_path = pg_catalog, pg_temp
  as $$
  begin
    return plataforma.privacidad_supresion(
      plataforma.privacidad_caso_para_resolver(caso_pedido, 'supresion'), false);
  end
  $$;

-- `plan_visto` es el plan con el que soporte armó el correo: si cambió, no se aplica nada.
create function plataforma.privacidad_ejecutar_supresion(caso_pedido uuid, plan_visto jsonb,
                                                         asunto text, texto text)
  returns jsonb
  language plpgsql security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    k plataforma.caso_privacidad;
    para text;
    hecho jsonb;
  begin
    k := plataforma.privacidad_caso_para_resolver(caso_pedido, 'supresion');
    if plataforma.privacidad_supresion(k, false) is distinct from plan_visto then
      raise exception 'el plan cambió desde que se mostró: volver a pedirlo'
        using errcode = 'P0001', hint = 'plan_cambio';
    end if;
    para := coalesce((select p.correo from nucleo.persona p where p.id = k.persona_id),
                     k.sujeto_correo);
    hecho := plataforma.privacidad_supresion(k, true);
    insert into plataforma.outbox (evento, consorcio_id, carga)
    values ('correo.solicitado', k.alcance_consorcio_id,
            jsonb_build_object('para', para, 'asunto', asunto, 'texto', texto));
    update plataforma.caso_privacidad
       set estado = 'resuelto', resuelto_en = now(),
           resolucion = jsonb_build_object('supresion', hecho)
     where id = caso_pedido;
    return hecho;
  end
  $$;

create function plataforma.privacidad_rectificar_nombre(caso_pedido uuid, nombre_nuevo text)
  returns jsonb
  language plpgsql security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    k plataforma.caso_privacidad;
    limpio text := btrim(nombre_nuevo);
    resultado jsonb;
    n integer;
  begin
    k := plataforma.privacidad_caso_para_resolver(caso_pedido, 'rectificacion_nombre');
    if limpio is null or length(limpio) not between 1 and 100 then
      raise exception 'el nombre tiene que tener entre 1 y 100 caracteres'
        using errcode = 'P0001', hint = 'nombre_invalido';
    end if;
    if k.persona_id is null then
      raise exception 'el titular no tiene cuenta: no hay nombre que rectificar'
        using errcode = 'P0001', hint = 'titular_sin_cuenta';
    end if;
    if k.alcance_consorcio_id is null then
      update nucleo.persona set nombre = limpio
       where id = k.persona_id and eliminado_en is null;
      get diagnostics n = row_count;
      resultado := jsonb_build_object('rectificado', 'persona.nombre', 'filas', n);
    else
      update nucleo.vinculo v set nombre_declarado = limpio
       where v.persona_id = k.persona_id and v.consorcio_id = k.alcance_consorcio_id
         and v.nombre_declarado is not null;
      get diagnostics n = row_count;
      resultado := jsonb_build_object('rectificado', 'vinculo.nombre_declarado', 'filas', n);
    end if;
    if n = 0 then
      raise exception 'no hay ningún nombre que rectificar en el alcance del caso'
        using errcode = 'P0001', hint = 'nada_que_rectificar';
    end if;
    update plataforma.caso_privacidad
       set estado = 'resuelto', resuelto_en = now(), resolucion = resultado
     where id = caso_pedido;
    return resultado;
  end
  $$;
"""

for _f in FUNCIONES:
    UPGRADE += f"revoke all on function {_f} from public;\n"
    UPGRADE += f"grant execute on function {_f} to {ROL};\n"

DOWNGRADE = f"""
drop function {FUNCIONES[2]};
drop function {FUNCIONES[1]};
drop function {FUNCIONES[0]};
drop function plataforma.privacidad_supresion(plataforma.caso_privacidad, boolean);
drop function plataforma.privacidad_caso_para_resolver(uuid, text);
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
