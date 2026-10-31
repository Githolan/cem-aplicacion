"""Pago manual asistido y su conciliación (piloto sin riel; decisión del propietario, 31-10-2026).

Diseño: `doc/diseno/Pago_Manual_y_Conciliacion_Piloto_2026-10-31.md` del repositorio CEM (#122),
con la regla del propietario del 31-10 sobre la cuenta completa: la privacidad depende del acto,
no de una etiqueta permanente sobre la persona.

**La modalidad** (`ejecucion_orden_pago.modalidad`, `riel` o `manual`) se fija al emitir y no cambia
(la ejecución solo se agrega). Es `riel` solo si el consorcio figura en
`nucleo.riel_de_pago_habilitado`, que ninguna ruta escribe: lo carga el dueño de la base el día que
haya un riel contratado (hoy, solo el consorcio LAB de staging, para el ensayo del riel simulado).
Si no, `manual`. Una ejecución manual no encola `egresos.ordenar_pago` ni `egresos.verificar_pago`,
y las funciones del riel (`pedido_de_pago`, `registrar_resultado_pago`) la niegan
(`modalidad_manual`); las del pago manual niegan una del riel (`modalidad_riel`). Una persona y un
trabajo nunca pagan la misma ejecución. Las ejecuciones creadas antes de `0055` quedan `riel`: así
se crearon (`0051` siempre encolaba el riel), no es una inferencia.

**El acto de ejecución** (`nucleo.cuenta_para_pago_manual`, con `egresos.ejecutar_pago`): el único
lugar donde quien va a transferir recibe la cuenta destino completa. Se evalúa de nuevo en cada
pedido: la orden en ejecución con una ejecución manual de la versión vigente y sin pago registrado,
la versión y la huella vistas, la aprobación vigente y suficiente (`exigir_orden_emitible`: 7 días,
aprobaciones de la franja, contrato vigente), el proveedor sin cambios y activo
(`exigir_proveedor_sin_cambios`), R10 (`exigir_acto_economico`: sesión reciente, segundo factor de
menos de 10 minutos, identidad humana verificada) y que quien la pide no aprobó esa versión, por la
identidad guardada al aprobar y por la de hoy (A5). Quien preparó la orden puede ejecutarla si no
la aprobó y tiene la facultad (preparar ≠ aprobar, aprobar ≠ ejecutar); la preparación sigue sin
recibir la cuenta (#172, #175, `0054`). Cada pedido deja su evento, con los últimos 4 dígitos, y
su apertura (`nucleo.apertura_cuenta_pago_manual`): quien vio la cuenta pudo haber transferido.

**El registro con evidencia** (`nucleo.pago_manual`, `registrar_pago_manual`): número de operación,
fecha, importe, moneda, cuenta destino y comprobante. El importe, la moneda y la cuenta tienen que
ser los de la ejecución (`no_coincide_con_la_orden`); la fecha, entre la emisión y el vencimiento de
la aprobación, y nunca futura (`fecha_operacion_invalida`); el comprobante, un documento vigente de
`facturas_y_comprobantes` con un archivo que pasó el antivirus, y se guarda **ese archivo**
(`version_documento_id`): un archivo subido después no cambia la evidencia. Un comprobante respalda
una sola ejecución (`nucleo.comprobante_pago_manual`, por clave). Registra solo quien recibió la
cuenta en el acto de esa ejecución (`no_es_quien_ejecuto`). El registro no da la orden por pagada:
la pasa a En verificación, que en un pago manual es «pendiente de conciliación».

**El número de operación** se normaliza (mayúsculas, solo letras y dígitos) y queda atado para
siempre a una sola ejecución (`nucleo.operacion_pago_manual`, clave `(consorcio, número)`): otra
ejecución no lo usa nunca (`operacion_de_otra_orden`), y la corrección de un registro observado de
la misma ejecución sí (Codex en CEM #122). Nunca es una cuenta: un número que es un CBU válido se
niega (P3 114). El registro cita el número junto con su ejecución (clave foránea compuesta): la base
no admite un número atado a otra.

**Un solo registro vigente por ejecución:** el primero no corrige a ninguno y cada corrección cita
el registro observado que reemplaza (`corrige_a`); dos registros nunca ocupan el mismo lugar (índice
único parcial y `unique (corrige_a)`), así que dos registros a la vez para la misma ejecución nunca
valen los dos. El vigente es el último de esa cadena, no el de la marca más nueva. Nada se borra:
los triggers de historia de la orden niegan UPDATE, DELETE y TRUNCATE.

**La conciliación** (`nucleo.conciliacion_pago_manual`, `conciliar_pago_manual`, con
`egresos.aprobar_orden`): otro ser humano que quien recibió la cuenta de esa ejecución o registró un
pago de ella (`exigir_no_ejecuto`) y que quien preparó cualquier versión, por la identidad guardada
en cada acto y por la de hoy. Una por registro. `conforme` escribe el resultado `pagada` con origen
`manual` y referencia = número de operación, en la misma tabla y con el mismo índice de resultado
definitivo que el riel, y la orden pasa a Pagada. `observado` (con motivo) deja el registro sin
efecto y en la historia; quien ejecuta registra otro.

**«No se pagó»** (`declarar_pago_no_realizado` con `egresos.ejecutar_pago`, y
`confirmar_pago_no_realizado` con `egresos.aprobar_orden` por otro ser humano): solo sin un registro
vigente. Declara quien no aprobó la versión (A5, también en la base). La confirmación la hace
alguien que no vio la cuenta ni registró, exige que cada persona que vio la cuenta haya declarado
después de su última apertura (si alguien la vio, declara alguien que la vio), y se niega si
después de declarar alguien recibió la cuenta o registró un pago (la declaración quedó superada:
`estado_invalido`). El orden de esos hechos lo da una secuencia, no el reloj. Escribe `fallida`
con origen `manual`; para volver a pagar hace falta una versión nueva y otra aprobación, como
siempre. Así nadie solo abre la puerta a pagar dos veces.

**Lectura** (`ejecuciones_manuales_lectura`, `pagos_manuales_lectura`,
`declaraciones_pago_no_realizado_lectura`): con la barrera de lectura de egresos (`0049`), sin la
cuenta. Los eventos nunca llevan la cuenta completa.

**Downgrade.** Se niega (`downgrade_con_datos`) si hay alguna ejecución manual, registro, número
atado, conciliación, declaración o consorcio con el riel habilitado: bajar haría pasar por pagos
del riel lo que se pagó a mano, o encolaría el riel donde nadie lo pidió.

Revisión: 0055
Anterior: 0054
"""

