"""H3: orden de pago versionada, aprobaciones por versión y barreras de la base (`0045`), con
la aprobación del Consejo y su cantidad configurable (`0046`). Datos SINTETICOS / PRUEBA / NO
REAL.

Diseño: `doc/diseno/H3_Paquete_Decisiones_Economicas.md` del repositorio CEM y la decisión 2 del
propietario (29-10-2026): `egresos.aprobar_orden` está en el paquete de `miembro_consejo`, así que
los dos consejeros de la escena la reciben con su alta. Sin configuración hace falta una
aprobación; las pruebas que necesitan dos las cargan como soporte (`exigir`).
"""

import threading
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
from tests.test_vinculo_0014 import (  # noqa: F401  (`base_aparte` es un fixture)
    _consorcio_y_unidad,
    _persona,
    base_aparte,
)

pytestmark = pytest.mark.db

ESENCIALES = {
    "importe": 1_234_500,
    "moneda": "ARS",
    "concepto": "SINTETICO / PRUEBA / NO REAL: reparación de la bomba",
    "comprobante": "Factura TEST-0001",
    "reserva": False,
}
PREPARAR = (
    "select nucleo.preparar_orden_pago(:importe, :moneda, :concepto, :proveedor,"
    " :comprobante, :reserva, :actor, :origen, :sesion)"
)
MODIFICAR = (
    "select nucleo.modificar_orden_pago(:orden, :version, :importe, :moneda, :concepto,"
    " :proveedor, :comprobante, :reserva, :actor, :origen, :sesion)"
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
    proveedor: UUID  # el beneficiario de las órdenes (0048)
    otro: UUID  # otro proveedor, para cambiar de beneficiario


RAZON_SOCIAL = "Proveedor SINTETICO (NO REAL)"


def cbu_sintetico(semilla: int) -> str:
    """Un CBU con sus dos dígitos verificadores válidos y el banco 999, que no existe."""
    b1 = f"999{semilla % 10_000:04d}"
    b2 = f"{semilla % 10**13:013d}"
    d1 = (10 - sum(int(x) * w for x, w in zip(b1, (7, 1, 3, 9, 7, 1, 3), strict=True)) % 10) % 10
    pesos = (3, 9, 7, 1, 3, 9, 7, 1, 3, 9, 7, 1, 3)
    d2 = (10 - sum(int(x) * w for x, w in zip(b2, pesos, strict=True)) % 10) % 10
    return f"{b1}{d1}{b2}{d2}"


def nuevo_proveedor(motor_migrate, cons, quien, *, cuenta=None, razon=RAZON_SOCIAL,
                    estado="activo") -> UUID:  # fmt: skip
    """Un proveedor con su versión 1, cargado como dueño de la base (las funciones de alta se
    prueban en `test_proveedor_0048.py`)."""
    with motor_migrate.begin() as c:
        p = c.execute(
            text("insert into nucleo.proveedor (consorcio_id, alta_por, identidad_alta)"
                 " values (:c, :q, nucleo.identidad_humana_de(:c, :q)) returning id"),
            {"c": cons, "q": quien},
        ).scalar_one()  # fmt: skip
        c.execute(
            text("insert into nucleo.proveedor_version (consorcio_id, proveedor_id, numero,"
                 " razon_social, estado, cuenta_destino, huella, preparada_por,"
                 " identidad_preparo) values (:c, :p, 1, :r, :e, :cta,"
                 " nucleo.huella_proveedor(:p, 1, null, :r, :cta), :q,"
                 " nucleo.identidad_humana_de(:c, :q))"),
            {"c": cons, "p": p, "r": razon, "e": estado, "cta": cuenta, "q": quien},
        )  # fmt: skip
    return p


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
    """Una facultad fuera del paquete del mandato, para la prueba (pasa por encima de la guarda de
    `0044`, que sigue negando las de aprobar sin decidir)."""
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
    for p in (mc1, mc2):
        alta(motor_app, cons, prop, acta, tipo="miembro_consejo", persona=p)
    sesion = {p: nueva_sesion(motor_migrate, p) for p in (adm, mc1, mc2)}
    return Escena(
        cons,
        prop,
        adm,
        mc1,
        mc2,
        sesion,
        nuevo_proveedor(motor_migrate, cons, adm, cuenta=cbu_sintetico(1)),
        nuevo_proveedor(motor_migrate, cons, adm, cuenta=cbu_sintetico(2), razon="Otro SINTETICO"),
    )


def exigir(motor_app, cons, cantidad: int) -> None:
    """Como soporte (`scripts/aprobaciones_orden_pago.py`): la cantidad rige desde hoy."""
    acta = decidir_equipo(motor_app, cons)
    with transaccion(motor_app, persona_id=None, consorcio_id=cons) as c:
        c.execute(
            text(
                "select nucleo.fijar_aprobaciones_orden_pago(cast(:n as smallint),"
                " nucleo.hoy_del_consorcio(:c), :d, null, 'soporte')"
            ),
            {"n": cantidad, "c": cons, "d": acta},
        )


@pytest.fixture
def e(motor_migrate, motor_app) -> Escena:
    return escena_economica(motor_migrate, motor_app)


def llamar(motor, e: Escena, actor, sentencia, sesion=None, origen="api", **extra):
    params = {**ESENCIALES, "proveedor": e.proveedor, **extra, "actor": actor, "origen": origen,
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
                " (select encode(huella, 'hex') from nucleo.proveedor_version"
                "   where proveedor_id = :p and numero = 1), :comprobante, :reserva)"
            ),
            {"o": o, "p": e.proveedor, **ESENCIALES},
        ).scalar_one()
    assert v["huella"] == recalculada and len(v["huella"]) == 32
    [ev] = eventos(motor_migrate, o)
    assert (ev["accion"], ev["actor_persona_id"], ev["origen"]) == (
        "consorcio.orden_pago.preparar", e.adm, "api",
    )  # fmt: skip
    assert ESENCIALES["concepto"] not in ev["todo"] and RAZON_SOCIAL not in ev["todo"]
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
    niega(motor_migrate, e, e.adm, PREPARAR, "modo_no_admitido")
    cambiar(motor_app, e.cons, "administrador_aumentado", decidir_equipo(motor_app, e.cons))
    assert llamar(motor_migrate, e, e.adm, PREPARAR) is not None  # control


