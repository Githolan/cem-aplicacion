"""Soporte deja de escribir los eventos de clase B (corte S2b de
`doc/diseno/Soporte_Fuera_de_la_Auditoria.md` del repositorio CEM).

Decisión del propietario del 28-10-2026: cuando la operación técnica la hace soporte de
plataforma, los actos de clase B tampoco generan eventos nuevos en `auditoria.eventos`. Es hacia
adelante: los eventos ya escritos no se tocan (la cadena está encadenada por hashes). Por la API,
igual que antes.

- **Funciones:** `registrar_decision`, `cambiar_modo`, `alta_mandato`, `resolver_revision_mandato`
  y `registrar_esquema_coeficientes` envuelven su inserción en `auditoria.eventos` con
  `origen_pedido <> 'soporte'`, entre líneas marcadas `-- S2b`, como hizo `0033` con la clase A.
  `baja_mandato` ya tenía la marca de S2a para `error_carga`; su condición pasa a ser la misma
  para todas las causas.
- **La evidencia queda en la fila** (condición de la decisión). Cada evento de clase B se arma con
  datos de su fila (`0014`, E31, ya les sacó nombres, CUIT y referencias): la decisión con su
  tipo, fecha, referencia del acta y origen; el modo con su decisión y su vigencia, y el anterior
  cerrado en su propia fila; el mandato con su decisión, vigencia y facultades; la revisión con su
  decisión y desde cuándo rige; el esquema de coeficientes con su versión, decisión, vigencia y
  filas. `cem_app` solo tiene `SELECT` sobre esas tablas; las cambian funciones `security definer`
  que no reescriben historia (estas y, sobre el mandato, la aceptación de su invitación, la
  desasociación, que guarda a la persona anterior, y el vencimiento de `0017`): el modo cierra la
  vigencia del anterior e inserta otro, la baja se niega sobre un mandato ya dado de baja
  (`mandato_ya_dado_de_baja`), y decisiones, revisiones y coeficientes no se actualizan nunca.
- **Lo único que vivía solo en el evento:** el fin previsto del mandato antes de la baja
  (`antes.vigente_hasta`), que la baja reemplaza por la fecha en que termina. Nueva columna
  `nucleo.mandato.vigente_hasta_antes_baja`, que la baja llena en la misma sentencia, con un check
  que la ata a `finalizado_en`. Las bajas por `error_carga` ya hechas se completan al subir: esa
  causa conserva el fin (`hasta_nuevo = vigente_hasta`), y las de soporte desde `0033` no tienen
  evento. Las demás bajas anteriores quedan con nulo: su valor está en su evento.
- **`consorcio` y `unidad`:** el alta del equipo (`scripts/alta_consorcio.py`) deja de escribir sus
  eventos. `cem_app` tenía `UPDATE` y `DELETE` sobre las dos tablas y ningún código los usa: se
  revocan, así nombre, CUIT, zona horaria y designación no se pueden reescribir desde la
  aplicación (el alta sigue con `INSERT`).

No se crea una auditoría paralela de soporte ni una cuenta de soporte, y soporte sigue fuera de
RF35 (`0032`). La restricción `auditoria.eventos.origen` conserva `soporte`: las filas viejas lo
usan.

Downgrade: se niega (`downgrade_con_datos`) si hay una baja que no sea `error_carga` sin su
evento: solo puede ser una de soporte posterior a `0043` (antes, toda baja así escribía evento), y
su fin previo vive solo en la columna, también cuando es nulo (el mandato no tenía fin previsto).

Revisión: 0043
Anterior: 0042
"""

import re

from alembic import op
from sqlalchemy import text

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None

DECISION = "nucleo.registrar_decision(nucleo.tipo_decision, date, text, text, uuid, text, text)"
MODO = "nucleo.cambiar_modo(nucleo.tipo_modo, uuid, date, uuid, text)"
ALTA = (
    "nucleo.alta_mandato(nucleo.tipo_mandato, uuid, text, text, text, uuid, date, date, text[],"
    " uuid, text)"
)
REVISION = "nucleo.resolver_revision_mandato(uuid, uuid, date, uuid, text)"
COEFICIENTES = (
    "nucleo.registrar_esquema_coeficientes(text, text, date, uuid, jsonb, nucleo.metodo_reparto,"
    " nucleo.tratamiento_centavos, uuid, text)"
)
BAJA = "nucleo.baja_mandato(uuid, nucleo.causa_baja, uuid, text, text, uuid, text)"

SIN_SOPORTE = "origen_pedido <> 'soporte'"
ENVOLVER = (DECISION, MODO, ALTA, REVISION, COEFICIENTES)
MARCA_SI = "  -- S2b: soporte no escribe eventos de auditoría funcional (decisión del 28-10-2026)\n"
MARCA_FIN = "end if;  -- S2b\n"
INSERCION = re.compile(r"^( *)insert into auditoria\.eventos\b.*?;\n", re.MULTILINE | re.DOTALL)

