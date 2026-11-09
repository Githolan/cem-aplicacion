"""Retirar una orden de pago mientras nadie la aprobó (paso 2 de 2; el estado, en `0092`).

Decisión del propietario del 09-11-2026, comentario 23 de la revisión de pantallas (repositorio
CEM, `doc/revision-pantallas/pendientes.md`), con sus palabras: «Si a «Retirar la orden»: solo
mientras nadie la aprobó, queda registrado quién y cuándo, y sale de la bandeja».

- **Transiciones nuevas** del diagrama 4 (documento 08 §6): Borrador → Retirada y EnRevision →
  Retirada. Retirada no sale a ningún lado: es un estado terminado, como Rechazada y Conciliada.
- **`nucleo.orden_pago`** suma `retirada_por` y `retirada_en`, que van juntas y solo con el estado
  `retirada`. `aprobada_en` sigue vacía en una orden retirada.
- **`nucleo.retirar_orden_pago`**: con `egresos.preparar_orden`, sin el gesto (no mueve dinero,
  como preparar y enviar, `0071`). Solo la retira el ser humano que preparó alguna de sus versiones
  (por la identidad guardada en la versión, `retirar_solo_quien_preparo`), y solo si la versión
  vigente no tiene ninguna aprobación (`orden_con_aprobaciones`). Un estado que no es Borrador ni
  EnRevision da `estado_invalido`. Deja el evento `consorcio.orden_pago.retirar` con quién actuó.
- **`nucleo.retiro_orden_pago_lectura`**: quién la retiró (nombre) y cuándo, para la ficha. Lee
  quien lee egresos (`exigir_lectura_egresos`).
- **Cambio a Consejo** (`nucleo.cambiar_modo`, `0090`): una orden retirada está terminada y no
  traba el cambio.
- **Fraccionamiento** (`nucleo.evaluar_orden_pago`, `0050`): una orden retirada no suma, igual que
  una rechazada.

Downgrade: se niega (`downgrade_con_datos`) si alguna orden está retirada; bajar borraría quién y
cuándo la retiró. Sin eso, todo vuelve a como estaba en `0091`.

Revisión: 0093
Anterior: 0092
"""

from alembic import op
from sqlalchemy import text

revision = "0093"
down_revision = "0092"
branch_labels = None
depends_on = None

RETIRAR = "nucleo.retirar_orden_pago(uuid, integer, uuid, text, uuid)"
LECTURA = "nucleo.retiro_orden_pago_lectura(uuid, text, uuid, text, uuid)"
MODO = "nucleo.cambiar_modo(nucleo.tipo_modo, uuid, date, uuid, text)"
EVALUAR = (
    "nucleo.evaluar_orden_pago(uuid, uuid, nucleo.proveedor_version, bigint, text, boolean, uuid)"
)

TERMINADAS_ANTES = "o.estado not in ('rechazada', 'conciliada')"
TERMINADAS_AHORA = "o.estado not in ('rechazada', 'retirada', 'conciliada')"
FRACCION_ANTES = "o2.estado <> 'rechazada'"
FRACCION_AHORA = "o2.estado not in ('rechazada', 'retirada')"

SIN_APROBAR_ANTES = "'borrador'::nucleo.estado_orden_pago, 'en_revision'::nucleo.estado_orden_pago, 'rechazada'::nucleo.estado_orden_pago]"  # noqa: E501
SIN_APROBAR_30 = "estado in ('borrador', 'en_revision', 'rechazada')"
SIN_APROBAR_93 = "estado in ('borrador', 'en_revision', 'rechazada', 'retirada')"

