"""H-B01: la exportación de soporte lleva lo económico (RF36, «paquete íntegro»).

La auditoría de CEM encontró que el paquete de `0038` salía sin proveedores, órdenes de pago, pagos
ni liquidaciones, y sin decirlo. Esta migración suma esas cuatro partes con el patrón de `0038`:
`security definer`, `search_path` fijo, el consorcio del contexto, una exportación registrada en
esta transacción y sin armar, `revoke … from public`, `grant` solo a `cem_soporte_exportacion`, y
filas `(entidad, fila)` armadas columna por columna, en orden estable.

- **`nucleo.exportar_proveedores`:** cada proveedor, todas sus versiones y sus contratos con las
  confirmaciones y las bajas.
- **`nucleo.exportar_ordenes_pago`:** cada orden (también los borradores), todas sus versiones,
  las aprobaciones, las transiciones de estado, los umbrales y la cantidad de aprobaciones
  configurada. Las transiciones salen de `auditoria.eventos` por la entidad `orden_pago`, solo
  fecha, acción, estado y versión: `auditoria.proyeccion` no cambia, porque eso cambiaría la
  auditoría visible (H-B02, otra decisión).
- **`nucleo.exportar_pagos`:** la ejecución con sus resultados, cada acto en que alguien recibió la
  cuenta, los comprobantes atados, los pagos manuales, sus conciliaciones y las declaraciones de
  no pagado con sus confirmaciones.
- **`nucleo.exportar_liquidaciones`:** cada liquidación con sus gastos y los gastos quitados.

**La cuenta.** Regla del propietario: la cuenta completa se ve solo en el acto de ejecución
manual. En el paquete va `cuenta_ultimos_digitos`, los mismos 4 dígitos que recibe quien prepara
(`0055`, `cem_contracts.egresos`), nunca `cuenta_destino`. Tampoco van:
- la huella de la versión del proveedor ni la de la orden o el contrato, que se calculan sobre la
  cuenta (`0052`: la sal las protege, pero las de antes de `0052` no la tienen);
- la clave de idempotencia de la ejecución, que es la que identifica el pedido ante el riel;
- una cuenta escrita a mano en un texto libre: `nucleo.texto_sin_cuenta` la reemplaza con la
  definición de `0060`. Ese disparador cubre los motivos, no el concepto, el alcance, el contacto
  ni la referencia, y la historia no se puede corregir. Solo ve una cuenta de 22 dígitos: un alias
  escrito a mano en un texto libre no se puede detectar.

**Las personas.** Ningún identificador de persona ni de identidad humana: cruzados entre consorcios
identifican a la misma cuenta. Va el nombre, con `nucleo.nombre_para_exportar`, que junta los dos
criterios que ya usa `0038`: el de `0025` para quien tiene un vínculo y el del mandato para quien
lo ejerció, cuyo nombre el paquete ya lleva en `mandatos/mandato.json`. Si no entra en ninguno, o
se suprimió, va nulo.

**Capacidad.** Que lo económico salga no puede dar acceso a quien no lo tenía: además de las cinco
facultades de lectura, `nucleo.capacidad_para_exportar` exige, general, al menos una facultad
económica del paquete estándar del tipo de su mandato. Se lee del catálogo
(`nucleo.facultad.economica` y `nucleo.paquete_facultad`): hoy `egresos.preparar_orden` o
`egresos.ejecutar_pago` para la administración, y `egresos.aprobar_orden` o
`fondo_reserva.autorizar` para el Consejo. Falla cerrada: sin ella `registrar_exportacion` niega
con `sin_capacidad` sin escribir nada. Las verificadas se suman a `facultades_verificadas` de la
constancia.

Downgrade: quita las cuatro funciones y las auxiliares y vuelve la capacidad y el registro al texto
de `0038`. Las constancias quedan: `facultades_verificadas` es texto y `0038` las admite.

Revisión: 0069
Anterior: 0068
"""

from alembic import op

revision = "0069"
down_revision = "0068"
branch_labels = None
depends_on = None

