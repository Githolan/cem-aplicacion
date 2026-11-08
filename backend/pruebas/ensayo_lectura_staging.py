"""Medir la lectura de comprobantes y facturas con el modelo real, en staging (SINTETICO, NO REAL).

Dos modos, los dos en un proceso de corta vida dentro de `cem-trabajos-staging`, leído de la entrada
estándar: no cambian ninguna variable del contenedor, no escriben en la base y no encolan nada.

**`--medir`.** Pasa por `lectura_comprobantes.leer`, la misma función del trabajo, cada comprobante
de `--datos` (los arma `comprobantes_sinteticos.py`) y compara lo leído con lo que dice el archivo:
aciertos por campo y, de los errores, si el modelo inventó, omitió o leyó otra cosa. Un campo que no
figura acierta solo si vuelve nulo. `titular` y `numero` se comparan con las normalizaciones que usa
la ficha. La capa `llm` no devuelve el uso: acá se anota el `usage` de cada respuesta real del
proveedor, envolviendo el método del SDK sin cambiar lo que devuelve. Si no hubo `usage` (un
proveedor con el otro protocolo), lo dice y no inventa.

**`--medir --tipo factura`.** Lo mismo con `lectura_carga.leer`, la función del trabajo
`documentos.leer_carga` (`0088`), sobre las facturas de `facturas_sinteticas.py`. Cada campo se
compara por su lado: CUIT exacto, razón social y número con las normalizaciones de la ficha,
teléfono por sus dígitos, correo sin mayúsculas, domicilio sin acentos ni signos, importe y fecha
exactos. El concepto es un resumen que el modelo escribe: acierta si vuelve cuando figura y nulo
cuando no, sin comparar el texto. Además cuenta los CUIT marcados inválidos y, de una factura con
un CBU impreso (`esperado.cuenta_impresa`), si esos dígitos volvieron en algún campo: tiene que ser
nunca.

**`--clave-invalida`.** Primero el control, con la clave del contenedor: el comprobante se lee.
Después el mismo comprobante con una clave inventada, puesta solo en `os.environ` de este proceso:
tiene que volver el 401 del proveedor, la lectura `falla_del_modelo` sin campos, y el registro con
el tipo de la falla, sin la clave. La clave verdadera nunca se lee ni se imprime acá.

    ssh oci 'docker exec -i <cem-trabajos-staging> sh -c "cat > /tmp/comprobantes.json"' < x.json
    ssh oci 'docker exec -i -w /app -e PYTHONPATH=/app <cem-trabajos-staging> python - --medir \\
        --base <host de cem-postgres-staging> --datos /tmp/comprobantes.json' \\
        < backend/pruebas/ensayo_lectura_staging.py

Con facturas, el mismo comando con `--tipo factura` y el archivo de `facturas_sinteticas.py`.
"""

import argparse
import base64
import io
import json
import logging
import os
import re
import secrets
import sys
import time
import unicodedata
from collections import Counter
from dataclasses import asdict
from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy.engine import make_url

from app import llm, registros
from app.consorcios import lectura_carga
from app.consorcios import lectura_comprobantes as lectura

CAMPOS = ("importe", "moneda", "cuenta", "titular", "numero", "fecha")
CAMPOS_FACTURA = ("cuit", "razon_social", "domicilio", "telefono", "correo", "numero_comprobante",
                  "fecha", "importe", "moneda", "concepto")  # fmt: skip
TIPOS = ("comprobante", "factura")
VARIABLE_CLAVE = "CEM_LLM_API_KEY"
HTTP_NO_AUTORIZADO = 401


def comprobar(condicion: bool, que: str) -> None:
    if not condicion:
        sys.exit(f"FALLÓ: {que}")
    print(f"ok  {que}", flush=True)


def exigir_staging(base: str) -> None:
    host = make_url(os.environ.get("CEM_DATABASE_URL", "")).host or ""
    if base not in host:
        sys.exit(f"negado: CEM_DATABASE_URL no apunta a {base}: no es staging")


class Usos:
    """El `usage` de cada respuesta real, sin tocar lo que el SDK devuelve."""

    def __init__(self) -> None:
        self.ultimo: dict | None = None
        from anthropic.resources.messages import Messages

        original = Messages.create

        def anotar(sdk, *args, **kwargs):
            respuesta = original(sdk, *args, **kwargs)
            self.ultimo = {
                "entrada": respuesta.usage.input_tokens,
                "salida": respuesta.usage.output_tokens,
                "fin": respuesta.stop_reason,
            }
            return respuesta

        Messages.create = anotar

    def tomar(self) -> dict | None:
        uso, self.ultimo = self.ultimo, None
        return uso


def igual(campo: str, leido, esperado) -> bool:
    if leido is None or esperado is None:
        return leido is None and esperado is None
    match campo:
        case "importe":
            return leido == Decimal(esperado)
        case "fecha":
            return leido == date.fromisoformat(esperado)
        case "titular":
            return lectura.titular_normalizado(leido) == lectura.titular_normalizado(esperado)
        case "numero":
            return lectura.numero_normalizado(leido) == lectura.numero_normalizado(esperado)
        case _:
            return leido == esperado


