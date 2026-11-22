"""Comunicados oficiales del consorcio (RF28, mínimo para el piloto en Modo Consejo; `0122`).

Diseño P0 S8 del repositorio CEM y plan §6.7 («RF28 sin estados de lectura», H4a).

- **Quién emite** (`comunicados.emitir`): quien tiene la facultad, que entra en los paquetes
  `miembro_consejo` y `administrador` y no se da a los mandatos ya registrados. Consejo,
  Administrador Aumentado y Autogestión.
- **Quién lee** (`comunicados.ver`): propietarios, inquilinos y ocupantes con el vínculo confirmado,
  y quien emite. En este corte todo comunicado va a todo el edificio.
- **Un comunicado no se cambia ni se borra.** Una corrección es otro comunicado que cita al que
  corrige (`corrige_a`). Un título o un texto con el número de una cuenta se rechaza
  (`texto_con_cuenta`): no se tapa.
- **Envíos: solo `enviado`.** Un correo por persona destinataria, por el outbox de siempre, con el
  título y el enlace a la aplicación; el texto se lee acá. La plataforma no registra la entrega:
  el trabajo del correo dice que el servidor SMTP lo aceptó y se purga a los 30 días. «Entregado»,
  «visto» y «formalizado» quedan para otro corte.
"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

LARGO_TITULO = 120
LARGO_TEXTO = 4000
"""La base tiene el mismo control (`0122`)."""
UN_RENGLON = r"^[^\x00-\x1f\x7f]*$"
"""Ningún carácter de control: el título va en el asunto del correo."""
CON_RENGLONES = r"^[^\x00-\x09\x0b-\x1f\x7f]*$"
"""Ningún carácter de control salvo el salto de renglón."""

Titulo = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=LARGO_TITULO,
                      pattern=UN_RENGLON),
]  # fmt: skip
Texto = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=LARGO_TEXTO,
                      pattern=CON_RENGLONES),
]  # fmt: skip


class ComunicadoNuevo(BaseModel):
    """`POST …/comunicados`: lo reciben todas las personas con un vínculo confirmado en el
    edificio en ese momento."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    titulo: Titulo
    texto: Texto
    corrige_a: UUID | None = Field(
        default=None, description="El comunicado que este corrige, del mismo edificio."
    )


class ReferenciaComunicado(BaseModel):
    """Otro comunicado, por su título: el que se corrige o el que corrige."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    titulo: str
    emitido_en: datetime


class EnviosComunicado(BaseModel):
    """Cuántas personas lo reciben y en qué quedó cada correo. Solo para quien emite, y nunca la
    lista de personas ni sus correos."""

    model_config = ConfigDict(frozen=True)

    destinatarios: int = Field(ge=0, description="Las personas a las que se dirigió.")
    enviados: int = Field(
        ge=0, description="Los correos que quedaron en la cola de salida (uno por persona)."
    )


class ComunicadoResumen(BaseModel):
    """Una fila de la lista."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    titulo: str
    emitido_por_nombre: str
    emitido_en: datetime
    corrige_a: ReferenciaComunicado | None
    corregido: bool = Field(description="Si algún comunicado posterior lo corrige.")


class Comunicado(ComunicadoResumen):
    """`GET …/comunicados/{c}` y la respuesta de emitir."""

    texto: str
    corregido_por: list[ReferenciaComunicado] = Field(
        description="Los comunicados que lo corrigen, del más viejo al más nuevo."
    )
    envios: EnviosComunicado | None = Field(
        description="Solo para quien emite comunicados en el edificio; null para quien lee."
    )


class ListaComunicados(BaseModel):
    """`GET …/comunicados`: del más nuevo al más viejo."""

    model_config = ConfigDict(frozen=True)

    elementos: list[ComunicadoResumen]
    puede_emitir: bool
    destinatarios_posibles: int | None = Field(
        description="Cuántas personas recibirían un comunicado emitido ahora; null si no emite."
    )
    largo_titulo: int
    largo_texto: int
