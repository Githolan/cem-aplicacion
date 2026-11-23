"""Ensayo del P0 en Modo Consejo en staging, de punta a punta (CEM LAB: SINTETICO, PRUEBA, NO REAL).

H6 del piloto: los flujos del P0 en Modo Consejo (`0119` a `0128`) están probados por las pruebas
de la PC y, en staging, solo por controles del bundle y de la base; el camino por HTTP del
frontend de staging (nginx, cookies, CSRF, claves de reintento, el encabezado de la ayuda) no se
había recorrido. Este ensayo lo recorre con datos sintéticos, en staging y nunca en producción:

1. **Liquidación importada.** Un PDF sintético con la geometría de Mis Expensas (el mismo de las
   pruebas, `tests/test_mis_expensas.liquidacion`), que este proceso lee antes con el lector
   determinista (`mis_expensas.leer`) y que solo se sube si lo reconoce: así la lectura de
   `cem-trabajos` no pasa nunca por el modelo (no gasta `CEM_LLM_*`). El Consejo lo sube, el
   antivirus real lo analiza, `cem-trabajos` lo lee, se decide cada dato y se confirma; una
   segunda del mismo período se niega sin reemplazo y entra reemplazando, con motivo: la primera
   queda `reemplazada`.
2. **Lecturas.** El propietario ve en «Mis expensas» (`mi-cuenta`, `resumen`) solo su unidad y no
   el panel; el Consejo ve la liquidación oficial y el panel con las cifras, la señal del perfil
   normativo (`enlace_documentacion`, la que da la lectura local del mismo PDF: sin enlaces y con
   texto en cada página, `no_encontrado`), los vencimientos y, después de registrar un
   presupuesto, los desvíos exactos por rubro.
3. **Reclamo** del propietario sobre la oficial (citar la reemplazada se niega), que responde el
   Consejo.
4. **Reparaciones** pedidas por el propietario y gestionadas por el Consejo: presupuesto y factura
   subidos y analizados por el antivirus real, aprobación con una decisión, y el cierre por un
   gasto de la liquidación confirmada (PA11: la reemplazada no sirve, `gasto_no_oficial`; un gasto
   ya enlazado no cierra otra, `gasto_ya_enlazado`).
5. **Comunicado** emitido y leído por el propietario; uno con un número de cuenta se niega
   (`texto_con_cuenta`).
6. **Ayuda (RF53).** El propietario autoriza a otra persona sintética; quien ayuda consulta y
   prepara un reclamo (la auditoría lo registra en representación); registrar un presupuesto se
   niega con `ayuda_no_admitida` (auditado); revocada, quien ayudaba recibe `ayuda_revocada`.
7. **Bandeja del Consejo.** Lo que cuenta el inicio (las mismas lecturas que `inicio/pendientes.ts`)
   coincide con lo creado, y con lo que lee `nucleo.pendientes_consejo_para_avisar` en el mismo
   momento. El aviso diario se arma con el código real (`avisos_consejo.leer`,
   `avisos_pagos.sumar_consejo`, `texto`, `registrar_aviso_pendientes`, `correo.solicitar`) **solo
   para las personas de este consorcio**: `avisos_pagos.avisar` entero mandaría el aviso a todos
   los edificios de staging, también a casillas reales. El correo encolado lleva solo cuántos: ni
   textos, ni la cuenta, ni nombres de personas.
8. **Exportación** (`--exportar`, en `cem-trabajos-staging`, que tiene la credencial de
   exportación): el paquete lleva las partes nuevas (liquidaciones importadas, comunicados,
   reclamos, reparaciones, perfil normativo, ayudas, presupuestos) y ninguna cuenta.
9. La cadena de auditoría del consorcio, ningún correo del consorcio fuera de `.invalid`, ningún
   trabajo muerto, y todas las sesiones revocadas.

**Cómo se corre** (bash o Git Bash; en PowerShell, `Get-Content -Raw <script> | ssh oci '...'`).
Primero, sin escribir nada, la guarda y la lectura local del PDF:

    ssh oci 'docker exec -i -w /app -e PYTHONPATH=/app <cem-backend-staging> python - \\
        --consorcio <uuid nuevo> --base <host de cem-postgres-staging> \\
        --url https://coolify-proxy --host cem-staging.holancloud.com \\
        --sin-verificar-certificado --simular' < backend/pruebas/ensayo_modo_consejo_staging.py

Después lo mismo sin `--simular`. Imprime un resumen JSON con la clave `exportacion`; su base64
va a la segunda corrida, en `cem-trabajos-staging`:

    ssh oci 'docker exec -i -w /app -e PYTHONPATH=/app <cem-trabajos-staging> python - \\
        --exportar --base <host de cem-postgres-staging> --datos <base64 de exportacion>' \\
        < backend/pruebas/ensayo_modo_consejo_staging.py

No va en la imagen (emite sesiones de prueba). **Guarda dura**, antes de escribir nada: el
`CEM_ORIGEN_PUBLICO` del contenedor es de staging (su primer nivel termina en `-staging`), el host
de `CEM_DATABASE_URL` es `--base`, `--host` es el del origen público, y el consorcio `--consorcio`
no existe: **corre una sola vez por consorcio** (código 4 si ya existe), para que lo que cuenta
la bandeja sea exactamente lo que crea la corrida.

**Qué deja en staging.** Un consorcio nuevo `NOMBRE_CONSORCIO` en Modo Consejo, vinculado al
perfil normativo de CABA, con cuatro unidades (`PB-A`, `1-A`, `1-B`, `2-A`); cuatro personas
`@cem-prueba.invalid` (`cem-trabajos` no intenta mandarles correo: `correo.DOMINIO_RESERVADO`);
una decisión y dos mandatos de Consejo dados de baja al terminar (el de consejo1, al terminar la
exportación); dos liquidaciones importadas (una reemplazada y la confirmada), sus documentos
y los de las reparaciones; un presupuesto; dos reclamos (uno respondido y uno presentado por
quien ayudó); tres reparaciones (dos cerradas y una pedida); un comunicado; una ayuda revocada;
los correos en la cola, a `.invalid`; y los eventos de auditoría. Todo de solo agregar y marcado
SINTETICO. Ninguna llamada al modelo.

Sale con 0 solo si todo dio lo esperado; ante el primer desvío falla con el detalle y aun así
revoca las sesiones y da de baja los mandatos.
"""

import argparse
import base64
import io
import json
import os
import re
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from sqlalchemy import Engine, text
from sqlalchemy.engine import make_url

NOMBRE_CONSORCIO = "CEM LAB — Modo Consejo sintético (SINTETICO, PRUEBA, NO REAL)"
DOMINIO = "cem-prueba.invalid"
SUFIJO_STAGING = "-staging"
ZONA = "America/Argentina/Buenos_Aires"
JURISDICCION = "caba"
REFERENCIA = "ACTA SINTETICA DE PRUEBA — SIN VALIDEZ JURIDICA — ensayo de Modo Consejo en staging"
IP_DE_PRUEBA = "198.51.100.79"
"""RFC 5737: otra que la del smoke (…77) y la de los ensayos de egresos y Autogestión (…78)."""
ENCABEZADO_AYUDA = "X-CEM-Ayuda"
UNIDADES = ("PB-A", "1-A", "1-B", "2-A")
"""Las tres primeras, las de la liquidación sintética (su «Depto.»); la cuarta, de quien ayuda."""
ROLES = {"propietario": "PB-A", "consejo1": "1-A", "consejo2": "1-B", "ayudante": "2-A"}
PERIODO = date(2026, 9, 1)
VENCIMIENTOS = ["2026-10-13", "2026-10-23"]
"""Los de la liquidación sintética («1er/2do Vencimiento»), como los da la importación."""
RUBROS_LIQUIDADOS = {1: Decimal("1500.50"), 2: Decimal("1000.00")}
TOTAL_LIQUIDADO = Decimal("2500.50")
TOTAL_PB_A = Decimal("1301.00")
PRESUPUESTO = {1: Decimal("1400.00"), 2: Decimal("1100.00")}
DESVIOS = {1: (Decimal("100.50"), Decimal("7.18")), 2: (Decimal("-100.00"), Decimal("-9.09"))}
"""Liquidado − presupuestado, y × 100 / presupuestado a dos decimales (`0128`)."""
GASTO_REPARACION_1 = "gasto:2:1"
GASTO_REPARACION_2 = "gasto:1:1"
ESPERA_ANTIVIRUS_S = 180
ESPERA_LECTURA_S = 180
ESPERA_MAXIMA_CUPO_S = 60
PESOS_1 = (7, 1, 3, 9, 7, 1, 3)
PESOS_2 = (3, 9, 7, 1, 3, 9, 7, 1, 3, 9, 7, 1, 3)
PARTES_NUEVAS = {
    "liquidaciones_importadas/importacion_liquidacion": 2,
    "liquidaciones_importadas/campo_preliminar": 1,
    "comunicados/comunicado": 1,
    "comunicados/comunicado_destinatario": 1,
    "reclamos_liquidacion/reclamo_liquidacion": 2,
    "reclamos_liquidacion/reclamo_liquidacion_estado": 1,
    "reparaciones/reparacion": 3,
    "reparaciones/reparacion_gestion": 1,
    "reparaciones/reparacion_adjunto": 4,
    "reparaciones/reparacion_estado": 1,
    "perfil_normativo/perfil_normativo": 1,
    "perfil_normativo/busqueda_enlaces_liquidacion": 2,
    "ayudas/autorizacion_ayuda": 1,
    "ayudas/autorizacion_ayuda_revocacion": 1,
    "presupuestos/presupuesto": 1,
    "presupuestos/presupuesto_rubro": 2,
}
"""Cada entidad nueva del paquete y cuántas filas, como mínimo, deja la corrida."""


class Negado(Exception):
    """La guarda dura: el entorno no es staging, o el consorcio ya existe."""


class Fallo(Exception):
    """Un paso no dio lo esperado."""


def comprobar(condicion: bool, que: str) -> None:
    if not condicion:
        raise Fallo(que)


def id_de_corrida() -> str:
    """Ocho hexadecimales sin cinco dígitos seguidos: en un texto libre nunca parecen un teléfono
    ni una cuenta (P3 153)."""
    while True:
        candidato = uuid4().hex[:8]
        if re.search(r"[0-9]{5}", candidato) is None:
            return candidato


def _digito(cifras: str, pesos: tuple[int, ...]) -> str:
    return str((10 - sum(int(c) * p for c, p in zip(cifras, pesos, strict=True)) % 10) % 10)


def cbu_sintetico(n: int) -> str:
    """Una cuenta con sus dos dígitos verificadores y el prefijo 999, que no es de ningún banco."""
    bloque1 = f"9990{n % 1000:03d}"
    bloque2 = f"{n:013d}"
    return bloque1 + _digito(bloque1, PESOS_1) + bloque2 + _digito(bloque2, PESOS_2)


def cuit_sintetico(n: int) -> str:
    """Un CUIT `30…` con su dígito verificador (salta los que darían 10)."""
    for intento in range(n, n + 20):
        base = f"30{intento % 10**8:08d}"
        resto = 11 - sum(int(x) * w for x, w in zip(base, (5, 4, 3, 2, 7, 6, 5, 4, 3, 2),
                                                        strict=True)) % 11  # fmt: skip
        if resto != 10:
            return base + str(0 if resto == 11 else resto)
    raise RuntimeError("sin CUIT sintético")