CONDICION_S2A = "if not (origen_pedido = 'soporte' and causa = 'error_carga') then"
CONDICION_S2B = "if origen_pedido <> 'soporte' then"
BAJA_ANTES = "set vigente_hasta = hasta_nuevo, finalizado_por = actor,"
BAJA_DESPUES = (
    "set vigente_hasta_antes_baja = m.vigente_hasta, vigente_hasta = hasta_nuevo,\n"
    "           finalizado_por = actor,"
)


def _definicion(firma: str) -> str:
    return (
        op.get_bind()
        .execute(text("select pg_get_functiondef(cast(:f as regprocedure))"), {"f": firma})
        .scalar_one()
    )


def _reemplazar_una_vez(firma: str, definicion: str, antes: str, despues: str) -> str:
    if definicion.count(antes) != 1:
        raise RuntimeError(f"0043: {firma} no tiene exactamente una vez {antes!r}")
    return definicion.replace(antes, despues)


def _envolver(firma: str) -> str:
    definicion = _definicion(firma)
    if MARCA_FIN in definicion or len(INSERCION.findall(definicion)) != 1:
        raise RuntimeError(f"0043: {firma} no tiene exactamente una inserción sin marcar")

    def envuelta(m: re.Match) -> str:
        sangria = m.group(1)
        return f"{sangria}if {SIN_SOPORTE} then{MARCA_SI}{m.group(0)}{sangria}{MARCA_FIN}"

    return INSERCION.sub(envuelta, definicion)


def _desenvolver(firma: str) -> str:
    definicion = _definicion(firma)
    linea_si = re.compile(
        rf"^ *if {re.escape(SIN_SOPORTE)} then{re.escape(MARCA_SI)}", re.MULTILINE
    )
    linea_fin = re.compile(rf"^ *{re.escape(MARCA_FIN)}", re.MULTILINE)
    if (len(linea_si.findall(definicion)), len(linea_fin.findall(definicion))) != (1, 1):
        raise RuntimeError(f"0043: {firma} no tiene exactamente una inserción marcada")
    return linea_fin.sub("", linea_si.sub("", definicion))


def upgrade() -> None:
    op.execute(
        """
alter table nucleo.mandato
  add column vigente_hasta_antes_baja date,
  add constraint mandato_vigente_hasta_antes_baja
    check (vigente_hasta_antes_baja is null or finalizado_en is not null);
comment on column nucleo.mandato.vigente_hasta_antes_baja is
  'Fin previsto antes de la baja (0043, S2b): la baja reemplaza vigente_hasta. En una baja que '
  'no sea error_carga, nulo con evento de baja: el valor está en el evento (anterior a 0043); '
  'nulo sin evento: el mandato no tenía fin previsto';
update nucleo.mandato set vigente_hasta_antes_baja = vigente_hasta
 where causa_baja = 'error_carga' and finalizado_en is not null;
revoke update, delete on nucleo.consorcio, nucleo.unidad from cem_app;
"""
    )
    for firma in ENVOLVER:
        op.execute(_envolver(firma))
    baja = _definicion(BAJA)
    baja = _reemplazar_una_vez(BAJA, baja, CONDICION_S2A, CONDICION_S2B)
    baja = _reemplazar_una_vez(BAJA, baja, BAJA_ANTES, BAJA_DESPUES)
    op.execute(baja)


def downgrade() -> None:
    op.execute(
        """
do $$
begin
  if exists (select 1 from nucleo.mandato m
              where m.finalizado_en is not null
                and m.causa_baja <> 'error_carga'
                and not exists (select 1 from auditoria.eventos e
                                 where e.entidad_id = m.id
                                   and e.accion = 'consorcio.mandato.baja')) then
    raise exception 'el downgrade de 0043 se niega: hay bajas cuyo fin previo vive solo en la fila'
      using errcode = 'P0001', hint = 'downgrade_con_datos',
            detail = 'vigente_hasta_antes_baja es la única constancia de esas bajas de soporte';
  end if;
end
$$;
"""
    )
    baja = _definicion(BAJA)
    baja = _reemplazar_una_vez(BAJA, baja, BAJA_DESPUES, BAJA_ANTES)
    baja = _reemplazar_una_vez(BAJA, baja, CONDICION_S2B, CONDICION_S2A)
    op.execute(baja)
    for firma in ENVOLVER:
        op.execute(_desenvolver(firma))
    op.execute(
        """
grant update, delete on nucleo.consorcio, nucleo.unidad to cem_app;
alter table nucleo.mandato
  drop constraint mandato_vigente_hasta_antes_baja,
  drop column vigente_hasta_antes_baja;
"""
    )
