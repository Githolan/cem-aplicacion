"""RF50: el Consejo autoriza a un habitante a ver los proveedores (respuesta del propietario del
15-11-2026 a «¿quiénes son los habitantes autorizados?»: «Los que autorice el Consejo»). Diseño:
`doc/diseno/RF50_Configurable.md` del repositorio CEM, sección «Autorización por persona».

Hasta `0104` el permiso `proveedores.ver` se daba solo por tipo de rol (`roles_que_ven`): todos los
propietarios, todos los inquilinos o todos los ocupantes. Ahora el Consejo puede autorizar a **una
persona** y retirarle la autorización. Las dos formas conviven: ve quien tiene un rol de
`roles_que_ven` **o** una autorización vigente.

**La autorización** (`nucleo.autorizacion_ver_proveedores`). Una fila por cambio (`autorizar` o
`retirar`), con quién lo hizo y el motivo, que no se modifica ni se borra; vale la última por
persona. Solo se autoriza a quien tiene hoy un vínculo vigente con el consorcio (un habitante), y
la autorización cuenta mientras lo tenga.

**Quién autoriza** (`proveedores.autorizar`). Lo concede la configuración del consorcio, como
`proveedores.ver`: un mandato vigente de un tipo que está en `roles_que_autorizan`. Por defecto,
`miembro_consejo` (`ROLES_QUE_AUTORIZAN`). Es un acto del Consejo sin dinero: pide una sesión
válida y no el gesto (`0071`).

**Dos parámetros nuevos** en `nucleo.configuracion_proveedores`, para la futura aplicación de
mantenimiento:

- `roles_que_autorizan` (tipos de mandato): quién autoriza. Vacío: nadie autoriza a nadie nuevo;
- `autoriza_por_persona` (`AUTORIZA_POR_PERSONA`, sí): si las autorizaciones por persona cuentan.
  Apagado, ninguna da el permiso (quedan registradas) y no se registran nuevas.

Los cambia el equipo con `configurar_autorizacion_proveedores` (origen `soporte`, como
`configurar_proveedores` de `0104`); el script `scripts/configurar_proveedores.py` los acepta.
`configurar_proveedores` conserva los dos valores al cambiar los demás.

**La API** repite la regla: `nucleo.acciones_autorizadas` le da al contexto de cada pedido las
acciones que el Consejo autorizó a la persona, y el evaluador las concede por configuración.

**Exportación (`0069`):** `exportar_proveedores` suma `autorizacion_ver_proveedores` (con el nombre
de la persona y de quien la registró) y los dos parámetros en `configuracion_proveedores`.

Downgrade: se niega (`downgrade_con_datos`) si hay alguna autorización o alguna configuración con
valores distintos de los de por defecto en los parámetros nuevos.

Revisión: 0105
Anterior: 0104
"""

from alembic import op
from sqlalchemy import text

revision = "0105"
down_revision = "0104"
branch_labels = None
depends_on = None

ROLES_QUE_AUTORIZAN = ("miembro_consejo",)
AUTORIZA_POR_PERSONA = True
ROLES_DE_MANDATO = ("administrador", "administrador_externo", "miembro_consejo")
"""Solo un mandato autoriza: es un acto del Consejo (o de quien la configuración diga)."""
ACCION_VER = "proveedores.ver"
ACCION_AUTORIZAR = "proveedores.autorizar"
ACCION_AUDITADA = "consorcio.proveedor.autorizacion_ver"
MOTIVO_MAXIMO = 500

_lista = ", ".join
_roles_mandato = _lista(f"'{r}'" for r in ROLES_DE_MANDATO)
_roles_defecto = _lista(f"'{r}'" for r in ROLES_QUE_AUTORIZAN)
_por_persona = str(AUTORIZA_POR_PERSONA).lower()

TABLA_CONFIG = "nucleo.configuracion_proveedores"
TABLA = "nucleo.autorizacion_ver_proveedores"

CONFIG_VIGENTE = "nucleo.configuracion_proveedores_vigente(uuid)"
CONFIG_SOPORTE = "nucleo.configuracion_proveedores_soporte()"
CONFIGURAR = (
    "nucleo.configurar_proveedores(integer, text, boolean, text[], text[], text[], uuid, text)"
)
ROLES_CONFIGURADOS = "nucleo.roles_configurados(uuid)"
PUEDE_VER = "nucleo.puede_ver_proveedores(uuid, uuid)"
EXPORTAR_PROVEEDORES = "nucleo.exportar_proveedores(uuid, uuid)"

