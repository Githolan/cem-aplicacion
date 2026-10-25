"""Arma contextos y entidades del evaluador a partir de la tabla de verdad (`tabla_de_verdad.csv`).

El CSV es legible para el propietario; acá se traduce cada fila a tipos puros:

- **figura:** términos unidos por `+`. `P`, `I` y `O` son vínculos vigentes en 3-A (`P@5B`, en
  otra unidad). `MC`, `AD` y `AE` son mandatos con el paquete completo (D2 de `0011` más las
  filas de `0012` de la tabla, §5.2: C2 y A2, y las de `0018` y `0028`); `MC[f1|f2]`, con el paquete
  achicado por el acta.
  `Fac(codigo)` o `Fac(codigo@recurso)` es un mandato con esa sola facultad. `ninguna`, nada.
- **modo:** `C`, `AA`, `AG` o `—` (sin modo registrado).
- **recurso:** los recursos de la entidad, `tipo:símbolo` separados por `;`.
- **mandato:** la situación de todos los mandatos de la figura (vigente, vencido, futuro…).
- **entidad:** `clave=valor` separados por `;`; las listas con `|`. Personas: `yo`, `otro`,
  `otro2` y `nula` (sin persona identificable). `actor=nadie` deja el contexto sin persona.
  Identidad humana (A13-C): por omisión `yo`, `otro` y `otro2` son tres seres humanos
  verificados en el consorcio; `sin_identidad=otro|yo` les quita la verificación y
  `misma_identidad=otro` hace a `otro` el mismo ser humano que `yo` (otra cuenta). Controles
  económicos (R10): por omisión la sesión tiene el segundo factor reciente y menos de 12 horas;
  `controles=sin_segundo_factor`, `sesion_vieja` o `ninguno` los quitan.

Los símbolos (`3A`, `5B`, `X`, `X2`…) se reemplazan por UUID canónicos fijos.
"""

import csv
import importlib.util
import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from uuid import UUID

from cem_contracts.estados import ModoOperacion, TipoVinculo
from cem_contracts.politicas import DIRECTORIO, TipoRecurso

from app.autorizacion.evaluador import (
    ContextoAutorizacion,
    Entidad,
    Facultad,
    Mandato,
    Vinculo,
)

TABLA = DIRECTORIO / "tabla_de_verdad.csv"
VERSIONES = Path(__file__).resolve().parents[1] / "migrations" / "versions"
MIGRACION_0011 = VERSIONES / "0011_mandatos.py"

CONSORCIO = UUID("0192f0c4-0000-7000-8000-00000000000a")
OTRO_CONSORCIO = UUID("0192f0c4-0000-7000-8000-00000000000b")
HOY = date(2026, 6, 15)

IDENTIDADES: dict[str, UUID] = {
    "yo": UUID("0192f0c4-0000-7000-8000-0000000000e1"),
    "otro": UUID("0192f0c4-0000-7000-8000-0000000000e2"),
    "otro2": UUID("0192f0c4-0000-7000-8000-0000000000e3"),
}
CONTROLES = {
    None: (True, True),
    "sin_segundo_factor": (False, True),
    "sesion_vieja": (True, False),
    "ninguno": (False, False),
}
"""`controles` → (segundo factor reciente, sesión de menos de 12 horas)."""

PERSONAS: dict[str, UUID | None] = {
    "yo": UUID("0192f0c4-0000-7000-8000-000000000001"),
    "otro": UUID("0192f0c4-0000-7000-8000-000000000002"),
    "otro2": UUID("0192f0c4-0000-7000-8000-000000000003"),
    "nula": None,
}

_X = "0192f0c4-7a1e-7c3b-9d2a-5b8e4f1a2c3d"
SIMBOLOS = {
    "3A": "0192f0c4-0000-7000-8000-0000000003a0",
    "5B": "0192f0c4-0000-7000-8000-0000000005b0",
    "7C": "0192f0c4-0000-7000-8000-0000000007c0",
    "B1": "0192f0c4-0000-7000-8000-00000000b001",
    "X": _X,
    "X2": _X[:-1] + "e",
    "X_MAYUS": _X.upper(),
    "Y": "0192f0c4-7a1e-7c3b-9d2a-5b8e4f1a2c3f",
}

MODOS: dict[str, ModoOperacion | None] = {
    "C": ModoOperacion.CONSEJO,
    "AA": ModoOperacion.ADMINISTRADOR_AUMENTADO,
    "AG": ModoOperacion.AUTOGESTION,
    "—": None,
}

VINCULOS = {"P": TipoVinculo.PROPIETARIO, "I": TipoVinculo.INQUILINO, "O": TipoVinculo.OCUPANTE}
FIGURAS_MANDATO = {"MC": "miembro_consejo", "AD": "administrador", "AE": "administrador_externo"}

# Filas de `paquete_facultad` que agrega `0012` (tabla de verdad, §5.2: C2 y A2).
ADICIONES_0012 = {
    "miembro_consejo": ["mandatos.registrar", "decisiones.registrar"],
    "administrador": ["mandatos.registrar", "decisiones.registrar", "fiscalizacion.ver_panel"],
    "administrador_externo": [],
}


