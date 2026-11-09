"""Quitar un archivo subido por error y retirar un documento (pedido del propietario del 08-11-2026:
«hacé 1 y 2»). Nada se borra: los dos son un cambio de estado del documento, con su constancia.

**Quitar** (`nucleo.quitar_documento`, estado `descartado`). Un formulario sube el archivo al
elegirlo (`0088`: la factura de una orden, el documento de un proveedor). Si era el equivocado,
quien lo subió lo quita antes de guardar, y el documento deja de aparecer. Solo:

- un documento **para carga** (`lectura_carga`), vigente;
- **quien lo creó y subió todas sus versiones** (`no_es_quien_lo_subio`), con lo mismo que pide
  crearlo (`egresos.preparar_orden` sin gesto y la carga de «Facturas y comprobantes»);
- **sin nada que lo cite** (`documento_referenciado`, ver abajo).

**Retirar** (`nucleo.retirar_documento`, estado `retirado`). Un documento ya guardado que dejó de
valer (un seguro vencido, un presupuesto reemplazado) sale de las listas con un motivo obligatorio
(1 a 300 caracteres, sin teléfonos, correos ni cuentas). Lo retira quien **carga esa categoría y
además tiene la facultad `documentos.ver`** (la de `documentos.ver_restringidos`): la administración
(con el paquete de siempre, `documentos.cargar` y `documentos.ver`). El administrador externo solo
carga (documento 08 §4) y el Consejo no carga las categorías comunes: ninguno retira lo que no
administra. Se niega si algo lo cita (`documento_referenciado`).

**Qué cita un documento** (`nucleo.documento_referenciado`): ser el comprobante de una orden
(`orden_comprobante`), o estar en una versión de una orden (la factura, con su versión y su
`sha256` en la huella), de un proveedor o de un contrato, en un pago manual, su comprobante o una
diferencia, en la lectura de un comprobante, o que el `sha256` de alguna de sus versiones sea el del
acta de una decisión formal. La lectura con IA de un documento para carga (`nucleo.lectura_carga`)
no lo cita: es lo que el formulario mostró, no lo que se guardó. Una prueba compara esta lista con
las claves foráneas del catálogo: una tabla nueva que cite documentos no pasa sin entrar acá.

**Sin carreras.** Quitar y retirar bloquean la fila del documento (`for update`) antes de mirar las
citas. Toda tabla que cita un documento lo bloquea `for share` al insertar
(`nucleo.documento_citado_vigente`) y se niega con `documento_invalido` si ya está descartado o
retirado: o la cita espera y ve el estado nuevo, o quitar espera y ve la cita. El acta de una
decisión no es una clave foránea sino un `sha256`: al registrarlo (`nucleo.decision_formal`, al
insertar o al cambiar `sha256_acta`), `nucleo.acta_de_documento_vigente` bloquea `for share` los
documentos del consorcio con una versión de ese `sha256` y se niega (`acta_dada_de_baja`) si alguno
está descartado o retirado. Un acta que no está entre los documentos se registra como siempre.
Una versión nueva (`nucleo.version_documento`, por cualquier camino: la carga de Documentos, la del
formulario o la del comprobante de `0068`) bloquea su documento `for share` y vuelve a mirar que
siga vigente (`nucleo.version_de_documento_vigente`, `no_encontrado`): un archivo que llega mientras
se quita o se retira espera y se niega, o quitar espera y ve la versión ajena.

**Los archivos quedan.** Las versiones no cambian (su estado sigue siendo el del antivirus) y los
objetos del almacén no se borran: el borrado de un rechazado (`documentos.borrar_rechazado`) existe
porque el archivo puede ser dañino, y acá el archivo pasó el antivirus y puede ser evidencia. Las
listas y la descarga ya filtran `estado = 'vigente'` (`0036`), así que un documento descartado o
retirado no aparece ni se baja por la API. Un documento descartado o retirado no cambia más
(`documento_dado_de_baja_no_cambia`), tampoco por soporte.

**Auditoría.** `consorcio.documento.quitar` y `consorcio.documento.retirar`, con la categoría. El
motivo no va: es texto libre (como el título); queda en la fila y en la exportación.

**Exportación (`0038`).** `exportar_documentos` entrega también los retirados, con su `estado`,
`retirado_en` y `retirado_motivo` (sin quién lo retiró: es el identificador de una persona), y sus
versiones `disponible`; `exportar_objetos_documentos`, sus binarios. Los descartados no van, como
los borrados por soporte: eran un archivo equivocado que nunca se usó.

Downgrade: se niega (`downgrade_con_datos`) si hay algún documento descartado o retirado.

Revisión: 0089
Anterior: 0088
"""