AUTORIZADOS = "nucleo.autorizados_ver_proveedores(uuid)"
ES_HABITANTE = "nucleo.es_habitante(uuid, uuid)"
PUEDE_AUTORIZAR = "nucleo.puede_autorizar_proveedores(uuid, uuid)"
EXIGIR_AUTORIZAR = "nucleo.exigir_autorizar_proveedores(uuid, uuid, text, uuid)"
INTERNAS = (AUTORIZADOS, ES_HABITANTE, PUEDE_AUTORIZAR, EXIGIR_AUTORIZAR)

ACCIONES_AUTORIZADAS = "nucleo.acciones_autorizadas(uuid)"
AUTORIZAR = "nucleo.autorizar_ver_proveedores(uuid, boolean, text, uuid, text, uuid)"
LECTURA = "nucleo.autorizaciones_ver_proveedores_lectura(uuid, text, uuid)"
CONFIGURAR_AUTORIZACION = "nucleo.configurar_autorizacion_proveedores(text[], boolean, uuid, text)"
RUTAS = (ACCIONES_AUTORIZADAS, AUTORIZAR, LECTURA, CONFIGURAR_AUTORIZACION)

TABLAS = f"""
alter table {TABLA_CONFIG}
  add column roles_que_autorizan text[] not null default array[{_roles_defecto}]::text[]
    check (roles_que_autorizan <@ array[{_roles_mandato}]::text[]),
  add column autoriza_por_persona boolean not null default {_por_persona};
-- Las filas que ya estaban toman los valores por defecto; las nuevas los dicen siempre.
alter table {TABLA_CONFIG}
  alter column roles_que_autorizan drop default,
  alter column autoriza_por_persona drop default;

create table {TABLA} (
  consorcio_id    uuid not null references nucleo.consorcio (id),
  numero          bigint generated always as identity primary key,
  persona_id      uuid not null references nucleo.persona (id),
  cambio          text not null check (cambio in ('autorizar', 'retirar')),
  motivo          text not null
    check (length(btrim(motivo)) between 1 and {MOTIVO_MAXIMO} and motivo = btrim(motivo)),
  registrado_por  uuid not null references nucleo.persona (id),
  registrado_en   timestamptz not null default now()
);
create index autorizacion_ver_proveedores_persona on {TABLA} (consorcio_id, persona_id, numero);
alter table {TABLA} enable row level security;
alter table {TABLA} force row level security;
create policy por_consorcio on {TABLA} for select
  using (consorcio_id = plataforma.consorcio_actual());
revoke all on {TABLA} from public, cem_app;
create trigger historia_autorizacion_ver_proveedores before update or delete on {TABLA}
  for each row execute function nucleo.negar_cambio_historia_orden_pago();
create trigger historia_autorizacion_ver_proveedores_truncate before truncate on {TABLA}
  for each statement execute function nucleo.negar_cambio_historia_orden_pago();
"""

FUNCIONES = f"""
-- Las personas cuya última fila es `autorizar`.
create function {AUTORIZADOS}
  returns setof uuid
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select u.persona_id
      from (select distinct on (a.persona_id) a.persona_id, a.cambio
              from {TABLA} a where a.consorcio_id = $1
             order by a.persona_id, a.numero desc) u
     where u.cambio = 'autorizar'
  $$;

-- Un habitante: un vínculo vigente hoy, que no sea uno importado que espera confirmación.
create function {ES_HABITANTE}
  returns boolean
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select exists (
      select 1 from nucleo.vinculo v
       where v.consorcio_id = $1 and v.persona_id = $2
         and daterange(v.vigente_desde, v.vigente_hasta) @> nucleo.hoy_del_consorcio($1)
         and v.id not in (select nucleo.vinculos_importados_pendientes()))
  $$;

-- Un mandato vigente de un tipo que la configuración lista en `roles_que_autorizan`.
create function {PUEDE_AUTORIZAR}
  returns boolean
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select exists (
             select 1 from nucleo.mandato m
              where m.consorcio_id = $1 and m.persona_id = $2
                and nucleo.mandato_vigente(m.id, nucleo.hoy_del_consorcio($1))
                and m.tipo::text = any (k.roles_que_autorizan))
      from nucleo.configuracion_proveedores_vigente($1) k
  $$;

-- `proveedores.autorizar`: origen `api`, la persona de la sesión, el rol de la configuración y una
-- sesión válida. Sin gesto (no mueve dinero, `0071`) ni identidad humana, como la API.
create function {EXIGIR_AUTORIZAR}
  returns void
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  begin
    if $1 is null then
      raise exception 'sin consorcio en el ámbito' using errcode = '42501';
    end if;
    if $3 is distinct from 'api' then
      raise exception 'origen inválido' using errcode = 'P0001', hint = 'origen_invalido';
    end if;
    if $2 is null or $2 is distinct from plataforma.persona_actual()
       or not nucleo.puede_autorizar_proveedores($1, $2) then
      raise exception 'sin {ACCION_AUTORIZAR} en este consorcio' using errcode = '42501';
    end if;
    if not exists (select 1 from nucleo.sesion x
                    where x.id = $4 and x.persona_id = $2 and x.revocada_en is null
                      and x.vence_en > now()) then
      raise exception 'la sesión no es válida' using errcode = 'P0001', hint = 'sesion_invalida';
    end if;
  end
  $$;
"""

