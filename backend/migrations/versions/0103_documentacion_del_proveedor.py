"""Seguros y habilitaciones con vigencia en la ficha del proveedor (RF50; documento 14, §5.2,
decisión 6 y fila de la verificación: «documento vencido marca y no excluye»). Diseño:
`doc/diseno/RF50_Seguros_y_Habilitaciones.md` del repositorio CEM.

**La constancia** (`nucleo.documentacion_proveedor`). Una fila por archivo subido: el proveedor, el
tipo (`seguro` o `habilitacion`), qué cubre en palabras (sin teléfonos, correos ni cuentas:
`nucleo.sin_contacto_libre`), el último día en que vale (`vence_el`), el documento y la versión
exacta del archivo, quién (persona e identidad humana) y cuándo, y la constancia que reemplaza.
No se cambia ni se borra (`negar_cambio_historia_orden_pago`), como la factura adjunta de
`0099`. Cita su documento (`documento_citado_vigente` y `documento_referenciado`): ya no se
quita ni se retira.

**El acto** (`nucleo.cargar_documentacion_proveedor`, `security definer`, solo `cem_app`). Lo hace
quien da de alta y cambia proveedores: `egresos.preparar_orden` con R10 sin el gesto
(`exigir_acto_sin_gesto`: no mueve dinero) y la carga de «Contratos con proveedores»
(`exigir_carga_documentos`), la categoría restringida: la ve solo quien tiene la facultad
`documentos.ver`, nunca un propietario por su vínculo (decisión 9 del 22-10-2026). En una sola
transacción, con el proveedor bloqueado:

- el proveedor existe en el consorcio (`proveedor_inexistente`); suspendido o dado de baja también
  acepta: es historia y no habilita órdenes;
- para reemplazar, `reemplaza` es una constancia de ese proveedor, del mismo tipo, que vale hoy
  (`documentacion_no_vigente`), y no tiene otro reemplazo en el antivirus
  (`documentacion_en_revision`);
- crea el documento (fecha: el día del consorcio), su versión con `nucleo.nueva_version`
  (cuarentena; la ruta encola el antivirus en la misma transacción), la constancia y el evento
  `consorcio.proveedor.cargar_documentacion`, sin la descripción (texto libre).

No toca `nucleo.proveedor` ni `nucleo.proveedor_version`: no hay versión nueva del proveedor ni se
vuelve a pedir aprobación por `cuenta_cambiada`.

**El estado** (`nucleo.estado_documentacion_proveedor`, con el día del consorcio): `en_revision`
(el archivo está en el antivirus), `rechazada` (no lo pasó: no cuenta), `sin_documento` (soporte
borró el documento), `reemplazada` (otra la reemplaza y su archivo pasó el antivirus), `vencida`
(`vence_el` antes de hoy), `por_vencer` (vence dentro de `DIAS_POR_VENCER` días, hoy incluido) o
`vigente`. Las tres últimas «valen». Mientras el reemplazo está en el antivirus, o si lo rechaza,
vale la anterior. Un proveedor con alguna que vale vencida queda marcado; **no se excluye**: nada de
esto se consulta al preparar, aprobar ni pagar una orden.

**Quién la ve** (`nucleo.documentacion_proveedor_lectura`): las lecturas de egresos
(`exigir_lectura_egresos`: quien prepara o quien revisa), con el estado, el nombre de quien la
cargó y si su documento sigue vigente. Con `proveedor` nulo, la de todos los proveedores del
consorcio (la lista marca a los vencidos).

**Exportación (`0069`):** `exportar_proveedores` suma la entidad `documentacion_proveedor`, con el
nombre de quien la cargó y no su identificador.

Downgrade: se niega (`downgrade_con_datos`) si hay alguna constancia. Sin eso, vuelve a las
definiciones anteriores.

Revisión: 0103
Anterior: 0102
"""

from alembic import op
from sqlalchemy import text

revision = "0103"
down_revision = "0102"
branch_labels = None
depends_on = None

CATEGORIA = "contratos_proveedores"
"""La de `CATEGORIA_DOCUMENTACION_PROVEEDOR` del contrato: restringida
(`CATEGORIAS_RESTRINGIDAS`)."""
TIPOS = ("seguro", "habilitacion")
"""Los de `TipoDocumentacionProveedor` del contrato."""
LARGO_DESCRIPCION = 120
"""El de `LARGO_DESCRIPCION_DOCUMENTACION` del contrato."""
DIAS_POR_VENCER = 30
"""El de `DIAS_POR_VENCER` del contrato (pregunta P3 del diseño, por defecto)."""
ACCION_AUDITADA = "consorcio.proveedor.cargar_documentacion"

