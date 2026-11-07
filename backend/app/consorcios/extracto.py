"""Leer un extracto en CSV y compararlo con los pagos (RF12). Puro: sin base y sin red.

Diseño: `doc/diseno/Rediseno_Circuito_Pagos_2026-11-06.md` del repositorio CEM, §10.

**El archivo es de una persona: no se confía en nada.** De él solo salen la fecha, el importe en
centavos (`Decimal`, nunca `float`) y un número de operación con el formato de la base. Una celda
que no se puede leer así no llega a ningún lado: la línea se cuenta como no leída y se dice cuál
fue. Una fórmula no es una fecha ni un importe, y como número de operación se descarta. Los
nombres de columna, que sí se guardan y se muestran, no pueden empezar como una fórmula
(`extracto_invalido`).

**Lo que se muestra para elegir las columnas** es la primera línea de datos, sin números largos: una
descripción puede traer un CBU o un CUIT, y eso no sale del servidor.

**El número de operación** se normaliza como `registrar_pago_manual` (`0055`). Con 22 dígitos
seguidos puede ser una cuenta: nunca se guarda (`0065` niega lo mismo al registrar).

**Clave de línea.** El hash va sobre los datos ya leídos, no sobre el texto: otra codificación u
otro separador del mismo extracto dan la misma clave. Lleva la cantidad de veces que esos mismos
datos ya aparecieron en el archivo: dos débitos iguales en un día son dos movimientos, y reimportar
da las mismas dos claves.

**El separador** es el que da una tabla: un encabezado de nombres con una columna de fechas debajo.

**Comparar.** Por número de operación; una línea sin número, por importe y fecha. Con un número que
coincide y otro importe no hay coincidencia: se muestran los dos lados. Por importe y fecha solo se
empareja cuando no hay duda: tantas líneas como pagos iguales. Si no, se miran. Nada se aproxima:
importe exacto y fecha exacta.
"""

import csv
import hashlib
import io
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from cem_contracts.cotejo import (
    FORMATO_COLUMNA,
    MONEDA_SUGERIDA,
    ColumnasExtracto,
    SignoExtracto,
)

BOMS_UTF16 = (b"\xff\xfe", b"\xfe\xff")
CODIFICACIONES = ("utf-8-sig", "cp1252")
"""Las de un home banking: la segunda es la de Excel en Windows. `cp1252` rechaza los bytes que no
define, y un archivo binario tiene bytes nulos: por las dos vías cae en `extracto_invalido`."""
SEPARADORES = (";", ",", "\t", "|")
LINEAS_PARA_SEPARADOR = 50
MINIMO_COLUMNAS = 2
MAXIMO_COLUMNAS = 60
LARGO_MUESTRA = 40
LARGO_ANTES_DE_MOSTRAR = 200
"""Lo que se mira de una celda para mostrarla: las celdas pueden ser enormes."""
CENTAVOS_POR_PESO = 100
MAXIMO_CENTAVOS = 10**16 - 1
"""El tope de `NUMERIC(18,2)`."""
PRIMER_ANIO = 2000
INICIO_DE_FORMULA = ("=", "+", "-", "@", "\t", "\r")

