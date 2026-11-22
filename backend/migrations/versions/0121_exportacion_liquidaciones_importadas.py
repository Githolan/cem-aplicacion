"""La exportación de soporte lleva las liquidaciones importadas (Modo Consejo; diseño P0 S4, PA27,
RF16, RF36, RNF08).

En Modo Consejo el registro económico sale de las liquidaciones del administrador que el Consejo
importa y confirma (`0113` a `0119`). El paquete de `0038` y `0069` no las llevaba: con él no se
podía reconstruir la liquidación oficial de un período. Esta migración suma la parte
`liquidaciones_importadas` con el patrón de `0069`: `security definer`, `search_path` fijo, el
consorcio del contexto, una exportación registrada en esta transacción y sin armar,
`revoke … from public`, `grant` solo a `cem_soporte_exportacion`, y filas `(entidad, fila)` armadas
columna por columna, en orden estable. `nucleo.exportar_liquidaciones_importadas` entrega:

- **`importacion_liquidacion`:** cada importación, en cualquier estado (también `leyendo`,
  `no_reconocida`, `descartada` y `reemplazada`: la historia va entera), con su documento y su
  versión (el binario ya va en `documentos/`, `0038`), formato, período, diferencias de la lectura,
  quién la decidió y cuándo, a quién reemplaza y los motivos.
- **`campo_preliminar`:** cada dato leído con su valor, su confianza, su origen, la página, el
  renglón y su texto, la unidad del padrón que le dio la lectura, la decisión, el valor corregido y
  quién la tomó y cuándo. Con el padrón del paquete alcanza para rehacer los datos oficiales de
  cada período (RN30, `campos_oficiales_de` de `0119`).
- **`correspondencia_grupo`:** cada versión de qué esquema de coeficientes corresponde a cada grupo
  de la liquidación (`0116`, el recálculo).
- **`enlace_liquidacion`:** cada enlace anotado de cada importación, con su origen y su página
  (`0118`).

**Lo que no va, como en `0069`.** Ningún identificador de persona: va el nombre, con
`nucleo.nombre_para_exportar`. Ninguna cuenta de 22 dígitos: los textos libres pasan por
`nucleo.texto_sin_cuenta` y los `jsonb` (valores, valores corregidos y diferencias), por
`nucleo.jsonb_sin_cuenta`, que aplica la misma definición a su texto (un renglón o un concepto
pueden traer una cuenta escrita). El gasto en IA de la lectura (`plataforma.gasto_ia`, `0114`) no
va: es un costo de la plataforma, no un dato del consorcio, y el paquete no lleva ninguna tabla de
`plataforma` (la constancia tampoco).

**La auditoría tampoco lleva cuentas.** `exportar_auditoria` (`0038`) ponía los cambios de cada
evento tal como se registraron: el nombre de una correspondencia (`0116`) con una cuenta escrita
salía entero (lo encontró la prueba de PA27 al sumar el caso que señaló Codex en #278). Desde acá
los cambios pasan por `nucleo.jsonb_sin_cuenta`; el resto de la función queda igual.

Downgrade: quita la función y la auxiliar, y deja `exportar_auditoria` como en `0038`. Las
constancias quedan: `cantidades` es `jsonb` y no
depende de las partes.

Datos de prueba: SINTETICOS / NO REAL.

Revisión: 0121
Anterior: 0120
"""

import importlib.util
from pathlib import Path

from alembic import op

revision = "0121"
down_revision = "0120"
branch_labels = None
depends_on = None

FUNCION = "exportar_liquidaciones_importadas"


def _de_0069():
    """`_funcion` de `0069`: el mismo encabezado, las mismas exigencias y los mismos permisos.
    `0069` ya está aplicada y no cambia."""
    ruta = Path(__file__).with_name("0069_exportacion_economica.py")
    spec = importlib.util.spec_from_file_location("migracion_0069", ruta)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def _de_0038():
    """`FUNCIONES` y `_funcion` de `0038`, para redefinir `exportar_auditoria` con el mismo
    encabezado y los mismos permisos. `0038` ya está aplicada y no cambia."""
    ruta = Path(__file__).with_name("0038_exportacion_soporte_2c3.py")
    spec = importlib.util.spec_from_file_location("migracion_0038", ruta)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


CAMBIOS_0038 = "'cambios', auditoria.cambios_funcionales(g.antes, g.despues, p.campos))"
CAMBIOS_SIN_CUENTA = (
    "'cambios', nucleo.jsonb_sin_cuenta("
    "auditoria.cambios_funcionales(g.antes, g.despues, p.campos)))"
)