ROL = "cem_soporte_exportacion"
FACULTADES = ("padron.ver", "documentos.ver", "auditoria.ver", "mandatos.ver", "decisiones.ver")
"""Las de `0038`: se copian porque una migración no cambia después de aplicada."""
TIPOS = ("administrador", "miembro_consejo")
DIGITOS_VISIBLES = 4
"""`cem_contracts.egresos.DIGITOS_VISIBLES_CUENTA`: lo que recibe quien prepara."""
# Los de `0060`: espacio, punto, barra, espacio duro (U+00A0), finos (U+202F y U+2009) y guion.
SEPARADORES = " ./   -"
CUENTA_OMITIDA = "[cuenta omitida]"

EXIGIR_CONSORCIO = """\
    if c is null or c is distinct from plataforma.consorcio_actual() then
      raise exception 'el consorcio no es el del contexto' using errcode = '42501';
    end if;
"""
EXIGIR_PEDIDO = """\
    if pedido is null or not exists (
         select 1 from plataforma.exportacion x
          where x.id = pedido and x.consorcio_id = c and x.armada_en is null
            and x.creada_en = now()) then
      raise exception 'sin una exportación registrada en esta transacción y sin armar'
        using errcode = '42501';
    end if;
"""


def _lista(valores: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in valores)


def _nombre(columna: str) -> str:
    return f"nucleo.nombre_para_exportar(c, {columna}, hoy)"


def _texto(columna: str) -> str:
    return f"nucleo.texto_sin_cuenta({columna})"


def _cuenta(columna: str) -> str:
    return f"right({columna}, {DIGITOS_VISIBLES})"


AUXILIARES = f"""
-- Un texto libre sin las cuentas que tenga: la misma definición de `nucleo.texto_con_cuenta`.
create function nucleo.texto_sin_cuenta(t text) returns text
  language sql immutable strict parallel safe
  set search_path = pg_catalog, pg_temp
  as $$
    select regexp_replace(t, '(?<![0-9])[0-9](?:[{SEPARADORES}]?[0-9]){{21}}(?![0-9])',
                          '{CUENTA_OMITIDA}', 'g')
  $$;
revoke all on function nucleo.texto_sin_cuenta(text) from public;

-- El nombre de quien hizo un acto económico: el de RF35-B de `exportar_auditoria` (el criterio de
-- `0025`) o, si ejerció un mandato en el consorcio, el que ya lleva `exportar_mandatos`.
create function nucleo.nombre_para_exportar(c uuid, quien uuid, hoy date)
  returns text
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select pe.nombre from nucleo.persona pe
     where pe.id = quien and pe.eliminado_en is null
       and (exists (select 1 from nucleo.mandato m
                     where m.consorcio_id = c and m.persona_id = pe.id)
            or exists (
              select 1 from nucleo.vinculo v
                join nucleo.unidad u on u.consorcio_id = v.consorcio_id and u.id = v.unidad_id
               where v.consorcio_id = c and v.persona_id = pe.id
                 and (v.vigente_hasta is null or v.vigente_hasta > hoy)
                 and u.eliminado_en is null
                 and not (v.nombre_declarado is not null
                          and v.confirmado_en is null
                          and not exists (
                            select 1 from auditoria.eventos ea
                              join nucleo.invitacion i
                                on i.id = ea.entidad_id and i.consorcio_id = ea.consorcio_id
                             where ea.consorcio_id = v.consorcio_id
                               and ea.accion = 'identidad.invitacion.aceptar'
                               and ea.entidad = 'invitacion'
                               and ea.despues ->> 'vinculo_id' = v.id::text
                               and i.aceptada_en is not null))))
  $$;
revoke all on function nucleo.nombre_para_exportar(uuid, uuid, date) from public;

-- Las facultades económicas generales del mandato que están en el paquete estándar de su tipo.
create function nucleo.facultades_economicas_para_exportar(c uuid, mandato_pedido uuid)
  returns text[]
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select coalesce(array_agg(distinct f.facultad order by f.facultad), array[]::text[])
      from nucleo.mandato m
      join nucleo.facultad_otorgada f on f.consorcio_id = m.consorcio_id and f.mandato_id = m.id
      join nucleo.facultad k on k.codigo = f.facultad
      join nucleo.paquete_facultad p on p.tipo = m.tipo and p.facultad = f.facultad
     where m.consorcio_id = c and m.id = mandato_pedido and f.recurso is null and k.economica
  $$;
revoke all on function nucleo.facultades_economicas_para_exportar(uuid, uuid) from public;
"""

