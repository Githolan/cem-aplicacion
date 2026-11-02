"""El comprobante de un pago manual queda restringido desde que se carga (P3 154, 02-11).

`0061` y `0062` restringen el comprobante cuando el registro lo ata a la ejecución: lo ven quien
subió cada versión y el Consejo que no preparó la orden. Antes del registro era un documento más de
«Facturas y comprobantes»: entre la carga y el registro lo descargaba todo propietario por su
vínculo y quien tenía `documentos.ver`, también quien preparó la orden. Es la decisión del
propietario del 02-11 («Restringir al Consejo», P3 148 y 156) aplicada a ese tramo; no es una
política nueva.

- **Se carga desde el pago** (`nucleo.crear_comprobante_de_pago`): el documento nace en
  `facturas_y_comprobantes` con la orden que respalda (`nucleo.documento.orden_comprobante`).
  Lo crea solo quien vio la cuenta en el acto de una ejecución manual de esa orden, por su
  identidad humana (`no_es_quien_ejecuto`), y con la facultad de carga de documentos. La versión
  se sube después por la ruta de siempre, con el antivirus.
- **Restringido desde que existe:** `nucleo.es_comprobante_de_pago` dice que sí también para un
  documento con orden, atado o no, y `nucleo.preparo_la_orden_del_comprobante` mira también esa
  orden. La lista y la descarga de `0061`/`0062` no cambian: ya preguntan a esas dos funciones.
- **El registro exige un comprobante cargado desde el pago de esa orden** (`comprobante_invalido`):
  una factura subida por la pantalla de documentos ya no se ata. Así no queda ningún comprobante
  que haya pasado por la categoría sin restricción.
- **La lista de documentos devuelve la orden** (`orden_comprobante`) a quien ya ve el documento,
  para que la pantalla del pago ofrezca solo los comprobantes de esa orden. Cambia lo que
  devuelve la función, así que se borra y se vuelve a crear, con el mismo permiso.
- **La orden de un comprobante no cambia** una vez puesta (disparador). Ninguna ruta la escribe
  después de crearlo; soporte puede cambiarle la categoría y la restricción sigue, porque no
  depende de la categoría.

La exportación formal (`0038`) no cambia (P3 155, aceptado por el propietario).

Downgrade: se niega (`downgrade_con_datos`) si hay un comprobante con orden que no está atado a un
pago: sin la columna volvería a ser un documento más de la categoría. Si no, vuelve a `0062`.

Revisión: 0063
Anterior: 0062
"""

from alembic import op
from sqlalchemy import text

revision = "0063"
down_revision = "0062"
branch_labels = None
depends_on = None

CATEGORIA = "facturas_y_comprobantes"
LISTA = "nucleo.documentos_del_consorcio()"
LISTA_CON_ORDEN = [
    ("subida_en timestamp with time zone)\n",
     "subida_en timestamp with time zone, orden_comprobante uuid)\n"),
    ("x.motivo_rechazo, x.tipo, x.tamano, x.sha256, x.subida_en\n",
     "x.motivo_rechazo, x.tipo, x.tamano, x.sha256, x.subida_en, d.orden_comprobante\n"),
]  # fmt: skip
CREAR = "nucleo.crear_comprobante_de_pago(uuid, text, date, uuid, text)"
REGISTRO = (
    "nucleo.registrar_pago_manual(uuid, integer, bytea, text, date, bigint, text, text, uuid,"
    " uuid, text, uuid)"
)

ES_COMPROBANTE_0061 = """
create or replace function nucleo.es_comprobante_de_pago(c uuid, documento uuid)
  returns boolean
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select exists (select 1 from nucleo.comprobante_pago_manual p
                    where p.consorcio_id = c and p.documento_id = documento)
  $$;
"""

