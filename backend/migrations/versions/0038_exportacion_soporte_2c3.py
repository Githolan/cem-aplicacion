"""2c-3: exportación de un consorcio por soporte, con su rol propio (RF36; H2c §1.1 y §5).

Diseño: `doc/diseno/H2c_Diseno.md` del repositorio CEM, §1.1 (soporte fuera de la auditoría
funcional, decisión del propietario del 26-10-2026) y §5 (exportación); contenido según §1.4 de
las decisiones del propietario del 25-10-2026.

- **`cem_soporte_exportacion`:** el rol lo crea un superusuario (`scripts/crear_roles.sql`, que
  incluye `crear_rol_soporte_exportacion.sql`), porque `cem_migrate` no tiene `CREATEROLE`. Sin
  `BYPASSRLS` y **sin `SELECT` directo sobre ninguna tabla del consorcio**: lee por las funciones
  `security definer` de exportación de abajo, y escribe solo la constancia,
  `plataforma.exportacion`.
- **Una función por parte del contenido** (§5): padrón (el consorcio, sus unidades y los vínculos
  vigentes y futuros con nombres), documentos (los vigentes y sus versiones `disponible`),
  decisiones, mandatos (con sus facultades y revisiones), modos, coeficientes, contactos y la
  auditoría funcional por la proyección de RF35-B. Cada una devuelve `(entidad, fila)`, con la
  fila armada **columna por columna** (nunca `select *`), y deja afuera lo que §5 lista en «Nunca
  van». Las columnas son las mismas que ya muestran las lecturas de la aplicación a quien tiene
  la facultad (`padron_del_consorcio`, `mandato_detalle`, `decision_detalle`,
  `modos_del_consorcio`, `coeficientes_del_consorcio`, `contactos_del_consorcio(false)`,
  `documentos_del_consorcio` y `eventos_del_consorcio`), sin la persona de la sesión.
- **`nucleo.exportar_objetos_documentos`:** dónde está cada binario `disponible` (clave y versión
  del objeto), para que el script lo lea con la clave de análisis. Es información de
  infraestructura: el script la usa y **no la escribe en el paquete**.
- Cada función exige que el consorcio pedido sea el del contexto (`plataforma.consorcio_actual()`,
  `42501` si no) **y una exportación de ese consorcio registrada en la misma transacción y
  todavía sin armar** (`42501` si no): así la capacidad no se puede saltear llamando a las
  funciones desde la base, y una lectura que se confirma deja su constancia. **No impide** leer
  dentro de una transacción que después se deshace: quien tiene la credencial del rol lee y la
  vuelta atrás borra la constancia. Eso es propio de cualquier credencial de base con lectura;
  la protección es la custodia del secreto, que vive solo en `cem-trabajos`, como el de
  `cem_soporte_documentos` (riesgo anotado para el propietario). Registrar las sentencias del rol
  (`log_statement`) se descartó: llevaría a los registros el destinatario y su documento.
  Tienen `search_path` fijo, `revoke execute … from public` y se conceden **solo** a
  `cem_soporte_exportacion`, nunca a `cem_app`.
- **`nucleo.registrar_exportacion(...)`:** la única forma de crear la constancia. Fija adentro
  las cinco facultades de lectura y los tipos de mandato (no los recibe de quien llama),
  comprueba la capacidad con `nucleo.capacidad_para_exportar` y recién entonces inserta la fila.
- **`nucleo.capacidad_para_exportar(consorcio, persona, fecha)`:** como
  `capacidad_del_solicitante` (`0037`), con las cinco facultades fijas y solo un mandato **de
  administración o de Consejo** (`administrador` o `miembro_consejo`; nunca
  `administrador_externo`, aunque tenga las facultades), que es lo que pide §5. Es interna: solo
  la llama `registrar_exportacion`, y nadie más la ejecuta. La de `0037` no cambia.
- **`plataforma.exportacion`:** la constancia operativa (no la auditoría funcional, §1.1): el
  consorcio, la referencia de la solicitud, el destinatario (nombre y CUIT o documento), quién la
  pidió, el mandato con el que ese día tenía la capacidad y las facultades verificadas, la fecha
  del pedido, la referencia del respaldo, y, al armarla, la fecha, el SHA-256 y el tamaño del
  paquete cifrado y la cantidad por parte; después, la fecha de entrega. RLS forzada por
  `consorcio_actual()`; `cem_app` no tiene ningún permiso. El rol tiene `SELECT` y `UPDATE` solo
  de las columnas del armado y de la entrega (sin `INSERT`: la fila la crea
  `registrar_exportacion`), y un disparador no deja cambiar el
  armado ni la entrega una vez registrados. La política no nombra rol: la copia diaria se
  verifica restaurando sin roles (`pg_restore --no-acl`).

Downgrade: se niega si hay alguna exportación registrada (es constancia); si no, quita las
funciones, la tabla y los permisos (el rol queda: lo borra un superusuario).

Revisión: 0038
Anterior: 0037
"""

