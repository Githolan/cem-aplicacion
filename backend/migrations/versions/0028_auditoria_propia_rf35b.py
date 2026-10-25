"""2b-6c: lectura de la auditoría con exposición mínima (RF35-B).

Diseño: decisiones del propietario del 24-10-2026 sobre 2b-6 (repositorio CEM,
`doc/diseno/H2b_6_Diseno.md`, §6). Hasta ahora nadie leía `auditoria.eventos` por la aplicación
(decisión 10 del 21-10). Ahora hay dos lecturas, y las dos devuelven una proyección funcional que
arma la base: `cem_app` sigue sin leer la fila por la ruta (la RLS sola no alcanza).

- **`auditoria.eventos_propios`:** los eventos que hizo quien lee, en el consorcio del ámbito.
- **`auditoria.eventos_del_consorcio`:** todos los del consorcio, con `auditoria.ver` vigente
  (segunda barrera, como las lecturas de `0018`). La facultad entra al catálogo y a los paquetes
  de `miembro_consejo` y `administrador`, no al de `administrador_externo`. Como en `0018`, los
  mandatos que ya existen conservan lo que otorgó su decisión: no se les agrega nada (decisión 1:
  un acta reduce un paquete, nunca lo amplía).

**Proyección.** `auditoria.proyeccion` es la lista blanca: por acción, el texto traducido, el
recurso funcional y los campos de `antes`/`despues` que se muestran. Una acción que no está en la
lista no sale nunca. Quedan fuera, a propósito:
- `autorizacion.denegada`: el detalle de una negación es interno;
- `identidad.humana.*`: la identidad humana la gestiona solo soporte (A13-C);
- de cada evento: IP, hashes (`hash`, `hash_previo`, `sha256_acta`, `archivo_sha256`), la
  secuencia, todos los identificadores (`*_id`, `entidad_id`, el actor), el texto libre
  (`referencia`), los datos del consorcio (`nombre`, `cuit`) y todo campo que no esté en la lista.

**Actor** (solo en los eventos del consorcio): `vos`, `persona`, `soporte` o `sistema`. El nombre de
otra persona sale solo si quien lee tiene `padron.ver` vigente (general, sin recurso) y esa persona
figura hoy en el padrón: un vínculo vigente o futuro, con la unidad y la persona sin borrar (el
filtro de `padron_del_consorcio`, 0014). Lo que el padrón ya no muestra (un propietario que se fue,
un vínculo anulado, un mandatario sin vínculo) sale sin nombre.

**Paginación.** Del más reciente al más antiguo, por `(creado_en, secuencia)`. El cursor no lleva
la secuencia: es la fecha del último evento de la página y cuántos eventos de esa misma fecha ya
salieron (`antes_de`, `saltar`). Sin exportación (E0).

Downgrade: se niega si algún mandato ya tiene `auditoria.ver` otorgada (es constancia del acta);
si no, quita las lecturas, la lista blanca, los índices y la facultad.

Revisión: 0028
Anterior: 0027
"""

from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None

FACULTAD = "auditoria.ver"
TIPOS_CON_AUDITORIA = ("miembro_consejo", "administrador")
LIMITE_MAXIMO = 100

