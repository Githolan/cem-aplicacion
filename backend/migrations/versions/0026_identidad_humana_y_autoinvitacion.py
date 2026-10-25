"""2b-6a: identidad humana por consorcio (A13-C) y nadie acepta su propia invitación (F9).

Diseño: decisiones del propietario del 24-10-2026 sobre 2b-6 (repositorio CEM,
`doc/diseno/H2b_6_Diseno.md`). Reemplazan las recomendaciones del borrador donde difieren.

**A13-C.** Una cuenta (`nucleo.persona`) controla un correo; no prueba quién es el ser humano.
Las separaciones que exigen personas distintas tienen que distinguir seres humanos:

- `nucleo.identidad_humana`: una por ser humano **y por consorcio**, con la huella HMAC del tipo
  y el número de su documento (acotada al consorcio: la misma persona en dos consorcios tiene
  dos huellas sin relación) y la versión de la clave. El número en claro nunca llega a la base:
  la huella la calcula `scripts/identidad_humana.py` con `CEM_IDENTIDAD_CLAVES`. `cem_app` no
  puede leer esta tabla; solo la tocan las funciones de soporte.
- `nucleo.identidad_persona`: qué cuentas son de qué identidad en el consorcio (una identidad
  puede tener varias cuentas; una cuenta, a lo sumo una identidad activa por consorcio). Sin
  huella: `cem_app` la lee, por consorcio, para que el evaluador compare identidades.
- `nucleo.verificacion_identidad`: cada acto de soporte (verificar, vincular, desvincular,
  corregir) con su método, la referencia del respaldo, el motivo y quién lo hizo. Sin imagen ni
  número del documento. `cem_app` no la lee.
- Solo soporte verifica, vincula, desvincula o corrige (decisión del propietario: la delegación
  al órgano queda para más adelante). Las cuatro funciones exigen origen `soporte` sin persona en
  la sesión. Ninguna une identidades por nombre, correo ni ningún otro dato: una huella que
  coincide no vincula sola, solo hace fallar `verificar` y habilita `vincular`, que soporte pide
  de forma explícita.
- `nucleo.exigir_separacion_humana(c, actor, otra, motivo)`: la segunda barrera de las reglas
  que exigen seres humanos distintos. Niega con `motivo` si son la misma cuenta o la misma
  identidad, y con `identidad_no_verificada` si a cualquiera de los dos le falta la identidad
  verificada en el consorcio (A5: si no se puede determinar, se niega; el propietario descartó
  «solo endurece»). Se agrega, por la API, a A4 y A15 de `alta_mandato` y
  `resolver_revision_mandato`, a A15 de `baja_mandato` y a A7 de `aprobar_invitacion_mandato`.
  Las comparaciones por `persona_id` siguen donde estaban.
- **No cambian en este corte** (frenados y presentados al propietario): P4-c de
  `confirmar_vinculo` (la otra parte es cualquier propietario, no un mandatario) y A7 y P1-d en
  `aceptar_invitacion_mandato` (quien acepta suele ser una cuenta que nace al aceptar y no se
  puede verificar antes), y A4 de `baja_mandato` y de `desasociar_persona_de_mandato` (con la
  regla fuerte, una cuenta mal asociada que nunca se verifica no se podría sacar por la API: la
  regla bloquearía el remedio). Siguen comparando `persona_id`.

**F9.** Quien invitó no acepta su propia invitación de vínculo, ni con la misma cuenta ni con
otra de la misma identidad humana verificada en el consorcio (`aceptar_invitacion`, que también
usa `aceptar_invitacion_propia`): responde `invitacion_propia`. Vale para las manuales y las
importadas (las dos tienen `invitada_por`); las del equipo no tienen quien invitó y no cambian.
`mis_invitaciones` ya no lista las propias. Para las ya aceptadas, la condición habilitante del
Consejo (`cobertura_consejo`, 0023) deja de contar una invitación aceptada por quien la creó: una
autoinvitación no sostiene la continuidad del mandato.

Cada función se cambia sobre su definición vigente (`pg_get_functiondef`) con reemplazos que
tienen que aparecer exactamente una vez: si el texto no es el esperado, la migración falla.

Downgrade: se niega (`downgrade_con_datos`) si hay alguna identidad o verificación; si no,
deshace los reemplazos y quita las tablas y las funciones nuevas.

Revisión: 0026
Anterior: 0025
"""

