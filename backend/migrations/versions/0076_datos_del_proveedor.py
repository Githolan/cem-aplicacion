"""Los datos del proveedor (`Rediseno_Circuito_Pagos_2026-11-06.md`, §12, repositorio CEM).

- **`contacto` ya se guardaba**: `0048` lo escribe en `nucleo.proveedor_version`, las lecturas lo
  devuelven y la pantalla lo mandaba. Faltaba mostrarlo. Sigue como está.
- **El CUIT pasa a ser obligatorio.** `alta_proveedor` niega el que falta con `cuit_invalido` y una
  restricción lo cierra en la tabla. Va `not valid`: `nucleo.proveedor` solo admite altas, así que
  rige para toda fila nueva, y los proveedores que ya se cargaron sin CUIT no traban la migración
  ni dejan de cobrar. El CUIT no cambia una vez cargado (`0048`): uno viejo sin CUIT se da de alta
  de nuevo si hace falta.
- **Teléfono, WhatsApp, correo, domicilio y si atiende emergencias**, opcionales, en cada versión.
  Son datos de un tercero: no van en la huella (cambiarlos no cambia lo que se aprobó pagar) ni en
  el evento de auditoría, como `contacto`. La versión nueva sí cuenta como cambio.

Las funciones suman los parámetros al final, y `alta_proveedor` y `nueva_version_proveedor` los
toman con valor por defecto.

**Downgrade** se niega (`downgrade_con_datos`) si algún proveedor ya tiene uno de estos datos:
borrar las columnas los perdería.

Revisión: 0076
Anterior: 0075
"""

from alembic import op
from sqlalchemy import text

revision = "0076"
down_revision = "0075"
branch_labels = None
depends_on = None

NUEVOS = "text, text, text, text, boolean"
INSERTAR = (
    "nucleo.insertar_version_proveedor(uuid, uuid, integer, text, text, text, text,"
    " nucleo.estado_proveedor, uuid, text, uuid, uuid{})"
)
ALTA = "nucleo.alta_proveedor(text, text, text, text, uuid, text, uuid, text, uuid{})"
VERSION = (
    "nucleo.nueva_version_proveedor(uuid, integer, text, text, text, nucleo.estado_proveedor,"
    " uuid, text, uuid, text, uuid{})"
)
LECTURA = "nucleo.proveedores_lectura(uuid, uuid, text, uuid)"
VERSIONES = "nucleo.proveedor_versiones_lectura(uuid, uuid, text, uuid)"

PARAMETROS = (
    "telefono_nuevo text, whatsapp_nuevo text, correo_nuevo text, domicilio_nuevo text,"
    " atiende_nuevo boolean"
)
CON_VALOR = (
    "telefono_nuevo text DEFAULT NULL::text, whatsapp_nuevo text DEFAULT NULL::text,"
    " correo_nuevo text DEFAULT NULL::text, domicilio_nuevo text DEFAULT NULL::text,"
    " atiende_nuevo boolean DEFAULT false"
)
ARGUMENTOS = "telefono_nuevo, whatsapp_nuevo, correo_nuevo, domicilio_nuevo, atiende_nuevo"
RECORTADOS = (
    "btrim(telefono_nuevo), btrim(whatsapp_nuevo), btrim(correo_nuevo), btrim(domicilio_nuevo),"
    " atiende_nuevo"
)
COLUMNAS = "telefono, whatsapp, correo, domicilio, atiende_emergencias"
EN_TABLA = "telefono text, whatsapp text, correo text, domicilio text, atiende_emergencias boolean"
DE_VERSION = "pv.telefono, pv.whatsapp, pv.correo, pv.domicilio, pv.atiende_emergencias"

INSERTAR_CAMBIOS = (
    ("actor uuid, identidad uuid)\n RETURNS bytea",
     f"actor uuid, identidad uuid, {PARAMETROS})\n RETURNS bytea"),
    ("(consorcio_id, proveedor_id, numero, razon_social, rubro, contacto, estado, documento_id,",
     f"(consorcio_id, proveedor_id, numero, razon_social, rubro, contacto, {COLUMNAS}, estado,"
     " documento_id,"),
    ("btrim(contacto_nuevo), estado_nuevo, documento, cuenta, h, actor, identidad);",
     f"btrim(contacto_nuevo), {RECORTADOS}, estado_nuevo, documento, cuenta, h, actor,"
     " identidad);"),
)  # fmt: skip
ALTA_CAMBIOS = (
    ("origen_pedido text, sesion uuid)\n RETURNS uuid",
     f"origen_pedido text, sesion uuid, {CON_VALOR})\n RETURNS uuid"),
    ("if cuit_nuevo is not null and not nucleo.cuit_valido(cuit_nuevo) then",
     "if cuit_nuevo is null or not nucleo.cuit_valido(cuit_nuevo) then"),
    ("contacto_nuevo, 'activo', documento, cuenta, actor, identidad);",
     f"contacto_nuevo, 'activo', documento, cuenta, actor, identidad, {ARGUMENTOS});"),
)  # fmt: skip
VERSION_CAMBIOS = (
    ("origen_pedido text, sesion uuid)\n RETURNS integer",
     f"origen_pedido text, sesion uuid, {CON_VALOR})\n RETURNS integer"),
    ("if (v.razon_social, v.rubro, v.contacto, v.estado, v.documento_id, v.cuenta_destino)",
     "if (v.razon_social, v.rubro, v.contacto, v.telefono, v.whatsapp, v.correo, v.domicilio,\n"
     "        v.atiende_emergencias, v.estado, v.documento_id, v.cuenta_destino)"),
    ("btrim(contacto_nuevo), estado_nuevo, documento, cuenta) then",
     f"btrim(contacto_nuevo), {RECORTADOS},\n"
     "                             estado_nuevo, documento, cuenta) then"),
    ("           actor, identidad);\n    perform nucleo.auditar_proveedor",
     f"           actor, identidad, {ARGUMENTOS});\n    perform nucleo.auditar_proveedor"),
)  # fmt: skip
LECTURA_CAMBIOS = (
    ("rubro text, contacto text, estado text",
     f"rubro text, contacto text, {EN_TABLA}, estado text"),
    ("pv.contacto, pv.estado::text", f"pv.contacto, {DE_VERSION}, pv.estado::text"),
)  # fmt: skip

