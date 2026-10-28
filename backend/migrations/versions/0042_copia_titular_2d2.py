"""2d-2: el titular confirma su caso y recibe la copia de sus datos dentro de la aplicación (RF37).

Diseño: `doc/diseno/H2d_RF37_Diseno.md` del repositorio CEM, §5 y §9.1. Decisión del propietario
del 28-10-2026: opción (a), entrega dentro de la aplicación **exclusivamente al titular
autenticado**. La copia no va por correo ni adjunta, no tiene una URL pública, soporte no la
descarga para reenviarla y no puede actuar por el titular.

- **Solo el titular.** Las tres funciones toman a la persona de `plataforma.persona_actual()` (la
  sesión de la petición), nunca de un parámetro. Un caso de otra persona responde igual que uno
  que no existe (`caso_inexistente`).
- **Reautenticación reciente** (`privacidad_reautenticacion`, interna): la sesión es de la
  persona, no está revocada ni vencida, y además
  - con contraseña, la contraseña se volvió a escribir en el mismo pedido (la compara la API con
    su hash Argon2id; la base solo recibe si coincidió);
  - sin contraseña, la sesión empezó hace menos de 10 minutos: entró recién con el enlace de
    `/entrar`, que prueba el control del correo;
  - con TOTP dado de alta, la sesión lo verificó hace menos de 10 minutos (TB-CEM-3).
  Los 10 minutos son `VIGENCIA_SEGUNDO_FACTOR_RECIENTE` de `app/identidad/sesiones.py`; una
  prueba compara los dos valores.
- **`privacidad_confirmar_en_app`:** el código del caso, con las mismas reglas que
  `privacidad_confirmar` (`0039`: vencimiento, uso, invitado que ya tiene cuenta, correo
  confiable o corroboración), más la reautenticación. Sirve para cualquier derecho y es el camino
  de quien tiene contraseña o TOTP, que por correo se rechaza (`requiere_aplicacion`).
- **`privacidad_entregar_copia`:** con el caso de acceso confirmado y la reautenticación, arma la
  copia (§5) y cierra el caso como resuelto en la misma transacción. La resolución dice cuándo y
  por dónde se entregó y qué partes llevaba, nunca los datos. Una sola entrega por caso: otra
  copia es otro caso.
- **`privacidad_mis_casos`:** los casos del titular, sin el código, la corroboración ni la
  referencia del pedido (texto de soporte).
- **Qué lleva la copia** (§5, columna por columna): con alcance `cuenta`, la persona, las
  sesiones que la base conserva (se purgan a los 90 días, `0005`), si tiene contraseña y TOTP (sí
  o no, sin hashes ni secretos) y sus casos; con un consorcio, sus vínculos, mandatos,
  invitaciones a su correo, eventos como actor o como entidad, los documentos y versiones que
  cargó y si su identidad humana está verificada. **No lleva** datos de otras personas: de un
  evento sale quién actuó solo como «titular», «otra persona» o el proceso (`soporte`,
  `trabajos`, `planificador`), sin `antes`, `despues` ni `motivo` (pueden nombrar a terceros), y la
  IP solo si actuó el titular; tampoco quién invitó, confirmó o verificó, el motivo de la baja de
  un mandato, la huella, el operador ni los contactos de emergencia que cargó (PA30).
- **No escribe en `auditoria.eventos`:** la constancia queda en el caso, como en `0039`.

`cem_app` recibe `EXECUTE` sobre las tres funciones y nada sobre la tabla: sigue sin poder leerla
ni escribirla directamente.

Revisión: 0042
Anterior: 0041
"""

from alembic import op

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None

FUNCIONES_APP = (
    "plataforma.privacidad_mis_casos()",
    "plataforma.privacidad_confirmar_en_app(uuid, bytea, uuid, boolean)",
    "plataforma.privacidad_entregar_copia(uuid, uuid, boolean)",
)

