"""Datos y reglas que pidió la revisión de fricción por flujos (18-11-2026, tanda 8), con las
respuestas del propietario del mismo día (la opción recomendada en cada pregunta).

Reglas nuevas:

- **Señalar un contacto con un dato mal (pregunta 16, FL-CTO-01).** `nucleo.senalar_contacto` deja
  de pedir `contactos.gestionar`: alcanza el ámbito con que se leen los contactos
  (`contactos.ver`, `en_ambito`), sobre un contacto que esa persona ve hoy. Quien gestiona sigue
  señalando también los futuros. El motivo sigue sin teléfonos ni correos y la auditoría es la
  misma (`consorcio.contacto.senalar`).
- **El Consejo también confirma vínculos (pregunta 7, FL-PAD-02).** `padron.confirmar_vinculo`
  entra al paquete de `miembro_consejo`. Siguen las barreras de la confirmación: nadie confirma su
  propio vínculo ni el que registró (invitó), y hace falta el título (`0014`). Como en `0028` y
  `0031`, los mandatos que ya existen conservan lo que les otorgó su decisión: la reciben los que
  se den de alta desde ahora (un acta reduce un paquete, nunca lo amplía).
- **Respuesta de una solicitud vencida que llegó por otro medio (pregunta 15, FL-SOL-03).**
  `registrar_respuesta_solicitud_formal` deja registrarla al Consejo también cuando el
  destinatario tiene cuenta, si la solicitud ya venció. Mientras corre el plazo, la da él en la
  aplicación, como hasta ahora (P2).
- **Quitar un rubro legal con faltantes pide motivo (pregunta 4, FL-LEG-10).**
  `asignar_rubro_legal_proveedor` suma el parámetro `motivo`: retirar un rubro al que le falta
  documentación exige el motivo (`motivo_requerido`), que queda en
  `nucleo.rubro_legal_retiro_motivo` con lo que faltaba, sin cambiar ni borrarse. Lo leen el
  Consejo y quien ve los proveedores (`retiros_rubro_con_motivo_lectura`). Asignar no lleva motivo.
- **Pedido de salteo (pregunta 3, FL-SAL-01, FL-SAL-02).** `nucleo.pedido_salteo_documentacion`:
  quien prepara órdenes pide al Consejo un salteo para un proveedor bloqueado, con el motivo y el
  importe. No se cambia ni se borra. Queda atendido cuando se registra un salteo de ese proveedor
  después del pedido; no se pide otro mientras uno espera, ni si ya hay un salteo que le sirve.
  `pedidos_salteo_lectura` lo leen quien saltea y quien prepara, con su estado (`pendiente`,
  `atendido`, `sin_bloqueo`).
- **El acta de una decisión que quedó sin ella (FL-DEC-01, pedido de la tanda 4).**
  `nucleo.adjuntar_acta_decision` ata a una decisión sin acta la huella de una versión
  `disponible` de un documento vigente del consorcio (`acta_no_disponible` si no lo es), una sola
  vez (`acta_ya_registrada`): es la única escritura sobre `decision_formal` después del alta, de
  nulo a un valor, y no reescribe nada. Queda en la auditoría como
  `consorcio.decision.adjuntar_acta` (con su proyección), salvo por soporte (S2b, `0043`).

Lecturas nuevas (no cambian ninguna regla; cada una pasa por los controles de la lectura que
amplía o la llama):

- `razon_social_por_aclarar` (FL-ACL-03, pregunta 17): la razón social de cada pago para aclarar.
- `ordenes_sin_cuenta_lectura` (FL-ORD-06): las órdenes cuyo proveedor, en la versión fijada, no
  tiene cuenta destino: la misma comparación con que `exigir_orden_emitible` niega
  `proveedor_sin_cuenta`.
- `candidatos_a_mandato_datos` (pregunta 8, FL-MAN-02, FL-MAN-03): de cada candidato, si cumple hoy
  la condición para integrar el Consejo con un vínculo confirmado y qué mandatos tiene vigentes o
  futuros.
- `vinculos_que_sostienen_consejo` (FL-PAD-03): los vínculos del padrón que hoy sostienen un
  mandato vigente del Consejo.
- `vinculos_de_invitaciones` (FL-INV-01): el vínculo que dejó cada invitación aceptada.
- `invitacion_aceptada_por` (FL-INVA-02): la invitación que ya aceptó la cuenta de la sesión.
- `mis_invitaciones` (FL-INVA-03) deja de listar la importada cuyo vínculo ya no le corresponde
  (`vinculo_no_corresponde` al aceptar).
- `autoriza_por_persona_lectura` (FL-VER-01) y `ven_proveedores_por_rol_lectura` (FL-VER-02).
- `solicitudes_formales_lectura` (FL-SOL-04) suma el estado del archivo adjunto al envío y a la
  respuesta.
- `grupos_del_periodo_lectura` (FL-LIQ-01): los grupos con porcentajes para un período, antes de
  abrir el borrador, con los controles de `grupos_liquidables_lectura`.

Cambiar el cuerpo de una función existente se hace sobre lo que hay en la base
(`pg_get_functiondef`), con reemplazos que tienen que aparecer exactamente una vez; el downgrade
aplica los mismos reemplazos al revés, así que deja cada función como estaba, letra por letra.

Downgrade: se niega (`downgrade_con_datos`) si hay pedidos de salteo, retiros de rubro con
motivo o un mandato del Consejo con `padron.confirmar_vinculo` otorgada. Si no, todo vuelve a
`0109`. Un acta ya adjuntada queda en su decisión (es un dato válido en `0109`) y su evento queda
en la cadena; sin su proyección, la auditoría propia no lo lista (filtra por proyección).

Revisión: 0110
Anterior: 0109
"""

