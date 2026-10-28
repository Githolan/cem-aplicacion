"""H3, infraestructura común: nadie recibe una facultad económica de aprobar hasta que decida el
propietario.

Diseño: `doc/diseno/H3_Paquete_Decisiones_Economicas.md` del repositorio CEM, «Infraestructura
común a todas las opciones», primera fila que se hace: «ningún paquete de facultades contiene
`egresos.aprobar_orden`, `liquidacion.aprobar` ni `coeficientes.aprobar`». Es la decisión 7 del
propietario del 21-10 (`Decisiones_Propietario_2026-10-21.md` §2.2: «no se asignan todavía
`liquidacion.aprobar`, `egresos.aprobar_orden` ni `coeficientes.aprobar` a ningún paquete»),
llevada a la base como segunda barrera. Cubre también los mandatos con su propio trigger: el
alta (0016) ya rechaza lo que no está en el paquete, pero esa regla vive en la función y un
insert directo la saltea. No decide quién las recibe: la migración
que lo decida saca la facultad de la lista, en el mismo PR que la asigna.

- **Paquetes** (`nucleo.paquete_facultad`) y **mandatos** (`nucleo.facultad_otorgada`): un
  trigger niega la fila con una de las tres facultades (`facultad_sin_decidir`).
- **Lo que ya existe:** el upgrade revisa las dos tablas y falla si alguna fila ya la tiene, en
  vez de dejar una guarda «verde» sobre datos que la violan.
- **Lo que no hace:** no prohíbe combinaciones de etapas en un paquete. Las políticas separan por
  acto (`no_es_quien_preparo`, `no_es_quien_aprobo`); que un paquete no pueda reunir preparar y
  aprobar sería una regla nueva (bloquearía, por ejemplo, la opción 4.1B del paquete H3).

`fondo_reserva.autorizar` no está en la lista: el propietario ya la asignó al Consejo.

Revisión: 0044
Anterior: 0043
"""

from alembic import op

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None

SIN_DECIDIR = ("egresos.aprobar_orden", "liquidacion.aprobar", "coeficientes.aprobar")
_lista = ", ".join(f"'{f}'" for f in SIN_DECIDIR)

UPGRADE = f"""
do $$
begin
  if exists (select 1 from nucleo.paquete_facultad where facultad in ({_lista}))
     or exists (select 1 from nucleo.facultad_otorgada where facultad in ({_lista})) then
    raise exception 'ya hay una facultad de aprobar sin decidir en un paquete o un mandato';
  end if;
end
$$;

create function nucleo.negar_facultad_sin_decidir()
  returns trigger
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  begin
    if new.facultad in ({_lista}) then
      raise exception 'la facultad % todavía no se asigna (decisión 7 del 21-10)', new.facultad
        using errcode = 'P0001', hint = 'facultad_sin_decidir';
    end if;
    return new;
  end
  $$;
revoke all on function nucleo.negar_facultad_sin_decidir() from public;

create trigger paquete_sin_facultad_sin_decidir
  before insert or update of facultad on nucleo.paquete_facultad
  for each row execute function nucleo.negar_facultad_sin_decidir();
create trigger mandato_sin_facultad_sin_decidir
  before insert or update of facultad on nucleo.facultad_otorgada
  for each row execute function nucleo.negar_facultad_sin_decidir();
"""

DOWNGRADE = """
drop trigger mandato_sin_facultad_sin_decidir on nucleo.facultad_otorgada;
drop trigger paquete_sin_facultad_sin_decidir on nucleo.paquete_facultad;
drop function nucleo.negar_facultad_sin_decidir();
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