SUBIR = """
alter table nucleo.proveedor_version
  add column telefono text check (length(btrim(telefono)) between 1 and 50),
  add column whatsapp text check (length(btrim(whatsapp)) between 1 and 50),
  add column correo text check (length(btrim(correo)) between 3 and 254),
  add column domicilio text check (length(btrim(domicilio)) between 1 and 200),
  add column atiende_emergencias boolean not null default false;

alter table nucleo.proveedor
  add constraint proveedor_cuit_obligatorio check (cuit is not null) not valid;
"""

GUARDA_DOWNGRADE = """
lock table nucleo.proveedor_version in share row exclusive mode;

do $$
begin
  if exists (select 1 from nucleo.proveedor_version
              where telefono is not null or whatsapp is not null or correo is not null
                 or domicilio is not null or atiende_emergencias) then
    raise exception '0076 no baja con datos de contacto cargados: se perderían'
      using errcode = 'P0001', hint = 'downgrade_con_datos';
  end if;
end
$$;
"""

BAJAR = """
alter table nucleo.proveedor drop constraint proveedor_cuit_obligatorio;
alter table nucleo.proveedor_version
  drop column telefono,
  drop column whatsapp,
  drop column correo,
  drop column domicilio,
  drop column atiende_emergencias;
"""


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
            raise RuntimeError(f"0076: {funcion} no tiene exactamente una vez {antes!r}")
        definicion = definicion.replace(antes, despues)
    return definicion


def _otra_firma(desde: str, hasta: str, cambios, *, al_reves: bool = False, app: bool) -> None:
    """Otra firma es otra función: se crea, se le dan los permisos y se borra la anterior."""
    op.execute(_definicion(desde, cambios, al_reves=al_reves))
    op.execute(f"revoke all on function {hasta} from public")
    if app:
        op.execute(f"grant execute on function {hasta} to cem_app")
    op.execute(f"drop function {desde}")


def _otra_lectura(funcion: str, cambios, *, al_reves: bool = False) -> None:
    """Lo que devuelve cambia: se borra y se crea de nuevo, con su permiso."""
    definicion = _definicion(funcion, cambios, al_reves=al_reves)
    op.execute(f"drop function {funcion}")
    op.execute(definicion)
    op.execute(f"revoke all on function {funcion} from public")
    op.execute(f"grant execute on function {funcion} to cem_app")


def _firmas(plantilla: str) -> tuple[str, str]:
    return plantilla.format(""), plantilla.format(f", {NUEVOS}")


def upgrade() -> None:
    op.execute(SUBIR)
    vieja, nueva = _firmas(INSERTAR)
    _otra_firma(vieja, nueva, INSERTAR_CAMBIOS, app=False)
    vieja, nueva = _firmas(ALTA)
    _otra_firma(vieja, nueva, ALTA_CAMBIOS, app=True)
    vieja, nueva = _firmas(VERSION)
    _otra_firma(vieja, nueva, VERSION_CAMBIOS, app=True)
    _otra_lectura(LECTURA, LECTURA_CAMBIOS)
    _otra_lectura(VERSIONES, LECTURA_CAMBIOS)


def downgrade() -> None:
    op.execute(GUARDA_DOWNGRADE)
    _otra_lectura(VERSIONES, LECTURA_CAMBIOS, al_reves=True)
    _otra_lectura(LECTURA, LECTURA_CAMBIOS, al_reves=True)
    vieja, nueva = _firmas(VERSION)
    _otra_firma(nueva, vieja, VERSION_CAMBIOS, al_reves=True, app=True)
    vieja, nueva = _firmas(ALTA)
    _otra_firma(nueva, vieja, ALTA_CAMBIOS, al_reves=True, app=True)
    vieja, nueva = _firmas(INSERTAR)
    _otra_firma(nueva, vieja, INSERTAR_CAMBIOS, al_reves=True, app=False)
    op.execute(BAJAR)
