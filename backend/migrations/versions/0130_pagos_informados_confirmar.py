"""Pagos informados, segundo corte: confirmar, rechazar, anular y corregir (PI-S2 del diseño «Pagos
informados» del repositorio CEM, `doc/diseno/Pagos_Informados.md`, reglas PI-3, PI-4, PI-5 y PI-9),
con la bandeja y el aviso diario del Consejo.

**Los estados** (PI-4): `informado` → `confirmado` o `rechazado` (con motivo); `confirmado` →
`anulado` (con motivo, PI-9). Ningún otro paso (`TRANSICIONES`). El check de `estado` de `0129` se
amplía con el mismo nombre; los índices únicos parciales de `0129` ya excluyen `rechazado` y
`anulado`, así que no se tocan.

**La historia** (`nucleo.pago_informado_estado`): una fila por paso, con de dónde, a dónde, el
motivo, quién y cuándo. A lo sumo una fila por pago y estado al que llega (clave). Sin cambios ni
borrado (`negar_cambio_pago_informado`, la de `0129`), RLS forzada, sin privilegios para `cem_app`.
El motivo, obligatorio al rechazar y al anular y prohibido al confirmar, de `LARGO_MOTIVO` como
máximo, en un renglón y sin una cuenta (`texto_con_cuenta`, `0060`: `motivo_con_cuenta`). Un
disparador repite al insertar que el paso parte del estado actual del pago.

**Cambiar el estado** (la regla de `0129`): `historia_pago_informado` ya no niega todo. Ahora
ejecuta `cambio_de_estado_pago_informado`, que sigue negando el borrado y todo cambio, salvo uno que
cumpla las cuatro cosas: (1) la transacción fijó `BANDERA` con el id de ese pago (solo lo hace
`decidir_pago_informado`, `security definer`, y lo vuelve a vaciar enseguida); (2) cambia solo
`estado`; (3) el paso está en `TRANSICIONES`; (4) su fila de historia ya está escrita. El vaciado
(`truncate`) sigue negado.

**Quién decide** (PI-5): quien tiene la facultad nueva `pagos.confirmar_informado`
(`FACULTAD`), que entra en los paquetes de `miembro_consejo` y de `administrador`, no económica, en
los tres modos (la política `pagos_informados.confirmar`). Como en `0113`, `0122` a `0124` y `0128`,
los mandatos que ya existen no la reciben: un acta reduce un paquete, nunca lo amplía. Quien ayuda
no decide (la sesión se pide sin alcance). **Separación:** nadie confirma, rechaza ni anula un pago
que informó él mismo (`informado_por`) ni uno que informó ayudando a otra persona (el actor del
evento de auditoría de informar): `no_es_quien_informo`.

**Confirmar con el comprobante** (PI-4): mientras su versión está `en_cuarentena`
(`comprobante_en_revision`) o si el antivirus la rechazó (`comprobante_rechazado`), no se confirma;
rechazar, sí. Un pago en efectivo sin comprobante se confirma.

**Corregir** (PI-3): `informar_pago` suma `corrige`: un pago `rechazado` o `anulado` de la misma
unidad (si no, `correccion_de_pago_invalida`), todavía sin corrección (si no, `pago_ya_corregido`;
`corrige_a` ya era única en `0129`). La corrección se crea aunque tenga el mismo número de operación
o el mismo archivo que el corregido: los índices de `0129` no cuentan los rechazados ni los
anulados. La búsqueda de «ya informado» corre primero: si ya cuenta uno con ese número o ese
archivo, se devuelve ese y no se escribe nada (también el reintento de la misma corrección). Para
que la corrección pueda citar la misma versión del comprobante, se quita la unicidad simple de
`comprobante_version_id`: el índice parcial del SHA-256 de `0129` ya impide que dos que cuentan
citen el mismo archivo.

**Lectura** (`pagos_informados_lectura`): suma `corregido_por` (el pago que corrige a este),
`historia` y, solo para la API (no se muestran), `informado_por` y `informo_ayudando`, que el
evaluador usa para la separación al calcular las acciones de cada pago.

**Bandeja y aviso diario** (`pendientes_consejo_para_avisar`, `0126`): suma `pagos_informados`,
cuántos pagos `informado` hay en el edificio que esa persona no informó, para quien tiene la
facultad en un modo de su acción. Solo el número: ni unidad, ni importe, ni persona.

**Exportación** (`0069`): la parte `pagos_informados` suma `pago_informado_estado`, con el nombre de
quien decidió y el motivo por `texto_sin_cuenta`.

Downgrade: se niega (`downgrade_con_datos`) si hay pasos en la historia, pagos que corrigen a otro
o mandatos con la facultad.

Datos de prueba: SINTETICOS / NO REAL.

Revisión: 0130
Anterior: 0129
"""