from alembic import op

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None

ROL = "cem_soporte_exportacion"
COLUMNAS_ARMADO = ("armada_en", "paquete_sha256", "paquete_tamano", "cantidades")
FACULTADES = ("padron.ver", "documentos.ver", "auditoria.ver", "mandatos.ver", "decisiones.ver")
"""Las facultades de lectura del contenido, todas generales (H2c §5)."""
TIPOS = ("administrador", "miembro_consejo")
"""Los mandatos de administración o de Consejo (§5); nunca `administrador_externo`."""

EXIGIR_CONSORCIO = """\
    if c is null or c is distinct from plataforma.consorcio_actual() then
      raise exception 'el consorcio no es el del contexto' using errcode = '42501';
    end if;
"""
# La exportación tiene que ser del consorcio, estar sin armar y haberse registrado **en esta
# misma transacción**: si no, con la URL del rol se podría registrar una vez, confirmar y leer
# después en otras transacciones cuantas veces se quisiera, sin armar nunca. (Leer y deshacer la
# transacción no deja constancia: ver el docstring.) Se compara
# `creada_en = now()`: `now()` es el comienzo de la transacción, `creada_en` toma ese valor por
# omisión y nadie lo puede fijar (el rol no inserta ni actualiza esa columna, y
# `registrar_exportacion` no la recibe). Se descartó `xmin` contra el identificador de la
# transacción: un `savepoint` (subtransacción) le da a la fila otro `xmin`, y cualquier `UPDATE`
# posterior lo cambia.
EXIGIR_PEDIDO = """\
    if pedido is null or not exists (
         select 1 from plataforma.exportacion x
          where x.id = pedido and x.consorcio_id = c and x.armada_en is null
            and x.creada_en = now()) then
      raise exception 'sin una exportación registrada en esta transacción y sin armar'
        using errcode = '42501';
    end if;
"""


def _lista(valores: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in valores)


# El nombre que muestra el padrón (`0025`): el importado pendiente, con el de la planilla.
NOMBRE_EN_EL_PADRON = """\
case when v.nombre_declarado is not null and v.confirmado_en is null
          and not exists (
            select 1 from auditoria.eventos e
              join nucleo.invitacion i
                on i.id = e.entidad_id and i.consorcio_id = e.consorcio_id
             where e.consorcio_id = v.consorcio_id
               and e.accion = 'identidad.invitacion.aceptar'
               and e.entidad = 'invitacion'
               and e.despues ->> 'vinculo_id' = v.id::text
               and i.aceptada_en is not null)
     then v.nombre_declarado else pe.nombre end"""