from alembic import op
from sqlalchemy import text

revision = "0055"
down_revision = "0054"
branch_labels = None
depends_on = None

MODALIDADES = ("riel", "manual")
ORIGENES_ANTES = ("ordenar", "consultar")
ORIGENES = (*ORIGENES_ANTES, "manual")
RESULTADOS_CONCILIACION = ("conforme", "observado")
CATEGORIA_COMPROBANTE = "facturas_y_comprobantes"
VIGENCIA_APROBACION_DIAS = 7
"""La de `0050`: el pago tiene que haberse hecho mientras la aprobación regía."""

CREAR_EJECUCION = "nucleo.crear_ejecucion_orden_pago(uuid, uuid, integer)"
PEDIDO = "nucleo.pedido_de_pago(uuid)"
RESULTADO = "nucleo.registrar_resultado_pago(uuid, text, text, text, text)"

SOLO_RIEL = (
    "      raise exception 'esta ejecución se paga a mano, no por el riel'\n"
    "        using errcode = 'P0001', hint = 'modalidad_manual';\n"
)

# Sobre el texto exacto de cada función de 0051: (antes, después).
CAMBIOS = {
    CREAR_EJECUCION: [
        (
            "    e uuid;\n  begin\n",
            "    e uuid;\n"
            "    m text := case when exists (select 1 from nucleo.riel_de_pago_habilitado r\n"
            "                                 where r.consorcio_id = c) then 'riel'\n"
            "                   else 'manual' end;\n"
            "  begin\n",
        ),
        (
            "       importe_centavos, moneda)\n"
            "    select c, orden, v.numero, v.proveedor_id, v.proveedor_version,"
            " pv.cuenta_destino,\n"
            "           v.importe_centavos, v.moneda\n",
            "       importe_centavos, moneda, modalidad)\n"
            "    select c, orden, v.numero, v.proveedor_id, v.proveedor_version,"
            " pv.cuenta_destino,\n"
            "           v.importe_centavos, v.moneda, m\n",
        ),
        (
            "    insert into plataforma.jobs\n",
            "    -- Manual (0055): la paga una persona por su canal; no se encola nada.\n"
            "    if m = 'manual' then\n"
            "      return;\n"
            "    end if;\n"
            "    insert into plataforma.jobs\n",
        ),
    ],
    PEDIDO: [
        (
            "    return query\n",
            "    if exists (select 1 from nucleo.ejecucion_orden_pago x\n"
            "                where x.consorcio_id = c and x.id = ejecucion\n"
            "                  and x.modalidad <> 'riel') then\n" + SOLO_RIEL + "    end if;\n"
            "    return query\n",
        ),
    ],
    RESULTADO: [
        (
            "    o := nucleo.orden_pago_bloqueada(c, e.orden_id);\n",
            "    o := nucleo.orden_pago_bloqueada(c, e.orden_id);\n"
            "    if e.modalidad <> 'riel' then\n" + SOLO_RIEL + "    end if;\n",
        ),
    ],
}

_lista = ", ".join
_modalidades = _lista(f"'{m}'" for m in MODALIDADES)
_origenes = _lista(f"'{o}'" for o in ORIGENES)
_origenes_antes = _lista(f"'{o}'" for o in ORIGENES_ANTES)
_conciliaciones = _lista(f"'{r}'" for r in RESULTADOS_CONCILIACION)

TABLAS = (
    "nucleo.operacion_pago_manual",
    "nucleo.comprobante_pago_manual",
    "nucleo.apertura_cuenta_pago_manual",
    "nucleo.pago_manual",
    "nucleo.conciliacion_pago_manual",
    "nucleo.declaracion_pago_no_realizado",
    "nucleo.confirmacion_pago_no_realizado",
)

