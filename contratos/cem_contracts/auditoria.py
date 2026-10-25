"""Lectura de la auditoría con exposición mínima (RF35-B, decisión del propietario del 24-10-2026).

Diseño: `doc/diseno/H2b_6_Diseno.md` del repositorio CEM, §6. Dos superficies, las dos una
proyección funcional que arma la base (segunda barrera, `0028`), nunca la fila de
`auditoria.eventos`:

- **Mis eventos:** quien está en el consorcio lee los eventos que hizo él.
- **Eventos del consorcio:** con `auditoria.ver` (paquetes de MC y AD, no AE).

Cada evento trae la fecha, la acción traducida, el recurso funcional, el origen y una lista
blanca de cambios. El actor sale solo en los eventos del consorcio: el nombre, solo si quien lee
tiene `padron.ver` y el padrón muestra hoy a esa persona con su nombre. Nunca IP, hashes,
secuencia, identificadores, el `antes`/`despues` completo, el detalle de una negación ni ningún
dato fuera de la proyección. Sin exportación (E0).

Paginación por cursor: el cursor es opaco para el cliente y solo lleva la fecha del último
evento de la página y cuántos eventos de esa misma fecha ya salieron.
"""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

LIMITE_POR_DEFECTO = 50
LIMITE_MAXIMO = 100
LARGO_MAXIMO_CURSOR = 200


class OrigenEvento(StrEnum):
    """De dónde vino el evento, en términos funcionales."""

    APLICACION = "aplicacion"
    """Una persona, desde la aplicación."""
    SOPORTE = "soporte"
    """El equipo de soporte."""
    SISTEMA = "sistema"
    """Un proceso automático (trabajos en segundo plano o tareas periódicas)."""


class TipoActor(StrEnum):
    VOS = "vos"
    """Quien lee."""
    PERSONA = "persona"
    """Otra persona; el nombre sale solo con `padron.ver` y si el padrón la muestra hoy."""
    SOPORTE = "soporte"
    SISTEMA = "sistema"


class CambioEvento(BaseModel):
    """Un campo de la lista blanca funcional de la acción, con su valor antes y después."""

    model_config = ConfigDict(frozen=True)

    campo: str
    antes: str | None
    despues: str | None


class ActorEvento(BaseModel):
    model_config = ConfigDict(frozen=True)

    tipo: TipoActor
    nombre: str | None = Field(
        description="Solo si quien lee tiene `padron.ver` y el padrón muestra hoy a esa persona "
        "con su nombre."
    )


class EventoPropio(BaseModel):
    model_config = ConfigDict(frozen=True)

    fecha: datetime
    accion: str = Field(description="La acción, traducida.")
    recurso: str = Field(description="El tipo de recurso, en términos funcionales.")
    origen: OrigenEvento
    cambios: list[CambioEvento]


class EventoConsorcio(EventoPropio):
    actor: ActorEvento


class ListaEventosPropios(BaseModel):
    """Del más reciente al más antiguo."""

    model_config = ConfigDict(frozen=True)

    elementos: list[EventoPropio]
    siguiente: str | None = Field(description="Cursor de la página siguiente; sin él, no hay más.")


class ListaEventosConsorcio(BaseModel):
    """Del más reciente al más antiguo."""

    model_config = ConfigDict(frozen=True)

    elementos: list[EventoConsorcio]
    siguiente: str | None = Field(description="Cursor de la página siguiente; sin él, no hay más.")
