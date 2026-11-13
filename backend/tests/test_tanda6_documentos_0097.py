"""Tanda 6 de fricción: lo que Documentos sabe antes de ofrecer un acto (`0097`).

- **G-02:** la lista dice si algo guardado cita cada documento (`respalda`), con la regla de `0089`,
  a quien tiene la facultad que pide retirar; al resto, nulo.
- **G-09:** quien subió un archivo pregunta cómo terminó el análisis, aunque no vea la lista.

Contra PostgreSQL real; las versiones se insertan directo (sin el almacén ni el antivirus): lo que
se prueba es la lectura. Cada negación lleva su control. Datos SINTETICOS / NO REAL.
"""

from uuid import UUID, uuid4

import pytest
from alembic import command
from sqlalchemy import text

from tests.test_lectura_carga_0088 import documento_comun
from tests.test_mandatos import otra_persona
from tests.test_orden_pago_neutra import nueva_sesion
from tests.test_rutas_egresos import codigo, get, post
from tests.test_rutas_egresos import e as e  # noqa: F401  (es un fixture)
from tests.test_vinculo_0014 import base_aparte  # noqa: F401  (es un fixture)

pytestmark = [pytest.mark.db, pytest.mark.redis]
# ruff: noqa: F811

MOTIVO = "Reemplazado por la versión firmada SINTETICA"

VERSION_A_MANO = text(
    "insert into nucleo.version_documento (consorcio_id, documento_id, tipo, tamano, sha256,"
    " clave_objeto, subida_por) values (:c, :d, 'application/pdf', 10, :s, :k, :p) returning id"
)
RECHAZAR = text(
    "update nucleo.version_documento set estado = 'rechazado', motivo_rechazo = 'virus',"
    " analizada_en = now() where id = :v"
)


def version_a_mano(motor_migrate, e, doc, quien: UUID, sha: str | None = None) -> str:
    """Datos sintéticos: una versión insertada directo, en cuarentena."""
    with motor_migrate.begin() as c:
        return str(c.execute(VERSION_A_MANO, {
            "c": e.cons, "d": doc, "s": sha or uuid4().hex * 2,
            "k": f"cem/sintetica/{uuid4()}", "p": quien,
        }).scalar_one())  # fmt: skip


def lista(e, quien: UUID, sesion: UUID | None = None) -> dict[str, dict]:
    r = get(e, quien, "documentos", sesion)
    assert r.status_code == 200, r.text
    return {d["id"]: d for d in r.json()["documentos"]}


def acta_de_una_decision(motor_migrate, e, sha: str) -> None:
    """Datos sintéticos: una decisión del consorcio registra ese archivo como su acta."""
    with motor_migrate.begin() as c:
        decision = c.execute(
            text("select id from nucleo.decision_formal where consorcio_id = :c limit 1"),
            {"c": e.cons},
        ).scalar_one()
        c.execute(text("update nucleo.decision_formal set sha256_acta = :s where id = :d"),
                  {"s": sha, "d": decision})  # fmt: skip


# G-02: respalda -------------------------------------------------------------------------------


def test_respalda_dice_cual_cita_algo_y_coincide_con_retirar(motor_migrate, e):
    acta = documento_comun(e, "actas_y_decisiones")
    libre = documento_comun(e, "actas_y_decisiones")
    sha = uuid4().hex * 2
    version_a_mano(motor_migrate, e, acta, e.adm, sha)
    version_a_mano(motor_migrate, e, libre, e.adm)
    # Control: antes de que la decisión lo cite, ninguno respalda nada.
    antes = lista(e, e.adm)
    assert antes[acta]["respalda"] is False and antes[libre]["respalda"] is False
    acta_de_una_decision(motor_migrate, e, sha)
    despues = lista(e, e.adm)
    assert despues[acta]["respalda"] is True
    assert despues[libre]["respalda"] is False
    # Lo que dice la lista es lo que hace retirar.
    r = post(e, e.adm, f"documentos/{acta}/retiro", {"motivo": MOTIVO})
    assert codigo(r) == (409, "documento_referenciado")
    assert post(e, e.adm, f"documentos/{libre}/retiro", {"motivo": MOTIVO}).status_code == 200


