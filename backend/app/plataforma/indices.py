"""Control diario de los índices GiST: lectura secuencial contra índice, y `REINDEX` si difieren
(E58; tracker de CEM).

Con PostgreSQL 18.4 y 18.6 la suite de pruebas dejó, de vez en cuando, la entrada GiST de una fila
viva marcada como muerta (LP_DEAD): un escaneo por ese índice se la saltea, mientras que una
lectura secuencial la ve. Coincide con el fallo de PostgreSQL "GiST: Invalidate killed items
consistently" (19-08-2026), que sale en la versión menor posterior a 18.6. Producción corre 18.6:
hasta actualizar, este control lo vigila todos los días.

Cómo compara, en una sola foto (`REPEATABLE READ`, rol `cem_backup`, que lee todo sin la RLS):

1. Por cada índice GiST de `nucleo`, cuenta las filas de la tabla por cada valor de la primera
   columna del índice (`consorcio_id` en todos los de hoy), con una lectura secuencial. Si el
   índice es parcial, solo las filas de su predicado.
2. Cuenta lo mismo forzando un escaneo por índice (no `index only`), valor por valor. Para que el
   planificador elija ese índice y no un B-tree de la misma columna, la condición suma su columna
   de rango con `<@ '(,)'`, que toda fila cumple (también un rango vacío) y que un B-tree no
   resuelve. El `EXPLAIN` de cada consulta tiene que nombrar el índice controlado: si nombra otro,
   el control no probó nada y falla (`ErrorDefinitivo`), en vez de pasar en silencio.
3. Si algún conteo difiere, reconstruye ese índice con `REINDEX` en una conexión de `cem_app`
   (permiso `MAINTAIN`, migración 0022) y vuelve a comparar. Son índices de restricciones de
   exclusión: PostgreSQL no los reconstruye con `CONCURRENTLY`, así que el `REINDEX` frena las
   escrituras de esa tabla mientras dura, con un tope de espera de bloqueo.

Aviso a Uptime Kuma (`CEM_INDICES_AVISO_URL`, push de un monitor "Push"): `up` si no hubo
diferencias; `down` si las hubo, aunque el `REINDEX` las haya corregido, porque alguien tiene que
enterarse. Si después del `REINDEX` siguen, o no se pudo reconstruir, avisa `down` y el trabajo
falla: agotados los intentos queda muerto y lo ve también el monitor de la cola. Un día sin
control deja al monitor sin latido y se pone rojo solo.
"""

import json
import logging
import os
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import Connection, create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.pool import NullPool

from app.plataforma import cola, copias

log = logging.getLogger("cem.indices")

VARIABLE_BACKUP = copias.VARIABLES["url_backup"]
VARIABLE_AVISO = "CEM_INDICES_AVISO_URL"
"""URL del push del monitor de Kuma del control. Opcional: sin ella el control corre igual."""

ESQUEMAS = ("nucleo",)
TOPE_DE_BLOQUEO = "5s"
"""`lock_timeout` del `REINDEX`: si una transacción larga tiene la tabla, el control falla y se
reintenta, en vez de dejar en cola a todos los que esperan detrás del `REINDEX`."""

LISTAR = text(
    """
    select format('%I.%I', n.nspname, i.relname) as indice, i.relname as nombre,
           format('%I.%I', n.nspname, t.relname) as tabla,
           pg_get_indexdef(x.indexrelid, 1, true) as clave,
           (select pg_get_indexdef(x.indexrelid, k, true)
              from generate_series(2, x.indnkeyatts) k
              join pg_opclass o on o.oid = x.indclass[k - 1]
             where o.opcname = 'range_ops'
             order by k limit 1) as rango,
           pg_get_expr(x.indpred, x.indrelid, true) as predicado
      from pg_index x
      join pg_class i on i.oid = x.indexrelid
      join pg_am a on a.oid = i.relam
      join pg_class t on t.oid = x.indrelid
      join pg_namespace n on n.oid = t.relnamespace
     where a.amname = 'gist' and n.nspname = any(:esquemas)
     order by 1
    """
)

SIN_INDICES = "set local enable_indexscan = off; set local enable_indexonlyscan = off;" \
    " set local enable_bitmapscan = off; set local enable_seqscan = on;"  # fmt: skip
SOLO_INDICE = "set local enable_seqscan = off; set local enable_indexonlyscan = off;" \
    " set local enable_bitmapscan = off; set local enable_indexscan = on;"  # fmt: skip


@dataclass(frozen=True)
class Diferencia:
    indice: str
    tabla: str
    valor: str
    secuencial: int
    por_indice: int


@dataclass(frozen=True)
class Verificacion:
    indices: int
    valores: int
    diferencias: list[Diferencia]


class ErrorIndices(Exception):
    """El índice siguió difiriendo después del `REINDEX`, o no se pudo reconstruir."""


def _indices_en_el_plan(plan: Any) -> set[str]:
    if isinstance(plan, dict):
        propios = {plan["Index Name"]} if "Index Name" in plan else set()
        return propios.union(*(_indices_en_el_plan(v) for v in plan.values()))
    if isinstance(plan, list):
        return set().union(*(_indices_en_el_plan(v) for v in plan))
    return set()