FECHAS = (
    (re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", re.ASCII), "dma"),
    (re.compile(r"^(\d{1,2})-(\d{1,2})-(\d{4})$", re.ASCII), "dma"),
    (re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{2})$", re.ASCII), "dma2"),
    (re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", re.ASCII), "amd"),
)
HORA = re.compile(r"[ T]\d{1,2}:\d{2}(:\d{2})?.*$")
SIN_DECORADO = re.compile(r"^(?:U\$S|US\$|USD|\$|ARS)\s*|\s*(?:U\$S|US\$|USD|\$|ARS)$")
IMPORTE_SIN_MILES = re.compile(r"^(\d{1,16})(?:[.,](\d{1,2}))?$", re.ASCII)
IMPORTE_MILES_PUNTO = re.compile(r"^(\d{1,3}(?:\.\d{3}){1,4})(?:,(\d{1,2}))?$", re.ASCII)
IMPORTE_MILES_COMA = re.compile(r"^(\d{1,3}(?:,\d{3}){1,4})(?:\.(\d{1,2}))?$", re.ASCII)
NUMERO_VALIDO = re.compile(r"^[A-Z0-9]{4,40}$")
CUENTA = re.compile(r"[0-9]{22,}")
DIGITOS_LARGOS = re.compile(r"\d[\d .-]{9,}\d")
COLUMNA_VALIDA = re.compile(FORMATO_COLUMNA)


class ExtractoInvalido(ValueError):
    """El archivo no se puede leer como un CSV de un extracto."""


class DemasiadasLineas(ExtractoInvalido):
    """El archivo pasa del máximo de líneas."""


class ColumnasNoEncontradas(ValueError):
    """El archivo no tiene alguna de las columnas pedidas."""


@dataclass(frozen=True)
class Tabla:
    columnas: list[str]
    filas: list[tuple[int, list[str]]]
    """Cada línea de datos, con su número de línea en el archivo."""


@dataclass(frozen=True)
class Movimiento:
    hash_linea: str
    fecha: date
    importe_centavos: int
    numero_operacion: str | None
    moneda: str


@dataclass(frozen=True)
class Lectura:
    movimientos: list[Movimiento]
    desde: date
    hasta: date
    filas_no_leidas: list[int]


def _decodificar(cuerpo: bytes) -> str:
    if b"\x00" in cuerpo and not cuerpo.startswith(BOMS_UTF16):
        raise ExtractoInvalido("tiene bytes nulos: no es un archivo de texto")
    if cuerpo.startswith(BOMS_UTF16):
        try:
            return cuerpo.decode("utf-16")
        except UnicodeDecodeError as e:
            raise ExtractoInvalido("codificación no admitida") from e
    for codificacion in CODIFICACIONES:
        try:
            return cuerpo.decode(codificacion)
        except UnicodeDecodeError:
            continue
    raise ExtractoInvalido("codificación no admitida")


def _filas(texto: str, separador: str) -> list[tuple[int, list[str]]]:
    lector = csv.reader(io.StringIO(texto, newline=""), delimiter=separador, strict=True)
    try:
        return [(lector.line_num, fila) for fila in lector if any(c.strip() for c in fila)]
    except csv.Error as e:
        raise ExtractoInvalido("no es un CSV") from e


def _es_tabla(filas: list[list[str]]) -> bool:
    """Si el primero es un encabezado de verdad, con solo nombres, y debajo hay una columna de
    fechas. Más columnas no dice nada: las comas de una descripción también las dan."""
    encabezado, *datos = filas
    if any(leer_fecha(c) is not None or leer_importe(c) is not None for c in encabezado):
        return False
    vistos = datos[:LINEAS_PARA_SEPARADOR]
    return any(
        sum(leer_fecha(f[i]) is not None for f in vistos) * 2 > len(vistos)
        for i in range(len(encabezado))
    )


def _estructura(filas: list[tuple[int, list[str]]]) -> tuple[bool, int, int]:
    """Si hay una tabla, cuántas líneas la forman y de cuántas columnas. Encabezado y datos
    comparten el ancho; el título y los datos de la cuenta que los bancos ponen arriba, no. Sin
    nada que se reconozca como tabla, gana el ancho con más líneas."""
    por_ancho = defaultdict(list)
    for _, fila in filas:
        por_ancho[len(fila)].append(fila)
    return max(
        ((_es_tabla(v), len(v), c) for c, v in por_ancho.items()
         if c >= MINIMO_COLUMNAS and len(v) > 1),
        default=(False, 0, 0),
    )  # fmt: skip


def _separador(texto: str) -> str:
    """El que da la mejor estructura."""
    inicio = "\n".join(texto.splitlines()[:LINEAS_PARA_SEPARADOR])
    candidatos = []
    for separador in SEPARADORES:
        try:
            estructura = _estructura(_filas(inicio, separador))
        except ExtractoInvalido:
            continue
        if estructura[2] > 0:
            candidatos.append((*estructura, separador))
    if not candidatos:
        raise ExtractoInvalido("no tiene columnas")
    return max(candidatos, key=lambda c: c[:3])[3]


def _nombres(encabezado: list[str]) -> list[str]:
    """Una columna sin nombre se llama por su lugar, y una repetida lleva su número."""
    vistos: Counter[str] = Counter()
    nombres = []
    for i, crudo in enumerate(encabezado, start=1):
        # Lo que se tomó por encabezado puede ser la línea de la cuenta: nunca se guarda una.
        nombre = DIGITOS_LARGOS.sub("…", crudo.strip()[:LARGO_ANTES_DE_MOSTRAR]) or f"Columna {i}"
        vistos[nombre] += 1
        if vistos[nombre] > 1:
            nombre = f"{nombre} ({vistos[nombre]})"
        if COLUMNA_VALIDA.fullmatch(nombre) is None:
            raise ExtractoInvalido("un encabezado no es un nombre de columna")
        nombres.append(nombre)
    return nombres


def leer_tabla(cuerpo: bytes, maximo_lineas: int) -> Tabla:
    """El encabezado es la primera línea con el ancho de la tabla."""
    texto = _decodificar(cuerpo)
    filas = _filas(texto, _separador(texto))
    if len(filas) > maximo_lineas:
        raise DemasiadasLineas("tiene demasiadas líneas")
    columnas = _estructura(filas)[2]
    if columnas > MAXIMO_COLUMNAS:
        raise ExtractoInvalido("tiene demasiadas columnas")
    inicio = next(i for i, (_, f) in enumerate(filas) if len(f) == columnas)
    return Tabla(_nombres(filas[inicio][1]), filas[inicio + 1 :])


def _sin_cuentas(celda: str) -> str:
    return DIGITOS_LARGOS.sub("…", celda.strip()[:LARGO_ANTES_DE_MOSTRAR])[:LARGO_MUESTRA]


def _celda_para_mostrar(celda: str) -> str:
    limpia = _sin_cuentas(celda)
    if limpia.startswith(INICIO_DE_FORMULA) and leer_importe(limpia) is None:
        return "'" + limpia
    return limpia


def muestra(tabla: Tabla) -> list[str]:
    """La primera línea de datos, para elegir las columnas."""
    largo = len(tabla.columnas)
    fila = next((f for _, f in tabla.filas if len(f) == largo), [""] * largo)
    return [_celda_para_mostrar(c) for c in fila]


def signo_sugerido(tabla: Tabla) -> SignoExtracto:
    """Con algún importe negativo en cualquier columna, la columna lleva signo."""
    for _, fila in tabla.filas:
        for celda in fila:
            valor = leer_importe(celda)
            if valor is not None and valor < 0:
                return SignoExtracto.CON_SIGNO
    return SignoExtracto.SIN_SIGNO


def leer_fecha(celda: str) -> date | None:
    limpia = HORA.sub("", celda.strip())
    for formato, orden in FECHAS:
        partes = formato.fullmatch(limpia)
        if partes is None:
            continue
        a, b, c = (int(x) for x in partes.groups())
        anio, mes, dia = {"dma": (c, b, a), "dma2": (2000 + c, b, a), "amd": (a, b, c)}[orden]
        try:
            fecha = date(anio, mes, dia)
        except ValueError:
            return None
        return fecha if fecha.year >= PRIMER_ANIO else None
    return None


def leer_importe(celda: str) -> Decimal | None:
    """Con su signo. Acepta «1.234,56», «1,234.56», «1234,56», «(1.234,56)» y «1.234,56-». Lo que
    tenga más de dos decimales no es un importe: `None`."""
    limpia = celda.strip().replace(" ", "").replace(" ", "")
    signos = []
    if limpia.startswith("(") and limpia.endswith(")"):
        signos, limpia = ["-"], limpia[1:-1]
    if limpia.endswith("-"):
        signos, limpia = [*signos, "-"], limpia[:-1]
    for _ in range(2):  # el signo puede ir antes o después de la moneda
        if limpia[:1] in ("-", "+"):
            signos, limpia = [*signos, limpia[0]], limpia[1:]
        limpia = SIN_DECORADO.sub("", limpia)
    if len(signos) > 1:
        return None
    negativo = signos == ["-"]
    for formato, miles in ((IMPORTE_SIN_MILES, ""), (IMPORTE_MILES_PUNTO, "."),
                           (IMPORTE_MILES_COMA, ",")):  # fmt: skip
        partes = formato.fullmatch(limpia)
        if partes is None:
            continue
        enteros = partes[1].replace(miles, "") if miles else partes[1]
        valor = Decimal(f"{enteros}.{partes[2] or '0'}")
        return -valor if negativo else valor
    return None


def leer_numero(celda: str) -> str | None:
    """Como lo guarda `registrar_pago_manual`. Sin un número válido, la línea no tiene número."""
    limpia = celda.strip()
    if limpia.startswith(INICIO_DE_FORMULA):
        return None
    numero = re.sub(r"[^A-Za-z0-9]", "", limpia).upper()
    if NUMERO_VALIDO.fullmatch(numero) is None or CUENTA.search(numero) is not None:
        return None
    return numero


def hash_archivo(cuerpo: bytes) -> str:
    return hashlib.sha256(cuerpo).hexdigest()


def _hash(fecha: date, centavos: int, numero: str | None, vez: int, moneda: str) -> str:
    """La moneda sugerida no entra: las líneas guardadas antes de `0085` conservan su clave."""
    texto = f"{fecha.isoformat()}|{centavos}|{numero or ''}|{vez}"
    if moneda != MONEDA_SUGERIDA:
        texto += f"|{moneda}"
    return hashlib.sha256(texto.encode()).hexdigest()


def _indice(tabla: Tabla, columna: str) -> int:
    try:
        return tabla.columnas.index(columna)
    except ValueError:
        raise ColumnasNoEncontradas(columna) from None


def tiene_columnas(tabla: Tabla, columnas: ColumnasExtracto) -> bool:
    pedidas = [columnas.fecha, columnas.importe]
    if columnas.numero_operacion is not None:
        pedidas.append(columnas.numero_operacion)
    return all(c in tabla.columnas for c in pedidas)


def _debito(valor: Decimal, signo: SignoExtracto) -> Decimal | None:
    """Lo que salió de la cuenta; `None` si la línea no es un débito."""
    if signo == SignoExtracto.CON_SIGNO:
        return -valor if valor < 0 else None
    return valor if valor > 0 else None


def leer_movimientos(tabla: Tabla, columnas: ColumnasExtracto) -> Lectura | None:
    """Los débitos del archivo. `None` si no se pudo leer ninguna línea: las columnas no son."""
    fecha_en = _indice(tabla, columnas.fecha)
    importe_en = _indice(tabla, columnas.importe)
    numero_en = None
    if columnas.numero_operacion is not None:
        numero_en = _indice(tabla, columnas.numero_operacion)
    veces: Counter[tuple] = Counter()
    movimientos = []
    fechas = []
    no_leidas = []
    for linea, fila in tabla.filas:
        if len(fila) != len(tabla.columnas):
            no_leidas.append(linea)
            continue
        crudo_fecha, crudo_importe = fila[fecha_en].strip(), fila[importe_en].strip()
        if crudo_importe == "" and (crudo_fecha == "" or columnas.signo == SignoExtracto.SIN_SIGNO):
            # Un pie sin datos, o una línea que no es débito en una columna solo de débitos.
            fecha = leer_fecha(crudo_fecha)
            if fecha is not None:
                fechas.append(fecha)
            continue
        fecha, valor = leer_fecha(crudo_fecha), leer_importe(crudo_importe)
        if fecha is None or valor is None or abs(valor) * CENTAVOS_POR_PESO > MAXIMO_CENTAVOS:
            no_leidas.append(linea)
            continue
        fechas.append(fecha)
        debito = _debito(valor, columnas.signo)
        if debito is None or debito == 0:
            continue
        centavos = int(debito * CENTAVOS_POR_PESO)
        numero = None if numero_en is None else leer_numero(fila[numero_en])
        clave = (fecha, centavos, numero)
        hash_linea = _hash(*clave, veces[clave], columnas.moneda)
        movimientos.append(Movimiento(hash_linea, fecha, centavos, numero, columnas.moneda))
        veces[clave] += 1
    if not fechas:
        return None
    return Lectura(movimientos, min(fechas), max(fechas), no_leidas)


# Comparar -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class MovimientoGuardado:
    id: UUID
    fecha: date
    importe_centavos: int
    moneda: str
    numero_operacion: str | None
    pago_id: UUID | None
    """El pago que ya tiene por un extracto anterior."""


@dataclass(frozen=True)
class PagoComparable:
    pago_id: UUID
    numero_operacion: str
    fecha_operacion: date
    importe_centavos: int
    moneda: str
    conforme: bool
    cotejado: bool
    anulado: bool
    """Alguien anuló un cotejo suyo (`0083`): ya no se tilda solo."""
    puede_cotejar: bool


@dataclass(frozen=True)
class Comparacion:
    pares: list[tuple[MovimientoGuardado, PagoComparable]]
    """Los que coinciden, sin el que el movimiento ya tenía."""
    sin_pago: list[MovimientoGuardado]
    sin_movimiento: list[PagoComparable]
    ya_atados: int


def comparar(
    movimientos: list[MovimientoGuardado], pagos: list[PagoComparable], desde: date, hasta: date
) -> Comparacion:
    """`sin_movimiento`: los pagos conformes sin cotejar del período que ningún débito explica."""
    por_id = {p.pago_id: p for p in pagos}
    usados = {m.pago_id for m in movimientos if m.pago_id is not None}
    ya_atados = sum(1 for m in movimientos if m.pago_id is not None)
    pendientes = [m for m in movimientos if m.pago_id is None]
    por_numero = {p.numero_operacion: p for p in pagos}

    pares = []
    sin_pago = []
    sin_numero = []
    discutidos = set()
    for m in pendientes:
        if m.numero_operacion is None:
            sin_numero.append(m)
            continue
        p = por_numero.get(m.numero_operacion)
        if p is None or p.pago_id in usados:
            sin_pago.append(m)
        elif p.importe_centavos != m.importe_centavos or p.moneda != m.moneda:
            discutidos.add(p.pago_id)
            sin_pago.append(m)
        else:
            usados.add(p.pago_id)
            pares.append((m, p))

    lineas = defaultdict(list)
    for m in sin_numero:
        lineas[m.fecha, m.importe_centavos, m.moneda].append(m)
    candidatos = defaultdict(list)
    for p in por_id.values():
        if p.pago_id not in usados and p.pago_id not in discutidos:
            candidatos[p.fecha_operacion, p.importe_centavos, p.moneda].append(p)
    for clave, iguales in lineas.items():
        pagos_iguales = candidatos.get(clave, [])
        if len(iguales) == len(pagos_iguales):
            pares.extend(zip(iguales, pagos_iguales, strict=True))
            usados.update(p.pago_id for p in pagos_iguales)
        else:
            sin_pago.extend(iguales)

    sin_movimiento = [
        p for p in pagos
        if p.pago_id not in usados and p.conforme and not p.cotejado
        and desde <= p.fecha_operacion <= hasta
    ]  # fmt: skip
    return Comparacion(pares, sin_pago, sin_movimiento, ya_atados)
