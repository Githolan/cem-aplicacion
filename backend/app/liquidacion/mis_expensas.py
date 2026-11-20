"""Lectura de una liquidación con el formato del programa «Mis Expensas» (H5-1, diseño H5 §4).

Función pura, sin acceso a la base ni al PDF: recibe las palabras de cada página con su recuadro
(las saca `app.liquidacion.palabras_pdf`) y devuelve lo que la liquidación informa, con el renglón
de origen de cada dato y las diferencias que encuentra al cruzarlo.

Cómo se ubica cada dato (medido sobre las cinco liquidaciones anonimizadas del 19-11-2026):

- Los importes de una tabla van alineados a la derecha bajo su encabezado. Un importe pertenece
  a la última columna cuyo encabezado empieza antes de donde termina el importe. Así no hace falta
  ninguna coordenada fija: las columnas salen del encabezado de cada página.
- Tabla de gastos: encabezado `RUBROS - CONCEPTO | <grupos> | TOTAL`; cada rubro empieza con su
  número en el margen («3 SERVICIOS PUBLICOS») y cierra con `TOTAL CATEGORIA <n>`; un gasto ocupa
  uno o más renglones y sus importes van en el último; la tabla cierra con `TOTALES`.
- Prorrateo: encabezado `UF | Depto. | Propietario | Saldo Anterior | Pagos | Deuda | Interés |
  Disponible | (% y Monto por grupo) | Otros gastos | <redondeo> | TOTAL`; una fila por unidad y
  `TOTALES` al final.

Nada se corrige ni se completa: lo que no cierra va a `diferencias` (D5: toda diferencia mayor que
un centavo) y lo que no tiene la forma esperada corta la lectura con `FormatoNoReconocido`.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal

TOLERANCIA_RENGLON = 2.0
"""Puntos de diferencia en `top` para que dos palabras estén en el mismo renglón visual."""
HUECO_CELDA = 1.0
"""Un hueco mayor que un ancho de carácter separa dos celdas de un encabezado: entre las palabras
de una celda («Gastos Comunes», «Otros gastos») hay un espacio (≈2 puntos); entre celdas, 6
o más."""
SANGRIA = 4.0
"""Puntos a la derecha del margen hasta donde un renglón todavía está en el margen. Medido: los
números de rubro empiezan en x≈31–33 y los renglones de los gastos, sangrados, en x≈39–43."""
CENTAVO = Decimal("0.01")
"""D5: se informa toda diferencia mayor que un centavo."""

MESES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6, "julio": 7,
    "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
}  # fmt: skip
IMPORTE = re.compile(r"^-?\d{1,3}(?:\.\d{3})*,\d{2}$")
PORCENTAJE = re.compile(r"^-?\d{1,3},\d{2}%?$")
NO_PARTICIPA = "-"
PERIODO = re.compile(r"(?i)liquidaci[oó]n\s+de\s+([a-zá-ú]+)\s+(\d{4})")
VENCIMIENTO = re.compile(r"(?i)\b(1er|2do)\s+vencimiento:\s*(\d{2})/(\d{2})/(\d{4})")
NUMERO_UF = re.compile(r"^\d{1,4}$")


class FormatoNoReconocido(ValueError):
    """El documento no tiene la estructura de una liquidación de Mis Expensas."""


@dataclass(frozen=True)
class Palabra:
    texto: str
    x0: float
    x1: float
    top: float
    bottom: float


@dataclass(frozen=True)
class Pagina:
    ancho: float
    alto: float
    palabras: tuple[Palabra, ...]


@dataclass(frozen=True)
class Origen:
    """Página (desde 1) y `top` del renglón donde está el dato: el fragmento que se muestra."""

    pagina: int
    renglon: float


@dataclass(frozen=True)
class Gasto:
    rubro: int
    concepto: str
    importes: dict[str, Decimal]
    total: Decimal
    origen: Origen


@dataclass(frozen=True)
class Rubro:
    numero: int
    nombre: str
    gastos: tuple[Gasto, ...]
    porcentaje: Decimal
    total: Decimal
    origen: Origen


@dataclass(frozen=True)
class ParteDeGrupo:
    porcentaje: Decimal | None
    """`None` cuando la liquidación marca que la unidad no participa del grupo («-»)."""
    monto: Decimal


@dataclass(frozen=True)
class FilaUnidad:
    uf: str
    depto: str
    nombre: str
    saldo_anterior: Decimal
    pagos: Decimal
    deuda: Decimal
    interes: Decimal
    disponible: Decimal
    grupos: dict[str, ParteDeGrupo]
    otros_gastos: Decimal
    redondeo: Decimal
    total: Decimal
    origen: Origen


@dataclass(frozen=True)
class TotalesProrrateo:
    saldo_anterior: Decimal
    pagos: Decimal
    deuda: Decimal
    interes: Decimal
    disponible: Decimal
    grupos: dict[str, ParteDeGrupo]
    otros_gastos: Decimal
    redondeo: Decimal
    total: Decimal
    origen: Origen


@dataclass(frozen=True)
class Diferencia:
    regla: str
    """Nombre corto y estable de la verificación (p. ej. `gasto_suma_grupos`)."""
    detalle: str
    esperado: Decimal
    informado: Decimal
    origen: Origen


@dataclass(frozen=True)
class LiquidacionLeida:
    periodo: tuple[int, int]
    vencimientos: tuple[str, ...]
    grupos: tuple[str, ...]
    rubros: tuple[Rubro, ...]
    totales_gastos: dict[str, Decimal]
    total_gastos: Decimal
    unidades: tuple[FilaUnidad, ...]
    totales_prorrateo: TotalesProrrateo
    origen_totales_gastos: Origen
    diferencias: tuple[Diferencia, ...] = field(default=())


# --------------------------------------------------------------------------------------------
# Renglones y celdas


@dataclass(frozen=True)
class _Renglon:
    pagina: int
    top: float
    palabras: tuple[Palabra, ...]

    @property
    def textos(self) -> list[str]:
        return [p.texto for p in self.palabras]

    @property
    def texto(self) -> str:
        return " ".join(self.textos)

    @property
    def origen(self) -> Origen:
        return Origen(self.pagina, round(self.top, 1))


def _renglones(pagina: Pagina, numero: int) -> list[_Renglon]:
    filas: list[list[Palabra]] = []
    for p in sorted(pagina.palabras, key=lambda p: (p.top, p.x0)):
        if filas and abs(filas[-1][0].top - p.top) <= TOLERANCIA_RENGLON:
            filas[-1].append(p)
        else:
            filas.append([p])
    return [_Renglon(numero, f[0].top, tuple(sorted(f, key=lambda p: p.x0))) for f in filas]


def _ancho_caracter(p: Palabra) -> float:
    return (p.x1 - p.x0) / max(len(p.texto), 1)


def _celdas(palabras: Sequence[Palabra]) -> list[Palabra]:
    """Une las palabras vecinas de un renglón en celdas («Gastos Comunes», «Otros gastos»)."""
    celdas: list[Palabra] = []
    for p in palabras:
        antes = celdas[-1] if celdas else None
        if antes is not None and p.x0 - antes.x1 <= HUECO_CELDA * _ancho_caracter(p):
            celdas[-1] = Palabra(
                f"{antes.texto} {p.texto}",
                antes.x0,
                p.x1,
                min(antes.top, p.top),
                max(antes.bottom, p.bottom),
            )
        else:
            celdas.append(p)
    return celdas


def _normal(texto: str) -> str:
    sin = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return " ".join(sin.lower().split())


def _importe(texto: str, origen: Origen) -> Decimal:
    if not IMPORTE.match(texto):
        raise FormatoNoReconocido(f"importe ilegible {texto!r} en {origen}")
    return Decimal(texto.replace(".", "").replace(",", "."))


def _porcentaje(texto: str, origen: Origen) -> Decimal | None:
    if texto == NO_PARTICIPA:
        return None
    if not PORCENTAJE.match(texto):
        raise FormatoNoReconocido(f"porcentaje ilegible {texto!r} en {origen}")
    return Decimal(texto.rstrip("%").replace(",", "."))


def _es_valor(texto: str) -> bool:
    return bool(IMPORTE.match(texto) or PORCENTAJE.match(texto) or texto == NO_PARTICIPA)


def _columna(columnas: Sequence[tuple[str, float]], x1: float) -> str | None:
    """La última columna cuyo encabezado empieza antes de donde termina el valor."""
    elegida = None
    for nombre, x0 in columnas:
        if x0 < x1:
            elegida = nombre
    return elegida


# --------------------------------------------------------------------------------------------
# Encabezado


def _periodo(renglones: Sequence[_Renglon]) -> tuple[int, int]:
    for r in renglones:
        m = PERIODO.search(r.texto)
        if m and _normal(m.group(1)) in MESES:
            return int(m.group(2)), MESES[_normal(m.group(1))]
    raise FormatoNoReconocido("no se encontró «Liquidación de <mes> <año>»")


def _vencimientos(renglones: Sequence[_Renglon]) -> tuple[str, ...]:
    vistos: dict[str, str] = {}
    for r in renglones:
        for m in VENCIMIENTO.finditer(r.texto):
            vistos.setdefault(m.group(1).lower(), f"{m.group(4)}-{m.group(3)}-{m.group(2)}")
    return tuple(vistos[k] for k in ("1er", "2do") if k in vistos)


# --------------------------------------------------------------------------------------------
# Tabla de gastos


def _encabezado_gastos(r: _Renglon) -> list[tuple[str, float]] | None:
    """Columnas de importes del encabezado de la tabla de gastos, o `None` si no lo es."""
    celdas = _celdas(r.palabras)
    textos = [_normal(c.texto) for c in celdas]
    if not textos or textos[-1] != "total":
        return None
    if "concepto" in " ".join(textos):
        inicio = next(i for i, t in enumerate(textos) if t.endswith("concepto")) + 1
    elif textos[0].startswith("gastos"):
        inicio = 0
    else:
        return None
    columnas = [(c.texto, c.x0) for c in celdas[inicio:]]
    return columnas if len(columnas) >= 2 else None


def _leer_gastos(
    renglones: Sequence[_Renglon],
) -> tuple[tuple[str, ...], list[Rubro], dict[str, Decimal], Decimal, Origen]:
    grupos: tuple[str, ...] | None = None
    columnas: list[tuple[str, float]] = []
    rubros: list[Rubro] = []
    rubro_abierto: tuple[int, str, Origen] | None = None
    gastos: list[Gasto] = []
    descripcion: list[str] = []
    margen: dict[int, float] = {}
    for r in renglones:
        cab = _encabezado_gastos(r)
        if cab is not None:
            nombres = tuple(n for n, _ in cab[:-1])
            if grupos is None:
                grupos = nombres
            elif tuple(map(_normal, nombres)) != tuple(map(_normal, grupos)):
                raise FormatoNoReconocido(f"los grupos del encabezado cambian en {r.origen}")
            columnas = [(g, x) for g, (_, x) in zip((*grupos, "TOTAL"), cab, strict=True)]
            margen.setdefault(r.pagina, min(p.x0 for p in r.palabras))
            continue
        if r.pagina not in margen:
            continue  # antes del encabezado de la página: títulos y leyendas
        margen[r.pagina] = min(margen[r.pagina], r.palabras[0].x0)
        primera = _normal(r.textos[0])
        valores = [p for p in r.palabras if _es_valor(p.texto) and _columna(columnas, p.x1)]
        if primera == "totales" and valores:
            totales = _importes_por_columna(valores, columnas, r.origen)
            if rubro_abierto is not None:
                raise FormatoNoReconocido(f"el rubro {rubro_abierto[0]} no tiene su total")
            total = totales.pop("TOTAL")
            return grupos, rubros, totales, total, r.origen
        if _normal(r.texto).startswith("total categoria"):
            if rubro_abierto is None:
                raise FormatoNoReconocido(f"total de rubro sin rubro en {r.origen}")
            numero, nombre, origen = rubro_abierto
            if r.textos[2] != str(numero):
                raise FormatoNoReconocido(f"el total del rubro {numero} dice {r.textos[2]!r}")
            if descripcion:
                raise FormatoNoReconocido(f"renglones sin importe antes de {r.origen}")
            pct = next((p for p in r.palabras if p.texto.endswith("%")), None)
            if pct is None or not valores or valores[-1] is pct:
                raise FormatoNoReconocido(f"el total del rubro {numero} no tiene % e importe")
            porcentaje = _porcentaje(pct.texto, r.origen)
            assert porcentaje is not None
            rubros.append(
                Rubro(
                    numero,
                    nombre,
                    tuple(gastos),
                    porcentaje,
                    _importe(valores[-1].texto, r.origen),
                    origen,
                )
            )
            rubro_abierto, gastos = None, []
            continue
        siguiente = len(rubros) + 1
        if (
            rubro_abierto is None
            and r.textos[0] == str(siguiente)
            and not valores
            and r.palabras[0].x0 <= margen[r.pagina] + SANGRIA
            and all(t.upper() == t for t in r.textos[1:])
            and len(r.textos) > 1
        ):
            rubro_abierto = (siguiente, " ".join(r.textos[1:]), r.origen)
            continue
        if rubro_abierto is None:
            continue  # títulos entre rubros («TOTAL DE GASTOS»)
        texto = [p.texto for p in r.palabras if p not in valores]
        descripcion += [" ".join(texto)] if texto else []
        if valores:
            importes = _importes_por_columna(valores, columnas, r.origen)
            total = importes.pop("TOTAL")
            gastos.append(
                Gasto(rubro_abierto[0], "\n".join(descripcion), importes, total, r.origen)
            )
            descripcion = []
    raise FormatoNoReconocido("la tabla de gastos no tiene encabezado o no cierra con TOTALES")


def _importes_por_columna(
    valores: Sequence[Palabra], columnas: Sequence[tuple[str, float]], origen: Origen
) -> dict[str, Decimal]:
    importes: dict[str, Decimal] = {}
    for p in valores:
        col = _columna(columnas, p.x1)
        assert col is not None
        if col in importes:
            raise FormatoNoReconocido(f"dos importes en la columna {col!r} en {origen}")
        importes[col] = _importe(p.texto, origen)
    faltan = [n for n, _ in columnas if n not in importes]
    if faltan:
        raise FormatoNoReconocido(f"faltan importes de {faltan} en {origen}")
    return importes


# --------------------------------------------------------------------------------------------
# Prorrateo

_FIJAS = ("saldo", "pagos", "deuda", "interes", "disponible")


@dataclass(frozen=True)
class _ColumnasProrrateo:
    columnas: list[tuple[str, float]]
    """(clave, x0) en orden; claves: saldo, pagos, …, `%:<grupo>`, `monto:<grupo>`, otros,
    redondeo, total."""
    inicio_valores: float


def _columnas_prorrateo(banda: Sequence[_Renglon], grupos: Sequence[str]) -> _ColumnasProrrateo:
    celdas = [c for r in banda for c in _celdas(r.palabras)]
    normales = {_normal(g) for g in grupos}
    rotulos = [c for c in celdas if _normal(c.texto) in normales]
    hojas: list[Palabra] = []
    for c in sorted((c for c in celdas if c not in rotulos), key=lambda c: c.x0):
        apilada = next((h for h in hojas if c.x0 < h.x1 and h.x0 < c.x1), None)
        if apilada is not None and _normal(apilada.texto) not in ("%", "monto"):
            i = hojas.index(apilada)
            arriba, abajo = sorted((apilada, c), key=lambda p: p.top)
            hojas[i] = Palabra(
                f"{arriba.texto} {abajo.texto}",
                min(c.x0, apilada.x0),
                max(c.x1, apilada.x1),
                arriba.top,
                abajo.bottom,
            )
        else:
            hojas.append(c)
    hojas.sort(key=lambda c: c.x0)
    nombres = [_normal(h.texto) for h in hojas]

    def en(i: int) -> str:
        return nombres[i] if i < len(nombres) else "(sin columna)"

    if nombres[:3] != ["uf", "depto.", "propietario"]:
        raise FormatoNoReconocido(f"el prorrateo no empieza con UF, Depto., Propietario: {nombres}")
    columnas: list[tuple[str, float]] = []
    i = 3
    for clave in _FIJAS:
        if not en(i).startswith(clave):
            raise FormatoNoReconocido(f"se esperaba la columna {clave!r} y está {en(i)!r}")
        columnas.append((clave, hojas[i].x0))
        i += 1
    for g in grupos:
        if nombres[i : i + 2] != ["%", "monto"]:
            raise FormatoNoReconocido(f"faltan % y Monto del grupo {g!r}: {nombres[i : i + 2]}")
        izq, der = hojas[i].x0, hojas[i + 1].x1
        rotulo = next((c for c in rotulos if _normal(c.texto) == _normal(g)), None)
        if rotulo is None or not (rotulo.x0 < der and izq < rotulo.x1):
            raise FormatoNoReconocido(f"el grupo {g!r} no está sobre sus columnas % y Monto")
        columnas += [(f"%:{g}", hojas[i].x0), (f"monto:{g}", hojas[i + 1].x0)]
        i += 2
    if not en(i).startswith("otros"):
        raise FormatoNoReconocido(f"se esperaba «Otros gastos» y está {en(i)!r}")
    columnas.append(("otros", hojas[i].x0))
    resto = nombres[i + 1 :]
    if len(resto) != 2 or resto[-1] != "total":
        raise FormatoNoReconocido(
            f"después de «Otros gastos» se esperaba redondeo y TOTAL: {resto}"
        )
    columnas += [("redondeo", hojas[i + 1].x0), ("total", hojas[i + 2].x0)]
    return _ColumnasProrrateo(columnas, columnas[0][1])


def _valores_prorrateo(r: _Renglon, cols: _ColumnasProrrateo) -> dict[str, str]:
    valores: dict[str, str] = {}
    for p in r.palabras:
        if p.x1 > cols.inicio_valores and _es_valor(p.texto):
            col = _columna(cols.columnas, p.x1)
            if col is None or col in valores:
                raise FormatoNoReconocido(f"valor fuera de columna {p.texto!r} en {r.origen}")
            valores[col] = p.texto
    faltan = [c for c, _ in cols.columnas if c not in valores]
    if faltan:
        raise FormatoNoReconocido(f"faltan valores de {faltan} en {r.origen}")
    return valores


def _partes(
    valores: dict[str, str], grupos: Sequence[str], origen: Origen
) -> dict[str, ParteDeGrupo]:
    return {
        g: ParteDeGrupo(
            _porcentaje(valores[f"%:{g}"], origen), _importe(valores[f"monto:{g}"], origen)
        )
        for g in grupos
    }


def _leer_prorrateo(
    renglones: Sequence[_Renglon], grupos: Sequence[str]
) -> tuple[list[FilaUnidad], TotalesProrrateo]:
    unidades: list[FilaUnidad] = []
    cols: _ColumnasProrrateo | None = None
    banda: list[_Renglon] | None = None
    for r in renglones:
        texto = _normal(r.texto)
        if "prorrateo" in texto and not any(_es_valor(t) for t in r.textos):
            banda, cols = [], None
            continue
        if banda is None:
            continue
        es_dato = NUMERO_UF.match(r.textos[0]) or _normal(r.textos[0]) == "totales"
        if cols is None and not es_dato:
            banda.append(r)
            continue
        if cols is None:
            cols = _columnas_prorrateo(banda, grupos)
        if not es_dato:
            continue
        v = _valores_prorrateo(r, cols)
        o = r.origen
        comunes = dict(
            saldo_anterior=_importe(v["saldo"], o),
            pagos=_importe(v["pagos"], o),
            deuda=_importe(v["deuda"], o),
            interes=_importe(v["interes"], o),
            disponible=_importe(v["disponible"], o),
            grupos=_partes(v, grupos, o),
            otros_gastos=_importe(v["otros"], o),
            redondeo=_importe(v["redondeo"], o),
            total=_importe(v["total"], o),
            origen=o,
        )
        if _normal(r.textos[0]) == "totales":
            return unidades, TotalesProrrateo(**comunes)
        texto_izq = [p.texto for p in r.palabras if p.x1 <= cols.inicio_valores]
        if len(texto_izq) < 2:
            raise FormatoNoReconocido(f"la fila de la unidad no tiene departamento en {o}")
        unidades.append(
            FilaUnidad(
                uf=texto_izq[0], depto=texto_izq[1], nombre=" ".join(texto_izq[2:]), **comunes
            )
        )
    raise FormatoNoReconocido("el prorrateo no tiene encabezado o no cierra con TOTALES")


# --------------------------------------------------------------------------------------------
# Verificaciones cruzadas (D5)


def _verificar(lq: LiquidacionLeida, origen_totales: Origen) -> tuple[Diferencia, ...]:
    dif: list[Diferencia] = []

    def comparar(
        regla: str, detalle: str, esperado: Decimal, informado: Decimal, origen: Origen
    ) -> None:
        if abs(esperado - informado) > CENTAVO:
            dif.append(Diferencia(regla, detalle, esperado, informado, origen))

    for rubro in lq.rubros:
        for g in rubro.gastos:
            comparar(
                "gasto_suma_grupos",
                f"rubro {rubro.numero}",
                sum(g.importes.values()),
                g.total,
                g.origen,
            )
        comparar(
            "rubro_suma_gastos",
            f"rubro {rubro.numero}",
            sum((g.total for g in rubro.gastos), Decimal(0)),
            rubro.total,
            rubro.origen,
        )
        if lq.total_gastos:
            pct = (rubro.total * 100 / lq.total_gastos).quantize(CENTAVO)
            comparar(
                "rubro_porcentaje", f"rubro {rubro.numero}", pct, rubro.porcentaje, rubro.origen
            )
    for grupo in lq.grupos:
        suma = sum((g.importes[grupo] for r in lq.rubros for g in r.gastos), Decimal(0))
        comparar("totales_gastos_grupo", grupo, suma, lq.totales_gastos[grupo], origen_totales)
    comparar(
        "totales_gastos",
        "suma de rubros",
        sum((r.total for r in lq.rubros), Decimal(0)),
        lq.total_gastos,
        origen_totales,
    )

    tp = lq.totales_prorrateo
    for u in lq.unidades:
        esperado = (
            u.deuda
            + u.interes
            - u.disponible
            + sum(p.monto for p in u.grupos.values())
            + u.otros_gastos
            + u.redondeo
        )
        comparar("unidad_total", f"UF {u.uf} {u.depto}", esperado, u.total, u.origen)
    for nombre in (
        "saldo_anterior",
        "pagos",
        "deuda",
        "interes",
        "disponible",
        "otros_gastos",
        "redondeo",
        "total",
    ):
        comparar(
            "prorrateo_columna",
            nombre,
            sum((getattr(u, nombre) for u in lq.unidades), Decimal(0)),
            getattr(tp, nombre),
            tp.origen,
        )
    for grupo in lq.grupos:
        montos = sum((u.grupos[grupo].monto for u in lq.unidades), Decimal(0))
        comparar("prorrateo_columna", f"monto {grupo}", montos, tp.grupos[grupo].monto, tp.origen)
        comparar(
            "prorrateo_grupo_vs_gastos",
            grupo,
            lq.totales_gastos[grupo],
            tp.grupos[grupo].monto,
            tp.origen,
        )
        pcts = sum((u.grupos[grupo].porcentaje or Decimal(0) for u in lq.unidades), Decimal(0))
        comparar("prorrateo_porcentaje_100", grupo, Decimal(100), pcts, tp.origen)
        informado = tp.grupos[grupo].porcentaje
        comparar(
            "prorrateo_columna",
            f"% {grupo}",
            pcts,
            Decimal(0) if informado is None else informado,
            tp.origen,
        )
    return tuple(dif)


# --------------------------------------------------------------------------------------------


def leer(paginas: Sequence[Pagina]) -> LiquidacionLeida:
    """Lee una liquidación de Mis Expensas a partir de las palabras de sus páginas."""
    renglones = [r for i, p in enumerate(paginas, 1) for r in _renglones(p, i)]
    if not renglones:
        raise FormatoNoReconocido("el documento no tiene texto")
    grupos, rubros, totales, total, origen_totales = _leer_gastos(renglones)
    unidades, totales_prorrateo = _leer_prorrateo(renglones, grupos)
    if not unidades:
        raise FormatoNoReconocido("el prorrateo no tiene unidades")
    lq = LiquidacionLeida(
        periodo=_periodo(renglones),
        vencimientos=_vencimientos(renglones),
        grupos=grupos,
        rubros=tuple(rubros),
        totales_gastos=totales,
        total_gastos=total,
        origen_totales_gastos=origen_totales,
        unidades=tuple(unidades),
        totales_prorrateo=totales_prorrateo,
    )
    return LiquidacionLeida(**{**lq.__dict__, "diferencias": _verificar(lq, origen_totales)})