def test_en_modo_consejo_no_se_prepara(motor_migrate, motor_app):
    e = escena_economica(motor_migrate, motor_app, con_modo=False)
    cambiar(motor_app, e.cons, "consejo", decidir_equipo(motor_app, e.cons))
    niega(motor_migrate, e, e.adm, PREPARAR, "modo_no_admitido")


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
            c.execute(text(PREPARAR), {**ESENCIALES, "proveedor": e.proveedor, "actor": e.adm,
                                       "origen": "api",
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
     {"proveedor": "otro"}, {"comprobante": None}, {"reserva": True}],
)  # fmt: skip
def test_cada_dato_esencial_cambia_la_huella(motor_migrate, e, cambio):
    o = preparada(motor_migrate, e)
    if cambio.get("proveedor") == "otro":
        cambio = {"proveedor": e.otro}
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


def test_sin_configuracion_una_aprobacion_deja_la_orden_aprobada(motor_migrate, e):
    """Decisión 2 (29-10-2026): sin configuración aprueba un consejero distinto de quien preparó."""
    o = preparada(motor_migrate, e, en_revision=True)
    assert versiones(motor_migrate, o)[0]["aprobaciones_requeridas"] == 1
    h = huella(motor_migrate, o, 1)
    llamar(motor_migrate, e, e.mc1, APROBAR, orden=o, version=1, huella=h)
    assert orden(motor_migrate, o)["estado"] == "aprobada"
    ev = eventos(motor_migrate, o)[-1]
    assert ev["accion"] == "consorcio.orden_pago.aprobar"
    assert {k: ev["despues"][k] for k in ("aprobaciones", "requeridas", "estado")} == {
        "aprobaciones": 1, "requeridas": 1, "estado": "aprobada",
    }  # fmt: skip
    # Ya aprobada, otro consejero no suma: la orden no está en revisión.
    niega(motor_migrate, e, e.mc2, APROBAR, "estado_invalido", orden=o, version=1, huella=h)
    assert aprobaciones(motor_migrate, o) == [(1, e.mc1)]


