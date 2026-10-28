"""H3, infraestructura común (`0045`): orden de pago versionada, aprobaciones por versión y
barreras de la base. Datos SINTETICOS / PRUEBA / NO REAL.

Diseño: `doc/diseno/H3_Paquete_Decisiones_Economicas.md` del repositorio CEM. Nadie tiene
`egresos.aprobar_orden` (decisión 7 del 21-10, guarda de `0044`): para probar la aprobación, la
escena se la otorga a dos consejeros deshabilitando la guarda dentro de la transacción de la
prueba, como haría la migración de la decisión futura. Que exista una aprobación no lleva la
orden a «aprobada»: cuántas hacen falta es la decisión 2, así que las pruebas de emitir fuerzan
ese estado con una actualización directa (permitida por la tabla de transiciones).
"""

import threading
from collections.abc import Iterator
from dataclasses import dataclass
from uuid import UUID

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db import transaccion
from app.identidad import sesiones
from tests.conftest import misma_identidad
from tests.test_mandatos import otra_persona
from tests.test_mandatos_0012 import alta
from tests.test_modo_operacion import cambiar, consorcio_con_persona, decidir_equipo
from tests.test_vinculo_0014 import base_aparte  # noqa: F401  (fixture)

pytestmark = pytest.mark.db

ESENCIALES = {
    "importe": 1_234_500,
    "moneda": "ARS",
    "concepto": "SINTETICO / PRUEBA / NO REAL: reparación de la bomba",
    "beneficiario": "Proveedor SINTETICO (NO REAL)",
    "comprobante": "Factura TEST-0001",
    "reserva": False,
}
PREPARAR = (
    "select nucleo.preparar_orden_pago(:importe, :moneda, :concepto, :beneficiario,"
    " :comprobante, :reserva, :actor, :origen, :sesion)"
)
MODIFICAR = (
    "select nucleo.modificar_orden_pago(:orden, :version, :importe, :moneda, :concepto,"
    " :beneficiario, :comprobante, :reserva, :actor, :origen, :sesion)"
)
ENVIAR = "select nucleo.enviar_orden_a_revision(:orden, :version, :actor, :origen, :sesion)"
APROBAR = (
    "select nucleo.registrar_aprobacion_orden_pago(:orden, :version, :huella, :actor, :origen,"
    " :sesion)"
)
RECHAZAR = "select nucleo.rechazar_orden_pago(:orden, :version, :motivo, :actor, :origen, :sesion)"
EMITIR = "select nucleo.emitir_orden_pago(:orden, :version, :actor, :origen, :sesion)"


@dataclass
class Escena:
    cons: UUID
    prop: UUID  # registra los mandatos
    adm: UUID  # prepara y ejecuta (paquete del administrador)
    mc1: UUID  # consejero con aprobar (otorgada por la prueba)
    mc2: UUID  # otro consejero con aprobar
    sesion: dict[UUID, UUID]


def nueva_sesion(motor_migrate, persona, *, horas_atras=0, totp_min_atras: int | None = 0) -> UUID:
    """Una sesión vigente, empezada hace `horas_atras`, con el TOTP de hace `totp_min_atras`."""
    with motor_migrate.begin() as c:
        return c.execute(
            text(
                "insert into nucleo.sesion (persona_id, token_hash, dispositivo, ip, creada_en,"
                " ultimo_uso_en, vence_en, segundo_factor_en) values (:p,"
                " sha256(gen_random_uuid()::text::bytea), gen_random_uuid(), '127.0.0.1',"
                " now() - make_interval(hours => :h), now(), now() + interval '1 day',"
                " case when cast(:t as int) is null then null"
                " else greatest(now() - make_interval(mins => cast(:t as int)),"
                " now() - make_interval(hours => :h)) end) returning id"
            ),
            {"p": persona, "h": horas_atras, "t": totp_min_atras},
        ).scalar_one()


def otorgar(motor_migrate, cons, mandato, decision, facultad) -> None:
    """La facultad para la prueba. Las de aprobar pasan por encima de la guarda de `0044`."""
    with motor_migrate.begin() as c:
        c.execute(text("alter table nucleo.facultad_otorgada disable trigger"
                       " mandato_sin_facultad_sin_decidir"))  # fmt: skip
        c.execute(
            text(
                "insert into nucleo.facultad_otorgada (consorcio_id, mandato_id, facultad,"
                " decision_id) values (:c, :m, :f, :d)"
            ),
            {"c": cons, "m": mandato, "f": facultad, "d": decision},
        )
        c.execute(text("alter table nucleo.facultad_otorgada enable trigger"
                       " mandato_sin_facultad_sin_decidir"))  # fmt: skip


