"""Condición habilitante para integrar el Consejo (E62; decisión 4 del propietario, 21-10-2026).

Diseño: `doc/diseno/Decisiones_Propietario_2026-10-21.md` del repositorio CEM, §2.1.

Un mandato de `miembro_consejo` concede algo solo mientras la persona cumple, sin interrupción,
alguna **condición habilitante para integrar el Consejo**. Si deja de cumplirla, el mandato
queda **sin efecto operativo** y pendiente de revisión: no se borra ni se da de baja, conserva
su historia y no autoriza actos nuevos. La continuidad la resuelve la decisión competente (hoy:
la baja del mandato con su causa y respaldo de C6 y, si corresponde, un alta nueva).

- **Qué cuenta como condición** (definición 1): un vínculo en una unidad activa, de un carácter
  que figure en `nucleo.condicion_habilitante_consejo`, y aceptado o confirmado. El código no
  nombra ningún carácter: el catálogo dice cuáles admite la norma, con su fuente. Hoy,
  `propietario` (CCyC, art. 2064). "Aceptado o confirmado" es la regla de la decisión 2: un
  vínculo habilita recién con una aceptación (fuente `invitacion`, que nace de la persona al
  aceptar) o una confirmación válida (`confirmado_en`). Uno importado, declarado o cargado por
  documento, sin confirmar, no cuenta.
- **Cómo se detecta la pérdida** (definición 2): se calcula en cada pedido a partir de los
  vínculos del padrón; no hay ninguna marca guardada. `nucleo.condicion_consejo_sin_interrupcion`
  une las vigencias de esos vínculos y la recorta a `[inicio del mandato, fecha]`: la condición
  se cumple si queda **un solo tramo que llega hasta la fecha**. Un hueco, aunque después la
  persona vuelva a cumplirla, deja dos tramos: el mandato sigue pendiente de revisión y no se
  reactiva solo. Dos vínculos contiguos (vende una unidad y compra otra el mismo día) no dejan
  hueco. Antes de cumplirla por primera vez dentro del mandato, el mandato tampoco opera.
- **Qué queda sin efecto** (definición 3): todo el mandato. `nucleo.mandato_vigente`, el único
  lugar de la validez material (0012), suma la condición para `miembro_consejo`; las seis
  barreras que conceden algo ya pasan por ahí:
  - el ámbito y las facultades de `require_scope` (`app/contexto.py`);
  - `consorcios_con_mandato_vigente`;
  - la lectura del padrón (0014);
  - `habilitado_para_mandatos` (0015);
  - `tiene_facultad_vigente` (0018);
  - el cierre de sesiones de `finalizar_vinculo` (0020): quien se queda solo con un mandato
    pendiente pierde sus sesiones.

  Los demás tipos de mandato no cambian.
- **Negar ante la duda:** sin catálogo, sin vínculo aceptado o confirmado, o con la unidad o la
  persona dadas de baja, la condición no se cumple. La condición es `security definer`: da lo
  mismo para cualquiera que pregunte. Quien no ve el mandato por la RLS recibe `falso` de
  `mandato_vigente`, igual que antes.
- **Lectura:** `mandatos_del_consorcio` y `mandato_detalle` devuelven el estado
  `pendiente_revision` para un mandato vigente por sus fechas que no opera. Las dos funciones
  salen del texto de 0018, con un solo cambio.
- **Lo que no cambia:** la fila del mandato, sus facultades, sus bajas y los actos que registró
  antes (decisiones, altas, auditoría). La baja de un mandato pendiente sigue disponible: no mira
  `mandato_vigente`.

Downgrade: vuelve a las funciones de 0012 y 0018 y borra el catálogo. No se niega: no hay datos
nuevos que perder, solo una regla.

Revisión: 0023
Anterior: 0022
"""

import importlib.util
from pathlib import Path

from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None

FUENTE_PROPIETARIO = (
    "Código Civil y Comercial, art. 2064: la asamblea puede designar un consejo integrado por"
    " propietarios"
)
CONDICION = "nucleo.condicion_consejo_sin_interrupcion(uuid, date)"
ESTADO = "nucleo.estado_operativo(uuid, text, date)"


def _funcion_de(archivo: str, constante: str, inicio: str) -> str:
    """El texto de una función tal como la dejó una migración anterior, en `create or replace`:
    es la base del cambio y lo que restaura el downgrade, sin copiarlo a mano."""
    ruta = Path(__file__).with_name(archivo)
    spec = importlib.util.spec_from_file_location(ruta.stem, ruta)
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    texto = getattr(modulo, constante)
    desde = texto.index(inicio)
    fin = texto.index("\n  $$;\n", desde) + len("\n  $$;\n")
    return texto[desde:fin].replace("create function", "create or replace function", 1)


def _vigente_de_0012() -> str:
    return _funcion_de(
        "0012_mandatos_baja_y_administrador_unico.py",
        "UPGRADE",
        "create function nucleo.mandato_vigente(",
    )


