"""Condición habilitante para integrar el Consejo (E62, migración 0023) contra PostgreSQL real.

Decisión 4 del propietario (21-10-2026) y sus tres definiciones: cuenta cualquier condición
habilitante del catálogo, la pérdida se calcula en cada pedido desde el padrón, y sin ninguna
condición queda sin efecto todo el mandato de Consejo, que conserva su historia y queda pendiente
de revisión. Las pruebas que pidió:

- conserva la historia;
- no altera retroactivamente los actos previos;
- pierde la autorización desde que deja de existir la condición;
- recuperarla no reactiva en silencio el mandato;
- niega ante los estados ambiguos.

Cada caso lleva su control: el mismo pedido o la misma consulta en el estado que sí habilita.
La escena le da a la persona del Consejo, además, un vínculo de inquilina, que no habilita: así
conserva el ámbito por ese vínculo y se ve que lo que pierde es el mandato entero (sus
facultades), no el acceso al consorcio.
"""

from datetime import date, timedelta
from uuid import UUID

import pytest
from sqlalchemy import text

from app.db import transaccion
from tests.test_contexto_mandatos import cliente
from tests.test_mandatos import otra_persona, sesiones_abiertas
from tests.test_mandatos_0012 import DESDE, alta, baja
from tests.test_modo_operacion import consorcio_con_persona, decidir_equipo
from tests.test_vinculo_fin_0020 import finalizar

pytestmark = pytest.mark.db

ESCRITURA = "Escritura de prueba"


def unidad(motor_migrate, cons: UUID, designacion: str) -> UUID:
    with motor_migrate.begin() as c:
        return c.execute(
            text(
                "insert into nucleo.unidad (consorcio_id, designacion) values (:c, :d) returning id"
            ),
            {"c": cons, "d": designacion},
        ).scalar_one()


def vinculo(motor_migrate, cons: UUID, uni: UUID, persona: UUID, *, tipo="propietario",
            desde=date(2020, 1, 1), hasta=None, fuente="documento",
            confirma: UUID | None = None, confirmado: date | None = None) -> UUID:  # fmt: skip
    """Un vínculo cargado por el equipo; con `confirma`, confirmado por esa persona el día
    `confirmado` (por omisión, el día en que empieza a regir)."""
    with motor_migrate.begin() as c:
        return c.execute(
            text(
                "insert into nucleo.vinculo (consorcio_id, unidad_id, persona_id, tipo,"
                " vigente_desde, vigente_hasta, fuente, origen, confirmado_por, confirmado_en,"
                " confirmacion_referencia)"
                " values (:c, :u, :p, :t, :d, :h, :f, 'soporte', cast(:k as uuid),"
                " case when cast(:k as uuid) is null then null else cast(:cf as timestamptz) end,"
                " case when cast(:k as uuid) is null then null else :r end) returning id"
            ),
            {
                "c": cons,
                "u": uni,
                "p": persona,
                "t": tipo,
                "d": desde,
                "h": hasta,
                "f": fuente,
                "k": confirma,
                "r": ESCRITURA,
                "cf": confirmado or desde,
            },  # fmt: skip
        ).scalar_one()


def hoy(motor_migrate, cons: UUID) -> date:
    with motor_migrate.connect() as c:
        return c.execute(text("select nucleo.hoy_del_consorcio(:c)"), {"c": cons}).scalar_one()


def opera(motor_migrate, mandato: UUID, fecha: date) -> bool:
    with motor_migrate.connect() as c:
        return c.execute(
            text("select nucleo.mandato_vigente(:m, :f)"), {"m": mandato, "f": fecha}
        ).scalar_one()


def fila_mandato(motor_migrate, mandato: UUID) -> dict:
    with motor_migrate.connect() as c:
        fila = c.execute(
            text(
                "select persona_id, vigente_desde, vigente_hasta, causa_baja, finalizado_en,"
                " decision_id, (select array_agg(facultad order by facultad)"
                "   from nucleo.facultad_otorgada f where f.mandato_id = m.id) as facultades"
                " from nucleo.mandato m where m.id = :m"
            ),
            {"m": mandato},
        ).one()
    return dict(fila._mapping)


