"""Soporte exporta un consorcio a pedido de quien tiene la capacidad (2c-3, RF36).

Diseño: `doc/diseno/H2c_Diseno.md` del repositorio CEM, §1.1 y §5. No hay ruta de la API que
exporte: lo hace el equipo con este script, que corre dentro del contenedor de `cem-trabajos`
con su rol propio, `cem_soporte_exportacion` (`CEM_DATABASE_URL_SOPORTE_EXPORTACION`): sin
`BYPASSRLS`, sin `SELECT` directo sobre las tablas del consorcio, con el consorcio fijado en el
contexto (`0038`). Los binarios los lee con la clave de análisis de documentos
(`CEM_DOCUMENTOS_ANALISIS_S3_*` y `CEM_DOCUMENTOS_BUCKET`).

- **Exige** la referencia de la solicitud, el destinatario concreto (nombre y CUIT o documento),
  quién la pidió, el día del pedido, la referencia del respaldo (el acta o el contrato que
  sostiene el pedido) y la clave pública `age` del destinatario.
- **Comprueba la capacidad en la base:** `nucleo.registrar_exportacion` exige que quien la pidió
  tuviera ese día un mandato vigente de administración o de Consejo con las cinco facultades de
  lectura del contenido, cada una general, y recién entonces crea la constancia. Si falta
  cualquiera, alguna está acotada a un recurso o el mandato es de otro tipo (un administrador
  externo), se niega sin escribir nada. Las funciones de exportación solo responden para esa
  constancia, todavía sin armar.
- **Arma el paquete en tramos directo a `age`:** un ZIP con un JSON por entidad (de las funciones
  de exportación de `0038`), los binarios `disponible` y un `manifiesto.json` con el SHA-256 de
  cada archivo, cifrado para la clave pública del destinatario. Nunca hay una copia en claro en
  el disco.
- **No escribe eventos de auditoría** (decisión del propietario del 26-10-2026): la constancia es
  la fila de `plataforma.exportacion` (con la fecha de armado y, después, la de entrega) y lo que
  imprime el script.

    python -m scripts.exportar_consorcio armar --consorcio <uuid> \\
        --referencia-solicitud "Nota 3/2026" --destinatario-nombre "Estudio Nuevo SRL" \\
        --destinatario-documento 30712345671 --solicitante <persona> --fecha-pedido 2026-10-27 \\
        --referencia-respaldo "Acta 45, libro 3" --clave-publica age1... --salida /tmp/x.zip.age
    python -m scripts.exportar_consorcio entregar --consorcio <uuid> --exportacion <uuid> \\
        --paquete /tmp/x.zip.age
"""

import argparse
import contextlib
import hashlib
import json
import os
import subprocess
import sys
import zipfile
from datetime import date
from pathlib import Path
from uuid import UUID

from cem_contracts.documentos import TipoArchivo
from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError

from app.db import crear_motor, transaccion
from app.plataforma import almacen

VARIABLE_URL = "CEM_DATABASE_URL_SOPORTE_EXPORTACION"
ROL = "cem_soporte_exportacion"
FACULTADES = ("padron.ver", "documentos.ver", "auditoria.ver", "mandatos.ver", "decisiones.ver")
"""Las facultades de lectura del contenido, todas generales (H2c §5)."""
PARTES: dict[str, tuple[str, ...]] = {
    "padron": ("consorcio", "unidad", "vinculo"),
    "documentos": ("documento", "version"),
    "decisiones": ("decision",),
    "mandatos": ("mandato", "facultad", "revision"),
    "modos": ("modo",),
    "coeficientes": ("coeficiente",),
    "contactos": ("contacto",),
    "auditoria": ("evento",),
}
"""Cada parte es una función `nucleo.exportar_<parte>` y cada entidad, un archivo del paquete."""
EXTENSIONES = {TipoArchivo.PDF: "pdf", TipoArchivo.JPEG: "jpg", TipoArchivo.PNG: "png"}
TRAMO = 1024 * 1024
FILAS_POR_TRAMO = 500
MANIFIESTO = "manifiesto.json"


class Negado(Exception):
    """El pedido no cumple lo que exige el script: no se escribió nada."""


class ArchivoQueNoCoincide(RuntimeError):
    """Un binario del almacén no tiene el tamaño o el SHA-256 que guardó la base."""


