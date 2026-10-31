"""Un cambio de cuenta del proveedor obliga a confirmar de nuevo su contrato (H3; P3 112).

Decisión del propietario del 31-10-2026: un contrato confirmado deja de habilitar la franja
rutinaria en cuanto cambia la cuenta (CBU/CVU) del proveedor, y vuelve a habilitarla recién con
una confirmación nueva hecha sobre la versión del proveedor que tiene la cuenta nueva. Se mantienen:
- quien cargó o cambió la cuenta no confirma (`exigir_no_cargo_la_cuenta_del_proveedor`, `0052`),
  por identidad humana;
- hasta la confirmación nueva, el contrato no respalda nada (falla cerrada);
- la historia no se toca: cada confirmación es una fila nueva, y la vieja queda;
- la confirmación vieja nunca pasa sola a la cuenta nueva.

Cómo:
- cada confirmación guarda la versión del proveedor sobre la que se hizo (`proveedor_version`). Las
  ya existentes toman la versión vigente en el momento en que se confirmaron, que la historia del
  proveedor permite saber sin suponer nada;
- `nucleo.confirmacion_vale` dice si alguna confirmación del contrato sigue valiendo: la cuenta de
  su versión es la de todas las versiones posteriores. Un cambio de cuenta, aunque después vuelva a la
  anterior, la deja sin efecto;
- `contrato_vigente` (lo rutinario y la emisión) lo exige; la lectura devuelve
  `pendiente_reconfirmacion`, la versión vigente del proveedor y los últimos dígitos de su cuenta;
- `confirmar_contrato_proveedor` recibe la versión del proveedor que se vio y se niega si ya no es
  la vigente (`proveedor_cambiado`). Se puede confirmar de nuevo solo si la confirmación anterior ya
  no vale.

Downgrade: se niega si algún contrato tiene más de una confirmación (no entra en la tabla de
`0052`); si no, vuelve a las definiciones de `0052`.

Revisión: 0053
Anterior: 0052
"""

from alembic import op
from sqlalchemy import text

revision = "0053"
down_revision = "0052"
branch_labels = None
depends_on = None

VIGENCIA = "nucleo.contrato_vigente(uuid, uuid, date)"
SIN_JOIN = ("        join nucleo.contrato_proveedor_confirmacion f on f.contrato_id = k.id\n", "")
CON_RESPALDO = (
    "         and nucleo.documento_respalda(d.consorcio_id, d.id)\n",
    "         and nucleo.documento_respalda(d.consorcio_id, d.id)\n"
    "         and nucleo.confirmacion_vale(k.consorcio_id, k.id)\n",
)

