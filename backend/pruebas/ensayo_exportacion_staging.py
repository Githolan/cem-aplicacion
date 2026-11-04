"""Exportación formal del recorrido completo del ensayo de egresos (CEM LAB: SINTETICO, NO REAL).

Mandato del propietario del 03-11-2026: el E2E sintético termina con la exportación formal y los
comprobantes que le corresponden. La exportación es un script de soporte
(`scripts.exportar_consorcio`, `0038`) que corre en `cem-trabajos` con su rol propio; la credencial
vive solo ahí, así que este paso va aparte del ensayo, que corre en `cem-backend`. Toma lo que
imprimió
`ensayo_egresos_staging.py --completo` (la clave `exportacion` de su resumen) y:

1. arma una clave `age` de un solo uso (X25519 en bech32, como `age-keygen`, que la imagen no
   trae), en un directorio temporal que borra al terminar;
2. pide la exportación con el propietario sin mandato como solicitante: se niega (`Negado`);
3. la pide con el Consejo que dejó vigente el ensayo: se arma y queda la constancia;
4. descifra el paquete con la clave y comprueba: cada comprobante del recorrido está en
   `documentos/documento.json` con sus versiones y su archivo, el control también, el manifiesto
   coincide con cada archivo, lo económico del recorrido viaja (`0069`: proveedores, órdenes,
   pagos y liquidaciones, con los últimos dígitos de cada cuenta) y ningún JSON lleva una cuenta
   del ensayo;
5. da de baja el mandato que quedó vigente y revoca sus sesiones.

Solo staging: exige que `CEM_DATABASE_URL` y `CEM_DATABASE_URL_SOPORTE_EXPORTACION` apunten al
host de `--base` (el de `cem-postgres-staging`). Nunca en producción.

    ssh oci 'docker exec -i -w /app -e PYTHONPATH=/app <cem-trabajos-staging> python - \\
        --base <host de cem-postgres-staging> --datos /tmp/exportacion.json' \\
        < backend/pruebas/ensayo_exportacion_staging.py
"""

import argparse
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.db import crear_motor, transaccion
from scripts import exportar_consorcio

REFERENCIA = "ACTA SINTETICA DE PRUEBA — SIN VALIDEZ JURIDICA — exportación del ensayo"
ECONOMICOS = ("proveedores/proveedor", "proveedores/proveedor_version", "ordenes_pago/orden",
              "ordenes_pago/orden_version", "ordenes_pago/orden_aprobacion",
              "ordenes_pago/orden_transicion", "pagos/ejecucion", "pagos/comprobante", "pagos/pago",
              "pagos/conciliacion", "liquidaciones/liquidacion", "liquidaciones/gasto")  # fmt: skip
ALFABETO = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"


class Fallo(Exception):
    """Un paso no dio lo esperado."""


def comprobar(condicion: bool, que: str) -> None:
    if not condicion:
        raise Fallo(que)


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
    """Bech32 (BIP 173, no bech32m), sin el tope de 90 caracteres: el de `age`."""
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
    privada = X25519PrivateKey.generate()
    identidad = directorio / "identidad.txt"
    identidad.touch(mode=0o600)
    identidad.write_text(bech32("age-secret-key-", privada.private_bytes_raw()).upper() + "\n")
    return bech32("age", privada.public_key().public_bytes_raw()), identidad


def exigir_staging(base: str) -> None:
    for variable in ("CEM_DATABASE_URL", exportar_consorcio.VARIABLE_URL):
        host = make_url(os.environ.get(variable, "")).host or ""
        comprobar(base in host, f"{variable} no apunta a {base}: no es staging")


def pedido(datos: dict, solicitante: str, clave: str, salida: Path):
    return exportar_consorcio._argumentos([
        "armar", "--consorcio", datos["consorcio"],
        "--referencia-solicitud", "Nota SINTETICA del ensayo (NO REAL)",
        "--destinatario-nombre", "Destinatario SINTETICO (NO REAL)",
        "--destinatario-documento", "20000000001", "--solicitante", solicitante,
        "--fecha-pedido", datos["hoy"], "--referencia-respaldo", REFERENCIA,
        "--clave-publica", clave, "--salida", str(salida),
    ])  # fmt: skip


def hoy_del_consorcio(consorcio: str) -> str:
    """El día del pedido es el del consorcio: el contenedor está en UTC y de noche ya es mañana."""
    motor = crear_motor(os.environ["CEM_DATABASE_URL"])
    try:
        with transaccion(motor, persona_id=None, consorcio_id=consorcio) as conn:
            return conn.execute(text("select nucleo.hoy_del_consorcio(:c)"),
                                {"c": consorcio}).scalar_one().isoformat()  # fmt: skip
    finally:
        motor.dispose()


