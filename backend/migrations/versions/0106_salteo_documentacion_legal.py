"""RF50: en una urgencia, el Consejo saltea el bloqueo por documentación legal, con un motivo
(respuesta del propietario del 16-11-2026 a la pregunta abierta 3 del diseño, una urgencia como una
pérdida de gas: «si con motivo»). Diseño: `doc/diseno/RF50_Configurable.md` del repositorio CEM,
sección «Salteo con motivo».

Con el modo `bloquear` (`0104`), una orden nueva a un proveedor al que le falta documentación que
exige la ley no se prepara (`exigir_documentacion_legal`, `documentacion_legal_faltante`). La
barrera impide que esa orden llegue a existir, así que el salteo no se le puede dar a una orden ya
creada: se registra **antes**, para un proveedor, y lo **consume la próxima versión** de una orden
que lo necesita (la primera de una orden, o la que cambia de proveedor). La orden y la versión
quedan escritas junto al salteo: vale para esa orden y no para el proveedor en general.

**El salteo** (`nucleo.salteo_documentacion_proveedor`). Una fila con el proveedor, el motivo
(obligatorio), quién, su identidad humana, cuándo y hasta cuándo sirve (`horas_salteo`). No se
modifica ni se borra. Solo se registra si el consorcio está en `bloquear` y al proveedor le falta
algo, y si la misma persona no tiene ya otro salteo de ese proveedor esperando su orden. Otro
miembro del Consejo sí puede registrar el suyo: si quien prepara registró uno, la orden puede usar
el de otra persona.

**El uso** (`nucleo.salteo_documentacion_uso`). La barrera escribe la orden y la versión que
consumieron el salteo. Un salteo se usa una sola vez.

**Separación de funciones.** Quien prepara la orden no saltea su propio bloqueo: la barrera no
toma un salteo registrado por la misma persona ni por la misma identidad humana (otra cuenta del
mismo ser humano) que prepara la versión. Si solo encuentra uno así, se niega con
`salteo_de_quien_prepara`.

**Quién saltea** (`egresos.saltear_documentacion`). Lo concede la configuración del consorcio,
como `proveedores.autorizar` (`0105`): un mandato vigente de un tipo que está en
`roles_que_saltean`. Por defecto, `miembro_consejo` (`ROLES_QUE_SALTEAN`). Es un acto económico
(deja pagar a quien no cumple): pide la sesión operativa y la identidad humana, sin el gesto.

**Dos parámetros nuevos** en `nucleo.configuracion_proveedores`, para la futura aplicación de
mantenimiento:

- `roles_que_saltean` (tipos de mandato): quién saltea. Vacío: nadie;
- `horas_salteo` (`HORAS_SALTEO`, 24; de 1 a 168): cuánto espera un salteo a su orden. Lo propuso
  la sesión que construyó esto, para que un salteo de urgencia no quede abierto para siempre; el
  propietario lo puede cambiar.

Los cambia el equipo con `configurar_salteo_documentacion` (origen `soporte`); el script
`scripts/configurar_proveedores.py` los acepta. Las otras dos funciones de configuración conservan
los dos valores al cambiar los suyos.

**Lectura:** `salteos_documentacion_lectura` da los salteos de un proveedor o de una orden, con su
estado (`vigente`, `usado` o `vencido`), para la ficha del proveedor y la de la orden.

**Exportación (`0069`):** `exportar_proveedores` suma los salteos y sus usos, y los dos parámetros
en `configuracion_proveedores`.

Downgrade: se niega (`downgrade_con_datos`) si hay algún salteo o alguna configuración con valores
distintos de los de por defecto en los parámetros nuevos.

Revisión: 0106
Anterior: 0105
"""

from alembic import op
from sqlalchemy import text

revision = "0106"
down_revision = "0105"
branch_labels = None
depends_on = None

ROLES_QUE_SALTEAN = ("miembro_consejo",)
HORAS_SALTEO = 24
HORAS_MAXIMAS = 168
ROLES_DE_MANDATO = ("administrador", "administrador_externo", "miembro_consejo")
"""Solo un mandato saltea: es un acto del Consejo (o de quien la configuración diga)."""
ACCION_SALTEAR = "egresos.saltear_documentacion"
ACCION_AUDITADA = "consorcio.proveedor.salteo_documentacion"
MOTIVO_MAXIMO = 500

