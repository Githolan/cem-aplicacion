"""Exportación de un consorcio por soporte (H2c §1.1, §5 y §7 «Exportación»; RF36; 0038).

`scripts.exportar_consorcio` corre como `cem_soporte_exportacion`: sin `BYPASSRLS` ni `SELECT`
directo, lee por las funciones de exportación y escribe solo `plataforma.exportacion`. Todo
contra servicios reales: PostgreSQL, el almacén S3 de pruebas, ClamAV (para que las versiones
lleguen a `disponible` o `rechazado` por el camino real) y `age`. Cada negación lleva su control:
el mismo pedido de quien sí tiene la capacidad pasa; cada búsqueda que da 0 se prueba antes en
un lugar donde lo que busca sí está.
"""

import io
import json
import os
import subprocess
import zipfile
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from app.db import fijar_ambito, transaccion
from scripts import exportar_consorcio
from tests.conftest import url_rol
from tests.test_coeficientes_0021 import EXPENSAS, porcentajes, registrar
from tests.test_condicion_habilitante_consejo import consejo, estado  # noqa: F401  (fixture)
from tests.test_contexto_mandatos import otra_unidad
from tests.test_documentos_2c1 import (  # noqa: F401  (`bucket` y `firmas_de_la_imagen` son fixtures)
    JPEG,
    PNG,
    _s3,
    analizar,
    bucket,
    crear,
    firmas_de_la_imagen,
    pdf,
    subir,
)
from tests.test_mandatos import otra_persona
from tests.test_mandatos_0012 import alta
from tests.test_modo_operacion import (
    cambiar,
    consorcio_con_persona,
    decidir,
    decidir_equipo,
    hoy_de,
)
from tests.test_vinculo_0014 import base_aparte  # noqa: F401  (`base_aparte` es un fixture)
from tests.test_vinculo_fin_0020 import finalizar

pytestmark = [pytest.mark.db, pytest.mark.redis]

FACULTADES = list(exportar_consorcio.FACULTADES)
IP = "203.0.113.77"
CONSENTIMIENTO = "Consentimiento firmado CONSENT-7781"
HASH_CONTRASENA = "$argon2id$v=19$m=65536,t=3,p=4$c2FsLWV4cG9ydA$HASH-DE-PRUEBA-EXPORTACION"
FUNCIONES = [f"exportar_{p}" for p in exportar_consorcio.PARTES] + ["exportar_objetos_documentos"]

# Las claves de cada fila del paquete, una por una. Una clave que no está acá es un dato que
# alguien agregó sin decidir que va (H2c §5, «Nunca van»).
CLAVES_POR_ENTIDAD = {
    "consorcio": {"id", "nombre", "cuit", "zona_horaria"},
    "unidad": {"id", "designacion"},
    "vinculo": {"id", "unidad_id", "designacion", "nombre", "tipo", "vigente_desde",
                "vigente_hasta", "confirmado"},
    "documento": {"id", "categoria", "titulo", "fecha", "cargado_en"},
    "version": {"id", "documento_id", "tipo", "tamano", "sha256", "subida_en", "archivo"},
    "decision": {"id", "tipo", "fecha", "descripcion", "referencia_acta", "sha256_acta"},
    "mandato": {"id", "tipo", "nombre", "cuit", "vigente_desde", "vigente_hasta", "estado",
                "con_cuenta", "matricula", "decision_id", "causa_baja", "baja_decision_id",
                "instrumento_referencia", "motivo_fin", "finalizado_en"},
    "facultad": {"mandato_id", "facultad", "recurso"},
    "revision": {"id", "mandato_id", "decision_id", "eficaz_desde"},
    "modo": {"modo", "vigente_desde", "vigente_hasta", "decision_id"},
    "coeficiente": {"esquema_id", "grupo", "version", "vigente_desde", "vigente_hasta",
                    "unidad_id", "designacion", "porcentaje"},
    "contacto": {"id", "funcion", "naturaleza", "nombre", "telefono", "indicaciones",
                 "vigente_desde", "vigente_hasta", "actualizado_en", "verificado_en",
                 "observado"},
    "evento": {"fecha", "accion", "recurso", "origen", "actor", "actor_nombre", "cambios"},
}  # fmt: skip


def _edificio(motor_migrate, motor_app, con_documentos: bool = True, externo: bool = False) -> dict:
    """Consorcio en Modo Consejo con:
    - un administrador (paquete completo) que carga los documentos;
    - un miembro del Consejo con el paquete completo (`mc`), uno limitado a `padron.ver`
      (`mc_padron`) y uno con las cinco facultades pero `padron.ver` acotado a una unidad
      (`mc_acotado`);
    - documentos con una versión PDF y una PNG `disponible`, una rechazada y una en cuarentena;
    - lo que nunca va: un evento con IP, la cadena de hashes, una negación, una contraseña, una
      sesión y la referencia del consentimiento de un contacto."""
    cons, uni, prop = consorcio_con_persona(motor_migrate)
    unidades = [uni] + [otra_unidad(motor_migrate, cons, f"{n}° A")[0] for n in range(2, 6)]
    with motor_migrate.begin() as c:
        c.execute(text("update nucleo.persona set nombre = 'Ana Exportada' where id = :p"),
                  {"p": prop})  # fmt: skip
    acta = decidir_equipo(motor_app, cons)
    cambiar(motor_app, cons, "consejo", acta)
    # El día del pedido es el del consorcio (Buenos Aires), no el del contenedor (UTC).
    e = {"cons": cons, "u": unidades, "prop": prop, "acta": acta, "hoy": hoy_de(motor_app, cons)}
    registrar(motor_app, cons, porcentajes(e, *EXPENSAS), decision=acta)

    def persona(nombre):
        p = otra_persona(motor_migrate, cons)
        with motor_migrate.begin() as c:
            c.execute(text("update nucleo.persona set nombre = :n where id = :p"),
                      {"n": nombre, "p": p})  # fmt: skip
        return p

    if externo:
        # La administración es un administrador externo con las cinco facultades generales. Su
        # paquete solo trae `documentos.cargar` (`alta_mandato` niega otorgarle más), así que se
        # le otorgan directo, como en `test_coeficientes_lectura.otorgar`. Va en lugar del
        # administrador: hay una sola administración a la vez (A14).
        e["ad"] = persona("Estudio Externo")
        e["m_ad"] = alta(motor_app, cons, None, acta, persona=e["ad"],
                         tipo="administrador_externo", nombre="Estudio Externo",
                         cuit="20111111112", origen="soporte")  # fmt: skip
        with motor_migrate.begin() as c:
            for f in FACULTADES:
                c.execute(
                    text("insert into nucleo.facultad_otorgada (consorcio_id, mandato_id,"
                         " facultad, decision_id) values (:c, :m, :f, :d)"),
                    {"c": cons, "m": e["m_ad"], "f": f, "d": acta},
                )  # fmt: skip
    else:
        e["ad"] = persona("Adela Administradora")
        e["m_ad"] = alta(motor_app, cons, None, acta, persona=e["ad"], tipo="administrador",
                         origen="soporte")  # fmt: skip
        # Una decisión que registra una persona: `registrada_por` es su identificador.
        e["decision_ad"] = decidir(motor_app, cons, e["ad"])
    e["mc"] = persona("Marta Consejera")
    e["m_mc"] = alta(motor_app, cons, None, acta, persona=e["mc"], tipo="miembro_consejo",
                     origen="soporte")  # fmt: skip
    e["mc_padron"] = persona("Pedro Padrón")
    alta(motor_app, cons, None, acta, persona=e["mc_padron"], tipo="miembro_consejo",
         facultades=["padron.ver"], origen="soporte")  # fmt: skip
    e["mc_acotado"] = persona("Ana Acotada")
    m = alta(motor_app, cons, None, acta, persona=e["mc_acotado"], tipo="miembro_consejo",
             facultades=[f for f in FACULTADES if f != "padron.ver"], origen="soporte")  # fmt: skip
    with motor_migrate.begin() as c:
        c.execute(
            text("insert into nucleo.facultad_otorgada (consorcio_id, mandato_id, facultad,"
                 " recurso, decision_id) values (:c, :m, 'padron.ver', :r, :d)"),
            {"c": cons, "m": m, "r": f"unidad:{uni}", "d": acta},
        )  # fmt: skip
    if con_documentos:
        e |= _documentos(e, motor_migrate, motor_app)
    e |= _lo_que_nunca_va(e, motor_migrate)
    return e