UPGRADE = f"""
alter table nucleo.ejecucion_orden_pago
  add column modalidad text not null default 'riel' check (modalidad in ({_modalidades}));
alter table nucleo.ejecucion_orden_pago alter column modalidad drop default;

alter table nucleo.resultado_ejecucion_orden_pago
  drop constraint resultado_ejecucion_orden_pago_origen_check,
  add constraint resultado_ejecucion_orden_pago_origen_check check (origen in ({_origenes}));

create table nucleo.riel_de_pago_habilitado (
  consorcio_id   uuid primary key references nucleo.consorcio (id),
  habilitado_en  timestamptz not null default now()
);
revoke all on nucleo.riel_de_pago_habilitado from public, cem_app;

create table nucleo.operacion_pago_manual (
  consorcio_id      uuid not null,
  numero_operacion  text not null check (numero_operacion ~ '^[A-Z0-9]{{4,40}}$'),
  ejecucion_id      uuid not null,
  atada_en          timestamptz not null default now(),
  primary key (consorcio_id, numero_operacion),
  unique (consorcio_id, numero_operacion, ejecucion_id),
  foreign key (consorcio_id, ejecucion_id)
    references nucleo.ejecucion_orden_pago (consorcio_id, id)
);

-- Un comprobante respalda una sola ejecución: la clave lo ata aunque dos registros corran a la vez.
create table nucleo.comprobante_pago_manual (
  consorcio_id  uuid not null,
  documento_id  uuid not null,
  ejecucion_id  uuid not null,
  atado_en      timestamptz not null default now(),
  primary key (consorcio_id, documento_id),
  unique (consorcio_id, documento_id, ejecucion_id),
  foreign key (consorcio_id, documento_id) references nucleo.documento (consorcio_id, id),
  foreign key (consorcio_id, ejecucion_id)
    references nucleo.ejecucion_orden_pago (consorcio_id, id)
);

-- El orden de los hechos de una ejecución manual (aperturas, registros, declaraciones): se toma
-- con la orden ya bloqueada, así sigue el orden real aunque el reloj del servidor retroceda.
create sequence nucleo.paso_pago_manual;
revoke all on sequence nucleo.paso_pago_manual from public, cem_app;

-- Cada acto en que alguien recibió la cuenta completa: quien la vio pudo haber transferido.
create table nucleo.apertura_cuenta_pago_manual (
  consorcio_id     uuid not null,
  id               uuid not null default plataforma.uuid_v7(),
  ejecucion_id     uuid not null,
  abierta_por      uuid not null references nucleo.persona (id),
  identidad_abrio  uuid not null,
  abierta_en       timestamptz not null default clock_timestamp(),
  paso             bigint not null default nextval('nucleo.paso_pago_manual'),
  primary key (id),
  foreign key (consorcio_id, ejecucion_id)
    references nucleo.ejecucion_orden_pago (consorcio_id, id),
  foreign key (consorcio_id, identidad_abrio)
    references nucleo.identidad_humana (consorcio_id, id)
);

create table nucleo.pago_manual (
  consorcio_id          uuid not null,
  id                    uuid not null default plataforma.uuid_v7(),
  ejecucion_id          uuid not null,
  corrige_a             uuid unique,
  numero_operacion      text not null,
  fecha_operacion       date not null,
  importe_centavos      bigint not null check (importe_centavos > 0),
  moneda                text not null check (moneda ~ '^[A-Z]{{3}}$'),
  documento_id          uuid not null,
  version_documento_id  uuid not null references nucleo.version_documento (id),
  registrado_por        uuid not null references nucleo.persona (id),
  identidad_registro    uuid not null,
  registrado_en         timestamptz not null default clock_timestamp(),
  paso                  bigint not null default nextval('nucleo.paso_pago_manual'),
  primary key (id),
  unique (consorcio_id, id),
  foreign key (consorcio_id, ejecucion_id)
    references nucleo.ejecucion_orden_pago (consorcio_id, id),
  foreign key (corrige_a) references nucleo.pago_manual (id),
  -- El número, atado a esta misma ejecución: la base no admite uno de otra.
  foreign key (consorcio_id, numero_operacion, ejecucion_id)
    references nucleo.operacion_pago_manual (consorcio_id, numero_operacion, ejecucion_id),
  foreign key (consorcio_id, documento_id, ejecucion_id)
    references nucleo.comprobante_pago_manual (consorcio_id, documento_id, ejecucion_id),
  foreign key (consorcio_id, identidad_registro)
    references nucleo.identidad_humana (consorcio_id, id)
);
create unique index pago_manual_primero_unico on nucleo.pago_manual (ejecucion_id)
  where corrige_a is null;

create table nucleo.conciliacion_pago_manual (
  consorcio_id        uuid not null,
  id                  uuid not null default plataforma.uuid_v7(),
  pago_id             uuid not null unique,
  resultado           text not null check (resultado in ({_conciliaciones})),
  -- Texto libre: no va a la auditoría.
  motivo              text check (length(btrim(motivo)) between 1 and 300),
  conciliado_por      uuid not null references nucleo.persona (id),
  identidad_concilio  uuid not null,
  conciliado_en       timestamptz not null default now(),
  primary key (id),
  check ((resultado = 'observado') = (motivo is not null)),
  foreign key (consorcio_id, pago_id) references nucleo.pago_manual (consorcio_id, id),
  foreign key (consorcio_id, identidad_concilio)
    references nucleo.identidad_humana (consorcio_id, id)
);

create table nucleo.declaracion_pago_no_realizado (
  consorcio_id       uuid not null,
  id                 uuid not null default plataforma.uuid_v7(),
  ejecucion_id       uuid not null,
  -- Texto libre: va al resultado `fallida`, no a la auditoría.
  motivo             text not null check (length(btrim(motivo)) between 1 and 200),
  declarado_por      uuid not null references nucleo.persona (id),
  identidad_declaro  uuid not null,
  declarado_en       timestamptz not null default clock_timestamp(),
  paso               bigint not null default nextval('nucleo.paso_pago_manual'),
  primary key (id),
  unique (consorcio_id, id),
  foreign key (consorcio_id, ejecucion_id)
    references nucleo.ejecucion_orden_pago (consorcio_id, id),
  foreign key (consorcio_id, identidad_declaro)
    references nucleo.identidad_humana (consorcio_id, id)
);

create table nucleo.confirmacion_pago_no_realizado (
  consorcio_id        uuid not null,
  id                  uuid not null default plataforma.uuid_v7(),
  declaracion_id      uuid not null unique,
  confirmado_por      uuid not null references nucleo.persona (id),
  identidad_confirmo  uuid not null,
  confirmado_en       timestamptz not null default now(),
  primary key (id),
  foreign key (consorcio_id, declaracion_id)
    references nucleo.declaracion_pago_no_realizado (consorcio_id, id),
  foreign key (consorcio_id, identidad_confirmo)
    references nucleo.identidad_humana (consorcio_id, id)
);
"""

for _t in TABLAS:
    _n = _t.split(".")[1]
    UPGRADE += f"""
alter table {_t} enable row level security;
alter table {_t} force row level security;
create policy por_consorcio on {_t} for select
  using (consorcio_id = plataforma.consorcio_actual());
revoke all on {_t} from public, cem_app;
create trigger historia_{_n} before update or delete on {_t}
  for each row execute function nucleo.negar_cambio_historia_orden_pago();
create trigger historia_{_n}_truncate before truncate on {_t}
  for each statement execute function nucleo.negar_cambio_historia_orden_pago();
"""

