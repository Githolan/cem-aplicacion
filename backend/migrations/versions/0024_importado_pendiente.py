"""Importado pendiente en RF02 (decisión del propietario del 21-10-2026, Codex en CEM #69).

Diseño: `doc/diseno/Decisiones_Propietario_2026-10-21.md` del repositorio CEM, §2.4, y la
excepción de la regla 11 de `H2b_3_Tabla_de_verdad.md`.

Un vínculo es **importado pendiente** si tiene `fuente = 'importacion'`, no está confirmado y
ninguna aceptación lo nombra. La aceptación es la misma de E62 (`0023`): el evento
`identidad.invitacion.aceptar` cuyo `vinculo_id` es **ese** vínculo, con su invitación aceptada.
No alcanza con otra invitación aceptada de la misma persona, unidad y carácter para otro
período (Codex en app #102). Mientras está pendiente, el vínculo no da ámbito ni aparece en
`/me/consorcios`, y la persona solo puede ver y aceptar las invitaciones de vínculo dirigidas a
su correo. Los vínculos `documento`, `declaracion` e `invitacion` sin confirmar no cambian.

- `nucleo.vinculos_importados_pendientes()`: los ids de los vínculos importados pendientes de
  la persona de la sesión. Ninguna otra: no responde por ids ajenos. `security definer` porque
  la aceptación está en `auditoria.eventos`, que la RLS esconde fuera del consorcio. La usan el
  `Contexto` (`app/contexto.py`) y `/me/consorcios` para dejarlos afuera. Las políticas no
  cambian: la `propios` de `vinculo` le sigue dejando leer sus vínculos (el estado neutro).
- `nucleo.mis_invitaciones()`: las invitaciones **de vínculo** pendientes dirigidas al correo
  de la persona de la sesión (las de mandato, A7, están en otra tabla y tienen su flujo).
- `nucleo.aceptar_invitacion_propia(invitacion, ip)`: acepta con la sesión una de esas
  invitaciones. La prueba es el control del correo, la misma que el token del enlace: llama a
  `aceptar_invitacion` (0014) con el hash guardado y la persona de la sesión, así que crea o
  reutiliza el vínculo, marca la invitación y audita igual que el enlace. Una invitación de
  otro correo responde igual que una que no existe.

Downgrade: borra las tres funciones. No hay datos nuevos.

Revisión: 0024
Anterior: 0023
"""

from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None

PENDIENTES = "nucleo.vinculos_importados_pendientes()"
MIS_INVITACIONES = "nucleo.mis_invitaciones()"
ACEPTAR = "nucleo.aceptar_invitacion_propia(uuid, inet)"

UPGRADE = f"""
create function {PENDIENTES}
  returns setof uuid
  language sql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
    select v.id from nucleo.vinculo v
     where v.persona_id = plataforma.persona_actual()
       and v.fuente = 'importacion' and v.confirmado_en is null
       and not exists (
         select 1 from auditoria.eventos e
           join nucleo.invitacion i
             on i.id = e.entidad_id and i.consorcio_id = e.consorcio_id
          where e.consorcio_id = v.consorcio_id
            and e.accion = 'identidad.invitacion.aceptar' and e.entidad = 'invitacion'
            and e.despues ->> 'vinculo_id' = v.id::text
            and i.aceptada_en is not null)
  $$;

create function {MIS_INVITACIONES}
  returns table (id uuid, consorcio text, unidad text, tipo text, nombre text,
                 invitada_por text, vence_en timestamptz)
  language sql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
    select i.id, c.nombre, u.designacion, i.tipo::text, i.nombre, p.nombre, i.vence_en
      from nucleo.persona yo
      join nucleo.invitacion i on i.correo = yo.correo
      join nucleo.consorcio c on c.id = i.consorcio_id
      join nucleo.unidad u on u.consorcio_id = i.consorcio_id and u.id = i.unidad_id
      left join nucleo.persona p on p.id = i.invitada_por
     where yo.id = plataforma.persona_actual() and yo.eliminado_en is null
       and nucleo.motivo_invitacion_invalida(i) is null
     order by i.vence_en, i.id
  $$;

create function {ACEPTAR.replace("(uuid, inet)", "(invitacion uuid, ip_pedido inet)")}
  returns table (resultado text, consorcio uuid)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    yo uuid := plataforma.persona_actual();
    hash bytea;
    r record;
  begin
    if yo is null then
      raise exception 'sin persona en la sesión' using errcode = '42501';
    end if;
    -- Solo una invitación dirigida a su correo: cualquier otra, como si no existiera.
    select i.token_hash into hash
      from nucleo.invitacion i
      join nucleo.persona p on p.correo = i.correo and p.id = yo and p.eliminado_en is null
     where i.id = invitacion;
    if hash is null then
      return query select 'inexistente'::text, null::uuid;
      return;
    end if;
    select * into r from nucleo.aceptar_invitacion(hash, yo, null, null, null, null, ip_pedido,
                                                   null);
    return query select r.resultado, r.consorcio;
  end
  $$;

revoke all on function {PENDIENTES}, {MIS_INVITACIONES}, {ACEPTAR} from public;
grant execute on function {PENDIENTES}, {MIS_INVITACIONES}, {ACEPTAR} to cem_app;
"""

DOWNGRADE = f"""
drop function {ACEPTAR};
drop function {MIS_INVITACIONES};
drop function {PENDIENTES};
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