TABLA = """
lock table nucleo.contrato_proveedor, nucleo.contrato_proveedor_confirmacion,
  nucleo.proveedor_version in share row exclusive mode;
alter table nucleo.contrato_proveedor_confirmacion
  disable trigger historia_contrato_proveedor_confirmacion;
alter table nucleo.contrato_proveedor_confirmacion
  add column proveedor_id uuid,
  add column proveedor_version integer;
-- La versión del proveedor vigente cuando se confirmó: la última creada hasta ese momento.
update nucleo.contrato_proveedor_confirmacion f
   set proveedor_id = k.proveedor_id,
       proveedor_version = (select max(v.numero) from nucleo.proveedor_version v
                             where v.proveedor_id = k.proveedor_id
                               and v.creada_en <= f.creada_en)
  from nucleo.contrato_proveedor k
 where k.id = f.contrato_id;
alter table nucleo.contrato_proveedor_confirmacion
  alter column proveedor_id set not null,
  alter column proveedor_version set not null,
  drop constraint contrato_proveedor_confirmacion_pkey,
  add primary key (contrato_id, proveedor_version),
  add foreign key (proveedor_id, proveedor_version)
    references nucleo.proveedor_version (proveedor_id, numero);
alter table nucleo.contrato_proveedor add constraint contrato_proveedor_id_proveedor_key
  unique (id, proveedor_id);
alter table nucleo.contrato_proveedor_confirmacion
  add constraint contrato_proveedor_confirmacion_del_proveedor
    foreign key (contrato_id, proveedor_id) references nucleo.contrato_proveedor (id, proveedor_id);
alter table nucleo.contrato_proveedor_confirmacion
  enable trigger historia_contrato_proveedor_confirmacion;

-- Alguna confirmación del contrato sigue valiendo: ninguna versión posterior del proveedor tiene
-- otra cuenta. Un cambio de cuenta la deja sin efecto aunque después vuelva a la anterior.
create function nucleo.confirmacion_vale(c uuid, contrato uuid)
  returns boolean
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select exists (
      select 1 from nucleo.contrato_proveedor_confirmacion f
        join nucleo.proveedor_version vista
          on vista.proveedor_id = f.proveedor_id and vista.numero = f.proveedor_version
       where f.consorcio_id = c and f.contrato_id = contrato
         and not exists (select 1 from nucleo.proveedor_version despues
                          where despues.proveedor_id = f.proveedor_id
                            and despues.numero > f.proveedor_version
                            and despues.cuenta_destino is distinct from vista.cuenta_destino))
  $$;
revoke all on function nucleo.confirmacion_vale(uuid, uuid) from public;
"""