from alembic import op
from sqlalchemy import text

revision = "0110"
down_revision = "0109"
branch_labels = None
depends_on = None

MOTIVO_MAXIMO = 500


def _definicion(firma: str) -> str:
    return (
        op.get_bind()
        .execute(text("select pg_get_functiondef(to_regprocedure(:f))"), {"f": firma})
        .scalar_one()
    )


def _reemplazar(definicion: str, cambios, al_reves: bool, firma: str) -> str:
    for antes, despues in cambios:
        if al_reves:
            antes, despues = despues, antes
        if definicion.count(antes) != 1:
            raise RuntimeError(f"0110: {firma} no tiene exactamente una vez {antes!r}")
        definicion = definicion.replace(antes, despues)
    return definicion


def _cambiar_funcion(firma: str, cambios, al_reves: bool) -> None:
    """Aplica `cambios` al cuerpo que la función tiene hoy en la base. Cada texto tiene que estar
    una sola vez: si no, la migración se detiene (la función no es la que se esperaba)."""
    nueva = _reemplazar(_definicion(firma), cambios, al_reves, firma)
    op.get_bind().exec_driver_sql(nueva)


# --- Pregunta 16 (FL-CTO-01): señalar con `contactos.ver` ---------------------------------------

SENALAR = "nucleo.senalar_contacto(uuid, text, uuid, text)"
CAMBIOS_SENALAR = (
    (
        "    perform nucleo.exigir_gestion_contactos(c, actor, origen_pedido);\n"
        "    perform nucleo.contacto_para_cambiar(c, contacto);\n",
        "    -- 0110 (pregunta 16 del 18-11): señala quien ve los contactos (`en_ambito`), sobre\n"
        "    -- uno que ve hoy; quien gestiona, también los futuros.\n"
        "    if c is null then\n"
        "      raise exception 'sin consorcio en el ámbito' using errcode = '42501';\n"
        "    end if;\n"
        "    if origen_pedido is distinct from 'api' then\n"
        "      raise exception 'origen inválido' using errcode = 'P0001',\n"
        "        hint = 'origen_invalido';\n"
        "    end if;\n"
        "    if actor is null or actor is distinct from plataforma.persona_actual()\n"
        "       or not nucleo.en_ambito(c, actor) then\n"
        "      raise exception 'sin ámbito en este consorcio' using errcode = '42501';\n"
        "    end if;\n"
        "    k := nucleo.contacto_para_cambiar(c, contacto);\n"
        "    if k.vigente_desde > nucleo.hoy_del_consorcio(c)\n"
        "       and not nucleo.tiene_facultad_vigente(c, actor, 'contactos.gestionar',\n"
        "                                             nucleo.hoy_del_consorcio(c)) then\n"
        "      raise exception 'el contacto no existe' using errcode = 'P0001',\n"
        "        hint = 'no_encontrado';\n"
        "    end if;\n",
    ),
)

# --- Pregunta 15 (FL-SOL-03): la respuesta de una vencida que llegó por otro medio -------------

REGISTRAR_RESPUESTA = (
    "nucleo.registrar_respuesta_solicitud_formal(uuid, text, date, uuid, uuid, text, uuid)"
)
CAMBIOS_RESPUESTA = (
    (
        "                where e.consorcio_id = c and e.solicitud_id = s.id"
        " and m.persona_id is not null)\n    then\n",
        "                where e.consorcio_id = c and e.solicitud_id = s.id"
        " and m.persona_id is not null)\n"
        "       -- 0110 (pregunta 15 del 18-11): vencida, la registra el Consejo, que la\n"
        "       -- recibió por otro medio.\n"
        "       and nucleo.estado_solicitud_formal(c, s.id) <> 'vencida'\n    then\n",
    ),
)

# --- FL-SOL-04: el estado del archivo del envío y de la respuesta -------------------------------

SOLICITUDES = "nucleo.solicitudes_formales_lectura(uuid, text, uuid)"
CAMBIOS_SOLICITUDES = tuple(
    (
        f"               'version_documento_id', {a}.version_documento_id) end,\n",
        f"               'version_documento_id', {a}.version_documento_id,\n"
        "               -- 0110 (FL-SOL-04): cómo terminó el análisis del archivo.\n"
        "               'estado_archivo', (select x.estado from nucleo.version_documento x\n"
        f"                                   where x.id = {a}.version_documento_id)) end,\n",
    )
    for a in ("an", "ar")
)

# --- FL-INVA-03: la importada cuyo vínculo ya no corresponde no se lista ------------------------

