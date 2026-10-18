"""Mandatos y facultades otorgadas (H2b, corte 2b-2).

Documento 08: RN03 (facultades con respaldo documental), RN22 (el fin de un mandato quita las
facultades y revoca las sesiones). Documento 13: §8.3.
Diseño: `doc/diseno/H2b_Mandatos_Padron_y_Permisos.md`
del repositorio CEM, §2 (criterios 3, 7 y 9), §4, §7 (2b-2) y D1, D2 y D3.

- `nucleo.facultad`: catálogo de facultades de D2, común a todos los consorcios. `economica`
  marca las que exigirán segundo factor y sesión corta (2b-6). Se amplía con filas, sin migrar
  datos.
- `nucleo.paquete_facultad`: paquete inicial por tipo de mandato (D2). El acta puede achicarlo;
  ampliarlo queda para cuando haya quien registre esa ampliación (2b-3).
- `nucleo.mandato`: quién ejerce qué figura (D1), con su decisión de respaldo (criterio 9),
  vigencia y, si termina, quién lo terminó y por qué. `persona_id` es nula cuando el mandatario
  no tiene cuenta (p. ej. un administrador externo que no usa la aplicación); entonces el nombre
  es obligatorio. No se superponen dos mandatos del mismo tipo para la misma persona.
- `nucleo.facultad_otorgada`: cada facultad del mandato, acotada a su consorcio (clave compuesta)
  y, si tiene recurso, solo a ese recurso (criterio 7); guarda la decisión que la otorgó.
- `nucleo.decision_formal.sha256_acta`: hash opcional del archivo del acta (D3); se verificará
  contra el documento cuando exista el almacenamiento (2c).

`cem_app` solo **lee**. Escriben `nucleo.alta_mandato` y `nucleo.baja_mandato`, `security
definer`, que toman el consorcio del ámbito, validan, auditan y, en la baja, revocan las sesiones
del mandatario en la misma transacción (RN22). La baja nunca borra: cierra la vigencia y conserva
el mandato, sus facultades y la auditoría. Quién puede llamarlas lo deciden las políticas de
2b-3; ninguna ruta las usa todavía.

Revisión: 0011
Anterior: 0010
"""

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

# (código, área, económica) — D2.
FACULTADES = [
    ("padron.ver", "padron", False),
    ("padron.gestionar", "padron", False),
    ("padron.confirmar_vinculo", "padron", False),
    ("invitaciones.crear", "padron", False),
    ("decisiones.registrar", "mandatos", False),
    ("mandatos.registrar", "mandatos", False),
    ("fiscalizacion.ver_panel", "fiscalizacion", False),
    ("solicitud_formal.preparar", "fiscalizacion", False),
    ("solicitud_formal.firmar", "fiscalizacion", False),
    ("documentos.cargar", "documentos", False),
    ("documentos.ver", "documentos", False),
    ("liquidacion.preparar", "liquidacion", False),
    ("liquidacion.revisar", "liquidacion", False),
    ("liquidacion.aprobar", "liquidacion", True),
    ("egresos.preparar_orden", "egresos", True),
    ("egresos.aprobar_orden", "egresos", True),
    ("egresos.ejecutar_pago", "egresos", True),
    ("fondo_reserva.solicitar", "fondo_reserva", False),
    ("fondo_reserva.autorizar", "fondo_reserva", True),
    ("coeficientes.proponer", "coeficientes", False),
    ("coeficientes.aprobar", "coeficientes", True),
]

# Paquetes iniciales de D2.
PAQUETES = {
    "miembro_consejo": [
        "padron.ver",
        "invitaciones.crear",
        "fiscalizacion.ver_panel",
        "solicitud_formal.preparar",
        "solicitud_formal.firmar",
        "documentos.ver",
        "fondo_reserva.autorizar",
        "liquidacion.revisar",
    ],
    "administrador": [
        "padron.ver",
        "padron.gestionar",
        "padron.confirmar_vinculo",
        "invitaciones.crear",
        "documentos.cargar",
        "documentos.ver",
        "liquidacion.preparar",
        "egresos.preparar_orden",
        "egresos.ejecutar_pago",
        "fondo_reserva.solicitar",
        "coeficientes.proponer",
    ],
    "administrador_externo": ["documentos.cargar"],
}

