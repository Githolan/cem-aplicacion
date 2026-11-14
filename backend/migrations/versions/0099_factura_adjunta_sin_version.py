"""Subir la factura que falta a una orden ya aprobada o pagada (decisión del propietario del
13-11-2026; informe de fricción, AC-44): **se adjunta con constancia, sin versión nueva y sin volver
a aprobación**, porque importe, proveedor y cuenta no cambian.

**La constancia** (`nucleo.factura_adjunta_orden_pago`). Una fila por archivo subido: la orden, la
versión vigente a la que se adjuntó, el documento y la versión exacta del archivo, quién (persona e
identidad humana) y cuándo. No se cambia ni se borra (`negar_cambio_historia_orden_pago`), como la
historia de la orden. Cita su documento (`documento_citado_vigente` y `documento_referenciado`):
ese documento ya no se quita ni se retira.

**El acto** (`nucleo.adjuntar_factura_orden_pago`, `security definer`, solo `cem_app`). Lo hace
quien puede cargar la factura al preparar: `egresos.preparar_orden` con R10 sin el gesto
(`exigir_acto_sin_gesto`, como preparar, modificar y renovar) y la carga de «Facturas y
comprobantes» (`exigir_carga_documentos`, como `crear_documento_para_carga`). En una sola
transacción, con la orden bloqueada (`orden_pago_bloqueada`):

- la orden tiene que estar aprobada o más adelante (`CON_DINERO_COMPROMETIDO`, las mismas que
  «Facturas faltantes» de `0094`; si no, `estado_invalido`) y en la versión que se vio
  (`version_desactualizada`);
- no hay otra subida a esa versión en el antivirus, de un documento vigente
  (`factura_ya_adjunta`);
- para subirla por primera vez (`reemplaza` nulo), su versión vigente no tiene factura ni otra
  adjunta que haya pasado el antivirus (`factura_ya_adjunta`). Una subida que el antivirus rechazó
  no cuenta: se sube otra;
- para reemplazarla (decisión del propietario del 13-11-2026), `reemplaza` es la constancia de la
  adjunta que vale hoy: la última de esa versión que pasó el antivirus. Si ya no es esa, o la
  factura es la de la versión, `factura_no_vigente`. La anterior no se borra: queda con su
  constancia y la nueva la cita (`reemplaza_a`). Mientras la nueva está en el antivirus vale la
  anterior; si el antivirus la rechaza, la anterior sigue valiendo;
- crea el documento en «Facturas y comprobantes» (fecha: el día del consorcio), su versión con
  `nucleo.nueva_version` (cuarentena; la ruta encola el antivirus en la misma transacción), la
  constancia y el evento `consorcio.orden_pago.adjuntar_factura` o
  `consorcio.orden_pago.reemplazar_factura` (sin `estado`: no es una transición de la orden; el
  reemplazo dice en `antes` qué archivo reemplaza).

**Lo que no toca.** Ni `nucleo.orden_pago` (ni el estado, ni la versión vigente, ni
`actualizada_en`) ni `nucleo.orden_pago_version` (ni la huella ni `factura_version_id`): no hay
versión nueva ni aprobación nueva. Por eso la factura adjunta no está en la huella aprobada; la
ficha dice que se adjuntó después.

**Quién la ve** (`nucleo.factura_adjunta_lectura`): las mismas lecturas que la orden
(`exigir_lectura_egresos`, quien prepara o quien revisa), con el estado del archivo y el nombre de
quien la subió, como el retiro (`retiro_orden_pago_lectura`).

**Facturas faltantes** (`nucleo.panel_comprobantes_faltantes`, otra forma: se borra y se crea).
Una orden deja de faltar cuando la factura adjunta a su versión vigente pasó el antivirus
(`nucleo.factura_adjunta_disponible`). Cada fila suma la versión vigente (la que manda el acto) y
`factura_subida`: el estado del último archivo subido, para decir que está en el antivirus o que no
lo pasó.

**Renovar** (`nucleo.renovar_orden_vencida`, en su lugar) conserva la factura: la de la versión o,
si no tiene, la adjunta que pasó el antivirus. La versión nueva la lleva en la huella y el Consejo
la aprueba con ella.

**Exportación (`0069`):** `exportar_ordenes_pago` suma la entidad `factura_adjunta`, con el nombre
de quien la subió y no su identificador (`nombre_para_exportar`).

Downgrade: se niega (`downgrade_con_datos`) si hay alguna constancia. Sin eso, vuelve a las
definiciones anteriores.

Revisión: 0099
Anterior: 0098
"""