def _argumentos(argumentos: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="accion", required=True)
    a = sub.add_parser("armar")
    a.add_argument("--consorcio", type=UUID, required=True)
    a.add_argument("--referencia-solicitud", required=True)
    a.add_argument("--destinatario-nombre", required=True)
    a.add_argument("--destinatario-documento", required=True, help="CUIT o documento")
    a.add_argument("--solicitante", type=UUID, required=True)
    a.add_argument("--fecha-pedido", type=date.fromisoformat, required=True)
    a.add_argument("--referencia-respaldo", required=True)
    a.add_argument("--clave-publica", required=True, help="la clave pública age del destinatario")
    a.add_argument("--salida", type=Path, required=True, help="el archivo cifrado (no existe)")
    e = sub.add_parser("entregar")
    e.add_argument("--consorcio", type=UUID, required=True)
    e.add_argument("--exportacion", type=UUID, required=True)
    e.add_argument("--paquete", type=Path, required=True, help="el archivo cifrado que se entrega")
    return p.parse_args(argumentos)


def ruta_binario(documento: str, version: str, tipo: str) -> str:
    return f"documentos/archivos/{documento}/{version}.{EXTENSIONES[TipoArchivo(tipo)]}"


def _exigir_texto(a: argparse.Namespace) -> None:
    faltan = [
        nombre
        for nombre in ("referencia_solicitud", "destinatario_nombre", "destinatario_documento",
                       "referencia_respaldo", "clave_publica")
        if not getattr(a, nombre).strip()
    ]  # fmt: skip
    if faltan:
        raise Negado("faltan " + ", ".join(f.replace("_", " ") for f in faltan))


def _exigir_clave_age(clave: str) -> None:
    hecho = subprocess.run(["age", "--recipient", clave], input=b"", capture_output=True)
    if hecho.returncode != 0:
        raise Negado(f"la clave pública no la acepta age: {hecho.stderr.decode().strip()}")


def _rol(conn: Connection) -> None:
    rol = conn.execute(text("select current_user")).scalar_one()
    if rol != ROL:
        raise Negado(f"{VARIABLE_URL} conecta como {rol}, no como {ROL}")


class _Paquete:
    """El ZIP que se escribe en la entrada de `age`, con la suma de cada archivo para el
    manifiesto."""

    def __init__(self, destino) -> None:
        self.zip = zipfile.ZipFile(destino, "w", compression=zipfile.ZIP_DEFLATED)
        self.archivos: list[dict] = []

    def escribir(self, ruta: str, tramos) -> tuple[str, int]:
        suma = hashlib.sha256()
        tamano = 0
        with self.zip.open(ruta, "w") as entrada:
            for tramo in tramos:
                entrada.write(tramo)
                suma.update(tramo)
                tamano += len(tramo)
        self.archivos.append({"ruta": ruta, "sha256": suma.hexdigest(), "tamano": tamano})
        return suma.hexdigest(), tamano


def _json_en_tramos(filas, cantidad: list[int]):
    """Un arreglo JSON, fila por fila, sin armarlo entero en memoria."""
    yield b"["
    for i, fila in enumerate(filas):
        yield (b",\n" if i else b"\n") + json.dumps(
            fila, ensure_ascii=False, sort_keys=True
        ).encode()
        cantidad[0] += 1
    yield b"\n]\n"


def _parte(
    conn: Connection, paquete: _Paquete, consorcio: UUID, pedido: UUID, parte: str
) -> dict[str, int]:
    """Escribe un archivo por entidad de la parte, en el orden que las devuelve la función."""
    resultado = conn.execute(
        text(f"select entidad, fila from nucleo.exportar_{parte}(:c, :e)"),
        {"c": consorcio, "e": pedido},
        execution_options={"yield_per": FILAS_POR_TRAMO},
    )
    filas = iter(resultado)
    siguiente = next(filas, None)
    cantidades: dict[str, int] = {}
    for entidad in PARTES[parte]:

        def de_la_entidad(entidad=entidad):
            nonlocal siguiente
            while siguiente is not None and siguiente.entidad == entidad:
                fila = dict(siguiente.fila)
                if entidad == "version":
                    fila["archivo"] = ruta_binario(fila["documento_id"], fila["id"], fila["tipo"])
                yield fila
                siguiente = next(filas, None)

        cantidad = [0]
        paquete.escribir(f"{parte}/{entidad}.json", _json_en_tramos(de_la_entidad(), cantidad))
        cantidades[entidad] = cantidad[0]
    if siguiente is not None:
        raise RuntimeError(f"nucleo.exportar_{parte} devolvió una entidad fuera de orden o"
                           f" desconocida: {siguiente.entidad}")  # fmt: skip
    resultado.close()
    return cantidades