def migracion_0011():
    """El módulo de la migración `0011`: su catálogo y sus paquetes son la referencia."""
    spec = importlib.util.spec_from_file_location("migracion_0011", MIGRACION_0011)
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


_M0011 = migracion_0011()
CATALOGO: dict[str, bool] = {codigo: economica for codigo, _, economica in _M0011.FACULTADES}
# `0018` (2b-4c): lectura de mandatos y decisiones, no económica, para MC y AD.
CATALOGO |= {"mandatos.ver": False, "decisiones.ver": False}
ADICIONES_0018 = {
    "miembro_consejo": ["mandatos.ver", "decisiones.ver"],
    "administrador": ["mandatos.ver", "decisiones.ver"],
    "administrador_externo": [],
}
# `0028` (2b-6c, RF35-B): lectura de la auditoría del consorcio, para MC y AD, no AE.
CATALOGO |= {"auditoria.ver": False}
ADICIONES_0028 = {
    "miembro_consejo": ["auditoria.ver"],
    "administrador": ["auditoria.ver"],
    "administrador_externo": [],
}
PAQUETES: dict[str, list[str]] = {
    tipo: list(_M0011.PAQUETES[tipo])
    + ADICIONES_0012[tipo]
    + ADICIONES_0018[tipo]
    + ADICIONES_0028[tipo]
    for tipo in FIGURAS_MANDATO.values()
}

SITUACIONES = {
    # (desde, hasta, causa_baja, consorcio) — hoy es el 15-06-2026.
    "vigente": (date(2026, 1, 1), date(2027, 1, 1), None, CONSORCIO),
    "vigente_ultimo_dia": (date(2026, 1, 1), HOY + timedelta(days=1), None, CONSORCIO),
    "sin_fin": (date(2026, 1, 1), None, None, CONSORCIO),
    "vencido": (date(2026, 1, 1), HOY, None, CONSORCIO),
    "futuro": (date(2026, 7, 1), None, None, CONSORCIO),
    "baja_hoy": (date(2026, 1, 1), HOY, "renuncia", CONSORCIO),
    "error_carga": (date(2026, 1, 1), date(2027, 1, 1), "error_carga", CONSORCIO),
    "rango_vacio": (HOY, HOY, "renuncia", CONSORCIO),
    "otro_consorcio": (date(2026, 1, 1), date(2027, 1, 1), None, OTRO_CONSORCIO),
}

_FAC = re.compile(r"Fac\(([a-z_.]+)(?:@(.*))?\)")
_PAQUETE = re.compile(r"(MC|AD|AE)(?:\[(.*)\])?")
_VINCULO = re.compile(r"([PIO])(?:@(\w+))?")


@dataclass(frozen=True)
class Fila:
    numero: int
    caso: str
    accion: str
    figura: str
    modo: str
    recurso: str
    mandato: str
    hay_administrador: str
    entidad: str
    esperado: int
    motivo: str
    descripcion: str

    def __str__(self) -> str:
        detalle = f"{self.accion} · {self.figura} · {self.descripcion}"
        return f"fila {self.numero} ({self.caso}): {detalle}"


def leer_tabla(ruta: Path = TABLA) -> list[Fila]:
    with ruta.open(encoding="utf-8", newline="") as f:
        return [
            Fila(numero=n, **{**fila, "esperado": int(fila["esperado"])})
            for n, fila in enumerate(csv.DictReader(f), start=2)
        ]


def simbolo(valor: str) -> str:
    """Reemplaza cada segmento entre `:` que sea un símbolo; el resto queda literal."""
    return ":".join(SIMBOLOS.get(s, s) for s in valor.split(":"))


def _recursos(texto: str) -> dict[TipoRecurso, str]:
    recursos: dict[TipoRecurso, str] = {}
    for item in filter(None, texto.split(";")):
        tipo, id_ = item.split(":", 1)
        recursos[TipoRecurso(tipo)] = SIMBOLOS.get(id_, id_)
    return recursos