from alembic import op
from sqlalchemy import text

revision = "0099"
down_revision = "0098"
branch_labels = None
depends_on = None

CATEGORIA = "facturas_y_comprobantes"
"""La de `CATEGORIA_DE_CARGA` del contrato: donde nace una factura (`0088`)."""
CON_DINERO_COMPROMETIDO = "('aprobada', 'en_ejecucion', 'en_verificacion', 'pagada', 'conciliada')"
"""Los de `CON_FACTURA_ESPERADA` del contrato y `CON_DINERO_COMPROMETIDO` de `0094`."""
ACCION_AUDITADA = "consorcio.orden_pago.adjuntar_factura"
ACCION_REEMPLAZO = "consorcio.orden_pago.reemplazar_factura"

TABLA_CONSTANCIA = "nucleo.factura_adjunta_orden_pago"
REFERENCIAS_NUEVAS = ((TABLA_CONSTANCIA, "documento_id"),)
"""Lo que esta migración suma a `REFERENCIAS` de `0089`: tablas que citan un documento."""

ADJUNTAR = (
    "nucleo.adjuntar_factura_orden_pago(uuid, integer, uuid, text, text, integer, text, uuid, text,"
    " uuid)"
)
LEER = "nucleo.factura_adjunta_lectura(uuid, text, uuid, text, uuid)"
DISPONIBLE = "nucleo.factura_adjunta_disponible(uuid, uuid, integer)"
FALTANTES = "nucleo.panel_comprobantes_faltantes()"
RENOVAR = "nucleo.renovar_orden_vencida(uuid, integer, uuid, text, uuid)"
REFERENCIADO = "nucleo.documento_referenciado(uuid, uuid)"
EXPORTAR_ORDENES = "nucleo.exportar_ordenes_pago(uuid, uuid)"

RUTAS = (ADJUNTAR, LEER)
INTERNAS = (DISPONIBLE,)

TABLA = f"""
create table {TABLA_CONSTANCIA} (
  consorcio_id        uuid not null,
  id                  uuid primary key default plataforma.uuid_v7(),
  orden_id            uuid not null,
  version             integer not null,
  documento_id        uuid not null,
  factura_version_id  uuid not null unique references nucleo.version_documento (id),
  adjuntada_por       uuid not null references nucleo.persona (id),
  identidad_adjunto   uuid not null,
  adjuntada_en        timestamptz not null default now(),
  -- La adjunta que esta reemplaza, de la misma orden y versión; nula en la primera.
  reemplaza_a         uuid,
  unique (orden_id, version, id),
  foreign key (orden_id, version, reemplaza_a)
    references {TABLA_CONSTANCIA} (orden_id, version, id),
  foreign key (consorcio_id, orden_id) references nucleo.orden_pago (consorcio_id, id),
  foreign key (orden_id, version) references nucleo.orden_pago_version (orden_id, numero),
  foreign key (consorcio_id, documento_id) references nucleo.documento (consorcio_id, id),
  foreign key (consorcio_id, identidad_adjunto)
    references nucleo.identidad_humana (consorcio_id, id)
);
create index factura_adjunta_orden_pago_orden
  on {TABLA_CONSTANCIA} (consorcio_id, orden_id, version);
create index factura_adjunta_orden_pago_documento
  on {TABLA_CONSTANCIA} (consorcio_id, documento_id);
alter table {TABLA_CONSTANCIA} enable row level security;
alter table {TABLA_CONSTANCIA} force row level security;
create policy por_consorcio on {TABLA_CONSTANCIA} for select
  using (consorcio_id = plataforma.consorcio_actual());
revoke all on {TABLA_CONSTANCIA} from public, cem_app;

create trigger historia_factura_adjunta before update or delete on {TABLA_CONSTANCIA}
  for each row execute function nucleo.negar_cambio_historia_orden_pago();
create trigger historia_factura_adjunta_truncate before truncate on {TABLA_CONSTANCIA}
  for each statement execute function nucleo.negar_cambio_historia_orden_pago();
create trigger documento_citado_vigente before insert or update of documento_id
  on {TABLA_CONSTANCIA}
  for each row execute function nucleo.documento_citado_vigente('documento_id');
"""

