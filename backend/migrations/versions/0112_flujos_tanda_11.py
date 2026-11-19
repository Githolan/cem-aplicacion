"""Tanda 11 de la revisión por flujos (19-11-2026): dos cosas que habían quedado afuera.

**Abrir una invitación cuyo vínculo ya no corresponde (quedaba de C8G-11).**
`nucleo.paso_invitacion` (`0109`) dice lo que pasaría al aceptar con la sesión del pedido, en el
orden de `aceptar_invitacion`. Le faltaban los dos `vinculo_no_corresponde` de `0025`: la
invitación que creó la importación del padrón nombra su vínculo, y si su persona ya no existe, o
ese vínculo fue anulado, terminó o ya no es de esa persona, unidad o carácter, la aceptación se
niega. La página ofrecía «Aceptar» y recién el 409 decía que no se podía (criterio C9: un acto que
se sabe imposible solo muestra el aviso). Ahora el paso es `vinculo_no_corresponde` en los mismos
dos lugares que `aceptar_invitacion`:

- sin persona del correo, antes de mirar la sesión, como en `0025` (lo dice a quien tiene el
  enlace, igual que el 409 de aceptar);
- el vínculo nombrado no es el de la persona, después de `propia`, con la misma condición que
  `aceptar_invitacion` (el vínculo exacto, de esa persona, unidad y carácter, que no terminó hoy en
  la zona horaria del consorcio). Solo sale con la sesión de la cuenta del correo.

**Otro comprobante del envío si el antivirus rechazó el registrado (pregunta 17, FL-SOL-04).** El
propietario decidió (18-11-2026) que se deje subir otro sin tocar la fecha. El envío es una
constancia de una sola fila que no cambia (`0108`), así que el reemplazo es un hecho nuevo:

- `nucleo.solicitud_formal_envio_comprobante`: una fila por reemplazo, con el comprobante anterior
  (cada uno se reemplaza una sola vez), el nuevo, quién y cuándo. Las mismas protecciones que las
  tablas de `0108`: RLS forzada por consorcio, sin acceso directo de `cem_app`, sin cambios ni
  borrado.
- `nucleo.comprobante_envio_vigente`: el comprobante que vale hoy (el último reemplazo, o el del
  envío). `nucleo.comprobante_envio_rechazado`: si el antivirus rechazó ese.
- `nucleo.reemplazar_comprobante_envio_solicitud_formal`: los controles de registrar el envío
  (`solicitud_formal.firmar`, modo, sesión e identidad humana), solo con el comprobante vigente
  rechazado, con un adjunto nuevo de esa solicitud para el envío, subido por quien reemplaza, que el
  antivirus no rechazó y que nadie usó. No toca medio, fecha ni vencimiento. Queda en la auditoría
  (`consorcio.solicitud_formal.reemplazar_comprobante`).
- `exigir_acto_con_adjunto` deja subir el adjunto del envío también con el comprobante vigente
  rechazado; `exigir_adjunto_solicitud_formal` cuenta como usado el que citó un reemplazo;
  `solicitudes_formales_lectura` muestra el comprobante vigente; `exportar_mandatos` suma la tabla
  nueva.

Cambiar el cuerpo de una función existente se hace sobre lo que hay en la base
(`pg_get_functiondef`), con reemplazos que tienen que aparecer exactamente una vez; el downgrade
aplica los mismos reemplazos al revés, así que deja cada función como estaba, letra por letra.

Downgrade: se niega (`downgrade_con_datos`) si hay algún reemplazo de comprobante. Si no, todo
vuelve a `0111`.

Revisión: 0112
Anterior: 0111
"""

from alembic import op
from sqlalchemy import text

revision = "0112"
down_revision = "0111"
branch_labels = None
depends_on = None

TABLA = "nucleo.solicitud_formal_envio_comprobante"
ACCION = "consorcio.solicitud_formal.reemplazar_comprobante"

# --- Invitación: `vinculo_no_corresponde` en el paso --------------------------------------------