from alembic import op
from sqlalchemy import text

revision = "0089"
down_revision = "0088"
branch_labels = None
depends_on = None

CATEGORIA_DE_CARGA = "facturas_y_comprobantes"
"""La de `CATEGORIA_DE_CARGA` del contrato: los documentos para carga nacen ahí (`0088`)."""
LARGO_MOTIVO = 300
"""El de `LARGO_MAXIMO_MOTIVO_RETIRO` del contrato."""

REFERENCIAS = (
    ("nucleo.orden_pago_version", "documento_id"),
    ("nucleo.proveedor_version", "documento_id"),
    ("nucleo.contrato_proveedor", "documento_id"),
    ("nucleo.comprobante_pago_manual", "documento_id"),
    ("nucleo.pago_manual", "documento_id"),
    ("nucleo.diferencia_pago_manual", "documento_id"),
    ("nucleo.lectura_comprobante", "documento_id"),
)
"""Las tablas que citan un documento, con la columna. Todas tienen `consorcio_id`."""
NO_CITAN = ("nucleo.version_documento", "nucleo.lectura_carga")
"""Las otras tablas con una clave foránea a documentos: las versiones del propio documento y la
lectura con IA de un documento para carga (no es lo que se guardó)."""

REFERENCIADO = "nucleo.documento_referenciado(uuid, uuid)"
CITADO = "nucleo.documento_citado_vigente()"
ACTA = "nucleo.acta_de_documento_vigente()"
VERSION_VIGENTE = "nucleo.version_de_documento_vigente()"
NO_CAMBIA = "nucleo.documento_dado_de_baja_no_cambia()"
QUITAR = "nucleo.quitar_documento(uuid, uuid, text, uuid)"
RETIRAR = "nucleo.retirar_documento(uuid, text, uuid, text)"
EXPORTAR_DOCUMENTOS = "nucleo.exportar_documentos(uuid, uuid)"
EXPORTAR_OBJETOS = "nucleo.exportar_objetos_documentos(uuid, uuid)"

PROYECCION = (
    ("consorcio.documento.quitar", "Quitó un archivo que había subido por error"),
    ("consorcio.documento.retirar", "Retiró un documento"),
)

CITAS = "\n".join(
    f"        or exists (select 1 from {tabla} x\n"
    f"                    where x.consorcio_id = c and x.{columna} = documento)"
    for tabla, columna in REFERENCIAS
)
DISPARADORES = "".join(
    f"create trigger documento_citado_vigente before insert or update of {columna} on {tabla}\n"
    f"  for each row execute function {CITADO.removesuffix('()')}('{columna}');\n"
    for tabla, columna in REFERENCIAS
)