def _documentos(e, motor_migrate, motor_app) -> dict:
    def documento(categoria, titulo):
        r = crear(e["ad"], e["cons"], categoria=categoria, titulo=titulo)
        assert r.status_code == 201, r.text
        return r.json()["id"]

    def version(doc, contenido, tipo):
        r = subir(e["ad"], e["cons"], doc, contenido, tipo)
        assert r.status_code == 201, r.text
        return r.json()["id"]

    acta, otros, malo = (documento("actas_y_decisiones", "Acta de asamblea"),
                         documento("otros", "Plano"), documento("seguros", "Póliza"))  # fmt: skip
    binarios = {version(acta, pdf(), "application/pdf"): pdf(),
                version(otros, PNG, "image/png"): PNG}  # fmt: skip
    rechazada = version(malo, pdf(js="app.alert(1)"), "application/pdf")
    while analizar(motor_app):  # cada lote toma un trabajo; el rechazo encola el borrado
        pass
    en_cuarentena = version(acta, JPEG, "image/jpeg")  # sin analizar
    with motor_migrate.connect() as c:
        estados = dict(c.execute(
            text("select id::text, estado from nucleo.version_documento where consorcio_id = :c"),
            {"c": e["cons"]},
        ).all())  # fmt: skip
    assert estados == {**{v: "disponible" for v in binarios}, rechazada: "rechazado",
                       en_cuarentena: "en_cuarentena"}  # fmt: skip
    return {"binarios": binarios, "rechazada": rechazada, "en_cuarentena": en_cuarentena,
            "documentos": {acta, otros, malo}}  # fmt: skip


def _lo_que_nunca_va(e, motor_migrate) -> dict:
    # Una negación de verdad, por la API: la propietaria no carga documentos.
    assert crear(e["prop"], e["cons"]).status_code == 403
    token = os.urandom(32)
    with motor_migrate.begin() as c:
        evento = c.execute(
            text("insert into auditoria.eventos (consorcio_id, actor_persona_id, origen, accion,"
                 " entidad, entidad_id, despues, ip, hash_previo, hash) values (:c, :p, 'api',"
                 " 'consorcio.decision.registrar', 'decision', :d, '{\"tipo\": \"asamblea\"}',"
                 " :ip, '', '') returning id"),
            {"c": e["cons"], "p": e["ad"], "d": e["acta"], "ip": IP},
        ).scalar_one()  # fmt: skip
        c.execute(text("insert into nucleo.contrasena (persona_id, hash) values (:p, :h)"),
                  {"p": e["ad"], "h": HASH_CONTRASENA})  # fmt: skip
        c.execute(
            text("insert into nucleo.sesion (persona_id, token_hash, dispositivo, ip, vence_en)"
                 " values (:p, :t, :d, :ip, now() + interval '1 day')"),
            {"p": e["ad"], "t": token, "d": uuid4(), "ip": IP},
        )  # fmt: skip
        c.execute(
            text("insert into nucleo.contacto_emergencia (consorcio_id, funcion, naturaleza,"
                 " nombre, telefono, consentimiento_referencia, vigente_desde) values (:c,"
                 " 'emergencias', 'profesional', 'Plomería Rápida', '+54 11 4000-0000', :r,"
                 " '2026-01-01')"),
            {"c": e["cons"], "r": CONSENTIMIENTO},
        )  # fmt: skip
    return {"evento_con_ip": evento, "token": token.hex()}


@pytest.fixture
def edificio(motor_migrate, motor_app, bucket):  # noqa: F811
    return _edificio(motor_migrate, motor_app)


@pytest.fixture
def con_url(monkeypatch):
    monkeypatch.setenv(exportar_consorcio.VARIABLE_URL, url_rol("soporte_exportacion"))


@pytest.fixture
def claves(tmp_path) -> dict:
    """Un par de claves `age` de prueba, generado en el momento."""
    privada = tmp_path / "destinatario.txt"
    hecho = subprocess.run(["age-keygen", "-o", str(privada)], capture_output=True, text=True)
    assert hecho.returncode == 0, hecho.stderr
    publica = next(linea.split(": ", 1)[1] for linea in privada.read_text().splitlines()
                   if linea.startswith("# public key: "))  # fmt: skip
    return {"privada": privada, "publica": publica}


def argumentos(e, claves, salida: Path, solicitante=None, **cambios) -> list[str]:
    valores = {
        "--consorcio": str(e["cons"]),
        "--referencia-solicitud": "Nota 3/2026 del Consejo",
        "--destinatario-nombre": "Estudio Nuevo SRL",
        "--destinatario-documento": "30712345671",
        "--solicitante": str(solicitante or e["mc"]),
        "--fecha-pedido": e["hoy"].isoformat(),
        "--referencia-respaldo": "Acta 45, libro 3, folio 12",
        "--clave-publica": claves["publica"],
        "--salida": str(salida),
    } | {f"--{k.replace('_', '-')}": v for k, v in cambios.items()}
    return ["armar"] + [x for k, v in valores.items() if v is not None for x in (k, v)]


def exportar(e, claves, tmp_path, solicitante=None, **cambios) -> tuple[int, Path]:
    salida = tmp_path / f"exportacion-{uuid4().hex[:8]}.zip.age"
    return exportar_consorcio.main(argumentos(e, claves, salida, solicitante, **cambios)), salida


def descifrar(salida: Path, privada: Path) -> zipfile.ZipFile:
    hecho = subprocess.run(["age", "--decrypt", "--identity", str(privada), str(salida)],
                           capture_output=True)  # fmt: skip
    assert hecho.returncode == 0, hecho.stderr
    return zipfile.ZipFile(io.BytesIO(hecho.stdout))


def entidades(paquete: zipfile.ZipFile) -> dict[str, list[dict]]:
    return {
        Path(nombre).stem: json.loads(paquete.read(nombre))
        for nombre in paquete.namelist()
        if nombre.endswith(".json") and nombre != exportar_consorcio.MANIFIESTO
    }


def constancias(motor_migrate, cons) -> list[dict]:
    with motor_migrate.connect() as c:
        return [dict(f) for f in c.execute(
            text("select * from plataforma.exportacion where consorcio_id = :c order by creada_en"),
            {"c": cons},
        ).mappings()]  # fmt: skip


def verificar_manifiesto(paquete: zipfile.ZipFile) -> list[str]:
    """Los archivos que no coinciden con el manifiesto (vacío si todo coincide)."""
    manifiesto = json.loads(paquete.read(exportar_consorcio.MANIFIESTO))
    listados = {a["ruta"]: a for a in manifiesto["archivos"]}
    problemas = sorted(set(paquete.namelist()) - set(listados) - {exportar_consorcio.MANIFIESTO})
    for ruta, a in listados.items():
        contenido = paquete.read(ruta) if ruta in paquete.namelist() else None
        if contenido is None or (exportar_consorcio.hashlib.sha256(contenido).hexdigest(),
                                 len(contenido)) != (a["sha256"], a["tamano"]):  # fmt: skip
            problemas.append(ruta)
    return problemas