def _auditoria(cambios: str) -> str:
    m = _de_0038()
    cuerpo = m.FUNCIONES["exportar_auditoria"]
    assert cuerpo.count(CAMBIOS_0038) == 1, "la auditoría de 0038 no tiene la forma esperada"
    sql = m._funcion("exportar_auditoria", cuerpo.replace(CAMBIOS_0038, cambios))
    return sql.replace("create function", "create or replace function", 1)


def _nombre(columna: str) -> str:
    return f"nucleo.nombre_para_exportar(c, {columna}, hoy)"


def _texto(columna: str) -> str:
    return f"nucleo.texto_sin_cuenta({columna})"


def _jsonb(columna: str) -> str:
    return f"nucleo.jsonb_sin_cuenta({columna})"


AUXILIAR = """
-- Un `jsonb` sin las cuentas que tenga: `texto_sin_cuenta` (`0069`) sobre su texto. El reemplazo
-- queda adentro de una cadena; si una cuenta fuera un número suelto, el resultado no es JSON y la
-- exportación falla (nunca sale la cuenta).
create function nucleo.jsonb_sin_cuenta(j jsonb) returns jsonb
  language sql immutable strict parallel safe
  set search_path = pg_catalog, pg_temp
  as $$ select cast(nucleo.texto_sin_cuenta(cast(j as text)) as jsonb) $$;
revoke all on function nucleo.jsonb_sin_cuenta(jsonb) from public;
"""

CUERPO = f"""
    hoy := nucleo.hoy_del_consorcio(c);
    return query
      select 'importacion_liquidacion'::text,
             jsonb_build_object('id', x.id, 'documento_id', x.documento_id,
                                'version_documento_id', x.version_documento_id,
                                'subida_por_nombre', {_nombre("x.subida_por")},
                                'creada_en', x.creada_en, 'estado', x.estado,
                                'formato', x.formato, 'periodo', x.periodo,
                                'motivo', {_texto("x.motivo")},
                                'diferencias', {_jsonb("x.diferencias")},
                                'leida_en', x.leida_en,
                                'decidida_por_nombre', {_nombre("x.decidida_por")},
                                'decidida_en', x.decidida_en,
                                'motivo_descarte', {_texto("x.motivo_descarte")},
                                'reemplaza_a', x.reemplaza_a,
                                'motivo_reemplazo', {_texto("x.motivo_reemplazo")})
        from nucleo.importacion_liquidacion x
       where x.consorcio_id = c
       order by x.creada_en, x.id;
    return query
      select 'campo_preliminar'::text,
             jsonb_build_object('id', p.id, 'importacion_id', p.importacion_id,
                                'orden', p.orden, 'tipo', p.tipo, 'clave', {_texto("p.clave")},
                                'valor', {_jsonb("p.valor")}, 'confianza', p.confianza,
                                'origen', p.origen, 'pagina', p.pagina, 'renglon', p.renglon,
                                'fragmento', {_texto("p.fragmento")},
                                'unidad_id', p.unidad_id, 'estado', p.estado,
                                'valor_corregido', {_jsonb("p.valor_corregido")},
                                'confirmado_por_nombre', {_nombre("p.confirmado_por")},
                                'confirmado_en', p.confirmado_en)
        from nucleo.campo_preliminar p
       where p.consorcio_id = c
       order by p.importacion_id, p.orden;
    return query
      select 'correspondencia_grupo'::text,
             jsonb_build_object('id', k.id, 'clave', {_texto("k.clave")},
                                'nombre', {_texto("k.nombre")},
                                'grupo', k.grupo, 'version', k.version,
                                'definida_por_nombre', {_nombre("k.definida_por")},
                                'definida_en', k.definida_en)
        from nucleo.correspondencia_grupo_liquidacion k
       where k.consorcio_id = c
       order by k.clave, k.version;
    return query
      select 'enlace_liquidacion'::text,
             jsonb_build_object('importacion_id', n.importacion_id, 'orden', n.orden,
                                'url', {_texto("n.url")}, 'origen', n.origen,
                                'pagina', n.pagina)
        from nucleo.enlace_liquidacion n
       where n.consorcio_id = c
       order by n.importacion_id, n.orden;
"""


def upgrade() -> None:
    op.execute(AUXILIAR + _de_0069()._funcion(FUNCION, CUERPO) + _auditoria(CAMBIOS_SIN_CUENTA))


def downgrade() -> None:
    op.execute(
        _auditoria(CAMBIOS_0038) + f"drop function nucleo.{FUNCION}(uuid, uuid);\n"
        "drop function nucleo.jsonb_sin_cuenta(jsonb);\n"
    )