def test_con_dos_requeridas_la_primera_no_aprueba_y_la_segunda_si(motor_migrate, motor_app, e):
    exigir(motor_app, e.cons, 2)
    o = preparada(motor_migrate, e, en_revision=True)
    assert versiones(motor_migrate, o)[0]["aprobaciones_requeridas"] == 2
    h = huella(motor_migrate, o, 1)
    llamar(motor_migrate, e, e.mc1, APROBAR, orden=o, version=1, huella=h)
    assert orden(motor_migrate, o)["estado"] == "en_revision"
    assert eventos(motor_migrate, o)[-1]["despues"]["estado"] == "en_revision"
    niega(motor_migrate, e, e.mc1, APROBAR, "aprobacion_repetida", orden=o, version=1, huella=h)
    llamar(motor_migrate, e, e.mc2, APROBAR, orden=o, version=1, huella=h)
    assert orden(motor_migrate, o)["estado"] == "aprobada"
    assert aprobaciones(motor_migrate, o) == [(1, e.mc1), (1, e.mc2)]
    acciones = [x["accion"] for x in eventos(motor_migrate, o)]
    assert acciones.count("consorcio.orden_pago.aprobar") == 2


def test_cada_version_guarda_la_cantidad_del_dia_en_que_se_preparo(motor_migrate, motor_app, e):
    """Un cambio de configuración no altera lo ya preparado; modificar toma la del día."""
    antes = preparada(motor_migrate, e, en_revision=True)
    exigir(motor_app, e.cons, 2)
    despues = preparada(motor_migrate, e, en_revision=True)
    assert versiones(motor_migrate, antes)[0]["aprobaciones_requeridas"] == 1
    assert versiones(motor_migrate, despues)[0]["aprobaciones_requeridas"] == 2
    llamar(motor_migrate, e, e.mc1, APROBAR, orden=antes, version=1,
           huella=huella(motor_migrate, antes, 1))  # fmt: skip
    assert orden(motor_migrate, antes)["estado"] == "aprobada"  # con la cantidad que tenía
    llamar(motor_migrate, e, e.adm, MODIFICAR, orden=antes, version=1, importe=4_321)
    assert versiones(motor_migrate, antes)[1]["aprobaciones_requeridas"] == 2
    llamar(motor_migrate, e, e.mc1, APROBAR, orden=antes, version=2,
           huella=huella(motor_migrate, antes, 2))  # fmt: skip
    assert orden(motor_migrate, antes)["estado"] == "en_revision"


def test_otra_cuenta_del_mismo_humano_no_suma_la_segunda(motor_migrate, motor_app, e):
    exigir(motor_app, e.cons, 2)
    o = preparada(motor_migrate, e, en_revision=True)
    h = huella(motor_migrate, o, 1)
    llamar(motor_migrate, e, e.mc1, APROBAR, orden=o, version=1, huella=h)
    otra = otra_cuenta_en_el_consejo(motor_migrate, motor_app, e, e.mc1)
    niega(motor_migrate, e, otra, APROBAR, "aprobacion_repetida", orden=o, version=1, huella=h)
    assert orden(motor_migrate, o)["estado"] == "en_revision"
    llamar(motor_migrate, e, e.mc2, APROBAR, orden=o, version=1, huella=h)  # control
    assert orden(motor_migrate, o)["estado"] == "aprobada"