import importlib.util
from pathlib import Path

from alembic import op

revision = "0130"
down_revision = "0129"
branch_labels = None
depends_on = None

ESTADOS = ("informado", "confirmado", "rechazado", "anulado")
"""`cem_contracts.pagos_informados.EstadoPagoInformado`; una prueba compara."""
TRANSICIONES = (
    ("informado", "confirmado"),
    ("informado", "rechazado"),
    ("confirmado", "anulado"),
)
ESTADOS_CON_MOTIVO = ("rechazado", "anulado")
ESTADOS_QUE_SE_CORRIGEN = ("rechazado", "anulado")
"""Los que no cuentan para «un solo registro por pago» (`ESTADOS_QUE_NO_CUENTAN` de `0129`)."""
LARGO_MOTIVO = 500
VERSIONES_QUE_NO_SE_CONFIRMAN = {
    "en_cuarentena": "comprobante_en_revision",
    "rechazado": "comprobante_rechazado",
}
"""El estado de la versión del comprobante que impide confirmar, con el motivo que da la base."""
FACULTAD = "pagos.confirmar_informado"
FACULTAD_VER = "fiscalizacion.ver_panel"
"""Decidir exige también ver el panel (PI-D14): sin ella la persona no lista los pagos, así que ni
decide ni la bandeja le cuenta los pendientes (observación de Codex en #290)."""
AREA = "pagos"
PAQUETES = ("miembro_consejo", "administrador")
ACCION_POLITICA = "pagos_informados.confirmar"
MODOS = ("consejo", "administrador_aumentado", "autogestion")
"""Los de la política `pagos_informados.confirmar`; una prueba compara."""
ACCION_INFORMAR = "consorcio.pago_informado.informar"
ACCIONES = {
    "confirmado": "consorcio.pago_informado.confirmar",
    "rechazado": "consorcio.pago_informado.rechazar",
    "anulado": "consorcio.pago_informado.anular",
}
"""La acción de auditoría de cada paso, por el estado al que llega."""
BANDERA = "cem.pago_informado_que_cambia"
"""El indicador de la transacción que fija `decidir_pago_informado`: el id del pago cuyo estado
cambia."""
HISTORIA = "nucleo.pago_informado_estado"
EXPORTAR = "exportar_pagos_informados"
PENDIENTES = "nucleo.pendientes_consejo_para_avisar()"
INDICE_EVENTOS = "eventos_pago_informado"
UNICA_VERSION = "pago_informado_comprobante_version_id_key"
"""La unicidad simple de `0129` que se quita para que una corrección cite la misma versión."""


def _lista(valores) -> str:
    return ", ".join(f"'{v}'" for v in valores)


def _pares(pares) -> str:
    return ", ".join(f"('{a}', '{b}')" for a, b in pares)


def _largo(columna: str, maximo: int) -> str:
    return (
        f"length(btrim({columna})) between 1 and {maximo} and {columna} = btrim({columna})"
        f" and {columna} !~ '[[:cntrl:]]'"
    )


def _modulo(nombre: str):
    """Una migración anterior, ya aplicada (no cambia), para volver a sus definiciones al bajar."""
    ruta = Path(__file__).with_name(nombre)
    spec = importlib.util.spec_from_file_location(ruta.stem, ruta)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def _definicion(sql: str, cabecera: str) -> str:
    """La definición de una función tal como la dejó su migración, con sus permisos aparte."""
    if sql.count(cabecera) != 1:
        raise RuntimeError(f"0130: no está exactamente una vez {cabecera!r}")
    inicio = sql.index(cabecera)
    fin = sql.index("\n  $$;", inicio) + len("\n  $$;")
    return sql[inicio:fin] + "\n"


