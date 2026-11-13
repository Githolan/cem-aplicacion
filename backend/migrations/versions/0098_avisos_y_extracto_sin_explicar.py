"""Avisos por correo y lo que el extracto no explica (informe de fricción, tanda 8; preguntas 7 y 8
del propietario, 13-11-2026).

- **Lo que cada persona puede hacer, sin sesión** (pregunta 7). El cuerpo de
  `nucleo.acciones_de_ordenes_lectura` (`0072` y siguientes) pasa a la función interna
  `nucleo.acciones_de_ordenes_de`, que recibe la persona, su identidad humana y sus facultades en
  vez de leerlas de la sesión. La función de la API la llama después de sus controles: la bandeja y
  el aviso por correo deciden con la misma regla, escrita una sola vez. La interna no se concede a
  `cem_app`. El cuerpo se mueve tal cual está en la base (se lee con `pg_get_functiondef`), así que
  lo que hayan cambiado `0075` a `0087` viaja entero.
- **`nucleo.pendientes_para_avisar`**: para el trabajo diario de avisos (`cem-trabajos`, sin
  persona ni consorcio en el ámbito, como `revocar_sesiones_por_vencimiento`), las acciones de cada
  persona con `egresos.preparar_orden` o `egresos.aprobar_orden` vigente, en cada edificio cuyo modo
  admite egresos y con su identidad humana verificada, más el estado de la orden y el correo de la
  persona. No devuelve importes, cuentas, proveedores ni conceptos: el correo no los lleva.
- **`nucleo.aviso_pendientes`** (una fila por persona y ventana del planificador) y
  `nucleo.registrar_aviso_pendientes`: el tope de avisos por persona en 24 horas
  (`CEM_AVISOS_TOPE_DIARIO`, que pasa el trabajo). Cuenta por ventana, no por reloj: dos corridas
  del mismo día (porque cambió `CEM_AVISOS_HORA_UTC`) no mandan dos correos con tope 1. RLS
  habilitada y forzada; `cem_app` no toca la tabla. Es un registro operativo: no tiene datos del
  consorcio.
- **Lo que el extracto no explica** (pregunta 8, opción c). `nucleo.movimientos_sin_cotejo_lectura`
  (los débitos guardados sin cotejo vigente) y `nucleo.pagos_para_explicar_lectura` (los pagos con
  los que se comparan, sin proveedor, concepto ni cuenta). Las lee quien concilia
  (`egresos.aprobar_orden`) **y la administración** (`egresos.preparar_orden`), sin el gesto: la
  administración solo lee, no coteja nada (`exigir_lectura_extracto`). La comparación es la de
  subir un extracto (`app/consorcios/extracto.py`), así que la lista dice lo mismo que el resultado.

- **«No es el pago de una orden»** (respuesta del propietario del 13-11 a la pregunta 1 del PR
  #240, opción a): `nucleo.movimiento_no_es_pago` guarda quién lo marcó, con qué identidad, cuándo y
  por qué; una fila por débito, que no se cambia ni se borra (`negar_cambio_historia_orden_pago`) y
  cuyo motivo no admite una cuenta (`negar_cuenta_en_motivo`, como `0060`). La escribe solo
  `nucleo.marcar_no_es_pago`, con las reglas de cotejar: `egresos.aprobar_orden` y el gesto (la
  administración no la marca). Un débito que ya explica un pago (cotejo vigente) no se marca;
  repetirla no hace nada. El débito marcado sale de lo que el extracto no explica. Exportación
  económica (`0069`): `exportar_pagos` suma `movimiento_no_es_pago`, con el nombre de quien marcó y
  el motivo sin cuentas. RLS habilitada y forzada; `cem_app` no toca la tabla.
- **Un pago ya atado a otro débito no explica este** (Codex en #240): `pagos_para_explicar_lectura`
  no devuelve los pagos que un cotejo vigente ya ata a un débito, como `extracto.comparar` aparta al
  subir los pagos de los débitos ya atados. Un pago cotejado a mano sí sigue explicando.
- **El tope de avisos cuenta envíos reales** (Codex en #240): `registrar_aviso_pendientes` cuenta
  los avisos registrados en las últimas 24 horas menos un margen que pasa el trabajo (la corrida de
  ayer pudo salir unos segundos tarde); la ventana queda como clave, para no repetir una corrida.
  Dos corridas atrasadas que salen juntas mandan un solo correo.

No toca la separación de funciones ni la regla de la cuenta: nada de esto devuelve una cuenta.

Downgrade: se niega (`downgrade_con_datos`) si hay un débito marcado como «no es el pago de una
orden»: se perdería quién lo marcó y por qué. Si no, `acciones_de_ordenes_lectura` vuelve a tener el
cuerpo adentro y se borra lo demás, también `aviso_pendientes` (sin datos del consorcio: perderla
solo reinicia la cuenta del tope).

Revisión: 0098
Anterior: 0096 (al fusionar, después de la 0097 de la tanda 6)
"""

