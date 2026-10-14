"""Cola de trabajos en PostgreSQL (documento 13, §12.1; AD-16).

Un trabajo se encola en la misma transacción que el cambio que lo produce: si esa
transacción se revierte, el trabajo no existe. `cem-trabajos` los toma con
`SELECT ... FOR UPDATE SKIP LOCKED` (en `plataforma.tomar_trabajos`), los ejecuta con el
consorcio del trabajo fijado para la RLS y reintenta con retroceso exponencial hasta el
tope de su tipo; al agotarlo el trabajo queda `muerto` y `/api/v1/health/cola` lo muestra.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, text

from app.registros import redactar


@dataclass(frozen=True)
class Trabajo:
    id: UUID
    tipo: str
    consorcio_id: UUID | None
    carga: dict[str, Any]
    intentos: int
    maximo_intentos: int


Manejador = Callable[[Connection, Trabajo], dict[str, Any] | None]
"""Recibe la conexión con el consorcio del trabajo fijado y la transacción abierta.

Lo que escriba en la base queda solo si el trabajo termina bien. Un efecto externo
(correo, WhatsApp) debe ser idempotente con `trabajo.id` o la clave del evento: si el
trabajador muere después de enviarlo y antes de confirmar, el trabajo se repite.
"""


class ErrorDefinitivo(Exception):
    """El trabajo no tiene arreglo con otro intento (datos inválidos): pasa a muerto."""


@dataclass(frozen=True)
class TipoTrabajo:
    tipo: str
    manejador: Manejador
    maximo_intentos: int = 3
    retroceso_base: timedelta = timedelta(seconds=30)
    retroceso_tope: timedelta = timedelta(hours=1)

    def retroceso(self, intentos: int) -> timedelta:
        """Espera antes del intento siguiente: base, 2 × base, 4 × base... hasta el tope."""
        return min(self.retroceso_base * 2 ** max(intentos - 1, 0), self.retroceso_tope)


@dataclass
class Registro:
    tipos: dict[str, TipoTrabajo] = field(default_factory=dict)

    def tipo(
        self,
        nombre: str,
        *,
        maximo_intentos: int = 3,
        retroceso_base: timedelta = timedelta(seconds=30),
        retroceso_tope: timedelta = timedelta(hours=1),
    ) -> Callable[[Manejador], Manejador]:
        def registrar(manejador: Manejador) -> Manejador:
            if nombre in self.tipos:
                raise ValueError(f"tipo de trabajo repetido: {nombre}")
            self.tipos[nombre] = TipoTrabajo(
                nombre, manejador, maximo_intentos, retroceso_base, retroceso_tope
            )
            return manejador

        return registrar


REGISTRO = Registro()
"""Tipos de trabajo de la aplicación. Cada módulo registra los suyos al importarse."""


def encolar(
    conn: Connection,
    tipo: str,
    *,
    carga: dict[str, Any] | None = None,
    consorcio_id: UUID | None = None,
    clave_unica: str | None = None,
    disponible_desde: datetime | None = None,
    maximo_intentos: int | None = None,
) -> UUID | None:
    """Encola dentro de la transacción de `conn`. El consorcio debe ser el del ámbito.

    Con `clave_unica`, un segundo encolado igual no hace nada y devuelve None.
    """
    registrado = REGISTRO.tipos.get(tipo)
    if maximo_intentos is None:
        maximo_intentos = registrado.maximo_intentos if registrado else 3
    return conn.execute(
        text(
            "insert into plataforma.jobs"
            " (tipo, consorcio_id, carga, clave_unica, disponible_desde, maximo_intentos)"
            " values (:tipo, :consorcio, cast(:carga as jsonb), :clave,"
            " coalesce(:desde, now()), :maximo)"
            " on conflict (clave_unica) do nothing returning id"
        ),
        {
            "tipo": tipo,
            "consorcio": consorcio_id,
            "carga": json.dumps(carga or {}),
            "clave": clave_unica,
            "desde": disponible_desde,
            "maximo": maximo_intentos,
        },
    ).scalar_one_or_none()


def tomar(conn: Connection, trabajador: str, cantidad: int, plazo: timedelta) -> list[Trabajo]:
    filas = conn.execute(
        text(
            "select id, tipo, consorcio_id, carga, intentos, maximo_intentos"
            " from plataforma.tomar_trabajos(:t, :n, :plazo) order by disponible_desde, id"
        ),
        {"t": trabajador, "n": cantidad, "plazo": plazo},
    ).mappings()
    return [Trabajo(**f) for f in filas]


def terminar(conn: Connection, trabajo: Trabajo, trabajador: str, resultado: Any) -> bool:
    """Marca hecho el trabajo. Un resultado que no es JSON es un `ErrorDefinitivo`.

    Repetir no lo arregla: el manejador devolvería lo mismo y volvería a producir sus efectos
    externos en cada intento. `allow_nan=False` porque PostgreSQL rechaza `NaN` en jsonb.
    """
    try:
        salida = json.dumps(resultado, allow_nan=False)
    except (TypeError, ValueError) as e:
        raise ErrorDefinitivo(f"el resultado del trabajo no es JSON: {e}") from e
    return conn.execute(
        text("select plataforma.terminar_trabajo(:id, :t, cast(:r as jsonb))"),
        {"id": trabajo.id, "t": trabajador, "r": salida},
    ).scalar_one()


def fallar(
    conn: Connection, trabajo: Trabajo, trabajador: str, error: str, reintentar_en: timedelta | None
) -> str | None:
    """Devuelve el estado nuevo (`pendiente` o `muerto`), o None si perdió la reserva.

    El error se guarda redactado (§16.1): `plataforma.jobs` la lee soporte.
    """
    return conn.execute(
        text("select plataforma.fallar_trabajo(:id, :t, :e, :r)::text"),
        {"id": trabajo.id, "t": trabajador, "e": redactar(error), "r": reintentar_en},
    ).scalar_one()


@dataclass(frozen=True)
class EstadoCola:
    pendientes: int
    en_curso: int
    muertos: int
    segundos_mas_viejo: float


def estado(conn: Connection) -> EstadoCola:
    fila = conn.execute(text("select * from plataforma.estado_cola()")).mappings().one()
    return EstadoCola(**fila)
