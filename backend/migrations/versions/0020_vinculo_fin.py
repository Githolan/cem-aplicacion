"""Fin de vínculo (H2b 2b-5, PR 1; PA17).

Diseño: `doc/diseno/H2b_5_Diseno.md` del repositorio CEM, §2, sobre H2b §4 ("terminar un vínculo
cierra su vigencia y revoca sesiones si a la persona no le queda otro acceso al consorcio").

- `nucleo.vinculo` suma `finalizado_por`, `finalizado_en` y `motivo_fin`. Las tres van juntas
  (`finalizado_por` es nulo con origen soporte) y el motivo, de 1 a 300, queda en la fila: la
  auditoría no lo lleva (E31). `cem_app` lee quién y cuándo, no el motivo.
- La vigencia admite quedar vacía (`vigente_hasta = vigente_desde`) **solo con un fin
  registrado**: un vínculo que empezó hoy y termina hoy no rigió ningún día. Una vigencia vacía
  no contiene ningún día ni se superpone con otra, y todo filtro de "vigente o futuro" usa
  `vigente_hasta > hoy`, que la deja afuera.
- **Un fin registrado no se mueve** (trigger `vinculo_fin_inalterable`): mientras haya un fin, ni
  la vigencia ni quién, cuándo y por qué cambian, aunque escriba el dueño de la tabla. Corregir
  un fin equivocado es anularlo entero (las cuatro columnas a la vez). Así ningún lector necesita
  mirar `finalizado_en`: la vigencia cerrada alcanza para `require_scope`, el padrón y
  `confirmar_vinculo` (Codex en app #94).
- `nucleo.finalizar_vinculo`: origen y actor como `baja_mandato` (0012, #66); motivo
  obligatorio; el vínculo terminado (`vinculo_finalizado`) o que todavía no empezó
  (`vinculo_no_iniciado`) no se finaliza. Cierra la vigencia hoy (día del consorcio), conserva
  la confirmación y el resto de la fila, y revoca las sesiones de la persona **solo si no le
  queda otro vínculo ni mandato vigente hoy en el consorcio**. Devuelve cuántas revocó. Dos fines
  simultáneos de la misma persona se serializan con un bloqueo por persona: si no, cada uno vería
  vigente el otro vínculo y ninguno cerraría las sesiones. Un fin simultáneo con `baja_mandato`
  o `aceptar_invitacion` no toma ese bloqueo (riesgo residual registrado en el diseño, §2.5): en
  el peor caso quedan sesiones abiertas sin acceso, porque la autorización se recalcula en cada
  pedido.

Downgrade: se niega (`downgrade_con_datos`) si algún vínculo tiene fin registrado o vigencia
vacía; si no, quita la función, las columnas y vuelve a la restricción estricta de 0001.

Revisión: 0020
Anterior: 0019
"""

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None

