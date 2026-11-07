"""Diferencia mal cargada: la corrige quien la registró (decisión 6 del propietario, 07-11-2026).

Como el pago observado (§8 y §9 del rediseño): una fila nueva con `corrige_a`, sin tocar la
anterior. Solo quien la registró, por la identidad guardada en el acto, y solo antes de que el
Consejo resuelva (`diferencia_ya_resuelta`). La fila corregida queda en la historia y deja de
contar: no se resuelve, no se coteja y no es la que muestra la ficha.

`registrar_diferencia_pago_manual` suma `corrige`, que hace los mismos controles que al registrar:
la evidencia, la fecha y que siga habiendo diferencia (`sin_diferencia`). Una por ejecución pasa a
ser un índice parcial: una sola sin `corrige_a`, y a cada fila la corrige una sola.

**Exportación económica (`0069`):** `diferencia_pago` suma `corrige_a`.

Downgrade: se niega (`downgrade_con_datos`) si hay alguna corrección.

Revisión: 0082
Anterior: 0081
"""

from alembic import op
from sqlalchemy import text

revision = "0082"
down_revision = "0081"
branch_labels = None
depends_on = None

FIRMA = "uuid, integer, bytea, text, date, numeric, text, text, uuid, text, uuid, text, uuid"
REGISTRAR = f"nucleo.registrar_diferencia_pago_manual({FIRMA})"
REGISTRAR_NUEVA = f"nucleo.registrar_diferencia_pago_manual({FIRMA}, uuid)"
HECHOS = "nucleo.hechos_de_pago_manual(uuid, nucleo.orden_pago)"
PARA_RESOLVER = "nucleo.diferencia_para_resolver(uuid, nucleo.orden_pago, uuid, uuid, uuid)"
CONSULTA = "nucleo.pagos_consulta_lectura(text, text[], integer, integer, text, uuid, text, uuid)"
COTEJO = "nucleo.pagos_del_cotejo(uuid)"
LECTURA = "nucleo.diferencias_pago_manual_lectura(uuid, text, uuid, text, uuid)"
EXPORTAR = "nucleo.exportar_pagos(uuid, uuid)"
ACCIONES = "nucleo.acciones_de_ordenes_lectura(uuid, uuid, text, uuid, interval)"
PARA_CORREGIR = "nucleo.diferencia_para_corregir(uuid, uuid, uuid)"
REGISTRO_LA = "nucleo.registro_la_diferencia(uuid, nucleo.orden_pago, uuid)"

TABLA = """
alter table nucleo.diferencia_pago_manual
  drop constraint diferencia_pago_manual_ejecucion_id_key,
  add column corrige_a uuid unique,
  add foreign key (consorcio_id, corrige_a)
    references nucleo.diferencia_pago_manual (consorcio_id, id);
create unique index diferencia_una_por_ejecucion on nucleo.diferencia_pago_manual (ejecucion_id)
  where corrige_a is null;
"""

FUNCIONES = f"""
-- Lo que registró quien pide, sin resolver y sin una corrección posterior.
create function {PARA_CORREGIR.split("(")[0]}(e uuid, corrige uuid, identidad uuid)
  returns nucleo.diferencia_pago_manual
  language plpgsql stable
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    f nucleo.diferencia_pago_manual;
  begin
    select * into f from nucleo.diferencia_pago_manual x
     where x.id = corrige and x.ejecucion_id = e
       and not exists (select 1 from nucleo.diferencia_pago_manual q where q.corrige_a = x.id);
    if not found then
      raise exception 'esa diferencia no es la vigente de esta orden' using errcode = 'P0001',
        hint = 'no_encontrado';
    end if;
    if f.identidad_registro <> identidad then
      raise exception 'corrige la diferencia quien la registró' using errcode = 'P0001',
        hint = 'no_es_quien_registro';
    end if;
    if f.resolucion is not null then
      raise exception 'esa diferencia ya está resuelta' using errcode = 'P0001',
        hint = 'diferencia_ya_resuelta';
    end if;
    return f;
  end
  $$;

create function {REGISTRO_LA.split("(")[0]}(c uuid, o nucleo.orden_pago, identidad uuid)
  returns boolean
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select exists (
      select 1 from nucleo.ejecucion_orden_pago e
        join nucleo.diferencia_pago_manual f
          on f.consorcio_id = e.consorcio_id and f.ejecucion_id = e.id
       where e.consorcio_id = c and e.orden_id = o.id and e.version = o.version_vigente
         and f.identidad_registro = identidad and f.resolucion is null
         and not exists (select 1 from nucleo.diferencia_pago_manual q where q.corrige_a = f.id))
  $$;
"""

