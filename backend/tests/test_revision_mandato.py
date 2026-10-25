"""Revisión de un mandato del Consejo pendiente de revisión (E62, alternativa 1) contra PostgreSQL.

Diseño: `doc/diseno/Decisiones_Propietario_2026-10-21.md` del repositorio CEM, §2.3. La revisión
es un acto sobre el mismo mandato, respaldado por una asamblea del mismo consorcio con fecha
hasta hoy, que le da eficacia desde `eficaz_desde` hacia adelante. Las pruebas mínimas que fijó
el propietario:

- pérdida → `pendiente_revision`, y recuperar el vínculo sin revisión no lo reactiva;
- una revisión válida opera solo desde `eficaz_desde`, y el período intermedio sigue negado;
- se rechaza con una decisión que no es de asamblea, de otro consorcio o con fecha futura, sobre
  el mandato propio (A4), con una decisión que registró el mismo actor (A15), con `eficaz_desde`
  anterior al registro, sin la condición ese día, y sobre un mandato que opera o que terminó;
- una pérdida posterior deja el mandato pendiente otra vez, y otra revisión abre otro tramo;
- ningún acto anterior se recalcula y la fila del mandato no cambia.

Cada rechazo lleva su control: el mismo pedido con el dato que sí corresponde pasa.
"""

from datetime import date, timedelta
from uuid import UUID

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db import transaccion
from tests.conftest import clave_idempotencia, misma_identidad
from tests.test_condicion_habilitante_consejo import (
    consejo,  # noqa: F401 (fixture)
    estado,
    fila_mandato,
    opera,
    padron,
    unidad,
    vinculo,
)
from tests.test_contexto_mandatos import cliente, denegaciones
from tests.test_mandatos import otra_persona
from tests.test_mandatos_0012 import CUIT_X, alta, baja
from tests.test_modo_operacion import consorcio_con_persona, decidir, decidir_equipo
from tests.test_vinculo_0014 import (  # noqa: F401  (`base_aparte` es un fixture)
    _consorcio_y_unidad,
    _persona,
    base_aparte,
)

pytestmark = [pytest.mark.db, pytest.mark.redis]


def revisar_http(persona, cons, mandato, decision, eficaz: date, clave=None):
    with cliente(persona) as c:
        return c.post(
            f"/api/v1/consorcios/{cons}/mandatos/{mandato}/revision",
            json={"decision_id": str(decision), "eficaz_desde": str(eficaz)},
            headers=clave or clave_idempotencia(),
        )


def revisar_soporte(motor_app, cons, mandato, decision, eficaz: date, actor=None):
    with transaccion(motor_app, persona_id=None, consorcio_id=cons) as c:
        return c.execute(
            text("select nucleo.resolver_revision_mandato(:m, :d, :e, :a, 'soporte')"),
            {"m": mandato, "d": decision, "e": eficaz, "a": actor},
        ).scalar_one()


def cortar(motor_migrate, vinculo_id: UUID, hasta: date) -> None:
    with motor_migrate.begin() as c:
        c.execute(
            text("update nucleo.vinculo set vigente_hasta = :h where id = :v"),
            {"h": hasta, "v": vinculo_id},
        )


def decision_directa(motor_migrate, cons, *, tipo="asamblea", fecha: date) -> UUID:
    """Una decisión escrita como el dueño, sin `registrar_decision`: la única forma de tener una
    con fecha futura (la función la rechaza, 0019) para probar la segunda barrera."""
    with motor_migrate.begin() as c:
        return c.execute(
            text(
                "insert into nucleo.decision_formal (consorcio_id, tipo, fecha, descripcion,"
                " referencia_acta, origen) values (:c, :t, :f, 'Revisa el Consejo',"
                " 'Acta 20, libro 3, folio 70', 'soporte') returning id"
            ),
            {"c": cons, "t": tipo, "f": fecha},
        ).scalar_one()


def revisiones(motor_migrate, mandato) -> list[date]:
    with motor_migrate.connect() as c:
        return list(
            c.execute(
                text(
                    "select eficaz_desde from nucleo.revision_mandato where mandato_id = :m"
                    " order by eficaz_desde"
                ),
                {"m": mandato},
            ).scalars()
        )