FUNCIONES = f"""
-- La factura adjunta a esa versión de la orden que vale: la última que pasó el antivirus, de un
-- documento vigente (las anteriores quedaron reemplazadas); nula si no hay.
create function nucleo.factura_adjunta_disponible(c uuid, orden uuid, numero integer)
  returns uuid
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select a.factura_version_id
      from nucleo.factura_adjunta_orden_pago a
      join nucleo.version_documento x on x.id = a.factura_version_id
      join nucleo.documento d on d.consorcio_id = a.consorcio_id and d.id = a.documento_id
     where a.consorcio_id = c and a.orden_id = orden and a.version = numero
       and x.estado = 'disponible' and d.estado = 'vigente'
     order by a.adjuntada_en desc, a.id desc
     limit 1
  $$;

create function nucleo.adjuntar_factura_orden_pago(orden uuid, version_esperada integer,
                                                   reemplaza uuid,
                                                   titulo_nuevo text, tipo_nuevo text,
                                                   tamano_nuevo integer, sha256_nuevo text,
                                                   actor uuid, origen_pedido text, sesion uuid)
  returns table (nueva_version_id uuid, nueva_clave_objeto text, nuevo_documento_id uuid)
  language plpgsql
  security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    o nucleo.orden_pago;
    v nucleo.orden_pago_version;
    identidad uuid;
    d nucleo.documento;
    archivo record;
    vale nucleo.factura_adjunta_orden_pago;
  begin
    -- Lo mismo que cargar la factura al preparar: la facultad de preparar, R10 sin el gesto (no
    -- mueve dinero ni cambia la orden) y la carga de la categoría.
    identidad := nucleo.exigir_acto_sin_gesto(c, actor, sesion, 'egresos.preparar_orden',
                                              origen_pedido);
    perform nucleo.exigir_carga_documentos(c, actor, origen_pedido, '{CATEGORIA}');
    o := nucleo.orden_pago_bloqueada(c, orden);
    if o.estado not in {CON_DINERO_COMPROMETIDO} then
      raise exception 'solo se adjunta la factura a una orden aprobada o más adelante'
        using errcode = 'P0001', hint = 'estado_invalido';
    end if;
    if version_esperada is distinct from o.version_vigente then
      raise exception 'la orden cambió mientras tanto' using errcode = 'P0001',
        hint = 'version_desactualizada';
    end if;
    select * into v from nucleo.orden_pago_version x
     where x.orden_id = orden and x.numero = o.version_vigente;
    -- Una por vez en el antivirus, sea la primera o un reemplazo.
    if exists (select 1 from nucleo.factura_adjunta_orden_pago a
                 join nucleo.version_documento x on x.id = a.factura_version_id
                 join nucleo.documento y on y.consorcio_id = a.consorcio_id
                  and y.id = a.documento_id
                where a.consorcio_id = c and a.orden_id = orden and a.version = o.version_vigente
                  and x.estado = 'en_cuarentena' and y.estado = 'vigente') then
      raise exception 'hay otra factura en el antivirus' using errcode = 'P0001',
        hint = 'factura_ya_adjunta';
    end if;
    -- La adjunta que vale hoy: la misma regla que `factura_adjunta_disponible`.
    select a.* into vale from nucleo.factura_adjunta_orden_pago a
     where a.consorcio_id = c and a.orden_id = orden and a.version = o.version_vigente
       and a.factura_version_id = nucleo.factura_adjunta_disponible(c, orden, o.version_vigente);
    if reemplaza is null then
      if v.factura_version_id is not null or vale.id is not null then
        raise exception 'la orden ya tiene su factura' using errcode = 'P0001',
          hint = 'factura_ya_adjunta';
      end if;
    elsif v.factura_version_id is not null or vale.id is distinct from reemplaza then
      raise exception 'la factura a reemplazar no es la que vale para la orden'
        using errcode = 'P0001', hint = 'factura_no_vigente';
    end if;
    insert into nucleo.documento (consorcio_id, categoria, titulo, fecha, cargado_por)
    values (c, '{CATEGORIA}', btrim(titulo_nuevo), nucleo.hoy_del_consorcio(c), actor)
    returning * into d;
    -- El título no va a la auditoría: es texto libre.
    perform nucleo.auditar_documento(c, 'consorcio.documento.crear', 'documento', d.id, actor,
      origen_pedido, jsonb_build_object('categoria', d.categoria, 'fecha', d.fecha));
    select * into archivo
      from nucleo.nueva_version(d.id, tipo_nuevo, tamano_nuevo, sha256_nuevo, actor,
                                origen_pedido);
    insert into nucleo.factura_adjunta_orden_pago
      (consorcio_id, orden_id, version, documento_id, factura_version_id, adjuntada_por,
       identidad_adjunto, reemplaza_a)
    values (c, orden, o.version_vigente, d.id, archivo.id, actor, identidad, reemplaza);
    -- Sin `estado`: no es una transición de la orden (la exportación las saca de ahí).
    if reemplaza is null then
      perform nucleo.auditar_orden_pago(c, '{ACCION_AUDITADA}', orden, actor,
        jsonb_build_object('version', o.version_vigente),
        jsonb_build_object('version', o.version_vigente, 'documento_id', d.id,
                           'factura_version_id', archivo.id));
    else
      perform nucleo.auditar_orden_pago(c, '{ACCION_REEMPLAZO}', orden, actor,
        jsonb_build_object('version', o.version_vigente, 'documento_id', vale.documento_id,
                           'factura_version_id', vale.factura_version_id),
        jsonb_build_object('version', o.version_vigente, 'documento_id', d.id,
                           'factura_version_id', archivo.id));
    end if;
    return query select archivo.id, archivo.clave_objeto, d.id;
  end
  $$;

create function nucleo.factura_adjunta_lectura(orden uuid, facultad text, actor uuid,
                                               origen_pedido text, sesion uuid)
  returns table (id uuid, version integer, documento_id uuid, factura_version_id uuid,
                 estado text, adjuntada_por uuid, adjuntada_por_nombre text,
                 adjuntada_en timestamptz, reemplaza_a uuid)
  language plpgsql
  security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
  begin
    perform nucleo.exigir_lectura_egresos(c, facultad, actor, origen_pedido, sesion);
    return query
      select a.id, a.version, a.documento_id, a.factura_version_id, x.estado, a.adjuntada_por,
             p.nombre, a.adjuntada_en, a.reemplaza_a
        from nucleo.factura_adjunta_orden_pago a
        join nucleo.orden_pago o on o.consorcio_id = a.consorcio_id and o.id = a.orden_id
        join nucleo.version_documento x on x.id = a.factura_version_id
        join nucleo.persona p on p.id = a.adjuntada_por
       where a.consorcio_id = c and a.orden_id = orden
         and (facultad = 'egresos.preparar_orden' or o.estado <> 'borrador')
       order by a.adjuntada_en, a.id;
  end
  $$;
"""

