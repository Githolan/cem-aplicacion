"""Esquemas de coeficientes y reglas de reparto (2b-5, PR 2; D4, RF05): migración 0021.

Diseño: `doc/diseno/H2b_5_Diseno.md` del repositorio CEM, §3. Los porcentajes son sintéticos:
tres grupos que suman 99,96 % como los de Pareja 3750, sin sus datos reales.
"""

import json
from datetime import date
from decimal import Decimal

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, ProgrammingError

from app.db import transaccion
from app.liquidacion.esquemas import repartir_por_esquema
from app.liquidacion.reparto import RepartoBloqueado
from scripts import cargar_coeficientes
from tests.conftest import url_rol
from tests.test_contexto_mandatos import otra_unidad
from tests.test_modo_operacion import consorcio_con_persona, decidir_equipo
from tests.test_vinculo_0014 import (  # noqa: F401  (`base_aparte` es un fixture)
    _consorcio_y_unidad,
    base_aparte,
    eventos,
)

pytestmark = pytest.mark.db

FUENTE = "Reglamento de copropiedad, escritura 812, cláusula 4"
DESDE = date(2024, 1, 1)
D = Decimal


@pytest.fixture
def edificio(motor_migrate, motor_app):
    """Un consorcio con cinco unidades y una decisión del equipo."""
    cons, u1, _ = consorcio_con_persona(motor_migrate)
    unidades = [u1] + [otra_unidad(motor_migrate, cons, f"{n}° A")[0] for n in range(2, 6)]
    return {"cons": cons, "u": unidades, "acta": decidir_equipo(motor_app, cons)}


def porcentajes(e, *valores: str) -> dict[str, str]:
    return {str(u): v for u, v in zip(e["u"], valores, strict=True)}


# Suman 99,96 % cada uno; cocheras con dos unidades en 0 %.
EXPENSAS = ("20.120000", "19.880000", "25.000000", "17.480000", "17.480000")
AGUA = ("18.330000", "21.670000", "20.000000", "19.980000", "19.980000")
COCHERAS = ("0.000000", "33.320000", "0.000000", "33.320000", "33.320000")


def registrar(motor_app, cons, coeficientes, *, grupo="expensas_comunes", fuente=FUENTE,
              desde=DESDE, decision=None, metodo="proporcional_a_coeficientes",
              centavos="restos_mayores", actor=None, origen="soporte", sesion=None):  # fmt: skip
    with transaccion(motor_app, persona_id=sesion, consorcio_id=cons) as c:
        return c.execute(
            text(
                "select nucleo.registrar_esquema_coeficientes(:g, :f, :d, :dec, cast(:co as jsonb),"
                " :m, :ce, :a, :o)"
            ),
            {
                "g": grupo,
                "f": fuente,
                "d": desde,
                "dec": decision,
                "m": metodo,
                "ce": centavos,
                "co": json.dumps(coeficientes) if coeficientes is not None else None,
                "a": actor,
                "o": origen,
            },  # fmt: skip
        ).scalar_one()


def motivo_de(error) -> str:
    return error.value.orig.diag.message_hint


def guardados(motor_migrate, esquema) -> dict[str, Decimal]:
    with motor_migrate.connect() as c:
        return {
            str(u): p
            for u, p in c.execute(
                text(
                    "select unidad_id, porcentaje from nucleo.coeficiente_juridico"
                    " where esquema_id = :e"
                ),
                {"e": esquema},
            )
        }


def reparto(motor_app, cons, esquema, total: str) -> dict:
    with transaccion(motor_app, persona_id=None, consorcio_id=cons) as c:
        return repartir_por_esquema(c, esquema, D(total))


# Lo que se guarda --------------------------------------------------------------------------