@pytest.fixture
def pendiente(consejo, motor_migrate, motor_app):  # noqa: F811
    """La escena de `consejo`, con:

    - la 3° C vendida hace 30 días y la 6° F comprada (y confirmada) hace 10: un hueco, así que
      el mandato queda pendiente aunque hoy vuelva a cumplir la condición;
    - otra integrante del Consejo, `mc2`, con su condición y su mandato, que opera.
    """
    e = dict(consejo)
    hoy = e["hoy"]
    e["venta"] = hoy - timedelta(days=30)
    e["compra"] = hoy - timedelta(days=10)
    cortar(motor_migrate, e["v_prop"], e["venta"])
    e["v_nuevo"] = vinculo(motor_migrate, e["cons"], unidad(motor_migrate, e["cons"], "6° F"),
                           e["mc"], desde=e["compra"], confirma=e["prop"])  # fmt: skip
    e["mc2"] = otra_persona(motor_migrate, e["cons"])
    e["m_mc2"] = alta(motor_app, e["cons"], None, e["acta"], tipo="miembro_consejo",
                      persona=e["mc2"], origen="soporte")  # fmt: skip
    return e


def _motivos(motor_app, e, persona) -> list[tuple[str, str]]:
    return [
        (d["accion"], d["motivo"]) for d in denegaciones(motor_app, e["cons"], e["adm"], persona)
    ]


# Pérdida, recuperación sin revisión y revisión válida -----------------------------------------


def test_recuperar_sin_revision_sigue_pendiente_y_la_revision_opera_solo_desde_su_fecha(
    pendiente, motor_migrate
):
    e = pendiente
    hoy, eficaz = e["hoy"], e["hoy"] + timedelta(days=3)
    antes = fila_mandato(motor_migrate, e["m"])
    # Pérdida y recuperación sin revisión: pendiente, aunque hoy cumpla la condición.
    assert opera(motor_migrate, e["m"], e["venta"] - timedelta(days=1))
    assert not opera(motor_migrate, e["m"], hoy)
    assert estado(e["adm"], e["cons"], e["m"]) == "pendiente_revision"

    clave = clave_idempotencia()
    r = revisar_http(e["adm"], e["cons"], e["m"], e["acta"], eficaz, clave)
    assert r.status_code == 201, r.text
    assert r.json()["mandato_id"] == str(e["m"]) and r.json()["eficaz_desde"] == str(eficaz)
    repetido = revisar_http(e["adm"], e["cons"], e["m"], e["acta"], eficaz, clave)
    assert (repetido.status_code, repetido.json()) == (201, r.json())
    assert revisiones(motor_migrate, e["m"]) == [eficaz]

    # Opera desde `eficaz_desde`, nunca antes: hoy y los días que faltan siguen negados.
    assert not opera(motor_migrate, e["m"], hoy)
    assert not opera(motor_migrate, e["m"], eficaz - timedelta(days=1))
    assert opera(motor_migrate, e["m"], eficaz)
    assert opera(motor_migrate, e["m"], eficaz + timedelta(days=30))
    assert estado(e["adm"], e["cons"], e["m"]) == "pendiente_revision"
    assert padron(e["mc"], e["cons"]) == 403
    # El pasado no se recalcula: antes de la venta operaba, el hueco sigue sin efecto.
    assert opera(motor_migrate, e["m"], e["venta"] - timedelta(days=1))
    assert not opera(motor_migrate, e["m"], e["venta"] + timedelta(days=1))
    assert not opera(motor_migrate, e["m"], e["compra"])
    # La fila del mandato no cambia.
    assert fila_mandato(motor_migrate, e["m"]) == antes


def test_una_revision_desde_hoy_devuelve_el_mandato_entero_y_queda_auditada(
    pendiente, motor_migrate
):
    e = pendiente
    assert padron(e["mc"], e["cons"]) == 403  # control: pendiente, sin facultades
    r = revisar_http(e["adm"], e["cons"], e["m"], e["acta"], e["hoy"])
    assert r.status_code == 201, r.text
    assert opera(motor_migrate, e["m"], e["hoy"])
    assert padron(e["mc"], e["cons"]) == 200
    assert estado(e["adm"], e["cons"], e["m"]) == "vigente"
    with motor_migrate.connect() as c:
        evento = c.execute(
            text(
                "select actor_persona_id, origen, antes, despues from auditoria.eventos"
                " where accion = 'consorcio.mandato.revision' and entidad_id = :m"
            ),
            {"m": e["m"]},
        ).one()
    assert (evento.actor_persona_id, evento.origen) == (e["adm"], "api")
    assert evento.antes == {"estado": "pendiente_revision"}
    assert evento.despues == {"revision_id": r.json()["id"], "decision_id": str(e["acta"]),
                              "eficaz_desde": str(e["hoy"])}  # fmt: skip


