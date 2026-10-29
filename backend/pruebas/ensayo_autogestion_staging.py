"""Ensayo sintético de Autogestión en staging, caminos A y B (CEM LAB: SINTETICO, PRUEBA, NO REAL).

Mandato del propietario del 28-10-2026: probar Autogestión de punta a punta con escrituras reales
en staging, sobre consorcios sintéticos inequívocamente marcados, sin activar ningún consorcio
real. Es **cobertura técnica, no decisión de negocio**: el administrador propietario, las actas y
la matrícula del RPA son supuestos del ensayo, no la política real, y las actas son
`ACTA SINTETICA DE PRUEBA — SIN VALIDEZ JURIDICA`. Los datos están en `cem_lab_autogestion.json`.

Corre la secuencia del equipo (CEM `doc/diseno/Autogestion_Preparacion_2026-10-28.md`, §3) con
los mismos scripts: `alta_consorcio`, la invitación de soporte que crea `scripts.invitar` con la
aceptación por HTTP (el correo es `.invalid`: no sale), la identidad humana de soporte (A13-C),
`alta_mandato` primero con `--simular` y después sin él, y lee el estado después de cada paso
(base y API). Cada negativo comprueba el motivo y que no quedó nada escrito. Los negativos que
necesitan un estado que el fixture no debe conservar (un administrador externo, un Consejo que
empezó hoy) corren dentro de una transacción que se deshace, y se comprueba después que no quedó.

**Fixture persistente de staging.** Los dos consorcios LAB, sus unidades, personas, decisiones,
modos y mandatos quedan en staging: no hay un procedimiento probado para borrarlos y no
interfieren con el smoke (que usa solo su propio consorcio). Por eso el ensayo **corre una sola
vez**: si un consorcio LAB ya existe, se niega sin escribir. Al terminar revoca todas las
sesiones de las personas sintéticas.

No va en la imagen (emite sesiones de prueba). Corre como el smoke, leído de la entrada estándar
dentro de `cem-backend-staging`, con los datos en base64 por argumento:

    ssh oci 'docker exec -i -w /app -e PYTHONPATH=/app <contenedor> python - \\
        --datos <base64 de cem_lab_autogestion.json> --base <host de cem-postgres-staging> \\
        --url https://coolify-proxy --host cem-staging.holancloud.com \\
        --sin-verificar-certificado' < backend/pruebas/ensayo_autogestion_staging.py

Guarda dura, igual que el smoke: origen público de staging y la base `--base`. Nunca en
producción. Sale con 0 solo si todo dio lo esperado.
"""

import argparse
import base64
import contextlib
import hashlib
import io
import json
import os
import ssl
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date, timedelta
from http.cookies import SimpleCookie
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from cem_contracts.estados import TipoVinculo
from cem_contracts.invitaciones import InvitacionNueva
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from app.config import cargar
from app.db import crear_motor, transaccion
from app.identidad import invitaciones
from scripts import alta_consorcio, alta_mandato

# Lo mismo que `smoke_staging.py` (guarda y navegador): corre leído de la entrada estándar, sin
# el resto del directorio `pruebas`, así que no puede importarlo.
SUFIJO_STAGING = "-staging"
IP_DE_PRUEBA = "198.51.100.78"
"""RFC 5737 (documentación), distinta de la del smoke: los límites de tasa no se mezclan."""


class Negado(Exception):
    """La guarda dura: el entorno no es staging o el ensayo ya corrió."""


class Fallo(Exception):
    """Un paso no dio lo esperado."""


def comprobar(condicion: bool, que: str) -> None:
    if not condicion:
        raise Fallo(que)


def exigir_staging(origen_publico: str, database_url: str, base: str) -> None:
    host_publico = urlsplit(origen_publico).hostname or ""
    if not host_publico.split(".")[0].endswith(SUFIJO_STAGING):
        raise Negado(f"CEM_ORIGEN_PUBLICO no es de staging: {host_publico!r}")
    host_base = make_url(database_url).host
    if host_base != base:
        raise Negado(f"CEM_DATABASE_URL apunta a {host_base!r}, no a --base {base!r}")


@dataclass
class Respuesta:
    estado: int
    cuerpo: object
    texto: str


@dataclass
class Navegador:
    url: str
    host: str
    contexto: ssl.SSLContext | None
    cookies: dict[str, str] = field(default_factory=dict)

    def pedir(self, metodo: str, ruta: str, cuerpo: object = None) -> Respuesta:
        encabezados = {
            "Host": self.host,
            "Accept": "application/json",
            "CF-Connecting-IP": IP_DE_PRUEBA,
        }
        datos = None
        if cuerpo is not None:
            datos = json.dumps(cuerpo).encode()
            encabezados["Content-Type"] = "application/json"
        if self.cookies:
            encabezados["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        pedido = urllib.request.Request(
            self.url + ruta, data=datos, headers=encabezados, method=metodo
        )
        try:
            r = urllib.request.urlopen(pedido, context=self.contexto, timeout=30)
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
        texto = r.read().decode()
        cuerpo_json = (
            json.loads(texto)
            if texto and r.headers.get_content_type() == ("application/json")
            else None
        )
        return Respuesta(r.status, cuerpo_json, texto)


