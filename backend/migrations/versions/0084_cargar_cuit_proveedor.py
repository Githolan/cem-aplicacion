"""Proveedores sin CUIT: cargarlo una sola vez, cuando es nulo (decisión 8 del propietario,
07-11-2026).

`0076` hizo obligatorio el CUIT para toda alta, con `proveedor_cuit_obligatorio` `not valid`: los
proveedores viejos sin CUIT no traban la migración, pero tampoco había cómo completarlo porque
`nucleo.proveedor` solo admitía altas. `cargar_cuit_proveedor` lo carga con `egresos.preparar_orden`
y gesto. `negar_cambio_proveedor` deja pasar solo eso, de nulo a un valor: un CUIT cargado no
cambia.

La huella de la versión incluye el CUIT, así que cargarlo crea una versión nueva con los mismos
datos y la misma cuenta, nunca en silencio. Una orden ya aprobada sobre la versión anterior pasa por
Modificar, como con cualquier otro cambio del proveedor (`0080`).

**No se valida la restricción ni se cuenta nada en producción:** el propietario decidió no tocarla.

Downgrade: vuelve al disparador que niega todo. Los CUIT cargados quedan: son datos del proveedor.

Revisión: 0084
Anterior: 0083
"""

from alembic import op

revision = "0084"
down_revision = "0083"
branch_labels = None
depends_on = None

CARGAR = "nucleo.cargar_cuit_proveedor(uuid, integer, text, uuid, text, uuid)"

SUBIR = f"""
create function nucleo.negar_cambio_proveedor()
  returns trigger
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  begin
    if tg_op = 'DELETE' or old.cuit is not null or new.cuit is null
       or (to_jsonb(old) - 'cuit') is distinct from (to_jsonb(new) - 'cuit') then
      raise exception 'la historia de un proveedor solo se agrega'
        using errcode = 'P0001', hint = 'historia_inmutable';
    end if;
    return new;
  end
  $$;
drop trigger historia_proveedor on nucleo.proveedor;
create trigger historia_proveedor before update or delete on nucleo.proveedor
  for each row execute function nucleo.negar_cambio_proveedor();
revoke all on function nucleo.negar_cambio_proveedor() from public;

create function {CARGAR.split("(")[0]}(proveedor uuid, version_esperada integer, cuit_nuevo text,
                                             actor uuid, origen_pedido text, sesion uuid)
  returns integer
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    identidad uuid;
    cuit_proveedor text;
    v nucleo.proveedor_version;
    h bytea;
  begin
    identidad := nucleo.exigir_acto_economico(c, actor, sesion, 'egresos.preparar_orden',
                                              origen_pedido);
    select p.cuit into cuit_proveedor from nucleo.proveedor p
     where p.consorcio_id = c and p.id = proveedor for update;
    if not found then
      raise exception 'el proveedor no existe' using errcode = 'P0001',
        hint = 'proveedor_inexistente';
    end if;
    if cuit_proveedor is not null then
      raise exception 'ese proveedor ya tiene CUIT y no cambia' using errcode = 'P0001',
        hint = 'cuit_ya_cargado';
    end if;
    if cuit_nuevo is null or not nucleo.cuit_valido(cuit_nuevo) then
      raise exception 'el CUIT no es válido' using errcode = 'P0001', hint = 'cuit_invalido';
    end if;
    v := nucleo.proveedor_vigente(c, proveedor);
    if version_esperada is distinct from v.numero then
      raise exception 'el proveedor cambió mientras tanto' using errcode = 'P0001',
        hint = 'version_desactualizada';
    end if;
    begin
      update nucleo.proveedor p set cuit = cuit_nuevo
       where p.consorcio_id = c and p.id = proveedor;
    exception when unique_violation then
      raise exception 'ya hay un proveedor con ese CUIT en este consorcio'
        using errcode = 'P0001', hint = 'cuit_repetido';
    end;
    h := nucleo.insertar_version_proveedor(c, proveedor, v.numero + 1, cuit_nuevo, v.razon_social,
           v.rubro, v.contacto, v.estado, v.documento_id, v.cuenta_destino, actor, identidad,
           v.telefono, v.whatsapp, v.correo, v.domicilio, v.atiende_emergencias);
    perform nucleo.auditar_proveedor(c, 'consorcio.proveedor.cargar_cuit', proveedor, actor,
      jsonb_build_object('version', v.numero, 'huella', encode(v.huella, 'hex')),
      jsonb_build_object('version', v.numero + 1, 'huella', encode(h, 'hex'), 'con_cuit', true));
    return v.numero + 1;
  end
  $$;
revoke all on function {CARGAR} from public;
grant execute on function {CARGAR} to cem_app;
"""

BAJAR = f"""
drop function {CARGAR};
drop trigger historia_proveedor on nucleo.proveedor;
create trigger historia_proveedor before update or delete on nucleo.proveedor
  for each row execute function nucleo.negar_cambio_historia_orden_pago();
drop function nucleo.negar_cambio_proveedor();
"""


def upgrade() -> None:
    op.execute(SUBIR)


def downgrade() -> None:
    op.execute(BAJAR)