# Rechazos ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "caso",
    ["no-es-asamblea", "otro-consorcio", "fecha-futura", "anterior-al-registro"],
)
def test_la_decision_y_la_fecha_tienen_que_corresponder(pendiente, motor_migrate, motor_app, caso):
    e = pendiente
    eficaz = e["hoy"]
    decision = e["acta"]
    if caso == "no-es-asamblea":
        decision = decidir_equipo(motor_app, e["cons"], tipo="consejo")
    elif caso == "otro-consorcio":
        otro, _, _ = consorcio_con_persona(motor_migrate)
        decision = decidir_equipo(motor_app, otro)
    elif caso == "fecha-futura":
        decision = decision_directa(motor_migrate, e["cons"], fecha=e["hoy"] + timedelta(days=1))
        eficaz = e["hoy"] + timedelta(days=1)
    else:
        eficaz = e["hoy"] - timedelta(days=1)
    r = revisar_http(e["adm"], e["cons"], e["m"], decision, eficaz)
    esperado = "eficaz_desde_invalida" if caso == "anterior-al-registro" else "decision_invalida"
    assert (r.status_code, r.json()["codigo"]) == (422, esperado), r.text
    assert revisiones(motor_migrate, e["m"]) == []
    # Control: la asamblea del consorcio, desde hoy.
    assert revisar_http(e["adm"], e["cons"], e["m"], e["acta"], e["hoy"]).status_code == 201


def test_a15_quien_registro_la_asamblea_no_la_usa_para_revisar(pendiente, motor_app, motor_migrate):
    e = pendiente
    propia = decidir(motor_app, e["cons"], e["adm"])
    r = revisar_http(e["adm"], e["cons"], e["m"], propia, e["hoy"])
    assert (r.status_code, r.json()["codigo"]) == (403, "sin_permiso")
    assert _motivos(motor_app, e, e["adm"]) == [
        ("mandatos.resolver_revision", "no_registro_la_decision")
    ]
    assert revisiones(motor_migrate, e["m"]) == []
    # Control: otra integrante del Consejo usa esa misma asamblea.
    r = revisar_http(e["mc2"], e["cons"], e["m"], propia, e["hoy"])
    assert r.status_code == 201, r.text


def test_a4_nadie_revisa_su_propio_mandato(pendiente, motor_app, motor_migrate):
    e = pendiente
    # Por la API: el mandato pendiente no le da ninguna facultad, así que ni llega a A4.
    r = revisar_http(e["mc"], e["cons"], e["m"], e["acta"], e["hoy"])
    assert r.status_code == 403
    assert _motivos(motor_app, e, e["mc"]) == [("mandatos.resolver_revision", "sin_concesion")]
    # La segunda barrera: la base lo niega aunque alguien la llame directo con su sesión.
    with pytest.raises(Exception, match="revision_de_si_mismo|nadie revisa su propio mandato"):
        with transaccion(motor_app, persona_id=e["mc"], consorcio_id=e["cons"]) as c:
            c.execute(
                text("select nucleo.resolver_revision_mandato(:m, :d, :e, :a, 'api')"),
                {"m": e["m"], "d": e["acta"], "e": e["hoy"], "a": e["mc"]},
            )
    assert revisiones(motor_migrate, e["m"]) == []
    # Control: el mismo pedido de la administradora pasa.
    assert revisar_http(e["adm"], e["cons"], e["m"], e["acta"], e["hoy"]).status_code == 201


def _revisar_en_la_base(motor_app, e, decision, actor) -> None:
    with transaccion(motor_app, persona_id=actor, consorcio_id=e["cons"]) as c:
        c.execute(
            text("select nucleo.resolver_revision_mandato(:m, :d, :e, :a, 'api')"),
            {"m": e["m"], "d": decision, "e": e["hoy"], "a": actor},
        )