def recorrer(datos: dict, controles: list[str]) -> None:
    datos["hoy"] = hoy_del_consorcio(datos["consorcio"])
    with tempfile.TemporaryDirectory(prefix="cem-ensayo-exportacion-") as tmp:
        directorio = Path(tmp)
        clave, identidad = clave_age(directorio)
        controles.append("clave age de un solo uso")
        motor = crear_motor(os.environ[exportar_consorcio.VARIABLE_URL])
        try:
            negada = directorio / "negada.zip.age"
            try:
                exportar_consorcio.armar(motor, pedido(datos, datos["propietario"], clave, negada))
                raise Fallo("un propietario sin mandato exportó")
            except exportar_consorcio.Negado as e:
                comprobar("mandato vigente" in str(e), f"negada por la capacidad: {e}")
            comprobar(not negada.exists(), "la negada no deja archivo")
            controles.append("un propietario sin mandato no exporta: no queda archivo")
            salida = directorio / "paquete.zip.age"
            constancia = exportar_consorcio.armar(
                motor, pedido(datos, datos["solicitante"], clave, salida)
            )
        finally:
            motor.dispose()
        comprobar(constancia["mandato"] == datos["mandato"], f"con su mandato: {constancia}")
        controles.append(f"exportación {constancia['exportacion']} armada por el Consejo")
        claro = subprocess.run(["age", "--decrypt", "--identity", str(identidad), str(salida)],
                               capture_output=True, check=True).stdout  # fmt: skip
        with zipfile.ZipFile(io.BytesIO(claro)) as z:
            manifiesto = json.loads(z.read(exportar_consorcio.MANIFIESTO))
            archivos = manifiesto["archivos"]
            for a in archivos:
                comprobar(hashlib.sha256(z.read(a["ruta"])).hexdigest() == a["sha256"],
                          f"el manifiesto coincide con {a['ruta']}")  # fmt: skip
            documentos = {d["id"]: d for d in json.loads(z.read("documentos/documento.json"))}
            versiones = json.loads(z.read("documentos/version.json"))
            textos = [z.read(n).decode() for n in z.namelist() if n.endswith(".json")]
            nombres = set(z.namelist())
            economicos = {e: json.loads(z.read(f"{e}.json")) for e in ECONOMICOS}
        vacios = [e for e, filas in economicos.items() if not filas]
        comprobar(not vacios, f"lo económico del recorrido viaja: vacíos {vacios}")
        digitos = {v["cuenta_ultimos_digitos"] for v in economicos["proveedores/proveedor_version"]}
        comprobar(bool(digitos & {c[-4:] for c in datos["cuentas"]}),
                  "las cuentas del ensayo van por sus últimos dígitos")  # fmt: skip
        esperados = [*datos["comprobantes"], datos["control"]]
        faltan = [d for d in esperados if d not in documentos]
        comprobar(not faltan, f"comprobantes y control en el paquete: faltan {faltan}")
        for d in esperados:
            propias = [v for v in versiones if v["documento_id"] == d]
            comprobar(propias and all(v["archivo"] in nombres for v in propias),
                      f"versiones y archivo de {d}")  # fmt: skip
        con_cuenta = [t[:80] for t in textos if any(c in t for c in datos["cuentas"])]
        comprobar(not con_cuenta, f"{len(con_cuenta)} archivos JSON llevan una cuenta")
        controles.append(f"{len(datos['comprobantes'])} comprobantes y el control, con sus"
                         f" versiones y archivos; manifiesto íntegro ({len(archivos)} archivos);"
                         f" {len(ECONOMICOS)} entidades económicas con filas;"
                         " ninguna cuenta en los JSON, sí sus últimos dígitos")  # fmt: skip


def cerrar(datos: dict) -> None:
    motor = crear_motor(os.environ["CEM_DATABASE_URL"])
    try:
        with transaccion(motor, persona_id=None, consorcio_id=datos["consorcio"]) as conn:
            conn.execute(
                text("select nucleo.baja_mandato(:m, 'renuncia', null, :r, 'Fin del ensayo de"
                     " exportación', null, 'soporte')"), {"m": datos["mandato"], "r": REFERENCIA},
            )  # fmt: skip
            conn.execute(text("select nucleo.revocar_sesiones_de(:p)"),
                         {"p": datos["solicitante"]})  # fmt: skip
    finally:
        motor.dispose()


def main(argumentos: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--base", required=True, help="host de cem-postgres-staging")
    p.add_argument("--datos", type=Path, required=True,
                   help="JSON con la clave `exportacion` del resumen del ensayo")  # fmt: skip
    a = p.parse_args(argumentos)
    datos = json.loads(a.datos.read_text())
    datos = datos.get("exportacion", datos)
    controles: list[str] = []
    try:
        exigir_staging(a.base)
    except Fallo as e:
        print(f"negado: {e}", file=sys.stderr)
        return 3
    error = None
    try:
        recorrer(datos, controles)
    except Fallo as e:
        error = e
        print(f"FALLÓ: {e}", file=sys.stderr, flush=True)
    finally:
        if datos.get("mandato"):
            cerrar(datos)
            controles.append("mandato del Consejo dado de baja y sus sesiones revocadas")
        print(json.dumps({"controles": controles}, ensure_ascii=False, indent=2))
    return 1 if error else 0


if __name__ == "__main__":
    sys.exit(main())
