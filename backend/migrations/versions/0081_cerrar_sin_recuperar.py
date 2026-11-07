"""Reclamo nunca recuperado: «Cerrar sin recuperar» (decisión 5 del propietario, 07-11-2026).

Un reclamo que nunca vuelve dejaba la orden en verificación para siempre: las salidas del Consejo
eran dar por pagado lo real o recuperar la plata. `cerrar_reclamo_sin_recuperar` es la tercera, con
motivo, gesto y la separación de conciliar (`diferencia_para_resolver`). La orden pasa a `fallida` y
la diferencia queda en la historia, con quién la cerró y por qué.

**No se imputa en la liquidación.** `importe_pagado_ejecucion` solo cuenta `pagada_por_lo_real` y
una orden `fallida` no es liquidable. Si esa plata se imputa o no lo decide el propietario con su
asesor.

`negar_cambio_diferencia_pago` deja completar el cierre una sola vez. Un cierre y una recuperación
se excluyen: lo niega una restricción de la tabla, además de la función.

**Exportación económica (`0069`):** `diferencia_pago` suma el cierre, con el motivo sin cuentas.

Downgrade: se niega (`downgrade_con_datos`) si hay un reclamo cerrado.

Revisión: 0081
Anterior: 0080
"""

from alembic import op
from sqlalchemy import text

revision = "0081"
down_revision = "0080"
branch_labels = None
depends_on = None

NEGAR = "nucleo.negar_cambio_diferencia_pago()"
HECHOS = "nucleo.hechos_de_pago_manual(uuid, nucleo.orden_pago)"
LECTURA = "nucleo.diferencias_pago_manual_lectura(uuid, text, uuid, text, uuid)"
EXPORTAR = "nucleo.exportar_pagos(uuid, uuid)"
CERRAR = "nucleo.cerrar_reclamo_sin_recuperar(uuid, uuid, text, uuid, text, uuid)"

COLUMNAS = """
alter table nucleo.diferencia_pago_manual
  add column cerrada_por uuid references nucleo.persona (id),
  add column identidad_cerro uuid,
  add column cerrada_en timestamptz,
  -- Texto libre: no va a la auditoría.
  add column motivo_cierre text check (length(btrim(motivo_cierre)) between 1 and 300),
  add foreign key (consorcio_id, identidad_cerro)
    references nucleo.identidad_humana (consorcio_id, id),
  add constraint diferencia_cierre_completo check (
    (cerrada_en is null and cerrada_por is null and identidad_cerro is null
     and motivo_cierre is null)
    or (resolucion = 'reclamo' and recuperada_en is null and cerrada_en is not null
        and cerrada_por is not null and identidad_cerro is not null
        and motivo_cierre is not null));
create trigger motivo_cierre_sin_cuenta before update of motivo_cierre
  on nucleo.diferencia_pago_manual
  for each row when (new.motivo_cierre is not null)
  execute function nucleo.negar_cuenta_en_motivo('motivo_cierre');
"""

FUNCION = f"""
create function {CERRAR.split("(")[0]}(orden uuid, diferencia uuid, motivo text, actor uuid,
                                                   origen_pedido text, sesion uuid)
  returns void
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    identidad uuid;
    o nucleo.orden_pago;
    e nucleo.ejecucion_orden_pago;
    f nucleo.diferencia_pago_manual;
  begin
    identidad := nucleo.exigir_acto_economico(c, actor, sesion, 'egresos.aprobar_orden',
                                              origen_pedido);
    o := nucleo.orden_pago_bloqueada(c, orden);
    f := nucleo.diferencia_para_resolver(c, o, diferencia, actor, identidad);
    e := nucleo.ejecucion_manual_abierta(c, o);
    if f.resolucion is distinct from 'reclamo' then
      raise exception 'esa diferencia no tiene un reclamo abierto' using errcode = 'P0001',
        hint = 'estado_invalido';
    end if;
    update nucleo.diferencia_pago_manual
       set cerrada_por = actor, identidad_cerro = identidad, cerrada_en = clock_timestamp(),
           motivo_cierre = btrim(motivo)
     where id = f.id;
    insert into nucleo.resultado_ejecucion_orden_pago
      (consorcio_id, ejecucion_id, resultado, origen, motivo)
    values (c, e.id, 'fallida', 'manual', 'Se cerró el reclamo sin recuperar la plata.');
    update nucleo.orden_pago set estado = 'fallida', actualizada_en = now() where id = orden;
    perform nucleo.auditar_orden_pago(c, 'consorcio.orden_pago.cerrar_reclamo_sin_recuperar',
      orden, actor, jsonb_build_object('estado', o.estado),
      jsonb_build_object('estado', 'fallida', 'version', e.version, 'ejecucion', e.id,
                         'diferencia', f.id));
  end
  $$;
revoke all on function {CERRAR} from public;
grant execute on function {CERRAR} to cem_app;
"""

