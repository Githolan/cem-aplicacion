"""H3, infraestructura común: ningún paquete ni mandato acumula las etapas que la separación de
funciones reparte.

Diseño: `doc/diseno/H3_Paquete_Decisiones_Economicas.md` del repositorio CEM, «Infraestructura
común a todas las opciones». No decide quién recibe ninguna facultad: solo impide combinaciones
que ya están prohibidas.

- **Liquidación, en el paquete** (`nucleo.paquete_facultad`): ningún tipo de mandato recibe
  `liquidacion.preparar` y `liquidacion.aprobar` juntas. El documento 08 §2 lo dice literal:
  «quien prepara una liquidación no recibe automáticamente autorización para aprobarla». La
  guarda es sobre el paquete, que es lo que se recibe automáticamente; un otorgamiento explícito
  por acta (`facultad_otorgada`) queda para la decisión 4 del paquete H3.
- **Egresos, en el paquete y en cada mandato** (`nucleo.facultad_otorgada`): nadie recibe a la
  vez `egresos.preparar_orden`, `egresos.aprobar_orden` y `egresos.ejecutar_pago`. Las políticas
  ya separan cada orden (`no_es_quien_preparo`, `no_es_quien_aprobo`, A5); esta segunda barrera
  impide que una configuración futura deje a un solo mandato con el ciclo entero. Ninguna opción
  del paquete H3 lo necesita.
- **Lo que no hace:** no prohíbe pares de egresos (el administrador ya tiene preparar y ejecutar,
  y A5 separa por orden), ni mira coeficientes y fondo de reserva, cuya separación por acto ya
  está en sus políticas. Tampoco cruza mandatos de una misma persona: si el administrador puede
  ser además consejero es la decisión 6 del paquete H3.

Las dos guardas son triggers de constraint diferibles, al final de la transacción: una migración
que mueve facultades en varios pasos solo falla si el resultado final viola la regla.

Revisión: 0044
Anterior: 0043
"""

from alembic import op

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None

UPGRADE = """
-- Las combinaciones que ningún paquete (tipo) puede tener. Devuelve el tipo que viola, o nulo.
create function nucleo.paquete_que_acumula_etapas()
  returns nucleo.tipo_mandato
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select p.tipo from nucleo.paquete_facultad p
     group by p.tipo
    having count(*) filter (where p.facultad in ('liquidacion.preparar', 'liquidacion.aprobar'))
           = 2
        or count(*) filter (where p.facultad in ('egresos.preparar_orden',
                                                  'egresos.aprobar_orden',
                                                  'egresos.ejecutar_pago')) = 3
     limit 1
  $$;
revoke all on function nucleo.paquete_que_acumula_etapas() from public;

create function nucleo.exigir_paquete_sin_etapas_acumuladas()
  returns trigger
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    t nucleo.tipo_mandato := nucleo.paquete_que_acumula_etapas();
  begin
    if t is not null then
      raise exception 'el paquete % acumularía etapas que la separación de funciones reparte', t
        using errcode = 'P0001', hint = 'paquete_acumula_etapas';
    end if;
    return null;
  end
  $$;
revoke all on function nucleo.exigir_paquete_sin_etapas_acumuladas() from public;

create constraint trigger paquete_sin_etapas_acumuladas
  after insert or update on nucleo.paquete_facultad
  deferrable initially deferred
  for each row execute function nucleo.exigir_paquete_sin_etapas_acumuladas();

-- El mismo ciclo de egresos, en las facultades de cada mandato.
create function nucleo.exigir_mandato_sin_ciclo_de_egresos()
  returns trigger
  language plpgsql security definer
  set search_path = pg_catalog, pg_temp
  as $$
  begin
    if (select count(distinct f.facultad) from nucleo.facultad_otorgada f
         where f.mandato_id = new.mandato_id
           and f.facultad in ('egresos.preparar_orden', 'egresos.aprobar_orden',
                              'egresos.ejecutar_pago')) = 3 then
      raise exception 'un mismo mandato no puede preparar, aprobar y ejecutar egresos'
        using errcode = 'P0001', hint = 'mandato_acumula_etapas';
    end if;
    return null;
  end
  $$;
revoke all on function nucleo.exigir_mandato_sin_ciclo_de_egresos() from public;

create constraint trigger mandato_sin_ciclo_de_egresos
  after insert or update on nucleo.facultad_otorgada
  deferrable initially deferred
  for each row execute function nucleo.exigir_mandato_sin_ciclo_de_egresos();
"""

DOWNGRADE = """
drop trigger mandato_sin_ciclo_de_egresos on nucleo.facultad_otorgada;
drop function nucleo.exigir_mandato_sin_ciclo_de_egresos();
drop trigger paquete_sin_etapas_acumuladas on nucleo.paquete_facultad;
drop function nucleo.exigir_paquete_sin_etapas_acumuladas();
drop function nucleo.paquete_que_acumula_etapas();
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