_lista = ", ".join
_roles_mandato = _lista(f"'{r}'" for r in ROLES_DE_MANDATO)
_roles_defecto = _lista(f"'{r}'" for r in ROLES_QUE_SALTEAN)

TABLA_CONFIG = "nucleo.configuracion_proveedores"
TABLA = "nucleo.salteo_documentacion_proveedor"
TABLA_USO = "nucleo.salteo_documentacion_uso"

CONFIG_VIGENTE = "nucleo.configuracion_proveedores_vigente(uuid)"
CONFIG_SOPORTE = "nucleo.configuracion_proveedores_soporte()"
CONFIGURAR = (
    "nucleo.configurar_proveedores(integer, text, boolean, text[], text[], text[], uuid, text)"
)
CONFIGURAR_AUTORIZACION = "nucleo.configurar_autorizacion_proveedores(text[], boolean, uuid, text)"
ROLES_CONFIGURADOS = "nucleo.roles_configurados(uuid)"
BARRERA = "nucleo.exigir_documentacion_legal()"
EXPORTAR_PROVEEDORES = "nucleo.exportar_proveedores(uuid, uuid)"

PUEDE_SALTEAR = "nucleo.puede_saltear_documentacion(uuid, uuid)"
INTERNAS = (PUEDE_SALTEAR,)

SALTEAR = "nucleo.saltear_documentacion_legal(uuid, text, uuid, text, uuid)"
LECTURA = "nucleo.salteos_documentacion_lectura(uuid, uuid, text, uuid, text, uuid)"
CONFIGURAR_SALTEO = "nucleo.configurar_salteo_documentacion(text[], integer, uuid, text)"
RUTAS = (SALTEAR, LECTURA, CONFIGURAR_SALTEO)

TABLAS = f"""
alter table {TABLA_CONFIG}
  add column roles_que_saltean text[] not null default array[{_roles_defecto}]::text[]
    check (roles_que_saltean <@ array[{_roles_mandato}]::text[]),
  add column horas_salteo smallint not null default {HORAS_SALTEO}
    check (horas_salteo between 1 and {HORAS_MAXIMAS});
-- Las filas que ya estaban toman los valores por defecto; las nuevas los dicen siempre.
alter table {TABLA_CONFIG}
  alter column roles_que_saltean drop default,
  alter column horas_salteo drop default;

create table {TABLA} (
  consorcio_id    uuid not null references nucleo.consorcio (id),
  numero          bigint generated always as identity primary key,
  proveedor_id    uuid not null,
  motivo          text not null
    check (length(btrim(motivo)) between 1 and {MOTIVO_MAXIMO} and motivo = btrim(motivo)),
  registrado_por  uuid not null references nucleo.persona (id),
  identidad       uuid not null,
  registrado_en   timestamptz not null default now(),
  vence_en        timestamptz not null,
  check (vence_en > registrado_en),
  foreign key (consorcio_id, proveedor_id) references nucleo.proveedor (consorcio_id, id),
  foreign key (consorcio_id, identidad) references nucleo.identidad_humana (consorcio_id, id)
);
create index salteo_documentacion_proveedor_proveedor
  on {TABLA} (consorcio_id, proveedor_id, numero);

-- La referencia es a la orden, que ya existe cuando el disparador escribe el uso: la versión se
-- escribe después, en la misma sentencia, y si falla se deshace todo. Una referencia diferida a la
-- versión dejaría eventos pendientes que traban los `alter table` de la misma transacción.
create table {TABLA_USO} (
  consorcio_id    uuid not null references nucleo.consorcio (id),
  salteo_numero   bigint primary key references {TABLA} (numero),
  orden_id        uuid not null,
  version         integer not null,
  usado_en        timestamptz not null default now(),
  unique (orden_id, version),
  foreign key (consorcio_id, orden_id) references nucleo.orden_pago (consorcio_id, id)
);
"""