def test_respalda_va_nulo_a_quien_no_tiene_la_facultad_de_retirar(motor_migrate, e):
    doc = documento_comun(e, "actas_y_decisiones")
    # Datos sintéticos: el propietario de la escena lee por su vínculo, sin la facultad.
    sesion = nueva_sesion(motor_migrate, e.prop)
    del_vinculo = lista(e, e.prop, sesion)
    assert doc in del_vinculo  # control: la lista le llega
    assert {d["respalda"] for d in del_vinculo.values()} == {None}
    assert lista(e, e.adm)[doc]["respalda"] is False  # control: con la facultad, sí


# G-09: el estado de la versión propia --------------------------------------------------------


def estado(e, quien: UUID, doc: str, version: str, sesion: UUID | None = None):
    return get(e, quien, f"documentos/{doc}/versiones/{version}/estado", sesion)


def test_quien_subio_el_archivo_ve_como_termino_el_analisis(motor_migrate, e):
    doc = documento_comun(e, "seguros")
    version = version_a_mano(motor_migrate, e, doc, e.adm)
    r = estado(e, e.adm, doc, version)
    assert r.status_code == 200, r.text
    assert r.json() == {"estado": "en_cuarentena", "motivo_rechazo": None}
    with motor_migrate.begin() as c:
        c.execute(RECHAZAR, {"v": version})
    assert estado(e, e.adm, doc, version).json() == {
        "estado": "rechazado",
        "motivo_rechazo": "virus",
    }


def test_la_version_de_otra_persona_responde_como_si_no_existiera(motor_migrate, e):
    doc = documento_comun(e, "actas_y_decisiones")
    ajena = version_a_mano(motor_migrate, e, doc, e.adm)
    propia = version_a_mano(motor_migrate, e, doc, e.mc1)
    assert codigo(estado(e, e.mc1, doc, ajena)) == (404, "no_encontrado")
    assert codigo(estado(e, e.mc1, doc, str(uuid4()))) == (404, "no_encontrado")
    assert estado(e, e.mc1, doc, propia).status_code == 200  # control: la suya, sí


def test_un_documento_retirado_ya_no_responde(motor_migrate, e):
    doc = documento_comun(e, "seguros")
    version = version_a_mano(motor_migrate, e, doc, e.adm)
    assert estado(e, e.adm, doc, version).status_code == 200  # control
    assert post(e, e.adm, f"documentos/{doc}/retiro", {"motivo": MOTIVO}).status_code == 200
    assert codigo(estado(e, e.adm, doc, version)) == (404, "no_encontrado")


def test_sin_ninguna_carga_es_403(motor_migrate, e):
    doc = documento_comun(e, "seguros")
    version = version_a_mano(motor_migrate, e, doc, e.adm)
    # El propietario de la escena participa del edificio y lee por su vínculo, pero no carga.
    sesion = nueva_sesion(motor_migrate, e.prop)
    assert lista(e, e.prop, sesion)  # control: participa y lee la lista
    assert estado(e, e.prop, doc, version, sesion).status_code == 403
    # Quien no participa del edificio no sabe ni que existe (como en el resto de la API).
    nadie = otra_persona(motor_migrate, e.cons)
    assert estado(e, nadie, doc, version, nueva_sesion(motor_migrate, nadie)).status_code == 404
    assert estado(e, e.adm, doc, version).status_code == 200  # control


# La base, sin la API ----------------------------------------------------------------------------


def test_las_funciones_solo_las_ejecuta_cem_app(motor_migrate):
    with motor_migrate.connect() as c:
        filas = c.execute(text(
            "select p.proname, p.prosecdef, has_function_privilege('cem_app', p.oid, 'EXECUTE'),"
            " has_function_privilege('public', p.oid, 'EXECUTE')"
            " from pg_proc p join pg_namespace n on n.oid = p.pronamespace"
            " where n.nspname = 'nucleo'"
            " and p.proname in ('documentos_que_respaldan', 'estado_de_mi_version')"
            " order by p.proname"
        )).all()  # fmt: skip
    assert [tuple(f) for f in filas] == [
        ("documentos_que_respaldan", True, True, False),
        ("estado_de_mi_version", True, True, False),
    ]


def test_0097_baja_y_sube(base_aparte):
    cfg, motor = base_aparte
    consulta = text(
        "select count(*) from pg_proc where proname in"
        " ('documentos_que_respaldan', 'estado_de_mi_version')"
    )

    def contar() -> int:
        with motor.connect() as c:
            return c.execute(consulta).scalar_one()

    command.upgrade(cfg, "0097")
    assert contar() == 2
    command.downgrade(cfg, "0096")
    assert contar() == 0
    command.upgrade(cfg, "0097")
    assert contar() == 2