def escena_economica(motor_migrate, motor_app, *, con_modo=True) -> Escena:
    cons, _, prop = consorcio_con_persona(motor_migrate)
    acta = decidir_equipo(motor_app, cons)
    if con_modo:
        cambiar(motor_app, cons, "administrador_aumentado", acta)
    adm, mc1, mc2 = (otra_persona(motor_migrate, cons) for _ in range(3))
    alta(motor_app, cons, prop, acta, persona=adm)
    mandatos = {
        p: alta(motor_app, cons, prop, acta, tipo="miembro_consejo", persona=p) for p in (mc1, mc2)
    }
    for m in mandatos.values():
        otorgar(motor_migrate, cons, m, acta, "egresos.aprobar_orden")
    return Escena(
        cons, prop, adm, mc1, mc2, {p: nueva_sesion(motor_migrate, p) for p in (adm, mc1, mc2)}
    )


def sin_facultades_de_aprobar(motor_migrate, cons) -> None:
    """Al terminar, la base compartida vuelve a cumplir la decisión 7 (`0044`)."""
    with motor_migrate.begin() as c:
        c.execute(
            text(
                "delete from nucleo.facultad_otorgada where consorcio_id = :c"
                " and facultad in ('egresos.aprobar_orden', 'liquidacion.aprobar',"
                " 'coeficientes.aprobar')"
            ),
            {"c": cons},
        )


@pytest.fixture
def e(motor_migrate, motor_app) -> Iterator[Escena]:
    escena = escena_economica(motor_migrate, motor_app)
    yield escena
    sin_facultades_de_aprobar(motor_migrate, escena.cons)


def llamar(motor, e: Escena, actor, sentencia, sesion=None, origen="api", **extra):
    params = {**ESENCIALES, **extra, "actor": actor, "origen": origen,
              "sesion": sesion if sesion is not None else e.sesion.get(actor)}  # fmt: skip
    with transaccion(motor, persona_id=actor, consorcio_id=e.cons) as c:
        return c.execute(text(sentencia), params).scalar()


def niega(motor, e, actor, sentencia, esperado, **kw) -> None:
    """`esperado`: el motivo (hint de P0001) o "42501" (sin facultad o actor ajeno)."""
    with pytest.raises(DBAPIError) as error:
        llamar(motor, e, actor, sentencia, **kw)
    diag = error.value.orig.diag
    if esperado == "42501":
        assert diag.sqlstate == "42501", diag.message_primary
    else:
        assert (diag.sqlstate, diag.message_hint) == ("P0001", esperado), diag.message_primary


def orden(motor_migrate, o) -> dict:
    with motor_migrate.connect() as c:
        return dict(
            c.execute(text("select * from nucleo.orden_pago where id = :o"), {"o": o})
            .mappings()
            .one()
        )


def versiones(motor_migrate, o) -> list[dict]:
    with motor_migrate.connect() as c:
        return [
            dict(f)
            for f in c.execute(
                text("select * from nucleo.orden_pago_version where orden_id = :o order by numero"),
                {"o": o},
            ).mappings()
        ]


def aprobaciones(motor_migrate, o) -> list[tuple[int, UUID]]:
    with motor_migrate.connect() as c:
        return [
            tuple(f)
            for f in c.execute(
                text(
                    "select version, persona_id from nucleo.orden_pago_aprobacion"
                    " where orden_id = :o order by creada_en, id"
                ),
                {"o": o},
            ).all()
        ]


def eventos(motor_migrate, o) -> list[dict]:
    with motor_migrate.connect() as c:
        return [
            dict(f)
            for f in c.execute(
                text(
                    "select accion, actor_persona_id, origen, despues, e::text as todo"
                    " from auditoria.eventos e where entidad = 'orden_pago' and entidad_id = :o"
                    " order by secuencia"
                ),
                {"o": o},
            ).mappings()
        ]


def preparada(motor_migrate, e, en_revision=False) -> UUID:
    o = llamar(motor_migrate, e, e.adm, PREPARAR)
    if en_revision:
        llamar(motor_migrate, e, e.adm, ENVIAR, orden=o, version=1)
    return o