import re

from alembic import op
from sqlalchemy import text

revision = "0098"
down_revision = "0096"
branch_labels = None
depends_on = None

ACCIONES = "nucleo.acciones_de_ordenes_lectura(uuid, uuid, text, uuid, interval)"
PARAMETROS_INTERNA = (
    "c uuid, actor uuid, identidad uuid, orden uuid, plazo_toma interval, prepara boolean,"
    " aprueba boolean, ejecuta boolean, carga boolean"
)
INTERNA = (
    "nucleo.acciones_de_ordenes_de(uuid, uuid, uuid, uuid, interval, boolean, boolean, boolean,"
    " boolean)"
)
LLAMADA = (
    "    return query select * from nucleo.acciones_de_ordenes_de(c, actor, identidad, orden,"
    " plazo_toma, prepara, aprueba, ejecuta, carga);\n"
)
INICIO_CUERPO = "    return query\n"
FIN_CUERPO = "  end\n"
DEVUELVE = re.compile(r"RETURNS TABLE\((?P<columnas>[^)]*)\)\n")

PENDIENTES = "nucleo.pendientes_para_avisar(interval)"
REGISTRAR = "nucleo.registrar_aviso_pendientes(uuid, timestamp with time zone, integer, interval)"
MARCAR = "nucleo.marcar_no_es_pago(uuid, text, uuid, text, uuid)"
EXPORTAR = "nucleo.exportar_pagos(uuid, uuid)"
EXPORTAR_ANTES = "    return query\n      select 'columnas_extracto'::text,\n"
EXPORTAR_NUEVO = """    return query
      select 'movimiento_no_es_pago'::text,
             jsonb_build_object('movimiento_id', y.movimiento_id,
                                'marcado_por_nombre',
                                nucleo.nombre_para_exportar(c, y.marcado_por, hoy),
                                'marcado_en', y.marcado_en,
                                'motivo', nucleo.texto_sin_cuenta(y.motivo))
        from nucleo.movimiento_no_es_pago y
       where y.consorcio_id = c
       order by y.marcado_en, y.movimiento_id;
"""
EXIGIR_LECTURA = "nucleo.exigir_lectura_extracto(uuid, uuid, uuid, text)"
MOVIMIENTOS = "nucleo.movimientos_sin_cotejo_lectura(uuid, text, uuid)"
PAGOS = "nucleo.pagos_para_explicar_lectura(text[], date, date, uuid, text, uuid)"
RUTAS = (PENDIENTES, REGISTRAR, MOVIMIENTOS, PAGOS, MARCAR)

TABLA = """
create table nucleo.aviso_pendientes (
  persona_id     uuid not null references nucleo.persona (id) on delete cascade,
  ventana        timestamptz not null,
  registrado_en  timestamptz not null default clock_timestamp(),
  primary key (persona_id, ventana)
);
alter table nucleo.aviso_pendientes enable row level security;
alter table nucleo.aviso_pendientes force row level security;
revoke all on nucleo.aviso_pendientes from public, cem_app;
"""