RUTAS_SQL = f"""
-- Para el contexto de cada pedido: las acciones que el Consejo le autorizó a la persona de la
-- sesión y que cuentan hoy (configuración encendida y la persona sigue siendo habitante).
create function {ACCIONES_AUTORIZADAS}
  returns setof text
  language sql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
    select '{ACCION_VER}'::text
      from nucleo.configuracion_proveedores_vigente($1) k
     where $1 = plataforma.consorcio_actual()
       and k.autoriza_por_persona
       and plataforma.persona_actual() in (select nucleo.autorizados_ver_proveedores($1))
       and nucleo.es_habitante($1, plataforma.persona_actual())
  $$;

-- El Consejo autoriza a un habitante o le retira la autorización, con el motivo. Devuelve el
-- número de la fila.
create function nucleo.autorizar_ver_proveedores(persona uuid, autorizar boolean, motivo text,
                                                 actor uuid, origen_pedido text, sesion uuid)
  returns bigint
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    tiene boolean;
    numero_nuevo bigint;
  begin
    perform nucleo.exigir_autorizar_proveedores(c, actor, origen_pedido, sesion);
    if not (select k.autoriza_por_persona from nucleo.configuracion_proveedores_vigente(c) k) then
      raise exception 'este consorcio no autoriza por persona' using errcode = 'P0001',
        hint = 'autorizacion_por_persona_apagada';
    end if;
    if motivo is null or length(btrim(motivo)) not between 1 and {MOTIVO_MAXIMO} then
      raise exception 'el motivo va de 1 a {MOTIVO_MAXIMO} caracteres' using errcode = 'P0001',
        hint = 'motivo_invalido';
    end if;
    if persona is null or not nucleo.es_habitante(c, persona) then
      raise exception 'la persona no tiene un vínculo vigente con el consorcio'
        using errcode = 'P0001', hint = 'persona_sin_vinculo';
    end if;
    -- Dos pedidos a la vez sobre la misma persona: el segundo ve el primero.
    perform pg_advisory_xact_lock(hashtextextended('{TABLA}:' || c || ':' || persona, 0));
    tiene := persona in (select nucleo.autorizados_ver_proveedores(c));
    if autorizar is null or tiene = autorizar then
      raise exception 'la autorización ya está así' using errcode = 'P0001',
        hint = 'autorizacion_sin_cambios';
    end if;
    insert into {TABLA} (consorcio_id, persona_id, cambio, motivo, registrado_por)
    values (c, persona, case when autorizar then 'autorizar' else 'retirar' end, btrim(motivo),
            actor)
    returning numero into numero_nuevo;
    insert into auditoria.eventos
      (consorcio_id, actor_persona_id, origen, accion, entidad, entidad_id, antes, despues,
       hash_previo, hash)
    values (c, actor, 'api', '{ACCION_AUDITADA}', 'persona', persona,
            jsonb_build_object('autorizada', tiene),
            jsonb_build_object('autorizada', autorizar, 'motivo', btrim(motivo)), '', '');
    return numero_nuevo;
  end
  $$;

-- Lo que ve quien autoriza: cada habitante, si está autorizado y la última fila (motivo, quién,
-- cuándo). Con la autorización por persona apagada, se niega.
create function nucleo.autorizaciones_ver_proveedores_lectura(actor uuid, origen_pedido text,
                                                              sesion uuid)
  returns table (persona_id uuid, nombre text, unidades text[], autorizada boolean,
                 motivo text, registrado_en timestamptz, registrado_por_nombre text)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
  begin
    perform nucleo.exigir_autorizar_proveedores(c, actor, origen_pedido, sesion);
    if not (select k.autoriza_por_persona from nucleo.configuracion_proveedores_vigente(c) k) then
      raise exception 'este consorcio no autoriza por persona' using errcode = 'P0001',
        hint = 'autorizacion_por_persona_apagada';
    end if;
    return query
      with habitantes as (
        select v.persona_id,
               array_agg(distinct u.designacion order by u.designacion) as unidades
          from nucleo.vinculo v
          join nucleo.unidad u on u.consorcio_id = v.consorcio_id and u.id = v.unidad_id
         where v.consorcio_id = c
           and daterange(v.vigente_desde, v.vigente_hasta) @> nucleo.hoy_del_consorcio(c)
           and v.id not in (select nucleo.vinculos_importados_pendientes())
         group by v.persona_id)
      select h.persona_id, p.nombre, h.unidades, coalesce(a.cambio = 'autorizar', false),
             a.motivo, a.registrado_en, q.nombre
        from habitantes h
        join nucleo.persona p on p.id = h.persona_id
        left join lateral (select x.cambio, x.motivo, x.registrado_en, x.registrado_por
                             from {TABLA} x
                            where x.consorcio_id = c and x.persona_id = h.persona_id
                            order by x.numero desc limit 1) a on true
        left join nucleo.persona q on q.id = a.registrado_por
       order by p.nombre, h.persona_id;
  end
  $$;

-- El equipo cambia quién autoriza y si la autorización por persona cuenta. Copia el resto de la
-- configuración que vale hoy; un valor nulo deja el que vale.
create function nucleo.configurar_autorizacion_proveedores(roles text[], por_persona boolean,
                                                           actor uuid, origen_pedido text)
  returns timestamptz
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    k record;
    roles_nuevos text[];
    por_persona_nuevo boolean;
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
                             case when roles is null then k.roles_que_autorizan
                                  else '{{}}'::text[] end);
    por_persona_nuevo := coalesce(por_persona, k.autoriza_por_persona);
    if not roles_nuevos <@ array[{_roles_mandato}]::text[] then
      raise exception 'rol desconocido: quien autoriza es un mandato (%)', array[{_roles_mandato}]
        using errcode = 'P0001', hint = 'rol_invalido';
    end if;
    if (roles_nuevos, por_persona_nuevo)
       is not distinct from (k.roles_que_autorizan, k.autoriza_por_persona)
       and k.registrada_en is not null then
      raise exception 'la configuración ya es esa' using errcode = 'P0001',
        hint = 'configuracion_sin_cambios';
    end if;
    insert into {TABLA_CONFIG}
      (consorcio_id, dias_por_vencer, modo_exigencia, admite_no_vence, roles_que_ven,
       requisitos_agregados, requisitos_omitidos, roles_que_autorizan, autoriza_por_persona,
       origen)
    values (c, k.dias_por_vencer, k.modo_exigencia, k.admite_no_vence, k.roles_que_ven,
            k.requisitos_agregados, k.requisitos_omitidos, roles_nuevos, por_persona_nuevo,
            'soporte')
    returning registrada_en into cuando;
    return cuando;
  end
  $$;
"""