def _sin_signos(texto: str) -> str:
    """En mayúsculas, sin acentos y con un espacio entre palabras."""
    sin_acentos = "".join(
        letra for letra in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(letra)
    )
    return " ".join(re.sub(r"[^A-Z0-9]", " ", sin_acentos.upper()).split())


def igual_factura(campo: str, leido, esperado) -> bool:
    if leido is None or esperado is None:
        return leido is None and esperado is None
    match campo:
        case "importe":
            return leido == Decimal(esperado)
        case "fecha":
            return leido == date.fromisoformat(esperado)
        case "razon_social":
            return lectura.titular_normalizado(leido) == lectura.titular_normalizado(esperado)
        case "numero_comprobante":
            return lectura.numero_normalizado(leido) == lectura.numero_normalizado(esperado)
        case "telefono":
            return re.sub(r"[^0-9]", "", leido) == re.sub(r"[^0-9]", "", esperado)
        case "correo":
            return leido.lower() == esperado.lower()
        case "domicilio":
            return _sin_signos(leido) == _sin_signos(esperado)
        case "concepto":
            return True  # un resumen: que vuelva cuando figura ya se comprobó arriba
        case _:
            return leido == esperado


def clase_de_error(leido, esperado) -> str:
    if esperado is None:
        return "invento"
    return "omitio" if leido is None else "distinto"


def visible(campo: str, valor) -> str | None:
    """Para el informe: de la cuenta, solo los últimos dígitos."""
    if valor is None:
        return None
    return f"…{str(valor)[-4:]}" if campo == "cuenta" else str(valor)


def medir(datos: dict) -> dict:
    usos = Usos()
    por_campo = {c: Counter() for c in CAMPOS}
    legibles = Counter()
    filas = []
    for comprobante in datos["comprobantes"]:
        contenido = base64.b64decode(comprobante["archivo"])
        esperado = comprobante["esperado"]
        inicio = time.monotonic()
        leida = lectura.leer(contenido, comprobante["tipo"])
        segundos = round(time.monotonic() - inicio, 1)
        uso = usos.tomar()
        legibles["acierto" if leida.legible == esperado["legible"] else "error"] += 1
        fallos = {}
        if esperado["legible"]:
            valores = asdict(leida)
            for campo in CAMPOS:
                valor = valores[campo]
                if igual(campo, valor, esperado[campo]):
                    por_campo[campo]["acierto"] += 1
                else:
                    clase = clase_de_error(valor, esperado[campo])
                    por_campo[campo][clase] += 1
                    fallos[campo] = {"clase": clase, "leido": visible(campo, valor),
                                     "esperado": visible(campo, esperado[campo])}  # fmt: skip
        fila = {"nombre": comprobante["nombre"], "rasgo": comprobante["rasgo"],
                "tipo": comprobante["tipo"], "bytes": len(contenido), "segundos": segundos,
                "legible": leida.legible, "esperado_legible": esperado["legible"],
                "error": leida.error, "modelo": leida.modelo, "uso": uso,
                "fallos": fallos}  # fmt: skip
        filas.append(fila)
        print(json.dumps(fila, ensure_ascii=False), flush=True)
    con_uso = [f["uso"] for f in filas if f["uso"] is not None]
    return {
        "comprobantes": len(filas),
        "por_campo": {c: dict(v) for c, v in por_campo.items()},
        "legible": dict(legibles),
        "modelos": sorted({f["modelo"] for f in filas if f["modelo"]}),
        "con_uso": len(con_uso),
        "fichas_entrada": sum(u["entrada"] for u in con_uso),
        "fichas_salida": sum(u["salida"] for u in con_uso),
        "fin": dict(Counter(u["fin"] for u in con_uso)),
        "segundos": sum(f["segundos"] for f in filas),
    }


def devolvio_la_cuenta(valores: dict, cuenta: str) -> bool:
    """Si los dígitos de la cuenta impresa están en algún texto leído, con o sin separadores."""
    return any(
        cuenta in re.sub(r"[^0-9]", "", str(valores[c]))
        for c in CAMPOS_FACTURA if valores[c] is not None
    )  # fmt: skip