MIS_INVITACIONES = "nucleo.mis_invitaciones()"
CAMBIOS_MIS_INVITACIONES = (
    (
        "       -- F9 (0026): las propias no se pueden aceptar.\n",
        "       -- 0110 (FL-INVA-03): la importada nombra su vínculo; si ya no le corresponde,\n"
        "       -- aceptarla responde `vinculo_no_corresponde` (0025): no se ofrece.\n"
        "       and (i.vinculo_id is null\n"
        "            or exists (select 1 from nucleo.vinculo v2\n"
        "                        where v2.id = i.vinculo_id and v2.consorcio_id = i.consorcio_id\n"
        "                          and v2.unidad_id = i.unidad_id and v2.persona_id = yo.id\n"
        "                          and v2.tipo = i.tipo\n"
        "                          and (v2.vigente_hasta is null\n"
        "                               or v2.vigente_hasta"
        " > nucleo.hoy_del_consorcio(i.consorcio_id))))\n"
        "       -- F9 (0026): las propias no se pueden aceptar.\n",
    ),
)

CAMBIOS = (
    (SENALAR, CAMBIOS_SENALAR),
    (REGISTRAR_RESPUESTA, CAMBIOS_RESPUESTA),
    (SOLICITUDES, CAMBIOS_SOLICITUDES),
    (MIS_INVITACIONES, CAMBIOS_MIS_INVITACIONES),
)

# --- Pregunta 4 (FL-LEG-10): retirar un rubro con faltantes pide motivo -------------------------

RUBRO_ANTES = "nucleo.asignar_rubro_legal_proveedor(uuid, text, boolean, uuid, text, uuid)"
RUBRO = "nucleo.asignar_rubro_legal_proveedor(uuid, text, boolean, text, uuid, text, uuid)"
TABLA_RETIRO = "nucleo.rubro_legal_retiro_motivo"
CAMBIOS_RUBRO = (
    ("asignar boolean, actor uuid", "asignar boolean, motivo text, actor uuid"),
    ("    numero_nuevo bigint;\n", "    numero_nuevo bigint;\n    faltaban text[];\n"),
    (
        "    insert into nucleo.rubro_legal_proveedor\n",
        "    -- 0110 (pregunta 4 del 18-11): quitar un rubro al que le falta documentación\n"
        "    -- destraba al proveedor; pide el motivo, que queda a la vista del Consejo.\n"
        "    faltaban := array(select f.codigo from nucleo.requisitos_faltantes(c, proveedor) f\n"
        "                       where f.rubro_legal = rubro order by f.codigo);\n"
        "    if asignar and motivo is not null then\n"
        "      raise exception 'asignar un rubro no lleva motivo' using errcode = 'P0001',\n"
        "        hint = 'motivo_invalido';\n"
        "    end if;\n"
        "    if not asignar and motivo is not null\n"
        f"       and length(btrim(motivo)) not between 1 and {MOTIVO_MAXIMO} then\n"
        f"      raise exception 'el motivo va de 1 a {MOTIVO_MAXIMO} caracteres'"
        " using errcode = 'P0001',\n"
        "        hint = 'motivo_invalido';\n"
        "    end if;\n"
        "    if not asignar and cardinality(faltaban) > 0 and motivo is null then\n"
        "      raise exception 'quitar un rubro con documentación faltante pide el motivo'\n"
        "        using errcode = 'P0001', hint = 'motivo_requerido';\n"
        "    end if;\n"
        "    insert into nucleo.rubro_legal_proveedor\n",
    ),
    (
        "    returning numero into numero_nuevo;\n",
        "    returning numero into numero_nuevo;\n"
        "    if not asignar and motivo is not null then\n"
        f"      insert into {TABLA_RETIRO} (consorcio_id, rubro_numero, motivo, faltaban)\n"
        "      values (c, numero_nuevo, btrim(motivo), faltaban);\n"
        "    end if;\n",
    ),
)

TABLA_PEDIDO = "nucleo.pedido_salteo_documentacion"
TABLAS = f"""
create table {TABLA_RETIRO} (
  consorcio_id  uuid not null references nucleo.consorcio (id),
  rubro_numero  bigint primary key references nucleo.rubro_legal_proveedor (numero),
  motivo        text not null
    check (length(btrim(motivo)) between 1 and {MOTIVO_MAXIMO} and motivo = btrim(motivo)),
  faltaban      text[] not null
);
comment on table {TABLA_RETIRO} is
  'Motivo de quitar un rubro legal al que le faltaba documentación (pregunta 4, 18-11-2026).';

create table {TABLA_PEDIDO} (
  consorcio_id      uuid not null references nucleo.consorcio (id),
  numero            bigint generated always as identity primary key,
  proveedor_id      uuid not null,
  motivo            text not null
    check (length(btrim(motivo)) between 1 and {MOTIVO_MAXIMO} and motivo = btrim(motivo)),
  importe_centavos  bigint not null check (importe_centavos > 0),
  moneda            text not null check (moneda ~ '^[A-Z]{{3}}$'),
  pedido_por        uuid not null references nucleo.persona (id),
  identidad         uuid not null,
  pedido_en         timestamptz not null default now(),
  foreign key (consorcio_id, proveedor_id) references nucleo.proveedor (consorcio_id, id),
  foreign key (consorcio_id, identidad) references nucleo.identidad_humana (consorcio_id, id)
);
comment on table {TABLA_PEDIDO} is
  'Pedidos de salteo al Consejo para un proveedor bloqueado (pregunta 3, 18-11-2026).';
create index pedido_salteo_documentacion_proveedor
  on {TABLA_PEDIDO} (consorcio_id, proveedor_id, numero);
"""
for _t, _n in ((TABLA_RETIRO, "rubro_legal_retiro_motivo"),
               (TABLA_PEDIDO, "pedido_salteo_documentacion")):  # fmt: skip
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

