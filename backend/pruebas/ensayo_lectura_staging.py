"""Medir la lectura de comprobantes con el modelo real, en staging (SINTETICO, NO REAL).

Dos modos, los dos en un proceso de corta vida dentro de `cem-trabajos-staging`, leído de la entrada
estándar: no cambian ninguna variable del contenedor, no escriben en la base y no encolan nada.

**`--medir`.** Pasa por `lectura_comprobantes.leer`, la misma función del trabajo, cada comprobante
de `--datos` (los arma `comprobantes_sinteticos.py`) y compara lo leído con lo que dice el archivo:
aciertos por campo y, de los errores, si el modelo inventó, omitió o leyó otra cosa. Un campo que no
figura acierta solo si vuelve nulo. `titular` y `numero` se comparan con las normalizaciones que usa
la ficha. La capa `llm` no devuelve el uso: acá se anota el `usage` de cada respuesta real del
proveedor, envolviendo el método del SDK sin cambiar lo que devuelve. Si no hubo `usage` (un
proveedor con el otro protocolo), lo dice y no inventa.

**`--clave-invalida`.** Primero el control, con la clave del contenedor: el comprobante se lee.
Después el mismo comprobante con una clave inventada, puesta solo en `os.environ` de este proceso:
tiene que volver el 401 del proveedor, la lectura `falla_del_modelo` sin campos, y el registro con
el tipo de la falla, sin la clave. La clave verdadera nunca se lee ni se imprime acá.

    ssh oci 'docker exec -i <cem-trabajos-staging> sh -c "cat > /tmp/comprobantes.json"' < x.json
    ssh oci 'docker exec -i -w /app -e PYTHONPATH=/app <cem-trabajos-staging> python - --medir \\
        --base <host de cem-postgres-staging> --datos /tmp/comprobantes.json' \\
        < backend/pruebas/ensayo_lectura_staging.py
"""

import argparse
import base64
import io
import json
import logging
import os
import secrets
import sys
import time
from collections import Counter
from dataclasses import asdict
from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy.engine import make_url

from app import llm, registros
from app.consorcios import lectura_comprobantes as lectura

CAMPOS = ("importe", "moneda", "cuenta", "titular", "numero", "fecha")
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
    a = p.parse_args()
    exigir_staging(a.base)
    datos = json.loads(a.datos.read_text(encoding="utf-8"))
    if a.medir:
        print(json.dumps({"resumen": medir(datos)}, ensure_ascii=False))
    else:
        clave_invalida(datos)


if __name__ == "__main__":
    main()