TABLA_CONSTANCIA = "nucleo.documentacion_proveedor"
REFERENCIAS_NUEVAS = ((TABLA_CONSTANCIA, "documento_id"),)
"""Lo que esta migración suma a `REFERENCIAS` de `0089`: tablas que citan un documento."""

CARGAR = (
    "nucleo.cargar_documentacion_proveedor(uuid, text, text, date, uuid, text, text, integer, text,"
    " uuid, text, uuid)"
)
LEER = "nucleo.documentacion_proveedor_lectura(uuid, text, uuid, text, uuid)"
ESTADO = "nucleo.estado_documentacion_proveedor(uuid, uuid)"
EN_REVISION = "nucleo.reemplazo_documentacion_en_revision(uuid, uuid)"
REFERENCIADO = "nucleo.documento_referenciado(uuid, uuid)"
EXPORTAR_PROVEEDORES = "nucleo.exportar_proveedores(uuid, uuid)"

RUTAS = (CARGAR, LEER)
INTERNAS = (ESTADO, EN_REVISION)

_tipos = ", ".join(f"'{t}'" for t in TIPOS)

TABLA = f"""
create table {TABLA_CONSTANCIA} (
  consorcio_id          uuid not null,
  id                    uuid primary key default plataforma.uuid_v7(),
  proveedor_id          uuid not null,
  tipo                  text not null check (tipo in ({_tipos})),
  descripcion           text not null
    check (length(btrim(descripcion)) between 1 and {LARGO_DESCRIPCION}
           and descripcion = btrim(descripcion) and nucleo.sin_contacto_libre(descripcion)),
  vence_el              date not null,
  documento_id          uuid not null,
  version_documento_id  uuid not null unique references nucleo.version_documento (id),
  cargada_por           uuid not null references nucleo.persona (id),
  identidad_carga       uuid not null,
  cargada_en            timestamptz not null default now(),
  -- La constancia que esta reemplaza, del mismo proveedor; nula en la primera.
  reemplaza_a           uuid,
  unique (proveedor_id, id),
  foreign key (proveedor_id, reemplaza_a) references {TABLA_CONSTANCIA} (proveedor_id, id),
  foreign key (consorcio_id, proveedor_id) references nucleo.proveedor (consorcio_id, id),
  foreign key (consorcio_id, documento_id) references nucleo.documento (consorcio_id, id),
  foreign key (consorcio_id, identidad_carga)
    references nucleo.identidad_humana (consorcio_id, id)
);
create index documentacion_proveedor_proveedor
  on {TABLA_CONSTANCIA} (consorcio_id, proveedor_id);
create index documentacion_proveedor_documento
  on {TABLA_CONSTANCIA} (consorcio_id, documento_id);
create index documentacion_proveedor_reemplaza
  on {TABLA_CONSTANCIA} (proveedor_id, reemplaza_a) where reemplaza_a is not null;
alter table {TABLA_CONSTANCIA} enable row level security;
alter table {TABLA_CONSTANCIA} force row level security;
create policy por_consorcio on {TABLA_CONSTANCIA} for select
  using (consorcio_id = plataforma.consorcio_actual());
revoke all on {TABLA_CONSTANCIA} from public, cem_app;

create trigger historia_documentacion_proveedor before update or delete on {TABLA_CONSTANCIA}
  for each row execute function nucleo.negar_cambio_historia_orden_pago();
create trigger historia_documentacion_proveedor_truncate before truncate on {TABLA_CONSTANCIA}
  for each statement execute function nucleo.negar_cambio_historia_orden_pago();
create trigger documento_citado_vigente before insert or update of documento_id
  on {TABLA_CONSTANCIA}
  for each row execute function nucleo.documento_citado_vigente('documento_id');
"""

