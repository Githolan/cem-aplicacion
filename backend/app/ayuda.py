"""La ayuda en curso de cada petición (RF53; `0127`).

Quien ayuda manda el encabezado `ENCABEZADO_AYUDA` con el id de la autorización. El middleware
`AyudaDePeticion` lo lee y deja, para toda la petición, un `EstadoAyuda` en una variable de
contexto. La variable guarda **el mismo objeto** en cada copia del contexto (las dependencias y la
ruta corren en hilos con una copia), así que lo que fija `require_scope` lo ven después todas las
transacciones de la petición:

- `require_scope` valida la autorización en la base y la fija (`fijar`); el contexto pasa a ser el
  de la persona asistida, con la autorización.
- `fijar_ambito` (`app/db.py`) pone `app.ayuda_id` en cada transacción de la persona asistida en
  ese consorcio. La base completa con eso, en cada evento de la auditoría, quién actuó de verdad,
  en representación de quién y con qué autorización (`auditoria.encadenar`), y las funciones que
  admiten ayuda comprueban la sesión de quien ayuda y el alcance.

El encabezado solo se admite en las rutas que arman el contexto del edificio y en
`GET /api/v1/me/consorcios` (`admite`): cualquier otra ruta con sesión lo rechaza
(`ayuda_no_admitida`), así nadie actúa como otra persona donde la ayuda no se evalúa.
"""

from collections.abc import Iterable
from contextvars import ContextVar
from dataclasses import dataclass, field
from uuid import UUID

from cem_contracts.ayuda import ENCABEZADO_AYUDA, AlcanceAyuda
from starlette.types import ASGIApp, Receive, Scope, Send

RUTAS_DE_PERSONA = frozenset({("GET", "/api/v1/me/consorcios")})
"""Las rutas sin consorcio en la ruta que admiten la ayuda: el selector de edificio."""
_ENCABEZADO = ENCABEZADO_AYUDA.lower().encode()


@dataclass(frozen=True)
class AyudaVigente:
    autorizacion_id: UUID
    consorcio_id: UUID
    otorgante_id: UUID
    """La persona asistida."""
    autorizado_id: UUID
    """Quien ayuda: la persona de la sesión."""
    alcances: frozenset[AlcanceAyuda]


@dataclass
class EstadoAyuda:
    """Una por petición. `pedida` es el texto del encabezado; `vigente`, lo que validó la base."""

    pedida: str | None = None
    vigente: AyudaVigente | None = field(default=None)


_estado: ContextVar[EstadoAyuda | None] = ContextVar("cem_ayuda", default=None)


def pedida() -> str | None:
    estado = _estado.get()
    return None if estado is None else estado.pedida


def vigente() -> AyudaVigente | None:
    estado = _estado.get()
    return None if estado is None else estado.vigente


def fijar(ayuda: AyudaVigente) -> None:
    estado = _estado.get()
    if estado is None or estado.pedida is None:
        raise RuntimeError("se fija una ayuda fuera de una petición que la pidió")
    estado.vigente = ayuda


def autorizacion_de(persona_id: UUID | None, consorcio_id: UUID | None) -> UUID | None:
    """La autorización que va en `app.ayuda_id`: solo en la transacción de la persona asistida en
    el consorcio de la ayuda."""
    a = vigente()
    if a is None or persona_id != a.otorgante_id or consorcio_id != a.consorcio_id:
        return None
    return a.autorizacion_id


def rutas_que_admiten(rutas: Iterable[tuple[str, str, bool]]) -> frozenset[tuple[str, str]]:
    """(método, ruta) de las que arman el contexto del edificio, más las de persona admitidas.
    Cada elemento de `rutas` es (método, ruta, arma el contexto)."""
    return RUTAS_DE_PERSONA | {(m, p) for m, p, arma in rutas if arma}


class AyudaDePeticion:
    """Middleware ASGI: un `EstadoAyuda` nuevo por petición, con el encabezado si vino."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            valores = [v for k, v in scope["headers"] if k == _ENCABEZADO]
            # Repetido no vale: queda vacío y `require_scope` lo rechaza como inválido.
            texto = valores[0].decode("latin-1") if len(valores) == 1 else ""
            _estado.set(EstadoAyuda(pedida=texto if valores else None))
        await self.app(scope, receive, send)