TABLA_SQL = f"""
alter table nucleo.pago_informado drop constraint pago_informado_estado_check;
alter table nucleo.pago_informado add constraint pago_informado_estado_check
  check (estado in ({_lista(ESTADOS)}));
alter table nucleo.pago_informado drop constraint {UNICA_VERSION};

create table {HISTORIA} (
  consorcio_id    uuid not null references nucleo.consorcio (id),
  pago_id         uuid not null,
  de              text not null,
  a               text not null,
  motivo          text check (motivo is null or ({_largo("motivo", LARGO_MOTIVO)})),
  registrado_por  uuid not null references nucleo.persona (id),
  registrado_en   timestamptz not null default clock_timestamp(),
  primary key (pago_id, a),
  foreign key (consorcio_id, pago_id) references nucleo.pago_informado (consorcio_id, id),
  constraint pago_informado_estado_paso check ((de, a) in ({_pares(TRANSICIONES)})),
  constraint pago_informado_estado_motivo
    check ((a in ({_lista(ESTADOS_CON_MOTIVO)})) = (motivo is not null))
);
create index pago_informado_estado_consorcio on {HISTORIA} (consorcio_id, pago_id);

-- El paso parte del estado actual del pago, y el motivo no lleva una cuenta (`0060`).
create function nucleo.paso_de_pago_informado_valido()
  returns trigger
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  begin
    if not exists (select 1 from nucleo.pago_informado p
                    where p.consorcio_id = new.consorcio_id and p.id = new.pago_id
                      and p.estado = new.de) then
      raise exception 'el paso no parte del estado actual del pago' using errcode = 'P0001',
        hint = 'pago_informado_decidido';
    end if;
    if nucleo.texto_con_cuenta(new.motivo) then
      raise exception 'el motivo no puede llevar una cuenta' using errcode = 'P0001',
        hint = 'motivo_con_cuenta';
    end if;
    return new;
  end
  $$;
revoke all on function nucleo.paso_de_pago_informado_valido() from public;
create trigger paso_valido before insert on {HISTORIA}
  for each row execute function nucleo.paso_de_pago_informado_valido();

alter table {HISTORIA} enable row level security;
alter table {HISTORIA} force row level security;
create policy por_consorcio on {HISTORIA} for select
  using (consorcio_id = plataforma.consorcio_actual());
revoke all on {HISTORIA} from public, cem_app;
create trigger historia_pago_informado_estado before update or delete on {HISTORIA}
  for each row execute function nucleo.negar_cambio_pago_informado();
create trigger historia_pago_informado_estado_truncate before truncate on {HISTORIA}
  for each statement execute function nucleo.negar_cambio_pago_informado();

-- Un pago informado no se borra ni se cambia, salvo su estado por `decidir_pago_informado`.
create function nucleo.cambio_de_estado_pago_informado()
  returns trigger
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  begin
    if tg_op = 'UPDATE'
       and current_setting('{BANDERA}', true) = old.id::text
       -- La columna generada todavía es nula en NEW antes de la fila: sale de la comparación
       -- (deriva de `numero_operacion`, que sí se compara).
       and (to_jsonb(new) - '{{estado,numero_operacion_normalizado}}'::text[])
           = (to_jsonb(old) - '{{estado,numero_operacion_normalizado}}'::text[])
       and (old.estado, new.estado) in ({_pares(TRANSICIONES)})
       and exists (select 1 from {HISTORIA} h
                    where h.consorcio_id = old.consorcio_id and h.pago_id = old.id
                      and h.de = old.estado and h.a = new.estado) then
      return new;
    end if;
    raise exception 'un pago informado no se cambia ni se borra: su estado lo cambia el Consejo'
      using errcode = 'P0001', hint = 'historia_inmutable';
  end
  $$;
revoke all on function nucleo.cambio_de_estado_pago_informado() from public;
drop trigger historia_pago_informado on nucleo.pago_informado;
create trigger historia_pago_informado before update or delete on nucleo.pago_informado
  for each row execute function nucleo.cambio_de_estado_pago_informado();

-- Quién informó ayudando sale del evento de informar (`informo_el_pago`, la lectura): sin recorrer
-- toda la auditoría del consorcio.
create index {INDICE_EVENTOS} on auditoria.eventos (consorcio_id, entidad_id)
  where entidad = 'pago_informado';
"""