FALTANTES_CAMBIOS = (
    (
        " RETURNS TABLE(orden_id uuid, estado text, proveedor text, concepto text,"
        " importe_centavos bigint, moneda text, actualizada_en timestamp with time zone)\n",
        " RETURNS TABLE(orden_id uuid, version integer, estado text, proveedor text,"
        " concepto text, importe_centavos bigint, moneda text,"
        " actualizada_en timestamp with time zone, factura_subida text)\n",
    ),
    (
        "      select o.id, o.estado::text, pv.razon_social, v.concepto, v.importe_centavos,"
        " v.moneda,\n             o.actualizada_en\n",
        "      select o.id, o.version_vigente, o.estado::text, pv.razon_social, v.concepto,\n"
        "             v.importe_centavos, v.moneda, o.actualizada_en,\n"
        "             -- 0099: el último archivo subido después de aprobada, si hay.\n"
        "             (select x.estado from nucleo.factura_adjunta_orden_pago a\n"
        "                join nucleo.version_documento x on x.id = a.factura_version_id\n"
        "               where a.consorcio_id = c and a.orden_id = o.id\n"
        "                 and a.version = o.version_vigente\n"
        "               order by a.adjuntada_en desc, a.id desc limit 1)\n",
    ),
    (
        "         and v.factura_version_id is null\n",
        "         and v.factura_version_id is null\n"
        "         -- 0099: tampoco falta si la adjunta después pasó el antivirus.\n"
        "         and nucleo.factura_adjunta_disponible(c, o.id, o.version_vigente) is null\n",
    ),
)
RENOVAR_CAMBIOS = (
    (
        "           v.factura_version_id);\n",
        "           -- 0099: si la versión no tiene factura, la adjunta después que pasó el\n"
        "           -- antivirus.\n"
        "           coalesce(v.factura_version_id,\n"
        "                    nucleo.factura_adjunta_disponible(c, orden, o.version_vigente)));\n",
    ),
)
REFERENCIADO_CAMBIOS = (
    (
        "        or exists (select 1 from nucleo.version_documento v\n",
        f"        or exists (select 1 from {TABLA_CONSTANCIA} x\n"
        "                    where x.consorcio_id = c and x.documento_id = documento)\n"
        "        or exists (select 1 from nucleo.version_documento v\n",
    ),
)
EXPORTAR_CAMBIOS = (
    (
        "       order by a.orden_id, a.version, a.creada_en, a.id;\n",
        "       order by a.orden_id, a.version, a.creada_en, a.id;\n"
        "    -- 0099: la factura subida después de aprobada, con el nombre de quien la subió.\n"
        "    return query\n"
        "      select 'factura_adjunta'::text,\n"
        "             jsonb_build_object('id', f.id, 'orden_id', f.orden_id,"
        " 'version', f.version,\n"
        "                                'documento_id', f.documento_id,\n"
        "                                'factura_version_id', f.factura_version_id,\n"
        "                                'adjuntada_por_nombre',"
        " nucleo.nombre_para_exportar(c, f.adjuntada_por, hoy),\n"
        "                                'adjuntada_en', f.adjuntada_en,\n"
        "                                'reemplaza_a', f.reemplaza_a)\n"
        f"        from {TABLA_CONSTANCIA} f\n"
        "       where f.consorcio_id = c\n"
        "       order by f.adjuntada_en, f.id;\n",
    ),
)