for _t, _n in ((TABLA, "salteo_documentacion_proveedor"), (TABLA_USO, "salteo_documentacion_uso")):
    TABLAS += f"""
alter table {_t} enable row level security;
alter table {_t} force row level security;
create policy por_consorcio on {_t} for select
  using (consorcio_id = plataforma.consorcio_actual());
revoke all on {_t} from public, cem_app;
create trigger historia_{_n} before update or delete on {_t}
  for each row execute function nucleo.negar_cambio_historia_orden_pago();
create trigger historia_{_n}_truncate before truncate on {_t}
  for each statement execute function nucleo.negar_cambio_historia_orden_pago();
"""

FUNCIONES = f"""
-- Un mandato vigente de un tipo que la configuración lista en `roles_que_saltean`.
create function {PUEDE_SALTEAR}
  returns boolean
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select exists (
             select 1 from nucleo.mandato m
              where m.consorcio_id = $1 and m.persona_id = $2
                and nucleo.mandato_vigente(m.id, nucleo.hoy_del_consorcio($1))
                and m.tipo::text = any (k.roles_que_saltean))
      from nucleo.configuracion_proveedores_vigente($1) k
  $$;
"""

RUTAS_SQL = f"""
-- Quien la configuración diga (por defecto, el Consejo) saltea el bloqueo por documentación legal
-- de un proveedor, con el motivo. Sirve para la próxima orden a ese proveedor que prepare otra
-- persona, durante `horas_salteo`. Devuelve el salteo como lo da la lectura.
create function nucleo.saltear_documentacion_legal(proveedor_salteado uuid, motivo_salteo text,
                                                   actor uuid, origen_pedido text, sesion uuid)
  returns table (numero bigint, proveedor_id uuid, motivo text, registrado_en timestamptz,
                 registrado_por_nombre text, vence_en timestamptz, estado text, orden_id uuid,
                 version integer, usado_en timestamptz)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  #variable_conflict use_column
  declare
    c uuid := plataforma.consorcio_actual();
    k record;
    identidad_actor uuid;
    numero_nuevo bigint;
    vence timestamptz;
  begin
    if c is null then
      raise exception 'sin consorcio en el ámbito' using errcode = '42501';
    end if;
    if origen_pedido is distinct from 'api' then
      raise exception 'origen inválido' using errcode = 'P0001', hint = 'origen_invalido';
    end if;
    if actor is null or actor is distinct from plataforma.persona_actual()
       or not nucleo.puede_saltear_documentacion(c, actor) then
      raise exception 'sin {ACCION_SALTEAR} en este consorcio' using errcode = '42501';
    end if;
    identidad_actor := nucleo.exigir_sesion_operativa(c, actor, sesion, origen_pedido, false);
    if motivo_salteo is null
       or length(btrim(motivo_salteo)) not between 1 and {MOTIVO_MAXIMO} then
      raise exception 'el motivo va de 1 a {MOTIVO_MAXIMO} caracteres' using errcode = 'P0001',
        hint = 'motivo_invalido';
    end if;
    if proveedor_salteado is null
       or not exists (select 1 from nucleo.proveedor p
                       where p.consorcio_id = c and p.id = proveedor_salteado) then
      raise exception 'el proveedor no existe en este consorcio' using errcode = 'P0001',
        hint = 'proveedor_inexistente';
    end if;
    -- El mismo candado que toma la barrera: un salteo y una orden a la vez se ven.
    perform pg_advisory_xact_lock(hashtextextended('{TABLA}:' || proveedor_salteado, 0));
    select * into k from nucleo.configuracion_proveedores_vigente(c);
    if k.modo_exigencia <> 'bloquear'
       or not exists (select 1 from nucleo.requisitos_faltantes(c, proveedor_salteado)) then
      raise exception 'nada bloquea las órdenes a este proveedor' using errcode = 'P0001',
        hint = 'salteo_innecesario';
    end if;
    if exists (select 1 from {TABLA} s
                where s.consorcio_id = c and s.proveedor_id = proveedor_salteado
                  and s.registrado_por = actor and s.vence_en > now()
                  and not exists (select 1 from {TABLA_USO} u where u.salteo_numero = s.numero))
    then
      raise exception 'ya registró un salteo que espera su orden' using errcode = 'P0001',
        hint = 'salteo_vigente';
    end if;
    vence := now() + make_interval(hours => k.horas_salteo);
    insert into {TABLA} (consorcio_id, proveedor_id, motivo, registrado_por, identidad, vence_en)
    values (c, proveedor_salteado, btrim(motivo_salteo), actor, identidad_actor, vence)
    returning numero into numero_nuevo;
    -- El motivo queda en la tabla y no en la auditoría (E31: la auditoría no guarda textos
    -- libres como el motivo).
    insert into auditoria.eventos
      (consorcio_id, actor_persona_id, origen, accion, entidad, entidad_id, antes, despues,
       hash_previo, hash)
    values (c, actor, 'api', '{ACCION_AUDITADA}', 'proveedor', proveedor_salteado,
            jsonb_build_object('salteo_vigente', false),
            jsonb_build_object('salteo_vigente', true, 'numero', numero_nuevo, 'vence_en', vence),
            '', '');
    return query
      select s.numero, s.proveedor_id, s.motivo, s.registrado_en, p.nombre, s.vence_en,
             'vigente'::text, null::uuid, null::integer, null::timestamptz
        from {TABLA} s
        join nucleo.persona p on p.id = s.registrado_por
       where s.numero = numero_nuevo;
  end
  $$;

-- Los salteos de un proveedor o de una orden (nulo: todos), con su estado y la orden que los usó.
-- Lee quien ve los proveedores o quien lee las órdenes, con la facultad con que lee.
create function nucleo.salteos_documentacion_lectura(proveedor uuid, orden uuid, facultad text,
                                                     actor uuid, origen_pedido text, sesion uuid)
  returns table (numero bigint, proveedor_id uuid, motivo text, registrado_en timestamptz,
                 registrado_por_nombre text, vence_en timestamptz, estado text, orden_id uuid,
                 version integer, usado_en timestamptz)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
  begin
    perform nucleo.exigir_lectura_proveedores(c, facultad, actor, origen_pedido, sesion);
    return query
      select s.numero, s.proveedor_id, s.motivo, s.registrado_en, p.nombre, s.vence_en,
             case when u.salteo_numero is not null then 'usado'
                  when s.vence_en <= now() then 'vencido'
                  else 'vigente' end,
             u.orden_id, u.version, u.usado_en
        from {TABLA} s
        join nucleo.persona p on p.id = s.registrado_por
        left join {TABLA_USO} u on u.salteo_numero = s.numero
       where s.consorcio_id = c
         and (proveedor is null or s.proveedor_id = proveedor)
         and (orden is null or u.orden_id = orden)
       order by s.numero;
  end
  $$;

-- El equipo cambia quién saltea y cuánto espera un salteo a su orden. Copia el resto de la
-- configuración que vale hoy; un valor nulo deja el que vale.
create function nucleo.configurar_salteo_documentacion(roles text[], horas integer, actor uuid,
                                                       origen_pedido text)
  returns timestamptz
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    k record;
    roles_nuevos text[];
    horas_nuevas integer;
    cuando timestamptz;
  begin
    if c is null then
      raise exception 'sin consorcio en el ámbito' using errcode = '42501';
    end if;
    if origen_pedido is distinct from 'soporte' then
      raise exception 'la configuración de proveedores la cambia solo el equipo (soporte)'
        using errcode = 'P0001', hint = 'origen_invalido';
    end if;
    if plataforma.persona_actual() is not null or actor is not null then
      raise exception 'el origen soporte no admite persona en la sesión ni actor'
        using errcode = 'P0001', hint = 'origen_invalido';
    end if;
    select * into k from nucleo.configuracion_proveedores_vigente(c);
    roles_nuevos := coalesce((select array_agg(distinct x order by x) from unnest(roles) x),
                             case when roles is null then k.roles_que_saltean
                                  else '{{}}'::text[] end);
    horas_nuevas := coalesce(horas, k.horas_salteo);
    if not roles_nuevos <@ array[{_roles_mandato}]::text[] then
      raise exception 'rol desconocido: quien saltea es un mandato (%)', array[{_roles_mandato}]
        using errcode = 'P0001', hint = 'rol_invalido';
    end if;
    if horas_nuevas not between 1 and {HORAS_MAXIMAS} then
      raise exception 'las horas van de 1 a {HORAS_MAXIMAS}' using errcode = 'P0001',
        hint = 'horas_invalidas';
    end if;
    if (roles_nuevos, horas_nuevas) is not distinct from (k.roles_que_saltean, k.horas_salteo)
       and k.registrada_en is not null then
      raise exception 'la configuración ya es esa' using errcode = 'P0001',
        hint = 'configuracion_sin_cambios';
    end if;
    insert into {TABLA_CONFIG}
      (consorcio_id, dias_por_vencer, modo_exigencia, admite_no_vence, roles_que_ven,
       requisitos_agregados, requisitos_omitidos, roles_que_autorizan, autoriza_por_persona,
       roles_que_saltean, horas_salteo, origen)
    values (c, k.dias_por_vencer, k.modo_exigencia, k.admite_no_vence, k.roles_que_ven,
            k.requisitos_agregados, k.requisitos_omitidos, k.roles_que_autorizan,
            k.autoriza_por_persona, roles_nuevos, horas_nuevas, 'soporte')
    returning registrada_en into cuando;
    return cuando;
  end
  $$;
"""