def huella(motor_migrate, o, numero) -> bytes:
    return next(v["huella"] for v in versiones(motor_migrate, o) if v["numero"] == numero)


# Preparar y R10 ------------------------------------------------------------------------------


def test_preparar_crea_el_borrador_con_su_version_y_su_huella(motor_migrate, e):
    o = preparada(motor_migrate, e)
    assert (orden(motor_migrate, o)["estado"], orden(motor_migrate, o)["version_vigente"]) == (
        "borrador", 1,
    )  # fmt: skip
    [v] = versiones(motor_migrate, o)
    with motor_migrate.connect() as c:
        recalculada = c.execute(
            text(
                "select nucleo.huella_orden_pago(:o, 1, :importe, :moneda, :concepto,"
                " :beneficiario, :comprobante, :reserva)"
            ),
            {"o": o, **ESENCIALES},
        ).scalar_one()
    assert v["huella"] == recalculada and len(v["huella"]) == 32
    [ev] = eventos(motor_migrate, o)
    assert (ev["accion"], ev["actor_persona_id"], ev["origen"]) == (
        "consorcio.orden_pago.preparar", e.adm, "api",
    )  # fmt: skip
    assert ESENCIALES["concepto"] not in ev["todo"] and ESENCIALES["beneficiario"] not in ev["todo"]
    assert ev["despues"]["huella"] == recalculada.hex()


def test_las_vigencias_de_r10_son_las_de_la_api(motor_migrate):
    with motor_migrate.connect() as c:
        definicion = c.execute(
            text(
                "select pg_get_functiondef("
                "'nucleo.exigir_acto_economico(uuid, uuid, uuid, text, text)'::regprocedure)"
            )
        ).scalar_one()
    assert sesiones.VIGENCIA_SESION_ECONOMICA.total_seconds() == 12 * 3600
    assert sesiones.VIGENCIA_SEGUNDO_FACTOR_RECIENTE.total_seconds() == 10 * 60
    assert "interval '12 hours'" in definicion and "interval '10 minutes'" in definicion


def test_sin_un_modo_que_admita_egresos_no_se_prepara(motor_migrate, motor_app):
    e = escena_economica(motor_migrate, motor_app, con_modo=False)
    try:
        niega(motor_migrate, e, e.adm, PREPARAR, "modo_no_admitido")
        cambiar(motor_app, e.cons, "administrador_aumentado", decidir_equipo(motor_app, e.cons))
        assert llamar(motor_migrate, e, e.adm, PREPARAR) is not None  # control
    finally:
        sin_facultades_de_aprobar(motor_migrate, e.cons)


def test_en_modo_consejo_no_se_prepara(motor_migrate, motor_app):
    e = escena_economica(motor_migrate, motor_app, con_modo=False)
    try:
        cambiar(motor_app, e.cons, "consejo", decidir_equipo(motor_app, e.cons))
        niega(motor_migrate, e, e.adm, PREPARAR, "modo_no_admitido")
    finally:
        sin_facultades_de_aprobar(motor_migrate, e.cons)


@pytest.mark.parametrize(
    ("horas", "totp", "motivo"),
    [
        (13, 0, "requiere_sesion_reciente"),
        (0, None, "requiere_segundo_factor_reciente"),
        (1, 11, "requiere_segundo_factor_reciente"),
    ],
)
def test_r10_la_sesion_y_el_segundo_factor(motor_migrate, e, horas, totp, motivo):
    s = nueva_sesion(motor_migrate, e.adm, horas_atras=horas, totp_min_atras=totp)
    niega(motor_migrate, e, e.adm, PREPARAR, motivo, sesion=s)
    # Control en el borde de adentro: 11 horas y TOTP de hace 9 minutos.
    ok = nueva_sesion(motor_migrate, e.adm, horas_atras=11, totp_min_atras=9)
    assert llamar(motor_migrate, e, e.adm, PREPARAR, sesion=ok) is not None


def test_una_sesion_vencida_revocada_o_ajena_no_sirve(motor_migrate, e):
    s = nueva_sesion(motor_migrate, e.adm)
    niega(motor_migrate, e, e.adm, PREPARAR, "sesion_invalida", sesion=e.sesion[e.mc1])
    with motor_migrate.begin() as c:
        c.execute(text("update nucleo.sesion set revocada_en = now() where id = :s"), {"s": s})
    niega(motor_migrate, e, e.adm, PREPARAR, "sesion_invalida", sesion=s)
    vencida = nueva_sesion(motor_migrate, e.adm, horas_atras=2, totp_min_atras=None)
    with motor_migrate.begin() as c:
        c.execute(
            text("update nucleo.sesion set vence_en = now() - interval '1 minute',"
                 " ultimo_uso_en = now() - interval '2 minutes' where id = :s"),
            {"s": vencida},
        )  # fmt: skip
    niega(motor_migrate, e, e.adm, PREPARAR, "sesion_invalida", sesion=vencida)