PASO = "nucleo.paso_invitacion(bytea, uuid)"
CAMBIOS_PASO = (
    (
        "     where x.correo = i.correo and x.eliminado_en is null;\n    if p is null then\n",
        "     where x.correo = i.correo and x.eliminado_en is null;\n"
        "    -- 0112: la invitación que nombra su vínculo y cuya persona ya no existe\n"
        "    -- (`aceptar_invitacion` de 0025, antes de mirar la sesión).\n"
        "    if i.vinculo_id is not null and p is null then\n"
        "      return 'vinculo_no_corresponde';\n"
        "    end if;\n"
        "    if p is null then\n",
    ),
    (
        "    return 'aceptar';\n",
        "    -- 0112: exactamente el vínculo que nombra, con la condición de `aceptar_invitacion`\n"
        "    -- (0025): de esa persona, unidad y carácter, y sin terminar hoy.\n"
        "    if i.vinculo_id is not null and not exists (\n"
        "         select 1 from nucleo.vinculo v\n"
        "          where v.id = i.vinculo_id and v.consorcio_id = i.consorcio_id\n"
        "            and v.unidad_id = i.unidad_id and v.persona_id = p and v.tipo = i.tipo\n"
        "            and (v.vigente_hasta is null\n"
        "                 or v.vigente_hasta > nucleo.hoy_del_consorcio(i.consorcio_id))) then\n"
        "      return 'vinculo_no_corresponde';\n"
        "    end if;\n"
        "    return 'aceptar';\n",
    ),
)

# --- Comprobante del envío: la tabla y los actos nuevos ------------------------------------------

TABLA_SQL = f"""
create table {TABLA} (
  consorcio_id          uuid not null,
  id                    uuid primary key default plataforma.uuid_v7(),
  solicitud_id          uuid not null,
  anterior_adjunto_id   uuid not null,
  adjunto_id            uuid not null unique,
  reemplazado_por       uuid not null references nucleo.persona (id),
  identidad_reemplazo   uuid not null,
  reemplazado_en        timestamptz not null default now(),
  -- Cada comprobante se reemplaza una sola vez: la cadena no se bifurca.
  unique (consorcio_id, anterior_adjunto_id),
  check (anterior_adjunto_id <> adjunto_id),
  foreign key (consorcio_id, solicitud_id)
    references nucleo.solicitud_formal_envio (consorcio_id, solicitud_id),
  foreign key (consorcio_id, anterior_adjunto_id)
    references nucleo.solicitud_formal_adjunto (consorcio_id, id),
  foreign key (consorcio_id, adjunto_id)
    references nucleo.solicitud_formal_adjunto (consorcio_id, id),
  foreign key (consorcio_id, identidad_reemplazo)
    references nucleo.identidad_humana (consorcio_id, id)
);
create index solicitud_formal_envio_comprobante_solicitud
  on {TABLA} (consorcio_id, solicitud_id);
alter table {TABLA} enable row level security;
alter table {TABLA} force row level security;
create policy por_consorcio on {TABLA} for select
  using (consorcio_id = plataforma.consorcio_actual());
revoke all on {TABLA} from public, cem_app;
create trigger historia_solicitud_formal_envio_comprobante before update or delete on {TABLA}
  for each row execute function nucleo.negar_cambio_historia_orden_pago();
create trigger historia_solicitud_formal_envio_comprobante_truncate before truncate on {TABLA}
  for each statement execute function nucleo.negar_cambio_historia_orden_pago();
"""

VIGENTE = "nucleo.comprobante_envio_vigente(uuid, uuid)"
RECHAZADO = "nucleo.comprobante_envio_rechazado(uuid, uuid)"
REEMPLAZAR = "nucleo.reemplazar_comprobante_envio_solicitud_formal(uuid, uuid, uuid, text, uuid)"
INTERNAS = (RECHAZADO, VIGENTE)
"""En el orden en que se pueden borrar: cada una antes de las que llama."""