def test_los_porcentajes_se_guardan_exactos_aunque_sumen_99_96(edificio, motor_migrate, motor_app):
    e = edificio
    esquema = registrar(motor_app, e["cons"], porcentajes(e, *EXPENSAS), decision=e["acta"])
    g = guardados(motor_migrate, esquema)
    assert g == {k: D(v) for k, v in porcentajes(e, *EXPENSAS).items()}
    assert sum(g.values()) == D("99.960000")
    with motor_migrate.connect() as c:
        fila = c.execute(
            text(
                "select s.version, s.grupo, s.fuente_referencia, s.origen, s.registrado_por,"
                " r.version as regla, r.metodo::text, r.centavos::text"
                " from nucleo.esquema_coeficientes s"
                " join nucleo.regla_reparto r on r.esquema_id = s.id"
                " where s.id = :e"
            ),
            {"e": esquema},
        ).one()
    assert tuple(fila) == (1, "expensas_comunes", FUENTE, "soporte", None, 1,
                           "proporcional_a_coeficientes", "restos_mayores")  # fmt: skip
    # S2b (0043): soporte no escribe el evento; versión, fuente, decisión y filas quedan arriba.
    assert eventos(motor_migrate, e["cons"], "consorcio.coeficientes.registrar") == []


def test_tres_grupos_independientes_y_el_cero_es_valido(edificio, motor_migrate, motor_app):
    e = edificio
    ids = {
        g: registrar(motor_app, e["cons"], porcentajes(e, *v), grupo=g, decision=e["acta"])
        for g, v in (("expensas_comunes", EXPENSAS), ("agua", AGUA), ("cocheras", COCHERAS))
    }
    assert len(set(ids.values())) == 3
    assert guardados(motor_migrate, ids["cocheras"])[str(e["u"][0])] == D("0")


# El reparto ---------------------------------------------------------------------------------


@pytest.mark.parametrize("total", ["123456.78", "0.01", "-999.99", "1000000.00"])
def test_el_reparto_cierra_al_centavo_sin_tocar_los_coeficientes(edificio, motor_migrate, motor_app,
                                                                  total):  # fmt: skip
    e = edificio
    esquema = registrar(motor_app, e["cons"], porcentajes(e, *EXPENSAS), decision=e["acta"])
    r = reparto(motor_app, e["cons"], esquema, total)
    assert set(r) == set(e["u"])
    assert sum(r.values()) == D(total)
    assert all(v == v.quantize(D("0.01")) for v in r.values())
    assert sum(guardados(motor_migrate, esquema).values()) == D("99.960000")


def test_el_reparto_es_proporcional_a_los_porcentajes(edificio, motor_app):
    e = edificio
    esquema = registrar(motor_app, e["cons"], porcentajes(e, *EXPENSAS), decision=e["acta"])
    r = reparto(motor_app, e["cons"], esquema, "99960.00")
    # Con un total de 99.960,00, cada unidad paga su porcentaje × 1000, exacto.
    assert [r[u] for u in e["u"]] == [D("20120.00"), D("19880.00"), D("25000.00"),
                                      D("17480.00"), D("17480.00")]  # fmt: skip


def test_las_unidades_con_cero_no_pagan(edificio, motor_app):
    e = edificio
    esquema = registrar(motor_app, e["cons"], porcentajes(e, *COCHERAS), grupo="cocheras",
                        decision=e["acta"])  # fmt: skip
    r = reparto(motor_app, e["cons"], esquema, "1000.00")
    assert r[e["u"][0]] == r[e["u"][2]] == D("0.00")
    assert sum(r.values()) == D("1000.00")


def test_un_esquema_de_otro_consorcio_no_se_reparte(edificio, motor_migrate, motor_app):
    e = edificio
    esquema = registrar(motor_app, e["cons"], porcentajes(e, *EXPENSAS), decision=e["acta"])
    otro, _, _ = consorcio_con_persona(motor_migrate)
    with pytest.raises(RepartoBloqueado):  # la RLS lo oculta: igual que uno inexistente
        reparto(motor_app, otro, esquema, "100.00")
    assert sum(reparto(motor_app, e["cons"], esquema, "100.00").values()) == D("100.00")  # control