# El paquete ---------------------------------------------------------------------------


def test_el_paquete_se_descifra_y_el_manifiesto_coincide(edificio, con_url, claves, tmp_path,
                                                         motor_migrate, capsys):  # fmt: skip
    e = edificio
    codigo, salida = exportar(e, claves, tmp_path)
    assert codigo == 0, capsys.readouterr().err
    impreso = capsys.readouterr().out
    paquete = descifrar(salida, claves["privada"])
    assert verificar_manifiesto(paquete) == []
    # Control: la verificación encuentra un archivo alterado, y otra clave privada no descifra.
    alterado = io.BytesIO()
    with zipfile.ZipFile(alterado, "w") as z:
        for nombre in paquete.namelist():
            contenido = paquete.read(nombre)
            z.writestr(nombre, contenido + b" " if nombre == "padron/unidad.json" else contenido)
    assert verificar_manifiesto(zipfile.ZipFile(alterado)) == ["padron/unidad.json"]
    otra = tmp_path / "otra.txt"
    subprocess.run(["age-keygen", "-o", str(otra)], capture_output=True, check=True)
    ajena = subprocess.run(["age", "--decrypt", "--identity", str(otra), str(salida)],
                           capture_output=True)  # fmt: skip
    assert ajena.returncode != 0 and not ajena.stdout
    # Los binarios son los que se subieron.
    datos = entidades(paquete)
    for v in datos["version"]:
        assert paquete.read(v["archivo"]) == e["binarios"][v["id"]]
    # Cada fila trae solo las claves decididas, y cada entidad con filas está.
    for entidad, filas in datos.items():
        for fila in filas:
            assert set(fila) == CLAVES_POR_ENTIDAD[entidad], entidad
    assert {k for k, v in datos.items() if v} == set(CLAVES_POR_ENTIDAD) - {"revision"}
    # El padrón vuelve con los nombres.
    assert "Ana Exportada" in {v["nombre"] for v in datos["vinculo"]}
    # La constancia: el paquete cifrado que quedó en disco, con su suma y su tamaño.
    [fila] = constancias(motor_migrate, e["cons"])
    suma, tamano = exportar_consorcio._sha256(salida)
    assert (fila["paquete_sha256"], fila["paquete_tamano"]) == (suma, tamano)
    assert (fila["solicitante"], fila["mandato_id"], fila["fecha_pedido"]) == (
        e["mc"], e["m_mc"], e["hoy"])  # fmt: skip
    assert (fila["destinatario_nombre"], fila["destinatario_documento"]) == (
        "Estudio Nuevo SRL", "30712345671")  # fmt: skip
    assert fila["solicitud_referencia"] == "Nota 3/2026 del Consejo"
    assert fila["respaldo_referencia"] == "Acta 45, libro 3, folio 12"
    assert fila["facultades_verificadas"] == FACULTADES
    assert fila["armada_en"] is not None and fila["entregada_en"] is None
    assert fila["cantidades"] == {k: len(v) for k, v in datos.items()} | {"archivos": 2}
    assert f"exportacion: {fila['id']}" in impreso and f"sha256: {suma}" in impreso


def _textos_del_paquete(paquete: zipfile.ZipFile) -> str:
    return "\n".join(paquete.read(n).decode("latin-1") for n in paquete.namelist()).lower()


def _textos_de_la_base(motor_migrate, e) -> str:
    """Las mismas tablas de las que sale el paquete, fila entera como texto."""
    consultas = [
        "select x::text from auditoria.eventos x where x.consorcio_id = :c",
        "select x::text from nucleo.sesion x where x.persona_id = :ad",
        "select x::text from nucleo.contrasena x where x.persona_id = :ad",
        "select x::text from nucleo.contacto_emergencia x where x.consorcio_id = :c",
        "select x::text from nucleo.version_documento x where x.consorcio_id = :c",
    ]
    with motor_migrate.connect() as c:
        return "\n".join(
            t for q in consultas
            for t in c.execute(text(q), {"c": e["cons"], "ad": e["ad"]}).scalars()
        ).lower()  # fmt: skip


def _lo_que_nunca_va_buscado(motor_migrate, e) -> dict[str, list[str]]:
    with motor_migrate.connect() as c:
        hashes = c.execute(
            text("select hash from auditoria.eventos where consorcio_id = :c union"
                 " select hash_previo from auditoria.eventos where consorcio_id = :c"
                 " and hash_previo <> ''"), {"c": e["cons"]},
        ).scalars().all()  # fmt: skip
        objetos = c.execute(
            text("select clave_objeto from nucleo.version_documento where consorcio_id = :c"
                 " and clave_objeto is not null"), {"c": e["cons"]},
        ).scalars().all()  # fmt: skip
    assert len(hashes) > 5 and len(objetos) >= 3  # la búsqueda tiene qué buscar
    return {
        "ip": [IP],
        "hashes de la cadena": hashes,
        "hash de la contraseña": [HASH_CONTRASENA.lower()],
        "token de la sesión": [e["token"]],
        "referencia del consentimiento": [CONSENTIMIENTO.lower()],
        "claves de los objetos": objetos,
        "antivirus": ["clamav"],
    }


def _encontrados(buscado: dict[str, list[str]], textos: str) -> dict[str, int]:
    return {que: sum(t.lower() in textos for t in valores) for que, valores in buscado.items()}


def test_lo_que_nunca_va_no_esta_en_el_paquete_y_si_en_la_base(
    edificio, con_url, claves, tmp_path, motor_migrate
):
    e = edificio
    codigo, salida = exportar(e, claves, tmp_path)
    assert codigo == 0
    buscado = _lo_que_nunca_va_buscado(motor_migrate, e)
    en_el_paquete = _encontrados(buscado, _textos_del_paquete(descifrar(salida, claves["privada"])))
    assert en_el_paquete == {que: 0 for que in buscado}
    # Control: la misma búsqueda, en la base de la que salió, los encuentra todos.
    en_la_base = _encontrados(buscado, _textos_de_la_base(motor_migrate, e))
    assert en_la_base == {que: len(valores) for que, valores in buscado.items()}
    # Y el evento que tenía la IP sí va, sin ella, por la proyección.
    datos = entidades(descifrar(salida, claves["privada"]))
    assert any(ev["accion"] == "Registró una decisión" and ev["actor"] == "persona"
               for ev in datos["evento"]), datos["evento"]  # fmt: skip


PERSONAS_DEL_CONSORCIO = """
    select x::text, p from (
      select v::text as x, v.persona_id as p from nucleo.vinculo v where v.consorcio_id = :c
      union all select m::text, m.persona_id from nucleo.mandato m where m.consorcio_id = :c
      union all select d::text, d.registrada_por from nucleo.decision_formal d
                 where d.consorcio_id = :c
      union all select e::text, e.actor_persona_id from auditoria.eventos e
                 where e.consorcio_id = :c
      union all select d::text, d.cargado_por from nucleo.documento d where d.consorcio_id = :c
      union all select v::text, v.subida_por from nucleo.version_documento v
                 where v.consorcio_id = :c
      union all select k::text, k.actualizado_por from nucleo.contacto_emergencia k
                 where k.consorcio_id = :c
      union all select k::text, k.verificado_por from nucleo.contacto_emergencia k
                 where k.consorcio_id = :c
      union all select k::text, k.observado_por from nucleo.contacto_emergencia k
                 where k.consorcio_id = :c
      union all select m::text, m.registrado_por from nucleo.mandato m where m.consorcio_id = :c
      union all select r::text, r.registrada_por from nucleo.revision_mandato r
                 where r.consorcio_id = :c
    ) t (x, p) where p is not null
"""