UPGRADE = f"""
insert into nucleo.transicion_orden_pago (desde, hacia) values
  ('borrador', 'retirada'), ('en_revision', 'retirada');

alter table nucleo.orden_pago
  add column retirada_por uuid references nucleo.persona (id),
  add column retirada_en timestamptz,
  add constraint orden_pago_retirada_con_quien_y_cuando check (
    (estado = 'retirada') = (retirada_por is not null)
    and (retirada_por is null) = (retirada_en is null));

-- Retirar una orden que nadie aprobó (0093): queda, en su estado, con quién y cuándo.
create function {RETIRAR.split("(")[0]}(orden uuid, version_esperada integer, actor uuid,
                                        origen_pedido text, sesion uuid)
  returns void
  language plpgsql
  security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    o nucleo.orden_pago;
    identidad uuid;
  begin
    identidad := nucleo.exigir_acto_sin_gesto(c, actor, sesion, 'egresos.preparar_orden',
                                              origen_pedido);
    o := nucleo.orden_pago_bloqueada(c, orden);
    if o.estado not in ('borrador', 'en_revision') then
      raise exception 'solo se retira una orden que nadie aprobó' using errcode = 'P0001',
        hint = 'estado_invalido';
    end if;
    if version_esperada is distinct from o.version_vigente then
      raise exception 'la orden cambió mientras tanto' using errcode = 'P0001',
        hint = 'version_desactualizada';
    end if;
    if not exists (select 1 from nucleo.orden_pago_version x
                    where x.orden_id = o.id and x.identidad_preparo = identidad) then
      raise exception 'solo la retira quien la preparó' using errcode = 'P0001',
        hint = 'retirar_solo_quien_preparo';
    end if;
    if exists (select 1 from nucleo.orden_pago_aprobacion x
                where x.orden_id = o.id and x.version = o.version_vigente) then
      raise exception 'alguien ya la aprobó' using errcode = 'P0001',
        hint = 'orden_con_aprobaciones';
    end if;
    update nucleo.orden_pago
       set estado = 'retirada', retirada_por = actor, retirada_en = now(), actualizada_en = now()
     where id = orden;
    perform nucleo.auditar_orden_pago(c, 'consorcio.orden_pago.retirar', orden, actor,
      jsonb_build_object('estado', o.estado),
      jsonb_build_object('estado', 'retirada', 'version', o.version_vigente));
  end
  $$;
revoke all on function {RETIRAR} from public;
grant execute on function {RETIRAR} to cem_app;

create function {LECTURA.split("(")[0]}(orden uuid, facultad text, actor uuid,
                                                 origen_pedido text, sesion uuid)
  returns table (orden_id uuid, retirada_por uuid, retirada_por_nombre text,
                 retirada_en timestamptz)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
  begin
    perform nucleo.exigir_lectura_egresos(c, facultad, actor, origen_pedido, sesion);
    return query
      select o.id, o.retirada_por, r.nombre, o.retirada_en
        from nucleo.orden_pago o
        join nucleo.persona r on r.id = o.retirada_por
       where o.consorcio_id = c and o.estado = 'retirada' and (orden is null or o.id = orden);
  end
  $$;
revoke all on function {LECTURA} from public;
grant execute on function {LECTURA} to cem_app;
"""

GUARDA_DOWNGRADE = """
do $$
begin
  if exists (select 1 from nucleo.orden_pago where estado = 'retirada') then
    raise exception '0093 no baja con órdenes retiradas' using errcode = 'P0001',
      hint = 'downgrade_con_datos';
  end if;
end
$$;
"""

DOWNGRADE = f"""
drop function {LECTURA};
drop function {RETIRAR};
alter table nucleo.orden_pago
  drop constraint orden_pago_retirada_con_quien_y_cuando,
  drop column retirada_en,
  drop column retirada_por;
delete from nucleo.transicion_orden_pago where hacia = 'retirada';
"""


def _definicion(firma: str) -> str:
    return (
        op.get_bind()
        .execute(text("select pg_get_functiondef(cast(:f as regprocedure))"), {"f": firma})
        .scalar_one()
    )


def _reemplazar(firma: str, antes: str, despues: str) -> None:
    vigente = _definicion(firma)
    if vigente.count(antes) != 1:
        raise RuntimeError(f"0093: {firma} no tiene una sola vez «{antes}»")
    op.execute(vigente.replace(antes, despues))


def _sin_aprobar(antes: str, despues: str) -> None:
    """La restricción «sin aprobar ⇔ sin `aprobada_en`» de `0050`, con o sin `retirada`."""
    nombres = (
        op.get_bind()
        .execute(
            text(
                "select conname from pg_constraint"
                " where conrelid = 'nucleo.orden_pago'::regclass and contype = 'c'"
                " and pg_get_constraintdef(oid) like '%aprobada_en IS NULL%'"
                " and pg_get_constraintdef(oid) like :a"
            ),
            {"a": f"%{antes}%"},
        )
        .scalars()
        .all()
    )
    if len(nombres) != 1:
        raise RuntimeError(f"0093: se esperaba una restricción de aprobada_en y hay {nombres}")
    op.execute(f"alter table nucleo.orden_pago drop constraint {nombres[0]}")
    op.execute(
        f"alter table nucleo.orden_pago add constraint {nombres[0]}"
        f" check (({despues}) = (aprobada_en is null))"
    )


def upgrade() -> None:
    op.execute(UPGRADE)
    _sin_aprobar(SIN_APROBAR_ANTES, SIN_APROBAR_93)
    _reemplazar(MODO, TERMINADAS_ANTES, TERMINADAS_AHORA)
    _reemplazar(EVALUAR, FRACCION_ANTES, FRACCION_AHORA)


def downgrade() -> None:
    op.execute(GUARDA_DOWNGRADE)
    _reemplazar(EVALUAR, FRACCION_AHORA, FRACCION_ANTES)
    _reemplazar(MODO, TERMINADAS_AHORA, TERMINADAS_ANTES)
    _sin_aprobar("'retirada'::nucleo.estado_orden_pago]", SIN_APROBAR_30)
    op.execute(DOWNGRADE)