TABLA_SQL_ABAJO = f"""
drop index auditoria.{INDICE_EVENTOS};
drop trigger historia_pago_informado on nucleo.pago_informado;
create trigger historia_pago_informado before update or delete on nucleo.pago_informado
  for each row execute function nucleo.negar_cambio_pago_informado();
drop function nucleo.cambio_de_estado_pago_informado();
drop table {HISTORIA};
drop function nucleo.paso_de_pago_informado_valido();
alter table nucleo.pago_informado add constraint {UNICA_VERSION} unique (comprobante_version_id);
alter table nucleo.pago_informado drop constraint pago_informado_estado_check;
alter table nucleo.pago_informado add constraint pago_informado_estado_check
  check (estado in ('informado'));
"""

_CASOS_VERSION = "".join(
    f"""
      if version_estado = '{estado}' then
        raise exception 'el comprobante no permite confirmar' using errcode = 'P0001',
          hint = '{motivo}';
      end if;"""
    for estado, motivo in VERSIONES_QUE_NO_SE_CONFIRMAN.items()
)
_ACCION_DEL_PASO = (
    "case nuevo "
    + " ".join(f"when '{estado}' then '{accion}'" for estado, accion in ACCIONES.items())
    + " end"
)

FUNCIONES = f"""
-- Si esa persona informó el pago, por sí o ayudando a otra (el actor del evento de informar).
create function nucleo.informo_el_pago(c uuid, pago uuid, actor uuid)
  returns boolean
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select exists (select 1 from nucleo.pago_informado p
                    where p.consorcio_id = c and p.id = pago and p.informado_por = actor)
        or exists (select 1 from auditoria.eventos e
                    where e.consorcio_id = c and e.entidad = 'pago_informado'
                      and e.entidad_id = pago and e.accion = '{ACCION_INFORMAR}'
                      and e.actor_persona_id = actor)
  $$;

-- Quien informó el pago ayudando a otra persona, si fue así (el primer evento de informar con
-- `en_representacion_de`). Aparte de la lectura: esa lectura devuelve el motivo de cada paso, y la
-- función que lee la auditoría no debe nombrar claves que E31 vigila (`test_vinculo_0014`).
create function nucleo.quien_ayudo_a_informar(c uuid, pago uuid)
  returns uuid
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select e.actor_persona_id from auditoria.eventos e
     where e.consorcio_id = c and e.entidad = 'pago_informado'
       and e.entidad_id = pago and e.accion = '{ACCION_INFORMAR}'
       and e.en_representacion_de is not null
     order by e.secuencia
     limit 1
  $$;

-- Quien decide (PI-5): la persona de la sesión, sin ayuda (la sesión se pide sin alcance), con un
-- modo registrado y `{FACULTAD}` vigente. Devuelve el día del consorcio.
create function nucleo.exigir_decidir_pago_informado(c uuid, actor uuid, sesion uuid,
                                                     origen_pedido text)
  returns date
  language plpgsql volatile
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    hoy date;
  begin
    hoy := nucleo.exigir_sesion_pagos_informados(c, actor, sesion, origen_pedido, null);
    if not nucleo.tiene_facultad_vigente(c, actor, '{FACULTAD}', hoy)
       or not nucleo.tiene_facultad_vigente(c, actor, '{FACULTAD_VER}', hoy) then
      raise exception 'sin {FACULTAD} y {FACULTAD_VER} en este consorcio' using errcode = '42501';
    end if;
    return hoy;
  end
  $$;

-- Un paso del pago (PI-4, PI-5, PI-9), bajo el candado de los pagos informados del consorcio (el
-- mismo de informar: una corrección y una decisión no se cruzan). La historia primero; después el
-- estado, con la bandera de la transacción que admite ese único cambio.
create function nucleo.decidir_pago_informado(pago uuid, nuevo text, motivo_nuevo text,
                                              actor uuid, origen_pedido text, sesion uuid)
  returns void
  language plpgsql volatile
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    p nucleo.pago_informado;
    version_estado text;
    n integer;
  begin
    perform nucleo.exigir_decidir_pago_informado(c, actor, sesion, origen_pedido);
    perform pg_advisory_xact_lock(hashtextextended('pago_informado:' || c::text, 0));
    select * into p from nucleo.pago_informado x where x.consorcio_id = c and x.id = pago;
    if p.id is null then
      raise exception 'el pago informado no existe' using errcode = 'P0001',
        hint = 'no_encontrado';
    end if;
    if nucleo.informo_el_pago(c, pago, actor) then
      raise exception 'quien informó el pago no decide sobre él' using errcode = 'P0001',
        hint = 'no_es_quien_informo';
    end if;
    if (p.estado, nuevo) not in ({_pares(TRANSICIONES)}) then
      raise exception 'el pago no admite ese paso' using errcode = 'P0001',
        hint = 'pago_informado_decidido';
    end if;
    if nuevo = 'confirmado' and p.comprobante_version_id is not null then
      select v.estado into version_estado from nucleo.version_documento v
       where v.consorcio_id = c and v.id = p.comprobante_version_id;{_CASOS_VERSION}
    end if;
    if nuevo in ({_lista(ESTADOS_CON_MOTIVO)}) and nullif(btrim(motivo_nuevo), '') is null then
      raise exception 'falta el motivo' using errcode = 'P0001', hint = 'motivo_requerido';
    end if;
    if nucleo.texto_con_cuenta(motivo_nuevo) then
      raise exception 'el motivo no puede llevar una cuenta' using errcode = 'P0001',
        hint = 'motivo_con_cuenta';
    end if;
    insert into {HISTORIA} (consorcio_id, pago_id, de, a, motivo, registrado_por)
    values (c, pago, p.estado, nuevo, motivo_nuevo, actor);
    perform set_config('{BANDERA}', pago::text, true);
    update nucleo.pago_informado x set estado = nuevo where x.consorcio_id = c and x.id = pago;
    get diagnostics n = row_count;
    perform set_config('{BANDERA}', '', true);
    if n <> 1 then
      raise exception 'el estado del pago no cambió' using errcode = 'P0001';
    end if;
    perform nucleo.auditar_pago_informado(c, actor, origen_pedido, {_ACCION_DEL_PASO}, pago,
      jsonb_build_object('de', p.estado, 'a', nuevo, 'motivo', motivo_nuevo));
  end
  $$;

create function nucleo.confirmar_pago_informado(pago uuid, actor uuid, origen_pedido text,
                                                sesion uuid)
  returns void
  language sql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
    select nucleo.decidir_pago_informado(pago, 'confirmado', null, actor, origen_pedido, sesion)
  $$;

create function nucleo.rechazar_pago_informado(pago uuid, motivo text, actor uuid,
                                               origen_pedido text, sesion uuid)
  returns void
  language sql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
    select nucleo.decidir_pago_informado(pago, 'rechazado', motivo, actor, origen_pedido, sesion)
  $$;

create function nucleo.anular_pago_informado(pago uuid, motivo text, actor uuid,
                                             origen_pedido text, sesion uuid)
  returns void
  language sql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
    select nucleo.decidir_pago_informado(pago, 'anulado', motivo, actor, origen_pedido, sesion)
  $$;

-- Informar (PI-1 a PI-3), ahora con la corrección de un rechazado o anulado (`corrige`). Un solo
-- registro por pago: bajo el candado de los pagos informados del consorcio, si ya cuenta uno con
-- la misma unidad y el mismo número normalizado, o con el mismo archivo, lo devuelve con
-- `ya_informado` y no escribe nada. Recién después mira la corrección.
create function nucleo.informar_pago(unidad uuid, importe_nuevo numeric, fecha date,
                                     medio_nuevo text, numero text, comprobante uuid,
                                     corrige uuid, actor uuid, origen_pedido text, sesion uuid)
  returns table (pago_id uuid, ya_informado boolean)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    hoy date;
    s nucleo.comprobante_pago_subido;
    suma text;
    normalizado text := lower(regexp_replace(numero, '[[:space:].-]', '', 'g'));
    existente nucleo.pago_informado;
    corregido nucleo.pago_informado;
    i uuid;
  begin
    hoy := nucleo.exigir_informar_pago(c, actor, sesion, origen_pedido, unidad);
    if fecha is null or fecha > hoy then
      raise exception 'la fecha del pago es posterior a hoy' using errcode = 'P0001',
        hint = 'fecha_futura';
    end if;
    if nucleo.texto_con_cuenta(numero) then
      raise exception 'el número de operación no puede ser una cuenta' using errcode = 'P0001',
        hint = 'texto_con_cuenta';
    end if;
    if comprobante is not null then
      select * into s from nucleo.comprobante_pago_subido x
       where x.consorcio_id = c and x.version_documento_id = comprobante;
      select v.sha256 into suma from nucleo.version_documento v
        join nucleo.documento d on d.consorcio_id = v.consorcio_id and d.id = v.documento_id
       where v.consorcio_id = c and v.id = comprobante and v.estado <> 'rechazado'
         and d.estado = 'vigente';
      if s.version_documento_id is null or s.subido_por is distinct from actor
         or s.unidad_id is distinct from unidad or suma is null then
        raise exception 'el comprobante no es uno que esta persona subió para esa unidad'
          using errcode = 'P0001', hint = 'comprobante_invalido';
      end if;
    end if;
    -- Un informe a la vez por consorcio: la búsqueda del existente y el alta no se cruzan.
    perform pg_advisory_xact_lock(hashtextextended('pago_informado:' || c::text, 0));
    select * into existente from nucleo.pago_informado p
     where p.consorcio_id = c and p.estado not in ({_lista(ESTADOS_QUE_SE_CORRIGEN)})
       and ((normalizado is not null and p.unidad_id = unidad
             and p.numero_operacion_normalizado = normalizado)
            or (suma is not null and p.comprobante_sha256 = suma))
     order by (p.unidad_id = unidad) desc, p.informado_en, p.id
     limit 1;
    if existente.id is not null then
      if existente.unidad_id is distinct from unidad then
        raise exception 'ese comprobante ya se informó para otra unidad' using errcode = 'P0001',
          hint = 'comprobante_de_otra_unidad';
      end if;
      return query select existente.id, true;
      return;
    end if;
    if corrige is not null then
      select * into corregido from nucleo.pago_informado p
       where p.consorcio_id = c and p.id = corrige;
      if corregido.id is null or corregido.unidad_id is distinct from unidad
         or corregido.estado not in ({_lista(ESTADOS_QUE_SE_CORRIGEN)}) then
        raise exception 'solo se corrige un pago rechazado o anulado de la misma unidad'
          using errcode = 'P0001', hint = 'correccion_de_pago_invalida';
      end if;
      if exists (select 1 from nucleo.pago_informado p
                  where p.consorcio_id = c and p.corrige_a = corrige) then
        raise exception 'ese pago ya tiene su corrección' using errcode = 'P0001',
          hint = 'pago_ya_corregido';
      end if;
    end if;
    insert into nucleo.pago_informado
      (consorcio_id, unidad_id, importe, fecha_pago, medio, numero_operacion,
       comprobante_documento_id, comprobante_version_id, comprobante_sha256, origen,
       informado_por, corrige_a)
    values (c, unidad, importe_nuevo, fecha, medio_nuevo, numero, s.documento_id,
            s.version_documento_id, suma, 'app', actor, corrige)
    returning id into i;
    perform nucleo.auditar_pago_informado(c, actor, origen_pedido, '{ACCION_INFORMAR}', i,
      jsonb_build_object('unidad_id', unidad, 'importe', importe_nuevo, 'fecha_pago', fecha,
                         'medio', medio_nuevo, 'version_id', s.version_documento_id,
                         'corrige_a', corrige));
    return query select i, false;
  end
  $$;

-- Lo que la persona puede ver (PI-6), del más nuevo al más viejo, con el estado del comprobante,
-- la corrección, la historia y, para el evaluador de la API, quién informó (y quién ayudó).
create function nucleo.pagos_informados_lectura(actor uuid, origen_pedido text, sesion uuid)
  returns table (id uuid, unidad_id uuid, unidad_designacion text, importe numeric(18, 2),
                 fecha_pago date, medio text, numero_operacion text, comprobante_version_id uuid,
                 comprobante_estado text, origen text, estado text, corrige_a uuid,
                 corregido_por uuid, informado_por_nombre text, informado_en timestamptz,
                 propio boolean, historia jsonb, informado_por uuid, informo_ayudando uuid)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    todos boolean;
    propias uuid[];
  begin
    todos := nucleo.exigir_lectura_pagos_informados(c, actor, sesion, origen_pedido);
    propias := nucleo.unidades_para_pagos_informados(c, actor);
    return query
      select p.id, p.unidad_id, u.designacion, p.importe, p.fecha_pago, p.medio,
             p.numero_operacion, p.comprobante_version_id, v.estado, p.origen, p.estado,
             p.corrige_a,
             (select k.id from nucleo.pago_informado k
               where k.consorcio_id = c and k.corrige_a = p.id),
             pe.nombre, p.informado_en, p.informado_por = actor,
             coalesce((select jsonb_agg(jsonb_build_object(
                                 'de', h.de, 'a', h.a, 'motivo', h.motivo,
                                 'registrado_por_nombre', hp.nombre,
                                 'registrado_en', h.registrado_en)
                               order by h.registrado_en, h.a)
                         from {HISTORIA} h
                         join nucleo.persona hp on hp.id = h.registrado_por
                        where h.consorcio_id = c and h.pago_id = p.id), '[]'::jsonb),
             p.informado_por,
             nucleo.quien_ayudo_a_informar(c, p.id)
        from nucleo.pago_informado p
        join nucleo.unidad u on u.consorcio_id = c and u.id = p.unidad_id
        join nucleo.persona pe on pe.id = p.informado_por
        left join nucleo.version_documento v
          on v.consorcio_id = c and v.id = p.comprobante_version_id
       where p.consorcio_id = c
         and (todos or p.unidad_id = any(propias) or p.informado_por = actor)
       order by p.informado_en desc, p.id desc;
  end
  $$;
"""

