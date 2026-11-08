"""Lectura con IA de un documento para cargar datos: la factura de una orden de pago y los datos de
un proveedor (pedido del propietario del 08-11-2026: «donde sea input de datos de un documento debe
realizarse la subida y después IA completa y humano confirma»). RN21: la IA propone, no decide.

**`nucleo.documento.lectura_carga`.** `factura` o `proveedor`: para qué se subió el documento. Se
fija al crearlo, solo por `crear_documento_para_carga` (quien prepara órdenes y proveedores y puede
cargar documentos), y no cambia (`lectura_carga_fija`). Nace en «Facturas y comprobantes» y nunca es
el comprobante de un pago. Un documento cargado desde Documentos no la tiene y no se lee.

**`nucleo.lectura_carga`.** Lo que la capa `llm` leyó de una versión de un documento para carga,
una fila por versión. La escribe solo `cem-trabajos` (`guardar_lectura_carga`, con
`exigir_trabajo`) y no se cambia ni se borra. Ninguna fila escribe en órdenes ni proveedores: la
persona confirma mandando el formulario de siempre. Sin cuentas bancarias: no se piden al modelo y
un `trigger` niega una cuenta en cualquier texto. El CUIT se guarda solo si su dígito verificador
cierra; si no, `cuit_invalido`.

**Qué le toca a quien mira** (`lectura_carga_lectura`): quien prepara (`egresos.preparar_orden`).
`leyendo` sale del trabajo de la cola, como en `0077`.

**La factura de la orden.** `orden_pago_version.documento_id`: la factura que respalda la versión.
`nueva_version_orden_pago` la exige del mismo consorcio, vigente, con un archivo `disponible`, para
carga como `factura` o de «Facturas y comprobantes», y que no sea el comprobante de un pago
(`documento_invalido`). Entra en la huella (`huella_orden_pago_con_factura`): aprobar una versión
aprueba también su factura. Sin factura la huella es la de antes, así que las versiones ya
guardadas no cambian. Modificar cambiando solo la factura es un cambio; renovar la conserva.
`preparar_orden_pago` y `modificar_orden_pago` la suman al final con `DEFAULT NULL`.

**Exportación (`0069`):** `orden_version` suma `documento_id` y `documento`, `lectura_carga`.
`exportar_ordenes_pago` suma la entidad `lectura_carga`, como `exportar_pagos` suma
`lectura_comprobante` (`0077`): cada fila con el modelo, el prompt y lo leído, los textos pasados
por `texto_sin_cuenta`. Va en la parte de órdenes porque la lee quien prepara (la factura de una
orden o lo que respalda un proveedor).

Downgrade: se niega (`downgrade_con_datos`) si hay alguna lectura, algún documento para carga o
alguna versión de orden con factura. Sin eso, vuelve a las definiciones anteriores.

Revisión: 0088
Anterior: 0087
"""

from alembic import op
from sqlalchemy import text

revision = "0088"
down_revision = "0087"
branch_labels = None
depends_on = None

TRABAJO = "documentos.leer_carga"
"""El de `TRABAJO_LEER_CARGA`, en `app/consorcios/lectura_carga.py`."""
CATEGORIA = "facturas_y_comprobantes"
"""La de `CATEGORIA_DE_CARGA` del contrato, para los dos tipos."""
LARGO_CONCEPTO = 120
"""El de `LARGO_CONCEPTO`, en `app/consorcios/lectura_carga.py`."""

ESENCIALES = "bigint, text, text, uuid, text, boolean, uuid"
NUEVA = f"nucleo.nueva_version_orden_pago(uuid, uuid, integer, {ESENCIALES}, uuid, uuid)"
NUEVA_CON = f"nucleo.nueva_version_orden_pago(uuid, uuid, integer, {ESENCIALES}, uuid, uuid, uuid)"
PREPARAR = f"nucleo.preparar_orden_pago({ESENCIALES}, uuid, text, uuid)"
PREPARAR_CON = f"nucleo.preparar_orden_pago({ESENCIALES}, uuid, text, uuid, uuid)"
MODIFICAR = f"nucleo.modificar_orden_pago(uuid, integer, {ESENCIALES}, uuid, text, uuid)"
MODIFICAR_CON = f"nucleo.modificar_orden_pago(uuid, integer, {ESENCIALES}, uuid, text, uuid, uuid)"
RENOVAR = "nucleo.renovar_orden_vencida(uuid, integer, uuid, text, uuid)"
VERSIONES = "nucleo.orden_pago_versiones_lectura(uuid, text, uuid, text, uuid)"
EXPORTAR_ORDENES = "nucleo.exportar_ordenes_pago(uuid, uuid)"
EXPORTAR_DOCUMENTOS = "nucleo.exportar_documentos(uuid, uuid)"