SOPORTE = "soporte"
CUIT_SINTETICO = "30000000007"
"""El del smoke: válido (dígito verificador) y sin dueño, solo para el externo de un negativo."""
REFERENCIA_IDENTIDAD = (
    "SINTETICO / PRUEBA / NO REAL: cuenta LAB sin documento, huella al azar (ensayo de Autogestión)"
)
SIN_PAQUETE = {"egresos.aprobar_orden", "liquidacion.aprobar", "coeficientes.aprobar"}
"""El administrador no tiene ninguna, tampoco en Autogestión: aprobar órdenes es del Consejo
(`0046`) y las otras dos no están en ningún paquete (decisión 7 del 21-10)."""


class _Deshacer(Exception):
    """Sale de una transacción de negativo para que se deshaga entera."""


class Ensayo:
    def __init__(self, motor, datos: dict, origen: str, url: str, host: str, contexto):
        self.motor = motor
        self.datos = datos
        self.origen = origen
        self.url = url
        self.host = host
        self.contexto = contexto
        self.controles: list[str] = []
        self.creado: dict = {"consorcios": {}}
        self.personas: dict[str, UUID] = {}
        self.navegadores: dict[str, Navegador] = {}

    def ok(self, que: str) -> None:
        self.controles.append(que)
        print(f"ok  {que}", flush=True)

    def tx(self, consorcio: UUID, persona: UUID | None = None):
        return transaccion(self.motor, persona_id=persona, consorcio_id=consorcio)

    # Lectura del estado ------------------------------------------------------------------

    def filas(self, cons: UUID) -> tuple[int, int, int]:
        with self.tx(cons) as c:
            return tuple(
                c.execute(
                    text(
                        "select"
                        " (select count(*) from nucleo.decision_formal where consorcio_id = :c),"
                        " (select count(*) from nucleo.modo_operacion where consorcio_id = :c),"
                        " (select count(*) from nucleo.mandato where consorcio_id = :c)"
                    ),
                    {"c": cons},
                ).one()
            )

    def periodos(self, cons: UUID) -> list[tuple]:
        with self.tx(cons) as c:
            return [
                (f.modo, f.vigente_desde, f.vigente_hasta, f.decision_id)
                for f in c.execute(
                    text(
                        "select modo::text as modo, vigente_desde, vigente_hasta, decision_id"
                        " from nucleo.modo_operacion where consorcio_id = :c order by vigente_desde"
                    ),
                    {"c": cons},
                )
            ]

    def hoy(self, cons: UUID) -> date:
        with self.tx(cons) as c:
            return c.execute(text("select nucleo.hoy_del_consorcio(:c)"), {"c": cons}).scalar_one()

    def mandato(self, cons: UUID, tipo: str, persona: UUID) -> dict:
        with self.tx(cons) as c:
            m = (
                c.execute(
                    text(
                        "select id, persona_id, vigente_desde, vigente_hasta, matricula, origen,"
                        " decision_id from nucleo.mandato where consorcio_id = :c and tipo = :t"
                        " and persona_id = :p"
                    ),
                    {"c": cons, "t": tipo, "p": persona},
                )
                .mappings()
                .one()
            )
            facultades = set(
                c.execute(
                    text("select facultad from nucleo.facultad_otorgada where mandato_id = :m"),
                    {"m": m["id"]},
                ).scalars()
            )
            paquete = set(
                c.execute(
                    text("select facultad from nucleo.paquete_facultad where tipo = :t"),
                    {"t": tipo},
                ).scalars()
            )
        return dict(m) | {"facultades": facultades, "paquete": paquete}

    def decision(self, cons: UUID, decision_id: UUID) -> dict:
        with self.tx(cons) as c:
            return dict(
                c.execute(
                    text(
                        "select tipo::text as tipo, fecha, descripcion, referencia_acta,"
                        " sha256_acta from nucleo.decision_formal where id = :d"
                    ),
                    {"d": decision_id},
                )
                .mappings()
                .one()
            )

    def cadena(self, cons: UUID) -> tuple[int, int, int, int]:
        """La misma cuenta que el smoke: total, enlaces rotos, hashes distintos y el control con
        la acción alterada (tiene que detectar todos)."""
        formula = (
            "encode(sha256(convert_to(concat_ws('|', hash_previo, consorcio_id, id, secuencia,"
            " to_char(creado_en at time zone 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS.US'),"
            " actor_persona_id, origen, {accion}, entidad, entidad_id, antes, despues, motivo, ip),"
            " 'UTF8')), 'hex')"
        )
        with self.tx(cons) as conn:
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
                    {"c": cons},
                ).one()
            )

    # Pasos con los scripts del equipo ----------------------------------------------------

    def alta_mandato(self, datos: dict, *extra: str) -> tuple[int, str, str]:
        with tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8") as f:
            json.dump(datos, f, ensure_ascii=False)
            f.flush()
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                codigo = alta_mandato.main(["--archivo", f.name, *extra])
        return codigo, out.getvalue(), err.getvalue()

    def negado(self, cons: UUID, datos: dict, motivo: str, que: str) -> None:
        antes = self.filas(cons)
        codigo, out, err = self.alta_mandato(datos)
        comprobar(
            codigo == 1 and "no se escribió nada" in err and motivo in err,
            f"{que}: se esperaba {motivo}, salió {codigo}: {out}{err}",
        )
        despues = self.filas(cons)
        comprobar(despues == antes, f"{que}: quedó escrito algo ({antes} -> {despues})")
        self.ok(f"{que}: {motivo}, sin escribir nada (decisiones, modos, mandatos = {despues})")

    def negado_en_transaccion(
        self, cons: UUID, preparar, intento, motivo: str | None, que: str
    ) -> None:
        """Prepara un estado, intenta y comprueba el motivo, todo en una transacción que se
        deshace entera. Después comprueba que los conteos del consorcio no cambiaron. Con
        `motivo` None es un control positivo: el intento no puede fallar de ningún modo."""
        antes = self.filas(cons)
        error = None
        try:
            with self.tx(cons) as c:
                preparar(c)
                try:
                    with c.begin_nested():
                        intento(c)
                except DBAPIError as e:
                    error = e
                raise _Deshacer()
        except _Deshacer:
            pass
        if motivo is None:
            comprobar(error is None, f"{que}: se esperaba que pasara, falló: {error}")
            resultado = "pasa"
        else:
            comprobar(
                error is not None
                and error.orig.sqlstate == "P0001"
                and error.orig.diag.message_hint == motivo,
                f"{que}: se esperaba {motivo}, salió {error}",
            )
            resultado = motivo
        despues = self.filas(cons)
        comprobar(despues == antes, f"{que}: quedó escrito algo ({antes} -> {despues})")
        self.ok(f"{que}: {resultado}; la transacción se deshizo y no quedó nada ({despues})")

    def arranque(
        self,
        cons: UUID,
        acta: dict,
        fecha: date,
        modo: str | None,
        desde: date,
        mandatos: list[dict],
        sha: bool = False,
    ) -> dict:
        d = {
            "consorcio_id": str(cons),
            "decision": {
                "tipo": "asamblea",
                "fecha": fecha.isoformat(),
                "descripcion": acta["descripcion"],
                "referencia_acta": acta["referencia"],
            },
            "mandatos": mandatos,
        }
        if modo is not None:
            d["modo_inicial"] = {"modo": modo, "desde": desde.isoformat()}
        if sha:
            # La huella de la referencia sintética, no la de un escaneo: no existe documento.
            d["decision"]["sha256_acta"] = hashlib.sha256(acta["referencia"].encode()).hexdigest()
        return d

    def dar_de_alta(self, camino: str) -> tuple[UUID, dict[str, UUID]]:
        c = self.datos["caminos"][camino]
        cons = UUID(c["consorcio_id"])
        archivo = {
            "consorcio": {
                "id": str(cons),
                "nombre": c["nombre"],
                "zona_horaria": self.datos["zona_horaria"],
            },
            "unidades": [{"designacion": u} for u in self.datos["unidades"]],
        }
        with tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8") as f:
            json.dump(archivo, f, ensure_ascii=False)
            f.flush()
            for extra in (["--simular"], []):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    codigo = alta_consorcio.main(["--archivo", f.name, *extra])
                comprobar(
                    codigo == 0, f"alta_consorcio {camino} {extra}: {codigo} {out.getvalue()}"
                )
                esperado = "SIMULACIÓN" if extra else ": creado"
                comprobar(esperado in out.getvalue(), f"alta_consorcio {camino}: {out.getvalue()}")
        with self.tx(cons) as conn:
            nombre = conn.execute(
                text("select nombre from nucleo.consorcio where id = :c"), {"c": cons}
            ).scalar_one()
            unidades = dict(
                conn.execute(
                    text("select designacion, id from nucleo.unidad where consorcio_id = :c"),
                    {"c": cons},
                ).all()
            )
        comprobar(
            nombre == c["nombre"] and sorted(unidades) == sorted(self.datos["unidades"]),
            f"consorcio {camino} leído después del alta",
        )
        self.creado["consorcios"][camino] = {
            "id": str(cons),
            "nombre": nombre,
            "unidades": {k: str(v) for k, v in unidades.items()},
        }
        self.ok(
            f"camino {camino}: alta_consorcio (simulado y real) con {len(unidades)} unidades LAB"
        )
        return cons, unidades

    def entrar(self, camino: str, cons: UUID, unidades: dict[str, UUID], rol: str) -> Navegador:
        p = self.datos["caminos"][camino]["personas"][rol]
        with self.tx(cons) as conn:
            creada = invitaciones.crear(
                conn,
                consorcio_id=cons,
                nueva=InvitacionNueva(
                    unidad_id=unidades[p["unidad"]],
                    tipo=TipoVinculo.PROPIETARIO,
                    correo=p["correo"],
                    nombre=p["nombre"],
                ),
                invitada_por=None,
                origen_publico=self.origen,
                origen=SOPORTE,
            )
        comprobar(creada is not None, f"invitación de soporte para {rol}")
        n = Navegador(self.url, self.host, self.contexto)
        r = n.pedir("POST", "/api/v1/auth/invitacion/aceptar", {"token": creada.token})
        comprobar(r.estado == 200 and r.cuerpo["cuenta_nueva"] is True, f"aceptar {rol}: {r.texto}")
        persona = UUID(r.cuerpo["persona_id"])
        self.personas[rol] = persona
        self.navegadores[rol] = n
        self.creado.setdefault("personas", {})[rol] = str(persona)
        self.creado.setdefault("invitaciones", {})[rol] = str(creada.invitacion.id)
        return n

    def identidad(self, cons: UUID, rol: str) -> None:
        with self.tx(cons) as conn:
            conn.execute(
                text(
                    "select nucleo.verificar_identidad_humana(:p, array[1]::smallint[],"
                    " array[:h]::bytea[], 'documento_presencial', :r, 'Ensayo LAB')"
                ),
                {"p": self.personas[rol], "h": os.urandom(32), "r": REFERENCIA_IDENTIDAD},
            )

    def c(self, cons: UUID, resto: str = "") -> str:
        return f"/api/v1/consorcios/{cons}{resto}"

    # Camino A ----------------------------------------------------------------------------

    def camino_a(self) -> None:
        datos = self.datos["caminos"]["A"]
        cons, u = self.dar_de_alta("A")
        adm = self.entrar("A", cons, u, "propietario01")
        otro = self.entrar("A", cons, u, "propietario02")
        self.identidad(cons, "propietario01")
        self.ok(
            "camino A: dos propietarios sintéticos entran por invitación (cuenta nueva) y"
            " soporte verifica la identidad del futuro administrador (A13-C)"
        )
        h = self.hoy(cons)
        acta = datos["actas"]["TEST-AG-001"]
        fecha = h - timedelta(days=acta["dias_antes_de_hoy"])
        p1, p2 = self.personas["propietario01"], self.personas["propietario02"]

        def admin(persona: UUID, desde: date, **extra) -> dict:
            return {
                "tipo": "administrador",
                "persona_id": str(persona),
                "desde": desde.isoformat(),
            } | extra

        mat = datos["matricula_rpa"]
        bueno = self.arranque(
            cons, acta, fecha, "autogestion", fecha, [admin(p1, fecha, matricula=mat)], sha=True
        )

        # Negativos antes del arranque: el consorcio no tiene nada.
        comprobar(self.filas(cons) == (0, 0, 0), "camino A parte de cero")
        malo = json.loads(json.dumps(bueno))
        malo["modo_inicial"]["desde"] = (fecha - timedelta(days=1)).isoformat()
        self.negado(cons, malo, "fecha_invalida", "A: el modo empieza antes de su decisión")
        malo = json.loads(json.dumps(bueno))
        malo["modo_inicial"]["desde"] = (h + timedelta(days=30)).isoformat()
        self.negado(cons, malo, "fecha_invalida", "A: el modo empieza en el futuro")
        malo = json.loads(json.dumps(bueno))
        malo["mandatos"].append(admin(p2, fecha))
        self.negado(cons, malo, "administracion_ocupada", "A: dos administradores a la vez")
        malo = json.loads(json.dumps(bueno))
        malo["decision"]["fecha"] = (h + timedelta(days=1)).isoformat()
        self.negado(cons, malo, "fecha_futura", "A: decisión con fecha futura")
        malo = json.loads(json.dumps(bueno))
        malo["decision"]["tipo"] = "consejo"
        self.negado(
            cons, malo, "decision_invalida", "A: el modo con una decisión que no es de asamblea"
        )

        codigo, out, err = self.alta_mandato(bueno, "--simular")
        comprobar(codigo == 0 and out.startswith("SIMULACIÓN"), f"A --simular: {out}{err}")
        comprobar(self.filas(cons) == (0, 0, 0), "A --simular no escribió nada")
        self.ok("A: alta_mandato --simular pasa y no escribe")
        codigo, out, err = self.alta_mandato(bueno)
        comprobar(codigo == 0 and acta["referencia"] in out, f"A arranque: {out}{err}")

        # Estado posterior, en la base.
        [(modo, desde, hasta, dec)] = self.periodos(cons)
        comprobar(
            (modo, desde, hasta) == ("autogestion", fecha, None),
            f"A: modo vigente {modo} {desde} {hasta}",
        )
        d = self.decision(cons, dec)
        comprobar(
            d["tipo"] == "asamblea"
            and d["fecha"] == fecha
            and d["referencia_acta"] == acta["referencia"]
            and "SIN VALIDEZ JURIDICA" in d["referencia_acta"]
            and d["sha256_acta"] == bueno["decision"]["sha256_acta"],
            f"A: decisión {d}",
        )
        m = self.mandato(cons, "administrador", p1)
        comprobar(
            (m["vigente_desde"], m["vigente_hasta"], m["origen"], m["decision_id"], m["matricula"])
            == (fecha, None, SOPORTE, dec, mat),
            f"A: mandato {m}",
        )
        comprobar(
            m["facultades"] == m["paquete"]
            and "egresos.preparar_orden" in m["facultades"]
            and not SIN_PAQUETE & m["facultades"],
            f"A: facultades {sorted(m['facultades'])}",
        )
        comprobar(self.filas(cons) == (1, 1, 1), f"A: filas {self.filas(cons)}")
        self.creado["consorcios"]["A"] |= {
            "decision_TEST-AG-001": str(dec),
            "mandato_administrador": str(m["id"]),
        }
        self.ok(
            f"A: Autogestión vigente desde {fecha} con la decisión TEST-AG-001 (SIN VALIDEZ"
            f" JURIDICA, sha256 de la referencia), mandato administrador del propietario"
            f" sintético con matrícula RPA-TEST, {len(m['facultades'])} facultades = el"
            f" paquete, ninguna de aprobar dinero"
        )

        # Estado posterior, por la API y con permisos reales.
        r = adm.pedir("GET", self.c(cons, "/modos"))
        comprobar(
            r.estado == 200
            and [(e["modo"], e["vigente_hasta"]) for e in r.cuerpo["elementos"]]
            == [("autogestion", None)],
            f"A: GET /modos: {r.texto}",
        )
        r = adm.pedir("GET", self.c(cons, "/mandatos"))
        comprobar(
            r.estado == 200
            and [(e["tipo"], e["estado"]) for e in r.cuerpo["elementos"]]
            == [("administrador", "vigente")],
            f"A: GET /mandatos: {r.texto}",
        )
        r = adm.pedir("GET", self.c(cons, f"/decisiones/{dec}"))
        comprobar(
            r.estado == 200 and "SIN VALIDEZ JURIDICA" in r.cuerpo["referencia"],
            f"A: GET /decisiones/{{id}}: {r.texto}",
        )
        r = adm.pedir("GET", self.c(cons, "/padron"))
        comprobar(r.estado == 200, f"A: el administrador lee el padrón: {r.texto}")
        r = otro.pedir("GET", self.c(cons, "/mandatos"))
        comprobar(r.estado == 403, f"A: un propietario sin mandato no ve los mandatos: {r.texto}")
        r = otro.pedir("GET", self.c(cons, "/modos"))
        comprobar(r.estado == 403, f"A: un propietario sin mandato no ve los modos: {r.texto}")
        self.ok(
            "A: por la API, el administrador ve Autogestión vigente, su mandato, la decisión"
            " con la marca SIN VALIDEZ JURIDICA y el padrón; el otro propietario recibe 403"
            " en mandatos y modos"
        )

        # Después del arranque: AG -> Consejo no está en el diagrama 12.
        otra = self.arranque(cons, acta, h, "consejo", h, [])
        otra["decision"]["descripcion"] += " [negativo: AG -> Consejo]"
        self.negado(cons, otra, "transicion_no_admitida", "A: Autogestión -> Consejo")
        otra = self.arranque(cons, acta, h, "autogestion", h, [])
        self.negado(cons, otra, "mismo_modo", "A: Autogestión -> Autogestión")

    # Camino B ----------------------------------------------------------------------------

    def camino_b(self) -> None:
        datos = self.datos["caminos"]["B"]
        cons, u = self.dar_de_alta("B")
        mc1 = self.entrar("B", cons, u, "consejero01")
        self.entrar("B", cons, u, "consejero02")
        adm = self.entrar("B", cons, u, "propietario03")
        self.identidad(cons, "propietario03")
        self.ok(
            "camino B: dos consejeros y el futuro administrador entran por invitación; soporte"
            " verifica la identidad del futuro administrador"
        )
        h = self.hoy(cons)
        a1, a2 = datos["actas"]["TEST-AG-002"], datos["actas"]["TEST-AG-003"]
        inicio = h - timedelta(days=a1["dias_antes_de_hoy"])
        c1, c2, p3 = (self.personas[k] for k in ("consejero01", "consejero02", "propietario03"))

        def mandato(tipo: str, persona: UUID, desde: date, **extra) -> dict:
            return {"tipo": tipo, "persona_id": str(persona), "desde": desde.isoformat()} | extra

        # Primer tiempo: Consejo con la decisión 1.
        primero = self.arranque(
            cons,
            a1,
            inicio,
            "consejo",
            inicio,
            [mandato("miembro_consejo", c1, inicio), mandato("miembro_consejo", c2, inicio)],
        )
        codigo, out, err = self.alta_mandato(primero, "--simular")
        comprobar(codigo == 0 and self.filas(cons) == (0, 0, 0), f"B1 --simular: {out}{err}")
        codigo, out, err = self.alta_mandato(primero)
        comprobar(codigo == 0, f"B1: {out}{err}")
        [(modo, desde, hasta, dec1)] = self.periodos(cons)
        comprobar((modo, desde, hasta) == ("consejo", inicio, None), f"B1: {modo} {desde} {hasta}")
        comprobar(self.filas(cons) == (1, 1, 2), f"B1: filas {self.filas(cons)}")
        r = mc1.pedir("GET", self.c(cons, "/modos"))
        comprobar(
            r.estado == 200
            and [(e["modo"], e["vigente_hasta"]) for e in r.cuerpo["elementos"]]
            == [("consejo", None)],
            f"B1: GET /modos del consejero: {r.texto}",
        )
        r = adm.pedir("GET", self.c(cons, "/mandatos"))
        comprobar(r.estado == 403, f"B1: el futuro administrador todavía no ve mandatos: {r.texto}")
        self.creado["consorcios"]["B"] |= {"decision_TEST-AG-002": str(dec1)}
        self.ok(
            f"B: primer tiempo, Consejo vigente desde {inicio} con TEST-AG-002 y dos"
            " consejeros; el consejero lo ve por la API y el futuro administrador todavía"
            " recibe 403"
        )

        segundo = self.arranque(cons, a2, h, "autogestion", h, [mandato("administrador", p3, h)])

        # Negativos del segundo tiempo, sobre el consorcio en Consejo.
        def cambiar_con_la_misma(c):
            c.execute(
                text("select nucleo.cambiar_modo('autogestion', :d, :h, null, 'soporte')"),
                {"d": dec1, "h": h},
            )

        self.negado_en_transaccion(
            cons,
            lambda c: None,
            cambiar_con_la_misma,
            "decision_ya_usada",
            "B: la misma decisión para los dos modos",
        )
        malo = json.loads(json.dumps(segundo))
        malo["modo_inicial"]["desde"] = (h - timedelta(days=1)).isoformat()
        malo["decision"]["fecha"] = (h - timedelta(days=1)).isoformat()
        self.negado(cons, malo, "fecha_invalida", "B: cambio de modo con fecha anterior a hoy")
        malo = json.loads(json.dumps(segundo))
        malo["decision"]["fecha"] = (h + timedelta(days=1)).isoformat()
        self.negado(cons, malo, "fecha_futura", "B: decisión futura")
        malo = json.loads(json.dumps(segundo))
        malo["decision"]["tipo"] = "consejo"
        self.negado(
            cons, malo, "decision_invalida", "B: cambio con una decisión que no es de asamblea"
        )
        malo = json.loads(json.dumps(segundo))
        malo["modo_inicial"]["modo"] = "consejo"
        malo["mandatos"] = []
        self.negado(cons, malo, "mismo_modo", "B: Consejo -> Consejo")
        malo = json.loads(json.dumps(segundo))
        malo["mandatos"][0]["hasta"] = (h - timedelta(days=1)).isoformat()
        self.negado(
            cons,
            malo,
            "vigencia_invalida",
            "B: segundo mandato incoherente (fin antes del inicio; se deshacen también"
            " la decisión y el modo de la corrida)",
        )
        malo = json.loads(json.dumps(segundo))
        malo["mandatos"].append(mandato("administrador", c1, h))
        self.negado(cons, malo, "administracion_ocupada", "B: dos administradores en el cambio")

        def externo(desde: date, hasta: date | None):
            def preparar(c):
                c.execute(
                    text(
                        "select nucleo.alta_mandato('administrador_externo', null,"
                        " 'Administración externa SINTETICA (LAB, NO REAL)', :cuit, null, :d,"
                        " :desde, :hasta, null, null, 'soporte')"
                    ),
                    {"cuit": CUIT_SINTETICO, "d": dec1, "desde": desde, "hasta": hasta},
                )

            return preparar

        def cambio_con_decision_nueva(c):
            d2 = c.execute(
                text(
                    "select nucleo.registrar_decision('asamblea', :h, :desc, :r, null, 'soporte')"
                ),
                {
                    "h": h,
                    "desc": a2["descripcion"] + " [negativo, se deshace]",
                    "r": a2["referencia"],
                },
            ).scalar_one()
            c.execute(
                text("select nucleo.cambiar_modo('autogestion', :d, :h, null, 'soporte')"),
                {"d": d2, "h": h},
            )

        self.negado_en_transaccion(
            cons,
            externo(inicio, None),
            cambio_con_decision_nueva,
            "transicion_requiere_fin_de_mandato",
            "B: con un administrador externo vigente",
        )
        self.negado_en_transaccion(
            cons,
            externo(h + timedelta(days=15), None),
            cambio_con_decision_nueva,
            "transicion_requiere_fin_de_mandato",
            "B: con un administrador externo futuro",
        )
        # Control de los dos anteriores: con un externo que ya terminó, el cambio pasa (y se
        # deshace igual, para no dejar el externo).
        self.negado_en_transaccion(
            cons,
            externo(inicio, h - timedelta(days=1)),
            cambio_con_decision_nueva,
            None,
            "B (control): con un externo que ya terminó, el cambio pasa",
        )

        # Un Consejo que empezó hoy no cambia el mismo día: hace falta otro consorcio, que vive
        # solo dentro de la transacción deshecha.
        tmp = uuid4()

        def consejo_hoy(c):
            c.execute(
                text(
                    "insert into nucleo.consorcio (id, nombre, zona_horaria) values"
                    " (:i, 'CEM LAB — temporal de un negativo (SINTETICO, se deshace)', :z)"
                ),
                {"i": tmp, "z": self.datos["zona_horaria"]},
            )
            d = c.execute(
                text(
                    "select nucleo.registrar_decision('asamblea', :h, 'SINTETICO', :r,"
                    " null, 'soporte')"
                ),
                {"h": h, "r": a1["referencia"]},
            ).scalar_one()
            c.execute(
                text("select nucleo.cambiar_modo('consejo', :d, :h, null, 'soporte')"),
                {"d": d, "h": h},
            )

        def cambio_tmp(c):
            d = c.execute(
                text(
                    "select nucleo.registrar_decision('asamblea', :h, 'SINTETICO', :r,"
                    " null, 'soporte')"
                ),
                {"h": h, "r": a2["referencia"]},
            ).scalar_one()
            c.execute(
                text("select nucleo.cambiar_modo('autogestion', :d, :h, null, 'soporte')"),
                {"d": d, "h": h},
            )

        hint = None
        try:
            with self.tx(tmp) as c:
                consejo_hoy(c)
                try:
                    with c.begin_nested():
                        cambio_tmp(c)
                except DBAPIError as e:
                    hint = e.orig.diag.message_hint
                raise _Deshacer()
        except _Deshacer:
            pass
        with self.tx(tmp) as c:
            quedo = c.execute(
                text("select count(*) from nucleo.consorcio where id = :i"), {"i": tmp}
            ).scalar_one()
        comprobar(
            hint == "fecha_invalida" and quedo == 0,
            f"B: Consejo iniciado hoy: {hint}, quedó {quedo}",
        )
        self.ok(
            "B: un Consejo que empezó hoy no pasa a Autogestión el mismo día (fecha_invalida);"
            " el consorcio temporal del negativo no quedó"
        )
        comprobar(self.filas(cons) == (1, 1, 2), f"B: después de los negativos {self.filas(cons)}")

        # Segundo tiempo real.
        codigo, out, err = self.alta_mandato(segundo, "--simular")
        comprobar(codigo == 0 and self.filas(cons) == (1, 1, 2), f"B2 --simular: {out}{err}")
        codigo, out, err = self.alta_mandato(segundo)
        comprobar(codigo == 0 and a2["referencia"] in out, f"B2: {out}{err}")
        pp = self.periodos(cons)
        comprobar(
            [(m, d, hh) for m, d, hh, _ in pp]
            == [("consejo", inicio, h), ("autogestion", h, None)],
            f"B2: historia {pp}",
        )
        dec2 = pp[1][3]
        comprobar(dec2 != dec1 and pp[0][3] == dec1, "B2: cada período con su decisión")
        d = self.decision(cons, dec2)
        comprobar(d["fecha"] == h and "SIN VALIDEZ JURIDICA" in d["referencia_acta"], f"B2: {d}")
        m = self.mandato(cons, "administrador", p3)
        comprobar(
            (m["vigente_desde"], m["vigente_hasta"], m["decision_id"]) == (h, None, dec2)
            and m["facultades"] == m["paquete"]
            and not SIN_PAQUETE & m["facultades"],
            f"B2: mandato {m}",
        )
        consejeros = [self.mandato(cons, "miembro_consejo", x) for x in (c1, c2)]
        comprobar(
            all(x["vigente_hasta"] is None for x in consejeros),
            "B2: los consejeros siguen vigentes (observado, sin regla todavía)",
        )
        comprobar(self.filas(cons) == (2, 2, 3), f"B2: filas {self.filas(cons)}")
        self.creado["consorcios"]["B"] |= {
            "decision_TEST-AG-003": str(dec2),
            "mandato_administrador": str(m["id"]),
            "mandatos_consejo": [str(x["id"]) for x in consejeros],
        }
        self.ok(
            f"B: segundo tiempo, Consejo {inicio}..{h} y Autogestión desde {h}, cada uno con su"
            " decisión; administrador = propietario sintético con el paquete entero y nada de"
            " aprobar dinero; los dos consejeros siguen vigentes (sin regla: documento 08)"
        )

        r = adm.pedir("GET", self.c(cons, "/modos"))
        comprobar(
            r.estado == 200
            and [(e["modo"], e["vigente_hasta"]) for e in r.cuerpo["elementos"]]
            == [("autogestion", None), ("consejo", h.isoformat())],
            f"B2: GET /modos del administrador: {r.texto}",
        )
        r = adm.pedir("GET", self.c(cons, "/mandatos"))
        comprobar(
            r.estado == 200
            and sorted(e["tipo"] for e in r.cuerpo["elementos"])
            == ["administrador", "miembro_consejo", "miembro_consejo"],
            f"B2: GET /mandatos del administrador: {r.texto}",
        )
        self.ok(
            "B: por la API el administrador nuevo ve la historia de dos modos y los tres mandatos"
        )

        # Autogestión rige desde hoy: cualquier cambio de hoy choca primero con la fecha (la
        # transición AG -> Consejo, que el diagrama no admite, se prueba en el camino A).
        otra = self.arranque(cons, a2, h, "consejo", h, [])
        self.negado(cons, otra, "fecha_invalida", "B: después del cambio, otro cambio el mismo día")

    # Cierre ------------------------------------------------------------------------------

    def cerrar(self) -> tuple[int, list[str]]:
        for camino, rol in (
            ("A", "propietario01"),
            ("A", "propietario02"),
            ("B", "consejero01"),
            ("B", "consejero02"),
            ("B", "propietario03"),
        ):
            if rol not in self.personas:
                continue
            cons = UUID(self.datos["caminos"][camino]["consorcio_id"])
            with self.tx(cons) as conn:
                conn.execute(
                    text("select nucleo.revocar_sesiones_de(:p)"), {"p": self.personas[rol]}
                )
        abiertas = 0
        for persona in self.personas.values():
            with transaccion(self.motor, persona_id=persona) as conn:
                abiertas += conn.execute(
                    text(
                        "select count(*) from nucleo.sesion where persona_id = :p"
                        " and revocada_en is null and vence_en > now()"
                    ),
                    {"p": persona},
                ).scalar_one()
        entran = [
            rol
            for rol, n in self.navegadores.items()
            if n.pedir("GET", "/api/v1/me/consorcios").estado != 401
        ]
        return abiertas, entran


