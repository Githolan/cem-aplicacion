"""Las funciones puras del ensayo PA26 de staging (`pruebas/ensayo_restauracion_staging.py`): el
ensayo mismo corre en la PC contra la VM, con la identidad de la bóveda, y no se puede repetir
acá; lo que decide qué bajar, hasta dónde recuperar y qué contenedores tocar, sí."""

import itertools
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


# Hallazgos P2 de Codex en #287 ---------------------------------------------------------------


def test_el_rpo_cuenta_desde_que_el_receptor_sincronizo_a_y_no_desde_a():
    # A confirmada a las 04:22:43; el receptor atrasado sincroniza su posición a las 04:24:10.
    seg = "000000010000000000000038"
    sincronizo = datetime(2026, 11, 23, 4, 24, 10, 300000, tzinfo=UTC)
    antes = f"wal-parcial/{seg}-20261123T042302Z.partial.gz.age"  # subido después de A
    despues = f"wal-parcial/{seg}-20261123T042502Z.partial.gz.age"
    assert ensayo.parcial_posterior([antes], seg, sincronizo) is None
    assert ensayo.parcial_posterior([antes, despues], seg, sincronizo) == despues
    # Control: contado desde la confirmación de A (lo de antes) se habría aceptado el primero.
    a = datetime(2026, 11, 23, 4, 22, 43, tzinfo=UTC)
    assert ensayo.parcial_posterior([antes, despues], seg, a) == antes


def test_los_artefactos_al_rpo_son_los_de_ese_momento():
    completos = [f"wal/0000000100000000000000{n:02X}.gz.age" for n in range(0x2E, 0x38)]
    parcial = "wal-parcial/000000010000000000000038-20261123T042502Z.partial.gz.age"
    elegidos = ensayo.artefactos_al_rpo(completos, "00000001000000000000002E", parcial)
    assert elegidos == completos + [parcial]
    # Con el segmento ya completo, hasta él y sin parcial.
    completo = "wal/000000010000000000000035.gz.age"
    hasta = ensayo.artefactos_al_rpo(completos, "00000001000000000000002E", completo)
    assert hasta[-1] == completo and len(hasta) == 8
    # El parcial del mismo segmento de la copia base: solo él.
    del_inicio = "wal-parcial/00000001000000000000002E-20261123T042502Z.partial.gz.age"
    assert ensayo.artefactos_al_rpo([], "00000001000000000000002E", del_inicio) == [del_inicio]
    # Controles: un hueco, o un parcial anterior a la copia base, se niegan.
    with pytest.raises(ensayo.Fallo, match="faltan 1 segmentos"):
        ensayo.artefactos_al_rpo([c for c in completos if "33" not in c],
                                 "00000001000000000000002E", parcial)  # fmt: skip
    with pytest.raises(ensayo.Fallo, match="anterior a la copia base"):
        ensayo.artefactos_al_rpo(completos, "000000010000000000000030", del_inicio)


def test_la_promocion_espera_lo_que_queda_del_rto_y_no_un_plazo_nuevo():
    assert ensayo.restante(100.0, 160.0, 14400) == 14340.0
    with pytest.raises(ensayo.Fallo, match="pasó el RTO"):
        ensayo.restante(100.0, 100.0 + 14400, 14400)
    ensayo.exigir_rto(14400.0, 14400)
    with pytest.raises(ensayo.Fallo, match="más que el RTO"):
        ensayo.exigir_rto(14400.1, 14400)


def test_sin_objetivo_aplica_todo_el_wal_y_promueve():
    texto = ensayo.ajustes_recuperacion(None, "/restaurar/wal")
    assert "recovery_target_time" not in texto
    assert "recovery_target_action = 'promote'" in texto
    assert "gunzip -c /restaurar/wal/%f.gz > %p" in texto


