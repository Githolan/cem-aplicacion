"""Condición habilitante para integrar el Consejo (E62; decisión 4 del propietario, 21-10-2026).

Diseño: `doc/diseno/Decisiones_Propietario_2026-10-21.md` del repositorio CEM, §2.1.

Un mandato de `miembro_consejo` concede algo solo mientras la persona cumple, sin interrupción,
alguna **condición habilitante para integrar el Consejo**. Si deja de cumplirla, el mandato
queda **sin efecto operativo** y pendiente de revisión: no se borra ni se da de baja, conserva
su historia y no autoriza actos nuevos. La continuidad la resuelve la decisión competente: la
**revisión del mandato** (alternativa 1 del propietario, 21-10-2026; diseño §2.3).

- **Qué cuenta como condición** (definición 1): un vínculo en una unidad activa, de un carácter
  que figure en `nucleo.condicion_habilitante_consejo`, y aceptado o confirmado. El código no
  nombra ningún carácter: el catálogo dice cuáles admite la norma, con su fuente. Hoy,
  `propietario` (CCyC, art. 2064). "Aceptado o confirmado" es la regla de la decisión 2: un
  vínculo habilita recién con una aceptación o una confirmación válida, y **desde ese día**, no
  desde su `vigente_desde` (Codex en app #102): una confirmación tardía no tapa un hueco. La
  aceptación es la invitación que creó el vínculo (fuente `invitacion`) o la que lo reutilizó
  (`aceptar_invitacion` busca uno de la misma persona, unidad y carácter y no le cambia la
  fuente). La reutilización se reconoce por el evento de la aceptación, que nombra a ese vínculo
  (`vinculo_id`): no alcanza con otra invitación aceptada de la misma persona, unidad y carácter
  para otro período (Codex en app #102). Uno importado, declarado o cargado por documento, sin
  confirmar ni aceptar, no cuenta.
- **Cómo se detecta la pérdida** (definición 2): se calcula en cada pedido a partir de los
  vínculos del padrón; no hay ninguna marca guardada. `nucleo.condicion_consejo_sin_interrupcion`
  une las vigencias de esos vínculos y la recorta a `[inicio del mandato, fecha]`: la condición
  se cumple si queda **un solo tramo que llega hasta la fecha**. Un hueco, aunque después la
  persona vuelva a cumplirla, deja dos tramos: el mandato sigue pendiente de revisión y no se
  reactiva solo. Dos vínculos contiguos (vende una unidad y compra otra el mismo día) no dejan
  hueco. Antes de cumplirla por primera vez dentro del mandato, el mandato tampoco opera.
- **Qué queda sin efecto** (definición 3): todo el mandato. `nucleo.mandato_vigente`, el único
  lugar de la validez material (0012), suma la condición para `miembro_consejo`; las seis
  barreras que conceden algo ya pasan por ahí:
  - el ámbito y las facultades de `require_scope` (`app/contexto.py`);
  - `consorcios_con_mandato_vigente`;
  - la lectura del padrón (0014);
  - `habilitado_para_mandatos` (0015);
  - `tiene_facultad_vigente` (0018);
  - el cierre de sesiones de `finalizar_vinculo` (0020): quien se queda solo con un mandato
    pendiente pierde sus sesiones.

  Los demás tipos de mandato no cambian.
- **Negar ante la duda:** sin catálogo, sin vínculo aceptado o confirmado, o con la unidad o la
  persona dadas de baja, la condición no se cumple. La condición es `security definer`: da lo
  mismo para cualquiera que pregunte. Quien no ve el mandato por la RLS recibe `falso` de
  `mandato_vigente`, igual que antes.
- **Lectura:** `mandatos_del_consorcio` y `mandato_detalle` devuelven el estado
  `pendiente_revision` para un mandato vigente por sus fechas que no opera. Las dos funciones
  salen del texto de 0018, con un solo cambio.
- **Salida: la revisión del mandato** (`nucleo.revision_mandato`, de solo agregar). Un acto sobre
  el mismo mandato, respaldado por una asamblea del mismo consorcio con fecha hasta hoy, que le da
  eficacia **desde `eficaz_desde`** hacia adelante. `eficaz_desde` no es anterior a la fecha de la
  decisión ni al día del registro, así que nunca cubre el período sin efecto. La condición tiene
  que cumplirse sin interrupción desde el registro hasta ese día; si no, se rechaza (no queda una
  revisión en espera que active el mandato sola). Solo la admite un mandato de Consejo
  `pendiente_revision` el día del registro.
- **Tramos.** La continuidad se mide desde la última revisión con `eficaz_desde` hasta la fecha
  evaluada, y en ese tramo la condición tiene que cumplirse desde el día de su registro: una
  pérdida entre el registro y `eficaz_desde` la deja sin efecto. Sin revisión, desde
  el inicio del mandato con la regla indulgente: opera desde la primera vez que la cumple. Cada
  pérdida posterior deja el mandato pendiente otra vez y pide otra revisión, que abre otro tramo.
- **Lo que no cambia:** la fila del mandato, sus facultades, sus bajas y los actos que registró
  antes (decisiones, altas, auditoría). La revisión no toca la fila del mandato. La baja de un
  mandato pendiente sigue disponible: no mira `mandato_vigente`.

Downgrade: vuelve a las funciones de 0012 y 0018 y borra el catálogo y la tabla de revisiones.
Se niega si hay alguna revisión: es el registro de un acto de asamblea y se perdería.

Revisión: 0023
Anterior: 0022
"""