UPGRADE = """
-- La reautenticación reciente del titular. Devuelve el motivo del rechazo, o nulo si alcanza.
-- `contrasena_ok` lo decide la API comparando el hash; sin contraseña no se mira. Interna.
create function plataforma.privacidad_reautenticacion(p uuid, s uuid, contrasena_ok boolean)
  returns text
  language plpgsql stable
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    x nucleo.sesion;
  begin
    select * into x from nucleo.sesion
     where id = s and persona_id = p and revocada_en is null and vence_en > now();
    if not found then
      return 'sesion_invalida';
    end if;
    if exists (select 1 from nucleo.contrasena c where c.persona_id = p) then
      if contrasena_ok is not true then
        return 'requiere_contrasena';
      end if;
    elsif x.creada_en <= now() - interval '10 minutes' then
      return 'requiere_sesion_reciente';
    end if;
    if nucleo.tiene_segundo_factor(p)
       and (x.segundo_factor_en is null
            or x.segundo_factor_en <= now() - interval '10 minutes') then
      return 'requiere_segundo_factor';
    end if;
    return null;
  end
  $$;
revoke all on function plataforma.privacidad_reautenticacion(uuid, uuid, boolean) from public;

-- El caso del titular de la sesión, bloqueado. Uno de otra persona no existe para él. Interna.
create function plataforma.privacidad_caso_propio(caso_pedido uuid)
  returns plataforma.caso_privacidad
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    p uuid := plataforma.persona_actual();
    k plataforma.caso_privacidad;
  begin
    if p is null then
      raise exception 'sin persona en la sesión' using errcode = '42501';
    end if;
    select * into k from plataforma.caso_privacidad
     where id = caso_pedido and persona_id = p for update;
    if not found then
      raise exception 'el caso no existe' using errcode = 'P0001', hint = 'caso_inexistente';
    end if;
    return k;
  end
  $$;
revoke all on function plataforma.privacidad_caso_propio(uuid) from public;

create function plataforma.privacidad_mis_casos()
  returns table (id uuid, alcance_consorcio_id uuid, derecho text, estado text,
                 abierto_en timestamptz, codigo_vence_en timestamptz,
                 confirmado_en timestamptz, resuelto_en timestamptz)
  language plpgsql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    p uuid := plataforma.persona_actual();
  begin
    if p is null then
      raise exception 'sin persona en la sesión' using errcode = '42501';
    end if;
    return query
      select k.id, k.alcance_consorcio_id, k.derecho, k.estado, k.abierto_en,
             case when k.codigo_usado_en is null then k.codigo_vence_en end,
             k.confirmado_en, k.resuelto_en
        from plataforma.caso_privacidad k
       where k.persona_id = p
       order by k.abierto_en desc, k.id desc;
  end
  $$;

create function plataforma.privacidad_confirmar_en_app(caso_pedido uuid, hash bytea, sesion uuid,
                                                       contrasena_ok boolean)
  returns void
  language plpgsql security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    k plataforma.caso_privacidad := plataforma.privacidad_caso_propio(caso_pedido);
    motivo text;
  begin
    if k.estado <> 'abierto' then
      raise exception 'el caso no está abierto' using errcode = 'P0001', hint = 'caso_no_abierto';
    end if;
    motivo := plataforma.privacidad_reautenticacion(k.persona_id, sesion, contrasena_ok);
    if motivo is not null then
      raise exception 'falta la reautenticación reciente' using errcode = 'P0001', hint = motivo;
    end if;
    if k.codigo_hash is null then
      raise exception 'el caso no tiene código: falta enviarlo'
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
    if k.corroboracion is null
       and not plataforma.privacidad_correo_confiable(k.persona_id, k.alcance_consorcio_id) then
      raise exception 'el correo no es confiable: falta la corroboración'
        using errcode = 'P0001', hint = 'requiere_corroboracion';
    end if;
    update plataforma.caso_privacidad
       set codigo_usado_en = now(), confirmado_en = now(),
           confirmacion = 'En la aplicación: sesión del titular, código del caso y'
                          ' reautenticación reciente',
           estado = 'confirmado'
     where id = caso_pedido;
    perform plataforma.privacidad_borrar_correo(k.codigo_evento_id);
  end
  $$;

-- La copia de los datos propios (§5), columna por columna. `c` nulo es el alcance `cuenta`.
-- Interna: solo la llama `privacidad_entregar_copia`.
create function plataforma.privacidad_copia(p uuid, c uuid)
  returns jsonb
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select case when c is null then
      jsonb_build_object(
        'alcance', 'cuenta',
        'persona', (select jsonb_build_object('correo', x.correo, 'nombre', x.nombre,
                                              'creado_en', x.creado_en)
                      from nucleo.persona x where x.id = p),
        'sesiones', coalesce((
          select jsonb_agg(jsonb_build_object(
                   'creada_en', s.creada_en, 'ultimo_uso_en', s.ultimo_uso_en,
                   'vence_en', s.vence_en, 'revocada_en', s.revocada_en, 'ip', s.ip,
                   'agente', s.agente, 'dispositivo', s.dispositivo)
                 order by s.creada_en)
            from nucleo.sesion s where s.persona_id = p), '[]'::jsonb),
        'credenciales', jsonb_build_object(
          'contrasena', exists (select 1 from nucleo.contrasena x where x.persona_id = p),
          'contrasena_cambiada_en',
            (select x.cambiada_en from nucleo.contrasena x where x.persona_id = p),
          'segundo_factor', nucleo.tiene_segundo_factor(p),
          'segundo_factor_desde',
            (select x.confirmado_en from nucleo.factor_totp x where x.persona_id = p)),
        'casos_privacidad', coalesce((
          select jsonb_agg(jsonb_build_object(
                   'id', k.id, 'alcance_consorcio_id', k.alcance_consorcio_id,
                   'derecho', k.derecho, 'estado', k.estado, 'abierto_en', k.abierto_en,
                   'confirmado_en', k.confirmado_en, 'resuelto_en', k.resuelto_en)
                 order by k.abierto_en)
            from plataforma.caso_privacidad k where k.persona_id = p), '[]'::jsonb))
    else
      jsonb_build_object(
        'alcance', 'consorcio',
        'consorcio_id', c,
        'vinculos', coalesce((
          select jsonb_agg(jsonb_build_object(
                   'unidad', u.designacion, 'tipo', v.tipo, 'vigente_desde', v.vigente_desde,
                   'vigente_hasta', v.vigente_hasta, 'nombre_declarado', v.nombre_declarado,
                   'fuente', v.fuente, 'confirmado_en', v.confirmado_en,
                   'creado_en', v.creado_en)
                 order by v.vigente_desde, v.id)
            from nucleo.vinculo v
            join nucleo.unidad u on u.consorcio_id = v.consorcio_id and u.id = v.unidad_id
           where v.consorcio_id = c and v.persona_id = p), '[]'::jsonb),
        'mandatos', coalesce((
          select jsonb_agg(jsonb_build_object(
                   'tipo', m.tipo, 'vigente_desde', m.vigente_desde,
                   'vigente_hasta', m.vigente_hasta, 'matricula', m.matricula,
                   'creado_en', m.creado_en, 'finalizado_en', m.finalizado_en,
                   'causa_baja', m.causa_baja)
                 order by m.vigente_desde, m.id)
            from nucleo.mandato m where m.consorcio_id = c and m.persona_id = p), '[]'::jsonb),
        'invitaciones', coalesce((
          select jsonb_agg(jsonb_build_object(
                   'unidad', u.designacion, 'tipo', i.tipo, 'correo', i.correo,
                   'nombre', i.nombre, 'creada_en', i.creada_en, 'vence_en', i.vence_en,
                   'aceptada_en', i.aceptada_en, 'revocada_en', i.revocada_en)
                 order by i.creada_en, i.id)
            from nucleo.invitacion i
            join nucleo.unidad u on u.consorcio_id = i.consorcio_id and u.id = i.unidad_id
           where i.consorcio_id = c
             and (i.persona_id = p
                  or i.correo = (select x.correo from nucleo.persona x where x.id = p))),
          '[]'::jsonb),
        'eventos', coalesce((
          select jsonb_agg(jsonb_build_object(
                   'creado_en', e.creado_en, 'accion', e.accion, 'entidad', e.entidad,
                   'papel', case when e.actor_persona_id = p then 'actor' else 'entidad' end,
                   'quien', case when e.actor_persona_id = p then 'titular'
                                 when e.actor_persona_id is null then e.origen
                                 else 'otra persona' end,
                   'ip', case when e.actor_persona_id = p then e.ip end)
                 order by e.secuencia)
            from auditoria.eventos e
           where e.consorcio_id = c and (e.actor_persona_id = p or e.entidad_id = p)),
          '[]'::jsonb),
        'documentos', coalesce((
          select jsonb_agg(jsonb_build_object(
                   'titulo', d.titulo, 'categoria', d.categoria, 'fecha', d.fecha,
                   'estado', d.estado, 'cargado_en', d.cargado_en, 'borrado_en', d.borrado_en)
                 order by d.cargado_en, d.id)
            from nucleo.documento d where d.consorcio_id = c and d.cargado_por = p),
          '[]'::jsonb),
        'versiones_subidas', coalesce((
          select jsonb_agg(jsonb_build_object(
                   'documento', d.titulo, 'tipo', vd.tipo, 'tamano', vd.tamano,
                   'estado', vd.estado, 'subida_en', vd.subida_en)
                 order by vd.subida_en, vd.id)
            from nucleo.version_documento vd
            join nucleo.documento d
              on d.consorcio_id = vd.consorcio_id and d.id = vd.documento_id
           where vd.consorcio_id = c and vd.subida_por = p), '[]'::jsonb),
        'identidad_humana', (
          select jsonb_build_object('verificada', count(*) > 0,
                                    'desde', min(ip.vinculada_en))
            from nucleo.identidad_persona ip
           where ip.consorcio_id = c and ip.persona_id = p and ip.desvinculada_en is null))
    end
  $$;
revoke all on function plataforma.privacidad_copia(uuid, uuid) from public;

create function plataforma.privacidad_entregar_copia(caso_pedido uuid, sesion uuid,
                                                     contrasena_ok boolean)
  returns jsonb
  language plpgsql security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    k plataforma.caso_privacidad := plataforma.privacidad_caso_propio(caso_pedido);
    motivo text;
    copia jsonb;
  begin
    if k.derecho <> 'acceso' then
      raise exception 'el caso no es un pedido de copia'
        using errcode = 'P0001', hint = 'derecho_no_acceso';
    end if;
    if k.estado <> 'confirmado' then
      raise exception 'el caso no está confirmado o ya se resolvió'
        using errcode = 'P0001', hint = 'caso_no_confirmado';
    end if;
    motivo := plataforma.privacidad_reautenticacion(k.persona_id, sesion, contrasena_ok);
    if motivo is not null then
      raise exception 'falta la reautenticación reciente' using errcode = 'P0001', hint = motivo;
    end if;
    copia := plataforma.privacidad_copia(k.persona_id, k.alcance_consorcio_id);
    update plataforma.caso_privacidad
       set estado = 'resuelto', resuelto_en = now(),
           resolucion = jsonb_build_object(
             'entrega', 'aplicación, al titular autenticado',
             'entregada_en', now(),
             'partes', (select jsonb_agg(x order by x) from jsonb_object_keys(copia) x))
     where id = caso_pedido;
    return copia || jsonb_build_object('caso_id', k.id, 'generada_en', now());
  end
  $$;
"""

for _f in FUNCIONES_APP:
    UPGRADE += f"revoke all on function {_f} from public;\n"
    UPGRADE += f"grant execute on function {_f} to cem_app;\n"

DOWNGRADE = f"""
drop function {FUNCIONES_APP[2]};
drop function {FUNCIONES_APP[1]};
drop function {FUNCIONES_APP[0]};
drop function plataforma.privacidad_copia(uuid, uuid);
drop function plataforma.privacidad_caso_propio(uuid);
drop function plataforma.privacidad_reautenticacion(uuid, uuid, boolean);
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
