"""Residuales de la revisión de 2d-3: orden de cerrojos de la purga y la identidad en el plan.

- **`nucleo.purgar_identidad`** (0005) borraba sesiones y después enlaces, y el `on delete
  cascade` de 0006 se llevaba el código después del enlace: sesión → enlace → código, al revés
  del orden único del código (código → enlace → sesión: `canjear_codigo` de 0006 y la supresión
  de 0040). Ahora borra los códigos de los enlaces que purga, después los enlaces y al final las
  sesiones. Las condiciones y lo que devuelve no cambian.
- **El plan de la supresión** (0040) no contaba `nucleo.identidad_persona`, que se conserva: la
  cuenta de qué identidad humana verificada (A13-C) tuvo la cuenta en cada consorcio. Ahora es
  un punto `conservar` más, junto a las verificaciones de identidad. La ejecución no cambia.

La baja del TOTP (`app/identidad/segundo_factor.py`) pasa en el mismo cambio a TOTP →
recuperación, como la confirmación del alta y la supresión.

La definición de `plataforma.privacidad_supresion` se lee de la base (`pg_get_functiondef`), como
en 0033, y se le agrega el punto antes del de los eventos de auditoría; el downgrade lo quita.

Revisión: 0041
Anterior: 0040
"""

from alembic import op
from sqlalchemy import text

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None

SUPRESION = "plataforma.privacidad_supresion(plataforma.caso_privacidad, boolean)"
ANCLA = "      select count(*) into n from auditoria.eventos e\n"
PUNTO = """\
      select count(*) into n from nucleo.identidad_persona ip
       where ip.persona_id = k.persona_id and (alc is null or ip.consorcio_id = alc);
      plan := plan || jsonb_build_array(jsonb_build_object(
        'que', 'identidad humana de la cuenta', 'tratamiento', 'conservar', 'filas', n,
        'motivo', 'qué identidad verificada (A13-C) tuvo la cuenta en cada consorcio; sin el '
          'documento'));
"""

PURGA = """
create or replace function nucleo.purgar_identidad(retencion interval)
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
    -- En el orden único del código: código, enlace y sesión (0006, 0040).
    delete from nucleo.codigo_acceso c
     using nucleo.enlace_acceso en
     where c.enlace_id = en.id
       and least(en.vence_en, coalesce(en.usado_en, en.vence_en)) < now() - retencion;
    delete from nucleo.enlace_acceso
     where least(vence_en, coalesce(usado_en, vence_en)) < now() - retencion;
    get diagnostics e = row_count;
    delete from nucleo.sesion
     where least(vence_en, coalesce(revocada_en, vence_en)) < now() - retencion;
    get diagnostics s = row_count;
    return query select s, e;
  end
  $$;
"""

PURGA_0005 = """
create or replace function nucleo.purgar_identidad(retencion interval)
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
"""


def _definicion(firma: str) -> str:
    return (
        op.get_bind()
        .execute(text("select pg_get_functiondef(cast(:f as regprocedure))"), {"f": firma})
        .scalar_one()
    )


def upgrade() -> None:
    op.execute(PURGA)
    definicion = _definicion(SUPRESION)
    if definicion.count(ANCLA) != 1 or PUNTO in definicion:
        raise RuntimeError("0041: el plan de la supresión no es el que dejó 0040")
    op.execute(definicion.replace(ANCLA, PUNTO + ANCLA))


def downgrade() -> None:
    definicion = _definicion(SUPRESION)
    if definicion.count(PUNTO) != 1:
        raise RuntimeError("0041: el plan de la supresión no tiene el punto de 0041")
    op.execute(definicion.replace(PUNTO, ""))
    op.execute(PURGA_0005)