REGISTRAR_DECISION_0010 = r"""
create function nucleo.registrar_decision(
  tipo_decision nucleo.tipo_decision, fecha_decision date, descripcion_decision text,
  referencia text, actor uuid, origen_pedido text)
  returns uuid
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    d uuid;
  begin
    if c is null then
      raise exception 'sin consorcio en el ámbito' using errcode = '42501';
    end if;
    insert into nucleo.decision_formal
      (consorcio_id, tipo, fecha, descripcion, referencia_acta, registrada_por, origen)
    values (c, tipo_decision, fecha_decision, btrim(descripcion_decision), btrim(referencia),
            actor, origen_pedido)
    returning id into d;
    insert into auditoria.eventos
      (consorcio_id, actor_persona_id, origen, accion, entidad, entidad_id, despues,
       hash_previo, hash)
    values (c, actor, origen_pedido, 'consorcio.decision.registrar', 'decision_formal', d,
            jsonb_build_object('tipo', tipo_decision, 'fecha', fecha_decision,
                               'referencia_acta', btrim(referencia)),
            '', '');
    return d;
  end
  $$;
"""

FIRMA_0010 = "nucleo.registrar_decision(nucleo.tipo_decision, date, text, text, uuid, text)"
FIRMA_0011 = "nucleo.registrar_decision(nucleo.tipo_decision, date, text, text, uuid, text, text)"
FIRMA_ALTA = (
    "nucleo.alta_mandato(nucleo.tipo_mandato, uuid, text, text, text, uuid, date, date,"
    " text[], uuid, text)"
)
FIRMA_BAJA = "nucleo.baja_mandato(uuid, text, uuid, text)"


def _catalogo() -> str:
    filas = ",\n  ".join(f"('{c}', '{a}', {str(e).lower()})" for c, a, e in FACULTADES)
    paquetes = ",\n  ".join(
        f"('{tipo}', '{f}')" for tipo, facultades in PAQUETES.items() for f in facultades
    )
    return (
        f"insert into nucleo.facultad (codigo, area, economica) values\n  {filas};\n"
        f"insert into nucleo.paquete_facultad (tipo, facultad) values\n  {paquetes};\n"
    )