# La liquidación sintética --------------------------------------------------------------------
# La de `tests/test_mis_expensas.liquidacion` escrita como en `tests/test_importacion_liquidacion_
# 0113.pdf_de`: el script corre leído de la entrada estándar, sin el directorio `tests`, así que
# no puede importarlas. Los nombres de las unidades llevan la marca SINTETICO.

LETRA = 4.0
ESPACIO = 3.0
CUERPO = 7
ASCENSO = 0.718
ANCHOS = {**dict.fromkeys("0123456789", 556), ",": 278, ".": 278, "%": 889, "-": 333}
GC, AYSA, TOT = 355, 425, 565
COLUMNAS_PRORRATEO = (280, 334, 377, 420, 461, 486, 540, 564, 614, 729, 758, 811)
FILAS_UNIDAD = {
    "001": ("PB-A", "LAB Uno SINTETICO",
            ["1.000,00", "1.000,00", "0,00", "0,00", "0,00", "50,00", "1.000,00", "60,00",
             "300,30", "0,00", "0,70", "1.301,00"]),
    "002": ("1-A", "LAB Dos SINTETICO",
            ["500,00", "0,00", "500,00", "25,00", "0,00", "30,00", "600,00", "40,00", "200,20",
             "0,00", "0,80", "1.326,00"]),
    "003": ("1-B", "LAB Tres SINTETICO",
            ["300,00", "310,00", "0,00", "0,00", "10,00", "20,00", "400,00", "-", "0,00", "0,00",
             "0,00", "390,00"]),
}  # fmt: skip
TOTALES_PRORRATEO = ["1.800,00", "1.310,00", "500,00", "25,00", "10,00", "100,00", "2.000,00",
                     "100,00", "500,50", "0,00", "1,50", "3.017,00"]  # fmt: skip


def _izq(texto: str, x0: float) -> list[tuple[str, float, float]]:
    salida, x = [], x0
    for p in texto.split():
        salida.append((p, x, x + LETRA * len(p)))
        x += LETRA * len(p) + ESPACIO
    return salida


def _der(texto: str, x1: float) -> list[tuple[str, float, float]]:
    return [(texto, x1 - LETRA * len(texto), x1)]


def _renglon(top: float, *celdas):
    from app.liquidacion.mis_expensas import Palabra

    return [Palabra(t, x0, x1, top, top + 8) for celda in celdas for t, x0, x1 in celda]


def _pagina(*renglones, apaisada: bool = False):
    from app.liquidacion.mis_expensas import Pagina

    ancho, alto = (841.0, 595.0) if apaisada else (595.0, 841.0)
    return Pagina(ancho, alto, tuple(p for r in renglones for p in r))


def _encabezado_gastos(top: float, con_rubros: bool = True):
    rubros = [_izq("RUBROS - CONCEPTO", 124)] if con_rubros else []
    return _renglon(top, *rubros, _izq("Gastos Comunes", 295), _izq("AySA", 383),
                    _izq("TOTAL", 521))  # fmt: skip


def _importes(top: float, texto: str, gc: str, aysa: str, total: str):
    return _renglon(top, _izq(texto, 43), _der(gc, GC), _der(aysa, AYSA), _der(total, TOT))


def _encabezado_prorrateo():
    return [
        _renglon(37, _izq("ESTADO DE CUENTAS Y PRORRATEO DE GASTOS", 336)),
        _renglon(54, _izq("Gastos Comunes", 476), _izq("AySA", 570)),
        _renglon(55, _izq("Saldo", 246), _izq("Pagos", 299)),
        _renglon(61, _izq("UF", 34), _izq("Depto.", 51), _izq("Propietario", 134),
                 _izq("Deuda", 347), _izq("Interés", 389), _izq("Disponible", 425),
                 _izq("Otros gastos", 640), _izq("Red.", 734), _izq("TOTAL", 776)),
        _renglon(66, _izq("Anterior", 242), _izq("Recibidos", 290)),
        _renglon(67, _izq("%", 472), _izq("Monto", 495), _izq("%", 551), _izq("Monto", 575)),
    ]  # fmt: skip


def _fila_unidad(top: float, uf: str):
    depto, nombre, valores = FILAS_UNIDAD[uf]
    return _renglon(top, _izq(uf, 32), _izq(depto, 53), _izq(nombre, 77),
                    *(_der(v, x) for v, x in zip(valores, COLUMNAS_PRORRATEO,
                                                 strict=True)))  # fmt: skip


def liquidacion_sintetica():
    """Septiembre de 2026: dos rubros, tres gastos y tres unidades que cierran (sin diferencias)."""
    p1 = _pagina(
        _renglon(39, _izq("EXPENSAS - Liquidación de Septiembre 2026", 202)),
        _renglon(111, _izq("Período: Septiembre 2026", 445)),
        _renglon(122, _izq("1er Vencimiento: 13/10/2026", 445)),
        _renglon(133, _izq("2do Vencimiento: 23/10/2026", 445)),
        _renglon(200, _izq("(Conf. Art. 10) PAGOS DEL PERIODO", 37)),
        _encabezado_gastos(220),
        _renglon(233, _izq("1 SERVICIOS PUBLICOS", 31)),
        _renglon(247, _izq("EMPRESA DE LUZ - CUIT 30-00000000-0", 43)),
        _renglon(258, _izq("2 puertas de acceso", 43)),
        _importes(269, "Periodo Septiembre/2026 - Factura B", "1.000,00", "0,00", "1.000,00"),
        _importes(282, "AGUA SA - Factura B", "0,00", "500,50", "500,50"),
        _renglon(295, _izq("TOTAL CATEGORIA 1", 31), _der("60,01%", 494), _der("1.500,50", TOT)),
    )
    p2 = _pagina(
        _renglon(36, _izq("(Conf. Art. 10) PAGOS DEL PERIODO", 37)),
        _encabezado_gastos(54),
        _renglon(67, _izq("2 MANTENIMIENTO", 33)),
        _renglon(80, _izq("PLOMERO - Reparación de cañería", 43)),
        _renglon(91, _izq("Saldo $", 43), _der("300.000,00", 248), _der("1.000,00", GC),
                 _der("0,00", AYSA), _der("1.000,00", TOT)),
        _renglon(104, _izq("TOTAL CATEGORIA 2", 31), _der("39,99%", 494), _der("1.000,00", TOT)),
        _renglon(125, _izq("TOTAL DE GASTOS", 266)),
        _encabezado_gastos(143, con_rubros=False),
        _renglon(156, _izq("TOTALES", 31), _der("2.000,00", GC), _der("500,50", AYSA),
                 _der("2.500,50", TOT)),
        _renglon(190, _izq("ESTADO FINANCIERO", 259)),
        _renglon(203, _izq("SALDO INICIAL", 31), _der("9.000,00", TOT)),
    )  # fmt: skip
    p3 = _pagina(*_encabezado_prorrateo(), _fila_unidad(80, "001"), _fila_unidad(93, "002"),
                 apaisada=True)  # fmt: skip
    totales = _renglon(93, _izq("TOTALES", 31), *(_der(v, x) for v, x in zip(
        TOTALES_PRORRATEO, COLUMNAS_PRORRATEO, strict=True)))  # fmt: skip
    p4 = _pagina(*_encabezado_prorrateo(), _fila_unidad(80, "003"), totales, apaisada=True)
    return [p1, p2, p3, p4]


def _cadena(texto: str) -> str:
    return texto.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _es_valor(texto: str) -> bool:
    from app.liquidacion.mis_expensas import IMPORTE, PORCENTAJE

    return bool(IMPORTE.match(texto) or PORCENTAJE.match(texto) or texto == "-")


def pdf_de(paginas, titulo: str) -> bytes:
    """Cada palabra donde la ubica la liquidación: los valores terminan en su `x1`; las palabras
    de un mismo tramo de texto se escriben juntas desde su `x0`. `titulo` va en `/Info`: hace
    único cada archivo y lo marca como sintético."""
    flujos = []
    for p in paginas:
        ops = []
        renglones: dict[float, list] = {}
        for w in p.palabras:
            renglones.setdefault(w.top, []).append(w)
        for top, ws in renglones.items():
            y = p.alto - top - ASCENSO * CUERPO
            tramo: list = []
            for w in sorted(ws, key=lambda w: w.x0) + [None]:
                sigue = (w is not None and tramo and not _es_valor(w.texto)
                         and abs(w.x0 - tramo[-1].x1 - ESPACIO) < 0.01)  # fmt: skip
                if sigue:
                    tramo.append(w)
                    continue
                if tramo:
                    ops.append((tramo[0].x0, y, " ".join(t.texto for t in tramo)))
                tramo = []
                if w is None:
                    break
                if _es_valor(w.texto):
                    ancho = sum(ANCHOS[ch] for ch in w.texto) * CUERPO / 1000
                    ops.append((w.x1 - ancho, y, w.texto))
                else:
                    tramo = [w]
        flujo = "".join(
            f"BT /F1 {CUERPO} Tf {x:.2f} {y:.2f} Td ({_cadena(t)}) Tj ET\n" for x, y, t in ops
        ).encode("cp1252")
        flujos.append((p.ancho, p.alto, flujo))
    n = len(flujos)
    objetos: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        ("<< /Type /Pages /Kids [" + " ".join(f"{3 + 2 * i} 0 R" for i in range(n))
         + f"] /Count {n} >>").encode(),
    ]  # fmt: skip
    fuente = 3 + 2 * n
    for i, (ancho, alto, flujo) in enumerate(flujos):
        objetos.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {ancho:g} {alto:g}] /Contents"
            f" {4 + 2 * i} 0 R /Resources << /Font << /F1 {fuente} 0 R >> >> >>".encode()
        )
        objetos.append(b"<< /Length %d >>\nstream\n" % len(flujo) + flujo + b"endstream")
    objetos.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica"
                   b" /Encoding /WinAnsiEncoding >>")  # fmt: skip
    objetos.append(f"<< /Title ({_cadena(titulo)}) >>".encode("cp1252"))
    salida = bytearray(b"%PDF-1.4\n")
    posiciones = []
    for i, cuerpo in enumerate(objetos, 1):
        posiciones.append(len(salida))
        salida += f"{i} 0 obj\n".encode() + cuerpo + b"\nendobj\n"
    xref = len(salida)
    salida += f"xref\n0 {len(objetos) + 1}\n0000000000 65535 f \n".encode()
    salida += b"".join(f"{p:010d} 00000 n \n".encode() for p in posiciones)
    salida += (f"trailer\n<< /Size {len(objetos) + 1} /Root 1 0 R /Info {len(objetos)} 0 R >>"
               f"\nstartxref\n{xref}\n%%EOF\n").encode()  # fmt: skip
    return bytes(salida)


@dataclass(frozen=True)
class LecturaEsperada:
    periodo: date
    campos: int
    diferencias: int
    enlace: str
    """`encontrado`, `no_encontrado` o `sin_buscar`, con la regla de `leer_importacion` (`0125`)."""


