"""Lo que dejó el error provocado en los registros de `cem-backend-staging` (SINTETICO, NO REAL).

`ensayo_egresos_staging.py --circuito` provoca un 500 real y pide una ruta inventada, y devuelve en
su resumen (`error_provocado`) el id de correlación y los datos sintéticos que mandó. Este script
lee los registros de la entrada estándar y comprueba, en una corrida que **sí** tuvo un error:

1. que el filtro con que cada despliegue cuenta errores (`FILTRO`, el mismo `grep -E` de los
   informes de `doc/deploys/`) lo cuenta: hasta ahora daba 0 sin que nadie supiera si habría
   contado uno;
2. que la línea del error lleva su traza y la redacción: `[cuenta]`, `[correo]` y la clave tapada;
3. que ninguno de los datos sintéticos enviados quedó en claro en todo lo leído. La búsqueda se
   prueba antes con lo que sí tiene que estar: el id de correlación y la marca de la ruta.

No toca nada: corre afuera del contenedor, sobre un texto.

    ssh oci 'docker logs --since 30m <contenedor>' 2>&1 | python backend/pruebas/\\
ensayo_registros_staging.py --datos error_provocado.json
"""

import argparse
import json
import re
import sys
from pathlib import Path

FILTRO = re.compile(r'"nivel": *"(ERROR|CRITICAL)"|Traceback')


def comprobar(condicion: bool, que: str) -> None:
    if not condicion:
        sys.exit(f"FALLÓ: {que}")
    print(f"ok  {que}", flush=True)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--datos", type=Path, required=True, help="el `error_provocado` del resumen")
    a = p.parse_args()
    datos = json.loads(a.datos.read_text(encoding="utf-8"))
    texto = sys.stdin.buffer.read().decode(errors="replace")
    lineas = texto.splitlines()

    con_correlacion = [x for x in lineas if datos["id_correlacion"] in x]
    comprobar(len(lineas) > 0 and len(con_correlacion) > 0,
              f"control: la búsqueda encuentra el id de correlación ({len(con_correlacion)} de"
              f" {len(lineas)} líneas)")  # fmt: skip
    de_la_ruta = [x for x in lineas if datos["marca"] in x]
    comprobar(len(de_la_ruta) > 0, f"control: encuentra la ruta inventada ({len(de_la_ruta)})")

    contadas = [x for x in lineas if FILTRO.search(x)]
    propias = [x for x in contadas if datos["id_correlacion"] in x]
    comprobar(len(propias) > 0, f"el filtro de los despliegues cuenta el error provocado:"
              f" {len(contadas)} en total, {len(propias)} del provocado")  # fmt: skip
    ajenas = [x for x in lineas if x not in contadas and '"nivel": "INFO"' in x]
    comprobar(len(ajenas) > 0, f"control: no cuenta las líneas INFO ({len(ajenas)} sin contar)")

    # Un 500 deja dos líneas: la del manejador de la aplicación y la de uvicorn. El filtro cuenta
    # las dos, así que un error se ve como 2.
    errores = {e["modulo"]: e for e in map(json.loads, propias) if e["nivel"] == "ERROR"}
    comprobar(set(errores) == {"cem", "uvicorn.error"}, f"las dos líneas del 500: {set(errores)}")
    comprobar(errores["cem"]["mensaje"] == "error no controlado", "con el mensaje del manejador")
    for modulo, error in errores.items():
        traza = error["excepcion"]
        comprobar("ValueError" in traza and "[cuenta]" in traza and "[correo]" in traza
                  and "[redactado]" in traza,
                  f"{modulo}: la traza trae cuenta, correo y clave ya tapados")  # fmt: skip
    ruta = json.loads(next(x for x in de_la_ruta if x.startswith("{")))
    comprobar("[cuenta]" in ruta["ruta"] and "[correo]" in ruta["ruta"]
              and "[redactado]" in ruta["ruta"],
              f"la ruta queda tapada: {ruta['ruta']}")  # fmt: skip
    for nombre in ("cuenta", "correo", "secreto"):
        comprobar(datos[nombre] not in texto, f"{nombre}: 0 apariciones en claro en lo leído")


if __name__ == "__main__":
    main()