RUTAS = (
    "nucleo.confirmar_pago_informado(uuid, uuid, text, uuid)",
    "nucleo.rechazar_pago_informado(uuid, text, uuid, text, uuid)",
    "nucleo.anular_pago_informado(uuid, text, uuid, text, uuid)",
    "nucleo.informar_pago(uuid, numeric, date, text, text, uuid, uuid, uuid, text, uuid)",
    "nucleo.pagos_informados_lectura(uuid, text, uuid)",
)
INTERNAS = (
    "nucleo.decidir_pago_informado(uuid, text, text, uuid, text, uuid)",
    "nucleo.exigir_decidir_pago_informado(uuid, uuid, uuid, text)",
    "nucleo.informo_el_pago(uuid, uuid, uuid)",
    "nucleo.quien_ayudo_a_informar(uuid, uuid)",
)
"""En el orden en que se pueden borrar: cada una antes de las que llama."""
REEMPLAZADAS = (
    ("nucleo.informar_pago(uuid, numeric, date, text, text, uuid, uuid, text, uuid)",
     "create function nucleo.informar_pago("),
    ("nucleo.pagos_informados_lectura(uuid, text, uuid)",
     "create function nucleo.pagos_informados_lectura("),
)  # fmt: skip
"""Las rutas de `0129` que cambian de firma o de lo que devuelven: se borran y se crean de nuevo;
al bajar vuelven las de `0129`."""