def test_sin_identidad_humana_verificada_no_se_prepara(motor_migrate, motor_app, e):
    with motor_migrate.begin() as c:
        c.execute(
            text("update nucleo.identidad_persona set desvinculada_en = now()"
                 " where consorcio_id = :c and persona_id = :p"),
            {"c": e.cons, "p": e.adm},
        )  # fmt: skip
    niega(motor_migrate, e, e.adm, PREPARAR, "identidad_no_verificada")


def test_sin_facultad_soporte_u_otro_actor_no_preparan(motor_migrate, e):
    niega(motor_migrate, e, e.mc1, PREPARAR, "42501")  # el Consejo no prepara
    niega(motor_migrate, e, e.adm, PREPARAR, "origen_invalido", origen="soporte")
    with pytest.raises(DBAPIError) as error:
        with transaccion(motor_migrate, persona_id=e.mc1, consorcio_id=e.cons) as c:
            c.execute(text(PREPARAR), {**ESENCIALES, "actor": e.adm, "origen": "api",
                                       "sesion": e.sesion[e.adm]})  # fmt: skip
    assert error.value.orig.diag.sqlstate == "42501"  # el actor no es la persona de la sesión


# Versionado ----------------------------------------------------------------------------------


def test_modificar_crea_una_version_nueva_y_conserva_la_anterior(motor_migrate, e):
    o = preparada(motor_migrate, e)
    v2 = llamar(motor_migrate, e, e.adm, MODIFICAR, orden=o, version=1, importe=2_000_000)
    assert v2 == 2
    v = versiones(motor_migrate, o)
    assert [(x["numero"], x["importe_centavos"]) for x in v] == [(1, 1_234_500), (2, 2_000_000)]
    assert v[0]["huella"] != v[1]["huella"]
    assert (orden(motor_migrate, o)["estado"], orden(motor_migrate, o)["version_vigente"]) == (
        "borrador", 2,
    )  # fmt: skip
    niega(motor_migrate, e, e.adm, MODIFICAR, "version_desactualizada", orden=o, version=1,
          importe=3_000_000)  # fmt: skip
    niega(motor_migrate, e, e.adm, MODIFICAR, "sin_cambios", orden=o, version=2,
          importe=2_000_000)  # fmt: skip


@pytest.mark.parametrize(
    "cambio",
    [{"importe": 1}, {"moneda": "USD"}, {"concepto": "Otro concepto SINTETICO"},
     {"beneficiario": "Otro beneficiario SINTETICO"}, {"comprobante": None}, {"reserva": True}],
)  # fmt: skip
def test_cada_dato_esencial_cambia_la_huella(motor_migrate, e, cambio):
    o = preparada(motor_migrate, e)
    llamar(motor_migrate, e, e.adm, MODIFICAR, orden=o, version=1, **cambio)
    v = versiones(motor_migrate, o)
    assert v[0]["huella"] != v[1]["huella"]


def test_la_historia_de_versiones_y_aprobaciones_solo_se_agrega(motor_migrate, e):
    o = preparada(motor_migrate, e, en_revision=True)
    llamar(motor_migrate, e, e.mc1, APROBAR, orden=o, version=1, huella=huella(motor_migrate, o, 1))
    for sentencia in (
        "update nucleo.orden_pago_version set importe_centavos = 1 where orden_id = :o",
        "delete from nucleo.orden_pago_version where orden_id = :o",
        "update nucleo.orden_pago_aprobacion set version = 2 where orden_id = :o",
        "delete from nucleo.orden_pago_aprobacion where orden_id = :o",
    ):
        with pytest.raises(DBAPIError) as error:
            with motor_migrate.begin() as c:
                c.execute(text(sentencia), {"o": o})
        assert error.value.orig.diag.message_hint == "historia_inmutable", sentencia


# Aprobación por versión ----------------------------------------------------------------------