def medir_facturas(datos: dict) -> dict:
    usos = Usos()
    por_campo = {c: Counter() for c in CAMPOS_FACTURA}
    legibles = Counter()
    cuit_invalidos = 0
    cuentas_devueltas = []
    filas = []
    for factura in datos["comprobantes"]:
        contenido = base64.b64decode(factura["archivo"])
        esperado = factura["esperado"]
        inicio = time.monotonic()
        leida = lectura_carga.leer(contenido, factura["tipo"])
        segundos = round(time.monotonic() - inicio, 1)
        uso = usos.tomar()
        legibles["acierto" if leida.legible == esperado["legible"] else "error"] += 1
        cuit_invalidos += leida.cuit_invalido
        valores = asdict(leida)
        cuenta = esperado.get("cuenta_impresa")
        if cuenta is not None and devolvio_la_cuenta(valores, cuenta):
            cuentas_devueltas.append(factura["nombre"])
        fallos = {}
        if esperado["legible"]:
            for campo in CAMPOS_FACTURA:
                valor = valores[campo]
                if igual_factura(campo, valor, esperado[campo]):
                    por_campo[campo]["acierto"] += 1
                else:
                    clase = clase_de_error(valor, esperado[campo])
                    por_campo[campo][clase] += 1
                    fallos[campo] = {"clase": clase, "leido": None if valor is None else str(valor),
                                     "esperado": esperado[campo]}  # fmt: skip
        fila = {"nombre": factura["nombre"], "rasgo": factura["rasgo"], "tipo": factura["tipo"],
                "bytes": len(contenido), "segundos": segundos, "legible": leida.legible,
                "esperado_legible": esperado["legible"], "error": leida.error,
                "cuit_invalido": leida.cuit_invalido, "modelo": leida.modelo, "uso": uso,
                "fallos": fallos}  # fmt: skip
        filas.append(fila)
        print(json.dumps(fila, ensure_ascii=False), flush=True)
    con_uso = [f["uso"] for f in filas if f["uso"] is not None]
    return {
        "facturas": len(filas),
        "prompt": lectura_carga.VERSION_PROMPT,
        "por_campo": {c: dict(v) for c, v in por_campo.items()},
        "legible": dict(legibles),
        "cuit_invalidos": cuit_invalidos,
        "cuentas_devueltas": cuentas_devueltas,
        "modelos": sorted({f["modelo"] for f in filas if f["modelo"]}),
        "con_uso": len(con_uso),
        "fichas_entrada": sum(u["entrada"] for u in con_uso),
        "fichas_salida": sum(u["salida"] for u in con_uso),
        "fin": dict(Counter(u["fin"] for u in con_uso)),
        "segundos": sum(f["segundos"] for f in filas),
    }


def clave_invalida(datos: dict) -> None:
    comprobante = next(c for c in datos["comprobantes"] if c["esperado"]["legible"])
    contenido = base64.b64decode(comprobante["archivo"])
    salida = io.StringIO()
    manejador = logging.StreamHandler(salida)
    manejador.setFormatter(registros.FormateadorJson())
    logging.getLogger(lectura.log.name).addHandler(manejador)

    control = lectura.leer(contenido, comprobante["tipo"])
    comprobar(control.legible and control.error is None and control.importe is not None,
              f"control, con la clave del contenedor: lee {comprobante['nombre']}")  # fmt: skip

    inventada = f"clave-invalida-{secrets.token_hex(16)}"
    os.environ[VARIABLE_CLAVE] = inventada
    fallida = lectura.leer(contenido, comprobante["tipo"])
    comprobar((fallida.modelo, fallida.legible, fallida.error, fallida.importe, fallida.cuenta)
              == (None, False, "falla_del_modelo", None, None),
              "con la clave inválida: falla_del_modelo, sin ningún campo")  # fmt: skip
    registrado = salida.getvalue()
    comprobar("AuthenticationError" in registrado,
              "el registro dice el tipo de la falla (AuthenticationError)")  # fmt: skip
    comprobar(inventada not in registrado, "el registro no lleva la clave")
    try:
        llm.complete([{"role": "user", "content": "SINTETICO: decí listo"}],
                     llm.Opciones(max_tokens=16, espera_s=lectura.ESPERA_S))  # fmt: skip
    except llm.ErrorDeModelo as e:
        estado = e.__cause__.status_code
        comprobar(estado == HTTP_NO_AUTORIZADO, f"el proveedor responde {estado}")
        comprobar(inventada not in str(e), "el error de la capa no lleva la clave")
    else:
        sys.exit("FALLÓ: con la clave inválida el proveedor respondió: el control no sirve")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--base", required=True, help="host de cem-postgres-staging")
    p.add_argument("--datos", type=Path, required=True)
    modo = p.add_mutually_exclusive_group(required=True)
    modo.add_argument("--medir", action="store_true")
    modo.add_argument("--clave-invalida", action="store_true")
    p.add_argument("--tipo", choices=TIPOS, default="comprobante",
                   help="qué se mide con --medir (por omisión, comprobantes)")  # fmt: skip
    a = p.parse_args()
    if a.clave_invalida and a.tipo != "comprobante":
        p.error("--clave-invalida prueba la capa con un comprobante: sin --tipo")
    exigir_staging(a.base)
    datos = json.loads(a.datos.read_text(encoding="utf-8"))
    if datos.get("tipo", "comprobante") != a.tipo:
        sys.exit(f"negado: --datos es de {datos.get('tipo', 'comprobante')}, no de {a.tipo}")
    if a.medir and a.tipo == "factura":
        print(json.dumps({"resumen": medir_facturas(datos)}, ensure_ascii=False))
    elif a.medir:
        print(json.dumps({"resumen": medir(datos)}, ensure_ascii=False))
    else:
        clave_invalida(datos)


if __name__ == "__main__":
    main()