def _puede(accion: str, facultad: str, modos) -> str:
    return (
        f"(x.modo in ({_lista(modos)})"
        f" and nucleo.tiene_facultad_vigente(x.c, x.persona, '{facultad}', x.hoy))"
    )


def _pendientes() -> str:
    """`pendientes_consejo_para_avisar` de `0126` con `pagos_informados` al final: misma lectura,
    una columna y una facultad más."""
    m = _modulo("0126_bandeja_consejo.py")
    sql = m.FUNCIONES
    cambios = (
        ("importaciones integer, solicitudes jsonb, reclamos integer, reparaciones integer)",
         "importaciones integer, solicitudes jsonb, reclamos integer, reparaciones integer,\n"
         "                 pagos_informados integer)"),
        ("    gestiona boolean;\n",
         "    gestiona boolean;\n    decide boolean;\n"),
        ("      continue when not (importa or firma or atiende or gestiona);\n",
         f"      decide := {_puede(ACCION_POLITICA, FACULTAD, MODOS)}"
         f" and nucleo.tiene_facultad_vigente(x.c, x.persona, '{FACULTAD_VER}', x.hoy);\n"
         "      continue when not (importa or firma or atiende or gestiona or decide);\n"),
        ("                     and nucleo.estado_de_reparacion(x.c, r.id) = 'pedida') end;\n",
         "                     and nucleo.estado_de_reparacion(x.c, r.id) = 'pedida') end,\n"
         "               case when decide then\n"
         "                 (select count(*)::integer from nucleo.pago_informado q\n"
         "                   where q.consorcio_id = x.c and q.estado = 'informado'\n"
         "                     and not nucleo.informo_el_pago(x.c, q.id, x.persona)) end;\n"),
    )  # fmt: skip
    for antes, despues in cambios:
        if sql.count(antes) != 1:
            raise RuntimeError(f"0130: 0126 no tiene exactamente una vez {antes!r}")
        sql = sql.replace(antes, despues)
    return sql