def test_ningun_identificador_de_persona_va_en_el_paquete(edificio, con_url, claves, tmp_path,
                                                         motor_migrate):  # fmt: skip
    """La API no muestra el identificador de las personas (salvo a quien da un alta): cruzado
    entre consorcios, identifica a la misma cuenta. Ninguno de los que nombran las tablas de las
    que sale el paquete va en él."""
    e = edificio
    codigo, salida = exportar(e, claves, tmp_path)
    assert codigo == 0
    with motor_migrate.connect() as c:
        filas = c.execute(text(PERSONAS_DEL_CONSORCIO), {"c": e["cons"]}).all()
    personas = {str(f.p) for f in filas}
    # La búsqueda tiene qué buscar: la decisión que registró la administradora, entre otros.
    assert {str(e["ad"]), str(e["mc"]), str(e["prop"])} <= personas
    paquete = _textos_del_paquete(descifrar(salida, claves["privada"]))
    assert {p for p in personas if p in paquete} == set()
    # Control: la misma búsqueda, en las filas de la base que los nombran, los encuentra todos.
    base = " ".join(f.x for f in filas)
    assert {p for p in personas if p in base} == personas


def test_la_auditoria_lleva_exactamente_los_eventos_proyectados(
    edificio, con_url, claves, tmp_path, motor_migrate
):
    """El paquete muestra el texto de la acción, no su código: buscar `autorizacion.denegada` en
    él no puede fallar. Esto sí: los eventos del paquete son, uno por uno y en orden, los de la
    base con una acción de la proyección y que no son de soporte; la negación sembrada y los
    eventos de soporte quedan afuera."""
    e = edificio
    codigo, salida = exportar(e, claves, tmp_path)
    assert codigo == 0
    del_paquete = [(datetime.fromisoformat(ev["fecha"]), ev["accion"])
                   for ev in entidades(descifrar(salida, claves["privada"]))["evento"]]  # fmt: skip
    with motor_migrate.connect() as c:
        proyectados = [tuple(f) for f in c.execute(
            text("select e.creado_en, p.texto from auditoria.eventos e"
                 " join auditoria.proyeccion p on p.accion = e.accion"
                 " where e.consorcio_id = :c and e.origen <> 'soporte'"
                 " order by e.creado_en, e.secuencia"), {"c": e["cons"]},
        )]  # fmt: skip
        por_tipo = dict(c.execute(
            text("select case when e.accion = 'autorizacion.denegada' then 'negacion'"
                 " when e.origen = 'soporte' then 'soporte' else 'otro' end, count(*)"
                 " from auditoria.eventos e where e.consorcio_id = :c group by 1"),
            {"c": e["cons"]},
        ).all())  # fmt: skip
    # Control: hay qué dejar afuera (la negación de la propietaria y lo que cargó el equipo).
    assert por_tipo["negacion"] >= 1 and por_tipo["soporte"] >= 1
    assert len(proyectados) < sum(por_tipo.values())
    assert del_paquete == proyectados
    # Y ninguno es una negación, aunque la proyección llegara a incluirla: ningún evento
    # `autorizacion.denegada` de la base tiene la fecha de un evento del paquete.
    with motor_migrate.connect() as c:
        negaciones = set(c.execute(
            text("select creado_en from auditoria.eventos where consorcio_id = :c"
                 " and accion = 'autorizacion.denegada'"), {"c": e["cons"]},
        ).scalars())  # fmt: skip
    assert negaciones and not negaciones & {fecha for fecha, _ in del_paquete}


def test_las_versiones_en_cuarentena_o_rechazadas_no_van(edificio, con_url, claves, tmp_path):
    e = edificio
    codigo, salida = exportar(e, claves, tmp_path)
    assert codigo == 0
    paquete = descifrar(salida, claves["privada"])
    versiones = {v["id"] for v in entidades(paquete)["version"]}
    archivos = " ".join(paquete.namelist())
    for fuera in (e["rechazada"], e["en_cuarentena"]):
        assert fuera not in versiones and fuera not in archivos
    # Control: las `disponible` sí, con su binario.
    assert versiones == set(e["binarios"])
    assert all(v in archivos for v in e["binarios"])


def test_exportar_no_escribe_eventos_de_auditoria(edificio, con_url, claves, tmp_path,
                                                  motor_migrate):  # fmt: skip
    e = edificio
    contar = "select count(*) from auditoria.eventos where consorcio_id = :c"
    # Control: la consulta cuenta un evento que sí existe (el sembrado con la IP).
    with motor_migrate.connect() as c:
        antes = c.execute(text(contar), {"c": e["cons"]}).scalar_one()
        assert c.execute(text(contar + " and id = :e"),
                         {"c": e["cons"], "e": e["evento_con_ip"]}).scalar_one() == 1  # fmt: skip
        ultimo = c.execute(text("select max(secuencia) from auditoria.eventos")).scalar_one()
    assert antes > 0
    assert exportar(e, claves, tmp_path)[0] == 0
    with motor_migrate.connect() as c:
        assert c.execute(text(contar), {"c": e["cons"]}).scalar_one() == antes
        # En ningún consorcio: soporte no escribe en la auditoría.
        assert c.execute(text("select count(*) from auditoria.eventos where secuencia > :s"),
                         {"s": ultimo}).scalar_one() == 0  # fmt: skip


# Quién la puede pedir -----------------------------------------------------------------


@pytest.mark.parametrize(
    "caso,cambios",
    [
        ("sin destinatario", {"destinatario_nombre": "  "}),
        ("sin documento del destinatario", {"destinatario_documento": ""}),
        ("sin referencia de la solicitud", {"referencia_solicitud": " "}),
        ("sin referencia del respaldo", {"referencia_respaldo": ""}),
        ("solicitante sin mandato", {"solicitante": "prop"}),
        ("consejo limitado a padron.ver", {"solicitante": "mc_padron"}),
        ("padron.ver acotado a una unidad", {"solicitante": "mc_acotado"}),
        ("antes del mandato", {"fecha_pedido": "2025-12-31"}),
    ],
)
def test_se_niega_sin_escribir_nada(edificio, con_url, claves, tmp_path, motor_migrate, capsys,
                                    caso, cambios):  # fmt: skip
    e = edificio
    if "solicitante" in cambios:
        cambios = cambios | {"solicitante": str(e[cambios["solicitante"]])}
    codigo, salida = exportar(e, claves, tmp_path, **cambios)
    assert codigo == 1, caso
    assert "no se escribió nada" in capsys.readouterr().err
    assert not salida.exists() and constancias(motor_migrate, e["cons"]) == []
    # Control: el mismo pedido de un miembro del Consejo con el paquete completo pasa.
    assert exportar(e, claves, tmp_path)[0] == 0
    assert len(constancias(motor_migrate, e["cons"])) == 1


@pytest.mark.parametrize("falta", ["--destinatario-nombre", "--destinatario-documento",
                                   "--referencia-solicitud", "--referencia-respaldo"])  # fmt: skip
def test_sin_el_argumento_no_corre(edificio, con_url, claves, tmp_path, motor_migrate, falta):
    e = edificio
    salida = tmp_path / "x.zip.age"
    a = argumentos(e, claves, salida)
    i = a.index(falta)
    with pytest.raises(SystemExit) as fin:
        exportar_consorcio.main(a[:i] + a[i + 2 :])
    assert fin.value.code == 2
    assert not salida.exists() and constancias(motor_migrate, e["cons"]) == []
    assert exportar_consorcio.main(a) == 0  # control