@pytest.mark.parametrize("cambio", ["unidad_nueva", "unidad_de_baja"])
def test_pa02_si_el_esquema_no_coincide_con_las_unidades_el_reparto_se_bloquea(
    edificio, motor_migrate, motor_app, cambio
):
    e = edificio
    esquema = registrar(motor_app, e["cons"], porcentajes(e, *EXPENSAS), decision=e["acta"])
    assert sum(reparto(motor_app, e["cons"], esquema, "100.00").values()) == D("100.00")  # control
    if cambio == "unidad_nueva":
        otra_unidad(motor_migrate, e["cons"], "6° A")
        esperado = "sin coeficiente: 6° A"
    else:
        with motor_migrate.begin() as c:
            c.execute(text("update nucleo.unidad set eliminado_en = now() where id = :u"),
                      {"u": e["u"][4]})  # fmt: skip
        esperado = "dadas de baja: 5° A"
    with pytest.raises(RepartoBloqueado, match=esperado):
        reparto(motor_app, e["cons"], esquema, "100.00")


# Versiones ----------------------------------------------------------------------------------


def test_la_version_siguiente_rige_despues_de_la_anterior(edificio, motor_migrate, motor_app):
    e = edificio
    registrar(motor_app, e["cons"], porcentajes(e, *EXPENSAS), decision=e["acta"])
    for desde in (DESDE, date(2023, 12, 31)):
        with pytest.raises(DBAPIError) as error:
            registrar(motor_app, e["cons"], porcentajes(e, *AGUA), decision=e["acta"], desde=desde)
        assert motivo_de(error) == "vigencia_no_posterior"
    v2 = registrar(motor_app, e["cons"], porcentajes(e, *AGUA), decision=e["acta"],
                   desde=date(2025, 1, 1))  # fmt: skip
    with motor_migrate.connect() as c:
        assert c.execute(text("select version from nucleo.esquema_coeficientes where id = :e"),
                         {"e": v2}).scalar_one() == 2  # fmt: skip
    # Otro grupo no compite con las fechas de este.
    registrar(motor_app, e["cons"], porcentajes(e, *AGUA), grupo="agua", decision=e["acta"])


# Validaciones -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("cambio", "motivo"),
    [
        ({"origen": "api"}, "origen_invalido"),
        ({"origen": None}, "origen_invalido"),
        ({"actor": "persona"}, "origen_invalido"),
        ({"sesion": "persona"}, "origen_invalido"),
        ({"grupo": "Expensas"}, "grupo_invalido"),
        ({"grupo": "a"}, "grupo_invalido"),
        ({"fuente": "  "}, "fuente_requerida"),
        ({"fuente": "x" * 301}, "fuente_requerida"),
        ({"desde": None}, "vigencia_requerida"),
        ({"decision": None}, "decision_invalida"),
        ({"decision": "otra"}, "decision_invalida"),
        ({"metodo": None}, "regla_incompleta"),
        ({"centavos": None}, "regla_incompleta"),
    ],
)
def test_cada_campo_se_valida_con_su_motivo(edificio, motor_migrate, motor_app, cambio, motivo):
    e = edificio
    otro, _, persona = consorcio_con_persona(motor_migrate)
    valores = {"persona": persona, "otra": decidir_equipo(motor_app, otro)}
    kw = {"decision": e["acta"]} | {k: valores.get(v, v) for k, v in cambio.items()}
    with pytest.raises(DBAPIError) as error:
        registrar(motor_app, e["cons"], porcentajes(e, *EXPENSAS), **kw)
    assert motivo_de(error) == motivo
    assert eventos(motor_migrate, e["cons"], "consorcio.coeficientes.registrar") == []