PREPARO_0062 = """
create or replace function nucleo.preparo_la_orden_del_comprobante(c uuid, documento uuid,
                                                                   quien uuid)
  returns boolean
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select nucleo.identidad_humana_de(c, quien) is null
        or exists (
             select 1 from nucleo.comprobante_pago_manual p
               join nucleo.ejecucion_orden_pago e
                 on e.consorcio_id = p.consorcio_id and e.id = p.ejecucion_id
               join nucleo.orden_pago o on o.consorcio_id = e.consorcio_id and o.id = e.orden_id
              where p.consorcio_id = c and p.documento_id = documento
                and (nucleo.misma_persona_humana(c, quien, o.preparada_por)
                     or exists (select 1 from nucleo.orden_pago_version v
                                 where v.consorcio_id = c and v.orden_id = o.id
                                   and (v.identidad_preparo = nucleo.identidad_humana_de(c, quien)
                                        or nucleo.misma_persona_humana(c, quien,
                                                                       v.preparada_por)))))
  $$;
"""

UPGRADE = f"""
alter table nucleo.documento
  add column orden_comprobante uuid,
  add foreign key (consorcio_id, orden_comprobante) references nucleo.orden_pago (consorcio_id, id);

-- Una vez puesta, la orden de un comprobante no cambia.
create function nucleo.orden_comprobante_fija()
  returns trigger
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  begin
    if old.orden_comprobante is not null
       and new.orden_comprobante is distinct from old.orden_comprobante then
      raise exception 'el comprobante de un pago no cambia de orden' using errcode = 'P0001',
        hint = 'historia_inmutable';
    end if;
    return new;
  end
  $$;
create trigger orden_comprobante_fija before update of orden_comprobante on nucleo.documento
  for each row execute function nucleo.orden_comprobante_fija();

-- El comprobante se carga desde el pago: lo crea quien vio la cuenta en el acto de una ejecución
-- manual de la orden, y nace restringido.
create function nucleo.crear_comprobante_de_pago(orden uuid, titulo_nuevo text,
                                                  fecha_nueva date, actor uuid,
                                                  origen_pedido text)
  returns uuid
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    identidad uuid;
    d nucleo.documento;
  begin
    perform nucleo.exigir_carga_documentos(c, actor, origen_pedido, '{CATEGORIA}');
    identidad := nucleo.identidad_humana_de(c, actor);
    if identidad is null or not exists (
         select 1 from nucleo.ejecucion_orden_pago e
           join nucleo.apertura_cuenta_pago_manual a
             on a.consorcio_id = e.consorcio_id and a.ejecucion_id = e.id
          where e.consorcio_id = c and e.orden_id = orden and e.modalidad = 'manual'
            and a.identidad_abrio = identidad) then
      raise exception 'carga el comprobante quien vio la cuenta en el acto de ejecución'
        using errcode = 'P0001', hint = 'no_es_quien_ejecuto';
    end if;
    insert into nucleo.documento (consorcio_id, categoria, titulo, fecha, cargado_por,
                                  orden_comprobante)
    values (c, '{CATEGORIA}', btrim(titulo_nuevo), fecha_nueva, actor, orden)
    returning * into d;
    -- El título no va a la auditoría: es texto libre.
    perform nucleo.auditar_documento(c, 'consorcio.documento.crear', 'documento', d.id, actor,
      origen_pedido, jsonb_build_object('categoria', d.categoria, 'fecha', d.fecha));
    return d.id;
  end
  $$;
revoke all on function {CREAR} from public;
grant execute on function {CREAR} to cem_app;

-- Comprobante de un pago: atado por el registro (0055) o cargado desde el pago (0063).
create or replace function nucleo.es_comprobante_de_pago(c uuid, documento uuid)
  returns boolean
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select exists (select 1 from nucleo.comprobante_pago_manual p
                    where p.consorcio_id = c and p.documento_id = documento)
        or exists (select 1 from nucleo.documento d
                    where d.consorcio_id = c and d.id = documento
                      and d.orden_comprobante is not null)
  $$;

-- La orden del comprobante es la de su pago o la de su carga (0063).
create or replace function nucleo.preparo_la_orden_del_comprobante(c uuid, documento uuid,
                                                                   quien uuid)
  returns boolean
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select nucleo.identidad_humana_de(c, quien) is null
        or exists (
             select 1 from nucleo.orden_pago o
              where o.consorcio_id = c
                and o.id in (select e.orden_id from nucleo.comprobante_pago_manual p
                               join nucleo.ejecucion_orden_pago e
                                 on e.consorcio_id = p.consorcio_id and e.id = p.ejecucion_id
                              where p.consorcio_id = c and p.documento_id = documento
                             union
                             select d.orden_comprobante from nucleo.documento d
                              where d.consorcio_id = c and d.id = documento)
                and (nucleo.misma_persona_humana(c, quien, o.preparada_por)
                     or exists (select 1 from nucleo.orden_pago_version v
                                 where v.consorcio_id = c and v.orden_id = o.id
                                   and (v.identidad_preparo = nucleo.identidad_humana_de(c, quien)
                                        or nucleo.misma_persona_humana(c, quien,
                                                                       v.preparada_por)))))
  $$;
"""