def test_la_administracion_tambien_la_pide(edificio, con_url, claves, tmp_path, motor_migrate):
    e = edificio
    assert exportar(e, claves, tmp_path, solicitante=str(e["ad"]))[0] == 0
    assert constancias(motor_migrate, e["cons"])[0]["mandato_id"] == e["m_ad"]


def test_una_clave_publica_que_age_no_acepta_se_niega(edificio, con_url, claves, tmp_path,
                                                      motor_migrate, capsys):  # fmt: skip
    e = edificio
    codigo, salida = exportar(e, claves, tmp_path, clave_publica="age1noesunaclave")
    assert codigo == 1 and "age" in capsys.readouterr().err
    assert not salida.exists() and constancias(motor_migrate, e["cons"]) == []


def test_no_sobrescribe_un_archivo(edificio, con_url, claves, tmp_path, motor_migrate, capsys):
    e = edificio
    salida = tmp_path / "ya.zip.age"
    salida.write_bytes(b"otro")
    assert exportar_consorcio.main(argumentos(e, claves, salida)) == 1
    assert "ya existe" in capsys.readouterr().err
    assert salida.read_bytes() == b"otro" and constancias(motor_migrate, e["cons"]) == []


def test_un_archivo_que_aparece_en_el_medio_no_se_toca(
    edificio, con_url, claves, tmp_path, motor_migrate, monkeypatch, capsys
):
    """Otro proceso crea el archivo de salida justo antes de que el script lo abra: se niega, el
    archivo ajeno queda intacto y no hay constancia."""
    e = edificio
    salida = tmp_path / "carrera.zip.age"
    original = exportar_consorcio._exigir_clave_age

    def y_aparece(clave):
        original(clave)
        salida.write_bytes(b"de otro proceso")

    monkeypatch.setattr(exportar_consorcio, "_exigir_clave_age", y_aparece)
    assert exportar_consorcio.main(argumentos(e, claves, salida)) == 1
    assert "ya existe" in capsys.readouterr().err
    assert salida.read_bytes() == b"de otro proceso"
    assert constancias(motor_migrate, e["cons"]) == []
    # Control: sin el otro proceso, la misma salida (libre) se arma.
    salida.unlink()
    monkeypatch.setattr(exportar_consorcio, "_exigir_clave_age", original)
    assert exportar_consorcio.main(argumentos(e, claves, salida)) == 0
    assert salida.read_bytes().startswith(b"age-encryption.org/v1")


def test_si_el_commit_falla_no_queda_el_paquete(edificio, con_url, claves, tmp_path,
                                                motor_migrate, monkeypatch, capsys):  # fmt: skip
    """La transacción falla al terminar, después de armar y de registrar el armado: no queda la
    fila, y tampoco el archivo cifrado (se da por bueno recién después del commit)."""
    e = edificio
    original = exportar_consorcio.transaccion

    @contextmanager
    def falla_al_confirmar(motor, **ambito):
        with original(motor, **ambito) as conn:
            yield conn
            conn.execute(text("select 1 / 0"))  # una falla de la base al cerrar la transacción

    monkeypatch.setattr(exportar_consorcio, "transaccion", falla_al_confirmar)
    codigo, salida = exportar(e, claves, tmp_path)
    assert codigo == 1 and "no se escribió nada" in capsys.readouterr().err
    assert not salida.exists() and constancias(motor_migrate, e["cons"]) == []
    # Control: sin la falla, el mismo pedido deja el archivo y la fila.
    monkeypatch.setattr(exportar_consorcio, "transaccion", original)
    codigo, salida = exportar(e, claves, tmp_path)
    assert codigo == 0 and salida.exists() and len(constancias(motor_migrate, e["cons"])) == 1


def test_un_binario_que_no_coincide_no_deja_paquete_ni_constancia(edificio, con_url, claves,
                                                                  tmp_path, motor_migrate,
                                                                  bucket):  # noqa: F811  # fmt: skip
    e = edificio
    v = next(iter(e["binarios"]))
    with motor_migrate.connect() as c:
        clave = c.execute(text("select clave_objeto from nucleo.version_documento where id = :v"),
                          {"v": v}).scalar_one()  # fmt: skip
    _s3().put_object(Bucket=bucket, Key=clave, Body=b"%PDF-otro contenido")
    salida = tmp_path / "x.zip.age"
    with pytest.raises(exportar_consorcio.ArchivoQueNoCoincide):
        exportar_consorcio.main(argumentos(e, claves, salida))
    assert not salida.exists() and constancias(motor_migrate, e["cons"]) == []


def test_con_la_url_de_otro_rol_se_niega(edificio, monkeypatch, claves, tmp_path, motor_migrate,
                                         capsys):  # fmt: skip
    e = edificio
    monkeypatch.setenv(exportar_consorcio.VARIABLE_URL, url_rol("app"))
    codigo, salida = exportar(e, claves, tmp_path)
    assert codigo == 1 and "conecta como cem_app" in capsys.readouterr().err
    assert not salida.exists() and constancias(motor_migrate, e["cons"]) == []


def test_sin_la_variable_no_corre(edificio, monkeypatch, claves, tmp_path, capsys):
    monkeypatch.delenv(exportar_consorcio.VARIABLE_URL, raising=False)
    assert exportar(edificio, claves, tmp_path)[0] == 2
    assert exportar_consorcio.VARIABLE_URL in capsys.readouterr().err


# La entrega ---------------------------------------------------------------------------


def test_la_entrega_se_registra_una_vez(edificio, con_url, claves, tmp_path, motor_migrate,
                                        capsys):  # fmt: skip
    e = edificio
    codigo, salida = exportar(e, claves, tmp_path)
    assert codigo == 0
    [fila] = constancias(motor_migrate, e["cons"])
    # Otro archivo (otro SHA-256) no se da por entregado.
    otro_archivo = tmp_path / "otro.zip.age"
    otro_archivo.write_bytes(b"no es el paquete")
    entregar = ["entregar", "--consorcio", str(e["cons"]), "--exportacion", str(fila["id"])]
    assert exportar_consorcio.main(entregar + ["--paquete", str(otro_archivo)]) == 1
    assert "otro SHA-256" in capsys.readouterr().err
    assert exportar_consorcio.main(entregar + ["--paquete", str(tmp_path / "no-esta")]) == 1
    assert "no existe o no es un archivo" in capsys.readouterr().err
    assert constancias(motor_migrate, e["cons"])[0]["entregada_en"] is None
    entregar += ["--paquete", str(salida)]
    assert exportar_consorcio.main(entregar) == 0
    assert f"sha256: {fila['paquete_sha256']}" in capsys.readouterr().out
    entregada = constancias(motor_migrate, e["cons"])[0]["entregada_en"]
    assert entregada is not None
    assert exportar_consorcio.main(entregar) == 1
    assert "entrega_registrada" in capsys.readouterr().err
    assert constancias(motor_migrate, e["cons"])[0]["entregada_en"] == entregada
    # Una exportación que no existe en el consorcio del contexto.
    assert exportar_consorcio.main(entregar[:3] + ["--exportacion", str(uuid4()),
                                                   "--paquete", str(salida)]) == 1  # fmt: skip
    otro = consorcio_con_persona(motor_migrate)[0]
    assert exportar_consorcio.main(["entregar", "--consorcio", str(otro), "--exportacion",
                                    str(fila["id"]), "--paquete", str(salida)]) == 1  # fmt: skip
    assert "no existe en este consorcio" in capsys.readouterr().err


