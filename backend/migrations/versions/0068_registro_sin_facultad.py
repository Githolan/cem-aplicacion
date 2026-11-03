"""Quien transfirió y perdió la facultad registra el pago y carga el comprobante (P3 184; decisión
del propietario, 03-11-2026: opción A).

Con `0067` (P3 179), quien vio la cuenta en el acto de la ejecución abierta y dejó de tener
`egresos.ejecutar_pago` declara «no se pagó». Si en cambio **sí** transfirió, no podía registrarlo:
el registro, la carga del comprobante y la de su archivo exigían la facultad. La orden quedaba sin
salida (si además la aprobación venció, nadie más ve la cuenta).

Ahora, sin la facultad, lo hace **solo** quien vio la cuenta de la ejecución abierta, y con R10 y su
identidad verificada (`nucleo.exigir_sesion_economica`), como en `0067`: primero se comprueba que
la vio (a nadie más se le dice nada: `42501`), después R10.

- **`crear_comprobante_de_pago`**: sin la facultad, sin `documentos.cargar` (ya no la tiene).
- **`nucleo.nueva_version_de_comprobante`** (nueva): la versión del archivo de un comprobante de esa
  orden, con la sesión, solo para quien ya no ejecuta. `nueva_version` (`0035`) no recibe sesión y
  exige la facultad de carga.
- **`nucleo.fijar_version_objeto`**: el `VersionId` del almacén (OCI lo devuelve siempre) también
  para esa versión, sin la facultad de carga (revisión independiente de #197: sin esto, la subida
  fallaba en staging y producción).
- **`registrar_pago_manual`**: sin la facultad; el pago queda con `sin_facultad`. Lo concilia otro
  consejero, como siempre.
- **`nucleo.pagos_por_aclarar`** agrega la huella de la versión (el registro la pide) y el último
  comprobante de esa orden que cargó quien pide, con el estado de su último archivo.

Downgrade: se niega (`downgrade_con_datos`) si hay un pago registrado sin la facultad. Sin eso,
vuelve a las definiciones de `0067`.

Revisión: 0068
Anterior: 0067
"""

from alembic import op
from sqlalchemy import text

revision = "0068"
down_revision = "0067"
branch_labels = None
depends_on = None

CATEGORIA = "facturas_y_comprobantes"
ABRIO = "nucleo.abrio_la_cuenta_de_la_orden"
REGISTRO = (
    "nucleo.registrar_pago_manual(uuid, integer, bytea, text, date, bigint, text, text, uuid,"
    " uuid, text, uuid)"
)
CREAR = "nucleo.crear_comprobante_de_pago(uuid, text, date, uuid, text, uuid)"
VERSION_SIN_FACULTAD = (
    "nucleo.nueva_version_de_comprobante(uuid, uuid, text, integer, text, uuid, text, uuid)"
)
POR_ACLARAR = "nucleo.pagos_por_aclarar(uuid, text, uuid)"
FIJAR = "nucleo.fijar_version_objeto(uuid, text, uuid, text)"


def _sin_facultad(identidad: str) -> str:
    """La rama de quien vio la cuenta y ya no ejecuta: primero si la vio, después R10."""
    asignar = f"{identidad} := " if identidad else "perform "
    return (
        "    else\n"
        "      -- Quien vio la cuenta de la ejecución abierta y dejó de ejecutar, con R10 y su\n"
        "      -- identidad verificada (P3 184, 0068). Primero si la vio: a nadie más se le dice\n"
        "      -- nada del consorcio ni de la orden.\n"
        "      if actor is null or actor is distinct from plataforma.persona_actual()\n"
        f"         or not {ABRIO}(c, orden, actor) then\n"
        "        raise exception 'sin egresos.ejecutar_pago en este consorcio'"
        " using errcode = '42501';\n"
        "      end if;\n"
        f"      {asignar}nucleo.exigir_sesion_economica(c, actor, sesion, origen_pedido);\n"
    )


