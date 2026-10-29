"""H3, decisión 2 del propietario (29-10-2026): el Consejo aprueba las órdenes de pago, y cuántas
aprobaciones hacen falta se configura por consorcio.

Diseño: `doc/diseno/H3_Paquete_Decisiones_Economicas.md` del repositorio CEM, decisión 2, y las
decisiones del 29-10 (`doc/diseno/Decisiones_Propietario_2026-10-29.md`): «2 configurable por
consorcio; por defecto aprueba 1 consejero distinto de quien preparó; Soporte CEM carga 1 o 2 con
su acta y nunca aprueba». El propietario dio el sí explícito para crear la facultad.

- **La facultad:** `egresos.aprobar_orden` entra al paquete de `miembro_consejo` y sale de la
  guarda de `0044` (que sigue negando `liquidacion.aprobar` y `coeficientes.aprobar`). No se
  otorga a ningún mandato existente: la reciben los mandatos que se den de alta después, con el
  paquete completo o eligiéndola.
- **La configuración:** `nucleo.aprobaciones_orden_pago`, una fila por cambio, con la cantidad (1
  o 2), desde cuándo rige y la decisión formal que la respalda. Solo se agrega (los mismos
  triggers que la historia de la orden): un cambio es otra fila con una fecha posterior. La carga
  soporte con `nucleo.fijar_aprobaciones_orden_pago` (`scripts/aprobaciones_orden_pago.py`),
  desde hoy en adelante, nunca hacia atrás. Como el resto de la clase B (S2b, `0043`), soporte no
  escribe evento: la evidencia es la fila con su decisión.
- **Sin configuración rige 1** (`POR_DEFECTO`, la decisión del propietario).
- **Cada versión guarda cuántas exige** (`orden_pago_version.aprobaciones_requeridas`), tomada al
  prepararla con la configuración vigente ese día. Un cambio de configuración no altera lo que ya
  está preparado; modificar la orden crea una versión nueva que toma la configuración del día.
- **La transición EnRevision → Aprobada:** `registrar_aprobacion_orden_pago` lleva la orden a
  «aprobada» cuando las identidades humanas distintas que aprobaron la versión vigente llegan a la
  cantidad de esa versión. El evento de la aprobación dice cuántas van, cuántas hacen falta y el
  estado que queda.
- **P3 69:** una aprobación repetida se reconoce también por la identidad *actual* de las cuentas
  que ya aprobaron, no solo por la guardada: si soporte corrige después el vínculo de una cuenta
  con su identidad, la otra cuenta del mismo humano no suma una segunda aprobación.

**Lo que no hace.** No decide qué órdenes necesitan aprobación (decisión 1: hasta entonces toda
orden pasa por revisión), ni si el administrador que además es consejero puede aprobar (decisión
6: hoy la separación es por orden, quien preparó no aprueba esa orden), ni rutas: `cem_app` sigue
sin permisos sobre las órdenes. No asigna aprobadores ni aprueba nada.

**Upgrade** se niega si ya hay órdenes: no hay cómo saber cuántas aprobaciones exigía cada versión.
**Downgrade** se niega (`downgrade_con_datos`) si hay órdenes, configuraciones o mandatos con
`egresos.aprobar_orden`: bajar los dejaría fuera de la guarda o sin su cantidad.

Revisión: 0046
Anterior: 0045
"""

from alembic import op
from sqlalchemy import text

revision = "0046"
down_revision = "0045"
branch_labels = None
depends_on = None

# Decisión 2 del propietario (29-10-2026): sin configuración cargada, aprueba un consejero.
POR_DEFECTO = 1
FACULTAD = "egresos.aprobar_orden"
PAQUETE = "miembro_consejo"

GUARDA = "nucleo.negar_facultad_sin_decidir()"
NUEVA_VERSION = (
    "nucleo.nueva_version_orden_pago(uuid, uuid, integer, bigint, text, text, text, text, boolean,"
    " uuid, uuid)"
)
APROBAR = "nucleo.registrar_aprobacion_orden_pago(uuid, integer, bytea, uuid, text, uuid)"
FIJAR = "nucleo.fijar_aprobaciones_orden_pago(smallint, date, uuid, uuid, text)"
REQUERIDAS = "nucleo.aprobaciones_requeridas_orden_pago(uuid)"