# --- Funciones nuevas ---------------------------------------------------------------------------

PEDIR = "nucleo.pedir_salteo_documentacion(uuid, text, bigint, text, uuid, text, uuid)"
PEDIDOS = "nucleo.pedidos_salteo_lectura(text, uuid, text, uuid)"
RETIROS = "nucleo.retiros_rubro_con_motivo_lectura(uuid, text, uuid, text, uuid)"
POR_ACLARAR = "nucleo.razon_social_por_aclarar(uuid, text, uuid)"
SIN_CUENTA = "nucleo.ordenes_sin_cuenta_lectura(text, uuid, text, uuid)"
CANDIDATOS = "nucleo.candidatos_a_mandato_datos()"
SOSTIENEN = "nucleo.vinculos_que_sostienen_consejo()"
DE_INVITACIONES = "nucleo.vinculos_de_invitaciones()"
ACEPTADA_POR = "nucleo.invitacion_aceptada_por(bytea, uuid)"
POR_PERSONA = "nucleo.autoriza_por_persona_lectura(text, uuid, text, uuid)"
POR_ROL = "nucleo.ven_proveedores_por_rol_lectura(uuid, text, uuid)"
GRUPOS_PERIODO = "nucleo.grupos_del_periodo_lectura(date, uuid, text, uuid)"
ADJUNTAR_ACTA = "nucleo.adjuntar_acta_decision(uuid, text, uuid, text)"
NUEVAS = (PEDIR, PEDIDOS, RETIROS, POR_ACLARAR, SIN_CUENTA, CANDIDATOS, SOSTIENEN,
          DE_INVITACIONES, ACEPTADA_POR, POR_PERSONA, POR_ROL, ADJUNTAR_ACTA)  # fmt: skip
ACCION_ACTA = "consorcio.decision.adjuntar_acta"

# Estado de un pedido: atendido si hay un salteo de ese proveedor registrado después; si no,
# pendiente mientras el proveedor sigue bloqueado.
_ESTADO_PEDIDO = """case when s.numero is not null then 'atendido'
                  when k.modo_exigencia = 'bloquear'
                       and exists (select 1 from nucleo.requisitos_faltantes(c, d.proveedor_id))
                    then 'pendiente'
                  else 'sin_bloqueo' end"""

