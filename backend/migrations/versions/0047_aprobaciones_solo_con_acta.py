"""P3 80, decisión del propietario del 29-10-2026: la cantidad de aprobaciones de una orden de
pago solo se carga con un acta de asamblea o del Consejo.

Decisión (mensaje del propietario, punto 2): «La configuración de 1 o 2 aprobaciones debe estar
respaldada por un acta formal de Asamblea o del Consejo, no por cualquier `decision_formal`
genérica». En `0046`, `nucleo.fijar_aprobaciones_orden_pago` aceptaba cualquier decisión del
consorcio, también un `contrato_administracion` o una `inscripcion_rpa`.

- **El tipo:** la decisión de respaldo tiene que ser de `TIPOS_ACTA` (`asamblea` o `consejo` de
  `nucleo.tipo_decision`); otra se rechaza con `decision_no_es_acta`. No hay jerarquía entre las
  dos (el propietario pidió no inventarla todavía).
- **Lo que ya cumplía `0046` y no cambia:** el consorcio de la decisión es el del ámbito (y la FK
  compuesta de la tabla), la decisión existe (`decision_invalida`), la carga rige desde hoy en
  adelante (`fecha_invalida`), solo se agregan filas, la carga solo soporte (`origen_invalido`) y
  soporte no aprueba (no tiene ninguna facultad). La referencia del acta y su fecha son columnas
  obligatorias de `decision_formal` (`0010`), y `registrar_decision`, la única vía de escritura de
  la aplicación, no admite una fecha futura (`0019`; es una regla de esa función, no de la tabla):
  el acta es anterior o igual al día en que se carga, y por eso al día desde el que rige.

P3 81, misma decisión (puntos 3, 4 y 8): `egresos.aprobar_orden` queda **solo** en el paquete de
`miembro_consejo`, y la facultad proviene exclusivamente de un mandato de ese tipo. Hasta acá lo
cumplía el contenido: `alta_mandato` y `aceptar_invitacion_mandato` solo copian facultades del
paquete del tipo (`facultad_fuera_de_paquete`), pero nada impedía que una migración la sumara al
paquete de `administrador`. Ahora es una regla de la base (`nucleo.aprobar_orden_solo_consejo`,
`facultad_solo_consejo`):
- no entra al paquete de otro tipo;
- no se otorga a un mandato de otro tipo;
- un mandato que la tiene no cambia de tipo (el código nunca cambia el tipo; es el cierre).

No decide la 6: quien es administrador y además consejero aprueba solo por su mandato de Consejo,
y la separación por orden (quien preparó no aprueba esa orden) sigue en
`registrar_aprobacion_orden_pago`.

**Upgrade** se niega (`upgrade_con_datos`) si alguna carga ya cargada está respaldada por otro
tipo, o si la facultad ya está en otro paquete o en un mandato de otro tipo: no se cambia la
historia, la corrige el equipo con su acta. **Downgrade** vuelve al texto de `0046` y quita la
guarda; no hay datos que proteger (lo que cumple `0047` también vale en `0046`).

Revisión: 0047
Anterior: 0046
"""

from alembic import op
from sqlalchemy import text

revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None

TIPOS_ACTA = ("asamblea", "consejo")
FIJAR = "nucleo.fijar_aprobaciones_orden_pago(smallint, date, uuid, uuid, text)"
FACULTAD = "egresos.aprobar_orden"
TIPO_CONSEJO = "miembro_consejo"

_LISTA = ", ".join(f"'{t}'" for t in TIPOS_ACTA)

# Sobre el texto exacto de la función de 0046: (antes, después).
CAMBIOS = [
    (
        "hint = 'decision_invalida';\n    end if;\n",
        "hint = 'decision_invalida';\n    end if;\n"
        "    -- P3 80 (29-10-2026): solo un acta de asamblea o del Consejo respalda la cantidad.\n"
        "    if not exists (select 1 from nucleo.decision_formal d\n"
        "                    where d.consorcio_id = c and d.id = decision\n"
        f"                      and d.tipo in ({_LISTA})) then\n"
        "      raise exception 'la cantidad solo se carga con un acta de asamblea o del Consejo'\n"
        "        using errcode = 'P0001', hint = 'decision_no_es_acta';\n"
        "    end if;\n",
    )
]