# Los cambios a las funciones de `0104` y `0105`, sobre el texto de `pg_get_functiondef`. Cada uno
# aparece exactamente una vez; el downgrade los deshace en el orden inverso.
_TABLA_0105 = (
    "roles_que_autorizan text[], autoriza_por_persona boolean,"
    " registrada_en timestamp with time zone)"
)
_TABLA_0106 = (
    "roles_que_autorizan text[], autoriza_por_persona boolean, roles_que_saltean text[],"
    " horas_salteo integer, registrada_en timestamp with time zone)"
)
VIGENTE_CAMBIOS = (
    (_TABLA_0105, _TABLA_0106),
    (
        "            k.autoriza_por_persona, k.registrada_en\n",
        "            k.autoriza_por_persona, k.roles_que_saltean, k.horas_salteo::integer,\n"
        "            k.registrada_en\n",
    ),
    (
        " true, null::timestamptz\n",
        f" true, array[{_roles_defecto}]::text[], {HORAS_SALTEO}, null::timestamptz\n",
    ),
)
SOPORTE_CAMBIOS = ((_TABLA_0105, _TABLA_0106),)
CONFIGURAR_CAMBIOS = (
    (
        " roles_que_autorizan, autoriza_por_persona, origen)\n",
        " roles_que_autorizan, autoriza_por_persona,\n"
        "       roles_que_saltean, horas_salteo, origen)\n",
    ),
    (
        "            nueva.omitidos, k.roles_que_autorizan, k.autoriza_por_persona, 'soporte')\n",
        "            nueva.omitidos, k.roles_que_autorizan, k.autoriza_por_persona,\n"
        "            k.roles_que_saltean, k.horas_salteo, 'soporte')\n",
    ),
)
CONFIGURAR_AUTORIZACION_CAMBIOS = (
    (
        "       requisitos_agregados, requisitos_omitidos, roles_que_autorizan,"
        " autoriza_por_persona,\n"
        "       origen)\n",
        "       requisitos_agregados, requisitos_omitidos, roles_que_autorizan,"
        " autoriza_por_persona,\n"
        "       roles_que_saltean, horas_salteo, origen)\n",
    ),
    (
        "            k.requisitos_agregados, k.requisitos_omitidos, roles_nuevos,"
        " por_persona_nuevo,\n"
        "            'soporte')\n",
        "            k.requisitos_agregados, k.requisitos_omitidos, roles_nuevos,"
        " por_persona_nuevo,\n"
        "            k.roles_que_saltean, k.horas_salteo, 'soporte')\n",
    ),
)
ROLES_CAMBIOS = (
    (
        "    select 'proveedores.autorizar'::text, k.roles_que_autorizan\n"
        "      from nucleo.configuracion_proveedores_vigente($1) k\n"
        "     where $1 = plataforma.consorcio_actual()\n",
        "    select 'proveedores.autorizar'::text, k.roles_que_autorizan\n"
        "      from nucleo.configuracion_proveedores_vigente($1) k\n"
        "     where $1 = plataforma.consorcio_actual()\n"
        "    union all\n"
        f"    select '{ACCION_SALTEAR}'::text, k.roles_que_saltean\n"
        "      from nucleo.configuracion_proveedores_vigente($1) k\n"
        "     where $1 = plataforma.consorcio_actual()\n",
    ),
)
BARRERA_CAMBIOS = (
    ("    anterior uuid;\n", "    anterior uuid;\n    salteo bigint;\n"),
    (
        "    if exists (select 1 from nucleo.requisitos_faltantes(new.consorcio_id,"
        " new.proveedor_id)) then\n"
        "      raise exception 'al proveedor le falta documentación que exige la ley'\n"
        "        using errcode = 'P0001', hint = 'documentacion_legal_faltante';\n"
        "    end if;\n",
        "    if exists (select 1 from nucleo.requisitos_faltantes(new.consorcio_id,"
        " new.proveedor_id)) then\n"
        "      -- 0106: un salteo con motivo, vigente y sin usar, que registró otra persona (y"
        " otra\n"
        "      -- identidad humana) que la que prepara, deja pasar esta versión, una sola vez.\n"
        f"      perform pg_advisory_xact_lock(hashtextextended('{TABLA}:' || new.proveedor_id,"
        " 0));\n"
        "      select s.numero into salteo\n"
        f"        from {TABLA} s\n"
        "       where s.consorcio_id = new.consorcio_id and s.proveedor_id = new.proveedor_id\n"
        "         and s.vence_en > now()\n"
        f"         and not exists (select 1 from {TABLA_USO} u where u.salteo_numero = s.numero)\n"
        "         and s.registrado_por is distinct from new.preparada_por\n"
        "         and s.identidad is distinct from new.identidad_preparo\n"
        "       order by s.numero limit 1;\n"
        "      if salteo is not null then\n"
        f"        insert into {TABLA_USO} (consorcio_id, salteo_numero, orden_id, version)\n"
        "        values (new.consorcio_id, salteo, new.orden_id, new.numero);\n"
        "        return new;\n"
        "      end if;\n"
        "      if exists (\n"
        f"           select 1 from {TABLA} s\n"
        "            where s.consorcio_id = new.consorcio_id and s.proveedor_id ="
        " new.proveedor_id\n"
        "              and s.vence_en > now()\n"
        f"              and not exists (select 1 from {TABLA_USO} u\n"
        "                               where u.salteo_numero = s.numero)) then\n"
        "        raise exception 'quien prepara la orden no saltea su propio bloqueo'\n"
        "          using errcode = 'P0001', hint = 'salteo_de_quien_prepara';\n"
        "      end if;\n"
        "      raise exception 'al proveedor le falta documentación que exige la ley'\n"
        "        using errcode = 'P0001', hint = 'documentacion_legal_faltante';\n"
        "    end if;\n",
    ),
)
_ULTIMA_ENTIDAD_0105 = (
    "                                'registrado_en', a.registrado_en)\n"
    "        from nucleo.autorizacion_ver_proveedores a\n"
    "       where a.consorcio_id = c\n"
    "       order by a.numero;\n"
)
EXPORTAR_CAMBIOS = (
    (
        "                                'autoriza_por_persona', k.autoriza_por_persona,\n",
        "                                'autoriza_por_persona', k.autoriza_por_persona,\n"
        "                                'roles_que_saltean', k.roles_que_saltean,\n"
        "                                'horas_salteo', k.horas_salteo,\n",
    ),
    (
        _ULTIMA_ENTIDAD_0105,
        _ULTIMA_ENTIDAD_0105
        + "    -- 0106: los salteos con motivo del bloqueo por documentación legal, y sus usos.\n"
        "    return query\n"
        "      select 'salteo_documentacion_proveedor'::text,\n"
        "             jsonb_build_object('numero', s.numero, 'proveedor_id', s.proveedor_id,\n"
        "                                'motivo', nucleo.texto_sin_cuenta(s.motivo),\n"
        "                                'registrado_por_nombre',\n"
        "                                nucleo.nombre_para_exportar(c, s.registrado_por, hoy),\n"
        "                                'registrado_en', s.registrado_en,\n"
        "                                'vence_en', s.vence_en)\n"
        f"        from {TABLA} s\n"
        "       where s.consorcio_id = c\n"
        "       order by s.numero;\n"
        "    return query\n"
        "      select 'salteo_documentacion_uso'::text,\n"
        "             jsonb_build_object('salteo_numero', u.salteo_numero, 'orden_id',"
        " u.orden_id,\n"
        "                                'version', u.version, 'usado_en', u.usado_en)\n"
        f"        from {TABLA_USO} u\n"
        "       where u.consorcio_id = c\n"
        "       order by u.salteo_numero;\n",
    ),
)

