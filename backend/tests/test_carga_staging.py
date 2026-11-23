"""El humo de carga de staging (`pruebas/carga_staging.py`): sus cuentas (percentiles, plan de
pedidos, IP de prueba, veredicto), su guarda, y una corrida corta entera contra un backend real
(como el smoke), que deja las sesiones revocadas y se niega a repetir el consorcio."""

import json
import random
from uuid import uuid4

import pytest

from pruebas import carga_staging as carga
from tests.test_smoke_staging import HOST, ORIGEN_STAGING, entorno, servidor  # noqa: F401


def test_el_percentil_es_el_del_rango_mas_cercano():
    valores = [float(v) for v in range(1, 101)]
    assert carga.percentil(valores, 50) == 50.0
    assert carga.percentil(valores, 95) == 95.0
    assert carga.percentil(valores, 99) == 99.0
    assert carga.percentil([3.0], 95) == 3.0
    assert carga.percentil([], 95) is None
    # Control: no depende del orden de llegada.
    assert carga.percentil(list(reversed(valores)), 95) == 95.0


def test_la_tasa_del_pico_sale_del_documento_13():
    assert carga.TASA_PICO == 13.9  # 120.000 por día × 10 / 86.400


def test_el_plan_reparte_las_escrituras_y_respeta_la_tasa():
    plan = carga.plan_de_pedidos(10, 13.9, 5)
    assert len(plan) == 139
    assert sum(1 for _, e in plan if e) == 5
    assert plan[1][0] == pytest.approx(1 / 13.9)
    turnos = [i for i, (_, e) in enumerate(plan) if e]
    assert turnos == sorted(turnos) and turnos[0] > 0 and turnos[-1] < 138
    assert sum(1 for _, e in carga.plan_de_pedidos(10, 13.9, 0) if e) == 0
    with pytest.raises(ValueError):
        carga.plan_de_pedidos(1, 2, 5)


def test_las_ip_de_prueba_son_de_documentacion_y_distintas():
    ips = carga.ips_de_prueba(400)
    assert len(set(ips)) == 400
    assert all(ip.startswith(("203.0.113.", "198.51.100.")) for ip in ips)
    assert not set(ips) & carga.IP_RESERVADAS
    with pytest.raises(ValueError):
        carga.ips_de_prueba(1000)


def test_el_veredicto_usa_solo_el_umbral_de_rnf01():
    m = [carga.Muestra("r", 200, s / 1000, True, False, False) for s in range(1, 1001)]
    resumen = carga.resumir(m)
    assert resumen["lecturas"]["p95_ms"] == 950.0
    v = carga.veredicto_rnf01(resumen, 100)
    assert v["cumple_latencia"] is True and v["sesiones_verificadas"] is False
    lentas = carga.resumir([carga.Muestra("r", 200, 1.2, True, False, False)] * 20)
    assert carga.veredicto_rnf01(lentas, 500)["cumple_latencia"] is False


def test_la_mezcla_elige_segun_los_pesos():
    azar = random.Random(1)
    elegidas = [carga.elegir({"a": 9, "b": 1}, azar) for _ in range(1000)]
    assert 850 < elegidas.count("a") < 950


def test_la_guarda_se_niega_fuera_de_staging():
    url = "postgresql+psycopg://u:p@cem-postgres-staging/cem"
    carga.exigir_staging("https://cem-staging.holancloud.com", url, "cem-postgres-staging",
                         "cem-staging.holancloud.com")  # fmt: skip
    for origen, base, host in (
        ("https://cem.holancloud.com", "cem-postgres-staging", "cem.holancloud.com"),
        ("https://cem-staging.holancloud.com", "cem-postgres", "cem-staging.holancloud.com"),
        ("https://cem-staging.holancloud.com", "cem-postgres-staging", "cem.holancloud.com"),
    ):
        with pytest.raises(carga.Negado):
            carga.exigir_staging(origen, url, base, host)


@pytest.mark.db
@pytest.mark.redis
def test_una_corrida_corta_mide_cierra_y_no_se_repite(
    servidor,  # noqa: F811
    entorno,  # noqa: F811
    capsys,
    motor_migrate,
    redis_limpio,
):
    consorcio = uuid4()
    argumentos = ["--consorcio", str(consorcio), "--base", entorno, "--url", servidor,
                  "--host", HOST, "--personas", "6", "--consejo", "2", "--tasa", "8",
                  "--duracion", "3", "--escrituras", "2", "--hilos", "8"]  # fmt: skip
    codigo = carga.main(argumentos)
    salida = capsys.readouterr()
    resumen = json.loads(salida.out[salida.out.find("\n{") + 1 :])
    assert codigo == 0, salida.err
    r = resumen["resultado"]
    assert r["total"]["pedidos"] == 24 and r["escrituras"]["pedidos"] == 2
    assert r["total"]["errores"] == 0, r["codigos"]
    assert r["medicion_valida"] is True and r["rnf01"]["sesiones"] == 6
    assert resumen["sesiones_abiertas_al_cerrar"] == 0
    assert resumen["personas_que_siguen_entrando"] == 0
    # Control: el mismo consorcio otra vez se niega sin escribir.
    assert carga.main(argumentos) == 4
    assert ORIGEN_STAGING.endswith(HOST)