def test_el_armado_registrado_no_cambia(edificio, con_url, claves, tmp_path, motor_migrate):
    e = edificio
    assert exportar(e, claves, tmp_path)[0] == 0
    [fila] = constancias(motor_migrate, e["cons"])
    with pytest.raises(DBAPIError) as error:
        _como_soporte(e["cons"], "update plataforma.exportacion set paquete_sha256 = :s"
                                 " where id = :id", {"s": "0" * 64, "id": fila["id"]})  # fmt: skip
    assert error.value.orig.diag.message_hint == "armado_registrado"
    # Ni las columnas del pedido: el rol no tiene `UPDATE` sobre ellas.
    with pytest.raises(DBAPIError) as error:
        _como_soporte(e["cons"], "update plataforma.exportacion set destinatario_nombre = 'X'"
                                 " where id = :id", {"id": fila["id"]})  # fmt: skip
    assert error.value.orig.sqlstate == "42501"
    # Control: la entrega, que todavía no está, sí se registra.
    r = _como_soporte(e["cons"], "update plataforma.exportacion set entregada_en = now()"
                                 " where id = :id", {"id": fila["id"]})  # fmt: skip
    assert r.rowcount == 1


# El rol y las funciones ---------------------------------------------------------------


def _como_soporte(cons, sql, parametros=None):
    motor = create_engine(url_rol("soporte_exportacion"))
    try:
        with transaccion(motor, persona_id=None, consorcio_id=cons) as c:
            return c.execute(text(sql), parametros or {})
    finally:
        motor.dispose()


@pytest.fixture
def sin_documentos(motor_migrate, motor_app):
    return _edificio(motor_migrate, motor_app, con_documentos=False)


REGISTRAR = (
    "select * from nucleo.registrar_exportacion(:c, 'Nota 1', 'Estudio', '30712345671',"
    " :p, :h, 'Acta 1')"
)


def pedido(e, solicitante=None, cons=None) -> object:
    """Una constancia registrada por `registrar_exportacion`, como el rol desde la base, en su
    propia transacción (confirmada)."""
    cons = cons or e["cons"]
    return _como_soporte(cons, REGISTRAR, {"c": cons, "p": solicitante or e["mc"],
                                           "h": e["hoy"]}).one()  # fmt: skip


def _registrar_en(c, e, solicitante=None):
    """Registra en la transacción `c`, que tiene que tener el consorcio de `e` en el contexto."""
    return c.execute(text(REGISTRAR), {"c": e["cons"], "p": solicitante or e["mc"],
                                       "h": e["hoy"]}).one()  # fmt: skip


def _en_una_transaccion(cons, pasos):
    """Corre `pasos(c)` como el rol, en una sola transacción con `cons` en el contexto."""
    motor = create_engine(url_rol("soporte_exportacion"))
    try:
        with transaccion(motor, persona_id=None, consorcio_id=cons) as c:
            return pasos(c)
    finally:
        motor.dispose()


@pytest.mark.parametrize("funcion", FUNCIONES)
def test_cada_funcion_exige_el_rol_el_consorcio_y_una_exportacion_sin_armar(
    sin_documentos, motor_migrate, motor_app, funcion
):
    """Con la URL del rol no alcanza para leer un consorcio desde la base: cada función exige una
    constancia del consorcio del contexto, registrada (con la capacidad comprobada) en la misma
    transacción y todavía sin armar. Cada caso registra la suya en su transacción, para que lo
    único que falte sea lo que prueba."""
    e = sin_documentos
    otro = _edificio(motor_migrate, motor_app, con_documentos=False)
    llamada = text(f"select * from nucleo.{funcion}(:c, :e)")
    with (
        pytest.raises(DBAPIError) as error,
        transaccion(motor_app, persona_id=e["mc"], consorcio_id=e["cons"]) as c,
    ):
        c.execute(llamada, {"c": e["cons"], "e": uuid4()})
    assert error.value.orig.sqlstate == "42501"  # cem_app, aun con el contexto correcto

    def con_contexto(contexto):
        def pasos(c):
            propio = _registrar_en(c, e).id
            fijar_ambito(c, persona_id=None, consorcio_id=contexto)
            return c.execute(llamada, {"c": e["cons"], "e": propio}).all()

        return pasos

    def con_pedido(elegir):
        def pasos(c):
            propio = _registrar_en(c, e).id
            return c.execute(llamada, {"c": e["cons"], "e": elegir(c, propio)}).all()

        return pasos

    def de_otro_consorcio(c, _):
        fijar_ambito(c, persona_id=None, consorcio_id=otro["cons"])
        suyo = _registrar_en(c, otro).id
        fijar_ambito(c, persona_id=None, consorcio_id=e["cons"])
        return suyo

    def ya_armada(c, propio):
        c.execute(text("update plataforma.exportacion set armada_en = clock_timestamp(),"
                       " paquete_sha256 = repeat('0', 64), paquete_tamano = 1,"
                       " cantidades = '{}' where id = :e"), {"e": propio})  # fmt: skip
        return propio

    casos = {
        "sin consorcio en el contexto": con_contexto(None),
        "con otro consorcio en el contexto": con_contexto(otro["cons"]),
        "sin exportación": con_pedido(lambda c, _: None),
        "una que no existe": con_pedido(lambda c, _: uuid4()),
        "la de otro consorcio": con_pedido(de_otro_consorcio),
        "una ya armada": con_pedido(ya_armada),
    }
    for caso, pasos in casos.items():
        with pytest.raises(DBAPIError) as error:
            _en_una_transaccion(e["cons"], pasos)
        assert error.value.orig.sqlstate == "42501", caso
    # Control: con la recién registrada, en la misma transacción, responde.
    _en_una_transaccion(e["cons"], con_pedido(lambda c, propio: propio))


@pytest.mark.parametrize("funcion", FUNCIONES)
def test_una_exportacion_confirmada_no_se_lee_en_otra_transaccion(sin_documentos, funcion):
    """Registrada y confirmada, la constancia no abre las lecturas en otra transacción: si no,
    con la URL del rol se leería el consorcio cuantas veces se quisiera, sin armar nunca."""
    e = sin_documentos
    llamada = text(f"select * from nucleo.{funcion}(:c, :e)")
    confirmada = pedido(e).id
    with pytest.raises(DBAPIError) as error:
        _en_una_transaccion(
            e["cons"], lambda c: c.execute(llamada, {"c": e["cons"], "e": confirmada}).all()
        )
    assert error.value.orig.sqlstate == "42501"

    # Control: registrada en la misma transacción, responde.
    def en_la_misma(c):
        return c.execute(llamada, {"c": e["cons"], "e": _registrar_en(c, e).id}).all()

    _en_una_transaccion(e["cons"], en_la_misma)


def test_con_el_contexto_correcto_el_padron_vuelve_con_nombres(sin_documentos):
    e = sin_documentos
    filas = _en_una_transaccion(e["cons"], lambda c: c.execute(
        text("select entidad, fila from nucleo.exportar_padron(:c, :e)"),
        {"c": e["cons"], "e": _registrar_en(c, e).id}).all())  # fmt: skip
    nombres = {f.fila["nombre"] for f in filas if f.entidad == "vinculo"}
    assert {"Ana Exportada", "Marta Consejera"} <= nombres
    assert {f.entidad for f in filas} == {"consorcio", "unidad", "vinculo"}