GUARDA = f"""
-- Nada se escribe en estas tablas entre la guarda y el cambio (revisión de app #164): una carga o
-- una facultad confirmada después de mirar quedaría fuera de la regla.
-- `mandato` antes que `facultad_otorgada`, en el orden en que escribe `alta_mandato`.
lock table nucleo.aprobaciones_orden_pago, nucleo.paquete_facultad, nucleo.mandato,
  nucleo.facultad_otorgada in share row exclusive mode;

do $$
begin
  if exists (select 1 from nucleo.aprobaciones_orden_pago a
               join nucleo.decision_formal d
                 on d.consorcio_id = a.consorcio_id and d.id = a.decision_id
              where d.tipo not in ({_LISTA})) then
    raise exception 'hay cantidades de aprobaciones respaldadas por algo que no es un acta'
      using errcode = 'P0001', hint = 'upgrade_con_datos';
  end if;
  if exists (select 1 from nucleo.paquete_facultad p
              where p.facultad = '{FACULTAD}' and p.tipo <> '{TIPO_CONSEJO}')
     or exists (select 1 from nucleo.facultad_otorgada f
                  join nucleo.mandato m on m.consorcio_id = f.consorcio_id and m.id = f.mandato_id
                 where f.facultad = '{FACULTAD}' and m.tipo <> '{TIPO_CONSEJO}') then
    raise exception '{FACULTAD} ya está fuera del Consejo'
      using errcode = 'P0001', hint = 'upgrade_con_datos';
  end if;
end
$$;
"""

# La función lee `mandato` con permisos de su dueño: la fila se ve también fuera del ámbito del
# consorcio (RLS forzada), y una fila que no se encuentra se rechaza igual.
SOLO_CONSEJO = f"""
create function nucleo.aprobar_orden_solo_consejo()
  returns trigger
  language plpgsql security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    tipo_mandato nucleo.tipo_mandato;
  begin
    if tg_table_name = 'paquete_facultad' then
      if new.facultad <> '{FACULTAD}' then
        return new;
      end if;
      tipo_mandato := new.tipo;
    elsif tg_table_name = 'facultad_otorgada' then
      if new.facultad <> '{FACULTAD}' then
        return new;
      end if;
      select m.tipo into tipo_mandato from nucleo.mandato m
       where m.consorcio_id = new.consorcio_id and m.id = new.mandato_id;
    else  -- mandato: solo importa si tiene la facultad
      if not exists (select 1 from nucleo.facultad_otorgada f
                      where f.consorcio_id = new.consorcio_id and f.mandato_id = new.id
                        and f.facultad = '{FACULTAD}') then
        return new;
      end if;
      tipo_mandato := new.tipo;
    end if;
    if tipo_mandato is distinct from '{TIPO_CONSEJO}' then
      raise exception '{FACULTAD} solo la tiene un mandato del Consejo (decisión del 29-10)'
        using errcode = 'P0001', hint = 'facultad_solo_consejo';
    end if;
    return new;
  end
  $$;
revoke all on function nucleo.aprobar_orden_solo_consejo() from public;

create trigger paquete_aprobar_orden_solo_consejo
  before insert or update on nucleo.paquete_facultad
  for each row execute function nucleo.aprobar_orden_solo_consejo();
create trigger mandato_aprobar_orden_solo_consejo
  before insert or update on nucleo.facultad_otorgada
  for each row execute function nucleo.aprobar_orden_solo_consejo();
create trigger tipo_con_aprobar_orden_solo_consejo
  before update of tipo on nucleo.mandato
  for each row execute function nucleo.aprobar_orden_solo_consejo();
"""

SIN_SOLO_CONSEJO = """
drop trigger tipo_con_aprobar_orden_solo_consejo on nucleo.mandato;
drop trigger mandato_aprobar_orden_solo_consejo on nucleo.facultad_otorgada;
drop trigger paquete_aprobar_orden_solo_consejo on nucleo.paquete_facultad;
drop function nucleo.aprobar_orden_solo_consejo();
"""


def _cambiar(pares: list[tuple[str, str]]) -> str:
    definicion = (
        op.get_bind()
        .execute(text("select pg_get_functiondef(cast(:f as regprocedure))"), {"f": FIJAR})
        .scalar_one()
    )
    for antes, despues in pares:
        if definicion.count(antes) != 1:
            raise RuntimeError(f"0047: {FIJAR} no tiene exactamente una vez {antes!r}")
        definicion = definicion.replace(antes, despues)
    return definicion


def upgrade() -> None:
    op.execute(GUARDA)
    op.execute(_cambiar(CAMBIOS))
    op.execute(SOLO_CONSEJO)


def downgrade() -> None:
    op.execute(SIN_SOLO_CONSEJO)
    op.execute(_cambiar([(despues, antes) for antes, despues in CAMBIOS]))