def lectura_esperada(pdf: bytes) -> LecturaEsperada:
    """Lo que va a leer `cem-trabajos`, leído acá con el mismo código y sin el modelo. Si el
    formato no se reconoce, se niega: subirlo mandaría la lectura al modelo."""
    from app.liquidacion import enlaces, imagenes, importacion, mis_expensas, palabras_pdf

    paginas = palabras_pdf.paginas(pdf)
    try:
        lq = mis_expensas.leer(paginas)
    except mis_expensas.FormatoNoReconocido as e:
        raise Negado(f"el lector determinista no reconoce el PDF sintético ({e}): no se sube,"
                     " porque su lectura iría al modelo") from e  # fmt: skip
    try:
        dibujadas: list[bytes] | None = imagenes.de_pdf(pdf)
    except (imagenes.DemasiadasPaginas, imagenes.ImagenIlegible):
        dibujadas = None
    completa = (dibujadas is not None and len(dibujadas) == len(paginas)
                and all(p.palabras for p in paginas))  # fmt: skip
    hallados = enlaces.de_pdf(pdf, paginas, dibujadas or [])
    estado = "encontrado" if hallados else ("no_encontrado" if completa else "sin_buscar")
    ano, mes = lq.periodo
    return LecturaEsperada(date(ano, mes, 1), len(importacion.campos(lq, paginas)),
                           len(lq.diferencias), estado)  # fmt: skip


def pdf_documento(titulo: str) -> bytes:
    """Un PDF válido de una página con un renglón (presupuesto o factura de una reparación)."""
    from app.liquidacion.mis_expensas import Pagina

    return pdf_de([Pagina(595.0, 841.0, tuple(_renglon(100, _izq(titulo, 60))))], titulo)


# Guarda --------------------------------------------------------------------------------------


def exigir_staging(origen_publico: str, database_url: str, base: str, host: str | None) -> None:
    host_publico = urlsplit(origen_publico or "").hostname or ""
    if not host_publico.split(".")[0].endswith(SUFIJO_STAGING):
        raise Negado(f"CEM_ORIGEN_PUBLICO no es de staging: {host_publico!r}")
    if make_url(database_url).host != base:
        raise Negado(f"CEM_DATABASE_URL no apunta a --base {base!r}")
    # El HTTP va al mismo staging que la base: el proxy sirve también producción.
    if host is not None and host != host_publico:
        raise Negado(f"--host {host!r} no es el de CEM_ORIGEN_PUBLICO ({host_publico!r})")


# HTTP ----------------------------------------------------------------------------------------


@dataclass
class Respuesta:
    estado: int
    encabezados: dict[str, str]
    cuerpo: object
    texto: str

    def codigo(self) -> str | None:
        return self.cuerpo.get("codigo") if isinstance(self.cuerpo, dict) else None