TABLA = f"""
insert into auditoria.proyeccion (accion, texto, recurso, campos) values
  {", ".join(f"('{a}', '{t}', 'Documento', array['categoria'])" for a, t in PROYECCION)};

alter table nucleo.documento
  drop constraint documento_estado_check,
  add constraint documento_estado_check
    check (estado in ('vigente', 'borrado', 'descartado', 'retirado')),
  add column descartado_en timestamptz,
  add column retirado_en timestamptz,
  add column retirado_por uuid references nucleo.persona (id),
  add column retirado_motivo text
    check (length(btrim(retirado_motivo)) between 1 and {LARGO_MOTIVO}
           and nucleo.sin_contacto_libre(retirado_motivo)),
  add constraint documento_descartado_con_fecha
    check ((estado = 'descartado') = (descartado_en is not null)),
  add constraint documento_retirado_con_constancia
    check ((estado = 'retirado') = (retirado_en is not null)
           and (retirado_en is null) = (retirado_por is null)
           and (retirado_en is null) = (retirado_motivo is null));

create trigger retirado_motivo_sin_cuenta before update of retirado_motivo on nucleo.documento
  for each row execute function nucleo.negar_cuenta_en_motivo('retirado_motivo');

-- Un documento descartado o retirado no se cambia más: ni vuelve a vigente, ni se borra, ni cambia
-- de categoría (el borrado de soporte tiene su propio disparador, `0037`).
create function {NO_CAMBIA.removesuffix("()")}()
  returns trigger
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  begin
    if old.estado in ('descartado', 'retirado') then
      raise exception 'el documento fue quitado o retirado: no se cambia'
        using errcode = 'P0001', hint = 'documento_dado_de_baja';
    end if;
    return new;
  end
  $$;
revoke all on function {NO_CAMBIA} from public;
create trigger documento_dado_de_baja_no_cambia before update on nucleo.documento
  for each row execute function {NO_CAMBIA};
"""

