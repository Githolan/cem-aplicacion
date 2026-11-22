"""Bandeja y correo del Consejo (RF40; diseño P0 S6), con el aviso de vencimiento de las
solicitudes formales (RF49, §5 del diseño: «Aviso de vencimiento por correo», fuera de aquel corte).

La bandeja es el inicio (decisión 9 del 18-11-2026): cada pendiente se lee con la lectura que ya
existe para su pantalla. Lo que falta es el correo, y sale por el aviso diario que ya existe
(`pagos.avisar_pendientes`, `0098`): el mismo tope por persona, la misma ventana del planificador y
el mismo `correo.solicitar`, en la misma transacción. Este corte no crea tablas.

**`nucleo.pendientes_consejo_para_avisar()`**: para el trabajo diario de avisos (sin persona ni
consorcio en el ámbito, como `pendientes_para_avisar`), una fila por persona y edificio con lo que
le toca a esa persona, y nulo en lo que no le toca:

- `importaciones`: cuántas liquidaciones del administrador esperan confirmación (`para_confirmar`),
  para quien tiene `liquidacion.importar`;
- `solicitudes`: el estado y el vencimiento de cada solicitud formal enviada o vencida, para quien
  tiene `solicitud_formal.firmar` (el Consejo; quien sigue la solicitud y registra la respuesta). La
  regla de «por vencer» y de «venció hace poco» la aplica el trabajo con la misma función que la API
  (`app.consorcios.avisos_consejo`), con el día del consorcio que devuelve esta función;
- `reclamos`: cuántos reclamos sobre la liquidación siguen `presentado`, para quien tiene
  `reclamos.atender`;
- `reparaciones`: cuántos pedidos de reparación esperan responsable (`pedida`), para quien tiene
  `reparaciones.gestionar`.

Cada facultad cuenta solo en los modos de su acción (`MODOS`, copia de las políticas que compara
una prueba) y con el modo vigente hoy en el edificio. No devuelve textos, unidades, personas,
importes ni cuentas: el correo no los lleva.

Downgrade: sin datos que perder (solo una función).

Revisión: 0126
Anterior: 0125
"""

from alembic import op

revision = "0126"
down_revision = "0125"
branch_labels = None
depends_on = None

MODOS = {
    "liquidacion.importar": ("consejo", "administrador_aumentado", "autogestion"),
    "solicitud_formal.firmar": ("consejo", "administrador_aumentado"),
    "reclamos_liquidacion.atender": ("consejo", "administrador_aumentado", "autogestion"),
    "reparaciones.gestionar": ("consejo",),
}
"""Los modos de cada acción (`politicas_datos/*.json`); la facultad de cada una lleva su nombre,
salvo la de los reclamos (`reclamos.atender`)."""
FACULTADES = {
    "liquidacion.importar": "liquidacion.importar",
    "solicitud_formal.firmar": "solicitud_formal.firmar",
    "reclamos_liquidacion.atender": "reclamos.atender",
    "reparaciones.gestionar": "reparaciones.gestionar",
}
"""La facultad de cada acción, como en sus políticas."""
SOLICITUDES_QUE_CUENTAN = ("enviada", "vencida")
FUNCION = "nucleo.pendientes_consejo_para_avisar()"


def _lista(valores: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in valores)


def _puede(accion: str) -> str:
    return (
        f"(x.modo in ({_lista(MODOS[accion])})"
        f" and nucleo.tiene_facultad_vigente(x.c, x.persona, '{FACULTADES[accion]}', x.hoy))"
    )


FUNCIONES = f"""
-- Solo el trabajo de avisos: sin persona y sin consorcio en el ámbito. Recorre todos los
-- edificios, como `pendientes_para_avisar` (`0098`).
create function {FUNCION}
  returns table (consorcio_id uuid, consorcio text, persona_id uuid, correo text, hoy date,
                 importaciones integer, solicitudes jsonb, reclamos integer, reparaciones integer)
  language plpgsql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    x record;
    importa boolean;
    firma boolean;
    atiende boolean;
    gestiona boolean;
  begin
    if plataforma.persona_actual() is not null or plataforma.consorcio_actual() is not null then
      raise exception 'solo el trabajo de avisos lee los pendientes de todos'
        using errcode = '42501';
    end if;
    for x in
      select co.id as c, co.nombre, p.id as persona, p.correo,
             nucleo.hoy_del_consorcio(co.id) as hoy,
             (select mo.modo::text from nucleo.modo_operacion mo
               where mo.consorcio_id = co.id
                 and mo.vigente_desde <= nucleo.hoy_del_consorcio(co.id)
                 and (mo.vigente_hasta is null
                      or mo.vigente_hasta > nucleo.hoy_del_consorcio(co.id))) as modo
        from nucleo.consorcio co
        join nucleo.mandato m on m.consorcio_id = co.id
        join nucleo.persona p on p.id = m.persona_id and p.eliminado_en is null
       where co.eliminado_en is null
       group by co.id, co.nombre, p.id, p.correo
       order by co.id, p.id
    loop
      continue when x.modo is null;
      importa := {_puede("liquidacion.importar")};
      firma := {_puede("solicitud_formal.firmar")};
      atiende := {_puede("reclamos_liquidacion.atender")};
      gestiona := {_puede("reparaciones.gestionar")};
      continue when not (importa or firma or atiende or gestiona);
      return query
        select x.c, x.nombre, x.persona, x.correo, x.hoy,
               case when importa then
                 (select count(*)::integer from nucleo.importacion_liquidacion i
                   where i.consorcio_id = x.c and i.estado = 'para_confirmar') end,
               case when firma then
                 (select coalesce(jsonb_agg(jsonb_build_object('estado', s.estado,
                                                               'vence_el', s.vence_el)
                                            order by s.vence_el, s.solicitud_id), '[]'::jsonb)
                    from (select n.solicitud_id, n.vence_el,
                                 nucleo.estado_solicitud_formal(x.c, n.solicitud_id) as estado
                            from nucleo.solicitud_formal_envio n
                           where n.consorcio_id = x.c) s
                   where s.estado in ({_lista(SOLICITUDES_QUE_CUENTAN)})) end,
               case when atiende then
                 (select count(*)::integer from nucleo.reclamo_liquidacion r
                   where r.consorcio_id = x.c
                     and not exists (select 1 from nucleo.reclamo_liquidacion_estado e
                                      where e.consorcio_id = r.consorcio_id
                                        and e.reclamo_id = r.id and e.estado <> 'presentado'))
               end,
               case when gestiona then
                 (select count(*)::integer from nucleo.reparacion r
                   where r.consorcio_id = x.c
                     and nucleo.estado_de_reparacion(x.c, r.id) = 'pedida') end;
    end loop;
  end
  $$;
"""


def upgrade() -> None:
    op.execute(FUNCIONES)
    op.execute(f"revoke all on function {FUNCION} from public")
    op.execute(f"grant execute on function {FUNCION} to cem_app")


def downgrade() -> None:
    op.execute(f"drop function {FUNCION}")