CREAR = "nucleo.crear_documento_para_carga(text, text, date, uuid, text, uuid)"
ES_CARGA = "nucleo.version_es_documento_para_carga(uuid)"
GUARDAR = (
    "nucleo.guardar_lectura_carga(uuid, text, text, boolean, text, text, boolean, text, text, text,"
    " text, text, date, numeric, text, text)"
)
LEER = "nucleo.lectura_carga_lectura(uuid, uuid, uuid, text, uuid)"
HUELLA = "nucleo.huella_orden_pago_con_factura(bytea, uuid)"
FACTURA = "nucleo.exigir_factura_de_la_orden(uuid, uuid)"
FIJA = "nucleo.lectura_carga_fija()"

TEXTOS = ("razon_social", "domicilio", "telefono", "correo", "numero_comprobante", "concepto")
"""Los textos leídos: ninguno lleva una cuenta (`negar_cuenta_en_motivo`)."""
SIN_CUENTA = "".join(
    f"""create trigger {campo}_sin_cuenta before insert on nucleo.lectura_carga
  for each row execute function nucleo.negar_cuenta_en_motivo('{campo}');
"""
    for campo in TEXTOS
)

TABLA = f"""
alter table nucleo.documento
  add column lectura_carga text check (lectura_carga in ('factura', 'proveedor')),
  add constraint documento_carga_no_es_comprobante
    check (lectura_carga is null or orden_comprobante is null);

create function {FIJA.removesuffix("()")}()
  returns trigger
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  begin
    if new.lectura_carga is distinct from old.lectura_carga then
      raise exception 'para qué se subió un documento no cambia' using errcode = 'P0001',
        hint = 'historia_inmutable';
    end if;
    return new;
  end
  $$;
revoke all on function {FIJA} from public;
create trigger lectura_carga_fija before update of lectura_carga on nucleo.documento
  for each row execute function {FIJA};

create table nucleo.lectura_carga (
  consorcio_id          uuid not null,
  id                    uuid not null default plataforma.uuid_v7(),
  version_documento_id  uuid not null unique references nucleo.version_documento (id),
  documento_id          uuid not null,
  tipo                  text not null check (tipo in ('factura', 'proveedor')),
  -- El que informó la capa. Nulo solo si no llegó a responder.
  modelo                text check (length(modelo) between 1 and 200),
  version_prompt        text not null check (length(version_prompt) between 1 and 60),
  legible               boolean not null,
  error                 text check (error in ('falla_del_modelo', 'respuesta_invalida')),
  -- Solo con el dígito verificador comprobado.
  cuit                  text check (nucleo.cuit_valido(cuit)),
  cuit_invalido         boolean not null default false,
  razon_social          text check (length(razon_social) between 1 and 200),
  domicilio             text check (length(domicilio) between 1 and 200),
  telefono              text check (length(telefono) between 1 and 50),
  correo                text check (length(correo) between 3 and 254
                                    and correo ~ '^[^@[:space:]]+@[^@[:space:]]+$'),
  numero_comprobante    text check (length(numero_comprobante) between 1 and 60),
  fecha                 date,
  importe               numeric(18, 2) check (importe > 0),
  moneda                text check (moneda ~ '^[A-Z]{{3}}$'),
  concepto              text check (length(concepto) between 1 and {LARGO_CONCEPTO}),
  leida_en              timestamptz not null default clock_timestamp(),
  primary key (id),
  unique (consorcio_id, id),
  foreign key (consorcio_id, documento_id) references nucleo.documento (consorcio_id, id),
  constraint lectura_carga_cuit_o_invalido check (cuit is null or not cuit_invalido),
  constraint lectura_carga_error_no_es_legible check (error is null or not legible),
  constraint lectura_carga_modelo_informado
    check (modelo is not null or error = 'falla_del_modelo'),
  constraint lectura_carga_sin_campos_si_no_legible check (
    legible or (cuit is null and not cuit_invalido and razon_social is null and domicilio is null
                and telefono is null and correo is null and numero_comprobante is null
                and fecha is null and importe is null and moneda is null and concepto is null))
);
alter table nucleo.lectura_carga enable row level security;
alter table nucleo.lectura_carga force row level security;
create policy por_consorcio on nucleo.lectura_carga for select
  using (consorcio_id = plataforma.consorcio_actual());
revoke all on nucleo.lectura_carga from public, cem_app;

create trigger historia_lectura_carga before update or delete on nucleo.lectura_carga
  for each row execute function nucleo.negar_cambio_historia_orden_pago();
create trigger historia_lectura_carga_truncate before truncate on nucleo.lectura_carga
  for each statement execute function nucleo.negar_cambio_historia_orden_pago();
{SIN_CUENTA}
alter table nucleo.orden_pago_version add column documento_id uuid,
  add foreign key (consorcio_id, documento_id) references nucleo.documento (consorcio_id, id);
"""