UPGRADE = r"""
create type nucleo.tipo_mandato as enum
  ('administrador', 'miembro_consejo', 'administrador_externo');

create table nucleo.facultad (
  codigo     text primary key check (codigo ~ '^[a-z_]+\.[a-z_]+$'),
  area       text not null,
  economica  boolean not null
);

create table nucleo.paquete_facultad (
  tipo      nucleo.tipo_mandato not null,
  facultad  text not null references nucleo.facultad (codigo),
  primary key (tipo, facultad)
);

alter table nucleo.decision_formal
  add column sha256_acta text check (sha256_acta ~ '^[0-9a-f]{64}$');

create table nucleo.mandato (
  consorcio_id       uuid not null references nucleo.consorcio (id),
  id                 uuid not null default plataforma.uuid_v7(),
  tipo               nucleo.tipo_mandato not null,
  persona_id         uuid references nucleo.persona (id),
  mandatario_nombre  text check (length(btrim(mandatario_nombre)) between 1 and 200),
  mandatario_cuit    text check (mandatario_cuit ~ '^[0-9]{11}$'),
  -- Matrícula, inscripción en el RPA u otro registro profesional, si corresponde.
  matricula          text check (length(btrim(matricula)) between 1 and 100),
  decision_id        uuid not null,
  vigente_desde      date not null,
  vigente_hasta      date,
  registrado_por     uuid references nucleo.persona (id),
  origen             text not null check (origen in ('api', 'soporte')),
  creado_en          timestamptz not null default now(),
  finalizado_por     uuid references nucleo.persona (id),
  finalizado_en      timestamptz,
  motivo_fin         text check (length(btrim(motivo_fin)) between 1 and 500),
  primary key (id),
  unique (consorcio_id, id),
  foreign key (consorcio_id, decision_id) references nucleo.decision_formal (consorcio_id, id),
  check (persona_id is not null or mandatario_nombre is not null),
  check ((origen = 'api') = (registrado_por is not null)),
  -- Igual: un mandato dado de baja antes de empezar queda con vigencia vacía.
  check (vigente_hasta is null or vigente_hasta >= vigente_desde),
  check ((finalizado_en is null) = (motivo_fin is null)),
  exclude using gist (consorcio_id with =, tipo with =, persona_id with =,
                      daterange(vigente_desde, vigente_hasta) with &&)
);
create index mandato_persona on nucleo.mandato (persona_id, consorcio_id);

create table nucleo.facultad_otorgada (
  consorcio_id  uuid not null,
  id            uuid primary key default plataforma.uuid_v7(),
  mandato_id    uuid not null,
  facultad      text not null references nucleo.facultad (codigo),
  -- Una cuenta, un grupo de gasto u otro recurso; nula = todo el consorcio del mandato.
  recurso       text check (length(btrim(recurso)) between 1 and 200),
  decision_id   uuid not null,
  otorgada_en   timestamptz not null default now(),
  foreign key (consorcio_id, mandato_id) references nucleo.mandato (consorcio_id, id),
  foreign key (consorcio_id, decision_id) references nucleo.decision_formal (consorcio_id, id)
);
create unique index facultad_otorgada_unica
  on nucleo.facultad_otorgada (mandato_id, facultad, coalesce(recurso, ''));

alter table nucleo.mandato enable row level security;
alter table nucleo.mandato force row level security;
alter table nucleo.facultad_otorgada enable row level security;
alter table nucleo.facultad_otorgada force row level security;
create policy por_consorcio on nucleo.mandato for select
  using (consorcio_id = plataforma.consorcio_actual());
create policy por_consorcio on nucleo.facultad_otorgada for select
  using (consorcio_id = plataforma.consorcio_actual());

drop function nucleo.registrar_decision(nucleo.tipo_decision, date, text, text, uuid, text);
create function nucleo.registrar_decision(
  tipo_decision nucleo.tipo_decision, fecha_decision date, descripcion_decision text,
  referencia text, actor uuid, origen_pedido text, sha256 text default null)
  returns uuid
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    d uuid;
  begin
    if c is null then
      raise exception 'sin consorcio en el ámbito' using errcode = '42501';
    end if;
    insert into nucleo.decision_formal
      (consorcio_id, tipo, fecha, descripcion, referencia_acta, registrada_por, origen,
       sha256_acta)
    values (c, tipo_decision, fecha_decision, btrim(descripcion_decision), btrim(referencia),
            actor, origen_pedido, lower(sha256))
    returning id into d;
    insert into auditoria.eventos
      (consorcio_id, actor_persona_id, origen, accion, entidad, entidad_id, despues,
       hash_previo, hash)
    values (c, actor, origen_pedido, 'consorcio.decision.registrar', 'decision_formal', d,
            jsonb_strip_nulls(jsonb_build_object(
              'tipo', tipo_decision, 'fecha', fecha_decision,
              'referencia_acta', btrim(referencia), 'sha256_acta', lower(sha256))),
            '', '');
    return d;
  end
  $$;

-- `facultades` nula = el paquete completo del tipo; si viene, tiene que ser parte del paquete
-- (el acta lo achica, no lo amplía) y no puede estar vacía.
create function nucleo.alta_mandato(
  tipo_nuevo nucleo.tipo_mandato, persona uuid, nombre text, cuit text, matricula_nueva text,
  decision uuid, desde date, hasta date, facultades text[], actor uuid, origen_pedido text)
  returns uuid
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    paquete text[];
    elegidas text[];
    fuera text[];
    nuevo uuid;
  begin
    if c is null then
      raise exception 'sin consorcio en el ámbito' using errcode = '42501';
    end if;
    -- Criterio 9: sin decisión de respaldo de este consorcio no hay mandato.
    if decision is null or not exists (
         select 1 from nucleo.decision_formal d where d.consorcio_id = c and d.id = decision) then
      raise exception 'el mandato necesita una decisión de respaldo de este consorcio'
        using errcode = 'P0001', hint = 'decision_invalida';
    end if;
    if persona is not null and not exists (select 1 from nucleo.persona p where p.id = persona) then
      raise exception 'la persona no existe' using errcode = 'P0001', hint = 'persona_invalida';
    end if;

    select array_agg(p.facultad order by p.facultad) into paquete
      from nucleo.paquete_facultad p where p.tipo = tipo_nuevo;
    elegidas := coalesce(facultades, paquete);
    if cardinality(elegidas) = 0 then
      raise exception 'el mandato necesita al menos una facultad'
        using errcode = 'P0001', hint = 'sin_facultades';
    end if;
    select array_agg(f order by f) into fuera
      from unnest(elegidas) f where not f = any (paquete);
    if fuera is not null then
      raise exception 'facultades fuera del paquete de %: %',
        tipo_nuevo, array_to_string(fuera, ', ')
        using errcode = 'P0001', hint = 'facultad_fuera_de_paquete';
    end if;

    begin
      insert into nucleo.mandato
        (consorcio_id, tipo, persona_id, mandatario_nombre, mandatario_cuit, matricula,
         decision_id, vigente_desde, vigente_hasta, registrado_por, origen)
      values (c, tipo_nuevo, persona, btrim(nombre), cuit, btrim(matricula_nueva), decision,
              desde, hasta, actor, origen_pedido)
      returning id into nuevo;
    exception when exclusion_violation then
      raise exception 'ya hay un mandato de % para esa persona en esas fechas', tipo_nuevo
        using errcode = 'P0001', hint = 'mandato_superpuesto';
    end;

    insert into nucleo.facultad_otorgada (consorcio_id, mandato_id, facultad, decision_id)
    select c, nuevo, f, decision from (select distinct unnest(elegidas) f) x;

    insert into auditoria.eventos
      (consorcio_id, actor_persona_id, origen, accion, entidad, entidad_id, despues,
       hash_previo, hash)
    values (c, actor, origen_pedido, 'consorcio.mandato.alta', 'mandato', nuevo,
            jsonb_strip_nulls(jsonb_build_object(
              'tipo', tipo_nuevo, 'persona_id', persona, 'mandatario_nombre', btrim(nombre),
              'decision_id', decision, 'desde', desde, 'hasta', hasta,
              'facultades', (select to_jsonb(array_agg(distinct f order by f))
                               from unnest(elegidas) f))),
            '', '');
    return nuevo;
  end
  $$;

-- RN22: la baja corta la vigencia hoy (fecha del consorcio) y revoca en el acto las sesiones
-- del mandatario. Nada se borra. Devuelve cuántas sesiones revocó.
create function nucleo.baja_mandato(mandato uuid, motivo text, actor uuid, origen_pedido text)
  returns bigint
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    m nucleo.mandato;
    hoy date;
    revocadas bigint := 0;
  begin
    if c is null then
      raise exception 'sin consorcio en el ámbito' using errcode = '42501';
    end if;
    select * into m from nucleo.mandato x where x.consorcio_id = c and x.id = mandato for update;
    if m.id is null then
      raise exception 'el mandato no existe en este consorcio'
        using errcode = 'P0001', hint = 'mandato_invalido';
    end if;
    hoy := nucleo.hoy_del_consorcio(c);
    if m.finalizado_en is not null or (m.vigente_hasta is not null and m.vigente_hasta <= hoy) then
      raise exception 'el mandato ya terminó' using errcode = 'P0001', hint = 'mandato_terminado';
    end if;

    update nucleo.mandato
       set vigente_hasta = greatest(m.vigente_desde, hoy), finalizado_por = actor,
           finalizado_en = now(), motivo_fin = btrim(motivo)
     where id = m.id;
    if m.persona_id is not null then
      revocadas := nucleo.revocar_sesiones_de(m.persona_id);
    end if;

    insert into auditoria.eventos
      (consorcio_id, actor_persona_id, origen, accion, entidad, entidad_id, antes, despues,
       hash_previo, hash)
    values (c, actor, origen_pedido, 'consorcio.mandato.baja', 'mandato', m.id,
            jsonb_build_object('vigente_hasta', m.vigente_hasta),
            jsonb_build_object('vigente_hasta', greatest(m.vigente_desde, hoy),
                               'motivo', btrim(motivo), 'sesiones_revocadas', revocadas),
            '', '');
    return revocadas;
  end
  $$;
"""

PRIVILEGIOS = f"""
grant select on nucleo.facultad, nucleo.paquete_facultad, nucleo.mandato,
  nucleo.facultad_otorgada to cem_app;
revoke all on function {FIRMA_0011}, {FIRMA_ALTA}, {FIRMA_BAJA} from public;
grant execute on function {FIRMA_0011}, {FIRMA_ALTA}, {FIRMA_BAJA} to cem_app;
"""

DOWNGRADE = f"""
drop function {FIRMA_BAJA};
drop function {FIRMA_ALTA};
drop function {FIRMA_0011};
{REGISTRAR_DECISION_0010}
revoke all on function {FIRMA_0010} from public;
grant execute on function {FIRMA_0010} to cem_app;
drop table nucleo.facultad_otorgada;
drop table nucleo.mandato;
alter table nucleo.decision_formal drop column sha256_acta;
drop table nucleo.paquete_facultad;
drop table nucleo.facultad;
drop type nucleo.tipo_mandato;
"""


def upgrade() -> None:
    op.execute(UPGRADE + _catalogo() + PRIVILEGIOS)


def downgrade() -> None:
    op.execute(DOWNGRADE)