@pytest.fixture
def consejo(motor_migrate, motor_app):
    """Consorcio con:

    - una administradora sin vínculo (lee los mandatos);
    - una persona del Consejo, propietaria confirmada de la 3° C desde 2020 e inquilina sin
      confirmar de la 4° D;
    - su mandato de Consejo desde el 01-01-2026.
    """
    cons, _, prop = consorcio_con_persona(motor_migrate)
    acta = decidir_equipo(motor_app, cons)
    adm = otra_persona(motor_migrate, cons)
    alta(motor_app, cons, None, acta, persona=adm, origen="soporte")
    mc = otra_persona(motor_migrate, cons)
    v_prop = vinculo(motor_migrate, cons, unidad(motor_migrate, cons, "3° C"), mc, confirma=prop)
    vinculo(motor_migrate, cons, unidad(motor_migrate, cons, "4° D"), mc, tipo="inquilino")
    m = alta(
        motor_app,
        cons,
        None,
        acta,
        tipo="miembro_consejo",
        persona=mc,
        origen="soporte",
        con_vinculo=False,
    )
    return {"cons": cons, "acta": acta, "adm": adm, "mc": mc, "prop": prop, "v_prop": v_prop,
            "m": m, "hoy": hoy(motor_migrate, cons)}  # fmt: skip


def padron(persona: UUID, cons: UUID) -> int:
    with cliente(persona) as c:
        return c.get(f"/api/v1/consorcios/{cons}/padron").status_code


def estado(adm: UUID, cons: UUID, mandato: UUID) -> str:
    with cliente(adm) as c:
        r = c.get(f"/api/v1/consorcios/{cons}/mandatos/{mandato}")
        assert r.status_code == 200, r.text
        listado = c.get(f"/api/v1/consorcios/{cons}/mandatos").json()["elementos"]
    [en_lista] = [x["estado"] for x in listado if x["id"] == str(mandato)]
    assert en_lista == r.json()["estado"]
    return en_lista


# Pierde la autorización desde que deja de existir la condición, y conserva la historia --------


def test_al_perder_la_condicion_el_mandato_entero_queda_sin_efecto_y_conserva_su_historia(
    consejo, motor_migrate, motor_app
):
    e = consejo
    antes = fila_mandato(motor_migrate, e["m"])
    # Control: con la condición, el mandato opera, da padron.ver y se lee vigente.
    assert opera(motor_migrate, e["m"], e["hoy"])
    assert padron(e["mc"], e["cons"]) == 200
    assert estado(e["adm"], e["cons"], e["m"]) == "vigente"

    finalizar(motor_app, e["cons"], e["adm"], e["v_prop"])

    assert not opera(motor_migrate, e["m"], e["hoy"])
    # Sigue entrando por su vínculo de inquilina, pero el mandato ya no le da ninguna facultad.
    assert padron(e["mc"], e["cons"]) == 403
    with cliente(e["mc"]) as c:
        assert c.get(f"/api/v1/consorcios/{e['cons']}/unidades").status_code == 200
    assert estado(e["adm"], e["cons"], e["m"]) == "pendiente_revision"
    # La fila no cambia: ni fechas, ni baja, ni facultades.
    assert fila_mandato(motor_migrate, e["m"]) == antes
    assert antes["vigente_hasta"] is None and antes["causa_baja"] is None
    assert "padron.ver" in antes["facultades"]


def test_quien_queda_solo_con_un_mandato_sin_efecto_pierde_sus_sesiones(motor_migrate, motor_app):
    cons, _, prop = consorcio_con_persona(motor_migrate)
    acta = decidir_equipo(motor_app, cons)
    adm = otra_persona(motor_migrate, cons)
    alta(motor_app, cons, None, acta, persona=adm, origen="soporte")
    mc = otra_persona(motor_migrate, cons)
    v = vinculo(motor_migrate, cons, unidad(motor_migrate, cons, "5° E"), mc, confirma=prop)
    alta(
        motor_app,
        cons,
        None,
        acta,
        tipo="miembro_consejo",
        persona=mc,
        origen="soporte",
        con_vinculo=False,
    )
    abiertas = sesiones_abiertas(motor_migrate, mc, 2)
    assert finalizar(motor_app, cons, adm, v) == abiertas == 2
    assert sesiones_abiertas(motor_migrate, mc) == 0
    with cliente(mc) as c:
        assert c.get(f"/api/v1/consorcios/{cons}/unidades").status_code == 404