FUNCIONES = f"""
-- FL-DEC-01: el acta de una decisión que quedó sin ella (o que se registró antes de que el
-- antivirus terminara). Solo una vez, de nulo a la huella de una versión disponible de un
-- documento vigente del consorcio: no reescribe nada que ya estuviera. Los controles de actor y
-- origen son los de `registrar_decision` (`0016`), y el disparador de `0089` bloquea el documento.
create function nucleo.adjuntar_acta_decision(decision uuid, huella_acta text, actor uuid,
                                              origen_pedido text)
  returns void
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    fila record;
  begin
    if c is null then
      raise exception 'sin consorcio en el ámbito' using errcode = '42501';
    end if;
    if origen_pedido is null or origen_pedido not in ('api', 'soporte') then
      raise exception 'origen no admitido: solo api o soporte'
        using errcode = 'P0001', hint = 'origen_invalido';
    end if;
    if origen_pedido = 'api' and actor is null then
      raise exception 'un registro por la API necesita quién lo hace'
        using errcode = 'P0001', hint = 'actor_requerido';
    end if;
    if origen_pedido = 'api' and actor is distinct from plataforma.persona_actual() then
      raise exception 'el actor no es la persona de la sesión'
        using errcode = 'P0001', hint = 'actor_no_coincide';
    end if;
    if origen_pedido = 'soporte'
       and (plataforma.persona_actual() is not null or actor is not null) then
      raise exception 'el origen soporte no lleva actor: lo registra el equipo'
        using errcode = 'P0001', hint = 'origen_invalido';
    end if;
    if huella_acta is null or lower(huella_acta) !~ '^[0-9a-f]{{64}}$' then
      raise exception 'el sha256 del acta no es hexadecimal de 64'
        using errcode = 'P0001', hint = 'sha256_invalido';
    end if;
    select f.id, f.tipo, f.fecha, f.sha256_acta into fila from nucleo.decision_formal f
     where f.consorcio_id = c and f.id = decision
       for update;
    if fila.id is null then
      raise exception 'la decisión no existe en este consorcio'
        using errcode = 'P0001', hint = 'decision_invalida';
    end if;
    if fila.sha256_acta is not null then
      raise exception 'la decisión ya tiene su acta' using errcode = 'P0001',
        hint = 'acta_ya_registrada';
    end if;
    if not exists (select 1 from nucleo.version_documento v
                     join nucleo.documento d
                       on d.consorcio_id = v.consorcio_id and d.id = v.documento_id
                    where v.consorcio_id = c and v.sha256 = lower(huella_acta)
                      and v.estado = 'disponible' and d.estado = 'vigente') then
      raise exception 'el acta no es una versión disponible de un documento del consorcio'
        using errcode = 'P0001', hint = 'acta_no_disponible';
    end if;
    update nucleo.decision_formal f set sha256_acta = lower(huella_acta)
     where f.consorcio_id = c and f.id = decision;
    -- S2b (`0043`): soporte no escribe eventos de clase B.
    if origen_pedido <> 'soporte' then
      insert into auditoria.eventos
        (consorcio_id, actor_persona_id, origen, accion, entidad, entidad_id, antes, despues,
         hash_previo, hash)
      values (c, actor, origen_pedido, '{ACCION_ACTA}', 'decision_formal', decision,
              jsonb_build_object('tipo', fila.tipo, 'fecha', fila.fecha, 'sha256_acta', null),
              jsonb_build_object('tipo', fila.tipo, 'fecha', fila.fecha,
                                 'sha256_acta', lower(huella_acta)),
              '', '');
    end if;
  end
  $$;
-- Pregunta 3: quien prepara las órdenes pide un salteo al Consejo, con el motivo y el importe.
create function nucleo.pedir_salteo_documentacion(proveedor_pedido uuid, motivo_pedido text,
                                                  importe bigint, moneda_pedido text,
                                                  actor uuid, origen_pedido text, sesion uuid)
  returns bigint
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    k record;
    identidad_actor uuid;
    numero_nuevo bigint;
  begin
    identidad_actor := nucleo.exigir_acto_sin_gesto(c, actor, sesion, 'egresos.preparar_orden',
                                                    origen_pedido);
    if motivo_pedido is null
       or length(btrim(motivo_pedido)) not between 1 and {MOTIVO_MAXIMO} then
      raise exception 'el motivo va de 1 a {MOTIVO_MAXIMO} caracteres' using errcode = 'P0001',
        hint = 'motivo_invalido';
    end if;
    if importe is null or importe <= 0 or moneda_pedido is null
       or moneda_pedido !~ '^[A-Z]{{3}}$' then
      raise exception 'el importe es positivo, en una moneda de tres letras'
        using errcode = 'P0001', hint = 'importe_invalido';
    end if;
    if proveedor_pedido is null
       or not exists (select 1 from nucleo.proveedor p
                       where p.consorcio_id = c and p.id = proveedor_pedido) then
      raise exception 'el proveedor no existe en este consorcio' using errcode = 'P0001',
        hint = 'proveedor_inexistente';
    end if;
    -- El candado del salteo y de la barrera: un pedido, un salteo y una orden a la vez se ven.
    perform pg_advisory_xact_lock(
      hashtextextended('nucleo.salteo_documentacion_proveedor:' || proveedor_pedido, 0));
    select * into k from nucleo.configuracion_proveedores_vigente(c);
    if k.modo_exigencia <> 'bloquear'
       or not exists (select 1 from nucleo.requisitos_faltantes(c, proveedor_pedido)) then
      raise exception 'nada bloquea las órdenes a este proveedor' using errcode = 'P0001',
        hint = 'salteo_innecesario';
    end if;
    -- Un salteo vigente que le sirve a quien pide (lo registró otra persona): no hace falta.
    if exists (select 1 from nucleo.salteo_documentacion_proveedor s
                where s.consorcio_id = c and s.proveedor_id = proveedor_pedido
                  and s.vence_en > now() and s.registrado_por <> actor
                  and s.identidad is distinct from identidad_actor
                  and not exists (select 1 from nucleo.salteo_documentacion_uso u
                                   where u.salteo_numero = s.numero)) then
      raise exception 'hay un salteo vigente que sirve para esta orden' using errcode = 'P0001',
        hint = 'salteo_vigente';
    end if;
    if exists (select 1 from {TABLA_PEDIDO} d
                where d.consorcio_id = c and d.proveedor_id = proveedor_pedido
                  and not exists (select 1 from nucleo.salteo_documentacion_proveedor s
                                   where s.consorcio_id = c and s.proveedor_id = d.proveedor_id
                                     and s.registrado_en >= d.pedido_en)) then
      raise exception 'ya hay un pedido de salteo que espera al Consejo' using errcode = 'P0001',
        hint = 'pedido_pendiente';
    end if;
    insert into {TABLA_PEDIDO}
      (consorcio_id, proveedor_id, motivo, importe_centavos, moneda, pedido_por, identidad)
    values (c, proveedor_pedido, btrim(motivo_pedido), importe, moneda_pedido, actor,
            identidad_actor)
    returning numero into numero_nuevo;
    -- El motivo queda en la tabla, no en la auditoría (E31).
    perform nucleo.auditar_proveedor(c, 'consorcio.proveedor.pedido_salteo', proveedor_pedido,
      actor, jsonb_build_object('pedido_salteo', false),
      jsonb_build_object('pedido_salteo', true, 'numero', numero_nuevo));
    return numero_nuevo;
  end
  $$;

-- Los pedidos de salteo del consorcio, con su estado y el salteo que los atendió. Los lee quien
-- saltea (`egresos.saltear_documentacion`, por la configuración) y quien lee las órdenes.
create function nucleo.pedidos_salteo_lectura(facultad text, actor uuid, origen_pedido text,
                                              sesion uuid)
  returns table (numero bigint, proveedor_id uuid, razon_social text, motivo text,
                 importe_centavos bigint, moneda text, pedido_en timestamptz,
                 pedido_por_nombre text, propio boolean, estado text, salteo_numero bigint,
                 salteo_vence_en timestamptz)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    k record;
  begin
    if facultad = 'egresos.saltear_documentacion' then
      if c is null then
        raise exception 'sin consorcio en el ámbito' using errcode = '42501';
      end if;
      if origen_pedido is distinct from 'api' then
        raise exception 'origen inválido' using errcode = 'P0001', hint = 'origen_invalido';
      end if;
      if actor is null or actor is distinct from plataforma.persona_actual()
         or not nucleo.puede_saltear_documentacion(c, actor) then
        raise exception 'sin egresos.saltear_documentacion en este consorcio'
          using errcode = '42501';
      end if;
      if not exists (select 1 from nucleo.sesion x
                      where x.id = sesion and x.persona_id = actor and x.revocada_en is null
                        and x.vence_en > now()) then
        raise exception 'la sesión no es válida' using errcode = 'P0001',
          hint = 'sesion_invalida';
      end if;
    else
      perform nucleo.exigir_lectura_egresos(c, facultad, actor, origen_pedido, sesion);
    end if;
    select * into k from nucleo.configuracion_proveedores_vigente(c);
    return query
      select d.numero, d.proveedor_id,
             (select pv.razon_social from nucleo.proveedor_version pv
               where pv.consorcio_id = c and pv.proveedor_id = d.proveedor_id
               order by pv.numero desc limit 1),
             d.motivo, d.importe_centavos, d.moneda, d.pedido_en, p.nombre, d.pedido_por = actor,
             {_ESTADO_PEDIDO},
             s.numero, s.vence_en
        from {TABLA_PEDIDO} d
        join nucleo.persona p on p.id = d.pedido_por
        left join lateral (select x.numero, x.vence_en
                             from nucleo.salteo_documentacion_proveedor x
                            where x.consorcio_id = c and x.proveedor_id = d.proveedor_id
                              and x.registrado_en >= d.pedido_en
                            order by x.numero limit 1) s on true
       where d.consorcio_id = c
       order by d.numero desc;
  end
  $$;

-- Pregunta 4: los rubros que se quitaron con documentación faltante, con el motivo.
create function nucleo.retiros_rubro_con_motivo_lectura(proveedor uuid, facultad text, actor uuid,
                                                        origen_pedido text, sesion uuid)
  returns table (proveedor_id uuid, rubro_legal text, motivo text, faltaban text[],
                 retirado_en timestamptz, retirado_por_nombre text)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
  begin
    perform nucleo.exigir_lectura_proveedores(c, facultad, actor, origen_pedido, sesion);
    return query
      select r.proveedor_id, r.rubro_legal, m.motivo, m.faltaban, r.registrado_en, p.nombre
        from {TABLA_RETIRO} m
        join nucleo.rubro_legal_proveedor r on r.numero = m.rubro_numero
        join nucleo.persona p on p.id = r.registrado_por
       where m.consorcio_id = c and (proveedor is null or r.proveedor_id = proveedor)
       order by r.numero desc;
  end
  $$;

-- FL-ACL-03 (pregunta 17): la razón social de cada pago para aclarar, con los controles de
-- `pagos_por_aclarar` (la llama).
create function nucleo.razon_social_por_aclarar(actor uuid, origen_pedido text, sesion uuid)
  returns table (orden_id uuid, razon_social text)
  language sql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
    select a.orden_id, pv.razon_social
      from nucleo.pagos_por_aclarar(actor, origen_pedido, sesion) a
      join nucleo.orden_pago_version v on v.orden_id = a.orden_id and v.numero = a.version
      join nucleo.proveedor_version pv
        on pv.proveedor_id = v.proveedor_id and pv.numero = v.proveedor_version
  $$;

-- FL-ORD-06: las órdenes cuyo proveedor, en la versión vigente, no tiene cuenta destino (la
-- comparación de `exigir_orden_emitible`). Con los controles de `ordenes_pago_lectura`.
create function nucleo.ordenes_sin_cuenta_lectura(facultad text, actor uuid, origen_pedido text,
                                                  sesion uuid)
  returns table (orden_id uuid)
  language sql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
    select o.id
      from nucleo.ordenes_pago_lectura(null, facultad, actor, origen_pedido, sesion) o
      join nucleo.orden_pago_version v on v.orden_id = o.id and v.numero = o.version_vigente
      join nucleo.proveedor_version pv
        on pv.proveedor_id = v.proveedor_id and pv.numero = v.proveedor_version
     where pv.cuenta_destino is null
  $$;

-- Pregunta 8 y FL-MAN-03: de cada candidato, si hoy cumple la condición para integrar el Consejo
-- con un vínculo confirmado (el catálogo de `0023`) y qué mandatos tiene vigentes o futuros. Con
-- los controles de `candidatos_a_mandato` (la llama).
create function nucleo.candidatos_a_mandato_datos()
  returns table (persona_id uuid, habilita_consejo boolean, mandatos text[])
  language plpgsql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
  begin
    return query
      select x.persona_id,
             exists (select 1 from nucleo.vinculo v
                       join nucleo.condicion_habilitante_consejo h on h.tipo = v.tipo
                       join nucleo.unidad u
                         on u.consorcio_id = v.consorcio_id and u.id = v.unidad_id
                        and u.eliminado_en is null
                      where v.consorcio_id = c and v.persona_id = x.persona_id
                        and v.confirmado_en is not null
                        and daterange(v.vigente_desde, v.vigente_hasta)
                            @> nucleo.hoy_del_consorcio(c)),
             coalesce((select array_agg(distinct m.tipo::text order by m.tipo::text)
                         from nucleo.mandato m
                        where m.consorcio_id = c and m.persona_id = x.persona_id
                          and m.causa_baja is null
                          and (m.vigente_hasta is null
                               or (m.vigente_hasta > nucleo.hoy_del_consorcio(c)
                                   and m.vigente_hasta > m.vigente_desde))),
                      '{{}}'::text[])
        from nucleo.candidatos_a_mandato() x;
  end
  $$;

-- FL-PAD-03: los vínculos del padrón de un carácter que habilita para el Consejo, de una persona
-- con un mandato del Consejo vigente. Con los controles de `padron_del_consorcio` (la llama).
create function nucleo.vinculos_que_sostienen_consejo()
  returns table (vinculo_id uuid)
  language plpgsql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
  begin
    return query
      select f.vinculo_id
        from nucleo.padron_del_consorcio() f
        join nucleo.vinculo v on v.consorcio_id = c and v.id = f.vinculo_id
        join nucleo.condicion_habilitante_consejo h on h.tipo = v.tipo
       where exists (select 1 from nucleo.mandato m
                      where m.consorcio_id = c and m.persona_id = v.persona_id
                        and m.tipo = 'miembro_consejo'
                        and nucleo.mandato_vigente(m.id, nucleo.hoy_del_consorcio(c)));
  end
  $$;

-- FL-INV-01: el vínculo que dejó cada invitación aceptada del consorcio (el evento de la
-- aceptación lo nombra, `0007` y `0025`). Solo ids, a quien tiene ámbito.
create function nucleo.vinculos_de_invitaciones()
  returns table (invitacion_id uuid, vinculo_id uuid)
  language plpgsql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    quien uuid := plataforma.persona_actual();
  begin
    if c is null or quien is null or not nucleo.en_ambito(c, quien) then
      raise exception 'sin ámbito en este consorcio' using errcode = '42501';
    end if;
    return query
      select distinct on (e.entidad_id) e.entidad_id, (e.despues ->> 'vinculo_id')::uuid
        from auditoria.eventos e
       where e.consorcio_id = c and e.accion = 'identidad.invitacion.aceptar'
         and e.entidad = 'invitacion' and (e.despues ->> 'vinculo_id') is not null
       order by e.entidad_id, e.secuencia desc;
  end
  $$;

-- FL-INVA-02: la invitación ya aceptada, solo a la cuenta de su correo y con su sesión. Nulo en
-- cualquier otro caso (la consulta sigue respondiendo igual a todos los demás).
create function nucleo.invitacion_aceptada_por(token bytea, persona_sesion uuid)
  returns table (consorcio text, unidad text, tipo text, correo text, nombre text,
                 invitada_por text, vence timestamptz)
  language sql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
    select c.nombre, u.designacion, i.tipo::text, i.correo, i.nombre, p.nombre, i.vence_en
      from nucleo.invitacion i
      join nucleo.persona yo
        on yo.id = persona_sesion and yo.correo = i.correo and yo.eliminado_en is null
      join nucleo.consorcio c on c.id = i.consorcio_id
      join nucleo.unidad u on u.consorcio_id = i.consorcio_id and u.id = i.unidad_id
      left join nucleo.persona p on p.id = i.invitada_por
     where i.token_hash = token and i.aceptada_en is not null
  $$;

-- FL-VER-01: si el consorcio autoriza a personas a ver los proveedores.
create function nucleo.autoriza_por_persona_lectura(facultad text, actor uuid,
                                                    origen_pedido text, sesion uuid)
  returns boolean
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
  begin
    perform nucleo.exigir_lectura_proveedores(c, facultad, actor, origen_pedido, sesion);
    return (select k.autoriza_por_persona from nucleo.configuracion_proveedores_vigente(c) k);
  end
  $$;

-- FL-VER-02: los habitantes que ven los proveedores por su rol (`roles_que_ven`, la regla de
-- `puede_ver_proveedores` sin la autorización por persona). Para quien autoriza.
create function nucleo.ven_proveedores_por_rol_lectura(actor uuid, origen_pedido text,
                                                       sesion uuid)
  returns table (persona_id uuid)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    hoy date;
  begin
    perform nucleo.exigir_autorizar_proveedores(c, actor, origen_pedido, sesion);
    hoy := nucleo.hoy_del_consorcio(c);
    return query
      select distinct v.persona_id
        from nucleo.vinculo v
        cross join nucleo.configuracion_proveedores_vigente(c) k
       where v.consorcio_id = c
         and daterange(v.vigente_desde, v.vigente_hasta) @> hoy
         and not nucleo.vinculo_importado_pendiente(v.id)
         and (exists (select 1 from nucleo.mandato m
                       where m.consorcio_id = c and m.persona_id = v.persona_id
                         and nucleo.mandato_vigente(m.id, hoy)
                         and m.tipo::text = any (k.roles_que_ven))
              or exists (select 1 from nucleo.vinculo w
                          where w.consorcio_id = c and w.persona_id = v.persona_id
                            and daterange(w.vigente_desde, w.vigente_hasta) @> hoy
                            and not nucleo.vinculo_importado_pendiente(w.id)
                            and w.tipo::text = any (k.roles_que_ven)));
  end
  $$;
"""

