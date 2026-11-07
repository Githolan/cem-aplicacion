"""Un solo pagador, cerrado en la base, y «Tomar el pago» (decisión 1 del propietario, 07-11-2026).

**Segundo pagador.** `circuito_pagos.ofrece_pagar` ya no le ofrecía pagar a otro ejecutor, pero la
base seguía aceptando `pago-manual/cuenta`: la §1 del rediseño dice que decide el servidor.
`cuenta_para_pago_manual` niega (`pago_en_curso`) mientras otra persona tenga el pago: vio la cuenta
de esta ejecución, no declaró después y nadie se lo tomó. `admite_pagar` dice lo mismo.

**Tomar el pago.** Si quien vio la cuenta desaparece sin perder la facultad, el otro ejecutor
quedaba en `espera_comprobante` sin salida: `admite_declarar` daba falso. Pasado el plazo desde la
última apertura, `tomar_pago_manual` deja el evento en `nucleo.toma_pago_manual`, con gesto y con la
separación de pagar. El plazo llega de la API (`CEM_PAGOS_HORAS_TOMAR`, 24 horas por defecto): la
base no tiene ningún número fijo. Quien toma dijo antes que no se transfirió (`no_se_transfirio`, en
el contrato).

**Tomar no da la cuenta.** Solo suelta a quien la vio, que deja de contar para `cuenta_vista`, para
declarar y para confirmar (`apertura_tomada`). Así, con la aprobación ya vencida, quien tomó declara
que no se pagó y la orden sale. Abrir la cuenta sigue siendo su acto, con sus barreras.

**La verdad gana.** Quien vio la cuenta y vuelve con el comprobante todavía registra: si transfirió,
tiene que poder decirlo. Las dos personas quedan separadas de conciliar (`exigir_no_ejecuto`).

**Sin registro.** Con un registro observado no se toma: la plata pudo haber salido, y lo corrige
quien lo registró.

**Exportación económica (`0069`):** `exportar_pagos` suma `toma_pago`.

Downgrade: se niega (`downgrade_con_datos`) si hay alguna toma. Sin eso, vuelve a las definiciones
anteriores.

Revisión: 0078
Anterior: 0077
"""

from alembic import op
from sqlalchemy import text

revision = "0078"
down_revision = "0077"
branch_labels = None
depends_on = None

CUENTA = "nucleo.cuenta_para_pago_manual(uuid, integer, bytea, uuid, text, uuid)"
DECLARAR = "nucleo.declarar_pago_no_realizado(uuid, integer, text, uuid, text, uuid)"
CONFIRMAR = "nucleo.confirmar_pago_no_realizado(uuid, uuid, uuid, text, uuid)"
HECHOS = "nucleo.hechos_de_pago_manual(uuid, nucleo.orden_pago)"
ADMITE_PAGAR = "nucleo.admite_pagar(uuid, nucleo.orden_pago, uuid, uuid)"
ADMITE_DECLARAR = "nucleo.admite_declarar(uuid, nucleo.orden_pago, uuid, uuid)"
SIN_SALIDA = "nucleo.pago_sin_salida(uuid, nucleo.orden_pago)"
FALTA = "nucleo.falta_que_declare(uuid, nucleo.orden_pago, uuid)"
ACCIONES = "nucleo.acciones_de_ordenes_lectura(uuid, uuid, text, uuid)"
ACCIONES_NUEVA = "nucleo.acciones_de_ordenes_lectura(uuid, uuid, text, uuid, interval)"
EXPORTAR = "nucleo.exportar_pagos(uuid, uuid)"

TABLA = """
create table nucleo.toma_pago_manual (
  consorcio_id    uuid not null,
  id              uuid not null default plataforma.uuid_v7(),
  ejecucion_id    uuid not null,
  tomado_por      uuid not null references nucleo.persona (id),
  identidad_tomo  uuid not null,
  tomada_en       timestamptz not null default clock_timestamp(),
  paso            bigint not null default nextval('nucleo.paso_pago_manual'),
  primary key (id),
  foreign key (consorcio_id, ejecucion_id)
    references nucleo.ejecucion_orden_pago (consorcio_id, id),
  foreign key (consorcio_id, identidad_tomo)
    references nucleo.identidad_humana (consorcio_id, id)
);
alter table nucleo.toma_pago_manual enable row level security;
alter table nucleo.toma_pago_manual force row level security;
create policy por_consorcio on nucleo.toma_pago_manual for select
  using (consorcio_id = plataforma.consorcio_actual());
revoke all on nucleo.toma_pago_manual from public, cem_app;
create trigger historia_toma_pago_manual before update or delete on nucleo.toma_pago_manual
  for each row execute function nucleo.negar_cambio_historia_orden_pago();
create trigger historia_toma_pago_manual_truncate before truncate on nucleo.toma_pago_manual
  for each statement execute function nucleo.negar_cambio_historia_orden_pago();
"""