def test_la_condicion_cuenta_desde_el_dia_en_que_se_pierde(consejo, motor_migrate):
    """Un vínculo que terminó por su fecha: el mandato operó hasta el día anterior."""
    e = consejo
    fin = e["hoy"] - timedelta(days=10)
    with motor_migrate.begin() as c:
        c.execute(
            text("update nucleo.vinculo set vigente_hasta = :h where id = :v"),
            {"h": fin, "v": e["v_prop"]},
        )
    assert opera(motor_migrate, e["m"], fin - timedelta(days=1))
    assert not opera(motor_migrate, e["m"], fin)
    assert not opera(motor_migrate, e["m"], e["hoy"])


# No altera retroactivamente los actos previos ---------------------------------------------------


def test_los_actos_previos_siguen_valiendo_y_la_fecha_anterior_no_cambia(
    consejo, motor_migrate, motor_app
):
    e = consejo
    with transaccion(motor_app, persona_id=e["mc"], consorcio_id=e["cons"]) as c:
        previa = c.execute(
            text(
                "select nucleo.registrar_decision('consejo', :f, 'Revisa el presupuesto',"
                " 'Acta del Consejo 3', :p, 'api')"
            ),
            {"f": e["hoy"], "p": e["mc"]},
        ).scalar_one()
    ayer = e["hoy"] - timedelta(days=1)
    assert opera(motor_migrate, e["m"], ayer)

    finalizar(motor_app, e["cons"], e["adm"], e["v_prop"])

    # La evaluación en una fecha anterior a la pérdida no cambia.
    assert opera(motor_migrate, e["m"], ayer)
    assert not opera(motor_migrate, e["m"], e["hoy"])
    # La decisión que registró sigue ahí, con su autor, y sirve para un alta nueva.
    with motor_migrate.connect() as c:
        autor = c.execute(
            text("select registrada_por from nucleo.decision_formal where id = :d"),
            {"d": previa},
        ).scalar_one()
    assert autor == e["mc"]
    otra = otra_persona(motor_migrate, e["cons"])
    nuevo = alta(motor_app, e["cons"], None, previa, tipo="miembro_consejo", persona=otra,
                 origen="soporte")  # fmt: skip
    assert fila_mandato(motor_migrate, nuevo)["decision_id"] == previa


# Recuperarla no reactiva el mandato -------------------------------------------------------------


@pytest.mark.parametrize(
    ("dias_de_hueco", "opera_hoy"),
    [(9, False), (0, True)],
    ids=["con-hueco-sigue-pendiente", "control-contiguo-no-corta"],
)
def test_recuperar_la_condicion_no_reactiva_el_mandato(
    consejo, motor_migrate, dias_de_hueco, opera_hoy
):
    """Vende la 3° C el 01-03-2026 y compra la 6° F: con días sin ser propietaria, el mandato
    sigue pendiente aunque hoy vuelva a cumplir; si la compra es el mismo día, nunca la perdió."""
    e = consejo
    venta = date(2026, 3, 1)
    with motor_migrate.begin() as c:
        c.execute(
            text("update nucleo.vinculo set vigente_hasta = :h where id = :v"),
            {"h": venta, "v": e["v_prop"]},
        )
    vinculo(motor_migrate, e["cons"], unidad(motor_migrate, e["cons"], "6° F"), e["mc"],
            desde=venta + timedelta(days=dias_de_hueco), confirma=e["prop"])  # fmt: skip
    assert opera(motor_migrate, e["m"], e["hoy"]) is opera_hoy
    assert padron(e["mc"], e["cons"]) == (200 if opera_hoy else 403)
    esperado = "vigente" if opera_hoy else "pendiente_revision"
    assert estado(e["adm"], e["cons"], e["m"]) == esperado


def test_un_mandato_nuevo_con_su_decision_resuelve_la_continuidad(
    consejo, motor_migrate, motor_app
):
    """La decisión competente resuelve: baja del mandato pendiente y alta nueva desde hoy."""
    e = consejo
    venta = date(2026, 3, 1)
    with motor_migrate.begin() as c:
        c.execute(
            text("update nucleo.vinculo set vigente_hasta = :h where id = :v"),
            {"h": venta, "v": e["v_prop"]},
        )
    vinculo(motor_migrate, e["cons"], unidad(motor_migrate, e["cons"], "6° F"), e["mc"],
            desde=date(2026, 4, 1), confirma=e["prop"])  # fmt: skip
    assert not opera(motor_migrate, e["m"], e["hoy"])
    baja(motor_app, e["cons"], None, e["m"], "otro", instrumento="Nota de revisión del Consejo",
         descripcion="Revisión por pérdida de la condición", origen="soporte")  # fmt: skip
    nuevo = alta(motor_app, e["cons"], None, decidir_equipo(motor_app, e["cons"]),
                 tipo="miembro_consejo", persona=e["mc"], desde=e["hoy"],
                 origen="soporte", con_vinculo=False)  # fmt: skip
    assert opera(motor_migrate, nuevo, e["hoy"])
    assert padron(e["mc"], e["cons"]) == 200