SIN_ARCHIVO = (
    "      raise exception 'el comprobante no existe o no pasó el antivirus'"
    " using errcode = 'P0001',\n"
    "        hint = 'comprobante_invalido';\n"
    "    end if;\n"
)
DESDE_EL_PAGO = """\
    -- El comprobante se cargó desde el pago de esta orden (0063): nació restringido. Antes de
    -- atarlo, así un documento de otra orden se niega siempre igual, aunque registren a la vez.
    if not exists (select 1 from nucleo.documento d
                    where d.consorcio_id = c and d.id = documento
                      and d.orden_comprobante = orden) then
      raise exception 'el comprobante no se cargó desde el pago de esta orden'
        using errcode = 'P0001', hint = 'comprobante_invalido';
    end if;
"""

SIN_REGISTRAR = """
do $$
begin
  if exists (select 1 from nucleo.documento d
              where d.orden_comprobante is not null
                and not exists (select 1 from nucleo.comprobante_pago_manual p
                                 where p.consorcio_id = d.consorcio_id
                                   and p.documento_id = d.id)) then
    raise exception 'hay comprobantes cargados desde el pago sin registrar'
      using errcode = 'P0001', hint = 'downgrade_con_datos';
  end if;
end
$$;
"""

DOWNGRADE = f"""
{ES_COMPROBANTE_0061}
{PREPARO_0062}
drop function {CREAR};
drop trigger orden_comprobante_fija on nucleo.documento;
drop function nucleo.orden_comprobante_fija();
alter table nucleo.documento drop column orden_comprobante;
"""


def _definicion(funcion: str, cambios: list[tuple[str, str]]) -> str:
    definicion = (
        op.get_bind()
        .execute(text("select pg_get_functiondef(cast(:f as regprocedure))"), {"f": funcion})
        .scalar_one()
    )
    for antes, despues in cambios:
        if definicion.count(antes) != 1:
            raise RuntimeError(f"0063: {funcion} no tiene exactamente una vez {antes!r}")
        definicion = definicion.replace(antes, despues)
    return definicion


def _reemplazar(funcion: str, antes: str, despues: str) -> None:
    op.execute(_definicion(funcion, [(antes, despues)]))


def _otra_lista(cambios: list[tuple[str, str]]) -> None:
    """Lo que devuelve la lista cambia: se borra y se crea de nuevo, con el permiso de `0036`."""
    definicion = _definicion(LISTA, cambios)
    op.execute(f"drop function {LISTA}")
    op.execute(definicion)
    op.execute(f"revoke all on function {LISTA} from public")
    op.execute(f"grant execute on function {LISTA} to cem_app")


def upgrade() -> None:
    op.execute(UPGRADE)
    _reemplazar(REGISTRO, SIN_ARCHIVO, SIN_ARCHIVO + DESDE_EL_PAGO)
    _otra_lista(LISTA_CON_ORDEN)


def downgrade() -> None:
    op.execute(SIN_REGISTRAR)
    _otra_lista([(despues, antes) for antes, despues in reversed(LISTA_CON_ORDEN)])
    _reemplazar(REGISTRO, SIN_ARCHIVO + DESDE_EL_PAGO, SIN_ARCHIVO)
    op.execute(DOWNGRADE)