FUNCIONES = f"""
-- Quien prepara órdenes y proveedores, con la carga de esa categoría y R10 sin gesto (no mueve
-- dinero: la persona confirma después con el formulario, que pide lo suyo).
create function nucleo.crear_documento_para_carga(tipo text, titulo_nuevo text, fecha_nueva date,
                                                  actor uuid, origen_pedido text, sesion uuid)
  returns uuid
  language plpgsql
  security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    d nucleo.documento;
  begin
    perform nucleo.exigir_acto_sin_gesto(c, actor, sesion, 'egresos.preparar_orden',
                                         origen_pedido);
    perform nucleo.exigir_carga_documentos(c, actor, origen_pedido, '{CATEGORIA}');
    insert into nucleo.documento (consorcio_id, categoria, titulo, fecha, cargado_por,
                                  lectura_carga)
    values (c, '{CATEGORIA}', btrim(titulo_nuevo), fecha_nueva, actor, tipo)
    returning * into d;
    -- El título no va a la auditoría: es texto libre.
    perform nucleo.auditar_documento(c, 'consorcio.documento.crear', 'documento', d.id, actor,
      origen_pedido, jsonb_build_object('categoria', d.categoria, 'fecha', d.fecha,
                                        'lectura_carga', d.lectura_carga));
    return d.id;
  end
  $$;

-- Solo los documentos creados para carga se leen con este prompt.
create function nucleo.version_es_documento_para_carga(version uuid)
  returns boolean
  language plpgsql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
  begin
    perform nucleo.exigir_trabajo(c);
    return exists (
      select 1 from nucleo.version_documento v
        join nucleo.documento d on d.consorcio_id = v.consorcio_id and d.id = v.documento_id
       where v.consorcio_id = c and v.id = version and d.lectura_carga is not null);
  end
  $$;

create function nucleo.guardar_lectura_carga(version uuid, modelo_informado text, prompt text,
                                             es_legible boolean, falla text, cuit_leido text,
                                             cuit_mal boolean, razon text, domicilio_leido text,
                                             telefono_leido text, correo_leido text, numero text,
                                             fecha_leida date, importe_leido numeric,
                                             moneda_leida text, concepto_leido text)
  returns boolean
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    documento uuid;
    tipo_carga text;
  begin
    perform nucleo.exigir_trabajo(c);
    select d.id, d.lectura_carga into documento, tipo_carga
      from nucleo.version_documento v
      join nucleo.documento d on d.consorcio_id = v.consorcio_id and d.id = v.documento_id
     where v.consorcio_id = c and v.id = version;
    if tipo_carga is null then
      raise exception 'la versión no es de un documento para carga' using errcode = 'P0001',
        hint = 'documento_invalido';
    end if;
    insert into nucleo.lectura_carga
      (consorcio_id, version_documento_id, documento_id, tipo, modelo, version_prompt, legible,
       error, cuit, cuit_invalido, razon_social, domicilio, telefono, correo, numero_comprobante,
       fecha, importe, moneda, concepto)
    values (c, version, documento, tipo_carga, modelo_informado, prompt, es_legible, falla,
            cuit_leido, coalesce(cuit_mal, false), razon, domicilio_leido, telefono_leido,
            correo_leido, numero, fecha_leida, importe_leido, moneda_leida, concepto_leido)
    on conflict (version_documento_id) do nothing;
    return found;
  end
  $$;

-- Sin filas si el documento no existe en el consorcio, está borrado, no es para carga o la versión
-- no es suya: la ruta responde 404.
create function nucleo.lectura_carga_lectura(documento uuid, version uuid, actor uuid,
                                             origen_pedido text, sesion uuid)
  returns table (tipo text, estado text, cuit text, cuit_invalido boolean, razon_social text,
                 domicilio text, telefono text, correo text, numero_comprobante text, fecha date,
                 importe numeric, moneda text, concepto text)
  language plpgsql security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    tipo_carga text;
    l nucleo.lectura_carga;
    trabajo text;
  begin
    perform nucleo.exigir_lectura_egresos(c, 'egresos.preparar_orden', actor, origen_pedido,
                                          sesion);
    select d.lectura_carga into tipo_carga
      from nucleo.version_documento v
      join nucleo.documento d on d.consorcio_id = v.consorcio_id and d.id = v.documento_id
     where v.consorcio_id = c and v.id = version and d.id = documento and d.estado = 'vigente';
    if tipo_carga is null then
      return;
    end if;
    select * into l from nucleo.lectura_carga x
     where x.consorcio_id = c and x.version_documento_id = version;
    if l.id is not null then
      return query select tipo_carga, case when l.legible then 'leida' else 'no_leida' end,
        l.cuit, l.cuit_invalido, l.razon_social, l.domicilio, l.telefono, l.correo,
        l.numero_comprobante, l.fecha, l.importe, l.moneda, l.concepto;
      return;
    end if;
    select j.estado::text into trabajo from plataforma.jobs j
     where j.consorcio_id = c and j.clave_unica = '{TRABAJO}:' || version::text;
    return query select tipo_carga,
      case when trabajo in ('pendiente', 'en_curso') then 'leyendo'
           when trabajo = 'muerto' then 'no_leida'
           else 'sin_lectura' end,
      null::text, false, null::text, null::text, null::text, null::text, null::text, null::date,
      null::numeric, null::text, null::text;
  end
  $$;

-- Sin factura, la huella de siempre: las versiones ya guardadas no cambian.
create function nucleo.huella_orden_pago_con_factura(base bytea, factura uuid)
  returns bytea
  language sql immutable
  set search_path = pg_catalog, pg_temp
  as $$
    select case when factura is null then base
                else sha256(base || convert_to(jsonb_build_object('factura', factura)::text,
                                               'UTF8')) end
  $$;

create function nucleo.exigir_factura_de_la_orden(c uuid, factura uuid)
  returns void
  language plpgsql stable
  set search_path = pg_catalog, pg_temp
  as $$
  begin
    if factura is null then
      return;
    end if;
    if not exists (
         select 1 from nucleo.documento d
          where d.consorcio_id = c and d.id = factura and d.orden_comprobante is null
            and (d.lectura_carga = 'factura' or d.categoria = '{CATEGORIA}'))
       or not nucleo.documento_respalda(c, factura) then
      raise exception 'la factura no es un documento vigente del consorcio'
        using errcode = 'P0001', hint = 'documento_invalido';
    end if;
  end
  $$;
"""