from alembic import op
from sqlalchemy import text

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None

VERIFICAR = "nucleo.verificar_identidad_humana(uuid, smallint[], bytea[], text, text, text)"
VINCULAR = "nucleo.vincular_identidad_humana(uuid, smallint[], bytea[], text, text, text)"
DESVINCULAR = "nucleo.desvincular_identidad_humana(uuid, text, text, text)"
CORREGIR = "nucleo.corregir_identidad_humana(uuid, smallint[], bytea[], text, text, text, text)"
SOPORTE = (VERIFICAR, VINCULAR, DESVINCULAR, CORREGIR)
IDENTIDAD_DE = "nucleo.identidad_humana_de(uuid, uuid)"
MISMA = "nucleo.misma_persona_humana(uuid, uuid, uuid)"
EXIGIR = "nucleo.exigir_separacion_humana(uuid, uuid, uuid, text)"
INTERNAS = (IDENTIDAD_DE, MISMA, EXIGIR)

ALTA = (
    "nucleo.alta_mandato(nucleo.tipo_mandato, uuid, text, text, text, uuid, date, date, text[],"
    " uuid, text)"
)
BAJA = "nucleo.baja_mandato(uuid, nucleo.causa_baja, uuid, text, text, uuid, text)"
REVISION = "nucleo.resolver_revision_mandato(uuid, uuid, date, uuid, text)"
APROBAR = "nucleo.aprobar_invitacion_mandato(uuid, bytea, timestamptz, uuid, text)"
ACEPTAR = "nucleo.aceptar_invitacion(bytea, uuid, text, bytea, uuid, text, inet, interval)"
MIS_INVITACIONES = "nucleo.mis_invitaciones()"
COBERTURA = "nucleo.cobertura_consejo(uuid, date, date)"

TABLAS = """
create table nucleo.identidad_humana (
  consorcio_id   uuid not null references nucleo.consorcio (id),
  id             uuid not null default plataforma.uuid_v7(),
  -- HMAC-SHA256 del documento canónico, con la clave `clave_version` y acotada al consorcio.
  huella         bytea not null check (length(huella) = 32),
  clave_version  smallint not null check (clave_version > 0),
  creada_en      timestamptz not null default now(),
  primary key (id),
  unique (consorcio_id, id),
  -- El mismo documento, con la misma clave, no da dos identidades en el consorcio.
  unique (consorcio_id, clave_version, huella)
);
alter table nucleo.identidad_humana enable row level security;
alter table nucleo.identidad_humana force row level security;
create policy por_consorcio on nucleo.identidad_humana
  using (consorcio_id = plataforma.consorcio_actual())
  with check (consorcio_id = plataforma.consorcio_actual());

create table nucleo.identidad_persona (
  consorcio_id     uuid not null,
  persona_id       uuid not null references nucleo.persona (id),
  identidad_id     uuid not null,
  vinculada_en     timestamptz not null default now(),
  desvinculada_en  timestamptz,
  primary key (consorcio_id, persona_id, vinculada_en),
  foreign key (consorcio_id, identidad_id) references nucleo.identidad_humana (consorcio_id, id),
  check (desvinculada_en is null or desvinculada_en >= vinculada_en)
);
-- Una cuenta, a lo sumo una identidad activa por consorcio.
create unique index identidad_persona_activa on nucleo.identidad_persona (consorcio_id, persona_id)
  where desvinculada_en is null;
create index identidad_persona_por_identidad
  on nucleo.identidad_persona (consorcio_id, identidad_id)
  where desvinculada_en is null;
alter table nucleo.identidad_persona enable row level security;
alter table nucleo.identidad_persona force row level security;
create policy por_consorcio on nucleo.identidad_persona
  using (consorcio_id = plataforma.consorcio_actual())
  with check (consorcio_id = plataforma.consorcio_actual());

create table nucleo.verificacion_identidad (
  consorcio_id   uuid not null,
  id             uuid not null default plataforma.uuid_v7(),
  identidad_id   uuid not null,
  persona_id     uuid not null references nucleo.persona (id),
  accion         text not null
                 check (accion in ('verificar', 'vincular', 'desvincular', 'corregir')),
  -- Cómo vio soporte el documento; nulo solo al desvincular, que no mira ningún documento.
  metodo         text check (metodo in ('documento_presencial', 'documento_por_videollamada')),
  -- Dónde está el respaldo (acta, nota, constancia). Nunca el número del documento: el script
  -- lo rechaza si lo contiene.
  referencia     text not null check (length(btrim(referencia)) between 1 and 300),
  motivo         text check (motivo is null or length(btrim(motivo)) between 1 and 300),
  -- Quién del equipo lo hizo (el origen es siempre `soporte`, sin persona en la sesión).
  operador       text not null check (length(btrim(operador)) between 1 and 120),
  clave_version  smallint check (clave_version > 0),
  realizada_en   timestamptz not null default now(),
  primary key (id),
  foreign key (consorcio_id, identidad_id) references nucleo.identidad_humana (consorcio_id, id),
  check ((accion = 'desvincular') = (metodo is null)),
  check ((accion in ('verificar', 'corregir')) = (clave_version is not null)),
  check (accion not in ('desvincular', 'corregir') or motivo is not null)
);
alter table nucleo.verificacion_identidad enable row level security;
alter table nucleo.verificacion_identidad force row level security;
create policy por_consorcio on nucleo.verificacion_identidad
  using (consorcio_id = plataforma.consorcio_actual())
  with check (consorcio_id = plataforma.consorcio_actual());

-- `cem_app` solo lee qué cuenta es de qué identidad: nunca la huella ni las verificaciones.
grant select on nucleo.identidad_persona to cem_app;
"""