# FL-LIQ-01: los grupos de un período sin borrador, con el cuerpo y los controles que tiene hoy
# `grupos_liquidables_lectura` (se arma desde la base).
GRUPOS = "nucleo.grupos_liquidables_lectura(uuid, uuid, text, uuid)"
CAMBIOS_GRUPOS = (
    (
        "nucleo.grupos_liquidables_lectura(liquidacion uuid,",
        "nucleo.grupos_del_periodo_lectura(periodo date,",
    ),
    (
        "    select (l.periodo + interval '1 month' - interval '1 day')::date into hasta\n"
        "      from nucleo.liquidacion l where l.consorcio_id = c and l.id = liquidacion;\n"
        "    if hasta is null then\n"
        "      raise exception 'la liquidación no existe' using errcode = 'P0001',"
        " hint = 'no_encontrado';\n"
        "    end if;\n",
        "    -- 0110 (FL-LIQ-01): el último día del período, sin borrador abierto.\n"
        "    if periodo is null or extract(day from periodo) <> 1 then\n"
        "      raise exception 'el período es el primer día de un mes' using errcode = 'P0001',\n"
        "        hint = 'periodo_invalido';\n"
        "    end if;\n"
        "    hasta := (periodo + interval '1 month' - interval '1 day')::date;\n",
    ),
)