UPGRADE += f"""
-- La ejecución manual de la versión vigente, esperando su pago: sin resultado definitivo.
create function nucleo.ejecucion_manual_abierta(c uuid, o nucleo.orden_pago)
  returns nucleo.ejecucion_orden_pago
  language plpgsql stable
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    e nucleo.ejecucion_orden_pago;
  begin
    if o.estado not in ('en_ejecucion', 'en_verificacion') then
      raise exception 'la orden no está esperando un pago' using errcode = 'P0001',
        hint = 'estado_invalido';
    end if;
    select * into e from nucleo.ejecucion_orden_pago x
     where x.consorcio_id = c and x.orden_id = o.id and x.version = o.version_vigente;
    if not found then
      raise exception 'la versión vigente no tiene ejecución' using errcode = 'P0001',
        hint = 'estado_invalido';
    end if;
    if e.modalidad <> 'manual' then
      raise exception 'esta ejecución es del riel de pago: no se paga a mano'
        using errcode = 'P0001', hint = 'modalidad_riel';
    end if;
    if exists (select 1 from nucleo.resultado_ejecucion_orden_pago r
                where r.ejecucion_id = e.id and r.resultado <> 'incierta') then
      raise exception 'la ejecución ya tiene un resultado definitivo' using errcode = 'P0001',
        hint = 'resultado_repetido';
    end if;
    return e;
  end
  $$;

-- El último registro de la cadena de correcciones (el que nadie corrige); vigente si no lo
-- observaron. La cadena es lineal: un solo primero y `corrige_a` único.
create function nucleo.ultimo_pago_manual(e uuid)
  returns nucleo.pago_manual
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select p.* from nucleo.pago_manual p
     where p.ejecucion_id = e
       and not exists (select 1 from nucleo.pago_manual q where q.corrige_a = p.id)
  $$;

create function nucleo.pago_manual_observado(pago uuid)
  returns boolean
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select exists (select 1 from nucleo.conciliacion_pago_manual k
                    where k.pago_id = pago and k.resultado = 'observado')
  $$;

-- Sin un registro vigente (pendiente de conciliar o conforme): el último falta o lo observaron.
create function nucleo.exigir_sin_pago_vigente(e uuid)
  returns nucleo.pago_manual
  language plpgsql stable
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    p nucleo.pago_manual := nucleo.ultimo_pago_manual(e);
  begin
    if p.id is not null and not nucleo.pago_manual_observado(p.id) then
      raise exception 'el pago de esta orden ya está registrado' using errcode = 'P0001',
        hint = 'pago_ya_registrado';
    end if;
    return p;
  end
  $$;

-- A5: quien ejecuta no es ninguno de los que aprobaron la versión, ni por la identidad guardada
-- al aprobar ni por la actual (lo mismo que `emitir_orden_pago`).
create function nucleo.exigir_no_aprobo(c uuid, orden uuid, version integer, actor uuid,
                                        identidad uuid)
  returns void
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    p uuid;
  begin
    if exists (select 1 from nucleo.orden_pago_aprobacion x
                where x.orden_id = orden and x.version = exigir_no_aprobo.version
                  and x.identidad_id = identidad) then
      raise exception 'la misma persona no puede ocupar los dos lugares de esta separación'
        using errcode = 'P0001', hint = 'no_es_quien_aprobo';
    end if;
    for p in select x.persona_id from nucleo.orden_pago_aprobacion x
              where x.orden_id = orden and x.version = exigir_no_aprobo.version loop
      perform nucleo.exigir_separacion_humana(c, actor, p, 'no_es_quien_aprobo');
    end loop;
  end
  $$;

-- Quien concilia o confirma no preparó ninguna versión de la orden.
create function nucleo.exigir_no_preparo(c uuid, orden uuid, actor uuid, identidad uuid)
  returns void
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    p uuid;
  begin
    if exists (select 1 from nucleo.orden_pago_version x
                where x.orden_id = orden and x.identidad_preparo = identidad) then
      raise exception 'la misma persona no puede ocupar los dos lugares de esta separación'
        using errcode = 'P0001', hint = 'no_es_quien_preparo';
    end if;
    for p in select distinct x.preparada_por from nucleo.orden_pago_version x
              where x.orden_id = orden loop
      perform nucleo.exigir_separacion_humana(c, actor, p, 'no_es_quien_preparo');
    end loop;
  end
  $$;

-- Quien concilia o confirma no recibió la cuenta de esta ejecución ni registró un pago de ella:
-- pudo haber transferido. Por la identidad guardada y por la actual.
create function nucleo.exigir_no_ejecuto(c uuid, e uuid, actor uuid, identidad uuid)
  returns void
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    p uuid;
  begin
    if exists (select 1 from nucleo.apertura_cuenta_pago_manual a
                where a.ejecucion_id = e and a.identidad_abrio = identidad)
       or exists (select 1 from nucleo.pago_manual x
                   where x.ejecucion_id = e and x.identidad_registro = identidad) then
      raise exception 'la misma persona no puede ocupar los dos lugares de esta separación'
        using errcode = 'P0001', hint = 'no_es_quien_ejecuto';
    end if;
    for p in select a.abierta_por from nucleo.apertura_cuenta_pago_manual a
              where a.ejecucion_id = e
             union
             select x.registrado_por from nucleo.pago_manual x where x.ejecucion_id = e loop
      perform nucleo.exigir_separacion_humana(c, actor, p, 'no_es_quien_ejecuto');
    end loop;
  end
  $$;

-- La orden, la versión y la huella vistas, y su ejecución manual abierta.
create function nucleo.orden_para_pago_manual(c uuid, orden uuid, version_vista integer,
                                              huella_vista bytea)
  returns nucleo.ejecucion_orden_pago
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    o nucleo.orden_pago := nucleo.orden_pago_bloqueada(c, orden);
  begin
    if version_vista is distinct from o.version_vigente then
      raise exception 'esa versión ya no es la vigente' using errcode = 'P0001',
        hint = 'version_desactualizada';
    end if;
    if not exists (select 1 from nucleo.orden_pago_version x
                    where x.orden_id = orden and x.numero = version_vista
                      and x.huella = huella_vista) then
      raise exception 'lo que se ve no es la versión vigente' using errcode = 'P0001',
        hint = 'huella_distinta';
    end if;
    return nucleo.ejecucion_manual_abierta(c, o);
  end
  $$;

-- El acto de ejecución manual: la cuenta completa, solo acá y con todas las barreras.
create function nucleo.cuenta_para_pago_manual(orden uuid, version_vista integer,
                                               huella_vista bytea, actor uuid,
                                               origen_pedido text, sesion uuid)
  returns table (ejecucion_id uuid, version integer, huella text, proveedor_id uuid,
                 proveedor_version integer, razon_social text, cuit text, cuenta_destino text,
                 importe_centavos bigint, moneda text, concepto text, comprobante text,
                 aprobada_en timestamptz, aprobacion_vence_en timestamptz)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    identidad uuid;
    e nucleo.ejecucion_orden_pago;
    o nucleo.orden_pago;
  begin
    identidad := nucleo.exigir_acto_economico(c, actor, sesion, 'egresos.ejecutar_pago',
                                              origen_pedido);
    e := nucleo.orden_para_pago_manual(c, orden, version_vista, huella_vista);
    perform nucleo.exigir_sin_pago_vigente(e.id);
    select * into o from nucleo.orden_pago x where x.consorcio_id = c and x.id = orden;
    perform nucleo.exigir_orden_emitible(c, o, e.version);
    perform nucleo.exigir_proveedor_sin_cambios(c, orden, e.version);
    perform nucleo.exigir_no_aprobo(c, orden, e.version, actor, identidad);
    insert into nucleo.apertura_cuenta_pago_manual
      (consorcio_id, ejecucion_id, abierta_por, identidad_abrio)
    values (c, e.id, actor, identidad);
    perform nucleo.auditar_orden_pago(c, 'consorcio.orden_pago.ver_cuenta_para_pago', orden,
      actor, null,
      jsonb_build_object('version', e.version, 'ejecucion', e.id,
                         'cuenta_ultimos_digitos', right(e.cuenta_destino, 4)));
    return query
      select e.id, e.version, encode(v.huella, 'hex'), e.proveedor_id, e.proveedor_version,
             pv.razon_social, p.cuit, e.cuenta_destino, e.importe_centavos, e.moneda,
             v.concepto, v.comprobante, o.aprobada_en,
             o.aprobada_en + make_interval(days => {VIGENCIA_APROBACION_DIAS})
        from nucleo.orden_pago_version v
        join nucleo.proveedor p on p.consorcio_id = c and p.id = e.proveedor_id
        join nucleo.proveedor_version pv
          on pv.proveedor_id = e.proveedor_id and pv.numero = e.proveedor_version
       where v.consorcio_id = c and v.orden_id = orden and v.numero = e.version;
  end
  $$;

create function nucleo.registrar_pago_manual(orden uuid, version_vista integer,
                                             huella_vista bytea, numero text, fecha date,
                                             importe bigint, moneda_pago text, cuenta text,
                                             documento uuid, actor uuid, origen_pedido text,
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
    anterior nucleo.pago_manual;
    n text := upper(regexp_replace(coalesce(numero, ''), '[^A-Za-z0-9]', '', 'g'));
    tz text;
    archivo uuid;
    atada uuid;
    pago uuid;
  begin
    identidad := nucleo.exigir_acto_economico(c, actor, sesion, 'egresos.ejecutar_pago',
                                              origen_pedido);
    e := nucleo.orden_para_pago_manual(c, orden, version_vista, huella_vista);
    select * into o from nucleo.orden_pago x where x.consorcio_id = c and x.id = orden;
    perform nucleo.exigir_no_aprobo(c, orden, e.version, actor, identidad);
    anterior := nucleo.exigir_sin_pago_vigente(e.id);
    -- Registra quien recibió la cuenta en el acto de esta ejecución: el que transfirió. Antes de
    -- comparar la cuenta escrita, para que el registro no sirva para probar cuentas sin rastro.
    if not exists (select 1 from nucleo.apertura_cuenta_pago_manual a
                    where a.ejecucion_id = e.id and a.identidad_abrio = identidad) then
      raise exception 'registra el pago quien vio la cuenta en el acto de ejecución'
        using errcode = 'P0001', hint = 'no_es_quien_ejecuto';
    end if;
    if n !~ '^[A-Z0-9]{{4,40}}$' or nucleo.cbu_valido(n) then
      raise exception 'el número de operación no es válido' using errcode = 'P0001',
        hint = 'numero_operacion_invalido';
    end if;
    if importe is distinct from e.importe_centavos or moneda_pago is distinct from e.moneda
       or regexp_replace(coalesce(cuenta, ''), '[^0-9]', '', 'g') <> e.cuenta_destino then
      raise exception 'lo pagado no es lo que se aprobó' using errcode = 'P0001',
        hint = 'no_coincide_con_la_orden';
    end if;
    select k.zona_horaria into tz from nucleo.consorcio k where k.id = c;
    if fecha is null or fecha < (e.creada_en at time zone tz)::date
       or fecha > nucleo.hoy_del_consorcio(c)
       or fecha > ((o.aprobada_en + make_interval(days => {VIGENCIA_APROBACION_DIAS}))
                   at time zone tz)::date then
      raise exception 'la fecha de la operación no es válida' using errcode = 'P0001',
        hint = 'fecha_operacion_invalida';
    end if;
    -- El archivo que pasó el antivirus, el último: es el que queda como evidencia.
    select v.id into archivo
      from nucleo.documento d
      join nucleo.version_documento v on v.consorcio_id = d.consorcio_id and v.documento_id = d.id
     where d.consorcio_id = c and d.id = documento and d.estado = 'vigente'
       and d.categoria = '{CATEGORIA_COMPROBANTE}' and v.estado = 'disponible'
     order by v.analizada_en desc, v.id desc limit 1;
    if archivo is null then
      raise exception 'el comprobante no existe o no pasó el antivirus' using errcode = 'P0001',
        hint = 'comprobante_invalido';
    end if;
    insert into nucleo.comprobante_pago_manual (consorcio_id, documento_id, ejecucion_id)
    values (c, documento, e.id)
    on conflict (consorcio_id, documento_id) do nothing;
    select x.ejecucion_id into atada from nucleo.comprobante_pago_manual x
     where x.consorcio_id = c and x.documento_id = documento;
    if atada is distinct from e.id then
      raise exception 'ese comprobante ya respalda otro pago' using errcode = 'P0001',
        hint = 'comprobante_de_otra_orden';
    end if;
    insert into nucleo.operacion_pago_manual (consorcio_id, numero_operacion, ejecucion_id)
    values (c, n, e.id)
    on conflict (consorcio_id, numero_operacion) do nothing;
    select x.ejecucion_id into atada from nucleo.operacion_pago_manual x
     where x.consorcio_id = c and x.numero_operacion = n;
    if atada is distinct from e.id then
      raise exception 'ese número de operación ya respalda otra orden' using errcode = 'P0001',
        hint = 'operacion_de_otra_orden';
    end if;
    insert into nucleo.pago_manual
      (consorcio_id, ejecucion_id, corrige_a, numero_operacion, fecha_operacion,
       importe_centavos, moneda, documento_id, version_documento_id, registrado_por,
       identidad_registro)
    values (c, e.id, anterior.id, n, fecha, importe, moneda_pago, documento, archivo, actor,
            identidad)
    returning id into pago;
    if o.estado = 'en_ejecucion' then
      update nucleo.orden_pago set estado = 'en_verificacion', actualizada_en = now()
       where id = orden;
    end if;
    perform nucleo.auditar_orden_pago(c, 'consorcio.orden_pago.registrar_pago_manual', orden,
      actor, jsonb_build_object('estado', o.estado),
      jsonb_build_object('estado', 'en_verificacion', 'version', e.version, 'ejecucion', e.id,
                         'pago', pago, 'corrige_a', anterior.id, 'numero_operacion', n,
                         'fecha_operacion', fecha, 'importe_centavos', importe,
                         'moneda', moneda_pago, 'comprobante', documento,
                         'archivo_comprobante', archivo));
    return pago;
  end
  $$;

create function nucleo.conciliar_pago_manual(orden uuid, pago uuid, resultado_nuevo text,
                                             motivo text, actor uuid, origen_pedido text,
                                             sesion uuid)
  returns text
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    identidad uuid;
    o nucleo.orden_pago;
    e nucleo.ejecucion_orden_pago;
    p nucleo.pago_manual;
  begin
    identidad := nucleo.exigir_acto_economico(c, actor, sesion, 'egresos.aprobar_orden',
                                              origen_pedido);
    o := nucleo.orden_pago_bloqueada(c, orden);
    e := nucleo.ejecucion_manual_abierta(c, o);
    select * into p from nucleo.pago_manual x
     where x.consorcio_id = c and x.id = pago and x.ejecucion_id = e.id;
    if not found then
      raise exception 'ese pago no es de esta orden' using errcode = 'P0001',
        hint = 'no_encontrado';
    end if;
    if exists (select 1 from nucleo.conciliacion_pago_manual k where k.pago_id = p.id) then
      raise exception 'ese pago ya está conciliado' using errcode = 'P0001',
        hint = 'conciliacion_repetida';
    end if;
    perform nucleo.exigir_no_ejecuto(c, e.id, actor, identidad);
    perform nucleo.exigir_no_preparo(c, orden, actor, identidad);
    if resultado_nuevo is null or resultado_nuevo not in ({_conciliaciones})
       or (resultado_nuevo = 'observado') <> (nullif(btrim(motivo), '') is not null) then
      raise exception 'la conciliación no es válida' using errcode = 'P0001',
        hint = 'conciliacion_invalida';
    end if;
    insert into nucleo.conciliacion_pago_manual
      (consorcio_id, pago_id, resultado, motivo, conciliado_por, identidad_concilio)
    values (c, p.id, resultado_nuevo, nullif(btrim(motivo), ''), actor, identidad);
    if resultado_nuevo = 'conforme' then
      insert into nucleo.resultado_ejecucion_orden_pago
        (consorcio_id, ejecucion_id, resultado, origen, referencia)
      values (c, e.id, 'pagada', 'manual', p.numero_operacion);
      update nucleo.orden_pago set estado = 'pagada', actualizada_en = now() where id = orden;
    end if;
    perform nucleo.auditar_orden_pago(c, 'consorcio.orden_pago.conciliar_pago_manual', orden,
      actor, jsonb_build_object('estado', o.estado),
      jsonb_build_object('estado', case when resultado_nuevo = 'conforme' then 'pagada'
                                        else o.estado::text end,
                         'version', e.version, 'ejecucion', e.id, 'pago', p.id,
                         'resultado', resultado_nuevo));
    return case when resultado_nuevo = 'conforme' then 'pagada' else o.estado::text end;
  end
  $$;

create function nucleo.declarar_pago_no_realizado(orden uuid, version_vista integer,
                                                  motivo text, actor uuid, origen_pedido text,
                                                  sesion uuid)
  returns uuid
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    identidad uuid;
    o nucleo.orden_pago;
    e nucleo.ejecucion_orden_pago;
    d uuid;
  begin
    identidad := nucleo.exigir_acto_economico(c, actor, sesion, 'egresos.ejecutar_pago',
                                              origen_pedido);
    o := nucleo.orden_pago_bloqueada(c, orden);
    if version_vista is distinct from o.version_vigente then
      raise exception 'esa versión ya no es la vigente' using errcode = 'P0001',
        hint = 'version_desactualizada';
    end if;
    e := nucleo.ejecucion_manual_abierta(c, o);
    perform nucleo.exigir_no_aprobo(c, orden, e.version, actor, identidad);
    perform nucleo.exigir_sin_pago_vigente(e.id);
    -- Si alguien recibió la cuenta, «no se pagó» lo dice alguien que la recibió.
    if exists (select 1 from nucleo.apertura_cuenta_pago_manual a where a.ejecucion_id = e.id)
       and not exists (select 1 from nucleo.apertura_cuenta_pago_manual a
                        where a.ejecucion_id = e.id and a.identidad_abrio = identidad) then
      raise exception 'declara que no se pagó quien vio la cuenta en el acto de ejecución'
        using errcode = 'P0001', hint = 'no_es_quien_ejecuto';
    end if;
    insert into nucleo.declaracion_pago_no_realizado
      (consorcio_id, ejecucion_id, motivo, declarado_por, identidad_declaro)
    values (c, e.id, btrim(motivo), actor, identidad)
    returning id into d;
    perform nucleo.auditar_orden_pago(c, 'consorcio.orden_pago.declarar_pago_no_realizado',
      orden, actor, null,
      jsonb_build_object('estado', o.estado, 'version', e.version, 'ejecucion', e.id,
                         'declaracion', d));
    return d;
  end
  $$;

create function nucleo.confirmar_pago_no_realizado(orden uuid, declaracion uuid, actor uuid,
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
    d nucleo.declaracion_pago_no_realizado;
  begin
    identidad := nucleo.exigir_acto_economico(c, actor, sesion, 'egresos.aprobar_orden',
                                              origen_pedido);
    o := nucleo.orden_pago_bloqueada(c, orden);
    e := nucleo.ejecucion_manual_abierta(c, o);
    select * into d from nucleo.declaracion_pago_no_realizado x
     where x.consorcio_id = c and x.id = declaracion and x.ejecucion_id = e.id;
    if not found then
      raise exception 'esa declaración no es de esta orden' using errcode = 'P0001',
        hint = 'no_encontrado';
    end if;
    perform nucleo.exigir_sin_pago_vigente(e.id);
    -- Superada: después de declararla, alguien recibió la cuenta o registró un pago.
    if exists (select 1 from nucleo.apertura_cuenta_pago_manual a
                where a.ejecucion_id = e.id and a.paso > d.paso)
       or exists (select 1 from nucleo.pago_manual x
                   where x.ejecucion_id = e.id and x.paso > d.paso) then
      raise exception 'después de esa declaración alguien vio la cuenta o registró un pago'
        using errcode = 'P0001', hint = 'estado_invalido';
    end if;
    -- Cada persona que recibió la cuenta (pudo haber transferido) declaró que no pagó, después de
    -- su última apertura.
    if exists (select 1 from nucleo.apertura_cuenta_pago_manual a
                where a.ejecucion_id = e.id
                  and not exists (select 1 from nucleo.declaracion_pago_no_realizado x
                                   where x.ejecucion_id = e.id
                                     and x.identidad_declaro = a.identidad_abrio
                                     and x.paso > a.paso)) then
      raise exception 'falta que declare cada persona que vio la cuenta' using errcode = 'P0001',
        hint = 'estado_invalido';
    end if;
    perform nucleo.exigir_no_ejecuto(c, e.id, actor, identidad);
    if d.identidad_declaro = identidad then
      raise exception 'la misma persona no puede ocupar los dos lugares de esta separación'
        using errcode = 'P0001', hint = 'no_es_quien_ejecuto';
    end if;
    perform nucleo.exigir_separacion_humana(c, actor, d.declarado_por, 'no_es_quien_ejecuto');
    perform nucleo.exigir_no_preparo(c, orden, actor, identidad);
    insert into nucleo.confirmacion_pago_no_realizado
      (consorcio_id, declaracion_id, confirmado_por, identidad_confirmo)
    values (c, d.id, actor, identidad);
    insert into nucleo.resultado_ejecucion_orden_pago
      (consorcio_id, ejecucion_id, resultado, origen, motivo)
    values (c, e.id, 'fallida', 'manual', d.motivo);
    update nucleo.orden_pago set estado = 'fallida', actualizada_en = now() where id = orden;
    perform nucleo.auditar_orden_pago(c, 'consorcio.orden_pago.confirmar_pago_no_realizado',
      orden, actor, jsonb_build_object('estado', o.estado),
      jsonb_build_object('estado', 'fallida', 'version', e.version, 'ejecucion', e.id,
                         'declaracion', d.id));
  end
  $$;

-- Lecturas, sin la cuenta: quien prepara (y ejecuta) y el Consejo, con la barrera de 0049.
create function nucleo.ejecuciones_manuales_lectura(orden uuid, facultad text, actor uuid,
                                                    origen_pedido text, sesion uuid)
  returns table (id uuid, orden_id uuid, version integer, modalidad text,
                 creada_en timestamptz, resultado text, referencia text)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
  begin
    perform nucleo.exigir_lectura_egresos(c, facultad, actor, origen_pedido, sesion);
    return query
      select e.id, e.orden_id, e.version, e.modalidad, e.creada_en, r.resultado, r.referencia
        from nucleo.ejecucion_orden_pago e
        left join nucleo.resultado_ejecucion_orden_pago r
          on r.ejecucion_id = e.id and r.resultado <> 'incierta'
       where e.consorcio_id = c and e.modalidad = 'manual'
         and (orden is null or e.orden_id = orden)
       order by e.creada_en desc, e.id;
  end
  $$;

create function nucleo.pagos_manuales_lectura(orden uuid, facultad text, actor uuid,
                                              origen_pedido text, sesion uuid)
  returns table (id uuid, ejecucion_id uuid, orden_id uuid, corrige_a uuid,
                 numero_operacion text, fecha_operacion date, importe_centavos bigint,
                 moneda text, documento_id uuid, version_documento_id uuid,
                 registrado_por uuid, registrado_en timestamptz, conciliacion text,
                 motivo_conciliacion text, conciliado_por uuid, conciliado_en timestamptz)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
  begin
    perform nucleo.exigir_lectura_egresos(c, facultad, actor, origen_pedido, sesion);
    return query
      select p.id, p.ejecucion_id, e.orden_id, p.corrige_a, p.numero_operacion,
             p.fecha_operacion, p.importe_centavos, p.moneda, p.documento_id,
             p.version_documento_id, p.registrado_por, p.registrado_en, k.resultado, k.motivo,
             k.conciliado_por, k.conciliado_en
        from nucleo.pago_manual p
        join nucleo.ejecucion_orden_pago e on e.consorcio_id = c and e.id = p.ejecucion_id
        left join nucleo.conciliacion_pago_manual k on k.pago_id = p.id
       where p.consorcio_id = c and (orden is null or e.orden_id = orden)
       order by p.registrado_en, p.id;
  end
  $$;

create function nucleo.declaraciones_pago_no_realizado_lectura(orden uuid, facultad text,
                                                               actor uuid, origen_pedido text,
                                                               sesion uuid)
  returns table (id uuid, ejecucion_id uuid, orden_id uuid, motivo text, declarado_por uuid,
                 declarado_en timestamptz, confirmado_por uuid, confirmado_en timestamptz)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
  begin
    perform nucleo.exigir_lectura_egresos(c, facultad, actor, origen_pedido, sesion);
    return query
      select d.id, d.ejecucion_id, e.orden_id, d.motivo, d.declarado_por, d.declarado_en,
             f.confirmado_por, f.confirmado_en
        from nucleo.declaracion_pago_no_realizado d
        join nucleo.ejecucion_orden_pago e on e.consorcio_id = c and e.id = d.ejecucion_id
        left join nucleo.confirmacion_pago_no_realizado f on f.declaracion_id = d.id
       where d.consorcio_id = c and (orden is null or e.orden_id = orden)
       order by d.declarado_en, d.id;
  end
  $$;
"""