AVISOS = """
-- Solo el trabajo de avisos: sin persona y sin consorcio en el ámbito. Recorre todos los
-- edificios, como `revocar_sesiones_por_vencimiento`.
create function nucleo.pendientes_para_avisar(plazo_toma interval)
  returns table (consorcio_id uuid, consorcio text, persona_id uuid, correo text,
                 quien_prepara boolean, estado text, {columnas})
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    x record;
    prepara boolean;
    aprueba boolean;
    ejecuta boolean;
    carga boolean;
    identidad uuid;
  begin
    if plataforma.persona_actual() is not null or plataforma.consorcio_actual() is not null then
      raise exception 'solo el trabajo de avisos lee los pendientes de todos'
        using errcode = '42501';
    end if;
    for x in
      select co.id as c, co.nombre, p.id as persona, p.correo,
             nucleo.hoy_del_consorcio(co.id) as hoy
        from nucleo.consorcio co
        join nucleo.mandato m on m.consorcio_id = co.id
        join nucleo.persona p on p.id = m.persona_id and p.eliminado_en is null
       where co.eliminado_en is null
         and exists (
           select 1 from nucleo.modo_operacion mo
            where mo.consorcio_id = co.id
              and mo.vigente_desde <= nucleo.hoy_del_consorcio(co.id)
              and (mo.vigente_hasta is null
                   or mo.vigente_hasta > nucleo.hoy_del_consorcio(co.id))
              and mo.modo in ('administrador_aumentado', 'autogestion'))
       group by co.id, co.nombre, p.id, p.correo
       order by co.id, p.id
    loop
      prepara := nucleo.tiene_facultad_vigente(x.c, x.persona, 'egresos.preparar_orden', x.hoy);
      aprueba := nucleo.tiene_facultad_vigente(x.c, x.persona, 'egresos.aprobar_orden', x.hoy);
      continue when not (prepara or aprueba);
      identidad := nucleo.identidad_humana_de(x.c, x.persona);
      continue when identidad is null;
      ejecuta := prepara
        and nucleo.tiene_facultad_vigente(x.c, x.persona, 'egresos.ejecutar_pago', x.hoy);
      carga := nucleo.tiene_facultad_vigente(x.c, x.persona, 'documentos.cargar', x.hoy);
      return query
        select x.c, x.nombre, x.persona, x.correo, prepara, o.estado::text, a.*
          from nucleo.acciones_de_ordenes_de(x.c, x.persona, identidad, null, plazo_toma,
                                             prepara, aprueba, ejecuta, carga) a
          join nucleo.orden_pago o on o.consorcio_id = x.c and o.id = a.orden_id
         order by a.orden_id;
    end loop;
  end
  $$;

-- El tope por persona: cuántos avisos se registraron (y salieron) de verdad en las últimas 24 h,
-- menos `margen`: la corrida de ayer pudo terminar unos segundos después que la de hoy. Devuelve si
-- este se registra (y entonces se manda). La ventana es la clave: una corrida no se repite.
create function nucleo.registrar_aviso_pendientes(persona uuid, ventana_aviso timestamptz,
                                                  tope integer, margen interval)
  returns boolean
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  begin
    if plataforma.persona_actual() is not null or plataforma.consorcio_actual() is not null then
      raise exception 'solo el trabajo de avisos registra un aviso' using errcode = '42501';
    end if;
    if tope is null or tope < 1 then
      raise exception 'el tope de avisos tiene que ser positivo'
        using errcode = 'P0001', hint = 'tope_invalido';
    end if;
    if margen is null or margen < interval '0' or margen >= interval '1 day' then
      raise exception 'el margen tiene que estar entre 0 y un día'
        using errcode = 'P0001', hint = 'margen_invalido';
    end if;
    perform pg_advisory_xact_lock(hashtextextended('aviso_pendientes:' || persona::text, 0));
    if (select count(*) from nucleo.aviso_pendientes a
         where a.persona_id = persona
           and a.registrado_en > clock_timestamp() - (interval '1 day' - margen)) >= tope then
      return false;
    end if;
    insert into nucleo.aviso_pendientes (persona_id, ventana) values (persona, ventana_aviso)
    on conflict do nothing;
    return found;
  end
  $$;
"""

