"""R10: el gesto se pide solo en lo que mueve dinero o cambia a quién se le paga.

Diseño `doc/diseno/Llave_de_Acceso_Diseno.md`, §4, del repositorio CEM: el propietario aceptó el
04-11-2026 que mirar, preparar una orden y armar una liquidación no pidan el segundo factor.
Reemplaza la decisión del 30-10 sobre las lecturas económicas.

Siguen exigiendo la facultad, el modo, la sesión de 12 horas y la identidad humana: solo sale la
verificación de los últimos 10 minutos. Aprobar, rechazar, emitir, ver la cuenta, registrar,
conciliar, declarar, confirmar contratos y tocar proveedores no cambian.

- `nucleo.exigir_sesion_operativa`: la de `0067`, con el gesto como parámetro.
- `nucleo.exigir_sesion_economica`: la misma de siempre, ahora llama a la operativa con el gesto.
- `nucleo.exigir_acto_sin_gesto`: la facultad y la sesión operativa sin el gesto.

Revisión: 0071
Anterior: 0070
"""

from alembic import op
from sqlalchemy import text

revision = "0071"
down_revision = "0070"
branch_labels = None
depends_on = None

SESION = "nucleo.exigir_sesion_economica(uuid, uuid, uuid, text)"
SIN_GESTO = (
    "enviar_orden_a_revision", "modificar_orden_pago", "preparar_orden_pago",
    "renovar_orden_vencida", "exigir_lectura_egresos", "agregar_gasto_liquidacion",
    "quitar_gasto_liquidacion", "preparar_liquidacion", "grupos_liquidables_lectura",
    "liquidacion_gastos_lectura", "liquidaciones_lectura", "ordenes_liquidables_lectura",
    "descartar_liquidacion",
)  # fmt: skip
"""Las funciones vigentes que preparan o leen: cada una llama a `exigir_acto_economico`."""

GESTO = """\
    if s.segundo_factor_en is null or s.segundo_factor_en <= now() - interval '10 minutes' then
"""
GESTO_OPCIONAL = """\
    if gesto and (s.segundo_factor_en is null
                  or s.segundo_factor_en <= now() - interval '10 minutes') then
"""

ENVOLTURA = """
create or replace function nucleo.exigir_sesion_economica(c uuid, actor uuid, sesion uuid,
                                                          origen_pedido text)
  returns uuid
  language sql volatile
  set search_path = pg_catalog, pg_temp
  as $$
    select nucleo.exigir_sesion_operativa(c, actor, sesion, origen_pedido, true)
  $$;
"""

ACTO = """
create function nucleo.exigir_acto_sin_gesto(c uuid, actor uuid, sesion uuid, codigo text,
                                             origen_pedido text)
  returns uuid
  language plpgsql volatile
  set search_path = pg_catalog, pg_temp
  as $$
  begin
    if c is null then
      raise exception 'sin consorcio en el ámbito' using errcode = '42501';
    end if;
    if origen_pedido is distinct from 'api' then
      raise exception 'origen inválido' using errcode = 'P0001', hint = 'origen_invalido';
    end if;
    if actor is null or actor is distinct from plataforma.persona_actual()
       or not nucleo.tiene_facultad_vigente(c, actor, codigo, nucleo.hoy_del_consorcio(c)) then
      raise exception 'sin % en este consorcio', codigo using errcode = '42501';
    end if;
    return nucleo.exigir_sesion_operativa(c, actor, sesion, origen_pedido, false);
  end
  $$;
revoke all on function nucleo.exigir_acto_sin_gesto(uuid, uuid, uuid, text, text) from public;
"""


def _definicion(firma: str) -> str:
    return (
        op.get_bind()
        .execute(text("select pg_get_functiondef(cast(:f as regprocedure))"), {"f": firma})
        .scalar_one()
    )


def _firmas() -> list[str]:
    filas = (
        op.get_bind()
        .execute(
            text(
                "select p.oid::regprocedure::text from pg_proc p"
                " join pg_namespace n on n.oid = p.pronamespace"
                " where n.nspname = 'nucleo' and p.proname = any(:n)"
            ),
            {"n": list(SIN_GESTO)},
        )
        .scalars()
        .all()
    )
    if len(filas) != len(SIN_GESTO):
        raise RuntimeError(f"0071: se esperaban {len(SIN_GESTO)} funciones y hay {filas}")
    return filas


def _cambiar_llamadas(antes: str, despues: str) -> None:
    for firma in _firmas():
        definicion = _definicion(firma)
        if definicion.count(antes) != 1:
            raise RuntimeError(f"0071: {firma} no llama una sola vez a {antes}")
        op.execute(definicion.replace(antes, despues))


def upgrade() -> None:
    vigente = _definicion(SESION)
    if vigente.count(GESTO) != 1:
        raise RuntimeError("0071: exigir_sesion_economica no es la que dejó 0067")
    operativa = (
        vigente.replace(GESTO, GESTO_OPCIONAL)
        .replace(
            "CREATE OR REPLACE FUNCTION nucleo.exigir_sesion_economica(",
            "CREATE FUNCTION nucleo.exigir_sesion_operativa(",
        )  # fmt: skip
        .replace("origen_pedido text)", "origen_pedido text, gesto boolean)")
    )
    op.execute(operativa)
    op.execute(
        "revoke all on function nucleo.exigir_sesion_operativa(uuid, uuid, uuid, text, boolean)"
        " from public"
    )
    op.execute(ENVOLTURA)
    op.execute(ACTO)
    _cambiar_llamadas("nucleo.exigir_acto_economico(", "nucleo.exigir_acto_sin_gesto(")


def downgrade() -> None:
    _cambiar_llamadas("nucleo.exigir_acto_sin_gesto(", "nucleo.exigir_acto_economico(")
    op.execute("drop function nucleo.exigir_acto_sin_gesto(uuid, uuid, uuid, text, text)")
    operativa = _definicion("nucleo.exigir_sesion_operativa(uuid, uuid, uuid, text, boolean)")
    if operativa.count(GESTO_OPCIONAL) != 1:
        raise RuntimeError("0071: exigir_sesion_operativa no es la esperada")
    op.execute(
        operativa.replace(GESTO_OPCIONAL, GESTO)
        .replace(
            "CREATE OR REPLACE FUNCTION nucleo.exigir_sesion_operativa(",
            "CREATE OR REPLACE FUNCTION nucleo.exigir_sesion_economica(",
        )  # fmt: skip
        .replace("origen_pedido text, gesto boolean)", "origen_pedido text)")
    )
    op.execute("drop function nucleo.exigir_sesion_operativa(uuid, uuid, uuid, text, boolean)")