@pytest.mark.parametrize(
    ("armar", "motivo"),
    [
        (lambda e: None, "coeficientes_invalidos"),
        (lambda e: {}, "coeficientes_invalidos"),
        (lambda e: [EXPENSAS], "coeficientes_invalidos"),
        (lambda e: porcentajes(e, *EXPENSAS) | {"no-es-un-uuid": "1.0"}, "unidad_invalida"),
        (lambda e: porcentajes(e, *EXPENSAS) | {e["ajena"]: "1.0"}, "unidad_invalida"),
        (lambda e: porcentajes(e, *EXPENSAS[:4], 17.48), "porcentaje_invalido"),  # número JSON
        (lambda e: porcentajes(e, *EXPENSAS[:4], "17.4800001"), "porcentaje_invalido"),
        (lambda e: porcentajes(e, *EXPENSAS[:4], "-1"), "porcentaje_invalido"),
        (lambda e: porcentajes(e, *EXPENSAS[:4], "100.000001"), "porcentaje_invalido"),
        (lambda e: porcentajes(e, *EXPENSAS[:4], "1e2"), "porcentaje_invalido"),
        (lambda e: dict(list(porcentajes(e, *EXPENSAS).items())[:4]), "coeficientes_incompletos"),
        (lambda e: porcentajes(e, *(["0"] * 5)), "esquema_sin_pesos"),
    ],
)
def test_los_coeficientes_se_validan_con_su_motivo(edificio, motor_migrate, motor_app, armar,
                                                    motivo):  # fmt: skip
    e = edificio
    _, ajena, _ = consorcio_con_persona(motor_migrate)
    e = e | {"ajena": str(ajena)}
    with pytest.raises(DBAPIError) as error:
        registrar(motor_app, e["cons"], armar(e), decision=e["acta"])
    assert motivo_de(error) == motivo


def test_una_unidad_dada_de_baja_no_hace_falta_ni_se_admite(edificio, motor_migrate, motor_app):
    e = edificio
    with motor_migrate.begin() as c:
        c.execute(text("update nucleo.unidad set eliminado_en = now() where id = :u"),
                  {"u": e["u"][4]})  # fmt: skip
    with pytest.raises(DBAPIError) as error:
        registrar(motor_app, e["cons"], porcentajes(e, *EXPENSAS), decision=e["acta"])
    assert motivo_de(error) == "unidad_invalida"
    sin_baja = dict(list(porcentajes(e, *EXPENSAS).items())[:4])
    assert registrar(motor_app, e["cons"], sin_baja, decision=e["acta"])  # control


def test_sin_consorcio_en_el_ambito(edificio, motor_app):
    e = edificio
    with pytest.raises(DBAPIError) as error:
        registrar(motor_app, None, porcentajes(e, *EXPENSAS), decision=e["acta"])
    assert error.value.orig.sqlstate == "42501"


# Permisos y RLS -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sentencia",
    [
        "update nucleo.coeficiente_juridico set porcentaje = 20",
        "delete from nucleo.coeficiente_juridico",
        "update nucleo.esquema_coeficientes set vigente_desde = '2020-01-01'",
        "delete from nucleo.regla_reparto",
    ],
)
def test_cem_app_no_modifica_nada_de_lo_registrado(edificio, motor_app, sentencia):
    e = edificio
    registrar(motor_app, e["cons"], porcentajes(e, *EXPENSAS), decision=e["acta"])
    with pytest.raises(ProgrammingError, match="permission denied"):
        with transaccion(motor_app, persona_id=None, consorcio_id=e["cons"]) as c:
            c.execute(text(sentencia))


def test_la_rls_solo_muestra_los_esquemas_del_consorcio(edificio, motor_migrate, motor_app):
    e = edificio
    registrar(motor_app, e["cons"], porcentajes(e, *EXPENSAS), decision=e["acta"])
    otro, _, _ = consorcio_con_persona(motor_migrate)
    for cons, esperado in ((otro, (0, 0, 0)), (e["cons"], (1, 5, 1))):
        with transaccion(motor_app, persona_id=None, consorcio_id=cons) as c:
            assert tuple(c.execute(text(
                "select (select count(*) from nucleo.esquema_coeficientes),"
                " (select count(*) from nucleo.coeficiente_juridico),"
                " (select count(*) from nucleo.regla_reparto)")).one()) == esperado  # fmt: skip


# Ida y vuelta ---------------------------------------------------------------------------------