INTERNAS = (HUELLA, FACTURA)
RUTAS = (CREAR, ES_CARGA, GUARDAR, LEER)

FIRMA_NUEVA = (
    "contrato_nuevo uuid, actor uuid, identidad uuid)\n RETURNS bytea",
    "contrato_nuevo uuid, actor uuid, identidad uuid, factura uuid)\n RETURNS bytea",
)
NUEVA_CAMBIOS = (
    FIRMA_NUEVA,
    (
        "    select * into ev from nucleo.evaluar_orden_pago(",
        "    -- 0088: la factura, del consorcio, vigente y con su archivo aprobado.\n"
        "    perform nucleo.exigir_factura_de_la_orden(c, factura);\n"
        "    select * into ev from nucleo.evaluar_orden_pago(",
    ),
    (
        "           ev.franja, ev.umbral_id, ev.contrato_id);\n",
        "           ev.franja, ev.umbral_id, ev.contrato_id);\n"
        "    -- 0088: la factura, si tiene, también entra en la huella.\n"
        "    h := nucleo.huella_orden_pago_con_factura(h, factura);\n",
    ),
    (
        "       contrato_id, alerta_fraccionamiento)\n",
        "       contrato_id, alerta_fraccionamiento, documento_id)\n",
    ),
    (
        "            ev.contrato_id, ev.alerta);\n",
        "            ev.contrato_id, ev.alerta, factura);\n",
    ),
)
PREPARAR_CAMBIOS = (
    (
        "contrato_nuevo uuid, actor uuid, origen_pedido text, sesion uuid)\n RETURNS uuid",
        "contrato_nuevo uuid, actor uuid, origen_pedido text, sesion uuid,"
        " factura uuid DEFAULT NULL::uuid)\n RETURNS uuid",
    ),
    ("           actor, identidad);\n", "           actor, identidad, factura);\n"),
)
MODIFICAR_CAMBIOS = (
    (
        "contrato_nuevo uuid, actor uuid, origen_pedido text, sesion uuid)\n RETURNS integer",
        "contrato_nuevo uuid, actor uuid, origen_pedido text, sesion uuid,"
        " factura uuid DEFAULT NULL::uuid)\n RETURNS integer",
    ),
    (
        "        v.comprobante, v.usa_fondo_reserva, v.contrato_id)\n",
        "        v.comprobante, v.usa_fondo_reserva, v.contrato_id, v.documento_id)\n",
    ),
    (
        "                             btrim(comprobante_nuevo), usa_reserva, contrato_nuevo)\n",
        "                             btrim(comprobante_nuevo), usa_reserva, contrato_nuevo,\n"
        "                             factura)\n",
    ),
    (
        "           contrato_nuevo, actor, identidad);\n",
        "           contrato_nuevo, actor, identidad, factura);\n",
    ),
)
RENOVAR_CAMBIOS = (
    (
        "           case when sin_contrato then null else v.contrato_id end, actor, identidad);\n",
        "           case when sin_contrato then null else v.contrato_id end, actor, identidad,\n"
        "           v.documento_id);\n",
    ),
)
VERSIONES_CAMBIOS = (
    (
        " cuenta_destino text, aprobada_por uuid[])\n",
        " cuenta_destino text, aprobada_por uuid[], documento_id uuid)\n",
    ),
    (
        "                          and a.version = v.numero), '{}')\n",
        "                          and a.version = v.numero), '{}'),\n"
        "             v.documento_id\n",
    ),
)
EXPORTAR_ORDENES_CAMBIOS = (
    (
        "                                'contrato_id', v.contrato_id,\n",
        "                                'contrato_id', v.contrato_id,\n"
        "                                'documento_id', v.documento_id,\n",
    ),
    (
        "        from nucleo.aprobaciones_orden_pago x\n       where x.consorcio_id = c\n"
        "       order by x.vigente_desde, x.id;\n",
        "        from nucleo.aprobaciones_orden_pago x\n       where x.consorcio_id = c\n"
        "       order by x.vigente_desde, x.id;\n"
        "    return query\n"
        "      select 'lectura_carga'::text,\n"
        "             jsonb_build_object('id', l.id,\n"
        "                                'version_documento_id', l.version_documento_id,\n"
        "                                'documento_id', l.documento_id, 'tipo', l.tipo,\n"
        "                                'modelo', l.modelo, 'version_prompt', l.version_prompt,\n"
        "                                'legible', l.legible, 'error', l.error, 'cuit', l.cuit,\n"
        "                                'cuit_invalido', l.cuit_invalido,\n"
        "                                'razon_social', nucleo.texto_sin_cuenta(l.razon_social),\n"
        "                                'domicilio', nucleo.texto_sin_cuenta(l.domicilio),\n"
        "                                'telefono', nucleo.texto_sin_cuenta(l.telefono),\n"
        "                                'correo', nucleo.texto_sin_cuenta(l.correo),\n"
        "                                'numero_comprobante',"
        " nucleo.texto_sin_cuenta(l.numero_comprobante),\n"
        "                                'fecha', l.fecha, 'importe', l.importe,\n"
        "                                'moneda', l.moneda,\n"
        "                                'concepto', nucleo.texto_sin_cuenta(l.concepto),\n"
        "                                'leida_en', l.leida_en)\n"
        "        from nucleo.lectura_carga l\n"
        "       where l.consorcio_id = c\n"
        "       order by l.leida_en, l.id;\n",
    ),
)
EXPORTAR_DOCUMENTOS_CAMBIOS = (
    (
        "                                'fecha', d.fecha, 'cargado_en', d.cargado_en)\n",
        "                                'fecha', d.fecha, 'cargado_en', d.cargado_en,\n"
        "                                'lectura_carga', d.lectura_carga)\n",
    ),
)