def test_aprobar_registra_la_version_y_no_cambia_el_estado(motor_migrate, e):
    o = preparada(motor_migrate, e, en_revision=True)
    h = huella(motor_migrate, o, 1)
    llamar(motor_migrate, e, e.mc1, APROBAR, orden=o, version=1, huella=h)
    llamar(motor_migrate, e, e.mc2, APROBAR, orden=o, version=1, huella=h)  # N aprobaciones
    assert aprobaciones(motor_migrate, o) == [(1, e.mc1), (1, e.mc2)]
    assert orden(motor_migrate, o)["estado"] == "en_revision"  # la cantidad es la decisión 2
    niega(motor_migrate, e, e.mc1, APROBAR, "aprobacion_repetida", orden=o, version=1, huella=h)
    acciones = [x["accion"] for x in eventos(motor_migrate, o)]
    assert acciones.count("consorcio.orden_pago.aprobar") == 2


def test_un_cambio_esencial_invalida_lo_aprobado_y_permite_aprobar_la_version_nueva(
    motor_migrate, e
):
    """RN12 y el P2 de Codex en CEM #111: la misma persona aprueba la versión nueva."""
    o = preparada(motor_migrate, e, en_revision=True)
    h1 = huella(motor_migrate, o, 1)
    llamar(motor_migrate, e, e.mc1, APROBAR, orden=o, version=1, huella=h1)
    llamar(motor_migrate, e, e.adm, MODIFICAR, orden=o, version=1, importe=9_999)
    h2 = huella(motor_migrate, o, 2)
    niega(motor_migrate, e, e.mc1, APROBAR, "version_desactualizada", orden=o, version=1,
          huella=h1)  # fmt: skip
    niega(motor_migrate, e, e.mc1, APROBAR, "huella_distinta", orden=o, version=2, huella=h1)
    llamar(motor_migrate, e, e.mc1, APROBAR, orden=o, version=2, huella=h2)
    assert aprobaciones(motor_migrate, o) == [(1, e.mc1), (2, e.mc1)]


def test_una_orden_aprobada_que_cambia_vuelve_a_revision(motor_migrate, e):
    o = preparada(motor_migrate, e, en_revision=True)
    with motor_migrate.begin() as c:
        c.execute(text("update nucleo.orden_pago set estado = 'aprobada' where id = :o"), {"o": o})
    llamar(motor_migrate, e, e.adm, MODIFICAR, orden=o, version=1, beneficiario="Otro SINTETICO")
    assert (orden(motor_migrate, o)["estado"], orden(motor_migrate, o)["version_vigente"]) == (
        "en_revision", 2,
    )  # fmt: skip


def test_solo_se_aprueba_una_orden_en_revision(motor_migrate, e):
    o = preparada(motor_migrate, e)
    niega(motor_migrate, e, e.mc1, APROBAR, "estado_invalido", orden=o, version=1,
          huella=huella(motor_migrate, o, 1))  # fmt: skip


# Separación de funciones ---------------------------------------------------------------------


def test_quien_preparo_no_aprueba_ni_con_otra_cuenta(motor_migrate, motor_app, e):
    o = preparada(motor_migrate, e, en_revision=True)
    h = huella(motor_migrate, o, 1)
    # El administrador recibe aprobar solo para esta prueba: igual no aprueba lo que preparó.
    with motor_migrate.connect() as c:
        m_adm = c.execute(
            text("select id from nucleo.mandato where consorcio_id = :c and persona_id = :p"),
            {"c": e.cons, "p": e.adm},
        ).scalar_one()
        acta = c.execute(
            text("select decision_id from nucleo.mandato where id = :m"), {"m": m_adm}
        ).scalar_one()
    otorgar(motor_migrate, e.cons, m_adm, acta, "egresos.aprobar_orden")
    niega(motor_migrate, e, e.adm, APROBAR, "no_es_quien_preparo", orden=o, version=1, huella=h)
    # Otra cuenta del mismo ser humano que preparó, en el Consejo y con aprobar.
    otra = otra_persona(motor_migrate, e.cons, verificada=False)
    misma_identidad(motor_migrate, e.cons, e.adm, otra)
    m_otra = alta(motor_app, e.cons, e.prop, acta, tipo="miembro_consejo", persona=otra)
    otorgar(motor_migrate, e.cons, m_otra, acta, "egresos.aprobar_orden")
    e.sesion[otra] = nueva_sesion(motor_migrate, otra)
    niega(motor_migrate, e, otra, APROBAR, "no_es_quien_preparo", orden=o, version=1, huella=h)
    llamar(motor_migrate, e, e.mc1, APROBAR, orden=o, version=1, huella=h)  # control