def _de_0069():
    return _modulo("0069_exportacion_economica.py")


CUERPO_EXPORTAR_ESTADOS = """    return query
      select 'pago_informado_estado'::text,
             jsonb_build_object('pago_id', h.pago_id, 'de', h.de, 'a', h.a,
                                'motivo', nucleo.texto_sin_cuenta(h.motivo),
                                'registrado_por_nombre',
                                nucleo.nombre_para_exportar(c, h.registrado_por, hoy),
                                'registrado_en', h.registrado_en)
        from nucleo.pago_informado_estado h
       where h.consorcio_id = c
       order by h.registrado_en, h.pago_id, h.a;
"""

GUARDA = f"""
do $guarda$
begin
  if exists (select 1 from {HISTORIA})
     or exists (select 1 from nucleo.pago_informado where corrige_a is not null)
     or exists (select 1 from nucleo.facultad_otorgada where facultad = '{FACULTAD}') then
    raise exception '0130 no baja: hay pasos de pagos informados, correcciones o mandatos con la'
      ' facultad' using errcode = 'P0001', hint = 'downgrade_con_datos';
  end if;
end
$guarda$;
"""

PROYECCIONES = (
    (ACCIONES["confirmado"], "Confirmó un pago informado", ["de", "a"]),
    (ACCIONES["rechazado"], "Rechazó un pago informado", ["de", "a"]),
    (ACCIONES["anulado"], "Anuló un pago informado", ["de", "a"]),
)