FUNCIONES = f"""
create function nucleo.identidad_humana_de(c uuid, p uuid)
  returns uuid
  language sql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
    select ip.identidad_id from nucleo.identidad_persona ip
     where ip.consorcio_id = c and ip.persona_id = p and ip.desvinculada_en is null
  $$;

-- La misma cuenta, o dos cuentas de la misma identidad verificada en el consorcio.
create function nucleo.misma_persona_humana(c uuid, a uuid, b uuid)
  returns boolean
  language sql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
    select a = b
        or coalesce(nucleo.identidad_humana_de(c, a) = nucleo.identidad_humana_de(c, b), false)
  $$;

-- Segunda barrera de A13-C: dos seres humanos distintos y verificados en el consorcio. Bloquea
-- los vínculos de las dos cuentas para que soporte no los cambie a mitad del acto.
create function nucleo.exigir_separacion_humana(c uuid, actor uuid, otra uuid, motivo_misma text)
  returns void
  language plpgsql security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    del_actor uuid;
    de_la_otra uuid;
  begin
    perform 1 from nucleo.identidad_persona ip
     where ip.consorcio_id = c and ip.persona_id in (actor, otra) and ip.desvinculada_en is null
       for share;
    del_actor := nucleo.identidad_humana_de(c, actor);
    de_la_otra := nucleo.identidad_humana_de(c, otra);
    if actor = otra or del_actor = de_la_otra then
      raise exception 'la misma persona no puede ocupar los dos lugares de esta separación'
        using errcode = 'P0001', hint = motivo_misma;
    end if;
    if del_actor is null or de_la_otra is null then
      raise exception 'la separación exige la identidad humana verificada de las dos personas'
                      ' en este consorcio'
        using errcode = 'P0001', hint = 'identidad_no_verificada';
    end if;
  end
  $$;

-- Las cuatro funciones de soporte: origen `soporte` y ninguna persona en la sesión.
create function nucleo.exigir_soporte()
  returns uuid
  language plpgsql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
  begin
    if c is null then
      raise exception 'sin consorcio en el ámbito' using errcode = '42501';
    end if;
    if plataforma.persona_actual() is not null then
      raise exception 'la identidad humana la maneja solo soporte, sin persona en la sesión'
        using errcode = 'P0001', hint = 'origen_invalido';
    end if;
    return c;
  end
  $$;

-- Una huella por cada versión de la clave que tiene el script: así un documento verificado con
-- una clave anterior se sigue reconociendo. La primera es la de la versión vigente.
create function nucleo.identidad_por_huellas(c uuid, versiones smallint[], huellas bytea[])
  returns uuid
  language plpgsql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    encontrada uuid;
  begin
    if versiones is null or huellas is null or cardinality(versiones) = 0
       or cardinality(versiones) <> cardinality(huellas)
       or array_position(versiones, null) is not null
       or array_position(huellas, null) is not null then
      raise exception 'faltan las huellas del documento' using errcode = 'P0001',
        hint = 'huellas_invalidas';
    end if;
    select h.id into encontrada from nucleo.identidad_humana h
      join unnest(versiones, huellas) as x(version, huella)
        on x.version = h.clave_version and x.huella = h.huella
     where h.consorcio_id = c
     limit 1;
    return encontrada;
  end
  $$;

create function nucleo.auditar_identidad(c uuid, accion text, identidad uuid, persona uuid,
                                         metodo text)
  returns void
  language sql security definer
  set search_path = pg_catalog, pg_temp
  as $$
    insert into auditoria.eventos
      (consorcio_id, actor_persona_id, origen, accion, entidad, entidad_id, despues,
       hash_previo, hash)
    -- Sin huella, referencia, motivo ni operador: quedan en `verificacion_identidad`.
    values (c, null, 'soporte', 'identidad.humana.' || accion, 'identidad_humana', identidad,
            jsonb_strip_nulls(jsonb_build_object('persona_id', persona, 'metodo', metodo)),
            '', '');
  $$;

create function nucleo.verificar_identidad_humana(
  persona uuid, versiones smallint[], huellas bytea[], metodo text, referencia text, operador text)
  returns uuid
  language plpgsql security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := nucleo.exigir_soporte();
    nueva uuid;
  begin
    if not exists (select 1 from nucleo.persona p where p.id = persona and p.eliminado_en is null)
    then
      raise exception 'la persona no existe' using errcode = 'P0001', hint = 'persona_invalida';
    end if;
    perform pg_advisory_xact_lock(hashtextextended('identidad:' || c, 0));
    if nucleo.identidad_humana_de(c, persona) is not null then
      raise exception 'la cuenta ya tiene una identidad verificada en este consorcio'
        using errcode = 'P0001', hint = 'cuenta_ya_verificada';
    end if;
    if nucleo.identidad_por_huellas(c, versiones, huellas) is not null then
      raise exception 'ese documento ya es de una identidad de este consorcio: si es la misma'
                      ' persona, se vincula'
        using errcode = 'P0001', hint = 'documento_ya_verificado';
    end if;
    insert into nucleo.identidad_humana (consorcio_id, huella, clave_version)
    values (c, huellas[1], versiones[1])
    returning id into nueva;
    insert into nucleo.identidad_persona (consorcio_id, persona_id, identidad_id)
    values (c, persona, nueva);
    insert into nucleo.verificacion_identidad
      (consorcio_id, identidad_id, persona_id, accion, metodo, referencia, operador,
       clave_version)
    values (c, nueva, persona, 'verificar', metodo, btrim(referencia), btrim(operador),
            versiones[1]);
    perform nucleo.auditar_identidad(c, 'verificar', nueva, persona, metodo);
    return nueva;
  end
  $$;

-- Otra cuenta del mismo ser humano: soporte vuelve a ver el documento y la huella tiene que
-- coincidir con una identidad que ya existe. Nunca se vincula por nombre ni por correo.
create function nucleo.vincular_identidad_humana(
  persona uuid, versiones smallint[], huellas bytea[], metodo text, referencia text, operador text)
  returns uuid
  language plpgsql security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := nucleo.exigir_soporte();
    existente uuid;
  begin
    if not exists (select 1 from nucleo.persona p where p.id = persona and p.eliminado_en is null)
    then
      raise exception 'la persona no existe' using errcode = 'P0001', hint = 'persona_invalida';
    end if;
    perform pg_advisory_xact_lock(hashtextextended('identidad:' || c, 0));
    if nucleo.identidad_humana_de(c, persona) is not null then
      raise exception 'la cuenta ya tiene una identidad verificada en este consorcio'
        using errcode = 'P0001', hint = 'cuenta_ya_verificada';
    end if;
    existente := nucleo.identidad_por_huellas(c, versiones, huellas);
    if existente is null then
      raise exception 'ese documento no es de ninguna identidad de este consorcio: se verifica'
        using errcode = 'P0001', hint = 'documento_sin_identidad';
    end if;
    insert into nucleo.identidad_persona (consorcio_id, persona_id, identidad_id)
    values (c, persona, existente);
    insert into nucleo.verificacion_identidad
      (consorcio_id, identidad_id, persona_id, accion, metodo, referencia, operador)
    values (c, existente, persona, 'vincular', metodo, btrim(referencia), btrim(operador));
    perform nucleo.auditar_identidad(c, 'vincular', existente, persona, metodo);
    return existente;
  end
  $$;

create function nucleo.desvincular_identidad_humana(
  persona uuid, referencia text, motivo text, operador text)
  returns uuid
  language plpgsql security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := nucleo.exigir_soporte();
    actual uuid;
  begin
    perform pg_advisory_xact_lock(hashtextextended('identidad:' || c, 0));
    update nucleo.identidad_persona ip set desvinculada_en = now()
     where ip.consorcio_id = c and ip.persona_id = persona and ip.desvinculada_en is null
    returning ip.identidad_id into actual;
    if actual is null then
      raise exception 'la cuenta no tiene una identidad verificada en este consorcio'
        using errcode = 'P0001', hint = 'cuenta_sin_identidad';
    end if;
    insert into nucleo.verificacion_identidad
      (consorcio_id, identidad_id, persona_id, accion, referencia, motivo, operador)
    values (c, actual, persona, 'desvincular', btrim(referencia), btrim(motivo), btrim(operador));
    perform nucleo.auditar_identidad(c, 'desvincular', actual, persona, null);
    return actual;
  end
  $$;

-- Un número mal cargado, o la huella con una clave nueva: reemplaza la huella de la identidad
-- de la cuenta. El documento nuevo no puede ser de otra identidad del consorcio.
create function nucleo.corregir_identidad_humana(
  persona uuid, versiones smallint[], huellas bytea[], metodo text, referencia text, motivo text,
  operador text)
  returns uuid
  language plpgsql security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := nucleo.exigir_soporte();
    actual uuid;
    otra uuid;
  begin
    perform pg_advisory_xact_lock(hashtextextended('identidad:' || c, 0));
    actual := nucleo.identidad_humana_de(c, persona);
    if actual is null then
      raise exception 'la cuenta no tiene una identidad verificada en este consorcio'
        using errcode = 'P0001', hint = 'cuenta_sin_identidad';
    end if;
    otra := nucleo.identidad_por_huellas(c, versiones, huellas);
    if otra is not null and otra <> actual then
      raise exception 'ese documento ya es de otra identidad de este consorcio'
        using errcode = 'P0001', hint = 'documento_ya_verificado';
    end if;
    update nucleo.identidad_humana set huella = huellas[1], clave_version = versiones[1]
     where consorcio_id = c and id = actual;
    insert into nucleo.verificacion_identidad
      (consorcio_id, identidad_id, persona_id, accion, metodo, referencia, motivo, operador,
       clave_version)
    values (c, actual, persona, 'corregir', metodo, btrim(referencia), btrim(motivo),
            btrim(operador), versiones[1]);
    perform nucleo.auditar_identidad(c, 'corregir', actual, persona, metodo);
    return actual;
  end
  $$;

revoke all on function {", ".join(INTERNAS)}, nucleo.exigir_soporte(),
  nucleo.identidad_por_huellas(uuid, smallint[], bytea[]),
  nucleo.auditar_identidad(uuid, text, uuid, uuid, text), {", ".join(SOPORTE)} from public;
grant execute on function {", ".join(SOPORTE)} to cem_app;
"""