OTRA_FIRMA = (
    # (antes, después, cambios, se da a cem_app)
    (NUEVA, NUEVA_CON, NUEVA_CAMBIOS, False),
    (PREPARAR, PREPARAR_CON, PREPARAR_CAMBIOS, True),
    (MODIFICAR, MODIFICAR_CON, MODIFICAR_CAMBIOS, True),
    (VERSIONES, VERSIONES, VERSIONES_CAMBIOS, True),
)
"""Otra firma o lo que devuelve: se borra y se crea de nuevo. La nueva de `nueva_version_orden_pago`
va antes que quienes la llaman; plpgsql la resuelve al ejecutar."""
EN_SU_LUGAR = (
    (RENOVAR, RENOVAR_CAMBIOS),
    (EXPORTAR_ORDENES, EXPORTAR_ORDENES_CAMBIOS),
    (EXPORTAR_DOCUMENTOS, EXPORTAR_DOCUMENTOS_CAMBIOS),
)

GUARDA_DOWNGRADE = """
-- Hasta el fin del downgrade nadie guarda: la guarda vale para lo que se borra.
lock table nucleo.lectura_carga, nucleo.documento, nucleo.orden_pago_version
  in access exclusive mode;
do $$
begin
  if exists (select 1 from nucleo.lectura_carga)
     or exists (select 1 from nucleo.documento where lectura_carga is not null)
     or exists (select 1 from nucleo.orden_pago_version where documento_id is not null) then
    raise exception '0088 no baja con documentos para carga, sus lecturas o facturas de órdenes:'
      ' se perdería el rastro' using errcode = 'P0001', hint = 'downgrade_con_datos';
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
            raise RuntimeError(f"0088: {funcion} no tiene exactamente una vez {antes!r}")
        definicion = definicion.replace(antes, despues)
    return definicion


def _otra(desde: str, hasta: str, cambios, de_la_app: bool, *, al_reves: bool = False) -> None:
    definicion = _definicion(desde, cambios, al_reves=al_reves)
    op.execute(f"drop function {desde}")
    op.execute(definicion)
    op.execute(f"revoke all on function {hasta} from public")
    if de_la_app:
        op.execute(f"grant execute on function {hasta} to cem_app")


def upgrade() -> None:
    op.execute(TABLA)
    op.execute(FUNCIONES)
    for funcion in INTERNAS + RUTAS:
        op.execute(f"revoke all on function {funcion} from public")
    for funcion in RUTAS:
        op.execute(f"grant execute on function {funcion} to cem_app")
    for desde, hasta, cambios, de_la_app in OTRA_FIRMA:
        _otra(desde, hasta, cambios, de_la_app)
    for funcion, cambios in EN_SU_LUGAR:
        op.execute(_definicion(funcion, cambios, al_reves=False))


def downgrade() -> None:
    op.execute(GUARDA_DOWNGRADE)
    for funcion, cambios in reversed(EN_SU_LUGAR):
        op.execute(_definicion(funcion, cambios, al_reves=True))
    for desde, hasta, cambios, de_la_app in reversed(OTRA_FIRMA):
        _otra(hasta, desde, cambios, de_la_app, al_reves=True)
    for funcion in RUTAS + INTERNAS:
        op.execute(f"drop function {funcion}")
    op.execute("alter table nucleo.orden_pago_version drop column documento_id")
    op.execute("drop table nucleo.lectura_carga")
    op.execute("drop trigger lectura_carga_fija on nucleo.documento")
    op.execute(f"drop function {FIJA}")
    op.execute(
        "alter table nucleo.documento drop constraint documento_carga_no_es_comprobante,"
        " drop column lectura_carga"
    )
