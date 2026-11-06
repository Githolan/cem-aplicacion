"""Lectura del comprobante con IA y fraccionamiento que pasa por el Consejo
(`Rediseno_Circuito_Pagos_2026-11-06.md`, §5, §6 y §12, repositorio CEM; decisión §12 del
propietario del 05-11-2026).

**`nucleo.lectura_comprobante`.** Lo que la capa `llm` leyó de una versión de un comprobante de
pago, una fila por versión. La escribe solo `cem-trabajos` (`guardar_lectura_comprobante`, con
`exigir_trabajo`) y no se cambia ni se borra: es el rastro de lo que dijo el modelo, con el modelo
que informó la capa y la versión del prompt. Propone, no decide: ninguna fila aprueba, concilia ni
mueve una orden.

**La cuenta leída no se guarda.** La función la recibe, la compara dígito a dígito con la de la
ejecución y guarda solo `cuenta_coincide` y los últimos cuatro dígitos: la cuenta de la orden nunca
sale de la base y la leída no queda en claro. Un `trigger` niega además una cuenta en el titular o
en el número de operación. La salvedad: «Pagué otra cosa» declara otra cuenta, y esa se compara con
lo leído por sus últimos dígitos.

**Con la cuenta leída no hace falta escribirla** (`Pagos-Comprobante-Coincide`):
`registrar_pago_manual` acepta un registro nuevo sin cuenta solo si la lectura del archivo que queda
como evidencia dio que coincide. Sin esa lectura sigue dando `no_coincide_con_la_orden`.

**Qué le toca a quien mira** (`lectura_comprobante_lectura`): con la barrera de egresos. `leyendo`
sale del trabajo de la cola: el proceso que decide si la lectura está encendida es `cem-trabajos`, y
`cem-backend` no necesita saberlo.

**Exportación económica (`0069`):** `exportar_pagos` suma `lectura_comprobante`.

**Fraccionamiento (§12).** Con `alerta_fraccionamiento`, `aprobar_por_franja` deja la orden en
revisión aunque su franja no pida aprobación: «si dos órdenes juntas superan el umbral, en el mismo
periodo la alerta debería mandarla a Aprueba». Vale al enviar, al modificar y al renovar, los tres
caminos que la llaman. La única otra forma de llegar a Aprobada es aprobarla, que cuenta las
aprobaciones, y por eso `exigir_orden_emitible` no cambia: lo aprobado antes por la franja se aprobó
con la regla de entonces y sigue, como las alertas ya evaluadas en `0052`.

Downgrade: se niega (`downgrade_con_datos`) si hay alguna lectura. Sin eso, vuelve a las
definiciones anteriores.

Revisión: 0077
Anterior: 0076
"""

from alembic import op
from sqlalchemy import text

revision = "0077"
down_revision = "0076"
branch_labels = None
depends_on = None

TRABAJO = "documentos.leer"
"""El de `TRABAJO_LEER`, en `app/consorcios/lectura_comprobantes.py`."""
DIGITOS_VISIBLES = 4
"""Los de `DIGITOS_VISIBLES_CUENTA`, en `contratos/`."""

FRANJA = "nucleo.aprobar_por_franja(uuid, uuid, integer, uuid)"
REGISTRAR = (
    "nucleo.registrar_pago_manual(uuid, integer, bytea, text, date, bigint, text, text, uuid, uuid,"
    " text, uuid)"
)
EXPORTAR = "nucleo.exportar_pagos(uuid, uuid)"
ACREDITA = "nucleo.lectura_acredita_cuenta(uuid, uuid, uuid)"
ES_COMPROBANTE = "nucleo.version_es_comprobante_de_pago(uuid)"
GUARDAR = (
    "nucleo.guardar_lectura_comprobante(uuid, text, text, boolean, text, numeric, text, text, text,"
    " text, date)"
)
LEER = "nucleo.lectura_comprobante_lectura(uuid, text, uuid, text, uuid)"

