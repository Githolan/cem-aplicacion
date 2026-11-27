"""Los controles de una liquidación leída, en palabras (rediseño de la confirmación, RL1-A).

Una persona acepta la liquidación entera después de ver estos controles (RL1-A, decidida por el
propietario el 26-11-2026): lo que está bien, plegado, y lo que hay para mirar, una tarjeta por
cosa. Cada control se calcula con lo leído y lo corregido de los datos no descartados, al pedir la
importación; no se guarda.

- **1 a 6** son los cruces de la lectura (D5, `mis_expensas._verificar`), sobre los mismos datos.
- **7**: lo que paga cada unidad en cada grupo frente a su porcentaje por el total del grupo, con
  los números del propio documento. La tolerancia sale de los decimales del porcentaje: un
  porcentaje con dos decimales puede estar ajustado hasta un centésimo de punto para que el grupo
  sume 100 % (el resto mayor), y eso, sobre el total del grupo, es lo que puede diferir sin que haya
  nada raro (más el centavo de D5-A). Medido sobre las cinco liquidaciones anonimizadas del
  19-11-2026: la mayor desviación fue 0,0038 % del total del grupo, contra 0,01 % de tolerancia;
  ninguna unidad fuera.
- **8**: deuda e interés de cada unidad, explicados. Para mirar, no es un error.
- **9**: el saldo anterior de cada unidad es lo que pagaba según la liquidación oficial del mes
  anterior. Medido sobre las cinco anonimizadas (cuatro pasos de mes): coincide en todas las
  unidades.
- **10**: las unidades del documento están en el padrón.

El control 11 (la factura de cada gasto) llega con S3b; el 12 (recálculo con los coeficientes del
edificio) sigue en `recalculo` y se ve después de aceptar.

Dice la cuenta, no califica a la administración (RN33). Un control que no tiene los datos que
necesita (una lectura con IA trae menos columnas) sale `no_aplica`, con el motivo.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from cem_contracts.importacion_liquidacion import ControlLiquidacion, EstadoControl

CENTAVO = Decimal("0.01")
"""D5-A: más de un centavo es una diferencia (19-11-2026)."""
CIEN = Decimal(100)


@dataclass(frozen=True)
class Dato:
    """Un dato leído, con lo corregido si se corrigió; los descartados no entran."""

    tipo: str
    clave: str
    valor: Mapping[str, Any]
    unidad_id: UUID | None


def _num(v: Any) -> Decimal | None:
    if not isinstance(v, str):
        return None
    try:
        return Decimal(v)
    except InvalidOperation:
        return None


def pesos(importe: Decimal) -> str:
    """«$ 1.234,50», o «$ 1.234» si no tiene centavos."""
    q = importe.quantize(CENTAVO)
    signo = "-" if q < 0 else ""
    enteros, centavos = f"{abs(q):.2f}".split(".")
    miles = f"{int(enteros):,}".replace(",", ".")
    return f"{signo}$ {miles}" + ("" if centavos == "00" else f",{centavos}")


def porcentaje(p: Decimal) -> str:
    """«16 %», «3,5 %»: sin ceros de más."""
    texto = f"{p.normalize():f}".replace(".", ",")
    return f"{texto} %"


def _unidad(v: Mapping[str, Any]) -> str:
    uf, depto = str(v.get("uf") or "").strip(), str(v.get("depto") or "").strip()
    if uf and depto:
        return f"{depto} (UF {uf})"
    return depto or (f"UF {uf}" if uf else "una unidad sin designación")


def _control(
    numero: int, clave: str, titulo: str, malas: list[str], bien: str, mirar: str
) -> ControlLiquidacion:
    if malas:
        return ControlLiquidacion(
            numero=numero,
            clave=clave,
            estado=EstadoControl.PARA_MIRAR,
            titulo=titulo,
            texto=mirar,
            lineas=malas,
        )
    return ControlLiquidacion(
        numero=numero, clave=clave, estado=EstadoControl.BIEN, titulo=titulo, texto=bien, lineas=[]
    )


def _no_aplica(numero: int, clave: str, titulo: str, motivo: str) -> ControlLiquidacion:
    return ControlLiquidacion(
        numero=numero,
        clave=clave,
        estado=EstadoControl.NO_APLICA,
        titulo=titulo,
        texto=motivo,
        lineas=[],
    )


def _difiere(a: Decimal, b: Decimal, tolerancia: Decimal = CENTAVO) -> bool:
    return abs(a - b) > tolerancia


def _de(datos: Iterable[Dato], tipo: str) -> list[Dato]:
    return [d for d in datos if d.tipo == tipo]


SIN_COLUMNAS = "La lectura no trae esas columnas: no se puede comprobar."


def _gastos(datos: Sequence[Dato]) -> list[ControlLiquidacion]:
    gastos, rubros = _de(datos, "gasto"), _de(datos, "rubro")
    totales = next((d.valor for d in datos if d.clave == "total:gastos"), None)
    total_gastos = _num(totales.get("total")) if totales else None
    salida = []

    titulo = "Cada gasto suma lo de sus grupos"
    con_grupos = [g for g in gastos if isinstance(g.valor.get("importes"), Mapping)]
    if not con_grupos:
        salida.append(_no_aplica(1, "gasto_suma_grupos", titulo, SIN_COLUMNAS))
    else:
        malas = []
        for g in con_grupos:
            partes = [_num(x) for x in g.valor["importes"].values()]
            total = _num(g.valor.get("total"))
            suma = sum((p for p in partes if p is not None), Decimal(0))
            if total is not None and _difiere(suma, total):
                malas.append(
                    f"{g.valor.get('concepto', '')}: sus grupos suman {pesos(suma)} y el"
                    f" total dice {pesos(total)}."
                )
        n = len(con_grupos)
        salida.append(
            _control(
                1,
                "gasto_suma_grupos",
                titulo,
                malas,
                f"Los {n} gastos suman lo de sus grupos."
                if n > 1
                else "El gasto suma lo de sus grupos.",
                "Hay gastos cuyo total no es la suma de sus grupos.",
            )
        )

    titulo = "Cada rubro suma sus gastos"
    if not rubros:
        salida.append(_no_aplica(2, "rubro_suma_gastos", titulo, "La lectura no trae rubros."))
    else:
        malas = []
        for r in rubros:
            total = _num(r.valor.get("total"))
            numero = r.valor.get("numero")
            suyos = [_num(g.valor.get("total")) for g in gastos if g.valor.get("rubro") == numero]
            if total is None:
                continue
            # Un rubro con importe y sin ningún gasto (por ejemplo, porque se descartó el único)
            # no cierra: sus gastos suman cero.
            suma = sum((x for x in suyos if x is not None), Decimal(0))
            if _difiere(suma, total):
                malas.append(
                    f"{r.valor.get('nombre', '')}: sus gastos suman {pesos(suma)} y el"
                    f" rubro dice {pesos(total)}."
                )
        salida.append(
            _control(
                2,
                "rubro_suma_gastos",
                titulo,
                malas,
                "Cada rubro suma sus gastos.",
                "Hay rubros que no suman sus gastos.",
            )
        )

    titulo = "El porcentaje de cada rubro sobre el total"
    con_pct = [r for r in rubros if _num(r.valor.get("porcentaje")) is not None]
    if not con_pct or not total_gastos:
        salida.append(_no_aplica(3, "rubro_porcentaje", titulo, SIN_COLUMNAS))
    else:
        malas = []
        for r in con_pct:
            total, pct = _num(r.valor.get("total")), _num(r.valor.get("porcentaje"))
            if total is None or pct is None:
                continue
            calculado = (total * CIEN / total_gastos).quantize(CENTAVO)
            if _difiere(calculado, pct):
                malas.append(
                    f"{r.valor.get('nombre', '')}: dice {porcentaje(pct)} y"
                    f" {pesos(total)} sobre {pesos(total_gastos)} es"
                    f" {porcentaje(calculado)}."
                )
        salida.append(
            _control(
                3,
                "rubro_porcentaje",
                titulo,
                malas,
                "El porcentaje de cada rubro es el que le toca sobre el total.",
                "Hay rubros con un porcentaje que no es el que le toca.",
            )
        )

    titulo = "Cada grupo suma sus gastos"
    grupos = totales.get("grupos") if totales else None
    if not isinstance(grupos, Mapping) or not grupos or not con_grupos:
        salida.append(_no_aplica(4, "totales_gastos_grupo", titulo, SIN_COLUMNAS))
    else:
        malas, bien = [], []
        prorrateo = next((d.valor for d in datos if d.clave == "total:prorrateo"), {})
        del_prorrateo = prorrateo.get("grupos") if isinstance(prorrateo, Mapping) else None
        for nombre, dicho in grupos.items():
            total = _num(dicho)
            if total is None:
                continue
            suma = sum(
                (_num(g.valor["importes"].get(nombre)) or Decimal(0) for g in con_grupos),
                Decimal(0),
            )
            if _difiere(suma, total):
                malas.append(
                    f"{nombre}: sus gastos suman {pesos(suma)} y el total del grupo dice"
                    f" {pesos(total)}."
                )
            else:
                bien.append(f"{nombre}: {pesos(total)}")
            repartido = (
                _num(del_prorrateo[nombre].get("monto"))
                if isinstance(del_prorrateo, Mapping)
                and isinstance(del_prorrateo.get(nombre), Mapping)
                else None
            )
            if repartido is not None and _difiere(repartido, total):
                malas.append(
                    f"{nombre}: los gastos del grupo son {pesos(total)} y entre las"
                    f" unidades se reparte {pesos(repartido)}."
                )
        salida.append(
            _control(
                4,
                "totales_gastos_grupo",
                titulo,
                malas,
                "Cada grupo suma sus gastos y se reparte entero: " + " · ".join(bien) + ".",
                "Hay grupos cuyo total no cierra.",
            )
        )

    titulo = "Los rubros suman el total de gastos"
    totales_rubros = [_num(r.valor.get("total")) for r in rubros]
    if total_gastos is None or not totales_rubros or None in totales_rubros:
        salida.append(_no_aplica(5, "totales_gastos", titulo, SIN_COLUMNAS))
    else:
        suma = sum((x for x in totales_rubros if x is not None), Decimal(0))
        malas = (
            [f"Los rubros suman {pesos(suma)} y el total de gastos dice {pesos(total_gastos)}."]
            if _difiere(suma, total_gastos)
            else []
        )
        salida.append(
            _control(
                5,
                "totales_gastos",
                titulo,
                malas,
                f"Los rubros suman el total de gastos: {pesos(total_gastos)}.",
                "Los rubros no suman el total de gastos.",
            )
        )
    return salida


CLAVES_TOTAL_UNIDAD = ("deuda", "interes", "disponible", "otros_gastos", "redondeo", "total")


def _total_unidad(datos: Sequence[Dato]) -> ControlLiquidacion:
    titulo = "El total de cada unidad cierra"
    importes = [
        d for d in _de(datos, "importe_unidad") if isinstance(d.valor.get("grupos"), Mapping)
    ]
    saldos = {
        (str(d.valor.get("uf")), str(d.valor.get("depto"))): d.valor
        for d in _de(datos, "saldo_unidad")
    }
    if not importes or not saldos:
        return _no_aplica(6, "unidad_total", titulo, SIN_COLUMNAS)
    malas = []
    for d in importes:
        s = saldos.get((str(d.valor.get("uf")), str(d.valor.get("depto"))), {})
        partes = {k: _num(s.get(k)) for k in ("deuda", "interes", "disponible")}
        partes |= {k: _num(d.valor.get(k)) for k in ("otros_gastos", "redondeo", "total")}
        montos = [
            _num(p.get("monto")) for p in d.valor["grupos"].values() if isinstance(p, Mapping)
        ]
        if any(v is None for v in partes.values()) or None in montos:
            continue
        p = {k: v for k, v in partes.items() if v is not None}
        esperado = (
            p["deuda"]
            + p["interes"]
            - p["disponible"]
            + sum((m for m in montos if m is not None), Decimal(0))
            + p["otros_gastos"]
            + p["redondeo"]
        )
        if _difiere(esperado, p["total"]):
            malas.append(
                f"{_unidad(d.valor)}: deuda, interés, saldo a favor y lo del mes suman"
                f" {pesos(esperado)} y el total dice {pesos(p['total'])}."
            )
    malas += _columnas_del_prorrateo(datos, importes, list(saldos.values()))
    n = len(importes)
    return _control(
        6,
        "unidad_total",
        titulo,
        malas,
        f"El total de cada una de las {n} unidades es la suma de lo que debía, el"
        " interés y lo del mes, menos su saldo a favor, y cada columna del prorrateo suma su"
        " total.",
        "Hay unidades cuyo total no es la suma de sus partes, o columnas del prorrateo que no"
        " suman su total.",
    )


COLUMNAS_SALDO = {
    "saldo_anterior": "saldo anterior",
    "pagos": "pagos",
    "deuda": "deuda",
    "interes": "interés",
    "disponible": "saldo a favor",
}
COLUMNAS_IMPORTE = {"otros_gastos": "otros gastos", "redondeo": "redondeo", "total": "total"}


def _columnas_del_prorrateo(
    datos: Sequence[Dato], importes: Sequence[Dato], saldos: Sequence[Mapping[str, Any]]
) -> list[str]:
    """Cada columna del prorrateo frente a la suma de sus unidades (`prorrateo_columna` de D5):
    el total impreso de una columna puede no ser la suma de las filas."""
    prorrateo = next((d.valor for d in datos if d.clave == "total:prorrateo"), None)
    if prorrateo is None:
        return []
    malas = []

    def comparar(nombre: str, filas: Iterable[Any], impreso: Any) -> None:
        dicho = _num(impreso)
        valores = [_num(x) for x in filas]
        if dicho is None or not valores or None in valores:
            return
        suma = sum((x for x in valores if x is not None), Decimal(0))
        if _difiere(suma, dicho):
            malas.append(
                f"La columna {nombre} del prorrateo dice {pesos(dicho)} y sus unidades suman"
                f" {pesos(suma)}."
            )

    for clave, nombre in COLUMNAS_SALDO.items():
        comparar(nombre, (s.get(clave) for s in saldos), prorrateo.get(clave))
    for clave, nombre in COLUMNAS_IMPORTE.items():
        comparar(nombre, (d.valor.get(clave) for d in importes), prorrateo.get(clave))
    grupos = prorrateo.get("grupos")
    if isinstance(grupos, Mapping):
        for g, parte in grupos.items():
            if not isinstance(parte, Mapping):
                continue
            montos = [
                d.valor["grupos"][g].get("monto")
                if isinstance(d.valor["grupos"].get(g), Mapping)
                else None
                for d in importes
            ]
            comparar(f"de {g}", montos, parte.get("monto"))
    return malas


def _porcentaje_grupo(datos: Sequence[Dato]) -> ControlLiquidacion:
    titulo = "Lo que paga cada unidad coincide con su porcentaje"
    prorrateo = next((d.valor for d in datos if d.clave == "total:prorrateo"), None)
    totales = next((d.valor for d in datos if d.clave == "total:gastos"), None)
    importes = [
        d for d in _de(datos, "importe_unidad") if isinstance(d.valor.get("grupos"), Mapping)
    ]
    if not importes or (prorrateo is None and totales is None):
        return _no_aplica(7, "porcentaje_grupo", titulo, SIN_COLUMNAS)

    def total_del_grupo(nombre: str) -> Decimal | None:
        if prorrateo is not None and isinstance(prorrateo.get("grupos"), Mapping):
            parte = prorrateo["grupos"].get(nombre)
            if isinstance(parte, Mapping) and _num(parte.get("monto")) is not None:
                return _num(parte.get("monto"))
        if totales is not None and isinstance(totales.get("grupos"), Mapping):
            return _num(totales["grupos"].get(nombre))
        return None

    malas: list[str] = []
    comprobadas = 0
    sumas: dict[str, Decimal] = {}
    for d in importes:
        for nombre, parte in d.valor["grupos"].items():
            if not isinstance(parte, Mapping):
                continue
            pct, monto, base = (
                _num(parte.get("porcentaje")),
                _num(parte.get("monto")),
                total_del_grupo(nombre),
            )
            if pct is None or monto is None or base is None:
                continue
            sumas[nombre] = sumas.get(nombre, Decimal(0)) + pct
            comprobadas += 1
            decimales = max(0, -int(pct.as_tuple().exponent))
            tolerancia = base * Decimal(10) ** -decimales / CIEN + CENTAVO
            esperado = pct * base / CIEN
            if _difiere(esperado, monto, tolerancia):
                dif = monto - esperado
                cuanto = f"{pesos(abs(dif))} {'más' if dif > 0 else 'menos'}"
                malas.append(
                    f"{_unidad(d.valor)} paga {pesos(monto)} de {nombre}:"
                    f" {cuanto} de lo que le toca por su {porcentaje(pct)} de"
                    f" {pesos(base)} ({pesos(esperado)})."
                )
    if comprobadas == 0:
        return _no_aplica(7, "porcentaje_grupo", titulo, "La lectura no trae los porcentajes.")
    for nombre, suma in sumas.items():
        if _difiere(suma, CIEN):
            malas.append(f"Los porcentajes de {nombre} suman {porcentaje(suma)}, no 100 %.")
    return _control(
        7,
        "porcentaje_grupo",
        titulo,
        malas,
        "Cada unidad paga, en cada grupo, su porcentaje del total del grupo.",
        "Hay unidades que no pagan su porcentaje del total del grupo.",
    )


def _deuda(datos: Sequence[Dato]) -> ControlLiquidacion:
    titulo = "Deuda e interés de cada unidad"
    saldos = _de(datos, "saldo_unidad")
    con_columnas = [s for s in saldos if _num(s.valor.get("deuda")) is not None]
    if not con_columnas:
        return _no_aplica(8, "deuda_unidad", titulo, SIN_COLUMNAS)
    totales = {
        (str(d.valor.get("uf")), str(d.valor.get("depto"))): _num(d.valor.get("total"))
        for d in _de(datos, "importe_unidad")
    }
    lineas = []
    for s in con_columnas:
        deuda = _num(s.valor.get("deuda")) or Decimal(0)
        interes = _num(s.valor.get("interes")) or Decimal(0)
        if deuda <= 0 and interes <= 0:
            continue
        total = totales.get((str(s.valor.get("uf")), str(s.valor.get("depto"))))
        paga = f" Este mes paga {pesos(total)}." if total is not None else ""
        if deuda > 0 and interes > 0:
            tasa = (interes * CIEN / deuda).quantize(CENTAVO)
            lineas.append(
                f"{_unidad(s.valor)} debe {pesos(deuda)} del mes anterior, más"
                f" {pesos(interes)} de interés ({porcentaje(tasa)} de la deuda).{paga}"
            )
        elif deuda > 0:
            lineas.append(
                f"{_unidad(s.valor)} debe {pesos(deuda)} del mes anterior, sin interés.{paga}"
            )
        else:
            lineas.append(
                f"{_unidad(s.valor)} paga {pesos(interes)} de interés sin deuda del mes"
                f" anterior (por ejemplo, por haber pagado tarde).{paga}"
            )
    return _control(
        8,
        "deuda_unidad",
        titulo,
        lineas,
        "Ninguna unidad debe del mes anterior.",
        "Hay unidades que deben del mes anterior: su total incluye la deuda y el interés.",
    )


def _saldo_anterior(
    datos: Sequence[Dato], anterior: Mapping[UUID, Decimal] | None
) -> ControlLiquidacion:
    titulo = "El saldo anterior es lo que pagaba el mes pasado"
    if anterior is None:
        return _no_aplica(
            9,
            "saldo_anterior",
            titulo,
            "No hay liquidación aceptada del mes anterior en CEM para comparar.",
        )
    malas, comparadas = [], 0
    for s in _de(datos, "saldo_unidad"):
        saldo = _num(s.valor.get("saldo_anterior"))
        if saldo is None or s.unidad_id is None or s.unidad_id not in anterior:
            continue
        comparadas += 1
        antes = anterior[s.unidad_id]
        if _difiere(saldo, antes):
            malas.append(
                f"{_unidad(s.valor)}: el saldo anterior dice {pesos(saldo)} y la"
                f" liquidación del mes pasado le cobraba {pesos(antes)}."
            )
    if comparadas == 0:
        return _no_aplica(
            9,
            "saldo_anterior",
            titulo,
            "Ninguna unidad de esta liquidación está en la del mes anterior.",
        )
    return _control(
        9,
        "saldo_anterior",
        titulo,
        malas,
        f"El saldo anterior de las {comparadas} unidades es lo que les cobraba la"
        " liquidación del mes pasado.",
        "Hay unidades cuyo saldo anterior no es lo que les cobraba el mes pasado.",
    )


def _padron(datos: Sequence[Dato]) -> ControlLiquidacion:
    titulo = "Las unidades son las del padrón"
    unidades = _de(datos, "unidad")
    if not unidades:
        return _no_aplica(10, "padron", titulo, "La lectura no trae unidades.")
    fuera = [
        f"{_unidad(u.valor)} no está en el padrón del edificio."
        for u in unidades
        if u.unidad_id is None
    ]
    n = len(unidades)
    return _control(
        10,
        "padron",
        titulo,
        fuera,
        f"Las {n} unidades de la liquidación están en el padrón."
        if n > 1
        else "La unidad de la liquidación está en el padrón.",
        "Hay unidades de la liquidación que no están en el padrón.",
    )


def controles(
    datos: Sequence[Dato], anterior: Mapping[UUID, Decimal] | None
) -> list[ControlLiquidacion]:
    """Los controles de la liquidación, en el orden del diseño. `anterior` es lo que cobraba a cada
    unidad la liquidación oficial del mes anterior (por unidad del padrón), o `None` si no hay."""
    if not datos:
        return []
    return [
        *_gastos(datos),
        _total_unidad(datos),
        _porcentaje_grupo(datos),
        _deuda(datos),
        _saldo_anterior(datos, anterior),
        _padron(datos),
    ]