FUNCIONES = """
drop function nucleo.contratos_proveedor_lectura(uuid, text, uuid, text, uuid);
create function nucleo.contratos_proveedor_lectura(contrato uuid, facultad text, actor uuid,
                                                   origen_pedido text, sesion uuid)
  returns table (id uuid, proveedor_id uuid, razon_social text, tipo text, alcance text,
                 importe_maximo_centavos bigint, moneda text, vigente_desde date,
                 vigente_hasta date, documento_id uuid, decision_id uuid, huella text,
                 registrado_por uuid, creado_en timestamptz, confirmado_por uuid,
                 confirmado_en timestamptz, baja_en timestamptz, estado text,
                 proveedor_version integer, cuenta_ultimos_digitos text)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    hoy date;
  begin
    perform nucleo.exigir_lectura_egresos(c, facultad, actor, origen_pedido, sesion);
    hoy := nucleo.hoy_del_consorcio(c);
    return query
      select k.id, k.proveedor_id, pv.razon_social, k.tipo, k.alcance,
             k.importe_maximo_centavos, k.moneda, k.vigente_desde, k.vigente_hasta,
             k.documento_id, k.decision_id, encode(k.huella, 'hex'), k.registrado_por,
             k.creado_en, f.persona_id, f.creada_en, b.creada_en,
             case when b.contrato_id is not null then 'dado_de_baja'
                  when not exists (select 1 from nucleo.documento d
                                    where d.consorcio_id = k.consorcio_id
                                      and d.id = k.documento_id
                                      and nucleo.documento_respalda(d.consorcio_id, d.id))
                    then 'documento_no_vigente'
                  when f.contrato_id is null then 'pendiente_confirmacion'
                  when not nucleo.confirmacion_vale(k.consorcio_id, k.id)
                    then 'pendiente_reconfirmacion'
                  when k.vigente_hasta < hoy then 'vencido'
                  when k.vigente_desde > hoy then 'no_iniciado'
                  else 'vigente' end,
             pv.numero, right(pv.cuenta_destino, 4)
        from nucleo.contrato_proveedor k
        join nucleo.proveedor_version pv
          on pv.consorcio_id = k.consorcio_id and pv.proveedor_id = k.proveedor_id
         and pv.numero = (select max(x.numero) from nucleo.proveedor_version x
                           where x.consorcio_id = k.consorcio_id
                             and x.proveedor_id = k.proveedor_id)
        -- La confirmación más nueva: quién y cuándo se muestran de esa.
        left join lateral (select x.* from nucleo.contrato_proveedor_confirmacion x
                            where x.contrato_id = k.id
                            order by x.proveedor_version desc limit 1) f on true
        left join nucleo.contrato_proveedor_baja b on b.contrato_id = k.id
       where k.consorcio_id = c and (contrato is null or k.id = contrato)
       order by k.creado_en desc, k.id;
  end
  $$;

drop function nucleo.confirmar_contrato_proveedor(uuid, bytea, uuid, text, uuid);
create function nucleo.confirmar_contrato_proveedor(contrato uuid, huella_vista bytea,
                                                    proveedor_version_vista integer,
                                                    actor uuid, origen_pedido text,
                                                    sesion uuid)
  returns void
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    identidad uuid;
    k nucleo.contrato_proveedor;
    pv nucleo.proveedor_version;
  begin
    identidad := nucleo.exigir_acto_economico(c, actor, sesion, 'egresos.aprobar_orden',
                                              origen_pedido);
    select * into k from nucleo.contrato_proveedor x
     where x.consorcio_id = c and x.id = contrato for update;
    if not found then
      raise exception 'el contrato no existe' using errcode = 'P0001', hint = 'no_encontrado';
    end if;
    if exists (select 1 from nucleo.contrato_proveedor_baja b where b.contrato_id = contrato)
    then
      raise exception 'el contrato está dado de baja' using errcode = 'P0001',
        hint = 'contrato_dado_de_baja';
    end if;
    -- FOR SHARE sobre el proveedor: una versión nueva concurrente espera al fin de la
    -- confirmación, y la que se confirma es la que se vio.
    pv := nucleo.proveedor_vigente(c, k.proveedor_id);
    if pv.numero is distinct from proveedor_version_vista then
      raise exception 'el proveedor cambió desde que se vio el contrato' using errcode = 'P0001',
        hint = 'proveedor_cambiado';
    end if;
    if nucleo.confirmacion_vale(c, contrato) then
      raise exception 'el contrato ya está confirmado' using errcode = 'P0001',
        hint = 'contrato_ya_confirmado';
    end if;
    if huella_vista is distinct from k.huella then
      raise exception 'lo que se confirmó no es el contrato registrado' using errcode = 'P0001',
        hint = 'huella_distinta';
    end if;
    if k.identidad_registro = identidad then
      raise exception 'quien registró el contrato no lo confirma' using errcode = 'P0001',
        hint = 'no_es_quien_registro';
    end if;
    perform nucleo.exigir_separacion_humana(c, actor, k.registrado_por, 'no_es_quien_registro');
    perform nucleo.exigir_no_cargo_la_cuenta_del_proveedor(c, k.proveedor_id, actor, identidad);
    insert into nucleo.contrato_proveedor_confirmacion
      (consorcio_id, contrato_id, huella, persona_id, identidad_id, proveedor_id,
       proveedor_version)
    values (c, contrato, huella_vista, actor, identidad, k.proveedor_id, pv.numero);
    perform nucleo.auditar_contrato_proveedor(c, 'consorcio.contrato_proveedor.confirmar',
      contrato, actor, jsonb_build_object('huella', encode(huella_vista, 'hex'),
                                          'proveedor_version', pv.numero));
  end
  $$;
"""

RUTAS = (
    "nucleo.contratos_proveedor_lectura(uuid, text, uuid, text, uuid)",
    "nucleo.confirmar_contrato_proveedor(uuid, bytea, integer, uuid, text, uuid)",
)
RUTAS_0052 = (
    "nucleo.contratos_proveedor_lectura(uuid, text, uuid, text, uuid)",
    "nucleo.confirmar_contrato_proveedor(uuid, bytea, uuid, text, uuid)",
)


def _permisos(rutas: tuple[str, ...]) -> str:
    return "".join(
        f"revoke all on function {f} from public;\ngrant execute on function {f} to cem_app;\n"
        for f in rutas
    )