FUNCIONES: dict[str, str] = {
    "exportar_proveedores": f"""
    hoy := nucleo.hoy_del_consorcio(c);
    -- Como `proveedores_lectura` y `proveedor_versiones_lectura` a quien prepara: los últimos
    -- dígitos, sin la cuenta ni la huella, y el nombre en lugar del identificador.
    return query
      select 'proveedor'::text,
             jsonb_build_object('id', p.id, 'cuit', p.cuit, 'creado_en', p.creado_en,
                                'alta_por_nombre', {_nombre("p.alta_por")})
        from nucleo.proveedor p
       where p.consorcio_id = c
       order by p.creado_en, p.id;
    return query
      select 'proveedor_version'::text,
             jsonb_build_object('proveedor_id', v.proveedor_id, 'version', v.numero,
                                'razon_social', {_texto("v.razon_social")},
                                'rubro', {_texto("v.rubro")},
                                'contacto', {_texto("v.contacto")}, 'estado', v.estado,
                                'documento_id', v.documento_id,
                                'cuenta_ultimos_digitos', {_cuenta("v.cuenta_destino")},
                                'preparada_por_nombre', {_nombre("v.preparada_por")},
                                'creada_en', v.creada_en)
        from nucleo.proveedor_version v
       where v.consorcio_id = c
       order by v.proveedor_id, v.numero;
    return query
      select 'contrato'::text,
             jsonb_build_object('id', k.id, 'proveedor_id', k.proveedor_id, 'tipo', k.tipo,
                                'alcance', {_texto("k.alcance")},
                                'importe_maximo_centavos', k.importe_maximo_centavos,
                                'moneda', k.moneda, 'vigente_desde', k.vigente_desde,
                                'vigente_hasta', k.vigente_hasta, 'documento_id', k.documento_id,
                                'decision_id', k.decision_id,
                                'registrado_por_nombre', {_nombre("k.registrado_por")},
                                'creado_en', k.creado_en)
        from nucleo.contrato_proveedor k
       where k.consorcio_id = c
       order by k.creado_en, k.id;
    return query
      select 'contrato_confirmacion'::text,
             jsonb_build_object('contrato_id', f.contrato_id, 'proveedor_id', f.proveedor_id,
                                'proveedor_version', f.proveedor_version,
                                'confirmada_por_nombre', {_nombre("f.persona_id")},
                                'creada_en', f.creada_en)
        from nucleo.contrato_proveedor_confirmacion f
       where f.consorcio_id = c
       order by f.contrato_id, f.proveedor_version;
    return query
      select 'contrato_baja'::text,
             jsonb_build_object('contrato_id', b.contrato_id,
                                'baja_por_nombre', {_nombre("b.persona_id")},
                                'creada_en', b.creada_en)
        from nucleo.contrato_proveedor_baja b
       where b.consorcio_id = c
       order by b.contrato_id;
""",
    "exportar_ordenes_pago": f"""
    hoy := nucleo.hoy_del_consorcio(c);
    return query
      select 'orden'::text,
             jsonb_build_object('id', o.id, 'estado', o.estado,
                                'version_vigente', o.version_vigente,
                                'preparada_por_nombre', {_nombre("o.preparada_por")},
                                'creada_en', o.creada_en, 'actualizada_en', o.actualizada_en,
                                'aprobada_en', o.aprobada_en,
                                'motivo_rechazo', {_texto("o.motivo_rechazo")})
        from nucleo.orden_pago o
       where o.consorcio_id = c
       order by o.creada_en, o.id;
    -- Cada versión con el proveedor exacto que fija y, de su cuenta, los últimos dígitos. Sin la
    -- huella: se calcula sobre la del proveedor, que se calcula sobre la cuenta.
    return query
      select 'orden_version'::text,
             jsonb_build_object('orden_id', v.orden_id, 'numero', v.numero,
                                'importe_centavos', v.importe_centavos, 'moneda', v.moneda,
                                'concepto', {_texto("v.concepto")},
                                'proveedor_id', v.proveedor_id,
                                'proveedor_version', v.proveedor_version,
                                'razon_social', {_texto("pv.razon_social")},
                                'cuenta_ultimos_digitos', {_cuenta("pv.cuenta_destino")},
                                'condicion_beneficiario', v.condicion_beneficiario,
                                'comprobante', {_texto("v.comprobante")},
                                'usa_fondo_reserva', v.usa_fondo_reserva,
                                'aprobaciones_requeridas', v.aprobaciones_requeridas,
                                'franja', v.franja, 'umbral_id', v.umbral_id,
                                'contrato_id', v.contrato_id,
                                'alerta_fraccionamiento', v.alerta_fraccionamiento,
                                'preparada_por_nombre', {_nombre("v.preparada_por")},
                                'creada_en', v.creada_en)
        from nucleo.orden_pago_version v
        join nucleo.proveedor_version pv
          on pv.consorcio_id = v.consorcio_id and pv.proveedor_id = v.proveedor_id
         and pv.numero = v.proveedor_version
       where v.consorcio_id = c
       order by v.orden_id, v.numero;
    return query
      select 'orden_aprobacion'::text,
             jsonb_build_object('id', a.id, 'orden_id', a.orden_id, 'version', a.version,
                                'aprobada_por_nombre', {_nombre("a.persona_id")},
                                'creada_en', a.creada_en)
        from nucleo.orden_pago_aprobacion a
       where a.consorcio_id = c
       order by a.orden_id, a.version, a.creada_en, a.id;
    -- Los cambios de estado, de la cadena de la auditoría y sin pasar por la proyección: solo el
    -- estado y la versión de cada evento, nunca `antes` ni `despues` enteros (traen la huella).
    return query
      select 'orden_transicion'::text,
             jsonb_build_object('orden_id', g.entidad_id, 'fecha', g.creado_en,
                                'accion', g.accion,
                                'origen', case g.origen when 'api' then 'aplicacion'
                                                        else 'sistema' end,
                                'desde', g.antes ->> 'estado', 'hacia', g.despues ->> 'estado',
                                'version', g.despues -> 'version',
                                'actor_nombre',
                                case when g.origen = 'api'
                                     then {_nombre("g.actor_persona_id")} end)
        from auditoria.eventos g
       where g.consorcio_id = c and g.entidad = 'orden_pago' and g.origen <> 'soporte'
         and g.despues ? 'estado'
       order by g.creado_en, g.secuencia;
    return query
      select 'umbral'::text,
             jsonb_build_object('id', u.id, 'importe_centavos', u.importe_centavos,
                                'moneda', u.moneda, 'vigente_desde', u.vigente_desde,
                                'decision_id', u.decision_id)
        from nucleo.umbral_orden_pago u
       where u.consorcio_id = c
       order by u.moneda, u.vigente_desde, u.id;
    return query
      select 'aprobaciones_configuradas'::text,
             jsonb_build_object('id', x.id, 'cantidad', x.cantidad,
                                'vigente_desde', x.vigente_desde, 'decision_id', x.decision_id)
        from nucleo.aprobaciones_orden_pago x
       where x.consorcio_id = c
       order by x.vigente_desde, x.id;
""",
    "exportar_pagos": f"""
    hoy := nucleo.hoy_del_consorcio(c);
    -- Sin la cuenta ni la clave de idempotencia. La completa se ve solo en el acto de ejecución
    -- manual (`cuenta_para_pago_manual`).
    return query
      select 'ejecucion'::text,
             jsonb_build_object('id', e.id, 'orden_id', e.orden_id, 'version', e.version,
                                'modalidad', e.modalidad, 'proveedor_id', e.proveedor_id,
                                'proveedor_version', e.proveedor_version,
                                'cuenta_ultimos_digitos', {_cuenta("e.cuenta_destino")},
                                'importe_centavos', e.importe_centavos, 'moneda', e.moneda,
                                'creada_en', e.creada_en)
        from nucleo.ejecucion_orden_pago e
       where e.consorcio_id = c
       order by e.creada_en, e.id;
    return query
      select 'ejecucion_resultado'::text,
             jsonb_build_object('id', r.id, 'ejecucion_id', r.ejecucion_id,
                                'resultado', r.resultado, 'origen', r.origen,
                                'referencia', {_texto("r.referencia")},
                                'motivo', {_texto("r.motivo")},
                                'registrado_en', r.registrado_en)
        from nucleo.resultado_ejecucion_orden_pago r
       where r.consorcio_id = c
       order by r.registrado_en, r.id;
    -- Quién recibió la cuenta y cuándo: sin el acto, una declaración de no pagado no se entiende.
    return query
      select 'apertura_cuenta'::text,
             jsonb_build_object('id', a.id, 'ejecucion_id', a.ejecucion_id,
                                'abierta_por_nombre', {_nombre("a.abierta_por")},
                                'abierta_en', a.abierta_en)
        from nucleo.apertura_cuenta_pago_manual a
       where a.consorcio_id = c
       order by a.paso;
    return query
      select 'comprobante'::text,
             jsonb_build_object('documento_id', k.documento_id, 'ejecucion_id', k.ejecucion_id,
                                'atado_en', k.atado_en)
        from nucleo.comprobante_pago_manual k
       where k.consorcio_id = c
       order by k.atado_en, k.documento_id;
    return query
      select 'pago'::text,
             jsonb_build_object('id', p.id, 'ejecucion_id', p.ejecucion_id,
                                'corrige_a', p.corrige_a,
                                'numero_operacion', {_texto("p.numero_operacion")},
                                'fecha_operacion', p.fecha_operacion,
                                'importe_centavos', p.importe_centavos, 'moneda', p.moneda,
                                'documento_id', p.documento_id,
                                'version_documento_id', p.version_documento_id,
                                'registrado_por_nombre', {_nombre("p.registrado_por")},
                                'registrado_en', p.registrado_en,
                                'fuera_de_termino', p.fuera_de_termino,
                                'sin_facultad', p.sin_facultad)
        from nucleo.pago_manual p
       where p.consorcio_id = c
       order by p.paso;
    return query
      select 'conciliacion'::text,
             jsonb_build_object('id', k.id, 'pago_id', k.pago_id, 'resultado', k.resultado,
                                'motivo', {_texto("k.motivo")},
                                'conciliado_por_nombre', {_nombre("k.conciliado_por")},
                                'conciliado_en', k.conciliado_en)
        from nucleo.conciliacion_pago_manual k
       where k.consorcio_id = c
       order by k.conciliado_en, k.id;
    return query
      select 'declaracion_no_pagado'::text,
             jsonb_build_object('id', d.id, 'ejecucion_id', d.ejecucion_id,
                                'motivo', {_texto("d.motivo")},
                                'declarado_por_nombre', {_nombre("d.declarado_por")},
                                'declarado_en', d.declarado_en, 'sin_facultad', d.sin_facultad)
        from nucleo.declaracion_pago_no_realizado d
       where d.consorcio_id = c
       order by d.paso;
    return query
      select 'confirmacion_no_pagado'::text,
             jsonb_build_object('id', f.id, 'declaracion_id', f.declaracion_id,
                                'confirmado_por_nombre', {_nombre("f.confirmado_por")},
                                'confirmado_en', f.confirmado_en)
        from nucleo.confirmacion_pago_no_realizado f
       where f.consorcio_id = c
       order by f.confirmado_en, f.id;
""",
    "exportar_liquidaciones": f"""
    hoy := nucleo.hoy_del_consorcio(c);
    return query
      select 'liquidacion'::text,
             jsonb_build_object('id', l.id, 'periodo', l.periodo, 'version', l.version,
                                'estado', l.estado,
                                'preparada_por_nombre', {_nombre("l.preparada_por")},
                                'creada_en', l.creada_en, 'descartada_en', l.descartada_en,
                                'descartada_por_nombre', {_nombre("l.descartada_por")},
                                'motivo_descarte', {_texto("l.motivo_descarte")})
        from nucleo.liquidacion l
       where l.consorcio_id = c
       order by l.periodo, l.version, l.id;
    return query
      select 'gasto'::text,
             jsonb_build_object('id', g.id, 'liquidacion_id', g.liquidacion_id,
                                'orden_id', g.orden_id, 'ejecucion_id', g.ejecucion_id,
                                'importe_centavos', g.importe_centavos, 'moneda', g.moneda,
                                'esquema_id', g.esquema_id,
                                'agregado_por_nombre', {_nombre("g.agregado_por")},
                                'agregado_en', g.agregado_en)
        from nucleo.liquidacion_gasto g
       where g.consorcio_id = c
       order by g.liquidacion_id, g.agregado_en, g.id;
    return query
      select 'gasto_quitado'::text,
             jsonb_build_object('gasto_id', q.gasto_id,
                                'quitado_por_nombre', {_nombre("q.quitado_por")},
                                'quitado_en', q.quitado_en)
        from nucleo.liquidacion_gasto_quitado q
       where q.consorcio_id = c
       order by q.quitado_en, q.gasto_id;
""",
}