FUNCIONES = """
-- Si otra persona tomó el pago después de esa apertura: ya no la ata.
create function nucleo.apertura_tomada(a nucleo.apertura_cuenta_pago_manual)
  returns boolean
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select exists (select 1 from nucleo.toma_pago_manual t
                    where t.ejecucion_id = a.ejecucion_id and t.paso > a.paso
                      and t.identidad_tomo <> a.identidad_abrio)
  $$;

-- Cuándo vio la cuenta, por última vez, otra persona que todavía tiene el pago: no declaró después
-- y nadie se lo tomó. Nula si nadie más lo tiene.
create function nucleo.otro_tiene_el_pago(e uuid, identidad uuid)
  returns timestamptz
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select max(a.abierta_en) from nucleo.apertura_cuenta_pago_manual a
     where a.ejecucion_id = e and a.identidad_abrio <> identidad
       and not nucleo.apertura_tomada(a)
       and not exists (select 1 from nucleo.declaracion_pago_no_realizado y
                        where y.ejecucion_id = e and y.identidad_declaro = a.identidad_abrio
                          and y.paso > a.paso)
  $$;

-- Desde cuándo espera a otra persona quien podría tomar el pago; nula si no le toca esperar.
create function nucleo.espera_el_pago_desde(c uuid, o nucleo.orden_pago, actor uuid, identidad uuid)
  returns timestamptz
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    e nucleo.ejecucion_orden_pago;
  begin
    e := nucleo.ejecucion_manual_abierta(c, o);
    perform nucleo.exigir_no_aprobo(c, o.id, e.version, actor, identidad);
    perform nucleo.exigir_sin_pago_vigente(e.id);
    if exists (select 1 from nucleo.pago_manual x where x.ejecucion_id = e.id) then
      return null;
    end if;
    return nucleo.otro_tiene_el_pago(e.id, identidad);
  exception when raise_exception then
    return null;
  end
  $$;

create function nucleo.tomar_pago_manual(orden uuid, version_vista integer, huella_vista bytea,
                                         plazo interval, actor uuid, origen_pedido text,
                                         sesion uuid)
  returns uuid
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    identidad uuid;
    e nucleo.ejecucion_orden_pago;
    o nucleo.orden_pago;
    desde timestamptz;
    toma uuid;
  begin
    identidad := nucleo.exigir_acto_economico(c, actor, sesion, 'egresos.ejecutar_pago',
                                              origen_pedido);
    e := nucleo.orden_para_pago_manual(c, orden, version_vista, huella_vista);
    select * into o from nucleo.orden_pago x where x.consorcio_id = c and x.id = orden;
    perform nucleo.exigir_no_aprobo(c, orden, e.version, actor, identidad);
    perform nucleo.exigir_sin_pago_vigente(e.id);
    desde := nucleo.espera_el_pago_desde(c, o, actor, identidad);
    if desde is null then
      raise exception 'este pago no espera a otra persona' using errcode = 'P0001',
        hint = 'estado_invalido';
    end if;
    if desde + plazo > now() then
      raise exception 'todavía no pasó el plazo para tomar el pago' using errcode = 'P0001',
        hint = 'toma_antes_de_tiempo';
    end if;
    insert into nucleo.toma_pago_manual (consorcio_id, ejecucion_id, tomado_por, identidad_tomo)
    values (c, e.id, actor, identidad)
    returning id into toma;
    perform nucleo.auditar_orden_pago(c, 'consorcio.orden_pago.tomar_pago_manual', orden, actor,
      null, jsonb_build_object('estado', o.estado, 'version', e.version, 'ejecucion', e.id,
                               'toma', toma));
    return toma;
  end
  $$;

create function nucleo.tomas_pago_manual_lectura(orden uuid, facultad text, actor uuid,
                                                 origen_pedido text, sesion uuid)
  returns table (ejecucion_id uuid, tomado_por_nombre text, tomada_en timestamptz)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
  begin
    perform nucleo.exigir_lectura_egresos(c, facultad, actor, origen_pedido, sesion);
    return query
      select t.ejecucion_id, r.nombre, t.tomada_en
        from nucleo.toma_pago_manual t
        join nucleo.ejecucion_orden_pago e on e.consorcio_id = c and e.id = t.ejecucion_id
        join nucleo.persona r on r.id = t.tomado_por
       where t.consorcio_id = c and e.orden_id = orden
       order by t.paso;
  end
  $$;
"""