DOWNGRADE_GUARDA = """
lock table nucleo.contrato_proveedor, nucleo.contrato_proveedor_confirmacion
  in share row exclusive mode;
do $$
begin
  if exists (select 1 from nucleo.contrato_proveedor_confirmacion
              group by contrato_id having count(*) > 1) then
    raise exception 'hay contratos confirmados más de una vez: no entran en la tabla de 0052'
      using errcode = 'P0001', hint = 'downgrade_con_datos';
  end if;
end
$$;
"""

DOWNGRADE_TABLA = """
alter table nucleo.contrato_proveedor_confirmacion
  disable trigger historia_contrato_proveedor_confirmacion;
alter table nucleo.contrato_proveedor_confirmacion
  drop constraint contrato_proveedor_confirmacion_del_proveedor,
  drop constraint contrato_proveedor_confirmacion_pkey,
  drop column proveedor_version,
  drop column proveedor_id,
  add primary key (contrato_id);
alter table nucleo.contrato_proveedor drop constraint contrato_proveedor_id_proveedor_key;
alter table nucleo.contrato_proveedor_confirmacion
  enable trigger historia_contrato_proveedor_confirmacion;
drop function nucleo.confirmacion_vale(uuid, uuid);
"""

# Las definiciones de `0052` tal como las devuelve `pg_get_functiondef`: el downgrade las vuelve a
# crear idénticas (lo compara `test_0053_ida_y_vuelta`).
LECTURA_0052 = """
CREATE OR REPLACE FUNCTION nucleo.contratos_proveedor_lectura(contrato uuid, facultad text, actor
   uuid, origen_pedido text, sesion uuid)
 RETURNS TABLE(id uuid, proveedor_id uuid, razon_social text, tipo text, alcance text,
   importe_maximo_centavos bigint, moneda text, vigente_desde date, vigente_hasta date,
   documento_id uuid, decision_id uuid, huella text, registrado_por uuid, creado_en timestamp with
   time zone, confirmado_por uuid, confirmado_en timestamp with time zone, baja_en timestamp with
   time zone, estado text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog', 'pg_temp'
AS $function$
  declare
    c uuid := plataforma.consorcio_actual();
    hoy date;
  begin
    perform nucleo.exigir_lectura_egresos(c, facultad, actor, origen_pedido, sesion);
    hoy := nucleo.hoy_del_consorcio(c);
    return query
      select k.id, k.proveedor_id, pv.razon_social, k.tipo, k.alcance,
             k.importe_maximo_centavos, k.moneda, k.vigente_desde, k.vigente_hasta,
             k.documento_id, k.decision_id, encode(k.huella, 'hex'), k.registrado_por,
             k.creado_en, f.persona_id, f.creada_en, b.creada_en,
             case when b.contrato_id is not null then 'dado_de_baja'
                  when not exists (select 1 from nucleo.documento d
                                    where d.consorcio_id = k.consorcio_id
                                      and d.id = k.documento_id
                                      and nucleo.documento_respalda(d.consorcio_id, d.id))
                    then 'documento_no_vigente'
                  when f.contrato_id is null then 'pendiente_confirmacion'
                  when k.vigente_hasta < hoy then 'vencido'
                  when k.vigente_desde > hoy then 'no_iniciado'
                  else 'vigente' end
        from nucleo.contrato_proveedor k
        join nucleo.proveedor_version pv
          on pv.consorcio_id = k.consorcio_id and pv.proveedor_id = k.proveedor_id
         and pv.numero = (select max(x.numero) from nucleo.proveedor_version x
                           where x.consorcio_id = k.consorcio_id
                             and x.proveedor_id = k.proveedor_id)
        left join nucleo.contrato_proveedor_confirmacion f on f.contrato_id = k.id
        left join nucleo.contrato_proveedor_baja b on b.contrato_id = k.id
       where k.consorcio_id = c and (contrato is null or k.id = contrato)
       order by k.creado_en desc, k.id;
  end
  $function$;
"""

