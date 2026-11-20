"""Recálculo de una liquidación importada con los coeficientes del edificio (H5-4, diseño H5 §5).

Función pura, sin acceso a la base: recibe los datos oficiales de la importación (los confirmados
o corregidos de una importación confirmada, RN30) y la base que da `0116` (por cada grupo con
correspondencia, el esquema vigente en el período y el coeficiente de cada unidad activa), y
devuelve, grupo por grupo, lo que informó la administración frente a lo que da el reparto.

- **Qué se reparte:** el gasto del grupo según la liquidación (`total:gastos`, `grupos`), con el
  mismo motor que la liquidación propia (`reparto.repartir`: proporcional al coeficiente, centavos
  por restos mayores).
- **Qué se compara:** el monto de cada unidad en ese grupo (`importe_unidad`, `grupos`).
- **Cuándo se señala (D5-A):** la diferencia pasa de un centavo, que es lo más que puede separar a
  dos maneras correctas de repartir los centavos.
- **Lo que no se puede recalcular** lo dice el estado del grupo; no se completa nada.

Dice la diferencia y su base; no la califica (RN33).
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from cem_contracts.importacion_liquidacion import (
    EstadoGrupoRecalculado,
    GrupoRecalculado,
    UnidadRecalculada,
)

from app.liquidacion.reparto import repartir

CENTAVO = Decimal("0.01")
"""D5-A: se señala la diferencia que pasa de un centavo."""


@dataclass(frozen=True)
class Oficial:
    tipo: str
    clave: str
    valor: Mapping[str, Any]
    unidad_id: UUID | None


@dataclass(frozen=True)
class Base:
    clave: str
    grupo: str
    esquema_id: UUID | None
    esquema_version: int | None
    vigente_desde: date | None
    unidad_id: UUID | None
    designacion: str | None
    porcentaje: Decimal | None


def clave_de(nombre: str) -> str:
    """Como `nucleo.clave_designacion`: espacios juntados y sin mayúsculas."""
    return re.sub(r"\s+", " ", nombre.strip()).lower()


def _decimal(texto: Any) -> Decimal | None:
    return None if texto is None else Decimal(str(texto))


def _designacion(valor: Mapping[str, Any]) -> str:
    partes = [f"UF {valor['uf']}" if valor.get("uf") else "", str(valor.get("depto") or "")]
    return " ".join(p for p in partes if p) or "Sin designación"


def nombres_de_grupos(oficiales: Sequence[Oficial]) -> list[str]:
    """Los grupos que informa la liquidación, por nombre: el orden del documento no llega
    (`jsonb` no guarda el orden de las claves)."""
    nombres: set[str] = set()
    for o in oficiales:
        if o.tipo in ("total", "importe_unidad") and isinstance(o.valor.get("grupos"), dict):
            nombres.update(o.valor["grupos"])
    return sorted(nombres, key=lambda n: (clave_de(n), n))


def _vacio(nombre: str, estado: EstadoGrupoRecalculado, base: Base | None) -> GrupoRecalculado:
    return GrupoRecalculado(
        nombre=nombre, estado=estado, grupo=base.grupo if base else None,
        esquema_version=base.esquema_version if base else None,
        vigente_desde=base.vigente_desde if base else None, total=None, unidades=[], senaladas=0,
    )  # fmt: skip


def _grupo(nombre: str, oficiales: Sequence[Oficial], filas: Sequence[Base]) -> GrupoRecalculado:
    if not filas:
        return _vacio(nombre, EstadoGrupoRecalculado.SIN_CORRESPONDENCIA, None)
    cabeza = filas[0]
    if cabeza.esquema_id is None:
        return _vacio(nombre, EstadoGrupoRecalculado.SIN_ESQUEMA, cabeza)
    gastos = next((o for o in oficiales if o.tipo == "total" and o.clave == "total:gastos"), None)
    total = _decimal((gastos.valor.get("grupos") or {}).get(nombre)) if gastos else None
    if total is None:
        return _vacio(nombre, EstadoGrupoRecalculado.SIN_TOTAL, cabeza)
    coeficientes = {f.unidad_id: f for f in filas if f.unidad_id is not None}
    if (
        not coeficientes
        or any(f.porcentaje is None for f in coeficientes.values())
        or sum(f.porcentaje or Decimal(0) for f in coeficientes.values()) <= 0
    ):
        return _vacio(nombre, EstadoGrupoRecalculado.COEFICIENTES_INCOMPLETOS, cabeza)
    pesos = {str(u): f.porcentaje or Decimal(0) for u, f in coeficientes.items()}
    esperado = {UUID(u): importe for u, importe in repartir(total, pesos).items()}

    # Lo informado, por unidad del padrón; las filas que no coinciden con una unidad activa van
    # aparte, con lo que informan.
    informado: dict[UUID, tuple[Decimal | None, Decimal]] = {}
    aparte: list[UnidadRecalculada] = []
    for o in oficiales:
        parte = (o.valor.get("grupos") or {}).get(nombre) if o.tipo == "importe_unidad" else None
        if not isinstance(parte, dict):
            continue
        monto = _decimal(parte.get("monto")) or Decimal(0)
        porcentaje = _decimal(parte.get("porcentaje"))
        if o.unidad_id in coeficientes:
            antes = informado.get(o.unidad_id)
            informado[o.unidad_id] = (porcentaje, monto + (antes[1] if antes else Decimal(0)))
        else:
            aparte.append(UnidadRecalculada(
                unidad_id=o.unidad_id, designacion=_designacion(o.valor), coeficiente=None,
                porcentaje_informado=porcentaje, esperado=None, informado=monto, diferencia=None,
                senalada=abs(monto) > CENTAVO,
            ))  # fmt: skip

    unidades: list[UnidadRecalculada] = []
    for u, fila in sorted(coeficientes.items(), key=lambda par: par[1].designacion or ""):
        porcentaje, monto = informado.get(u, (None, None))
        diferencia = (monto if monto is not None else Decimal(0)) - esperado[u]
        unidades.append(UnidadRecalculada(
            unidad_id=u, designacion=fila.designacion or "", coeficiente=fila.porcentaje,
            porcentaje_informado=porcentaje, esperado=esperado[u], informado=monto,
            diferencia=diferencia, senalada=abs(diferencia) > CENTAVO,
        ))  # fmt: skip
    unidades.extend(aparte)
    return GrupoRecalculado(
        nombre=nombre, estado=EstadoGrupoRecalculado.RECALCULADO, grupo=cabeza.grupo,
        esquema_version=cabeza.esquema_version, vigente_desde=cabeza.vigente_desde, total=total,
        unidades=unidades, senaladas=sum(1 for x in unidades if x.senalada),
    )  # fmt: skip


def recalcular(oficiales: Sequence[Oficial], base: Sequence[Base]) -> list[GrupoRecalculado]:
    """Cada grupo de la liquidación recalculado, o con el motivo por el que no se puede."""
    por_clave: dict[str, list[Base]] = {}
    for b in base:
        por_clave.setdefault(b.clave, []).append(b)
    return [
        _grupo(n, oficiales, por_clave.get(clave_de(n), [])) for n in nombres_de_grupos(oficiales)
    ]