FUNCIONES = f"""
-- Si algo con dinero o con historia cita el documento.
create function nucleo.documento_referenciado(c uuid, documento uuid)
  returns boolean
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select exists (select 1 from nucleo.documento d
                    where d.consorcio_id = c and d.id = documento
                      and d.orden_comprobante is not null)
{CITAS}
        or exists (select 1 from nucleo.version_documento v
                     join nucleo.decision_formal f
                       on f.consorcio_id = v.consorcio_id and f.sha256_acta = v.sha256
                    where v.consorcio_id = c and v.documento_id = documento)
  $$;

-- Quien cita un documento lo bloquea hasta terminar: quitar o retirar esperan, o esto espera y ve
-- que ya no está vigente.
create function {CITADO.removesuffix("()")}()
  returns trigger
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    doc uuid := (to_jsonb(new) ->> tg_argv[0])::uuid;
    estado_doc text;
  begin
    if doc is null then
      return new;
    end if;
    select d.estado into estado_doc from nucleo.documento d
     where d.consorcio_id = new.consorcio_id and d.id = doc
       for share;
    if estado_doc in ('descartado', 'retirado') then
      raise exception 'el documento fue quitado o retirado: no respalda nada'
        using errcode = 'P0001', hint = 'documento_invalido';
    end if;
    return new;
  end
  $$;
{DISPARADORES}
-- El acta de una decisión cita un documento por el `sha256` de una versión: se bloquean esos
-- documentos como en `documento_citado_vigente`, y retirar (que los bloquea `for update` y después
-- mira las decisiones) espera o es esperado.
create function {ACTA.removesuffix("()")}()
  returns trigger
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  begin
    if new.sha256_acta is null then
      return new;
    end if;
    perform 1 from nucleo.documento d
     where d.consorcio_id = new.consorcio_id
       and d.id in (select v.documento_id from nucleo.version_documento v
                     where v.consorcio_id = new.consorcio_id and v.sha256 = new.sha256_acta)
       for share;
    -- Otra sentencia, otra foto: después de esperar, ve el estado que dejó quien retiró.
    if exists (select 1 from nucleo.documento d
                 join nucleo.version_documento v
                   on v.consorcio_id = d.consorcio_id and v.documento_id = d.id
                where d.consorcio_id = new.consorcio_id and v.sha256 = new.sha256_acta
                  and d.estado in ('descartado', 'retirado')) then
      raise exception 'el acta es un documento quitado o retirado: no respalda una decisión'
        using errcode = 'P0001', hint = 'acta_dada_de_baja';
    end if;
    return new;
  end
  $$;
create trigger acta_de_documento_vigente before insert or update of sha256_acta
  on nucleo.decision_formal
  for each row execute function {ACTA};

-- Una versión nueva bloquea su documento y, en otra sentencia (otra foto, después de esperar a
-- quien quita o retira), vuelve a mirar que siga vigente. Las funciones que crean versiones ya lo
-- miraban, pero sin bloquear.
create function {VERSION_VIGENTE.removesuffix("()")}()
  returns trigger
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  begin
    perform 1 from nucleo.documento d
     where d.consorcio_id = new.consorcio_id and d.id = new.documento_id
       for share;
    if not exists (select 1 from nucleo.documento d
                    where d.consorcio_id = new.consorcio_id and d.id = new.documento_id
                      and d.estado = 'vigente') then
      raise exception 'el documento no está vigente: no recibe archivos'
        using errcode = 'P0001', hint = 'no_encontrado';
    end if;
    return new;
  end
  $$;
create trigger version_de_documento_vigente before insert on nucleo.version_documento
  for each row execute function {VERSION_VIGENTE};
create function nucleo.quitar_documento(documento uuid, actor uuid, origen_pedido text,
                                        sesion uuid)
  returns void
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    d nucleo.documento;
  begin
    -- Lo mismo que pide crearlo (`crear_documento_para_carga`, `0088`).
    perform nucleo.exigir_acto_sin_gesto(c, actor, sesion, 'egresos.preparar_orden',
                                         origen_pedido);
    perform nucleo.exigir_carga_documentos(c, actor, origen_pedido, '{CATEGORIA_DE_CARGA}');
    select * into d from nucleo.documento x
     where x.consorcio_id = c and x.id = documento and x.estado = 'vigente'
       and x.lectura_carga is not null
       for update;
    if d.id is null then
      raise exception 'el documento no existe' using errcode = 'P0001', hint = 'no_encontrado';
    end if;
    if d.cargado_por is distinct from actor
       or exists (select 1 from nucleo.version_documento v
                   where v.consorcio_id = c and v.documento_id = documento
                     and v.subida_por is distinct from actor) then
      raise exception 'solo quien subió el archivo lo quita'
        using errcode = 'P0001', hint = 'no_es_quien_lo_subio';
    end if;
    if nucleo.documento_referenciado(c, documento) then
      raise exception 'el documento respalda algo guardado: no se quita'
        using errcode = 'P0001', hint = 'documento_referenciado';
    end if;
    update nucleo.documento x set estado = 'descartado', descartado_en = now()
     where x.consorcio_id = c and x.id = documento;
    perform nucleo.auditar_documento(c, 'consorcio.documento.quitar', 'documento', documento,
      actor, origen_pedido, jsonb_build_object('categoria', d.categoria));
  end
  $$;

-- La carga de la categoría y la facultad `documentos.ver`: quien administra los documentos.
create function nucleo.retirar_documento(documento uuid, motivo text, actor uuid,
                                         origen_pedido text)
  returns void
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    d nucleo.documento;
  begin
    -- Sin ninguna carga ni la facultad de ver, no se dice si el documento existe.
    perform nucleo.exigir_carga_documentos(c, actor, origen_pedido, null);
    if not nucleo.tiene_facultad_vigente(c, actor, 'documentos.ver',
                                         nucleo.hoy_del_consorcio(c)) then
      raise exception 'sin documentos.ver en este consorcio' using errcode = '42501';
    end if;
    select * into d from nucleo.documento x
     where x.consorcio_id = c and x.id = documento and x.estado = 'vigente'
       for update;
    if d.id is null then
      raise exception 'el documento no existe' using errcode = 'P0001', hint = 'no_encontrado';
    end if;
    perform nucleo.exigir_carga_documentos(c, actor, origen_pedido, d.categoria);
    if nucleo.documento_referenciado(c, documento) then
      raise exception 'el documento respalda algo guardado: no se retira'
        using errcode = 'P0001', hint = 'documento_referenciado';
    end if;
    update nucleo.documento x
       set estado = 'retirado', retirado_en = now(), retirado_por = actor,
           retirado_motivo = btrim(motivo)
     where x.consorcio_id = c and x.id = documento;
    -- El motivo no va a la auditoría: es texto libre.
    perform nucleo.auditar_documento(c, 'consorcio.documento.retirar', 'documento', documento,
      actor, origen_pedido, jsonb_build_object('categoria', d.categoria));
  end
  $$;
"""