def test_quien_preparo_una_version_anterior_tampoco_aprueba(motor_migrate, e):
    with motor_migrate.connect() as c:
        m = c.execute(
            text("select id, decision_id from nucleo.mandato where consorcio_id = :c"
                 " and persona_id = :p"),
            {"c": e.cons, "p": e.mc2},
        ).one()  # fmt: skip
    otorgar(motor_migrate, e.cons, m.id, m.decision_id, "egresos.preparar_orden")
    o = preparada(motor_migrate, e, en_revision=True)
    llamar(motor_migrate, e, e.mc2, MODIFICAR, orden=o, version=1, importe=777)
    llamar(motor_migrate, e, e.adm, MODIFICAR, orden=o, version=2, importe=888)
    niega(motor_migrate, e, e.mc2, APROBAR, "no_es_quien_preparo", orden=o, version=3,
          huella=huella(motor_migrate, o, 3))  # fmt: skip


def test_rechazar_tambien_exige_no_haber_preparado_y_guarda_el_motivo_fuera_del_evento(
    motor_migrate, e
):
    o = preparada(motor_migrate, e, en_revision=True)
    motivo = "SINTETICO: el presupuesto no coincide"
    niega(motor_migrate, e, e.adm, RECHAZAR, "42501", orden=o, version=1, motivo=motivo)
    llamar(motor_migrate, e, e.mc1, RECHAZAR, orden=o, version=1, motivo=motivo)
    o_ = orden(motor_migrate, o)
    assert (o_["estado"], o_["motivo_rechazo"]) == ("rechazada", motivo)
    assert all(motivo not in x["todo"] for x in eventos(motor_migrate, o))
    niega(motor_migrate, e, e.adm, MODIFICAR, "estado_invalido", orden=o, version=1, importe=5)


def _aprobada(motor_migrate, e) -> UUID:
    """Aprobada por mc1; el paso a «aprobada» lo fuerza la prueba (decisión 2 pendiente)."""
    o = preparada(motor_migrate, e, en_revision=True)
    llamar(motor_migrate, e, e.mc1, APROBAR, orden=o, version=1, huella=huella(motor_migrate, o, 1))
    with motor_migrate.begin() as c:
        c.execute(text("update nucleo.orden_pago set estado = 'aprobada' where id = :o"), {"o": o})
    return o


def test_quien_aprobo_no_emite_ni_con_otra_cuenta(motor_migrate, motor_app, e):
    o = _aprobada(motor_migrate, e)
    with motor_migrate.connect() as c:
        m = c.execute(
            text("select id, decision_id from nucleo.mandato where consorcio_id = :c"
                 " and persona_id = :p"),
            {"c": e.cons, "p": e.mc1},
        ).one()  # fmt: skip
    otorgar(motor_migrate, e.cons, m.id, m.decision_id, "egresos.ejecutar_pago")
    niega(motor_migrate, e, e.mc1, EMITIR, "no_es_quien_aprobo", orden=o, version=1)
    otra = otra_persona(motor_migrate, e.cons, verificada=False)
    misma_identidad(motor_migrate, e.cons, e.mc1, otra)
    m_otra = alta(motor_app, e.cons, e.prop, m.decision_id, tipo="miembro_consejo", persona=otra)
    otorgar(motor_migrate, e.cons, m_otra, m.decision_id, "egresos.ejecutar_pago")
    e.sesion[otra] = nueva_sesion(motor_migrate, otra)
    niega(motor_migrate, e, otra, EMITIR, "no_es_quien_aprobo", orden=o, version=1)
    llamar(
        motor_migrate, e, e.adm, EMITIR, orden=o, version=1
    )  # control: A5 no frena a quien preparó
    assert orden(motor_migrate, o)["estado"] == "en_ejecucion"


def test_emitir_dos_veces_no_emite_dos_veces(motor_migrate, e):
    o = _aprobada(motor_migrate, e)
    llamar(motor_migrate, e, e.adm, EMITIR, orden=o, version=1)
    niega(motor_migrate, e, e.adm, EMITIR, "estado_invalido", orden=o, version=1)
    acciones = [x["accion"] for x in eventos(motor_migrate, o)]
    assert acciones.count("consorcio.orden_pago.emitir") == 1


