"""Soporte abre y confirma los casos de privacidad de RF37 (corte 2d-1).

Diseño: `doc/diseno/H2d_RF37_Diseno.md` del repositorio CEM, §4 y §7. Decisión del propietario
del 25-10-2026 (§1.5): lo tramita el equipo CEM; figurar en el padrón no es un atajo. Sin ruta:
corre dentro del contenedor de `cem-trabajos` con su rol propio, `cem_soporte_privacidad`
(`CEM_DATABASE_URL_SOPORTE_PRIVACIDAD`), que solo ejecuta las funciones de `0039`.

- **abrir:** registra el caso (titular por su correo, o una descripción si no tiene correo) y,
  si el titular tiene correo, le manda el código de confirmación. El destino lo decide la base.
  El código no se imprime ni se escribe en ningún registro: va solo en el correo.
- **confirmar:** el titular devolvió el código. Se lee de la entrada estándar (no queda en el
  historial de la terminal). La base lo compara y se niega si la persona tiene contraseña o TOTP
  (su confirmación va por la aplicación, §9.1) o si su correo no es confiable y falta la
  corroboración.
- **corroborar:** anota la corroboración de la administración o del Consejo (§4.2, punto 4).
- **rechazar:** cierra el caso con su motivo.
- **ver:** el estado del caso, sin el código.

No escribe eventos de auditoría (decisión del 26-10): la constancia queda en el caso.

    python -m scripts.privacidad abrir --correo vecina@ejemplo.com --derecho supresion \\
        --origen titular --referencia "Correo a soporte del 27-10" [--consorcio <uuid>]
    python -m scripts.privacidad abrir --descripcion "Mandatario externo, CUIT de la nota" ...
    python -m scripts.privacidad confirmar --caso <uuid> --como "Respuesta por correo del 28-10"
    python -m scripts.privacidad corroborar --caso <uuid> --texto "Nota de la administración"
    python -m scripts.privacidad rechazar --caso <uuid> --motivo "El titular no lo pidió"
    python -m scripts.privacidad ver --caso <uuid>
"""

import argparse
import base64
import hashlib
import os
import re
import secrets
import sys
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db import crear_motor, transaccion
from app.plataforma import correo

VARIABLE_URL = "CEM_DATABASE_URL_SOPORTE_PRIVACIDAD"
ROL = "cem_soporte_privacidad"
DERECHOS = ("acceso", "rectificacion_nombre", "supresion")
ORIGENES = ("titular", "administracion")
NOMBRE_DERECHO = {
    "acceso": "una copia de tus datos",
    "rectificacion_nombre": "corregir tu nombre",
    "supresion": "suprimir tus datos",
}
NOMBRE_ORIGEN = {"titular": "vos", "administracion": "la administración de tu consorcio"}

ASUNTO = "Tu pedido sobre tus datos en Consorcio en Mano"
TEXTO = """Hola:

Recibimos un pedido para {derecho} ({alcance}). Lo hizo {origen}.

Para confirmar que el pedido es tuyo, respondé a este correo con este código:

{codigo}

El código vence en 72 horas y sirve una sola vez.

Si no hiciste este pedido, no hagas nada: sin el código no se hace ningún cambio.

Equipo de Consorcio en Mano
"""


class Negado(Exception):
    """El pedido no cumple lo que exige el script: no se cambió nada."""


def codigo_nuevo() -> str:
    """128 bits en base32, en grupos de cinco: se lee y se dicta sin confundir letras."""
    crudo = base64.b32encode(secrets.token_bytes(16)).decode("ascii").rstrip("=")
    return "-".join(crudo[i : i + 5] for i in range(0, len(crudo), 5))


def hash_codigo(codigo: str) -> bytes:
    """El código se compara sin guiones, espacios ni mayúsculas: como lo escriba la persona."""
    normal = re.sub(r"[^A-Z2-7]", "", codigo.upper())
    return hashlib.sha256(normal.encode("ascii")).digest()