CON_FACULTAD = (
    "    if c is null or nucleo.tiene_facultad_vigente(c, actor, 'egresos.ejecutar_pago',\n"
    "                                                  nucleo.hoy_del_consorcio(c)) then\n"
)

CREAR_CAMBIOS = (
    (
        "    perform nucleo.exigir_acto_economico(c, actor, sesion, 'egresos.ejecutar_pago',\n"
        "                                         origen_pedido);\n"
        f"    perform nucleo.exigir_carga_documentos(c, actor, origen_pedido, '{CATEGORIA}');\n",
        CON_FACULTAD
        + "      perform nucleo.exigir_acto_economico(c, actor, sesion, 'egresos.ejecutar_pago',\n"
        "                                           origen_pedido);\n"
        f"      perform nucleo.exigir_carga_documentos(c, actor, origen_pedido, '{CATEGORIA}');\n"
        + _sin_facultad("")
        + "    end if;\n",
    ),
)

COLUMNA = """
-- Lo registró quien vio la cuenta y ya no tenía egresos.ejecutar_pago (P3 184, 0068).
alter table nucleo.pago_manual add column sin_facultad boolean not null default false;
"""

REGISTRO_CAMBIOS = (
    (
        "    tarde boolean;\n  begin\n",
        "    tarde boolean;\n    sin_facultad boolean := false;\n  begin\n",
    ),
    (
        "    identidad := nucleo.exigir_acto_economico(c, actor, sesion, 'egresos.ejecutar_pago',\n"
        "                                              origen_pedido);\n",
        CON_FACULTAD + "      identidad := nucleo.exigir_acto_economico(c, actor, sesion,"
        " 'egresos.ejecutar_pago',\n"
        "                                                origen_pedido);\n"
        + _sin_facultad("identidad")
        + "      sin_facultad := true;\n"
        "    end if;\n",
    ),
    (
        "       identidad_registro, fuera_de_termino)\n",
        "       identidad_registro, fuera_de_termino, sin_facultad)\n",
    ),
    ("            identidad, tarde)\n", "            identidad, tarde, sin_facultad)\n"),
    (
        "'archivo_comprobante', archivo, 'fuera_de_termino', tarde));\n",
        "'archivo_comprobante', archivo, 'fuera_de_termino', tarde,\n"
        "                         'sin_facultad', sin_facultad));\n",
    ),
)

VERSION_SQL = f"""
-- Un archivo del comprobante de la orden, cargado por quien vio la cuenta de su ejecución abierta
-- y ya no ejecuta (P3 184, 0068): con R10, sin la facultad de carga. Las mismas filas y la misma
-- auditoría que nucleo.nueva_version.
create function {VERSION_SIN_FACULTAD.split("(")[0]}(orden uuid, documento uuid, tipo_nuevo text,
                                                 tamano_nuevo integer, sha256_nuevo text,
                                                 actor uuid, origen_pedido text, sesion uuid)
  returns table (id uuid, clave_objeto text)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    v uuid := plataforma.uuid_v7();
    clave text;
  begin
    if c is null or actor is null or actor is distinct from plataforma.persona_actual()
       or not {ABRIO}(c, orden, actor)
       or nucleo.tiene_facultad_vigente(c, actor, 'egresos.ejecutar_pago',
                                        nucleo.hoy_del_consorcio(c))
       or not exists (select 1 from nucleo.documento d
                       where d.consorcio_id = c and d.id = documento and d.estado = 'vigente'
                         and d.categoria = '{CATEGORIA}' and d.orden_comprobante = orden) then
      raise exception 'sin egresos.ejecutar_pago en este consorcio' using errcode = '42501';
    end if;
    perform nucleo.exigir_sesion_economica(c, actor, sesion, origen_pedido);
    clave := 'cem/' || c || '/' || documento || '/' || v;
    insert into nucleo.version_documento
      (consorcio_id, documento_id, id, tipo, tamano, sha256, clave_objeto, subida_por)
    values (c, documento, v, tipo_nuevo, tamano_nuevo, sha256_nuevo, clave, actor);
    perform nucleo.auditar_documento(c, 'consorcio.documento.cargar_version', 'version_documento',
      v, actor, origen_pedido, jsonb_build_object('tipo', tipo_nuevo, 'tamano', tamano_nuevo));
    return query select v, clave;
  end
  $$;
revoke all on function {VERSION_SIN_FACULTAD} from public;
grant execute on function {VERSION_SIN_FACULTAD} to cem_app;
"""


