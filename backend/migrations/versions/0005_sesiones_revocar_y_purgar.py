"""Identidad: revocar todas las sesiones de una persona y purgar sesiones y enlaces viejos.

Documento 13, §10.4 (sesiones listables y revocables, RF04; cierre inmediato al cambiar un
mandato, RN22) y §15.6 (registros de acceso técnico: 90 días).

- `nucleo.revocar_sesiones_de(persona)`: punto de enganche de RN22. El módulo de mandatos
  (H2 2b) lo llamará en la misma transacción que cambia un mandato o termina un vínculo. Cruza
  personas (quien cambia el mandato no es la persona afectada), así que es `security definer`
  de `cem_migrate`, como las funciones de la 0004.
- `nucleo.purgar_identidad(retencion)`: borra las sesiones y los enlaces que dejaron de servir
  (vencidos, revocados o usados) hace más de `retencion`. La llama la purga diaria
  (`plataforma.purgar_cola`). Una retención negativa borraría lo vigente: la función la rechaza.

Las revocaciones de la propia persona (una sesión, o todas menos la actual) no necesitan
función: `cem_app` ya puede `update (revocada_en)` sobre sus sesiones (RLS de la 0004).

Revisión: 0005
Anterior: 0004
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

UPGRADE = r"""
-- Revoca todas las sesiones vigentes de una persona. Devuelve cuántas revocó.
create function nucleo.revocar_sesiones_de(persona uuid)
  returns bigint
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    revocadas bigint;
  begin
    if persona is null then
      raise exception 'revocar_sesiones_de: falta la persona';
    end if;
    update nucleo.sesion set revocada_en = now()
     where persona_id = persona and revocada_en is null and vence_en > now();
    get diagnostics revocadas = row_count;
    return revocadas;
  end
  $$;

-- Borra sesiones y enlaces que dejaron de servir hace más de `retencion`. Una sesión deja de
-- servir al vencer o al revocarse (lo que ocurra antes); un enlace, al vencer o al usarse.
create function nucleo.purgar_identidad(retencion interval)
  returns table (sesiones bigint, enlaces bigint)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    s bigint;
    e bigint;
  begin
    if retencion is null or retencion < interval '0' then
      raise exception 'purgar_identidad: retención inválida (%)', retencion;
    end if;
    delete from nucleo.sesion
     where least(vence_en, coalesce(revocada_en, vence_en)) < now() - retencion;
    get diagnostics s = row_count;
    delete from nucleo.enlace_acceso
     where least(vence_en, coalesce(usado_en, vence_en)) < now() - retencion;
    get diagnostics e = row_count;
    return query select s, e;
  end
  $$;

revoke all on function
  nucleo.revocar_sesiones_de(uuid),
  nucleo.purgar_identidad(interval)
  from public;
grant execute on function
  nucleo.revocar_sesiones_de(uuid),
  nucleo.purgar_identidad(interval)
  to cem_app;
"""

DOWNGRADE = """
drop function if exists
  nucleo.revocar_sesiones_de(uuid),
  nucleo.purgar_identidad(interval);
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
