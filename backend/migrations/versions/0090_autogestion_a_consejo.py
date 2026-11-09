"""Autogestión → Consejo y ningún paso a Consejo con órdenes de pago sin terminar.

Diseño: repositorio CEM, `doc/diseno/Transicion_Autogestion_a_Consejo.md`, decisiones D1 a D5
recomendadas y aprobadas por el propietario el 09-11-2026 («hacelo con tus decisiones
recomendadas»). Se cambia `nucleo.cambiar_modo` sobre su definición vigente (la de `0027`
con las marcas S2b de `0043`), leída de la base como hace `0043`, sin copiarla a mano:

- **D1, la flecha nueva del diagrama 12** (documento 08 §5): «Asamblea contrata un administrador
  que no opera en la plataforma». Las barreras de `0027` valen igual: solo soporte, una decisión
  por cambio, desde hoy, el mandato del propietario administrador ya terminado
  (`transicion_requiere_fin_de_mandato`) y la baja con la misma decisión espera a RF52
  (`transicion_requiere_paquete_de_transferencia`, D5).
- **D2, órdenes abiertas.** Las funciones de egresos (`0045`, `0067`) exigen AA o AG; en Consejo
  una orden sin terminar queda congelada: no se ejecuta, no se concilia, no vuelve a revisión.
  Ningún paso a Consejo, desde AG ni desde AA, mientras haya una orden que no esté en
  `rechazada` ni `conciliada` (`transicion_con_ordenes_abiertas`). Una `pagada` sin conciliar
  también frena: el dinero salió y falta la conciliación. Nunca se cierra ni se rechaza una orden
  de oficio. Arregla también AA → Consejo, que ya existía.
- D3 (agenda y delegaciones de AG) y D4 (lectura del saliente) no cambian la base: no hay agenda
  ni delegaciones construidas, y el saliente conserva los vínculos que ya tenga.

Downgrade: deshace los dos reemplazos sobre la definición vigente. No hay datos nuevos.

Revisión: 0090
Anterior: 0089
"""

from alembic import op
from sqlalchemy import text

revision = "0090"
down_revision = "0089"
branch_labels = None
depends_on = None

MODO = "nucleo.cambiar_modo(nucleo.tipo_modo, uuid, date, uuid, text)"
ESTADOS_TERMINADOS = ("rechazada", "conciliada")

TRANSICIONES_0027 = """\
      -- Diagrama 12 (documento 08 §5): no hay AG → Consejo.
      if (anterior.modo::text, modo_nuevo::text) not in (
           ('consejo', 'administrador_aumentado'), ('consejo', 'autogestion'),
           ('administrador_aumentado', 'autogestion'),
           ('autogestion', 'administrador_aumentado'),
           ('administrador_aumentado', 'consejo')) then"""

TRANSICIONES_0090 = """\
      -- Diagrama 12 (documento 08 §5); AG → Consejo desde 0090 (D1).
      if (anterior.modo::text, modo_nuevo::text) not in (
           ('consejo', 'administrador_aumentado'), ('consejo', 'autogestion'),
           ('administrador_aumentado', 'autogestion'),
           ('autogestion', 'administrador_aumentado'),
           ('administrador_aumentado', 'consejo'),
           ('autogestion', 'consejo')) then"""

_terminados = ", ".join(f"'{e}'" for e in ESTADOS_TERMINADOS)

CIERRE_0027 = """\
      update nucleo.modo_operacion set vigente_hasta = desde where id = anterior.id;"""

CIERRE_0090 = f"""\
      -- D2 (0090): en Consejo no hay egresos (0045, 0067); una orden sin terminar quedaría
      -- congelada. Nunca se cierra ni se rechaza de oficio.
      if modo_nuevo = 'consejo' and exists (
           select 1 from nucleo.orden_pago o
            where o.consorcio_id = c and o.estado not in ({_terminados})) then
        raise exception 'hay órdenes de pago sin terminar'
          using errcode = 'P0001', hint = 'transicion_con_ordenes_abiertas';
      end if;
{CIERRE_0027}"""


def _definicion() -> str:
    return (
        op.get_bind()
        .execute(text("select pg_get_functiondef(cast(:f as regprocedure))"), {"f": MODO})
        .scalar_one()
    )


def _reemplazar_una_vez(definicion: str, antes: str, despues: str) -> str:
    if definicion.count(antes) != 1:
        raise RuntimeError(f"0090: {MODO} no tiene exactamente una vez {antes!r}")
    return definicion.replace(antes, despues)


def upgrade() -> None:
    d = _reemplazar_una_vez(_definicion(), TRANSICIONES_0027, TRANSICIONES_0090)
    op.execute(_reemplazar_una_vez(d, CIERRE_0027, CIERRE_0090))


def downgrade() -> None:
    d = _reemplazar_una_vez(_definicion(), CIERRE_0090, CIERRE_0027)
    op.execute(_reemplazar_una_vez(d, TRANSICIONES_0090, TRANSICIONES_0027))
