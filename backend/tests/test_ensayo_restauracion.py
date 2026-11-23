"""Las funciones puras del ensayo PA26 de staging (`pruebas/ensayo_restauracion_staging.py`): el
ensayo mismo corre en la PC contra la VM, con la identidad de la bóveda, y no se puede repetir
acá; lo que decide qué bajar, hasta dónde recuperar y qué contenedores tocar, sí."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.plataforma import copia_continua
from pruebas import ensayo_restauracion_staging as ensayo

MIGRACIONES = Path(__file__).resolve().parents[1] / "migrations" / "versions"


def test_el_nombre_del_segmento_es_el_de_pg_walfile_name():
    # pg_walfile_name('0/36000028') en una línea de tiempo 1 con segmentos de 16 MB.
    assert ensayo.segmento_de(1, ensayo.lsn("0/36000028")) == "000000010000000000000036"
    assert ensayo.segmento_de(2, ensayo.lsn("1/0")) == "000000020000000100000000"
    # El último segmento de un "id" lógico y el primero del siguiente.
    assert ensayo.segmento_de(1, ensayo.lsn("0/FFFFFFFF")) == "0000000100000000000000FF"
    assert ensayo.numero_de("000000010000000100000000") == (1, 256)
    # Control: la misma cuenta que usa la copia continua.
    assert ensayo.numero_de("0000000100000002000000A0") == copia_continua.numero_segmento(
        "0000000100000002000000A0", ensayo.TAMANO_SEGMENTO
    )


def test_elige_la_copia_base_mas_nueva_anterior_a_la_marca():
    marcas = {"20261110T060926Z": {"n": 1}, "20261117T053929Z": {"n": 2},
              "20261124T050000Z": {"n": 3}}  # fmt: skip
    momento, marca = ensayo.elegir_base(marcas, datetime(2026, 11, 23, tzinfo=UTC))
    assert (momento, marca) == ("20261117T053929Z", {"n": 2})
    with pytest.raises(ensayo.Fallo, match="no hay copia base"):
        ensayo.elegir_base(marcas, datetime(2026, 11, 1, tzinfo=UTC))


def test_los_segmentos_necesarios_son_todos_y_sin_huecos():
    claves = [f"wal/0000000100000000000000{n:02X}.gz.age" for n in range(0x30, 0x38)]
    claves += ["wal/00000002000000000000003A.gz.age", "wal/00000002.history.gz.age"]
    elegidos = ensayo.segmentos_necesarios(claves, "000000010000000000000031",
                                           "000000010000000000000035")  # fmt: skip
    assert elegidos == ["wal/00000002.history.gz.age"] + [
        f"wal/0000000100000000000000{n:02X}.gz.age" for n in range(0x31, 0x36)
    ]
    # Control: con un hueco en el medio, se niega (la recuperación se cortaría ahí).
    sin_uno = [c for c in claves if "33" not in c]
    with pytest.raises(ensayo.Fallo, match="faltan 1 segmentos"):
        ensayo.segmentos_necesarios(sin_uno, "000000010000000000000031",
                                    "000000010000000000000035")  # fmt: skip


def test_el_punto_medio_queda_entre_las_marcas():
    a = datetime(2026, 11, 23, 4, 10, 48, tzinfo=UTC)
    b = a + timedelta(minutes=6)
    assert a < ensayo.punto_medio(a, b) < b
    with pytest.raises(ensayo.Fallo):
        ensayo.punto_medio(b, a)


def test_el_parcial_tiene_que_ser_posterior_al_segundo_de_la_marca():
    marca = datetime(2026, 11, 23, 4, 10, 48, 500000, tzinfo=UTC)
    seg = "000000010000000000000036"
    mismo_segundo = f"wal-parcial/{seg}-20261123T041048Z.partial.gz.age"
    despues = f"wal-parcial/{seg}-20261123T041510Z.partial.gz.age"
    otro = "wal-parcial/000000010000000000000035-20261123T041510Z.partial.gz.age"
    assert ensayo.parcial_posterior([mismo_segundo, otro], seg, marca) is None
    assert ensayo.parcial_posterior([mismo_segundo, despues], seg, marca) == despues
    assert ensayo.parcial_posterior(["wal/000000010000000000000036.gz.age"], seg, marca) == (
        "wal/000000010000000000000036.gz.age"
    )
    assert ensayo.parcial_posterior(["wal/000000010000000000000035.gz.age"], seg, marca) is None


def test_las_tablas_nuevas_salen_de_las_migraciones_0119_a_0128():
    tablas = ensayo.tablas_de_migraciones(MIGRACIONES)
    assert "nucleo.presupuesto" in tablas and "nucleo.autorizacion_ayuda" in tablas
    assert "nucleo.comunicado" in tablas and "nucleo.reparacion" in tablas
    # Control: una tabla de antes de 0119 no entra.
    assert "nucleo.decision_formal" not in tablas
    assert ensayo.tablas_de_migraciones(MIGRACIONES, [10]) == sorted(
        ["nucleo.decision_formal", "nucleo.modo_operacion"]
    )


def test_los_ajustes_de_recuperacion_apuntan_al_momento_y_promueven():
    texto = ensayo.ajustes_recuperacion(datetime(2026, 11, 23, 4, 13, 0, 123456, tzinfo=UTC),
                                        "/restaurar/wal")  # fmt: skip
    assert "recovery_target_time = '2026-11-23 04:13:00.123456+00'" in texto
    assert "recovery_target_action = 'promote'" in texto
    assert "listen_addresses = ''" in texto
    assert "test -f /restaurar/wal/%f.gz && gunzip -c /restaurar/wal/%f.gz > %p" in texto


ST = "consorcio-en-mano-staging"
SALIDA_INSPECT = f"""/qor6-20261123T040456 i/cem-frontend:c7c cem-frontend-staging {ST}
/virs-20261123T040435 i/cem-backend:c7c cem-copia-continua-staging {ST}
/yepj-20261123T040314 i/cem-backend:c7c cem-backend-staging {ST}
/6y0z-20261103T222158 i/cem-backend:153 cem-copia-continua consorcio-en-mano
/ai3m postgres:18-alpine cem-postgres-staging {ST}
/ijhp postgres:18-alpine cem-postgres consorcio-en-mano
/otro nginx:1 <no value> <no value>
"""


def test_los_recursos_son_solo_los_del_proyecto_de_staging():
    hay = ensayo.recursos(SALIDA_INSPECT)
    assert hay["cem-copia-continua-staging"] == (
        "virs-20261123T040435",
        "i/cem-backend:c7c",
    )
    assert hay["cem-postgres-staging"] == ("ai3m", "postgres:18-alpine")
    # Control: los de producción están en la salida y no entran.
    assert "cem-copia-continua" not in hay and "cem-postgres" not in hay
    doble = SALIDA_INSPECT + ("/yepj-20261123T050000 ghcr.io/githolan/cem-backend:d1"
                              " cem-backend-staging consorcio-en-mano-staging\n")  # fmt: skip
    with pytest.raises(ensayo.Negado, match="más de un contenedor"):
        ensayo.recursos(doble)


@pytest.mark.parametrize(("valor", "staging"), [
    ("cem-staging.holancloud.com", True),
    ("cem-copias-seguridad-staging", True),
    ("cem.holancloud.com", False),
    ("cem-copias-seguridad", False),
    ("staging.cem.holancloud.com", False),
    ("", False),
])  # fmt: skip
def test_es_staging(valor, staging):
    assert ensayo.es_staging(valor) is staging