def _argumentos(argumentos: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="accion", required=True)
    a = sub.add_parser("abrir")
    titular = a.add_mutually_exclusive_group(required=True)
    titular.add_argument("--correo")
    titular.add_argument("--descripcion")
    a.add_argument("--consorcio", type=UUID)
    a.add_argument("--derecho", choices=DERECHOS, required=True)
    a.add_argument("--origen", choices=ORIGENES, required=True)
    a.add_argument("--referencia", required=True)
    c = sub.add_parser("confirmar")
    c.add_argument("--caso", type=UUID, required=True)
    c.add_argument("--como", required=True)
    o = sub.add_parser("corroborar")
    o.add_argument("--caso", type=UUID, required=True)
    o.add_argument("--texto", required=True)
    r = sub.add_parser("rechazar")
    r.add_argument("--caso", type=UUID, required=True)
    r.add_argument("--motivo", required=True)
    v = sub.add_parser("ver")
    v.add_argument("--caso", type=UUID, required=True)
    return p.parse_args(argumentos)


def _abrir(conn, a: argparse.Namespace) -> dict:
    caso, destino = conn.execute(
        text("select caso, destino from plataforma.privacidad_abrir(:c, :k, :d, :der, :o, :r)"),
        {"c": a.correo, "k": a.consorcio, "d": a.descripcion, "der": a.derecho, "o": a.origen,
         "r": a.referencia},
    ).one()  # fmt: skip
    constancia = {"caso": str(caso), "codigo": "no: caso manual, sin correo"}
    if destino is not None:
        codigo = codigo_nuevo()
        alcance = "toda tu cuenta" if a.consorcio is None else "uno de tus consorcios"
        carga = correo.validar({
            "para": destino,
            "asunto": ASUNTO,
            "texto": TEXTO.format(derecho=NOMBRE_DERECHO[a.derecho], alcance=alcance,
                                  origen=NOMBRE_ORIGEN[a.origen], codigo=codigo),
        })  # fmt: skip
        conn.execute(
            text("select plataforma.privacidad_enviar_codigo(:caso, :h, :asunto, :texto)"),
            {"caso": caso, "h": hash_codigo(codigo), "asunto": carga.asunto, "texto": carga.texto},
        )
        del codigo
        constancia["codigo"] = "enviado al correo del titular (no se muestra)"
    return constancia


def _confirmar(conn, a: argparse.Namespace, entrada) -> dict:
    codigo = entrada.readline().strip()
    if not codigo:
        raise Negado("falta el código: se lee de la entrada estándar")
    conn.execute(
        text("select plataforma.privacidad_confirmar(:caso, :h, :como)"),
        {"caso": a.caso, "h": hash_codigo(codigo), "como": a.como},
    )
    return {"caso": str(a.caso), "estado": "confirmado"}


def _ver(conn, a: argparse.Namespace) -> dict:
    fila = (
        conn.execute(text("select * from plataforma.privacidad_ver(:caso)"), {"caso": a.caso})
        .mappings()
        .one_or_none()
    )
    if fila is None:
        raise Negado("el caso no existe")
    return {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in fila.items()}


def ejecutar(motor, a: argparse.Namespace, entrada=sys.stdin) -> dict:
    """Todo en una transacción: si algo se niega, no se cambia nada."""
    with transaccion(motor, persona_id=None) as conn:
        rol = conn.execute(text("select current_user")).scalar_one()
        if rol != ROL:
            raise Negado(f"{VARIABLE_URL} conecta como {rol}, no como {ROL}")
        if a.accion == "abrir":
            return _abrir(conn, a)
        if a.accion == "confirmar":
            return _confirmar(conn, a, entrada)
        if a.accion == "corroborar":
            conn.execute(
                text("select plataforma.privacidad_corroborar(:caso, :t)"),
                {"caso": a.caso, "t": a.texto},
            )
            return {"caso": str(a.caso), "corroborado": "sí"}
        if a.accion == "rechazar":
            conn.execute(
                text("select plataforma.privacidad_rechazar(:caso, :m)"),
                {"caso": a.caso, "m": a.motivo},
            )
            return {"caso": str(a.caso), "estado": "rechazado"}
        return _ver(conn, a)


def main(argumentos: list[str] | None = None, entrada=sys.stdin) -> int:
    a = _argumentos(argumentos)
    url = os.environ.get(VARIABLE_URL, "")
    if not url:
        print(f"falta {VARIABLE_URL}", file=sys.stderr)
        return 2
    motor = crear_motor(url)
    try:
        constancia = ejecutar(motor, a, entrada)
    except Negado as e:
        print(f"no se cambió nada: {e}", file=sys.stderr)
        return 1
    except DBAPIError as e:
        print(f"no se cambió nada: {e.orig.diag.message_hint or e.orig}", file=sys.stderr)
        return 1
    finally:
        motor.dispose()
    for clave, valor in constancia.items():
        print(f"{clave}: {valor}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
