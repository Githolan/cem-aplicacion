"""Deshacer el cotejo: una anulación con motivo, sin borrar (decisión 7 del propietario,
07-11-2026).

`anular_cotejo` tiene las reglas de cotejar: la facultad, el gesto, las separaciones y el mismo
turno por consorcio. La fila queda con quién anuló, cuándo y por qué: `negar_cambio_cotejo_pago`
deja completar la anulación una sola vez y ningún otro cambio. Un pago, una diferencia y un
movimiento tienen un solo cotejo vigente: las restricciones pasan a índices parciales.

**Un cotejo anulado no vuelve a tildarse solo.** Reimportar el extracto vuelve a encontrar la misma
línea: `cotejar_por_extracto` no lo coteja, aunque la API se lo pida, y la pantalla lo cuenta aparte
(`anulados`). Queda en lo que falta cotejar y una persona lo tilda a mano, con las reglas de
siempre.

**Exportación económica (`0069`):** `cotejo` suma la anulación, con el motivo sin cuentas.

Downgrade: se niega (`downgrade_con_datos`) si hay un cotejo anulado.

Revisión: 0083
Anterior: 0082
"""

from alembic import op
from sqlalchemy import text

revision = "0083"
down_revision = "0082"
branch_labels = None
depends_on = None

FACULTAD = "egresos.aprobar_orden"
"""La de `egresos.conciliar_pago`, como en `0074`."""

PAGOS = "nucleo.pagos_del_cotejo(uuid)"
COMPARAR = "nucleo.pagos_para_comparar_lectura(text[], date, date, uuid, text, uuid)"
IMPORTAR = (
    "nucleo.importar_extracto(text, date, date, jsonb, text, text, text, text, uuid, text, uuid)"
)
POR_EXTRACTO = "nucleo.cotejar_por_extracto(jsonb, uuid, text, uuid)"
EXPORTAR = "nucleo.exportar_pagos(uuid, uuid)"
ANULAR = "nucleo.anular_cotejo(uuid, text, uuid, text, uuid)"
COTEJADOS = "nucleo.cotejados_lectura(uuid, integer, uuid, text, uuid)"

TABLA = """
alter table nucleo.cotejo_pago
  drop constraint cotejo_pago_consorcio_id_pago_id_key,
  drop constraint cotejo_pago_consorcio_id_diferencia_id_key,
  drop constraint cotejo_pago_movimiento_id_key,
  add column anulado_por uuid references nucleo.persona (id),
  add column identidad_anulo uuid,
  add column anulado_en timestamptz,
  -- Texto libre: no va a la auditoría.
  add column motivo_anulacion text check (length(btrim(motivo_anulacion)) between 1 and 300),
  add foreign key (consorcio_id, identidad_anulo)
    references nucleo.identidad_humana (consorcio_id, id),
  add constraint cotejo_anulacion_completa
    check (num_nonnulls(anulado_por, identidad_anulo, anulado_en, motivo_anulacion) in (0, 4));
create unique index cotejo_vigente_de_pago on nucleo.cotejo_pago (consorcio_id, pago_id)
  where anulado_en is null;
create unique index cotejo_vigente_de_diferencia on nucleo.cotejo_pago (consorcio_id, diferencia_id)
  where anulado_en is null;
create unique index cotejo_vigente_de_movimiento on nucleo.cotejo_pago (movimiento_id)
  where anulado_en is null;
create trigger motivo_anulacion_sin_cuenta before update of motivo_anulacion
  on nucleo.cotejo_pago
  for each row when (new.motivo_anulacion is not null)
  execute function nucleo.negar_cuenta_en_motivo('motivo_anulacion');

create function nucleo.negar_cambio_cotejo_pago()
  returns trigger
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    anulacion constant text[] :=
      array['anulado_por', 'identidad_anulo', 'anulado_en', 'motivo_anulacion'];
  begin
    if tg_op = 'DELETE' or old.anulado_en is not null
       or (to_jsonb(old) - anulacion) is distinct from (to_jsonb(new) - anulacion) then
      raise exception 'la historia de una orden de pago solo se agrega'
        using errcode = 'P0001', hint = 'historia_inmutable';
    end if;
    return new;
  end
  $$;
drop trigger historia_cotejo_pago on nucleo.cotejo_pago;
create trigger historia_cotejo_pago before update or delete on nucleo.cotejo_pago
  for each row execute function nucleo.negar_cambio_cotejo_pago();
revoke all on function nucleo.negar_cambio_cotejo_pago() from public;
"""