def _permisos(funciones) -> None:
    for funcion in funciones:
        op.execute(f"revoke all on function {funcion} from public")
        op.execute(f"grant execute on function {funcion} to cem_app")


GUARDA = f"""
do $guarda$
begin
  if exists (select 1 from {TABLA_PEDIDO}) or exists (select 1 from {TABLA_RETIRO})
     or exists (select 1 from nucleo.facultad_otorgada f
                  join nucleo.mandato m on m.consorcio_id = f.consorcio_id and m.id = f.mandato_id
                 where f.facultad = 'padron.confirmar_vinculo'
                   and m.tipo = 'miembro_consejo') then
    raise exception '0110 no baja: hay pedidos de salteo, retiros o el Consejo confirma'
      using errcode = 'P0001', hint = 'downgrade_con_datos';
  end if;
end
$guarda$;
"""


def upgrade() -> None:
    for firma, cambios in CAMBIOS:
        _cambiar_funcion(firma, cambios, al_reves=False)
    op.execute(
        "insert into nucleo.paquete_facultad (tipo, facultad)"
        " values ('miembro_consejo', 'padron.confirmar_vinculo')"
    )
    op.execute(TABLAS)
    nueva = _reemplazar(_definicion(RUBRO_ANTES), CAMBIOS_RUBRO, False, RUBRO_ANTES)
    op.get_bind().exec_driver_sql(nueva)
    op.execute(f"drop function {RUBRO_ANTES}")
    op.execute(FUNCIONES)
    grupos = _reemplazar(_definicion(GRUPOS), CAMBIOS_GRUPOS, False, GRUPOS)
    op.get_bind().exec_driver_sql(grupos)
    _permisos((RUBRO, GRUPOS_PERIODO, *NUEVAS))
    op.execute(
        "insert into auditoria.proyeccion (accion, texto, recurso, campos) values"
        f" ('{ACCION_ACTA}', 'Adjuntó el acta de una decisión', 'Decisión',"
        " array['tipo', 'fecha'])"
    )


def downgrade() -> None:
    op.execute(GUARDA)
    op.execute(f"delete from auditoria.proyeccion where accion = '{ACCION_ACTA}'")
    op.execute(f"drop function {GRUPOS_PERIODO}")
    for funcion in reversed(NUEVAS):
        op.execute(f"drop function {funcion}")
    anterior = _reemplazar(_definicion(RUBRO), CAMBIOS_RUBRO, True, RUBRO)
    op.get_bind().exec_driver_sql(anterior)
    op.execute(f"drop function {RUBRO}")
    _permisos((RUBRO_ANTES,))
    op.execute(f"drop table {TABLA_PEDIDO}")
    op.execute(f"drop table {TABLA_RETIRO}")
    op.execute(
        "delete from nucleo.paquete_facultad"
        " where tipo = 'miembro_consejo' and facultad = 'padron.confirmar_vinculo'"
    )
    for firma, cambios in reversed(CAMBIOS):
        _cambiar_funcion(firma, cambios, al_reves=True)