import importlib.util
from pathlib import Path

from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None

FUENTE_PROPIETARIO = (
    "Código Civil y Comercial, art. 2064: la asamblea puede designar un consejo integrado por"
    " propietarios"
)
CONDICION = "nucleo.condicion_consejo_sin_interrupcion(uuid, date)"
ESTADO = "nucleo.estado_operativo(uuid, text, date)"
COBERTURA = "nucleo.cobertura_consejo(uuid, date, date)"
RESOLVER = "nucleo.resolver_revision_mandato(uuid, uuid, date, uuid, text)"


def _funcion_de(archivo: str, constante: str, inicio: str) -> str:
    """El texto de una función tal como la dejó una migración anterior, en `create or replace`:
    es la base del cambio y lo que restaura el downgrade, sin copiarlo a mano."""
    ruta = Path(__file__).with_name(archivo)
    spec = importlib.util.spec_from_file_location(ruta.stem, ruta)
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    texto = getattr(modulo, constante)
    desde = texto.index(inicio)
    fin = texto.index("\n  $$;\n", desde) + len("\n  $$;\n")
    return texto[desde:fin].replace("create function", "create or replace function", 1)


def _vigente_de_0012() -> str:
    return _funcion_de(
        "0012_mandatos_baja_y_administrador_unico.py",
        "UPGRADE",
        "create function nucleo.mandato_vigente(",
    )


def _lecturas_de_0018() -> list[str]:
    return [
        _funcion_de("0018_mandatos_lectura_y_destinatario.py", "FUNCIONES", inicio)
        for inicio in (
            "create function nucleo.mandatos_del_consorcio(",
            "create function nucleo.mandato_detalle(",
        )
    ]


VIGENTE_0012 = """\
                      and daterange(m.vigente_desde, m.vigente_hasta) @> fecha)
"""
VIGENTE_0023 = """\
                      and daterange(m.vigente_desde, m.vigente_hasta) @> fecha
                      -- 0023 (E62): el Consejo opera solo con su condición habilitante.
                      and (m.tipo <> 'miembro_consejo'
                           or nucleo.condicion_consejo_sin_interrupcion(m.id, fecha)))
"""
ESTADO_0018 = "nucleo.estado_mandato(m.causa_baja, m.vigente_desde, m.vigente_hasta, hoy)"
ESTADO_0023 = (
    "nucleo.estado_operativo(m.id,\n"
    "               nucleo.estado_mandato(m.causa_baja, m.vigente_desde, m.vigente_hasta, hoy),"
    " hoy)"
)


def _vigente_0023() -> str:
    texto = _vigente_de_0012()
    assert texto.count(VIGENTE_0012) == 1
    return texto.replace(VIGENTE_0012, VIGENTE_0023)


def _lecturas_0023() -> list[str]:
    lecturas = _lecturas_de_0018()
    assert all(t.count(ESTADO_0018) == 1 for t in lecturas)
    return [t.replace(ESTADO_0018, ESTADO_0023) for t in lecturas]


