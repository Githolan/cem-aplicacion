"""Motor de reparto (documento 13, §7.3; documento 08, §8).

Función pura, sin acceso a la base. Distribuye el total de un grupo de gasto entre
unidades según sus pesos:

- aritmética `Decimal` con contexto de 28 dígitos y pesos normalizados en el grupo;
- cada unidad recibe el piso de su parte exacta en centavos;
- los centavos residuales van a los restos mayores, con desempate estable por `unidad_id`;
- la suma distribuida es exactamente el total del grupo (RN05);
- un grupo sin pesos válidos bloquea el reparto (RN04).
"""

from collections.abc import Mapping
from decimal import ROUND_FLOOR, Context, Decimal, localcontext

CENTAVO = Decimal("0.01")
CONTEXTO = Context(prec=28)


class RepartoBloqueado(ValueError):
    """El grupo no puede repartirse; el cierre del período queda bloqueado (RN04)."""


def repartir(total: Decimal, pesos: Mapping[str, Decimal]) -> dict[str, Decimal]:
    """Reparte `total` (pesos con dos decimales; puede ser negativo, p. ej. un crédito)."""
    if not isinstance(total, Decimal) or not total.is_finite():
        raise RepartoBloqueado("el total debe ser un Decimal finito")
    if total != total.quantize(CENTAVO):
        raise RepartoBloqueado("el total debe expresarse en centavos")
    if not pesos:
        raise RepartoBloqueado("el grupo no tiene unidades")
    for unidad, peso in pesos.items():
        if not isinstance(peso, Decimal) or not peso.is_finite() or peso < 0:
            raise RepartoBloqueado(f"peso inválido para la unidad {unidad}")

    with localcontext(CONTEXTO):
        suma = sum(pesos.values(), Decimal(0))
        if suma <= 0:
            raise RepartoBloqueado("el grupo no tiene pesos válidos")

        signo = -1 if total < 0 else 1
        centavos = int((abs(total) / CENTAVO).to_integral_value())
        exactos = {u: Decimal(centavos) * p / suma for u, p in pesos.items()}
        base = {u: int(x.to_integral_value(rounding=ROUND_FLOOR)) for u, x in exactos.items()}

    residuo = centavos - sum(base.values())
    orden = sorted(pesos, key=lambda u: (-(exactos[u] - base[u]), u))
    for u in orden[:residuo]:
        base[u] += 1

    return {u: signo * Decimal(c) * CENTAVO for u, c in base.items()}