def test_downgrade_con_un_esquema_se_niega_y_sin_el_baja(base_aparte):  # noqa: F811
    cfg, motor = base_aparte
    command.upgrade(cfg, "0021")
    cons, uni = _consorcio_y_unidad(motor)
    with motor.begin() as c:
        c.execute(text("select set_config('app.consorcio_id', :c, true)"), {"c": str(cons)})
        acta = c.execute(
            text("select nucleo.registrar_decision('asamblea', '2024-01-01', 'Aprueba',"
                 " 'Acta 1', null, 'soporte')")
        ).scalar_one()  # fmt: skip
        c.execute(
            text("select nucleo.registrar_esquema_coeficientes('expensas_comunes', 'Reglamento',"
                 " '2024-01-01', :a, cast(:co as jsonb), 'proporcional_a_coeficientes',"
                 " 'restos_mayores', null, 'soporte')"),
            {"a": acta, "co": json.dumps({str(uni): "99.96"})},
        )  # fmt: skip
    with pytest.raises(DBAPIError) as error:
        command.downgrade(cfg, "0020")
    assert motivo_de(error) == "downgrade_con_datos"
    with motor.begin() as c:  # control: sin esquemas, baja
        c.execute(text("delete from nucleo.regla_reparto"))
        c.execute(text("delete from nucleo.coeficiente_juridico"))
        c.execute(text("delete from nucleo.esquema_coeficientes"))
    command.downgrade(cfg, "0020")
    with motor.connect() as c:
        assert c.execute(text("select to_regclass('nucleo.esquema_coeficientes')")).scalar() is None


# El script del equipo -------------------------------------------------------------------------


def planilla(e, valores, designaciones=None) -> str:
    nombres = designaciones or ["1° A", "2° A", "3° A", "4° A", "5° A"]
    return "designacion,porcentaje\n" + "".join(
        f"{d},{v}\n" for d, v in zip(nombres, valores, strict=True)
    )


@pytest.fixture
def correr(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("CEM_DATABASE_URL", url_rol("app"))

    def _correr(e, contenido, *extra):
        ruta = tmp_path / "coeficientes.csv"
        ruta.write_text(contenido, encoding="utf-8")
        codigo = cargar_coeficientes.main(
            [
                "--consorcio",
                str(e["cons"]),
                "--grupo",
                "expensas_comunes",
                "--fuente",
                FUENTE,
                "--desde",
                "2024-01-01",
                "--decision",
                str(e["acta"]),
                "--archivo",
                str(ruta),
                *extra,
            ]  # fmt: skip
        )
        salida = capsys.readouterr()
        return codigo, salida.out, salida.err

    return _correr


def test_el_script_carga_la_planilla_tal_cual(edificio, motor_migrate, correr):
    e = edificio
    codigo, out, err = correr(e, planilla(e, EXPENSAS))
    assert codigo == 0, err
    datos = json.loads(out)
    assert (datos["version"], datos["suma"], datos["grupo"]) == (1, "99.960000", "expensas_comunes")
    assert guardados(motor_migrate, datos["esquema_id"]) == {
        k: D(v) for k, v in porcentajes(e, *EXPENSAS).items()
    }


@pytest.mark.parametrize(
    ("contenido", "codigo", "mensaje"),
    [
        ("unidad,coeficiente\n1° A,10\n", 2, "encabezado"),
        ("designacion,porcentaje\n", 2, "no tiene filas"),
        ("designacion,porcentaje\n1° A,10\n1° A,20\n", 2, "repetidas"),
        ("designacion,porcentaje\n9° Z,10\n", 2, "no están en el consorcio"),
        ("designacion,porcentaje\n1° A,10,x\n", 2, "columnas"),
        ("designacion,porcentaje\n1° A,10\n", 1, "coeficientes_incompletos"),
        (
            "designacion,porcentaje\n1° A,20.1234567\n2° A,1\n3° A,1\n4° A,1\n5° A,1\n",
            1,
            "porcentaje_invalido",
        ),
    ],
)
def test_el_script_no_escribe_nada_si_la_planilla_no_sirve(edificio, motor_migrate, correr,
                                                           contenido, codigo, mensaje):  # fmt: skip
    e = edificio
    resultado, _, err = correr(e, contenido)
    assert (resultado, mensaje in err) == (codigo, True), err
    with motor_migrate.connect() as c:
        n = c.execute(
            text("select count(*) from nucleo.esquema_coeficientes where consorcio_id = :c"),
            {"c": e["cons"]},
        ).scalar_one()
    assert n == 0