def test_p3_69_tampoco_suma_si_soporte_corrige_la_identidad_despues(motor_migrate, motor_app, e):
    """P3 69: mc1 aprueba con su identidad; después soporte corrige su cuenta a la identidad de
    otra persona del Consejo (son el mismo ser humano). Esa otra no suma la segunda aprobación,
    aunque la identidad guardada en la aprobación sea la vieja. Control: mc2, otro humano, sí."""
    exigir(motor_app, e.cons, 2)
    o = preparada(motor_migrate, e, en_revision=True)
    h = huella(motor_migrate, o, 1)
    llamar(motor_migrate, e, e.mc1, APROBAR, orden=o, version=1, huella=h)
    otra = otra_persona(motor_migrate, e.cons)
    with motor_migrate.connect() as c:
        acta = c.execute(
            text("select decision_id from nucleo.mandato where consorcio_id = :c"
                 " and persona_id = :p"), {"c": e.cons, "p": e.mc1},
        ).scalar_one()  # fmt: skip
        de_otra = c.execute(
            text("select nucleo.identidad_humana_de(:c, :p)"), {"c": e.cons, "p": otra}
        ).scalar_one()
    alta(motor_app, e.cons, e.prop, acta, tipo="miembro_consejo", persona=otra)
    e.sesion[otra] = nueva_sesion(motor_migrate, otra)
    _revincular(motor_migrate, e.cons, e.mc1, a=de_otra)
    with motor_migrate.connect() as c:
        guardada = c.execute(
            text("select identidad_id from nucleo.orden_pago_aprobacion where orden_id = :o"),
            {"o": o},
        ).scalar_one()
    assert de_otra is not None and guardada != de_otra  # control: la guardada no la delata
    niega(motor_migrate, e, otra, APROBAR, "aprobacion_repetida", orden=o, version=1, huella=h)
    assert orden(motor_migrate, o)["estado"] == "en_revision"
    llamar(motor_migrate, e, e.mc2, APROBAR, orden=o, version=1, huella=h)
    assert orden(motor_migrate, o)["estado"] == "aprobada"


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
    assert orden(motor_migrate, o)["estado"] == "aprobada"


def test_una_orden_aprobada_que_cambia_vuelve_a_revision(motor_migrate, e):
    o = _aprobada(motor_migrate, e)
    llamar(motor_migrate, e, e.adm, MODIFICAR, orden=o, version=1, proveedor=e.otro)
    assert (orden(motor_migrate, o)["estado"], orden(motor_migrate, o)["version_vigente"]) == (
        "en_revision", 2,
    )  # fmt: skip


def test_pa12_cambiar_el_beneficiario_tras_aprobar_invalida_y_vuelve_a_revision(motor_migrate, e):
    """PA12 (documento 08, RN12): cambiar el beneficiario de una orden aprobada invalida la
    autorización y la devuelve a revisión. Parcial: la base; la ruta y la pantalla esperan a H3."""
    o = _aprobada(motor_migrate, e)
    h1 = huella(motor_migrate, o, 1)
    llamar(motor_migrate, e, e.adm, MODIFICAR, orden=o, version=1, proveedor=e.otro)
    despues = orden(motor_migrate, o)
    assert (despues["estado"], despues["version_vigente"]) == ("en_revision", 2)
    assert huella(motor_migrate, o, 2) != h1
    with motor_migrate.connect() as c:
        vigentes = c.execute(
            text("select count(*) from nucleo.orden_pago_aprobacion where orden_id = :o"
                 " and version = 2"), {"o": o},
        ).scalar_one()  # fmt: skip
    assert vigentes == 0  # la aprobación de la versión 1 queda como historia, sin contar
    assert aprobaciones(motor_migrate, o) == [(1, e.mc1)]
    niega(motor_migrate, e, e.mc1, APROBAR, "huella_distinta", orden=o, version=2, huella=h1)


def test_solo_se_aprueba_una_orden_en_revision(motor_migrate, e):
    o = preparada(motor_migrate, e)
    niega(motor_migrate, e, e.mc1, APROBAR, "estado_invalido", orden=o, version=1,
          huella=huella(motor_migrate, o, 1))  # fmt: skip


# Separación de funciones ---------------------------------------------------------------------