def test_la_base_niega_revisar_entre_cuentas_del_mismo_ser_humano(
    pendiente, motor_app, motor_migrate
):
    """A4 y A15 de la revisión entre seres humanos (0026), sin el evaluador."""
    e = pendiente
    # A4: otra cuenta de quien tiene el mandato.
    segunda = otra_persona(motor_migrate, e["cons"], verificada=False)
    misma_identidad(motor_migrate, e["cons"], e["mc"], segunda)
    with pytest.raises(DBAPIError) as error:
        _revisar_en_la_base(motor_app, e, e["acta"], segunda)
    assert error.value.orig.diag.message_hint == "revision_de_si_mismo"
    # A15: la asamblea la registró otra cuenta de quien revisa.
    propia = decidir(motor_app, e["cons"], e["adm"])
    otra_del_adm = otra_persona(motor_migrate, e["cons"], verificada=False)
    misma_identidad(motor_migrate, e["cons"], e["adm"], otra_del_adm)
    with pytest.raises(DBAPIError) as error:
        _revisar_en_la_base(motor_app, e, propia, otra_del_adm)
    assert error.value.orig.diag.message_hint == "decision_registrada_por_el_actor"
    # Sin verificar quien revisa, tampoco.
    sin = otra_persona(motor_migrate, e["cons"], verificada=False)
    with pytest.raises(DBAPIError) as error:
        _revisar_en_la_base(motor_app, e, e["acta"], sin)
    assert error.value.orig.diag.message_hint == "identidad_no_verificada"
    assert revisiones(motor_migrate, e["m"]) == []
    # Control: otra integrante del Consejo, verificada, revisa con esa asamblea.
    _revisar_en_la_base(motor_app, e, propia, e["mc2"])
    assert revisiones(motor_migrate, e["m"]) == [e["hoy"]]


def test_sin_la_condicion_el_dia_de_eficacia_se_rechaza(pendiente, motor_migrate):
    """La 6° F se vende dentro de 5 días: la revisión desde el día 6 no queda en espera."""
    e = pendiente
    cortar(motor_migrate, e["v_nuevo"], e["hoy"] + timedelta(days=5))
    r = revisar_http(e["adm"], e["cons"], e["m"], e["acta"], e["hoy"] + timedelta(days=6))
    assert (r.status_code, r.json()["codigo"]) == (422, "sin_condicion_habilitante"), r.text
    assert revisiones(motor_migrate, e["m"]) == []
    # Control: el último día en que la cumple.
    r = revisar_http(e["adm"], e["cons"], e["m"], e["acta"], e["hoy"] + timedelta(days=4))
    assert r.status_code == 201, r.text


@pytest.mark.parametrize(
    "caso", ["opera", "administrador", "externo-sin-cuenta", "terminado", "dado-de-baja"]
)
def test_solo_un_mandato_del_consejo_pendiente_admite_la_revision(
    pendiente, motor_migrate, motor_app, caso
):
    e = pendiente
    if caso == "opera":
        mandato, esperado = e["m_mc2"], (409, "mandato_no_pendiente")
    elif caso == "administrador":
        with motor_migrate.connect() as c:
            mandato = c.execute(
                text("select id from nucleo.mandato where persona_id = :p"), {"p": e["adm"]}
            ).scalar_one()
        esperado = (409, "mandato_no_pendiente")
        r = revisar_http(e["mc2"], e["cons"], mandato, e["acta"], e["hoy"])
    elif caso == "externo-sin-cuenta":
        # Sin cuenta no hay identidad que comparar: la base no puede pedirla (revisión de 0026).
        with motor_migrate.connect() as c:
            del_adm = c.execute(
                text("select id from nucleo.mandato where persona_id = :p"), {"p": e["adm"]}
            ).scalar_one()
        baja(motor_app, e["cons"], None, del_adm, "error_carga", instrumento="Corrección",
             descripcion="Se cargó mal", origen="soporte")  # fmt: skip
        mandato = alta(motor_app, e["cons"], None, e["acta"], tipo="administrador_externo",
                       nombre="Estudio de prueba", cuit=CUIT_X, origen="soporte")  # fmt: skip
        esperado = (409, "mandato_no_pendiente")
        r = revisar_http(e["mc2"], e["cons"], mandato, e["acta"], e["hoy"])
    elif caso == "terminado":
        otra = otra_persona(motor_migrate, e["cons"])
        mandato = alta(motor_app, e["cons"], None, e["acta"], tipo="miembro_consejo",
                       persona=otra, hasta=e["hoy"], origen="soporte")  # fmt: skip
        esperado = (409, "mandato_terminado")
    else:
        baja(motor_app, e["cons"], None, e["m"], "renuncia", instrumento="Nota de renuncia",
             descripcion="Renuncia", origen="soporte")  # fmt: skip
        mandato, esperado = e["m"], (409, "mandato_ya_dado_de_baja")
    if caso not in ("administrador", "externo-sin-cuenta"):
        r = revisar_http(e["adm"], e["cons"], mandato, e["acta"], e["hoy"])
    assert (r.status_code, r.json()["codigo"]) == esperado, r.text
    assert revisiones(motor_migrate, mandato) == []