def _funcion(nombre: str, cuerpo: str) -> str:
    return f"""
create function nucleo.{nombre}(c uuid, pedido uuid)
  returns table (entidad text, fila jsonb)
  language plpgsql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    hoy date;
  begin
{EXIGIR_CONSORCIO}{EXIGIR_PEDIDO}{cuerpo}  end
  $$;
revoke all on function nucleo.{nombre}(uuid, uuid) from public;
grant execute on function nucleo.{nombre}(uuid, uuid) to {ROL};
"""


def _capacidad(economica: bool) -> str:
    """`create or replace` conserva el dueño y los permisos: nadie más la ejecuta."""
    exige = (
        "\n       and cardinality(nucleo.facultades_economicas_para_exportar(c, x.id)) > 0"
        if economica
        else ""
    )
    return f"""
create or replace function nucleo.capacidad_para_exportar(c uuid, persona uuid, fecha date)
  returns uuid
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select x.id from nucleo.mandato x
     where x.consorcio_id = c and x.persona_id = persona
       and x.tipo in ({_lista(TIPOS)})
       and nucleo.mandato_vigente(x.id, fecha)
       and (select count(distinct f.facultad) from nucleo.facultad_otorgada f
             where f.consorcio_id = c and f.mandato_id = x.id and f.recurso is null
               and f.facultad in ({_lista(FACULTADES)})) = {len(FACULTADES)}{exige}
     order by x.id
     limit 1
  $$;
"""


