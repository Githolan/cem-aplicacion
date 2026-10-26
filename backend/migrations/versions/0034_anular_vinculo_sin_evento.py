"""`anular_vinculo` por soporte deja de escribir eventos (`doc/diseno/Anular_Vinculo_Sin_Evento.md`
del repositorio CEM, opción C).

Decisión del propietario del 26-10-2026: soporte de plataforma es un actor externo al modelo de
usuarios, personas, mandatos y sesiones, y sus intervenciones no forman parte de la auditoría
funcional del consorcio. `0033` dejó de escribir los demás eventos de clase A; este corte hace lo
mismo con `nucleo.anular_vinculo`, que había quedado afuera porque borra la fila.

Con origen `soporte`, la función valida igual, borra la fila y retira las invitaciones pendientes
que nombran al vínculo (`revocada_en`), pero ya no inserta `consorcio.vinculo.anular` ni
`identidad.invitacion.revocar`. La base no conserva rastro de la anulación: la constancia queda
en el respaldo que el equipo conserva y en la salida de `scripts/anular_vinculo.py`. No se crea
ningún registro nuevo. Con origen `api`, igual que antes.

Sin cambio de firma (`returns void`): `create or replace` conserva el dueño y los permisos de
`0025`. La definición se lee de la base (`pg_get_functiondef`), como en `0033`; cada inserción
se envuelve entre dos líneas marcadas `-- 0034`, y el downgrade quita exactamente esas líneas.

Revisión: 0034
Anterior: 0033
"""

import re

from alembic import op
from sqlalchemy import text

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None

ANULAR = "nucleo.anular_vinculo(uuid, text, text, uuid, text)"
# La del evento de cada invitación retirada (dentro del bucle) y la del vínculo.
INSERCIONES = 2
CONDICION = "origen_pedido <> 'soporte'"
MARCA_SI = "  -- 0034: soporte no escribe eventos (decisión del 26-10-2026)\n"
MARCA_FIN = "end if;  -- 0034\n"
INSERCION = re.compile(r"^( *)insert into auditoria\.eventos\b.*?;\n", re.MULTILINE | re.DOTALL)


def _definicion() -> str:
    return (
        op.get_bind()
        .execute(text("select pg_get_functiondef(cast(:f as regprocedure))"), {"f": ANULAR})
        .scalar_one()
    )


def upgrade() -> None:
    definicion = _definicion()
    if MARCA_FIN in definicion or len(INSERCION.findall(definicion)) != INSERCIONES:
        raise RuntimeError(
            f"0034: {ANULAR} no tiene exactamente {INSERCIONES} inserciones sin marcar"
        )

    def envuelta(m: re.Match) -> str:
        sangria = m.group(1)
        return f"{sangria}if {CONDICION} then{MARCA_SI}{m.group(0)}{sangria}{MARCA_FIN}"

    op.execute(INSERCION.sub(envuelta, definicion))


def downgrade() -> None:
    definicion = _definicion()
    linea_si = re.compile(rf"^ *if {re.escape(CONDICION)} then{re.escape(MARCA_SI)}", re.MULTILINE)
    linea_fin = re.compile(rf"^ *{re.escape(MARCA_FIN)}", re.MULTILINE)
    marcadas = (len(linea_si.findall(definicion)), len(linea_fin.findall(definicion)))
    if marcadas != (INSERCIONES, INSERCIONES):
        raise RuntimeError(
            f"0034: {ANULAR} no tiene exactamente {INSERCIONES} inserciones marcadas"
        )
    op.execute(linea_fin.sub("", linea_si.sub("", definicion)))
