"""Lecturas de la tanda 8 (`0110`; 18-11-2026) que no cambian reglas: los grupos con porcentajes
de un período antes de abrir el borrador (FL-LIQ-01, pedido de la tanda 6) y la lista de
documentos sin los que no tienen nada que abrir para quien solo lee (FL-DOC-02, pedido de la
tanda 7).

Contra servicios reales. Datos SINTETICOS / PRUEBA / NO REAL.
"""

import pytest

from tests.test_contexto_mandatos import cliente as cliente_documentos
from tests.test_documentos_2c1 import (  # noqa: F401  (los fixtures se importan)
    analizar,
    bucket,
    crear,
    escena,
    firmas_de_la_imagen,
    pdf,
    subir,
    url,
)
from tests.test_liquidacion_0057 import esquema, periodo  # noqa: F401  (`esquema`: fixture)
from tests.test_pago_manual_0055 import e as e  # noqa: F401  (es un fixture)
from tests.test_rutas_egresos import codigo, get

pytestmark = [pytest.mark.db, pytest.mark.redis]
# ruff: noqa: F811


# FL-LIQ-01: los grupos de un período, antes del borrador ------------------------------------------


def grupos(e, mes: str):
    return get(e, e.adm, f"liquidaciones/grupos?periodo={mes}")


def test_sin_esquema_no_hay_grupos_y_con_esquema_si(motor_migrate, e):
    mes = periodo(motor_migrate, e)
    r = grupos(e, mes)
    assert r.status_code == 200, r.text
    assert r.json() == {"periodo": mes, "elementos": []}


def test_los_grupos_del_periodo_son_los_del_esquema_vigente(motor_migrate, e, esquema):
    mes = periodo(motor_migrate, e)
    [g] = grupos(e, mes).json()["elementos"]
    assert (g["grupo"], g["esquema_id"]) == ("expensas_comunes", str(esquema))
    # Antes de que rija el esquema (2024), ninguno.
    assert grupos(e, "2023-12-01").json()["elementos"] == []
    # El período es el primer día de un mes; quien no prepara no lo lee.
    assert grupos(e, "2026-10-15").status_code == 422
    assert codigo(get(e, e.mc1, f"liquidaciones/grupos?periodo={mes}")) == (403, "sin_permiso")


# FL-DOC-02: quien solo lee no recibe documentos sin nada que abrir --------------------------------


def ids(persona, cons) -> set[str]:
    with cliente_documentos(persona) as c:
        r = c.get(url(cons))
    assert r.status_code == 200, r.text
    return {d["id"] for d in r.json()["documentos"]}


def test_un_documento_sin_version_disponible_no_se_lista_a_quien_solo_lee(
    motor_app, escena, bucket
):
    e = escena
    # El Consejo carga actas (`documentos.cargar_operativos`) y las lee; la propietaria solo lee.
    vacio, en_analisis = (
        crear(e["mc"], e["cons"], titulo=f"Acta {n}").json()["id"] for n in (1, 2)
    )  # fmt: skip
    assert subir(e["mc"], e["cons"], en_analisis, pdf(), "application/pdf").status_code == 201
    # Quien carga los ve a los dos, para subirles o esperar el archivo.
    assert {vacio, en_analisis} <= ids(e["mc"], e["cons"])
    # Quien solo lee (la propietaria, por su vínculo), ninguno: no hay nada que abrir.
    assert not {vacio, en_analisis} & ids(e["prop"], e["cons"])
    # Control: con el archivo aprobado, lo ve también quien solo lee.
    assert analizar(motor_app) == 1
    assert en_analisis in ids(e["prop"], e["cons"]) and vacio not in ids(e["prop"], e["cons"])
