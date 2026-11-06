"""El comprobante en un paso (`Rediseno_Circuito_Pagos_2026-11-06.md`, §4 y §8, repositorio CEM).

- **La subida deja de pedir el gesto.** `crear_comprobante_de_pago` y
  `nueva_version_de_comprobante` siguen pidiendo la facultad o haber visto la cuenta, el modo, la
  sesión de 12 horas y la identidad humana: solo sale la verificación de los últimos 10 minutos,
  como en `0071`. Quien sube ya vio la cuenta, no mueve dinero y la atadura ocurre al registrar,
  que sí pide el gesto. Antes el gesto vencía entre transferir y subir.
- **Corregir no pide la cuenta otra vez** (§8). `registrar_pago_manual` acepta `cuenta` nula solo
  si hay un registro observado que reemplazar: ese registro ya acreditó la cuenta y no se guarda,
  así que la ficha no tiene de dónde mostrarla sin volver a entregarla. En un registro nuevo sigue
  dando `no_coincide_con_la_orden`.
- **`pagos_manuales_lectura` suma `registrado_por_nombre`**: el Consejo ve quién registró (§10).
  La RLS `propia` de `nucleo.persona` no deja leer nombres ajenos con `cem_app`.
- **`comprobante_subido_lectura` devuelve lo subido con su estado**, de lo último a lo primero: la
  ficha dice si el antivirus lo revisa o lo rechazó. Antes elegía por `analizada_en`: un archivo
  viejo que tardaba más le ganaba al nuevo, y volver a subir tiene que reemplazar.
- **`pagos_por_aclarar.comprobante_id`** toma el último de la ejecución y no el de la orden (§4,
  `0068`): atado a una ejecución anterior, esa pantalla se quedaba sin salida.

Downgrade: vuelve a las definiciones anteriores. No hay datos que dependan de esta: la cuenta no
se guarda en el registro.

Revisión: 0073
Anterior: 0072
"""

from alembic import op
from sqlalchemy import text

revision = "0073"
down_revision = "0072"
branch_labels = None
depends_on = None

CATEGORIA = "facturas_y_comprobantes"
CREAR = "nucleo.crear_comprobante_de_pago(uuid, text, date, uuid, text, uuid)"
VERSION = "nucleo.nueva_version_de_comprobante(uuid, uuid, text, integer, text, uuid, text, uuid)"
REGISTRO = (
    "nucleo.registrar_pago_manual(uuid, integer, bytea, text, date, bigint, text, text, uuid,"
    " uuid, text, uuid)"
)
PAGOS = "nucleo.pagos_manuales_lectura(uuid, text, uuid, text, uuid)"
SUBIDO = "nucleo.comprobante_subido_lectura(uuid, text, uuid, text, uuid)"
POR_ACLARAR = "nucleo.pagos_por_aclarar(uuid, text, uuid)"

SESION = (
    "nucleo.exigir_sesion_economica(c, actor, sesion, origen_pedido);\n",
    "nucleo.exigir_sesion_operativa(c, actor, sesion, origen_pedido, false);\n",
)
CREAR_CAMBIOS = (
    (
        "    -- R10, como el acto de ejecución en el que se vio la cuenta (0066).\n",
        "    -- R10 sin el gesto (0073): subir no mueve dinero y registrar lo pide.\n",
    ),
    (
        "      perform nucleo.exigir_acto_economico(c, actor, sesion, 'egresos.ejecutar_pago',\n"
        "                                           origen_pedido);\n",
        "      perform nucleo.exigir_acto_sin_gesto(c, actor, sesion, 'egresos.ejecutar_pago',\n"
        "                                           origen_pedido);\n",
    ),
    SESION,
)
VERSION_CAMBIOS = (SESION,)

REGISTRO_CAMBIOS = (
    (
        "       or regexp_replace(coalesce(cuenta, ''), '[^0-9]', '', 'g') <> e.cuenta_destino then\n",
        "       -- Sin cuenta, solo al corregir: el registro observado ya la acreditó (0073).\n"
        "       or ((anterior.id is null or cuenta is not null)\n"
        "           and regexp_replace(coalesce(cuenta, ''), '[^0-9]', '', 'g')\n"
        "               <> e.cuenta_destino) then\n",
    ),
)

PAGOS_CAMBIOS = (
    (
        "conciliado_en timestamp with time zone, fuera_de_termino boolean)\n",
        "conciliado_en timestamp with time zone, fuera_de_termino boolean,"
        " registrado_por_nombre text)\n",
    ),
    (
        "             k.conciliado_por, k.conciliado_en, p.fuera_de_termino\n",
        "             k.conciliado_por, k.conciliado_en, p.fuera_de_termino, r.nombre\n",
    ),
    (
        "        left join nucleo.conciliacion_pago_manual k on k.pago_id = p.id\n",
        "        join nucleo.persona r on r.id = p.registrado_por\n"
        "        left join nucleo.conciliacion_pago_manual k on k.pago_id = p.id\n",
    ),
)