CATALOGO = f"""
create table nucleo.condicion_habilitante_consejo (
  tipo   nucleo.tipo_vinculo primary key,
  fuente text not null check (length(btrim(fuente)) between 1 and 300)
);
comment on table nucleo.condicion_habilitante_consejo is
  'Caracteres de vínculo que la norma admite como condición para integrar el Consejo (E62).';
insert into nucleo.condicion_habilitante_consejo (tipo, fuente)
values ('propietario', '{FUENTE_PROPIETARIO}');
grant select on nucleo.condicion_habilitante_consejo to cem_app;

create table nucleo.revision_mandato (
  consorcio_id    uuid not null,
  id              uuid primary key default plataforma.uuid_v7(),
  mandato_id      uuid not null,
  decision_id     uuid not null,
  eficaz_desde    date not null,
  -- Nula cuando la registra el equipo de la plataforma (origen `soporte`).
  registrada_por  uuid references nucleo.persona (id),
  origen          text not null check (origen in ('api', 'soporte')),
  creada_en       timestamptz not null default now(),
  foreign key (consorcio_id, mandato_id) references nucleo.mandato (consorcio_id, id),
  foreign key (consorcio_id, decision_id) references nucleo.decision_formal (consorcio_id, id),
  unique (mandato_id, eficaz_desde),
  check ((origen = 'api') = (registrada_por is not null))
);
comment on table nucleo.revision_mandato is
  'Revisiones de un mandato del Consejo pendiente de revisión (E62): de solo agregar.';
alter table nucleo.revision_mandato enable row level security;
alter table nucleo.revision_mandato force row level security;
create policy por_consorcio on nucleo.revision_mandato for select
  using (consorcio_id = plataforma.consorcio_actual());
-- De solo agregar: `cem_app` lee; las filas entran solo por `resolver_revision_mandato`.
grant select on nucleo.revision_mandato to cem_app;

-- E62. Los días de `[desde, fecha]` en que la persona del mandato cumple alguna condición
-- habilitante, calculados del padrón en cada llamada, sin marcas guardadas. `range_agg` une los
-- tramos contiguos. Sin filas, o con `desde` posterior a `fecha`, vacía.
-- `security definer`: es un hecho del padrón y da lo mismo para cualquiera que pregunte. Con
-- los permisos de quien llama, la RLS de `persona` o de `vinculo` le escondería filas a un
-- lector y no a otro, y el mismo mandato tendría dos estados. `cem_app` no la llama: la usan
-- la condición y la revisión.
create function {COBERTURA.replace("(uuid, date, date)", "(mandato uuid, desde date, fecha date)")}
  returns datemultirange
  language sql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
    select coalesce((
      select range_agg(daterange(greatest(v.vigente_desde, a.habilita), v.vigente_hasta))
               * datemultirange(daterange(least(cobertura_consejo.desde, fecha + 1), fecha + 1))
        from nucleo.mandato m
        join nucleo.consorcio co on co.id = m.consorcio_id
        join nucleo.vinculo v
          on v.consorcio_id = m.consorcio_id and v.persona_id = m.persona_id
        join nucleo.condicion_habilitante_consejo h on h.tipo = v.tipo
        join nucleo.unidad u
          on u.consorcio_id = v.consorcio_id and u.id = v.unidad_id and u.eliminado_en is null
        join nucleo.persona pe on pe.id = v.persona_id and pe.eliminado_en is null
        -- Desde qué día habilita el vínculo: el primero de su confirmación, de su alta por una
        -- invitación aceptada o de una aceptación que lo reutilizó (`aceptar_invitacion`, 0014,
        -- no le cambia la fuente). La aceptación es la que nombra a **este** vínculo en su
        -- evento (`vinculo_id`): una invitación aceptada para otro período de la misma persona,
        -- unidad y carácter no lo habilita (Codex en app #102). Antes, no cuenta.
        cross join lateral (
          select least(
            (v.confirmado_en at time zone co.zona_horaria)::date,
            case when v.fuente = 'invitacion'
                 then (v.creado_en at time zone co.zona_horaria)::date end,
            (select min((i.aceptada_en at time zone co.zona_horaria)::date)
               from auditoria.eventos e
               join nucleo.invitacion i
                 on i.id = e.entidad_id and i.consorcio_id = e.consorcio_id
              where e.consorcio_id = v.consorcio_id
                and e.accion = 'identidad.invitacion.aceptar' and e.entidad = 'invitacion'
                and e.despues ->> 'vinculo_id' = v.id::text
                and i.aceptada_en is not null)) as habilita) a
       where m.id = mandato and a.habilita is not null
         and (v.vigente_hasta is null or greatest(v.vigente_desde, a.habilita) < v.vigente_hasta)),
      '{{}}'::datemultirange)
  $$;

-- E62. ¿Cumple la persona del mandato, sin interrupción en el tramo que llega a `fecha`, alguna
-- condición habilitante? El tramo lo abre la última revisión con `eficaz_desde` hasta `fecha`,
-- y entonces la condición tiene que cumplirse desde el día en que se registró (o desde
-- `eficaz_desde`, si es anterior): una pérdida entre el registro y la eficacia la deja sin
-- efecto, y la revisión no espera a un vínculo nuevo (revisión independiente en app #102). Sin
-- revisión, empieza con el mandato y vale la regla indulgente: desde la primera vez que la
-- cumple. En los dos casos la cobertura tiene que ser **un solo tramo que llega hasta la
-- fecha**: un hueco deja dos, y el mandato no se reactiva solo. Sin filas, falso.
create function {CONDICION.replace("(uuid, date)", "(mandato uuid, fecha date)")}
  returns boolean
  language sql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
    select coalesce((
      select not isempty(c.cobertura)
             and c.cobertura = datemultirange(daterange(lower(c.cobertura), fecha + 1))
             and (t.revision is null or lower(c.cobertura) = t.revision)
        from (select m.vigente_desde,
                     (select least((r.creada_en at time zone co.zona_horaria)::date,
                                   r.eficaz_desde)
                        from nucleo.revision_mandato r
                       where r.mandato_id = m.id and r.eficaz_desde <= fecha
                       order by r.eficaz_desde desc limit 1) as revision
                from nucleo.mandato m
                join nucleo.consorcio co on co.id = m.consorcio_id
               where m.id = mandato) t
       cross join lateral (
         select nucleo.cobertura_consejo(mandato, coalesce(t.revision, t.vigente_desde), fecha)
                as cobertura) c), false)
  $$;

-- La revisión del mandato (alternativa 1, diseño §2.3): le da eficacia desde `eficaz_desde` a
-- un mandato del Consejo pendiente de revisión. La política ya decidió quién (facultad, A4 y
-- A15); acá se repiten A4 y A15 como segunda barrera y se valida lo demás. No toca la fila del
-- mandato.
create function {
    RESOLVER.replace(
        "(uuid, uuid, date, uuid, text)",
        "(mandato uuid, decision uuid, eficaz_desde date, actor uuid, origen_pedido text)",
    )
}
  returns uuid
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    m nucleo.mandato;
    d nucleo.decision_formal;
    hoy date;
    nueva uuid;
  begin
    if c is null then
      raise exception 'sin consorcio en el ámbito' using errcode = '42501';
    end if;
    if origen_pedido is null or origen_pedido not in ('api', 'soporte') then
      raise exception 'origen no admitido: solo api o soporte'
        using errcode = 'P0001', hint = 'origen_invalido';
    end if;
    -- El actor es la persona de la sesión (como en `baja_mandato`); con soporte no hay ninguno.
    if origen_pedido = 'api'
       and (actor is null or actor is distinct from plataforma.persona_actual()) then
      raise exception 'el actor no es la persona de la sesión'
        using errcode = 'P0001', hint = 'actor_no_coincide';
    end if;
    if origen_pedido = 'soporte'
       and (actor is not null or plataforma.persona_actual() is not null) then
      raise exception 'el origen soporte no lleva actor ni persona en la sesión'
        using errcode = 'P0001', hint = 'origen_invalido';
    end if;
    select * into m from nucleo.mandato x where x.consorcio_id = c and x.id = mandato for update;
    if m.id is null then
      raise exception 'el mandato no existe en este consorcio'
        using errcode = 'P0001', hint = 'mandato_invalido';
    end if;
    -- A4: nadie resuelve su propio mandato.
    if origen_pedido = 'api' and actor = m.persona_id then
      raise exception 'nadie revisa su propio mandato'
        using errcode = 'P0001', hint = 'revision_de_si_mismo';
    end if;
    hoy := nucleo.hoy_del_consorcio(c);
    if m.causa_baja is not null then
      raise exception 'el mandato ya tiene una baja registrada'
        using errcode = 'P0001', hint = 'mandato_ya_dado_de_baja';
    end if;
    if m.vigente_hasta is not null and m.vigente_hasta <= hoy then
      raise exception 'el mandato ya terminó' using errcode = 'P0001', hint = 'mandato_terminado';
    end if;
    -- Solo el Consejo pendiente de revisión hoy: sobre uno que opera, o que todavía no empezó,
    -- sería un acto vacío.
    if m.tipo <> 'miembro_consejo'
       or nucleo.estado_operativo(
            m.id, nucleo.estado_mandato(m.causa_baja, m.vigente_desde, m.vigente_hasta, hoy), hoy)
          <> 'pendiente_revision' then
      raise exception 'el mandato no está pendiente de revisión'
        using errcode = 'P0001', hint = 'mandato_no_pendiente';
    end if;
    -- Decisión válida: del mismo consorcio, de asamblea y con fecha hasta hoy (0019).
    select * into d from nucleo.decision_formal x
     where x.consorcio_id = c and x.id = decision and x.tipo = 'asamblea' and x.fecha <= hoy;
    if d.id is null then
      raise exception 'la revisión necesita una decisión de asamblea de este consorcio'
        using errcode = 'P0001', hint = 'decision_invalida';
    end if;
    -- A15: quien registró la decisión no la usa para revisar.
    if origen_pedido = 'api' and d.registrada_por = actor then
      raise exception 'quien registró la decisión no la usa para revisar'
        using errcode = 'P0001', hint = 'decision_registrada_por_el_actor';
    end if;
    -- Hacia adelante: nunca antes de la decisión ni del día del registro, y dentro del mandato.
    if eficaz_desde is null or eficaz_desde < greatest(d.fecha, hoy)
       or (m.vigente_hasta is not null and eficaz_desde >= m.vigente_hasta) then
      raise exception 'la revisión rige desde hoy o después, y dentro del mandato'
        using errcode = 'P0001', hint = 'eficaz_desde_invalida';
    end if;
    if exists (select 1 from nucleo.revision_mandato r
                where r.mandato_id = m.id
                  and r.eficaz_desde = resolver_revision_mandato.eficaz_desde) then
      raise exception 'el mandato ya tiene una revisión desde ese día'
        using errcode = 'P0001', hint = 'revision_duplicada';
    end if;
    -- La condición tiene que cumplirse sin interrupción desde hoy hasta el día en que empieza a
    -- regir, con lo que se sabe hoy del padrón; después, la condición la vuelve a medir desde
    -- hoy en cada pedido (una pérdida en el medio deja la revisión sin efecto).
    if nucleo.cobertura_consejo(m.id, hoy, eficaz_desde)
       <> datemultirange(daterange(hoy, eficaz_desde + 1)) then
      raise exception 'la persona no cumple una condición habilitante de hoy a ese día'
        using errcode = 'P0001', hint = 'sin_condicion_habilitante';
    end if;

    insert into nucleo.revision_mandato
      (consorcio_id, mandato_id, decision_id, eficaz_desde, registrada_por, origen)
    values (c, m.id, d.id, eficaz_desde, actor, origen_pedido)
    returning id into nueva;

    insert into auditoria.eventos
      (consorcio_id, actor_persona_id, origen, accion, entidad, entidad_id, antes, despues,
       hash_previo, hash)
    values (c, actor, origen_pedido, 'consorcio.mandato.revision', 'mandato', m.id,
            jsonb_build_object('estado', 'pendiente_revision'),
            jsonb_build_object('revision_id', nueva, 'decision_id', d.id,
                               'eficaz_desde', eficaz_desde),
            '', '');
    return nueva;
  end
  $$;

-- Para leer: un mandato vigente por sus fechas que no opera queda pendiente de revisión.
create function {ESTADO.replace("(uuid, text, date)", "(mandato uuid, estado text, hoy date)")}
  returns text
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select case when estado = 'vigente' and not nucleo.mandato_vigente(mandato, hoy)
                then 'pendiente_revision' else estado end
  $$;

revoke all on function {COBERTURA}, {CONDICION}, {ESTADO}, {RESOLVER} from public;
grant execute on function {CONDICION}, {RESOLVER} to cem_app;
"""

GUARDA = r"""
do $guarda$
begin
  if exists (select 1 from nucleo.revision_mandato) then
    raise exception 'hay revisiones de mandatos: bajar a 0022 las perdería'
      using errcode = 'P0001', hint = 'downgrade_con_datos';
  end if;
end
$guarda$;
"""


def upgrade() -> None:
    op.execute(CATALOGO)
    op.execute(_vigente_0023())
    for lectura in _lecturas_0023():
        op.execute(lectura)


def downgrade() -> None:
    op.execute(GUARDA)
    op.execute(_vigente_de_0012())
    for lectura in _lecturas_de_0018():
        op.execute(lectura)
    op.execute(
        f"drop function {ESTADO};\n"
        f"drop function {RESOLVER};\n"
        f"drop function {CONDICION};\n"
        f"drop function {COBERTURA};\n"
        "drop table nucleo.revision_mandato;\n"
        "drop table nucleo.condicion_habilitante_consejo;\n"
    )