FUNCIONES = f"""
create function {ANULAR.split("(")[0]}(pago uuid, motivo text, actor uuid, origen_pedido text,
                                     sesion uuid)
  returns void
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    identidad uuid;
    f record;
  begin
    identidad := nucleo.exigir_acto_economico(c, actor, sesion, '{FACULTAD}', origen_pedido);
    perform nucleo.bloquear_cotejo(c);
    select * into f from nucleo.pagos_del_cotejo(c) x where x.pago_id = pago;
    if f.pago_id is null then
      raise exception 'ese pago no existe en este consorcio' using errcode = 'P0001',
        hint = 'no_encontrado';
    end if;
    perform nucleo.exigir_no_ejecuto(c, f.ejecucion_id, actor, identidad);
    perform nucleo.exigir_no_preparo(c, f.orden_id, actor, identidad);
    if not f.cotejado then
      raise exception 'ese pago no tiene un cotejo vigente' using errcode = 'P0001',
        hint = 'estado_invalido';
    end if;
    update nucleo.cotejo_pago t
       set anulado_por = actor, identidad_anulo = identidad, anulado_en = clock_timestamp(),
           motivo_anulacion = btrim(motivo)
     where t.consorcio_id = c and coalesce(t.pago_id, t.diferencia_id) = pago
       and t.anulado_en is null;
    perform nucleo.auditar_orden_pago(c, 'consorcio.orden_pago.anular_cotejo', f.orden_id, actor,
      null, jsonb_build_object('pago', pago));
  end
  $$;

-- Lo cotejado y vigente. Por `despues`, como lo que falta cotejar.
create function {COTEJADOS.split("(")[0]}(despues uuid, limite integer, actor uuid,
                                         origen_pedido text, sesion uuid)
  returns table (pago_id uuid, orden_id uuid, proveedor text, concepto text,
                 numero_operacion text, fecha_operacion date, importe_centavos bigint,
                 moneda text, puede_cotejar boolean, origen text,
                 cotejado_en timestamptz)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    identidad uuid;
  begin
    identidad := nucleo.exigir_acto_sin_gesto(c, actor, sesion, '{FACULTAD}', origen_pedido);
    return query
      select f.pago_id, f.orden_id, f.proveedor, f.concepto, f.numero_operacion,
             f.fecha_operacion, f.importe_centavos, f.moneda,
             nucleo.admite_cotejar(c, f.orden_id, f.ejecucion_id, actor, identidad),
             t.origen, t.cotejado_en
        from nucleo.pagos_del_cotejo(c) f
        join nucleo.cotejo_pago t
          on t.consorcio_id = c and coalesce(t.pago_id, t.diferencia_id) = f.pago_id
         and t.anulado_en is null
       where despues is null or f.pago_id > despues
       order by f.pago_id
       limit limite;
  end
  $$;
"""

PAGOS_CAMBIOS = (
    (" moneda text, conforme boolean, cotejado boolean)\n",
     " moneda text, conforme boolean, cotejado boolean, anulado boolean)\n"),
    (
        "                    where t.consorcio_id = c and t.pago_id = p.id)\n",
        "                    where t.consorcio_id = c and t.pago_id = p.id"
        " and t.anulado_en is null),\n"
        "           exists (select 1 from nucleo.cotejo_pago t\n"
        "                    where t.consorcio_id = c and t.pago_id = p.id"
        " and t.anulado_en is not null)\n",
    ),
    (
        "                    where t.consorcio_id = c and t.diferencia_id = f.id)\n",
        "                    where t.consorcio_id = c and t.diferencia_id = f.id"
        " and t.anulado_en is null),\n"
        "           exists (select 1 from nucleo.cotejo_pago t\n"
        "                    where t.consorcio_id = c and t.diferencia_id = f.id"
        " and t.anulado_en is not null)\n",
    ),
)  # fmt: skip
COMPARAR_CAMBIOS = (
    (" conforme boolean, cotejado boolean, puede_cotejar boolean)\n",
     " conforme boolean, cotejado boolean, anulado boolean, puede_cotejar boolean)\n"),
    ("f.moneda, f.conforme, f.cotejado,\n", "f.moneda, f.conforme, f.cotejado, f.anulado,\n"),
)  # fmt: skip
IMPORTAR_CAMBIOS = (
    (
        "          on t.consorcio_id = m.consorcio_id and t.movimiento_id = m.id\n",
        "          on t.consorcio_id = m.consorcio_id and t.movimiento_id = m.id\n"
        "         and t.anulado_en is null\n",
    ),
)
POR_EXTRACTO_CAMBIOS = (
    (
        "      if not f.cotejado then\n",
        "      -- Una persona anuló ese cotejo: no vuelve solo. Se tilda a mano.\n"
        "      if not f.cotejado and not f.anulado then\n",
    ),
)
EXPORTAR_CAMBIOS = (
    (
        "                                'cotejado_en', t.cotejado_en)\n",
        "                                'cotejado_en', t.cotejado_en,\n"
        "                                'anulado_por_nombre',"
        " nucleo.nombre_para_exportar(c, t.anulado_por, hoy),\n"
        "                                'anulado_en', t.anulado_en,\n"
        "                                'motivo_anulacion',"
        " nucleo.texto_sin_cuenta(t.motivo_anulacion))\n",
    ),
)

