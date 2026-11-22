"""Bandeja y correo del Consejo (RF40; diseño P0 S6) y el aviso de vencimiento de las solicitudes
formales (RF49, §5 de su diseño).

**La bandeja es el inicio** (decisión 9 del 18-11-2026): cada fila lee lo que ya informa la API de
su pantalla. Lo único nuevo en la API es `por_vencer` en cada solicitud formal, con la regla de este
módulo: la misma que usa el correo.

**El correo** va dentro del aviso diario que ya existe (`avisos_pagos`, `pagos.avisar_pendientes`):
un renglón por edificio y por cosa para hacer, con cuántas son y el enlace al inicio. Nunca un texto
de reclamo, una unidad, una persona, un importe ni una cuenta: la carga de
`nucleo.pendientes_consejo_para_avisar` (`0126`) ni siquiera los trae. Comparte el tope por persona
y la ventana del planificador: nadie recibe un correo de más por esto.

**Qué se avisa y a quién** (la misma persona que lo ve en el inicio y puede resolverlo):

- las liquidaciones del administrador que esperan confirmación, a quien tiene
  `liquidacion.importar`;
- las solicitudes formales enviadas que vencen dentro de `DIAS_AVISO_VENCIMIENTO` días, y las que
  vencieron sin respuesta hace `DIAS_AVISO_VENCIMIENTO` días o menos, a quien firma
  (`solicitud_formal.firmar`). Una vencida sigue en el inicio hasta que se registra la respuesta,
  pero el correo deja de repetirla: el aviso es el del vencimiento;
- los reclamos sobre la liquidación que esperan respuesta, a quien los atiende (`reclamos.atender`);
- los pedidos de reparación que esperan responsable, a quien gestiona (`reparaciones.gestionar`).
  Las reparaciones en curso se ven en el inicio y no se repiten por correo: ya tienen responsable.
"""

from datetime import date
from typing import Any

from cem_contracts.fiscalizacion import DIAS_AVISO_VENCIMIENTO, EstadoSolicitudFormal
from sqlalchemy import Connection, text

RUTA_INICIO = "/"
"""La bandeja: el inicio del frontend (`RUTA_INICIO` en `frontend/src/enlace.ts`)."""

QUE_HACER = {
    "importaciones": (
        "una liquidación del administrador espera confirmación",
        "{n} liquidaciones del administrador esperan confirmación",
    ),
    "por_vencer": (
        "una solicitud formal vence en {dias} días o menos",
        "{n} solicitudes formales vencen en {dias} días o menos",
    ),
    "vencidas": (
        "una solicitud formal venció sin respuesta",
        "{n} solicitudes formales vencieron sin respuesta",
    ),
    "reclamos": ("un reclamo espera respuesta", "{n} reclamos esperan respuesta"),
    "reparaciones": (
        "un pedido de reparación espera responsable",
        "{n} pedidos de reparación esperan responsable",
    ),
}
"""El orden de los renglones en el correo."""


def por_vencer(estado: EstadoSolicitudFormal, vence_el: date | None, hoy: date) -> bool:
    """Enviada y vence dentro de `DIAS_AVISO_VENCIMIENTO` días, contando el día del consorcio.
    Vencida (la base la calcula con `vence_el < hoy`) ya no está por vencer."""
    return (
        estado is EstadoSolicitudFormal.ENVIADA
        and vence_el is not None
        and (vence_el - hoy).days <= DIAS_AVISO_VENCIMIENTO
    )


def vencio_hace_poco(estado: EstadoSolicitudFormal, vence_el: date | None, hoy: date) -> bool:
    """Vencida sin respuesta desde hace `DIAS_AVISO_VENCIMIENTO` días o menos: el correo del
    vencimiento sale esos días (cubre una corrida perdida o un día sin cupo del tope) y después
    no se repite."""
    return (
        estado is EstadoSolicitudFormal.VENCIDA
        and vence_el is not None
        and (hoy - vence_el).days <= DIAS_AVISO_VENCIMIENTO
    )


def cuentas_de(fila: dict[str, Any]) -> dict[str, int]:
    """De una fila de `pendientes_consejo_para_avisar`, lo que hay para avisar (sin ceros)."""
    hoy: date = fila["hoy"]
    cuentas = {
        "importaciones": fila["importaciones"] or 0,
        "reclamos": fila["reclamos"] or 0,
        "reparaciones": fila["reparaciones"] or 0,
        "por_vencer": 0,
        "vencidas": 0,
    }
    for s in fila["solicitudes"] or []:
        estado = EstadoSolicitudFormal(s["estado"])
        vence = date.fromisoformat(s["vence_el"])
        if por_vencer(estado, vence, hoy):
            cuentas["por_vencer"] += 1
        elif vencio_hace_poco(estado, vence, hoy):
            cuentas["vencidas"] += 1
    return {que: n for que, n in cuentas.items() if n > 0}


def leer(conn: Connection) -> list[dict[str, Any]]:
    """Cada persona y edificio con algo del Consejo para avisar, con sus cuentas."""
    filas = conn.execute(
        text("select * from nucleo.pendientes_consejo_para_avisar()")
    ).mappings().all()  # fmt: skip
    leidas = []
    for fila in filas:
        cuentas = cuentas_de(dict(fila))
        if cuentas:
            leidas.append({**dict(fila), "cuentas": cuentas})
    return leidas


def cuantas(que: str, n: int) -> str:
    una, varias = QUE_HACER[que]
    return (una if n == 1 else varias).format(n=n, dias=DIAS_AVISO_VENCIMIENTO)


def partes(cuentas: dict[str, int]) -> list[str]:
    """Lo de un edificio, en el orden de `QUE_HACER`."""
    return [cuantas(que, cuentas[que]) for que in QUE_HACER if cuentas.get(que, 0) > 0]