TABLA = """
create table nucleo.lectura_comprobante (
  consorcio_id            uuid not null,
  id                      uuid not null default plataforma.uuid_v7(),
  version_documento_id    uuid not null unique references nucleo.version_documento (id),
  documento_id            uuid not null,
  ejecucion_id            uuid not null,
  -- El que informó la capa. Nulo solo si no llegó a responder.
  modelo                  text check (length(modelo) between 1 and 200),
  version_prompt          text not null check (length(version_prompt) between 1 and 60),
  legible                 boolean not null,
  error                   text check (error in ('falla_del_modelo', 'respuesta_invalida')),
  importe                 numeric(18, 2) check (importe > 0),
  moneda                  text check (moneda ~ '^[A-Z]{3}$'),
  -- De la cuenta, solo esto: la completa no se guarda.
  cuenta_ultimos_digitos  text check (cuenta_ultimos_digitos ~ '^[0-9]{4}$'),
  cuenta_coincide         boolean,
  titular_destino         text check (length(titular_destino) between 1 and 200),
  numero_operacion        text check (length(numero_operacion) between 1 and 60),
  fecha                   date,
  leida_en                timestamptz not null default clock_timestamp(),
  primary key (id),
  unique (consorcio_id, id),
  foreign key (consorcio_id, documento_id) references nucleo.documento (consorcio_id, id),
  foreign key (consorcio_id, ejecucion_id)
    references nucleo.ejecucion_orden_pago (consorcio_id, id),
  constraint lectura_cuenta_completa
    check ((cuenta_ultimos_digitos is null) = (cuenta_coincide is null)),
  constraint lectura_error_no_es_legible check (error is null or not legible),
  constraint lectura_modelo_informado check (modelo is not null or error = 'falla_del_modelo'),
  constraint lectura_sin_campos_si_no_legible check (
    legible or (importe is null and moneda is null and cuenta_ultimos_digitos is null
                and titular_destino is null and numero_operacion is null and fecha is null))
);
alter table nucleo.lectura_comprobante enable row level security;
alter table nucleo.lectura_comprobante force row level security;
create policy por_consorcio on nucleo.lectura_comprobante for select
  using (consorcio_id = plataforma.consorcio_actual());
revoke all on nucleo.lectura_comprobante from public, cem_app;

create trigger historia_lectura_comprobante before update or delete
  on nucleo.lectura_comprobante
  for each row execute function nucleo.negar_cambio_historia_orden_pago();
create trigger historia_lectura_comprobante_truncate before truncate
  on nucleo.lectura_comprobante
  for each statement execute function nucleo.negar_cambio_historia_orden_pago();
create trigger titular_sin_cuenta before insert on nucleo.lectura_comprobante
  for each row execute function nucleo.negar_cuenta_en_motivo('titular_destino');
create trigger numero_sin_cuenta before insert on nucleo.lectura_comprobante
  for each row execute function nucleo.negar_cuenta_en_motivo('numero_operacion');
"""

