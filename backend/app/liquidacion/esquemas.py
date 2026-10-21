"""Reparto según un esquema de coeficientes y su regla (D4, RF05; migración 0021).

Los porcentajes jurídicos se usan tal como están guardados (pueden sumar 99,96 %): la regla
`proporcional_a_coeficientes` los toma como pesos y el motor (`reparto.repartir`) distribuye
exactamente el total, con los centavos residuales por restos mayores y desempate por
`unidad_id`. Nunca se ajusta un coeficiente para que la suma cierre.

PA02: si el esquema ya no coincide con las unidades activas del consorcio (una unidad nueva sin
coeficiente, o un coeficiente de una unidad dada de baja), el reparto se bloquea con la lista;
hace falta una versión nueva del esquema. Nunca se reparte en silencio entre las que quedan.
"""

from decimal import Decimal
from uuid import UUID

from sqlalchemy import Connection, text

from app.liquidacion.reparto import RepartoBloqueado, repartir

METODO = "proporcional_a_coeficientes"
CENTAVOS = "restos_mayores"


def repartir_por_esquema(conn: Connection, esquema_id: UUID, total: Decimal) -> dict[UUID, Decimal]:
    """Reparte `total` entre las unidades del esquema con su última regla. La conexión es la del
    ámbito del consorcio: la RLS no deja ver esquemas de otro."""
    regla = conn.execute(
        text(
            "select metodo::text, centavos::text from nucleo.regla_reparto"
            " where esquema_id = :e order by version desc limit 1"
        ),
        {"e": esquema_id},
    ).one_or_none()
    if regla is None:
        raise RepartoBloqueado("el esquema no existe o no tiene regla de reparto")
    if (regla.metodo, regla.centavos) != (METODO, CENTAVOS):
        raise RepartoBloqueado(f"regla no soportada: {regla.metodo}, {regla.centavos}")
    desajustes = conn.execute(
        text(
            "select u.designacion, (c.unidad_id is null) as sin_coeficiente"
            " from nucleo.esquema_coeficientes s"
            " join nucleo.unidad u on u.consorcio_id = s.consorcio_id"
            " left join nucleo.coeficiente_juridico c"
            "   on c.esquema_id = s.id and c.unidad_id = u.id"
            " where s.id = :e and ((u.eliminado_en is null and c.unidad_id is null)"
            "   or (u.eliminado_en is not null and c.unidad_id is not null))"
            " order by u.designacion"
        ),
        {"e": esquema_id},
    ).all()
    if desajustes:
        faltan = [f.designacion for f in desajustes if f.sin_coeficiente]
        de_baja = [f.designacion for f in desajustes if not f.sin_coeficiente]
        raise RepartoBloqueado(
            "el esquema no coincide con las unidades activas; hace falta una versión nueva"
            f" (sin coeficiente: {', '.join(faltan) or 'ninguna'};"
            f" dadas de baja: {', '.join(de_baja) or 'ninguna'})"
        )
    pesos = {
        str(f.unidad_id): f.porcentaje
        for f in conn.execute(
            text(
                "select unidad_id, porcentaje from nucleo.coeficiente_juridico"
                " where esquema_id = :e"
            ),
            {"e": esquema_id},
        )
    }
    return {UUID(u): importe for u, importe in repartir(total, pesos).items()}