# Cada cambio, sobre el texto exacto de la función de 0044 o 0045: (antes, después).
CAMBIOS = {
    GUARDA: [(f"('{FACULTAD}', 'liquidacion.aprobar'", "('liquidacion.aprobar'")],
    NUEVA_VERSION: [
        (
            "huella, preparada_por, identidad_preparo)",
            "huella, preparada_por, identidad_preparo,\n       aprobaciones_requeridas)",
        ),
        (
            "usa_reserva, h, actor,\n            identidad);",
            "usa_reserva, h, actor,\n            identidad,\n"
            "            nucleo.aprobaciones_requeridas_orden_pago(c));",
        ),
    ],
    APROBAR: [
        (
            "    a uuid;\n  begin",
            "    a uuid;\n    aprobaron integer;\n    requeridas smallint;\n  begin",
        ),
        # P3 69: también por la identidad actual de las cuentas que ya aprobaron.
        (
            "and (x.identidad_id = identidad or x.persona_id = actor)) then",
            "and (x.identidad_id = identidad or x.persona_id = actor\n"
            "                    or nucleo.identidad_humana_de(c, x.persona_id) = identidad)) then",
        ),
        (
            """    returning id into a;
    perform nucleo.auditar_orden_pago(c, 'consorcio.orden_pago.aprobar', orden, actor, null,
      jsonb_build_object('version', version_vista, 'huella', encode(huella_vista, 'hex')));""",
            """    returning id into a;
    -- Decisión 2 (29-10-2026): aprobada cuando las identidades humanas distintas llegan a la
    -- cantidad que guardó la versión al prepararse.
    select count(distinct x.identidad_id) into aprobaron from nucleo.orden_pago_aprobacion x
     where x.orden_id = orden and x.version = version_vista;
    select x.aprobaciones_requeridas into requeridas from nucleo.orden_pago_version x
     where x.orden_id = orden and x.numero = version_vista;
    if aprobaron >= requeridas then
      update nucleo.orden_pago set estado = 'aprobada', actualizada_en = now() where id = orden;
    end if;
    perform nucleo.auditar_orden_pago(c, 'consorcio.orden_pago.aprobar', orden, actor,
      jsonb_build_object('estado', o.estado),
      jsonb_build_object('version', version_vista, 'huella', encode(huella_vista, 'hex'),
                         'aprobaciones', aprobaron, 'requeridas', requeridas,
                         'estado', case when aprobaron >= requeridas then 'aprobada'
                                        else 'en_revision' end));""",
        ),
    ],
}