FUNCIONES = f"""
-- El estado de una constancia hoy, en el día del consorcio. Nulo si no existe en el consorcio.
create function nucleo.estado_documentacion_proveedor(c uuid, constancia uuid)
  returns text
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select case
             when x.estado = 'en_cuarentena' then 'en_revision'
             when x.estado = 'rechazado' then 'rechazada'
             when y.estado <> 'vigente' then 'sin_documento'
             when exists (
               select 1 from nucleo.documentacion_proveedor r
                 join nucleo.version_documento rx on rx.id = r.version_documento_id
                 join nucleo.documento ry
                   on ry.consorcio_id = r.consorcio_id and ry.id = r.documento_id
                where r.proveedor_id = a.proveedor_id and r.reemplaza_a = a.id
                  and rx.estado = 'disponible' and ry.estado = 'vigente')
               then 'reemplazada'
             when a.vence_el < nucleo.hoy_del_consorcio(c) then 'vencida'
             when a.vence_el <= nucleo.hoy_del_consorcio(c) + {DIAS_POR_VENCER} then 'por_vencer'
             else 'vigente'
           end
      from nucleo.documentacion_proveedor a
      join nucleo.version_documento x on x.id = a.version_documento_id
      join nucleo.documento y on y.consorcio_id = a.consorcio_id and y.id = a.documento_id
     where a.consorcio_id = c and a.id = constancia
  $$;

-- El reemplazo de esa constancia cuyo archivo está en el antivirus, de un documento vigente; nulo
-- si no hay. A lo sumo uno: el acto no deja subir otro mientras tanto.
create function nucleo.reemplazo_documentacion_en_revision(c uuid, constancia uuid)
  returns uuid
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select r.id
      from nucleo.documentacion_proveedor r
      join nucleo.version_documento x on x.id = r.version_documento_id
      join nucleo.documento d on d.consorcio_id = r.consorcio_id and d.id = r.documento_id
     where r.consorcio_id = c and r.reemplaza_a = constancia
       and x.estado = 'en_cuarentena' and d.estado = 'vigente'
     order by r.cargada_en desc, r.id desc
     limit 1
  $$;

create function nucleo.cargar_documentacion_proveedor(proveedor uuid, tipo_nuevo text,
                                                      descripcion_nueva text, vence date,
                                                      reemplaza uuid, titulo_nuevo text,
                                                      tipo_archivo text, tamano_nuevo integer,
                                                      sha256_nuevo text, actor uuid,
                                                      origen_pedido text, sesion uuid)
  returns table (nueva_version_id uuid, nueva_clave_objeto text, nuevo_documento_id uuid,
                 nueva_constancia_id uuid)
  language plpgsql
  security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    identidad uuid;
    anterior nucleo.documentacion_proveedor;
    d nucleo.documento;
    archivo record;
    constancia uuid;
  begin
    -- Lo mismo que dar de alta o cambiar un proveedor, sin el gesto (no mueve dinero), y la carga
    -- de la categoría.
    identidad := nucleo.exigir_acto_sin_gesto(c, actor, sesion, 'egresos.preparar_orden',
                                              origen_pedido);
    perform nucleo.exigir_carga_documentos(c, actor, origen_pedido, '{CATEGORIA}');
    -- Bloqueado: dos reemplazos simultáneos de la misma constancia se ven uno al otro.
    perform 1 from nucleo.proveedor p where p.consorcio_id = c and p.id = proveedor for update;
    if not found then
      raise exception 'el proveedor no existe en este consorcio' using errcode = 'P0001',
        hint = 'proveedor_inexistente';
    end if;
    if reemplaza is not null then
      select * into anterior from nucleo.documentacion_proveedor a
       where a.consorcio_id = c and a.proveedor_id = proveedor and a.id = reemplaza;
      if anterior.id is null or anterior.tipo is distinct from tipo_nuevo
         or nucleo.estado_documentacion_proveedor(c, reemplaza)
              not in ('vigente', 'por_vencer', 'vencida') then
        raise exception 'la constancia a reemplazar no es una que vale de ese tipo'
          using errcode = 'P0001', hint = 'documentacion_no_vigente';
      end if;
      if nucleo.reemplazo_documentacion_en_revision(c, reemplaza) is not null then
        raise exception 'hay otro reemplazo en el antivirus' using errcode = 'P0001',
          hint = 'documentacion_en_revision';
      end if;
    end if;
    insert into nucleo.documento (consorcio_id, categoria, titulo, fecha, cargado_por)
    values (c, '{CATEGORIA}', btrim(titulo_nuevo), nucleo.hoy_del_consorcio(c), actor)
    returning * into d;
    -- El título no va a la auditoría: es texto libre.
    perform nucleo.auditar_documento(c, 'consorcio.documento.crear', 'documento', d.id, actor,
      origen_pedido, jsonb_build_object('categoria', d.categoria, 'fecha', d.fecha));
    select * into archivo
      from nucleo.nueva_version(d.id, tipo_archivo, tamano_nuevo, sha256_nuevo, actor,
                                origen_pedido);
    insert into nucleo.documentacion_proveedor
      (consorcio_id, proveedor_id, tipo, descripcion, vence_el, documento_id,
       version_documento_id, cargada_por, identidad_carga, reemplaza_a)
    values (c, proveedor, tipo_nuevo, btrim(descripcion_nueva), vence, d.id, archivo.id, actor,
            identidad, reemplaza)
    returning id into constancia;
    -- La descripción no va a la auditoría: es texto libre.
    perform nucleo.auditar_proveedor(c, '{ACCION_AUDITADA}', proveedor, actor,
      case when reemplaza is null then null
           else jsonb_build_object('constancia_id', anterior.id, 'vence_el', anterior.vence_el,
                                   'documento_id', anterior.documento_id) end,
      jsonb_build_object('constancia_id', constancia, 'tipo', tipo_nuevo, 'vence_el', vence,
                         'documento_id', d.id, 'version_documento_id', archivo.id));
    return query select archivo.id, archivo.clave_objeto, d.id, constancia;
  end
  $$;

create function nucleo.documentacion_proveedor_lectura(proveedor uuid, facultad text, actor uuid,
                                                       origen_pedido text, sesion uuid)
  returns table (id uuid, proveedor_id uuid, tipo text, descripcion text, vence_el date,
                 documento_id uuid, version_documento_id uuid, estado text,
                 cargada_por_nombre text, cargada_en timestamptz, reemplaza_a uuid,
                 documento_vigente boolean)
  language plpgsql
  security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
  begin
    perform nucleo.exigir_lectura_egresos(c, facultad, actor, origen_pedido, sesion);
    return query
      select a.id, a.proveedor_id, a.tipo, a.descripcion, a.vence_el, a.documento_id,
             a.version_documento_id, nucleo.estado_documentacion_proveedor(c, a.id), p.nombre,
             a.cargada_en, a.reemplaza_a, y.estado = 'vigente'
        from nucleo.documentacion_proveedor a
        join nucleo.documento y on y.consorcio_id = a.consorcio_id and y.id = a.documento_id
        join nucleo.persona p on p.id = a.cargada_por
       where a.consorcio_id = c and (proveedor is null or a.proveedor_id = proveedor)
       order by a.proveedor_id, a.cargada_en, a.id;
  end
  $$;
"""

