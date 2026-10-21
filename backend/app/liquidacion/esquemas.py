"""Reparto según un esquema de coeficientes y su regla (D4, RF05; migración 0021).

Los porcentajes jurídicos se usan tal como están guardados (pueden sumar 99,96 %): la regla
`proporcional_a_coeficientes` los toma como pesos y el motor (`reparto.repartir`) distribuye
exactamente el total, con los centavos residuales por restos mayores y desempate por
`unidad_id`. Nunca se ajusta un coeficiente para que la suma cierre.
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