def test_dos_revisiones_desde_el_mismo_dia_no(pendiente, motor_migrate):
    e = pendiente
    dia = e["hoy"] + timedelta(days=3)
    assert revisar_http(e["adm"], e["cons"], e["m"], e["acta"], dia).status_code == 201
    r = revisar_http(e["adm"], e["cons"], e["m"], e["acta"], dia)
    assert (r.status_code, r.json()["codigo"]) == (409, "revision_duplicada")
    # Control: sigue pendiente hoy, y otro día sí se admite.
    assert (
        revisar_http(e["adm"], e["cons"], e["m"], e["acta"], dia + timedelta(days=1)).status_code
        == 201
    )
    assert revisiones(motor_migrate, e["m"]) == [dia, dia + timedelta(days=1)]


def test_sin_la_facultad_da_403_y_con_ella_un_mandato_inexistente_404(pendiente, motor_app):
    e = pendiente
    inexistente = "01a10000-0000-7000-8000-0000000000ff"
    r = revisar_http(e["prop"], e["cons"], inexistente, e["acta"], e["hoy"])
    assert (r.status_code, r.json()["codigo"]) == (403, "sin_permiso")
    assert revisar_http(e["prop"], e["cons"], e["m"], e["acta"], e["hoy"]).status_code == 403
    assert (
        _motivos(motor_app, e, e["prop"]) == [("mandatos.resolver_revision", "sin_concesion")] * 2
    )
    r = revisar_http(e["adm"], e["cons"], inexistente, e["acta"], e["hoy"])
    assert (r.status_code, r.json()["codigo"]) == (404, "no_encontrado")


# Tramos -----------------------------------------------------------------------------------------


def test_una_perdida_posterior_vuelve_a_dejarlo_pendiente_y_otra_revision_abre_otro_tramo(
    consejo,  # noqa: F811
    motor_migrate,
    motor_app,  # noqa: F811
):
    """Una revisión de hace 45 días (anotada como el dueño: la API solo admite desde hoy) con la
    6° F; la vende hace 30 y compra la 7° G hace 20. El tramo de la primera revisión termina en
    la venta y no se reactiva con la compra: hace falta otra revisión, que abre otro tramo."""
    e = consejo
    hoy = e["hoy"]
    cortar(motor_migrate, e["v_prop"], hoy - timedelta(days=60))
    v_6f = vinculo(motor_migrate, e["cons"], unidad(motor_migrate, e["cons"], "6° F"), e["mc"],
                   desde=hoy - timedelta(days=50), confirma=e["prop"])  # fmt: skip
    with motor_migrate.begin() as c:
        c.execute(
            text(
                "insert into nucleo.revision_mandato (consorcio_id, mandato_id, decision_id,"
                " eficaz_desde, origen) values (:c, :m, :d, :f, 'soporte')"
            ),
            {"c": e["cons"], "m": e["m"], "d": e["acta"], "f": hoy - timedelta(days=45)},
        )
    cortar(motor_migrate, v_6f, hoy - timedelta(days=30))
    vinculo(motor_migrate, e["cons"], unidad(motor_migrate, e["cons"], "7° G"), e["mc"],
            desde=hoy - timedelta(days=20), confirma=e["prop"])  # fmt: skip

    assert not opera(motor_migrate, e["m"], hoy - timedelta(days=46))  # el primer hueco
    assert opera(motor_migrate, e["m"], hoy - timedelta(days=40))  # el tramo de la revisión
    assert not opera(motor_migrate, e["m"], hoy - timedelta(days=25))  # la pérdida posterior
    assert not opera(motor_migrate, e["m"], hoy)  # recuperada sin revisión
    assert estado(e["adm"], e["cons"], e["m"]) == "pendiente_revision"

    r = revisar_http(e["adm"], e["cons"], e["m"], e["acta"], hoy)
    assert r.status_code == 201, r.text
    assert opera(motor_migrate, e["m"], hoy)
    # Los tramos anteriores no cambian.
    assert opera(motor_migrate, e["m"], hoy - timedelta(days=40))
    assert not opera(motor_migrate, e["m"], hoy - timedelta(days=25))


