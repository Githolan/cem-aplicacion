"""La revisión niega la cuenta completa por la identidad histórica (P3 115); el respaldo del
proveedor exige un documento disponible (P3 93).

Hasta `0053`, la API decidía si quien lee la revisión de una orden o de un contrato es quien la
preparó o lo registró comparando su identidad humana con los vínculos **vigentes** de las cuentas
que prepararon o registraron. Si la cuenta con la que alguien preparó se desvinculaba después de su
identidad, otra cuenta suya veía la cuenta destino completa (revisión del delta de seguridad de
#175). La aprobación y la confirmación ya se negaban en la base con la identidad guardada en cada
acto (`identidad_preparo`, `identidad_registro`); la lectura, no.

Cómo: dos funciones de solo lectura, en el ámbito del consorcio actual y para la persona actual,
que comparan la identidad humana vigente de quien lee con la identidad guardada al preparar cada
versión de la orden o al registrar el contrato. Encima conservan la barrera anterior: la misma
cuenta o la misma identidad de hoy (`nucleo.misma_persona_humana`), igual que la aprobación
(`0045`) y la confirmación (`0050`). Así, si soporte corrige un vínculo equivocado, el dueño real
tampoco recibe la cuenta (revisión independiente de #180):
- `nucleo.lee_quien_preparo_la_orden(orden, actor)`;
- `nucleo.lee_quien_registro_el_contrato(contrato, actor)`.

Fallan cerradas: sin identidad humana vigente de quien lee, o con una orden o un contrato que no es
del consorcio actual, devuelven verdadero (la API no da la cuenta completa). No hay datos nuevos ni
backfill: la identidad de cada acto ya estaba guardada desde `0045` y `0050`.

P3 93: una versión nueva del proveedor con documento de respaldo exige que el documento respalde
(`nucleo.documento_respalda`, `0052`: vigente y con al menos una versión `disponible`), como ya
exigían los contratos. Antes alcanzaba con `vigente`: un documento en cuarentena o rechazado por el
antivirus servía. Solo mira el acto nuevo: las versiones ya creadas no se tocan, porque el
documento era válido cuando se cargaron (o lo que valía entonces), y la historia no se reinterpreta.
Ninguna orden ni contrato toma su respaldo del documento del proveedor.

Downgrade: borra las dos funciones y vuelve el control del documento del proveedor a `vigente`.
No revive ningún permiso, confirmación ni regla ya invalidada: las funciones no guardan nada, y el
control del documento solo mira las versiones que se crean de ahí en adelante. El código anterior
vuelve a comparar por vínculos vigentes, que es la debilidad que esto corrige, no una pérdida de
datos.

Revisión: 0054
Anterior: 0053
"""

from alembic import op
from sqlalchemy import text

revision = "0054"
down_revision = "0053"
branch_labels = None
depends_on = None

FUNCIONES = """
create function nucleo.lee_quien_preparo_la_orden(orden uuid, actor uuid)
  returns boolean
  language plpgsql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    yo uuid;
  begin
    if c is null or actor is null or actor is distinct from plataforma.persona_actual() then
      raise exception 'sin ámbito para leer la revisión' using errcode = '42501';
    end if;
    yo := nucleo.identidad_humana_de(c, actor);
    return yo is null
        or not exists (select 1 from nucleo.orden_pago o where o.consorcio_id = c and o.id = orden)
        or exists (select 1 from nucleo.orden_pago o
                    where o.consorcio_id = c and o.id = orden
                      and nucleo.misma_persona_humana(c, actor, o.preparada_por))
        or exists (select 1 from nucleo.orden_pago_version v
                    where v.consorcio_id = c and v.orden_id = orden
                      and (v.identidad_preparo = yo
                           or nucleo.misma_persona_humana(c, actor, v.preparada_por)));
  end
  $$;

create function nucleo.lee_quien_registro_el_contrato(contrato uuid, actor uuid)
  returns boolean
  language plpgsql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    yo uuid;
  begin
    if c is null or actor is null or actor is distinct from plataforma.persona_actual() then
      raise exception 'sin ámbito para leer la revisión' using errcode = '42501';
    end if;
    yo := nucleo.identidad_humana_de(c, actor);
    return yo is null
        or not exists (select 1 from nucleo.contrato_proveedor k
                        where k.consorcio_id = c and k.id = contrato
                          and k.identidad_registro is distinct from yo
                          and not nucleo.misma_persona_humana(c, actor, k.registrado_por));
  end
  $$;
"""

INSERTAR_VERSION = (
    "nucleo.insertar_version_proveedor(uuid, uuid, integer, text, text, text, text,"
    " nucleo.estado_proveedor, uuid, text, uuid, uuid)"
)
SOLO_VIGENTE = (
    "          where d.consorcio_id = c and d.id = documento and d.estado = 'vigente') then\n"
    "      raise exception 'el documento no existe en este consorcio' using errcode = 'P0001',\n"
)
RESPALDA = (
    "          where d.consorcio_id = c and d.id = documento\n"
    "            and nucleo.documento_respalda(c, documento)) then\n"
    "      raise exception 'el documento no existe en este consorcio o no tiene una versión"
    " disponible'\n"
    "        using errcode = 'P0001',\n"
)

FIRMAS = (
    "nucleo.lee_quien_preparo_la_orden(uuid, uuid)",
    "nucleo.lee_quien_registro_el_contrato(uuid, uuid)",
)


def _reemplazar(funcion: str, antes: str, despues: str) -> None:
    definicion = (
        op.get_bind()
        .execute(text("select pg_get_functiondef(cast(:f as regprocedure))"), {"f": funcion})
        .scalar_one()
    )
    if definicion.count(antes) != 1:
        raise RuntimeError(f"0054: {funcion} no tiene exactamente una vez {antes!r}")
    op.execute(definicion.replace(antes, despues))


def upgrade() -> None:
    op.execute(FUNCIONES)
    for f in FIRMAS:
        op.execute(f"revoke all on function {f} from public")
        op.execute(f"grant execute on function {f} to cem_app")
    _reemplazar(INSERTAR_VERSION, SOLO_VIGENTE, RESPALDA)


def downgrade() -> None:
    _reemplazar(INSERTAR_VERSION, RESPALDA, SOLO_VIGENTE)
    for f in FIRMAS:
        op.execute(f"drop function {f}")