def _permisos(rutas, internas) -> None:
    for funcion in internas + rutas:
        op.execute(f"revoke all on function {funcion} from public")
    for funcion in rutas:
        op.execute(f"grant execute on function {funcion} to cem_app")


def upgrade() -> None:
    paquetes = ", ".join(f"('{t}', '{FACULTAD}')" for t in PAQUETES)
    op.execute(
        f"insert into nucleo.facultad (codigo, area, economica) values ('{FACULTAD}', '{AREA}',"
        f" false); insert into nucleo.paquete_facultad (tipo, facultad) values {paquetes};"
    )
    op.execute(TABLA_SQL)
    for funcion, _ in REEMPLAZADAS:
        op.execute(f"drop function {funcion}")
    op.execute(FUNCIONES)
    _permisos(RUTAS, INTERNAS)
    op.execute(f"drop function {PENDIENTES}")
    op.execute(_pendientes())
    op.execute(f"revoke all on function {PENDIENTES} from public")
    op.execute(f"grant execute on function {PENDIENTES} to cem_app")
    m0129 = _modulo("0129_pagos_informados.py")
    op.execute(f"drop function nucleo.{EXPORTAR}(uuid, uuid)")
    op.execute(_de_0069()._funcion(EXPORTAR, m0129.CUERPO_EXPORTAR + CUERPO_EXPORTAR_ESTADOS))
    filas = ", ".join(
        f"('{accion}', '{texto_}', 'Pago informado', array[{_lista(campos)}])"
        for accion, texto_, campos in PROYECCIONES
    )
    op.execute(f"insert into auditoria.proyeccion (accion, texto, recurso, campos) values {filas}")


def downgrade() -> None:
    op.execute(GUARDA)
    acciones = _lista(accion for accion, _, _ in PROYECCIONES)
    op.execute(f"delete from auditoria.proyeccion where accion in ({acciones})")
    m0129 = _modulo("0129_pagos_informados.py")
    op.execute(f"drop function nucleo.{EXPORTAR}(uuid, uuid)")
    op.execute(_de_0069()._funcion(EXPORTAR, m0129.CUERPO_EXPORTAR))
    m0126 = _modulo("0126_bandeja_consejo.py")
    op.execute(f"drop function {PENDIENTES}")
    op.execute(m0126.FUNCIONES)
    op.execute(f"revoke all on function {PENDIENTES} from public")
    op.execute(f"grant execute on function {PENDIENTES} to cem_app")
    for funcion in RUTAS + INTERNAS:
        op.execute(f"drop function {funcion}")
    for funcion, cabecera in REEMPLAZADAS:
        op.execute(_definicion(m0129.FUNCIONES, cabecera))
        op.execute(f"revoke all on function {funcion} from public")
        op.execute(f"grant execute on function {funcion} to cem_app")
    op.execute(TABLA_SQL_ABAJO)
    op.execute(f"delete from nucleo.paquete_facultad where facultad = '{FACULTAD}'")
    op.execute(f"delete from nucleo.facultad where codigo = '{FACULTAD}'")