FUNCIONES: dict[str, str] = {
    "exportar_padron": f"""
    hoy := nucleo.hoy_del_consorcio(c);
    return query
      select 'consorcio'::text,
             jsonb_build_object('id', x.id, 'nombre', x.nombre, 'cuit', x.cuit,
                                'zona_horaria', x.zona_horaria)
        from nucleo.consorcio x
       where x.id = c;
    return query
      select 'unidad'::text, jsonb_build_object('id', u.id, 'designacion', u.designacion)
        from nucleo.unidad u
       where u.consorcio_id = c and u.eliminado_en is null
       order by u.designacion, u.id;
    -- Los vínculos vigentes y futuros, como `padron_del_consorcio`: sin la persona, su correo ni
    -- la referencia de la confirmación.
    return query
      select 'vinculo'::text,
             jsonb_build_object('id', v.id, 'unidad_id', v.unidad_id,
                                'designacion', u.designacion,
                                'nombre', {NOMBRE_EN_EL_PADRON},
                                'tipo', v.tipo, 'vigente_desde', v.vigente_desde,
                                'vigente_hasta', v.vigente_hasta,
                                'confirmado', v.confirmado_en is not null)
        from nucleo.vinculo v
        join nucleo.unidad u on u.consorcio_id = v.consorcio_id and u.id = v.unidad_id
        join nucleo.persona pe on pe.id = v.persona_id
       where v.consorcio_id = c
         and (v.vigente_hasta is null or v.vigente_hasta > hoy)
         and u.eliminado_en is null and pe.eliminado_en is null
       order by u.designacion, v.tipo, v.id;
""",
    "exportar_documentos": """
    -- Los documentos vigentes y, de cada uno, solo las versiones `disponible`, como
    -- `documentos_del_consorcio` a quien tiene `documentos.ver`. Sin la clave ni la versión del
    -- objeto, el antivirus ni sus firmas.
    return query
      select 'documento'::text,
             jsonb_build_object('id', d.id, 'categoria', d.categoria, 'titulo', d.titulo,
                                'fecha', d.fecha, 'cargado_en', d.cargado_en)
        from nucleo.documento d
       where d.consorcio_id = c and d.estado = 'vigente'
       order by d.fecha, d.id;
    return query
      select 'version'::text,
             jsonb_build_object('id', v.id, 'documento_id', v.documento_id, 'tipo', v.tipo,
                                'tamano', v.tamano, 'sha256', v.sha256,
                                'subida_en', v.subida_en)
        from nucleo.version_documento v
        join nucleo.documento d on d.consorcio_id = v.consorcio_id and d.id = v.documento_id
       where v.consorcio_id = c and v.estado = 'disponible' and d.estado = 'vigente'
       order by v.documento_id, v.subida_en, v.id;
""",
    "exportar_decisiones": """
    -- Sin quién la registró (la API no lo muestra: A15, y es el identificador de una persona) ni
    -- su origen (si la cargó el equipo: §1.1).
    return query
      select 'decision'::text,
             jsonb_build_object('id', d.id, 'tipo', d.tipo, 'fecha', d.fecha,
                                'descripcion', d.descripcion,
                                'referencia_acta', d.referencia_acta,
                                'sha256_acta', d.sha256_acta)
        from nucleo.decision_formal d
       where d.consorcio_id = c
       order by d.fecha, d.id;
""",
    "exportar_mandatos": """
    hoy := nucleo.hoy_del_consorcio(c);
    -- Como `mandato_detalle`: sin el identificador de la persona del mandato, su correo ni las
    -- invitaciones; el CUIT, solo del administrador externo. Sin el origen del mandato, de la
    -- revisión ni la decisión y la fecha de cada facultad, que la API no muestra y dejarían ver
    -- las intervenciones del equipo (§1.1). El recurso de cada facultad sí: sin él, una facultad
    -- acotada se leería como general.
    return query
      select 'mandato'::text,
             jsonb_build_object(
               'id', m.id, 'tipo', m.tipo,
               'nombre', case when m.tipo = 'administrador_externo'
                              then coalesce(m.mandatario_nombre, pe.nombre)
                              else coalesce(pe.nombre, m.mandatario_nombre) end,
               'cuit', case when m.tipo = 'administrador_externo' then m.mandatario_cuit end,
               'vigente_desde', m.vigente_desde, 'vigente_hasta', m.vigente_hasta,
               -- Como `mandato_detalle` desde 0023: un Consejo sin su condición habilitante
               -- queda `pendiente_revision`, no `vigente`.
               'estado', nucleo.estado_operativo(
                           m.id,
                           nucleo.estado_mandato(m.causa_baja, m.vigente_desde, m.vigente_hasta,
                                                 hoy),
                           hoy),
               'con_cuenta', m.persona_id is not null, 'matricula', m.matricula,
               'decision_id', m.decision_id, 'causa_baja', m.causa_baja,
               'baja_decision_id', m.baja_decision_id,
               'instrumento_referencia', m.instrumento_referencia,
               'motivo_fin', m.motivo_fin, 'finalizado_en', m.finalizado_en)
        from nucleo.mandato m
        left join nucleo.persona pe on pe.id = m.persona_id
       where m.consorcio_id = c
       order by m.vigente_desde, m.id;
    return query
      select 'facultad'::text,
             jsonb_build_object('mandato_id', f.mandato_id, 'facultad', f.facultad,
                                'recurso', f.recurso)
        from nucleo.facultad_otorgada f
       where f.consorcio_id = c
       order by f.mandato_id, f.facultad, f.recurso;
    return query
      select 'revision'::text,
             jsonb_build_object('id', r.id, 'mandato_id', r.mandato_id,
                                'decision_id', r.decision_id, 'eficaz_desde', r.eficaz_desde)
        from nucleo.revision_mandato r
       where r.consorcio_id = c
       order by r.eficaz_desde, r.id;
""",
    "exportar_modos": """
    return query
      select 'modo'::text,
             jsonb_build_object('modo', m.modo, 'vigente_desde', m.vigente_desde,
                                'vigente_hasta', m.vigente_hasta, 'decision_id', m.decision_id)
        from nucleo.modo_operacion m
       where m.consorcio_id = c
       order by m.vigente_desde, m.id;
""",
    "exportar_coeficientes": """
    -- Como `coeficientes_del_consorcio` con la lectura amplia (`padron.ver`).
    return query
      with e as (
        select x.id, x.consorcio_id, x.grupo, x.version, x.vigente_desde,
               lead(x.vigente_desde) over (partition by x.grupo order by x.vigente_desde)
                 as vigente_hasta
          from nucleo.esquema_coeficientes x
         where x.consorcio_id = c)
      select 'coeficiente'::text,
             jsonb_build_object('esquema_id', e.id, 'grupo', e.grupo, 'version', e.version,
                                'vigente_desde', e.vigente_desde,
                                'vigente_hasta', e.vigente_hasta, 'unidad_id', cj.unidad_id,
                                'designacion', u.designacion,
                                'porcentaje', cj.porcentaje::text)
        from e
        left join nucleo.coeficiente_juridico cj
          on cj.consorcio_id = e.consorcio_id and cj.esquema_id = e.id
        left join nucleo.unidad u on u.consorcio_id = cj.consorcio_id and u.id = cj.unidad_id
       order by e.grupo, e.version, u.designacion, cj.unidad_id;
""",
    "exportar_contactos": """
    hoy := nucleo.hoy_del_consorcio(c);
    -- Como `contactos_del_consorcio(false)`: los vigentes, sin la referencia del consentimiento,
    -- la observación ni quién los cargó, verificó u observó.
    return query
      select 'contacto'::text,
             jsonb_build_object('id', k.id, 'funcion', k.funcion, 'naturaleza', k.naturaleza,
                                'nombre', k.nombre, 'telefono', k.telefono,
                                'indicaciones', k.indicaciones,
                                'vigente_desde', k.vigente_desde,
                                'vigente_hasta', k.vigente_hasta,
                                'actualizado_en', k.actualizado_en,
                                'verificado_en', k.verificado_en,
                                'observado', k.observacion is not null)
        from nucleo.contacto_emergencia k
       where k.consorcio_id = c
         and (k.vigente_hasta is null or (k.vigente_hasta > hoy
                                          and k.vigente_hasta > k.vigente_desde))
         and k.vigente_desde <= hoy
       order by k.funcion, k.nombre, k.id;
""",
    "exportar_auditoria": """
    hoy := nucleo.hoy_del_consorcio(c);
    -- La proyección de RF35-B (`eventos_del_consorcio`), entera y sin la persona de la sesión:
    -- solo las acciones de `auditoria.proyeccion` (nunca `autorizacion.denegada`), nada de
    -- soporte, sin IP, sin hashes de la cadena, sin el identificador del actor y solo los campos
    -- funcionales de cada acción. El nombre del actor, con el criterio de RF35-B para quien
    -- tiene `padron.ver`.
    return query
      select 'evento'::text,
             jsonb_build_object(
               'fecha', g.creado_en, 'accion', p.texto, 'recurso', p.recurso,
               'origen', case g.origen when 'api' then 'aplicacion' else 'sistema' end,
               'actor', case when g.origen <> 'api' then 'sistema' else 'persona' end,
               'actor_nombre',
               case when g.origen = 'api'
                    then (select pe.nombre from nucleo.persona pe
                           where pe.id = g.actor_persona_id and pe.eliminado_en is null
                             and exists (
                               select 1 from nucleo.vinculo v
                                 join nucleo.unidad u
                                   on u.consorcio_id = v.consorcio_id and u.id = v.unidad_id
                                where v.consorcio_id = c and v.persona_id = pe.id
                                  and (v.vigente_hasta is null or v.vigente_hasta > hoy)
                                  and u.eliminado_en is null
                                  -- El criterio de 0025 para mostrar el nombre de la persona.
                                  and not (v.nombre_declarado is not null
                                           and v.confirmado_en is null
                                           and not exists (
                                             select 1 from auditoria.eventos ea
                                               join nucleo.invitacion i
                                                 on i.id = ea.entidad_id
                                                and i.consorcio_id = ea.consorcio_id
                                              where ea.consorcio_id = v.consorcio_id
                                                and ea.accion = 'identidad.invitacion.aceptar'
                                                and ea.entidad = 'invitacion'
                                                and ea.despues ->> 'vinculo_id' = v.id::text
                                                and i.aceptada_en is not null))))
               end,
               'cambios', auditoria.cambios_funcionales(g.antes, g.despues, p.campos))
        from auditoria.eventos g
        join auditoria.proyeccion p on p.accion = g.accion
       where g.consorcio_id = c and g.origen <> 'soporte'
       order by g.creado_en, g.secuencia;
""",
}