FUNCIONES = f"""
-- Solo los comprobantes cargados desde el pago de una orden se leen: una factura no.
create function nucleo.version_es_comprobante_de_pago(version uuid)
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
       where v.consorcio_id = c and v.id = version and d.orden_comprobante is not null);
  end
  $$;

create function nucleo.guardar_lectura_comprobante(version uuid, modelo_informado text,
                                                   prompt text, es_legible boolean, falla text,
                                                   importe_leido numeric, moneda_leida text,
                                                   cuenta_leida text, titular text, numero text,
                                                   fecha_leida date)
  returns boolean
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    documento uuid;
    orden uuid;
    subida timestamptz;
    e nucleo.ejecucion_orden_pago;
  begin
    perform nucleo.exigir_trabajo(c);
    if cuenta_leida !~ '^[0-9]{{22}}$' then
      raise exception 'la cuenta leída no es una cuenta' using errcode = 'P0001',
        hint = 'cuenta_invalida';
    end if;
    select d.id, d.orden_comprobante, v.subida_en into documento, orden, subida
      from nucleo.version_documento v
      join nucleo.documento d on d.consorcio_id = v.consorcio_id and d.id = v.documento_id
     where v.consorcio_id = c and v.id = version;
    -- La ejecución para la que se subió: la última que ya existía.
    select * into e from nucleo.ejecucion_orden_pago x
     where x.consorcio_id = c and x.orden_id = orden and x.creada_en <= subida
     order by x.creada_en desc, x.id desc limit 1;
    if e.id is null then
      raise exception 'la versión no es el comprobante de un pago' using errcode = 'P0001',
        hint = 'comprobante_invalido';
    end if;
    insert into nucleo.lectura_comprobante
      (consorcio_id, version_documento_id, documento_id, ejecucion_id, modelo, version_prompt,
       legible, error, importe, moneda, cuenta_ultimos_digitos, cuenta_coincide, titular_destino,
       numero_operacion, fecha)
    values (c, version, documento, e.id, modelo_informado, prompt, es_legible, falla,
            importe_leido, moneda_leida, right(cuenta_leida, {DIGITOS_VISIBLES}),
            cuenta_leida = e.cuenta_destino, titular, numero, fecha_leida)
    on conflict (version_documento_id) do nothing;
    return found;
  end
  $$;

-- Si el archivo que queda como evidencia dice la cuenta de esta ejecución. Es la versión que elige
-- `registrar_pago_manual`.
create function nucleo.lectura_acredita_cuenta(c uuid, ejecucion uuid, documento uuid)
  returns boolean
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select exists (
      select 1 from nucleo.lectura_comprobante l
       where l.consorcio_id = c and l.ejecucion_id = ejecucion and l.documento_id = documento
         and l.cuenta_coincide
         and l.version_documento_id = (
           select v.id from nucleo.version_documento v
            where v.consorcio_id = c and v.documento_id = documento and v.estado = 'disponible'
            order by v.analizada_en desc, v.id desc limit 1))
  $$;

create function nucleo.lectura_comprobante_lectura(version uuid, facultad text, actor uuid,
                                                   origen_pedido text, sesion uuid)
  returns table (estado text, importe numeric, moneda text, cuenta_ultimos_digitos text,
                 cuenta_coincide boolean, titular_destino text, numero_operacion text, fecha date)
  language plpgsql security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    l nucleo.lectura_comprobante;
    trabajo text;
  begin
    perform nucleo.exigir_lectura_egresos(c, facultad, actor, origen_pedido, sesion);
    select * into l from nucleo.lectura_comprobante x
     where x.consorcio_id = c and x.version_documento_id = version;
    if l.id is not null then
      return query select case when l.legible then 'leida' else 'no_leida' end, l.importe,
        l.moneda, l.cuenta_ultimos_digitos, l.cuenta_coincide, l.titular_destino,
        l.numero_operacion, l.fecha;
      return;
    end if;
    select j.estado::text into trabajo from plataforma.jobs j
     where j.consorcio_id = c and j.clave_unica = '{TRABAJO}:' || version::text;
    return query select
      case when trabajo in ('pendiente', 'en_curso') then 'leyendo'
           when trabajo = 'muerto' then 'no_leida'
           else 'sin_lectura' end,
      null::numeric, null::text, null::text, null::boolean, null::text, null::text, null::date;
  end
  $$;
"""

INTERNAS = (ACREDITA,)
RUTAS = (ES_COMPROBANTE, GUARDAR, LEER)

