"""Tanda 10 de la revisión por flujos (18-11-2026): «Lo que cargó esta cuenta» para quien carga sin
ver la lista (FL-DOC-04, `0111`), y la ida y vuelta de `0111`.

Contra PostgreSQL real; las versiones se insertan directo (sin el almacén ni el antivirus): lo que
se prueba es la lectura. Cada negación lleva su control. Datos SINTETICOS / NO REAL.
"""

from datetime import timedelta
from uuid import UUID

import pytest
from alembic import command
from cem_contracts.documentos import CARGAS_PROPIAS_DIAS, CARGAS_PROPIAS_MAXIMO
from sqlalchemy import text

from tests.test_lectura_carga_0088 import documento_comun
from tests.test_mandatos import otra_persona
from tests.test_orden_pago_neutra import nueva_sesion
from tests.test_rutas_egresos import e as e  # noqa: F401  (es un fixture)
from tests.test_rutas_egresos import get, post
from tests.test_tanda6_documentos_0097 import MOTIVO, RECHAZAR, version_a_mano
from tests.test_vinculo_0014 import base_aparte  # noqa: F401  (es un fixture)

pytestmark = [pytest.mark.db, pytest.mark.redis]
# ruff: noqa: F811

NUEVAS = (
    "nucleo.lectura_por_aclarar(uuid, text, uuid)",
    "nucleo.mis_cargas_recientes(timestamptz, integer)",
)


def mis_cargas(e, quien: UUID, sesion: UUID | None = None):
    return get(e, quien, "documentos/mis-cargas", sesion)


def envejecer_version(motor_migrate, version: str, dias: int) -> None:
    with motor_migrate.begin() as c:
        n = c.execute(
            text("update nucleo.version_documento set subida_en = subida_en - :d where id = :v"),
            {"d": timedelta(days=dias), "v": version},
        ).rowcount
    assert n == 1


def test_lista_lo_propio_reciente_con_su_estado(motor_migrate, e):
    seguro = documento_comun(e, "seguros")
    acta = documento_comun(e, "actas_y_decisiones")
    primera = version_a_mano(motor_migrate, e, seguro, e.adm)
    segunda = version_a_mano(motor_migrate, e, acta, e.adm)
    ajena = version_a_mano(motor_migrate, e, acta, e.mc1)
    with motor_migrate.begin() as c:
        c.execute(RECHAZAR, {"v": primera})
    r = mis_cargas(e, e.adm)
    assert r.status_code == 200, r.text
    filas = r.json()["elementos"]
    # De la más nueva a la más vieja, solo las propias, con el título y el estado del análisis.
    assert [(f["version_id"], f["estado"], f["motivo_rechazo"]) for f in filas] == [
        (segunda, "en_cuarentena", None),
        (primera, "rechazado", "virus"),
    ]
    assert {f["documento_id"] for f in filas} == {seguro, acta}
    assert all(f["titulo"] and f["categoria"] for f in filas)
    # Control: la de mc1 la ve mc1.
    assert [f["version_id"] for f in mis_cargas(e, e.mc1).json()["elementos"]] == [ajena]


def test_lo_viejo_y_lo_retirado_no_se_listan(motor_migrate, e):
    doc = documento_comun(e, "seguros")
    otro = documento_comun(e, "seguros")
    vieja = version_a_mano(motor_migrate, e, doc, e.adm)
    retirada = version_a_mano(motor_migrate, e, otro, e.adm)
    ids = lambda: [f["version_id"] for f in mis_cargas(e, e.adm).json()["elementos"]]  # noqa: E731
    assert set(ids()) == {vieja, retirada}  # control
    envejecer_version(motor_migrate, vieja, CARGAS_PROPIAS_DIAS + 1)
    assert post(e, e.adm, f"documentos/{otro}/retiro", {"motivo": MOTIVO}).status_code == 200
    assert ids() == []


def test_a_lo_sumo_el_maximo(motor_migrate, e):
    versiones = [version_a_mano(motor_migrate, e, documento_comun(e, "seguros"), e.adm)
                 for _ in range(CARGAS_PROPIAS_MAXIMO + 1)]  # fmt: skip
    filas = mis_cargas(e, e.adm).json()["elementos"]
    assert len(filas) == CARGAS_PROPIAS_MAXIMO
    assert versiones[0] not in {f["version_id"] for f in filas}  # la más vieja queda afuera


def test_sin_ninguna_carga_es_403_y_fuera_del_edificio_404(motor_migrate, e):
    sesion = nueva_sesion(motor_migrate, e.prop)
    assert get(e, e.prop, "documentos", sesion).status_code == 200  # control: participa
    assert mis_cargas(e, e.prop, sesion).status_code == 403
    nadie = otra_persona(motor_migrate, e.cons)
    assert mis_cargas(e, nadie, nueva_sesion(motor_migrate, nadie)).status_code == 404
    assert mis_cargas(e, e.adm).status_code == 200  # control


def test_las_funciones_solo_las_ejecuta_cem_app(motor_migrate):
    with motor_migrate.connect() as c:
        filas = [
            tuple(c.execute(text(
                "select p.prosecdef, has_function_privilege('cem_app', p.oid, 'EXECUTE'),"
                " has_function_privilege('public', p.oid, 'EXECUTE')"
                " from pg_proc p where p.oid = to_regprocedure(:f)"
            ), {"f": f}).one())
            for f in NUEVAS
        ]  # fmt: skip
    assert filas == [(True, True, False)] * len(NUEVAS)


def test_0111_ida_y_vuelta(base_aparte):
    cfg, motor = base_aparte

    def existen() -> list:
        with motor.connect() as c:
            return [c.execute(text("select to_regprocedure(:f)"), {"f": f}).scalar()
                    for f in NUEVAS]  # fmt: skip

    command.upgrade(cfg, "0110")
    assert existen() == [None, None]
    command.upgrade(cfg, "0111")
    assert all(f is not None for f in existen())
    command.downgrade(cfg, "0110")
    assert existen() == [None, None]