def _funcion(nombre: str, cuerpo: str) -> str:
    return f"""
create function nucleo.{nombre}(c uuid, pedido uuid)
  returns table (entidad text, fila jsonb)
  language plpgsql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    hoy date;
  begin
{EXIGIR_CONSORCIO}{EXIGIR_PEDIDO}{cuerpo}  end
  $$;
revoke all on function nucleo.{nombre}(uuid, uuid) from public;
grant execute on function nucleo.{nombre}(uuid, uuid) to {ROL};
"""


UPGRADE = f"""
create table plataforma.exportacion (
  consorcio_id            uuid not null references nucleo.consorcio (id),
  id                      uuid primary key default plataforma.uuid_v7(),
  solicitud_referencia    text not null
    check (length(btrim(solicitud_referencia)) between 1 and 300),
  destinatario_nombre     text not null
    check (length(btrim(destinatario_nombre)) between 1 and 200),
  destinatario_documento  text not null
    check (length(btrim(destinatario_documento)) between 1 and 40),
  solicitante             uuid not null references nucleo.persona (id),
  mandato_id              uuid not null,
  facultades_verificadas  text[] not null check (cardinality(facultades_verificadas) > 0),
  fecha_pedido            date not null,
  respaldo_referencia     text not null
    check (length(btrim(respaldo_referencia)) between 1 and 300),
  creada_en               timestamptz not null default now(),
  armada_en               timestamptz,
  paquete_sha256          text check (paquete_sha256 ~ '^[0-9a-f]{{64}}$'),
  paquete_tamano          bigint check (paquete_tamano > 0),
  cantidades              jsonb check (jsonb_typeof(cantidades) = 'object'),
  entregada_en            timestamptz,
  foreign key (consorcio_id, mandato_id) references nucleo.mandato (consorcio_id, id),
  check ((armada_en is null) = (paquete_sha256 is null)
         and (armada_en is null) = (paquete_tamano is null)
         and (armada_en is null) = (cantidades is null)),
  check (entregada_en is null or (armada_en is not null and entregada_en >= armada_en))
);
alter table plataforma.exportacion enable row level security;
alter table plataforma.exportacion force row level security;
-- Sin `to {ROL}`: una política que nombra un rol rompe la restauración de la copia diaria en el
-- PostgreSQL efímero (`pg_restore --no-acl` no la omite y el rol no existe ahí). `cem_app` no
-- tiene ningún permiso sobre la tabla, y `cem_migrate` tiene BYPASSRLS.
create policy por_consorcio on plataforma.exportacion
  using (consorcio_id = plataforma.consorcio_actual())
  with check (consorcio_id = plataforma.consorcio_actual());

-- El armado y la entrega se registran una vez: después no cambian.
create function plataforma.exportacion_constancia_fija()
  returns trigger
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  begin
    if old.armada_en is not null
       and (new.armada_en, new.paquete_sha256, new.paquete_tamano, new.cantidades)
           is distinct from (old.armada_en, old.paquete_sha256, old.paquete_tamano,
                             old.cantidades) then
      raise exception 'el armado de la exportación ya está registrado'
        using errcode = 'P0001', hint = 'armado_registrado';
    end if;
    if old.entregada_en is not null and new.entregada_en is distinct from old.entregada_en then
      raise exception 'la entrega de la exportación ya está registrada'
        using errcode = 'P0001', hint = 'entrega_registrada';
    end if;
    return new;
  end
  $$;
create trigger exportacion_constancia_fija before update on plataforma.exportacion
  for each row execute function plataforma.exportacion_constancia_fija();

grant usage on schema nucleo, plataforma to {ROL};
grant execute on function plataforma.consorcio_actual() to {ROL};
grant execute on function plataforma.uuid_v7() to {ROL};
grant select on plataforma.exportacion to {ROL};
grant update ({", ".join(COLUMNAS_ARMADO)}, entregada_en) on plataforma.exportacion to {ROL};

-- §5: «un mandato vigente de administración o de Consejo» con las cinco facultades de lectura,
-- cada una general. La misma consulta que `capacidad_del_solicitante` (0037), con las facultades
-- y los tipos fijos. Interna: solo la llama `registrar_exportacion` (como su dueño).
create function nucleo.capacidad_para_exportar(c uuid, persona uuid, fecha date)
  returns uuid
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select x.id from nucleo.mandato x
     where x.consorcio_id = c and x.persona_id = persona
       and x.tipo in ({_lista(TIPOS)})
       and nucleo.mandato_vigente(x.id, fecha)
       and (select count(distinct f.facultad) from nucleo.facultad_otorgada f
             where f.consorcio_id = c and f.mandato_id = x.id and f.recurso is null
               and f.facultad in ({_lista(FACULTADES)})) = {len(FACULTADES)}
     order by x.id
     limit 1
  $$;
revoke all on function nucleo.capacidad_para_exportar(uuid, uuid, date) from public;

-- La única forma de crear una constancia: comprueba la capacidad con las facultades y los tipos
-- fijos y recién entonces inserta. El rol no tiene `INSERT` sobre la tabla.
create function nucleo.registrar_exportacion(c uuid, solicitud text, destinatario_nombre text,
                                             destinatario_documento text, solicitante uuid,
                                             fecha date, respaldo text)
  returns table (id uuid, mandato_id uuid, creada_en timestamptz)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    m uuid;
  begin
{EXIGIR_CONSORCIO}    if solicitante is null or fecha is null then
      raise exception 'faltan quien la pidió o la fecha del pedido'
        using errcode = 'P0001', hint = 'pedido_incompleto';
    end if;
    if fecha > nucleo.hoy_del_consorcio(c) then
      raise exception 'la fecha del pedido es posterior a hoy en el consorcio'
        using errcode = 'P0001', hint = 'fecha_futura';
    end if;
    m := nucleo.capacidad_para_exportar(c, solicitante, fecha);
    if m is null then
      raise exception 'quien la pidió no tenía ese día un mandato vigente de administración o de'
                      ' Consejo con las cinco facultades de lectura generales'
        using errcode = 'P0001', hint = 'sin_capacidad';
    end if;
    return query
      insert into plataforma.exportacion as x (consorcio_id, solicitud_referencia,
                                               destinatario_nombre, destinatario_documento,
                                               solicitante, mandato_id, facultades_verificadas,
                                               fecha_pedido, respaldo_referencia)
      values (c, btrim(solicitud), btrim(destinatario_nombre), btrim(destinatario_documento),
              solicitante, m, array[{_lista(FACULTADES)}], fecha, btrim(respaldo))
      returning x.id, x.mandato_id, x.creada_en;
  end
  $$;
revoke all on function nucleo.registrar_exportacion(uuid, text, text, text, uuid, date, text)
  from public;
grant execute on function nucleo.registrar_exportacion(uuid, text, text, text, uuid, date, text)
  to {ROL};
{"".join(_funcion(n, c) for n, c in FUNCIONES.items())}
-- Dónde leer cada binario `disponible`: solo para el script, que no lo escribe en el paquete.
create function nucleo.exportar_objetos_documentos(c uuid, pedido uuid)
  returns table (version_id uuid, documento_id uuid, tipo text, tamano integer, sha256 text,
                 clave_objeto text, version_objeto text)
  language plpgsql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
  begin
{EXIGIR_CONSORCIO}{EXIGIR_PEDIDO}    return query
      select v.id, v.documento_id, v.tipo, v.tamano, v.sha256, v.clave_objeto, v.version_objeto
        from nucleo.version_documento v
        join nucleo.documento d on d.consorcio_id = v.consorcio_id and d.id = v.documento_id
       where v.consorcio_id = c and v.estado = 'disponible' and d.estado = 'vigente'
       order by v.documento_id, v.subida_en, v.id;
  end
  $$;
revoke all on function nucleo.exportar_objetos_documentos(uuid, uuid) from public;
grant execute on function nucleo.exportar_objetos_documentos(uuid, uuid) to {ROL};
"""

DOWNGRADE = f"""
do $guarda$
begin
  if exists (select 1 from plataforma.exportacion) then
    raise exception 'el downgrade de 0038 se niega: hay exportaciones registradas'
      using errcode = 'P0001', hint = 'downgrade_con_datos',
            detail = 'plataforma.exportacion es la constancia de cada exportación';
  end if;
end
$guarda$;
drop function nucleo.exportar_objetos_documentos(uuid, uuid);
{"".join(f"drop function nucleo.{n}(uuid, uuid);{chr(10)}" for n in FUNCIONES)}
drop function nucleo.registrar_exportacion(uuid, text, text, text, uuid, date, text);
drop function nucleo.capacidad_para_exportar(uuid, uuid, date);
drop table plataforma.exportacion;
drop function plataforma.exportacion_constancia_fija();
revoke execute on function plataforma.uuid_v7() from {ROL};
revoke execute on function plataforma.consorcio_actual() from {ROL};
revoke usage on schema nucleo, plataforma from {ROL};
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