def test_una_revision_sin_la_condicion_ese_dia_no_espera_al_vinculo(consejo, motor_migrate):  # noqa: F811
    """Negar ante la duda: si una revisión quedara anotada en un día sin condición (la función
    no lo admite; acá se escribe como el dueño), el vínculo que aparece después no la activa."""
    e = consejo
    hoy = e["hoy"]
    cortar(motor_migrate, e["v_prop"], hoy - timedelta(days=60))
    with motor_migrate.begin() as c:
        c.execute(
            text(
                "insert into nucleo.revision_mandato (consorcio_id, mandato_id, decision_id,"
                " eficaz_desde, origen) values (:c, :m, :d, :f, 'soporte')"
            ),
            {"c": e["cons"], "m": e["m"], "d": e["acta"], "f": hoy - timedelta(days=45)},
        )
    nuevo = vinculo(motor_migrate, e["cons"], unidad(motor_migrate, e["cons"], "6° F"), e["mc"],
                    desde=hoy - timedelta(days=40), confirma=e["prop"])  # fmt: skip
    assert not opera(motor_migrate, e["m"], hoy)
    # Control: con el vínculo desde el día de la revisión, el tramo opera.
    with motor_migrate.begin() as c:
        c.execute(
            text("update nucleo.vinculo set vigente_desde = :d, confirmado_en = :d where id = :v"),
            {"d": hoy - timedelta(days=45), "v": nuevo},
        )
    assert opera(motor_migrate, e["m"], hoy)


# Origen soporte y la tabla ----------------------------------------------------------------------


def test_el_equipo_revisa_sin_actor_y_no_a_nombre_de_alguien(pendiente, motor_app, motor_migrate):
    e = pendiente
    with pytest.raises(Exception, match="origen_invalido|no lleva actor"):
        revisar_soporte(motor_app, e["cons"], e["m"], e["acta"], e["hoy"], actor=e["adm"])
    assert revisiones(motor_migrate, e["m"]) == []
    revisar_soporte(motor_app, e["cons"], e["m"], e["acta"], e["hoy"])
    assert opera(motor_migrate, e["m"], e["hoy"])
    with motor_migrate.connect() as c:
        fila = c.execute(
            text(
                "select registrada_por, origen from nucleo.revision_mandato where mandato_id = :m"
            ),
            {"m": e["m"]},
        ).one()
    assert (fila.registrada_por, fila.origen) == (None, "soporte")


@pytest.mark.parametrize(
    "sentencia",
    [
        "insert into nucleo.revision_mandato (consorcio_id, mandato_id, decision_id,"
        " eficaz_desde, origen) values (:c, :m, :d, :f, 'soporte')",
        "update nucleo.revision_mandato set eficaz_desde = :f where mandato_id = :m",
        "delete from nucleo.revision_mandato where mandato_id = :m",
    ],
    ids=["insert", "update", "delete"],
)
def test_cem_app_solo_lee_las_revisiones(pendiente, motor_app, sentencia):
    e = pendiente
    revisar_soporte(motor_app, e["cons"], e["m"], e["acta"], e["hoy"])
    with transaccion(motor_app, persona_id=e["adm"], consorcio_id=e["cons"]) as c:
        assert (
            c.execute(  # control: la lee
                text("select count(*) from nucleo.revision_mandato where mandato_id = :m"),
                {"m": e["m"]},
            ).scalar_one()
            == 1
        )
    with pytest.raises(Exception, match="permission denied"):
        with transaccion(motor_app, persona_id=e["adm"], consorcio_id=e["cons"]) as c:
            c.execute(text(sentencia), {"c": e["cons"], "m": e["m"], "d": e["acta"], "f": e["hoy"]})