# Transiciones --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("desde", "hacia"),
    [("borrador", "aprobada"), ("en_revision", "pagada"), ("rechazada", "en_revision"),
     ("en_ejecucion", "en_revision"), ("conciliada", "pagada")],
)  # fmt: skip
def test_la_base_niega_las_transiciones_fuera_del_diagrama(motor_migrate, e, desde, hacia):
    o = preparada(motor_migrate, e)
    with motor_migrate.begin() as c:
        c.execute(text("alter table nucleo.orden_pago disable trigger transicion_orden_pago"))
        c.execute(
            text("update nucleo.orden_pago set estado = cast(:d as nucleo.estado_orden_pago),"
                 " motivo_rechazo = case when cast(:d as text) = 'rechazada' then 'SINTETICO'"
                 " end where id = :o"),
            {"d": desde, "o": o},
        )  # fmt: skip
        c.execute(text("alter table nucleo.orden_pago enable trigger transicion_orden_pago"))
    with pytest.raises(DBAPIError) as error:
        with motor_migrate.begin() as c:
            c.execute(
                text("update nucleo.orden_pago set estado = :h, motivo_rechazo = null"
                     " where id = :o"),
                {"h": hacia, "o": o},
            )  # fmt: skip
    assert error.value.orig.diag.message_hint == "transicion_invalida"


def test_la_identidad_de_la_orden_no_cambia(motor_migrate, e):
    o = preparada(motor_migrate, e)
    with pytest.raises(DBAPIError) as error:
        with motor_migrate.begin() as c:
            c.execute(
                text("update nucleo.orden_pago set preparada_por = :p where id = :o"),
                {"p": e.mc1, "o": o},
            )
    assert error.value.orig.diag.message_hint == "orden_inmutable"


# Concurrencia --------------------------------------------------------------------------------


def _en_paralelo(motor_migrate, e, primero, segundo) -> dict:
    """`primero` corre y deja su transacción abierta (con la orden bloqueada); `segundo` espera
    el bloqueo; al confirmar el primero, el segundo ve el estado nuevo."""
    actor1, sentencia1, kw1 = primero
    conn = motor_migrate.connect()
    tx = conn.begin()
    conn.execute(text("select set_config('app.persona_id', :p, true),"
                      " set_config('app.consorcio_id', :c, true)"),
                 {"p": str(actor1), "c": str(e.cons)})  # fmt: skip
    conn.execute(text(sentencia1), {**ESENCIALES, **kw1, "actor": actor1, "origen": "api",
                                    "sesion": e.sesion[actor1]})  # fmt: skip
    resultado: dict = {}

    def correr():
        actor2, sentencia2, kw2 = segundo
        try:
            resultado["ok"] = llamar(motor_migrate, e, actor2, sentencia2, **kw2)
        except DBAPIError as error:
            resultado["motivo"] = error.orig.diag.message_hint

    hilo = threading.Thread(target=correr)
    hilo.start()
    hilo.join(1.0)
    resultado["esperaba"] = hilo.is_alive()
    tx.commit()
    conn.close()
    hilo.join(10)
    return resultado


def test_dos_modificaciones_simultaneas_de_la_misma_version(motor_migrate, e):
    o = preparada(motor_migrate, e)
    r = _en_paralelo(motor_migrate, e,
                     (e.adm, MODIFICAR, {"orden": o, "version": 1, "importe": 10}),
                     (e.adm, MODIFICAR, {"orden": o, "version": 1, "importe": 20}))  # fmt: skip
    assert r == {"esperaba": True, "motivo": "version_desactualizada"}
    assert [x["importe_centavos"] for x in versiones(motor_migrate, o)] == [1_234_500, 10]


def test_aprobar_mientras_otro_modifica_no_aprueba_lo_viejo(motor_migrate, e):
    o = preparada(motor_migrate, e, en_revision=True)
    h1 = huella(motor_migrate, o, 1)
    r = _en_paralelo(motor_migrate, e,
                     (e.adm, MODIFICAR, {"orden": o, "version": 1, "importe": 10}),
                     (e.mc1, APROBAR, {"orden": o, "version": 1, "huella": h1}))  # fmt: skip
    assert r == {"esperaba": True, "motivo": "version_desactualizada"}
    assert aprobaciones(motor_migrate, o) == []