FUNCIONES = f"""
-- El comprobante del envío que vale hoy: el último reemplazo o, sin reemplazos, el del envío. Nulo
-- si no hay envío o es por la aplicación (no lleva comprobante).
create function {VIGENTE.replace("(uuid, uuid)", "(c uuid, solicitud uuid)")}
  returns uuid
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select coalesce(
      (select k.adjunto_id from {TABLA} k
        where k.consorcio_id = c and k.solicitud_id = solicitud
        order by k.reemplazado_en desc, k.id desc
        limit 1),
      (select n.adjunto_id from nucleo.solicitud_formal_envio n
        where n.consorcio_id = c and n.solicitud_id = solicitud))
  $$;

-- Si el antivirus rechazó el comprobante vigente del envío.
create function {RECHAZADO.replace("(uuid, uuid)", "(c uuid, solicitud uuid)")}
  returns boolean
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select coalesce((
      select x.estado = 'rechazado'
        from nucleo.solicitud_formal_adjunto a
        join nucleo.version_documento x on x.id = a.version_documento_id
       where a.consorcio_id = c
         and a.id = {VIGENTE.split("(")[0]}(c, solicitud)), false)
  $$;

-- Pregunta 17 (FL-SOL-04): otro comprobante para el envío ya registrado, si el antivirus rechazó el
-- vigente. Medio, fecha y vencimiento no cambian.
create function nucleo.reemplazar_comprobante_envio_solicitud_formal(solicitud uuid, adjunto uuid,
                                                                     actor uuid,
                                                                     origen_pedido text,
                                                                     sesion uuid)
  returns void
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    identidad uuid;
    s nucleo.solicitud_formal;
    anterior uuid;
  begin
    identidad := nucleo.exigir_acto_solicitud_formal(c, actor, sesion, 'solicitud_formal.firmar',
                                                     origen_pedido);
    s := nucleo.solicitud_formal_bloqueada(c, solicitud);
    anterior := nucleo.comprobante_envio_vigente(c, s.id);
    if anterior is null or not nucleo.comprobante_envio_rechazado(c, s.id) then
      raise exception 'el comprobante del envío no fue rechazado' using errcode = 'P0001',
        hint = 'solicitud_no_admite';
    end if;
    if adjunto is null then
      raise exception 'falta el comprobante nuevo' using errcode = 'P0001',
        hint = 'adjunto_invalido';
    end if;
    perform nucleo.exigir_adjunto_solicitud_formal(c, s.id, adjunto, 'envio', actor);
    insert into {TABLA}
      (consorcio_id, solicitud_id, anterior_adjunto_id, adjunto_id, reemplazado_por,
       identidad_reemplazo)
    values (c, s.id, anterior, adjunto, actor, identidad);
    perform nucleo.auditar_solicitud_formal(c, '{ACCION}', s.id, actor,
      jsonb_build_object('numero', s.numero, 'anterior_adjunto_id', anterior,
                         'adjunto_id', adjunto));
  end
  $$;
"""

# --- Comprobante del envío: lo que cambia en las funciones de 0108 y 0110 ------------------------

CON_ADJUNTO = (
    "nucleo.exigir_acto_con_adjunto(uuid, nucleo.solicitud_formal, text, uuid, uuid, text)"
)
CAMBIOS_CON_ADJUNTO = (
    (
        "    if para_nuevo = 'envio' then\n      if estado <> 'envio_pendiente' then\n",
        "    if para_nuevo = 'envio' then\n"
        "      -- 0112 (pregunta 17): también, si el antivirus rechazó el comprobante del envío\n"
        "      -- ya registrado.\n"
        "      if estado <> 'envio_pendiente'\n"
        "         and not nucleo.comprobante_envio_rechazado(c, s.id) then\n",
    ),
)

EXIGIR_ADJUNTO = "nucleo.exigir_adjunto_solicitud_formal(uuid, uuid, uuid, text, uuid)"
CAMBIOS_EXIGIR_ADJUNTO = (
    (
        "       or exists (select 1 from nucleo.solicitud_formal_respuesta r\n",
        "       -- 0112: el que citó un reemplazo del comprobante del envío también está usado.\n"
        f"       or exists (select 1 from {TABLA} k\n"
        "                   where k.consorcio_id = c and k.adjunto_id = adjunto)\n"
        "       or exists (select 1 from nucleo.solicitud_formal_respuesta r\n",
    ),
)