SIN_EXPLICAR = """
-- Quien concilia o la administración, sin el gesto. Con las dos facultades, como quien concilia.
create function nucleo.exigir_lectura_extracto(c uuid, actor uuid, sesion uuid,
                                               origen_pedido text)
  returns uuid
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  begin
    if c is not null and nucleo.tiene_facultad_vigente(c, actor, 'egresos.aprobar_orden',
                                                       nucleo.hoy_del_consorcio(c)) then
      return nucleo.exigir_acto_sin_gesto(c, actor, sesion, 'egresos.aprobar_orden',
                                          origen_pedido);
    end if;
    return nucleo.exigir_acto_sin_gesto(c, actor, sesion, 'egresos.preparar_orden', origen_pedido);
  end
  $$;
revoke all on function nucleo.exigir_lectura_extracto(uuid, uuid, uuid, text) from public;

-- Los débitos guardados que ningún cotejo vigente ata a un pago. La primera vez que se vieron es la
-- de su importación.
create function nucleo.movimientos_sin_cotejo_lectura(actor uuid, origen_pedido text, sesion uuid)
  returns table (movimiento_id uuid, fecha date, importe_centavos bigint, moneda text,
                 numero_operacion text, visto_en timestamptz)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
  begin
    perform nucleo.exigir_lectura_extracto(c, actor, sesion, origen_pedido);
    return query
      select m.id, m.fecha, m.importe_centavos, m.moneda, m.numero_operacion, i.importado_en
        from nucleo.movimiento_extracto m
        join nucleo.importacion_extracto i
          on i.consorcio_id = m.consorcio_id and i.id = m.importacion_id
       where m.consorcio_id = c
         and not exists (select 1 from nucleo.cotejo_pago t
                          where t.consorcio_id = c and t.movimiento_id = m.id
                            and t.anulado_en is null)
         and not exists (select 1 from nucleo.movimiento_no_es_pago y
                          where y.consorcio_id = c and y.movimiento_id = m.id)
       order by m.fecha, m.id;
  end
  $$;

-- Los pagos contra los que se comparan esos débitos: los de `pagos_para_comparar_lectura`, sin
-- proveedor, concepto ni la separación de quién coteja (acá nadie coteja). Sin los que un cotejo
-- vigente ya ata a otro débito: ese pago ya está explicado y no explica dos (al subir, `comparar`
-- los aparta por el pago del débito atado). Un pago cotejado a mano no tiene débito: sigue.
create function nucleo.pagos_para_explicar_lectura(numeros text[], desde date, hasta date,
                                                   actor uuid, origen_pedido text, sesion uuid)
  returns table (pago_id uuid, numero_operacion text, fecha_operacion date,
                 importe_centavos bigint, moneda text, conforme boolean, cotejado boolean,
                 anulado boolean)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
  begin
    perform nucleo.exigir_lectura_extracto(c, actor, sesion, origen_pedido);
    return query
      select f.pago_id, f.numero_operacion, f.fecha_operacion, f.importe_centavos, f.moneda,
             f.conforme, f.cotejado, f.anulado
        from nucleo.pagos_del_cotejo(c) f
       where (f.fecha_operacion between desde and hasta or f.numero_operacion = any(numeros))
         and not exists (select 1 from nucleo.cotejo_pago t
                          where t.consorcio_id = c
                            and coalesce(t.pago_id, t.diferencia_id) = f.pago_id
                            and t.movimiento_id is not null and t.anulado_en is null)
       order by f.pago_id;
  end
  $$;
"""

NO_ES_PAGO = """
create table nucleo.movimiento_no_es_pago (
  consorcio_id     uuid not null,
  movimiento_id    uuid not null,
  motivo           text not null check (length(btrim(motivo)) between 1 and 300),
  marcado_por      uuid not null references nucleo.persona (id),
  identidad_marco  uuid not null,
  marcado_en       timestamptz not null default clock_timestamp(),
  primary key (consorcio_id, movimiento_id),
  foreign key (consorcio_id, movimiento_id)
    references nucleo.movimiento_extracto (consorcio_id, id),
  foreign key (consorcio_id, identidad_marco)
    references nucleo.identidad_humana (consorcio_id, id)
);
alter table nucleo.movimiento_no_es_pago enable row level security;
alter table nucleo.movimiento_no_es_pago force row level security;
create policy por_consorcio on nucleo.movimiento_no_es_pago for select
  using (consorcio_id = plataforma.consorcio_actual());
revoke all on nucleo.movimiento_no_es_pago from public, cem_app;
create trigger motivo_sin_cuenta before insert on nucleo.movimiento_no_es_pago
  for each row execute function nucleo.negar_cuenta_en_motivo('motivo');
create trigger historia_movimiento_no_es_pago before update or delete
  on nucleo.movimiento_no_es_pago
  for each row execute function nucleo.negar_cambio_historia_orden_pago();
create trigger historia_movimiento_no_es_pago_truncate before truncate
  on nucleo.movimiento_no_es_pago
  for each statement execute function nucleo.negar_cambio_historia_orden_pago();

-- Las reglas de cotejar: la facultad, el gesto y el turno por consorcio. Repetir no hace nada.
create function nucleo.marcar_no_es_pago(movimiento uuid, motivo text, actor uuid,
                                         origen_pedido text, sesion uuid)
  returns void
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    identidad uuid;
  begin
    identidad := nucleo.exigir_acto_economico(c, actor, sesion, 'egresos.aprobar_orden',
                                              origen_pedido);
    perform nucleo.bloquear_cotejo(c);
    if not exists (select 1 from nucleo.movimiento_extracto m
                    where m.consorcio_id = c and m.id = movimiento) then
      raise exception 'ese débito no existe en este consorcio' using errcode = 'P0001',
        hint = 'no_encontrado';
    end if;
    if exists (select 1 from nucleo.cotejo_pago t
                where t.consorcio_id = c and t.movimiento_id = movimiento
                  and t.anulado_en is null) then
      raise exception 'ese débito ya lo explica un pago' using errcode = 'P0001',
        hint = 'estado_invalido';
    end if;
    insert into nucleo.movimiento_no_es_pago
      (consorcio_id, movimiento_id, motivo, marcado_por, identidad_marco)
    values (c, movimiento, btrim(motivo), actor, identidad)
    on conflict do nothing;
    if found then
      -- El motivo queda en la tabla, no en el evento (como el rechazo de una orden).
      insert into auditoria.eventos
        (consorcio_id, actor_persona_id, origen, accion, entidad, entidad_id, antes, despues,
         hash_previo, hash)
      values (c, actor, 'api', 'consorcio.extracto.no_es_pago', 'movimiento_extracto',
              movimiento, null, jsonb_build_object('movimiento', movimiento), '', '');
    end if;
  end
  $$;
"""