UPGRADE = r"""
alter table nucleo.vinculo
  add column finalizado_por uuid references nucleo.persona (id),
  add column finalizado_en  timestamptz,
  add column motivo_fin     text,
  drop constraint vinculo_check,
  add constraint vinculo_vigencia_valida
    check (vigente_hasta is null or vigente_hasta > vigente_desde
           or (vigente_hasta = vigente_desde and finalizado_en is not null)),
  add constraint vinculo_fin_completo
    check ((finalizado_en is null) = (motivo_fin is null)
           and (finalizado_en is not null or finalizado_por is null)),
  add constraint vinculo_motivo_fin
    check (motivo_fin is null or length(btrim(motivo_fin)) between 1 and 300),
  add constraint vinculo_finalizado_con_vigencia
    check (finalizado_en is null or vigente_hasta is not null);

grant select (finalizado_por, finalizado_en) on nucleo.vinculo to cem_app;

create function nucleo.vinculo_fin_inalterable() returns trigger
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  begin
    if old.finalizado_en is not null and new.finalizado_en is not null
       and (new.vigente_hasta, new.finalizado_en, new.finalizado_por, new.motivo_fin)
           is distinct from
           (old.vigente_hasta, old.finalizado_en, old.finalizado_por, old.motivo_fin) then
      raise exception 'un fin de vínculo registrado no se modifica: se anula entero'
        using errcode = 'P0001', hint = 'fin_inalterable';
    end if;
    return new;
  end
  $$;
create trigger vinculo_fin_inalterable before update on nucleo.vinculo
  for each row execute function nucleo.vinculo_fin_inalterable();

-- PA17. La facultad la decide la aplicación (`padron.gestionar` sobre la unidad); la base
-- garantiza ámbito, actor, estado y que nada se borre.
create function nucleo.finalizar_vinculo(
  vinculo_a_finalizar uuid, motivo text, actor uuid, origen_pedido text)
  returns bigint
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    v nucleo.vinculo;
    hoy date;
    revocadas bigint := 0;
  begin
    if c is null then
      raise exception 'sin consorcio en el ámbito' using errcode = '42501';
    end if;
    -- Origen y actor como `baja_mandato` (0012, #66).
    if origen_pedido is null or origen_pedido not in ('api', 'soporte') then
      raise exception 'origen no admitido: solo api o soporte'
        using errcode = 'P0001', hint = 'origen_invalido';
    end if;
    if origen_pedido = 'api' and actor is null then
      raise exception 'un fin de vínculo por la API necesita quién lo registra'
        using errcode = 'P0001', hint = 'actor_requerido';
    end if;
    if origen_pedido = 'api' and actor is distinct from plataforma.persona_actual() then
      raise exception 'el actor no es la persona de la sesión'
        using errcode = 'P0001', hint = 'actor_no_coincide';
    end if;
    if origen_pedido = 'soporte' and plataforma.persona_actual() is not null then
      raise exception 'el origen soporte no admite una persona en la sesión'
        using errcode = 'P0001', hint = 'origen_invalido';
    end if;
    if origen_pedido = 'soporte' and actor is not null then
      raise exception 'el origen soporte no lleva actor: lo registra el equipo'
        using errcode = 'P0001', hint = 'origen_invalido';
    end if;
    if motivo is null or length(btrim(motivo)) not between 1 and 300 then
      raise exception 'el fin de un vínculo necesita su motivo'
        using errcode = 'P0001', hint = 'motivo_requerido';
    end if;
    select * into v from nucleo.vinculo x
     where x.consorcio_id = c and x.id = vinculo_a_finalizar;
    if v.id is null then
      raise exception 'el vínculo no existe en este consorcio'
        using errcode = 'P0001', hint = 'vinculo_invalido';
    end if;
    -- Serializa los fines de la misma persona antes de bloquear y releer el vínculo.
    perform pg_advisory_xact_lock(hashtextextended('acceso:' || v.persona_id, 0));
    select * into v from nucleo.vinculo x where x.id = v.id for update;
    hoy := nucleo.hoy_del_consorcio(c);
    if v.vigente_hasta is not null and v.vigente_hasta <= hoy then
      raise exception 'el vínculo ya terminó'
        using errcode = 'P0001', hint = 'vinculo_finalizado';
    end if;
    if v.vigente_desde > hoy then
      raise exception 'el vínculo todavía no empezó: no se finaliza'
        using errcode = 'P0001', hint = 'vinculo_no_iniciado';
    end if;

    update nucleo.vinculo
       set vigente_hasta = hoy, finalizado_por = actor, finalizado_en = now(),
           motivo_fin = btrim(motivo)
     where id = v.id;

    -- Con otro vínculo o un mandato vigente hoy en el consorcio, la persona conserva el acceso:
    -- no se le cierran las sesiones. La autorización ya deja de concederle lo de este vínculo.
    if not exists (
         select 1 from nucleo.vinculo x
          where x.consorcio_id = c and x.persona_id = v.persona_id
            and daterange(x.vigente_desde, x.vigente_hasta) @> hoy)
       and not exists (
         select 1 from nucleo.mandato m
          where m.consorcio_id = c and m.persona_id = v.persona_id
            and nucleo.mandato_vigente(m.id, hoy)) then
      revocadas := nucleo.revocar_sesiones_de(v.persona_id);
    end if;

    -- El motivo queda en la fila: la auditoría no se puede borrar y no lleva texto libre (E31).
    insert into auditoria.eventos
      (consorcio_id, actor_persona_id, origen, accion, entidad, entidad_id, antes, despues,
       hash_previo, hash)
    values (c, actor, origen_pedido, 'consorcio.vinculo.finalizar', 'vinculo', v.id,
            jsonb_build_object('vigente_hasta', v.vigente_hasta),
            jsonb_build_object('vigente_hasta', hoy, 'unidad_id', v.unidad_id, 'tipo', v.tipo,
                               'sesiones_revocadas', revocadas),
            '', '');
    return revocadas;
  end
  $$;

revoke all on function nucleo.finalizar_vinculo(uuid, text, uuid, text) from public;
grant execute on function nucleo.finalizar_vinculo(uuid, text, uuid, text) to cem_app;
"""

DOWNGRADE = r"""
do $guarda$
begin
  -- Una vigencia vacía exige un fin registrado (`vinculo_vigencia_valida`): basta con mirar esto.
  if exists (select 1 from nucleo.vinculo where finalizado_en is not null) then
    raise exception 'hay vínculos finalizados: bajar a 0019 perdería quién, cuándo y por qué'
      using errcode = 'P0001', hint = 'downgrade_con_datos';
  end if;
end
$guarda$;

drop function nucleo.finalizar_vinculo(uuid, text, uuid, text);
drop trigger vinculo_fin_inalterable on nucleo.vinculo;
drop function nucleo.vinculo_fin_inalterable();

revoke select (finalizado_por, finalizado_en) on nucleo.vinculo from cem_app;

alter table nucleo.vinculo
  drop constraint vinculo_finalizado_con_vigencia,
  drop constraint vinculo_motivo_fin,
  drop constraint vinculo_fin_completo,
  drop constraint vinculo_vigencia_valida,
  add constraint vinculo_check check (vigente_hasta is null or vigente_hasta > vigente_desde),
  drop column motivo_fin,
  drop column finalizado_en,
  drop column finalizado_por;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