REFERENCIADO_CAMBIOS = (
    (
        "        or exists (select 1 from nucleo.factura_adjunta_orden_pago x\n",
        f"        or exists (select 1 from {TABLA_CONSTANCIA} x\n"
        "                    where x.consorcio_id = c and x.documento_id = documento)\n"
        "        or exists (select 1 from nucleo.factura_adjunta_orden_pago x\n",
    ),
)
EXPORTAR_CAMBIOS = (
    (
        "       order by b.contrato_id;\n",
        "       order by b.contrato_id;\n"
        "    -- 0103: los seguros y las habilitaciones, con el nombre de quien los cargó.\n"
        "    return query\n"
        "      select 'documentacion_proveedor'::text,\n"
        "             jsonb_build_object('id', a.id, 'proveedor_id', a.proveedor_id,\n"
        "                                'tipo', a.tipo, 'descripcion', a.descripcion,\n"
        "                                'vence_el', a.vence_el, 'documento_id', a.documento_id,\n"
        "                                'version_documento_id', a.version_documento_id,\n"
        "                                'cargada_por_nombre',"
        " nucleo.nombre_para_exportar(c, a.cargada_por, hoy),\n"
        "                                'cargada_en', a.cargada_en,\n"
        "                                'reemplaza_a', a.reemplaza_a)\n"
        f"        from {TABLA_CONSTANCIA} a\n"
        "       where a.consorcio_id = c\n"
        "       order by a.cargada_en, a.id;\n",
    ),
)

EN_SU_LUGAR = (
    (REFERENCIADO, REFERENCIADO_CAMBIOS),
    (EXPORTAR_PROVEEDORES, EXPORTAR_CAMBIOS),
)
"""Misma firma y lo mismo que devuelven: `create or replace` con la definición cambiada."""

GUARDA_DOWNGRADE = f"""
-- Hasta el fin del downgrade nadie carga: la guarda vale para lo que se borra.
lock table {TABLA_CONSTANCIA} in access exclusive mode;
do $$
begin
  if exists (select 1 from {TABLA_CONSTANCIA}) then
    raise exception '0103 no baja con documentación de proveedores: se perdería la constancia'
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
            raise RuntimeError(f"0103: {funcion} no tiene exactamente una vez {antes!r}")
        definicion = definicion.replace(antes, despues)
    return definicion


def upgrade() -> None:
    op.execute(TABLA)
    op.execute(FUNCIONES)
    for funcion in RUTAS + INTERNAS:
        op.execute(f"revoke all on function {funcion} from public")
    for funcion in RUTAS:
        op.execute(f"grant execute on function {funcion} to cem_app")
    for funcion, cambios in EN_SU_LUGAR:
        op.execute(_definicion(funcion, cambios, al_reves=False))


def downgrade() -> None:
    op.execute(GUARDA_DOWNGRADE)
    for funcion, cambios in reversed(EN_SU_LUGAR):
        op.execute(_definicion(funcion, cambios, al_reves=True))
    # La lectura llama a `estado_…`: se borran las rutas primero.
    for funcion in RUTAS + INTERNAS:
        op.execute(f"drop function {funcion}")
    op.execute(f"drop table {TABLA_CONSTANCIA}")