def test_una_confirmacion_tardia_no_tapa_el_hueco(consejo, motor_migrate):
    """Vende la 3° C el 01-03-2026; la 6° F rige desde ese día, pero se confirma el 01-06-2026.
    Entre una y otra no tuvo ningún vínculo que habilitara: el mandato queda pendiente (Codex
    en app #102). Control: confirmada el mismo día de la venta, no hay hueco."""
    e = consejo
    venta = date(2026, 3, 1)
    with motor_migrate.begin() as c:
        c.execute(
            text("update nucleo.vinculo set vigente_hasta = :h where id = :v"),
            {"h": venta, "v": e["v_prop"]},
        )
    tardio = vinculo(motor_migrate, e["cons"], unidad(motor_migrate, e["cons"], "6° F"), e["mc"],
                     desde=venta, confirma=e["prop"], confirmado=date(2026, 6, 1))  # fmt: skip
    assert not opera(motor_migrate, e["m"], e["hoy"])
    with motor_migrate.begin() as c:
        c.execute(
            text("update nucleo.vinculo set confirmado_en = :f where id = :v"),
            {"f": venta, "v": tardio},
        )
    assert opera(motor_migrate, e["m"], e["hoy"])


def test_una_aceptacion_que_reutiliza_el_vinculo_lo_habilita_desde_ese_dia(consejo, motor_migrate):
    """`aceptar_invitacion` reutiliza un vínculo de la misma persona, unidad y carácter sin
    cambiarle la fuente (Codex en app #102): la aceptación habilita desde su día. Sin la
    invitación aceptada, el vínculo cargado por documento sin confirmar no cuenta."""
    e = consejo
    venta = date(2026, 3, 1)
    with motor_migrate.begin() as c:
        c.execute(
            text("update nucleo.vinculo set vigente_hasta = :h where id = :v"),
            {"h": venta, "v": e["v_prop"]},
        )
    uni = unidad(motor_migrate, e["cons"], "6° F")
    vinculo(motor_migrate, e["cons"], uni, e["mc"], desde=venta)
    assert not opera(motor_migrate, e["m"], e["hoy"])  # control: sin aceptar ni confirmar
    with motor_migrate.begin() as c:
        c.execute(
            text(
                "insert into nucleo.invitacion (consorcio_id, unidad_id, tipo, correo, nombre,"
                " token_hash, creada_en, vence_en, aceptada_en, persona_id)"
                " values (:c, :u, 'propietario', :correo, 'Consejo', sha256(gen_random_uuid()"
                "::text::bytea), :a, cast(:a as timestamptz) + interval '1 day',"
                " cast(:a as timestamptz) + interval '1 hour', :p)"
            ),
            {
                "c": e["cons"],
                "u": uni,
                "correo": f"mc-{uni.hex[:8]}@ejemplo.com",
                "a": "2026-03-01 12:00-03",
                "p": e["mc"],
            },  # fmt: skip
        )
    assert opera(motor_migrate, e["m"], e["hoy"])