def _binarios(
    conn: Connection, paquete: _Paquete, consorcio: UUID, pedido: UUID, versiones: int
) -> int:
    objetos = conn.execute(
        text("select * from nucleo.exportar_objetos_documentos(:c, :e)"),
        {"c": consorcio, "e": pedido},
    ).mappings().all()  # fmt: skip
    if len(objetos) != versiones:
        raise RuntimeError(f"{len(objetos)} objetos para {versiones} versiones disponibles")
    if not objetos:
        return 0
    origen = almacen.AlmacenDocumentos.desde_entorno(almacen.DOCUMENTOS_ANALISIS)
    s3 = origen.cliente()
    for o in objetos:
        lugar = {"Bucket": origen.bucket, "Key": o["clave_objeto"]}
        if o["version_objeto"]:
            lugar["VersionId"] = o["version_objeto"]
        cuerpo = s3.get_object(**lugar)["Body"]
        ruta = ruta_binario(str(o["documento_id"]), str(o["version_id"]), o["tipo"])
        suma, tamano = paquete.escribir(ruta, cuerpo.iter_chunks(TRAMO))
        if (suma, tamano) != (o["sha256"], o["tamano"]):
            raise ArchivoQueNoCoincide(f"la versión {o['version_id']} del almacén no coincide"
                                       " con la base")  # fmt: skip
    return len(objetos)


def _sha256(ruta: Path) -> tuple[str, int]:
    suma = hashlib.sha256()
    with ruta.open("rb") as f:
        while tramo := f.read(TRAMO):
            suma.update(tramo)
    return suma.hexdigest(), ruta.stat().st_size


class _Tubo:
    """La entrada de `age` para el ZIP. Si el armado se corta, `cortar()` descarta lo que el ZIP
    todavía quiera escribir al cerrarse (su índice final): ese paquete no se entrega y `age` se
    detiene sin terminarlo."""

    def __init__(self, entrada) -> None:
        self.entrada = entrada
        self.cortado = False

    def write(self, datos: bytes) -> int:
        if not self.cortado:
            self.entrada.write(datos)
        return len(datos)

    def flush(self) -> None:
        if not self.cortado:
            self.entrada.flush()

    def cortar(self) -> None:
        self.cortado = True


def _cifrar(conn: Connection, a: argparse.Namespace, fila, salida, cantidades: dict) -> None:
    """Escribe el ZIP en la entrada de `age`, que cifra hacia `salida`."""
    age = subprocess.Popen(
        ["age", "--recipient", a.clave_publica.strip()],
        stdin=subprocess.PIPE, stdout=salida, stderr=subprocess.PIPE,
    )  # fmt: skip
    tubo = _Tubo(age.stdin)
    paquete = _Paquete(tubo)
    try:
        for parte in PARTES:
            cantidades |= _parte(conn, paquete, a.consorcio, fila.id, parte)
        cantidades["archivos"] = _binarios(
            conn, paquete, a.consorcio, fila.id, cantidades["version"]
        )
        manifiesto = {
            "exportacion": str(fila.id),
            "consorcio": str(a.consorcio),
            "solicitud": a.referencia_solicitud.strip(),
            "destinatario": a.destinatario_nombre.strip(),
            "creada_en": fila.creada_en.isoformat(),
            "archivos": paquete.archivos,
        }
        texto = json.dumps(manifiesto, ensure_ascii=False, indent=1).encode()
        paquete.escribir(MANIFIESTO, [texto])
        paquete.zip.close()
        age.stdin.close()
    except BaseException:
        tubo.cortar()
        paquete.zip.close()
        age.kill()
        # `age` ya no lee: cerrar su entrada puede dar `BrokenPipeError` al vaciar el búfer, y eso
        # no tiene que tapar el error que cortó el armado.
        with contextlib.suppress(BrokenPipeError):
            age.stdin.close()
        age.stderr.close()
        age.wait()
        raise
    error = age.stderr.read()
    age.wait()
    if age.returncode != 0:
        raise RuntimeError(f"age terminó con {age.returncode}: {error.decode()}")


def _registrar(conn: Connection, a: argparse.Namespace):
    """La constancia, por `nucleo.registrar_exportacion`, que comprueba la capacidad."""
    try:
        return conn.execute(
            text("select * from nucleo.registrar_exportacion(:c, :sol, :dn, :dd, :p, :fp, :r)"),
            {"c": a.consorcio, "sol": a.referencia_solicitud, "dn": a.destinatario_nombre,
             "dd": a.destinatario_documento, "p": a.solicitante, "fp": a.fecha_pedido,
             "r": a.referencia_respaldo},
        ).one()  # fmt: skip
    except DBAPIError as e:
        if e.orig.diag.message_hint == "sin_capacidad":
            raise Negado(
                "quien la pidió no tenía ese día un mandato vigente de administración o de"
                " Consejo con " + ", ".join(FACULTADES) + " generales"
            ) from e
        raise