# Los cambios a las funciones de `0104`, sobre el texto de `pg_get_functiondef`. Cada uno aparece
# exactamente una vez; el downgrade los deshace en el orden inverso.
_TABLA_0104 = (
    "requisitos_agregados text[], requisitos_omitidos text[],"
    " registrada_en timestamp with time zone)"
)
_TABLA_0105 = (
    "requisitos_agregados text[], requisitos_omitidos text[], roles_que_autorizan text[],"
    " autoriza_por_persona boolean, registrada_en timestamp with time zone)"
)
VIGENTE_CAMBIOS = (
    (_TABLA_0104, _TABLA_0105),
    (
        "            k.requisitos_agregados, k.requisitos_omitidos, k.registrada_en\n",
        "            k.requisitos_agregados, k.requisitos_omitidos, k.roles_que_autorizan,\n"
        "            k.autoriza_por_persona, k.registrada_en\n",
    ),
    (
        "'{}'::text[], '{}'::text[], null::timestamptz\n",
        f"'{{}}'::text[], '{{}}'::text[], array[{_roles_defecto}]::text[], {_por_persona},"
        " null::timestamptz\n",
    ),
)
SOPORTE_CAMBIOS = ((_TABLA_0104, _TABLA_0105),)
CONFIGURAR_CAMBIOS = (
    (
        "       requisitos_agregados, requisitos_omitidos, origen)\n",
        "       requisitos_agregados, requisitos_omitidos, roles_que_autorizan,"
        " autoriza_por_persona, origen)\n",
    ),
    (
        "            nueva.omitidos, 'soporte')\n",
        "            nueva.omitidos, k.roles_que_autorizan, k.autoriza_por_persona, 'soporte')\n",
    ),
)
ROLES_CAMBIOS = (
    (
        "     where $1 = plataforma.consorcio_actual()\n",
        "     where $1 = plataforma.consorcio_actual()\n"
        f"    union all\n"
        f"    select '{ACCION_AUTORIZAR}'::text, k.roles_que_autorizan\n"
        "      from nucleo.configuracion_proveedores_vigente($1) k\n"
        "     where $1 = plataforma.consorcio_actual()\n",
    ),
)
PUEDE_VER_CAMBIOS = (
    (
        "                and v.tipo::text = any (k.roles_que_ven))\n",
        "                and v.tipo::text = any (k.roles_que_ven))\n"
        "        or (k.autoriza_por_persona\n"
        "            and $2 in (select nucleo.autorizados_ver_proveedores($1))\n"
        "            and nucleo.es_habitante($1, $2))\n",
    ),
)
EXPORTAR_CAMBIOS = (
    (
        "                                'requisitos_omitidos', k.requisitos_omitidos,\n",
        "                                'requisitos_omitidos', k.requisitos_omitidos,\n"
        "                                'roles_que_autorizan', k.roles_que_autorizan,\n"
        "                                'autoriza_por_persona', k.autoriza_por_persona,\n",
    ),
    (
        "        from nucleo.configuracion_proveedores k\n"
        "       where k.consorcio_id = c\n"
        "       order by k.numero;\n",
        "        from nucleo.configuracion_proveedores k\n"
        "       where k.consorcio_id = c\n"
        "       order by k.numero;\n"
        "    -- 0105: las autorizaciones del Consejo para ver los proveedores, con los nombres.\n"
        "    return query\n"
        "      select 'autorizacion_ver_proveedores'::text,\n"
        "             jsonb_build_object('numero', a.numero,\n"
        "                                'persona_nombre',\n"
        "                                nucleo.nombre_para_exportar(c, a.persona_id, hoy),\n"
        "                                'cambio', a.cambio,\n"
        "                                'motivo', nucleo.texto_sin_cuenta(a.motivo),\n"
        "                                'registrado_por_nombre',\n"
        "                                nucleo.nombre_para_exportar(c, a.registrado_por, hoy),\n"
        "                                'registrado_en', a.registrado_en)\n"
        "        from nucleo.autorizacion_ver_proveedores a\n"
        "       where a.consorcio_id = c\n"
        "       order by a.numero;\n",
    ),
)