UPGRADE = f"""
do $$
begin
  if exists (select 1 from nucleo.orden_pago) then
    raise exception '0046 no sube con órdenes de pago: no se sabe cuántas aprobaciones exigía cada'
      ' versión' using errcode = 'P0001', hint = 'upgrade_con_datos';
  end if;
end
$$;

create table nucleo.aprobaciones_orden_pago (
  consorcio_id   uuid not null references nucleo.consorcio (id),
  id             uuid not null default plataforma.uuid_v7(),
  cantidad       smallint not null check (cantidad between 1 and 2),
  vigente_desde  date not null,
  decision_id    uuid not null,
  creada_en      timestamptz not null default now(),
  primary key (id),
  unique (consorcio_id, vigente_desde),
  foreign key (consorcio_id, decision_id) references nucleo.decision_formal (consorcio_id, id)
);
alter table nucleo.aprobaciones_orden_pago enable row level security;
alter table nucleo.aprobaciones_orden_pago force row level security;
create policy por_consorcio on nucleo.aprobaciones_orden_pago
  using (consorcio_id = plataforma.consorcio_actual())
  with check (consorcio_id = plataforma.consorcio_actual());
create trigger historia_aprobaciones_orden_pago before update or delete
  on nucleo.aprobaciones_orden_pago
  for each row execute function nucleo.negar_cambio_historia_orden_pago();
create trigger historia_aprobaciones_orden_pago_truncate before truncate
  on nucleo.aprobaciones_orden_pago
  for each statement execute function nucleo.negar_cambio_historia_orden_pago();

alter table nucleo.orden_pago_version
  add column aprobaciones_requeridas smallint not null
  check (aprobaciones_requeridas between 1 and 2);

-- La cantidad vigente hoy en el consorcio; sin configuración, la de la decisión del propietario.
create function nucleo.aprobaciones_requeridas_orden_pago(c uuid)
  returns smallint
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select coalesce(
      (select a.cantidad from nucleo.aprobaciones_orden_pago a
        where a.consorcio_id = c and a.vigente_desde <= nucleo.hoy_del_consorcio(c)
        order by a.vigente_desde desc limit 1),
      {POR_DEFECTO}::smallint)
  $$;

create function nucleo.fijar_aprobaciones_orden_pago(cantidad_nueva smallint, desde date,
                                                     decision uuid, actor uuid,
                                                     origen_pedido text)
  returns uuid
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    nueva uuid;
  begin
    if c is null then
      raise exception 'sin consorcio en el ámbito' using errcode = '42501';
    end if;
    -- La carga solo el equipo (decisión del 29-10): ninguna persona del consorcio la cambia.
    if origen_pedido is distinct from 'soporte' then
      raise exception 'la cantidad de aprobaciones la carga solo el equipo (soporte)'
        using errcode = 'P0001', hint = 'origen_invalido';
    end if;
    if plataforma.persona_actual() is not null or actor is not null then
      raise exception 'el origen soporte no admite persona en la sesión ni actor'
        using errcode = 'P0001', hint = 'origen_invalido';
    end if;
    if cantidad_nueva is null or cantidad_nueva not between 1 and 2 then
      raise exception 'la cantidad de aprobaciones es 1 o 2' using errcode = 'P0001',
        hint = 'cantidad_invalida';
    end if;
    if desde is null or desde < nucleo.hoy_del_consorcio(c) then
      raise exception 'la cantidad rige desde hoy o más adelante, nunca hacia atrás'
        using errcode = 'P0001', hint = 'fecha_invalida';
    end if;
    if decision is null or not exists (
         select 1 from nucleo.decision_formal d where d.consorcio_id = c and d.id = decision) then
      raise exception 'la cantidad necesita una decisión de respaldo de este consorcio'
        using errcode = 'P0001', hint = 'decision_invalida';
    end if;
    if exists (select 1 from nucleo.aprobaciones_orden_pago a
                where a.consorcio_id = c and a.vigente_desde = desde) then
      raise exception 'ya hay una cantidad que rige desde %', desde using errcode = 'P0001',
        hint = 'vigencia_repetida';
    end if;
    insert into nucleo.aprobaciones_orden_pago (consorcio_id, cantidad, vigente_desde, decision_id)
    values (c, cantidad_nueva, desde, decision)
    returning id into nueva;
    return nueva;
  end
  $$;
revoke all on function {REQUERIDAS} from public;
revoke all on function {FIJAR} from public;
grant execute on function {FIJAR} to cem_app;
"""

DOWNGRADE = f"""
do $guarda$
begin
  if exists (select 1 from nucleo.orden_pago)
     or exists (select 1 from nucleo.aprobaciones_orden_pago)
     or exists (select 1 from nucleo.facultad_otorgada where facultad = '{FACULTAD}') then
    raise exception 'el downgrade de 0046 se niega: hay órdenes, cantidades de aprobación o'
      ' mandatos con {FACULTAD}' using errcode = 'P0001', hint = 'downgrade_con_datos';
  end if;
end
$guarda$;
delete from nucleo.paquete_facultad where tipo = '{PAQUETE}' and facultad = '{FACULTAD}';
drop function {FIJAR};
"""

DOWNGRADE_FINAL = f"""
drop function {REQUERIDAS};
alter table nucleo.orden_pago_version drop column aprobaciones_requeridas;
drop table nucleo.aprobaciones_orden_pago;
"""


def _definicion(firma: str) -> str:
    return (
        op.get_bind()
        .execute(text("select pg_get_functiondef(cast(:f as regprocedure))"), {"f": firma})
        .scalar_one()
    )


def _cambiar(firma: str, pares: list[tuple[str, str]]) -> str:
    definicion = _definicion(firma)
    for antes, despues in pares:
        if definicion.count(antes) != 1:
            raise RuntimeError(f"0046: {firma} no tiene exactamente una vez {antes!r}")
        definicion = definicion.replace(antes, despues)
    return definicion


def upgrade() -> None:
    op.execute(UPGRADE)
    for firma, pares in CAMBIOS.items():
        op.execute(_cambiar(firma, pares))
    op.execute(
        f"insert into nucleo.paquete_facultad (tipo, facultad) values ('{PAQUETE}', '{FACULTAD}')"
    )


def downgrade() -> None:
    op.execute(DOWNGRADE)
    # Primero la guarda vuelve a negar la facultad, después las funciones dejan de usar la columna.
    for firma, pares in CAMBIOS.items():
        op.execute(_cambiar(firma, [(despues, antes) for antes, despues in reversed(pares)]))
    op.execute(DOWNGRADE_FINAL)