def armar(motor, a: argparse.Namespace) -> dict:
    """Todo en una transacción de lectura repetible: el paquete sale de una sola foto de la base.
    El archivo se crea con `xb` (nunca pisa uno ajeno, tampoco si aparece en el medio) y se da
    por bueno recién después de confirmar la transacción: si algo falla, incluido el commit, se
    borra el archivo que creó esta corrida y no queda la fila."""
    _exigir_texto(a)
    _exigir_clave_age(a.clave_publica.strip())
    try:
        salida = a.salida.open("xb")
    except FileExistsError as e:
        raise Negado(f"{a.salida} ya existe: no se sobrescribe") from e
    try:
        repetible = motor.execution_options(isolation_level="REPEATABLE READ")
        with transaccion(repetible, persona_id=None, consorcio_id=a.consorcio) as conn:
            _rol(conn)
            fila = _registrar(conn, a)
            cantidades: dict[str, int] = {}
            with salida:
                _cifrar(conn, a, fila, salida, cantidades)
            suma, tamano = _sha256(a.salida)
            armada = conn.execute(
                text(
                    "update plataforma.exportacion set armada_en = clock_timestamp(),"
                    " paquete_sha256 = :s, paquete_tamano = :t, cantidades = cast(:q as jsonb)"
                    " where id = :id returning armada_en"
                ),
                {"s": suma, "t": tamano, "q": json.dumps(cantidades), "id": fila.id},
            ).scalar_one()
    except BaseException:
        # El archivo es de esta corrida (lo creó `xb`): cifrado e incompleto o sin constancia
        # (la transacción volvió atrás). No se entrega.
        salida.close()
        a.salida.unlink()
        raise
    return {
        "exportacion": str(fila.id),
        "consorcio": str(a.consorcio),
        "solicitud": a.referencia_solicitud.strip(),
        "destinatario": f"{a.destinatario_nombre.strip()} ({a.destinatario_documento.strip()})",
        "solicitante": str(a.solicitante),
        "mandato": str(fila.mandato_id),
        "fecha_pedido": a.fecha_pedido.isoformat(),
        "respaldo": a.referencia_respaldo.strip(),
        "armada_en": armada.isoformat(),
        "paquete": str(a.salida),
        "sha256": suma,
        "tamano": tamano,
        "cantidades": json.dumps(cantidades, sort_keys=True),
    }


def entregar(motor, a: argparse.Namespace) -> dict:
    """Registra la entrega del archivo que se entrega: su SHA-256 tiene que ser el de la
    constancia."""
    suma, _ = _sha256(a.paquete)
    with transaccion(motor, persona_id=None, consorcio_id=a.consorcio) as conn:
        _rol(conn)
        registrada = conn.execute(
            text(
                "select paquete_sha256 from plataforma.exportacion"
                " where id = :e and armada_en is not null for update"
            ),
            {"e": a.exportacion},
        ).scalar_one_or_none()
        if registrada is None:
            raise Negado("la exportación no existe en este consorcio o no está armada")
        if registrada != suma:
            raise Negado(f"{a.paquete} no es el paquete de esta exportación (otro SHA-256)")
        entregada = conn.execute(
            text("update plataforma.exportacion set entregada_en = now() where id = :e"
                 " returning entregada_en"),
            {"e": a.exportacion},
        ).scalar_one()  # fmt: skip
    return {
        "exportacion": str(a.exportacion),
        "consorcio": str(a.consorcio),
        "sha256": suma,
        "entregada_en": entregada.isoformat(),
    }


def main(argumentos: list[str] | None = None) -> int:
    a = _argumentos(argumentos)
    url = os.environ.get(VARIABLE_URL, "")
    if not url:
        print(f"falta {VARIABLE_URL}", file=sys.stderr)
        return 2
    motor = crear_motor(url)
    try:
        constancia = (armar if a.accion == "armar" else entregar)(motor, a)
    except Negado as e:
        print(f"no se escribió nada: {e}", file=sys.stderr)
        return 1
    except DBAPIError as e:
        print(f"no se escribió nada: {e.orig.diag.message_hint or e.orig}", file=sys.stderr)
        return 1
    finally:
        motor.dispose()
    for clave, valor in constancia.items():
        print(f"{clave}: {valor}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