def test_registrar_exportacion_fija_la_capacidad_adentro(sin_documentos, motor_migrate):
    """Quien llama no elige las facultades ni los tipos: un solicitante sin la capacidad no deja
    constancia. Control: el Consejo con el paquete completo sí, con las cinco verificadas."""
    e = sin_documentos
    for quien in ("prop", "mc_padron", "mc_acotado"):
        with pytest.raises(DBAPIError) as error:
            pedido(e, e[quien])
        assert error.value.orig.diag.message_hint == "sin_capacidad", quien
    assert constancias(motor_migrate, e["cons"]) == []
    fila = pedido(e)
    assert fila.mandato_id == e["m_mc"]
    [constancia] = constancias(motor_migrate, e["cons"])
    assert constancia["facultades_verificadas"] == FACULTADES
    assert constancia["armada_en"] is None and constancia["entregada_en"] is None


@pytest.mark.parametrize(
    "sql",
    [
        "select count(*) from nucleo.persona",
        "select count(*) from nucleo.vinculo",
        "select count(*) from nucleo.documento",
        "select count(*) from nucleo.mandato",
        "select count(*) from auditoria.eventos",
        "select count(*) from nucleo.sesion",
        "delete from plataforma.exportacion",
        # `creada_en` decide qué exportación habilita las lecturas: el rol no la puede tocar.
        "update plataforma.exportacion set creada_en = now()",
        "insert into plataforma.exportacion (consorcio_id, solicitud_referencia,"
        " destinatario_nombre, destinatario_documento, solicitante, mandato_id,"
        " facultades_verificadas, fecha_pedido, respaldo_referencia, armada_en, paquete_sha256,"
        " paquete_tamano, cantidades) select consorcio_id, 'r', 'd', '1', solicitante, mandato_id,"
        " facultades_verificadas, fecha_pedido, 'r', now(), repeat('0', 64), 1, '{}'"
        " from plataforma.exportacion",
        "insert into auditoria.eventos (consorcio_id, origen, accion, entidad, hash_previo, hash)"
        " values (gen_random_uuid(), 'soporte', 'x', 'x', '', '')",
    ],
)
def test_el_rol_no_lee_tablas_ni_escribe_mas_que_la_constancia(sin_documentos, sql):
    e = sin_documentos
    with pytest.raises(DBAPIError) as error:
        _como_soporte(e["cons"], sql)
    assert error.value.orig.sqlstate == "42501"
    # Control: la constancia sí la lee.
    assert _como_soporte(e["cons"], "select count(*) from plataforma.exportacion").scalar_one() == 0


def test_la_constancia_no_la_lee_cem_app_y_el_rol_ve_solo_su_consorcio(edificio, con_url, claves,
                                                                      tmp_path, motor_app,
                                                                      motor_migrate):  # fmt: skip
    e = edificio
    assert exportar(e, claves, tmp_path)[0] == 0
    with (
        pytest.raises(DBAPIError) as error,
        transaccion(motor_app, persona_id=e["mc"], consorcio_id=e["cons"]) as c,
    ):
        c.execute(text("select count(*) from plataforma.exportacion"))
    assert error.value.orig.sqlstate == "42501"
    contar = "select count(*) from plataforma.exportacion"
    otro = consorcio_con_persona(motor_migrate)[0]
    assert _como_soporte(None, contar).scalar_one() == 0
    assert _como_soporte(otro, contar).scalar_one() == 0
    assert _como_soporte(e["cons"], contar).scalar_one() == 1  # control
    # Ni escribir una constancia de otro consorcio.
    with pytest.raises(DBAPIError) as error:
        _como_soporte(otro, "insert into plataforma.exportacion (consorcio_id,"
                            " solicitud_referencia, destinatario_nombre, destinatario_documento,"
                            " solicitante, mandato_id, facultades_verificadas, fecha_pedido,"
                            " respaldo_referencia) values (:c, 'r', 'd', '1', :p, :m, '{x}',"
                            " current_date, 'r')",
                      {"c": e["cons"], "p": e["mc"], "m": e["m_mc"]})  # fmt: skip
    assert error.value.orig.sqlstate == "42501"


def test_de_las_funciones_security_definer_el_rol_ejecuta_solo_las_suyas(motor_migrate):
    consulta = (
        "select p.oid::regprocedure::text from pg_proc p"
        " join pg_namespace n on n.oid = p.pronamespace"
        " where n.nspname in ('nucleo', 'plataforma', 'auditoria') and p.prosecdef"
        " and has_function_privilege(:rol, p.oid, 'execute')"
    )
    with motor_migrate.connect() as c:
        del_rol = set(c.execute(text(consulta), {"rol": "cem_soporte_exportacion"}).scalars())
        de_app = set(c.execute(text(consulta), {"rol": "cem_app"}).scalars())
        de_documentos = set(c.execute(text(consulta),
                                      {"rol": "cem_soporte_documentos"}).scalars())  # fmt: skip
    assert len(de_app) > 10  # control: la búsqueda encuentra las de cem_app
    assert del_rol == {f"nucleo.{f}(uuid,uuid)" for f in FUNCIONES} | {
        "nucleo.registrar_exportacion(uuid,text,text,text,uuid,date,text)"}  # fmt: skip
    assert not del_rol & de_app
    assert de_documentos == {"nucleo.capacidad_del_solicitante(uuid,uuid,text[],date)"}
    # La capacidad interna no la ejecuta nadie más que su dueño.
    with motor_migrate.connect() as c:
        quienes = c.execute(
            text("select r.rolname from pg_roles r where r.rolname like 'cem\\_%' and"
                 " has_function_privilege(r.oid,"
                 " 'nucleo.capacidad_para_exportar(uuid,uuid,date)', 'execute')"),
        ).scalars().all()  # fmt: skip
    assert set(quienes) == {"cem_migrate"}


def test_crear_roles_sin_la_clave_nueva_no_crea_el_rol_y_lo_dice():
    from sqlalchemy.engine import make_url

    from tests.conftest import ADMIN_URL, BASE, CLAVES, RAIZ

    libpq = make_url(ADMIN_URL).set(drivername="postgresql").render_as_string(False)
    comun = ["psql", libpq, "-q", "-v", f"base={BASE}",
             "-v", f"clave_migrate={CLAVES['migrate']}", "-v", f"clave_app={CLAVES['app']}",
             "-v", f"clave_ro={CLAVES['ro']}", "-v", f"clave_backup={CLAVES['backup']}",
             "-v", f"clave_soporte_documentos={CLAVES['soporte_documentos']}",
             "-f", str(RAIZ / "scripts" / "crear_roles.sql")]  # fmt: skip
    hecho = subprocess.run(comun, capture_output=True, text=True)
    assert hecho.returncode == 0, hecho.stderr
    assert "cem_soporte_exportacion no se crea" in hecho.stdout
    # La de la copia continua (2c-4) va también: sin ella, su rol tampoco se crea y lo dice.
    con = comun[:-2] + ["-v", f"clave_soporte_exportacion={CLAVES['soporte_exportacion']}",
                        "-v", f"clave_copia_continua={CLAVES['copia_continua']}",
                        *comun[-2:]]  # fmt: skip
    hecho = subprocess.run(con, capture_output=True, text=True)
    assert hecho.returncode == 0 and "no se crea" not in hecho.stdout, hecho.stderr  # control
    # El rol quedó como lo pide el diseño (§1.1).
    motor = create_engine(ADMIN_URL)
    try:
        with motor.connect() as c:
            rol = c.execute(
                text("select rolbypassrls, rolsuper, rolcreaterole, rolcreatedb, rolreplication,"
                     " rolconnlimit, rolcanlogin from pg_roles"
                     " where rolname = 'cem_soporte_exportacion'"),
            ).one()  # fmt: skip
    finally:
        motor.dispose()
    assert tuple(rol) == (False, False, False, False, False, 2, True)


# Migración ----------------------------------------------------------------------------