SEPARACION = "    -- A13-C (0026): también entre seres humanos, verificados en este consorcio.\n"
CAMBIOS = {
    ALTA: [
        (
            "        using errcode = 'P0001', hint = 'alta_de_si_mismo';\n    end if;\n",
            "        using errcode = 'P0001', hint = 'alta_de_si_mismo';\n    end if;\n"
            + SEPARACION
            + "    if origen_pedido = 'api' and persona is not null then\n"
            "      perform nucleo.exigir_separacion_humana(\n"
            "                c, actor, persona, 'alta_de_si_mismo');\n"
            "    end if;\n",
        ),
        (
            "    if origen_pedido = 'api' and dec.registrada_por = actor then\n"
            "      raise exception 'quien registró la decisión no la usa para dar de alta'\n"
            "        using errcode = 'P0001', hint = 'decision_registrada_por_el_actor';\n"
            "    end if;\n",
            "    if origen_pedido = 'api' and dec.registrada_por = actor then\n"
            "      raise exception 'quien registró la decisión no la usa para dar de alta'\n"
            "        using errcode = 'P0001', hint = 'decision_registrada_por_el_actor';\n"
            "    end if;\n"
            + SEPARACION
            + "    if origen_pedido = 'api' and dec.registrada_por is not null then\n"
            "      perform nucleo.exigir_separacion_humana(\n"
            "        c, actor, dec.registrada_por, 'decision_registrada_por_el_actor');\n"
            "    end if;\n",
        ),
    ],
    BAJA: [
        (
            "        raise exception 'quien registró la decisión no la usa para remover'\n"
            "          using errcode = 'P0001', hint = 'decision_registrada_por_el_actor';\n"
            "      end if;\n",
            "        raise exception 'quien registró la decisión no la usa para remover'\n"
            "          using errcode = 'P0001', hint = 'decision_registrada_por_el_actor';\n"
            "      end if;\n"
            "      -- A13-C (0026): también entre seres humanos, verificados en este consorcio.\n"
            "      if origen_pedido = 'api' then\n"
            "        perform nucleo.exigir_separacion_humana(\n"
            "                  c, actor, d.registrada_por, 'decision_registrada_por_el_actor')\n"
            "           from nucleo.decision_formal d\n"
            "          where d.consorcio_id = c and d.id = decision\n"
            "            and d.registrada_por is not null;\n"
            "      end if;\n",
        ),
    ],
    REVISION: [
        (
            "        using errcode = 'P0001', hint = 'revision_de_si_mismo';\n    end if;\n",
            "        using errcode = 'P0001', hint = 'revision_de_si_mismo';\n    end if;\n"
            + SEPARACION
            + "    if origen_pedido = 'api' then\n"
            "      perform nucleo.exigir_separacion_humana(c, actor, m.persona_id,"
            " 'revision_de_si_mismo');\n"
            "    end if;\n",
        ),
        (
            "      raise exception 'quien registró la decisión no la usa para revisar'\n"
            "        using errcode = 'P0001', hint = 'decision_registrada_por_el_actor';\n"
            "    end if;\n",
            "      raise exception 'quien registró la decisión no la usa para revisar'\n"
            "        using errcode = 'P0001', hint = 'decision_registrada_por_el_actor';\n"
            "    end if;\n"
            + SEPARACION
            + "    if origen_pedido = 'api' and d.registrada_por is not null then\n"
            "      perform nucleo.exigir_separacion_humana(\n"
            "        c, actor, d.registrada_por, 'decision_registrada_por_el_actor');\n"
            "    end if;\n",
        ),
    ],
    APROBAR: [
        (
            "        using errcode = 'P0001', hint = 'aprobacion_de_si_mismo';\n    end if;\n",
            "        using errcode = 'P0001', hint = 'aprobacion_de_si_mismo';\n    end if;\n"
            + SEPARACION
            + "    if i.emitida_por is not null then\n"
            "      perform nucleo.exigir_separacion_humana(c, actor, i.emitida_por,"
            " 'aprobacion_de_si_mismo');\n"
            "    end if;\n",
        ),
    ],
    ACEPTAR: [
        (
            "\n    select (now() at time zone c.zona_horaria)::date into hoy\n",
            "\n    -- F9 (0026): quien invitó no acepta su propia invitación, ni con otra\n"
            "    -- cuenta de la\n"
            "    -- misma identidad humana verificada en el consorcio. Las del equipo no\n"
            "    -- tienen quien invitó. Una cuenta que nace al aceptar nunca es la de\n"
            "    -- quien invitó.\n"
            "    if i.invitada_por is not null\n"
            "       and nucleo.misma_persona_humana(i.consorcio_id, i.invitada_por, p) then\n"
            "      return query select 'invitacion_propia'::text, null::uuid, null::uuid,\n"
            "                          null::boolean, null::uuid, null::timestamptz;\n"
            "      return;\n"
            "    end if;\n"
            "\n    select (now() at time zone c.zona_horaria)::date into hoy\n",
        ),
    ],
    MIS_INVITACIONES: [
        (
            "       and nucleo.motivo_invitacion_invalida(i) is null\n",
            "       and nucleo.motivo_invitacion_invalida(i) is null\n"
            "       -- F9 (0026): las propias no se pueden aceptar.\n"
            "       and (i.invitada_por is null\n"
            "            or not nucleo.misma_persona_humana(\n"
            "                     i.consorcio_id, i.invitada_por, yo.id))\n",
        ),
    ],
    COBERTURA: [
        (
            "            case when v.fuente = 'invitacion'\n"
            "                 then (v.creado_en at time zone co.zona_horaria)::date end,\n",
            "            -- F9 (0026): una invitación aceptada por quien la creó no habilita.\n"
            "            case when v.fuente = 'invitacion'\n"
            "                      and (v.registrado_por is null\n"
            "                           or not nucleo.misma_persona_humana(\n"
            "                             v.consorcio_id, v.registrado_por, v.persona_id))\n"
            "                 then (v.creado_en at time zone co.zona_horaria)::date end,\n",
        ),
        (
            "                and i.aceptada_en is not null)) as habilita) a\n",
            "                and i.aceptada_en is not null\n"
            "                and (i.invitada_por is null or not nucleo.misma_persona_humana(\n"
            "                       i.consorcio_id, i.invitada_por, v.persona_id))))\n"
            "         as habilita) a\n",
        ),
    ],
}