def otra_cuenta_en_el_consejo(motor_migrate, motor_app, e, de: UUID) -> UUID:
    """Otra cuenta del mismo ser humano que `de`, con mandato del Consejo (y por eso con
    `egresos.aprobar_orden`) y sesión. Devuelve esa cuenta."""
    with motor_migrate.connect() as c:
        acta = c.execute(
            text("select decision_id from nucleo.mandato where consorcio_id = :c"
                 " and persona_id = :p"), {"c": e.cons, "p": de},
        ).scalar_one()  # fmt: skip
    otra = otra_persona(motor_migrate, e.cons, verificada=False)
    misma_identidad(motor_migrate, e.cons, de, otra)
    alta(motor_app, e.cons, e.prop, acta, tipo="miembro_consejo", persona=otra)
    e.sesion[otra] = nueva_sesion(motor_migrate, otra)
    return otra


def _quien_preparo_tambien_en_el_consejo(motor_migrate, motor_app, e) -> UUID:
    """El ser humano que prepara (el administrador) tiene además otra cuenta con mandato del
    Consejo, y por eso con `egresos.aprobar_orden`. Desde `0047` es la única forma de que tenga
    la facultad (P3 81): su mandato de administrador no la recibe. Devuelve esa otra cuenta."""
    return otra_cuenta_en_el_consejo(motor_migrate, motor_app, e, e.adm)


def test_pa14_quien_preparo_no_aprueba_ni_con_otra_cuenta(motor_migrate, motor_app, e):
    """PA14 (documento 08): el solicitante como segundo aprobador queda bloqueado. Parcial: la
    base; la ruta y la pantalla esperan a las decisiones de H3."""
    o = preparada(motor_migrate, e, en_revision=True)
    h = huella(motor_migrate, o, 1)
    otra = _quien_preparo_tambien_en_el_consejo(motor_migrate, motor_app, e)
    niega(motor_migrate, e, e.adm, APROBAR, "42501", orden=o, version=1, huella=h)
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
    motor_migrate, motor_app, e
):
    o = preparada(motor_migrate, e, en_revision=True)
    motivo = "SINTETICO: el presupuesto no coincide"
    niega(motor_migrate, e, e.adm, RECHAZAR, "42501", orden=o, version=1, motivo=motivo)
    # Con la facultad de aprobar (por su otra cuenta, en el Consejo), quien preparó tampoco
    # rechaza.
    otra = _quien_preparo_tambien_en_el_consejo(motor_migrate, motor_app, e)
    niega(motor_migrate, e, otra, RECHAZAR, "no_es_quien_preparo", orden=o, version=1,
          motivo=motivo)  # fmt: skip
    llamar(motor_migrate, e, e.mc1, RECHAZAR, orden=o, version=1, motivo=motivo)
    o_ = orden(motor_migrate, o)
    assert (o_["estado"], o_["motivo_rechazo"]) == ("rechazada", motivo)
    assert all(motivo not in x["todo"] for x in eventos(motor_migrate, o))
    niega(motor_migrate, e, e.adm, MODIFICAR, "estado_invalido", orden=o, version=1, importe=5)


def _aprobada(motor_migrate, e) -> UUID:
    """Aprobada por mc1, que alcanza sin configuración (decisión 2)."""
    o = preparada(motor_migrate, e, en_revision=True)
    llamar(motor_migrate, e, e.mc1, APROBAR, orden=o, version=1, huella=huella(motor_migrate, o, 1))
    assert orden(motor_migrate, o)["estado"] == "aprobada"
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


def _revincular(motor_migrate, cons: UUID, persona: UUID, a: UUID | None = None) -> None:
    """Como haría soporte al corregir un vínculo: la cuenta deja su identidad y pasa a `a` o, si
    no se dice, a otra nueva (SINTETICA)."""
    with motor_migrate.begin() as c:
        c.execute(
            text(
                "update nucleo.identidad_persona set desvinculada_en = now()"
                " where consorcio_id = :c and persona_id = :p and desvinculada_en is null"
            ),
            {"c": cons, "p": persona},
        )
    with motor_migrate.begin() as c:
        if a is None:
            a = c.execute(
                text(
                    "insert into nucleo.identidad_humana (consorcio_id, huella, clave_version)"
                    " values (:c, sha256(convert_to(gen_random_uuid()::text, 'UTF8')), 1)"
                    " returning id"
                ),
                {"c": cons},
            ).scalar_one()
        c.execute(
            text(
                "insert into nucleo.identidad_persona (consorcio_id, persona_id, identidad_id)"
                " values (:c, :p, :i)"
            ),
            {"c": cons, "p": persona, "i": a},
        )