def test_0038_ida_y_vuelta_y_se_niega_con_una_exportacion(base_aparte):  # noqa: F811
    cfg, motor = base_aparte
    command.upgrade(cfg, "0038")

    def funciones() -> int:
        with motor.connect() as c:
            return c.execute(
                text("select count(*) from pg_proc p join pg_namespace n"
                     " on n.oid = p.pronamespace where n.nspname = 'nucleo'"
                     " and p.proname like 'exportar\\_%'"),
            ).scalar_one()  # fmt: skip

    assert funciones() == len(FUNCIONES)
    command.downgrade(cfg, "0037")
    assert funciones() == 0
    command.upgrade(cfg, "0038")  # control: vuelve a subir limpio
    with motor.begin() as c:
        cons = c.execute(text("insert into nucleo.consorcio (nombre) values ('A') returning id"))
        cons = cons.scalar_one()
        dec = c.execute(
            text("insert into nucleo.decision_formal (consorcio_id, tipo, fecha, descripcion,"
                 " referencia_acta, origen) values (:c, 'asamblea', '2026-01-01', 'x', 'Acta',"
                 " 'soporte') returning id"), {"c": cons},
        ).scalar_one()  # fmt: skip
        per = c.execute(
            text("insert into nucleo.persona (correo, nombre) values ('x@e.com', 'X')"
                 " returning id")).scalar_one()  # fmt: skip
        m = c.execute(
            text("insert into nucleo.mandato (consorcio_id, tipo, persona_id, decision_id,"
                 " vigente_desde, origen) values (:c, 'administrador', :p, :d, '2026-01-01',"
                 " 'soporte') returning id"), {"c": cons, "p": per, "d": dec},
        ).scalar_one()  # fmt: skip
        c.execute(
            text("insert into plataforma.exportacion (consorcio_id, solicitud_referencia,"
                 " destinatario_nombre, destinatario_documento, solicitante, mandato_id,"
                 " facultades_verificadas, fecha_pedido, respaldo_referencia) values"
                 " (:c, 'r', 'd', '1', :p, :m, '{padron.ver}', '2026-01-02', 'r')"),
            {"c": cons, "p": per, "m": m},
        )  # fmt: skip
    with pytest.raises(DBAPIError) as error:
        command.downgrade(cfg, "0037")
    assert error.value.orig.diag.message_hint == "downgrade_con_datos"


def test_el_paquete_no_pasa_por_el_disco_en_claro(edificio, con_url, claves, tmp_path):
    """Lo único que el script escribe es el archivo cifrado: ningún otro archivo nuevo en la
    carpeta de salida ni en la temporal del sistema, y lo escrito no es un ZIP."""
    import tempfile

    e = edificio
    temporal = Path(tempfile.gettempdir())
    antes = {p for p in temporal.rglob("*") if p.is_file()}
    codigo, salida = exportar(e, claves, tmp_path)
    assert codigo == 0
    nuevos = {p for p in temporal.rglob("*") if p.is_file()} - antes
    assert {p for p in nuevos if not p.is_relative_to(tmp_path)} == set()
    assert {p.name for p in tmp_path.iterdir()} == {claves["privada"].name, salida.name}
    assert salida.read_bytes().startswith(b"age-encryption.org/v1")
    assert not zipfile.is_zipfile(salida)
    # Control: descifrado, es un ZIP.
    assert descifrar(salida, claves["privada"]).testzip() is None


def test_un_pedido_con_fecha_futura_se_niega(edificio, con_url, claves, tmp_path, motor_migrate,
                                             capsys):  # fmt: skip
    e = edificio
    codigo, _ = exportar(
        e, claves, tmp_path, fecha_pedido=(e["hoy"] + timedelta(days=2)).isoformat()
    )
    assert codigo == 1 and "fecha_futura" in capsys.readouterr().err
    assert constancias(motor_migrate, e["cons"]) == []


@pytest.fixture
def con_externo(motor_migrate, motor_app):
    return _edificio(motor_migrate, motor_app, con_documentos=False, externo=True)


def test_un_administrador_externo_con_las_cinco_se_niega(con_externo, con_url, claves, tmp_path,
                                                         motor_migrate, capsys):  # fmt: skip
    """§5 pide un mandato de administración o de Consejo: un administrador externo no alcanza
    aunque tenga las cinco facultades generales."""
    e = con_externo
    codigo, salida = exportar(e, claves, tmp_path, solicitante=str(e["ad"]))
    assert codigo == 1
    assert "de administración o de Consejo" in capsys.readouterr().err
    assert not salida.exists() and constancias(motor_migrate, e["cons"]) == []
    # Control: el mismo pedido del Consejo con el paquete completo, en el mismo consorcio, pasa.
    assert exportar(e, claves, tmp_path)[0] == 0
    assert constancias(motor_migrate, e["cons"])[0]["mandato_id"] == e["m_mc"]


def test_la_capacidad_para_exportar_solo_cuenta_administracion_o_consejo(con_externo, motor_app,
                                                                        motor_migrate):  # fmt: skip
    """Control de que el caso está bien sembrado: la capacidad de 0037, que no mira el tipo,
    sí encuentra el mandato del administrador externo (tiene las cinco, generales);
    `registrar_exportacion` lo niega. Y sí registra al Consejo y, en otro consorcio, a la
    administración."""
    e = con_externo
    with pytest.raises(DBAPIError) as error:
        pedido(e, e["ad"])
    assert error.value.orig.diag.message_hint == "sin_capacidad"
    motor = create_engine(url_rol("soporte_documentos"))
    try:
        with transaccion(motor, persona_id=None, consorcio_id=e["cons"]) as c:
            de_0037 = c.execute(
                text("select nucleo.capacidad_del_solicitante(:c, :p, :f, :h)"),
                {"c": e["cons"], "p": e["ad"], "f": FACULTADES, "h": e["hoy"]},
            ).scalar_one()
    finally:
        motor.dispose()
    assert de_0037 == e["m_ad"]
    assert pedido(e).mandato_id == e["m_mc"]
    o = _edificio(motor_migrate, motor_app, con_documentos=False)
    assert pedido(o, o["ad"]).mandato_id == o["m_ad"]
    # Exige el consorcio del contexto, y cem_app no la ejecuta.
    datos = {"c": e["cons"], "p": e["mc"], "h": e["hoy"]}
    with pytest.raises(DBAPIError) as error:
        _como_soporte(None, REGISTRAR, datos)
    assert error.value.orig.sqlstate == "42501"
    with (
        pytest.raises(DBAPIError) as error,
        transaccion(motor_app, persona_id=e["mc"], consorcio_id=e["cons"]) as c,
    ):
        c.execute(text(REGISTRAR), datos)
    assert error.value.orig.sqlstate == "42501"


def test_un_consejo_sin_su_condicion_sale_pendiente_de_revision(
    consejo,  # noqa: F811
    con_url,
    claves,
    tmp_path,
    motor_app,
):
    """El estado del mandato es el operativo, como en `mandato_detalle` desde 0023: sin la
    condición habilitante, el Consejo no está `vigente`. Pide la administradora del escenario."""
    e = consejo
    finalizar(motor_app, e["cons"], e["adm"], e["v_prop"])
    # Control: la API lo muestra `pendiente_revision`.
    assert estado(e["adm"], e["cons"], e["m"]) == "pendiente_revision"
    codigo, salida = exportar(e, claves, tmp_path, solicitante=str(e["adm"]))
    assert codigo == 0
    mandatos = {m["id"]: m for m in entidades(descifrar(salida, claves["privada"]))["mandato"]}
    assert mandatos[str(e["m"])]["estado"] == "pendiente_revision"
    assert {m["estado"] for i, m in mandatos.items() if i != str(e["m"])} == {"vigente"}