RUTAS = (
    "nucleo.cuenta_para_pago_manual(uuid, integer, bytea, uuid, text, uuid)",
    "nucleo.registrar_pago_manual(uuid, integer, bytea, text, date, bigint, text, text, uuid,"
    " uuid, text, uuid)",
    "nucleo.conciliar_pago_manual(uuid, uuid, text, text, uuid, text, uuid)",
    "nucleo.declarar_pago_no_realizado(uuid, integer, text, uuid, text, uuid)",
    "nucleo.confirmar_pago_no_realizado(uuid, uuid, uuid, text, uuid)",
    "nucleo.ejecuciones_manuales_lectura(uuid, text, uuid, text, uuid)",
    "nucleo.pagos_manuales_lectura(uuid, text, uuid, text, uuid)",
    "nucleo.declaraciones_pago_no_realizado_lectura(uuid, text, uuid, text, uuid)",
)
INTERNAS = (
    "nucleo.ejecucion_manual_abierta(uuid, nucleo.orden_pago)",
    "nucleo.ultimo_pago_manual(uuid)",
    "nucleo.pago_manual_observado(uuid)",
    "nucleo.exigir_sin_pago_vigente(uuid)",
    "nucleo.exigir_no_aprobo(uuid, uuid, integer, uuid, uuid)",
    "nucleo.exigir_no_preparo(uuid, uuid, uuid, uuid)",
    "nucleo.exigir_no_ejecuto(uuid, uuid, uuid, uuid)",
    "nucleo.orden_para_pago_manual(uuid, uuid, integer, bytea)",
)
UPGRADE += "".join(f"revoke all on function {f} from public;\n" for f in RUTAS + INTERNAS)
UPGRADE += "".join(f"grant execute on function {f} to cem_app;\n" for f in RUTAS)