def main(argumentos: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--datos", required=True, help="cem_lab_autogestion.json en base64")
    p.add_argument("--base", required=True, help="host de cem-postgres-staging")
    p.add_argument("--url", required=True)
    p.add_argument("--host", required=True)
    p.add_argument("--sin-verificar-certificado", action="store_true")
    a = p.parse_args(argumentos)
    datos = json.loads(base64.b64decode(a.datos).decode("utf-8"))
    config = cargar()
    try:
        exigir_staging(config.origen_publico, config.database_url, a.base)
    except Negado as e:
        print(f"negado: {e}", file=sys.stderr)
        return 3
    for c in datos["caminos"].values():
        if not all(x in c["nombre"] for x in ("LAB", "SINTETICO", "NO REAL")):
            print(f"negado: el nombre no lleva las marcas: {c['nombre']!r}", file=sys.stderr)
            return 3
        if not all(x["correo"].endswith(".invalid") for x in c["personas"].values()):
            print("negado: un correo no es .invalid", file=sys.stderr)
            return 3
        if not all("NO REAL" in x["nombre"] for x in c["personas"].values()):
            print("negado: una persona no lleva las marcas", file=sys.stderr)
            return 3
        if not all("SIN VALIDEZ JURIDICA" in x["referencia"] for x in c["actas"].values()):
            print("negado: un acta no lleva las marcas", file=sys.stderr)
            return 3
    if not all(u.startswith("LAB ") for u in datos["unidades"]):
        print("negado: una unidad no lleva las marcas", file=sys.stderr)
        return 3
    contexto = None
    if a.sin_verificar_certificado:
        contexto = ssl.create_default_context()
        contexto.check_hostname = False
        contexto.verify_mode = ssl.CERT_NONE
    motor = crear_motor(config.database_url)
    e = Ensayo(motor, datos, config.origen_publico, a.url.rstrip("/"), a.host, contexto)
    try:
        for camino, c in datos["caminos"].items():
            with e.tx(UUID(c["consorcio_id"])) as conn:
                existe = conn.execute(
                    text("select nombre from nucleo.consorcio where id = :c"),
                    {"c": UUID(c["consorcio_id"])},
                ).scalar_one_or_none()
            if existe is not None:
                print(
                    f"negado: el consorcio LAB {camino} ya existe ({existe!r}); el ensayo corre"
                    " una sola vez y queda como fixture persistente",
                    file=sys.stderr,
                )
                return 4
        error = None
        try:
            e.camino_a()
            e.camino_b()
            for camino, c in datos["caminos"].items():
                total, rotos, distintos, control = e.cadena(UUID(c["consorcio_id"]))
                comprobar(
                    total > 0 and rotos == 0 and distintos == 0 and control == total,
                    f"cadena {camino}: {total} {rotos} {distintos} {control}",
                )
                e.ok(
                    f"cadena de auditoría del consorcio {camino}: {total} eventos, 0 rotos, 0"
                    f" distintos (control: {control} de {total} con la acción alterada)"
                )
        except Fallo as f:
            error = f
            print(f"FALLÓ: {f}", file=sys.stderr, flush=True)
        finally:
            abiertas, entran = e.cerrar()
            print(
                json.dumps(
                    {
                        "creado": e.creado,
                        "sesiones_abiertas_al_cerrar": abiertas,
                        "navegadores_que_siguen_entrando": entran,
                        "controles": e.controles,
                    },
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                )
            )
        if error is not None or abiertas or entran:
            return 1
        return 0
    finally:
        motor.dispose()


if __name__ == "__main__":
    sys.exit(main())