# (acción, texto, recurso, campos): la lista blanca funcional. Los campos son los de `antes` y
# `despues` que no identifican a nadie ni son técnicos (inventario de los eventos de 0027).
PROYECCION = (
    ("consorcios.consorcio.crear", "Dio de alta el consorcio", "Consorcio", ()),
    ("consorcios.unidad.crear", "Creó una unidad", "Unidad", ("designacion",)),
    ("consorcio.unidad.crear", "Creó una unidad al importar el padrón", "Unidad", ()),
    ("consorcio.padron.importar", "Importó el padrón", "Padrón", ()),
    (
        "consorcio.vinculo.importar",
        "Importó un vínculo del padrón",
        "Vínculo",
        ("tipo", "vigente_desde", "vigente_hasta"),
    ),
    (
        "consorcio.vinculo.anular",
        "Anuló un vínculo importado",
        "Vínculo",
        ("tipo", "vigente_desde", "vigente_hasta", "confirmado"),
    ),
    ("consorcio.vinculo.confirmar", "Confirmó un vínculo", "Vínculo", ("tipo", "confirmado")),
    ("consorcio.vinculo.finalizar", "Finalizó un vínculo", "Vínculo", ("tipo", "vigente_hasta")),
    ("identidad.invitacion.crear", "Invitó a una persona a una unidad", "Invitación", ("tipo",)),
    ("identidad.invitacion.reemplazar", "Reemplazó una invitación", "Invitación", ()),
    ("identidad.invitacion.revocar", "Retiró una invitación", "Invitación", ()),
    ("identidad.invitacion.aceptar", "Aceptó una invitación", "Invitación", ("tipo",)),
    ("consorcio.decision.registrar", "Registró una decisión", "Decisión", ("tipo", "fecha")),
    (
        "consorcio.modo.cambiar",
        "Cambió el modo de operación",
        "Modo de operación",
        ("modo", "desde"),
    ),
    (
        "consorcio.coeficientes.registrar",
        "Registró un esquema de coeficientes",
        "Esquema de coeficientes",
        ("grupo", "version", "vigente_desde", "metodo"),
    ),
    ("consorcio.mandato.alta", "Dio de alta un mandato", "Mandato", ("tipo", "desde", "hasta")),
    ("consorcio.mandato.baja", "Dio de baja un mandato", "Mandato", ("causa", "vigente_hasta")),
    (
        "consorcio.mandato.revision",
        "Resolvió la revisión de un mandato",
        "Mandato",
        ("estado", "eficaz_desde"),
    ),
    ("consorcio.mandato.vencimiento", "Venció un mandato", "Mandato", ("vigente_hasta",)),
    (
        "consorcio.mandato.invitar_persona",
        "Invitó a una persona a un mandato",
        "Invitación a un mandato",
        (),
    ),
    (
        "consorcio.mandato.reemplazar_invitacion",
        "Reemplazó la invitación a un mandato",
        "Invitación a un mandato",
        (),
    ),
    (
        "consorcio.mandato.aprobar_invitacion",
        "Aprobó la invitación a un mandato",
        "Invitación a un mandato",
        (),
    ),
    (
        "consorcio.mandato.revocar_invitacion",
        "Retiró la invitación a un mandato",
        "Invitación a un mandato",
        (),
    ),
    ("consorcio.mandato.asociar_persona", "Asoció su cuenta a un mandato", "Mandato", ()),
    ("consorcio.mandato.desasociar_persona", "Desasoció la cuenta de un mandato", "Mandato", ()),
)


def _literal(texto: str) -> str:
    return "'" + texto.replace("'", "''") + "'"


def _proyeccion() -> str:
    filas = ",\n  ".join(
        f"({_literal(a)}, {_literal(t)}, {_literal(r)}, "
        f"array[{', '.join(_literal(c) for c in campos)}]::text[])"
        for a, t, r, campos in PROYECCION
    )
    paquetes = ",\n  ".join(f"('{t}', '{FACULTAD}')" for t in TIPOS_CON_AUDITORIA)
    return f"""
create table auditoria.proyeccion (
  accion   text primary key,
  texto    text not null,
  recurso  text not null,
  campos   text[] not null
);
insert into auditoria.proyeccion (accion, texto, recurso, campos) values
  {filas};
revoke all on auditoria.proyeccion from public, cem_app;

insert into nucleo.facultad (codigo, area, economica) values ('{FACULTAD}', 'auditoria', false);
insert into nucleo.paquete_facultad (tipo, facultad) values
  {paquetes};
"""