GUARDA_DOWNGRADE = """
lock table nucleo.cotejo_pago in access exclusive mode;
do $$
begin
  if exists (select 1 from nucleo.cotejo_pago where anulado_en is not null) then
    raise exception '0083 no baja con cotejos anulados: se perdería quién los anuló y por qué'
      using errcode = 'P0001', hint = 'downgrade_con_datos';
  end if;
end
$$;
"""

TABLA_ABAJO = """
drop trigger historia_cotejo_pago on nucleo.cotejo_pago;
create trigger historia_cotejo_pago before update or delete on nucleo.cotejo_pago
  for each row execute function nucleo.negar_cambio_historia_orden_pago();
drop function nucleo.negar_cambio_cotejo_pago();
drop trigger motivo_anulacion_sin_cuenta on nucleo.cotejo_pago;
drop index nucleo.cotejo_vigente_de_pago;
drop index nucleo.cotejo_vigente_de_diferencia;
drop index nucleo.cotejo_vigente_de_movimiento;
alter table nucleo.cotejo_pago
  drop column anulado_por, drop column identidad_anulo, drop column anulado_en,
  drop column motivo_anulacion,
  add constraint cotejo_pago_consorcio_id_pago_id_key unique (consorcio_id, pago_id),
  add constraint cotejo_pago_consorcio_id_diferencia_id_key unique (consorcio_id, diferencia_id),
  add constraint cotejo_pago_movimiento_id_key unique (movimiento_id);
"""


def _definicion(funcion: str, cambios, *, al_reves: bool) -> str:
    definicion = (
        op.get_bind()
        .execute(text("select pg_get_functiondef(cast(:f as regprocedure))"), {"f": funcion})
        .scalar_one()
    )
    for antes, despues in cambios:
        if al_reves:
            antes, despues = despues, antes
        if definicion.count(antes) != 1:
            raise RuntimeError(f"0083: {funcion} no tiene exactamente una vez {antes!r}")
        definicion = definicion.replace(antes, despues)
    return definicion


def _otra_salida(funcion: str, cambios, *, grant: bool, al_reves: bool = False) -> None:
    """Otras columnas de salida: se borra y se crea de nuevo, con su permiso."""
    definicion = _definicion(funcion, cambios, al_reves=al_reves)
    op.execute(f"drop function {funcion}")
    op.execute(definicion)
    op.execute(f"revoke all on function {funcion} from public")
    if grant:
        op.execute(f"grant execute on function {funcion} to cem_app")


def upgrade() -> None:
    op.execute(TABLA)
    _otra_salida(PAGOS, PAGOS_CAMBIOS, grant=False)
    _otra_salida(COMPARAR, COMPARAR_CAMBIOS, grant=True)
    for funcion, cambios in (
        (IMPORTAR, IMPORTAR_CAMBIOS),
        (POR_EXTRACTO, POR_EXTRACTO_CAMBIOS),
        (EXPORTAR, EXPORTAR_CAMBIOS),
    ):
        op.execute(_definicion(funcion, cambios, al_reves=False))
    op.execute(FUNCIONES)
    for funcion in (ANULAR, COTEJADOS):
        op.execute(f"revoke all on function {funcion} from public")
        op.execute(f"grant execute on function {funcion} to cem_app")


def downgrade() -> None:
    op.execute(GUARDA_DOWNGRADE)
    for funcion in (ANULAR, COTEJADOS):
        op.execute(f"drop function {funcion}")
    for funcion, cambios in (
        (EXPORTAR, EXPORTAR_CAMBIOS),
        (POR_EXTRACTO, POR_EXTRACTO_CAMBIOS),
        (IMPORTAR, IMPORTAR_CAMBIOS),
    ):
        op.execute(_definicion(funcion, cambios, al_reves=True))
    _otra_salida(COMPARAR, COMPARAR_CAMBIOS, grant=True, al_reves=True)
    _otra_salida(PAGOS, PAGOS_CAMBIOS, grant=False, al_reves=True)
    op.execute(TABLA_ABAJO)