EN_SU_LUGAR = (
    (CONFIGURAR, CONFIGURAR_CAMBIOS),
    (CONFIGURAR_AUTORIZACION, CONFIGURAR_AUTORIZACION_CAMBIOS),
    (ROLES_CONFIGURADOS, ROLES_CAMBIOS),
    (BARRERA, BARRERA_CAMBIOS),
    (EXPORTAR_PROVEEDORES, EXPORTAR_CAMBIOS),
)
"""Mismo nombre, argumentos y resultado: `create or replace`."""
REDEFINIDAS = ((CONFIG_VIGENTE, VIGENTE_CAMBIOS), (CONFIG_SOPORTE, SOPORTE_CAMBIOS))
"""Cambia el resultado: se borran y se vuelven a crear con el texto parchado."""

GUARDA_DOWNGRADE = f"""
lock table {TABLA_CONFIG}, {TABLA}, {TABLA_USO} in access exclusive mode;
do $$
begin
  if exists (select 1 from {TABLA})
     or exists (select 1 from {TABLA_CONFIG}
                 where roles_que_saltean is distinct from array[{_roles_defecto}]::text[]
                    or horas_salteo is distinct from {HORAS_SALTEO}) then
    raise exception '0106 no baja con salteos o con una configuración que los usa'
      using errcode = 'P0001', hint = 'downgrade_con_datos';
  end if;
end
$$;
"""