def _lecturas_de_0018() -> list[str]:
    return [
        _funcion_de("0018_mandatos_lectura_y_destinatario.py", "FUNCIONES", inicio)
        for inicio in (
            "create function nucleo.mandatos_del_consorcio(",
            "create function nucleo.mandato_detalle(",
        )
    ]


VIGENTE_0012 = """\
                      and daterange(m.vigente_desde, m.vigente_hasta) @> fecha)
"""
VIGENTE_0023 = """\
                      and daterange(m.vigente_desde, m.vigente_hasta) @> fecha
                      -- 0023 (E62): el Consejo opera solo con su condición habilitante.
                      and (m.tipo <> 'miembro_consejo'
                           or nucleo.condicion_consejo_sin_interrupcion(m.id, fecha)))
"""
ESTADO_0018 = "nucleo.estado_mandato(m.causa_baja, m.vigente_desde, m.vigente_hasta, hoy)"
ESTADO_0023 = (
    "nucleo.estado_operativo(m.id,\n"
    "               nucleo.estado_mandato(m.causa_baja, m.vigente_desde, m.vigente_hasta, hoy),"
    " hoy)"
)


def _vigente_0023() -> str:
    texto = _vigente_de_0012()
    assert texto.count(VIGENTE_0012) == 1
    return texto.replace(VIGENTE_0012, VIGENTE_0023)


def _lecturas_0023() -> list[str]:
    lecturas = _lecturas_de_0018()
    assert all(t.count(ESTADO_0018) == 1 for t in lecturas)
    return [t.replace(ESTADO_0018, ESTADO_0023) for t in lecturas]


CATALOGO = f"""
create table nucleo.condicion_habilitante_consejo (
  tipo   nucleo.tipo_vinculo primary key,
  fuente text not null check (length(btrim(fuente)) between 1 and 300)
);
comment on table nucleo.condicion_habilitante_consejo is
  'Caracteres de vínculo que la norma admite como condición para integrar el Consejo (E62).';
insert into nucleo.condicion_habilitante_consejo (tipo, fuente)
values ('propietario', '{FUENTE_PROPIETARIO}');
grant select on nucleo.condicion_habilitante_consejo to cem_app;

-- E62. ¿Cumple la persona del mandato, sin interrupción desde que la cumplió por primera vez
-- dentro del mandato y hasta `fecha`, alguna condición habilitante? Calculado del padrón en
-- cada llamada, sin marcas guardadas. `range_agg` une los tramos contiguos; la intersección con
-- `[inicio, fecha]` deja afuera lo anterior al mandato. Sin filas, falso.
-- `security definer`: es un hecho del padrón y da lo mismo para cualquiera que pregunte. Con
-- los permisos de quien llama, la RLS de `persona` o de `vinculo` le escondería filas a un
-- lector y no a otro, y el mismo mandato tendría dos estados. Solo devuelve un booleano, y
-- `mandato_vigente` la llama únicamente para mandatos que quien pregunta ya puede ver.
create function {CONDICION.replace("(uuid, date)", "(mandato uuid, fecha date)")}
  returns boolean
  language sql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
    select coalesce((
      select not isempty(t.cobertura)
             and t.cobertura = datemultirange(daterange(lower(t.cobertura), fecha + 1))
        from (select range_agg(daterange(v.vigente_desde, v.vigente_hasta))
                       * datemultirange(daterange(m.vigente_desde, fecha + 1)) as cobertura
                from nucleo.mandato m
                join nucleo.vinculo v
                  on v.consorcio_id = m.consorcio_id and v.persona_id = m.persona_id
                join nucleo.condicion_habilitante_consejo h on h.tipo = v.tipo
                join nucleo.unidad u
                  on u.consorcio_id = v.consorcio_id and u.id = v.unidad_id
                 and u.eliminado_en is null
                join nucleo.persona pe on pe.id = v.persona_id and pe.eliminado_en is null
               where m.id = mandato
                 and (v.confirmado_en is not null or v.fuente = 'invitacion')
               group by m.vigente_desde) t), false)
  $$;

-- Para leer: un mandato vigente por sus fechas que no opera queda pendiente de revisión.
create function {ESTADO.replace("(uuid, text, date)", "(mandato uuid, estado text, hoy date)")}
  returns text
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select case when estado = 'vigente' and not nucleo.mandato_vigente(mandato, hoy)
                then 'pendiente_revision' else estado end
  $$;

revoke all on function {CONDICION}, {ESTADO} from public;
grant execute on function {CONDICION} to cem_app;
"""


def upgrade() -> None:
    op.execute(CATALOGO)
    op.execute(_vigente_0023())
    for lectura in _lecturas_0023():
        op.execute(lectura)


def downgrade() -> None:
    op.execute(_vigente_de_0012())
    for lectura in _lecturas_de_0018():
        op.execute(lectura)
    op.execute(
        f"drop function {ESTADO};\n"
        f"drop function {CONDICION};\n"
        "drop table nucleo.condicion_habilitante_consejo;\n"
    )