RUTAS = (
    "nucleo.tomar_pago_manual(uuid, integer, bytea, interval, uuid, text, uuid)",
    "nucleo.tomas_pago_manual_lectura(uuid, text, uuid, text, uuid)",
)
INTERNAS = (
    "nucleo.espera_el_pago_desde(uuid, nucleo.orden_pago, uuid, uuid)",
    "nucleo.otro_tiene_el_pago(uuid, uuid)",
    "nucleo.apertura_tomada(nucleo.apertura_cuenta_pago_manual)",
)

CUENTA_CAMBIOS = (
    (
        "    perform nucleo.exigir_no_aprobo(c, orden, e.version, actor, identidad);\n"
        "    insert into nucleo.apertura_cuenta_pago_manual\n",
        "    perform nucleo.exigir_no_aprobo(c, orden, e.version, actor, identidad);\n"
        "    -- Una sola persona paga: la misma orden no se transfiere dos veces (0078).\n"
        "    if nucleo.otro_tiene_el_pago(e.id, identidad) is not null then\n"
        "      raise exception 'otra persona ya vio la cuenta de este pago'"
        " using errcode = 'P0001',\n"
        "        hint = 'pago_en_curso';\n"
        "    end if;\n"
        "    insert into nucleo.apertura_cuenta_pago_manual\n",
    ),
)
DECLARAR_CAMBIOS = (
    (
        "    if exists (select 1 from nucleo.apertura_cuenta_pago_manual a"
        " where a.ejecucion_id = e.id)\n       and not exists",
        "    if exists (select 1 from nucleo.apertura_cuenta_pago_manual a"
        " where a.ejecucion_id = e.id\n"
        "                  and not nucleo.apertura_tomada(a))\n       and not exists",
    ),
)
CONFIRMAR_CAMBIOS = (
    (
        "                where a.ejecucion_id = e.id\n"
        "                  and not exists (select 1 from nucleo.declaracion_pago_no_realizado x\n",
        "                where a.ejecucion_id = e.id and not nucleo.apertura_tomada(a)\n"
        "                  and not exists (select 1 from nucleo.declaracion_pago_no_realizado x\n",
    ),
)
HECHOS_CAMBIOS = (
    (
        "    cuenta_vista := exists (select 1 from nucleo.apertura_cuenta_pago_manual a\n"
        "                             where a.ejecucion_id = e.id);\n",
        "    cuenta_vista := exists (select 1 from nucleo.apertura_cuenta_pago_manual a\n"
        "                             where a.ejecucion_id = e.id\n"
        "                               and not nucleo.apertura_tomada(a));\n",
    ),
    (
        "                       where a.ejecucion_id = e.id\n"
        "                         and not exists (select 1 from"
        " nucleo.declaracion_pago_no_realizado y\n",
        "                       where a.ejecucion_id = e.id and not nucleo.apertura_tomada(a)\n"
        "                         and not exists (select 1 from"
        " nucleo.declaracion_pago_no_realizado y\n",
    ),
)
ADMITE_PAGAR_CAMBIOS = (
    (
        "      perform nucleo.exigir_sin_pago_vigente(e.id);\n    end if;\n",
        "      perform nucleo.exigir_sin_pago_vigente(e.id);\n"
        "      if nucleo.otro_tiene_el_pago(e.id, identidad) is not null then\n"
        "        return false;\n"
        "      end if;\n"
        "    end if;\n",
    ),
)
ADMITE_DECLARAR_CAMBIOS = (
    (
        "    return not exists (select 1 from nucleo.apertura_cuenta_pago_manual a\n"
        "                        where a.ejecucion_id = e.id)\n",
        "    return not exists (select 1 from nucleo.apertura_cuenta_pago_manual a\n"
        "                        where a.ejecucion_id = e.id"
        " and not nucleo.apertura_tomada(a))\n",
    ),
)
SIN_SALIDA_CAMBIOS = (
    (
        "    if exists (select 1 from nucleo.apertura_cuenta_pago_manual a"
        " where a.ejecucion_id = e.id)\n       or exists",
        "    if exists (select 1 from nucleo.apertura_cuenta_pago_manual a"
        " where a.ejecucion_id = e.id\n"
        "                  and not nucleo.apertura_tomada(a))\n       or exists",
    ),
)
FALTA_CAMBIOS = (
    (
        "         and a.identidad_abrio = identidad\n",
        "         and a.identidad_abrio = identidad and not nucleo.apertura_tomada(a)\n",
    ),
)
ACCIONES_CAMBIOS = (
    (
        "origen_pedido text, sesion uuid)\n RETURNS TABLE",
        "origen_pedido text, sesion uuid, plazo_toma interval)\n RETURNS TABLE",
    ),
    (
        " puede_resolver_diferencia boolean)\n",
        " puede_resolver_diferencia boolean, pago_de_otro boolean, puede_tomar boolean,"
        " tomar_desde timestamp with time zone)\n",
    ),
    (
        "               and nucleo.admite_resolver_diferencia(c, o, actor, identidad)\n",
        "               and nucleo.admite_resolver_diferencia(c, o, actor, identidad),\n"
        "             w.desde is not null, coalesce(w.desde + plazo_toma <= now(), false),\n"
        "             w.desde + plazo_toma\n",
    ),
    (
        "       cross join lateral nucleo.hechos_de_pago_manual(c, o) h\n",
        "       cross join lateral nucleo.hechos_de_pago_manual(c, o) h\n"
        "       cross join lateral (\n"
        "         select case when ejecuta\n"
        "                     then nucleo.espera_el_pago_desde(c, o, actor, identidad)\n"
        "                end as desde) w\n",
    ),
)
EXPORTAR_CAMBIOS = (
    (
        "    return query\n      select 'diferencia_pago'::text,\n",
        "    return query\n"
        "      select 'toma_pago'::text,\n"
        "             jsonb_build_object('id', t.id, 'ejecucion_id', t.ejecucion_id,\n"
        "                                'tomado_por_nombre',"
        " nucleo.nombre_para_exportar(c, t.tomado_por, hoy),\n"
        "                                'tomada_en', t.tomada_en)\n"
        "        from nucleo.toma_pago_manual t\n"
        "       where t.consorcio_id = c\n"
        "       order by t.paso;\n"
        "    return query\n      select 'diferencia_pago'::text,\n",
    ),
)