def _verificar_indice(c: Connection, fila: Any) -> tuple[int, list[Diferencia]]:
    c.exec_driver_sql(SIN_INDICES)
    tipo_rango = f"pg_typeof({fila.rango})::text" if fila.rango else "null::text"
    # Un índice parcial solo cubre las filas de su predicado, y el planificador solo lo usa si
    # la consulta lo repite: las dos lecturas llevan el mismo predicado.
    parcial = f"({fila.predicado})" if fila.predicado else "true"
    secuencial = c.exec_driver_sql(
        f"select pg_typeof({fila.clave})::text, ({fila.clave})::text, count(*), {tipo_rango}"
        f" from {fila.tabla} where {parcial} group by 1, 2, 4"
    ).all()
    c.exec_driver_sql(SOLO_INDICE)
    diferencias = []
    for tipo, valor, cantidad, tipo_rango in secuencial:
        condicion = f"{parcial} and ({fila.clave}) = cast(%(v)s as {tipo})"
        if fila.rango:
            condicion += f" and ({fila.rango}) <@ '(,)'::{tipo_rango}"
        consulta = f"select count(*) from {fila.tabla} where {condicion}"
        plan = c.exec_driver_sql(f"explain (format json) {consulta}", {"v": valor}).scalar_one()
        usados = _indices_en_el_plan(json.loads(plan) if isinstance(plan, str) else plan)
        if usados != {fila.nombre}:
            raise cola.ErrorDefinitivo(
                f"no_verificable: para {fila.indice} el planificador usó {sorted(usados)}"
            )
        por_indice = c.exec_driver_sql(consulta, {"v": valor}).scalar_one()
        if por_indice != cantidad:
            diferencias.append(Diferencia(fila.indice, fila.tabla, valor, cantidad, por_indice))
    return len(secuencial), diferencias


def verificar(url_backup: str | URL, esquemas: Sequence[str] = ESQUEMAS) -> Verificacion:
    """Compara cada índice GiST de `esquemas` con su tabla en una sola foto."""
    motor = create_engine(url_backup, poolclass=NullPool)
    try:
        with motor.connect().execution_options(isolation_level="REPEATABLE READ") as c, c.begin():
            filas = c.execute(LISTAR, {"esquemas": list(esquemas)}).all()
            valores, diferencias = 0, []
            for fila in filas:
                n, propias = _verificar_indice(c, fila)
                valores += n
                diferencias += propias
    finally:
        motor.dispose()
    return Verificacion(len(filas), valores, diferencias)


def reconstruir(url_app: str | URL, indices: Sequence[str]) -> None:
    """`REINDEX` de cada índice, fuera de toda transacción y con `cem_app` (`MAINTAIN`)."""
    motor = create_engine(url_app, poolclass=NullPool, isolation_level="AUTOCOMMIT")
    try:
        with motor.connect() as c:
            c.exec_driver_sql(f"set lock_timeout = '{TOPE_DE_BLOQUEO}'")
            for indice in indices:
                c.exec_driver_sql(f"reindex index {indice}")
                log.warning("índice reconstruido: %s", indice)
    finally:
        motor.dispose()


def avisar(estado: str, mensaje: str, entorno: Mapping[str, str] = os.environ) -> str:
    """Push a Kuma. Devuelve `enviado`, `fallido` o `sin_configurar`; nunca lanza: el
    resultado del control no depende del aviso, y un monitor sin latido se pone rojo solo."""
    url = entorno.get(VARIABLE_AVISO)
    if not url:
        log.warning("control de índices sin aviso a Kuma: falta %s", VARIABLE_AVISO)
        return "sin_configurar"
    try:
        copias.empujar(copias.url_push(url, VARIABLE_AVISO, estado, mensaje))
    except copias.ErrorAviso as e:
        log.error("el aviso del control de índices a Kuma falló: %s", e)
        return "fallido"
    return "enviado"


def controlar(
    url_app: str | URL,
    entorno: Mapping[str, str] = os.environ,
    esquemas: Sequence[str] = ESQUEMAS,
) -> dict[str, Any]:
    """El control completo: verificar, reconstruir lo que difiera, volver a verificar y avisar."""
    url_backup = entorno.get(VARIABLE_BACKUP)
    if not url_backup:
        raise cola.ErrorDefinitivo(f"falta {VARIABLE_BACKUP}")
    primera = verificar(url_backup, esquemas)
    resumen = {"indices": primera.indices, "valores": primera.valores}
    if not primera.diferencias:
        mensaje = f"{primera.indices} índices GiST, {primera.valores} valores, sin diferencias"
        return resumen | {"diferencias": [], "aviso": avisar("up", mensaje, entorno)}

    distintos = sorted({d.indice for d in primera.diferencias})
    log.error("índices GiST que no coinciden con su tabla: %s",
              [asdict(d) for d in primera.diferencias])  # fmt: skip
    try:
        reconstruir(url_app, distintos)
    except Exception as e:
        avisar(
            "down", f"no se pudo reconstruir {', '.join(distintos)}: {type(e).__name__}", entorno
        )
        raise ErrorIndices(f"no se pudo reconstruir {distintos}") from e
    segunda = verificar(url_backup, esquemas)
    if segunda.diferencias:
        restantes = sorted({d.indice for d in segunda.diferencias})
        avisar("down", f"siguen las diferencias después del REINDEX: {', '.join(restantes)}",
               entorno)  # fmt: skip
        raise ErrorIndices(f"siguen las diferencias después del REINDEX: {restantes}")
    aviso = avisar("down", f"reparado con REINDEX: {', '.join(distintos)}", entorno)
    return resumen | {
        "diferencias": [asdict(d) for d in primera.diferencias],
        "reconstruidos": distintos,
        "aviso": aviso,
    }