# El orden de lectura de las dos superficies.
INDICES = """
create index eventos_por_fecha on auditoria.eventos (consorcio_id, creado_en desc, secuencia desc);
create index eventos_por_actor
  on auditoria.eventos (consorcio_id, actor_persona_id, creado_en desc, secuencia desc);
"""

FUNCIONES = f"""
-- Los cambios de la lista blanca: por campo, el valor antes y después, en el orden de la lista.
create function auditoria.cambios_funcionales(antes jsonb, despues jsonb, campos text[])
  returns jsonb
  language sql immutable
  set search_path = pg_catalog, pg_temp
  as $$
    select coalesce(jsonb_agg(jsonb_build_object('campo', u.k, 'antes', antes ->> u.k,
                                                 'despues', despues ->> u.k)
                              order by u.o), '[]'::jsonb)
      from unnest(campos) with ordinality as u(k, o)
     where antes ->> u.k is not null or despues ->> u.k is not null
  $$;

-- Interna: los parámetros de la página.
create function auditoria.exigir_pagina(antes_de timestamptz, saltar integer, limite integer)
  returns void
  language plpgsql immutable
  set search_path = pg_catalog, pg_temp
  as $$
  begin
    if limite is null or limite < 1 or limite > {LIMITE_MAXIMO}
       or saltar is null or saltar < 0 or (saltar > 0 and antes_de is null) then
      raise exception 'página inválida' using errcode = '22023';
    end if;
  end
  $$;

create function auditoria.eventos_propios(antes_de timestamptz, saltar integer, limite integer)
  returns table (fecha timestamptz, accion text, recurso text, origen text, cambios jsonb)
  language plpgsql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    quien uuid := plataforma.persona_actual();
  begin
    if c is null or quien is null then
      raise exception 'sin ámbito para leer la auditoría' using errcode = '42501';
    end if;
    perform auditoria.exigir_pagina(antes_de, saltar, limite);
    return query
      with pagina as (
        (select e.creado_en, e.secuencia, e.origen, e.accion, e.antes, e.despues
           from auditoria.eventos e
          where e.consorcio_id = c and e.actor_persona_id = quien and e.creado_en = antes_de
            and exists (select 1 from auditoria.proyeccion p where p.accion = e.accion)
          order by e.secuencia desc
         offset saltar limit limite)
        union all
        (select e.creado_en, e.secuencia, e.origen, e.accion, e.antes, e.despues
           from auditoria.eventos e
          where e.consorcio_id = c and e.actor_persona_id = quien
            and (antes_de is null or e.creado_en < antes_de)
            and exists (select 1 from auditoria.proyeccion p where p.accion = e.accion)
          order by e.creado_en desc, e.secuencia desc
          limit limite)
      )
      select g.creado_en, p.texto, p.recurso,
             case g.origen when 'api' then 'aplicacion' when 'soporte' then 'soporte'
                           else 'sistema' end,
             auditoria.cambios_funcionales(g.antes, g.despues, p.campos)
        from pagina g join auditoria.proyeccion p on p.accion = g.accion
       order by g.creado_en desc, g.secuencia desc
       limit limite;
  end
  $$;

create function auditoria.eventos_del_consorcio(antes_de timestamptz, saltar integer,
                                                limite integer)
  returns table (fecha timestamptz, accion text, recurso text, origen text, actor text,
                 actor_nombre text, cambios jsonb)
  language plpgsql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    quien uuid := plataforma.persona_actual();
    hoy date;
    con_nombres boolean;
  begin
    if c is null or quien is null then
      raise exception 'sin ámbito para leer la auditoría' using errcode = '42501';
    end if;
    hoy := nucleo.hoy_del_consorcio(c);
    if not nucleo.tiene_facultad_vigente(c, quien, '{FACULTAD}', hoy) then
      raise exception 'sin {FACULTAD} en este consorcio' using errcode = '42501';
    end if;
    perform auditoria.exigir_pagina(antes_de, saltar, limite);
    -- El nombre de otra persona, solo para quien puede conocerlo por el padrón.
    con_nombres := nucleo.tiene_facultad_vigente(c, quien, 'padron.ver', hoy);
    return query
      with pagina as (
        (select e.creado_en, e.secuencia, e.actor_persona_id, e.origen, e.accion, e.antes,
                e.despues
           from auditoria.eventos e
          where e.consorcio_id = c and e.creado_en = antes_de
            and exists (select 1 from auditoria.proyeccion p where p.accion = e.accion)
          order by e.secuencia desc
         offset saltar limit limite)
        union all
        (select e.creado_en, e.secuencia, e.actor_persona_id, e.origen, e.accion, e.antes,
                e.despues
           from auditoria.eventos e
          where e.consorcio_id = c and (antes_de is null or e.creado_en < antes_de)
            and exists (select 1 from auditoria.proyeccion p where p.accion = e.accion)
          order by e.creado_en desc, e.secuencia desc
          limit limite)
      )
      select g.creado_en, p.texto, p.recurso,
             case g.origen when 'api' then 'aplicacion' when 'soporte' then 'soporte'
                           else 'sistema' end,
             case when g.origen = 'soporte' then 'soporte'
                  when g.origen <> 'api' then 'sistema'
                  when g.actor_persona_id = quien then 'vos'
                  else 'persona' end,
             case when con_nombres and g.origen = 'api' and g.actor_persona_id <> quien
                  then (select x.nombre from nucleo.persona x
                         where x.id = g.actor_persona_id and x.eliminado_en is null
                           and exists (
                             select 1 from nucleo.vinculo v
                               join nucleo.unidad u
                                 on u.consorcio_id = v.consorcio_id and u.id = v.unidad_id
                              where v.consorcio_id = c and v.persona_id = x.id
                                and (v.vigente_hasta is null or v.vigente_hasta > hoy)
                                and u.eliminado_en is null))
             end,
             auditoria.cambios_funcionales(g.antes, g.despues, p.campos)
        from pagina g join auditoria.proyeccion p on p.accion = g.accion
       order by g.creado_en desc, g.secuencia desc
       limit limite;
  end
  $$;

revoke all on function auditoria.cambios_funcionales(jsonb, jsonb, text[]) from public;
revoke all on function auditoria.exigir_pagina(timestamptz, integer, integer) from public;
revoke all on function auditoria.eventos_propios(timestamptz, integer, integer) from public;
revoke all on function auditoria.eventos_del_consorcio(timestamptz, integer, integer) from public;
grant execute on function auditoria.eventos_propios(timestamptz, integer, integer) to cem_app;
grant execute on function auditoria.eventos_del_consorcio(timestamptz, integer, integer)
  to cem_app;
"""


def upgrade() -> None:
    op.execute(_proyeccion())
    op.execute(INDICES)
    op.execute(FUNCIONES)


def downgrade() -> None:
    op.execute(
        f"""
do $$
begin
  if exists (select 1 from nucleo.facultad_otorgada where facultad = '{FACULTAD}') then
    raise exception 'el downgrade de 0028 se niega: hay mandatos con {FACULTAD}'
      using errcode = 'P0001', hint = 'downgrade_con_datos',
            detail = 'las facultades otorgadas son constancia de lo que decidió el acta';
  end if;
end
$$;
drop function auditoria.eventos_del_consorcio(timestamptz, integer, integer);
drop function auditoria.eventos_propios(timestamptz, integer, integer);
drop function auditoria.exigir_pagina(timestamptz, integer, integer);
drop function auditoria.cambios_funcionales(jsonb, jsonb, text[]);
drop index auditoria.eventos_por_actor;
drop index auditoria.eventos_por_fecha;
drop table auditoria.proyeccion;
delete from nucleo.paquete_facultad where facultad = '{FACULTAD}';
delete from nucleo.facultad where codigo = '{FACULTAD}';
"""
    )