CONFIRMAR_0052 = """
CREATE OR REPLACE FUNCTION nucleo.confirmar_contrato_proveedor(contrato uuid, huella_vista bytea,
   actor uuid, origen_pedido text, sesion uuid)
 RETURNS void
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog', 'pg_temp'
AS $function$
  declare
    c uuid := plataforma.consorcio_actual();
    identidad uuid;
    k nucleo.contrato_proveedor;
  begin
    identidad := nucleo.exigir_acto_economico(c, actor, sesion, 'egresos.aprobar_orden',
                                              origen_pedido);
    select * into k from nucleo.contrato_proveedor x
     where x.consorcio_id = c and x.id = contrato for update;
    if not found then
      raise exception 'el contrato no existe' using errcode = 'P0001', hint = 'no_encontrado';
    end if;
    if exists (select 1 from nucleo.contrato_proveedor_baja b where b.contrato_id = contrato)
    then
      raise exception 'el contrato está dado de baja' using errcode = 'P0001',
        hint = 'contrato_dado_de_baja';
    end if;
    if exists (select 1 from nucleo.contrato_proveedor_confirmacion f
                where f.contrato_id = contrato) then
      raise exception 'el contrato ya está confirmado' using errcode = 'P0001',
        hint = 'contrato_ya_confirmado';
    end if;
    if huella_vista is distinct from k.huella then
      raise exception 'lo que se confirmó no es el contrato registrado' using errcode = 'P0001',
        hint = 'huella_distinta';
    end if;
    if k.identidad_registro = identidad then
      raise exception 'quien registró el contrato no lo confirma' using errcode = 'P0001',
        hint = 'no_es_quien_registro';
    end if;
    perform nucleo.exigir_separacion_humana(c, actor, k.registrado_por, 'no_es_quien_registro');
    perform nucleo.exigir_no_cargo_la_cuenta_del_proveedor(c, k.proveedor_id, actor, identidad);
    insert into nucleo.contrato_proveedor_confirmacion
      (consorcio_id, contrato_id, huella, persona_id, identidad_id)
    values (c, contrato, huella_vista, actor, identidad);
    perform nucleo.auditar_contrato_proveedor(c, 'consorcio.contrato_proveedor.confirmar',
      contrato, actor, jsonb_build_object('huella', encode(huella_vista, 'hex')));
  end
  $function$;
"""


def _reemplazar(funcion: str, cambios: tuple[tuple[str, str], ...]) -> None:
    definicion = (
        op.get_bind()
        .execute(text("select pg_get_functiondef(cast(:f as regprocedure))"), {"f": funcion})
        .scalar_one()
    )
    for antes, despues in cambios:
        if definicion.count(antes) != 1:
            raise RuntimeError(f"0053: {funcion} no tiene exactamente una vez {antes!r}")
        definicion = definicion.replace(antes, despues)
    op.execute(definicion)


def upgrade() -> None:
    op.execute(TABLA)
    _reemplazar(VIGENCIA, (SIN_JOIN, CON_RESPALDO))
    op.execute(FUNCIONES + _permisos(RUTAS))


def downgrade() -> None:
    op.execute(DOWNGRADE_GUARDA)
    op.execute("".join(f"drop function {f};\n" for f in RUTAS))
    op.execute(LECTURA_0052 + CONFIRMAR_0052 + _permisos(RUTAS_0052))
    # El join de la confirmación vuelve antes del de documento, donde estaba.
    documento = "        join nucleo.documento d"
    _reemplazar(
        VIGENCIA,
        ((CON_RESPALDO[1], CON_RESPALDO[0]), (documento, SIN_JOIN[0] + documento)),
    )
    op.execute(DOWNGRADE_TABLA)