def _registrar(economica: bool) -> str:
    falta = (
        "' Consejo con las cinco facultades de lectura generales y una facultad económica"
        " general de'\n                      ' su paquete'"
        if economica
        else "' Consejo con las cinco facultades de lectura generales'"
    )
    verificadas = f"array[{_lista(FACULTADES)}]"
    if economica:
        verificadas += " || nucleo.facultades_economicas_para_exportar(c, m)"
    return f"""
create or replace function nucleo.registrar_exportacion(c uuid, solicitud text,
                                                        destinatario_nombre text,
                                                        destinatario_documento text,
                                                        solicitante uuid, fecha date,
                                                        respaldo text)
  returns table (id uuid, mandato_id uuid, creada_en timestamptz)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    m uuid;
  begin
{EXIGIR_CONSORCIO}    if solicitante is null or fecha is null then
      raise exception 'faltan quien la pidió o la fecha del pedido'
        using errcode = 'P0001', hint = 'pedido_incompleto';
    end if;
    if fecha > nucleo.hoy_del_consorcio(c) then
      raise exception 'la fecha del pedido es posterior a hoy en el consorcio'
        using errcode = 'P0001', hint = 'fecha_futura';
    end if;
    m := nucleo.capacidad_para_exportar(c, solicitante, fecha);
    if m is null then
      raise exception 'quien la pidió no tenía ese día un mandato vigente de administración o de'
                      {falta}
        using errcode = 'P0001', hint = 'sin_capacidad';
    end if;
    return query
      insert into plataforma.exportacion as x (consorcio_id, solicitud_referencia,
                                               destinatario_nombre, destinatario_documento,
                                               solicitante, mandato_id, facultades_verificadas,
                                               fecha_pedido, respaldo_referencia)
      values (c, btrim(solicitud), btrim(destinatario_nombre), btrim(destinatario_documento),
              solicitante, m, {verificadas}, fecha, btrim(respaldo))
      returning x.id, x.mandato_id, x.creada_en;
  end
  $$;
"""


UPGRADE = (
    AUXILIARES
    + "".join(_funcion(n, c) for n, c in FUNCIONES.items())
    + _capacidad(economica=True)
    + _registrar(economica=True)
)

DOWNGRADE = (
    _registrar(economica=False)
    + _capacidad(economica=False)
    + "".join(f"drop function nucleo.{n}(uuid, uuid);\n" for n in FUNCIONES)
    + """
drop function nucleo.facultades_economicas_para_exportar(uuid, uuid);
drop function nucleo.nombre_para_exportar(uuid, uuid, date);
drop function nucleo.texto_sin_cuenta(text);
"""
)


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
