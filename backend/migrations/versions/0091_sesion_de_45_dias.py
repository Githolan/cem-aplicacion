"""R10 sin la sesión de 12 horas: la sesión vence a los 45 días sin usar la aplicación.

Decisión del propietario del 09-11-2026, comentario 28 de la revisión de pantallas (repositorio
CEM, `doc/revision-pantallas/pendientes.md`), con sus palabras: «Quita la decision de las 12 hs
por 45 dias sin usar la app para reducir friccion». Reemplaza la parte de la R10 del 24-10-2026
que pedía, para lo económico, una sesión empezada hace menos de 12 horas.

- `nucleo.exigir_sesion_operativa` (la de `0071`, con el modo de `0090`): sale la barrera
  `requiere_sesion_reciente`. Quedan la sesión válida y propia, el modo, el segundo factor de los
  últimos 10 minutos cuando el acto mueve dinero (`gesto`) y la identidad humana verificada. Se
  cambia sobre la definición vigente leída de la base, como hacen `0071` y `0090`.
- `nucleo.sesion`: el tope del vencimiento deslizante pasa de 30 a 45 días desde el último uso
  (`VIGENCIA_SESION`).

La copia del contacto del titular (`0042`) pide su propia sesión de 10 minutos: no cambia.

Downgrade: vuelve la barrera de 12 horas y el tope de 30 días. Las sesiones que vencen a más de 30
días de su último uso se acortan a esos 30 días: es lo que la restricción de antes admite, y acortar
una sesión no le da a nadie más acceso que el que tenía.

Revisión: 0091
Anterior: 0090
"""

from alembic import op
from sqlalchemy import text

revision = "0091"
down_revision = "0090"
branch_labels = None
depends_on = None

OPERATIVA = "nucleo.exigir_sesion_operativa(uuid, uuid, uuid, text, boolean)"

BARRERA_12_HORAS = """\
    if s.creada_en <= now() - interval '12 hours' then
      raise exception 'hace falta una sesión reciente' using errcode = 'P0001',
        hint = 'requiere_sesion_reciente';
    end if;
"""
ANTES_DEL_GESTO = """\
    if gesto and (s.segundo_factor_en is null
"""

TOPE_30 = "vence_en - ultimo_uso_en <= interval '30 days'"
TOPE_45 = "vence_en - ultimo_uso_en <= interval '45 days'"

ACORTAR_A_30_DIAS = """
update nucleo.sesion set vence_en = ultimo_uso_en + interval '30 days'
 where vence_en - ultimo_uso_en > interval '30 days'
"""


def _definicion(firma: str) -> str:
    return (
        op.get_bind()
        .execute(text("select pg_get_functiondef(cast(:f as regprocedure))"), {"f": firma})
        .scalar_one()
    )


def _restriccion_del_tope(tope: str) -> str:
    nombres = (
        op.get_bind()
        .execute(
            text(
                "select conname from pg_constraint"
                " where conrelid = 'nucleo.sesion'::regclass and contype = 'c'"
                " and pg_get_constraintdef(oid) like :t"
            ),
            {"t": f"%{tope.split(' <= ')[0]}%"},
        )
        .scalars()
        .all()
    )
    if len(nombres) != 1:
        raise RuntimeError(f"0091: se esperaba un tope de la sesión y hay {nombres}")
    return nombres[0]


def _cambiar_tope(nuevo: str) -> None:
    nombre = _restriccion_del_tope(nuevo)
    op.execute(f"alter table nucleo.sesion drop constraint {nombre}")
    op.execute(f"alter table nucleo.sesion add constraint {nombre} check ({nuevo})")


def upgrade() -> None:
    vigente = _definicion(OPERATIVA)
    if vigente.count(BARRERA_12_HORAS) != 1:
        raise RuntimeError("0091: exigir_sesion_operativa no tiene una sola barrera de 12 horas")
    op.execute(vigente.replace(BARRERA_12_HORAS, ""))
    _cambiar_tope(TOPE_45)


def downgrade() -> None:
    op.execute(ACORTAR_A_30_DIAS)
    _cambiar_tope(TOPE_30)
    vigente = _definicion(OPERATIVA)
    if vigente.count(ANTES_DEL_GESTO) != 1 or BARRERA_12_HORAS in vigente:
        raise RuntimeError("0091: exigir_sesion_operativa no es la que dejó el upgrade")
    op.execute(vigente.replace(ANTES_DEL_GESTO, BARRERA_12_HORAS + ANTES_DEL_GESTO))