POR_ACLARAR_CAMBIOS = (
    (
        "             and d.estado = 'vigente' and d.cargado_por = actor\n",
        "             and d.estado = 'vigente' and d.cargado_por = actor\n"
        "             -- De esta ejecución: uno anterior no sirve para registrar (0073).\n"
        "             and d.cargado_en >= e.creada_en\n"
        "             and not exists (select 1 from nucleo.comprobante_pago_manual t\n"
        "                              where t.consorcio_id = d.consorcio_id\n"
        "                                and t.documento_id = d.id and t.ejecucion_id <> e.id)\n",
    ),
)


def _comprobante_subido(columnas: str, valores: str, orden: str, limite: str) -> str:
    return f"""
create function {SUBIDO.split("(")[0]}(orden uuid, facultad text, actor uuid,
                                                  origen_pedido text, sesion uuid)
  returns table (documento_id uuid, version_documento_id uuid{columnas})
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
  begin
    perform nucleo.exigir_lectura_egresos(c, facultad, actor, origen_pedido, sesion);
    return query
      select d.id, v.id{valores}
        from nucleo.orden_pago o
        join nucleo.ejecucion_orden_pago e
          on e.consorcio_id = o.consorcio_id and e.orden_id = o.id
         and e.version = o.version_vigente
        join nucleo.documento d
          on d.consorcio_id = o.consorcio_id and d.orden_comprobante = o.id
        join nucleo.version_documento v
          on v.consorcio_id = d.consorcio_id and v.documento_id = d.id
        left join nucleo.comprobante_pago_manual t
          on t.consorcio_id = d.consorcio_id and t.documento_id = d.id
       where o.consorcio_id = c and o.id = orden and d.estado = 'vigente'
         and d.categoria = '{CATEGORIA}' and v.subida_por = actor
         {limite}and v.subida_en >= e.creada_en and (t.ejecucion_id is null or t.ejecucion_id = e.id)
       order by {orden};
  end
  $$;
revoke all on function {SUBIDO} from public;
grant execute on function {SUBIDO} to cem_app;
"""


SUBIDO_0073 = _comprobante_subido(", estado text", ", v.estado", "v.subida_en desc, v.id desc", "")
SUBIDO_0072 = _comprobante_subido(
    "", "", "v.analizada_en desc, v.id desc\n       limit 1", "and v.estado = 'disponible' "
)


def _definicion(funcion: str, cambios, *, al_reves: bool) -> str:
    definicion = (
        op.get_bind()
        .execute(text("select pg_get_functiondef(cast(:f as regprocedure))"), {"f": funcion})
        .scalar_one()
    )
    for antes, despues in cambios:
        if al_reves:
            antes, despues = despues, antes
        if definicion.count(antes) != 1:
            raise RuntimeError(f"0073: {funcion} no tiene exactamente una vez {antes!r}")
        definicion = definicion.replace(antes, despues)
    return definicion


def _reemplazar(funcion: str, cambios, *, al_reves: bool = False) -> None:
    op.execute(_definicion(funcion, cambios, al_reves=al_reves))


def _otra_lectura(funcion: str, cambios, *, al_reves: bool = False) -> None:
    """Lo que devuelve cambia: se borra y se crea de nuevo, con su permiso."""
    definicion = _definicion(funcion, cambios, al_reves=al_reves)
    op.execute(f"drop function {funcion}")
    op.execute(definicion)
    op.execute(f"revoke all on function {funcion} from public")
    op.execute(f"grant execute on function {funcion} to cem_app")


def upgrade() -> None:
    _reemplazar(CREAR, CREAR_CAMBIOS)
    _reemplazar(VERSION, VERSION_CAMBIOS)
    _reemplazar(REGISTRO, REGISTRO_CAMBIOS)
    _reemplazar(POR_ACLARAR, POR_ACLARAR_CAMBIOS)
    _otra_lectura(PAGOS, PAGOS_CAMBIOS)
    op.execute(f"drop function {SUBIDO}")
    op.execute(SUBIDO_0073)


def downgrade() -> None:
    op.execute(f"drop function {SUBIDO}")
    op.execute(SUBIDO_0072)
    _otra_lectura(PAGOS, PAGOS_CAMBIOS, al_reves=True)
    _reemplazar(POR_ACLARAR, POR_ACLARAR_CAMBIOS, al_reves=True)
    _reemplazar(REGISTRO, REGISTRO_CAMBIOS, al_reves=True)
    _reemplazar(VERSION, VERSION_CAMBIOS, al_reves=True)
    _reemplazar(CREAR, CREAR_CAMBIOS, al_reves=True)