FIJAR_CAMBIOS = (
    (
        "    perform nucleo.exigir_carga_documentos(c, actor, origen_pedido, null);\n",
        "    -- La versión de un comprobante que subió en esta transacción quien vio la cuenta\n"
        "    -- de esa orden (nueva_version_de_comprobante, 0068) no pide la facultad de carga.\n"
        "    if actor is distinct from plataforma.persona_actual() or not exists (\n"
        "         select 1 from nucleo.version_documento x\n"
        "           join nucleo.documento d\n"
        "             on d.consorcio_id = x.consorcio_id and d.id = x.documento_id\n"
        "          where x.consorcio_id = c and x.id = version and x.subida_por = actor\n"
        "            and x.estado = 'en_cuarentena' and x.version_objeto is null\n"
        f"            and d.orden_comprobante is not null and d.categoria = '{CATEGORIA}'\n"
        f"            and {ABRIO}(c, d.orden_comprobante, actor)) then\n"
        "      perform nucleo.exigir_carga_documentos(c, actor, origen_pedido, null);\n"
        "    end if;\n",
    ),
)


def _por_aclarar(con_registro: bool) -> str:
    """`0067` sin `con_registro`; con él, la huella y el último comprobante propio de la orden."""
    columnas = ", huella text, comprobante_id uuid, comprobante_estado text" if con_registro else ""
    valores = ", encode(v.huella, 'hex'), cp.id, cp.estado" if con_registro else ""
    comprobante = (
        """
        left join lateral (
          select d.id, (select x.estado from nucleo.version_documento x
                         where x.consorcio_id = d.consorcio_id and x.documento_id = d.id
                         order by x.subida_en desc, x.id desc limit 1) as estado
            from nucleo.documento d
           where d.consorcio_id = e.consorcio_id and d.orden_comprobante = o.id
             and d.estado = 'vigente' and d.cargado_por = actor
           order by d.cargado_en desc, d.id desc limit 1) cp on true"""
        if con_registro
        else ""
    )
    return f"""
drop function {POR_ACLARAR};
-- Las órdenes que esperan que quien pide declare si pagó: vio la cuenta en el acto de la ejecución
-- manual abierta y ya no tiene egresos.ejecutar_pago en ese consorcio. En cualquier consorcio: sin
-- la facultad, ya no tiene ámbito en él (0067). Sin la cuenta.
create function {POR_ACLARAR.split("(")[0]}(actor uuid, origen_pedido text, sesion uuid)
  returns table (consorcio_id uuid, consorcio text, orden_id uuid, version integer,
                 concepto text, importe_centavos bigint, moneda text,
                 cuenta_vista_en timestamptz{columnas})
  language plpgsql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
  begin
    if origen_pedido is distinct from 'api' then
      raise exception 'origen inválido' using errcode = 'P0001', hint = 'origen_invalido';
    end if;
    if actor is null or actor is distinct from plataforma.persona_actual() then
      raise exception 'la persona no es la de la petición' using errcode = '42501';
    end if;
    if not exists (select 1 from nucleo.sesion x
                    where x.id = sesion and x.persona_id = actor and x.revocada_en is null
                      and x.vence_en > now()) then
      raise exception 'la sesión no es válida' using errcode = 'P0001', hint = 'sesion_invalida';
    end if;
    -- Su última apertura de cada ejecución; ya no figura si declaró después de ella.
    return query
      select k.id, k.nombre, o.id, e.version, v.concepto, e.importe_centavos, e.moneda,
             u.abierta_en{valores}
        from nucleo.ejecucion_orden_pago e
        join nucleo.orden_pago o on o.consorcio_id = e.consorcio_id and o.id = e.orden_id
        join nucleo.orden_pago_version v on v.orden_id = e.orden_id and v.numero = e.version
        join nucleo.consorcio k on k.id = e.consorcio_id and k.eliminado_en is null
        cross join lateral (
          select a.abierta_en, a.paso, a.identidad_abrio
            from nucleo.apertura_cuenta_pago_manual a
           where a.consorcio_id = e.consorcio_id and a.ejecucion_id = e.id
             and a.identidad_abrio = nucleo.identidad_humana_de(e.consorcio_id, actor)
           order by a.paso desc limit 1) u{comprobante}
       where e.modalidad = 'manual' and e.version = o.version_vigente
         and {ABRIO}(e.consorcio_id, o.id, actor)
         -- Sin un pago vigente: si otro ya lo registró, no hay nada que declarar.
         and (select x.id is null or nucleo.pago_manual_observado(x.id)
                from nucleo.ultimo_pago_manual(e.id) x)
         and not nucleo.tiene_facultad_vigente(e.consorcio_id, actor, 'egresos.ejecutar_pago',
                                               nucleo.hoy_del_consorcio(e.consorcio_id))
         and not exists (select 1 from nucleo.declaracion_pago_no_realizado d
                          where d.ejecucion_id = e.id and d.identidad_declaro = u.identidad_abrio
                            and d.paso > u.paso)
       order by u.abierta_en;
  end
  $$;
revoke all on function {POR_ACLARAR} from public;
grant execute on function {POR_ACLARAR} to cem_app;
"""