def test_una_perdida_entre_el_registro_y_la_eficacia_deja_la_revision_sin_efecto(
    pendiente, motor_migrate
):
    """Revisión registrada hoy para dentro de 30 días; vende la 6° F en 10 y compra la 7° G en
    20. Sin otra asamblea después de la pérdida, el día 30 el mandato sigue pendiente
    (revisión independiente en app #102). Control: sin la venta, opera desde el día 30."""
    e = pendiente
    hoy, eficaz = e["hoy"], e["hoy"] + timedelta(days=30)
    r = revisar_http(e["adm"], e["cons"], e["m"], e["acta"], eficaz)
    assert r.status_code == 201, r.text
    assert opera(motor_migrate, e["m"], eficaz)  # control
    cortar(motor_migrate, e["v_nuevo"], hoy + timedelta(days=10))
    vinculo(motor_migrate, e["cons"], unidad(motor_migrate, e["cons"], "7° G"), e["mc"],
            desde=hoy + timedelta(days=20), confirma=e["prop"],
            confirmado=hoy + timedelta(days=20))  # fmt: skip
    assert not opera(motor_migrate, e["m"], eficaz)
    assert not opera(motor_migrate, e["m"], eficaz + timedelta(days=30))


def test_la_eficacia_tiene_que_caer_dentro_del_mandato(pendiente, motor_migrate):
    e = pendiente
    with motor_migrate.begin() as c:
        c.execute(
            text("update nucleo.mandato set vigente_hasta = :h where id = :m"),
            {"h": e["hoy"] + timedelta(days=5), "m": e["m"]},
        )
    r = revisar_http(e["adm"], e["cons"], e["m"], e["acta"], e["hoy"] + timedelta(days=5))
    assert (r.status_code, r.json()["codigo"]) == (422, "eficaz_desde_invalida"), r.text
    # Control: el último día del mandato.
    r = revisar_http(e["adm"], e["cons"], e["m"], e["acta"], e["hoy"] + timedelta(days=4))
    assert r.status_code == 201, r.text


def test_downgrade_con_una_revision_se_niega_y_sin_ella_baja(base_aparte):  # noqa: F811
    cfg, motor = base_aparte
    command.upgrade(cfg, "0023")
    cons, _ = _consorcio_y_unidad(motor)
    persona = _persona(motor, "Consejo")
    with motor.begin() as c:
        c.execute(text("select set_config('app.consorcio_id', :c, true)"), {"c": str(cons)})
        acta = c.execute(
            text("select nucleo.registrar_decision('asamblea', '2024-01-01', 'Designa',"
                 " 'Acta 1', null, 'soporte')")
        ).scalar_one()  # fmt: skip
        mandato = c.execute(
            text("select nucleo.alta_mandato('miembro_consejo', :p, null, null, null, :a,"
                 " '2024-01-01', null, null, null, 'soporte')"),
            {"p": persona, "a": acta},
        ).scalar_one()  # fmt: skip
        c.execute(
            text("insert into nucleo.revision_mandato (consorcio_id, mandato_id, decision_id,"
                 " eficaz_desde, origen) values (:c, :m, :a, '2024-06-01', 'soporte')"),
            {"c": cons, "m": mandato, "a": acta},
        )  # fmt: skip
    with pytest.raises(DBAPIError) as error:
        command.downgrade(cfg, "0022")
    assert error.value.orig.diag.message_hint == "downgrade_con_datos"
    with motor.begin() as c:  # control: sin revisiones, baja
        c.execute(text("delete from nucleo.revision_mandato"))
    command.downgrade(cfg, "0022")
    with motor.connect() as c:
        assert c.execute(text("select to_regclass('nucleo.revision_mandato')")).scalar() is None


def test_un_hueco_conocido_entre_hoy_y_la_eficacia_se_rechaza_al_registrar(
    pendiente, motor_migrate
):
    """La 6° F se vende dentro de 3 días y la 7° G se compra dentro de 5: el día 6 cumple, pero
    no sin interrupción desde hoy. Control: desde el día 2, antes de la venta, sí."""
    e = pendiente
    hoy = e["hoy"]
    cortar(motor_migrate, e["v_nuevo"], hoy + timedelta(days=3))
    vinculo(motor_migrate, e["cons"], unidad(motor_migrate, e["cons"], "7° G"), e["mc"],
            desde=hoy + timedelta(days=5), confirma=e["prop"],
            confirmado=hoy + timedelta(days=5))  # fmt: skip
    r = revisar_http(e["adm"], e["cons"], e["m"], e["acta"], hoy + timedelta(days=6))
    assert (r.status_code, r.json()["codigo"]) == (422, "sin_condicion_habilitante"), r.text
    r = revisar_http(e["adm"], e["cons"], e["m"], e["acta"], hoy + timedelta(days=2))
    assert r.status_code == 201, r.text