def test_la_separacion_usa_la_identidad_de_quien_preparo_aunque_se_revincule(
    motor_migrate, motor_app, e
):
    """Quien preparó cambia de identidad después; su otra cuenta, que sigue en la identidad con
    la que preparó, tampoco aprueba ni rechaza. Control: sin la identidad guardada, la
    comparación actual (cuentas en identidades distintas) la dejaría pasar."""
    o = preparada(motor_migrate, e, en_revision=True)
    h = huella(motor_migrate, o, 1)
    otra = _quien_preparo_tambien_en_el_consejo(motor_migrate, motor_app, e)
    _revincular(motor_migrate, e.cons, e.adm)
    with motor_migrate.connect() as c:
        distintas = c.execute(
            text(
                "select nucleo.identidad_humana_de(:c, :a) is distinct from"
                " nucleo.identidad_humana_de(:c, :o)"
            ),
            {"c": e.cons, "a": e.adm, "o": otra},
        ).scalar_one()
    assert distintas  # control: hoy las dos cuentas están en identidades distintas
    niega(motor_migrate, e, otra, APROBAR, "no_es_quien_preparo", orden=o, version=1, huella=h)
    niega(motor_migrate, e, otra, RECHAZAR, "no_es_quien_preparo", orden=o, version=1,
          motivo="SINTETICO")  # fmt: skip
    llamar(motor_migrate, e, e.mc1, APROBAR, orden=o, version=1, huella=h)  # control