def _reemplazar(firma: str, pares: list[tuple[str, str]]) -> None:
    """Aplica los reemplazos sobre la definición vigente. Cada texto viejo, exactamente una vez."""
    conn = op.get_bind()
    definicion = conn.execute(
        text("select pg_get_functiondef(cast(:f as regprocedure))"), {"f": firma}
    ).scalar_one()
    for viejo, nuevo in pares:
        if definicion.count(viejo) != 1:
            raise RuntimeError(f"0026: {firma} no tiene exactamente una vez: {viejo!r}")
        definicion = definicion.replace(viejo, nuevo)
    op.execute(definicion)


def upgrade() -> None:
    op.execute(TABLAS)
    op.execute(FUNCIONES)
    for firma, pares in CAMBIOS.items():
        _reemplazar(firma, pares)


def downgrade() -> None:
    op.execute(
        """
do $$
begin
  if exists (select 1 from nucleo.identidad_humana)
     or exists (select 1 from nucleo.verificacion_identidad) then
    raise exception 'el downgrade de 0026 se niega: hay identidades humanas verificadas'
      using errcode = 'P0001', hint = 'downgrade_con_datos',
            detail = 'cada verificación es constancia de un acto de soporte';
  end if;
end
$$;
"""
    )
    for firma, pares in CAMBIOS.items():
        _reemplazar(firma, [(nuevo, viejo) for viejo, nuevo in pares])
    for f in SOPORTE + INTERNAS:
        op.execute(f"drop function {f};")
    op.execute(
        "drop function nucleo.exigir_soporte();\n"
        "drop function nucleo.identidad_por_huellas(uuid, smallint[], bytea[]);\n"
        "drop function nucleo.auditar_identidad(uuid, text, uuid, uuid, text);\n"
        "drop table nucleo.verificacion_identidad;\n"
        "drop table nucleo.identidad_persona;\n"
        "drop table nucleo.identidad_humana;\n"
    )