GUARDA_DOWNGRADE = """
lock table nucleo.movimiento_no_es_pago in access exclusive mode;
do $$
begin
  if exists (select 1 from nucleo.movimiento_no_es_pago) then
    raise exception '0098 no baja con débitos marcados: se perdería quién los marcó y por qué'
      using errcode = 'P0001', hint = 'downgrade_con_datos';
  end if;
end
$$;
"""


def _definicion(funcion: str) -> str:
    return (
        op.get_bind()
        .execute(text("select pg_get_functiondef(cast(:f as regprocedure))"), {"f": funcion})
        .scalar_one()
    )


def _una_vez(definicion: str, parte: str, funcion: str) -> None:
    if definicion.count(parte) != 1:
        raise RuntimeError(f"0098: {funcion} no tiene exactamente una vez {parte!r}")


def _cuerpo(definicion: str, funcion: str) -> str:
    """De `return query` hasta el `end` de la función, sin incluirlo."""
    _una_vez(definicion, INICIO_CUERPO, funcion)
    inicio = definicion.index(INICIO_CUERPO)
    fin = definicion.rindex(FIN_CUERPO)
    if fin < inicio:
        raise RuntimeError(f"0098: {funcion} no termina después de su return query")
    return definicion[inicio:fin]


def _columnas(definicion: str) -> str:
    encontradas = DEVUELVE.findall(definicion)
    if len(encontradas) != 1:
        raise RuntimeError("0098: no se encontró una sola lista de columnas en las acciones")
    return encontradas[0]


def upgrade() -> None:
    definicion = _definicion(ACCIONES)
    cuerpo = _cuerpo(definicion, ACCIONES)
    # La interna recibe por parámetro todo lo que el cuerpo usa de la función de la API.
    if "hoy" in cuerpo:
        raise RuntimeError("0098: el cuerpo de las acciones usa `hoy`, que la interna no recibe")
    columnas = _columnas(definicion)
    op.execute(
        f"create function nucleo.acciones_de_ordenes_de({PARAMETROS_INTERNA})\n"
        f"  returns table ({columnas})\n"
        "  language plpgsql volatile\n"
        "  set search_path = pg_catalog, pg_temp\n"
        "  as $cuerpo$\n"
        "  begin\n"
        f"{cuerpo}"
        "  end\n"
        "  $cuerpo$"
    )
    op.execute(f"revoke all on function {INTERNA} from public")
    op.execute(definicion.replace(cuerpo, LLAMADA))
    op.execute(TABLA)
    op.execute(AVISOS.replace("{columnas}", columnas))
    op.execute(NO_ES_PAGO)
    op.execute(SIN_EXPLICAR)
    for funcion in RUTAS:
        op.execute(f"revoke all on function {funcion} from public")
        op.execute(f"grant execute on function {funcion} to cem_app")
    exportar = _definicion(EXPORTAR)
    _una_vez(exportar, EXPORTAR_ANTES, EXPORTAR)
    op.execute(exportar.replace(EXPORTAR_ANTES, EXPORTAR_NUEVO + EXPORTAR_ANTES))


def downgrade() -> None:
    op.execute(GUARDA_DOWNGRADE)
    exportar = _definicion(EXPORTAR)
    _una_vez(exportar, EXPORTAR_NUEVO, EXPORTAR)
    op.execute(exportar.replace(EXPORTAR_NUEVO, ""))
    for funcion in (*RUTAS, EXIGIR_LECTURA):
        op.execute(f"drop function {funcion}")
    op.execute("drop table nucleo.movimiento_no_es_pago")
    op.execute("drop table nucleo.aviso_pendientes")
    cuerpo = _cuerpo(_definicion(INTERNA), INTERNA)
    definicion = _definicion(ACCIONES)
    _una_vez(definicion, LLAMADA, ACCIONES)
    op.execute(definicion.replace(LLAMADA, cuerpo))
    op.execute(f"drop function {INTERNA}")