REEMPLAZOS = (
    (CUENTA, CUENTA_CAMBIOS),
    (DECLARAR, DECLARAR_CAMBIOS),
    (CONFIRMAR, CONFIRMAR_CAMBIOS),
    (HECHOS, HECHOS_CAMBIOS),
    (ADMITE_PAGAR, ADMITE_PAGAR_CAMBIOS),
    (ADMITE_DECLARAR, ADMITE_DECLARAR_CAMBIOS),
    (SIN_SALIDA, SIN_SALIDA_CAMBIOS),
    (FALTA, FALTA_CAMBIOS),
    (EXPORTAR, EXPORTAR_CAMBIOS),
)

GUARDA_DOWNGRADE = """
lock table nucleo.toma_pago_manual in access exclusive mode;
do $$
begin
  if exists (select 1 from nucleo.toma_pago_manual) then
    raise exception '0078 no baja con pagos tomados: se perdería quién los tomó'
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
    for antes, despues in reversed(cambios) if al_reves else cambios:
        if al_reves:
            antes, despues = despues, antes
        if definicion.count(antes) != 1:
            raise RuntimeError(f"0078: {funcion} no tiene exactamente una vez {antes!r}")
        definicion = definicion.replace(antes, despues)
    return definicion


def _acciones(desde: str, hasta: str, *, al_reves: bool) -> None:
    """Otra firma y otro resultado: se borra y se crea de nuevo, con su permiso."""
    definicion = _definicion(desde, ACCIONES_CAMBIOS, al_reves=al_reves)
    op.execute(f"drop function {desde}")
    op.execute(definicion)
    op.execute(f"revoke all on function {hasta} from public")
    op.execute(f"grant execute on function {hasta} to cem_app")


def upgrade() -> None:
    op.execute(TABLA)
    op.execute(FUNCIONES)
    for funcion in INTERNAS + RUTAS:
        op.execute(f"revoke all on function {funcion} from public")
    for funcion in RUTAS:
        op.execute(f"grant execute on function {funcion} to cem_app")
    for funcion, cambios in REEMPLAZOS:
        op.execute(_definicion(funcion, cambios, al_reves=False))
    _acciones(ACCIONES, ACCIONES_NUEVA, al_reves=False)


def downgrade() -> None:
    op.execute(GUARDA_DOWNGRADE)
    _acciones(ACCIONES_NUEVA, ACCIONES, al_reves=True)
    for funcion, cambios in reversed(REEMPLAZOS):
        op.execute(_definicion(funcion, cambios, al_reves=True))
    for funcion in RUTAS + INTERNAS:
        op.execute(f"drop function {funcion}")
    op.execute("drop table nucleo.toma_pago_manual")