SEG = "000000010000000000000038"
A_CONFIRMADA = datetime(2026, 11, 23, 4, 22, 43, tzinfo=UTC)
SINCRONIZO = datetime(2026, 11, 23, 4, 24, 10, 300000, tzinfo=UTC)
PARCIAL_SIN_A = f"wal-parcial/{SEG}-20261123T042302Z.partial.gz.age"
PARCIAL_CON_A = f"wal-parcial/{SEG}-20261123T042502Z.partial.gz.age"


def ensayo_falso(monkeypatch) -> ensayo.Ensayo:
    """Un ensayo sin VM: las lecturas de la base y del almacén son las de un receptor atrasado
    (sincroniza A a las 04:24:10, después de subir un parcial a las 04:23:02)."""
    e = ensayo.Ensayo(None, None, None)
    marcas = iter([A_CONFIRMADA, A_CONFIRMADA + timedelta(minutes=3)])

    def marca(consorcio, letra):
        t = next(marcas)
        return {"decision": letra, "antes": t, "despues": t}

    def real(sql):
        if "pg_current_wal_flush_lsn" in sql:
            return [["0/38000100", SEG]]
        if "pg_stat_replication" in sql:
            return [["t", "0/38000200", str(SINCRONIZO.timestamp())]]
        return [["0/39000000", SEG]]  # pg_switch_wal

    def listar(*prefijos):
        objetos = [{"clave": c, "bytes": 1, "modificado": m} for c, m in (
            (PARCIAL_SIN_A, "2026-11-23T04:23:03+00:00"),
            (PARCIAL_CON_A, "2026-11-23T04:25:03+00:00"),
            (f"wal/{SEG}.gz.age", "2026-11-23T04:26:00+00:00"))]  # fmt: skip
        return [o for o in objetos if o["clave"].startswith(prefijos)]

    monkeypatch.setattr(e, "marca", marca)
    monkeypatch.setattr(e, "real", real)
    monkeypatch.setattr(e, "listar", listar)
    return e


def test_las_marcas_cuentan_el_parcial_tomado_con_a_sincronizada(monkeypatch, capsys):
    e = ensayo_falso(monkeypatch)
    e.marcas()
    assert e.rpo["clave"] == PARCIAL_CON_A
    assert e.rpo["lsn_a"] == "0/38000100"
    assert e.resumen["rpo_subido_s"] == 140.0  # 04:25:03 − 04:22:43
    # Control: el parcial de las 04:23:02, posterior a A pero anterior a la sincronización, estaba.
    assert PARCIAL_SIN_A in [o["clave"] for o in e.listar("wal-parcial/")]


def test_la_restauracion_falla_si_el_total_pasa_el_rto(monkeypatch):
    e = ensayo.Ensayo(None, None, None)
    reloj = itertools.chain([0.0], itertools.repeat(ensayo.ESPERA_PROMOCION_S + 1.0))
    monkeypatch.setattr(ensayo.time, "monotonic", lambda: next(reloj))
    monkeypatch.setattr(e, "base_anterior", lambda antes: ("20261117T053929Z", SEG))
    monkeypatch.setattr(e, "listar", lambda *p: [{"clave": f"wal/{SEG}.gz.age"}])
    monkeypatch.setattr(e, "marca_base", {"inicio_lsn": "0/38000028"})
    vistos = {}

    def restaurar_con(que, momento, wal, objetivo, inicio, limite):
        vistos.update(inicio=inicio, limite=limite)
        return "c", "LOG:  recovery stopping before commit of transaction 1", 10

    monkeypatch.setattr(e, "restaurar_con", restaurar_con)
    monkeypatch.setattr(e, "controles", lambda contenedor: None)
    a = {"despues": A_CONFIRMADA, "antes": A_CONFIRMADA}
    b = {"antes": A_CONFIRMADA + timedelta(minutes=3)}
    with pytest.raises(ensayo.Fallo, match="más que el RTO"):
        e.restaurar(a, b, SEG)
    # La espera recibió el inicio de todo el ensayo y el RTO entero como plazo total.
    assert vistos == {"inicio": 0.0, "limite": ensayo.ESPERA_PROMOCION_S}