def test_rechazar_mientras_otro_aprueba(motor_migrate, e):
    o = preparada(motor_migrate, e, en_revision=True)
    h1 = huella(motor_migrate, o, 1)
    r = _en_paralelo(motor_migrate, e,
                     (e.mc2, RECHAZAR, {"orden": o, "version": 1, "motivo": "SINTETICO"}),
                     (e.mc1, APROBAR, {"orden": o, "version": 1, "huella": h1}))  # fmt: skip
    assert r == {"esperaba": True, "motivo": "estado_invalido"}
    assert aprobaciones(motor_migrate, o) == []


def test_dos_emisiones_simultaneas_emiten_una_sola_vez(motor_migrate, e):
    o = _aprobada(motor_migrate, e)
    r = _en_paralelo(motor_migrate, e,
                     (e.adm, EMITIR, {"orden": o, "version": 1}),
                     (e.adm, EMITIR, {"orden": o, "version": 1}))  # fmt: skip
    assert r == {"esperaba": True, "motivo": "estado_invalido"}


def test_dos_cuentas_del_mismo_humano_aprobando_a_la_vez_suman_una(motor_migrate, motor_app, e):
    o = preparada(motor_migrate, e, en_revision=True)
    h1 = huella(motor_migrate, o, 1)
    with motor_migrate.connect() as c:
        acta = c.execute(
            text("select decision_id from nucleo.mandato where consorcio_id = :c"
                 " and persona_id = :p"),
            {"c": e.cons, "p": e.mc1},
        ).scalar_one()  # fmt: skip
    otra = otra_persona(motor_migrate, e.cons, verificada=False)
    misma_identidad(motor_migrate, e.cons, e.mc1, otra)
    m_otra = alta(motor_app, e.cons, e.prop, acta, tipo="miembro_consejo", persona=otra)
    otorgar(motor_migrate, e.cons, m_otra, acta, "egresos.aprobar_orden")
    e.sesion[otra] = nueva_sesion(motor_migrate, otra)
    r = _en_paralelo(motor_migrate, e,
                     (e.mc1, APROBAR, {"orden": o, "version": 1, "huella": h1}),
                     (otra, APROBAR, {"orden": o, "version": 1, "huella": h1}))  # fmt: skip
    assert r == {"esperaba": True, "motivo": "aprobacion_repetida"}
    assert aprobaciones(motor_migrate, o) == [(1, e.mc1)]


# Permisos e ida y vuelta ---------------------------------------------------------------------


def test_cem_app_no_tiene_ningun_permiso_economico(motor_migrate):
    tablas = ("nucleo.orden_pago", "nucleo.orden_pago_version", "nucleo.orden_pago_aprobacion",
              "nucleo.transicion_orden_pago")  # fmt: skip
    with motor_migrate.connect() as c:
        tabla = [
            c.execute(
                text("select has_table_privilege('cem_app', :t, :p)"), {"t": t, "p": p}
            ).scalar_one()
            for t in tablas
            for p in ("SELECT", "INSERT", "UPDATE", "DELETE")
        ]
        funciones = c.execute(
            text(
                "select count(*) filter (where has_function_privilege('cem_app', p.oid,"
                " 'EXECUTE')), count(*) from pg_proc p join pg_namespace n on n.oid ="
                " p.pronamespace where n.nspname = 'nucleo' and (p.proname like '%orden_pago%'"
                " or p.proname in ('exigir_acto_economico', 'enviar_orden_a_revision',"
                " 'exigir_decision_sobre_orden'))"
            )
        ).one()
    assert not any(tabla)
    assert funciones[0] == 0 and funciones[1] >= 13  # control: las funciones existen


def test_0045_ida_y_vuelta(base_aparte):  # noqa: F811
    cfg, motor = base_aparte

    def existe() -> bool:
        with motor.connect() as c:
            return c.execute(
                text("select to_regclass('nucleo.orden_pago') is not null")
            ).scalar_one()

    command.upgrade(cfg, "0044")
    assert not existe()
    command.upgrade(cfg, "0045")
    assert existe()
    command.downgrade(cfg, "0044")
    with motor.connect() as c:
        restos = c.execute(
            text(
                "select count(*) from pg_proc p join pg_namespace n on n.oid = p.pronamespace"
                " where n.nspname = 'nucleo' and (p.proname like '%orden_pago%'"
                " or p.proname = 'exigir_acto_economico')"
            )
        ).scalar_one()
        tipo = c.execute(text("select to_regtype('nucleo.estado_orden_pago')")).scalar_one()
    assert not existe() and restos == 0 and tipo is None
    command.upgrade(cfg, "0045")  # control: vuelve a subir limpio
    assert existe()
