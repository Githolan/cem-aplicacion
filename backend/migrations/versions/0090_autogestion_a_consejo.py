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
- **D2 sin carrera** (revisión de Codex en #227, P2). Una orden que se prepara mientras soporte
  pasa a Consejo podía comprobar el modo antes del cambio e insertarse después. La barrera del modo
  de los actos económicos (`nucleo.exigir_sesion_operativa`, `0071`) toma un candado de aviso
  compartido por consorcio antes de leer el modo, y `cambiar_modo` lo toma exclusivo **antes** de
  bloquear la fila del consorcio. Si el cambio va primero, la orden espera y después lee Consejo
  (`modo_no_admitido`). Si va primero la orden, el cambio espera y después la ve
  (`transicion_con_ordenes_abiertas`). Es un candado de aviso y no `for share` sobre la fila
  porque `cem_app` no tiene `update` sobre `nucleo.consorcio` (`0043`), y porque el insert de la
  orden toma la fila con su clave foránea: con la fila primero se cruzarían.
- D3 (agenda y delegaciones de AG) y D4 (lectura del saliente) no cambian la base: no hay agenda
  ni delegaciones construidas, y el saliente conserva los vínculos que ya tenga.

Downgrade: deshace los reemplazos sobre las definiciones vigentes. No hay datos nuevos.

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
OPERATIVA = "nucleo.exigir_sesion_operativa(uuid, uuid, uuid, text, boolean)"
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


CANDADO = "hashtextextended('modo:' || c::text, 0)"

SERIALIZA_0027 = """\
    -- Serializa los cambios de modo del consorcio.
    perform 1 from nucleo.consorcio x where x.id = c for update;"""

SERIALIZA_0090 = f"""\
    -- Serializa los cambios de modo del consorcio. El candado de aviso va antes que la fila: el
    -- insert de una orden toma la fila con su clave foránea (0090).
    perform pg_advisory_xact_lock({CANDADO});
    perform 1 from nucleo.consorcio x where x.id = c for update;"""

MODO_ECONOMICO_0067 = """\
    if not exists (
         select 1 from nucleo.modo_operacion m
"""

MODO_ECONOMICO_0090 = f"""\
    -- 0090: comparte con cambiar_modo el candado del modo; un cambio en curso termina antes de
    -- que esto lea el modo, y no empieza hasta que este acto termine.
    perform pg_advisory_xact_lock_shared({CANDADO});
{MODO_ECONOMICO_0067}"""


def _definicion(firma: str) -> str:
    return (
        op.get_bind()
        .execute(text("select pg_get_functiondef(cast(:f as regprocedure))"), {"f": firma})
        .scalar_one()
    )


def _cambiar(firma: str, *pares: tuple[str, str]) -> None:
    definicion = _definicion(firma)
    for antes, despues in pares:
        if definicion.count(antes) != 1:
            raise RuntimeError(f"0090: {firma} no tiene exactamente una vez {antes!r}")
        definicion = definicion.replace(antes, despues)
    op.execute(definicion)


def upgrade() -> None:
    _cambiar(
        MODO,
        (TRANSICIONES_0027, TRANSICIONES_0090),
        (CIERRE_0027, CIERRE_0090),
        (SERIALIZA_0027, SERIALIZA_0090),
    )
    _cambiar(OPERATIVA, (MODO_ECONOMICO_0067, MODO_ECONOMICO_0090))


def downgrade() -> None:
    _cambiar(OPERATIVA, (MODO_ECONOMICO_0090, MODO_ECONOMICO_0067))
    _cambiar(
        MODO,
        (SERIALIZA_0090, SERIALIZA_0027),
        (CIERRE_0090, CIERRE_0027),
        (TRANSICIONES_0090, TRANSICIONES_0027),
    )
