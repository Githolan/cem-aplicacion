"""Soporte deja de escribir los eventos de clase A (corte S2a de
`doc/diseno/Soporte_Fuera_de_la_Auditoria.md` del repositorio CEM).

Decisión del propietario del 26-10-2026: soporte de plataforma es un actor externo al modelo de
usuarios, personas, mandatos y sesiones, y sus intervenciones no forman parte de la auditoría
funcional del consorcio. S1 (`0032`) las sacó de RF35; este corte deja de escribir los eventos
que existían solo para dejar constancia de una intervención de soporte (clase A):

- invitar a un mandato y reemplazar la invitación, revocarla y desasociar a la persona (`0015`),
  finalizar un vínculo (`0020`) y la baja de un mandato por `error_carga` (`0016`): con origen
  `soporte` hacen el cambio sin insertar en `auditoria.eventos`. Con origen `api`, igual que
  antes. La constancia queda en la fila: `motivo_soporte`, `revocada_en`, `motivo_fin` y
  `causa_baja` con su `instrumento_referencia`;
- la identidad humana (`0026`, solo de soporte) deja de llamar a `nucleo.auditar_identidad`,
  que se borra. Su registro propio es `nucleo.verificacion_identidad`.

Sigue igual lo que espera al propietario (clase B, S2b: decisiones, modo, altas y bajas de
mandatos por renuncia o remoción, revisiones y coeficientes) y `anular_vinculo`, que borra la
fila y guarda solo en el evento la referencia y el estado anterior: pasar a borrado lógico es
un corte propio. Los eventos ya escritos no se tocan (la cadena está encadenada por hashes).

Las definiciones se leen de la base (`pg_get_functiondef`), como en `0026`, porque varias
cambiaron en migraciones posteriores a la que las creó. Cada inserción se envuelve entre dos
líneas marcadas `-- S2a`, y el downgrade quita exactamente esas líneas.

Revisión: 0033
Anterior: 0032
"""

import importlib.util
import re
from pathlib import Path

from alembic import op
from sqlalchemy import text

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None

INVITAR = "nucleo.invitar_a_mandato(uuid, text, text, text, bytea, timestamptz, uuid, text)"
REVOCAR = "nucleo.revocar_invitacion_mandato(uuid, uuid, text)"
DESASOCIAR = "nucleo.desasociar_persona_de_mandato(uuid, text, text, uuid, text)"
FINALIZAR = "nucleo.finalizar_vinculo(uuid, text, uuid, text)"
BAJA = "nucleo.baja_mandato(uuid, nucleo.causa_baja, uuid, text, text, uuid, text)"

# Cuántas inserciones en `auditoria.eventos` tiene cada función, y la condición que las deja
# afuera para soporte. La baja solo es clase A por `error_carga`; las demás causas son de S2b.
SIN_SOPORTE = "origen_pedido <> 'soporte'"
ENVOLVER = {
    INVITAR: (2, SIN_SOPORTE),
    REVOCAR: (1, SIN_SOPORTE),
    DESASOCIAR: (1, SIN_SOPORTE),
    FINALIZAR: (1, SIN_SOPORTE),
    BAJA: (1, "not (origen_pedido = 'soporte' and causa = 'error_carga')"),
}
MARCA_SI = "  -- S2a: soporte no escribe eventos de auditoría funcional (decisión del 26-10-2026)\n"
MARCA_FIN = "end if;  -- S2a\n"
INSERCION = re.compile(r"^( *)insert into auditoria\.eventos\b.*?;\n", re.MULTILINE | re.DOTALL)

AUDITAR = "nucleo.auditar_identidad(uuid, text, uuid, uuid, text)"
LLAMADAS = {
    "nucleo.verificar_identidad_humana(uuid, smallint[], bytea[], text, text, text)":
        "    perform nucleo.auditar_identidad(c, 'verificar', nueva, persona, metodo);\n",
    "nucleo.vincular_identidad_humana(uuid, smallint[], bytea[], text, text, text)":
        "    perform nucleo.auditar_identidad(c, 'vincular', existente, persona, metodo);\n",
    "nucleo.desvincular_identidad_humana(uuid, text, text, text)":
        "    perform nucleo.auditar_identidad(c, 'desvincular', actual, persona, null);\n",
    "nucleo.corregir_identidad_humana(uuid, smallint[], bytea[], text, text, text, text)":
        "    perform nucleo.auditar_identidad(c, 'corregir', actual, persona, metodo);\n",
}  # fmt: skip


def _definicion(firma: str) -> str:
    return (
        op.get_bind()
        .execute(text("select pg_get_functiondef(cast(:f as regprocedure))"), {"f": firma})
        .scalar_one()
    )


def _envolver(firma: str, cuantas: int, condicion: str) -> str:
    definicion = _definicion(firma)
    if MARCA_FIN in definicion or len(INSERCION.findall(definicion)) != cuantas:
        raise RuntimeError(f"0033: {firma} no tiene exactamente {cuantas} inserciones sin marcar")

    def envuelta(m: re.Match) -> str:
        sangria = m.group(1)
        return f"{sangria}if {condicion} then{MARCA_SI}{m.group(0)}{sangria}{MARCA_FIN}"

    return INSERCION.sub(envuelta, definicion)


def _desenvolver(firma: str, cuantas: int, condicion: str) -> str:
    definicion = _definicion(firma)
    linea_si = re.compile(rf"^ *if {re.escape(condicion)} then{re.escape(MARCA_SI)}", re.MULTILINE)
    linea_fin = re.compile(rf"^ *{re.escape(MARCA_FIN)}", re.MULTILINE)
    marcadas = (len(linea_si.findall(definicion)), len(linea_fin.findall(definicion)))
    if marcadas != (cuantas, cuantas):
        raise RuntimeError(f"0033: {firma} no tiene exactamente {cuantas} inserciones marcadas")
    return linea_fin.sub("", linea_si.sub("", definicion))


def _auditar_de_0026() -> str:
    """El texto de `nucleo.auditar_identidad` que dejó `0026`, para recrearla en el downgrade
    sin copiarlo a mano."""
    ruta = Path(__file__).with_name("0026_identidad_humana_y_autoinvitacion.py")
    spec = importlib.util.spec_from_file_location(ruta.stem, ruta)
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    desde = modulo.FUNCIONES.index("create function nucleo.auditar_identidad(")
    fin = modulo.FUNCIONES.index("\n  $$;\n", desde) + len("\n  $$;\n")
    return modulo.FUNCIONES[desde:fin]


def upgrade() -> None:
    for firma, (cuantas, condicion) in ENVOLVER.items():
        op.execute(_envolver(firma, cuantas, condicion))
    for firma, llamada in LLAMADAS.items():
        definicion = _definicion(firma)
        if definicion.count(llamada) != 1:
            raise RuntimeError(f"0033: {firma} no llama exactamente una vez a auditar_identidad")
        op.execute(definicion.replace(llamada, ""))
    op.execute(f"drop function {AUDITAR};")


def downgrade() -> None:
    op.execute(_auditar_de_0026())
    op.execute(f"revoke all on function {AUDITAR} from public;")
    for firma, llamada in LLAMADAS.items():
        definicion = _definicion(firma)
        retorno = re.search(r"^    return \w+;\n(?![\s\S]*^    return )", definicion, re.MULTILINE)
        if retorno is None:
            raise RuntimeError(f"0033: {firma} no termina con un return")
        op.execute(definicion[: retorno.start()] + llamada + definicion[retorno.start() :])
    for firma, (cuantas, condicion) in ENVOLVER.items():
        op.execute(_desenvolver(firma, cuantas, condicion))