def test_quien_aprobo_no_emite_aunque_se_revincule(motor_migrate, motor_app, e):
    """mc1 aprueba y después cambia de identidad; su otra cuenta, en la identidad con la que
    aprobó, no emite."""
    o = _aprobada(motor_migrate, e)
    with motor_migrate.connect() as c:
        m = c.execute(
            text("select id, decision_id from nucleo.mandato where consorcio_id = :c"
                 " and persona_id = :p"),
            {"c": e.cons, "p": e.mc1},
        ).one()  # fmt: skip
    otra = otra_persona(motor_migrate, e.cons, verificada=False)
    misma_identidad(motor_migrate, e.cons, e.mc1, otra)
    m_otra = alta(motor_app, e.cons, e.prop, m.decision_id, tipo="miembro_consejo", persona=otra)
    otorgar(motor_migrate, e.cons, m_otra, m.decision_id, "egresos.ejecutar_pago")
    e.sesion[otra] = nueva_sesion(motor_migrate, otra)
    _revincular(motor_migrate, e.cons, e.mc1)
    niega(motor_migrate, e, otra, EMITIR, "no_es_quien_aprobo", orden=o, version=1)
    llamar(motor_migrate, e, e.adm, EMITIR, orden=o, version=1)  # control


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
    conn.execute(text(sentencia1), {**ESENCIALES, "proveedor": e.proveedor, **kw1,
                                    "actor": actor1, "origen": "api",
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


def test_dos_humanos_aprobando_a_la_vez_con_dos_requeridas_la_aprueban(motor_migrate, motor_app, e):
    """La segunda espera el bloqueo de la orden y, al seguir, cuenta la primera ya confirmada."""
    exigir(motor_app, e.cons, 2)
    o = preparada(motor_migrate, e, en_revision=True)
    h1 = huella(motor_migrate, o, 1)
    r = _en_paralelo(motor_migrate, e,
                     (e.mc1, APROBAR, {"orden": o, "version": 1, "huella": h1}),
                     (e.mc2, APROBAR, {"orden": o, "version": 1, "huella": h1}))  # fmt: skip
    assert r["esperaba"] and "ok" in r, r
    assert orden(motor_migrate, o)["estado"] == "aprobada"
    assert sorted(p for _, p in aprobaciones(motor_migrate, o)) == sorted([e.mc1, e.mc2])


def test_dos_cuentas_del_mismo_humano_aprobando_a_la_vez_suman_una(motor_migrate, motor_app, e):
    exigir(motor_app, e.cons, 2)  # con una, la segunda chocaría con «aprobada», no con la repetida
    o = preparada(motor_migrate, e, en_revision=True)
    h1 = huella(motor_migrate, o, 1)
    otra = otra_cuenta_en_el_consejo(motor_migrate, motor_app, e, e.mc1)
    r = _en_paralelo(motor_migrate, e,
                     (e.mc1, APROBAR, {"orden": o, "version": 1, "huella": h1}),
                     (otra, APROBAR, {"orden": o, "version": 1, "huella": h1}))  # fmt: skip
    assert r == {"esperaba": True, "motivo": "aprobacion_repetida"}
    assert aprobaciones(motor_migrate, o) == [(1, e.mc1)]
    assert orden(motor_migrate, o)["estado"] == "en_revision"


# Permisos e ida y vuelta ---------------------------------------------------------------------


RUTAS_0049 = {
    "alta_proveedor", "enviar_orden_a_revision", "modificar_orden_pago",
    "nueva_version_proveedor", "orden_pago_versiones_lectura", "ordenes_pago_lectura",
    "preparar_orden_pago", "proveedor_versiones_lectura", "proveedores_lectura",
    "rechazar_orden_pago", "registrar_aprobacion_orden_pago",
}  # fmt: skip
"""Lo único que `cem_app` ejecuta desde `0049`: las funciones de las rutas. Emitir no."""


def test_cem_app_solo_ejecuta_las_funciones_de_las_rutas(motor_migrate):
    tablas = ("nucleo.orden_pago", "nucleo.orden_pago_version", "nucleo.orden_pago_aprobacion",
              "nucleo.transicion_orden_pago", "nucleo.aprobaciones_orden_pago",
              "nucleo.proveedor", "nucleo.proveedor_version")  # fmt: skip
    with motor_migrate.connect() as c:
        tabla = [
            c.execute(
                text("select has_table_privilege('cem_app', :t, :p)"), {"t": t, "p": p}
            ).scalar_one()
            for t in tablas
            for p in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")
        ]
        filas = c.execute(
            text(
                "select p.proname, has_function_privilege('cem_app', p.oid, 'EXECUTE')"
                " from pg_proc p join pg_namespace n on n.oid = p.pronamespace"
                " where n.nspname = 'nucleo' and (p.proname like '%orden_pago%'"
                " or p.proname like '%ordenes_pago%' or p.proname like '%proveedor%'"
                " or p.proname like '%egresos%'"
                " or p.proname in ('exigir_acto_economico', 'enviar_orden_a_revision',"
                " 'exigir_decision_sobre_orden'))"
                " and p.proname <> 'fijar_aprobaciones_orden_pago'"
            )
        ).all()
        # La carga de soporte corre como `cem_app` (como los coeficientes): solo esa función.
        fijar = c.execute(
            text("select has_function_privilege('cem_app', 'nucleo.fijar_aprobaciones_orden_pago("
                 "smallint, date, uuid, uuid, text)', 'EXECUTE')")
        ).scalar_one()  # fmt: skip
    assert not any(tabla)
    assert {nombre for nombre, puede in filas if puede} == RUTAS_0049
    nombres = {nombre for nombre, _ in filas}
    # Controles: las demás existen y no se ejecutan, entre ellas emitir y la barrera de lectura.
    assert {"emitir_orden_pago", "exigir_lectura_egresos", "exigir_acto_economico"} <= nombres
    assert len(filas) >= 26
    assert fijar


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


def test_0045_no_baja_si_hay_ordenes(base_aparte):  # noqa: F811
    """Bajar borraría la historia de órdenes, versiones y aprobaciones. Control: sin la orden,
    baja."""
    cfg, motor = base_aparte
    command.upgrade(cfg, "0045")
    cons, _ = _consorcio_y_unidad(motor)
    quien = _persona(motor, "Sintetica")
    with motor.begin() as c:
        orden = c.execute(
            text(
                "insert into nucleo.orden_pago (consorcio_id, version_vigente, preparada_por)"
                " values (:c, 1, :p) returning id"
            ),
            {"c": cons, "p": quien},
        ).scalar_one()
    with pytest.raises(DBAPIError) as e:
        command.downgrade(cfg, "0044")
    assert e.value.orig.diag.message_hint == "downgrade_con_datos"
    with motor.connect() as c:
        assert c.execute(text("select to_regclass('nucleo.orden_pago')")).scalar_one()
    with motor.begin() as c:
        c.execute(text("delete from nucleo.orden_pago where id = :o"), {"o": orden})
    command.downgrade(cfg, "0044")
    with motor.connect() as c:
        assert c.execute(text("select to_regclass('nucleo.orden_pago')")).scalar_one() is None


def test_solo_aprobar_lleva_la_orden_a_aprobada(motor_migrate):
    """Desde la decisión 2 (`0046`), solo `registrar_aprobacion_orden_pago` pone «aprobada», al
    llegar a la cantidad. Alarma por inventario: las otras que nombran el estado lo leen (modificar
    devuelve a revisión, emitir parte de ahí). Una función nueva que lo nombre hace fallar esta
    prueba y obliga a revisarla. Mira todos los esquemas propios, no solo `nucleo` (P3 70)."""
    with motor_migrate.connect() as c:
        nombran = set(
            c.execute(
                text(
                    "select n.nspname || '.' || p.proname from pg_proc p join pg_namespace n"
                    " on n.oid = p.pronamespace"
                    " where n.nspname not in ('pg_catalog', 'information_schema')"
                    " and p.prosrc like '%orden_pago%' and p.prosrc like '%''aprobada''%'"
                )
            ).scalars()
        )
    # `condicion_beneficiario` (0048) solo la lee: cuenta las órdenes que ya pasaron una
    # aprobación completa.
    assert nombran == {
        "nucleo.modificar_orden_pago", "nucleo.emitir_orden_pago",
        "nucleo.registrar_aprobacion_orden_pago", "nucleo.condicion_beneficiario",
    }  # fmt: skip


def test_truncate_tampoco_borra_la_historia(motor_migrate, e):
    """Control: la versión tiene filas, así que el TRUNCATE las borraría sin el trigger (el de
    la aprobación es por sentencia: se dispara aunque esté vacía).

    Cada trigger se prueba solo (P3 68). La versión no se puede truncar sin `cascade` (la
    aprobación la referencia), y con `cascade` el trigger de la aprobación daría el mismo hint:
    se apaga dentro de la transacción. Las dos transacciones se deshacen siempre: si un trigger
    faltara, la prueba falla sin dejar la base truncada ni el trigger apagado."""
    o = preparada(motor_migrate, e)

    def hint_al_truncar(*sentencias: str) -> str | None:
        with motor_migrate.connect() as c:
            transaccion_ = c.begin()
            try:
                for s in sentencias:
                    c.execute(text(s))
            except DBAPIError as error:
                return error.orig.diag.message_hint
            finally:
                transaccion_.rollback()
        return None

    assert (
        hint_al_truncar(
            "alter table nucleo.orden_pago_aprobacion"
            " disable trigger historia_orden_pago_aprobacion_truncate",
            "truncate nucleo.orden_pago_version cascade",
        )
        == "historia_inmutable"
    )
    assert hint_al_truncar("truncate nucleo.orden_pago_aprobacion") == "historia_inmutable"
    with motor_migrate.connect() as c:
        assert (
            c.execute(
                text("select count(*) from nucleo.orden_pago_version where orden_id = :o"), {"o": o}
            ).scalar_one()
            == 1
        )
