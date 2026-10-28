"""Soporte abre, confirma y resuelve los casos de privacidad de RF37 (cortes 2d-1 y 2d-3).

Diseño: `doc/diseno/H2d_RF37_Diseno.md` del repositorio CEM, §4 y §7. Decisión del propietario
del 25-10-2026 (§1.5): lo tramita el equipo CEM; figurar en el padrón no es un atajo. Sin ruta:
corre dentro del contenedor de `cem-trabajos` con su rol propio, `cem_soporte_privacidad`
(`CEM_DATABASE_URL_SOPORTE_PRIVACIDAD`), que solo ejecuta las funciones de `0039` y `0040`.

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
- **suprimir** (2d-3, §6): muestra el plan (qué se borra, qué se anonimiza y qué se conserva, con
  el porqué) y su huella. `--ejecutar <huella>` lo aplica solo si el plan de ese momento tiene la
  misma huella que el que se revisó; cierra el caso y le manda al titular la resolución por el
  mismo correo.
- **rectificar** (2d-3, §2): el nombre nuevo se lee de la entrada estándar. Con alcance `cuenta`
  corrige el nombre de la persona; con un consorcio, el nombre declarado de sus vínculos allí.

No escribe eventos de auditoría (decisión del 26-10): la constancia queda en el caso.

    python -m scripts.privacidad abrir --correo vecina@ejemplo.com --derecho supresion \\
        --origen titular --referencia "Correo a soporte del 27-10" [--consorcio <uuid>]
    python -m scripts.privacidad abrir --descripcion "Mandatario externo, CUIT de la nota" ...
    python -m scripts.privacidad confirmar --caso <uuid> --como "Respuesta por correo del 28-10"
    python -m scripts.privacidad corroborar --caso <uuid> --texto "Nota de la administración"
    python -m scripts.privacidad rechazar --caso <uuid> --motivo "El titular no lo pidió"
    python -m scripts.privacidad ver --caso <uuid>
    python -m scripts.privacidad suprimir --caso <uuid> [--ejecutar <huella del plan revisado>]
    python -m scripts.privacidad rectificar --caso <uuid>
"""

import argparse
import base64
import hashlib
import json
import os
import secrets
import sys
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db import crear_motor, transaccion
from app.identidad.privacidad import hash_codigo
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

ASUNTO_RESOLUCION = "Resolvimos tu pedido de supresión en Consorcio en Mano"
TEXTO_RESOLUCION = """Hola:

Resolvimos tu pedido para suprimir tus datos ({alcance}). Esto es lo que hicimos:

{detalle}

Lo que se conserva es por obligación, por prueba o porque es del consorcio, como se explica en
cada punto. Si tenés dudas, respondé a este correo.

Equipo de Consorcio en Mano
"""
LARGO_HUELLA = 16
"""Caracteres hexadecimales de la huella del plan: detecta un cambio, no es un secreto."""
VERBO = {"borrar": "Borrado", "anonimizar": "Anonimizado", "conservar": "Conservado"}


class Negado(Exception):
    """El pedido no cumple lo que exige el script: no se cambió nada."""


def codigo_nuevo() -> str:
    """128 bits en base32, en grupos de cinco: se lee y se dicta sin confundir letras."""
    crudo = base64.b32encode(secrets.token_bytes(16)).decode("ascii").rstrip("=")
    return "-".join(crudo[i : i + 5] for i in range(0, len(crudo), 5))


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
    s = sub.add_parser("suprimir")
    s.add_argument("--caso", type=UUID, required=True)
    s.add_argument(
        "--ejecutar",
        dest="huella",
        metavar="HUELLA",
        help="aplica el plan que se mostró con esta huella",
    )
    n = sub.add_parser("rectificar")
    n.add_argument("--caso", type=UUID, required=True)
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


def _detalle(plan: list[dict]) -> str:
    """Una línea por punto del plan, sin datos del titular: qué, qué se hizo y por qué."""
    lineas = []
    for punto in plan:
        filas = "" if punto["filas"] is None else f" ({punto['filas']})"
        verbo = VERBO[punto["tratamiento"]]
        lineas.append(f"- {verbo}: {punto['que']}{filas}. {punto['motivo']}.")
    return "\n".join(lineas)


def huella_plan(plan: list[dict]) -> str:
    """Identifica el plan que vio soporte: `--ejecutar` la exige y la compara con el de ahora."""
    canonico = json.dumps(plan, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonico.encode("utf-8")).hexdigest()[:LARGO_HUELLA]


def _suprimir(conn, a: argparse.Namespace) -> dict:
    plan = conn.execute(
        text("select plataforma.privacidad_plan_supresion(:caso)"), {"caso": a.caso}
    ).scalar_one()
    huella = huella_plan(plan)
    if a.huella is None:
        return {"caso": str(a.caso), "huella": huella, "plan (sin aplicar)": "\n" + _detalle(plan)}
    if a.huella != huella:
        raise Negado(f"el plan cambió desde que se mostró (huella de ahora: {huella}): revisarlo"
                     " de nuevo antes de aplicarlo")  # fmt: skip
    alcance = conn.execute(
        text("select alcance_consorcio_id from plataforma.privacidad_ver(:caso)"), {"caso": a.caso}
    ).scalar_one()
    # El destino lo pone la base (el correo del titular, leído antes de anonimizarlo); asunto y
    # texto son de este script y no llevan datos del titular.
    texto_correo = TEXTO_RESOLUCION.format(
        alcance="toda tu cuenta" if alcance is None else "uno de tus consorcios",
        detalle=_detalle(plan))  # fmt: skip
    hecho = conn.execute(
        text("select plataforma.privacidad_ejecutar_supresion(:caso, cast(:plan as jsonb),"
             " :asunto, :texto)"),
        {"caso": a.caso, "plan": json.dumps(plan), "asunto": ASUNTO_RESOLUCION,
         "texto": texto_correo},
    ).scalar_one()  # fmt: skip
    return {"caso": str(a.caso), "estado": "resuelto", "aplicado": "\n" + _detalle(hecho)}


def _rectificar(conn, a: argparse.Namespace, entrada) -> dict:
    nombre = entrada.readline().strip()
    if not nombre:
        raise Negado("falta el nombre nuevo: se lee de la entrada estándar")
    hecho = conn.execute(
        text("select plataforma.privacidad_rectificar_nombre(:caso, :n)"),
        {"caso": a.caso, "n": nombre},
    ).scalar_one()
    return {"caso": str(a.caso), "estado": "resuelto", "rectificado": hecho["rectificado"],
            "filas": hecho["filas"]}  # fmt: skip


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
        if a.accion == "suprimir":
            return _suprimir(conn, a)
        if a.accion == "rectificar":
            return _rectificar(conn, a, entrada)
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