def _definicion(funcion: str, cambios, *, al_reves: bool) -> str:
    definicion = (
        op.get_bind()
        .execute(text("select pg_get_functiondef(cast(:f as regprocedure))"), {"f": funcion})
        .scalar_one()
    )
    for antes, despues in reversed(cambios) if al_reves else cambios:
        if al_reves:
            antes, despues = despues, antes
        if definicion.count(antes) != 1:
            raise RuntimeError(f"0106: {funcion} no tiene exactamente una vez {antes!r}")
        definicion = definicion.replace(antes, despues)
    return definicion


def _redefinir(funcion: str, cambios, *, al_reves: bool) -> None:
    """Toma el texto, lo parcha, borra la función y la crea de nuevo, con sus permisos."""
    acl = (
        op.get_bind()
        .execute(
            text("select coalesce(proacl::text, '') from pg_proc where oid = to_regprocedure(:f)"),
            {"f": funcion},
        )
        .scalar_one()
    )
    definicion = _definicion(funcion, cambios, al_reves=al_reves)
    op.execute(f"drop function {funcion}")
    op.execute(definicion)
    op.execute(f"revoke all on function {funcion} from public")
    if "cem_app=X" in acl:
        op.execute(f"grant execute on function {funcion} to cem_app")


def upgrade() -> None:
    op.execute(TABLAS)
    for funcion, cambios in REDEFINIDAS:
        _redefinir(funcion, cambios, al_reves=False)
    op.execute(FUNCIONES)
    for funcion, cambios in EN_SU_LUGAR:
        op.execute(_definicion(funcion, cambios, al_reves=False))
    op.execute(RUTAS_SQL)
    for funcion in (*RUTAS, *INTERNAS):
        op.execute(f"revoke all on function {funcion} from public")
    for funcion in RUTAS:
        op.execute(f"grant execute on function {funcion} to cem_app")


def downgrade() -> None:
    op.execute(GUARDA_DOWNGRADE)
    for funcion in RUTAS:
        op.execute(f"drop function {funcion}")
    for funcion, cambios in reversed(EN_SU_LUGAR):
        op.execute(_definicion(funcion, cambios, al_reves=True))
    for funcion in reversed(INTERNAS):
        op.execute(f"drop function {funcion}")
    for funcion, cambios in reversed(REDEFINIDAS):
        _redefinir(funcion, cambios, al_reves=True)
    op.execute(f"drop table {TABLA_USO}")
    op.execute(f"drop table {TABLA}")
    op.execute(
        f"alter table {TABLA_CONFIG} drop column horas_salteo, drop column roles_que_saltean"
    )