NEGAR_CAMBIOS = (
    (
        "      array['recuperada_por', 'identidad_recupero', 'recuperada_en'];\n",
        "      array['recuperada_por', 'identidad_recupero', 'recuperada_en'];\n"
        "    cierre_c constant text[] :=\n"
        "      array['cerrada_por', 'identidad_cerro', 'cerrada_en', 'motivo_cierre'];\n",
    ),
    (
        "    if (to_jsonb(old) - resolucion_c - recuperacion_c)\n"
        "       is distinct from (to_jsonb(new) - resolucion_c - recuperacion_c)\n",
        "    if (to_jsonb(old) - resolucion_c - recuperacion_c - cierre_c)\n"
        "       is distinct from (to_jsonb(new) - resolucion_c - recuperacion_c - cierre_c)\n"
        "       or (old.cerrada_en is not null\n"
        "           and (old.cerrada_por, old.identidad_cerro, old.cerrada_en, old.motivo_cierre)\n"
        "               is distinct from\n"
        "               (new.cerrada_por, new.identidad_cerro, new.cerrada_en,"
        " new.motivo_cierre))\n",
    ),
)
HECHOS_CAMBIOS = (
    (
        "        and dif.recuperada_en is null;\n",
        "        and dif.recuperada_en is null and dif.cerrada_en is null;\n",
    ),
)
LECTURA_CAMBIOS = (
    (
        " recuperada_en timestamp with time zone)\n",
        " recuperada_en timestamp with time zone, cerrada_en timestamp with time zone,"
        " motivo_cierre text)\n",
    ),
    (
        "             f.resolucion, f.resuelta_por, f.resuelta_en, f.recuperada_en\n",
        "             f.resolucion, f.resuelta_por, f.resuelta_en, f.recuperada_en, f.cerrada_en,\n"
        "             f.motivo_cierre\n",
    ),
)
EXPORTAR_CAMBIOS = (
    (
        "                                'recuperada_en', f.recuperada_en)\n",
        "                                'recuperada_en', f.recuperada_en,\n"
        "                                'cerrada_por_nombre',"
        " nucleo.nombre_para_exportar(c, f.cerrada_por, hoy),\n"
        "                                'cerrada_en', f.cerrada_en,\n"
        "                                'motivo_cierre',"
        " nucleo.texto_sin_cuenta(f.motivo_cierre))\n",
    ),
)

GUARDA_DOWNGRADE = """
lock table nucleo.diferencia_pago_manual in access exclusive mode;
do $$
begin
  if exists (select 1 from nucleo.diferencia_pago_manual where cerrada_en is not null) then
    raise exception '0081 no baja con reclamos cerrados: se perdería quién los cerró y por qué'
      using errcode = 'P0001', hint = 'downgrade_con_datos';
  end if;
end
$$;
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
            raise RuntimeError(f"0081: {funcion} no tiene exactamente una vez {antes!r}")
        definicion = definicion.replace(antes, despues)
    return definicion


def _lectura(*, al_reves: bool = False) -> None:
    """Cambia lo que devuelve: se borra y se crea de nuevo, con su permiso."""
    definicion = _definicion(LECTURA, LECTURA_CAMBIOS, al_reves=al_reves)
    op.execute(f"drop function {LECTURA}")
    op.execute(definicion)
    op.execute(f"revoke all on function {LECTURA} from public")
    op.execute(f"grant execute on function {LECTURA} to cem_app")


def upgrade() -> None:
    op.execute(COLUMNAS)
    op.execute(_definicion(NEGAR, NEGAR_CAMBIOS, al_reves=False))
    op.execute(FUNCION)
    op.execute(_definicion(HECHOS, HECHOS_CAMBIOS, al_reves=False))
    op.execute(_definicion(EXPORTAR, EXPORTAR_CAMBIOS, al_reves=False))
    _lectura()


def downgrade() -> None:
    op.execute(GUARDA_DOWNGRADE)
    _lectura(al_reves=True)
    op.execute(_definicion(EXPORTAR, EXPORTAR_CAMBIOS, al_reves=True))
    op.execute(_definicion(HECHOS, HECHOS_CAMBIOS, al_reves=True))
    op.execute(f"drop function {CERRAR}")
    op.execute(_definicion(NEGAR, NEGAR_CAMBIOS, al_reves=True))
    op.execute("drop trigger motivo_cierre_sin_cuenta on nucleo.diferencia_pago_manual")
    op.execute(
        "alter table nucleo.diferencia_pago_manual drop column motivo_cierre,"
        " drop column cerrada_en, drop column identidad_cerro, drop column cerrada_por"
    )