INTERNAS = (REFERENCIADO, CITADO, ACTA, VERSION_VIGENTE)
RUTAS = (QUITAR, RETIRAR)

EXPORTAR_DOCUMENTOS_CAMBIOS = (
    (
        "                                'lectura_carga', d.lectura_carga)\n",
        "                                'lectura_carga', d.lectura_carga,\n"
        "                                'estado', d.estado, 'retirado_en', d.retirado_en,\n"
        "                                'retirado_motivo', d.retirado_motivo)\n",
    ),
    (
        "       where d.consorcio_id = c and d.estado = 'vigente'\n",
        "       where d.consorcio_id = c and d.estado in ('vigente', 'retirado')\n",
    ),
    (
        "v.estado = 'disponible' and d.estado = 'vigente'\n",
        "v.estado = 'disponible'\n         and d.estado in ('vigente', 'retirado')\n",
    ),
)
EXPORTAR_OBJETOS_CAMBIOS = (
    (
        "v.estado = 'disponible' and d.estado = 'vigente'",
        "v.estado = 'disponible'\n         and d.estado in ('vigente', 'retirado')",
    ),
)
EN_SU_LUGAR = (
    (EXPORTAR_DOCUMENTOS, EXPORTAR_DOCUMENTOS_CAMBIOS),
    (EXPORTAR_OBJETOS, EXPORTAR_OBJETOS_CAMBIOS),
)

GUARDA_DOWNGRADE = """
lock table nucleo.documento in access exclusive mode;
do $$
begin
  if exists (select 1 from nucleo.documento where estado in ('descartado', 'retirado')) then
    raise exception '0089 no baja con documentos quitados o retirados: se perdería la constancia'
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
            raise RuntimeError(f"0089: {funcion} no tiene exactamente una vez {antes!r}")
        definicion = definicion.replace(antes, despues)
    return definicion


def upgrade() -> None:
    op.execute(TABLA)
    op.execute(FUNCIONES)
    for funcion in INTERNAS + RUTAS:
        op.execute(f"revoke all on function {funcion} from public")
    for funcion in RUTAS:
        op.execute(f"grant execute on function {funcion} to cem_app")
    for funcion, cambios in EN_SU_LUGAR:
        op.execute(_definicion(funcion, cambios, al_reves=False))


def downgrade() -> None:
    op.execute(GUARDA_DOWNGRADE)
    for funcion, cambios in reversed(EN_SU_LUGAR):
        op.execute(_definicion(funcion, cambios, al_reves=True))
    for funcion in RUTAS:
        op.execute(f"drop function {funcion}")
    for tabla, _ in REFERENCIAS:
        op.execute(f"drop trigger documento_citado_vigente on {tabla}")
    op.execute("drop trigger acta_de_documento_vigente on nucleo.decision_formal")
    op.execute("drop trigger version_de_documento_vigente on nucleo.version_documento")
    for funcion in INTERNAS:
        op.execute(f"drop function {funcion}")
    op.execute("drop trigger documento_dado_de_baja_no_cambia on nucleo.documento")
    op.execute(f"drop function {NO_CAMBIA}")
    op.execute("drop trigger retirado_motivo_sin_cuenta on nucleo.documento")
    op.execute(
        "alter table nucleo.documento"
        " drop constraint documento_retirado_con_constancia,"
        " drop constraint documento_descartado_con_fecha,"
        " drop column retirado_motivo, drop column retirado_por, drop column retirado_en,"
        " drop column descartado_en,"
        " drop constraint documento_estado_check,"
        " add constraint documento_estado_check check (estado in ('vigente', 'borrado'))"
    )
    acciones = ", ".join(f"'{a}'" for a, _ in PROYECCION)
    op.execute(f"delete from auditoria.proyeccion where accion in ({acciones})")