REGISTRAR_CAMBIOS = (
    (
        "origen_pedido text, sesion uuid)\n RETURNS uuid",
        "origen_pedido text, sesion uuid, corrige uuid DEFAULT NULL::uuid)\n RETURNS uuid",
    ),
    (
        "    anterior := nucleo.exigir_sin_pago_vigente(e.id);\n",
        "    if corrige is null then\n"
        "      anterior := nucleo.exigir_sin_pago_vigente(e.id);\n"
        "    else\n"
        "      perform nucleo.diferencia_para_corregir(e.id, corrige, identidad);\n"
        "      anterior := nucleo.ultimo_pago_manual(e.id);\n"
        "    end if;\n",
    ),
    (
        "       fuera_de_termino, registrada_por, identidad_registro, sin_facultad)\n",
        "       fuera_de_termino, registrada_por, identidad_registro, sin_facultad, corrige_a)\n",
    ),
    (
        "            btrim(explicacion_nueva), tarde, actor, identidad, sin_facultad)\n",
        "            btrim(explicacion_nueva), tarde, actor, identidad, sin_facultad, corrige)\n",
    ),
    (
        "                         'diferencia', nueva, 'corrige_a', anterior.id,",
        "                         'diferencia', nueva, 'corrige_a', anterior.id,\n"
        "                         'corrige_diferencia', corrige,",
    ),
)
HECHOS_CAMBIOS = (
    (
        "    select * into dif from nucleo.diferencia_pago_manual x where x.ejecucion_id = e.id;\n",
        "    select * into dif from nucleo.diferencia_pago_manual x where x.ejecucion_id = e.id\n"
        "     order by x.paso desc limit 1;\n",
    ),
)
PARA_RESOLVER_CAMBIOS = (
    (
        "     where x.consorcio_id = c and x.id = diferencia and x.ejecucion_id = e.id;\n",
        "     where x.consorcio_id = c and x.id = diferencia and x.ejecucion_id = e.id\n"
        "       and not exists (select 1 from nucleo.diferencia_pago_manual q"
        " where q.corrige_a = x.id);\n",
    ),
)
CONSULTA_CAMBIOS = (
    (
        "             and e.version = o.version_vigente) f on true\n",
        "             and e.version = o.version_vigente\n"
        "           order by x.paso desc limit 1) f on true\n",
    ),
)
COTEJO_CAMBIOS = (
    (
        "     where f.consorcio_id = c\n",
        "     where f.consorcio_id = c\n"
        "       and not exists (select 1 from nucleo.diferencia_pago_manual q"
        " where q.corrige_a = f.id)\n",
    ),
)
LECTURA_CAMBIOS = (
    (" motivo_cierre text)\n", " motivo_cierre text, corrige_a uuid)\n"),
    ("             f.motivo_cierre\n", "             f.motivo_cierre, f.corrige_a\n"),
)
EXPORTAR_CAMBIOS = (
    (
        "                                'sin_facultad', f.sin_facultad,\n"
        "                                'registrada_por_nombre',",
        "                                'sin_facultad', f.sin_facultad,\n"
        "                                'corrige_a', f.corrige_a,\n"
        "                                'registrada_por_nombre',",
    ),
)
ACCIONES_CAMBIOS = (
    (
        " proveedor_cambiado boolean)\n",
        " proveedor_cambiado boolean, puede_corregir_diferencia boolean)\n",
    ),
    (
        "             prepara and nucleo.proveedor_cambio_tras_aprobar(c, o)\n",
        "             prepara and nucleo.proveedor_cambio_tras_aprobar(c, o),\n"
        "             ejecuta and nucleo.registro_la_diferencia(c, o, identidad)\n",
    ),
)

REEMPLAZOS = (
    (HECHOS, HECHOS_CAMBIOS),
    (PARA_RESOLVER, PARA_RESOLVER_CAMBIOS),
    (CONSULTA, CONSULTA_CAMBIOS),
    (COTEJO, COTEJO_CAMBIOS),
    (EXPORTAR, EXPORTAR_CAMBIOS),
)
OTRAS_LECTURAS = ((LECTURA, LECTURA_CAMBIOS), (ACCIONES, ACCIONES_CAMBIOS))

GUARDA_DOWNGRADE = """
lock table nucleo.diferencia_pago_manual in access exclusive mode;
do $$
begin
  if exists (select 1 from nucleo.diferencia_pago_manual where corrige_a is not null) then
    raise exception '0082 no baja con diferencias corregidas: se perdería la historia'
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
            raise RuntimeError(f"0082: {funcion} no tiene exactamente una vez {antes!r}")
        definicion = definicion.replace(antes, despues)
    return definicion


def _otra(desde: str, hasta: str, cambios, *, al_reves: bool = False) -> None:
    """Otra firma o lo que devuelve: se borra y se crea de nuevo, con su permiso."""
    definicion = _definicion(desde, cambios, al_reves=al_reves)
    op.execute(f"drop function {desde}")
    op.execute(definicion)
    op.execute(f"revoke all on function {hasta} from public")
    op.execute(f"grant execute on function {hasta} to cem_app")


def upgrade() -> None:
    op.execute(TABLA)
    op.execute(FUNCIONES)
    for funcion in (PARA_CORREGIR, REGISTRO_LA):
        op.execute(f"revoke all on function {funcion} from public")
    _otra(REGISTRAR, REGISTRAR_NUEVA, REGISTRAR_CAMBIOS)
    for funcion, cambios in REEMPLAZOS:
        op.execute(_definicion(funcion, cambios, al_reves=False))
    for funcion, cambios in OTRAS_LECTURAS:
        _otra(funcion, funcion, cambios)


def downgrade() -> None:
    op.execute(GUARDA_DOWNGRADE)
    for funcion, cambios in OTRAS_LECTURAS:
        _otra(funcion, funcion, cambios, al_reves=True)
    for funcion, cambios in REEMPLAZOS:
        op.execute(_definicion(funcion, cambios, al_reves=True))
    _otra(REGISTRAR_NUEVA, REGISTRAR, REGISTRAR_CAMBIOS, al_reves=True)
    for funcion in (REGISTRO_LA, PARA_CORREGIR):
        op.execute(f"drop function {funcion}")
    op.execute(
        "drop index nucleo.diferencia_una_por_ejecucion;"
        " alter table nucleo.diferencia_pago_manual drop column corrige_a,"
        " add constraint diferencia_pago_manual_ejecucion_id_key unique (ejecucion_id)"
    )