GUARDA_DOWNGRADE = """
lock table nucleo.orden_pago in share row exclusive mode;

do $$
begin
  if exists (select 1 from nucleo.ejecucion_orden_pago where modalidad = 'manual')
     or exists (select 1 from nucleo.pago_manual)
     or exists (select 1 from nucleo.operacion_pago_manual)
     or exists (select 1 from nucleo.comprobante_pago_manual)
     or exists (select 1 from nucleo.apertura_cuenta_pago_manual)
     or exists (select 1 from nucleo.conciliacion_pago_manual)
     or exists (select 1 from nucleo.declaracion_pago_no_realizado)
     or exists (select 1 from nucleo.confirmacion_pago_no_realizado)
     or exists (select 1 from nucleo.riel_de_pago_habilitado) then
    raise exception '0055 no baja con pagos manuales ni con el riel habilitado: se perdería su'
                    ' historia o se encolaría el riel donde nadie lo pidió'
      using errcode = 'P0001', hint = 'downgrade_con_datos';
  end if;
end
$$;
"""

DOWNGRADE_FINAL = (
    "".join(f"drop function {f};\n" for f in RUTAS + INTERNAS)
    + "".join(f"drop table {t};\n" for t in reversed(TABLAS))
    + f"""
drop table nucleo.riel_de_pago_habilitado;
drop sequence nucleo.paso_pago_manual;
alter table nucleo.resultado_ejecucion_orden_pago
  drop constraint resultado_ejecucion_orden_pago_origen_check,
  add constraint resultado_ejecucion_orden_pago_origen_check check (origen in ({_origenes_antes}));
alter table nucleo.ejecucion_orden_pago drop column modalidad;
"""
)


def _reemplazar(funcion: str, antes: str, despues: str) -> None:
    definicion = (
        op.get_bind()
        .execute(text("select pg_get_functiondef(cast(:f as regprocedure))"), {"f": funcion})
        .scalar_one()
    )
    if definicion.count(antes) != 1:
        raise RuntimeError(f"0055: {funcion} no tiene exactamente una vez {antes!r}")
    op.execute(definicion.replace(antes, despues))


def upgrade() -> None:
    op.execute(UPGRADE)
    for funcion, cambios in CAMBIOS.items():
        for antes, despues in cambios:
            _reemplazar(funcion, antes, despues)


def downgrade() -> None:
    op.execute(GUARDA_DOWNGRADE)
    for funcion, cambios in CAMBIOS.items():
        for antes, despues in reversed(cambios):
            _reemplazar(funcion, despues, antes)
    op.execute(DOWNGRADE_FINAL)