@dataclass
class Navegador:
    """Un navegador con sus cookies, contra el frontend de staging (el `Host` lo elige Traefik)."""

    url: str
    host: str
    contexto: ssl.SSLContext | None
    cookies: dict[str, str] = field(default_factory=dict)

    def pedir(self, metodo: str, ruta: str, cuerpo: object = None, *, csrf: bool = False,
              clave: bool = False, crudo: bytes | None = None, tipo: str | None = None,
              ayuda: str | None = None) -> Respuesta:  # fmt: skip
        from app.identidad import sesiones
        from app.plataforma.idempotencia import ENCABEZADO

        encabezados = {"Host": self.host, "Accept": "application/json",
                       "CF-Connecting-IP": IP_DE_PRUEBA}  # fmt: skip
        datos = crudo
        if cuerpo is not None:
            datos = json.dumps(cuerpo).encode()
            encabezados["Content-Type"] = "application/json"
        elif tipo is not None:
            encabezados["Content-Type"] = tipo
        if self.cookies:
            encabezados["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        if csrf:
            encabezados[sesiones.ENCABEZADO_CSRF] = self.cookies[sesiones.COOKIE_CSRF]
        if clave:
            encabezados[ENCABEZADO] = str(uuid4())
        if ayuda is not None:
            encabezados[ENCABEZADO_AYUDA] = ayuda
        pedido = urllib.request.Request(self.url + ruta, data=datos, headers=encabezados,
                                        method=metodo)  # fmt: skip
        try:
            r = urllib.request.urlopen(pedido, context=self.contexto, timeout=60)
        except urllib.error.HTTPError as e:
            r = e
        for linea in r.headers.get_all("Set-Cookie") or []:
            galleta = SimpleCookie()
            galleta.load(linea)
            for nombre, morsel in galleta.items():
                if morsel["max-age"] == "0":
                    self.cookies.pop(nombre, None)
                else:
                    self.cookies[nombre] = morsel.value
        texto = r.read().decode(errors="replace")
        cuerpo_json = json.loads(texto) if texto and r.headers.get_content_type() == (
            "application/json") else None  # fmt: skip
        respuesta = Respuesta(r.status, {k.lower(): v for k, v in r.headers.items()},
                              cuerpo_json, texto)  # fmt: skip
        if respuesta.codigo() == "demasiadas_peticiones":
            espera = int(respuesta.encabezados.get("retry-after", "0"))
            if 0 < espera <= ESPERA_MAXIMA_CUPO_S:
                print(f"cupo por minuto agotado: espero {espera} s", flush=True)
                time.sleep(espera)
                return self.pedir(metodo, ruta, cuerpo, csrf=csrf, clave=clave, crudo=crudo,
                                  tipo=tipo, ayuda=ayuda)  # fmt: skip
        return respuesta

    def acto(self, ruta: str, cuerpo: object = None, **kw) -> Respuesta:
        """Una escritura como la manda la pantalla: con CSRF y clave de reintento nueva."""
        return self.pedir("POST", ruta, cuerpo, csrf=True, clave=True, **kw)


# Corrida -------------------------------------------------------------------------------------


@dataclass
class Corrida:
    motor: Engine
    consorcio: UUID
    origen_publico: str
    url: str
    host: str
    contexto: ssl.SSLContext | None
    base: str | None = None
    """El host de cem-postgres-staging (`--base`): cada escritura y la limpieza lo exigen."""
    id: str = field(default_factory=id_de_corrida)
    personas: dict[str, UUID] = field(default_factory=dict)
    correos: dict[str, str] = field(default_factory=dict)
    nombres: dict[str, str] = field(default_factory=dict)
    unidades: dict[str, UUID] = field(default_factory=dict)
    mandatos: dict[str, UUID] = field(default_factory=dict)
    navegadores: dict[str, "Navegador"] = field(default_factory=dict)
    controles: list[str] = field(default_factory=list)
    textos: dict[str, str] = field(default_factory=dict)
    """Los textos libres de la corrida: ninguno puede salir en el aviso diario."""
    cuenta: str = ""
    inicio: datetime | None = None
    hoy: date | None = None
    datos: dict = field(default_factory=dict)
    archivos: int = 0

    def ok(self, que: str) -> None:
        self.controles.append(que)
        print(f"ok  {que}", flush=True)

    def tx(self):
        """Toda escritura de la corrida en la base pasa por acá: repite la guarda (#286)."""
        from app.db import transaccion

        exigir_entorno(self)
        return transaccion(self.motor, persona_id=None, consorcio_id=self.consorcio)

    def c(self, resto: str = "") -> str:
        return f"/api/v1/consorcios/{self.consorcio}{resto}"

    def n(self, rol: str) -> Navegador:
        return self.navegadores[rol]

    def pdf(self, que: str) -> bytes:
        """Un PDF nuevo por archivo: dos iguales serían el mismo para el almacén."""
        self.archivos += 1
        return pdf_documento(f"{que} LAB {self.id}-{self.archivos} SINTETICO NO REAL")


def exigir_entorno(corrida: Corrida) -> None:
    """La guarda de la corrida, repetida por cada función que escribe o limpia (revisión de Codex
    en #286): el entorno del proceso es staging, la base del motor es `--base` y el `Host` es el
    del origen público. Llamada sin `--base` o fuera de staging, se niega sin escribir."""
    from app.config import cargar

    if not corrida.base:
        raise Negado("la corrida no tiene --base: no se escribe nada")
    config = cargar()
    exigir_staging(config.origen_publico, config.database_url, corrida.base, corrida.host)
    if corrida.motor.url.host != corrida.base:
        raise Negado(f"el motor de la corrida no apunta a --base {corrida.base!r}")
    if corrida.origen_publico != config.origen_publico:
        raise Negado("el origen público de la corrida no es el del proceso")


def denegado(corrida: Corrida, persona: UUID, accion: str, motivo: str) -> dict | None:
    """El último `autorizacion.denegada` de la persona para esa acción y motivo, con la ayuda."""
    with corrida.tx() as conn:
        fila = conn.execute(
            text("select en_representacion_de, autorizacion_id from auditoria.eventos"
                 " where consorcio_id = :c and actor_persona_id = :p"
                 " and accion = 'autorizacion.denegada' and despues->>'accion' = :a"
                 " and despues->>'motivo' = :m and creado_en >= :i"
                 " order by secuencia desc limit 1"),
            {"c": corrida.consorcio, "p": persona, "a": accion, "m": motivo, "i": corrida.inicio},
        ).mappings().one_or_none()  # fmt: skip
    return None if fila is None else dict(fila)


# Escena --------------------------------------------------------------------------------------


def alta_del_consorcio(corrida: Corrida) -> None:
    """Con el script del equipo (`scripts.alta_consorcio`): vincula el perfil normativo de CABA.
    Se niega si el consorcio ya existe: la corrida cuenta lo que crea."""
    from scripts import alta_consorcio

    exigir_entorno(corrida)
    archivo = alta_consorcio.Archivo.model_validate({
        "consorcio": {"id": str(corrida.consorcio), "nombre": NOMBRE_CONSORCIO,
                      "zona_horaria": ZONA, "jurisdiccion": JURISDICCION},
        "unidades": [{"designacion": d} for d in UNIDADES],
    })  # fmt: skip
    try:
        resultado = alta_consorcio.dar_de_alta(corrida.motor, archivo, simular=False)
    except alta_consorcio.Diferente as e:
        raise Negado(f"el consorcio {corrida.consorcio} ya existe y es otro: {e}") from e
    if resultado.estado != "creado":
        raise Negado(f"el consorcio {corrida.consorcio} ya existe: corre una sola vez")
    corrida.unidades = {d: u for d, u in resultado.unidades.items() if u is not None}


def entra(corrida: Corrida, rol: str) -> None:
    """La invitación de soporte a su unidad (`.invalid`: no sale correo) y la aceptación por
    HTTP."""
    from cem_contracts.estados import TipoVinculo
    from cem_contracts.invitaciones import InvitacionNueva

    from app.identidad import invitaciones

    corrida.correos[rol] = f"modo-consejo-{corrida.id}-{rol}@{DOMINIO}"
    corrida.nombres[rol] = f"LAB {rol} {corrida.id} SINTETICO"
    with corrida.tx() as conn:
        creada = invitaciones.crear(
            conn, consorcio_id=corrida.consorcio,
            nueva=InvitacionNueva(unidad_id=corrida.unidades[ROLES[rol]],
                                  tipo=TipoVinculo.PROPIETARIO, correo=corrida.correos[rol],
                                  nombre=corrida.nombres[rol]),
            invitada_por=None, origen_publico=corrida.origen_publico, origen="soporte",
        )  # fmt: skip
    n = Navegador(corrida.url, corrida.host, corrida.contexto)
    r = n.pedir("POST", "/api/v1/auth/invitacion/aceptar", {"token": creada.token})
    comprobar(r.estado == 200 and r.cuerpo["cuenta_nueva"] is True,
              f"aceptar la invitación de {rol}: {r.texto}")  # fmt: skip
    corrida.personas[rol] = UUID(r.cuerpo["persona_id"])
    corrida.navegadores[rol] = n


def confirmar_vinculo(corrida: Corrida, quien: str, de: str) -> None:
    """El Consejo confirma por HTTP el vínculo que registró soporte (`0110`, FL-PAD-02)."""
    with corrida.tx() as conn:
        vinculo = conn.execute(
            text("select id from nucleo.vinculo where consorcio_id = :c and persona_id = :p"),
            {"c": corrida.consorcio, "p": corrida.personas[de]},
        ).scalar_one()  # fmt: skip
    r = corrida.n(quien).acto(corrida.c(f"/vinculos/{vinculo}/confirmar"),
                              {"referencia": "Escritura SINTETICA (NO REAL)"})  # fmt: skip
    comprobar(r.estado == 200 and r.cuerpo["confirmado"] is True,
              f"{quien} confirma el vínculo de {de}: {r.texto}")  # fmt: skip


def preparar_escena(corrida: Corrida) -> None:
    alta_del_consorcio(corrida)
    for rol in ROLES:
        entra(corrida, rol)
    with corrida.tx() as conn:
        corrida.hoy = conn.execute(
            text("select nucleo.hoy_del_consorcio(:c)"), {"c": corrida.consorcio}
        ).scalar_one()
        decision = conn.execute(
            text("select nucleo.registrar_decision('asamblea', :h, :d, :r, null, 'soporte')"),
            {"h": corrida.hoy, "d": "SINTETICA: Modo Consejo y mandatos del ensayo",
             "r": REFERENCIA},
        ).scalar_one()  # fmt: skip
        conn.execute(text("select nucleo.cambiar_modo('consejo', :d, :h, null, 'soporte')"),
                     {"d": decision, "h": corrida.hoy})  # fmt: skip
        for rol in ("consejo1", "consejo2"):
            corrida.mandatos[rol] = conn.execute(
                text("select nucleo.alta_mandato('miembro_consejo', :p, null, null, null, :d, :h,"
                     " null, null, null, 'soporte')"),
                {"p": corrida.personas[rol], "d": decision, "h": corrida.hoy},
            ).scalar_one()  # fmt: skip
    corrida.datos["decision"] = str(decision)
    confirmar_vinculo(corrida, "consejo1", "propietario")
    confirmar_vinculo(corrida, "consejo1", "consejo2")
    confirmar_vinculo(corrida, "consejo2", "consejo1")
    r = corrida.n("propietario").pedir("GET", "/api/v1/me/consorcios")
    [fila] = [x for x in r.cuerpo if x["id"] == str(corrida.consorcio)]
    comprobar(fila["vinculos"] == ["propietario"], f"el propietario entra al edificio: {r.texto}")
    corrida.ok("consorcio por alta_consorcio (CABA) en Modo Consejo; cuatro personas por"
               " invitación; dos mandatos de Consejo; el Consejo confirma tres vínculos por HTTP"
               " (el de quien va a ayudar queda sin confirmar)")  # fmt: skip


# 1. La liquidación importada -----------------------------------------------------------------


def subir_documento(corrida: Corrida, rol: str, categoria: str, titulo: str,
                    contenido: bytes) -> tuple[str, str]:  # fmt: skip
    """Documento y versión por HTTP; espera a que el antivirus real la deje disponible."""
    n = corrida.n(rol)
    r = n.acto(corrida.c("/documentos"), {"categoria": categoria, "titulo": titulo,
                                          "fecha": corrida.hoy.isoformat()})  # fmt: skip
    comprobar(r.estado == 201, f"crear el documento {titulo!r}: {r.texto}")
    doc = r.cuerpo["id"]
    r = n.acto(corrida.c(f"/documentos/{doc}/versiones"), crudo=contenido, tipo="application/pdf")
    comprobar(r.estado == 201 and r.cuerpo["estado"] == "en_cuarentena",
              f"subir la versión de {titulo!r}: {r.texto}")  # fmt: skip
    version = r.cuerpo["id"]
    ruta = corrida.c(f"/documentos/{doc}/versiones/{version}/estado")
    limite = time.monotonic() + ESPERA_ANTIVIRUS_S
    estado = "en_cuarentena"
    while estado == "en_cuarentena" and time.monotonic() < limite:
        time.sleep(2)
        r = n.pedir("GET", ruta)
        comprobar(r.estado == 200, f"estado de la versión: {r.texto}")
        estado = r.cuerpo["estado"]
    comprobar(estado == "disponible", f"el antivirus deja disponible {titulo!r}: {r.texto}")
    return doc, version


def importar(corrida: Corrida, letra: str, esperada: LecturaEsperada) -> str:
    """Sube la liquidación, la importa, espera la lectura de `cem-trabajos` y decide cada dato."""
    mc1 = corrida.n("consejo1")
    contenido = pdf_de(liquidacion_sintetica(), f"Liquidacion LAB {corrida.id}-{letra} SINTETICA")
    comprobar(lectura_esperada(contenido) == esperada, f"la liquidación {letra} se lee igual")
    doc, version = subir_documento(corrida, "consejo1", "liquidaciones_del_administrador",
                                   f"Liquidación {letra} LAB {corrida.id} SINTETICA (NO REAL)",
                                   contenido)  # fmt: skip
    r = mc1.acto(corrida.c("/liquidaciones-importadas"),
                 {"documento_id": doc, "version_documento_id": version})  # fmt: skip
    comprobar(r.estado == 201, f"importar la liquidación {letra}: {r.texto}")
    i = r.cuerpo["id"]
    limite = time.monotonic() + ESPERA_LECTURA_S
    detalle = {"estado": "leyendo"}
    while detalle["estado"] == "leyendo" and time.monotonic() < limite:
        time.sleep(2)
        r = mc1.pedir("GET", corrida.c(f"/liquidaciones-importadas/{i}"))
        comprobar(r.estado == 200, f"leer la importación {letra}: {r.texto}")
        detalle = r.cuerpo
    campos = detalle.get("campos", [])
    comprobar(
        (detalle["estado"], detalle.get("formato"), detalle.get("periodo"))
        == ("para_confirmar", "mis_expensas", esperada.periodo.isoformat())
        and len(campos) == esperada.campos and len(detalle["diferencias"]) == esperada.diferencias
        and {(c["origen"], c["estado"]) for c in campos} == {("determinista", "pendiente")},
        f"cem-trabajos la lee con el formato Mis Expensas, sin el modelo: {detalle.get('estado')}"
        f" {detalle.get('formato')} {detalle.get('periodo')} {len(campos)} campos"
        f" {detalle.get('motivo')}",
    )  # fmt: skip
    for campo in campos:
        r = mc1.acto(corrida.c(f"/liquidaciones-importadas/{i}/campos/{campo['id']}/decision"),
                     {"decision": "confirmado"})  # fmt: skip
        comprobar(r.estado == 200, f"confirmar el dato {campo['clave']}: {r.texto}")
    corrida.ok(f"liquidación {letra}: subida por el Consejo, analizada por el antivirus real, leída"
               f" por cem-trabajos con el lector determinista ({len(campos)} datos, sin"
               " diferencias) y cada dato confirmado por HTTP")  # fmt: skip
    return i


def para_confirmar(corrida: Corrida) -> int:
    """Lo que cuenta el inicio en «Liquidación del administrador» (`pendientes.ts`)."""
    r = corrida.n("consejo1").pedir("GET", corrida.c("/liquidaciones-importadas"))
    comprobar(r.estado == 200, f"listar las importaciones: {r.texto}")
    return sum(1 for x in r.cuerpo if x["estado"] == "para_confirmar")


def cuentas_del_aviso(corrida: Corrida) -> dict[str, dict[str, int]]:
    """Lo que lee `nucleo.pendientes_consejo_para_avisar` para este consorcio, por rol. Sin persona
    ni consorcio en el ámbito (con cualquiera de los dos, la base niega), como `cem-trabajos`."""
    from app.consorcios import avisos_consejo
    from app.db import transaccion

    exigir_entorno(corrida)
    with transaccion(corrida.motor, persona_id=None, consorcio_id=None) as conn:
        filas = [f for f in avisos_consejo.leer(conn) if f["consorcio_id"] == corrida.consorcio]
    por_persona = {v: k for k, v in corrida.personas.items()}
    return {por_persona.get(f["persona_id"], str(f["persona_id"])): f["cuentas"] for f in filas}


def recorrer_importacion(corrida: Corrida, esperada: LecturaEsperada) -> tuple[str, str]:
    mc1 = corrida.n("consejo1")
    a = importar(corrida, "A", esperada)
    r = mc1.acto(corrida.c(f"/liquidaciones-importadas/{a}/confirmacion"), {})
    comprobar(r.estado == 200 and r.cuerpo["estado"] == "confirmada", f"confirmar A: {r.texto}")
    b = importar(corrida, "B", esperada)
    contadas = para_confirmar(corrida)
    aviso = cuentas_del_aviso(corrida)
    comprobar(contadas == 1, f"el inicio cuenta una liquidación por confirmar: {contadas}")
    comprobar(aviso.get("consejo1") == {"importaciones": 1} == aviso.get("consejo2"),
              f"el aviso del Consejo cuenta lo mismo que el inicio: {aviso}")  # fmt: skip
    corrida.ok("bandeja: el inicio cuenta 1 liquidación por confirmar y el aviso del Consejo lee"
               " lo mismo para los dos consejeros")  # fmt: skip
    r = mc1.acto(corrida.c(f"/liquidaciones-importadas/{b}/confirmacion"), {})
    comprobar(r.estado == 409 and r.codigo() == "periodo_ya_confirmado",
              f"sin reemplazo, la segunda del período se niega: {r.texto}")  # fmt: skip
    motivo = f"SINTETICO {corrida.id}: la administración mandó otra versión (NO REAL)"
    r = mc1.acto(corrida.c(f"/liquidaciones-importadas/{b}/confirmacion"),
                 {"reemplaza_a": a, "motivo": motivo})  # fmt: skip
    comprobar(r.estado == 200 and (r.cuerpo["estado"], r.cuerpo["reemplaza_a"],
                                   r.cuerpo["motivo_reemplazo"]) == ("confirmada", a, motivo),
              f"reemplazar con motivo: {r.texto}")  # fmt: skip
    r = mc1.pedir("GET", corrida.c(f"/liquidaciones-importadas/{a}"))
    comprobar(r.cuerpo["estado"] == "reemplazada", f"la primera queda reemplazada: {r.texto}")
    comprobar(para_confirmar(corrida) == 0, "el inicio ya no cuenta ninguna por confirmar")
    corrida.ok("una oficial por período: la segunda sin reemplazo 409 periodo_ya_confirmado; con"
               " reemplazo y motivo, confirmada, y la primera queda reemplazada")  # fmt: skip
    return a, b


# 2. Lecturas del propietario y del Consejo ---------------------------------------------------


def recorrer_lecturas(corrida: Corrida, a: str, b: str, esperada: LecturaEsperada) -> None:
    prop, mc2 = corrida.n("propietario"), corrida.n("consejo2")
    r = prop.pedir("GET", corrida.c("/mi-cuenta"))
    comprobar(r.estado == 200, f"mi cuenta: {r.texto}")
    cuenta = r.cuerpo["liquidacion"]
    unidades = {u["designacion"]: u for u in cuenta["unidades"]}
    comprobar(
        cuenta["importacion_id"] == b and cuenta["periodo"] == PERIODO.isoformat()
        and set(unidades) == {"PB-A"} and unidades["PB-A"]["figura"] is True
        and Decimal(unidades["PB-A"]["total"]) == TOTAL_PB_A,
        f"Mis expensas: solo su unidad, de la oficial vigente: {r.texto}",
    )  # fmt: skip
    for otra in ("1-A", "1-B", "LAB Dos", "LAB Tres"):
        comprobar(otra not in r.texto, f"Mis expensas no menciona {otra!r}")
    r = prop.pedir("GET", corrida.c("/resumen"))
    comprobar(r.estado == 200 and r.cuerpo["liquidacion"]["importacion_id"] == b
              and r.cuerpo["liquidacion"]["vencimientos"], f"resumen: {r.texto}")  # fmt: skip
    for ruta in ("/panel", "/liquidacion-oficial"):
        r = prop.pedir("GET", corrida.c(ruta))
        comprobar(r.estado == 403, f"el propietario no ve {ruta}: {r.texto}")
    comprobar(denegado(corrida, corrida.personas["propietario"], "fiscalizacion.ver_panel",
                       "sin_concesion") is not None, "el 403 del panel quedó auditado")  # fmt: skip
    corrida.ok("Mis expensas: el propietario ve solo PB-A (1.301,00) de la vigente y su resumen;"
               " el panel y la liquidación oficial, 403 auditado")  # fmt: skip

    r = mc2.pedir("GET", corrida.c("/liquidacion-oficial"))
    comprobar(r.estado == 200, f"liquidaciones oficiales: {r.texto}")
    lista = {x["id"]: x for x in r.cuerpo}
    comprobar(b in lista and (lista[b].get("enlace_documentacion") or {}).get("estado")
              == esperada.enlace, f"la lista con la señal del perfil: {r.texto}")  # fmt: skip
    r = mc2.pedir("GET", corrida.c(f"/liquidacion-oficial/{b}"))
    enlace = r.cuerpo.get("enlace_documentacion") or {}
    comprobar(r.estado == 200 and r.cuerpo["vigente"] is True and r.cuerpo["reemplaza_a"] == a
              and (enlace.get("estado"), enlace.get("jurisdiccion"), enlace.get("version"))
              == (esperada.enlace, JURISDICCION, 1) and enlace.get("norma"),
              f"la oficial vigente con su señal: {r.texto[:400]}")  # fmt: skip
    r = mc2.pedir("GET", corrida.c("/panel"))
    comprobar(r.estado == 200, f"panel: {r.texto}")
    panel = r.cuerpo
    vigente = panel["liquidacion_vigente"]
    comprobar(
        (vigente["id"], vigente["periodo"], vigente["diferencias"], vigente["vencimientos"],
         vigente["enlace_documentacion"]["estado"])
        == (b, PERIODO.isoformat(), 0, VENCIMIENTOS, esperada.enlace),
        f"el panel con la vigente, sus vencimientos y la señal: {vigente}",
    )  # fmt: skip
    [gasto] = [g for g in panel["gasto_liquidado"] if g["periodo"] == PERIODO.isoformat()]
    rubros = {x["numero"]: Decimal(x["total"]) for x in panel["rubros_liquidados"]
              if x["periodo"] == PERIODO.isoformat()}  # fmt: skip
    comprobar(Decimal(gasto["total"]) == TOTAL_LIQUIDADO and rubros == RUBROS_LIQUIDADOS,
              f"el panel con las cifras oficiales: {gasto} {rubros}")  # fmt: skip
    corrida.ok(f"Consejo: la oficial vigente y el panel con 2.500,50, los dos rubros, los"
               f" vencimientos {VENCIMIENTOS} y la señal del perfil normativo"
               f" «{esperada.enlace}» (CABA, versión 1)")  # fmt: skip


def recorrer_presupuesto(corrida: Corrida) -> str:
    mc1, prop = corrida.n("consejo1"), corrida.n("propietario")
    cuerpo = {"desde": PERIODO.isoformat(), "hasta": "2026-12-01",
              "aprobacion": f"ACTA SINTETICA DE PRUEBA {corrida.id} — SIN VALIDEZ JURIDICA",
              "rubros": [{"numero": 1, "nombre": "SERVICIOS PUBLICOS",
                          "importe": str(PRESUPUESTO[1])},
                         {"numero": 2, "nombre": "MANTENIMIENTO",
                          "importe": str(PRESUPUESTO[2])}]}  # fmt: skip
    r = prop.acto(corrida.c("/presupuestos"), cuerpo)
    comprobar(r.estado == 403, f"control: el propietario no registra un presupuesto: {r.texto}")
    r = mc1.acto(corrida.c("/presupuestos"), cuerpo)
    comprobar(r.estado == 201, f"registrar el presupuesto: {r.texto}")
    presupuesto = r.cuerpo["id"]
    r = mc1.pedir("GET", corrida.c("/panel"))
    desvios = {x["numero"]: x for x in r.cuerpo["desvios_presupuesto"]
               if x["periodo"] == PERIODO.isoformat()}  # fmt: skip
    obtenidos = {
        n: (Decimal(x["presupuestado"]), Decimal(x["liquidado"]), Decimal(x["desvio"]),
            Decimal(x["porcentaje"]), x["presupuesto_id"])
        for n, x in desvios.items()
    }  # fmt: skip
    esperados = {n: (PRESUPUESTO[n], RUBROS_LIQUIDADOS[n], *DESVIOS[n], presupuesto)
                 for n in PRESUPUESTO}  # fmt: skip
    comprobar(obtenidos == esperados, f"desvíos exactos por rubro: {obtenidos} vs {esperados}")
    corrida.ok("presupuesto registrado por el Consejo (control: el propietario, 403); desvíos del"
               " panel exactos: +100,50 (7,18 %) y −100,00 (−9,09 %)")  # fmt: skip
    return presupuesto


# 3. Reclamo ----------------------------------------------------------------------------------


def recorrer_reclamo(corrida: Corrida, a: str, b: str) -> str:
    prop, mc2 = corrida.n("propietario"), corrida.n("consejo2")
    corrida.textos["reclamo"] = f"SINTETICO {corrida.id}: no entiendo el total de mi unidad"
    pb_a = str(corrida.unidades["PB-A"])
    r = prop.acto(corrida.c("/reclamos-liquidacion"),
                  {"liquidacion_id": a, "unidad_id": pb_a, "campo": "importe_unidad",
                   "texto": corrida.textos["reclamo"]})  # fmt: skip
    comprobar(r.estado == 409 and r.codigo() == "liquidacion_reemplazada",
              f"citar la reemplazada se niega: {r.texto}")  # fmt: skip
    r = prop.acto(corrida.c("/reclamos-liquidacion"),
                  {"liquidacion_id": b, "unidad_id": pb_a, "campo": "importe_unidad",
                   "texto": corrida.textos["reclamo"]})  # fmt: skip
    comprobar(r.estado == 201 and r.cuerpo["estado"] == "presentado"
              and r.cuerpo["acciones"] == ["retirar"], f"presentar: {r.texto}")  # fmt: skip
    reclamo = r.cuerpo["id"]
    r = mc2.pedir("GET", corrida.c(f"/reclamos-liquidacion/{reclamo}"))
    comprobar(r.estado == 200 and "responder" in r.cuerpo["acciones"]
              and r.cuerpo["texto"] == corrida.textos["reclamo"],
              f"el Consejo lo ve con el texto: {r.texto}")  # fmt: skip
    presentados = presentados_del_inicio(corrida)
    comprobar(presentados == 1, f"el inicio cuenta un reclamo que espera respuesta: {presentados}")
    corrida.textos["respuesta"] = f"SINTETICO {corrida.id}: es el gasto de luz y agua del período"
    r = mc2.acto(corrida.c(f"/reclamos-liquidacion/{reclamo}/respuesta"),
                 {"texto": corrida.textos["respuesta"]})  # fmt: skip
    comprobar(r.estado == 200 and r.cuerpo["estado"] == "respondido", f"responder: {r.texto}")
    r = prop.pedir("GET", corrida.c(f"/reclamos-liquidacion/{reclamo}"))
    comprobar(r.estado == 200 and r.cuerpo["estado"] == "respondido"
              and corrida.textos["respuesta"] in r.texto, f"el propietario ve la respuesta:"
              f" {r.texto}")  # fmt: skip
    comprobar(presentados_del_inicio(corrida) == 0, "respondido, el inicio ya no lo cuenta")
    corrida.ok("reclamo del propietario sobre la oficial (citar la reemplazada: 409"
               " liquidacion_reemplazada), contado en el inicio y respondido por el"
               " Consejo")  # fmt: skip
    return reclamo


def presentados_del_inicio(corrida: Corrida) -> int:
    r = corrida.n("consejo1").pedir("GET", corrida.c("/reclamos-liquidacion"))
    comprobar(r.estado == 200, f"listar reclamos: {r.texto}")
    return sum(1 for x in r.cuerpo["elementos"] if x["estado"] == "presentado")


# 4. Reparaciones -----------------------------------------------------------------------------


def reparacion(corrida: Corrida, rol: str, r_id: str) -> dict:
    r = corrida.n(rol).pedir("GET", corrida.c(f"/reparaciones/{r_id}"))
    comprobar(r.estado == 200, f"ver la reparación: {r.texto}")
    return r.cuerpo


def pedir_reparacion(corrida: Corrida, que: str) -> str:
    corrida.textos[que] = f"SINTETICO {corrida.id}: {que}, pierde agua la canilla de la cocina"
    r = corrida.n("propietario").acto(
        corrida.c("/reparaciones"),
        {"descripcion": corrida.textos[que], "unidad_id": str(corrida.unidades["PB-A"])},
    )
    comprobar(r.estado == 201 and r.cuerpo["estado"] == "pedida", f"pedir {que}: {r.texto}")
    return r.cuerpo["id"]


def adjunto_disponible(corrida: Corrida, r_id: str, tipo: str) -> str:
    mc1 = corrida.n("consejo1")
    r = mc1.pedir("POST", corrida.c(f"/reparaciones/{r_id}/adjuntos?tipo={tipo}"),
                  crudo=corrida.pdf(tipo.capitalize()), tipo="application/pdf", csrf=True,
                  clave=True)  # fmt: skip
    comprobar(r.estado == 201 and r.cuerpo["archivo"]["estado"] == "en_cuarentena",
              f"subir el {tipo}: {r.texto}")  # fmt: skip
    adjunto = r.cuerpo["adjunto_id"]
    limite = time.monotonic() + ESPERA_ANTIVIRUS_S
    estado = "en_cuarentena"
    while estado == "en_cuarentena" and time.monotonic() < limite:
        time.sleep(2)
        [a] = [x for x in reparacion(corrida, "consejo1", r_id)["adjuntos"] if x["id"] == adjunto]
        estado = a["estado"]
    comprobar(estado == "disponible", f"el antivirus deja disponible el {tipo}: {estado}")
    return adjunto


def hasta_terminada(corrida: Corrida, r_id: str, n: int) -> None:
    """Gestión, presupuesto, aprobación con la decisión del edificio y terminación con factura."""
    mc1 = corrida.n("consejo1")
    detalle = reparacion(corrida, "consejo1", r_id)
    comprobar("gestionar" in detalle["acciones"]
              and str(corrida.personas["consejo1"]) in {p["id"] for p in
                                                        detalle["responsables_posibles"]},
              f"el Consejo la puede gestionar: {detalle['acciones']}")  # fmt: skip
    pasos = [
        ("gestion", {"responsable_id": str(corrida.personas["consejo1"]),
                     "prioridad": "normal"}, "en_gestion"),
        ("presupuesto", lambda: {"adjunto_id": adjunto_disponible(corrida, r_id, "presupuesto"),
                                 "proveedor_nombre": "Proveedor SINTETICO (NO REAL)",
                                 "proveedor_cuit": cuit_sintetico(70_000_000 + 20 * n)},
         "presupuestada"),
        ("aprobacion", {"aprobado_por": "Asamblea SINTETICA (NO REAL)",
                        "decision_id": corrida.datos["decision"]}, "aprobada"),
        ("terminacion", lambda: {"adjunto_id": adjunto_disponible(corrida, r_id, "factura")},
         "terminada_gasto_pendiente"),
    ]  # fmt: skip
    for acto, cuerpo, estado in pasos:
        r = mc1.acto(corrida.c(f"/reparaciones/{r_id}/{acto}"),
                     cuerpo() if callable(cuerpo) else cuerpo)  # fmt: skip
        comprobar(r.estado == 200 and r.cuerpo["estado"] == estado, f"{acto}: {r.texto}")


def recorrer_reparaciones(corrida: Corrida, a: str, b: str) -> None:
    mc1 = corrida.n("consejo1")
    r1 = pedir_reparacion(corrida, "reparación 1")
    r2 = pedir_reparacion(corrida, "reparación 2")
    pedidas, en_curso = reparaciones_del_inicio(corrida)
    comprobar((pedidas, en_curso) == (2, 0), f"el inicio cuenta 2 pedidas: {pedidas}, {en_curso}")
    hasta_terminada(corrida, r1, 1)
    comprobar(reparaciones_del_inicio(corrida) == (1, 0), "terminada sin gasto no está en curso")
    enlazables = {(g["liquidacion_id"], g["clave"])
                  for g in reparacion(corrida, "consejo1", r1)["gastos_enlazables"]}  # fmt: skip
    comprobar((b, GASTO_REPARACION_1) in enlazables
              and all(liq == b for liq, _ in enlazables),
              f"solo los gastos de la oficial vigente son enlazables: {enlazables}")  # fmt: skip
    r = mc1.acto(corrida.c(f"/reparaciones/{r1}/gasto"),
                 {"liquidacion_id": a, "clave": GASTO_REPARACION_1})  # fmt: skip
    comprobar(r.estado == 422 and r.codigo() == "gasto_no_oficial",
              f"un gasto de la reemplazada no cierra: {r.texto}")  # fmt: skip
    r = mc1.acto(corrida.c(f"/reparaciones/{r1}/gasto"),
                 {"liquidacion_id": b, "clave": GASTO_REPARACION_1})  # fmt: skip
    comprobar(r.estado == 200 and r.cuerpo["estado"] == "cerrada"
              and r.cuerpo["historia"][-1]["gasto"]["clave"] == GASTO_REPARACION_1,
              f"el gasto de la oficial la cierra: {r.texto}")  # fmt: skip
    hasta_terminada(corrida, r2, 2)
    enlazables = {g["clave"] for g in reparacion(corrida, "consejo1", r2)["gastos_enlazables"]}
    comprobar(GASTO_REPARACION_1 not in enlazables and GASTO_REPARACION_2 in enlazables,
              f"el gasto enlazado ya no se ofrece: {enlazables}")  # fmt: skip
    r = mc1.acto(corrida.c(f"/reparaciones/{r2}/gasto"),
                 {"liquidacion_id": b, "clave": GASTO_REPARACION_1})  # fmt: skip
    comprobar(r.estado == 409 and r.codigo() == "gasto_ya_enlazado",
              f"PA11: un gasto cierra una sola reparación: {r.texto}")  # fmt: skip
    r = mc1.acto(corrida.c(f"/reparaciones/{r2}/gasto"),
                 {"liquidacion_id": b, "clave": GASTO_REPARACION_2})  # fmt: skip
    comprobar(r.estado == 200 and r.cuerpo["estado"] == "cerrada", f"cerrar la 2: {r.texto}")
    r = corrida.n("propietario").pedir("GET", corrida.c(f"/reparaciones/{r1}"))
    comprobar(r.estado == 200 and r.cuerpo["estado"] == "cerrada" and r.cuerpo["propia"] is True,
              f"quien la pidió la ve cerrada: {r.texto[:300]}")  # fmt: skip
    corrida.ok("dos reparaciones pedidas por el propietario y cerradas por el Consejo: presupuesto"
               " y factura analizados por el antivirus real, aprobación con la decisión, gasto"
               " de la reemplazada 422 gasto_no_oficial, el de la oficial cierra, y el mismo"
               " gasto en la otra 409 gasto_ya_enlazado (PA11)")  # fmt: skip
    corrida.datos["reparacion_pedida"] = pedir_reparacion(corrida, "reparación 3")


def reparaciones_del_inicio(corrida: Corrida) -> tuple[int, int]:
    r = corrida.n("consejo1").pedir("GET", corrida.c("/reparaciones"))
    comprobar(r.estado == 200, f"listar reparaciones: {r.texto}")
    estados = [x["estado"] for x in r.cuerpo["elementos"]]
    return estados.count("pedida"), sum(
        estados.count(e) for e in ("en_gestion", "presupuestada", "aprobada")
    )


# 5. Comunicado -------------------------------------------------------------------------------


def recorrer_comunicado(corrida: Corrida) -> None:
    mc1, prop = corrida.n("consejo1"), corrida.n("propietario")
    corrida.cuenta = cbu_sintetico(4_200_000_000 + int(corrida.id[:4], 16))
    r = mc1.acto(corrida.c("/comunicados"),
                 {"titulo": f"Pago de expensas LAB {corrida.id} SINTETICO",
                  "texto": f"Transferir a {corrida.cuenta} (SINTETICO, NO REAL)"})  # fmt: skip
    comprobar(r.estado == 422 and r.codigo() == "texto_con_cuenta",
              f"un comunicado con una cuenta se niega: {r.texto}")  # fmt: skip
    r = mc1.pedir("GET", corrida.c("/comunicados"))
    comprobar(r.estado == 200 and r.cuerpo["puede_emitir"] is True
              and r.cuerpo["elementos"] == [] and r.cuerpo["destinatarios_posibles"] == 3,
              f"lo negado no quedó y lo reciben los tres confirmados: {r.texto}")  # fmt: skip
    titulo = f"Corte de agua LAB {corrida.id} SINTETICO"
    corrida.textos["comunicado"] = f"SINTETICO {corrida.id}: el martes se corta el agua (NO REAL)"
    r = mc1.acto(corrida.c("/comunicados"),
                 {"titulo": titulo, "texto": corrida.textos["comunicado"]})  # fmt: skip
    comprobar(r.estado == 201 and r.cuerpo["envios"] == {"destinatarios": 3, "enviados": 3},
              f"emitir el comunicado: {r.texto}")  # fmt: skip
    comunicado = r.cuerpo["id"]
    r = prop.pedir("GET", corrida.c("/comunicados"))
    comprobar(r.estado == 200 and [x["id"] for x in r.cuerpo["elementos"]] == [comunicado]
              and r.cuerpo["puede_emitir"] is False, f"el propietario: {r.texto}")  # fmt: skip
    r = prop.pedir("GET", corrida.c(f"/comunicados/{comunicado}"))
    comprobar(r.estado == 200 and r.cuerpo["texto"] == corrida.textos["comunicado"]
              and r.cuerpo["envios"] is None, f"y lo lee, sin los envíos: {r.texto}")  # fmt: skip
    r = corrida.n("ayudante").pedir("GET", corrida.c("/comunicados"))
    comprobar(r.estado == 403, f"control: un vínculo sin confirmar no lo lee: {r.texto}")
    corrida.ok("comunicado con una cuenta: 422 texto_con_cuenta y no queda; emitido a 3"
               " confirmados (3 correos en la cola) y leído por el propietario; sin confirmar,"
               " 403")  # fmt: skip


# 6. Ayuda ------------------------------------------------------------------------------------


def recorrer_ayuda(corrida: Corrida, b: str) -> str:
    prop, ayu = corrida.n("propietario"), corrida.n("ayudante")
    r = prop.acto(corrida.c("/ayudas"), {"correo": corrida.correos["ayudante"],
                                         "alcances": ["consultar_expensas",
                                                      "preparar_reclamos"]})  # fmt: skip
    comprobar(r.estado == 201 and r.cuerpo["revocada_en"] is None, f"autorizar: {r.texto}")
    a = r.cuerpo["id"]
    r = ayu.pedir("GET", "/api/v1/me/ayudas")
    comprobar(r.estado == 200 and [(x["id"], x["consorcio_id"]) for x in r.cuerpo["elementos"]]
              == [(a, str(corrida.consorcio))], f"quien ayuda la ve: {r.texto}")  # fmt: skip
    r = ayu.pedir("GET", corrida.c("/mi-cuenta"))
    comprobar(r.estado in (403, 404), f"control: sin la ayuda no ve la cuenta: {r.texto}")
    r = ayu.pedir("GET", corrida.c("/mi-cuenta"), ayuda=a)
    comprobar(r.estado == 200 and [u["designacion"] for u in r.cuerpo["liquidacion"]["unidades"]]
              == ["PB-A"], f"ayudando, consulta la cuenta de la asistida: {r.texto}")  # fmt: skip
    corrida.textos["reclamo_ayuda"] = f"SINTETICO {corrida.id}: lo prepara quien ayuda (NO REAL)"
    r = ayu.acto(corrida.c("/reclamos-liquidacion"),
                 {"liquidacion_id": b, "unidad_id": str(corrida.unidades["PB-A"]),
                  "campo": "saldo_unidad", "texto": corrida.textos["reclamo_ayuda"]},
                 ayuda=a)  # fmt: skip
    comprobar(r.estado == 201 and r.cuerpo["estado"] == "presentado",
              f"ayudando, prepara y presenta un reclamo: {r.texto}")  # fmt: skip
    reclamo = r.cuerpo["id"]
    with corrida.tx() as conn:
        evento = conn.execute(
            text("select actor_persona_id, en_representacion_de, autorizacion_id"
                 " from auditoria.eventos where consorcio_id = :c and entidad_id = :r"
                 " and accion = 'consorcio.reclamo_liquidacion.presentar'"),
            {"c": corrida.consorcio, "r": reclamo},
        ).one()  # fmt: skip
    comprobar(tuple(evento) == (corrida.personas["ayudante"], corrida.personas["propietario"],
                                UUID(a)),
              f"auditoría: actuó quien ayuda, en representación: {evento}")  # fmt: skip
    r = prop.pedir("GET", corrida.c(f"/reclamos-liquidacion/{reclamo}"))
    comprobar(r.estado == 200, f"la asistida lo ve como suyo: {r.texto}")
    r = ayu.acto(corrida.c("/presupuestos"),
                 {"desde": "2027-01-01", "hasta": "2027-01-01",
                  "aprobacion": "ACTA SINTETICA — intento de quien ayuda",
                  "rubros": [{"numero": 1, "nombre": "X", "importe": "1.00"}]},
                 ayuda=a)  # fmt: skip
    comprobar(r.estado == 403 and r.codigo() == "sin_permiso",
              f"ayudando, registrar un presupuesto (decisorio) se niega: {r.texto}")  # fmt: skip
    negacion = denegado(corrida, corrida.personas["ayudante"], "presupuesto.registrar",
                        "ayuda_no_admitida")  # fmt: skip
    comprobar(negacion == {"en_representacion_de": corrida.personas["propietario"],
                           "autorizacion_id": UUID(a)},
              f"auditado con ayuda_no_admitida, en representación: {negacion}")  # fmt: skip
    r = prop.acto(corrida.c(f"/ayudas/{a}/revocacion"))
    comprobar(r.estado == 200 and r.cuerpo["revocada_en"] is not None, f"revocar: {r.texto}")
    r = ayu.pedir("GET", corrida.c("/mi-cuenta"), ayuda=a)
    comprobar(r.estado == 403 and r.codigo() == "ayuda_revocada",
              f"revocada, quien ayudaba recibe ayuda_revocada: {r.texto}")  # fmt: skip
    r = ayu.pedir("GET", "/api/v1/me/ayudas")
    comprobar(r.estado == 200 and r.cuerpo["elementos"] == [], f"y ya no la tiene: {r.texto}")
    corrida.ok("ayuda: el propietario autoriza; quien ayuda consulta Mis expensas y presenta un"
               " reclamo (auditado en representación); el presupuesto, 403 con"
               " ayuda_no_admitida en la auditoría; revocada, 403 ayuda_revocada")  # fmt: skip
    return a


# 7. Bandeja y aviso diario -------------------------------------------------------------------


def recorrer_bandeja(corrida: Corrida) -> None:
    from app.config import cargar
    from app.consorcios import avisos_consejo, avisos_pagos
    from app.db import transaccion
    from app.plataforma import correo

    exigir_entorno(corrida)
    contadas = {"importaciones": para_confirmar(corrida), "reclamos": presentados_del_inicio(
        corrida), "reparaciones": reparaciones_del_inicio(corrida)[0]}  # fmt: skip
    comprobar(contadas == {"importaciones": 0, "reclamos": 1, "reparaciones": 1},
              f"el inicio cuenta lo que dejaron los pasos: {contadas}")  # fmt: skip
    esperadas = {k: v for k, v in contadas.items() if v}
    config = cargar()
    comprobar(config.avisos_tope_diario > 0, "el aviso diario está encendido en staging"
              f" (CEM_AVISOS_TOPE_DIARIO = {config.avisos_tope_diario})")  # fmt: skip
    inicio = datetime.now().astimezone()
    with transaccion(corrida.motor, persona_id=None, consorcio_id=None) as conn:
        filas = [f for f in avisos_consejo.leer(conn) if f["consorcio_id"] == corrida.consorcio]
        por_persona = {f["persona_id"]: f["cuentas"] for f in filas}
        comprobar(por_persona == {corrida.personas["consejo1"]: esperadas,
                                  corrida.personas["consejo2"]: esperadas},
                  f"el aviso lee lo mismo que el inicio: {por_persona}")  # fmt: skip
        comprobar(all(f["correo"].endswith("@" + DOMINIO) for f in filas),
                  "el aviso va solo a cuentas sintéticas .invalid")  # fmt: skip
        avisos: dict = {}
        avisos_pagos.sumar_consejo(avisos, filas)
        margen = min(avisos_pagos.MARGEN, avisos_pagos.periodo(config.avisos_tope_diario) / 2)
        for persona, aviso in avisos.items():
            admite = conn.execute(
                text("select nucleo.registrar_aviso_pendientes(:p, :v, :t, :m)"),
                {"p": persona, "v": inicio, "t": config.avisos_tope_diario, "m": margen},
            ).scalar_one()  # fmt: skip
            comprobar(admite, "el tope admite el primer aviso de una persona nueva")
            correo.solicitar(conn, para=aviso.correo, asunto=avisos_pagos.ASUNTO,
                             texto=avisos_pagos.texto(aviso, avisos_pagos.enlace(config, aviso),
                                                      config.avisos_tope_diario))  # fmt: skip
        textos = conn.execute(
            text("select carga->>'para', carga->>'texto' from plataforma.outbox"
                 " where carga->>'asunto' = :a and carga->>'para' = any(:p)"),
            {"a": avisos_pagos.ASUNTO, "p": list(corrida.correos.values())},
        ).all()  # fmt: skip
    para = sorted(t[0] for t in textos)
    comprobar(para == sorted([corrida.correos["consejo1"], corrida.correos["consejo2"]]),
              f"un aviso encolado por consejero: {para}")  # fmt: skip
    prohibidos = [*corrida.textos.values(), corrida.cuenta, *corrida.nombres.values(),
                  *(nombre for _, nombre, _ in FILAS_UNIDAD.values()), "PB-A"]  # fmt: skip
    for _, cuerpo in textos:
        comprobar("un reclamo espera respuesta" in cuerpo
                  and "un pedido de reparación espera responsable" in cuerpo
                  and f"{corrida.origen_publico}/\n" in cuerpo,
                  f"el aviso dice cuántos y lleva al inicio: {cuerpo!r}")  # fmt: skip
        presentes = [p for p in prohibidos if p and p in cuerpo]
        comprobar(not presentes, f"el aviso no lleva textos, cuentas ni nombres: {presentes}")
        comprobar(not re.search(r"\d{7,}", cuerpo), "el aviso no lleva números largos")
    corrida.ok("bandeja: el inicio cuenta 1 reclamo y 1 pedido de reparación, igual que"
               " pendientes_consejo_para_avisar; el aviso diario (código real, solo este"
               " consorcio) encola uno por consejero, con cuántos y sin textos, cuenta ni"
               " nombres")  # fmt: skip


# 9. Cierre -----------------------------------------------------------------------------------


def comprobar_fin(corrida: Corrida) -> None:
    with corrida.tx() as conn:
        eventos = conn.execute(
            text("select coalesce(antes::text, '') || coalesce(despues::text, '')"
                 " from auditoria.eventos where consorcio_id = :c and creado_en >= :i"),
            {"c": corrida.consorcio, "i": corrida.inicio},
        ).scalars().all()  # fmt: skip
        destinos = conn.execute(
            text("select carga->>'para' from plataforma.outbox where consorcio_id = :c"
                 " and evento = 'correo.solicitado'"), {"c": corrida.consorcio},
        ).scalars().all()  # fmt: skip
        muertos = conn.execute(
            text("select count(*) from plataforma.jobs where consorcio_id = :c"
                 " and estado = 'muerto'"), {"c": corrida.consorcio},
        ).scalar_one()  # fmt: skip
    comprobar(len(eventos) > 0, "control: hay eventos de la corrida")
    comprobar(not [e for e in eventos if corrida.cuenta in e], "ningún evento lleva la cuenta")
    comprobar(len(destinos) > 0, "control: la corrida encoló correos del consorcio")
    afuera = [d for d in destinos if not d.endswith("@" + DOMINIO)]
    comprobar(not afuera, f"{len(afuera)} correos del consorcio fuera de .invalid")
    comprobar(muertos == 0, f"{muertos} trabajos muertos del consorcio")
    corrida.ok(f"{len(eventos)} eventos sin la cuenta; {len(destinos)} correos del consorcio,"
               " todos a .invalid (no salen); 0 trabajos muertos")  # fmt: skip


def cadena(corrida: Corrida) -> tuple[int, int, int, int]:
    """Total, enlaces rotos, hashes distintos y, de control, cuántos distintos da la misma cuenta
    con la acción alterada (tienen que ser todos). Con las dos columnas de `0127` al final."""
    formula = (
        "encode(sha256(convert_to(concat_ws('|', hash_previo, consorcio_id, id, secuencia,"
        " to_char(creado_en at time zone 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS.US'),"
        " actor_persona_id, origen, {accion}, entidad, entidad_id, antes, despues, motivo, ip,"
        " en_representacion_de, autorizacion_id),"
        " 'UTF8')), 'hex')"
    )
    with corrida.tx() as conn:
        return tuple(
            conn.execute(
                text(
                    "with e as (select *, lag(hash) over (order by secuencia) as anterior"
                    " from auditoria.eventos where consorcio_id = :c)"
                    " select count(*),"
                    " count(*) filter (where hash_previo <> coalesce(anterior, '')),"
                    f" count(*) filter (where hash <> {formula.format(accion='accion')}),"
                    f" count(*) filter (where hash <> {formula.format(accion="accion || 'x'")})"
                    " from e"
                ),
                {"c": corrida.consorcio},
            ).one()
        )


def cerrar(corrida: Corrida, conservar: str | None) -> tuple[int, list[str]]:
    """Da de baja los mandatos (salvo el de `conservar`, que cierra la exportación) y revoca las
    sesiones de todas las personas. Cuenta las sesiones abiertas persona por persona (la RLS de
    `nucleo.sesion` solo deja ver las propias) y qué navegadores siguen entrando. Repite la guarda:
    llamada sola, fuera de staging, se niega sin escribir."""
    from app.db import transaccion

    exigir_entorno(corrida)

    with corrida.tx() as conn:
        for rol, m in corrida.mandatos.items():
            if rol != conservar:
                conn.execute(
                    text("select nucleo.baja_mandato(:m, 'renuncia', null, :r, 'Fin del ensayo"
                         " de Modo Consejo', null, 'soporte')"), {"m": m, "r": REFERENCIA},
                )  # fmt: skip
        for persona in corrida.personas.values():
            conn.execute(text("select nucleo.revocar_sesiones_de(:p)"), {"p": persona})
    abiertas = 0
    for persona in corrida.personas.values():
        with transaccion(corrida.motor, persona_id=persona) as conn:
            abiertas += conn.execute(
                text("select count(*) from nucleo.sesion where persona_id = :p"
                     " and revocada_en is null and vence_en > now()"), {"p": persona},
            ).scalar_one()  # fmt: skip
    entran = [rol for rol, n in corrida.navegadores.items()
              if n.pedir("GET", "/api/v1/me/consorcios").estado != 401]  # fmt: skip
    return abiertas, entran


def recorrer(corrida: Corrida, esperada: LecturaEsperada) -> dict:
    exigir_entorno(corrida)
    preparar_escena(corrida)
    a, b = recorrer_importacion(corrida, esperada)
    recorrer_lecturas(corrida, a, b, esperada)
    recorrer_presupuesto(corrida)
    recorrer_reclamo(corrida, a, b)
    recorrer_reparaciones(corrida, a, b)
    recorrer_comunicado(corrida)
    recorrer_ayuda(corrida, b)
    recorrer_bandeja(corrida)
    comprobar_fin(corrida)
    total, rotos, distintos, control = cadena(corrida)
    comprobar(total > 0 and rotos == 0 and distintos == 0,
              f"cadena: {total} eventos, {rotos} rotos, {distintos} distintos")  # fmt: skip
    comprobar(control == total, f"control de la cadena: {control} de {total}")
    corrida.ok(f"cadena de auditoría: {total} eventos, 0 rotos, 0 distintos (control: {control}"
               f" de {total} detectados con la acción alterada)")  # fmt: skip
    return {
        "consorcio": str(corrida.consorcio),
        "hoy": corrida.hoy.isoformat(),
        "solicitante": str(corrida.personas["consejo1"]),
        "mandato": str(corrida.mandatos["consejo1"]),
        "propietario": str(corrida.personas["propietario"]),
        "cuenta": corrida.cuenta,
        "reclamo": corrida.textos["reclamo"],
    }


# 8. Exportación (en cem-trabajos-staging) ----------------------------------------------------

ALFABETO = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"


def _polimod(valores: list[int]) -> int:
    generador = (0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3)
    chk = 1
    for v in valores:
        b = chk >> 25
        chk = (chk & 0x1FFFFFF) << 5 ^ v
        for i in range(5):
            chk ^= generador[i] if (b >> i) & 1 else 0
    return chk


def bech32(hrp: str, datos: bytes) -> str:
    """Bech32 (BIP 173) sin el tope de 90 caracteres: el de `age` (como el ensayo de egresos)."""
    acumulado, bits, cinco = 0, 0, []
    for byte in datos:
        acumulado = (acumulado << 8) | byte
        bits += 8
        while bits >= 5:
            bits -= 5
            cinco.append((acumulado >> bits) & 31)
    if bits:
        cinco.append((acumulado << (5 - bits)) & 31)
    expandido = [ord(x) >> 5 for x in hrp] + [0] + [ord(x) & 31 for x in hrp]
    modulo = _polimod(expandido + cinco + [0] * 6) ^ 1
    control = [(modulo >> 5 * (5 - i)) & 31 for i in range(6)]
    return hrp + "1" + "".join(ALFABETO[d] for d in cinco + control)


def clave_age(directorio: Path) -> tuple[str, Path]:
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

    privada = X25519PrivateKey.generate()
    identidad = directorio / "identidad.txt"
    identidad.touch(mode=0o600)
    identidad.write_text(bech32("age-secret-key-", privada.private_bytes_raw()).upper() + "\n")
    return bech32("age", privada.public_key().public_bytes_raw()), identidad


def exigir_staging_exportacion(base: str) -> None:
    """La guarda de `--exportar`, antes de cualquier escritura y de armar ninguna limpieza: las dos
    bases (`CEM_DATABASE_URL` y la de exportación) en `--base` y el origen público de staging."""
    from scripts import exportar_consorcio

    for variable in ("CEM_DATABASE_URL", exportar_consorcio.VARIABLE_URL):
        if make_url(os.environ.get(variable, "")).host != base:
            raise Negado(f"{variable} no apunta a --base {base!r}: no es staging")
    exigir_staging(os.environ.get("CEM_ORIGEN_PUBLICO", ""), os.environ["CEM_DATABASE_URL"],
                   base, None)  # fmt: skip


def exportar(datos: dict, base: str, controles: list[str]) -> None:
    """Pide la exportación formal: negada al propietario sin mandato; armada para el Consejo; el
    paquete lleva las partes nuevas y ninguna cuenta. Cierra el mandato que dejó el ensayo."""
    from app.db import crear_motor
    from scripts import exportar_consorcio

    exigir_staging_exportacion(base)

    def pedido(solicitante: str, clave: str, salida: Path):
        return exportar_consorcio._argumentos([
            "armar", "--consorcio", datos["consorcio"],
            "--referencia-solicitud", "Nota SINTETICA del ensayo de Modo Consejo (NO REAL)",
            "--destinatario-nombre", "Destinatario SINTETICO (NO REAL)",
            "--destinatario-documento", "20000000001", "--solicitante", solicitante,
            "--fecha-pedido", datos["hoy"], "--referencia-respaldo", REFERENCIA,
            "--clave-publica", clave, "--salida", str(salida),
        ])  # fmt: skip

    with tempfile.TemporaryDirectory(prefix="cem-ensayo-modo-consejo-") as tmp:
        directorio = Path(tmp)
        clave, identidad = clave_age(directorio)
        motor = crear_motor(os.environ[exportar_consorcio.VARIABLE_URL])
        try:
            negada = directorio / "negada.zip.age"
            try:
                exportar_consorcio.armar(motor, pedido(datos["propietario"], clave, negada))
                raise Fallo("un propietario sin mandato exportó")
            except exportar_consorcio.Negado as e:
                comprobar("mandato vigente" in str(e), f"negada por la capacidad: {e}")
            comprobar(not negada.exists(), "la negada no deja archivo")
            salida = directorio / "paquete.zip.age"
            constancia = exportar_consorcio.armar(motor, pedido(datos["solicitante"], clave,
                                                                salida))  # fmt: skip
        finally:
            motor.dispose()
        comprobar(constancia["mandato"] == datos["mandato"], f"con su mandato: {constancia}")
        claro = subprocess.run(["age", "--decrypt", "--identity", str(identidad), str(salida)],
                               capture_output=True, check=True).stdout  # fmt: skip
    with zipfile.ZipFile(io.BytesIO(claro)) as z:
        nombres = set(z.namelist())
        textos = {n: z.read(n).decode() for n in nombres if n.endswith(".json")}
    filas = {e: len(json.loads(textos[f"{e}.json"])) if f"{e}.json" in textos else None
             for e in PARTES_NUEVAS}  # fmt: skip
    cortas = {e: n for e, n in filas.items() if n is None or n < PARTES_NUEVAS[e]}
    comprobar(not cortas, f"las partes nuevas viajan con sus filas: faltan {cortas}")
    comprobar(datos["reclamo"] in textos["reclamos_liquidacion/reclamo_liquidacion.json"],
              "control: el texto del reclamo viaja (los textos libres no se pierden)")  # fmt: skip
    con_cuenta = [n for n, t in textos.items() if datos["cuenta"] in t]
    comprobar(not con_cuenta, f"ningún JSON lleva la cuenta del ensayo: {con_cuenta}")
    largos = [n for n, t in textos.items() if n.split("/")[0] in {e.split("/")[0] for e in
              PARTES_NUEVAS} and re.search(r"(?<![0-9a-f-])[0-9]{22}(?![0-9])", t)]  # fmt: skip
    comprobar(not largos, f"ninguna parte nueva lleva un número de 22 cifras: {largos}")
    controles.append(f"exportación {constancia['exportacion']}: negada al propietario sin mandato"
                     f" (sin archivo); armada por el Consejo; {len(PARTES_NUEVAS)} entidades"
                     f" nuevas con filas ({filas}); el texto del reclamo viaja; ninguna"
                     " cuenta")  # fmt: skip


def cerrar_exportacion(datos: dict, base: str) -> None:
    """Da de baja el mandato que dejó la corrida y revoca sus sesiones. Repite la guarda: llamada
    sola, fuera de staging, se niega sin escribir (revisión de Codex en #286)."""
    from app.db import crear_motor, transaccion

    exigir_staging_exportacion(base)
    motor = crear_motor(os.environ["CEM_DATABASE_URL"])
    try:
        with transaccion(motor, persona_id=None, consorcio_id=datos["consorcio"]) as conn:
            conn.execute(
                text("select nucleo.baja_mandato(:m, 'renuncia', null, :r, 'Fin del ensayo de"
                     " Modo Consejo', null, 'soporte')"), {"m": datos["mandato"], "r": REFERENCIA},
            )  # fmt: skip
            conn.execute(text("select nucleo.revocar_sesiones_de(:p)"),
                         {"p": datos["solicitante"]})  # fmt: skip
    finally:
        motor.dispose()


def main_exportar(a: argparse.Namespace) -> int:
    datos = json.loads(base64.b64decode(a.datos))
    datos = datos.get("exportacion", datos)
    controles: list[str] = []
    # La guarda va antes del bloque que limpia: rechazado el entorno, no se escribe nada, ni la
    # baja del mandato ni la revocación de sesiones (revisión de Codex en #286).
    try:
        exigir_staging_exportacion(a.base)
    except Negado as e:
        print(f"negado: {e}", file=sys.stderr)
        return 3
    error = None
    try:
        exportar(datos, a.base, controles)
    except Fallo as e:
        error = e
        print(f"FALLÓ: {e}", file=sys.stderr, flush=True)
    finally:
        cerrar_exportacion(datos, a.base)
        controles.append("mandato de consejo1 dado de baja y sus sesiones revocadas")
        print(json.dumps({"controles": controles}, ensure_ascii=False, indent=2))
    return 1 if error else 0


# Entrada -------------------------------------------------------------------------------------


def main(argumentos: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--base", required=True, help="host de cem-postgres-staging")
    p.add_argument("--consorcio", type=UUID, help="uuid nuevo: el consorcio que crea la corrida")
    p.add_argument("--url", help="adónde mandar los pedidos HTTP")
    p.add_argument("--host", help="encabezado Host del frontend de staging")
    p.add_argument("--sin-verificar-certificado", action="store_true")
    p.add_argument("--simular", action="store_true",
                   help="solo la guarda y la lectura local del PDF: no escribe nada")  # fmt: skip
    p.add_argument("--exportar", action="store_true",
                   help="la exportación, en cem-trabajos-staging, con --datos")  # fmt: skip
    p.add_argument("--datos", help="con --exportar: base64 del resumen de la corrida")
    a = p.parse_args(argumentos)
    if a.exportar:
        if not a.datos:
            p.error("--exportar pide --datos")
        return main_exportar(a)
    if a.consorcio is None or a.url is None or a.host is None:
        p.error("la corrida pide --consorcio, --url y --host")
    from app.config import cargar
    from app.db import crear_motor

    config = cargar()
    try:
        exigir_staging(config.origen_publico, config.database_url, a.base, a.host)
        esperada = lectura_esperada(pdf_de(liquidacion_sintetica(), "Liquidacion LAB SINTETICA"))
    except Negado as e:
        print(f"negado: {e}", file=sys.stderr)
        return 3
    comprobar_esperada = (esperada.periodo == PERIODO and esperada.diferencias == 0
                          and esperada.campos > 0)  # fmt: skip
    if not comprobar_esperada:
        print(f"negado: la lectura local del PDF no es la esperada: {esperada}", file=sys.stderr)
        return 3
    if a.simular:
        print(json.dumps({"simulacion": True, "consorcio": str(a.consorcio),
                          "lectura_esperada": {"periodo": esperada.periodo.isoformat(),
                                               "campos": esperada.campos,
                                               "diferencias": esperada.diferencias,
                                               "enlace": esperada.enlace}},
                         ensure_ascii=False, indent=2))  # fmt: skip
        return 0
    contexto = None
    if a.sin_verificar_certificado:
        contexto = ssl.create_default_context()
        contexto.check_hostname = False
        contexto.verify_mode = ssl.CERT_NONE
    motor = crear_motor(config.database_url)
    corrida = Corrida(motor, a.consorcio, config.origen_publico, a.url.rstrip("/"), a.host,
                      contexto, a.base)  # fmt: skip
    try:
        with corrida.tx() as conn:
            existe = conn.execute(text("select count(*) from nucleo.consorcio where id = :c"),
                                  {"c": a.consorcio}).scalar_one()  # fmt: skip
            corrida.inicio = conn.execute(text("select clock_timestamp()")).scalar_one()
        if existe:
            print(f"negado: el consorcio {a.consorcio} ya existe: corre una sola vez",
                  file=sys.stderr)  # fmt: skip
            return 4
        print(f"corrida {corrida.id}; consorcio {a.consorcio}", flush=True)
        error = None
        exportacion = None
        try:
            exportacion = recorrer(corrida, esperada)
        except Negado as e:
            print(f"negado: {e}", file=sys.stderr)
            return 4
        except Fallo as e:
            error = e
            print(f"FALLÓ: {e}", file=sys.stderr, flush=True)
        finally:
            # Siempre, también ante una excepción inesperada: sesiones y mandatos. En verde, el
            # mandato de consejo1 queda para la exportación, que lo da de baja.
            abiertas, entran = cerrar(corrida, "consejo1" if exportacion else None)
            print(json.dumps({
                "corrida": corrida.id,
                "consorcio": str(a.consorcio),
                "personas": {k: str(v) for k, v in corrida.personas.items()},
                "mandatos": {k: str(v) for k, v in corrida.mandatos.items()},
                "sesiones_abiertas_al_cerrar": abiertas,
                "navegadores_que_siguen_entrando": entran,
                "controles": corrida.controles,
                "exportacion": exportacion,
            }, ensure_ascii=False, indent=2))  # fmt: skip
        if error is not None or abiertas != 0 or entran:
            if error is None:
                print(f"FALLÓ: quedaron {abiertas} sesiones abiertas; siguen entrando: {entran}",
                      file=sys.stderr)  # fmt: skip
            return 1
        return 0
    finally:
        motor.dispose()


if __name__ == "__main__":
    sys.exit(main())