FRANJA_CAMBIOS = (
    ("    f text;\n", "    f text;\n    alerta boolean;\n"),
    (
        "    select v.franja into f from nucleo.orden_pago_version v\n",
        "    select v.franja, v.alerta_fraccionamiento into f, alerta\n"
        "      from nucleo.orden_pago_version v\n",
    ),
    (
        "    if not nucleo.franja_requiere_aprobacion(f) then\n",
        "    -- Con la alerta de fraccionamiento la decide el Consejo (decisión §12, `0077`).\n"
        "    if not nucleo.franja_requiere_aprobacion(f) and not alerta then\n",
    ),
)

REGISTRAR_CAMBIOS = (
    (
        "       -- Sin cuenta, solo al corregir: el registro observado ya la acreditó (0073).\n"
        "       or ((anterior.id is null or cuenta is not null)\n",
        "       -- Sin cuenta, solo al corregir, porque el registro observado ya la acreditó\n"
        "       -- (0073), o si la lectura del archivo dio que coincide (0077).\n"
        "       or ((anterior.id is null or cuenta is not null)\n"
        "           and (cuenta is not null\n"
        "                or not nucleo.lectura_acredita_cuenta(c, e.id, documento))\n",
    ),
)

EXPORTAR_CAMBIOS = (
    (
        "        from nucleo.columnas_extracto x\n       where x.consorcio_id = c;\n",
        "        from nucleo.columnas_extracto x\n       where x.consorcio_id = c;\n"
        "    return query\n"
        "      select 'lectura_comprobante'::text,\n"
        "             jsonb_build_object('id', l.id,\n"
        "                                'version_documento_id', l.version_documento_id,\n"
        "                                'documento_id', l.documento_id,\n"
        "                                'ejecucion_id', l.ejecucion_id, 'modelo', l.modelo,\n"
        "                                'version_prompt', l.version_prompt,\n"
        "                                'legible', l.legible, 'error', l.error,\n"
        "                                'importe', l.importe, 'moneda', l.moneda,\n"
        "                                'cuenta_ultimos_digitos', l.cuenta_ultimos_digitos,\n"
        "                                'cuenta_coincide', l.cuenta_coincide,\n"
        "                                'titular_destino',"
        " nucleo.texto_sin_cuenta(l.titular_destino),\n"
        "                                'numero_operacion',"
        " nucleo.texto_sin_cuenta(l.numero_operacion),\n"
        "                                'fecha', l.fecha, 'leida_en', l.leida_en)\n"
        "        from nucleo.lectura_comprobante l\n"
        "       where l.consorcio_id = c\n"
        "       order by l.leida_en, l.id;\n",
    ),
)

REEMPLAZOS = (
    (FRANJA, FRANJA_CAMBIOS),
    (REGISTRAR, REGISTRAR_CAMBIOS),
    (EXPORTAR, EXPORTAR_CAMBIOS),
)

GUARDA_DOWNGRADE = """
-- Hasta el fin del downgrade nadie guarda: la guarda vale para lo que se borra.
lock table nucleo.lectura_comprobante in access exclusive mode;
do $$
begin
  if exists (select 1 from nucleo.lectura_comprobante) then
    raise exception '0077 no baja con lecturas de comprobantes: se perdería el rastro'
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
            raise RuntimeError(f"0077: {funcion} no tiene exactamente una vez {antes!r}")
        definicion = definicion.replace(antes, despues)
    return definicion


def upgrade() -> None:
    op.execute(TABLA)
    op.execute(FUNCIONES)
    for funcion in INTERNAS + RUTAS:
        op.execute(f"revoke all on function {funcion} from public")
    for funcion in RUTAS:
        op.execute(f"grant execute on function {funcion} to cem_app")
    for funcion, cambios in REEMPLAZOS:
        op.execute(_definicion(funcion, cambios, al_reves=False))


def downgrade() -> None:
    op.execute(GUARDA_DOWNGRADE)
    for funcion, cambios in reversed(REEMPLAZOS):
        op.execute(_definicion(funcion, cambios, al_reves=True))
    for funcion in RUTAS + INTERNAS:
        op.execute(f"drop function {funcion}")
    op.execute("drop table nucleo.lectura_comprobante")