EN_SU_LUGAR = (
    (CONFIGURAR, CONFIGURAR_CAMBIOS),
    (ROLES_CONFIGURADOS, ROLES_CAMBIOS),
    (PUEDE_VER, PUEDE_VER_CAMBIOS),
    (EXPORTAR_PROVEEDORES, EXPORTAR_CAMBIOS),
)
"""Mismo nombre, argumentos y resultado: `create or replace`."""
REDEFINIDAS = ((CONFIG_VIGENTE, VIGENTE_CAMBIOS), (CONFIG_SOPORTE, SOPORTE_CAMBIOS))
"""Cambia el resultado: se borran y se vuelven a crear con el texto parchado."""

GUARDA_DOWNGRADE = f"""
lock table {TABLA_CONFIG}, {TABLA} in access exclusive mode;
do $$
begin
  if exists (select 1 from {TABLA})
     or exists (select 1 from {TABLA_CONFIG}
                 where roles_que_autorizan is distinct from array[{_roles_defecto}]::text[]
                    or autoriza_por_persona is distinct from {_por_persona}) then
    raise exception '0105 no baja con autorizaciones o con una configuración que las usa'
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
            raise RuntimeError(f"0105: {funcion} no tiene exactamente una vez {antes!r}")
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
    op.execute(f"drop table {TABLA}")
    op.execute(
        f"alter table {TABLA_CONFIG} drop column autoriza_por_persona,"
        " drop column roles_que_autorizan"
    )