SOLICITUDES = "nucleo.solicitudes_formales_lectura(uuid, text, uuid)"
CAMBIOS_SOLICITUDES = (
    (
        "          on an.consorcio_id = n.consorcio_id and an.id = n.adjunto_id\n",
        "          -- 0112 (pregunta 17): el comprobante vigente, con sus reemplazos.\n"
        "          on an.consorcio_id = n.consorcio_id\n"
        "         and an.id = nucleo.comprobante_envio_vigente(n.consorcio_id, n.solicitud_id)\n",
    ),
)

EXPORTAR = "nucleo.exportar_mandatos(uuid, uuid)"
CAMBIOS_EXPORTAR = (
    (
        "       order by x.registrado_en, x.solicitud_id;\n",
        "       order by x.registrado_en, x.solicitud_id;\n"
        "    -- 0112: los reemplazos del comprobante del envío.\n"
        "    return query\n"
        "      select 'solicitud_formal_envio_comprobante'::text,\n"
        "             jsonb_build_object('id', x.id, 'solicitud_id', x.solicitud_id,"
        " 'anterior_adjunto_id', x.anterior_adjunto_id, 'adjunto_id', x.adjunto_id,"
        " 'reemplazado_por_nombre', nucleo.nombre_para_exportar(c, x.reemplazado_por, hoy),"
        " 'reemplazado_en', x.reemplazado_en)\n"
        f"        from {TABLA} x\n"
        "       where x.consorcio_id = c\n"
        "       order by x.reemplazado_en, x.id;\n",
    ),
)

EN_SU_LUGAR = (
    (PASO, CAMBIOS_PASO),
    (CON_ADJUNTO, CAMBIOS_CON_ADJUNTO),
    (EXIGIR_ADJUNTO, CAMBIOS_EXIGIR_ADJUNTO),
    (SOLICITUDES, CAMBIOS_SOLICITUDES),
    (EXPORTAR, CAMBIOS_EXPORTAR),
)
"""Las que llaman a las internas nuevas: se cambian después de crearlas y se restituyen antes de
borrarlas."""

GUARDA = f"""
lock table {TABLA} in access exclusive mode;
do $guarda$
begin
  if exists (select 1 from {TABLA}) then
    raise exception '0112 no baja con reemplazos de comprobante: se perdería la constancia'
      using errcode = 'P0001', hint = 'downgrade_con_datos';
  end if;
end
$guarda$;
"""


def _definicion(firma: str) -> str:
    return (
        op.get_bind()
        .execute(text("select pg_get_functiondef(to_regprocedure(:f))"), {"f": firma})
        .scalar_one()
    )


def _cambiar_funcion(firma: str, cambios, al_reves: bool) -> None:
    """Aplica `cambios` al cuerpo que la función tiene hoy en la base. Cada texto tiene que estar
    una sola vez: si no, la migración se detiene (la función no es la que se esperaba)."""
    definicion = _definicion(firma)
    for antes, despues in reversed(cambios) if al_reves else cambios:
        if al_reves:
            antes, despues = despues, antes
        if definicion.count(antes) != 1:
            raise RuntimeError(f"0112: {firma} no tiene exactamente una vez {antes!r}")
        definicion = definicion.replace(antes, despues)
    op.get_bind().exec_driver_sql(definicion)


def upgrade() -> None:
    op.execute(TABLA_SQL)
    op.execute(FUNCIONES)
    for funcion in (REEMPLAZAR, *INTERNAS):
        op.execute(f"revoke all on function {funcion} from public")
    op.execute(f"grant execute on function {REEMPLAZAR} to cem_app")
    for firma, cambios in EN_SU_LUGAR:
        _cambiar_funcion(firma, cambios, al_reves=False)


def downgrade() -> None:
    op.execute(GUARDA)
    for firma, cambios in reversed(EN_SU_LUGAR):
        _cambiar_funcion(firma, cambios, al_reves=True)
    for funcion in (REEMPLAZAR, *INTERNAS):
        op.execute(f"drop function {funcion}")
    op.execute(f"drop table {TABLA}")