# Negar ante la duda -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("caso", "opera_esperado"),
    [
        ("propietaria-confirmada", True),
        ("propietaria-por-invitacion", True),
        ("importada-sin-confirmar", False),
        ("documento-sin-confirmar", False),
        ("inquilina-confirmada", False),
        ("unidad-dada-de-baja", False),
    ],
)
def test_solo_un_vinculo_habilitante_aceptado_o_confirmado_cuenta(
    motor_migrate, motor_app, caso, opera_esperado
):
    cons, _, prop = consorcio_con_persona(motor_migrate)
    mc = otra_persona(motor_migrate, cons)
    uni = unidad(motor_migrate, cons, "7° G")
    tipo = "inquilino" if caso == "inquilina-confirmada" else "propietario"
    fuente = {"propietaria-por-invitacion": "invitacion",
              "importada-sin-confirmar": "importacion"}.get(caso, "documento")  # fmt: skip
    confirma = prop if caso in ("propietaria-confirmada", "inquilina-confirmada",
                                "unidad-dada-de-baja") else None  # fmt: skip
    vinculo(motor_migrate, cons, uni, mc, tipo=tipo, fuente=fuente, confirma=confirma)
    if caso == "unidad-dada-de-baja":
        with motor_migrate.begin() as c:
            c.execute(text("update nucleo.unidad set eliminado_en = now() where id = :u"),
                      {"u": uni})  # fmt: skip
    m = alta(motor_app, cons, None, decidir_equipo(motor_app, cons), tipo="miembro_consejo",
             persona=mc, origen="soporte", con_vinculo=False)  # fmt: skip
    assert opera(motor_migrate, m, hoy(motor_migrate, cons)) is opera_esperado


def test_sin_el_caracter_en_el_catalogo_nadie_opera(consejo, motor_migrate):
    e = consejo
    with motor_migrate.connect() as c, c.begin():
        assert c.execute(
            text("select nucleo.mandato_vigente(:m, :f)"), {"m": e["m"], "f": e["hoy"]}
        ).scalar_one()  # control, en la misma transacción
        c.execute(text("delete from nucleo.condicion_habilitante_consejo"))
        assert not c.execute(
            text("select nucleo.mandato_vigente(:m, :f)"), {"m": e["m"], "f": e["hoy"]}
        ).scalar_one()
        c.rollback()
    assert opera(motor_migrate, e["m"], e["hoy"])


def test_sin_ambito_cem_app_no_ve_nada_y_niega(consejo, motor_app):
    """Como `cem_app` sin consorcio en el ámbito ni persona, la RLS esconde el mandato y sus
    vínculos: la respuesta es negar, nunca un error que alguien trate como permiso."""
    e = consejo
    consulta = text("select nucleo.mandato_vigente(:m, :f)")
    with transaccion(motor_app, persona_id=e["adm"], consorcio_id=e["cons"]) as c:
        assert c.execute(consulta, {"m": e["m"], "f": e["hoy"]}).scalar_one()  # control
    with transaccion(motor_app, persona_id=None) as c:
        assert not c.execute(consulta, {"m": e["m"], "f": e["hoy"]}).scalar_one()


def test_los_otros_tipos_de_mandato_no_cambian(consejo, motor_migrate):
    """La administradora no tiene vínculo: su mandato sigue operando."""
    e = consejo
    with motor_migrate.connect() as c:
        m_adm = c.execute(
            text("select id from nucleo.mandato where persona_id = :p"), {"p": e["adm"]}
        ).scalar_one()
    assert opera(motor_migrate, m_adm, e["hoy"])


def test_el_catalogo_dice_la_fuente_y_cem_app_no_lo_escribe(motor_app, motor_migrate):
    with motor_migrate.connect() as c:
        filas = c.execute(
            text("select tipo::text, fuente from nucleo.condicion_habilitante_consejo")
        ).all()
    assert [f.tipo for f in filas] == ["propietario"]
    assert "art. 2064" in filas[0].fuente
    with pytest.raises(Exception, match="permission denied"):
        with transaccion(motor_app, persona_id=None) as c:
            c.execute(text("insert into nucleo.condicion_habilitante_consejo values"
                           " ('inquilino', 'cualquiera')"))  # fmt: skip


def test_el_alta_desde_el_inicio_del_mandato_no_exige_la_condicion_previa(motor_migrate, motor_app):
    """Un mandato que empezó antes de que la persona figure en el padrón opera desde que
    figura: la condición se exige sin interrupción desde que se cumple por primera vez."""
    cons, _, prop = consorcio_con_persona(motor_migrate)
    mc = otra_persona(motor_migrate, cons)
    vinculo(motor_migrate, cons, unidad(motor_migrate, cons, "8° H"), mc,
            desde=date(2026, 6, 1), confirma=prop)  # fmt: skip
    m = alta(motor_app, cons, None, decidir_equipo(motor_app, cons), tipo="miembro_consejo",
             persona=mc, desde=DESDE, origen="soporte", con_vinculo=False)  # fmt: skip
    assert not opera(motor_migrate, m, date(2026, 5, 31))
    assert opera(motor_migrate, m, hoy(motor_migrate, cons))