GUARDA_DOWNGRADE = """
-- Hasta el fin del downgrade nadie registra: la guarda vale para lo que se borra.
lock table nucleo.pago_manual in access exclusive mode;
do $$
begin
  if exists (select 1 from nucleo.pago_manual where sin_facultad) then
    raise exception '0068 no baja con pagos registrados sin la facultad'
      using errcode = 'P0001', hint = 'downgrade_con_datos';
  end if;
end
$$;
"""


def _reemplazar(funcion: str, cambios, *, al_reves: bool = False) -> None:
    definicion = (
        op.get_bind()
        .execute(text("select pg_get_functiondef(cast(:f as regprocedure))"), {"f": funcion})
        .scalar_one()
    )
    for antes, despues in cambios:
        if al_reves:
            antes, despues = despues, antes
        if definicion.count(antes) != 1:
            raise RuntimeError(f"0068: {funcion} no tiene exactamente una vez {antes!r}")
        definicion = definicion.replace(antes, despues)
    op.execute(definicion)


def upgrade() -> None:
    _reemplazar(CREAR, CREAR_CAMBIOS)
    op.execute(COLUMNA)
    _reemplazar(REGISTRO, REGISTRO_CAMBIOS)
    op.execute(VERSION_SQL)
    _reemplazar(FIJAR, FIJAR_CAMBIOS)
    op.execute(_por_aclarar(con_registro=True))


def downgrade() -> None:
    op.execute(GUARDA_DOWNGRADE)
    op.execute(_por_aclarar(con_registro=False))
    _reemplazar(FIJAR, FIJAR_CAMBIOS, al_reves=True)
    op.execute(f"drop function {VERSION_SIN_FACULTAD}")
    _reemplazar(REGISTRO, REGISTRO_CAMBIOS, al_reves=True)
    op.execute("alter table nucleo.pago_manual drop column sin_facultad")
    _reemplazar(CREAR, CREAR_CAMBIOS, al_reves=True)