EN_SU_LUGAR = (
    (RENOVAR, RENOVAR_CAMBIOS),
    (REFERENCIADO, REFERENCIADO_CAMBIOS),
    (EXPORTAR_ORDENES, EXPORTAR_CAMBIOS),
)
"""Misma firma y lo mismo que devuelven: `create or replace` con la definición cambiada."""

GUARDA_DOWNGRADE = f"""
-- Hasta el fin del downgrade nadie adjunta: la guarda vale para lo que se borra.
lock table {TABLA_CONSTANCIA} in access exclusive mode;
do $$
begin
  if exists (select 1 from {TABLA_CONSTANCIA}) then
    raise exception '0099 no baja con facturas adjuntas: se perdería la constancia'
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
            raise RuntimeError(f"0099: {funcion} no tiene exactamente una vez {antes!r}")
        definicion = definicion.replace(antes, despues)
    return definicion


def _otra_forma_de_faltantes(*, al_reves: bool) -> None:
    """Lo que devuelve cambia: se borra y se crea de nuevo, con los mismos privilegios."""
    definicion = _definicion(FALTANTES, FALTANTES_CAMBIOS, al_reves=al_reves)
    op.execute(f"drop function {FALTANTES}")
    op.execute(definicion)
    op.execute(f"revoke all on function {FALTANTES} from public")
    op.execute(f"grant execute on function {FALTANTES} to cem_app")


def upgrade() -> None:
    op.execute(TABLA)
    op.execute(FUNCIONES)
    for funcion in RUTAS + INTERNAS:
        op.execute(f"revoke all on function {funcion} from public")
    for funcion in RUTAS:
        op.execute(f"grant execute on function {funcion} to cem_app")
    _otra_forma_de_faltantes(al_reves=False)
    for funcion, cambios in EN_SU_LUGAR:
        op.execute(_definicion(funcion, cambios, al_reves=False))


def downgrade() -> None:
    op.execute(GUARDA_DOWNGRADE)
    for funcion, cambios in reversed(EN_SU_LUGAR):
        op.execute(_definicion(funcion, cambios, al_reves=True))
    _otra_forma_de_faltantes(al_reves=True)
    for funcion in RUTAS + INTERNAS:
        op.execute(f"drop function {funcion}")
    op.execute(f"drop table {TABLA_CONSTANCIA}")