def _entidad(texto: str, recursos: dict[TipoRecurso, str]) -> tuple[Entidad, dict[str, str]]:
    valores = dict(item.split("=", 1) for item in filter(None, texto.split(";")))
    desconocidas = set(valores) - {
        "preparo", "aprobaron", "firmas", "solicito", "propuso", "objetivo", "registrado",
        "emitio", "decision", "respaldo", "actor", "sin_identidad", "misma_identidad",
        "controles",
    }  # fmt: skip
    if desconocidas:
        raise ValueError(f"claves de entidad desconocidas: {desconocidas}")

    def persona(clave: str) -> UUID | None:
        if (clave, valores.get(clave)) in {
            ("objetivo", "sin_cuenta"),
            ("registrado", "soporte"),
            ("decision", "equipo"),
            ("decision", "ninguna"),
        }:
            return None
        return PERSONAS[valores[clave]] if clave in valores else None

    def personas(clave: str) -> tuple[UUID | None, ...]:
        return tuple(PERSONAS[p] for p in valores[clave].split("|")) if clave in valores else ()

    humanas = valores.get("sin_identidad", "").split("|") + valores.get(
        "misma_identidad", ""
    ).split("|")
    if (
        valores.get("respaldo", "no") not in {"si", "no"}
        or valores.get("actor", "") not in {"", "nadie"}
        or valores.get("controles") not in CONTROLES
        or not set(filter(None, humanas)) <= set(IDENTIDADES)
    ):
        raise ValueError(f"entidad mal escrita: {texto!r}")
    entidad = Entidad(
        recursos=recursos,
        preparado_por=persona("preparo"),
        aprobado_por=personas("aprobaron"),
        firmas=personas("firmas"),
        solicitado_por=persona("solicito"),
        propuesto_por=persona("propuso"),
        persona_objetivo=persona("objetivo"),
        objetivo_sin_cuenta=valores.get("objetivo") == "sin_cuenta",
        registrado_por=persona("registrado"),
        registrado_por_soporte=valores.get("registrado") == "soporte",
        emitido_por=persona("emitio"),
        decision_registrada_por=persona("decision"),
        decision_del_equipo=valores.get("decision") == "equipo",
        sin_decision=valores.get("decision") == "ninguna",
        respaldo_presente=valores.get("respaldo") == "si",
    )
    return entidad, valores


def _identidades(valores: dict[str, str]) -> dict[UUID, UUID]:
    sin = set(valores.get("sin_identidad", "").split("|"))
    misma = set(valores.get("misma_identidad", "").split("|"))
    return {
        PERSONAS[nombre]: IDENTIDADES["yo" if nombre in misma else nombre]
        for nombre in IDENTIDADES
        if nombre not in sin
    }


def escenario(fila: Fila) -> tuple[ContextoAutorizacion, Entidad]:
    vinculos: list[Vinculo] = []
    mandatos: list[Mandato] = []
    facultades: list[Facultad] = []
    if fila.mandato not in SITUACIONES and fila.mandato != "sin_mandato":
        raise ValueError(f"situación de mandato desconocida: {fila.mandato!r}")

    def mandato(codigos: list[tuple[str, str | None]]) -> None:
        if fila.mandato == "sin_mandato":
            raise ValueError(f"{fila}: la figura tiene mandato y la situación dice sin_mandato")
        desde, hasta, causa, consorcio = SITUACIONES[fila.mandato]
        m = Mandato(
            id=UUID(int=len(mandatos) + 1),
            consorcio_id=consorcio,
            vigente_desde=desde,
            vigente_hasta=hasta,
            causa_baja=causa,
        )
        mandatos.append(m)
        for codigo, recurso in codigos:
            if codigo not in CATALOGO:
                raise ValueError(f"{fila}: facultad fuera del catálogo de D2: {codigo!r}")
            facultades.append(Facultad(codigo=codigo, recurso=recurso, mandato_id=m.id))

    for termino in fila.figura.split("+"):
        if termino == "ninguna":
            continue
        if v := _VINCULO.fullmatch(termino):
            unidad = SIMBOLOS[v.group(2) or "3A"]
            vinculos.append(Vinculo(tipo=VINCULOS[v.group(1)], unidad_id=UUID(unidad)))
        elif p := _PAQUETE.fullmatch(termino):
            paquete = PAQUETES[FIGURAS_MANDATO[p.group(1)]]
            elegidas = p.group(2).split("|") if p.group(2) is not None else paquete
            if not set(elegidas) <= set(paquete):
                raise ValueError(f"{fila}: el acta achica el paquete, nunca lo amplía")
            mandato([(c, None) for c in elegidas])
        elif f := _FAC.fullmatch(termino):
            recurso = simbolo(f.group(2)) if f.group(2) is not None else None
            mandato([(f.group(1), recurso)])
        else:
            raise ValueError(f"{fila}: término de figura desconocido: {termino!r}")
    if not mandatos and fila.mandato != "sin_mandato":
        raise ValueError(f"{fila}: sin mandato en la figura y situación {fila.mandato!r}")
    if fila.hay_administrador not in {"si", "no"}:
        raise ValueError(f"{fila}: hay_administrador debe ser si o no")

    entidad, valores = _entidad(fila.entidad, _recursos(fila.recurso))
    segundo_factor, sesion = CONTROLES[valores.get("controles")]
    contexto = ContextoAutorizacion(
        persona_id=None if valores.get("actor") == "nadie" else PERSONAS["yo"],
        consorcio_id=CONSORCIO,
        hoy=HOY,
        vinculos=tuple(vinculos),
        modo=MODOS[fila.modo],
        mandatos=tuple(mandatos),
        facultades=tuple(facultades),
        hay_administrador_vigente=fila.hay_administrador == "si",
        identidades=_identidades(valores),
        segundo_factor_reciente=segundo_factor,
        sesion_reciente=sesion,
    )
    return contexto, entidad
