"""RF35 sin soporte (corte S1 de `doc/diseno/Soporte_Fuera_de_la_Auditoria.md` del repositorio CEM).

Decisión del propietario del 26-10-2026: soporte de plataforma es un actor externo al modelo de
usuarios, personas, mandatos y sesiones, y sus intervenciones no forman parte de la auditoría
funcional del consorcio. Este corte saca a soporte de lo que ven los usuarios:

- `auditoria.eventos_del_consorcio` y `auditoria.eventos_propios` excluyen `origen = 'soporte'`
  en las dos mitades del `union all` (la página `antes_de` y la de `saltar`), así la paginación no
  se corre;
- desaparecen las ramas `soporte` del origen y del actor que devuelven.

No toca los eventos ya escritos (la cadena está encadenada por hashes) ni las funciones que los
escriben: dejar de escribirlos es S2a y S2b. Las dos funciones se reemplazan con el texto de
`0028` leído de esa migración, con los cambios de arriba; el downgrade vuelve a ese texto.

Revisión: 0032
Anterior: 0031
"""

import importlib.util
from pathlib import Path

from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None

FIN_FUNCION = "\n  $$;\n"
PROYECTADO = "and exists (select 1 from auditoria.proyeccion p where p.accion = e.accion)"
SIN_SOPORTE = PROYECTADO + "\n            and e.origen <> 'soporte'"
ORIGEN_0028 = (
    "case g.origen when 'api' then 'aplicacion' when 'soporte' then 'soporte'\n"
    "                           else 'sistema' end"
)
ORIGEN_0032 = "case g.origen when 'api' then 'aplicacion' else 'sistema' end"
ACTOR_0028 = (
    "case when g.origen = 'soporte' then 'soporte'\n                  when g.origen <> 'api'"
)
ACTOR_0032 = "case when g.origen <> 'api'"


def _funcion_de_0028(nombre: str) -> str:
    """El texto de la función que dejó `0028`, como `create or replace`: la base del cambio y lo
    que restaura el downgrade, sin copiarlo a mano."""
    ruta = Path(__file__).with_name("0028_auditoria_propia_rf35b.py")
    spec = importlib.util.spec_from_file_location(ruta.stem, ruta)
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    inicio = f"create function auditoria.{nombre}("
    desde = modulo.FUNCIONES.index(inicio)
    fin = modulo.FUNCIONES.index(FIN_FUNCION, desde) + len(FIN_FUNCION)
    texto = modulo.FUNCIONES[desde:fin]
    return texto.replace("create function", "create or replace function", 1)


def _sin_soporte(nombre: str, con_actor: bool) -> str:
    texto = _funcion_de_0028(nombre)
    assert texto.count(PROYECTADO) == 2 and texto.count(ORIGEN_0028) == 1
    texto = texto.replace(PROYECTADO, SIN_SOPORTE).replace(ORIGEN_0028, ORIGEN_0032)
    if con_actor:
        assert texto.count(ACTOR_0028) == 1
        texto = texto.replace(ACTOR_0028, ACTOR_0032)
    assert "'soporte' then" not in texto
    return texto


def upgrade() -> None:
    op.execute(_sin_soporte("eventos_propios", con_actor=False))
    op.execute(_sin_soporte("eventos_del_consorcio", con_actor=True))


def downgrade() -> None:
    op.execute(_funcion_de_0028("eventos_propios"))
    op.execute(_funcion_de_0028("eventos_del_consorcio"))
